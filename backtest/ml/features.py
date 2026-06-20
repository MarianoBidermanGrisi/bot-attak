import numpy as np
import pandas as pd

def compute_rsi(series, period=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def compute_macd(series):
    ema12 = series.ewm(span=12, adjust=False).mean()
    ema26 = series.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    return macd, signal, macd - signal

def rolling_zscore(series, window=50):
    mean = series.rolling(window).mean()
    std = series.rolling(window).std()
    return (series - mean) / std.replace(0, np.nan)

def build_features(df):
    df = df.copy()
    o, h, l, c, v = df['open'], df['high'], df['low'], df['close'], df['volume']

    # === Price returns ===
    for p in [1, 2, 3, 5, 10, 20]:
        df[f'ret_{p}'] = c.pct_change(p)
        df[f'ret_{p}_z'] = rolling_zscore(df[f'ret_{p}'], 50)

    # === Price action ===
    candle_range = h - l
    df['candle_range'] = candle_range
    df['close_pos'] = (c - l) / candle_range.replace(0, np.nan)
    df['high_low_pct'] = candle_range / l.replace(0, np.nan)
    df['body_pct'] = abs(c - o) / candle_range.replace(0, np.nan)
    df['upper_shadow'] = (h - o.abs().where(c > o, c)) / candle_range.replace(0, np.nan)

    # === Volume ===
    df['vol_ratio'] = v / v.rolling(21).mean().shift(1)
    df['vol_z'] = rolling_zscore(v, 50)
    df['vol_ma_ratio'] = v / v.rolling(5).mean()

    # === STC ===
    if 'stc' in df.columns:
        df['stc_val'] = df['stc']
        df['stc_slope'] = df['stc'].diff()
        df['stc_slope_z'] = rolling_zscore(df['stc_slope'], 30)
        df['stc_ma_dist'] = df['stc'] - df['stc'].rolling(10).mean()

    # === RSI ===
    rsi = compute_rsi(c, 14)
    df['rsi_14'] = rsi
    df['rsi_slope'] = rsi.diff()
    df['rsi_z'] = rolling_zscore(rsi, 50)

    # === MACD ===
    macd, signal, hist = compute_macd(c)
    df['macd'] = macd
    df['macd_signal'] = signal
    df['macd_hist'] = hist
    df['macd_hist_slope'] = hist.diff()
    df['macd_z'] = rolling_zscore(hist, 50)

    # === Moving Averages ===
    ema200 = df['ema_200'] if 'ema_200' in df else c.ewm(span=200, adjust=False).mean()
    hma25 = df['hma_25'] if 'hma_25' in df else c
    ema20 = c.ewm(span=20, adjust=False).mean()
    ema50 = c.ewm(span=50, adjust=False).mean()

    df['ema20'] = ema20
    df['ema50'] = ema50
    df['price_ema20'] = (c - ema20) / ema20
    df['price_ema50'] = (c - ema50) / ema50
    df['price_ema200'] = (c - ema200) / ema200
    df['price_hma25'] = (c - hma25) / hma25
    df['hma_ema200'] = (hma25 - ema200) / ema200
    df['hma_slope'] = hma25.diff() / hma25.shift(1).replace(0, np.nan)
    df['hma_slope_z'] = rolling_zscore(df['hma_slope'].fillna(0), 30)

    # === Bollinger Bands ===
    bb_sma = c.rolling(20).mean()
    bb_std = c.rolling(20).std()
    bb_upper = bb_sma + 2 * bb_std
    bb_lower = bb_sma - 2 * bb_std
    df['bb_position'] = (c - bb_lower) / (bb_upper - bb_lower).replace(0, np.nan)
    df['bb_width'] = (bb_upper - bb_lower) / bb_sma
    df['bb_width_z'] = rolling_zscore(df['bb_width'], 50)

    # === ADX / ATR ===
    tr = pd.concat([
        h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()
    ], axis=1).max(axis=1)
    atr14 = tr.rolling(14).mean()
    df['atr_pct'] = atr14 / c
    df['atr_z'] = rolling_zscore(atr14, 50)

    up_move = h - h.shift(1)
    down_move = l.shift(1) - l
    pos_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0), index=df.index)
    neg_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0), index=df.index)
    pos_di = 100 * pos_dm.ewm(span=14, adjust=False).mean() / atr14.replace(0, np.nan)
    neg_di = 100 * neg_dm.ewm(span=14, adjust=False).mean() / atr14.replace(0, np.nan)
    dx = 100 * (pos_di - neg_di).abs() / (pos_di + neg_di).replace(0, np.nan)
    df['adx_14'] = dx.ewm(span=14, adjust=False).mean()

    # === Market structure ===
    df['hh'] = (c > c.shift(1)) & (c.shift(1) > c.shift(2))
    df['ll'] = (c < c.shift(1)) & (c.shift(1) < c.shift(2))

    for look in [3, 5, 10]:
        df[f'break_high_{look}'] = c > df['high'].shift(1).rolling(look).max()
        df[f'break_low_{look}'] = c < df['low'].shift(1).rolling(look).min()

    # === Consecutive candles ===
    bull = c > o
    bear = c < o
    df['consec_bull'] = bull.groupby((bull != bull.shift()).cumsum()).cumcount() + 1
    df['consec_bear'] = bear.groupby((bear != bear.shift()).cumsum()).cumcount() + 1
    df.loc[~bull, 'consec_bull'] = 0
    df.loc[~bear, 'consec_bear'] = 0

    # === Cyclical ===
    if 'timestamp' in df.columns:
        ts = pd.to_datetime(df['timestamp'])
        df['hour'] = ts.dt.hour
        df['dayofweek'] = ts.dt.dayofweek
        df['is_asia'] = ((ts.dt.hour >= 1) & (ts.dt.hour <= 9)).astype(int)
        df['is_london'] = ((ts.dt.hour >= 8) & (ts.dt.hour <= 16)).astype(int)
        df['is_ny'] = ((ts.dt.hour >= 13) & (ts.dt.hour <= 21)).astype(int)

    # === Target (forward-looking, no lookahead for features) ===
    future_ret_2 = c.shift(-2) / c - 1
    future_ret_4 = c.shift(-4) / c - 1
    df['target_up_2'] = (future_ret_2 > 0.001).astype(int)  # >0.1% up in 2 candles
    df['target_down_2'] = (future_ret_2 < -0.001).astype(int)
    df['target_dir_2'] = np.where(future_ret_2 > 0.001, 1, np.where(future_ret_2 < -0.001, -1, 0))

    return df

FEATURE_COLS = [
    'ret_1_z', 'ret_2_z', 'ret_3_z', 'ret_5_z', 'ret_10_z',
    'close_pos', 'high_low_pct', 'body_pct', 'candle_range',
    'vol_ratio', 'vol_z', 'vol_ma_ratio',
    'stc_val', 'stc_slope', 'stc_ma_dist', 'stc_slope_z',
    'rsi_14', 'rsi_slope', 'rsi_z',
    'macd_hist', 'macd_hist_slope', 'macd_z',
    'price_ema20', 'price_ema50', 'price_ema200', 'price_hma25',
    'hma_ema200', 'hma_slope', 'hma_slope_z',
    'bb_position', 'bb_width', 'bb_width_z',
    'atr_pct', 'atr_z', 'adx_14',
    'consec_bull', 'consec_bear',
    'hour', 'dayofweek', 'is_asia', 'is_london', 'is_ny',
]

def get_feature_vector(df, i, feature_cols=None):
    if feature_cols is None:
        feature_cols = FEATURE_COLS
    row = df.iloc[i]
    vec = []
    for col in feature_cols:
        val = row.get(col, 0)
        if pd.isna(val) or np.isinf(val):
            val = 0
        vec.append(float(val))
    return np.array(vec)
