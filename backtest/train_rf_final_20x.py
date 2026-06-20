import os, sys, numpy as np, pandas as pd, logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(BASE_DIR))
from backtest.data.downloader import load_symbol_data, get_all_saved_symbols
from backtest.ml.features import build_features, FEATURE_COLS
from backtest.strategies.abracadabra_v1 import compute_indicators
from sklearn.ensemble import RandomForestClassifier
import joblib

TF = '15m'

sym_data = {}
for sym in get_all_saved_symbols():
    d = load_symbol_data(sym)
    if TF in d: sym_data[sym] = d
log.info(f"{len(sym_data)} simbolos")

Xl, yl = [], []
for sym, data in sym_data.items():
    df = data[TF]
    if df is None or len(df) < 500: continue
    df = df.sort_values('timestamp').reset_index(drop=True)
    df = compute_indicators(df)
    df = build_features(df)
    df = df.dropna(subset=FEATURE_COLS)
    if len(df) < 100: continue
    Xl.append(df[FEATURE_COLS].values)
    yl.append(df['target_dir_2'].values)

X = np.vstack(Xl)
y = np.concatenate(yl)
m = y != 0
log.info(f"Entrenando RF final: {m.sum()} muestras, balance={(y[m]>0).mean():.3f}")

rf = RandomForestClassifier(n_estimators=200, max_depth=12, min_samples_leaf=10,
                            class_weight='balanced', random_state=42, n_jobs=-1)
rf.fit(X[m], (y[m] > 0).astype(int))

model_path = os.path.join(BASE_DIR, 'models', 'rf_15m_20x.joblib')
os.makedirs(os.path.dirname(model_path), exist_ok=True)
joblib.dump(rf, model_path)
log.info(f"Modelo guardado: {model_path}")

imp = sorted(zip(FEATURE_COLS, rf.feature_importances_), key=lambda x: -x[1])
log.info("Top features:" + " ".join(f"{n}={v:.3f}" for n, v in imp[:8]))
