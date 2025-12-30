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
            print("No se encontro operaciones_log.csv (optimizador)")
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
            print(f"No hay suficientes datos para optimizar (se requieren {self.min_samples}, hay {len(self.datos)})")
            return None
        mejor_score = -1e9
        mejores_param = None
        trend_values = [3, 5, 8, 10, 12, 15, 18, 20, 25, 30, 35, 40]
        strength_values = [3, 5, 8, 10, 12, 15, 18, 20, 25, 30]
        margin_values = [0.0005, 0.001, 0.0015, 0.002, 0.0025, 0.003, 0.004, 0.005, 0.008, 0.01]
        from itertools import product
        combos = list(product(trend_values, strength_values, margin_values))
        total = len(combos)
        print(f"Optimizador: probando {total} combinaciones...")
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
            print("Optimizador: mejores parametros encontrados:", mejores_param)
            try:
                with open("mejores_parametros.json", "w", encoding='utf-8') as f:
                    json.dump(mejores_param, f, indent=2)
            except Exception as e:
                print(f"Error guardando mejores_parametros.json: {e}")
        else:
            print("No se encontro una configuracion mejor")
        return mejores_param

# ---------------------------
# BITGET CLIENT - INTEGRACION COMPLETA CON API BITGET FUTUROS
# ---------------------------
class BitgetClient:
    def __init__(self, api_key, api_secret, passphrase, bot_instance=None):
        self.api_key = api_key
        self.api_secret = api_secret
        self.passphrase = passphrase
        self.base_url = "https://api.bitget.com"
        self._bot_instance = bot_instance
        logger.info(f"Cliente Bitget FUTUROS inicializado con API Key: {api_key[:10]}...")

    def _generate_signature(self, timestamp, method, request_path, body=''):
        if isinstance(body, str):
            body_str = body
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
        """Verificar que las credenciales sean validas"""
        try:
            logger.info("Verificando credenciales Bitget FUTUROS...")
            
            if not self.api_key or not self.api_secret or not self.passphrase:
                logger.error("Credenciales incompletas")
                return False
            
            accounts = self.get_account_info()
            if accounts:
                logger.info("Credenciales BITGET FUTUROS verificadas exitosamente")
                for account in accounts:
                    if account.get('marginCoin') == 'USDT':
                        available = float(account.get('available', 0))
                        logger.info(f"Balance disponible FUTUROS: {available:.2f} USDT")
                return True
            else:
                logger.error("No se pudo verificar credenciales BITGET FUTUROS")
                return False
                
        except Exception as e:
            logger.error(f"Error verificando credenciales BITGET FUTUROS: {e}")
            return False

    def get_account_info(self, product_type='USDT-FUTURES'):
        """Obtener informacion de cuenta Bitget V2 - FUTUROS"""
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
        """Obtener informacion del simbolo FUTUROS"""
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
            logger.error(f"Error obteniendo info del simbolo BITGET FUTUROS: {e}")
            return None

    def place_tpsl_order(self, symbol, hold_side, trigger_price, order_type='stop_loss', stop_loss_price=None, take_profit_price=None):
        """
        Coloca orden de Stop Loss o Take Profit en Bitget Futuros
        """
        request_path = '/api/v2/mix/order/place-pos-tpsl'
        
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
            'stopLossTriggerType': 'mark_price',
            'stopSurplusTriggerType': 'mark_price'
        }
        
        if order_type == 'stop_loss' and stop_loss_price:
            precision_sl = self.obtener_precision_adaptada(stop_loss_price, symbol)
            stop_loss_formatted = self.redondear_precio_manual(stop_loss_price, precision_sl)
            body['stopLossTriggerPrice'] = stop_loss_formatted
            logger.info(f"SL para {symbol}: precio={stop_loss_price}, precision={precision_sl}, formatted={stop_loss_formatted}")
        elif order_type == 'take_profit' and take_profit_price:
            precision_tp = self.obtener_precision_adaptada(take_profit_price, symbol)
            take_profit_formatted = self.redondear_precio_manual(take_profit_price, precision_tp)
            body['stopSurplusTriggerPrice'] = take_profit_formatted
            logger.info(f"TP para {symbol}: precio={take_profit_price}, precision={precision_tp}, formatted={take_profit_formatted}")
        
        body_json = json.dumps(body, separators=(',', ':'), ensure_ascii=False)
        headers = self._get_headers('POST', request_path, body_json)
        
        logger.info(f"Enviando orden {order_type} para {symbol}: {body}")
        
        response = requests.post(
            self.base_url + request_path,
            headers=headers,
            data=body_json,
            timeout=10
        )
        
        logger.info(f"Respuesta TP/SL BITGET: {response.status_code} - {response.text}")
        
        if response.status_code == 200:
            data = response.json()
            if data.get('code') == '00000':
                logger.info(f"{order_type.upper()} creado correctamente para {symbol}")
                return data.get('data')
            else:
                if data.get('code') == '40017':
                    logger.error(f"Error 40017 en {order_type}: {data.get('msg')}")
                    logger.error(f"Body enviado: {body}")
                if data.get('code') == '40034':
                    logger.error(f"Error 40034 en {order_type}: {data.get('msg')}")
                    logger.error(f"Body enviado: {body}")
        
        logger.error(f"Error creando {order_type}: {response.text}")
        return None

    def set_hedged_mode(self, symbol, hedged_mode=True):
        """
        Configurar el modo de posicion (hedge/unilateral) para un simbolo
        """
        try:
            request_path = '/api/v2/mix/account/set-position-mode'
            body = {
                'symbol': symbol,
                'productType': 'USDT-FUTURES',
                'marginCoin': 'USDT',
                'holdSide': 'long' if hedged_mode else 'short',
                'positionMode': 'hedged' if hedged_mode else 'single'
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
                    logger.info(f"Modo {'hedged' if hedged_mode else 'unilateral'} configurado para {symbol}")
                    return True
                    
            return False
        except Exception as e:
            logger.error(f"Error configurando modo de posicion: {e}")
            return False

    def place_order(self, symbol, side, size, order_type='market', posSide=None, is_hedged_account=False, 
                    stop_loss_price=None, take_profit_price=None):
        """
        Coloca orden de entrada MARKET en Bitget Futuros con TP/SL integrados
        """
        request_path = '/api/v2/mix/order/place-order'

        if stop_loss_price is not None:
            precision_sl = self.obtener_precision_adaptada(float(stop_loss_price), symbol)
            stop_loss_formatted = self.redondear_precio_manual(float(stop_loss_price), precision_sl, symbol)
        else:
            stop_loss_formatted = None
            
        if take_profit_price is not None:
            precision_tp = self.obtener_precision_adaptada(float(take_profit_price), symbol)
            take_profit_formatted = self.redondear_precio_manual(float(take_profit_price), precision_tp, symbol)
        else:
            take_profit_formatted = None

        if is_hedged_account:
            body = {
                "symbol": symbol,
                "productType": "USDT-FUTURES",
                "marginMode": "isolated",
                "marginCoin": "USDT",
                "side": side,
                "orderType": "market",
                "size": str(size)
            }
            logger.info(f"Orden en MODO HEDGE: side={side}, size={size} (sin posSide)")
        else:
            if not posSide:
                logger.error("En modo unilateral, posSide es obligatorio ('long' o 'short')")
                return None
            body = {
                "symbol": symbol,
                "productType": "USDT-FUTURES",
                "marginMode": "isolated",
                "marginCoin": "USDT",
                "side": side,
                "orderType": "market",
                "size": str(size),
                "posSide": posSide
            }
            logger.info(f"Orden en MODO UNILATERAL: side={side}, posSide={posSide}, size={size}")

        if stop_loss_formatted is not None:
            body["presetStopLossPrice"] = str(stop_loss_formatted)
            logger.info(f"SL integrado: presetStopLossPrice={stop_loss_formatted}")
        
        if take_profit_formatted is not None:
            body["presetStopSurplusPrice"] = str(take_profit_formatted)
            logger.info(f"TP integrado: presetStopSurplusPrice={take_profit_formatted}")

        body_json = json.dumps(body, separators=(',', ':'), ensure_ascii=False)
        headers = self._get_headers('POST', request_path, body_json)

        logger.info(f"Enviando orden con TP/SL integrados: {body}")

        response = requests.post(
            self.base_url + request_path,
            headers=headers,
            data=body_json,
            timeout=10
        )

        if response.status_code == 200:
            data = response.json()
            if data.get('code') == '00000':
                logger.info(f"Orden ejecutada ({side.upper()}) con TP/SL en modo {'HEDGE' if is_hedged_account else 'UNILATERAL'}")
                return data.get('data')
            else:
                if data.get('code') == '40774':
                    logger.error(f"Error 40774: La cuenta esta en modo {'HEDGE' if not is_hedged_account else 'UNILATERAL'} pero la orden espera el otro modo")
                    logger.error(f"Solucion: Verificar configuracion de modo de posicion en Bitget")
                if data.get('code') == '40034':
                    logger.error(f"Error 40034: {data.get('msg')}")

        logger.error(f"Error orden entrada: {response.text}")
        return None

    def obtener_precision_precio(self, symbol):
        """Obtiene la precision de precio para un simbolo especifico"""
        try:
            symbol_info = self.get_symbol_info(symbol)
            if symbol_info:
                price_scale = symbol_info.get('priceScale', 4)
                logger.info(f"{symbol}: priceScale = {price_scale}")
                return price_scale
            else:
                logger.warning(f"No se pudo obtener info de {symbol}, usando 4 decimales por defecto")
                return 4
        except Exception as e:
            logger.error(f"Error obteniendo precision de {symbol}: {e}")
            return 4

    def obtener_precision_adaptada(self, price, symbol):
        """
        Obtiene la precision adaptativa basada en el precio para evitar redondeo a cero.
        """
        try:
            price = float(price)
            
            if price < 1:
                if price < 0.00001:
                    return 12
                elif price < 0.0001:
                    return 10
                elif price < 0.001:
                    return 8
                elif price < 0.01:
                    return 7
                elif price < 0.1:
                    return 6
                elif price < 1:
                    return 5
            else:
                return 4
                
        except Exception as e:
            logger.error(f"Error calculando precision adaptativa: {e}")
            return 8

    def redondear_precio_manual(self, price, precision, symbol=None):
        """
        Redondea el precio con una precision especifica, asegurando que sea un multiplo valido.
        """
        try:
            price = float(price)
            if price == 0:
                return "0.0"
            
            if symbol:
                symbol_info = self.get_symbol_info(symbol)
                if symbol_info:
                    price_scale = symbol_info.get('priceScale', 4)
                    tick_size = 10 ** (-price_scale)
                    
                    precio_redondeado = round(price / tick_size) * tick_size
                    precio_formateado = f"{precio_redondeado:.{price_scale}f}"
                    
                    if float(precio_formateado) == 0.0 and price > 0:
                        nueva_scale = price_scale + 4
                        tick_size = 10 ** (-nueva_scale)
                        precio_redondeado = round(price / tick_size) * tick_size
                        precio_formateado = f"{precio_redondeado:.{nueva_scale}f}"
                    
                    logger.info(f"{symbol}: precio={price}, priceScale={price_scale}, tick={tick_size}, resultado={precio_formateado}")
                    return precio_formateado
            
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
        Redondea el precio al priceStep correcto del simbolo segun la API de Bitget.
        """
        try:
            precision = self.obtener_precision_precio(symbol)
            price_step = 10 ** (-precision)
            
            precio_redondeado = round(price / price_step) * price_step
            
            return float(f"{precio_redondeado:.{precision}f}")
        except Exception as e:
            logger.error(f"Error redondeando a priceStep para {symbol}: {e}")
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
            headers = self._get_headers('POST', request_path, body_json)

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
                    logger.info(f"Apalancamiento {leverage}x configurado en BITGET FUTUROS para {symbol}")
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
        """Obtiene las reglas especificas de tamano para un simbolo"""
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
            
            logger.info(f"Reglas de {symbol}:")
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
        """Ajusta el tamano de la orden segun las reglas del simbolo"""
        try:
            size_scale = reglas['size_scale']
            quantity_scale = reglas['quantity_scale']
            min_trade_num = reglas['min_trade_num']
            size_multiplier = reglas['size_multiplier']
            
            escala_actual = quantity_scale if quantity_scale > 0 else size_scale
            
            if escala_actual == 0:
                cantidad_contratos = round(cantidad_contratos)
                logger.info(f"{symbol}: ajustado a entero = {cantidad_contratos}")
            elif escala_actual == 1:
                cantidad_contratos = round(cantidad_contratos, 1)
                logger.info(f"{symbol}: ajustado a 1 decimal = {cantidad_contratos}")
            elif escala_actual == 2:
                cantidad_contratos = round(cantidad_contratos, 2)
                logger.info(f"{symbol}: ajustado a 2 decimales = {cantidad_contratos}")
            else:
                cantidad_contratos = round(cantidad_contratos, escala_actual)
                logger.info(f"{symbol}: ajustado a {escala_actual} decimales = {cantidad_contratos}")
            
            if size_multiplier > 1:
                cantidad_contratos = round(cantidad_contratos / size_multiplier) * size_multiplier
                logger.info(f"{symbol}: aplicado multiplicador {size_multiplier}x = {cantidad_contratos}")
            
            if cantidad_contratos < min_trade_num:
                cantidad_contratos = min_trade_num
                logger.info(f"{symbol}: ajustado a minimo = {min_trade_num}")
            
            if escala_actual == 0:
                if min_trade_num < 1 and min_trade_num > 0:
                    cantidad_contratos = max(1, int(round(cantidad_contratos)))
                    logger.info(f"{symbol}: caso especial - min decimal pero requiere entero = {cantidad_contratos}")
                else:
                    cantidad_contratos = int(round(cantidad_contratos))
                logger.info(f"{symbol} final: {cantidad_contratos} (entero)")
            else:
                cantidad_contratos = round(cantidad_contratos, escala_actual)
                logger.info(f"{symbol} final: {cantidad_contratos} ({escala_actual} decimales)")
            
            return cantidad_contratos
            
        except Exception as e:
            logger.error(f"Error ajustando tamano para {symbol}: {e}")
            return int(round(cantidad_contratos))
    
    def obtener_saldo_cuenta(self):
        """Obtiene el saldo actual de la cuenta Bitget FUTUROS"""
        try:
            accounts = self.get_account_info()
            if accounts:
                for account in accounts:
                    if account.get('marginCoin') == 'USDT':
                        balance_usdt = float(account.get('available', 0))
                        logger.info(f"Saldo disponible USDT: ${balance_usdt:.2f}")
                        return balance_usdt
            logger.warning("No se pudo obtener saldo de la cuenta")
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
    Ejecutar una operacion completa en BITGET FUTUROS (posicion + TP/SL)
    """
    logger.info(f"EJECUTANDO OPERACION REAL EN BITGET FUTUROS")
    logger.info(f"Simbolo: {simbolo}")
    logger.info(f"Tipo: {tipo_operacion}")
    
    try:
        saldo_cuenta = bitget_client.obtener_saldo_cuenta()
        if not saldo_cuenta or saldo_cuenta < 10:
            logger.error(f"Saldo insuficiente o no disponible: ${saldo_cuenta if saldo_cuenta else 0:.2f}")
            return None
        
        margin_usdt_objetivo = round(saldo_cuenta * 0.03, 2)
        
        logger.info(f"Saldo actual cuenta: ${saldo_cuenta:.2f}")
        logger.info(f"3% del saldo actual (MARGIN USDT objetivo): ${margin_usdt_objetivo:.2f}")
        logger.info(f"Apalancamiento: {leverage}x")
        
        symbol_info = bitget_client.get_symbol_info(simbolo)
        if not symbol_info:
            logger.error(f"No se pudo obtener info de {simbolo} en BITGET FUTUROS")
            return None
        
        hold_side = 'long' if tipo_operacion == 'LONG' else 'short'
        
        logger.info(f"Configurando apalancamiento {leverage}x para {simbolo} ({hold_side})")
        leverage_ok = bitget_client.set_leverage(simbolo, leverage, hold_side)
        
        if not leverage_ok:
            logger.warning("No se pudo configurar apalancamiento, continuando...")
        else:
            logger.info("Apalancamiento configurado exitosamente")
            
        time.sleep(1)
        
        klines = bitget_client.get_klines(simbolo, '1m', 1)
        if not klines or len(klines) == 0:
            logger.error(f"No se pudo obtener precio de {simbolo} en BITGET FUTUROS")
            return None
        
        klines.reverse()
        precio_actual = float(klines[0][4])
        
        logger.info(f"Precio actual: {precio_actual:.8f}")
        
        reglas = bitget_client.obtener_reglas_simbolo(simbolo)
        
        valor_nocional_objetivo = margin_usdt_objetivo * leverage
        logger.info(f"MARGIN USDT (3% del saldo): ${margin_usdt_objetivo:.2f}")
        logger.info(f"Valor nocional objetivo (MARGIN x LEVERAGE): ${valor_nocional_objetivo:.2f}")
        
        cantidad_contratos = valor_nocional_objetivo / precio_actual
        cantidad_contratos = bitget_client.ajustar_tamaño_orden(simbolo, cantidad_contratos, reglas)
        
        valor_nocional_real = cantidad_contratos * precio_actual
        margin_real = valor_nocional_real / leverage
        
        logger.info(f"Cantidad ajustada: {cantidad_contratos} contratos")
        logger.info(f"Valor nocional real: ${valor_nocional_real:.2f}")
        logger.info(f"MARGIN USDT real: ${margin_real:.2f}")
        
        max_margin_permitido = saldo_cuenta * 0.95
        if margin_real > max_margin_permitido:
            logger.warning(f"MARGIN USDT real (${margin_real:.2f}) excede el maximo permitido (${max_margin_permitido:.2f})")
            
            max_valor_nocional = max_margin_permitido * leverage
            cantidad_maxima = max_valor_nocional / precio_actual
            cantidad_maxima = bitget_client.ajustar_tamaño_orden(simbolo, cantidad_maxima, reglas)
            
            if cantidad_maxima < cantidad_contratos:
                cantidad_contratos = cantidad_maxima
                valor_nocional_real = cantidad_contratos * precio_actual
                margin_real = valor_nocional_real / leverage
                
                logger.info(f"Cantidad reducida al maximo permitido: {cantidad_contratos} contratos")
        
        logger.info(f"MARGIN USDT final (sera el 'capital_usado'): ${margin_real:.2f}")
        
        side = 'buy' if tipo_operacion == 'LONG' else 'sell'
        
        resultado = {
            'simbolo': simbolo,
            'tipo': tipo_operacion,
            'capital_usado': margin_real,
            'leverage': leverage,
            'precio_entrada': precio_actual,
            'orden_entrada': None,
            'orden_sl': None,
            'orden_tp': None,
            'stop_loss': None,
            'take_profit': None,
            'saldo_cuenta': saldo_cuenta
        }
        
        stop_loss_price, take_profit_price = calcular_stop_loss_take_profit(
            precio_actual, tipo_operacion, margin_usdt_objetivo, leverage, reglas, simbolo, bitget_client
        )
        
        resultado['stop_loss'] = stop_loss_price
        resultado['take_profit'] = take_profit_price
        
        logger.info(f"Stop Loss calculado: {stop_loss_price:.8f}")
        logger.info(f"Take Profit calculado: {take_profit_price:.8f}")
        
        logger.info(f"Enviando orden de entrada {tipo_operacion} para {simbolo}: side={side}, size={cantidad_contratos}")
        
        orden_entrada = bitget_client.place_order(
            symbol=simbolo,
            side=side,
            size=cantidad_contratos,
            order_type='market',
            is_hedged_account=False,
            posSide=hold_side,
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price
        )
        
        if not orden_entrada:
            logger.error(f"No se pudo ejecutar la orden de entrada para {simbolo}")
            return None
        
        resultado['orden_entrada'] = orden_entrada
        
        logger.info(f"Orden de entrada ejecutada exitosamente para {simbolo}")
        
        time.sleep(1)
        
        if stop_loss_price and take_profit_price:
            logger.info(f"Configurando Stop Loss y Take Profit para {simbolo}...")
            
            orden_sl = bitget_client.place_tpsl_order(
                symbol=simbolo,
                hold_side=hold_side,
                trigger_price=precio_actual,
                order_type='stop_loss',
                stop_loss_price=stop_loss_price
            )
            
            orden_tp = bitget_client.place_tpsl_order(
                symbol=simbolo,
                hold_side=hold_side,
                trigger_price=precio_actual,
                order_type='take_profit',
                take_profit_price=take_profit_price
            )
            
            resultado['orden_sl'] = orden_sl
            resultado['orden_tp'] = orden_tp
            
            if orden_sl:
                logger.info(f"Stop Loss configurado para {simbolo}: {orden_sl}")
            else:
                logger.warning(f"No se pudo configurar Stop Loss para {simbolo}")
            
            if orden_tp:
                logger.info(f"Take Profit configurado para {simbolo}: {orden_tp}")
            else:
                logger.warning(f"No se pudo configurar Take Profit para {simbolo}")
        
        logger.info(f"OPERACION COMPLETADA PARA {simbolo}")
        logger.info(f"Tipo: {tipo_operacion}")
        logger.info(f"Precio entrada: {precio_actual:.8f}")
        logger.info(f"Stop Loss: {stop_loss_price:.8f}")
        logger.info(f"Take Profit: {take_profit_price:.8f}")
        logger.info(f"MARGIN USDT: ${margin_real:.2f}")
        logger.info(f"Valor nocional: ${valor_nocional_real:.2f}")
        logger.info(f"Apalancamiento: {leverage}x")
        logger.info(f"Orden entrada ID: {orden_entrada.get('orderId', 'N/A')}")
        if orden_sl:
            logger.info(f"Orden SL ID: {orden_sl.get('orderId', 'N/A')}")
        if orden_tp:
            logger.info(f"Orden TP ID: {orden_tp.get('orderId', 'N/A')}")
        
        return resultado
        
    except Exception as e:
        logger.error(f"Error ejecutando operacion para {simbolo}: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return None

def calcular_stop_loss_take_profit(precio_actual, tipo_operacion, capital_usd, leverage, reglas, simbolo, bitget_client=None):
    """
    Calcular niveles de Stop Loss y Take Profit basados en el capital y apalancamiento.
    
    Args:
        precio_actual: Precio actual del simbolo
        tipo_operacion: 'LONG' o 'SHORT'
        capital_usd: Capital en USD a arriesgar (aproximadamente el margin)
        leverage: Apalancamiento
        reglas: Reglas del simbolo
        simbolo: Simbolo de trading
        bitget_client: Cliente Bitget (opcional, para precision)
    
    Returns:
        tuple: (stop_loss_price, take_profit_price)
    """
    try:
        riesgo_porcentual = 0.03  # 3% del precio como riesgo
        
        if tipo_operacion == 'LONG':
            stop_loss_price = precio_actual * (1 - riesgo_porcentual)
            beneficio_esperado = capital_usd * leverage * 0.15  # Objetivo: 15% de ganancia sobre el valor nocional
            take_profit_price = precio_actual + (beneficio_esperado / capital_usd * precio_actual * riesgo_porcentual)
        else:
            stop_loss_price = precio_actual * (1 + riesgo_porcentual)
            beneficio_esperado = capital_usd * leverage * 0.15
            take_profit_price = precio_actual - (beneficio_esperado / capital_usd * precio_actual * riesgo_porcentual)
        
        if bitget_client:
            precision = bitget_client.obtener_precision_adaptada(precio_actual, simbolo)
            stop_loss_price = float(bitget_client.redondear_precio_manual(stop_loss_price, precision, simbolo))
            take_profit_price = float(bitget_client.redondear_precio_manual(take_profit_price, precision, simbolo))
        else:
            stop_loss_price = round(stop_loss_price, 8)
            take_profit_price = round(take_profit_price, 8)
        
        return stop_loss_price, take_profit_price
        
    except Exception as e:
        print(f"Error calculando SL/TP: {e}")
        return None, None

# ---------------------------
# FUNCIONES AUXILIARES DE ANALISIS TECNICO
# ---------------------------

def calcular_regresion_lineal(x, y):
    """Calcular regresion lineal simple"""
    if len(x) != len(y) or len(x) == 0:
        return None, None
    try:
        import numpy as np
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
    except Exception as e:
        print(f"Error en regresion lineal: {e}")
        return None, None

def calcular_pearson_y_angulo(x, y):
    """Calcular coeficiente de correlacion de Pearson y angulo de tendencia"""
    if len(x) != len(y) or len(x) < 2:
        return 0, 0
    try:
        import numpy as np
        import math
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
    except Exception as e:
        print(f"Error calculando Pearson: {e}")
        return 0, 0

def calcular_r2(y_real, x, pendiente, intercepto):
    """Calcular coeficiente R2"""
    if len(y_real) != len(x):
        return 0
    try:
        import numpy as np
        y_real = np.array(y_real)
        y_pred = pendiente * np.array(x) + intercepto
        ss_res = np.sum((y_real - y_pred) ** 2)
        ss_tot = np.sum((y_real - np.mean(y_real)) ** 2)
        if ss_tot == 0:
            return 0
        return 1 - (ss_res / ss_tot)
    except Exception as e:
        print(f"Error calculando R2: {e}")
        return 0

def clasificar_fuerza_tendencia(angulo_grados):
    """Clasificar la fuerza de la tendencia"""
    angulo_abs = abs(angulo_grados)
    if angulo_abs < 3:
        return "Muy Debil", 1
    elif angulo_abs < 13:
        return "Debil", 2
    elif angulo_abs < 27:
        return "Moderada", 3
    elif angulo_abs < 45:
        return "Fuerte", 4
    else:
        return "Muy Fuerte", 5

def determinar_direccion_tendencia(angulo_grados, umbral_minimo=1):
    """Determinar la direccion de la tendencia"""
    if abs(angulo_grados) < umbral_minimo:
        return "RANGO"
    elif angulo_grados > 0:
        return "ALCISTA"
    else:
        return "BAJISTA"

def calcular_niveles_canales(datos_mercado, num_velas=80):
    """
    Calcular niveles de soporte y resistencia del canal.
    """
    cierres = datos_mercado['cierres']
    maximos = datos_mercado['maximos']
    minimos = datos_mercado['minimos']
    
    if len(cierres) < 20:
        return None
    
    tiempos = list(range(len(cierres)))
    
    slope_res, intercept_res = calcular_regresion_lineal(tiempos, maximos[-num_velas:])
    slope_supp, intercept_supp = calcular_regresion_lineal(tiempos, minimos[-num_velas:])
    
    if slope_res is None or slope_supp is None:
        return None
    
    ultimo_tiempo = len(tiempos) - 1
    
    resistencia_actual = slope_res * ultimo_tiempo + intercept_res
    soporte_actual = slope_supp * ultimo_tiempo + intercept_supp
    
    ancho_canal = resistencia_actual - soporte_actual
    ancho_canal_porcentual = (ancho_canal / soporte_actual) * 100
    
    pendiente_resistencia = slope_res * num_velas / (cierres[-1] if cierres[-1] != cierres[0] else 1)
    pendiente_soporte = slope_supp * num_velas / (cierres[-1] if cierres[-1] != cierres[0] else 1)
    
    return {
        'resistencia': resistencia_actual,
        'soporte': soporte_actual,
        'pendiente_resistencia': pendiente_resistencia,
        'pendiente_soporte': pendiente_soporte,
        'ancho_canal': ancho_canal,
        'ancho_canal_porcentual': ancho_canal_porcentual
    }

def detectar_breakout_reentry(precio_actual, info_canal, tipo_operacion, precio_breakout=None):
    """
    Detectar si el precio ha hecho breakout o reentry del canal.
    
    Args:
        precio_actual: Precio actual del activo
        info_canal: Diccionario con niveles del canal
        tipo_operacion: 'LONG' o 'SHORT'
        precio_breakout: Precio donde empezo el breakout (para reentry)
    
    Returns:
        dict: Informacion del breakout/reentry o None
    """
    if not info_canal:
        return None
    
    try:
        resistencia = info_canal['resistencia']
        soporte = info_canal['soporte']
        precio_actual = float(precio_actual)
        
        distancia_resistencia = resistencia - precio_actual
        distancia_soporte = precio_actual - soporte
        
        margen_breakout = resistencia * 0.002  # 0.2% por encima de resistencia
        margen_reentry = resistencia * 0.005   # 5% de penetracion maxima para reentry
        
        margen_breakout_short = soporte * 0.002  # 0.2% por debajo de soporte
        
        if tipo_operacion == 'LONG':
            if precio_actual > resistencia + margen_breakout:
                return {
                    'tipo': 'breakout',
                    'precio_breakout': precio_actual,
                    'distancia_breakout': distancia_resistencia,
                    'fase': 'breakout_confirmado'
                }
            
            elif precio_breakout and precio_actual > resistencia:
                penetracion = (precio_actual - resistencia) / resistencia * 100
                if penetracion < 5:
                    return {
                        'tipo': 'reentry',
                        'precio_breakout': precio_breakout,
                        'distancia_breakout': distancia_resistencia,
                        'fase': 'reentry_validado'
                    }
        
        else:
            if precio_actual < soporte - margen_breakout_short:
                return {
                    'tipo': 'breakout',
                    'precio_breakout': precio_actual,
                    'distancia_breakout': distancia_soporte,
                    'fase': 'breakout_confirmado'
                }
            
            elif precio_breakout and precio_actual < soporte:
                penetracion = (soporte - precio_actual) / soporte * 100
                if penetracion < 5:
                    return {
                        'tipo': 'reentry',
                        'precio_breakout': precio_breakout,
                        'distancia_breakout': distancia_soporte,
                        'fase': 'reentry_validado'
                    }
        
        return None
        
    except Exception as e:
        print(f"Error detectando breakout/reentry: {e}")
        return None

def calcular_nivel_senales(datos_mercado, num_velas=80, trend_threshold=16, min_strength=16, entry_margin=0.001):
    """
    Calcular el nivel de senales de trading.
    
    Returns:
        dict: Nivel de senales o None
    """
    try:
        cierres = datos_mercado['cierres']
        if len(cierres) < 50:
            return None
        
        precios_recientes = cierres[-num_velas:]
        tiempos = list(range(len(precios_recientes)))
        
        slope, intercept = calcular_regresion_lineal(tiempos, precios_recientes)
        if slope is None:
            return None
        
        precio_actual = cierres[-1]
        
        pearson, angulo = calcular_pearson_y_angulo(tiempos, precios_recientes)
        r2 = calcular_r2(precios_recientes, tiempos, slope, intercept)
        
        fuerza_texto, nivel_fuerza = clasificar_fuerza_tendencia(angulo)
        direccion = determinar_direccion_tendencia(angulo)
        
        datos_canal = calcular_niveles_canales(datos_mercado, num_velas)
        
        if datos_canal:
            ancho_pct = datos_canal['ancho_canal_porcentual']
            resistencia = datos_canal['resistencia']
            soporte = datos_canal['soporte']
        else:
            return None
        
        stoch_k, stoch_d = datos_mercado.get('stoch_k', 50), datos_mercado.get('stoch_d', 50)
        
        nivel = {
            'pearson': pearson,
            'angulo_tendencia': angulo,
            'r2_score': r2,
            'coeficiente_pearson': pearson,
            'ancho_canal': datos_canal['ancho_canal'],
            'ancho_canal_porcentual': ancho_pct,
            'nivel_fuerza': nivel_fuerza,
            'fuerza_texto': fuerza_texto,
            'direccion': direccion,
            'pendiente_resistencia': datos_canal['pendiente_resistencia'],
            'pendiente_soporte': datos_canal['pendiente_soporte'],
            'resistencia': resistencia,
            'soporte': soporte,
            'stoch_k': stoch_k,
            'stoch_d': stoch_d
        }
        
        return nivel
        
    except Exception as e:
        print(f"Error calculando nivel de senales: {e}")
        return None

# ---------------------------
# CLASE PRINCIPAL DEL BOT DE TRADING
# ---------------------------
class TradingBot:
    def __init__(self, config):
        self.config = config
        self.archivo_log = config.get('log_path', 'operaciones_log.csv')
        self.estado_file = config.get('estado_file', 'estado_bot.json')
        
        self.operaciones_activas = {}
        self.senales_enviadas = set()
        self.breakout_history = {}
        self.breakouts_detectados = {}
        self.esperando_reentry = {}
        self.total_operaciones = 0
        self.operaciones_desde_optimizacion = 0
        self.ultima_optimizacion = datetime.now()
        
        self.bitget_client = None
        if config.get('bitget_api_key') and config.get('bitget_api_secret') and config.get('bitget_passphrase'):
            self.bitget_client = BitgetClient(
                config['bitget_api_key'],
                config['bitget_api_secret'],
                config['bitget_passphrase'],
                bot_instance=self
            )
        
        self.ejecutar_operaciones_automaticas = config.get('ejecutar_operaciones_automaticas', False)
        self.leverage_por_defecto = config.get('leverage_por_defecto', 20)
        
        self.config_optima_por_simbolo = {}
        self.cargar_estado()
        self.inicializar_log()
        
        print(f"Bot inicializado con {len(config.get('symbols', []))} simbolos")
        print(f"Bitget Client: {'Conectado' if self.bitget_client else 'No configurado'}")
        print(f"Auto-trading: {'Activado' if self.ejecutar_operaciones_automaticas else 'Desactivado'}")
    
    def cargar_estado(self):
        if os.path.exists(self.estado_file):
            try:
                with open(self.estado_file, 'r', encoding='utf-8') as f:
                    estado = json.load(f)
                    self.operaciones_activas = estado.get('operaciones_activas', {})
                    self.breakout_history = estado.get('breakout_history', {})
                    self.breakouts_detectados = estado.get('breakouts_detectados', {})
                    self.esperando_reentry = estado.get('esperando_reentry', {})
                    self.senales_enviadas = set(estado.get('senales_enviadas', []))
                    self.total_operaciones = estado.get('total_operaciones', 0)
                    self.operaciones_desde_optimizacion = estado.get('operaciones_desde_optimizacion', 0)
                    
                    if estado.get('ultima_optimizacion'):
                        self.ultima_optimizacion = datetime.fromisoformat(estado['ultima_optimizacion'])
                    
                    if estado.get('config_optima_por_simbolo'):
                        self.config_optima_por_simbolo = estado['config_optima_por_simbolo']
                        
                print(f"Estado cargado: {len(self.operaciones_activas)} operaciones activas")
            except Exception as e:
                print(f"Error cargando estado: {e}")
    
    def guardar_estado(self):
        try:
            estado = {
                'operaciones_activas': self.operaciones_activas,
                'breakout_history': {k: v.isoformat() if isinstance(v, datetime) else v 
                                     for k, v in self.breakout_history.items()},
                'breakouts_detectados': {k: {**v, 'timestamp': v['timestamp'].isoformat() if isinstance(v['timestamp'], datetime) else v['timestamp']} 
                                        for k, v in self.breakouts_detectados.items()},
                'esperando_reentry': {k: {**v, 'timestamp': v['timestamp'].isoformat() if isinstance(v['timestamp'], datetime) else v['timestamp']} 
                                      for k, v in self.esperando_reentry.items()},
                'senales_enviadas': list(self.senales_enviadas),
                'total_operaciones': self.total_operaciones,
                'operaciones_desde_optimizacion': self.operaciones_desde_optimizacion,
                'ultima_optimizacion': self.ultima_optimizacion.isoformat(),
                'config_optima_por_simbolo': self.config_optima_por_simbolo
            }
            
            with open(self.estado_file, 'w', encoding='utf-8') as f:
                json.dump(estado, f, indent=2, default=str)
                
        except Exception as e:
            print(f"Error guardando estado: {e}")
    
    def obtener_datos_mercado(self, simbolo, timeframe='5m', num_velas=100):
        """Obtener datos de mercado para un simbolo"""
        try:
            if self.bitget_client:
                klines = self.bitget_client.get_klines(simbolo, timeframe, num_velas)
                if klines:
                    cierres = [float(k[4]) for k in klines]
                    maximos = [float(k[2]) for k in klines]
                    minimos = [float(k[3]) for k in klines]
                    
                    stoch_k, stoch_d = self.calcular_stochastic({
                        'cierres': cierres,
                        'maximos': maximos,
                        'minimos': minimos
                    })
                    
                    return {
                        'cierres': cierres,
                        'maximos': maximos,
                        'minimos': minimos,
                        'precio_actual': cierres[-1] if cierres else None,
                        'stoch_k': stoch_k,
                        'stoch_d': stoch_d
                    }
            
            url = "https://api.binance.com/api/v3/klines"
            params = {
                'symbol': simbolo,
                'interval': timeframe,
                'limit': num_velas
            }
            respuesta = requests.get(url, params=params, timeout=10)
            
            if respuesta.status_code == 200:
                klines = respuesta.json()
                cierres = [float(k[4]) for k in klines]
                maximos = [float(k[2]) for k in klines]
                minimos = [float(k[3]) for k in klines]
                
                stoch_k, stoch_d = self.calcular_stochastic({
                    'cierres': cierres,
                    'maximos': maximos,
                    'minimos': minimos
                })
                
                return {
                    'cierres': cierres,
                    'maximos': maximos,
                    'minimos': minimos,
                    'precio_actual': cierres[-1] if cierres else None,
                    'stoch_k': stoch_k,
                    'stoch_d': stoch_d
                }
            return None
            
        except Exception as e:
            print(f"Error obteniendo datos de mercado para {simbolo}: {e}")
            return None
    
    def obtener_datos_mercado_config(self, simbolo, timeframe, num_velas):
        """Obtener datos de mercado usando la configuracion especifica"""
        return self.obtener_datos_mercado(simbolo, timeframe, num_velas)
    
    def sincronizar_con_bitget(self):
        """Sincronizar estado con posiciones reales en Bitget"""
        if not self.bitget_client:
            return
        
        try:
            posiciones = self.bitget_client.get_positions()
            if posiciones is None:
                return
            
            posiciones_bitget = {}
            for pos in posiciones:
                symbol = pos.get('symbol')
                if symbol:
                    hold_side = pos.get('holdSide', '')
                    if hold_side == 'long':
                        posiciones_bitget[symbol] = 'LONG'
                    elif hold_side == 'short':
                        posiciones_bitget[symbol] = 'SHORT'
            
            operaciones_a_cerrar = []
            for simbolo, operacion in self.operaciones_activas.items():
                if simbolo in posiciones_bitget:
                    posicion_actual = posiciones_bitget[simbolo]
                    if operacion['tipo'] != posicion_actual:
                        operaciones_a_cerrar.append(simbolo)
            
            if operaciones_a_cerrar:
                print(f"Sincronizando con Bitget: {len(operaciones_a_cerrar)} operaciones requieren cierre")
                for simbolo in operaciones_a_cerrar:
                    del self.operaciones_activas[simbolo]
                    if simbolo in self.senales_enviadas:
                        self.senales_enviadas.remove(simbolo)
            
            posiciones_activas_bitget = set(posiciones_bitget.keys())
            operaciones_local = set(self.operaciones_activas.keys())
            
            for simbolo in posiciones_activas_bitget - operaciones_local:
                print(f"Operacion detectada en Bitget pero no local: {simbolo}")
                self.verificar_y_recolocar_tp_sl()
            
        except Exception as e:
            print(f"Error sincronizando con Bitget: {e}")
    
    def verificar_y_recolocar_tp_sl(self):
        """Verificar y recolocar ordenes TP/SL si es necesario"""
        if not self.bitget_client:
            return
        
        for simbolo, operacion in list(self.operaciones_activas.items()):
            try:
                posiciones = self.bitget_client.get_positions(simbolo)
                if not posiciones:
                    del self.operaciones_activas[simbolo]
                    if simbolo in self.senales_enviadas:
                        self.senales_enviadas.remove(simbolo)
                    continue
                
                pos = posiciones[0]
                hold_side = pos.get('holdSide', '')
                
                posiciones_abierta = float(pos.get('total', 0))
                
                if posiciones_abierta <= 0:
                    del self.operaciones_activas[simbolo]
                    if simbolo in self.senales_enviadas:
                        self.senales_enviadas.remove(simbolo)
                    continue
                
                tiene_tp_sl = all(operacion.get(k) for k in ['order_id_sl', 'order_id_tp'])
                
                if not tiene_tp_sl and self.bitget_client:
                    try:
                        klines = self.bitget_client.get_klines(simbolo, '1m', 1)
                        if klines:
                            precio_actual = float(klines[0][4])
                            
                            stop_loss = operacion.get('stop_loss')
                            take_profit = operacion.get('take_profit')
                            
                            orden_sl = self.bitget_client.place_tpsl_order(
                                symbol=simbolo,
                                hold_side=hold_side,
                                trigger_price=precio_actual,
                                order_type='stop_loss',
                                stop_loss_price=stop_loss
                            )
                            
                            orden_tp = self.bitget_client.place_tpsl_order(
                                symbol=simbolo,
                                hold_side=hold_side,
                                trigger_price=precio_actual,
                                order_type='take_profit',
                                take_profit_price=take_profit
                            )
                            
                            if orden_sl:
                                operacion['order_id_sl'] = orden_sl.get('orderId')
                            if orden_tp:
                                operacion['order_id_tp'] = orden_tp.get('orderId')
                            
                            self.guardar_estado()
                            
                    except Exception as e:
                        print(f"Error recolocando TP/SL para {simbolo}: {e}")
                        
            except Exception as e:
                print(f"Error verificando TP/SL para {simbolo}: {e}")
    
    def mover_stop_loss_breakeven(self):
        """Mover stop loss a punto de equilibrio cuando el precio ha avanzado suficientemente"""
        if not self.bitget_client:
            return
        
        for simbolo, operacion in list(self.operaciones_activas.items()):
            try:
                klines = self.bitget_client.get_klines(simbolo, '1m', 1)
                if not klines:
                    continue
                
                precio_actual = float(klines[0][4])
                precio_entrada = operacion['precio_entrada']
                tipo = operacion['tipo']
                stop_loss_actual = operacion.get('stop_loss')
                
                if tipo == "LONG":
                    precio_breakeven = precio_entrada * 1.003
                    precio_avanzado = precio_entrada * 1.008
                    if precio_actual >= precio_avanzado and stop_loss_actual < precio_breakeven:
                        nuevo_sl = precio_breakeven
                        try:
                            orden_sl = self.bitget_client.place_tpsl_order(
                                symbol=simbolo,
                                hold_side='long',
                                trigger_price=precio_actual,
                                order_type='stop_loss',
                                stop_loss_price=nuevo_sl
                            )
                            if orden_sl:
                                operacion['stop_loss'] = nuevo_sl
                                operacion['order_id_sl'] = orden_sl.get('orderId')
                                print(f"Stop Loss movido a breakeven para {simbolo}")
                                self.guardar_estado()
                        except Exception as e:
                            print(f"Error moviendo SL a breakeven para {simbolo}: {e}")
                
                else:
                    precio_breakeven = precio_entrada * 0.997
                    precio_avanzado = precio_entrada * 0.992
                    if precio_actual <= precio_avanzado and stop_loss_actual > precio_breakeven:
                        nuevo_sl = precio_breakeven
                        try:
                            orden_sl = self.bitget_client.place_tpsl_order(
                                symbol=simbolo,
                                hold_side='short',
                                trigger_price=precio_actual,
                                order_type='stop_loss',
                                stop_loss_price=nuevo_sl
                            )
                            if orden_sl:
                                operacion['stop_loss'] = nuevo_sl
                                operacion['order_id_sl'] = orden_sl.get('orderId')
                                print(f"Stop Loss movido a breakeven para {simbolo}")
                                self.guardar_estado()
                        except Exception as e:
                            print(f"Error moviendo SL a breakeven para {simbolo}: {e}")
                            
            except Exception as e:
                print(f"Error en mover_stop_loss_breakeven para {simbolo}: {e}")
    
    def detectar_breakout(self, simbolo, datos_mercado, timeframe, num_velas):
        """Detectar breakout/reentry para un simbolo"""
        try:
            if not datos_mercado or not datos_mercado.get('precio_actual'):
                return None, None, None
            
            precio_actual = datos_mercado['precio_actual']
            nivel = calcular_nivel_senales(
                datos_mercado,
                num_velas=num_velas,
                trend_threshold=self.config.get('trend_threshold_degrees', 16),
                min_strength=self.config.get('min_trend_strength_degrees', 16),
                entry_margin=self.config.get('entry_margin', 0.001)
            )
            
            if not nivel:
                return None, None, None
            
            angulo = nivel['angulo_tendencia']
            pearson = nivel['coeficiente_pearson']
            r2 = nivel['r2_score']
            ancho_canal = nivel['ancho_canal_porcentual']
            direccion = nivel['direccion']
            
            if abs(angulo) < self.config.get('trend_threshold_degrees', 16):
                return None, None, None
            
            if abs(pearson) < 0.4:
                return None, None, None
            
            if r2 < 0.4:
                return None, None, None
            
            min_ancho = self.config.get('min_channel_width_percent', 4)
            if ancho_canal < min_ancho:
                return None, None, None
            
            if direccion == "ALCISTA":
                tipo_operacion = "LONG"
            elif direccion == "BAJISTA":
                tipo_operacion = "SHORT"
            else:
                return None, None, None
            
            stoch_k = nivel['stoch_k']
            stoch_d = nivel['stoch_d']
            
            if tipo_operacion == "LONG":
                if stoch_k > 50:
                    return None, None, None
            else:
                if stoch_k < 50:
                    return None, None, None
            
            nivel_fuerza = nivel['nivel_fuerza']
            if nivel_fuerza < 2:
                return None, None, None
            
            resistencia = nivel['resistencia']
            soporte = nivel['soporte']
            
            margen_entry = self.config.get('entry_margin', 0.001)
            
            if tipo_operacion == "LONG":
                entrada_candidate = soporte
                sl_candidate = soporte * (1 - 0.03)
                tp_candidate = resistencia * (1 + 0.03)
            else:
                entrada_candidate = resistencia
                sl_candidate = resistencia * (1 + 0.03)
                tp_candidate = soporte * (1 - 0.03)
            
            precio_entrada = entrada_candidate
            sl = sl_candidate
            tp = tp_candidate
            
            riesgo = abs(precio_entrada - sl)
            beneficio = abs(tp - precio_entrada)
            ratio_rr = beneficio / riesgo if riesgo > 0 else 0
            
            min_rr = self.config.get('min_rr_ratio', 1.2)
            if ratio_rr < min_rr:
                return None, None, None
            
            return tipo_operacion, {'precio_entrada': precio_entrada, 'stop_loss': sl, 'take_profit': tp, 'ratio_rr': ratio_rr}, nivel
            
        except Exception as e:
            print(f"Error detectando breakout para {simbolo}: {e}")
            return None, None, None
    
    def calcular_nivel_senales(self, datos_mercado, num_velas=80, trend_threshold=16, min_strength=16, entry_margin=0.001):
        """Wrapper para calcular nivel de senales"""
        return calcular_nivel_senales(datos_mercado, num_velas, trend_threshold, min_strength, entry_margin)
    
    def escanear_simbolo(self, simbolo):
        """Escanear un solo simbolo en busca de oportunidades"""
        mejor_config = None
        
        timeframes = self.config.get('timeframes', ['5m', '15m', '30m', '1h'])
        velas_options = self.config.get('velas_options', [80, 100, 120, 150, 200])
        
        if simbolo in self.config_optima_por_simbolo:
            mejor_config = self.config_optima_por_simbolo[simbolo]
        else:
            mejor_score = -1e9
            for tf in timeframes:
                for num_velas in velas_options:
                    datos = self.obtener_datos_mercado(simbolo, tf, num_velas)
                    if not datos:
                        continue
                    
                    tipo_operacion, niveles, nivel = self.detectar_breakout(simbolo, datos, tf, num_velas)
                    if not tipo_operacion:
                        continue
                    
                    score = abs(nivel['angulo_tendencia']) * abs(nivel['coeficiente_pearson']) * nivel['r2_score'] * nivel['nivel_fuerza']
                    if nivel['ancho_canal_porcentual'] > 8:
                        score *= 1.2
                    
                    if score > mejor_score:
                        mejor_score = score
                        mejor_config = {
                            'timeframe': tf,
                            'num_velas': num_velas,
                            'score': score,
                            'tipo_operacion': tipo_operacion,
                            'niveles': niveles,
                            'nivel_info': nivel
                        }
                    
                    if mejor_config:
                        self.config_optima_por_simbolo[simbolo] = mejor_config
        
        if mejor_config:
            datos = self.obtener_datos_mercado(simbolo, mejor_config['timeframe'], mejor_config['num_velas'])
            if datos:
                breakout_info = None
                
                tipo_operacion = mejor_config['tipo_operacion']
                niveles = mejor_config['niveles']
                nivel_info = mejor_config['nivel_info']
                
                if simbolo in self.breakouts_detectados:
                    info = self.breakouts_detectados[simbolo]
                    if info['tipo'] == tipo_operacion:
                        precio_breakout = info['precio_breakout']
                        datos['precio_actual'] = datos['cierres'][-1]
                        breakout_result = detectar_breakout_reentry(datos['precio_actual'], nivel_info, tipo_operacion, precio_breakout)
                        
                        if breakout_result and breakout_result['tipo'] == 'reentry':
                            breakout_info = {
                                'timestamp': datetime.now(),
                                'precio_breakout': precio_breakout,
                                'tipo': 'reentry',
                                'fase': 'reentry_validado'
                            }
                            
                            del self.breakouts_detectados[simbolo]
                            self.esperando_reentry[simbolo] = {
                                'tipo': tipo_operacion,
                                'timestamp': datetime.now(),
                                'precio_breakout': precio_breakout,
                                'nivel_info': nivel_info,
                                'niveles': niveles
                            }
                            
                            print(f"   -> {simbolo} {tipo_operacion}: REENTRY VALIDADO - Esperando 15 minutos")
                
                if simbolo in self.esperando_reentry:
                    info = self.esperando_reentry[simbolo]
                    tiempo_espera = (datetime.now() - info['timestamp']).total_seconds() / 60
                    
                    if tiempo_espera >= 15:
                        datos['precio_actual'] = datos['cierres'][-1]
                        stoch_k, stoch_d = self.calcular_stochastic(datos)
                        
                        if tipo_operacion == "LONG" and stoch_k < 30:
                            self.generar_senal_operacion(simbolo, tipo_operacion, niveles['precio_entrada'], niveles['take_profit'], niveles['stop_loss'], nivel_info, datos, mejor_config, breakout_info)
                            return 1
                        elif tipo_operacion == "SHORT" and stoch_k > 70:
                            self.generar_senal_operacion(simbolo, tipo_operacion, niveles['precio_entrada'], niveles['take_profit'], niveles['stop_loss'], nivel_info, datos, mejor_config, breakout_info)
                            return 1
                    else:
                        pass
                else:
                    datos['precio_actual'] = datos['cierres'][-1]
                    breakout_result = detectar_breakout_reentry(datos['precio_actual'], nivel_info, tipo_operacion)
                    
                    if breakout_result and breakout_result['tipo'] == 'breakout':
                        self.breakouts_detectados[simbolo] = {
                            'timestamp': datetime.now(),
                            'precio_breakout': datos['precio_actual'],
                            'tipo': tipo_operacion,
                            'fase': 'breakout_confirmado'
                        }
                        print(f"   -> {simbolo} {tipo_operacion}: BREAKOUT CONFIRMADO - Esperando reentry")
                        return 0
        
        return 0
    
    def escanear_mercado(self):
        """Escanear todos los simbolos en busca de oportunidades"""
        symbols = self.config.get('symbols', [])
        senales_encontradas = 0
        
        print(f"\n Escaneando {len(symbols)} simbolos...")
        
        for simbolo in symbols:
            try:
                senales = self.escanear_simbolo(simbolo)
                senales_encontradas += senales
            except Exception as e:
                print(f"Error escaneando {simbolo}: {e}")
                continue
        
        if self.esperando_reentry:
            print(f"\nEsperando reingreso en {len(self.esperando_reentry)} simbolos:")
            for simbolo, info in self.esperando_reentry.items():
                tiempo_espera = (datetime.now() - info['timestamp']).total_seconds() / 60
                print(f"   {simbolo} - {info['tipo']} - Esperando {tiempo_espera:.1f} min")
        if self.breakouts_detectados:
            print(f"\nBreakouts detectados recientemente:")
            for simbolo, info in self.breakouts_detectados.items():
                tiempo_desde_deteccion = (datetime.now() - info['timestamp']).total_seconds() / 60
                print(f"   {simbolo} - {info['tipo']} - Hace {tiempo_desde_deteccion:.1f} min")
        if senales_encontradas > 0:
            print(f"Se encontraron {senales_encontradas} senales de trading")
        else:
            print("No se encontraron senales en este ciclo")
        return senales_encontradas

    def generar_senal_operacion(self, simbolo, tipo_operacion, precio_entrada, tp, sl,
                            info_canal, datos_mercado, config_optima, breakout_info=None):
        """Generar y enviar senal de operacion con info de breakout"""
        if simbolo in self.operaciones_activas:
            es_manual = self.operaciones_activas[simbolo].get('operacion_manual_usuario', False)
            if es_manual:
                print(f"    {simbolo} - Operacion manual detectada, omitiendo senal")
            else:
                print(f"    {simbolo} - Operacion automatica activa, omitiendo senal")
            return
        if simbolo in self.senales_enviadas:
            return
        if precio_entrada is None or tp is None or sl is None:
            print(f"    Niveles invalidos para {simbolo}, omitiendo senal")
            return
        riesgo = abs(precio_entrada - sl)
        beneficio = abs(tp - precio_entrada)
        ratio_rr = beneficio / riesgo if riesgo > 0 else 0
        sl_percent = abs((sl - precio_entrada) / precio_entrada) * 100
        tp_percent = abs((tp - precio_entrada) / precio_entrada) * 100
        stoch_estado = "SOBREVENTA" if tipo_operacion == "LONG" else "SOBRECOMPRA"
        breakout_texto = ""
        if breakout_info:
            tiempo_breakout = (datetime.now() - breakout_info['timestamp']).total_seconds() / 60
            breakout_texto = f"""
BREAKOUT + REENTRY DETECTADO:
Tiempo desde breakout: {tiempo_breakout:.1f} minutos
Precio breakout: {breakout_info['precio_breakout']:.8f}
"""
        mensaje = f"""
SEÑAL DE {tipo_operacion} - {simbolo}
{breakout_texto}
Configuracion optima:
Timeframe: {config_optima['timeframe']}
Velas: {config_optima['num_velas']}
Ancho Canal: {info_canal['ancho_canal_porcentual']:.1f}%
Precio Actual: {datos_mercado['precio_actual']:.8f}
Entrada: {precio_entrada:.8f}
Stop Loss: {sl:.8f}
Take Profit: {tp:.8f}
Ratio R/B: {ratio_rr:.2f}:1
SL: {sl_percent:.2f}%
TP: {tp_percent:.2f}%
Riesgo: {riesgo:.8f}
Beneficio Objetivo: {beneficio:.8f}
Tendencia: {info_canal['direccion']}
Fuerza: {info_canal['fuerza_texto']}
Angulo: {info_canal['angulo_tendencia']:.1f}
Pearson: {info_canal['coeficiente_pearson']:.3f}
R² Score: {info_canal['r2_score']:.3f}
Estocastico: {stoch_estado}
Stoch K: {info_canal['stoch_k']:.1f}
Stoch D: {info_canal['stoch_d']:.1f}
Hora: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
Estrategia: BREAKOUT + REENTRY con confirmacion Estocastico
        """
        token = self.config.get('telegram_token')
        chat_ids = self.config.get('telegram_chat_ids', [])
        if token and chat_ids:
            try:
                print(f"     Generando grafico para {simbolo}...")
                buf = self.generar_grafico_profesional(simbolo, info_canal, datos_mercado, 
                                                      precio_entrada, tp, sl, tipo_operacion)
                if buf:
                    print(f"     Enviando grafico por Telegram...")
                    self.enviar_grafico_telegram(buf, token, chat_ids)
                    time.sleep(1)
                self._enviar_telegram_simple(mensaje, token, chat_ids)
                print(f"     Senal {tipo_operacion} para {simbolo} enviada")
            except Exception as e:
                print(f"     Error enviando senal: {e}")
        
        operacion_bitget = None
        if self.ejecutar_operaciones_automaticas and self.bitget_client:
            print(f"     Ejecutando operacion automatica en BITGET FUTUROS...")
            try:
                operacion_bitget = ejecutar_operacion_bitget(
                    bitget_client=self.bitget_client,
                    simbolo=simbolo,
                    tipo_operacion=tipo_operacion,
                    capital_usd=None,
                    leverage=self.leverage_por_defecto
                )
                if operacion_bitget:
                    print(f"     Operacion ejecutada en BITGET FUTUROS para {simbolo}")
                    mensaje_confirmacion = f"""
OPERACION AUTOMATICA EJECUTADA - {simbolo}
Status: EJECUTADA EN BITGET FUTUROS
Tipo: {tipo_operacion}
MARGIN USDT: ${operacion_bitget.get('capital_usado', 0):.2f} (3% del saldo actual)
Saldo Total: ${operacion_bitget.get('saldo_cuenta', 0):.2f}
Saldo Restante: ${operacion_bitget.get('saldo_cuenta', 0) - operacion_bitget.get('capital_usado', 0):.2f}
Valor Nocional: ${operacion_bitget.get('capital_usado', 0) * operacion_bitget.get('leverage', 1):.2f}
Apalancamiento: {operacion_bitget.get('leverage', self.leverage_por_defecto)}x
Entrada: {operacion_bitget['precio_entrada']:.8f}
Stop Loss: {operacion_bitget['stop_loss']:.8f}
Take Profit: {operacion_bitget['take_profit']:.8f}
ID Orden: {operacion_bitget['orden_entrada'].get('orderId', 'N/A')}
Sistema: Cada operacion usa 3% del saldo actual (saldo disminuye)
Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
                    """
                    self._enviar_telegram_simple(mensaje_confirmacion, token, chat_ids)
                    
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
                        'operacion_ejecutada': True,
                        'order_id_entrada': operacion_bitget['orden_entrada'].get('orderId'),
                        'order_id_sl': operacion_bitget['orden_sl'].get('orderId') if operacion_bitget['orden_sl'] else None,
                        'order_id_tp': operacion_bitget['orden_tp'].get('orderId') if operacion_bitget['orden_tp'] else None,
                        'capital_usado': operacion_bitget['capital_usado'],
                        'valor_nocional': operacion_bitget['capital_usado'] * operacion_bitget['leverage'],
                        'margin_usdt_real': operacion_bitget['capital_usado'],
                        'leverage_usado': operacion_bitget['leverage']
                    }
                    
                    self.guardar_estado()
                    
                else:
                    print(f"     Error ejecutando operacion en BITGET FUTUROS para {simbolo}")
                    
            except Exception as e:
                print(f"     Error en ejecucion automatica: {e}")
        
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
                'operacion_ejecutada': False
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
                print(f"     {simbolo} Operacion {resultado} - PnL: {pnl_percent:.2f}%")
        return operaciones_cerradas

    def generar_mensaje_cierre(self, datos_operacion):
        emoji = "🟢" if datos_operacion['resultado'] == "TP" else "🔴"
        color_emoji = "✅" if datos_operacion['resultado'] == "TP" else "❌"
        if datos_operacion['tipo'] == 'LONG':
            pnl_absoluto = datos_operacion['precio_salida'] - datos_operacion['precio_entrada']
        else:
            pnl_absoluto = datos_operacion['precio_entrada'] - datos_operacion['precio_salida']
        breakout_usado = "Si" if datos_operacion.get('breakout_usado', False) else "No"
        operacion_ejecutada = "Si" if datos_operacion.get('operacion_ejecutada', False) else "No"
        mensaje = f"""
{emoji} OPERACION CERRADA - {datos_operacion['symbol']}
{color_emoji} RESULTADO: {datos_operacion['resultado']}
Tipo: {datos_operacion['tipo']}
Entrada: {datos_operacion['precio_entrada']:.8f}
Salida: {datos_operacion['precio_salida']:.8f}
PnL Absoluto: {pnl_absoluto:.8f}
PnL %: {datos_operacion['pnl_percent']:.2f}%
Duracion: {datos_operacion['duracion_minutos']:.1f} minutos
Breakout+Reentry: {breakout_usado}
Operacion BITGET FUTUROS: {operacion_ejecutada}
Angulo: {datos_operacion['angulo_tendencia']:.1f}
Pearson: {datos_operacion['pearson']:.3f}
R2: {datos_operacion['r2_score']:.3f}
Ancho: {datos_operacion.get('ancho_canal_porcentual', 0):.1f}%
TF: {datos_operacion.get('timeframe_utilizado', 'N/A')}
Velas: {datos_operacion.get('velas_utilizadas', 0)}
{datos_operacion['timestamp']}
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
            return "Muy Debil", 1
        elif angulo_abs < 13:
            return "Debil", 2
        elif angulo_abs < 27:
            return "Moderada", 3
        elif angulo_abs < 45:
            return "Fuerte", 4
        else:
            return "Muy Fuerte", 5

    def determinar_direccion_tendencia(self, angulo_grados, umbral_minimo=1):
        if abs(angulo_grados) < umbral_minimo:
            return "RANGO"
        elif angulo_grados > 0:
            return "ALCISTA"
        else:
            return "BAJISTA"

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
            print(f"Error generando grafico: {e}")
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
                print(f"     Error enviando grafico: {e}")
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
                print("Iniciando re-optimizacion automatica...")
                ia = OptimizadorIA(log_path=self.log_path, min_samples=self.config.get('min_samples_optimizacion', 30))
                nuevos_parametros = ia.buscar_mejores_parametros()
                if nuevos_parametros:
                    self.actualizar_parametros(nuevos_parametros)
                    self.ultima_optimizacion = datetime.now()
                    self.operaciones_desde_optimizacion = 0
                    print("Parametros actualizados en tiempo real")
        except Exception as e:
            print(f"Error en re-optimizacion automatica: {e}")

    def actualizar_parametros(self, nuevos_parametros):
        self.config['trend_threshold_degrees'] = nuevos_parametros.get('trend_threshold_degrees', 
                                                                        self.config.get('trend_threshold_degrees', 16))
        self.config['min_trend_strength_degrees'] = nuevos_parametros.get('min_trend_strength_degrees', 
                                                                           self.config.get('min_trend_strength_degrees', 16))
        self.config['entry_margin'] = nuevos_parametros.get('entry_margin', 
                                                             self.config.get('entry_margin', 0.001))

    def ejecutar_analisis(self):
        """Ejecutar analisis completo incluyendo sincronizacion con Bitget"""
        try:
            if self.bitget_client:
                self.sincronizar_con_bitget()
            
            if self.bitget_client:
                self.verificar_y_recolocar_tp_sl()
            
            if random.random() < 0.1:
                self.reoptimizar_periodicamente()
            
            cierres = self.verificar_cierre_operaciones()
            if cierres:
                print(f"     Operaciones cerradas: {', '.join(cierres)}")
            
            self.guardar_estado()
            
            return self.escanear_mercado()
            
        except Exception as e:
            logger.error(f"Error en ejecutar_analisis: {e}")
            try:
                self.guardar_estado()
            except:
                pass
            return 0

    def mostrar_resumen_operaciones(self):
        print(f"\nRESUMEN OPERACIONES:")
        print(f"   Activas: {len(self.operaciones_activas)}")
        print(f"   Esperando reentry: {len(self.esperando_reentry)}")
        print(f"   Total ejecutadas: {self.total_operaciones}")
        if self.bitget_client:
            print(f"   BITGET FUTUROS: Conectado")
            if self.ejecutar_operaciones_automaticas:
                print(f"   AUTO-TRADING: ACTIVADO (Dinero REAL)")
            else:
                print(f"   AUTO-TRADING: Solo senales")
        else:
            print(f"   BITGET FUTUROS: No configurado")
        if self.operaciones_activas:
            for simbolo, op in self.operaciones_activas.items():
                estado = "LONG" if op['tipo'] == 'LONG' else "SHORT"
                ancho_canal = op.get('ancho_canal_porcentual', 0)
                timeframe = op.get('timeframe_utilizado', 'N/A')
                velas = op.get('velas_utilizadas', 0)
                breakout = "🚀" if op.get('breakout_usado', False) else ""
                ejecutada = "🤖" if op.get('operacion_ejecutada', False) else ""
                manual = "👤" if op.get('operacion_manual_usuario', False) else ""
                print(f"   {simbolo} {estado} {breakout} {ejecutada} {manual} - {timeframe} - {velas}v - Ancho: {ancho_canal:.1f}%")

    def iniciar(self):
        print("\n" + "=" * 70)
        print("BOT DE TRADING - ESTRATEGIA BREAKOUT + REENTRY")
        print("PRIORIDAD: TIMEFRAMES CORTOS (1m > 3m > 5m > 15m > 30m)")
        print("PERSISTENCIA: ACTIVADA")
        print("REEVALUACION: CADA 2 HORAS")
        print("INTEGRACION: BITGET FUTUROS API (Dinero REAL)")
        print("=" * 70)
        print(f"Simbolos: {len(self.config.get('symbols', []))} monedas")
        print(f"Timeframes: {', '.join(self.config.get('timeframes', []))}")
        print(f"Velas: {self.config.get('velas_options', [])}")
        print(f"ANCHO MINIMO: {self.config.get('min_channel_width_percent', 4)}%")
        print(f"Estrategia: 1) Detectar Breakout -> 2) Esperar Reentry -> 3) Confirmar con Stoch")
        if self.bitget_client:
            print(f"BITGET FUTUROS: API Conectada")
            print(f"Apalancamiento: {self.leverage_por_defecto}x")
            print(f"MARGIN USDT: 3% del saldo actual (se recalcula para CADA operacion)")
            print(f"Sistema: El saldo disminuye progresivamente con cada operacion")
            if self.ejecutar_operaciones_automaticas:
                print(f"AUTO-TRADING: ACTIVADO (Operaciones REALES con dinero)")
                print("ADVERTENCIA: TRADING AUTOMATICO REAL ACTIVADO")
                print("   El bot ejecutara operaciones REALES en Bitget Futures")
                print("   Cada operacion usara 3% del saldo actual (el saldo disminuye)")
                print("   Usa con cuidado y solo con capital que puedas perder")
                confirmar = input("\nContinuar? (s/n): ").strip().lower()
                if confirmar not in ['s', 'si', 's', 'y', 'yes']:
                    print("Operacion cancelada")
                    return
            else:
                print(f"AUTO-TRADING: Solo senales (Paper Trading)")
        else:
            print(f"BITGET FUTUROS: No configurado (solo senales)")
        print("=" * 70)
        print("\nINICIANDO BOT...")
        
        if self.bitget_client:
            print("\nREALIZANDO SINCRONIZACION INICIAL CON BITGET...")
            self.sincronizar_con_bitget()
            print("Sincronizacion inicial completada")
        
        try:
            while True:
                nuevas_senales = self.ejecutar_analisis()
                self.mostrar_resumen_operaciones()
                minutos_espera = self.config.get('scan_interval_minutes', 1)
                print(f"\nAnalisis completado. Senales nuevas: {nuevas_senales}")
                print(f"Proximo analisis en {minutos_espera} minutos...")
                print("-" * 60)
                for minuto in range(minutos_espera):
                    time.sleep(60)
                    restantes = minutos_espera - (minuto + 1)
                    if restantes > 0 and restantes % 5 == 0:
                        print(f"   {restantes} minutos restantes...")
        except KeyboardInterrupt:
            print("\nBot detenido por el usuario")
            print("Guardando estado final...")
            self.guardar_estado()
            print("Hasta pronto!")
        except Exception as e:
            print(f"\nError en el bot: {e}")
            print("Intentando guardar estado...")
            try:
                self.guardar_estado()
            except:
                pass

# ---------------------------
# CONFIGURACION CON CREDENCIALES REALES DE BITGET FUTUROS
# ---------------------------
def crear_config_completa():
    """Configuracion completa con credenciales REALES de Bitget Futures y Telegram"""
    
    BITGET_API_KEY = 'bg_0e9c732f2ed08d90c986a7fd9a4cdedd'
    BITGET_SECRET_KEY = '52582b11761d83bce4e4475182b1510617081dd4e56051e787178a2a06a5bd3b'
    BITGET_PASSPHRASE = 'Rasputino977'
    
    TELEGRAM_TOKEN = '8406173543:AAFIuYlFd3jtAF1Q6SNntUGn1PopgkZ7S0k'
    TELEGRAM_CHAT_ID = '2108159591'
    
    if 'RENDER' in os.environ:
        print("Leyendo configuracion desde variables de entorno (Render.com)...", file=sys.stdout)
        
        if os.environ.get('BITGET_API_KEY'):
            BITGET_API_KEY = os.environ.get('BITGET_API_KEY')
            print("Usando BITGET_API_KEY desde variable de entorno", file=sys.stdout)
        if os.environ.get('BITGET_SECRET_KEY'):
            BITGET_SECRET_KEY = os.environ.get('BITGET_SECRET_KEY')
            print("Usando BITGET_SECRET_KEY desde variable de entorno", file=sys.stdout)
        if os.environ.get('BITGET_PASSPHRASE'):
            BITGET_PASSPHRASE = os.environ.get('BITGET_PASSPHRASE')
            print("Usando BITGET_PASSPHRASE desde variable de entorno", file=sys.stdout)
        if os.environ.get('TELEGRAM_TOKEN'):
            TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
            print("Usando TELEGRAM_TOKEN desde variable de entorno", file=sys.stdout)
        if os.environ.get('TELEGRAM_CHAT_ID'):
            TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')
            print("Usando TELEGRAM_CHAT_ID desde variable de entorno", file=sys.stdout)
        
        ejecutar_automaticas = True
        if os.environ.get('EJECUTAR_OPERACIONES_AUTOMATICAS'):
            ejecutar_automaticas = os.environ.get('EJECUTAR_OPERACIONES_AUTOMATICAS').lower() == 'true'
            print(f"EJECUTAR_OPERACIONES_AUTOMATICAS: {ejecutar_automaticas}", file=sys.stdout)
        
        leverage = 20
        if os.environ.get('LEVERAGE_POR_DEFECTO'):
            leverage = int(os.environ.get('LEVERAGE_POR_DEFECTO'))
            print(f"LEVERAGE_POR_DEFECTO: {leverage}", file=sys.stdout)
        
        capital_por_operacion = 0.03
        if os.environ.get('CAPITAL_POR_OPERACION'):
            try:
                capital_por_operacion = float(os.environ.get('CAPITAL_POR_OPERACION'))
                print(f"CAPITAL_POR_OPERACION: {capital_por_operacion}", file=sys.stdout)
            except ValueError:
                print("CAPITAL_POR_OPERACION invalido, usando valor por defecto 3%", file=sys.stdout)
    else:
        ejecutar_automaticas = True
        leverage = 20
        capital_por_operacion = 0.03
    
    directorio_actual = os.path.dirname(os.path.abspath(__file__))
    
    return {
        'min_channel_width_percent': 4.0,
        'trend_threshold_degrees': 16.0,
        'min_trend_strength_degrees': 16.0,
        'entry_margin': 0.001,
        'min_rr_ratio': 1.2,
        'scan_interval_minutes': 8,
        'timeframes': ['5m', '15m', '30m', '1h', ],
        'velas_options': [80, 100, 120, 150, 200],
        'symbols': [
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
        'bitget_api_key': BITGET_API_KEY,
        'bitget_api_secret': BITGET_SECRET_KEY,
        'bitget_passphrase': BITGET_PASSPHRASE,
        'ejecutar_operaciones_automaticas': ejecutar_automaticas,
        'leverage_por_defecto': leverage
    }

# ---------------------------
# FLASK APP PARA EJECUCION LOCAL
# ---------------------------

app = Flask(__name__)

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

bot_thread = threading.Thread(target=run_bot_loop, daemon=True)
bot_thread.start()


@app.route('/')
def index():
    return "Bot Breakout + Reentry con integracion Bitget FUTUROS esta en linea.", 200

@app.route('/webhook', methods=['POST'])
def telegram_webhook():
    if request.is_json:
        update = request.get_json()
        print(f"Update recibido: {json.dumps(update)}", file=sys.stdout)
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
        print(f"Error en health check: {e}", file=sys.stderr)
        return jsonify({"status": "error", "message": str(e)}), 500

def setup_telegram_webhook():
    """Configura automaticamente el webhook de Telegram"""
    token = config.get('telegram_token')
    if not token:
        print("No hay token de Telegram configurado", file=sys.stdout)
        return
    
    webhook_url = os.environ.get('WEBHOOK_URL')
    if not webhook_url:
        render_url = os.environ.get('RENDER_EXTERNAL_URL')
        if render_url:
            webhook_url = f"{render_url}/webhook"
        else:
            print("No hay URL de webhook configurada y no se encontro RENDER_EXTERNAL_URL", file=sys.stdout)
            return
    
    try:
        print(f"Configurando webhook Telegram en: {webhook_url}", file=sys.stdout)
        requests.get(f"https://api.telegram.org/bot{token}/deleteWebhook", timeout=10)
        time.sleep(1)
        response = requests.get(f"https://api.telegram.org/bot{token}/setWebhook?url={webhook_url}", timeout=10)
        
        if response.status_code == 200:
            print("Webhook de Telegram configurado correctamente", file=sys.stdout)
        else:
            print(f"Error configurando webhook: {response.status_code} - {response.text}", file=sys.stderr)
    except Exception as e:
        print(f"Error configurando webhook: {e}", file=sys.stderr)

def ejecutar_bot_directamente():
    """Ejecuta el bot directamente sin Flask"""
    print("="*70)
    print("BOT BREAKOUT+REENTRY COMPLETO - BITGET FUTUROS")
    print("Caracteristicas completas:")
    print("   1. Estrategia Breakout+Reentry con confirmacion Estocastico")
    print("   2. Graficos profesionales con mplfinance")
    print("   3. Conexion REAL a Bitget Futures (Dinero REAL)")
    print("   4. Trading automatico REAL con SL/TP")
    print("   5. Optimizador IA automatico")
    print("   6. Alertas Telegram con graficos")
    print("   7. Persistencia de estado")
    print("="*70)
    
    bot.iniciar()

if __name__ == '__main__':
    render_external_url = os.environ.get('RENDER_EXTERNAL_URL')
    render_port = os.environ.get('PORT')
    
    if render_external_url or render_port:
        print("Detectando entorno Render.com", file=sys.stdout)
        if render_external_url:
            print(f"   URL externa: {render_external_url}", file=sys.stdout)
        if render_port:
            print(f"   Puerto: {render_port}", file=sys.stdout)
        
        setup_telegram_webhook()
        
        port = int(render_port) if render_port else 5000
        print(f"Iniciando servidor Flask en puerto {port}...", file=sys.stdout)
        app.run(debug=False, host='0.0.0.0', port=port)
    else:
        print("Modo ejecucion local detectado", file=sys.stdout)
        ejecutar_bot_directamente()
