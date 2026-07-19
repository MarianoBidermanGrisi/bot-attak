#!/usr/bin/env python3
"""
LOBOBOT v4 — BITLOBO TRADING (Alineación Completa con Estrategia Documentada)
================================================================================

Correcciones respecto a v2:
  F1 - Split de capital 20/80/0 (liquidez/futuros/spot)
  F2 - Dominancias reales USDT.D / BTC.D (CoinGecko + proxy calculado)
  F3 - Stop Loss por liquidación forzosa (anti-cacería de stops)
  F4 - Coberturas asimétricas (hedging de emergencia hiper-apalancado)
  F5 - RSI filtro obligatorio + Volumen como validador
  F6 - Confirmación Pullback ("Rompe y Apoya")
  F7 - Timing de entrada al cierre de vela H4
  F8 - Riesgo base 1.5-2% sobre el 80% de la cuenta de futuros
  F9 - Break Even al alcanzar TP1 (no al 1.5%)
  F10- Invalidación D1 estructural (swing points)
  F11- Ondas Elliott con relaciones Fibonacci entre ondas
  F12- TPs en zonas reales (FVG/OB/estructurales)

Uso:
    python lobobot_v3.py                          # Bot standalone
    gunicorn lobobot_v3:app --workers 1 --threads 2   # Render

Variables de entorno (nuevas respecto a v2):
    LOBO_LIQUIDEZ_PCT=20    LOBO_FUTUROS_PCT=80
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
PARTIAL_LEVEL: dict = {}       # 0=nada, 1=TP1 hecho, 2=TP2 hecho

# Cache para F2: dominancias (evita llamadas API repetitivas)
DOMINANCE_CACHE: dict = {'btc': None, 'usdtd': None, 'ts': 0}
DOMINANCE_CACHE_TTL = 300  # 5 minutos
# Historial de USDT.D proxy para detección de FVG
USDTD_HISTORY: list = []  # [(timestamp, proxy_value), ...]

# =====================================================================
# 3. RUTAS DE ARCHIVOS
# =====================================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PRICE_PATHS_DIR = os.path.join(BASE_DIR, 'price_paths_v3')
os.makedirs(PRICE_PATHS_DIR, exist_ok=True)
TRADES_CSV_PATH      = os.path.join(BASE_DIR, 'trades_v3.csv')
TRADE_ENTRIES_PATH   = os.path.join(BASE_DIR, 'trade_entries_v3.json')
PARTIAL_LEVEL_PATH   = os.path.join(BASE_DIR, 'partial_level_v3.json')
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

def _save_partial_level():
    try:
        with open(PARTIAL_LEVEL_PATH, 'w', encoding='utf-8') as f:
            json.dump(PARTIAL_LEVEL, f, ensure_ascii=False, indent=2)
    except Exception as ex:
        log.error("Error guardando partial_level: %s", ex)

def _load_partial_level():
    try:
        if not os.path.exists(PARTIAL_LEVEL_PATH):
            return
        with open(PARTIAL_LEVEL_PATH, 'r', encoding='utf-8') as f:
            loaded = json.load(f)
        loaded = {k: int(v) for k, v in loaded.items()}
        PARTIAL_LEVEL.update(loaded)
        log.info("Cargados %d estados parciales de partial_level_v3.json", len(loaded))
    except Exception as ex:
        log.error("Error cargando partial_level: %s", ex)

# =====================================================================
# 4. CONFIGURACIÓN DESDE ENTORNO (incluye nuevos parámetros F1-F12)
# =====================================================================
API_KEY      = os.environ.get('BITGET_API_KEY', '')
SECRET_KEY   = os.environ.get('BITGET_SECRET_KEY', '')
PASSPHRASE   = os.environ.get('BITGET_PASSPHRASE', '')
TELEGRAM_TOKEN  = os.environ.get('TELEGRAM_TOKEN', '')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '')

TOP_N             = int(os.environ.get('LOBO_TOP_N',          '100'))
TIMEFRAME_1H      = os.environ.get('LOBO_TIMEFRAME_1H',     '1h')   # ← Principal (señal)
TIMEFRAME_4H      = os.environ.get('LOBO_TIMEFRAME_4H',     '4h')   # ← Confirmación
TIMEFRAME_15M     = os.environ.get('LOBO_TIMEFRAME_15M',    '15m')  # ← Microfractalidad

# === F1: Gestión de Capital en 3 Vectores ===
LOBO_LIQUIDEZ_PCT    = float(os.environ.get('LOBO_LIQUIDEZ_PCT', '20')) / 100
# LOBO_SPOT_PCT eliminado en v4 (solo futuros en este scope)
LOBO_FUTUROS_PCT     = float(os.environ.get('LOBO_FUTUROS_PCT', '80')) / 100
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
LOBO_RISK_PCT            = float(os.environ.get('LOBO_RISK_PCT', '5')) / 100  # 5%
LOBO_RISK_PCT_EXCEP      = float(os.environ.get('LOBO_RISK_PCT_EXCEP', '10')) / 100
LOBO_MAX_POSITIONS       = int(os.environ.get('LOBO_MAX_POSITIONS', '5'))

# TP/SL (F12: TPs basados en zonas reales)
LOBO_TP1_SIZE            = float(os.environ.get('LOBO_TP1_SIZE', '0.40'))
LOBO_TP2_SIZE            = float(os.environ.get('LOBO_TP2_SIZE', '0.30'))
LOBO_TP3_SIZE            = float(os.environ.get('LOBO_TP3_SIZE', '0.30'))
LOBO_TP2_ATR_MULT        = float(os.environ.get('LOBO_TP2_ATR_MULT', '2.5'))
LOBO_TP3_ATR_MULT        = float(os.environ.get('LOBO_TP3_ATR_MULT', '4.0'))
LOBO_TRAIL_ATR_MULT      = float(os.environ.get('LOBO_TRAIL_ATR_MULT', '1.0'))

# --- CIERRES PARCIALES 3 NIVELES (PnL-based) ---
PARTIAL_ENABLED    = True
TP1_CLOSE_PCT      = LOBO_TP1_SIZE   # 40% de la qty en TP1
TP2_CLOSE_PCT      = LOBO_TP2_SIZE   # 30% de la qty en TP2
# TP3 cierra el 30% restante (trailing o safety net)
MAX_SL_PCT         = float(os.environ.get('LOBO_MAX_SL_PCT', '0.030'))  # 3% max SL
SL_LOOKBACK        = int(os.environ.get('LOBO_SL_LOOKBACK', '20'))  # velas para SL

# --- TARGETS DE PNL FIJOS (sobre margin, sin importar leverage) ---
TP1_PNL_TARGET     = float(os.environ.get('LOBO_TP1_PNL_TARGET', '0.25'))  # 25% PnL en TP1
TP2_PNL_TARGET     = float(os.environ.get('LOBO_TP2_PNL_TARGET', '0.50'))  # 50% PnL en TP2
TP3_PNL_TARGET     = float(os.environ.get('LOBO_TP3_PNL_TARGET', '1.00'))  # 100% PnL safety net
RR_RATIO           = float(os.environ.get('LOBO_RR_RATIO', '2.5'))  # Solo para evaluación R:R mínimo

# F9: BE trigger ahora es "alcanzar TP1" (en vez de % fijo)
# Se usa TP1 como trigger, no un porcentaje independiente

# General
LOBO_TIMEOUT_HORAS       = float(os.environ.get('LOBO_TIMEOUT_HORAS', '96'))
LEVERAGE                 = float(os.environ.get('LOBO_LEVERAGE', '20.0'))
LOBO_SCORE_MIN           = int(os.environ.get('LOBO_SCORE_MIN', '14'))  # v5: subido de 12→14 (solo setups fuertes)
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

# === v4: Cobertura asimétrica CORREGIDA (manual BITLOBO) ===
LOBO_HEDGE_MARGIN_PCT    = float(os.environ.get('LOBO_HEDGE_MARGIN_PCT', '0.15'))  # 15% del margen principal

# === v4: CHOCH (Change of Character) ===
LOBO_CHOCH_LOOKBACK      = int(os.environ.get('LOBO_CHOCH_LOOKBACK', '30'))

# === v4: Microfractalidad (ondas 1H) ===
LOBO_MICRO_LOOKBACK_1H   = int(os.environ.get('LOBO_MICRO_LOOKBACK_1H', '72'))

# === v4: Flat Continuación ===
LOBO_FLAT_MIN_VELAS      = int(os.environ.get('LOBO_FLAT_MIN_VELAS', '3'))
LOBO_FLAT_MAX_ATR        = float(os.environ.get('LOBO_FLAT_MAX_ATR', '1.5'))

# === v4: BTC.D + Elliott ===
LOBO_BTCD_ELLOTT_LOOKBACK = int(os.environ.get('LOBO_BTCD_ELLOTT_LOOKBACK', '60'))

# === v4: D1 validación solo 00:00-00:05 UTC ===
LOBO_D1_CHECK_START      = int(os.environ.get('LOBO_D1_CHECK_START', '0'))

# === F5: RSI y Volumen ===
LOBO_RSI_PERIOD           = int(os.environ.get('LOBO_RSI_PERIOD', '14'))
LOBO_RSI_OVERSOLD         = float(os.environ.get('LOBO_RSI_OVERSOLD', '35'))
LOBO_RSI_OVERBOUGHT       = float(os.environ.get('LOBO_RSI_OVERBOUGHT', '65'))
LOBO_VOL_RATIO_MIN        = float(os.environ.get('LOBO_VOL_RATIO_MIN', '1.5'))
LOBO_VOL_PERIOD           = int(os.environ.get('LOBO_VOL_PERIOD', '20'))

log.info(
    "BITLOBO v4 Config: TOP=%d | Split Liq:%d%%/Fut:%d%% | "
    "Risk=%.1f%%(sobre %d%%) | SL=%.1fATR | MaxPos=%d | "
    "Hedge=%s(%.0fx trig=%.0f%%) | RSI[%.0f,%.0f] | "
    "ScoreMin=%d | Paper=%s",
    TOP_N,
    LOBO_LIQUIDEZ_PCT*100, LOBO_FUTUROS_PCT*100,
    LOBO_RISK_PCT*100, LOBO_FUTUROS_PCT*100, LOBO_SL_ATR, LOBO_MAX_POSITIONS,
    LOBO_HEDGE_ENABLED, LOBO_HEDGE_LEV_MULT, LOBO_HEDGE_TRIGGER_PCT*100,
    LOBO_RSI_OVERSOLD, LOBO_RSI_OVERBOUGHT,
    LOBO_SCORE_MIN, PAPER_TRADE,
)

# =====================================================================
# 5. INDICADORES BITLOBO v4 — CORREGIDOS Y EXTENDIDOS
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
    CORREGIDO: Detecta FVG (Fair Value Gap) en el proxy de USDT.D.
    Si el valor actual está en un FVG alcista no rellenado → resistencia activa.
    Cacheado 5 min. Usa proxy calculado de Bitget.
    """
    global DOMINANCE_CACHE, USDTD_HISTORY
    now = time.time()
    if now - DOMINANCE_CACHE['ts'] < DOMINANCE_CACHE_TTL and DOMINANCE_CACHE['usdtd'] is not None:
        return DOMINANCE_CACHE['usdtd']

    result = True
    proxy = None
    try:
        proxy = calcular_proxy_usdtd()
        if proxy is not None:
            # Almacenar en historial para detección de FVG
            USDTD_HISTORY.append((now, proxy))
            if len(USDTD_HISTORY) > 80:
                USDTD_HISTORY = USDTD_HISTORY[-80:]

            # Detectar FVG alcista (resistencia) en los valores proxy
            if len(USDTD_HISTORY) >= 15:
                vals = [v for _, v in USDTD_HISTORY]
                for i in range(2, len(vals) - 2):
                    # Gap alcista en USDT.D → precio saltó arriba = resistencia
                    gap_up = vals[i] - vals[i-2]
                    if gap_up > 0.5:  # gap mínimo 0.5% de dominancia
                        gap_alto = max(vals[i-2], vals[i])
                        gap_bajo = min(vals[i-2], vals[i])
                        # Verificar que NO se haya rellenado después
                        rellenado = any(gap_bajo <= vals[j] <= gap_alto for j in range(i+1, len(vals)))
                        if not rellenado:
                            # FVG alcista vigente = USDT.D en resistencia
                            if proxy >= gap_bajo * 0.99:
                                log.debug("USDT.D FVG resistencia: proxy=%.2f en gap [%.2f, %.2f]",
                                          proxy, gap_bajo, gap_alto)
                                DOMINANCE_CACHE['usdtd'] = True
                                DOMINANCE_CACHE['ts'] = now
                                return True

            # Fallback: percentil 85 de los últimos 30 valores
            vals = [v for _, v in USDTD_HISTORY[-30:]]
            if len(vals) >= 10:
                p85 = sorted(vals)[int(len(vals) * 0.85)]
                result = proxy >= p85 * 0.98
                log.debug("USDT.D proxy=%.2f p85=%.2f resistencia=%s", proxy, p85, result)
            else:
                result = proxy > 62.0
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
    # El bot solo llama a evaluar_senal_bitlobo_v4 cuando es_nueva_vela_1h es True,
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
    """v4: SPOT eliminado del scope de futuros. Retorna 0."""
    return 0.0

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
    # Mapear nombres de columnas (CCXT devuelve ['ts','o','h','l','c','v'])
    col_low = 'low' if 'low' in df_d1.columns else 'l'
    col_high = 'high' if 'high' in df_d1.columns else 'h'
    col_close = 'close' if 'close' in df_d1.columns else 'c'
    lows = df_d1[col_low].values
    highs = df_d1[col_high].values
    n = len(lows)
    swing_lows = []
    swing_highs = []
    for i in range(3, n - 3):
        if lows[i] == min(lows[i-3:i+4]):
            swing_lows.append((i, lows[i]))
        if highs[i] == max(highs[i-3:i+4]):
            swing_highs.append((i, highs[i]))
    ult_cierre = float(df_d1[col_close].iloc[-1])
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
def evaluar_cobertura_v4(pos_entry: dict, precio_actual: float) -> Optional[dict]:
    """
    v4 CORREGIDA: Cobertura ASIMÉTRICA manual BITLOBO.
    - Margen del hedge: 15% del margen principal (NO 100%)
    - Apalancamiento: MÁXIMO del activo (50X BTC, 20X alts, 10X otros)
    - TP del hedge = precio de liquidación del principal
    - SL del hedge = precio de entrada del principal
    """
    symbol = pos_entry.get('symbol', '')
    if HEDGE_ENTRIES.get(symbol):
        return None
    side = pos_entry.get('side', 'long')
    entry_price = float(pos_entry['entry_price'])
    sl_price = float(pos_entry.get('sl_price', 0))
    liq_price = float(pos_entry.get('liq_price', 0))
    main_margin = float(pos_entry.get('size_usdt', 0))
    if sl_price <= 0 or liq_price <= 0 or main_margin <= 0:
        return None
    # Distancia al SL
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
        return None
    # Dirección opuesta
    hedge_side = 'short' if side == 'long' else 'long'
    # v4: Margen = 15% del principal
    hedge_margin = main_margin * LOBO_HEDGE_MARGIN_PCT
    # v4: Leverage MÁXIMO del activo
    base = symbol.split('/')[0].replace(':USDT', '').strip()
    if base == 'BTC':
        hedge_lev = 50.0
    elif base in {'ETH', 'SOL', 'BNB', 'XRP', 'ADA', 'DOGE', 'AVAX', 'LINK', 'DOT', 'TRX', 'SHIB', 'UNI', 'ATOM', 'LTC'}:
        hedge_lev = 20.0
    else:
        hedge_lev = 10.0
    tp_price = liq_price  # TP del hedge = liquidación del principal
    sl_hedge = entry_price  # SL del hedge = entry del principal
    hedge_size_notional = hedge_margin * hedge_lev
    log.info("Hedge v4 %s: margin=%.2f(15%% de %.2f) lev=%.0fx tp=%.4f sl=%.4f",
             symbol, hedge_margin, main_margin, hedge_lev, tp_price, sl_hedge)
    return {
        'side': hedge_side,
        'leverage': hedge_lev,
        'tp_price': tp_price,
        'sl_price': sl_hedge,
        'margin_usdt': round(hedge_margin, 2),
        'size_usdt': round(hedge_size_notional, 2),
        'entry_price': precio_actual,
    }

# ================================================================
# F12: TPs en Zonas Reales (REEMPLAZADO — lógica RR-based de bot_v6)
# ================================================================
def calcular_tps_en_zonas(precio_actual: float, atr_val: float, fvg_list: list,
                          ob_list: list, es_long: bool,
                          leverage: float = LEVERAGE,
                          sl_price: float = 0.0) -> tuple[float, float, float, float, float]:
    """
    F12 PnL-BASED: Calcula TP1/TP2/TP3 basado en targets fijos de PnL sobre margin.

    Fórmula: TP_price = entry ± (entry × target_pnl / leverage)
    - TP1: 25% PnL → cierra 40% de la qty
    - TP2: 50% PnL → cierra 30% de la qty
    - TP3: 100% PnL → safety net, cierra el 30% restante

    Retorna (tp1_price, tp2_price, tp3_price, rr_ratio, dist_sl).
    """
    lev = leverage if leverage > 0 else LEVERAGE

    # Calcular distancia SL real (para R:R mínimo y dist_sl de retorno)
    if sl_price > 0:
        dist_sl = abs(precio_actual - sl_price)
    else:
        dist_sl = atr_val * LOBO_SL_ATR

    # --- TPs basados en PnL fijo sobre margin ---
    # PnL = (TP_price - entry) × qty = (TP_price - entry) × (margin × lev / entry)
    # Para PnL = target% × margin:
    #   target% × margin = (TP_price - entry) × (margin × lev / entry)
    #   TP_price - entry = (target% × entry) / lev
    sign = 1 if es_long else -1

    tp1_dist = (precio_actual * TP1_PNL_TARGET) / lev
    tp2_dist = (precio_actual * TP2_PNL_TARGET) / lev
    tp3_dist = (precio_actual * TP3_PNL_TARGET) / lev

    tp1 = precio_actual + sign * tp1_dist
    tp2 = precio_actual + sign * tp2_dist
    tp3 = precio_actual + sign * tp3_dist

    # Garantizar mínimo sobre ATR para mercados de muy bajo ruido
    min_dist = atr_val * 0.3
    if es_long:
        tp1 = max(tp1, precio_actual + min_dist)
        tp2 = max(tp2, tp1 + min_dist * 0.5)
        tp3 = max(tp3, tp2 + min_dist)
    else:
        tp1 = min(tp1, precio_actual - min_dist)
        tp2 = min(tp2, tp1 - min_dist * 0.5)
        tp3 = min(tp3, tp2 - min_dist)

    # R:R evaluación (basado en TP1 vs SL)
    rr = tp1_dist / dist_sl if dist_sl > 0 else 0

    return tp1, tp2, tp3, rr, dist_sl

# ================================================================
# F7: Timing de entrada (cierre de vela H1)
# ================================================================
def es_nueva_vela_1h(df_1h: pd.DataFrame) -> bool:
    """F7: True si la última vela H1 acaba de cerrar (< 5 minutos desde cierre)."""
    if df_1h.empty:
        return False
    ultimo_ts = df_1h['timestamp'].iloc[-1]
    ahora = int(time.time() * 1000)
    diff_ms = ahora - ultimo_ts
    # 1h = 3,600,000 ms + 5min buffer
    return diff_ms < 3_900_000

# =====================================================================
# F3: Apalancamiento dinámico (liq price calza con la mecha)
# =====================================================================
HIGH_LIQUIDITY_ALTS = {'ETH', 'SOL', 'BNB', 'XRP', 'ADA', 'DOGE', 'AVAX', 'LINK', 'DOT', 'MATIC', 'TRX', 'SHIB', 'UNI', 'ATOM', 'LTC'}

def calcular_apalancamiento_optimo(
    entry_price: float, df_h4: pd.DataFrame,
    zona_inf: float, zona_sup: float,
    es_long: bool, sweeps: list, symbol: str,
) -> tuple[float, float]:
    """
    F3 CORREGIDO: Apalancamiento dinámico.
    Calcula el apalancamiento para que el precio de liquidación forzosa
    calce JUSTO DEBAJO (long) / ENCIMA (short) de la mecha de absorción.
    
    - BTC: max 50X
    - Altcoins alta liquidez (HIGH_LIQUIDITY_ALTS): max 20X
    - Otros: max 10X
    
    Retorna (apalancamiento, liq_price).
    """
    # Determinar máximo apalancamiento según el activo
    base = symbol.split('/')[0].replace(':USDT', '').strip()
    if base == 'BTC':
        max_lev = 50.0
        log.debug("Apalancamiento: BTC -> max %.0fX", max_lev)
    elif base in HIGH_LIQUIDITY_ALTS:
        max_lev = 20.0
        log.debug("Apalancamiento: %s (alt alta liquidez) -> max %.0fX", base, max_lev)
    else:
        max_lev = 10.0
        log.debug("Apalancamiento: %s (otro) -> max %.0fX", base, max_lev)

    # Encontrar el nivel extremo de la mecha (últimas 5 velas)
    n_ultimas = min(8, len(df_h4))
    ultimas = df_h4.iloc[-n_ultimas:]

    if es_long:
        # Low más bajo de velas recientes (wick de absorción/sweep)
        nivel_extremo = float(ultimas['low'].min())
        # También revisar sweeps por si hay un nivel más bajo
        for s in sweeps:
            if s['tipo'] == 'sweep_bajista_long':
                nivel_extremo = min(nivel_extremo, s.get('nivel_roto', nivel_extremo))
        # Asegurar que está por debajo del entry
        if nivel_extremo >= entry_price:
            nivel_extremo = entry_price * 0.97
        # Target: 0.3% por debajo del extremo (colchón mínimo)
        target_liq = nivel_extremo * 0.997
        # Calcular apalancamiento: lev = 1 / (1 - liq/entry)
        ratio = target_liq / entry_price
        if ratio >= 1.0:
            lev_needed = max_lev
        else:
            lev_needed = 1.0 / (1.0 - ratio)
    else:
        # High más alto de velas recientes
        nivel_extremo = float(ultimas['high'].max())
        for s in sweeps:
            if s['tipo'] == 'sweep_alcista_short':
                nivel_extremo = max(nivel_extremo, s.get('nivel_roto', nivel_extremo))
        if nivel_extremo <= entry_price:
            nivel_extremo = entry_price * 1.03
        target_liq = nivel_extremo * 1.003
        ratio = target_liq / entry_price
        lev_needed = 1.0 / (ratio - 1.0)

    # Limitar al máximo permitido y mínimo 2X
    lev = min(max_lev, max(2.0, lev_needed))
    liq_price = calcular_precio_liquidacion(entry_price, lev, 'long' if es_long else 'short')

    log.debug("Apalancamiento óptimo: entry=%.4f extremo=%.4f target_liq=%.4f lev_needed=%.1f lev_final=%.1f",
              entry_price, nivel_extremo, target_liq, lev_needed, lev)

    return round(lev, 1), round(liq_price, 4)


# =====================================================================
# v4 — D2: EXPANDED FLAT / "DOUBLE KILL"
# =====================================================================
def detectar_expanded_flat(df_h4: pd.DataFrame, es_long: bool) -> dict:
    """
    D2: Patrón A-B-C donde C rompe A pero cierra con mecha (absorción).
    Long: A(min) → B(max) → C(nuevo min < A) con cierre > A.
    Short: A(max) → B(min) → C(nuevo max > A) con cierre < A.
    """
    left, right = 5, 5
    if len(df_h4) < left + right + 10:
        return {'encontrado': False, 'razon': 'pocos_datos'}
    highs = df_h4['high'].values
    lows = df_h4['low'].values
    closes = df_h4['close'].values
    opens = df_h4['open'].values
    n = len(highs)
    pivot_highs_idx = []
    pivot_lows_idx = []
    for i in range(left, n - right):
        if highs[i] == max(highs[max(0, i-left):i+right+1]):
            pivot_highs_idx.append(i)
        if lows[i] == min(lows[max(0, i-left):i+right+1]):
            pivot_lows_idx.append(i)
    if len(pivot_highs_idx) < 2 or len(pivot_lows_idx) < 2:
        return {'encontrado': False, 'razon': 'pocos_pivots'}
    if es_long:
        for i_a in range(len(pivot_lows_idx)):
            idx_a = pivot_lows_idx[i_a]
            level_a = lows[idx_a]
            for i_b in range(i_a + 1, min(i_a + 4, len(pivot_highs_idx))):
                idx_b = pivot_highs_idx[i_b]
                if idx_b <= idx_a:
                    continue
                level_b = highs[idx_b]
                if level_b <= level_a:
                    continue
                for i_c in range(i_b + 1, min(i_b + 4, len(pivot_lows_idx))):
                    idx_c = pivot_lows_idx[i_c]
                    if idx_c <= idx_b:
                        continue
                    level_c = lows[idx_c]
                    if level_c < level_a:
                        vela_c_range = highs[idx_c] - lows[idx_c]
                        if vela_c_range > 0:
                            mecha_inf = min(opens[idx_c], closes[idx_c]) - lows[idx_c]
                            ratio_mecha = mecha_inf / vela_c_range
                            if ratio_mecha >= 0.15:
                                return {
                                    'encontrado': True,
                                    'tipo': 'exp_flat_long',
                                    'nivel_a': float(level_a),
                                    'nivel_c': float(level_c),
                                    'nivel_b': float(level_b),
                                    'distancia_ab': round((level_b - level_a) / level_a * 100, 2),
                                    'mecha_c_ratio': round(ratio_mecha, 2),
                                }
    else:
        for i_a in range(len(pivot_highs_idx)):
            idx_a = pivot_highs_idx[i_a]
            level_a = highs[idx_a]
            for i_b in range(i_a + 1, min(i_a + 4, len(pivot_lows_idx))):
                idx_b = pivot_lows_idx[i_b]
                if idx_b <= idx_a:
                    continue
                level_b = lows[idx_b]
                if level_b >= level_a:
                    continue
                for i_c in range(i_b + 1, min(i_b + 4, len(pivot_highs_idx))):
                    idx_c = pivot_highs_idx[i_c]
                    if idx_c <= idx_b:
                        continue
                    level_c = highs[idx_c]
                    if level_c > level_a:
                        vela_c_range = highs[idx_c] - lows[idx_c]
                        if vela_c_range > 0:
                            mecha_sup = highs[idx_c] - max(opens[idx_c], closes[idx_c])
                            ratio_mecha = mecha_sup / vela_c_range
                            if ratio_mecha >= 0.15:
                                return {
                                    'encontrado': True,
                                    'tipo': 'exp_flat_short',
                                    'nivel_a': float(level_a),
                                    'nivel_c': float(level_c),
                                    'nivel_b': float(level_b),
                                    'distancia_ab': round((level_a - level_b) / level_a * 100, 2),
                                    'mecha_c_ratio': round(ratio_mecha, 2),
                                }
    return {'encontrado': False}


# =====================================================================
# v4 — D3: CHOCH (Change of Character)
# =====================================================================
def detectar_choch(df_h4: pd.DataFrame, es_long: bool) -> dict:
    """
    D3: Quiebre de estructura — en tendencia bajista, CHOCH cuando
    precio cierra sobre el último lower high (y viceversa).
    """
    if len(df_h4) < LOBO_CHOCH_LOOKBACK:
        return {'choch': False, 'razon': 'pocos_datos'}
    left, right = 3, 3
    highs = df_h4['high'].values
    lows = df_h4['low'].values
    closes = df_h4['close'].values
    n = len(highs)
    pivot_highs_idx = []
    pivot_lows_idx = []
    for i in range(left, n - right):
        if highs[i] == max(highs[max(0, i-left):i+right+1]):
            pivot_highs_idx.append(i)
        if lows[i] == min(lows[max(0, i-left):i+right+1]):
            pivot_lows_idx.append(i)
    if len(pivot_highs_idx) < 3 or len(pivot_lows_idx) < 2:
        return {'choch': False, 'razon': 'pocos_pivots'}
    if es_long:
        ultimos_highs = [(i, highs[i]) for i in pivot_highs_idx[-4:]]
        if len(ultimos_highs) < 3:
            return {'choch': False}
        lh_count = sum(1 for j in range(len(ultimos_highs)-1) if ultimos_highs[j][1] > ultimos_highs[j+1][1])
        if lh_count < 2:
            return {'choch': False, 'razon': 'sin_lower_highs'}
        nivel_choch = ultimos_highs[-1][1]
        if closes[-1] > nivel_choch:
            body = closes[-1] - df_h4['open'].iloc[-1]
            rango = highs[-1] - lows[-1]
            if rango > 0 and body / rango > 0.3:
                return {'choch': True, 'tipo': 'bullish_choch', 'nivel_roto': float(nivel_choch), 'pullback_confirmado': False}
    else:
        ultimos_lows = [(i, lows[i]) for i in pivot_lows_idx[-4:]]
        if len(ultimos_lows) < 3:
            return {'choch': False}
        hl_count = sum(1 for j in range(len(ultimos_lows)-1) if ultimos_lows[j][1] < ultimos_lows[j+1][1])
        if hl_count < 2:
            return {'choch': False, 'razon': 'sin_higher_lows'}
        nivel_choch = ultimos_lows[-1][1]
        if closes[-1] < nivel_choch:
            body = df_h4['open'].iloc[-1] - closes[-1]
            rango = highs[-1] - lows[-1]
            if rango > 0 and body / rango > 0.3:
                return {'choch': True, 'tipo': 'bearish_choch', 'nivel_roto': float(nivel_choch), 'pullback_confirmado': False}
    return {'choch': False}


# =====================================================================
# v4 — D4: MICROFRACTALIDAD (ondas en 1H)
# =====================================================================
def verificar_microfractalidad(df_1h: pd.DataFrame) -> dict:
    """
    D4: Detecta estructura de 5+ ondas en 1H para confirmar giro.
    """
    if len(df_1h) < 30:
        return {'completo': False, 'razon': 'pocos_datos'}
    left, right = 3, 3
    highs = df_1h['high'].values
    lows = df_1h['low'].values
    n = len(highs)
    pivot_highs_idx = []
    pivot_lows_idx = []
    for i in range(left, n - right):
        if highs[i] == max(highs[max(0, i-left):i+right+1]):
            pivot_highs_idx.append(i)
        if lows[i] == min(lows[max(0, i-left):i+right+1]):
            pivot_lows_idx.append(i)
    pivots = sorted(
        [(i, 'high', highs[i]) for i in pivot_highs_idx[-8:]] +
        [(i, 'low', lows[i]) for i in pivot_lows_idx[-8:]],
        key=lambda x: x[0]
    )
    if len(pivots) < 5:
        return {'completo': False, 'razon': 'pocos_pivots'}
    ondas = 1
    for j in range(1, len(pivots)):
        if pivots[j][1] != pivots[j-1][1]:
            ondas += 1
        else:
            break
    if ondas >= 5:
        primer_pivot = pivots[0][2]
        ultimo_pivot = pivots[-1][2]
        if ultimo_pivot > primer_pivot:
            tipo = 'impulsivo_alcista'
        elif ultimo_pivot < primer_pivot:
            tipo = 'impulsivo_bajista'
        else:
            tipo = 'zigzag'
        return {'completo': True, 'ondas': ondas, 'tipo': tipo}
    return {'completo': False, 'ondas': ondas}


# =====================================================================
# v4 — D5: PLANA DE CONTINUACION
# =====================================================================
def detectar_flat_continuacion(df_h4: pd.DataFrame, es_long: bool) -> bool:
    """
    D5: Ruptura de estructura + consolidación lateral sin nuevos extremos.
    """
    if len(df_h4) < 15:
        return False
    n = len(df_h4)
    atr_vals = _atr(df_h4, LOBO_ATR_PERIOD)
    lookback = min(20, n - LOBO_FLAT_MIN_VELAS - 5)
    zone = df_h4.iloc[-(lookback + LOBO_FLAT_MIN_VELAS):-LOBO_FLAT_MIN_VELAS]
    current = df_h4.iloc[-LOBO_FLAT_MIN_VELAS:]
    if len(zone) < 5 or len(current) < LOBO_FLAT_MIN_VELAS:
        return False
    atr_avg = atr_vals.iloc[-LOBO_FLAT_MIN_VELAS:].mean()
    if pd.isna(atr_avg) or atr_avg <= 0:
        return False
    if es_long:
        resistencia = zone['high'].iloc[:-1].max()
        rupture_velas = zone[zone['close'] > resistencia]
        if rupture_velas.empty:
            return False
        min_rupture = rupture_velas['low'].min()
        for _, vela in current.iterrows():
            if vela['low'] < min_rupture * 0.995:
                return False
        rango_actual = current['high'].max() - current['low'].min()
        if rango_actual < atr_avg * LOBO_FLAT_MAX_ATR:
            return True
    else:
        soporte = zone['low'].iloc[:-1].min()
        rupture_velas = zone[zone['close'] < soporte]
        if rupture_velas.empty:
            return False
        max_rupture = rupture_velas['high'].max()
        for _, vela in current.iterrows():
            if vela['high'] > max_rupture * 1.005:
                return False
        rango_actual = current['high'].max() - current['low'].min()
        if rango_actual < atr_avg * LOBO_FLAT_MAX_ATR:
            return True
    return False


# =====================================================================
# v4 — D8: BTC.D + Elliott (ventana altcoins)
# =====================================================================
def check_btcd_elliott_ventana_altcoins(df_btcd_4h: Optional[pd.DataFrame] = None) -> dict:
    """
    D8: BTC.D bajando + 5 ondas bajistas completas en H4 → ventana altcoins.
    """
    result = {'ventana_altcoins': False, 'btcd_bajista': False, 'elliott_completo': False}
    btcd_subiendo = check_dominancia_btc_long()
    if btcd_subiendo:
        return result
    result['btcd_bajista'] = True
    if df_btcd_4h is not None and len(df_btcd_4h) >= LOBO_BTCD_ELLOTT_LOOKBACK:
        elliott = detectar_estructura_elliott_v3(df_btcd_4h)
        if elliott.get('fase') == 'estructura_5_ondas' and elliott.get('direccion') == 'bajista':
            result['elliott_completo'] = True
    result['ventana_altcoins'] = True
    return result


# =====================================================================
# v4 — D9: INVALIDACION H4 STRUCTURAL (cada 4h)
# =====================================================================
def debe_validar_h4() -> bool:
    """D9: Solo valida estructura H4 en los 5 min posteriores al cierre de vela H4."""
    now_utc = datetime.utcnow()
    # H4 cierra cada 4h: 00, 04, 08, 12, 16, 20 UTC
    return now_utc.hour % 4 == 0 and now_utc.minute <= 5


# =====================================================================
# 6. EVALUACIÓN COMPLETA DE SEÑAL (v4 con todas las correcciones)
# =====================================================================
def evaluar_senal_bitlobo_v4(
    symbol: str, df_h4: pd.DataFrame, df_d1: pd.DataFrame,
    precio_actual: float, atr_val: float, balance_total: float,
    es_long: bool, df_1h: Optional[pd.DataFrame] = None,
    ventana_altcoins: Optional[dict] = None,
) -> Optional[dict]:
    """
    v4: Evalúa TODAS las reglas BITLOBO con mejoras D2-D9.
    Diferencias vs v3:
      - Score max: 22 (era 16)
      - R:R mínimo: 1.5 (era 1.0)
      - Nuevos: CHOCH, Expanded Flat, Microfractalidad, Flat Continuación
      - BTC.D usa Elliott para ventana altcoins
    """
    capital_fut = capital_disponible_futuros(balance_total)
    senal = {'symbol': symbol, 'precio_actual': precio_actual, 'atr_val': atr_val, 'es_long': es_long}
    detalles = []
    score = 0
    max_score = 22

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
        return None
    if zona_inf <= precio_actual <= zona_sup:
        score += 1
        detalles.append('R1:en_OTE')

    # --- R2: SMA 100 en zona OTE ---
    if len(df_h4) >= 100:
        sma100 = _sma(df_h4['close'], 100).iloc[-1]
        if not pd.isna(sma100) and sma100_en_zona_ote(sma100, fibo, atr_val):
            score += 1
            detalles.append('R2:SMA100_en_OTE')

    # --- R3: ADX ---
    if adx_permite_entrada(df_h4):
        score += 1
        detalles.append('R3:ADX_ok')

    # --- R4: USDT.D ---
    if es_long:
        if check_usdtd_resistencia_long():
            score += 1
            detalles.append('R4:USDT.D_resistencia')
    else:
        score += 1
        detalles.append('R4:Short_ok')

    # --- R5: BTC.D con Elliott (D8) ---
    if ventana_altcoins:
        if not (ventana_altcoins.get('btcd_bajista', False) and 'BTC' not in symbol):
            score += 1
            detalles.append('R5:BTC.D_ok')
        elif 'BTC' in symbol and ventana_altcoins.get('btcd_bajista', False):
            score += 1
            detalles.append('R5:BTC_fav')
        else:
            detalles.append('R5:BTC.D_bloquea_alt')
    else:
        btcd_subiendo = check_dominancia_btc_long()
        if not (btcd_subiendo and 'BTC' not in symbol):
            score += 1
            detalles.append(f'R5:BTC.D_{"sube" if btcd_subiendo else "baja"}')

    # --- R6: FVG ---
    fvgs = detectar_fvg(df_h4)
    fvg_en_zona = [f for f in fvgs if f['gap_sup'] >= zona_inf and f['gap_inf'] <= zona_sup]
    senal['fvgs'] = fvg_en_zona
    if fvg_en_zona:
        score += 1
        detalles.append(f'R6:FVG_{len(fvg_en_zona)}')

    # --- R7: Order Block ---
    obs = detectar_order_blocks(df_h4)
    ob_en_zona = [o for o in obs if o['low'] <= zona_sup and o['high'] >= zona_inf]
    senal['obs'] = ob_en_zona
    if ob_en_zona:
        score += 1
        detalles.append(f'R7:OB_{len(ob_en_zona)}')

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
            detalles.append('R8:Sweep')

    # --- R9: Mecha/Absorción ---
    mecha_ok, mecha_det = validar_mecha_absorcion_en_zona(df_h4, zona_inf, zona_sup, es_long, atr_val)
    if not mecha_ok:
        return None
    score += 1
    detalles.append(f'R9:Mecha_{mecha_det}')

    # --- F5: RSI ---
    rsi_ok, rsi_val = filtro_rsi(df_h4, es_long)
    if rsi_ok:
        score += 1
        detalles.append(f'F5:RSI_{rsi_val:.0f}')

    # --- F5: Volumen ---
    vol_ok, vol_ratio = validar_volumen(df_h4, es_long)
    if vol_ok:
        score += 1
        detalles.append(f'F5:Vol_{vol_ratio:.1f}x')

    # --- F6: Pullback ---
    nivel_ref = zona_sup if es_long else zona_inf
    pullback_ok = detectar_pullback_confirmado(df_h4, nivel_ref, es_long)
    if pullback_ok:
        score += 1
        detalles.append('F6:Pullback_ok')

    # --- F11: Elliott ---
    elliott = detectar_estructura_elliott_v3(df_h4)
    senal['elliott'] = elliott
    if elliott['fase'] == 'estructura_5_ondas':
        score += 1
        detalles.append('F11:Elliott_5ondas')

    # --- D3: CHOCH ---
    choch = detectar_choch(df_h4, es_long)
    senal['choch'] = choch
    if choch.get('choch', False):
        score += 1
        detalles.append(f'D3:{choch["tipo"]}')

    # --- D2: Expanded Flat / Double Kill (+2 pts) ---
    exp_flat = detectar_expanded_flat(df_h4, es_long)
    senal['expanded_flat'] = exp_flat
    if exp_flat.get('encontrado', False):
        score += 2
        detalles.append(f'D2:DoubleKill_{exp_flat["tipo"]}')

    # --- D4: Microfractalidad ---
    if df_1h is not None and len(df_1h) > 0:
        micro = verificar_microfractalidad(df_1h)
        senal['microfractal'] = micro
        if micro.get('completo', False):
            if (es_long and micro.get('tipo') == 'impulsivo_alcista') or \
               (not es_long and micro.get('tipo') == 'impulsivo_bajista'):
                score += 1
                detalles.append(f'D4:micro_{micro["tipo"]}')

    # --- D5: Flat Continuación ---
    flat_cont = detectar_flat_continuacion(df_h4, es_long)
    if flat_cont:
        score += 1
        detalles.append('D5:flat_continuacion')

    # --- F10: Validación D1 ---
    if validar_estructura_d1(df_d1, precio_actual, 'long' if es_long else 'short'):
        score += 1
        detalles.append('F10:D1_ok')
    else:
        return None

    # --- F3: Apalancamiento dinámico ---
    apalancamiento, liq_price = calcular_apalancamiento_optimo(
        precio_actual, df_h4, zona_inf, zona_sup, es_long, sweeps, symbol,
    )

    # --- SL ---
    sl_mult = LOBO_SL_ATR
    sl_price = precio_actual - (atr_val * sl_mult) if es_long else precio_actual + (atr_val * sl_mult)
    senal['sl_price'] = sl_price

    # --- Safety: liq_price debe quedar más allá del SL (buffer de 1 ATR) ---
    if es_long:
        liq_min = sl_price - atr_val * 1.0  # liq 1 ATR por debajo de SL
        if liq_price >= sl_price:
            liq_price = liq_min  # Recalcular liq para que esté más abajo
    else:
        liq_max = sl_price + atr_val * 1.0  # liq 1 ATR por encima de SL
        if liq_price <= sl_price:
            liq_price = liq_max

    # --- F12: TPs PnL-based (targets fijos sobre margin) ---
    tp1_price, tp2_price, tp3_price, rr, dist_sl = calcular_tps_en_zonas(
        precio_actual, atr_val, fvg_en_zona, ob_en_zona, es_long,
        leverage=apalancamiento, sl_price=sl_price,
    )
    senal['tp1_price'] = tp1_price
    senal['tp2_price'] = tp2_price
    senal['tp3_price'] = tp3_price
    senal['rr'] = rr
    senal['dist_sl'] = dist_sl

    # v4: R:R mínimo 1.5:1 (subido desde 1.0)
    if rr < 1.5:
        return None
    if rr >= 1.5:
        score += 1
        detalles.append(f'R13:R:R_{rr:.2f}')

    # --- Position Sizing ---
    riesgo_capital = capital_fut * LOBO_RISK_PCT
    distancia_sl = abs(precio_actual - sl_price) / precio_actual
    if distancia_sl <= 0:
        return None
    pos_value = riesgo_capital / distancia_sl
    if pos_value < MIN_ORDER_USDT:
        pos_value = MIN_ORDER_USDT
    qty = pos_value / precio_actual
    margin_real = pos_value / apalancamiento if apalancamiento > 0 else 0

    senal['qty'] = qty
    senal['pos_value'] = pos_value
    senal['liq_price'] = liq_price
    senal['size_usdt'] = margin_real
    senal['leverage_calculado'] = apalancamiento
    senal['riesgo_real_pct'] = round((pos_value * distancia_sl) / capital_fut * 100, 2)
    score += 1
    detalles.append(f'F3:lev{apalancamiento:.0f}x_mrg{margin_real:.2f}')

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
# 8. CSV LOGGING (adaptado a v4)
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
        ohlcv_1h  = await exch.fetch_ohlcv(symbol, timeframe=TIMEFRAME_1H,  limit=200)  # Principal
        ohlcv_4h  = await exch.fetch_ohlcv(symbol, timeframe=TIMEFRAME_4H,  limit=100)  # Confirmación
        ohlcv_15m = await exch.fetch_ohlcv(symbol, timeframe=TIMEFRAME_15M, limit=200)  # Micro
        return symbol, ohlcv_1h, ohlcv_4h, ohlcv_15m
    except Exception:
        return symbol, None, None, None

async def fetch_all_ohlcv(symbols):
    exch = ccxt_async.bitget({
        'apiKey': API_KEY, 'secret': SECRET_KEY, 'password': PASSPHRASE,
        'enableRateLimit': True, 'options': {'defaultType': 'swap'},
    })
    try:
        results = await asyncio.gather(*[_fetch_symbol_async(exch, s) for s in symbols])
    finally:
        await exch.close()
    return {r[0]: (r[1], r[2], r[3]) for r in results}

# =====================================================================
# 10. EXCHANGE — CONEXIÓN Y ÓRDENES
# =====================================================================
exchange: ccxt.bitget | None = None

def init_exchange() -> bool:
    global exchange
    if PAPER_TRADE:
        log.info("PAPER_TRADE v4 activo")
        try:
            exchange = ccxt.bitget({'enableRateLimit': True, 'options': {'defaultType': 'swap'}})
            exchange.load_markets()
            log.info("Exchange paper v4 listo (%d mercados)", len(exchange.markets))
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
        log.info("Conexion Bitget v4 exitosa")
        return True
    except Exception as e:
        log.critical("Error conectando Bitget: %s", e)
        return False

# =====================================================================
# 10b. TAKE PROFIT PLAN ORDERS (extraído de bot_v6)
# =====================================================================
def _place_tp_plan(sym: str, tp_price: float, tp_qty: float, side: str) -> bool:
    """Coloca una take-profit plan order vía API directa (hedge mode)."""
    if not exchange or PAPER_TRADE:
        return False
    try:
        market_info = exchange.market(sym)
        hold_side = side  # 'long' o 'short' (hedge mode)
        params = {
            'marginCoin': market_info['settleId'],
            'productType': 'usdt-futures',
            'symbol': market_info['id'].lower(),
            'planType': 'profit_plan',
            'triggerPrice': exchange.price_to_precision(sym, tp_price),
            'triggerType': 'mark_price',
            'holdSide': hold_side,
            'size': exchange.amount_to_precision(sym, tp_qty),
        }
        exchange.privateMixPostV2MixOrderPlaceTpslOrder(params)
        return True
    except Exception as e:
        err = str(e)
        if '43030' in err:
            log.info("TP plan ya existe %s @ %s: %s", sym, tp_price, e)
        else:
            log.error("Error colocando plan order %s @ %s: %s", sym, tp_price, e)
        return False

def _cancel_tp_plans(sym: str):
    """Cancela todos los profit_plan activos de un símbolo."""
    if not exchange or PAPER_TRADE:
        return
    try:
        market_info = exchange.market(sym)
        params = {
            'productType': 'usdt-futures',
            'symbol': market_info['id'].lower(),
            'planType': 'profit_plan',
        }
        pending = exchange.privateMixGetV2MixOrderOrdersPending(params)
        for plan in (pending.get('data', {}).get('entrustedList', []) or []):
            if plan.get('planType') == 'profit_plan':
                exchange.privateMixPostV2MixOrderCancelTpslOrder({
                    'symbol': market_info['id'].lower(),
                    'productType': 'usdt-futures',
                    'marginCoin': market_info['settleId'],
                    'planType': 'profit_plan',
                    'orderId': plan['orderId'],
                })
                log.info("Cancelado TP plan %s orderId=%s", sym, plan['orderId'])
    except Exception as e:
        log.warning("Error cancelando TP plans %s: %s", sym, e)

def restaurar_tp_exchange():
    """Coloca TP1/TP2/Full en exchange (plan orders) para posiciones abiertas post-reinicio."""
    if not exchange or PAPER_TRADE:
        return
    try:
        positions = exchange.fetch_positions()
        for pos in positions:
            sym = pos['symbol']
            if float(pos['contracts']) == 0:
                continue
            if sym not in TRADE_ENTRIES:
                continue
            ed = TRADE_ENTRIES[sym]
            side = ed.get('side', 'long')
            ep = float(ed['entry_price'])
            step = ed.get('step', 0)
            tp1_p = float(ed.get('tp1_price', 0))
            tp2_p = float(ed.get('tp2_price', 0))
            tp_full = float(ed.get('tp3_price', 0))
            original_qty = float(ed.get('original_qty', ed.get('quantity', 0)))
            cur_qty = float(pos['contracts'])
            if tp1_p == ep or tp2_p == ep or tp_full == ep or step <= 0:
                continue

            _cancel_tp_plans(sym)

            # TP1 si aún no se ejecutó
            tp1_qty = ((original_qty * TP1_CLOSE_PCT) // step) * step
            if cur_qty >= original_qty * 0.85 and tp1_qty >= step:
                notional = tp1_qty * tp1_p
                if notional >= 5:
                    if _place_tp_plan(sym, tp1_p, tp1_qty, side):
                        log.info("%s TP1 plan restaurado: %s @ %s", sym, tp1_qty, tp1_p)

            # TP2 si aún no se ejecutó
            remaining_after_tp1 = original_qty - tp1_qty
            tp2_qty = ((remaining_after_tp1 * TP2_CLOSE_PCT / (1 - TP1_CLOSE_PCT)) // step) * step
            if cur_qty >= original_qty * 0.45 and tp2_qty >= step:
                notional = tp2_qty * tp2_p
                if notional >= 5:
                    if _place_tp_plan(sym, tp2_p, tp2_qty, side):
                        log.info("%s TP2 plan restaurado: %s @ %s", sym, tp2_qty, tp2_p)

            # Full TP (restante)
            full_qty = original_qty - tp1_qty - tp2_qty
            if cur_qty >= original_qty * 0.15 and full_qty >= step:
                notional = full_qty * tp_full
                if notional >= 5:
                    if _place_tp_plan(sym, tp_full, full_qty, side):
                        log.info("%s Full TP plan restaurado: %s @ %s", sym, full_qty, tp_full)
    except Exception as e:
        log.error("Error en restaurar_tp_exchange: %s", e)

# =====================================================================
# 11. GESTIÓN DE POSICIONES v4 (con SL por liquidación, BE, trailing, coberturas)
# =====================================================================
def _full_cleanup(symbol: str, cooldown: int = 3600):
    """Limpia todos los rastros de una posición cerrada.
    
    Args:
        symbol: símbolo a limpiar
        cooldown: segundos de cooldown antes de re-entrar (default 1h, D1 usa 7200)
    """
    TRADE_ENTRIES.pop(symbol, None)
    HEDGE_ENTRIES.pop(symbol, None)
    _save_trade_entries()
    SESSION_ACTIVE_SYMBOLS.discard(symbol)
    COOLDOWNS[symbol] = time.time() + cooldown
    PEAK_PRICES.pop(symbol, None)
    ADVERSE_PRICES.pop(symbol, None)
    # Limpiar TODAS las claves de ALERTS_HISTORY que contengan el símbolo
    keys_to_remove = [k for k in ALERTS_HISTORY if symbol in k]
    for k in keys_to_remove:
        ALERTS_HISTORY.pop(k, None)
    TRAIL_COUNTS.pop(symbol, None)
    PARTIAL_LEVEL.pop(symbol, None)
    _save_partial_level()
    # Cancelar TP plan orders en exchange
    _cancel_tp_plans(symbol)

def _manage_paper_positions_v3(balance_total: float):
    """Gestiona posiciones simuladas en paper mode con TODAS las reglas v4."""
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

            # --- F10: Validación H4 estructural (v4: cada 4h en cierre de vela) ---
            if debe_validar_h4():
                try:
                    ohlcv_4h_val = exchange.fetch_ohlcv(symbol, timeframe='4h', limit=30)
                    if len(ohlcv_4h_val) >= 10:
                        df_4h_val = pd.DataFrame(ohlcv_4h_val, columns=['ts','o','h','l','c','v'])
                        if not validar_estructura_d1(df_4h_val, entry_price, side):
                            log.info("[PAPER v4] %s: H4 invalida estructura - cerrando", symbol)
                            remaining_qty = entry.get('remaining_qty', entry['quantity'])
                            pnl = 0.0
                            if side == 'long':
                                pnl = (mark - entry_price) * remaining_qty
                            else:
                                pnl = (entry_price - mark) * remaining_qty
                            guardar_trade_csv(entry, mark, pnl, 0, pnl, 'D1_INVALID', 'd1_estructura')
                            _full_cleanup(symbol, cooldown=7200)
                            send_telegram(f"[PAPER v4] *{symbol}* Cerrada por D1 estructura")
                            continue
                except Exception:
                    pass

            # --- F4: Evaluar cobertura asimétrica v4 ---
            if LOBO_HEDGE_ENABLED and symbol not in HEDGE_ENTRIES:
                hedge_params = evaluar_cobertura_v4(entry, mark)
                if hedge_params:
                    log.info("[PAPER v4] %s: Activando cobertura %s lev=%.0fx tp=%.4f",
                             symbol, hedge_params['side'], hedge_params['leverage'], hedge_params['tp_price'])
                    HEDGE_ENTRIES[symbol] = hedge_params
                    send_telegram(f"[PAPER v4] *{symbol}* Cobertura {hedge_params['side']} activada")

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
                    log.info("[PAPER v4] %s: Cobertura TP alcanzado! PnL=%.2f", symbol, pnl_hedge)
                    HEDGE_ENTRIES.pop(symbol, None)
                elif hedge_side == 'long' and mark >= hedge_tp:
                    pnl_hedge = hedge.get('size_usdt', 0) * hedge_lev * \
                                ((mark - hedge['entry_price']) / hedge['entry_price'])
                    log.info("[PAPER v4] %s: Cobertura TP alcanzado! PnL=%.2f", symbol, pnl_hedge)
                    HEDGE_ENTRIES.pop(symbol, None)
                # Si la cobertura alcanza SL
                if hedge_side == 'short' and mark >= hedge_sl:
                    HEDGE_ENTRIES.pop(symbol, None)
                elif hedge_side == 'long' and mark <= hedge_sl:
                    HEDGE_ENTRIES.pop(symbol, None)

            # --- TP PARCIAL + BE (lógica bot_v6: PARTIAL_LEVEL 0→1→2) ---
            long_side = side == 'long'
            short_side = side == 'short'
            original_qty = float(entry.get('original_qty', entry.get('quantity', 0)))
            remaining_qty = float(entry.get('remaining_qty', entry.get('quantity', 0)))
            step_p = float(entry.get('step', 0))
            partial_lvl = PARTIAL_LEVEL.get(symbol, 0)
            lev = float(entry.get('leverage', LEVERAGE))

            def _pnl_parcial(qty_sold: float, exit_px: float) -> float:
                """Calcula PnL para una venta parcial."""
                if side == 'long':
                    return (exit_px - entry_price) * qty_sold
                else:
                    return (entry_price - exit_px) * qty_sold

            sl_hit = (long_side and mark <= sl_price) or (short_side and mark >= sl_price)
            liq_hit = (long_side and mark <= liq_price) or (short_side and mark >= liq_price)
            tp_full_hit = (long_side and mark >= tp3_price) or (short_side and mark <= tp3_price)

            # ── SL / LIQ: cierre completo de lo que quede ──
            if sl_hit or liq_hit:
                if remaining_qty > 0:
                    pnl = _pnl_parcial(remaining_qty, mark)
                    status = 'SL' if sl_hit else 'LIQ'
                    reason = 'sl' if sl_hit else 'liquidacion'
                    log.info("[PAPER] %s %s | Entry=%.4f Exit=%.4f Qty=%.4f PnL=%.2f",
                             symbol, status, entry_price, mark, remaining_qty, pnl)
                    guardar_trade_csv(entry, mark, pnl, 0, pnl, status, reason)
                    send_telegram(f"[PAPER] *{symbol} {status}*\nPnL: {pnl:.2f} USDT ({pnl/(entry.get('size_usdt',1)*lev)*100:.2f}%)")
                _full_cleanup(symbol)
                continue

            # ── Full TP (TP3): cierre completo del remanente ──
            if tp_full_hit:
                if remaining_qty > 0:
                    pnl = _pnl_parcial(remaining_qty, tp3_price)
                    log.info("[PAPER] %s TP3 FULL | Entry=%.4f Exit=%.4f Qty=%.4f PnL=%.2f",
                             symbol, entry_price, tp3_price, remaining_qty, pnl)
                    guardar_trade_csv(entry, tp3_price, pnl, 0, pnl, 'TP3', 'tp3')
                    send_telegram(f"[PAPER] *{symbol} TP3 FULL*\nPnL: {pnl:.2f} USDT")
                _full_cleanup(symbol)
                continue

            # ── TP1: parcial 40% + BE (nivel 0→1) ──
            if partial_lvl == 0 and step_p > 0 and remaining_qty >= step_p:
                tp1_price = float(entry.get('tp1_price', 0))
                if tp1_price != entry_price:
                    tp1_reached = (long_side and mark >= tp1_price) or (short_side and mark <= tp1_price)
                    if tp1_reached:
                        tp1_qty = ((original_qty * TP1_CLOSE_PCT) // step_p) * step_p
                        tp1_qty = min(tp1_qty, remaining_qty - step_p)  # Reservar al menos 1 step
                        if tp1_qty >= step_p:
                            pnl = _pnl_parcial(tp1_qty, tp1_price)
                            entry['remaining_qty'] = remaining_qty - tp1_qty
                            PARTIAL_LEVEL[symbol] = 1
                            ALERTS_HISTORY[f"{symbol}_tp1_sold"] = True
                            ALERTS_HISTORY[f"{symbol}_be_price"] = entry_price
                            entry['sl_price'] = entry_price  # Break Even
                            log.info("[PAPER] %s TP1 (40%%)+BE | Entry=%.4f Exit=%.4f Qty=%.4f PnL=%.2f | Restan=%.4f",
                                     symbol, entry_price, tp1_price, tp1_qty, pnl, entry['remaining_qty'])
                            guardar_trade_csv(entry, tp1_price, pnl, 0, pnl, 'TP1_PARTIAL', 'tp1')
                            send_telegram(f"[PAPER] *{symbol} TP1 (40%)+BE*\nPnL: {pnl:.2f} USDT | SL→Entry")
                            _save_trade_entries()
                            _save_partial_level()

            # ── TP2: parcial 30% (nivel 1→2) ──
            elif partial_lvl == 1 and step_p > 0 and remaining_qty >= step_p:
                tp2_price = float(entry.get('tp2_price', 0))
                if tp2_price != entry_price:
                    tp2_reached = (long_side and mark >= tp2_price) or (short_side and mark <= tp2_price)
                    if tp2_reached:
                        remaining_after_tp1 = original_qty - ((original_qty * TP1_CLOSE_PCT) // step_p) * step_p
                        tp2_qty = ((remaining_after_tp1 * TP2_CLOSE_PCT / (1 - TP1_CLOSE_PCT)) // step_p) * step_p
                        tp2_qty = min(tp2_qty, remaining_qty - step_p)
                        if tp2_qty >= step_p:
                            pnl = _pnl_parcial(tp2_qty, tp2_price)
                            entry['remaining_qty'] = remaining_qty - tp2_qty
                            PARTIAL_LEVEL[symbol] = 2
                            ALERTS_HISTORY[f"{symbol}_tp2_sold"] = True
                            log.info("[PAPER] %s TP2 (30%%) | Entry=%.4f Exit=%.4f Qty=%.4f PnL=%.2f | Restan=%.4f",
                                     symbol, entry_price, tp2_price, tp2_qty, pnl, entry['remaining_qty'])
                            guardar_trade_csv(entry, tp2_price, pnl, 0, pnl, 'TP2_PARTIAL', 'tp2')
                            send_telegram(f"[PAPER] *{symbol} TP2 (30%)*\nPnL: {pnl:.2f} USDT | Restan: {entry['remaining_qty']:.4f}")
                            _save_trade_entries()
                            _save_partial_level()

            # --- Timeout (cierra remanente si perdiendo) ---
            entry_time = entry.get('entry_time')
            if isinstance(entry_time, datetime) and profit_pct < 0:
                horas = (datetime.now() - entry_time).total_seconds() / 3600
                if horas >= LOBO_TIMEOUT_HORAS:
                    remaining_qty = float(entry.get('remaining_qty', entry.get('quantity', 0)))
                    if remaining_qty > 0:
                        pnl = _pnl_parcial(remaining_qty, mark)
                        log.info("[PAPER] %s TIMEOUT +%.0fh Qty=%.4f PnL=%.2f", symbol, horas, remaining_qty, pnl)
                        guardar_trade_csv(entry, mark, pnl, 0, pnl, 'Timeout', 'timeout')
                        send_telegram(f"[PAPER] *{symbol} TIMEOUT*\nPnL: {pnl:.2f} USDT")
                    _full_cleanup(symbol)
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
                        log.info("[PAPER v4] %s Trail→%.4f", symbol, nuevo_sl)

        except Exception as e:
            log.error("[PAPER v4] Error gestionando %s: %s", symbol, e)

def _cerrar_pos_real(symbol: str, side: str, qty: float) -> bool:
    """Cierra una posición real en Bitget vía API.
    Retorna True si se cerró (o ya estaba cerrada), False si falló por otra razón."""
    close_side = 'sell' if side == 'long' else 'buy'
    try:
        exchange.create_order(symbol, 'market', close_side, qty, params={
            'marginCoin': 'USDT', 'marginMode': 'isolated', 'tradeSide': 'close',
        })
        return True
    except ccxt.ExchangeError as e:
        err_str = str(e)
        # 22002: No position to close — posición ya cerrada por exchange (TP/LIQ) o manualmente
        if '22002' in err_str or 'No position to close' in err_str:
            log.warning("[REAL] %s: Posición ya cerrada en exchange (22002) — limpiando local", symbol)
            return True  # La posición ya no existe → tratar como éxito
        log.error("[REAL] %s: ExchangeError cerrando: %s", symbol, e)
        return False
    except ccxt.NetworkError as e:
        log.error("[REAL] %s: NetworkError cerrando: %s", symbol, e)
        return False
    except Exception as e:
        log.error("[REAL] %s: Error inesperado cerrando: %s", symbol, e)
        return False

def manage_escudo_pro_v3(balance_total: float = 0.0):
    """Versión v4 de gestión de posiciones (real + paper)."""
    if PAPER_TRADE:
        _manage_paper_positions_v3(balance_total)
        return

    # Modo real — misma lógica que paper pero cerrando vía API
    if not TRADE_ENTRIES:
        return

    capital_fut = capital_disponible_futuros(balance_total)

    # v5 HYBRID: Fetch posiciones reales una vez por ciclo (detecta TP1/TP3 del exchange)
    pos_by_symbol = {}
    try:
        all_positions = exchange.fetch_positions()
        for p in all_positions:
            if float(p.get('contracts', 0)) > 0:
                pos_by_symbol[p['symbol']] = p
    except Exception as e:
        log.warning("[REAL] Error fetching positions: %s", e)

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

            # Detectar posición en exchange
            pos_data = pos_by_symbol.get(symbol)
            remaining_qty = float(entry.get('remaining_qty', entry.get('quantity', 0)))

            # Si la posición ya no existe en exchange pero la tenemos local → cerrada por exchange
            if pos_data is None and remaining_qty > 0:
                pnl = (mark - entry_price) * remaining_qty if side == 'long' else (entry_price - mark) * remaining_qty
                log.info("[REAL] %s Posición cerrada en exchange (TP3/LIQ). PnL≈%.2f", symbol, pnl)
                guardar_trade_csv(entry, mark, pnl, 0, pnl, 'EXCHANGE_CLOSE', 'exchange')
                _full_cleanup(symbol)
                continue

            # --- F10: Validación H4 estructural (v4: cada 4h en cierre de vela) ---
            if debe_validar_h4():
                try:
                    ohlcv_4h_val = exchange.fetch_ohlcv(symbol, timeframe='4h', limit=30)
                    if len(ohlcv_4h_val) >= 10:
                        df_4h_val = pd.DataFrame(ohlcv_4h_val, columns=['ts','o','h','l','c','v'])
                        if not validar_estructura_d1(df_4h_val, entry_price, side):
                            log.info("[REAL v4] %s: H4 invalida estructura - cerrando", symbol)
                            remaining_qty = entry.get('remaining_qty', entry['quantity'])
                            pnl = (mark - entry_price) * remaining_qty if side == 'long' else (entry_price - mark) * remaining_qty
                            _cerrar_pos_real(symbol, side, remaining_qty)
                            guardar_trade_csv(entry, mark, pnl, 0, pnl, 'D1_INVALID', 'd1_estructura')
                            _full_cleanup(symbol, cooldown=7200)
                            send_telegram(f"[REAL v4] *{symbol}* Cerrada por D1 estructura")
                            continue
                except Exception:
                    pass

            # --- F4: Evaluar cobertura asimétrica v4 ---
            if LOBO_HEDGE_ENABLED and symbol not in HEDGE_ENTRIES:
                hedge_params = evaluar_cobertura_v4(entry, mark)
                if hedge_params:
                    log.info("[REAL v4] %s: Activando cobertura %s lev=%.0fx",
                             symbol, hedge_params['side'], hedge_params['leverage'])
                    HEDGE_ENTRIES[symbol] = hedge_params
                    # Abrir cobertura real
                    try:
                        exchange.set_leverage(int(hedge_params['leverage']), symbol)
                    except Exception:
                        pass
                    try:
                        hedge_qty = hedge_params['size_usdt'] / mark
                        hs = hedge_params['side']
                        exchange.create_order(symbol, 'market', 'buy' if hs == 'long' else 'sell',
                                              hedge_qty, params={
                            'marginCoin': 'USDT', 'marginMode': 'isolated', 'tradeSide': 'open',
                            'presetStopSurplusPrice': str(exchange.price_to_precision(symbol, hedge_params['tp_price'])),
                        })
                    except Exception as e:
                        log.error("Error abriendo cobertura %s: %s", symbol, e)
                    send_telegram(f"[REAL v4] *{symbol}* Cobertura {hedge_params['side']} activada")

            # --- Gestionar cobertura activa ---
            hedge = HEDGE_ENTRIES.get(symbol)
            if hedge:
                hedge_side = hedge['side']; hedge_tp = hedge['tp_price']; hedge_sl = hedge['sl_price']
                hedge_lev = hedge['leverage']
                if hedge_side == 'short' and mark <= hedge_tp:
                    pnl_hedge = hedge.get('size_usdt', 0) * hedge_lev * ((hedge['entry_price'] - mark) / hedge['entry_price'])
                    log.info("[REAL] %s: Cobertura TP! PnL=%.2f", symbol, pnl_hedge)
                    HEDGE_ENTRIES.pop(symbol, None)
                elif hedge_side == 'long' and mark >= hedge_tp:
                    pnl_hedge = hedge.get('size_usdt', 0) * hedge_lev * ((mark - hedge['entry_price']) / hedge['entry_price'])
                    log.info("[REAL] %s: Cobertura TP! PnL=%.2f", symbol, pnl_hedge)
                    HEDGE_ENTRIES.pop(symbol, None)
                if hedge_side == 'short' and mark >= hedge_sl:
                    HEDGE_ENTRIES.pop(symbol, None)
                elif hedge_side == 'long' and mark <= hedge_sl:
                    HEDGE_ENTRIES.pop(symbol, None)

            # --- TP PARCIAL + BE (lógica bot_v6: PARTIAL_LEVEL 0→1→2) ---
            long_side = side == 'long'
            short_side = side == 'short'
            original_qty = float(entry.get('original_qty', entry.get('quantity', 0)))
            remaining_qty = float(entry.get('remaining_qty', entry.get('quantity', 0)))
            step_p = float(entry.get('step', 0))
            partial_lvl = PARTIAL_LEVEL.get(symbol, 0)
            lev = float(entry.get('leverage', LEVERAGE))

            # Detectar fills del exchange (TP plan orders) por qty discrepancy
            exchange_qty = float(pos_data['contracts']) if pos_data else remaining_qty
            if pos_data is not None and exchange_qty < remaining_qty * 0.95:
                # Exchange ejecutó algo (TP1 o TP2 plan order)
                if partial_lvl == 0 and exchange_qty <= original_qty * 0.65:
                    # TP1 ejecutado en exchange
                    tp1_p = float(entry.get('tp1_price', 0))
                    tp1_pnl = (tp1_p - entry_price) * original_qty * TP1_CLOSE_PCT if side == 'long' \
                        else (entry_price - tp1_p) * original_qty * TP1_CLOSE_PCT
                    entry['remaining_qty'] = exchange_qty
                    remaining_qty = exchange_qty
                    PARTIAL_LEVEL[symbol] = 1
                    ALERTS_HISTORY[f"{symbol}_tp1_sold"] = True
                    ALERTS_HISTORY[f"{symbol}_be_price"] = entry_price
                    entry['sl_price'] = entry_price  # Break Even
                    log.info("[REAL] %s TP1 EXCHANGE fill → BE. Remaining=%.4f PnL≈%.2f",
                             symbol, exchange_qty, tp1_pnl)
                    guardar_trade_csv(entry, tp1_p, tp1_pnl, 0, tp1_pnl, 'TP1_EXCHANGE', 'tp1_exchange')
                    _save_trade_entries()
                    _save_partial_level()
                elif partial_lvl == 1 and exchange_qty <= original_qty * 0.40:
                    # TP2 ejecutado en exchange
                    tp2_p = float(entry.get('tp2_price', 0))
                    tp2_pnl = (tp2_p - entry_price) * original_qty * TP2_CLOSE_PCT if side == 'long' \
                        else (entry_price - tp2_p) * original_qty * TP2_CLOSE_PCT
                    entry['remaining_qty'] = exchange_qty
                    remaining_qty = exchange_qty
                    PARTIAL_LEVEL[symbol] = 2
                    ALERTS_HISTORY[f"{symbol}_tp2_sold"] = True
                    log.info("[REAL] %s TP2 EXCHANGE fill. Remaining=%.4f PnL≈%.2f",
                             symbol, exchange_qty, tp2_pnl)
                    guardar_trade_csv(entry, tp2_p, tp2_pnl, 0, tp2_pnl, 'TP2_EXCHANGE', 'tp2_exchange')
                    _save_trade_entries()
                    _save_partial_level()

            def _pnl_parcial(qty_sold: float, exit_px: float) -> float:
                if side == 'long':
                    return (exit_px - entry_price) * qty_sold
                else:
                    return (entry_price - exit_px) * qty_sold

            sl_hit = (long_side and mark <= sl_price) or (short_side and mark >= sl_price)
            liq_hit = (long_side and mark <= liq_price) or (short_side and mark >= liq_price)
            tp_full_hit = (long_side and mark >= tp3_price) or (short_side and mark <= tp3_price)

            # ── SL / LIQ: cierre completo de lo que quede ──
            if sl_hit or liq_hit:
                if remaining_qty > 0:
                    pnl = _pnl_parcial(remaining_qty, mark)
                    status = 'SL' if sl_hit else 'LIQ'
                    reason = 'sl' if sl_hit else 'liquidacion'
                    log.info("[REAL] %s %s | Entry=%.4f Exit=%.4f Qty=%.4f PnL=%.2f",
                             symbol, status, entry_price, mark, remaining_qty, pnl)
                    _cerrar_pos_real(symbol, side, remaining_qty)
                    guardar_trade_csv(entry, mark, pnl, 0, pnl, status, reason)
                    send_telegram(f"[REAL] *{symbol} {status}*\nPnL: {pnl:.2f} USDT")
                _full_cleanup(symbol)
                continue

            # ── Full TP (TP3): cierre completo del remanente ──
            if tp_full_hit:
                if remaining_qty > 0:
                    pnl = _pnl_parcial(remaining_qty, tp3_price)
                    log.info("[REAL] %s TP3 FULL | Entry=%.4f Exit=%.4f Qty=%.4f PnL=%.2f",
                             symbol, entry_price, tp3_price, remaining_qty, pnl)
                    _cerrar_pos_real(symbol, side, remaining_qty)
                    guardar_trade_csv(entry, tp3_price, pnl, 0, pnl, 'TP3', 'tp3')
                    send_telegram(f"[REAL] *{symbol} TP3 FULL*\nPnL: {pnl:.2f} USDT")
                _full_cleanup(symbol)
                continue

            # ── TP1: parcial 40% + BE (nivel 0→1) — local fallback si exchange no ejecutó ──
            if partial_lvl == 0 and step_p > 0 and remaining_qty >= step_p:
                tp1_price = float(entry.get('tp1_price', 0))
                if tp1_price != entry_price:
                    tp1_reached = (long_side and mark >= tp1_price) or (short_side and mark <= tp1_price)
                    if tp1_reached:
                        tp1_qty = ((original_qty * TP1_CLOSE_PCT) // step_p) * step_p
                        tp1_qty = min(tp1_qty, remaining_qty - step_p)
                        if tp1_qty >= step_p:
                            pnl = _pnl_parcial(tp1_qty, tp1_price)
                            cerrado = _cerrar_pos_real(symbol, side, tp1_qty)
                            if cerrado:
                                entry['remaining_qty'] = remaining_qty - tp1_qty
                                PARTIAL_LEVEL[symbol] = 1
                                ALERTS_HISTORY[f"{symbol}_tp1_sold"] = True
                                ALERTS_HISTORY[f"{symbol}_be_price"] = entry_price
                                entry['sl_price'] = entry_price  # Break Even
                                log.info("[REAL] %s TP1 LOCAL(40%%)+BE | Entry=%.4f Exit=%.4f Qty=%.4f PnL=%.2f | Restan=%.4f",
                                         symbol, entry_price, tp1_price, tp1_qty, pnl, entry['remaining_qty'])
                                guardar_trade_csv(entry, tp1_price, pnl, 0, pnl, 'TP1_PARTIAL', 'tp1')
                                send_telegram(f"[REAL] *{symbol} TP1 (40%)+BE*\nPnL: {pnl:.2f} USDT | SL→Entry")
                                _save_trade_entries()
                                _save_partial_level()
                            else:
                                log.warning("[REAL] %s TP1 parcial falló (reintentará)", symbol)

            # ── TP2: parcial 30% (nivel 1→2) ──
            elif partial_lvl == 1 and step_p > 0 and remaining_qty >= step_p:
                tp2_price = float(entry.get('tp2_price', 0))
                if tp2_price != entry_price:
                    tp2_reached = (long_side and mark >= tp2_price) or (short_side and mark <= tp2_price)
                    if tp2_reached:
                        remaining_after_tp1 = original_qty - ((original_qty * TP1_CLOSE_PCT) // step_p) * step_p
                        tp2_qty = ((remaining_after_tp1 * TP2_CLOSE_PCT / (1 - TP1_CLOSE_PCT)) // step_p) * step_p
                        tp2_qty = min(tp2_qty, remaining_qty - step_p)
                        if tp2_qty >= step_p:
                            pnl = _pnl_parcial(tp2_qty, tp2_price)
                            cerrado = _cerrar_pos_real(symbol, side, tp2_qty)
                            if cerrado:
                                entry['remaining_qty'] = remaining_qty - tp2_qty
                                PARTIAL_LEVEL[symbol] = 2
                                ALERTS_HISTORY[f"{symbol}_tp2_sold"] = True
                                log.info("[REAL] %s TP2 LOCAL(30%%) | Entry=%.4f Exit=%.4f Qty=%.4f PnL=%.2f | Restan=%.4f",
                                         symbol, entry_price, tp2_price, tp2_qty, pnl, entry['remaining_qty'])
                                guardar_trade_csv(entry, tp2_price, pnl, 0, pnl, 'TP2_PARTIAL', 'tp2')
                                send_telegram(f"[REAL] *{symbol} TP2 (30%)*\nPnL: {pnl:.2f} USDT | Restan: {entry['remaining_qty']:.4f}")
                                _save_trade_entries()
                                _save_partial_level()
                            else:
                                log.warning("[REAL] %s TP2 parcial falló (reintentará)", symbol)

            # --- Timeout (cierra remanente si perdiendo) ---
            entry_time = entry.get('entry_time')
            if isinstance(entry_time, datetime) and profit_pct < 0:
                horas = (datetime.now() - entry_time).total_seconds() / 3600
                if horas >= LOBO_TIMEOUT_HORAS:
                    remaining_qty = float(entry.get('remaining_qty', entry.get('quantity', 0)))
                    if remaining_qty > 0:
                        pnl = _pnl_parcial(remaining_qty, mark)
                        log.info("[REAL] %s TIMEOUT +%.0fh Qty=%.4f PnL=%.2f", symbol, horas, remaining_qty, pnl)
                        _cerrar_pos_real(symbol, side, remaining_qty)
                        guardar_trade_csv(entry, mark, pnl, 0, pnl, 'Timeout', 'timeout')
                        send_telegram(f"[REAL] *{symbol} TIMEOUT*\nPnL: {pnl:.2f} USDT")
                    _full_cleanup(symbol)
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

            # --- Trailing stop (solo después de TP1 → BE activado) ---
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
                        log.info("[REAL] %s Trail→%.4f", symbol, nuevo_sl)

        except Exception as e:
            log.error("[REAL] Error gestionando %s: %s", symbol, e)

# =====================================================================
# 12. BUCLE PRINCIPAL v4
# =====================================================================
def main():
    global LAST_KNOWN_INDICATORS, ALERTS_HISTORY, PEAK_PRICES, COOLDOWNS
    global SESSION_ACTIVE_SYMBOLS, DAILY_STATS, TRADE_ENTRIES, TRAIL_COUNTS
    global HEDGE_ENTRIES, ADVERSE_PRICES, PRICE_PATHS, exchange, PARTIAL_LEVEL

    log.info("=" * 60)
    log.info("LOBOBOT v4 — BITLOBO FORMALIZADO (F1-F12 + D2-D9) iniciando")
    log.info("=" * 60)

    if exchange is None:
        if not init_exchange():
            log.critical("No se pudo inicializar exchange")
            return

    _load_trade_entries()
    _load_partial_level()
    restaurar_tp_exchange()
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
                    f"*REPORTE DIARIO v4* ({now.strftime('%d/%m')})\n"
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
            log.info("Balance total=%.2f | Futuros(30%%)=%.2f | Liquidez(50%%)=%.2f",
                     balance_total, capital_fut,
                     capital_liquidez(balance_total))

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
                    ohlcv_1h, ohlcv_4h, ohlcv_15m = ohlcv_data.get(symbol, (None, None, None))
                    if not ohlcv_1h or not ohlcv_4h:
                        continue
                    if len(ohlcv_1h) < 50 or len(ohlcv_4h) < 10:
                        continue

                    df_1h  = pd.DataFrame(ohlcv_1h, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                    df_4h  = pd.DataFrame(ohlcv_4h, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                    df_15m = pd.DataFrame(ohlcv_15m, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume']) if ohlcv_15m else None

                    # F7: Solo evaluar al cierre de vela H1
                    if not es_nueva_vela_1h(df_1h):
                        continue

                    precio_actual = float(df_1h['close'].iloc[-1])
                    atr_val = float(_atr(df_1h, LOBO_ATR_PERIOD).iloc[-1])
                    if atr_val == 0 or pd.isna(atr_val):
                        continue

                    # v4: Ventana altcoins (BTC.D + Elliott)
                    ventana_altcoins = check_btcd_elliott_ventana_altcoins()

                    # Evaluar señal BITLOBO v4
                    # Evaluar señal BITLOBO v5
                    # Mapeo: df_1h→df_h4 (principal), df_4h→df_d1 (confirmación), df_15m→df_1h (micro)
                    senal_long = evaluar_senal_bitlobo_v4(
                        symbol, df_1h, df_4h, precio_actual, atr_val, balance_total,
                        es_long=True, df_1h=df_15m, ventana_altcoins=ventana_altcoins,
                    )

                    sweeps = detectar_sweep(df_1h)
                    hay_sweep_short = any(s['tipo'] == 'sweep_alcista_short' for s in sweeps)

                    fvgs = detectar_fvg(df_1h)
                    hay_fvg_bajista = any(f['tipo'] == 'bajista' for f in fvgs)
                    rsi_series = _rsi(df_1h['close'], LOBO_RSI_PERIOD)
                    try:
                        rsi_val_actual = float(rsi_series.iloc[-1])
                    except (IndexError, ValueError):
                        rsi_val_actual = 50.0
                    hay_rsi_sobrecompra = not pd.isna(rsi_val_actual) and rsi_val_actual > LOBO_RSI_OVERBOUGHT

                    condicion_short = hay_sweep_short or hay_rsi_sobrecompra  # v5: relax (sin FVG requerido)

                    senal_short = None
                    if condicion_short:
                        senal_short = evaluar_senal_bitlobo_v4(
                            symbol, df_1h, df_4h, precio_actual, atr_val, balance_total,
                            es_long=False, df_1h=df_15m, ventana_altcoins=ventana_altcoins,
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
                        'original_qty': qty,
                        'remaining_qty': qty,
                        'step': step,
                        'balance_before': balance_total,
                        'capital_futuros': capital_fut,
                        'atr_val': senal.get('atr_val', 0),
                        'size_usdt': round(actual_margin, 2),
                        'risk_pct': round(actual_margin / max(capital_fut, 1) * 100, 2),
                        'score': score,
                        'rr': rr,
                    }

                    if PAPER_TRADE:
                        log.info("[PAPER] %s %s qty=%.6f lev=%.0f step=%s", side_name, symbol, qty, lev_calc, step)
                        send_telegram(
                            f"[PAPER] *{symbol} {side_name}* (BITLOBO)\n"
                            f"Entry: `{exchange.price_to_precision(symbol, precio_actual)}`\n"
                            f"SL/Liq: `{exchange.price_to_precision(symbol, sl_price)}` / `{exchange.price_to_precision(symbol, liq_price)}`\n"
                            f"Lev: {lev_calc:.0f}x\n"
                            f"TP1(40%): `{exchange.price_to_precision(symbol, tp1_price)}`\n"
                            f"TP2(30%): `{exchange.price_to_precision(symbol, tp2_price)}`\n"
                            f"TP3(30%): `{exchange.price_to_precision(symbol, tp3_price)}`\n"
                            f"R:R: {rr:.2f} | Score: {score}/{max_score}"
                        )
                        TRADE_ENTRIES[symbol] = entry_record
                        PARTIAL_LEVEL[symbol] = 0
                        _save_trade_entries()
                        _save_partial_level()
                        busy_symbols.add(symbol)
                        SESSION_ACTIVE_SYMBOLS.add(symbol)
                        COOLDOWNS[symbol] = time.time() + 14400
                        guardar_signal_log(symbol, side_name, precio_actual, score, max_score,
                                           senal['detalles'], sl_price, liq_price, lev_calc,
                                           tp1_price, tp2_price, tp3_price, rr, taken=True)
                        continue

                    # ── Orden real en Bitget ──
                    try:
                        exchange.set_leverage(int(lev_calc), symbol)
                    except Exception as e:
                        log.warning("Error set_leverage %s %.0f: %s", symbol, lev_calc, e)

                    # Entrada + TP3 safety como presetStopSurplusPrice
                    params = {
                        'marginCoin': 'USDT',
                        'marginMode': 'isolated',
                        'tradeSide': 'open',
                        'presetStopSurplusPrice': str(exchange.price_to_precision(symbol, tp3_price)),
                    }
                    try:
                        exchange.create_order(symbol, 'market', 'buy' if es_long else 'sell', qty, params=params)
                    except Exception as e:
                        log.error("Error orden %s %s: %s", side_name, symbol, e)
                        continue

                    # Colocar TP1 y TP2 como plan orders en exchange
                    trade_side = 'long' if es_long else 'short'
                    tp1_qty_plan = ((qty * TP1_CLOSE_PCT) // step) * step
                    tp2_remaining = qty - tp1_qty_plan
                    tp2_qty_plan = ((tp2_remaining * TP2_CLOSE_PCT / (1 - TP1_CLOSE_PCT)) // step) * step
                    tp1_ok = False
                    tp2_ok = False
                    time.sleep(1)
                    if tp1_qty_plan >= step and tp1_qty_plan * tp1_price >= 5:
                        tp1_ok = _place_tp_plan(symbol, tp1_price, tp1_qty_plan, trade_side)
                        if tp1_ok:
                            log.info("[REAL] %s TP1 plan: %s @ %s (40%%)", symbol, tp1_qty_plan, tp1_price)
                    if tp2_qty_plan >= step and tp2_qty_plan * tp2_price >= 5:
                        tp2_ok = _place_tp_plan(symbol, tp2_price, tp2_qty_plan, trade_side)
                        if tp2_ok:
                            log.info("[REAL] %s TP2 plan: %s @ %s (30%%)", symbol, tp2_qty_plan, tp2_price)

                    send_telegram(
                        f"*{symbol} {side_name}* (BITLOBO)\n"
                        f"Entry: `{exchange.price_to_precision(symbol, precio_actual)}`\n"
                        f"Lev: {lev_calc:.0f}x | Liq: `{exchange.price_to_precision(symbol, liq_price)}`\n"
                        f"TP1(40%): `{exchange.price_to_precision(symbol, tp1_price)}` [{'EX' if tp1_ok else 'LOCAL'}]\n"
                        f"TP2(30%): `{exchange.price_to_precision(symbol, tp2_price)}` [{'EX' if tp2_ok else 'LOCAL'}]\n"
                        f"TP3(30%): `{exchange.price_to_precision(symbol, tp3_price)}` [EX-SAFETY]\n"
                        f"R:R: {rr:.2f} | Score: {score}/{max_score}"
                    )
                    PARTIAL_LEVEL[symbol] = 0
                    TRADE_ENTRIES[symbol] = entry_record
                    _save_trade_entries()
                    _save_partial_level()
                    busy_symbols.add(symbol)
                    SESSION_ACTIVE_SYMBOLS.add(symbol)
                    COOLDOWNS[symbol] = time.time() + 14400
                    guardar_signal_log(symbol, side_name, precio_actual, score, max_score,
                                       senal['detalles'], sl_price, liq_price, lev_calc,
                                       tp1_price, tp2_price, tp3_price, rr, taken=True)

                except Exception as e:
                    log.debug("Error procesando %s: %s", symbol, e)
                    continue

            time.sleep(60)

        except Exception as e:
            log.error("Error en ciclo principal v4: %s", e, exc_info=True)
            time.sleep(60)

# =====================================================================
# 13. ENTRY POINT
# =====================================================================
if __name__ == "__main__":
    log.info("LOBOBOT v4 iniciando en modo standalone...")
    if exchange is None:
        init_exchange()
    main()
