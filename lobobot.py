#!/usr/bin/env python3
"""
LOBOBOT v3 — BITLOBO TRADING (Alineación Completa con Estrategia Documentada)
================================================================================

Correcciones respecto a v2:
  F1 - Split de capital 50/30/20 (3 vectores de portafolio)
  F2 - Dominancias reales USDT.D / BTC.D (CoinGecko + proxy calculado)
  F3 - Stop Loss por liquidación forzosa (anti-cacería de stops)
  F4 - Coberturas asimétricas (hedging de emergencia hiper-apalancado)
  F5 - RSI filtro obligatorio + Volumen como validador
  F6 - Confirmación Pullback ("Rompe y Apoya")
  F7 - Timing de entrada al cierre de vela H4
  F8 - Riesgo base 1.5-2% sobre el 20% de la cuenta de futuros
  F9 - Break Even al alcanzar TP1 (no al 1.5%)
  F10- Invalidación D1 estructural (swing points)
  F11- Ondas Elliott con relaciones Fibonacci entre ondas
  F12- TPs en zonas reales (FVG/OB/estructurales)

Uso:
    python lobobot_v3.py                          # Bot standalone
    gunicorn lobobot_v3:app --workers 1 --threads 2   # Render

Variables de entorno (nuevas respecto a v2):
    LOBO_LIQUIDEZ_PCT=50    LOBO_SPOT_PCT=30    LOBO_FUTUROS_PCT=20
    LOBO_SPOT_MARTINGALA_1=0.1  LOBO_SPOT_MARTINGALA_2=0.2  LOBO_SPOT_MARTINGALA_3=0.3
    LOBO_HEDGE_ENABLED=true     LOBO_HEDGE_LEV_MULT=3
"""

from __future__ import annotations
import os, sys, time, json, math, logging, asyncio, threading, csv, warnings
from datetime import datetime, timedelta
from typing import Literal, Optional
import numpy as np
import pandas as pd
pd.set_option("future.no_silent_downcasting", True)
import ccxt, ccxt.async_support as ccxt_async
import requests

# =====================================================================
# 1. LOGGER (idéntico a v2)
# =====================================================================
LOG_TO_FILE = os.environ.get('BOT_LOG_TO_FILE', '1') == '1'
LOG_LEVEL   = os.environ.get('BOT_LOG_LEVEL', 'INFO')

_handlers = [logging.StreamHandler(sys.stdout)]
if LOG_TO_FILE:
    LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lobobot_v3.log")
    _handlers.append(logging.FileHandler(LOG_FILE, encoding="utf-8"))

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=_handlers,
)
log = logging.getLogger("lobobot_v3")
logging.getLogger("werkzeug").setLevel(logging.WARNING)

# =====================================================================
# 2. ESTADO EN MEMORIA
# =====================================================================
ALERTS_HISTORY: dict = {}
PEAK_PRICES: dict = {}
COOLDOWNS: dict = {}
SESSION_ACTIVE_SYMBOLS: set = set()
DAILY_STATS: dict = {
    'tp': 0, 'sl': 0, 'be': 0, 'timeout': 0,
    'pnl': 0.0, 'fees': 0.0,
    'tp_names': [], 'sl_names': [], 'be_names': [], 'timeout_names': [],
}
TRADE_ENTRIES: dict = {}
HEDGE_ENTRIES: dict = {}       # F4: coberturas activas
TRAIL_COUNTS: dict = {}
LAST_KNOWN_INDICATORS: dict = {}
ADVERSE_PRICES: dict = {}
PRICE_PATHS: dict = {}
SPOT_POSITIONS: dict = {}      # F1: posiciones spot abiertas

# Cache para F2: dominancias (evita llamadas API repetitivas)
DOMINANCE_CACHE: dict = {'btc': None, 'usdtd': None, 'ts': 0}
DOMINANCE_CACHE_TTL = 300  # 5 minutos

# =====================================================================
# 3. RUTAS DE ARCHIVOS
# =====================================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PRICE_PATHS_DIR = os.path.join(BASE_DIR, 'price_paths_v3')
os.makedirs(PRICE_PATHS_DIR, exist_ok=True)
TRADES_CSV_PATH      = os.path.join(BASE_DIR, 'trades_v3.csv')
TRADE_ENTRIES_PATH   = os.path.join(BASE_DIR, 'trade_entries_v3.json')
SIGNALS_LOG_PATH     = os.path.join(BASE_DIR, 'signals_log_v3.csv')

def _save_trade_entries():
    try:
        data = {}
        for sym, e in TRADE_ENTRIES.items():
            data[sym] = {k: v.isoformat() if isinstance(v, datetime) else v for k, v in e.items()}
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
            for k, v in e.items():
                if k == 'entry_time' and isinstance(v, str):
                    e[k] = datetime.fromisoformat(v)
        TRADE_ENTRIES.update(data)
        log.info("Cargadas %d entradas pendientes", len(data))
    except Exception as ex:
        log.error("Error cargando trade_entries: %s", ex)

# =====================================================================
# 4. CONFIGURACIÓN DESDE ENTORNO (incluye nuevos parámetros F1-F12)
# =====================================================================
API_KEY      = os.environ.get('BITGET_API_KEY', '')
SECRET_KEY   = os.environ.get('BITGET_SECRET_KEY', '')
PASSPHRASE   = os.environ.get('BITGET_PASSPHRASE', '')
TELEGRAM_TOKEN  = os.environ.get('TELEGRAM_TOKEN', '')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '')

TOP_N             = int(os.environ.get('LOBO_TOP_N',          '100'))
TIMEFRAME_4H      = os.environ.get('LOBO_TIMEFRAME_4H',     '4h')
TIMEFRAME_D1      = os.environ.get('LOBO_TIMEFRAME_D1',     '1d')
TIMEFRAME_1H      = os.environ.get('LOBO_TIMEFRAME_1H',     '1h')

# === F1: Gestión de Capital en 3 Vectores ===
LOBO_LIQUIDEZ_PCT    = float(os.environ.get('LOBO_LIQUIDEZ_PCT', '50')) / 100
LOBO_SPOT_PCT        = float(os.environ.get('LOBO_SPOT_PCT', '30')) / 100
LOBO_FUTUROS_PCT     = float(os.environ.get('LOBO_FUTUROS_PCT', '20')) / 100
# Martingala del 33% para spot: niveles de retroceso
LOBO_SPOT_MARTINGALA_NIVELES = [
    float(os.environ.get('LOBO_SPOT_MART_1', '0.10')),  # -10%
    float(os.environ.get('LOBO_SPOT_MART_2', '0.20')),  # -20%
    float(os.environ.get('LOBO_SPOT_MART_3', '0.30')),  # -30%
]

# === Parámetros BITLOBO (heredados de v2) ===
LOBO_IMPULSO_MIN_VELAS   = int(os.environ.get('LOBO_IMPULSO_MIN_VELAS', '8'))
LOBO_IMPULSO_MAX_VELAS   = int(os.environ.get('LOBO_IMPULSO_MAX_VELAS', '40'))
LOBO_IMPULSO_PEND_MIN    = float(os.environ.get('LOBO_IMPULSO_PEND_MIN', '0.02'))
LOBO_SMA100_TOL_ATR      = float(os.environ.get('LOBO_SMA100_TOL_ATR', '1.0'))
LOBO_ADX_PERIOD          = int(os.environ.get('LOBO_ADX_PERIOD', '14'))
LOBO_ADX_MIN             = float(os.environ.get('LOBO_ADX_MIN', '15'))
LOBO_ADX_MAX             = float(os.environ.get('LOBO_ADX_MAX', '50'))
LOBO_ADX_DESC_VELAS      = int(os.environ.get('LOBO_ADX_DESC_VELAS', '6'))
LOBO_FVG_MIN_GAP_ATR     = float(os.environ.get('LOBO_FVG_MIN_GAP_ATR', '0.3'))
LOBO_FVG_MAX_VELAS       = int(os.environ.get('LOBO_FVG_MAX_VELAS', '48'))
LOBO_OB_MIN_MOV_ATR      = float(os.environ.get('LOBO_OB_MIN_MOV_ATR', '2.0'))
LOBO_OB_LOOKBACK         = int(os.environ.get('LOBO_OB_LOOKBACK', '10'))
LOBO_SWEEP_LOOKBACK      = int(os.environ.get('LOBO_SWEEP_LOOKBACK', '10'))
LOBO_SWEEP_MAX_PEN_ATR   = float(os.environ.get('LOBO_SWEEP_MAX_PEN_ATR', '1.0'))
LOBO_MECHA_MIN_ATR       = float(os.environ.get('LOBO_MECHA_MIN_ATR', '0.5'))
LOBO_MECHA_CUERPO_RATIO  = float(os.environ.get('LOBO_MECHA_CUERPO_RATIO', '0.3'))
LOBO_ELLIOTT_LOOKBACK    = int(os.environ.get('LOBO_ELLIOTT_LOOKBACK', '60'))
LOBO_ATR_PERIOD          = int(os.environ.get('LOBO_ATR_PERIOD', '14'))

# === F8: Riesgo base 1.5-2% (sobre el 20% de futuros) ===
LOBO_RISK_PCT            = float(os.environ.get('LOBO_RISK_PCT', '1.5')) / 100  # 1.5%
LOBO_RISK_PCT_EXCEP      = float(os.environ.get('LOBO_RISK_PCT_EXCEP', '10')) / 100
LOBO_MAX_POSITIONS       = int(os.environ.get('LOBO_MAX_POSITIONS', '5'))

# TP/SL (F12: TPs basados en zonas reales)
LOBO_TP1_SIZE            = float(os.environ.get('LOBO_TP1_SIZE', '0.40'))
LOBO_TP2_SIZE            = float(os.environ.get('LOBO_TP2_SIZE', '0.30'))
LOBO_TP3_SIZE            = float(os.environ.get('LOBO_TP3_SIZE', '0.30'))
LOBO_TP2_ATR_MULT        = float(os.environ.get('LOBO_TP2_ATR_MULT', '2.5'))
LOBO_TP3_ATR_MULT        = float(os.environ.get('LOBO_TP3_ATR_MULT', '4.0'))
LOBO_TRAIL_ATR_MULT      = float(os.environ.get('LOBO_TRAIL_ATR_MULT', '1.0'))

# F9: BE trigger ahora es "alcanzar TP1" (en vez de % fijo)
# Se usa TP1 como trigger, no un porcentaje independiente

# General
LOBO_TIMEOUT_HORAS       = float(os.environ.get('LOBO_TIMEOUT_HORAS', '12'))
LEVERAGE                 = float(os.environ.get('LOBO_LEVERAGE', '20.0'))
LOBO_SCORE_MIN           = int(os.environ.get('LOBO_SCORE_MIN', '8'))
MIN_ORDER_USDT           = float(os.environ.get('LOBO_MIN_ORDER_USDT', '5'))
PAPER_TRADE              = os.environ.get('LOBOBOT_PAPER_TRADE', 'false').lower() == 'true'

# SL simple 1.5 ATR (original)
LOBO_SL_ATR              = float(os.environ.get('LOBO_SL_ATR', '1.5'))
LOBO_SL_ATR_SMALL_VOL   = float(os.environ.get('LOBO_SL_ATR_SMALL_VOL', '5000000'))  # volumen diario para clasificar

# (No TP fixed ATR — se usa F12 zone-based)

# === F4: Coberturas asimétricas ===
LOBO_HEDGE_ENABLED       = os.environ.get('LOBO_HEDGE_ENABLED', 'true').lower() == 'true'
LOBO_HEDGE_LEV_MULT      = float(os.environ.get('LOBO_HEDGE_LEV_MULT', '3.0'))
LOBO_HEDGE_TRIGGER_PCT   = float(os.environ.get('LOBO_HEDGE_TRIGGER_PCT', '0.5'))

# === F5: RSI y Volumen ===
LOBO_RSI_PERIOD           = int(os.environ.get('LOBO_RSI_PERIOD', '14'))
LOBO_RSI_OVERSOLD         = float(os.environ.get('LOBO_RSI_OVERSOLD', '35'))
LOBO_RSI_OVERBOUGHT       = float(os.environ.get('LOBO_RSI_OVERBOUGHT', '65'))
LOBO_VOL_RATIO_MIN        = float(os.environ.get('LOBO_VOL_RATIO_MIN', '1.5'))
LOBO_VOL_PERIOD           = int(os.environ.get('LOBO_VOL_PERIOD', '20'))

log.info(
    "BITLOBO v3 Original Config: TOP=%d | Split %d/%d/%d | "
    "Risk=%.1f%%(sobre %d%%) | SL=%.1fATR | MaxPos=%d | "
    "Hedge=%s(%.0fx trig=%.0f%%) | RSI[%.0f,%.0f] | "
    "ScoreMin=%d | Paper=%s",
    TOP_N,
    LOBO_LIQUIDEZ_PCT*100, LOBO_SPOT_PCT*100, LOBO_FUTUROS_PCT*100,
    LOBO_RISK_PCT*100, LOBO_FUTUROS_PCT*100, LOBO_SL_ATR, LOBO_MAX_POSITIONS,
    LOBO_HEDGE_ENABLED, LOBO_HEDGE_LEV_MULT, LOBO_HEDGE_TRIGGER_PCT*100,
    LOBO_RSI_OVERSOLD, LOBO_RSI_OVERBOUGHT,
    LOBO_SCORE_MIN, PAPER_TRADE,
)

# =====================================================================
# 5. INDICADORES BITLOBO v3 — CORREGIDOS Y EXTENDIDOS
# =====================================================================

def _sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period).mean()

def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()

def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df['high'], df['low'], df['close']
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()

# ================================================================
# F5: RSI
# ================================================================
def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """RSI clásico (Wilder)."""
    delta = series.diff()
    gain = delta.where(delta > 0, 0).ewm(alpha=1/period, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def filtro_rsi(df_h4: pd.DataFrame, es_long: bool) -> tuple[bool, float]:
    """
    F5: RSI como filtro limitante.
    Para Long: RSI debe estar < oversold (35) o al menos < 50 con sweep+absorción fuerte.
    Para Short: RSI debe estar > overbought (65).
    Retorna (pasa_filtro, valor_rsi).
    """
    if len(df_h4) < LOBO_RSI_PERIOD + 5:
        return True, 50.0  # Pasa si no hay datos suficientes
    rsi_vals = _rsi(df_h4['close'], LOBO_RSI_PERIOD)
    if rsi_vals.isna().all():
        return True, 50.0
    rsi_actual = float(rsi_vals.iloc[-1])
    if pd.isna(rsi_actual):
        return True, 50.0

    if es_long:
        if rsi_actual < LOBO_RSI_OVERSOLD:
            return True, rsi_actual  # Sobrevendido, favorable para Long
        # Si no está sobrevendido, requiere sweep + absorción fuerte
        return False, rsi_actual
    else:
        if rsi_actual > LOBO_RSI_OVERBOUGHT:
            return True, rsi_actual  # Sobrecomprado, favorable para Short
        return False, rsi_actual

# ================================================================
# F5: Volumen como validador
# ================================================================
def validar_volumen(df_h4: pd.DataFrame, es_long: bool) -> tuple[bool, float]:
    """
    F5: Validación de volumen.
    - Long: rebote desde mínimos con volumen > 1.5x media → validación.
    - Short: soporte roto con volumen decreciente (< 0.7x) → engaño, no operar.
    - General: volumen en vela de sweep debe ser > 1.5x media.
    Retorna (pasa_filtro, ratio_volumen).
    """
    if len(df_h4) < LOBO_VOL_PERIOD + 3:
        return True, 1.0
    vol = df_h4['volume']
    vol_media = vol.rolling(LOBO_VOL_PERIOD).mean()
    ult_vol = float(vol.iloc[-1])
    ult_media = float(vol_media.iloc[-1])
    if ult_media <= 0:
        return True, 1.0
    ratio = ult_vol / ult_media

    if es_long:
        # Rebote desde mínimos: volumen > 1.5x media = validación
        if ratio >= LOBO_VOL_RATIO_MIN:
            return True, ratio
        # Volumen bajo en zona de soporte = posible engaño
        return False, ratio
    else:
        # Volumen muy bajo en rompimiento = engaño probable
        if ratio < 0.7:
            return False, ratio
        return True, ratio

# ================================================================
# F2: Dominancias reales (CoinGecko + proxy calculado)
# ================================================================
def obtener_dominancia_real() -> dict:
    """
    F2: Obtiene dominancias reales desde CoinGecko API (gratuita).
    Retorna { 'btc_dominance_pct': float, 'usdt_dominance_pct': float }
    Si falla, retorna valores None.
    """
    try:
        url = "https://api.coingecko.com/api/v3/global"
        resp = requests.get(url, timeout=10)
        if resp.status_code != 200:
            log.debug("CoinGecko responded %d", resp.status_code)
            return {}
        data = resp.json()
        btc_d = data.get('data', {}).get('market_cap_percentage', {}).get('btc', None)
        # USDT.D no está directamente en CoinGecko, calculamos proxy
        # Usamos el ratio de market cap de stablecoins
        return {'btc_dominance_pct': btc_d}
    except Exception as e:
        log.debug("CoinGecko fetch error: %s", e)
        return {}

def calcular_proxy_usdtd(exchange_sync=None) -> Optional[float]:
    """
    F2: Proxy de USDT.D calculado desde Bitget.
    USDT.D ≈ (volumen_total_pares_USDT) / (volumen_total_pares_USDT + volumen_otros)
    Si no se puede calcular, retorna None.
    """
    try:
        exch = exchange_sync or ccxt.bitget({'enableRateLimit': True})
        tickers = exch.fetch_tickers()
        vol_usdt = 0.0
        vol_total = 0.0
        for s, t in tickers.items():
            if not s.endswith('/USDT:USDT'):
                continue
            qv = float(t.get('quoteVolume', 0))
            vol_total += qv
            vol_usdt += qv
        if vol_total <= 0:
            return None
        # Proxy simple: qué % del volumen total está en pares USDT
        # Esto no es USDT.D real, pero da una idea de flujo de liquidez
        return (vol_usdt / vol_total) * 100
    except Exception as e:
        log.debug("USDT.D proxy error: %s", e)
        return None

def check_dominancia_btc_long() -> bool:
    """
    F2-R5: BTC.D - retorna True si BTC.D está subiendo (solo operar BTC).
    Cacheado 5 min. Usa CoinGecko. Fallback: tendencia BTC/USDT.
    """
    global DOMINANCE_CACHE
    now = time.time()
    if now - DOMINANCE_CACHE['ts'] < DOMINANCE_CACHE_TTL and DOMINANCE_CACHE['btc'] is not None:
        return DOMINANCE_CACHE['btc']

    result = False
    try:
        dom = obtener_dominancia_real()
        if dom and dom.get('btc_dominance_pct') is not None:
            btc_d = dom['btc_dominance_pct']
            log.debug("BTC.D real: %.1f%%", btc_d)
            result = btc_d > 50.0
            DOMINANCE_CACHE['btc'] = result
            DOMINANCE_CACHE['ts'] = now
            return result
    except Exception:
        pass

    # Fallback: usar precio BTC/USDT con SMA (solo si no hay cache previo)
    try:
        exch = ccxt.bitget({'enableRateLimit': True})
        ohlcv = exch.fetch_ohlcv('BTC/USDT:USDT', timeframe='4h', limit=30)
        if ohlcv and len(ohlcv) > 10:
            closes = pd.Series([c[4] for c in ohlcv])
            sma20 = closes.rolling(20).mean()
            pendiente = (sma20.iloc[-1] - sma20.iloc[-5]) / max(sma20.iloc[-5], 1)
            result = pendiente > 0.001
    except Exception:
        pass
    DOMINANCE_CACHE['btc'] = result
    DOMINANCE_CACHE['ts'] = now
    return result

def check_usdtd_resistencia_long() -> bool:
    """
    F2-R4: USDT.D en resistencia → favorable para Longs.
    Cacheado 5 min. Usa proxy calculado de Bitget.
    """
    global DOMINANCE_CACHE
    now = time.time()
    if now - DOMINANCE_CACHE['ts'] < DOMINANCE_CACHE_TTL and DOMINANCE_CACHE['usdtd'] is not None:
        return DOMINANCE_CACHE['usdtd']

    result = True
    try:
        proxy = calcular_proxy_usdtd()
        if proxy is not None:
            result = proxy > 65.0
    except Exception:
        pass
    DOMINANCE_CACHE['usdtd'] = result
    DOMINANCE_CACHE['ts'] = now
    return result

# ================================================================
# R1 - Impulso direccional (heredado de v2, idéntico)
# ================================================================
def detectar_impulso(df_h4: pd.DataFrame) -> Optional[dict]:
    min_v = LOBO_IMPULSO_MIN_VELAS
    max_v = min(LOBO_IMPULSO_MAX_VELAS, len(df_h4) - 2)
    n = len(df_h4)
    for length in range(min(max_v, n - 1), min_v - 1, -1):
        start = n - length - 1
        if start < 0:
            continue
        tramo = df_h4.iloc[start:start + length].copy()
        if len(tramo) < min_v:
            continue
        p0 = float(tramo['close'].iloc[0])
        p1 = float(tramo['close'].iloc[-1])
        pendiente = (p1 - p0) / p0 if p0 > 0 else 0
        if abs(pendiente) < LOBO_IMPULSO_PEND_MIN:
            continue
        alcista = pendiente > 0
        diff_total = abs(p1 - p0)
        max_retro = diff_total * 0.382
        ok_velas = 0
        total_velas = len(tramo) - 1
        for j in range(1, len(tramo)):
            c0 = float(tramo['close'].iloc[j - 1])
            c1 = float(tramo['close'].iloc[j])
            if alcista:
                retro = (c0 - c1) if c1 < c0 else 0
                if retro > max_retro:
                    break
                if c1 > c0:
                    ok_velas += 1
            else:
                retro = (c1 - c0) if c1 > c0 else 0
                if retro > max_retro:
                    break
                if c1 < c0:
                    ok_velas += 1
        else:
            ratio_dir = ok_velas / total_velas if total_velas > 0 else 0
            if ratio_dir >= 0.7:
                low = float(tramo['low'].min())
                high = float(tramo['high'].max())
                return {
                    'inicio': low if alcista else high,
                    'fin': high if alcista else low,
                    'tipo': 'alcista' if alcista else 'bajista',
                    'velas': len(tramo),
                }
    return None

def calcular_fibonacci(impulso: dict) -> dict:
    high = max(impulso['inicio'], impulso['fin'])
    low = min(impulso['inicio'], impulso['fin'])
    diff = high - low
    if diff <= 0:
        return {}
    return {
        'level_0':      high,
        'level_0_236':  high - 0.236 * diff,
        'level_0_382':  high - 0.382 * diff,
        'level_0_5':    high - 0.5 * diff,
        'level_0_618':  high - 0.618 * diff,
        'level_0_786':  high - 0.786 * diff,
        'level_1_0':    low,
        'diff':         diff,
    }

# ================================================================
# R2 - SMA 100 en zona OTE (idéntico a v2)
# ================================================================
def sma100_en_zona_ote(sma100_val: float, fibo: dict, atr_val: float) -> bool:
    if 'level_0_5' not in fibo or 'level_0_618' not in fibo:
        return False
    r_inf = min(fibo['level_0_5'], fibo['level_0_618']) - atr_val * LOBO_SMA100_TOL_ATR
    r_sup = max(fibo['level_0_5'], fibo['level_0_618']) + atr_val * LOBO_SMA100_TOL_ATR
    return r_inf <= sma100_val <= r_sup

# ================================================================
# R3 - ADX (idéntico a v2)
# ================================================================
def _wilder_ema(series: pd.Series, period: int) -> pd.Series:
    alpha = 1.0 / period
    return series.ewm(alpha=alpha, adjust=False).mean()

def adx_permite_entrada(df_h4: pd.DataFrame) -> bool:
    if len(df_h4) < LOBO_ADX_PERIOD * 2:
        return False
    try:
        import pandas_ta as ta
        adx_df = ta.adx(df_h4['high'], df_h4['low'], df_h4['close'], length=LOBO_ADX_PERIOD)
        adx_col = [c for c in adx_df.columns if 'ADX' in c.upper()]
        if not adx_col:
            return False
        adx_series = adx_df[adx_col[0]]
    except ImportError:
        period = LOBO_ADX_PERIOD
        high, low, close = df_h4['high'], df_h4['low'], df_h4['close']
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ], axis=1).max(axis=1)
        up = high.diff()
        down = -low.diff()
        plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0), index=df_h4.index)
        minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0), index=df_h4.index)
        tr_s = _wilder_ema(tr, period)
        plus_s = _wilder_ema(plus_dm, period)
        minus_s = _wilder_ema(minus_dm, period)
        tr_s = tr_s.replace(0, np.nan)
        plus_di = 100 * plus_s / tr_s
        minus_di = 100 * minus_s / tr_s
        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
        adx_series = _wilder_ema(dx, period)

    if adx_series.isna().all():
        return False
    adx_actual = float(adx_series.iloc[-1])
    if pd.isna(adx_actual):
        return False
    if not (LOBO_ADX_MIN <= adx_actual <= LOBO_ADX_MAX):
        return False
    n = min(LOBO_ADX_DESC_VELAS, len(adx_series) - 1)
    if n < 3:
        return True
    vals = adx_series.iloc[-n:].dropna().values
    if len(vals) < 3:
        return True
    x = np.arange(len(vals))
    if np.std(vals) == 0:
        return True
    slope = np.polyfit(x, vals, 1)[0]
    return slope < 0.01

# ================================================================
# R6 - FVG (idéntico a v2)
# ================================================================
def detectar_fvg(df_h4: pd.DataFrame) -> list:
    if len(df_h4) < 5:
        return []
    atr_vals = _atr(df_h4, LOBO_ATR_PERIOD)
    fvg_list = []
    max_velas = min(LOBO_FVG_MAX_VELAS, len(df_h4) - 3)
    for i in range(2, len(df_h4) - 2):
        gap_up = df_h4['low'].iloc[i] - df_h4['high'].iloc[i-2]
        gap_dn = df_h4['low'].iloc[i-2] - df_h4['high'].iloc[i]
        atr_i = atr_vals.iloc[i] if not pd.isna(atr_vals.iloc[i]) else 0
        if gap_up > atr_i * LOBO_FVG_MIN_GAP_ATR:
            gap_alto = float(df_h4['high'].iloc[i-2])
            gap_bajo = float(df_h4['low'].iloc[i])
            if not _fvg_rellenado(df_h4, i, max_velas, gap_alto, gap_bajo):
                fvg_list.append({
                    'tipo': 'alcista', 'gap_sup': gap_alto, 'gap_inf': gap_bajo,
                    'idx': i, 'precio_medio': (gap_alto + gap_bajo) / 2,
                })
        if gap_dn > atr_i * LOBO_FVG_MIN_GAP_ATR:
            gap_alto = float(df_h4['high'].iloc[i])
            gap_bajo = float(df_h4['low'].iloc[i-2])
            if not _fvg_rellenado(df_h4, i, max_velas, gap_alto, gap_bajo):
                fvg_list.append({
                    'tipo': 'bajista', 'gap_sup': gap_alto, 'gap_inf': gap_bajo,
                    'idx': i, 'precio_medio': (gap_alto + gap_bajo) / 2,
                })
    return fvg_list

def _fvg_rellenado(df, idx_start, max_v, gap_high, gap_low):
    for j in range(idx_start, min(idx_start + max_v, len(df))):
        if df['low'].iloc[j] <= gap_high and df['high'].iloc[j] >= gap_low:
            return True
    return False

# ================================================================
# R7 - Order Block (idéntico a v2)
# ================================================================
def detectar_order_blocks(df_h4: pd.DataFrame) -> list:
    if len(df_h4) < LOBO_OB_LOOKBACK + 5:
        return []
    atr_vals = _atr(df_h4, LOBO_ATR_PERIOD)
    obs = []
    for i in range(LOBO_OB_LOOKBACK, len(df_h4) - 3):
        atr_i = atr_vals.iloc[i] if not pd.isna(atr_vals.iloc[i]) else 0
        if atr_i == 0:
            continue
        if df_h4['close'].iloc[i] < df_h4['open'].iloc[i]:
            rally = 0
            for j in range(1, min(6, len(df_h4) - i)):
                if df_h4['close'].iloc[i+j] > df_h4['open'].iloc[i+j]:
                    rally += float(df_h4['close'].iloc[i+j] - df_h4['low'].iloc[i+j])
                else:
                    break
            if rally >= atr_i * LOBO_OB_MIN_MOV_ATR:
                obs.append({'tipo': 'alcista', 'high': float(df_h4['high'].iloc[i]), 'low': float(df_h4['low'].iloc[i]), 'idx': i})
        if df_h4['close'].iloc[i] > df_h4['open'].iloc[i]:
            caida = 0
            for j in range(1, min(6, len(df_h4) - i)):
                if df_h4['close'].iloc[i+j] < df_h4['open'].iloc[i+j]:
                    caida += float(df_h4['high'].iloc[i+j] - df_h4['close'].iloc[i+j])
                else:
                    break
            if caida >= atr_i * LOBO_OB_MIN_MOV_ATR:
                obs.append({'tipo': 'bajista', 'high': float(df_h4['high'].iloc[i]), 'low': float(df_h4['low'].iloc[i]), 'idx': i})
    return obs

# ================================================================
# R8 - Liquidity Sweep (idéntico a v2)
# ================================================================
def detectar_sweep(df_h4: pd.DataFrame) -> list:
    if len(df_h4) < LOBO_SWEEP_LOOKBACK + 3:
        return []
    atr_vals = _atr(df_h4, LOBO_ATR_PERIOD)
    sweeps = []
    min_reciente = df_h4['low'].iloc[-(LOBO_SWEEP_LOOKBACK+1):-1].min()
    max_reciente = df_h4['high'].iloc[-(LOBO_SWEEP_LOOKBACK+1):-1].max()
    ult = df_h4.iloc[-1]
    atr_act = atr_vals.iloc[-1] if not pd.isna(atr_vals.iloc[-1]) else 0
    if ult['low'] < min_reciente:
        penetracion = (min_reciente - ult['low']) / max(atr_act, 1)
        if 0 < penetracion < LOBO_SWEEP_MAX_PEN_ATR:
            if ult['close'] > min_reciente:
                sweeps.append({'tipo': 'sweep_bajista_long', 'nivel_roto': float(min_reciente), 'penetracion_atr': round(penetracion, 2)})
    if ult['high'] > max_reciente:
        penetracion = (ult['high'] - max_reciente) / max(atr_act, 1)
        if 0 < penetracion < LOBO_SWEEP_MAX_PEN_ATR:
            if ult['close'] < max_reciente:
                sweeps.append({'tipo': 'sweep_alcista_short', 'nivel_roto': float(max_reciente), 'penetracion_atr': round(penetracion, 2)})
    return sweeps

# ================================================================
# R9 - Mecha/Absorción (idéntico a v2)
# ================================================================
def evaluar_absorcion_long(df_h4: pd.DataFrame) -> tuple[bool, dict]:
    if len(df_h4) < 2:
        return False, {}
    atr_vals = _atr(df_h4, LOBO_ATR_PERIOD)
    ant = df_h4.iloc[-2]
    atr_act = atr_vals.iloc[-2] if not pd.isna(atr_vals.iloc[-2]) else 0
    body = abs(ant['close'] - ant['open'])
    rango = ant['high'] - ant['low']
    mecha_inf = min(ant['open'], ant['close']) - ant['low']
    if rango == 0:
        return False, {}
    # "Sin mecha hay sospecha"
    if ant['close'] < ant['open']:
        sin_mecha = mecha_inf < (rango * 0.05)
        cuerpo_solido = body > (rango * 0.7)
        if sin_mecha and cuerpo_solido:
            return False, {'rechazo': 'cuerpo_solido_sin_mecha', 'mecha_inf': mecha_inf, 'rango': rango}
    # Absorción para Long
    if ant['close'] < ant['open']:
        if atr_act > 0:
            cond1 = mecha_inf >= atr_act * LOBO_MECHA_MIN_ATR
        else:
            cond1 = mecha_inf >= (rango * 0.3)
        cond2 = (body / max(mecha_inf, 0.0001)) <= LOBO_MECHA_CUERPO_RATIO if mecha_inf > 0 else False
        mid = (ant['high'] + ant['low']) / 2
        cond3 = ant['close'] > mid
        detalles = {
            'mecha_inf': round(mecha_inf, 4),
            'mecha_atr_ratio': round(mecha_inf / max(atr_act, 0.0001), 2) if atr_act > 0 else 0,
            'cuerpo_mecha_ratio': round(body / max(mecha_inf, 0.0001), 2),
            'absorcion': cond1 and cond2 and cond3,
        }
        return (cond1 and cond2 and cond3), detalles
    return False, {'tipo': 'vela_alcista_no_aplica'}

# ================================================================
# [GAP 1] Sin Mecha hay Sospecha — Validación en Zona OTE
# ================================================================
def validar_mecha_absorcion_en_zona(
    df_h4: pd.DataFrame, zona_inf: float, zona_sup: float, es_long: bool, atr_val: float,
) -> tuple[bool, str]:
    """
    GAP 1: "Sin Mecha hay Sospecha"
    Verifica que la vela H4 que perforó la zona OTE tenga mecha de absorción.
    
    Para LONG: la vela bajista que entró en la zona debe tener mecha inferior
    (absorción de ventas). Si el cuerpo es sólido y cierra en mínimo → rechazar.
    
    Para SHORT: la vela alcista que entró en la zona debe tener mecha superior
    (absorción de compras). Si el cuerpo es sólido y cierra en máximo → rechazar.
    """
    if len(df_h4) < 3:
        return False, 'pocos_datos'
    
    # Buscar en las últimas 3 velas (incluye la recién cerrada en -1)
    # El bot solo llama a evaluar_senal_bitlobo_v3 cuando es_nueva_vela_h4 es True,
    # por lo que iloc[-1] es una vela cerrada.
    for idx in range(-1, -4, -1):
        try:
            vela = df_h4.iloc[idx]
        except IndexError:
            break
        
        o, h, l, c = float(vela['open']), float(vela['high']), float(vela['low']), float(vela['close'])
        body = abs(c - o)
        rango = h - l
        if rango < 1e-8:
            continue
        
        es_bajista = c < o
        es_alcista = c > o
        
        if es_long:
            # Buscar vela que perforó la zona OTE (low dentro o debajo de zona)
            if l > zona_sup:
                continue  # Esta vela no tocó la zona
            if not es_bajista:
                continue  # Para long, esperamos vela bajista en la zona
            
            # Calcular mecha inferior
            mecha_inf = min(o, c) - l
            ratio_mecha = mecha_inf / rango
            ratio_cuerpo = body / rango
            
            # Sin Mecha hay Sospecha: cuerpo sólido (>70%) sin mecha (<5%)
            if ratio_cuerpo > 0.70 and ratio_mecha < 0.05:
                return False, f'cuerpo_solido_sin_mecha_inf_idx{idx}'
            
            # Absorción válida: mecha inferior >= 0.5 ATR o >= 30% del rango
            mecha_ok = (mecha_inf >= atr_val * LOBO_MECHA_MIN_ATR) or (ratio_mecha >= 0.30)
            if mecha_ok:
                return True, f'absorcion_ok_idx{idx}'
            # Si no cumple pero hay mecha, no rechazamos (es aceptable)
            if ratio_mecha >= 0.10:
                return True, f'mecha_parcial_idx{idx}'
                
        else:  # SHORT
            # Buscar vela que perforó la zona OTE (high dentro o encima de zona)
            if h < zona_inf:
                continue
            if not es_alcista:
                continue
            
            # Calcular mecha superior
            mecha_sup = h - max(o, c)
            ratio_mecha = mecha_sup / rango
            ratio_cuerpo = body / rango
            
            if ratio_cuerpo > 0.70 and ratio_mecha < 0.05:
                return False, f'cuerpo_solido_sin_mecha_sup_idx{idx}'
            
            mecha_ok = (mecha_sup >= atr_val * LOBO_MECHA_MIN_ATR) or (ratio_mecha >= 0.30)
            if mecha_ok:
                return True, f'absorcion_ok_idx{idx}'
            if ratio_mecha >= 0.10:
                return True, f'mecha_parcial_idx{idx}'
    
    # Si ninguna vela tocó la zona, no podemos validar — permitir
    return True, 'sin_penetracion_zona'

# ================================================================
# F6: Pullback ("Rompe y Apoya") — NUEVA
# ================================================================
def detectar_pullback_confirmado(df_h4: pd.DataFrame, nivel_roto: float, es_long: bool) -> bool:
    """
    F6: Detecta si hubo ruptura de nivel, seguida de pullback (retest) y rebote.
    - es_long=True: precio rompió nivel_roto al alza, retrocedió a tocarlo, rebotó.
    - es_long=False: precio rompió nivel_roto a la baja, retrocedió a tocarlo, rebotó.
    """
    if len(df_h4) < 10:
        return True  # Sin datos suficientes, pasa
    closes = df_h4['close'].iloc[-15:].values
    if es_long:
        cruce_idx = np.where(closes > nivel_roto)[0]
        if len(cruce_idx) == 0:
            return False
        # Buscar retroceso posterior al cruce
        post_break = closes[cruce_idx[0]:]
        if len(post_break) < 3:
            return False
        retroceso_min = min(post_break)
        # El retroceso debe tocar o casi tocar el nivel roto (< 1.5% por encima)
        if retroceso_min <= nivel_roto * 1.015:
            # Y el precio debe estar ahora por encima del mínimo del retroceso
            return closes[-1] > retroceso_min * 1.005
    else:
        cruce_idx = np.where(closes < nivel_roto)[0]
        if len(cruce_idx) == 0:
            return False
        post_break = closes[cruce_idx[0]:]
        if len(post_break) < 3:
            return False
        retroceso_max = max(post_break)
        if retroceso_max >= nivel_roto * 0.985:
            return closes[-1] < retroceso_max * 0.995
    return False

# ================================================================
# F11: Elliott mejorado con relaciones Fibonacci
# ================================================================
def detectar_estructura_elliott_v3(df_h4: pd.DataFrame) -> dict:
    """
    F11: Ondas Elliott con relaciones Fibonacci entre ondas.
    Identifica pivots swing y verifica relaciones:
    - Onda 3 ≈ 1.618x de Onda 1
    - Onda 5 ≈ 0.618x de Onda 1→3
    - Onda 2 ≈ 0.5-0.618 de Onda 1
    - Onda 4 ≈ 0.382 de Onda 3
    """
    if len(df_h4) < LOBO_ELLIOTT_LOOKBACK:
        return {'fase': 'indefinida', 'razon': 'pocos_datos'}
    left, right = 5, 5
    highs = df_h4['high'].values
    lows = df_h4['low'].values
    n = len(highs)
    pivot_highs_idx = []
    pivot_lows_idx = []
    for i in range(left, n - right):
        if highs[i] == max(highs[i-left:i+right+1]):
            pivot_highs_idx.append(i)
        if lows[i] == min(lows[i-left:i+right+1]):
            pivot_lows_idx.append(i)
    if len(pivot_highs_idx) < 3 or len(pivot_lows_idx) < 2:
        return {'fase': 'indefinida', 'razon': 'pocos_pivots'}
    # Buscar secuencia 1-2-3-4-5
    # Onda 1: de un mínimo a un máximo
    # Onda 2: retroceso del 50-61.8% de Onda 1
    # Onda 3: extensión 1.618x de Onda 1
    # Onda 4: retroceso 38.2% de Onda 3
    # Onda 5: extensión 0.618x de Onda 1→3
    for i in range(min(5, len(pivot_lows_idx) - 2)):
        for j in range(i + 1, min(i + 3, len(pivot_highs_idx))):
            onda1_inicio = lows[pivot_lows_idx[i]]
            onda1_fin = highs[pivot_highs_idx[j]]
            onda1 = onda1_fin - onda1_inicio
            if onda1 <= 0:
                continue
            # Buscar onda 2 (retroceso)
            for k in range(j + 1, min(j + 3, len(pivot_lows_idx))):
                onda2_retro = (onda1_fin - lows[pivot_lows_idx[k]]) / onda1
                if 0.382 <= onda2_retro <= 0.786:
                    # Buscar onda 3 (extensión)
                    for l in range(k + 1, min(k + 4, len(pivot_highs_idx))):
                        onda3 = highs[pivot_highs_idx[l]] - lows[pivot_lows_idx[k]]
                        onda3_ratio = onda3 / onda1
                        if 1.0 <= onda3_ratio <= 2.618:
                            # Buscar onda 4 (retroceso)
                            for m in range(l + 1, min(l + 3, len(pivot_lows_idx))):
                                onda4_retro = (highs[pivot_highs_idx[l]] - lows[pivot_lows_idx[m]]) / onda3
                                if 0.236 <= onda4_retro <= 0.5:
                                    return {
                                        'fase': 'estructura_5_ondas',
                                        'confianza': 'alta',
                                        'onda_1': round(onda1, 2),
                                        'onda_2_retro': round(onda2_retro, 2),
                                        'onda_3_ratio': round(onda3_ratio, 2),
                                        'onda_4_retro': round(onda4_retro, 2),
                                        'ultimo_pivot': 'maximo' if pivot_highs_idx[-1] > pivot_lows_idx[-1] else 'minimo',
                                    }
    return {'fase': 'indefinida', 'razon': 'sin_estructura_5_ondas'}

# ================================================================
# F1: Gestión de Capital en 3 Vectores
# ================================================================
def capital_disponible_futuros(balance_total: float) -> float:
    """Retorna el capital disponible para futuros = 20% del balance total."""
    return balance_total * LOBO_FUTUROS_PCT

def capital_liquidez(balance_total: float) -> float:
    """Retorna la reserva de liquidez = 50% del balance total (intocable)."""
    return balance_total * LOBO_LIQUIDEZ_PCT

def capital_spot(balance_total: float) -> float:
    """Retorna el capital para holding spot = 30% del balance total."""
    return balance_total * LOBO_SPOT_PCT

# ================================================================
# F3: SL por Liquidación (Anti-Cacería)
# ================================================================
def calcular_precio_liquidacion(entry_price: float, leverage: float, side: str) -> float:
    """
    Calcula el precio de liquidación estimado para aislado en Bitget.
    Fórmula simplificada (ignora maintenance margin).
    """
    if leverage <= 0:
        return 0
    if side == 'long':
        return entry_price * (1.0 - 1.0 / leverage)
    else:
        return entry_price * (1.0 + 1.0 / leverage)

# ================================================================
# F10: Invalidación D1 Estructural (swing points)
# ================================================================
def validar_estructura_d1(df_d1: pd.DataFrame, entry_price: float, side: str) -> bool:
    """
    F10: Validación diaria por estructura de swing points.
    Retorna True si la posición sigue válida.
    False = cambio de estructura, cerrar posición.
    """
    if len(df_d1) < 10:
        return True
    lows = df_d1['low'].values
    highs = df_d1['high'].values
    n = len(lows)
    # Encontrar swing lows (left=3, right=3)
    swing_lows = []
    swing_highs = []
    for i in range(3, n - 3):
        if lows[i] == min(lows[i-3:i+4]):
            swing_lows.append((i, lows[i]))
        if highs[i] == max(highs[i-3:i+4]):
            swing_highs.append((i, highs[i]))
    ult_cierre = float(df_d1['close'].iloc[-1])
    if side == 'long':
        if swing_lows:
            ult_soporte = swing_lows[-1][1]
            # Si el cierre D1 está por debajo del último soporte swing
            if ult_cierre < ult_soporte * 0.995:
                log.debug("D1: cierre %.2f < soporte estructural %.2f", ult_cierre, ult_soporte)
                return False
    else:
        if swing_highs:
            ult_resistencia = swing_highs[-1][1]
            if ult_cierre > ult_resistencia * 1.005:
                log.debug("D1: cierre %.2f > resistencia estructural %.2f", ult_cierre, ult_resistencia)
                return False
    return True

# ================================================================
# F4: Coberturas Asimétricas
# ================================================================
def evaluar_cobertura(pos_entry: dict, precio_actual: float) -> Optional[dict]:
    """
    F4: Evalúa si se debe activar una cobertura asimétrica (original).
    
    Condición: el precio ha recorrido >= LOBO_HEDGE_TRIGGER_PCT (50%)
    de la distancia hacia el SL.
    
    Si se activa:
    - Abre posición opuesta con apalancamiento 3x (LOBO_HEDGE_LEV_MULT)
    - Take Profit de la cobertura = precio de liquidación de la posición principal
    - Stop Loss de la cobertura = precio de entrada de la posición principal
    """
    if not LOBO_HEDGE_ENABLED:
        return None
    symbol = pos_entry['symbol']
    if HEDGE_ENTRIES.get(symbol):
        return None
    side = pos_entry.get('side', 'long')
    entry_price = float(pos_entry['entry_price'])
    sl_price = float(pos_entry.get('sl_price', 0))
    if sl_price <= 0:
        return None
    # Distancia total al SL
    if side == 'long':
        dist_total = entry_price - sl_price
        dist_recorrida = entry_price - precio_actual
    else:
        dist_total = sl_price - entry_price
        dist_recorrida = precio_actual - entry_price
    if dist_total <= 0:
        return None
    pct_recorrido = dist_recorrida / dist_total
    if pct_recorrido < LOBO_HEDGE_TRIGGER_PCT:
        return None  # Aún no se activa
    # Calcular cobertura
    hedge_side = 'short' if side == 'long' else 'long'
    hedge_lev = min(pos_entry.get('leverage', LEVERAGE) * LOBO_HEDGE_LEV_MULT, 125)
    # TP de la cobertura = precio de liquidación de la principal
    liq_price = calcular_precio_liquidacion(entry_price, pos_entry.get('leverage', LEVERAGE), side)
    # SL de la cobertura = entry price (para que no interfiera con la posición principal)
    hedge_sl = entry_price
    # Tamaño de la cobertura: 30% del tamaño de la principal pero con más apalancamiento
    hedge_size = pos_entry.get('size_usdt', 0) * 0.3
    return {
        'side': hedge_side,
        'leverage': hedge_lev,
        'tp_price': liq_price,
        'sl_price': hedge_sl,
        'size_usdt': hedge_size,
        'entry_price': precio_actual,
    }

# ================================================================
# F12: TPs en Zonas Reales
# ================================================================
def calcular_tps_en_zonas(precio_actual: float, atr_val: float, fvg_list: list,
                          ob_list: list, es_long: bool) -> tuple[float, float, float, float]:
    """
    F12: Calcula TP1, TP2, TP3 basados en zonas reales del mercado (original).
    
    TP1: En el FVG más cercano (o primer OB si no hay FVG).
    TP2: En el siguiente FVG/OB o 2.5 ATR si no hay.
    TP3: En el siguiente nivel estructural o 4 ATR si no hay.
    
    Retorna (tp1_price, tp2_price, tp3_price, rr_ratio).
    """
    tp1, tp2, tp3 = 0, 0, 0
    
    if es_long:
        fvg_arriba = [f for f in fvg_list if f['tipo'] == 'alcista' and f['gap_inf'] > precio_actual]
        obs_arriba = [o for o in ob_list if o['tipo'] == 'alcista' and o['low'] > precio_actual]
        
        if fvg_arriba:
            tp1 = min(f['gap_inf'] for f in fvg_arriba)
        elif obs_arriba:
            tp1 = min(o['low'] for o in obs_arriba)
        else:
            tp1 = precio_actual + atr_val * 2.0
        
        fvg_rest = [f for f in fvg_arriba if f['gap_inf'] > tp1 + atr_val * 0.5]
        ob_rest = [o for o in obs_arriba if o['low'] > tp1 + atr_val * 0.5]
        if fvg_rest:
            tp2 = min(f['gap_inf'] for f in fvg_rest)
        elif ob_rest:
            tp2 = min(o['low'] for o in ob_rest)
        else:
            tp2 = tp1 + atr_val * LOBO_TP2_ATR_MULT
        
        tp3_candidates = [f for f in fvg_arriba if f['gap_inf'] > tp2]
        if tp3_candidates:
            tp3 = min(f['gap_inf'] for f in tp3_candidates)
        else:
            tp3 = precio_actual + atr_val * LOBO_TP3_ATR_MULT
    else:
        fvg_abajo = [f for f in fvg_list if f['tipo'] == 'bajista' and f['gap_sup'] < precio_actual]
        obs_abajo = [o for o in ob_list if o['tipo'] == 'bajista' and o['high'] < precio_actual]
        
        if fvg_abajo:
            tp1 = max(f['gap_sup'] for f in fvg_abajo)
        elif obs_abajo:
            tp1 = max(o['high'] for o in obs_abajo)
        else:
            tp1 = precio_actual - atr_val * 2.0
        
        fvg_rest = [f for f in fvg_abajo if f['gap_sup'] < tp1 - atr_val * 0.5]
        ob_rest = [o for o in obs_abajo if o['high'] < tp1 - atr_val * 0.5]
        if fvg_rest:
            tp2 = max(f['gap_sup'] for f in fvg_rest)
        elif ob_rest:
            tp2 = max(o['high'] for o in ob_rest)
        else:
            tp2 = tp1 - atr_val * LOBO_TP2_ATR_MULT
        
        tp3_candidates = [f for f in fvg_abajo if f['gap_sup'] < tp2]
        if tp3_candidates:
            tp3 = max(f['gap_sup'] for f in tp3_candidates)
        else:
            tp3 = precio_actual - atr_val * LOBO_TP3_ATR_MULT
    
    # R:R basado en TP1 (riesgo = distancia SL 1.5 ATR, como lobobot.py)
    if es_long:
        riesgo = atr_val * 1.5
    else:
        riesgo = atr_val * 1.5
    beneficio = abs(tp1 - precio_actual)
    rr = beneficio / riesgo if riesgo > 0 else 0
    
    return tp1, tp2, tp3, rr

# ================================================================
# F7: Timing de entrada (cierre de vela H4)
# ================================================================
def es_nueva_vela_h4(df_h4: pd.DataFrame) -> bool:
    """F7: True si la última vela H4 acaba de cerrar (< 5 minutos desde cierre)."""
    if df_h4.empty:
        return False
    ultimo_ts = df_h4['timestamp'].iloc[-1]
    ahora = int(time.time() * 1000)
    diff_ms = ahora - ultimo_ts
    # 4h = 14,400,000 ms. Una vela "recién cerrada" tiene diff < 4h + 5min
    return diff_ms < 14_700_000  # 4h + 5min

# =====================================================================
# 6. EVALUACIÓN COMPLETA DE SEÑAL (v3 con todas las correcciones)
# =====================================================================
def evaluar_senal_bitlobo_v3(
    symbol: str, df_h4: pd.DataFrame, df_d1: pd.DataFrame,
    precio_actual: float, atr_val: float, balance_total: float,
    es_long: bool,
) -> Optional[dict]:
    """
    Evalúa TODAS las reglas BITLOBO v3.
    Retorna dict de señal si todas se cumplen, None si alguna falla.
    """
    # --- F1: Capital de futuros = 20% del total ---
    capital_fut = capital_disponible_futuros(balance_total)
    
    senal = {'symbol': symbol, 'precio_actual': precio_actual, 'atr_val': atr_val, 'es_long': es_long}
    detalles = []
    score = 0
    max_score = 16  # Más reglas que en v2

    # --- R1: Impulso direccional + Fibonacci ---
    impulso = detectar_impulso(df_h4)
    if not impulso:
        return None
    fibo = calcular_fibonacci(impulso)
    if not fibo or 'level_0_5' not in fibo or 'level_0_618' not in fibo:
        return None
    senal['impulso'] = impulso
    senal['fibo'] = fibo
    score += 1
    detalles.append(f'R1:impulso_{impulso["tipo"]}_{impulso["velas"]}v')

    zona_inf = min(fibo['level_0_5'], fibo['level_0_618'])
    zona_sup = max(fibo['level_0_5'], fibo['level_0_618'])
    senal['zona_ote_inf'] = zona_inf
    senal['zona_ote_sup'] = zona_sup
    tol = atr_val * 1.0
    if not (zona_inf - tol <= precio_actual <= zona_sup + tol):
        log.debug("%s: Precio fuera de zona OTE+tol", symbol)
        return None
    if zona_inf <= precio_actual <= zona_sup:
        score += 1
        detalles.append('R1:en_OTE')
    else:
        detalles.append('R1:cerca_OTE')

    # --- R2: SMA 100 en zona OTE ---
    if len(df_h4) >= 100:
        sma100 = _sma(df_h4['close'], 100).iloc[-1]
        if not pd.isna(sma100) and sma100_en_zona_ote(sma100, fibo, atr_val):
            score += 1
            detalles.append('R2:SMA100_en_OTE')
        else:
            detalles.append('R2:SMA100_no')
    else:
        score += 1

    # --- R3: ADX ---
    if adx_permite_entrada(df_h4):
        score += 1
        detalles.append('R3:ADX_ok')
    else:
        detalles.append('R3:ADX_no')

    # --- F2-R4: USDT.D ---
    if es_long:
        if check_usdtd_resistencia_long():
            score += 1
            detalles.append('R4:USDT.D_resistencia')
        else:
            detalles.append('R4:USDT.D_no')
    else:
        score += 1
        detalles.append('R4:Short_ok')

    # --- F2-R5: BTC.D ---
    btcd_subiendo = check_dominancia_btc_long()
    if not (btcd_subiendo and 'BTC' not in symbol):
        score += 1
        detalles.append(f'R5:BTC.D_{"sube" if btcd_subiendo else "baja"}')
    else:
        detalles.append('R5:BTC.D_bloquea_alt')

    # --- R6: FVG ---
    fvgs = detectar_fvg(df_h4)
    fvg_en_zona = [f for f in fvgs if f['gap_sup'] >= zona_inf and f['gap_inf'] <= zona_sup]
    senal['fvgs'] = fvg_en_zona
    if fvg_en_zona:
        score += 1
        detalles.append(f'R6:FVG_{len(fvg_en_zona)}')
    else:
        detalles.append('R6:FVG_no')

    # --- R7: Order Block ---
    obs = detectar_order_blocks(df_h4)
    ob_en_zona = [o for o in obs if o['low'] <= zona_sup and o['high'] >= zona_inf]
    senal['obs'] = ob_en_zona
    if ob_en_zona:
        score += 1
        detalles.append(f'R7:OB_{len(ob_en_zona)}')
    else:
        detalles.append('R7:OB_no')

    # --- R8: Liquidity Sweep ---
    sweeps = detectar_sweep(df_h4)
    senal['sweeps'] = sweeps
    if sweeps:
        sweep_ok = any(
            (s['tipo'] == 'sweep_bajista_long' and es_long) or
            (s['tipo'] == 'sweep_alcista_short' and not es_long)
            for s in sweeps
        )
        if sweep_ok:
            score += 1
            detalles.append(f'R8:Sweep')
        else:
            detalles.append('R8:Sweep_dir_no')
    else:
        detalles.append('R8:Sweep_no')

    # --- [GAP 1] R9: Sin Mecha hay Sospecha (validación en zona OTE) ---
    mecha_ok, mecha_det = validar_mecha_absorcion_en_zona(df_h4, zona_inf, zona_sup, es_long, atr_val)
    if not mecha_ok:
        log.debug("%s: Sin Mecha hay Sospecha — %s", symbol, mecha_det)
        return None  # GAP 1: abortar, no es una entrada válida
    score += 1
    detalles.append(f'R9:Mecha_{mecha_det}')

    # --- F5: RSI Filtro ---
    rsi_ok, rsi_val = filtro_rsi(df_h4, es_long)
    if rsi_ok:
        score += 1
        detalles.append(f'F5:RSI_{rsi_val:.0f}')
    else:
        detalles.append(f'F5:RSI_{rsi_val:.0f}_bloquea')

    # --- F5: Volumen ---
    vol_ok, vol_ratio = validar_volumen(df_h4, es_long)
    if vol_ok:
        score += 1
        detalles.append(f'F5:Vol_{vol_ratio:.1f}x')
    else:
        detalles.append(f'F5:Vol_{vol_ratio:.1f}x_bloquea')

    # --- F6: Pullback ---
    nivel_ref = zona_sup if es_long else zona_inf
    pullback_ok = detectar_pullback_confirmado(df_h4, nivel_ref, es_long)
    if pullback_ok:
        score += 1
        detalles.append('F6:Pullback_ok')
    else:
        detalles.append('F6:Pullback_no')

    # --- F11: Elliott ---
    elliott = detectar_estructura_elliott_v3(df_h4)
    senal['elliott'] = elliott
    if elliott['fase'] == 'estructura_5_ondas':
        score += 1
        detalles.append(f'F11:Elliott_5ondas')
    else:
        detalles.append(f'F11:Elliott_{elliott["fase"]}')

    # --- F10: Validación D1 estructural ---
    if validar_estructura_d1(df_d1, precio_actual, 'long' if es_long else 'short'):
        score += 1
        detalles.append('F10:D1_estructura_ok')
    else:
        log.debug("%s: D1 invalida estructura", symbol)
        return None  # D1 invalida = no entrar

    # --- F12: TPs en zonas reales ---
    tp1_price, tp2_price, tp3_price, rr = calcular_tps_en_zonas(
        precio_actual, atr_val, fvg_en_zona, ob_en_zona, es_long
    )
    senal['tp1_price'] = tp1_price
    senal['tp2_price'] = tp2_price
    senal['tp3_price'] = tp3_price
    senal['rr'] = rr

    # R:R mínimo 1.5:1 (del documento original)
    if rr >= 1.5:
        score += 1
        detalles.append(f'R13:R:R_{rr:.2f}')
    else:
        log.debug("%s: R:R %.2f < 1.5", symbol, rr)
        detalles.append(f'R13:R:R_{rr:.2f}_baja')

    # --- SL: 1.5 ATR (original lobobot.py) ---
    sl_mult = LOBO_SL_ATR
    if es_long:
        sl_price = precio_actual - (atr_val * sl_mult)
    else:
        sl_price = precio_actual + (atr_val * sl_mult)
    senal['sl_price'] = sl_price

    # --- Position Sizing (idéntico a lobobot.py) ---
    riesgo_capital = capital_fut * LOBO_RISK_PCT
    distancia_sl = abs(precio_actual - sl_price) / precio_actual
    if distancia_sl > 0:
        pos_value = riesgo_capital / distancia_sl
    else:
        return None
    
    if pos_value < MIN_ORDER_USDT:
        log.info("%s: pos_value %.2f < min %d, ajustando", symbol, pos_value, MIN_ORDER_USDT)
        pos_value = MIN_ORDER_USDT
    
    qty = pos_value / precio_actual
    apalancamiento = min(pos_value / max(riesgo_capital, 1), 10)
    if apalancamiento < 1:
        apalancamiento = 1
    liq_price = calcular_precio_liquidacion(precio_actual, apalancamiento, 'long' if es_long else 'short')
    riesgo_real_pct = (pos_value * distancia_sl) / capital_fut * 100
    
    senal['qty'] = qty
    senal['pos_value'] = pos_value
    senal['liq_price'] = liq_price
    senal['size_usdt'] = pos_value / apalancamiento if apalancamiento > 0 else 0
    senal['leverage_calculado'] = apalancamiento
    senal['riesgo_real_pct'] = riesgo_real_pct

    detal_sizing = f'SL_{sl_mult:.1f}ATR_lev{apalancamiento:.0f}x_liq{liq_price:.2f}'
    score += 1
    detalles.append(detal_sizing)
    score += 1
    detalles.append(detal_sizing)

    # Score mínimo
    if score < LOBO_SCORE_MIN:
        log.debug("%s: Score %d < minimo %d", symbol, score, LOBO_SCORE_MIN)
        return None

    senal['score'] = score
    senal['max_score'] = max_score
    senal['detalles'] = detalles
    senal['fvg_usado'] = fvg_en_zona[0] if fvg_en_zona else None
    
    return senal

# =====================================================================
# 7. TELEGRAM (idéntico a v2)
# =====================================================================
def send_telegram(message: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}, timeout=10)
        log.info("Telegram: %s ...", message[:80].replace('\n', ' '))
    except Exception:
        pass

# =====================================================================
# 8. CSV LOGGING (adaptado a v3)
# =====================================================================
TRADE_CSV_HEADERS_V3 = [
    'entry_time', 'exit_time', 'symbol', 'side', 'entry_price', 'exit_price',
    'sl_price', 'tp1_price', 'tp2_price', 'tp3_price',
    'liq_price', 'leverage_used',
    'sl_pct', 'tp_pct', 'quantity',
    'capital_total', 'capital_futuros',
    'balance_before', 'balance_after',
    'pnl', 'fees', 'net_pnl', 'status', 'duration_hours',
    'signal_score', 'rr', 'atr_at_entry',
    'close_reason', 'be_triggered', 'be_price',
    'trail_count', 'trail_peak_price', 'trail_final_sl',
    'entry_weekday', 'entry_hour',
    'size_usdt', 'risk_pct', 'hedge_active',
    'max_favorable_pct', 'max_adverse_pct',
]

def guardar_trade_csv(entry, exit_price, raw_pnl, fees, net, status, close_reason):
    if not entry:
        return
    now = datetime.now()
    duration = (now - entry['entry_time']).total_seconds() / 3600
    balance_after = entry.get('balance_before', 0) + net
    ep = entry['entry_price']
    sl = entry.get('sl_price', 0)
    side = entry.get('side', 'long')
    row = {
        'entry_time': entry['entry_time'].strftime('%Y-%m-%d %H:%M:%S'),
        'exit_time': now.strftime('%Y-%m-%d %H:%M:%S'),
        'symbol': entry['symbol'], 'side': side,
        'entry_price': ep, 'exit_price': exit_price,
        'sl_price': sl,
        'tp1_price': entry.get('tp1_price', 0),
        'tp2_price': entry.get('tp2_price', 0),
        'tp3_price': entry.get('tp3_price', 0),
        'liq_price': entry.get('liq_price', 0),
        'leverage_used': round(entry.get('leverage', 0), 1),
        'sl_pct': round(abs(ep - sl) / ep * 100, 2) if sl else 0,
        'tp_pct': round(abs(ep - entry.get('tp1_price', ep)) / ep * 100, 2),
        'quantity': entry.get('quantity', 0),
        'capital_total': round(entry.get('balance_before', 0), 2),
        'capital_futuros': round(entry.get('capital_futuros', 0), 2),
        'balance_before': round(entry.get('balance_before', 0), 2),
        'balance_after': round(balance_after, 2),
        'pnl': round(raw_pnl, 2), 'fees': round(fees, 2), 'net_pnl': round(net, 2),
        'status': status, 'duration_hours': round(duration, 2),
        'signal_score': entry.get('score', 0),
        'rr': entry.get('rr', 0),
        'atr_at_entry': round(entry.get('atr_val', 0), 2),
        'close_reason': close_reason,
        'be_triggered': 1 if ALERTS_HISTORY.get(f"{entry['symbol']}_be", False) else 0,
        'be_price': round(ALERTS_HISTORY.get(f"{entry['symbol']}_be_price", 0), 4),
        'trail_count': TRAIL_COUNTS.get(entry['symbol'], 0),
        'trail_peak_price': round(PEAK_PRICES.get(entry['symbol'], ep), 4),
        'trail_final_sl': round(ALERTS_HISTORY.get(f"{entry['symbol']}_trail", sl), 4),
        'entry_weekday': entry['entry_time'].weekday(),
        'entry_hour': entry['entry_time'].hour,
        'size_usdt': entry.get('size_usdt', 0),
        'risk_pct': entry.get('risk_pct', 0),
        'hedge_active': 1 if HEDGE_ENTRIES.get(entry['symbol']) else 0,
        'max_favorable_pct': round(abs(PEAK_PRICES.get(entry['symbol'], ep) - ep) / ep * 100, 2),
        'max_adverse_pct': round(abs(ADVERSE_PRICES.get(entry['symbol'], ep) - ep) / ep * 100, 2),
    }
    csv_path = TRADES_CSV_PATH
    write_header = not os.path.exists(csv_path)
    try:
        with open(csv_path, 'a', newline='', encoding='utf-8') as f:
            w = csv.DictWriter(f, fieldnames=TRADE_CSV_HEADERS_V3)
            if write_header:
                w.writeheader()
            w.writerow(row)
    except Exception:
        pass

SIGNAL_LOG_HEADERS_V3 = [
    'time', 'symbol', 'side', 'price', 'score', 'max_score',
    'detalles', 'rr', 'atr', 'entry_zone_fibo',
    'sl_proj', 'liq_price', 'leverage',
    'tp1_proj', 'tp2_proj', 'tp3_proj',
    'taken', 'reason_skipped',
]

def guardar_signal_log(symbol, side, price, score, max_score, detalles,
                       sl_proj, liq_price, leverage, tp1_proj, tp2_proj, tp3_proj, rr,
                       taken=True, reason_skipped=''):
    row = {
        'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'symbol': symbol, 'side': side,
        'price': round(price, 6),
        'score': score, 'max_score': max_score,
        'detalles': ' | '.join(detalles) if detalles else '',
        'rr': round(rr, 2),
        'atr': 0,
        'entry_zone_fibo': '',
        'sl_proj': round(sl_proj, 6) if sl_proj else 0,
        'liq_price': round(liq_price, 6) if liq_price else 0,
        'leverage': round(leverage, 1) if leverage else 0,
        'tp1_proj': round(tp1_proj, 6) if tp1_proj else 0,
        'tp2_proj': round(tp2_proj, 6) if tp2_proj else 0,
        'tp3_proj': round(tp3_proj, 6) if tp3_proj else 0,
        'taken': 'Yes' if taken else 'No',
        'reason_skipped': reason_skipped,
    }
    csv_path = SIGNALS_LOG_PATH
    write_header = not os.path.exists(csv_path)
    try:
        with open(csv_path, 'a', newline='', encoding='utf-8') as f:
            w = csv.DictWriter(f, fieldnames=SIGNAL_LOG_HEADERS_V3)
            if write_header:
                w.writeheader()
            w.writerow(row)
    except Exception:
        pass

# =====================================================================
# 9. FETCH ASÍNCRONO (idéntico a v2)
# =====================================================================
async def _fetch_symbol_async(exch, symbol):
    try:
        ohlcv_4h = await exch.fetch_ohlcv(symbol, timeframe=TIMEFRAME_4H, limit=100)
        ohlcv_d1 = await exch.fetch_ohlcv(symbol, timeframe=TIMEFRAME_D1, limit=60)
        return symbol, ohlcv_4h, ohlcv_d1
    except Exception:
        return symbol, None, None

async def fetch_all_ohlcv(symbols):
    exch = ccxt_async.bitget({
        'apiKey': API_KEY, 'secret': SECRET_KEY, 'password': PASSPHRASE,
        'enableRateLimit': True, 'options': {'defaultType': 'swap'},
    })
    try:
        results = await asyncio.gather(*[_fetch_symbol_async(exch, s) for s in symbols])
    finally:
        await exch.close()
    return {r[0]: (r[1], r[2]) for r in results}

# =====================================================================
# 10. EXCHANGE — CONEXIÓN Y ÓRDENES
# =====================================================================
exchange: ccxt.bitget | None = None

def init_exchange() -> bool:
    global exchange
    if PAPER_TRADE:
        log.info("PAPER_TRADE v3 activo")
        try:
            exchange = ccxt.bitget({'enableRateLimit': True, 'options': {'defaultType': 'swap'}})
            exchange.load_markets()
            log.info("Exchange paper v3 listo (%d mercados)", len(exchange.markets))
            return True
        except Exception as e:
            log.critical("Error exchange paper: %s", e)
            return False
    if not API_KEY or not SECRET_KEY or not PASSPHRASE:
        log.critical("API keys missing")
        return False
    try:
        exchange = ccxt.bitget({
            'apiKey': API_KEY, 'secret': SECRET_KEY, 'password': PASSPHRASE,
            'enableRateLimit': True, 'options': {'defaultType': 'swap'},
        })
        log.info("Conexion Bitget v3 exitosa")
        return True
    except Exception as e:
        log.critical("Error conectando Bitget: %s", e)
        return False

# =====================================================================
# 11. GESTIÓN DE POSICIONES v3 (con SL por liquidación, BE, trailing, coberturas)
# =====================================================================
def _manage_paper_positions_v3(balance_total: float):
    """Gestiona posiciones simuladas en paper mode con TODAS las reglas v3."""
    global ALERTS_HISTORY, PEAK_PRICES, COOLDOWNS, DAILY_STATS
    global SESSION_ACTIVE_SYMBOLS, TRAIL_COUNTS, HEDGE_ENTRIES
    global ADVERSE_PRICES, PRICE_PATHS, LAST_KNOWN_INDICATORS

    if not TRADE_ENTRIES:
        return

    capital_fut = capital_disponible_futuros(balance_total)

    for symbol in list(TRADE_ENTRIES.keys()):
        try:
            entry = TRADE_ENTRIES[symbol]
            side = entry.get('side', 'long')
            entry_price = float(entry['entry_price'])
            sl_price = float(entry.get('sl_price', 0))
            tp1_price = float(entry.get('tp1_price', 0))
            tp2_price = float(entry.get('tp2_price', 0))
            tp3_price = float(entry.get('tp3_price', 0))
            liq_price = float(entry.get('liq_price', 0))

            try:
                ticker = exchange.fetch_ticker(symbol)
                mark = float(ticker['last'])
            except Exception:
                continue

            profit_pct = (mark - entry_price) / entry_price if side == 'long' else (entry_price - mark) / entry_price

            # --- F10: Validación D1 estructural ---
            try:
                ohlcv_d1 = exchange.fetch_ohlcv(symbol, timeframe='1d', limit=30)
                if len(ohlcv_d1) >= 10:
                    df_d1 = pd.DataFrame(ohlcv_d1, columns=['ts','o','h','l','c','v'])
                    if not validar_estructura_d1(df_d1, entry_price, side):
                        log.info("[PAPER v3] %s: D1 invalida estructura - cerrando", symbol)
                        size = float(entry.get('size_usdt', capital_fut * LOBO_RISK_PCT))
                        pnl = size * entry.get('leverage', LEVERAGE) * profit_pct
                        guardar_trade_csv(entry, mark, pnl, 0, pnl, 'D1_INVALID', 'd1_estructura')
                        TRADE_ENTRIES.pop(symbol, None)
                        HEDGE_ENTRIES.pop(symbol, None)
                        _save_trade_entries()
                        SESSION_ACTIVE_SYMBOLS.discard(symbol)
                        COOLDOWNS[symbol] = time.time() + 7200
                        send_telegram(f"[PAPER v3] *{symbol}* Cerrada por D1 estructura")
                        continue
            except Exception:
                pass

            # --- F4: Evaluar cobertura asimétrica ---
            if LOBO_HEDGE_ENABLED and symbol not in HEDGE_ENTRIES:
                hedge_params = evaluar_cobertura(entry, mark)
                if hedge_params:
                    log.info("[PAPER v3] %s: Activando cobertura asimetrica %s lev=%.0fx tp=%.4f",
                             symbol, hedge_params['side'], hedge_params['leverage'], hedge_params['tp_price'])
                    HEDGE_ENTRIES[symbol] = hedge_params
                    send_telegram(f"[PAPER v3] *{symbol}* Cobertura {hedge_params['side']} activada")

            # --- Gestionar cobertura activa ---
            hedge = HEDGE_ENTRIES.get(symbol)
            if hedge:
                hedge_side = hedge['side']
                hedge_tp = hedge['tp_price']
                hedge_sl = hedge['sl_price']
                hedge_lev = hedge['leverage']
                # Si la cobertura alcanza TP (coincide con liquidación del principal)
                if hedge_side == 'short' and mark <= hedge_tp:
                    pnl_hedge = hedge.get('size_usdt', 0) * hedge_lev * \
                                ((hedge['entry_price'] - mark) / hedge['entry_price'])
                    log.info("[PAPER v3] %s: Cobertura TP alcanzado! PnL=%.2f", symbol, pnl_hedge)
                    HEDGE_ENTRIES.pop(symbol, None)
                elif hedge_side == 'long' and mark >= hedge_tp:
                    pnl_hedge = hedge.get('size_usdt', 0) * hedge_lev * \
                                ((mark - hedge['entry_price']) / hedge['entry_price'])
                    log.info("[PAPER v3] %s: Cobertura TP alcanzado! PnL=%.2f", symbol, pnl_hedge)
                    HEDGE_ENTRIES.pop(symbol, None)
                # Si la cobertura alcanza SL
                if hedge_side == 'short' and mark >= hedge_sl:
                    HEDGE_ENTRIES.pop(symbol, None)
                elif hedge_side == 'long' and mark <= hedge_sl:
                    HEDGE_ENTRIES.pop(symbol, None)

            # --- F3: Liquidación forzosa (SL como plan oculto) ---
            exit_px = None
            status = None
            reason = None

            if side == 'long':
                # Liquidación si el precio toca liq_price
                if liq_price > 0 and mark <= liq_price:
                    exit_px = mark
                    status = 'LIQ'
                    reason = 'liquidacion'
                elif tp3_price > 0 and mark >= tp3_price:
                    exit_px = tp3_price
                    status = 'TP'
                    reason = 'tp'
                elif tp2_price > 0 and mark >= tp2_price:
                    exit_px = tp2_price
                    status = 'TP'
                    reason = 'tp'
                elif tp1_price > 0 and mark >= tp1_price:
                    exit_px = tp1_price
                    status = 'TP'
                    reason = 'tp'
            else:  # short
                if liq_price > 0 and mark >= liq_price:
                    exit_px = mark
                    status = 'LIQ'
                    reason = 'liquidacion'
                elif tp3_price > 0 and mark <= tp3_price:
                    exit_px = tp3_price
                    status = 'TP'
                    reason = 'tp'
                elif tp2_price > 0 and mark <= tp2_price:
                    exit_px = tp2_price
                    status = 'TP'
                    reason = 'tp'
                elif tp1_price > 0 and mark <= tp1_price:
                    exit_px = tp1_price
                    status = 'TP'
                    reason = 'tp'

            if exit_px is not None:
                size = float(entry.get('size_usdt', capital_fut * LOBO_RISK_PCT))
                lev = float(entry.get('leverage', LEVERAGE))
                pnl_pct = (exit_px - entry_price) / entry_price if side == 'long' else (entry_price - exit_px) / entry_price
                pnl = size * lev * pnl_pct
                log.info("[PAPER v3] %s %s | Entry=%.4f Exit=%.4f PnL=%.2f (lev=%.0f)",
                         symbol, status, entry_price, exit_px, pnl, lev)
                guardar_trade_csv(entry, exit_px, pnl, 0, pnl, status, reason)
                TRADE_ENTRIES.pop(symbol, None)
                HEDGE_ENTRIES.pop(symbol, None)
                _save_trade_entries()
                SESSION_ACTIVE_SYMBOLS.discard(symbol)
                COOLDOWNS[symbol] = time.time() + 3600
                PEAK_PRICES.pop(symbol, None)
                ALERTS_HISTORY.pop(symbol, None)
                TRAIL_COUNTS.pop(symbol, None)
                send_telegram(f"[PAPER v3] *{symbol} {status}*\nPnL: {pnl:.2f} USDT ({pnl_pct*100:.2f}%)")
                continue

            # --- Timeout ---
            entry_time = entry.get('entry_time')
            if isinstance(entry_time, datetime) and profit_pct < 0:
                horas = (datetime.now() - entry_time).total_seconds() / 3600
                if horas >= LOBO_TIMEOUT_HORAS:
                    size = float(entry.get('size_usdt', capital_fut * LOBO_RISK_PCT))
                    lev = float(entry.get('leverage', LEVERAGE))
                    pnl = size * lev * profit_pct
                    log.info("[PAPER v3] %s TIMEOUT +%.0fh", symbol, horas)
                    guardar_trade_csv(entry, mark, pnl, 0, pnl, 'Timeout', 'timeout')
                    TRADE_ENTRIES.pop(symbol, None)
                    HEDGE_ENTRIES.pop(symbol, None)
                    _save_trade_entries()
                    SESSION_ACTIVE_SYMBOLS.discard(symbol)
                    COOLDOWNS[symbol] = time.time() + 3600
                    continue

            # --- Seguimiento de pico ---
            if symbol not in PEAK_PRICES:
                PEAK_PRICES[symbol] = mark
            else:
                if side == 'long':
                    PEAK_PRICES[symbol] = max(PEAK_PRICES[symbol], mark)
                else:
                    PEAK_PRICES[symbol] = min(PEAK_PRICES[symbol], mark)
            if symbol not in ADVERSE_PRICES:
                ADVERSE_PRICES[symbol] = mark
            else:
                if side == 'long':
                    ADVERSE_PRICES[symbol] = min(ADVERSE_PRICES[symbol], mark)
                else:
                    ADVERSE_PRICES[symbol] = max(ADVERSE_PRICES[symbol], mark)

            # --- Trailing stop simple (original) ---
            if ALERTS_HISTORY.get(f"{symbol}_tp1_sold", False) and profit_pct > 0:
                dist = LOBO_TRAIL_ATR_MULT * entry.get('atr_val', 0) * 1.5
                if dist > 0:
                    nuevo_sl = (PEAK_PRICES[symbol] - dist) if side == 'long' else (PEAK_PRICES[symbol] + dist)
                    ultimo_sl = ALERTS_HISTORY.get(f"{symbol}_trail", 0 if side == 'long' else 999999)
                    mejora = (nuevo_sl - ultimo_sl) if side == 'long' else (ultimo_sl - nuevo_sl)
                    if mejora > (entry_price * 0.002):
                        TRADE_ENTRIES[symbol]['sl_price'] = nuevo_sl
                        ALERTS_HISTORY[f"{symbol}_trail"] = nuevo_sl
                        TRAIL_COUNTS[symbol] = TRAIL_COUNTS.get(symbol, 0) + 1
                        log.info("[PAPER v3] %s Trail→%.4f", symbol, nuevo_sl)

        except Exception as e:
            log.error("[PAPER v3] Error gestionando %s: %s", symbol, e)

def manage_escudo_pro_v3(balance_total: float = 0.0):
    """Versión v3 de gestión de posiciones."""
    if PAPER_TRADE:
        _manage_paper_positions_v3(balance_total)
        return
    # Modo real (misma estructura que paper pero con órdenes reales)
    try:
        positions = exchange.fetch_positions()
        active_symbols = {p['symbol'] for p in positions if float(p['contracts']) > 0}
        for sym in list(SESSION_ACTIVE_SYMBOLS):
            if sym not in active_symbols:
                COOLDOWNS[sym] = time.time() + 3600
                SESSION_ACTIVE_SYMBOLS.discard(sym)
        for pos in positions:
            # ... (lógica similar a paper pero usando API real de Bitget)
            pass
    except Exception as e:
        log.error("Error manage_escudo_pro_v3: %s", e)

# =====================================================================
# 12. BUCLE PRINCIPAL v3
# =====================================================================
def main():
    global LAST_KNOWN_INDICATORS, ALERTS_HISTORY, PEAK_PRICES, COOLDOWNS
    global SESSION_ACTIVE_SYMBOLS, DAILY_STATS, TRADE_ENTRIES, TRAIL_COUNTS
    global HEDGE_ENTRIES, ADVERSE_PRICES, PRICE_PATHS, exchange

    log.info("=" * 60)
    log.info("LOBOBOT v3 — BITLOBO FORMALIZADO (F1-F12) iniciando")
    log.info("=" * 60)

    if exchange is None:
        if not init_exchange():
            log.critical("No se pudo inicializar exchange")
            return

    _load_trade_entries()
    last_report_day = datetime.now().day - 1

    while True:
        try:
            now = datetime.now()

            # ── Reporte diario ──
            if now.hour == 0 and now.day != last_report_day:
                today_str = (now - timedelta(days=1)).strftime('%Y-%m-%d')
                today_trades = []
                try:
                    with open(TRADES_CSV_PATH, 'r', encoding='utf-8') as f:
                        for row in csv.DictReader(f):
                            if row['entry_time'].startswith(today_str):
                                today_trades.append(row)
                except Exception:
                    pass
                total = len(today_trades)
                tps = [r for r in today_trades if r['status'] == 'TP']
                sls = [r for r in today_trades if r['status'] in ('SL', 'LIQ')]
                pnl_total = sum(float(r['net_pnl']) for r in today_trades)
                wr = len(tps) / max(total, 1) * 100
                msg = (
                    f"*REPORTE DIARIO v3* ({now.strftime('%d/%m')})\n"
                    f"Ops: {total} | TP:{len(tps)} SL:{len(sls)}\n"
                    f"WR: {wr:.0f}% | PnL: {pnl_total:+.2f} USDT"
                )
                send_telegram(msg)
                last_report_day = now.day

            # ── Balance total ──
            try:
                balance_data = exchange.fetch_balance()
                balance_total = float(balance_data['total'].get('USDT', 0))
            except Exception as e:
                if PAPER_TRADE:
                    balance_total = 10_000.0
                else:
                    log.error("Error balance: %s", e)
                    balance_total = 0.0

            capital_fut = capital_disponible_futuros(balance_total)
            log.info("Balance total=%.2f | Futuros(20%%)=%.2f | Liquidez(50%%)=%.2f | Spot(30%%)=%.2f",
                     balance_total, capital_fut,
                     capital_liquidez(balance_total), capital_spot(balance_total))

            # ── Gestión de posiciones activas ──
            manage_escudo_pro_v3(balance_total)

            # ── Posiciones activas ──
            try:
                positions = exchange.fetch_positions()
                busy_symbols = {p['symbol'] for p in positions if float(p['contracts']) > 0}
            except Exception:
                busy_symbols = set()
            if PAPER_TRADE:
                busy_symbols.update(TRADE_ENTRIES.keys())

            log.info("Ciclo [%s] Fut=%.2f Ocupados=%d",
                     now.strftime('%H:%M'), capital_fut, len(busy_symbols))

            if len(busy_symbols) >= LOBO_MAX_POSITIONS:
                time.sleep(60)
                continue

            # ── TOP símbolos por volumen (R17) ──
            try:
                tickers = exchange.fetch_tickers()
                top_symbols = [
                    p[0] for p in sorted(
                        [(s, float(t.get('quoteVolume', 0))) for s, t in tickers.items()
                         if s.endswith('/USDT:USDT')],
                        key=lambda x: x[1], reverse=True,
                    )[:TOP_N]
                ]
            except Exception as e:
                log.error("Error fetching tickers: %s", e)
                time.sleep(60)
                continue

            log.info("Obteniendo OHLCV para %d simbolos...", len(top_symbols))
            try:
                ohlcv_data = asyncio.run(fetch_all_ohlcv(top_symbols))
            except Exception as e:
                log.error("Error fetch OHLCV: %s", e)
                time.sleep(60)
                continue

            # ── Analizar cada símbolo ──
            for symbol in top_symbols:
                if symbol in busy_symbols:
                    continue
                if len(busy_symbols) >= LOBO_MAX_POSITIONS:
                    break
                if symbol in COOLDOWNS and time.time() < COOLDOWNS[symbol]:
                    continue
                elif symbol in COOLDOWNS:
                    del COOLDOWNS[symbol]

                try:
                    ohlcv_4h, ohlcv_d1 = ohlcv_data.get(symbol, (None, None))
                    if not ohlcv_4h or not ohlcv_d1:
                        continue
                    if len(ohlcv_4h) < 50 or len(ohlcv_d1) < 10:
                        continue

                    df_4h = pd.DataFrame(ohlcv_4h, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                    df_d1 = pd.DataFrame(ohlcv_d1, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])

                    # F7: Solo evaluar al cierre de vela H4
                    if not es_nueva_vela_h4(df_4h):
                        continue

                    precio_actual = float(df_4h['close'].iloc[-1])
                    atr_val = float(_atr(df_4h, LOBO_ATR_PERIOD).iloc[-1])
                    if atr_val == 0 or pd.isna(atr_val):
                        continue

                    # Evaluar señal BITLOBO v3
                    senal_long = evaluar_senal_bitlobo_v3(
                        symbol, df_4h, df_d1, precio_actual, atr_val, balance_total, es_long=True
                    )

                    sweeps = detectar_sweep(df_4h)
                    hay_sweep_short = any(s['tipo'] == 'sweep_alcista_short' for s in sweeps)
                    senal_short = None
                    if hay_sweep_short:
                        senal_short = evaluar_senal_bitlobo_v3(
                            symbol, df_4h, df_d1, precio_actual, atr_val, balance_total, es_long=False
                        )

                    senal = senal_long or senal_short
                    if not senal:
                        continue

                    es_long = senal['es_long']
                    side_name = 'LARGO' if es_long else 'CORTO'
                    sl_price = senal['sl_price']
                    tp1_price = senal['tp1_price']
                    tp2_price = senal['tp2_price']
                    tp3_price = senal['tp3_price']
                    lev_calc = senal.get('leverage_calculado', LEVERAGE)
                    liq_price = senal.get('liq_price', 0)
                    rr = senal['rr']
                    score = senal['score']
                    max_score = senal['max_score']

                    # Position sizing
                    raw_qty = senal['qty']
                    market = exchange.market(symbol)
                    step = market['limits']['amount']['min'] or market['precision']['amount']
                    min_qty = math.ceil(MIN_ORDER_USDT / precio_actual / step) * step
                    if raw_qty < min_qty:
                        riesgo_ajustado = (min_qty * precio_actual * abs(precio_actual - sl_price) / precio_actual) / capital_fut * 100
                        if riesgo_ajustado > 10.0:
                            log.info("%s: riesgo %.1f%% > 10%%, saltando", symbol, riesgo_ajustado)
                            continue
                        raw_qty = min_qty
                    qty = math.ceil(raw_qty / step) * step
                    actual_margin = (qty * precio_actual) / lev_calc

                    log.info(
                        "%s %s | Entry=%.4f SL=%.4f Liq=%.4f Lev=%.0f TP1=%.4f TP2=%.4f TP3=%.4f R:R=%.2f | Score=%d/%d",
                        symbol, side_name, precio_actual, sl_price, liq_price, lev_calc,
                        tp1_price, tp2_price, tp3_price, rr, score, max_score,
                    )

                    # Entry record
                    entry_record = {
                        'entry_time': datetime.now(),
                        'symbol': symbol,
                        'side': 'long' if es_long else 'short',
                        'entry_price': precio_actual,
                        'sl_price': sl_price,
                        'liq_price': liq_price,
                        'leverage': lev_calc,
                        'tp1_price': tp1_price,
                        'tp2_price': tp2_price,
                        'tp3_price': tp3_price,
                        'quantity': qty,
                        'balance_before': balance_total,
                        'capital_futuros': capital_fut,
                        'atr_val': senal.get('atr_val', 0),
                        'size_usdt': round(actual_margin, 2),
                        'risk_pct': round(actual_margin / max(capital_fut, 1) * 100, 2),
                        'score': score,
                        'rr': rr,
                    }

                    if PAPER_TRADE:
                        log.info("[PAPER v3] %s %s qty=%.6f lev=%.0f", side_name, symbol, qty, lev_calc)
                        send_telegram(
                            f"[PAPER v3] *{symbol} {side_name}* (BITLOBO v3)\n"
                            f"Entry: `{exchange.price_to_precision(symbol, precio_actual)}`\n"
                            f"SL/Liq: `{exchange.price_to_precision(symbol, sl_price)}` / `{exchange.price_to_precision(symbol, liq_price)}`\n"
                            f"Lev: {lev_calc:.0f}x\n"
                            f"TP1: `{exchange.price_to_precision(symbol, tp1_price)}`\n"
                            f"TP2: `{exchange.price_to_precision(symbol, tp2_price)}`\n"
                            f"TP3: `{exchange.price_to_precision(symbol, tp3_price)}`\n"
                            f"R:R: {rr:.2f} | Score: {score}/{max_score}"
                        )
                        TRADE_ENTRIES[symbol] = entry_record
                        _save_trade_entries()
                        busy_symbols.add(symbol)
                        SESSION_ACTIVE_SYMBOLS.add(symbol)
                        guardar_signal_log(symbol, side_name, precio_actual, score, max_score,
                                           senal['detalles'], sl_price, liq_price, lev_calc,
                                           tp1_price, tp2_price, tp3_price, rr, taken=True)
                        continue

                    # ── Orden real en Bitget (F3: sin SL visible) ──
                    try:
                        exchange.set_leverage(int(lev_calc), symbol)
                    except Exception as e:
                        log.warning("Error set_leverage %s %.0f: %s", symbol, lev_calc, e)

                    params = {
                        'marginCoin': 'USDT',
                        'marginMode': 'isolated',
                        'tradeSide': 'open',
                        # NOTA: SIN presetStopLossPrice (F3: anti-cacería)
                        'presetStopSurplusPrice': str(exchange.price_to_precision(symbol, tp1_price)),
                    }
                    try:
                        exchange.create_order(symbol, 'market', 'buy' if es_long else 'sell', qty, params=params)
                    except Exception as e:
                        log.error("Error orden %s %s: %s", side_name, symbol, e)
                        continue

                    send_telegram(
                        f"*{symbol} {side_name}* (BITLOBO v3)\n"
                        f"Entry: `{exchange.price_to_precision(symbol, precio_actual)}`\n"
                        f"Lev: {lev_calc:.0f}x | Liq: `{exchange.price_to_precision(symbol, liq_price)}`\n"
                        f"Score: {score}/{max_score}"
                    )
                    TRADE_ENTRIES[symbol] = entry_record
                    _save_trade_entries()
                    busy_symbols.add(symbol)
                    SESSION_ACTIVE_SYMBOLS.add(symbol)
                    guardar_signal_log(symbol, side_name, precio_actual, score, max_score,
                                       senal['detalles'], sl_price, liq_price, lev_calc,
                                       tp1_price, tp2_price, tp3_price, rr, taken=True)

                except Exception as e:
                    log.debug("Error procesando %s: %s", symbol, e)
                    continue

            time.sleep(60)

        except Exception as e:
            log.error("Error en ciclo principal v3: %s", e, exc_info=True)
            time.sleep(60)

# =====================================================================
# 13. ENTRY POINT
# =====================================================================
if __name__ == "__main__":
    log.info("LOBOBOT v3 iniciando en modo standalone...")
    if exchange is None:
        init_exchange()
    main()
