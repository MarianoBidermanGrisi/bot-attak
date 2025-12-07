# bot_web_service.py
# Versión actualizada con gestión robusta de órdenes en Bitget
import requests
import time
import json
import os
import sys
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
import hmac
import hashlib
import base64
import uuid  # ✅ Añadido para clientOid

# Configurar logging básico
logging.basicConfig(level=logging.INFO, stream=sys.stdout, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ---------------------------
# Optimizador IA (sin cambios)
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
            time.sleep(0.1)
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
# UTILIDADES BITGET
# ---------------------------

def firmar_bitget(timestamp, method, endpoint, body, secret):
    message = timestamp + method + endpoint + body
    return base64.b64encode(hmac.new(secret.encode('utf-8'), message.encode('utf-8'), hashlib.sha256).digest()).decode()

def enviar_orden_bitget(simbolo, tipo, precio_entrada, tp, sl, api_key, api_secret, api_passphrase):
    base_url = "https://api.bitget.com"
    margin_coin = "USDT"
    size_usd = 2.0  # ✅ 2 USDT de margen
    leverage = 10   # ✅ 10x

    # === Paso 0: Obtener contrato para redondeo preciso ===
    contracts_url = "/api/mix/v1/market/contracts"
    contracts_params = {
        "productType": "USDT-FUTURES",
        "symbol": simbolo
    }
    timestamp = str(int(time.time() * 1000))
    signature = firmar_bitget(timestamp, "GET", contracts_url, "", api_secret)
    headers = {
        "ACCESS-KEY": api_key,
        "ACCESS-SIGN": signature,
        "ACCESS-TIMESTAMP": timestamp,
        "ACCESS-PASSPHRASE": api_passphrase,
        "Content-Type": "application/json"
    }
    try:
        resp = requests.get(base_url + contracts_url, headers=headers, params=contracts_params, timeout=10)
        time.sleep(0.1)
        if resp.status_code != 200 or resp.json().get("code") != "00000":
            print(f"❌ Error obteniendo contrato {simbolo}: {resp.text}")
            return False
        contrato = resp.json()["data"][0]
        min_trade_num = float(contrato["minTradeNum"])
    except Exception as e:
        print(f"❌ Excepción obteniendo contrato {simbolo}: {e}")
        return False

    # === Paso 1: Establecer apalancamiento ===
    leverage_url = "/api/mix/v1/account/setLeverage"
    leverage_data = {
        "symbol": simbolo,
        "marginCoin": margin_coin,
        "leverage": str(leverage),
        "holdSide": "long" if tipo == "LONG" else "short"
    }
    timestamp = str(int(time.time() * 1000))
    body_str = json.dumps(leverage_data)
    signature = firmar_bitget(timestamp, "POST", leverage_url, body_str, api_secret)
    headers = {
        "ACCESS-KEY": api_key,
        "ACCESS-SIGN": signature,
        "ACCESS-TIMESTAMP": timestamp,
        "ACCESS-PASSPHRASE": api_passphrase,
        "Content-Type": "application/json"
    }
    resp = requests.post(base_url + leverage_url, headers=headers, json=leverage_data, timeout=10)
    time.sleep(0.1)
    if resp.status_code != 200 or resp.json().get("code") != "00000":
        print(f"⚠️ Advertencia: error al establecer apalancamiento ({resp.text})")

    # === Paso 2: Calcular y redondear size ===
    qty_contracts_raw = (size_usd * leverage) / precio_entrada
    qty_contracts = math.floor(qty_contracts_raw / min_trade_num) * min_trade_num
    if qty_contracts < min_trade_num:
        print(f"❌ Cantidad {qty_contracts} < mínimo {min_trade_num} para {simbolo}")
        return False
    qty_str = f"{qty_contracts:.10f}".rstrip('0').rstrip('.')

    # === Paso 3: Enviar orden principal (mercado) ===
    client_oid = str(uuid.uuid4())
    order_url = "/api/mix/v1/order/placeOrder"
    order_data = {
        "symbol": simbolo,
        "marginCoin": margin_coin,
        "size": qty_str,
        "side": "open_long" if tipo == "LONG" else "open_short",
        "orderType": "market",
        "timeInForceValue": "normal",
        "reduceOnly": False,
        "marginMode": "isolated",
        "clientOid": client_oid
    }
    timestamp = str(int(time.time() * 1000))
    body_str = json.dumps(order_data)
    signature = firmar_bitget(timestamp, "POST", order_url, body_str, api_secret)
    headers = {
        "ACCESS-KEY": api_key,
        "ACCESS-SIGN": signature,
        "ACCESS-TIMESTAMP": timestamp,
        "ACCESS-PASSPHRASE": api_passphrase,
        "Content-Type": "application/json"
    }
    resp = requests.post(base_url + order_url, headers=headers, json=order_data, timeout=10)
    time.sleep(0.1)
    if resp.status_code != 200 or resp.json().get("code") != "00000":
        print(f"❌ Error creando orden principal: {resp.text}")
        return False

    order_id = resp.json()["data"]["orderId"]

    # === Paso 4: Órdenes TP/SL ===
    tpsl_url = "/api/mix/v1/order/placeTPSL"
    tp_data = {
        "symbol": simbolo,
        "marginCoin": margin_coin,
        "planType": "profit_plan",
        "holdSide": "long" if tipo == "LONG" else "short",
        "triggerPrice": f"{tp:.10f}".rstrip('0').rstrip('.'),
        "executePrice": "",
        "size": qty_str,
        "triggerType": "fill_price"
    }
    sl_data = {
        "symbol": simbolo,
        "marginCoin": margin_coin,
        "planType": "loss_plan",
        "holdSide": "long" if tipo == "LONG" else "short",
        "triggerPrice": f"{sl:.10f}".rstrip('0').rstrip('.'),
        "executePrice": "",
        "size": qty_str,
        "triggerType": "fill_price"
    }

    timestamp = str(int(time.time() * 1000))
    body_str = json.dumps(tp_data)
    signature = firmar_bitget(timestamp, "POST", tpsl_url, body_str, api_secret)
    headers["ACCESS-SIGN"] = signature
    headers["ACCESS-TIMESTAMP"] = timestamp
    resp_tp = requests.post(base_url + tpsl_url, headers=headers, json=tp_data, timeout=10)
    time.sleep(0.1)

    timestamp = str(int(time.time() * 1000))
    body_str = json.dumps(sl_data)
    signature = firmar_bitget(timestamp, "POST", tpsl_url, body_str, api_secret)
    headers["ACCESS-SIGN"] = signature
    headers["ACCESS-TIMESTAMP"] = timestamp
    resp_sl = requests.post(base_url + tpsl_url, headers=headers, json=sl_data, timeout=10)
    time.sleep(0.1)

    if resp_tp.status_code == 200 and resp_sl.status_code == 200:
        print(f"✅ Órdenes TP/SL activadas en Bitget para {simbolo}")
        return True
    else:
        print(f"⚠️ Error TP/SL en Bitget: TP={resp_tp.text}, SL={resp_sl.text}")

        # === Paso 5: Si TP/SL fallan, cerrar posición ===
        print(f"🔒 Cerrando posición para evitar riesgo desnudo en {simbolo}...")
        close_data = {
            "symbol": simbolo,
            "marginCoin": margin_coin,
            "size": qty_str,
            "side": "close_long" if tipo == "LONG" else "close_short",
            "orderType": "market",
            "timeInForceValue": "normal",
            "reduceOnly": True,
            "marginMode": "isolated",
            "clientOid": str(uuid.uuid4())
        }
        timestamp = str(int(time.time() * 1000))
        body_str = json.dumps(close_data)
        signature = firmar_bitget(timestamp, "POST", order_url, body_str, api_secret)
        headers["ACCESS-SIGN"] = signature
        headers["ACCESS-TIMESTAMP"] = timestamp
        resp_close = requests.post(base_url + order_url, headers=headers, json=close_data, timeout=10)
        time.sleep(0.1)
        if resp_close.status_code == 200 and resp_close.json().get("code") == "00000":
            print(f"✅ Posición cerrada tras fallo en TP/SL")
        else:
            print(f"❌ Fallo al cerrar posición: {resp_close.text}")
        return False

# ---------------------------
# BOT PRINCIPAL
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
        self.operaciones_activas = {}
        self.senales_enviadas = set()
        self.archivo_log = self.log_path
        self.inicializar_log()
        self.indice_escaneo = 0  # ✅ 5 símbolos/minuto

    def cargar_estado(self):
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
                if 'esperando_reentry' in estado:
                    for simbolo, info in estado['esperando_reentry'].items():
                        info['timestamp'] = datetime.fromisoformat(info['timestamp'])
                    self.esperando_reentry = estado['esperando_reentry']
                if 'breakouts_detectados' in estado:
                    for simbolo, info in estado['breakouts_detectados'].items():
                        info['timestamp'] = datetime.fromisoformat(info['timestamp'])
                    self.breakouts_detectados = estado['breakouts_detectados']
                self.ultima_optimizacion = estado.get('ultima_optimizacion', datetime.now())
                self.operaciones_desde_optimizacion = estado.get('operaciones_desde_optimizacion', 0)
                self.total_operaciones = estado.get('total_operaciones', 0)
                self.breakout_history = estado.get('breakout_history', {})
                self.config_optima_por_simbolo = estado.get('config_optima_por_simbolo', {})
                self.ultima_busqueda_config = estado.get('ultima_busqueda_config', {})
                self.operaciones_activas = estado.get('operaciones_activas', {})
                self.senales_enviadas = set(estado.get('senales_enviadas', []))
                self.indice_escaneo = estado.get('indice_escaneo', 0)
                print("✅ Estado anterior cargado correctamente")
        except Exception as e:
            print(f"⚠ Error cargando estado previo: {e}")

    def guardar_estado(self):
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
                'indice_escaneo': self.indice_escaneo,
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
                }
            }
            with open(self.estado_file, 'w', encoding='utf-8') as f:
                json.dump(estado, f, indent=2, ensure_ascii=False)
            print("💾 Estado guardado correctamente")
        except Exception as e:
            print(f"⚠ Error guardando estado: {e}")

    # === Datos desde Binance (solo lectura) ===
    def obtener_datos_con_volumen(self, simbolo, timeframe, num_velas):
        url = "https://api.binance.com/api/v3/klines"
        params = {'symbol': simbolo, 'interval': timeframe, 'limit': num_velas + 55}
        try:
            respuesta = requests.get(url, params=params, timeout=10)
            datos = respuesta.json()
            if not isinstance(datos, list) or len(datos) == 0:
                return None
            opens = [float(vela[1]) for vela in datos]
            highs = [float(vela[2]) for vela in datos]
            lows = [float(vela[3]) for vela in datos]
            closes = [float(vela[4]) for vela in datos]
            volumes = [float(vela[5]) for vela in datos]
            times = [pd.to_datetime(vela[0], unit='ms') for vela in datos]
            df = pd.DataFrame({
                'Date': times,
                'Open': opens,
                'High': highs,
                'Low': lows,
                'Close': closes,
                'Volume': volumes
            })
            df.set_index('Date', inplace=True)
            return df
        except Exception as e:
            print(f"    ❌ Error obteniendo datos de {simbolo}: {e}")
            return None

    def calcular_kvo(self, df):
        TrigLen = 13
        FastX = 34
        SlowX = 55
        df = df.copy()
        df['hlc3'] = (df['High'] + df['Low'] + df['Close']) / 3
        df['trend'] = np.where(df['hlc3'] > df['hlc3'].shift(1), df['Volume'], -df['Volume'])
        df['xFast'] = df['trend'].ewm(span=FastX, adjust=False).mean()
        df['xSlow'] = df['trend'].ewm(span=SlowX, adjust=False).mean()
        df['xKVO'] = df['xFast'] - df['xSlow']
        df['xTrigger'] = df['xKVO'].ewm(span=TrigLen, adjust=False).mean()
        return df

    def calcular_kvo_valores(self, df):
        df_kvo = self.calcular_kvo(df)
        if df_kvo.empty:
            return 0, 0
        kvo = df_kvo['xKVO'].iloc[-1]
        trigger = df_kvo['xTrigger'].iloc[-1]
        return kvo, trigger

    def calcular_stochastic_df(self, df, period=14, k_period=3, d_period=3):
        if len(df) < period:
            return 50, 50
        stoch_k_values = []
        for i in range(len(df)):
            if i < period - 1:
                stoch_k_values.append(50)
            else:
                window = df.iloc[i - period + 1:i + 1]
                hh = window['High'].max()
                ll = window['Low'].min()
                close = window['Close'].iloc[-1]
                k = 100 * (close - ll) / (hh - ll) if hh != ll else 50
                stoch_k_values.append(k)
        stoch_k = np.array(stoch_k_values)
        stoch_k_smooth = np.convolve(stoch_k, np.ones(k_period)/k_period, mode='same')
        stoch_d = np.convolve(stoch_k_smooth, np.ones(d_period)/d_period, mode='same')
        return stoch_k[-1], stoch_d[-1]

    def calcular_canal_regresion_df(self, df, candle_period):
        if len(df) < candle_period:
            return None
        sub_df = df.tail(candle_period)
        tiempos = list(range(len(sub_df)))
        maximos = sub_df['High'].values
        minimos = sub_df['Low'].values
        cierres = sub_df['Close'].values
        reg_max = self.calcular_regresion_lineal(tiempos, maximos)
        reg_min = self.calcular_regresion_lineal(tiempos, minimos)
        reg_close = self.calcular_regresion_lineal(tiempos, cierres)
        if not all([reg_max, reg_min, reg_close]):
            return None
        pendiente_max, intercepto_max = reg_max
        pendiente_min, intercepto_min = reg_min
        pendiente_cierre, intercepto_cierre = reg_close
        tiempo_actual = tiempos[-1]
        resistencia_media = pendiente_max * tiempo_actual + intercepto_max
        soporte_media = pendiente_min * tiempo_actual + intercepto_min
        diferencias_max = [maximos[i] - (pendiente_max * tiempos[i] + intercepto_max) for i in range(len(tiempos))]
        diferencias_min = [minimos[i] - (pendiente_min * tiempos[i] + intercepto_min) for i in range(len(tiempos))]
        desviacion_max = np.std(diferencias_max) if diferencias_max else 0
        desviacion_min = np.std(diferencias_min) if diferencias_min else 0
        resistencia_superior = resistencia_media + desviacion_max
        soporte_inferior = soporte_media - desviacion_min
        precio_actual = sub_df['Close'].iloc[-1]
        pearson, angulo_tendencia = self.calcular_pearson_y_angulo(tiempos, cierres)
        fuerza_texto, nivel_fuerza = self.clasificar_fuerza_tendencia(angulo_tendencia)
        direccion = self.determinar_direccion_tendencia(angulo_tendencia, 1)
        stoch_k, stoch_d = self.calcular_stochastic_df(df)
        kvo, trigger = self.calcular_kvo_valores(df)
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
            'r2_score': self.calcular_r2(cierres, tiempos, pendiente_cierre, intercepto_cierre),
            'pendiente_resistencia': pendiente_max,
            'pendiente_soporte': pendiente_min,
            'stoch_k': stoch_k,
            'stoch_d': stoch_d,
            'kvo': kvo,
            'kvo_trigger': trigger,
            'timeframe': df.index.freqstr if hasattr(df.index, 'freqstr') else 'Unknown',
            'num_velas': candle_period
        }

    def buscar_configuracion_optima_simbolo(self, simbolo):
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
                    df = self.obtener_datos_con_volumen(simbolo, timeframe, num_velas)
                    if df is None or len(df) < num_velas + 55:
                        continue
                    canal_info = self.calcular_canal_regresion_df(df, num_velas)
                    if not canal_info:
                        continue
                    if (canal_info['nivel_fuerza'] >= 2 and 
                        abs(canal_info['coeficiente_pearson']) >= 0.4 and 
                        canal_info['r2_score'] >= 0.4 and
                        canal_info['ancho_canal_porcentual'] >= self.config.get('min_channel_width_percent', 4.0)):
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
                except Exception as e:
                    continue

        if mejor_config:
            self.config_optima_por_simbolo[simbolo] = mejor_config
            self.ultima_busqueda_config[simbolo] = datetime.now()
            print(f"   ✅ Config óptima: {mejor_config['timeframe']} - {mejor_config['num_velas']} velas - Ancho: {mejor_config['ancho_canal']:.1f}%")
        return mejor_config

    def detectar_breakout(self, simbolo, info_canal, df):
        if not info_canal:
            return None
        if info_canal['ancho_canal_porcentual'] < self.config.get('min_channel_width_percent', 4.0):
            return None
        precio_cierre = df['Close'].iloc[-1]
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
        if simbolo in self.breakouts_detectados:
            ultimo_breakout = self.breakouts_detectados[simbolo]
            tiempo_desde_ultimo = (datetime.now() - ultimo_breakout['timestamp']).total_seconds() / 60
            if tiempo_desde_ultimo < 115:
                return None

        if direccion == "🟢 ALCISTA" and nivel_fuerza >= 2:
            if precio_cierre < soporte:
                return "BREAKOUT_LONG"
        elif direccion == "🔴 BAJISTA" and nivel_fuerza >= 2:
            if precio_cierre > resistencia:
                return "BREAKOUT_SHORT"
        return None

    def detectar_reentry(self, simbolo, info_canal, df):
        if simbolo not in self.esperando_reentry:
            return None
        breakout_info = self.esperando_reentry[simbolo]
        tipo_breakout = breakout_info['tipo']
        timestamp_breakout = breakout_info['timestamp']
        tiempo_desde_breakout = (datetime.now() - timestamp_breakout).total_seconds() / 60
        if tiempo_desde_breakout > 120:
            del self.esperando_reentry[simbolo]
            if simbolo in self.breakouts_detectados:
                del self.breakouts_detectados[simbolo]
            return None
        precio_actual = df['Close'].iloc[-1]
        resistencia = info_canal['resistencia']
        soporte = info_canal['soporte']
        stoch_k = info_canal['stoch_k']
        stoch_d = info_canal['stoch_d']
        kvo = info_canal['kvo']
        tolerancia = 0.001 * precio_actual

        if tipo_breakout == "BREAKOUT_LONG":
            if soporte <= precio_actual <= resistencia:
                if abs(precio_actual - soporte) <= tolerancia and stoch_k <= 30 and stoch_d <= 30 and kvo > 0:
                    if simbolo in self.breakouts_detectados:
                        del self.breakouts_detectados[simbolo]
                    return "LONG"
        elif tipo_breakout == "BREAKOUT_SHORT":
            if soporte <= precio_actual <= resistencia:
                if abs(precio_actual - resistencia) <= tolerancia and stoch_k >= 70 and stoch_d >= 70 and kvo < 0:
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

    def generar_senal_operacion(self, simbolo, tipo_operacion, precio_entrada, tp, sl,
                                info_canal, df, config_optima, breakout_info=None):
        if simbolo in self.senales_enviadas:
            return
        if not all([precio_entrada, tp, sl]):
            print(f"    ❌ Niveles inválidos para {simbolo}")
            return

        # ✅ AQUÍ SE ENVÍA LA ORDEN REAL A BITGET (versión mejorada)
        bitget_success = enviar_orden_bitget(
            simbolo=simbolo,
            tipo=tipo_operacion,
            precio_entrada=precio_entrada,
            tp=tp,
            sl=sl,
            api_key=self.config.get('bitget_api_key'),
            api_secret=self.config.get('bitget_api_secret'),
            api_passphrase=self.config.get('bitget_api_passphrase')
        )
        if not bitget_success:
            print(f"    ❌ Error enviando orden a Bitget para {simbolo}")
            return

        # Continúa con Telegram y log
        sl_percent = abs((sl - precio_entrada) / precio_entrada) * 100
        tp_percent = abs((tp - precio_entrada) / precio_entrada) * 100
        riesgo = abs(precio_entrada - sl)
        beneficio = abs(tp - precio_entrada)
        ratio_rr = beneficio / riesgo if riesgo > 0 else 0
        stoch_estado = "📉 SOBREVENTA" if tipo_operacion == "LONG" else "📈 SOBRECOMPRA"
        kvo_condicion = "🟢 KVO > 0 (alcista)" if tipo_operacion == "LONG" else "🔴 KVO < 0 (bajista)"
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
✅ <b>ORDEN ENVIADA A BITGET (2 USDT @ 10x, isolated)</b>
{breakout_texto}
⏱️ <b>Configuración óptima:</b>
📊 Timeframe: {config_optima['timeframe']}
🕯️ Velas: {config_optima['num_velas']}
📏 Ancho Canal: {info_canal['ancho_canal_porcentual']:.1f}% ⭐
💰 <b>Precio Actual:</b> {info_canal['precio_actual']:.8f}
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
📊 <b>KVO:</b> {info_canal['kvo']:.2f} → {kvo_condicion}
⏰ <b>Hora:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
💡 <b>Estrategia:</b> BREAKOUT + REENTRY + CONFIRMACIÓN KVO y STOCH
        """
        token = self.config.get('telegram_token')
        chat_ids = self.config.get('telegram_chat_ids', [])
        if token and chat_ids:
            try:
                buf = self.generar_grafico_profesional(simbolo, info_canal, df, precio_entrada, tp, sl, tipo_operacion)
                if buf:
                    self.enviar_grafico_telegram(buf, token, chat_ids)
                    time.sleep(1)
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
            'kvo': info_canal['kvo'],
            'breakout_usado': breakout_info is not None
        }
        self.senales_enviadas.add(simbolo)
        self.total_operaciones += 1

    def generar_grafico_profesional(self, simbolo, info_canal, df, precio_entrada, tp, sl, tipo_operacion):
        try:
            config_optima = self.config_optima_por_simbolo.get(simbolo)
            if not config_optima:
                return None
            sub_df = df.tail(config_optima['num_velas'])
            tiempos_reg = list(range(len(sub_df)))
            resistencia_values = []
            soporte_values = []
            for i, t in enumerate(tiempos_reg):
                resist = info_canal['pendiente_resistencia'] * t + \
                        (info_canal['resistencia'] - info_canal['pendiente_resistencia'] * tiempos_reg[-1])
                sop = info_canal['pendiente_soporte'] * t + \
                     (info_canal['soporte'] - info_canal['pendiente_soporte'] * tiempos_reg[-1])
                resistencia_values.append(resist)
                soporte_values.append(sop)
            sub_df = sub_df.copy()
            sub_df['Resistencia'] = resistencia_values
            sub_df['Soporte'] = soporte_values
            df_kvo = self.calcular_kvo(df)
            kvo_values = df_kvo['xKVO'].tail(len(sub_df)).values
            trigger_values = df_kvo['xTrigger'].tail(len(sub_df)).values
            sub_df['KVO'] = kvo_values
            sub_df['KVO_Trigger'] = trigger_values
            stoch_k, stoch_d = [], []
            period = 14
            for i in range(len(sub_df)):
                if i < period - 1:
                    stoch_k.append(50)
                    stoch_d.append(50)
                else:
                    window = sub_df.iloc[i - period + 1:i + 1]
                    hh = window['High'].max()
                    ll = window['Low'].min()
                    close = window['Close'].iloc[-1]
                    k = 100 * (close - ll) / (hh - ll) if hh != ll else 50
                    stoch_k.append(k)
            stoch_k = np.array(stoch_k)
            stoch_k_smooth = np.convolve(stoch_k, np.ones(3)/3, mode='same')
            stoch_d = np.convolve(stoch_k_smooth, np.ones(3)/3, mode='same')
            sub_df['Stoch_K'] = stoch_k
            sub_df['Stoch_D'] = stoch_d
            apds = [
                mpf.make_addplot(sub_df['Resistencia'], color='#5444ff', linestyle='--', width=2, panel=0),
                mpf.make_addplot(sub_df['Soporte'], color="#5444ff", linestyle='--', width=2, panel=0),
            ]
            if precio_entrada and tp and sl:
                entry_line = [precio_entrada] * len(sub_df)
                tp_line = [tp] * len(sub_df)
                sl_line = [sl] * len(sub_df)
                apds.append(mpf.make_addplot(entry_line, color='#FFD700', width=2, panel=0))
                apds.append(mpf.make_addplot(tp_line, color='#00FF00', width=2, panel=0))
                apds.append(mpf.make_addplot(sl_line, color='#FF0000', width=2, panel=0))
            apds.append(mpf.make_addplot(sub_df['Stoch_K'], color='#00BFFF', width=1.5, panel=1, ylabel='Stochastic'))
            apds.append(mpf.make_addplot(sub_df['Stoch_D'], color='#FF6347', width=1.5, panel=1))
            apds.append(mpf.make_addplot([80]*len(sub_df), color="#E7E4E4", linestyle='--', width=0.8, panel=1, alpha=0.5))
            apds.append(mpf.make_addplot([20]*len(sub_df), color="#E9E4E4", linestyle='--', width=0.8, panel=1, alpha=0.5))
            apds.append(mpf.make_addplot(sub_df['KVO'], color='purple', width=1.2, panel=2, ylabel='KVO'))
            apds.append(mpf.make_addplot([0]*len(sub_df), color='white', linestyle='--', width=1, panel=2))
            fig, axes = mpf.plot(sub_df, type='candle', style='nightclouds',
                               title=f'{simbolo} | {tipo_operacion} | {config_optima["timeframe"]} | Breakout+Reentry+KVO',
                               ylabel='Precio',
                               addplot=apds,
                               volume=False,
                               returnfig=True,
                               figsize=(14, 12),
                               panel_ratios=(3, 1, 1))
            axes[2].set_ylim([0, 100])
            axes[2].grid(True, alpha=0.3)
            axes[4].grid(True, alpha=0.3)
            buf = BytesIO()
            plt.savefig(buf, format='png', dpi=100, bbox_inches='tight', facecolor='#1a1a1a')
            buf.seek(0)
            plt.close(fig)
            return buf
        except Exception as e:
            print(f"⚠️ Error generando gráfico: {e}")
            return None

    def generar_grafico_breakout(self, simbolo, info_canal, df, tipo_breakout, config_optima):
        try:
            sub_df = df.tail(config_optima['num_velas'])
            tiempos_reg = list(range(len(sub_df)))
            resistencia_values = []
            soporte_values = []
            for i, t in enumerate(tiempos_reg):
                resist = info_canal['pendiente_resistencia'] * t + \
                        (info_canal['resistencia'] - info_canal['pendiente_resistencia'] * tiempos_reg[-1])
                sop = info_canal['pendiente_soporte'] * t + \
                     (info_canal['soporte'] - info_canal['pendiente_soporte'] * tiempos_reg[-1])
                resistencia_values.append(resist)
                soporte_values.append(sop)
            sub_df = sub_df.copy()
            sub_df['Resistencia'] = resistencia_values
            sub_df['Soporte'] = soporte_values
            df_kvo = self.calcular_kvo(df)
            kvo_values = df_kvo['xKVO'].tail(len(sub_df)).values
            trigger_values = df_kvo['xTrigger'].tail(len(sub_df)).values
            sub_df['KVO'] = kvo_values
            sub_df['KVO_Trigger'] = trigger_values
            stoch_k, stoch_d = [], []
            period = 14
            for i in range(len(sub_df)):
                if i < period - 1:
                    stoch_k.append(50)
                    stoch_d.append(50)
                else:
                    window = sub_df.iloc[i - period + 1:i + 1]
                    hh = window['High'].max()
                    ll = window['Low'].min()
                    close = window['Close'].iloc[-1]
                    k = 100 * (close - ll) / (hh - ll) if hh != ll else 50
                    stoch_k.append(k)
            stoch_k = np.array(stoch_k)
            stoch_k_smooth = np.convolve(stoch_k, np.ones(3)/3, mode='same')
            stoch_d = np.convolve(stoch_k_smooth, np.ones(3)/3, mode='same')
            sub_df['Stoch_K'] = stoch_k
            sub_df['Stoch_D'] = stoch_d
            apds = [
                mpf.make_addplot(sub_df['Resistencia'], color='#5444ff', linestyle='--', width=2, panel=0),
                mpf.make_addplot(sub_df['Soporte'], color="#5444ff", linestyle='--', width=2, panel=0),
            ]
            precio_breakout = sub_df['Close'].iloc[-1]
            breakout_line = [precio_breakout] * len(sub_df)
            if tipo_breakout == "BREAKOUT_LONG":
                color_breakout = "#D68F01"
                titulo_extra = "🚀 RUPTURA ALCISTA"
            else:
                color_breakout = '#D68F01'
                titulo_extra = "📉 RUPTURA BAJISTA"
            apds.append(mpf.make_addplot(breakout_line, color=color_breakout, linestyle='-', width=3, panel=0, alpha=0.8))
            apds.append(mpf.make_addplot(sub_df['Stoch_K'], color='#00BFFF', width=1.5, panel=1, ylabel='Stochastic'))
            apds.append(mpf.make_addplot(sub_df['Stoch_D'], color='#FF6347', width=1.5, panel=1))
            apds.append(mpf.make_addplot([80]*len(sub_df), color="#E7E4E4", linestyle='--', width=0.8, panel=1, alpha=0.5))
            apds.append(mpf.make_addplot([20]*len(sub_df), color="#E9E4E4", linestyle='--', width=0.8, panel=1, alpha=0.5))
            apds.append(mpf.make_addplot(sub_df['KVO'], color='purple', width=1.2, panel=2, ylabel='KVO'))
            apds.append(mpf.make_addplot([0]*len(sub_df), color='white', linestyle='--', width=1, panel=2))
            fig, axes = mpf.plot(sub_df, type='candle', style='nightclouds',
                                title=f'{simbolo} | {titulo_extra} | {config_optima["timeframe"]} | ⏳ ESPERANDO REENTRY',
                                ylabel='Precio',
                                addplot=apds,
                                volume=False,
                                returnfig=True,
                                figsize=(14, 12),
                                panel_ratios=(3, 1, 1))
            axes[2].set_ylim([0, 100])
            axes[2].grid(True, alpha=0.3)
            axes[4].grid(True, alpha=0.3)
            buf = BytesIO()
            plt.savefig(buf, format='png', dpi=100, bbox_inches='tight', facecolor='#1a1a1a')
            buf.seek(0)
            plt.close(fig)
            return buf
        except Exception as e:
            print(f"⚠️ Error generando gráfico de breakout: {e}")
            return None

    def enviar_alerta_breakout(self, simbolo, tipo_breakout, info_canal, df, config_optima):
        precio_cierre = df['Close'].iloc[-1]
        resistencia = info_canal['resistencia']
        soporte = info_canal['soporte']
        direccion_canal = info_canal['direccion']
        if tipo_breakout == "BREAKOUT_LONG":
            emoji_principal = "🚀"
            tipo_texto = "RUPTURA de SOPORTE"
            direccion_emoji = "⬇️"
            expectativa = "posible entrada en long si el precio reingresa al canal"
        else:
            emoji_principal = "📉"
            tipo_texto = "RUPTURA BAJISTA de RESISTENCIA"
            direccion_emoji = "⬆️"
            expectativa = "posible entrada en short si el precio reingresa al canal"
        mensaje = f"""
{emoji_principal} <b>¡BREAKOUT DETECTADO! - {simbolo}</b>
⚠️ <b>{tipo_texto}</b> {direccion_emoji}
⏰ <b>Hora:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
⏳ <b>ESPERANDO REINGRESO...</b>
📍 {expectativa}
        """
        token = self.config.get('telegram_token')
        chat_ids = self.config.get('telegram_chat_ids', [])
        if token and chat_ids:
            try:
                buf = self.generar_grafico_breakout(simbolo, info_canal, df, tipo_breakout, config_optima)
                if buf:
                    self.enviar_grafico_telegram(buf, token, chat_ids)
                    time.sleep(0.5)
                self._enviar_telegram_simple(mensaje, token, chat_ids)
            except Exception as e:
                print(f"     ❌ Error enviando alerta de breakout: {e}")

    def escanear_mercado(self):
        todos_simbolos = self.config.get('symbols', [])
        if not todos_simbolos:
            print("❌ No hay símbolos configurados")
            return 0
        total = len(todos_simbolos)
        inicio = self.indice_escaneo % total
        simbolos_a_escanear = []
        for i in range(5):
            idx = (inicio + i) % total
            simbolos_a_escanear.append(todos_simbolos[idx])
        self.indice_escaneo = (self.indice_escaneo + 5) % total
        print(f"\n🔍 Escaneando {len(simbolos_a_escanear)} de {total} símbolos (Estrategia: Breakout + Reentry + KVO)...")

        senales_encontradas = 0
        for simbolo in simbolos_a_escanear:
            try:
                if simbolo in self.operaciones_activas:
                    continue
                config_optima = self.buscar_configuracion_optima_simbolo(simbolo)
                if not config_optima:
                    print(f"   ❌ {simbolo} - No se encontró configuración válida")
                    continue
                df = self.obtener_datos_con_volumen(simbolo, config_optima['timeframe'], config_optima['num_velas'])
                if df is None or len(df) < config_optima['num_velas'] + 55:
                    print(f"   ❌ {simbolo} - Error obteniendo datos")
                    continue
                info_canal = self.calcular_canal_regresion_df(df, config_optima['num_velas'])
                if not info_canal:
                    print(f"   ❌ {simbolo} - Error calculando canal")
                    continue

                print(
                    f"📊 {simbolo} - {config_optima['timeframe']} - {config_optima['num_velas']}v | "
                    f"{info_canal['direccion']} ({info_canal['angulo_tendencia']:.1f}° - {info_canal['fuerza_texto']}) | "
                    f"Ancho: {info_canal['ancho_canal_porcentual']:.1f}% | "
                    f"Stoch: {info_canal['stoch_k']:.1f}/{info_canal['stoch_d']:.1f} | "
                    f"KVO: {info_canal['kvo']:.2f}"
                )

                if (info_canal['nivel_fuerza'] < 2 or 
                    abs(info_canal['coeficiente_pearson']) < 0.4 or 
                    info_canal['r2_score'] < 0.4):
                    continue

                if simbolo not in self.esperando_reentry:
                    tipo_breakout = self.detectar_breakout(simbolo, info_canal, df)
                    if tipo_breakout:
                        self.esperando_reentry[simbolo] = {
                            'tipo': tipo_breakout,
                            'timestamp': datetime.now(),
                            'precio_breakout': df['Close'].iloc[-1],
                            'config': config_optima
                        }
                        self.breakouts_detectados[simbolo] = {
                            'tipo': tipo_breakout,
                            'timestamp': datetime.now(),
                            'precio_breakout': df['Close'].iloc[-1]
                        }
                        print(f"     🎯 {simbolo} - Breakout registrado, esperando reingreso...")
                        self.enviar_alerta_breakout(simbolo, tipo_breakout, info_canal, df, config_optima)
                        continue

                tipo_operacion = self.detectar_reentry(simbolo, info_canal, df)
                if tipo_operacion:
                    precio_entrada, tp, sl = self.calcular_niveles_entrada(tipo_operacion, info_canal, df['Close'].iloc[-1])
                    if not all([precio_entrada, tp, sl]):
                        continue
                    if simbolo in self.breakout_history:
                        ultimo_breakout = self.breakout_history[simbolo]
                        tiempo_desde_ultimo = (datetime.now() - ultimo_breakout).total_seconds() / 3600
                        if tiempo_desde_ultimo < 2:
                            continue

                    breakout_info = self.esperando_reentry[simbolo]
                    self.generar_senal_operacion(simbolo, tipo_operacion, precio_entrada, tp, sl, info_canal, df, config_optima, breakout_info)
                    senales_encontradas += 1
                    self.breakout_history[simbolo] = datetime.now()
                    del self.esperando_reentry[simbolo]

            except Exception as e:
                print(f"⚠️ Error analizando {simbolo}: {e}")
                continue

        if senales_encontradas > 0:
            print(f"✅ Se encontraron {senales_encontradas} señales de trading")
        else:
            print("❌ No se encontraron señales en este ciclo")
        return senales_encontradas

    def verificar_cierre_operaciones(self):
        return []  # Bitget maneja TP/SL automáticamente

    def ejecutar_analisis(self):
        self.guardar_estado()  # No hay verificación de cierre porque TP/SL son automáticos
        return self.escanear_mercado()

    def _enviar_telegram_simple(self, mensaje, token, chat_ids):
        if not token or not chat_ids:
            return False
        for chat_id in chat_ids:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            payload = {'chat_id': chat_id, 'text': mensaje, 'parse_mode': 'HTML'}
            try:
                requests.post(url, json=payload, timeout=10)
            except:
                pass
        return True

    def enviar_grafico_telegram(self, buf, token, chat_ids):
        if not buf or not token or not chat_ids:
            return False
        for chat_id in chat_ids:
            url = f"https://api.telegram.org/bot{token}/sendPhoto"
            try:
                buf.seek(0)
                files = {'photo': ('grafico.png', buf.read(), 'image/png')}
                data = {'chat_id': chat_id}
                requests.post(url, files=files, data=data, timeout=120)
            except:
                pass
        return True

    # === Métodos auxiliares matemáticos (sin cambios) ===
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
        pendiente = (n * sum_xy - sum_x * sum_y) / (n * sum_x2 - sum_x * sum_x) if (n * sum_x2 - sum_x * sum_x) != 0 else 0
        angulo_radianes = math.atan(pendiente * len(x) / (max(y) - min(y))) if (max(y) - min(y)) != 0 else 0
        return pearson, math.degrees(angulo_radianes)

    def clasificar_fuerza_tendencia(self, angulo_grados):
        angulo_abs = abs(angulo_grados)
        if angulo_abs < 3: return "💔 Muy Débil", 1
        elif angulo_abs < 13: return "❤️‍🩹 Débil", 2
        elif angulo_abs < 27: return "💛 Moderada", 3
        elif angulo_abs < 45: return "💚 Fuerte", 4
        else: return "💙 Muy Fuerte", 5

    def determinar_direccion_tendencia(self, angulo_grados, umbral_minimo=1):
        if abs(angulo_grados) < umbral_minimo: return "⚪ RANGO"
        elif angulo_grados > 0: return "🟢 ALCISTA"
        else: return "🔴 BAJISTA"

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
                    'stoch_k', 'stoch_d', 'kvo', 'breakout_usado'
                ])

    def registrar_operacion(self, datos_operacion):
        pass  # TP/SL automáticos → no cerramos manualmente

# ---------------------------
# CONFIGURACIÓN
# ---------------------------

def crear_config_desde_entorno():
    directorio_actual = os.path.dirname(os.path.abspath(__file__))
    telegram_chat_ids_str = os.environ.get('TELEGRAM_CHAT_ID', '-1002272872445')
    telegram_chat_ids = [cid.strip() for cid in telegram_chat_ids_str.split(',') if cid.strip()]
    return {
        'min_channel_width_percent': 4.0,
        'trend_threshold_degrees': 16.0,
        'min_trend_strength_degrees': 16.0,
        'entry_margin': 0.001,
        'min_rr_ratio': 1.2,
        'scan_interval_minutes': 1,
        'timeframes': ['5m', '15m', '30m', '1h', '4h'],
        'velas_options': [80, 100, 120, 150, 200],
        'symbols': [
            'BTCUSDT','ETHUSDT','DOTUSDT','LINKUSDT','BNBUSDT','XRPUSDT','SOLUSDT','AVAXUSDT',
            'DOGEUSDT','LTCUSDT','ATOMUSDT','XLMUSDT','ALGOUSDT','VETUSDT','ICPUSDT','FILUSDT',
            'BCHUSDT','EOSUSDT','TRXUSDT','XTZUSDT','SUSHIUSDT','COMPUSDT','YFIUSDT','ETCUSDT',
            'SNXUSDT','RENUSDT','1INCHUSDT','NEOUSDT','ZILUSDT','HOTUSDT','ENJUSDT','ZECUSDT'
        ],
        'telegram_token': os.environ.get('TELEGRAM_TOKEN'),
        'telegram_chat_ids': telegram_chat_ids,
        'auto_optimize': True,
        'min_samples_optimizacion': 30,
        'reevaluacion_horas': 24,
        'log_path': os.path.join(directorio_actual, 'operaciones_log_v24.csv'),
        'estado_file': os.path.join(directorio_actual, 'estado_bot_v24.json'),
        # --- Bitget Credenciales ---
        'bitget_api_key': os.environ.get('BITGET_API_KEY'),
        'bitget_api_secret': os.environ.get('BITGET_API_SECRET'),
        'bitget_api_passphrase': os.environ.get('BITGET_API_PASSPHRASE'),
    }

# ---------------------------
# FLASK APP
# ---------------------------

app = Flask(__name__)
config = crear_config_desde_entorno()
bot = TradingBot(config)

def run_bot_loop():
    while True:
        try:
            bot.ejecutar_analisis()
            time.sleep(bot.config.get('scan_interval_minutes', 1) * 60)
        except Exception as e:
            print(f"Error en el hilo del bot: {e}", file=sys.stderr)
            time.sleep(60)

bot_thread = threading.Thread(target=run_bot_loop, daemon=True)
bot_thread.start()

@app.route('/')
def index():
    return "Bot Breakout + Reentry + KVO + Bitget está en línea.", 200

@app.route('/webhook', methods=['POST'])
def telegram_webhook():
    if request.is_json:
        update = request.get_json()
        print(f"Update recibido: {json.dumps(update)}", file=sys.stdout)
        return jsonify({"status": "ok"}), 200
    return jsonify({"error": "Request must be JSON"}), 400

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
    app.run(debug=True, port=int(os.environ.get('PORT', 5000)))
