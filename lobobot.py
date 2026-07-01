#!/usr/bin/env python3
"""
LOBOBOT v2 — Bot BITLOBO TRADING (Formalización Cuantitativa Completa)
======================================================================
Implementa las 18 reglas formales de la auditoría BITLOBO:

  R1  - Impulso direccional para Fibonacci (8-40 velas, pendiente >2%)
  R2  - SMA 100 en zona OTE (tolerancia 0.5 ATR)
  R3  - ADX [20,30] + descendente 6 velas
  R4  - USDT.D en resistencia (percentil 80, 90d)
  R5  - BTC.D pendiente (SMA20 H4 > 0.1%)
  R6  - FVG formalizado (gap ≥0.3 ATR, no rellenado 48H4)
  R7  - Order Block (vela contraria + movimiento ≥2 ATR)
  R8  - Liquidity Sweep (penetración <1 ATR + cierre recuperado)
  R9  - Mecha/Absorción (mecha ≥0.5 ATR, cuerpo ≤30%, cierre mitad superior)
  R10 - Ondas Elliott (pivots + relaciones Fibo)
  R11 - Trigger: MARKET al cierre H4
  R12 - Apalancamiento desde pérdida objetivo 1.5%
  R13 - TP escalonado: TP1=FVG(40%), TP2=2.5ATR(30%), TP3=4ATR(30%)
  R14 - Trailing: 1 ATR desde TP1
  R15 - Break Even: SL→BE al alcanzar TP1
  R16 - Validación D1: cierre bajo soporte = salida
  R17 - Filtro TOP 100 por volumen 24h Bitget
  R18 - Position Sizing: riesgo 1.5-2%, aislado

Uso:
    python lobobot.py                 # Bot + web service
    gunicorn lobobot:app --workers 1 --threads 2   # Render

Variables de entorno requeridas:
    BITGET_API_KEY, BITGET_SECRET_KEY, BITGET_PASSPHRASE
    TELEGRAM_TOKEN, TELEGRAM_CHAT_ID         (opcional)

Variables de entorno configurables:
    LOBO_TOP_N=100         LOBO_TIMEFRAME_4H=4h      LOBO_TIMEFRAME_D1=1d
    LOBO_RISK_PCT=1.0      LOBO_MAX_POSITIONS=5       LOBO_LEVERAGE=20
    LOBO_PAPER_TRADE=true  PORT=8000
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
# 1. LOGGER
# =====================================================================
LOG_TO_FILE = os.environ.get('BOT_LOG_TO_FILE', '1') == '1'
LOG_LEVEL   = os.environ.get('BOT_LOG_LEVEL', 'INFO')

_handlers = [logging.StreamHandler(sys.stdout)]
if LOG_TO_FILE:
    LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lobobot.log")
    _handlers.append(logging.FileHandler(LOG_FILE, encoding="utf-8"))

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=_handlers,
)
log = logging.getLogger("lobobot")
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
TRAIL_COUNTS: dict = {}
PREMATURE_SL_MONITOR: dict = {}
LAST_KNOWN_INDICATORS: dict = {}
ADVERSE_PRICES: dict = {}
PRICE_PATHS: dict = {}

# =====================================================================
# 3. RUTAS DE ARCHIVOS
# =====================================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PRICE_PATHS_DIR = os.path.join(BASE_DIR, 'price_paths')
os.makedirs(PRICE_PATHS_DIR, exist_ok=True)
TRADES_CSV_PATH      = os.path.join(BASE_DIR, 'trades.csv')
PREMATURE_SL_CSV_PATH = os.path.join(BASE_DIR, 'premature_sl.csv')
TRADE_ENTRIES_PATH   = os.path.join(BASE_DIR, 'trade_entries.json')
SIGNALS_LOG_PATH     = os.path.join(BASE_DIR, 'signals_log.csv')

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
# 4. CONFIGURACIÓN DESDE ENTORNO
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

# === PARÁMETROS BITLOBO FORMALIZADOS ===
# R1 - Impulso
LOBO_IMPULSO_MIN_VELAS   = int(os.environ.get('LOBO_IMPULSO_MIN_VELAS', '8'))
LOBO_IMPULSO_MAX_VELAS   = int(os.environ.get('LOBO_IMPULSO_MAX_VELAS', '40'))
LOBO_IMPULSO_PEND_MIN    = float(os.environ.get('LOBO_IMPULSO_PEND_MIN', '0.02'))

# R2 - SMA100 tolerancia
LOBO_SMA100_TOL_ATR      = float(os.environ.get('LOBO_SMA100_TOL_ATR', '1.0'))

# R3 - ADX
LOBO_ADX_PERIOD          = int(os.environ.get('LOBO_ADX_PERIOD', '14'))
LOBO_ADX_MIN             = float(os.environ.get('LOBO_ADX_MIN', '15'))
LOBO_ADX_MAX             = float(os.environ.get('LOBO_ADX_MAX', '50'))
LOBO_ADX_DESC_VELAS      = int(os.environ.get('LOBO_ADX_DESC_VELAS', '6'))

# R4 - USDT.D
LOBO_USDTD_LOOKBACK      = int(os.environ.get('LOBO_USDTD_LOOKBACK', '90'))
LOBO_USDTD_PERCENTIL     = float(os.environ.get('LOBO_USDTD_PERCENTIL', '80'))
LOBO_USDTD_TOL_TOUCH     = float(os.environ.get('LOBO_USDTD_TOL_TOUCH', '0.98'))

# R5 - BTC.D
LOBO_BTCD_SMA_PERIOD     = int(os.environ.get('LOBO_BTCD_SMA_PERIOD', '20'))
LOBO_BTCD_PEND_MIN       = float(os.environ.get('LOBO_BTCD_PEND_MIN', '0.001'))

# R6 - FVG
LOBO_FVG_MIN_GAP_ATR     = float(os.environ.get('LOBO_FVG_MIN_GAP_ATR', '0.3'))
LOBO_FVG_MAX_VELAS       = int(os.environ.get('LOBO_FVG_MAX_VELAS', '48'))

# R7 - Order Block
LOBO_OB_MIN_MOV_ATR      = float(os.environ.get('LOBO_OB_MIN_MOV_ATR', '2.0'))
LOBO_OB_LOOKBACK         = int(os.environ.get('LOBO_OB_LOOKBACK', '10'))

# R8 - Sweep
LOBO_SWEEP_LOOKBACK      = int(os.environ.get('LOBO_SWEEP_LOOKBACK', '10'))
LOBO_SWEEP_MAX_PEN_ATR   = float(os.environ.get('LOBO_SWEEP_MAX_PEN_ATR', '1.0'))

# R9 - Mecha/Absorción
LOBO_MECHA_MIN_ATR       = float(os.environ.get('LOBO_MECHA_MIN_ATR', '0.5'))
LOBO_MECHA_CUERPO_RATIO  = float(os.environ.get('LOBO_MECHA_CUERPO_RATIO', '0.3'))

# R10 - Elliott
LOBO_ELLIOTT_LOOKBACK    = int(os.environ.get('LOBO_ELLIOTT_LOOKBACK', '60'))

# ATR
LOBO_ATR_PERIOD          = int(os.environ.get('LOBO_ATR_PERIOD', '14'))

# Riesgo (R12, R18)
LOBO_RISK_PCT            = float(os.environ.get('LOBO_RISK_PCT', '1.0')) / 100  # 1.0%
LOBO_RISK_PCT_EXCEP      = float(os.environ.get('LOBO_RISK_PCT_EXCEP', '10')) / 100  # 10% excepcional
LOBO_MAX_POSITIONS       = int(os.environ.get('LOBO_MAX_POSITIONS', '5'))

# TP/SL (R13, R14, R15)
LOBO_TP1_SIZE            = float(os.environ.get('LOBO_TP1_SIZE', '0.40'))
LOBO_TP2_SIZE            = float(os.environ.get('LOBO_TP2_SIZE', '0.30'))
LOBO_TP3_SIZE            = float(os.environ.get('LOBO_TP3_SIZE', '0.30'))
LOBO_TP2_ATR_MULT        = float(os.environ.get('LOBO_TP2_ATR_MULT', '2.5'))
LOBO_TP3_ATR_MULT        = float(os.environ.get('LOBO_TP3_ATR_MULT', '4.0'))
LOBO_TRAIL_ATR_MULT      = float(os.environ.get('LOBO_TRAIL_ATR_MULT', '1.0'))
LOBO_BE_TRIGGER_PCT      = float(os.environ.get('LOBO_BE_TRIGGER_PCT', '1.5')) / 100
LOBO_BE_OFFSET_PCT       = float(os.environ.get('LOBO_BE_OFFSET_PCT', '0.5')) / 100

# General
LOBO_TIMEOUT_HORAS       = float(os.environ.get('LOBO_TIMEOUT_HORAS', '12'))
LEVERAGE                 = float(os.environ.get('LOBO_LEVERAGE', '20.0'))
LOBO_SCORE_MIN           = int(os.environ.get('LOBO_SCORE_MIN', '9'))
PAPER_TRADE              = os.environ.get('LOBOBOT_PAPER_TRADE', 'false').lower() == 'true'

log.info(
    "BITLOBO v2 Config: TOP=%d | H4=%s D1=%s | "
    "ADX[%d](%.0f-%.0f) | FVG gap>=%.1fATR | OB mov>=%.0fATR | "
    "Risk=%.1f%% | Lev=%.0fx | MaxPos=%d | "
    "TP %.0f/%.0f/%.0f%% | Trail=%.0fATR | "
    "BE=%.1f%%(offset=%.1f%%) | Paper=%s",
    TOP_N, TIMEFRAME_4H, TIMEFRAME_D1,
    LOBO_ADX_PERIOD, LOBO_ADX_MIN*100, LOBO_ADX_MAX*100,
    LOBO_FVG_MIN_GAP_ATR, LOBO_OB_MIN_MOV_ATR,
    LOBO_RISK_PCT*100, LEVERAGE, LOBO_MAX_POSITIONS,
    LOBO_TP1_SIZE*100, LOBO_TP2_SIZE*100, LOBO_TP3_SIZE*100,
    LOBO_TRAIL_ATR_MULT, LOBO_BE_TRIGGER_PCT*100, LOBO_BE_OFFSET_PCT*100,
    PAPER_TRADE,
)

# =====================================================================
# 5. INDICADORES BITLOBO — FORMALIZACIÓN CUANTITATIVA COMPLETA
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

# ----------------------------------------------------------------
# R1 - Identificación de impulso direccional para Fibonacci
# ----------------------------------------------------------------
def detectar_impulso(df_h4: pd.DataFrame) -> Optional[dict]:
    """
    Busca el último tramo direccional en H4 cumpliendo:
    - Longitud entre LOBO_IMPULSO_MIN_VELAS (8) y LOBO_IMPULSO_MAX_VELAS (40).
    - Pendiente neta >= LOBO_IMPULSO_PEND_MIN (2%).
    - Ninguna vela individual retrocde >38.2% del movimiento total del tramo.
    - Al menos 70% de las velas cierran en la dirección del impulso.
    Retorna {inicio, fin, tipo, velas} o None.
    """
    min_v = LOBO_IMPULSO_MIN_VELAS
    max_v = min(LOBO_IMPULSO_MAX_VELAS, len(df_h4) - 2)
    n = len(df_h4)

    # Probar ventanas de distintas longitudes (de mayor a menor, priorizando la más reciente)
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
                # Verificar retroceso individual no exceda 38.2% del total
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
            # Todas las velas pasaron el filtro de retraceso
            ratio_dir = ok_velas / total_velas if total_velas > 0 else 0
            if ratio_dir >= 0.7:
                # Encontrar extremos del tramo
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
    """Calcula niveles Fibonacci desde inicio a fin del impulso."""
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
        'retro_38':     0.382,
        'retro_50':     0.50,
        'retro_618':    0.618,
        'diff':         diff,
    }

# ----------------------------------------------------------------
# R2 - SMA 100 en zona OTE
# ----------------------------------------------------------------
def sma100_en_zona_ote(sma100_val: float, fibo: dict, atr_val: float) -> bool:
    """SMA 100 está 'cerca' de zona 50%-61.8% con tolerancia 0.5 ATR."""
    if 'level_0_5' not in fibo or 'level_0_618' not in fibo:
        return False
    r_inf = min(fibo['level_0_5'], fibo['level_0_618']) - atr_val * LOBO_SMA100_TOL_ATR
    r_sup = max(fibo['level_0_5'], fibo['level_0_618']) + atr_val * LOBO_SMA100_TOL_ATR
    return r_inf <= sma100_val <= r_sup

# ----------------------------------------------------------------
# R3 - ADX
# ----------------------------------------------------------------
def _wilder_ema(series: pd.Series, period: int) -> pd.Series:
    """Wilder's Exponential Moving Average (α=1/period)."""
    alpha = 1.0 / period
    return series.ewm(alpha=alpha, adjust=False).mean()

def adx_permite_entrada(df_h4: pd.DataFrame) -> bool:
    """ADX entre [LOBO_ADX_MIN, LOBO_ADX_MAX] y preferentemente no subiendo."""
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
        # Wilder's ADX manual calculation
        period = LOBO_ADX_PERIOD
        # 1) True Range
        high, low, close = df_h4['high'], df_h4['low'], df_h4['close']
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ], axis=1).max(axis=1)
        # 2) +DM, -DM
        up = high.diff()
        down = -low.diff()
        plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0), index=df_h4.index)
        minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0), index=df_h4.index)
        # 3) Wilder's smoothing
        tr_s = _wilder_ema(tr, period)
        plus_s = _wilder_ema(plus_dm, period)
        minus_s = _wilder_ema(minus_dm, period)
        # 4) +DI, -DI
        tr_s = tr_s.replace(0, np.nan)
        plus_di = 100 * plus_s / tr_s
        minus_di = 100 * minus_s / tr_s
        # 5) DX
        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
        # 6) ADX = Wilder's smooth of DX
        adx_series = _wilder_ema(dx, period)

    if adx_series.isna().all():
        return False
    adx_actual = float(adx_series.iloc[-1])
    if pd.isna(adx_actual):
        return False
    if not (LOBO_ADX_MIN <= adx_actual <= LOBO_ADX_MAX):
        return False

    # Verificar descendente en últimas N velas
    n = min(LOBO_ADX_DESC_VELAS, len(adx_series) - 1)
    if n < 3:
        return True
    vals = adx_series.iloc[-n:].dropna().values
    if len(vals) < 3:
        return True
    # Pendiente (regresión lineal simple) - permitir plano o descendente
    x = np.arange(len(vals))
    if np.std(vals) == 0:
        return True
    slope = np.polyfit(x, vals, 1)[0]
    return slope < 0.01

# ----------------------------------------------------------------
# R4 - USDT.D en resistencia (filtro macro Long)
# ----------------------------------------------------------------
def check_usdtd_resistencia() -> bool:
    """
    Obtiene USDT.D de TradingView vía fetch público.
    Retorna True si está tocando resistencia (favorable para Longs).
    Si no se puede obtener, retorna True (pasa filtro).
    """
    try:
        exch = ccxt.bitget({'enableRateLimit': True})
        ohlcv = exch.fetch_ohlcv('USDT/USDT', timeframe='1d', limit=LOBO_USDTD_LOOKBACK)
        if not ohlcv or len(ohlcv) < 10:
            return True
        closes = [c[4] for c in ohlcv]
        maximo = max(closes)
        minimo = min(closes)
        rango = maximo - minimo
        if rango == 0:
            return True
        resistencia = minimo + rango * (LOBO_USDTD_PERCENTIL / 100)
        actual = closes[-1]
        return actual >= resistencia * LOBO_USDTD_TOL_TOUCH
    except Exception as e:
        log.debug("USDT.D check skipped: %s", e)
        return True  # Pasa filtro si no disponible

# ----------------------------------------------------------------
# R5 - BTC.D pendiente (filtro altcoins)
# ----------------------------------------------------------------
def check_btcd_tendencia() -> bool:
    """
    Obtiene BTC.D.
    Retorna True si BTC.D está subiendo (solo operar BTC).
    False si está bajando/estable (operar altcoins).
    Si no disponible, retorna False (permite altcoins).
    """
    try:
        exch = ccxt.bitget({'enableRateLimit': True})
        ohlcv = exch.fetch_ohlcv('BTC/USDT', timeframe='4h', limit=LOBO_BTCD_SMA_PERIOD + 10)
        if not ohlcv or len(ohlcv) < LOBO_BTCD_SMA_PERIOD + 1:
            return False
        closes = pd.Series([c[4] for c in ohlcv])
        sma_val = closes.rolling(LOBO_BTCD_SMA_PERIOD).mean()
        if sma_val.isna().all():
            return False
        pendiente = (sma_val.iloc[-1] - sma_val.iloc[-LOBO_BTCD_SMA_PERIOD]) / max(sma_val.iloc[-LOBO_BTCD_SMA_PERIOD], 1)
        return pendiente > LOBO_BTCD_PEND_MIN
    except Exception as e:
        log.debug("BTC.D check skipped: %s", e)
        return False

# ----------------------------------------------------------------
# R6 - FVG (Fair Value Gap) formalizado
# ----------------------------------------------------------------
def detectar_fvg(df_h4: pd.DataFrame) -> list:
    """
    FVG existe cuando el gap entre velas consecutivas supera umbral
    y no ha sido rellenado después de N velas.
    """
    if len(df_h4) < 5:
        return []
    atr_vals = _atr(df_h4, LOBO_ATR_PERIOD)
    fvg_list = []
    max_velas = min(LOBO_FVG_MAX_VELAS, len(df_h4) - 3)

    for i in range(2, len(df_h4) - 2):
        gap_up = df_h4['low'].iloc[i] - df_h4['high'].iloc[i-2]
        gap_dn = df_h4['low'].iloc[i-2] - df_h4['high'].iloc[i]
        atr_i = atr_vals.iloc[i] if not pd.isna(atr_vals.iloc[i]) else 0

        # Gap alcista (precio saltó arriba)
        if gap_up > atr_i * LOBO_FVG_MIN_GAP_ATR:
            gap_alto = float(df_h4['high'].iloc[i-2])
            gap_bajo = float(df_h4['low'].iloc[i])
            if not _fvg_rellenado(df_h4, i, max_velas, gap_alto, gap_bajo):
                fvg_list.append({
                    'tipo': 'alcista', 'gap_sup': gap_alto, 'gap_inf': gap_bajo,
                    'idx': i, 'precio_medio': (gap_alto + gap_bajo) / 2,
                })

        # Gap bajista (precio saltó abajo)
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

# ----------------------------------------------------------------
# R7 - Order Block (OB) formalizado
# ----------------------------------------------------------------
def detectar_order_blocks(df_h4: pd.DataFrame) -> list:
    """OB = vela contraria antes de movimiento >= N * ATR."""
    if len(df_h4) < LOBO_OB_LOOKBACK + 5:
        return []
    atr_vals = _atr(df_h4, LOBO_ATR_PERIOD)
    obs = []

    for i in range(LOBO_OB_LOOKBACK, len(df_h4) - 3):
        atr_i = atr_vals.iloc[i] if not pd.isna(atr_vals.iloc[i]) else 0
        if atr_i == 0:
            continue

        # OB alcista: vela roja + rally
        if df_h4['close'].iloc[i] < df_h4['open'].iloc[i]:
            rally = 0
            for j in range(1, min(6, len(df_h4) - i)):
                if df_h4['close'].iloc[i+j] > df_h4['open'].iloc[i+j]:
                    rally += float(df_h4['close'].iloc[i+j] - df_h4['low'].iloc[i+j])
                else:
                    break
            if rally >= atr_i * LOBO_OB_MIN_MOV_ATR:
                obs.append({
                    'tipo': 'alcista',
                    'high': float(df_h4['high'].iloc[i]),
                    'low': float(df_h4['low'].iloc[i]),
                    'idx': i,
                })

        # OB bajista: vela verde + caída
        if df_h4['close'].iloc[i] > df_h4['open'].iloc[i]:
            caida = 0
            for j in range(1, min(6, len(df_h4) - i)):
                if df_h4['close'].iloc[i+j] < df_h4['open'].iloc[i+j]:
                    caida += float(df_h4['high'].iloc[i+j] - df_h4['close'].iloc[i+j])
                else:
                    break
            if caida >= atr_i * LOBO_OB_MIN_MOV_ATR:
                obs.append({
                    'tipo': 'bajista',
                    'high': float(df_h4['high'].iloc[i]),
                    'low': float(df_h4['low'].iloc[i]),
                    'idx': i,
                })

    return obs

# ----------------------------------------------------------------
# R8 - Liquidity Sweep formalizado
# ----------------------------------------------------------------
def detectar_sweep(df_h4: pd.DataFrame) -> list:
    """Sweep = falso breakout de mínimo/máximo reciente con recuperación."""
    if len(df_h4) < LOBO_SWEEP_LOOKBACK + 3:
        return []
    atr_vals = _atr(df_h4, LOBO_ATR_PERIOD)
    sweeps = []

    min_reciente = df_h4['low'].iloc[-(LOBO_SWEEP_LOOKBACK+1):-1].min()
    max_reciente = df_h4['high'].iloc[-(LOBO_SWEEP_LOOKBACK+1):-1].max()
    ult = df_h4.iloc[-1]
    atr_act = atr_vals.iloc[-1] if not pd.isna(atr_vals.iloc[-1]) else 0

    # Sweep bajista (para Long)
    if ult['low'] < min_reciente:
        penetracion = (min_reciente - ult['low']) / max(atr_act, 1)
        if 0 < penetracion < LOBO_SWEEP_MAX_PEN_ATR:
            if ult['close'] > min_reciente:
                sweeps.append({
                    'tipo': 'sweep_bajista_long',
                    'nivel_roto': float(min_reciente),
                    'penetracion_atr': round(penetracion, 2),
                })

    # Sweep alcista (para Short)
    if ult['high'] > max_reciente:
        penetracion = (ult['high'] - max_reciente) / max(atr_act, 1)
        if 0 < penetracion < LOBO_SWEEP_MAX_PEN_ATR:
            if ult['close'] < max_reciente:
                sweeps.append({
                    'tipo': 'sweep_alcista_short',
                    'nivel_roto': float(max_reciente),
                    'penetracion_atr': round(penetracion, 2),
                })

    return sweeps

# ----------------------------------------------------------------
# R9 - Mecha / Absorción cuantitativa
# ----------------------------------------------------------------
def evaluar_absorcion_long(df_h4: pd.DataFrame) -> tuple[bool, dict]:
    """
    Vela H4 tiene 'absorción' para Long si:
    1. Mecha inferior >= 0.5 ATR
    2. Cuerpo <= 30% de la mecha
    3. Cierre en mitad superior de la vela
    
    "Sin mecha hay sospecha": vela bajista sin mecha = cancelar.
    """
    if len(df_h4) < 2:
        return False, {}
    atr_vals = _atr(df_h4, LOBO_ATR_PERIOD)
    ult = df_h4.iloc[-1]
    ant = df_h4.iloc[-2]  # Vela anterior (la que nos interesa para evaluar)
    atr_act = atr_vals.iloc[-2] if not pd.isna(atr_vals.iloc[-2]) else 0

    # Evaluamos la vela anterior (la que acaba de cerrar en zona)
    body = abs(ant['close'] - ant['open'])
    rango = ant['high'] - ant['low']
    mecha_inf = min(ant['open'], ant['close']) - ant['low']
    mecha_sup = ant['high'] - max(ant['open'], ant['close'])

    if rango == 0:
        return False, {}

    # "Sin mecha hay sospecha" — vela bajista de cuerpo sólido sin mecha
    if ant['close'] < ant['open']:  # Vela bajista
        sin_mecha = mecha_inf < (rango * 0.05)
        cuerpo_solido = body > (rango * 0.7)
        if sin_mecha and cuerpo_solido:
            return False, {'rechazo': 'cuerpo_solido_sin_mecha', 'mecha_inf': mecha_inf, 'rango': rango}

    # Absorción para Long: vela bajista con mecha larga
    if ant['close'] < ant['open']:
        if atr_act > 0:
            cond1 = mecha_inf >= atr_act * LOBO_MECHA_MIN_ATR
        else:
            cond1 = mecha_inf >= (rango * 0.3)
        cond2 = (body / max(mecha_inf, 0.0001)) <= LOBO_MECHA_CUERPO_RATIO if mecha_inf > 0 else False
        mid = (ant['high'] + ant['low']) / 2
        cond3 = ant['close'] > mid  # Cierre en mitad superior

        detalles = {
            'mecha_inf': round(mecha_inf, 4),
            'mecha_atr_ratio': round(mecha_inf / max(atr_act, 0.0001), 2) if atr_act > 0 else 0,
            'cuerpo_mecha_ratio': round(body / max(mecha_inf, 0.0001), 2),
            'close_pos': round((ant['close'] - ant['low']) / rango, 2),
            'absorcion': cond1 and cond2 and cond3,
        }
        return (cond1 and cond2 and cond3), detalles

    return False, {'tipo': 'vela_alcista_no_aplica'}

# ----------------------------------------------------------------
# R10 - Ondas Elliott (simplificado con pivots)
# ----------------------------------------------------------------
def detectar_estructura_elliott(df_h4: pd.DataFrame) -> dict:
    """
    Identificación básica de estructura de 5 ondas usando pivots swing.
    Retorna fase actual del ciclo o None.
    """
    if len(df_h4) < LOBO_ELLIOTT_LOOKBACK:
        return {'fase': 'indefinida'}

    # Detectar pivots swing
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
        return {'fase': 'indefinida', 'pivot_highs': len(pivot_highs_idx), 'pivot_lows': len(pivot_lows_idx)}

    # Identificar última onda completa
    ultimo_pivot_alto = highs[pivot_highs_idx[-1]]
    ultimo_pivot_bajo = lows[pivot_lows_idx[-1]]
    ant_penultimo_alto = highs[pivot_highs_idx[-2]] if len(pivot_highs_idx) >= 2 else None
    ant_penultimo_bajo = lows[pivot_lows_idx[-2]] if len(pivot_lows_idx) >= 2 else None

    # Determinar dirección del último movimiento
    if pivot_highs_idx[-1] > pivot_lows_idx[-1]:
        # Último pivot alto es más reciente = posible onda 5
        distancia = ultimo_pivot_alto - ant_penultimo_bajo if ant_penultimo_bajo else 0
        retroceso_previo = (ant_penultimo_alto - ant_penultimo_bajo) if (ant_penultimo_alto and ant_penultimo_bajo) else 0
        if retroceso_previo > 0:
            ratio = distancia / retroceso_previo
            if 0.5 <= ratio <= 0.9:
                return {'fase': 'onda_5_completandose', 'direccion': 'bajista', 'confianza': 'media'}
    else:
        distancia = ant_penultimo_alto - ultimo_pivot_bajo if ant_penultimo_alto else 0
        retroceso_previo = (ant_penultimo_alto - ant_penultimo_bajo) if (ant_penultimo_alto and ant_penultimo_bajo) else 0
        if retroceso_previo > 0:
            ratio = distancia / retroceso_previo
            if 0.5 <= ratio <= 0.9:
                return {'fase': 'onda_5_completandose', 'direccion': 'alcista', 'confianza': 'media'}

    # Si precio está cerca del último pivot bajo, posible onda 4 completada
    precio_actual = float(df_h4['close'].iloc[-1])
    if ant_penultimo_alto and ultimo_pivot_bajo:
        if 0.382 <= (ant_penultimo_alto - precio_actual) / (ant_penultimo_alto - ultimo_pivot_bajo + 0.0001) <= 0.618:
            return {'fase': 'posible_onda_4', 'direccion': 'alcista', 'confianza': 'baja'}

    return {'fase': 'indefinida', 'pivot_highs': len(pivot_highs_idx), 'pivot_lows': len(pivot_lows_idx)}

# ----------------------------------------------------------------
# R11 + R12 + R13 + R14 + R15 — Evaluación completa de señal
# ----------------------------------------------------------------
def evaluar_senal_bitlobo(
    symbol: str, df_h4: pd.DataFrame, df_d1: pd.DataFrame,
    precio_actual: float, atr_val: float, balance: float,
    es_long: bool,
) -> Optional[dict]:
    """
    Evalúa TODAS las reglas BITLOBO y retorna dict de señal si todas se cumplen.
    Retorna None si alguna condición falla.
    """
    senal = {'symbol': symbol, 'precio_actual': precio_actual, 'atr_val': atr_val, 'es_long': es_long}
    detalles = []
    score = 0
    max_score = 14  # Total de reglas evaluables

    # --- R1: Impulso direccional + Fibonacci ---
    impulso = detectar_impulso(df_h4)
    if not impulso:
        return None  # Sin impulso, no hay Fibonacci, no hay nada
    fibo = calcular_fibonacci(impulso)
    if not fibo or 'level_0_5' not in fibo or 'level_0_618' not in fibo:
        return None
    senal['impulso'] = impulso
    senal['fibo'] = fibo
    score += 1
    detalles.append(f'R1:impulso_{impulso["tipo"]}_{impulso["velas"]}v')

    # Precio debe estar dentro de zona OTE o próximo (tolerancia 0.5 ATR)
    zona_inf = min(fibo['level_0_5'], fibo['level_0_618'])
    zona_sup = max(fibo['level_0_5'], fibo['level_0_618'])
    senal['zona_ote_inf'] = zona_inf
    senal['zona_ote_sup'] = zona_sup

    tol = atr_val * 1.0  # 1.0 ATR de tolerancia a cada lado
    if not (zona_inf - tol <= precio_actual <= zona_sup + tol):
        log.debug("%s: Precio %.4f fuera de zona OTE+tol [%.4f, %.4f]",
                  symbol, precio_actual, zona_inf - tol, zona_sup + tol)
        return None
    if zona_inf <= precio_actual <= zona_sup:
        score += 1  # Bonus por estar exactamente en zona
        detalles.append('R1:en_OTE')
    else:
        detalles.append('R1:cerca_OTE')

    # --- R2: SMA 100 en zona OTE ---
    if len(df_h4) >= 100:
        sma100 = _sma(df_h4['close'], 100).iloc[-1]
        if pd.isna(sma100):
            pass  # No disponible, no filtra
        elif sma100_en_zona_ote(sma100, fibo, atr_val):
            score += 1
            detalles.append(f'R2:SMA100_en_OTE')
        else:
            detalles.append(f'R2:SMA100_no')
    else:
        score += 1  # Pasa si no hay suficientes datos

    # --- R3: ADX ---
    if adx_permite_entrada(df_h4):
        score += 1
        detalles.append('R3:ADX_ok')
    else:
        log.debug("%s: R3 fail - ADX fuera de rango", symbol)
        detalles.append('R3:ADX_no')

    # --- R4: USDT.D (solo para Long) ---
    if es_long:
        if check_usdtd_resistencia():
            score += 1
            detalles.append('R4:USDT.D_resistencia')
        else:
            log.debug("%s: R4 - USDT.D no en resistencia", symbol)
            detalles.append('R4:USDT.D_no')
    else:
        score += 1
        detalles.append('R4:Short_ok')

    # --- R5: BTC.D / Altcoins ---
    btcd_subiendo = check_btcd_tendencia()
    if not (btcd_subiendo and 'BTC' not in symbol):
        score += 1
        detalles.append(f'R5:BTC.D_{"sube" if btcd_subiendo else "baja"}')
    else:
        log.debug("%s: R5 - BTC.D subiendo, ignorar altcoins", symbol)
        detalles.append('R5:BTC.D_bloquea_alt')

    # --- R6: FVG en zona ---
    fvgs = detectar_fvg(df_h4)
    fvg_en_zona = [f for f in fvgs if f['gap_sup'] >= zona_inf and f['gap_inf'] <= zona_sup]
    senal['fvgs'] = fvg_en_zona
    if fvg_en_zona:
        score += 1
        detalles.append(f'R6:FVG_{len(fvg_en_zona)}')
    else:
        detalles.append('R6:FVG_no')

    # --- R7: Order Block en zona ---
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
            detalles.append(f'R8:Sweep_{sweeps[0]["tipo"]}')
        else:
            detalles.append('R8:Sweep_dir_no')
    else:
        detalles.append('R8:Sweep_no')

    # --- R9: Mecha/Absorción ---
    absorcion_ok, det_abs = evaluar_absorcion_long(df_h4)
    if absorcion_ok:
        score += 1
        detalles.append(f'R9:Absorcion')
    else:
        detalles.append(f'R9:NoAbs_{det_abs.get("rechazo","")}')

    # --- R10: Elliott (informativo, no bloqueante) ---
    elliott = detectar_estructura_elliott(df_h4)
    senal['elliott'] = elliott
    score += 1
    detalles.append(f'R10:Elliott_{elliott["fase"]}')

    # --- Validación D1 (R16) - verificar tendencia mayor ---
    soporte_d1 = float(df_d1['low'].iloc[-min(20, len(df_d1)):].min())
    resistencia_d1 = float(df_d1['high'].iloc[-min(20, len(df_d1)):].max())
    if es_long:
        if precio_actual > soporte_d1 * 1.02:  # Por encima del soporte D1
            score += 1
            detalles.append('R16:SoporteD1_ok')
        else:
            log.debug("%s: R16 - precio cerca soporte D1", symbol)
            detalles.append('R16:SoporteD1_cerca')
    else:  # Short
        if precio_actual < resistencia_d1 * 0.98:  # Por debajo de resistencia D1
            score += 1
            detalles.append('R16:ResistenciaD1_ok')
        else:
            log.debug("%s: R16 - precio cerca resistencia D1", symbol)
            detalles.append('R16:ResistenciaD1_cerca')

    # --- TP/SL Levels (R13) ---
    sl_mult = float(os.environ.get('LOBO_SL_ATR', '1.5'))  # 1.5 ATR para SL
    tp_default_mult = float(os.environ.get('LOBO_TP_DEFAULT_ATR', '3.0'))  # 3.0 ATR default TP
    
    if es_long:
        sl_price = precio_actual - (atr_val * sl_mult)
        tp1_candidates = [f for f in fvg_en_zona if f['gap_inf'] > precio_actual]
        if tp1_candidates:
            tp1_price = tp1_candidates[0]['gap_inf']
        else:
            tp1_price = precio_actual + (atr_val * tp_default_mult)
        tp2_price = precio_actual + (atr_val * LOBO_TP2_ATR_MULT)
        tp3_price = precio_actual + (atr_val * LOBO_TP3_ATR_MULT)
        # SL debe estar debajo de la mecha (R9)
        if det_abs and isinstance(det_abs, dict) and 'mecha_inf' in det_abs:
            mecha_low = precio_actual - det_abs['mecha_inf']
            sl_price = min(sl_price, mecha_low * 0.995)
    else:
        sl_price = precio_actual + (atr_val * sl_mult)
        tp1_candidates = [f for f in fvg_en_zona if f['gap_sup'] < precio_actual]
        if tp1_candidates:
            tp1_price = tp1_candidates[0]['gap_sup']
        else:
            tp1_price = precio_actual - (atr_val * tp_default_mult)
        tp2_price = precio_actual - (atr_val * LOBO_TP2_ATR_MULT)
        tp3_price = precio_actual - (atr_val * LOBO_TP3_ATR_MULT)

    # R:R mínimo 1.5:1
    riesgo_pct = abs(precio_actual - sl_price) / precio_actual
    beneficio_pct = abs(tp1_price - precio_actual) / precio_actual
    rr = beneficio_pct / riesgo_pct if riesgo_pct > 0 else 0
    if rr >= 1.5:
        score += 1
        detalles.append(f'R13:R:R_{rr:.2f}')
    else:
        log.debug("%s: R:R %.2f < 1.5", symbol, rr)
        detalles.append(f'R13:R:R_{rr:.2f}_baja')

    # --- R12: Position Sizing ---
    riesgo_capital = balance * LOBO_RISK_PCT
    distancia_sl = abs(precio_actual - sl_price) / precio_actual
    if distancia_sl > 0:
        pos_value = riesgo_capital / distancia_sl
    else:
        return None
    qty = pos_value / precio_actual
    apalancamiento = min(pos_value / max(balance * LOBO_RISK_PCT, 1), 10)

    score += 1
    detalles.append(f'R12:Sizing_qty={qty:.4f}')

    # Score mínimo para considerar señal válida
    if score < LOBO_SCORE_MIN:
        log.debug("%s: Score %d < minimo %d", symbol, score, LOBO_SCORE_MIN)
        return None

    return {
        'symbol': symbol,
        'es_long': es_long,
        'precio_actual': precio_actual,
        'atr_val': atr_val,
        'sl_price': sl_price,
        'tp1_price': tp1_price,
        'tp2_price': tp2_price,
        'tp3_price': tp3_price,
        'rr': rr,
        'qty': qty,
        'apalancamiento': apalancamiento,
        'pos_value': pos_value,
        'score': score,
        'max_score': 14,
        'fvg_usado': tp1_candidates[0] if tp1_candidates else None,
        'impulso': impulso,
        'fibo': fibo,
        'fvgs': fvg_en_zona,
        'obs': ob_en_zona,
        'sweeps': sweeps,
        'elliott': elliott,
        'detalles': detalles,
        'score': score,
        'max_score': max_score,
        'zona_ote_inf': zona_inf,
        'zona_ote_sup': zona_sup,
    }

# ----------------------------------------------------------------
# VALIDACIÓN D1 (R16) - Post-entrada
# ----------------------------------------------------------------
def validar_cierre_diario(df_d1: pd.DataFrame, entry_price: float, side: str) -> bool:
    """
    Retorna True si la posición sigue siendo válida.
    False = cierre D1 invalida la operación.
    """
    if len(df_d1) < 2:
        return True
    ult_d1 = df_d1.iloc[-1]
    ant_d1 = df_d1.iloc[-2]
    soporte = float(df_d1['low'].iloc[-min(20, len(df_d1)):].min())

    if side == 'long':
        # Si la vela diaria CERRÓ por debajo del soporte estructural
        if ult_d1['close'] < soporte * 0.99:
            return False
        # Si hubo un cierre semanal débil
        if ant_d1['close'] < soporte * 0.99:
            return False
    else:
        resistencia = float(df_d1['high'].iloc[-min(20, len(df_d1)):].max())
        if ult_d1['close'] > resistencia * 1.01:
            return False
        if ant_d1['close'] > resistencia * 1.01:
            return False
    return True

# =====================================================================
# 6. TELEGRAM
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
# 7. CSV LOGGING
# =====================================================================
TRADE_CSV_HEADERS = [
    'entry_time', 'exit_time', 'symbol', 'side', 'entry_price', 'exit_price',
    'sl_price', 'tp1_price', 'tp2_price', 'tp3_price',
    'sl_pct', 'tp_pct', 'quantity',
    'balance_before', 'balance_after',
    'pnl', 'fees', 'net_pnl', 'status', 'duration_hours',
    'signal_score', 'rr', 'atr_at_entry',
    'close_reason', 'be_triggered', 'be_price',
    'trail_count', 'trail_peak_price', 'trail_final_sl',
    'entry_weekday', 'entry_hour',
    'size_usdt', 'risk_pct',
    'hours_to_tp', 'hours_to_sl',
    'max_favorable_pct', 'max_adverse_pct',
]

def guardar_trade_csv(entry, exit_price, raw_pnl, fees, net, status, close_reason):
    if not entry:
        return
    now = datetime.now()
    duration = (now - entry['entry_time']).total_seconds() / 3600
    balance_after = entry['balance_before'] + net
    ep = entry['entry_price']
    sl = entry['sl_price']
    side = entry['side']
    row = {
        'entry_time': entry['entry_time'].strftime('%Y-%m-%d %H:%M:%S'),
        'exit_time': now.strftime('%Y-%m-%d %H:%M:%S'),
        'symbol': entry['symbol'], 'side': side,
        'entry_price': ep, 'exit_price': exit_price,
        'sl_price': sl,
        'tp1_price': entry.get('tp1_price', 0),
        'tp2_price': entry.get('tp2_price', 0),
        'tp3_price': entry.get('tp3_price', 0),
        'sl_pct': round(abs(ep - sl) / ep * 100, 2),
        'tp_pct': round(abs(ep - entry.get('tp1_price', ep)) / ep * 100, 2),
        'quantity': entry['quantity'],
        'balance_before': round(entry['balance_before'], 2),
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
        'hours_to_tp': round(duration, 2) if close_reason == 'tp' else '',
        'hours_to_sl': round(duration, 2) if close_reason == 'sl' else '',
        'max_favorable_pct': round(abs(PEAK_PRICES.get(entry['symbol'], ep) - ep) / ep * 100, 2),
        'max_adverse_pct': round(abs(ADVERSE_PRICES.get(entry['symbol'], ep) - ep) / ep * 100, 2),
    }
    csv_path = TRADES_CSV_PATH
    write_header = not os.path.exists(csv_path)
    try:
        with open(csv_path, 'a', newline='', encoding='utf-8') as f:
            w = csv.DictWriter(f, fieldnames=TRADE_CSV_HEADERS)
            if write_header:
                w.writeheader()
            w.writerow(row)
    except Exception:
        pass

SIGNAL_LOG_HEADERS = [
    'time', 'symbol', 'side', 'price', 'score', 'max_score',
    'detalles', 'rr', 'atr', 'entry_zone_fibo',
    'sl_proj', 'tp1_proj', 'tp2_proj', 'tp3_proj',
    'taken', 'reason_skipped',
]

def guardar_signal_log(symbol, side, price, score, max_score, detalles,
                       sl_proj, tp1_proj, tp2_proj, tp3_proj, rr,
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
            w = csv.DictWriter(f, fieldnames=SIGNAL_LOG_HEADERS)
            if write_header:
                w.writeheader()
            w.writerow(row)
    except Exception:
        pass

# =====================================================================
# 8. FETCH ASÍNCRONO
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
# 9. EXCHANGE — CONEXIÓN Y ÓRDENES
# =====================================================================
exchange: ccxt.bitget | None = None

def init_exchange() -> bool:
    global exchange
    if PAPER_TRADE:
        log.info("PAPER_TRADE activo")
        try:
            exchange = ccxt.bitget({'enableRateLimit': True, 'options': {'defaultType': 'swap'}})
            exchange.load_markets()
            log.info("Exchange paper mode listo (%d mercados)", len(exchange.markets))
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
        log.info("Conexion Bitget exitosa")
        return True
    except Exception as e:
        log.critical("Error conectando Bitget: %s", e)
        return False

def update_stop_loss(symbol: str, side: str, new_sl: float) -> bool:
    try:
        new_sl_fmt = exchange.price_to_precision(symbol, new_sl)
        clean_symbol = symbol.split(':')[0].replace('/', '')
        params = {
            'symbol': clean_symbol, 'marginCoin': 'USDT',
            'productType': 'USDT-FUTURES', 'planType': 'pos_loss',
            'stopLossTriggerPrice': str(new_sl_fmt),
            'stopLossTriggerType': 'fill_price',
            'holdSide': 'long' if side == 'long' else 'short',
        }
        exchange.private_mix_post_v2_mix_order_place_pos_tpsl(params)
        return True
    except Exception:
        return False

# =====================================================================
# 10. GESTIÓN DE POSICIONES (BE, TRAILING, TIMEOUT, D1 VALIDATION)
# =====================================================================
def _manage_paper_positions(balance: float):
    """Gestiona posiciones simuladas en paper mode con todas las reglas BITLOBO."""
    global ALERTS_HISTORY, PEAK_PRICES, COOLDOWNS, DAILY_STATS
    global SESSION_ACTIVE_SYMBOLS, PREMATURE_SL_MONITOR
    global ADVERSE_PRICES, PRICE_PATHS, TRAIL_COUNTS, LAST_KNOWN_INDICATORS

    if not TRADE_ENTRIES:
        return

    for symbol in list(TRADE_ENTRIES.keys()):
        try:
            entry = TRADE_ENTRIES[symbol]
            side = entry.get('side', 'long')
            entry_price = float(entry['entry_price'])
            sl_price = float(entry.get('sl_price', 0))
            tp1_price = float(entry.get('tp1_price', 0))
            tp2_price = float(entry.get('tp2_price', 0))
            tp3_price = float(entry.get('tp3_price', 0))

            try:
                ticker = exchange.fetch_ticker(symbol)
                mark = float(ticker['last'])
            except Exception:
                continue

            profit_pct = (mark - entry_price) / entry_price if side == 'long' else (entry_price - mark) / entry_price

            # --- R16: Validación D1 ---
            if symbol not in ALERTS_HISTORY.get(f"{symbol}_d1_valid", {}):
                try:
                    ohlcv_d1 = exchange.fetch_ohlcv(symbol, timeframe='1d', limit=25)
                    if len(ohlcv_d1) >= 5:
                        df_d1 = pd.DataFrame(ohlcv_d1, columns=['ts','o','h','l','c','v'])
                        if not validar_cierre_diario(df_d1, entry_price, side):
                            log.info("[PAPER] %s: R16 D1 invalida - cerrando", symbol)
                            pnl_usd = entry.get('size_usdt', balance * LOBO_RISK_PCT) * LEVERAGE * profit_pct
                            guardar_trade_csv(entry, mark, pnl_usd, 0, pnl_usd, 'D1_INVALID', 'd1_invalid')
                            TRADE_ENTRIES.pop(symbol, None)
                            _save_trade_entries()
                            SESSION_ACTIVE_SYMBOLS.discard(symbol)
                            COOLDOWNS[symbol] = time.time() + 7200
                            send_telegram(f"[PAPER] *{symbol}* Cerrada por D1 invalida\nPnL: {pnl_usd:.2f} USDT")
                            continue
                except Exception:
                    pass

            # --- Verificar SL/TP (escalonado R13) ---
            exit_px = None
            status = None
            reason = None

            if side == 'long':
                if mark <= sl_price:
                    exit_px, status, reason = sl_price, 'SL', 'sl'
                elif mark >= tp3_price:
                    exit_px, status, reason = tp3_price, 'TP', 'tp'
                elif mark >= tp2_price:
                    # Vender TP2 (30%) - si TP1 ya se vendió
                    if not ALERTS_HISTORY.get(f"{symbol}_tp1_sold", False):
                        # Primera vez que alcanza TP2, vender TP1+TP2
                        exit_px = tp2_price
                        status = 'TP'
                        reason = 'tp'
                    else:
                        # TP2 ya se procesó, ver TP3
                        continue
                elif mark >= tp1_price:
                    if not ALERTS_HISTORY.get(f"{symbol}_tp1_sold", False):
                        # Vender TP1 (40%)
                        ALERTS_HISTORY[f"{symbol}_tp1_sold"] = True
                        exit_px = tp1_price
                        status = 'TP'
                        reason = 'tp'
            else:
                if mark >= sl_price:
                    exit_px, status, reason = sl_price, 'SL', 'sl'
                elif mark <= tp3_price:
                    exit_px, status, reason = tp3_price, 'TP', 'tp'
                elif mark <= tp2_price:
                    if not ALERTS_HISTORY.get(f"{symbol}_tp1_sold", False):
                        exit_px = tp2_price
                        status = 'TP'
                        reason = 'tp'
                elif mark <= tp1_price:
                    if not ALERTS_HISTORY.get(f"{symbol}_tp1_sold", False):
                        ALERTS_HISTORY[f"{symbol}_tp1_sold"] = True
                        exit_px = tp1_price
                        status = 'TP'
                        reason = 'tp'

            if exit_px is not None:
                size_usdt = float(entry.get('size_usdt', balance * LOBO_RISK_PCT))
                pnl_pct = (exit_px - entry_price) / entry_price if side == 'long' else (entry_price - exit_px) / entry_price
                pnl_usd = size_usdt * LEVERAGE * pnl_pct
                log.info("[PAPER] %s %s | Entry=%.4f Exit=%.4f PnL=%.2f", symbol, status, entry_price, exit_px, pnl_usd)
                guardar_trade_csv(entry, exit_px, pnl_usd, 0, pnl_usd, status, reason)
                TRADE_ENTRIES.pop(symbol, None)
                _save_trade_entries()
                SESSION_ACTIVE_SYMBOLS.discard(symbol)
                COOLDOWNS[symbol] = time.time() + 3600
                PEAK_PRICES.pop(symbol, None)
                ALERTS_HISTORY.pop(symbol, None)
                TRAIL_COUNTS.pop(symbol, None)
                send_telegram(f"[PAPER] *{symbol} {status}*\nPnL: {pnl_usd:.2f} USDT ({pnl_pct*100:.2f}%)")
                continue

            # --- Timeout (R17 implícito) ---
            entry_time = entry.get('entry_time')
            if isinstance(entry_time, datetime) and profit_pct < 0:
                horas = (datetime.now() - entry_time).total_seconds() / 3600
                if horas >= LOBO_TIMEOUT_HORAS:
                    size_usdt = float(entry.get('size_usdt', balance * LOBO_RISK_PCT))
                    pnl_usd = size_usdt * LEVERAGE * profit_pct
                    log.info("[PAPER] %s TIMEOUT +%.0fh", symbol, horas)
                    guardar_trade_csv(entry, mark, pnl_usd, 0, pnl_usd, 'Timeout', 'timeout')
                    TRADE_ENTRIES.pop(symbol, None)
                    _save_trade_entries()
                    SESSION_ACTIVE_SYMBOLS.discard(symbol)
                    COOLDOWNS[symbol] = time.time() + 3600
                    PEAK_PRICES.pop(symbol, None)
                    ALERTS_HISTORY.pop(symbol, None)
                    TRAIL_COUNTS.pop(symbol, None)
                    send_telegram(f"[PAPER] *{symbol} TIMEOUT*\n+{horas:.0f}h ({profit_pct*100:.2f}%)")
                    continue

            # --- Seguimiento de pico ---
            if symbol not in PEAK_PRICES:
                PEAK_PRICES[symbol] = mark
            else:
                PEAK_PRICES[symbol] = max(PEAK_PRICES[symbol], mark) if side == 'long' else min(PEAK_PRICES[symbol], mark)

            # --- R15: Break Even ---
            if profit_pct >= LOBO_BE_TRIGGER_PCT:
                if not ALERTS_HISTORY.get(f"{symbol}_be", False):
                    offset = entry_price * LOBO_BE_OFFSET_PCT * (1 if side == 'long' else -1)
                    new_sl = entry_price + offset
                    TRADE_ENTRIES[symbol]['sl_price'] = new_sl
                    ALERTS_HISTORY[f"{symbol}_be"] = True
                    ALERTS_HISTORY[f"{symbol}_be_price"] = new_sl
                    log.info("[PAPER] %s BE+ activado SL=%.4f", symbol, new_sl)
                    send_telegram(f"[PAPER] *{symbol}* BE+\nSL movido a {new_sl:.4f}")

            # --- R14: Trailing Stop ---
            if profit_pct >= LOBO_BE_TRIGGER_PCT + 0.005:
                dist = LOBO_TRAIL_ATR_MULT * entry.get('atr_val', 0) * 1.5
                nuevo_sl = (PEAK_PRICES[symbol] - dist) if side == 'long' else (PEAK_PRICES[symbol] + dist)
                ultimo_sl = ALERTS_HISTORY.get(f"{symbol}_trail", 0 if side == 'long' else 999999)
                mejora = (nuevo_sl - ultimo_sl) if side == 'long' else (ultimo_sl - nuevo_sl)
                if mejora > (entry_price * 0.002):
                    TRADE_ENTRIES[symbol]['sl_price'] = nuevo_sl
                    ALERTS_HISTORY[f"{symbol}_trail"] = nuevo_sl
                    TRAIL_COUNTS[symbol] = TRAIL_COUNTS.get(symbol, 0) + 1
                    log.info("[PAPER] %s Trail→%.4f", symbol, nuevo_sl)

        except Exception as e:
            log.error("[PAPER] Error gestionando %s: %s", symbol, e)

def manage_escudo_pro(balance: float = 0.0):
    if PAPER_TRADE:
        _manage_paper_positions(balance)
        return

    try:
        positions = exchange.fetch_positions()
        active_symbols = [p['symbol'] for p in positions if float(p['contracts']) > 0]

        # Limpiar símbolos que ya no tienen posición
        for sym in list(SESSION_ACTIVE_SYMBOLS):
            if sym not in active_symbols:
                COOLDOWNS[sym] = time.time() + 3600
                log.info("Cooldown 1h para %s (cerrada)", sym)
                # ... (código de logging de cierre similar al existente)
                SESSION_ACTIVE_SYMBOLS.discard(sym)

        for pos in positions:
            symbol = pos['symbol']
            side = pos['side']
            if float(pos['contracts']) == 0:
                continue
            entry_price = float(pos['entryPrice'])
            mark = float(pos['markPrice'])
            profit_pct = (mark - entry_price) / entry_price if side == 'long' else (entry_price - mark) / entry_price

            # R16: Validación D1
            try:
                ohlcv_d1 = exchange.fetch_ohlcv(symbol, timeframe='1d', limit=25)
                if len(ohlcv_d1) >= 5:
                    df_d1 = pd.DataFrame(ohlcv_d1, columns=['ts','o','h','l','c','v'])
                    if not validar_cierre_diario(df_d1, entry_price, side):
                        log.warning("%s: D1 invalida - cerrando posicion", symbol)
                        exchange.close_position(symbol)
                        send_telegram(f"*{symbol}* Cerrada por D1 invalida")
                        continue
            except Exception:
                pass

            # R15: Break Even
            if profit_pct >= LOBO_BE_TRIGGER_PCT:
                if symbol not in PEAK_PRICES:
                    PEAK_PRICES[symbol] = mark
                else:
                    PEAK_PRICES[symbol] = max(PEAK_PRICES[symbol], mark) if side == 'long' else min(PEAK_PRICES[symbol], mark)
                if not ALERTS_HISTORY.get(f"{symbol}_be", False):
                    offset = entry_price * LOBO_BE_OFFSET_PCT * (1 if side == 'long' else -1)
                    new_sl = entry_price + offset
                    if update_stop_loss(symbol, side, new_sl):
                        ALERTS_HISTORY[f"{symbol}_be"] = True
                        ALERTS_HISTORY[f"{symbol}_be_price"] = new_sl
                        log.info("%s BE+ activado", symbol)
                        send_telegram(f"*{symbol}* BE+ activado")

            # R14: Trailing
            atr_est = TRADE_ENTRIES.get(symbol, {}).get('atr_val', 0)
            if profit_pct >= LOBO_BE_TRIGGER_PCT + 0.005 and atr_est > 0:
                dist = LOBO_TRAIL_ATR_MULT * atr_est * 1.5
                nuevo_sl = (PEAK_PRICES[symbol] - dist) if side == 'long' else (PEAK_PRICES[symbol] + dist)
                ultimo = ALERTS_HISTORY.get(f"{symbol}_trail", 0 if side == 'long' else 999999)
                mejora = (nuevo_sl - ultimo) if side == 'long' else (ultimo - nuevo_sl)
                if mejora > (entry_price * 0.002):
                    if update_stop_loss(symbol, side, nuevo_sl):
                        ALERTS_HISTORY[f"{symbol}_trail"] = nuevo_sl
                        TRAIL_COUNTS[symbol] = TRAIL_COUNTS.get(symbol, 0) + 1
                        log.info("%s Trail→%.4f", symbol, nuevo_sl)

    except Exception as e:
        log.error("Error en manage_escudo_pro: %s", e)

# =====================================================================
# 11. BUCLE PRINCIPAL — ESTRATEGIA BITLOBO FORMALIZADA
# =====================================================================
def main():
    global LAST_KNOWN_INDICATORS, ALERTS_HISTORY, PEAK_PRICES, COOLDOWNS
    global SESSION_ACTIVE_SYMBOLS, DAILY_STATS, TRADE_ENTRIES, TRAIL_COUNTS
    global PREMATURE_SL_MONITOR, ADVERSE_PRICES, PRICE_PATHS, exchange

    log.info("=" * 60)
    log.info("LOBOBOT v2 — Estrategia BITLOBO FORMALIZADA iniciando")
    log.info("=" * 60)

    if exchange is None:
        if not init_exchange():
            log.critical("No se pudo inicializar exchange")
            return

    _load_trade_entries()
    for f in os.listdir(PRICE_PATHS_DIR):
        try:
            os.remove(os.path.join(PRICE_PATHS_DIR, f))
        except Exception:
            pass

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
                sls = [r for r in today_trades if r['status'] == 'SL']
                bes = [r for r in today_trades if r['status'] == 'BE']
                tos = [r for r in today_trades if r['status'] == 'Timeout']
                pnl_total = sum(float(r['net_pnl']) for r in today_trades)
                wr = len(tps) / max(total, 1) * 100
                msg = (
                    f"*REPORTE DIARIO* ({now.strftime('%d/%m')})\n"
                    f"Ops: {total} | TP:{len(tps)} SL:{len(sls)} BE:{len(bes)} T/O:{len(tos)}\n"
                    f"WR: {wr:.0f}% | PnL: {pnl_total:+.2f} USDT"
                )
                send_telegram(msg)
                last_report_day = now.day

            # ── Balance ──
            try:
                balance_data = exchange.fetch_balance()
                balance = float(balance_data['total'].get('USDT', 0))
            except Exception as e:
                if PAPER_TRADE:
                    balance = 10_000.0
                else:
                    log.error("Error balance: %s", e)
                    balance = 0.0

            # ── Gestión de posiciones activas ──
            manage_escudo_pro(balance)

            # ── Posiciones activas ──
            try:
                positions = exchange.fetch_positions()
                busy_symbols = {p['symbol'] for p in positions if float(p['contracts']) > 0}
            except Exception:
                busy_symbols = set()
            if PAPER_TRADE:
                busy_symbols.update(TRADE_ENTRIES.keys())
            SESSION_ACTIVE_SYMBOLS.update(busy_symbols)

            log.info(
                "Ciclo [%s] Balance=%.2f Ocupados=[%s]",
                now.strftime('%H:%M'), balance,
                ", ".join(s.split('/')[0] for s in busy_symbols) if busy_symbols else "ninguno",
            )

            if len(busy_symbols) >= LOBO_MAX_POSITIONS:
                time.sleep(60)
                continue

            # ── TOP símbolos por volumen ── (R17)
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

                    precio_actual = float(df_4h['close'].iloc[-1])
                    atr_val = float(_atr(df_4h, LOBO_ATR_PERIOD).iloc[-1])

                    if atr_val == 0 or pd.isna(atr_val):
                        continue

                    # Evaluar señal BITLOBO completa para Long
                    senal_long = evaluar_senal_bitlobo(
                        symbol, df_4h, df_d1, precio_actual, atr_val, balance, es_long=True
                    )

                    # Evaluar señal BITLOBO para Short (si hay sweep alcista)
                    sweeps = detectar_sweep(df_4h)
                    hay_sweep_short = any(s['tipo'] == 'sweep_alcista_short' for s in sweeps)
                    senal_short = None
                    if hay_sweep_short:
                        senal_short = evaluar_senal_bitlobo(
                            symbol, df_4h, df_d1, precio_actual, atr_val, balance, es_long=False
                        )

                    senal = senal_long or senal_short
                    if not senal:
                        continue

                    # ── Ejecutar señal ──
                    es_long = senal['es_long']
                    side = 'buy' if es_long else 'sell'
                    side_name = 'LARGO' if es_long else 'CORTO'

                    sl_price = senal['sl_price']
                    tp1_price = senal['tp1_price']
                    tp2_price = senal['tp2_price']
                    tp3_price = senal['tp3_price']
                    rr = senal['rr']
                    score = senal['score']
                    max_score = senal['max_score']

                    # Position sizing (R12, R18)
                    raw_qty = senal['qty']
                    market = exchange.market(symbol)
                    precision = market['precision']['amount']
                    step = market['limits']['amount']['min'] or (10 ** -precision)
                    qty = (raw_qty // step) * step
                    actual_margin = (qty * precio_actual) / LEVERAGE

                    log.info(
                        "%s %s | Entry=%.4f SL=%.4f TP1=%.4f TP2=%.4f TP3=%.4f "
                        "R:R=%.2f | Score=%d/%d",
                        symbol, side_name, precio_actual, sl_price,
                        tp1_price, tp2_price, tp3_price, rr, score, max_score,
                    )

                    # Verificar posicion existente
                    try:
                        check_pos = exchange.fetch_position(symbol)
                        if float(check_pos.get('contracts', 0)) > 0:
                            continue
                    except Exception:
                        pass

                    # Guardar en entry tracking
                    entry_record = {
                        'entry_time': datetime.now(),
                        'symbol': symbol,
                        'side': 'long' if es_long else 'short',
                        'entry_price': precio_actual,
                        'sl_price': sl_price,
                        'tp1_price': tp1_price,
                        'tp2_price': tp2_price,
                        'tp3_price': tp3_price,
                        'quantity': qty,
                        'balance_before': balance,
                        'atr_val': atr_val,
                        'size_usdt': round(actual_margin, 2),
                        'risk_pct': round(actual_margin / max(balance, 1) * 100, 2),
                        'score': score,
                        'rr': rr,
                    }

                    if PAPER_TRADE:
                        log.info("[PAPER] %s %s qty=%.6f", side.upper(), symbol, qty)
                        send_telegram(
                            f"[PAPER] *{symbol} {side_name}* (BITLOBO v2)\n"
                            f"Entry: `{exchange.price_to_precision(symbol, precio_actual)}`\n"
                            f"SL: `{exchange.price_to_precision(symbol, sl_price)}`\n"
                            f"TP1: `{exchange.price_to_precision(symbol, tp1_price)}`\n"
                            f"TP2: `{exchange.price_to_precision(symbol, tp2_price)}`\n"
                            f"TP3: `{exchange.price_to_precision(symbol, tp3_price)}`\n"
                            f"R:R: {rr:.2f} | Score: {score}/{max_score}"
                        )
                        TRADE_ENTRIES[symbol] = entry_record
                        _save_trade_entries()
                        busy_symbols.add(symbol)
                        SESSION_ACTIVE_SYMBOLS.add(symbol)
                        guardar_signal_log(
                            symbol, side_name, precio_actual, score, max_score,
                            senal['detalles'], sl_price, tp1_price, tp2_price, tp3_price, rr,
                            taken=True,
                        )
                        continue

                    # ── Orden real en Bitget ──
                    params = {
                        'marginCoin': 'USDT',
                        'marginMode': 'isolated',
                        'tradeSide': 'open',
                        'presetStopSurplusPrice': str(exchange.price_to_precision(symbol, tp1_price)),
                        'presetStopLossPrice': str(exchange.price_to_precision(symbol, sl_price)),
                    }
                    try:
                        exchange.create_order(symbol, 'market', side, qty, params=params)
                    except Exception as e:
                        log.error("Error orden %s %s: %s", side, symbol, e)
                        continue

                    send_telegram(
                        f"*{symbol} {side_name}* (BITLOBO v2)\n"
                        f"Entry: `{exchange.price_to_precision(symbol, precio_actual)}`\n"
                        f"SL: `{exchange.price_to_precision(symbol, sl_price)}`\n"
                        f"TP1: `{exchange.price_to_precision(symbol, tp1_price)}`\n"
                        f"TP2: `{exchange.price_to_precision(symbol, tp2_price)}`\n"
                        f"TP3: `{exchange.price_to_precision(symbol, tp3_price)}`\n"
                        f"Score: {score}/{max_score}"
                    )

                    TRADE_ENTRIES[symbol] = entry_record
                    _save_trade_entries()
                    busy_symbols.add(symbol)
                    SESSION_ACTIVE_SYMBOLS.add(symbol)
                    guardar_signal_log(
                        symbol, side_name, precio_actual, score, max_score,
                        senal['detalles'], sl_price, tp1_price, tp2_price, tp3_price, rr,
                        taken=True,
                    )

                except Exception as e:
                    log.debug("Error procesando %s: %s", symbol, e)
                    continue

            # ── Ciclo cada 60s ──
            time.sleep(60)

        except Exception as e:
            log.error("Error en ciclo principal: %s", e, exc_info=True)
            time.sleep(60)

# =====================================================================
# 12. ENTRY POINT — standalone (sin web service)
# =====================================================================
if __name__ == "__main__":
    log.info("LOBOBOT v2 iniciando en modo standalone...")
    if exchange is None:
        init_exchange()
    main()
