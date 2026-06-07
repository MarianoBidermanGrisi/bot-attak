import pandas as pd

try:
    from .config import BotConfig
except ImportError:
    from config import BotConfig


def generate_mr_signals(df: pd.DataFrame, cfg: BotConfig) -> pd.DataFrame:
    out = df.copy()
    out["Master_Buy"] = False
    out["Master_Sell"] = False
    out["Signal_Trigger"] = ""

    for i in range(1, len(out)):
        close = out["close"].iloc[i]
        rsi = out["RSI"].iloc[i]
        bb_lo = out["BB_Lower"].iloc[i]
        bb_hi = out["BB_Upper"].iloc[i]
        zs = out["ZScore"].iloc[i]
        vol_ok = bool(out["Vol_Anomaly"].iloc[i])

        # Trend filter: VMA direction alignment
        vma = out["VMA"].iloc[i]
        vma_prev = out["VMA"].iloc[i - 1] if i > 0 else vma
        if cfg.use_vma_slope:
            trend_filter_ok_long = (not cfg.use_trend_filter) or (vma > vma_prev)
            trend_filter_ok_short = (not cfg.use_trend_filter) or (vma < vma_prev)
        else:
            trend_filter_ok_long = (not cfg.use_trend_filter) or close > vma
            trend_filter_ok_short = (not cfg.use_trend_filter) or close < vma

        # Long confluences
        long_confs = []
        if cfg.mr_use_bb and close <= bb_lo:
            long_confs.append("bb")
        if cfg.mr_use_rsi and rsi < cfg.mr_rsi_oversold:
            long_confs.append("rsi")
        if cfg.mr_use_zscore and zs < -cfg.mr_zscore_threshold:
            long_confs.append("zscore")
        vol_ok_long = (not cfg.mr_volume_filter) or vol_ok

        if len(long_confs) >= cfg.mr_min_confluences and vol_ok_long and trend_filter_ok_long:
            out.at[out.index[i], "Master_Buy"] = True
            out.at[out.index[i], "Signal_Trigger"] = "+".join(long_confs)

        # Short confluences
        short_confs = []
        if cfg.mr_use_bb and close >= bb_hi:
            short_confs.append("bb")
        if cfg.mr_use_rsi and rsi > cfg.mr_rsi_overbought:
            short_confs.append("rsi")
        if cfg.mr_use_zscore and zs > cfg.mr_zscore_threshold:
            short_confs.append("zscore")
        vol_ok_short = (not cfg.mr_volume_filter) or vol_ok

        if len(short_confs) >= cfg.mr_min_confluences and vol_ok_short and trend_filter_ok_short:
            out.at[out.index[i], "Master_Sell"] = True
            out.at[out.index[i], "Signal_Trigger"] = "+".join(short_confs)

    return out
