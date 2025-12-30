# bot_web_service.py
# Adaptación para ejecución local del bot Breakout + Reentry
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
# Copiado íntegro y corregido para ejecución local
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
# BITGET CLIENT - INTEGRACIÓN COMPLETA CON API BITGET FUTUROS
# ---------------------------
class BitgetClient:
    def __init__(self, api_key, api_secret, passphrase, bot_instance=None):
        # CREDENCIALES REALES DE BITGET FUTUROS (desde automatico.py)
        self.api_key = api_key
        self.api_secret = api_secret
        self.passphrase = passphrase
        self.base_url = "https://api.bitget.com"
        self._bot_instance = bot_instance  # Referencia al bot para persistencia
        logger.info(f"Cliente Bitget FUTUROS inicializado con API Key: {api_key[:10]}...")

    def _generate_signature(self, timestamp, method, request_path, body=''):
        # SI body ya es string (JSON), NO lo tocamos
        if isinstance(body, str):
            body_str = body
        # SI es dict, lo convertimos UNA sola vez
        elif body:
            body_str = json.dumps(body, separators=(',', ':'), ensure_ascii=False)
        else:
            body_str = ''

        message = f'{timestamp}{method}{request_path}{body_str}'

        signature = hmac.new(
            self.api_secret.encode('utf-8'),
            message.encode('utf-8'),
            hashlib.sha256
        ).digest()

        return base64.b64encode(signature).decode()

    def _get_headers(self, method, request_path, body=None):
        timestamp = str(int(time.time() * 1000))
        sign = self._generate_signature(timestamp, method, request_path, body)
        headers = {
            'ACCESS-KEY': self.api_key,
            'ACCESS-SIGN': sign,
            'ACCESS-TIMESTAMP': timestamp,
            'ACCESS-PASSPHRASE': self.passphrase,
            'Content-Type': 'application/json'
        }
        return headers

    def verificar_credenciales(self):
        """Verificar que las credenciales sean válidas"""
        try:
            logger.info("Verificando credenciales Bitget FUTUROS...")
            
            if not self.api_key or not self.api_secret or not self.passphrase:
                logger.error("Credenciales incompletas")
                return False
            
            accounts = self.get_account_info()
            if accounts:
                logger.info("✓ Credenciales BITGET FUTUROS verificadas exitosamente")
                for account in accounts:
                    if account.get('marginCoin') == 'USDT':
                        available = float(account.get('available', 0))
                        logger.info(f"✓ Balance disponible FUTUROS: {available:.2f} USDT")
                return True
            else:
                logger.error("✗ No se pudo verificar credenciales BITGET FUTUROS")
                return False
                
        except Exception as e:
            logger.error(f"Error verificando credenciales BITGET FUTUROS: {e}")
            return False

    def get_account_info(self, product_type='USDT-FUTURES'):
        """Obtener información de cuenta Bitget V2 - FUTUROS"""
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
            
            logger.info(f"Respuesta cuenta FUTUROS - Status: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                if data.get('code') == '00000':
                    return data.get('data', [])
                else:
                    error_msg = data.get('msg', 'Unknown error')
                    error_code = data.get('code', 'Unknown')
                    logger.error(f"Error API BITGET FUTUROS: {error_code} - {error_msg}")
                    
                    if error_code == '40020' and product_type == 'USDT-FUTURES':
                        logger.info("Intentando con productType='USDT-MIX'...")
                        return self.get_account_info('USDT-MIX')
            else:
                logger.error(f"Error HTTP BITGET FUTUROS: {response.status_code} - {response.text}")
                
            return None
            
        except Exception as e:
            logger.error(f"Error en get_account_info BITGET FUTUROS: {e}")
            return None

    def get_symbol_info(self, symbol):
        """Obtener información del símbolo FUTUROS"""
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
            logger.error(f"Error obteniendo info del símbolo BITGET FUTUROS: {e}")
            return None

    def place_tpsl_order(self, symbol, hold_side, trigger_price, order_type='stop_loss', stop_loss_price=None, take_profit_price=None):
        """
        Coloca orden de Stop Loss o Take Profit en Bitget Futuros
        
        Args:
            symbol: Símbolo (ej: 'CRVUSDT')
            hold_side: 'long' o 'short'
            trigger_price: Precio de activación
            order_type: 'stop_loss' o 'take_profit'
            stop_loss_price: Precio de stop loss (opcional)
            take_profit_price: Precio de take profit (opcional)
        """
        request_path = '/api/v2/mix/order/place-pos-tpsl'
        
        # CORRECCIÓN: Usar precisión dinámica basada en el precio, no en priceScale
        # Para precios muy pequeños (como SHIBUSDT, PEPE, ENSUSDT, XLMUSDT, etc.)
        precision_adaptada = self.obtener_precision_adaptada(trigger_price, symbol)
        trigger_price_formatted = self.redondear_precio_manual(trigger_price, precision_adaptada)
        
        body = {
            'symbol': symbol,
            'productType': 'USDT-FUTURES',
            'marginCoin': 'USDT',
            'holdSide': hold_side,
            'orderType': 'market',
            'triggerType': 'mark_price',
            'triggerPrice': trigger_price_formatted,
            # CORRECCIÓN ERROR 40034: Estos parámetros son OBLIGATORIOS
            'stopLossTriggerType': 'mark_price',
            'stopSurplusTriggerType': 'mark_price'
        }
        
        # CORRECCIÓN: Usar nombres correctos de parámetros según API Bitget v2
        if order_type == 'stop_loss' and stop_loss_price:
            precision_sl = self.obtener_precision_adaptada(stop_loss_price, symbol)
            stop_loss_formatted = self.redondear_precio_manual(stop_loss_price, precision_sl)
            body['stopLossTriggerPrice'] = stop_loss_formatted
            logger.info(f"🔧 SL para {symbol}: precio={stop_loss_price}, precision={precision_sl}, formatted={stop_loss_formatted}")
        elif order_type == 'take_profit' and take_profit_price:
            precision_tp = self.obtener_precision_adaptada(take_profit_price, symbol)
            take_profit_formatted = self.redondear_precio_manual(take_profit_price, precision_tp)
            body['stopSurplusTriggerPrice'] = take_profit_formatted
            logger.info(f"🔧 TP para {symbol}: precio={take_profit_price}, precision={precision_tp}, formatted={take_profit_formatted}")
        
        body_json = json.dumps(body, separators=(',', ':'), ensure_ascii=False)
        headers = self._get_headers('POST', request_path, body_json)
        
        logger.info(f"📤 Enviando orden {order_type} para {symbol}: {body}")
        
        response = requests.post(
            self.base_url + request_path,
            headers=headers,
            data=body_json,
            timeout=10
        )
        
        logger.info(f"📤 Respuesta TP/SL BITGET: {response.status_code} - {response.text}")
        
        if response.status_code == 200:
            data = response.json()
            if data.get('code') == '00000':
                logger.info(f"✅ {order_type.upper()} creado correctamente para {symbol}")
                return data.get('data')
            else:
                # Error 40017: parámetros incorrectos
                if data.get('code') == '40017':
                    logger.error(f"❌ Error 40017 en {order_type}: {data.get('msg')}")
                    logger.error(f"💡 Body enviado: {body}")
                # Error 40034: faltan parámetros de tipo
                if data.get('code') == '40034':
                    logger.error(f"❌ Error 40034 en {order_type}: {data.get('msg')}")
                    logger.error(f"💡 Body enviado: {body}")
        
        logger.error(f"❌ Error creando {order_type}: {response.text}")
        return None

    def place_plan_order(self, symbol, hold_side, trigger_price, plan_type):
        """
        Método legacy - ya no usar. Usar place_tpsl_order en su lugar.
        """
        # Este método está obsoleto, usar place_tpsl_order
        logger.warning("⚠️ place_plan_order está obsoleto, usando place_tpsl_order")
        return None

    def get_position_mode(self, symbol, product_type='USDT-FUTURES'):
        """
        Obtener el modo de posición actual de la cuenta para un símbolo específico
        
        Returns:
            str: 'hedged' (cobertura) o 'unilateral' (one-way) o None si hay error
        """
        try:
            request_path = '/api/v2/mix/account/account'
            params = {'productType': product_type, 'marginCoin': 'USDT', 'symbol': symbol}
            
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
                    account_data = data.get('data', {})
                    logger.info(f"📋 Modo de cuenta para {symbol}: {account_data}")
                    return account_data
            
            return None
        except Exception as e:
            logger.error(f"Error obteniendo modo de posición: {e}")
            return None

    def set_hedged_mode(self, symbol, hedged_mode=True):
        """
        Configurar el modo de posición (hedge/unilateral) para un símbolo
        
        Args:
            symbol: Símbolo (ej: 'BTCUSDT')
            hedged_mode: True para modo cobertura (hedged), False para unilateral (one-way)
        
        Returns:
            bool: True si se configuró correctamente
        """
        try:
            request_path = '/api/v2/mix/account/set-position-mode'
            body = {
                'symbol': symbol,
                'productType': 'USDT-FUTURES',
                'marginCoin': 'USDT',
                'holdSide': 'long' if hedged_mode else 'short',  # Configuración dummy para API
                'positionMode': 'hedged' if hedged_mode else 'single'  # Modo de posición
            }
            
            logger.info(f"Configurando modo {'hedged' if hedged_mode else 'unilateral'} para {symbol}")
            
            body_json = json.dumps(body, separators=(',', ':'), ensure_ascii=False)
            headers = self._get_headers('POST', request_path, body_json)
            
            response = requests.post(
                self.base_url + request_path,
                headers=headers,
                data=body_json,
                timeout=10
            )
            
            logger.info(f"Respuesta set_position_mode BITGET: {response.status_code} - {response.text}")
            
            if response.status_code == 200:
                data = response.json()
                if data.get('code') == '00000':
                    logger.info(f"✓ Modo {'hedged' if hedged_mode else 'unilateral'} configurado para {symbol}")
                    return True
                    
            return False
        except Exception as e:
            logger.error(f"Error configurando modo de posición: {e}")
            return False

    def place_order(self, symbol, side, size, order_type='market', posSide=None, is_hedged_account=False, 
                    stop_loss_price=None, take_profit_price=None):
        """
        Coloca orden de entrada MARKET en Bitget Futuros con TP/SL integrados
        
        Args:
            symbol: Símbolo (ej: 'BTCUSDT')
            side: 'buy' o 'sell'
            size: número de contratos (int)
            order_type: 'market' o 'limit'
            posSide: 'long' o 'short' (SOLO para modo unilateral, omitir en modo hedge)
            is_hedged_account: True si la cuenta está en modo cobertura (hedged)
            stop_loss_price: Precio de Stop Loss (opcional)
            take_profit_price: Precio de Take Profit (opcional)
        """
        request_path = '/api/v2/mix/order/place-order'

        # Obtener precisión adaptativa para precios TP/SL
        if stop_loss_price is not None:
            # Usar precision adaptativa para evitar redondeo a cero en memecoins
            precision_sl = self.obtener_precision_adaptada(float(stop_loss_price), symbol)
            # Redondear pasando el símbolo para obtener el priceStep correcto
            stop_loss_formatted = self.redondear_precio_manual(float(stop_loss_price), precision_sl, symbol)
        else:
            stop_loss_formatted = None
            
        if take_profit_price is not None:
            precision_tp = self.obtener_precision_adaptada(float(take_profit_price), symbol)
            take_profit_formatted = self.redondear_precio_manual(float(take_profit_price), precision_tp, symbol)
        else:
            take_profit_formatted = None

        # EN MODO HEDGE: NO enviar posSide (causa error 40774)
        # EN MODO UNILATERAL: SÍ enviar posSide
        if is_hedged_account:
            body = {
                "symbol": symbol,
                "productType": "USDT-FUTURES",
                "marginMode": "isolated",
                "marginCoin": "USDT",
                "side": side,           # buy = long, sell = short
                "orderType": "market",
                "size": str(size)
                # NOTA: En modo hedge, NO incluir posSide - causa error 40774
            }
            logger.info(f"📤 Orden en MODO HEDGE: side={side}, size={size} (sin posSide)")
        else:
            # Modo unilateral: posSide es obligatorio
            if not posSide:
                logger.error("❌ En modo unilateral, posSide es obligatorio ('long' o 'short')")
                return None
            body = {
                "symbol": symbol,
                "productType": "USDT-FUTURES",
                "marginMode": "isolated",
                "marginCoin": "USDT",
                "side": side,           # buy = long, sell = short  
                "orderType": "market",
                "size": str(size),
                "posSide": posSide      # OBLIGATORIO para modo unilateral
            }
            logger.info(f"📤 Orden en MODO UNILATERAL: side={side}, posSide={posSide}, size={size}")

        # AGREGAR TP/SL DIRECTAMENTE EN LA ORDEN (según API Bitget v2)
        # Documentación Bitget: presetStopSurplusPrice y presetStopLossPrice
        if stop_loss_formatted is not None:
            body["presetStopLossPrice"] = str(stop_loss_formatted)
            logger.info(f"🔧 SL integrado: presetStopLossPrice={stop_loss_formatted}")
        
        if take_profit_formatted is not None:
            body["presetStopSurplusPrice"] = str(take_profit_formatted)
            logger.info(f"🔧 TP integrado: presetStopSurplusPrice={take_profit_formatted}")

        body_json = json.dumps(body, separators=(',', ':'), ensure_ascii=False)

        headers = self._get_headers('POST', request_path, body_json)

        logger.info(f"📤 Enviando orden con TP/SL integrados: {body}")

        response = requests.post(
            self.base_url + request_path,
            headers=headers,
            data=body_json,
            timeout=10
        )

        if response.status_code == 200:
            data = response.json()
            if data.get('code') == '00000':
                logger.info(f"✅ Orden ejecutada ({side.upper()}) con TP/SL en modo {'HEDGE' if is_hedged_account else 'UNILATERAL'}")
                return data.get('data')
            else:
                # Error 40774: Modo de posición冲突
                if data.get('code') == '40774':
                    logger.error(f"❌ Error 40774: La cuenta está en modo {'HEDGE' if not is_hedged_account else 'UNILATERAL'} pero la orden espera el otro modo")
                    logger.error(f"💡 Solución: Verificar configuración de modo de posición en Bitget")
                # Error 40034: parámetros faltantes
                if data.get('code') == '40034':
                    logger.error(f"❌ Error 40034: {data.get('msg')}")

        logger.error(f"❌ Error orden entrada: {response.text}")
        return None

    def obtener_precision_precio(self, symbol):
        """Obtiene la precisión de precio para un símbolo específico"""
        try:
            symbol_info = self.get_symbol_info(symbol)
            if symbol_info:
                # Obtener la precisión de precio (priceScale)
                price_scale = symbol_info.get('priceScale', 4)  # Default a 4 decimales
                logger.info(f"📋 {symbol}: priceScale = {price_scale}")
                return price_scale
            else:
                logger.warning(f"No se pudo obtener info de {symbol}, usando 4 decimales por defecto")
                return 4
        except Exception as e:
            logger.error(f"Error obteniendo precisión de {symbol}: {e}")
            return 4  # Fallback seguro

    def redondear_precio_precision(self, price, symbol):
        """Redondea el precio a la precisión correcta para el símbolo"""
        try:
            precision = self.obtener_precision_precio(symbol)
            precio_redondeado = round(float(price), precision)
            logger.info(f"🔢 {symbol}: {price} → {precio_redondeado} (precisión: {precision} decimales)")
            return precio_redondeado
        except Exception as e:
            logger.error(f"Error redondeando precio para {symbol}: {e}")
            return float(price)  # Fallback

    def obtener_precision_adaptada(self, price, symbol):
        """
        Obtiene la precisión adaptativa basada en el precio para evitar redondeo a cero.
        
        Para símbolos con precios muy pequeños (SHIBUSDT, PEPE, ENS, XLM, etc.), la precisión
        de priceScale no es suficiente. Este método calcula la precisión necesaria
        para mantener al menos 4-6 dígitos significativos.
        
        Args:
            price: Precio a evaluar
            symbol: Símbolo de trading
        
        Returns:
            int: Número de decimales a usar como "mínimo" para cálculo interno
        """
        try:
            price = float(price)
            
            # Para precios < 1, siempre usar alta precisión para evitar redondeo a cero
            # Esta es la causa principal del error con SHIB, PEPE, ENS, XLM, PHA, etc.
            if price < 1:
                # Para precios muy pequeños, necesitamos muchos decimales
                if price < 0.00001:
                    return 12  # Para PEPE, SHIB y similares
                elif price < 0.0001:
                    return 10  # Para memecoins extremos
                elif price < 0.001:
                    return 8   # Para memecoins y precios muy pequeños
                elif price < 0.01:
                    return 7   # Para precios como ENS (~0.008)
                elif price < 0.1:
                    return 6   # Para precios como PHA (~0.1)
                elif price < 1:
                    return 5   # Para precios como XLM (~0.2)
            else:
                # Para precios >= 1, usar 4 decimales como mínimo
                return 4
                
        except Exception as e:
            logger.error(f"Error calculando precisión adaptativa: {e}")
            return 8  # Fallback seguro para cualquier precio

    def redondear_precio_manual(self, price, precision, symbol=None):
        """
        Redondea el precio con una precisión específica, asegurando que sea un múltiplo válido.
        IMPORTANTE: Para la API de Bitget, el precio debe ser un múltiplo del priceStep.
        
        Args:
            price: Precio a redondear
            precision: Número de decimales
            symbol: Símbolo opcional para obtener priceStep real del exchange
        
        Returns:
            str: Precio redondeado como string (nunca cero si el precio original > 0)
        """
        try:
            price = float(price)
            if price == 0:
                return "0.0"
            
            # Si tenemos el símbolo, intentar obtener el priceStep real del exchange
            if symbol:
                symbol_info = self.get_symbol_info(symbol)
                if symbol_info:
                    # priceScale define los decimales, y el precio debe ser múltiplo del tick
                    # Para la mayoría de símbolos, el tick es 10^(-priceScale)
                    price_scale = symbol_info.get('priceScale', 4)
                    tick_size = 10 ** (-price_scale)
                    
                    # Redondear matemáticamente al múltiplo más cercano del tick_size
                    precio_redondeado = round(price / tick_size) * tick_size
                    
                    # Usar formato para evitar errores de punto flotante
                    # IMPORTANTE: Usar price_scale decimales, no la precisión recibida
                    precio_formateado = f"{precio_redondeado:.{price_scale}f}"
                    
                    # Verificar que no sea cero
                    if float(precio_formateado) == 0.0 and price > 0:
                        # Si se redondeó a cero, usar más decimales
                        nueva_scale = price_scale + 4
                        tick_size = 10 ** (-nueva_scale)
                        precio_redondeado = round(price / tick_size) * tick_size
                        precio_formateado = f"{precio_redondeado:.{nueva_scale}f}"
                    
                    logger.info(f"🔢 {symbol}: precio={price}, priceScale={price_scale}, tick={tick_size}, resultado={precio_formateado}")
                    return precio_formateado
            
            # Fallback: usar la precisión proporcionada
            tick_size = 10 ** (-precision)
            precio_redondeado = round(price / tick_size) * tick_size
            precio_formateado = f"{precio_redondeado:.{precision}f}"
            
            if float(precio_formateado) == 0.0 and price > 0:
                nueva_precision = precision + 4
                return self.redondear_precio_manual(price, nueva_precision, symbol)
            
            return precio_formateado
        except Exception as e:
            logger.error(f"Error redondeando precio manualmente: {e}")
            return str(price)

    def redondear_a_price_step(self, price, symbol):
        """
        Redondea el precio al priceStep correcto del símbolo según la API de Bitget.
        Esto asegura que el precio sea un múltiplo válido para la API.
        
        Args:
            price: Precio a redondear
            symbol: Símbolo de trading
        
        Returns:
            float: Precio redondeado al priceStep del símbolo
        """
        try:
            # Obtener la precisión del símbolo
            precision = self.obtener_precision_precio(symbol)
            price_step = 10 ** (-precision)
            
            # Redondear al múltiplo más cercano del priceStep
            precio_redondeado = round(price / price_step) * price_step
            
            # Formatear para eliminar errores de punto flotante
            return float(f"{precio_redondeado:.{precision}f}")
        except Exception as e:
            logger.error(f"Error redondeando a priceStep para {symbol}: {e}")
            # Fallback: usar 4 decimales para la mayoría de símbolos
            return float(f"{price:.4f}")

    def set_leverage(self, symbol, leverage, hold_side='long'):
        """Configurar apalancamiento en BITGET FUTUROS"""
        try:
            request_path = '/api/v2/mix/account/set-leverage'
            body = {
                'symbol': symbol,
                'productType': 'USDT-FUTURES',
                'marginCoin': 'USDT',
                'leverage': str(leverage),
                'holdSide': hold_side
            }
            
            logger.info(f"Configurando leverage {leverage}x para {symbol} ({hold_side})")
            
            body_json = json.dumps(body, separators=(',', ':'), ensure_ascii=False)

            headers = self._get_headers(
                'POST',
                request_path,
                body_json
            )

            response = requests.post(
                self.base_url + request_path,
                headers=headers,
                data=body_json,
                timeout=10
            )
            
            logger.info(f"Respuesta leverage BITGET: {response.status_code} - {response.text}")
            
            if response.status_code == 200:
                data = response.json()
                if data.get('code') == '00000':
                    logger.info(f"✓ Apalancamiento {leverage}x configurado en BITGET FUTUROS para {symbol}")
                    return True
                else:
                    logger.error(f"Error configurando leverage BITGET FUTUROS: {data.get('code')} - {data.get('msg')}")
            else:
                logger.error(f"Error HTTP configurando leverage BITGET FUTUROS: {response.status_code} - {response.text}")
            return False
        except Exception as e:
            logger.error(f"Error en set_leverage BITGET FUTUROS: {e}")
            return False

    def get_positions(self, symbol=None, product_type='USDT-FUTURES'):
        """Obtener posiciones abiertas en BITGET FUTUROS"""
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
            logger.error(f"Error obteniendo posiciones BITGET FUTUROS: {e}")
            return []

    def obtener_reglas_simbolo(self, symbol):
        """Obtiene las reglas específicas de tamaño para un símbolo"""
        try:
            symbol_info = self.get_symbol_info(symbol)
            if not symbol_info:
                logger.warning(f"No se pudo obtener info de {symbol}, usando valores por defecto")
                return {
                    'size_scale': 0,
                    'quantity_scale': 0,
                    'min_trade_num': 1,
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
            return {
                'size_scale': 0,
                'quantity_scale': 0,
                'min_trade_num': 1,
                'size_multiplier': 1,
                'delivery_mode': 0
            }
    
    def ajustar_tamaño_orden(self, symbol, cantidad_contratos, reglas):
        """Ajusta el tamaño de la orden según las reglas del símbolo"""
        try:
            size_scale = reglas['size_scale']
            quantity_scale = reglas['quantity_scale']
            min_trade_num = reglas['min_trade_num']
            size_multiplier = reglas['size_multiplier']
            
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
            
            # Validación final - MANEJAR CASOS ESPECIALES
            if escala_actual == 0:
                # Si requiere entero pero min_trade_num es decimal (caso especial como INJUSDT)
                if min_trade_num < 1 and min_trade_num > 0:
                    # Usar el mínimo pero asegurar que sea al menos 1
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
            logger.error(f"Error ajustando tamaño para {symbol}: {e}")
            return int(round(cantidad_contratos))  # Fallback a entero
    
    def obtener_saldo_cuenta(self):
        """Obtiene el saldo actual de la cuenta Bitget FUTUROS"""
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
            logger.error(f"Error obteniendo saldo de cuenta: {e}")
            return None

    def get_klines(self, symbol, interval='5m', limit=200):
        """Obtener velas (datos de mercado) de BITGET FUTUROS"""
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
            logger.error(f"Error en get_klines BITGET FUTUROS: {e}")
            return None

# ---------------------------
# FUNCIONES DE OPERACIONES BITGET FUTUROS
# ---------------------------
def ejecutar_operacion_bitget(bitget_client, simbolo, tipo_operacion, capital_usd=None, leverage=20):
    """
    Ejecutar una operación completa en BITGET FUTUROS (posición + TP/SL)
    
    Args:
        bitget_client: Instancia de BitgetClient
        simbolo: Símbolo de trading (ej: 'BTCUSDT')
        tipo_operacion: 'LONG' o 'SHORT'
        capital_usd: Capital a usar en USD (None = usar 3% del saldo actual)
        leverage: Apalancamiento (default: 20)
    
    Returns:
        dict con información de la operación ejecutada
    """
    
    logger.info(f"🚀 EJECUTANDO OPERACIÓN REAL EN BITGET FUTUROS")
    logger.info(f"Símbolo: {simbolo}")
    logger.info(f"Tipo: {tipo_operacion}")
    
    try:
        # OBTENER SALDO ACTUAL DE LA CUENTA
        saldo_cuenta = bitget_client.obtener_saldo_cuenta()
        if not saldo_cuenta or saldo_cuenta < 10:  # Mínimo $10 para operar
            logger.error(f"❌ Saldo insuficiente o no disponible: ${saldo_cuenta if saldo_cuenta else 0:.2f}")
            return None
        
        # CALCULAR MARGIN USDT: 3% del saldo actual
        margin_usdt_objetivo = round(saldo_cuenta * 0.03, 2)
        
        logger.info(f"💰 Saldo actual cuenta: ${saldo_cuenta:.2f}")
        logger.info(f"📊 3% del saldo actual (MARGIN USDT objetivo): ${margin_usdt_objetivo:.2f}")
        logger.info(f"⚡ Apalancamiento: {leverage}x")
        logger.info(f"💡 Esta operación usará hasta 3% del saldo actual: ${margin_usdt_objetivo:.2f}")
        logger.info(f"💡 Saldo restante después de esta operación: ${saldo_cuenta - margin_usdt_objetivo:.2f}")
        
        # 1. Obtener información del símbolo primero
        symbol_info = bitget_client.get_symbol_info(simbolo)
        if not symbol_info:
            logger.error(f"No se pudo obtener info de {simbolo} en BITGET FUTUROS")
            return None
        
        # 2. Configurar apalancamiento ANTES de obtener precio
        hold_side = 'long' if tipo_operacion == 'LONG' else 'short'
        
        logger.info(f"Configurando apalancamiento {leverage}x para {simbolo} ({hold_side})")
        leverage_ok = bitget_client.set_leverage(simbolo, leverage, hold_side)
        
        if not leverage_ok:
            logger.warning("No se pudo configurar apalancamiento, continuando...")
        else:
            logger.info("✓ Apalancamiento configurado exitosamente")
            
        time.sleep(1)  # Esperar un poco más para asegurar que se aplique
        
        # 3. Obtener precio actual
        klines = bitget_client.get_klines(simbolo, '1m', 1)
        if not klines or len(klines) == 0:
            logger.error(f"No se pudo obtener precio de {simbolo} en BITGET FUTUROS")
            return None
        
        klines.reverse()  # Bitget devuelve en orden descendente
        precio_actual = float(klines[0][4])  # Precio de cierre de la última vela
        
        logger.info(f"Precio actual: {precio_actual:.8f}")
        
        # 4. Calcular cantidad de contratos basada en MARGIN USDT (3% del saldo)
        # El valor nocional de la posición será MARGIN * LEVERAGE
        
        # Obtener reglas específicas del símbolo
        reglas = bitget_client.obtener_reglas_simbolo(simbolo)
        
        # Calcular valor nocional basado en MARGIN USDT y apalancamiento
        valor_nocional_objetivo = margin_usdt_objetivo * leverage
        logger.info(f"💰 MARGIN USDT (3% del saldo): ${margin_usdt_objetivo:.2f}")
        logger.info(f"💰 Valor nocional objetivo (MARGIN x LEVERAGE): ${valor_nocional_objetivo:.2f}")
        logger.info(f"💡 Objetivo: Todas las operaciones tendrán hasta ${margin_usdt_objetivo:.2f} como MARGIN USDT")
        
        # Calcular cantidad de contratos basada en el valor nocional objetivo
        cantidad_contratos = valor_nocional_objetivo / precio_actual
        
        # Ajustar tamaño usando la función especializada
        cantidad_contratos = bitget_client.ajustar_tamaño_orden(simbolo, cantidad_contratos, reglas)
        
        # Calcular el valor nocional real después del ajuste
        valor_nocional_real = cantidad_contratos * precio_actual
        
        # Calcular el MARGIN USDT real (valor nocional / leverage)
        margin_real = valor_nocional_real / leverage
        
        logger.info(f"📊 Cantidad ajustada: {cantidad_contratos} contratos")
        logger.info(f"📊 Valor nocional real: ${valor_nocional_real:.2f}")
        logger.info(f"📊 MARGIN USDT real: ${margin_real:.2f}")
        logger.info(f"📊 Size scale: {reglas['size_scale']}")
        logger.info(f"📊 Quantity scale: {reglas['quantity_scale']}")
        logger.info(f"📊 Min trade num: {reglas['min_trade_num']}")
        logger.info(f"📊 Size multiplier: {reglas['size_multiplier']}")
        
        # VERIFICACIÓN CRÍTICA: El MARGIN USDT real no debe exceder el saldo disponible
        max_margin_permitido = saldo_cuenta * 0.95  # Dejar siempre 5% de reserva
        if margin_real > max_margin_permitido:
            logger.warning(f"⚠️ MARGIN USDT real (${margin_real:.2f}) excede el máximo permitido (${max_margin_permitido:.2f})")
            logger.warning(f"📊 Calculando tamaño máximo permitido según saldo disponible...")
            
            # Calcular el tamaño máximo basado en el saldo disponible
            max_valor_nocional = max_margin_permitido * leverage
            cantidad_maxima = max_valor_nocional / precio_actual
            
            # Ajustar a las reglas del símbolo
            cantidad_maxima = bitget_client.ajustar_tamaño_orden(simbolo, cantidad_maxima, reglas)
            
            # Verificar que no sea mayor que la cantidad original
            if cantidad_maxima < cantidad_contratos:
                cantidad_contratos = cantidad_maxima
                valor_nocional_real = cantidad_contratos * precio_actual
                margin_real = valor_nocional_real / leverage
                
                logger.info(f"📊 Cantidad reducida al máximo permitido: {cantidad_contratos} contratos")
                logger.info(f"📊 Valor nocional ajustado: ${valor_nocional_real:.2f}")
                logger.info(f"📊 MARGIN USDT ajustado: ${margin_real:.2f}")
            else:
                logger.warning(f"⚠️ Incluso el tamaño máximo ({cantidad_contratos}) excede el saldo disponible")
                logger.error(f"❌ No se puede ejecutar la operación: margen requerido (${margin_real:.2f}) > saldo disponible (${max_margin_permitido:.2f})")
                return None
        
        # VERIFICACIÓN FINAL: Verificar que la orden pueda ejecutarse con el saldo disponible
        if margin_real > saldo_cuenta * 0.9:  # Si requiere más del 90% del saldo
            logger.error(f"❌ Error: Margen requerido (${margin_real:.2f}) excede el 90% del saldo disponible (${saldo_cuenta:.2f})")
            logger.error(f"📊 Para {simbolo} a ${precio_actual:.2f}, el mínimo de {cantidad_contratos} contratos requiere ${margin_real:.2f} de margen")
            logger.error(f"💡 Recomendación: El saldo de ${saldo_cuenta:.2f} es muy bajo para operar {simbolo}")
            return None
        
        # Verificación adicional: el margin_real no debe exceder significativamente el objetivo
        diferencia_porcentual = abs(margin_real - margin_usdt_objetivo) / margin_usdt_objetivo * 100
        if diferencia_porcentual > 5:  # Más de 5% de diferencia
            logger.warning(f"⚠️ MARGIN USDT real (${margin_real:.2f}) difiere del objetivo (${margin_usdt_objetivo:.2f})")
            logger.warning(f"📊 Diferencia: {diferencia_porcentual:.1f}%")
            logger.info(f"💡 Esto puede ocurrir por limitaciones de tamaño mínimo del símbolo")
        
        # Usar el margin_real como el margin_usdt final
        margin_usdt = margin_real
        
        # 5. Calcular TP y SL (2% SL, 10% TP para mejor RR)
        if tipo_operacion == "LONG":
            sl_porcentaje = 0.02  # 2% Stop Loss
            tp_porcentaje = 0.10  # 10% Take Profit (RR 5:1)
            stop_loss = precio_actual * (1 - sl_porcentaje)
            take_profit = precio_actual * (1 + tp_porcentaje)
        else:
            sl_porcentaje = 0.02  # 2% Stop Loss
            tp_porcentaje = 0.10  # 10% Take Profit (RR 5:1)
            stop_loss = precio_actual * (1 + sl_porcentaje)
            take_profit = precio_actual * (1 - tp_porcentaje)
        
        # Formatear precios según la precisión del símbolo
        def formatear_precio(price):
            """Formatear precio con precisión apropiada para Bitget"""
            if price >= 1:
                return f"{price:.4f}"
            elif price >= 0.1:
                return f"{price:.5f}"
            elif price >= 0.01:
                return f"{price:.6f}"
            elif price >= 0.001:
                return f"{price:.7f}"
            else:
                # Para precios muy pequeños como PEPE
                return f"{price:.8f}".rstrip('0').rstrip('.')
        
        sl_formatted = formatear_precio(stop_loss)
        tp_formatted = formatear_precio(take_profit)
        
        logger.info(f"SL: {stop_loss} ({sl_porcentaje*100}%) -> {sl_formatted}")
        logger.info(f"TP: {take_profit} ({tp_porcentaje*100}%) -> {tp_formatted}")
        
        # 6. Abrir posición con TP/SL INTEGRADOS según API Bitget v2
        if tipo_operacion == "LONG":
            side = 'buy'
        else:
            side = 'sell'
            
        logger.info(f"Colocando orden de {side} con cantidad {cantidad_contratos}")
        
        # Determinar posSide para evitar error 40774
        pos_side = 'long' if tipo_operacion == 'LONG' else 'short'
        
        # LÓGICA DE REINTENTO PARA ERROR 40774
        # Intentar primero con la configuración actual, si falla, intentar con el otro modo
        orden_entrada = None
        errores = []
        intentos = 0
        max_intentos = 2
        
        while intentos < max_intentos and orden_entrada is None:
            intentos += 1
            
            if intentos == 1:
                # Primer intento: modo unilateral (con posSide)
                logger.info(f"🔄 Intento {intentos}/2: Modo UNILATERAL (con posSide={pos_side})")
                orden_entrada = bitget_client.place_order(
                    symbol=simbolo,
                    side=side,
                    order_type='market',
                    size=str(cantidad_contratos),
                    posSide=pos_side,
                    is_hedged_account=False,
                    stop_loss_price=stop_loss,
                    take_profit_price=take_profit
                )
            elif intentos == 2:
                # Segundo intento: modo hedge (sin posSide)
                logger.info(f"🔄 Intento {intentos}/2: Modo HEDGE (sin posSide)")
                orden_entrada = bitget_client.place_order(
                    symbol=simbolo,
                    side=side,
                    order_type='market',
                    size=str(cantidad_contratos),
                    posSide=None,
                    is_hedged_account=True,
                    stop_loss_price=stop_loss,
                    take_profit_price=take_profit
                )
        
        if orden_entrada:
            logger.info("✅ Orden de entrada ejecutada exitosamente con TP/SL integrados")
        else:
            logger.error("❌ Error abriendo posición en BITGET FUTUROS después de todos los intentos")
            logger.error(f"💡 Error 40774: Verificar configuración de modo de posición en Bitget")
            logger.error("💡 Solución: Cambiar a modo 'Unilateral' (One-Way) en la configuración de Bitget Futures")
            return None
        
        logger.info(f"✓ Posición abierta en BITGET FUTUROS: {orden_entrada}")
        time.sleep(2)  # Espera reducida porque TP/SL ya están integrados en la orden principal
        
        # 7. VERIFICACIÓN DE POSICIÓN CON TP/SL YA INTEGRADOS
        logger.info("🔍 Verificando posición con TP/SL integrados...")
        time.sleep(2)
        try:
            posiciones = bitget_client.get_positions(simbolo)
            for pos in posiciones:
                if pos.get('symbol') == simbolo:
                    logger.info(f"✅ Posición verificada:")
                    logger.info(f"  - Side: {pos.get('holdSide', 'N/A')}")
                    logger.info(f"  - Size: {pos.get('available', pos.get('positionSize', 'N/A'))}")
                    logger.info(f"  - Entry Price: {pos.get('openPriceAvg', 'N/A')}")
                    logger.info(f"  - Take Profit (preset): {pos.get('takeProfit', 'N/A')}")
                    logger.info(f"  - Stop Loss (preset): {pos.get('stopLoss', 'N/A')}")
                    
                    # Verificar que TP/SL fueron configurados correctamente
                    tp_configurado = pos.get('takeProfit', '')
                    sl_configurado = pos.get('stopLoss', '')
                    
                    if tp_configurado and sl_configurado:
                        logger.info("✅ TP y SL configurados correctamente en la posición")
                    elif tp_configurado:
                        logger.warning("⚠️ Solo TP configurado, SL no visible en posición")
                    elif sl_configurado:
                        logger.warning("⚠️ Solo SL configurado, TP no visible en posición")
                    else:
                        logger.warning("⚠️ TP/SL no visibles en posición (pueden estar en proceso)")
                    
                    break
        except Exception as e:
            logger.warning(f"⚠️ Error verificando posición: {e}")
        
        # 8. Verificar posiciones (simplificado)
        logger.info("🔍 Verificando posiciones activas...")
        try:
            posiciones = bitget_client.get_positions(simbolo)
            if posiciones:
                logger.info(f"📊 Posiciones encontradas: {len(posiciones)}")
                for pos in posiciones:
                    if pos.get('symbol') == simbolo:
                        logger.info(f"  - {simbolo}: {pos.get('holdSide', 'N/A')} @ {pos.get('openPriceAvg', 'N/A')}")
        except Exception as e:
            logger.warning(f"⚠️ Error verificando posiciones: {e}")
        
        # 9. Guardar información en estructuras de persistencia del bot
        if hasattr(bitget_client, '_bot_instance'):
            bot = bitget_client._bot_instance
            if bot:
                # Guardar orderid de entrada
                bot.order_ids_entrada[simbolo] = orden_entrada.get('orderId')
                
                # Actualizar operación en estado persistente
                if simbolo in bot.operaciones_activas:
                    bot.operaciones_activas[simbolo].update({
                        'order_id_entrada': orden_entrada.get('orderId'),
                        'order_id_sl': None,  # Ya no hay orderId separado para SL
                        'order_id_tp': None,  # Ya no hay orderId separado para TP
                        'cantidad_contratos': cantidad_contratos,
                        'valor_nocional': valor_nocional_real,
                        'margin_usdt_real': margin_usdt,
                        'leverage_usado': leverage,
                        'tp_integrado': take_profit,
                        'sl_integrado': stop_loss
                    })
                
                # Guardar estado inmediatamente
                bot.guardar_estado()
        
        # 10. Retornar información de la operación
        operacion_data = {
            'orden_entrada': orden_entrada,
            'orden_sl': None,  # No hay orden separada para SL
            'orden_tp': None,  # No hay orden separada para TP
            'cantidad_contratos': cantidad_contratos,
            'precio_entrada': precio_actual,
            'take_profit': take_profit,
            'stop_loss': stop_loss,
            'leverage': leverage,
            'capital_usado': margin_usdt,
            'saldo_cuenta': saldo_cuenta,
            'porcentaje_saldo': round(margin_usdt / saldo_cuenta * 100, 2),
            'tipo': tipo_operacion,
            'timestamp_entrada': datetime.now().isoformat(),
            'symbol': simbolo,
            'order_ids': {
                'entrada': orden_entrada.get('orderId'),
                'sl': None,  # Integrado en la orden principal
                'tp': None   # Integrado en la orden principal
            }
        }
        
        logger.info(f"✅ OPERACIÓN EJECUTADA EXITOSAMENTE EN BITGET FUTUROS")
        logger.info(f"ID Orden: {orden_entrada.get('orderId', 'N/A')}")
        logger.info(f"Contratos: {cantidad_contratos}")
        logger.info(f"💰 MARGIN USDT usado: ${margin_usdt:.2f} (3% del saldo actual)")
        logger.info(f"💰 Saldo antes de la operación: ${saldo_cuenta:.2f}")
        logger.info(f"💰 Saldo después de la operación: ${saldo_cuenta - margin_usdt:.2f}")
        logger.info(f"📊 Valor nocional: ${cantidad_contratos * precio_actual:.2f}")
        logger.info(f"⚡ Apalancamiento: {leverage}x")
        logger.info(f"🎯 Entrada: {precio_actual:.8f}")
        logger.info(f"🛑 SL: {stop_loss:.8f} (-2%) - INTEGRADO")
        logger.info(f"🎯 TP: {take_profit:.8f} - INTEGRADO")
        logger.info(f"🔧 SISTEMA: TP/SL integrados directamente en la orden (API Bitget v2)")
        
        return operacion_data
        
    except Exception as e:
        logger.error(f"Error ejecutando operación en BITGET FUTUROS: {e}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        return None

# ---------------------------
# BOT PRINCIPAL - BREAKOUT + REENTRY CON INTEGRACIÓN BITGET FUTUROS
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
        self.cargar_estado()
        
        # Inicializar persistencia avanzada
        self.inicializar_persistencia_avanzada()
        
        # Inicializar cliente Bitget FUTUROS con credenciales REALES
        self.bitget_client = None
        if config.get('bitget_api_key') and config.get('bitget_api_secret') and config.get('bitget_passphrase'):
            self.bitget_client = BitgetClient(
                api_key=config['bitget_api_key'],
                api_secret=config['bitget_api_secret'],
                passphrase=config['bitget_passphrase'],
                bot_instance=self  # Pasar referencia del bot para persistencia
            )
            if self.bitget_client.verificar_credenciales():
                logger.info("✅ Cliente BITGET FUTUROS inicializado y verificado")
            else:
                logger.warning("⚠️ No se pudieron verificar las credenciales de BITGET FUTUROS")
        
        # Configuración de operaciones automáticas
        self.ejecutar_operaciones_automaticas = config.get('ejecutar_operaciones_automaticas', False)
        self.capital_por_operacion = config.get('capital_por_operacion', None)  # 3% del saldo (dinámico)
        self.leverage_por_defecto = config.get('leverage_por_defecto', 20)  # 20x apalancamiento
        
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
        """Guardar estado completo del bot en archivo JSON - VERSIÓN MEJORADA CON PERSISTENCIA BITGET"""
        try:
            # Convertir ultima_sincronizacion_bitget a string si es datetime
            ultima_sync = getattr(self, 'ultima_sincronizacion_bitget', None)
            ultima_sync_str = None
            if ultima_sync:
                if isinstance(ultima_sync, datetime):
                    ultima_sync_str = ultima_sync.isoformat()
                else:
                    ultima_sync_str = str(ultima_sync)
            
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
                'version_bot': 'v24_persistencia_avanzada',
                # NUEVAS FUNCIONES DE PERSISTENCIA BITGET
                'operaciones_bitget_activas': getattr(self, 'operaciones_bitget_activas', {}),
                'order_ids_entrada': getattr(self, 'order_ids_entrada', {}),
                'order_ids_sl': getattr(self, 'order_ids_sl', {}),
                'order_ids_tp': getattr(self, 'order_ids_tp', {}),
                'ultima_sincronizacion_bitget': ultima_sync_str,
                'operaciones_cerradas_registradas': list(getattr(self, 'operaciones_cerradas_registradas', set()))
            }
            
            with open(self.estado_file, 'w', encoding='utf-8') as f:
                json.dump(estado, f, indent=2, ensure_ascii=False)
            
            logger.info(f"✅ Estado completo guardado exitosamente en {self.estado_file}")
            logger.info(f"📊 Operaciones activas en estado: {len(self.operaciones_activas)}")
            logger.info(f"📊 Operaciones Bitget activas: {len(getattr(self, 'operaciones_bitget_activas', {}))}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Error guardando estado: {e}")
            # Intento de debug adicional
            try:
                logger.error(f"Error type: {type(e)}")
                logger.error(f"Error details: {str(e)}")
            except:
                pass
            return False

    def cargar_estado(self):
        """Cargar estado completo del bot desde archivo JSON - VERSIÓN MEJORADA"""
        try:
            if not os.path.exists(self.estado_file):
                logger.info("📝 No existe archivo de estado, iniciando con estado limpio")
                return False
            
            with open(self.estado_file, 'r', encoding='utf-8') as f:
                estado = json.load(f)
            
            # Cargar datos básicos
            ultima_opt_str = estado.get('ultima_optimizacion')
            if ultima_opt_str:
                try:
                    self.ultima_optimizacion = datetime.fromisoformat(ultima_opt_str)
                except:
                    self.ultima_optimizacion = datetime.now()
            
            self.operaciones_desde_optimizacion = estado.get('operaciones_desde_optimizacion', 0)
            self.total_operaciones = estado.get('total_operaciones', 0)
            
            # Reconstruir breakout_history
            self.breakout_history = {}
            for k, v in estado.get('breakout_history', {}).items():
                if isinstance(v, str):
                    try:
                        self.breakout_history[k] = datetime.fromisoformat(v)
                    except:
                        self.breakout_history[k] = v
                else:
                    self.breakout_history[k] = v
            
            self.config_optima_por_simbolo = estado.get('config_optima_por_simbolo', {})
            
            # Reconstruir ultima_busqueda_config
            self.ultima_busqueda_config = {}
            for k, v in estado.get('ultima_busqueda_config', {}).items():
                if isinstance(v, str):
                    try:
                        self.ultima_busqueda_config[k] = datetime.fromisoformat(v)
                    except:
                        self.ultima_busqueda_config[k] = v
                else:
                    self.ultima_busqueda_config[k] = v
            
            self.operaciones_activas = estado.get('operaciones_activas', {})
            
            # Reconstruir senales_enviadas
            senales_lista = estado.get('senales_enviadas', [])
            self.senales_enviadas = set(senales_lista)
            
            # Reconstruir esperando_reentry
            self.esperando_reentry = {}
            for k, v in estado.get('esperando_reentry', {}).items():
                try:
                    self.esperando_reentry[k] = {
                        'tipo': v['tipo'],
                        'timestamp': datetime.fromisoformat(v['timestamp']),
                        'precio_breakout': v['precio_breakout'],
                        'config': v.get('config', {})
                    }
                except Exception as e:
                    logger.warning(f"⚠️ Error reconstruyendo esperando_reentry para {k}: {e}")
                    continue
            
            # Reconstruir breakouts_detectados
            self.breakouts_detectados = {}
            for k, v in estado.get('breakouts_detectados', {}).items():
                try:
                    self.breakouts_detectados[k] = {
                        'tipo': v['tipo'],
                        'timestamp': datetime.fromisoformat(v['timestamp']),
                        'precio_breakout': v.get('precio_breakout', 0)
                    }
                except Exception as e:
                    logger.warning(f"⚠️ Error reconstruyendo breakouts_detectados para {k}: {e}")
                    continue
            
            # NUEVAS FUNCIONES DE PERSISTENCIA BITGET
            self.operaciones_bitget_activas = estado.get('operaciones_bitget_activas', {})
            self.order_ids_entrada = estado.get('order_ids_entrada', {})
            self.order_ids_sl = estado.get('order_ids_sl', {})
            self.order_ids_tp = estado.get('order_ids_tp', {})
            
            # Convertir lista de vuelta a set
            operaciones_cerradas_lista = estado.get('operaciones_cerradas_registradas', [])
            self.operaciones_cerradas_registradas = set(operaciones_cerradas_lista)
            
            # Cargar ultima_sincronizacion_bitget con manejo de errores
            ultima_sync_str = estado.get('ultima_sincronizacion_bitget')
            self.ultima_sincronizacion_bitget = None
            if ultima_sync_str:
                try:
                    if isinstance(ultima_sync_str, str):
                        self.ultima_sincronizacion_bitget = datetime.fromisoformat(ultima_sync_str)
                    elif isinstance(ultima_sync_str, datetime):
                        self.ultima_sincronizacion_bitget = ultima_sync_str
                except Exception as e:
                    logger.warning(f"⚠️ Error cargando ultima_sincronizacion_bitget: {e}")
                    self.ultima_sincronizacion_bitget = None
            
            logger.info(f"✅ Estado cargado exitosamente desde {self.estado_file}")
            logger.info(f"📊 Operaciones activas restauradas: {len(self.operaciones_activas)}")
            logger.info(f"📊 Operaciones Bitget activas restauradas: {len(self.operaciones_bitget_activas)}")
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Error cargando estado: {e}")
            # Intentar cargar con datos por defecto para no romper el bot
            try:
                logger.info("🔄 Intentando cargar con datos por defecto...")
                self.operaciones_activas = {}
                self.config_optima_por_simbolo = {}
                self.esperando_reentry = {}
                self.breakouts_detectados = {}
                self.breakout_history = {}
                self.senales_enviadas = set()
                self.inicializar_persistencia_avanzada()
                logger.info("✅ Estado por defecto cargado")
                return True
            except:
                logger.error("❌ No se pudo cargar ni siquiera el estado por defecto")
                return False

    def inicializar_persistencia_avanzada(self):
        """Inicializar estructuras de datos para persistencia avanzada"""
        # Estructuras para seguimiento de operaciones Bitget
        self.operaciones_bitget_activas = {}  # {simbolo: operacion_data}
        self.order_ids_entrada = {}           # {simbolo: order_id_entrada}
        self.order_ids_sl = {}                # {simbolo: order_id_sl}
        self.order_ids_tp = {}                # {simbolo: order_id_tp}
        self.operaciones_cerradas_registradas = set()  # set de simbolos ya procesados
        self.ultima_sincronizacion_bitget = None
        
        logger.info("✅ Persistencia avanzada inicializada")

    def sincronizar_con_bitget(self):
        """Sincronizar estado local con posiciones reales en Bitget - FUNCIÓN CRÍTICA"""
        if not self.bitget_client:
            logger.warning("⚠️ No hay cliente Bitget configurado, omitiendo sincronización")
            return
        
        try:
            logger.info("🔄 Iniciando sincronización con Bitget FUTUROS...")
            
            # Obtener todas las posiciones abiertas en Bitget
            posiciones_bitget = self.bitget_client.get_positions()
            
            if not posiciones_bitget:
                logger.info("📊 No hay posiciones abiertas en Bitget")
                # Limpiar operaciones locales si no hay posiciones en exchange
                operaciones_para_limpiar = []
                for simbolo, op_local in self.operaciones_activas.items():
                    if op_local.get('operacion_ejecutada', False):
                        operaciones_para_limpiar.append(simbolo)
                
                for simbolo in operaciones_para_limpiar:
                    logger.info(f"🧹 Limpiando operación local {simbolo} (no existe en Bitget)")
                    self.procesar_cierre_operacion(simbolo, 'CLEANUP', reason="No existe en exchange")
                
                return
            
            # Procesar posiciones encontradas en Bitget
            posiciones_activas = {}
            for posicion in posiciones_bitget:
                symbol = posicion.get('symbol')
                position_size = float(posicion.get('positionSize', 0))
                hold_side = posicion.get('holdSide', '')
                
                if position_size > 0 and symbol and hold_side:
                    posiciones_activas[symbol] = {
                        'position_size': position_size,
                        'hold_side': hold_side,
                        'average_price': float(posicion.get('averageOpenPrice', 0)),
                        'unrealized_pnl': float(posicion.get('unrealizedAmount', 0)),
                        'position_usdt': float(posicion.get('positionUsdt', 0))
                    }
            
            logger.info(f"📊 Posiciones activas en Bitget: {list(posiciones_activas.keys())}")
            
            # Verificar operaciones locales vs exchange
            for simbolo, op_local in list(self.operaciones_activas.items()):
                if not op_local.get('operacion_ejecutada', False):
                    continue  # Solo procesar operaciones ejecutadas en Bitget
                
                if simbolo not in posiciones_activas:
                    # La operación local no existe en exchange - puede haber sido cerrada
                    logger.warning(f"⚠️ Operación local {simbolo} no encontrada en Bitget")
                    self.procesar_cierre_operacion(simbolo, 'UNKNOWN', reason="No encontrada en exchange")
                else:
                    # Actualizar información local con datos de exchange
                    pos_exchange = posiciones_activas[simbolo]
                    
                    # Actualizar operación local con datos reales
                    self.operaciones_activas[simbolo].update({
                        'precio_entrada_real': pos_exchange['average_price'],
                        'pnl_no_realizado': pos_exchange['unrealized_pnl'],
                        'size_real': pos_exchange['position_size'],
                        'valor_nocional': pos_exchange['position_usdt'],
                        'ultima_sincronizacion': datetime.now().isoformat()
                    })
                    
                    # Mantener en seguimiento
                    self.operaciones_bitget_activas[simbolo] = self.operaciones_activas[simbolo].copy()
            
            # Verificar si hay nuevas operaciones en Bitget que no están en nuestro tracking
            for simbolo, pos_data in posiciones_activas.items():
                if simbolo not in self.operaciones_activas:
                    logger.info(f"👤 OPERACIÓN MANUAL DETECTADA: {simbolo}")
                    logger.info(f"   🛡️ El bot omitirá señales para este par hasta que cierres la operación")
                    logger.info(f"   📊 Detalles: {pos_data['hold_side'].upper()} | Precio: {pos_data['average_price']:.8f} | Size: {pos_data['position_size']}")
                    
                    # Crear entrada local para esta operación
                    tipo_operacion = 'LONG' if pos_data['hold_side'] == 'long' else 'SHORT'
                    self.operaciones_activas[simbolo] = {
                        'tipo': tipo_operacion,
                        'precio_entrada': pos_data['average_price'],
                        'precio_entrada_real': pos_data['average_price'],
                        'timestamp_entrada': datetime.now().isoformat(),
                        'operacion_ejecutada': True,
                        'detected_from_exchange': True,
                        'operacion_manual_usuario': True,  # NUEVO: Marca explícita de operación manual
                        'pnl_no_realizado': pos_data['unrealized_pnl'],
                        'size_real': pos_data['position_size'],
                        'valor_nocional': pos_data['position_usdt'],
                        'fuente': 'sincronizacion_bitget'
                    }
                    
                    self.operaciones_bitget_activas[simbolo] = self.operaciones_activas[simbolo].copy()
                    
                    # Enviar notificación al usuario si hay Telegram configurado
                    try:
                        token = self.config.get('telegram_token')
                        chat_ids = self.config.get('telegram_chat_ids', [])
                        if token and chat_ids:
                            mensaje_manual = f"""
👤 <b>OPERACIÓN MANUAL DETECTADA</b>
📊 <b>Símbolo:</b> {simbolo}
📈 <b>Tipo:</b> {tipo_operacion}
💰 <b>Precio entrada:</b> {pos_data['average_price']:.8f}
📏 <b>Size:</b> {pos_data['position_size']}
💵 <b>Valor nocional:</b> ${pos_data['position_usdt']:.2f}
🛡️ <b>Protección activada:</b> El bot omitirá señales para {simbolo}
⏰ <b>Detectado:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
                            """
                            self._enviar_telegram_simple(mensaje_manual, token, chat_ids)
                    except Exception as e:
                        logger.warning(f"⚠️ Error enviando notificación Telegram: {e}")
            
            self.ultima_sincronizacion_bitget = datetime.now()
            logger.info(f"✅ Sincronización con Bitget completada")
            logger.info(f"📊 Operaciones activas locales: {len(self.operaciones_activas)}")
            logger.info(f"📊 Operaciones Bitget activas: {len(self.operaciones_bitget_activas)}")
            
        except Exception as e:
            logger.error(f"❌ Error en sincronización con Bitget: {e}")

    def verificar_y_recolocar_tp_sl(self):
        """Verificar y recolocar automáticamente TP y SL si es necesario - FUNCIÓN CRÍTICA"""
        if not self.bitget_client:
            return
        
        try:
            logger.info("🔍 Verificando estado de órdenes TP/SL...")
            
            for simbolo, operacion in self.operaciones_bitget_activas.items():
                try:
                    # Verificar si las órdenes plan están activas
                    orden_sl_id = self.order_ids_sl.get(simbolo)
                    orden_tp_id = self.order_ids_tp.get(simbolo)
                    
                    sl_activa = False
                    tp_activa = False
                    
                    # Verificar estado de las órdenes (simplificado - en implementación real usarías API de órdenes)
                    if orden_sl_id:
                        # Aquí iría la verificación real del estado de la orden SL
                        # Por ahora asumimos que están activas si tenemos el ID
                        sl_activa = True
                    
                    if orden_tp_id:
                        # Aquí iría la verificación real del estado de la orden TP
                        tp_activa = True
                    
                    # Si alguna orden no está activa, recolocar
                    if not sl_activa or not tp_activa:
                        logger.warning(f"⚠️ Órdenes TP/SL faltantes para {simbolo}, recolocando...")
                        
                        # Obtener precio actual
                        klines = self.bitget_client.get_klines(simbolo, '1m', 1)
                        if not klines:
                            continue
                        
                        klines.reverse()
                        precio_actual = float(klines[0][4])
                        
                        # Recalcular niveles
                        tipo = operacion['tipo']
                        sl_porcentaje = 0.02
                        tp_porcentaje = 0.10

                        if tipo == "LONG":
                            stop_loss = precio_actual * (1 - sl_porcentaje)
                            take_profit = precio_actual * (1 + tp_porcentaje)
                        else:
                            stop_loss = precio_actual * (1 + sl_porcentaje)
                            take_profit = precio_actual * (1 - tp_porcentaje)

                        hold_side = 'long' if operacion['tipo'] == 'LONG' else 'short'

                        # Recolocar SL
                        if not sl_activa:
                            logger.info(f"🔧 Recolocando STOP LOSS para {simbolo}: {stop_loss}")
                            orden_sl_nueva = self.bitget_client.place_tpsl_order(
                                symbol=simbolo,
                                hold_side=hold_side,
                                trigger_price=stop_loss,
                                order_type='stop_loss',
                                stop_loss_price=stop_loss,
                                take_profit_price=None
                            )
                            if orden_sl_nueva:
                                self.order_ids_sl[simbolo] = orden_sl_nueva.get('orderId')

                        # Recolocar TP
                        if not tp_activa:
                            logger.info(f"🔧 Recolocando TAKE PROFIT para {simbolo}: {take_profit}")
                            orden_tp_nueva = self.bitget_client.place_tpsl_order(
                                symbol=simbolo,
                                hold_side=hold_side,
                                trigger_price=take_profit,
                                order_type='take_profit',
                                stop_loss_price=None,
                                take_profit_price=take_profit
                            )
                            if orden_tp_nueva:
                                self.order_ids_tp[simbolo] = orden_tp_nueva.get('orderId')
                            if orden_tp_nueva:
                                self.order_ids_tp[simbolo] = orden_tp_nueva.get('orderId')
                                logger.info(f"✅ TP recolocada para {simbolo}")
                
                except Exception as e:
                    logger.error(f"❌ Error verificando TP/SL para {simbolo}: {e}")
                    continue
            
            logger.info("✅ Verificación y recolocación de TP/SL completada")
            
        except Exception as e:
            logger.error(f"❌ Error en verificación de TP/SL: {e}")

    def procesar_cierre_operacion(self, simbolo, resultado, reason="", precio_salida=None):
        """Procesar cierre de operación y registrar en log"""
        if simbolo in self.operaciones_cerradas_registradas:
            logger.info(f"⏭️ Operación {simbolo} ya procesada, omitiendo")
            return
        
        try:
            operacion = self.operaciones_activas.get(simbolo)
            if not operacion:
                logger.warning(f"⚠️ No se encontró operación para {simbolo}")
                return
            
            # Obtener precio de salida si no se proporcionó
            if precio_salida is None and self.bitget_client:
                klines = self.bitget_client.get_klines(simbolo, '1m', 1)
                if klines:
                    klines.reverse()
                    precio_salida = float(klines[0][4])
                else:
                    precio_salida = operacion['precio_entrada']
            
            # Calcular PnL
            precio_entrada = operacion.get('precio_entrada_real', operacion['precio_entrada'])
            if operacion['tipo'] == "LONG":
                pnl_percent = ((precio_salida - precio_entrada) / precio_entrada) * 100
            else:
                pnl_percent = ((precio_entrada - precio_salida) / precio_entrada) * 100
            
            # Calcular duración
            tiempo_entrada = datetime.fromisoformat(operacion['timestamp_entrada'])
            duracion_minutos = (datetime.now() - tiempo_entrada).total_seconds() / 60
            
            # Preparar datos para registro
            datos_operacion = {
                'timestamp': datetime.now().isoformat(),
                'symbol': simbolo,
                'tipo': operacion['tipo'],
                'precio_entrada': precio_entrada,
                'take_profit': operacion.get('take_profit', 0),
                'stop_loss': operacion.get('stop_loss', 0),
                'precio_salida': precio_salida,
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
                'reason': reason,
                'pnl_no_realizado_final': operacion.get('pnl_no_realizado', 0)
            }
            
            # Registrar en log
            self.registrar_operacion(datos_operacion)
            
            # Enviar notificación
            mensaje_cierre = self.generar_mensaje_cierre(datos_operacion)
            token = self.config.get('telegram_token')
            chats = self.config.get('telegram_chat_ids', [])
            if token and chats:
                try:
                    self._enviar_telegram_simple(mensaje_cierre, token, chats)
                except Exception:
                    pass
            
            # Marcar como procesada para evitar duplicados
            self.operaciones_cerradas_registradas.add(simbolo)
            
            # Limpiar estructuras locales
            if simbolo in self.operaciones_activas:
                del self.operaciones_activas[simbolo]
            if simbolo in self.operaciones_bitget_activas:
                del self.operaciones_bitget_activas[simbolo]
            if simbolo in self.order_ids_entrada:
                del self.order_ids_entrada[simbolo]
            if simbolo in self.order_ids_sl:
                del self.order_ids_sl[simbolo]
            if simbolo in self.order_ids_tp:
                del self.order_ids_tp[simbolo]
            if simbolo in self.senales_enviadas:
                self.senales_enviadas.remove(simbolo)
            
            self.operaciones_desde_optimizacion += 1
            
            logger.info(f"📊 {simbolo} Operación {resultado} procesada - PnL: {pnl_percent:.2f}% - {reason}")
            
        except Exception as e:
            logger.error(f"❌ Error procesando cierre de {simbolo}: {e}")

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
        """Obtiene datos con configuración específica usando API de Bitget FUTUROS"""
        # Usar API de Bitget FUTUROS
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
                print(f"   ⚠️ Error obteniendo datos de BITGET FUTUROS para {simbolo}: {e}")
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
        """
        precio_cierre = datos_mercado['cierres'][-1]
        resistencia = info_canal['resistencia']
        soporte = info_canal['soporte']
        direccion_canal = info_canal['direccion']
        # Determinar tipo de ruptura
        if tipo_breakout == "BREAKOUT_LONG":
            emoji_principal = "🚀"
            tipo_texto = "RUPTURA de SOPORTE"
            nivel_roto = f"Soporte: {soporte:.8f}"
            direccion_emoji = "⬇️"
            contexto = f"Canal {direccion_canal} → Ruptura de SOPORTE"
            expectativa = "posible entrada en long si el precio reingresa al canal"
        else:  # BREAKOUT_SHORT
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
        """
        try:
            import matplotlib.font_manager as fm
            plt.rcParams['font.family'] = ['DejaVu Sans', 'Segoe UI Emoji', 'Apple Color Emoji', 'Noto Color Emoji']
            
            # Usar API de Bitget FUTUROS si está disponible
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
                sop = info_canal['pendiente_soporte'] * t + \
                     (info_canal['soporte'] - info_canal['pendiente_soporte'] * tiempos_reg[-1])
                resistencia_values.append(resist)
                soporte_values.append(sop)
            df['Resistencia'] = resistencia_values
            df['Soporte'] = soporte_values
            # Calcular Stochastic
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
            # Preparar plots
            apds = [
                mpf.make_addplot(df['Resistencia'], color='#5444ff', linestyle='--', width=2, panel=0),
                mpf.make_addplot(df['Soporte'], color="#5444ff", linestyle='--', width=2, panel=0),
            ]
            # MARCAR ZONA DE BREAKOUT con línea gruesa
            precio_breakout = datos_mercado['precio_actual']
            breakout_line = [precio_breakout] * len(df)
            if tipo_breakout == "BREAKOUT_LONG":
                color_breakout = "#D68F01"
                titulo_extra = "🚀 RUPTURA ALCISTA"
            else:
                color_breakout = '#D68F01'
                titulo_extra = "📉 RUPTURA BAJISTA"
            apds.append(mpf.make_addplot(breakout_line, color=color_breakout, linestyle='-', width=3, panel=0, alpha=0.8))
            # Stochastic
            apds.append(mpf.make_addplot(df['Stoch_K'], color='#00BFFF', width=1.5, panel=1, ylabel='Stochastic'))
            apds.append(mpf.make_addplot(df['Stoch_D'], color='#FF6347', width=1.5, panel=1))
            overbought = [80] * len(df)
            oversold = [20] * len(df)
            apds.append(mpf.make_addplot(overbought, color="#E7E4E4", linestyle='--', width=0.8, panel=1, alpha=0.5))
            apds.append(mpf.make_addplot(oversold, color="#E9E4E4", linestyle='--', width=0.8, panel=1, alpha=0.5))
            # Crear gráfico
            fig, axes = mpf.plot(df, type='candle', style='nightclouds',
                               title=f'{simbolo} | {titulo_extra} | {config_optima["timeframe"]} | ⏳ ESPERANDO REENTRY',
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
            print(f"⚠️ Error generando gráfico de breakout: {e}")
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
        # Verificar si ya hubo un breakout reciente (menos de 25 minutos)
        if simbolo in self.breakouts_detectados:
            ultimo_breakout = self.breakouts_detectados[simbolo]
            tiempo_desde_ultimo = (datetime.now() - ultimo_breakout['timestamp']).total_seconds() / 60
            if tiempo_desde_ultimo < 115:
                print(f"     ⏰ {simbolo} - Breakout detectado recientemente ({tiempo_desde_ultimo:.1f} min), omitiendo...")
                return None
        # CORREGIR LÓGICA DE DETECCIÓN DE BREAKOUT
        if direccion == "🟢 ALCISTA" and nivel_fuerza >= 2:
            if precio_cierre < soporte:  # Precio rompió hacia abajo el soporte
                print(f"     🚀 {simbolo} - BREAKOUT LONG: {precio_cierre:.8f} < Soporte: {soporte:.8f}")
                return "BREAKOUT_LONG"
        elif direccion == "🔴 BAJISTA" and nivel_fuerza >= 2:
            if precio_cierre > resistencia:  # Precio rompió hacia arriba la resistencia
                print(f"     📉 {simbolo} - BREAKOUT SHORT: {precio_cierre:.8f} > Resistencia: {resistencia:.8f}")
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
        if tiempo_desde_breakout > 120:
            print(f"     ⏰ {simbolo} - Timeout de reentry (>30 min), cancelando espera")
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
                    print(f"     ✅ {simbolo} - REENTRY LONG confirmado! Entrada en soporte con Stoch oversold")
                    if simbolo in self.breakouts_detectados:
                        del self.breakouts_detectados[simbolo]
                    return "LONG"
        elif tipo_breakout == "BREAKOUT_SHORT":
            if soporte <= precio_actual <= resistencia:
                distancia_resistencia = abs(precio_actual - resistencia)
                if distancia_resistencia <= tolerancia and stoch_k >= 70 and stoch_d >= 70:
                    print(f"     ✅ {simbolo} - REENTRY SHORT confirmado! Entrada en resistencia con Stoch overbought")
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
        """Escanea el mercado con estrategia Breakout + Reentry"""
        print(f"\n🔍 Escaneando {len(self.config.get('symbols', []))} símbolos (Estrategia: Breakout + Reentry)...")
        senales_encontradas = 0
        for simbolo in self.config.get('symbols', []):
            try:
                if simbolo in self.operaciones_activas:
                    # Verificar si es operación manual del usuario
                    es_manual = self.operaciones_activas[simbolo].get('operacion_manual_usuario', False)
                    if es_manual:
                        print(f"   👤 {simbolo} - Operación manual detectada, omitiendo...")
                    else:
                        print(f"   ⚡ {simbolo} - Operación automática activa, omitiendo...")
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
                        self.breakouts_detectados[simbolo] = {
                            'tipo': tipo_breakout,
                            'timestamp': datetime.now(),
                            'precio_breakout': precio_actual
                        }
                        print(f"     🎯 {simbolo} - Breakout registrado, esperando reingreso...")
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
        # 🛡️ PROTECCIÓN CRÍTICA: No generar señales en pares con operaciones activas
        if simbolo in self.operaciones_activas:
            # Verificar si es operación manual del usuario
            es_manual = self.operaciones_activas[simbolo].get('operacion_manual_usuario', False)
            if es_manual:
                print(f"    👤 {simbolo} - Operación manual detectada, omitiendo señal")
            else:
                print(f"    🚫 {simbolo} - Operación automática activa, omitiendo señal")
            return
        if simbolo in self.senales_enviadas:
            return
        if precio_entrada is None or tp is None or sl is None:
            print(f"    ❌ Niveles inválidos para {simbolo}, omitiendo señal")
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
        
        # Ejecutar operación automáticamente si está habilitado y tenemos cliente BITGET FUTUROS
        operacion_bitget = None  # Definir variable antes del try
        if self.ejecutar_operaciones_automaticas and self.bitget_client:
            print(f"     🤖 Ejecutando operación automática en BITGET FUTUROS...")
            try:
                operacion_bitget = ejecutar_operacion_bitget(
                    bitget_client=self.bitget_client,
                    simbolo=simbolo,
                    tipo_operacion=tipo_operacion,
                    capital_usd=None,  # SIEMPRE calcular como 3% del saldo dinámicamente
                    leverage=self.leverage_por_defecto
                )
                if operacion_bitget:
                    print(f"     ✅ Operación ejecutada en BITGET FUTUROS para {simbolo}")
                    # Enviar confirmación de ejecución
                    mensaje_confirmacion = f"""
🤖 <b>OPERACIÓN AUTOMÁTICA EJECUTADA - {simbolo}</b>
✅ <b>Status:</b> EJECUTADA EN BITGET FUTUROS
📊 <b>Tipo:</b> {tipo_operacion}
💰 <b>MARGIN USDT:</b> ${operacion_bitget.get('capital_usado', 0):.2f} (3% del saldo actual)
💰 <b>Saldo Total:</b> ${operacion_bitget.get('saldo_cuenta', 0):.2f}
💰 <b>Saldo Restante:</b> ${operacion_bitget.get('saldo_cuenta', 0) - operacion_bitget.get('capital_usado', 0):.2f}
📊 <b>Valor Nocional:</b> ${operacion_bitget.get('capital_usado', 0) * operacion_bitget.get('leverage', 1):.2f}
⚡ <b>Apalancamiento:</b> {operacion_bitget.get('leverage', self.leverage_por_defecto)}x
🎯 <b>Entrada:</b> {operacion_bitget['precio_entrada']:.8f}
🛑 <b>Stop Loss:</b> {operacion_bitget['stop_loss']:.8f}
🎯 <b>Take Profit:</b> {operacion_bitget['take_profit']:.8f}
📋 <b>ID Orden:</b> {operacion_bitget['orden_entrada'].get('orderId', 'N/A')}
🔧 <b>Sistema:</b> Cada operación usa 3% del saldo actual (saldo disminuye)
⏰ <b>Timestamp:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
                    """
                    self._enviar_telegram_simple(mensaje_confirmacion, token, chat_ids)
                    
                    # SOLO agregar a operaciones_activas si la ejecución fue exitosa
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
                        'operacion_ejecutada': True,  # Confirma ejecución exitosa
                        # NUEVOS CAMPOS PARA BITGET
                        'order_id_entrada': operacion_bitget['orden_entrada'].get('orderId'),
                        'order_id_sl': operacion_bitget['orden_sl'].get('orderId') if operacion_bitget['orden_sl'] else None,
                        'order_id_tp': operacion_bitget['orden_tp'].get('orderId') if operacion_bitget['orden_tp'] else None,
                        'capital_usado': operacion_bitget['capital_usado'],
                        'valor_nocional': operacion_bitget['capital_usado'] * operacion_bitget['leverage'],
                        'margin_usdt_real': operacion_bitget['capital_usado'],
                        'leverage_usado': operacion_bitget['leverage']
                    }
                    
                    # Guardar estado después de ejecutar operación automática exitosa
                    self.guardar_estado()
                    
                else:
                    print(f"     ❌ Error ejecutando operación en BITGET FUTUROS para {simbolo}")
                    print(f"     ⚠️  Operación NO agregada a operaciones_activas (falló ejecución)")
                    
            except Exception as e:
                print(f"     ⚠️ Error en ejecución automática: {e}")
                print(f"     ⚠️  Operación NO agregada a operaciones_activas (excepción: {e})")
        
        # SOLO agregar a operaciones_activas si NO se ejecutó operación automática o si falló
        if not operacion_bitget:
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
                'operacion_ejecutada': False  # Confirma que no se ejecutó automáticamente
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
🤖 Operación BITGET FUTUROS: {operacion_ejecutada}
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
            
            # Usar API de Bitget FUTUROS si está disponible
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
                               title=f'{simbolo} | {tipo_operacion} | {config_optima["timeframe"]} | BITGET FUTUROS + Breakout+Reentry',
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
            print(f"⚠️ Error generando gráfico: {e}")
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
        """Ejecutar análisis completo incluyendo sincronización con Bitget"""
        try:
            # 1. Sincronización con Bitget (cada ciclo)
            if self.bitget_client:
                self.sincronizar_con_bitget()
            
            # 2. Verificación y recolocación de TP/SL (cada ciclo)
            if self.bitget_client:
                self.verificar_y_recolocar_tp_sl()
            
            # 3. Reoptimización periódica
            if random.random() < 0.1:
                self.reoptimizar_periodicamente()
            
            # 4. Verificar cierres de operaciones locales
            cierres = self.verificar_cierre_operaciones()
            if cierres:
                print(f"     📊 Operaciones cerradas: {', '.join(cierres)}")
            
            # 5. Guardar estado después del análisis
            self.guardar_estado()
            
            # 6. Escanear mercado para nuevas señales
            return self.escanear_mercado()
            
        except Exception as e:
            logger.error(f"❌ Error en ejecutar_analisis: {e}")
            # Intentar guardar estado incluso en caso de error
            try:
                self.guardar_estado()
            except:
                pass
            return 0

    def mostrar_resumen_operaciones(self):
        print(f"\n📊 RESUMEN OPERACIONES:")
        print(f"   Activas: {len(self.operaciones_activas)}")
        print(f"   Esperando reentry: {len(self.esperando_reentry)}")
        print(f"   Total ejecutadas: {self.total_operaciones}")
        if self.bitget_client:
            print(f"   🤖 BITGET FUTUROS: ✅ Conectado")
            if self.ejecutar_operaciones_automaticas:
                print(f"   🤖 AUTO-TRADING: ✅ ACTIVADO (Dinero REAL)")
            else:
                print(f"   🤖 AUTO-TRADING: ❌ Solo señales")
        else:
            print(f"   🤖 BITGET FUTUROS: ❌ No configurado")
        if self.operaciones_activas:
            for simbolo, op in self.operaciones_activas.items():
                estado = "🟢 LONG" if op['tipo'] == 'LONG' else "🔴 SHORT"
                ancho_canal = op.get('ancho_canal_porcentual', 0)
                timeframe = op.get('timeframe_utilizado', 'N/A')
                velas = op.get('velas_utilizadas', 0)
                breakout = "🚀" if op.get('breakout_usado', False) else ""
                ejecutada = "🤖" if op.get('operacion_ejecutada', False) else ""
                # Marcar operaciones manuales
                manual = "👤" if op.get('operacion_manual_usuario', False) else ""
                print(f"   • {simbolo} {estado} {breakout} {ejecutada} {manual} - {timeframe} - {velas}v - Ancho: {ancho_canal:.1f}%")

    def iniciar(self):
        print("\n" + "=" * 70)
        print("🤖 BOT DE TRADING - ESTRATEGIA BREAKOUT + REENTRY")
        print("🎯 PRIORIDAD: TIMEFRAMES CORTOS (1m > 3m > 5m > 15m > 30m)")
        print("💾 PERSISTENCIA: ACTIVADA")
        print("🔄 REEVALUACIÓN: CADA 2 HORAS")
        print("🏦 INTEGRACIÓN: BITGET FUTUROS API (Dinero REAL)")
        print("=" * 70)
        print(f"💱 Símbolos: {len(self.config.get('symbols', []))} monedas")
        print(f"⏰ Timeframes: {', '.join(self.config.get('timeframes', []))}")
        print(f"🕯️ Velas: {self.config.get('velas_options', [])}")
        print(f"📏 ANCHO MÍNIMO: {self.config.get('min_channel_width_percent', 4)}%")
        print(f"🚀 Estrategia: 1) Detectar Breakout → 2) Esperar Reentry → 3) Confirmar con Stoch")
        if self.bitget_client:
            print(f"🤖 BITGET FUTUROS: ✅ API Conectada")
            print(f"⚡ Apalancamiento: {self.leverage_por_defecto}x")
            print(f"💰 MARGIN USDT: 3% del saldo actual (se recalcula para CADA operación)")
            print(f"🔧 Sistema: El saldo disminuye progresivamente con cada operación")
            if self.ejecutar_operaciones_automaticas:
                print(f"🤖 AUTO-TRADING: ✅ ACTIVADO (Operaciones REALES con dinero)")
                print("⚠️  ADVERTENCIA: TRADING AUTOMÁTICO REAL ACTIVADO")
                print("   El bot ejecutará operaciones REALES en Bitget Futures")
                print("   Cada operación usará 3% del saldo actual (el saldo disminuye)")
                print("   Usa con cuidado y solo con capital que puedas perder")
                confirmar = input("\n¿Continuar? (s/n): ").strip().lower()
                if confirmar not in ['s', 'si', 'sí', 'y', 'yes']:
                    print("❌ Operación cancelada")
                    return
            else:
                print(f"🤖 AUTO-TRADING: ❌ Solo señales (Paper Trading)")
        else:
            print(f"🤖 BITGET FUTUROS: ❌ No configurado (solo señales)")
        print("=" * 70)
        print("\n🚀 INICIANDO BOT...")
        
        # SINCRONIZACIÓN INICIAL CON BITGET
        if self.bitget_client:
            print("\n🔄 REALIZANDO SINCRONIZACIÓN INICIAL CON BITGET...")
            self.sincronizar_con_bitget()
            print("✅ Sincronización inicial completada")
        
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
# CONFIGURACIÓN CON CREDENCIALES REALES DE BITGET FUTUROS
# ---------------------------
def crear_config_completa():
    """Configuración completa con credenciales REALES de Bitget Futures y Telegram"""
    
    # CREDENCIALES REALES DE BITGET FUTUROS (tomadas de automatico.py)
    BITGET_API_KEY = 'bg_0e9c732f2ed08d90c986a7fd9a4cdedd'
    BITGET_SECRET_KEY = '52582b11761d83bce4e4475182b1510617081dd4e56051e787178a2a06a5bd3b'
    BITGET_PASSPHRASE = 'Rasputino977'
    
    # CREDENCIALES REALES DE TELEGRAM (tomadas de automatico.py)
    TELEGRAM_TOKEN = '8406173543:AAFIuYlFd3jtAF1Q6SNntUGn1PopgkZ7S0k'
    TELEGRAM_CHAT_ID = '2108159591'
    
    # Variables de entorno SOLO para Render.com
    # Estas variables solo se leen cuando el bot se ejecuta en el entorno de Render
    if 'RENDER' in os.environ:
        print("📝 Leyendo configuración desde variables de entorno (Render.com)...", file=sys.stdout)
        
        if os.environ.get('BITGET_API_KEY'):
            BITGET_API_KEY = os.environ.get('BITGET_API_KEY')
            print("📝 Usando BITGET_API_KEY desde variable de entorno", file=sys.stdout)
        if os.environ.get('BITGET_SECRET_KEY'):
            BITGET_SECRET_KEY = os.environ.get('BITGET_SECRET_KEY')
            print("📝 Usando BITGET_SECRET_KEY desde variable de entorno", file=sys.stdout)
        if os.environ.get('BITGET_PASSPHRASE'):
            BITGET_PASSPHRASE = os.environ.get('BITGET_PASSPHRASE')
            print("📝 Usando BITGET_PASSPHRASE desde variable de entorno", file=sys.stdout)
        if os.environ.get('TELEGRAM_TOKEN'):
            TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
            print("📝 Usando TELEGRAM_TOKEN desde variable de entorno", file=sys.stdout)
        if os.environ.get('TELEGRAM_CHAT_ID'):
            TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')
            print("📝 Usando TELEGRAM_CHAT_ID desde variable de entorno", file=sys.stdout)
        
        # Configuración de trading
        ejecutar_automaticas = True
        if os.environ.get('EJECUTAR_OPERACIONES_AUTOMATICAS'):
            ejecutar_automaticas = os.environ.get('EJECUTAR_OPERACIONES_AUTOMATICAS').lower() == 'true'
            print(f"📝 EJECUTAR_OPERACIONES_AUTOMATICAS: {ejecutar_automaticas}", file=sys.stdout)
        
        leverage = 20
        if os.environ.get('LEVERAGE_POR_DEFECTO'):
            leverage = int(os.environ.get('LEVERAGE_POR_DEFECTO'))
            print(f"📝 LEVERAGE_POR_DEFECTO: {leverage}", file=sys.stdout)
        
        # CAPITAL_POR_OPERACION se usa para override del porcentaje por defecto (3%)
        capital_por_operacion = 0.03  # Por defecto 3%
        if os.environ.get('CAPITAL_POR_OPERACION'):
            try:
                capital_por_operacion = float(os.environ.get('CAPITAL_POR_OPERACION'))
                print(f"📝 CAPITAL_POR_OPERACION: {capital_por_operacion}", file=sys.stdout)
            except ValueError:
                print("⚠️ CAPITAL_POR_OPERACION inválido, usando valor por defecto 3%", file=sys.stdout)
    else:
        # Modo local - valores por defecto
        ejecutar_automaticas = True
        leverage = 20
        capital_por_operacion = 0.03
    
    # Directorio actual para archivos
    directorio_actual = os.path.dirname(os.path.abspath(__file__))
    
    return {
        'min_channel_width_percent': 4.0,
        'trend_threshold_degrees': 16.0,
        'min_trend_strength_degrees': 16.0,
        'entry_margin': 0.001,
        'min_rr_ratio': 1.2,
        'scan_interval_minutes': 8,  # Valor por defecto (2 minutos)
        'timeframes': ['5m', '15m', '30m', '1h', ],
        'velas_options': [80, 100, 120, 150, 200],
        'symbols': [
            # SOLO LOS QUE SÍ FUNCIONARON EN TU LOG (65)
            'PEPEUSDT', 'WIFUSDT', 'FLOKIUSDT', 'SHIBUSDT', 'POPCATUSDT',
            'CHILLGUYUSDT', 'PNUTUSDT', 'MEWUSDT', 'FARTCOINUSDT', 'DOGEUSDT',
            'VINEUSDT', 'HIPPOUSDT', 'TRXUSDT', 'XLMUSDT', 'XRPUSDT',
            'ADAUSDT', 'ATOMUSDT', 'ETCUSDT', 'LINKUSDT', 'UNIUSDT',
            'SUSHIUSDT', 'CRVUSDT', 'SNXUSDT', 'SANDUSDT', 'MANAUSDT',
            'AXSUSDT', 'LRCUSDT', 'ARBUSDT', 'OPUSDT', 'INJUSDT',
            'FILUSDT', 'SUIUSDT', 'AAVEUSDT', 'COMPUSDT', 'ENSUSDT',
            'LDOUSDT', 'RENDERUSDT', 'POLUSDT', 'ALGOUSDT', 'QNTUSDT',
            '1INCHUSDT', 'CVCUSDT', 'STGUSDT', 'ENJUSDT', 'GALAUSDT',
            'MAGICUSDT', 'REZUSDT', 'BLURUSDT', 'HMSTRUSDT', 'BEATUSDT',
            'ZEREBROUSDT', 'ZENUSDT', 'CETUSUSDT', 'DRIFTUSDT', 'PHAUSDT',
            'API3USDT', 'ACHUSDT', 'SPELLUSDT', 'ILVUSDT', 'YGGUSDT',
            'GMXUSDT', 'C98USDT', 'BALUSDT','XMRUSDT','AAVEUSDT','DOTUSDT',
            'BNBUSDT','SOLUSDT','AVAXUSDT','VETUSDT','ICPUSDT','FILUSDT',
            'BCHUSDT','NEOUSDT','TIAUSDT','TONUSDT','NMRUSDT','TRUMPUSDT'
            
        ],
        'telegram_token': TELEGRAM_TOKEN,
        'telegram_chat_ids': [TELEGRAM_CHAT_ID],
        'auto_optimize': True,
        'min_samples_optimizacion': 30,
        'reevaluacion_horas': 24,
        'log_path': os.path.join(directorio_actual, 'operaciones_log_v23_real.csv'),
        'estado_file': os.path.join(directorio_actual, 'estado_bot_v23_real.json'),
        # CREDENCIALES REALES DE BITGET FUTUROS
        'bitget_api_key': BITGET_API_KEY,
        'bitget_api_secret': BITGET_SECRET_KEY,
        'bitget_passphrase': BITGET_PASSPHRASE,
        'ejecutar_operaciones_automaticas': ejecutar_automaticas,
        # MARGIN USDT: SIEMPRE se calcula dinámicamente como 3% del saldo de la cuenta
        'leverage_por_defecto': leverage
    }

# ---------------------------
# FLASK APP PARA EJECUCIÓN LOCAL
# ---------------------------

app = Flask(__name__)

# Crear bot con configuración completa
config = crear_config_completa()
bot = TradingBot(config)

def run_bot_loop():
    """Ejecuta el bot en un hilo separado"""
    while True:
        try:
            bot.ejecutar_analisis()
            time.sleep(bot.config.get('scan_interval_minutes', 2) * 60)
        except Exception as e:
            print(f"Error en el hilo del bot: {e}", file=sys.stderr)
            time.sleep(60)

# Iniciar hilo del bot
bot_thread = threading.Thread(target=run_bot_loop, daemon=True)
bot_thread.start()

@app.route('/')
def index():
    return "Bot Breakout + Reentry con integración Bitget FUTUROS está en línea.", 200

@app.route('/webhook', methods=['POST'])
def telegram_webhook():
    if request.is_json:
        update = request.get_json()
        print(f"Update recibido: {json.dumps(update)}", file=sys.stdout)
        return jsonify({"status": "ok"}), 200
    return jsonify({"error": "Request must be JSON"}), 400

@app.route('/health', methods=['GET'])
def health_check():
    """Endpoint para verificar el estado del bot (para Render.com monitoring)"""
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
        print(f"Error en health check: {e}", file=sys.stderr)
        return jsonify({"status": "error", "message": str(e)}), 500

# ---------------------------
# CONFIGURACIÓN DE TELEGRAM WEBHOOK PARA RENDER.COM
# ---------------------------
def setup_telegram_webhook():
    """Configura automáticamente el webhook de Telegram usando la URL de Render"""
    token = config.get('telegram_token')
    if not token:
        print("⚠️ No hay token de Telegram configurado", file=sys.stdout)
        return
    
    webhook_url = os.environ.get('WEBHOOK_URL')
    if not webhook_url:
        render_url = os.environ.get('RENDER_EXTERNAL_URL')
        if render_url:
            webhook_url = f"{render_url}/webhook"
        else:
            print("⚠️ No hay URL de webhook configurada y no se encontró RENDER_EXTERNAL_URL", file=sys.stdout)
            return
    
    try:
        print(f"🔗 Configurando webhook Telegram en: {webhook_url}", file=sys.stdout)
        # Eliminar webhook anterior
        requests.get(f"https://api.telegram.org/bot{token}/deleteWebhook", timeout=10)
        time.sleep(1)
        # Configurar nuevo webhook
        response = requests.get(f"https://api.telegram.org/bot{token}/setWebhook?url={webhook_url}", timeout=10)
        
        if response.status_code == 200:
            print("✅ Webhook de Telegram configurado correctamente", file=sys.stdout)
        else:
            print(f"❌ Error configurando webhook: {response.status_code} - {response.text}", file=sys.stderr)
    except Exception as e:
        print(f"❌ Error configurando webhook: {e}", file=sys.stderr)

# Función para ejecutar el bot directamente
def ejecutar_bot_directamente():
    """Ejecuta el bot directamente sin Flask"""
    print("="*70)
    print("🚨 BOT BREAKOUT+REENTRY COMPLETO - BITGET FUTUROS")
    print("🔧 Características completas:")
    print("   1. ✅ Estrategia Breakout+Reentry con confirmación Stochastic")
    print("   2. ✅ Gráficos profesionales con mplfinance")
    print("   3. ✅ Conexión REAL a Bitget Futures (Dinero REAL)")
    print("   4. ✅ Trading automático REAL con SL/TP")
    print("   5. ✅ Optimizador IA automático")
    print("   6. ✅ Alertas Telegram con gráficos")
    print("   7. ✅ Persistencia de estado")
    print("="*70)
    
    bot.iniciar()

if __name__ == '__main__':
    # Verificar si estamos en Render.com
    render_external_url = os.environ.get('RENDER_EXTERNAL_URL')
    render_port = os.environ.get('PORT')
    
    if render_external_url or render_port:
        # Modo Render.com
        print("🚀 Detectando entorno Render.com", file=sys.stdout)
        if render_external_url:
            print(f"   📍 URL externa: {render_external_url}", file=sys.stdout)
        if render_port:
            print(f"   🔌 Puerto: {render_port}", file=sys.stdout)
        
        # Configurar webhook de Telegram automáticamente
        setup_telegram_webhook()
        
        # Iniciar servidor Flask en el puerto de Render
        port = int(render_port) if render_port else 5000
        print(f"🌐 Iniciando servidor Flask en puerto {port}...", file=sys.stdout)
        app.run(debug=False, host='0.0.0.0', port=port)
    else:
        # Modo ejecución local (comportamiento original)
        print("🏠 Modo ejecución local detectado", file=sys.stdout)
        ejecutar_bot_directamente()
