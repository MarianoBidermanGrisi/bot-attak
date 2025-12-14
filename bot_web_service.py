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

# Configurar logging detallado
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('bot_errors.log')
    ]
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
# BITGET CLIENT - INTEGRACIÓN COMPLETA CON API BITGET CORREGIDA
# ---------------------------
class BitgetClient:
    def __init__(self, api_key, api_secret, passphrase):
        self.api_key = api_key
        self.api_secret = api_secret
        self.passphrase = passphrase
        self.base_url = "https://api.bitget.com"
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
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
            logger.debug(f"Firma generada para {method} {request_path}")
            return signature
            
        except Exception as e:
            logger.error(f"❌ Error generando firma: {e}")
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
            logger.error(f"❌ Error creando headers: {e}")
            raise

    def _request_with_retry(self, method, url, headers=None, params=None, json_data=None, max_retries=3):
        """Realizar petición con reintentos"""
        for attempt in range(max_retries):
            try:
                response = self.session.request(
                    method=method,
                    url=url,
                    headers=headers,
                    params=params,
                    json=json_data,
                    timeout=15
                )
                return response
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt
                    logger.warning(f"⚠️ Intento {attempt+1}/{max_retries} fallido para {url}: {e}. Reintentando en {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    logger.error(f"❌ Todos los intentos fallaron para {url}: {e}")
                    raise
            except Exception as e:
                logger.error(f"❌ Error inesperado en petición a {url}: {e}")
                raise
        return None

    def verificar_credenciales(self):
        """Verificar que las credenciales sean válidas"""
        try:
            logger.info("🔍 Verificando credenciales Bitget...")
            
            if not self.api_key or not self.api_secret or not self.passphrase:
                logger.error("❌ Credenciales incompletas. API Key: %s, Secret: %s, Passphrase: %s", 
                           'Sí' if self.api_key else 'No',
                           'Sí' if self.api_secret else 'No',
                           'Sí' if self.passphrase else 'No')
                return False
            
            # Intentar múltiples tipos de producto
            product_types = ['usdt-futures', 'USDT-FUTURES', 'USDT-MIX', 'usdt-mix']
            
            for product_type in product_types:
                try:
                    logger.info(f"🔍 Probando productType: {product_type}")
                    accounts = self.get_account_info(product_type)
                    if accounts is not None:
                        logger.info(f"✅ Credenciales verificadas con productType: {product_type}")
                        for account in accounts:
                            if account.get('marginCoin') == 'USDT':
                                available = float(account.get('available', 0))
                                logger.info(f"💰 Balance disponible: {available:.2f} USDT")
                        return True
                except Exception as e:
                    logger.warning(f"⚠️ productType {product_type} falló: {e}")
                    continue
            
            logger.error("❌ Todos los productType fallaron. No se pudo verificar credenciales")
            return False
                
        except Exception as e:
            logger.error(f"❌ Error verificando credenciales: {e}")
            return False

    def get_account_info(self, product_type='usdt-futures'):
        """Obtener información de cuenta Bitget V2"""
        try:
            request_path = '/api/v2/mix/account/accounts'
            params = {'productType': product_type, 'marginCoin': 'USDT'}
            
            query_string = f"?productType={product_type}&marginCoin=USDT"
            full_request_path = request_path + query_string
            
            headers = self._get_headers('GET', full_request_path, '')
            
            response = self._request_with_retry(
                'GET',
                f"{self.base_url}{request_path}",
                headers=headers,
                params=params
            )
            
            if response is None:
                logger.error("❌ No se pudo obtener respuesta del servidor")
                return None
            
            logger.info(f"📊 Respuesta cuenta - Status: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                logger.debug(f"📄 Respuesta completa: {json.dumps(data)}")
                
                if data.get('code') == '00000':
                    logger.info(f"✅ Account info exitoso para productType: {product_type}")
                    return data.get('data', [])
                else:
                    error_msg = data.get('msg', 'Unknown error')
                    error_code = data.get('code', 'Unknown')
                    logger.error(f"❌ Error API en get_account_info: {error_code} - {error_msg}")
                    
                    # Si es error de productType, intentar con alternativo
                    if error_code == '40020':
                        if product_type == 'usdt-futures':
                            logger.info("🔄 Intentando con productType='usdt-mix'...")
                            return self.get_account_info('usdt-mix')
                        elif product_type == 'USDT-FUTURES':
                            logger.info("🔄 Intentando con productType='usdt-futures'...")
                            return self.get_account_info('usdt-futures')
            else:
                logger.error(f"❌ Error HTTP en get_account_info: {response.status_code} - {response.text}")
                
            return None
            
        except Exception as e:
            logger.error(f"❌ Error en get_account_info: {e}", exc_info=True)
            return None

    def get_symbol_info(self, symbol):
        """Obtener información del símbolo"""
        try:
            request_path = '/api/v2/mix/market/contracts'
            
            # Intentar múltiples product_types
            product_types = ['usdt-futures', 'USDT-FUTURES', 'usdt-mix', 'USDT-MIX']
            
            for product_type in product_types:
                try:
                    params = {'productType': product_type}
                    query_string = f"?productType={product_type}"
                    full_request_path = request_path + query_string
                    
                    headers = self._get_headers('GET', full_request_path, '')
                    
                    response = self._request_with_retry(
                        'GET',
                        self.base_url + request_path,
                        headers=headers,
                        params=params
                    )
                    
                    if response and response.status_code == 200:
                        data = response.json()
                        if data.get('code') == '00000':
                            contracts = data.get('data', [])
                            for contract in contracts:
                                if contract.get('symbol') == symbol:
                                    logger.info(f"✅ Symbol info encontrado para {symbol} con productType: {product_type}")
                                    return contract
                except Exception as e:
                    logger.warning(f"⚠️ productType {product_type} falló para {symbol}: {e}")
                    continue
            
            logger.error(f"❌ No se pudo obtener info del símbolo {symbol} con ningún productType")
            return None
            
        except Exception as e:
            logger.error(f"❌ Error obteniendo info del símbolo {symbol}: {e}", exc_info=True)
            return None

    def place_order(self, symbol, side, order_type, size, price=None, 
                    client_order_id=None, time_in_force='normal', margin_type='isolated'):
        """Colocar orden de mercado o límite con margen aislado"""
        try:
            logger.info(f"📤 Colocando orden: {symbol} {side} {order_type} tamaño: {size}")
            
            request_path = '/api/v2/mix/order/place-order'
            body = {
                'symbol': symbol,
                'productType': 'usdt-futures',
                'marginCoin': 'USDT',
                'marginType': margin_type,
                'side': side,
                'orderType': order_type,
                'size': str(size),
                'timeInForce': time_in_force
            }
            
            if price:
                body['price'] = str(price)
            if client_order_id:
                body['clientOrderId'] = client_order_id
            
            logger.debug(f"📦 Body de orden: {json.dumps(body)}")
            
            headers = self._get_headers('POST', request_path, body)
            
            response = self._request_with_retry(
                'POST',
                self.base_url + request_path,
                headers=headers,
                json_data=body
            )
            
            if response is None:
                logger.error("❌ No se pudo obtener respuesta para place_order")
                return None
            
            logger.info(f"📊 Respuesta orden - Status: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                logger.debug(f"📄 Respuesta completa orden: {json.dumps(data)}")
                
                if data.get('code') == '00000':
                    order_data = data.get('data', {})
                    logger.info(f"✅ Orden colocada exitosamente: {order_data}")
                    return order_data
                else:
                    error_msg = data.get('msg', 'Unknown error')
                    error_code = data.get('code', 'Unknown')
                    logger.error(f"❌ Error en orden: {error_code} - {error_msg}")
                    
                    # Intentar con productType alternativo si es error 40020
                    if error_code == '40020':
                        logger.info("🔄 Intentando con productType alternativo...")
                        body['productType'] = 'usdt-mix'
                        headers = self._get_headers('POST', request_path, body)
                        
                        response2 = self._request_with_retry(
                            'POST',
                            self.base_url + request_path,
                            headers=headers,
                            json_data=body
                        )
                        
                        if response2 and response2.status_code == 200:
                            data2 = response2.json()
                            if data2.get('code') == '00000':
                                logger.info("✅ Orden colocada con productType alternativo")
                                return data2.get('data', {})
                    
                    return None
            else:
                logger.error(f"❌ Error HTTP en place_order: {response.status_code} - {response.text}")
                return None
                
        except Exception as e:
            logger.error(f"❌ Error colocando orden: {e}", exc_info=True)
            return None

    def place_plan_order(self, symbol, side, trigger_price, order_type, size, 
                         price=None, plan_type='normal_plan', margin_type='isolated'):
        """Colocar orden de plan (TP/SL) con margen aislado"""
        try:
            logger.info(f"📤 Colocando orden plan: {symbol} {side} trigger: {trigger_price}")
            
            request_path = '/api/v2/mix/order/place-plan-order'
            body = {
                'symbol': symbol,
                'productType': 'usdt-futures',
                'marginCoin': 'USDT',
                'marginType': margin_type,
                'side': side,
                'orderType': order_type,
                'triggerPrice': str(trigger_price),
                'size': str(size),
                'planType': plan_type,
                'triggerType': 'market_price'
            }
            if price:
                body['executePrice'] = str(price)
            
            logger.debug(f"📦 Body de plan order: {json.dumps(body)}")
            
            headers = self._get_headers('POST', request_path, body)
            
            response = self._request_with_retry(
                'POST',
                self.base_url + request_path,
                headers=headers,
                json_data=body
            )
            
            if response is None:
                logger.error("❌ No se pudo obtener respuesta para place_plan_order")
                return None
            
            if response.status_code == 200:
                data = response.json()
                if data.get('code') == '00000':
                    logger.info(f"✅ Plan order colocada exitosamente")
                    return data.get('data', {})
                else:
                    error_msg = data.get('msg', 'Unknown error')
                    error_code = data.get('code', 'Unknown')
                    logger.warning(f"⚠️ Error en plan order: {error_code} - {error_msg}")
                    
                    # Intentar con productType alternativo
                    if error_code == '40020':
                        body['productType'] = 'usdt-mix'
                        headers = self._get_headers('POST', request_path, body)
                        
                        response2 = self._request_with_retry(
                            'POST',
                            self.base_url + request_path,
                            headers=headers,
                            json_data=body
                        )
                        
                        if response2 and response2.status_code == 200:
                            data2 = response2.json()
                            if data2.get('code') == '00000':
                                logger.info("✅ Plan order colocada con productType alternativo")
                                return data2.get('data', {})
            else:
                logger.warning(f"⚠️ Error HTTP en plan order: {response.status_code} - {response.text}")
                
            return None
        except Exception as e:
            logger.error(f"❌ Error colocando plan order: {e}", exc_info=True)
            return None

    def set_margin_mode(self, symbol, margin_mode='isolated'):
        """Configurar modo de margen (isolated o crossed)"""
        try:
            logger.info(f"⚙️ Configurando margin mode {margin_mode} para {symbol}")
            
            request_path = '/api/v2/mix/account/set-margin-mode'
            body = {
                'symbol': symbol,
                'productType': 'usdt-futures',
                'marginCoin': 'USDT',
                'marginMode': margin_mode
            }
            
            headers = self._get_headers('POST', request_path, body)
            
            response = self._request_with_retry(
                'POST',
                self.base_url + request_path,
                headers=headers,
                json_data=body
            )
            
            if response is None:
                logger.error("❌ No se pudo obtener respuesta para set_margin_mode")
                return True  # Continuar aunque falle
            
            if response.status_code == 200:
                data = response.json()
                if data.get('code') == '00000':
                    logger.info(f"✅ Modo margen {margin_mode} configurado para {symbol}")
                    return True
                # Si ya está en ese modo, también es éxito
                if data.get('code') == '40756':
                    logger.info(f"✅ Margen {margin_mode} ya estaba configurado para {symbol}")
                    return True
                else:
                    error_msg = data.get('msg', 'Unknown error')
                    error_code = data.get('code', 'Unknown')
                    logger.warning(f"⚠️ Error configurando margin mode: {error_code} - {error_msg}")
                    
                    # Intentar con productType alternativo
                    if error_code == '40020':
                        body['productType'] = 'usdt-mix'
                        headers = self._get_headers('POST', request_path, body)
                        
                        response2 = self._request_with_retry(
                            'POST',
                            self.base_url + request_path,
                            headers=headers,
                            json_data=body
                        )
                        
                        if response2 and response2.status_code == 200:
                            data2 = response2.json()
                            if data2.get('code') == '00000' or data2.get('code') == '40756':
                                logger.info(f"✅ Margin mode configurado con productType alternativo")
                                return True
            else:
                logger.warning(f"⚠️ Error HTTP configurando margin mode: {response.status_code} - {response.text}")
                
            return True  # Continuar aunque falle
        except Exception as e:
            logger.error(f"❌ Error en set_margin_mode: {e}", exc_info=True)
            return True  # Continuar aunque falle

    def set_leverage(self, symbol, leverage, hold_side='long', margin_type='isolated'):
        """Configurar apalancamiento con margen aislado"""
        try:
            logger.info(f"⚙️ Configurando leverage {leverage}x para {symbol} ({hold_side})")
            
            # Primero configurar el modo de margen
            self.set_margin_mode(symbol, margin_type)
            time.sleep(0.5)
            
            request_path = '/api/v2/mix/account/set-leverage'
            body = {
                'symbol': symbol,
                'productType': 'usdt-futures',
                'marginCoin': 'USDT',
                'leverage': str(leverage),
                'holdSide': hold_side
            }
            
            headers = self._get_headers('POST', request_path, body)
            
            response = self._request_with_retry(
                'POST',
                self.base_url + request_path,
                headers=headers,
                json_data=body
            )
            
            if response is None:
                logger.error("❌ No se pudo obtener respuesta para set_leverage")
                return False
            
            if response.status_code == 200:
                data = response.json()
                if data.get('code') == '00000':
                    logger.info(f"✅ Apalancamiento {leverage}x configurado para {symbol}")
                    return True
                # Si el leverage ya está configurado, también es éxito
                if data.get('code') == '40761':
                    logger.info(f"✅ Apalancamiento {leverage}x ya estaba configurado para {symbol}")
                    return True
                else:
                    error_msg = data.get('msg', 'Unknown error')
                    error_code = data.get('code', 'Unknown')
                    logger.warning(f"⚠️ Error configurando leverage: {error_code} - {error_msg}")
                    
                    # Intentar con productType alternativo
                    if error_code == '40020':
                        body['productType'] = 'usdt-mix'
                        headers = self._get_headers('POST', request_path, body)
                        
                        response2 = self._request_with_retry(
                            'POST',
                            self.base_url + request_path,
                            headers=headers,
                            json_data=body
                        )
                        
                        if response2 and response2.status_code == 200:
                            data2 = response2.json()
                            if data2.get('code') == '00000' or data2.get('code') == '40761':
                                logger.info(f"✅ Leverage configurado con productType alternativo")
                                return True
            else:
                logger.warning(f"⚠️ Error HTTP configurando leverage: {response.status_code} - {response.text}")
                
            return False
        except Exception as e:
            logger.error(f"❌ Error en set_leverage: {e}", exc_info=True)
            return False

    def get_positions(self, symbol=None, product_type='usdt-futures'):
        """Obtener posiciones abiertas"""
        try:
            logger.debug(f"🔍 Obteniendo posiciones para {symbol or 'TODOS'} con productType: {product_type}")
            
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
            
            response = self._request_with_retry(
                'GET',
                self.base_url + request_path,
                headers=headers,
                params=params
            )
            
            if response is None:
                logger.error("❌ No se pudo obtener respuesta para get_positions")
                return []
            
            if response.status_code == 200:
                data = response.json()
                if data.get('code') == '00000':
                    positions = data.get('data', [])
                    logger.info(f"✅ Posiciones obtenidas: {len(positions)}")
                    return positions
                else:
                    error_msg = data.get('msg', 'Unknown error')
                    error_code = data.get('code', 'Unknown')
                    logger.warning(f"⚠️ Error obteniendo posiciones: {error_code} - {error_msg}")
                    
                    # Intentar con productType alternativo
                    if error_code == '40020' and product_type == 'usdt-futures':
                        logger.info("🔄 Intentando con productType alternativo para posiciones...")
                        return self.get_positions(symbol, 'usdt-mix')
            
            logger.warning(f"⚠️ Error HTTP obteniendo posiciones: {response.status_code}")
            return []
            
        except Exception as e:
            logger.error(f"❌ Error obteniendo posiciones: {e}", exc_info=True)
            return []

    def get_klines(self, symbol, interval='5m', limit=200):
        """Obtener velas (datos de mercado) con reintentos robustos"""
        try:
            interval_map = {
                '1m': '1m', '3m': '3m', '5m': '5m',
                '15m': '15m', '30m': '30m', '1h': '1H',
                '4h': '4H', '1d': '1D'
            }
            bitget_interval = interval_map.get(interval, '5m')
            
            # Intentar múltiples product_types
            product_types = ['usdt-futures', 'usdt-mix', 'USDT-FUTURES', 'USDT-MIX']
            
            for product_type in product_types:
                try:
                    logger.debug(f"🔍 Obteniendo klines para {symbol} con productType: {product_type}")
                    
                    request_path = f'/api/v2/mix/market/candles'
                    params = {
                        'symbol': symbol,
                        'productType': product_type,
                        'granularity': bitget_interval,
                        'limit': limit
                    }
                    
                    response = self._request_with_retry(
                        'GET',
                        self.base_url + request_path,
                        params=params
                    )
                    
                    if response is None:
                        logger.warning(f"⚠️ No se pudo obtener respuesta para klines con productType: {product_type}")
                        continue
                    
                    if response.status_code == 200:
                        data = response.json()
                        if data.get('code') == '00000':
                            candles = data.get('data', [])
                            if candles:
                                logger.info(f"✅ Klines obtenidos para {symbol}: {len(candles)} velas con productType: {product_type}")
                                candles.reverse()  # Bitget devuelve en orden descendente
                                return candles
                        else:
                            error_msg = data.get('msg', 'Unknown error')
                            error_code = data.get('code', 'Unknown')
                            logger.debug(f"⚠️ productType {product_type} falló: {error_code} - {error_msg}")
                    else:
                        logger.debug(f"⚠️ HTTP {response.status_code} para productType {product_type}")
                        
                except Exception as e:
                    logger.debug(f"⚠️ Excepción con productType {product_type}: {e}")
                    continue
            
            logger.error(f"❌ Todos los productType fallaron para obtener klines de {symbol}")
            return None
            
        except Exception as e:
            logger.error(f"❌ Error en get_klines para {symbol}: {e}", exc_info=True)
            return None

# ---------------------------
# FUNCIONES DE OPERACIONES BITGET - MEJORADAS CON LOGS DETALLADOS
# ---------------------------
def ejecutar_operacion_bitget(bitget_client, simbolo, tipo_operacion, capital_usd, leverage=20):
    """
    Ejecutar una operación completa en Bitget (posición + TP/SL) con margen aislado
    """
    
    logger.info(f"[EXEC] EJECUTANDO OPERACIÓN REAL EN BITGET (MARGEN AISLADO)")
    logger.info(f"Símbolo: {simbolo}")
    logger.info(f"Tipo: {tipo_operacion}")
    logger.info(f"Apalancamiento: {leverage}x")
    logger.info(f"Capital: ${capital_usd}")
    logger.info(f"Margen: AISLADO")
    
    try:
        # 1. Configurar apalancamiento con margen aislado
        hold_side = 'long' if tipo_operacion == 'LONG' else 'short'
        logger.info(f"🔧 Configurando apalancamiento...")
        leverage_ok = bitget_client.set_leverage(simbolo, leverage, hold_side, margin_type='isolated')
        if not leverage_ok:
            logger.error("❌ Error configurando apalancamiento con margen aislado")
            return None
        time.sleep(1)
        
        # 2. Obtener precio actual
        logger.info(f"🔍 Obteniendo precio actual...")
        klines = bitget_client.get_klines(simbolo, '1m', 1)
        if not klines or len(klines) == 0:
            logger.error(f"❌ No se pudo obtener precio de {simbolo}")
            return None
        
        precio_actual = float(klines[0][4])  # Precio de cierre de la última vela
        logger.info(f"💰 Precio actual: {precio_actual:.8f}")
        
        # 3. Obtener información del símbolo
        logger.info(f"🔍 Obteniendo información del símbolo...")
        symbol_info = bitget_client.get_symbol_info(simbolo)
        if not symbol_info:
            logger.error(f"❌ No se pudo obtener info de {simbolo}")
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
        
        logger.info(f"📊 Cantidad: {cantidad_contratos} contratos")
        logger.info(f"💰 Valor nocional: ${cantidad_contratos * precio_actual:.2f}")
        
        # 5. Calcular TP y SL (2% fijo)
        if tipo_operacion == "LONG":
            sl_porcentaje = 0.02
            tp_porcentaje = 0.04  # TP doble del SL (RR 2:1)
            stop_loss = precio_actual * (1 - sl_porcentaje)
            take_profit = precio_actual * (1 + tp_porcentaje)
            logger.info(f"🎯 TP: {take_profit:.8f} (+{tp_porcentaje*100}%), SL: {stop_loss:.8f} (-{sl_porcentaje*100}%)")
        else:
            sl_porcentaje = 0.02
            tp_porcentaje = 0.04
            stop_loss = precio_actual * (1 + sl_porcentaje)
            take_profit = precio_actual * (1 - tp_porcentaje)
            logger.info(f"🎯 TP: {take_profit:.8f} (-{tp_porcentaje*100}%), SL: {stop_loss:.8f} (+{sl_porcentaje*100}%)")
        
        # 6. Abrir posición con margen aislado
        side = 'open_long' if tipo_operacion == 'LONG' else 'open_short'
        logger.info(f"📤 Abriendo posición {side}...")
        
        orden_entrada = bitget_client.place_order(
            symbol=simbolo,
            side=side,
            order_type='market',
            size=cantidad_contratos,
            margin_type='isolated'
        )
        
        if not orden_entrada:
            logger.error("❌ Error abriendo posición")
            return None
        
        logger.info(f"✅ Posición abierta: {orden_entrada.get('orderId', 'N/A')}")
        time.sleep(2)  # Esperar más tiempo para que la posición se refleje
        
        # 7. VERIFICACIÓN CRÍTICA: Confirmar que la posición realmente se abrió
        logger.info("🔍 Verificando estado real de la posición en Bitget...")
        posiciones_reales = bitget_client.get_positions(simbolo)
        
        posicion_encontrada = None
        for pos in posiciones_reales:
            if pos.get('symbol') == simbolo:
                posicion_encontrada = pos
                break
        
        if not posicion_encontrada:
            logger.error(f"❌ CRÍTICO: La posición no se abrió realmente en Bitget para {simbolo}")
            logger.error(f"📄 Respuesta de orden: {orden_entrada}")
            logger.error(f"🔍 Posiciones encontradas: {len(posiciones_reales)}")
            
            # Intentar nuevamente después de más tiempo
            time.sleep(3)
            posiciones_reales = bitget_client.get_positions(simbolo)
            for pos in posiciones_reales:
                if pos.get('symbol') == simbolo:
                    posicion_encontrada = pos
                    break
            
            if not posicion_encontrada:
                logger.error(f"❌ Segunda verificación fallida para {simbolo}")
                return None
        
        logger.info(f"✅ Posición confirmada en Bitget: {posicion_encontrada.get('positionId', 'N/A')}")
        logger.info(f"📏 Tamaño real: {posicion_encontrada.get('positionSize', 'N/A')}")
        logger.info(f"💰 Precio de entrada: {posicion_encontrada.get('avgPrice', 'N/A')}")
        
        # 8. Colocar Stop Loss con margen aislado
        sl_side = 'close_long' if tipo_operacion == 'LONG' else 'close_short'
        logger.info(f"🛑 Configurando Stop Loss...")
        
        orden_sl = bitget_client.place_plan_order(
            symbol=simbolo,
            side=sl_side,
            trigger_price=stop_loss,
            order_type='market',
            size=cantidad_contratos,
            plan_type='loss_plan',
            margin_type='isolated'
        )
        
        if orden_sl:
            logger.info(f"✅ Stop Loss configurado en: {stop_loss:.8f}")
        else:
            logger.warning("⚠️ Error configurando Stop Loss - continuando sin SL")
        
        time.sleep(1)
        
        # 9. Colocar Take Profit con margen aislado
        logger.info(f"🎯 Configurando Take Profit...")
        
        orden_tp = bitget_client.place_plan_order(
            symbol=simbolo,
            side=sl_side,
            trigger_price=take_profit,
            order_type='market',
            size=cantidad_contratos,
            plan_type='normal_plan',
            margin_type='isolated'
        )
        
        if orden_tp:
            logger.info(f"✅ Take Profit configurado en: {take_profit:.8f}")
        else:
            logger.warning("⚠️ Error configurando Take Profit - continuando sin TP")
        
        # 10. Retornar información de la operación
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
            'posicion_real': posicion_encontrada,
            'margen_tipo': 'isolated'
        }
        
        logger.info(f"✅ OPERACIÓN EJECUTADA EXITOSAMENTE EN BITGET")
        logger.info(f"📋 ID Orden: {orden_entrada.get('orderId', 'N/A')}")
        logger.info(f"📏 Contratos: {cantidad_contratos}")
        logger.info(f"💰 Entrada: {precio_actual:.8f}")
        logger.info(f"🛑 SL: {stop_loss:.8f}")
        logger.info(f"🎯 TP: {take_profit:.8f}")
        
        return operacion_data
        
    except Exception as e:
        logger.error(f"❌ Error ejecutando operación en Bitget: {e}", exc_info=True)
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
        self.breakouts_detectados = {}
        self.esperando_reentry = {}
        self.estado_file = config.get('estado_file', 'estado_bot.json')
        
        logger.info("🤖 Inicializando TradingBot...")
        
        self.cargar_estado()
        
        # Inicializar cliente Bitget si están las credenciales
        self.bitget_client = None
        bitget_config_present = (
            config.get('bitget_api_key') and 
            config.get('bitget_api_secret') and 
            config.get('bitget_passphrase')
        )
        
        if bitget_config_present:
            logger.info("🔧 Inicializando cliente Bitget...")
            try:
                self.bitget_client = BitgetClient(
                    api_key=config['bitget_api_key'],
                    api_secret=config['bitget_api_secret'],
                    passphrase=config['bitget_passphrase']
                )
                
                # Verificar credenciales con múltiples intentos
                for attempt in range(3):
                    logger.info(f"🔍 Verificando credenciales Bitget (intento {attempt+1}/3)...")
                    if self.bitget_client.verificar_credenciales():
                        logger.info("✅ Cliente Bitget inicializado y verificado")
                        break
                    else:
                        logger.warning(f"⚠️ Intento {attempt+1} fallido para verificar credenciales de Bitget")
                        if attempt < 2:
                            time.sleep(2)
                else:
                    logger.warning("⚠️ No se pudieron verificar las credenciales de Bitget después de 3 intentos")
            except Exception as e:
                logger.error(f"❌ Error inicializando cliente Bitget: {e}", exc_info=True)
        
        # Configuración de operaciones automáticas
        self.ejecutar_operaciones_automaticas = config.get('ejecutar_operaciones_automaticas', False)
        self.capital_por_operacion = config.get('capital_por_operacion', 50)
        self.leverage_por_defecto = config.get('leverage_por_defecto', 20)
        
        logger.info(f"🤖 Auto-trading: {'✅ ACTIVADO' if self.ejecutar_operaciones_automaticas else '❌ DESACTIVADO'}")
        logger.info(f"💰 Capital por operación: ${self.capital_por_operacion}")
        logger.info(f"⚡ Apalancamiento: {self.leverage_por_defecto}x")
        
        # Optimización automática
        parametros_optimizados = None
        if self.auto_optimize:
            try:
                logger.info("🔍 Ejecutando optimización automática...")
                ia = OptimizadorIA(log_path=self.log_path, min_samples=config.get('min_samples_optimizacion', 15))
                parametros_optimizados = ia.buscar_mejores_parametros()
            except Exception as e:
                logger.error(f"⚠ Error en optimización automática: {e}", exc_info=True)
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
        
        # Limpiar operaciones obsoletas al inicializar
        if self.bitget_client:
            logger.info("🧹 Limpiando operaciones obsoletas al inicializar...")
            self.sincronizar_estado_con_bitget()

    def cargar_estado(self):
        """Carga el estado previo del bot incluyendo breakouts"""
        try:
            if os.path.exists(self.estado_file):
                logger.info(f"📂 Cargando estado desde {self.estado_file}...")
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
            logger.error(f"⚠ Error cargando estado previo: {e}", exc_info=True)
            logger.info("   Se iniciará con estado limpio")

    def guardar_estado(self):
        """Guarda el estado actual del bot incluyendo breakouts"""
        try:
            logger.debug("💾 Guardando estado del bot...")
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
            
            logger.info("✅ Estado guardado correctamente")
            
        except Exception as e:
            logger.error(f"⚠ Error guardando estado: {e}", exc_info=True)

    def sincronizar_estado_con_bitget(self):
        """Sincroniza el estado interno con las posiciones reales en Bitget"""
        if not self.bitget_client:
            logger.warning("⚠️ No hay cliente Bitget para sincronizar")
            return
        
        try:
            logger.info("🔄 Sincronizando estado interno con posiciones reales de Bitget...")
            
            # Obtener todas las posiciones reales de Bitget
            posiciones_reales = self.bitget_client.get_positions()
            simbolos_reales = {pos.get('symbol') for pos in posiciones_reales if float(pos.get('positionSize', 0)) != 0}
            
            logger.info(f"📊 Posiciones reales en Bitget: {len(simbolos_reales)}")
            
            # Limpiar operaciones que ya no existen en Bitget
            simbolos_a_eliminar = []
            for simbolo in list(self.operaciones_activas.keys()):
                if simbolo not in simbolos_reales:
                    logger.warning(f"🗑️ Limpiando operación inexistente en Bitget: {simbolo}")
                    simbolos_a_eliminar.append(simbolo)
            
            for simbolo in simbolos_a_eliminar:
                del self.operaciones_activas[simbolo]
                if simbolo in self.senales_enviadas:
                    self.senales_enviadas.remove(simbolo)
            
            # Agregar posiciones que están en Bitget pero no en nuestro estado
            for pos in posiciones_reales:
                simbolo = pos.get('symbol')
                if simbolo and float(pos.get('positionSize', 0)) != 0 and simbolo not in self.operaciones_activas:
                    logger.info(f"➕ Agregando posición real desde Bitget: {simbolo}")
                    
                    # Determinar tipo de posición
                    position_size = float(pos.get('positionSize', 0))
                    tipo_posicion = 'LONG' if position_size > 0 else 'SHORT'
                    
                    self.operaciones_activas[simbolo] = {
                        'tipo': tipo_posicion,
                        'precio_entrada': float(pos.get('avgPrice', 0)),
                        'take_profit': 0,  # Se actualizará cuando se verifique el cierre
                        'stop_loss': 0,   # Se actualizará cuando se verifique el cierre
                        'timestamp_entrada': datetime.now().isoformat(),
                        'angulo_tendencia': 0,
                        'pearson': 0,
                        'r2_score': 0,
                        'ancho_canal_relativo': 0,
                        'ancho_canal_porcentual': 0,
                        'nivel_fuerza': 1,
                        'timeframe_utilizado': 'N/A',
                        'velas_utilizadas': 0,
                        'stoch_k': 0,
                        'stoch_d': 0,
                        'breakout_usado': False,
                        'operacion_ejecutada': True,
                        'posicion_real': pos,
                        'size': position_size
                    }
            
            logger.info(f"✅ Sincronización completada. Operaciones activas: {len(self.operaciones_activas)}")
            
        except Exception as e:
            logger.error(f"❌ Error sincronizando estado con Bitget: {e}", exc_info=True)

    def verificar_operaciones_reales_bitget(self):
        """Verifica y actualiza el estado basado en posiciones reales de Bitget"""
        if not self.bitget_client:
            logger.debug("⚠️ No hay cliente Bitget para verificar operaciones")
            return []
        
        operaciones_cerradas = []
        try:
            # Sincronizar primero
            self.sincronizar_estado_con_bitget()
            
            # Verificar cierre de operaciones
            for simbolo, operacion in list(self.operaciones_activas.items()):
                try:
                    # Obtener posición real actual
                    posiciones = self.bitget_client.get_positions(simbolo)
                    posicion_actual = None
                    for pos in posiciones:
                        if pos.get('symbol') == simbolo:
                            posicion_actual = pos
                            break
                    
                    position_size = 0
                    if posicion_actual:
                        position_size = float(posicion_actual.get('positionSize', 0))
                    
                    if not posicion_actual or position_size == 0:
                        # La posición fue cerrada
                        logger.info(f"📊 {simbolo} - Posición cerrada en Bitget")
                        
                        # Calcular PnL basado en precio de entrada real
                        precio_entrada = float(posicion_actual.get('avgPrice', operacion['precio_entrada']) if posicion_actual else operacion['precio_entrada'])
                        
                        # Obtener precio actual
                        config_optima = self.config_optima_por_simbolo.get(simbolo)
                        if config_optima:
                            datos = self.obtener_datos_mercado_config(simbolo, config_optima['timeframe'], config_optima['num_velas'])
                            if datos:
                                precio_salida = datos['precio_actual']
                                tipo = operacion['tipo']
                                
                                # Determinar resultado basado en si hitó TP o SL
                                resultado = "TP"  # Asumir TP por defecto
                                if tipo == "LONG":
                                    if precio_salida <= operacion['stop_loss']:
                                        resultado = "SL"
                                else:
                                    if precio_salida >= operacion['stop_loss']:
                                        resultado = "SL"
                                
                                # Calcular PnL
                                if tipo == "LONG":
                                    pnl_percent = ((precio_salida - precio_entrada) / precio_entrada) * 100
                                else:
                                    pnl_percent = ((precio_entrada - precio_salida) / precio_entrada) * 100
                                
                                # Registrar operación
                                datos_operacion = {
                                    'timestamp': datetime.now().isoformat(),
                                    'symbol': simbolo,
                                    'tipo': tipo,
                                    'precio_entrada': precio_entrada,
                                    'take_profit': operacion['take_profit'],
                                    'stop_loss': operacion['stop_loss'],
                                    'precio_salida': precio_salida,
                                    'resultado': resultado,
                                    'pnl_percent': pnl_percent,
                                    'duracion_minutos': (datetime.now() - datetime.fromisoformat(operacion['timestamp_entrada'])).total_seconds() / 60,
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
                                    'operacion_ejecutada': True
                                }
                                
                                # Enviar mensaje de cierre
                                mensaje_cierre = self.generar_mensaje_cierre(datos_operacion)
                                token = self.config.get('telegram_token')
                                chats = self.config.get('telegram_chat_ids', [])
                                if token and chats:
                                    try:
                                        self._enviar_telegram_simple(mensaje_cierre, token, chats)
                                    except Exception as e:
                                        logger.error(f"❌ Error enviando mensaje de cierre: {e}")
                                
                                # Registrar en log
                                self.registrar_operacion(datos_operacion)
                                operaciones_cerradas.append(simbolo)
                                
                                # Limpiar del estado
                                del self.operaciones_activas[simbolo]
                                if simbolo in self.senales_enviadas:
                                    self.senales_enviadas.remove(simbolo)
                                
                                self.operaciones_desde_optimizacion += 1
                                logger.info(f"     📊 {simbolo} Operación {resultado} - PnL: {pnl_percent:.2f}%")
                
                except Exception as e:
                    logger.error(f"❌ Error verificando operación {simbolo}: {e}", exc_info=True)
                    continue
            
        except Exception as e:
            logger.error(f"❌ Error verificando operaciones reales: {e}", exc_info=True)
        
        return operaciones_cerradas

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
                    logger.debug(f"   ⚠️ Error probando config {timeframe}-{num_velas} para {simbolo}: {e}")
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
            logger.info(f"   ✅ Config óptima: {mejor_config['timeframe']} - {mejor_config['num_velas']} velas - Ancho: {mejor_config['ancho_canal']:.1f}%")
        
        return mejor_config

    def obtener_datos_mercado_config(self, simbolo, timeframe, num_velas):
        """Obtiene datos con configuración específica usando API de Bitget"""
        # Usar API de Bitget si está disponible
        if self.bitget_client:
            try:
                candles = self.bitget_client.get_klines(simbolo, timeframe, num_velas + 14)
                if not candles or len(candles) == 0:
                    logger.warning(f"   ⚠️ No se obtuvieron candles de Bitget para {simbolo}")
                    return None
                
                # Procesar datos de Bitget
                maximos = []
                minimos = []
                cierres = []
                tiempos = []
                
                for i, candle in enumerate(candles):
                    try:
                        # Formato Bitget: [timestamp, open, high, low, close, volume, ...]
                        maximos.append(float(candle[2]))  # high
                        minimos.append(float(candle[3]))  # low
                        cierres.append(float(candle[4]))  # close
                        tiempos.append(i)
                    except (IndexError, ValueError) as e:
                        logger.warning(f"   ⚠️ Error procesando candle {i} para {simbolo}: {e}")
                        continue
                
                if len(cierres) < num_velas:
                    logger.warning(f"   ⚠️ Datos insuficientes para {simbolo}: {len(cierres)} < {num_velas}")
                    return None
                
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
                logger.error(f"   ❌ Error obteniendo datos de Bitget para {simbolo}: {e}")
                # Fallback a Binance si falla Bitget
                pass
        
        # Fallback a Binance API
        try:
            url = "https://api.binance.com/api/v3/klines"
            params = {'symbol': simbolo, 'interval': timeframe, 'limit': num_velas + 14}
            
            response = requests.get(url, params=params, timeout=10)
            if response.status_code != 200:
                logger.error(f"   ❌ Error HTTP Binance para {simbolo}: {response.status_code}")
                return None
            
            datos = response.json()
            if not isinstance(datos, list) or len(datos) == 0:
                logger.warning(f"   ⚠️ Datos vacíos de Binance para {simbolo}")
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
            logger.error(f"   ❌ Error fallback Binance para {simbolo}: {e}")
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
            ancho_canal_porcentual = (ancho_canal_absoluto / precio_medio) * 100 if precio_medio != 0 else 0
            
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
            logger.error(f"❌ Error calculando canal de regresión: {e}")
            return None

    def enviar_alerta_breakout(self, simbolo, tipo_breakout, info_canal, datos_mercado, config_optima):
        """
        Envía alerta de BREAKOUT detectado a Telegram con gráfico
        """
        precio_cierre = datos_mercado['cierres'][-1]
        resistencia = info_canal['resistencia']
        soporte = info_canal['soporte']
        direccion_canal = info_canal['direccion']
        
        # Calcular Stochastic para incluir en el mensaje
        try:
            if len(datos_mercado['cierres']) >= 14:
                period = 14
                stoch_k_values = []
                for i in range(len(datos_mercado['cierres'])):
                    if i < period - 1:
                        stoch_k_values.append(50)
                    else:
                        highest_high = max(datos_mercado['maximos'][i-period+1:i+1])
                        lowest_low = min(datos_mercado['minimos'][i-period+1:i+1])
                        if highest_high == lowest_low:
                            k = 50
                        else:
                            k = 100 * (datos_mercado['cierres'][i] - lowest_low) / (highest_high - lowest_low)
                        stoch_k_values.append(k)
                
                stoch_actual = stoch_k_values[-1] if stoch_k_values else 50
                
                if stoch_actual <= 30:
                    estado_stoch = "[SOBREVENTA] (buen momento para LONG)"
                elif stoch_actual >= 70:
                    estado_stoch = "[SOBRECOMPRA] (buen momento para SHORT)"
                else:
                    estado_stoch = "[NEUTRO]"
            else:
                stoch_actual = 50
                estado_stoch = "➖ NEUTRO (datos insuficientes)"
        except Exception as e:
            logger.error(f"❌ Error calculando Stochastic para alerta: {e}")
            stoch_actual = 50
            estado_stoch = "➖ NEUTRO (error cálculo)"
        
        # Determinar tipo de ruptura
        if tipo_breakout == "BREAKOUT_LONG":
            emoji_principal = "📉"
            tipo_texto = "RUPTURA de SOPORTE"
            nivel_roto = f"Soporte: {soporte:.8f}"
            direccion_emoji = "↓"
            expectativa = "posible entrada en SHORT si el precio reingresa al canal"
        else:  # BREAKOUT_SHORT
            emoji_principal = "📈"
            tipo_texto = "RUPTURA de RESISTENCIA"
            nivel_roto = f"Resistencia: {resistencia:.8f}"
            direccion_emoji = "↑"
            expectativa = "posible entrada en LONG si el precio reingresa al canal"
        
        mensaje = f"""
{emoji_principal} <b>¡BREAKOUT DETECTADO! - {simbolo}</b>
⚠️ <b>{tipo_texto}</b> {direccion_emoji}
⏰ <b>Hora:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
⏳ <b>ESPERANDO REINGRESO...</b>
👁️ Máximo 30 minutos para confirmación
📍 {expectativa}
{nivel_roto}
📊 <b>Stochastic:</b> {stoch_actual:.1f} - {estado_stoch}
        """
        
        token = self.config.get('telegram_token')
        chat_ids = self.config.get('telegram_chat_ids', [])
        
        if token and chat_ids:
            try:
                logger.info(f"     📊 Iniciando generación de gráfico para {simbolo}...")
                
                buf = self.generar_grafico_breakout(simbolo, info_canal, datos_mercado, tipo_breakout, config_optima)
                
                if buf:
                    logger.info(f"     📨 Enviando gráfico por Telegram...")
                    exito_grafico = self.enviar_grafico_telegram(buf, token, chat_ids)
                    if exito_grafico:
                        logger.info(f"     ✅ Gráfico enviado exitosamente")
                    else:
                        logger.warning(f"     ⚠️ Error enviando gráfico")
                    time.sleep(1)
                else:
                    logger.warning(f"     ⚠️ No se pudo generar gráfico")
                
                # Enviar mensaje de texto siempre
                logger.info(f"     📤 Enviando mensaje de texto...")
                exito_mensaje = self._enviar_telegram_simple(mensaje, token, chat_ids)
                if exito_mensaje:
                    logger.info(f"     ✅ Mensaje enviado exitosamente")
                else:
                    logger.error(f"     ❌ Error enviando mensaje")
                    
                logger.info(f"     ✅ Alerta de breakout procesada para {simbolo}")
                
            except Exception as e:
                logger.error(f"     ❌ Error completo enviando alerta de breakout: {e}", exc_info=True)
        else:
            logger.info(f"     📢 Breakout detectado en {simbolo} (sin Telegram configurado)")

    def generar_grafico_breakout(self, simbolo, info_canal, datos_mercado, tipo_breakout, config_optima):
        """
        Genera gráfico especial para el momento del BREAKOUT
        """
        try:
            plt.rcParams['font.sans-serif'] = ['DejaVu Sans', 'Arial']
            plt.rcParams['axes.unicode_minus'] = False
            
            df = None
            datos_exitosos = False
            
            # Intentar Bitget primero
            if self.bitget_client:
                try:
                    logger.debug(f"     📊 Obteniendo datos de Bitget para {simbolo}...")
                    klines = self.bitget_client.get_klines(simbolo, config_optima['timeframe'], config_optima['num_velas'])
                    if klines and len(klines) > 20:
                        df_data = []
                        for kline in klines:
                            try:
                                df_data.append({
                                    'Date': pd.to_datetime(int(kline[0]), unit='ms'),
                                    'Open': float(kline[1]),
                                    'High': float(kline[2]),
                                    'Low': float(kline[3]),
                                    'Close': float(kline[4]),
                                    'Volume': float(kline[5])
                                })
                            except (ValueError, IndexError) as e:
                                logger.debug(f"     ⚠️ Error procesando kline: {e}")
                                continue
                        
                        if len(df_data) >= 20:
                            df = pd.DataFrame(df_data)
                            df.set_index('Date', inplace=True)
                            datos_exitosos = True
                            logger.info(f"     ✅ Datos de Bitget obtenidos: {len(df)} velas")
                except Exception as e:
                    logger.warning(f"     ⚠️ Error con Bitget: {e}")
            
            # Fallback a Binance si Bitget falló
            if not datos_exitosos:
                try:
                    logger.debug(f"     📊 Obteniendo datos de Binance para {simbolo}...")
                    url = "https://api.binance.com/api/v3/klines"
                    params = {
                        'symbol': simbolo,
                        'interval': config_optima['timeframe'],
                        'limit': min(config_optima['num_velas'], 100)
                    }
                    
                    response = requests.get(url, params=params, timeout=15)
                    if response.status_code == 200:
                        klines = response.json()
                        if len(klines) >= 20:
                            df_data = []
                            for kline in klines:
                                try:
                                    df_data.append({
                                        'Date': pd.to_datetime(int(kline[0]), unit='ms'),
                                        'Open': float(kline[1]),
                                        'High': float(kline[2]),
                                        'Low': float(kline[3]),
                                        'Close': float(kline[4]),
                                        'Volume': float(kline[5])
                                    })
                                except (ValueError, IndexError):
                                    continue
                            
                            if len(df_data) >= 20:
                                df = pd.DataFrame(df_data)
                                df.set_index('Date', inplace=True)
                                datos_exitosos = True
                                logger.info(f"     ✅ Datos de Binance obtenidos: {len(df)} velas")
                except Exception as e:
                    logger.warning(f"     ⚠️ Error con Binance: {e}")
            
            if not datos_exitosos or df is None:
                raise ValueError("No se pudieron obtener datos suficientes de ninguna API")
            
            # Calcular líneas de canal con validación
            tiempos_reg = np.arange(len(df))
            resistencia_values = []
            soporte_values = []
            
            try:
                for i in tiempos_reg:
                    resist = info_canal['pendiente_resistencia'] * i + \
                            (info_canal['resistencia'] - info_canal['pendiente_resistencia'] * tiempos_reg[-1])
                    sop = info_canal['pendiente_soporte'] * i + \
                         (info_canal['soporte'] - info_canal['pendiente_soporte'] * tiempos_reg[-1])
                    resistencia_values.append(resist)
                    soporte_values.append(sop)
            except KeyError as e:
                raise ValueError(f"Info de canal incompleta: {e}")
            
            df['Resistencia'] = resistencia_values
            df['Soporte'] = soporte_values
            
            # CALCULAR STOCHASTIC
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
            
            # Crear arrays numpy válidos
            precio_breakout = datos_mercado.get('precio_actual') or df['Close'].iloc[-1]
            resistencia_array = np.array(resistencia_values)
            soporte_array = np.array(soporte_values)
            
            logger.debug(f"     [PRICE] Precio breakout: {precio_breakout:.8f}")
            logger.debug(f"     [RESIST] Resistencias calculadas: {len(resistencia_values)} puntos")
            logger.debug(f"     [SUPPORT] Soportes calculados: {len(soporte_values)} puntos")
            logger.debug(f"     📊 Stochastic calculado: {len(df)} puntos")
            
            # Crear gráfico CON STOCHASTIC
            apds = [
                mpf.make_addplot(resistencia_array, color='#FF4444', linestyle='--', width=2, panel=0),
                mpf.make_addplot(soporte_array, color='#4444FF', linestyle='--', width=2, panel=0),
                mpf.make_addplot(df['Stoch_K'], color='#00BFFF', width=1.5, panel=1, ylabel='Stochastic'),
                mpf.make_addplot(df['Stoch_D'], color='#FF6347', width=1.5, panel=1)
            ]
            
            # Agregar líneas de sobrecompra/sobreventa para Stochastic
            overbought = [80] * len(df)
            oversold = [20] * len(df)
            apds.append(mpf.make_addplot(overbought, color="#E7E4E4", linestyle='--', width=0.8, panel=1, alpha=0.5))
            apds.append(mpf.make_addplot(oversold, color="#E9E4E4", linestyle='--', width=0.8, panel=1, alpha=0.5))
            
            # Generar gráfico base CON STOCHASTIC
            try:
                if tipo_breakout == "BREAKOUT_LONG":
                    direccion_grafico = "DOWN"
                else:
                    direccion_grafico = "UP"
                
                fig, axes = mpf.plot(df, type='candle', style='charles',
                                   title=f'{simbolo} [{direccion_grafico}] | {tipo_breakout} | {config_optima["timeframe"]} | Breakout Detectado',
                                   ylabel='Precio',
                                   addplot=apds,
                                   volume=False,
                                   returnfig=True,
                                   figsize=(14, 10),
                                   panel_ratios=(3, 1))
                
                # Configurar el panel del Stochastic
                if len(axes) > 1:
                    axes[1].set_ylim([0, 100])
                    axes[1].grid(True, alpha=0.3)
                    axes[1].set_ylabel('Stochastic', fontsize=10)
                
                # Agregar marker manualmente con matplotlib
                ax = axes[0]
                ax.scatter([len(df) - 1], [precio_breakout], color='#FFD700', s=100, marker='o', 
                          zorder=5, label='Breakout Point')
                
                # Agregar anotación
                ax.annotate(f'BREAKOUT\n{precio_breakout:.8f}', 
                           xy=(len(df) - 1, precio_breakout),
                           xytext=(10, 20), textcoords='offset points',
                           bbox=dict(boxstyle='round,pad=0.5', fc='yellow', alpha=0.7),
                           arrowprops=dict(arrowstyle='->', connectionstyle='arc3,rad=0'))
                
                ax.legend()
                
            except Exception as mpl_error:
                logger.warning(f"     ⚠️ Error con mplfinance, usando versión básica: {mpl_error}")
                if tipo_breakout == "BREAKOUT_LONG":
                    direccion_grafico = "DOWN"
                else:
                    direccion_grafico = "UP"
                    
                fig, axes = mpf.plot(df, type='candle',
                                   title=f'{simbolo} [{direccion_grafico}] | {tipo_breakout} | Breakout',
                                   ylabel='Precio',
                                   addplot=apds,
                                   volume=False,
                                   returnfig=True,
                                   figsize=(14, 10),
                                   panel_ratios=(3, 1))
                
                if len(axes) > 1:
                    axes[1].set_ylim([0, 100])
                    axes[1].grid(True, alpha=0.3)
            
            # Guardar gráfico
            buf = BytesIO()
            
            try:
                plt.savefig(buf, format='png', dpi=100, bbox_inches='tight', 
                           facecolor='white', edgecolor='none')
                buf.seek(0)
                logger.info(f"     ✅ Gráfico generado exitosamente para {simbolo}")
                return buf
            except Exception as save_error:
                logger.error(f"     ❌ Error guardando gráfico: {save_error}")
                raise
            finally:
                plt.close(fig)
                plt.clf()
                plt.close('all')
            
        except Exception as e:
            logger.error(f"     ❌ Error crítico generando gráfico: {e}", exc_info=True)
            return None

    def detectar_breakout(self, simbolo, info_canal, datos_mercado):
        """
        Detecta breakout basado en el precio saliendo del canal
        """
        precio_actual = datos_mercado['precio_actual']
        resistencia = info_canal['resistencia']
        soporte = info_canal['soporte']
        direccion = info_canal['direccion']
        
        # Verificar si el precio salió del canal
        if precio_actual > resistencia:
            if direccion == "🔴 BAJISTA":
                return "BREAKOUT_LONG"
        elif precio_actual < soporte:
            if direccion == "🟢 ALCISTA":
                return "BREAKOUT_SHORT"
        
        return None

    def detectar_reentry(self, simbolo, info_canal, datos_mercado):
        """
        Detecta reentry: precio regresa al canal después del breakout
        """
        if simbolo not in self.esperando_reentry:
            return None
        
        breakout_info = self.esperando_reentry[simbolo]
        tiempo_breakout = breakout_info['timestamp']
        tipo_breakout = breakout_info['tipo']
        precio_breakout = breakout_info['precio_breakout']
        
        # Verificar si pasaron menos de 30 minutos desde el breakout
        tiempo_transcurrido = (datetime.now() - tiempo_breakout).total_seconds() / 60
        if tiempo_transcurrido > 30:
            del self.esperando_reentry[simbolo]
            logger.info(f"     ⏰ {simbolo} - Tiempo de espera expirado (30 min)")
            return None
        
        precio_actual = datos_mercado['precio_actual']
        resistencia = info_canal['resistencia']
        soporte = info_canal['soporte']
        
        # Verificar reentry según el tipo de breakout
        if tipo_breakout == "BREAKOUT_LONG":
            if soporte <= precio_actual <= resistencia:
                try:
                    if len(datos_mercado['cierres']) >= 14:
                        period = 14
                        stoch_k_values = []
                        for i in range(len(datos_mercado['cierres'])):
                            if i < period - 1:
                                stoch_k_values.append(50)
                            else:
                                highest_high = max(datos_mercado['maximos'][i-period+1:i+1])
                                lowest_low = min(datos_mercado['minimos'][i-period+1:i+1])
                                if highest_high == lowest_low:
                                    k = 50
                                else:
                                    k = 100 * (datos_mercado['cierres'][i] - lowest_low) / (highest_high - lowest_low)
                                stoch_k_values.append(k)
                        
                        stoch_actual = stoch_k_values[-1] if stoch_k_values else 50
                        
                        if stoch_actual >= 70 or 30 < stoch_actual < 70:
                            logger.info(f"     ✅ {simbolo} - Reentry SHORT confirmado con Stochastic: {stoch_actual:.1f}")
                            return "SHORT"
                        else:
                            logger.info(f"     ⚠️ {simbolo} - Stochastic no favorable para SHORT: {stoch_actual:.1f} (en sobreventa)")
                            return None
                    else:
                        return "SHORT"
                except Exception as e:
                    logger.error(f"     ❌ Error calculando Stochastic para reentry SHORT: {e}")
                    return "SHORT"
                    
        elif tipo_breakout == "BREAKOUT_SHORT":
            if soporte <= precio_actual <= resistencia:
                try:
                    if len(datos_mercado['cierres']) >= 14:
                        period = 14
                        stoch_k_values = []
                        for i in range(len(datos_mercado['cierres'])):
                            if i < period - 1:
                                stoch_k_values.append(50)
                            else:
                                highest_high = max(datos_mercado['maximos'][i-period+1:i+1])
                                lowest_low = min(datos_mercado['minimos'][i-period+1:i+1])
                                if highest_high == lowest_low:
                                    k = 50
                                else:
                                    k = 100 * (datos_mercado['cierres'][i] - lowest_low) / (highest_high - lowest_low)
                                stoch_k_values.append(k)
                        
                        stoch_actual = stoch_k_values[-1] if stoch_k_values else 50
                        
                        if stoch_actual <= 30 or 30 < stoch_actual < 70:
                            logger.info(f"     ✅ {simbolo} - Reentry LONG confirmado con Stochastic: {stoch_actual:.1f}")
                            return "LONG"
                        else:
                            logger.info(f"     ⚠️ {simbolo} - Stochastic no favorable para LONG: {stoch_actual:.1f} (en sobrecompra)")
                            return None
                    else:
                        return "LONG"
                except Exception as e:
                    logger.error(f"     ❌ Error calculando Stochastic para reentry LONG: {e}")
                    return "LONG"
        
        return None

    def calcular_niveles_entrada(self, tipo_operacion, info_canal, precio_actual):
        if not info_canal:
            return None, None, None
        
        resistencia = info_canal['resistencia']
        soporte = info_canal['soporte']
        ancho_canal = resistencia - soporte
        sl_porcentaje = 0.02
        
        if tipo_operacion == "LONG":
            precio_entrada = precio_actual
            stop_loss = precio_entrada * (1 - sl_porcentaje)
            take_profit = precio_entrada + ancho_canal 
        else:
            precio_entrada = precio_actual
            stop_loss = resistencia * (1 + sl_porcentaje)
            take_profit = precio_entrada - ancho_canal
        
        riesgo = abs(precio_entrada - stop_loss)
        beneficio = abs(take_profit - precio_entrada)
        ratio_rr = beneficio / riesgo if riesgo > 0 else 0
        
        if ratio_rr < self.config.get('min_rr_ratio', 1.2):
            if tipo_operacion == "LONG":
                take_profit = precio_entrada + (riesgo * self.config['min_rr_ratio'])
            else:
                take_profit = precio_entrada - (riesgo * self.config['min_rr_ratio'])
        
        return precio_entrada, take_profit, stop_loss

    def escanear_mercado(self):
        """
        Escanea el mercado con estrategia Breakout + Reentry
        """
        symbols = self.config.get('symbols', [])
        logger.info(f"\n🔍 Escaneando {len(symbols)} símbolos (Estrategia: Breakout + Reentry)...")
        
        senales_encontradas = 0
        for simbolo in symbols:
            try:
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
                
                logger.info(
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
                        
                        self.breakouts_detectados[simbolo] = {
                            'tipo': tipo_breakout,
                            'timestamp': datetime.now(),
                            'precio_breakout': precio_actual
                        }
                        
                        logger.info(f"     🎯 {simbolo} - Breakout registrado, esperando reingreso...")
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
                        logger.info(f"   ⏳ {simbolo} - Señal reciente, omitiendo...")
                        continue
                
                breakout_info = self.esperando_reentry[simbolo]
                self.generar_senal_operacion(
                    simbolo, tipo_operacion, precio_entrada, tp, sl, 
                    info_canal, datos_mercado, config_optima, breakout_info
                )
                
                senales_encontradas += 1
                self.breakout_history[simbolo] = datetime.now()
                del self.esperando_reentry[simbolo]
                
            except Exception as e:
                logger.error(f"⚠️ Error analizando {simbolo}: {e}", exc_info=True)
                continue
        
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
        """Genera y envía señal de operación con info de breakout"""
        if simbolo in self.senales_enviadas:
            logger.info(f"     ⚠️ Señal ya enviada para {simbolo}, omitiendo...")
            return
        
        if precio_entrada is None or tp is None or sl is None:
            logger.error(f"    ❌ Niveles inválidos para {simbolo}, omitiendo señal")
            return
        
        riesgo = abs(precio_entrada - sl)
        beneficio = abs(tp - precio_entrada)
        ratio_rr = beneficio / riesgo if riesgo > 0 else 0
        
        sl_percent = abs((sl - precio_entrada) / precio_entrada) * 100
        tp_percent = abs((tp - precio_entrada) / precio_entrada) * 100
        
        stoch_estado = "[OVERSOLD]" if tipo_operacion == "LONG" else "[OVERBOUGHT]"
        breakout_texto = ""
        
        if breakout_info:
            tiempo_breakout = (datetime.now() - breakout_info['timestamp']).total_seconds() / 60
            breakout_texto = f"""
[UP] <b>BREAKOUT + REENTRY DETECTADO:</b>
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
[TREND] <b>Tendencia:</b> {info_canal['direccion']}
💪 <b>Fuerza:</b> {info_canal['fuerza_texto']}
📏 <b>Ángulo:</b> {info_canal['angulo_tendencia']:.1f}°
📊 <b>Pearson:</b> {info_canal['coeficiente_pearson']:.3f}
🎯 <b>R² Score:</b> {info_canal['r2_score']:.3f}
🎰 <b>Stochástico:</b> {stoch_estado}
📊 <b>Stoch K:</b> {info_canal['stoch_k']:.1f}
[STOCH_D] <b>Stoch D:</b> {info_canal['stoch_d']:.1f}
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
                logger.error(f"     ❌ Error enviando señal: {e}", exc_info=True)
        
        # Ejecutar operación automáticamente si está habilitado
        operacion_ejecutada_exitosa = False
        if self.ejecutar_operaciones_automaticas and self.bitget_client:
            logger.info(f"     🤖 Ejecutando operación automática en Bitget...")
            try:
                operacion_bitget = ejecutar_operacion_bitget(
                    bitget_client=self.bitget_client,
                    simbolo=simbolo,
                    tipo_operacion=tipo_operacion,
                    capital_usd=self.capital_por_operacion,
                    leverage=self.leverage_por_defecto
                )
                
                if operacion_bitget:
                    logger.info(f"     ✅ Operación ejecutada y verificada en Bitget para {simbolo}")
                    operacion_ejecutada_exitosa = True
                    
                    # Enviar confirmación de ejecución
                    mensaje_confirmacion = f"""
🤖 <b>OPERACIÓN AUTOMÁTICA EJECUTADA - {simbolo}</b>
✅ <b>Status:</b> EJECUTADA Y VERIFICADA EN BITGET
📊 <b>Tipo:</b> {tipo_operacion}
💰 <b>Capital:</b> ${self.capital_por_operacion}
⚡ <b>Apalancamiento:</b> {self.leverage_por_defecto}x
🎯 <b>Margen:</b> AISLADO
🎯 <b>Entrada:</b> {operacion_bitget['precio_entrada']:.8f}
🛑 <b>Stop Loss:</b> {operacion_bitget['stop_loss']:.8f}
🎯 <b>Take Profit:</b> {operacion_bitget['take_profit']:.8f}
📋 <b>ID Orden:</b> {operacion_bitget['orden_entrada'].get('orderId', 'N/A')}
⏰ <b>Timestamp:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
                    """
                    
                    self._enviar_telegram_simple(mensaje_confirmacion, token, chat_ids)
                else:
                    logger.error(f"     ❌ Error ejecutando operación en Bitget para {simbolo}")
                    
            except Exception as e:
                logger.error(f"     ⚠️ Error en ejecución automática: {e}", exc_info=True)
        
        # Solo agregar al estado interno si la operación se ejecutó exitosamente
        if operacion_ejecutada_exitosa:
            self.operaciones_activas[simbolo] = {
                'tipo': tipo_operacion,
                'precio_entrada': precio_entrada,
                'take_profit': tp,
                'stop_loss': sl,
                'timestamp_entrada': datetime.now().isoformat(),
                'angulo_tendencia': info_canal['angulo_tendencia'],
                'pearson': info_canal['coeficiente_pearson'],
                'r2_score': info_canal['r2_score'],
                'ancho_canal_relativo': info_canal['ancho_canal'] / precio_entrada if precio_entrada != 0 else 0,
                'ancho_canal_porcentual': info_canal['ancho_canal_porcentual'],
                'nivel_fuerza': info_canal['nivel_fuerza'],
                'timeframe_utilizado': config_optima['timeframe'],
                'velas_utilizadas': config_optima['num_velas'],
                'stoch_k': info_canal['stoch_k'],
                'stoch_d': info_canal['stoch_d'],
                'breakout_usado': breakout_info is not None,
                'operacion_ejecutada': operacion_ejecutada_exitosa
            }
            
            self.senales_enviadas.add(simbolo)
            self.total_operaciones += 1

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
                    'stoch_k', 'stoch_d', 'breakout_usado', 'operacion_ejecutada'
                ])
            logger.info(f"📄 Archivo de log creado: {self.archivo_log}")

    def registrar_operacion(self, datos_operacion):
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
                    datos_operacion.get('ancho_canal_relativo', 0),
                    datos_operacion.get('ancho_canal_porcentual', 0),
                    datos_operacion.get('nivel_fuerza', 1),
                    datos_operacion.get('timeframe_utilizado', 'N/A'),
                    datos_operacion.get('velas_utilizadas', 0),
                    datos_operacion.get('stoch_k', 0),
                    datos_operacion.get('stoch_d', 0),
                    datos_operacion.get('breakout_usado', False),
                    datos_operacion.get('operacion_ejecutada', False)
                ])
            logger.info(f"📝 Operación registrada en log: {datos_operacion['symbol']}")
        except Exception as e:
            logger.error(f"❌ Error registrando operación en log: {e}", exc_info=True)

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
                        timestamp = datetime.fromisoformat(row['timestamp'])
                        if timestamp >= fecha_limite:
                            ops_recientes.append({
                                'timestamp': timestamp,
                                'symbol': row['symbol'],
                                'resultado': row['resultado'],
                                'pnl_percent': float(row['pnl_percent']),
                                'tipo': row['tipo'],
                                'breakout_usado': row.get('breakout_usado', 'False') == 'True',
                                'operacion_ejecutada': row.get('operacion_ejecutada', 'False') == 'True'
                            })
                    except Exception as e:
                        logger.debug(f"⚠️ Error procesando fila del log: {e}")
                        continue
            
            return ops_recientes
        except Exception as e:
            logger.error(f"⚠️ Error filtrando operaciones: {e}", exc_info=True)
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
            logger.info("ℹ️ No hay operaciones en la última semana para generar reporte")
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
        
        emoji_resultado = "🟢" if pnl_total > 0 else "🔴" if pnl_total < 0 else "⚪"
        
        mensaje = f"""
━━━━━━━━━━━━━━━━━━━━
📊 <b>REPORTE SEMANAL</b>
━━━━━━━━━━━━━━━━━━━━
📅 {datetime.now().strftime('%d/%m/%Y')} | Últimos 7 días
<b>RENDIMIENTO GENERAL</b>
{emoji_resultado} PnL Total: <b>{pnl_total:+.2f}%</b>
[WINRATE] Win Rate: <b>{winrate:.1f}%</b>
✅ Ganadas: {wins} | ❌ Perdidas: {losses}
<b>ESTADÍSTICAS</b>
📊 Operaciones: {total_ops}
🤖 Automáticas: {ops_automaticas}
💰 Ganancia Promedio: +{avg_ganancia:.2f}%
📉 Pérdida Promedio: -{avg_perdida:.2f}%
🔥 Racha actual: {racha_actual} wins
<b>DESTACADOS</b>
🏆 Mejor: {mejor_op['symbol']} ({mejor_op['tipo']})
   → {mejor_op['pnl_percent']:+.2f}%
⚠️ Peor: {peor_op['symbol']} ({peor_op['tipo']})
   → {peor_op['pnl_percent']:+.2f}%
━━━━━━━━━━━━━━━━━━━━
🤖 Bot automático 24/7
⚡ Estrategia: Breakout + Reentry
💎 Integración: Bitget API
💻 Acceso Premium: @TuUsuario
    """
        
        return mensaje

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
                logger.error(f"❌ Error enviando reporte: {e}", exc_info=True)
                return False
        
        return False

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
                logger.error(f"⚠️ Error en envío automático de reporte: {e}")
        
        return False

    def verificar_cierre_operaciones(self):
        if not self.operaciones_activas:
            return []
        
        operaciones_cerradas = []
        for simbolo, operacion in list(self.operaciones_activas.items()):
            try:
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
                    if tipo == "LONG":
                        pnl_percent = ((precio_actual - operacion['precio_entrada']) / operacion['precio_entrada']) * 100
                    else:
                        pnl_percent = ((operacion['precio_entrada'] - precio_actual) / operacion['precio_entrada']) * 100
                    
                    tiempo_entrada = datetime.fromisoformat(operacion['timestamp_entrada'])
                    duracion_minutos = (datetime.now() - tiempo_entrada).total_seconds() / 60
                    
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
                        'operacion_ejecutada': operacion.get('operacion_ejecutada', False)
                    }
                    
                    mensaje_cierre = self.generar_mensaje_cierre(datos_operacion)
                    token = self.config.get('telegram_token')
                    chats = self.config.get('telegram_chat_ids', [])
                    
                    if token and chats:
                        try:
                            self._enviar_telegram_simple(mensaje_cierre, token, chats)
                        except Exception as e:
                            logger.error(f"❌ Error enviando mensaje de cierre: {e}")
                    
                    self.registrar_operacion(datos_operacion)
                    operaciones_cerradas.append(simbolo)
                    
                    del self.operaciones_activas[simbolo]
                    if simbolo in self.senales_enviadas:
                        self.senales_enviadas.remove(simbolo)
                    
                    self.operaciones_desde_optimizacion += 1
                    logger.info(f"     📊 {simbolo} Operación {resultado} - PnL: {pnl_percent:.2f}%")
            
            except Exception as e:
                logger.error(f"❌ Error verificando operación {simbolo}: {e}", exc_info=True)
                continue
        
        return operaciones_cerradas

    def generar_mensaje_cierre(self, datos_operacion):
        emoji = "🟢" if datos_operacion['resultado'] == "TP" else "🔴"
        color_emoji = "✅" if datos_operacion['resultado'] == "TP" else "❌"
        
        if datos_operacion['tipo'] == 'LONG':
            pnl_absoluto = datos_operacion['precio_salida'] - datos_operacion['precio_entrada']
        else:
            pnl_absoluto = datos_operacion['precio_entrada'] - datos_operacion['precio_salida']
        
        breakout_usado = "[YES] Sí" if datos_operacion.get('breakout_usado', False) else "[NO] No"
        operacion_ejecutada = "🤖 Sí" if datos_operacion.get('operacion_ejecutada', False) else "❌ No"
        
        mensaje = f"""
{emoji} <b>OPERACIÓN CERRADA - {datos_operacion['symbol']}</b>
{color_emoji} <b>RESULTADO: {datos_operacion['resultado']}</b>
📊 Tipo: {datos_operacion['tipo']}
💰 Entrada: {datos_operacion['precio_entrada']:.8f}
🎯 Salida: {datos_operacion['precio_salida']:.8f}
💵 PnL Absoluto: {pnl_absoluto:.8f}
[PNL] PnL %: {datos_operacion['pnl_percent']:.2f}%
⏰ Duración: {datos_operacion['duracion_minutos']:.1f} minutos
[BREAKOUT] Breakout+Reentry: {breakout_usado}
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
                logger.error(f"❌ No hay config óptima para {simbolo}")
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
                               title=f'{simbolo} | {tipo_operacion} | {config_optima["timeframe"]} | Bitget + Breakout+Reentry',
                               ylabel='Precio',
                               addplot=apds,
                               volume=False,
                               returnfig=True,
                               figsize=(14, 10),
                               panel_ratios=(3, 1))
            
            if len(axes) > 2:
                axes[2].set_ylim([0, 100])
                axes[2].grid(True, alpha=0.3)
            
            buf = BytesIO()
            plt.savefig(buf, format='png', dpi=100, bbox_inches='tight', facecolor='#1a1a1a')
            buf.seek(0)
            plt.close(fig)
            
            logger.info(f"✅ Gráfico profesional generado para {simbolo}")
            return buf
            
        except Exception as e:
            logger.error(f"⚠️ Error generando gráfico para {simbolo}: {e}", exc_info=True)
            return None

    def enviar_grafico_telegram(self, buf, token, chat_ids):
        if not buf or not token or not chat_ids:
            logger.warning("⚠️ Datos insuficientes para enviar gráfico a Telegram")
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
                    logger.debug(f"✅ Gráfico enviado a chat {chat_id}")
                else:
                    logger.error(f"❌ Error enviando gráfico a chat {chat_id}: {r.status_code} - {r.text}")
                    
            except Exception as e:
                logger.error(f"❌ Error enviando gráfico: {e}")
        
        return exito

    def _enviar_telegram_simple(self, mensaje, token, chat_ids):
        if not token or not chat_ids:
            logger.warning("⚠️ Token o chat_ids no configurados para Telegram")
            return False
        
        resultados = []
        for chat_id in chat_ids:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            payload = {'chat_id': chat_id, 'text': mensaje, 'parse_mode': 'HTML'}
            try:
                r = requests.post(url, json=payload, timeout=10)
                if r.status_code == 200:
                    resultados.append(True)
                    logger.debug(f"✅ Mensaje enviado a chat {chat_id}")
                else:
                    resultados.append(False)
                    logger.error(f"❌ Error enviando mensaje a chat {chat_id}: {r.status_code} - {r.text}")
            except Exception as e:
                logger.error(f"❌ Error en petición Telegram a chat {chat_id}: {e}")
                resultados.append(False)
        
        return any(resultados)

    def reoptimizar_periodicamente(self):
        try:
            horas_desde_opt = (datetime.now() - self.ultima_optimizacion).total_seconds() / 7200
            if self.operaciones_desde_optimizacion >= 8 or horas_desde_opt >= self.config.get('reevaluacion_horas', 24):
                logger.info("🔄 Iniciando re-optimización automática...")
                ia = OptimizadorIA(log_path=self.log_path, min_samples=self.config.get('min_samples_optimizacion', 30))
                nuevos_parametros = ia.buscar_mejores_parametros()
                if nuevos_parametros:
                    self.actualizar_parametros(nuevos_parametros)
                    self.ultima_optimizacion = datetime.now()
                    self.operaciones_desde_optimizacion = 0
                    logger.info("✅ Parámetros actualizados en tiempo real")
        except Exception as e:
            logger.error(f"⚠ Error en re-optimización automática: {e}", exc_info=True)

    def actualizar_parametros(self, nuevos_parametros):
        self.config['trend_threshold_degrees'] = nuevos_parametros.get('trend_threshold_degrees', 
                                                                        self.config.get('trend_threshold_degrees', 16))
        self.config['min_trend_strength_degrees'] = nuevos_parametros.get('min_trend_strength_degrees', 
                                                                           self.config.get('min_trend_strength_degrees', 16))
        self.config['entry_margin'] = nuevos_parametros.get('entry_margin', 
                                                             self.config.get('entry_margin', 0.001))
        
        logger.info(f"⚙️ Parámetros actualizados: trend_threshold={self.config['trend_threshold_degrees']}, "
                   f"min_strength={self.config['min_trend_strength_degrees']}, entry_margin={self.config['entry_margin']}")

    def ejecutar_analisis(self):
        # Reoptimización periódica
        if random.random() < 0.1:
            self.reoptimizar_periodicamente()
            self.verificar_envio_reporte_automatico()    
        
        # Verificar operaciones reales en Bitget
        cierres = self.verificar_operaciones_reales_bitget()
        if cierres:
            logger.info(f"     📊 Operaciones cerradas en Bitget: {', '.join(cierres)}")
        
        # También verificar operaciones locales
        cierres_locales = self.verificar_cierre_operaciones()
        if cierres_locales:
            logger.info(f"     📊 Operaciones locales cerradas: {', '.join(cierres_locales)}")
        
        self.guardar_estado()
        return self.escanear_mercado()

    def mostrar_resumen_operaciones(self):
        logger.info(f"\n📊 RESUMEN OPERACIONES:")
        logger.info(f"   Activas (Estado Interno): {len(self.operaciones_activas)}")
        logger.info(f"   Esperando reentry: {len(self.esperando_reentry)}")
        logger.info(f"   Total ejecutadas: {self.total_operaciones}")
        
        if self.bitget_client:
            logger.info(f"   🤖 Bitget: ✅ Conectado (MARGEN AISLADO)")
            
            # Mostrar estado real en Bitget
            try:
                posiciones_reales = self.bitget_client.get_positions()
                posiciones_abiertas = [pos for pos in posiciones_reales if float(pos.get('positionSize', 0)) != 0]
                logger.info(f"   📊 Posiciones Reales en Bitget: {len(posiciones_abiertas)}")
                
                if posiciones_abiertas:
                    for pos in posiciones_abiertas:
                        simbolo = pos.get('symbol', 'N/A')
                        size = float(pos.get('positionSize', 0))
                        tipo = "🟢 LONG" if size > 0 else "🔴 SHORT"
                        pnl = float(pos.get('unrealizedPnl', 0))
                        logger.info(f"   • {simbolo} {tipo} | PnL: {pnl:.2f} USDT | Size: {size}")
                        
            except Exception as e:
                logger.error(f"   ⚠️ Error consultando posiciones reales: {e}")
        else:
            logger.info(f"   🤖 Bitget: ❌ No configurado")
        
        if self.operaciones_activas:
            logger.info(f"\n   📋 OPERACIONES EN ESTADO INTERNO:")
            for simbolo, op in self.operaciones_activas.items():
                estado = "🟢 LONG" if op['tipo'] == 'LONG' else "🔴 SHORT"
                ancho_canal = op.get('ancho_canal_porcentual', 0)
                timeframe = op.get('timeframe_utilizado', 'N/A')
                velas = op.get('velas_utilizadas', 0)
                breakout = "[B]" if op.get('breakout_usado', False) else ""
                ejecutada = "✅ REAL" if op.get('operacion_ejecutada', False) else "📢 SEÑAL"
                logger.info(f"   • {simbolo} {estado} {breakout} {ejecutada} - {timeframe} - {velas}v - Ancho: {ancho_canal:.1f}%")

    def iniciar(self):
        logger.info("\n" + "=" * 70)
        logger.info("🤖 BOT DE TRADING - ESTRATEGIA BREAKOUT + REENTRY")
        logger.info("🎯 PRIORIDAD: TIMEFRAMES CORTOS (1m > 3m > 5m > 15m > 30m)")
        logger.info("💾 PERSISTENCIA: ACTIVADA")
        logger.info("🔄 REEVALUACIÓN: CADA 2 HORAS")
        logger.info("🏦 INTEGRACIÓN: BITGET API")
        logger.info("=" * 70)
        logger.info(f"💱 Símbolos: {len(self.config.get('symbols', []))} monedas")
        logger.info(f"⏰ Timeframes: {', '.join(self.config.get('timeframes', []))}")
        logger.info(f"🕯️ Velas: {self.config.get('velas_options', [])}")
        logger.info(f"📏 ANCHO MÍNIMO: {self.config.get('min_channel_width_percent', 4)}%")
        logger.info(f"[STRATEGY] Estrategia: 1) Detectar Breakout → 2) Esperar Reentry → 3) Confirmar con Stoch y Ejecutar")
        
        if self.bitget_client:
            logger.info(f"🤖 BITGET: ✅ API Conectada")
            logger.info(f"⚡ Apalancamiento: {self.leverage_por_defecto}x")
            logger.info(f"💰 Capital por operación: ${self.capital_por_operacion}")
            if self.ejecutar_operaciones_automaticas:
                logger.info(f"🤖 AUTO-TRADING: ✅ ACTIVADO (MARGEN AISLADO)")
            else:
                logger.info(f"🤖 AUTO-TRADING: ❌ Solo señales")
        else:
            logger.info(f"🤖 BITGET: ❌ No configurado (solo señales)")
        
        logger.info("=" * 70)
        logger.info("\n[START] INICIANDO BOT...")
        
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
            logger.error(f"\n❌ Error en el bot: {e}", exc_info=True)
            logger.info("💾 Intentando guardar estado...")
            try:
                self.guardar_estado()
            except Exception as save_error:
                logger.error(f"❌ Error guardando estado: {save_error}")

# ---------------------------
# CONFIGURACIÓN SIMPLE
# ---------------------------
def crear_config_desde_entorno():
    """Configuración desde variables de entorno"""
    directorio_actual = os.path.dirname(os.path.abspath(__file__))
    telegram_chat_ids_str = os.environ.get('TELEGRAM_CHAT_ID', '-1002272872445')
    telegram_chat_ids = [cid.strip() for cid in telegram_chat_ids_str.split(',') if cid.strip()]
    
    config = {
        'min_channel_width_percent': 4.0,
        'trend_threshold_degrees': 16.0,
        'min_trend_strength_degrees': 16.0,
        'entry_margin': 0.001,
        'min_rr_ratio': 1.2,
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
        # CONFIGURACIONES BITGET
        'bitget_api_key': os.environ.get('BITGET_API_KEY'),
        'bitget_api_secret': os.environ.get('BITGET_SECRET_KEY'),
        'bitget_passphrase': os.environ.get('BITGET_PASSPHRASE'),
        'ejecutar_operaciones_automaticas': os.environ.get('EJECUTAR_OPERACIONES_AUTOMATICAS', 'false').lower() == 'true',
        'capital_por_operacion': float(os.environ.get('CAPITAL_POR_OPERACION', '2')),
        'leverage_por_defecto': int(os.environ.get('LEVERAGE_POR_DEFECTO', '10'))
    }
    
    logger.info("🔧 Configuración cargada desde variables de entorno")
    logger.info(f"📊 Número de símbolos: {len(config['symbols'])}")
    logger.info(f"🤖 Auto-trading: {config['ejecutar_operaciones_automaticas']}")
    logger.info(f"💰 Capital por operación: ${config['capital_por_operacion']}")
    
    return config

# ---------------------------
# FLASK APP Y RENDER
# ---------------------------

app = Flask(__name__)

# Crear bot con configuración desde entorno
try:
    config = crear_config_desde_entorno()
    bot = TradingBot(config)
    logger.info("🤖 Bot de trading inicializado correctamente")
except Exception as e:
    logger.error(f"❌ Error inicializando bot: {e}", exc_info=True)
    bot = None

def run_bot_loop():
    """Ejecuta el bot en un hilo separado"""
    if bot is None:
        logger.error("❌ No se pudo inicializar el bot, no se ejecutará el loop")
        return
    
    while True:
        try:
            bot.ejecutar_analisis()
            intervalo = bot.config.get('scan_interval_minutes', 1) * 60
            logger.info(f"⏳ Esperando {intervalo/60} minutos para próximo análisis...")
            time.sleep(intervalo)
        except Exception as e:
            logger.error(f"❌ Error en el hilo del bot: {e}", exc_info=True)
            time.sleep(60)

# Iniciar hilo del bot si está configurado
if bot:
    bot_thread = threading.Thread(target=run_bot_loop, daemon=True)
    bot_thread.start()
    logger.info("✅ Hilo del bot iniciado")
else:
    logger.warning("⚠️ Bot no inicializado, hilo no iniciado")

@app.route('/')
def index():
    return "Bot Breakout + Reentry con integración Bitget está en línea.", 200

@app.route('/webhook', methods=['POST'])
def telegram_webhook():
    if request.is_json:
        update = request.get_json()
        logger.info(f"📩 Update recibido de Telegram: {json.dumps(update)}")
        return jsonify({"status": "ok"}), 200
    return jsonify({"error": "Request must be JSON"}), 400

@app.route('/health', methods=['GET'])
def health_check():
    """Endpoint de salud para Render"""
    if bot:
        return jsonify({
            "status": "running",
            "bot_initialized": True,
            "timestamp": datetime.now().isoformat()
        }), 200
    else:
        return jsonify({
            "status": "error",
            "bot_initialized": False,
            "timestamp": datetime.now().isoformat()
        }), 500

@app.route('/status', methods=['GET'])
def status():
    """Endpoint para ver el estado del bot"""
    if not bot:
        return jsonify({"error": "Bot no inicializado"}), 500
    
    status_info = {
        "operaciones_activas": len(bot.operaciones_activas),
        "esperando_reentry": len(bot.esperando_reentry),
        "total_operaciones": bot.total_operaciones,
        "bitget_conectado": bot.bitget_client is not None,
        "auto_trading": bot.ejecutar_operaciones_automaticas,
        "timestamp": datetime.now().isoformat()
    }
    
    return jsonify(status_info), 200

# Configuración automática del webhook
def setup_telegram_webhook():
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
            logger.warning("⚠️ No se encontró URL para webhook")
            return
    
    try:
        # Eliminar webhook existente
        delete_url = f"https://api.telegram.org/bot{token}/deleteWebhook"
        requests.get(delete_url, timeout=10)
        
        # Configurar nuevo webhook
        set_url = f"https://api.telegram.org/bot{token}/setWebhook?url={webhook_url}"
        response = requests.get(set_url, timeout=10)
        
        if response.status_code == 200:
            logger.info(f"✅ Webhook configurado en: {webhook_url}")
        else:
            logger.error(f"❌ Error configurando webhook: {response.status_code} - {response.text}")
            
    except Exception as e:
        logger.error(f"❌ Error configurando webhook: {e}")

if __name__ == '__main__':
    # Configurar webhook al iniciar
    setup_telegram_webhook()
    
    # Obtener puerto de variable de entorno
    port = int(os.environ.get('PORT', 5000))
    
    # Iniciar servidor Flask
    app.run(host='0.0.0.0', port=port, debug=False)
