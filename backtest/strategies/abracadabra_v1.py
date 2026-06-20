import pandas as pd
import numpy as np

# --- Bot's exact indicator functions ---

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
    stoch_macd = stoch_macd.ffill().fillna(0)
    smoothed_stoch = stoch_macd.ewm(alpha=0.5, adjust=False).mean()
    stoch_min = smoothed_stoch.rolling(window=length).min()
    stoch_max = smoothed_stoch.rolling(window=length).max()
    stoch_stoch = 100 * (smoothed_stoch - stoch_min) / (stoch_max - stoch_min).replace(0, np.nan)
    stoch_stoch = stoch_stoch.ffill().fillna(0)
    return stoch_stoch.ewm(alpha=0.5, adjust=False).mean()

def calculate_poc(df_5m_window):
    if df_5m_window is None or df_5m_window.empty:
        return 0
    tick_size = df_5m_window['close'].diff().abs().median()
    if pd.isna(tick_size) or tick_size == 0:
        return 0
    price_levels = (df_5m_window['close'] / tick_size).round() * tick_size
    poc = price_levels.groupby(price_levels).agg(lambda x: df_5m_window.loc[x.index, 'volume'].sum()).idxmax()
    return poc

def get_poc_at_timestamp(df_5m_full, ts, hours_24=False):
    if df_5m_full is None or df_5m_full.empty:
        return 0
    end = pd.to_datetime(ts) if not isinstance(ts, pd.Timestamp) else ts
    start = end - pd.Timedelta(hours=24) if hours_24 else end - pd.Timedelta(days=30)
    window = df_5m_full[(df_5m_full['timestamp'] >= start) & (df_5m_full['timestamp'] <= end)]
    return calculate_poc(window)

def detect_order_blocks(df_window):
    obs = {'bullish': [], 'bearish': []}
    avg_vol = df_window['volume'].rolling(20).mean()
    for i in range(len(df_window) - 5, 20, -1):
        if df_window['volume'].iloc[i] > avg_vol.iloc[i] * 1.5:
            if df_window['low'].iloc[i+1] > df_window['high'].iloc[i-1]:
                if df_window['close'].iloc[i+2] > df_window['high'].iloc[i-10:i].max():
                    obs['bullish'].append(df_window['low'].iloc[i])
            elif df_window['high'].iloc[i+1] < df_window['low'].iloc[i-1]:
                if df_window['close'].iloc[i+2] < df_window['low'].iloc[i-10:i].min():
                    obs['bearish'].append(df_window['high'].iloc[i])
        if len(obs['bullish']) > 2 and len(obs['bearish']) > 2:
            break
    return obs

# --- Parameters (match bot exactly) ---
PARAMS = {
    'TIMEFRAME': '2h',
    'EMA_MACRO': 200,
    'HMA_SIGNAL': 25,
    'STC_FAST': 25,
    'STC_SLOW': 30,
    'STC_CYCLE': 15,
    'STC_UPPER': 80,
    'STC_LOWER': 30,
    'BE_TRIGGER_PCT': 0.015,
    'BE_OFFSET_PCT': 0.005,
    'TRAILING_DIST_PCT': 0.012,
    'SL_LOOKBACK': 10,
    'MAX_SL_PCT': 0.02,
    'RR_RATIO': 2.0,
    'MAX_OPEN_POSITIONS': 5,
    'RISK_PERCENT': 0.07,
    'LEVERAGE': 10.0,
}

def compute_indicators(df, df_5m_full=None):
    df = df.copy()
    df['ema_200'] = calculate_ema(df['close'], PARAMS['EMA_MACRO'])
    df['hma_25'] = calculate_hma(df['close'], PARAMS['HMA_SIGNAL'])
    df['stc'] = calculate_stc(df['close'], PARAMS['STC_FAST'], PARAMS['STC_SLOW'], PARAMS['STC_CYCLE'])
    df['volume_ratio'] = df['volume'] / df['volume'].rolling(21).mean().shift(1)
    return df

def get_entry_signal(df, i, poc):
    price = df['close'].iloc[i]
    ema = df['ema_200'].iloc[i]
    hma = df['hma_25'].iloc[i]
    stc = df['stc'].iloc[i]

    prev = df.iloc[i-1] if i > 0 else df.iloc[i]
    is_bull_macro = price > ema
    is_bear_macro = price < ema
    is_above_poc = price > poc
    is_below_poc = price < poc

    hma_slope_up = hma > prev['hma_25']
    hma_slope_down = hma < prev['hma_25']
    is_above_hma = price > hma
    is_below_hma = price < hma

    stc_cross_up = stc >= PARAMS['STC_LOWER'] and prev['stc'] < PARAMS['STC_LOWER']
    stc_cross_down = stc <= PARAMS['STC_UPPER'] and prev['stc'] > PARAMS['STC_UPPER']

    buy = is_bull_macro and is_above_poc and hma_slope_up and is_above_hma and stc_cross_up
    sell = is_bear_macro and is_below_poc and hma_slope_down and is_below_hma and stc_cross_down

    if buy:
        return 'buy', price, stc, hma, ema
    if sell:
        return 'sell', price, stc, hma, ema
    return None, None, None, None, None

def compute_sl_tp(df, i, side, price):
    if side == 'buy':
        last_lows = df['low'].iloc[max(0, i-PARAMS['SL_LOOKBACK']-1):i].min()
        sl = max(last_lows, price * (1 - PARAMS['MAX_SL_PCT']))
        tp = price + (price - sl) * PARAMS['RR_RATIO']
    else:
        last_highs = df['high'].iloc[max(0, i-PARAMS['SL_LOOKBACK']-1):i].max()
        sl = min(last_highs, price * (1 + PARAMS['MAX_SL_PCT']))
        tp = price - (sl - price) * PARAMS['RR_RATIO']
    return sl, tp
