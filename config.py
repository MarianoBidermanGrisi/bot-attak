import os
from dataclasses import dataclass


def _env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return float(value)


@dataclass(frozen=True)
class BotConfig:
    timeframe: str = "15m"
    max_open_positions: int = 5
    leverage: float = 10.0

    risk_per_trade: float = _env_float("BOT_RISK_PER_TRADE", 0.015)

    min_margin_usdt: float = _env_float("BOT_MIN_MARGIN_USDT", 1.0)
    max_margin_fraction: float = _env_float("BOT_MAX_MARGIN_FRACTION", 0.10)

    enable_early_exit: bool = True
    max_position_age_hours: float = 6.0
    limit_discount_pct: float = _env_float("BOT_LIMIT_DISCOUNT_PCT", 0.001)
    limit_order_expiry_minutes: int = 120
    use_volume_filter: bool = os.environ.get("BOT_USE_VOLUME_FILTER", "false").lower() in {"1", "true", "yes", "on"}
    use_supertrend_filter: bool = os.environ.get("BOT_USE_SUPERTREND_FILTER", "false").lower() in {"1", "true", "yes", "on"}

    # Strategy mode: "mean_rev" (Mean Reversion - ganador del backtest) o "trend"
    strategy_mode: str = "mean_rev"

    # Signal config (trend mode)
    min_triggers: int = 2
    require_all_trend: bool = True

    # --- Mean Reversion parameters (optimizados vía best_config.json) ---
    mr_rsi_period: int = 21
    mr_rsi_oversold: float = 20.0
    mr_rsi_overbought: float = 65.0
    mr_bb_period: int = 20
    mr_bb_std: float = 1.5
    mr_zscore_period: int = 30
    mr_zscore_threshold: float = 1.5
    mr_use_rsi: bool = True
    mr_use_bb: bool = True
    mr_use_zscore: bool = True
    mr_min_confluences: int = 3
    mr_volume_filter: bool = True
    mr_tp_at_middle: bool = True
    mr_early_exit_rsi_long: float = 50.0
    mr_early_exit_rsi_short: float = 50.0
    use_trend_filter: bool = False
    use_vma_slope: bool = os.environ.get("BOT_USE_VMA_SLOPE", "true").lower() in {"1", "true", "yes", "on"}

    max_sl_distance_pct: float = 0.035
    min_tp_distance_pct: float = 0.010
    min_risk_reward_ratio: float = 1.0

    diy_st_length: int = 10
    diy_st_mult: float = 3.0
    diy_vma_len: int = 6
    diy_macd_fast: int = 12
    diy_macd_slow: int = 26
    diy_macd_sig: int = 9
    diy_expiry: int = 1

    zl_length: int = 70
    zl_mult: float = 1.5
    tp_filter_len: int = 15

    # --- SL/TP calculation ---
    sl_atr_mult: float = _env_float("BOT_SL_ATR_MULT", 3.0)
    tp_atr_mult: float = _env_float("BOT_TP_ATR_MULT", 3.0)

    be_atr_mult: float = 2.0

    early_exit_max_loss: float = -0.03

    trail_tight_atr_threshold: float = 3.0
    trail_tight_mult: float = 0.20
    trail_medium_atr_threshold: float = 2.0
    trail_medium_mult: float = 0.15
    trail_loose_mult: float = 0.40

    fee_rate: float = 0.0006
    slippage_pct: float = 0.0005
    funding_rate_per_8h: float = 0.0

    state_db_path: str = os.environ.get("BOT_STATE_DB", "combined_strategy_v2_state.sqlite3")
    order_prefix: str = os.environ.get("BOT_ORDER_PREFIX", "combined-v2")
    dry_run: bool = os.environ.get("BOT_DRY_RUN", "true").lower() in {"1", "true", "yes", "on"}


def timeframe_to_minutes(timeframe: str) -> int:
    unit = timeframe[-1]
    value = int(timeframe[:-1])
    if unit == "m":
        return value
    if unit == "h":
        return value * 60
    if unit == "d":
        return value * 1440
    raise ValueError(f"Unsupported timeframe: {timeframe}")


def validate_live_env() -> None:
    required = ["BITGET_API_KEY", "BITGET_SECRET_KEY", "BITGET_PASSPHRASE"]
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise RuntimeError(f"Missing required live env vars: {', '.join(missing)}")
