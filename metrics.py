import math
from collections import defaultdict
from dataclasses import asdict

import pandas as pd


def equity_curve_stats(equity: list[float]) -> dict:
    if not equity:
        return {"start": 0.0, "end": 0.0, "return_pct": 0.0, "max_drawdown_pct": 0.0}
    peak = equity[0]
    max_dd = 0.0
    for value in equity:
        peak = max(peak, value)
        if peak > 0:
            max_dd = min(max_dd, (value - peak) / peak)
    return {
        "start": equity[0],
        "end": equity[-1],
        "return_pct": (equity[-1] - equity[0]) / equity[0] if equity[0] else 0.0,
        "max_drawdown_pct": max_dd,
    }


def summarize_trades(trades: list) -> dict:
    if not trades:
        return {
            "trades": 0,
            "winrate": 0.0,
            "avg_win_pct": 0.0,
            "avg_loss_pct": 0.0,
            "expectancy_pct": 0.0,
            "profit_factor": 0.0,
        }
    pnl = [t.pnl_pct for t in trades]
    wins = [x for x in pnl if x > 0]
    losses = [x for x in pnl if x <= 0]
    gross_win = sum(x for x in pnl if x > 0)
    gross_loss = abs(sum(x for x in pnl if x < 0))
    return {
        "trades": len(trades),
        "winrate": len(wins) / len(trades),
        "avg_win_pct": sum(wins) / len(wins) if wins else 0.0,
        "avg_loss_pct": sum(losses) / len(losses) if losses else 0.0,
        "expectancy_pct": sum(pnl) / len(pnl),
        "profit_factor": gross_win / gross_loss if gross_loss else math.inf,
    }


def summarize_by_trigger(trades: list) -> pd.DataFrame:
    groups = defaultdict(list)
    for trade in trades:
        groups[trade.trigger or "unknown"].append(trade)
    rows = []
    for trigger, group in sorted(groups.items()):
        row = summarize_trades(group)
        row["trigger"] = trigger
        rows.append(row)
    return pd.DataFrame(rows)


def trades_to_dataframe(trades: list) -> pd.DataFrame:
    return pd.DataFrame([asdict(t) for t in trades])
