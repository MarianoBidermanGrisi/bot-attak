import sys, os, time, logging, requests, ccxt, gc
import pandas as pd
import numpy as np
from datetime import datetime
import pandas_ta as ta

# ==========================================================
# 1. LOGGING
# ==========================================================
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "combined_bot.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

# Memoria de sesión
ALERTS_HISTORY         = {}
PEAK_PRICES            = {}
COOLDOWNS              = {}
SESSION_ACTIVE_SYMBOLS = set()
LAST_KNOWN_PNL         = {}  # Último profit_pct conocido por símbolo (para detectar TP vs SL al cierre)

# ==========================================================
# 2. CREDENCIALES Y PARÁMETROS
# ==========================================================
API_KEY    = os.environ.get('BITGET_API_KEY')
SECRET_KEY = os.environ.get('BITGET_SECRET_KEY')
PASSPHRASE = os.environ.get('BITGET_PASSPHRASE')

TELEGRAM_TOKEN   = os.environ.get('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

# --- Timeframe y posiciones ---
TIMEFRAME          = '15m'
MAX_OPEN_POSITIONS = 4
RISK_PERCENT       = 0.07
LEVERAGE           = 10.0

# --- Gestión de riesgo ---
ENABLE_EARLY_EXIT          = True   # Activar o desactivar manualmente el early exit (True/False)
BE_TRIGGER_PCT    = 0.015   # Activar Breakeven al 1.5%
TRAILING_DIST_PCT = 0.019   # Trailing Stop 1.9%
MAX_POSITION_AGE_HOURS = 6.0
LIMIT_DISCOUNT_PCT = 0.01   # Descuento del 1.0% para orden Límite
LIMIT_ORDER_EXPIRY_MINUTES = 30 # Minutos de espera para la orden Límite

# --- Filtros de calidad ---
MAX_SL_DISTANCE_PCT   = 0.035
MIN_TP_DISTANCE_PCT   = 0.020
MIN_RISK_REWARD_RATIO = 1.8

# --- Parámetros de indicadores ---
# DIY Bot
DIY_ST_LENGTH = 10
DIY_ST_MULT   = 3.0
DIY_VMA_LEN   = 6   # Longitud de la Media Adaptativa VMA (reemplaza a DIY_EMA_LEN)
DIY_MACD_FAST = 12
DIY_MACD_SLOW = 26
DIY_MACD_SIG  = 9
DIY_EXPIRY    = 1 # Signal Expiry candles

# Zero Lag
ZL_LENGTH = 70
ZL_MULT   = 1.5 # ORIGINAL 1.2

# Two-Pole Oscillator
TP_FILTER_LEN = 15

# ==========================================================
# 3. CONEXIÓN A BITGET
# ==========================================================
try:
    exchange = ccxt.bitget({
        'apiKey': API_KEY, 'secret': SECRET_KEY, 'password': PASSPHRASE,
        'enableRateLimit': True, 'options': {'defaultType': 'swap'}
    })
    log.info("✅ Conexión a Bitget exitosa.")
except Exception as e:
    log.critical(f"❌ Error conectando a Bitget: {e}"); sys.exit(1)

# ==========================================================
# 4. FUNCIONES AUXILIARES
# ==========================================================
def send_telegram(msg: str):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=10)
    except Exception as e:
        log.warning(f"⚠️ Error enviando mensaje de Telegram: {e}")

def update_stop_loss(symbol, side, new_sl):
    try:
        clean = symbol.split(':')[0].replace('/', '')
        params = {
            'symbol': clean, 'marginCoin': 'USDT', 'productType': 'USDT-FUTURES',
            'planType': 'pos_loss',
            'stopLossTriggerPrice': str(exchange.price_to_precision(symbol, new_sl)),
            'stopLossTriggerType': 'fill_price',
            'holdSide': 'long' if side == 'long' else 'short'
        }
        exchange.private_mix_post_v2_mix_order_place_pos_tpsl(params)
        return True
    except Exception as e:
        log.error(f"❌ Error actualizando SL {symbol}: {e}"); return False

def close_position(symbol, side, reason=""):
    try:
        clean = symbol.split(':')[0].replace('/', '')
        exchange.private_mix_post_v2_mix_order_close_positions({
            'symbol': clean, 'productType': 'USDT-FUTURES',
            'marginCoin': 'USDT', 'holdSide': side
        })
        log.info(f"🔒 {symbol} cerrada. Motivo: {reason}")
        return True
    except Exception as e:
        log.error(f"❌ Error cerrando {symbol}: {e}"); return False

# ==========================================================
# 5. CÁLCULO DE INDICADORES
# ==========================================================
def calc_zlema(close, length=70):
    lag = int((length - 1) / 2)
    src = close + (close - close.shift(lag))
    return src.ewm(span=length, adjust=False).mean()

def calc_zl_bands(df, length=70, mult=1.2):
    # Usamos el atr de pandas_ta para mayor precisión
    atr = ta.atr(df['high'], df['low'], df['close'], length=length)
    highest_atr = atr.rolling(window=length * 3).max()
    volatility = highest_atr * mult
    zlema = calc_zlema(df['close'], length)
    return zlema, zlema + volatility, zlema - volatility

def calc_two_pole(close, filter_length=15):
    sma1 = close.rolling(25).mean()
    diff = close - sma1
    sma_diff = diff.rolling(25).mean()
    std_diff = diff.rolling(25).std().replace(0, 1e-10)
    normalized = (diff - sma_diff) / std_diff
    smooth1 = normalized.ewm(span=filter_length, adjust=False).mean()
    smooth2 = smooth1.ewm(span=filter_length, adjust=False).mean()
    return smooth2, smooth2.shift(4)

def calculate_vma(close_series, length=6):
    k30 = 1.0 / length
    close = close_series
    
    # pdm y mdm vectorizados con .clip()
    pdm = (close - close.shift(1)).clip(lower=0)
    mdm = (close.shift(1) - close).clip(lower=0)
    
    # pdmS y mdmS calculados vectorialmente como EMAs
    pdmS = pdm.ewm(alpha=k30, adjust=False).mean()
    mdmS = mdm.ewm(alpha=k30, adjust=False).mean()
    
    s = pdmS + mdmS
    
    # pdi y mdi vectorizados evitando división por cero
    pdi = np.where(s != 0, pdmS / s, 0)
    mdi = np.where(s != 0, mdmS / s, 0)
    
    # Suavizado de pdi y mdi vectorialmente
    pdiS = pd.Series(pdi, index=close.index).ewm(alpha=k30, adjust=False).mean()
    mdiS = pd.Series(mdi, index=close.index).ewm(alpha=k30, adjust=False).mean()
    
    d = (pdiS - mdiS).abs()
    s1 = pdiS + mdiS
    
    # iS vectorial
    dx = np.where(s1 != 0, d / s1, 0)
    iS = pd.Series(dx, index=close.index).ewm(alpha=k30, adjust=False).mean()
    
    # Máximos y Mínimos rodantes (vectorial) con min_periods=1 para evitar NaN al inicio
    hhv = iS.rolling(window=length, min_periods=1).max()
    llv = iS.rolling(window=length, min_periods=1).min()
    d1 = hhv - llv
    
    # Índice de Volatilidad (vI) vectorial
    vI = np.where(d1 != 0, (iS - llv) / d1, 0)
    
    # Bucle recursivo final
    vma_vals = np.zeros(len(close))
    close_arr = close.values
    vma_vals[0] = close_arr[0]
    
    for i in range(1, len(close)):
        vi_val = vI[i]
        vma_vals[i] = (1 - k30 * vi_val) * vma_vals[i-1] + k30 * vi_val * close_arr[i]
        
    return pd.Series(vma_vals, index=close.index)

def calculate_all_indicators(df):
    """Calcula los 3 sistemas de indicadores sobre el dataframe."""
    close = df['close']

    # --- DIY Bot ---
    # Supertrend de pandas_ta
    st = ta.supertrend(high=df['high'], low=df['low'], close=df['close'], length=DIY_ST_LENGTH, multiplier=DIY_ST_MULT)
    # Extraemos la columna de dirección (1 alcista, -1 bajista) dinámica
    st_dir_col = [col for col in st.columns if col.startswith('SUPERTd_')][0]
    df['ST_dir'] = st[st_dir_col]
    
    df['VMA'] = calculate_vma(close, length=DIY_VMA_LEN)
    
    # MACD de pandas_ta
    macd = ta.macd(close, fast=DIY_MACD_FAST, slow=DIY_MACD_SLOW, signal=DIY_MACD_SIG)
    macd_col = [col for col in macd.columns if col.startswith('MACD_')][0]
    macd_sig_col = [col for col in macd.columns if col.startswith('MACDs_')][0]
    df['MACD'] = macd[macd_col]
    df['MACD_sig'] = macd[macd_sig_col]

    # --- Zero Lag ---
    df['ZLEMA'], df['ZL_Upper'], df['ZL_Lower'] = calc_zl_bands(df, ZL_LENGTH, ZL_MULT)

    # --- Two-Pole ---
    df['Two_P'], df['Two_PP'] = calc_two_pole(close, TP_FILTER_LEN)

    # --- Filtro de Anomalía de Volumen ---
    # Usamos percentil 80 de las últimas 50 velas (adaptativo según liquidez de cada moneda)
    vol_pct_80 = df['volume'].rolling(50).quantile(0.80)
    df['Vol_Anomaly'] = df['volume'] > vol_pct_80

    return df.dropna()

# ==========================================================
# 6. GENERACIÓN DE SEÑALES COMBINADAS
# ==========================================================
def generate_signals(df):
    """
    Confluencia Triple:
    TENDENCIA (EMA200 + SuperTrend + ZL Trend) → GATILLO (ZL Pullback OR Two-Pole crossover)
    Signal Expiry: busca SuperTrend flip en las últimas DIY_EXPIRY velas.
    """
    df['Master_Buy']  = False
    df['Master_Sell'] = False
    
    # --- Estado de Tendencia ZL ---
    # Persistimos el último cruce de bandas mediante forward-fill
    cross_up = (df['close'] > df['ZL_Upper']) & (df['close'].shift(1) <= df['ZL_Upper'].shift(1))
    cross_down = (df['close'] < df['ZL_Lower']) & (df['close'].shift(1) >= df['ZL_Lower'].shift(1))
    
    df['zl_trend_state'] = np.nan
    df.loc[cross_up, 'zl_trend_state'] = 1
    df.loc[cross_down, 'zl_trend_state'] = -1
    df['zl_trend_state'] = df['zl_trend_state'].ffill().fillna(0)

    for i in range(DIY_EXPIRY + 1, len(df)):
        zl_trend = df['zl_trend_state'].iloc[i]

        # ---- FILTROS DE TENDENCIA ----
        vma_long  = df['close'].iloc[i] > df['VMA'].iloc[i]
        vma_short = df['close'].iloc[i] < df['VMA'].iloc[i]
        st_long   = df['ST_dir'].iloc[i] == 1
        st_short  = df['ST_dir'].iloc[i] == -1
        macd_long  = df['MACD'].iloc[i] > df['MACD_sig'].iloc[i]
        macd_short = df['MACD'].iloc[i] < df['MACD_sig'].iloc[i]
        zl_up   = zl_trend == 1
        zl_down = zl_trend == -1

        trend_long  = vma_long  and st_long  and macd_long  and zl_up
        trend_short = vma_short and st_short and macd_short and zl_down

        # ---- GATILLOS ----
        # 1. DIY Bot: SuperTrend Flip con Signal Expiry (3 velas)
        st_buy = False
        st_sell = False
        for j in range(i - DIY_EXPIRY + 1, i + 1):
            if df['ST_dir'].iloc[j] == 1 and df['ST_dir'].iloc[j-1] == -1:
                st_buy = True
            if df['ST_dir'].iloc[j] == -1 and df['ST_dir'].iloc[j-1] == 1:
                st_sell = True

        # 2. ZL Pullback
        zl_buy  = df['close'].iloc[i] > df['ZLEMA'].iloc[i] and df['close'].iloc[i-1] <= df['ZLEMA'].iloc[i-1]
        zl_sell = df['close'].iloc[i] < df['ZLEMA'].iloc[i] and df['close'].iloc[i-1] >= df['ZLEMA'].iloc[i-1]
        
        # 3. Two-Pole crossover
        tp_buy  = df['Two_P'].iloc[i] > df['Two_PP'].iloc[i] and df['Two_P'].iloc[i-1] <= df['Two_PP'].iloc[i-1] and df['Two_P'].iloc[i] < 0
        tp_sell = df['Two_P'].iloc[i] < df['Two_PP'].iloc[i] and df['Two_P'].iloc[i-1] >= df['Two_PP'].iloc[i-1] and df['Two_P'].iloc[i] > 0

        if trend_long  and (zl_buy  or tp_buy  or st_buy) and df['Vol_Anomaly'].iloc[i]:
            df.at[df.index[i], 'Master_Buy']  = True
        if trend_short and (zl_sell or tp_sell or st_sell) and df['Vol_Anomaly'].iloc[i]:
            df.at[df.index[i], 'Master_Sell'] = True

    return df

# ==========================================================
# 7. GESTIÓN DE POSICIONES ABIERTAS (Escudo Pro)
# ==========================================================
def manage_open_positions():
    global ALERTS_HISTORY, PEAK_PRICES, COOLDOWNS
    try:
        positions = exchange.fetch_positions()
        active_symbols = {p['symbol'] for p in positions if float(p['contracts']) > 0}

        # Limpiar posiciones cerradas
        for sym in list(PEAK_PRICES.keys()):
            if sym not in active_symbols:
                # PRIORIDAD 3: Cooldown diferenciado — 4h si perdió, 1h si ganó/BE
                status = ALERTS_HISTORY.get(sym)
                last_pnl = LAST_KNOWN_PNL.get(sym, None)

                if status == 'CLOSED_BY_BOT':
                    # El bot ya cerró activamente (Early Exit o Tiempo).
                    # Cooldown extendido porque puede haber sido una pérdida.
                    if sym not in COOLDOWNS:
                        COOLDOWNS[sym] = time.time() + 14400  # 4 horas
                    log.info(f"⏳ {sym} cerrada por bot. Cooldown 4h activado.")
                    # El mensaje ya fue enviado en el momento del cierre activo.
                elif status == 'BE' or (last_pnl is not None and last_pnl > 0):
                    # BE activado O el último PnL conocido fue positivo → ganó (TP del exchange)
                    COOLDOWNS[sym] = time.time() + 3600  # 1 hora
                    log.info(f"⏳ {sym} cerrada (BE/TP/Trail). Cooldown 1h activado. PnL final: {last_pnl*100:.2f}%" if last_pnl is not None else f"⏳ {sym} cerrada (BE/TP/Trail). Cooldown 1h activado.")
                    send_telegram(f"💰 *{sym} CERRADA*\nLa posición tocó el Take Profit, el Trailing Stop, o cerró en Breakeven (Riesgo Cero).\n⏳ Cooldown de 1 hora activado.")
                else:
                    # Stop Loss original o cierre manual → perdió
                    COOLDOWNS[sym] = time.time() + 14400  # 4 horas
                    log.info(f"⏳ {sym} cerrada en SL. Cooldown 4h activado. PnL final: {last_pnl*100:.2f}%" if last_pnl is not None else f"⏳ {sym} cerrada en SL. Cooldown 4h activado.")
                    send_telegram(f"📉 *{sym} CERRADA*\nLa posición tocó el Stop Loss original o fue cerrada manualmente.\n⏳ Cooldown de 4 horas activado.")

                del PEAK_PRICES[sym]
                if sym in ALERTS_HISTORY: del ALERTS_HISTORY[sym]
                if sym in LAST_KNOWN_PNL: del LAST_KNOWN_PNL[sym]
                SESSION_ACTIVE_SYMBOLS.discard(sym)

        for pos in positions:
            symbol = pos['symbol']
            side   = pos['side']
            if float(pos['contracts']) == 0: continue

            entry = float(pos['entryPrice'])
            mark  = float(pos['markPrice'])
            profit_pct = (mark - entry) / entry if side == 'long' else (entry - mark) / entry

            # Guardar último PnL conocido para detectar TP vs SL en el cierre del exchange
            LAST_KNOWN_PNL[symbol] = profit_pct

            # Actualizar pico
            if symbol not in PEAK_PRICES:
                PEAK_PRICES[symbol] = mark
            else:
                PEAK_PRICES[symbol] = max(PEAK_PRICES[symbol], mark) if side == 'long' else min(PEAK_PRICES[symbol], mark)

            # --- REGLA: CIERRE POR TIEMPO MÁXIMO ---
            try:
                open_ms = float(pos.get('timestamp') or pos['info'].get('cTime') or 0)
                if open_ms > 0:
                    age_h = (time.time() - open_ms / 1000) / 3600
                    if age_h >= MAX_POSITION_AGE_HOURS:
                        log.info(f"⏰ {symbol}: {age_h:.1f}h >= {MAX_POSITION_AGE_HOURS}h — cierre por tiempo.")
                        if close_position(symbol, side, "Tiempo máximo"):
                            send_telegram(f"⏰ *{symbol} CERRADA (Tiempo)*\nEdad: {age_h:.1f}h | PnL: {profit_pct*100:.2f}%")
                            ALERTS_HISTORY[symbol] = 'CLOSED_BY_BOT'
                            COOLDOWNS[symbol] = time.time() + 14400
                        continue
            except Exception as e:
                log.error(f"⚠️ Error tiempo máx {symbol}: {e}")

            # --- CÁLCULO ATR E INDICADORES PARA GESTIÓN ---
            current_atr = None
            try:
                # Obtenemos suficientes velas para el "warm-up" de indicadores
                ohlcv = exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=300)
                df_ind = pd.DataFrame(ohlcv, columns=['timestamp','open','high','low','close','volume'])
                current_atr = ta.atr(df_ind['high'], df_ind['low'], df_ind['close'], length=14).iloc[-1]
                
                # --- EARLY EXIT (Cierre Anticipado) ---
                if ENABLE_EARLY_EXIT:
                    df_ind['ZLEMA'] = calc_zlema(df_ind['close'], ZL_LENGTH)
                    df_ind['Two_P'], df_ind['Two_PP'] = calc_two_pole(df_ind['close'], TP_FILTER_LEN)
                    c_closed = df_ind.iloc[-2]  # Última vela cerrada
                    c_live   = df_ind.iloc[-1]  # Vela actual en formación
                    
                    early_exit = False
                    if side == 'long':
                        # Single-vela: Reacción inmediata a la vela viva
                        zlema_broken = c_live['close'] < c_live['ZLEMA']
                        tp_bear = c_live['Two_P'] < c_live['Two_PP']
                        if (zlema_broken or tp_bear) and profit_pct < -0.005:
                            early_exit = True
                    else:
                        # Single-vela: Reacción inmediata a la vela viva
                        zlema_broken = c_live['close'] > c_live['ZLEMA']
                        tp_bull = c_live['Two_P'] > c_live['Two_PP']
                        if (zlema_broken or tp_bull) and profit_pct < -0.005:
                            early_exit = True
                    
                    if early_exit:
                        log.info(f"🚨 EARLY EXIT activado para {symbol}. PnL: {profit_pct*100:.2f}%")
                        if close_position(symbol, side, "Early Exit (Vela-Viva)"):
                            send_telegram(f"🚨 *{symbol} CERRADA (Early Exit)*\nMotivo: ZLEMA roto + Two-Pole invertido (Live)\nPnL: {profit_pct*100:.2f}%")
                            ALERTS_HISTORY[symbol] = 'CLOSED_BY_BOT'
                            COOLDOWNS[symbol] = time.time() + 14400
                        continue
                
                # Porcentaje dinámico basado en ATR vs precio de entrada
                dynamic_be_trigger = (current_atr * 1.5) / entry   # BE a 1.5 ATR de distancia
            except Exception as e:
                log.warning(f"⚠️ {symbol} Fallo al calcular ATR/Indicadores en gestión. Usando fallback estático. {e}")
                dynamic_be_trigger = BE_TRIGGER_PCT
                dynamic_trail_dist = TRAILING_DIST_PCT

            # --- REGLA: BREAKEVEN ---
            if profit_pct >= dynamic_be_trigger and ALERTS_HISTORY.get(symbol) != 'BE':
                if update_stop_loss(symbol, side, entry):
                    send_telegram(f"🛡️ *{symbol} BREAKEVEN activado*\nDistancia cruzada: `{dynamic_be_trigger*100:.2f}%`")
                    ALERTS_HISTORY[symbol] = 'BE'

            # --- REGLA: TRAILING STOP (3 MARCHAS) ---
            if ALERTS_HISTORY.get(symbol) == 'BE':
                peak = PEAK_PRICES[symbol]
                
                if current_atr is not None and current_atr > 0:
                    # Calcular la ganancia en el pico actual
                    profit_at_peak = (peak - entry) / entry if side == 'long' else (entry - peak) / entry
                    # Convertir esa ganancia a cuántas veces equivale al ATR actual
                    atr_profit = profit_at_peak / (current_atr / entry)

                    # Lógica Escalonada
                    if atr_profit >= 2.7:
                        dynamic_trail_dist = (current_atr * 0.2) / peak
                        marcha_txt = "🚀 M3 Súper Agresivo"
                    elif atr_profit >= 2.2:
                        dynamic_trail_dist = (current_atr * 0.5) / peak
                        marcha_txt = "🔥 M2 Apretado"
                    else:
                        dynamic_trail_dist = (current_atr * 1.0) / peak
                        marcha_txt = "🐢 M1 Crecimiento"
                else:
                    # Si hubo un error en la API y current_atr no existe, usar fallback
                    marcha_txt = "⚠️ M-Fallback Estático"
                    # dynamic_trail_dist ya se definió en el except

                trail_sl = peak * (1 - dynamic_trail_dist) if side == 'long' else peak * (1 + dynamic_trail_dist)
                
                last_trail = ALERTS_HISTORY.get(f"{symbol}_trail", 0 if side == 'long' else 999999)
                moved = (side == 'long'  and trail_sl > last_trail * 1.001) or \
                        (side == 'short' and trail_sl < last_trail * 0.999)
                valid = (side == 'long'  and trail_sl > entry * 1.001) or \
                        (side == 'short' and trail_sl < entry * 0.999)
                        
                if moved and valid:
                    if update_stop_loss(symbol, side, trail_sl):
                        send_telegram(f"📈 *{symbol} TRAILING*\nSL → `{trail_sl:.4f}`\nFase: {marcha_txt}\nDistancia: `{(dynamic_trail_dist*100):.2f}%`")
                        ALERTS_HISTORY[f"{symbol}_trail"] = trail_sl

    except Exception as e:
        log.error(f"❌ Error en manage_open_positions: {e}")

# ==========================================================
# 7.5 GESTIÓN DE ÓRDENES LÍMITE ABIERTAS
# ==========================================================
def manage_open_orders():
    try:
        orders = exchange.fetch_open_orders()
        for order in orders:
            symbol = order['symbol']
            # Nos aseguramos de cancelar solo las órdenes límite que el bot puso para abrir
            if order.get('type') == 'limit':
                open_ms = float(order.get('timestamp') or order['info'].get('cTime') or 0)
                if open_ms > 0:
                    age_min = (time.time() - open_ms / 1000) / 60
                    if age_min >= LIMIT_ORDER_EXPIRY_MINUTES:
                        exchange.cancel_order(order['id'], symbol)
                        log.info(f"⏳ {symbol} Orden límite cancelada (Expiró tras {age_min:.1f}m)")
                        send_telegram(f"⏳ *{symbol} LÍMITE CANCELADA*\nEl precio no retrocedió al punto de entrada en {age_min:.1f}m.")
    except Exception as e:
        log.error(f"❌ Error en manage_open_orders: {e}")

# ==========================================================
# 8. BUCLE PRINCIPAL
# ==========================================================
if __name__ == "__main__":
    log.info("🚀 Combined Strategy Bot iniciado.")
    last_report_day = datetime.now().day

    while True:
        try:
            now = datetime.now()
            if now.hour == 0 and now.day != last_report_day:
                send_telegram("📊 *REPORTE DIARIO*"); last_report_day = now.day

            # Gestionar posiciones y órdenes abiertas
            manage_open_positions()
            manage_open_orders()

            # Balance
            try:
                balance = float(exchange.fetch_balance()['total'].get('USDT', 0))
            except Exception as e:
                log.error(f"Error balance: {e}"); balance = 0.0

            # Posiciones y Órdenes activas (Bloqueo de seguridad)
            positions = exchange.fetch_positions()
            busy_symbols = {p['symbol'] for p in positions if float(p['contracts']) > 0}
            
            try:
                open_orders = exchange.fetch_open_orders()
                limit_symbols = {o['symbol'] for o in open_orders if o.get('type') == 'limit'}
                busy_symbols.update(limit_symbols)
            except Exception as e:
                log.error(f"⚠️ Error leyendo órdenes para memoria: {e}")

            SESSION_ACTIVE_SYMBOLS = set(busy_symbols)
            log.info(f"🔄 [{now.strftime('%H:%M:%S')}] Balance: {balance:.2f} USDT | Abiertas: {len(busy_symbols)}/{MAX_OPEN_POSITIONS}")

            if len(busy_symbols) >= MAX_OPEN_POSITIONS:
                time.sleep(60); continue

            # Top 100 por volumen
            tickers = exchange.fetch_tickers()
            top_100 = [p[0] for p in sorted(
                [(s, float(t.get('quoteVolume', 0))) for s, t in tickers.items() if s.endswith('/USDT:USDT')],
                key=lambda x: x[1], reverse=True
            )[:100]]

            for symbol in top_100:
                if symbol in busy_symbols or len(busy_symbols) >= MAX_OPEN_POSITIONS: continue
                if symbol in COOLDOWNS:
                    if time.time() < COOLDOWNS[symbol]: continue
                    else: del COOLDOWNS[symbol]
                if symbol in SESSION_ACTIVE_SYMBOLS: continue

                try:
                    ohlcv = exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=500)
                    df = pd.DataFrame(ohlcv, columns=['timestamp','open','high','low','close','volume'])
                    if len(df) < 300: continue

                    df = calculate_all_indicators(df)
                    df = generate_signals(df)

                    # PRIORIDAD 1: Evaluar señales SOLO en la vela recién CERRADA para evitar repainting.
                    last_closed = df.iloc[-2]

                    buy  = bool(last_closed['Master_Buy'])
                    sell = bool(last_closed['Master_Sell'])

                    if not (buy or sell): continue

                    # PRIORIDAD 2: Usar precio live del mercado para SL/TP
                    # Evita que el SL quede dentro del rango actual de precio
                    # por slippage/gap entre cierre de vela y ejecución de orden.
                    try:
                        ticker_live = exchange.fetch_ticker(symbol)
                        price = float(ticker_live['last'])
                    except Exception:
                        price = float(df.iloc[-1]['close'])  # Fallback a la vela actual si falla

                    # ---- Anti-recompra ----
                    try:
                        chk = exchange.fetch_position(symbol)
                        if float(chk.get('contracts', 0)) > 0:
                            SESSION_ACTIVE_SYMBOLS.add(symbol); continue
                    except: pass

                    side_order = 'buy' if buy else 'sell'

                    # ---- Precio Límite Francotirador ----
                    if buy:
                        limit_price = price * (1 - LIMIT_DISCOUNT_PCT)
                    else:
                        limit_price = price * (1 + LIMIT_DISCOUNT_PCT)

                    # ---- SL / TP dinámico (RR 1:2) ----
                    # Usar ATR de la vela cerrada para evitar inflaciones por mechazos vivos
                    atr_val = ta.atr(df['high'], df['low'], df['close'], length=14).iloc[-2]
                    
                    if buy:
                        sl = limit_price - atr_val * 1.5
                        tp = limit_price + atr_val * 3.0
                    else:
                        sl = limit_price + atr_val * 1.5
                        tp = limit_price - atr_val * 3.0

                    sl_pct = abs(limit_price - sl) / limit_price
                    tp_pct = abs(limit_price - tp) / limit_price

                    # ---- REGLA 1: SL > TP → rechazar ----
                    if sl_pct >= tp_pct:
                        log.warning(f"⚠️ {symbol} RECHAZADA: SL% >= TP%"); continue
                    # ---- REGLA 2: SL demasiado lejos ----
                    if sl_pct > MAX_SL_DISTANCE_PCT:
                        log.warning(f"⚠️ {symbol} RECHAZADA: SL {sl_pct*100:.2f}% > {MAX_SL_DISTANCE_PCT*100:.1f}%"); continue
                    # ---- REGLA 3: TP demasiado cerca ----
                    if tp_pct < MIN_TP_DISTANCE_PCT:
                        log.warning(f"⚠️ {symbol} RECHAZADA: TP {tp_pct*100:.2f}% < {MIN_TP_DISTANCE_PCT*100:.1f}%"); continue
                    # ---- R/R mínimo ----
                    rr = tp_pct / sl_pct if sl_pct > 0 else 0
                    if rr < MIN_RISK_REWARD_RATIO:
                        log.warning(f"⚠️ {symbol} RECHAZADA: R/R {rr:.2f} < {MIN_RISK_REWARD_RATIO}"); continue

                    # ---- Cálculo de cantidad ----
                    market     = exchange.market(symbol)
                    step       = market['limits']['amount']['min'] or 1e-8
                    pos_value  = (balance * RISK_PERCENT) * LEVERAGE
                    raw_qty    = pos_value / limit_price
                    qty        = (raw_qty // step) * step

                    if qty <= 0:
                        log.warning(f"⚠️ {symbol} RECHAZADA: Cantidad calculada ({qty}) es menor que el mínimo permitido ({step})")
                        continue

                    # ---- Abrir posición ----
                    params = {
                        'marginCoin': 'USDT', 'marginMode': 'isolated', 'tradeSide': 'open',
                        'presetStopSurplusPrice': str(exchange.price_to_precision(symbol, tp)),
                        'presetStopLossPrice':    str(exchange.price_to_precision(symbol, sl))
                    }
                    exchange.set_leverage(int(LEVERAGE), symbol)
                    exchange.create_order(symbol, 'limit', side_order, qty, limit_price, params=params)

                    log.info(f"✅ {symbol} {side_order.upper()} LÍMITE | Precio actual: {price:.4f} | Target: {limit_price:.4f} | SL: {sl:.4f} | TP: {tp:.4f}")
                    send_telegram(
                        f"🎯 *{symbol} {side_order.upper()} LÍMITE*\n"
                        f"Precio Esperado: `{exchange.price_to_precision(symbol, limit_price)}`\n"
                        f"🛑 SL: `{exchange.price_to_precision(symbol, sl)}`\n"
                        f"🎯 TP: `{exchange.price_to_precision(symbol, tp)}`\n"
                        f"R/R: `{rr:.2f}` | VMA{DIY_VMA_LEN}✅ | ST✅ | ZL✅ | MACD✅"
                    )
                    busy_symbols.add(symbol)
                    SESSION_ACTIVE_SYMBOLS.add(symbol)

                except Exception as e:
                    log.error(f"⚠️ Error en {symbol}: {e}"); continue
                finally:
                    # Limpieza agresiva de memoria para entornos con poca RAM (Render)
                    gc.collect()

            time.sleep(60)

        except Exception as e:
            log.error(f"❌ Error ciclo principal: {e}"); time.sleep(60)
