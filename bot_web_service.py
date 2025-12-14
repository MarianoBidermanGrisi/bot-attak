

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
# Copiado íntegro de Pasted_Text_1763228298547.txt y corregido para Render
# ---------------------------

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
import mplfinance as mpf
import pandas as pd
from io import BytesIO

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
        """Generar firma HMAC-SHA256 para Bitget V2"""
        try:
            if isinstance(body, dict):
                body_str = json.dumps(body, separators=(',', ':')) if body else ''
            else:
                body_str = str(body) if body else ''
            
            message = timestamp + method.upper() + request_path + body_str
            
            mac = hmac.new(
                bytes(self.api_secret, 'utf-8'),
                bytes(message, 'utf-8'),
                digestmod=hashlib.sha256
            )
            
            signature = base64.b64encode(mac.digest()).decode()
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
                    
                    # Intentar con productType alternativo si falla
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
        """Obtener información del símbolo"""
        try:
            request_path = '/api/v2/mix/market/contracts'
            params = {'productType': 'USDT-FUTURES'}
            
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
        """Colocar orden de mercado o límite"""
        try:
            request_path = '/api/v2/mix/order/place-order'
            body = {
                'symbol': symbol,
                'productType': 'USDT-FUTURES',
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
        """Colocar orden de plan (TP/SL)"""
        try:
            request_path = '/api/v2/mix/order/place-plan-order'
            body = {
                'symbol': symbol,
                'productType': 'USDT-FUTURES',
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
        """Configurar apalancamiento"""
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

# ---------------------------
# FUNCIONES DE OPERACIONES BITGET
# ---------------------------
def ejecutar_operacion_bitget(bitget_client, simbolo, tipo_operacion, capital_usd, leverage=20):
    """
    Ejecutar una operación completa en Bitget (posición + TP/SL)
    
    Args:
        bitget_client: Instancia de BitgetClient
        simbolo: Símbolo de trading (ej: 'BTCUSDT')
        tipo_operacion: 'LONG' o 'SHORT'
        capital_usd: Capital a usar en USD
        leverage: Apalancamiento (default: 20)
    
    Returns:
        dict con información de la operación ejecutada
    """
    
    logger.info(f"🚀 EJECUTANDO OPERACIÓN REAL EN BITGET")
    logger.info(f"Símbolo: {simbolo}")
    logger.info(f"Tipo: {tipo_operacion}")
    logger.info(f"Apalancamiento: {leverage}x")
    logger.info(f"Capital: ${capital_usd}")
    
    try:
        # 1. Configurar apalancamiento
        hold_side = 'long' if tipo_operacion == 'LONG' else 'short'
        leverage_ok = bitget_client.set_leverage(simbolo, leverage, hold_side)
        if not leverage_ok:
            logger.error("Error configurando apalancamiento")
            return None
        time.sleep(0.5)
        
        # 2. Obtener precio actual
        klines = bitget_client.get_klines(simbolo, '1m', 1)
        if not klines or len(klines) == 0:
            logger.error(f"No se pudo obtener precio de {simbolo}")
            return None
        
        klines.reverse()  # Bitget devuelve en orden descendente
        precio_actual = float(klines[0][4])  # Precio de cierre de la última vela
        
        # 3. Obtener información del símbolo
        symbol_info = bitget_client.get_symbol_info(simbolo)
        if not symbol_info:
            logger.error(f"No se pudo obtener info de {simbolo}")
            return None
        
        # 4. Calcular tamaño de la posición
        size_multiplier = float(symbol_info.get('sizeMultiplier', 1))
        min_trade_num = float(symbol_info.get('minTradeNum', 1))
        
        # Calcular cantidad en USD
        cantidad_usd = capital_usd * leverage
        # Convertir a cantidad de contratos
        cantidad_contratos = cantidad_usd / precio_actual
        cantidad_contratos = round(cantidad_contratos / size_multiplier) * size_multiplier
        
        # Verificar mínimo
        if cantidad_contratos < min_trade_num:
            cantidad_contratos = min_trade_num
        
        logger.info(f"Cantidad: {cantidad_contratos} contratos")
        logger.info(f"Valor nocional: ${cantidad_contratos * precio_actual:.2f}")
        
        # 5. Calcular TP y SL (2% fijo)
        if tipo_operacion == "LONG":
            sl_porcentaje = 0.02
            tp_porcentaje = 0.04  # TP doble del SL (RR 2:1)
            stop_loss = precio_actual * (1 - sl_porcentaje)
            take_profit = precio_actual * (1 + tp_porcentaje)
        else:
            sl_porcentaje = 0.02
            tp_porcentaje = 0.04
            stop_loss = precio_actual * (1 + sl_porcentaje)
            take_profit = precio_actual * (1 - tp_porcentaje)
        
        # 6. Abrir posición
        side = 'open_long' if tipo_operacion == 'LONG' else 'open_short'
        orden_entrada = bitget_client.place_order(
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
        
        # 7. Colocar Stop Loss
        sl_side = 'close_long' if tipo_operacion == 'LONG' else 'close_short'
        orden_sl = bitget_client.place_plan_order(
            symbol=simbolo,
            side=sl_side,
            trigger_price=stop_loss,
            order_type='market',
            size=cantidad_contratos,
            plan_type='loss_plan'
        )
        
        if orden_sl:
            logger.info(f"✓ Stop Loss configurado en: {stop_loss:.8f}")
        else:
            logger.warning("Error configurando Stop Loss")
        
        time.sleep(0.5)
        
        # 8. Colocar Take Profit
        orden_tp = bitget_client.place_plan_order(
            symbol=simbolo,
            side=sl_side,
            trigger_price=take_profit,
            order_type='market',
            size=cantidad_contratos,
            plan_type='normal_plan'
        )
        
        if orden_tp:
            logger.info(f"✓ Take Profit configurado en: {take_profit:.8f}")
        else:
            logger.warning("Error configurando Take Profit")
        
        # 9. Retornar información de la operación
        operacion_data = {
            'orden_entrada': orden_entrada,
            'orden_sl': orden_sl,
            'orden_tp': orden_tp,
            'cantidad_contratos': cantidad_contratos,
            'precio_entrada': precio_actual,
            'take_profit': take_profit,
            'stop_loss': stop_loss,
            'leverage': leverage,
            'capital_usado': capital_usd,
            'tipo': tipo_operacion,
            'timestamp_entrada': datetime.now().isoformat(),
            'symbol': simbolo
        }
        
        logger.info(f"✅ OPERACIÓN EJECUTADA EXITOSAMENTE")
        logger.info(f"ID Orden: {orden_entrada.get('orderId', 'N/A')}")
        logger.info(f"Contratos: {cantidad_contratos}")
        logger.info(f"Entrada: {precio_actual:.8f}")
        logger.info(f"SL: {stop_loss:.8f} (-2%)")
        logger.info(f"TP: {take_profit:.8f}")
        
        return operacion_data
        
    except Exception as e:
        logger.error(f"Error ejecutando operación: {e}")
        return None

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
            print("💾 Estado guardado correctamente")
        except Exception as e:
            print(f"⚠ Error guardando estado: {e}")

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
            print(f"   ✅ Config óptima: {mejor_config['timeframe']} - {mejor_config['num_velas']} velas - Ancho: {mejor_config['ancho_canal']:.1f}%")
        return mejor_config

    def obtener_datos_mercado_config(self, simbolo, timeframe, num_velas):
        """Obtiene datos con configuración específica usando API de Bitget"""
        # Usar API de Bitget en lugar de Binance
        if self.bitget_client:
            try:
                candles = self.bitget_client.get_klines(simbolo, timeframe, num_velas + 14)
                if not candles or len(candles) == 0:
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
                print(f"   ⚠️ Error obteniendo datos de Bitget para {simbolo}: {e}")
                # Fallback a Binance si falla Bitget
                pass
        
        # Fallback a Binance API (código original)
        try:
            url = "https://api.binance.com/api/v3/klines"
            params = {'symbol': simbolo, 'interval': timeframe, 'limit': num_velas + 14}
            respuesta = requests.get(url, params=params, timeout=10)
            datos = respuesta.json()
            if not isinstance(datos, list) or len(datos) == 0:
                return None
            maximos = [float(vela[2]) for vela in datos]
            minimos = [float(vela[3]) for vela in datos]
            cierres = [float(vela[4]) for vela in datos]
            tiempos = list(range(len(datos)))
            return {
                'maximos': maximos,
                'minimos': minimos,
                'cierres': cierres,
                'tiempos': tiempos,
                'precio_actual': cierres[-1] if cierres else 0,
                'timeframe': timeframe,
                'num_velas': num_velas
            }
        except Exception:
            return None

    def calcular_canal_regresion_config(self, datos_mercado, candle_period):
        """Calcula canal de regresión"""
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

    def enviar_alerta_breakout(self, simbolo, tipo_breakout, info_canal, datos_mercado, config_optima):
        """
        Envía alerta de BREAKOUT detectado a Telegram con gráfico
        LÓGICA CORREGIDA:
        - BREAKOUT_LONG → Ruptura de resistencia en canal BAJISTA (oportunidad de reversión alcista)
        - BREAKOUT_SHORT → Ruptura de soporte en canal ALCISTA (oportunidad de reversión bajista)
        """
        precio_cierre = datos_mercado['cierres'][-1]
        resistencia = info_canal['resistencia']
        soporte = info_canal['soporte']
        direccion_canal = info_canal['direccion']
        # Determinar tipo de ruptura CORREGIDO SEGÚN TU ESTRATEGIA
        if tipo_breakout == "BREAKOUT_LONG":
            # Para un LONG, nos interesa la ruptura del SOPORTE hacia arriba
            emoji_principal = "🚀"
            tipo_texto = "RUPTURA de SOPORTE"
            nivel_roto = f"Soporte: {soporte:.8f}"
            direccion_emoji = "⬇️"
            contexto = f"Canal {direccion_canal} → Ruptura de SOPORTE"
            expectativa = "posible entrada en long si el precio reingresa al canal"
        else:  # BREAKOUT_SHORT
            # Para un SHORT, nos interesa la ruptura de la RESISTENCIA hacia abajo
            emoji_principal = "📉"
            tipo_texto = "RUPTURA BAJISTA de RESISTENCIA"
            nivel_roto = f"Resistencia: {resistencia:.8f}"
            direccion_emoji = "⬆️"
            contexto = f"Canal {direccion_canal} → Rechazo desde RESISTENCIA"
            expectativa = "posible entrada en sort si el precio reingresa al canal"
        # Mensaje de alerta
        mensaje = f"""
{emoji_principal} <b>¡BREAKOUT DETECTADO! - {simbolo}</b>
⚠️ <b>{tipo_texto}</b> {direccion_emoji}
⏰ <b>Hora:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
⏳ <b>ESPERANDO REINGRESO...</b>
👁️ Máximo 30 minutos para confirmación
📍 {expectativa}
        """
        token = self.config.get('telegram_token')
        chat_ids = self.config.get('telegram_chat_ids', [])
        if token and chat_ids:
            try:
                print(f"     📊 Generando gráfico de breakout para {simbolo}...")
                buf = self.generar_grafico_breakout(simbolo, info_canal, datos_mercado, tipo_breakout, config_optima)
                if buf:
                    print(f"     📨 Enviando alerta de breakout por Telegram...")
                    self.enviar_grafico_telegram(buf, token, chat_ids)
                    time.sleep(0.5)
                    self._enviar_telegram_simple(mensaje, token, chat_ids)
                    print(f"     ✅ Alerta de breakout enviada para {simbolo}")
                else:
                    self._enviar_telegram_simple(mensaje, token, chat_ids)
                    print(f"     ⚠️ Alerta enviada sin gráfico")
            except Exception as e:
                print(f"     ❌ Error enviando alerta de breakout: {e}")
        else:
            print(f"     📢 Breakout detectado en {simbolo} (sin Telegram)")

    def generar_grafico_breakout(self, simbolo, info_canal, datos_mercado, tipo_breakout, config_optima):
        """
        Genera gráfico especial para el momento del BREAKOUT
        Marca visualmente la ruptura del canal
        """
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
            
            # Calcular líneas del canal
            tiempos_reg = list(range(len(df)))
            resistencia_values = []
            soporte_values = []
            for i, t in enumerate(tiempos_reg):
                resist = info_canal['pendiente_resistencia'] * t + \
                        (info_canal['resistencia'] - info_canal['pendiente_resistencia'] * tiempos_reg[-1])
                resist_values.append(resist)
                soporte = info_canal['pendiente_soporte'] * t + \
                         (info_canal['soporte'] - info_canal['pendiente_soporte'] * tiempos_reg[-1])
                soporte_values.append(soporte)
            
            # Configurar gráfico
            fig, axes = plt.subplots(2, 1, gridspec_kw={'height_ratios': [3, 1]}, figsize=(12, 8))
            fig.suptitle(f'BREAKOUT DETECTADO - {simbolo} ({config_optima["timeframe"]})', fontsize=14, fontweight='bold')
            
            # Gráfico de precios
            mpf.plot(df, type='candle', ax=axes[0], volume=False, style='yahoo')
            axes[0].plot(df.index, resistencia_values, 'r--', label='Resistencia')
            axes[0].plot(df.index, soporte_values, 'g--', label='Soporte')
            
            # Marcar breakout
            if tipo_breakout == "BREAKOUT_LONG":
                axes[0].scatter(df.index[-1], df['Close'][-1], color='green', marker='^', s=100, label='Breakout Long')
            else:
                axes[0].scatter(df.index[-1], df['Close'][-1], color='red', marker='v', s=100, label='Breakout Short')
            
            axes[0].set_title(f'Precio - {simbolo}')
            axes[0].legend()
            
            # Gráfico de estocástico
            axes[1].plot(df.index, [info_canal['stoch_k']] * len(df), 'b-', label='Stoch K')
            axes[1].plot(df.index, [info_canal['stoch_d']] * len(df), 'r-', label='Stoch D')
            axes[1].axhline(y=80, color='r', linestyle='--', alpha=0.5)
            axes[1].axhline(y=20, color='g', linestyle='--', alpha=0.5)
            axes[1].set_title('Estocástico')
            axes[1].set_ylim(0, 100)
            axes[1].legend()
            
            plt.tight_layout()
            
            # Guardar en buffer
            buf = BytesIO()
            plt.savefig(buf, format='png', dpi=100)
            buf.seek(0)
            plt.close()
            
            return buf
        except Exception as e:
            print(f"Error generando gráfico de breakout: {e}")
            return None

    def _enviar_telegram_simple(self, mensaje, token, chat_ids):
        """Envía un mensaje simple a Telegram"""
        for chat_id in chat_ids:
            try:
                url = f"https://api.telegram.org/bot{token}/sendMessage"
                payload = {
                    'chat_id': chat_id,
                    'text': mensaje,
                    'parse_mode': 'HTML'
                }
                requests.post(url, json=payload, timeout=10)
            except Exception as e:
                print(f"Error enviando mensaje a Telegram: {e}")

    def enviar_grafico_telegram(self, buf, token, chat_ids):
        """Envía un gráfico a Telegram"""
        for chat_id in chat_ids:
            try:
                url = f"https://api.telegram.org/bot{token}/sendPhoto"
                files = {'photo': ('chart.png', buf, 'image/png')}
                data = {'chat_id': chat_id}
                requests.post(url, files=files, data=data, timeout=10)
            except Exception as e:
                print(f"Error enviando gráfico a Telegram: {e}")

    def calcular_regresion_lineal(self, x, y):
        """Calcula regresión lineal simple"""
        try:
            if len(x) != len(y) or len(x) == 0:
                return None
            x_array = np.array(x)
            y_array = np.array(y)
            n = len(x)
            sum_x = np.sum(x_array)
            sum_y = np.sum(y_array)
            sum_xy = np.sum(x_array * y_array)
            sum_x2 = np.sum(x_array ** 2)
            
            # Calcular pendiente e intercepto
            denominator = n * sum_x2 - sum_x ** 2
            if denominator == 0:
                return None
            
            pendiente = (n * sum_xy - sum_x * sum_y) / denominator
            intercepto = (sum_y - pendiente * sum_x) / n
            
            return (pendiente, intercepto)
        except Exception:
            return None

    def calcular_pearson_y_angulo(self, x, y):
        """Calcula coeficiente de Pearson y ángulo de tendencia"""
        try:
            if len(x) != len(y) or len(x) < 2:
                return (0, 0)
            
            # Calcular coeficiente de Pearson
            x_array = np.array(x)
            y_array = np.array(y)
            correlation_matrix = np.corrcoef(x_array, y_array)
            pearson = correlation_matrix[0, 1]
            
            # Calcular ángulo de tendencia en grados
            if len(x) >= 2:
                delta_x = x[-1] - x[0]
                delta_y = y[-1] - y[0]
                if delta_x != 0:
                    angulo_rad = math.atan(delta_y / delta_x)
                    angulo_deg = math.degrees(angulo_rad)
                else:
                    angulo_deg = 90 if delta_y > 0 else -90
            else:
                angulo_deg = 0
            
            return (pearson, angulo_deg)
        except Exception:
            return (0, 0)

    def clasificar_fuerza_tendencia(self, angulo):
        """Clasifica la fuerza de la tendencia según el ángulo"""
        try:
            abs_angulo = abs(angulo)
            if abs_angulo < 5:
                return ("Sin tendencia clara", 1)
            elif abs_angulo < 10:
                return ("Tendencia muy débil", 2)
            elif abs_angulo < 15:
                return ("Tendencia débil", 3)
            elif abs_angulo < 20:
                return ("Tendencia moderada", 4)
            elif abs_angulo < 30:
                return ("Tendencia fuerte", 5)
            else:
                return ("Tendencia muy fuerte", 6)
        except Exception:
            return ("Error", 0)

    def determinar_direccion_tendencia(self, angulo, umbral=0):
        """Determina dirección de la tendencia"""
        try:
            if angulo > umbral:
                return "alcista"
            elif angulo < -umbral:
                return "bajista"
            else:
                return "lateral"
        except Exception:
            return "desconocida"

    def calcular_r2(self, y_real, x, pendiente, intercepto):
        """Calcula coeficiente R²"""
        try:
            if len(y_real) != len(x) or len(y_real) == 0:
                return 0
            
            y_pred = [pendiente * xi + intercepto for xi in x]
            y_mean = np.mean(y_real)
            
            ss_res = sum((y_real[i] - y_pred[i]) ** 2 for i in range(len(y_real)))
            ss_tot = sum((y_real[i] - y_mean) ** 2 for i in range(len(y_real)))
            
            if ss_tot == 0:
                return 0
            
            r2 = 1 - (ss_res / ss_tot)
            return r2
        except Exception:
            return 0

    def calcular_stochastic(self, datos_mercado, k_period=14, d_period=3):
        """Calcula indicador estocástico"""
        try:
            if not datos_mercado or len(datos_mercado['maximos']) < k_period:
                return (50, 50)
            
            maximos = datos_mercado['maximos'][-k_period:]
            minimos = datos_mercado['minimos'][-k_period:]
            cierres = datos_mercado['cierres'][-k_period:]
            
            highest_high = max(maximos)
            lowest_low = min(minimos)
            
            if highest_high == lowest_low:
                return (50, 50)
            
            k_percent = 100 * ((cierres[-1] - lowest_low) / (highest_high - lowest_low))
            
            # Para simplificar, usamos un valor fijo para D
            d_percent = 50  # Valor por defecto
            
            return (k_percent, d_percent)
        except Exception:
            return (50, 50)

    def inicializar_log(self):
        """Inicializa archivo de log"""
        try:
            if not os.path.exists(self.archivo_log):
                with open(self.archivo_log, 'w', encoding='utf-8') as f:
                    f.write("timestamp,simbolo,tipo_operacion,precio_entrada,precio_salida,pnl,pnl_percent,angulo_tendencia,pearson,r2_score,ancho_canal_relativo,nivel_fuerza\n")
        except Exception as e:
            print(f"Error inicializando log: {e}")

    def registrar_operacion(self, simbolo, tipo_operacion, precio_entrada, precio_salida=None, 
                           canal_info=None, timestamp=None):
        """Registra operación en log"""
        try:
            if not timestamp:
                timestamp = datetime.now().isoformat()
            
            pnl = 0
            pnl_percent = 0
            if precio_salida and precio_entrada:
                if tipo_operacion == "LONG":
                    pnl = precio_salida - precio_entrada
                    pnl_percent = (pnl / precio_entrada) * 100
                else:
                    pnl = precio_entrada - precio_salida
                    pnl_percent = (pnl / precio_entrada) * 100
            
            angulo_tendencia = canal_info.get('angulo_tendencia', 0) if canal_info else 0
            pearson = canal_info.get('coeficiente_pearson', 0) if canal_info else 0
            r2_score = canal_info.get('r2_score', 0) if canal_info else 0
            ancho_canal_relativo = canal_info.get('ancho_canal_porcentual', 0) if canal_info else 0
            nivel_fuerza = canal_info.get('nivel_fuerza', 1) if canal_info else 1
            
            with open(self.archivo_log, 'a', encoding='utf-8') as f:
                f.write(f"{timestamp},{simbolo},{tipo_operacion},{precio_entrada},{precio_salida},{pnl},{pnl_percent},{angulo_tendencia},{pearson},{r2_score},{ancho_canal_relativo},{nivel_fuerza}\n")
            
            self.operaciones_desde_optimizacion += 1
            self.total_operaciones += 1
            
            # Guardar estado después de registrar operación
            self.guardar_estado()
            
            return True
        except Exception as e:
            print(f"Error registrando operación: {e}")
            return False

    def verificar_reentry(self, simbolo, tipo_breakout, precio_breakout, config_optima):
        """
        Verifica si se ha producido un reingreso al canal después de un breakout
        """
        try:
            # Obtener datos actualizados
            datos_actualizados = self.obtener_datos_mercado_config(simbolo, config_optima['timeframe'], config_optima['num_velas'])
            if not datos_actualizados:
                return False
            
            # Calcular canal actualizado
            canal_actualizado = self.calcular_canal_regresion_config(datos_actualizados, config_optima['num_velas'])
            if not canal_actualizado:
                return False
            
            precio_actual = datos_actualizados['cierres'][-1]
            
            # Verificar condiciones de reingreso
            if tipo_breakout == "BREAKOUT_LONG":
                # Para LONG: precio estaba por encima de resistencia y ahora vuelve a entrar al canal
                if precio_actual < canal_actualizado['resistencia'] and precio_actual > canal_actualizado['soporte']:
                    # Confirmación con estocástico
                    stoch_k, stoch_d = canal_actualizado['stoch_k'], canal_actualizado['stoch_d']
                    if stoch_k < 80 and stoch_d < 80:  # No sobrecomprado
                        return True
            else:  # BREAKOUT_SHORT
                # Para SHORT: precio estaba por debajo de soporte y ahora vuelve a entrar al canal
                if precio_actual > canal_actualizado['soporte'] and precio_actual < canal_actualizado['resistencia']:
                    # Confirmación con estocástico
                    stoch_k, stoch_d = canal_actualizado['stoch_k'], canal_actualizado['stoch_d']
                    if stoch_k > 20 and stoch_d > 20:  # No sobrevendido
                        return True
            
            return False
        except Exception as e:
            print(f"Error verificando reentry para {simbolo}: {e}")
            return False

    def ejecutar_operacion(self, simbolo, tipo_operacion, canal_info):
        """
        Ejecuta operación real si está configurado para hacerlo
        """
        if not self.ejecutar_operaciones_automaticas or not self.bitget_client:
            print(f"   📢 Señal de {tipo_operacion} para {simbolo} (operación no ejecutada)")
            return None
        
        try:
            operacion = ejecutar_operacion_bitget(
                self.bitget_client,
                simbolo,
                tipo_operacion,
                self.capital_por_operacion,
                self.leverage_por_defecto
            )
            
            if operacion:
                # Registrar operación
                self.registrar_operacion(
                    simbolo=simbolo,
                    tipo_operacion=tipo_operacion,
                    precio_entrada=operacion['precio_entrada'],
                    canal_info=canal_info
                )
                
                # Enviar notificación a Telegram
                mensaje = f"""
🚀 <b>OPERACIÓN EJECUTADA - {simbolo}</b>
💹 <b>Tipo:</b> {tipo_operacion}
💰 <b>Capital:</b> ${self.capital_por_operacion}
📊 <b>Apalancamiento:</b> {self.leverage_por_defecto}x
💵 <b>Precio entrada:</b> {operacion['precio_entrada']:.8f}
🎯 <b>Take Profit:</b> {operacion['take_profit']:.8f}
🛑 <b>Stop Loss:</b> {operacion['stop_loss']:.8f}
📈 <b>Contratos:</b> {operacion['cantidad_contratos']}
⏰ <b>Hora:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
                """
                
                token = self.config.get('telegram_token')
                chat_ids = self.config.get('telegram_chat_ids', [])
                if token and chat_ids:
                    self._enviar_telegram_simple(mensaje, token, chat_ids)
                
                return operacion
            else:
                print(f"   ❌ Error ejecutando operación para {simbolo}")
                return None
        except Exception as e:
            print(f"   ❌ Error ejecutando operación para {simbolo}: {e}")
            return None

    def procesar_breakout(self, simbolo, tipo_breakout, info_canal, datos_mercado, config_optima):
        """
        Procesa un breakout detectado
        """
        try:
            # Registrar breakout detectado
            self.breakouts_detectados[simbolo] = {
                'tipo': tipo_breakout,
                'timestamp': datetime.now(),
                'precio_breakout': datos_mercado['cierres'][-1]
            }
            
            # Iniciar espera de reentry
            self.esperando_reentry[simbolo] = {
                'tipo': tipo_breakout,
                'timestamp': datetime.now(),
                'precio_breakout': datos_mercado['cierres'][-1],
                'config': config_optima
            }
            
            # Enviar alerta de breakout
            self.enviar_alerta_breakout(simbolo, tipo_breakout, info_canal, datos_mercado, config_optima)
            
            # Guardar estado
            self.guardar_estado()
            
            return True
        except Exception as e:
            print(f"Error procesando breakout para {simbolo}: {e}")
            return False

    def verificar_y_procesar_reentry(self):
        """
        Verifica si hay reingresos pendientes y los procesa
        """
        try:
            simbolos_procesar = list(self.esperando_reentry.keys())
            for simbolo in simbolos_procesar:
                if simbolo not in self.esperando_reentry:
                    continue
                
                info_reentry = self.esperando_reentry[simbolo]
                tiempo_transcurrido = (datetime.now() - info_reentry['timestamp']).total_seconds() / 60  # minutos
                
                # Si han pasado más de 30 minutos, eliminar
                if tiempo_transcurrido > 30:
                    print(f"   ⏰ Tiempo de espera expirado para {simbolo} ({tiempo_transcurrido:.1f} min)")
                    del self.esperando_reentry[simbolo]
                    continue
                
                # Verificar si hay reingreso
                if self.verificar_reentry(simbolo, info_reentry['tipo'], info_reentry['precio_breakout'], info_reentry['config']):
                    print(f"   ✅ REENTRY CONFIRMADO para {simbolo} después de {tiempo_transcurrido:.1f} min")
                    
                    # Ejecutar operación
                    tipo_operacion = "LONG" if info_reentry['tipo'] == "BREAKOUT_LONG" else "SHORT"
                    self.ejecutar_operacion(simbolo, tipo_operacion, info_reentry['config'])
                    
                    # Eliminar de espera
                    del self.esperando_reentry[simbolo]
                    
                    # Enviar notificación de reentry
                    mensaje = f"""
✅ <b>REENTRY CONFIRMADO - {simbolo}</b>
🔄 <b>Tipo:</b> {tipo_operacion}
⏱️ <b>Tiempo desde breakout:</b> {tiempo_transcurrido:.1f} min
⏰ <b>Hora:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
                    """
                    
                    token = self.config.get('telegram_token')
                    chat_ids = self.config.get('telegram_chat_ids', [])
                    if token and chat_ids:
                        self._enviar_telegram_simple(mensaje, token, chat_ids)
            
            # Guardar estado después de procesar
            self.guardar_estado()
        except Exception as e:
            print(f"Error verificando y procesando reentry: {e}")

    def analizar_mercado(self, simbolo):
        """
        Analiza un símbolo en busca de breakouts
        """
        try:
            # Verificar si ya hay un breakout detectado recientemente
            if simbolo in self.breakouts_detectados:
                tiempo_ultimo_breakout = (datetime.now() - self.breakouts_detectados[simbolo]['timestamp']).total_seconds() / 60  # minutos
                if tiempo_ultimo_breakout < 60:  # Si ha pasado menos de 1 hora, omitir
                    return None
            
            # Buscar configuración óptima
            config_optima = self.buscar_configuracion_optima_simbolo(simbolo)
            if not config_optima:
                return None
            
            # Obtener datos de mercado
            datos_mercado = self.obtener_datos_mercado_config(simbolo, config_optima['timeframe'], config_optima['num_velas'])
            if not datos_mercado:
                return None
            
            # Calcular canal de regresión
            canal_info = self.calcular_canal_regresion_config(datos_mercado, config_optima['num_velas'])
            if not canal_info:
                return None
            
            # Verificar condiciones de breakout
            precio_actual = datos_mercado['cierres'][-1]
            resistencia = canal_info['resistencia']
            soporte = canal_info['soporte']
            direccion_canal = canal_info['direccion']
            
            # Verificar fuerza del canal
            if canal_info['nivel_fuerza'] < 2 or abs(canal_info['coeficiente_pearson']) < 0.4 or canal_info['r2_score'] < 0.4:
                return None
            
            # Verificar ancho del canal
            if canal_info['ancho_canal_porcentual'] < self.config.get('min_channel_width_percent', 4.0):
                return None
            
            # Detectar breakout
            breakout_detectado = None
            
            # Breakout alcista (ruptura de resistencia en canal bajista)
            if direccion_canal == "bajista" and precio_actual > resistencia:
                # Confirmación con estocástico
                stoch_k, stoch_d = canal_info['stoch_k'], canal_info['stoch_d']
                if stoch_k > 80 and stoch_d > 80:  # Sobrecomprado
                    breakout_detectado = "BREAKOUT_LONG"
            
            # Breakout bajista (ruptura de soporte en canal alcista)
            elif direccion_canal == "alcista" and precio_actual < soporte:
                # Confirmación con estocástico
                stoch_k, stoch_d = canal_info['stoch_k'], canal_info['stoch_d']
                if stoch_k < 20 and stoch_d < 20:  # Sobrevendido
                    breakout_detectado = "BREAKOUT_SHORT"
            
            # Procesar breakout detectado
            if breakout_detectado:
                print(f"   🔍 Breakout detectado: {breakout_detectado} para {simbolo}")
                self.procesar_breakout(simbolo, breakout_detectado, canal_info, datos_mercado, config_optima)
                return breakout_detectado
            
            return None
        except Exception as e:
            print(f"Error analizando mercado para {simbolo}: {e}")
            return None

    def generar_reporte_semanal(self):
        """
        Genera reporte semanal de operaciones
        """
        try:
            # Leer operaciones de la semana
            ahora = datetime.now()
            semana_pasada = ahora - timedelta(days=7)
            
            operaciones_semana = []
            try:
                with open(self.archivo_log, 'r', encoding='utf-8') as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        try:
                            timestamp = datetime.fromisoformat(row['timestamp'])
                            if timestamp >= semana_pasada:
                                operaciones_semana.append(row)
                        except Exception:
                            continue
            except FileNotFoundError:
                print("⚠ No se encontró archivo de log para reporte semanal")
                return
            
            if not operaciones_semana:
                print("ℹ️ No hay operaciones esta semana para reporte")
                return
            
            # Calcular estadísticas
            total_operaciones = len(operaciones_semana)
            operaciones_ganadoras = sum(1 for op in operaciones_semana if float(op['pnl_percent']) > 0)
            operaciones_perdedoras = total_operaciones - operaciones_ganadoras
            winrate = (operaciones_ganadoras / total_operaciones) * 100 if total_operaciones > 0 else 0
            
            pnl_total = sum(float(op['pnl_percent']) for op in operaciones_semana)
            pnl_promedio = pnl_total / total_operaciones if total_operaciones > 0 else 0
            
            # Agrupar por símbolo
            operaciones_por_simbolo = {}
            for op in operaciones_semana:
                simbolo = op['simbolo']
                if simbolo not in operaciones_por_simbolo:
                    operaciones_por_simbolo[simbolo] = []
                operaciones_por_simbolo[simbolo].append(op)
            
            # Generar mensaje
            mensaje = f"""
📊 <b>REPORTE SEMANAL DE OPERACIONES</b>
📅 <b>Período:</b> {semana_pasada.strftime('%Y-%m-%d')} al {ahora.strftime('%Y-%m-%d')}
📈 <b>Total operaciones:</b> {total_operaciones}
✅ <b>Operaciones ganadoras:</b> {operaciones_ganadoras} ({winrate:.1f}%)
❌ <b>Operaciones perdedoras:</b> {operaciones_perdedoras}
💰 <b>PnL total:</b> {pnl_total:.2f}%
💹 <b>PnL promedio:</b> {pnl_promedio:.2f}%

<b>Resumen por símbolo:</b>
            """
            
            for simbolo, ops in operaciones_por_simbolo.items():
                ops_simbolo = len(ops)
                ganadoras_simbolo = sum(1 for op in ops if float(op['pnl_percent']) > 0)
                pnl_simbolo = sum(float(op['pnl_percent']) for op in ops)
                winrate_simbolo = (ganadoras_simbolo / ops_simbolo) * 100 if ops_simbolo > 0 else 0
                
                mensaje += f"\n• {simbolo}: {ops_simbolo} ops, {ganadoras_simbolo} ganadoras ({winrate_simbolo:.1f}%), PnL: {pnl_simbolo:.2f}%"
            
            # Enviar reporte a Telegram
            token = self.config.get('telegram_token')
            chat_ids = self.config.get('telegram_chat_ids', [])
            if token and chat_ids:
                self._enviar_telegram_simple(mensaje, token, chat_ids)
                print("✅ Reporte semanal enviado a Telegram")
            else:
                print("⚠️ No se pudo enviar reporte semanal (falta configuración de Telegram)")
        except Exception as e:
            print(f"Error generando reporte semanal: {e}")

    def iniciar_bot(self):
        """
        Inicia el bot de trading
        """
        try:
            print("🚀 Iniciando bot de trading...")
            print(f"   📊 Símbolos a monitorear: {self.config.get('simbolos', [])}")
            print(f"   🔄 Auto-optimización: {'Activada' if self.auto_optimize else 'Desactivada'}")
            print(f"   💰 Operaciones automáticas: {'Activadas' if self.ejecutar_operaciones_automaticas else 'Desactivadas'}")
            
            # Verificar cliente Bitget
            if self.bitget_client:
                if self.bitget_client.verificar_credenciales():
                    print("✅ Cliente Bitget verificado correctamente")
                else:
                    print("⚠️ No se pudo verificar el cliente Bitget")
            
            # Bucle principal
            while True:
                try:
                    # Verificar y procesar reingresos
                    self.verificar_y_procesar_reentry()
                    
                    # Analizar mercado para cada símbolo
                    for simbolo in self.config.get('simbolos', []):
                        self.analizar_mercado(simbolo)
                        time.sleep(1)  # Pequeña pausa entre símbolos
                    
                    # Verificar si es hora de generar reporte semanal (domingo a las 23:00)
                    ahora = datetime.now()
                    if ahora.weekday() == 6 and ahora.hour == 23 and ahora.minute == 0:
                        self.generar_reporte_semanal()
                    
                    # Verificar si es hora de optimizar parámetros (cada 24 horas)
                    if self.auto_optimize and (ahora - self.ultima_optimizacion).total_seconds() > 86400:
                        print("🔄 Iniciando optimización automática de parámetros...")
                        ia = OptimizadorIA(log_path=self.log_path, min_samples=self.config.get('min_samples_optimizacion', 15))
                        parametros_optimizados = ia.buscar_mejores_parametros()
                        
                        if parametros_optimizados:
                            self.config['trend_threshold_degrees'] = parametros_optimizados.get('trend_threshold_degrees', 
                                                                                             self.config.get('trend_threshold_degrees', 13))
                            self.config['min_trend_strength_degrees'] = parametros_optimizados.get('min_trend_strength_degrees', 
                                                                                               self.config.get('min_trend_strength_degrees', 16))
                            self.config['entry_margin'] = parametros_optimizados.get('entry_margin', 
                                                                                    self.config.get('entry_margin', 0.001))
                            self.ultima_optimizacion = ahora
                            self.operaciones_desde_optimizacion = 0
                            print("✅ Parámetros optimizados y actualizados")
                    
                    # Pausa entre ciclos
                    time.sleep(self.config.get('intervalo_analisis', 60))  # Default: 1 minuto
                    
                except KeyboardInterrupt:
                    print("\n⏹️ Bot detenido por el usuario")
                    self.guardar_estado()
                    break
                except Exception as e:
                    print(f"❌ Error en ciclo principal: {e}")
                    time.sleep(10)  # Pausa antes de reintentar
        except Exception as e:
            print(f"❌ Error iniciando bot: {e}")

# ---------------------------
# FLASK WEB SERVICE
# ---------------------------
app = Flask(__name__)

# Variables globales para el bot
bot = None
bot_thread = None

@app.route('/iniciar', methods=['POST'])
def iniciar_bot():
    """Inicia el bot de trading"""
    global bot, bot_thread
    
    try:
        # Obtener configuración del request
        config = request.json
        
        # Crear instancia del bot
        bot = TradingBot(config)
        
        # Iniciar bot en un hilo separado
        bot_thread = threading.Thread(target=bot.iniciar_bot)
        bot_thread.daemon = True
        bot_thread.start()
        
        return jsonify({
            'status': 'success',
            'message': 'Bot iniciado correctamente'
        })
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'Error iniciando bot: {str(e)}'
        }), 500

@app.route('/estado', methods=['GET'])
def obtener_estado():
    """Obtiene el estado actual del bot"""
    global bot
    
    if not bot:
        return jsonify({
            'status': 'error',
            'message': 'Bot no iniciado'
        }), 404
    
    try:
        estado = {
            'operaciones_activas': len(bot.operaciones_activas),
            'operaciones_desde_optimizacion': bot.operaciones_desde_optimizacion,
            'total_operaciones': bot.total_operaciones,
            'breakouts_detectados': len(bot.breakouts_detectados),
            'esperando_reentry': len(bot.esperando_reentry),
            'ultima_optimizacion': bot.ultima_optimizacion.isoformat() if bot.ultima_optimizacion else None,
            'config_optima_por_simbolo': bot.config_optima_por_simbolo
        }
        
        return jsonify({
            'status': 'success',
            'data': estado
        })
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'Error obteniendo estado: {str(e)}'
        }), 500

@app.route('/analizar', methods=['POST'])
def analizar_simbolo():
    """Analiza un símbolo específico"""
    global bot
    
    if not bot:
        return jsonify({
            'status': 'error',
            'message': 'Bot no iniciado'
        }), 404
    
    try:
        data = request.json
        simbolo = data.get('simbolo')
        
        if not simbolo:
            return jsonify({
                'status': 'error',
                'message': 'Se requiere el parámetro simbolo'
            }), 400
        
        resultado = bot.analizar_mercado(simbolo)
        
        return jsonify({
            'status': 'success',
            'data': {
                'simbolo': simbolo,
                'resultado': resultado
            }
        })
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'Error analizando símbolo: {str(e)}'
        }), 500

@app.route('/reporte', methods=['GET'])
def generar_reporte():
    """Genera un reporte de operaciones"""
    global bot
    
    if not bot:
        return jsonify({
            'status': 'error',
            'message': 'Bot no iniciado'
        }), 404
    
    try:
        bot.generar_reporte_semanal()
        
        return jsonify({
            'status': 'success',
            'message': 'Reporte generado correctamente'
        })
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'Error generando reporte: {str(e)}'
        }), 500

@app.route('/health', methods=['GET'])
def health_check():
    """Verifica el estado del servicio"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat()
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=False)
