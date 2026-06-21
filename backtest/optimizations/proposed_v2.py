import numpy as np
import pandas as pd
from backtest.strategies.abracadabra_v1 import (
    compute_indicators, calculate_poc, detect_order_blocks,
    PARAMS as V1_PARAMS
)

# --- Scenario definitions ---
SCENARIOS = {
    'V2_Propuesto': {
        'RISK_PERCENT': 0.02,
        'LEVERAGE': 5.0,
        'MAX_SL_PCT': 0.035,
        'MAX_OPEN_POSITIONS': 3,
        'RR_RATIO': 2.0,
        'SL_LOOKBACK': 10,
        'BE_TRIGGER_PCT': 0.015,
        'BE_OFFSET_PCT': 0.005,
        'TRAILING_DIST_PCT': 0.012,
        'STC_LOWER': 30,
        'STC_UPPER': 80,
        'min_volume_ratio': 1.5,
        'use_atr_sl': False,
        'cooldown_hours_loss': 4,
    },
    'V4_Agresivo_Moderado': {
        'RISK_PERCENT': 0.03,
        'LEVERAGE': 5.0,
        'MAX_SL_PCT': 0.025,
        'MAX_OPEN_POSITIONS': 4,
        'RR_RATIO': 2.0,
        'SL_LOOKBACK': 10,
        'BE_TRIGGER_PCT': 0.015,
        'BE_OFFSET_PCT': 0.005,
        'TRAILING_DIST_PCT': 0.012,
        'STC_LOWER': 30,
        'STC_UPPER': 80,
        'min_volume_ratio': 1.3,
        'use_atr_sl': False,
        'cooldown_hours_loss': 3,
    },
    'V7_ATR_Dinamico': {
        'RISK_PERCENT': 0.02,
        'LEVERAGE': 5.0,
        'MAX_SL_PCT': 0.035,
        'MAX_OPEN_POSITIONS': 3,
        'RR_RATIO': 2.0,
        'SL_LOOKBACK': 10,
        'BE_TRIGGER_PCT': 0.015,
        'BE_OFFSET_PCT': 0.005,
        'TRAILING_DIST_PCT': 0.012,
        'STC_LOWER': 30,
        'STC_UPPER': 80,
        'min_volume_ratio': 1.5,
        'use_atr_sl': True,
        'atr_multiplier': 1.5,
        'atr_period': 14,
        'cooldown_hours_loss': 4,
    },
}

def compute_atr(df, period=14):
    high, low, close = df['high'], df['low'], df['close']
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs()
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()

def get_entry_signal_v2(df, i, poc, params):
    price = df['close'].iloc[i]
    ema = df['ema_200'].iloc[i]
    hma = df['hma_25'].iloc[i]
    stc = df['stc'].iloc[i]
    vol_ratio = df['volume_ratio'].iloc[i]

    if vol_ratio < params['min_volume_ratio']:
        return None, None, None, None, None

    prev = df.iloc[i-1] if i > 0 else df.iloc[i]
    is_bull_macro = price > ema
    is_bear_macro = price < ema
    is_above_poc = price > poc
    is_below_poc = price < poc
    hma_slope_up = hma > prev['hma_25']
    hma_slope_down = hma < prev['hma_25']
    is_above_hma = price > hma
    is_below_hma = price < hma
    stc_cross_up = stc >= params['STC_LOWER'] and prev['stc'] < params['STC_LOWER']
    stc_cross_down = stc <= params['STC_UPPER'] and prev['stc'] > params['STC_UPPER']

    buy = is_bull_macro and is_above_poc and hma_slope_up and is_above_hma and stc_cross_up
    sell = is_bear_macro and is_below_poc and hma_slope_down and is_below_hma and stc_cross_down

    if buy:
        return 'buy', price, stc, hma, ema
    if sell:
        return 'sell', price, stc, hma, ema
    return None, None, None, None, None

def compute_sl_tp_v2(df, i, side, price, params):
    min_dist = params.get('MIN_SL_DIST_PCT', 0.003)
    if params['use_atr_sl']:
        atr = compute_atr(df, params['atr_period'])
        atr_val = atr.iloc[i]
        sl_dist = atr_val * params['atr_multiplier']
        if side == 'buy':
            sl = price - sl_dist
            sl = max(sl, price * (1 - params['MAX_SL_PCT']))
            sl = min(sl, price * (1 - min_dist))
        else:
            sl = price + sl_dist
            sl = min(sl, price * (1 + params['MAX_SL_PCT']))
            sl = max(sl, price * (1 + min_dist))
        tp = price + (price - sl) * params['RR_RATIO'] if side == 'buy' else price - (sl - price) * params['RR_RATIO']
    else:
        if side == 'buy':
            last_lows = df['low'].iloc[max(0, i-params['SL_LOOKBACK']-1):i].min()
            sl = max(last_lows, price * (1 - params['MAX_SL_PCT']))
            sl = min(sl, price * (1 - min_dist))
        else:
            last_highs = df['high'].iloc[max(0, i-params['SL_LOOKBACK']-1):i].max()
            sl = min(last_highs, price * (1 + params['MAX_SL_PCT']))
            sl = max(sl, price * (1 + min_dist))
        tp = price + (price - sl) * params['RR_RATIO'] if side == 'buy' else price - (sl - price) * params['RR_RATIO']
    return sl, tp


# ==========================================================
# V5 — Filtros de régimen de mercado (ADX + ATR% + BB)
# ==========================================================

def compute_adx(df, period=14):
    high, low, close = df['high'], df['low'], df['close']
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs()
    ], axis=1).max(axis=1)
    atr_smooth = tr.rolling(period).mean()

    up_move = high - high.shift(1)
    down_move = low.shift(1) - low
    pos_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0), index=df.index)
    neg_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0), index=df.index)

    pos_di = 100 * pos_dm.ewm(span=period, adjust=False).mean() / atr_smooth
    neg_di = 100 * neg_dm.ewm(span=period, adjust=False).mean() / atr_smooth

    dx = 100 * (pos_di - neg_di).abs() / (pos_di + neg_di).replace(0, np.nan)
    adx = dx.ewm(span=period, adjust=False).mean()
    return adx

def compute_bollinger_bandwidth(df, period=20, std_mult=2.0):
    sma = df['close'].rolling(period).mean()
    std = df['close'].rolling(period).std()
    upper = sma + std_mult * std
    lower = sma - std_mult * std
    bandwidth = (upper - lower) / sma.replace(0, np.nan)
    return bandwidth

def compute_atr_percentile(df, period=14, lookback=50):
    high, low, close = df['high'], df['low'], df['close']
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs()
    ], axis=1).max(axis=1)
    atr = tr.rolling(period).mean()
    pctile = atr.rolling(lookback).apply(
        lambda x: (x.iloc[-1] >= x).sum() / len(x) * 100, raw=False
    )
    return pctile

def compute_regime_indicators(df):
    df = df.copy()
    df['adx_14'] = compute_adx(df, 14)
    df['bb_bandwidth_20'] = compute_bollinger_bandwidth(df, 20)
    df['atr_pctile_50'] = compute_atr_percentile(df, 14, 50)
    return df

def get_entry_signal_v5(df, i, poc, params):
    price = df['close'].iloc[i]
    ema = df['ema_200'].iloc[i]
    hma = df['hma_25'].iloc[i]
    stc = df['stc'].iloc[i]
    vol_ratio = df['volume_ratio'].iloc[i]

    # Market regime filters
    adx = df['adx_14'].iloc[i] if 'adx_14' in df else 0
    atr_pct = df['atr_pctile_50'].iloc[i] if 'atr_pctile_50' in df else 0
    bb_bw = df['bb_bandwidth_20'].iloc[i] if 'bb_bandwidth_20' in df else 0

    # Block: high volatility + strong trend — mean-reversion fails here
    if adx > 25 and atr_pct > 70:
        return None, None, None, None, None

    # Volume filter
    if vol_ratio < params['min_volume_ratio']:
        return None, None, None, None, None

    prev = df.iloc[i-1] if i > 0 else df.iloc[i]
    is_bull_macro = price > ema
    is_bear_macro = price < ema
    is_above_poc = price > poc
    is_below_poc = price < poc
    hma_slope_up = hma > prev['hma_25']
    hma_slope_down = hma < prev['hma_25']
    is_above_hma = price > hma
    is_below_hma = price < hma
    stc_cross_up = stc >= params['STC_LOWER'] and prev['stc'] < params['STC_LOWER']
    stc_cross_down = stc <= params['STC_UPPER'] and prev['stc'] > params['STC_UPPER']

    buy = is_bull_macro and is_above_poc and hma_slope_up and is_above_hma and stc_cross_up
    sell = is_bear_macro and is_below_poc and hma_slope_down and is_below_hma and stc_cross_down

    if buy:
        return 'buy', price, stc, hma, ema
    if sell:
        return 'sell', price, stc, hma, ema
    return None, None, None, None, None


# --- V5 Scenario Definition ---
SCENARIOS['V5_RegimenMercado'] = {
    'RISK_PERCENT': 0.02,
    'LEVERAGE': 5.0,
    'MAX_SL_PCT': 0.035,
    'MAX_OPEN_POSITIONS': 3,
    'RR_RATIO': 2.0,
    'SL_LOOKBACK': 10,
    'BE_TRIGGER_PCT': 0.015,
    'BE_OFFSET_PCT': 0.005,
    'TRAILING_DIST_PCT': 0.012,
    'STC_LOWER': 30,
    'STC_UPPER': 80,
    'min_volume_ratio': 1.5,
    'use_atr_sl': False,
    'cooldown_hours_loss': 4,
    'use_regime_filter': True,
    'adx_threshold': 25,
    'atr_pctile_threshold': 70,
    'use_stc_depth': False,
    'use_candle_position': False,
    'use_price_structure': False,
    'use_hma_slope_mag': False,
}

# ==========================================================
# V5b — Momento + Posicion de vela + Estructura de precio
# ==========================================================

def get_entry_signal_v5b(df, i, poc, params):
    price = df['close'].iloc[i]
    ema = df['ema_200'].iloc[i]
    hma = df['hma_25'].iloc[i]
    stc = df['stc'].iloc[i]
    vol_ratio = df['volume_ratio'].iloc[i]
    o, h, l, c = df['open'].iloc[i], df['high'].iloc[i], df['low'].iloc[i], df['close'].iloc[i]

    if vol_ratio < params['min_volume_ratio']:
        return None, None, None, None, None

    # Regime filter
    if params.get('use_regime_filter', False):
        adx = df['adx_14'].iloc[i] if 'adx_14' in df else 0
        atr_pct = df['atr_pctile_50'].iloc[i] if 'atr_pctile_50' in df else 0
        if adx > params.get('adx_threshold', 25) and atr_pct > params.get('atr_pctile_threshold', 70):
            return None, None, None, None, None

    # STC Depth filter
    if params.get('use_stc_depth', False):
        stc_vals = df['stc'].iloc[max(0,i-3):i+1]
        if stc >= params['STC_LOWER']:
            if not (stc_vals < 20).any():
                return None, None, None, None, None
        elif stc <= params['STC_UPPER']:
            if not (stc_vals > 90).any():
                return None, None, None, None, None

    prev = df.iloc[i-1] if i > 0 else df.iloc[i]
    is_bull_macro = price > ema
    is_bear_macro = price < ema
    is_above_poc = price > poc
    is_below_poc = price < poc

    hma_slope_up = hma > prev['hma_25']
    hma_slope_down = hma < prev['hma_25']
    is_above_hma = price > hma
    is_below_hma = price < hma
    stc_cross_up = stc >= params['STC_LOWER'] and prev['stc'] < params['STC_LOWER']
    stc_cross_down = stc <= params['STC_UPPER'] and prev['stc'] > params['STC_UPPER']

    buy = is_bull_macro and is_above_poc and hma_slope_up and is_above_hma and stc_cross_up
    sell = is_bear_macro and is_below_poc and hma_slope_down and is_below_hma and stc_cross_down

    if not buy and not sell:
        return None, None, None, None, None

    # Candle position filter (momentum)
    if params.get('use_candle_position', False):
        candle_range = h - l
        if candle_range > 0:
            close_pos = (c - l) / candle_range
            if buy and close_pos < 0.6:
                return None, None, None, None, None
            if sell and close_pos > 0.4:
                return None, None, None, None, None

    # Price structure filter
    if params.get('use_price_structure', False):
        if buy:
            recent_high = df['high'].iloc[max(0,i-3):i].max()
            if price < recent_high * 1.001:
                return None, None, None, None, None
        if sell:
            recent_low = df['low'].iloc[max(0,i-3):i].min()
            if price > recent_low * 0.999:
                return None, None, None, None, None

    # HMA slope magnitude filter
    if params.get('use_hma_slope_mag', False):
        slope_pct = abs(hma - prev['hma_25']) / prev['hma_25']
        if slope_pct < 0.0005:
            return None, None, None, None, None

    if buy:
        return 'buy', price, stc, hma, ema
    if sell:
        return 'sell', price, stc, hma, ema
    return None, None, None, None, None


# --- V5b: Momentum + Candle position ---
SCENARIOS['V5b_MomentumStructure'] = dict(SCENARIOS['V5_RegimenMercado'])
SCENARIOS['V5b_MomentumStructure'].update({
    'use_regime_filter': True,
    'use_candle_position': True,
    'use_price_structure': True,
    'use_hma_slope_mag': True,
    'use_stc_depth': False,
})

# --- V5c: STC Depth (deeper oversold/overbought) ---
SCENARIOS['V5c_STC_Depth'] = dict(SCENARIOS['V5_RegimenMercado'])
SCENARIOS['V5c_STC_Depth'].update({
    'use_regime_filter': True,
    'use_candle_position': False,
    'use_price_structure': False,
    'use_hma_slope_mag': False,
    'use_stc_depth': True,
})

# --- V5d: All combined ---
SCENARIOS['V5d_Combined'] = dict(SCENARIOS['V5_RegimenMercado'])
SCENARIOS['V5d_Combined'].update({
    'use_regime_filter': True,
    'use_candle_position': True,
    'use_price_structure': True,
    'use_hma_slope_mag': True,
    'use_stc_depth': True,
})

# ==========================================================
# V5e — Tendencia diaria (resample 2h -> daily)
# ==========================================================

def compute_daily_trend(df_2h):
    df = df_2h.copy()
    df['date'] = df['timestamp'].dt.date
    daily = df.groupby('date').agg({
        'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'
    }).reset_index(drop=True)
    daily['ema50'] = daily['close'].ewm(span=50, adjust=False).mean()
    daily_map = {}
    for i, row in daily.iterrows():
        daily_map[row['close']] = row['ema50']
    # Map each 2h candle to its daily EMA50 value
    df_by_date = df.groupby('date')
    ema_mapped = []
    for date_val, group in df_by_date:
        daily_row = daily[daily['close'] == daily.loc[daily.index[0], 'close']]
        ema_val = daily_row['ema50'].iloc[0]
        ema_mapped.append(pd.Series([ema_val] * len(group), index=group.index))
    if ema_mapped:
        df['daily_ema50'] = pd.concat(ema_mapped)
    else:
        df['daily_ema50'] = np.nan
    df['daily_ema50'] = df['daily_ema50'].ffill()
    return df['daily_ema50']

# --- V5e: Daily trend filter ---
def get_entry_signal_v5e(df, i, poc, params):
    price = df['close'].iloc[i]
    ema = df['ema_200'].iloc[i]
    hma = df['hma_25'].iloc[i]
    stc = df['stc'].iloc[i]
    vol_ratio = df['volume_ratio'].iloc[i]
    daily_ema50 = df['daily_ema50'].iloc[i] if 'daily_ema50' in df else ema

    if vol_ratio < params['min_volume_ratio']:
        return None, None, None, None, None

    # Trend alignment: daily EMA50 must agree
    bull_daily = price > daily_ema50
    bear_daily = price < daily_ema50

    prev = df.iloc[i-1] if i > 0 else df.iloc[i]
    is_bull_macro = price > ema
    is_bear_macro = price < ema
    is_above_poc = price > poc
    is_below_poc = price < poc
    hma_slope_up = hma > prev['hma_25']
    hma_slope_down = hma < prev['hma_25']
    is_above_hma = price > hma
    is_below_hma = price < hma
    stc_cross_up = stc >= params['STC_LOWER'] and prev['stc'] < params['STC_LOWER']
    stc_cross_down = stc <= params['STC_UPPER'] and prev['stc'] > params['STC_UPPER']

    buy = is_bull_macro and is_above_poc and hma_slope_up and is_above_hma and stc_cross_up and bull_daily
    sell = is_bear_macro and is_below_poc and hma_slope_down and is_below_hma and stc_cross_down and bear_daily

    if buy:
        return 'buy', price, stc, hma, ema
    if sell:
        return 'sell', price, stc, hma, ema
    return None, None, None, None, None

SCENARIOS['V5e_DailyTrend'] = {
    'RISK_PERCENT': 0.02,
    'LEVERAGE': 5.0,
    'MAX_SL_PCT': 0.035,
    'MAX_OPEN_POSITIONS': 3,
    'RR_RATIO': 2.0,
    'SL_LOOKBACK': 10,
    'BE_TRIGGER_PCT': 0.015,
    'BE_OFFSET_PCT': 0.005,
    'TRAILING_DIST_PCT': 0.012,
    'STC_LOWER': 30,
    'STC_UPPER': 80,
    'min_volume_ratio': 1.5,
    'use_atr_sl': False,
    'cooldown_hours_loss': 4,
    'use_daily_trend': True,
}

# ==========================================================
# V5f — STC Level filter (momentum confirmado)
# ==========================================================

def get_entry_signal_v5f(df, i, poc, params):
    price = df['close'].iloc[i]
    ema = df['ema_200'].iloc[i]
    hma = df['hma_25'].iloc[i]
    stc = df['stc'].iloc[i]
    vol_ratio = df['volume_ratio'].iloc[i]

    if vol_ratio < params['min_volume_ratio']:
        return None, None, None, None, None

    prev = df.iloc[i-1] if i > 0 else df.iloc[i]
    is_bull_macro = price > ema
    is_bear_macro = price < ema
    is_above_poc = price > poc
    is_below_poc = price < poc
    hma_slope_up = hma > prev['hma_25']
    hma_slope_down = hma < prev['hma_25']
    is_above_hma = price > hma
    is_below_hma = price < hma
    stc_cross_up = stc >= params['STC_LOWER'] and prev['stc'] < params['STC_LOWER']
    stc_cross_down = stc <= params['STC_UPPER'] and prev['stc'] > params['STC_UPPER']

    buy = is_bull_macro and is_above_poc and hma_slope_up and is_above_hma and stc_cross_up
    sell = is_bear_macro and is_below_poc and hma_slope_down and is_below_hma and stc_cross_down

    if not buy and not sell:
        return None, None, None, None, None

    # STC level filter: require more extreme STC values for entry
    stc_min = df['stc'].iloc[max(0,i-3):i+1].min()
    stc_max = df['stc'].iloc[max(0,i-3):i+1].max()
    if buy and stc_min > 25:
        return None, None, None, None, None
    if sell and stc_max < 75:
        return None, None, None, None, None

    if buy:
        return 'buy', price, stc, hma, ema
    if sell:
        return 'sell', price, stc, hma, ema
    return None, None, None, None, None

SCENARIOS['V5f_STC_Level'] = dict(SCENARIOS['V5e_DailyTrend'])
SCENARIOS['V5f_STC_Level'].update({'use_daily_trend': False})

# ==========================================================
# V6 — Estrategia direccional (Trend Following)
# ==========================================================

def calculate_hma_50(series):
    """HMA(50) for trend following"""
    def _wma(s, n):
        weights = np.arange(1, n + 1, dtype=float)
        return s.rolling(n).apply(lambda x: np.dot(x, weights) / weights.sum(), raw=True)
    half, sqrtn = 25, int(np.sqrt(50))
    raw = 2 * _wma(series, half) - _wma(series, 50)
    return _wma(raw, sqrtn)

def compute_trend_indicators(df):
    df = df.copy()
    df['hma_50'] = calculate_hma_50(df['close'])
    df['daily_ema50'] = compute_daily_trend(df)
    return df

def get_entry_signal_v6(df, i, poc, params):
    price = df['close'].iloc[i]
    ema = df['ema_200'].iloc[i]
    hma = df['hma_25'].iloc[i]
    vol_ratio = df['volume_ratio'].iloc[i]

    if vol_ratio < params['min_volume_ratio']:
        return None, None, None, None, None

    prev = df.iloc[i-1] if i > 0 else df.iloc[i]

    # Only entry: HMA(25) crosses EMA(200) with slope momentum
    hma_above_ema = hma > ema
    hma_below_ema = hma < ema
    price_above_hma = price > hma
    price_below_hma = price < hma

    hma_slope = (hma - prev['hma_25']) / prev['hma_25'] if prev['hma_25'] != 0 else 0
    hma_rising = hma_slope > params.get('hma_slope_min', 0.0005)
    hma_falling = hma_slope < -params.get('hma_slope_min', 0.0005)

    hma_cross_up = not (prev['hma_25'] > prev['ema_200']) and hma_above_ema
    hma_cross_down = not (prev['hma_25'] < prev['ema_200']) and hma_below_ema

    buy = hma_cross_up and hma_rising and price_above_hma
    sell = hma_cross_down and hma_falling and price_below_hma

    if buy:
        return 'buy', price, 50, hma, ema
    if sell:
        return 'sell', price, 50, hma, ema
    return None, None, None, None, None

def compute_sl_tp_trend(df, i, side, price, params):
    atr = compute_atr(df, 14)
    atr_val = atr.iloc[i]
    sl_dist = atr_val * params.get('atr_sl_trend', 2.5)
    if side == 'buy':
        sl = price - sl_dist
        sl = max(sl, price * (1 - params['MAX_SL_PCT']))
        tp = None  # trailing only, no fixed TP
    else:
        sl = price + sl_dist
        sl = min(sl, price * (1 + params['MAX_SL_PCT']))
        tp = None
    return sl, tp

SCENARIOS['V6_TrendFollowing'] = {
    'RISK_PERCENT': 0.02,
    'LEVERAGE': 5.0,
    'MAX_SL_PCT': 0.05,
    'MAX_OPEN_POSITIONS': 3,
    'RR_RATIO': 3.0,
    'SL_LOOKBACK': 10,
    'BE_TRIGGER_PCT': 0.03,
    'BE_OFFSET_PCT': 0.01,
    'TRAILING_DIST_PCT': 0.025,
    'STC_LOWER': 30,
    'STC_UPPER': 80,
    'min_volume_ratio': 1.3,
    'use_atr_sl': True,
    'atr_period': 14,
    'atr_multiplier': 2.5,
    'atr_sl_trend': 2.5,
    'cooldown_hours_loss': 6,
    'hma_slope_min': 0.001,
    'use_trend': True,
    'timeout_hours': 48,
}

# ==========================================================
# V6b — HMA cross de HMA(25)/HMA(50) como gatillo
# ==========================================================

def get_entry_signal_v6b(df, i, poc, params):
    price = df['close'].iloc[i]
    hma = df['hma_25'].iloc[i]
    hma_50 = df['hma_50'].iloc[i]
    ema = df['ema_200'].iloc[i]
    vol_ratio = df['volume_ratio'].iloc[i]

    if vol_ratio < params['min_volume_ratio']:
        return None, None, None, None, None

    prev = df.iloc[i-1] if i > 0 else df.iloc[i]

    # HMA(25) cross of HMA(50)
    hma_cross_up = not (prev['hma_25'] > prev['hma_50']) and hma > hma_50
    hma_cross_down = not (prev['hma_25'] < prev['hma_50']) and hma < hma_50

    # HMA(25) cross of EMA(200) — macro alignment
    ema_cross_up = not (prev['hma_25'] > prev['ema_200']) and hma > ema
    ema_cross_down = not (prev['hma_25'] < prev['ema_200']) and hma < ema

    # Entry: require BOTH crosses to agree (direction)
    buy = hma_cross_up and ema_cross_up
    sell = hma_cross_down and ema_cross_down

    if buy:
        return 'buy', price, 50, hma, ema
    if sell:
        return 'sell', price, 50, hma, ema
    return None, None, None, None, None

SCENARIOS['V6b_HMACross'] = dict(SCENARIOS['V6_TrendFollowing'])
SCENARIOS['V6b_HMACross'].update({'use_trend': True, 'hma_slope_min': 0.0})

# ==========================================================
# V8-V12: Optimizaciones enfocadas en mejorar WR
# ==========================================================

# V8: STC thresholds 25/75 (más conservador)
SCENARIOS['V8_STC_Tight'] = dict(SCENARIOS['V2_Propuesto'])
SCENARIOS['V8_STC_Tight'].update({
    'STC_LOWER': 25,
    'STC_UPPER': 75,
    'min_volume_ratio': 1.5,
})

# V9: ATR SL amplio (3.5x) + RR bajo (1.5) — menos stops ajustados
SCENARIOS['V9_WideSL_LowRR'] = dict(SCENARIOS['V2_Propuesto'])
SCENARIOS['V9_WideSL_LowRR'].update({
    'use_atr_sl': True,
    'atr_period': 14,
    'atr_multiplier': 3.5,
    'RR_RATIO': 1.5,
    'MAX_SL_PCT': 0.05,
    'min_volume_ratio': 1.5,
})

# V10: HMA trend filter — solo long en uptrend, solo short en downtrend
def get_entry_signal_v10(df, i, poc, params):
    price = df['close'].iloc[i]
    ema = df['ema_200'].iloc[i]
    hma = df['hma_25'].iloc[i]
    stc = df['stc'].iloc[i]
    vol_ratio = df['volume_ratio'].iloc[i]

    if vol_ratio < params['min_volume_ratio']:
        return None, None, None, None, None

    prev = df.iloc[i-1] if i > 0 else df.iloc[i]
    hma_slope_up = hma > prev['hma_25']
    hma_slope_down = hma < prev['hma_25']

    # HMA trend direction filter — only enter WITH the trend
    hma_uptrend = hma > ema and hma_slope_up  # HMA above EMA200 and rising
    hma_downtrend = hma < ema and hma_slope_down

    is_above_poc = price > poc
    is_below_poc = price < poc
    is_above_hma = price > hma
    is_below_hma = price < hma
    stc_cross_up = stc >= params['STC_LOWER'] and prev['stc'] < params['STC_LOWER']
    stc_cross_down = stc <= params['STC_UPPER'] and prev['stc'] > params['STC_UPPER']

    buy = hma_uptrend and is_above_poc and is_above_hma and stc_cross_up
    sell = hma_downtrend and is_below_poc and is_below_hma and stc_cross_down

    if buy:
        return 'buy', price, stc, hma, ema
    if sell:
        return 'sell', price, stc, hma, ema
    return None, None, None, None, None

SCENARIOS['V10_HMA_Trend_Filter'] = {
    'RISK_PERCENT': 0.02,
    'LEVERAGE': 5.0,
    'MAX_SL_PCT': 0.035,
    'MAX_OPEN_POSITIONS': 3,
    'RR_RATIO': 2.0,
    'SL_LOOKBACK': 10,
    'BE_TRIGGER_PCT': 0.015,
    'BE_OFFSET_PCT': 0.005,
    'TRAILING_DIST_PCT': 0.012,
    'STC_LOWER': 30,
    'STC_UPPER': 80,
    'min_volume_ratio': 1.5,
    'use_atr_sl': False,
    'atr_period': 14,
    'atr_multiplier': 3.0,
    'cooldown_hours_loss': 4,
    'use_v10_filter': True,
}

# V11: V10 + ATR SL (3.0x)
SCENARIOS['V11_HMA_Trend_ATRSL'] = dict(SCENARIOS['V10_HMA_Trend_Filter'])
SCENARIOS['V11_HMA_Trend_ATRSL'].update({
    'use_atr_sl': True,
    'atr_multiplier': 3.0,
    'MAX_SL_PCT': 0.05,
    'RR_RATIO': 2.0,
})

# V12: Best Effort — tight thresholds + ATR SL + trend filter + volume
SCENARIOS['V12_Best_Effort'] = dict(SCENARIOS['V10_HMA_Trend_Filter'])
SCENARIOS['V12_Best_Effort'].update({
    'STC_LOWER': 25,
    'STC_UPPER': 75,
    'use_atr_sl': True,
    'atr_multiplier': 3.0,
    'RR_RATIO': 1.5,
    'MAX_SL_PCT': 0.05,
    'min_volume_ratio': 1.5,
    'cooldown_hours_loss': 6,
})

# V13: V5 regime filter + STC thresholds 25/75
SCENARIOS['V13_Regimen_Tight'] = dict(SCENARIOS['V5_RegimenMercado'])
SCENARIOS['V13_Regimen_Tight'].update({
    'STC_LOWER': 25,
    'STC_UPPER': 75,
})

# V14: V5 regime filter + STC depth (<20/>90 before cross)
SCENARIOS['V14_Regimen_Depth'] = dict(SCENARIOS['V5_RegimenMercado'])
SCENARIOS['V14_Regimen_Depth'].update({
    'use_stc_depth': True,
})

# V15: V5 + ATR SL (2.5x) instead of lookback SL
SCENARIOS['V15_Regimen_ATRSL'] = dict(SCENARIOS['V5_RegimenMercado'])
SCENARIOS['V15_Regimen_ATRSL'].update({
    'use_atr_sl': True,
    'atr_period': 14,
    'atr_multiplier': 2.5,
    'MAX_SL_PCT': 0.05,
    'RR_RATIO': 2.0,
})

# V16: Solo STC cross + volume (sin macro filtros) — pureza
SCENARIOS['V16_STC_Pure'] = dict(SCENARIOS['V2_Propuesto'])
SCENARIOS['V16_STC_Pure'].update({
    'min_volume_ratio': 1.2,
})

# ==========================================================
# V17-V20: Estrategias enfocadas en WR >70%
# ==========================================================

def compute_rsi(df, period=14):
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def compute_macd(df):
    ema12 = df['close'].ewm(span=12, adjust=False).mean()
    ema26 = df['close'].ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    return macd, signal

# V17: Maximum Confluence Strategy — ~12 filters for highest WR
def get_entry_signal_v17(df, i, poc, params):
    price = df['close'].iloc[i]
    o, h, l, c = df['open'].iloc[i], df['high'].iloc[i], df['low'].iloc[i], df['close'].iloc[i]
    ema = df['ema_200'].iloc[i]
    hma = df['hma_25'].iloc[i]
    stc = df['stc'].iloc[i]
    vol_ratio = df['volume_ratio'].iloc[i]

    # --- FILTER 1: Volume ---
    if vol_ratio < params['min_volume_ratio']:
        return None, None, None, None, None

    prev = df.iloc[i-1] if i > 0 else df.iloc[i]

    # --- FILTER 2: Macro trend — price above/below EMA200 ---
    is_bull = price > ema
    is_bear = price < ema

    # --- FILTER 3: HMA trend alignment ---
    hma_align_bull = hma > ema and hma > prev['hma_25']  # HMA above EMA and rising
    hma_align_bear = hma < ema and hma < prev['hma_25']

    # --- FILTER 4: STC oversold/overbought zone before cross ---
    prev_stc = prev['stc']
    stc_cross_up = stc >= params['STC_LOWER'] and prev_stc < params['STC_LOWER']
    stc_cross_down = stc <= params['STC_UPPER'] and prev_stc > params['STC_UPPER']
    stc_was_extreme_up = df['stc'].iloc[max(0,i-5):i+1].min() < params.get('stc_depth_lower', 25)
    stc_was_extreme_down = df['stc'].iloc[max(0,i-5):i+1].max() > params.get('stc_depth_upper', 75)

    # --- FILTER 5: POC structure ---
    above_poc = price > poc
    below_poc = price < poc

    # --- FILTER 6: Candle confirmation ---
    candle_bull = c > o  # bullish candle
    candle_bear = c < o
    candle_range = h - l
    close_pos = (c - l) / candle_range if candle_range > 0 else 0.5
    candle_bull_strong = candle_bull and close_pos > 0.5
    candle_bear_strong = candle_bear and close_pos < 0.5

    # --- FILTER 7: RSI not overbought/oversold ---
    rsi_val = compute_rsi(df).iloc[i] if 'rsi' not in df else df['rsi'].iloc[i]
    rsi_recovery = rsi_val > 40  # not in deep oversold
    rsi_falling = rsi_val < 60  # not in deep overbought

    # --- FILTER 8: Regime filter (ADX < 25 for mean-reversion) ---
    if 'adx_14' in df:
        adx = df['adx_14'].iloc[i]
        if adx > params.get('adx_threshold', 25):
            return None, None, None, None, None

    # --- FILTER 9: ATR percentile < 70 (not extreme volatility) ---
    if 'atr_pctile_50' in df:
        atr_pct = df['atr_pctile_50'].iloc[i]
        if atr_pct > params.get('atr_pctile_threshold', 70):
            return None, None, None, None, None

    # --- All filters combined ---
    buy = (is_bull and hma_align_bull and stc_cross_up and stc_was_extreme_up
           and above_poc and candle_bull_strong and rsi_recovery)
    sell = (is_bear and hma_align_bear and stc_cross_down and stc_was_extreme_down
            and below_poc and candle_bear_strong and rsi_falling)

    if buy:
        return 'buy', price, stc, hma, ema
    if sell:
        return 'sell', price, stc, hma, ema
    return None, None, None, None, None

SCENARIOS['V17_HighWR_Confluence'] = {
    'RISK_PERCENT': 0.015,
    'LEVERAGE': 5.0,
    'MAX_SL_PCT': 0.03,
    'MAX_OPEN_POSITIONS': 2,
    'RR_RATIO': 2.0,
    'SL_LOOKBACK': 5,
    'BE_TRIGGER_PCT': 0.015,
    'BE_OFFSET_PCT': 0.005,
    'TRAILING_DIST_PCT': 0.01,
    'STC_LOWER': 30,
    'STC_UPPER': 80,
    'stc_depth_lower': 25,
    'stc_depth_upper': 75,
    'min_volume_ratio': 1.5,
    'use_atr_sl': False,
    'cooldown_hours_loss': 6,
    'use_v17_filter': True,
    'adx_threshold': 25,
    'atr_pctile_threshold': 70,
}

# ==========================================================
# V18: Bollinger Band Squeeze Breakout
# ==========================================================

def get_entry_signal_v18(df, i, poc, params):
    price = df['close'].iloc[i]
    vol_ratio = df['volume_ratio'].iloc[i]

    if vol_ratio < params['min_volume_ratio']:
        return None, None, None, None, None

    sma20 = df['close'].rolling(20).mean()
    std20 = df['close'].rolling(20).std()
    upper = sma20 + 2 * std20
    lower = sma20 - 2 * std20
    bandwidth = (upper - lower) / sma20

    # BB Squeeze: bandwidth < 20th percentile of last 100
    bb_squeeze = bandwidth.iloc[i] < bandwidth.iloc[max(0,i-100):i].quantile(0.2)

    if not bb_squeeze:
        return None, None, None, None, None

    prev = df.iloc[i-1] if i > 0 else df.iloc[i]
    hma = df['hma_25'].iloc[i] if 'hma_25' in df else price
    ema = df['ema_200'].iloc[i] if 'ema_200' in df else price

    # Breakout above upper band (long)
    buy = price > upper.iloc[i] and prev['close'] <= upper.iloc[i-1]
    # Breakout below lower band (short)
    sell = price < lower.iloc[i] and prev['close'] >= lower.iloc[i-1]

    if buy:
        return 'buy', price, 50, hma, ema
    if sell:
        return 'sell', price, 50, hma, ema
    return None, None, None, None, None

SCENARIOS['V18_BBand_Squeeze'] = {
    'RISK_PERCENT': 0.015,
    'LEVERAGE': 5.0,
    'MAX_SL_PCT': 0.03,
    'MAX_OPEN_POSITIONS': 2,
    'RR_RATIO': 1.5,
    'SL_LOOKBACK': 5,
    'BE_TRIGGER_PCT': 0.01,
    'BE_OFFSET_PCT': 0.005,
    'TRAILING_DIST_PCT': 0.01,
    'STC_LOWER': 30,
    'STC_UPPER': 80,
    'min_volume_ratio': 1.3,
    'use_atr_sl': False,
    'cooldown_hours_loss': 6,
    'use_v18_filter': True,
}

# ==========================================================
# V19: RSI Divergence + STC Confluence
# ==========================================================

def get_entry_signal_v19(df, i, poc, params):
    price = df['close'].iloc[i]
    vol_ratio = df['volume_ratio'].iloc[i]

    if vol_ratio < params['min_volume_ratio']:
        return None, None, None, None, None

    rsi = compute_rsi(df)
    stc = df['stc'].iloc[i]
    prev = df.iloc[i-1] if i > 0 else df.iloc[i]
    hma = df['hma_25'].iloc[i]
    ema = df['ema_200'].iloc[i]

    # Look for swing points in last 14 bars for divergence
    lookback = 14
    if i < lookback + 5:
        return None, None, None, None, None

    slice_prices = df['close'].iloc[i-lookback:i+1]
    slice_rsi = rsi.iloc[i-lookback:i+1]

    price_low = slice_prices.min()
    price_high = slice_prices.max()
    price_low_idx = slice_prices.idxmin()
    price_high_idx = slice_prices.idxmax()
    rsi_low = slice_rsi.min()
    rsi_high = slice_rsi.max()

    # Bullish divergence: price makes lower low, RSI makes higher low
    prev_slice_prices = df['close'].iloc[i-lookback-5:i+1-5]
    prev_slice_rsi = rsi.iloc[i-lookback-5:i+1-5]
    prev_price_low = prev_slice_prices.min()
    prev_rsi_low = prev_slice_rsi.min()

    bull_div = (price_low < prev_price_low * 0.995 and rsi_low > prev_rsi_low * 1.005
                and price_low == df['close'].iloc[i])  # current bar is the low

    # Bearish divergence: price makes higher high, RSI makes lower high
    prev_price_high = prev_slice_prices.max()
    prev_rsi_high = prev_slice_rsi.max()

    bear_div = (price_high > prev_price_high * 1.005 and rsi_high < prev_rsi_high * 0.995
                and price_high == df['close'].iloc[i])

    # STC must be in agreement zone
    stc_ok_buy = stc < 50 and stc > prev['stc']
    stc_ok_sell = stc > 50 and stc < prev['stc']

    # HMA trend filter
    above_hma = price > hma
    below_hma = price < hma

    buy = bull_div and stc_ok_buy and above_hma
    sell = bear_div and stc_ok_sell and below_hma

    if buy:
        return 'buy', price, stc, hma, ema
    if sell:
        return 'sell', price, stc, hma, ema
    return None, None, None, None, None

SCENARIOS['V19_RSI_Divergence'] = {
    'RISK_PERCENT': 0.015,
    'LEVERAGE': 5.0,
    'MAX_SL_PCT': 0.03,
    'MAX_OPEN_POSITIONS': 2,
    'RR_RATIO': 2.0,
    'SL_LOOKBACK': 10,
    'BE_TRIGGER_PCT': 0.015,
    'BE_OFFSET_PCT': 0.005,
    'TRAILING_DIST_PCT': 0.01,
    'STC_LOWER': 30,
    'STC_UPPER': 80,
    'min_volume_ratio': 1.2,
    'use_atr_sl': False,
    'cooldown_hours_loss': 6,
    'use_v19_filter': True,
}

# ==========================================================
# V20: Short-Term Momentum (RR 1:1, quick trades)
# ==========================================================

def get_entry_signal_v20(df, i, poc, params):
    price = df['close'].iloc[i]
    vol_ratio = df['volume_ratio'].iloc[i]

    if vol_ratio < params['min_volume_ratio']:
        return None, None, None, None, None

    prev = df.iloc[i-1] if i > 0 else df.iloc[i]
    ema5 = df['close'].ewm(span=5, adjust=False).mean().iloc[i]
    ema = df['ema_200'].iloc[i]
    hma = df['hma_25'].iloc[i]
    stc = df['stc'].iloc[i]

    # Short-term momentum: price above EMA5
    price_above_ema5 = price > ema5
    price_below_ema5 = price < ema5

    # STC crossing in direction of short-term trend
    stc_cross_up = stc >= 50 and prev['stc'] < 50
    stc_cross_down = stc <= 50 and prev['stc'] > 50

    # Volume confirmation
    vol_spike = vol_ratio > 1.5

    # Recent high/low breakout
    recent_high = df['high'].iloc[max(0,i-3):i].max()
    recent_low = df['low'].iloc[max(0,i-3):i].min()
    breaking_high = price > recent_high * 1.001
    breaking_low = price < recent_low * 0.999

    # Entry
    buy = price_above_ema5 and stc_cross_up and vol_spike and breaking_high
    sell = price_below_ema5 and stc_cross_down and vol_spike and breaking_low

    if buy:
        return 'buy', price, stc, hma, ema
    if sell:
        return 'sell', price, stc, hma, ema
    return None, None, None, None, None

SCENARIOS['V20_ST_Momentum'] = {
    'RISK_PERCENT': 0.01,
    'LEVERAGE': 5.0,
    'MAX_SL_PCT': 0.02,
    'MAX_OPEN_POSITIONS': 3,
    'RR_RATIO': 1.0,
    'SL_LOOKBACK': 5,
    'BE_TRIGGER_PCT': 0.01,
    'BE_OFFSET_PCT': 0.005,
    'TRAILING_DIST_PCT': 0.005,
    'STC_LOWER': 50,
    'STC_UPPER': 50,
    'min_volume_ratio': 1.3,
    'use_atr_sl': False,
    'cooldown_hours_loss': 4,
    'use_v20_filter': True,
}

# ==========================================================
# V21-V24: Combinaciones para escalar WR a 60-70%
# ==========================================================

# V21: V10 (HMA trend + STC cross) + Regime filter (ADX<25, ATRpct<70) + STC depth
def get_entry_signal_v21(df, i, poc, params):
    price = df['close'].iloc[i]
    ema = df['ema_200'].iloc[i]
    hma = df['hma_25'].iloc[i]
    stc = df['stc'].iloc[i]
    vol_ratio = df['volume_ratio'].iloc[i]

    if vol_ratio < params['min_volume_ratio']:
        return None, None, None, None, None

    # Regime filter
    if 'adx_14' in df:
        adx = df['adx_14'].iloc[i]
        if adx > params.get('adx_threshold', 25):
            return None, None, None, None, None
    if 'atr_pctile_50' in df:
        atr_pct = df['atr_pctile_50'].iloc[i]
        if atr_pct > params.get('atr_pctile_threshold', 70):
            return None, None, None, None, None

    prev = df.iloc[i-1] if i > 0 else df.iloc[i]

    # HMA trend direction filter (V10 core)
    hma_slope_up = hma > prev['hma_25']
    hma_slope_down = hma < prev['hma_25']
    hma_uptrend = hma > ema and hma_slope_up
    hma_downtrend = hma < ema and hma_slope_down

    # STC cross
    stc_cross_up = stc >= params['STC_LOWER'] and prev['stc'] < params['STC_LOWER']
    stc_cross_down = stc <= params['STC_UPPER'] and prev['stc'] > params['STC_UPPER']

    # STC depth: was below 20 before crossing up, or above 80 before crossing down
    stc_depth_up = df['stc'].iloc[max(0,i-5):i+1].min() < params.get('stc_depth_lower', 20)
    stc_depth_down = df['stc'].iloc[max(0,i-5):i+1].max() > params.get('stc_depth_upper', 80)

    # POC + HMA price position
    is_above_poc = price > poc
    is_below_poc = price < poc
    is_above_hma = price > hma
    is_below_hma = price < hma

    # Candle confirmation
    o, c = df['open'].iloc[i], df['close'].iloc[i]
    candle_bull = c > o
    candle_bear = c < o

    buy = (hma_uptrend and stc_cross_up and stc_depth_up
           and is_above_poc and is_above_hma and candle_bull)
    sell = (hma_downtrend and stc_cross_down and stc_depth_down
            and is_below_poc and is_below_hma and candle_bear)

    if buy:
        return 'buy', price, stc, hma, ema
    if sell:
        return 'sell', price, stc, hma, ema
    return None, None, None, None, None

SCENARIOS['V21_HMA_Regime_Depth'] = {
    'RISK_PERCENT': 0.02,
    'LEVERAGE': 5.0,
    'MAX_SL_PCT': 0.035,
    'MAX_OPEN_POSITIONS': 2,
    'RR_RATIO': 2.0,
    'SL_LOOKBACK': 5,
    'BE_TRIGGER_PCT': 0.015,
    'BE_OFFSET_PCT': 0.005,
    'TRAILING_DIST_PCT': 0.01,
    'STC_LOWER': 30,
    'STC_UPPER': 80,
    'stc_depth_lower': 20,
    'stc_depth_upper': 80,
    'min_volume_ratio': 1.5,
    'use_atr_sl': False,
    'cooldown_hours_loss': 6,
    'use_v21_filter': True,
    'adx_threshold': 25,
    'atr_pctile_threshold': 70,
}

# V22: V10 + BB Squeeze + MACD alignment
def get_entry_signal_v22(df, i, poc, params):
    price = df['close'].iloc[i]
    ema = df['ema_200'].iloc[i]
    hma = df['hma_25'].iloc[i]
    stc = df['stc'].iloc[i]
    vol_ratio = df['volume_ratio'].iloc[i]

    if vol_ratio < params['min_volume_ratio']:
        return None, None, None, None, None

    sma20 = df['close'].rolling(20).mean()
    std20 = df['close'].rolling(20).std()
    upper = sma20 + 2 * std20
    lower = sma20 - 2 * std20
    bandwidth = (upper - lower) / sma20

    # BB Squeeze check
    bb_squeeze = bandwidth.iloc[i] < bandwidth.iloc[max(0,i-100):i].quantile(0.3)
    if not bb_squeeze:
        return None, None, None, None, None

    prev = df.iloc[i-1] if i > 0 else df.iloc[i]

    hma_slope_up = hma > prev['hma_25']
    hma_slope_down = hma < prev['hma_25']
    hma_uptrend = hma > ema and hma_slope_up
    hma_downtrend = hma < ema and hma_slope_down

    stc_cross_up = stc >= params['STC_LOWER'] and prev['stc'] < params['STC_LOWER']
    stc_cross_down = stc <= params['STC_UPPER'] and prev['stc'] > params['STC_UPPER']

    # MACD confirmation
    macd, signal = compute_macd(df)
    macd_turning_up = macd.iloc[i] > signal.iloc[i] and macd.iloc[i] > macd.iloc[i-1]
    macd_turning_down = macd.iloc[i] < signal.iloc[i] and macd.iloc[i] < macd.iloc[i-1]

    is_above_poc = price > poc
    is_below_poc = price < poc
    is_above_hma = price > hma
    is_below_hma = price < hma

    buy = (hma_uptrend and stc_cross_up and macd_turning_up
           and is_above_poc and is_above_hma)
    sell = (hma_downtrend and stc_cross_down and macd_turning_down
            and is_below_poc and is_below_hma)

    if buy:
        return 'buy', price, stc, hma, ema
    if sell:
        return 'sell', price, stc, hma, ema
    return None, None, None, None, None

SCENARIOS['V22_Squeeze_MACD'] = {
    'RISK_PERCENT': 0.02,
    'LEVERAGE': 5.0,
    'MAX_SL_PCT': 0.035,
    'MAX_OPEN_POSITIONS': 2,
    'RR_RATIO': 2.0,
    'SL_LOOKBACK': 5,
    'BE_TRIGGER_PCT': 0.015,
    'BE_OFFSET_PCT': 0.005,
    'TRAILING_DIST_PCT': 0.01,
    'STC_LOWER': 30,
    'STC_UPPER': 80,
    'min_volume_ratio': 1.5,
    'use_atr_sl': False,
    'cooldown_hours_loss': 6,
    'use_v22_filter': True,
}

# V23: V10 + regime + RSI filter (enter only when RSI in "sweet spot" 35-65)
def get_entry_signal_v23(df, i, poc, params):
    price = df['close'].iloc[i]
    ema = df['ema_200'].iloc[i]
    hma = df['hma_25'].iloc[i]
    stc = df['stc'].iloc[i]
    vol_ratio = df['volume_ratio'].iloc[i]

    if vol_ratio < params['min_volume_ratio']:
        return None, None, None, None, None

    # Regime filter
    if 'adx_14' in df:
        if df['adx_14'].iloc[i] > params.get('adx_threshold', 20):
            return None, None, None, None, None
    if 'atr_pctile_50' in df:
        if df['atr_pctile_50'].iloc[i] > params.get('atr_pctile_threshold', 65):
            return None, None, None, None, None

    rsi = compute_rsi(df).iloc[i]
    prev = df.iloc[i-1] if i > 0 else df.iloc[i]

    hma_slope_up = hma > prev['hma_25']
    hma_slope_down = hma < prev['hma_25']
    hma_uptrend = hma > ema and hma_slope_up
    hma_downtrend = hma < ema and hma_slope_down

    stc_cross_up = stc >= params['STC_LOWER'] and prev['stc'] < params['STC_LOWER']
    stc_cross_down = stc <= params['STC_UPPER'] and prev['stc'] > params['STC_UPPER']

    # RSI sweet spot: not oversold (<30) nor overbought (>70)
    rsi_ok_buy = 30 < rsi < 65
    rsi_ok_sell = 35 < rsi < 70

    # STC depth confirmation
    stc_depth_up = df['stc'].iloc[max(0,i-4):i+1].min() < params.get('stc_depth_lower', 25)
    stc_depth_down = df['stc'].iloc[max(0,i-4):i+1].max() > params.get('stc_depth_upper', 75)

    is_above_poc = price > poc
    is_below_poc = price < poc
    is_above_hma = price > hma
    is_below_hma = price < hma

    o, c = df['open'].iloc[i], df['close'].iloc[i]
    candle_bull = c > o
    candle_bear = c < o

    buy = (hma_uptrend and stc_cross_up and stc_depth_up
           and rsi_ok_buy and is_above_poc and is_above_hma and candle_bull)
    sell = (hma_downtrend and stc_cross_down and stc_depth_down
            and rsi_ok_sell and is_below_poc and is_below_hma and candle_bear)

    if buy:
        return 'buy', price, stc, hma, ema
    if sell:
        return 'sell', price, stc, hma, ema
    return None, None, None, None, None

SCENARIOS['V23_HMA_RSI_SweetSpot'] = {
    'RISK_PERCENT': 0.02,
    'LEVERAGE': 5.0,
    'MAX_SL_PCT': 0.035,
    'MAX_OPEN_POSITIONS': 2,
    'RR_RATIO': 2.0,
    'SL_LOOKBACK': 5,
    'BE_TRIGGER_PCT': 0.015,
    'BE_OFFSET_PCT': 0.005,
    'TRAILING_DIST_PCT': 0.01,
    'STC_LOWER': 30,
    'STC_UPPER': 80,
    'stc_depth_lower': 25,
    'stc_depth_upper': 75,
    'min_volume_ratio': 1.5,
    'use_atr_sl': False,
    'cooldown_hours_loss': 6,
    'use_v23_filter': True,
    'adx_threshold': 20,
    'atr_pctile_threshold': 65,
}

# V24: V10 + tight thresholds + price within 1% of EMA20 (pullback to mean)
def get_entry_signal_v24(df, i, poc, params):
    price = df['close'].iloc[i]
    ema = df['ema_200'].iloc[i]
    hma = df['hma_25'].iloc[i]
    stc = df['stc'].iloc[i]
    vol_ratio = df['volume_ratio'].iloc[i]

    if vol_ratio < params['min_volume_ratio']:
        return None, None, None, None, None

    ema20 = df['close'].ewm(span=20, adjust=False).mean().iloc[i]
    prev = df.iloc[i-1] if i > 0 else df.iloc[i]

    hma_slope_up = hma > prev['hma_25']
    hma_slope_down = hma < prev['hma_25']
    hma_uptrend = hma > ema and hma_slope_up
    hma_downtrend = hma < ema and hma_slope_down

    stc_cross_up = stc >= params['STC_LOWER'] and prev['stc'] < params['STC_LOWER']
    stc_cross_down = stc <= params['STC_UPPER'] and prev['stc'] > params['STC_UPPER']

    # Price near EMA20 (pullback confirmation)
    near_ema20_up = abs(price - ema20) / ema20 < 0.015  # within 1.5%
    near_ema20_down = abs(price - ema20) / ema20 < 0.015

    is_above_poc = price > poc
    is_below_poc = price < poc
    is_above_hma = price > hma
    is_below_hma = price < hma

    o, c = df['open'].iloc[i], df['close'].iloc[i]
    candle_bull = c > o
    candle_bear = c < o

    buy = (hma_uptrend and stc_cross_up and near_ema20_up
           and is_above_poc and is_above_hma and candle_bull)
    sell = (hma_downtrend and stc_cross_down and near_ema20_down
            and is_below_poc and is_below_hma and candle_bear)

    if buy:
        return 'buy', price, stc, hma, ema
    if sell:
        return 'sell', price, stc, hma, ema
    return None, None, None, None, None

SCENARIOS['V24_Pullback_EMA20'] = {
    'RISK_PERCENT': 0.02,
    'LEVERAGE': 5.0,
    'MAX_SL_PCT': 0.03,
    'MAX_OPEN_POSITIONS': 2,
    'RR_RATIO': 2.0,
    'SL_LOOKBACK': 5,
    'BE_TRIGGER_PCT': 0.015,
    'BE_OFFSET_PCT': 0.005,
    'TRAILING_DIST_PCT': 0.01,
    'STC_LOWER': 30,
    'STC_UPPER': 80,
    'min_volume_ratio': 1.5,
    'use_atr_sl': False,
    'cooldown_hours_loss': 6,
    'use_v24_filter': True,
}
