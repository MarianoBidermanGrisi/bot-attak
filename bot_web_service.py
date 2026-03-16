# bot_web_service.py
# Adaptación para ejecución local del bot Breakout + Reentry
import requests
import ccxt
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
import time
import json
import sys
try:
    sys.stdout.reconfigure(encoding='utf-8')
except AttributeError:
    pass
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
        (smoothed_dm_plus / np.where(smoothed_true_range == 0, np.nan, smoothed_true_range)) * 100
    )
    
    # DIMinus = SmoothedDirectionalMovementMinus / SmoothedTrueRange * 100
    di_minus = np.where(
        np.isnan(safe_tr),
        np.nan,
        (smoothed_dm_minus / np.where(smoothed_true_range == 0, np.nan, smoothed_true_range)) * 100
    )
    
    # DX = abs(DIPlus-DIMinus) / (DIPlus+DIMinus)*100
    di_sum = np.nan_to_num(di_plus) + np.nan_to_num(di_minus)
    di_diff = np.abs(np.nan_to_num(di_plus) - np.nan_to_num(di_minus))
    
    dx = np.where(
        di_sum == 0,
        0,
        (di_diff / np.where(di_sum == 0, np.nan, di_sum)) * 100
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
            np.abs(resultado[high_col] - resultado[close_col].shift(1)),
            np.abs(resultado[low_col] - resultado[close_col].shift(1))
        )
    )
    
    # Calcular Directional Movement
    resultado['up_move'] = resultado[high_col] - resultado[high_col].shift(1)
    resultado['down_move'] = resultado[low_col].shift(1) - resultado[low_col]
    
    # DirectionalMovementPlus
    resultado['dm_plus'] = np.where(
        (resultado['up_move'] > resultado['down_move']) & (resultado['up_move'] > 0),
        resultado['up_move'],
        0
    )
    
    # DirectionalMovementMinus
    resultado['dm_minus'] = np.where(
        (resultado['down_move'] > resultado['up_move']) & (resultado['down_move'] > 0),
        resultado['down_move'],
        0
    )
    
    # Suavizado usando la fórmula de Pine Script
    smoothed_tr = np.zeros(len(resultado))
    smoothed_dm_plus = np.zeros(len(resultado))
    smoothed_dm_minus = np.zeros(len(resultado))
    
    for i in range(len(resultado)):
        if i == 0:
            smoothed_tr[i] = resultado['tr'].iloc[i]
            smoothed_dm_plus[i] = resultado['dm_plus'].iloc[i]
            smoothed_dm_minus[i] = resultado['dm_minus'].iloc[i]
        else:
            smoothed_tr[i] = smoothed_tr[i-1] - smoothed_tr[i-1]/length + resultado['tr'].iloc[i]
            smoothed_dm_plus[i] = smoothed_dm_plus[i-1] - smoothed_dm_plus[i-1]/length + resultado['dm_plus'].iloc[i]
            smoothed_dm_minus[i] = smoothed_dm_minus[i-1] - smoothed_dm_minus[i-1]/length + resultado['dm_minus'].iloc[i]
    
    resultado['smoothed_tr'] = smoothed_tr
    resultado['smoothed_dm_plus'] = smoothed_dm_plus
    resultado['smoothed_dm_minus'] = smoothed_dm_minus
    
    # Calcular DI+ y DI-
    resultado['DI+'] = (resultado['smoothed_dm_plus'] / resultado['smoothed_tr']) * 100
    resultado['DI-'] = (resultado['smoothed_dm_minus'] / resultado['smoothed_tr']) * 100
    
    # Calcular DX
    di_sum = resultado['DI+'] + resultado['DI-']
    di_diff = np.abs(resultado['DI+'] - resultado['DI-'])
    resultado['DX'] = (di_diff / di_sum) * 100
    
    # Calcular ADX como SMA de DX
    resultado['ADX'] = resultado['DX'].rolling(window=length).mean()
    
    # Limpiar columnas intermedias
    resultado.drop(columns=['tr', 'up_move', 'down_move', 'dm_plus', 'dm_minus', 
                           'smoothed_tr', 'smoothed_dm_plus', 'smoothed_dm_minus', 'DX'], 
                   inplace=True)
    
    return resultado


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
# SÍMBOLOS OMITIDOS - Lista de símbolos a excluir en la generación dinámica
# ---------------------------
MARGEN_USDT = 1 
PALANCA_ESTRICTA = 10
stopFijo = 0.016
NUM_MONEDAS_ESCANEAR = 200

def a_decimal_estricto(numero, precision_raw):
    if numero is None: return None
    if isinstance(precision_raw, float):
        precision_str = format(precision_raw, 'f').rstrip('0')
        decimales = len(precision_str.split('.')[1]) if '.' in precision_str else 0
    else:
        decimales = int(precision_raw)
    valor = Decimal(str(numero)).quantize(Decimal(str(10**-decimales)), rounding=ROUND_DOWN)
    return str(valor)

SIMBOLOS_OMITIDOS = {
    # stablecoins y relacionados
    'USDTUSDT', 'USDCUSDT', 'DAIUSDT', 'TUSDUSDT', 'BUSDUSDT',
    # velas y fractional
    'BTCVUSDT', 'ETHWUSDT',
    # duplicados y errores comunes
    'LUNA2USDT', 'LUNAUSDT',
    # futuros perpetuos con sufijos especiales (ya no disponibles o renombrados)
    'DOGEUSDTS', 'XRPUSDTS',
    # tokens muy illiquidos o delistados
    'SRMUSDT', 'FTTUSDT', 'FTMUSDT', 'CELRUSDT',
    # pares con bajo volumen histórico
    'KSMUSDT', 'DOTUSDT', 'NEARUSDT',
    # repetir para asegurar
    'LUNAUSDT', 'LUNA2USDT'
}

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
        self.exchange = None
        if config.get('bitget_api_key') and config.get('bitget_api_secret') and config.get('bitget_passphrase'):
            try:
                api_key = config['bitget_api_key']
                logger.info(f"Cliente Bitget FUTUROS inicializado con API Key: {api_key[:10]}...")
                logger.info("Verificando credenciales Bitget FUTUROS...")
                self.exchange = ccxt.bitget({
                    'apiKey': api_key,           
                    'secret': config['bitget_api_secret'],        
                    'password': config['bitget_passphrase'],      
                    'options': {'defaultType': 'swap'},
                    'enableRateLimit': True
                })

                # --- MONKEY PATCHING PARA COMPATIBILIDAD CON LÓGICA EXISTENTE ---
                def get_klines_patch(symbol, timeframe, limit=150):
                    try:
                        # Convertir símbolo simple a formato CCXT si es necesario
                        symbol_ccxt = symbol
                        if 'USDT' in symbol and '/' not in symbol:
                            symbol_ccxt = symbol.replace('USDT', '/USDT:USDT')
                        
                        ohlcv = self.exchange.fetch_ohlcv(symbol_ccxt, timeframe, limit=limit)
                        # Re-formatear para que coincida con lo que esperaba el cliente antiguo si es necesario
                        # El cliente antiguo devolvía: [[timestamp, open, high, low, close, volume], ...]
                        # CCXT ya devuelve exactamente eso.
                        return ohlcv
                    except Exception as e:
                        logger.error(f"Error en get_klines (patch): {e}")
                        return None

                def get_positions_patch(symbol=None):
                    try:
                        # Importante: Usar USDT-FUTURES (mayúsculas) para Bitget V2
                        return self.exchange.fetch_positions(symbol, params={'productType': 'USDT-FUTURES'})
                    except Exception as e:
                        logger.error(f"Error en get_positions (patch): {e}")
                        return []

                def verificar_orden_activa_patch(order_id, symbol):
                    try:
                        if not order_id: return False
                        order = self.exchange.fetch_order(order_id, symbol)
                        return order['status'] == 'open'
                    except:
                        return False

                def place_tpsl_order_patch(**kwargs):
                    try:
                        # Mapeo de parámetros para Bitget V2 Plan Order
                        symbol = kwargs.get('symbol')
                        if 'USDT' in symbol and '/' not in symbol:
                            symbol = symbol.replace('USDT', '/USDT:USDT')
                            
                        # El side debe ser el opuesto a la posición
                        hold_side = kwargs.get('hold_side', '').lower()
                        side = 'sell' if hold_side == 'long' else 'buy'
                        
                        order_type = kwargs.get('order_type') # 'stop_loss' o 'take_profit'
                        trigger_price = kwargs.get('trigger_price')
                        
                        amount = kwargs.get('amount')
                        if amount is None:
                            # Intentar obtener el size de la posición abierta
                            positions = self.exchange.fetch_positions(symbol, params={'productType': 'USDT-FUTURES'})
                            for pos in positions:
                                if pos['symbol'] == symbol:
                                    amount = abs(float(pos['contracts']))
                                    break
                        
                        if not amount:
                            logger.error(f"Error: No se encontró monto para la orden {order_type} en {symbol}")
                            return None

                        params = {
                            'stopPrice': trigger_price,
                            'planType': 'normal_plan',
                        }
                        
                        if order_type == 'stop_loss':
                            params['triggerType'] = 'fill_price'
                        
                        # Usar create_order de CCXT para órdenes plan (tpsl)
                        return self.exchange.create_order(
                            symbol=symbol,
                            type='market' if order_type == 'stop_loss' else 'limit',
                            side=side,
                            amount=amount,
                            price=trigger_price if order_type == 'take_profit' else None,
                            params=params
                        )
                    except Exception as e:
                        logger.error(f"Error en place_tpsl_order (patch): {e}")
                        return None

                # Asignar los métodos parchados al objeto exchange
                self.exchange.get_klines = get_klines_patch
                self.exchange.get_positions = get_positions_patch
                self.exchange.fetch_positions_orig = self.exchange.fetch_positions # Guardar original
                self.exchange.fetch_positions = get_positions_patch # Reemplazar para consistencia
                self.exchange.verificar_orden_activa = verificar_orden_activa_patch
                self.exchange.place_tpsl_order = place_tpsl_order_patch
                # --- FIN MONKEY PATCHING ---

                # Check connection
                balance = self.exchange.fetch_balance()
                available = float(balance.get('USDT', {}).get('free', 0))
                logger.info("✓ Credenciales BITGET FUTUROS verificadas exitosamente")
                logger.info(f"✓ Balance disponible FUTUROS: {available:.2f} USDT")
            except Exception as e:
                logger.error("✗ No se pudo verificar credenciales BITGET FUTUROS")
                logger.error(f"Error verificando credenciales BITGET FUTUROS: {e}")
                self.exchange = None
        
        # LIMPIEZA INICIAL: Liberar símbolos bloquados si no hay posiciones activas
        self.limpiar_bloqueos_iniciales()

        #monedas - Generación dinámica de símbolos por volumen
        self.moned = config.get('symbols', ['BTCUSDT'])

        # Configuración de operaciones automáticas
        self.ejecutar_operaciones_automaticas = config.get('ejecutar_operaciones_automaticas', False)
        self.capital_por_operacion = config.get('capital_por_operacion', None)  # 3% del saldo (dinámico)
        self.leverage_por_defecto = config.get('leverage_por_defecto', 10)  # 10x apalancamiento
        
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


    def actualizar_moned(self):
        """Actualiza la lista de símbolos analizando las de mayor volumen, omitiendo estables e illiquidos"""
        if not self.exchange:
            logger.warning("⚠️ No se puede actualizar símbolos sin cliente exchange")
            return False
            
        try:
            logger.info(f"🔄 Obteniendo tickers del exchange para top {NUM_MONEDAS_ESCANEAR} monedas...")
            tickers = self.exchange.fetch_tickers()
            monedas_vivas = []
            
            for s, t in tickers.items():
                # Filtrar solo USDT swaps y remover ":" de ccxt symbols si es necesario
                # En ccxt para swaps a veces el símbolo es 'BTC/USDT:USDT'
                simbolo_limpio = s.replace('/', '').replace(':USDT', '')
                
                # Para la lógica usamos el ticker CCXT o el limpio? El bot original usaba 'BTCUSDT'
                # Guardaremos el limpio para compatibilidad con la lógica existente
                if ':USDT' in s or '/USDT' in s:
                    # Validaciones
                    if simbolo_limpio in SIMBOLOS_OMITIDOS:
                        continue
                        
                    # Filtrar por volumen quote (USDT)
                    vol = t.get('quoteVolume', 0)
                    if vol and vol > 0:
                        monedas_vivas.append({'s': simbolo_limpio, 'v': vol})
                        
            # Ordenar por volumen y tomar las N primeras
            monedas_vivas = sorted(monedas_vivas, key=lambda x: x['v'], reverse=True)[:NUM_MONEDAS_ESCANEAR]
            self.moned = [m['s'] for m in monedas_vivas]
            self.ultima_actualizacion_moned = datetime.now()
            
            logger.info(f"✅ Lista de monedas actualizada: {len(self.moned)} pares. Principal: {self.moned[0] if self.moned else 'Ninguno'}")
            return True
        except Exception as e:
            logger.error(f"❌ Error actualizando monedas dinámicas: {e}")
            return False

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
                'operaciones_cerradas_registradas': getattr(self, 'operaciones_cerradas_registradas', []),
                #monedas dinámicas
                'monedas_dinamicas': getattr(self, 'moned', []),
                'ultima_actualizacion_moned': getattr(self, 'ultima_actualizacion_moned', None).isoformat() if getattr(self, 'ultima_actualizacion_moned', None) else None
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


    def actualizar_moned(self):
        """Actualiza la lista de símbolos analizando las de mayor volumen, omitiendo estables e illiquidos"""
        if not self.exchange:
            logger.warning("⚠️ No se puede actualizar símbolos sin cliente exchange")
            return False
            
        try:
            logger.info(f"🔄 Obteniendo tickers del exchange para top {NUM_MONEDAS_ESCANEAR} monedas...")
            tickers = self.exchange.fetch_tickers()
            monedas_vivas = []
            
            for s, t in tickers.items():
                # Filtrar solo USDT swaps y remover ":" de ccxt symbols si es necesario
                # En ccxt para swaps a veces el símbolo es 'BTC/USDT:USDT'
                simbolo_limpio = s.replace('/', '').replace(':USDT', '')
                
                # Para la lógica usamos el ticker CCXT o el limpio? El bot original usaba 'BTCUSDT'
                # Guardaremos el limpio para compatibilidad con la lógica existente
                if ':USDT' in s or '/USDT' in s:
                    # Validaciones
                    if simbolo_limpio in SIMBOLOS_OMITIDOS:
                        continue
                        
                    # Filtrar por volumen quote (USDT)
                    vol = t.get('quoteVolume', 0)
                    if vol and vol > 0:
                        monedas_vivas.append({'s': simbolo_limpio, 'v': vol})
                        
            # Ordenar por volumen y tomar las N primeras
            monedas_vivas = sorted(monedas_vivas, key=lambda x: x['v'], reverse=True)[:NUM_MONEDAS_ESCANEAR]
            self.moned = [m['s'] for m in monedas_vivas]
            self.ultima_actualizacion_moned = datetime.now()
            
            logger.info(f"✅ Lista de monedas actualizada: {len(self.moned)} pares. Principal: {self.moned[0] if self.moned else 'Ninguno'}")
            return True
        except Exception as e:
            logger.error(f"❌ Error actualizando monedas dinámicas: {e}")
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
            self.operaciones_cerradas_registradas = operaciones_cerradas_lista
            
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
            
            # Cargar monedas dinámicas
            self.moned = estado.get('monedas_dinamicas', [])
            ultima_moned_str = estado.get('ultima_actualizacion_moned')
            if ultima_moned_str:
                try:
                    pass
                except Exception as e:
                    logger.warning(f"⚠️ Error cargando ultima_actualizacion_moned: {e}")
            
            logger.info(f"✅ Estado cargado exitosamente desde {self.estado_file}")
            logger.info(f"📊 Operaciones activas restauradas: {len(self.operaciones_activas)}")
            logger.info(f"📊 Operaciones Bitget activas restauradas: {len(self.operaciones_bitget_activas)}")
            if self.moned:
                logger.info(f"📊 Monedas dinámicas restauradas: {len(self.moned)}")
            
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
        self.operaciones_cerradas_registradas = []  # lista de simbolos ya procesados
        self.ultima_sincronizacion_bitget = None
        
        logger.info("✅ Persistencia avanzada inicializada")

    def limpiar_bloqueos_iniciales(self):
        """
        Limpia bloqueos al iniciar el bot.
        Si no hay posiciones activas en Bitget, libera todos los símbolos bloqueados
        para permitir nuevas operaciones.
        """
        if not self.exchange:
            return
        
        try:
            # Obtener posiciones activas en Bitget
            posiciones_bitget = self.exchange.fetch_positions(params={'productType': 'USDT-FUTURES'})
                
            if not posiciones_bitget:
                # No hay posiciones activas, liberar bloqueos
                simbolos_bloqueados = list(self.senales_enviadas)
                operaciones_bloqueadas = list(self.operaciones_activas.keys())
                
                # Liberar operaciones_activas
                if operaciones_bloqueadas:
                    logger.info(f"🧹 LIMPIEZA INICIAL: Liberando {len(operaciones_bloqueadas)} operaciones activas")
                    for simbolo in operaciones_bloqueadas:
                        self.operaciones_activas.pop(simbolo, None)
                        if simbolo in self.operaciones_bitget_activas:
                            self.operaciones_bitget_activas.pop(simbolo, None)
                        logger.info(f"   ✅ {simbolo} liberado de operaciones_activas")
                
                # Liberar senales_enviadas
                if simbolos_bloqueados:
                    logger.info(f"🧹 LIMPIEZA INICIAL: Liberando {len(simbolos_bloqueados)} símbolos bloquados")
                    for simbolo in simbolos_bloqueados:
                        self.senales_enviadas.discard(simbolo)
                        if simbolo in self.operaciones_cerradas_registradas:
                            self.operaciones_cerradas_registradas.remove(simbolo)
                        logger.info(f"   ✅ {simbolo} liberado")
                    
                    logger.info("🔄 El bot puede generar nuevas señales")
                else:
                    logger.info("✅ No hay símbolos bloquados al iniciar")
            else:
                # Hay posiciones activas, verificar cuáles operaciones locales aún existen
                simbolos_exchange = set()
                for pos in posiciones_bitget:
                    symbol = pos.get('symbol')
                    position_size = float(pos.get('positionSize', 0))
                    if position_size > 0 and symbol:
                        simbolos_exchange.add(symbol)
                
                logger.info(f"📊 Posiciones activas en Bitget: {list(simbolos_exchange)}")
                
        except Exception as e:
            logger.error(f"Error en limpieza inicial: {e}")

    def obtener_simbolos_analisis(self):
        if self.config.get('simbolos_dinamicos', False):
            # Verificar si necesitamos actualizar
            if not hasattr(self, 'ultima_actualizacion_moned') or (datetime.now() - self.ultima_actualizacion_moned).total_seconds() > 3600:
                self.actualizar_moned()
            return self.moned if self.moned else ['BTCUSDT']
        return self.config.get('symbols', ['BTCUSDT'])

    def liberar_simbolo(self, simbolo):
        """
        Liberar un símbolo que ya no tiene posición activa en Bitget.
        Esto permite que el bot pueda generar nuevas señales para este símbolo.
        
        Args:
            simbolo: Símbolo a liberar (ej: 'BTCUSDT')
        """
        try:
            logger.info(f"🆓 Liberando símbolo {simbolo}...")
            
            # Eliminar de operaciones activas
            if simbolo in self.operaciones_bitget_activas:
                del self.operaciones_bitget_activas[simbolo]
                logger.info(f"   ✅ Eliminado de operaciones_bitget_activas")
            
            # Eliminar de operaciones_activas (persistencia)
            if simbolo in self.operaciones_activas:
                del self.operaciones_activas[simbolo]
                logger.info(f"   ✅ Eliminado de operaciones_activas")
            
            # Eliminar IDs de órdenes SL/TP
            if simbolo in self.order_ids_sl:
                del self.order_ids_sl[simbolo]
                logger.info(f"   ✅ order_ids_sl liberado")
            
            if simbolo in self.order_ids_tp:
                del self.order_ids_tp[simbolo]
                logger.info(f"   ✅ order_ids_tp liberado")
            
            # Eliminar de senales_enviadas para permitir nuevas señales
            self.senales_enviadas.discard(simbolo)
            logger.info(f"   ✅ senales_enviadas liberado")
            
            # Guardar estado inmediatamente
            self.guardar_estado()
            logger.info(f"✅ Estado guardado después de liberar {simbolo}")
            
        except Exception as e:
            logger.error(f"❌ Error liberando símbolo {simbolo}: {e}")

    def sincronizar_con_bitget(self):
        """Sincronizar estado local con posiciones reales en Bitget - FUNCIÓN CRÍTICA"""
        if not self.exchange:
            logger.warning("⚠️ No hay cliente Bitget configurado, omitiendo sincronización")
            return
        
        try:
            logger.info("🔄 Iniciando sincronización con Bitget FUTUROS...")
            
            # OBTENER POSICIONES ACTIVAS EN BITGET
            posiciones_bitget = self.exchange.fetch_positions(params={'productType': 'USDT-FUTURES'})
            
            # DEBUG: Log de todas las posiciones encontradas
            if posiciones_bitget:
                logger.info(f"🔍 DEBUG: Raw positions data:")
                for i, posicion in enumerate(posiciones_bitget):
                    logger.info(f"   Posición {i+1}: {posicion}")
            
            # Extraer símbolos activos del exchange
            simbolos_exchange_activos = set()
            if posiciones_bitget:
                for posicion in posiciones_bitget:
                    symbol = posicion.get('symbol')
                    # IMPORTANTE: Bitget usa 'available' para el tamaño de la posición, NO 'positionSize'
                    # Si 'available' > 0, hay una posición activa
                    available_size = float(posicion.get('available', 0))
                    total_size = float(posicion.get('total', 0))
                    position_size = max(available_size, total_size)  # Usar el mayor de los dos
                    hold_side = posicion.get('holdSide', '')
                    logger.info(f"🔍 DEBUG: {symbol} - available={available_size}, total={total_size}, holdSide={hold_side}")
                    if position_size > 0 and symbol:
                        simbolos_exchange_activos.add(symbol)
            
            logger.info(f"📊 Símbolos activos en Bitget: {list(simbolos_exchange_activos)}")
            
            # LIBERAR SÍMBOLOS BLOQUEADOS EN operaciones_cerradas_registradas
            # Si un símbolo está en la lista de bloqueados pero ya NO existe en Bitget,
            # significa que fue cerrado manualmente y debemos liberarlo
            simbolos_liberados = []
            if hasattr(self, 'operaciones_cerradas_registradas') and self.operaciones_cerradas_registradas:
                for simbolo in list(self.operaciones_cerradas_registradas):
                    if simbolo not in simbolos_exchange_activos:
                        # El símbolo está bloqueado pero NO existe en Bitget → fue cerrado manualmente
                        self.operaciones_cerradas_registradas.remove(simbolo)
                        simbolos_liberados.append(simbolo)
                        logger.info(f"✅ {simbolo} LIBERADO automáticamente (cerrado manualmente)")
            
            if simbolos_liberados:
                logger.info(f"🔄 {len(simbolos_liberados)} símbolos liberados para nuevas operaciones")
            
            if not posiciones_bitget:
                logger.info("📊 No hay posiciones abiertas en Bitget")
                
                # Verificar si hay operaciones locales que fueron cerradas manualmente o nunca se ejecutaron
                operaciones_a_liberar = []
                operaciones_recientes = []  # Operaciones abiertas hace menos de 5 minutos
                intervalo_tolerancia_minutos = 5
                
                for simbolo, op_local in self.operaciones_activas.items():
                    # Verificar si la operación fue abierta recientemente
                    timestamp_entrada = op_local.get('timestamp_entrada', '')
                    if timestamp_entrada:
                        try:
                            tiempo_entrada = datetime.fromisoformat(timestamp_entrada)
                            minutos_desde_entrada = (datetime.now() - tiempo_entrada).total_seconds() / 60
                            
                            if minutos_desde_entrada < intervalo_tolerancia_minutos:
                                # La operación fue abierta hace menos de 5 minutos
                                # Bitget puede tener delay, no liberarla aún
                                operaciones_recientes.append((simbolo, minutos_desde_entrada))
                                logger.info(f"⏳ {simbolo}: operación reciente ({minutos_desde_entrada:.1f} min), esperando sincronización...")
                                continue
                        except Exception:
                            pass
                    
                    # Para operaciones viejas o sin timestamp, verificar si fue ejecutada
                    if op_local.get('operacion_ejecutada', False):
                        operaciones_a_liberar.append(simbolo)
                    else:
                        # Operaciones que nunca se ejecutaron en Bitget, liberar
                        operaciones_a_liberar.append(simbolo)
                
                # Reportar operaciones recientes
                if operaciones_recientes:
                    logger.info(f"⏳ {len(operaciones_recientes)} operaciones recientes esperando sincronización:")
                    for simbolo, minutos in operaciones_recientes:
                        logger.info(f"   • {simbolo}: {minutos:.1f} minutos desde apertura")
                    logger.info(f"   💡 Esperando {intervalo_tolerancia_minutos - max(m[1] for m in operaciones_recientes):.1f} minutos más para sincronización...")
                
                # Liberar solo operaciones viejas
                if operaciones_a_liberar:
                    logger.info(f"🔄 Liberando {len(operaciones_a_liberar)} operaciones antiguas sin posiciones en Bitget:")
                    for simbolo in operaciones_a_liberar:
                        if simbolo in self.operaciones_activas:
                            self.operaciones_activas.pop(simbolo, None)
                        if simbolo in self.operaciones_bitget_activas:
                            self.operaciones_bitget_activas.pop(simbolo, None)
                        
                        # También liberar de senales_enviadas para permitir nuevo escaneo
                        if simbolo in self.senales_enviadas:
                            self.senales_enviadas.remove(simbolo)
                        
                        logger.info(f"   ✅ {simbolo} liberada del tracking (sin posición en Bitget)")
                    
                    logger.info(f"🔄 El bot volverá a escanear oportunidades para estos símbolos")
                else:
                    if operaciones_recientes:
                        logger.info("✅ Solo hay operaciones recientes, esperando sincronización")
                    else:
                        logger.info("✅ No hay operaciones locales pendientes de sincronización")
                
                return
            
            # Procesar posiciones encontradas en Bitget
            posiciones_activas = {}
            for posicion in posiciones_bitget:
                symbol = posicion.get('symbol')
                # IMPORTANTE: Usar 'available' o 'total' para el tamaño de la posición
                available_size = float(posicion.get('available', 0))
                total_size = float(posicion.get('total', 0))
                position_size = max(available_size, total_size)
                hold_side = posicion.get('holdSide', '')
                
                if position_size > 0 and symbol and hold_side:
                    # Mapear campos de Bitget a nombres internos
                    # 'openPriceAvg' -> 'averageOpenPrice'
                    # 'unrealizedPL' -> 'unrealizedAmount'
                    average_price = float(posicion.get('openPriceAvg', 0))
                    unrealized_pnl = float(posicion.get('unrealizedPL', 0))
                    # Calcular position_usdt desde el precio promedio y el tamaño
                    position_usdt = average_price * position_size if average_price > 0 else 0
                    
                    posiciones_activas[symbol] = {
                        'position_size': position_size,
                        'hold_side': hold_side,
                        'average_price': average_price,
                        'unrealized_pnl': unrealized_pnl,
                        'position_usdt': position_usdt
                    }
            
            logger.info(f"📊 Posiciones activas en Bitget: {list(posiciones_activas.keys())}")
            
            # Verificar operaciones locales vs exchange
            operaciones_cerradas_manual = []
            operaciones_pendientes_sincronizacion = []  # Operaciones recientes esperando sincronización
            intervalo_tolerancia_minutos = 5
            
            for simbolo, op_local in list(self.operaciones_activas.items()):
                # Solo procesar operaciones que fueron ejecutadas en Bitget
                if not op_local.get('operacion_ejecutada', False):
                    continue  # Saltar operaciones que no se ejecutaron en Bitget
                
                if simbolo not in posiciones_activas:
                    # La operación local no existe en exchange - verificar si es reciente
                    timestamp_entrada = op_local.get('timestamp_entrada', '')
                    if timestamp_entrada:
                        try:
                            tiempo_entrada = datetime.fromisoformat(timestamp_entrada)
                            minutos_desde_entrada = (datetime.now() - tiempo_entrada).total_seconds() / 60
                            
                            if minutos_desde_entrada < intervalo_tolerancia_minutos:
                                # La operación fue abierta hace menos de 5 minutos
                                # Bitget puede tener delay, no marcarla como cerrada
                                operaciones_pendientes_sincronizacion.append((simbolo, minutos_desde_entrada))
                                logger.info(f"⏳ {simbolo}: operación reciente ({minutos_desde_entrada:.1f} min), omitiendo verificación...")
                                continue
                        except Exception:
                            pass
                    
                    # Operación vieja o sin timestamp, considerar como cerrada manualmente
                    logger.warning(f"⚠️ Operación local {simbolo} no encontrada en Bitget (cerrada manualmente)")
                    
                    # Marcar para eliminación del tracking (no procesar como cierre normal)
                    operaciones_cerradas_manual.append(simbolo)
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
            
            # Reportar operaciones pendientes de sincronización
            if operaciones_pendientes_sincronizacion:
                logger.info(f"⏳ {len(operaciones_pendientes_sincronizacion)} operaciones recientes pendientes de sincronización:")
                for simbolo, minutos in operaciones_pendientes_sincronizacion:
                    logger.info(f"   • {simbolo}: {minutos:.1f} minutos desde apertura")
                logger.info(f"   💡 Estas operaciones serán verificadas en el próximo ciclo de sincronización")
            
            # Eliminar operaciones que fueron cerradas manualmente del tracking
            for simbolo in operaciones_cerradas_manual:
                op_local = self.operaciones_activas.pop(simbolo, None)
                if simbolo in self.operaciones_bitget_activas:
                    self.operaciones_bitget_activas.pop(simbolo, None)
                
                # LIBERAR el símbolo de operaciones_cerradas_registradas si está bloqueado
                if hasattr(self, 'operaciones_cerradas_registradas') and simbolo in self.operaciones_cerradas_registradas:
                    self.operaciones_cerradas_registradas.remove(simbolo)
                    logger.info(f"🔓 {simbolo} liberado de operaciones_cerradas_registradas")
                
                logger.info(f"✅ {simbolo} eliminada del tracking (cerrada manualmente)")
                logger.info(f"🔄 El bot volverá a escanear oportunidades para {simbolo}")
            
            # Liberar operaciones locales que NO fueron ejecutadas en Bitget (operacion_ejecutada=False)
            # Estas operaciones nunca se abrieron realmente, así que deben liberarse
            operaciones_no_ejecutadas = []
            for simbolo, op_local in list(self.operaciones_activas.items()):
                if not op_local.get('operacion_ejecutada', False):
                    # Esta operación nunca se ejecutó en Bitget, liberar
                    operaciones_no_ejecutadas.append(simbolo)
            
            if operaciones_no_ejecutadas:
                logger.info(f"🔄Liberando {len(operaciones_no_ejecutadas)} operaciones que nunca se ejecutaron en Bitget:")
                for simbolo in operaciones_no_ejecutadas:
                    self.operaciones_activas.pop(simbolo, None)
                    if simbolo in self.senales_enviadas:
                        self.senales_enviadas.remove(simbolo)
                    if simbolo in self.operaciones_bitget_activas:
                        self.operaciones_bitget_activas.pop(simbolo, None)
                    logger.info(f"   ✅ {simbolo} liberado (nunca se ejecutó en Bitget)")
            
            # Verificar si hay nuevas operaciones en Bitget que no están en nuestro tracking
            for simbolo, pos_data in posiciones_activas.items():
                if simbolo in self.operaciones_activas:
                    # La operación ya existe en nuestro estado
                    op_existente = self.operaciones_activas[simbolo]
                    
                    # Detectar si es operación automática:
                    # 1. Si tiene explícitamente operacion_manual_usuario = False
                    # 2. O si tiene operacion_ejecutada = True (asumimos automática si ya estaba ejecutada al cargar estado)
                    tiene_flag_automatica = 'operacion_manual_usuario' in op_existente
                    es_explicitamente_automatica = op_existente.get('operacion_manual_usuario') is False
                    fue_ejecutada = op_existente.get('operacion_ejecutada', False)
                    tiene_order_id = op_existente.get('order_id_entrada') is not None
                    
                    es_operacion_automatica = es_explicitamente_automatica or (fue_ejecutada and tiene_order_id)
                    
                    if es_operacion_automatica:
                        # Operación automática restaurada desde el estado
                        logger.info(f"🤖 OPERACIÓN AUTOMÁTICA RESTAURADA: {simbolo}")
                        if tiene_flag_automatica:
                            logger.info(f"   📊 Flag automática detectada")
                        else:
                            logger.info(f"   📊 Detected_from_state (compatibilidad): operacion_ejecutada=True, order_id={op_existente.get('order_id_entrada', 'N/A')}")
                        
                        # Actualizar operación existente con datos frescos del exchange
                        tipo_operacion = op_existente.get('tipo', 'LONG' if pos_data['hold_side'] == 'long' else 'SHORT')
                        self.operaciones_activas[simbolo].update({
                            'precio_entrada_real': pos_data['average_price'],
                            'pnl_no_realizado': pos_data['unrealized_pnl'],
                            'size_real': pos_data['position_size'],
                            'valor_nocional': pos_data['position_usdt'],
                            'ultima_sincronizacion': datetime.now().isoformat(),
                            # Asegurar flag para futuras sincronizaciones
                            'operacion_manual_usuario': False
                        })
                        
                        # Mantener en seguimiento de Bitget
                        self.operaciones_bitget_activas[simbolo] = self.operaciones_activas[simbolo].copy()
                    else:
                        # Operación manual existente, actualizar datos
                        logger.info(f"👤 Operación manual existente actualizada: {simbolo}")
                        self.operaciones_activas[simbolo].update({
                            'precio_entrada_real': pos_data['average_price'],
                            'pnl_no_realizado': pos_data['unrealized_pnl'],
                            'size_real': pos_data['position_size'],
                            'valor_nocional': pos_data['position_usdt'],
                            'ultima_sincronizacion': datetime.now().isoformat()
                        })
                        self.operaciones_bitget_activas[simbolo] = self.operaciones_activas[simbolo].copy()
                else:
                    # Nueva operación detectada - es manual del usuario
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
                        'operacion_manual_usuario': True,  # Marca explícita de operación manual
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
            
            # GUARDAR ESTADO después de sincronización
            self.guardar_estado()
            logger.info("💾 Estado guardado después de sincronización")
            
        except Exception as e:
            logger.error(f"❌ Error en sincronización con Bitget: {e}")

    def verificar_y_recolocar_tp_sl(self):
        """Verificar y recolocar automáticamente TP y SL si es necesario - SOLO PARA OPERACIONES AUTOMÁTICAS"""
        if not self.exchange:
            return
        
        try:
            logger.info("🔍 Verificando estado de órdenes TP/SL...")
            
            for simbolo, operacion in list(self.operaciones_bitget_activas.items()):
                try:
                    # Verificar si es una operación MANUAL del usuario
                    es_operacion_manual = operacion.get('operacion_manual_usuario', False)
                    
                    # PARA OPERACIONES MANUALES: Solo monitorear, NO recolocar SL/TP
                    if es_operacion_manual:
                        logger.info(f"👤 {simbolo}: Operación MANUAL detectada - Solo monitoreando, sin recolocación de SL/TP")
                        
                        # Verificar si la posición aún existe en Bitget
                        posiciones = self.exchange.get_positions(simbolo)
                        if not posiciones or len(posiciones) == 0:
                            # La operación manual fue cerrada - LIBERAR EL SÍMBOLO
                            logger.info(f"🆓 {simbolo}: Operación manual cerrada por usuario - Liberando símbolo para nuevos escaneos")
                            self.liberar_simbolo(simbolo)
                            del self.operaciones_bitget_activas[simbolo]
                            if simbolo in self.order_ids_sl:
                                del self.order_ids_sl[simbolo]
                            if simbolo in self.order_ids_tp:
                                del self.order_ids_tp[simbolo]
                            self.guardar_estado()
                        continue
                    
                    # PARA OPERACIONES AUTOMÁTICAS: Proceder con recolocación de SL/TL
                    # Verificar si las órdenes plan están activas consultando Bitget
                    orden_sl_id = self.order_ids_sl.get(simbolo)
                    orden_tp_id = self.order_ids_tp.get(simbolo)
                    
                    # Verificación REAL del estado de las órdenes en Bitget
                    sl_activa = self.exchange.verificar_orden_activa(orden_sl_id, simbolo) if orden_sl_id else False
                    tp_activa = self.exchange.verificar_orden_activa(orden_tp_id, simbolo) if orden_tp_id else False
                    
                    # Solo recolocar si las órdenes realmente no están activas
                    if not sl_activa or not tp_activa:
                        # Determinar qué órdenes necesitan recolocación
                        sl_necesita = not sl_activa
                        tp_necesita = not tp_activa
                        
                        if sl_necesita or tp_necesita:
                            logger.info(f"ℹ️ Órdenes TP/SL para {simbolo}: SL={'OK' if sl_activa else 'FALTA'}, TP={'OK' if tp_activa else 'FALTA'}")
                        
                        # Obtener precio actual
                        klines = self.exchange.get_klines(simbolo, '15m', 1)
                        if not klines:
                            continue
                        
                        klines.reverse()
                        precio_actual = float(klines[0][4])
                        
                        # USAR LOS NIVELES ORIGINALES DE SL/TP (no recalcular desde precio actual)
                        stop_loss = operacion.get('stop_loss')
                        take_profit = operacion.get('take_profit')
                        
                        # Si por alguna razón no hay niveles guardados, usar porcentajes por defecto
                        if not stop_loss or not take_profit:
                            logger.warning(f"⚠️ No se encontraron niveles SL/TP originales para {simbolo}, recalculando...")
                            tipo = operacion['tipo']
                            sl_porcentaje = 0.02
                            tp_porcentaje = 0.10

                            if tipo == "LONG":
                                stop_loss = precio_actual * (1 - sl_porcentaje)
                                take_profit = precio_actual * (1 + tp_porcentaje)
                            else:
                                stop_loss = precio_actual * (1 + sl_porcentaje)
                                take_profit = precio_actual * (1 - tp_porcentaje)
                        
                        logger.info(f"ℹ️ Usando niveles originales para {simbolo}: SL={stop_loss}, TP={take_profit}")
                        
                        hold_side = 'long' if operacion['tipo'] == 'LONG' else 'short'

                        # Recolocar SL solo si no está activa
                        if sl_necesita:
                            logger.info(f"🔧 Recolocando STOP LOSS para {simbolo}: {stop_loss}")
                            orden_sl_nueva = self.exchange.place_tpsl_order(
                                symbol=simbolo,
                                hold_side=hold_side,
                                trigger_price=stop_loss,
                                order_type='stop_loss',
                                stop_loss_price=stop_loss,
                                take_profit_price=None,
                                trade_direction=operacion['tipo']
                            )
                            if orden_sl_nueva:
                                self.order_ids_sl[simbolo] = orden_sl_nueva.get('orderId')
                                logger.info(f"✅ SL recolocada para {simbolo}")
                            else:
                                logger.warning(f"⚠️ No se pudo recolocar SL para {simbolo}")

                        # Recolocar TP solo si no está activa
                        if tp_necesita:
                            logger.info(f"🔧 Recolocando TAKE PROFIT para {simbolo}: {take_profit}")
                            orden_tp_nueva = self.exchange.place_tpsl_order(
                                symbol=simbolo,
                                hold_side=hold_side,
                                trigger_price=take_profit,
                                order_type='take_profit',
                                stop_loss_price=None,
                                take_profit_price=take_profit,
                                trade_direction=operacion['tipo']
                            )
                            if orden_tp_nueva:
                                self.order_ids_tp[simbolo] = orden_tp_nueva.get('orderId')
                                logger.info(f"✅ TP recolocada para {simbolo}")
                            else:
                                logger.warning(f"⚠️ No se pudo recolocar TP para {simbolo}")
                    else:
                        logger.info(f"✅ Órdenes TP/SL activas para {simbolo}")
                
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
            if precio_salida is None and self.exchange:
                klines = self.exchange.get_klines(simbolo, '15m', 1)
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
            
            # Marcar como procesada para evitar duplicados
            self.operaciones_cerradas_registradas.append(simbolo)
            
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
        timeframes = self.config.get('timeframes', ['15m', '30m', '1h', '4h'])
        velas_options = self.config.get('velas_options', [80, 100, 120, 150, 200])
        mejor_config = None
        mejor_puntaje = -999999
        prioridad_timeframe = {'15m': 4, '30m': 3, '1h': 2, '4h': 1}
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
                            puntaje_timeframe = prioridad_timeframe.get(timeframe,0) * 100
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
        # Segundo bucle sin filtro de ancho de canal
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
                            puntaje_timeframe = prioridad_timeframe.get(timeframe, 0) * 100
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
        if self.exchange:
            try:
                candles = self.exchange.get_klines(simbolo, timeframe, num_velas + 14)
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

        # Crear un DataFrame de pandas para poder usar la función calcular_adx_di
        df_indicadores = pd.DataFrame({
            'High': datos_mercado['maximos'][-candle_period:],
            'Low': datos_mercado['minimos'][-candle_period:],
            'Close': datos_mercado['cierres'][-candle_period:]
        })
        
        # Calcular ADX, DI+ y DI-
        resultado_adx = calcular_adx_di(df_indicadores['High'], df_indicadores['Low'], df_indicadores['Close'], length=14)
        
        # Obtener los valores más recientes
        di_plus = resultado_adx['di_plus'][-1]
        di_minus = resultado_adx['di_minus'][-1]
        adx = resultado_adx['adx'][-1]
        
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
            'di_plus': di_plus,
            'di_minus': di_minus,
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
            expectativa = "posible entrada en LONG"
        else:  # BREAKOUT_SHORT
            emoji_principal = "📉"
            tipo_texto = "RUPTURA BAJISTA de RESISTENCIA"
            nivel_roto = f"Resistencia: {resistencia:.8f}"
            direccion_emoji = "⬆️"
            contexto = f"Canal {direccion_canal} → Rechazo desde RESISTENCIA"
            expectativa = "posible entrada en SHORT"
        # Mensaje de alerta
        mensaje = f"""
{emoji_principal} <b>¡BREAKOUT DETECTADO! - {simbolo}</b>
⚠️ <b>{tipo_texto}</b> {direccion_emoji}
⏰ <b>Hora:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
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
            if self.exchange:
                klines = self.exchange.get_klines(simbolo, config_optima['timeframe'], config_optima['num_velas'])
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
                    stoch_k_values.append(np.nan)
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
            # ... (tu código existente para calcular canal y Stochastic) ...
            df['Stoch_K'] = k_smoothed
            df['Stoch_D'] = stoch_d_values

            # Calcular ADX, DI+ y DI-
            resultado_adx = calcular_adx_di(df['High'], df['Low'], df['Close'], length=14)
            df['DI+'] = resultado_adx['di_plus']
            df['DI-'] = resultado_adx['di_minus']
            df['ADX'] = resultado_adx['adx']

            
            # =====================================================
            # NUEVO: Calcular ADX, DI+ y DI- usando la función importada
            # =====================================================
            adx_results = calcular_adx_di(
                df['High'].values, 
                df['Low'].values, 
                df['Close'].values, 
                length=14
            )
            df['ADX'] = adx_results['adx']
            df['DI+'] = adx_results['di_plus']
            df['DI-'] = adx_results['di_minus']
            
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
                titulo_extra = "RUPTURA ALCISTA"
            else:
                color_breakout = '#D68F01'
                titulo_extra = "RUPTURA BAJISTA"
            apds.append(mpf.make_addplot(breakout_line, color=color_breakout, linestyle='-', width=3, panel=0, alpha=0.8))
            # Stochastic
            apds.append(mpf.make_addplot(df['Stoch_K'], color='#00BFFF', width=1.5, panel=1, ylabel='Stochastic'))
            apds.append(mpf.make_addplot(df['Stoch_D'], color='#FF6347', width=1.5, panel=1))
            overbought = [20] * len(df)
            oversold = [80] * len(df)
            apds.append(mpf.make_addplot(overbought, color="#E7E4E4", linestyle='--', width=0.8, panel=1, alpha=0.5))
            apds.append(mpf.make_addplot(oversold, color="#E9E4E4", linestyle='--', width=0.8, panel=1, alpha=0.5))
            
            # =====================================================
            # NUEVO: Añadir panel de ADX, DI+ y DI- (Panel 2)
            # =====================================================
            apds.append(mpf.make_addplot(df['DI+'], color='#00FF00', width=1.5, panel=2, ylabel='ADX/DI'))
            apds.append(mpf.make_addplot(df['DI-'], color='#FF0000', width=1.5, panel=2))
            apds.append(mpf.make_addplot(df['ADX'], color='#000080', width=2, panel=2))  # Navy color
            # Línea threshold en ADX
            adx_threshold = [20] * len(df)
            apds.append(mpf.make_addplot(adx_threshold, color="#808080", linestyle='--', width=0.8, panel=2, alpha=0.5))
            
            # Crear gráfico
            fig, axes = mpf.plot(df, type='candle', style='nightclouds',
                               title=f'{simbolo} | {titulo_extra} | {config_optima["timeframe"]} | [ESPERANDO REENTRY]',
                               ylabel='Precio',
                               addplot=apds,
                               volume=False,
                               returnfig=True,
                               figsize=(14, 12),
                               panel_ratios=(3, 1, 1))
            axes[2].set_ylim([0, 100])
            axes[2].grid(True, alpha=0.3)
            # Configurar panel ADX (axes[3])
            if len(axes) > 3:
                axes[3].set_ylim([0, 100])
                axes[3].grid(True, alpha=0.3)
                axes[3].set_ylabel('ADX/DI', fontsize=8)
            
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
        if direccion == "ALCISTA" and nivel_fuerza >= 2:
            if precio_cierre < soporte:  # Precio rompió hacia abajo el soporte
                print(f"     🚀 {simbolo} - BREAKOUT LONG: {precio_cierre:.8f} < Soporte: {soporte:.8f}")
                return "BREAKOUT_LONG"
        elif direccion == "BAJISTA" and nivel_fuerza >= 2:
            if precio_cierre > resistencia:  # Precio rompió hacia arriba la resistencia
                print(f"     📉 {simbolo} - BREAKOUT SHORT: {precio_cierre:.8f} > Resistencia: {resistencia:.8f}")
                return "BREAKOUT_SHORT"
        return None

    def detectar_reentry(self, simbolo, info_canal, datos_mercado):
        """
        Detecta si el precio ha REINGRESADO al canal después de un BREAKOUT.
        SOLO genera señales cuando:
        1. Ya hubo un breakout detectado (precio salió del canal)
        2. El precio ha reingresado al canal
        3. Se cumplen las REGLAS DE ORO:
           - LONG: di_minus < di_plus AND stoch_k > stoch_d
           - SHORT: di_minus > di_plus AND stoch_k < stoch_d
        
        La verificación de DI es OBLIGATORIA y debe cumplirse SIEMPRE.
        """
        # Verificar que ya hubo un breakout detectado
        if simbolo not in self.esperando_reentry:
            return None
        
        breakout_info = self.esperando_reentry[simbolo]
        tipo_breakout = breakout_info['tipo']
        timestamp_breakout = breakout_info['timestamp']
        tiempo_desde_breakout = (datetime.now() - timestamp_breakout).total_seconds() / 60
        
        # Timeout de reentry (120 minutos)
        if tiempo_desde_breakout > 120:
            print(f"     ⏰ {simbolo} - Timeout de reentry ({tiempo_desde_breakout:.1f} min), cancelando espera")
            del self.esperando_reentry[simbolo]
            if simbolo in self.breakouts_detectados:
                del self.breakouts_detectados[simbolo]
            return None
        
        precio_actual = datos_mercado['precio_actual']
        resistencia = info_canal['resistencia']
        soporte = info_canal['soporte']
        
        # Verificar si el precio ha reingresado al canal
        if not (soporte <= precio_actual <= resistencia):
            return None
        
        # Extraer valores de Stochastic y DI para confirmación
        stoch_k = info_canal['stoch_k']
        stoch_d = info_canal['stoch_d']
        di_plus = info_canal.get('di_plus', 25)
        di_minus = info_canal.get('di_minus', 25)
        angulo = info_canal['angulo_tendencia']
        
        # Verificar condiciones de Stochastic
        es_long_stoch = stoch_k > stoch_d
        es_short_stoch = stoch_k < stoch_d
        
        # Verificar condiciones de DI (OBLIGATORIO PARA REGLAS DE ORO)
        condicion_di_long = di_plus > di_minus
        condicion_di_short = di_minus > di_plus
        
        # Determinar dirección válida según ángulo Y DI
        es_long_tendencia = angulo > 0 and condicion_di_long
        es_short_tendencia = angulo < 0 and condicion_di_short
        
        # Logs de debug para verificar cumplimiento de reglas de oro
        print(f"     🔍 {simbolo} - REENTRY: Precio={precio_actual:.2f} en canal [{soporte:.2f}-{resistencia:.2f}]")
        print(f"     🔍 {simbolo} - DI+: {di_plus:.2f}, DI-: {di_minus:.2f}")
        print(f"     🔍 {simbolo} - Stochastic: K={stoch_k:.2f}, D={stoch_d:.2f}")
        print(f"     🔍 {simbolo} - Ángulo: {angulo:.2f}° | Breakout: {tipo_breakout} | Tiempo: {tiempo_desde_breakout:.1f}min")
        
        # REGLAS DE ORO: Ambas condiciones DI Y Stochastic DEBEN cumplirse
        if tipo_breakout == "BREAKOUT_LONG":
            # Para LONG: reingreso cerca del soporte + DI- < DI+ + Stoch K > D
            tolerancia = 0.001 * precio_actual
            distancia_soporte = abs(precio_actual - soporte)
            
            if distancia_soporte <= tolerancia:
                # Confirmar con REGLAS DE ORO
                if es_long_stoch and es_long_tendencia:
                    print(f"     ✅ {simbolo} - REENTRY LONG CONFIRMADO!")
                    print(f"         📊 Soporte + DI+>{di_plus:.2f}>{di_minus:.2f}=DI- + Stoch K>{stoch_k:.2f}>{stoch_d:.2f}=D")
                    if simbolo in self.breakouts_detectados:
                        del self.breakouts_detectados[simbolo]
                    return "LONG"
                else:
                    if not es_long_stoch:
                        print(f"     ❌ {simbolo} - REENTRY LONG RECHAZADO: Stoch K NO > D ({stoch_k:.2f} <= {stoch_d:.2f})")
                    if not es_long_tendencia:
                        if not condicion_di_long:
                            print(f"     ❌ {simbolo} - REENTRY LONG RECHAZADO: DI+ NO > DI- ({di_plus:.2f} <= {di_minus:.2f}) - VIOLA REGLAS DE ORO")
                        if angulo <= 0:
                            print(f"     ❌ {simbolo} - REENTRY LONG RECHAZADO: Ángulo NO positivo ({angulo:.2f}°)")
        
        elif tipo_breakout == "BREAKOUT_SHORT":
            # Para SHORT: reingreso cerca de la resistencia + DI- > DI+ + Stoch K < D
            tolerancia = 0.001 * precio_actual
            distancia_resistencia = abs(precio_actual - resistencia)
            
            if distancia_resistencia <= tolerancia:
                # Confirmar con REGLAS DE ORO
                if es_short_stoch and es_short_tendencia:
                    print(f"     ✅ {simbolo} - REENTRY SHORT CONFIRMADO!")
                    print(f"         📊 Resistencia + DI->{di_minus:.2f}>{di_plus:.2f}=DI+ + Stoch K<{stoch_k:.2f}<{stoch_d:.2f}=D")
                    if simbolo in self.breakouts_detectados:
                        del self.breakouts_detectados[simbolo]
                    return "SHORT"
                else:
                    if not es_short_stoch:
                        print(f"     ❌ {simbolo} - REENTRY SHORT RECHAZADO: Stoch K NO < D ({stoch_k:.2f} >= {stoch_d:.2f})")
                    if not es_short_tendencia:
                        if not condicion_di_short:
                            print(f"     ❌ {simbolo} - REENTRY SHORT RECHAZADO: DI- NO > DI+ ({di_minus:.2f} <= {di_plus:.2f}) - VIOLA REGLAS DE ORO")
                        if angulo >= 0:
                            print(f"     ❌ {simbolo} - REENTRY SHORT RECHAZADO: Ángulo NO negativo ({angulo:.2f}°)")
        
        return None

    def calcular_niveles_entrada(self, tipo_operacion, info_canal, precio_actual):
        """Calcula niveles de entrada, SL y TP.
        
        El TP se coloca en el ANCHO COMPLETO DEL CANAL (lado opuesto):
        - LONG: TP en la resistencia (límite superior del canal)
        - SHORT: TP en el soporte (límite inferior del canal)
        """
        if not info_canal:
            return None, None, None
        resistencia = info_canal['resistencia']
        soporte = info_canal['soporte']
        ancho_canal = resistencia - soporte
        sl_porcentaje = 0.02
        
        if tipo_operacion == "LONG":
            precio_entrada = precio_actual
            stop_loss = precio_entrada * (1 - sl_porcentaje)
            # TP en la resistencia (ancho completo del canal desde el soporte)
            take_profit = resistencia
        else:
            precio_entrada = precio_actual
            stop_loss = resistencia * (1 + sl_porcentaje)
            # TP en el soporte (ancho completo del canal desde la resistencia)
            take_profit = soporte
        
        riesgo = abs(precio_entrada - stop_loss)
        beneficio = abs(take_profit - precio_entrada)
        ratio_rr = beneficio / riesgo if riesgo > 0 else 0
        
        # Solo ajustar si el ratio es muy bajo (protección adicional)
        if ratio_rr < 0.5:
            if tipo_operacion == "LONG":
                take_profit = precio_entrada + (riesgo * self.config['min_rr_ratio'])
            else:
                take_profit = precio_entrada - (riesgo * self.config['min_rr_ratio'])
        
        return precio_entrada, take_profit, stop_loss

    def escanear_mercado(self):
        """Escanea el mercado con estrategia Breakout + Reentry"""
        # Usar símbolos dinámicos o fallback a configuración
        simbolos_a_analizar = self.obtener_simbolos_analisis()
        print(f"\n🔍 Escaneando {len(simbolos_a_analizar)} símbolos (Estrategia: Breakout + Reentry)...")
        senales_encontradas = 0
        for simbolo in simbolos_a_analizar:
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
                                    # ... (código anterior) ...
                info_canal = self.calcular_canal_regresion_config(datos_mercado, config_optima['num_velas'])
                if not info_canal:
                    print(f"   ❌ {simbolo} - Error calculando canal")
                    continue

                # La función calcular_canal_regresion_config ya debería haberlos calculado
                # pero los añadimos aquí como una capa de seguridad
                if 'di_plus' not in info_canal or 'di_minus' not in info_canal:
                    
                    df = datos_mercado.get('df')
                    if df is not None and not df.empty:
                        # Calcular ADX, DI+ y DI-
                        resultado_adx = calcular_adx_di(df['High'], df['Low'], df['Close'], length=14)
                        
                        # Añadir los valores más recientes al diccionario info_canal
                        info_canal['di_plus'] = resultado_adx['di_plus'][-1]
                        info_canal['di_minus'] = resultado_adx['di_minus'][-1]
                        info_canal['adx'] = resultado_adx['adx'][-1]
                    else:
                        # Valores por defecto si no hay DataFrame
                        info_canal['di_plus'] = 0
                        info_canal['di_minus'] = 0
                        info_canal['adx'] = 0
        
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
                
                # CORRECCIÓN: Verificar cooldown más permisivo
                # Solo bloquear si hay una operación activa para este símbolo
                # NO bloquear solo por breakout_history (eso bloqueaba reentries válidos)
                if simbolo in self.operaciones_activas:
                    print(f"   ⏳ {simbolo} - Operación activa existente, omitiendo...")
                    continue
                
                breakout_info = self.esperando_reentry[simbolo]
                self.generar_senal_operacion(
                    simbolo, tipo_operacion, precio_entrada, tp, sl, 
                    info_canal, datos_mercado, config_optima, breakout_info
                )
                senales_encontradas += 1
                # Actualizar breakout_history SOLO cuando se genera una señal exitosa
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
            print(f"    ⏳ {simbolo} - Señal ya procesada anteriormente, omitiendo...")
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
📈 <b>Tendencia:</b> {info_canal['direccion']}
💪 <b>Fuerza:</b> {info_canal['fuerza_texto']}
📊 <b>Stoch K:</b> {info_canal['stoch_k']:.1f}
📈 <b>Stoch D:</b> {info_canal['stoch_d']:.1f}
📊 <b>D +:</b> {info_canal['di_plus']:.1f}
📈 <b>D -:</b> {info_canal['di_minus']:.1f}
⏰ <b>Hora:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
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
        if self.ejecutar_operaciones_automaticas and self.exchange:
            print(f"     🤖 Ejecutando operación automática en BITGET FUTUROS...")
            try:
                operacion_bitget = ejecutar_operacion_bitget(
                    exchange=self.exchange,
                    simbolo=simbolo,
                    tipo_operacion=tipo_operacion,
                    capital_usd=None,  # SIEMPRE calcular como 3% del saldo dinámicamente
                    leverage=None  # Usar apalancamiento máximo permitido por Bitget para este símbolo
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
                        'di_plus': info_canal['di_plus'],
                        'di_minus': info_canal['di_minus'],
                        'breakout_usado': breakout_info is not None,
                        'operacion_ejecutada': True,  # Confirma ejecución exitosa
                        'operacion_manual_usuario': False,  # MARCA EXPLÍCITA: Operación automática
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
                'di_plus': info_canal['di_plus'],
                'di_minus': info_canal['di_minus'],
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
                    'stoch_k', 'stoch_d','di_plus','di_minus', 'breakout_usado', 'operacion_ejecutada'
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
                datos_operacion.get('di_plus', 0),
                datos_operacion.get('di_minus', 0),
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
            # Saltar operaciones que no tienen TP/SL (operaciones manuales abiertas sin SL/TP)
            if 'take_profit' not in operacion or 'stop_loss' not in operacion:
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
                    'di_plus': operacion.get('di_plus', 0),
                    'di_minus': operacion.get('di_minus', 0),
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
        emoji = "OK" if datos_operacion['resultado'] == "TP" else "SL"
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

    def limpiar_breakouts_expirados(self, max_wait_minutes=120):
 
        if not self.esperando_reentry:
            return 0
    
        eliminados = 0
        simbolos_a_eliminar = []
    
        for simbolo, info in self.esperando_reentry.items():
            tiempo_espera = (datetime.now() - info['timestamp']).total_seconds() / 60
            if tiempo_espera > max_wait_minutes:
                simbolos_a_eliminar.append(simbolo)
                eliminados += 1
                print(f"   ⏰ {simbolo} - Expiró tiempo de espera ({tiempo_espera:.1f} min > {max_wait_minutes} min), eliminando...")
    
        # Eliminar los breakout expirados
        for simbolo in simbolos_a_eliminar:
            if simbolo in self.esperando_reentry:
                del self.esperando_reentry[simbolo]
            # También eliminar de breakouts_detectados si existe
            if simbolo in self.breakouts_detectados:
                del self.breakouts_detectados[simbolo]
    
        if eliminados > 0:
           print(f"   🗑️ Total de breakout expirados eliminados: {eliminados}")
           # Guardar estado después de la limpieza
           self.guardar_estado()
    
        return eliminados
    
    
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
            
            # Usar API de Bitget FUTUROS si está disponible
            if self.exchange:
                klines = self.exchange.get_klines(simbolo, config_optima['timeframe'], config_optima['num_velas'])
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
                    stoch_k_values.append(np.nan)
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
            
            # =====================================================
            # NUEVO: Calcular ADX, DI+ y DI- usando la función importada
            # =====================================================
            adx_results = calcular_adx_di(
                df['High'].values, 
                df['Low'].values, 
                df['Close'].values, 
                length=14
            )
            df['ADX'] = adx_results['adx']
            df['DI+'] = adx_results['di_plus']
            df['DI-'] = adx_results['di_minus']
            
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
            
            # =====================================================
            # NUEVO: Añadir panel de ADX, DI+ y DI- (Panel 2)
            # =====================================================
            apds.append(mpf.make_addplot(df['DI+'], color='#00FF00', width=1.5, panel=2, ylabel='ADX/DI'))
            apds.append(mpf.make_addplot(df['DI-'], color='#FF0000', width=1.5, panel=2))
            apds.append(mpf.make_addplot(df['ADX'], color='#000080', width=2, panel=2))  # Navy color
            # Línea threshold en ADX
            adx_threshold = [20] * len(df)
            apds.append(mpf.make_addplot(adx_threshold, color="#808080", linestyle='--', width=0.8, panel=2, alpha=0.5))
            
            fig, axes = mpf.plot(df, type='candle', style='nightclouds',
                               title=f'{simbolo} | {tipo_operacion} | {config_optima["timeframe"]} | BITGET FUTUROS + Breakout+Reentry',
                               ylabel='Precio',
                               addplot=apds,
                               volume=False,
                               returnfig=True,
                               figsize=(14, 12),
                               panel_ratios=(3, 1, 1))
            axes[2].set_ylim([0, 100])
            axes[2].grid(True, alpha=0.3)
            # Configurar panel ADX (axes[3])
            if len(axes) > 3:
                axes[3].set_ylim([0, 100])
                axes[3].grid(True, alpha=0.3)
                axes[3].set_ylabel('ADX/DI', fontsize=8)
            
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
            # 0. LIMPIEZA DE BREAKOUT EXPIRADOS - siempre ejecutar primero
            max_wait = self.config.get('max_wait_minutes', 120)
            self.limpiar_breakouts_expirados(max_wait)
            
            
            # 1. Sincronización con Bitget (cada ciclo)
            if self.exchange:
                self.sincronizar_con_bitget()
            
            # 2. Verificación y recolocación de TP/SL (cada ciclo)
            if self.exchange:
                self.verificar_y_recolocar_tp_sl()
            
            # 3. Reoptimización periódica
            if random.random() < 0.1:
                self.reoptimizar_periodicamente()
            
            # 4. Actualización periódica de monedas dinámicas (cada ~10 ciclos)
            if random.random() < 0.1:
                if self.config.get('simbolos_dinamicos', False) and self.exchange:
                    print("\n🔄 Actualización programada de monedas dinámicas...")
                    self.actualizar_moned()
            
            # 5. Verificar cierres de operaciones locales
            cierres = self.verificar_cierre_operaciones()
            if cierres:
                print(f"     📊 Operaciones cerradas: {', '.join(cierres)}")
            
            # 6. Guardar estado después del análisis
            self.guardar_estado()
            
            # 7. Escanear mercado para nuevas señales
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
        if self.exchange:
            print(f"   🤖 BITGET FUTUROS: ✅ Conectado")
            if self.ejecutar_operaciones_automaticas:
                print(f"   🤖 AUTO-TRADING: ✅ ACTIVADO (Dinero REAL)")
            else:
                print(f"   🤖 AUTO-TRADING: ❌ Solo señales")
        else:
            print(f"   🤖 BITGET FUTUROS: ❌ No configurado")
        if self.operaciones_activas:
            for simbolo, op in self.operaciones_activas.items():
                estado = "LONG" if op['tipo'] == 'LONG' else "SHORT"
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
        print("💾 PERSISTENCIA: ACTIVADA")
        print("🔄 REEVALUACIÓN: CADA 2 HORAS")
        print("🏦 INTEGRACIÓN: BITGET FUTUROS API (Dinero REAL)")
        print("=" * 70)
        print(f"💱 Símbolos: {len(self.moned) if self.moned else 'Dinámicos (se actualizarán al iniciar)'}")
        print(f"⏰ Timeframes: {', '.join(self.config.get('timeframes', []))}")
        print(f"🕯️ Velas: {self.config.get('velas_options', [])}")
        print(f"📏 ANCHO MÍNIMO: {self.config.get('min_channel_width_percent', 4)}%")
        print(f"🚀 Estrategia: 1) Detectar Breakout → 2) Esperar Reentry → 3) Confirmar con Stoch")
        if self.config.get('simbolos_dinamicos', False):
            print(f"📊 Modo: 🟢 MONEDAS DINÁMICAS (Top 200 por volumen)")
        else:
            print(f"📊 Modo: FIJOS")
        if self.exchange:
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

        # ACTUALIZACIÓN INICIAL DE MONEDAS DINÁMICAS
        print("\n📊 INICIALIZANDO MONEDAS DINÁMICAS POR VOLUMEN...")
        if self.exchange:
            if self.actualizar_moned():
                print(f"✅ Monedas dinámicas inicializadas: {len(self.moned)} símbolos")
            else:
                print("⚠️ Error inicializando monedas dinámicas, usando fallback")
        else:
            print("⚠️ No hay cliente Bitget configurado para actualizar monedas")
            # Intentar con configuración vacía
            self.moned = self.config.get('symbols', [])
            print(f"ℹ️ Usando símbolos de configuración: {len(self.moned)}")

        # SINCRONIZACIÓN INICIAL CON BITGET
        if self.exchange:
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
# CONFIGURACIÓN SIMPLE
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
        'scan_interval_minutes': 15,  
        'timeframes': ['15m', '30m', '1h', '4h'],
        'velas_options': [80, 100, 120, 150, 200],
        # Símbolos vacíos - Se generarán dinámicamente en actualizar_moned()
        'symbols': ['BTCUSDT'],
         # NUEVO: Tiempo máximo de espera para breakout
        'max_wait_minutes': int(os.environ.get('MAX_WAIT_MINUTES', '120')),
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
        'leverage_por_defecto': min(int(os.environ.get('LEVERAGE_POR_DEFECTO', '10')), 10)
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
    logger.info("🤖 Iniciando hilo del bot...")
    while True:
        try:
            bot.ejecutar_analisis()
            time.sleep(bot.config.get('scan_interval_minutes', 1) * 60)
        except Exception as e:
            logger.error(f"❌ Error en el hilo del bot: {e}", exc_info=True)
            time.sleep(60)

# Iniciar hilo del bot
bot_thread = threading.Thread(target=run_bot_loop, daemon=True)
bot_thread.start()

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
            "bitget_conectado": bot.exchange is not None,
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


def ejecutar_operacion_bitget(exchange, simbolo, tipo_operacion, capital_usd=None, leverage=None, sl=None, tp=None):
    """
    Ejecuta una operación usando CCXT aplicando la REGLA DE ORO (MARGEN_USDT = 1).
    """
    logger.info(f"🚀 EJECUTANDO OPERACIÓN REAL EN BITGET FUTUROS")
    logger.info(f"Símbolo: {simbolo}")
    logger.info(f"Tipo: {tipo_operacion}")
    try:
        tipo_ccxt = 'buy' if tipo_operacion == 'LONG' else 'sell'
        
        # CCXT expects symbols with a slash for Spot or :USDT for futures
        # We will dynamically format the symbol. Usually bitget futures in ccxt is BTC/USDT:USDT
        simbolo_ccxt = simbolo.replace('USDT', '/USDT:USDT') if '/' not in simbolo else simbolo
        
        # Balance details to return
        pnl_data = {}
        try:
            balance = exchange.fetch_balance()
            pnl_data['saldo_cuenta'] = float(balance.get('USDT', {}).get('free', 0))
            logger.info(f"💰 Saldo actual cuenta: ${pnl_data['saldo_cuenta']:.2f}")
        except:
            pnl_data['saldo_cuenta'] = 0.0
            logger.warning("⚠️ No se pudo obtener saldo de la cuenta")

        logger.info(f"Configurando apalancamiento {PALANCA_ESTRICTA}x para {simbolo}")
        try:
            exchange.set_margin_mode('isolated', simbolo_ccxt)
        except Exception as e:
            logger.warning(f"Error configurando modo de posición: {e}")
        
        try:
            exchange.set_leverage(PALANCA_ESTRICTA, simbolo_ccxt)
            logger.info("✓ Apalancamiento configurado exitosamente")
        except Exception as e:
            logger.warning(f"No se pudo configurar apalancamiento, continuando... {e}")

        # Obtener información del mercado para la precisión
        mercados = exchange.load_markets()
        if simbolo_ccxt not in mercados:
            print(f"[{simbolo}] Símbolo no encontrado en mercados cargados.")
            return None
            
        info_mercado = mercados[simbolo_ccxt]
        prec_precio = info_mercado['precision']['price']
        prec_cantidad = info_mercado['precision']['amount']
        
        # Size minimums
        min_cost = info_mercado.get('limits', {}).get('cost', {}).get('min', 5) # Default 5 USDT
        min_amount = info_mercado.get('limits', {}).get('amount', {}).get('min', 0)

        # Usar MARGEN_USDT global = 1, palanca = 10
        capital = MARGEN_USDT
        palanca = PALANCA_ESTRICTA
        
        ticker = exchange.fetch_ticker(simbolo_ccxt)
        precio_actual = float(ticker['last'])
        
        cantidad_real = (capital * palanca) / precio_actual
        # Apply precision
        from decimal import Decimal, ROUND_DOWN
        prec_str = format(prec_cantidad, 'f').rstrip('0')
        decimales = len(prec_str.split('.')[1]) if '.' in prec_str else 0
        cantidad_final = float(Decimal(str(cantidad_real)).quantize(Decimal(str(10**-decimales)), rounding=ROUND_DOWN))
        
        if cantidad_final < min_amount:
            logger.error(f"❌ Para {simbolo} a ${precio_actual:.2f}, el mínimo requiere más margen")
            return None

        logger.info(f"Colocando orden de {tipo_ccxt} con cantidad {cantidad_final}")
        orden_entrada = exchange.create_order(simbolo_ccxt, 'market', tipo_ccxt, cantidad_final)
        precio_entrada_real = orden_entrada.get('average', precio_actual)
        if not precio_entrada_real: precio_entrada_real = precio_actual
        
        logger.info(f"✓ Posición abierta en BITGET FUTUROS: {orden_entrada.get('id', 'N/A')}")
        
        # Stop loss logic (fijo) 1.6% (0.016)
        if tipo_operacion == 'LONG':
            sl_calc = precio_entrada_real * (1 - stopFijo)
            tp_calc = precio_entrada_real * (1 + stopFijo * 1.5)
            # Send SL order
            try:
                exchange.create_order(simbolo_ccxt, 'market', 'sell', cantidad_final, params={
                    'stopLossPrice': a_decimal_estricto(sl_calc, prec_precio),
                    'reduceOnly': True
                })
                logger.info("✓ Orden Stop Loss colocada")
            except Exception as e:
                logger.warning(f"⚠️ Aviso creando SL: {e}")
            # TP order
            try:
                exchange.create_order(simbolo_ccxt, 'market', 'sell', cantidad_final, params={
                    'takeProfitPrice': a_decimal_estricto(tp_calc, prec_precio),
                    'reduceOnly': True
                })
                logger.info("✓ Orden Take Profit colocada")
            except Exception as e:
                logger.warning(f"⚠️ Aviso creando TP: {e}")
        else:
            sl_calc = precio_entrada_real * (1 + stopFijo)
            tp_calc = precio_entrada_real * (1 - stopFijo * 1.5)
            try:
                exchange.create_order(simbolo_ccxt, 'market', 'buy', cantidad_final, params={
                    'stopLossPrice': a_decimal_estricto(sl_calc, prec_precio),
                    'reduceOnly': True
                })
                logger.info("✓ Orden Stop Loss colocada")
            except Exception as e:
                logger.warning(f"⚠️ Aviso creando SL: {e}")
            # TP order
            try:
                exchange.create_order(simbolo_ccxt, 'market', 'buy', cantidad_final, params={
                    'takeProfitPrice': a_decimal_estricto(tp_calc, prec_precio),
                    'reduceOnly': True
                })
                logger.info("✓ Orden Take Profit colocada")
            except Exception as e:
                logger.warning(f"⚠️ Aviso creando TP: {e}")

        logger.info(f"Capital usado (estimado): {capital}")
        logger.info(f"Precio Entrada Real: {precio_entrada_real}")
        return {
            'capital_usado': capital,
            'saldo_cuenta': pnl_data['saldo_cuenta'],
            'leverage': palanca,
            'precio_entrada': precio_entrada_real,
            'take_profit': tp_calc,
            'stop_loss': sl_calc,
            'orden_entrada': orden_entrada
        }
        
    except Exception as e:
        logger.error(f"❌ ERROR ejecutando orden en Bitget para {simbolo}: {e}")
        return None


if __name__ == '__main__':
    logger.info("🚀 Iniciando aplicación Flask...")
    setup_telegram_webhook()
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port)
