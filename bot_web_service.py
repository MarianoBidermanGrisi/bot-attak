# bot_breakout_reentry.py
# VERSIÓN COMPLETA con estrategia Breakout + Reentry para Bitget
import requests
import time
import json
import os
import hmac
import hashlib
import base64
import websocket
import threading
from datetime import datetime, timedelta
import numpy as np
import math
import csv
import itertools
import statistics
import random
import pandas as pd
from io import BytesIO
from flask import Flask, request, jsonify

# ---------------------------
# Configuración de Bitget API
# ---------------------------
class BitgetClient:
    def __init__(self, api_key=None, secret_key=None, passphrase=None):
        self.api_key = api_key or os.getenv('BITGET_API_KEY')
        self.secret_key = secret_key or os.getenv('BITGET_SECRET_KEY')
        self.passphrase = passphrase or os.getenv('BITGET_PASSPHRASE')
        self.base_url = "https://api.bitget.com"
        self.ws_public_url = "wss://ws.bitget.com/v2/ws/public"
        self.ws_private_url = "wss://ws.bitget.com/v2/ws/private"
        self.time_offset = 0
        self.ws = None
        self.ws_thread = None
        self.ws_callbacks = {}
        self.ws_subscriptions = {}
        self.symbol_data = {}
        self.positions = {}
        self.orders = {}
        
        # Sincronizar tiempo con el servidor
        self._sync_time()
        
    def _sync_time(self):
        """Sincroniza el tiempo local con el servidor Bitget"""
        try:
            response = requests.get(f"{self.base_url}/api/v2/public/time", timeout=5)
            if response.status_code == 200:
                server_time = response.json()['data']['serverTime']
                local_time = int(time.time() * 1000)
                self.time_offset = server_time - local_time
                print(f"✅ Tiempo sincronizado con servidor. Offset: {self.time_offset}ms")
        except Exception as e:
            print(f"⚠️ Error sincronizando tiempo: {e}")
            
    def _get_timestamp(self):
        """Obtiene timestamp en milisegundos ajustado con el offset del servidor"""
        return int(time.time() * 1000) + self.time_offset
        
    def _get_timestamp_seconds(self):
        """Obtiene timestamp en segundos ajustado con el offset del servidor"""
        return int(time.time()) + int(self.time_offset / 1000)
        
    def _sign(self, timestamp, method, request_path, body=""):
        """Genera firma HMAC-SHA256 para autenticación"""
        if isinstance(body, dict):
            body = json.dumps(body)
        message = timestamp + method.upper() + request_path + body
        signature = hmac.new(
            self.secret_key.encode('utf-8'),
            message.encode('utf-8'),
            hashlib.sha256
        ).digest()
        return base64.b64encode(signature).decode('utf-8')
        
    def _make_request(self, method, endpoint, params=None, data=None):
        """Realiza una petición a la API REST de Bitget"""
        url = f"{self.base_url}{endpoint}"
        timestamp = str(self._get_timestamp())
        
        # Preparar query string si hay parámetros
        query_string = ""
        if params:
            query_string = "&".join([f"{k}={v}" for k, v in params.items()])
            if query_string:
                endpoint = f"{endpoint}?{query_string}"
                
        # Preparar cuerpo de la petición
        body = ""
        if data:
            body = json.dumps(data)
            
        # Generar firma
        signature = self._sign(timestamp, method, endpoint, body)
        
        # Preparar headers
        headers = {
            "ACCESS-KEY": self.api_key,
            "ACCESS-SIGN": signature,
            "ACCESS-TIMESTAMP": timestamp,
            "ACCESS-PASSPHRASE": self.passphrase,
            "Content-Type": "application/json"
        }
        
        # Realizar petición
        try:
            if method.upper() == "GET":
                response = requests.get(url, headers=headers, params=params, timeout=10)
            elif method.upper() == "POST":
                response = requests.post(url, headers=headers, data=body, timeout=10)
            else:
                raise ValueError(f"Método HTTP no soportado: {method}")
                
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"❌ Error en petición {method} {endpoint}: {e}")
            return None
            
    def get_server_time(self):
        """Obtiene el tiempo del servidor"""
        try:
            response = requests.get(f"{self.base_url}/api/v2/public/time", timeout=5)
            if response.status_code == 200:
                return response.json()['data']['serverTime']
            return None
        except Exception as e:
            print(f"❌ Error obteniendo tiempo del servidor: {e}")
            return None
            
    def get_contract_info(self, symbol):
        """Obtiene información del contrato para un símbolo"""
        try:
            response = self._make_request("GET", "/api/v2/mix/market/contracts", {"symbol": symbol})
            if response and response.get('code') == '00000' and response.get('data'):
                return response['data'][0]  # Generalmente devuelve una lista con un elemento
            return None
        except Exception as e:
            print(f"❌ Error obteniendo información del contrato {symbol}: {e}")
            return None
            
    def set_margin_mode(self, symbol, margin_mode="isolated"):
        """Configura el modo de margen para un símbolo"""
        try:
            data = {
                "symbol": symbol,
                "marginMode": margin_mode
            }
            response = self._make_request("POST", "/api/v2/mix/account/set-margin-mode", data=data)
            if response and response.get('code') == '00000':
                print(f"✅ Modo de margen {margin_mode} configurado para {symbol}")
                return True
            return False
        except Exception as e:
            print(f"❌ Error configurando modo de margen para {symbol}: {e}")
            return False
            
    def set_position_mode(self, symbol, position_mode="one-way"):
        """Configura el modo de posición para un símbolo"""
        try:
            data = {
                "symbol": symbol,
                "positionMode": position_mode
            }
            response = self._make_request("POST", "/api/v2/mix/account/set-position-mode", data=data)
            if response and response.get('code') == '00000':
                print(f"✅ Modo de posición {position_mode} configurado para {symbol}")
                return True
            return False
        except Exception as e:
            print(f"❌ Error configurando modo de posición para {symbol}: {e}")
            return False
            
    def set_leverage(self, symbol, leverage=10):
        """Configura el apalancamiento para un símbolo"""
        try:
            data = {
                "symbol": symbol,
                "leverage": str(leverage)
            }
            response = self._make_request("POST", "/api/v2/mix/account/set-leverage", data=data)
            if response and response.get('code') == '00000':
                print(f"✅ Apalancamiento {leverage}x configurado para {symbol}")
                return True
            return False
        except Exception as e:
            print(f"❌ Error configurando apalancamiento para {symbol}: {e}")
            return False
            
    def place_order(self, symbol, side, order_type, size, price=None, margin_mode="isolated"):
        """Coloca una orden en el mercado"""
        try:
            data = {
                "symbol": symbol,
                "side": side,
                "orderType": order_type,
                "size": str(size),
                "marginMode": margin_mode
            }
            
            if price and order_type.lower() == "limit":
                data["price"] = str(price)
                
            response = self._make_request("POST", "/api/v2/mix/order/place-order", data=data)
            if response and response.get('code') == '00000':
                print(f"✅ Orden {side} {order_type} colocada para {symbol}")
                return response['data']
            return None
        except Exception as e:
            print(f"❌ Error colocando orden para {symbol}: {e}")
            return None
            
    def place_plan_order(self, symbol, plan_type, trigger_price, execute_price, size, side):
        """Coloca una orden planificada (TP/SL)"""
        try:
            data = {
                "symbol": symbol,
                "planType": plan_type,
                "triggerPrice": str(trigger_price),
                "executePrice": str(execute_price),
                "size": str(size),
                "side": side
            }
            
            response = self._make_request("POST", "/api/v2/mix/order/place-plan-order", data=data)
            if response and response.get('code') == '00000':
                print(f"✅ Orden planificada {plan_type} colocada para {symbol}")
                return response['data']
            return None
        except Exception as e:
            print(f"❌ Error colocando orden planificada para {symbol}: {e}")
            return None
            
    def get_positions(self):
        """Obtiene las posiciones abiertas"""
        try:
            response = self._make_request("GET", "/api/v2/mix/position/all-position")
            if response and response.get('code') == '00000':
                positions = {}
                for pos in response.get('data', []):
                    symbol = pos.get('symbol')
                    if symbol and float(pos.get('totalPos', 0)) != 0:
                        positions[symbol] = pos
                return positions
            return {}
        except Exception as e:
            print(f"❌ Error obteniendo posiciones: {e}")
            return {}
            
    def get_order_fills(self, symbol, limit=10):
        """Obtiene los fills de órdenes para un símbolo"""
        try:
            params = {
                "symbol": symbol,
                "limit": str(limit)
            }
            response = self._make_request("GET", "/api/v2/mix/order/fills", params=params)
            if response and response.get('code') == '00000':
                return response.get('data', [])
            return []
        except Exception as e:
            print(f"❌ Error obteniendo fills de órdenes para {symbol}: {e}")
            return []
            
    def get_historical_klines(self, symbol, interval, limit=200, start_time=None, end_time=None):
        """Obtiene velas históricas"""
        try:
            params = {
                "symbol": symbol,
                "granularity": interval,
                "limit": str(limit)
            }
            
            if start_time:
                params["startTime"] = str(start_time)
            if end_time:
                params["endTime"] = str(end_time)
                
            response = self._make_request("GET", "/api/v2/mix/market/candles", params=params)
            if response and response.get('code') == '00000':
                # Convertir a formato estándar [timestamp, open, high, low, close, volume]
                candles = []
                for item in response.get('data', []):
                    # Bitget devuelve: [timestamp, open, high, low, close, volume]
                    candles.append(item)
                return candles
            return []
        except Exception as e:
            print(f"❌ Error obteniendo velas históricas para {symbol}: {e}")
            return []
            
    def start_websocket(self, private=False):
        """Inicia la conexión WebSocket"""
        url = self.ws_private_url if private else self.ws_public_url
        
        def on_message(ws, message):
            try:
                data = json.loads(message)
                
                if data.get('event') == 'ping':
                    ws.send(json.dumps({'op': 'pong'}))
                    return
                    
                if data.get('event') == 'error':
                    print(f"❌ Error WebSocket: {data}")
                    return
                    
                channel = data.get('arg', {}).get('channel')
                if channel in self.ws_callbacks:
                    self.ws_callbacks[channel](data)
                    
            except Exception as e:
                print(f"❌ Error procesando mensaje WebSocket: {e}")
                
        def on_error(ws, error):
            print(f"❌ Error WebSocket: {error}")
            
        def on_close(ws, close_status_code, close_msg):
            print(f"🔌 WebSocket cerrado: {close_status_code} - {close_msg}")
            
        def on_open(ws):
            print(f"✅ WebSocket {'privado' if private else 'público'} conectado")
            
            # Autenticación para WebSocket privado
            if private:
                timestamp = str(self._get_timestamp_seconds())
                message = timestamp + "GET" + "/user/verify"
                signature = hmac.new(
                    self.secret_key.encode('utf-8'),
                    message.encode('utf-8'),
                    hashlib.sha256
                ).digest()
                sign = base64.b64encode(signature).decode('utf-8')
                
                auth_msg = {
                    "op": "login",
                    "args": [{
                        "apiKey": self.api_key,
                        "passphrase": self.passphrase,
                        "timestamp": timestamp,
                        "sign": sign
                    }]
                }
                ws.send(json.dumps(auth_msg))
                
            # Re-suscribir a canales anteriores
            for channel_id, params in self.ws_subscriptions.items():
                sub_msg = {
                    "op": "subscribe",
                    "args": [params]
                }
                ws.send(json.dumps(sub_msg))
                
        # Crear y configurar WebSocket
        self.ws = websocket.WebSocketApp(
            url,
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close
        )
        
        # Iniciar en un hilo separado
        self.ws_thread = threading.Thread(target=self.ws.run_forever)
        self.ws_thread.daemon = True
        self.ws_thread.start()
        
        # Enviar ping cada 30 segundos para mantener conexión
        def ping_loop():
            while self.ws:
                try:
                    self.ws.send(json.dumps({'op': 'ping'}))
                    time.sleep(30)
                except Exception as e:
                    print(f"❌ Error enviando ping: {e}")
                    break
                    
        ping_thread = threading.Thread(target=ping_loop)
        ping_thread.daemon = True
        ping_thread.start()
        
    def subscribe(self, channel, inst_id, inst_type="MC", callback=None):
        """Suscribe a un canal WebSocket"""
        params = {
            "channel": channel,
            "instId": inst_id,
            "instType": inst_type
        }
        
        channel_id = f"{channel}_{inst_id}"
        self.ws_subscriptions[channel_id] = params
        
        if callback:
            self.ws_callbacks[channel_id] = callback
            
        if self.ws:
            sub_msg = {
                "op": "subscribe",
                "args": [params]
            }
            self.ws.send(json.dumps(sub_msg))
            
    def close_websocket(self):
        """Cierra la conexión WebSocket"""
        if self.ws:
            self.ws.close()
            self.ws = None
            if self.ws_thread and self.ws_thread.is_alive():
                self.ws_thread.join(timeout=5)

# ---------------------------
# Optimizador IA
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
# BOT PRINCIPAL - BREAKOUT + REENTRY
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

        # Cliente de Bitget
        self.bitget = BitgetClient()
        
        # Iniciar WebSocket público para datos de mercado
        self.bitget.start_websocket(private=False)
        
        # Iniciar WebSocket privado para órdenes y posiciones
        self.bitget.start_websocket(private=True)
        
        # Suscribirse a canales relevantes
        self.setup_websocket_subscriptions()
        
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
        
        # Configurar apalancamiento y modos para símbolos
        self.setup_symbols()

    def setup_websocket_subscriptions(self):
        """Configura las suscripciones WebSocket para datos de mercado"""
        # Callback para velas
        def on_candle(data):
            try:
                if data.get('data') and len(data['data']) > 0:
                    candle_data = data['data'][0]
                    symbol = data['arg']['instId']
                    
                    if symbol not in self.ultimos_datos:
                        self.ultimos_datos[symbol] = {}
                    
                    # Actualizar datos del símbolo
                    self.ultimos_datos[symbol]['last_candle'] = candle_data
                    self.ultimos_datos[symbol]['last_update'] = datetime.now()
            except Exception as e:
                print(f"❌ Error procesando datos de vela: {e}")
        
        # Callback para posiciones
        def on_position(data):
            try:
                if data.get('data'):
                    for pos_data in data['data']:
                        symbol = pos_data.get('symbol')
                        if symbol:
                            self.bitget.positions[symbol] = pos_data
            except Exception as e:
                print(f"❌ Error procesando datos de posición: {e}")
        
        # Callback para órdenes
        def on_order(data):
            try:
                if data.get('data'):
                    for order_data in data['data']:
                        symbol = order_data.get('symbol')
                        order_id = order_data.get('orderId')
                        if symbol and order_id:
                            self.bitget.orders[order_id] = order_data
            except Exception as e:
                print(f"❌ Error procesando datos de orden: {e}")
        
        # Suscribirse a canales para cada símbolo
        for symbol in self.config.get('symbols', []):
            # Velas de 1 minuto
            self.bitget.subscribe("candle1m", symbol, "MC", on_candle)
        
        # Suscribirse a canales privados
        self.bitget.subscribe("positions", "", "", on_position)
        self.bitget.subscribe("orders", "", "", on_order)
        
    def setup_symbols(self):
        """Configura los símbolos para trading"""
        leverage = self.config.get('leverage', 10)
        margin_mode = self.config.get('margin_mode', 'isolated')
        position_mode = self.config.get('position_mode', 'one-way')
        
        for symbol in self.config.get('symbols', []):
            # Obtener información del contrato
            contract_info = self.bitget.get_contract_info(symbol)
            if not contract_info:
                print(f"❌ No se pudo obtener información del contrato para {symbol}")
                continue
                
            # Guardar información del contrato para uso posterior
            if symbol not in self.ultimos_datos:
                self.ultimos_datos[symbol] = {}
            self.ultimos_datos[symbol]['contract_info'] = contract_info
            
            # Configurar modo de margen
            if not self.bitget.set_margin_mode(symbol, margin_mode):
                print(f"❌ Error configurando modo de margen para {symbol}")
                
            # Configurar modo de posición
            if not self.bitget.set_position_mode(symbol, position_mode):
                print(f"❌ Error configurando modo de posición para {symbol}")
                
            # Configurar apalancamiento
            if not self.bitget.set_leverage(symbol, leverage):
                print(f"❌ Error configurando apalancamiento para {symbol}")
                
            # Obtener datos históricos iniciales
            self.cargar_datos_historicos(symbol)
            
    def cargar_datos_historicos(self, symbol):
        """Carga datos históricos iniciales para un símbolo"""
        try:
            # Obtener velas de diferentes timeframes
            timeframes = self.config.get('timeframes', ['1m', '3m', '5m', '15m', '30m'])
            velas_options = self.config.get('velas_options', [80, 100, 120, 150, 200])
            
            if symbol not in self.ultimos_datos:
                self.ultimos_datos[symbol] = {}
                
            self.ultimos_datos[symbol]['historical_data'] = {}
            
            for timeframe in timeframes:
                for num_velas in velas_options:
                    # Mapear timeframe a granularidad de Bitget
                    granularity_map = {
                        '1m': '1m',
                        '3m': '3m',
                        '5m': '5m',
                        '15m': '15m',
                        '30m': '30m'
                    }
                    granularity = granularity_map.get(timeframe, '1m')
                    
                    # Obtener velas históricas
                    candles = self.bitget.get_historical_klines(symbol, granularity, num_velas)
                    
                    if candles:
                        if timeframe not in self.ultimos_datos[symbol]['historical_data']:
                            self.ultimos_datos[symbol]['historical_data'][timeframe] = {}
                            
                        self.ultimos_datos[symbol]['historical_data'][timeframe][num_velas] = candles
                        
            print(f"✅ Datos históricos cargados para {symbol}")
        except Exception as e:
            print(f"❌ Error cargando datos históricos para {symbol}: {e}")

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
                self.senales_enviadas = set(estado.get('senales_enviados', []))
                
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
                'senales_enviados': list(self.senales_enviadas),
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
        """Obtiene datos con configuración específica"""
        try:
            # Primero intentar obtener datos del WebSocket si están disponibles
            if (simbolo in self.ultimos_datos and 
                'historical_data' in self.ultimos_datos[simbolo] and
                timeframe in self.ultimos_datos[simbolo]['historical_data'] and
                num_velas in self.ultimos_datos[simbolo]['historical_data'][timeframe]):
                
                candles = self.ultimos_datos[simbolo]['historical_data'][timeframe][num_velas]
                
                # Convertir a formato necesario
                maximos = [float(candle[2]) for candle in candles]
                minimos = [float(candle[3]) for candle in candles]
                cierres = [float(candle[4]) for candle in candles]
                tiempos = list(range(len(candles)))
                
                return {
                    'maximos': maximos,
                    'minimos': minimos,
                    'cierres': cierres,
                    'tiempos': tiempos,
                    'precio_actual': cierres[-1] if cierres else 0,
                    'timeframe': timeframe,
                    'num_velas': num_velas
                }
            
            # Si no hay datos en caché, obtener de la API
            # Mapear timeframe a granularidad de Bitget
            granularity_map = {
                '1m': '1m',
                '3m': '3m',
                '5m': '5m',
                '15m': '15m',
                '30m': '30m'
            }
            granularity = granularity_map.get(timeframe, '1m')
            
            # Obtener velas de la API
            candles = self.bitget.get_historical_klines(simbolo, granularity, num_velas)
            
            if not candles:
                return None
                
            # Convertir a formato necesario
            maximos = [float(candle[2]) for candle in candles]
            minimos = [float(candle[3]) for candle in candles]
            cierres = [float(candle[4]) for candle in candles]
            tiempos = list(range(len(candles)))
            
            # Guardar en caché para uso futuro
            if simbolo not in self.ultimos_datos:
                self.ultimos_datos[simbolo] = {}
            if 'historical_data' not in self.ultimos_datos[simbolo]:
                self.ultimos_datos[simbolo]['historical_data'] = {}
            if timeframe not in self.ultimos_datos[simbolo]['historical_data']:
                self.ultimos_datos[simbolo]['historical_data'][timeframe] = {}
                
            self.ultimos_datos[simbolo]['historical_data'][timeframe][num_velas] = candles
            
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
            print(f"❌ Error obteniendo datos para {simbolo}: {e}")
            return None

    def calcular_regresion_lineal(self, x, y):
        """Calcula la regresión lineal simple"""
        try:
            n = len(x)
            if n == 0:
                return None
                
            sum_x = sum(x)
            sum_y = sum(y)
            sum_xy = sum(x[i] * y[i] for i in range(n))
            sum_x2 = sum(x[i] ** 2 for i in range(n))
            
            # Calcular pendiente e intercepción
            denominator = n * sum_x2 - sum_x ** 2
            if denominator == 0:
                return None
                
            slope = (n * sum_xy - sum_x * sum_y) / denominator
            intercept = (sum_y - slope * sum_x) / n
            
            return slope, intercept
        except Exception:
            return None

    def calcular_pearson_y_angulo(self, x, y):
        """Calcula el coeficiente de Pearson y el ángulo de la tendencia"""
        try:
            n = len(x)
            if n < 2:
                return 0, 0
                
            # Calcular medias
            mean_x = sum(x) / n
            mean_y = sum(y) / n
            
            # Calcular covarianza y desviaciones estándar
            covariance = sum((x[i] - mean_x) * (y[i] - mean_y) for i in range(n))
            std_x = math.sqrt(sum((x[i] - mean_x) ** 2 for i in range(n)))
            std_y = math.sqrt(sum((y[i] - mean_y) ** 2 for i in range(n)))
            
            # Calcular coeficiente de Pearson
            if std_x == 0 or std_y == 0:
                return 0, 0
                
            pearson = covariance / (std_x * std_y)
            
            # Calcular pendiente para obtener el ángulo
            slope, _ = self.calcular_regresion_lineal(x, y)
            
            # Convertir pendiente a ángulo en grados
            angle_rad = math.atan(slope)
            angle_deg = math.degrees(angle_rad)
            
            return pearson, angle_deg
        except Exception:
            return 0, 0

    def calcular_r2(self, y, x, slope, intercept):
        """Calcula el coeficiente de determinación R²"""
        try:
            n = len(y)
            if n == 0:
                return 0
                
            # Calcular valores predichos
            y_pred = [slope * x[i] + intercept for i in range(n)]
            
            # Calcular media de y
            mean_y = sum(y) / n
            
            # Calcular suma de cuadrados total y residual
            ss_total = sum((y[i] - mean_y) ** 2 for i in range(n))
            ss_residual = sum((y[i] - y_pred[i]) ** 2 for i in range(n))
            
            # Calcular R²
            if ss_total == 0:
                return 0
                
            r2 = 1 - (ss_residual / ss_total)
            return r2
        except Exception:
            return 0

    def clasificar_fuerza_tendencia(self, angulo):
        """Clasifica la fuerza de la tendencia según el ángulo"""
        abs_angulo = abs(angulo)
        
        if abs_angulo >= 30:
            return "MUY FUERTE", 4
        elif abs_angulo >= 20:
            return "FUERTE", 3
        elif abs_angulo >= 10:
            return "MODERADA", 2
        else:
            return "DÉBIL", 1

    def determinar_direccion_tendencia(self, angulo, min_angulo=1):
        """Determina la dirección de la tendencia según el ángulo"""
        if angulo > min_angulo:
            return "🟢 ALCISTA"
        elif angulo < -min_angulo:
            return "🔴 BAJISTA"
        else:
            return "🟡 LATERAL"

    def calcular_stochastic(self, datos_mercado, k_period=14, d_period=3):
        """Calcula el indicador Stochastic"""
        try:
            if not datos_mercado or len(datos_mercado['cierres']) < k_period:
                return 50, 50
                
            cierres = datos_mercado['cierres']
            maximos = datos_mercado['maximos']
            minimos = datos_mercado['minimos']
            
            # Calcular %K
            k_values = []
            for i in range(k_period - 1, len(cierres)):
                highest_high = max(maximos[i - k_period + 1:i + 1])
                lowest_low = min(minimos[i - k_period + 1:i + 1])
                
                if highest_high == lowest_low:
                    k = 50
                else:
                    k = 100 * (cierres[i] - lowest_low) / (highest_high - lowest_low)
                    
                k_values.append(k)
            
            # Suavizar %K
            k_smoothed = []
            for i in range(len(k_values)):
                if i < d_period - 1:
                    k_smoothed.append(k_values[i])
                else:
                    k_avg = sum(k_values[i - d_period + 1:i + 1]) / d_period
                    k_smoothed.append(k_avg)
            
            # Calcular %D como media móvil de %K
            d_values = []
            for i in range(len(k_smoothed)):
                if i < d_period - 1:
                    d_values.append(k_smoothed[i])
                else:
                    d_avg = sum(k_smoothed[i - d_period + 1:i + 1]) / d_period
                    d_values.append(d_avg)
            
            # Devolver los últimos valores
            if k_smoothed and d_values:
                return k_smoothed[-1], d_values[-1]
            else:
                return 50, 50
        except Exception:
            return 50, 50

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
        Envía alerta de BREAKOUT detectado a Telegram SIN gráfico
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
                print(f"     📨 Enviando alerta de breakout por Telegram...")
                self._enviar_telegram_simple(mensaje, token, chat_ids)
                print(f"     ✅ Alerta de breakout enviada para {simbolo}")
                    
            except Exception as e:
                print(f"     ❌ Error enviando alerta de breakout: {e}")
        else:
            print(f"     📢 Breakout detectado en {simbolo} (sin Telegram)")

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
        
        if direccion == "🟢 ALCISTA" and nivel_fuerza >= 2:
            if precio_cierre < soporte:
                print(f"     🚀 {simbolo} - BREAKOUT: {precio_cierre:.8f} < Soporte: {soporte:.8f}")
                return "BREAKOUT_LONG"
        
        elif direccion == "🔴 BAJISTA" and nivel_fuerza >= 2:
            if precio_cierre > resistencia:
                print(f"     📉 {simbolo} - BREAKOUT: {precio_cierre:.8f} > Resistencia: {resistencia:.8f}")
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
            # Limpiar también de breakouts_detectados cuando expira el reentry
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
                    # Limpiar breakouts_detectados cuando se confirma reentry
                    if simbolo in self.breakouts_detectados:
                        del self.breakouts_detectados[simbolo]
                    return "LONG"
        
        elif tipo_breakout == "BREAKOUT_SHORT":
            if soporte <= precio_actual <= resistencia:
                distancia_resistencia = abs(precio_actual - resistencia)
                
                if distancia_resistencia <= tolerancia and stoch_k >= 70 and stoch_d >= 70:
                    print(f"     ✅ {simbolo} - REENTRY SHORT confirmado! Entrada en resistencia con Stoch overbought")
                    # Limpiar breakouts_detectados cuando se confirma reentry
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

    def ejecutar_orden(self, simbolo, tipo_operacion, precio_entrada, tp, sl):
        """Ejecuta una orden en Bitget"""
        try:
            # Obtener información del contrato para calcular tamaño correcto
            contract_info = None
            if simbolo in self.ultimos_datos and 'contract_info' in self.ultimos_datos[simbolo]:
                contract_info = self.ultimos_datos[simbolo]['contract_info']
            else:
                contract_info = self.bitget.get_contract_info(simbolo)
                if not contract_info:
                    print(f"❌ No se pudo obtener información del contrato para {simbolo}")
                    return False
                    
                # Guardar información del contrato
                if simbolo not in self.ultimos_datos:
                    self.ultimos_datos[simbolo] = {}
                self.ultimos_datos[simbolo]['contract_info'] = contract_info
            
            # Extraer parámetros del contrato
            min_order_sz = float(contract_info.get('minOrderSz', 0.001))
            max_order_sz = float(contract_info.get('maxOrderSz', 1000000))
            price_tick = float(contract_info.get('priceTick', 0.0001))
            lot_sz = float(contract_info.get('lotSz', 0.001))
            
            # Calcular tamaño de la orden basado en el margen y apalancamiento
            margin = self.config.get('order_margin_usdt', 2)  # Margen en USDT
            leverage = self.config.get('leverage', 10)
            notional = margin * leverage
            
            # Calcular tamaño bruto
            size_raw = notional / precio_entrada
            
            # Redondear según lot_sz
            size = round(size_raw / lot_sz) * lot_sz
            
            # Verificar límites
            if size < min_order_sz:
                size = min_order_sz
                print(f"⚠️ Tamaño ajustado al mínimo: {size}")
            elif size > max_order_sz:
                size = max_order_sz
                print(f"⚠️ Tamaño ajustado al máximo: {size}")
            
            # Redondear precios según price_tick
            entry_price = round(precio_entrada / price_tick) * price_tick
            tp_price = round(tp / price_tick) * price_tick
            sl_price = round(sl / price_tick) * price_tick
            
            # Determinar lado de la orden
            side = "buy" if tipo_operacion == "LONG" else "sell"
            
            # Colocar orden de mercado
            order_result = self.bitget.place_order(
                symbol=simbolo,
                side=side,
                order_type="market",
                size=size
            )
            
            if not order_result:
                print(f"❌ Error colocando orden de entrada para {simbolo}")
                return False
                
            order_id = order_result.get('orderId')
            print(f"✅ Orden de entrada colocada: {order_id}")
            
            # Pequeña pausa para asegurar que la orden se ejecute
            time.sleep(1)
            
            # Colocar órdenes de TP y SL
            # Para TP
            tp_side = "sell" if tipo_operacion == "LONG" else "buy"
            tp_order = self.bitget.place_plan_order(
                symbol=simbolo,
                plan_type="normal",
                trigger_price=tp_price,
                execute_price=tp_price,
                size=size,
                side=tp_side
            )
            
            if tp_order:
                tp_order_id = tp_order.get('orderId')
                print(f"✅ Orden de TP colocada: {tp_order_id}")
            else:
                print(f"❌ Error colocando orden de TP para {simbolo}")
            
            # Para SL
            sl_side = "sell" if tipo_operacion == "LONG" else "buy"
            sl_order = self.bitget.place_plan_order(
                symbol=simbolo,
                plan_type="normal",
                trigger_price=sl_price,
                execute_price=sl_price,
                size=size,
                side=sl_side
            )
            
            if sl_order:
                sl_order_id = sl_order.get('orderId')
                print(f"✅ Orden de SL colocada: {sl_order_id}")
            else:
                print(f"❌ Error colocando orden de SL para {simbolo}")
            
            return True
        except Exception as e:
            print(f"❌ Error ejecutando orden para {simbolo}: {e}")
            return False

    def escanear_mercado(self):
        """Escanea el mercado con estrategia Breakout + Reentry"""
        print(f"\n🔍 Escaneando {len(self.config.get('symbols', []))} símbolos (Estrategia: Breakout + Reentry)...")
        senales_encontradas = 0
        
        for simbolo in self.config.get('symbols', []):
            try:
                # Verificar si ya hay una posición abierta para este símbolo
                if simbolo in self.bitget.positions:
                    print(f"   ⚡ {simbolo} - Posición abierta, omitiendo...")
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
                        # Registrar el breakout detectado para evitar repeticiones
                        self.breakouts_detectados[simbolo] = {
                            'tipo': tipo_breakout,
                            'timestamp': datetime.now(),
                            'precio_breakout': precio_actual
                        }
                        print(f"     🎯 {simbolo} - Breakout registrado, esperando reingreso...")
                        
                        # Enviar alerta de breakout a Telegram
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
                
                # Ejecutar orden
                if self.ejecutar_orden(simbolo, tipo_operacion, precio_entrada, tp, sl):
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
        
        # Mostrar breakouts detectados recientemente
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
                print(f"     📨 Enviando señal por Telegram...")
                self._enviar_telegram_simple(mensaje, token, chat_ids)
                print(f"     ✅ Señal {tipo_operacion} para {simbolo} enviada")
            except Exception as e:
                print(f"     ❌ Error enviando señal: {e}")
        
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
            'breakout_usado': breakout_info is not None
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
                    'stoch_k', 'stoch_d', 'breakout_usado'
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
                datos_operacion.get('breakout_usado', False)
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
                                'breakout_usado': row.get('breakout_usado', 'False') == 'True'
                            })
                    except Exception:
                        continue
                        
            return ops_recientes
        except Exception as e:
            print(f"❌ Error filtrando operaciones: {e}")
            return []

    def _enviar_telegram_simple(self, mensaje, token, chat_ids):
        """Envía un mensaje simple a Telegram"""
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        
        for chat_id in chat_ids:
            try:
                data = {
                    'chat_id': chat_id,
                    'text': mensaje,
                    'parse_mode': 'HTML'
                }
                response = requests.post(url, json=data, timeout=10)
                if response.status_code != 200:
                    print(f"❌ Error enviando a Telegram: {response.text}")
            except Exception as e:
                print(f"❌ Error enviando mensaje a Telegram: {e}")

    def run(self):
        """Bucle principal del bot"""
        print("🚀 Bot de Trading Breakout + Reentry iniciado")
        print("📡 Usando WebSocket para datos en tiempo real")
        
        while True:
            try:
                # Escanear mercado
                self.escanear_mercado()
                
                # Guardar estado
                self.guardar_estado()
                
                # Esperar antes del siguiente ciclo
                time.sleep(self.config.get('scan_interval', 60))
                
            except KeyboardInterrupt:
                print("\n👋 Bot detenido por el usuario")
                break
            except Exception as e:
                print(f"❌ Error en el bucle principal: {e}")
                time.sleep(10)
        
        # Limpiar recursos
        self.bitget.close_websocket()

# ---------------------------
# Web Service para Render.com
# ---------------------------
app = Flask(__name__)
bot_instance = None

@app.route('/')
def home():
    return jsonify({
        'status': 'running',
        'message': 'Bitget Trading Bot - Breakout + Reentry Strategy',
        'timestamp': datetime.now().isoformat()
    })

@app.route('/health')
def health_check():
    return jsonify({'status': 'healthy'}), 200

@app.route('/start', methods=['POST'])
def start_bot():
    global bot_instance
    
    if bot_instance and bot_instance.bitget:
        return jsonify({'error': 'Bot already running'}), 400
    
    try:
        # Configuración desde variables de entorno
        config = {
            'symbols': os.getenv('SYMBOLS', 'BTCUSDT,ETHUSDT').split(','),
            'telegram_token': os.getenv('TELEGRAM_TOKEN'),
            'telegram_chat_ids': os.getenv('TELEGRAM_CHAT_IDS', '').split(','),
            'leverage': int(os.getenv('LEVERAGE', '10')),
            'order_margin_usdt': float(os.getenv('ORDER_MARGIN_USDT', '2')),
            'margin_mode': os.getenv('MARGIN_MODE', 'isolated'),
            'position_mode': os.getenv('POSITION_MODE', 'one-way'),
            'scan_interval': int(os.getenv('SCAN_INTERVAL', '60')),
            'min_channel_width_percent': float(os.getenv('MIN_CHANNEL_WIDTH_PERCENT', '4.0')),
            'min_rr_ratio': float(os.getenv('MIN_RR_RATIO', '1.2')),
            'auto_optimize': os.getenv('AUTO_OPTIMIZE', 'true').lower() == 'true',
            'min_samples_optimizacion': int(os.getenv('MIN_SAMPLES_OPTIMIZACION', '15')),
            'timeframes': os.getenv('TIMEFRAMES', '1m,3m,5m,15m,30m').split(','),
            'velas_options': [int(x) for x in os.getenv('VELAS_OPTIONS', '80,100,120,150,200').split(',')],
            'trend_threshold_degrees': float(os.getenv('TREND_THRESHOLD_DEGREES', '13')),
            'min_trend_strength_degrees': float(os.getenv('MIN_TREND_STRENGTH_DEGREES', '16')),
            'entry_margin': float(os.getenv('ENTRY_MARGIN', '0.001')),
            'log_path': os.getenv('LOG_PATH', 'operaciones_log.csv'),
            'estado_file': os.getenv('ESTADO_FILE', 'estado_bot.json')
        }
        
        # Iniciar bot en un hilo separado
        bot_instance = TradingBot(config)
        bot_thread = threading.Thread(target=bot_instance.run)
        bot_thread.daemon = True
        bot_thread.start()
        
        return jsonify({
            'status': 'started',
            'config': {
                'symbols': config['symbols'],
                'leverage': config['leverage'],
                'margin': config['order_margin_usdt']
            }
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/stop', methods=['POST'])
def stop_bot():
    global bot_instance
    
    if not bot_instance:
        return jsonify({'error': 'Bot not running'}), 400
    
    try:
        if bot_instance.bitget:
            bot_instance.bitget.close_websocket()
        bot_instance = None
        return jsonify({'status': 'stopped'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/status')
def status():
    global bot_instance
    
    if not bot_instance:
        return jsonify({'status': 'stopped'})
    
    try:
        positions = bot_instance.bitget.get_positions()
        waiting_reentry = len(bot_instance.esperando_reentry)
        breakouts_detected = len(bot_instance.breakouts_detectados)
        
        return jsonify({
            'status': 'running',
            'positions': len(positions),
            'waiting_reentry': waiting_reentry,
            'breakouts_detected': breakouts_detected,
            'total_operations': bot_instance.total_operaciones
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
