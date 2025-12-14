
# bot_web_service.py
# Adaptación para Render del bot Breakout + Reentry
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
matplotlib.use('Agg') # Backend sin GUI
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

# -# [INICIO DEL CÓDIGO DEL BOT NUEVO]
# Copiado íntegro de Pasted_Text_1763228298547.txt y corregido para Render
# -#
# bot_breakout_reentry.py
# VERSIÓN COMPLETA con estrategia Breakout + Reentry
import requests
import time
import json
import os
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

class BitgetClient:
    """Cliente para interactuar con la API de Bitget V2"""
    def __init__(self, api_key, api_secret, passphrase):
        self.api_key = api_key
        self.api_secret = api_secret
        self.passphrase = passphrase
        self.base_url = 'https://api.bitget.com'

    def _get_headers(self, method, request_path, body=''):
        """Generar encabezados de autenticación para Bitget V2"""
        try:
            timestamp = str(int(time.time() * 1000))
            if isinstance(body, dict):
                body_str = json.dumps(body, separators=(',', ':')) if body else ''
            else:
                body_str = str(body) if body else ''
            message = timestamp + method.upper() + request_path + body_str
            mac = hmac.new(bytes(self.api_secret, 'utf-8'), bytes(message, 'utf-8'), digestmod=hashlib.sha256)
            signature = base64.b64encode(mac.digest()).decode()
            headers = {
                'ACCESS-KEY': self.api_key,
                'ACCESS-SIGN': signature,
                'ACCESS-TIMESTAMP': timestamp,
                'ACCESS-PASSPHRASE': self.passphrase,
                'Content-Type': 'application/json'
            }
            return headers
        except Exception as e:
            logger.error(f"Error generando firma: {e}")
            raise

    def get_klines(self, symbol, interval, limit):
        """Obtener velas (klines) de Bitget V2"""
        try:
            request_path = '/api/v2/mix/market/candles'
            params = {
                'symbol': symbol,
                'granularity': interval,
                'limit': str(limit)
            }
            query_string = f"?symbol={symbol}&granularity={interval}&limit={limit}"
            full_request_path = request_path + query_string
            headers = self._get_headers('GET', full_request_path, '')
            response = requests.get(f"{self.base_url}{request_path}", headers=headers, params=params, timeout=10)
            logger.info(f"Respuesta klines - Status: {response.status_code}, URL: {full_request_path}")
            if response.status_code == 200:
                data = response.json()
                if data.get('code') == '00000':
                    candles = data.get('data', [])
                    logger.info(f"✅ Klines obtenidas exitosamente para {symbol}: {len(candles)} velas")
                    return candles
                else:
                    logger.error(f"❌ Error en la respuesta de klines: {data.get('msg', 'Unknown error')}")
                    return None
            else:
                logger.error(f"❌ Error HTTP al obtener klines: {response.status_code} - {response.text}")
                return None
        except Exception as e:
            logger.error(f"❌ Error en get_klines: {e}")
            return None

    def place_order(self, symbol, side, marginCoin, size, price='', clientOrderId='', posSide='long'):
        """Colocar una orden en Bitget V2"""
        try:
            request_path = '/api/v2/mix/order/place-order'
            body = {
                'symbol': symbol,
                'marginCoin': marginCoin,
                'size': str(size),
                'side': side.lower(),
                'posSide': posSide.lower(),
                'tradeMode': 'cross',
                'orderType': 'market' if price == '' else 'limit',
                'price': price
            }
            if clientOrderId:
                body['clientOid'] = clientOrderId
            headers = self._get_headers('POST', request_path, body)
            response = requests.post(f"{self.base_url}{request_path}", headers=headers, json=body, timeout=10)
            logger.info(f"Respuesta colocar orden - Status: {response.status_code}, Body: {body}")
            if response.status_code == 200:
                data = response.json()
                if data.get('code') == '00000':
                    order_data = data.get('data', {})
                    logger.info(f"✅ Orden colocada exitosamente. ID: {order_data.get('orderId', 'N/A')}")
                    return order_data
                else:
                    logger.error(f"❌ Error en la respuesta de colocar orden: {data.get('msg', 'Unknown error')}")
                    return None
            else:
                logger.error(f"❌ Error HTTP al colocar orden: {response.status_code} - {response.text}")
                return None
        except Exception as e:
            logger.error(f"❌ Error en place_order: {e}")
            return None

    def set_leverage(self, symbol, leverage, hold_side='long'):
        """Configurar apalancamiento en Bitget V2"""
        try:
            request_path = '/api/v2/mix/account/set-leverage'
            body = {
                'symbol': symbol,
                'productType': 'USDT-FUTURES',
                'marginCoin': 'USDT',
                'leverage': str(leverage),
                'holdSide': hold_side
            }
            headers = self._get_headers('POST', request_path, body)
            response = requests.post(f"{self.base_url}{request_path}", headers=headers, json=body, timeout=10)
            logger.info(f"Respuesta set_leverage - Status: {response.status_code}, Body: {body}")
            if response.status_code == 200:
                data = response.json()
                if data.get('code') == '00000':
                    logger.info(f"✓ Apalancamiento {leverage}x configurado para {symbol}")
                    return True
                else:
                    logger.error(f"❌ Error en la respuesta de set_leverage: {data.get('msg', 'Unknown error')}")
                    return False
            else:
                logger.error(f"❌ Error HTTP en set_leverage: {response.status_code} - {response.text}")
                return False
        except Exception as e:
            logger.error(f"❌ Error en set_leverage: {e}")
            return False

    def get_positions(self, symbol=None, product_type='USDT-FUTURES'):
        """Obtener posiciones abiertas en Bitget V2"""
        try:
            request_path = '/api/v2/mix/position/all-position'
            params = {'productType': product_type, 'marginCoin': 'USDT'}
            if symbol:
                params['symbol'] = symbol
            query_parts = []
            for key, value in params.items():
                query_parts.append(f"{key}={value}")
            query_string = "?" + "&".join(query_parts)
            full_request_path = request_path + query_string
            headers = self._get_headers('GET', full_request_path, '')
            response = requests.get(f"{self.base_url}{request_path}", headers=headers, params=params, timeout=10)
            logger.info(f"Respuesta get_positions - Status: {response.status_code}, Query: {query_string}")
            if response.status_code == 200:
                data = response.json()
                if data.get('code') == '00000':
                    positions = data.get('data', [])
                    logger.info(f"✅ Posiciones obtenidas: {len(positions)}")
                    return positions
                else:
                    logger.error(f"❌ Error en la respuesta de get_positions: {data.get('msg', 'Unknown error')}")
                    return None
            else:
                logger.error(f"❌ Error HTTP en get_positions: {response.status_code} - {response.text}")
                return None
        except Exception as e:
            logger.error(f"❌ Error en get_positions: {e}")
            return None

    def create_plan_order(self, symbol, marginCoin, size, triggerPrice, executePrice='', planType='normal', side='buy', posSide='long'):
        """Crear una orden plan (TP/SL) en Bitget V2"""
        try:
            request_path = '/api/v2/mix/order/place-plan'
            body = {
                'symbol': symbol,
                'marginCoin': marginCoin,
                'size': str(size),
                'triggerPrice': str(triggerPrice),
                'executePrice': executePrice,
                'planType': planType,
                'side': side.lower(),
                'posSide': posSide.lower(),
                'triggerType': 'market_price',
                'tradeMode': 'cross'
            }
            headers = self._get_headers('POST', request_path, body)
            response = requests.post(f"{self.base_url}{request_path}", headers=headers, json=body, timeout=10)
            logger.info(f"Respuesta crear plan order - Status: {response.status_code}, Body: {body}")
            if response.status_code == 200:
                data = response.json()
                if data.get('code') == '00000':
                    plan_data = data.get('data', {})
                    logger.info(f"✅ Plan order creado exitosamente. ID: {plan_data.get('planId', 'N/A')}")
                    return plan_data
                else:
                    logger.error(f"❌ Error en la respuesta de crear plan order: {data.get('msg', 'Unknown error')}")
                    return None
            else:
                logger.error(f"❌ Error HTTP al crear plan order: {response.status_code} - {response.text}")
                return None
        except Exception as e:
            logger.error(f"❌ Error en create_plan_order: {e}")
            return None

    def get_account_info(self, product_type='USDT-FUTURES'):
        """Obtener información de cuenta Bitget V2"""
        try:
            request_path = '/api/v2/mix/account/accounts'
            params = {'productType': product_type, 'marginCoin': 'USDT'}
            query_string = f"?productType={product_type}&marginCoin=USDT"
            full_request_path = request_path + query_string
            headers = self._get_headers('GET', full_request_path, '')
            response = requests.get(f"{self.base_url}{request_path}", headers=headers, params=params, timeout=10)
            logger.info(f"Respuesta cuenta - Status: {response.status_code}")
            if response.status_code == 200:
                data = response.json()
                if data.get('code') == '00000':
                    accounts = data.get('data', [])
                    logger.info(f"✅ Información de cuenta obtenida: {len(accounts)} cuentas")
                    return accounts
                else:
                    logger.error(f"❌ Error en la respuesta de cuenta: {data.get('msg', 'Unknown error')}")
                    return None
            else:
                logger.error(f"❌ Error HTTP al obtener cuenta: {response.status_code} - {response.text}")
                return None
        except Exception as e:
            logger.error(f"❌ Error en get_account_info: {e}")
            return None

    def verificar_credenciales(self):
        """Verificar si las credenciales son válidas"""
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
            logger.error(f"❌ Error verificando credenciales: {e}")
            return False

class BotBreakoutReentry:
    def __init__(self, config_file='config.json'):
        self.config = {}
        self.load_config(config_file)
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

        # Optimización automática
        self.auto_optimize = config.get('auto_optimize', True)
        parametros_optimizados = None
        if self.auto_optimize:
            try:
                from optimizador_ia import OptimizadorIA
                ia = OptimizadorIA(log_path=self.log_path, min_samples=config.get('min_samples_optimizacion', 15))
                parametros_optimizados = ia.buscar_mejores_parametros()
            except Exception as e:
                print("⚠ Error en optimización automática:", e)
                parametros_optimizados = None

        if parametros_optimizados:
            self.config['trend_threshold_degrees'] = parametros_optimizados.get('trend_threshold_degrees', self.config.get('trend_threshold_degrees', 13))
            self.config['min_trend_strength_degrees'] = parametros_optimizados.get('min_trend_strength_degrees', self.config.get('min_trend_strength_degrees', 16))
            self.config['entry_margin'] = parametros_optimizados.get('entry_margin', self.config.get('entry_margin', 0.001))

        self.ultimos_datos = {}
        self.operaciones_activas = {}
        self.senales_enviadas = set()
        self.archivo_log = self.log_path
        self.inicializar_log()

    def load_config(self, config_file):
        """Cargar configuración desde archivo o variables de entorno"""
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                self.config = json.load(f)
        except FileNotFoundError:
            logger.warning(f"Archivo de configuración {config_file} no encontrado. Usando variables de entorno.")
            self.config = {
                'symbols': ['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'SOLUSDT', 'ADAUSDT', 'XRPUSDT', 'DOTUSDT', 'DOGEUSDT', 'AVAXUSDT', 'LINKUSDT'],
                'timeframes': ['1m', '3m', '5m', '15m', '30m'],
                'velas_options': [80, 100, 120, 150, 200],
                'trend_threshold_degrees': 13,
                'min_trend_strength_degrees': 16,
                'entry_margin': 0.001,
                'scan_interval_minutes': 1,
                'telegram_token': os.environ.get('TELEGRAM_TOKEN'),
                'telegram_chat_ids': [int(os.environ.get('TELEGRAM_CHAT_ID'))] if os.environ.get('TELEGRAM_CHAT_ID') else [],
                'bitget_api_key': os.environ.get('BITGET_API_KEY'),
                'bitget_secret_key': os.environ.get('BITGET_SECRET_KEY'),
                'bitget_passphrase': os.environ.get('BITGET_PASSPHRASE'),
                'ejecutar_operaciones_automaticas': os.environ.get('EJECUTAR_OPERACIONES_AUTOMATICAS', 'false').lower() == 'true',
                'capital_por_operacion': float(os.environ.get('CAPITAL_POR_OPERACION', 50)),
                'leverage_por_defecto': int(os.environ.get('LEVERAGE_POR_DEFECTO', 20)),
                'log_path': 'bot_log.txt',
                'estado_file': 'estado_bot.json',
                'auto_optimize': True,
                'min_samples_optimizacion': 15
            }
        except Exception as e:
            logger.error(f"Error cargando configuración: {e}")
            self.config = {}

    def inicializar_log(self):
        """Inicializar el archivo de log"""
        try:
            with open(self.archivo_log, 'a', encoding='utf-8') as f:
                f.write(f"\n--- INICIO DE SESIÓN {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---\n")
        except Exception as e:
            logger.error(f"Error inicializando log: {e}")

    def cargar_estado(self):
        """Carga el estado previo del bot incluyendo breakouts"""
        try:
            if os.path.exists(self.estado_file):
                with open(self.estado_file, 'r', encoding='utf-8') as f:
                    estado = json.load(f)
                    self.ultima_optimizacion = datetime.fromisoformat(estado.get('ultima_optimizacion', datetime.now().isoformat()))
                    self.operaciones_desde_optimizacion = estado.get('operaciones_desde_optimizacion', 0)
                    self.total_operaciones = estado.get('total_operaciones', 0)
                    self.breakout_history = {k: datetime.fromisoformat(v) for k, v in estado.get('breakout_history', {}).items()}
                    self.config_optima_por_simbolo = estado.get('config_optima_por_simbolo', {})
                    self.ultima_busqueda_config = {k: datetime.fromisoformat(v) for k, v in estado.get('ultima_busqueda_config', {}).items()}
                    self.breakouts_detectados = estado.get('breakouts_detectados', {})
                    self.esperando_reentry = estado.get('esperando_reentry', {})
                    self.operaciones_activas = estado.get('operaciones_activas', {})
                    self.senales_enviadas = set(estado.get('senales_enviadas', []))
                    logger.info("✅ Estado cargado correctamente")
        except Exception as e:
            logger.error(f"⚠ Error cargando estado previo: {e}")
            logger.info("Se iniciará con estado limpio")

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
                'breakouts_detectados': self.breakouts_detectados,
                'esperando_reentry': self.esperando_reentry,
                'operaciones_activas': self.operaciones_activas,
                'senales_enviadas': list(self.senales_enviadas)
            }
            with open(self.estado_file, 'w', encoding='utf-8') as f:
                json.dump(estado, f, indent=2, ensure_ascii=False)
            logger.info("💾 Estado guardado correctamente")
        except Exception as e:
            logger.error(f"⚠ Error guardando estado: {e}")

    def obtener_datos_mercado_config(self, simbolo, timeframe, num_velas):
        """Obtiene datos con configuración específica usando API de Bitget"""
        # Usar API de Bitget en lugar de Binance
        if self.bitget_client:
            try:
                candles = self.bitget_client.get_klines(simbolo, timeframe, num_velas + 14)
                if not candles or len(candles) == 0:
                    logger.error(f"❌ No se obtuvieron velas para {simbolo} en {timeframe}")
                    return None

                # Procesar datos de Bitget
                maximos = []
                minimos = []
                cierres = []
                tiempos = []
                for i, candle in enumerate(candles):
                    # Formato Bitget: [timestamp, open, high, low, close, volume, ...]
                    maximos.append(float(candle[2]))  # high
                    minimos.append(float(candle[3]))  # low
                    cierres.append(float(candle[4]))  # close
                    tiempos.append(i)

                precio_actual = cierres[-1]
                logger.info(f"✅ Datos obtenidos para {simbolo}: {len(cierres)} velas, precio actual: {precio_actual:.8f}")
                return {
                    'maximos': maximos,
                    'minimos': minimos,
                    'cierres': cierres,
                    'tiempos': tiempos,
                    'precio_actual': precio_actual,
                    'timeframe': timeframe,
                    'num_velas': num_velas
                }
            except Exception as e:
                logger.error(f"❌ Error obteniendo datos de Bitget para {simbolo}: {e}")
                return None
        else:
            # Fallback a Binance
            try:
                url = "https://api.binance.com/api/v3/klines"
                params = {
                    'symbol': simbolo,
                    'interval': timeframe,
                    'limit': num_velas + 14
                }
                respuesta = requests.get(url, params=params, timeout=10)
                klines = respuesta.json()
                df_data = []
                for kline in klines:
                    df_data.append({
                        'Date': pd.to_datetime(kline[0], unit='ms'),
                        'Open': float(kline[1]),
                        'High': float(kline[2]),
                        'Low': float(kline[3]),
                        'Close': float(kline[4]),
                        'Volume': float(kline[5])
                    })
                df = pd.DataFrame(df_data)
                df.set_index('Date', inplace=True)
                maximos = df['High'].tolist()
                minimos = df['Low'].tolist()
                cierres = df['Close'].tolist()
                tiempos = list(range(len(cierres)))
                precio_actual = cierres[-1]
                logger.info(f"✅ Datos obtenidos de Binance para {simbolo}: {len(cierres)} velas, precio actual: {precio_actual:.8f}")
                return {
                    'maximos': maximos,
                    'minimos': minimos,
                    'cierres': cierres,
                    'tiempos': tiempos,
                    'precio_actual': precio_actual,
                    'timeframe': timeframe,
                    'num_velas': num_velas
                }
            except Exception as e:
                logger.error(f"❌ Error obteniendo datos de Binance para {simbolo}: {e}")
                return None

    def calcular_canal_regresion_config(self, datos_mercado, num_velas):
        """Calcula el canal de regresión lineal para los datos proporcionados"""
        try:
            cierres = datos_mercado['cierres']
            tiempos = datos_mercado['tiempos']
            maximos = datos_mercado['maximos']
            minimos = datos_mercado['minimos']

            # Calcular regresión lineal
            x = np.array(tiempos)
            y = np.array(cierres)
            coeficiente_pearson = np.corrcoef(x, y)[0, 1]
            r2_score = coeficiente_pearson ** 2

            # Calcular pendiente y intercepto
            m, b = np.polyfit(x, y, 1)
            pendiente_grados = np.degrees(np.arctan(m))

            # Calcular niveles de soporte y resistencia
            desviacion_estandar = np.std(y)
            resistencia = y + desviacion_estandar
            soporte = y - desviacion_estandar

            # Calcular ancho del canal
            ancho_canal = np.mean(resistencia - soporte)
            ancho_canal_porcentual = (ancho_canal / np.mean(y)) * 100

            # Calcular Stochastic Oscillator
            n = 14
            if len(y) >= n:
                lowest_low = np.min(y[-n:])
                highest_high = np.max(y[-n:])
                stoch_k = ((y[-1] - lowest_low) / (highest_high - lowest_low)) * 100 if highest_high != lowest_low else 50
                stoch_d = np.mean([stoch_k])  # Simplificado, usar media móvil en producción
            else:
                stoch_k = 50
                stoch_d = 50

            # Determinar dirección del canal
            direccion_canal = "ALCISTA" if m > 0 else "BAJISTA"

            # Calcular fuerza del canal
            nivel_fuerza = 0
            if abs(pendiente_grados) >= self.config.get('min_trend_strength_degrees', 16):
                nivel_fuerza = 2
            elif abs(pendiente_grados) >= self.config.get('trend_threshold_degrees', 13):
                nivel_fuerza = 1

            info_canal = {
                'pendiente_grados': pendiente_grados,
                'coeficiente_pearson': coeficiente_pearson,
                'r2_score': r2_score,
                'nivel_fuerza': nivel_fuerza,
                'ancho_canal': ancho_canal,
                'ancho_canal_porcentual': ancho_canal_porcentual,
                'direccion_canal': direccion_canal,
                'stoch_k': stoch_k,
                'stoch_d': stoch_d,
                'pendiente_resistencia': np.polyfit(x, resistencia, 1)[0],
                'pendiente_soporte': np.polyfit(x, soporte, 1)[0],
                'timeframe': datos_mercado.get('timeframe', 'N/A'),
                'num_velas': num_velas
            }

            logger.info(f"✅ Canal calculado para {datos_mercado.get('timeframe', 'N/A')} - R²: {r2_score:.4f}, Pendiente: {pendiente_grados:.2f}°, Fuerza: {nivel_fuerza}")
            return info_canal

        except Exception as e:
            logger.error(f"❌ Error calculando canal de regresión: {e}")
            return None

    def buscar_configuracion_optima_simbolo(self, simbolo):
        """Busca la mejor combinación de velas/timeframe"""
        if simbolo in self.config_optima_por_simbolo:
            config_optima = self.config_optima_por_simbolo[simbolo]
            ultima_busqueda = self.ultima_busqueda_config.get(simbolo)
            if ultima_busqueda and (datetime.now() - ultima_busqueda).total_seconds() < 7200:
                return config_optima
            else:
                logger.info(f" 🔄 Reevaluando configuración para {simbolo} (pasó 2 horas)")

        logger.info(f" 🔍 Buscando configuración óptima para {simbolo}...")
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
                        puntaje_ancho = ancho_actual * 10
                        puntaje_timeframe = prioridad_timeframe.get(timeframe, 50) * 100
                        puntaje_total = puntaje_timeframe + puntaje_ancho

                        if puntaje_total > mejor_puntaje:
                            mejor_puntaje = puntaje_total
                            mejor_config = {
                                'timeframe': timeframe,
                                'num_velas': num_velas,
                                'puntaje': puntaje_total,
                                'info_canal': canal_info
                            }

                except Exception as e:
                    logger.error(f"❌ Error evaluando configuración {timeframe}/{num_velas} para {simbolo}: {e}")
                    continue

        if mejor_config:
            self.config_optima_por_simbolo[simbolo] = mejor_config
            self.ultima_busqueda_config[simbolo] = datetime.now()
            logger.info(f" ✅ Mejor configuración encontrada para {simbolo}: {mejor_config['timeframe']}/{mejor_config['num_velas']} - Puntaje: {mejor_config['puntaje']:.2f}")
            return mejor_config
        else:
            logger.warning(f" ❌ No se encontró configuración óptima para {simbolo}")
            return None

    def detectar_breakout(self, simbolo, info_canal, datos_mercado):
        """Detecta si hay un breakout en el canal"""
        try:
            precio_cierre = datos_mercado['cierres'][-1]
            resistencia = info_canal['pendiente_resistencia'] * len(datos_mercado['tiempos']) + info_canal['pendiente_resistencia']
            soporte = info_canal['pendiente_soporte'] * len(datos_mercado['tiempos']) + info_canal['pendiente_soporte']
            margen = self.config.get('entry_margin', 0.001)

            # Lógica corregida para breakout
            # BREAKOUT_LONG: Ruptura de resistencia en canal BAJISTA (reversión alcista)
            # BREAKOUT_SHORT: Ruptura de soporte en canal ALCISTA (reversión bajista)
            if info_canal['direccion_canal'] == "BAJISTA" and precio_cierre > resistencia * (1 + margen):
                tipo_breakout = "LONG"
                logger.info(f" 🚀 BREAKOUT LONG detectado en {simbolo} - Precio: {precio_cierre:.8f} > Resistencia: {resistencia:.8f}")
                return tipo_breakout
            elif info_canal['direccion_canal'] == "ALCISTA" and precio_cierre < soporte * (1 - margen):
                tipo_breakout = "SHORT"
                logger.info(f" 📉 BREAKOUT SHORT detectado en {simbolo} - Precio: {precio_cierre:.8f} < Soporte: {soporte:.8f}")
                return tipo_breakout
            else:
                logger.debug(f" ⏸️ Sin breakout en {simbolo} - Precio: {precio_cierre:.8f}, Resistencia: {resistencia:.8f}, Soporte: {soporte:.8f}")
                return None

        except Exception as e:
            logger.error(f"❌ Error detectando breakout para {simbolo}: {e}")
            return None

    def enviar_alerta_breakout(self, simbolo, tipo_breakout, info_canal, datos_mercado, config_optima):
        """Envía alerta de BREAKOUT detectado a Telegram con gráfico"""
        try:
            precio_cierre = datos_mercado['cierres'][-1]
            resistencia = info_canal['pendiente_resistencia'] * len(datos_mercado['tiempos']) + info_canal['pendiente_resistencia']
            soporte = info_canal['pendiente_soporte'] * len(datos_mercado['tiempos']) + info_canal['pendiente_soporte']
            direccion_canal = info_canal['direccion_canal']
            margen = self.config.get('entry_margin', 0.001)

            # Preparar mensaje según el tipo de breakout
            if tipo_breakout == "LONG":
                emoji_principal = "📈"
                tipo_texto = "RUPTURA ALCISTA de RESISTENCIA"
                nivel_roto = f"Resistencia: {resistencia:.8f}"
                direccion_emoji = "⬇️"
                contexto = f"Canal {direccion_canal} → Rechazo desde RESISTENCIA"
                expectativa = "posible entrada en long si el precio reingresa al canal"
            else:  # SHORT
                emoji_principal = "📉"
                tipo_texto = "RUPTURA BAJISTA de RESISTENCIA"
                nivel_roto = f"Resistencia: {resistencia:.8f}"
                direccion_emoji = "⬆️"
                contexto = f"Canal {direccion_canal} → Rechazo desde RESISTENCIA"
                expectativa = "posible entrada en short si el precio reingresa al canal"

            mensaje = f"""{emoji_principal} <b>¡BREAKOUT DETECTADO! - {simbolo}</b>
⚠️ <b>{tipo_texto}</b> {direccion_emoji}
⏰ <b>Hora:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
⏳ <b>ESPERANDO REINGRESO...</b>
👁️ Máximo 30 minutos para confirmación

📍 {expectativa}"""

            token = self.config.get('telegram_token')
            chat_ids = self.config.get('telegram_chat_ids', [])

            if token and chat_ids:
                try:
                    logger.info(f" 📊 Generando gráfico de breakout para {simbolo}...")
                    buf = self.generar_grafico_breakout(simbolo, info_canal, datos_mercado, tipo_breakout, config_optima)
                    if buf:
                        logger.info(f" 📨 Enviando alerta de breakout por Telegram...")
                        self.enviar_grafico_telegram(buf, token, chat_ids)
                        time.sleep(0.5)
                        self._enviar_telegram_simple(mensaje, token, chat_ids)
                        logger.info(f" ✅ Alerta de breakout enviada para {simbolo}")
                    else:
                        logger.warning(f" ⚠️ Alerta enviada sin gráfico")
                        self._enviar_telegram_simple(mensaje, token, chat_ids)
                except Exception as e:
                    logger.error(f" ❌ Error enviando alerta de breakout: {e}")
            else:
                logger.info(f" 📢 Breakout detectado en {simbolo} (sin Telegram)")
        except Exception as e:
            logger.error(f" ❌ Error general en enviar_alerta_breakout: {e}")

    def generar_grafico_breakout(self, simbolo, info_canal, datos_mercado, tipo_breakout, config_optima):
        """Genera gráfico especial para el momento del BREAKOUT"""
        try:
            import matplotlib.font_manager as fm
            plt.rcParams['font.family'] = ['DejaVu Sans', 'Segoe UI Emoji', 'Apple Color Emoji', 'Noto Color Emoji']

            # Usar API de Bitget si está disponible
            if self.bitget_client:
                klines = self.bitget_client.get_klines(simbolo, config_optima['timeframe'], config_optima['num_velas'])
                if klines:
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
                else:
                    logger.warning(f" ❗ No se pudieron obtener klines de Bitget para {simbolo}. Usando Binance.")
                    # Fallback a Binance
                    url = "https://api.binance.com/api/v3/klines"
                    params = {
                        'symbol': simbolo,
                        'interval': config_optima['timeframe'],
                        'limit': config_optima['num_velas']
                    }
                    respuesta = requests.get(url, params=params, timeout=10)
                    klines = respuesta.json()
                    df_data = []
                    for kline in klines:
                        df_data.append({
                            'Date': pd.to_datetime(kline[0], unit='ms'),
                            'Open': float(kline[1]),
                            'High': float(kline[2]),
                            'Low': float(kline[3]),
                            'Close': float(kline[4]),
                            'Volume': float(kline[5])
                        })
                    df = pd.DataFrame(df_data)
                    df.set_index('Date', inplace=True)
            else:
                # Fallback a Binance
                url = "https://api.binance.com/api/v3/klines"
                params = {
                    'symbol': simbolo,
                    'interval': config_optima['timeframe'],
                    'limit': config_optima['num_velas']
                }
                respuesta = requests.get(url, params=params, timeout=10)
                klines = respuesta.json()
                df_data = []
                for kline in klines:
                    df_data.append({
                        'Date': pd.to_datetime(kline[0], unit='ms'),
                        'Open': float(kline[1]),
                        'High': float(kline[2]),
                        'Low': float(kline[3]),
                        'Close': float(kline[4]),
                        'Volume': float(kline[5])
                    })
                df = pd.DataFrame(df_data)
                df.set_index('Date', inplace=True)

            # Calcular canal de regresión para el gráfico
            x = np.arange(len(df))
            y = df['Close'].values
            m, b = np.polyfit(x, y, 1)
            resistencia = y + np.std(y)
            soporte = y - np.std(y)

            # Crear gráfico
            fig, axes = plt.subplots(2, 1, figsize=(14, 10), sharex=True, gridspec_kw={'height_ratios': [3, 1]})
            fig.patch.set_facecolor('#1a1a1a')
            axes[0].set_facecolor('#1a1a1a')
            axes[1].set_facecolor('#1a1a1a')

            # Plotear velas
            mpf.plot(df, type='candle', ax=axes[0], style='charles', volume=False, show_nontrading=False)

            # Plotear canal de regresión
            axes[0].plot(df.index, m * x + b, color='yellow', linestyle='--', label='Regresión')
            axes[0].plot(df.index, resistencia, color='red', linestyle='--', label='Resistencia')
            axes[0].plot(df.index, soporte, color='green', linestyle='--', label='Soporte')

            # Marcar el breakout
            ultimo_precio = df['Close'].iloc[-1]
            if tipo_breakout == "LONG":
                axes[0].axhline(y=resistencia[-1], color='red', linestyle='--', linewidth=2, label='Breakout Long')
                axes[0].scatter(df.index[-1], ultimo_precio, color='green', s=100, zorder=5, label='Breakout Point')
            else:
                axes[0].axhline(y=soporte[-1], color='green', linestyle='--', linewidth=2, label='Breakout Short')
                axes[0].scatter(df.index[-1], ultimo_precio, color='red', s=100, zorder=5, label='Breakout Point')

            axes[0].set_title(f'{simbolo}| {tipo_breakout}| {config_optima["timeframe"]}| Bitget + Breakout+Reentry', color='white', fontsize=14)
            axes[0].set_ylabel('Precio', color='white')
            axes[0].tick_params(colors='white')
            axes[0].grid(True, alpha=0.3, color='gray')
            axes[0].legend(loc='upper left', facecolor='#1a1a1a', edgecolor='white')

            # Plotear Stochastic
            n = 14
            if len(y) >= n:
                lowest_low = np.min(y[-n:])
                highest_high = np.max(y[-n:])
                stoch_k = ((y[-1] - lowest_low) / (highest_high - lowest_low)) * 100 if highest_high != lowest_low else 50
                stoch_d = np.mean([stoch_k])  # Simplificado
            else:
                stoch_k = 50
                stoch_d = 50

            axes[1].plot(df.index, [stoch_k] * len(df), color='blue', label='Stoch K')
            axes[1].plot(df.index, [stoch_d] * len(df), color='orange', label='Stoch D')
            axes[1].axhline(y=80, color='red', linestyle='--', label='Overbought')
            axes[1].axhline(y=20, color='green', linestyle='--', label='Oversold')
            axes[1].set_ylim([0, 100])
            axes[1].set_ylabel('Stochastic', color='white')
            axes[1].tick_params(colors='white')
            axes[1].grid(True, alpha=0.3, color='gray')
            axes[1].legend(loc='upper left', facecolor='#1a1a1a', edgecolor='white')

            buf = BytesIO()
            plt.savefig(buf, format='png', dpi=100, bbox_inches='tight', facecolor='#1a1a1a')
            buf.seek(0)
            plt.close(fig)
            logger.info(f" ✅ Gráfico de breakout generado para {simbolo}")
            return buf

        except Exception as e:
            logger.error(f"⚠️ Error generando gráfico de breakout: {e}")
            return None

    def detectar_reentry(self, simbolo, info_canal, datos_mercado):
        """Detecta si hay un reentry válido después de un breakout"""
        try:
            precio_actual = datos_mercado['precio_actual']
            resistencia = info_canal['pendiente_resistencia'] * len(datos_mercado['tiempos']) + info_canal['pendiente_resistencia']
            soporte = info_canal['pendiente_soporte'] * len(datos_mercado['tiempos']) + info_canal['pendiente_soporte']
            margen = self.config.get('entry_margin', 0.001)

            # Verificar si el precio ha reingresado al canal
            if info_canal['direccion_canal'] == "BAJISTA" and precio_actual <= resistencia * (1 + margen) and precio_actual >= soporte * (1 - margen):
                return "LONG"
            elif info_canal['direccion_canal'] == "ALCISTA" and precio_actual >= soporte * (1 - margen) and precio_actual <= resistencia * (1 + margen):
                return "SHORT"
            else:
                logger.debug(f" ⏸️ Sin reentry en {simbolo} - Precio: {precio_actual:.8f}, Resistencia: {resistencia:.8f}, Soporte: {soporte:.8f}")
                return None

        except Exception as e:
            logger.error(f"❌ Error detectando reentry para {simbolo}: {e}")
            return None

    def calcular_niveles_entrada(self, tipo_operacion, info_canal, precio_actual):
        """Calcula los niveles de entrada, TP y SL basados en el canal"""
        try:
            # Calcular niveles de entrada
            if tipo_operacion == "LONG":
                precio_entrada = precio_actual
                tp = precio_actual * 1.02  # 2% de ganancia
                sl = precio_actual * 0.98  # 2% de pérdida
            else:  # SHORT
                precio_entrada = precio_actual
                tp = precio_actual * 0.98  # 2% de ganancia
                sl = precio_actual * 1.02  # 2% de pérdida

            logger.info(f" ✅ Niveles calculados para {tipo_operacion}: Entrada: {precio_entrada:.8f}, TP: {tp:.8f}, SL: {sl:.8f}")
            return precio_entrada, tp, sl

        except Exception as e:
            logger.error(f"❌ Error calculando niveles de entrada: {e}")
            return None, None, None

    def generar_grafico_profesional(self, simbolo, info_canal, datos_mercado, precio_entrada, tp, sl, tipo_operacion):
        """Genera gráfico profesional con niveles de entrada, TP y SL"""
        try:
            config_optima = self.config_optima_por_simbolo.get(simbolo)
            if not config_optima:
                return None

            # Usar API de Bitget si está disponible
            if self.bitget_client:
                klines = self.bitget_client.get_klines(simbolo, config_optima['timeframe'], config_optima['num_velas'])
                if klines:
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
                else:
                    logger.warning(f" ❗ No se pudieron obtener klines de Bitget para {simbolo}. Usando Binance.")
                    # Fallback a Binance
                    url = "https://api.binance.com/api/v3/klines"
                    params = {
                        'symbol': simbolo,
                        'interval': config_optima['timeframe'],
                        'limit': config_optima['num_velas']
                    }
                    respuesta = requests.get(url, params=params, timeout=10)
                    klines = respuesta.json()
                    df_data = []
                    for kline in klines:
                        df_data.append({
                            'Date': pd.to_datetime(kline[0], unit='ms'),
                            'Open': float(kline[1]),
                            'High': float(kline[2]),
                            'Low': float(kline[3]),
                            'Close': float(kline[4]),
                            'Volume': float(kline[5])
                        })
                    df = pd.DataFrame(df_data)
                    df.set_index('Date', inplace=True)
            else:
                # Fallback a Binance
                url = "https://api.binance.com/api/v3/klines"
                params = {
                    'symbol': simbolo,
                    'interval': config_optima['timeframe'],
                    'limit': config_optima['num_velas']
                }
                respuesta = requests.get(url, params=params, timeout=10)
                klines = respuesta.json()
                df_data = []
                for kline in klines:
                    df_data.append({
                        'Date': pd.to_datetime(kline[0], unit='ms'),
                        'Open': float(kline[1]),
                        'High': float(kline[2]),
                        'Low': float(kline[3]),
                        'Close': float(kline[4]),
                        'Volume': float(kline[5])
                    })
                df = pd.DataFrame(df_data)
                df.set_index('Date', inplace=True)

            # Calcular canal de regresión
            x = np.arange(len(df))
            y = df['Close'].values
            m, b = np.polyfit(x, y, 1)
            resistencia = y + np.std(y)
            soporte = y - np.std(y)

            # Crear gráfico
            fig, axes = plt.subplots(2, 1, figsize=(14, 10), sharex=True, gridspec_kw={'height_ratios': [3, 1]})
            fig.patch.set_facecolor('#1a1a1a')
            axes[0].set_facecolor('#1a1a1a')
            axes[1].set_facecolor('#1a1a1a')

            # Plotear velas
            mpf.plot(df, type='candle', ax=axes[0], style='charles', volume=False, show_nontrading=False)

            # Plotear canal de regresión
            axes[0].plot(df.index, m * x + b, color='yellow', linestyle='--', label='Regresión')
            axes[0].plot(df.index, resistencia, color='red', linestyle='--', label='Resistencia')
            axes[0].plot(df.index, soporte, color='green', linestyle='--', label='Soporte')

            # Plotear niveles de entrada, TP y SL
            axes[0].axhline(y=precio_entrada, color='blue', linestyle='-', linewidth=2, label=f'Entrada: {precio_entrada:.8f}')
            axes[0].axhline(y=tp, color='green', linestyle='--', linewidth=2, label=f'TP: {tp:.8f}')
            axes[0].axhline(y=sl, color='red', linestyle='--', linewidth=2, label=f'SL: {sl:.8f}')

            # Marcar el punto de entrada
            axes[0].scatter(df.index[-1], precio_actual, color='blue', s=100, zorder=5, label='Entrada')

            axes[0].set_title(f'{simbolo}| {tipo_operacion}| {config_optima["timeframe"]}| Bitget + Breakout+Reentry', color='white', fontsize=14)
            axes[0].set_ylabel('Precio', color='white')
            axes[0].tick_params(colors='white')
            axes[0].grid(True, alpha=0.3, color='gray')
            axes[0].legend(loc='upper left', facecolor='#1a1a1a', edgecolor='white')

            # Plotear Stochastic
            n = 14
            if len(y) >= n:
                lowest_low = np.min(y[-n:])
                highest_high = np.max(y[-n:])
                stoch_k = ((y[-1] - lowest_low) / (highest_high - lowest_low)) * 100 if highest_high != lowest_low else 50
                stoch_d = np.mean([stoch_k])  # Simplificado
            else:
                stoch_k = 50
                stoch_d = 50

            axes[1].plot(df.index, [stoch_k] * len(df), color='blue', label='Stoch K')
            axes[1].plot(df.index, [stoch_d] * len(df), color='orange', label='Stoch D')
            axes[1].axhline(y=80, color='red', linestyle='--', label='Overbought')
            axes[1].axhline(y=20, color='green', linestyle='--', label='Oversold')
            axes[1].set_ylim([0, 100])
            axes[1].set_ylabel('Stochastic', color='white')
            axes[1].tick_params(colors='white')
            axes[1].grid(True, alpha=0.3, color='gray')
            axes[1].legend(loc='upper left', facecolor='#1a1a1a', edgecolor='white')

            buf = BytesIO()
            plt.savefig(buf, format='png', dpi=100, bbox_inches='tight', facecolor='#1a1a1a')
            buf.seek(0)
            plt.close(fig)
            logger.info(f" ✅ Gráfico profesional generado para {simbolo}")
            return buf

        except Exception as e:
            logger.error(f"⚠️ Error generando gráfico profesional: {e}")
            return None

    def generar_senal_operacion(self, simbolo, tipo_operacion, precio_entrada, tp, sl, info_canal, datos_mercado, config_optima, breakout_info):
        """Genera y envía la señal de operación"""
        try:
            # Preparar mensaje
            emoji = "🟢" if tipo_operacion == "LONG" else "🔴"
            color_emoji = "✅" if tipo_operacion == "LONG" else "❌"
            direccion = "↑" if tipo_operacion == "LONG" else "↓"
            estado_stoch = ""
            if info_canal['stoch_k'] <= 30:
                estado_stoch = "📉 OVERSOLD"
            elif info_canal['stoch_k'] >= 70:
                estado_stoch = "📈 OVERBOUGHT"
            else:
                estado_stoch = "➖ NEUTRO"

            mensaje = f"""{emoji} <b>SEÑAL DE OPERACIÓN - {simbolo}</b>
⚡ <b>Tipo:</b> {tipo_operacion} {direccion}
💰 <b>Precio Entrada:</b> {precio_entrada:.8f}
🎯 <b>Take Profit:</b> {tp:.8f}
🛑 <b>Stop Loss:</b> {sl:.8f}
📊 <b>Timeframe:</b> {config_optima['timeframe']}
📈 <b>Stochastic:</b> {info_canal['stoch_k']:.2f} ({estado_stoch})
🔄 <b>Breakout Detectado:</b> {breakout_info['tipo']} el {breakout_info['timestamp'].strftime('%Y-%m-%d %H:%M:%S')}
💡 <b>Estrategia:</b> BREAKOUT + REENTRY con confirmación Stochastic"""

            token = self.config.get('telegram_token')
            chat_ids = self.config.get('telegram_chat_ids', [])

            if token and chat_ids:
                try:
                    logger.info(f" 📊 Generando gráfico para {simbolo}...")
                    buf = self.generar_grafico_profesional(simbolo, info_canal, datos_mercado, precio_entrada, tp, sl, tipo_operacion)
                    if buf:
                        logger.info(f" 📨 Enviando gráfico por Telegram...")
                        self.enviar_grafico_telegram(buf, token, chat_ids)
                        time.sleep(1)
                        self._enviar_telegram_simple(mensaje, token, chat_ids)
                        logger.info(f" ✅ Señal {tipo_operacion} para {simbolo} enviada")
                    else:
                        logger.warning(f" ⚠️ Señal enviada sin gráfico")
                        self._enviar_telegram_simple(mensaje, token, chat_ids)
                except Exception as e:
                    logger.error(f" ❌ Error enviando señal: {e}")
            else:
                logger.info(f" 📢 Señal generada para {simbolo} (sin Telegram)")

            # NUEVO: Ejecutar operación automáticamente si está habilitado
            if self.ejecutar_operaciones_automaticas and self.bitget_client:
                try:
                    logger.info(f" 🤖 Ejecutando operación automática para {simbolo}...")
                    resultado = self.ejecutar_operacion_bitget(simbolo, tipo_operacion, self.capital_por_operacion, self.leverage_por_defecto)
                    if resultado:
                        logger.info(f" ✅ Operación ejecutada exitosamente para {simbolo}")
                    else:
                        logger.error(f" ❌ Fallo en la ejecución automática para {simbolo}")
                except Exception as e:
                    logger.error(f" ⚠️ Error en ejecución automática: {e}")

        except Exception as e:
            logger.error(f" ❌ Error generando señal de operación: {e}")

    def ejecutar_operacion_bitget(self, simbolo, tipo_operacion, capital_usd, leverage=20):
        """Ejecutar una operación completa en Bitget (posición + TP/SL)"""
        try:
            if not self.bitget_client:
                logger.error("❌ Cliente Bitget no inicializado")
                return False

            # Configurar apalancamiento
            if not self.bitget_client.set_leverage(simbolo, leverage, tipo_operacion.lower()):
                logger.error(f"❌ Fallo al configurar apalancamiento para {simbolo}")
                return False

            # Obtener información de cuenta
            accounts = self.bitget_client.get_account_info()
            if not accounts:
                logger.error("❌ No se pudo obtener información de cuenta")
                return False

            balance_disponible = 0
            for account in accounts:
                if account.get('marginCoin') == 'USDT':
                    balance_disponible = float(account.get('available', 0))
                    break

            if balance_disponible < capital_usd:
                logger.error(f"❌ Saldo insuficiente: {balance_disponible:.2f} USDT < {capital_usd} USDT requeridos")
                return False

            # Calcular tamaño de la posición
            precio_actual = self.obtener_datos_mercado_config(simbolo, '1m', 1)['precio_actual']
            cantidad_contratos = (capital_usd * leverage) / precio_actual

            # Colocar orden de entrada
            orden_entrada = self.bitget_client.place_order(
                symbol=simbolo,
                side='buy' if tipo_operacion == 'LONG' else 'sell',
                marginCoin='USDT',
                size=str(cantidad_contratos),
                posSide=tipo_operacion.lower()
            )

            if not orden_entrada:
                logger.error(f"❌ Fallo al colocar orden de entrada para {simbolo}")
                return False

            # Crear orden TP
            take_profit = precio_actual * 1.02 if tipo_operacion == 'LONG' else precio_actual * 0.98
            orden_tp = self.bitget_client.create_plan_order(
                symbol=simbolo,
                marginCoin='USDT',
                size=str(cantidad_contratos),
                triggerPrice=str(take_profit),
                executePrice=str(take_profit),
                planType='normal',
                side='sell' if tipo_operacion == 'LONG' else 'buy',
                posSide=tipo_operacion.lower()
            )

            # Crear orden SL
            stop_loss = precio_actual * 0.98 if tipo_operacion == 'LONG' else precio_actual * 1.02
            orden_sl = self.bitget_client.create_plan_order(
                symbol=simbolo,
                marginCoin='USDT',
                size=str(cantidad_contratos),
                triggerPrice=str(stop_loss),
                executePrice=str(stop_loss),
                planType='normal',
                side='sell' if tipo_operacion == 'LONG' else 'buy',
                posSide=tipo_operacion.lower()
            )

            # Registrar operación activa
            self.operaciones_activas[simbolo] = {
                'tipo': tipo_operacion,
                'precio_entrada': precio_actual,
                'take_profit': take_profit,
                'stop_loss': stop_loss,
                'timestamp_entrada': datetime.now().isoformat(),
                'angulo_tendencia': info_canal['angulo_tendencia'],
                'pearson': info_canal['coeficiente_pearson'],
                'r2_score': info_canal['r2_score'],
                'ancho_canal_relativo': info_canal['ancho_canal'] / precio_actual,
                'ancho_canal_porcentual': info_canal['ancho_canal_porcentual'],
                'nivel_fuerza': info_canal['nivel_fuerza'],
                'timeframe_utilizado': config_optima['timeframe'],
                'velas_utilizadas': config_optima['num_velas'],
                'stoch_k': info_canal['stoch_k'],
                'stoch_d': info_canal['stoch_d'],
                'breakout_usado': True,
                'operacion_ejecutada': True
            }

            # Enviar confirmación por Telegram
            token = self.config.get('telegram_token')
            chat_ids = self.config.get('telegram_chat_ids', [])
            if token and chat_ids:
                mensaje_confirmacion = f"""{emoji} <b>OPERACIÓN EJECUTADA - {simbolo}</b>
⚡ <b>Tipo:</b> {tipo_operacion}
💰 <b>Precio Entrada:</b> {precio_actual:.8f}
🎯 <b>Take Profit:</b> {take_profit:.8f}
🛑 <b>Stop Loss:</b> {stop_loss:.8f}
⚖️ <b>Apalancamiento:</b> {leverage}x
🎯 <b>Entrada:</b> {precio_actual:.8f}
🛑 <b>Stop Loss:</b> {stop_loss:.8f}
🎯 <b>Take Profit:</b> {take_profit:.8f}
📋 <b>ID Orden:</b> {orden_entrada.get('orderId', 'N/A')}
⏰ <b>Timestamp:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"""
                self._enviar_telegram_simple(mensaje_confirmacion, token, chat_ids)

            logger.info(f"✅ OPERACIÓN EJECUTADA EXITOSAMENTE para {simbolo}")
            return True

        except Exception as e:
            logger.error(f"❌ Error ejecutando operación en Bitget para {simbolo}: {e}")
            return False

    def escanear_mercado(self):
        """Escanea el mercado con estrategia Breakout + Reentry"""
        logger.info(f"🔍 Escaneando {len(self.config.get('symbols', []))} símbolos (Estrategia: Breakout + Reentry)...")
        senales_encontradas = 0

        for simbolo in self.config.get('symbols', []):
            try:
                if simbolo in self.operaciones_activas:
                    logger.info(f" ⚡ {simbolo} - Operación activa, omitiendo...")
                    continue

                config_optima = self.buscar_configuracion_optima_simbolo(simbolo)
                if not config_optima:
                    logger.warning(f" ❌ {simbolo} - No se encontró configuración válida")
                    continue

                datos_mercado = self.obtener_datos_mercado_config(simbolo, config_optima['timeframe'], config_optima['num_velas'])
                if not datos_mercado:
                    logger.error(f" ❌ {simbolo} - Error obteniendo datos")
                    continue

                info_canal = self.calcular_canal_regresion_config(datos_mercado, config_optima['num_velas'])
                if not info_canal:
                    logger.error(f" ❌ {simbolo} - Error calculando canal")
                    continue

                estado_stoch = ""
                if info_canal['stoch_k'] <= 30:
                    estado_stoch = "📉 OVERSOLD"
                elif info_canal['stoch_k'] >= 70:
                    estado_stoch = "📈 OVERBOUGHT"
                else:
                    estado_stoch = "➖ NEUTRO"

                precio_actual = datos_mercado['precio_actual']

                # Detectar breakout
                tipo_breakout = self.detectar_breakout(simbolo, info_canal, datos_mercado)
                if tipo_breakout:
                    logger.info(f" 🚀 Breakout detectado en {simbolo} - Tipo: {tipo_breakout}")
                    self.breakouts_detectados[simbolo] = {
                        'tipo': tipo_breakout,
                        'timestamp': datetime.now()
                    }
                    self.esperando_reentry[simbolo] = {
                        'tipo': tipo_breakout,
                        'timestamp': datetime.now(),
                        'info_canal': info_canal,
                        'datos_mercado': datos_mercado,
                        'config_optima': config_optima
                    }
                    self.enviar_alerta_breakout(simbolo, tipo_breakout, info_canal, datos_mercado, config_optima)
                    continue

                # Detectar reentry
                tipo_operacion = self.detectar_reentry(simbolo, info_canal, datos_mercado)
                if not tipo_operacion:
                    continue

                precio_entrada, tp, sl = self.calcular_niveles_entrada(tipo_operacion, info_canal, precio_actual)
                if not precio_entrada or not tp or not sl:
                    continue

                if simbolo in self.breakout_history:
                    ultimo_breakout = self.breakout_history[simbolo]
                    tiempo_desde_ultimo = (datetime.now() - ultimo_breakout).total_seconds() / 3600
                    if tiempo_desde_ultimo < 2:
                        logger.info(f" ⏳ {simbolo} - Señal reciente, omitiendo...")
                        continue

                breakout_info = self.esperando_reentry.get(simbolo)
                if breakout_info:
                    self.generar_senal_operacion(simbolo, tipo_operacion, precio_entrada, tp, sl, info_canal, datos_mercado, config_optima, breakout_info)
                    senales_encontradas += 1
                    self.breakout_history[simbolo] = datetime.now()
                    del self.esperando_reentry[simbolo]

            except Exception as e:
                logger.error(f"⚠️ Error analizando {simbolo}: {e}")
                continue

        if self.esperando_reentry:
            logger.info(f"⏳ Esperando reingreso en {len(self.esperando_reentry)} símbolos:")
            for simbolo, info in self.esperando_reentry.items():
                tiempo_espera = (datetime.now() - info['timestamp']).total_seconds() / 60
                logger.info(f" • {simbolo} - {info['tipo']} - Esperando {tiempo_espera:.1f} min")

        # NUEVO: Mostrar breakouts detectados recientemente
        if self.breakouts_detectados:
            logger.info(f"⏰ Breakouts detectados recientemente:")
            for simbolo, info in self.breakouts_detectados.items():
                tiempo_desde_deteccion = (datetime.now() - info['timestamp']).total_seconds() / 60
                logger.info(f" • {simbolo} - {info['tipo']} - Hace {tiempo_desde_deteccion:.1f} min")

        return senales_encontradas

    def verificar_cierre_operaciones(self):
        """Verifica si las operaciones deben cerrarse por TP o SL"""
        if not self.operaciones_activas:
            return []

        operaciones_cerradas = []
        for simbolo, operacion in list(self.operaciones_activas.items()):
            config_optima = self.config_optima_por_simbolo.get(simbolo)
            if not config_optima:
                continue

            datos = self.obtener_datos_mercado_config(simbolo, config_optima['timeframe'], config_optima['num_velas'])
            if not datos:
                continue

            precio_actual = datos['precio_actual']
            tp = operacion['take_profit']
            sl = operacion['stop_loss']
            tipo = operacion['tipo']

            resultado = None
            pnl_percent = 0

            if tipo == "LONG":
                if precio_actual >= tp:
                    resultado = "TP"
                    pnl_percent = ((precio_actual - operacion['precio_entrada']) / operacion['precio_entrada']) * 100
                elif precio_actual <= sl:
                    resultado = "SL"
                    pnl_percent = ((precio_actual - operacion['precio_entrada']) / operacion['precio_entrada']) * 100
            else:  # SHORT
                if precio_actual <= tp:
                    resultado = "TP"
                    pnl_percent = ((operacion['precio_entrada'] - precio_actual) / operacion['precio_entrada']) * 100
                elif precio_actual >= sl:
                    resultado = "SL"
                    pnl_percent = ((operacion['precio_entrada'] - precio_actual) / operacion['precio_entrada']) * 100

            if resultado:
                # Registrar operación cerrada
                datos_operacion = {
                    'simbolo': simbolo,
                    'tipo': tipo,
                    'precio_entrada': operacion['precio_entrada'],
                    'precio_salida': precio_actual,
                    'take_profit': tp,
                    'stop_loss': sl,
                    'resultado': resultado,
                    'pnl_percent': pnl_percent,
                    'timestamp_salida': datetime.now().isoformat(),
                    'timeframe_utilizado': operacion.get('timeframe_utilizado', 'N/A'),
                    'velas_utilizadas': operacion.get('velas_utilizadas', 0),
                    'stoch_k': operacion.get('stoch_k', 0),
                    'stoch_d': operacion.get('stoch_d', 0),
                    'breakout_usado': operacion.get('breakout_usado', False),
                    'operacion_ejecutada': operacion.get('operacion_ejecutada', False)
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
                logger.info(f" 📊 {simbolo} Operación {resultado} - PnL: {pnl_percent:.2f}%")

        return operaciones_cerradas

    def generar_mensaje_cierre(self, datos_operacion):
        """Genera el mensaje de cierre de operación"""
        emoji = "🟢" if datos_operacion['resultado'] == "TP" else "🔴"
        color_emoji = "✅" if datos_operacion['resultado'] == "TP" else "❌"
        if datos_operacion['tipo'] == 'LONG':
            direccion = "↑"
        else:
            direccion = "↓"

        mensaje = f"""{emoji} <b>OPERACIÓN CERRADA - {datos_operacion['simbolo']}</b>
{color_emoji} <b>Resultado:</b> {datos_operacion['resultado']}
💰 <b>Precio Entrada:</b> {datos_operacion['precio_entrada']:.8f}
💸 <b>Precio Salida:</b> {datos_operacion['precio_salida']:.8f}
📊 <b>PnL:</b> {datos_operacion['pnl_percent']:.2f}%
⚡ <b>Tipo:</b> {datos_operacion['tipo']} {direccion}
🕒 <b>Duration:</b> {(datetime.fromisoformat(datos_operacion['timestamp_salida']) - datetime.fromisoformat(datos_operacion['timestamp_entrada'])).seconds // 60} min
📈 <b>Stochastic:</b> K={datos_operacion['stoch_k']:.2f}, D={datos_operacion['stoch_d']:.2f}
🔄 <b>Breakout Usado:</b> {datos_operacion['breakout_usado']}
🤖 <b>Operación Ejecutada:</b> {datos_operacion['operacion_ejecutada']}
⏰ <b>Timestamp:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"""
        return mensaje

    def registrar_operacion(self, datos_operacion):
        """Registra la operación en un archivo CSV"""
        try:
            with open('operaciones_historicas.csv', 'a', newline='', encoding='utf-8') as csvfile:
                fieldnames = ['simbolo', 'tipo', 'precio_entrada', 'precio_salida', 'take_profit', 'stop_loss', 'resultado', 'pnl_percent', 'timestamp_entrada', 'timestamp_salida', 'timeframe_utilizado', 'velas_utilizadas', 'stoch_k', 'stoch_d', 'breakout_usado', 'operacion_ejecutada']
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                if csvfile.tell() == 0:
                    writer.writeheader()
                writer.writerow(datos_operacion)
            logger.info(f" ✅ Operación registrada en CSV: {datos_operacion['simbolo']}")
        except Exception as e:
            logger.error(f" ❌ Error registrando operación: {e}")

    def verificar_envio_reporte_automatico(self):
        """Verifica si debe enviar el reporte semanal (cada lunes a las 9:00)"""
        ahora = datetime.now()
        if ahora.weekday() == 0 and 9 <= ahora.hour < 10:
            archivo_control = "ultimo_reporte.txt"
            try:
                if os.path.exists(archivo_control):
                    with open(archivo_control, 'r') as f:
                        ultima_fecha = f.read().strip()
                        if ultima_fecha == ahora.strftime('%Y-%m-%d'):
                            return False
                if self.enviar_reporte_semanal():
                    with open(archivo_control, 'w') as f:
                        f.write(ahora.strftime('%Y-%m-%d'))
                    return True
            except Exception as e:
                logger.error(f" ❌ Error verificando envío de reporte: {e}")
                return False
        return False

    def enviar_reporte_semanal(self):
        """Envía el reporte semanal por Telegram"""
        mensaje = self.generar_reporte_semanal()
        if not mensaje:
            logger.info("ℹ️ No hay datos suficientes para generar reporte")
            return False

        token = self.config.get('telegram_token')
        chat_ids = self.config.get('telegram_chat_ids', [])
        if token and chat_ids:
            try:
                self._enviar_telegram_simple(mensaje, token, chat_ids)
                logger.info("✅ Reporte semanal enviado correctamente")
                return True
            except Exception as e:
                logger.error(f" ❌ Error enviando reporte: {e}")
                return False
        return False

    def generar_reporte_semanal(self):
        """Genera reporte automático cada semana"""
        try:
            # Contar operaciones totales
            total_operaciones = self.total_operaciones
            operaciones_ganadoras = 0
            operaciones_perdedoras = 0
            pnl_total = 0
            operaciones_con_breakout = 0

            # Leer operaciones históricas
            operaciones = []
            if os.path.exists('operaciones_historicas.csv'):
                with open('operaciones_historicas.csv', 'r', encoding='utf-8') as csvfile:
                    reader = csv.DictReader(csvfile)
                    for row in reader:
                        operaciones.append({
                            'simbolo': row['simbolo'],
                            'tipo': row['tipo'],
                            'precio_entrada': float(row['precio_entrada']),
                            'precio_salida': float(row['precio_salida']),
                            'take_profit': float(row['take_profit']),
                            'stop_loss': float(row['stop_loss']),
                            'resultado': row['resultado'],
                            'pnl_percent': float(row['pnl_percent']),
                            'timestamp_entrada': row['timestamp_entrada'],
                            'timestamp_salida': row['timestamp_salida'],
                            'timeframe_utilizado': row['timeframe_utilizado'],
                            'velas_utilizadas': int(row['velas_utilizadas']),
                            'stoch_k': float(row['stoch_k']),
                            'stoch_d': float(row['stoch_d']),
                            'breakout_usado': row.get('breakout_usado', 'False') == 'True',
                            'operacion_ejecutada': row.get('operacion_ejecutada', 'False') == 'True'
                        })

            # Filtrar operaciones de la última semana
            hoy = datetime.now()
            una_semana_atras = hoy - timedelta(days=7)
            ops_ultima_semana = [op for op in operaciones if datetime.fromisoformat(op['timestamp_salida']) >= una_semana_atras]

            # Calcular estadísticas
            if ops_ultima_semana:
                operaciones_ganadoras = sum(1 for op in ops_ultima_semana if op['resultado'] == 'TP')
                operaciones_perdedoras = sum(1 for op in ops_ultima_semana if op['resultado'] == 'SL')
                pnl_total = sum(op['pnl_percent'] for op in ops_ultima_semana)
                operaciones_con_breakout = sum(1 for op in ops_ultima_semana if op['breakout_usado'])

                win_rate = (operaciones_ganadoras / len(ops_ultima_semana)) * 100 if len(ops_ultima_semana) > 0 else 0
                avg_pnl = pnl_total / len(ops_ultima_semana) if len(ops_ultima_semana) > 0 else 0

                mensaje = f"""📊 <b>REPORTE SEMANAL DEL BOT</b>
📅 <b>Periodo:</b> {una_semana_atras.strftime('%Y-%m-%d')} - {hoy.strftime('%Y-%m-%d')}
📈 <b>Operaciones Totales:</b> {len(ops_ultima_semana)}
✅ <b>Ganadoras:</b> {operaciones_ganadoras}
❌ <b>Perdedoras:</b> {operaciones_perdedoras}
🎯 <b>Win Rate:</b> {win_rate:.2f}%
💰 <b>PnL Total:</b> {pnl_total:.2f}%
📈 <b>PnL Promedio:</b> {avg_pnl:.2f}%
🚀 <b>Con Breakout:</b> {operaciones_con_breakout}

🤖 Bot automático 24/7
⚡ Estrategia: Breakout + Reentry
💎 Integración: Bitget API
💻 Acceso Premium: @TuUsuario"""
                return mensaje
            else:
                return "ℹ️ No hubo operaciones esta semana."
        except Exception as e:
            logger.error(f" ❌ Error generando reporte semanal: {e}")
            return "❌ Error generando reporte semanal."

    def mostrar_resumen_operaciones(self):
        """Muestra un resumen de las operaciones actuales"""
        logger.info(f"📊 RESUMEN OPERACIONES:")
        logger.info(f" Activas: {len(self.operaciones_activas)}")
        logger.info(f" Esperando reentry: {len(self.esperando_reentry)}")
        logger.info(f" Total ejecutadas: {self.total_operaciones}")
        if self.bitget_client:
            logger.info(" 🤖 Bitget: ✅ Conectado")
        else:
            logger.info(" 🤖 Bitget: ❌ No configurado")
        if self.operaciones_activas:
            for simbolo, op in self.operaciones_activas.items():
                estado = "🟢 LONG" if op['tipo'] == 'LONG' else "🔴 SHORT"
                ancho_canal = op.get('ancho_canal_porcentual', 0)
                timeframe = op.get('timeframe_utilizado', 'N/A')
                velas = op.get('velas_utilizadas', 0)
                breakout = "🚀" if op.get('breakout_usado', False) else ""
                ejecutada = "🤖" if op.get('operacion_ejecutada', False) else ""
                logger.info(f" • {simbolo} {estado} {breakout} {ejecutada} - {timeframe} - {velas}v - Ancho: {ancho_canal:.1f}%")

    def reoptimizar_periodicamente(self):
        """Reoptimiza los parámetros periódicamente"""
        try:
            logger.info("🔄 Reoptimizando parámetros...")
            from optimizador_ia import OptimizadorIA
            ia = OptimizadorIA(log_path=self.log_path, min_samples=self.config.get('min_samples_optimizacion', 15))
            nuevos_parametros = ia.buscar_mejores_parametros()
            if nuevos_parametros:
                self.config['trend_threshold_degrees'] = nuevos_parametros.get('trend_threshold_degrees', self.config.get('trend_threshold_degrees', 13))
                self.config['min_trend_strength_degrees'] = nuevos_parametros.get('min_trend_strength_degrees', self.config.get('min_trend_strength_degrees', 16))
                self.config['entry_margin'] = nuevos_parametros.get('entry_margin', self.config.get('entry_margin', 0.001))
                logger.info("✅ Parámetros reoptimizados")
        except Exception as e:
            logger.error(f"⚠ Error en reoptimización: {e}")

    def ejecutar_analisis(self):
        """Ejecuta el análisis completo del bot"""
        try:
            if random.random() < 0.1:
                self.reoptimizar_periodicamente()
            self.verificar_envio_reporte_automatico()
            cierres = self.verificar_cierre_operaciones()
            if cierres:
                logger.info(f" 📊 Operaciones cerradas: {', '.join(cierres)}")
            self.guardar_estado()
            return self.escanear_mercado()
        except Exception as e:
            logger.error(f" ❌ Error en ejecutar_analisis: {e}")
            return 0

    def iniciar(self):
        """Inicia el bot"""
        logger.info("=" * 70)
        logger.info("🤖 BOT DE TRADING - ESTRATEGIA BREAKOUT + REENTRY")
        logger.info("🎯 PRIORIDAD: TIMEFRAMES CORTOS (1m > 3m > 5m > 15m > 30m)")
        logger.info("💾 PERSISTENCIA: ACTIVADA")
        logger.info("🔄 REEVALUACIÓN: CADA 2 HORAS")
        logger.info("🏦 INTEGRACIÓN: BITGET API")
        logger.info("=" * 70)
        logger.info(f"💱 Símbolos: {len(self.config.get('symbols', []))} monedas")
        if self.bitget_client:
            logger.info("🤖 BITGET: ✅ Conectado")
        else:
            logger.info("🤖 BITGET: ❌ No configurado (solo señales)")
        logger.info("=" * 70)
        logger.info("🚀 INICIANDO BOT...")

        try:
            while True:
                nuevas_senales = self.ejecutar_analisis()
                self.mostrar_resumen_operaciones()
                minutos_espera = self.config.get('scan_interval_minutes', 1)
                logger.info(f"✅ Análisis completado. Señales nuevas: {nuevas_senales}")
                logger.info(f"⏳ Próximo análisis en {minutos_espera} minutos...")
                logger.info("-" * 60)
                for minuto in range(minutos_espera):
                    time.sleep(60)
                    restantes = minutos_espera - (minuto + 1)
                    if restantes > 0 and restantes % 5 == 0:
                        logger.info(f" ⏰ {restantes} minutos restantes...")
        except KeyboardInterrupt:
            logger.info("🛑 Bot detenido por el usuario")
        except Exception as e:
            logger.error(f" ❌ Error en el hilo del bot: {e}")
            time.sleep(60)

    def _enviar_telegram_simple(self, mensaje, token, chat_ids):
        """Envía un mensaje simple a Telegram"""
        if not token or not chat_ids:
            return False
        resultados = []
        for chat_id in chat_ids:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            payload = {
                'chat_id': chat_id,
                'text': mensaje,
                'parse_mode': 'HTML'
            }
            try:
                r = requests.post(url, data=payload, timeout=120)
                if r.status_code == 200:
                    resultados.append(True)
                else:
                    logger.error(f" ❌ Error enviando mensaje a {chat_id}: {r.status_code} - {r.text}")
                    resultados.append(False)
            except Exception as e:
                logger.error(f" ❌ Error enviando mensaje a {chat_id}: {e}")
                resultados.append(False)
        return all(resultados)

    def enviar_grafico_telegram(self, buf, token, chat_ids):
        """Envía un gráfico a Telegram"""
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
                else:
                    logger.error(f" ❌ Error enviando gráfico a {chat_id}: {r.status_code} - {r.text}")
            except Exception as e:
                logger.error(f" ❌ Error enviando gráfico a {chat_id}: {e}")
        return exito

# Configuración de Flask
app = Flask(__name__)

# Iniciar bot en un hilo separado
def run_bot_loop():
    try:
        bot = BotBreakoutReentry()
        bot.iniciar()
    except Exception as e:
        logger.error(f"Error en el hilo del bot: {e}", file=sys.stderr)
        time.sleep(60)

# Iniciar hilo del bot
bot_thread = threading.Thread(target=run_bot_loop, daemon=True)
bot_thread.start()

@app.route('/')
def index():
    return "Bot Breakout + Reentry con integración Bitget está en línea.", 200

@app.route('/webhook', methods=['POST'])
def telegram_webhook():
    if request.is_json:
        update = request.get_json()
        logger.info(f"Update recibido: {json.dumps(update)}")
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
        logger.error(f"Error configurando webhook: {e}", file=sys.stderr)

if __name__ == '__main__':
    setup_telegram_webhook()
    app.run(debug=True, port=5000)
