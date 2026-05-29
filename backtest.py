import argparse
from dataclasses import dataclass

import pandas as pd

try:
    from .config import BotConfig, timeframe_to_minutes
    from .indicators import calculate_all_indicators
    from .metrics import equity_curve_stats, summarize_by_trigger, summarize_trades, trades_to_dataframe
    from .risk import apply_entry_slippage, apply_exit_slippage, build_trade_plan
    from .signals import SignalOptions, build_signal_options, generate_signals
except ImportError:
    from config import BotConfig, timeframe_to_minutes
    from indicators import calculate_all_indicators
    from metrics import equity_curve_stats, summarize_by_trigger, summarize_trades, trades_to_dataframe
    from risk import apply_entry_slippage, apply_exit_slippage, build_trade_plan
    from signals import SignalOptions, build_signal_options, generate_signals


@dataclass
class Trade:
    symbol: str
    side: str
    trigger: str
    entry_time: object
    exit_time: object
    entry: float
    exit: float
    stop_loss: float
    take_profit: float
    qty: float
    pnl_usdt: float
    pnl_pct: float
    reason: str
    bars_held: int
    fees_usdt: float
    funding_usdt: float


def _timestamp(row: pd.Series):
    value = row.get("timestamp")
    if pd.isna(value):
        return row.name
    return value


def _exit_price_for_bar(row: pd.Series, side: str, sl: float, tp: float, cfg: BotConfig) -> tuple[float | None, str | None]:
    # Conservative same-candle rule: if SL and TP both touch, assume SL first.
    if side == "long":
        if row["low"] <= sl:
            return apply_exit_slippage(sl, side, cfg), "sl"
        if row["high"] >= tp:
            return apply_exit_slippage(tp, side, cfg), "tp"
    else:
        if row["high"] >= sl:
            return apply_exit_slippage(sl, side, cfg), "sl"
        if row["low"] <= tp:
            return apply_exit_slippage(tp, side, cfg), "tp"
    return None, None


def _unrealized_pct(side: str, entry: float, mark: float) -> float:
    if side == "long":
        return (mark - entry) / entry
    return (entry - mark) / entry


def _pnl_usdt(side: str, entry: float, exit_price: float, qty: float) -> float:
    if side == "long":
        return (exit_price - entry) * qty
    return (entry - exit_price) * qty


def run_backtest(
    raw_df: pd.DataFrame,
    symbol: str = "SYMBOL/USDT:USDT",
    starting_balance: float = 1000.0,
    cfg: BotConfig | None = None,
    options: SignalOptions | None = None,
) -> dict:
    cfg = cfg or BotConfig()
    expiry_bars = max(1, cfg.limit_order_expiry_minutes // timeframe_to_minutes(cfg.timeframe))
    df = calculate_all_indicators(raw_df, cfg)
    df = generate_signals(df, cfg, options)

    balance = starting_balance
    equity = [starting_balance]
    trades: list[Trade] = []
    pending_orders: list[dict] = []
    open_trade: dict | None = None
    cooldown_until = -1

    for i in range(1, len(df)):
        row = df.iloc[i]

        still_pending = []
        for order in pending_orders:
            if i > order["expires_at"]:
                continue
            filled = False
            if order["side"] == "long" and row["low"] <= order["entry"]:
                filled = True
            if order["side"] == "short" and row["high"] >= order["entry"]:
                filled = True
            if filled and open_trade is None:
                entry = apply_entry_slippage(order["entry"], order["side"], cfg)
                open_trade = {
                    **order,
                    "entry": entry,
                    "entry_time": _timestamp(row),
                    "entry_i": i,
                    "peak": entry,
                    "be_active": False,
                    "last_trail_sl": None,
                }
                entry_fee = abs(entry * order["qty"]) * cfg.fee_rate
                balance -= entry_fee
                open_trade["entry_fee"] = entry_fee
            elif not filled:
                still_pending.append(order)
        pending_orders = still_pending

        if open_trade is not None:
            side = open_trade["side"]
            entry = open_trade["entry"]
            qty = open_trade["qty"]
            exit_price, exit_reason = _exit_price_for_bar(row, side, open_trade["stop_loss"], open_trade["take_profit"], cfg)

            atr = float(row.get("ATR14") or 0)
            profit_pct = _unrealized_pct(side, entry, row["close"])

            if exit_price is None:
                open_trade["peak"] = max(open_trade["peak"], row["high"]) if side == "long" else min(open_trade["peak"], row["low"])
                if atr > 0:
                    be_trigger = (atr * 1.5) / entry
                else:
                    be_trigger = 0.015
                if profit_pct >= be_trigger and not open_trade["be_active"]:
                    open_trade["be_active"] = True
                    open_trade["stop_loss"] = entry

                if open_trade["be_active"] and atr > 0:
                    peak = open_trade["peak"]
                    profit_at_peak = _unrealized_pct(side, entry, peak)
                    atr_profit = profit_at_peak / (atr / entry) if atr > 0 else 0
                    if atr_profit >= 2.7:
                        trail_dist = (atr * 0.2) / peak
                    elif atr_profit >= 2.2:
                        trail_dist = (atr * 0.5) / peak
                    else:
                        trail_dist = (atr * 1.0) / peak
                    trail_sl = peak * (1 - trail_dist) if side == "long" else peak * (1 + trail_dist)
                    valid = (side == "long" and trail_sl > entry * 1.001) or (side == "short" and trail_sl < entry * 0.999)
                    moved = open_trade["last_trail_sl"] is None or (side == "long" and trail_sl > open_trade["last_trail_sl"]) or (side == "short" and trail_sl < open_trade["last_trail_sl"])
                    if valid and moved:
                        open_trade["stop_loss"] = trail_sl
                        open_trade["last_trail_sl"] = trail_sl

                if cfg.enable_early_exit and profit_pct < -0.005:
                    if side == "long" and (row["close"] < row["ZLEMA"] or row["Two_P"] < row["Two_PP"]):
                        exit_price, exit_reason = apply_exit_slippage(row["close"], side, cfg), "early_exit"
                    if side == "short" and (row["close"] > row["ZLEMA"] or row["Two_P"] > row["Two_PP"]):
                        exit_price, exit_reason = apply_exit_slippage(row["close"], side, cfg), "early_exit"

            age_hours = (i - open_trade["entry_i"]) * timeframe_to_minutes(cfg.timeframe) / 60
            if exit_price is None and age_hours >= cfg.max_position_age_hours:
                exit_price, exit_reason = apply_exit_slippage(row["close"], side, cfg), "max_age"

            if exit_price is not None:
                gross = _pnl_usdt(side, entry, exit_price, qty)
                exit_fee = abs(exit_price * qty) * cfg.fee_rate
                funding = abs(entry * qty) * cfg.funding_rate_per_8h * (age_hours / 8)
                net = gross - exit_fee - funding
                balance += net
                fees = open_trade.get("entry_fee", 0.0) + exit_fee
                pnl_pct = net / (entry * qty) if entry * qty else 0.0
                trades.append(
                    Trade(
                        symbol=symbol,
                        side=side,
                        trigger=open_trade["trigger"],
                        entry_time=open_trade["entry_time"],
                        exit_time=_timestamp(row),
                        entry=entry,
                        exit=exit_price,
                        stop_loss=open_trade["stop_loss"],
                        take_profit=open_trade["take_profit"],
                        qty=qty,
                        pnl_usdt=net,
                        pnl_pct=pnl_pct,
                        reason=exit_reason or "unknown",
                        bars_held=i - open_trade["entry_i"],
                        fees_usdt=fees,
                        funding_usdt=funding,
                    )
                )
                cooldown_minutes = 240 if net <= 0 else 60
                cooldown_until = i + max(1, cooldown_minutes // timeframe_to_minutes(cfg.timeframe))
                open_trade = None

        if open_trade is None and not pending_orders and i >= cooldown_until:
            side = None
            if bool(row["Master_Buy"]):
                side = "long"
            elif bool(row["Master_Sell"]):
                side = "short"
            if side:
                plan, reason = build_trade_plan(side, float(row["close"]), float(row["ATR14"]), balance, cfg)
                if plan:
                    pending_orders.append(
                        {
                            "side": side,
                            "trigger": str(row["Signal_Trigger"]),
                            "created_i": i,
                            "expires_at": i + expiry_bars,
                            "entry": plan.entry,
                            "stop_loss": plan.stop_loss,
                            "take_profit": plan.take_profit,
                            "qty": plan.qty,
                        }
                    )

        equity.append(balance)

    return {
        "symbol": symbol,
        "starting_balance": starting_balance,
        "ending_balance": balance,
        "equity": equity,
        "trades": trades,
        "summary": {**summarize_trades(trades), **equity_curve_stats(equity)},
        "by_trigger": summarize_by_trigger(trades),
        "signals_df": df,
    }


def run_ablation(raw_df: pd.DataFrame, symbol: str, starting_balance: float, cfg: BotConfig) -> pd.DataFrame:
    cases = {
        "full": SignalOptions(),
        "no_volume": build_signal_options(use_volume=False),
        "no_macd": build_signal_options(use_macd=False),
        "no_vma": build_signal_options(use_vma=False),
        "no_supertrend_filter": build_signal_options(use_supertrend=False),
        "no_zl_trend": build_signal_options(use_zl_trend=False),
        "only_st_trigger": build_signal_options(use_zl_trigger=False, use_tp_trigger=False),
        "only_zl_trigger": build_signal_options(use_st_trigger=False, use_tp_trigger=False),
        "only_tp_trigger": build_signal_options(use_st_trigger=False, use_zl_trigger=False),
    }
    rows = []
    for name, options in cases.items():
        result = run_backtest(raw_df, symbol, starting_balance, cfg, options)
        row = result["summary"].copy()
        row["case"] = name
        rows.append(row)
    return pd.DataFrame(rows).sort_values("expectancy_pct", ascending=False)


def load_ohlcv_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    required = {"open", "high", "low", "close", "volume"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"CSV missing columns: {', '.join(sorted(missing))}")
    return df


def main() -> None:
    parser = argparse.ArgumentParser(description="Combined Strategy v2 backtest")
    parser.add_argument("csv", help="CSV with timestamp, open, high, low, close, volume")
    parser.add_argument("--symbol", default="SYMBOL/USDT:USDT")
    parser.add_argument("--balance", type=float, default=1000.0)
    parser.add_argument("--ablation", action="store_true")
    parser.add_argument("--trades-out", default="")
    args = parser.parse_args()

    cfg = BotConfig()
    raw_df = load_ohlcv_csv(args.csv)
    if args.ablation:
        print(run_ablation(raw_df, args.symbol, args.balance, cfg).to_string(index=False))
        return

    result = run_backtest(raw_df, args.symbol, args.balance, cfg)
    print(pd.DataFrame([result["summary"]]).to_string(index=False))
    print("\nBy trigger:")
    print(result["by_trigger"].to_string(index=False))
    if args.trades_out:
        trades_to_dataframe(result["trades"]).to_csv(args.trades_out, index=False)


if __name__ == "__main__":
    main()
