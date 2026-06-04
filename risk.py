from dataclasses import dataclass
import math

try:
    from .config import BotConfig
except ImportError:
    from config import BotConfig


@dataclass(frozen=True)
class TradePlan:
    side: str
    entry: float
    stop_loss: float
    take_profit: float
    sl_pct: float
    tp_pct: float
    rr: float
    qty: float
    notional: float
    risk_usdt: float


def calculate_levels(side: str, price: float, atr: float, cfg: BotConfig) -> tuple[float, float, float]:
    if side == "long":
        entry = price * (1 - cfg.limit_discount_pct)
        sl = entry - atr * cfg.sl_atr_mult
        tp = entry + atr * cfg.tp_atr_mult
    elif side == "short":
        entry = price * (1 + cfg.limit_discount_pct)
        sl = entry + atr * cfg.sl_atr_mult
        tp = entry - atr * cfg.tp_atr_mult
    else:
        raise ValueError(f"Invalid side: {side}")
    return entry, sl, tp


def validate_distances(entry: float, sl: float, tp: float, cfg: BotConfig) -> tuple[bool, str, float, float, float]:
    if not all(math.isfinite(value) and value > 0 for value in (entry, sl, tp)):
        return False, "non-finite or non-positive price level", 0.0, 0.0, 0.0
    sl_pct = abs(entry - sl) / entry
    tp_pct = abs(entry - tp) / entry
    rr = tp_pct / sl_pct if sl_pct > 0 else 0.0
    if sl_pct > tp_pct:
        return False, "SL distance > TP distance", sl_pct, tp_pct, rr
    if sl_pct > cfg.max_sl_distance_pct:
        return False, f"SL too far: {sl_pct:.4f}", sl_pct, tp_pct, rr
    if tp_pct < cfg.min_tp_distance_pct:
        return False, f"TP too close: {tp_pct:.4f}", sl_pct, tp_pct, rr
    if rr < cfg.min_risk_reward_ratio:
        return False, f"RR too low: {rr:.2f}", sl_pct, tp_pct, rr
    return True, "ok", sl_pct, tp_pct, rr


def risk_sized_quantity(balance: float, entry: float, sl: float, cfg: BotConfig, min_amount: float = 1e-8) -> tuple[float, float, float]:
    if not all(math.isfinite(value) and value > 0 for value in (balance, entry, sl, min_amount)):
        return 0.0, 0.0, 0.0
    target_risk_usdt = balance * cfg.risk_per_trade
    stop_distance = abs(entry - sl)
    if stop_distance <= 0:
        return 0.0, 0.0, target_risk_usdt

    raw_qty = target_risk_usdt / stop_distance

    max_margin = balance * cfg.max_margin_fraction
    max_notional_by_margin = max_margin * cfg.leverage
    max_qty_by_margin = max_notional_by_margin / entry

    min_notional = min(cfg.min_margin_usdt * cfg.leverage, max_notional_by_margin)
    min_qty_for_small_account = min_notional / entry if min_notional > 0 else 0.0

    raw_qty = max(raw_qty, min_qty_for_small_account)
    max_qty_by_leverage = (balance * cfg.leverage) / entry
    raw_qty = min(raw_qty, max_qty_by_margin, max_qty_by_leverage)
    if raw_qty <= 0:
        return 0.0, 0.0, target_risk_usdt

    qty = (raw_qty // min_amount) * min_amount
    notional = qty * entry
    estimated_risk_usdt = qty * stop_distance
    return qty, notional, estimated_risk_usdt


def build_trade_plan(side: str, price: float, atr: float, balance: float, cfg: BotConfig, min_amount: float = 1e-8) -> tuple[TradePlan | None, str]:
    entry, sl, tp = calculate_levels(side, price, atr, cfg)
    valid, reason, sl_pct, tp_pct, rr = validate_distances(entry, sl, tp, cfg)
    if not valid:
        return None, reason
    qty, notional, risk_usdt = risk_sized_quantity(balance, entry, sl, cfg, min_amount)
    if qty <= 0:
        return None, "quantity below exchange minimum"
    plan = TradePlan(side, entry, sl, tp, sl_pct, tp_pct, rr, qty, notional, risk_usdt)
    return plan, "ok"


def apply_exit_slippage(price: float, side: str, cfg: BotConfig) -> float:
    if side == "long":
        return price * (1 - cfg.slippage_pct)
    return price * (1 + cfg.slippage_pct)


def apply_entry_slippage(price: float, side: str, cfg: BotConfig) -> float:
    if side == "long":
        return price * (1 + cfg.slippage_pct)
    return price * (1 - cfg.slippage_pct)
