# bot_web_service.py
# Adaptación para Render del bot Breakout + Reentry con API de Bitget
import requests
import time
import json
import os
import sys
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
from flask import Flask, request, jsonify
import logging

# Configurar logging básico
logging.basicConfig(level=logging.INFO, stream=sys.stdout, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ---------------------------
# [INICIO DEL CÓDIGO DEL BOT NUEVO]
# Adaptado para Bitget API
# ---------------------------

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
        
        # Configuración de API de Bitget
        self.api_key = os.environ.get('BITGET_API_KEY')
        self.secret_key = os.environ.get('BITGET_SECRET_KEY')
        self.passphrase = os.environ.get('BITGET_PASSPHRASE')
        self.base_url = "https://api.bitget.com"
        self.ws_public_url = "wss://ws.bitget.com/v2/ws/public"
        self.ws_private_url = "wss://ws.bitget.com/v2/ws/private"
        
        # WebSocket connections
        self.ws_public = None
        self.ws_private = None
        self.ws_thread = None
        self.ws_connected = False
        self.last_ping = time.time()
        self.ws_reconnect_attempts = 0
        self.max_reconnect_attempts = 5
        
        # Rate limiting
        self.last_request_time = 0
        self.request_interval = 0.1  # 100ms between requests to respect rate limits
        
        # Contract information cache
        self.contract_info = {}
        
        self.cargar_estado()
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
        
        # Inicializar WebSocket para datos de mercado
        self.inicializar_websocket()

    def generar_firma(self, timestamp, method, request_path, body=""):
        """Generar firma para API de Bitget"""
        message = timestamp + method + request_path + body
        signature = hmac.new(
            self.secret_key.encode('utf-8'),
            message.encode('utf-8'),
            hashlib.sha256
        ).digest()
        return base64.b64encode(signature).decode('utf-8')

    def hacer_request(self, method, endpoint, params=None, data=None):
        """Realizar request a API de Bitget con autenticación"""
        # Rate limiting
        current_time = time.time()
        if current_time - self.last_request_time < self.request_interval:
            time.sleep(self.request_interval - (current_time - self.last_request_time))
        self.last_request_time = time.time()
        
        url = f"{self.base_url}{endpoint}"
        timestamp = str(int(time.time() * 1000))
        
        headers = {
            "ACCESS-KEY": self.api_key,
            "ACCESS-PASSPHRASE": self.passphrase,
            "ACCESS-TIMESTAMP": timestamp,
            "Content-Type": "application/json"
        }
        
        if params:
            query_string = "&".join([f"{k}={v}" for k, v in params.items()])
            request_path = f"{endpoint}?{query_string}"
        else:
            request_path = endpoint
            
        body = json.dumps(data) if data else ""
        headers["ACCESS-SIGN"] = self.generar_firma(timestamp, method.upper(), request_path, body)
        
        try:
            if method.upper() == "GET":
                response = requests.get(url, headers=headers, params=params, timeout=10)
            elif method.upper() == "POST":
                response = requests.post(url, headers=headers, json=data, timeout=10)
            else:
                raise ValueError(f"Método no soportado: {method}")
                
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"Error en request a {endpoint}: {e}")
            return None

    def inicializar_websocket(self):
        """Inicializar conexiones WebSocket para datos de mercado"""
        def ws_thread():
            while self.ws_reconnect_attempts < self.max_reconnect_attempts:
                try:
                    print(f"🔌 Intentando conectar WebSocket (intento {self.ws_reconnect_attempts + 1})...")
                    self.ws_public = websocket.WebSocketApp(
                        self.ws_public_url,
                        on_open=self.on_ws_open,
                        on_message=self.on_ws_message,
                        on_error=self.on_ws_error,
                        on_close=self.on_ws_close
                    )
                    
                    # Iniciar conexión
                    self.ws_public.run_forever()
                    break  # Si la conexión fue exitosa, salir del bucle
                    
                except Exception as e:
                    print(f"❌ Error en conexión WebSocket: {e}")
                    self.ws_reconnect_attempts += 1
                    if self.ws_reconnect_attempts < self.max_reconnect_attempts:
                        print(f"🔄 Reintentando en 10 segundos...")
                        time.sleep(10)
                    else:
                        print("❌ Máximo de intentos de reconexión alcanzado")
            
        self.ws_thread = threading.Thread(target=ws_thread, daemon=True)
        self.ws_thread.start()

    def on_ws_open(self, ws):
        """Manejar apertura de WebSocket"""
        print("✅ WebSocket conectado")
        self.ws_connected = True
        self.ws_reconnect_attempts = 0  # Resetear contador de reconexiones
        self.last_ping = time.time()
        
        # Suscribir a los canales necesarios
        self.suscribir_canales()

    def on_ws_message(self, ws, message):
        """Manejar mensajes de WebSocket"""
        try:
            data = json.loads(message)
            
            # Manejar ping/pong
            if data.get("event") == "ping":
                ws.send(json.dumps({"op": "pong"}))
                self.last_ping = time.time()
                return
                
            # Procesar datos de velas
            if data.get("arg") and data.get("arg").get("channel") == "candle1m":
                self.procesar_datos_vela(data)
                
        except Exception as e:
            print(f"Error procesando mensaje WebSocket: {e}")

    def on_ws_error(self, ws, error):
        """Manejar errores de WebSocket"""
        print(f"❌ Error WebSocket: {error}")
        self.ws_connected = False

    def on_ws_close(self, ws, close_status_code, close_msg):
        """Manejar cierre de WebSocket"""
        print("🔴 WebSocket desconectado")
        self.ws_connected = False
        
        # Intentar reconectar después de 5 segundos
        time.sleep(5)
        self.inicializar_websocket()

    def suscribir_canales(self):
        """Suscribir a canales de WebSocket"""
        if not self.ws_connected:
            return
            
        # Suscribir a datos de velas para cada símbolo
        for symbol in self.config.get('symbols', []):
            subscribe_msg = {
                "op": "subscribe",
                "args": [{
                    "instType": "MC",
                    "channel": "candle1m",
                    "instId": symbol
                }]
            }
            self.ws_public.send(json.dumps(subscribe_msg))
            print(f"📡 Suscrito a velas de {symbol}")

    def procesar_datos_vela(self, data):
        """Procesar datos de velas recibidos por WebSocket"""
        try:
            symbol = data.get("arg", {}).get("instId")
            if not symbol or symbol not in self.config.get('symbols', []):
                return
                
            # Extraer datos de la vela
            candle_data = data.get("data", [])
            if not candle_data:
                return
                
            # Procesar solo la vela más reciente
            latest_candle = candle_data[0]
            timestamp, open_price, high_price, low_price, close_price, volume = latest_candle
            
            # Actualizar datos en memoria
            if symbol not in self.ultimos_datos:
                self.ultimos_datos[symbol] = {
                    'maximos': [],
                    'minimos': [],
                    'cierres': [],
                    'tiempos': [],
                    'timeframe': '1m'
                }
                
            # Agregar nuevos datos
            self.ultimos_datos[symbol]['maximos'].append(float(high_price))
            self.ultimos_datos[symbol]['minimos'].append(float(low_price))
            self.ultimos_datos[symbol]['cierres'].append(float(close_price))
            self.ultimos_datos[symbol]['tiempos'].append(len(self.ultimos_datos[symbol]['tiempos']))
            
            # Mantener solo los últimos 200 datos
            max_len = 200
            if len(self.ultimos_datos[symbol]['maximos']) > max_len:
                self.ultimos_datos[symbol]['maximos'] = self.ultimos_datos[symbol]['maximos'][-max_len:]
                self.ultimos_datos[symbol]['minimos'] = self.ultimos_datos[symbol]['minimos'][-max_len:]
                self.ultimos_datos[symbol]['cierres'] = self.ultimos_datos[symbol]['cierres'][-max_len:]
                self.ultimos_datos[symbol]['tiempos'] = list(range(len(self.ultimos_datos[symbol]['cierres'])))
                
            # Actualizar precio actual
            self.ultimos_datos[symbol]['precio_actual'] = float(close_price)
            
        except Exception as e:
            print(f"Error procesando datos de vela: {e}")

    def obtener_info_contrato(self, symbol):
        """Obtener información del contrato para un símbolo"""
        if symbol in self.contract_info:
            return self.contract_info[symbol]
            
        params = {"symbol": symbol, "instType": "MC"}
        response = self.hacer_request("GET", "/api/v2/mix/market/contracts", params)
        
        if response and response.get("code") == "0" and response.get("data"):
            contract_data = response["data"][0]
            self.contract_info[symbol] = {
                "minOrderSz": float(contract_data.get("minOrderSz", 0)),
                "maxOrderSz": float(contract_data.get("maxOrderSz", 0)),
                "priceTick": float(contract_data.get("priceTick", 0)),
                "lotSz": float(contract_data.get("lotSz", 0)),
                "leverage": int(contract_data.get("leverage", 10))
            }
            return self.contract_info[symbol]
            
        return None

    def configurar_cuenta(self):
        """Configurar modo de margen, posición y apalancamiento"""
        # Configurar modo de margen aislado
        margin_data = {"marginMode": "isolated"}
        response = self.hacer_request("POST", "/api/v2/mix/account/set-margin-mode", data=margin_data)
        if response and response.get("code") != "0":
            print(f"Error configurando modo de margen: {response}")
            
        # Configurar modo de posición one-way
        position_data = {"positionMode": "one-way"}
        response = self.hacer_request("POST", "/api/v2/mix/account/set-position-mode", data=position_data)
        if response and response.get("code") != "0":
            print(f"Error configurando modo de posición: {response}")
            
        # Configurar apalancamiento para cada símbolo
        for symbol in self.config.get('symbols', []):
            contract_info = self.obtener_info_contrato(symbol)
            if contract_info:
                leverage = contract_info.get("leverage", 10)
                leverage_data = {
                    "symbol": symbol,
                    "leverage": str(leverage),
                    "marginCoin": "USDT"
                }
                response = self.hacer_request("POST", "/api/v2/mix/account/set-leverage", data=leverage_data)
                if response and response.get("code") != "0":
                    print(f"Error configurando apalancamiento para {symbol}: {response}")

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
        """Obtiene datos con configuración específica usando API REST de Bitget"""
        # Primero verificar si ya tenemos datos en memoria desde WebSocket
        if simbolo in self.ultimos_datos and len(self.ultimos_datos[simbolo]['cierres']) >= num_velas:
            return self.ultimos_datos[simbolo]
            
        # Si no hay suficientes datos, obtenerlos de la API REST
        # Convertir timeframe al formato correcto de Bitget
        bitget_timeframe = {
            '1m': '1m',
            '3m': '3m',
            '5m': '5m',
            '15m': '15m',
            '30m': '30m',
            '1h': '1H',
            '4h': '4H',
            '1d': '1D'
        }.get(timeframe, timeframe)
        
        # CORRECCIÓN: Usar los parámetros correctos para la API de Bitget
        params = {
            'instId': simbolo,  # Cambiado de 'symbol' a 'instId'
            'granularity': bitget_timeframe,
            'limit': str(min(num_velas, 200))  # Limitar a máximo 200
        }
        
        response = self.hacer_request("GET", "/api/v2/mix/market/candles", params)
        
        if not response or response.get("code") != "0" or not response.get("data"):
            if response:
                print(f"❌ Bitget API error /api/v2/mix/market/candles: {response}")
            return None
            
        # Procesar datos de velas
        candle_data = response["data"]
        maximos = []
        minimos = []
        cierres = []
        
        for candle in candle_data:
            _, open_price, high_price, low_price, close_price, _ = candle
            maximos.append(float(high_price))
            minimos.append(float(low_price))
            cierres.append(float(close_price))
            
        tiempos = list(range(len(cierres)))
        
        return {
            'maximos': maximos,
            'minimos': minimos,
            'cierres': cierres,
            'tiempos': tiempos,
            'precio_actual': cierres[-1] if cierres else 0,
            'timeframe': timeframe,
            'num_velas': num_velas
        }

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
        LÓGICA CORREGIDA:
        - BREAKOUT_LONG → Ruptura de resistencia en canal BAJISTA (oportunidad de reversión alcista)
        - BREAKOUT_SHORT → Ruptura de soporte en canal ALCISTA (oportunidad de reversión bajista)
        """
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
📊 <b>Configuración:</b> {config_optima['timeframe']} - {config_optima['num_velas']} velas
📏 <b>Ancho Canal:</b> {info_canal['ancho_canal_porcentual']:.1f}%
💪 <b>Fuerza:</b> {info_canal['fuerza_texto']} ({info_canal['angulo_tendencia']:.1f}°)
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
        # NUEVO: Verificar si ya hubo un breakout reciente (menos de 25 minutos)
        if simbolo in self.breakouts_detectados:
            ultimo_breakout = self.breakouts_detectados[simbolo]
            tiempo_desde_ultimo = (datetime.now() - ultimo_breakout['timestamp']).total_seconds() / 60
            if tiempo_desde_ultimo < 115:
                print(f"     ⏰ {simbolo} - Breakout detectado recientemente ({tiempo_desde_ultimo:.1f} min), omitiendo...")
                return None
        margen_breakout =  precio_cierre
        if direccion == "🟢 ALCISTA" and nivel_fuerza >= 2:
            if precio_cierre < soporte:
                print(f"     🚀 {simbolo} - BREAKOUT: {precio_cierre:.8f} > Resistencia: {resistencia:.8f}")
                return "BREAKOUT_LONG"
        elif direccion == "🔴 BAJISTA" and nivel_fuerza >= 2:
            if precio_cierre > resistencia:
                print(f"     📉 {simbolo} - BREAKOUT: {precio_cierre:.8f} < Soporte: {soporte:.8f}")
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
            # NUEVO: Limpiar también de breakouts_detectados cuando expira el reentry
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
                    # NUEVO: Limpiar breakouts_detectados cuando se confirma reentry
                    if simbolo in self.breakouts_detectados:
                        del self.breakouts_detectados[simbolo]
                    return "LONG"
        elif tipo_breakout == "BREAKOUT_SHORT":
            if soporte <= precio_actual <= resistencia:
                distancia_resistencia = abs(precio_actual - resistencia)
                if distancia_resistencia <= tolerancia and stoch_k >= 70 and stoch_d >= 70:
                    print(f"     ✅ {simbolo} - REENTRY SHORT confirmado! Entrada en resistencia con Stoch overbought")
                    # NUEVO: Limpiar breakouts_detectados cuando se confirma reentry
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

    def colocar_orden(self, simbolo, tipo_operacion, precio_entrada, take_profit, stop_loss):
        """Coloca orden en Bitget con TP y SL"""
        try:
            # Obtener información del contrato para calcular tamaño y precisión
            contract_info = self.obtener_info_contrato(simbolo)
            if not contract_info:
                print(f"❌ No se pudo obtener información del contrato para {simbolo}")
                return False
                
            # Calcular tamaño de orden
            margin = self.config.get('order_margin_usdt', 2)
            leverage = contract_info.get("leverage", 10)
            notional = margin * leverage
            size_raw = notional / precio_entrada
            lot_sz = contract_info.get("lotSz", 0.001)
            size = round(size_raw / lot_sz) * lot_sz
            
            # Redondear precios
            price_tick = contract_info.get("priceTick", 0.001)
            tp_price = round(take_profit / price_tick) * price_tick
            sl_price = round(stop_loss / price_tick) * price_tick
            
            # Determinar lado de la orden
            side = "buy" if tipo_operacion == "LONG" else "sell"
            
            # Colocar orden de mercado
            order_data = {
                "symbol": simbolo,
                "side": side,
                "orderType": "market",
                "size": str(size),
                "marginMode": "isolated"
            }
            
            response = self.hacer_request("POST", "/api/v2/mix/order/place-order", data=order_data)
            if not response or response.get("code") != "0":
                print(f"❌ Error colocando orden de entrada para {simbolo}: {response}")
                return False
                
            order_id = response.get("data", {}).get("orderId")
            print(f"✅ Orden de entrada colocada para {simbolo}: {order_id}")
            
            # Colocar órdenes de TP y SL
            # Para TP
            tp_side = "sell" if tipo_operacion == "LONG" else "buy"
            tp_data = {
                "symbol": simbolo,
                "planType": "normal",
                "orderType": "limit",
                "side": tp_side,
                "size": str(size),
                "triggerPrice": str(tp_price),
                "executePrice": str(tp_price),
                "marginMode": "isolated"
            }
            
            response = self.hacer_request("POST", "/api/v2/mix/order/place-plan-order", data=tp_data)
            if not response or response.get("code") != "0":
                print(f"❌ Error colocando TP para {simbolo}: {response}")
                return False
                
            tp_order_id = response.get("data", {}).get("orderId")
            print(f"✅ Orden TP colocada para {simbolo}: {tp_order_id}")
            
            # Para SL
            sl_side = "sell" if tipo_operacion == "LONG" else "buy"
            sl_data = {
                "symbol": simbolo,
                "planType": "normal",
                "orderType": "limit",
                "side": sl_side,
                "size": str(size),
                "triggerPrice": str(sl_price),
                "executePrice": str(sl_price),
                "marginMode": "isolated"
            }
            
            response = self.hacer_request("POST", "/api/v2/mix/order/place-plan-order", data=sl_data)
            if not response or response.get("code") != "0":
                print(f"❌ Error colocando SL para {simbolo}: {response}")
                return False
                
            sl_order_id = response.get("data", {}).get("orderId")
            print(f"✅ Orden SL colocada para {simbolo}: {sl_order_id}")
            
            return True
            
        except Exception as e:
            print(f"❌ Error en colocar_orden para {simbolo}: {e}")
            return False

    def escanear_mercado(self):
        """Escanea el mercado con estrategia Breakout + Reentry"""
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
                
                # Colocar orden en Bitget
                orden_exitosa = self.colocar_orden(simbolo, tipo_operacion, precio_entrada, tp, sl)
                if not orden_exitosa:
                    continue
                    
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
        """Genera y envía señal de operación con info de breakout SIN gráfico"""
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
        breakouts = self.contar_breakouts_semana()
        reporte = f"""
📊 <b>REPORTE SEMANAL - BOT TRADING</b>
📅 Período: {(datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')} a {datetime.now().strftime('%Y-%m-%d')}
📈 Total operaciones: {total_ops}
✅ Operaciones ganadoras: {wins} ({winrate:.1f}%)
❌ Operaciones perdedoras: {losses}
💰 PnL total: {pnl_total:.2f}%
🏆 Mejor operación: {mejor_op['symbol']} ({mejor_op['pnl_percent']:.2f}%)
💔 Peor operación: {peor_op['symbol']} ({peor_op['pnl_percent']:.2f}%)
📊 Ganancia promedio: {avg_ganancia:.2f}%
📊 Pérdida promedio: {avg_perdida:.2f}%
🚀 Breakouts utilizados: {breakouts}
        """
        token = self.config.get('telegram_token')
        chat_ids = self.config.get('telegram_chat_ids', [])
        if token and chat_ids:
            try:
                self._enviar_telegram_simple(reporte, token, chat_ids)
                print("✅ Reporte semanal enviado")
            except Exception as e:
                print(f"❌ Error enviando reporte: {e}")
        return reporte

    def calcular_regresion_lineal(self, x, y):
        """Calcula regresión lineal simple"""
        if len(x) != len(y) or len(x) < 2:
            return None
        n = len(x)
        sum_x = sum(x)
        sum_y = sum(y)
        sum_xy = sum(x[i] * y[i] for i in range(n))
        sum_x2 = sum(x[i] ** 2 for i in range(n))
        denominator = n * sum_x2 - sum_x ** 2
        if denominator == 0:
            return None
        slope = (n * sum_xy - sum_x * sum_y) / denominator
        intercept = (sum_y - slope * sum_x) / n
        return slope, intercept

    def calcular_pearson_y_angulo(self, x, y):
        """Calcula coeficiente de Pearson y ángulo de tendencia"""
        if len(x) != len(y) or len(x) < 2:
            return 0, 0
        n = len(x)
        sum_x = sum(x)
        sum_y = sum(y)
        sum_xy = sum(x[i] * y[i] for i in range(n))
        sum_x2 = sum(x[i] ** 2 for i in range(n))
        sum_y2 = sum(y[i] ** 2 for i in range(n))
        denominator = math.sqrt((n * sum_x2 - sum_x ** 2) * (n * sum_y2 - sum_y ** 2))
        if denominator == 0:
            return 0, 0
        pearson = (n * sum_xy - sum_x * sum_y) / denominator
        # Calcular ángulo en grados
        regression = self.calcular_regresion_lineal(x, y)
        if regression:
            slope, _ = regression
            # Escalar pendiente para que sea más significativa
            slope_scaled = slope * 1000
            angulo_rad = math.atan(slope_scaled)
            angulo_deg = math.degrees(angulo_rad)
            return pearson, angulo_deg
        return pearson, 0

    def calcular_r2(self, y, x, slope, intercept):
        """Calcula R² (coeficiente de determinación)"""
        if len(x) != len(y) or len(x) < 2:
            return 0
        n = len(y)
        y_mean = sum(y) / n
        ss_tot = sum((yi - y_mean) ** 2 for yi in y)
        y_pred = [slope * xi + intercept for xi in x]
        ss_res = sum((y[i] - y_pred[i]) ** 2 for i in range(n))
        if ss_tot == 0:
            return 0
        r2 = 1 - (ss_res / ss_tot)
        return r2

    def clasificar_fuerza_tendencia(self, angulo):
        """Clasifica la fuerza de la tendencia según el ángulo"""
        abs_angulo = abs(angulo)
        if abs_angulo < 5:
            return "Débil", 1
        elif abs_angulo < 10:
            return "Moderada", 2
        elif abs_angulo < 20:
            return "Fuerte", 3
        else:
            return "Muy Fuerte", 4

    def determinar_direccion_tendencia(self, angulo, min_angulo=5):
        """Determina la dirección de la tendencia"""
        if abs(angulo) < min_angulo:
            return "➡️ LATERAL"
        elif angulo > 0:
            return "🟢 ALCISTA"
        else:
            return "🔴 BAJISTA"

    def calcular_stochastic(self, datos_mercado, k_period=14, d_period=3):
        """Calcula indicador Stochastic"""
        if not datos_mercado or len(datos_mercado['maximos']) < k_period:
            return 50, 50
        maximos = datos_mercado['maximos'][-k_period:]
        minimos = datos_mercado['minimos'][-k_period:]
        cierres = datos_mercado['cierres'][-k_period:]
        highest_high = max(maximos)
        lowest_low = min(minimos)
        if highest_high == lowest_low:
            k_percent = 50
        else:
            k_percent = 100 * (cierres[-1] - lowest_low) / (highest_high - lowest_low)
        # Suavizar K
        if len(datos_mercado['cierres']) >= k_period + d_period - 1:
            k_values = []
            for i in range(d_period):
                idx = -(i + 1)
                high = max(datos_mercado['maximos'][idx-k_period:idx])
                low = min(datos_mercado['minimos'][idx-k_period:idx])
                if high == low:
                    k = 50
                else:
                    k = 100 * (datos_mercado['cierres'][idx] - low) / (high - low)
                k_values.append(k)
            k_smoothed = sum(k_values) / len(k_values)
        else:
            k_smoothed = k_percent
        # Calcular D como media móvil de K
        d_percent = k_smoothed  # Simplificado, en realidad sería media móvil de K
        return k_smoothed, d_percent

    def _enviar_telegram_simple(self, mensaje, token, chat_ids):
        """Envía mensaje simple a Telegram"""
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        for chat_id in chat_ids:
            payload = {
                'chat_id': chat_id,
                'text': mensaje,
                'parse_mode': 'HTML'
            }
            try:
                response = requests.post(url, json=payload, timeout=10)
                if response.status_code != 200:
                    print(f"❌ Error enviando a Telegram: {response.text}")
            except Exception as e:
                print(f"❌ Error enviando a Telegram: {e}")

# ---------------------------
# CONFIGURACIÓN DE FLASK
# ---------------------------
app = Flask(__name__)
bot = None

@app.route('/')
def index():
    return "Bot de Trading Breakout + Reentry con API de Bitget"

@app.route('/start', methods=['POST'])
def start_bot():
    global bot
    if bot is None:
        config = {
            'symbols': os.environ.get('SYMBOLS', 'BTCUSDT,ETHUSDT').split(','),
            'telegram_token': os.environ.get('TELEGRAM_TOKEN'),
            'telegram_chat_ids': os.environ.get('TELEGRAM_CHAT_IDS', '').split(','),
            'timeframes': ['1m', '3m', '5m', '15m', '30m'],
            'velas_options': [80, 100, 120, 150, 200],
            'min_channel_width_percent': 4.0,
            'min_trend_strength_degrees': 16,
            'trend_threshold_degrees': 13,
            'entry_margin': 0.001,
            'min_rr_ratio': 1.2,
            'order_margin_usdt': 2,
            'auto_optimize': True,
            'min_samples_optimizacion': 15,
            'log_path': 'operaciones_log.csv',
            'estado_file': 'estado_bot.json'
        }
        bot = TradingBot(config)
        # Configurar cuenta de Bitget
        bot.configurar_cuenta()
        return jsonify({"status": "Bot iniciado correctamente"})
    return jsonify({"status": "Bot ya está en ejecución"})

@app.route('/scan', methods=['POST'])
def scan_market():
    global bot
    if bot is None:
        return jsonify({"error": "Bot no iniciado"}), 400
    try:
        signals = bot.escanear_mercado()
        bot.guardar_estado()
        return jsonify({"status": "Escaneo completado", "signals_found": signals})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/report', methods=['POST'])
def generate_report():
    global bot
    if bot is None:
        return jsonify({"error": "Bot no iniciado"}), 400
    try:
        report = bot.generar_reporte_semanal()
        return jsonify({"status": "Reporte generado", "report": report})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ---------------------------
# FUNCIÓN PRINCIPAL
# ---------------------------
if __name__ == '__main__':
    # Iniciar bot automáticamente al arrancar el servidor
    with app.app_context():
        start_bot()
    # Ejecutar servidor Flask
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
