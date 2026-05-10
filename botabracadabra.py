import sys
import os
import json
import time
import logging
import requests
import ccxt
import pandas as pd
import numpy as np
from datetime import datetime

# ==========================================================
# 1. CONFIGURACIÓN DE LOGGING
# ==========================================================
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot_completo.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

# Memoria temporal
ALERTS_HISTORY = {} 
PEAK_PRICES    = {} 
COOLDOWNS      = {} 
SESSION_ACTIVE_SYMBOLS = set() # Nueva memoria de sesión para evitar recompras

# ==========================================================
# 2. CREDENCIALES Y CONFIGURACIÓN
# ==========================================================
API_KEY    = os.environ.get('BITGET_API_KEY')
SECRET_KEY = os.environ.get('BITGET_SECRET_KEY')
PASSPHRASE = os.environ.get('BITGET_PASSPHRASE')

TELEGRAM_TOKEN   = os.environ.get('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

TIMEFRAME          = '1h'
EMA_MACRO          = 250# Filtro de tendencia diaria
HMA_SIGNAL         = 25 # Dirección inmediata (Optimizado)
STC_FAST           = 20
STC_SLOW           = 45
STC_CYCLE          = 10
STC_UPPER          = 65  # Para Shorts
STC_LOWER          = 35  # Para Longs
BE_TRIGGER_PCT     = 0.015# BE al 2%
TRAILING_DIST_PCT  = 0.019# Trail 2%
MAX_OPEN_POSITIONS = 4
RISK_PERCENT       = 0.07  # % riesgo
LEVERAGE           = 10.0

# ==============================================================================
# PARÁMETROS DE FILTRADO DE CALIDAD DE OPERACIÓN (CONFIGURABLE)
# ==============================================================================
# Regla 2: Distancia máxima al Stop Loss (en porcentaje del precio de entrada)
MAX_SL_DISTANCE_PCT = 0.035
# Regla 3: Distancia mínima al Take Profit (en porcentaje del precio de entrada)
MIN_TP_DISTANCE_PCT = 0.020
# Adicional: Ratio Riesgo-Beneficio mínimo (opcional)
MIN_RISK_REWARD_RATIO = 1.8

# ==============================================================================
# CONTROL DE FUNCIONALIDADES (INTERRUPTORES OPERACIONALES)
# ==============================================================================
# True  → El bot evalúa STC + HMA en cada ciclo y puede cerrar posiciones anticipadamente
# False → Las posiciones solo se cierran por SL/TP o Trailing Stop (sin interferencia)
ENABLE_EARLY_EXIT = True  # Cambiar a True para activar
# Tiempo máximo que una posición puede permanecer abierta antes de cierre forzoso
# Con TIMEFRAME='15m': 4h=16 velas | 6h=24 velas | 8h=32 velas
# Calibrar entre 4.0 y 8.0 según resultados observados
MAX_POSITION_AGE_HOURS = 24.0

# ==========================================================
# 3. FUNCIONES AUXILIARES
# ==========================================================
def send_telegram(message: str):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}, timeout=10)
    except: pass

def calculate_ema(series, length):
    return series.ewm(span=length, adjust=False).mean()

def calculate_hma(series, length):
    def _wma(s, n):
        weights = np.arange(1, n + 1, dtype=float)
        return s.rolling(n).apply(lambda x: np.dot(x, weights) / weights.sum(), raw=True)
    half, sqrtn = length // 2, int(np.sqrt(length))
    raw = 2 * _wma(series, half) - _wma(series, length)
    return _wma(raw, sqrtn)

def calculate_stc(series, fast=23, slow=50, length=10):
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd = ema_fast - ema_slow
    
    macd_min = macd.rolling(window=length).min()
    macd_max = macd.rolling(window=length).max()
    stoch_macd = 100 * (macd - macd_min) / (macd_max - macd_min).replace(0, np.nan)
    stoch_macd = stoch_macd.ffill().fillna(50)  # 50 = neutro: evita falsa señal oversold en warm-up
    
    smoothed_stoch = stoch_macd.ewm(alpha=0.5, adjust=False).mean()
    
    stoch_min = smoothed_stoch.rolling(window=length).min()
    stoch_max = smoothed_stoch.rolling(window=length).max()
    stoch_stoch = 100 * (smoothed_stoch - stoch_min) / (stoch_max - stoch_min).replace(0, np.nan)
    stoch_stoch = stoch_stoch.ffill().fillna(50)  # 50 = neutro: evita falsa señal overbought en warm-up
    
    return stoch_stoch.ewm(alpha=0.5, adjust=False).mean()

def calculate_poc(df_5m):
    """Calcula el POC exacto sumando el volumen por nivel de precio (24h)."""
    if df_5m.empty: return 0
    # Redondeamos al tick size promedio para agrupar niveles de precio
    tick_size = df_5m['close'].diff().abs().median()
    if tick_size == 0: return 0  # Guard: evita división por cero si todos los precios son iguales
    df_5m['price_level'] = (df_5m['close'] / tick_size).round() * tick_size
    poc = df_5m.groupby('price_level')['volume'].sum().idxmax()
    return poc

def detect_order_blocks(df, lookback=100):
    """Detecta OBs institucionales: Volumen inusual + Imbalance + BOS."""
    obs = {'bullish': [], 'bearish': []}
    avg_vol = df['volume'].rolling(20).mean()
    
    for i in range(len(df) - 6, 20, -1):  # -6 para que iloc[i+2] nunca salga del rango
        # 1. ¿Hay volumen inusual? (1.5x el promedio)
        if df['volume'].iloc[i] > avg_vol.iloc[i] * 1.5:
            # 2. ¿Hubo un Imbalance (FVG) inmediato?
            # Bullish: Gap entre el High de i-1 y el Low de i+1
            if df['low'].iloc[i+1] > df['high'].iloc[i-1]:
                # 3. ¿Rompió estructura (BOS)? 
                if df['close'].iloc[i+2] > df['high'].iloc[i-10:i].max():
                    obs['bullish'].append(df['low'].iloc[i])
            
            # Bearish: Gap entre el Low de i-1 y el High de i+1
            elif df['high'].iloc[i+1] < df['low'].iloc[i-1]:
                if df['close'].iloc[i+2] < df['low'].iloc[i-10:i].min():
                    obs['bearish'].append(df['high'].iloc[i])
                    
        if len(obs['bullish']) > 2 and len(obs['bearish']) > 2: break
    return obs

# Función send_telegram fue definida previamente en la línea 58

# ==========================================================
# 4. CONEXIÓN Y GESTIÓN
# ==========================================================
try:
    exchange = ccxt.bitget({'apiKey': API_KEY, 'secret': SECRET_KEY, 'password': PASSPHRASE, 'enableRateLimit': True, 'options': {'defaultType': 'swap'}})
    log.info("✅ CONEXIÓN EXITOSA — ESPERANDO NUEVA ESTRATEGIA.")
    log.info(f"{'✅' if ENABLE_EARLY_EXIT else '⏸️'} Salida Anticipada: {'ACTIVADA' if ENABLE_EARLY_EXIT else 'DESACTIVADA'}")
except Exception as e: log.critical(f"❌ ERROR: {e}"); sys.exit(1)

def update_stop_loss(symbol, side, new_sl):
    try:
        new_sl_fmt = exchange.price_to_precision(symbol, new_sl)
        clean_symbol = symbol.split(':')[0].replace('/', '')
        params = {
            'symbol': clean_symbol, 'marginCoin': 'USDT', 'productType': 'USDT-FUTURES',
            'planType': 'pos_loss', 'stopLossTriggerPrice': str(new_sl_fmt), 
            'stopLossTriggerType': 'fill_price', 'holdSide': 'long' if side == 'long' else 'short'
        }
        exchange.private_mix_post_v2_mix_order_place_pos_tpsl(params)
        return True
    except Exception as e: 
        log.error(f"❌ Fallo al actualizar SL para {symbol}: {e}")
        return False

def manage_escudo_pro():
    global ALERTS_HISTORY, PEAK_PRICES, COOLDOWNS
    try:
        positions = exchange.fetch_positions()
        active_symbols = [p['symbol'] for p in positions if float(p['contracts']) > 0]
        
        for sym in list(PEAK_PRICES.keys()):
            if sym not in active_symbols:
                COOLDOWNS[sym] = time.time() + 3600
                log.info(f"⏳ {sym} CERRADA. Cooldown 1h activado.")
                try:
                    time.sleep(2)
                    trades = exchange.fetch_my_trades(sym, limit=20)
                    if trades:
                        trade_pnl, trade_fees, last_closing_trade = 0.0, 0.0, None
                        for t in reversed(trades):
                            if float(t['info'].get('profit', 0)) != 0: last_closing_trade = t; break
                        if last_closing_trade:
                            order_id = last_closing_trade.get('order') or last_closing_trade['info'].get('orderId')
                            for t in trades:
                                if (t.get('order') or t['info'].get('orderId')) == order_id:
                                    trade_pnl += float(t['info'].get('profit', 0))
                                    if 'fee' in t and t['fee']: trade_fees += abs(float(t['fee'].get('cost', 0)))
                            net = trade_pnl - trade_fees
                            status = "✅ TP" if net > 0 else "❌ SL"
                            if trade_pnl == 0: status = "⚪ BE"
                            send_telegram(f"🏁 *{sym} CERRADA*\nPnL: {net:.2f} USDT ({status})\nFees: -{trade_fees:.2f} USDT")
                except: pass
                del PEAK_PRICES[sym]
                if sym in ALERTS_HISTORY: del ALERTS_HISTORY[sym]
                if sym in SESSION_ACTIVE_SYMBOLS: SESSION_ACTIVE_SYMBOLS.remove(sym)

        for pos in positions:
            symbol, side = pos['symbol'], pos['side']
            if float(pos['contracts']) == 0: continue
            entry, mark = float(pos['entryPrice']), float(pos['markPrice'])
            profit_pct = (mark - entry) / entry if side == 'long' else (entry - mark) / entry
            
            # Inicializar o actualizar PEAK_PRICES siempre antes de evaluar salidas
            if symbol not in PEAK_PRICES: PEAK_PRICES[symbol] = mark
            else: PEAK_PRICES[symbol] = max(PEAK_PRICES[symbol], mark) if side == 'long' else min(PEAK_PRICES[symbol], mark)
            
            # --- ⏰ CIERRE POR TIEMPO MÁXIMO ---
            try:
                # CCXT normaliza el timestamp en milisegundos, fallback a cTime (Bitget v2)
                open_ms = float(pos.get('timestamp') or pos['info'].get('cTime') or 0)
                if open_ms > 0:
                    age_hours = (time.time() - open_ms / 1000) / 3600
                    if age_hours >= MAX_POSITION_AGE_HOURS:
                        log.info(f"⏰ {symbol}: {age_hours:.1f}h >= {MAX_POSITION_AGE_HOURS}h máx — cerrando por tiempo.")
                        clean_symbol = symbol.split(':')[0].replace('/', '')
                        exchange.private_mix_post_v2_mix_order_close_positions({
                            'symbol': clean_symbol,
                            'productType': 'USDT-FUTURES',
                            'marginCoin': 'USDT',
                            'holdSide': side
                        })
                        send_telegram(f"⏰ *{symbol} CERRADA (Tiempo máx)*\n"
                                      f"Edad: {age_hours:.1f}h\n"
                                      f"PnL aprox: {profit_pct*100:.2f}%")
                        continue  # Saltar al siguiente símbolo
            except Exception as e:
                log.error(f"⚠️ Error evaluando tiempo máximo para {symbol}: {e}")
            # --- FIN CIERRE POR TIEMPO MÁXIMO ---

            # --- 🚀 SALIDA ANTICIPADA (HMA + STC) ---
            if ENABLE_EARLY_EXIT:
                try:
                    # 1. Descargar datos para calcular indicadores (limit=450 para EMA-400)
                    ohlcv = exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=450)
                    df_ee = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                    
                    # 2. Calcular indicadores
                    df_ee['hma_25'] = calculate_hma(df_ee['close'], HMA_SIGNAL)
                    df_ee['stc'] = calculate_stc(df_ee['close'], STC_FAST, STC_SLOW, STC_CYCLE)
                    
                    last_ee, prev_ee = df_ee.iloc[-1], df_ee.iloc[-2]
                    current_stc = last_ee['stc']
                    current_hma = last_ee['hma_25']
                    prev_hma = prev_ee['hma_25']
                    
                    close_position = False
                    reason = ""
                    
                    # 3. Evaluación del Doble Check
                    if side == 'long':
                        # STC < 75 Y (Precio < HMA Y Pendiente Negativa)
                        if current_stc < STC_UPPER and (mark < current_hma and current_hma < prev_hma):
                            close_position = True
                            reason = f"STC ({current_stc:.1f} < {STC_UPPER}) + HMA Negativa"
                    elif side == 'short':
                        # STC > 25 Y (Precio > HMA Y Pendiente Positiva)
                        if current_stc > STC_LOWER and (mark > current_hma and current_hma > prev_hma):
                            close_position = True
                            reason = f"STC ({current_stc:.1f} > {STC_LOWER}) + HMA Positiva"
                            
                    # 4. Ejecución del Cierre
                    if close_position:
                        log.info(f"🚨 SALIDA ANTICIPADA {symbol} ({side}): {reason}")
                        
                        # Llamada directa a la API nativa de Bitget V2 para cerrar toda la posición
                        clean_symbol = symbol.split(':')[0].replace('/', '')
                        params_close = {
                            'symbol': clean_symbol,
                            'productType': 'USDT-FUTURES',
                            'marginCoin': 'USDT',
                            'holdSide': side
                        }
                        exchange.private_mix_post_v2_mix_order_close_positions(params_close)
                        
                        send_telegram(f"🚨 *{symbol} CERRANDO (Anticipada)*\nMotivo: {reason}\nPnL aprox: {profit_pct*100:.2f}%")
                        
                        # Dejamos que el sistema nativo del bot se encargue de limpiar la memoria
                        # en el próximo ciclo para que aplique el Cooldown de 1h correctamente.
                        continue # Saltar a la siguiente posición
                except Exception as e:
                    log.error(f"⚠️ Error evaluando Salida Anticipada para {symbol}: {e}")
            # --- FIN LÓGICA SALIDA ANTICIPADA ---


            if profit_pct >= BE_TRIGGER_PCT and ALERTS_HISTORY.get(symbol) != 'BE':
                if update_stop_loss(symbol, side, entry):
                    send_telegram(f"🛡️ *{symbol} PROTEGIDA* (BE)"); ALERTS_HISTORY[symbol] = 'BE'

            if ALERTS_HISTORY.get(symbol) == 'BE':
                trail_sl = PEAK_PRICES[symbol] * (1 - TRAILING_DIST_PCT) if side == 'long' else PEAK_PRICES[symbol] * (1 + TRAILING_DIST_PCT)
                # Filtro de ruido reducido al 0.1% (1.001 / 0.999) para un trailing más fluido
                if (side == 'long' and trail_sl > entry * 1.001) or (side == 'short' and trail_sl < entry * 0.999):
                    last_trail = ALERTS_HISTORY.get(f"{symbol}_trail", 0 if side == 'long' else 999999)
                    if (side == 'long' and trail_sl > last_trail * 1.001) or (side == 'short' and trail_sl < last_trail * 0.999):
                        if update_stop_loss(symbol, side, trail_sl):
                            send_telegram(f"📈 *{symbol} TRAILING*: SL @ {trail_sl:.2f}"); ALERTS_HISTORY[f"{symbol}_trail"] = trail_sl
    except Exception as e: 
        log.error(f"❌ Error crítico en manage_escudo_pro: {e}")

# ==========================================================
# 5. BUCLE PRINCIPAL
# ==========================================================
if __name__ == "__main__":
    last_report_day = datetime.now().day

    while True:
        try:
            now = datetime.now()
            if now.hour == 0 and now.day != last_report_day:
                send_telegram("📊 *REPORTE DIARIO GENERADO*"); last_report_day = now.day

            manage_escudo_pro()
            
            try:
                # Usamos el balance total estandarizado de CCXT
                balance_data = exchange.fetch_balance()
                balance = float(balance_data['total'].get('USDT', 0))
            except Exception as e:
                log.error(f"Error obteniendo balance: {e}")
                balance = 0.0

            positions = exchange.fetch_positions()
            # Actualizamos SESSION_ACTIVE_SYMBOLS con lo que el exchange nos dice
            busy_symbols = {p['symbol'] for p in positions if float(p['contracts']) > 0}
            SESSION_ACTIVE_SYMBOLS.update(busy_symbols)
            
            occupied_str = ", ".join([s.split('/')[0] for s in busy_symbols]) if busy_symbols else "Ninguno"
            log.info(f"🔄 CICLO [{now.strftime('%H:%M:%S')}] | Balance: {balance:.2f} | Ocupados: [{occupied_str}]")
            
            if len(busy_symbols) >= MAX_OPEN_POSITIONS:
                time.sleep(60); continue

            tickers = exchange.fetch_tickers()
            top_100 = [p[0] for p in sorted([(s, float(t.get('quoteVolume', 0))) for s, t in tickers.items() if s.endswith('/USDT:USDT')], key=lambda x: x[1], reverse=True)[:100]]

            for symbol in top_100:
                if symbol in busy_symbols or len(busy_symbols) >= MAX_OPEN_POSITIONS: continue
                if symbol in COOLDOWNS:
                    if time.time() < COOLDOWNS[symbol]: continue
                    else: del COOLDOWNS[symbol]

                try:
                    # Obtenemos datos para la tendencia (limit=450 para EMA-400) y 5m para el POC preciso
                    ohlcv_1h = exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=450)
                    ohlcv_5m = exchange.fetch_ohlcv(symbol, timeframe='5m', limit=288) # 24h = 288 velas de 5m
                    
                    df = pd.DataFrame(ohlcv_1h, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                    df_5m = pd.DataFrame(ohlcv_5m, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                    
                    # CÁLCULOS PRECISOS
                    df['ema_200'] = calculate_ema(df['close'], EMA_MACRO)
                    df['hma_25']  = calculate_hma(df['close'], HMA_SIGNAL)
                    df['stc']     = calculate_stc(df['close'], STC_FAST, STC_SLOW, STC_CYCLE)
                    poc           = calculate_poc(df_5m)
                    obs           = detect_order_blocks(df)
                    
                    last, prev = df.iloc[-1], df.iloc[-2]
                    price, ema, hma, stc = last['close'], last['ema_200'], last['hma_25'], last['stc']
                    
                    # 1. Filtro Macro: Precio vs EMA 200
                    is_bull_macro = price > ema
                    is_bear_macro = price < ema
                    
                    # 2. Contexto de Volumen: Precio vs POC
                    is_above_poc = price > poc
                    is_below_poc = price < poc

                    # 3. Estructura SMC (Rebote en OB o BOS)
                    # Bullish: Rebote en OB o precio arriba de un OB reciente
                    has_bull_structure = any(price > ob * 0.998 and price < ob * 1.01 for ob in obs['bullish']) or (price > df['high'].iloc[-5:-1].max())
                    # Bearish: Rechazo en OB o precio abajo de un OB reciente
                    has_bear_structure = any(price < ob * 1.002 and price > ob * 0.99 for ob in obs['bearish']) or (price < df['low'].iloc[-5:-1].min())

                    # 4. Confirmación HMA 25 (Pendiente y posición)
                    hma_slope_up = hma > prev['hma_25']
                    hma_slope_down = hma < prev['hma_25']
                    is_above_hma = price > hma
                    is_below_hma = price < hma

                    # 5. Gatillo: Cruce de STC (25 hacia arriba Long, 75 hacia abajo Short)
                    stc_cross_up = stc >= STC_LOWER and prev['stc'] < STC_LOWER
                    stc_cross_down = stc <= STC_UPPER and prev['stc'] > STC_UPPER

                    # --- CONFLUENCIA TOTAL ---
                    buy  = is_bull_macro and is_above_poc and has_bull_structure and hma_slope_up and is_above_hma and stc_cross_up
                    sell = is_bear_macro and is_below_poc and has_bear_structure and hma_slope_down and is_below_hma and stc_cross_down

                    # --- LOG DE INDICADORES POR SÍMBOLO ---
                    fmt_hma = exchange.price_to_precision(symbol, hma)
                    fmt_poc = exchange.price_to_precision(symbol, poc)
                    bull_top = exchange.price_to_precision(symbol, obs['bullish'][0]) if obs['bullish'] else "N/A"
                    bear_top = exchange.price_to_precision(symbol, obs['bearish'][0]) if obs['bearish'] else "N/A"
                    log.info(
                        f"📊 {symbol} | HMA25: {fmt_hma} | POC: {fmt_poc} | STC: {stc:.1f} | "
                        f"OB_Bull: {bull_top} | OB_Bear: {bear_top} | "
                        f"Bull={int(buy)} Bear={int(sell)}"
                    )

                    if (buy or sell) and symbol not in SESSION_ACTIVE_SYMBOLS:
                        # VERIFICACIÓN DE ÚLTIMO SEGUNDO (Anti-recompra)
                        try:
                            check_pos = exchange.fetch_position(symbol)
                            if float(check_pos.get('contracts', 0)) > 0:
                                log.warning(f"⚠️ {symbol} ya tiene posición. Abortando re-entrada.")
                                SESSION_ACTIVE_SYMBOLS.add(symbol)
                                continue
                        except: pass
                        side = 'buy' if buy else 'sell'
                        
                        # --- GESTIÓN DE SL/TP BASADA EN ORDER BLOCKS ---
                        sl, tp = None, None
                        buffer = 0.001 # 0.1% de margen
                        
                        if buy:
                            # SL detrás del bloque alcista más cercano
                            if obs['bullish']:
                                sl = obs['bullish'][0] * (1 - buffer)
                            else:
                                sl = price * 0.98 # Fallback 2%
                            
                            # TP en el bloque bajista (contrario) más cercano por encima
                            targets = [ob for ob in obs['bearish'] if ob > price]
                            if targets:
                                tp = min(targets) # El más cercano arriba
                            else:
                                tp = price + (price - sl) * 2.0 # Fallback RR 1:2
                                
                        else: # SELL
                            # SL detrás del bloque bajista más cercano
                            if obs['bearish']:
                                sl = obs['bearish'][0] * (1 + buffer)
                            else:
                                sl = price * 1.02 # Fallback 2%
                            
                            # TP en el bloque alcista (contrario) más cercano por debajo
                            targets = [ob for ob in obs['bullish'] if ob < price]
                            if targets:
                                tp = max(targets) # El más cercano abajo
                            else:
                                tp = price - (sl - price) * 2.0 # Fallback RR 1:2

                        # Verificación de seguridad para SL/TP básica
                        if buy and (tp <= price or sl >= price): continue
                        if sell and (tp >= price or sl <= price): continue

                        # ==============================================================================
                        # REGLAS DE FILTRADO DE CALIDAD DE OPERACIÓN
                        # ==============================================================================
                        
                        # Cálculo simplificado de distancias en valor absoluto
                        sl_distance_pct = abs(price - sl) / price
                        tp_distance_pct = abs(price - tp) / price

                        # Regla 1: Rechazar si distancia de SL >= distancia de TP (relación riesgo-beneficio invertida numéricamente)
                        if sl_distance_pct >= tp_distance_pct:
                            log.warning(f"⚠️ {symbol} RECHAZADA ({side.upper()}): Distancia SL ({sl_distance_pct*100:.2f}%) >= TP ({tp_distance_pct*100:.2f}%) - R/R inválido")
                            continue

                        # Regla 2: Rechazar si distancia al SL > MAX_SL_DISTANCE_PCT
                        if sl_distance_pct > MAX_SL_DISTANCE_PCT:
                            log.warning(f"⚠️ {symbol} RECHAZADA ({side.upper()}): Distancia SL ({sl_distance_pct*100:.2f}%) > {MAX_SL_DISTANCE_PCT*100:.1f}% (MAX)")
                            continue

                        # Regla 3: Rechazar si distancia al TP < MIN_TP_DISTANCE_PCT
                        if tp_distance_pct < MIN_TP_DISTANCE_PCT:
                            log.warning(f"⚠️ {symbol} RECHAZADA ({side.upper()}): Distancia TP ({tp_distance_pct*100:.2f}%) < {MIN_TP_DISTANCE_PCT*100:.1f}% (MIN)")
                            continue

                        # Validación adicional: Ratio Riesgo-Beneficio mínimo
                        if MIN_RISK_REWARD_RATIO > 0:
                            risk_reward_ratio = tp_distance_pct / sl_distance_pct if sl_distance_pct > 0 else 0
                            if risk_reward_ratio < MIN_RISK_REWARD_RATIO:
                                log.warning(f"⚠️ {symbol} RECHAZADA ({side.upper()}): R/R ({risk_reward_ratio:.2f}) < {MIN_RISK_REWARD_RATIO:.1f} (MIN)")
                                continue
                        
                        # Cálculo de cantidad con redondeo estricto hacia abajo (floor)
                        target_margin = balance * RISK_PERCENT
                        pos_value = target_margin * LEVERAGE
                        raw_qty = pos_value / price
                        
                        # Obtenemos la precisión del lote para este símbolo
                        market = exchange.market(symbol)
                        precision = market['precision']['amount']
                        step = market['limits']['amount']['min'] or (10**(-int(precision)))  # int() evita resultado incorrecto con floats
                        
                        # Forzamos redondeo hacia abajo (floor) al step más cercano
                        qty_precision = (raw_qty // step) * step
                        
                        actual_margin = (qty_precision * price) / LEVERAGE
                        
                        # PROTECCIÓN EXTRA: Si por algún motivo el margen real se pasa, bajamos un lote
                        if actual_margin > target_margin:
                            qty_precision -= step
                            actual_margin = (qty_precision * price) / LEVERAGE

                        log.info(f"⚖️ {symbol} | Objetivo: {target_margin:.2f} | Real: {actual_margin:.2f} (Floor)")

                        params = {
                            'marginCoin': 'USDT', 'marginMode': 'isolated', 'tradeSide': 'open', 
                            'presetStopSurplusPrice': str(exchange.price_to_precision(symbol, tp)), 
                            'presetStopLossPrice': str(exchange.price_to_precision(symbol, sl))
                        }
                        exchange.create_order(symbol, 'market', side, qty_precision, params=params)
                        
                        fmt_price = exchange.price_to_precision(symbol, price)
                        fmt_sl = exchange.price_to_precision(symbol, sl)
                        fmt_tp = exchange.price_to_precision(symbol, tp)

                        msg = f"🏛️ *{symbol} {side.upper()}* (OB to OB)\n"
                        msg += f"Entrada: `{fmt_price}`\n"
                        msg += f"🛑 SL: `{fmt_sl}` (Tras Bloque)\n"
                        msg += f"🎯 TP: `{fmt_tp}` (Bloque Contrario)\n"
                        msg += f"\n📊 *Indicadores:*\n"
                        msg += f"  HMA 25: `{fmt_hma}`\n"
                        msg += f"  POC:    `{fmt_poc}`\n"
                        msg += f"  STC:    `{stc:.1f}`\n"
                        msg += f"  OB Alcista: `{bull_top}`\n"
                        msg += f"  OB Bajista: `{bear_top}`"
                        send_telegram(msg)
                        busy_symbols.add(symbol)
                        SESSION_ACTIVE_SYMBOLS.add(symbol)

                except Exception as e:
                    log.error(f"⚠️ Error analizando/abriendo {symbol}: {e}")
                    continue

            time.sleep(60)
        except Exception as e: log.error(f"Error ciclo: {e}"); time.sleep(60)




