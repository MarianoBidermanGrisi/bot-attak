# bot_bitget_web_service_fixed.py
# Bot de Trading Automático para Bitget Futuros Perpetuos
# CORREGIDO para error 40020 - Parameter productType error
import requests
import time
import json
import os
import sys
from datetime import datetime, timedelta
import numpy as np
import math
import csv
import itertools
import statistics
import random
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import mplfinance as mpf
import pandas as pd
from io import BytesIO
import hmac
import hashlib
import base64
from flask import Flask, request, jsonify
import threading
import logging

# Configurar logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ---------------------------
# Cliente Bitget API CORREGIDO - ERROR 40020 FIXED
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
            # Preparar body string según documentación Bitget
            if isinstance(body, dict):
                body_str = json.dumps(body, separators=(',', ':')) if body else ''
            else:
                body_str = str(body) if body else ''
            
            # Construir mensaje para firmar (timestamp + method + requestPath + body)
            message = timestamp + method.upper() + request_path + body_str
            
            logger.debug(f"String para firma: {message[:100]}...")
            
            # Crear firma HMAC-SHA256
            mac = hmac.new(
                bytes(self.api_secret, 'utf-8'),
                bytes(message, 'utf-8'),
                digestmod=hashlib.sha256
            )
            
            # Codificar en base64
            signature = base64.b64encode(mac.digest()).decode()
            
            return signature
            
        except Exception as e:
            logger.error(f"Error generando firma: {e}")
            raise

    def _get_headers(self, method, request_path, body=''):
        """Obtener headers con firma para Bitget V2 - CORREGIDO"""
        try:
            # Timestamp en milisegundos (requerido por Bitget)
            timestamp = str(int(time.time() * 1000))
            
            # Generar firma
            sign = self._generate_signature(timestamp, method, request_path, body)
            
            # Headers para Bitget V2 API
            headers = {
                'Content-Type': 'application/json',
                'ACCESS-KEY': self.api_key,
                'ACCESS-SIGN': sign,
                'ACCESS-TIMESTAMP': timestamp,
                'ACCESS-PASSPHRASE': self.passphrase,
                'locale': 'en-US'
            }
            
            return headers
            
        except Exception as e:
            logger.error(f"Error creando headers: {e}")
            raise

    def verificar_credenciales(self):
        """Verificar que las credenciales sean válidas - CORREGIDO para error 40020"""
        try:
            logger.info("Verificando credenciales Bitget...")
            
            if not self.api_key or not self.api_secret or not self.passphrase:
                logger.error("Credenciales incompletas (faltan API Key, Secret o Passphrase)")
                return False
            
            # Intentar obtener información de cuenta con diferentes productTypes
            # Bitget V2 requiere 'USDT-FUTURES' o 'USDT-MIX' para futuros perpétuos
            accounts = self.get_account_info()
            if accounts:
                logger.info("✓ Credenciales verificadas exitosamente")
                for account in accounts:
                    if account.get('marginCoin') == 'USDT':
                        available = float(account.get('available', 0))
                        logger.info(f"✓ Balance disponible: {available:.2f} USDT")
                return True
            else:
                logger.error("✗ No se pudo verificar credenciales - respuesta vacía o error")
                return False
                
        except Exception as e:
            logger.error(f"Error verificando credenciales: {e}")
            return False

    def get_account_info(self, product_type='USDT-FUTURES'):
        """Obtener información de cuenta Bitget V2 - CORREGIDO ERROR 40020"""
        try:
            request_path = '/api/v2/mix/account/accounts'
            
            # CORRECCIÓN: Bitget V2 usa 'USDT-FUTURES' o 'USDT-MIX' para futuros
            # El error 40020 era porque usábamos 'umcbl' que es para otra versión
            params = {
                'productType': product_type,
                'marginCoin': 'USDT'
            }
            
            # Para GET, los parámetros van en el query string para la firma
            query_string = f"?productType={product_type}&marginCoin=USDT"
            full_request_path = request_path + query_string
            
            # Obtener headers con body vacío para GET
            headers = self._get_headers('GET', full_request_path, '')
            
            # Hacer la solicitud
            response = requests.get(
                f"{self.base_url}{request_path}",
                headers=headers,
                params=params,
                timeout=10
            )
            
            logger.info(f"Respuesta cuenta - Status: {response.status_code}")
            logger.debug(f"URL: {self.base_url}{request_path}")
            logger.debug(f"Params: {params}")
            logger.debug(f"Response: {response.text}")
            
            if response.status_code == 200:
                data = response.json()
                logger.info(f"Respuesta cuenta - Código: {data.get('code')}")
                
                if data.get('code') == '00000':
                    return data.get('data', [])
                else:
                    error_msg = data.get('msg', 'Unknown error')
                    error_code = data.get('code', 'Unknown')
                    logger.error(f"Error API: {error_code} - {error_msg}")
                    
                    # Si falla con 'USDT-FUTURES', intentar con 'USDT-MIX'
                    if error_code == '40020' and product_type == 'USDT-FUTURES':
                        logger.info("Intentando con productType='USDT-MIX'...")
                        return self.get_account_info('USDT-MIX')
            else:
                logger.error(f"Error HTTP: {response.status_code} - {response.text}")
                
            return None
            
        except Exception as e:
            logger.error(f"Error en get_account_info: {e}")
            return None

    def get_symbol_info(self, symbol):
        try:
            request_path = f'/api/v2/mix/market/contracts'
            params = {'productType': 'USDT-FUTURES'}  # CORREGIDO
            
            # Query string para firma
            query_string = f"?productType=USDT-FUTURES"
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
                    contracts = data.get('data', [])
                    for contract in contracts:
                        if contract.get('symbol') == symbol:
                            return contract
            
            # Si falla, intentar con USDT-MIX
            params = {'productType': 'USDT-MIX'}
            query_string = f"?productType=USDT-MIX"
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
                    contracts = data.get('data', [])
                    for contract in contracts:
                        if contract.get('symbol') == symbol:
                            return contract
            
            return None
        except Exception as e:
            logger.error(f"Error obteniendo info del símbolo: {e}")
            return None

    def place_order(self, symbol, side, order_type, size, price=None, 
                    client_order_id=None, time_in_force='normal'):
        try:
            request_path = '/api/v2/mix/order/place-order'
            body = {
                'symbol': symbol,
                'productType': 'USDT-FUTURES',  # CORREGIDO
                'marginCoin': 'USDT',
                'side': side,
                'orderType': order_type,
                'size': str(size),
                'timeInForce': time_in_force
            }
            if price:
                body['price'] = str(price)
            if client_order_id:
                body['clientOrderId'] = client_order_id
            
            headers = self._get_headers('POST', request_path, body)
            
            response = requests.post(
                self.base_url + request_path,
                headers=headers,
                json=body,
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                if data.get('code') == '00000':
                    logger.info(f"✓ Orden colocada: {data.get('data', {})}")
                    return data.get('data', {})
                else:
                    logger.error(f"Error en orden: {data.get('code')} - {data.get('msg')}")
                    return None
            else:
                logger.error(f"Error HTTP: {response.status_code} - {response.text}")
                return None
        except Exception as e:
            logger.error(f"Error colocando orden: {e}")
            return None

    def place_plan_order(self, symbol, side, trigger_price, order_type, size, 
                         price=None, plan_type='normal_plan'):
        try:
            request_path = '/api/v2/mix/order/place-plan-order'
            body = {
                'symbol': symbol,
                'productType': 'USDT-FUTURES',  # CORREGIDO
                'marginCoin': 'USDT',
                'side': side,
                'orderType': order_type,
                'triggerPrice': str(trigger_price),
                'size': str(size),
                'planType': plan_type,
                'triggerType': 'market_price'
            }
            if price:
                body['executePrice'] = str(price)
            
            headers = self._get_headers('POST', request_path, body)
            
            response = requests.post(
                self.base_url + request_path,
                headers=headers,
                json=body,
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                if data.get('code') == '00000':
                    return data.get('data', {})
            logger.warning(f"Error en plan order: {response.text}")
            return None
        except Exception as e:
            logger.error(f"Error colocando plan order: {e}")
            return None

    def set_leverage(self, symbol, leverage, hold_side='long'):
        try:
            request_path = '/api/v2/mix/account/set-leverage'
            body = {
                'symbol': symbol,
                'productType': 'USDT-FUTURES',  # CORREGIDO
                'marginCoin': 'USDT',
                'leverage': str(leverage),
                'holdSide': hold_side
            }
            
            headers = self._get_headers('POST', request_path, body)
            
            response = requests.post(
                self.base_url + request_path,
                headers=headers,
                json=body,
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                if data.get('code') == '00000':
                    logger.info(f"✓ Apalancamiento {leverage}x configurado para {symbol}")
                    return True
            logger.warning(f"Error configurando leverage: {response.text}")
            return False
        except Exception as e:
            logger.error(f"Error en set_leverage: {e}")
            return False

    def get_positions(self, symbol=None, product_type='USDT-FUTURES'):  # CORREGIDO
        try:
            request_path = '/api/v2/mix/position/all-position'
            params = {'productType': product_type, 'marginCoin': 'USDT'}
            if symbol:
                params['symbol'] = symbol
            
            # Query string para firma
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
            
            # Si falla, intentar con USDT-MIX
            if product_type == 'USDT-FUTURES':
                return self.get_positions(symbol, 'USDT-MIX')
            
            return []
        except Exception as e:
            logger.error(f"Error obteniendo posiciones: {e}")
            return []

    def get_klines(self, symbol, interval='5m', limit=200):
        try:
            interval_map = {
                '1m': '1m',
                '3m': '3m', 
                '5m': '5m',
                '15m': '15m',
                '30m': '30m',
                '1h': '1H',
                '4h': '4H',
                '1d': '1D'
            }
            bitget_interval = interval_map.get(interval, '5m')
            request_path = f'/api/v2/mix/market/candles'
            params = {
                'symbol': symbol,
                'productType': 'USDT-FUTURES',  # CORREGIDO
                'granularity': bitget_interval,
                'limit': limit
            }
            
            # No necesita autenticación para datos de mercado
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
                    # Intentar con USDT-MIX
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
                    
                    logger.warning(f"Error API klines: {data.get('msg', 'Unknown error')}")
            return None
        except Exception as e:
            logger.error(f"Error en get_klines: {e}")
            return None

# ---------------------------
# Optimizador IA (SIN CAMBIOS)
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
            logger.warning("No se encontró operaciones_log.csv (optimizador)")
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
            logger.info(f"No hay suficientes datos para optimizar (se requieren {self.min_samples}, hay {len(self.datos)})")
            return None
        mejor_score = -1e9
        mejores_param = None
        trend_values = [3, 5, 8, 10, 12, 15, 18, 20, 25, 30, 35, 40]
        strength_values = [3, 5, 8, 10, 12, 15, 18, 20, 25, 30]
        margin_values = [0.0005, 0.001, 0.0015, 0.002, 0.0025, 0.003, 0.004, 0.005, 0.008, 0.01]
        combos = list(itertools.product(trend_values, strength_values, margin_values))
        total = len(combos)
        logger.info(f"Optimizador: probando {total} combinaciones...")
        for idx, (t, s, m) in enumerate(combos, start=1):
            score = self.evaluar_configuracion(t, s, m)
            if idx % 100 == 0 or idx == total:
                logger.info(f"Probado {idx}/{total} combos (mejor score actual: {mejor_score:.4f})")
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
            logger.info(f"Optimizador: mejores parámetros encontrados: {mejores_param}")
            try:
                with open("mejores_parametros.json", "w", encoding='utf-8') as f:
                    json.dump(mejores_param, f, indent=2)
            except Exception as e:
                logger.warning(f"Error guardando mejores_parametros.json: {e}")
        else:
            logger.warning("No se encontró una configuración mejor")
        return mejores_param

# ---------------------------
# BOT PRINCIPAL - BITGET FUTURES (CON CORRECCIONES)
# ---------------------------
class TradingBot:
    def __init__(self, config):
        self.config = config
        self.log_path = config.get('log_path', 'operaciones_log.csv')
        self.auto_optimize = config.get('auto_optimize', True)
        
        # Inicializar cliente Bitget
        self.bitget = BitgetClient(
            api_key=config.get('bitget_api_key'),
            api_secret=config.get('bitget_api_secret'),
            passphrase=config.get('bitget_passphrase')
        )
        
        # Verificar credenciales inmediatamente
        self.credenciales_validas = self.bitget.verificar_credenciales()
        if not self.credenciales_validas:
            logger.error("⚠️ CREDENCIALES DE BITGET INVÁLIDAS ⚠️")
            logger.error("El bot funcionará en modo DEMO (solo análisis)")
            logger.error("Para operaciones reales, verifica en Render.com:")
            logger.error("1. BITGET_API_KEY")
            logger.error("2. BITGET_SECRET_KEY") 
            logger.error("3. BITGET_PASSPHRASE")
            logger.error("4. Permisos de API (lectura + trading)")
        
        self.leverage = config.get('leverage', 20)
        self.capital_por_operacion = config.get('capital_por_operacion', 5.0)
        self.ultima_optimizacion = datetime.now()
        self.operaciones_desde_optimizacion = 0
        self.total_operaciones = 0
        self.breakout_history = {}
        self.config_optima_por_simbolo = {}
        self.ultima_busqueda_config = {}
        self.breakouts_detectados = {}
        self.esperando_reentry = {}
        self.estado_file = config.get('estado_file', 'estado_bot.json')
        self.cargar_estado()
        
        parametros_optimizados = None
        if self.auto_optimize:
            try:
                ia = OptimizadorIA(log_path=self.log_path, min_samples=config.get('min_samples_optimizacion', 15))
                parametros_optimizados = ia.buscar_mejores_parametros()
            except Exception as e:
                logger.error(f"Error en optimización automática: {e}")
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

    def ejecutar_operacion_real(self, simbolo, tipo_operacion, precio_entrada, tp, sl, info_canal, config_optima):
        """Ejecutar operación real en Bitget - SOLO si credenciales son válidas"""
        if not self.credenciales_validas:
            logger.error(f"⚠️ Credenciales inválidas - Operación DEMO para {simbolo}")
            logger.info(f"DEMO: {tipo_operacion} {simbolo} @ {precio_entrada:.8f}")
            logger.info(f"DEMO: TP={tp:.8f}, SL={sl:.8f}")
            return {
                'tipo': tipo_operacion,
                'precio_entrada': precio_entrada,
                'take_profit': tp,
                'stop_loss': sl,
                'timestamp_entrada': datetime.now().isoformat(),
                'angulo_tendencia': info_canal['angulo_tendencia'],
                'pearson': info_canal['coeficiente_pearson'],
                'r2_score': info_canal['r2_score'],
                'ancho_canal_relativo': info_canal['ancho_canal'] / precio_entrada,
                'ancho_canal_porcentual': info_canal['ancho_canal_porcentual'],
                'nivel_fuerza': info_canal['nivel_fuerza'],
                'timeframe_utilizado': config_optima['timeframe'],
                'velas_utilizadas': config_optima['num_velas'],
                'stoch_k': info_canal['stoch_k'],
                'stoch_d': info_canal['stoch_d'],
                'breakout_usado': True,
                'leverage': self.leverage,
                'capital_usado': self.capital_por_operacion,
                'demo': True
            }
        
        try:
            logger.info(f"🚀 EJECUTANDO OPERACIÓN REAL EN BITGET")
            logger.info(f"Símbolo: {simbolo}")
            logger.info(f"Tipo: {tipo_operacion}")
            logger.info(f"Apalancamiento: {self.leverage}x")
            logger.info(f"Capital: ${self.capital_por_operacion}")
            
            # 1. Configurar apalancamiento
            hold_side = 'long' if tipo_operacion == 'LONG' else 'short'
            leverage_ok = self.bitget.set_leverage(simbolo, self.leverage, hold_side)
            if not leverage_ok:
                logger.error("Error configurando apalancamiento")
                return None
            time.sleep(0.5)
            
            # 2. Obtener información del símbolo
            symbol_info = self.bitget.get_symbol_info(simbolo)
            if not symbol_info:
                logger.error(f"No se pudo obtener info de {simbolo}")
                return None
            
            # 3. Calcular tamaño de la posición
            size_multiplier = float(symbol_info.get('sizeMultiplier', 1))
            min_trade_num = float(symbol_info.get('minTradeNum', 1))
            
            # Calcular cantidad en USD
            cantidad_usd = self.capital_por_operacion * self.leverage
            # Convertir a cantidad de contratos
            cantidad_contratos = cantidad_usd / precio_entrada
            cantidad_contratos = round(cantidad_contratos / size_multiplier) * size_multiplier
            
            # Verificar mínimo
            if cantidad_contratos < min_trade_num:
                cantidad_contratos = min_trade_num
            
            logger.info(f"Cantidad: {cantidad_contratos} contratos")
            logger.info(f"Valor nocional: ${cantidad_contratos * precio_entrada:.2f}")
            
            # 4. Abrir posición
            side = 'open_long' if tipo_operacion == 'LONG' else 'open_short'
            orden_entrada = self.bitget.place_order(
                symbol=simbolo,
                side=side,
                order_type='market',
                size=cantidad_contratos
            )
            
            if not orden_entrada:
                logger.error("Error abriendo posición")
                return None
            
            logger.info(f"✓ Posición abierta: {orden_entrada}")
            time.sleep(1)
            
            # 5. Colocar Stop Loss
            sl_side = 'close_long' if tipo_operacion == 'LONG' else 'close_short'
            orden_sl = self.bitget.place_plan_order(
                symbol=simbolo,
                side=sl_side,
                trigger_price=sl,
                order_type='market',
                size=cantidad_contratos,
                plan_type='loss_plan'
            )
            
            if orden_sl:
                logger.info(f"✓ Stop Loss configurado en: {sl:.8f}")
            else:
                logger.warning("Error configurando Stop Loss")
            
            time.sleep(0.5)
            
            # 6. Colocar Take Profit
            orden_tp = self.bitget.place_plan_order(
                symbol=simbolo,
                side=sl_side,
                trigger_price=tp,
                order_type='market',
                size=cantidad_contratos,
                plan_type='normal_plan'
            )
            
            if orden_tp:
                logger.info(f"✓ Take Profit configurado en: {tp:.8f}")
            else:
                logger.warning("Error configurando Take Profit")
            
            # 7. Registrar operación
            operacion_data = {
                'orden_entrada': orden_entrada,
                'orden_sl': orden_sl,
                'orden_tp': orden_tp,
                'cantidad_contratos': cantidad_contratos,
                'precio_entrada': precio_entrada,
                'take_profit': tp,
                'stop_loss': sl,
                'leverage': self.leverage,
                'capital_usado': self.capital_por_operacion,
                'tipo': tipo_operacion,
                'timestamp_entrada': datetime.now().isoformat(),
                'angulo_tendencia': info_canal['angulo_tendencia'],
                'pearson': info_canal['coeficiente_pearson'],
                'r2_score': info_canal['r2_score'],
                'ancho_canal_relativo': info_canal['ancho_canal'] / precio_entrada,
                'ancho_canal_porcentual': info_canal['ancho_canal_porcentual'],
                'nivel_fuerza': info_canal['nivel_fuerza'],
                'timeframe_utilizado': config_optima['timeframe'],
                'velas_utilizadas': config_optima['num_velas'],
                'stoch_k': info_canal['stoch_k'],
                'stoch_d': info_canal['stoch_d'],
                'breakout_usado': True,
                'demo': False
            }
            
            logger.info(f"✅ OPERACIÓN EJECUTADA EXITOSAMENTE")
            logger.info(f"ID Orden: {orden_entrada.get('orderId', 'N/A')}")
            logger.info(f"Contratos: {cantidad_contratos}")
            logger.info(f"Entrada: {precio_entrada:.8f}")
            logger.info(f"SL: {sl:.8f} (-2%)")
            logger.info(f"TP: {tp:.8f}")
            
            return operacion_data
            
        except Exception as e:
            logger.error(f"Error ejecutando operación: {e}")
            return None

    def cargar_estado(self):
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
                logger.info("Estado anterior cargado correctamente")
                logger.info(f"Operaciones activas: {len(self.operaciones_activas)}")
                logger.info(f"Esperando reentry: {len(self.esperando_reentry)}")
        except Exception as e:
            logger.warning(f"Error cargando estado previo: {e}")

    def guardar_estado(self):
        try:
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
                'timestamp_guardado': datetime.now().isoformat(),
                'credenciales_validas': self.credenciales_validas
            }
            with open(self.estado_file, 'w', encoding='utf-8') as f:
                json.dump(estado, f, indent=2, ensure_ascii=False)
            logger.info("Estado guardado correctamente")
        except Exception as e:
            logger.warning(f"Error guardando estado: {e}")

    def buscar_configuracion_optima_simbolo(self, simbolo):
        if simbolo in self.config_optima_por_simbolo:
            config_optima = self.config_optima_por_simbolo[simbolo]
            ultima_busqueda = self.ultima_busqueda_config.get(simbolo)
            if ultima_busqueda and (datetime.now() - ultima_busqueda).total_seconds() < 7200:
                return config_optima
            else:
                logger.info(f"Reevaluando configuración para {simbolo} (pasó 2 horas)")
        
        logger.info(f"Buscando configuración óptima para {simbolo}...")
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
                            puntaje_total = puntaje_timeframe + puntaje_ancho
                            
                            if puntaje_total > mejor_puntaje:
                                mejor_puntaje = puntaje_total
                                mejor_config = {
                                    'timeframe': timeframe,
                                    'num_velas': num_velas,
                                    'ancho_canal': ancho_actual,
                                    'puntaje_total': puntaje_total
                                }
                except Exception:
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
                        
                        if (canal_info['nivel_fuerza'] >= 2 and 
                            abs(canal_info['coeficiente_pearson']) >= 0.4 and 
                            canal_info['r2_score'] >= 0.4):
                            
                            ancho_actual = canal_info['ancho_canal_porcentual']
                            puntaje_ancho = ancho_actual * 10
                            puntaje_timeframe = prioridad_timeframe.get(timeframe, 50) * 100
                            puntaje_total = puntaje_timeframe + puntaje_ancho
                            
                            if puntaje_total > mejor_puntaje:
                                mejor_puntaje = puntaje_total
                                mejor_config = {
                                    'timeframe': timeframe,
                                    'num_velas': num_velas,
                                    'ancho_canal': ancho_actual,
                                    'puntaje_total': puntaje_total
                                }
                    except Exception:
                        continue
        
        if mejor_config:
            self.config_optima_por_simbolo[simbolo] = mejor_config
            self.ultima_busqueda_config[simbolo] = datetime.now()
            logger.info(f"Config óptima: {mejor_config['timeframe']} - {mejor_config['num_velas']} velas - Ancho: {mejor_config['ancho_canal']:.1f}%")
        
        return mejor_config

    def obtener_datos_mercado_config(self, simbolo, timeframe, num_velas):
        try:
            klines = self.bitget.get_klines(simbolo, timeframe, num_velas + 14)
            if not klines or len(klines) == 0:
                return None
            
            # Bitget retorna en orden descendente, invertir
            klines.reverse()
            maximos = [float(vela[2]) for vela in klines]
            minimos = [float(vela[3]) for vela in klines]
            cierres = [float(vela[4]) for vela in klines]
            tiempos = list(range(len(klines)))
            
            return {
                'maximos': maximos,
                'minimos': minimos,
                'cierres': cierres,
                'tiempos': tiempos,
                'precio_actual': cierres[-1] if cierres else 0,
                'timeframe': timeframe,
                'num_velas': num_velas
            }
        except Exception as e:
            logger.warning(f"Error obteniendo datos de {simbolo}: {e}")
            return None

    def calcular_canal_regresion_config(self, datos_mercado, candle_period):
        if not datos_mercado or len(datos_mercado['maximos']) < candle_period:
            return None
        
        start_idx = -candle_period
        tiempos = datos_mercado['tiempos'][start_idx:]
        maximos = datos_mercado['maximos'][start_idx:]
        minimos = datos_mercado['minimos'][start_idx:]
        cierres = datos_mercado['cierres'][start_idx:]
        
        tiempos_reg = list(range(len(tiempos)))
        reg_max = self.calcular_regresion_lineal(tiempos_reg, maximos)
        reg_min = self.calcular_regresion_lineal(tiempos_reg, minimos)
        reg_close = self.calcular_regresion_lineal(tiempos_reg, cierres)
        
        if not all([reg_max, reg_min, reg_close]):
            return None
        
        pendiente_max, intercepto_max = reg_max
        pendiente_min, intercepto_min = reg_min
        pendiente_cierre, intercepto_cierre = reg_close
        
        tiempo_actual = tiempos_reg[-1]
        resistencia_media = pendiente_max * tiempo_actual + intercepto_max
        soporte_media = pendiente_min * tiempo_actual + intercepto_min
        
        diferencias_max = [maximos[i] - (pendiente_max * tiempos_reg[i] + intercepto_max) for i in range(len(tiempos_reg))]
        diferencias_min = [minimos[i] - (pendiente_min * tiempos_reg[i] + intercepto_min) for i in range(len(tiempos_reg))]
        
        desviacion_max = np.std(diferencias_max) if diferencias_max else 0
        desviacion_min = np.std(diferencias_min) if diferencias_min else 0
        
        resistencia_superior = resistencia_media + desviacion_max
        soporte_inferior = soporte_media - desviacion_min
        precio_actual = datos_mercado['precio_actual']
        
        pearson, angulo_tendencia = self.calcular_pearson_y_angulo(tiempos_reg, cierres)
        fuerza_texto, nivel_fuerza = self.clasificar_fuerza_tendencia(angulo_tendencia)
        direccion = self.determinar_direccion_tendencia(angulo_tendencia, 1)
        
        stoch_k, stoch_d = self.calcular_stochastic(datos_mercado)
        
        precio_medio = (resistencia_superior + soporte_inferior) / 2
        ancho_canal_absoluto = resistencia_superior - soporte_inferior
        ancho_canal_porcentual = (ancho_canal_absoluto / precio_medio) * 100
        
        return {
            'resistencia': resistencia_superior,
            'soporte': soporte_inferior,
            'resistencia_media': resistencia_media,
            'soporte_media': soporte_media,
            'linea_tendencia': pendiente_cierre * tiempo_actual + intercepto_cierre,
            'pendiente_tendencia': pendiente_cierre,
            'precio_actual': precio_actual,
            'ancho_canal': ancho_canal_absoluto,
            'ancho_canal_porcentual': ancho_canal_porcentual,
            'angulo_tendencia': angulo_tendencia,
            'coeficiente_pearson': pearson,
            'fuerza_texto': fuerza_texto,
            'nivel_fuerza': nivel_fuerza,
            'direccion': direccion,
            'r2_score': self.calcular_r2(cierres, tiempos_reg, pendiente_cierre, intercepto_cierre),
            'pendiente_resistencia': pendiente_max,
            'pendiente_soporte': pendiente_min,
            'stoch_k': stoch_k,
            'stoch_d': stoch_d,
            'timeframe': datos_mercado.get('timeframe', 'N/A'),
            'num_velas': candle_period
        }

    def detectar_breakout(self, simbolo, info_canal, datos_mercado):
        if not info_canal:
            return None
        
        if info_canal['ancho_canal_porcentual'] < self.config.get('min_channel_width_percent', 4.0):
            return None
        
        precio_cierre = datos_mercado['cierres'][-1]
        resistencia = info_canal['resistencia']
        soporte = info_canal['soporte']
        angulo = info_canal['angulo_tendencia']
        direccion = info_canal['direccion']
        nivel_fuerza = info_canal['nivel_fuerza']
        r2 = info_canal['r2_score']
        pearson = info_canal['coeficiente_pearson']
        
        if abs(angulo) < self.config.get('min_trend_strength_degrees', 16):
            return None
        
        if abs(pearson) < 0.4 or r2 < 0.4:
            return None
        
        if simbolo in self.breakouts_detectados:
            ultimo_breakout = self.breakouts_detectados[simbolo]
            tiempo_desde_ultimo = (datetime.now() - ultimo_breakout['timestamp']).total_seconds() / 60
            if tiempo_desde_ultimo < 115:
                return None
        
        if direccion == "🟢 ALCISTA" and nivel_fuerza >= 2:
            if precio_cierre > resistencia:
                logger.info(f"{simbolo} - BREAKOUT LONG: {precio_cierre:.8f} > Resistencia: {resistencia:.8f}")
                return "BREAKOUT_LONG"
        elif direccion == "🔴 BAJISTA" and nivel_fuerza >= 2:
            if precio_cierre < soporte:
                logger.info(f"{simbolo} - BREAKOUT SHORT: {precio_cierre:.8f} < Soporte: {soporte:.8f}")
                return "BREAKOUT_SHORT"
        
        return None

    def detectar_reentry(self, simbolo, info_canal, datos_mercado):
        if simbolo not in self.esperando_reentry:
            return None
        
        breakout_info = self.esperando_reentry[simbolo]
        tipo_breakout = breakout_info['tipo']
        timestamp_breakout = breakout_info['timestamp']
        tiempo_desde_breakout = (datetime.now() - timestamp_breakout).total_seconds() / 60
        
        if tiempo_desde_breakout > 120:
            logger.info(f"{simbolo} - Timeout de reentry (>120 min), cancelando espera")
            del self.esperando_reentry[simbolo]
            if simbolo in self.breakouts_detectados:
                del self.breakouts_detectados[simbolo]
            return None
        
        precio_actual = datos_mercado['precio_actual']
        resistencia = info_canal['resistencia']
        soporte = info_canal['soporte']
        stoch_k = info_canal['stoch_k']
        stoch_d = info_canal['stoch_d']
        
        tolerancia = 0.001 * precio_actual
        
        if tipo_breakout == "BREAKOUT_LONG":
            if soporte <= precio_actual <= resistencia:
                distancia_soporte = abs(precio_actual - soporte)
                if distancia_soporte <= tolerancia and stoch_k <= 30 and stoch_d <= 30:
                    logger.info(f"{simbolo} - REENTRY LONG confirmado! Entrada en soporte con Stoch oversold")
                    if simbolo in self.breakouts_detectados:
                        del self.breakouts_detectados[simbolo]
                    return "LONG"
        elif tipo_breakout == "BREAKOUT_SHORT":
            if soporte <= precio_actual <= resistencia:
                distancia_resistencia = abs(precio_actual - resistencia)
                if distancia_resistencia <= tolerancia and stoch_k >= 70 and stoch_d >= 70:
                    logger.info(f"{simbolo} - REENTRY SHORT confirmado! Entrada en resistencia con Stoch overbought")
                    if simbolo in self.breakouts_detectados:
                        del self.breakouts_detectados[simbolo]
                    return "SHORT"
        
        return None

    def calcular_niveles_entrada(self, tipo_operacion, info_canal, precio_actual):
        if not info_canal:
            return None, None, None
        
        resistencia = info_canal['resistencia']
        soporte = info_canal['soporte']
        
        # SL FIJO AL 2%
        sl_porcentaje = 0.02
        
        if tipo_operacion == "LONG":
            precio_entrada = precio_actual
            stop_loss = precio_entrada * (1 - sl_porcentaje)
            # TP en la parte opuesta del canal (resistencia)
            take_profit = resistencia
        else:
            precio_entrada = precio_actual
            stop_loss = precio_entrada * (1 + sl_porcentaje)
            # TP en la parte opuesta del canal (soporte)
            take_profit = soporte
        
        return precio_entrada, take_profit, stop_loss

    def escanear_mercado(self):
        logger.info(f"Escaneando {len(self.config.get('symbols', []))} símbolos (Estrategia: Breakout + Reentry)...")
        senales_encontradas = 0
        
        for simbolo in self.config.get('symbols', []):
            try:
                if simbolo in self.operaciones_activas:
                    logger.info(f"{simbolo} - Operación activa, omitiendo...")
                    continue
                
                config_optima = self.buscar_configuracion_optima_simbolo(simbolo)
                if not config_optima:
                    logger.error(f"{simbolo} - No se encontró configuración válida")
                    continue
                
                datos_mercado = self.obtener_datos_mercado_config(
                    simbolo, config_optima['timeframe'], config_optima['num_velas']
                )
                
                if not datos_mercado:
                    logger.error(f"{simbolo} - Error obteniendo datos")
                    continue
                
                info_canal = self.calcular_canal_regresion_config(datos_mercado, config_optima['num_velas'])
                
                if not info_canal:
                    logger.error(f"{simbolo} - Error calculando canal")
                    continue
                
                estado_stoch = ""
                if info_canal['stoch_k'] <= 30:
                    estado_stoch = "📉 OVERSOLD"
                elif info_canal['stoch_k'] >= 70:
                    estado_stoch = "📈 OVERBOUGHT"
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
                    posicion = "🎯 DENTRO"
                
                logger.info(
                    f"{simbolo} - {config_optima['timeframe']} - {config_optima['num_velas']}v | "
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
                        self.breakouts_detectados[simbolo] = {
                            'tipo': tipo_breakout,
                            'timestamp': datetime.now(),
                            'precio_breakout': precio_actual
                        }
                        logger.info(f"{simbolo} - Breakout registrado, esperando reingreso...")
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
                        logger.info(f"{simbolo} - Señal reciente, omitiendo...")
                        continue
                
                breakout_info = self.esperando_reentry[simbolo]
                
                # EJECUTAR OPERACIÓN (real o demo según credenciales)
                operacion_ejecutada = self.ejecutar_operacion_real(
                    simbolo, tipo_operacion, precio_entrada, tp, sl, 
                    info_canal, config_optima
                )
                
                if operacion_ejecutada:
                    self.generar_senal_operacion(
                        simbolo, tipo_operacion, precio_entrada, tp, sl, 
                        info_canal, datos_mercado, config_optima, breakout_info
                    )
                    
                    self.operaciones_activas[simbolo] = operacion_ejecutada
                    senales_encontradas += 1
                    self.breakout_history[simbolo] = datetime.now()
                    del self.esperando_reentry[simbolo]
                else:
                    logger.error(f"{simbolo} - Error ejecutando operación, reintentando más tarde")
                    
            except Exception as e:
                logger.error(f"Error analizando {simbolo}: {e}")
                continue
        
        if self.esperando_reentry:
            logger.info(f"Esperando reingreso en {len(self.esperando_reentry)} símbolos:")
            for simbolo, info in self.esperando_reentry.items():
                tiempo_espera = (datetime.now() - info['timestamp']).total_seconds() / 60
                logger.info(f"{simbolo} - {info['tipo']} - Esperando {tiempo_espera:.1f} min")
        
        if self.breakouts_detectados:
            logger.info(f"Breakouts detectados recientemente:")
            for simbolo, info in self.breakouts_detectados.items():
                tiempo_desde_deteccion = (datetime.now() - info['timestamp']).total_seconds() / 60
                logger.info(f"{simbolo} - {info['tipo']} - Hace {tiempo_desde_deteccion:.1f} min")
        
        if senales_encontradas > 0:
            logger.info(f"Se encontraron {senales_encontradas} señales de trading")
        else:
            logger.info("No se encontraron señales en este ciclo")
        
        return senales_encontradas

    def generar_senal_operacion(self, simbolo, tipo_operacion, precio_entrada, tp, sl,
                            info_canal, datos_mercado, config_optima, breakout_info=None):
        if simbolo in self.senales_enviadas:
            return
        
        if precio_entrada is None or tp is None or sl is None:
            logger.error(f"Niveles inválidos para {simbolo}, omitiendo señal")
            return
        
        riesgo = abs(precio_entrada - sl)
        beneficio = abs(tp - precio_entrada)
        ratio_rr = beneficio / riesgo if riesgo > 0 else 0
        sl_percent = abs((sl - precio_entrada) / precio_entrada) * 100
        tp_percent = abs((tp - precio_entrada) / precio_entrada) * 100
        
        stoch_estado = "📉 SOBREVENTA" if tipo_operacion == "LONG" else "📈 SOBRECOMPRA"
        
        breakout_texto = ""
        if breakout_info:
            tiempo_breakout = (datetime.now() - breakout_info['timestamp']).total_seconds() / 60
            breakout_texto = f"""
🚀 <b>BREAKOUT + REENTRY DETECTADO:</b>
⏰ Tiempo desde breakout: {tiempo_breakout:.1f} minutos
💰 Precio breakout: {breakout_info['precio_breakout']:.8f}
"""
        
        modo = "⚠️ MODO DEMO ⚠️" if not self.credenciales_validas else "🚀 BITGET REAL 🚀"
        
        mensaje = f"""
🎯 <b>SEÑAL DE {tipo_operacion} - {simbolo}</b>
💎 <b>{modo} - {self.leverage}x LEVERAGE</b>
{breakout_texto}
⏱️ <b>Configuración óptima:</b>
📊 Timeframe: {config_optima['timeframe']}
🕯️ Velas: {config_optima['num_velas']}
📏 Ancho Canal: {info_canal['ancho_canal_porcentual']:.1f}% ⭐
💰 <b>Precio Actual:</b> {datos_mercado['precio_actual']:.8f}
🎯 <b>Entrada:</b> {precio_entrada:.8f}
🛑 <b>Stop Loss:</b> {sl:.8f} (-2% FIJO)
🎯 <b>Take Profit:</b> {tp:.8f}
📊 <b>Ratio R/B:</b> {ratio_rr:.2f}:1
🎯 <b>SL:</b> {sl_percent:.2f}%
🎯 <b>TP:</b> {tp_percent:.2f}%
💰 <b>Riesgo:</b> {riesgo:.8f}
🎯 <b>Beneficio Objetivo:</b> {beneficio:.8f}
💵 <b>Capital usado:</b> ${self.capital_por_operacion}
⚡ <b>Apalancamiento:</b> {self.leverage}x
💎 <b>Valor posición:</b> ${self.capital_por_operacion * self.leverage:.2f}
📈 <b>Tendencia:</b> {info_canal['direccion']}
💪 <b>Fuerza:</b> {info_canal['fuerza_texto']}
📐 <b>Ángulo:</b> {info_canal['angulo_tendencia']:.1f}°
📊 <b>Pearson:</b> {info_canal['coeficiente_pearson']:.3f}
🎯 <b>R² Score:</b> {info_canal['r2_score']:.3f}
🎰 <b>Stochástico:</b> {stoch_estado}
📊 <b>Stoch K:</b> {info_canal['stoch_k']:.1f}
📈 <b>Stoch D:</b> {info_canal['stoch_d']:.1f}
⏰ <b>Hora:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
💡 <b>Estrategia:</b> BREAKOUT + REENTRY con confirmación Stochastic
🤖 <b>Exchange:</b> Bitget Futuros Perpetuos
        """
        
        token = self.config.get('telegram_token')
        chat_ids = self.config.get('telegram_chat_ids', [])
        
        if token and chat_ids:
            try:
                logger.info(f"Generando gráfico para {simbolo}...")
                buf = self.generar_grafico_profesional(simbolo, info_canal, datos_mercado, 
                                                      precio_entrada, tp, sl, tipo_operacion)
                if buf:
                    logger.info(f"Enviando gráfico por Telegram...")
                    self.enviar_grafico_telegram(buf, token, chat_ids)
                    time.sleep(1)
                
                self._enviar_telegram_simple(mensaje, token, chat_ids)
                logger.info(f"Señal {tipo_operacion} para {simbolo} enviada")
            except Exception as e:
                logger.error(f"Error enviando señal: {e}")
        
        self.senales_enviadas.add(simbolo)
        self.total_operaciones += 1

    def verificar_cierre_operaciones(self):
        if not self.operaciones_activas:
            return []
        
        operaciones_cerradas = []
        
        for simbolo, operacion in list(self.operaciones_activas.items()):
            try:
                # Si es operación demo, cerrar después de 5-15 minutos aleatorios
                if operacion.get('demo', False):
                    tiempo_entrada = datetime.fromisoformat(operacion['timestamp_entrada'])
                    tiempo_actual = datetime.now()
                    duracion_minutos = (tiempo_actual - tiempo_entrada).total_seconds() / 60
                    
                    # Cerrar demo después de tiempo aleatorio (5-15 min)
                    if duracion_minutos > random.uniform(5, 15):
                        tipo = operacion['tipo']
                        precio_entrada = operacion['precio_entrada']
                        tp = operacion['take_profit']
                        sl = operacion['stop_loss']
                        
                        # Precio de salida aleatorio entre SL y TP
                        if tipo == "LONG":
                            precio_salida = random.uniform(
                                min(precio_entrada * 0.98, precio_entrada * 1.02),
                                max(precio_entrada * 0.98, precio_entrada * 1.02)
                            )
                            pnl_percent = ((precio_salida - precio_entrada) / precio_entrada) * 100
                        else:
                            precio_salida = random.uniform(
                                min(precio_entrada * 0.98, precio_entrada * 1.02),
                                max(precio_entrada * 0.98, precio_entrada * 1.02)
                            )
                            pnl_percent = ((precio_entrada - precio_salida) / precio_entrada) * 100
                        
                        pnl_percent_real = pnl_percent * self.leverage
                        
                        # Determinar resultado
                        if tipo == "LONG":
                            if precio_salida >= tp * 0.998:
                                resultado = "TP"
                            elif precio_salida <= sl * 1.002:
                                resultado = "SL"
                            else:
                                resultado = "CIERRE_MANUAL"
                        else:
                            if precio_salida <= tp * 1.002:
                                resultado = "TP"
                            elif precio_salida >= sl * 0.998:
                                resultado = "SL"
                            else:
                                resultado = "CIERRE_MANUAL"
                        
                        datos_operacion = {
                            'timestamp': datetime.now().isoformat(),
                            'symbol': simbolo,
                            'tipo': tipo,
                            'precio_entrada': precio_entrada,
                            'take_profit': tp,
                            'stop_loss': sl,
                            'precio_salida': precio_salida,
                            'resultado': resultado,
                            'pnl_percent': pnl_percent_real,
                            'duracion_minutos': duracion_minutos,
                            'angulo_tendencia': operacion.get('angulo_tendencia', 0),
                            'pearson': operacion.get('pearson', 0),
                            'r2_score': operacion.get('r2_score', 0),
                            'ancho_canal_relativo': operacion.get('ancho_canal_relativo', 0),
                            'ancho_canal_porcentual': operacion.get('ancho_canal_porcentual', 0),
                            'nivel_fuerza': operacion.get('nivel_fuerza', 1),
                            'timeframe_utilizado': operacion.get('timeframe_utilizado', 'N/A'),
                            'velas_utilizadas': operacion.get('velas_utilizadas', 0),
                            'stoch_k': operacion.get('stoch_k', 0),
                            'stoch_d': operacion.get('stoch_d', 0),
                            'breakout_usado': operacion.get('breakout_usado', False),
                            'leverage': self.leverage,
                            'capital_usado': self.capital_por_operacion,
                            'demo': True
                        }
                        
                        mensaje_cierre = self.generar_mensaje_cierre(datos_operacion)
                        token = self.config.get('telegram_token')
                        chats = self.config.get('telegram_chat_ids', [])
                        
                        if token and chats:
                            try:
                                self._enviar_telegram_simple(mensaje_cierre, token, chats)
                            except Exception:
                                pass
                        
                        self.registrar_operacion(datos_operacion)
                        operaciones_cerradas.append(simbolo)
                        del self.operaciones_activas[simbolo]
                        
                        if simbolo in self.senales_enviadas:
                            self.senales_enviadas.remove(simbolo)
                        
                        self.operaciones_desde_optimizacion += 1
                        logger.info(f"{simbolo} Demo operación {resultado} - PnL: {pnl_percent_real:.2f}%")
                        continue
                
                # Para operaciones reales, consultar Bitget
                if self.credenciales_validas:
                    positions = self.bitget.get_positions(simbolo)
                    posicion_abierta = False
                    
                    for pos in positions:
                        total = float(pos.get('total', 0))
                        if total > 0:
                            posicion_abierta = True
                            break
                    
                    if not posicion_abierta:
                        config_optima = self.config_optima_por_simbolo.get(simbolo)
                        if config_optima:
                            datos = self.obtener_datos_mercado_config(
                                simbolo, config_optima['timeframe'], 50
                            )
                            
                            if datos:
                                precio_actual = datos['precio_actual']
                                tipo = operacion['tipo']
                                precio_entrada = operacion['precio_entrada']
                                tp = operacion['take_profit']
                                sl = operacion['stop_loss']
                                
                                if tipo == "LONG":
                                    if precio_actual >= tp * 0.998:
                                        resultado = "TP"
                                    elif precio_actual <= sl * 1.002:
                                        resultado = "SL"
                                    else:
                                        resultado = "CIERRE_MANUAL"
                                    pnl_percent = ((precio_actual - precio_entrada) / precio_entrada) * 100
                                else:
                                    if precio_actual <= tp * 1.002:
                                        resultado = "TP"
                                    elif precio_actual >= sl * 0.998:
                                        resultado = "SL"
                                    else:
                                        resultado = "CIERRE_MANUAL"
                                    pnl_percent = ((precio_entrada - precio_actual) / precio_entrada) * 100
                                
                                pnl_percent_real = pnl_percent * self.leverage
                                tiempo_entrada = datetime.fromisoformat(operacion['timestamp_entrada'])
                                duracion_minutos = (datetime.now() - tiempo_entrada).total_seconds() / 60
                                
                                datos_operacion = {
                                    'timestamp': datetime.now().isoformat(),
                                    'symbol': simbolo,
                                    'tipo': tipo,
                                    'precio_entrada': precio_entrada,
                                    'take_profit': tp,
                                    'stop_loss': sl,
                                    'precio_salida': precio_actual,
                                    'resultado': resultado,
                                    'pnl_percent': pnl_percent_real,
                                    'duracion_minutos': duracion_minutos,
                                    'angulo_tendencia': operacion.get('angulo_tendencia', 0),
                                    'pearson': operacion.get('pearson', 0),
                                    'r2_score': operacion.get('r2_score', 0),
                                    'ancho_canal_relativo': operacion.get('ancho_canal_relativo', 0),
                                    'ancho_canal_porcentual': operacion.get('ancho_canal_porcentual', 0),
                                    'nivel_fuerza': operacion.get('nivel_fuerza', 1),
                                    'timeframe_utilizado': operacion.get('timeframe_utilizado', 'N/A'),
                                    'velas_utilizadas': operacion.get('velas_utilizadas', 0),
                                    'stoch_k': operacion.get('stoch_k', 0),
                                    'stoch_d': operacion.get('stoch_d', 0),
                                    'breakout_usado': operacion.get('breakout_usado', False),
                                    'leverage': self.leverage,
                                    'capital_usado': self.capital_por_operacion,
                                    'demo': False
                                }
                                
                                mensaje_cierre = self.generar_mensaje_cierre(datos_operacion)
                                token = self.config.get('telegram_token')
                                chats = self.config.get('telegram_chat_ids', [])
                                
                                if token and chats:
                                    try:
                                        self._enviar_telegram_simple(mensaje_cierre, token, chats)
                                    except Exception:
                                        pass
                                
                                self.registrar_operacion(datos_operacion)
                                operaciones_cerradas.append(simbolo)
                                del self.operaciones_activas[simbolo]
                                
                                if simbolo in self.senales_enviadas:
                                    self.senales_enviadas.remove(simbolo)
                                
                                self.operaciones_desde_optimizacion += 1
                                logger.info(f"{simbolo} Operación REAL {resultado} - PnL: {pnl_percent_real:.2f}%")
            
            except Exception as e:
                logger.error(f"Error verificando operación {simbolo}: {e}")
                continue
        
        return operaciones_cerradas

    def generar_mensaje_cierre(self, datos_operacion):
        emoji = "🟢" if datos_operacion['resultado'] == "TP" else "🔴"
        color_emoji = "✅" if datos_operacion['resultado'] == "TP" else "❌"
        modo = "⚠️ DEMO ⚠️" if datos_operacion.get('demo', False) else "🚀 REAL 🚀"
        
        if datos_operacion['tipo'] == 'LONG':
            pnl_absoluto = datos_operacion['precio_salida'] - datos_operacion['precio_entrada']
        else:
            pnl_absoluto = datos_operacion['precio_entrada'] - datos_operacion['precio_salida']
        
        pnl_usd = (datos_operacion['pnl_percent'] / 100) * datos_operacion.get('capital_usado', self.capital_por_operacion)
        breakout_usado = "🚀 Sí" if datos_operacion.get('breakout_usado', False) else "❌ No"
        
        mensaje = f"""
{emoji} <b>OPERACIÓN CERRADA - {datos_operacion['symbol']}</b>
{color_emoji} <b>RESULTADO: {datos_operacion['resultado']} - {modo}</b>
📊 Tipo: {datos_operacion['tipo']}
💰 Entrada: {datos_operacion['precio_entrada']:.8f}
🎯 Salida: {datos_operacion['precio_salida']:.8f}
💵 PnL Absoluto: {pnl_absoluto:.8f}
📈 PnL %: {datos_operacion['pnl_percent']:.2f}% (con {datos_operacion.get('leverage', self.leverage)}x)
💎 PnL USD: ${pnl_usd:.2f}
⏰ Duración: {datos_operacion['duracion_minutos']:.1f} minutos
💰 Capital usado: ${datos_operacion.get('capital_usado', self.capital_por_operacion)}
⚡ Leverage: {datos_operacion.get('leverage', self.leverage)}x
🚀 Breakout+Reentry: {breakout_usado}
📐 Ángulo: {datos_operacion['angulo_tendencia']:.1f}°
📊 Pearson: {datos_operacion['pearson']:.3f}
🎯 R²: {datos_operacion['r2_score']:.3f}
📏 Ancho: {datos_operacion.get('ancho_canal_porcentual', 0):.1f}%
⏱️ TF: {datos_operacion.get('timeframe_utilizado', 'N/A')}
🕯️ Velas: {datos_operacion.get('velas_utilizadas', 0)}
🕒 {datos_operacion['timestamp']}
        """
        return mensaje

    def inicializar_log(self):
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
                    'stoch_k', 'stoch_d', 'breakout_usado', 'leverage', 'capital_usado', 'demo'
                ])

    def registrar_operacion(self, datos_operacion):
        with open(self.archivo_log, 'a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow([
                datos_operacion['timestamp'],
                datos_operacion['symbol'],
                datos_operacion['tipo'],
                datos_operacion['precio_entrada'],
                datos_operacion['take_profit'],
                datos_operacion['stop_loss'],
                datos_operacion['precio_salida'],
                datos_operacion['resultado'],
                datos_operacion['pnl_percent'],
                datos_operacion['duracion_minutos'],
                datos_operacion['angulo_tendencia'],
                datos_operacion['pearson'],
                datos_operacion['r2_score'],
                datos_operacion.get('ancho_canal_relativo', 0),
                datos_operacion.get('ancho_canal_porcentual', 0),
                datos_operacion.get('nivel_fuerza', 1),
                datos_operacion.get('timeframe_utilizado', 'N/A'),
                datos_operacion.get('velas_utilizadas', 0),
                datos_operacion.get('stoch_k', 0),
                datos_operacion.get('stoch_d', 0),
                datos_operacion.get('breakout_usado', False),
                datos_operacion.get('leverage', self.leverage),
                datos_operacion.get('capital_usado', self.capital_por_operacion),
                datos_operacion.get('demo', False)
            ])

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

    def generar_grafico_profesional(self, simbolo, info_canal, datos_mercado, precio_entrada, tp, sl, tipo_operacion):
        try:
            config_optima = self.config_optima_por_simbolo.get(simbolo)
            if not config_optima:
                return None
            
            klines = self.bitget.get_klines(simbolo, config_optima['timeframe'], config_optima['num_velas'])
            if not klines:
                return None
            
            klines.reverse()
            df_data = []
            
            for kline in klines:
                df_data.append({
                    'Date': pd.to_datetime(int(kline[0]), unit='ms'),
                    'Open': float(kline[1]),
                    'High': float(kline[2]),
                    'Low': float(kline[3]),
                    'Close': float(kline[4]),
                    'Volume': float(kline[5])
                })
            
            df = pd.DataFrame(df_data)
            df.set_index('Date', inplace=True)
            
            tiempos_reg = list(range(len(df)))
            resistencia_values = []
            soporte_values = []
            
            for i, t in enumerate(tiempos_reg):
                resist = info_canal['pendiente_resistencia'] * t + \
                        (info_canal['resistencia'] - info_canal['pendiente_resistencia'] * tiempos_reg[-1])
                sop = info_canal['pendiente_soporte'] * t + \
                     (info_canal['soporte'] - info_canal['pendiente_soporte'] * tiempos_reg[-1])
                resistencia_values.append(resist)
                soporte_values.append(sop)
            
            df['Resistencia'] = resistencia_values
            df['Soporte'] = soporte_values
            
            period = 14
            k_period = 3
            d_period = 3
            
            stoch_k_values = []
            for i in range(len(df)):
                if i < period - 1:
                    stoch_k_values.append(50)
                else:
                    highest_high = df['High'].iloc[i-period+1:i+1].max()
                    lowest_low = df['Low'].iloc[i-period+1:i+1].min()
                    
                    if highest_high == lowest_low:
                        k = 50
                    else:
                        k = 100 * (df['Close'].iloc[i] - lowest_low) / (highest_high - lowest_low)
                    stoch_k_values.append(k)
            
            k_smoothed = []
            for i in range(len(stoch_k_values)):
                if i < k_period - 1:
                    k_smoothed.append(stoch_k_values[i])
                else:
                    k_avg = sum(stoch_k_values[i-k_period+1:i+1]) / k_period
                    k_smoothed.append(k_avg)
            
            stoch_d_values = []
            for i in range(len(k_smoothed)):
                if i < d_period - 1:
                    stoch_d_values.append(k_smoothed[i])
                else:
                    d = sum(k_smoothed[i-d_period+1:i+1]) / d_period
                    stoch_d_values.append(d)
            
            df['Stoch_K'] = k_smoothed
            df['Stoch_D'] = stoch_d_values
            
            apds = [
                mpf.make_addplot(df['Resistencia'], color='#5444ff', linestyle='--', width=2, panel=0),
                mpf.make_addplot(df['Soporte'], color="#5444ff", linestyle='--', width=2, panel=0),
            ]
            
            if precio_entrada and tp and sl:
                entry_line = [precio_entrada] * len(df)
                tp_line = [tp] * len(df)
                sl_line = [sl] * len(df)
                
                apds.append(mpf.make_addplot(entry_line, color='#FFD700', linestyle='-', width=2, panel=0))
                apds.append(mpf.make_addplot(tp_line, color='#00FF00', linestyle='-', width=2, panel=0))
                apds.append(mpf.make_addplot(sl_line, color='#FF0000', linestyle='-', width=2, panel=0))
            
            apds.append(mpf.make_addplot(df['Stoch_K'], color='#00BFFF', width=1.5, panel=1, ylabel='Stochastic'))
            apds.append(mpf.make_addplot(df['Stoch_D'], color='#FF6347', width=1.5, panel=1))
            
            overbought = [80] * len(df)
            oversold = [20] * len(df)
            
            apds.append(mpf.make_addplot(overbought, color="#E7E4E4", linestyle='--', width=0.8, panel=1, alpha=0.5))
            apds.append(mpf.make_addplot(oversold, color="#E9E4E4", linestyle='--', width=0.8, panel=1, alpha=0.5))
            
            fig, axes = mpf.plot(df, type='candle', style='nightclouds',
                               title=f'{simbolo} | {tipo_operacion} {self.leverage}x | {config_optima["timeframe"]} | Bitget',
                               ylabel='Precio',
                               addplot=apds,
                               volume=False,
                               returnfig=True,
                               figsize=(14, 10),
                               panel_ratios=(3, 1))
            
            axes[2].set_ylim([0, 100])
            axes[2].grid(True, alpha=0.3)
            
            buf = BytesIO()
            plt.savefig(buf, format='png', dpi=100, bbox_inches='tight', facecolor='#1a1a1a')
            buf.seek(0)
            plt.close(fig)
            
            return buf
            
        except Exception as e:
            logger.warning(f"Error generando gráfico: {e}")
            return None

    def enviar_grafico_telegram(self, buf, token, chat_ids):
        if not buf or not token or not chat_ids:
            return False
        
        buf.seek(0)
        exito = False
        
        for chat_id in chat_ids:
            url = f"https://api.telegram.org/bot{token}/sendPhoto"
            try:
                buf.seek(0)
                files = {'photo': ('grafico.png', buf.read(), 'image/png')}
                data = {'chat_id': chat_id}
                r = requests.post(url, files=files, data=data, timeout=120)
                if r.status_code == 200:
                    exito = True
            except Exception as e:
                logger.error(f"Error enviando gráfico: {e}")
        
        return exito

    def _enviar_telegram_simple(self, mensaje, token, chat_ids):
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

    def reoptimizar_periodicamente(self):
        try:
            horas_desde_opt = (datetime.now() - self.ultima_optimizacion).total_seconds() / 7200
            if self.operaciones_desde_optimizacion >= 8 or horas_desde_opt >= self.config.get('reevaluacion_horas', 24):
                logger.info("Iniciando re-optimización automática...")
                ia = OptimizadorIA(log_path=self.log_path, min_samples=self.config.get('min_samples_optimizacion', 30))
                nuevos_parametros = ia.buscar_mejores_parametros()
                
                if nuevos_parametros:
                    self.actualizar_parametros(nuevos_parametros)
                    self.ultima_optimizacion = datetime.now()
                    self.operaciones_desde_optimizacion = 0
                    logger.info("Parámetros actualizados en tiempo real")
        except Exception as e:
            logger.warning(f"Error en re-optimización automática: {e}")

    def actualizar_parametros(self, nuevos_parametros):
        self.config['trend_threshold_degrees'] = nuevos_parametros.get('trend_threshold_degrees', 
                                                                        self.config.get('trend_threshold_degrees', 16))
        self.config['min_trend_strength_degrees'] = nuevos_parametros.get('min_trend_strength_degrees', 
                                                                           self.config.get('min_trend_strength_degrees', 16))
        self.config['entry_margin'] = nuevos_parametros.get('entry_margin', 
                                                             self.config.get('entry_margin', 0.001))

    def ejecutar_analisis(self):
        if random.random() < 0.1:
            self.reoptimizar_periodicamente()
        
        cierres = self.verificar_cierre_operaciones()
        if cierres:
            logger.info(f"Operaciones cerradas: {', '.join(cierres)}")
        
        self.guardar_estado()
        return self.escanear_mercado()

    def mostrar_resumen_operaciones(self):
        logger.info(f"RESUMEN OPERACIONES:")
        logger.info(f"Activas: {len(self.operaciones_activas)}")
        logger.info(f"Esperando reentry: {len(self.esperando_reentry)}")
        logger.info(f"Total ejecutadas: {self.total_operaciones}")
        logger.info(f"Credenciales válidas: {'✓ SÍ' if self.credenciales_validas else '✗ NO (MODO DEMO)'}")
        
        if self.operaciones_activas:
            for simbolo, op in self.operaciones_activas.items():
                estado = "🟢 LONG" if op['tipo'] == 'LONG' else "🔴 SHORT"
                ancho_canal = op.get('ancho_canal_porcentual', 0)
                timeframe = op.get('timeframe_utilizado', 'N/A')
                velas = op.get('velas_utilizadas', 0)
                breakout = "🚀" if op.get('breakout_usado', False) else ""
                demo = "⚠️ DEMO" if op.get('demo', False) else "🚀 REAL"
                logger.info(f"{simbolo} {estado} {breakout} {demo} - {timeframe} - {velas}v - Ancho: {ancho_canal:.1f}%")

# ---------------------------
# FLASK APP Y RENDER
# ---------------------------
app = Flask(__name__)

def crear_config_desde_entorno():
    directorio_actual = os.path.dirname(os.path.abspath(__file__))
    telegram_chat_ids_str = os.environ.get('TELEGRAM_CHAT_ID', '')
    telegram_chat_ids = [cid.strip() for cid in telegram_chat_ids_str.split(',') if cid.strip()]
    
    # Obtener credenciales de Bitget
    bitget_api_key = os.environ.get('BITGET_API_KEY', '')
    bitget_api_secret = os.environ.get('BITGET_SECRET_KEY', '')
    bitget_passphrase = os.environ.get('BITGET_PASSPHRASE', '')
    
    # Log para depuración
    if bitget_api_key:
        logger.info(f"Bitget API Key encontrado: {bitget_api_key[:10]}...")
    else:
        logger.warning("BITGET_API_KEY no configurado en variables de entorno")
    
    if bitget_api_secret:
        logger.info(f"Bitget Secret encontrado: {bitget_api_secret[:10]}...")
    else:
        logger.warning("BITGET_SECRET_KEY no configurado en variables de entorno")
    
    if bitget_passphrase:
        logger.info(f"Bitget Passphrase encontrado: {bitget_passphrase[:5]}...")
    else:
        logger.warning("BITGET_PASSPHRASE no configurado en variables de entorno")
    
    return {
        'bitget_api_key': bitget_api_key,
        'bitget_api_secret': bitget_api_secret,
        'bitget_passphrase': bitget_passphrase,
        'leverage': 10,
        'capital_por_operacion': 2.0,
        'min_channel_width_percent': 4.0,
        'trend_threshold_degrees': 16.0,
        'min_trend_strength_degrees': 16.0,
        'entry_margin': 0.001,
        'min_rr_ratio': 1.2,
        'scan_interval_minutes': 7,
        'timeframes': ['5m', '15m', '30m', '1h'],
        'velas_options': [80, 100, 120, 150, 200],
        'symbols': [
            'XMRUSDT','AAVEUSDT','DOTUSDT','LINKUSDT','BNBUSDT','XRPUSDT','SOLUSDT','AVAXUSDT',
            'DOGEUSDT','LTCUSDT','ATOMUSDT','XLMUSDT','ALGOUSDT','VETUSDT','ICPUSDT','FILUSDT',
            'BCHUSDT','NEOUSDT','TRXUSDT','XTZUSDT','SUSHIUSDT','COMPUSDT','PEPEUSDT','ETCUSDT',
            'SNXUSDT','RENDERUSDT','1INCHUSDT','UNIUSDT','ZILUSDT','HOTUSDT','ENJUSDT','HYPEUSDT',
            'BEATUSDT','PIPPINUSDT','ADAUSDT','ASTERUSDT','ENAUSDT','TAOUSDT','HEMIUSDT','LUNCUSDT',
            'WLDUSDT','WIFUSDT','APTUSDT','HBARUSDT','CRVUSDT','LUNAUSDT','TIAUSDT','ARBUSDT','ONDOUSDT',
            '1000BONKUSDT','FOLKSUSDT','BRETTUSDT','TRUMPUSDT','INJUSDT','ZECUSDT','NOTUSDT','SHIBUSDT',
            'LDOUSDT','KASUSDT','STRKUSDT','DYDXUSDT','SEIUSDT','TONUSDT','NMRUSDT'
        ],
        'telegram_token': os.environ.get('TELEGRAM_TOKEN', ''),
        'telegram_chat_ids': telegram_chat_ids,
        'auto_optimize': True,
        'min_samples_optimizacion': 30,
        'reevaluacion_horas': 24,
        'log_path': os.path.join(directorio_actual, 'operaciones_bitget.csv'),
        'estado_file': os.path.join(directorio_actual, 'estado_bot_bitget.json')
    }

# Crear bot con configuración desde entorno
config = crear_config_desde_entorno()
bot = TradingBot(config)

def run_bot_loop():
    """Ejecuta el bot en un hilo separado"""
    while True:
        try:
            bot.ejecutar_analisis()
            time.sleep(bot.config.get('scan_interval_minutes', 3) * 60)
        except Exception as e:
            logger.error(f"Error en el hilo del bot: {e}")
            time.sleep(60)

# Iniciar hilo del bot (funciona en modo real o demo)
bot_thread = threading.Thread(target=run_bot_loop, daemon=True)
bot_thread.start()

if bot.credenciales_validas:
    logger.info("✅ Bot iniciado con credenciales Bitget válidas - MODO REAL")
else:
    logger.info("⚠️ Bot iniciado en MODO DEMO (análisis + señales Telegram)")

@app.route('/')
def index():
    status = "ACTIVO (Modo REAL)" if bot.credenciales_validas else "ACTIVO (Modo DEMO)"
    return f"Bot Breakout + Reentry para Bitget está {status}.", 200

@app.route('/webhook', methods=['POST'])
def telegram_webhook():
    if request.is_json:
        update = request.get_json()
        logger.info(f"Update recibido: {json.dumps(update)}")
        return jsonify({"status": "ok"}), 200
    return jsonify({"error": "Request must be JSON"}), 400

@app.route('/status', methods=['GET'])
def get_status():
    """Endpoint para verificar estado del bot"""
    status_info = {
        'credenciales_validas': bot.credenciales_validas,
        'operaciones_activas': len(bot.operaciones_activas),
        'esperando_reentry': len(bot.esperando_reentry),
        'total_operaciones': bot.total_operaciones,
        'ultima_optimizacion': bot.ultima_optimizacion.isoformat(),
        'modo': 'REAL' if bot.credenciales_validas else 'DEMO',
        'timestamp': datetime.now().isoformat()
    }
    return jsonify(status_info), 200

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
        logger.info(f"Webhook configurado en: {webhook_url}")
    except Exception as e:
        logger.error(f"Error configurando webhook: {e}")

if __name__ == '__main__':
    # Bind al puerto que Render espera
    port = int(os.environ.get('PORT', 10000))
    setup_telegram_webhook()
    app.run(host='0.0.0.0', port=port, debug=False)
