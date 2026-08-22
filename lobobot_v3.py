#!/usr/bin/env python3
"""
LOBOBOT v4 — BITLOBO TRADING (Refactorizado Limpio)
=====================================================
Single-file monolithic deployment para Render.com.
97 parches integrados sin deuda tecnica.

Variables de entorno: BITGET_API_KEY, BITGET_SECRET_KEY, BITGET_PASSPHRASE,
    TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, LOBO_LIQUIDEZ_PCT, LOBO_FUTUROS_PCT,
    LOBO_HEDGE_ENABLED, LOBOBOT_PAPER_TRADE, etc.
"""
from __future__ import annotations
import os, sys, time, json, math, logging, asyncio, threading, csv, signal, atexit
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional
import numpy as np
import pandas as pd
pd.set_option("future.no_silent_downcasting", True)
import ccxt, ccxt.async_support as ccxt_async
import requests
try:
    from flask import Flask
    _FLASK_AVAILABLE = True
except ImportError:
    _FLASK_AVAILABLE = False

# ── LOGGER (UTF-8 portable) ──
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass
LOG_TO_FILE = os.environ.get('BOT_LOG_TO_FILE', '1') == '1'
LOG_LEVEL = os.environ.get('BOT_LOG_LEVEL', 'INFO')
_handlers = [logging.StreamHandler(sys.stdout)]
if LOG_TO_FILE:
    _handlers.append(logging.FileHandler(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "lobobot_v3.log"),
        encoding="utf-8"))
logging.basicConfig(level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
                    format="%(asctime)s [%(levelname)s] %(message)s", handlers=_handlers)
log = logging.getLogger("lobobot_v3")
logging.getLogger("werkzeug").setLevel(logging.WARNING)

# ── ESTADO EN MEMORIA ──
ALERTS_HISTORY: dict = {}; PEAK_PRICES: dict = {}; COOLDOWNS: dict = {}
SESSION_ACTIVE_SYMBOLS: set = set()
DAILY_STATS: dict = {'tp':0,'sl':0,'be':0,'timeout':0,'pnl':0.0,'fees':0.0,
    'tp_names':[],'sl_names':[],'be_names':[],'timeout_names':[]}
TRADE_ENTRIES: dict = {}; HEDGE_ENTRIES: dict = {}; TRAIL_COUNTS: dict = {}
LAST_KNOWN_INDICATORS: dict = {}; ADVERSE_PRICES: dict = {}; PRICE_PATHS: dict = {}
SPOT_POSITIONS: dict = {}; PARTIAL_LEVEL: dict = {}
_LAST_SCAN_TIME: float = 0.0
DOMINANCE_CACHE: dict = {'btc':None,'usdtd':None,'usdtd_short':None,'ts':0}
DOMINANCE_CACHE_TTL = 300; USDTD_HISTORY: list = []
_DOMINANCE_LOCK = threading.Lock()
_BG_DOMINANCE_THREAD: Optional[threading.Thread] = None
_BG_PROXY_THREAD: Optional[threading.Thread] = None

# ── BACKGROUND THREADS: Dominancias ──
def _bg_refresh_dominancia():
    try:
        resp = requests.get("https://api.coingecko.com/api/v3/global", timeout=10)
        if resp.status_code == 200:
            btc_d = resp.json().get('data',{}).get('market_cap_percentage',{}).get('btc')
            if btc_d is not None:
                with _DOMINANCE_LOCK:
                    DOMINANCE_CACHE['btc'] = btc_d > 50.0
                    DOMINANCE_CACHE['ts'] = time.time()
    except Exception as e:
        log.debug("BG Dominancia error: %s", e)

def _bg_refresh_proxy_usdtd():
    exch_bg = None
    try:
        exch_bg = ccxt.bitget({'enableRateLimit': True})
        tickers = exch_bg.fetch_tickers()
        vol_usdt = sum(float(t.get('quoteVolume',0)) for s,t in tickers.items() if s.endswith('/USDT:USDT'))
        vol_total = sum(float(t.get('quoteVolume',0)) for t in tickers.values())
        proxy = (vol_usdt / vol_total * 100) if vol_total > 0 else 50.0
        now = time.time()
        with _DOMINANCE_LOCK:
            USDTD_HISTORY.append((now, proxy))
            if len(USDTD_HISTORY) > 80:
                USDTD_HISTORY[:] = USDTD_HISTORY[-80:]
            result = True
            if len(USDTD_HISTORY) >= 15:
                vals = [v for _,v in USDTD_HISTORY]
                for i in range(2, len(vals)-2):
                    gap_up = vals[i] - vals[i-2]
                    if gap_up > 0.5:
                        gap_alto = max(vals[i-2], vals[i])
                        gap_bajo = min(vals[i-2], vals[i])
                        rellenado = any(gap_bajo <= vals[j] <= gap_alto for j in range(i+1, len(vals)))
                        if not rellenado and proxy >= gap_bajo * 0.99:
                            DOMINANCE_CACHE['usdtd'] = True
                            DOMINANCE_CACHE['ts'] = now
                            return
            vals = [v for _,v in USDTD_HISTORY[-30:]]
            result = (proxy >= sorted(vals)[int(len(vals)*0.85)] * 0.98) if len(vals) >= 10 else (proxy > 62.0)
            DOMINANCE_CACHE['usdtd'] = result
            DOMINANCE_CACHE['ts'] = now
    except Exception as e:
        log.debug("BG USDT.D error: %s", e)
    finally:
        if exch_bg:
            try: exch_bg.close()
            except: pass

def _schedule_bg_dominance_refresh():
    global _BG_DOMINANCE_THREAD, _BG_PROXY_THREAD
    now = time.time()
    if now - DOMINANCE_CACHE.get('ts',0) < DOMINANCE_CACHE_TTL:
        return
    if (_BG_DOMINANCE_THREAD and _BG_DOMINANCE_THREAD.is_alive()) or \
       (_BG_PROXY_THREAD and _BG_PROXY_THREAD.is_alive()):
        return
    _BG_DOMINANCE_THREAD = threading.Thread(target=_bg_refresh_dominancia, daemon=True)
    _BG_PROXY_THREAD = threading.Thread(target=_bg_refresh_proxy_usdtd, daemon=True)
    _BG_DOMINANCE_THREAD.start(); _BG_PROXY_THREAD.start()

# ── RUTAS DE ARCHIVOS ──
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PRICE_PATHS_DIR = os.path.join(BASE_DIR, 'price_paths_v3')
os.makedirs(PRICE_PATHS_DIR, exist_ok=True)
TRADES_CSV_PATH    = os.path.join(BASE_DIR, 'trades_v3.csv')
TRADE_ENTRIES_PATH = os.path.join(BASE_DIR, 'trade_entries_v3.json')
PARTIAL_LEVEL_PATH = os.path.join(BASE_DIR, 'partial_level_v3.json')
SIGNALS_LOG_PATH   = os.path.join(BASE_DIR, 'signals_log_v3.csv')

def _save_trade_entries():
    try:
        data = {sym: {k: v.isoformat() if isinstance(v, datetime) else v for k,v in e.items()}
                for sym, e in TRADE_ENTRIES.items()}
        with open(TRADE_ENTRIES_PATH, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as ex:
        log.error("Error guardando trade_entries: %s", ex)

def _load_trade_entries():
    try:
        if not os.path.exists(TRADE_ENTRIES_PATH): return
        with open(TRADE_ENTRIES_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
        for sym, e in data.items():
            if 'entry_time' in e and isinstance(e['entry_time'], str):
                e['entry_time'] = datetime.fromisoformat(e['entry_time'])
        TRADE_ENTRIES.update(data)
        log.info("Cargadas %d entradas pendientes", len(data))
    except Exception as ex:
        log.error("Error cargando trade_entries: %s", ex)

def _save_partial_level():
    try:
        with open(PARTIAL_LEVEL_PATH, 'w', encoding='utf-8') as f:
            json.dump(PARTIAL_LEVEL, f, ensure_ascii=False, indent=2)
    except Exception as ex:
        log.error("Error guardando partial_level: %s", ex)

def _load_partial_level():
    try:
        if not os.path.exists(PARTIAL_LEVEL_PATH): return
        with open(PARTIAL_LEVEL_PATH, 'r', encoding='utf-8') as f:
            loaded = {k: int(v) for k, v in json.load(f).items()}
        PARTIAL_LEVEL.update(loaded)
        log.info("Cargados %d estados parciales", len(loaded))
    except Exception as ex:
        log.error("Error cargando partial_level: %s", ex)

# ── CONFIGURACION DESDE ENTORNO ──
API_KEY = os.environ.get('BITGET_API_KEY', '')
SECRET_KEY = os.environ.get('BITGET_SECRET_KEY', '')
PASSPHRASE = os.environ.get('BITGET_PASSPHRASE', '')
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN', '')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '')
TOP_N = int(os.environ.get('LOBO_TOP_N', '100'))
TIMEFRAME_PRINCIPAL = os.environ.get('LOBO_TIMEFRAME_PRINCIPAL', '15m')
TIMEFRAME_CONFIRMACION = os.environ.get('LOBO_TIMEFRAME_CONFIRMACION', '4h')
TIMEFRAME_MICRO = os.environ.get('LOBO_TIMEFRAME_MICRO', '5m')
LOBO_LIQUIDEZ_PCT = float(os.environ.get('LOBO_LIQUIDEZ_PCT', '20')) / 100
LOBO_FUTUROS_PCT = float(os.environ.get('LOBO_FUTUROS_PCT', '80')) / 100
LOBO_SPOT_MARTINGALA_NIVELES = [float(os.environ.get(f'LOBO_SPOT_MART_{i}', str(v))) for i,v in enumerate([0.10,0.20,0.30],1)]
LOBO_IMPULSO_MIN_VELAS = int(os.environ.get('LOBO_IMPULSO_MIN_VELAS', '5'))
LOBO_IMPULSO_MAX_VELAS = int(os.environ.get('LOBO_IMPULSO_MAX_VELAS', '40'))
LOBO_IMPULSO_PEND_MIN = float(os.environ.get('LOBO_IMPULSO_PEND_MIN', '0.012'))
LOBO_SMA100_TOL_ATR = float(os.environ.get('LOBO_SMA100_TOL_ATR', '1.0'))
LOBO_ADX_PERIOD = int(os.environ.get('LOBO_ADX_PERIOD', '14'))
LOBO_ADX_MIN = float(os.environ.get('LOBO_ADX_MIN', '15'))
LOBO_ADX_MAX = float(os.environ.get('LOBO_ADX_MAX', '50'))
LOBO_ADX_DESC_VELAS = int(os.environ.get('LOBO_ADX_DESC_VELAS', '6'))
LOBO_FVG_MIN_GAP_ATR = float(os.environ.get('LOBO_FVG_MIN_GAP_ATR', '0.3'))
LOBO_FVG_MAX_VELAS = int(os.environ.get('LOBO_FVG_MAX_VELAS', '48'))
LOBO_OB_MIN_MOV_ATR = float(os.environ.get('LOBO_OB_MIN_MOV_ATR', '2.0'))
LOBO_OB_LOOKBACK = int(os.environ.get('LOBO_OB_LOOKBACK', '10'))
LOBO_SWEEP_LOOKBACK = int(os.environ.get('LOBO_SWEEP_LOOKBACK', '10'))
LOBO_SWEEP_MAX_PEN_ATR = float(os.environ.get('LOBO_SWEEP_MAX_PEN_ATR', '1.0'))
LOBO_MECHA_MIN_ATR = float(os.environ.get('LOBO_MECHA_MIN_ATR', '0.5'))
LOBO_MECHA_CUERPO_RATIO = float(os.environ.get('LOBO_MECHA_CUERPO_RATIO', '0.3'))
LOBO_ELLIOTT_LOOKBACK = int(os.environ.get('LOBO_ELLIOTT_LOOKBACK', '60'))
LOBO_ATR_PERIOD = int(os.environ.get('LOBO_ATR_PERIOD', '14'))
LOBO_RISK_PCT = float(os.environ.get('LOBO_RISK_PCT', '2')) / 100
LOBO_RISK_PCT_EXCEP = float(os.environ.get('LOBO_RISK_PCT_EXCEP', '4')) / 100
LOBO_MAX_POSITIONS = int(os.environ.get('LOBO_MAX_POSITIONS', '5'))
LOBO_TP1_SIZE = float(os.environ.get('LOBO_TP1_SIZE', '0.40'))
LOBO_TP2_SIZE = float(os.environ.get('LOBO_TP2_SIZE', '0.30'))
LOBO_TP3_SIZE = float(os.environ.get('LOBO_TP3_SIZE', '0.30'))
LOBO_TP2_ATR_MULT = float(os.environ.get('LOBO_TP2_ATR_MULT', '2.5'))
LOBO_TP3_ATR_MULT = float(os.environ.get('LOBO_TP3_ATR_MULT', '4.0'))
LOBO_TRAIL_ATR_MULT = float(os.environ.get('LOBO_TRAIL_ATR_MULT', '1.0'))
PARTIAL_ENABLED = True
TP1_CLOSE_PCT = LOBO_TP1_SIZE; TP2_CLOSE_PCT = LOBO_TP2_SIZE; TP3_CLOSE_PCT = LOBO_TP3_SIZE
MAX_SL_PCT = float(os.environ.get('LOBO_MAX_SL_PCT', '0.030'))
SL_LOOKBACK = int(os.environ.get('LOBO_SL_LOOKBACK', '20'))
TP1_PNL_TARGET = float(os.environ.get('LOBO_TP1_PNL_TARGET', '0.15'))
TP2_PNL_TARGET = float(os.environ.get('LOBO_TP2_PNL_TARGET', '0.30'))
TP3_PNL_TARGET = float(os.environ.get('LOBO_TP3_PNL_TARGET', '0.50'))
LOBO_TIMEOUT_HORAS = float(os.environ.get('LOBO_TIMEOUT_HORAS', '96'))
LEVERAGE = float(os.environ.get('LOBO_LEVERAGE', '20.0'))
LOBO_SCORE_MIN = int(os.environ.get('LOBO_SCORE_MIN', '12'))
MIN_ORDER_USDT = float(os.environ.get('LOBO_MIN_ORDER_USDT', '5'))
PAPER_TRADE = os.environ.get('LOBOBOT_PAPER_TRADE', 'false').lower() == 'true'
FEE_TAKER = float(os.environ.get('LOBO_FEE_TAKER', '0.0006'))
LOBO_KILL_MAX_CONSEC_LOSSES = int(os.environ.get('LOBO_KILL_MAX_CONSEC_LOSSES', '4'))
LOBO_KILL_COOLDOWN_H = float(os.environ.get('LOBO_KILL_COOLDOWN_H', '24'))
KILL_UNTIL: float = 0.0; CONSECUTIVE_LOSSES: int = 0; KILL_STREAK_AT_TRIGGER: int = 0
_shutdown_event: threading.Event = threading.Event()
LOBO_SL_ATR = 3.0
LOBO_SL_ATR_SMALL_VOL = float(os.environ.get('LOBO_SL_ATR_SMALL_VOL', '5000000'))
LOBO_REGIME_FILTER = os.environ.get('LOBO_REGIME_FILTER', '0').lower() == '1'
LOBO_WHITELIST = {b.strip().upper() for b in os.environ.get('LOBO_WHITELIST', '').split(',') if b.strip()}
LOBO_REGIME_EMA_PERIOD = int(os.environ.get('LOBO_REGIME_EMA_PERIOD', '50'))
LOBO_BLACKLIST = {'SPCX','NBIS','MRVL','SKHYNIX','SKHY','RKLB','INJ','ENA','APT','JTO','ALICE','DOS','ENSO','GWEI','BASED','CRV','HOME'}
LOBO_TRADE_START_HOUR = 10; LOBO_TRADE_END_HOUR = 23
LOBO_TRADING_HOURS_ENABLED = False  # True = activar filtro de horario, False = operar 24/7
LOBO_HEDGE_ENABLED = os.environ.get('LOBO_HEDGE_ENABLED', 'true').lower() == 'true'
LOBO_HEDGE_LEV_MULT = float(os.environ.get('LOBO_HEDGE_LEV_MULT', '3.0'))
LOBO_HEDGE_TRIGGER_PCT = float(os.environ.get('LOBO_HEDGE_TRIGGER_PCT', '0.5'))
LOBO_HEDGE_MARGIN_PCT = float(os.environ.get('LOBO_HEDGE_MARGIN_PCT', '0.15'))
LOBO_CHOCH_LOOKBACK = int(os.environ.get('LOBO_CHOCH_LOOKBACK', '30'))
LOBO_MICRO_LOOKBACK = int(os.environ.get('LOBO_MICRO_LOOKBACK', '72'))
LOBO_FLAT_MIN_VELAS = int(os.environ.get('LOBO_FLAT_MIN_VELAS', '3'))
LOBO_FLAT_MAX_ATR = float(os.environ.get('LOBO_FLAT_MAX_ATR', '1.5'))
LOBO_BTCD_ELLOTT_LOOKBACK = int(os.environ.get('LOBO_BTCD_ELLOTT_LOOKBACK', '60'))
LOBO_D1_CHECK_START = int(os.environ.get('LOBO_D1_CHECK_START', '0'))
LOBO_RSI_PERIOD = int(os.environ.get('LOBO_RSI_PERIOD', '14'))
LOBO_RSI_OVERSOLD = float(os.environ.get('LOBO_RSI_OVERSOLD', '30'))
LOBO_RSI_OVERBOUGHT = float(os.environ.get('LOBO_RSI_OVERBOUGHT', '70'))
LOBO_VOL_RATIO_MIN = float(os.environ.get('LOBO_VOL_RATIO_MIN', '1.5'))
LOBO_VOL_PERIOD = int(os.environ.get('LOBO_VOL_PERIOD', '20'))
FETCH_CONCURRENCY = int(os.environ.get('LOBO_FETCH_CONCURRENCY', '10'))
FETCH_TIMEOUT_S = float(os.environ.get('LOBO_FETCH_TIMEOUT_S', '15'))
log.info("BITLOBO v4: TOP=%d Risk=%.1f%% SL=%.1fATR MaxPos=%d ScoreMin=%d Paper=%s BK=%d",
    TOP_N, LOBO_RISK_PCT*100, LOBO_SL_ATR, LOBO_MAX_POSITIONS, LOBO_SCORE_MIN, PAPER_TRADE, len(LOBO_BLACKLIST))

# ── INDICADORES ──
def _sma(s, p): return s.rolling(p).mean()
def _ema(s, p): return s.ewm(span=p, adjust=False).mean()
def _atr(df, period=14):
    h,l,c = df['high'],df['low'],df['close']
    tr = pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()
def _rsi(series, period=14):
    if period < 1: period = 14
    d = series.diff()
    g = d.where(d>0,0).ewm(alpha=1/period, adjust=False).mean()
    ls = (-d.where(d<0,0)).ewm(alpha=1/period, adjust=False).mean()
    return 100 - (100 / (1 + g / ls.replace(0, np.nan)))
def _wilder_ema(s, p): return s.ewm(alpha=1.0/p, adjust=False).mean()

def filtro_rsi(df, es_long):
    if len(df) < LOBO_RSI_PERIOD+5: return True, 50.0
    r = _rsi(df['close'], LOBO_RSI_PERIOD)
    if r.isna().all(): return True, 50.0
    v = float(r.iloc[-1])
    if pd.isna(v): return True, 50.0
    if es_long: return (v < LOBO_RSI_OVERSOLD, v)
    return (v > LOBO_RSI_OVERBOUGHT, v)

def validar_volumen(df, es_long):
    if len(df) < LOBO_VOL_PERIOD+3: return True, 1.0
    vm = df['volume'].rolling(LOBO_VOL_PERIOD).mean()
    ratio = float(df['volume'].iloc[-1]) / max(float(vm.iloc[-1]), 1)
    if es_long: return (ratio >= LOBO_VOL_RATIO_MIN, ratio)
    return (ratio >= 0.7, ratio)

def check_usdtd_resistencia_short():
    now = time.time()
    if now - DOMINANCE_CACHE['ts'] < DOMINANCE_CACHE_TTL and DOMINANCE_CACHE.get('usdtd_short') is not None:
        return bool(DOMINANCE_CACHE['usdtd_short'])
    _schedule_bg_dominance_refresh()
    with _DOMINANCE_LOCK: snap = list(USDTD_HISTORY)
    if len(snap) >= 10:
        vals = [v for _,v in snap[-30:]]
        proxy = snap[-1][1]
        result = proxy <= sorted(vals)[len(vals)//2] * 1.01
        with _DOMINANCE_LOCK:
            DOMINANCE_CACHE['usdtd_short'] = result; DOMINANCE_CACHE['ts'] = 0
        return result
    return False

def check_dominancia_btc_long():
    now = time.time()
    if now - DOMINANCE_CACHE['ts'] < DOMINANCE_CACHE_TTL and DOMINANCE_CACHE.get('btc') is not None:
        return DOMINANCE_CACHE['btc']
    _schedule_bg_dominance_refresh()
    result = False; _exch_fb = None
    try:
        _exch_fb = ccxt.bitget({'enableRateLimit': True})
        ohlcv = _exch_fb.fetch_ohlcv('BTC/USDT:USDT', timeframe='4h', limit=30)
        if ohlcv and len(ohlcv) > 10:
            closes = pd.Series([c[4] for c in ohlcv])
            sma20 = closes.rolling(20).mean()
            if not (pd.isna(sma20.iloc[-1]) or pd.isna(sma20.iloc[-5])):
                result = (sma20.iloc[-1] - sma20.iloc[-5]) / max(sma20.iloc[-5], 1) > 0.001
    except Exception as e:
        log.debug("Fallback BTC.D error: %s", e)
    finally:
        if _exch_fb:
            try: _exch_fb.close()
            except: pass
    with _DOMINANCE_LOCK:
        DOMINANCE_CACHE['btc'] = result; DOMINANCE_CACHE['ts'] = time.time()
    return result

def check_usdtd_resistencia_long():
    now = time.time()
    if now - DOMINANCE_CACHE['ts'] < DOMINANCE_CACHE_TTL and DOMINANCE_CACHE.get('usdtd') is not None:
        return DOMINANCE_CACHE['usdtd']
    _schedule_bg_dominance_refresh()
    with _DOMINANCE_LOCK: snap = list(USDTD_HISTORY)
    if snap:
        vals = [v for _,v in snap[-30:]]
        if len(vals) >= 10:
            result = snap[-1][1] >= sorted(vals)[int(len(vals)*0.85)] * 0.98
            with _DOMINANCE_LOCK: DOMINANCE_CACHE['usdtd'] = result; DOMINANCE_CACHE['ts'] = 0
            return result
    with _DOMINANCE_LOCK: DOMINANCE_CACHE['usdtd'] = True; DOMINANCE_CACHE['ts'] = 0
    return True

# ── DETECCION DE PATRONES ──
def detectar_impulso(df):
    min_v = LOBO_IMPULSO_MIN_VELAS; max_v = min(LOBO_IMPULSO_MAX_VELAS, len(df)-2)
    n = len(df)
    for length in range(min(max_v, n-1), min_v-1, -1):
        start = n - length - 1
        if start < 0: continue
        tramo = df.iloc[start:start+length].copy()
        if len(tramo) < min_v: continue
        p0, p1 = float(tramo['close'].iloc[0]), float(tramo['close'].iloc[-1])
        pend = (p1-p0)/p0 if p0 > 0 else 0
        if abs(pend) < LOBO_IMPULSO_PEND_MIN: continue
        alcista = pend > 0; diff_total = abs(p1-p0); max_retro = diff_total * 0.382
        ok_velas = 0; total_velas = len(tramo)-1
        for j in range(1, len(tramo)):
            c0, c1 = float(tramo['close'].iloc[j-1]), float(tramo['close'].iloc[j])
            if alcista:
                retro = (c0-c1) if c1<c0 else 0
                if retro > max_retro: break
                if c1>c0: ok_velas += 1
            else:
                retro = (c1-c0) if c1>c0 else 0
                if retro > max_retro: break
                if c1<c0: ok_velas += 1
        else:
            if ok_velas / max(total_velas, 1) >= 0.7:
                return {'inicio': float(tramo['low'].min()) if alcista else float(tramo['high'].max()),
                    'fin': float(tramo['high'].max()) if alcista else float(tramo['low'].min()),
                    'tipo': 'alcista' if alcista else 'bajista', 'velas': len(tramo)}
    return None

def calcular_fibonacci(imp):
    h, l = max(imp['inicio'],imp['fin']), min(imp['inicio'],imp['fin'])
    d = h - l
    if d <= 0: return {}
    return {'level_0':h, 'level_0_236':h-0.236*d, 'level_0_382':h-0.382*d,
        'level_0_5':h-0.5*d, 'level_0_618':h-0.618*d, 'level_0_786':h-0.786*d, 'level_1_0':l, 'diff':d}

def sma100_en_zona_ote(v, f, atr):
    if 'level_0_5' not in f or 'level_0_618' not in f: return False
    ri = min(f['level_0_5'],f['level_0_618']) - atr*LOBO_SMA100_TOL_ATR
    rs = max(f['level_0_5'],f['level_0_618']) + atr*LOBO_SMA100_TOL_ATR
    return ri <= v <= rs

def adx_permite_entrada(df):
    if len(df) < LOBO_ADX_PERIOD*2: return False
    try:
        import pandas_ta as ta
        adx_df = ta.adx(df['high'],df['low'],df['close'], length=LOBO_ADX_PERIOD)
        ac = [c for c in adx_df.columns if 'ADX' in c.upper()]
        if not ac: return False
        adx_s = adx_df[ac[0]]
    except ImportError:
        p = LOBO_ADX_PERIOD; h,l,c = df['high'],df['low'],df['close']
        tr = pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1)
        up,down = h.diff(),-l.diff()
        pdm = pd.Series(np.where((up>down)&(up>0),up,0),index=df.index)
        mdm = pd.Series(np.where((down>up)&(down>0),down,0),index=df.index)
        tr_s,ps,ms = _wilder_ema(tr,p),_wilder_ema(pdm,p),_wilder_ema(mdm,p)
        tr_s = tr_s.replace(0,np.nan)
        dx = 100*(100*ps/tr_s - 100*ms/tr_s).abs()/(100*ps/tr_s + 100*ms/tr_s).replace(0,np.nan)
        adx_s = _wilder_ema(dx, p)
    if adx_s.isna().all(): return False
    v = float(adx_s.iloc[-1])
    if pd.isna(v) or not (LOBO_ADX_MIN <= v <= LOBO_ADX_MAX): return False
    n = min(LOBO_ADX_DESC_VELAS, len(adx_s)-1)
    if n < 3: return True
    vals = adx_s.iloc[-n:].dropna().values
    if len(vals) < 3 or np.std(vals)==0: return True
    return np.polyfit(np.arange(len(vals)), vals, 1)[0] < 0.01

def _fvg_rellenado(df, s, mx, gh, gl):
    for j in range(s, min(s+mx, len(df))):
        if df['low'].iloc[j] <= gh and df['high'].iloc[j] >= gl: return True
    return False

def detectar_fvg(df):
    if len(df) < 5: return []
    av = _atr(df, LOBO_ATR_PERIOD); out = []
    mx = min(LOBO_FVG_MAX_VELAS, len(df)-3)
    for i in range(2, len(df)-2):
        ai = av.iloc[i] if not pd.isna(av.iloc[i]) else 0
        gu = df['low'].iloc[i] - df['high'].iloc[i-2]
        if gu > ai*LOBO_FVG_MIN_GAP_ATR:
            ga, gb = float(df['high'].iloc[i-2]), float(df['low'].iloc[i])
            if not _fvg_rellenado(df,i,mx,ga,gb):
                out.append({'tipo':'alcista','gap_sup':ga,'gap_inf':gb,'idx':i,'precio_medio':(ga+gb)/2})
        gd = df['low'].iloc[i-2] - df['high'].iloc[i]
        if gd > ai*LOBO_FVG_MIN_GAP_ATR:
            ga, gb = float(df['high'].iloc[i]), float(df['low'].iloc[i-2])
            if not _fvg_rellenado(df,i,mx,ga,gb):
                out.append({'tipo':'bajista','gap_sup':ga,'gap_inf':gb,'idx':i,'precio_medio':(ga+gb)/2})
    return out

def detectar_order_blocks(df):
    if len(df) < LOBO_OB_LOOKBACK+5: return []
    av = _atr(df, LOBO_ATR_PERIOD); obs = []
    for i in range(LOBO_OB_LOOKBACK, len(df)-3):
        ai = av.iloc[i] if not pd.isna(av.iloc[i]) else 0
        if ai == 0: continue
        if df['close'].iloc[i] < df['open'].iloc[i]:
            rally = sum(float(df['close'].iloc[i+j]-df['low'].iloc[i+j]) for j in range(1,min(6,len(df)-i))
                if df['close'].iloc[i+j] > df['open'].iloc[i+j])
            if rally >= ai*LOBO_OB_MIN_MOV_ATR:
                obs.append({'tipo':'alcista','high':float(df['high'].iloc[i]),'low':float(df['low'].iloc[i]),'idx':i})
        if df['close'].iloc[i] > df['open'].iloc[i]:
            caida = sum(float(df['high'].iloc[i+j]-df['close'].iloc[i+j]) for j in range(1,min(6,len(df)-i))
                if df['close'].iloc[i+j] < df['open'].iloc[i+j])
            if caida >= ai*LOBO_OB_MIN_MOV_ATR:
                obs.append({'tipo':'bajista','high':float(df['high'].iloc[i]),'low':float(df['low'].iloc[i]),'idx':i})
    return obs

def detectar_sweep(df):
    if len(df) < LOBO_SWEEP_LOOKBACK+3: return []
    av = _atr(df, LOBO_ATR_PERIOD); sw = []
    mn = df['low'].iloc[-(LOBO_SWEEP_LOOKBACK+1):-1].min()
    mx = df['high'].iloc[-(LOBO_SWEEP_LOOKBACK+1):-1].max()
    u = df.iloc[-1]; ai = av.iloc[-1] if not pd.isna(av.iloc[-1]) else 0
    if u['low'] < mn:
        p = (mn-u['low'])/max(ai,1)
        if 0 < p < LOBO_SWEEP_MAX_PEN_ATR and u['close'] > mn:
            sw.append({'tipo':'sweep_bajista_long','nivel_roto':float(mn),'penetracion_atr':round(p,2)})
    if u['high'] > mx:
        p = (u['high']-mx)/max(ai,1)
        if 0 < p < LOBO_SWEEP_MAX_PEN_ATR and u['close'] < mx:
            sw.append({'tipo':'sweep_alcista_short','nivel_roto':float(mx),'penetracion_atr':round(p,2)})
    return sw

def validar_mecha_absorcion_en_zona(df, zi, zs, es_long, atr):
    if len(df) < 3: return False, 'pocos_datos'
    for idx in range(-1, -4, -1):
        try: v = df.iloc[idx]
        except IndexError: break
        o,h,l,c = float(v['open']),float(v['high']),float(v['low']),float(v['close'])
        body, rango = abs(c-o), h-l
        if rango < 1e-8: continue
        if es_long:
            if l > zs or not (c < o): continue
            mi = min(o,c)-l; rm = mi/rango; rc = body/rango
            if rc > 0.70 and rm < 0.05: return False, f'cuerpo_solido_inf_idx{idx}'
            if (mi >= atr*LOBO_MECHA_MIN_ATR) or rm >= 0.30: return True, f'abs_ok_idx{idx}'
            if rm >= 0.10: return True, f'mecha_parcial_idx{idx}'
        else:
            if h < zi or not (c > o): continue
            ms = h-max(o,c); rm = ms/rango; rc = body/rango
            if rc > 0.70 and rm < 0.05: return False, f'cuerpo_solido_sup_idx{idx}'
            if (ms >= atr*LOBO_MECHA_MIN_ATR) or rm >= 0.30: return True, f'abs_ok_idx{idx}'
            if rm >= 0.10: return True, f'mecha_parcial_idx{idx}'
    return True, 'sin_penetracion'

def evaluar_absorcion_long(df):
    """Wrapper de compatibilidad: evalúa absorción para long sin zona específica."""
    if len(df) < 3: return False, {'razon': 'pocos_datos'}
    atr = _atr(df, 14)
    atr_val = float(atr.iloc[-1]) if len(atr) > 0 and not pd.isna(atr.iloc[-1]) else 1.0
    lo = float(df['low'].min()) if len(df) > 0 else 0.0
    hi = float(df['high'].max()) if len(df) > 0 else 100.0
    ok, det = validar_mecha_absorcion_en_zona(df, lo, hi, True, atr_val)
    return ok, {'razon': det} if isinstance(det, str) else det

def detectar_pullback_confirmado(df, nivel, es_long):
    if len(df) < 10: return True
    c = df['close'].iloc[-15:].values
    if es_long:
        ci = np.where(c > nivel)[0]
        if len(ci) == 0: return False
        pb = c[ci[0]:]
        if len(pb) < 3: return False
        rm = min(pb)
        return rm <= nivel*1.015 and c[-1] > rm*1.005
    else:
        ci = np.where(c < nivel)[0]
        if len(ci) == 0: return False
        pb = c[ci[0]:]
        if len(pb) < 3: return False
        rm = max(pb)
        return rm >= nivel*0.985 and c[-1] < rm*0.995

def find_pivots(df, left=5, right=5):
    ch = 'high' if 'high' in df.columns else 'h'
    cl = 'low' if 'low' in df.columns else 'l'
    hs, ls = df[ch].values, df[cl].values; n = len(hs)
    ph, pl = [], []
    for i in range(left, n-right):
        if hs[i] == max(hs[max(0,i-left):i+right+1]): ph.append(i)
        if ls[i] == min(ls[max(0,i-left):i+right+1]): pl.append(i)
    return ph, pl

# ── ELLIOTT F11 ──
def detectar_estructura_elliott_v3(df):
    if len(df) < LOBO_ELLIOTT_LOOKBACK: return {'fase':'indefinida','razon':'pocos_datos'}
    phi, pli = find_pivots(df, 5, 5)
    if len(phi) < 3 or len(pli) < 2: return {'fase':'indefinida','razon':'pocos_pivots'}
    ch = 'high' if 'high' in df.columns else 'h'
    cl = 'low' if 'low' in df.columns else 'l'
    hs, ls = df[ch].values, df[cl].values
    for i in range(min(5, len(pli)-2)):
        for j in range(i+1, min(i+3, len(phi))):
            o1 = ls[pli[i]]; o1f = hs[phi[j]]; o1 = o1f-o1
            if o1 <= 0: continue
            for k in range(j+1, min(j+3, len(pli))):
                r2 = (o1f-ls[pli[k]])/o1
                if 0.382 <= r2 <= 0.786:
                    for l in range(k+1, min(k+4, len(phi))):
                        o3 = hs[phi[l]]-ls[pli[k]]; r3 = o3/o1
                        if 1.0 <= r3 <= 2.618:
                            for m in range(l+1, min(l+3, len(pli))):
                                r4 = (hs[phi[l]]-ls[pli[m]])/o3
                                if 0.236 <= r4 <= 0.5:
                                    return {'fase':'estructura_5_ondas','confianza':'alta',
                                        'onda_1':round(o1,2),'onda_2_retro':round(r2,2),
                                        'onda_3_ratio':round(r3,2),'onda_4_retro':round(r4,2),
                                        'ultimo_pivot':'maximo' if phi[-1]>pli[-1] else 'minimo'}
    return {'fase':'indefinida','razon':'sin_estructura_5_ondas'}

# ── CAPITAL ──
def capital_disponible_futuros(bt): return bt * LOBO_FUTUROS_PCT
def capital_liquidez(bt): return bt * LOBO_LIQUIDEZ_PCT
def capital_spot(bt): return 0.0

def calcular_margen_real_disponible(bt, positions_list=None):
    cf = capital_disponible_futuros(bt); ml = 0.0
    try:
        if exchange is None or PAPER_TRADE:
            for s, e in TRADE_ENTRIES.items():
                try:
                    sz = float(e.get('size_usdt',0))
                    if math.isfinite(sz) and sz > 0: ml += sz
                except: continue
            return max(0.0, cf-ml)
        if positions_list is None: positions_list = exchange.fetch_positions()
        for p in positions_list:
            try:
                ct = float(p.get('contracts',0))
                if not (math.isfinite(ct) and ct > 0): continue
                nt = float(p.get('notional',0)); lv = float(p.get('leverage',1))
                if math.isfinite(nt) and nt > 0 and math.isfinite(lv) and lv > 0:
                    ml += abs(nt)/lv
                else:
                    im = float(p.get('initialMargin',0))
                    if math.isfinite(im) and im > 0: ml += im
            except: continue
    except Exception as e:
        log.debug("Error margen lockeado: %s", e)
    d = max(0.0, cf-ml)
    log.info("Margen real: FutBruto=%.2f Lockeado=%.2f Disp=%.2f", cf, ml, d)
    return d

# ── F3: LIQUIDACION + APALANCAMIENTO ──
def calcular_precio_liquidacion(ep, lev, side):
    if not (math.isfinite(ep) and ep > 0 and math.isfinite(lev) and lev > 0): return 0
    return ep*(1-1/lev) if side=='long' else ep*(1+1/lev)

HIGH_LIQ_ALTS = {'ETH','SOL','BNB','XRP','ADA','DOGE','AVAX','LINK','DOT','MATIC','TRX','SHIB','UNI','ATOM','LTC'}

def calcular_apalancamiento_optimo(ep, df, zi, zs, es_long, sweeps, sym):
    base = sym.split('/')[0].replace(':USDT','').strip()
    mx = 20.0 if base=='BTC' else 20.0 if base in HIGH_LIQ_ALTS else 10.0
    n = min(8, len(df)); u = df.iloc[-n:]
    if es_long:
        ne = float(u['low'].min())
        for s in sweeps:
            if s['tipo']=='sweep_bajista_long': ne = min(ne, s.get('nivel_roto',ne))
        if ne >= ep: ne = ep*0.97
        tl = ne*0.997; ratio = tl/ep
        lv = mx if ratio >= 1.0 else 1.0/(1.0-ratio)
    else:
        ne = float(u['high'].max())
        for s in sweeps:
            if s['tipo']=='sweep_alcista_short': ne = max(ne, s.get('nivel_roto',ne))
        if ne <= ep: ne = ep*1.03
        tl = ne*1.003; ratio = tl/ep; lv = 1.0/(ratio-1.0)
    lev = min(mx, max(2.0, lv))
    lp = calcular_precio_liquidacion(ep, lev, 'long' if es_long else 'short')
    return round(lev,1), round(lp,4)

# ── F10: D1 ESTRUCTURAL ──
def validar_estructura_d1(df, ep, side):
    if len(df) < 10: return True
    phi, pli = find_pivots(df, 3, 3)
    cl = 'low' if 'low' in df.columns else 'l'
    ch = 'high' if 'high' in df.columns else 'h'
    cc = 'close' if 'close' in df.columns else 'c'
    sl = [(i, df[cl].values[i]) for i in pli]
    sh = [(i, df[ch].values[i]) for i in phi]
    cierre = float(df[cc].iloc[-1])
    if side == 'long' and sl:
        if cierre < sl[-1][1]*0.995: return False
    elif side == 'short' and sh:
        if cierre > sh[-1][1]*1.005: return False
    return True

# ── F4: COBERTURAS ──
def evaluar_cobertura_v4(pe, pa):
    sym = pe.get('symbol','')
    if sym in HEDGE_ENTRIES: return None
    side = pe.get('side','long')
    try:
        ep = float(pe.get('entry_price',0)); sl = float(pe.get('sl_price',0))
        lp = float(pe.get('liq_price',0)); mm = float(pe.get('size_usdt',0))
    except: return None
    if not all(math.isfinite(x) and x > 0 for x in [ep,sl,lp,mm]): return None
    dt = (ep-sl) if side=='long' else (sl-ep)
    dr = (ep-pa) if side=='long' else (pa-ep)
    if dt <= 0 or dr/dt < LOBO_HEDGE_TRIGGER_PCT: return None
    hs = 'short' if side=='long' else 'long'
    hm = mm*LOBO_HEDGE_MARGIN_PCT
    base = sym.split('/')[0].replace(':USDT','').strip()
    hl = 50.0 if base=='BTC' else 20.0 if base in HIGH_LIQ_ALTS else 10.0
    return {'side':hs,'leverage':hl,'tp_price':lp,'sl_price':ep,
        'margin_usdt':round(hm,2),'size_usdt':round(hm*hl,2),'entry_price':pa}

# ── F12a: PLAN TP QTY (split de cantidades para órdenes parciales) ──
def _plan_tp_qty(qty, step, tp1_price, tp2_price,
                 tp1_pct=None, tp2_pct=None,
                 min_notional=None):
    """Planifica qty de TP1/TP2/TP3 como profit_plans.
    Retorna dict: {tp1_qty, tp2_qty, tp3_qty, tp1_pct, tp2_pct, mode}.
    mode ∈ {'normal', 'merge', 'fallback', 'none', 'invalid'}"""
    if tp1_pct is None: tp1_pct = TP1_CLOSE_PCT
    if tp2_pct is None: tp2_pct = TP2_CLOSE_PCT
    if min_notional is None: min_notional = MIN_ORDER_USDT
    try:
        if not all(math.isfinite(v) for v in (qty, step, tp1_price, tp2_price)):
            return {'tp1_qty': 0.0, 'tp2_qty': 0.0, 'tp3_qty': 0.0,
                    'tp1_pct': tp1_pct, 'tp2_pct': tp2_pct, 'mode': 'invalid'}
    except TypeError:
        return {'tp1_qty': 0.0, 'tp2_qty': 0.0, 'tp3_qty': 0.0,
                'tp1_pct': tp1_pct, 'tp2_pct': tp2_pct, 'mode': 'invalid'}
    if (step is None or step <= 0 or qty is None or qty <= 0
            or tp1_price <= 0 or tp2_price <= 0
            or tp1_pct <= 0 or tp1_pct >= 1 or tp2_pct <= 0):
        return {'tp1_qty': 0.0, 'tp2_qty': 0.0, 'tp3_qty': 0.0,
                'tp1_pct': tp1_pct, 'tp2_pct': tp2_pct, 'mode': 'invalid'}
    # CHECK: posición total debe cumplir min_notional
    total_notional = qty * tp1_price
    if total_notional < min_notional:
        return {'tp1_qty': 0.0, 'tp2_qty': 0.0, 'tp3_qty': 0.0,
                'tp1_pct': tp1_pct, 'tp2_pct': tp2_pct, 'mode': 'none'}
    # Split: TP1=40%, TP2=30%, TP3=30% — round-half-up para tp1, floor para tp2
    tp1_qty = math.floor(qty * tp1_pct / step + 0.5) * step
    rem = qty - tp1_qty
    tp2_qty = math.floor(rem * (tp2_pct / (1 - tp1_pct)) / step) * step
    tp3_qty = max(qty - tp1_qty - tp2_qty, 0.0)
    # Ajustar residuo: si tp3 no es múltiplo de step, mover a tp2
    if tp3_qty > 0 and (tp3_qty % step) > 0:
        extra = tp3_qty - math.floor(tp3_qty / step) * step
        tp3_qty = math.floor(tp3_qty / step) * step
        tp2_qty += extra
        tp2_qty = math.floor(tp2_qty / step) * step
        tp3_qty = max(qty - tp1_qty - tp2_qty, 0.0)
    # Evaluar modes — checks por notional individual vs total
    tp1_n = tp1_qty * tp1_price
    tp2_n = tp2_qty * tp2_price
    tp3_n = tp3_qty * tp2_price
    # Normal: tp1 y tp2 individuales >= min_notional
    if tp1_n >= min_notional and tp2_n >= min_notional:
        mode = 'normal'
    elif tp1_n >= min_notional and (tp2_qty + tp3_qty) * tp2_price >= min_notional:
        # tp2 individual no llega, pero combinado sí → merge
        merged = tp2_qty + tp3_qty
        tp2_qty = math.floor(merged / step) * step
        tp3_qty = max(qty - tp1_qty - tp2_qty, 0.0)
        if tp2_qty < step:
            tp1_qty = math.floor(qty / step) * step
            tp2_qty = 0.0; tp3_qty = 0.0; mode = 'fallback'
        else:
            mode = 'merge'
    elif tp1_n >= min_notional:
        # ni tp2 ni tp3 alcanzan → fallback todo a tp1
        tp1_qty = math.floor(qty / step) * step
        tp2_qty = 0.0
        tp3_qty = 0.0
        mode = 'fallback'
    else:
        # tp1 tampoco alcanza → fallback todo a tp1 completo
        tp1_qty = math.floor(qty / step) * step
        tp2_qty = 0.0
        tp3_qty = 0.0
        mode = 'fallback'
    return {'tp1_qty': tp1_qty, 'tp2_qty': tp2_qty, 'tp3_qty': tp3_qty,
            'tp1_pct': tp1_pct, 'tp2_pct': tp2_pct, 'mode': mode}

# ── F12b: TPs PnL-BASED ──
def calcular_tps_en_zonas(ep, atr, fobs, obls, es_long, leverage=LEVERAGE, slp=0.0):
    if not (math.isfinite(ep) and ep > 0): return 0,0,0,0,0
    a = atr if (math.isfinite(atr) and atr > 0) else ep*0.01
    lv = leverage if (math.isfinite(leverage) and leverage > 0) else LEVERAGE
    ds = abs(ep-slp) if slp > 0 else a*LOBO_SL_ATR
    s = 1 if es_long else -1
    t1d, t2d, t3d = ep*TP1_PNL_TARGET/lv, ep*TP2_PNL_TARGET/lv, ep*TP3_PNL_TARGET/lv
    t1, t2, t3 = ep+s*t1d, ep+s*t2d, ep+s*t3d
    md = a*0.3
    if es_long:
        t1 = max(t1, ep+md); t2 = max(t2, t1+md*0.5); t3 = max(t3, t2+md)
    else:
        t1 = min(t1, ep-md); t2 = min(t2, t1-md*0.5); t3 = min(t3, t2-md)
    rr = t1d/ds if ds > 0 else 0
    return t1, t2, t3, rr, ds

# ── F7: TIMING ──
_ULTIMA_VELA_EVALUADA = {}
def es_nueva_vela_principal(df, sym=''):
    if df is None or df.empty or len(df) < 2: return False
    uts = int(df['timestamp'].iloc[-1]); ah = int(time.time()*1000); dm = ah-uts
    if not (0 < dm <= 1_200_000): return False
    if sym:
        if _ULTIMA_VELA_EVALUADA.get(sym) == uts: return False
        _ULTIMA_VELA_EVALUADA[sym] = uts
    return True

# ── PATRONES v4 ──
def detectar_expanded_flat(df, es_long):
    l,r = 5,5
    if len(df) < l+r+10: return {'encontrado':False,'razon':'pocos_datos'}
    hs,ls,cs,os = df['high'].values,df['low'].values,df['close'].values,df['open'].values
    phi,pli = find_pivots(df,l,r)
    if len(phi)<2 or len(pli)<2: return {'encontrado':False,'razon':'pocos_pivots'}
    if es_long:
        for ia in range(len(pli)):
            a = pli[ia]; la = ls[a]
            for ib in range(ia+1,min(ia+4,len(phi))):
                b = phi[ib]
                if b<=a: continue
                lb = hs[b]
                if lb<=la: continue
                for ic in range(ib+1,min(ib+4,len(pli))):
                    c2 = pli[ic]
                    if c2<=b: continue
                    lc = ls[c2]
                    if lc<la:
                        vr = hs[c2]-ls[c2]
                        if vr>0:
                            mi = min(os[c2],cs[c2])-ls[c2]
                            if mi/vr >= 0.15:
                                return {'encontrado':True,'tipo':'exp_flat_long','nivel_a':float(la),
                                    'nivel_c':float(lc),'nivel_b':float(lb),
                                    'distancia_ab':round((lb-la)/la*100,2),'mecha_c_ratio':round(mi/vr,2)}
    else:
        for ia in range(len(phi)):
            a = phi[ia]; la = hs[a]
            for ib in range(ia+1,min(ia+4,len(pli))):
                b = pli[ib]
                if b<=a: continue
                lb = ls[b]
                if lb>=la: continue
                for ic in range(ib+1,min(ib+4,len(phi))):
                    c2 = phi[ic]
                    if c2<=b: continue
                    lc = hs[c2]
                    if lc>la:
                        vr = hs[c2]-ls[c2]
                        if vr>0:
                            ms = hs[c2]-max(os[c2],cs[c2])
                            if ms/vr >= 0.15:
                                return {'encontrado':True,'tipo':'exp_flat_short','nivel_a':float(la),
                                    'nivel_c':float(lc),'nivel_b':float(lb),
                                    'distancia_ab':round((la-lb)/la*100,2),'mecha_c_ratio':round(ms/vr,2)}
    return {'encontrado':False}

def detectar_choch(df, es_long):
    if len(df) < LOBO_CHOCH_LOOKBACK: return {'choch':False}
    hs,ls,cs = df['high'].values,df['low'].values,df['close'].values
    phi,pli = find_pivots(df,3,3)
    if len(phi)<3 or len(pli)<2: return {'choch':False}
    if es_long:
        uh = [(i,hs[i]) for i in phi[-4:]]
        if len(uh)<3: return {'choch':False}
        if sum(1 for j in range(len(uh)-1) if uh[j][1]>uh[j+1][1]) < 2: return {'choch':False}
        nc = uh[-1][1]
        if cs[-1]>nc:
            b = cs[-1]-df['open'].iloc[-1]; rn = hs[-1]-ls[-1]
            if rn>0 and b/rn>0.3: return {'choch':True,'tipo':'bullish_choch','nivel_roto':float(nc),'pullback_confirmado':False}
    else:
        ul = [(i,ls[i]) for i in pli[-4:]]
        if len(ul)<3: return {'choch':False}
        if sum(1 for j in range(len(ul)-1) if ul[j][1]<ul[j+1][1]) < 2: return {'choch':False}
        nc = ul[-1][1]
        if cs[-1]<nc:
            b = df['open'].iloc[-1]-cs[-1]; rn = hs[-1]-ls[-1]
            if rn>0 and b/rn>0.3: return {'choch':True,'tipo':'bearish_choch','nivel_roto':float(nc),'pullback_confirmado':False}
    return {'choch':False}

def verificar_microfractalidad(df):
    if len(df) < LOBO_MICRO_LOOKBACK: return {'completo':False,'razon':'pocos_datos'}
    hs,ls = df['high'].values,df['low'].values
    phi,pli = find_pivots(df,3,3)
    pivots = sorted([(i,'high',hs[i]) for i in phi[-8:]]+[(i,'low',ls[i]) for i in pli[-8:]],key=lambda x:x[0])
    if len(pivots)<5: return {'completo':False,'razon':'pocos_pivots'}
    ondas = 1
    for j in range(1,len(pivots)):
        if pivots[j][1] != pivots[j-1][1]: ondas += 1
        else: break
    if ondas >= 5:
        tipo = 'impulsivo_alcista' if pivots[-1][2]>pivots[0][2] else 'impulsivo_bajista' if pivots[-1][2]<pivots[0][2] else 'zigzag'
        return {'completo':True,'ondas':ondas,'tipo':tipo}
    return {'completo':False,'ondas':ondas}

def detectar_flat_continuacion(df, es_long):
    if len(df)<15: return False
    n=len(df); av=_atr(df,LOBO_ATR_PERIOD)
    lb=min(20,n-LOBO_FLAT_MIN_VELAS-5)
    z = df.iloc[-(lb+LOBO_FLAT_MIN_VELAS):-LOBO_FLAT_MIN_VELAS]
    cu = df.iloc[-LOBO_FLAT_MIN_VELAS:]
    if len(z)<5 or len(cu)<LOBO_FLAT_MIN_VELAS: return False
    aa = av.iloc[-LOBO_FLAT_MIN_VELAS:].mean()
    if pd.isna(aa) or aa<=0: return False
    if es_long:
        r = z['high'].iloc[:-1].max(); rv = z[z['close']>r]
        if rv.empty: return False
        mr = rv['low'].min()
        for _,v in cu.iterrows():
            if v['low'] < mr*0.995: return False
        return (cu['high'].max()-cu['low'].min()) < aa*LOBO_FLAT_MAX_ATR
    else:
        s = z['low'].iloc[:-1].min(); rv = z[z['close']<s]
        if rv.empty: return False
        xr = rv['high'].max()
        for _,v in cu.iterrows():
            if v['high'] > xr*1.005: return False
        return (cu['high'].max()-cu['low'].min()) < aa*LOBO_FLAT_MAX_ATR

def check_btcd_elliott_ventana_altcoins(df_btcd=None):
    r = {'ventana_altcoins':False,'btcd_bajista':False,'elliott_completo':False}
    if check_dominancia_btc_long(): return r
    r['btcd_bajista'] = True
    if df_btcd is not None and len(df_btcd) >= LOBO_BTCD_ELLOTT_LOOKBACK:
        de = df_btcd
        if 'high' not in de.columns and 'h' in de.columns:
            de = de.rename(columns={'ts':'timestamp','o':'open','h':'high','l':'low','c':'close','v':'volume'})
        el = detectar_estructura_elliott_v3(de)
        if el.get('fase')=='estructura_5_ondas' and el.get('ultimo_pivot')=='maximo':
            r['elliott_completo'] = True
    r['ventana_altcoins'] = True
    return r

def debe_validar_h4():
    now_utc = datetime.now(timezone.utc)
    return now_utc.hour % 4 == 0 and now_utc.minute <= 5

def check_regime_tendencia(df, es_long, df_d1=None):
    if not LOBO_REGIME_FILTER: return True, 'REGIME:off'
    try:
        if df is None or 'close' not in df.columns: return True, 'REGIME:sin_datos'
        c4 = df['close'].dropna(); mr = max(LOBO_REGIME_EMA_PERIOD//2, 10)
        if len(c4) < mr: return True, 'REGIME:sin_datos'
        e4 = _ema(c4, LOBO_REGIME_EMA_PERIOD)
        up4 = bool(float(c4.iloc[-1]) > float(e4.iloc[-1])) if not pd.isna(e4.iloc[-1]) else bool(float(c4.iloc[-1]) > float(c4.mean()))
        aligned = True
        if df_d1 is not None and 'close' in df_d1.columns and len(df_d1) >= mr:
            c1 = df_d1['close'].dropna(); e1 = _ema(c1, LOBO_REGIME_EMA_PERIOD)
            up1 = bool(float(c1.iloc[-1]) > float(e1.iloc[-1])) if not pd.isna(e1.iloc[-1]) else bool(float(c1.iloc[-1]) > float(c1.mean()))
            aligned = (up4 == up1)
        allow = (up4 if es_long else (not up4)) and aligned
        return allow, f'REGIME:{("LONG_ok" if es_long else "SHORT_ok") if allow else "BLOQUEADO"}:4h{"UP" if up4 else "DN"}'
    except: return True, 'REGIME:error'

# ── 17. EVALUACION COMPLETA DE SENAL (22 pts max) ──
def evaluar_senal_bitlobo_v4(sym, dfp, dfc, pa, atr, bt, es_long, dfm=None, va=None, mrd=None, dfd1=None):
    side_lbl = 'LONG' if es_long else 'SHORT'
    cf = capital_disponible_futuros(bt)
    ce = mrd if mrd is not None else cf
    s = {'symbol':sym,'precio_actual':pa,'atr_val':atr,'es_long':es_long}
    d = []; sc = 0; ms = 22
    sr = {'regime':0,'impulso':0,'fibo_zone':0,'sma100':0,'adx':0,'usdtd':0,'btcd':0,
          'fvg':0,'ob':0,'sweep':0,'mecha':0,'rsi':0,'vol':0,'pullback':0,'elliott':0,
          'choch':0,'exp_flat':0,'micro':0,'flat_cont':0,'d1':0,'rr':0,'lev':0}
    # R0: REGIME
    ar, dr = check_regime_tendencia(dfc, es_long, dfd1)
    if not ar:
        sr['regime'] = -1
        log.debug("[EVAL-%s] %s RECHAZO: REGIME filtró (%s)", side_lbl, sym, dr)
        s['score']=sc; s['max_score']=ms; s['detalles']=[dr]; s['score_report']=sr; s['_rejected']=True
        return s
    d.append(dr); sr['regime'] = 1
    # R1: IMPULSO
    imp = detectar_impulso(dfp)
    if not imp:
        sr['impulso'] = -1
        log.debug("[EVAL-%s] %s RECHAZO: sin impulso detectado", side_lbl, sym)
        s['score']=sc; s['max_score']=ms; s['detalles']=d; s['score_report']=sr; s['_rejected']=True
        return s
    # R1: FIBONACCI
    fb = calcular_fibonacci(imp)
    if not fb or 'level_0_5' not in fb or 'level_0_618' not in fb:
        sr['impulso'] = 1; sr['fibo_zone'] = -1
        log.debug("[EVAL-%s] %s RECHAZO: Fibonacci incompleto (fb=%s)", side_lbl, sym, bool(fb))
        s['score']=sc; s['max_score']=ms; s['detalles']=d; s['score_report']=sr; s['_rejected']=True
        return s
    s['impulso']=imp; s['fibo']=fb; sc+=1; sr['impulso']=1; sr['fibo_zone']=1
    d.append(f'R1:impulso_{imp["tipo"]}_{imp["velas"]}v')
    zi = min(fb['level_0_5'],fb['level_0_618']); zs = max(fb['level_0_5'],fb['level_0_618'])
    s['zona_ote_inf']=zi; s['zona_ote_sup']=zs; tol=atr*1.0
    # R1: PRECIO EN ZONA OTE
    if not (zi-tol <= pa <= zs+tol):
        sr['fibo_zone'] = -1
        log.debug("[EVAL-%s] %s RECHAZO: precio %.4f fuera de zona OTE [%.4f-%.4f] ± tol %.4f",
            side_lbl, sym, pa, zi, zs, tol)
        s['score']=sc; s['max_score']=ms; s['detalles']=d; s['score_report']=sr; s['_rejected']=True
        return s
    if zi <= pa <= zs: sc+=1; d.append('R1:en_OTE')
    # R2: SMA100
    if len(dfp) >= 100:
        sm = _sma(dfp['close'],100).iloc[-1]
        if not pd.isna(sm) and sma100_en_zona_ote(sm,fb,atr): sc+=1; sr['sma100']=1; d.append('R2:SMA100_en_OTE')
    # R3: ADX
    if adx_permite_entrada(dfp): sc+=1; sr['adx']=1; d.append('R3:ADX_ok')
    # R4: USDT.D
    if es_long:
        if check_usdtd_resistencia_long(): sc+=1; sr['usdtd']=1; d.append('R4:USDT.D_resistencia')
    else:
        if check_usdtd_resistencia_short(): sc+=1; sr['usdtd']=1; d.append('R4:USDT.D_debil')
    # R5: BTC.D
    bdb = va.get('btcd_bajista',False) if va else False
    if 'BTC' in sym:
        btu = False
        if len(dfp)>=20:
            sm20 = _sma(dfp['close'],20)
            if not sm20.isna().all(): btu = bool(float(dfp['close'].iloc[-1])>float(sm20.iloc[-1]))
        if btu: sc+=1; sr['btcd']=1; d.append('R5:BTC_trend_up')
        else: d.append('R5:BTC_trend_down')
    else:
        if bdb: sc+=1; sr['btcd']=1; d.append('R5:BTC.D_baja_alt_ok')
        else: d.append('R5:BTC.D_sube_bloquea_alt')
    # R6: FVG
    fvs = detectar_fvg(dfp)
    fez = [f for f in fvs if f['gap_sup']>=zi and f['gap_inf']<=zs]
    s['fvgs']=fez
    if fez: sc+=1; sr['fvg']=1; d.append(f'R6:FVG_{len(fez)}')
    # R7: ORDER BLOCKS
    obs = detectar_order_blocks(dfp)
    oez = [o for o in obs if o['low']<=zs and o['high']>=zi]
    s['obs']=oez
    if oez: sc+=1; sr['ob']=1; d.append(f'R7:OB_{len(oez)}')
    # R8: SWEEP
    sws = detectar_sweep(dfp); s['sweeps']=sws
    if sws:
        sok = any((s2['tipo']=='sweep_bajista_long' and es_long) or (s2['tipo']=='sweep_alcista_short' and not es_long) for s2 in sws)
        if sok: sc+=1; sr['sweep']=1; d.append('R8:Sweep')
    # R9: MECHA / ABSORCION
    mk, md = validar_mecha_absorcion_en_zona(dfp, zi, zs, es_long, atr)
    if not mk:
        sr['mecha'] = -1
        log.debug("[EVAL-%s] %s RECHAZO: mecha absorción falló (%s)", side_lbl, sym, md)
        s['score']=sc; s['max_score']=ms; s['detalles']=d; s['score_report']=sr; s['_rejected']=True
        return s
    sc+=1; sr['mecha']=1; d.append(f'R9:Mecha_{md}')
    # F5: RSI
    ro, rv = filtro_rsi(dfp, es_long)
    if ro: sc+=1; sr['rsi']=1; d.append(f'F5:RSI_{rv:.0f}')
    # F5: VOLUMEN
    vo, vr = validar_volumen(dfp, es_long)
    if vo: sc+=1; sr['vol']=1; d.append(f'F5:Vol_{vr:.1f}x')
    # F6: PULLBACK
    nf = zs if es_long else zi
    if detectar_pullback_confirmado(dfp, nf, es_long): sc+=1; sr['pullback']=1; d.append('F6:Pullback_ok')
    # F11: ELLIOTT
    el = detectar_estructura_elliott_v3(dfp); s['elliott']=el
    if el['fase']=='estructura_5_ondas': sc+=1; sr['elliott']=1; d.append('F11:Elliott_5ondas')
    # D3: CHoCH
    ch = detectar_choch(dfp, es_long); s['choch']=ch
    if ch.get('choch',False): sc+=1; sr['choch']=1; d.append(f'D3:{ch["tipo"]}')
    # D2: EXPANDED FLAT / DOUBLE KILL
    ef = detectar_expanded_flat(dfp, es_long); s['expanded_flat']=ef
    if ef.get('encontrado',False): sc+=2; sr['exp_flat']=2; d.append(f'D2:DoubleKill_{ef["tipo"]}')
    # D4: MICROFRACTALIDAD
    if dfm is not None and len(dfm)>0:
        mi = verificar_microfractalidad(dfm); s['microfractal']=mi
        if mi.get('completo',False):
            if (es_long and mi.get('tipo')=='impulsivo_alcista') or (not es_long and mi.get('tipo')=='impulsivo_bajista'):
                sc+=1; sr['micro']=1; d.append(f'D4:micro_{mi["tipo"]}')
    # D5: FLAT CONTINUACION
    if detectar_flat_continuacion(dfp, es_long): sc+=1; sr['flat_cont']=1; d.append('D5:flat_continuacion')
    # F10: D1 ESTRUCTURAL
    de = dfd1 if (dfd1 is not None and len(dfd1)>=10) else dfc
    if validar_estructura_d1(de, pa, 'long' if es_long else 'short'): sc+=1; sr['d1']=1; d.append('F10:D1_ok')
    else:
        sr['d1'] = -1
        log.debug("[EVAL-%s] %s RECHAZO: D1 estructura inválida", side_lbl, sym)
        s['score']=sc; s['max_score']=ms; s['detalles']=d; s['score_report']=sr; s['_rejected']=True
        return s
    # F3: LEVERAGE + SL
    alv, lp = calcular_apalancamiento_optimo(pa, dfp, zi, zs, es_long, sws, sym)
    sl = pa-(atr*LOBO_SL_ATR) if es_long else pa+(atr*LOBO_SL_ATR); s['sl_price']=sl
    if es_long:
        lm = sl-atr*1.0
        if lp >= sl: lp = lm
    else:
        lm = sl+atr*1.0
        if lp <= sl: lp = lm
    # F12b: TPs
    t1,t2,t3,rr,ds = calcular_tps_en_zonas(pa, atr, fez, oez, es_long, leverage=alv, slp=sl)
    s['tp1_price']=t1; s['tp2_price']=t2; s['tp3_price']=t3; s['rr']=rr; s['dist_sl']=ds
    if ds > 0:
        t1v,t2v,t3v = abs(t1-pa),abs(t2-pa),abs(t3-pa)
        rrp = (0.40*t1v+0.30*t2v+0.30*t3v)/ds
    else: rrp = rr
    # R:R MINIMO
    if rrp < 1.0:
        sr['rr'] = -1
        log.debug("[EVAL-%s] %s RECHAZO: R:R promedio %.2f < 1.0", side_lbl, sym, rrp)
        s['score']=sc; s['max_score']=ms; s['detalles']=d; s['score_report']=sr; s['_rejected']=True
        return s
    rr = rrp
    if rr >= 1.2: sc+=1; sr['rr']=1; d.append(f'R13:R:R_{rr:.2f}')
    # F3: SIZING
    rc = ce*LOBO_RISK_PCT; ds2 = abs(pa-sl)/pa
    if ds2 <= 0:
        sr['lev'] = -1
        log.debug("[EVAL-%s] %s RECHAZO: dist_SL/precio = 0", side_lbl, sym)
        s['score']=sc; s['max_score']=ms; s['detalles']=d; s['score_report']=sr; s['_rejected']=True
        return s
    pv = rc/ds2; mmx = ce*0.90
    if alv > 0: pv = min(pv, mmx*alv)
    mmin = MIN_ORDER_USDT/alv if alv > 0 else MIN_ORDER_USDT
    if ce < mmin:
        sr['lev'] = -1
        log.debug("[EVAL-%s] %s RECHAZO: capital elegible %.2f < minimo %.2f", side_lbl, sym, ce, mmin)
        s['score']=sc; s['max_score']=ms; s['detalles']=d; s['score_report']=sr; s['_rejected']=True
        return s
    # F12a: pv mínimo para que TP1 (40%) cumpla MIN_ORDER_USDT individual
    min_pv_for_tp = MIN_ORDER_USDT / max(TP1_CLOSE_PCT, 0.01)
    if pv < min_pv_for_tp:
        margin_needed = min_pv_for_tp / alv if alv > 0 else min_pv_for_tp
        risk_if_forced = (min_pv_for_tp * ds2) / max(ce, 0.01) * 100
        if margin_needed <= ce * 0.90 and risk_if_forced <= 15.0:
            pv = min_pv_for_tp
        else:
            sr['lev'] = -1
            log.debug("[SIZING] %s pv=%.2f < min_tp=%.2f (ce=%.2f riskWould=%.1f%%) — skip",
                sym, pv, min_pv_for_tp, ce, risk_if_forced)
            s['score']=sc; s['max_score']=ms; s['detalles']=d; s['score_report']=sr; s['_rejected']=True
            return s
    qty = pv/pa; mr = pv/alv if alv > 0 else 0
    s['qty']=qty; s['pos_value']=pv; s['liq_price']=lp; s['size_usdt']=mr
    s['leverage_calculado']=alv; s['riesgo_real_pct']=round((pv*ds2)/max(ce,0.01)*100,2)
    sc+=1; sr['lev']=1; d.append(f'F3:lev{alv:.0f}x_mrg{mr:.2f}')
    # ── SCORE MINIMO ──
    s['score']=sc; s['max_score']=ms; s['detalles']=d; s['fvg_usado']=fez[0] if fez else None
    s['score_report']=sr
    if sc < LOBO_SCORE_MIN:
        s['_rejected']=True
        log.debug("[EVAL-%s] %s RECHAZO: score %d/%d < min %d | %s",
            side_lbl, sym, sc, ms, LOBO_SCORE_MIN, ' | '.join(d))
    else:
        s['_rejected']=False
        log.info("[EVAL-%s] %s SEÑAL OK score=%d/%d | %s", side_lbl, sym, sc, ms, ' | '.join(d))
    return s

# ── 17b. SCORE REPORT POR SIMBOLO ──
SCORE_LABELS = {
    'regime':'REGIME','impulso':'Impulso','fibo_zone':'Fibo+ZonaOTE',
    'sma100':'SMA100','adx':'ADX','usdtd':'USDT.D','btcd':'BTC.D',
    'fvg':'FVG','ob':'OrderBlock','sweep':'Sweep','mecha':'MechaAbs',
    'rsi':'RSI','vol':'Volumen','pullback':'Pullback','elliott':'Elliott',
    'choch':'CHoCH','exp_flat':'ExpFlat','micro':'MicroFrac',
    'flat_cont':'FlatCont','d1':'D1_Estruct','rr':'R:R','lev':'Lev sizing'
}

def log_score_report(sym, es_long, result):
    """Log detallado de scores por símbolo (tomado o rechazado)."""
    if not result or not isinstance(result, dict):
        log.info("[SCORE-?] %s %s | result_invalid=%s", sym, 'LONG' if es_long else 'SHORT', type(result))
        return
    side = 'LONG' if es_long else 'SHORT'
    sc = result.get('score', 0)
    ms = result.get('max_score', 22)
    sr = result.get('score_report', {}) or {}
    d = result.get('detalles', []) or []
    rejected = result.get('_rejected', True)
    status = 'TOMADA' if not rejected else 'RECHAZADA'
    won = [k for k, v in sr.items() if v > 0]
    lost = [k for k, v in sr.items() if v < 0]
    off  = [k for k, v in sr.items() if v == 0]
    won_s = '+'.join(SCORE_LABELS.get(k, k) for k in won) if won else 'ninguno'
    lost_s = '+'.join(SCORE_LABELS.get(k, k) for k in lost) if lost else 'ninguno'
    off_s  = '+'.join(SCORE_LABELS.get(k, k) for k in off) if off else 'todos'
    log.info("[SCORE-%s] %s %s | score=%d/%d | +%s | -%s | off=%s | %s",
        status, sym, side, sc, ms, won_s, lost_s, off_s,
        ' | '.join(d) if d else 'sin_detalle')


# ── 18. TELEGRAM ──
def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id":TELEGRAM_CHAT_ID,"text":msg,"parse_mode":"Markdown"}, timeout=10)
        log.info("Telegram: %s ...", msg[:80].replace('\n',' '))
    except Exception as e: log.warning("Telegram fallo: %s", e)

# ── 19. CSV LOGGING ──
TCV3 = ['entry_time','exit_time','symbol','side','entry_price','exit_price','sl_price','tp1_price',
    'tp2_price','tp3_price','liq_price','leverage_used','sl_pct','tp_pct','quantity','capital_total',
    'capital_futuros','balance_before','balance_after','pnl','fees','net_pnl','status','duration_hours',
    'signal_score','rr','atr_at_entry','close_reason','be_triggered','be_price','trail_count',
    'trail_peak_price','trail_final_sl','entry_weekday','entry_hour','size_usdt','risk_pct',
    'hedge_active','max_favorable_pct','max_adverse_pct']

def guardar_trade_csv(entry, ep, rpnl, fees, net, status, cr):
    if not entry: return
    if fees == 0 and FEE_TAKER > 0:
        qf = float(entry.get('quantity',0) or entry.get('remaining_qty',0) or 0)
        fees = abs(ep*qf)*FEE_TAKER; net = rpnl-fees
    global CONSECUTIVE_LOSSES
    if status in ('TP3','EXCHANGE_CLOSE') and cr not in ('tp1_exchange','tp2_exchange'):
        CONSECUTIVE_LOSSES = 0
    elif status in ('SL','LIQ','Timeout','D1_INVALID'):
        if net < 0: CONSECUTIVE_LOSSES += 1
        else: CONSECUTIVE_LOSSES = 0
    now = datetime.now(); dur = (now-entry['entry_time']).total_seconds()/3600
    ba = entry.get('balance_before',0)+net; epx=entry['entry_price']; sl=entry.get('sl_price',0); sd=entry.get('side','long')
    row = {'entry_time':entry['entry_time'].strftime('%Y-%m-%d %H:%M:%S'),'exit_time':now.strftime('%Y-%m-%d %H:%M:%S'),
        'symbol':entry['symbol'],'side':sd,'entry_price':epx,'exit_price':ep,'sl_price':sl,
        'tp1_price':entry.get('tp1_price',0),'tp2_price':entry.get('tp2_price',0),'tp3_price':entry.get('tp3_price',0),
        'liq_price':entry.get('liq_price',0),'leverage_used':round(entry.get('leverage',0),1),
        'sl_pct':round(abs(epx-sl)/epx*100,2) if sl else 0,
        'tp_pct':round(abs(epx-entry.get('tp1_price',epx))/epx*100,2),
        'quantity':entry.get('quantity',0),'capital_total':round(entry.get('balance_before',0),2),
        'capital_futuros':round(entry.get('capital_futuros',0),2),'balance_before':round(entry.get('balance_before',0),2),
        'balance_after':round(ba,2),'pnl':round(rpnl,2),'fees':round(fees,2),'net_pnl':round(net,2),
        'status':status,'duration_hours':round(dur,2),'signal_score':entry.get('score',0),
        'rr':entry.get('rr',0),'atr_at_entry':round(entry.get('atr_val',0),2),'close_reason':cr,
        'be_triggered':1 if ALERTS_HISTORY.get(f"{entry['symbol']}_be",False) else 0,
        'be_price':round(ALERTS_HISTORY.get(f"{entry['symbol']}_be_price",0),4),
        'trail_count':TRAIL_COUNTS.get(entry['symbol'],0),
        'trail_peak_price':round(PEAK_PRICES.get(entry['symbol'],epx),4),
        'trail_final_sl':round(ALERTS_HISTORY.get(f"{entry['symbol']}_trail",sl),4),
        'entry_weekday':entry['entry_time'].weekday(),'entry_hour':entry['entry_time'].hour,
        'size_usdt':entry.get('size_usdt',0),'risk_pct':entry.get('risk_pct',0),
        'hedge_active':1 if HEDGE_ENTRIES.get(entry['symbol']) else 0,
        'max_favorable_pct':round(abs(PEAK_PRICES.get(entry['symbol'],epx)-epx)/epx*100,2),
        'max_adverse_pct':round(abs(ADVERSE_PRICES.get(entry['symbol'],epx)-epx)/epx*100,2)}
    wh = not os.path.exists(TRADES_CSV_PATH)
    try:
        with open(TRADES_CSV_PATH,'a',newline='',encoding='utf-8') as f:
            w = csv.DictWriter(f,fieldnames=TCV3)
            if wh: w.writeheader()
            w.writerow(row)
    except Exception as e: log.error("CRITICO: No se pudo guardar trade CSV: %s", e)

SLV3 = ['time','symbol','side','price','score','max_score','detalles','rr','atr','entry_zone_fibo',
    'sl_proj','liq_price','leverage','tp1_proj','tp2_proj','tp3_proj','taken','reason_skipped']

def guardar_signal_log(sym, sd, pr, sc, ms, det, sl, lp, lv, t1, t2, t3, rr, taken=True, rs=''):
    row = {'time':datetime.now().strftime('%Y-%m-%d %H:%M:%S'),'symbol':sym,'side':sd,'price':round(pr,6),
        'score':sc,'max_score':ms,'detalles':' | '.join(det) if det else '','rr':round(rr,2),'atr':0,
        'entry_zone_fibo':'','sl_proj':round(sl,6) if sl else 0,'liq_price':round(lp,6) if lp else 0,
        'leverage':round(lv,1) if lv else 0,'tp1_proj':round(t1,6) if t1 else 0,'tp2_proj':round(t2,6) if t2 else 0,
        'tp3_proj':round(t3,6) if t3 else 0,'taken':'Yes' if taken else 'No','reason_skipped':rs}
    wh = not os.path.exists(SIGNALS_LOG_PATH)
    try:
        with open(SIGNALS_LOG_PATH,'a',newline='',encoding='utf-8') as f:
            w = csv.DictWriter(f,fieldnames=SLV3)
            if wh: w.writeheader()
            w.writerow(row)
    except Exception as e: log.error("CRITICO: No se pudo guardar signal log CSV: %s", e)

# ── 20. FETCH ASINCRONO ──
_ASYNC_EXCH: Optional[ccxt_async.bitget] = None
_ASYNC_LOOP: Optional[asyncio.AbstractEventLoop] = None

def _get_async_loop():
    global _ASYNC_LOOP
    if _ASYNC_LOOP is None or _ASYNC_LOOP.is_closed():
        _ASYNC_LOOP = asyncio.new_event_loop()
    return _ASYNC_LOOP

async def _fetch_symbol_async(exch, sym):
    le = None
    for att in range(3):
        try:
            o15 = await asyncio.wait_for(exch.fetch_ohlcv(sym, timeframe=TIMEFRAME_PRINCIPAL, limit=200), FETCH_TIMEOUT_S)
            o4h = await asyncio.wait_for(exch.fetch_ohlcv(sym, timeframe=TIMEFRAME_CONFIRMACION, limit=100), FETCH_TIMEOUT_S)
            o5m = await asyncio.wait_for(exch.fetch_ohlcv(sym, timeframe=TIMEFRAME_MICRO, limit=200), FETCH_TIMEOUT_S)
            o1d = await asyncio.wait_for(exch.fetch_ohlcv(sym, timeframe='1d', limit=60), FETCH_TIMEOUT_S)
            return sym, o15, o4h, o5m, o1d
        except (ccxt_async.RateLimitExceeded, ccxt_async.ExchangeNotAvailable) as e:
            le=str(e); w=2**att; log.warning("RL/NA %s (att %d/3): retry %ds",sym,att+1,w); await asyncio.sleep(w)
        except asyncio.TimeoutError:
            le='timeout'
            if att==0: log.warning("Timeout %s (att %d/2)",sym,att+1); await asyncio.sleep(0.5)
            else: break
        except: return sym, None, None, None, None
    if le: log.warning("Fetch fallo %s: %s",sym,le)
    return sym, None, None, None, None

async def _fetch_all_async(symbols):
    global _ASYNC_EXCH
    if _ASYNC_EXCH is None:
        _ASYNC_EXCH = ccxt_async.bitget({'apiKey':API_KEY,'secret':SECRET_KEY,'password':PASSPHRASE,
            'enableRateLimit':True,'options':{'defaultType':'swap'}})
    sem = asyncio.Semaphore(FETCH_CONCURRENCY)
    async def _w(s):
        async with sem: return await _fetch_symbol_async(_ASYNC_EXCH, s)
    return await asyncio.gather(*[_w(s) for s in symbols])

def fetch_all_ohlcv(symbols):
    global _ASYNC_EXCH
    loop = _get_async_loop()
    try:
        results = loop.run_until_complete(_fetch_all_async(symbols))
    except Exception as e:
        log.error("Error fetch_all_async: %s", e)
        return {}
    finally:
        pass  # Mantener exchange abierto para reusar
    return {r[0]:(r[1],r[2],r[3],r[4]) for r in results}

def _close_async_exchange():
    global _ASYNC_EXCH, _ASYNC_LOOP
    if _ASYNC_EXCH:
        try: loop = _get_async_loop(); loop.run_until_complete(_ASYNC_EXCH.close())
        except: pass
        _ASYNC_EXCH = None
    if _ASYNC_LOOP and not _ASYNC_LOOP.is_closed():
        try: _ASYNC_LOOP.close()
        except: pass
        _ASYNC_LOOP = None

# ── 21. EXCHANGE ──
exchange: ccxt.bitget | None = None

def init_exchange() -> bool:
    global exchange
    if PAPER_TRADE:
        log.info("PAPER_TRADE v4 activo")
        try:
            exchange = ccxt.bitget({'enableRateLimit':True,'options':{'defaultType':'swap'}})
            exchange.load_markets(); log.info("Exchange paper listo (%d mercados)",len(exchange.markets)); return True
        except Exception as e: log.critical("Error exchange paper: %s",e); return False
    if not API_KEY or not SECRET_KEY or not PASSPHRASE: log.critical("API keys missing"); return False
    try:
        exchange = ccxt.bitget({'apiKey':API_KEY,'secret':SECRET_KEY,'password':PASSPHRASE,
            'enableRateLimit':True,'options':{'defaultType':'swap'}})
        log.info("Conexion Bitget exitosa"); return True
    except Exception as e: log.critical("Error conectando Bitget: %s",e); return False

# ── 22. TP/SL PLAN ORDERS ──
def _place_tp_plan(sym, tp, qty, side, max_retries=3, refresh=True):
    """Retorna (bool_ok, str_error). Si ok=True, error=''. Si ok=False, error='Bitget code=XXXX: msg'."""
    last_err = ''
    if not exchange or PAPER_TRADE: return False, 'paper_mode'
    for att in range(1, max_retries+1):
        try:
            mi = exchange.market(sym)
            params = {'marginCoin':mi['settleId'],'productType':'usdt-futures','symbol':mi['id'].lower(),
                'planType':'profit_plan','triggerPrice':exchange.price_to_precision(sym,tp),
                'triggerType':'mark_price','holdSide':side,'size':exchange.amount_to_precision(sym,qty)}
            resp = exchange.privateMixPostV2MixOrderPlaceTpslOrder(params)
            if isinstance(resp,dict):
                rc = int(str(resp.get('code','0')))
                if rc != 0:
                    if rc == 43030: return True, ''
                    last_err = f"code={rc}: {resp.get('msg','?')}"
                    raise ccxt.ExchangeError(last_err)
            return True, ''
        except Exception as e:
            ls = str(e)
            if '43030' in ls: return True, ''
            if refresh and any(c in ls for c in ('45060','45061','45064','45065')):
                try: mk = float(exchange.fetch_ticker(sym).get('last',0))
                except: mk = 0
                if mk > 0:
                    tp = max(tp,mk*1.0015) if side=='long' else min(tp,mk*0.9985)
                    tp = float(exchange.price_to_precision(sym,tp))
            last_err = str(e)[:120]
            if att < max_retries: time.sleep(2**att)
            else: log.error("TP plan FAILED %s @ %s: %s",sym,tp,e)
    return False, last_err

def _cancel_tp_plans(sym):
    if not exchange or PAPER_TRADE: return
    try:
        mi = exchange.market(sym)
        p = {'productType':'usdt-futures','symbol':mi['id'].lower(),'planType':'profit_plan'}
        for plan in (exchange.privateMixGetV2MixOrderOrdersPending(p).get('data',{}).get('entrustedList',[]) or []):
            if plan.get('planType')=='profit_plan':
                exchange.privateMixPostV2MixOrderCancelTpslOrder({'symbol':mi['id'].lower(),'productType':'usdt-futures',
                    'marginCoin':mi['settleId'],'planType':'profit_plan','orderId':plan['orderId']})
    except: pass

def _cancel_sl_plans(sym):
    if not exchange or PAPER_TRADE: return
    try:
        mi = exchange.market(sym)
        p = {'productType':'usdt-futures','symbol':mi['id'].lower(),'planType':'loss_plan'}
        for plan in (exchange.privateMixGetV2MixOrderOrdersPending(p).get('data',{}).get('entrustedList',[]) or []):
            if plan.get('planType')=='loss_plan':
                exchange.privateMixPostV2MixOrderCancelTpslOrder({'symbol':mi['id'].lower(),'productType':'usdt-futures',
                    'marginCoin':mi['settleId'],'planType':'loss_plan','orderId':plan['orderId']})
    except: pass

def _place_sl_plan(sym, sl, qty, side, max_retries=3):
    if not exchange or PAPER_TRADE: return False
    for att in range(1, max_retries+1):
        try:
            mi = exchange.market(sym)
            params = {'marginCoin':mi['settleId'],'productType':'usdt-futures','symbol':mi['id'].lower(),
                'planType':'loss_plan','triggerPrice':exchange.price_to_precision(sym,sl),
                'triggerType':'mark_price','holdSide':side,'size':exchange.amount_to_precision(sym,qty)}
            resp = exchange.privateMixPostV2MixOrderPlaceTpslOrder(params)
            if isinstance(resp,dict):
                rc = int(str(resp.get('code','0')))
                if rc != 0:
                    if rc == 43030: return True
                    raise ccxt.ExchangeError(f"Bitget code={rc}: {resp.get('msg','?')}")
            return True
        except Exception as e:
            if '43030' in str(e): return True
            if att < max_retries: time.sleep(2**att)
            else: log.error("SL plan FAILED %s @ %s: %s",sym,sl,e)
    return False

def _diagnose_tp_plans(sym, ep=0, el=0):
    r = {'profit_plans':0,'loss_plans':0,'ok':False}
    if not exchange or PAPER_TRADE: return r
    try:
        time.sleep(0.5); mi = exchange.market(sym)
        p = {'productType':'usdt-futures','symbol':mi['id'].lower()}
        for plan in (exchange.privateMixGetV2MixOrderOrdersPending(p).get('data',{}).get('entrustedList',[]) or []):
            pt = plan.get('planType','')
            if pt=='profit_plan': r['profit_plans']+=1
            elif pt=='loss_plan': r['loss_plans']+=1
        r['ok'] = ((ep<=0) or (r['profit_plans']>=ep)) and ((el<=0) or (r['loss_plans']>=el))
    except: pass
    return r

# ── 22b. SAFE FETCH WITH BACKOFF ──
_last_fetch_ts: dict = {}
_MIN_FETCH_INTERVAL_S = 2.0  # mínimo 2s entre fetches al mismo endpoint

def _safe_fetch(fn, *args, max_retries=3, label='fetch', **kwargs):
    """Wrapper con backoff exponencial y rate-limit mínimo entre llamadas."""
    key = label + '|' + str(args)
    now = time.time()
    last = _last_fetch_ts.get(key, 0)
    if now - last < _MIN_FETCH_INTERVAL_S:
        time.sleep(_MIN_FETCH_INTERVAL_S - (now - last))
    for att in range(max_retries):
        try:
            result = fn(*args, **kwargs)
            _last_fetch_ts[key] = time.time()
            return result
        except (ccxt.RateLimitExceeded, ccxt.ExchangeNotAvailable) as e:
            wait = min(2 ** att * 3, 60)
            log.warning("[SAFE] %s RateLimit/Unavailable (att %d/%d) — retry %ds: %s",
                label, att+1, max_retries, wait, str(e)[:80])
            time.sleep(wait)
        except ccxt.NetworkError as e:
            wait = min(2 ** att * 2, 30)
            log.warning("[SAFE] %s NetworkError (att %d/%d) — retry %ds: %s",
                label, att+1, max_retries, wait, str(e)[:80])
            time.sleep(wait)
        except Exception as e:
            log.error("[SAFE] %s Error fatal: %s", label, str(e)[:120])
            return None
    log.error("[SAFE] %s agotó %d reintentos", label, max_retries)
    return None

def _safe_fetch_balance():
    r = _safe_fetch(exchange.fetch_balance, label='fetch_balance')
    if r is None: return None
    return float(r.get('total',{}).get('USDT',0))

def _safe_fetch_positions():
    r = _safe_fetch(exchange.fetch_positions, label='fetch_positions')
    return r if r is not None else []

# ── 23. UTILIDADES DE POSICIONES ──
def _calc_pnl_parcial(side, ep, qty, xp):
    return (xp-ep)*qty if side=='long' else (ep-xp)*qty

def _fetch_plans_exchange(sym):
    p,l = [],[]
    if not exchange or PAPER_TRADE: return p,l
    try:
        mi = exchange.market(sym); pa = {'productType':'usdt-futures','symbol':mi['id'].lower()}
        for pt,bk in (('profit_plan',p),('loss_plan',l)):
            for plan in (exchange.privateMixGetV2MixOrderOrdersPending(dict(pa,planType=pt)).get('data',{}).get('entrustedList',[]) or []):
                if plan.get('planType')==pt:
                    try: bk.append({'triggerPrice':float(plan.get('triggerPrice',0)),'size':float(plan.get('size',0))})
                    except: pass
    except: pass
    return p,l

def _atr_est_15m(sym, ep):
    d = ep*0.01
    if not exchange or PAPER_TRADE: return d
    try:
        oh = exchange.fetch_ohlcv(sym,'15m',100)
        if not oh or len(oh)<20: return d
        df = pd.DataFrame(oh,columns=['ts','open','high','low','close','volume'])
        v = float(_atr(df,14).dropna().iloc[-1])
        return v if v > 0 else d
    except: return d

def _sl_desde_posicion(pos, side, ep):
    try: sl = float(pos.get('stopLossPrice') or 0)
    except: return None
    if sl <= 0: return None
    if side=='long' and sl >= ep: return None
    if side=='short' and sl <= ep: return None
    return sl

def _cerrar_pos_real(sym, side, qty):
    """Cierra posición real con verificación. Retorna True solo si confirmado."""
    cs = 'sell' if side=='long' else 'buy'
    # Intentar con tradeSide (hedge mode) Y reduceOnly (one-way mode)
    params_base = {'marginCoin':'USDT','marginMode':'isolated'}
    for attempt in range(3):
        try:
            # Primer intento: hedge mode (tradeSide)
            resp = exchange.create_order(sym,'market',cs,qty,
                params={**params_base,'tradeSide':'close'})
            log.info("[REAL] %s close order sent (hedge), resp=%s", sym,
                     str(resp)[:200] if resp else 'None')
            # Verificar que se ejecutó
            if _verify_position_closed(sym):
                return True
            # Si no se cerró, intentar con reduceOnly (one-way mode)
            log.warning("[REAL] %s hedge close no confirmado, intentando reduceOnly", sym)
            resp2 = exchange.create_order(sym,'market',cs,qty,
                params={**params_base,'reduceOnly':True})
            log.info("[REAL] %s close order sent (reduceOnly), resp=%s", sym,
                     str(resp2)[:200] if resp2 else 'None')
            if _verify_position_closed(sym):
                return True
            # Si aún no, intentar closePosition de ccxt
            log.warning("[REAL] %s reduceOnly no confirmado, intentando closePosition", sym)
            try:
                exchange.close_position(sym)
                if _verify_position_closed(sym):
                    return True
            except Exception as ecp:
                log.warning("[REAL] %s closePosition fallo: %s", sym, ecp)
        except ccxt.ExchangeError as e:
            es = str(e)
            if '22002' in es or 'No position' in es or 'no position' in es.lower():
                log.info("[REAL] %s ya cerrada en exchange (code match): %s", sym, es[:100])
                return True
            log.error("[REAL] %s ExchangeError att %d: %s", sym, attempt+1, es[:200])
            if attempt < 2: time.sleep(2 ** attempt)
        except Exception as e:
            log.error("[REAL] %s Error att %d: %s", sym, attempt+1, str(e)[:200])
            if attempt < 2: time.sleep(2 ** attempt)
    # Verificación final
    if _verify_position_closed(sym):
        log.info("[REAL] %s confirmada cerrada tras reintentos", sym)
        return True
    log.error("[REAL] %s FALLO cerrar tras 3 intentos — POSICIÓN ABIERTA", sym)
    return False

def _verify_position_closed(sym, max_wait=5):
    """Verifica que la posición ya no existe en el exchange."""
    try:
        time.sleep(max_wait)
        for pos in exchange.fetch_positions([sym]):
            ct = float(pos.get('contracts', 0) or 0)
            if ct > 0:
                log.warning("[VERIFY] %s aún abierta: ct=%.6f", sym, ct)
                return False
        log.info("[VERIFY] %s cerrada confirmada", sym)
        return True
    except Exception as e:
        log.warning("[VERIFY] %s error verificando: %s", sym, e)
        return False  # No asumir cerrada

def _update_sl_to_be(sym, entry, nsl, reason='BE'):
    if not exchange or PAPER_TRADE:
        entry['sl_price']=nsl
        if reason=='BE': ALERTS_HISTORY[f"{sym}_be_price"]=nsl
        elif reason=='TRAIL': ALERTS_HISTORY[f"{sym}_trail"]=nsl
        return True
    side = entry.get('side','long'); rq = float(entry.get('remaining_qty',entry.get('quantity',0)))
    if rq <= 0: return False
    try: mk = float(exchange.fetch_ticker(sym)['last'])
    except: mk = 0
    if mk > 0:
        if side=='long' and nsl >= mk:
            adj = mk*0.997
            if reason=='TRAIL' and adj < float(entry.get('sl_price',0)): return False
            nsl = adj
        elif side=='short' and nsl <= mk:
            adj = mk*1.003
            if reason=='TRAIL' and adj > float(entry.get('sl_price',999999)): return False
            nsl = adj
    _cancel_sl_plans(sym)
    ok = _place_sl_plan(sym, nsl, rq, side)
    if not ok:
        closed = _cerrar_pos_real(sym, side, rq)
        _full_cleanup(sym)
        if not closed:
            log.error("[ADOP] %s FALLO cerrar SL plan fallo — POSICION ABIERTA", sym)
            send_telegram(f"❌ *{sym} NO CERRADA* (SL plan fallo — intervenir manual)")
        else:
            send_telegram(f"❌ *{sym} CERRADA* ({reason} fallo)")
        return False
    entry['sl_price']=nsl
    if reason=='BE': ALERTS_HISTORY[f"{sym}_be_price"]=nsl
    elif reason=='TRAIL': ALERTS_HISTORY[f"{sym}_trail"]=nsl
    return True

# ── 24. ADOPTAR POSICIONES HUERFANAS ──
def adoptar_posiciones_exchange():
    if not exchange or PAPER_TRADE: return 0
    try: positions = exchange.fetch_positions()
    except Exception as e:
        log.warning("[ADOP] Error fetch_positions: %s", e)
        return 0
    active = [p for p in positions if float(p.get('contracts',0) or 0) > 0]
    log.info("[ADOP] Posiciones en exchange: %d activas de %d totales", len(active), len(positions))
    ad = 0
    for pos in active:
        sym = pos.get('symbol')
        ct = float(pos.get('contracts',0) or 0)
        sd = pos.get('side','?')
        ep = float(pos.get('entryPrice',0) or 0)
        lv = float(pos.get('leverage') or LEVERAGE)
        li = float(pos.get('liquidationPrice') or 0)
        ts = pos.get('timestamp')
        age_h = (time.time()*1000 - ts)/3600000 if ts else 0
        if not sym:
            log.warning("[ADOP] Posición sin symbol: ct=%.4f side=%s", ct, sd)
            continue
        if sym in TRADE_ENTRIES:
            log.debug("[ADOP] %s ya trackeada — skip", sym)
            continue
        log.info("[ADOP] %s HUÉRFANA detectada: side=%s entry=%.4f ct=%.4f lev=%.1f liq=%.4f age=%.1fh",
            sym, sd, ep, ct, lv, li, age_h)
        try:
            if ct <= 0: continue
            if sd not in ('long','short'): continue
            if ep <= 0: continue
            if lv <= 0: lv = LEVERAGE
            if li <= 0: li = ep*(1-1/lv) if sd=='long' else ep*(1+1/lv)
            et = datetime.fromtimestamp(ts/1000) if ts else datetime.now()
            pr, lo = _fetch_plans_exchange(sym)
            slp = None; sl_pl = False
            if not lo:
                slp = _sl_desde_posicion(pos, sd, ep)
                if slp is not None: ls2=ct; sl_pl=True
                else:
                    log.warning("[ADOP] %s SIN SL plans ni SL en posición — cerrando por seguridad", sym)
                    closed = _cerrar_pos_real(sym, sd, ct)
                    if closed:
                        log.info("[ADOP] %s CERRADA OK (sin SL plans)", sym)
                        send_telegram(f"⚠️ *{sym} CERRADA* (huérfana sin SL)")
                    else:
                        log.error("[ADOP] %s FALLO CERRAR (sin SL plans) — POSICIÓN ABIERTA", sym)
                        send_telegram(f"❌ *{sym} NO CERRADA* (huérfana sin SL — intervenir manual)")
                    continue
            else: ls2 = sum(p['size'] for p in lo)
            if abs(ls2-ct) > ct*0.05:
                log.warning("[ADOP] %s desajuste SL qty: plans=%.4f pos=%.4f diff=%.1f%% — cerrando",
                    sym, ls2, ct, abs(ls2-ct)/max(ct,1)*100)
                closed = _cerrar_pos_real(sym, sd, ct)
                if closed:
                    log.info("[ADOP] %s CERRADA OK (desajuste SL qty)", sym)
                    send_telegram(f"⚠️ *{sym} CERRADA* (desajuste SL qty)")
                else:
                    log.error("[ADOP] %s FALLO CERRAR (desajuste SL qty) — POSICIÓN ABIERTA", sym)
                    send_telegram(f"❌ *{sym} NO CERRADA* (desajuste SL qty — intervenir manual)")
                continue
            if not pr:
                log.warning("[ADOP] %s SIN TP plans — cerrando por seguridad", sym)
                closed = _cerrar_pos_real(sym, sd, ct)
                if closed:
                    log.info("[ADOP] %s CERRADA OK (sin TP plans)", sym)
                    send_telegram(f"⚠️ *{sym} CERRADA* (huérfana sin TP)")
                else:
                    log.error("[ADOP] %s FALLO CERRAR (sin TP plans) — POSICIÓN ABIERTA", sym)
                    send_telegram(f"❌ *{sym} NO CERRADA* (huérfana sin TP — intervenir manual)")
                continue
            sl = slp if sl_pl else (min(lo,key=lambda p:p['triggerPrice'])['triggerPrice'] if sd=='long' else max(lo,key=lambda p:p['triggerPrice'])['triggerPrice'])
            np2 = len(pr)
            if np2==3: pl,oq = 0, ct
            elif np2==2: pl,oq = 1, ct/(1-TP1_CLOSE_PCT)
            elif np2==1: pl,oq = 2, ct/(1-TP1_CLOSE_PCT-TP2_CLOSE_PCT)
            else:
                log.warning("[ADOP] %s TP plans inesperados: %d — cerrando", sym, np2)
                closed = _cerrar_pos_real(sym, sd, ct)
                if closed:
                    log.info("[ADOP] %s CERRADA OK (TP plans inesperados)", sym)
                    send_telegram(f"⚠️ *{sym} CERRADA* (TP plans inesperados: {np2})")
                else:
                    log.error("[ADOP] %s FALLO CERRAR (TP plans inesperados) — POSICION ABIERTA", sym)
                    send_telegram(f"❌ *{sym} NO CERRADA* (TP plans inesperados — intervenir manual)")
                continue
            if abs(sum(p['size'] for p in pr)-ct) > ct*0.05:
                log.warning("[ADOP] %s desajuste TP qty: plans=%.4f pos=%.4f — cerrando",
                    sym, sum(p['size'] for p in pr), ct)
                closed = _cerrar_pos_real(sym, sd, ct)
                if closed:
                    log.info("[ADOP] %s CERRADA OK (desajuste TP qty)", sym)
                    send_telegram(f"⚠️ *{sym} CERRADA* (desajuste TP qty)")
                else:
                    log.error("[ADOP] %s FALLO CERRAR (desajuste TP qty) — POSICION ABIERTA", sym)
                    send_telegram(f"❌ *{sym} NO CERRADA* (desajuste TP qty — intervenir manual)")
                continue
            sn = 1 if sd=='long' else -1
            pr.sort(key=lambda p:p['triggerPrice'],reverse=(sd=='short'))
            if np2==3: t1p,t2p,t3p = (p['triggerPrice'] for p in pr)
            elif np2==2: t2p,t3p = pr[0]['triggerPrice'],pr[1]['triggerPrice']; t1p = ep*(1+sn*TP1_PNL_TARGET/lv)
            else: t3p=pr[0]['triggerPrice']; t2p=ep*(1+sn*TP2_PNL_TARGET/lv); t1p=ep*(1+sn*TP1_PNL_TARGET/lv)
            step = 0.01
            try: mi = exchange.market(sym); step = mi['limits']['amount']['min'] or mi['precision']['amount']
            except: pass
            av = _atr_est_15m(sym, ep)
            er = {'entry_time':et,'symbol':sym,'side':sd,'entry_price':ep,'sl_price':sl,'liq_price':li,
                'leverage':lv,'tp1_price':t1p,'tp2_price':t2p,'tp3_price':t3p,'quantity':round(oq,8),
                'original_qty':round(oq,8),'remaining_qty':round(ct,8),'step':step,'balance_before':0.0,
                'capital_futuros':0.0,'atr_val':av,'size_usdt':round(ct*ep/lv,2),'risk_pct':0.0,'score':0,'rr':0.0,'adoptada':True}
            TRADE_ENTRIES[sym]=er; PARTIAL_LEVEL[sym]=pl; _save_trade_entries(); _save_partial_level()
            log.info("[ADOP] %s adoptada OK | side=%s lvl=%d entry=%.4f sl=%.4f tp1=%.4f tp2=%.4f tp3=%.4f lev=%.1f age=%.1fh",
                sym, sd, pl, ep, sl, t1p, t2p, t3p, lv, age_h)
            ad+=1
        except Exception as e: log.error("[ADOP] Error %s: %s",sym,e)
    return ad

def restaurar_tp_exchange():
    if not exchange or PAPER_TRADE: return
    try:
        for pos in exchange.fetch_positions():
            sym = pos['symbol']
            if float(pos['contracts'])==0 or sym not in TRADE_ENTRIES: continue
            ed = TRADE_ENTRIES[sym]; sd = ed.get('side','long')
            ep=float(ed['entry_price']); step=ed.get('step',0)
            t1p=float(ed.get('tp1_price',0)); t2p=float(ed.get('tp2_price',0)); t3p=float(ed.get('tp3_price',0))
            oq=float(ed.get('original_qty',ed.get('quantity',0))); cq=float(pos['contracts'])
            if t1p==ep or t2p==ep or t3p==ep or step<=0: continue
            _cancel_tp_plans(sym); _cancel_sl_plans(sym)
            if cq >= oq * 0.85:
                t1q = ((oq*TP1_CLOSE_PCT)//step)*step
                if t1q >= step and t1q*t1p >= MIN_ORDER_USDT:
                    r_ok, r_err = _place_tp_plan(sym, t1p, min(t1q, math.floor(cq/step)*step), sd)
                    log.info("[RESTORE-TP1-%s] %s %s", 'EX' if r_ok else 'FAIL', sym, r_err if r_err else '')
            elif cq >= oq * 0.45:
                t2q = ((oq*TP2_CLOSE_PCT)//step)*step
                if t2q >= step and t2q*t2p >= MIN_ORDER_USDT:
                    r_ok, r_err = _place_tp_plan(sym, t2p, min(t2q, math.floor(cq/step)*step), sd)
                    log.info("[RESTORE-TP2-%s] %s %s", 'EX' if r_ok else 'FAIL', sym, r_err if r_err else '')
            csl = float(ed.get('sl_price',0))
            if csl > 0 and cq >= step: _place_sl_plan(sym,csl,cq,sd)
    except Exception as e: log.error("Error restaurar_tp: %s",e)

# ── 25. GESTION DE POSICIONES ──
def _full_cleanup(sym, cd=3600):
    TRADE_ENTRIES.pop(sym,None); HEDGE_ENTRIES.pop(sym,None); _save_trade_entries()
    SESSION_ACTIVE_SYMBOLS.discard(sym); COOLDOWNS[sym]=time.time()+cd
    PEAK_PRICES.pop(sym,None); ADVERSE_PRICES.pop(sym,None)
    for k in [k for k in ALERTS_HISTORY if sym in k]: ALERTS_HISTORY.pop(k,None)
    TRAIL_COUNTS.pop(sym,None); PARTIAL_LEVEL.pop(sym,None); _save_partial_level()
    _cancel_tp_plans(sym); _cancel_sl_plans(sym)


# ─ 25. GESTION DE POSICIONES (UNIFICADA DRY) ─
def _full_cleanup(sym, cd=3600):
    TRADE_ENTRIES.pop(sym,None); HEDGE_ENTRIES.pop(sym,None); _save_trade_entries()
    SESSION_ACTIVE_SYMBOLS.discard(sym); COOLDOWNS[sym]=time.time()+cd
    PEAK_PRICES.pop(sym,None); ADVERSE_PRICES.pop(sym,None)
    for k in [k for k in ALERTS_HISTORY if sym in k]: ALERTS_HISTORY.pop(k,None)
    TRAIL_COUNTS.pop(sym,None); PARTIAL_LEVEL.pop(sym,None); _save_partial_level()
    _cancel_tp_plans(sym); _cancel_sl_plans(sym)

def _tick_manage_posicion(sym, paper=False):
    """Tick unificado de gestion para una posicion. Retorna 'continue' si debe saltar, None si OK."""
    e = TRADE_ENTRIES[sym]; sd = e.get('side','long')
    ep = float(e['entry_price']); sl = float(e.get('sl_price',0))
    t1=float(e.get('tp1_price',0)); t2=float(e.get('tp2_price',0))
    t3=float(e.get('tp3_price',0)); li=float(e.get('liq_price',0))
    # --- fetch ticker ---
    try:
        if paper:
            mk = float(exchange.fetch_ticker(sym)['last'])
        else:
            _t = _safe_fetch(exchange.fetch_ticker, sym, label=f'ticker_{sym}')
            mk = float(_t['last']) if _t else None
            if mk is None:
                log.warning("[MGMT] %s Error fetch_ticker \u2014 skip ciclo", sym)
                return 'continue'
    except:
        log.warning("[MGMT] %s Error fetch_ticker \u2014 skip ciclo", sym)
        return 'continue'
    pp = (mk-ep)/ep if sd=='long' else (ep-mk)/ep
    rq = float(e.get('remaining_qty',e.get('quantity',0)))
    age_h = (datetime.now()-e.get('entry_time',datetime.now())).total_seconds()/3600
    # --- logging ---
    if paper:
        log.debug("[PAPER] %s %s | entry=%.4f mk=%.4f pp=%.2f%%", sym, sd.upper(), ep, mk, pp*100)
    else:
        log.info("[MGMT] %s %s | entry=%.4f mk=%.4f pp=%.2f%% sl=%.4f lvl=%d rem=%.4f age=%.1fh",
            sym, sd.upper(), ep, mk, pp*100, sl, PARTIAL_LEVEL.get(sym,0), rq, age_h)
    # --- D1 validation (compartido) ---
    if debe_validar_h4():
        try:
            oh = exchange.fetch_ohlcv(sym,'4h',30)
            if len(oh)>=10:
                df4 = pd.DataFrame(oh,columns=['ts','o','h','l','c','v'])
                if not validar_estructura_d1(df4,ep,sd):
                    rq2 = e.get('remaining_qty',e['quantity'])
                    pnl = (mk-ep)*rq2 if sd=='long' else (ep-mk)*rq2
                    if not paper:
                        log.warning("[MGMT] %s D1 INVALID — cerrando. pnl=%.4f", sym, pnl)
                        if not _cerrar_pos_real(sym,sd,rq2):
                            log.error("[MGMT] %s FALLO cerrar D1_INVALID — reintento próximo ciclo", sym)
                    guardar_trade_csv(e,mk,pnl,0,pnl,'D1_INVALID','d1_estructura')
                    _full_cleanup(sym,7200); return 'continue'
        except: pass
    # --- Hedge (compartido) ---
    if LOBO_HEDGE_ENABLED and sym not in HEDGE_ENTRIES:
        hp = evaluar_cobertura_v4(e, mk)
        if hp:
            if paper:
                HEDGE_ENTRIES[sym]=hp
            else:
                hn = float(hp.get('size_usdt',0))
                if hn < MIN_ORDER_USDT:
                    log.debug("[MGMT] %s hedge candidato pero margen %.2f < min %.2f", sym, hn, MIN_ORDER_USDT)
                else:
                    HEDGE_ENTRIES[sym]=hp
                    log.info("[MGMT] %s HEDGE ACTIVADO: side=%s lev=%sx tp=%.4f sl=%.4f margin=%.2f",
                        sym, hp['side'], hp['leverage'], hp['tp_price'], hp['sl_price'], hn)
                    try: exchange.set_leverage(int(hp['leverage']),sym)
                    except: pass
                    try:
                        try: hmi=exchange.market(sym); hs2=hmi['limits']['amount']['min'] or hmi['precision']['amount'] or 1
                        except: hs2=1
                        hq = math.ceil((hn/mk)/hs2)*hs2
                        exchange.create_order(sym,'market','buy' if hp['side']=='long' else 'sell',hq,
                            params={'marginCoin':'USDT','marginMode':'isolated','tradeSide':'open',
                                'presetStopSurplusPrice':str(exchange.price_to_precision(sym,hp['tp_price'])),
                                'presetStopLossPrice':str(exchange.price_to_precision(sym,hp['sl_price']))})
                    except Exception as ex: log.error("[MGMT] Error creando hedge: %s",ex)
    # --- Hedge tracking (compartido) ---
    he = HEDGE_ENTRIES.get(sym)
    if he:
        hs,ht,hs2 = he['side'],he['tp_price'],he['sl_price']
        if (hs=='short' and mk<=ht) or (hs=='long' and mk>=ht): HEDGE_ENTRIES.pop(sym,None)
        if (hs=='short' and mk>=hs2) or (hs=='long' and mk<=hs2): HEDGE_ENTRIES.pop(sym,None)
    # --- Exchange TP detection (real only) ---
    ls2=sd=='long'; ss=sd=='short'
    oq=float(e.get('original_qty',e.get('quantity',0))); rq=float(e.get('remaining_qty',e.get('quantity',0)))
    sp=float(e.get('step',0)); pl=PARTIAL_LEVEL.get(sym,0); lv=float(e.get('leverage',LEVERAGE))
    pd_pos = e.get('_exchange_pos')
    if not paper and pd_pos is not None:
        eq = float(pd_pos.get('contracts',rq))
        if eq < rq*0.95:
            t1p_val = float(e.get('tp1_price',0)); t2p_val = float(e.get('tp2_price',0))
            if pl==0 and eq <= oq*0.65 and t1p_val>0:
                tp1_hit = (sd=='long' and mk>=t1p_val) or (sd=='short' and mk<=t1p_val)
                if tp1_hit:
                    tp1pnl = (t1p_val-ep)*oq*TP1_CLOSE_PCT if sd=='long' else (ep-t1p_val)*oq*TP1_CLOSE_PCT
                    e['remaining_qty']=eq; rq=eq; PARTIAL_LEVEL[sym]=1
                    guardar_trade_csv(e,t1p_val,tp1pnl,0,tp1pnl,'TP1_EXCHANGE','tp1_exchange')
                    _save_trade_entries(); _save_partial_level()
            elif pl==1 and eq <= oq*0.40 and t2p_val>0:
                tp2_hit = (sd=='long' and mk>=t2p_val) or (sd=='short' and mk<=t2p_val)
                if tp2_hit:
                    tp2pnl = (t2p_val-ep)*oq*TP2_CLOSE_PCT if sd=='long' else (ep-t2p_val)*oq*TP2_CLOSE_PCT
                    e['remaining_qty']=eq; rq=eq; PARTIAL_LEVEL[sym]=2
                    _update_sl_to_be(sym,e,ep,reason='BE')
                    guardar_trade_csv(e,t2p_val,tp2pnl,0,tp2pnl,'TP2_EXCHANGE','tp2_exchange')
                    _save_trade_entries(); _save_partial_level()
                    pl = PARTIAL_LEVEL.get(sym,0); rq = float(e.get('remaining_qty',e.get('quantity',0)))
    # --- SL check (compartido) ---
    if (ls2 and mk<=sl) or (ss and mk>=sl):
        log.info("[MGMT] %s SL HIT! mk=%.4f sl=%.4f", sym, mk, sl)
        if rq>0:
            pnl = _calc_pnl_parcial(sd,ep,rq,mk)
            if not paper:
                if not _cerrar_pos_real(sym,sd,rq):
                    log.error("[MGMT] %s FALLO cerrar SL — reintento en próximo ciclo", sym)
            guardar_trade_csv(e,mk,pnl,0,pnl,'SL','sl')
        if not paper: _cancel_tp_plans(sym); _cancel_sl_plans(sym)
        _full_cleanup(sym); return 'continue'
    # --- TP3 check (compartido) ---
    if (ls2 and mk>=t3) or (ss and mk<=t3):
        log.info("[MGMT] %s TP3 HIT! mk=%.4f t3=%.4f", sym, mk, t3)
        if rq>0:
            pnl = _calc_pnl_parcial(sd,ep,rq,t3)
            if not paper:
                if not _cerrar_pos_real(sym,sd,rq):
                    log.error("[MGMT] %s FALLO cerrar TP3 — reintento en próximo ciclo", sym)
            guardar_trade_csv(e,t3,pnl,0,pnl,'TP3','tp3')
        if not paper: _cancel_tp_plans(sym); _cancel_sl_plans(sym)
        _full_cleanup(sym); return 'continue'
    # --- TP1 partial (compartido) ---
    if pl==0 and sp>0 and rq>=sp and t1!=ep:
        if (ls2 and mk>=t1) or (ss and mk<=t1):
            tq = ((oq*TP1_CLOSE_PCT)//sp)*sp; tq = min(tq,rq-sp)
            if tq>=sp:
                pnl = _calc_pnl_parcial(sd,ep,tq,t1)
                if paper or _cerrar_pos_real(sym,sd,tq):
                    log.info("[MGMT] %s TP1 PARTIAL: qty=%.4f pnl=%.4f", sym, tq, pnl)
                    e['remaining_qty']=rq-tq; PARTIAL_LEVEL[sym]=1
                    guardar_trade_csv(e,t1,pnl,0,pnl,'TP1_PARTIAL','tp1')
                    _save_trade_entries(); _save_partial_level()
    # --- TP2 partial + BE (compartido) ---
    elif pl==1 and sp>0 and rq>=sp and t2!=ep:
        if (ls2 and mk>=t2) or (ss and mk<=t2):
            ra = oq-((oq*TP1_CLOSE_PCT)//sp)*sp
            tq = ((ra*TP2_CLOSE_PCT/(1-TP1_CLOSE_PCT))//sp)*sp; tq = min(tq,rq-sp)
            if tq>=sp:
                pnl = _calc_pnl_parcial(sd,ep,tq,t2)
                if paper or _cerrar_pos_real(sym,sd,tq):
                    log.info("[MGMT] %s TP2 PARTIAL: qty=%.4f pnl=%.4f -> BE", sym, tq, pnl)
                    e['remaining_qty']=rq-tq; PARTIAL_LEVEL[sym]=2
                    _update_sl_to_be(sym,e,ep,reason='BE')
                    guardar_trade_csv(e,t2,pnl,0,pnl,'TP2_PARTIAL','tp2')
                    _save_trade_entries(); _save_partial_level()
    # --- Timeout (compartido) ---
    et = e.get('entry_time')
    if isinstance(et,datetime) and pp<0:
        if (datetime.now()-et).total_seconds()/3600 >= LOBO_TIMEOUT_HORAS:
            rq = float(e.get('remaining_qty',e.get('quantity',0)))
            log.warning("[MGMT] %s TIMEOUT (%.1fh) — cerrando. pp=%.2f%%", sym, age_h, pp*100)
            if rq>0:
                pnl = _calc_pnl_parcial(sd,ep,rq,mk)
                if not paper:
                    if not _cerrar_pos_real(sym,sd,rq):
                        log.error("[MGMT] %s FALLO cerrar TIMEOUT — reintento en próximo ciclo", sym)
                guardar_trade_csv(e,mk,pnl,0,pnl,'Timeout','timeout')
            _full_cleanup(sym); return 'continue'
    # --- Peak/Adverse tracking (compartido) ---
    if sym not in PEAK_PRICES: PEAK_PRICES[sym]=mk
    else: PEAK_PRICES[sym] = max(PEAK_PRICES[sym],mk) if sd=='long' else min(PEAK_PRICES[sym],mk)
    if sym not in ADVERSE_PRICES: ADVERSE_PRICES[sym]=mk
    else: ADVERSE_PRICES[sym] = min(ADVERSE_PRICES[sym],mk) if sd=='long' else max(ADVERSE_PRICES[sym],mk)
    # --- Trailing (compartido) ---
    if PARTIAL_LEVEL.get(sym,0)>=2 and pp>0:
        dist = LOBO_TRAIL_ATR_MULT*e.get('atr_val',0)*1.5
        if dist>0:
            ns = (PEAK_PRICES[sym]-dist) if sd=='long' else (PEAK_PRICES[sym]+dist)
            us = e.get('sl_price',0 if sd=='long' else 999999)
            mej = (ns-us) if sd=='long' else (us-ns)
            if mej > (ep*0.002):
                if _update_sl_to_be(sym,e,ns,reason='TRAIL'):
                    TRAIL_COUNTS[sym]=TRAIL_COUNTS.get(sym,0)+1
                    log.info("[MGMT] %s TRAIL #%d: sl \u2192 %.4f (peak=%.4f dist=%.4f)",
                        sym, TRAIL_COUNTS[sym], ns, PEAK_PRICES.get(sym,mk), dist)
    return None

def manage_escudo_pro_v3(bt=0.0):
    """Punto de entrada unificado. Paper y real usan el mismo tick."""
    if not TRADE_ENTRIES: return
    pos_by_sym = {}; po = False
    if not PAPER_TRADE:
        try:
            ap = exchange.fetch_positions(); po = True
            for p in ap:
                if float(p.get('contracts',0))>0: pos_by_sym[p['symbol']]=p
        except Exception as e:
            log.warning("[MGMT] Error fetch_positions: %s", e)
        log.info("[MGMT] Ciclo gestion: %d posiciones trackeadas, %d en exchange", len(TRADE_ENTRIES), len(pos_by_sym))
    for sym in list(TRADE_ENTRIES.keys()):
        try:
            e = TRADE_ENTRIES[sym]
            if not PAPER_TRADE:
                pd_pos = pos_by_sym.get(sym)
                rq = float(e.get('remaining_qty',e.get('quantity',0)))
                ep = float(e['entry_price']); sd = e.get('side','long')
                # --- Exchange close detection (real only) ---
                if po and pd_pos is None:
                    try:
                        _t = _safe_fetch(exchange.fetch_ticker, sym, label=f'ticker_{sym}')
                        mk = float(_t['last']) if _t else 0
                    except: mk = ep
                    log.warning("[MGMT] %s CERRADA EN EXCHANGE (no encontrada) \u2014 limpiando", sym)
                    if rq>0:
                        pnl = (mk-ep)*rq if sd=='long' else (ep-mk)*rq
                        guardar_trade_csv(e,mk,pnl,0,pnl,'EXCHANGE_CLOSE','exchange')
                    _full_cleanup(sym); continue
                if pd_pos is None and rq>0:
                    log.warning("[MGMT] %s posicion fantasma (rq>0 pero sin pos en exchange) \u2014 limpiando", sym)
                    try:
                        _t = _safe_fetch(exchange.fetch_ticker, sym, label=f'ticker_{sym}')
                        mk = float(_t['last']) if _t else ep
                    except: mk = ep
                    pnl = (mk-ep)*rq if sd=='long' else (ep-mk)*rq
                    guardar_trade_csv(e,mk,pnl,0,pnl,'EXCHANGE_CLOSE','exchange')
                    _full_cleanup(sym); continue
                # Injectar pos_data para deteccion exchange TP
                if pd_pos is not None:
                    e['_exchange_pos'] = pd_pos
                ret = _tick_manage_posicion(sym, paper=False)
                e.pop('_exchange_pos', None)  # cleanup temp key
            else:
                ret = _tick_manage_posicion(sym, paper=True)
            if ret == 'continue': continue
        except Exception as ex:
            log.error("[%s] Error %s: %s", 'REAL' if not PAPER_TRADE else 'PAPER', sym, ex)


# ── 26. SHUTDOWN GRACEFUL ──
def _graceful_shutdown():
    log.info("="*40); log.info("SHUTDOWN GRACEFUL INICIADO"); log.info("="*40)
    try: _save_trade_entries(); _save_partial_level()
    except: pass
    _close_async_exchange()
    n = len(TRADE_ENTRIES)
    if n > 0:
        log.warning("Posiciones abiertas al cerrar: %d",n)
        for s2,e in TRADE_ENTRIES.items():
            log.warning("  %s %s entry=%.4f sl=%.4f rem=%.4f",s2,e.get('side','?'),e.get('entry_price',0),e.get('sl_price',0),e.get('remaining_qty',0))
    try: send_telegram(f"🔴 *BOT APAGADO*\nPosiciones: {n}\nRazón: SIGTERM")
    except: pass
    for h in logging.root.handlers:
        try: h.flush()
        except: pass
    log.info("SHUTDOWN GRACEFUL COMPLETO")

# ── 27. FLASK HEALTHCHECK ──
app: Optional[object] = None
_BOT_START_TIME = time.time()

def _create_flask_app():
    fa = Flask("lobobot_v3")
    @fa.route("/health")
    def health():
        from flask import jsonify
        return jsonify({"status":"ok","uptime":time.time()-_BOT_START_TIME}), 200
    @fa.route("/status")
    def status():
        from flask import jsonify
        return jsonify({"positions":len(TRADE_ENTRIES),"daily_stats":DAILY_STATS,
            "active_symbols":list(SESSION_ACTIVE_SYMBOLS),"paper_mode":PAPER_TRADE}), 200
    return fa

def _start_healthcheck_server():
    global app
    if not _FLASK_AVAILABLE: log.warning("Flask no disponible — healthcheck deshabilitado"); return
    port = int(os.environ.get("PORT", 10000))
    app = _create_flask_app()
    threading.Thread(target=lambda: app.run(host="0.0.0.0",port=port,use_reloader=False,debug=False), daemon=True).start()
    log.info("Healthcheck en puerto %d",port)

# ── 28. BUCLE PRINCIPAL ──
def main():
    log.info("="*60); log.info("LOBOBOT v4 Refactorizado — iniciando"); log.info("="*60)
    def _h(s,f):
        log.warning("Senal %d — shutdown",s); _shutdown_event.set()
    try:
        signal.signal(signal.SIGTERM,_h); signal.signal(signal.SIGINT,_h)
    except ValueError:
        log.info("Signal handlers no disponibles (background thread)")
    atexit.register(_graceful_shutdown)
    if exchange is None:
        if not init_exchange(): log.critical("No se pudo inicializar exchange"); return
    _load_trade_entries(); _load_partial_level()
    try:
        na = adoptar_posiciones_exchange()
        if na > 0: log.info("Posiciones adoptadas: %d",na)
    except Exception as e: log.error("Error adoptar: %s",e)
    restaurar_tp_exchange()
    if TRADE_ENTRIES:
        log.info("=== ESTADO POST-ARRANQUE: %d posiciones ===", len(TRADE_ENTRIES))
        for _sym, _e in TRADE_ENTRIES.items():
            _age = (datetime.now()-_e.get('entry_time',datetime.now())).total_seconds()/3600
            log.info("  %s %s entry=%.4f sl=%.4f tp1=%.4f tp2=%.4f tp3=%.4f lvl=%d rem=%.4f age=%.1fh score=%d",
                _sym, _e.get('side','?'), _e.get('entry_price',0), _e.get('sl_price',0),
                _e.get('tp1_price',0), _e.get('tp2_price',0), _e.get('tp3_price',0),
                PARTIAL_LEVEL.get(_sym,0), _e.get('remaining_qty',0), _age, _e.get('score',0))
    lrd = datetime.now().day-1
    _prev_balance = 0.0
    while not _shutdown_event.is_set():
        try:
            now = datetime.now()
            if now.hour==0 and now.day!=lrd:
                ts = (now-timedelta(days=1)).strftime('%Y-%m-%d')
                tt=[]
                try:
                    with open(TRADES_CSV_PATH,'r',encoding='utf-8') as f:
                        for r in csv.DictReader(f):
                            if r['entry_time'].startswith(ts): tt.append(r)
                except: pass
                cl = [r for r in tt if r['status'] in ('TP3','SL','LIQ','Timeout','D1_INVALID','EXCHANGE_CLOSE')]
                tc2=len(cl); tp2=[r for r in cl if r['status']=='TP3']
                pt=sum(float(r['net_pnl']) for r in cl); wr=len(tp2)/max(tc2,1)*100
                send_telegram(f"*REPORTE DIARIO* ({now.strftime('%d/%m')})\nOps:{tc2} TP:{len(tp2)} WR:{wr:.0f}% PnL:{pt:+.2f}")
                lrd=now.day
            try:
                bt = _safe_fetch_balance()
                if bt is None:
                    if PAPER_TRADE: bt=10000.0
                    else: log.error("Error balance (backoff agotado)"); bt=0.0
            except:
                if PAPER_TRADE: bt=10000.0
                else: log.error("Error balance"); bt=0.0
            cf = capital_disponible_futuros(bt)
            bal_delta = bt - _prev_balance if _prev_balance > 0 else 0
            _prev_balance = bt
            log.info("Balance=%.2f Futuros(80%%)=%.2f Δ=%.4f",bt,cf,bal_delta)
            _schedule_bg_dominance_refresh()
            manage_escudo_pro_v3(bt)
            global KILL_UNTIL, CONSECUTIVE_LOSSES, KILL_STREAK_AT_TRIGGER, _LAST_SCAN_TIME
            if time.time() < KILL_UNTIL:
                log.warning("KILL-SWITCH: %.1fh restantes", (KILL_UNTIL-time.time())/3600)
                _shutdown_event.wait(timeout=60); continue
            if CONSECUTIVE_LOSSES >= LOBO_KILL_MAX_CONSEC_LOSSES:
                KILL_STREAK_AT_TRIGGER=CONSECUTIVE_LOSSES
                KILL_UNTIL=time.time()+LOBO_KILL_COOLDOWN_H*3600
                log.warning("KILL-SWITCH ARMADO: %d perdidas",KILL_STREAK_AT_TRIGGER)
                send_telegram(f"🛑 KILL-SWITCH\n{KILL_STREAK_AT_TRIGGER} pérdidas\nPausa {LOBO_KILL_COOLDOWN_H:.0f}h")
                CONSECUTIVE_LOSSES=0
                _shutdown_event.wait(timeout=60); continue
            h = now.hour
            enh = True
            if LOBO_TRADING_HOURS_ENABLED:
                enh = (LOBO_TRADE_START_HOUR <= h < LOBO_TRADE_END_HOUR) if LOBO_TRADE_START_HOUR<=LOBO_TRADE_END_HOUR else (h>=LOBO_TRADE_START_HOUR or h<LOBO_TRADE_END_HOUR)
            if not enh:
                _shutdown_event.wait(timeout=300); continue
            try:
                pos = _safe_fetch_positions()
                bs = {p['symbol'] for p in pos if float(p.get('contracts',0))>0}
            except: pos=[]; bs=set()
            if PAPER_TRADE: bs.update(TRADE_ENTRIES.keys())
            mr = calcular_margen_real_disponible(bt,positions_list=pos)
            log.info("Ciclo [%s] Fut=%.2f MR=%.2f Ocup=%d",now.strftime('%H:%M'),cf,mr,len(bs))
            if len(bs) >= LOBO_MAX_POSITIONS:
                log.info("[SCAN] SKIP: máx posiciones alcanzado (%d/%d)", len(bs), LOBO_MAX_POSITIONS)
                _shutdown_event.wait(timeout=60); continue
            scan_elapsed = time.time() - _LAST_SCAN_TIME
            if scan_elapsed < 840:
                log.debug("[SCAN] SKIP: intervalo %.0fs < 840s (restan %.0fs)", scan_elapsed, 840-scan_elapsed)
                _shutdown_event.wait(timeout=60); continue
            _LAST_SCAN_TIME = time.time()
            try:
                tk = _safe_fetch(exchange.fetch_tickers, label='fetch_tickers')
                if tk is None:
                    log.error("fetch_tickers None tras reintentos")
                    _shutdown_event.wait(timeout=60); continue
                ts2 = [p[0] for p in sorted([(s2,float(t.get('quoteVolume',0))) for s2,t in tk.items() if s2.endswith('/USDT:USDT')],key=lambda x:x[1],reverse=True)[:TOP_N]]
                if LOBO_WHITELIST: ts2=[s2 for s2 in ts2 if s2.split('/')[0] in LOBO_WHITELIST]
                bk = len(ts2); ts2=[s2 for s2 in ts2 if s2.split('/')[0] not in LOBO_BLACKLIST]
                if bk!=len(ts2): log.info("Blacklist: %d removidos",bk-len(ts2))
            except Exception as e: log.error("Error tickers: %s",e); _shutdown_event.wait(timeout=60); continue
            log.info("OHLCV para %d simbolos...",len(ts2))
            try: od = fetch_all_ohlcv(ts2)
            except Exception as e: log.error("Error OHLCV: %s",e); _shutdown_event.wait(timeout=60); continue
            va = check_btcd_elliott_ventana_altcoins()
            _rej = {'no_data':0,'no_signal':0,'tp_guard':0,'entered':0}
            for sym in ts2:
                if sym in bs or len(bs)>=LOBO_MAX_POSITIONS: continue
                if sym in COOLDOWNS:
                    if time.time()<COOLDOWNS[sym]: continue
                    else: del COOLDOWNS[sym]
                try:
                    o15,o4h,o5m,o1d = od.get(sym,(None,None,None,None))
                    if not o15 or not o4h: _rej['no_data']+=1; continue
                    if len(o15)<50 or len(o4h)<10: _rej['no_data']+=1; continue
                    df15 = pd.DataFrame(o15[:-1],columns=['timestamp','open','high','low','close','volume'])
                    df4h = pd.DataFrame(o4h[:-1],columns=['timestamp','open','high','low','close','volume'])
                    df5m = pd.DataFrame(o5m[:-1],columns=['timestamp','open','high','low','close','volume']) if o5m and len(o5m)>1 else None
                    df1d = pd.DataFrame(o1d[:-1],columns=['timestamp','open','high','low','close','volume']) if o1d and len(o1d)>1 else None
                    if not es_nueva_vela_principal(df15,sym): continue
                    pa = float(df15['close'].iloc[-1]); av = float(_atr(df15,LOBO_ATR_PERIOD).iloc[-1])
                    if av==0 or pd.isna(av):
                        log.debug("[SCAN] %s ATR=0/NaN — skip", sym)
                        continue
                    sl = evaluar_senal_bitlobo_v4(sym,df15,df4h,pa,av,bt,es_long=True,dfm=df5m,va=va,mrd=mr,dfd1=df1d)
                    log_score_report(sym, True, sl)
                    sws = detectar_sweep(df15)
                    hs = any(s2['tipo']=='sweep_alcista_short' for s2 in sws)
                    fvs = detectar_fvg(df15)
                    hb = any(f['tipo']=='bajista' for f in fvs)
                    rvs = _rsi(df15['close'],LOBO_RSI_PERIOD)
                    try: rv = float(rvs.iloc[-1])
                    except: rv = 50.0
                    hsc = not pd.isna(rv) and rv > LOBO_RSI_OVERBOUGHT
                    cs = hs or hsc
                    ss = evaluar_senal_bitlobo_v4(sym,df15,df4h,pa,av,bt,es_long=False,dfm=df5m,va=va,mrd=mr,dfd1=df1d) if cs else None
                    if ss: log_score_report(sym, False, ss)
                    # Seleccionar mejor señal: priorizar la no rechazada
                    if sl and not sl.get('_rejected', True):
                        sn = sl
                    elif ss and not ss.get('_rejected', True):
                        sn = ss
                    else:
                        _rej['no_signal']+=1
                        log.debug("[SCAN] %s sin señal (long=%s short=%s cs=%s hs=%s rsi=%.1f)",
                            sym, not sl.get('_rejected',True) if sl else False,
                            not ss.get('_rejected',True) if ss else False, cs, hs, rv)
                        continue
                    es_long=sn['es_long']; snn='LARGO' if es_long else 'CORTO'
                    slp=sn['sl_price']; t1p=sn['tp1_price']; t2p=sn['tp2_price']; t3p=sn['tp3_price']
                    alv=sn.get('leverage_calculado',LEVERAGE); lvp=sn.get('liq_price',0)
                    rr=sn['rr']; sc=sn['score']; ms2=sn['max_score']
                    rq=sn['qty']; mk2=exchange.market(sym)
                    stp = mk2['limits']['amount']['min'] or 10**(-(mk2['precision']['amount'] or 6))
                    if not stp or stp <= 0: stp = 10**(-(mk2['precision']['amount'] or 6))
                    stp = max(stp, 1e-12)
                    mq=math.ceil(MIN_ORDER_USDT/pa/stp)*stp
                    if rq<mq:
                        ra2=(mq*pa*abs(pa-slp)/pa)/max(mr,0.01)*100
                        if ra2>10: continue
                        rq=mq
                    qty=math.ceil(rq/stp)*stp; am=(qty*pa)/alv
                    mmr=mr*0.90
                    if mmr < MIN_ORDER_USDT/alv: continue
                    if am>mmr: qty=math.floor((mmr*alv/pa)/stp)*stp; am=(qty*pa)/alv
                    if qty<mq or qty<=0: continue
                    if qty*pa < MIN_ORDER_USDT:
                        log.warning("Notional bajo %s: %.4f < %.2f — skip",sym,qty*pa,MIN_ORDER_USDT); continue
                    # ── Guard pre-entry: verificar que TP1 y TP2 al menos uno sea válido ──
                    _t1q = ((qty*TP1_CLOSE_PCT)//stp)*stp
                    if _t1q < stp: _t1q = stp
                    _t2q = ((qty-_t1q)*TP2_CLOSE_PCT/(1-TP1_CLOSE_PCT)//stp)*stp
                    if _t2q < 0: _t2q = 0.0
                    if _t2q > 0 and _t2q * t2p < MIN_ORDER_USDT: _t2q = 0.0
                    _tp1_n = _t1q * t1p; _tp2_n = _t2q * t2p
                    if _tp1_n < MIN_ORDER_USDT and _tp2_n < MIN_ORDER_USDT:
                        _rej['tp_guard']+=1
                        log.warning("[TP-GUARD] %s TP1=%.2f TP2=%.2f ambas < $%.2f — skip",
                            sym, _tp1_n, _tp2_n, MIN_ORDER_USDT); continue
                    log.info("%s %s | Entry=%.4f SL=%.4f Liq=%.4f Lev=%.0f TPs=%.4f/%.4f/%.4f RR=%.2f S=%d/%d",
                        sym,snn,pa,slp,lvp,alv,t1p,t2p,t3p,rr,sc,ms2)
                    er = {'entry_time':datetime.now(),'symbol':sym,'side':'long' if es_long else 'short',
                        'entry_price':pa,'sl_price':slp,'liq_price':lvp,'leverage':alv,'tp1_price':t1p,
                        'tp2_price':t2p,'tp3_price':t3p,'quantity':qty,'original_qty':qty,'remaining_qty':qty,
                        'step':stp,'balance_before':bt,'capital_futuros':cf,'atr_val':sn.get('atr_val',0),
                        'size_usdt':round(am,2),'risk_pct':round(am/max(mr,0.01)*100,2),'score':sc,'rr':rr}
                    if PAPER_TRADE:
                        log.info("[PAPER] %s %s qty=%.6f",snn,sym,qty)
                        TRADE_ENTRIES[sym]=er; PARTIAL_LEVEL[sym]=0
                        _save_trade_entries(); _save_partial_level()
                        bs.add(sym); COOLDOWNS[sym]=time.time()+14400
                        _rej['entered']+=1
                        continue
                    try: exchange.set_leverage(int(alv),sym)
                    except: pass
                    try:
                        exchange.create_order(sym,'market','buy' if es_long else 'sell',qty,
                            params={'marginCoin':'USDT','marginMode':'isolated','tradeSide':'open',
                                'presetStopSurplusPrice':str(exchange.price_to_precision(sym,t3p))})
                    except Exception as e:
                        log.error("Error orden %s: %s",sym,e); COOLDOWNS[sym]=time.time()+14400; continue
                    tsd = 'long' if es_long else 'short'
                    t1q = ((qty*TP1_CLOSE_PCT)//stp)*stp
                    if t1q < stp: t1q = stp
                    t2q = ((qty-t1q)*TP2_CLOSE_PCT/(1-TP1_CLOSE_PCT)//stp)*stp
                    if t2q < 0: t2q = 0.0
                    if t2q > 0 and t2q * t2p < MIN_ORDER_USDT: t2q = 0.0
                    t3q = max(qty - t1q - t2q, 0.0)
                    log.info("[TP-CALC] %s qty=%.6f step=%.8f | TP1=%.6f (%.0f%%) TP2=%.6f (%.0f%%) TP3=%.6f (%.0f%%)",
                        sym,qty,stp,t1q,t1q/qty*100,t2q,t2q/qty*100,t3q,t3q/qty*100 if qty>0 else 0)
                    time.sleep(3)
                    # TP1
                    if t1q >= stp and t1q * t1p >= MIN_ORDER_USDT:
                        tp1_ok, tp1_err = _place_tp_plan(sym, t1p, t1q, tsd)
                        log.info("[TP1-%s] %s qty=%.6f price=%.6f notional=%.2f %s",
                            'EX' if tp1_ok else 'FAIL', sym, t1q, t1p, t1q*t1p,
                            '' if tp1_ok else f'ERR={tp1_err}')
                    else:
                        tp1_ok = False
                        tp1_err = f'notional={t1q*t1p:.2f}<min={MIN_ORDER_USDT}'
                        log.warning("[TP1-SKIP] %s qty=%.6f price=%.6f notional=%.2f < min=%.2f",
                            sym, t1q, t1p, t1q*t1p, MIN_ORDER_USDT)
                    # TP2
                    if t2q >= stp and t2q * t2p >= MIN_ORDER_USDT:
                        tp2_ok, tp2_err = _place_tp_plan(sym, t2p, t2q, tsd)
                        log.info("[TP2-%s] %s qty=%.6f price=%.6f notional=%.2f %s",
                            'EX' if tp2_ok else 'FAIL', sym, t2q, t2p, t2q*t2p,
                            '' if tp2_ok else f'ERR={tp2_err}')
                    else:
                        tp2_ok = False
                        tp2_err = f'notional={t2q*t2p:.2f}<min={MIN_ORDER_USDT}'
                        log.warning("[TP2-SKIP] %s qty=%.6f price=%.6f notional=%.2f < min=%.2f",
                            sym, t2q, t2p, t2q*t2p, MIN_ORDER_USDT)
                    # TP3 via presetStopSurplusPrice en orden de entrada
                    log.info("[TP3-ENTRY] %s qty_rest=%.6f price=%.6f via=presetStopSurplusPrice",
                        sym, t3q, t3p)
                    rq2 = None
                    for _fatt in range(3):
                        try:
                            for pc in exchange.fetch_positions([sym]):
                                if float(pc.get('contracts',0))>0: rq2=float(pc['contracts']); break
                            break
                        except Exception as fe:
                            log.warning("fetch_positions retry %d/%d %s: %s",_fatt+1,3,sym,fe)
                            time.sleep(2**(_fatt+1))
                    if rq2 is None or rq2 <= 0:
                        log.error("No se pudo leer posición real %s — abortando",sym)
                        try:
                            if not _cerrar_pos_real(sym,tsd,qty):
                                send_telegram(f"❌ {sym} ABORTADA + NO CERRADA — intervenir manual")
                        except: pass
                        _full_cleanup(sym); send_telegram(f"❌ {sym} ABORTADA — fetch_positions fallo"); continue
                    sl_ok = _place_sl_plan(sym,slp,rq2,tsd)
                    if not sl_ok:
                        if not _cerrar_pos_real(sym,tsd,rq2):
                            send_telegram(f"❌ {sym} ABORTADA + NO CERRADA — intervenir manual")
                        _full_cleanup(sym)
                        send_telegram(f"❌ {sym} ABORTADA — SL fallo"); continue
                    PARTIAL_LEVEL[sym]=0; TRADE_ENTRIES[sym]=er
                    _save_trade_entries(); _save_partial_level()
                    bs.add(sym); COOLDOWNS[sym]=time.time()+14400
                    _rej['entered']+=1
                    sl_lbl = '[EX]' if sl_ok else '[FAIL]'
                    tp1_lbl = '[EX]' if tp1_ok else '[LO]'
                    tp2_lbl = '[EX]' if tp2_ok else '[LO]'
                    log.info("[ENTRY-OK] %s %s | SL=%s TP1=%s TP2=%s TP3=[EX] | qty=%.6f Entry=%.4f",
                        sym, snn, sl_lbl, tp1_lbl, tp2_lbl, qty, pa)
                    send_telegram(f"*{sym} {snn}*\nEntry: `{exchange.price_to_precision(sym,pa)}`\n"
                        f"Lev:{alv:.0f}x Liq:`{exchange.price_to_precision(sym,lvp)}`\n"
                        f"SL:`{exchange.price_to_precision(sym,slp)}` {sl_lbl}\n"
                        f"TP1(40%):`{exchange.price_to_precision(sym,t1p)}` [{'EX' if tp1_ok else 'LO'}]\n"
                        f"TP2(30%):`{exchange.price_to_precision(sym,t2p)}` [{'EX' if tp2_ok else 'LO'}]\n"
                        f"TP3(30%):`{exchange.price_to_precision(sym,t3p)}` [EX]\n"
                        f"RR:{rr:.2f} Score:{sc}/{ms2}")
                except Exception as e: log.debug("Error %s: %s",sym,e); continue
            scan_dur = time.time() - _LAST_SCAN_TIME
            log.info("Scan completado en %.0fs: %d símbolos | sin_data=%d sin_señal=%d tp_guard=%d entradas=%d",
                scan_dur, len(ts2), _rej['no_data'], _rej['no_signal'], _rej['tp_guard'], _rej['entered'])
            _shutdown_event.wait(timeout=60)
        except Exception as e: log.error("Error ciclo: %s",e,exc_info=True); _shutdown_event.wait(timeout=60)
    _graceful_shutdown()

if __name__ == "__main__":
    log.info("LOBOBOT v4 standalone...")
    _start_healthcheck_server()
    if exchange is None: init_exchange()
    main()
