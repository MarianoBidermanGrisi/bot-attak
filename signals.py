from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

try:
    from .config import BotConfig
except ImportError:
    from config import BotConfig


@dataclass(frozen=True)
class SignalOptions:
    use_vma: bool = True
    use_supertrend: bool = True
    use_macd: bool = True
    use_zl_trend: bool = True
    use_volume: bool = True
    use_st_trigger: bool = True
    use_zl_trigger: bool = True
    use_tp_trigger: bool = True


def build_signal_options(**overrides: bool) -> SignalOptions:
    return replace(SignalOptions(), **overrides)


def generate_signals(df: pd.DataFrame, cfg: BotConfig, options: SignalOptions | None = None) -> pd.DataFrame:
    opts = options or SignalOptions()
    out = df.copy()
    out["Master_Buy"] = False
    out["Master_Sell"] = False
    out["Signal_Trigger"] = ""
    out["st_buy"] = False
    out["st_sell"] = False
    out["zl_buy"] = False
    out["zl_sell"] = False
    out["tp_buy"] = False
    out["tp_sell"] = False

    cross_up = (out["close"] > out["ZL_Upper"]) & (out["close"].shift(1) <= out["ZL_Upper"].shift(1))
    cross_down = (out["close"] < out["ZL_Lower"]) & (out["close"].shift(1) >= out["ZL_Lower"].shift(1))
    out["zl_trend_state"] = np.nan
    out.loc[cross_up, "zl_trend_state"] = 1
    out.loc[cross_down, "zl_trend_state"] = -1
    out["zl_trend_state"] = out["zl_trend_state"].ffill().fillna(0)

    for i in range(cfg.diy_expiry + 1, len(out)):
        vma_long = out["close"].iloc[i] > out["VMA"].iloc[i]
        vma_short = out["close"].iloc[i] < out["VMA"].iloc[i]
        st_long = out["ST_dir"].iloc[i] == 1
        st_short = out["ST_dir"].iloc[i] == -1
        macd_long = out["MACD"].iloc[i] > out["MACD_sig"].iloc[i]
        macd_short = out["MACD"].iloc[i] < out["MACD_sig"].iloc[i]
        zl_up = out["zl_trend_state"].iloc[i] == 1
        zl_down = out["zl_trend_state"].iloc[i] == -1
        volume_ok = bool(out["Vol_Anomaly"].iloc[i])

        trend_long = (
            (vma_long or not opts.use_vma)
            and (st_long or not opts.use_supertrend)
            and (macd_long or not opts.use_macd)
            and (zl_up or not opts.use_zl_trend)
        )
        trend_short = (
            (vma_short or not opts.use_vma)
            and (st_short or not opts.use_supertrend)
            and (macd_short or not opts.use_macd)
            and (zl_down or not opts.use_zl_trend)
        )

        st_buy = False
        st_sell = False
        for j in range(i - cfg.diy_expiry + 1, i + 1):
            if out["ST_dir"].iloc[j] == 1 and out["ST_dir"].iloc[j - 1] == -1:
                st_buy = True
            if out["ST_dir"].iloc[j] == -1 and out["ST_dir"].iloc[j - 1] == 1:
                st_sell = True

        zl_buy = out["close"].iloc[i] > out["ZLEMA"].iloc[i] and out["close"].iloc[i - 1] <= out["ZLEMA"].iloc[i - 1]
        zl_sell = out["close"].iloc[i] < out["ZLEMA"].iloc[i] and out["close"].iloc[i - 1] >= out["ZLEMA"].iloc[i - 1]
        tp_buy = out["Two_P"].iloc[i] > out["Two_PP"].iloc[i] and out["Two_P"].iloc[i - 1] <= out["Two_PP"].iloc[i - 1] and out["Two_P"].iloc[i] < 0
        tp_sell = out["Two_P"].iloc[i] < out["Two_PP"].iloc[i] and out["Two_P"].iloc[i - 1] >= out["Two_PP"].iloc[i - 1] and out["Two_P"].iloc[i] > 0

        out.at[out.index[i], "st_buy"] = st_buy
        out.at[out.index[i], "st_sell"] = st_sell
        out.at[out.index[i], "zl_buy"] = zl_buy
        out.at[out.index[i], "zl_sell"] = zl_sell
        out.at[out.index[i], "tp_buy"] = tp_buy
        out.at[out.index[i], "tp_sell"] = tp_sell

        buy_triggers = []
        sell_triggers = []
        if opts.use_st_trigger and st_buy:
            buy_triggers.append("st")
        if opts.use_zl_trigger and zl_buy:
            buy_triggers.append("zl")
        if opts.use_tp_trigger and tp_buy:
            buy_triggers.append("tp")
        if opts.use_st_trigger and st_sell:
            sell_triggers.append("st")
        if opts.use_zl_trigger and zl_sell:
            sell_triggers.append("zl")
        if opts.use_tp_trigger and tp_sell:
            sell_triggers.append("tp")

        if trend_long and len(buy_triggers) >= 2 and (volume_ok or not opts.use_volume):
            out.at[out.index[i], "Master_Buy"] = True
            out.at[out.index[i], "Signal_Trigger"] = "+".join(buy_triggers)
        if trend_short and len(sell_triggers) >= 2 and (volume_ok or not opts.use_volume):
            out.at[out.index[i], "Master_Sell"] = True
            out.at[out.index[i], "Signal_Trigger"] = "+".join(sell_triggers)

    return out
