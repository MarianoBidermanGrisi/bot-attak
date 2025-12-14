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
                    except Exception as e:
                        logger.error(f"Error procesando fila en OptimizadorIA.cargar_datos: {e}")
                        continue
        except FileNotFoundError:
            logger.warning("⚠ No se encontró operaciones_log.csv (optimizador)")
        except Exception as e:
            logger.error(f"Error inesperado en OptimizadorIA.cargar_datos: {e}")
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
            logger.info("✅ Optimizador: mejores parámetros encontrados:", mejores_param)
            try:
                with open("mejores_parametros.json", "w", encoding='utf-8') as f:
                    json.dump(mejores_param, f, indent=2)
            except Exception as e:
                logger.error(f"⚠ Error guardando mejores_parametros.json: {e}")
        else:
            logger.warning("⚠ No se encontró una configuración mejor")
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
                    error_msg = data.get('msg', 'Unknown error')
                    error_code = data.get('code', 'Unknown')
                    logger.error(f"Error en orden: {error_code} - {error_msg}")
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
                    logger.info(f"✓ Orden plan colocada: {data.get('data', {})}")
                    return data.get('data', {})
                else:
                    error_msg = data.get('msg', 'Unknown error')
                    error_code = data.get('code', 'Unknown')
                    logger.error(f"Error en orden plan: {error_code} - {error_msg}")
                    return None
            else:
                logger.error(f"Error HTTP en orden plan: {response.status_code} - {response.text}")
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
                else:
                    error_msg = data.get('msg', 'Unknown error')
                    error_code = data.get('code', 'Unknown')
                    logger.error(f"Error configurando leverage: {error_code} - {error_msg}")
                    return False
            else:
                logger.error(f"Error HTTP configurando leverage: {response.status_code} - {response.text}")
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
                else:
                    error_msg = data.get('msg', 'Unknown error')
                    error_code = data.get('code', 'Unknown')
                    logger.error(f"Error en get_positions: {error_code} - {error_msg}")
            
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
                    error_msg = data.get('msg', 'Unknown error')
                    error_code = data.get('code', 'Unknown')
                    logger.error(f"Error en get_klines: {error_code} - {error_msg}")
                    
                    # Intentar con USDT-MIX si falla con USDT-FUTURES
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
                        else:
                            error_msg = data.get('msg', 'Unknown error')
                            error_code = data.get('code', 'Unknown')
                            logger.error(f"Error en get_klines con USDT-MIX: {error_code} - {error_msg}")
            else:
                logger.error(f"Error HTTP en get_klines: {response.status_code} - {response.text}")
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
                logger.error(f"⚠ Error en optimización automática: {e}")
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

    def buscar_configuracion_optima_simbolo(self, simbolo):
        """Busca la mejor combinación de velas/timeframe"""
        if simbolo in self.config_optima_por_simbolo:
            config_optima = self.config_optima_por_simbolo[simbolo]
            ultima_busqueda = self.ultima_busqueda_config.get(simbolo)
            if ultima_busqueda and (datetime.now() - ultima_busqueda).total_seconds() < 7200:
                return config_optima
            else:
                logger.info(f"   🔄 Reevaluando configuración para {simbolo} (pasó 2 horas)")
        logger.info(f"   🔍 Buscando configuración óptima para {simbolo}...")
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
                except Exception as e:
                    logger.error(f"Error evaluando configuración {timeframe}-{num_velas}: {e}")
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
                    except Exception as e:
                        logger.error(f"Error evaluando configuración alternativa {timeframe}-{num_velas}: {e}")
                        continue
        if mejor_config:
            self.config_optima_por_simbolo[simbolo] = mejor_config
            self.ultima_busqueda_config[simbolo] = datetime.now()
            logger.info(f"   ✅ Config óptima: {mejor_config['timeframe']} - {mejor_config['num_velas']} velas - Ancho: {mejor_config['ancho_canal']:.1f}%")
        return mejor_config

    def obtener_datos_mercado_config(self, simbolo, timeframe, num_velas):
        """Obtiene datos con configuración específica usando API de Bitget"""
        # Usar API de Bitget en lugar de Binance
        if self.bitget_client:
            try:
                candles = self.bitget_client.get_klines(simbolo, timeframe, num_velas + 14)
                if not candles or len(candles) == 0:
                    logger.warning(f"No se obtuvieron velas de Bitget para {simbolo} {timeframe}")
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
                logger.error(f"Error obteniendo datos de Bitget para {simbolo}: {e}")
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
        except Exception as e:
            logger.error(f"Error obteniendo datos de Binance para {simbolo}: {e}")
            return None

    def calcular_canal_regresion_config(self, datos_mercado, candle_period):
        """Calcula canal de regresión"""
        try:
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
        except Exception as e:
            logger.error(f"Error en calcular_canal_regresion_config: {e}")
            return None

    def enviar_alerta_breakout(self, simbolo, tipo_breakout, info_canal, datos_mercado, config_optima):
        """
        Envía alerta de BREAKOUT detectado a Telegram con gráfico
        LÓGICA CORREGIDA:
        - BREAKOUT_LONG → Ruptura de resistencia en canal BAJISTA (oportunidad de reversión alcista)
        - BREAKOUT_SHORT → Ruptura de soporte en canal ALCISTA (oportunidad de reversión bajista)
        """
        try:
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
                    logger.info(f"     📊 Generando gráfico de breakout para {simbolo}...")
                    buf = self.generar_grafico_breakout(simbolo, info_canal, datos_mercado, tipo_breakout, config_optima)
                    if buf:
                        logger.info(f"     📨 Enviando alerta de breakout por Telegram...")
                        self.enviar_grafico_telegram(buf, token, chat_ids)
                        time.sleep(0.5)
                        self._enviar_telegram_simple(mensaje, token, chat_ids)
                        logger.info(f"     ✅ Alerta de breakout enviada para {simbolo}")
                    else:
                        self._enviar_telegram_simple(mensaje, token, chat_ids)
                        logger.warning(f"     ⚠️ Alerta enviada sin gráfico para {simbolo}")
                except Exception as e:
                    logger.error(f"     ❌ Error enviando alerta de breakout: {e}")
            else:
                logger.info(f"     📢 Breakout detectado en {simbolo} (sin Telegram)")
        except Exception as e:
            logger.error(f"Error en enviar_alerta_breakout: {e}")

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
                try:
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
                        logger.warning(f"No se obtuvieron datos de Bitget para el gráfico de {simbolo}")
                        # Fallback a Binance
                        return self._generar_grafico_breakout_binance(simbolo, info_canal, datos_mercado, tipo_breakout, config_optima)
                except Exception as e:
                    logger.error(f"Error obteniendo datos de Bitget para gráfico: {e}")
                    # Fallback a Binance
                    return self._generar_grafico_breakout_binance(simbolo, info_canal, datos_mercado, tipo_breakout, config_optima)
            else:
                # Fallback a Binance
                return self._generar_grafico_breakout_binance(simbolo, info_canal, datos_mercado, tipo_breakout, config_optima)
            
            # Calcular líneas del canal
            tiempos_reg = list(range(len(df)))
            resistencia_values = []
            soporte_values = []
            for i, t in enumerate(tiempos_reg):
                resist = info_canal['pendiente_resistencia'] * t + \
                        (info_canal['resistencia'] - info_canal['pendiente_resistencia'] * tiempos_reg[-1])
                soport = info_canal['pendiente_soporte'] * t + \
                        (info_canal['soporte'] - info_canal['pendiente_soporte'] * tiempos_reg[-1])
                resistencia_values.append(resist)
                soporte_values.append(soport)
            
            # Configurar el gráfico
            fig, axes = plt.subplots(2, 1, gridspec_kw={'height_ratios': [3, 1]}, figsize=(12, 8))
            fig.suptitle(f'BREAKOUT DETECTADO - {simbolo} ({config_optima["timeframe"]})', fontsize=14)
            
            # Gráfico de precios
            mpf.plot(df, type='candle', ax=axes[0], volume=False, style='yahoo')
            
            # Añadir líneas del canal
            axes[0].plot(df.index, resistencia_values, 'r--', label='Resistencia')
            axes[0].plot(df.index, soporte_values, 'g--', label='Soporte')
            
            # Marcar el punto de breakout
            if tipo_breakout == "BREAKOUT_LONG":
                breakout_price = info_canal['soporte']
                breakout_color = 'green'
                breakout_label = 'Ruptura de Soporte'
            else:
                breakout_price = info_canal['resistencia']
                breakout_color = 'red'
                breakout_label = 'Ruptura de Resistencia'
            
            axes[0].axhline(y=breakout_price, color=breakout_color, linestyle=':', label=breakout_label)
            axes[0].scatter(df.index[-1], df['Close'].iloc[-1], color=breakout_color, s=100, marker='o')
            
            # Añadir indicadores
            axes[0].set_title(f'Precio: {df["Close"].iloc[-1]:.8f} | Canal: {info_canal["direccion"]} | Fuerza: {info_canal["fuerza_texto"]}')
            axes[0].legend()
            
            # Gráfico de volumen
            axes[1].bar(df.index, df['Volume'], color='blue', alpha=0.5)
            axes[1].set_title('Volumen')
            
            # Ajustar diseño
            plt.tight_layout()
            
            # Guardar en buffer
            buf = BytesIO()
            plt.savefig(buf, format='png', dpi=100)
            buf.seek(0)
            plt.close()
            
            return buf
        except Exception as e:
            logger.error(f"Error generando gráfico de breakout: {e}")
            return None

    def _generar_grafico_breakout_binance(self, simbolo, info_canal, datos_mercado, tipo_breakout, config_optima):
        """Fallback method para generar gráfico usando datos de Binance"""
        try:
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
                soport = info_canal['pendiente_soporte'] * t + \
                        (info_canal['soporte'] - info_canal['pendiente_soporte'] * tiempos_reg[-1])
                resistencia_values.append(resist)
                soporte_values.append(soport)
            
            # Configurar el gráfico
            fig, axes = plt.subplots(2, 1, gridspec_kw={'height_ratios': [3, 1]}, figsize=(12, 8))
            fig.suptitle(f'BREAKOUT DETECTADO - {simbolo} ({config_optima["timeframe"]})', fontsize=14)
            
            # Gráfico de precios
            mpf.plot(df, type='candle', ax=axes[0], volume=False, style='yahoo')
            
            # Añadir líneas del canal
            axes[0].plot(df.index, resistencia_values, 'r--', label='Resistencia')
            axes[0].plot(df.index, soporte_values, 'g--', label='Soporte')
            
            # Marcar el punto de breakout
            if tipo_breakout == "BREAKOUT_LONG":
                breakout_price = info_canal['soporte']
                breakout_color = 'green'
                breakout_label = 'Ruptura de Soporte'
            else:
                breakout_price = info_canal['resistencia']
                breakout_color = 'red'
                breakout_label = 'Ruptura de Resistencia'
            
            axes[0].axhline(y=breakout_price, color=breakout_color, linestyle=':', label=breakout_label)
            axes[0].scatter(df.index[-1], df['Close'].iloc[-1], color=breakout_color, s=100, marker='o')
            
            # Añadir indicadores
            axes[0].set_title(f'Precio: {df["Close"].iloc[-1]:.8f} | Canal: {info_canal["direccion"]} | Fuerza: {info_canal["fuerza_texto"]}')
            axes[0].legend()
            
            # Gráfico de volumen
            axes[1].bar(df.index, df['Volume'], color='blue', alpha=0.5)
            axes[1].set_title('Volumen')
            
            # Ajustar diseño
            plt.tight_layout()
            
            # Guardar en buffer
            buf = BytesIO()
            plt.savefig(buf, format='png', dpi=100)
            buf.seek(0)
            plt.close()
            
            return buf
        except Exception as e:
            logger.error(f"Error generando gráfico de breakout con Binance: {e}")
            return None

    def enviar_grafico_telegram(self, buf, token, chat_ids):
        """Envía un gráfico a través de Telegram"""
        try:
            for chat_id in chat_ids:
                url = f"https://api.telegram.org/bot{token}/sendPhoto"
                files = {'photo': buf}
                data = {'chat_id': chat_id}
                response = requests.post(url, files=files, data=data, timeout=10)
                if response.status_code != 200:
                    logger.error(f"Error enviando gráfico a Telegram: {response.text}")
        except Exception as e:
            logger.error(f"Error en enviar_grafico_telegram: {e}")

    def _enviar_telegram_simple(self, mensaje, token, chat_ids):
        """Envía un mensaje simple a través de Telegram"""
        try:
            for chat_id in chat_ids:
                url = f"https://api.telegram.org/bot{token}/sendMessage"
                data = {
                    'chat_id': chat_id,
                    'text': mensaje,
                    'parse_mode': 'HTML'
                }
                response = requests.post(url, json=data, timeout=10)
                if response.status_code != 200:
                    logger.error(f"Error enviando mensaje a Telegram: {response.text}")
        except Exception as e:
            logger.error(f"Error en _enviar_telegram_simple: {e}")

    def calcular_regresion_lineal(self, x, y):
        """Calcula la regresión lineal de y sobre x"""
        try:
            if len(x) != len(y) or len(x) == 0:
                return None
            n = len(x)
            sum_x = sum(x)
            sum_y = sum(y)
            sum_xy = sum(x[i] * y[i] for i in range(n))
            sum_x2 = sum(x[i] ** 2 for i in range(n))
            
            # Calcular pendiente e intercepto
            denominator = n * sum_x2 - sum_x ** 2
            if denominator == 0:
                return None
            
            slope = (n * sum_xy - sum_x * sum_y) / denominator
            intercept = (sum_y - slope * sum_x) / n
            
            return (slope, intercept)
        except Exception as e:
            logger.error(f"Error en calcular_regresion_lineal: {e}")
            return None

    def calcular_pearson_y_angulo(self, x, y):
        """Calcula el coeficiente de Pearson y el ángulo de la tendencia"""
        try:
            if len(x) != len(y) or len(x) < 2:
                return (0, 0)
            
            # Calcular coeficiente de Pearson
            n = len(x)
            sum_x = sum(x)
            sum_y = sum(y)
            sum_xy = sum(x[i] * y[i] for i in range(n))
            sum_x2 = sum(x[i] ** 2 for i in range(n))
            sum_y2 = sum(y[i] ** 2 for i in range(n))
            
            numerator = n * sum_xy - sum_x * sum_y
            denominator_x = math.sqrt(n * sum_x2 - sum_x ** 2)
            denominator_y = math.sqrt(n * sum_y2 - sum_y ** 2)
            
            if denominator_x == 0 or denominator_y == 0:
                return (0, 0)
            
            pearson = numerator / (denominator_x * denominator_y)
            
            # Calcular ángulo en grados
            if len(x) >= 2:
                slope = (y[-1] - y[0]) / (x[-1] - x[0])
                angle_rad = math.atan(slope)
                angle_deg = math.degrees(angle_rad)
            else:
                angle_deg = 0
            
            return (pearson, angle_deg)
        except Exception as e:
            logger.error(f"Error en calcular_pearson_y_angulo: {e}")
            return (0, 0)

    def clasificar_fuerza_tendencia(self, angulo):
        """Clasifica la fuerza de la tendencia según el ángulo"""
        try:
            abs_angulo = abs(angulo)
            if abs_angulo < 5:
                return ("Muy débil", 1)
            elif abs_angulo < 10:
                return ("Débil", 2)
            elif abs_angulo < 20:
                return ("Moderada", 3)
            elif abs_angulo < 30:
                return ("Fuerte", 4)
            else:
                return ("Muy fuerte", 5)
        except Exception as e:
            logger.error(f"Error en clasificar_fuerza_tendencia: {e}")
            return ("Desconocida", 0)

    def determinar_direccion_tendencia(self, angulo, umbral=0):
        """Determina la dirección de la tendencia"""
        try:
            if angulo > umbral:
                return "ALCISTA"
            elif angulo < -umbral:
                return "BAJISTA"
            else:
                return "LATERAL"
        except Exception as e:
            logger.error(f"Error en determinar_direccion_tendencia: {e}")
            return "DESCONOCIDA"

    def calcular_r2(self, y, x, slope, intercept):
        """Calcula el coeficiente de determinación R²"""
        try:
            if len(y) != len(x) or len(y) == 0:
                return 0
            
            # Calcular valores predichos
            y_pred = [slope * xi + intercept for xi in x]
            
            # Calcular mediana de y
            y_mean = sum(y) / len(y)
            
            # Calcular sumatorias
            ss_res = sum((y[i] - y_pred[i]) ** 2 for i in range(len(y)))
            ss_tot = sum((y[i] - y_mean) ** 2 for i in range(len(y)))
            
            # Calcular R²
            if ss_tot == 0:
                return 0
            
            r2 = 1 - (ss_res / ss_tot)
            return r2
        except Exception as e:
            logger.error(f"Error en calcular_r2: {e}")
            return 0

    def calcular_stochastic(self, datos_mercado, k_period=14, d_period=3):
        """Calcula el indicador estocástico"""
        try:
            if not datos_mercado or len(datos_mercado['cierres']) < k_period:
                return (50, 50)  # Valores por defecto si no hay suficientes datos
            
            cierres = datos_mercado['cierres']
            maximos = datos_mercado['maximos']
            minimos = datos_mercado['minimos']
            
            # Calcular %K
            k_values = []
            for i in range(k_period - 1, len(cierres)):
                highest_high = max(maximos[i - k_period + 1:i + 1])
                lowest_low = min(minimos[i - k_period + 1:i + 1])
                
                if highest_high == lowest_low:
                    k_percent = 50  # Evitar división por cero
                else:
                    k_percent = 100 * ((cierres[i] - lowest_low) / (highest_high - lowest_low))
                
                k_values.append(k_percent)
            
            # Calcular %D como media móvil de %K
            if len(k_values) >= d_period:
                d_values = []
                for i in range(d_period - 1, len(k_values)):
                    d_values.append(sum(k_values[i - d_period + 1:i + 1]) / d_period)
                
                return (k_values[-1], d_values[-1])
            else:
                return (k_values[-1], k_values[-1])  # Si no hay suficientes valores para %D
        except Exception as e:
            logger.error(f"Error en calcular_stochastic: {e}")
            return (50, 50)  # Valores por defecto en caso de error

    def inicializar_log(self):
        """Inicializa el archivo de log de operaciones"""
        try:
            if not os.path.exists(self.archivo_log):
                with open(self.archivo_log, 'w', newline='', encoding='utf-8') as f:
                    writer = csv.writer(f)
                    writer.writerow([
                        'timestamp', 'symbol', 'tipo', 'precio_entrada', 'precio_salida',
                        'pnl_percent', 'angulo_tendencia', 'pearson', 'r2_score',
                        'ancho_canal_relativo', 'nivel_fuerza', 'timeframe', 'num_velas'
                    ])
        except Exception as e:
            logger.error(f"Error inicializando log: {e}")

    def registrar_operacion(self, simbolo, tipo, precio_entrada, precio_salida, info_canal, config_optima):
        """Registra una operación en el log"""
        try:
            pnl_percent = ((precio_salida - precio_entrada) / precio_entrada) * 100
            if tipo == 'SHORT':
                pnl_percent = -pnl_percent
            
            with open(self.archivo_log, 'a', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow([
                    datetime.now().isoformat(),
                    simbolo,
                    tipo,
                    precio_entrada,
                    precio_salida,
                    pnl_percent,
                    info_canal.get('angulo_tendencia', 0),
                    info_canal.get('coeficiente_pearson', 0),
                    info_canal.get('r2_score', 0),
                    info_canal.get('ancho_canal_porcentual', 0),
                    info_canal.get('nivel_fuerza', 0),
                    config_optima.get('timeframe', 'N/A'),
                    config_optima.get('num_velas', 0)
                ])
            
            self.total_operaciones += 1
            self.operaciones_desde_optimizacion += 1
            
            # Verificar si es momento de optimizar
            if (self.auto_optimize and 
                self.operaciones_desde_optimizacion >= 20 and 
                (datetime.now() - self.ultima_optimizacion).days >= 7):
                logger.info("⚙️ Iniciando optimización automática...")
                try:
                    ia = OptimizadorIA(log_path=self.log_path, min_samples=15)
                    parametros_optimizados = ia.buscar_mejores_parametros()
                    if parametros_optimizados:
                        self.config['trend_threshold_degrees'] = parametros_optimizados.get('trend_threshold_degrees', 
                                                                                        self.config.get('trend_threshold_degrees', 13))
                        self.config['min_trend_strength_degrees'] = parametros_optimizados.get('min_trend_strength_degrees', 
                                                                                               self.config.get('min_trend_strength_degrees', 16))
                        self.config['entry_margin'] = parametros_optimizados.get('entry_margin', 
                                                                               self.config.get('entry_margin', 0.001))
                        self.ultima_optimizacion = datetime.now()
                        self.operaciones_desde_optimizacion = 0
                        logger.info("✅ Parámetros optimizados y actualizados")
                except Exception as e:
                    logger.error(f"Error en optimización automática: {e}")
        except Exception as e:
            logger.error(f"Error registrando operación: {e}")

    def procesar_senal(self, simbolo, tipo_senal, info_canal, datos_mercado, config_optima):
        """Procesa una señal de trading"""
        try:
            timestamp_actual = datetime.now()
            
            # Verificar si ya hay una operación activa para este símbolo
            if simbolo in self.operaciones_activas:
                logger.info(f"Ya hay una operación activa para {simbolo}, ignorando señal")
                return
            
            # Crear ID único para esta señal
            senal_id = f"{simbolo}_{tipo_senal}_{timestamp_actual.strftime('%Y%m%d%H%M%S')}"
            
            # Verificar si ya se envió esta señal
            if senal_id in self.senales_enviadas:
                logger.info(f"Señal ya enviada: {senal_id}")
                return
            
            # Agregar a señales enviadas
            self.senales_enviadas.add(senal_id)
            
            # Guardar estado
            self.guardar_estado()
            
            # Si está habilitada la ejecución automática, ejecutar la operación
            if self.ejecutar_operaciones_automaticas and self.bitget_client:
                try:
                    # Ejecutar operación en Bitget
                    operacion = ejecutar_operacion_bitget(
                        bitget_client=self.bitget_client,
                        simbolo=simbolo,
                        tipo_operacion=tipo_senal.split('_')[1],  # LONG o SHORT
                        capital_usd=self.capital_por_operacion,
                        leverage=self.leverage_por_defecto
                    )
                    
                    if operacion:
                        # Registrar operación activa
                        self.operaciones_activas[simbolo] = {
                            'timestamp': timestamp_actual,
                            'tipo': tipo_senal,
                            'operacion': operacion,
                            'info_canal': info_canal,
                            'config_optima': config_optima
                        }
                        
                        # Enviar confirmación a Telegram
                        mensaje = f"""
✅ <b>OPERACIÓN EJECUTADA - {simbolo}</b>
📈 <b>Tipo:</b> {tipo_senal}
💰 <b>Capital:</b> ${self.capital_por_operacion}
⚙️ <b>Apalancamiento:</b> {self.leverage_por_defecto}x
💵 <b>Precio entrada:</b> {operacion['precio_entrada']:.8f}
🎯 <b>Take Profit:</b> {operacion['take_profit']:.8f}
🛡️ <b>Stop Loss:</b> {operacion['stop_loss']:.8f}
⏰ <b>Hora:</b> {timestamp_actual.strftime('%Y-%m-%d %H:%M:%S')}
                        """
                        
                        token = self.config.get('telegram_token')
                        chat_ids = self.config.get('telegram_chat_ids', [])
                        if token and chat_ids:
                            self._enviar_telegram_simple(mensaje, token, chat_ids)
                        
                        logger.info(f"Operación ejecutada para {simbolo}: {tipo_senal}")
                    else:
                        logger.error(f"No se pudo ejecutar la operación para {simbolo}")
                except Exception as e:
                    logger.error(f"Error ejecutando operación para {simbolo}: {e}")
            else:
                logger.info(f"Señal detectada para {simbolo}: {tipo_senal} (ejecución automática deshabilitada)")
        except Exception as e:
            logger.error(f"Error procesando señal: {e}")

    def verificar_breakouts_y_reentry(self, simbolo):
        """Verifica si hay breakouts o reingresos para un símbolo"""
        try:
            # Buscar configuración óptima para este símbolo
            config_optima = self.buscar_configuracion_optima_simbolo(simbolo)
            if not config_optima:
                logger.warning(f"No se encontró configuración óptima para {simbolo}")
                return
            
            # Obtener datos de mercado
            datos_mercado = self.obtener_datos_mercado_config(
                simbolo, 
                config_optima['timeframe'], 
                config_optima['num_velas']
            )
            if not datos_mercado:
                logger.warning(f"No se pudieron obtener datos para {simbolo}")
                return
            
            # Calcular canal de regresión
            info_canal = self.calcular_canal_regresion_config(datos_mercado, config_optima['num_velas'])
            if not info_canal:
                logger.warning(f"No se pudo calcular canal para {simbolo}")
                return
            
            # Verificar condiciones de breakout
            precio_actual = datos_mercado['precio_actual']
            resistencia = info_canal['resistencia']
            soporte = info_canal['soporte']
            
            # Verificar si ya hay un breakout detectado para este símbolo
            if simbolo in self.breakouts_detectados:
                breakout_info = self.breakouts_detectados[simbolo]
                tiempo_breakout = breakout_info['timestamp']
                tipo_breakout = breakout_info['tipo']
                
                # Verificar si ha pasado mucho tiempo desde el breakout (más de 30 minutos)
                if (datetime.now() - tiempo_breakout).total_seconds() > 1800:
                    # Demasiado tiempo, eliminar breakout
                    del self.breakouts_detectados[simbolo]
                    logger.info(f"Breakout para {simbolo} expirado por tiempo")
                    return
                
                # Verificar si hay reingreso
                if tipo_breakout == "BREAKOUT_LONG":
                    # Buscar reingreso por encima del soporte
                    if precio_actual > soporte * (1 - self.config.get('entry_margin', 0.001)):
                        logger.info(f"¡REINGRESO DETECTADO para {simbolo} (LONG)!")
                        self.enviar_alerta_reentry(simbolo, "REENTRY_LONG", info_canal, datos_mercado, config_optima)
                        self.procesar_senal(simbolo, "REENTRY_LONG", info_canal, datos_mercado, config_optima)
                        del self.breakouts_detectados[simbolo]
                else:  # BREAKOUT_SHORT
                    # Buscar reingreso por debajo de la resistencia
                    if precio_actual < resistencia * (1 + self.config.get('entry_margin', 0.001)):
                        logger.info(f"¡REINGRESO DETECTADO para {simbolo} (SHORT)!")
                        self.enviar_alerta_reentry(simbolo, "REENTRY_SHORT", info_canal, datos_mercado, config_optima)
                        self.procesar_senal(simbolo, "REENTRY_SHORT", info_canal, datos_mercado, config_optima)
                        del self.breakouts_detectados[simbolo]
            else:
                # No hay breakout detectado, verificar si hay uno nuevo
                # Para BREAKOUT_LONG: precio por debajo del soporte en canal bajista
                if (info_canal['direccion'] == "BAJISTA" and 
                    precio_actual < soporte and
                    abs(info_canal['angulo_tendencia']) >= self.config.get('trend_threshold_degrees', 13) and
                    abs(info_canal['angulo_tendencia']) >= self.config.get('min_trend_strength_degrees', 16) and
                    abs(info_canal['coeficiente_pearson']) >= 0.4 and
                    info_canal['r2_score'] >= 0.4 and
                    info_canal['nivel_fuerza'] >= 2):
                    
                    logger.info(f"¡BREAKOUT_LONG DETECTADO para {simbolo}!")
                    self.breakouts_detectados[simbolo] = {
                        'tipo': 'BREAKOUT_LONG',
                        'timestamp': datetime.now(),
                        'precio_breakout': precio_actual
                    }
                    self.enviar_alerta_breakout(simbolo, "BREAKOUT_LONG", info_canal, datos_mercado, config_optima)
                
                # Para BREAKOUT_SHORT: precio por encima de la resistencia en canal alcista
                elif (info_canal['direccion'] == "ALCISTA" and 
                      precio_actual > resistencia and
                      abs(info_canal['angulo_tendencia']) >= self.config.get('trend_threshold_degrees', 13) and
                      abs(info_canal['angulo_tendencia']) >= self.config.get('min_trend_strength_degrees', 16) and
                      abs(info_canal['coeficiente_pearson']) >= 0.4 and
                      info_canal['r2_score'] >= 0.4 and
                      info_canal['nivel_fuerza'] >= 2):
                    
                    logger.info(f"¡BREAKOUT_SHORT DETECTADO para {simbolo}!")
                    self.breakouts_detectados[simbolo] = {
                        'tipo': 'BREAKOUT_SHORT',
                        'timestamp': datetime.now(),
                        'precio_breakout': precio_actual
                    }
                    self.enviar_alerta_breakout(simbolo, "BREAKOUT_SHORT", info_canal, datos_mercado, config_optima)
        except Exception as e:
            logger.error(f"Error verificando breakouts para {simbolo}: {e}")

    def enviar_alerta_reentry(self, simbolo, tipo_reentry, info_canal, datos_mercado, config_optima):
        """Envía alerta de REENTRY detectado a Telegram con gráfico"""
        try:
            precio_cierre = datos_mercado['cierres'][-1]
            resistencia = info_canal['resistencia']
            soporte = info_canal['soporte']
            direccion_canal = info_canal['direccion']
            
            if tipo_reentry == "REENTRY_LONG":
                emoji_principal = "🟢"
                tipo_texto = "REINGRESO ALCISTA"
                nivel = f"Soporte: {soporte:.8f}"
                expectativa = "posible operación en LONG"
            else:  # REENTRY_SHORT
                emoji_principal = "🔴"
                tipo_texto = "REINGRESO BAJISTA"
                nivel = f"Resistencia: {resistencia:.8f}"
                expectativa = "posible operación en SHORT"
            
            # Mensaje de alerta
            mensaje = f"""
{emoji_principal} <b>¡REENTRY DETECTADO! - {simbolo}</b>
✅ <b>{tipo_texto}</b>
⏰ <b>Hora:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
📍 <b>Precio actual:</b> {precio_cierre:.8f}
📍 <b>{nivel}</b>
📈 <b>Canal:</b> {direccion_canal}
🎯 <b>{expectativa}</b>
            """
            
            token = self.config.get('telegram_token')
            chat_ids = self.config.get('telegram_chat_ids', [])
            if token and chat_ids:
                try:
                    logger.info(f"     📊 Generando gráfico de reentry para {simbolo}...")
                    buf = self.generar_grafico_reentry(simbolo, info_canal, datos_mercado, tipo_reentry, config_optima)
                    if buf:
                        logger.info(f"     📨 Enviando alerta de reentry por Telegram...")
                        self.enviar_grafico_telegram(buf, token, chat_ids)
                        time.sleep(0.5)
                        self._enviar_telegram_simple(mensaje, token, chat_ids)
                        logger.info(f"     ✅ Alerta de reentry enviada para {simbolo}")
                    else:
                        self._enviar_telegram_simple(mensaje, token, chat_ids)
                        logger.warning(f"     ⚠️ Alerta de reentry enviada sin gráfico para {simbolo}")
                except Exception as e:
                    logger.error(f"     ❌ Error enviando alerta de reentry: {e}")
            else:
                logger.info(f"     📢 Reentry detectado en {simbolo} (sin Telegram)")
        except Exception as e:
            logger.error(f"Error en enviar_alerta_reentry: {e}")

    def generar_grafico_reentry(self, simbolo, info_canal, datos_mercado, tipo_reentry, config_optima):
        """Genera gráfico especial para el momento del REENTRY"""
        try:
            # Usar el mismo método que para generar_grafico_breakout pero con cambios menores
            return self.generar_grafico_breakout(simbolo, info_canal, datos_mercado, tipo_reentry, config_optima)
        except Exception as e:
            logger.error(f"Error generando gráfico de reentry: {e}")
            return None

    def generar_reporte_semanal(self):
        """Genera un reporte semanal de operaciones"""
        try:
            logger.info("Generando reporte semanal...")
            
            # Leer datos del log
            operaciones = []
            try:
                with open(self.archivo_log, 'r', encoding='utf-8') as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        operaciones.append(row)
            except FileNotFoundError:
                logger.warning("No se encontró el archivo de log para generar reporte")
                return
            
            if not operaciones:
                logger.warning("No hay operaciones para generar reporte")
                return
            
            # Filtrar operaciones de la última semana
            una_semana_atras = datetime.now() - timedelta(days=7)
            operaciones_semana = [
                op for op in operaciones 
                if datetime.fromisoformat(op['timestamp']) >= una_semana_atras
            ]
            
            if not operaciones_semana:
                logger.warning("No hay operaciones en la última semana")
                return
            
            # Calcular estadísticas
            total_operaciones = len(operaciones_semana)
            operaciones_ganadoras = [op for op in operaciones_semana if float(op['pnl_percent']) > 0]
            operaciones_perdedoras = [op for op in operaciones_semana if float(op['pnl_percent']) < 0]
            
            winrate = len(operaciones_ganadoras) / total_operaciones * 100 if total_operaciones > 0 else 0
            
            pnl_total = sum(float(op['pnl_percent']) for op in operaciones_semana)
            pnl_promedio = pnl_total / total_operaciones if total_operaciones > 0 else 0
            
            # Agrupar por símbolo
            simbolos = {}
            for op in operaciones_semana:
                simbolo = op['symbol']
                if simbolo not in simbolos:
                    simbolos[simbolo] = {
                        'operaciones': 0,
                        'ganadoras': 0,
                        'pnl_total': 0
                    }
                
                simbolos[simbolo]['operaciones'] += 1
                simbolos[simbolo]['pnl_total'] += float(op['pnl_percent'])
                
                if float(op['pnl_percent']) > 0:
                    simbolos[simbolo]['ganadoras'] += 1
            
            # Generar mensaje del reporte
            mensaje = f"""
📊 <b>REPORTE SEMANAL DE OPERACIONES</b>
📅 <b>Período:</b> {una_semana_atras.strftime('%Y-%m-%d')} a {datetime.now().strftime('%Y-%m-%d')}
📈 <b>Total operaciones:</b> {total_operaciones}
✅ <b>Operaciones ganadoras:</b> {len(operaciones_ganadoras)} ({winrate:.1f}%)
❌ <b>Operaciones perdedoras:</b> {len(operaciones_perdedoras)} ({100-winrate:.1f}%)
💰 <b>PnL total:</b> {pnl_total:.2f}%
📊 <b>PnL promedio:</b> {pnl_promedio:.2f}%

<b>Resultados por símbolo:</b>
            """
            
            for simbolo, datos in sorted(simbolos.items(), key=lambda x: x[1]['pnl_total'], reverse=True):
                winrate_simbolo = datos['ganadoras'] / datos['operaciones'] * 100 if datos['operaciones'] > 0 else 0
                mensaje += f"\n• {simbolo}: {datos['operaciones']} ops, {datos['pnl_total']:.2f}% PnL, {winrate_simbolo:.1f}% winrate"
            
            # Enviar reporte a Telegram
            token = self.config.get('telegram_token')
            chat_ids = self.config.get('telegram_chat_ids', [])
            if token and chat_ids:
                self._enviar_telegram_simple(mensaje, token, chat_ids)
                logger.info("Reporte semanal enviado a Telegram")
            else:
                logger.info("Reporte semanal generado (sin Telegram)")
        except Exception as e:
            logger.error(f"Error generando reporte semanal: {e}")

    def iniciar_bot(self):
        """Inicia el bot de trading"""
        logger.info("Iniciando bot de trading...")
        
        # Verificar si hay un estado previo
        self.cargar_estado()
        
        # Lista de símbolos a monitorear
        simbolos = self.config.get('simbolos', ['BTCUSDT', 'ETHUSDT', 'ADAUSDT'])
        
        # Bucle principal
        while True:
            try:
                # Verificar cada símbolo
                for simbolo in simbolos:
                    try:
                        self.verificar_breakouts_y_reentry(simbolo)
                    except Exception as e:
                        logger.error(f"Error verificando {simbolo}: {e}")
                
                # Verificar si es hora de generar reporte semanal (cada domingo a las 23:00)
                ahora = datetime.now()
                if ahora.weekday() == 6 and ahora.hour == 23 and ahora.minute == 0:
                    self.generar_reporte_semanal()
                
                # Guardar estado cada 5 minutos
                if ahora.minute % 5 == 0:
                    self.guardar_estado()
                
                # Esperar antes de la siguiente iteración
                time.sleep(60)  # Verificar cada minuto
            except KeyboardInterrupt:
                logger.info("Bot detenido por el usuario")
                self.guardar_estado()
                break
            except Exception as e:
                logger.error(f"Error en bucle principal: {e}")
                time.sleep(60)  # Esperar antes de reintentar

# ---------------------------
# FLASK WEB SERVICE
# ---------------------------
app = Flask(__name__)

# Variable global para el bot
bot = None

@app.route('/')
def index():
    return "Bot Breakout + Reentry está en ejecución"

@app.route('/start', methods=['POST'])
def start_bot():
    global bot
    try:
        if bot is not None:
            return jsonify({"status": "error", "message": "El bot ya está en ejecución"})
        
        # Obtener configuración de la solicitud
        config = request.json
        if not config:
            # Configuración por defecto
            config = {
                'simbolos': ['BTCUSDT', 'ETHUSDT', 'ADAUSDT'],
                'timeframes': ['1m', '3m', '5m', '15m', '30m'],
                'velas_options': [80, 100, 120, 150, 200],
                'trend_threshold_degrees': 13,
                'min_trend_strength_degrees': 16,
                'entry_margin': 0.001,
                'min_channel_width_percent': 4.0,
                'auto_optimize': True,
                'min_samples_optimizacion': 15,
                'telegram_token': os.environ.get('TELEGRAM_TOKEN', ''),
                'telegram_chat_ids': os.environ.get('TELEGRAM_CHAT_IDS', '').split(','),
                'bitget_api_key': os.environ.get('BITGET_API_KEY', ''),
                'bitget_api_secret': os.environ.get('BITGET_SECRET_KEY', ''),
                'bitget_passphrase': os.environ.get('BITGET_PASSPHRASE', ''),
                'ejecutar_operaciones_automaticas': os.environ.get('EJECUTAR_OPERACIONES', 'False').lower() == 'true',
                'capital_por_operacion': float(os.environ.get('CAPITAL_POR_OPERACION', 50)),
                'leverage_por_defecto': int(os.environ.get('LEVERAGE_POR_DEFECTO', 20))
            }
        
        # Iniciar bot en un hilo separado
        bot = TradingBot(config)
        bot_thread = threading.Thread(target=bot.iniciar_bot)
        bot_thread.daemon = True
        bot_thread.start()
        
        return jsonify({"status": "success", "message": "Bot iniciado correctamente"})
    except Exception as e:
        logger.error(f"Error iniciando bot: {e}")
        return jsonify({"status": "error", "message": str(e)})

@app.route('/stop', methods=['POST'])
def stop_bot():
    global bot
    try:
        if bot is None:
            return jsonify({"status": "error", "message": "El bot no está en ejecución"})
        
        # Guardar estado antes de detener
        bot.guardar_estado()
        
        # Detener bot
        bot = None
        
        return jsonify({"status": "success", "message": "Bot detenido correctamente"})
    except Exception as e:
        logger.error(f"Error deteniendo bot: {e}")
        return jsonify({"status": "error", "message": str(e)})

@app.route('/status', methods=['GET'])
def status():
    try:
        global bot
        if bot is None:
            return jsonify({"status": "stopped", "message": "El bot no está en ejecución"})
        
        # Obtener estado del bot
        estado = {
            "status": "running",
            "total_operaciones": bot.total_operaciones,
            "operaciones_desde_optimizacion": bot.operaciones_desde_optimizacion,
            "ultima_optimizacion": bot.ultima_optimizacion.isoformat() if bot.ultima_optimizacion else None,
            "breakouts_detectados": len(bot.breakouts_detectados),
            "operaciones_activas": len(bot.operaciones_activas),
            "simbolos_monitoreados": bot.config.get('simbolos', [])
        }
        
        return jsonify(estado)
    except Exception as e:
        logger.error(f"Error obteniendo estado: {e}")
        return jsonify({"status": "error", "message": str(e)})

@app.route('/optimize', methods=['POST'])
def optimize():
    try:
        global bot
        if bot is None:
            return jsonify({"status": "error", "message": "El bot no está en ejecución"})
        
        # Ejecutar optimización
        ia = OptimizadorIA(log_path=bot.log_path, min_samples=bot.config.get('min_samples_optimizacion', 15))
        parametros_optimizados = ia.buscar_mejores_parametros()
        
        if parametros_optimizados:
            # Actualizar configuración del bot
            bot.config['trend_threshold_degrees'] = parametros_optimizados.get('trend_threshold_degrees', 
                                                                             bot.config.get('trend_threshold_degrees', 13))
            bot.config['min_trend_strength_degrees'] = parametros_optimizados.get('min_trend_strength_degrees', 
                                                                                 bot.config.get('min_trend_strength_degrees', 16))
            bot.config['entry_margin'] = parametros_optimizados.get('entry_margin', 
                                                                     bot.config.get('entry_margin', 0.001))
            bot.ultima_optimizacion = datetime.now()
            bot.operaciones_desde_optimizacion = 0
            
            return jsonify({
                "status": "success", 
                "message": "Optimización completada",
                "parametros": parametros_optimizados
            })
        else:
            return jsonify({
                "status": "warning", 
                "message": "No se encontraron parámetros optimizados"
            })
    except Exception as e:
        logger.error(f"Error en optimización: {e}")
        return jsonify({"status": "error", "message": str(e)})

@app.route('/report', methods=['GET'])
def generate_report():
    try:
        global bot
        if bot is None:
            return jsonify({"status": "error", "message": "El bot no está en ejecución"})
        
        # Generar reporte semanal
        bot.generar_reporte_semanal()
        
        return jsonify({"status": "success", "message": "Reporte generado y enviado"})
    except Exception as e:
        logger.error(f"Error generando reporte: {e}")
        return jsonify({"status": "error", "message": str(e)})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
