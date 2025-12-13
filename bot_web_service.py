# bot_web_service.py
# Adaptación para Render del bot Breakout + Reentry con correcciones Bitget V2
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

# Configurar logging detallado
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
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
            logger.warning("⚠ No se encontró operaciones_log.csv (optimizador)")
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
            logger.warning("⚠ No se encontró una configuración mejor")
        return mejores_param

# ---------------------------
# BITGET CLIENT - INTEGRACIÓN CORREGIDA CON API BITGET V2
# ---------------------------
class BitgetClient:
    def __init__(self, api_key, api_secret, passphrase):
        self.api_key = api_key
        self.api_secret = api_secret
        self.passphrase = passphrase
        self.base_url = "https://api.bitget.com"
        self.session = requests.Session()
        logger.info(f"Cliente Bitget inicializado con API Key: {api_key[:10]}...")
        logger.info("📌 IMPORTANTE: Verifica que en Bitget Futures esté configurado:")
        logger.info("   1. Modo de posición: Modo de cobertura")
        logger.info("   2. Prevención de auto-trading: Modo por defecto")
        logger.info("   3. Unidad de futuros: Costo")

    def _generate_signature(self, timestamp, method, request_path, body=''):
        """Generar firma HMAC-SHA256 para Bitget V2"""
        try:
            if isinstance(body, dict):
                body_str = json.dumps(body, separators=(',', ':')) if body else ''
            else:
                body_str = str(body) if body else ''
            
            message = timestamp + method.upper() + request_path + body_str
            
            logger.debug(f"Mensaje para firma: {message}")
            
            mac = hmac.new(
                bytes(self.api_secret, 'utf-8'),
                bytes(message, 'utf-8'),
                digestmod=hashlib.sha256
            )
            
            signature = base64.b64encode(mac.digest()).decode()
            logger.debug(f"Firma generada: {signature[:50]}...")
            return signature
            
        except Exception as e:
            logger.error(f"❌ Error generando firma: {str(e)}")
            logger.error(f"API Secret usado: {self.api_secret[:10]}...")
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
            logger.error(f"❌ Error creando headers: {str(e)}")
            raise

    def _make_request(self, method, endpoint, params=None, body=None):
        """Método genérico para hacer requests con logging detallado"""
        try:
            url = f"{self.base_url}{endpoint}"
            headers = self._get_headers(method, endpoint, body)
            
            logger.info(f"📤 Enviando {method} a {endpoint}")
            logger.debug(f"URL: {url}")
            logger.debug(f"Params: {params}")
            logger.debug(f"Body: {body}")
            
            start_time = time.time()
            
            if method.upper() == 'GET':
                response = self.session.get(url, headers=headers, params=params, timeout=30)
            elif method.upper() == 'POST':
                response = self.session.post(url, headers=headers, json=body, timeout=30)
            else:
                logger.error(f"Método no soportado: {method}")
                return None
            
            elapsed = (time.time() - start_time) * 1000
            
            logger.info(f"📥 Respuesta recibida en {elapsed:.0f}ms - Status: {response.status_code}")
            logger.debug(f"Response headers: {dict(response.headers)}")
            logger.debug(f"Response text: {response.text[:500]}")
            
            if response.status_code != 200:
                logger.error(f"❌ Error HTTP {response.status_code}: {response.text}")
                return None
                
            data = response.json()
            
            if data.get('code') != '00000':
                error_code = data.get('code', 'Unknown')
                error_msg = data.get('msg', 'Unknown error')
                logger.error(f"❌ Error API Bitget: {error_code} - {error_msg}")
                logger.error(f"Request details: {method} {endpoint}")
                logger.error(f"Error data: {data}")
                return None
                
            logger.info(f"✅ Request exitoso a {endpoint}")
            return data.get('data', {})
            
        except requests.exceptions.Timeout:
            logger.error(f"⏱️ Timeout en request a {endpoint}")
            return None
        except requests.exceptions.ConnectionError:
            logger.error(f"🔌 Error de conexión en {endpoint}")
            return None
        except Exception as e:
            logger.error(f"❌ Error en _make_request: {str(e)}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            return None

    def verificar_credenciales(self):
        """Verificar que las credenciales sean válidas"""
        try:
            logger.info("🔐 Verificando credenciales Bitget...")
            
            if not self.api_key or not self.api_secret or not self.passphrase:
                logger.error("❌ Credenciales incompletas")
                logger.error(f"API Key: {'Presente' if self.api_key else 'Faltante'}")
                logger.error(f"API Secret: {'Presente' if self.api_secret else 'Faltante'}")
                logger.error(f"Passphrase: {'Presente' if self.passphrase else 'Faltante'}")
                return False
            
            # Verificar cuenta primero
            accounts = self.get_account_info()
            if accounts:
                logger.info("✅ Credenciales verificadas exitosamente")
                for account in accounts:
                    if account.get('marginCoin') == 'USDT':
                        available = float(account.get('available', 0))
                        logger.info(f"💰 Balance disponible: {available:.2f} USDT")
                        if available < 10:
                            logger.warning(f"⚠️ Balance bajo: {available:.2f} USDT")
                return True
            else:
                logger.error("❌ No se pudo verificar credenciales - respuesta vacía")
                return False
                
        except Exception as e:
            logger.error(f"❌ Error verificando credenciales: {str(e)}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            return False

    def get_account_info(self, product_type='USDT-FUTURES'):
        """Obtener información de cuenta Bitget V2"""
        try:
            endpoint = '/api/v2/mix/account/accounts'
            params = {'productType': product_type, 'marginCoin': 'USDT'}
            
            data = self._make_request('GET', endpoint, params)
            
            if data is None and product_type == 'USDT-FUTURES':
                logger.info("Intentando con productType='USDT-MIX'...")
                return self.get_account_info('USDT-MIX')
                
            return data if data else []
            
        except Exception as e:
            logger.error(f"❌ Error en get_account_info: {str(e)}")
            return None

    def get_symbol_info(self, symbol):
        """Obtener información del símbolo"""
        try:
            endpoint = '/api/v2/mix/market/contracts'
            params = {'productType': 'USDT-FUTURES'}
            
            data = self._make_request('GET', endpoint, params)
            
            if data:
                for contract in data:
                    if contract.get('symbol') == symbol:
                        logger.info(f"✅ Info símbolo {symbol}: {contract.get('symbolName')}")
                        logger.debug(f"Contract details: {contract}")
                        return contract
            
            # Intentar con USDT-MIX
            params = {'productType': 'USDT-MIX'}
            data = self._make_request('GET', endpoint, params)
            
            if data:
                for contract in data:
                    if contract.get('symbol') == symbol:
                        logger.info(f"✅ Info símbolo {symbol} (USDT-MIX): {contract.get('symbolName')}")
                        return contract
            
            logger.error(f"❌ Símbolo {symbol} no encontrado en Bitget")
            return None
            
        except Exception as e:
            logger.error(f"❌ Error obteniendo info del símbolo {symbol}: {str(e)}")
            return None

    def place_order(self, symbol, side, order_type, size, price=None, 
                    client_order_id=None, time_in_force='normal'):
        """Colocar orden de mercado o límite - CORREGIDO para Bitget V2"""
        try:
            logger.info(f"📤 Colocando orden: {symbol} {side} {order_type} size={size}")
            
            # Mapeo de sides según documentación Bitget V2
            side_map = {
                'open_long': 'open_long',
                'open_short': 'open_short',
                'close_long': 'close_long',
                'close_short': 'close_short'
            }
            
            if side not in side_map:
                logger.error(f"❌ Side inválido: {side}. Opciones: {list(side_map.keys())}")
                return None
            
            endpoint = '/api/v2/mix/order/place-order'
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
            
            logger.info(f"📝 Body de la orden: {json.dumps(body, indent=2)}")
            
            data = self._make_request('POST', endpoint, body=body)
            
            if data:
                logger.info(f"✅ Orden colocada exitosamente: {data}")
                return data
            else:
                logger.error(f"❌ Error colocando orden para {symbol}")
                return None
                
        except Exception as e:
            logger.error(f"❌ Error colocando orden: {str(e)}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            return None

    def place_plan_order(self, symbol, side, trigger_price, order_type, size, 
                         price=None, plan_type='normal_plan'):
        """Colocar orden de plan (TP/SL) - CORREGIDO"""
        try:
            logger.info(f"📤 Colocando orden plan: {symbol} {side} trigger={trigger_price}")
            
            endpoint = '/api/v2/mix/order/place-plan-order'
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
            
            logger.info(f"📝 Body orden plan: {json.dumps(body, indent=2)}")
            
            data = self._make_request('POST', endpoint, body=body)
            
            if data:
                logger.info(f"✅ Orden plan colocada: {data}")
                return data
            else:
                logger.warning(f"⚠️ Error colocando orden plan para {symbol}")
                return None
                
        except Exception as e:
            logger.error(f"❌ Error colocando plan order: {str(e)}")
            return None

    def set_leverage(self, symbol, leverage, hold_side='long'):
        """Configurar apalancamiento - CORREGIDO"""
        try:
            logger.info(f"⚙️ Configurando apalancamiento {leverage}x para {symbol} ({hold_side})")
            
            endpoint = '/api/v2/mix/account/set-leverage'
            body = {
                'symbol': symbol,
                'productType': 'USDT-FUTURES',
                'marginCoin': 'USDT',
                'leverage': str(leverage),
                'holdSide': hold_side
            }
            
            data = self._make_request('POST', endpoint, body=body)
            
            if data:
                logger.info(f"✅ Apalancamiento {leverage}x configurado para {symbol}")
                return True
            else:
                logger.warning(f"⚠️ Error configurando leverage para {symbol}")
                return False
                
        except Exception as e:
            logger.error(f"❌ Error en set_leverage: {str(e)}")
            return False

    def get_positions(self, symbol=None, product_type='USDT-FUTURES'):
        """Obtener posiciones abiertas"""
        try:
            endpoint = '/api/v2/mix/position/all-position'
            params = {'productType': product_type, 'marginCoin': 'USDT'}
            if symbol:
                params['symbol'] = symbol
            
            data = self._make_request('GET', endpoint, params)
            
            if data is None and product_type == 'USDT-FUTURES':
                return self.get_positions(symbol, 'USDT-MIX')
            
            return data if data else []
            
        except Exception as e:
            logger.error(f"❌ Error obteniendo posiciones: {str(e)}")
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
            endpoint = '/api/v2/mix/market/candles'
            params = {
                'symbol': symbol,
                'productType': 'USDT-FUTURES',
                'granularity': bitget_interval,
                'limit': limit
            }
            
            data = self._make_request('GET', endpoint, params)
            
            if data is None:
                # Intentar con USDT-MIX
                params['productType'] = 'USDT-MIX'
                data = self._make_request('GET', endpoint, params)
            
            if data:
                # Bitget devuelve las velas en orden descendente (más reciente primero)
                # Las invertimos para tener el orden cronológico
                if data and len(data) > 0:
                    return list(reversed(data))
            
            return None
            
        except Exception as e:
            logger.error(f"❌ Error en get_klines: {str(e)}")
            return None

    def get_ticker_price(self, symbol):
        """Obtener precio actual del ticker"""
        try:
            endpoint = '/api/v2/mix/market/ticker'
            params = {
                'symbol': symbol,
                'productType': 'USDT-FUTURES'
            }
            
            data = self._make_request('GET', endpoint, params)
            
            if data:
                return float(data.get('last', 0))
            
            return None
        except Exception as e:
            logger.error(f"❌ Error obteniendo precio ticker: {str(e)}")
            return None

# ---------------------------
# FUNCIONES DE OPERACIONES BITGET - CORREGIDAS
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
    
    logger.info("=" * 70)
    logger.info("🚀 EJECUTANDO OPERACIÓN REAL EN BITGET")
    logger.info(f"   Símbolo: {simbolo}")
    logger.info(f"   Tipo: {tipo_operacion}")
    logger.info(f"   Apalancamiento: {leverage}x")
    logger.info(f"   Capital: ${capital_usd}")
    logger.info("=" * 70)
    
    try:
        # 1. Verificar que el cliente esté inicializado
        if not bitget_client:
            logger.error("❌ Cliente Bitget no inicializado")
            return None
        
        # 2. Obtener precio actual usando ticker (más confiable)
        precio_actual = bitget_client.get_ticker_price(simbolo)
        if not precio_actual or precio_actual <= 0:
            logger.error(f"❌ No se pudo obtener precio válido para {simbolo}")
            return None
        
        logger.info(f"💰 Precio actual {simbolo}: {precio_actual:.8f}")
        
        # 3. Obtener información del símbolo
        symbol_info = bitget_client.get_symbol_info(simbolo)
        if not symbol_info:
            logger.error(f"❌ No se pudo obtener info de {simbolo}")
            return None
        
        logger.info(f"📊 Info símbolo: {symbol_info.get('symbolName', 'N/A')}")
        logger.info(f"   Tamaño mínimo: {symbol_info.get('minTradeNum', 'N/A')}")
        logger.info(f"   Multiplicador: {symbol_info.get('sizeMultiplier', 'N/A')}")
        
        # 4. Configurar apalancamiento
        hold_side = 'long' if tipo_operacion == 'LONG' else 'short'
        logger.info(f"⚙️ Configurando apalancamiento {leverage}x para {hold_side}")
        
        leverage_ok = bitget_client.set_leverage(simbolo, leverage, hold_side)
        if not leverage_ok:
            logger.error("❌ Error configurando apalancamiento. Continuando...")
            # Continuamos aunque falle, puede que ya esté configurado
        
        time.sleep(1)  # Esperar para que se aplique el leverage
        
        # 5. Calcular tamaño de la posición
        size_multiplier = float(symbol_info.get('sizeMultiplier', '1'))
        min_trade_num = float(symbol_info.get('minTradeNum', '0.001'))
        
        # Calcular cantidad en USD
        cantidad_usd = capital_usd * leverage
        logger.info(f"💵 Valor nocional: ${cantidad_usd:.2f} (${capital_usd} × {leverage}x)")
        
        # Convertir a cantidad de contratos
        cantidad_contratos = cantidad_usd / precio_actual
        logger.info(f"📈 Cantidad bruta: {cantidad_contratos:.8f} contratos")
        
        # Aplicar multiplicador de tamaño
        cantidad_contratos = cantidad_contratos / size_multiplier
        logger.info(f"📈 Después de multiplicador ({size_multiplier}): {cantidad_contratos:.8f}")
        
        # Redondear según las reglas del exchange
        # Para la mayoría de símbolos, redondear a 3 decimales
        cantidad_contratos = round(cantidad_contratos, 3)
        
        # Verificar mínimo
        if cantidad_contratos < min_trade_num:
            logger.warning(f"⚠️ Cantidad {cantidad_contratos} < mínimo {min_trade_num}, ajustando...")
            cantidad_contratos = min_trade_num
        
        logger.info(f"🎯 Cantidad final: {cantidad_contratos} contratos")
        logger.info(f"💎 Valor real: ${cantidad_contratos * precio_actual:.2f}")
        
        # 6. Calcular TP y SL (2% fijo)
        if tipo_operacion == "LONG":
            sl_porcentaje = 0.02  # 2% SL
            tp_porcentaje = 0.04  # 4% TP (RR 2:1)
            stop_loss = precio_actual * (1 - sl_porcentaje)
            take_profit = precio_actual * (1 + tp_porcentaje)
            logger.info(f"🔼 LONG: Entrada {precio_actual:.8f}, SL {stop_loss:.8f} (-2%), TP {take_profit:.8f} (+4%)")
        else:
            sl_porcentaje = 0.02
            tp_porcentaje = 0.04
            stop_loss = precio_actual * (1 + sl_porcentaje)
            take_profit = precio_actual * (1 - tp_porcentaje)
            logger.info(f"🔽 SHORT: Entrada {precio_actual:.8f}, SL {stop_loss:.8f} (+2%), TP {take_profit:.8f} (-4%)")
        
        # 7. Abrir posición
        side = 'open_long' if tipo_operacion == 'LONG' else 'open_short'
        logger.info(f"📤 Abriendo posición: {side} con {cantidad_contratos} contratos")
        
        orden_entrada = bitget_client.place_order(
            symbol=simbolo,
            side=side,
            order_type='market',
            size=cantidad_contratos
        )
        
        if not orden_entrada:
            logger.error("❌ Error abriendo posición")
            return None
        
        order_id = orden_entrada.get('orderId', 'N/A')
        logger.info(f"✅ Posición abierta - ID: {order_id}")
        logger.info(f"📊 Detalles orden: {json.dumps(orden_entrada, indent=2)}")
        
        time.sleep(2)  # Esperar a que se complete la orden
        
        # 8. Colocar Stop Loss
        logger.info(f"🛑 Colocando Stop Loss en {stop_loss:.8f}")
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
            sl_id = orden_sl.get('orderId', 'N/A')
            logger.info(f"✅ Stop Loss configurado - ID: {sl_id}")
        else:
            logger.warning("⚠️ Error configurando Stop Loss. Intentando manualmente...")
            # Podríamos intentar colocarlo como orden normal si falla el plan
        
        time.sleep(1)
        
        # 9. Colocar Take Profit
        logger.info(f"🎯 Colocando Take Profit en {take_profit:.8f}")
        
        orden_tp = bitget_client.place_plan_order(
            symbol=simbolo,
            side=sl_side,
            trigger_price=take_profit,
            order_type='market',
            size=cantidad_contratos,
            plan_type='normal_plan'
        )
        
        if orden_tp:
            tp_id = orden_tp.get('orderId', 'N/A')
            logger.info(f"✅ Take Profit configurado - ID: {tp_id}")
        else:
            logger.warning("⚠️ Error configurando Take Profit")
        
        # 10. Verificar posición abierta
        logger.info("🔍 Verificando posición abierta...")
        positions = bitget_client.get_positions(simbolo)
        
        position_info = {}
        if positions:
            for pos in positions:
                if pos.get('symbol') == simbolo:
                    position_info = pos
                    break
        
        # 11. Retornar información de la operación
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
            'symbol': simbolo,
            'position_info': position_info,
            'order_id': order_id
        }
        
        logger.info("=" * 70)
        logger.info("✅ OPERACIÓN EJECUTADA EXITOSAMENTE")
        logger.info(f"   📋 ID Orden: {order_id}")
        logger.info(f"   📊 Contratos: {cantidad_contratos}")
        logger.info(f"   💰 Entrada: {precio_actual:.8f}")
        logger.info(f"   🛑 SL: {stop_loss:.8f} (-2%)")
        logger.info(f"   🎯 TP: {take_profit:.8f} (+4%)")
        logger.info(f"   ⚡ Leverage: {leverage}x")
        logger.info(f"   💵 Capital: ${capital_usd}")
        logger.info("=" * 70)
        
        return operacion_data
        
    except Exception as e:
        logger.error(f"❌ Error ejecutando operación: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
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
        
        # Configurar logging específico para errores
        self.error_logger = logging.getLogger('bitget_errors')
        error_handler = logging.FileHandler('bitget_errors.log')
        error_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        self.error_logger.addHandler(error_handler)
        self.error_logger.setLevel(logging.ERROR)
        
        self.cargar_estado()
        
        # NUEVO: Inicializar cliente Bitget si están las credenciales
        self.bitget_client = None
        bitget_api_key = config.get('bitget_api_key')
        bitget_api_secret = config.get('bitget_api_secret')
        bitget_passphrase = config.get('bitget_passphrase')
        
        if bitget_api_key and bitget_api_secret and bitget_passphrase:
            logger.info("🔐 Inicializando cliente Bitget...")
            logger.info(f"   API Key: {bitget_api_key[:10]}...")
            logger.info(f"   Passphrase: {bitget_passphrase[:3]}...")
            
            try:
                self.bitget_client = BitgetClient(
                    api_key=bitget_api_key,
                    api_secret=bitget_api_secret,
                    passphrase=bitget_passphrase
                )
                
                if self.bitget_client.verificar_credenciales():
                    logger.info("✅ Cliente Bitget inicializado y verificado")
                    
                    # Verificar configuración recomendada
                    logger.info("📋 CONFIGURACIÓN RECOMENDADA EN BITGET:")
                    logger.info("   1. Ir a Futures → Configuración de trading")
                    logger.info("   2. Modo de posición: Modo de cobertura")
                    logger.info("   3. Prevención de auto-trading: Modo por defecto")
                    logger.info("   4. Unidad de futuros: Costo")
                    logger.info("   5. Precio de activación TP/SL: Último precio")
                    
                else:
                    logger.warning("⚠️ No se pudieron verificar las credenciales de Bitget")
                    logger.warning("   Revise que las claves sean correctas y tengan permisos de trading")
                    
            except Exception as e:
                logger.error(f"❌ Error inicializando cliente Bitget: {str(e)}")
                self.bitget_client = None
        else:
            logger.warning("⚠️ Credenciales Bitget incompletas en configuración")
            logger.warning("   BITGET_API_KEY, BITGET_SECRET_KEY, BITGET_PASSPHRASE requeridos")
        
        # NUEVO: Configuración de operaciones automáticas
        self.ejecutar_operaciones_automaticas = config.get('ejecutar_operaciones_automaticas', False)
        self.capital_por_operacion = config.get('capital_por_operacion', 50)
        self.leverage_por_defecto = config.get('leverage_por_defecto', 20)
        
        logger.info(f"🤖 Modo auto-trading: {'✅ ACTIVADO' if self.ejecutar_operaciones_automaticas else '❌ DESACTIVADO'}")
        logger.info(f"💰 Capital por operación: ${self.capital_por_operacion}")
        logger.info(f"⚡ Apalancamiento: {self.leverage_por_defecto}x")
        
        # Optimización de parámetros
        parametros_optimizados = None
        if self.auto_optimize:
            try:
                ia = OptimizadorIA(log_path=self.log_path, min_samples=config.get('min_samples_optimizacion', 15))
                parametros_optimizados = ia.buscar_mejores_parametros()
            except Exception as e:
                logger.error(f"❌ Error en optimización automática: {str(e)}")
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
                
                logger.info("📂 Cargando estado anterior...")
                
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
                logger.info(f"   🚀 Breakouts detectados: {len(self.breakouts_detectados)}")
                
        except Exception as e:
            logger.error(f"❌ Error cargando estado previo: {str(e)}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            logger.info("🆕 Se iniciará con estado limpio")

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
                'timestamp_guardado': datetime.now().isoformat(),
                'bitget_connected': self.bitget_client is not None,
                'auto_trading': self.ejecutar_operaciones_automaticas
            }
            
            with open(self.estado_file, 'w', encoding='utf-8') as f:
                json.dump(estado, f, indent=2, ensure_ascii=False)
            
            logger.info("💾 Estado guardado correctamente")
            
        except Exception as e:
            logger.error(f"❌ Error guardando estado: {str(e)}")
            self.error_logger.error(f"Error guardando estado: {str(e)}")

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
                    logger.debug(f"Error probando config {timeframe}/{num_velas} para {simbolo}: {str(e)}")
                    continue
        
        if mejor_config:
            self.config_optima_por_simbolo[simbolo] = mejor_config
            self.ultima_busqueda_config[simbolo] = datetime.now()
            logger.info(f"   ✅ Config óptima: {mejor_config['timeframe']} - {mejor_config['num_velas']} velas - Ancho: {mejor_config['ancho_canal']:.1f}%")
        else:
            logger.warning(f"   ⚠️ No se encontró configuración óptima para {simbolo}")
        
        return mejor_config

    def obtener_datos_mercado_config(self, simbolo, timeframe, num_velas):
        """Obtiene datos con configuración específica usando API de Bitget"""
        # Usar API de Bitget si está disponible
        if self.bitget_client:
            try:
                candles = self.bitget_client.get_klines(simbolo, timeframe, num_velas + 14)
                if not candles or len(candles) == 0:
                    logger.warning(f"   ⚠️ No hay datos de Bitget para {simbolo} {timeframe}")
                    return None
                
                # Procesar datos de Bitget
                maximos = []
                minimos = []
                cierres = []
                tiempos = []
                
                for i, candle in enumerate(candles):
                    # Formato Bitget: [timestamp, open, high, low, close, volume, ...]
                    try:
                        maximos.append(float(candle[2]))  # high
                        minimos.append(float(candle[3]))  # low
                        cierres.append(float(candle[4]))  # close
                        tiempos.append(i)
                    except (IndexError, ValueError) as e:
                        logger.warning(f"Error procesando vela {i} para {simbolo}: {str(e)}")
                        continue
                
                if len(maximos) < num_velas:
                    logger.warning(f"   ⚠️ Datos insuficientes para {simbolo}: {len(maximos)} < {num_velas}")
                    return None
                
                return {
                    'maximos': maximos[-num_velas:],
                    'minimos': minimos[-num_velas:],
                    'cierres': cierres[-num_velas:],
                    'tiempos': tiempos[-num_velas:],
                    'precio_actual': cierres[-1] if cierres else 0,
                    'timeframe': timeframe,
                    'num_velas': num_velas
                }
            except Exception as e:
                logger.error(f"   ❌ Error obteniendo datos de Bitget para {simbolo}: {str(e)}")
                self.error_logger.error(f"Error obteniendo datos Bitget {simbolo}: {str(e)}")
                # Fallback a Binance si falla Bitget
        
        # Fallback a Binance API
        try:
            url = "https://api.binance.com/api/v3/klines"
            params = {'symbol': simbolo, 'interval': timeframe, 'limit': num_velas + 14}
            
            respuesta = requests.get(url, params=params, timeout=10)
            
            if respuesta.status_code != 200:
                logger.error(f"   ❌ Error Binance API: {respuesta.status_code}")
                return None
            
            datos = respuesta.json()
            
            if not isinstance(datos, list) or len(datos) == 0:
                logger.warning(f"   ⚠️ No hay datos de Binance para {simbolo}")
                return None
            
            maximos = [float(vela[2]) for vela in datos]
            minimos = [float(vela[3]) for vela in datos]
            cierres = [float(vela[4]) for vela in datos]
            tiempos = list(range(len(datos)))
            
            return {
                'maximos': maximos[-num_velas:],
                'minimos': minimos[-num_velas:],
                'cierres': cierres[-num_velas:],
                'tiempos': tiempos[-num_velas:],
                'precio_actual': cierres[-1] if cierres else 0,
                'timeframe': timeframe,
                'num_velas': num_velas
            }
        except Exception as e:
            logger.error(f"   ❌ Error obteniendo datos de Binance para {simbolo}: {str(e)}")
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
            logger.error(f"Error calculando canal regresión: {str(e)}")
            return None

    def enviar_alerta_breakout(self, simbolo, tipo_breakout, info_canal, datos_mercado, config_optima):
        """
        Envía alerta de BREAKOUT detectado a Telegram con gráfico
        """
        precio_cierre = datos_mercado['cierres'][-1]
        resistencia = info_canal['resistencia']
        soporte = info_canal['soporte']
        direccion_canal = info_canal['direccion']
        
        # Determinar tipo de ruptura
        if tipo_breakout == "BREAKOUT_LONG":
            emoji_principal = "🚀"
            tipo_texto = "RUPTURA ALCISTA"
            nivel_roto = f"Resistencia: {resistencia:.8f}"
            direccion_emoji = "⬆️"
            contexto = f"Canal {direccion_canal} → Ruptura de RESISTENCIA"
            expectativa = "posible entrada en long si el precio reingresa al canal"
        else:  # BREAKOUT_SHORT
            emoji_principal = "📉"
            tipo_texto = "RUPTURA BAJISTA"
            nivel_roto = f"Soporte: {soporte:.8f}"
            direccion_emoji = "⬇️"
            contexto = f"Canal {direccion_canal} → Ruptura de SOPORTE"
            expectativa = "posible entrada en short si el precio reingresa al canal"
        
        # Mensaje de alerta
        mensaje = f"""
{emoji_principal} <b>¡BREAKOUT DETECTADO! - {simbolo}</b>
⚠️ <b>{tipo_texto}</b> {direccion_emoji}
⏰ <b>Hora:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
💰 <b>Precio actual:</b> {precio_cierre:.8f}
📊 <b>{nivel_roto}</b>
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
                    logger.info(f"     ⚠️ Alerta enviada sin gráfico")
                    
            except Exception as e:
                logger.error(f"     ❌ Error enviando alerta de breakout: {str(e)}")
                self.error_logger.error(f"Error enviando alerta breakout {simbolo}: {str(e)}")
        else:
            logger.info(f"     📢 Breakout detectado en {simbolo} (sin Telegram)")

    def generar_grafico_breakout(self, simbolo, info_canal, datos_mercado, tipo_breakout, config_optima):
        """
        Genera gráfico especial para el momento del BREAKOUT
        """
        try:
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
                    return None
            else:
                # Fallback a Binance
                return None
            
            # Calcular líneas del canal
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
            
            # Crear gráfico
            apds = [
                mpf.make_addplot(df['Resistencia'], color='#5444ff', linestyle='--', width=2, panel=0),
                mpf.make_addplot(df['Soporte'], color="#5444ff", linestyle='--', width=2, panel=0),
            ]
            
            # Marcar zona de breakout
            precio_breakout = datos_mercado['precio_actual']
            breakout_line = [precio_breakout] * len(df)
            
            if tipo_breakout == "BREAKOUT_LONG":
                color_breakout = "#00FF00"
                titulo_extra = "🚀 RUPTURA ALCISTA"
            else:
                color_breakout = '#FF0000'
                titulo_extra = "📉 RUPTURA BAJISTA"
            
            apds.append(mpf.make_addplot(breakout_line, color=color_breakout, linestyle='-', width=3, panel=0, alpha=0.8))
            
            # Crear gráfico
            fig, axes = mpf.plot(df, type='candle', style='nightclouds',
                               title=f'{simbolo} | {titulo_extra} | {config_optima["timeframe"]} | ⏳ ESPERANDO REENTRY',
                               ylabel='Precio',
                               addplot=apds,
                               volume=False,
                               returnfig=True,
                               figsize=(14, 8))
            
            buf = BytesIO()
            plt.savefig(buf, format='png', dpi=100, bbox_inches='tight', facecolor='#1a1a1a')
            buf.seek(0)
            plt.close(fig)
            return buf
            
        except Exception as e:
            logger.error(f"⚠️ Error generando gráfico de breakout: {str(e)}")
            return None

    def detectar_breakout(self, simbolo, info_canal, datos_mercado):
        """Detecta si el precio ha ROTO el canal"""
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
        
        # Verificar si ya hubo un breakout reciente
        if simbolo in self.breakouts_detectados:
            ultimo_breakout = self.breakouts_detectados[simbolo]
            tiempo_desde_ultimo = (datetime.now() - ultimo_breakout['timestamp']).total_seconds() / 60
            
            if tiempo_desde_ultimo < 115:
                logger.info(f"     ⏰ {simbolo} - Breakout detectado recientemente ({tiempo_desde_ultimo:.1f} min), omitiendo...")
                return None
        
        # Detectar breakout según dirección del canal
        margen = 0.001 * precio_cierre
        
        if direccion == "🟢 ALCISTA":
            # En canal alcista, breakout long cuando rompe resistencia
            if precio_cierre > (resistencia + margen):
                logger.info(f"     🚀 {simbolo} - BREAKOUT_LONG detectado: {precio_cierre:.8f} > Resistencia: {resistencia:.8f}")
                return "BREAKOUT_LONG"
                
        elif direccion == "🔴 BAJISTA":
            # En canal bajista, breakout short cuando rompe soporte
            if precio_cierre < (soporte - margen):
                logger.info(f"     📉 {simbolo} - BREAKOUT_SHORT detectado: {precio_cierre:.8f} < Soporte: {soporte:.8f}")
                return "BREAKOUT_SHORT"
        
        return None

    def detectar_reentry(self, simbolo, info_canal, datos_mercado):
        """Detecta si el precio ha REINGRESADO al canal"""
        if simbolo not in self.esperando_reentry:
            return None
        
        breakout_info = self.esperando_reentry[simbolo]
        tipo_breakout = breakout_info['tipo']
        timestamp_breakout = breakout_info['timestamp']
        tiempo_desde_breakout = (datetime.now() - timestamp_breakout).total_seconds() / 60
        
        # Timeout de 120 minutos (2 horas)
        if tiempo_desde_breakout > 120:
            logger.info(f"     ⏰ {simbolo} - Timeout de reentry (>120 min), cancelando espera")
            del self.esperando_reentry[simbolo]
            
            if simbolo in self.breakouts_detectados:
                del self.breakouts_detectados[simbolo]
            
            return None
        
        precio_actual = datos_mercado['precio_actual']
        resistencia = info_canal['resistencia']
        soporte = info_canal['soporte']
        stoch_k = info_canal['stoch_k']
        stoch_d = info_canal['stoch_d']
        
        # Tolerancia para considerar reentry
        tolerancia = 0.002 * precio_actual
        
        if tipo_breakout == "BREAKOUT_LONG":
            # Reentry para LONG: precio vuelve al canal cerca del soporte con stoch oversold
            if soporte - tolerancia <= precio_actual <= resistencia + tolerancia:
                distancia_soporte = abs(precio_actual - soporte)
                
                if distancia_soporte <= tolerancia and stoch_k <= 35 and stoch_d <= 35:
                    logger.info(f"     ✅ {simbolo} - REENTRY LONG confirmado! Entrada en soporte con Stoch oversold")
                    
                    if simbolo in self.breakouts_detectados:
                        del self.breakouts_detectados[simbolo]
                    
                    return "LONG"
                    
        elif tipo_breakout == "BREAKOUT_SHORT":
            # Reentry para SHORT: precio vuelve al canal cerca de resistencia con stoch overbought
            if soporte - tolerancia <= precio_actual <= resistencia + tolerancia:
                distancia_resistencia = abs(precio_actual - resistencia)
                
                if distancia_resistencia <= tolerancia and stoch_k >= 65 and stoch_d >= 65:
                    logger.info(f"     ✅ {simbolo} - REENTRY SHORT confirmado! Entrada en resistencia con Stoch overbought")
                    
                    if simbolo in self.breakouts_detectados:
                        del self.breakouts_detectados[simbolo]
                    
                    return "SHORT"
        
        return None

    def calcular_niveles_entrada(self, tipo_operacion, info_canal, precio_actual):
        if not info_canal:
            return None, None, None
        
        resistencia = info_canal['resistencia']
        soporte = info_canal['soporte']
        ancho_canal = resistencia - soporte
        
        if tipo_operacion == "LONG":
            precio_entrada = precio_actual
            stop_loss = soporte * 0.995  # 0.5% por debajo del soporte
            take_profit = precio_entrada + (ancho_canal * 0.7)  # 70% del ancho del canal
        else:
            precio_entrada = precio_actual
            stop_loss = resistencia * 1.005  # 0.5% por encima de la resistencia
            take_profit = precio_entrada - (ancho_canal * 0.7)  # 70% del ancho del canal
        
        # Asegurar ratio riesgo/beneficio mínimo
        riesgo = abs(precio_entrada - stop_loss)
        beneficio = abs(take_profit - precio_entrada)
        ratio_rr = beneficio / riesgo if riesgo > 0 else 0
        
        if ratio_rr < self.config.get('min_rr_ratio', 1.5):
            if tipo_operacion == "LONG":
                take_profit = precio_entrada + (riesgo * self.config['min_rr_ratio'])
            else:
                take_profit = precio_entrada - (riesgo * self.config['min_rr_ratio'])
        
        return precio_entrada, take_profit, stop_loss

    def escanear_mercado(self):
        """Escanea el mercado con estrategia Breakout + Reentry"""
        logger.info(f"\n🔍 Escaneando {len(self.config.get('symbols', []))} símbolos (Breakout + Reentry)...")
        
        senales_encontradas = 0
        symbols = self.config.get('symbols', [])
        
        for idx, simbolo in enumerate(symbols, 1):
            try:
                logger.info(f"\n[{idx}/{len(symbols)}] Analizando {simbolo}...")
                
                if simbolo in self.operaciones_activas:
                    logger.info(f"   ⚡ {simbolo} - Operación activa, omitiendo...")
                    continue
                
                config_optima = self.buscar_configuracion_optima_simbolo(simbolo)
                if not config_optima:
                    logger.warning(f"   ❌ {simbolo} - No se encontró configuración válida")
                    continue
                
                datos_mercado = self.obtener_datos_mercado_config(
                    simbolo, config_optima['timeframe'], config_optima['num_velas']
                )
                
                if not datos_mercado:
                    logger.warning(f"   ❌ {simbolo} - Error obteniendo datos")
                    continue
                
                info_canal = self.calcular_canal_regresion_config(datos_mercado, config_optima['num_velas'])
                if not info_canal:
                    logger.warning(f"   ❌ {simbolo} - Error calculando canal")
                    continue
                
                # Estado del stoch
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
                    posicion = "📍 DENTRO"
                
                logger.info(
    f"📊 {simbolo} - {config_optima['timeframe']} - {config_optima['num_velas']}v | "
    f"{info_canal['direccion']} ({info_canal['angulo_tendencia']:.1f}° - {info_canal['fuerza_texto']}) | "
    f"Ancho: {info_canal['ancho_canal_porcentual']:.1f}% - Stoch: {info_canal['stoch_k']:.1f}/{info_canal['stoch_d']:.1f} {estado_stoch} | "
    f"Precio: {posicion}"
                )
                
                # Filtrar por calidad del canal
                if (info_canal['nivel_fuerza'] < 2 or 
                    abs(info_canal['coeficiente_pearson']) < 0.4 or 
                    info_canal['r2_score'] < 0.4):
                    logger.debug(f"   ⚠️ Canal de baja calidad, omitiendo")
                    continue
                
                # 1. Detectar breakout si no estamos esperando reentry
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
                        
                        logger.info(f"     🎯 {simbolo} - Breakout {tipo_breakout} registrado, esperando reingreso...")
                        
                        # Enviar alerta de breakout
                        self.enviar_alerta_breakout(simbolo, tipo_breakout, info_canal, datos_mercado, config_optima)
                        continue
                
                # 2. Detectar reentry si estamos esperando
                tipo_operacion = self.detectar_reentry(simbolo, info_canal, datos_mercado)
                if not tipo_operacion:
                    continue
                
                # 3. Calcular niveles de entrada
                precio_entrada, tp, sl = self.calcular_niveles_entrada(
                    tipo_operacion, info_canal, datos_mercado['precio_actual']
                )
                
                if not precio_entrada or not tp or not sl:
                    logger.warning(f"   ⚠️ {simbolo} - Niveles de entrada inválidos")
                    continue
                
                # Verificar breakout reciente
                if simbolo in self.breakout_history:
                    ultimo_breakout = self.breakout_history[simbolo]
                    tiempo_desde_ultimo = (datetime.now() - ultimo_breakout).total_seconds() / 3600
                    
                    if tiempo_desde_ultimo < 2:
                        logger.info(f"   ⏳ {simbolo} - Señal reciente ({tiempo_desde_ultimo:.1f}h), omitiendo...")
                        continue
                
                # Generar señal
                breakout_info = self.esperando_reentry[simbolo]
                self.generar_senal_operacion(
                    simbolo, tipo_operacion, precio_entrada, tp, sl, 
                    info_canal, datos_mercado, config_optima, breakout_info
                )
                
                senales_encontradas += 1
                self.breakout_history[simbolo] = datetime.now()
                del self.esperando_reentry[simbolo]
                
            except Exception as e:
                logger.error(f"❌ Error analizando {simbolo}: {str(e)}")
                self.error_logger.error(f"Error analizando {simbolo}: {str(e)}")
                continue
        
        # Mostrar estados actuales
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
            logger.info("ℹ️ No se encontraron señales en este ciclo")
        
        return senales_encontradas

    def generar_senal_operacion(self, simbolo, tipo_operacion, precio_entrada, tp, sl,
                            info_canal, datos_mercado, config_optima, breakout_info=None):
        """Genera y envía señal de operación con info de breakout"""
        if simbolo in self.senales_enviadas:
            logger.info(f"   ⚠️ Señal ya enviada para {simbolo}, omitiendo...")
            return
        
        if precio_entrada is None or tp is None or sl is None:
            logger.error(f"    ❌ Niveles inválidos para {simbolo}, omitiendo señal")
            return
        
        # Calcular métricas
        riesgo = abs(precio_entrada - sl)
        beneficio = abs(tp - precio_entrada)
        ratio_rr = beneficio / riesgo if riesgo > 0 else 0
        sl_percent = abs((sl - precio_entrada) / precio_entrada) * 100
        tp_percent = abs((tp - precio_entrada) / precio_entrada) * 100
        
        stoch_estado = "📉 SOBREVENTA" if tipo_operacion == "LONG" else "📈 SOBRECOMPRA"
        
        # Información de breakout
        breakout_texto = ""
        if breakout_info:
            tiempo_breakout = (datetime.now() - breakout_info['timestamp']).total_seconds() / 60
            breakout_texto = f"""
🚀 <b>BREAKOUT + REENTRY DETECTADO:</b>
⏰ Tiempo desde breakout: {tiempo_breakout:.1f} minutos
💰 Precio breakout: {breakout_info['precio_breakout']:.8f}
"""
        
        mensaje = f"""
🎯 <b>SEÑAL DE {tipo_operacion} - {simbolo}</b>
{breakout_texto}
⏱️ <b>Configuración óptima:</b>
📊 Timeframe: {config_optima['timeframe']}
🕯️ Velas: {config_optima['num_velas']}
📏 Ancho Canal: {info_canal['ancho_canal_porcentual']:.1f}% ⭐
💰 <b>Precio Actual:</b> {datos_mercado['precio_actual']:.8f}
🎯 <b>Entrada:</b> {precio_entrada:.8f}
🛑 <b>Stop Loss:</b> {sl:.8f}
🎯 <b>Take Profit:</b> {tp:.8f}
📊 <b>Ratio R/B:</b> {ratio_rr:.2f}:1
🎯 <b>SL:</b> {sl_percent:.2f}%
🎯 <b>TP:</b> {tp_percent:.2f}%
💰 <b>Riesgo:</b> {riesgo:.8f}
🎯 <b>Beneficio Objetivo:</b> {beneficio:.8f}
📈 <b>Tendencia:</b> {info_canal['direccion']}
💪 <b>Fuerza:</b> {info_canal['fuerza_texto']}
📏 <b>Ángulo:</b> {info_canal['angulo_tendencia']:.1f}°
📊 <b>Pearson:</b> {info_canal['coeficiente_pearson']:.3f}
🎯 <b>R² Score:</b> {info_canal['r2_score']:.3f}
🎰 <b>Stochástico:</b> {stoch_estado}
📊 <b>Stoch K:</b> {info_canal['stoch_k']:.1f}
📈 <b>Stoch D:</b> {info_canal['stoch_d']:.1f}
⏰ <b>Hora:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
💡 <b>Estrategia:</b> BREAKOUT + REENTRY con confirmación Stochastic
        """
        
        token = self.config.get('telegram_token')
        chat_ids = self.config.get('telegram_chat_ids', [])
        
        if token and chat_ids:
            try:
                logger.info(f"     📊 Generando gráfico para {simbolo}...")
                buf = self.generar_grafico_profesional(simbolo, info_canal, datos_mercado, 
                                                      precio_entrada, tp, sl, tipo_operacion)
                if buf:
                    logger.info(f"     📨 Enviando gráfico por Telegram...")
                    self.enviar_grafico_telegram(buf, token, chat_ids)
                    time.sleep(1)
                
                self._enviar_telegram_simple(mensaje, token, chat_ids)
                logger.info(f"     ✅ Señal {tipo_operacion} para {simbolo} enviada")
                
            except Exception as e:
                logger.error(f"     ❌ Error enviando señal: {str(e)}")
                self.error_logger.error(f"Error enviando señal {simbolo}: {str(e)}")
        
        # EJECUTAR OPERACIÓN AUTOMÁTICA EN BITGET
        if self.ejecutar_operaciones_automaticas and self.bitget_client:
            logger.info(f"     🤖 Ejecutando operación automática en Bitget...")
            
            try:
                # Verificar balance antes de operar
                accounts = self.bitget_client.get_account_info()
                balance_suficiente = False
                
                if accounts:
                    for account in accounts:
                        if account.get('marginCoin') == 'USDT':
                            available = float(account.get('available', 0))
                            if available >= self.capital_por_operacion:
                                balance_suficiente = True
                                logger.info(f"     💰 Balance suficiente: {available:.2f} USDT")
                            else:
                                logger.error(f"     ❌ Balance insuficiente: {available:.2f} USDT < {self.capital_por_operacion} USDT")
                                break
                
                if not balance_suficiente:
                    logger.error("     ❌ Balance insuficiente para operar")
                    return
                
                # Ejecutar operación
                operacion_bitget = ejecutar_operacion_bitget(
                    bitget_client=self.bitget_client,
                    simbolo=simbolo,
                    tipo_operacion=tipo_operacion,
                    capital_usd=self.capital_por_operacion,
                    leverage=self.leverage_por_defecto
                )
                
                if operacion_bitget:
                    logger.info(f"     ✅ Operación ejecutada en Bitget para {simbolo}")
                    
                    # Enviar confirmación
                    mensaje_confirmacion = f"""
🤖 <b>OPERACIÓN AUTOMÁTICA EJECUTADA - {simbolo}</b>
✅ <b>Status:</b> EJECUTADA EN BITGET
📊 <b>Tipo:</b> {tipo_operacion}
💰 <b>Capital:</b> ${self.capital_por_operacion}
⚡ <b>Apalancamiento:</b> {self.leverage_por_defecto}x
🎯 <b>Entrada:</b> {operacion_bitget['precio_entrada']:.8f}
🛑 <b>Stop Loss:</b> {operacion_bitget['stop_loss']:.8f}
🎯 <b>Take Profit:</b> {operacion_bitget['take_profit']:.8f}
📋 <b>ID Orden:</b> {operacion_bitget.get('order_id', 'N/A')}
⏰ <b>Timestamp:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
                    """
                    
                    if token and chat_ids:
                        self._enviar_telegram_simple(mensaje_confirmacion, token, chat_ids)
                        
                else:
                    logger.error(f"     ❌ Error ejecutando operación en Bitget para {simbolo}")
                    self.error_logger.error(f"Error ejecutando operación Bitget {simbolo}")
                    
            except Exception as e:
                logger.error(f"     ⚠️ Error en ejecución automática: {str(e)}")
                self.error_logger.error(f"Error ejecución automática {simbolo}: {str(e)}")
        
        # Registrar operación localmente
        self.operaciones_activas[simbolo] = {
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
            'breakout_usado': breakout_info is not None,
            'operacion_ejecutada': self.ejecutar_operaciones_automaticas and self.bitget_client is not None
        }
        
        self.senales_enviadas.add(simbolo)
        self.total_operaciones += 1

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
                        'ancho_canal_relativo', 'ancho_canal_porcentual',
                        'nivel_fuerza', 'timeframe_utilizado', 'velas_utilizadas',
                        'stoch_k', 'stoch_d', 'breakout_usado', 'operacion_ejecutada',
                        'bitget_order_id', 'error_details'
                    ])
                logger.info(f"✅ Log de operaciones inicializado: {self.archivo_log}")
        except Exception as e:
            logger.error(f"❌ Error inicializando log: {str(e)}")
            self.error_logger.error(f"Error inicializando log: {str(e)}")

    def registrar_operacion(self, datos_operacion):
        """Registra una operación en el archivo CSV"""
        try:
            with open(self.archivo_log, 'a', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow([
                    datos_operacion.get('timestamp', ''),
                    datos_operacion.get('symbol', ''),
                    datos_operacion.get('tipo', ''),
                    datos_operacion.get('precio_entrada', 0),
                    datos_operacion.get('take_profit', 0),
                    datos_operacion.get('stop_loss', 0),
                    datos_operacion.get('precio_salida', 0),
                    datos_operacion.get('resultado', ''),
                    datos_operacion.get('pnl_percent', 0),
                    datos_operacion.get('duracion_minutos', 0),
                    datos_operacion.get('angulo_tendencia', 0),
                    datos_operacion.get('pearson', 0),
                    datos_operacion.get('r2_score', 0),
                    datos_operacion.get('ancho_canal_relativo', 0),
                    datos_operacion.get('ancho_canal_porcentual', 0),
                    datos_operacion.get('nivel_fuerza', 1),
                    datos_operacion.get('timeframe_utilizado', 'N/A'),
                    datos_operacion.get('velas_utilizadas', 0),
                    datos_operacion.get('stoch_k', 0),
                    datos_operacion.get('stoch_d', 0),
                    datos_operacion.get('breakout_usado', False),
                    datos_operacion.get('operacion_ejecutada', False),
                    datos_operacion.get('bitget_order_id', ''),
                    datos_operacion.get('error_details', '')
                ])
            logger.debug(f"Operación registrada en log: {datos_operacion.get('symbol')}")
        except Exception as e:
            logger.error(f"❌ Error registrando operación: {str(e)}")
            self.error_logger.error(f"Error registrando operación: {str(e)}")

    def filtrar_operaciones_ultima_semana(self):
        """Filtra operaciones de los últimos 7 días"""
        if not os.path.exists(self.archivo_log):
            return []
        
        try:
            ops_recientes = []
            fecha_limite = datetime.now() - timedelta(days=7)
            
            with open(self.archivo_log, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    try:
                        timestamp_str = row.get('timestamp', '')
                        if not timestamp_str:
                            continue
                            
                        timestamp = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
                        
                        if timestamp >= fecha_limite:
                            ops_recientes.append({
                                'timestamp': timestamp,
                                'symbol': row.get('symbol', ''),
                                'resultado': row.get('resultado', ''),
                                'pnl_percent': float(row.get('pnl_percent', 0)),
                                'tipo': row.get('tipo', ''),
                                'breakout_usado': row.get('breakout_usado', 'False') == 'True',
                                'operacion_ejecutada': row.get('operacion_ejecutada', 'False') == 'True',
                                'bitget_order_id': row.get('bitget_order_id', '')
                            })
                    except Exception as e:
                        logger.debug(f"Error procesando fila del log: {str(e)}")
                        continue
            
            return ops_recientes
            
        except Exception as e:
            logger.error(f"❌ Error filtrando operaciones: {str(e)}")
            return []

    def contar_breakouts_semana(self):
        """Cuenta breakouts detectados en la última semana"""
        ops = self.filtrar_operaciones_ultima_semana()
        breakouts = sum(1 for op in ops if op.get('breakout_usado', False))
        return breakouts

    def generar_reporte_semanal(self):
        """Genera reporte automático cada semana"""
        ops_ultima_semana = self.filtrar_operaciones_ultima_semana()
        
        if not ops_ultima_semana:
            logger.info("ℹ️ No hay datos para generar reporte semanal")
            return None
        
        total_ops = len(ops_ultima_semana)
        wins = sum(1 for op in ops_ultima_semana if op['resultado'] == 'TP')
        losses = sum(1 for op in ops_ultima_semana if op['resultado'] == 'SL')
        winrate = (wins/total_ops*100) if total_ops > 0 else 0
        
        pnl_total = sum(op['pnl_percent'] for op in ops_ultima_semana)
        
        if ops_ultima_semana:
            mejor_op = max(ops_ultima_semana, key=lambda x: x['pnl_percent'])
            peor_op = min(ops_ultima_semana, key=lambda x: x['pnl_percent'])
        else:
            mejor_op = {'symbol': 'N/A', 'tipo': 'N/A', 'pnl_percent': 0}
            peor_op = {'symbol': 'N/A', 'tipo': 'N/A', 'pnl_percent': 0}
        
        ganancias = [op['pnl_percent'] for op in ops_ultima_semana if op['pnl_percent'] > 0]
        perdidas = [abs(op['pnl_percent']) for op in ops_ultima_semana if op['pnl_percent'] < 0]
        
        avg_ganancia = sum(ganancias)/len(ganancias) if ganancias else 0
        avg_perdida = sum(perdidas)/len(perdidas) if perdidas else 0
        
        # Calcular racha actual
        racha_actual = 0
        for op in reversed(ops_ultima_semana):
            if op['resultado'] == 'TP':
                racha_actual += 1
            else:
                break
        
        # Contar operaciones automáticas
        ops_automaticas = sum(1 for op in ops_ultima_semana if op.get('operacion_ejecutada', False))
        ops_manuales = total_ops - ops_automaticas
        
        emoji_resultado = "🟢" if pnl_total > 0 else "🔴" if pnl_total < 0 else "⚪"
        
        mensaje = f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 <b>REPORTE SEMANAL BOT TRADING</b>
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📅 {datetime.now().strftime('%d/%m/%Y')} | Últimos 7 días
<b>RENDIMIENTO GENERAL</b>
{emoji_resultado} PnL Total: <b>{pnl_total:+.2f}%</b>
📈 Win Rate: <b>{winrate:.1f}%</b>
✅ Ganadas: {wins} | ❌ Perdidas: {losses}
<b>ESTADÍSTICAS DETALLADAS</b>
📊 Operaciones totales: {total_ops}
🤖 Automáticas (Bitget): {ops_automaticas}
👤 Manuales (Señales): {ops_manuales}
💰 Ganancia Promedio: +{avg_ganancia:.2f}%
📉 Pérdida Promedio: -{avg_perdida:.2f}%
🔥 Racha actual: {racha_actual} wins
<b>OPERACIONES DESTACADAS</b>
🏆 Mejor: {mejor_op['symbol']} ({mejor_op['tipo']})
   → {mejor_op['pnl_percent']:+.2f}%
⚠️ Peor: {peor_op['symbol']} ({peor_op['tipo']})
   → {peor_op['pnl_percent']:+.2f}%
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🤖 <b>ESTADO DEL BOT:</b>
⚡ Estrategia: Breakout + Reentry
💎 Integración: Bitget API {'✅' if self.bitget_client else '❌'}
🤖 Auto-trading: {'✅ ACTIVADO' if self.ejecutar_operaciones_automaticas else '❌ DESACTIVADO'}
💰 Capital/op: ${self.capital_por_operacion}
⚡ Leverage: {self.leverage_por_defecto}x
💻 Estado: OPERATIVO
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
        return mensaje

    def enviar_reporte_semanal(self):
        """Envía el reporte semanal por Telegram"""
        mensaje = self.generar_reporte_semanal()
        
        if not mensaje:
            logger.warning("ℹ️ No hay datos suficientes para generar reporte")
            return False
        
        token = self.config.get('telegram_token')
        chat_ids = self.config.get('telegram_chat_ids', [])
        
        if token and chat_ids:
            try:
                success = self._enviar_telegram_simple(mensaje, token, chat_ids)
                if success:
                    logger.info("✅ Reporte semanal enviado correctamente")
                    return True
                else:
                    logger.error("❌ Error enviando reporte semanal")
                    return False
            except Exception as e:
                logger.error(f"❌ Error enviando reporte: {str(e)}")
                self.error_logger.error(f"Error enviando reporte: {str(e)}")
                return False
        
        logger.warning("⚠️ No hay configuración de Telegram para enviar reporte")
        return False

    def verificar_envio_reporte_automatico(self):
        """Verifica si debe enviar el reporte semanal (cada lunes a las 9:00)"""
        ahora = datetime.now()
        
        # Lunes (0) entre las 9:00 y 10:00
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
                logger.error(f"❌ Error en envío automático de reporte: {str(e)}")
                self.error_logger.error(f"Error envío automático reporte: {str(e)}")
        
        return False

    def verificar_cierre_operaciones(self):
        """Verifica y cierra operaciones que hayan alcanzado TP o SL"""
        if not self.operaciones_activas:
            return []
        
        operaciones_cerradas = []
        
        for simbolo, operacion in list(self.operaciones_activas.items()):
            try:
                config_optima = self.config_optima_por_simbolo.get(simbolo)
                if not config_optima:
                    logger.debug(f"No hay config óptima para {simbolo}, omitiendo verificación")
                    continue
                
                datos = self.obtener_datos_mercado_config(simbolo, config_optima['timeframe'], 10)  # Solo necesitamos precio actual
                if not datos:
                    logger.warning(f"No se pudieron obtener datos para verificar cierre de {simbolo}")
                    continue
                
                precio_actual = datos['precio_actual']
                tp = operacion['take_profit']
                sl = operacion['stop_loss']
                tipo = operacion['tipo']
                resultado = None
                
                if tipo == "LONG":
                    if precio_actual >= tp:
                        resultado = "TP"
                    elif precio_actual <= sl:
                        resultado = "SL"
                else:
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
                    
                    # Datos para el log
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
                        'ancho_canal_relativo': operacion.get('ancho_canal_relativo', 0),
                        'ancho_canal_porcentual': operacion.get('ancho_canal_porcentual', 0),
                        'nivel_fuerza': operacion.get('nivel_fuerza', 1),
                        'timeframe_utilizado': operacion.get('timeframe_utilizado', 'N/A'),
                        'velas_utilizadas': operacion.get('velas_utilizadas', 0),
                        'stoch_k': operacion.get('stoch_k', 0),
                        'stoch_d': operacion.get('stoch_d', 0),
                        'breakout_usado': operacion.get('breakout_usado', False),
                        'operacion_ejecutada': operacion.get('operacion_ejecutada', False),
                        'bitget_order_id': operacion.get('bitget_order_id', ''),
                        'error_details': ''
                    }
                    
                    # Enviar mensaje de cierre
                    mensaje_cierre = self.generar_mensaje_cierre(datos_operacion)
                    token = self.config.get('telegram_token')
                    chats = self.config.get('telegram_chat_ids', [])
                    
                    if token and chats:
                        try:
                            self._enviar_telegram_simple(mensaje_cierre, token, chats)
                        except Exception as e:
                            logger.error(f"Error enviando mensaje cierre {simbolo}: {str(e)}")
                    
                    # Registrar operación
                    self.registrar_operacion(datos_operacion)
                    operaciones_cerradas.append(simbolo)
                    
                    # Limpiar operación
                    del self.operaciones_activas[simbolo]
                    
                    if simbolo in self.senales_enviadas:
                        self.senales_enviadas.remove(simbolo)
                    
                    self.operaciones_desde_optimizacion += 1
                    
                    logger.info(f"     📊 {simbolo} Operación {resultado} - PnL: {pnl_percent:.2f}%")
                    
            except Exception as e:
                logger.error(f"Error verificando cierre de {simbolo}: {str(e)}")
                self.error_logger.error(f"Error verificando cierre {simbolo}: {str(e)}")
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
        operacion_ejecutada = "🤖 Sí" if datos_operacion.get('operacion_ejecutada', False) else "❌ No"
        
        mensaje = f"""
{emoji} <b>OPERACIÓN CERRADA - {datos_operacion['symbol']}</b>
{color_emoji} <b>RESULTADO: {datos_operacion['resultado']}</b>
📊 Tipo: {datos_operacion['tipo']}
💰 Entrada: {datos_operacion['precio_entrada']:.8f}
🎯 Salida: {datos_operacion['precio_salida']:.8f}
💵 PnL Absoluto: {pnl_absoluto:.8f}
📈 PnL %: {datos_operacion['pnl_percent']:.2f}%
⏰ Duración: {datos_operacion['duracion_minutos']:.1f} minutos
🚀 Breakout+Reentry: {breakout_usado}
🤖 Operación Bitget: {operacion_ejecutada}
📏 Ángulo: {datos_operacion['angulo_tendencia']:.1f}°
📊 Pearson: {datos_operacion['pearson']:.3f}
🎯 R²: {datos_operacion['r2_score']:.3f}
📏 Ancho: {datos_operacion.get('ancho_canal_porcentual', 0):.1f}%
⏱️ TF: {datos_operacion.get('timeframe_utilizado', 'N/A')}
🕯️ Velas: {datos_operacion.get('velas_utilizadas', 0)}
🕒 {datos_operacion['timestamp']}
        """
        return mensaje

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
        """Calcula regresión lineal"""
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
        if len(x) > 1:
            x_range = max(x) - min(x)
            y_range = max(y) - min(y) if max(y) != min(y) else 1
            
            angulo_radianes = math.atan(pendiente * (x_range / y_range))
            angulo_grados = math.degrees(angulo_radianes)
        else:
            angulo_grados = 0
        
        return pearson, angulo_grados

    def clasificar_fuerza_tendencia(self, angulo_grados):
        """Clasifica la fuerza de la tendencia"""
        angulo_abs = abs(angulo_grados)
        
        if angulo_abs < 3:
            return "💔 Muy Débil", 1
        elif angulo_abs < 8:
            return "❤️‍🩹 Débil", 2
        elif angulo_abs < 15:
            return "💛 Moderada", 3
        elif angulo_abs < 25:
            return "💚 Fuerte", 4
        else:
            return "💙 Muy Fuerte", 5

    def determinar_direccion_tendencia(self, angulo_grados, umbral_minimo=1):
        """Determina la dirección de la tendencia"""
        if abs(angulo_grados) < umbral_minimo:
            return "⚪ RANGO"
        elif angulo_grados > 0:
            return "🟢 ALCISTA"
        else:
            return "🔴 BAJISTA"

    def calcular_r2(self, y_real, x, pendiente, intercepto):
        """Calcula coeficiente R²"""
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
        """Genera gráfico profesional para la señal"""
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
                    return None
            else:
                return None
            
            # Calcular líneas del canal
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
            
            # Addplots
            apds = [
                mpf.make_addplot(df['Resistencia'], color='#5444ff', linestyle='--', width=2, panel=0),
                mpf.make_addplot(df['Soporte'], color="#5444ff", linestyle='--', width=2, panel=0),
            ]
            
            # Líneas de entrada, TP y SL
            if precio_entrada and tp and sl:
                entry_line = [precio_entrada] * len(df)
                tp_line = [tp] * len(df)
                sl_line = [sl] * len(df)
                
                apds.append(mpf.make_addplot(entry_line, color='#FFD700', linestyle='-', width=2, panel=0))
                apds.append(mpf.make_addplot(tp_line, color='#00FF00', linestyle='-', width=2, panel=0))
                apds.append(mpf.make_addplot(sl_line, color='#FF0000', linestyle='-', width=2, panel=0))
            
            # Crear gráfico
            fig, axes = mpf.plot(df, type='candle', style='nightclouds',
                               title=f'{simbolo} | {tipo_operacion} | {config_optima["timeframe"]} | Breakout+Reentry',
                               ylabel='Precio',
                               addplot=apds,
                               volume=False,
                               returnfig=True,
                               figsize=(14, 8))
            
            buf = BytesIO()
            plt.savefig(buf, format='png', dpi=100, bbox_inches='tight', facecolor='#1a1a1a')
            buf.seek(0)
            plt.close(fig)
            
            return buf
            
        except Exception as e:
            logger.error(f"❌ Error generando gráfico {simbolo}: {str(e)}")
            return None

    def enviar_grafico_telegram(self, buf, token, chat_ids):
        """Envía gráfico a Telegram"""
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
                
                response = requests.post(url, files=files, data=data, timeout=120)
                
                if response.status_code == 200:
                    exito = True
                    logger.debug(f"Gráfico enviado a chat {chat_id}")
                else:
                    logger.error(f"Error enviando gráfico: {response.status_code} - {response.text}")
                    
            except Exception as e:
                logger.error(f"Error enviando gráfico a Telegram: {str(e)}")
        
        return exito

    def _enviar_telegram_simple(self, mensaje, token, chat_ids):
        """Envía mensaje simple a Telegram"""
        if not token or not chat_ids:
            return False
        
        resultados = []
        
        for chat_id in chat_ids:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            payload = {
                'chat_id': chat_id,
                'text': mensaje,
                'parse_mode': 'HTML',
                'disable_web_page_preview': True
            }
            
            try:
                response = requests.post(url, json=payload, timeout=10)
                resultados.append(response.status_code == 200)
                
                if response.status_code != 200:
                    logger.error(f"Error Telegram chat {chat_id}: {response.status_code} - {response.text}")
                    
            except Exception as e:
                logger.error(f"Exception enviando Telegram: {str(e)}")
                resultados.append(False)
        
        return any(resultados)

    def reoptimizar_periodicamente(self):
        """Re-optimiza parámetros periódicamente"""
        try:
            horas_desde_opt = (datetime.now() - self.ultima_optimizacion).total_seconds() / 3600
            
            if self.operaciones_desde_optimizacion >= 8 or horas_desde_opt >= self.config.get('reevaluacion_horas', 24):
                logger.info("🔄 Iniciando re-optimización automática...")
                
                ia = OptimizadorIA(
                    log_path=self.log_path,
                    min_samples=self.config.get('min_samples_optimizacion', 30)
                )
                
                nuevos_parametros = ia.buscar_mejores_parametros()
                
                if nuevos_parametros:
                    self.actualizar_parametros(nuevos_parametros)
                    self.ultima_optimizacion = datetime.now()
                    self.operaciones_desde_optimizacion = 0
                    logger.info("✅ Parámetros actualizados en tiempo real")
                    
        except Exception as e:
            logger.error(f"❌ Error en re-optimización automática: {str(e)}")
            self.error_logger.error(f"Error re-optimización: {str(e)}")

    def actualizar_parametros(self, nuevos_parametros):
        """Actualiza parámetros del bot"""
        self.config['trend_threshold_degrees'] = nuevos_parametros.get(
            'trend_threshold_degrees', 
            self.config.get('trend_threshold_degrees', 16)
        )
        self.config['min_trend_strength_degrees'] = nuevos_parametros.get(
            'min_trend_strength_degrees', 
            self.config.get('min_trend_strength_degrees', 16)
        )
        self.config['entry_margin'] = nuevos_parametros.get(
            'entry_margin', 
            self.config.get('entry_margin', 0.001)
        )

    def ejecutar_analisis(self):
        """Ejecuta un ciclo completo de análisis"""
        try:
            # Re-optimización periódica
            if random.random() < 0.1:
                self.reoptimizar_periodicamente()
                self.verificar_envio_reporte_automatico()
            
            # Verificar cierre de operaciones
            cierres = self.verificar_cierre_operaciones()
            if cierres:
                logger.info(f"     📊 Operaciones cerradas: {', '.join(cierres)}")
            
            # Guardar estado
            self.guardar_estado()
            
            # Escanear mercado
            return self.escanear_mercado()
            
        except Exception as e:
            logger.error(f"❌ Error en ejecutar_analisis: {str(e)}")
            self.error_logger.error(f"Error ejecutar_analisis: {str(e)}")
            return 0

    def mostrar_resumen_operaciones(self):
        """Muestra resumen de operaciones"""
        logger.info(f"\n📊 RESUMEN OPERACIONES:")
        logger.info(f"   Activas: {len(self.operaciones_activas)}")
        logger.info(f"   Esperando reentry: {len(self.esperando_reentry)}")
        logger.info(f"   Breakouts detectados: {len(self.breakouts_detectados)}")
        logger.info(f"   Total ejecutadas: {self.total_operaciones}")
        
        if self.bitget_client:
            logger.info(f"   🤖 Bitget: ✅ Conectado")
        else:
            logger.info(f"   🤖 Bitget: ❌ No configurado")
        
        if self.operaciones_activas:
            logger.info(f"   📋 Operaciones activas:")
            for simbolo, op in self.operaciones_activas.items():
                estado = "🟢 LONG" if op['tipo'] == 'LONG' else "🔴 SHORT"
                ancho_canal = op.get('ancho_canal_porcentual', 0)
                timeframe = op.get('timeframe_utilizado', 'N/A')
                breakout = "🚀" if op.get('breakout_usado', False) else ""
                ejecutada = "🤖" if op.get('operacion_ejecutada', False) else ""
                logger.info(f"      • {simbolo} {estado} {breakout} {ejecutada} - {timeframe} - Ancho: {ancho_canal:.1f}%")

    def iniciar(self):
        """Inicia el bot principal"""
        logger.info("\n" + "=" * 80)
        logger.info("🤖 BOT DE TRADING - ESTRATEGIA BREAKOUT + REENTRY")
        logger.info("🎯 PRIORIDAD: TIMEFRAMES CORTOS (1m > 3m > 5m > 15m > 30m)")
        logger.info("💾 PERSISTENCIA: ACTIVADA")
        logger.info("🔄 REEVALUACIÓN: CADA 2 HORAS")
        logger.info("🏦 INTEGRACIÓN: BITGET API V2")
        logger.info("=" * 80)
        
        logger.info(f"💱 Símbolos: {len(self.config.get('symbols', []))} monedas")
        logger.info(f"⏰ Timeframes: {', '.join(self.config.get('timeframes', []))}")
        logger.info(f"🕯️ Velas: {self.config.get('velas_options', [])}")
        logger.info(f"📏 ANCHO MÍNIMO: {self.config.get('min_channel_width_percent', 4)}%")
        logger.info(f"🚀 Estrategia: 1) Detectar Breakout → 2) Esperar Reentry → 3) Confirmar con Stoch")
        
        if self.bitget_client:
            logger.info(f"🤖 BITGET: ✅ API Conectada")
            logger.info(f"⚡ Apalancamiento: {self.leverage_por_defecto}x")
            logger.info(f"💰 Capital por operación: ${self.capital_por_operacion}")
            
            if self.ejecutar_operaciones_automaticas:
                logger.info(f"🤖 AUTO-TRADING: ✅ ACTIVADO")
            else:
                logger.info(f"🤖 AUTO-TRADING: ❌ Solo señales")
        else:
            logger.info(f"🤖 BITGET: ❌ No configurado (solo señales)")
        
        logger.info("=" * 80)
        logger.info("\n🚀 INICIANDO BOT...")
        
        # Verificar configuración recomendada de Bitget
        if self.bitget_client:
            logger.info("\n📋 CONFIGURACIÓN RECOMENDADA EN BITGET FUTURES:")
            logger.info("   1. Ir a Futures → Configuración de trading")
            logger.info("   2. Modo de posición: Modo de cobertura")
            logger.info("   3. Prevención de auto-trading: Modo por defecto")
            logger.info("   4. Unidad de futuros: Costo")
            logger.info("   5. Precio de activación TP/SL: Último precio")
            logger.info("   6. Modo del activo: Modo de activo único")
        
        try:
            while True:
                nuevas_senales = self.ejecutar_analisis()
                self.mostrar_resumen_operaciones()
                
                minutos_espera = self.config.get('scan_interval_minutes', 1)
                logger.info(f"\n✅ Análisis completado. Señales nuevas: {nuevas_senales}")
                logger.info(f"⏳ Próximo análisis en {minutos_espera} minutos...")
                logger.info("-" * 60)
                
                for minuto in range(minutos_espera):
                    time.sleep(60)
                    restantes = minutos_espera - (minuto + 1)
                    
                    if restantes > 0 and restantes % 5 == 0:
                        logger.info(f"   ⏰ {restantes} minutos restantes...")
                        
        except KeyboardInterrupt:
            logger.info("\n🛑 Bot detenido por el usuario")
            logger.info("💾 Guardando estado final...")
            self.guardar_estado()
            logger.info("👋 ¡Hasta pronto!")
            
        except Exception as e:
            logger.error(f"\n❌ Error crítico en el bot: {str(e)}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            
            logger.info("💾 Intentando guardar estado...")
            try:
                self.guardar_estado()
            except Exception as save_error:
                logger.error(f"❌ Error guardando estado: {str(save_error)}")
            
            logger.error("❌ El bot se ha detenido debido a un error crítico")

# ---------------------------
# CONFIGURACIÓN DESDE ENTORNO
# ---------------------------
def crear_config_desde_entorno():
    """Configuración desde variables de entorno"""
    import traceback
    
    directorio_actual = os.path.dirname(os.path.abspath(__file__))
    
    # Obtener chat IDs de Telegram
    telegram_chat_ids_str = os.environ.get('TELEGRAM_CHAT_ID', '-1002272872445')
    telegram_chat_ids = [cid.strip() for cid in telegram_chat_ids_str.split(',') if cid.strip()]
    
    # Obtener configuración de Bitget
    bitget_api_key = os.environ.get('BITGET_API_KEY')
    bitget_secret_key = os.environ.get('BITGET_SECRET_KEY')
    bitget_passphrase = os.environ.get('BITGET_PASSPHRASE')
    
    # Log de configuración
    logger.info("🔧 Cargando configuración desde entorno...")
    logger.info(f"   Telegram Chat IDs: {len(telegram_chat_ids)} encontrados")
    logger.info(f"   Bitget API Key: {'✅ Presente' if bitget_api_key else '❌ Faltante'}")
    logger.info(f"   Bitget Secret: {'✅ Presente' if bitget_secret_key else '❌ Faltante'}")
    logger.info(f"   Bitget Passphrase: {'✅ Presente' if bitget_passphrase else '❌ Faltante'}")
    
    # Parsear configuración de operaciones automáticas
    ejecutar_automaticas_str = os.environ.get('EJECUTAR_OPERACIONES_AUTOMATICAS', 'false')
    ejecutar_automaticas = ejecutar_automaticas_str.lower() == 'true'
    
    capital_operacion = float(os.environ.get('CAPITAL_POR_OPERACION', '2'))
    leverage = int(os.environ.get('LEVERAGE_POR_DEFECTO', '10'))
    
    logger.info(f"   Auto-trading: {'✅ ACTIVADO' if ejecutar_automaticas else '❌ DESACTIVADO'}")
    logger.info(f"   Capital/operación: ${capital_operacion}")
    logger.info(f"   Leverage: {leverage}x")
    
    return {
        'min_channel_width_percent': 4.0,
        'trend_threshold_degrees': 16.0,
        'min_trend_strength_degrees': 16.0,
        'entry_margin': 0.001,
        'min_rr_ratio': 1.5,
        'scan_interval_minutes': 6,
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
        # CONFIGURACIÓN BITGET
        'bitget_api_key': bitget_api_key,
        'bitget_api_secret': bitget_secret_key,
        'bitget_passphrase': bitget_passphrase,
        'ejecutar_operaciones_automaticas': ejecutar_automaticas,
        'capital_por_operacion': capital_operacion,
        'leverage_por_defecto': leverage
    }

# ---------------------------
# FLASK APP Y RENDER
# ---------------------------

app = Flask(__name__)

# Crear bot con configuración desde entorno
config = crear_config_desde_entorno()
bot = TradingBot(config)

def run_bot_loop():
    """Ejecuta el bot en un hilo separado"""
    logger.info("🧵 Iniciando hilo del bot...")
    
    while True:
        try:
            bot.ejecutar_analisis()
            time.sleep(bot.config.get('scan_interval_minutes', 1) * 60)
        except Exception as e:
            logger.error(f"❌ Error en el hilo del bot: {str(e)}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            time.sleep(60)

# Iniciar hilo del bot
bot_thread = threading.Thread(target=run_bot_loop, daemon=True)
bot_thread.start()

@app.route('/')
def index():
    return jsonify({
        "status": "online",
        "service": "Bot Breakout + Reentry con Bitget",
        "timestamp": datetime.now().isoformat(),
        "auto_trading": bot.ejecutar_operaciones_automaticas,
        "bitget_connected": bot.bitget_client is not None,
        "active_operations": len(bot.operaciones_activas)
    }), 200

@app.route('/health')
def health():
    """Endpoint de health check"""
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "active_threads": threading.active_count()
    }), 200

@app.route('/status')
def status():
    """Endpoint de estado del bot"""
    return jsonify({
        "status": "operational",
        "auto_trading": bot.ejecutar_operaciones_automaticas,
        "bitget_connected": bot.bitget_client is not None,
        "active_operations": len(bot.operaciones_activas),
        "waiting_reentry": len(bot.esperando_reentry),
        "total_operations": bot.total_operations,
        "scan_interval": bot.config.get('scan_interval_minutes', 1),
        "timestamp": datetime.now().isoformat()
    }), 200

@app.route('/webhook', methods=['POST'])
def telegram_webhook():
    """Webhook para Telegram"""
    if request.is_json:
        update = request.get_json()
        logger.info(f"📩 Webhook Telegram recibido: {json.dumps(update, indent=2)}")
        return jsonify({"status": "ok"}), 200
    
    return jsonify({"error": "Request must be JSON"}), 400

# Configuración automática del webhook
def setup_telegram_webhook():
    """Configura el webhook de Telegram automáticamente"""
    token = os.environ.get('TELEGRAM_TOKEN')
    if not token:
        logger.warning("⚠️ No hay token de Telegram para configurar webhook")
        return
    
    webhook_url = os.environ.get('WEBHOOK_URL')
    if not webhook_url:
        render_url = os.environ.get('RENDER_EXTERNAL_URL')
        if render_url:
            webhook_url = f"{render_url}/webhook"
        else:
            logger.warning("⚠️ No se pudo determinar la URL del webhook")
            return
    
    try:
        logger.info(f"🔗 Configurando webhook Telegram a: {webhook_url}")
        
        # Eliminar webhook existente
        delete_url = f"https://api.telegram.org/bot{token}/deleteWebhook"
        response = requests.get(delete_url, timeout=10)
        logger.info(f"🗑️ Delete webhook: {response.status_code}")
        
        # Configurar nuevo webhook
        set_url = f"https://api.telegram.org/bot{token}/setWebhook?url={webhook_url}"
        response = requests.get(set_url, timeout=10)
        
        if response.status_code == 200:
            logger.info("✅ Webhook de Telegram configurado correctamente")
        else:
            logger.error(f"❌ Error configurando webhook: {response.status_code} - {response.text}")
            
    except Exception as e:
        logger.error(f"❌ Error configurando webhook Telegram: {str(e)}")

if __name__ == '__main__':
    # Configurar webhook al inicio
    setup_telegram_webhook()
    
    # Iniciar Flask
    port = int(os.environ.get('PORT', 5000))
    logger.info(f"🚀 Iniciando servidor Flask en puerto {port}")
    
    app.run(host='0.0.0.0', port=port, debug=False)
