import numpy as np
import pandas as pd
import pandas_ta as ta

try:
    from .config import BotConfig
except ImportError:
    from config import BotConfig


def calc_zlema(close: pd.Series, length: int) -> pd.Series:
    lag = int((length - 1) / 2)
    src = close + (close - close.shift(lag))
    return src.ewm(span=length, adjust=False).mean()


def calc_zl_bands(df: pd.DataFrame, length: int, mult: float) -> tuple[pd.Series, pd.Series, pd.Series]:
    atr = ta.atr(df["high"], df["low"], df["close"], length=length)
    volatility = atr.rolling(window=length * 3).max() * mult
    zlema = calc_zlema(df["close"], length)
    return zlema, zlema + volatility, zlema - volatility


def calc_two_pole(close: pd.Series, filter_length: int) -> tuple[pd.Series, pd.Series]:
    sma1 = close.rolling(25).mean()
    diff = close - sma1
    sma_diff = diff.rolling(25).mean()
    std_diff = diff.rolling(25).std().replace(0, 1e-10)
    normalized = (diff - sma_diff) / std_diff
    smooth1 = normalized.ewm(span=filter_length, adjust=False).mean()
    smooth2 = smooth1.ewm(span=filter_length, adjust=False).mean()
    return smooth2, smooth2.shift(4)


def calculate_vma(close_series: pd.Series, length: int) -> pd.Series:
    k = 1.0 / length
    close = close_series.astype(float)
    pdm = (close - close.shift(1)).clip(lower=0)
    mdm = (close.shift(1) - close).clip(lower=0)
    pdm_s = pdm.ewm(alpha=k, adjust=False).mean()
    mdm_s = mdm.ewm(alpha=k, adjust=False).mean()
    total = pdm_s + mdm_s
    pdi = np.where(total != 0, pdm_s / total, 0)
    mdi = np.where(total != 0, mdm_s / total, 0)
    pdi_s = pd.Series(pdi, index=close.index).ewm(alpha=k, adjust=False).mean()
    mdi_s = pd.Series(mdi, index=close.index).ewm(alpha=k, adjust=False).mean()
    spread = (pdi_s - mdi_s).abs()
    total_di = pdi_s + mdi_s
    dx = np.where(total_di != 0, spread / total_di, 0)
    i_s = pd.Series(dx, index=close.index).ewm(alpha=k, adjust=False).mean()
    hhv = i_s.rolling(window=length, min_periods=1).max()
    llv = i_s.rolling(window=length, min_periods=1).min()
    denom = hhv - llv
    vi = np.where(denom != 0, (i_s - llv) / denom, 0)

    values = np.zeros(len(close))
    close_arr = close.to_numpy()
    values[0] = close_arr[0]
    for i in range(1, len(close)):
        values[i] = (1 - k * vi[i]) * values[i - 1] + k * vi[i] * close_arr[i]
    return pd.Series(values, index=close.index)


def calculate_all_indicators(df: pd.DataFrame, cfg: BotConfig) -> pd.DataFrame:
    out = df.copy()
    close = out["close"]

    supertrend = ta.supertrend(
        high=out["high"],
        low=out["low"],
        close=out["close"],
        length=cfg.diy_st_length,
        multiplier=cfg.diy_st_mult,
    )
    st_dir_col = [col for col in supertrend.columns if col.startswith("SUPERTd_")][0]
    out["ST_dir"] = supertrend[st_dir_col]
    out["VMA"] = calculate_vma(close, cfg.diy_vma_len)

    macd = ta.macd(close, fast=cfg.diy_macd_fast, slow=cfg.diy_macd_slow, signal=cfg.diy_macd_sig)
    macd_col = [col for col in macd.columns if col.startswith("MACD_")][0]
    macd_sig_col = [col for col in macd.columns if col.startswith("MACDs_")][0]
    out["MACD"] = macd[macd_col]
    out["MACD_sig"] = macd[macd_sig_col]

    out["ZLEMA"], out["ZL_Upper"], out["ZL_Lower"] = calc_zl_bands(out, cfg.zl_length, cfg.zl_mult)
    out["Two_P"], out["Two_PP"] = calc_two_pole(close, cfg.tp_filter_len)
    out["ATR14"] = ta.atr(out["high"], out["low"], out["close"], length=14)
    out["Vol_Anomaly"] = out["volume"].rolling(3).mean() > out["volume"].rolling(20).mean()
    return out.dropna().copy()
