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
# BITGET CLIENT - INTEGRACIÓN COMPLETA CON API BITGET V2
# ---------------------------
class BitgetClient:
    def __init__(self, api_key, api_secret, passphrase):
        self.api_key = api_key
        self.api_secret = api_secret
        self.passphrase = passphrase
        self.base_url = "https://api.bitget.com"
        self.position_mode = "hedge_mode"  # Por defecto según configuración de las imágenes
        logger.info(f"Cliente Bitget V2 inicializado con API Key: {api_key[:10]}...")
        logger.info(f"Modo de posición configurado: {self.position_mode}")

    def _generate_signature(self, timestamp, method, request_path, body=''):
        """Generar firma HMAC-SHA256 para Bitget V2"""
        try:
            # Para Bitget V2, la firma debe construirse de manera específica
            if isinstance(body, dict) and body:
                body_str = json.dumps(body, separators=(',', ':'))
            elif isinstance(body, str):
                body_str = body
            else:
                body_str = str(body) if body else ''
            
            # Construir mensaje según especificación Bitget V2
            # timestamp + method + request_path + body_str
            message = timestamp + method.upper() + request_path + body_str
            
            # Generar HMAC-SHA256
            mac = hmac.new(
                bytes(self.api_secret, 'utf-8'),
                bytes(message, 'utf-8'),
                hashlib.sha256
            )
            
            # Convertir a base64
            signature = base64.b64encode(mac.digest()).decode('utf-8')
            return signature
            
        except Exception as e:
            logger.error(f"Error generando firma para {method} {request_path}: {e}")
            logger.error(f"Body: {body}")
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

    def set_position_mode(self, pos_mode="hedge_mode", product_type="USDT-FUTURES"):
        """Configurar modo de posición (hedge_mode o one_way_mode)"""
        try:
            # IMPORTANTE: El endpoint set-position-mode puede tener restricciones
            # Vamos a intentar configurarlo solo si es necesario
            request_path = '/api/v2/mix/account/set-position-mode'
            body = {
                'productType': product_type,
                'posMode': pos_mode
            }
            
            # Para evitar problemas de firma, vamos a usar one_way_mode por defecto
            # y solo intentar cambiar si es específicamente requerido
            logger.info(f"Intentando configurar modo de posición: {pos_mode}")
            
            # Usar el método genérico de headers
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
                    self.position_mode = pos_mode
                    logger.info(f"✓ Modo de posición configurado exitosamente: {pos_mode}")
                    return True
                else:
                    error_code = data.get('code')
                    error_msg = data.get('msg', 'Unknown error')
                    
                    # Algunos códigos de error son aceptables
                    if error_code in ['40755', '40756', '40009']:
                        # Ya está configurado o error de firma (podemos continuar)
                        logger.info(f"⚠️ Modo posición {pos_mode} ya configurado o sin permisos para cambiar")
                        self.position_mode = pos_mode
                        return True
                    else:
                        logger.warning(f"⚠️ Error configurando modo posición: {error_msg} (Code: {error_code})")
                        # Continuar con one_way_mode como fallback
                        self.position_mode = "one_way_mode"
                        logger.info(f"🔄 Usando modo de posición por defecto: one_way_mode")
                        return True
            else:
                logger.warning(f"⚠️ HTTP Error configurando modo posición: {response.status_code}")
                # Fallback a one_way_mode
                self.position_mode = "one_way_mode"
                logger.info(f"🔄 Fallback a modo de posición: one_way_mode")
                return True
                
        except Exception as e:
            logger.warning(f"⚠️ Excepción configurando modo posición: {e}")
            # En caso de cualquier error, usar one_way_mode como fallback seguro
            self.position_mode = "one_way_mode"
            logger.info(f"🔄 Fallback seguro a modo de posición: one_way_mode")
            return True

    def verificar_credenciales(self):
        """Verificar que las credenciales sean válidas"""
        try:
            logger.info("Verificando credenciales Bitget...")
            
            if not self.api_key or not self.api_secret or not self.passphrase:
                logger.error("Credenciales incompletas")
                return False
            
            # Configurar modo de posición hedge_mode según configuración de las imágenes
            if not self.set_position_mode("hedge_mode"):
                logger.warning("No se pudo configurar hedge_mode, continuando...")
            
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
                    client_order_id=None, time_in_force='normal', margin_mode='isolated',
                    trade_side=None, stp_mode='none'):
        """Colocar orden de mercado o límite con margen aislado y soporte para hedge mode"""
        try:
            request_path = '/api/v2/mix/order/place-order'
            body = {
                'symbol': symbol,
                'productType': 'USDT-FUTURES',
                'marginCoin': 'USDT',
                'marginMode': margin_mode,  # isolated o crossed
                'side': side,  # buy o sell
                'orderType': order_type,  # limit o market
                'size': str(size),
                'stpMode': stp_mode  # Configuración STP según imagen
            }
            
            # Configurar tradeSide según hedge mode
            if self.position_mode == 'hedge_mode' and trade_side:
                body['tradeSide'] = trade_side  # open o close
            
            if price:
                body['price'] = str(price)
            if client_order_id:
                body['clientOid'] = client_order_id
            
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
                         price=None, plan_type='normal_plan', margin_mode='isolated',
                         trade_side=None, stp_mode='none'):
        """Colocar orden de plan (TP/SL) con margen aislado y soporte para hedge mode"""
        try:
            request_path = '/api/v2/mix/order/place-plan-order'
            body = {
                'symbol': symbol,
                'productType': 'USDT-FUTURES',
                'marginCoin': 'USDT',
                'marginMode': margin_mode,
                'side': side,
                'orderType': order_type,
                'triggerPrice': str(trigger_price),
                'size': str(size),
                'planType': plan_type,
                'triggerType': 'market_price',
                'stpMode': stp_mode
            }
            
            # Configurar tradeSide según hedge mode
            if self.position_mode == 'hedge_mode' and trade_side:
                body['tradeSide'] = trade_side
                
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

    def set_margin_mode(self, symbol, margin_mode='isolated'):
        """Configurar modo de margen (isolated o crossed)"""
        try:
            request_path = '/api/v2/mix/account/set-margin-mode'
            body = {
                'symbol': symbol,
                'productType': 'USDT-FUTURES',
                'marginCoin': 'USDT',
                'marginMode': margin_mode
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
                    logger.info(f"✓ Modo margen {margin_mode} configurado para {symbol}")
                    return True
                # Si ya está en ese modo, también es éxito
                if data.get('code') == '40756':
                    logger.info(f"✓ Margen {margin_mode} ya estaba configurado para {symbol}")
                    return True
            logger.warning(f"Error configurando margin mode: {response.text}")
            return True  # Continuar aunque falle
        except Exception as e:
            logger.error(f"Error en set_margin_mode: {e}")
            return True  # Continuar aunque falle

    def set_leverage(self, symbol, leverage, hold_side='long', margin_type='isolated'):
        """Configurar apalancamiento con margen aislado"""
        try:
            # Primero configurar el modo de margen
            self.set_margin_mode(symbol, margin_type)
            time.sleep(0.3)
            
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
                # Si el leverage ya está configurado, también es éxito
                if data.get('code') == '40761':
                    logger.info(f"✓ Apalancamiento {leverage}x ya estaba configurado para {symbol}")
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
    Ejecutar una operación completa en Bitget (posición + TP/SL) con margen aislado y hedge mode
    
    Args:
        bitget_client: Instancia de BitgetClient
        simbolo: Símbolo de trading (ej: 'BTCUSDT')
        tipo_operacion: 'LONG' o 'SHORT'
        capital_usd: Capital a usar en USD
        leverage: Apalancamiento (default: 20)
    
    Returns:
        dict con información de la operación ejecutada o None si falla
    """
    
    logger.info(f"[EXEC] EJECUTANDO OPERACIÓN REAL EN BITGET (HEDGE MODE + MARGEN AISLADO)")
    logger.info(f"Símbolo: {simbolo}")
    logger.info(f"Tipo: {tipo_operacion}")
    logger.info(f"Apalancamiento: {leverage}x")
    logger.info(f"Capital: ${capital_usd}")
    logger.info(f"Modo Posición: {bitget_client.position_mode}")
    logger.info(f"Margen: AISLADO")
    
    try:
        # 1. Configurar apalancamiento con margen aislado
        hold_side = 'long' if tipo_operacion == 'LONG' else 'short'
        leverage_ok = bitget_client.set_leverage(simbolo, leverage, hold_side, margin_type='isolated')
        if not leverage_ok:
            logger.error("Error configurando apalancamiento con margen aislado")
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
        
        # 6. Abrir posición con margen aislado y hedge mode
        if bitget_client.position_mode == 'hedge_mode':
            # En hedge mode necesitamos tradeSide
            side = 'buy' if tipo_operacion == 'LONG' else 'sell'
            trade_side = 'open'
        else:
            # En one-way mode
            side = 'buy' if tipo_operacion == 'LONG' else 'sell'
            trade_side = None
        
        orden_entrada = bitget_client.place_order(
            symbol=simbolo,
            side=side,
            order_type='market',
            size=cantidad_contratos,
            margin_mode='isolated',
            trade_side=trade_side,
            stp_mode='cancel_taker'  # Configuración STP según imagen
        )
        
        if not orden_entrada:
            logger.error("Error abriendo posición")
            return None
        
        logger.info(f"✓ Posición abierta: {orden_entrada}")
        time.sleep(1)
        
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
            logger.error(f"Respuesta de orden: {orden_entrada}")
            return None
        
        logger.info(f"✅ Posición confirmada en Bitget: {posicion_encontrada.get('positionId', 'N/A')}")
        logger.info(f"Tamaño real: {posicion_encontrada.get('positionSize', 'N/A')}")
        logger.info(f"Precio de entrada: {posicion_encontrada.get('avgPrice', 'N/A')}")
        
        # 8. Colocar Stop Loss con margen aislado y hedge mode
        if bitget_client.position_mode == 'hedge_mode':
            sl_side = 'buy' if tipo_operacion == 'LONG' else 'sell'
            sl_trade_side = 'close'
        else:
            sl_side = 'sell' if tipo_operacion == 'LONG' else 'buy'
            sl_trade_side = None
        
        orden_sl = bitget_client.place_plan_order(
            symbol=simbolo,
            side=sl_side,
            trigger_price=stop_loss,
            order_type='market',
            size=cantidad_contratos,
            plan_type='loss_plan',
            margin_mode='isolated',
            trade_side=sl_trade_side,
            stp_mode='cancel_taker'
        )
        
        if orden_sl:
            logger.info(f"✓ Stop Loss configurado en: {stop_loss:.8f}")
        else:
            logger.warning("Error configurando Stop Loss")
        
        time.sleep(0.5)
        
        # 9. Colocar Take Profit con margen aislado y hedge mode
        orden_tp = bitget_client.place_plan_order(
            symbol=simbolo,
            side=sl_side,
            trigger_price=take_profit,
            order_type='market',
            size=cantidad_contratos,
            plan_type='normal_plan',
            margin_mode='isolated',
            trade_side=sl_trade_side,
            stp_mode='cancel_taker'
        )
        
        if orden_tp:
            logger.info(f"✓ Take Profit configurado en: {take_profit:.8f}")
        else:
            logger.warning("Error configurando Take Profit")
        
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
            'posicion_real': posicion_encontrada,  # Información real de la posición
            'margen_tipo': 'isolated',
            'posicion_mode': bitget_client.position_mode
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
                logger.info(f"🤖 Modo de posición: {self.bitget_client.position_mode}")
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
        
        # NUEVO: Limpiar operaciones obsoletas al inicializar
        if self.bitget_client:
            logger.info("🧹 Limpiando operaciones obsoletas al inicializar...")
            self.sincronizar_estado_con_bitget()

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

    def sincronizar_estado_con_bitget(self):
        """Sincroniza el estado interno con las posiciones reales en Bitget"""
        if not self.bitget_client:
            return
        
        try:
            logger.info("🔄 Sincronizando estado interno con posiciones reales de Bitget...")
            
            # Obtener todas las posiciones reales de Bitget
            posiciones_reales = self.bitget_client.get_positions()
            simbolos_reales = {pos.get('symbol') for pos in posiciones_reales if pos.get('positionSize', 0) != 0}
            
            # Limpiar operaciones que ya no existen en Bitget
            simbolos_a_eliminar = []
            for simbolo in list(self.operaciones_activas.keys()):
                if simbolo not in simbolos_reales:
                    logger.warning(f"🗑️ Limpiando operación inexistente: {simbolo}")
                    simbolos_a_eliminar.append(simbolo)
            
            for simbolo in simbolos_a_eliminar:
                del self.operaciones_activas[simbolo]
                if simbolo in self.senales_enviadas:
                    self.senales_enviadas.remove(simbolo)
            
            # Agregar posiciones que están en Bitget pero no en nuestro estado
            for pos in posiciones_reales:
                simbolo = pos.get('symbol')
                if simbolo and pos.get('positionSize', 0) != 0 and simbolo not in self.operaciones_activas:
                    logger.info(f"➕ Agregando posición real: {simbolo}")
                    self.operaciones_activas[simbolo] = {
                        'tipo': 'LONG' if float(pos.get('positionSize', 0)) > 0 else 'SHORT',
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
                        'posicion_real': pos
                    }
            
            logger.info(f"✅ Sincronización completada. Operaciones activas: {len(self.operaciones_activas)}")
            
        except Exception as e:
            logger.error(f"❌ Error sincronizando estado con Bitget: {e}")

    def verificar_operaciones_reales_bitget(self):
        """Verifica y actualiza el estado basado en posiciones reales de Bitget"""
        if not self.bitget_client:
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
                    
                    if not posicion_actual or float(posicion_actual.get('positionSize', 0)) == 0:
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
                                    except Exception:
                                        pass
                                
                                # Registrar en log
                                self.registrar_operacion(datos_operacion)
                                operaciones_cerradas.append(simbolo)
                                
                                # Limpiar del estado
                                del self.operaciones_activas[simbolo]
                                if simbolo in self.senales_enviadas:
                                    self.senales_enviadas.remove(simbolo)
                                
                                self.operaciones_desde_optimizacion += 1
                                print(f"     📊 {simbolo} Operación {resultado} - PnL: {pnl_percent:.2f}%")
                
                except Exception as e:
                    logger.error(f"❌ Error verificando operación {simbolo}: {e}")
                    continue
            
        except Exception as e:
            logger.error(f"❌ Error verificando operaciones reales: {e}")
        
        return operaciones_cerradas

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
                            abs(canal_info['coeficiente_pearson']) >= 0.3 and 
                            canal_info['r2_score'] >= 0.3):
                            ancho_actual = canal_info['ancho_canal_porcentual']
                            if ancho_actual >= self.config.get('min_channel_width_percent', 3.0):
                                puntaje_ancho = ancho_actual * 5
                                puntaje_timeframe = prioridad_timeframe.get(timeframe, 50) * 50
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
            print(f"   ✅ {simbolo}: {mejor_config['timeframe']} - {mejor_config['num_velas']}v - Puntaje: {mejor_config['puntaje_total']:.0f}")
        else:
            print(f"   ❌ {simbolo}: No se encontró configuración válida")
        return mejor_config

    def obtener_datos_mercado_config(self, simbolo, timeframe, num_velas):
        """Obtener datos de mercado con configuración específica"""
        try:
            if self.bitget_client:
                klines = self.bitget_client.get_klines(simbolo, timeframe, num_velas)
                if klines and len(klines) > 0:
                    cierres = []
                    maximos = []
                    minimos = []
                    volumenes = []
                    tiempos = []
                    
                    for kline in reversed(klines):
                        try:
                            timestamp = int(kline[0])
                            open_price = float(kline[1])
                            high_price = float(kline[2])
                            low_price = float(kline[3])
                            close_price = float(kline[4])
                            volume = float(kline[5])
                            
                            cierres.append(close_price)
                            maximos.append(high_price)
                            minimos.append(low_price)
                            volumenes.append(volume)
                            tiempos.append(timestamp)
                        except (ValueError, IndexError):
                            continue
                    
                    if len(cierres) >= num_velas:
                        return {
                            'cierres': cierres,
                            'maximos': maximos,
                            'minimos': minimos,
                            'volumenes': volumenes,
                            'tiempos': tiempos,
                            'precio_actual': cierres[-1],
                            'source': 'bitget'
                        }
                else:
                    # Fallback a Binance si Bitget falla
                    return self._obtener_datos_binance(simbolo, timeframe, num_velas)
            else:
                # Usar Binance si no hay cliente Bitget
                return self._obtener_datos_binance(simbolo, timeframe, num_velas)
                
        except Exception as e:
            logger.error(f"Error obteniendo datos de {simbolo}: {e}")
            return None

    def _obtener_datos_binance(self, simbolo, timeframe, num_velas):
        """Fallback a Binance si Bitget falla"""
        try:
            url = "https://api.binance.com/api/v3/klines"
            params = {
                'symbol': simbolo,
                'interval': timeframe,
                'limit': num_velas
            }
            respuesta = requests.get(url, params=params, timeout=10)
            klines = respuesta.json()
            
            cierres = []
            maximos = []
            minimos = []
            volumenes = []
            tiempos = []
            
            for kline in klines:
                try:
                    timestamp = int(kline[0])
                    open_price = float(kline[1])
                    high_price = float(kline[2])
                    low_price = float(kline[3])
                    close_price = float(kline[4])
                    volume = float(kline[5])
                    
                    cierres.append(close_price)
                    maximos.append(high_price)
                    minimos.append(low_price)
                    volumenes.append(volume)
                    tiempos.append(timestamp)
                except (ValueError, IndexError):
                    continue
            
            return {
                'cierres': cierres,
                'maximos': maximos,
                'minimos': minimos,
                'volumenes': volumenes,
                'tiempos': tiempos,
                'precio_actual': cierres[-1],
                'source': 'binance'
            }
            
        except Exception as e:
            logger.error(f"Error obteniendo datos de Binance para {simbolo}: {e}")
            return None

    def calcular_canal_regresion_config(self, datos_mercado, num_velas):
        """Calcular canal de regresión con configuración específica"""
        try:
            cierres = datos_mercado['cierres']
            if len(cierres) < num_velas:
                return None
            
            cierres_recientes = cierres[-num_velas:]
            tiempos_reg = list(range(len(cierres_recientes)))
            
            # Calcular regresión lineal
            regresion = self.calcular_regresion_lineal(tiempos_reg, cierres_recientes)
            if not regresion:
                return None
            
            pendiente, intercepto = regresion
            
            # Calcular Pearson y ángulo
            pearson, angulo_grados = self.calcular_pearson_y_angulo(tiempos_reg, cierres_recientes)
            
            # Calcular R²
            r2_score = self.calcular_r2(cierres_recientes, tiempos_reg, pendiente, intercepto)
            
            # Calcular resistencia y soporte
            maximos_recientes = datos_mercado['maximos'][-num_velas:]
            minimos_recientes = datos_mercado['minimos'][-num_velas:]
            
            # Calcular líneas de resistencia y soporte usando regresión en máximos y mínimos
            regresion_maximos = self.calcular_regresion_lineal(tiempos_reg, maximos_recientes)
            regresion_minimos = self.calcular_regresion_lineal(tiempos_reg, minimos_recientes)
            
            if not regresion_maximos or not regresion_minimos:
                return None
            
            pendiente_max, intercepto_max = regresion_maximos
            pendiente_min, intercepto_min = regresion_minimos
            
            # Proyectar líneas al final del período
            resistencia = pendiente_max * (len(tiempos_reg) - 1) + intercepto_max
            soporte = pendiente_min * (len(tiempos_reg) - 1) + intercepto_min
            
            # Calcular ancho del canal
            ancho_canal = abs(resistencia - soporte)
            precio_actual = cierres_recientes[-1]
            ancho_canal_porcentual = (ancho_canal / precio_actual) * 100
            
            # Calcular stochastic
            stoch_k, stoch_d = self.calcular_stochastic(datos_mercado)
            
            # Clasificar fuerza de tendencia
            fuerza_texto, nivel_fuerza = self.clasificar_fuerza_tendencia(angulo_grados)
            
            # Determinar dirección de tendencia
            direccion = self.determinar_direccion_tendencia(angulo_grados)
            
            return {
                'pendiente': pendiente,
                'intercepto': intercepto,
                'resistencia': resistencia,
                'soporte': soporte,
                'ancho_canal': ancho_canal,
                'ancho_canal_porcentual': ancho_canal_porcentual,
                'coeficiente_pearson': pearson,
                'angulo_tendencia': angulo_grados,
                'r2_score': r2_score,
                'stoch_k': stoch_k,
                'stoch_d': stoch_d,
                'fuerza_texto': fuerza_texto,
                'nivel_fuerza': nivel_fuerza,
                'direccion': direccion,
                'pendiente_resistencia': pendiente_max,
                'pendiente_soporte': pendiente_min
            }
            
        except Exception as e:
            logger.error(f"Error calculando canal de regresión: {e}")
            return None

    def detectar_breakout(self, simbolo, info_canal, datos_mercado):
        """Detectar breakout con lógica corregida"""
        precio_actual = datos_mercado['precio_actual']
        resistencia = info_canal['resistencia']
        soporte = info_canal['soporte']
        
        # Tolerancia mínima para evitar falsas señales
        tolerancia = self.config.get('entry_margin', 0.001)
        
        # BREAKOUT LONG: Ruptura del soporte (precio cae por debajo del soporte)
        if precio_actual < soporte * (1 - tolerancia):
            # Verificar que no haya breakout reciente
            if simbolo not in self.breakout_history:
                return "BREAKOUT_LONG"
        
        # BREAKOUT SHORT: Ruptura de la resistencia (precio sube por encima de la resistencia)
        if precio_actual > resistencia * (1 + tolerancia):
            # Verificar que no haya breakout reciente
            if simbolo not in self.breakout_history:
                return "BREAKOUT_SHORT"
        
        return None

    def detectar_reentry(self, simbolo, info_canal, datos_mercado):
        """Detectar reentry con lógica corregida"""
        if simbolo not in self.esperando_reentry:
            return None
        
        precio_actual = datos_mercado['precio_actual']
        resistencia = info_canal['resistencia']
        soporte = info_canal['soporte']
        
        # Obtener información del breakout
        breakout_info = self.esperando_reentry[simbolo]
        tipo_breakout = breakout_info['tipo']
        
        # Verificar que haya pasado suficiente tiempo desde el breakout
        tiempo_desde_breakout = (datetime.now() - breakout_info['timestamp']).total_seconds() / 60
        if tiempo_desde_breakout < 2:  # Mínimo 2 minutos
            return None
        
        # Verificar que el precio haya regresado al canal
        # BREAKOUT_LONG (ruptura soporte) → Reentry = señal SHORT
        if tipo_breakout == "BREAKOUT_LONG":
            if precio_actual >= soporte and precio_actual <= resistencia:
                # Confirmar con stochastic
                if info_canal['stoch_k'] >= 50:  # No oversold
                    return "SHORT"
        
        # BREAKOUT_SHORT (ruptura resistencia) → Reentry = señal LONG
        elif tipo_breakout == "BREAKOUT_SHORT":
            if precio_actual >= soporte and precio_actual <= resistencia:
                # Confirmar con stochastic
                if info_canal['stoch_k'] <= 50:  # No overbought
                    return "LONG"
        
        return None

    def enviar_alerta_breakout(self, simbolo, tipo_breakout, info_canal, datos_mercado, config_optima):
        """Enviar alerta de breakout detectado"""
        try:
            precio_actual = datos_mercado['precio_actual']
            mensaje = f"""
🎯 <b>BREAKOUT DETECTADO - {simbolo}</b>

📊 <b>Tipo:</b> {tipo_breakout}
💰 <b>Precio:</b> {precio_actual:.8f}
📈 <b>Configuración:</b> {config_optima['timeframe']} - {config_optima['num_velas']}v
📏 <b>Ancho Canal:</b> {info_canal['ancho_canal_porcentual']:.1f}%
🎯 <b>Tendencia:</b> {info_canal['direccion']} ({info_canal['angulo_tendencia']:.1f}°)
⏰ <b>Hora:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

🔄 <b>Estrategia:</b> Esperando reentry para ejecutar operación
            """
            
            token = self.config.get('telegram_token')
            chats = self.config.get('telegram_chat_ids', [])
            if token and chats:
                self._enviar_telegram_simple(mensaje, token, chats)
                print(f"     📢 Alerta de breakout enviada para {simbolo}")
        except Exception as e:
            logger.error(f"Error enviando alerta de breakout: {e}")

    def calcular_niveles_entrada(self, tipo_operacion, info_canal, precio_actual):
        """Calcular niveles de entrada, TP y SL con ratio R/R mínimo"""
        try:
            if tipo_operacion == "LONG":
                # Para LONG: entrada cerca del soporte, TP en resistencia, SL por debajo del soporte
                precio_entrada = info_canal['soporte'] * (1 + self.config.get('entry_margin', 0.001))
                take_profit = info_canal['resistencia']
                stop_loss = precio_entrada * (1 - 0.02)  # 2% de riesgo
            else:
                # Para SHORT: entrada cerca de la resistencia, TP en soporte, SL por encima de la resistencia
                precio_entrada = info_canal['resistencia'] * (1 - self.config.get('entry_margin', 0.001))
                take_profit = info_canal['soporte']
                stop_loss = precio_entrada * (1 + 0.02)  # 2% de riesgo
            
            # Verificar ratio R/R mínimo
            riesgo = abs(precio_entrada - stop_loss)
            beneficio = abs(take_profit - precio_entrada)
            ratio_rr = beneficio / riesgo if riesgo > 0 else 0
            
            # Ajustar TP si el ratio es muy bajo
            if ratio_rr < self.config.get('min_rr_ratio', 1.2):
                if tipo_operacion == "LONG":
                    take_profit = precio_entrada + (riesgo * self.config['min_rr_ratio'])
                else:
                    take_profit = precio_entrada - (riesgo * self.config['min_rr_ratio'])
            return precio_entrada, take_profit, stop_loss
        except Exception as e:
            logger.error(f"Error calculando niveles de entrada: {e}")
            return None, None, None

    def escanear_mercado(self):
        """
        Escanea el mercado con estrategia Breakout + Reentry
        
        LÓGICA CORREGIDA:
        - BREAKOUT_LONG (ruptura soporte): Reentry = señal SHORT
        - BREAKOUT_SHORT (ruptura resistencia): Reentry = señal LONG
        """
        print(f"\n🔍 Escaneando {len(self.config.get('symbols', []))} símbolos (Estrategia: Breakout + Reentry)...")
        senales_encontradas = 0
        for simbolo in self.config.get('symbols', []):
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
                breakout_info = self.esperando_reentry[simbolo]
                self.generar_senal_operacion(
                    simbolo, tipo_operacion, precio_entrada, tp, sl, 
                    info_canal, datos_mercado, config_optima, breakout_info
                )
                senales_encontradas += 1
                self.breakout_history[simbolo] = datetime.now()
                del self.esperando_reentry[simbolo]
            except Exception as e:
                print(f"⚠️ Error analizando {simbolo}: {e}")
                continue
        if self.esperando_reentry:
            print(f"\n⏳ Esperando reingreso en {len(self.esperando_reentry)} símbolos:")
            for simbolo, info in self.esperando_reentry.items():
                tiempo_espera = (datetime.now() - info['timestamp']).total_seconds() / 60
                print(f"   • {simbolo} - {info['tipo']} - Esperando {tiempo_espera:.1f} min")
        # NUEVO: Mostrar breakouts detectados recientemente
        if self.breakouts_detectados:
            print(f"\n⏰ Breakouts detectados recientemente:")
            for simbolo, info in self.breakouts_detectados.items():
                tiempo_desde_deteccion = (datetime.now() - info['timestamp']).total_seconds() / 60
                print(f"   • {simbolo} - {info['tipo']} - Hace {tiempo_desde_deteccion:.1f} min")
        if senales_encontradas > 0:
            print(f"✅ Se encontraron {senales_encontradas} señales de trading")
        else:
            print("❌ No se encontraron señales en este ciclo")
        return senales_encontradas

    def generar_senal_operacion(self, simbolo, tipo_operacion, precio_entrada, tp, sl,
                            info_canal, datos_mercado, config_optima, breakout_info=None):
        """Genera y envía señal de operación con info de breakout"""
        if simbolo in self.senales_enviadas:
            return
        if precio_entrada is None or tp is None or sl is None:
            print(f"    ❌ Niveles inválidos para {simbolo}, omitiendo señal")
            return
        riesgo = abs(precio_entrada - sl)
        beneficio = abs(tp - precio_entrada)
        ratio_rr = beneficio / riesgo if riesgo > 0 else 0
        # Calcular SL y TP en porcentaje
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
                print(f"     📊 Generando gráfico para {simbolo}...")
                buf = self.generar_grafico_profesional(simbolo, info_canal, datos_mercado, 
                                                      precio_entrada, tp, sl, tipo_operacion)
                if buf:
                    print(f"     📨 Enviando gráfico por Telegram...")
                    self.enviar_grafico_telegram(buf, token, chat_ids)
                    time.sleep(1)
                self._enviar_telegram_simple(mensaje, token, chat_ids)
                print(f"     ✅ Señal {tipo_operacion} para {simbolo} enviada")
            except Exception as e:
                print(f"     ❌ Error enviando señal: {e}")
        
        # NUEVO: Ejecutar operación automáticamente si está habilitado
        operacion_ejecutada_exitosa = False
        if self.ejecutar_operaciones_automaticas and self.bitget_client:
            print(f"     🤖 Ejecutando operación automática en Bitget...")
            try:
                operacion_bitget = ejecutar_operacion_bitget(
                    bitget_client=self.bitget_client,
                    simbolo=simbolo,
                    tipo_operacion=tipo_operacion,
                    capital_usd=self.capital_por_operacion,
                    leverage=self.leverage_por_defecto
                )
                if operacion_bitget:
                    print(f"     ✅ Operación ejecutada y verificada en Bitget para {simbolo}")
                    operacion_ejecutada_exitosa = True
                    
                    # Enviar confirmación de ejecución
                    mensaje_confirmacion = f"""
🤖 <b>OPERACIÓN AUTOMÁTICA EJECUTADA - {simbolo}</b>
✅ <b>Status:</b> EJECUTADA Y VERIFICADA EN BITGET
📊 <b>Tipo:</b> {tipo_operacion}
💰 <b>Capital:</b> ${self.capital_por_operacion}
⚡ <b>Apalancamiento:</b> {self.leverage_por_defecto}x
🎯 <b>Margen:</b> AISLADO
🎯 <b>Posición Mode:</b> {self.bitget_client.position_mode}
🎯 <b>Entrada:</b> {operacion_bitget['precio_entrada']:.8f}
🛑 <b>Stop Loss:</b> {operacion_bitget['stop_loss']:.8f}
🎯 <b>Take Profit:</b> {operacion_bitget['take_profit']:.8f}
📋 <b>ID Orden:</b> {operacion_bitget['orden_entrada'].get('orderId', 'N/A')}
⏰ <b>Timestamp:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
                    """
                    self._enviar_telegram_simple(mensaje_confirmacion, token, chat_ids)
                else:
                    print(f"     ❌ Error ejecutando operación en Bitget para {simbolo}")
            except Exception as e:
                print(f"     ⚠️ Error en ejecución automática: {e}")
        
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
                'ancho_canal_relativo': info_canal['ancho_canal'] / precio_entrada,
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
                datos_operacion.get('operacion_ejecutada', False)
            ])

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
                    except Exception:
                        continue
            return ops_recientes
        except Exception as e:
            print(f"⚠️ Error filtrando operaciones: {e}")
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
            return None
        total_ops = len(ops_ultima_semana)
        wins = sum(1 for op in ops_ultima_semana if op['resultado'] == 'TP')
        losses = sum(1 for op in ops_ultima_semana if op['resultado'] == 'SL')
        winrate = (wins/total_ops*100) if total_ops > 0 else 0
        pnl_total = sum(op['pnl_percent'] for op in ops_ultima_semana)
        mejor_op = max(ops_ultima_semana, key=lambda x: x['pnl_percent'])
        peor_op = min(ops_ultima_semana, key=lambda x: x['pnl_percent'])
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
💎 Integración: Bitget API V2
💻 Acceso Premium: @TuUsuario
    """
        return mensaje

    def enviar_reporte_semanal(self):
        """Envía el reporte semanal por Telegram"""
        mensaje = self.generar_reporte_semanal()
        if not mensaje:
            print("ℹ️ No hay datos suficientes para generar reporte")
            return False
        token = self.config.get('telegram_token')
        chat_ids = self.config.get('telegram_chat_ids', [])
        if token and chat_ids:
            try:
                self._enviar_telegram_simple(mensaje, token, chat_ids)
                print("✅ Reporte semanal enviado correctamente")
                return True
            except Exception as e:
                print(f"❌ Error enviando reporte: {e}")
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
                print(f"⚠️ Error en envío automático: {e}")
        return False

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
                    except Exception:
                        pass
                self.registrar_operacion(datos_operacion)
                operaciones_cerradas.append(simbolo)
                del self.operaciones_activas[simbolo]
                if simbolo in self.senales_enviadas:
                    self.senales_enviadas.remove(simbolo)
                self.operaciones_desde_optimizacion += 1
                print(f"     📊 {simbolo} Operación {resultado} - PnL: {pnl_percent:.2f}%")
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
                               title=f'{simbolo} | {tipo_operacion} | {config_optima["timeframe"]} | Bitget V2 + Breakout+Reentry',
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
            logger.warning(f"Error generando gráfico para {simbolo}: {e}")
            # Intentar con estilo básico si falla el principal
            try:
                fig, axes = mpf.plot(df, type='candle', style='classic',
                                   title=f'{simbolo} | {tipo_operacion}',
                                   ylabel='Precio',
                                   addplot=apds[:2],  # Solo resistencia y soporte
                                   volume=False,
                                   returnfig=True,
                                   figsize=(12, 8))
                axes[1].set_ylim([0, 100])
                buf = BytesIO()
                plt.savefig(buf, format='png', dpi=80, bbox_inches='tight')
                buf.seek(0)
                plt.close(fig)
                return buf
            except Exception as e2:
                logger.error(f"Error crítico generando gráfico: {e2}")
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
                print(f"     ❌ Error enviando gráfico: {e}")
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
                print("🔄 Iniciando re-optimización automática...")
                ia = OptimizadorIA(log_path=self.log_path, min_samples=self.config.get('min_samples_optimizacion', 30))
                nuevos_parametros = ia.buscar_mejores_parametros()
                if nuevos_parametros:
                    self.actualizar_parametros(nuevos_parametros)
                    self.ultima_optimizacion = datetime.now()
                    self.operaciones_desde_optimizacion = 0
                    print("✅ Parámetros actualizados en tiempo real")
        except Exception as e:
            print(f"⚠ Error en re-optimización automática: {e}")

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
            self.verificar_envio_reporte_automatico()    
        
        # Usar la nueva verificación que consulta Bitget directamente
        cierres = self.verificar_operaciones_reales_bitget()
        if cierres:
            print(f"     📊 Operaciones cerradas: {', '.join(cierres)}")
        
        # También verificar operaciones locales (por compatibilidad)
        cierres_locales = self.verificar_cierre_operaciones()
        if cierres_locales:
            print(f"     📊 Operaciones locales cerradas: {', '.join(cierres_locales)}")
        
        self.guardar_estado()
        return self.escanear_mercado()

    def mostrar_resumen_operaciones(self):
        print(f"\n📊 RESUMEN OPERACIONES:")
        print(f"   Activas (Estado Interno): {len(self.operaciones_activas)}")
        print(f"   Esperando reentry: {len(self.esperando_reentry)}")
        print(f"   Total ejecutadas: {self.total_operaciones}")
        if self.bitget_client:
            print(f"   🤖 Bitget V2: ✅ Conectado (MARGEN AISLADO)")
            print(f"   📊 Posición Mode: {self.bitget_client.position_mode}")
            
            # Mostrar estado real en Bitget
            try:
                posiciones_reales = self.bitget_client.get_positions()
                posiciones_abiertas = [pos for pos in posiciones_reales if pos.get('positionSize', 0) != 0]
                print(f"   [POSITIONS] Posiciones Reales en Bitget: {len(posiciones_abiertas)}")
                
                if posiciones_abiertas:
                    for pos in posiciones_abiertas:
                        simbolo = pos.get('symbol', 'N/A')
                        size = float(pos.get('positionSize', 0))
                        tipo = "🟢 LONG" if size > 0 else "🔴 SHORT"
                        pnl = float(pos.get('unrealizedPnl', 0))
                        print(f"   • {simbolo} {tipo} | PnL: {pnl:.2f} USDT")
                        
            except Exception as e:
                print(f"   ⚠️ Error consultando posiciones reales: {e}")
        else:
            print(f"   🤖 Bitget: ❌ No configurado")
        
        if self.operaciones_activas:
            print(f"\n   📋 OPERACIONES EN ESTADO INTERNO:")
            for simbolo, op in self.operaciones_activas.items():
                estado = "🟢 LONG" if op['tipo'] == 'LONG' else "🔴 SHORT"
                ancho_canal = op.get('ancho_canal_porcentual', 0)
                timeframe = op.get('timeframe_utilizado', 'N/A')
                velas = op.get('velas_utilizadas', 0)
                breakout = "[B]" if op.get('breakout_usado', False) else ""
                ejecutada = "✅ REAL" if op.get('operacion_ejecutada', False) else "📢 SEÑAL"
                print(f"   • {simbolo} {estado} {breakout} {ejecutada} - {timeframe} - {velas}v - Ancho: {ancho_canal:.1f}%")

    def iniciar(self):
        print("\n" + "=" * 70)
        print("🤖 BOT DE TRADING - ESTRATEGIA BREAKOUT + REENTRY")
        print("🎯 PRIORIDAD: TIMEFRAMES CORTOS (1m > 3m > 5m > 15m > 30m)")
        print("💾 PERSISTENCIA: ACTIVADA")
        print("🔄 REEVALUACIÓN: CADA 2 HORAS")
        print("🏦 INTEGRACIÓN: BITGET API V2")
        print("=" * 70)
        print(f"💱 Símbolos: {len(self.config.get('symbols', []))} monedas")
        print(f"⏰ Timeframes: {', '.join(self.config.get('timeframes', []))}")
        print(f"🕯️ Velas: {self.config.get('velas_options', [])}")
        print(f"📏 ANCHO MÍNIMO: {self.config.get('min_channel_width_percent', 4)}%")
        print(f"[STRATEGY] Estrategia: 1) Detectar Breakout → 2) Esperar Reentry → 3) Confirmar con Stoch y Ejecutar")
        if self.bitget_client:
            print(f"🤖 BITGET V2: ✅ API Conectada")
            print(f"⚡ Apalancamiento: {self.leverage_por_defecto}x")
            print(f"💰 Capital por operación: ${self.capital_por_operacion}")
            print(f"📊 Posición Mode: {self.bitget_client.position_mode}")
            if self.ejecutar_operaciones_automaticas:
                print(f"🤖 AUTO-TRADING: ✅ ACTIVADO (MARGEN AISLADO + HEDGE MODE)")
            else:
                print(f"🤖 AUTO-TRADING: ❌ Solo señales")
        else:
            print(f"🤖 BITGET: ❌ No configurado (solo señales)")
        print("=" * 70)
        print("\n[START] INICIANDO BOT...")
        try:
            while True:
                nuevas_senales = self.ejecutar_analisis()
                self.mostrar_resumen_operaciones()
                minutos_espera = self.config.get('scan_interval_minutes', 1)
                print(f"\n✅ Análisis completado. Señales nuevas: {nuevas_senales}")
                print(f"⏳ Próximo análisis en {minutos_espera} minutos...")
                print("-" * 60)
                for minuto in range(minutos_espera):
                    time.sleep(60)
                    restantes = minutos_espera - (minuto + 1)
                    if restantes > 0 and restantes % 5 == 0:
                        print(f"   ⏰ {restantes} minutos restantes...")
        except KeyboardInterrupt:
            print("\n🛑 Bot detenido por el usuario")
            print("💾 Guardando estado final...")
            self.guardar_estado()
            print("👋 ¡Hasta pronto!")
        except Exception as e:
            print(f"\n❌ Error en el bot: {e}")
            print("💾 Intentando guardar estado...")
            try:
                self.guardar_estado()
            except:
                pass

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
        # NUEVAS CONFIGURACIONES BITGET V2
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
    """Ejecuta el bot en un hilo separado"""
    while True:
        try:
            bot.ejecutar_analisis()
            time.sleep(bot.config.get('scan_interval_minutes', 1) * 60)
        except Exception as e:
            print(f"Error en el hilo del bot: {e}", file=sys.stderr)
            time.sleep(60)

# Iniciar hilo del bot
bot_thread = threading.Thread(target=run_bot_loop, daemon=True)
bot_thread.start()

@app.route('/')
def index():
    return "Bot Breakout + Reentry con integración Bitget V2 está en línea.", 200

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
