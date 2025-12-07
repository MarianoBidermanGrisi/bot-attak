# bot_web_service.py
# Adaptación para Render del bot Breakout + Reentry
# MODIFICADO PARA BITGET FUTUROS - SIN GRÁFICOS
# CORREGIDO PARA RENDER PUERTO 10000

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
import hmac
import hashlib
import base64
from flask import Flask, request, jsonify
import threading
import logging

# Configurar logging para Render
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# ---------------------------
# CONFIGURACIÓN BITGET
# ---------------------------
BITGET_API_KEY = os.environ.get('BITGET_API_KEY')
BITGET_SECRET_KEY = os.environ.get('BITGET_SECRET_KEY')
BITGET_PASSPHRASE = os.environ.get('BITGET_PASSPHRASE')
BITGET_REST_URL = "https://api.bitget.com"

# Verificar credenciales Bitget
if not BITGET_API_KEY or not BITGET_SECRET_KEY or not BITGET_PASSPHRASE:
    logger.warning("⚠️ Credenciales de Bitget no configuradas. El bot funcionará en modo demo.")
    BITGET_API_KEY = BITGET_API_KEY or "demo"
    BITGET_SECRET_KEY = BITGET_SECRET_KEY or "demo"
    BITGET_PASSPHRASE = BITGET_PASSPHRASE or "demo"

# Símbolos para futuros de Bitget (Mix Contracts)
BITGET_SYMBOLS = [
    'BTCUSDT_UMCBL',
    'ETHUSDT_UMCBL',
    'LINKUSDT_UMCBL',
    'DOTUSDT_UMCBL',
    'BNBUSDT_UMCBL',
    'XRPUSDT_UMCBL',
    'SOLUSDT_UMCBL',
    'AVAXUSDT_UMCBL',
    'DOGEUSDT_UMCBL',
    'LTCUSDT_UMCBL'
]

# ---------------------------
# Optimizador IA
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
            logger.info("⚠ No se encontró operaciones_log.csv (optimizador)")
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
            logger.info(f"ℹ️ No hay suficientes datos para optimizar (se requieren {self.min_samples}, hay {len(self.datos)})")
            return None
        mejor_score = -1e9
        mejores_param = None
        trend_values = [3, 5, 8, 10, 12, 15, 18, 20, 25, 30, 35, 40]
        strength_values = [3, 5, 8, 10, 12, 15, 18, 20, 25, 30]
        margin_values = [0.0005, 0.001, 0.0015, 0.002, 0.0025, 0.003, 0.004, 0.005, 0.008, 0.01]
        combos = list(itertools.product(trend_values, strength_values, margin_values))
        total = len(combos)
        logger.info(f"🔎 Optimizador: probando {total} combinaciones...")
        for idx, (t, s, m) in enumerate(combos, start=1):
            score = self.evaluar_configuracion(t, s, m)
            if idx % 100 == 0 or idx == total:
                logger.info(f"   · probado {idx}/{total} combos (mejor score actual: {mejor_score:.4f})")
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
            logger.info("✅ Optimizador: mejores parámetros encontrados: %s", mejores_param)
            try:
                with open("mejores_parametros.json", "w", encoding='utf-8') as f:
                    json.dump(mejores_param, f, indent=2)
            except Exception as e:
                logger.error("⚠ Error guardando mejores_parametros.json: %s", e)
        else:
            logger.info("⚠ No se encontró una configuración mejor")
        return mejores_param

# ---------------------------
# CLASE BITGET API HELPER
# ---------------------------
class BitgetAPI:
    def __init__(self, api_key, secret_key, passphrase):
        self.api_key = api_key
        self.secret_key = secret_key
        self.passphrase = passphrase
        self.base_url = BITGET_REST_URL
        self._sync_time_offset()

    def _sync_time_offset(self):
        """Sincroniza el offset de tiempo con Bitget"""
        try:
            resp = requests.get(f"{self.base_url}/api/v2/public/time", timeout=5)
            server_time = resp.json()['data']['timestamp']
            local_time = int(time.time() * 1000)
            self.time_offset = server_time - local_time
            logger.info(f"✅ Offset de tiempo sincronizado: {self.time_offset} ms")
        except Exception as e:
            logger.warning(f"⚠ Error sincronizando tiempo: {e}")
            self.time_offset = 0

    def _get_timestamp(self):
        """Retorna timestamp en milisegundos ajustado"""
        return int(time.time() * 1000) + self.time_offset

    def _sign(self, message):
        """Firma HMAC-SHA256 + Base64"""
        if self.secret_key == "demo":
            return "demo_signature"
        mac = hmac.new(
            bytes(self.secret_key, 'utf-8'),
            bytes(message, 'utf-8'),
            hashlib.sha256
        )
        return base64.b64encode(mac.digest()).decode()

    def _request(self, method, endpoint, params=None, data=None):
        """Realiza petición firmada a la API de Bitget"""
        if self.api_key == "demo":
            logger.info("📡 Modo demo: Simulando petición a Bitget")
            return {'code': '00000', 'data': [], 'msg': 'Demo mode'}
            
        timestamp = str(self._get_timestamp())
        
        if method == "GET" and params:
            query_string = '&'.join([f'{k}={v}' for k, v in sorted(params.items())])
            message = timestamp + method + endpoint + '?' + query_string
            signature = self._sign(message)
            headers = {
                'ACCESS-KEY': self.api_key,
                'ACCESS-SIGN': signature,
                'ACCESS-TIMESTAMP': timestamp,
                'ACCESS-PASSPHRASE': self.passphrase,
                'Content-Type': 'application/json'
            }
            url = self.base_url + endpoint + '?' + query_string
            response = requests.get(url, headers=headers, timeout=10)
        else:
            body = json.dumps(data) if data else ''
            message = timestamp + method + endpoint + body
            signature = self._sign(message)
            headers = {
                'ACCESS-KEY': self.api_key,
                'ACCESS-SIGN': signature,
                'ACCESS-TIMESTAMP': timestamp,
                'ACCESS-PASSPHRASE': self.passphrase,
                'Content-Type': 'application/json'
            }
            url = self.base_url + endpoint
            response = requests.post(url, headers=headers, json=data, timeout=10)
        
        return response.json()

    def get_contract_info(self, symbol):
        """Obtiene información del contrato (lotSz, priceTick, etc.)"""
        endpoint = "/api/v2/mix/market/contracts"
        params = {"symbol": symbol}
        return self._request("GET", endpoint, params=params)

    def get_klines(self, symbol, granularity="1m", limit=200):
        """Obtiene velas históricas (para inicialización)"""
        endpoint = "/api/v2/mix/market/candles"
        params = {
            "symbol": symbol,
            "granularity": granularity,
            "limit": limit
        }
        result = self._request("GET", endpoint, params=params)
        
        # En modo demo, generar datos simulados
        if self.api_key == "demo" and result.get('code') == '00000':
            import random
            base_price = 100.0
            data = []
            for i in range(limit):
                open_price = base_price + random.uniform(-5, 5)
                close_price = open_price + random.uniform(-2, 2)
                high_price = max(open_price, close_price) + random.uniform(0, 3)
                low_price = min(open_price, close_price) - random.uniform(0, 3)
                data.append([
                    str(int(time.time() * 1000) - (limit - i) * 60000),
                    str(open_price),
                    str(high_price),
                    str(low_price),
                    str(close_price),
                    str(random.uniform(1000, 10000))
                ])
            result['data'] = data
            
        return result

# ---------------------------
# BOT PRINCIPAL - BREAKOUT + REENTRY (BITGET)
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
        # Tracking de breakouts y reingresos
        self.breakouts_detectados = {}
        self.esperando_reentry = {}
        self.estado_file = config.get('estado_file', 'estado_bot.json')
        
        logger.info("🤖 Inicializando Bot Trading...")
        
        # Inicializar API Bitget
        try:
            self.bitget = BitgetAPI(
                api_key=BITGET_API_KEY,
                secret_key=BITGET_SECRET_KEY,
                passphrase=BITGET_PASSPHRASE
            )
            logger.info("✅ API Bitget inicializada")
        except Exception as e:
            logger.error(f"❌ Error inicializando API Bitget: {e}")
            raise
        
        self.cargar_estado()
        
        # Optimización automática
        if self.auto_optimize:
            try:
                ia = OptimizadorIA(log_path=self.log_path, min_samples=config.get('min_samples_optimizacion', 15))
                parametros_optimizados = ia.buscar_mejores_parametros()
                if parametros_optimizados:
                    self.config['trend_threshold_degrees'] = parametros_optimizados.get('trend_threshold_degrees', 
                                                                                   self.config.get('trend_threshold_degrees', 13))
                    self.config['min_trend_strength_degrees'] = parametros_optimizados.get('min_trend_strength_degrees', 
                                                                                       self.config.get('min_trend_strength_degrees', 16))
                    self.config['entry_margin'] = parametros_optimizados.get('entry_margin', 
                                                                         self.config.get('entry_margin', 0.001))
                    logger.info("✅ Parámetros optimizados aplicados")
            except Exception as e:
                logger.warning(f"⚠ Error en optimización automática: {e}")
        
        self.ultimos_datos = {}
        self.operaciones_activas = {}
        self.senales_enviadas = set()
        self.archivo_log = self.log_path
        self.inicializar_log()
        
        # Cache de información de contratos
        self.contract_cache = {}
        
        logger.info("✅ Bot Trading inicializado correctamente")

    def cargar_estado(self):
        """Carga el estado previo del bot incluyendo breakouts"""
        try:
            if os.path.exists(self.estado_file):
                with open(self.estado_file, 'r', encoding='utf-8') as f:
                    estado = json.load(f)
                
                # Convertir strings de fecha a datetime
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
                
                # Asignar valores
                self.ultima_optimizacion = estado.get('ultima_optimizacion', datetime.now())
                self.operaciones_desde_optimizacion = estado.get('operaciones_desde_optimizacion', 0)
                self.total_operaciones = estado.get('total_operaciones', 0)
                self.breakout_history = estado.get('breakout_history', {})
                self.config_optima_por_simbolo = estado.get('config_optima_por_simbolo', {})
                self.ultima_busqueda_config = estado.get('ultima_busqueda_config', {})
                self.operaciones_activas = estado.get('operaciones_activas', {})
                self.senales_enviadas = set(estado.get('senales_enviadas', []))
                
                logger.info("✅ Estado anterior cargado correctamente")
                logger.info(f"   📊 Operaciones activas: {len(self.operaciones_activas)}")
                logger.info(f"   ⏳ Esperando reentry: {len(self.esperando_reentry)}")
                
        except Exception as e:
            logger.error(f"⚠ Error cargando estado previo: {e}")
            logger.info("   Se iniciará con estado limpio")

    def guardar_estado(self):
        """Guarda el estado actual del bot incluyendo breakouts"""
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
                'timestamp_guardado': datetime.now().isoformat()
            }
            
            with open(self.estado_file, 'w', encoding='utf-8') as f:
                json.dump(estado, f, indent=2, ensure_ascii=False)
                
            logger.info("💾 Estado guardado correctamente")
            
        except Exception as e:
            logger.error(f"⚠ Error guardando estado: {e}")

    def obtener_datos_mercado_config(self, simbolo, timeframe, num_velas):
        """Obtiene datos con configuración específica desde Bitget"""
        # Mapear timeframe de Binance a Bitget
        timeframe_map = {
            '1m': '1m', '3m': '3m', '5m': '5m', 
            '15m': '15m', '30m': '30m', '1h': '1H', '4h': '4H'
        }
        bitget_timeframe = timeframe_map.get(timeframe, '1m')
        
        try:
            result = self.bitget.get_klines(simbolo, bitget_timeframe, num_velas + 14)
            
            if result.get('code') != '00000' or not result.get('data'):
                logger.warning(f"⚠ No se pudieron obtener datos para {simbolo}")
                return None
            
            datos = result['data']
            
            # Extraer datos de las velas
            maximos = []
            minimos = []
            cierres = []
            
            for vela in datos:
                if len(vela) >= 5:
                    try:
                        maximos.append(float(vela[2]))  # High
                        minimos.append(float(vela[3]))  # Low
                        cierres.append(float(vela[4]))  # Close
                    except (ValueError, IndexError):
                        continue
            
            if not cierres:
                return None
            
            tiempos = list(range(len(cierres)))
            
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
            logger.error(f"Error obteniendo datos para {simbolo}: {e}")
            return None

    def calcular_canal_regresion_config(self, datos_mercado, candle_period):
        """Calcula canal de regresión"""
        if not datos_mercado or len(datos_mercado['maximos']) < candle_period:
            return None
        
        try:
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
            
            # Calcular desviaciones
            diferencias_max = [maximos[i] - (pendiente_max * tiempos_reg[i] + intercepto_max) for i in range(len(tiempos_reg))]
            diferencias_min = [minimos[i] - (pendiente_min * tiempos_reg[i] + intercepto_min) for i in range(len(tiempos_reg))]
            
            desviacion_max = np.std(diferencias_max) if diferencias_max else 0
            desviacion_min = np.std(diferencias_min) if diferencias_min else 0
            
            resistencia_superior = resistencia_media + desviacion_max
            soporte_inferior = soporte_media - desviacion_min
            
            precio_actual = datos_mercado['precio_actual']
            
            # Calcular indicadores
            pearson, angulo_tendencia = self.calcular_pearson_y_angulo(tiempos_reg, cierres)
            fuerza_texto, nivel_fuerza = self.clasificar_fuerza_tendencia(angulo_tendencia)
            direccion = self.determinar_direccion_tendencia(angulo_tendencia, 1)
            stoch_k, stoch_d = self.calcular_stochastic(datos_mercado)
            
            precio_medio = (resistencia_superior + soporte_inferior) / 2
            ancho_canal_absoluto = resistencia_superior - soporte_inferior
            ancho_canal_porcentual = (ancho_canal_absoluto / precio_medio) * 100 if precio_medio > 0 else 0
            
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
            
        except Exception as e:
            logger.error(f"Error calculando canal de regresión: {e}")
            return None

    def buscar_configuracion_optima_simbolo(self, simbolo):
        """Busca la mejor combinación de velas/timeframe usando Bitget"""
        try:
            # Verificar si ya tenemos configuración óptima reciente
            if simbolo in self.config_optima_por_simbolo:
                config_optima = self.config_optima_por_simbolo[simbolo]
                ultima_busqueda = self.ultima_busqueda_config.get(simbolo)
                
                if ultima_busqueda and (datetime.now() - ultima_busqueda).total_seconds() < 7200:
                    return config_optima
                else:
                    logger.info(f"   🔄 Reevaluando configuración para {simbolo} (pasó 2 horas)")
            
            logger.info(f"   🔍 Buscando configuración óptima para {simbolo}...")
            
            timeframes = self.config.get('timeframes', ['5m', '15m', '30m'])
            velas_options = self.config.get('velas_options', [80, 100, 120])
            
            mejor_config = None
            mejor_puntaje = -999999
            
            for timeframe in timeframes:
                for num_velas in velas_options:
                    try:
                        # Obtener datos
                        datos = self.obtener_datos_mercado_config(simbolo, timeframe, num_velas)
                        if not datos:
                            continue
                        
                        # Calcular canal
                        canal_info = self.calcular_canal_regresion_config(datos, num_velas)
                        if not canal_info:
                            continue
                        
                        # Evaluar calidad del canal
                        if (canal_info['nivel_fuerza'] >= 2 and 
                            abs(canal_info['coeficiente_pearson']) >= 0.4 and 
                            canal_info['r2_score'] >= 0.4):
                            
                            ancho_actual = canal_info['ancho_canal_porcentual']
                            
                            if ancho_actual >= self.config.get('min_channel_width_percent', 4.0):
                                # Priorizar timeframes más cortos
                                prioridad = {'5m': 3, '15m': 2, '30m': 1}.get(timeframe, 1)
                                puntaje = ancho_actual * prioridad * 100
                                
                                if puntaje > mejor_puntaje:
                                    mejor_puntaje = puntaje
                                    mejor_config = {
                                        'timeframe': timeframe,
                                        'num_velas': num_velas,
                                        'ancho_canal': ancho_actual,
                                        'puntaje_total': puntaje
                                    }
                                    
                    except Exception as e:
                        logger.debug(f"Error evaluando {timeframe}-{num_velas} para {simbolo}: {e}")
                        continue
            
            if mejor_config:
                self.config_optima_por_simbolo[simbolo] = mejor_config
                self.ultima_busqueda_config[simbolo] = datetime.now()
                logger.info(f"   ✅ Config óptima: {mejor_config['timeframe']} - {mejor_config['num_velas']} velas - Ancho: {mejor_config['ancho_canal']:.1f}%")
            else:
                # Configuración por defecto si no se encuentra óptima
                mejor_config = {
                    'timeframe': '15m',
                    'num_velas': 100,
                    'ancho_canal': 0,
                    'puntaje_total': 0
                }
                self.config_optima_por_simbolo[simbolo] = mejor_config
                logger.info(f"   ⚠ Configuración por defecto para {simbolo}")
            
            return mejor_config
            
        except Exception as e:
            logger.error(f"Error buscando configuración óptima para {simbolo}: {e}")
            return None

    def detectar_breakout(self, simbolo, info_canal, datos_mercado):
        """Detecta si el precio ha ROTO el canal"""
        if not info_canal:
            return None
            
        try:
            # Verificar condiciones mínimas
            if info_canal['ancho_canal_porcentual'] < self.config.get('min_channel_width_percent', 4.0):
                return None
            
            precio_cierre = datos_mercado['cierres'][-1]
            resistencia = info_canal['resistencia']
            soporte = info_canal['soporte']
            angulo = info_canal['angulo_tendencia']
            direccion = info_canal['direccion']
            nivel_fuerza = info_canal['nivel_fuerza']
            
            # Filtrar por fuerza de tendencia
            if abs(angulo) < self.config.get('min_trend_strength_degrees', 16):
                return None
            
            # Verificar si ya hubo un breakout reciente
            if simbolo in self.breakouts_detectados:
                ultimo_breakout = self.breakouts_detectados[simbolo]
                tiempo_desde_ultimo = (datetime.now() - ultimo_breakout['timestamp']).total_seconds() / 60
                if tiempo_desde_ultimo < 115:
                    logger.debug(f"     ⏰ {simbolo} - Breakout detectado recientemente ({tiempo_desde_ultimo:.1f} min), omitiendo...")
                    return None
            
            # Detectar breakout según dirección
            if direccion == "🟢 ALCISTA" and nivel_fuerza >= 2:
                if precio_cierre > resistencia * 1.001:  # Ruptura alcista
                    logger.info(f"     🚀 {simbolo} - BREAKOUT ALCISTA: {precio_cierre:.8f} > Resistencia: {resistencia:.8f}")
                    return "BREAKOUT_LONG"
                    
            elif direccion == "🔴 BAJISTA" and nivel_fuerza >= 2:
                if precio_cierre < soporte * 0.999:  # Ruptura bajista
                    logger.info(f"     📉 {simbolo} - BREAKOUT BAJISTA: {precio_cierre:.8f} < Soporte: {soporte:.8f}")
                    return "BREAKOUT_SHORT"
            
            return None
            
        except Exception as e:
            logger.error(f"Error detectando breakout para {simbolo}: {e}")
            return None

    def detectar_reentry(self, simbolo, info_canal, datos_mercado):
        """Detecta si el precio ha REINGRESADO al canal"""
        if simbolo not in self.esperando_reentry:
            return None
            
        try:
            breakout_info = self.esperando_reentry[simbolo]
            tipo_breakout = breakout_info['tipo']
            timestamp_breakout = breakout_info['timestamp']
            
            # Verificar timeout (30 minutos máximo)
            tiempo_desde_breakout = (datetime.now() - timestamp_breakout).total_seconds() / 60
            if tiempo_desde_breakout > 30:
                logger.info(f"     ⏰ {simbolo} - Timeout de reentry (>30 min), cancelando espera")
                del self.esperando_reentry[simbolo]
                if simbolo in self.breakouts_detectados:
                    del self.breakouts_detectados[simbolo]
                return None
            
            precio_actual = datos_mercado['precio_actual']
            resistencia = info_canal['resistencia']
            soporte = info_canal['soporte']
            stoch_k = info_canal['stoch_k']
            stoch_d = info_canal['stoch_d']
            
            # Tolerancia para reingreso
            tolerancia = 0.002 * precio_actual
            
            if tipo_breakout == "BREAKOUT_LONG":
                # Reingreso para LONG: precio vuelve al canal y Stoch en oversold
                if soporte <= precio_actual <= resistencia:
                    distancia_soporte = abs(precio_actual - soporte)
                    if distancia_soporte <= tolerancia and stoch_k <= 30 and stoch_d <= 30:
                        logger.info(f"     ✅ {simbolo} - REENTRY LONG confirmado! Entrada en soporte con Stoch oversold")
                        if simbolo in self.breakouts_detectados:
                            del self.breakouts_detectados[simbolo]
                        return "LONG"
                        
            elif tipo_breakout == "BREAKOUT_SHORT":
                # Reingreso para SHORT: precio vuelve al canal y Stoch en overbought
                if soporte <= precio_actual <= resistencia:
                    distancia_resistencia = abs(precio_actual - resistencia)
                    if distancia_resistencia <= tolerancia and stoch_k >= 70 and stoch_d >= 70:
                        logger.info(f"     ✅ {simbolo} - REENTRY SHORT confirmado! Entrada en resistencia con Stoch overbought")
                        if simbolo in self.breakouts_detectados:
                            del self.breakouts_detectados[simbolo]
                        return "SHORT"
            
            return None
            
        except Exception as e:
            logger.error(f"Error detectando reentry para {simbolo}: {e}")
            return None

    def calcular_niveles_entrada(self, tipo_operacion, info_canal, precio_actual):
        """Calcula niveles de entrada, TP y SL"""
        if not info_canal:
            return None, None, None
            
        try:
            resistencia = info_canal['resistencia']
            soporte = info_canal['soporte']
            ancho_canal = resistencia - soporte
            
            if tipo_operacion == "LONG":
                precio_entrada = precio_actual
                stop_loss = soporte * 0.995  # 0.5% por debajo del soporte
                take_profit = precio_entrada + ancho_canal * 0.5
                
            else:  # SHORT
                precio_entrada = precio_actual
                stop_loss = resistencia * 1.005  # 0.5% por encima de resistencia
                take_profit = precio_entrada - ancho_canal * 0.5
            
            # Asegurar ratio riesgo/beneficio mínimo
            riesgo = abs(precio_entrada - stop_loss)
            beneficio = abs(take_profit - precio_entrada)
            ratio_rr = beneficio / riesgo if riesgo > 0 else 0
            
            if ratio_rr < self.config.get('min_rr_ratio', 1.2):
                if tipo_operacion == "LONG":
                    take_profit = precio_entrada + (riesgo * self.config['min_rr_ratio'])
                else:
                    take_profit = precio_entrada - (riesgo * self.config['min_rr_ratio'])
            
            return precio_entrada, take_profit, stop_loss
            
        except Exception as e:
            logger.error(f"Error calculando niveles de entrada: {e}")
            return None, None, None

    def _enviar_telegram_simple(self, mensaje, token, chat_ids):
        """Envía mensaje simple a Telegram"""
        if not token or not chat_ids:
            return False
            
        resultados = []
        for chat_id in chat_ids:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            payload = {'chat_id': chat_id, 'text': mensaje, 'parse_mode': 'HTML'}
            try:
                response = requests.post(url, json=payload, timeout=10)
                resultados.append(response.status_code == 200)
            except Exception as e:
                logger.error(f"Error enviando a Telegram: {e}")
                resultados.append(False)
                
        return any(resultados)

    def enviar_alerta_breakout(self, simbolo, tipo_breakout, info_canal, datos_mercado, config_optima):
        """Envía alerta de BREAKOUT detectado a Telegram"""
        try:
            if tipo_breakout == "BREAKOUT_LONG":
                emoji = "🚀"
                tipo_texto = "RUPTURA ALCISTA"
                direccion = "⬆️"
            else:
                emoji = "📉"
                tipo_texto = "RUPTURA BAJISTA"
                direccion = "⬇️"
            
            mensaje = f"""
{emoji} <b>¡BREAKOUT DETECTADO! - {simbolo}</b>
⚠️ <b>{tipo_texto}</b> {direccion}
⏰ <b>Hora:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
💰 <b>Precio:</b> {datos_mercado['precio_actual']:.8f}
📊 <b>Config:</b> {config_optima['timeframe']} - {config_optima['num_velas']} velas
📏 <b>Canal:</b> {info_canal['soporte']:.8f} - {info_canal['resistencia']:.8f}
⏳ <b>ESPERANDO REINGRESO...</b>
👁️ Máximo 30 minutos para confirmación
            """
            
            token = self.config.get('telegram_token')
            chat_ids = self.config.get('telegram_chat_ids', [])
            
            if token and chat_ids:
                logger.info(f"     📨 Enviando alerta de breakout para {simbolo}")
                self._enviar_telegram_simple(mensaje, token, chat_ids)
            else:
                logger.info(f"     📢 Breakout detectado en {simbolo} (sin Telegram)")
                
        except Exception as e:
            logger.error(f"Error enviando alerta de breakout: {e}")

    def escanear_mercado(self):
        """Escanea el mercado con estrategia Breakout + Reentry"""
        logger.info(f"🔍 Escaneando {len(self.config.get('symbols', []))} símbolos...")
        senales_encontradas = 0
        
        for simbolo in self.config.get('symbols', []):
            try:
                # Omitir si ya hay operación activa
                if simbolo in self.operaciones_activas:
                    logger.debug(f"   ⚡ {simbolo} - Operación activa, omitiendo...")
                    continue
                
                # Buscar configuración óptima
                config_optima = self.buscar_configuracion_optima_simbolo(simbolo)
                if not config_optima:
                    logger.debug(f"   ❌ {simbolo} - No se encontró configuración válida")
                    continue
                
                # Obtener datos
                datos_mercado = self.obtener_datos_mercado_config(
                    simbolo, config_optima['timeframe'], config_optima['num_velas']
                )
                if not datos_mercado:
                    logger.debug(f"   ❌ {simbolo} - Error obteniendo datos")
                    continue
                
                # Calcular canal
                info_canal = self.calcular_canal_regresion_config(datos_mercado, config_optima['num_velas'])
                if not info_canal:
                    logger.debug(f"   ❌ {simbolo} - Error calculando canal")
                    continue
                
                # Mostrar información del símbolo
                estado_stoch = "📉 OVERSOLD" if info_canal['stoch_k'] <= 30 else "📈 OVERBOUGHT" if info_canal['stoch_k'] >= 70 else "➖ NEUTRO"
                precio_actual = datos_mercado['precio_actual']
                
                logger.info(
                    f"📊 {simbolo} - {config_optima['timeframe']} | "
                    f"{info_canal['direccion']} ({info_canal['angulo_tendencia']:.1f}°) | "
                    f"Ancho: {info_canal['ancho_canal_porcentual']:.1f}% | "
                    f"Stoch: {info_canal['stoch_k']:.1f} {estado_stoch}"
                )
                
                # Verificar condiciones mínimas
                if (info_canal['nivel_fuerza'] < 2 or 
                    abs(info_canal['coeficiente_pearson']) < 0.4 or 
                    info_canal['r2_score'] < 0.4):
                    continue
                
                # Primero verificar si estamos esperando reentry
                if simbolo not in self.esperando_reentry:
                    # Detectar breakout
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
                        
                        logger.info(f"     🎯 {simbolo} - Breakout registrado, esperando reingreso...")
                        self.enviar_alerta_breakout(simbolo, tipo_breakout, info_canal, datos_mercado, config_optima)
                        continue
                
                # Verificar reentry
                tipo_operacion = self.detectar_reentry(simbolo, info_canal, datos_mercado)
                if not tipo_operacion:
                    continue
                
                # Calcular niveles de entrada
                precio_entrada, tp, sl = self.calcular_niveles_entrada(
                    tipo_operacion, info_canal, datos_mercado['precio_actual']
                )
                
                if not precio_entrada or not tp or not sl:
                    continue
                
                # Verificar si hubo breakout reciente para este símbolo
                if simbolo in self.breakout_history:
                    ultimo_breakout = self.breakout_history[simbolo]
                    tiempo_desde_ultimo = (datetime.now() - ultimo_breakout).total_seconds() / 3600
                    if tiempo_desde_ultimo < 2:
                        logger.info(f"   ⏳ {simbolo} - Señal reciente, omitiendo...")
                        continue
                
                # Generar y enviar señal
                breakout_info = self.esperando_reentry[simbolo]
                self.generar_senal_operacion(
                    simbolo, tipo_operacion, precio_entrada, tp, sl, 
                    info_canal, datos_mercado, config_optima, breakout_info
                )
                
                senales_encontradas += 1
                self.breakout_history[simbolo] = datetime.now()
                del self.esperando_reentry[simbolo]
                
            except Exception as e:
                logger.error(f"⚠️ Error analizando {simbolo}: {e}")
                continue
        
        # Mostrar resumen de espera
        if self.esperando_reentry:
            logger.info(f"\n⏳ Esperando reingreso en {len(self.esperando_reentry)} símbolos:")
            for simbolo, info in self.esperando_reentry.items():
                tiempo_espera = (datetime.now() - info['timestamp']).total_seconds() / 60
                logger.info(f"   • {simbolo} - {info['tipo']} - Esperando {tiempo_espera:.1f} min")
        
        if self.breakouts_detectados:
            logger.info(f"\n⏰ Breakouts detectados recientemente:")
            for simbolo, info in self.breakouts_detectados.items():
                tiempo_desde_deteccion = (datetime.now() - info['timestamp']).total_seconds() / 60
                logger.info(f"   • {simbolo} - {info['tipo']} - Hace {tiempo_desde_deteccion:.1f} min")
        
        if senales_encontradas > 0:
            logger.info(f"✅ Se encontraron {senales_encontradas} señales de trading")
        else:
            logger.info("❌ No se encontraron señales en este ciclo")
            
        return senales_encontradas

    def generar_senal_operacion(self, simbolo, tipo_operacion, precio_entrada, tp, sl,
                              info_canal, datos_mercado, config_optima, breakout_info=None):
        """Genera y envía señal de operación"""
        if simbolo in self.senales_enviadas:
            return
            
        if precio_entrada is None or tp is None or sl is None:
            logger.warning(f"    ❌ Niveles inválidos para {simbolo}, omitiendo señal")
            return
        
        try:
            # Calcular métricas
            riesgo = abs(precio_entrada - sl)
            beneficio = abs(tp - precio_entrada)
            ratio_rr = beneficio / riesgo if riesgo > 0 else 0
            sl_percent = abs((sl - precio_entrada) / precio_entrada) * 100
            tp_percent = abs((tp - precio_entrada) / precio_entrada) * 100
            
            stoch_estado = "📉 SOBREVENTA" if tipo_operacion == "LONG" else "📈 SOBRECOMPRA"
            
            # Construir mensaje
            breakout_texto = ""
            if breakout_info:
                tiempo_breakout = (datetime.now() - breakout_info['timestamp']).total_seconds() / 60
                breakout_texto = f"""
🚀 <b>BREAKOUT + REENTRY CONFIRMADO:</b>
⏰ Tiempo desde breakout: {tiempo_breakout:.1f} minutos
💰 Precio breakout: {breakout_info['precio_breakout']:.8f}
"""
            
            mensaje = f"""
🎯 <b>SEÑAL DE {tipo_operacion} - {simbolo}</b>
{breakout_texto}
⏱️ <b>Configuración:</b> {config_optima['timeframe']} - {config_optima['num_velas']}v
📏 <b>Ancho Canal:</b> {info_canal['ancho_canal_porcentual']:.1f}%
💰 <b>Precio Actual:</b> {datos_mercado['precio_actual']:.8f}
🎯 <b>Entrada:</b> {precio_entrada:.8f}
🛑 <b>Stop Loss:</b> {sl:.8f} ({sl_percent:.2f}%)
🎯 <b>Take Profit:</b> {tp:.8f} ({tp_percent:.2f}%)
📊 <b>Ratio R/B:</b> {ratio_rr:.2f}:1
📈 <b>Tendencia:</b> {info_canal['direccion']}
💪 <b>Fuerza:</b> {info_canal['fuerza_texto']}
📏 <b>Ángulo:</b> {info_canal['angulo_tendencia']:.1f}°
🎰 <b>Stochástico:</b> {stoch_estado}
📊 <b>Stoch K/D:</b> {info_canal['stoch_k']:.1f}/{info_canal['stoch_d']:.1f}
⏰ <b>Hora:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
💡 <b>Estrategia:</b> BREAKOUT + REENTRY
🤖 <b>Bot Bitget Futures</b>
            """
            
            # Enviar a Telegram
            token = self.config.get('telegram_token')
            chat_ids = self.config.get('telegram_chat_ids', [])
            
            if token and chat_ids:
                logger.info(f"     📨 Enviando señal {tipo_operacion} para {simbolo}")
                self._enviar_telegram_simple(mensaje, token, chat_ids)
            else:
                logger.info(f"     📢 Señal {tipo_operacion} para {simbolo} (sin Telegram)")
            
            # Registrar operación activa
            self.operaciones_activas[simbolo] = {
                'tipo': tipo_operacion,
                'precio_entrada': precio_entrada,
                'take_profit': tp,
                'stop_loss': sl,
                'timestamp_entrada': datetime.now().isoformat(),
                'angulo_tendencia': info_canal['angulo_tendencia'],
                'pearson': info_canal['coeficiente_pearson'],
                'r2_score': info_canal['r2_score'],
                'ancho_canal_porcentual': info_canal['ancho_canal_porcentual'],
                'nivel_fuerza': info_canal['nivel_fuerza'],
                'timeframe_utilizado': config_optima['timeframe'],
                'velas_utilizadas': config_optima['num_velas'],
                'stoch_k': info_canal['stoch_k'],
                'stoch_d': info_canal['stoch_d'],
                'breakout_usado': breakout_info is not None
            }
            
            self.senales_enviadas.add(simbolo)
            self.total_operaciones += 1
            logger.info(f"     ✅ Señal {tipo_operacion} para {simbolo} registrada")
            
        except Exception as e:
            logger.error(f"Error generando señal para {simbolo}: {e}")

    def inicializar_log(self):
        """Inicializa el archivo de log de operaciones"""
        try:
            if not os.path.exists(self.archivo_log):
                with open(self.archivo_log, 'w', newline='', encoding='utf-8') as f:
                    writer = csv.writer(f)
                    writer.writerow([
                        'timestamp', 'symbol', 'tipo', 'precio_entrada',
                        'take_profit', 'stop_loss', 'precio_salida',
                        'resultado', 'pnl_percent', 'duracion_minutos',
                        'angulo_tendencia', 'pearson', 'r2_score',
                        'ancho_canal_porcentual', 'nivel_fuerza',
                        'timeframe_utilizado', 'velas_utilizadas',
                        'stoch_k', 'stoch_d', 'breakout_usado'
                    ])
                logger.info("✅ Archivo de log inicializado")
        except Exception as e:
            logger.error(f"Error inicializando log: {e}")

    def verificar_cierre_operaciones(self):
        """Verifica y cierra operaciones que hayan alcanzado TP o SL"""
        if not self.operaciones_activas:
            return []
            
        operaciones_cerradas = []
        
        for simbolo, operacion in list(self.operaciones_activas.items()):
            try:
                # Obtener precio actual
                config_optima = self.config_optima_por_simbolo.get(simbolo)
                if not config_optima:
                    continue
                    
                datos = self.obtener_datos_mercado_config(simbolo, config_optima['timeframe'], 1)
                if not datos:
                    continue
                    
                precio_actual = datos['precio_actual']
                tp = operacion['take_profit']
                sl = operacion['stop_loss']
                tipo = operacion['tipo']
                
                resultado = None
                
                # Verificar TP/SL
                if tipo == "LONG":
                    if precio_actual >= tp:
                        resultado = "TP"
                    elif precio_actual <= sl:
                        resultado = "SL"
                else:  # SHORT
                    if precio_actual <= tp:
                        resultado = "TP"
                    elif precio_actual >= sl:
                        resultado = "SL"
                
                if resultado:
                    # Calcular PnL
                    if tipo == "LONG":
                        pnl_percent = ((precio_actual - operacion['precio_entrada']) / operacion['precio_entrada']) * 100
                    else:
                        pnl_percent = ((operacion['precio_entrada'] - precio_actual) / operacion['precio_entrada']) * 100
                    
                    tiempo_entrada = datetime.fromisoformat(operacion['timestamp_entrada'])
                    duracion_minutos = (datetime.now() - tiempo_entrada).total_seconds() / 60
                    
                    # Registrar operación
                    datos_operacion = {
                        'timestamp': datetime.now().isoformat(),
                        'symbol': simbolo,
                        'tipo': tipo,
                        'precio_entrada': operacion['precio_entrada'],
                        'take_profit': tp,
                        'stop_loss': sl,
                        'precio_salida': precio_actual,
                        'resultado': resultado,
                        'pnl_percent': pnl_percent,
                        'duracion_minutos': duracion_minutos,
                        'angulo_tendencia': operacion.get('angulo_tendencia', 0),
                        'pearson': operacion.get('pearson', 0),
                        'r2_score': operacion.get('r2_score', 0),
                        'ancho_canal_porcentual': operacion.get('ancho_canal_porcentual', 0),
                        'nivel_fuerza': operacion.get('nivel_fuerza', 1),
                        'timeframe_utilizado': operacion.get('timeframe_utilizado', 'N/A'),
                        'velas_utilizadas': operacion.get('velas_utilizadas', 0),
                        'stoch_k': operacion.get('stoch_k', 0),
                        'stoch_d': operacion.get('stoch_d', 0),
                        'breakout_usado': operacion.get('breakout_usado', False)
                    }
                    
                    self.registrar_operacion(datos_operacion)
                    
                    # Notificar por Telegram
                    mensaje_cierre = self.generar_mensaje_cierre(datos_operacion)
                    token = self.config.get('telegram_token')
                    chats = self.config.get('telegram_chat_ids', [])
                    
                    if token and chats:
                        try:
                            self._enviar_telegram_simple(mensaje_cierre, token, chats)
                        except Exception:
                            pass
                    
                    operaciones_cerradas.append(simbolo)
                    del self.operaciones_activas[simbolo]
                    
                    if simbolo in self.senales_enviadas:
                        self.senales_enviadas.remove(simbolo)
                    
                    self.operaciones_desde_optimizacion += 1
                    logger.info(f"     📊 {simbolo} Operación {resultado} - PnL: {pnl_percent:.2f}%")
                    
            except Exception as e:
                logger.error(f"Error verificando cierre para {simbolo}: {e}")
                continue
        
        return operaciones_cerradas

    def generar_mensaje_cierre(self, datos_operacion):
        """Genera mensaje de cierre de operación"""
        emoji = "🟢" if datos_operacion['resultado'] == "TP" else "🔴"
        color_emoji = "✅" if datos_operacion['resultado'] == "TP" else "❌"
        
        if datos_operacion['tipo'] == 'LONG':
            pnl_absoluto = datos_operacion['precio_salida'] - datos_operacion['precio_entrada']
        else:
            pnl_absoluto = datos_operacion['precio_entrada'] - datos_operacion['precio_salida']
        
        breakout_usado = "🚀 Sí" if datos_operacion.get('breakout_usado', False) else "❌ No"
        
        mensaje = f"""
{emoji} <b>OPERACIÓN CERRADA - {datos_operacion['symbol']}</b>
{color_emoji} <b>RESULTADO: {datos_operacion['resultado']}</b>
📊 Tipo: {datos_operacion['tipo']}
💰 Entrada: {datos_operacion['precio_entrada']:.8f}
🎯 Salida: {datos_operacion['precio_salida']:.8f}
💵 PnL Absoluto: {pnl_absoluto:+.8f}
📈 PnL %: {datos_operacion['pnl_percent']:+.2f}%
⏰ Duración: {datos_operacion['duracion_minutos']:.1f} minutos
🚀 Breakout+Reentry: {breakout_usado}
📏 Ángulo: {datos_operacion['angulo_tendencia']:.1f}°
📊 Pearson: {datos_operacion['pearson']:.3f}
🎯 R²: {datos_operacion['r2_score']:.3f}
📏 Ancho: {datos_operacion.get('ancho_canal_porcentual', 0):.1f}%
⏱️ TF: {datos_operacion.get('timeframe_utilizado', 'N/A')}
🕯️ Velas: {datos_operacion.get('velas_utilizadas', 0)}
🕒 {datos_operacion['timestamp']}
        """
        return mensaje

    def registrar_operacion(self, datos_operacion):
        """Registra operación en el archivo CSV"""
        try:
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
                    datos_operacion.get('ancho_canal_porcentual', 0),
                    datos_operacion.get('nivel_fuerza', 1),
                    datos_operacion.get('timeframe_utilizado', 'N/A'),
                    datos_operacion.get('velas_utilizadas', 0),
                    datos_operacion.get('stoch_k', 0),
                    datos_operacion.get('stoch_d', 0),
                    datos_operacion.get('breakout_usado', False)
                ])
        except Exception as e:
            logger.error(f"Error registrando operación: {e}")

    # ---------------------------
    # MÉTODOS DE CÁLCULO
    # ---------------------------
    def calcular_stochastic(self, datos_mercado, period=14, k_period=3, d_period=3):
        """Calcula el indicador Stochastic"""
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
        """Calcula regresión lineal simple"""
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
        """Calcula coeficiente de Pearson y ángulo de tendencia"""
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
        
        # Calcular ángulo en grados
        if len(y) > 1:
            rango_y = max(y) - min(y)
            if rango_y != 0:
                escala = len(x) / rango_y
                angulo_radianes = math.atan(pendiente * escala)
            else:
                angulo_radianes = math.atan(pendiente)
        else:
            angulo_radianes = math.atan(pendiente)
            
        angulo_grados = math.degrees(angulo_radianes)
        return pearson, angulo_grados

    def clasificar_fuerza_tendencia(self, angulo_grados):
        """Clasifica la fuerza de la tendencia"""
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
        """Determina dirección de la tendencia"""
        if abs(angulo_grados) < umbral_minimo:
            return "⚪ RANGO"
        elif angulo_grados > 0:
            return "🟢 ALCISTA"
        else:
            return "🔴 BAJISTA"

    def calcular_r2(self, y_real, x, pendiente, intercepto):
        """Calcula R² score"""
        if len(y_real) != len(x):
            return 0
            
        y_real = np.array(y_real)
        y_pred = pendiente * np.array(x) + intercepto
        
        ss_res = np.sum((y_real - y_pred) ** 2)
        ss_tot = np.sum((y_real - np.mean(y_real)) ** 2)
        
        if ss_tot == 0:
            return 0
            
        return 1 - (ss_res / ss_tot)

    def ejecutar_analisis(self):
        """Ejecuta un ciclo completo de análisis"""
        try:
            logger.info("\n" + "="*60)
            logger.info("🔁 INICIANDO CICLO DE ANÁLISIS")
            logger.info("="*60)
            
            # Verificar cierre de operaciones
            cierres = self.verificar_cierre_operaciones()
            if cierres:
                logger.info(f"     📊 Operaciones cerradas: {', '.join(cierres)}")
            
            # Ejecutar escaneo de mercado
            senales_encontradas = self.escanear_mercado()
            
            # Guardar estado
            self.guardar_estado()
            
            # Mostrar resumen
            self.mostrar_resumen_operaciones()
            
            logger.info(f"\n✅ Ciclo completado. Señales encontradas: {senales_encontradas}")
            logger.info("="*60)
            
            return senales_encontradas
            
        except Exception as e:
            logger.error(f"❌ Error en ejecución de análisis: {e}")
            return 0

    def mostrar_resumen_operaciones(self):
        """Muestra resumen de operaciones"""
        logger.info(f"\n📊 RESUMEN OPERACIONES:")
        logger.info(f"   Activas: {len(self.operaciones_activas)}")
        logger.info(f"   Esperando reentry: {len(self.esperando_reentry)}")
        logger.info(f"   Total ejecutadas: {self.total_operaciones}")
        
        if self.operaciones_activas:
            for simbolo, op in self.operaciones_activas.items():
                estado = "🟢 LONG" if op['tipo'] == 'LONG' else "🔴 SHORT"
                ancho_canal = op.get('ancho_canal_porcentual', 0)
                timeframe = op.get('timeframe_utilizado', 'N/A')
                velas = op.get('velas_utilizadas', 0)
                breakout = "🚀" if op.get('breakout_usado', False) else ""
                logger.info(f"   • {simbolo} {estado} {breakout} - {timeframe} - {velas}v - Ancho: {ancho_canal:.1f}%")

# ---------------------------
# CONFIGURACIÓN
# ---------------------------
def crear_config_desde_entorno():
    """Configuración desde variables de entorno"""
    directorio_actual = os.path.dirname(os.path.abspath(__file__))
    
    # Obtener chat IDs de Telegram
    telegram_chat_ids_str = os.environ.get('TELEGRAM_CHAT_ID', '')
    telegram_chat_ids = [cid.strip() for cid in telegram_chat_ids_str.split(',') if cid.strip()]
    
    # Si no hay chat IDs, usar uno por defecto
    if not telegram_chat_ids:
        telegram_chat_ids = ['-1002272872445']  # Chat por defecto
    
    # Configuración del bot
    config = {
        # Parámetros de trading
        'min_channel_width_percent': 4.0,
        'trend_threshold_degrees': 16.0,
        'min_trend_strength_degrees': 16.0,
        'entry_margin': 0.001,
        'min_rr_ratio': 1.2,
        
        # Tiempos y escaneo
        'scan_interval_minutes': 5,  # Escanear cada 5 minutos
        'timeframes': ['5m', '15m', '30m'],
        'velas_options': [80, 100, 120],
        
        # Símbolos a analizar
        'symbols': BITGET_SYMBOLS,
        
        # Telegram
        'telegram_token': os.environ.get('TELEGRAM_TOKEN', ''),
        'telegram_chat_ids': telegram_chat_ids,
        
        # Optimización
        'auto_optimize': True,
        'min_samples_optimizacion': 30,
        'reevaluacion_horas': 24,
        
        # Archivos
        'log_path': os.path.join(directorio_actual, 'operaciones_log_bitget.csv'),
        'estado_file': os.path.join(directorio_actual, 'estado_bot_bitget.json')
    }
    
    # Validar configuración
    logger.info("⚙️ Configuración cargada:")
    logger.info(f"   • Símbolos: {len(config['symbols'])}")
    logger.info(f"   • Timeframes: {config['timeframes']}")
    logger.info(f"   • Telegram configurado: {'✅' if config['telegram_token'] else '❌'}")
    logger.info(f"   • Modo demo: {'✅' if BITGET_API_KEY == 'demo' else '❌'}")
    
    return config

# ---------------------------
# FLASK APP PARA RENDER
# ---------------------------

app = Flask(__name__)

# Inicializar bot (pero NO iniciar el hilo automáticamente)
# El hilo se iniciará manualmente cuando verifiquemos que todo está listo
config = crear_config_desde_entorno()
bot = None
bot_thread = None

def run_bot_loop():
    """Función que ejecuta el bot en un hilo separado"""
    global bot
    
    if not bot:
        logger.error("❌ Bot no inicializado")
        return
    
    logger.info("🚀 Iniciando loop principal del bot...")
    
    try:
        while True:
            try:
                # Ejecutar análisis
                bot.ejecutar_analisis()
                
                # Esperar intervalo configurado
                wait_minutes = bot.config.get('scan_interval_minutes', 5)
                logger.info(f"⏳ Esperando {wait_minutes} minutos para próximo análisis...")
                
                # Esperar en intervalos pequeños para poder interrumpir
                for _ in range(wait_minutes * 60):
                    time.sleep(1)
                    
            except KeyboardInterrupt:
                logger.info("🛑 Bot detenido por interrupción")
                break
            except Exception as e:
                logger.error(f"❌ Error en loop del bot: {e}")
                logger.info("🔄 Reintentando en 60 segundos...")
                time.sleep(60)
                
    except Exception as e:
        logger.error(f"❌ Error crítico en hilo del bot: {e}")
    finally:
        logger.info("👋 Hilo del bot finalizado")

@app.route('/')
def index():
    """Endpoint principal - Health check"""
    global bot, bot_thread
    
    status = {
        'status': 'online',
        'service': 'Bitget Breakout+Reentry Bot',
        'timestamp': datetime.now().isoformat(),
        'bot_initialized': bot is not None,
        'bot_running': bot_thread is not None and bot_thread.is_alive() if bot_thread else False
    }
    
    return jsonify(status), 200

@app.route('/health')
def health():
    """Endpoint de health check para Render"""
    return jsonify({'status': 'healthy', 'timestamp': datetime.now().isoformat()}), 200

@app.route('/start-bot', methods=['POST'])
def start_bot():
    """Inicia el bot manualmente"""
    global bot, bot_thread
    
    try:
        if bot_thread and bot_thread.is_alive():
            return jsonify({'status': 'error', 'message': 'Bot ya está en ejecución'}), 400
        
        # Inicializar bot si no está inicializado
        if not bot:
            bot = TradingBot(config)
        
        # Iniciar hilo del bot
        bot_thread = threading.Thread(target=run_bot_loop, daemon=True)
        bot_thread.start()
        
        return jsonify({
            'status': 'success',
            'message': 'Bot iniciado correctamente',
            'thread_alive': bot_thread.is_alive()
        }), 200
        
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/stop-bot', methods=['POST'])
def stop_bot():
    """Detiene el bot"""
    global bot_thread
    
    if bot_thread and bot_thread.is_alive():
        # Enviar señal de interrupción al hilo
        # (en Flask, esto es limitado, pero podemos marcar una variable de control)
        return jsonify({'status': 'warning', 'message': 'Para detener completamente, reinicia el servicio'}), 200
    
    return jsonify({'status': 'success', 'message': 'Bot no está en ejecución'}), 200

@app.route('/status')
def get_status():
    """Obtiene estado del bot"""
    global bot, bot_thread
    
    status = {
        'bot_initialized': bot is not None,
        'bot_running': bot_thread is not None and bot_thread.is_alive() if bot_thread else False,
        'operaciones_activas': len(bot.operaciones_activas) if bot else 0,
        'esperando_reentry': len(bot.esperando_reentry) if bot else 0,
        'total_operaciones': bot.total_operaciones if bot else 0,
        'timestamp': datetime.now().isoformat()
    }
    
    return jsonify(status), 200

@app.route('/scan-now', methods=['POST'])
def scan_now():
    """Ejecuta un escaneo inmediato"""
    global bot
    
    if not bot:
        return jsonify({'status': 'error', 'message': 'Bot no inicializado'}), 400
    
    try:
        senales = bot.ejecutar_analisis()
        return jsonify({'status': 'success', 'senales_encontradas': senales}), 200
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

# Configuración automática del webhook de Telegram
def setup_telegram_webhook():
    """Configura webhook de Telegram si hay token"""
    token = os.environ.get('TELEGRAM_TOKEN')
    if not token:
        logger.info("ℹ️ No hay token de Telegram, omitiendo configuración de webhook")
        return
    
    try:
        # Obtener URL del servicio
        webhook_url = os.environ.get('WEBHOOK_URL')
        if not webhook_url:
            render_url = os.environ.get('RENDER_EXTERNAL_URL')
            if render_url:
                webhook_url = f"{render_url}/webhook"
            else:
                logger.warning("⚠️ No se pudo obtener URL para webhook")
                return
        
        # Configurar webhook
        logger.info(f"🔗 Configurando webhook de Telegram: {webhook_url}")
        
        # Eliminar webhook anterior
        requests.get(f"https://api.telegram.org/bot{token}/deleteWebhook", timeout=5)
        
        # Configurar nuevo webhook
        response = requests.get(f"https://api.telegram.org/bot{token}/setWebhook?url={webhook_url}", timeout=5)
        
        if response.status_code == 200:
            logger.info("✅ Webhook de Telegram configurado correctamente")
        else:
            logger.warning(f"⚠️ Error configurando webhook: {response.text}")
            
    except Exception as e:
        logger.error(f"❌ Error configurando webhook de Telegram: {e}")

# Inicialización al arrancar
if __name__ == '__main__':
    # Configurar webhook de Telegram
    setup_telegram_webhook()
    
    # Obtener puerto de Render (por defecto 10000)
    port = int(os.environ.get('PORT', 10000))
    
    # Inicializar bot pero NO iniciar el hilo automáticamente
    # Esto permite que Render verifique que el servicio está vivo primero
    try:
        bot = TradingBot(config)
        logger.info(f"✅ Bot inicializado. Servicio Flask en puerto {port}")
        logger.info("📢 Envía POST a /start-bot para iniciar el bot automáticamente")
    except Exception as e:
        logger.error(f"❌ Error inicializando bot: {e}")
        bot = None
    
    # Iniciar servidor Flask
    app.run(host='0.0.0.0', port=port, debug=False)
else:
    # Para gunicorn en Render
    try:
        bot = TradingBot(config)
        logger.info("✅ Bot inicializado para gunicorn")
    except Exception as e:
        logger.error(f"❌ Error inicializando bot en gunicorn: {e}")
        bot = None
