"""Mean Reversion optimizer"""
import gc, json, os, random, sys, time
from dataclasses import replace
from itertools import product
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import BotConfig
from backtest import run_backtest

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "optimize_output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

MIN_TRADES = 5
TRAIN_COUNT = 12
TEST_COUNT = 10

MR_GRID = {
    "mr_rsi_period": [7, 14, 21],
    "mr_rsi_oversold": [20, 25, 30, 35],
    "mr_rsi_overbought": [65, 70, 75, 80],
    "mr_bb_period": [14, 20, 30],
    "mr_bb_std": [1.5, 2.0, 2.5, 3.0],
    "mr_zscore_period": [14, 20, 30],
    "mr_zscore_threshold": [1.0, 1.5, 2.0, 2.5, 3.0],
    "mr_min_confluences": [1, 2, 3],
    "sl_atr_mult": [2.0, 3.0, 4.0, 5.0],
    "tp_atr_mult": [0.5, 1.0, 1.5, 2.0, 3.0],
    "be_atr_mult": [1.0, 2.0, 3.0, 5.0],
    "early_exit_max_loss": [-0.01, -0.02, -0.03],
    "limit_discount_pct": [0.001, 0.002, 0.003],
    "trail_tight_atr_threshold": [2.0, 3.0, 4.0],
    "trail_tight_mult": [0.05, 0.10, 0.15, 0.20],
    "trail_medium_atr_threshold": [1.5, 2.0, 3.0],
    "trail_medium_mult": [0.15, 0.20, 0.30],
    "trail_loose_mult": [0.30, 0.40, 0.50],
    "mr_tp_at_middle": [True, False],
}

MR_SAMPLES = 500


def load_symbols() -> dict:
    files = sorted(f for f in os.listdir(DATA_DIR) if f.endswith(".csv"))
    result = {}
    for fname in files:
        path = os.path.join(DATA_DIR, fname)
        try:
            df = pd.read_csv(path)
            if {"open","high","low","close","volume"}.issubset(set(df.columns)) and len(df) >= 500:
                sym = fname.replace("_USDT_USDT.csv", "/USDT:USDT")
                result[sym] = path
        except:
            pass
    return result


def rank_score(rdf: pd.DataFrame) -> float:
    if rdf.empty or len(rdf) == 0:
        return -999
    avg_pf = rdf["profit_factor"].mean()
    avg_exp = rdf["expectancy_pct"].mean()
    avg_wr = rdf["winrate"].mean()
    avg_ret = rdf["return_pct"].mean()
    avg_dd = rdf["max_drawdown_pct"].mean()
    total_trades = int(rdf["trades"].sum())
    if total_trades < MIN_TRADES:
        return -999
    score = (
        min(max(avg_pf, 0), 5.0) * 0.30
        + max(avg_exp, -0.05) * 100 * 0.25
        + avg_wr * 0.15
        + max(min(avg_ret, 0.50), -0.50) * 100 * 0.20
        - abs(min(avg_dd, 0)) * 100 * 0.10
    )
    return score


def main():
    t_start = time.time()
    random.seed(42)

    print("=" * 60)
    print("OPTIMIZACION MEAN REVERSION")
    print("=" * 60)

    print("Cargando datos...")
    all_data = load_symbols()
    all_syms = sorted(all_data.keys())
    print(f"{len(all_syms)} simbolos cargados")

    train_syms = all_syms[:TRAIN_COUNT]
    test_syms = all_syms[TRAIN_COUNT:TRAIN_COUNT + TEST_COUNT]
    train_paths = [all_data[s] for s in train_syms]
    test_paths = [all_data[s] for s in test_syms]
    print(f"Train: {len(train_syms)}, Test: {len(test_syms)}")

    # Build grid — sample without generating full product (too large)
    keys = list(MR_GRID.keys())
    grid_vals = {k: list(v) for k, v in MR_GRID.items()}
    total_possible = 1
    for v in grid_vals.values():
        total_possible *= len(v)
    sampled = []
    for _ in range(min(MR_SAMPLES, total_possible)):
        combo = {}
        for k in keys:
            combo[k] = random.choice(grid_vals[k])
        sampled.append(combo)
    print(f"Total possible: {total_possible}, Sampling: {len(sampled)}")

    results = []
    best_score = -999
    best_combo = None

    for idx, overrides in enumerate(sampled):
        overrides["strategy_mode"] = "mean_rev"
        cfg = replace(BotConfig(), **overrides)

        rows = []
        for sym, path in zip(train_syms, train_paths):
            try:
                df = pd.read_csv(path)
                if len(df) < 500:
                    continue
                res = run_backtest(df, symbol=sym, starting_balance=1000.0, cfg=cfg)
                s = res["summary"]
                if s["trades"] >= MIN_TRADES:
                    rows.append(s)
            except:
                pass

        rdf = pd.DataFrame(rows) if rows else pd.DataFrame()
        if rdf.empty:
            continue

        score = rank_score(rdf)
        row_data = {
            **overrides,
            "avg_profit_factor": rdf["profit_factor"].mean(),
            "avg_expectancy_pct": rdf["expectancy_pct"].mean(),
            "avg_winrate": rdf["winrate"].mean(),
            "avg_return_pct": rdf["return_pct"].mean(),
            "avg_max_drawdown_pct": rdf["max_drawdown_pct"].mean(),
            "total_trades": int(rdf["trades"].sum()),
            "score": score,
        }
        results.append(row_data)

        if score > best_score:
            best_score = score
            best_combo = row_data
            elapsed = time.time() - t_start
            print(
                f"[{idx+1}/{len(sampled)}] BEST score={score:.2f} "
                f"PF={rdf['profit_factor'].mean():.3f} "
                f"Exp={rdf['expectancy_pct'].mean()*100:+.2f}% "
                f"WR={rdf['winrate'].mean()*100:.0f}% "
                f"Trades={int(rdf['trades'].sum())} "
                f"elapsed={elapsed/60:.1f}min"
            )

        if (idx + 1) % 50 == 0:
            elapsed = time.time() - t_start
            print(f"[{idx+1}/{len(sampled)}] progress best={best_score:.2f} elapsed={elapsed/60:.1f}min")

    if not results:
        print("No se encontraron configuraciones viables")
        return

    rdf_out = pd.DataFrame(results).sort_values("score", ascending=False)
    path_out = os.path.join(OUTPUT_DIR, "mr_results.csv")
    rdf_out.to_csv(path_out, index=False)
    print(f"\nResultados -> {path_out}")

    # Print top 15
    print("\nTOP 15:")
    print(f"{'#':>3} {'Score':>6} {'PF':>6} {'Exp%':>7} {'WR%':>4} {'Trades':>6} {'rsi_per':>4} {'rsi_os':>4} {'rsi_ob':>4} {'bb_per':>4} {'bb_std':>4} {'zs_per':>4} {'zs_thr':>4} {'min_conf':>3}")
    for i, (_, r) in enumerate(rdf_out.head(15).iterrows()):
        print(f"{i+1:3d} {r['score']:6.2f} {r['avg_profit_factor']:6.3f} {r['avg_expectancy_pct']*100:7.2f} {r['avg_winrate']*100:4.0f} {r['total_trades']:6d} {r['mr_rsi_period']:4d} {r['mr_rsi_oversold']:4.0f} {r['mr_rsi_overbought']:4.0f} {r['mr_bb_period']:4d} {r['mr_bb_std']:4.1f} {r['mr_zscore_period']:4d} {r['mr_zscore_threshold']:4.1f} {r['mr_min_confluences']:3d}")

    # Validate top 5 on test symbols
    print("\n--- VALIDACION FINAL ---")
    top5 = rdf_out.head(5)
    validated = []
    for _, row in top5.iterrows():
        overrides = row.to_dict()
        overrides.pop("score", None)
        overrides.pop("avg_profit_factor", None)
        overrides.pop("avg_expectancy_pct", None)
        overrides.pop("avg_winrate", None)
        overrides.pop("avg_return_pct", None)
        overrides.pop("avg_max_drawdown_pct", None)
        overrides.pop("total_trades", None)
        cfg = replace(BotConfig(), **overrides)
        cfg = replace(cfg, strategy_mode="mean_rev")

        test_rows = []
        for sym, path in zip(test_syms, test_paths):
            try:
                df = pd.read_csv(path)
                if len(df) < 500:
                    continue
                res = run_backtest(df, symbol=sym, starting_balance=1000.0, cfg=cfg)
                s = res["summary"]
                if s["trades"] >= MIN_TRADES:
                    test_rows.append(s)
            except:
                pass
        trdf = pd.DataFrame(test_rows) if test_rows else pd.DataFrame()
        val_pf = trdf["profit_factor"].mean() if not trdf.empty else 0
        val_exp = trdf["expectancy_pct"].mean() if not trdf.empty else 0
        val_trades = int(trdf["trades"].sum()) if not trdf.empty else 0
        validated.append({**overrides, "val_pf": val_pf, "val_exp": val_exp, "val_trades": val_trades})
        print(f"  score={row['score']:.2f} train_PF={row['avg_profit_factor']:.3f} test_PF={val_pf:.3f} test_Exp={val_exp*100:+.2f}% test_Trades={val_trades}")

    vdf = pd.DataFrame(validated)
    vdf.to_csv(os.path.join(OUTPUT_DIR, "mr_validated.csv"), index=False)

    elapsed = time.time() - t_start
    print(f"\nTotal: {elapsed/60:.1f} min")


if __name__ == "__main__":
    main()
