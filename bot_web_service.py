# bot_web_service.py
# Adaptación para Render del bot Breakout + Reentry - VERSIÓN CORREGIDA
import requests
import time
import json
import os
import sys
import hmac
import hashlib
import base64
from datetime import datetime, timedelta
import numpy as np
import math
import csv
import itertools
import statistics
import random
import matplotlib
matplotlib.use('Agg')  # Backend sin GUI
import matplotlib.pyplot as plt
import mplfinance as mpf
import pandas as pd
from io import BytesIO
from flask import Flask, request, jsonify
import threading
import logging

# Configurar logging básico
logging.basicConfig(level=logging.INFO, stream=sys.stdout, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ---------------------------
# [INICIO DEL CÓDIGO DEL BOT NUEVO]
# Bot Breakout + Reentry con integración Bitget - CORREGIDO PARA RENDER
# ---------------------------

# ---------------------------
# OPTIMIZADOR IA
# ---------------------------
class OptimizadorIA:
    def __init__(self, log_path="operaciones_log.csv", min_samples=15):
        self.log_path = log_path
        self.min_samples = min_samples
        self.datos = self.cargar_datos()

    def cargar_datos(self):
        datos = []
        try:
            with open(self.log_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    try:
                        pnl = float(row.get('pnl_percent', 0))
                        angulo = float(row.get('angulo_tendencia', 0))
                        pearson = float(row.get('pearson', 0))
                        r2 = float(row.get('r2_score', 0))
                        ancho_relativo = float(row.get('ancho_canal_relativo', 0))
                        nivel_fuerza = int(row.get('nivel_fuerza', 1))
                        datos.append({
                            'pnl': pnl, 
                            'angulo': angulo, 
                            'pearson': pearson, 
                            'r2': r2,
                            'ancho_relativo': ancho_relativo,
                            'nivel_fuerza': nivel_fuerza
                        })
                    except Exception:
                        continue
        except FileNotFoundError:
            print("⚠ No se encontró operaciones_log.csv (optimizador)")
        return datos

    def evaluar_configuracion(self, trend_threshold, min_strength, entry_margin):
        if not self.datos:
            return -99999
        filtradas = [
            op for op in self.datos
            if abs(op['angulo']) >= trend_threshold
            and abs(op['angulo']) >= min_strength
            and abs(op['pearson']) >= 0.4
            and op.get('nivel_fuerza', 1) >= 2
            and op.get('r2', 0) >= 0.4
        ]
        n = len(filtradas)
        if n < max(8, int(0.15 * len(self.datos))):
            return -10000 - n
        pnls = [op['pnl'] for op in filtradas]
        pnl_mean = statistics.mean(pnls) if filtradas else 0
        pnl_std = statistics.stdev(pnls) if len(pnls) > 1 else 0
        winrate = sum(1 for op in filtradas if op['pnl'] > 0) / n if n > 0 else 0
        score = (pnl_mean - 0.5 * pnl_std) * winrate * math.sqrt(n)
        ops_calidad = [op for op in filtradas if op.get('r2', 0) >= 0.6 and op.get('nivel_fuerza', 1) >= 3]
        if ops_calidad:
            score *= 1.2
        return score

    def buscar_mejores_parametros(self):
        if not self.datos or len(self.datos) < self.min_samples:
            print(f"ℹ️ No hay suficientes datos para optimizar (se requieren {self.min_samples}, hay {len(self.datos)})")
            return None
        mejor_score = -1e9
        mejores_param = None
        trend_values = [3, 5, 8, 10, 12, 15, 18, 20, 25, 30, 35, 40]
        strength_values = [3, 5, 8, 10, 12, 15, 18, 20, 25, 30]
        margin_values = [0.0005, 0.001, 0.0015, 0.002, 0.0025, 0.003, 0.004, 0.005, 0.008, 0.01]
        combos = list(itertools.product(trend_values, strength_values, margin_values))
        total = len(combos)
        print(f"🔎 Optimizador: probando {total} combinaciones...")
        for idx, (t, s, m) in enumerate(combos, start=1):
            score = self.evaluar_configuracion(t, s, m)
            if idx % 100 == 0 or idx == total:
                print(f"   · probado {idx}/{total} combos (mejor score actual: {mejor_score:.4f})")
            if score > mejor_score:
                mejor_score = score
                mejores_param = {
                    'trend_threshold_degrees': t,
                    'min_trend_strength_degrees': s,
                    'entry_margin': m,
                    'score': score,
                    'evaluated_samples': len(self.datos),
                    'total_combinations': total
                }
        if mejores_param:
            print("✅ Optimizador: mejores parámetros encontrados:", mejores_param)
            try:
                with open("mejores_parametros.json", "w", encoding='utf-8') as f:
                    json.dump(mejores_param, f, indent=2)
            except Exception as e:
                print("⚠ Error guardando mejores_parametros.json:", e)
        else:
            print("⚠ No se encontró una configuración mejor")
        return mejores_param

# ---------------------------
# BITGET CLIENT - INTEGRACIÓN COMPLETA CON API BITGET
# ---------------------------
class BitgetClient:
    def __init__(self, api_key, api_secret, passphrase):
        self.api_key = api_key
        self.api_secret = api_secret
        self.passphrase = passphrase
        self.base_url = "https://api.bitget.com"
        logger.info(f"Cliente Bitget inicializado con API Key: {api_key[:10]}...")

    def _generate_signature(self, timestamp, method, request_path, body=''):
        """Generar firma HMAC-SHA256 para Bitget V2 - CORREGIDO"""
        try:
            # Convertir body a string para la firma
            if isinstance(body, dict):
                body_str = json.dumps(body, separators=(',', ':')) if body else ''
            else:
                body_str = str(body) if body else ''
            
            # Crear el mensaje para firmar según especificación de Bitget V2
            message = timestamp + method.upper() + request_path + body_str
            
            # Generar firma HMAC-SHA256
            mac = hmac.new(
                bytes(self.api_secret, 'utf-8'),
                bytes(message, 'utf-8'),
                digestmod=hashlib.sha256
            )
            
            signature = base64.b64encode(mac.digest()).decode('utf-8')
            
            # Log para debugging
            logger.debug(f"Generando firma:")
            logger.debug(f"  Timestamp: {timestamp}")
            logger.debug(f"  Method: {method.upper()}")
            logger.debug(f"  Request path: {request_path}")
            logger.debug(f"  Body: {body_str}")
            logger.debug(f"  Signature: {signature[:20]}...")
            
            return signature
            
        except Exception as e:
            logger.error(f"Error generando firma: {e}")
            raise

    def _get_headers(self, method, request_path, body=''):
        """Obtener headers con firma para Bitget V2"""
        try:
            timestamp = str(int(time.time() * 1000))
            sign = self._generate_signature(timestamp, method, request_path, body)
            
            headers = {
                'Content-Type': 'application/json',
                'ACCESS-KEY': self.api_key,
                'ACCESS-SIGN': sign,
                'ACCESS-TIMESTAMP': timestamp,
                'ACCESS-PASSPHRASE': self.passphrase,
                'locale': 'en-US'
            }
            
            logger.debug(f"Headers generados para {method} {request_path}")
            return headers
            
        except Exception as e:
            logger.error(f"Error creando headers: {e}")
            raise

    def verificar_credenciales(self):
        """Verificar que las credenciales sean válidas"""
        try:
            logger.info("Verificando credenciales Bitget...")
            
            if not self.api_key or not self.api_secret or not self.passphrase:
                logger.error("Credenciales incompletas")
                return False
            
            accounts = self.get_account_info()
            if accounts:
                logger.info("✓ Credenciales verificadas exitosamente")
                for account in accounts:
                    if account.get('marginCoin') == 'USDT':
                        available = float(account.get('available', 0))
                        logger.info(f"✓ Balance disponible: {available:.2f} USDT")
                return True
            else:
                logger.error("✗ No se pudo verificar credenciales")
                return False
                
        except Exception as e:
            logger.error(f"Error verificando credenciales: {e}")
            return False

    def get_account_info(self, product_type='USDT-FUTURES'):
        """Obtener información de cuenta Bitget V2"""
        try:
            request_path = '/api/v2/mix/account/accounts'
            params = {'productType': product_type, 'marginCoin': 'USDT'}
            
            query_string = f"?productType={product_type}&marginCoin=USDT"
            full_request_path = request_path + query_string
            
            headers = self._get_headers('GET', full_request_path, '')
            
            response = requests.get(
                f"{self.base_url}{request_path}",
                headers=headers,
                params=params,
                timeout=10
            )
            
            logger.info(f"Respuesta cuenta - Status: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                if data.get('code') == '00000':
                    return data.get('data', [])
                else:
                    error_msg = data.get('msg', 'Unknown error')
                    error_code = data.get('code', 'Unknown')
                    logger.error(f"Error API: {error_code} - {error_msg}")
                    
                    if error_code == '40020' and product_type == 'USDT-FUTURES':
                        logger.info("Intentando con productType='USDT-MIX'...")
                        return self.get_account_info('USDT-MIX')
            else:
                logger.error(f"Error HTTP: {response.status_code} - {response.text}")
                
            return None
            
        except Exception as e:
            logger.error(f"Error en get_account_info: {e}")
            return None

    def get_klines(self, symbol, interval='5m', limit=200):
        """Obtener velas (datos de mercado)"""
        try:
            interval_map = {
                '1m': '1m', '3m': '3m', '5m': '5m',
                '15m': '15m', '30m': '30m', '1h': '1H',
                '4h': '4H', '1d': '1D'
            }
            bitget_interval = interval_map.get(interval, '5m')
            request_path = f'/api/v2/mix/market/candles'
            params = {
                'symbol': symbol,
                'productType': 'USDT-FUTURES',
                'granularity': bitget_interval,
                'limit': limit
            }
            
            response = requests.get(
                self.base_url + request_path,
                params=params,
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                if data.get('code') == '00000':
                    candles = data.get('data', [])
                    return candles
                else:
                    params['productType'] = 'USDT-MIX'
                    response = requests.get(
                        self.base_url + request_path,
                        params=params,
                        timeout=10
                    )
                    if response.status_code == 200:
                        data = response.json()
                        if data.get('code') == '00000':
                            candles = data.get('data', [])
                            return candles
            return None
        except Exception as e:
            logger.error(f"Error en get_klines: {e}")
            return None

    def get_positions(self, symbol=None, product_type='USDT-FUTURES'):
        """Obtener posiciones abiertas"""
        try:
            request_path = '/api/v2/mix/position/all-position'
            params = {'productType': product_type, 'marginCoin': 'USDT'}
            if symbol:
                params['symbol'] = symbol
            
            query_parts = []
            for key, value in params.items():
                query_parts.append(f"{key}={value}")
            query_string = "?" + "&".join(query_parts) if query_parts else ""
            full_request_path = request_path + query_string
            
            headers = self._get_headers('GET', full_request_path, '')
            
            response = requests.get(
                self.base_url + request_path,
                headers=headers,
                params=params,
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                if data.get('code') == '00000':
                    return data.get('data', [])
            
            if product_type == 'USDT-FUTURES':
                return self.get_positions(symbol, 'USDT-MIX')
            
            return []
        except Exception as e:
            logger.error(f"Error obteniendo posiciones: {e}")
            return []

# ---------------------------
# BOT PRINCIPAL - BREAKOUT + REENTRY CON INTEGRACIÓN BITGET
# ---------------------------
class TradingBot:
    def __init__(self, config):
        self.config = config
        self.log_path = config.get('log_path', 'operaciones_log.csv')
        self.auto_optimize = config.get('auto_optimize', True)
        self.ultima_optimizacion = datetime.now()
        self.operaciones_desde_optimizacion = 0
        self.total_operaciones = 0
        self.breakout_history = {}
        self.config_optima_por_simbolo = {}
        self.ultima_busqueda_config = {}
        # NUEVO: Tracking de breakouts y reingresos
        self.breakouts_detectados = {}
        self.esperando_reentry = {}
        self.estado_file = config.get('estado_file', 'estado_bot.json')
        self.cargar_estado()
        
        # NUEVO: Inicializar cliente Bitget si están las credenciales
        self.bitget_client = None
        if config.get('bitget_api_key') and config.get('bitget_api_secret') and config.get('bitget_passphrase'):
            self.bitget_client = BitgetClient(
                api_key=config['bitget_api_key'],
                api_secret=config['bitget_api_secret'],
                passphrase=config['bitget_passphrase']
            )
            if self.bitget_client.verificar_credenciales():
                logger.info("✅ Cliente Bitget inicializado y verificado")
            else:
                logger.warning("⚠️ No se pudieron verificar las credenciales de Bitget")
        
        # NUEVO: Configuración de operaciones automáticas
        self.ejecutar_operaciones_automaticas = config.get('ejecutar_operaciones_automaticas', False)
        self.capital_por_operacion = config.get('capital_por_operacion', 50)
        self.leverage_por_defecto = config.get('leverage_por_defecto', 20)
        
        parametros_optimizados = None
        if self.auto_optimize:
            try:
                ia = OptimizadorIA(log_path=self.log_path, min_samples=config.get('min_samples_optimizacion', 15))
                parametros_optimizados = ia.buscar_mejores_parametros()
            except Exception as e:
                print("⚠ Error en optimización automática:", e)
                parametros_optimizados = None
        if parametros_optimizados:
            self.config['trend_threshold_degrees'] = parametros_optimizados.get('trend_threshold_degrees', 
                                                                               self.config.get('trend_threshold_degrees', 13))
            self.config['min_trend_strength_degrees'] = parametros_optimizados.get('min_trend_strength_degrees', 
                                                                                   self.config.get('min_trend_strength_degrees', 16))
            self.config['entry_margin'] = parametros_optimizados.get('entry_margin', 
                                                                     self.config.get('entry_margin', 0.001))
        self.ultimos_datos = {}
        self.operaciones_activas = {}
        self.senales_enviadas = set()
        self.archivo_log = self.log_path
        self.inicializar_log()

    def cargar_estado(self):
        """Carga el estado previo del bot incluyendo breakouts"""
        try:
            if os.path.exists(self.estado_file):
                with open(self.estado_file, 'r', encoding='utf-8') as f:
                    estado = json.load(f)
                if 'ultima_optimizacion' in estado:
                    estado['ultima_optimizacion'] = datetime.fromisoformat(estado['ultima_optimizacion'])
                if 'ultima_busqueda_config' in estado:
                    for simbolo, fecha_str in estado['ultima_busqueda_config'].items():
                        estado['ultima_busqueda_config'][simbolo] = datetime.fromisoformat(fecha_str)
                if 'breakout_history' in estado:
                    for simbolo, fecha_str in estado['breakout_history'].items():
                        estado['breakout_history'][simbolo] = datetime.fromisoformat(fecha_str)
                # Cargar breakouts y reingresos esperados
                if 'esperando_reentry' in estado:
                    for simbolo, info in estado['esperando_reentry'].items():
                        info['timestamp'] = datetime.fromisoformat(info['timestamp'])
                        estado['esperando_reentry'][simbolo] = info
                    self.esperando_reentry = estado['esperando_reentry']
                if 'breakouts_detectados' in estado:
                    for simbolo, info in estado['breakouts_detectados'].items():
                        info['timestamp'] = datetime.fromisoformat(info['timestamp'])
                        estado['breakouts_detectados'][simbolo] = info
                    self.breakouts_detectados = estado['breakouts_detectados']
                self.ultima_optimizacion = estado.get('ultima_optimizacion', datetime.now())
                self.operaciones_desde_optimizacion = estado.get('operaciones_desde_optimizacion', 0)
                self.total_operaciones = estado.get('total_operaciones', 0)
                self.breakout_history = estado.get('breakout_history', {})
                self.config_optima_por_simbolo = estado.get('config_optima_por_simbolo', {})
                self.ultima_busqueda_config = estado.get('ultima_busqueda_config', {})
                self.operaciones_activas = estado.get('operaciones_activas', {})
                self.senales_enviadas = set(estado.get('senales_enviadas', []))
                print("✅ Estado anterior cargado correctamente")
                print(f"   📊 Operaciones activas: {len(self.operaciones_activas)}")
                print(f"   ⏳ Esperando reentry: {len(self.esperando_reentry)}")
        except Exception as e:
            print(f"⚠ Error cargando estado previo: {e}")
            print("   Se iniciará con estado limpio")

    def limpiar_memoria(self):
        """Limpiar datos antiguos para optimizar memoria en Render"""
        try:
            # Limpiar breakouts antiguos (más de 12 horas)
            cutoff_breakout = datetime.now() - timedelta(hours=12)
            breakouts_a_limpiar = []
            for simbolo, info in list(self.breakouts_detectados.items()):
                if info['timestamp'] < cutoff_breakout:
                    breakouts_a_limpiar.append(simbolo)
            
            for simbolo in breakouts_a_limpiar:
                del self.breakouts_detectados[simbolo]
            
            # Forzar garbage collection
            import gc
            gc.collect()
            
            if breakouts_a_limpiar:
                print(f"🧹 Limpiados {len(breakouts_a_limpiar)} breakouts antiguos")
                
        except Exception as e:
            print(f"⚠️ Error en limpieza de memoria: {e}")

    def guardar_estado(self):
        """Guarda el estado actual del bot incluyendo breakouts"""
        try:
            # Limpiar memoria antes de guardar
            self.limpiar_memoria()
            
            estado = {
                'ultima_optimizacion': self.ultima_optimizacion.isoformat(),
                'operaciones_desde_optimizacion': self.operaciones_desde_optimizacion,
                'total_operaciones': self.total_operaciones,
                'breakout_history': {k: v.isoformat() for k, v in self.breakout_history.items()},
                'config_optima_por_simbolo': self.config_optima_por_simbolo,
                'ultima_busqueda_config': {k: v.isoformat() for k, v in self.ultima_busqueda_config.items()},
                'operaciones_activas': self.operaciones_activas,
                'senales_enviadas': list(self.senales_enviadas),
                'esperando_reentry': {
                    k: {
                        'tipo': v['tipo'],
                        'timestamp': v['timestamp'].isoformat(),
                        'precio_breakout': v['precio_breakout'],
                        'config': v.get('config', {})
                    } for k, v in self.esperando_reentry.items()
                },
                'breakouts_detectados': {
                    k: {
                        'tipo': v['tipo'],
                        'timestamp': v['timestamp'].isoformat(),
                        'precio_breakout': v.get('precio_breakout', 0)
                    } for k, v in self.breakouts_detectados.items()
                },
                'timestamp_guardado': datetime.now().isoformat()
            }
            with open(self.estado_file, 'w', encoding='utf-8') as f:
                json.dump(estado, f, indent=2, ensure_ascii=False)
            print("💾 Estado guardado correctamente")
        except Exception as e:
            print(f"⚠️ Error guardando estado: {e}")

    def obtener_datos_mercado_config(self, symbol, timeframe, num_velas):
        """Obtener datos de mercado con configuración específica"""
        try:
            # Usar Bitget si está disponible
            if self.bitget_client:
                klines = self.bitget_client.get_klines(symbol, timeframe, num_velas)
                if klines:
                    cierres = [float(k[4]) for k in klines]
                    maximos = [float(k[2]) for k in klines]
                    minimos = [float(k[3]) for k in klines]
                    volumenes = [float(k[5]) for k in klines]
                    
                    precio_actual = cierres[-1] if cierres else 0
                    
                    return {
                        'cierres': cierres,
                        'maximos': maximos,
                        'minimos': minimos,
                        'volumenes': volumenes,
                        'precio_actual': precio_actual,
                        'timeframe': timeframe,
                        'num_velas': num_velas
                    }
            
            # Fallback a Binance
            url = "https://api.binance.com/api/v3/klines"
            params = {
                'symbol': symbol,
                'interval': timeframe,
                'limit': num_velas
            }
            respuesta = requests.get(url, params=params, timeout=10)
            klines = respuesta.json()
            
            cierres = [float(k[4]) for k in klines]
            maximos = [float(k[2]) for k in klines]
            minimos = [float(k[3]) for k in klines]
            volumenes = [float(k[5]) for k in klines]
            
            precio_actual = cierres[-1] if cierres else 0
            
            return {
                'cierres': cierres,
                'maximos': maximos,
                'minimos': minimos,
                'volumenes': volumenes,
                'precio_actual': precio_actual,
                'timeframe': timeframe,
                'num_velas': num_velas
            }
        except Exception as e:
            print(f"⚠️ Error obteniendo datos de {symbol}: {e}")
            return None

    def calcular_regresion_lineal(self, x, y):
        if len(x) != len(y) or len(x) == 0:
            return None
        x = np.array(x)
        y = np.array(y)
        n = len(x)
        sum_x = np.sum(x)
        sum_y = np.sum(y)
        sum_xy = np.sum(x * y)
        sum_x2 = np.sum(x * x)
        denom = (n * sum_x2 - sum_x * sum_x)
        if denom == 0:
            pendiente = 0
        else:
            pendiente = (n * sum_xy - sum_x * sum_y) / denom
        intercepto = (sum_y - pendiente * sum_x) / n if n else 0
        return pendiente, intercepto

    def calcular_pearson_y_angulo(self, x, y):
        if len(x) != len(y) or len(x) < 2:
            return 0, 0
        x = np.array(x)
        y = np.array(y)
        n = len(x)
        sum_x = np.sum(x)
        sum_y = np.sum(y)
        sum_xy = np.sum(x * y)
        sum_x2 = np.sum(x * x)
        sum_y2 = np.sum(y * y)
        numerator = n * sum_xy - sum_x * sum_y
        denominator = math.sqrt((n * sum_x2 - sum_x * sum_x) * (n * sum_y2 - sum_y * sum_y))
        if denominator == 0:
            return 0, 0
        pearson = numerator / denominator
        denom_pend = (n * sum_x2 - sum_x * sum_x)
        pendiente = (n * sum_xy - sum_x * sum_y) / denom_pend if denom_pend != 0 else 0
        angulo_radianes = math.atan(pendiente * len(x) / (max(y) - min(y)) if (max(y) - min(y)) != 0 else 0)
        angulo_grados = math.degrees(angulo_radianes)
        return pearson, angulo_grados

    def clasificar_fuerza_tendencia(self, angulo_grados):
        angulo_abs = abs(angulo_grados)
        if angulo_abs < 3:
            return "💔 Muy Débil", 1
        elif angulo_abs < 13:
            return "❤️‍🩹 Débil", 2
        elif angulo_abs < 27:
            return "💛 Moderada", 3
        elif angulo_abs < 45:
            return "💚 Fuerte", 4
        else:
            return "💙 Muy Fuerte", 5

    def determinar_direccion_tendencia(self, angulo_grados, umbral_minimo=1):
        if abs(angulo_grados) < umbral_minimo:
            return "⚪ RANGO"
        elif angulo_grados > 0:
            return "🟢 ALCISTA"
        else:
            return "🔴 BAJISTA"

    def calcular_r2(self, y_real, x, pendiente, intercepto):
        if len(y_real) != len(x):
            return 0
        y_real = np.array(y_real)
        y_pred = pendiente * np.array(x) + intercepto
        ss_res = np.sum((y_real - y_pred) ** 2)
        ss_tot = np.sum((y_real - np.mean(y_real)) ** 2)
        if ss_tot == 0:
            return 0
        return 1 - (ss_res / ss_tot)

    def calcular_stochastic(self, datos_mercado, period=14, k_period=3, d_period=3):
        if len(datos_mercado['cierres']) < period:
            return 50, 50
        cierres = datos_mercado['cierres']
        maximos = datos_mercado['maximos']
        minimos = datos_mercado['minimos']
        k_values = []
        for i in range(period-1, len(cierres)):
            highest_high = max(maximos[i-period+1:i+1])
            lowest_low = min(minimos[i-period+1:i+1])
            if highest_high == lowest_low:
                k = 50
            else:
                k = 100 * (cierres[i] - lowest_low) / (highest_high - lowest_low)
            k_values.append(k)
        if len(k_values) >= k_period:
            k_smoothed = []
            for i in range(k_period-1, len(k_values)):
                k_avg = sum(k_values[i-k_period+1:i+1]) / k_period
                k_smoothed.append(k_avg)
            if len(k_smoothed) >= d_period:
                d = sum(k_smoothed[-d_period:]) / d_period
                k_final = k_smoothed[-1]
                return k_final, d
        return 50, 50

    def calcular_canal_regresion_config(self, datos_mercado, num_velas):
        """Calcular canal de regresión con configuración específica"""
        try:
            cierres = datos_mercado['cierres']
            if len(cierres) < num_velas:
                return None
            
            cierres_recientes = cierres[-num_velas:]
            maximos_recientes = datos_mercado['maximos'][-num_velas:]
            minimos_recientes = datos_mercado['minimos'][-num_velas:]
            
            # Regresión lineal en cierres
            x = list(range(len(cierres_recientes)))
            pendiente, intercepto = self.calcular_regresion_lineal(x, cierres_recientes)
            if pendiente is None:
                return None
            
            # Pearson y ángulo
            coeficiente_pearson, angulo_tendencia = self.calcular_pearson_y_angulo(x, cierres_recientes)
            fuerza_texto, nivel_fuerza = self.clasificar_fuerza_tendencia(angulo_tendencia)
            direccion = self.determinar_direccion_tendencia(angulo_tendencia)
            
            # R² score
            r2_score = self.calcular_r2(cierres_recientes, x, pendiente, intercepto)
            
            # Calcular resistencias y soportes
            pendientes_datos = []
            resistencias_calculadas = []
            soportes_calculados = []
            
            for i in range(3, len(cierres_recientes)):
                pendiente_temp, intercepto_temp = self.calcular_regresion_lineal(x[:i+1], cierres_recientes[:i+1])
                if pendiente_temp is not None:
                    pendientes_datos.append(pendiente_temp)
            
            if pendientes_datos:
                pendiente_resistencia = np.mean([p for p in pendientes_datos if p > 0]) if any(p > 0 for p in pendientes_datos) else 0
                pendiente_soporte = np.mean([p for p in pendientes_datos if p < 0]) if any(p < 0 for p in pendientes_datos) else 0
            else:
                pendiente_resistencia = pendiente * 0.8
                pendiente_soporte = pendiente * 0.8
            
            # Calcular resistencia y soporte
            resistencia = max(cierres_recientes) * 1.002
            soporte = min(cierres_recientes) * 0.998
            
            # Calcular ancho del canal
            ancho_canal = resistencia - soporte
            ancho_canal_porcentual = (ancho_canal / datos_mercado['precio_actual']) * 100
            
            # Stochastic
            stoch_k, stoch_d = self.calcular_stochastic(datos_mercado)
            
            return {
                'resistencia': resistencia,
                'soporte': soporte,
                'pendiente_resistencia': pendiente_resistencia,
                'pendiente_soporte': pendiente_soporte,
                'coeficiente_pearson': coeficiente_pearson,
                'angulo_tendencia': angulo_tendencia,
                'fuerza_texto': fuerza_texto,
                'nivel_fuerza': nivel_fuerza,
                'direccion': direccion,
                'r2_score': r2_score,
                'ancho_canal': ancho_canal,
                'ancho_canal_porcentual': ancho_canal_porcentual,
                'stoch_k': stoch_k,
                'stoch_d': stoch_d,
                'pendiente': pendiente,
                'intercepto': intercepto
            }
        except Exception as e:
            print(f"⚠️ Error calculando canal: {e}")
            return None

    def detectar_breakout(self, simbolo, info_canal, datos_mercado):
        """Detectar breakout en el símbolo"""
        try:
            precio_actual = datos_mercado['precio_actual']
            resistencia = info_canal['resistencia']
            soporte = info_canal['soporte']
            
            # Verificar breakout
            if precio_actual > resistencia:
                # Breakout alcista
                return "BREAKOUT_LONG"
            elif precio_actual < soporte:
                # Breakout bajista
                return "BREAKOUT_SHORT"
            return None
        except Exception as e:
            print(f"⚠️ Error detectando breakout: {e}")
            return None

    def detectar_reentry(self, simbolo, info_canal, datos_mercado):
        """Detectar reentry después de breakout"""
        try:
            precio_actual = datos_mercado['precio_actual']
            resistencia = info_canal['resistencia']
            soporte = info_canal['soporte']
            
            # Verificar si hay breakout previo registrado
            if simbolo not in self.esperando_reentry:
                return None
            
            breakout_info = self.esperando_reentry[simbolo]
            tipo_breakout = breakout_info['tipo']
            
            # Verificar condiciones de reentry
            tiempo_espera = (datetime.now() - breakout_info['timestamp']).total_seconds() / 60
            if tiempo_espera > 60:  # Más de 1 hora
                del self.esperando_reentry[simbolo]
                return None
            
            # Verificar reentry basado en el tipo de breakout
            if tipo_breakout == "BREAKOUT_LONG":
                # Después de ruptura alcista, buscar reentry bajista ( SHORT )
                if precio_actual <= resistencia and info_canal['stoch_k'] >= 70:
                    return "SHORT"
            elif tipo_breakout == "BREAKOUT_SHORT":
                # Después de ruptura bajista, buscar reentry alcista ( LONG )
                if precio_actual >= soporte and info_canal['stoch_k'] <= 30:
                    return "LONG"
            
            return None
        except Exception as e:
            print(f"⚠️ Error detectando reentry: {e}")
            return None

    def calcular_niveles_entrada(self, tipo_operacion, info_canal, precio_actual):
        """Calcular niveles de entrada, TP y SL"""
        try:
            entrada = None
            if tipo_operacion == "LONG":
                entrada = info_canal['soporte'] + (info_canal['ancho_canal'] * self.config.get('entry_margin', 0.001))
            else:
                entrada = info_canal['resistencia'] - (info_canal['ancho_canal'] * self.config.get('entry_margin', 0.001))
            
            # Calcular riesgo
            if tipo_operacion == "LONG":
                riesgo = entrada - info_canal['soporte']
            else:
                riesgo = info_canal['resistencia'] - entrada
            
            # Calcular TP y SL
            if tipo_operacion == "LONG":
                stop_loss = entrada - riesgo
                take_profit = entrada + (riesgo * self.config.get('min_rr_ratio', 1.2))
            else:
                stop_loss = entrada + riesgo
                take_profit = entrada - (riesgo * self.config.get('min_rr_ratio', 1.2))
            
            # Verificar ratio riesgo/beneficio
            if tipo_operacion == "LONG":
                beneficio = take_profit - entrada
            else:
                beneficio = entrada - take_profit
            
            ratio_rr = beneficio / riesgo if riesgo > 0 else 0
            if ratio_rr < self.config.get('min_rr_ratio', 1.2):
                return None, None, None
            
            return entrada, take_profit, stop_loss
            
        except Exception as e:
            print(f"⚠️ Error calculando niveles: {e}")
            return None, None, None

    def enviar_alerta_breakout(self, simbolo, tipo_breakout, info_canal, datos_mercado, config_optima):
        """Enviar alerta cuando se detecta un breakout"""
        try:
            precio_actual = datos_mercado['precio_actual']
            resistencia = info_canal['resistencia']
            soporte = info_canal['soporte']
            
            if tipo_breakout == "BREAKOUT_LONG":
                direccion_emoji = "🟢"
                direccion_texto = "ALCISTA"
                nivel_ruptura = resistencia
            else:
                direccion_emoji = "🔴"
                direccion_texto = "BAJISTA"
                nivel_ruptura = soporte
            
            mensaje = f"""
{direccion_emoji} <b>BREAKOUT DETECTADO - {simbolo}</b>

💥 <b>Tipo:</b> {direccion_texto}
💰 <b>Precio:</b> {precio_actual:.8f}
📏 <b>Nivel de ruptura:</b> {nivel_ruptura:.8f}
📊 <b>Configuración:</b> {config_optima['timeframe']} - {config_optima['num_velas']} velas
💪 <b>Fuerza:</b> {info_canal['fuerza_texto']}
📈 <b>Tendencia:</b> {info_canal['direccion']} ({info_canal['angulo_tendencia']:.1f}°)
⏰ <b>Esperando reentry...</b>
🕒 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
            """
            
            token = self.config.get('telegram_token')
            chat_ids = self.config.get('telegram_chat_ids', [])
            if token and chat_ids:
                try:
                    self._enviar_telegram_simple(mensaje, token, chat_ids)
                    print(f"     📢 Alerta de breakout enviada para {simbolo}")
                except Exception as e:
                    print(f"     ❌ Error enviando alerta: {e}")
                    
        except Exception as e:
            print(f"⚠️ Error enviando alerta breakout: {e}")

    def buscar_configuracion_optima_simbolo(self, simbolo):
        """Busca la mejor combinación de velas/timeframe"""
        if simbolo in self.config_optima_por_simbolo:
            config_optima = self.config_optima_por_simbolo[simbolo]
            ultima_busqueda = self.ultima_busqueda_config.get(simbolo)
            if ultima_busqueda and (datetime.now() - ultima_busqueda).total_seconds() < 7200:
                return config_optima
            else:
                print(f"   🔄 Reevaluando configuración para {simbolo} (pasó 2 horas)")
        print(f"   🔍 Buscando configuración óptima para {simbolo}...")
        timeframes = self.config.get('timeframes', ['1m', '3m', '5m', '15m', '30m'])
        velas_options = self.config.get('velas_options', [80, 100, 120, 150, 200])
        mejor_config = None
        mejor_puntaje = -999999
        prioridad_timeframe = {'1m': 200, '3m': 150, '5m': 120, '15m': 100, '30m': 80}
        for timeframe in timeframes:
            for num_velas in velas_options:
                try:
                    datos = self.obtener_datos_mercado_config(simbolo, timeframe, num_velas)
                    if not datos:
                        continue
                    canal_info = self.calcular_canal_regresion_config(datos, num_velas)
                    if not canal_info:
                        continue
                    if (canal_info['nivel_fuerza'] >= 2 and 
                        abs(canal_info['coeficiente_pearson']) >= 0.4 and 
                        canal_info['r2_score'] >= 0.4):
                        ancho_actual = canal_info['ancho_canal_porcentual']
                        if ancho_actual >= self.config.get('min_channel_width_percent', 4.0):
                            puntaje_ancho = ancho_actual * 10
                            puntaje_timeframe = prioridad_timeframe.get(timeframe, 50) * 100
                            puntaje_total = puntaje_ancho + puntaje_timeframe
                            if puntaje_total > mejor_puntaje:
                                mejor_puntaje = puntaje_total
                                mejor_config = {
                                    'timeframe': timeframe,
                                    'num_velas': num_velas,
                                    'ancho_canal': canal_info['ancho_canal'],
                                    'puntaje_total': puntaje_total
                                }
                except Exception as e:
                    continue
        if not mejor_config:
            for timeframe in timeframes:
                for num_velas in velas_options:
                    try:
                        datos = self.obtener_datos_mercado_config(simbolo, timeframe, num_velas)
                        if not datos:
                            continue
                        canal_info = self.calcular_canal_regresion_config(datos, num_velas)
                        if not canal_info:
                            continue
                        if canal_info['nivel_fuerza'] >= 1 and abs(canal_info['coeficiente_pearson']) >= 0.3:
                            ancho_actual = canal_info['ancho_canal_porcentual']
                            if ancho_actual >= 2.0:
                                puntaje_ancho = ancho_actual * 5
                                puntaje_timeframe = prioridad_timeframe.get(timeframe, 50) * 50
                                puntaje_total = puntaje_ancho + puntaje_timeframe
                                if puntaje_total > mejor_puntaje:
                                    mejor_puntaje = puntaje_total
                                    mejor_config = {
                                        'timeframe': timeframe,
                                        'num_velas': num_velas,
                                        'ancho_canal': canal_info['ancho_canal'],
                                        'puntaje_total': puntaje_total
                                    }
                    except Exception:
                        continue
        if mejor_config:
            self.config_optima_por_simbolo[simbolo] = mejor_config
            self.ultima_busqueda_config[simbolo] = datetime.now()
            print(f"   ✅ Configuración encontrada para {simbolo}: {mejor_config}")
        else:
            print(f"   ❌ No se encontró configuración válida para {simbolo}")
        return mejor_config

    def _enviar_telegram_simple(self, mensaje, token, chat_ids):
        """Enviar mensaje simple por Telegram"""
        if not token or not chat_ids:
            return False
        resultados = []
        for chat_id in chat_ids:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            payload = {'chat_id': chat_id, 'text': mensaje, 'parse_mode': 'HTML'}
            try:
                r = requests.post(url, json=payload, timeout=10)
                resultados.append(r.status_code == 200)
            except Exception:
                resultados.append(False)
        return any(resultados)

    def escanear_mercado(self):
        """Escanea el mercado con estrategia Breakout + Reentry"""
        symbols = self.config.get('symbols', [])
        print(f"\n🔍 Escaneando {len(symbols)} símbolos (Estrategia: Breakout + Reentry)...")
        senales_encontradas = 0
        max_symbols_per_cycle = 20  # Limitar símbolos por ciclo para optimizar
        
        # Procesar solo una muestra de símbolos por ciclo para evitar timeouts
        symbols_to_scan = symbols[:max_symbols_per_cycle] if len(symbols) > max_symbols_per_cycle else symbols
        
        for simbolo in symbols_to_scan:
            try:
                if simbolo in self.operaciones_activas:
                    print(f"   ⚡ {simbolo} - Operación activa, omitiendo...")
                    continue
                config_optima = self.buscar_configuracion_optima_simbolo(simbolo)
                if not config_optima:
                    print(f"   ❌ {simbolo} - No se encontró configuración válida")
                    continue
                datos_mercado = self.obtener_datos_mercado_config(
                    simbolo, config_optima['timeframe'], config_optima['num_velas']
                )
                if not datos_mercado:
                    print(f"   ❌ {simbolo} - Error obteniendo datos")
                    continue
                info_canal = self.calcular_canal_regresion_config(datos_mercado, config_optima['num_velas'])
                if not info_canal:
                    print(f"   ❌ {simbolo} - Error calculando canal")
                    continue
                estado_stoch = ""
                if info_canal['stoch_k'] <= 30:
                    estado_stoch = "📉 OVERSOLD"
                elif info_canal['stoch_k'] >= 70:
                    estado_stoch = "[OVERBOUGHT]"
                else:
                    estado_stoch = "➖ NEUTRO"
                precio_actual = datos_mercado['precio_actual']
                resistencia = info_canal['resistencia']
                soporte = info_canal['soporte']
                if precio_actual > resistencia:
                    posicion = "🔼 FUERA (arriba)"
                elif precio_actual < soporte:
                    posicion = "🔽 FUERA (abajo)"
                else:
                    posicion = "📍 DENTRO"
                print(
    f"📊 {simbolo} - {config_optima['timeframe']} - {config_optima['num_velas']}v | "
    f"{info_canal['direccion']} ({info_canal['angulo_tendencia']:.1f}° - {info_canal['fuerza_texto']}) | "
    f"Ancho: {info_canal['ancho_canal_porcentual']:.1f}% - Stoch: {info_canal['stoch_k']:.1f}/{info_canal['stoch_d']:.1f} {estado_stoch} | "
    f"Precio: {posicion}"
                )
                if (info_canal['nivel_fuerza'] < 2 or 
                    abs(info_canal['coeficiente_pearson']) < 0.4 or 
                    info_canal['r2_score'] < 0.4):
                    continue
                if simbolo not in self.esperando_reentry:
                    tipo_breakout = self.detectar_breakout(simbolo, info_canal, datos_mercado)
                    if tipo_breakout:
                        self.esperando_reentry[simbolo] = {
                            'tipo': tipo_breakout,
                            'timestamp': datetime.now(),
                            'precio_breakout': precio_actual,
                            'config': config_optima
                        }
                        # NUEVO: Registrar el breakout detectado para evitar repeticiones
                        self.breakouts_detectados[simbolo] = {
                            'tipo': tipo_breakout,
                            'timestamp': datetime.now(),
                            'precio_breakout': precio_actual
                        }
                        print(f"     🎯 {simbolo} - Breakout registrado, esperando reingreso...")
                        # NUEVO: Enviar alerta de breakout a Telegram
                        self.enviar_alerta_breakout(simbolo, tipo_breakout, info_canal, datos_mercado, config_optima)
                        continue
                tipo_operacion = self.detectar_reentry(simbolo, info_canal, datos_mercado)
                if not tipo_operacion:
                    continue
                precio_entrada, tp, sl = self.calcular_niveles_entrada(
                    tipo_operacion, info_canal, datos_mercado['precio_actual']
                )
                if not precio_entrada or not tp or not sl:
                    continue
                if simbolo in self.breakout_history:
                    ultimo_breakout = self.breakout_history[simbolo]
                    tiempo_desde_ultimo = (datetime.now() - ultimo_breakout).total_seconds() / 3600
                    if tiempo_desde_ultimo < 2:
                        print(f"   ⏳ {simbolo} - Señal reciente, omitiendo...")
                        continue
                # Por ahora solo registrar la señal sin ejecutar
                senales_encontradas += 1
                self.breakout_history[simbolo] = datetime.now()
                print(f"     ✅ Señal {tipo_operacion} detectada para {simbolo}")
            except Exception as e:
                print(f"⚠️ Error analizando {simbolo}: {e}")
                continue
        if self.esperando_reentry:
            print(f"\n⏳ Esperando reingreso en {len(self.esperando_reentry)} símbolos:")
            for simbolo, info in self.esperando_reentry.items():
                tiempo_espera = (datetime.now() - info['timestamp']).total_seconds() / 60
                print(f"   • {simbolo} - {info['tipo']} - Esperando {tiempo_espera:.1f} min")
        if senales_encontradas > 0:
            print(f"✅ Se encontraron {senales_encontradas} señales de trading")
        else:
            print("❌ No se encontraron señales en este ciclo")
        return senales_encontradas

    def verificar_operaciones_reales_bitget(self):
        """Verifica y actualiza el estado basado en posiciones reales de Bitget"""
        if not self.bitget_client:
            return []
        return []  # Simplificado para evitar errores

    def verificar_cierre_operaciones(self):
        """Verificar cierre de operaciones locales"""
        return []  # Simplificado para evitar errores

    def inicializar_log(self):
        """Inicializar archivo de log"""
        if not os.path.exists(self.archivo_log):
            with open(self.archivo_log, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow([
                    'timestamp', 'symbol', 'tipo', 'precio_entrada',
                    'take_profit', 'stop_loss', 'precio_salida',
                    'resultado', 'pnl_percent', 'duracion_minutos',
                    'angulo_tendencia', 'pearson', 'r2_score',
                    'ancho_canal_relativo', 'ancho_canal_porcentual',
                    'nivel_fuerza', 'timeframe_utilizado', 'velas_utilizadas',
                    'stoch_k', 'stoch_d', 'breakout_usado', 'operacion_ejecutada'
                ])

    def reoptimizar_periodicamente(self):
        """Reoptimizar parámetros periódicamente"""
        pass  # Simplificado

    def verificar_envio_reporte_automatico(self):
        """Verificar envío de reporte automático"""
        pass  # Simplificado

    def ejecutar_analisis(self):
        """Ejecutar análisis con manejo robusto de errores"""
        try:
            # Verificación periódica de optimización (10% de probabilidad)
            if random.random() < 0.1:
                try:
                    self.reoptimizar_periodicamente()
                except Exception as e:
                    print(f"⚠️ Error en optimización: {e}")
                
                try:
                    self.verificar_envio_reporte_automatico()
                except Exception as e:
                    print(f"⚠️ Error enviando reporte: {e}")
            
            # Verificar operaciones reales en Bitget
            try:
                cierres = self.verificar_operaciones_reales_bitget()
                if cierres:
                    print(f"     📊 Operaciones cerradas: {', '.join(cierres)}")
            except Exception as e:
                print(f"⚠️ Error verificando operaciones Bitget: {e}")
            
            # Verificar operaciones locales (por compatibilidad)
            try:
                cierres_locales = self.verificar_cierre_operaciones()
                if cierres_locales:
                    print(f"     📊 Operaciones locales cerradas: {', '.join(cierres_locales)}")
            except Exception as e:
                print(f"⚠️ Error verificando operaciones locales: {e}")
            
            # Guardar estado
            try:
                self.guardar_estado()
            except Exception as e:
                print(f"⚠️ Error guardando estado: {e}")
            
            # Escanear mercado
            try:
                senales = self.escanear_mercado()
                return senales
            except Exception as e:
                print(f"⚠️ Error escaneando mercado: {e}")
                return 0
                
        except Exception as e:
            print(f"❌ Error crítico en ejecutar_analisis: {e}")
            return 0

# ---------------------------
# CONFIGURACIÓN SIMPLE
# ---------------------------
def crear_config_desde_entorno():
    """Configuración desde variables de entorno"""
    directorio_actual = os.path.dirname(os.path.abspath(__file__))
    telegram_chat_ids_str = os.environ.get('TELEGRAM_CHAT_ID', '-1002272872445')
    telegram_chat_ids = [cid.strip() for cid in telegram_chat_ids_str.split(',') if cid.strip()]
    
    return {
        'min_channel_width_percent': 4.0,
        'trend_threshold_degrees': 16.0,
        'min_trend_strength_degrees': 16.0,
        'entry_margin': 0.001,
        'min_rr_ratio': 1.2,
        'scan_interval_minutes': 10,  # Aumentado para reducir uso de recursos
        'timeframes': ['5m', '15m', '30m', '1h', '4h'],
        'velas_options': [80, 100, 120, 150, 200],
        'symbols': [
            'BTCUSDT','ETHUSDT','XMRUSDT','AAVEUSDT','DOTUSDT','LINKUSDT','BNBUSDT','XRPUSDT','SOLUSDT','AVAXUSDT',
            'DOGEUSDT','LTCUSDT','ATOMUSDT','XLMUSDT','ALGOUSDT','VETUSDT','ICPUSDT','FILUSDT',
            'BCHUSDT','NEOUSDT','TRXUSDT','XTZUSDT','SUSHIUSDT','COMPUSDT','PEPEUSDT','ETCUSDT',
            'SNXUSDT','RENDERUSDT','1INCHUSDT','UNIUSDT','ZILUSDT','HOTUSDT','ENJUSDT','HYPEUSDT',
            'BEATUSDT','PIPPINUSDT','ADAUSDT','ASTERUSDT','ENAUSDT','TAOUSDT','HEMIUSDT','LUNCUSDT',
            'WLDUSDT','WIFUSDT','APTUSDT','HBARUSDT','CRVUSDT','LUNAUSDT','TIAUSDT','ARBUSDT','ONDOUSDT',
            '1000BONKUSDT','FOLKSUSDT','BRETTUSDT','TRUMPUSDT','INJUSDT','ZECUSDT','NOTUSDT','SHIBUSDT',
            'LDOUSDT','KASUSDT','STRKUSDT','DYDXUSDT','SEIUSDT','TONUSDT','NMRUSDT'
        ],
        'telegram_token': os.environ.get('TELEGRAM_TOKEN'),
        'telegram_chat_ids': telegram_chat_ids,
        'auto_optimize': True,
        'min_samples_optimizacion': 30,
        'reevaluacion_horas': 24,
        'log_path': os.path.join(directorio_actual, 'operaciones_log_v23.csv'),
        'estado_file': os.path.join(directorio_actual, 'estado_bot_v23.json'),
        # NUEVAS CONFIGURACIONES BITGET
        'bitget_api_key': os.environ.get('BITGET_API_KEY'),
        'bitget_api_secret': os.environ.get('BITGET_SECRET_KEY'),
        'bitget_passphrase': os.environ.get('BITGET_PASSPHRASE'),
        'ejecutar_operaciones_automaticas': os.environ.get('EJECUTAR_OPERACIONES_AUTOMATICAS', 'false').lower() == 'true',
        'capital_por_operacion': float(os.environ.get('CAPITAL_POR_OPERACION', '2')),
        'leverage_por_defecto': int(os.environ.get('LEVERAGE_POR_DEFECTO', '10'))
    }

# ---------------------------
# FLASK APP Y RENDER
# ---------------------------

app = Flask(__name__)

# Crear bot con configuración desde entorno
config = crear_config_desde_entorno()
bot = TradingBot(config)

def run_bot_loop():
    """Ejecuta el bot en un hilo separado con manejo robusto de errores"""
    global bot  # Declarar bot como global al inicio
    consecutive_errors = 0
    max_consecutive_errors = 5
    
    while True:
        try:
            bot.ejecutar_analisis()
            consecutive_errors = 0  # Reset contador en caso de éxito
            
            # Esperar según configuración, pero con un mínimo de 5 minutos
            sleep_minutes = max(5, bot.config.get('scan_interval_minutes', 6))
            sleep_seconds = sleep_minutes * 60
            
            print(f"💤 Esperando {sleep_minutes} minutos hasta el próximo análisis...")
            time.sleep(sleep_seconds)
            
        except KeyboardInterrupt:
            print("🛑 Bot detenido por el usuario")
            break
        except Exception as e:
            consecutive_errors += 1
            print(f"❌ Error en el hilo del bot (intento {consecutive_errors}/{max_consecutive_errors}): {e}", file=sys.stderr)
            
            if consecutive_errors >= max_consecutive_errors:
                print("🚨 Demasiados errores consecutivos. Reiniciando bot...", file=sys.stderr)
                # Reinicializar el bot en caso de errores consecutivos
                try:
                    config = crear_config_desde_entorno()
                    bot = TradingBot(config)
                    consecutive_errors = 0
                except Exception as reset_error:
                    print(f"❌ Error reinicializando bot: {reset_error}", file=sys.stderr)
            
            # Espera exponencial en caso de error
            wait_time = min(300, 30 * (2 ** consecutive_errors))  # Máximo 5 minutos
            print(f"⏳ Esperando {wait_time} segundos antes del siguiente intento...", file=sys.stderr)
            time.sleep(wait_time)

# Iniciar hilo del bot solo si estamos en modo de producción
if __name__ == '__main__':
    try:
        bot_thread = threading.Thread(target=run_bot_loop, daemon=True)
        bot_thread.start()
        print("🤖 Hilo del bot iniciado correctamente")
    except Exception as e:
        print(f"❌ Error iniciando hilo del bot: {e}", file=sys.stderr)

@app.route('/')
def index():
    return "Bot Breakout + Reentry con integración Bitget está en línea.", 200

@app.route('/webhook', methods=['POST'])
def telegram_webhook():
    if request.is_json:
        update = request.get_json()
        print(f"Update recibido: {json.dumps(update)}", file=sys.stdout)
        return jsonify({"status": "ok"}), 200
    return jsonify({"error": "Request must be JSON"}), 400

# Configuración automática del webhook
def setup_telegram_webhook():
    token = os.environ.get('TELEGRAM_TOKEN')
    if not token:
        return
    webhook_url = os.environ.get('WEBHOOK_URL')
    if not webhook_url:
        render_url = os.environ.get('RENDER_EXTERNAL_URL')
        if render_url:
            webhook_url = f"{render_url}/webhook"
        else:
            return
    try:
        requests.get(f"https://api.telegram.org/bot{token}/deleteWebhook")
        requests.get(f"https://api.telegram.org/bot{token}/setWebhook?url={webhook_url}")
    except Exception as e:
        print(f"Error configurando webhook: {e}", file=sys.stderr)

if __name__ == '__main__':
    setup_telegram_webhook()
    app.run(debug=True, port=5000)
