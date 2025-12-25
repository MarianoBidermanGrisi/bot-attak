# bot_web_service.py
# Adaptación para Render del bot Breakout + Reentry con correcciones Bitget
# CORRECCIÓN: Error 400172 planType Illegal type - SOLUCIONADO
# - Endpoint cambiado de /place-plan-order a /place-tpsl-order
# - Parámetro side cambiado a holdSide (buy/sell para one-way)
# - body actualizado según documentación oficial Bitget API
# CORRECCIONES PREVIAS APLICADAS:
# 1. ✅ CORREGIDO 40009: Firma HMAC inconsistente
# 2. ✅ CORREGIDO 40020: Parámetros rechazados
# 3. ✅ CORREGIDO URL Malformada: Query string
# 4. ✅ CORREGIDO Interval Incompatible: Timeframes
# 5. ✅ CORREGIDO Símbolo Rechazado: Validación
# 6. ✅ MEJORADO: Retry logic para firma HMAC
# 7. ✅ MEJORADO: Logging detallado para debugging

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
import warnings
import urllib.parse
# Importar configuración de mínimos de Bitget
try:
    from config.bitget_config import get_minimum_size, get_recommended_leverage, get_price_precision
    BITGET_CONFIG_AVAILABLE = True
except ImportError:
    BITGET_CONFIG_AVAILABLE = False
    # logger.warning("⚠️ config/bitget_config.py no disponible, usando valores por defecto")
import matplotlib
matplotlib.use('Agg')  # Backend sin GUI
import matplotlib.pyplot as plt
import mplfinance as mpf
import pandas as pd
from io import BytesIO
from flask import Flask, request, jsonify
import threading
import logging

# Configuración mejorada de matplotlib para evitar warnings de emojis
def configurar_matplotlib():
    """Configura matplotlib para manejar emojis correctamente"""
    # Suprimir warnings de fuentes de manera más agresiva
    warnings.filterwarnings('ignore', category=UserWarning)
    warnings.filterwarnings('ignore', message='.*Glyph.*missing.*font.*')
    warnings.filterwarnings('ignore', message='.*font.*warning.*')
    
    # Configurar fuentes con soporte para emojis y caracteres especiales
    plt.rcParams['font.family'] = ['DejaVu Sans', 'Arial Unicode MS', 'Noto Sans CJK SC', 'sans-serif']
    plt.rcParams['axes.unicode_minus'] = False
    plt.rcParams['font.size'] = 10
    plt.rcParams['figure.facecolor'] = '#1a1a1a'
    plt.rcParams['axes.facecolor'] = '#1a1a1a'
    
    # Configurar parámetros adicionales para evitar warnings
    matplotlib.rcParams['savefig.dpi'] = 100
    matplotlib.rcParams['savefig.bbox'] = 'tight'
    matplotlib.rcParams['savefig.facecolor'] = '#1a1a1a'
    
    # Configurar el logger de matplotlib para suprimir warnings
    logging.getLogger('matplotlib').setLevel(logging.ERROR)

# Configurar matplotlib al inicio
configurar_matplotlib()

# Suprimir todos los warnings de matplotlib de forma global
warnings.filterwarnings('ignore', category=UserWarning, module='matplotlib.*')
warnings.filterwarnings('ignore', message='.*Glyph.*missing.*')
warnings.filterwarnings('ignore', message='.*font.*')

# Configurar logging detallado
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('bot_debug.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

# ---------------------------
# [INICIO DEL CÓDIGO DEL BOT NUEVO]
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
            logger.info("✅ Optimizador: mejores parámetros encontrados:", mejores_param)
            try:
                with open("mejores_parametros.json", "w", encoding='utf-8') as f:
                    json.dump(mejores_param, f, indent=2)
            except Exception as e:
                logger.error("⚠ Error guardando mejores_parametros.json:", e)
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
        self.product_type = "USDT-FUTURES"
        logger.info(f"Cliente Bitget inicializado con API Key: {api_key[:10]}...")

    def _normalize_body(self, body):
        """
        ✅ CORREGIDO 40009: Normaliza el body a string JSON sin espacios para la firma.
        Evita errores de firma 40009 por diferencias en JSON.
        """
        if body is None or body == '':
            return ''
        
        if isinstance(body, dict):
            # ✅ CORREGIDO: Ordenar keys para consistencia en la firma HMAC
            # Manejar valores None, convertir a string apropiado
            normalized_body = {}
            for k, v in sorted(body.items()):
                if v is None:
                    normalized_body[k] = ''
                elif isinstance(v, (int, float)):
                    normalized_body[k] = v
                else:
                    normalized_body[k] = str(v)
            # Convertir dict a JSON con separadores compactos (sin espacios)
            return json.dumps(normalized_body, separators=(',', ':'))
        
        # Si ya es string, parsearlo y volver a serializar para normalizar
        try:
            parsed = json.loads(body)
            # ✅ CORREGIDO: Ordenar keys para consistencia
            if isinstance(parsed, dict):
                normalized_parsed = {}
                for k, v in sorted(parsed.items()):
                    if v is None:
                        normalized_parsed[k] = ''
                    elif isinstance(v, (int, float)):
                        normalized_parsed[k] = v
                    else:
                        normalized_parsed[k] = str(v)
                return json.dumps(normalized_parsed, separators=(',', ':'))
            else:
                return json.dumps(parsed, separators=(',', ':'))
        except (json.JSONDecodeError, TypeError):
            # Si no es JSON válido, devolver como string limpio
            return str(body).strip() if body else ''

    def _build_encoded_query_string(self, params):
        """
        ✅ CORREGIDO 40003: Construye query string con URL encoding correcto
        """
        if not params:
            return ""
        
        # Filtrar parámetros None o vacíos
        filtered_params = {k: v for k, v in params.items() if v is not None and v != ''}
        if not filtered_params:
            return ""
        
        # Ordenar parámetros para consistencia
        sorted_params = {k: str(v) for k, v in sorted(filtered_params.items())}
        
        # Construir query string con URL encoding
        query_parts = []
        for key, value in sorted_params.items():
            # ✅ CORREGIDO: Usar quote_plus para mejor compatibilidad
            encoded_key = urllib.parse.quote_plus(str(key))
            encoded_value = urllib.parse.quote_plus(str(value))
            query_parts.append(f"{encoded_key}={encoded_value}")
        
        return "?" + "&".join(query_parts)

    def _validate_request_params(self, method, params, body):
        """
        ✅ CORREGIDO 40020: Validación mejorada de parámetros antes de hacer requests
        - Evita errores 40020 por validaciones demasiado estrictas
        """
        try:
            # Validaciones básicas mínimas
            if not self.api_key or not self.api_secret or not self.passphrase:
                logger.error("❌ Credenciales incompletas para request")
                return False
            
            # Validar que el método sea válido
            valid_methods = ['GET', 'POST', 'PUT', 'DELETE']
            if method.upper() not in valid_methods:
                logger.error(f"❌ Método HTTP inválido: {method}")
                return False
            
            # ✅ CORREGIDO: Validación más permisiva para params
            if params:
                # Solo validar campos críticos, no rechazar por campos adicionales
                critical_fields = ['symbol']
                
                # Verificar compatibilidad solo para campos críticos
                for field in critical_fields:
                    if field in params:
                        symbol = params.get(field)
                        if not self._validar_simbolo_compatible(symbol):
                            logger.warning(f"⚠️ Símbolo {symbol} podría no ser compatible, continuando...")
                            # No rechazar, solo advertencia
            
            # ✅ CORREGIDO: Validación más permisiva para body
            if method.upper() == 'POST' and body:
                if isinstance(body, dict):
                    # Solo verificar campos realmente requeridos por la API
                    required_fields = ['symbol', 'side', 'orderType', 'size']
                    missing_fields = [field for field in required_fields if field not in body]
                    
                    if missing_fields:
                        logger.error(f"❌ Campos requeridos faltantes en body: {missing_fields}")
                        return False
                    
                    # Validar valores específicos con mensajes más claros
                    if 'symbol' in body:
                        symbol = body['symbol']
                        if not self._validar_simbolo_compatible(symbol):
                            logger.warning(f"⚠️ Símbolo {symbol} podría no ser compatible con {self.product_type}")
                            # No rechazar por compatibilidad, solo advertencia
            
            # Si llegamos aquí, pasar la validación
            return True
            
        except Exception as e:
            logger.error(f"❌ Error en validación de parámetros: {e}")
            # En caso de error en validación, permitir continuar
            logger.warning("⚠️ Error en validación, continuando con la request...")
            return True

    def _validar_product_type(self, product_type):
        """
        ✅ CORREGIDO: Validar que el productType sea válido para Bitget
        """
        valid_types = [
            'USDT-FUTURES',
            'COIN-FUTURES',
            'USDT-OPTION',
            'COIN-OPTION',
            'USDT-FUTURES-SWAP',
            'COIN-FUTURES-SWAP'
        ]
        return product_type in valid_types

    def _generate_signature(self, timestamp, method, request_path, body=''):
        """Generar firma HMAC-SHA256 para Bitget V2 - CORREGIDO"""
        try:
            # Normalizar el body para asegurar consistencia
            body_str = self._normalize_body(body)
            
            message = timestamp + method.upper() + request_path + body_str
            
            if not self.api_secret:
                logger.error("❌ API Secret está vacía")
                return None
                
            mac = hmac.new(
                bytes(self.api_secret, 'utf-8'),
                bytes(message, 'utf-8'),
                digestmod=hashlib.sha256
            )
            
            signature = base64.b64encode(mac.digest()).decode()
            return signature
            
        except Exception as e:
            logger.error(f"❌ Error generando firma: {e}")
            return None

    def _get_headers(self, method, request_path, body=''):
        """Obtener headers con firma para Bitget V2 - CORREGIDO"""
        try:
            timestamp = str(int(time.time() * 1000))
            
            # Normalizar body ANTES de generar la firma
            body_str = self._normalize_body(body)
            
            sign = self._generate_signature(timestamp, method, request_path, body_str)
            
            if not sign:
                logger.error("❌ No se pudo generar la firma")
                return None
            
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
            return None

    def _make_request_with_retry(self, method, endpoint, params=None, body=None, max_retries=2):
        """
        ✅ CORREGIDO: Request con retry logic mejorado y regeneración correcta de HMAC
        """
        for attempt in range(max_retries + 1):
            try:
                # Construir request path con query string
                request_path = endpoint
                if params:
                    query_string = self._build_encoded_query_string(params)
                    request_path = endpoint + query_string
                
                # Log URL completa para debugging
                full_url = f"{self.base_url}{request_path}"
                logger.debug(f"🔗 Making {method} request to: {full_url}")
                
                # Validar parámetros antes del request
                if not self._validate_request_params(method, params, body):
                    logger.error(f"❌ Validación de parámetros falló en intento {attempt + 1}")
                    return None
                
                # Preparar headers
                body_str = self._normalize_body(body) if body else ''
                headers = self._get_headers(method, request_path, body_str)
                
                if not headers:
                    logger.error(f"❌ No se pudieron generar headers en intento {attempt + 1}")
                    return None
                
                # Realizar request
                if method.upper() == 'GET':
                    response = requests.get(
                        full_url,
                        headers=headers,
                        timeout=15
                    )
                elif method.upper() == 'POST':
                    response = requests.post(
                        full_url,
                        headers=headers,
                        data=body_str,
                        timeout=15
                    )
                else:
                    logger.error(f"❌ Método HTTP no soportado: {method}")
                    return None
                
                # Log de respuesta
                logger.debug(f"📥 Response {attempt + 1}: {response.status_code} - {response.text[:200]}...")
                
                if response.status_code == 200:
                    data = response.json()
                    if data.get('code') == '00000':
                        return data
                    else:
                        error_code = data.get('code', 'Unknown')
                        error_msg = data.get('msg', 'Unknown error')
                        logger.warning(f"⚠️ API Error {attempt + 1}: {error_code} - {error_msg}")
                        
                        # ✅ Retry solo para errores específicos 40009 y 40020
                        if attempt < max_retries and error_code in ['40020', '40009']:
                            logger.info(f"🔄 Retry {attempt + 1} por error {error_code}...")
                            time.sleep(1)  # Pausa antes del retry
                            continue
                        else:
                            logger.error(f"❌ Error API final: {error_code} - {error_msg}")
                            logger.error(f"❌ URL completa del error: {full_url}")
                            logger.error(f"❌ Headers enviados: {headers}")
                            return None
                else:
                    logger.error(f"❌ HTTP Error {attempt + 1}: {response.status_code}")
                    logger.error(f"❌ URL completa del error HTTP: {full_url}")
                    logger.error(f"❌ Response text: {response.text}")
                    logger.error(f"❌ Headers enviados: {headers}")
                    
                    # Retry solo para errores de servidor
                    if attempt < max_retries and response.status_code >= 500:
                        logger.info(f"🔄 Retry {attempt + 1} por error HTTP {response.status_code}...")
                        time.sleep(2)
                        continue
                    else:
                        return None
                        
            except requests.exceptions.ConnectionError as e:
                logger.error(f"❌ Error de conexión {attempt + 1}: {e}")
                if attempt < max_retries:
                    time.sleep(2)
                    continue
            except requests.exceptions.Timeout as e:
                logger.error(f"❌ Timeout {attempt + 1}: {e}")
                if attempt < max_retries:
                    time.sleep(1)
                    continue
            except Exception as e:
                logger.error(f"❌ Error inesperado {attempt + 1}: {e}")
                if attempt < max_retries:
                    time.sleep(1)
                    continue
            
            # Si llegamos aquí, es el último intento
            logger.error(f"❌ Falló request después de {attempt + 1} intentos")
            logger.error(f"❌ URL que falló: {self.base_url}{endpoint}")
            return None
        
        return None

    def verificar_credenciales(self):
        """Verificar que las credenciales sean válidas - CORREGIDO CON MÚLTIPLES PRODUCT TYPES"""
        try:
            logger.info("🔐 Verificando credenciales Bitget...")
            
            if not self.api_key or not self.api_secret or not self.passphrase:
                logger.error("❌ Credenciales incompletas")
                return False
            
            product_types = ['USDT-FUTURES'] 
            
            for product_type in product_types:
                logger.info(f"🔍 Probando productType: {product_type}")
                
                try:
                    test_response = self._make_request_with_retry(
                        'GET',
                        '/api/v2/mix/account/accounts',
                        params={'productType': product_type, 'marginCoin': 'USDT'}
                    )
                    
                    if test_response is not None:
                        logger.info(f"✅ Credenciales verificadas exitosamente con productType: {product_type}")
                        self.product_type = product_type
                        
                        accounts = self.get_account_info()
                        if accounts:
                            for account in accounts:
                                if account.get('marginCoin') == 'USDT':
                                    available = float(account.get('available', 0))
                                    logger.info(f"💰 Balance disponible: {available:.2f} USDT")
                        
                        return True
                    else:
                        logger.warning(f"⚠️ ProductType {product_type} no funcionó")
                        
                except Exception as e:
                    logger.warning(f"⚠️ Error probando productType {product_type}: {e}")
                    continue
            
            logger.error("❌ No se pudo verificar credenciales con ningún productType")
            return False
                
        except Exception as e:
            logger.error(f"❌ Error verificando credenciales: {e}")
            return False

    def get_account_info(self, product_type=None):
        """Obtener información de cuenta Bitget V2 - CORREGIDO"""
        try:
            if product_type is None:
                product_type = self.product_type
            
            params = {'productType': product_type, 'marginCoin': 'USDT'}
            
            response_data = self._make_request_with_retry(
                'GET',
                '/api/v2/mix/account/accounts',
                params=params
            )
            
            if response_data:
                return response_data.get('data', [])
            
            return None
            
        except Exception as e:
            logger.error(f"❌ Error en get_account_info: {e}")
            return None

    def get_symbol_info(self, symbol):
        """Obtener información del símbolo - CORREGIDO"""
        try:
            # ✅ CORREGIDO: Validar compatibilidad símbolo-productType ANTES del request
            if not self._validar_simbolo_compatible(symbol):
                logger.error(f"❌ Símbolo {symbol} no es compatible con {self.product_type}")
                return None
            
            params = {'productType': self.product_type}
            
            response_data = self._make_request_with_retry(
                'GET',
                '/api/v2/mix/market/contracts',
                params=params
            )
            
            if response_data:
                contracts = response_data.get('data', [])
                for contract in contracts:
                    if contract.get('symbol') == symbol:
                        logger.info(f"✅ Información de símbolo obtenida para {symbol}")
                        return contract
            
            logger.warning(f"⚠️ No se encontró información para {symbol} en {self.product_type}")
            return None
            
        except Exception as e:
            logger.error(f"❌ Error obteniendo info del símbolo: {e}")
            return None

    def place_order(self, symbol, side, order_type, size, price=None, 
                    client_order_id=None, time_in_force='normal'):
        """Colocar orden de mercado o límite - CORREGIDO"""
        try:
            logger.info(f"📤 Colocando orden: {symbol} {side} {size} {order_type}")
            
            # ✅ CORREGIDO: Validar compatibilidad símbolo-productType ANTES del request
            if not self._validar_simbolo_compatible(symbol):
                logger.error(f"❌ Símbolo {symbol} no es compatible con {self.product_type}")
                return None
            
            body = {
                'symbol': symbol,
                'productType': self.product_type,
                'marginMode': 'isolated',  # ✅ CORREGIDO: Requerido para modo one-way
                'marginCoin': 'USDT',
                'side': side,
                'orderType': order_type,
                'size': str(size)
            }
            
            # Solo agregar timeInForce para órdenes límite
            if order_type == 'limit':
                body['timeInForce'] = time_in_force
            
            if price:
                body['price'] = str(price)
            if client_order_id:
                body['clientOrderId'] = client_order_id
            
            logger.debug(f"📦 Body de orden: {body}")
            
            response_data = self._make_request_with_retry(
                'POST',
                '/api/v2/mix/order/place-order',
                body=body
            )
            
            if response_data:
                logger.info(f"✅ Orden colocada exitosamente: {response_data.get('data', {})}")
                return response_data.get('data', {})
            else:
                logger.error("❌ No se pudo colocar la orden")
                return None
                
        except Exception as e:
            logger.error(f"❌ Error colocando orden: {e}")
            return None

    def place_tpsl_order(self, symbol, side, trigger_price, order_type, size, 
                          price=None, plan_type='loss_plan', trigger_type='mark_price'):
        """
        Colocar orden TP/SL (Take Profit / Stop Loss) - CORREGIDO para API Bitget V2
        
        ✅ CORREGIDO: Usa el endpoint correcto /place-tpsl-order con holdSide
        ✅ CORREGIDO: planType 'profit_plan', 'loss_plan', 'moving_plan', 'pos_profit', 'pos_loss'
        ✅ CORREGIDO: holdSide es 'buy' para long, 'sell' para short en modo one-way
        
        Args:
            symbol: Símbolo de trading (ej. 'BTCUSDT')
            side: 'long' o 'short'
            trigger_price: Precio de activación
            order_type: 'market' o 'limit'
            size: Tamaño de la orden
            price: Precio de ejecución (para órdenes límite)
            plan_type: Tipo de plan ('profit_plan', 'loss_plan', 'moving_plan', 'pos_profit', 'pos_loss')
            trigger_type: Tipo de precio de activación ('mark_price', 'fill_price')
            
        Returns:
            dict: Respuesta de la API o None si hay error
        """
        try:
            logger.info(f"📤 Colocando orden TPSL: {symbol} {side} {plan_type} en {trigger_price}")
            logger.info(f"📦 Parámetros: plan_type={plan_type}, trigger_type={trigger_type}, order_type={order_type}")
            
            # ✅ CORREGIDO: Validar compatibilidad símbolo-productType ANTES del request
            if not self._validar_simbolo_compatible(symbol):
                logger.error(f"❌ Símbolo {symbol} no es compatible con {self.product_type}")
                return None
            
            # ✅ CORREGIDO: Convertir holdSide correctamente para TPSL
            # Para one-way mode: 'buy' = long, 'sell' = short
            if side == 'long':
                hold_side = 'buy'
            elif side == 'short':
                hold_side = 'sell'
            else:
                hold_side = side  # Mantener original si ya es buy/sell
            
            # ✅ CORREGIDO: Validar que planType sea uno de los valores válidos
            valid_plan_types = ['profit_plan', 'loss_plan', 'moving_plan', 'pos_profit', 'pos_loss']
            if plan_type not in valid_plan_types:
                logger.error(f"❌ planType '{plan_type}' no es válido. Valores válidos: {valid_plan_types}")
                return None
            
            body = {
                'symbol': symbol,
                'productType': self.product_type,
                'marginCoin': 'USDT',
                'triggerPrice': str(float(trigger_price)),
                'size': str(size),
                'planType': plan_type,  # ✅ CORREGIDO: 'profit_plan', 'loss_plan', 'moving_plan', 'pos_profit', 'pos_loss'
                'triggerType': trigger_type,
                'holdSide': hold_side,  # ✅ CORREGIDO: usar holdSide en lugar de side
                'executePrice': str(price) if price else '0'  # 0 = market price execution
            }
            
            # Para moving_plan, executePrice debe estar vacío o ser 0
            if plan_type == 'moving_plan':
                body['executePrice'] = '0'
                # moving_plan requiere rangeRate
                if 'rangeRate' not in body:
                    body['rangeRate'] = '0.01'  # 1% de trailing distance por defecto
            
            logger.debug(f"📦 Body de TPSL order: {json.dumps(body, indent=2)}")
            
            # ✅ CORREGIDO: Usar el endpoint correcto para TPSL orders
            response_data = self._make_request_with_retry(
                'POST',
                '/api/v2/mix/order/place-tpsl-order',
                body=body
            )
            
            if response_data:
                logger.info(f"✅ TPSL order ({plan_type}) colocada exitosamente para {symbol}")
                return response_data.get('data', {})
            else:
                logger.error(f"❌ No se pudo colocar la TPSL order ({plan_type}) para {symbol}")
                return None
                
        except Exception as e:
            logger.error(f"❌ Error colocando TPSL order: {e}")
            return None
    
    def place_stop_loss_order(self, symbol, side, stop_loss_price, size, 
                               trigger_type='mark_price', order_type='market'):
        """
        Colocar orden de Stop Loss específica
        
        Args:
            symbol: Símbolo de trading
            side: 'long' o 'short'
            stop_loss_price: Precio de stop loss
            size: Tamaño de la posición
            trigger_type: Tipo de precio de activación
            order_type: Tipo de orden
            
        Returns:
            dict: Respuesta de la API
        """
        return self.place_tpsl_order(
            symbol=symbol,
            side=side,
            trigger_price=stop_loss_price,
            order_type=order_type,
            size=size,
            price=None,
            plan_type='loss_plan',  # ✅ CORREGIDO: usar 'loss_plan' para stop loss
            trigger_type=trigger_type
        )
    
    def place_take_profit_order(self, symbol, side, take_profit_price, size,
                                 trigger_type='mark_price', order_type='market'):
        """
        Colocar orden de Take Profit específica
        
        Args:
            symbol: Símbolo de trading
            side: 'long' o 'short'
            take_profit_price: Precio de take profit
            size: Tamaño de la posición
            trigger_type: Tipo de precio de activación
            order_type: Tipo de orden
            
        Returns:
            dict: Respuesta de la API
        """
        return self.place_tpsl_order(
            symbol=symbol,
            side=side,
            trigger_price=take_profit_price,
            order_type=order_type,
            size=size,
            price=None,
            plan_type='profit_plan',  # ✅ CORREGIDO: usar 'profit_plan' para take profit
            trigger_type=trigger_type
        )

    def obtener_saldo_cuenta(self):
        """Obtiene el saldo actual de la cuenta"""
        try:
            accounts = self.get_account_info()
            if accounts:
                for account in accounts:
                    if account.get('marginCoin') == 'USDT':
                        balance_usdt = float(account.get('available', 0))
                        logger.info(f"💰 Saldo disponible USDT: ${balance_usdt:.2f}")
                        return balance_usdt
            logger.warning("⚠️ No se pudo obtener saldo de la cuenta")
            return None
        except Exception as e:
            logger.error(f"❌ Error obteniendo saldo de cuenta: {e}")
            return None

    def obtener_precision_precio(self, symbol):
        """Obtiene la precisión de precio para un símbolo específico"""
        try:
            symbol_info = self.get_symbol_info(symbol)
            if symbol_info:
                price_scale = symbol_info.get('priceScale', 4)
                logger.info(f"📋 {symbol}: priceScale = {price_scale}")
                return price_scale
            else:
                logger.warning(f"No se pudo obtener info de {symbol}, usando 4 decimales por defecto")
                return 4
        except Exception as e:
            logger.error(f"Error obteniendo precisión de {symbol}: {e}")
            return 4

    def redondear_precio_precision(self, price, symbol):
        """Redondea el precio a la precisión correcta para el símbolo"""
        try:
            precision = self.obtener_precision_precio(symbol)
            precio_redondeado = round(float(price), precision)
            logger.info(f"🔢 {symbol}: {price} → {precio_redondeado} (precisión: {precision} decimales)")
            return precio_redondeado
        except Exception as e:
            logger.error(f"Error redondeando precio para {symbol}: {e}")
            return float(price)

    def obtener_reglas_simbolo(self, symbol):
        """Obtiene las reglas específicas de tamaño para un símbolo - CORREGIDO"""
        try:
            # ✅ CORREGIDO: Validar que el símbolo sea compatible con el productType
            if not self._validar_simbolo_compatible(symbol):
                logger.error(f"❌ Símbolo {symbol} no es compatible con {self.product_type}")
                return None
            
            symbol_info = self.get_symbol_info(symbol)
            if not symbol_info:
                logger.warning(f"⚠️ Símbolo {symbol} no encontrado en {self.product_type}, intentando validación...")
                
                # Validación adicional: verificar que el símbolo siga el patrón correcto
                if not self._validar_simbolo_compatible(symbol):
                    logger.error(f"❌ Símbolo {symbol} no es compatible con {self.product_type}")
                    return None
                
                logger.warning(f"No se pudo obtener info de {symbol}, usando valores por defecto")
                
                # Usar configuración centralizada si está disponible
                if BITGET_CONFIG_AVAILABLE:
                    default_min_trade = get_minimum_size(symbol)
                    logger.info(f"📋 Usando configuración centralizada para {symbol}: {default_min_trade}")
                else:
                    # Fallback a valores por defecto
                    default_min_trade = 0.001
                    if 'BTC' in symbol:
                        default_min_trade = 0.001  # BTC/USDT: 0.001 BTC
                    elif 'ETH' in symbol:
                        default_min_trade = 0.01   # ETH/USDT: 0.01 ETH
                
                return {
                    'size_scale': 0,
                    'quantity_scale': 0,
                    'min_trade_num': default_min_trade,
                    'size_multiplier': 1,
                    'delivery_mode': 0
                }
            
            reglas = {
                'size_scale': int(symbol_info.get('sizeScale', 0)),
                'quantity_scale': int(symbol_info.get('quantityScale', 0)),
                'min_trade_num': float(symbol_info.get('minTradeNum', 1)),
                'size_multiplier': float(symbol_info.get('sizeMultiplier', 1)),
                'delivery_mode': symbol_info.get('deliveryMode', 0)
            }
            
            logger.info(f"📋 Reglas de {symbol}:")
            logger.info(f"  - sizeScale: {reglas['size_scale']}")
            logger.info(f"  - quantityScale: {reglas['quantity_scale']}")
            logger.info(f"  - minTradeNum: {reglas['min_trade_num']}")
            logger.info(f"  - sizeMultiplier: {reglas['size_multiplier']}")
            
            return reglas
            
        except Exception as e:
            logger.error(f"Error obteniendo reglas de {symbol}: {e}")
            # Valores por defecto actualizados según mínimos de Bitget 2025
            default_min_trade = 0.001
            if 'BTC' in symbol:
                default_min_trade = 0.001  # BTC/USDT: 0.001 BTC
            elif 'ETH' in symbol:
                default_min_trade = 0.01   # ETH/USDT: 0.01 ETH
                
            return {
                'size_scale': 0,
                'quantity_scale': 0,
                'min_trade_num': default_min_trade,
                'size_multiplier': 1,
                'delivery_mode': 0
            }
    
    def _validar_simbolo_compatible(self, symbol):
        """✅ CORREGIDO 40005: Valida que el símbolo sea compatible con el productType actual"""
        try:
            if not symbol:
                return False
                
            # ✅ CORREGIDO: Validación más flexible para símbolos
            if self.product_type == 'USDT-FUTURES':
                # Para USDT-FUTURES, el símbolo debe terminar en 'USDT' o contener 'USDT'
                if symbol.endswith('USDT'):
                    return True
                # También permitir símbolos que contengan USDT en el medio
                return 'USDT' in symbol.upper()
            elif self.product_type == 'COIN-FUTURES':
                # Para COIN-FUTURES, el símbolo no debe terminar en 'USDT'
                # pero puede contener otras monedas
                return not symbol.endswith('USDT') and len(symbol) >= 3
            else:
                # Para otros tipos, validación muy permisiva
                return len(symbol) >= 3 and symbol.replace('-', '').replace('_', '').isalnum()
        except Exception as e:
            logger.error(f"Error validando símbolo {symbol}: {e}")
            return True  # Por defecto permitir en caso de error para evitar rechazos 40020

    def ajustar_tamaño_orden(self, symbol, cantidad_contratos, reglas):
        """Ajusta el tamaño de la orden según las reglas del símbolo - CORREGIDO"""
        try:
            # ✅ CORREGIDO: Validar entrada antes de procesar
            if not reglas or cantidad_contratos <= 0:
                logger.error(f"❌ Parámetros inválidos para {symbol}: reglas={reglas}, cantidad={cantidad_contratos}")
                return 1  # Valor mínimo por defecto
            
            size_scale = reglas.get('size_scale', 0)
            quantity_scale = reglas.get('quantity_scale', 0)
            min_trade_num = reglas.get('min_trade_num', 0.001)
            size_multiplier = reglas.get('size_multiplier', 1)
            
            # Determinar la escala a usar (prioridad: quantityScale > sizeScale)
            escala_actual = quantity_scale if quantity_scale > 0 else size_scale
            
            # Ajustar según la escala
            if escala_actual == 0:
                # Requiere entero
                cantidad_contratos = round(cantidad_contratos)
                logger.info(f"🔢 {symbol}: ajustado a entero = {cantidad_contratos}")
            elif escala_actual == 1:
                # 1 decimal permitido
                cantidad_contratos = round(cantidad_contratos, 1)
                logger.info(f"🔢 {symbol}: ajustado a 1 decimal = {cantidad_contratos}")
            elif escala_actual == 2:
                # 2 decimales permitidos
                cantidad_contratos = round(cantidad_contratos, 2)
                logger.info(f"🔢 {symbol}: ajustado a 2 decimales = {cantidad_contratos}")
            else:
                # Otros casos
                cantidad_contratos = round(cantidad_contratos, escala_actual)
                logger.info(f"🔢 {symbol}: ajustado a {escala_actual} decimales = {cantidad_contratos}")
            
            # Aplicar multiplicador si existe
            if size_multiplier > 1:
                cantidad_contratos = round(cantidad_contratos / size_multiplier) * size_multiplier
                logger.info(f"🔢 {symbol}: aplicado multiplicador {size_multiplier}x = {cantidad_contratos}")
            
            # Verificar mínimo
            if cantidad_contratos < min_trade_num:
                cantidad_contratos = min_trade_num
                logger.info(f"⚠️ {symbol}: ajustado a mínimo = {min_trade_num}")
            
            # Validación final
            if escala_actual == 0:
                if min_trade_num < 1 and min_trade_num > 0:
                    cantidad_contratos = max(1, int(round(cantidad_contratos)))
                    logger.info(f"🔢 {symbol}: caso especial - min decimal pero requiere entero = {cantidad_contratos}")
                else:
                    cantidad_contratos = int(round(cantidad_contratos))
                logger.info(f"✅ {symbol} final: {cantidad_contratos} (entero)")
            else:
                cantidad_contratos = round(cantidad_contratos, escala_actual)
                logger.info(f"✅ {symbol} final: {cantidad_contratos} ({escala_actual} decimales)")
            
            return cantidad_contratos
            
        except Exception as e:
            logger.error(f"❌ Error ajustando tamaño para {symbol}: {e}")
            # ✅ CORREGIDO: Fallback más robusto
            try:
                return max(1, int(round(cantidad_contratos)))
            except:
                return 1  # Valor mínimo absoluto

    def set_leverage(self, symbol, leverage, hold_side='long'):
        """Configurar apalancamiento - CORREGIDO"""
        try:
            logger.info(f"⚙️ Configurando apalancamiento {leverage}x para {symbol} ({hold_side})")
            
            # ✅ CORREGIDO: Validar compatibilidad símbolo-productType ANTES del request
            if not self._validar_simbolo_compatible(symbol):
                logger.error(f"❌ Símbolo {symbol} no es compatible con {self.product_type}")
                return False
            
            body = {
                'symbol': symbol,
                'productType': self.product_type,
                'marginCoin': 'USDT',
                'leverage': str(leverage),
                'holdSide': 'long'  # ✅ CORREGIDO: siempre usar 'long' para modo one-way
            }
            
            logger.debug(f"📦 Body leverage: {body}")
            
            response_data = self._make_request_with_retry(
                'POST',
                '/api/v2/mix/account/set-leverage',
                body=body
            )
            
            if response_data:
                logger.info(f"✅ Apalancamiento {leverage}x configurado para {symbol}")
                return True
            else:
                # Manejo de errores específicos
                logger.warning(f"⚠️ No se pudo configurar leverage {leverage}x")
                return False
                
        except Exception as e:
            logger.error(f"❌ Error en set_leverage: {e}")
            return False

    def get_positions(self, symbol=None):
        """Obtener posiciones abiertas - CORREGIDO"""
        try:
            params = {'productType': self.product_type, 'marginCoin': 'USDT'}
            if symbol:
                # ✅ CORREGIDO: Validar compatibilidad símbolo-productType ANTES del request
                if not self._validar_simbolo_compatible(symbol):
                    logger.error(f"❌ Símbolo {symbol} no es compatible con {self.product_type}")
                    return []
                params['symbol'] = symbol
            
            response_data = self._make_request_with_retry(
                'GET',
                '/api/v2/mix/position/all-position',
                params=params
            )
            
            if response_data:
                return response_data.get('data', [])
            
            return []
            
        except Exception as e:
            logger.error(f"❌ Error obteniendo posiciones: {e}")
            return []

    def get_klines(self, symbol, interval='5m', limit=200):
        """✅ CORREGIDO 40004: Obtener velas (datos de mercado) con interval mapping corregido"""
        try:
            # ✅ CORREGIDO: Validar compatibilidad símbolo-productType ANTES del request
            if not self._validar_simbolo_compatible(symbol):
                logger.error(f"❌ Símbolo {symbol} no es compatible con {self.product_type}")
                return None
            
            # ✅ CORREGIDO 40004: Interval mapping CORREGIDO para Bitget API
            # La API de Bitget usa intervalos en minúsculas
            interval_map = {
                '1m': '1m', '3m': '3m', '5m': '5m',
                '15m': '15m', '30m': '30m', '1h': '1m',  # ✅ CORREGIDO: 1h debe ser 1m en Bitget
                '4h': '4m', '1d': '1d'  # ✅ CORREGIDO: 4h debe ser 4m, 1d se mantiene
            }
            
            # Fallback si el intervalo no está en el mapeo
            bitget_interval = interval_map.get(interval, '5m')
            
            # ✅ CORREGIDO: Solo incluir parámetros requeridos para evitar errores 40020
            params = {
                'symbol': symbol,
                'productType': self.product_type,
                'granularity': bitget_interval,
                'limit': limit
            }
            
            logger.debug(f"📊 Obteniendo klines para {symbol} {interval} -> {bitget_interval}")
            
            # ✅ CORREGIDO: Usar el método unificado con retry logic mejorado
            response_data = self._make_request_with_retry(
                'GET',
                '/api/v2/mix/market/candles',
                params=params
            )
            
            if response_data:
                candles = response_data.get('data', [])
                if candles:
                    logger.debug(f"✅ Klines obtenidas: {len(candles)} velas para {symbol}")
                return candles
            else:
                logger.error(f"❌ No se pudieron obtener klines para {symbol}")
                return None
                
        except Exception as e:
            logger.error(f"❌ Error en get_klines: {e}")
            return None

# ---------------------------
# FUNCIONES DE OPERACIONES BITGET CORREGIDAS
# ---------------------------
def ejecutar_operacion_bitget(bitget_client, simbolo, tipo_operacion, capital_usd, leverage=10):
    """
    Ejecutar una operación completa en Bitget (posición + TP/SL) - CORREGIDO
    """
    
    logger.info("🚀 EJECUTANDO OPERACIÓN REAL EN BITGET")
    logger.info(f"📈 Símbolo: {simbolo}")
    logger.info(f"🎯 Tipo: {tipo_operacion}")
    logger.info(f"⚡ Apalancamiento: {leverage}x")
    logger.info(f"💰 Capital: ${capital_usd}")
    
    try:
        hold_side = 'long' if tipo_operacion == 'LONG' else 'short'
        leverage = min(leverage, 10)
        
        leverage_ok = bitget_client.set_leverage(simbolo, leverage, hold_side)
        if not leverage_ok:
            logger.error("❌ Error configurando apalancamiento")
            logger.info("🔄 Intentando con apalancamiento 5x...")
            leverage_ok = bitget_client.set_leverage(simbolo, 5, hold_side)
            if not leverage_ok:
                logger.error("❌ No se pudo configurar apalancamiento después de múltiples intentos")
                return None
            leverage = 5
        
        time.sleep(1)
        
        klines = bitget_client.get_klines(simbolo, '1m', 1)
        if not klines or len(klines) == 0:
            logger.error(f"❌ No se pudo obtener precio de {simbolo}")
            try:
                url = "https://api.binance.com/api/v3/ticker/price"
                params = {'symbol': simbolo}
                response = requests.get(url, params=params, timeout=5)
                if response.status_code == 200:
                    data = response.json()
                    precio_actual = float(data['price'])
                    logger.info(f"💰 Precio actual (de Binance): {precio_actual:.8f}")
                else:
                    logger.error("❌ No se pudo obtener precio de ninguna fuente")
                    return None
            except Exception as e:
                logger.error(f"❌ Error obteniendo precio de Binance: {e}")
                return None
        else:
            klines.reverse()
            precio_actual = float(klines[0][4])
            logger.info(f"💰 Precio actual: {precio_actual:.8f}")
        
        symbol_info = bitget_client.get_symbol_info(simbolo)
        if not symbol_info:
            logger.warning(f"⚠️ No se pudo obtener info de {simbolo} de Bitget, usando valores por defecto")
            size_multiplier = 1
            
            # Usar configuración centralizada si está disponible
            if BITGET_CONFIG_AVAILABLE:
                min_trade_num = get_minimum_size(simbolo)
                price_place = get_price_precision(simbolo)
                logger.info(f"📋 Usando configuración centralizada para {simbolo}: min={min_trade_num}, prec={price_place}")
            else:
                # Fallback a valores por defecto
                min_trade_num = 0.001  # Por defecto para la mayoría de símbolos
                if 'BTC' in simbolo:
                    min_trade_num = 0.001  # BTC/USDT: 0.001 BTC
                elif 'ETH' in simbolo:
                    min_trade_num = 0.01   # ETH/USDT: 0.01 ETH
                price_place = 8
        else:
            size_multiplier = float(symbol_info.get('sizeMultiplier', 1))
            min_trade_num = float(symbol_info.get('minTradeNum', 0.001))
            price_place = int(symbol_info.get('pricePlace', 8))
        
        cantidad_usd = capital_usd * leverage
        cantidad_contratos = cantidad_usd / precio_actual
        cantidad_contratos = round(cantidad_contratos / size_multiplier) * size_multiplier
        
        # Validación final: asegurar que cumple con los mínimos de Bitget
        if cantidad_contratos < min_trade_num:
            logger.warning(f"⚠️ Cantidad calculada ({cantidad_contratos}) menor al mínimo ({min_trade_num}) - ajustando")
            cantidad_contratos = min_trade_num
        
        # Ajustar según las reglas del símbolo
        reglas = bitget_client.obtener_reglas_simbolo(simbolo)
        if reglas:
            cantidad_contratos = bitget_client.ajustar_tamaño_orden(simbolo, cantidad_contratos, reglas)
        
        logger.info(f"💵 Valor nocional: ${cantidad_contratos * precio_actual:.2f}")
        
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
        
        stop_loss = round(stop_loss, price_place)
        take_profit = round(take_profit, price_place)
        
        logger.info(f"🛑 Stop Loss: {stop_loss:.8f}")
        logger.info(f"🎯 Take Profit: {take_profit:.8f}")
        
        side = 'buy' if tipo_operacion == 'LONG' else 'sell'
        orden_entrada = bitget_client.place_order(
            symbol=simbolo,
            side=side,
            order_type='market',
            size=cantidad_contratos
        )
        
        if not orden_entrada:
            logger.error("❌ Error abriendo posición")
            return None
        
        logger.info(f"✅ Posición abierta exitosamente")
        time.sleep(1)
        
        # ✅ CORREGIDO: Usar place_stop_loss_order y place_take_profit_order
        # en lugar de place_plan_order con plan_type
        sl_side = 'sell' if tipo_operacion == 'LONG' else 'buy'
        logger.info(f"🎯 Colocando SL para {tipo_operacion} en {stop_loss:.8f}")
        orden_sl = bitget_client.place_stop_loss_order(
            symbol=simbolo,
            side=tipo_operacion.lower(),  # ✅ CORREGIDO: usar 'long' o 'short', no 'buy' o 'sell'
            stop_loss_price=stop_loss,
            size=cantidad_contratos,
            trigger_type='mark_price',
            order_type='market'
        )
        
        if orden_sl:
            logger.info(f"✅ Stop Loss configurado en: {stop_loss:.8f}")
        else:
            logger.warning(f"⚠️ No se pudo configurar Stop Loss")
        
        time.sleep(1)
        
        tp_side = 'sell' if tipo_operacion == 'LONG' else 'buy'
        logger.info(f"🎯 Colocando TP para {tipo_operacion} en {take_profit:.8f}")
        orden_tp = bitget_client.place_take_profit_order(
            symbol=simbolo,
            side=tipo_operacion.lower(),  # ✅ CORREGIDO: usar 'long' o 'short', no 'buy' o 'sell'
            take_profit_price=take_profit,
            size=cantidad_contratos,
            trigger_type='mark_price',
            order_type='market'
        )
        
        if orden_tp:
            logger.info(f"✅ Take Profit configurado en: {take_profit:.8f}")
        else:
            logger.warning(f"⚠️ No se pudo configurar Take Profit")
        
        return {
            'orden_entrada': orden_entrada,
            'orden_sl': orden_sl,
            'orden_tp': orden_tp,
            'precio_entrada': precio_actual,
            'stop_loss': stop_loss,
            'take_profit': take_profit,
            'cantidad_contratos': cantidad_contratos,
            'leverage': leverage
        }
        
    except Exception as e:
        logger.error(f"❌ Error en ejecución de operación: {e}")
        return None

# ---------------------------
# CLASE PRINCIPAL DEL BOT - TRADING BREAKOUT + REENTRY
# ---------------------------

class TradingBot:
    def __init__(self, config):
        self.config = config
        self.operaciones_activas = {}
        self.senales_enviadas = set()
        self.total_operaciones = 0
        self.config_optima_por_simbolo = {}
        self.ultima_optimizacion = datetime.now()
        self.operaciones_desde_optimizacion = 0
        self.log_path = config.get('log_path', 'operaciones_log.csv')
        self.archivo_estado = config.get('estado_file', 'estado_bot.json')
        self.archivo_log = self.log_path  # Definir archivo_log antes de usarlo
        self.inicializar_log()
        self.cargar_estado()
        
        # Inicializar cliente Bitget si hay credenciales
        api_key = config.get('bitget_api_key')
        api_secret = config.get('bitget_api_secret')
        passphrase = config.get('bitget_passphrase')
        
        if api_key and api_secret and passphrase:
            self.bitget_client = BitgetClient(api_key, api_secret, passphrase)
            if self.bitget_client.verificar_credenciales():
                logger.info("✅ Bitget API inicializada correctamente")
                # Ajustar configuración de operaciones según credenciales
                if config.get('ejecutar_operaciones_automaticas'):
                    logger.info("🤖 Modo auto-trading ACTIVADO")
                else:
                    logger.info("🤖 Modo solo señales (auto-trading DESACTIVADO)")
            else:
                logger.error("❌ Error inicializando Bitget API")
                self.bitget_client = None
        else:
            logger.warning("⚠️ No hay credenciales de Bitget configuradas")
            self.bitget_client = None
        
        self.capital_por_operacion = config.get('capital_por_operacion', 4)
        self.leverage_por_defecto = min(config.get('leverage_por_defecto', 10), 10)
        self.ejecutar_operaciones_automaticas = config.get('ejecutar_operaciones_automaticas', False)
        
        self.esperando_reentry = {}
        self.breakouts_detectados = {}
        self.breakout_history = {}
        
        self.analisis_counter = 0
        
        # Configuración por símbolo
        for simbolo in config.get('symbols', []):
            self.config_optima_por_simbolo[simbolo] = {
                'timeframe': '5m',
                'num_velas': 80
            }
        
        logger.info(f"🤖 Bot inicializado con {len(self.config_optima_por_simbolo)} símbolos")

    def obtener_datos_mercado_config(self, simbolo, timeframe, num_velas):
        """Obtener datos de mercado con configuración específica"""
        try:
            # Intentar primero con Bitget
            if self.bitget_client:
                klines = self.bitget_client.get_klines(simbolo, timeframe, num_velas + 50)
                if klines and len(klines) >= num_velas:
                    cierres = [float(k[4]) for k in klines]
                    maximos = [float(k[2]) for k in klines]
                    minimos = [float(k[3]) for k in klines]
                    return {
                        'cierres': cierres[-num_velas:],
                        'maximos': maximos[-num_velas:],
                        'minimos': minimos[-num_velas:],
                        'precio_actual': float(klines[-1][4]),
                        'fuente': 'bitget'
                    }
            
            # Fallback a Binance
            url = "https://api.binance.com/api/v3/klines"
            params = {
                'symbol': simbolo,
                'interval': timeframe,
                'limit': num_velas + 50
            }
            respuesta = requests.get(url, params=params, timeout=10)
            klines = respuesta.json()
            
            if not klines or len(klines) == 0:
                logger.warning(f"⚠️ No se pudieron obtener datos de {simbolo}")
                return None
            
            cierres = [float(k[4]) for k in klines]
            maximos = [float(k[2]) for k in klines]
            minimos = [float(k[3]) for k in klines]
            return {
                'cierres': cierres[-num_velas:],
                'maximos': maximos[-num_velas:],
                'minimos': minimos[-num_velas:],
                'precio_actual': float(klines[-1][4]),
                'fuente': 'binance'
            }
            
        except Exception as e:
            logger.error(f"❌ Error obteniendo datos de mercado para {simbolo}: {e}")
            return None

    def calcular_canal_regresion_config(self, datos_mercado, num_velas):
        """Calcular canal de regresión lineal"""
        try:
            cierres = datos_mercado['cierres']
            maximos = datos_mercado['maximos']
            minimos = datos_mercado['minimos']
            
            if len(cierres) < num_velas:
                logger.warning(f"⚠️ Datos insuficientes: {len(cierres)} < {num_velas}")
                return None
            
            cierres = cierres[-num_velas:]
            maximos = maximos[-num_velas:]
            minimos = minimos[-num_velas:]
            
            tiempos = list(range(len(cierres)))
            
            # Calcular regresión para cierres
            pendiente_cierre, intercepto_cierre = self.calcular_regresion_lineal(tiempos, cierres)
            
            # Calcular desviación estándar para establecer canales
            cierres_predichos = [pendiente_cierre * t + intercepto_cierre for t in tiempos]
            residuos = [c - cp for c, cp in zip(cierres, cierres_predichos)]
            desviacion = np.std(residuos)
            
            # Calcular pendientes de resistencias y soportes
            # Usar máx/min para determinar tendencias de canales
            maximos_line = maximos
            minimos_line = minimos
            
            pendiente_max, intercepto_max = self.calcular_regresion_lineal(tiempos, maximos_line)
            pendiente_min, intercepto_min = self.calcular_regresion_lineal(tiempos, minimos_line)
            
            # Calcular resistencia y soporte en el punto actual (última vela)
            resistencia = pendiente_max * (len(cierres) - 1) + intercepto_max
            soporte = pendiente_min * (len(cierres) - 1) + intercepto_min
            
            precio_actual = cierres[-1]
            
            # Calcular indicadores adicionales
            stoch_k, stoch_d = self.calcular_stochastic(datos_mercado)
            
            pearson, angulo = self.calcular_pearson_y_angulo(tiempos, cierres)
            r2 = self.calcular_r2(cierres, tiempos, pendiente_cierre, intercepto_cierre)
            
            # Determinar dirección y fuerza
            direccion = self.determinar_direccion_tendencia(angulo)
            fuerza_texto, nivel_fuerza = self.clasificar_fuerza_tendencia(angulo)
            
            # Calcular ancho del canal
            ancho_canal = abs(resistencia - soporte)
            ancho_canal_porcentual = (ancho_canal / precio_actual) * 100
            
            # Calcular pendiente de resistencia y soporte
            pendiente_resistencia = pendiente_max
            pendiente_soporte = pendiente_min
            
            return {
                'resistencia': resistencia,
                'soporte': soporte,
                'pendiente_resistencia': pendiente_resistencia,
                'pendiente_soporte': pendiente_soporte,
                'ancho_canal': ancho_canal,
                'ancho_canal_porcentual': ancho_canal_porcentual,
                'coeficiente_pearson': pearson,
                'angulo_tendencia': angulo,
                'r2_score': r2,
                'direccion': direccion,
                'fuerza_texto': fuerza_texto,
                'nivel_fuerza': nivel_fuerza,
                'stoch_k': stoch_k,
                'stoch_d': stoch_d
            }
            
        except Exception as e:
            logger.error(f"❌ Error calculando canal de regresión: {e}")
            return None

    def guardar_estado(self):
        """Guardar estado del bot en archivo JSON"""
        try:
            estado = {
                'config_optima_por_simbolo': self.config_optima_por_simbolo,
                'ultima_optimizacion': self.ultima_optimizacion.isoformat(),
                'operaciones_desde_optimizacion': self.operaciones_desde_optimizacion,
                'total_operaciones': self.total_operaciones,
                'timestamp': datetime.now().isoformat()
            }
            
            with open(self.archivo_estado, 'w', encoding='utf-8') as f:
                json.dump(estado, f, indent=2)
            
            logger.info(f"💾 Estado guardado en {self.archivo_estado}")
        except Exception as e:
            logger.error(f"❌ Error guardando estado: {e}")

    def cargar_estado(self):
        """Cargar estado previo del bot"""
        try:
            if os.path.exists(self.archivo_estado):
                with open(self.archivo_estado, 'r', encoding='utf-8') as f:
                    estado = json.load(f)
                
                if 'config_optima_por_simbolo' in estado:
                    self.config_optima_por_simbolo = estado['config_optima_por_simbolo']
                    logger.info("✅ Configuración óptima cargada")
                
                if 'ultima_optimizacion' in estado:
                    self.ultima_optimizacion = datetime.fromisoformat(estado['ultima_optimizacion'])
                
                if 'operaciones_desde_optimizacion' in estado:
                    self.operaciones_desde_optimizacion = estado['operaciones_desde_optimizacion']
                
                if 'total_operaciones' in estado:
                    self.total_operaciones = estado['total_operaciones']
        except Exception as e:
            logger.warning(f"⚠️ No se pudo cargar estado anterior: {e}")

    def detectar_breakout(self, symbol, info_canal, datos_mercado):
        """Detectar breakout del canal"""
        try:
            precio_actual = datos_mercado['precio_actual']
            resistencia = info_canal['resistencia']
            soporte = info_canal['soporte']
            
            # Breakout alcista
            if precio_actual > resistencia * 1.001:  # 0.1% de tolerancia
                # Verificar que Stochastic no esté en sobrecompra extrema
                if info_canal['stoch_k'] < 90:
                    return "ALCISTA"
            
            # Breakout bajista
            elif precio_actual < soporte * 0.999:  # 0.1% de tolerancia
                # Verificar que Stochastic no esté en sobreventa extrema
                if info_canal['stoch_k'] > 10:
                    return "BAJISTA"
            
            return None
            
        except Exception as e:
            logger.error(f"❌ Error detectando breakout: {e}")
            return None

    def detectar_reentry(self, symbol, info_canal, datos_mercado):
        """Detectar reentry al canal después de breakout"""
        try:
            precio_actual = datos_mercado['precio_actual']
            resistencia = info_canal['resistencia']
            soporte = info_canal['soporte']
            
            # Verificar que estamos dentro del canal
            if precio_actual <= resistencia * 1.005 and precio_actual >= soporte * 0.995:
                # Confirmar con Stochastic
                if info_canal['stoch_k'] <= 30 and info_canal['stoch_d'] <= 30:
                    return "LONG"
                elif info_canal['stoch_k'] >= 70 and info_canal['stoch_d'] >= 70:
                    return "SHORT"
            
            return None
            
        except Exception as e:
            logger.error(f"❌ Error detectando reentry: {e}")
            return None

    def calcular_niveles_entrada(self, tipo_operacion, info_canal, precio_actual):
        """Calcular niveles de entrada, TP y SL"""
        try:
            entry_margin = self.config.get('entry_margin', 0.001)
            
            if tipo_operacion == "LONG":
                precio_entrada = precio_actual * (1 + entry_margin)
                sl_porcentaje = 0.02
                tp_porcentaje = 0.04
                stop_loss = precio_entrada * (1 - sl_porcentaje)
                take_profit = precio_entrada * (1 + tp_porcentaje)
            else:  # SHORT
                precio_entrada = precio_actual * (1 - entry_margin)
                sl_porcentaje = 0.02
                tp_porcentaje = 0.04
                stop_loss = precio_entrada * (1 + sl_porcentaje)
                take_profit = precio_entrada * (1 - tp_porcentaje)
            
            # Verificar ratio riesgo/beneficio
            riesgo = abs(precio_entrada - stop_loss)
            beneficio = abs(take_profit - precio_entrada)
            ratio_rr = beneficio / riesgo if riesgo > 0 else 0
            
            min_rr_ratio = self.config.get('min_rr_ratio', 1.2)
            if ratio_rr < min_rr_ratio:
                logger.debug(f"⚠️ Ratio R/R {ratio_rr:.2f} menor al mínimo {min_rr_ratio}")
                return None, None, None
            
            return precio_entrada, take_profit, stop_loss
            
        except Exception as e:
            logger.error(f"❌ Error calculando niveles de entrada: {e}")
            return None, None, None

    def escanear_mercado(self):
        """Escanear mercado en busca de oportunidades"""
        senales_encontradas = 0
        
        symbols = self.config.get('symbols', [])
        timeframes = self.config.get('timeframes', ['5m', '15m', '30m', '1h'])
        velas_options = self.config.get('velas_options', [80, 100, 120, 150, 200])
        min_ancho = self.config.get('min_channel_width_percent', 4)
        
        logger.info(f"\n🔍 ESCANEANDO MERCADO - {len(symbols)} símbolos")
        
        for simbolo in symbols:
            try:
                # Obtener configuración óptima para este símbolo
                config_optima = self.config_optima_por_simbolo.get(simbolo)
                if not config_optima:
                    continue
                
                datos_mercado = self.obtener_datos_mercado_config(
                    simbolo, 
                    config_optima['timeframe'], 
                    config_optima['num_velas']
                )
                
                if not datos_mercado:
                    logger.warning(f"   ❌ {simbolo} - Error obteniendo datos")
                    continue
                
                info_canal = self.calcular_canal_regresion_config(datos_mercado, config_optima['num_velas'])
                if not info_canal:
                    logger.warning(f"   ❌ {simbolo} - Error calculando canal")
                    continue
                
                # Verificar ancho mínimo del canal
                if info_canal['ancho_canal_porcentual'] < min_ancho:
                    logger.debug(f"   🔍 {simbolo} - Canal muy estrecho ({info_canal['ancho_canal_porcentual']:.1f}%)")
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
                    posicion = "📍 DENTRO"
                
                logger.info(
                    f"📊 {simbolo} - {config_optima['timeframe']} - {config_optima['num_velas']}v | "
                    f"{info_canal['direccion']} ({info_canal['angulo_tendencia']:.1f}° - {info_canal['fuerza_texto']}) | "
                    f"Ancho: {info_canal['ancho_canal_porcentual']:.1f}% - Stoch: {info_canal['stoch_k']:.1f}/{info_canal['stoch_d']:.1f} {estado_stoch} | "
                    f"Precio: {posicion}"
                )
                
                # Filtros de calidad
                if (info_canal['nivel_fuerza'] < 2 or 
                    abs(info_canal['coeficiente_pearson']) < 0.4 or 
                    info_canal['r2_score'] < 0.4):
                    continue
                
                # Detectar breakout si no estamos esperando reentry
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
                        continue
                
                # Detectar reentry
                tipo_operacion = self.detectar_reentry(simbolo, info_canal, datos_mercado)
                if not tipo_operacion:
                    continue
                
                precio_entrada, tp, sl = self.calcular_niveles_entrada(
                    tipo_operacion, info_canal, datos_mercado['precio_actual']
                )
                
                if not precio_entrada or not tp or not sl:
                    continue
                
                # Verificar cooldown de señales
                if simbolo in self.breakout_history:
                    ultimo_breakout = self.breakout_history[simbolo]
                    tiempo_desde_ultimo = (datetime.now() - ultimo_breakout).total_seconds() / 3600
                    if tiempo_desde_ultimo < 2:
                        logger.info(f"   ⏳ {simbolo} - Señal reciente, omitiendo...")
                        continue
                
                # Generar señal de operación
                breakout_info = self.esperando_reentry.get(simbolo)
                self.generar_senal_operacion(
                    simbolo, tipo_operacion, precio_entrada, tp, sl, 
                    info_canal, datos_mercado, config_optima, breakout_info
                )
                
                senales_encontradas += 1
                self.breakout_history[simbolo] = datetime.now()
                if simbolo in self.esperando_reentry:
                    del self.esperando_reentry[simbolo]
                
            except Exception as e:
                logger.error(f"⚠️ Error analizando {simbolo}: {e}")
                continue
        
        # Mostrar estado de breakouts y reentries
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
            return
        
        if precio_entrada is None or tp is None or sl is None:
            logger.warning(f"    ❌ Niveles inválidos para {simbolo}, omitiendo señal")
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
                logger.error(f"     ❌ Error enviando señal: {e}")
        
        # Ejecutar operación automática si está habilitada
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
                    logger.info(f"     ✅ Operación ejecutada en Bitget para {simbolo}")
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
                    logger.error(f"     ❌ Error ejecutando operación en Bitget para {simbolo}")
            except Exception as e:
                logger.error(f"     ⚠️ Error en ejecución automática: {e}")
        
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
            logger.error(f"❌ Error registrando operación en log: {e}")

    def verificar_cierre_operaciones(self):
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
                        logger.error(f"Error enviando mensaje de cierre: {e}")
                
                self.registrar_operacion(datos_operacion)
                operaciones_cerradas.append(simbolo)
                del self.operaciones_activas[simbolo]
                
                if simbolo in self.senales_enviadas:
                    self.senales_enviadas.remove(simbolo)
                
                self.operaciones_desde_optimizacion += 1
                logger.info(f"     📊 {simbolo} Operación {resultado} - PnL: {pnl_percent:.2f}%")
        
        return operaciones_cerradas

    def generar_mensaje_cierre(self, datos_operacion):
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
            return None, None
        
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
            
            # Configuración específica para este gráfico
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
            
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
            
            axes[2].set_ylim([0, 100])
            axes[2].grid(True, alpha=0.3)
            
            buf = BytesIO()
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                plt.savefig(buf, format='png', dpi=100, bbox_inches='tight', facecolor='#1a1a1a')
            buf.seek(0)
            plt.close(fig)
            
            return buf
        except Exception as e:
            logger.error(f"⚠️ Error generando gráfico: {e}")
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
                else:
                    logger.error(f"     ❌ Error enviando gráfico a {chat_id}: {r.status_code}")
            except Exception as e:
                logger.error(f"     ❌ Error enviando gráfico: {e}")
        
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
                if r.status_code != 200:
                    logger.error(f"     ❌ Error enviando mensaje a {chat_id}: {r.status_code}")
            except Exception as e:
                logger.error(f"     ❌ Error enviando mensaje: {e}")
                resultados.append(False)
        
        return any(resultados)

    def ejecutar_analisis(self):
        # Reoptimización periódica (10% de probabilidad)
        if random.random() < 0.1:
            self.reoptimizar_periodicamente()
        
        # Verificar cierre de operaciones
        cierres = self.verificar_cierre_operaciones()
        if cierres:
            logger.info(f"     📊 Operaciones cerradas: {', '.join(cierres)}")
        
        # Guardar estado
        self.guardar_estado()
        
        # Escanear mercado
        return self.escanear_mercado()

    def reoptimizar_periodicamente(self):
        """Reoptimizar parámetros basándose en el historial de operaciones"""
        try:
            horas_desde_opt = (datetime.now() - self.ultima_optimizacion).total_seconds() / 3600
            
            # Reoptimizar cada 24 horas o después de 8 operaciones
            if self.operaciones_desde_optimizacion >= 8 or horas_desde_opt >= 24:
                logger.info("🔄 Iniciando re-optimización automática...")
                
                # Crear optimizador con datos recientes
                ia = OptimizadorIA(log_path=self.log_path, min_samples=30)
                nuevos_parametros = ia.buscar_mejores_parametros()
                
                if nuevos_parametros:
                    self.actualizar_parametros(nuevos_parametros)
                    self.ultima_optimizacion = datetime.now()
                    self.operaciones_desde_optimizacion = 0
                    logger.info("✅ Parámetros actualizados en tiempo real")
                else:
                    logger.warning("⚠️ No se pudieron optimizar parámetros")
        except Exception as e:
            logger.error(f"⚠️ Error en re-optimización: {e}")

    def actualizar_parametros(self, nuevos_parametros):
        """Actualizar parámetros del bot con nuevos valores optimizados"""
        try:
            old_params = {
                'trend_threshold_degrees': self.config.get('trend_threshold_degrees'),
                'min_trend_strength_degrees': self.config.get('min_trend_strength_degrees'),
                'entry_margin': self.config.get('entry_margin')
            }
            
            self.config['trend_threshold_degrees'] = nuevos_parametros.get('trend_threshold_degrees', 
                                                                        self.config.get('trend_threshold_degrees', 16))
            self.config['min_trend_strength_degrees'] = nuevos_parametros.get('min_trend_strength_degrees', 
                                                                           self.config.get('min_trend_strength_degrees', 16))
            self.config['entry_margin'] = nuevos_parametros.get('entry_margin', 
                                                             self.config.get('entry_margin', 0.001))
            
            logger.info("📊 Parámetros actualizados:")
            logger.info(f"   • Trend threshold: {old_params['trend_threshold_degrees']}° → {self.config['trend_threshold_degrees']}°")
            logger.info(f"   • Min strength: {old_params['min_trend_strength_degrees']}° → {self.config['min_trend_strength_degrees']}°")
            logger.info(f"   • Entry margin: {old_params['entry_margin']} → {self.config['entry_margin']}")
            
        except Exception as e:
            logger.error(f"⚠️ Error actualizando parámetros: {e}")

    def mostrar_resumen_operaciones(self):
        """Mostrar resumen del estado actual de operaciones"""
        logger.info(f"\n📊 RESUMEN OPERACIONES:")
        logger.info(f"   Activas: {len(self.operaciones_activas)}")
        logger.info(f"   Esperando reentry: {len(self.esperando_reentry)}")
        logger.info(f"   Total ejecutadas: {self.total_operaciones}")
        
        if self.bitget_client:
            logger.info(f"   🤖 Bitget: ✅ Conectado")
        else:
            logger.info(f"   🤖 Bitget: ❌ No configurado")
        
        if self.operaciones_activas:
            for simbolo, op in self.operaciones_activas.items():
                estado = "🟢 LONG" if op['tipo'] == 'LONG' else "🔴 SHORT"
                ancho_canal = op.get('ancho_canal_porcentual', 0)
                timeframe = op.get('timeframe_utilizado', 'N/A')
                velas = op.get('velas_utilizadas', 0)
                breakout = "🚀" if op.get('breakout_usado', False) else ""
                ejecutada = "🤖" if op.get('operacion_ejecutada', False) else ""
                logger.info(f"   • {simbolo} {estado} {breakout} {ejecutada} - {timeframe} - {velas}v - Ancho: {ancho_canal:.1f}%")

    def iniciar(self):
        """Iniciar el bot de trading"""
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
        logger.info("\n🚀 INICIANDO BOT...")
        
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
            logger.error(f"\n❌ Error en el bot: {e}")
            logger.info("💾 Intentando guardar estado...")
            try:
                self.guardar_estado()
            except Exception as e2:
                logger.error(f"❌ Error guardando estado final: {e2}")

# ---------------------------
# CONFIGURACIÓN
# ---------------------------

def crear_config_desde_entorno():
    """Configuración desde variables de entorno"""
    directorio_actual = os.path.dirname(os.path.abspath(__file__))
    telegram_chat_ids_str = os.environ.get('TELEGRAM_CHAT_ID', '1570204748')
    telegram_chat_ids = [cid.strip() for cid in telegram_chat_ids_str.split(',') if cid.strip()]
    
    return {
        'min_channel_width_percent': 4.0,
        'trend_threshold_degrees': 16.0,
        'min_trend_strength_degrees': 16.0,
        'entry_margin': 0.001,
        'min_rr_ratio': 1.2,
        'scan_interval_minutes': 6,
        'timeframes': ['5m', '15m', '30m', '1h'],
        'velas_options': [80, 100, 120, 150, 200],
        'symbols': [
            'XMRUSDT','AAVEUSDT','DOTUSDT','LINKUSDT',
            'BNBUSDT','XRPUSDT','SOLUSDT','AVAXUSDT',
            'DOGEUSDT','LTCUSDT','ATOMUSDT','XLMUSDT',
            'ALGOUSDT','VETUSDT','ICPUSDT','FILUSDT',
            'BCHUSDT','NEOUSDT','TRXUSDT','XTZUSDT',
            'SUSHIUSDT','COMPUSDT','PEPEUSDT','ETCUSDT',
            'SNXUSDT','RENDERUSDT','1INCHUSDT','UNIUSDT',
            'ZILUSDT','HOTUSDT','ENJUSDT','HYPEUSDT',
            'BEATUSDT','PIPPINUSDT','ADAUSDT','ASTERUSDT',
            'ENAUSDT','TAOUSDT','LUNCUSDT','WLDUSDT',
            'WIFUSDT','APTUSDT','HBARUSDT','CRVUSDT',
            'LUNAUSDT','TIAUSDT','ARBUSDT','ONDOUSDT',
            'FOLKSUSDT','BRETTUSDT','TRUMPUSDT',
            'INJUSDT','ZECUSDT','NOTUSDT','SHIBUSDT',
            'LDOUSDT','KASUSDT','STRKUSDT','DYDXUSDT',
            'SEIUSDT','TONUSDT','NMRUSDT',
        ],
        'telegram_token': os.environ.get('TELEGRAM_TOKEN'),
        'telegram_chat_ids': telegram_chat_ids,
        'auto_optimize': True,
        'min_samples_optimizacion': 30,
        'reevaluacion_horas': 24,
        'log_path': os.path.join(directorio_actual, 'operaciones_log_v23.csv'),
        'estado_file': os.path.join(directorio_actual, 'estado_bot_v23.json'),
        'bitget_api_key': os.environ.get('BITGET_API_KEY'),
        'bitget_api_secret': os.environ.get('BITGET_SECRET_KEY'),
        'bitget_passphrase': os.environ.get('BITGET_PASSPHRASE'),
        'webhook_url': os.environ.get('WEBHOOK_URL'),
        'ejecutar_operaciones_automaticas': os.environ.get('EJECUTAR_OPERACIONES_AUTOMATICAS', 'false').lower() == 'true',
        'capital_por_operacion': float(os.environ.get('CAPITAL_POR_OPERACION', '4')),
        'leverage_por_defecto': min(int(os.environ.get('LEVERAGE_POR_DEFECTO', '10')), 10)
    }

# ---------------------------
# FLASK APP
# ---------------------------

app = Flask(__name__)

# Crear bot con configuración desde entorno
config = crear_config_desde_entorno()
bot = TradingBot(config)

@app.route('/')
def index():
    return "✅ Bot Breakout + Reentry con integración Bitget está en línea.", 200

@app.route('/webhook', methods=['POST'])
def telegram_webhook():
    if request.is_json:
        update = request.get_json()
        logger.info(f"📩 Update recibido: {json.dumps(update)}")
        return jsonify({"status": "ok"}), 200
    return jsonify({"error": "Request must be JSON"}), 400

@app.route('/health', methods=['GET'])
def health_check():
    """Endpoint para verificar el estado del bot"""
    try:
        status = {
            "status": "running",
            "timestamp": datetime.now().isoformat(),
            "operaciones_activas": len(bot.operaciones_activas),
            "esperando_reentry": len(bot.esperando_reentry),
            "total_operaciones": bot.total_operaciones,
            "bitget_conectado": bot.bitget_client is not None,
            "auto_trading": bot.ejecutar_operaciones_automaticas
        }
        return jsonify(status), 200
    except Exception as e:
        logger.error(f"Error en health check: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

# Configuración automática del webhook
def setup_telegram_webhook():
    token = os.environ.get('TELEGRAM_TOKEN')
    if not token:
        logger.warning("⚠️ No hay token de Telegram configurado")
        return
    
    webhook_url = os.environ.get('WEBHOOK_URL')
    if not webhook_url:
        render_url = os.environ.get('RENDER_EXTERNAL_URL')
        if render_url:
            webhook_url = f"{render_url}/webhook"
        else:
            logger.warning("⚠️ No hay URL de webhook configurada")
            return
    
    try:
        logger.info(f"🔗 Configurando webhook Telegram en: {webhook_url}")
        # Eliminar webhook anterior
        requests.get(f"https://api.telegram.org/bot{token}/deleteWebhook", timeout=10)
        time.sleep(1)
        # Configurar nuevo webhook
        response = requests.get(f"https://api.telegram.org/bot{token}/setWebhook?url={webhook_url}", timeout=10)
        
        if response.status_code == 200:
            logger.info("✅ Webhook de Telegram configurado correctamente")
        else:
            logger.error(f"❌ Error configurando webhook: {response.status_code} - {response.text}")
    except Exception as e:
        logger.error(f"❌ Error configurando webhook: {e}")

if __name__ == '__main__':
    logger.info("🚀 Iniciando aplicación Flask...")
    setup_telegram_webhook()
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port)
    
import test_real_order
test_real_order.run_test()    
    
