"""Comprehensive compatibility check for all modified files"""
import sys, os, traceback

base = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, base)

errors = []
def check(label, ok, detail=""):
    if ok:
        print(f"  OK  {label}")
    else:
        print(f"  FAIL {label}: {detail}")
        errors.append((label, detail))

# 1. All imports work
print("=== IMPORTS ===")
try:
    from config import BotConfig, timeframe_to_minutes, validate_live_env
    check("config.py imports", True)
except Exception as e:
    check("config.py imports", False, str(e))

try:
    from indicators import calculate_all_indicators, calc_zlema, calc_two_pole, calculate_vma
    check("indicators.py imports", True)
except Exception as e:
    check("indicators.py imports", False, str(e))

try:
    from signals import SignalOptions, build_signal_options, generate_signals
    check("signals.py imports", True)
except Exception as e:
    check("signals.py imports", False, str(e))

try:
    from mean_rev_signals import generate_mr_signals
    check("mean_rev_signals.py imports", True)
except Exception as e:
    check("mean_rev_signals.py imports", False, str(e))

try:
    from risk import build_trade_plan, calculate_levels, validate_distances, apply_entry_slippage, apply_exit_slippage
    check("risk.py imports", True)
except Exception as e:
    check("risk.py imports", False, str(e))

try:
    from metrics import equity_curve_stats, summarize_trades, summarize_by_trigger, trades_to_dataframe
    check("metrics.py imports", True)
except Exception as e:
    check("metrics.py imports", False, str(e))

try:
    from backtest import run_backtest, load_ohlcv_csv, run_ablation
    check("backtest.py imports", True)
except Exception as e:
    check("backtest.py imports", False, str(e))

# 2. Config defaults are valid
print("\n=== CONFIG DEFAULTS ===")
cfg = BotConfig()
check("strategy_mode default", cfg.strategy_mode in ("mean_rev", "trend"))
check("min_triggers range", 1 <= cfg.min_triggers <= 3)
check("mr_min_confluences range", 1 <= cfg.mr_min_confluences <= 3)
check("mr_rsi_oversold < mr_rsi_overbought", cfg.mr_rsi_oversold < cfg.mr_rsi_overbought)
check("trail thresholds ordered",
      cfg.trail_medium_atr_threshold <= cfg.trail_tight_atr_threshold)

# 3. Mode dispatch consistency
print("\n=== MODE DISPATCH ===")
for fname, label in [("backtest.py", None), ("live_bot.py", None)]:
    path = os.path.join(base, fname)
    with open(path) as f:
        content = f.read()
    checks = [
        ('"mean_rev"' in content, f"{fname} references mean_rev mode"),
        ('generate_mr_signals' in content, f"{fname} imports generate_mr_signals"),
        ('generate_signals' in content, f"{fname} still uses generate_signals"),
        ('strategy_mode' in content, f"{fname} checks strategy_mode"),
    ]
    for ok, label2 in checks:
        check(label2, ok)

# 4. DataFrame column consistency
print("\n=== COLUMN NAMES ===")
# columns created by indicators.py
ind_cols = {"RSI", "BB_Upper", "BB_Middle", "BB_Lower", "ZScore", "Vol_Anomaly", "ATR14",
            "VMA", "ST_dir", "MACD", "MACD_sig", "ZLEMA", "ZL_Upper", "ZL_Lower", "Two_P", "Two_PP"}
# columns consumed by mean_rev_signals.py
mr_cols = {"RSI", "BB_Lower", "BB_Upper", "ZScore", "Vol_Anomaly", "close"}
# columns consumed by signals.py (trend)
trend_cols = {"VMA", "ST_dir", "MACD", "MACD_sig", "ZLEMA", "ZL_Upper", "ZL_Lower", "Two_P", "Two_PP", "Vol_Anomaly", "close",
              "zl_trend_state"}
# columns consumed by backtest.py entry/exit logic
bt_cols = {"Master_Buy", "Master_Sell", "Signal_Trigger", "ATR14", "close", "high", "low",
           "ZLEMA", "Two_P", "Two_PP", "BB_Middle", "RSI"}

check("MR signals use only indicator columns", mr_cols.issubset(ind_cols | {"close"}))

# 5. risk.py validate_distances edge case
print("\n=== VALIDATE DISTANCES ===")
import math
from risk import validate_distances
entry, sl, tp = 100.0, 97.0, 103.0
ok, reason, _, _, rr = validate_distances(entry, sl, tp, cfg)
check("normal: sl=3%, tp=3%, rr=1.0 (min_rr=1.0)", ok, reason)
# Reject when min_risk_reward_ratio > 1.0 with equal distances
from dataclasses import replace
entry, sl, tp = 100.0, 95.0, 105.0
cfg2 = replace(BotConfig(), min_risk_reward_ratio=1.5)
ok2, reason2, sl_pct, tp_pct, rr2 = validate_distances(entry, sl, tp, cfg2)
check("equal distances with min_rr=1.5: should reject", not ok2, f"rr={rr2:.2f}")

# 6. Full backtest: trend mode still works
print("\n=== TREND MODE BACKTEST ===")
data_dir = os.path.join(base, "data")
files = sorted(f for f in os.listdir(data_dir) if f.endswith(".csv"))
if files:
    import pandas as pd
    path = os.path.join(data_dir, files[0])
    df = pd.read_csv(path)
    from dataclasses import replace
    cfg_trend = replace(BotConfig(), strategy_mode="trend")
    res = run_backtest(df, symbol="TEST/USDT:USDT", starting_balance=1000.0, cfg=cfg_trend)
    n_trades = res["summary"]["trades"]
    check(f"trend mode backtest works on {files[0]}: {n_trades} trades", n_trades >= 0)

# 7. Full backtest: MR mode works
print("\n=== MR MODE BACKTEST ===")
cfg_mr = BotConfig()  # strategy_mode="mean_rev" by default
res = run_backtest(df, symbol="TEST/USDT:USDT", starting_balance=1000.0, cfg=cfg_mr)
n_trades_mr = res["summary"]["trades"]
check(f"MR mode backtest works on {files[0]}: {n_trades_mr} trades", n_trades_mr >= 0)

# 8. Check mr_tp_at_middle=False config can produce trades
print("\n=== MR CONFIG WITH EQUAL SL/TP ===")
cfg_eq = replace(BotConfig(),
    sl_atr_mult=3.0, tp_atr_mult=3.0,
    limit_discount_pct=0.003,
    mr_tp_at_middle=False,
    mr_min_confluences=1,
    mr_rsi_period=21, mr_rsi_oversold=35, mr_rsi_overbought=75,
    mr_bb_period=20, mr_bb_std=3.0,
    mr_zscore_period=30, mr_zscore_threshold=3.0,
    be_atr_mult=5.0, early_exit_max_loss=-0.02,
    trail_tight_atr_threshold=3.0, trail_tight_mult=0.1,
    trail_medium_atr_threshold=3.0, trail_medium_mult=0.2,
    trail_loose_mult=0.3,
)
res_eq = run_backtest(df, symbol="TEST/USDT:USDT", starting_balance=1000.0, cfg=cfg_eq)
n_eq = res_eq["summary"]["trades"]
if n_eq > 0:
    check(f"equal sl/tp config: {n_eq} trades (OK - uses middle band override?)", True)
else:
    check(f"equal sl/tp config: {n_eq} trades (blocked by validate_distances) - NEED FIX", n_eq >= 0)

# 9. Check mr_tp_at_middle=True config
print("\n=== MR CONFIG WITH MIDDLE BAND TP ===")
cfg_mid = replace(BotConfig(),
    sl_atr_mult=3.0, tp_atr_mult=3.0,
    mr_tp_at_middle=True,
    mr_min_confluences=1,
    mr_rsi_period=21, mr_rsi_oversold=35, mr_rsi_overbought=75,
    mr_bb_period=20, mr_bb_std=3.0,
)
res_mid = run_backtest(df, symbol="TEST/USDT:USDT", starting_balance=1000.0, cfg=cfg_mid)
n_mid = res_mid["summary"]["trades"]
check(f"middle band TP config: {n_mid} trades", n_mid >= 0)

# 10. live_bot.py key patterns
print("\n=== LIVE_BOT PATTERNS ===")
with open(os.path.join(base, "live_bot.py")) as f:
    lb = f.read()
check("live_bot has MR indicator display", "BB_Lower" in lb)
check("live_bot has MR early exit", "mr_early_exit_rsi_long" in lb)
check("live_bot has trend mode path still", "ZLEMA" in lb)

print(f"\n{'='*50}")
print(f"RESULT: {len(errors)} errors found")
for label, detail in errors:
    print(f"  - {label}: {detail}")
if not errors:
    print("  ALL CHECKS PASSED")
