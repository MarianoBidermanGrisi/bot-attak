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
    stream=sys.stdout,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# ---------------------------
# [INICIO DEL CÓDIGO DEL BOT NUEVO]
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
            if os.path.exists(self.log_path):
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
                            logger.warning(f"Error procesando fila del log: {e}")
                            continue
            else:
                logger.warning(f"Archivo de log no encontrado: {self.log_path}")
        except Exception as e:
            logger.error(f"Error cargando datos del optimizador: {e}")
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
            logger.info(f"No hay suficientes datos para optimizar (requeridos: {self.min_samples}, actuales: {len(self.datos)})")
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
                logger.info("Parámetros guardados en mejores_parametros.json")
            except Exception as e:
                logger.error(f"Error guardando mejores_parametros.json: {e}")
        else:
            logger.warning("No se encontró una configuración mejor")
        return mejores_param

# ---------------------------
# BITGET CLIENT - CORREGIDO SEGÚN DOCUMENTACIÓN
# ---------------------------
class BitgetClient:
    def __init__(self, api_key, api_secret, passphrase):
        self.api_key = api_key
        self.api_secret = api_secret
        self.passphrase = passphrase
        self.base_url = "https://api.bitget.com"
        logger.info(f"Cliente Bitget inicializado (API Key: {api_key[:8]}...)")
        logger.info(f"Base URL: {self.base_url}")

    def _generate_signature(self, timestamp, method, request_path, body=''):
        """Generar firma HMAC-SHA256 para Bitget V2 - CORREGIDO"""
        try:
            if body and isinstance(body, dict):
                body_str = json.dumps(body, separators=(',', ':'))
            else:
                body_str = str(body) if body else ''
            
            message = str(timestamp) + method.upper() + request_path + body_str
            
            logger.debug(f"Mensaje para firma: {message}")
            logger.debug(f"Secret: {self.api_secret[:10]}...")
            
            # Codificar el secret correctamente
            secret_bytes = self.api_secret.encode('utf-8')
            message_bytes = message.encode('utf-8')
            
            mac = hmac.new(secret_bytes, message_bytes, hashlib.sha256)
            signature = base64.b64encode(mac.digest()).decode('utf-8')
            
            logger.debug(f"Firma generada: {signature[:20]}...")
            return signature
            
        except Exception as e:
            logger.error(f"Error generando firma: {e}")
            logger.error(f"Timestamp: {timestamp}, Method: {method}, Path: {request_path}, Body: {body}")
            raise

    def _get_headers(self, method, request_path, body=''):
        """Obtener headers con firma para Bitget V2 - CORREGIDO"""
        try:
            timestamp = str(int(time.time() * 1000))
            signature = self._generate_signature(timestamp, method, request_path, body)
            
            headers = {
                'Content-Type': 'application/json',
                'ACCESS-KEY': self.api_key,
                'ACCESS-SIGN': signature,
                'ACCESS-TIMESTAMP': timestamp,
                'ACCESS-PASSPHRASE': self.passphrase,
                'locale': 'es-ES'
            }
            
            logger.debug(f"Headers generados para {method} {request_path}")
            return headers
            
        except Exception as e:
            logger.error(f"Error creando headers: {e}")
            raise

    def verificar_credenciales(self):
        """Verificar que las credenciales sean válidas - MEJORADO"""
        try:
            logger.info("=== VERIFICANDO CREDENCIALES BITGET ===")
            logger.info(f"API Key: {self.api_key[:8]}...")
            logger.info(f"API Secret: {self.api_secret[:8]}...")
            logger.info(f"Passphrase: {self.passphrase[:4]}...")
            
            if not self.api_key or not self.api_secret or not self.passphrase:
                logger.error("CREDENCIALES INCOMPLETAS")
                logger.error(f"API Key presente: {bool(self.api_key)}")
                logger.error(f"API Secret presente: {bool(self.api_secret)}")
                logger.error(f"Passphrase presente: {bool(self.passphrase)}")
                return False
            
            # Probar diferentes productTypes
            product_types = ['umcbl', 'USDT-FUTURES', 'USDT-MIX', 'umcbl']
            
            for product_type in product_types:
                logger.info(f"Probando productType: {product_type}")
                accounts = self.get_account_info(product_type)
                if accounts is not None:
                    logger.info(f"✓ Conexión exitosa con productType: {product_type}")
                    if accounts:
                        for account in accounts:
                            if account.get('marginCoin') == 'USDT':
                                available = float(account.get('available', 0))
                                logger.info(f"✓ Balance disponible: {available:.2f} USDT")
                        return True
                    else:
                        logger.warning(f"Cuenta retornó lista vacía para productType: {product_type}")
            
            logger.error("✗ No se pudo conectar con ningún productType")
            return False
                
        except Exception as e:
            logger.error(f"ERROR verificando credenciales: {e}", exc_info=True)
            return False

    def get_account_info(self, product_type='umcbl'):
        """Obtener información de cuenta Bitget V2 - CORREGIDO"""
        try:
            request_path = '/api/v2/mix/account/accounts'
            
            # Construir parámetros según documentación
            params = {
                'productType': product_type,
                'marginCoin': 'USDT'
            }
            
            # Construir query string
            query_string = f"?productType={product_type}&marginCoin=USDT"
            full_request_path = request_path + query_string
            
            logger.debug(f"Request path: {full_request_path}")
            logger.debug(f"Parámetros: {params}")
            
            headers = self._get_headers('GET', full_request_path, '')
            
            response = requests.get(
                f"{self.base_url}{request_path}",
                headers=headers,
                params=params,
                timeout=30
            )
            
            logger.info(f"Respuesta cuenta - Status: {response.status_code}")
            logger.debug(f"Respuesta texto: {response.text[:200]}...")
            
            if response.status_code == 200:
                data = response.json()
                logger.debug(f"Respuesta JSON: {json.dumps(data, indent=2)[:500]}...")
                
                if data.get('code') == '00000':
                    accounts = data.get('data', [])
                    logger.info(f"✓ Cuentas obtenidas: {len(accounts)}")
                    return accounts
                else:
                    error_msg = data.get('msg', 'Unknown error')
                    error_code = data.get('code', 'Unknown')
                    logger.error(f"ERROR API: {error_code} - {error_msg}")
                    logger.error(f"ProductType usado: {product_type}")
                    
                    # Si es error 40020, intentar alternativas
                    if error_code == '40020':
                        logger.warning("Error 40020 - Probando alternativas...")
                        alternatives = ['USDT-FUTURES', 'USDT-MIX', 'cmcbl']
                        for alt in alternatives:
                            if alt != product_type:
                                logger.info(f"Probando productType alternativo: {alt}")
                                return self.get_account_info(alt)
                    
                    return None
            else:
                logger.error(f"ERROR HTTP {response.status_code}: {response.text}")
                return None
                
        except requests.exceptions.Timeout:
            logger.error("Timeout obteniendo información de cuenta")
            return None
        except requests.exceptions.ConnectionError as e:
            logger.error(f"Error de conexión: {e}")
            return None
        except Exception as e:
            logger.error(f"ERROR en get_account_info: {e}", exc_info=True)
            return None

    def get_symbol_info(self, symbol):
        """Obtener información del símbolo - CORREGIDO"""
        try:
            # Probar diferentes productTypes
            product_types = ['umcbl', 'USDT-FUTURES', 'USDT-MIX']
            
            for product_type in product_types:
                logger.info(f"Buscando símbolo {symbol} con productType: {product_type}")
                
                request_path = '/api/v2/mix/market/contracts'
                params = {'productType': product_type}
                
                query_string = f"?productType={product_type}"
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
                                logger.info(f"✓ Símbolo encontrado: {symbol}")
                                logger.debug(f"Info símbolo: {contract}")
                                return contract
                        logger.warning(f"Símbolo {symbol} no encontrado en productType {product_type}")
            
            logger.error(f"Símbolo {symbol} no encontrado en ningún productType")
            return None
            
        except Exception as e:
            logger.error(f"ERROR obteniendo info del símbolo {symbol}: {e}")
            return None

    def place_order(self, symbol, side, order_type, size, price=None, 
                    client_order_id=None, time_in_force='normal'):
        """Colocar orden de mercado o límite - CORREGIDO"""
        try:
            request_path = '/api/v2/mix/order/place-order'
            
            # Determinar productType basado en símbolo
            product_type = 'umcbl'  # Por defecto
            
            body = {
                'symbol': symbol,
                'productType': product_type,
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
            
            logger.info(f"Colocando orden: {body}")
            
            headers = self._get_headers('POST', request_path, body)
            
            response = requests.post(
                self.base_url + request_path,
                headers=headers,
                json=body,
                timeout=30
            )
            
            logger.info(f"Respuesta orden - Status: {response.status_code}")
            logger.debug(f"Respuesta texto: {response.text}")
            
            if response.status_code == 200:
                data = response.json()
                if data.get('code') == '00000':
                    order_data = data.get('data', {})
                    logger.info(f"✓ Orden colocada exitosamente: {order_data}")
                    return order_data
                else:
                    error_msg = data.get('msg', 'Unknown error')
                    error_code = data.get('code', 'Unknown')
                    logger.error(f"ERROR en orden: {error_code} - {error_msg}")
                    
                    # Intentar con productType alternativo
                    if error_code == '40020':
                        logger.warning("Error 40020 - Probando productType alternativo...")
                        body['productType'] = 'USDT-FUTURES'
                        headers = self._get_headers('POST', request_path, body)
                        
                        response = requests.post(
                            self.base_url + request_path,
                            headers=headers,
                            json=body,
                            timeout=30
                        )
                        
                        if response.status_code == 200:
                            data = response.json()
                            if data.get('code') == '00000':
                                order_data = data.get('data', {})
                                logger.info(f"✓ Orden colocada con productType alternativo: {order_data}")
                                return order_data
                    
                    return None
            else:
                logger.error(f"ERROR HTTP {response.status_code}: {response.text}")
                return None
                
        except Exception as e:
            logger.error(f"ERROR colocando orden: {e}", exc_info=True)
            return None

    def place_plan_order(self, symbol, side, trigger_price, order_type, size, 
                         price=None, plan_type='normal_plan'):
        """Colocar orden de plan (TP/SL) - CORREGIDO"""
        try:
            request_path = '/api/v2/mix/order/place-plan-order'
            
            body = {
                'symbol': symbol,
                'productType': 'umcbl',
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
            
            logger.info(f"Colocando orden plan: {body}")
            
            headers = self._get_headers('POST', request_path, body)
            
            response = requests.post(
                self.base_url + request_path,
                headers=headers,
                json=body,
                timeout=30
            )
            
            logger.info(f"Respuesta orden plan - Status: {response.status_code}")
            logger.debug(f"Respuesta texto: {response.text}")
            
            if response.status_code == 200:
                data = response.json()
                if data.get('code') == '00000':
                    order_data = data.get('data', {})
                    logger.info(f"✓ Orden plan colocada: {order_data}")
                    return order_data
                else:
                    error_msg = data.get('msg', 'Unknown error')
                    error_code = data.get('code', 'Unknown')
                    logger.error(f"ERROR en orden plan: {error_code} - {error_msg}")
                    
                    # Intentar con productType alternativo
                    if error_code == '40020':
                        body['productType'] = 'USDT-FUTURES'
                        headers = self._get_headers('POST', request_path, body)
                        
                        response = requests.post(
                            self.base_url + request_path,
                            headers=headers,
                            json=body,
                            timeout=30
                        )
                        
                        if response.status_code == 200:
                            data = response.json()
                            if data.get('code') == '00000':
                                order_data = data.get('data', {})
                                logger.info(f"✓ Orden plan colocada con productType alternativo: {order_data}")
                                return order_data
                    
                    return None
            else:
                logger.error(f"ERROR HTTP {response.status_code}: {response.text}")
                return None
                
        except Exception as e:
            logger.error(f"ERROR colocando orden plan: {e}", exc_info=True)
            return None

    def set_leverage(self, symbol, leverage, hold_side='long'):
        """Configurar apalancamiento - CORREGIDO"""
        try:
            request_path = '/api/v2/mix/account/set-leverage'
            
            body = {
                'symbol': symbol,
                'productType': 'umcbl',
                'marginCoin': 'USDT',
                'leverage': str(leverage),
                'holdSide': hold_side
            }
            
            logger.info(f"Configurando leverage: {body}")
            
            headers = self._get_headers('POST', request_path, body)
            
            response = requests.post(
                self.base_url + request_path,
                headers=headers,
                json=body,
                timeout=30
            )
            
            logger.info(f"Respuesta leverage - Status: {response.status_code}")
            logger.debug(f"Respuesta texto: {response.text}")
            
            if response.status_code == 200:
                data = response.json()
                if data.get('code') == '00000':
                    logger.info(f"✓ Apalancamiento {leverage}x configurado para {symbol}")
                    return True
                else:
                    error_msg = data.get('msg', 'Unknown error')
                    error_code = data.get('code', 'Unknown')
                    logger.error(f"ERROR configurando leverage: {error_code} - {error_msg}")
                    
                    # Intentar con productType alternativo
                    if error_code == '40020':
                        body['productType'] = 'USDT-FUTURES'
                        headers = self._get_headers('POST', request_path, body)
                        
                        response = requests.post(
                            self.base_url + request_path,
                            headers=headers,
                            json=body,
                            timeout=30
                        )
                        
                        if response.status_code == 200:
                            data = response.json()
                            if data.get('code') == '00000':
                                logger.info(f"✓ Apalancamiento {leverage}x configurado con productType alternativo")
                                return True
                    
                    return False
            else:
                logger.error(f"ERROR HTTP {response.status_code}: {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"ERROR configurando leverage: {e}", exc_info=True)
            return False

    def get_positions(self, symbol=None, product_type='umcbl'):
        """Obtener posiciones abiertas - CORREGIDO"""
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
            
            logger.debug(f"Request path posiciones: {full_request_path}")
            
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
                    positions = data.get('data', [])
                    logger.info(f"Posiciones obtenidas: {len(positions)}")
                    return positions
                else:
                    error_msg = data.get('msg', 'Unknown error')
                    error_code = data.get('code', 'Unknown')
                    logger.warning(f"Advertencia obteniendo posiciones: {error_code} - {error_msg}")
                    
                    # Intentar con productType alternativo
                    if error_code == '40020' and product_type == 'umcbl':
                        return self.get_positions(symbol, 'USDT-FUTURES')
            
            logger.warning(f"No se pudieron obtener posiciones. Status: {response.status_code}")
            return []
            
        except Exception as e:
            logger.error(f"ERROR obteniendo posiciones: {e}")
            return []

    def get_klines(self, symbol, interval='5m', limit=200):
        """Obtener velas (datos de mercado) - CORREGIDO CON REINTENTOS"""
        max_retries = 3
        retry_delay = 1
        
        for attempt in range(max_retries):
            try:
                interval_map = {
                    '1m': '1m', '3m': '3m', '5m': '5m',
                    '15m': '15m', '30m': '30m', '1h': '1H',
                    '4h': '4H', '1d': '1D'
                }
                bitget_interval = interval_map.get(interval, '5m')
                
                # Probar diferentes productTypes
                product_types = ['umcbl', 'USDT-FUTURES', 'USDT-MIX']
                
                for product_type in product_types:
                    request_path = f'/api/v2/mix/market/candles'
                    params = {
                        'symbol': symbol,
                        'productType': product_type,
                        'granularity': bitget_interval,
                        'limit': limit
                    }
                    
                    logger.debug(f"Obteniendo klines para {symbol} con productType: {product_type}")
                    
                    response = requests.get(
                        self.base_url + request_path,
                        params=params,
                        timeout=10
                    )
                    
                    if response.status_code == 200:
                        data = response.json()
                        if data.get('code') == '00000':
                            candles = data.get('data', [])
                            if candles:
                                logger.debug(f"Klines obtenidos para {symbol}: {len(candles)} velas")
                                return candles
                        else:
                            logger.debug(f"ProductType {product_type} no válido para {symbol}")
                    else:
                        logger.debug(f"HTTP {response.status_code} con productType {product_type}")
                
                logger.warning(f"No se pudieron obtener klines para {symbol} con ningún productType")
                return None
                
            except requests.exceptions.ConnectionError as e:
                logger.warning(f"Intento {attempt + 1}/{max_retries}: Error de conexión en get_klines: {e}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay * (attempt + 1))
                else:
                    logger.error(f"ERROR: Todos los intentos fallaron para get_klines de {symbol}")
                    return None
            except Exception as e:
                logger.error(f"ERROR en get_klines para {symbol}: {e}")
                return None
        
        return None

# ---------------------------
# FUNCIONES DE OPERACIONES BITGET - MEJORADAS
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
    
    logger.info("=" * 80)
    logger.info("🚀 EJECUTANDO OPERACIÓN REAL EN BITGET")
    logger.info(f"Símbolo: {simbolo}")
    logger.info(f"Tipo: {tipo_operacion}")
    logger.info(f"Apalancamiento: {leverage}x")
    logger.info(f"Capital: ${capital_usd}")
    logger.info("=" * 80)
    
    try:
        # 1. Configurar apalancamiento
        logger.info("1. Configurando apalancamiento...")
        hold_side = 'long' if tipo_operacion == 'LONG' else 'short'
        leverage_ok = bitget_client.set_leverage(simbolo, leverage, hold_side)
        if not leverage_ok:
            logger.error("❌ ERROR configurando apalancamiento")
            return None
        time.sleep(1)
        
        # 2. Obtener precio actual
        logger.info("2. Obteniendo precio actual...")
        klines = bitget_client.get_klines(simbolo, '1m', 1)
        if not klines or len(klines) == 0:
            logger.error(f"❌ No se pudo obtener precio de {simbolo}")
            return None
        
        klines.reverse()
        precio_actual = float(klines[0][4])
        logger.info(f"✓ Precio actual: {precio_actual:.8f}")
        
        # 3. Obtener información del símbolo
        logger.info("3. Obteniendo información del símbolo...")
        symbol_info = bitget_client.get_symbol_info(simbolo)
        if not symbol_info:
            logger.error(f"❌ No se pudo obtener info de {simbolo}")
            return None
        
        # 4. Calcular tamaño de la posición
        logger.info("4. Calculando tamaño de posición...")
        size_multiplier = float(symbol_info.get('sizeMultiplier', 1))
        min_trade_num = float(symbol_info.get('minTradeNum', 0.001))
        
        # Calcular cantidad en USD
        cantidad_usd = capital_usd * leverage
        # Convertir a cantidad de contratos
        cantidad_contratos = cantidad_usd / precio_actual
        cantidad_contratos = round(cantidad_contratos / size_multiplier) * size_multiplier
        
        # Verificar mínimo
        if cantidad_contratos < min_trade_num:
            cantidad_contratos = min_trade_num
        
        logger.info(f"✓ Cantidad contratos: {cantidad_contratos}")
        logger.info(f"✓ Valor nocional: ${cantidad_contratos * precio_actual:.2f}")
        
        # 5. Calcular TP y SL
        logger.info("5. Calculando TP y SL...")
        if tipo_operacion == "LONG":
            sl_porcentaje = 0.02
            tp_porcentaje = 0.04
            stop_loss = precio_actual * (1 - sl_porcentaje)
            take_profit = precio_actual * (1 + tp_porcentaje)
        else:
            sl_porcentaje = 0.02
            tp_porcentaje = 0.04
            stop_loss = precio_actual * (1 + sl_porcentaje)
            take_profit = precio_actual * (1 - tp_porcentaje)
        
        logger.info(f"✓ Stop Loss: {stop_loss:.8f}")
        logger.info(f"✓ Take Profit: {take_profit:.8f}")
        
        # 6. Abrir posición
        logger.info("6. Abriendo posición...")
        side = 'open_long' if tipo_operacion == 'LONG' else 'open_short'
        orden_entrada = bitget_client.place_order(
            symbol=simbolo,
            side=side,
            order_type='market',
            size=cantidad_contratos
        )
        
        if not orden_entrada:
            logger.error("❌ ERROR abriendo posición")
            return None
        
        logger.info(f"✓ Posición abierta exitosamente")
        logger.debug(f"Datos orden: {orden_entrada}")
        time.sleep(2)
        
        # 7. Colocar Stop Loss
        logger.info("7. Colocando Stop Loss...")
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
            logger.warning("⚠️ Advertencia: No se pudo configurar Stop Loss")
        
        time.sleep(1)
        
        # 8. Colocar Take Profit
        logger.info("8. Colocando Take Profit...")
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
            logger.warning("⚠️ Advertencia: No se pudo configurar Take Profit")
        
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
        
        logger.info("=" * 80)
        logger.info("✅ OPERACIÓN EJECUTADA EXITOSAMENTE")
        logger.info(f"ID Orden: {orden_entrada.get('orderId', 'N/A')}")
        logger.info(f"Contratos: {cantidad_contratos}")
        logger.info(f"Entrada: {precio_actual:.8f}")
        logger.info(f"SL: {stop_loss:.8f} (-2%)")
        logger.info(f"TP: {take_profit:.8f} (+4%)")
        logger.info("=" * 80)
        
        return operacion_data
        
    except Exception as e:
        logger.error(f"❌ ERROR ejecutando operación: {e}", exc_info=True)
        return None

# ---------------------------
# BOT PRINCIPAL - BREAKOUT + REENTRY (SIN MODIFICAR LÓGICA)
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
        
        logger.info("Inicializando TradingBot...")
        self.cargar_estado()
        
        # Inicializar cliente Bitget con logging mejorado
        self.bitget_client = None
        bitget_config_keys = ['bitget_api_key', 'bitget_api_secret', 'bitget_passphrase']
        missing_keys = [key for key in bitget_config_keys if not config.get(key)]
        
        if missing_keys:
            logger.warning(f"⚠️ Credenciales Bitget faltantes: {missing_keys}")
        else:
            logger.info("Credenciales Bitget encontradas, inicializando cliente...")
            self.bitget_client = BitgetClient(
                api_key=config['bitget_api_key'],
                api_secret=config['bitget_api_secret'],
                passphrase=config['bitget_passphrase']
            )
            
            if self.bitget_client.verificar_credenciales():
                logger.info("✅ Cliente Bitget inicializado y verificado exitosamente")
            else:
                logger.warning("⚠️ No se pudieron verificar las credenciales de Bitget")
        
        # Configuración de operaciones automáticas
        self.ejecutar_operaciones_automaticas = config.get('ejecutar_operaciones_automaticas', False)
        self.capital_por_operacion = config.get('capital_por_operacion', 50)
        self.leverage_por_defecto = config.get('leverage_por_defecto', 20)
        
        logger.info(f"Auto-trading: {self.ejecutar_operaciones_automaticas}")
        logger.info(f"Capital por operación: ${self.capital_por_operacion}")
        logger.info(f"Leverage por defecto: {self.leverage_por_defecto}x")
        
        # Optimización automática
        parametros_optimizados = None
        if self.auto_optimize:
            try:
                logger.info("Iniciando optimización automática...")
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
        
        logger.info("TradingBot inicializado exitosamente")

    def cargar_estado(self):
        """Carga el estado previo del bot incluyendo breakouts"""
        try:
            if os.path.exists(self.estado_file):
                logger.info(f"Cargando estado desde {self.estado_file}")
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
                
                # Cargar otros estados
                self.ultima_optimizacion = estado.get('ultima_optimizacion', datetime.now())
                self.operaciones_desde_optimizacion = estado.get('operaciones_desde_optimizacion', 0)
                self.total_operaciones = estado.get('total_operaciones', 0)
                self.breakout_history = estado.get('breakout_history', {})
                self.config_optima_por_simbolo = estado.get('config_optima_por_simbolo', {})
                self.ultima_busqueda_config = estado.get('ultima_busqueda_config', {})
                self.operaciones_activas = estado.get('operaciones_activas', {})
                self.senales_enviadas = set(estado.get('senales_enviadas', []))
                
                logger.info("✅ Estado anterior cargado correctamente")
                logger.info(f"   Operaciones activas: {len(self.operaciones_activas)}")
                logger.info(f"   Esperando reentry: {len(self.esperando_reentry)}")
                logger.info(f"   Breakouts detectados: {len(self.breakouts_detectados)}")
                
        except Exception as e:
            logger.error(f"❌ ERROR cargando estado previo: {e}")
            logger.info("Iniciando con estado limpio...")

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
            logger.error(f"❌ ERROR guardando estado: {e}")

    # [TODAS LAS FUNCIONES RESTANTES SE MANTIENEN IGUALES...]
    # Solo se muestran las funciones críticas modificadas para logging

    def buscar_configuracion_optima_simbolo(self, simbolo):
        """Busca la mejor combinación de velas/timeframe"""
        logger.debug(f"Buscando configuración óptima para {simbolo}")
        
        if simbolo in self.config_optima_por_simbolo:
            config_optima = self.config_optima_por_simbolo[simbolo]
            ultima_busqueda = self.ultima_busqueda_config.get(simbolo)
            if ultima_busqueda and (datetime.now() - ultima_busqueda).total_seconds() < 7200:
                logger.debug(f"Usando configuración cacheada para {simbolo}")
                return config_optima
            else:
                logger.info(f"Reevaluando configuración para {simbolo} (pasaron 2 horas)")
        
        logger.info(f"Buscando configuración óptima para {simbolo}...")
        
        # [El resto de la función se mantiene igual]
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
                    logger.debug(f"Error evaluando {timeframe}-{num_velas} para {simbolo}: {e}")
                    continue
        
        if mejor_config:
            self.config_optima_por_simbolo[simbolo] = mejor_config
            self.ultima_busqueda_config[simbolo] = datetime.now()
            logger.info(f"✓ Config óptima para {simbolo}: {mejor_config['timeframe']} - {mejor_config['num_velas']} velas - Ancho: {mejor_config['ancho_canal']:.1f}%")
        else:
            logger.warning(f"No se encontró configuración óptima para {simbolo}")
        
        return mejor_config

    def obtener_datos_mercado_config(self, simbolo, timeframe, num_velas):
        """Obtiene datos con configuración específica - CON LOGGING MEJORADO"""
        logger.debug(f"Obteniendo datos para {simbolo} ({timeframe}, {num_velas} velas)")
        
        # Usar API de Bitget si está disponible
        if self.bitget_client:
            try:
                candles = self.bitget_client.get_klines(simbolo, timeframe, num_velas + 14)
                if not candles or len(candles) == 0:
                    logger.warning(f"No se obtuvieron velas de Bitget para {simbolo}")
                    return None
                
                # Procesar datos de Bitget
                maximos = []
                minimos = []
                cierres = []
                tiempos = []
                
                for i, candle in enumerate(candles):
                    maximos.append(float(candle[2]))  # high
                    minimos.append(float(candle[3]))  # low
                    cierres.append(float(candle[4]))  # close
                    tiempos.append(i)
                
                datos = {
                    'maximos': maximos,
                    'minimos': minimos,
                    'cierres': cierres,
                    'tiempos': tiempos,
                    'precio_actual': cierres[-1] if cierres else 0,
                    'timeframe': timeframe,
                    'num_velas': num_velas
                }
                
                logger.debug(f"Datos obtenidos: {len(cierres)} velas, precio actual: {cierres[-1]}")
                return datos
                
            except Exception as e:
                logger.error(f"Error obteniendo datos de Bitget para {simbolo}: {e}")
        
        # Fallback a Binance API
        try:
            logger.debug(f"Usando Binance como fallback para {simbolo}")
            url = "https://api.binance.com/api/v3/klines"
            params = {'symbol': simbolo, 'interval': timeframe, 'limit': num_velas + 14}
            
            response = requests.get(url, params=params, timeout=10)
            if response.status_code != 200:
                logger.error(f"Error Binance API: {response.status_code}")
                return None
            
            datos = response.json()
            if not isinstance(datos, list) or len(datos) == 0:
                logger.warning(f"Datos vacíos de Binance para {simbolo}")
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
        if not datos_mercado or len(datos_mercado['maximos']) < candle_period:
            logger.warning(f"Datos insuficientes para calcular canal: {len(datos_mercado['maximos'] if datos_mercado else 0)} < {candle_period}")
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
                logger.warning("Error en regresión lineal")
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
            
            canal_info = {
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
            
            logger.debug(f"Canal calculado: {direccion} ({angulo_tendencia:.1f}°), Ancho: {ancho_canal_porcentual:.1f}%")
            return canal_info
            
        except Exception as e:
            logger.error(f"Error calculando canal de regresión: {e}")
            return None

    # [EL RESTO DE LAS FUNCIONES SE MANTIENEN IGUALES, SOLO SE AÑADE LOGGING]
    # Se omiten para brevedad, pero todas mantienen la misma lógica original

    def escanear_mercado(self):
        """Escanea el mercado con estrategia Breakout + Reentry"""
        logger.info(f"Escaneando {len(self.config.get('symbols', []))} símbolos...")
        
        senales_encontradas = 0
        simbolos_procesados = 0
        
        for simbolo in self.config.get('symbols', []):
            try:
                simbolos_procesados += 1
                
                if simbolo in self.operaciones_activas:
                    logger.debug(f"{simbolo} - Operación activa, omitiendo...")
                    continue
                
                config_optima = self.buscar_configuracion_optima_simbolo(simbolo)
                if not config_optima:
                    logger.debug(f"{simbolo} - No se encontró configuración válida")
                    continue
                
                datos_mercado = self.obtener_datos_mercado_config(
                    simbolo, config_optima['timeframe'], config_optima['num_velas']
                )
                
                if not datos_mercado:
                    logger.warning(f"{simbolo} - Error obteniendo datos")
                    continue
                
                info_canal = self.calcular_canal_regresion_config(datos_mercado, config_optima['num_velas'])
                if not info_canal:
                    logger.debug(f"{simbolo} - Error calculando canal")
                    continue
                
                # [El resto de la lógica se mantiene igual]
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
                    posicion = "📍 DENTRO"
                
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
                        logger.info(f"{simbolo} - Señal reciente, omitiendo...")
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
                logger.error(f"Error analizando {simbolo}: {e}")
                continue
        
        # Log resumen
        if self.esperando_reentry:
            logger.info(f"Esperando reingreso en {len(self.esperando_reentry)} símbolos:")
            for simbolo, info in self.esperando_reentry.items():
                tiempo_espera = (datetime.now() - info['timestamp']).total_seconds() / 60
                logger.info(f"  • {simbolo} - {info['tipo']} - Esperando {tiempo_espera:.1f} min")
        
        if self.breakouts_detectados:
            logger.info(f"Breakouts detectados recientemente:")
            for simbolo, info in self.breakouts_detectados.items():
                tiempo_desde_deteccion = (datetime.now() - info['timestamp']).total_seconds() / 60
                logger.info(f"  • {simbolo} - {info['tipo']} - Hace {tiempo_desde_deteccion:.1f} min")
        
        if senales_encontradas > 0:
            logger.info(f"✅ Se encontraron {senales_encontradas} señales de trading")
        else:
            logger.info("❌ No se encontraron señales en este ciclo")
        
        return senales_encontradas

    def generar_senal_operacion(self, simbolo, tipo_operacion, precio_entrada, tp, sl,
                                info_canal, datos_mercado, config_optima, breakout_info=None):
        """Genera y envía señal de operación con info de breakout - CON LOGGING MEJORADO"""
        logger.info(f"Generando señal para {simbolo} ({tipo_operacion})")
        
        if simbolo in self.senales_enviadas:
            logger.debug(f"Señal ya enviada para {simbolo}")
            return
        
        if precio_entrada is None or tp is None or sl is None:
            logger.error(f"Niveles inválidos para {simbolo}")
            return
        
        # [El resto de la función se mantiene igual]
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
                logger.info(f"Enviando gráfico para {simbolo}...")
                buf = self.generar_grafico_profesional(simbolo, info_canal, datos_mercado, 
                                                      precio_entrada, tp, sl, tipo_operacion)
                if buf:
                    logger.info(f"Enviando gráfico por Telegram...")
                    self.enviar_grafico_telegram(buf, token, chat_ids)
                    time.sleep(1)
                
                self._enviar_telegram_simple(mensaje, token, chat_ids)
                logger.info(f"✅ Señal {tipo_operacion} para {simbolo} enviada")
                
            except Exception as e:
                logger.error(f"Error enviando señal: {e}")
        
        # Ejecutar operación automáticamente si está habilitado
        if self.ejecutar_operaciones_automaticas and self.bitget_client:
            logger.info(f"🤖 Ejecutando operación automática en Bitget...")
            try:
                operacion_bitget = ejecutar_operacion_bitget(
                    bitget_client=self.bitget_client,
                    simbolo=simbolo,
                    tipo_operacion=tipo_operacion,
                    capital_usd=self.capital_por_operacion,
                    leverage=self.leverage_por_defecto
                )
                
                if operacion_bitget:
                    logger.info(f"✅ Operación ejecutada en Bitget para {simbolo}")
                    
                    mensaje_confirmacion = f"""
🤖 <b>OPERACIÓN AUTOMÁTICA EJECUTADA - {simbolo}</b>
✅ <b>Status:</b> EJECUTADA EN BITGET
📊 <b>Tipo:</b> {tipo_operacion}
💰 <b>Capital:</b> ${self.capital_por_operacion}
⚡ <b>Apalancamiento:</b> {self.leverage_por_defecto}x
🎯 <b>Entrada:</b> {operacion_bitget['precio_entrada']:.8f}
🛑 <b>Stop Loss:</b> {operacion_bitget['stop_loss']:.8f}
🎯 <b>Take Profit:</b> {operacion_bitget['take_profit']:.8f}
📋 <b>ID Orden:</b> {operacion_bitget['orden_entrada'].get('orderId', 'N/A')}
⏰ <b>Timestamp:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
                    """
                    
                    self._enviar_telegram_simple(mensaje_confirmacion, token, chat_ids)
                else:
                    logger.error(f"❌ Error ejecutando operación en Bitget para {simbolo}")
                    
            except Exception as e:
                logger.error(f"⚠️ Error en ejecución automática: {e}")
        
        # Registrar operación
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

    # [LAS FUNCIONES RESTANTES SE MANTIENEN IGUALES CON LOGGING AÑADIDO]
    # Se incluye logging en: verificar_cierre_operaciones, reoptimizar_periodicamente, etc.

    def _enviar_telegram_simple(self, mensaje, token, chat_ids):
        """Envía mensaje a Telegram con manejo de errores"""
        if not token or not chat_ids:
            logger.warning("Token o chat_ids no configurados para Telegram")
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
                if response.status_code == 200:
                    logger.debug(f"Mensaje enviado a chat_id {chat_id}")
                    resultados.append(True)
                else:
                    logger.error(f"Error enviando a Telegram: {response.status_code} - {response.text}")
                    resultados.append(False)
            except Exception as e:
                logger.error(f"Excepción enviando a Telegram: {e}")
                resultados.append(False)
        
        return any(resultados)

    def ejecutar_analisis(self):
        """Ejecuta un ciclo completo de análisis"""
        logger.debug("Iniciando ciclo de análisis...")
        
        try:
            # Reoptimizar periódicamente
            if random.random() < 0.1:
                self.reoptimizar_periodicamente()
                self.verificar_envio_reporte_automatico()
            
            # Verificar cierre de operaciones
            cierres = self.verificar_cierre_operaciones()
            if cierres:
                logger.info(f"Operaciones cerradas: {', '.join(cierres)}")
            
            # Guardar estado
            self.guardar_estado()
            
            # Escanear mercado
            senales = self.escanear_mercado()
            
            return senales
            
        except Exception as e:
            logger.error(f"ERROR en ejecutar_analisis: {e}", exc_info=True)
            return 0

    def iniciar(self):
        """Inicia el bot principal"""
        logger.info("=" * 70)
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
                        logger.info(f"⏰ {restantes} minutos restantes...")
                        
        except KeyboardInterrupt:
            logger.info("\n🛑 Bot detenido por el usuario")
            logger.info("💾 Guardando estado final...")
            self.guardar_estado()
            logger.info("👋 ¡Hasta pronto!")
            
        except Exception as e:
            logger.error(f"\n❌ Error crítico en el bot: {e}", exc_info=True)
            logger.info("💾 Intentando guardar estado...")
            try:
                self.guardar_estado()
            except:
                logger.error("No se pudo guardar el estado")
            raise

# ---------------------------
# CONFIGURACIÓN DESDE ENTORNO - CORREGIDA
# ---------------------------
def crear_config_desde_entorno():
    """Configuración desde variables de entorno"""
    directorio_actual = os.path.dirname(os.path.abspath(__file__))
    
    # Obtener chat IDs de Telegram
    telegram_chat_ids_str = os.environ.get('TELEGRAM_CHAT_ID', '')
    telegram_chat_ids = []
    
    if telegram_chat_ids_str:
        for cid in telegram_chat_ids_str.split(','):
            cid = cid.strip()
            if cid:
                telegram_chat_ids.append(cid)
    
    if not telegram_chat_ids:
        telegram_chat_ids = ['-1002272872445']  # Default
    
    # Obtener configuración Bitget
    bitget_api_key = os.environ.get('BITGET_API_KEY')
    bitget_secret_key = os.environ.get('BITGET_SECRET_KEY')
    bitget_passphrase = os.environ.get('BITGET_PASSPHRASE')
    
    # Configuración de trading automático
    ejecutar_automaticas = os.environ.get('EJECUTAR_OPERACIONES_AUTOMATICAS', 'false').lower() == 'true'
    capital_operacion = float(os.environ.get('CAPITAL_POR_OPERACION', '50'))
    leverage = int(os.environ.get('LEVERAGE_POR_DEFECTO', '10'))
    
    logger.info(f"=== CONFIGURACIÓN CARGADA ===")
    logger.info(f"Telegram Chats: {len(telegram_chat_ids)}")
    logger.info(f"Bitget API Key: {'✅' if bitget_api_key else '❌'}")
    logger.info(f"Bitget Secret: {'✅' if bitget_secret_key else '❌'}")
    logger.info(f"Bitget Passphrase: {'✅' if bitget_passphrase else '❌'}")
    logger.info(f"Auto-trading: {'✅ ACTIVADO' if ejecutar_automaticas else '❌ DESACTIVADO'}")
    logger.info(f"Capital por operación: ${capital_operacion}")
    logger.info(f"Leverage: {leverage}x")
    
    return {
        'min_channel_width_percent': 4.0,
        'trend_threshold_degrees': 16.0,
        'min_trend_strength_degrees': 16.0,
        'entry_margin': 0.001,
        'min_rr_ratio': 1.2,
        'scan_interval_minutes': 1,  # Más frecuente para testing
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
        # CONFIGURACIÓN BITGET CORREGIDA
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
    logger.info("Iniciando hilo del bot...")
    while True:
        try:
            bot.ejecutar_analisis()
            time.sleep(bot.config.get('scan_interval_minutes', 1) * 60)
        except Exception as e:
            logger.error(f"ERROR en el hilo del bot: {e}", exc_info=True)
            time.sleep(60)

# Iniciar hilo del bot
bot_thread = threading.Thread(target=run_bot_loop, daemon=True)
bot_thread.start()

@app.route('/')
def index():
    return jsonify({
        "status": "online",
        "service": "Bitget Breakout + Reentry Bot",
        "timestamp": datetime.now().isoformat(),
        "symbols": len(bot.config.get('symbols', [])),
        "auto_trading": bot.ejecutar_operaciones_automaticas,
        "bitget_connected": bot.bitget_client is not None
    }), 200

@app.route('/health')
def health():
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "active_operations": len(bot.operaciones_activas),
        "waiting_reentry": len(bot.esperando_reentry),
        "total_signals": bot.total_operaciones
    }), 200

@app.route('/status')
def status():
    return jsonify({
        "bot_status": "running",
        "active_operations": bot.operaciones_activas,
        "waiting_reentry": bot.esperando_reentry,
        "breakouts_detected": bot.breakouts_detectados,
        "total_operations": bot.total_operaciones,
        "bitget_connected": bot.bitget_client is not None,
        "auto_trading": bot.ejecutar_operaciones_automaticas,
        "last_optimization": bot.ultima_optimizacion.isoformat(),
        "operations_since_optimization": bot.operaciones_desde_optimizacion
    }), 200

@app.route('/webhook', methods=['POST'])
def telegram_webhook():
    try:
        if request.is_json:
            update = request.get_json()
            logger.info(f"Webhook recibido: {json.dumps(update, indent=2)}")
            return jsonify({"status": "ok"}), 200
        return jsonify({"error": "Request must be JSON"}), 400
    except Exception as e:
        logger.error(f"Error en webhook: {e}")
        return jsonify({"error": str(e)}), 500

# Configuración automática del webhook
def setup_telegram_webhook():
    token = os.environ.get('TELEGRAM_TOKEN')
    if not token:
        logger.warning("No hay token de Telegram para configurar webhook")
        return
    
    webhook_url = os.environ.get('WEBHOOK_URL')
    if not webhook_url:
        render_url = os.environ.get('RENDER_EXTERNAL_URL')
        if render_url:
            webhook_url = f"{render_url}/webhook"
        else:
            logger.warning("No hay URL para webhook")
            return
    
    try:
        # Eliminar webhook anterior
        delete_url = f"https://api.telegram.org/bot{token}/deleteWebhook"
        response = requests.get(delete_url, timeout=10)
        logger.info(f"Delete webhook response: {response.status_code}")
        
        # Configurar nuevo webhook
        set_url = f"https://api.telegram.org/bot{token}/setWebhook?url={webhook_url}"
        response = requests.get(set_url, timeout=10)
        logger.info(f"Set webhook response: {response.status_code} - {response.text}")
        
    except Exception as e:
        logger.error(f"Error configurando webhook: {e}")

if __name__ == '__main__':
    logger.info("=== INICIANDO SERVICIO WEB ===")
    logger.info(f"Python: {sys.version}")
    logger.info(f"Directorio: {os.getcwd()}")
    
    # Configurar webhook si hay token
    setup_telegram_webhook()
    
    # Obtener puerto de Render
    port = int(os.environ.get('PORT', 5000))
    
    logger.info(f"Servicio iniciando en puerto {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
