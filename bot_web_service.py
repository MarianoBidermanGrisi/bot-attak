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
# INDICADORES TÉCNICOS - ADX, DI+, DI-
# ---------------------------

def calcular_adx_di(high, low, close, length=14):
    """
    Calcula el ADX (Average Directional Index) y los indicadores DI+, DI-.
    
    Implementación idéntica a la versión de Pine Script en TradingView.
    
    Parámetros:
    -----------
    high : array-like
        Array de precios máximos
    low : array-like
        Array de precios mínimos
    close : array-like
        Array de precios de cierre
    length : int, opcional
        Período para el cálculo (por defecto 14)
    
    Retorna:
    --------
    dict con las siguientes claves:
        - 'di_plus': Array con los valores de DI+
        - 'di_minus': Array con los valores de DI-
        - 'adx': Array con los valores de ADX
    """
    # Si high es un DataFrame o Serie (convertir a arrays)
    if hasattr(high, 'values'):
        high = high.values
    if hasattr(low, 'values'):
        low = low.values
    if hasattr(close, 'values'):
        close = close.values
    
    # Convertir a arrays de numpy para mejor rendimiento
    try:
        high = np.array(high, dtype=np.float64)
        low = np.array(low, dtype=np.float64)
        close = np.array(close, dtype=np.float64)
    except Exception as e:
        # Si hay error en la conversión, retornar arrays vacíos
        n = 100  # Valor por defecto
        return {
            'di_plus': np.full(n, np.nan),
            'di_minus': np.full(n, np.nan),
            'adx': np.full(n, np.nan)
        }
    
    n = len(high)
    
    # Inicializar arrays de resultados
    true_range = np.zeros(n)
    directional_movement_plus = np.zeros(n)
    directional_movement_minus = np.zeros(n)
    smoothed_true_range = np.zeros(n)
    smoothed_dm_plus = np.zeros(n)
    smoothed_dm_minus = np.zeros(n)
    di_plus = np.zeros(n)
    di_minus = np.zeros(n)
    dx = np.zeros(n)
    adx = np.zeros(n)
    
    
    # Calcular True Range y Directional Movement
    for i in range(1, n):
        # TrueRange = max(high-low, |high-close[1]|, |low-close[1]|)
        true_range[i] = max(
            high[i] - low[i],
            abs(high[i] - close[i-1]),
            abs(low[i] - close[i-1])
        )
        
        # DirectionalMovementPlus = high-nz(high[1]) > nz(low[1])-low ? max(high-nz(high[1]), 0): 0
        up_move = high[i] - high[i-1]
        down_move = low[i-1] - low[i]
        
        if up_move > down_move and up_move > 0:
            directional_movement_plus[i] = up_move
        else:
            directional_movement_plus[i] = 0
        
        # DirectionalMovementMinus = nz(low[1])-low > high-nz(high[1]) ? max(nz(low[1])-low, 0): 0
        if down_move > up_move and down_move > 0:
            directional_movement_minus[i] = down_move
        else:
            directional_movement_minus[i] = 0
    
    # SmoothedTrueRange usando la fórmula de Pine Script
    for i in range(1, n):
        if i == 1:
            # Primera iteración: inicializar con el primer valor
            smoothed_true_range[i] = true_range[i]
            smoothed_dm_plus[i] = directional_movement_plus[i]
            smoothed_dm_minus[i] = directional_movement_minus[i]
        else:
            # Aplicar el suavizado recursivo de Pine Script
            smoothed_true_range[i] = (
                smoothed_true_range[i-1] - 
                smoothed_true_range[i-1] / length + 
                true_range[i]
            )
            smoothed_dm_plus[i] = (
                smoothed_dm_plus[i-1] - 
                smoothed_dm_plus[i-1] / length + 
                directional_movement_plus[i]
            )
            smoothed_dm_minus[i] = (
                smoothed_dm_minus[i-1] - 
                smoothed_dm_minus[i-1] / length + 
                directional_movement_minus[i]
            )
    
    # Evitar división por cero
    safe_tr = np.where(smoothed_true_range == 0, np.nan, smoothed_true_range)
    
    # DIPlus = SmoothedDirectionalMovementPlus / SmoothedTrueRange * 100
    di_plus = np.where(
        np.isnan(safe_tr),
        np.nan,
        (smoothed_dm_plus / smoothed_true_range) * 100
    )
    
    # DIMinus = SmoothedDirectionalMovementMinus / SmoothedTrueRange * 100
    di_minus = np.where(
        np.isnan(safe_tr),
        np.nan,
        (smoothed_dm_minus / smoothed_true_range) * 100
    )
    
    # DX = abs(DIPlus-DIMinus) / (DIPlus+DIMinus)*100
    di_sum = np.nan_to_num(di_plus) + np.nan_to_num(di_minus)
    di_diff = np.abs(np.nan_to_num(di_plus) - np.nan_to_num(di_minus))
    
    dx = np.where(
        di_sum == 0,
        0,
        (di_diff / di_sum) * 100
    )
    
    # ADX = sma(DX, length) - Media móvil simple de DX
    for i in range(n):
        if i < length - 1:
            adx[i] = np.nan
        else:
            adx[i] = np.mean(dx[i-length+1:i+1])
    
    return {
        'di_plus': di_plus,
        'di_minus': di_minus,
        'adx': adx
    }


def calcular_adx_di_pandas(df, high_col='High', low_col='Low', close_col='Close', length=14):
    """
    Versión optimizada usando pandas DataFrame.
    
    Parámetros:
    -----------
    df : pd.DataFrame
        DataFrame con los datos OHLC
    high_col : str
        Nombre de la columna de precios máximos (por defecto 'High')
    low_col : str
        Nombre de la columna de precios mínimos (por defecto 'Low')
    close_col : str
        Nombre de la columna de precios de cierre (por defecto 'Close')
    length : int
        Período para el cálculo (por defecto 14)
    
    Retorna:
    --------
    pd.DataFrame con las columnas DI+, DI-, ADX añadidas
    """
    resultado = df.copy()
    
    # Calcular True Range
    resultado['tr'] = np.maximum(
        resultado[high_col] - resultado[low_col],
        np.maximum(
            abs(resultado[high_col] - resultado[close_col].shift()),
            abs(resultado[low_col] - resultado[close_col].shift())
        )
    )
    
    # Calcular movimientos direccionales
    up_move = resultado[high_col] - resultado[high_col].shift()
    down_move = resultado[low_col].shift() - resultado[low_col]
    
    resultado['dm_plus'] = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
    resultado['dm_minus'] = np.where((down_move > up_move) & (down_move > 0), down_move, 0)
    
    # Suavizado recursivo (equivalente a Wilder's smoothing)
    alpha = 1 / length
    resultado['smoothed_tr'] = resultado['tr'].ewm(alpha=alpha, adjust=False).mean()
    resultado['smoothed_dm_plus'] = resultado['dm_plus'].ewm(alpha=alpha, adjust=False).mean()
    resultado['smoothed_dm_minus'] = resultado['dm_minus'].ewm(alpha=alpha, adjust=False).mean()
    
    # Calcular DI+ y DI-
    resultado['DI+'] = (resultado['smoothed_dm_plus'] / resultado['smoothed_tr']) * 100
    resultado['DI-'] = (resultado['smoothed_dm_minus'] / resultado['smoothed_tr']) * 100
    
    # Calcular DX
    di_sum = resultado['DI+'] + resultado['DI-']
    resultado['dx'] = np.where(
        di_sum == 0,
        0,
        (abs(resultado['DI+'] - resultado['DI-']) / di_sum) * 100
    )
    
    # Calcular ADX (SMA de DX)
    resultado['ADX'] = resultado['dx'].rolling(window=length).mean()
    
    # Limpiar columnas intermedias
    resultado.drop(['tr', 'dm_plus', 'dm_minus', 'smoothed_tr', 
                    'smoothed_dm_plus', 'smoothed_dm_minus', 'dx'], 
                   axis=1, inplace=True)
    
    return resultado


# ---------------------------
# CLIENTE BITGET API
# ---------------------------
class BitgetFuturesClient:
    """Cliente para la API de Bitget Futures"""
    
    def __init__(self, api_key, api_secret, passphrase):
        self.api_key = api_key
        self.api_secret = api_secret
        self.passphrase = passphrase
        self.base_url = "https://api.bitget.com"
        
    def _generate_signature(self, timestamp, method, request_path, body=''):
        """Genera la firma para autenticación"""
        message = timestamp + method + request_path + body
        mac = hmac.new(
            bytes(self.api_secret, encoding='utf8'),
            bytes(message, encoding='utf-8'),
            digestmod=hashlib.sha256
        )
        return base64.b64encode(mac.digest()).decode()
    
    def _get_headers(self, method, request_path, body=''):
        """Genera los headers para la petición"""
        timestamp = str(int(time.time() * 1000))
        signature = self._generate_signature(timestamp, method, request_path, body)
        
        return {
            'ACCESS-KEY': self.api_key,
            'ACCESS-SIGN': signature,
            'ACCESS-TIMESTAMP': timestamp,
            'ACCESS-PASSPHRASE': self.passphrase,
            'Content-Type': 'application/json',
            'locale': 'en-US'
        }
    
    def get_account_balance(self):
        """Obtiene el balance de la cuenta de futuros USDT"""
        try:
            method = 'GET'
            request_path = '/api/v2/mix/account/accounts'
            params = '?productType=USDT-FUTURES'
            
            headers = self._get_headers(method, request_path + params)
            url = self.base_url + request_path + params
            
            response = requests.get(url, headers=headers, timeout=10)
            data = response.json()
            
            if data.get('code') == '00000':
                accounts = data.get('data', [])
                for account in accounts:
                    if account.get('marginCoin') == 'USDT':
                        return {
                            'available': float(account.get('available', 0)),
                            'equity': float(account.get('equity', 0)),
                            'locked': float(account.get('locked', 0)),
                            'unrealizedPL': float(account.get('unrealizedPL', 0))
                        }
            return None
        except Exception as e:
            logger.error(f"Error obteniendo balance: {e}")
            return None
    
    def get_klines(self, symbol, interval, limit=100):
        """Obtiene datos de velas (klines) para un símbolo"""
        try:
            method = 'GET'
            request_path = '/api/v2/mix/market/candles'
            
            # Mapear intervalos de tiempo
            interval_map = {
                '1m': '1m', '5m': '5m', '15m': '15m', '30m': '30m',
                '1h': '1H', '4h': '4H', '12h': '12H', '1d': '1D'
            }
            bitget_interval = interval_map.get(interval, interval)
            
            params = f'?symbol={symbol}&productType=USDT-FUTURES&granularity={bitget_interval}&limit={limit}'
            
            headers = self._get_headers(method, request_path + params)
            url = self.base_url + request_path + params
            
            response = requests.get(url, headers=headers, timeout=10)
            data = response.json()
            
            if data.get('code') == '00000':
                return data.get('data', [])
            return None
        except Exception as e:
            logger.error(f"Error obteniendo klines para {symbol}: {e}")
            return None
    
    def get_ticker(self, symbol):
        """Obtiene el precio actual de un símbolo"""
        try:
            method = 'GET'
            request_path = '/api/v2/mix/market/ticker'
            params = f'?symbol={symbol}&productType=USDT-FUTURES'
            
            headers = self._get_headers(method, request_path + params)
            url = self.base_url + request_path + params
            
            response = requests.get(url, headers=headers, timeout=10)
            data = response.json()
            
            if data.get('code') == '00000':
                ticker_data = data.get('data', [])
                if ticker_data:
                    return {
                        'last': float(ticker_data[0].get('last', 0)),
                        'bid': float(ticker_data[0].get('bidPr', 0)),
                        'ask': float(ticker_data[0].get('askPr', 0))
                    }
            return None
        except Exception as e:
            logger.error(f"Error obteniendo ticker para {symbol}: {e}")
            return None
    
    def place_order(self, symbol, side, order_type, size, price=None, reduce_only=False):
        """Coloca una orden en Bitget Futures"""
        try:
            method = 'POST'
            request_path = '/api/v2/mix/order/place-order'
            
            body_data = {
                'symbol': symbol,
                'productType': 'USDT-FUTURES',
                'marginMode': 'crossed',
                'marginCoin': 'USDT',
                'side': side,  # 'buy' o 'sell'
                'orderType': order_type,  # 'limit' o 'market'
                'size': str(size),
                'tradeSide': 'open' if not reduce_only else 'close'
            }
            
            if price:
                body_data['price'] = str(price)
            
            body = json.dumps(body_data)
            headers = self._get_headers(method, request_path, body)
            url = self.base_url + request_path
            
            response = requests.post(url, headers=headers, data=body, timeout=10)
            data = response.json()
            
            if data.get('code') == '00000':
                return data.get('data', {})
            else:
                logger.error(f"Error colocando orden: {data.get('msg', 'Unknown error')}")
                return None
        except Exception as e:
            logger.error(f"Error colocando orden: {e}")
            return None
    
    def place_plan_order(self, symbol, side, size, trigger_price, order_price=None, hold_side='long'):
        """Coloca una orden plan (TP/SL) en Bitget Futures"""
        try:
            method = 'POST'
            request_path = '/api/v2/mix/order/place-plan-order'
            
            body_data = {
                'symbol': symbol,
                'productType': 'USDT-FUTURES',
                'marginMode': 'crossed',
                'marginCoin': 'USDT',
                'side': side,
                'orderType': 'market',
                'triggerPrice': str(trigger_price),
                'size': str(size),
                'tradeSide': 'close',
                'triggerType': 'mark_price',
                'holdSide': hold_side  # 'long' o 'short'
            }
            
            if order_price:
                body_data['executePrice'] = str(order_price)
            
            body = json.dumps(body_data)
            headers = self._get_headers(method, request_path, body)
            url = self.base_url + request_path
            
            response = requests.post(url, headers=headers, data=body, timeout=10)
            data = response.json()
            
            if data.get('code') == '00000':
                return data.get('data', {})
            else:
                logger.error(f"Error colocando orden plan: {data.get('msg', 'Unknown error')}")
                return None
        except Exception as e:
            logger.error(f"Error colocando orden plan: {e}")
            return None
    
    def get_position(self, symbol):
        """Obtiene la posición actual para un símbolo"""
        try:
            method = 'GET'
            request_path = '/api/v2/mix/position/single-position'
            params = f'?symbol={symbol}&productType=USDT-FUTURES&marginCoin=USDT'
            
            headers = self._get_headers(method, request_path + params)
            url = self.base_url + request_path + params
            
            response = requests.get(url, headers=headers, timeout=10)
            data = response.json()
            
            if data.get('code') == '00000':
                positions = data.get('data', [])
                for pos in positions:
                    if float(pos.get('total', 0)) != 0:
                        return pos
            return None
        except Exception as e:
            logger.error(f"Error obteniendo posición: {e}")
            return None
    
    def get_all_positions(self):
        """Obtiene todas las posiciones abiertas"""
        try:
            method = 'GET'
            request_path = '/api/v2/mix/position/all-position'
            params = '?productType=USDT-FUTURES&marginCoin=USDT'
            
            headers = self._get_headers(method, request_path + params)
            url = self.base_url + request_path + params
            
            response = requests.get(url, headers=headers, timeout=10)
            data = response.json()
            
            if data.get('code') == '00000':
                return data.get('data', [])
            return []
        except Exception as e:
            logger.error(f"Error obteniendo posiciones: {e}")
            return []
    
    def set_leverage(self, symbol, leverage, hold_side='long'):
        """Establece el apalancamiento para un símbolo"""
        try:
            method = 'POST'
            request_path = '/api/v2/mix/account/set-leverage'
            
            body_data = {
                'symbol': symbol,
                'productType': 'USDT-FUTURES',
                'marginCoin': 'USDT',
                'leverage': str(leverage),
                'holdSide': hold_side
            }
            
            body = json.dumps(body_data)
            headers = self._get_headers(method, request_path, body)
            url = self.base_url + request_path
            
            response = requests.post(url, headers=headers, data=body, timeout=10)
            data = response.json()
            
            return data.get('code') == '00000'
        except Exception as e:
            logger.error(f"Error estableciendo apalancamiento: {e}")
            return False
    
    def close_position(self, symbol, hold_side='long'):
        """Cierra una posición completamente"""
        try:
            position = self.get_position(symbol)
            if not position:
                return True
            
            size = abs(float(position.get('total', 0)))
            if size == 0:
                return True
            
            side = 'sell' if hold_side == 'long' else 'buy'
            
            return self.place_order(
                symbol=symbol,
                side=side,
                order_type='market',
                size=size,
                reduce_only=True
            )
        except Exception as e:
            logger.error(f"Error cerrando posición: {e}")
            return False
    
    def cancel_plan_order(self, symbol, order_id):
        """Cancela una orden plan (TP/SL)"""
        try:
            method = 'POST'
            request_path = '/api/v2/mix/order/cancel-plan-order'
            
            body_data = {
                'orderId': order_id,
                'symbol': symbol,
                'productType': 'USDT-FUTURES',
                'marginCoin': 'USDT'
            }
            
            body = json.dumps(body_data)
            headers = self._get_headers(method, request_path, body)
            url = self.base_url + request_path
            
            response = requests.post(url, headers=headers, data=body, timeout=10)
            data = response.json()
            
            return data.get('code') == '00000'
        except Exception as e:
            logger.error(f"Error cancelando orden plan: {e}")
            return False
    
    def get_symbol_info(self, symbol):
        """Obtiene información del símbolo (tamaño mínimo, precisión, etc.)"""
        try:
            method = 'GET'
            request_path = '/api/v2/mix/market/contracts'
            params = f'?productType=USDT-FUTURES&symbol={symbol}'
            
            headers = self._get_headers(method, request_path + params)
            url = self.base_url + request_path + params
            
            response = requests.get(url, headers=headers, timeout=10)
            data = response.json()
            
            if data.get('code') == '00000':
                contracts = data.get('data', [])
                for contract in contracts:
                    if contract.get('symbol') == symbol:
                        return {
                            'minTradeNum': float(contract.get('minTradeNum', 0)),
                            'priceEndStep': int(contract.get('priceEndStep', 2)),
                            'volumePlace': int(contract.get('volumePlace', 0)),
                            'pricePlace': int(contract.get('pricePlace', 2)),
                            'sizeMultiplier': float(contract.get('sizeMultiplier', 1))
                        }
            return None
        except Exception as e:
            logger.error(f"Error obteniendo info del símbolo: {e}")
            return None


# ---------------------------
# FUNCIONES DE EJECUCIÓN DE OPERACIONES EN BITGET
# ---------------------------

def ejecutar_operacion_bitget(bitget_client, simbolo, tipo_operacion, capital_usd=None, leverage=None):
    """
    Ejecuta una operación en Bitget Futures.
    
    Args:
        bitget_client: Cliente de Bitget
        simbolo: Símbolo del par (ej: 'BTCUSDT')
        tipo_operacion: 'LONG' o 'SHORT'
        capital_usd: Capital a usar en USD (si None, usa 3% del saldo)
        leverage: Apalancamiento (si None, usa el máximo permitido)
    
    Returns:
        dict con información de la operación o None si falla
    """
    try:
        logger.info(f"🚀 Iniciando operación {tipo_operacion} en {simbolo}")
        
        # Obtener balance
        balance = bitget_client.get_account_balance()
        if not balance:
            logger.error("❌ No se pudo obtener el balance")
            return None
        
        saldo_disponible = balance['available']
        logger.info(f"💰 Saldo disponible: ${saldo_disponible:.2f}")
        
        # Calcular capital a usar (3% del saldo si no se especifica)
        if capital_usd is None:
            capital_usd = saldo_disponible * 0.03
        
        capital_usd = min(capital_usd, saldo_disponible)  # No usar más del disponible
        logger.info(f"💵 Capital a usar: ${capital_usd:.2f}")
        
        # Obtener info del símbolo
        symbol_info = bitget_client.get_symbol_info(simbolo)
        if not symbol_info:
            logger.error(f"❌ No se pudo obtener info del símbolo {simbolo}")
            return None
        
        # Obtener precio actual
        ticker = bitget_client.get_ticker(simbolo)
        if not ticker:
            logger.error(f"❌ No se pudo obtener precio de {simbolo}")
            return None
        
        precio_actual = ticker['last']
        logger.info(f"💰 Precio actual: {precio_actual}")
        
        # Determinar apalancamiento
        if leverage is None:
            leverage = 20  # Apalancamiento por defecto
        
        leverage = min(leverage, 20)  # Máximo 20x
        
        # Establecer apalancamiento
        hold_side = 'long' if tipo_operacion == 'LONG' else 'short'
        if not bitget_client.set_leverage(simbolo, leverage, hold_side):
            logger.warning(f"⚠️ No se pudo establecer apalancamiento {leverage}x, continuando...")
        else:
            logger.info(f"⚡ Apalancamiento establecido: {leverage}x")
        
        # Calcular tamaño de la posición
        valor_nocional = capital_usd * leverage
        size = valor_nocional / precio_actual
        
        # Redondear según precisión del símbolo
        volume_place = symbol_info['volumePlace']
        size = round(size, volume_place)
        
        # Verificar tamaño mínimo
        min_size = symbol_info['minTradeNum']
        if size < min_size:
            logger.error(f"❌ Tamaño {size} menor que mínimo {min_size}")
            return None
        
        logger.info(f"📊 Tamaño de posición: {size} contratos")
        logger.info(f"💰 Valor nocional: ${valor_nocional:.2f}")
        
        # Colocar orden de entrada
        side = 'buy' if tipo_operacion == 'LONG' else 'sell'
        orden_entrada = bitget_client.place_order(
            symbol=simbolo,
            side=side,
            order_type='market',
            size=size
        )
        
        if not orden_entrada:
            logger.error("❌ Error colocando orden de entrada")
            return None
        
        logger.info(f"✅ Orden de entrada colocada: {orden_entrada.get('orderId')}")
        
        # Esperar a que se ejecute la orden
        time.sleep(2)
        
        # Obtener la posición para confirmar entrada
        position = bitget_client.get_position(simbolo)
        if not position:
            logger.error("❌ No se pudo verificar la posición")
            return None
        
        precio_entrada_real = float(position.get('averageOpenPrice', precio_actual))
        logger.info(f"🎯 Precio de entrada real: {precio_entrada_real}")
        
        # Calcular TP y SL (2% SL, ratio 2:1)
        sl_percent = 0.02
        tp_multiplier = 2.0
        
        if tipo_operacion == 'LONG':
            stop_loss = precio_entrada_real * (1 - sl_percent)
            take_profit = precio_entrada_real * (1 + (sl_percent * tp_multiplier))
            sl_side = 'sell'
            tp_side = 'sell'
        else:
            stop_loss = precio_entrada_real * (1 + sl_percent)
            take_profit = precio_entrada_real * (1 - (sl_percent * tp_multiplier))
            sl_side = 'buy'
            tp_side = 'buy'
        
        # Redondear precios según precisión
        price_place = symbol_info['pricePlace']
        stop_loss = round(stop_loss, price_place)
        take_profit = round(take_profit, price_place)
        
        logger.info(f"🛑 Stop Loss: {stop_loss}")
        logger.info(f"🎯 Take Profit: {take_profit}")
        
        # Colocar orden de Stop Loss
        orden_sl = bitget_client.place_plan_order(
            symbol=simbolo,
            side=sl_side,
            size=size,
            trigger_price=stop_loss,
            hold_side=hold_side
        )
        
        if orden_sl:
            logger.info(f"✅ Stop Loss colocado: {orden_sl.get('orderId')}")
        else:
            logger.warning("⚠️ No se pudo colocar Stop Loss")
        
        # Colocar orden de Take Profit
        orden_tp = bitget_client.place_plan_order(
            symbol=simbolo,
            side=tp_side,
            size=size,
            trigger_price=take_profit,
            hold_side=hold_side
        )
        
        if orden_tp:
            logger.info(f"✅ Take Profit colocado: {orden_tp.get('orderId')}")
        else:
            logger.warning("⚠️ No se pudo colocar Take Profit")
        
        return {
            'orden_entrada': orden_entrada,
            'orden_sl': orden_sl,
            'orden_tp': orden_tp,
            'precio_entrada': precio_entrada_real,
            'stop_loss': stop_loss,
            'take_profit': take_profit,
            'capital_usado': capital_usd,
            'leverage': leverage,
            'size': size,
            'saldo_cuenta': balance['equity']
        }
        
    except Exception as e:
        logger.error(f"❌ Error ejecutando operación: {e}", exc_info=True)
        return None


# ---------------------------
# BOT DE TRADING
# ---------------------------
class TradingBot:
    def __init__(self, config):
        self.config = config
        self.operaciones_activas = {}
        self.breakout_history = {}
        self.esperando_reentry = {}
        self.breakouts_detectados = {}
        self.senales_enviadas = []
        self.total_operaciones = 0
        self.config_optima_por_simbolo = {}
        self.ultima_busqueda_config = {}
        self.operaciones_desde_optimizacion = 0
        self.operaciones_cerradas_registradas = []
        
        # Cliente de Bitget
        self.bitget_client = None
        self.operaciones_bitget_activas = {}
        self.order_ids_entrada = {}
        self.order_ids_sl = {}
        self.order_ids_tp = {}
        self.ejecutar_operaciones_automaticas = config.get('ejecutar_operaciones_automaticas', False)
        self.leverage_por_defecto = config.get('leverage_por_defecto', 20)
        
        # Inicializar cliente Bitget si hay credenciales
        api_key = config.get('bitget_api_key')
        api_secret = config.get('bitget_api_secret')
        passphrase = config.get('bitget_passphrase')
        
        if api_key and api_secret and passphrase:
            try:
                self.bitget_client = BitgetFuturesClient(api_key, api_secret, passphrase)
                # Verificar conexión
                balance = self.bitget_client.get_account_balance()
                if balance:
                    logger.info(f"✅ Cliente Bitget conectado - Saldo: ${balance['equity']:.2f}")
                else:
                    logger.error("❌ Error conectando con Bitget")
                    self.bitget_client = None
            except Exception as e:
                logger.error(f"❌ Error inicializando cliente Bitget: {e}")
                self.bitget_client = None
        
        self.cargar_estado()
    
    def sincronizar_con_bitget(self):
        """Sincroniza el estado del bot con las posiciones reales en Bitget"""
        if not self.bitget_client:
            return
        
        try:
            print("🔄 Sincronizando con Bitget...")
            
            # Obtener todas las posiciones de Bitget
            posiciones_bitget = self.bitget_client.get_all_positions()
            
            simbolos_en_bitget = set()
            
            # Procesar cada posición
            for pos in posiciones_bitget:
                simbolo = pos.get('symbol')
                total = float(pos.get('total', 0))
                
                if total == 0:
                    continue
                
                simbolos_en_bitget.add(simbolo)
                
                # Si hay posición en Bitget pero no en nuestro tracking, agregarla
                if simbolo not in self.operaciones_activas:
                    hold_side = pos.get('holdSide', 'long')
                    tipo = 'LONG' if hold_side == 'long' else 'SHORT'
                    
                    print(f"   📊 Detectada posición manual en Bitget: {simbolo} {tipo}")
                    
                    self.operaciones_activas[simbolo] = {
                        'tipo': tipo,
                        'precio_entrada': float(pos.get('averageOpenPrice', 0)),
                        'take_profit': 0,
                        'stop_loss': 0,
                        'timestamp_entrada': datetime.now().isoformat(),
                        'operacion_manual_usuario': True,  # Marca como manual
                        'operacion_ejecutada': True,
                        'stoch_k': 0,
                        'stoch_d': 0,
                        'di_plus': 0,
                        'di_minus': 0,
                        'sincronizada_bitget': True
                    }
                    
                    self.operaciones_bitget_activas[simbolo] = pos
            
            # Limpiar operaciones que ya no existen en Bitget
            for simbolo in list(self.operaciones_activas.keys()):
                if simbolo not in simbolos_en_bitget:
                    es_manual = self.operaciones_activas[simbolo].get('operacion_manual_usuario', False)
                    if es_manual:
                        print(f"   🧹 Limpiando operación manual cerrada: {simbolo}")
                        del self.operaciones_activas[simbolo]
                        if simbolo in self.operaciones_bitget_activas:
                            del self.operaciones_bitget_activas[simbolo]
            
            print(f"✅ Sincronización completa - {len(simbolos_en_bitget)} posiciones activas en Bitget")
            
        except Exception as e:
            logger.error(f"❌ Error en sincronización con Bitget: {e}")
    
    def guardar_estado(self):
        """Guarda el estado actual del bot"""
        try:
            estado = {
                'operaciones_activas': self.operaciones_activas,
                'breakout_history': {k: v.isoformat() for k, v in self.breakout_history.items()},
                'esperando_reentry': {
                    k: {
                        'tipo': v['tipo'],
                        'timestamp': v['timestamp'].isoformat(),
                        'precio_breakout': v['precio_breakout'],
                        'config': v['config']
                    } for k, v in self.esperando_reentry.items()
                },
                'breakouts_detectados': {
                    k: {
                        'tipo': v['tipo'],
                        'timestamp': v['timestamp'].isoformat(),
                        'precio_breakout': v['precio_breakout']
                    } for k, v in self.breakouts_detectados.items()
                },
                'senales_enviadas': self.senales_enviadas,
                'total_operaciones': self.total_operaciones,
                'config_optima_por_simbolo': self.config_optima_por_simbolo,
                'ultima_busqueda_config': {k: v.isoformat() for k, v in self.ultima_busqueda_config.items()},
                'operaciones_desde_optimizacion': self.operaciones_desde_optimizacion,
                'operaciones_cerradas_registradas': self.operaciones_cerradas_registradas,
                'operaciones_bitget_activas': self.operaciones_bitget_activas,
                'order_ids_entrada': self.order_ids_entrada,
                'order_ids_sl': self.order_ids_sl,
                'order_ids_tp': self.order_ids_tp
            }
            
            with open(self.config['estado_file'], 'w') as f:
                json.dump(estado, f, indent=2)
        except Exception as e:
            logger.error(f"Error guardando estado: {e}")
    
    def cargar_estado(self):
        """Carga el estado guardado del bot"""
        try:
            if os.path.exists(self.config['estado_file']):
                with open(self.config['estado_file'], 'r') as f:
                    estado = json.load(f)
                
                self.operaciones_activas = estado.get('operaciones_activas', {})
                self.breakout_history = {k: datetime.fromisoformat(v) for k, v in estado.get('breakout_history', {}).items()}
                self.esperando_reentry = {
                    k: {
                        'tipo': v['tipo'],
                        'timestamp': datetime.fromisoformat(v['timestamp']),
                        'precio_breakout': v['precio_breakout'],
                        'config': v['config']
                    } for k, v in estado.get('esperando_reentry', {}).items()
                }
                self.breakouts_detectados = {
                    k: {
                        'tipo': v['tipo'],
                        'timestamp': datetime.fromisoformat(v['timestamp']),
                        'precio_breakout': v['precio_breakout']
                    } for k, v in estado.get('breakouts_detectados', {}).items()
                }
                self.senales_enviadas = estado.get('senales_enviadas', [])
                self.total_operaciones = estado.get('total_operaciones', 0)
                self.config_optima_por_simbolo = estado.get('config_optima_por_simbolo', {})
                self.ultima_busqueda_config = {k: datetime.fromisoformat(v) for k, v in estado.get('ultima_busqueda_config', {}).items()}
                self.operaciones_desde_optimizacion = estado.get('operaciones_desde_optimizacion', 0)
                self.operaciones_cerradas_registradas = estado.get('operaciones_cerradas_registradas', [])
                self.operaciones_bitget_activas = estado.get('operaciones_bitget_activas', {})
                self.order_ids_entrada = estado.get('order_ids_entrada', {})
                self.order_ids_sl = estado.get('order_ids_sl', {})
                self.order_ids_tp = estado.get('order_ids_tp', {})
                
                print(f"✅ Estado cargado: {len(self.operaciones_activas)} operaciones activas")
        except Exception as e:
            logger.error(f"Error cargando estado: {e}")
    
    def calcular_regresion_lineal(self, x, y):
        """Calcula regresión lineal simple"""
        n = len(x)
        if n == 0:
            return None
        sum_x = sum(x)
        sum_y = sum(y)
        sum_xy = sum(xi * yi for xi, yi in zip(x, y))
        sum_x2 = sum(xi ** 2 for xi in x)
        denominador = n * sum_x2 - sum_x ** 2
        if denominador == 0:
            return None
        pendiente = (n * sum_xy - sum_x * sum_y) / denominador
        intercepto = (sum_y - pendiente * sum_x) / n
        return pendiente, intercepto
    
    def calcular_pearson_y_angulo(self, tiempos, precios):
        """Calcula correlación de Pearson y ángulo de tendencia"""
        n = len(tiempos)
        if n < 2:
            return 0, 0
        mean_t = np.mean(tiempos)
        mean_p = np.mean(precios)
        numerador = sum((t - mean_t) * (p - mean_p) for t, p in zip(tiempos, precios))
        denom_t = math.sqrt(sum((t - mean_t) ** 2 for t in tiempos))
        denom_p = math.sqrt(sum((p - mean_p) ** 2 for p in precios))
        if denom_t == 0 or denom_p == 0:
            return 0, 0
        pearson = numerador / (denom_t * denom_p)
        reg = self.calcular_regresion_lineal(tiempos, precios)
        if not reg:
            return pearson, 0
        pendiente, _ = reg
        angulo = math.degrees(math.atan(pendiente))
        return pearson, angulo
    
    def clasificar_fuerza_tendencia(self, angulo_tendencia):
        """Clasifica la fuerza de la tendencia según el ángulo"""
        angulo_abs = abs(angulo_tendencia)
        if angulo_abs >= 45:
            return "💪 Muy Fuerte", 5
        elif angulo_abs >= 30:
            return "💚 Fuerte", 4
        elif angulo_abs >= 16:
            return "🟡 Moderada", 3
        elif angulo_abs >= 8:
            return "🟠 Débil", 2
        else:
            return "🔴 Muy Débil", 1
    
    def determinar_direccion_tendencia(self, angulo_tendencia, nivel_fuerza):
        """Determina la dirección de la tendencia"""
        if nivel_fuerza < 2:
            return "➖ LATERAL"
        if angulo_tendencia > 0:
            return "🔴 BAJISTA"
        else:
            return "🟢 ALCISTA"
    
    def calcular_stochastic(self, datos_mercado):
        """Calcula el oscilador estocástico"""
        k_period = 14
        d_period = 3
        cierres = datos_mercado['cierres']
        maximos = datos_mercado['maximos']
        minimos = datos_mercado['minimos']
        if len(cierres) < k_period:
            return 50.0, 50.0
        cierres_period = cierres[-k_period:]
        maximos_period = maximos[-k_period:]
        minimos_period = minimos[-k_period:]
        max_high = max(maximos_period)
        min_low = min(minimos_period)
        if max_high == min_low:
            stoch_k = 50.0
        else:
            stoch_k = ((cierres_period[-1] - min_low) / (max_high - min_low)) * 100
        if len(cierres) >= k_period + d_period - 1:
            k_values = []
            for i in range(d_period):
                idx = -(d_period - i)
                cierres_temp = cierres[idx - k_period + 1:idx + 1]
                maximos_temp = maximos[idx - k_period + 1:idx + 1]
                minimos_temp = minimos[idx - k_period + 1:idx + 1]
                max_h = max(maximos_temp)
                min_l = min(minimos_temp)
                if max_h == min_l:
                    k_values.append(50.0)
                else:
                    k_values.append(((cierres_temp[-1] - min_l) / (max_h - min_l)) * 100)
            stoch_d = np.mean(k_values)
        else:
            stoch_d = stoch_k
        return round(stoch_k, 1), round(stoch_d, 1)
    
    def detectar_breakout(self, simbolo, info_canal, datos_mercado):
        """Detecta si el precio ha roto el canal"""
        precio_actual = datos_mercado['precio_actual']
        resistencia = info_canal['resistencia']
        soporte = info_canal['soporte']
        if precio_actual > resistencia:
            return "BREAKOUT_LONG"
        elif precio_actual < soporte:
            return "BREAKOUT_SHORT"
        return None
    
    def detectar_reentry(self, simbolo, info_canal, datos_mercado):
        """
        Detecta si el precio ha REINGRESADO al canal.
        
        🛡️ REGLAS DE ORO (NO MODIFICAR):
        - LONG: di_minus < di_plus AND stoch_k > stoch_d
        - SHORT: di_minus > di_plus AND stoch_k < stoch_d
        """
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
        di_plus = info_canal['di_plus']
        di_minus = info_canal['di_minus']
        
        tolerancia = 0.001 * precio_actual
        
        if tipo_breakout == "BREAKOUT_LONG":
            # Verificar que el precio está dentro del canal
            if soporte <= precio_actual <= resistencia:
                distancia_soporte = abs(precio_actual - soporte)
                # 🛡️ REGLA DE ORO PARA LONG: di_minus < di_plus AND stoch_k > stoch_d
                if distancia_soporte <= tolerancia and di_minus < di_plus and stoch_k > stoch_d:
                    print(f"     ✅ {simbolo} - REENTRY LONG confirmado!")
                    print(f"        📊 DI-: {di_minus:.2f} < DI+: {di_plus:.2f} ✓")
                    print(f"        📊 Stoch K: {stoch_k:.1f} > Stoch D: {stoch_d:.1f} ✓")
                    if simbolo in self.breakouts_detectados:
                        del self.breakouts_detectados[simbolo]
                    return "LONG"
                else:
                    # Debug: mostrar por qué no se cumple
                    if not (di_minus < di_plus):
                        print(f"     ❌ {simbolo} - LONG rechazado: DI- ({di_minus:.2f}) NO < DI+ ({di_plus:.2f})")
                    if not (stoch_k > stoch_d):
                        print(f"     ❌ {simbolo} - LONG rechazado: Stoch K ({stoch_k:.1f}) NO > Stoch D ({stoch_d:.1f})")
        
        elif tipo_breakout == "BREAKOUT_SHORT":
            # Verificar que el precio está dentro del canal
            if soporte <= precio_actual <= resistencia:
                distancia_resistencia = abs(precio_actual - resistencia)
                # 🛡️ REGLA DE ORO PARA SHORT: di_minus > di_plus AND stoch_k < stoch_d
                if distancia_resistencia <= tolerancia and di_minus > di_plus and stoch_k < stoch_d:
                    print(f"     ✅ {simbolo} - REENTRY SHORT confirmado!")
                    print(f"        📊 DI-: {di_minus:.2f} > DI+: {di_plus:.2f} ✓")
                    print(f"        📊 Stoch K: {stoch_k:.1f} < Stoch D: {stoch_d:.1f} ✓")
                    if simbolo in self.breakouts_detectados:
                        del self.breakouts_detectados[simbolo]
                    return "SHORT"
                else:
                    # Debug: mostrar por qué no se cumple
                    if not (di_minus > di_plus):
                        print(f"     ❌ {simbolo} - SHORT rechazado: DI- ({di_minus:.2f}) NO > DI+ ({di_plus:.2f})")
                    if not (stoch_k < stoch_d):
                        print(f"     ❌ {simbolo} - SHORT rechazado: Stoch K ({stoch_k:.1f}) NO < Stoch D ({stoch_d:.1f})")
        
        return None
    
    def calcular_niveles_entrada(self, tipo_operacion, info_canal, precio_actual):
        """Calcula niveles de entrada, SL y TP"""
        if not info_canal:
            return None, None, None
        
        resistencia = info_canal['resistencia']
        soporte = info_canal['soporte']
        ancho_canal = resistencia - soporte
        sl_porcentaje = 0.02
        
        if tipo_operacion == "LONG":
            precio_entrada = precio_actual
            stop_loss = precio_entrada * (1 - sl_porcentaje)
            take_profit = resistencia
        else:
            precio_entrada = precio_actual
            stop_loss = resistencia * (1 + sl_porcentaje)
            take_profit = soporte
        
        riesgo = abs(precio_entrada - stop_loss)
        beneficio = abs(take_profit - precio_entrada)
        ratio_rr = beneficio / riesgo if riesgo > 0 else 0
        
        if ratio_rr < 0.5:
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
                
                # Verificar que tenemos los valores de DI+, DI-, ADX
                if 'di_plus' not in info_canal or 'di_minus' not in info_canal:
                    print(f"   ⚠️ {simbolo} - Faltan indicadores DI+/DI-, recalculando...")
                    
                    df = pd.DataFrame({
                        'High': datos_mercado['maximos'][-config_optima['num_velas']:],
                        'Low': datos_mercado['minimos'][-config_optima['num_velas']:],
                        'Close': datos_mercado['cierres'][-config_optima['num_velas']:]
                    })
                    
                    resultado_adx = calcular_adx_di(df['High'], df['Low'], df['Close'], length=14)
                    
                    info_canal['di_plus'] = resultado_adx['di_plus'][-1]
                    info_canal['di_minus'] = resultado_adx['di_minus'][-1]
                    info_canal['adx'] = resultado_adx['adx'][-1]
                
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
                    f"DI+: {info_canal['di_plus']:.1f} DI-: {info_canal['di_minus']:.1f} | "
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
                
                if simbolo in self.operaciones_activas:
                    print(f"   ⏳ {simbolo} - Operación activa existente, omitiendo...")
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
        """
        Genera y envía señal de operación con info de breakout.
        
        🛡️ VERIFICACIÓN ADICIONAL DE REGLAS DE ORO:
        Antes de enviar la señal, verifica nuevamente que se cumplan las condiciones.
        """
        # 🛡️ PROTECCIÓN CRÍTICA: No generar señales en pares con operaciones activas
        if simbolo in self.operaciones_activas:
            es_manual = self.operaciones_activas[simbolo].get('operacion_manual_usuario', False)
            if es_manual:
                print(f"    👤 {simbolo} - Operación manual detectada, omitiendo señal")
            else:
                print(f"    🚫 {simbolo} - Operación automática activa, omitiendo señal")
            return
        
        if simbolo in self.senales_enviadas:
            print(f"    ⏳ {simbolo} - Señal ya procesada anteriormente, omitiendo...")
            return
        
        if precio_entrada is None or tp is None or sl is None:
            print(f"    ❌ Niveles inválidos para {simbolo}, omitiendo señal")
            return
        
        # 🛡️ DOBLE VERIFICACIÓN DE REGLAS DE ORO
        di_plus = info_canal.get('di_plus', 0)
        di_minus = info_canal.get('di_minus', 0)
        stoch_k = info_canal.get('stoch_k', 0)
        stoch_d = info_canal.get('stoch_d', 0)
        
        # Verificar que los valores existen
        if di_plus == 0 or di_minus == 0:
            print(f"    ⚠️ {simbolo} - Valores de DI+ o DI- no disponibles, omitiendo señal")
            return
        
        # 🛡️ REGLAS DE ORO - VERIFICACIÓN FINAL ANTES DE ENVIAR
        if tipo_operacion == "LONG":
            if not (di_minus < di_plus and stoch_k > stoch_d):
                print(f"    ❌ {simbolo} - SEÑAL LONG RECHAZADA en verificación final:")
                print(f"        DI-: {di_minus:.2f} < DI+: {di_plus:.2f} ? {di_minus < di_plus}")
                print(f"        Stoch K: {stoch_k:.1f} > Stoch D: {stoch_d:.1f} ? {stoch_k > stoch_d}")
                return
        elif tipo_operacion == "SHORT":
            if not (di_minus > di_plus and stoch_k < stoch_d):
                print(f"    ❌ {simbolo} - SEÑAL SHORT RECHAZADA en verificación final:")
                print(f"        DI-: {di_minus:.2f} > DI+: {di_plus:.2f} ? {di_minus > di_plus}")
                print(f"        Stoch K: {stoch_k:.1f} < Stoch D: {stoch_d:.1f} ? {stoch_k < stoch_d}")
                return
        
        print(f"    ✅ {simbolo} - Señal {tipo_operacion} APROBADA - Cumple REGLAS DE ORO")
        print(f"        📊 DI+: {di_plus:.2f}, DI-: {di_minus:.2f}")
        print(f"        📊 Stoch K: {stoch_k:.1f}, Stoch D: {stoch_d:.1f}")
        
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

🔬 <b>Indicadores ADX:</b>
📊 <b>DI+:</b> {di_plus:.2f}
📊 <b>DI-:</b> {di_minus:.2f}
📊 <b>ADX:</b> {info_canal.get('adx', 0):.2f}

⏰ <b>Hora:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

💡 <b>Estrategia:</b> BREAKOUT + REENTRY con confirmación Stochasti.
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
        
        # Ejecutar operación automáticamente si está habilitado
        operacion_bitget = None
        if self.ejecutar_operaciones_automaticas and self.bitget_client:
            print(f"     🤖 Ejecutando operación automática en BITGET FUTUROS...")
            try:
                operacion_bitget = ejecutar_operacion_bitget(
                    bitget_client=self.bitget_client,
                    simbolo=simbolo,
                    tipo_operacion=tipo_operacion,
                    capital_usd=None,
                    leverage=None
                )
                
                if operacion_bitget:
                    print(f"     ✅ Operación ejecutada en BITGET FUTUROS para {simbolo}")
                    
                    mensaje_confirmacion = f"""
🤖 <b>OPERACIÓN AUTOMÁTICA EJECUTADA - {simbolo}</b>
✅ <b>Status:</b> EJECUTADA EN BITGET FUTUROS
📊 <b>Tipo:</b> {tipo_operacion}
💰 <b>MARGIN USDT:</b> ${operacion_bitget.get('capital_usado', 0):.2f} (3% del saldo actual)
💰 <b>Saldo Total:</b> ${operacion_bitget.get('saldo_cuenta', 0):.2f}
💰 <b>Saldo Restante:</b> ${operacion_bitget.get('saldo_cuenta', 0) - operacion_bitget.get('capital_usado', 0):.2f}
📊 <b>Valor Nocional:</b> ${operacion_bitget.get('capital_usado', 0) * operacion_bitget.get('leverage', 1):.2f}
⚡ <b>Apalancamiento:</b> {operacion_bitget.get('leverage', self.leverage_por_defecto)}x
🎯 <b>Entrada:</b> {operacion_bitget.get('precio_entrada', 0):.8f}
🛑 <b>Stop Loss:</b> {operacion_bitget.get('stop_loss', 'N/A')}
🎯 <b>Take Profit:</b> {operacion_bitget.get('take_profit', 'N/A')}
📋 <b>ID Orden:</b> {operacion_bitget.get('orden_entrada', {}).get('orderId', 'N/A')}
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
                        'di_plus': di_plus,
                        'di_minus': di_minus,
                        'breakout_usado': breakout_info is not None,
                        'operacion_ejecutada': True,
                        'operacion_manual_usuario': False,
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
                'di_plus': di_plus,
                'di_minus': di_minus,
                'breakout_usado': breakout_info is not None,
                'operacion_ejecutada': False,
                'operacion_manual_usuario': False
            }
        
        self.senales_enviadas.append(simbolo)
        self.total_operaciones += 1
        self.guardar_estado()
    
    def enviar_alerta_breakout(self, simbolo, tipo_breakout, info_canal, datos_mercado, config_optima):
        """Envía alerta de breakout detectado"""
        direccion = "🔼 ALCISTA" if tipo_breakout == "BREAKOUT_LONG" else "🔽 BAJISTA"
        
        mensaje = f"""
⚡ <b>BREAKOUT DETECTADO - {simbolo}</b>

🎯 <b>Tipo:</b> {direccion}
💰 <b>Precio Actual:</b> {datos_mercado['precio_actual']:.8f}
📊 <b>Resistencia:</b> {info_canal['resistencia']:.8f}
📉 <b>Soporte:</b> {info_canal['soporte']:.8f}

📊 <b>Timeframe:</b> {config_optima['timeframe']}
🕯️ <b>Velas:</b> {config_optima['num_velas']}
📏 <b>Ancho Canal:</b> {info_canal['ancho_canal_porcentual']:.1f}%

⏰ <b>Hora:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

🔍 <b>Estado:</b> Esperando reingreso al canal para confirmar entrada...
        """
        
        token = self.config.get('telegram_token')
        chat_ids = self.config.get('telegram_chat_ids', [])
        
        if token and chat_ids:
            try:
                self._enviar_telegram_simple(mensaje, token, chat_ids)
            except Exception as e:
                print(f"     ❌ Error enviando alerta de breakout: {e}")
    
    def generar_grafico_profesional(self, simbolo, info_canal, datos_mercado, precio_entrada, tp, sl, tipo_operacion):
        """Genera un gráfico profesional para la señal"""
        try:
            # Preparar datos
            cierres = datos_mercado['cierres']
            maximos = datos_mercado['maximos']
            minimos = datos_mercado['minimos']
            tiempos = list(range(len(cierres)))
            
            # Crear figura
            fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8), 
                                          gridspec_kw={'height_ratios': [3, 1]})
            fig.patch.set_facecolor('#0a0a0a')
            
            # Configurar eje principal
            ax1.set_facecolor('#1a1a1a')
            ax1.plot(tiempos, cierres, color='white', linewidth=1.5, label='Precio')
            
            # Líneas de canal
            resistencia = [info_canal['resistencia']] * len(tiempos)
            soporte = [info_canal['soporte']] * len(tiempos)
            ax1.plot(tiempos, resistencia, 'r--', linewidth=1, alpha=0.7, label='Resistencia')
            ax1.plot(tiempos, soporte, 'g--', linewidth=1, alpha=0.7, label='Soporte')
            ax1.fill_between(tiempos, resistencia, soporte, alpha=0.1, color='blue')
            
            # Niveles de entrada, TP y SL
            if tipo_operacion == "LONG":
                ax1.axhline(y=precio_entrada, color='lime', linestyle='-', linewidth=1.5, label='Entrada')
                ax1.axhline(y=tp, color='green', linestyle='--', linewidth=1.5, label='TP')
                ax1.axhline(y=sl, color='red', linestyle='--', linewidth=1.5, label='SL')
            else:
                ax1.axhline(y=precio_entrada, color='orange', linestyle='-', linewidth=1.5, label='Entrada')
                ax1.axhline(y=tp, color='green', linestyle='--', linewidth=1.5, label='TP')
                ax1.axhline(y=sl, color='red', linestyle='--', linewidth=1.5, label='SL')
            
            # Configuración del gráfico
            ax1.set_title(f'{simbolo} - {tipo_operacion}', color='white', fontsize=14, pad=10)
            ax1.set_ylabel('Precio', color='white')
            ax1.tick_params(colors='white')
            ax1.legend(loc='best', facecolor='#2a2a2a', edgecolor='white', labelcolor='white')
            ax1.grid(True, alpha=0.2, color='white')
            
            # Stochastic en el segundo eje
            ax2.set_facecolor('#1a1a1a')
            stoch_k_values = [info_canal['stoch_k']] * len(tiempos)
            stoch_d_values = [info_canal['stoch_d']] * len(tiempos)
            ax2.plot(tiempos, stoch_k_values, color='cyan', linewidth=1.5, label='Stoch K')
            ax2.plot(tiempos, stoch_d_values, color='magenta', linewidth=1.5, label='Stoch D')
            ax2.axhline(y=80, color='red', linestyle='--', alpha=0.5)
            ax2.axhline(y=20, color='green', linestyle='--', alpha=0.5)
            ax2.fill_between(tiempos, 0, 20, alpha=0.1, color='green')
            ax2.fill_between(tiempos, 80, 100, alpha=0.1, color='red')
            ax2.set_ylabel('Stochastic', color='white')
            ax2.set_xlabel('Time', color='white')
            ax2.tick_params(colors='white')
            ax2.legend(loc='best', facecolor='#2a2a2a', edgecolor='white', labelcolor='white')
            ax2.grid(True, alpha=0.2, color='white')
            ax2.set_ylim([0, 100])
            
            plt.tight_layout()
            
            # Guardar en buffer
            buf = BytesIO()
            plt.savefig(buf, format='png', facecolor='#0a0a0a', dpi=100)
            buf.seek(0)
            plt.close()
            
            return buf
        except Exception as e:
            logger.error(f"Error generando gráfico: {e}")
            return None
    
    def enviar_grafico_telegram(self, buf, token, chat_ids):
        """Envía un gráfico por Telegram"""
        for chat_id in chat_ids:
            try:
                url = f"https://api.telegram.org/bot{token}/sendPhoto"
                files = {'photo': buf}
                data = {'chat_id': chat_id}
                requests.post(url, files=files, data=data, timeout=10)
            except Exception as e:
                logger.error(f"Error enviando gráfico a {chat_id}: {e}")
    
    def _enviar_telegram_simple(self, mensaje, token, chat_ids):
        """Envía un mensaje simple por Telegram"""
        for chat_id in chat_ids:
            try:
                url = f"https://api.telegram.org/bot{token}/sendMessage"
                data = {
                    'chat_id': chat_id,
                    'text': mensaje,
                    'parse_mode': 'HTML'
                }
                requests.post(url, json=data, timeout=10)
            except Exception as e:
                logger.error(f"Error enviando mensaje a {chat_id}: {e}")
    
    def registrar_operacion(self, datos):
        """Registra una operación en el log CSV"""
        try:
            file_exists = os.path.exists(self.config['log_path'])
            
            with open(self.config['log_path'], 'a', newline='') as f:
                fieldnames = [
                    'timestamp', 'symbol', 'tipo', 'precio_entrada', 'take_profit', 'stop_loss',
                    'precio_salida', 'resultado', 'pnl_percent', 'duracion_minutos',
                    'angulo_tendencia', 'pearson', 'r2_score', 'ancho_canal_relativo',
                    'ancho_canal_porcentual', 'nivel_fuerza', 'timeframe_utilizado',
                    'velas_utilizadas', 'stoch_k', 'stoch_d', 'di_plus', 'di_minus',
                    'breakout_usado', 'operacion_ejecutada', 'reason', 'pnl_no_realizado_final'
                ]
                
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                
                if not file_exists:
                    writer.writeheader()
                
                writer.writerow(datos)
        except Exception as e:
            logger.error(f"Error registrando operación: {e}")
    
    def generar_mensaje_cierre(self, datos):
        """Genera mensaje de cierre de operación"""
        emoji_resultado = "✅" if datos['resultado'] == "GANADA" else "❌" if datos['resultado'] == "PERDIDA" else "⚠️"
        
        return f"""
{emoji_resultado} <b>OPERACIÓN {datos['resultado']} - {datos['symbol']}</b>

📊 <b>Tipo:</b> {datos['tipo']}
💰 <b>Entrada:</b> {datos['precio_entrada']:.8f}
💰 <b>Salida:</b> {datos['precio_salida']:.8f}
📈 <b>PnL:</b> {datos['pnl_percent']:.2f}%
⏱️ <b>Duración:</b> {datos['duracion_minutos']:.1f} min

🎯 <b>Take Profit:</b> {datos['take_profit']:.8f}
🛑 <b>Stop Loss:</b> {datos['stop_loss']:.8f}

📊 <b>Indicadores:</b>
• Ángulo: {datos['angulo_tendencia']:.1f}°
• Pearson: {datos['pearson']:.3f}
• R²: {datos['r2_score']:.3f}
• Stoch K: {datos['stoch_k']:.1f}
• Stoch D: {datos['stoch_d']:.1f}
• DI+: {datos.get('di_plus', 0):.2f}
• DI-: {datos.get('di_minus', 0):.2f}

⏰ <b>Cierre:</b> {datos['timestamp']}
📝 <b>Razón:</b> {datos['reason']}
        """
    
    def verificar_tp_sl_bitget(self):
        """Verifica si alguna posición ha alcanzado TP o SL en Bitget"""
        if not self.bitget_client:
            return
        
        try:
            for simbolo in list(self.operaciones_activas.keys()):
                operacion = self.operaciones_activas[simbolo]
                
                # Solo verificar operaciones ejecutadas en Bitget
                if not operacion.get('operacion_ejecutada', False):
                    continue
                
                # Obtener posición actual
                position = self.bitget_client.get_position(simbolo)
                
                # Si no hay posición, la operación se cerró
                if not position or float(position.get('total', 0)) == 0:
                    # Verificar si ya fue procesada
                    if simbolo in self.operaciones_cerradas_registradas:
                        continue
                    
                    # Obtener precio actual para calcular PnL
                    ticker = self.bitget_client.get_ticker(simbolo)
                    if ticker:
                        precio_salida = ticker['last']
                        
                        # Determinar razón de cierre
                        tp = operacion.get('take_profit', 0)
                        sl = operacion.get('stop_loss', 0)
                        
                        if operacion['tipo'] == 'LONG':
                            if precio_salida >= tp:
                                resultado = "GANADA"
                                reason = "Take Profit alcanzado"
                            elif precio_salida <= sl:
                                resultado = "PERDIDA"
                                reason = "Stop Loss alcanzado"
                            else:
                                resultado = "CERRADA"
                                reason = "Cierre manual"
                        else:
                            if precio_salida <= tp:
                                resultado = "GANADA"
                                reason = "Take Profit alcanzado"
                            elif precio_salida >= sl:
                                resultado = "PERDIDA"
                                reason = "Stop Loss alcanzado"
                            else:
                                resultado = "CERRADA"
                                reason = "Cierre manual"
                        
                        # Procesar cierre
                        self.procesar_cierre_operacion(simbolo, resultado, reason, precio_salida)
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
                'di_plus': operacion.get('di_plus', 0),
                'di_minus': operacion.get('di_minus', 0),
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
            
            # Marcar como procesada
            self.operaciones_cerradas_registradas.append(simbolo)
            
            # Limpiar estructuras
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
        
        timeframes = self.config.get('timeframes', ['15m', '30m', '1h', '4h'])
        velas_options = self.config.get('velas_options', [80, 100, 120, 150, 200])
        
        mejor_config = None
        mejor_puntaje = -999999
        prioridad_timeframe = {'15m': 200, '30m': 150, '1h': 120, '4h': 100}
        
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
        
        if mejor_config:
            self.config_optima_por_simbolo[simbolo] = mejor_config
            self.ultima_busqueda_config[simbolo] = datetime.now()
            print(f"   ✅ Config óptima: {mejor_config['timeframe']} - {mejor_config['num_velas']} velas - Ancho: {mejor_config['ancho_canal']:.1f}%")
        
        return mejor_config
    
    def obtener_datos_mercado_config(self, simbolo, timeframe, num_velas):
        """Obtiene datos con configuración específica"""
        if self.bitget_client:
            try:
                candles = self.bitget_client.get_klines(simbolo, timeframe, num_velas + 14)
                if not candles or len(candles) == 0:
                    return None
                
                maximos = []
                minimos = []
                cierres = []
                tiempos = []
                
                for i, candle in enumerate(candles):
                    maximos.append(float(candle[2]))
                    minimos.append(float(candle[3]))
                    cierres.append(float(candle[4]))
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
                print(f"   ⚠️ Error obteniendo datos de BITGET para {simbolo}: {e}")
        
        # Fallback a Binance
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
        """Calcula canal de regresión con indicadores ADX"""
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
        
        diferencias_max = [maximos[i] - (pendiente_max * tiempos_reg[i] + intercepto_max) 
                          for i in range(len(tiempos_reg))]
        diferencias_min = [minimos[i] - (pendiente_min * tiempos_reg[i] + intercepto_min) 
                          for i in range(len(tiempos_reg))]
        
        desviacion_max = np.std(diferencias_max) if diferencias_max else 0
        desviacion_min = np.std(diferencias_min) if diferencias_min else 0
        
        resistencia_superior = resistencia_media + desviacion_max
        soporte_inferior = soporte_media - desviacion_min
        
        precio_actual = datos_mercado['precio_actual']
        pearson, angulo_tendencia = self.calcular_pearson_y_angulo(tiempos_reg, cierres)
        fuerza_texto, nivel_fuerza = self.clasificar_fuerza_tendencia(angulo_tendencia)
        direccion = self.determinar_direccion_tendencia(angulo_tendencia, 1)
        stoch_k, stoch_d = self.calcular_stochastic(datos_mercado)
        
        # Calcular ADX, DI+ y DI-
        df_indicadores = pd.DataFrame({
            'High': datos_mercado['maximos'][-candle_period:],
            'Low': datos_mercado['minimos'][-candle_period:],
            'Close': datos_mercado['cierres'][-candle_period:]
        })
        
        resultado_adx = calcular_adx_di(df_indicadores['High'], df_indicadores['Low'], 
                                       df_indicadores['Close'], length=14)
        
        di_plus = resultado_adx['di_plus'][-1]
        di_minus = resultado_adx['di_minus'][-1]
        adx = resultado_adx['adx'][-1]
        
        precio_medio = (resistencia_superior + soporte_inferior) / 2
        ancho_canal_absoluto = resistencia_superior - soporte_inferior
        ancho_canal_porcentual = (ancho_canal_absoluto / precio_medio) * 100
        
        # Calcular R² score
        predicciones = [pendiente_cierre * t + intercepto_cierre for t in tiempos_reg]
        ss_res = sum((cierres[i] - predicciones[i]) ** 2 for i in range(len(cierres)))
        ss_tot = sum((c - np.mean(cierres)) ** 2 for c in cierres)
        r2_score = 1 - (ss_res / ss_tot) if ss_tot != 0 else 0
        
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
            'stoch_k': stoch_k,
            'stoch_d': stoch_d,
            'di_plus': di_plus,
            'di_minus': di_minus,
            'adx': adx,
            'r2_score': r2_score
        }
    
    def ejecutar_analisis(self):
        """Ejecuta un ciclo de análisis del mercado"""
        try:
            # Sincronizar con Bitget primero
            if self.bitget_client:
                self.sincronizar_con_bitget()
            
            # Verificar TP/SL en Bitget
            self.verificar_tp_sl_bitget()
            
            # Escanear mercado
            senales = self.escanear_mercado()
            
            return senales
        except Exception as e:
            logger.error(f"Error en análisis: {e}", exc_info=True)
            return 0
    
    def mostrar_resumen_operaciones(self):
        """Muestra un resumen de las operaciones activas"""
        if self.operaciones_activas:
            print(f"\n📊 OPERACIONES ACTIVAS: {len(self.operaciones_activas)}")
            for simbolo, op in self.operaciones_activas.items():
                tipo_texto = "🟢 LONG" if op['tipo'] == "LONG" else "🔴 SHORT"
                es_manual = "👤 MANUAL" if op.get('operacion_manual_usuario') else "🤖 AUTO"
                print(f"   • {simbolo}: {tipo_texto} ({es_manual})")
    
    def ejecutar_bot(self):
        """Ejecuta el bot de trading"""
        print("=" * 70)
        print("🤖 BOT BREAKOUT + REENTRY CON STOCHASTIC - v23 REAL")
        print("=" * 70)
        print("🎯 ESTRATEGIA: 1) Detectar Breakout → 2) Esperar Reentry → 3) Confirmar con Stoch + ADX")
        print("🔄 REEVALUACIÓN: CADA 2 HORAS")
        print("🏦 INTEGRACIÓN: BITGET FUTUROS API (Dinero REAL)")
        print("=" * 70)
        print(f"💱 Símbolos: {len(self.config.get('symbols', []))} monedas")
        print(f"⏰ Timeframes: {', '.join(self.config.get('timeframes', []))}")
        print(f"🕯️ Velas: {self.config.get('velas_options', [])}")
        print(f"📏 ANCHO MÍNIMO: {self.config.get('min_channel_width_percent', 4)}%")
        print(f"🚀 Estrategia: 1) Detectar Breakout → 2) Esperar Reentry → 3) Confirmar con Stoch + ADX")
        
        if self.bitget_client:
            print(f"🤖 BITGET FUTUROS: ✅ API Conectada")
            print(f"⚡ Apalancamiento: {self.leverage_por_defecto}x")
            print(f"💰 MARGIN USDT: 3% del saldo actual")
            
            if self.ejecutar_operaciones_automaticas:
                print(f"🤖 AUTO-TRADING: ✅ ACTIVADO")
                print("⚠️  ADVERTENCIA: TRADING AUTOMÁTICO REAL ACTIVADO")
                confirmar = input("\n¿Continuar? (s/n): ").strip().lower()
                if confirmar not in ['s', 'si', 'sí', 'y', 'yes']:
                    print("❌ Operación cancelada")
                    return
            else:
                print(f"🤖 AUTO-TRADING: ❌ Solo señales")
        else:
            print(f"🤖 BITGET FUTUROS: ❌ No configurado")
        
        print("=" * 70)
        print("\n🚀 INICIANDO BOT...")
        
        # Sincronización inicial
        if self.bitget_client:
            print("\n🔄 REALIZANDO SINCRONIZACIÓN INICIAL...")
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
        'scan_interval_minutes': 5,
        'timeframes': ['15m', '30m', '1h', '4h'],
        'velas_options': [80, 100, 120, 150, 200],
        'symbols': [
            'PEPEUSDT', 'WIFUSDT', 'FLOKIUSDT', 'SHIBUSDT', 'POPCATUSDT',
            'CHILLGUYUSDT', 'PNUTUSDT', 'MEWUSDT', 'FARTCOINUSDT', 'DOGEUSDT',
            'VINEUSDT', 'HIPPOUSDT', 'TRXUSDT', 'XLMUSDT', 'XRPUSDT',
            'ADAUSDT', 'ATOMUSDT', 'LINKUSDT', 'UNIUSDT',
            'SUSHIUSDT', 'CRVUSDT', 'SNXUSDT', 'SANDUSDT', 'MANAUSDT',
            'AXSUSDT', 'LRCUSDT', 'ARBUSDT', 'OPUSDT', 'INJUSDT',
            'FILUSDT', 'SUIUSDT', 'AAVEUSDT', 'ENSUSDT',
            'LDOUSDT', 'POLUSDT', 'ALGOUSDT', 'QNTUSDT',
            '1INCHUSDT', 'CVCUSDT', 'STGUSDT', 'ENJUSDT', 'GALAUSDT',
            'MAGICUSDT', 'REZUSDT', 'BLURUSDT', 'HMSTRUSDT', 'BEATUSDT',
            'ZEREBROUSDT', 'ZENUSDT', 'CETUSUSDT', 'DRIFTUSDT', 'PHAUSDT',
            'API3USDT', 'ACHUSDT', 'SPELLUSDT', 'YGGUSDT',
            'GMXUSDT', 'C98USDT',
            'XMRUSDT', 'DOTUSDT', 'BNBUSDT', 'SOLUSDT', 'AVAXUSDT',
            'VETUSDT', 'BCHUSDT', 'NEOUSDT', 'TIAUSDT',
            'TONUSDT', 'TRUMPUSDT',
            'IPUSDT', 'TAOUSDT', 'XPLUSDT', 'HOLOUSDT', 'MONUSDT',
            'OGUSDT', 'MSTRUSDT', 'VIRTUALUSDT',
            'TLMUSDT', 'BOMEUSDT', 'KAITOUSDT', 'APEUSDT', 'METUSDT',
            'TUTUSDT'
        ],
        'telegram_token': os.environ.get('TELEGRAM_TOKEN'),
        'telegram_chat_ids': telegram_chat_ids,
        'auto_optimize': True,
        'min_samples_optimizacion': 15,
        'reevaluacion_horas': 6,
        'log_path': os.path.join(directorio_actual, 'operaciones_log_v23_real.csv'),
        'estado_file': os.path.join(directorio_actual, 'estado_bot_v23_real.json'),
        'bitget_api_key': os.environ.get('BITGET_API_KEY'),
        'bitget_api_secret': os.environ.get('BITGET_SECRET_KEY'),
        'bitget_passphrase': os.environ.get('BITGET_PASSPHRASE'),
        'webhook_url': os.environ.get('WEBHOOK_URL'),
        'ejecutar_operaciones_automaticas': os.environ.get('EJECUTAR_OPERACIONES_AUTOMATICAS', 'false').lower() == 'true',
        'leverage_por_defecto': min(int(os.environ.get('LEVERAGE_POR_DEFECTO', '20')), 20)
    }


# ---------------------------
# FLASK APP
# ---------------------------
app = Flask(__name__)

config = crear_config_desde_entorno()
bot = TradingBot(config)

def run_bot_loop():
    """Ejecuta el bot en un hilo separado"""
    logger.info("🤖 Iniciando hilo del bot...")
    while True:
        try:
            bot.ejecutar_analisis()
            time.sleep(bot.config.get('scan_interval_minutes', 1) * 60)
        except Exception as e:
            logger.error(f"❌ Error en el hilo del bot: {e}", exc_info=True)
            time.sleep(60)

bot_thread = threading.Thread(target=run_bot_loop, daemon=True)
bot_thread.start()

@app.route('/')
def index():
    return "✅ Bot Breakout + Reentry con REGLAS DE ORO está en línea.", 200

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

def setup_telegram_webhook():
    """Configura el webhook de Telegram"""
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
        requests.get(f"https://api.telegram.org/bot{token}/deleteWebhook", timeout=10)
        time.sleep(1)
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
