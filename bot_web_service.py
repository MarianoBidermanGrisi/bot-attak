# bot_web_service.py
# Adaptación para Render del bot Breakout + Reentry
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

# Configurar logging básico
logging.basicConfig(level=logging.INFO, stream=sys.stdout, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ---------------------------
# Cliente Bitget API
# ---------------------------
class BitgetClient:
    def __init__(self, api_key, api_secret, passphrase):
        self.api_key = api_key
        self.api_secret = api_secret
        self.passphrase = passphrase
        self.base_url = "https://api.bitget.com"
        
    def _generate_signature(self, timestamp, method, request_path, body=''):
        """Genera firma para autenticación"""
        if body:
            body = json.dumps(body) if isinstance(body, dict) else body
        else:
            body = ''
        
        message = timestamp + method.upper() + request_path + body
        mac = hmac.new(
            bytes(self.api_secret, encoding='utf8'),
            bytes(message, encoding='utf8'),
            digestmod='sha256'
        )
        return base64.b64encode(mac.digest()).decode()
    
    def _get_headers(self, method, request_path, body=''):
        """Genera headers para request"""
        timestamp = str(int(time.time() * 1000))
        
        if body:
            body_str = json.dumps(body) if isinstance(body, dict) else body
        else:
            body_str = ''
        
        sign = self._generate_signature(timestamp, method.upper(), request_path, body_str)
        
        return {
            'Content-Type': 'application/json',
            'ACCESS-KEY': self.api_key,
            'ACCESS-SIGN': sign,
            'ACCESS-TIMESTAMP': timestamp,
            'ACCESS-PASSPHRASE': self.passphrase,
            'locale': 'en-US'
        }
    
    def get_account_info(self, product_type='umcbl'):
        """Obtiene información de la cuenta - API V2"""
        try:
            request_path = f'/api/v2/mix/account/accounts'
            params = {'productType': product_type}
            headers = self._get_headers('GET', request_path)
            
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
            
            print(f"⚠️ Error obteniendo cuenta V2: {response.text}")
            return None
            
        except Exception as e:
            print(f"❌ Error en get_account_info V2: {e}")
            return None
    
    def get_available_balance(self):
        """Obtiene el balance disponible de USDT"""
        try:
            account_info = self.get_account_info()
            if account_info:
                for account in account_info:
                    if account.get('marginCoin') == 'USDT':
                        return float(account.get('available', 0))
            return 0
        except Exception as e:
            print(f"❌ Error obteniendo balance: {e}")
            return 0
    
    def get_symbol_info(self, symbol):
        """Obtiene información del símbolo - API V2"""
        try:
            request_path = f'/api/v2/mix/market/contracts'
            params = {'productType': 'umcbl'}
            headers = self._get_headers('GET', request_path)
            
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
            print(f"❌ Error obteniendo info del símbolo V2: {e}")
            return None
    
    def place_order(self, symbol, side, order_type, size, price=None, 
                    client_order_id=None, time_in_force='normal'):
        """Coloca una orden en Bitget - API V2"""
        try:
            request_path = '/api/v2/mix/order/place-order'
            
            body = {
                'symbol': symbol,
                'productType': 'umcbl',
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
                    print(f"✅ Orden colocada exitosamente: {data.get('data', {})}")
                    return data.get('data', {})
                else:
                    print(f"❌ Error en orden V2: {data.get('msg', 'Unknown error')}")
                    return None
            else:
                print(f"❌ Error HTTP V2: {response.status_code} - {response.text}")
                return None
                
        except Exception as e:
            print(f"❌ Error colocando orden V2: {e}")
            return None
    
    def place_plan_order(self, symbol, side, trigger_price, order_type, size, 
                         price=None, plan_type='normal_plan'):
        """Coloca orden planificada (TP/SL) - API V2"""
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
            
            print(f"⚠️ Error en plan order V2: {response.text}")
            return None
            
        except Exception as e:
            print(f"❌ Error colocando plan order V2: {e}")
            return None
    
    def set_leverage(self, symbol, leverage, hold_side='long'):
        """Configura el apalancamiento - API V2"""
        try:
            request_path = '/api/v2/mix/account/set-leverage'
            
            body = {
                'symbol': symbol,
                'productType': 'umcbl',
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
                    print(f"✅ Apalancamiento {leverage}x configurado para {symbol}")
                    return True
            
            print(f"⚠️ Error configurando leverage V2: {response.text}")
            return False
            
        except Exception as e:
            print(f"❌ Error en set_leverage V2: {e}")
            return False
    
    def get_positions(self, symbol=None, product_type='umcbl'):
        """Obtiene posiciones abiertas - API V2"""
        try:
            request_path = '/api/v2/mix/position/all-position'
            params = {'productType': product_type, 'marginCoin': 'USDT'}
            
            if symbol:
                params['symbol'] = symbol
            
            headers = self._get_headers('GET', request_path)
            
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
            
            return []
            
        except Exception as e:
            print(f"❌ Error obteniendo posiciones V2: {e}")
            return []
    
    def get_klines(self, symbol, interval='5m', limit=200):
        """Obtiene datos de velas (klines) de Bitget - API V2"""
        try:
            interval_map = {
                '1m': '1m',
                '3m': '3m', 
                '5m': '5m',
                '15m': '15m',
                '30m': '30m',
                '1h': '1H',
                '4h': '4H',
                '1d': '1D'
            }
            
            bitget_interval = interval_map.get(interval, '5m')
            
            request_path = f'/api/v2/mix/market/candles'
            
            params = {
                'symbol': symbol,
                'productType': 'umcbl',
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
                    print(f"⚠️ Error API V2: {data.get('msg', 'Unknown error')}")
            
            print(f"⚠️ Error obteniendo klines V2: {response.text}")
            return None
            
        except Exception as e:
            print(f"❌ Error en get_klines V2: {e}")
            return None

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
            print("⚠️ No se encontró operaciones_log.csv (optimizador)")
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
                print("⚠️ Error guardando mejores_parametros.json:", e)
        else:
            print("⚠️ No se encontró una configuración mejor")
            
        return mejores_param

# ---------------------------
# BOT PRINCIPAL - BITGET FUTURES
# ---------------------------
class TradingBot:
    def __init__(self, config):
        self.config = config
        self.log_path = config.get('log_path', 'operaciones_log.csv')
        self.auto_optimize = config.get('auto_optimize', True)
        
        # Inicializar cliente Bitget
        self.bitget = BitgetClient(
            api_key=config.get('bitget_api_key'),
            api_secret=config.get('bitget_api_secret'),
            passphrase=config.get('bitget_passphrase')
        )
        
        self.leverage = config.get('leverage', 20)
        self.capital_por_operacion = config.get('capital_por_operacion', 5.0)
        
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
                print("⚠️ Error en optimización automática:", e)
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
        
        # Verificar conexión y balance
        self.verificar_conexion_bitget()

    def verificar_conexion_bitget(self):
        """Verifica la conexión con Bitget y muestra balance"""
        try:
            balance = self.bitget.get_available_balance()
            print(f"\n✅ Conexión con Bitget establecida")
            print(f"💰 Balance disponible: {balance:.2f} USDT")
            
            if balance < self.capital_por_operacion:
                print(f"⚠️ ADVERTENCIA: Balance insuficiente para operar")
                print(f"   Requerido: {self.capital_por_operacion} USDT")
                print(f"   Disponible: {balance:.2f} USDT")
            
            return True
            
        except Exception as e:
            print(f"❌ Error conectando con Bitget: {e}")
            print("⚠️ Verifica tus credenciales API")
            return False

    def ejecutar_operacion_real(self, simbolo, tipo_operacion, precio_entrada, tp, sl, info_canal, config_optima):
        """Ejecuta operación real en Bitget con apalancamiento 20x"""
        try:
            print(f"\n🚀 EJECUTANDO OPERACIÓN REAL EN BITGET")
            print(f"   Símbolo: {simbolo}")
            print(f"   Tipo: {tipo_operacion}")
            print(f"   Apalancamiento: {self.leverage}x")
            print(f"   Capital: ${self.capital_por_operacion}")
            
            # 1. Configurar apalancamiento
            hold_side = 'long' if tipo_operacion == 'LONG' else 'short'
            leverage_ok = self.bitget.set_leverage(simbolo, self.leverage, hold_side)
            
            if not leverage_ok:
                print("❌ Error configurando apalancamiento")
                return None
            
            time.sleep(0.5)
            
            # 2. Obtener información del símbolo
            symbol_info = self.bitget.get_symbol_info(simbolo)
            if not symbol_info:
                print(f"❌ No se pudo obtener info de {simbolo}")
                return None
            
            # 3. Calcular tamaño de la posición
            size_multiplier = float(symbol_info.get('sizeMultiplier', 1))
            min_trade_num = float(symbol_info.get('minTradeNum', 1))
            
            # Calcular cantidad en USD
            cantidad_usd = self.capital_por_operacion * self.leverage
            
            # Convertir a cantidad de contratos
            cantidad_contratos = cantidad_usd / precio_entrada
            cantidad_contratos = round(cantidad_contratos / size_multiplier) * size_multiplier
            
            # Verificar mínimo
            if cantidad_contratos < min_trade_num:
                cantidad_contratos = min_trade_num
            
            print(f"   📊 Cantidad: {cantidad_contratos} contratos")
            print(f"   💵 Valor nocional: ${cantidad_contratos * precio_entrada:.2f}")
            
            # 4. Abrir posición
            side = 'open_long' if tipo_operacion == 'LONG' else 'open_short'
            
            orden_entrada = self.bitget.place_order(
                symbol=simbolo,
                side=side,
                order_type='market',
                size=cantidad_contratos
            )
            
            if not orden_entrada:
                print("❌ Error abriendo posición")
                return None
            
            print(f"✅ Posición abierta: {orden_entrada}")
            
            time.sleep(1)
            
            # 5. Colocar Stop Loss
            sl_side = 'close_long' if tipo_operacion == 'LONG' else 'close_short'
            
            orden_sl = self.bitget.place_plan_order(
                symbol=simbolo,
                side=sl_side,
                trigger_price=sl,
                order_type='market',
                size=cantidad_contratos,
                plan_type='loss_plan'
            )
            
            if orden_sl:
                print(f"✅ Stop Loss configurado en: {sl:.8f}")
            else:
                print("⚠️ Error configurando Stop Loss")
            
            time.sleep(0.5)
            
            # 6. Colocar Take Profit
            orden_tp = self.bitget.place_plan_order(
                symbol=simbolo,
                side=sl_side,
                trigger_price=tp,
                order_type='market',
                size=cantidad_contratos,
                plan_type='normal_plan'
            )
            
            if orden_tp:
                print(f"✅ Take Profit configurado en: {tp:.8f}")
            else:
                print("⚠️ Error configurando Take Profit")
            
            # 7. Registrar operación
            operacion_data = {
                'orden_entrada': orden_entrada,
                'orden_sl': orden_sl,
                'orden_tp': orden_tp,
                'cantidad_contratos': cantidad_contratos,
                'precio_entrada': precio_entrada,
                'take_profit': tp,
                'stop_loss': sl,
                'leverage': self.leverage,
                'capital_usado': self.capital_por_operacion,
                'tipo': tipo_operacion,
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
                'breakout_usado': True
            }
            
            print(f"\n✅ OPERACIÓN EJECUTADA EXITOSAMENTE")
            print(f"   ID Orden: {orden_entrada.get('orderId', 'N/A')}")
            print(f"   Contratos: {cantidad_contratos}")
            print(f"   Entrada: {precio_entrada:.8f}")
            print(f"   SL: {sl:.8f} (-2%)")
            print(f"   TP: {tp:.8f}")
            
            return operacion_data
            
        except Exception as e:
            print(f"❌ Error ejecutando operación: {e}")
            import traceback
            traceback.print_exc()
            return None

    def cargar_estado(self):
        """Carga el estado previo del bot"""
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
            print(f"⚠️ Error cargando estado previo: {e}")
            print("   Se iniciará con estado limpio")

    def guardar_estado(self):
        """Guarda el estado actual del bot"""
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
            print(f"⚠️ Error guardando estado: {e}")

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
        """Obtiene datos de Bitget con configuración específica"""
        try:
            klines = self.bitget.get_klines(simbolo, timeframe, num_velas + 14)
            
            if not klines or len(klines) == 0:
                return None
            
            # Bitget retorna en orden descendente, invertir
            klines.reverse()
            
            maximos = [float(vela[2]) for vela in klines]
            minimos = [float(vela[3]) for vela in klines]
            cierres = [float(vela[4]) for vela in klines]
            tiempos = list(range(len(klines)))
            
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
            print(f"⚠️ Error obteniendo datos de {simbolo}: {e}")
            return None

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
        
        pendiente = (n * sum_xy - sum_x * sum_y) / denominator
        intercepto = (sum_y - pendiente * sum_x) / n
        
        return pendiente, intercepto

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
        if len(x) >= 2:
            dx = x[-1] - x[0]
            dy = y[-1] - y[0]
            if dx != 0:
                angulo_rad = math.atan(dy / dx)
                angulo_deg = math.degrees(angulo_rad)
            else:
                angulo_deg = 90 if dy > 0 else -90
        else:
            angulo_deg = 0
        
        return pearson, angulo_deg

    def clasificar_fuerza_tendencia(self, angulo):
        """Clasifica la fuerza de la tendencia según el ángulo"""
        abs_angulo = abs(angulo)
        
        if abs_angulo >= 40:
            return "MUY FUERTE", 4
        elif abs_angulo >= 25:
            return "FUERTE", 3
        elif abs_angulo >= 15:
            return "MODERADA", 2
        else:
            return "DÉBIL", 1

    def determinar_direccion_tendencia(self, angulo, umbral=0.5):
        """Determina la dirección de la tendencia"""
        if angulo > umbral:
            return "🟢 ALCISTA"
        elif angulo < -umbral:
            return "🔴 BAJISTA"
        else:
            return "🟡 LATERAL"

    def calcular_r2(self, y, x, pendiente, intercepto):
        """Calcula el coeficiente de determinación R²"""
        if len(y) != len(x) or len(y) < 2:
            return 0
        
        # Calcular valores predichos
        y_pred = [pendiente * xi + intercepto for xi in x]
        
        # Calcular media de y
        y_mean = sum(y) / len(y)
        
        # Calcular suma total de cuadrados (SST) y suma residual de cuadrados (SSR)
        sst = sum((yi - y_mean) ** 2 for yi in y)
        ssr = sum((yi - y_pred[i]) ** 2 for i, yi in enumerate(y))
        
        # Calcular R²
        if sst == 0:
            return 0
        
        r2 = 1 - (ssr / sst)
        return r2

    def calcular_stochastic(self, datos_mercado, k_period=14, d_period=3):
        """Calcula el indicador estocástico"""
        try:
            cierres = datos_mercado['cierres']
            maximos = datos_mercado['maximos']
            minimos = datos_mercado['minimos']
            
            if len(cierres) < k_period:
                return 50, 50
            
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
            
            # Calcular %D (media móvil de %K)
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
                
        except Exception as e:
            print(f"⚠️ Error calculando estocástico: {e}")
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
        Envía alerta de BREAKOUT detectado a Telegram con gráfico
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
        Marca visualmente la ruptura del canal
        """
        try:
            import matplotlib.font_manager as fm
            plt.rcParams['font.family'] = ['DejaVu Sans', 'Segoe UI Emoji', 'Apple Color Emoji', 'Noto Color Emoji']
            
            # Obtener datos históricos
            klines = self.bitget.get_klines(simbolo, config_optima['timeframe'], config_optima['num_velas'])
            
            if not klines or len(klines) == 0:
                return None
                
            # Convertir a DataFrame
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
                color_breakout = "#D68F01"  # Verde para alcista
                titulo_extra = "🚀 RUPTURA ALCISTA"
            else:
                color_breakout = '#D68F01'  # Rojo para bajista
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
        """Calcula niveles con SL fijo al 2%"""
        if not info_canal:
            return None, None, None
        
        resistencia = info_canal['resistencia']
        soporte = info_canal['soporte']
        ancho_canal = resistencia - soporte
        
        # SL FIJO AL 2%
        sl_porcentaje = 0.02

        if tipo_operacion == "LONG":
            precio_entrada = precio_actual
            stop_loss = precio_entrada * (1 - sl_porcentaje)
            # TP en la parte opuesta del canal (resistencia)
            take_profit = resistencia
        else:
            precio_entrada = precio_actual
            stop_loss = precio_entrada * (1 + sl_porcentaje)
            # TP en la parte opuesta del canal (soporte)
            take_profit = soporte
        
        return precio_entrada, take_profit, stop_loss

    def generar_grafico_profesional(self, simbolo, info_canal, datos_mercado, precio_entrada, tp, sl, tipo_operacion):
        """Genera un gráfico profesional con matplotlib y mplfinance"""
        try:
            import matplotlib.font_manager as fm
            plt.rcParams['font.family'] = ['DejaVu Sans', 'Segoe UI Emoji', 'Apple Color Emoji', 'Noto Color Emoji']
            
            # Obtener datos históricos
            klines = self.bitget.get_klines(simbolo, info_canal['timeframe'], info_canal['num_velas'])
            
            if not klines or len(klines) == 0:
                return None
                
            # Convertir a DataFrame
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
            
            # Añadir líneas de entrada, TP y SL
            entrada_line = [precio_entrada] * len(df)
            tp_line = [tp] * len(df)
            sl_line = [sl] * len(df)
            
            if tipo_operacion == "LONG":
                color_entrada = "#00ff00"
                color_tp = "#00ff00"
                color_sl = "#ff0000"
            else:
                color_entrada = "#ff0000"
                color_tp = "#ff0000"
                color_sl = "#00ff00"
                
            apds.append(mpf.make_addplot(entrada_line, color=color_entrada, linestyle='-', width=2, panel=0, alpha=0.7))
            apds.append(mpf.make_addplot(tp_line, color=color_tp, linestyle='--', width=1.5, panel=0, alpha=0.7))
            apds.append(mpf.make_addplot(sl_line, color=color_sl, linestyle='--', width=1.5, panel=0, alpha=0.7))
            
            # Stochastic
            apds.append(mpf.make_addplot(df['Stoch_K'], color='#00BFFF', width=1.5, panel=1, ylabel='Stochastic'))
            apds.append(mpf.make_addplot(df['Stoch_D'], color='#FF6347', width=1.5, panel=1))
            overbought = [80] * len(df)
            oversold = [20] * len(df)
            apds.append(mpf.make_addplot(overbought, color="#E7E4E4", linestyle='--', width=0.8, panel=1, alpha=0.5))
            apds.append(mpf.make_addplot(oversold, color="#E9E4E4", linestyle='--', width=0.8, panel=1, alpha=0.5))
            
            # Crear gráfico
            fig, axes = mpf.plot(df, type='candle', style='nightclouds',
                               title=f'{simbolo} | {tipo_operacion} | {info_canal["timeframe"]} | {info_canal["direccion"]}',
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
        """Envía un gráfico a través de Telegram"""
        try:
            buf.seek(0)
            for chat_id in chat_ids:
                url = f"https://api.telegram.org/bot{token}/sendPhoto"
                files = {'photo': ('chart.png', buf, 'image/png')}
                data = {'chat_id': chat_id}
                requests.post(url, files=files, data=data)
        except Exception as e:
            print(f"⚠️ Error enviando gráfico a Telegram: {e}")

    def _enviar_telegram_simple(self, mensaje, token, chat_ids):
        """Envía un mensaje de texto simple a Telegram"""
        try:
            for chat_id in chat_ids:
                url = f"https://api.telegram.org/bot{token}/sendMessage"
                data = {
                    'chat_id': chat_id,
                    'text': mensaje,
                    'parse_mode': 'HTML'
                }
                requests.post(url, json=data)
        except Exception as e:
            print(f"⚠️ Error enviando mensaje a Telegram: {e}")

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
        factor_profit = (avg_ganancia/avg_perdida) if avg_perdida > 0 else 0
        breakouts = self.contar_breakouts_semana()
        reporte = f"""
📊 <b>REPORTE SEMANAL DE TRADING</b> 📊
🗓️ <b>Período:</b> {(datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')} al {datetime.now().strftime('%Y-%m-%d')}
📈 <b>Operaciones Totales:</b> {total_ops}
✅ <b>Ganadoras (TP):</b> {wins}
❌ <Perdedoras (SL):</b> {losses}
🎯 <b>Win Rate:</b> {winrate:.2f}%
💰 <b>PnL Total:</b> {pnl_total:.2f}%
🏆 <b>Mejor Operación:</b> {mejor_op['symbol']} ({mejor_op['tipo']}) - {mejor_op['pnl_percent']:.2f}%
💔 <b>Peor Operación:</b> {peor_op['symbol']} ({peor_op['tipo']}) - {peor_op['pnl_percent']:.2f}%
📊 <b>Ganancia Promedio:</b> {avg_ganancia:.2f}%
📉 <b>Pérdida Promedio:</b> {avg_perdida:.2f}%
⚖️ <b>Factor de Beneficio:</b> {factor_profit:.2f}
🚀 <b>Operaciones con Breakout:</b> {breakouts} ({(breakouts/total_ops*100) if total_ops > 0 else 0:.1f}%)
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

    def run(self):
        """Bucle principal del bot"""
        print("🚀 Iniciando bot de trading Breakout + Reentry...")
        while True:
            try:
                self.escanear_mercado()
                self.guardar_estado()
                time.sleep(60)
            except KeyboardInterrupt:
                print("\n⏹️ Bot detenido por el usuario")
                self.guardar_estado()
                break
            except Exception as e:
                print(f"❌ Error en bucle principal: {e}")
                import traceback
                traceback.print_exc()
                time.sleep(60)

# ---------------------------
# CONFIGURACIÓN Y EJECUCIÓN
# ---------------------------
app = Flask(__name__)

# Cargar configuración desde variables de entorno o archivo
def load_config():
    config = {
        'bitget_api_key': os.getenv('BITGET_API_KEY'),
        'bitget_api_secret': os.getenv('BITGET_API_SECRET'),
        'bitget_passphrase': os.getenv('BITGET_PASSPHRASE'),
        'telegram_token': os.getenv('TELEGRAM_TOKEN'),
        'telegram_chat_ids': [int(id) for id in os.getenv('TELEGRAM_CHAT_IDS', '').split(',') if id],
        'symbols': os.getenv('SYMBOLS', 'BTCUSDT,ETHUSDT').split(','),
        'leverage': int(os.getenv('LEVERAGE', 20)),
        'capital_por_operacion': float(os.getenv('CAPITAL_POR_OPERACION', 5.0)),
        'timeframes': os.getenv('TIMEFRAMES', '1m,3m,5m,15m,30m').split(','),
        'velas_options': [int(v) for v in os.getenv('VELAS_OPTIONS', '80,100,120,150,200').split(',')],
        'auto_optimize': os.getenv('AUTO_OPTIMIZE', 'true').lower() == 'true',
        'min_samples_optimizacion': int(os.getenv('MIN_SAMPLES_OPTIMIZACION', 15)),
        'min_channel_width_percent': float(os.getenv('MIN_CHANNEL_WIDTH_PERCENT', 4.0)),
        'trend_threshold_degrees': float(os.getenv('TREND_THRESHOLD_DEGREES', 13)),
        'min_trend_strength_degrees': float(os.getenv('MIN_TRENGTH_DEGREES', 16)),
        'entry_margin': float(os.getenv('ENTRY_MARGIN', 0.001)),
        'log_path': os.getenv('LOG_PATH', 'operaciones_log.csv'),
        'estado_file': os.getenv('ESTADO_FILE', 'estado_bot.json')
    }
    return config

# Inicializar el bot
bot_config = load_config()
bot = TradingBot(bot_config)

@app.route('/')
def index():
    return jsonify({
        'status': 'running',
        'timestamp': datetime.now().isoformat(),
        'active_operations': len(bot.operaciones_activas),
        'waiting_reentry': len(bot.esperando_reentry)
    })

@app.route('/status')
def status():
    return jsonify({
        'status': 'running',
        'timestamp': datetime.now().isoformat(),
        'config': {
            'symbols': bot.config['symbols'],
            'leverage': bot.leverage,
            'capital': bot.capital_por_operacion
        },
        'operations': bot.operaciones_activas,
        'waiting_reentry': list(bot.esperando_reentry.keys()),
        'last_optimization': bot.ultima_optimizacion.isoformat()
    })

@app.route('/report')
def report():
    reporte = bot.generar_reporte_semanal()
    return jsonify({'report': reporte, 'timestamp': datetime.now().isoformat()})

def run_bot():
    """Ejecuta el bot en un hilo separado"""
    bot.run()

# Iniciar el bot en un hilo separado
bot_thread = threading.Thread(target=run_bot, daemon=True)
bot_thread.start()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
