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
  F9 - Break Even al alcanzar TP2 (no al 1.5%)
  F10- Invalidación D1 estructural (swing points)
  F11- Ondas Elliott con relaciones Fibonacci entre ondas
  F12- TPs en zonas reales (FVG/OB/estructurales)

Uso:
    python lobobot_v3.py                          # Bot standalone
    gunicorn lobobot_v3:app --workers 1 --threads 2   # Render (requiere flask)

Variables de entorno (nuevas respecto a v2):
    LOBO_LIQUIDEZ_PCT=20    LOBO_FUTUROS_PCT=80
    LOBO_SPOT_MARTINGALA_1=0.1  LOBO_SPOT_MARTINGALA_2=0.2  LOBO_SPOT_MARTINGALA_3=0.3
    LOBO_HEDGE_ENABLED=true     LOBO_HEDGE_LEV_MULT=3
"""

from __future__ import annotations
import os, sys, time, json, math, logging, asyncio, threading, csv, warnings
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional
import numpy as np
import pandas as pd
pd.set_option("future.no_silent_downcasting", True)
import ccxt, ccxt.async_support as ccxt_async
import requests

# FIX (Issue 4): Flask para Render healthcheck (import condicional)
# NOTA: Flask vive en bot_web_service.py, NO aquí.
# Este import es solo para compatibilidad si se ejecuta directo.
try:
    from flask import Flask
    _FLASK_AVAILABLE = True
except ImportError:
    _FLASK_AVAILABLE = False

# =====================================================================
# 1. LOGGER (idéntico a v2)
# =====================================================================
# QA-FIX (2026-08-09): stdout cp1252 en Windows rompe logs con '→'/'≈'.
# Reconversión a UTF-8 con reemplazo (portable: Render/Linux ignora).
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass  # stdout no reconfigurable (p.ej. redirección a pipe binario)

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
# Lock para thread-safety en DOMINANCE_CACHE y USDTD_HISTORY
_DOMINANCE_LOCK: threading.Lock = threading.Lock()

# =====================================================================
# FIX (Issues #1,#3,#4,#5,#6,#14): Background thread para APIs bloqueantes
# =====================================================================
_BG_DOMINANCE_THREAD: Optional[threading.Thread] = None
_BG_PROXY_THREAD: Optional[threading.Thread] = None

def _bg_refresh_dominancia():
    """Fetch CoinGecko en background thread (no bloquea el loop)."""
    try:
        resp = requests.get("https://api.coingecko.com/api/v3/global", timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            btc_d = data.get('data', {}).get('market_cap_percentage', {}).get('btc', None)
            if btc_d is not None:
                now = time.time()
                result = btc_d > 50.0
                with _DOMINANCE_LOCK:
                    DOMINANCE_CACHE['btc'] = result
                    DOMINANCE_CACHE['ts'] = now
                log.debug("BG Dominancia refreshed: BTC.D=%.1f%% trend=%s", btc_d, result)
    except Exception as e:
        log.debug("BG Dominancia refresh error: %s", e)

def _bg_refresh_proxy_usdtd():
    """Fetch proxy USDT.D en background thread (no bloquea el loop).
    FIX Bug#3: Cierra exchange instance al terminar (evita socket leak).
    """
    exch_bg = None
    try:
        exch_bg = ccxt.bitget({'enableRateLimit': True})
        tickers = exch_bg.fetch_tickers()
        vol_usdt_pairs = 0.0
        vol_non_usdt = 0.0
        for s, t in tickers.items():
            qv = float(t.get('quoteVolume', 0))
            if s.endswith('/USDT:USDT'):
                vol_usdt_pairs += qv
            else:
                vol_non_usdt += qv
        vol_total = vol_usdt_pairs + vol_non_usdt
        if vol_total > 0:
            # FIX Bug#2: Ahora compara USDT pairs vs TOTAL (incluye BTC pairs etc.)
            proxy = (vol_usdt_pairs / vol_total) * 100
        else:
            proxy = 50.0  # fallback neutro
        now = time.time()
        with _DOMINANCE_LOCK:
            USDTD_HISTORY.append((now, proxy))
            if len(USDTD_HISTORY) > 80:
                USDTD_HISTORY[:] = USDTD_HISTORY[-80:]
            # Recalcular resistencia con datos frescos
            result = True
            if len(USDTD_HISTORY) >= 15:
                vals = [v for _, v in USDTD_HISTORY]
                for i in range(2, len(vals) - 2):
                    gap_up = vals[i] - vals[i-2]
                    if gap_up > 0.5:
                        gap_alto = max(vals[i-2], vals[i])
                        gap_bajo = min(vals[i-2], vals[i])
                        rellenado = any(gap_bajo <= vals[j] <= gap_alto for j in range(i+1, len(vals)))
                        if not rellenado and proxy >= gap_bajo * 0.99:
                            result = True
                            DOMINANCE_CACHE['usdtd'] = result
                            DOMINANCE_CACHE['ts'] = now
                            log.debug("BG USDT.D proxy=%.2f FVG resistencia activa", proxy)
                            return
            vals = [v for _, v in USDTD_HISTORY[-30:]]
            if len(vals) >= 10:
                p85 = sorted(vals)[int(len(vals) * 0.85)]
                result = proxy >= p85 * 0.98
            else:
                result = proxy > 62.0
            DOMINANCE_CACHE['usdtd'] = result
            DOMINANCE_CACHE['ts'] = now
            log.debug("BG USDT.D proxy=%.2f resistencia=%s", proxy, result)
    except Exception as e:
        log.debug("BG USDT.D proxy refresh error: %s", e)
    finally:
        if exch_bg:
            try:
                exch_bg.close()
            except Exception:
                pass

def _schedule_bg_dominance_refresh():
    """Agenda refresh de dominancia + proxy en background threads (no bloquea).
    FIX Bug#1: Ya no usa .cancel() (threading.Thread no tiene ese método).
    FIX Bug#6: TTL para reintentar es correcto (< 60s, no > 300s).
    """
    global _BG_DOMINANCE_THREAD, _BG_PROXY_THREAD
    now = time.time()
    if now - DOMINANCE_CACHE.get('ts', 0) < DOMINANCE_CACHE_TTL:
        return  # Cache aún fresco, no refrescar
    # No cancelar threads previos — son daemon threads de corta duración
    # Si ya terminaron, is_alive()=False y no se crea duplicado innecesariamente
    # Si aún corren (CoinGecko lento), esperar que terminen naturalmente
    if (_BG_DOMINANCE_THREAD and _BG_DOMINANCE_THREAD.is_alive()) or \
       (_BG_PROXY_THREAD and _BG_PROXY_THREAD.is_alive()):
        return  # Ya hay un refresh en curso, esperar
    _BG_DOMINANCE_THREAD = threading.Thread(target=_bg_refresh_dominancia, daemon=True)
    _BG_PROXY_THREAD = threading.Thread(target=_bg_refresh_proxy_usdtd, daemon=True)
    _BG_DOMINANCE_THREAD.start()
    _BG_PROXY_THREAD.start()
    log.debug("BG dominance refresh scheduled")

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
TIMEFRAME_PRINCIPAL  = os.environ.get('LOBO_TIMEFRAME_PRINCIPAL',  '15m')  # ← Principal (señal)
TIMEFRAME_CONFIRMACION = os.environ.get('LOBO_TIMEFRAME_CONFIRMACION', '4h')  # ← Confirmación
TIMEFRAME_MICRO    = os.environ.get('LOBO_TIMEFRAME_MICRO',     '5m')   # ← Microfractalidad

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

# === F8: Riesgo base 1.5-2% (sobre el 80% de futuros) ===
# FIX-AUDIT-8: 5% → 2% (alineado con F8 documentado: 1.5-2% por trade)
LOBO_RISK_PCT            = float(os.environ.get('LOBO_RISK_PCT', '2')) / 100
LOBO_RISK_PCT_EXCEP      = float(os.environ.get('LOBO_RISK_PCT_EXCEP', '4')) / 100
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
# FIX-AUDIT-3: restaurados a la estrategia DOCUMENTADA (25/50/100% PnL sobre margen).
# Con 15/30/50% y lev 10-20x, TP3 = solo 2.5-5% de precio vs SL 1.5ATR (~2-3%)
# → RR efectiva < 1 → EV negativo. 15/30/50% restaura expectativa positiva:
#   win medio = 0.4×15 + 0.3×30 + 0.3×50 = 30% margen vs pérdida ~30-100% margen.
TP1_PNL_TARGET     = float(os.environ.get('LOBO_TP1_PNL_TARGET', '0.15'))  # 15% PnL en TP1
TP2_PNL_TARGET     = float(os.environ.get('LOBO_TP2_PNL_TARGET', '0.30'))  # 30% PnL en TP2
TP3_PNL_TARGET     = float(os.environ.get('LOBO_TP3_PNL_TARGET', '0.50'))  # 50% PnL safety net

# F9: BE trigger ahora es "alcanzar TP2" (en vez de TP1)
# Se usa TP2 como trigger, no un porcentaje independiente

# General
LOBO_TIMEOUT_HORAS       = float(os.environ.get('LOBO_TIMEOUT_HORAS', '96'))
LEVERAGE                 = float(os.environ.get('LOBO_LEVERAGE', '20.0'))
# FIX-AUDIT-4: umbral por evidencia de grid 2026-08-08 (score_sl_grid.py, cache v6,
# 43 simbolos, RSI[30,70], risk 2%): score=8/SL=1.5 -> net +30,968 PF 1.37 Sharpe 7.39
# (config v3 original). score=14 -> 3-5 trades/30d, PF ~0 (sin poder, perdedor).
# Render: si LOBO_SCORE_MIN se setea por env, actualizar a 8 ahi tambien.
LOBO_SCORE_MIN           = int(os.environ.get('LOBO_SCORE_MIN', '8'))
MIN_ORDER_USDT           = float(os.environ.get('LOBO_MIN_ORDER_USDT', '5'))
PAPER_TRADE              = os.environ.get('LOBOBOT_PAPER_TRADE', 'false').lower() == 'true'

# === FIX-AUDIT-6: Fees realistas en paper/simulación (taker Bitget futures) ===
FEE_TAKER                = float(os.environ.get('LOBO_FEE_TAKER', '0.0006'))

# === FIX-AUDIT-7: Kill-Switch (parada de emergencia) ===
LOBO_KILL_MAX_CONSEC_LOSSES = int(os.environ.get('LOBO_KILL_MAX_CONSEC_LOSSES', '4'))
LOBO_KILL_COOLDOWN_H        = float(os.environ.get('LOBO_KILL_COOLDOWN_H', '24'))
KILL_UNTIL: float = 0.0   # epoch ts; mientras time.time() < KILL_UNTIL → no abrir posiciones
CONSECUTIVE_LOSSES: int = 0

# SL simple 1.5 ATR (original)
LOBO_SL_ATR              = float(os.environ.get('LOBO_SL_ATR', '1.5'))
LOBO_SL_ATR_SMALL_VOL   = float(os.environ.get('LOBO_SL_ATR_SMALL_VOL', '5000000'))  # volumen diario para clasificar
# FILTRO DE REGIMEN (A): hipótesis trend-following FALSADA por backtest 2026-08-06
# (PF 1.22→0.87, +84%→-30%: eliminó los TP3 reversales). DEFAULT OFF.
# Código mantenido por si se quiere re-probar en otro régimen de mercado.
LOBO_REGIME_FILTER      = os.environ.get('LOBO_REGIME_FILTER', '0').lower() == '1'
# WHITELIST (D): operar SOLO criptos reales (lista de bases separadas por coma).
# El backtest 2026-08-06 demostró que el edge aparente vive en ETFs-sintéticos
# (SOXL/MU/SNDK/SKHY/BLESS...) que el bot no debe tocar. Vacío = sin filtro.
LOBO_WHITELIST          = {b.strip().upper() for b in os.environ.get('LOBO_WHITELIST', '').split(',') if b.strip()}
LOBO_REGIME_EMA_PERIOD  = int(os.environ.get('LOBO_REGIME_EMA_PERIOD', '50'))

# (No TP fixed ATR — se usa F12 zone-based)

# === F4: Coberturas asimétricas ===
LOBO_HEDGE_ENABLED       = os.environ.get('LOBO_HEDGE_ENABLED', 'true').lower() == 'true'
LOBO_HEDGE_LEV_MULT      = float(os.environ.get('LOBO_HEDGE_LEV_MULT', '3.0'))
LOBO_HEDGE_TRIGGER_PCT   = float(os.environ.get('LOBO_HEDGE_TRIGGER_PCT', '0.5'))

# === v4: Cobertura asimétrica CORREGIDA (manual BITLOBO) ===
LOBO_HEDGE_MARGIN_PCT    = float(os.environ.get('LOBO_HEDGE_MARGIN_PCT', '0.15'))  # 15% del margen principal

# === v4: CHOCH (Change of Character) ===
LOBO_CHOCH_LOOKBACK      = int(os.environ.get('LOBO_CHOCH_LOOKBACK', '30'))

# === v4: Microfractalidad (ondas 5m) ===
LOBO_MICRO_LOOKBACK      = int(os.environ.get('LOBO_MICRO_LOOKBACK', '72'))

# === v4: Flat Continuación ===
LOBO_FLAT_MIN_VELAS      = int(os.environ.get('LOBO_FLAT_MIN_VELAS', '3'))
LOBO_FLAT_MAX_ATR        = float(os.environ.get('LOBO_FLAT_MAX_ATR', '1.5'))

# === v4: BTC.D + Elliott ===
LOBO_BTCD_ELLOTT_LOOKBACK = int(os.environ.get('LOBO_BTCD_ELLOTT_LOOKBACK', '60'))

# === v4: D1 validación solo 00:00-00:05 UTC ===
LOBO_D1_CHECK_START      = int(os.environ.get('LOBO_D1_CHECK_START', '0'))

# === F5: RSI y Volumen ===
LOBO_RSI_PERIOD           = int(os.environ.get('LOBO_RSI_PERIOD', '14'))
# AUDIT 2026-08-08 (brute-force 62 símbolos × 30d, score 8 y 14): RSI[45,55] era
# el PEOR config (PF 1.29, Sharpe 6.14, MaxDD 42.9%). RSI[30,70] (manual) es el
# #1 (PF 1.37, Sharpe 7.46, MaxDD 36.4%). Umbrales laxos = menos trades marginales.
LOBO_RSI_OVERSOLD         = float(os.environ.get('LOBO_RSI_OVERSOLD', '30'))
LOBO_RSI_OVERBOUGHT       = float(os.environ.get('LOBO_RSI_OVERBOUGHT', '70'))
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
    if period < 1:
        period = 14  # QA-FIX: periodo inválido/misconfig env → default (anti ZeroDivisionError)
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
# F2: Dominancias (refactorizado a background threads)
# ================================================================
# DEPRECATED: obtener_dominancia_real() y calcular_proxy_usdtd() eliminadas.
# Ahora se usa _bg_refresh_dominancia() + _bg_refresh_proxy_usdtd() en threads
# separadas que alimentan DOMINANCE_CACHE (non-blocking para el loop principal).
# ================================================================

def check_usdtd_resistencia_short() -> bool:
    """
    FIX-AUDIT-5: Simétrico de R4 para SHORT.
    Favorable para shorts si USDT.D está DÉBIL (por debajo del percentil 50 del
    historial proxy): capital fluyendo hacia cripto/riesgo → shorts en contra
    de la marea. Antes los shorts recibían +1 incondicional (loophole).
    """
    now = time.time()
    if now - DOMINANCE_CACHE['ts'] < DOMINANCE_CACHE_TTL and DOMINANCE_CACHE.get('usdtd_short') is not None:
        return bool(DOMINANCE_CACHE['usdtd_short'])
    _schedule_bg_dominance_refresh()
    with _DOMINANCE_LOCK:
        history_snapshot = list(USDTD_HISTORY)
    if len(history_snapshot) >= 10:
        vals = [v for _, v in history_snapshot[-30:]]
        mediana = sorted(vals)[len(vals) // 2]
        proxy = history_snapshot[-1][1]
        result = proxy <= mediana * 1.01
        with _DOMINANCE_LOCK:
            DOMINANCE_CACHE['usdtd_short'] = result
            DOMINANCE_CACHE['ts'] = 0
        return result
    # Sin historial: neutro (no regalar puntos)
    return False

def check_dominancia_btc_long() -> bool:
    """
    F2-R5: BTC.D - retorna True si BTC.D está subiendo (solo operar BTC).
    FIX (Issue 3): Solo lee del cache (non-blocking). El refresh lo hace
    _schedule_bg_dominance_refresh() en threads background.
    Fallback: tendencia BTC/USDT si cache está vacío.
    """
    now = time.time()
    # Si cache fresco, retornar directo (0ms)
    if now - DOMINANCE_CACHE['ts'] < DOMINANCE_CACHE_TTL and DOMINANCE_CACHE['btc'] is not None:
        return DOMINANCE_CACHE['btc']

    # Cache vacío o expirado → agendar refresh en background + usar fallback inmediato
    _schedule_bg_dominance_refresh()

    # Fallback síncrono RÁPIDO (solo 1 llamada BTC/USDT, no bloqueante)
    result = False
    try:
        exch = ccxt.bitget({'enableRateLimit': True})
        ohlcv = exch.fetch_ohlcv('BTC/USDT:USDT', timeframe='4h', limit=30)
        if ohlcv and len(ohlcv) > 10:
            closes = pd.Series([c[4] for c in ohlcv])
            sma20 = closes.rolling(20).mean()
            # FIX Bug#7: Verificar NaN antes de calcular pendiente
            if pd.isna(sma20.iloc[-1]) or pd.isna(sma20.iloc[-5]):
                result = False  # Sin datos suficientes, default neutro
            else:
                pendiente = (sma20.iloc[-1] - sma20.iloc[-5]) / max(sma20.iloc[-5], 1)
                result = pendiente > 0.001
    except Exception as e:
        log.debug("Fallback BTC.D SMA error: %s", e)
    # AUDIT-FIX 2026-08-08: ts=now (antes 0). Con ts=0 el cache NUNCA estaba
    # fresco → el fallback síncrono se repetía cada ciclo (bloqueaba ~1-2s) y
    # _schedule_bg_dominance_refresh() re-creaba los threads cada ciclo (2 llamadas
    # API extra por ciclo a CoinGecko+Bitget, derroche en Render free tier).
    # Con ts=now el valor del fallback es válido 5 min y el refresh background
    # lo sobreescribe cuando termina. Si CoinGecko falla, el fallback persiste.
    with _DOMINANCE_LOCK:
        DOMINANCE_CACHE['btc'] = result
        DOMINANCE_CACHE['ts'] = time.time()  # cache fresco 5 min
    return result

def check_usdtd_resistencia_long() -> bool:
    """
    F2-R4: USDT.D en resistencia → favorable para Longs.
    FIX (Issue 3): Solo lee del cache (non-blocking). El refresh lo hace
    _bg_refresh_proxy_usdtd() en threads background.
    Cacheado 5 min. Fallback: percentil simple.
    """
    now = time.time()
    if now - DOMINANCE_CACHE['ts'] < DOMINANCE_CACHE_TTL and DOMINANCE_CACHE['usdtd'] is not None:
        return DOMINANCE_CACHE['usdtd']

    # Cache vacío → agendar refresh background + fallback rápido
    _schedule_bg_dominance_refresh()

    # Si hay datos en historial de sessions anteriores, usarlos
    with _DOMINANCE_LOCK:
        history_snapshot = list(USDTD_HISTORY)  # copia segura
    if history_snapshot:
        vals = [v for _, v in history_snapshot[-30:]]
        if len(vals) >= 10:
            p85 = sorted(vals)[int(len(vals) * 0.85)]
            proxy = history_snapshot[-1][1]
            result = proxy >= p85 * 0.98
            with _DOMINANCE_LOCK:
                DOMINANCE_CACHE['usdtd'] = result
                DOMINANCE_CACHE['ts'] = 0  # FIX Bug#6: forzar refresh en próximo ciclo
            return result

    # Sin datos históricos: default conservador = True (no bloquear entradas)
    result = True
    with _DOMINANCE_LOCK:
        DOMINANCE_CACHE['usdtd'] = result
        DOMINANCE_CACHE['ts'] = 0  # FIX Bug#6: forzar refresh en próximo ciclo
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
        return bool(cond1 and cond2 and cond3), detalles
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
    # El bot solo llama a evaluar_senal_bitlobo_v4 cuando es_nueva_vela_principal es True,
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
    """Retorna el capital de futuros = 80% del balance total (bruto, sin descontar posiciones)."""
    return balance_total * LOBO_FUTUROS_PCT

def calcular_margen_real_disponible(balance_total: float, positions_list: Optional[list] = None) -> float:
    """
    Calcula el margen REAL disponible para nuevas posiciones.
    Descuenta el margen ya comprometido en posiciones abiertas.
    Retorna max(0, capital_futuros - margen_lockeado).

    Args:
        balance_total: Balance total USDT
        positions_list: Lista pre-fetched de posiciones (evita doble API call).
                        Si es None, hace fetch internamente.
    """
    capital_fut = capital_disponible_futuros(balance_total)
    margen_lockeado = 0.0
    try:
        if exchange is None or PAPER_TRADE:
            # Paper mode: estimar desde TRADE_ENTRIES
            for sym, entry in TRADE_ENTRIES.items():
                try:
                    size = float(entry.get('size_usdt', 0))
                    if math.isfinite(size) and size > 0:
                        margen_lockeado += size
                except (TypeError, ValueError):
                    continue  # QA-FIX: entrada corrupta no aborta el cálculo
            return max(0.0, capital_fut - margen_lockeado)
        if positions_list is None:
            positions_list = exchange.fetch_positions()
        for pos in positions_list:
            try:
                contracts = float(pos.get('contracts', 0))
                if not (math.isfinite(contracts) and contracts > 0):
                    continue  # posiciones cerradas (0) o corruptas (negativo/NaN) se omiten
                # Margin = notional / leverage
                notional = float(pos.get('notional', 0))
                lev = float(pos.get('leverage', 1))
                if math.isfinite(notional) and notional > 0 and math.isfinite(lev) and lev > 0:
                    margen_lockeado += abs(notional) / lev
                else:
                    # Fallback: usar initialMargin si está disponible
                    initial_margin = float(pos.get('initialMargin', 0))
                    if math.isfinite(initial_margin) and initial_margin > 0:
                        margen_lockeado += initial_margin
            except (TypeError, ValueError):
                continue  # QA-FIX: posición corrupta no aborta el cálculo del resto
    except Exception as e:
        log.debug("Error calculando margen lockeado: %s", e)
    disponible = max(0.0, capital_fut - margen_lockeado)
    log.info("Margen real: FutBruto=%.2f Lockeado=%.2f Disponible=%.2f",
             capital_fut, margen_lockeado, disponible)
    return disponible

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
    # AUDIT-FIX (QA fuzzing): NaN/inf o precios no positivos o lev inválido → 0 (defensivo)
    # Nota: NaN <= 0 es False, por eso se exige isfinite explícito.
    if not (math.isfinite(entry_price) and entry_price > 0) or \
       not (math.isfinite(leverage) and leverage > 0):
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
    # QA-FIX: membership (no truthiness): hedge registrado como dict vacío/placeholder
    # aún bloquea re-cobertura. Consistente con el guard del main loop (line 3024).
    if symbol in HEDGE_ENTRIES:
        return None
    side = pos_entry.get('side', 'long')
    # QA-FIX: diccionarios corruptos/incompletos → None (anti KeyError/TypeError)
    try:
        entry_price = float(pos_entry.get('entry_price', 0))
        sl_price = float(pos_entry.get('sl_price', 0))
        liq_price = float(pos_entry.get('liq_price', 0))
        main_margin = float(pos_entry.get('size_usdt', 0))
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(entry_price) and entry_price > 0 and
            math.isfinite(sl_price) and sl_price > 0 and
            math.isfinite(liq_price) and liq_price > 0 and
            math.isfinite(main_margin) and main_margin > 0):
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
    - TP1: 15% PnL → cierra 40% de la qty
    - TP2: 30% PnL → cierra 30% de la qty
    - TP3: 50% PnL → safety net, cierra el 30% restante

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
# F7: Timing de entrada (cierre de vela 15m) — FIX-AUDIT-1
# ================================================================
# FIX-AUDIT-1 (CRÍTICO): El código original retornaba True con la vela ABIERTA
# (última fila del df = vela en formación, diff_ms siempre < 20 min) → el bot
# entraba a mitad de vela con indicadores que aún repinteaban (lookahead) y
# al precio de una vela incompleta. Evidencia: max_favorable_pct=0.00 en todos
# los SL (entrada contra el movimiento).
#
# Ahora: (a) el main loop descarta la vela abierta (iloc[:-1]) antes de analizar,
# (b) aquí solo se evalúan velas CERRADAS recientes (< 20 min desde su cierre),
# (c) dedupe por timestamp: cada vela se evalúa UNA sola vez.
_ULTIMA_VELA_EVALUADA: dict = {}

def es_nueva_vela_principal(df: pd.DataFrame, symbol: str = '') -> bool:
    """
    F7 CORREGIDO: True solo si la última vela CERRADA del df acaba de cerrar
    (hace < 20 min) y aún no ha sido evaluada en esta sesión.
    El df debe venir SIN la vela actual abierta (ver main loop).
    """
    if df is None or df.empty or len(df) < 2:
        return False
    ultimo_ts = int(df['timestamp'].iloc[-1])
    ahora = int(time.time() * 1000)
    diff_ms = ahora - ultimo_ts
    # Vela de 15m = 900,000 ms. Recién cerrada si diff ∈ (0, 20 min]
    if not (0 < diff_ms <= 1_200_000):
        return False
    if symbol:
        if _ULTIMA_VELA_EVALUADA.get(symbol) == ultimo_ts:
            return False  # Ya evaluada esta vela → no re-evaluar (evita re-entradas)
        _ULTIMA_VELA_EVALUADA[symbol] = ultimo_ts
    return True

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
# v4 — D4: MICROFRACTALIDAD (ondas en 5m)
# =====================================================================
def verificar_microfractalidad(df_5m: pd.DataFrame) -> dict:
    """
    D4: Detecta estructura de 5+ ondas en 5m para confirmar giro microestructural.
    Recibe velas de 5 minutos (TIMEFRAME_MICRO).
    """
    if len(df_5m) < LOBO_MICRO_LOOKBACK:
        return {'completo': False, 'razon': 'pocos_datos'}
    left, right = 3, 3
    highs = df_5m['high'].values
    lows = df_5m['low'].values
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
        # QA-FIX: normalizar naming CCXT (ts/o/h/l/c/v) → full (mismo patrón
        # que validar_estructura_d1). Anti KeyError 'high' en datos CCXT crudos.
        df_ell = df_btcd_4h
        if 'high' not in df_ell.columns and 'h' in df_ell.columns:
            df_ell = df_ell.rename(columns={'ts': 'timestamp', 'o': 'open',
                                            'h': 'high', 'l': 'low',
                                            'c': 'close', 'v': 'volume'})
        elliott = detectar_estructura_elliott_v3(df_ell)
        # BUG-M1 FIX: detectar_estructura_elliott_v3 retorna 'ultimo_pivot', no 'direccion'
        # ultimo_pivot='maximo' = BTC.D hizo techo = bajista = ventana altcoins
        if elliott.get('fase') == 'estructura_5_ondas' and elliott.get('ultimo_pivot') == 'maximo':
            result['elliott_completo'] = True
    result['ventana_altcoins'] = True
    return result


# =====================================================================
# v4 — D9: INVALIDACION H4 STRUCTURAL (cada 4h)
# =====================================================================
def debe_validar_h4() -> bool:
    """D9: Solo valida estructura H4 en los 5 min posteriores al cierre de vela H4."""
    # AUDIT-FIX: datetime.utcnow() deprecado en 3.12+; usar timezone-aware UTC
    now_utc = datetime.now(timezone.utc)
    # H4 cierra cada 4h: 00, 04, 08, 12, 16, 20 UTC
    return now_utc.hour % 4 == 0 and now_utc.minute <= 5


# =====================================================================
# A — FILTRO DE REGIMEN (tendencia 4h + D1 opcional)
# =====================================================================
def check_regime_tendencia(df_confirmacion: pd.DataFrame, es_long: bool,
                           df_d1: Optional[pd.DataFrame] = None) -> tuple[bool, str]:
    """
    FILTRO DE REGIMEN (A): solo LONG en tendencia alcista, SHORT en bajista.

    Gauge principal: EMA(LOBO_REGIME_EMA_PERIOD) sobre 4h (df_confirmacion).
    Refuerzo: EMA sobre D1 si se provee (en backtest no se pasa D1 → solo 4h).
    Es un hard gate (no suma score): si la tendencia contradice la dirección
    o 4h/D1 se desalinean, la señal se rechaza.

    Datos insuficientes o error => permisivo (True) para no matar el bot en
    arranque por falta de histórico.
    """
    if not LOBO_REGIME_FILTER:
        return True, 'REGIME:off'
    try:
        if df_confirmacion is None or 'close' not in df_confirmacion.columns:
            return True, 'REGIME:sin_datos'
        c4 = df_confirmacion['close'].dropna()
        min_rows = max(LOBO_REGIME_EMA_PERIOD // 2, 10)
        if len(c4) < min_rows:
            return True, 'REGIME:sin_datos'
        ema4 = _ema(c4, LOBO_REGIME_EMA_PERIOD)
        if not pd.isna(ema4.iloc[-1]):
            up4 = bool(float(c4.iloc[-1]) > float(ema4.iloc[-1]))
        else:
            up4 = bool(float(c4.iloc[-1]) > float(c4.mean()))
        aligned = True
        if df_d1 is not None and 'close' in df_d1.columns and len(df_d1) >= min_rows:
            c1 = df_d1['close'].dropna()
            ema1 = _ema(c1, LOBO_REGIME_EMA_PERIOD)
            if not pd.isna(ema1.iloc[-1]):
                up1 = bool(float(c1.iloc[-1]) > float(ema1.iloc[-1]))
            else:
                up1 = bool(float(c1.iloc[-1]) > float(c1.mean()))
            aligned = (up4 == up1)
        allow = (up4 if es_long else (not up4)) and aligned
        tag = ('LONG_ok' if es_long else 'SHORT_ok') if allow else 'BLOQUEADO'
        return allow, f'REGIME:{tag}:4h{"UP" if up4 else "DN"}'
    except Exception:
        return True, 'REGIME:error_permisivo'


# =====================================================================
# 6. EVALUACIÓN COMPLETA DE SEÑAL (v4 con todas las correcciones)
# =====================================================================
def evaluar_senal_bitlobo_v4(
    symbol: str, df_principal: pd.DataFrame, df_confirmacion: pd.DataFrame,
    precio_actual: float, atr_val: float, balance_total: float,
    es_long: bool, df_micro: Optional[pd.DataFrame] = None,
    ventana_altcoins: Optional[dict] = None,
    margen_real_disponible: Optional[float] = None,
    df_d1: Optional[pd.DataFrame] = None,
) -> Optional[dict]:
    """
    v4: Evalúa TODAS las reglas BITLOBO con mejoras D2-D9.

    Parámetros de dataframes (CORREGIDO v4.1 — names matching actual timeframes):
      - df_principal   : Velas de 15m (TIMEFRAME_PRINCIPAL) — Corazón del análisis
      - df_confirmacion: Velas de 4h  (TIMEFRAME_CONFIRMACION) — Validación estructural
      - df_micro       : Velas de 5m  (TIMEFRAME_MICRO) — Microfractalidad D4

    MAPA DE SCORING COMPLETO (22 puntos máximo):
    ┌──────┬──────────────────────────────────────┬──────┬─────────────────────┐
    │ Regla│ Descripción                          │ Pts  │ Condición           │
    ├──────┼──────────────────────────────────────┼──────┼─────────────────────┤
    │ R1   │ Impulso direccional detectado        │ +1   │ Pendiente > 2%      │
    │ R1b  │ Precio dentro de zona OTE (Fibonacci)│ +1   │ Entre 0.5-0.618    │
    │ R2   │ SMA 100 dentro de zona OTE           │ +1   │ SMA en 0.5-0.618   │
    │      │  (Tolerancia: ±1 ATR)               │      │ ± 1×ATR            │
    │ R3   │ ADX en rango válido                  │ +1   │ 15 ≤ ADX ≤ 50      │
    │ R4   │ USDT.D en resistencia (long)         │ +1   │ FVG/percentil 85    │
    │ R5   │ BTC.D favorables (Elliott D8)        │ +1   │ Bajista→altcoins    │
    │ R6   │ FVG en zona OTE                      │ +1   │ Gap > 0.3×ATR       │
    │ R7   │ Order Block en zona OTE              │ +1   │ Rally/Caída > 2×ATR │
    │ R8   │ Liquidity Sweep direccional          │ +1   │ Sweep correcto      │
    │ R9   │ Mecha/Absorción en zona OTE          │ +1   │ Mecha ≥ 0.5×ATR     │
    │ F5a  │ RSI en zona favorable                │ +1   │ Long<35 / Short>65  │
    │ F5b  │ Volumen validador                    │ +1   │ Ratio > 1.5× media  │
    │ F6   │ Pullback confirmado ("Rompe y Apoya")│ +1   │ Retest + rebote     │
    │ F11  │ Elliott 5 ondas estructurado         │ +1   │ Fibo ratios válidos │
    │ D3   │ CHOCH (Change of Character)          │ +1   │ Break estructura    │
    │ D2   │ Expanded Flat / Double Kill          │ +2   │ A-B-C + mecha > 15% │
    │ D4   │ Microfractalidad (5m) 5+ ondas       │ +1   │ Impulsiva direcc.   │
    │ D5   │ Flat de Continuación                 │ +1   │ Lateral + ruptura   │
    │ F10  │ Validación D1 estructural            │ +1   │ Swing points OK     │
    │ R13  │ Risk:Reward mínimo 1.5:1             │ +1   │ RR ≥ 1.5            │
    │ F3   │ Apalancamiento dinámico calculado    │ +1   │ Liq alineada mecha  │
    ├──────┼──────────────────────────────────────┼──────┼─────────────────────┤
    │      │ TOTAL MÁXIMO                         │ 22   │                     │
    └──────┴──────────────────────────────────────┴──────┴─────────────────────┘

    UMBRAL DE ENTRADA: score >= LOBO_SCORE_MIN (14/22)

    Diferencias vs v3:
      - Score max: 22 (era 16)
      - R:R mínimo: 1.5 (era 1.0)
      - Nuevos: CHOCH, Expanded Flat, Microfractalidad, Flat Continuación
      - BTC.D usa Elliott para ventana altcoins
    """
    capital_fut = capital_disponible_futuros(balance_total)
    # FIX 40762: Usar margen real si se proporciona (descuenta posiciones abiertas)
    capital_effectivo = margen_real_disponible if margen_real_disponible is not None else capital_fut
    senal = {'symbol': symbol, 'precio_actual': precio_actual, 'atr_val': atr_val, 'es_long': es_long}
    detalles = []
    score = 0
    max_score = 22

    # --- FILTRO DE REGIMEN (A): tendencia 4h (+D1) alineada con la dirección ---
    allow_regime, det_regime = check_regime_tendencia(df_confirmacion, es_long, df_d1)
    if not allow_regime:
        log.debug("%s: %s bloquea %s", symbol, det_regime, 'long' if es_long else 'short')
        return None
    detalles.append(det_regime)

    # --- R1: Impulso direccional + Fibonacci ---
    impulso = detectar_impulso(df_principal)
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

    # --- R2: SMA 100 en zona OTE (+1 punto — visible en tabla de scoring) ---
    if len(df_principal) >= 100:
        sma100 = _sma(df_principal['close'], 100).iloc[-1]
        if not pd.isna(sma100) and sma100_en_zona_ote(sma100, fibo, atr_val):
            score += 1
            detalles.append('R2:SMA100_en_OTE')

    # --- R3: ADX ---
    if adx_permite_entrada(df_principal):
        score += 1
        detalles.append('R3:ADX_ok')

    # --- R4: USDT.D ---
    # FIX-AUDIT-5: eliminado el +1 incondicional para shorts (loophole).
    if es_long:
        if check_usdtd_resistencia_long():
            score += 1
            detalles.append('R4:USDT.D_resistencia')
    else:
        if check_usdtd_resistencia_short():
            score += 1
            detalles.append('R4:USDT.D_debil')

    # --- R5: BTC.D con Elliott (D8) ---
    # FIX (Issue 1): BTC.D es una métrica RELATIVA (BTC vs alts), no absoluta.
    # BTC.D subiendo ≠ bullish para BTC (puede ser mercado bajista donde alts caen más).
    # FIX: Para BTC → usar tendencia BTC/USDT propia (SMA), ignorar BTC.D.
    #      Para alts → BTC.D bajando = capital fluye a alts = FAVORABLE.
    btcd_bajista = ventana_altcoins.get('btcd_bajista', False) if ventana_altcoins else False
    if 'BTC' in symbol:
        # BTC: R5 basado en tendencia BTC/USDT (no BTC.D relativo)
        # Usamos SMA 20 de df_principal (15m) como proxy de tendencia corta
        btc_trend_up = False
        if len(df_principal) >= 20:
            sma20 = _sma(df_principal['close'], 20)
            if not sma20.isna().all():
                btc_trend_up = bool(float(df_principal['close'].iloc[-1]) > float(sma20.iloc[-1]))
        if btc_trend_up:
            score += 1
            detalles.append('R5:BTC_trend_up')
        else:
            detalles.append('R5:BTC_trend_down')
    else:
        if btcd_bajista:
            score += 1
            detalles.append('R5:BTC.D_baja_alt_ok')
        else:
            detalles.append('R5:BTC.D_sube_bloquea_alt')

    # --- R6: FVG ---
    fvgs = detectar_fvg(df_principal)
    fvg_en_zona = [f for f in fvgs if f['gap_sup'] >= zona_inf and f['gap_inf'] <= zona_sup]
    senal['fvgs'] = fvg_en_zona
    if fvg_en_zona:
        score += 1
        detalles.append(f'R6:FVG_{len(fvg_en_zona)}')

    # --- R7: Order Block ---
    obs = detectar_order_blocks(df_principal)
    ob_en_zona = [o for o in obs if o['low'] <= zona_sup and o['high'] >= zona_inf]
    senal['obs'] = ob_en_zona
    if ob_en_zona:
        score += 1
        detalles.append(f'R7:OB_{len(ob_en_zona)}')

    # --- R8: Liquidity Sweep ---
    sweeps = detectar_sweep(df_principal)
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
    mecha_ok, mecha_det = validar_mecha_absorcion_en_zona(df_principal, zona_inf, zona_sup, es_long, atr_val)
    if not mecha_ok:
        return None
    score += 1
    detalles.append(f'R9:Mecha_{mecha_det}')

    # --- F5: RSI ---
    rsi_ok, rsi_val = filtro_rsi(df_principal, es_long)
    if rsi_ok:
        score += 1
        detalles.append(f'F5:RSI_{rsi_val:.0f}')

    # --- F5: Volumen ---
    vol_ok, vol_ratio = validar_volumen(df_principal, es_long)
    if vol_ok:
        score += 1
        detalles.append(f'F5:Vol_{vol_ratio:.1f}x')

    # --- F6: Pullback ---
    nivel_ref = zona_sup if es_long else zona_inf
    pullback_ok = detectar_pullback_confirmado(df_principal, nivel_ref, es_long)
    if pullback_ok:
        score += 1
        detalles.append('F6:Pullback_ok')

    # --- F11: Elliott ---
    elliott = detectar_estructura_elliott_v3(df_principal)
    senal['elliott'] = elliott
    if elliott['fase'] == 'estructura_5_ondas':
        score += 1
        detalles.append('F11:Elliott_5ondas')

    # --- D3: CHOCH ---
    choch = detectar_choch(df_principal, es_long)
    senal['choch'] = choch
    if choch.get('choch', False):
        score += 1
        detalles.append(f'D3:{choch["tipo"]}')

    # --- D2: Expanded Flat / Double Kill (+2 pts) ---
    exp_flat = detectar_expanded_flat(df_principal, es_long)
    senal['expanded_flat'] = exp_flat
    if exp_flat.get('encontrado', False):
        score += 2
        detalles.append(f'D2:DoubleKill_{exp_flat["tipo"]}')

    # --- D4: Microfractalidad (ondas 5m) ---
    if df_micro is not None and len(df_micro) > 0:
        micro = verificar_microfractalidad(df_micro)
        senal['microfractal'] = micro
        if micro.get('completo', False):
            if (es_long and micro.get('tipo') == 'impulsivo_alcista') or \
               (not es_long and micro.get('tipo') == 'impulsivo_bajista'):
                score += 1
                detalles.append(f'D4:micro_{micro["tipo"]}')

    # --- D5: Flat Continuación ---
    flat_cont = detectar_flat_continuacion(df_principal, es_long)
    if flat_cont:
        score += 1
        detalles.append('D5:flat_continuacion')

    # --- F10: Validación D1 ---
    # FIX-AUDIT-7: usar velas D1 REALES si se proveen (antes usaba 4h → semántica
    # incorrecta: los swing points se calculaban sobre velas de 4h, no diarias).
    df_estructura = df_d1 if (df_d1 is not None and len(df_d1) >= 10) else df_confirmacion
    if validar_estructura_d1(df_estructura, precio_actual, 'long' if es_long else 'short'):
        score += 1
        detalles.append('F10:D1_ok')
    else:
        return None

    # --- F3: Apalancamiento dinámico (anti-cacería de stops) ---
    # Calcula lev para que liq_price quede alineada con la mecha de protección,
    # evitando que manipulaciones bruscas saquen por SL sin liquidar.
    apalancamiento, liq_price = calcular_apalancamiento_optimo(
        precio_actual, df_principal, zona_inf, zona_sup, es_long, sweeps, symbol,
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

    # R:R mínimo 1.0 (TP1) — FIX-AUDIT-4: antes 0.8 (entradas con RR pobre).
    if rr < 1.0:
        return None
    if rr >= 1.2:
        score += 1
        detalles.append(f'R13:R:R_{rr:.2f}')

    # --- Position Sizing ---
    riesgo_capital = capital_effectivo * LOBO_RISK_PCT
    distancia_sl = abs(precio_actual - sl_price) / precio_actual
    if distancia_sl <= 0:
        return None
    pos_value = riesgo_capital / distancia_sl

    # FIX 40762: Cap sobre margen REAL disponible (no bruto)
    MAX_MARGIN_USE_PCT = 0.90  # Nunca usar más del 90% del margen disponible
    max_margin = capital_effectivo * MAX_MARGIN_USE_PCT
    if apalancamiento > 0:
        max_pos_value = max_margin * apalancamiento
        pos_value = min(pos_value, max_pos_value)

    # FIX 40762: Si no hay margen suficiente, rechazar señal
    margin_minimo = MIN_ORDER_USDT / apalancamiento if apalancamiento > 0 else MIN_ORDER_USDT
    if capital_effectivo < margin_minimo:
        log.debug("%s: capital efectivo %.2f < mínimo %.2f — saltando", symbol, capital_effectivo, margin_minimo)
        return None

    if pos_value < MIN_ORDER_USDT:
        pos_value = MIN_ORDER_USDT
    qty = pos_value / precio_actual
    margin_real = pos_value / apalancamiento if apalancamiento > 0 else 0

    senal['qty'] = qty
    senal['pos_value'] = pos_value
    senal['liq_price'] = liq_price
    senal['size_usdt'] = margin_real
    senal['leverage_calculado'] = apalancamiento
    senal['riesgo_real_pct'] = round((pos_value * distancia_sl) / max(capital_effectivo, 0.01) * 100, 2)
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
    # FIX-AUDIT-6: fees realistas en paper/simulación (taker 0.06% sobre notional).
    # Antes fees=0 siempre → backtests/paper optimistas (~4 fills/trade × 0.06%).
    if fees == 0 and FEE_TAKER > 0:
        qty_fee = float(entry.get('quantity', 0) or entry.get('remaining_qty', 0) or 0)
        fees = abs(exit_price * qty_fee) * FEE_TAKER
        net = raw_pnl - fees
    # FIX-AUDIT-7: racha de pérdidas consecutivas → kill-switch (solo cierres completos)
    global CONSECUTIVE_LOSSES
    if status in ('TP3', 'EXCHANGE_CLOSE') and close_reason != 'tp1_exchange' and close_reason != 'tp2_exchange':
        CONSECUTIVE_LOSSES = 0
    elif status in ('SL', 'LIQ', 'Timeout', 'D1_INVALID'):
        # AUDIT-FIX: solo alimenta el kill-switch un cierre efectivamente NEGATIVO.
        # Un cierre por Timeout/D1_INVALID con pnl >= 0 (p.ej. BE alcanzado antes)
        # no es una pérdida y no debe acumular racha (evita falsos positivos).
        if net < 0:
            CONSECUTIVE_LOSSES += 1
            if CONSECUTIVE_LOSSES >= LOBO_KILL_MAX_CONSEC_LOSSES:
                log.warning("Racha de %d pérdidas — kill-switch se armará en el próximo ciclo",
                            CONSECUTIVE_LOSSES)
        else:
            CONSECUTIVE_LOSSES = 0
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
# =====================================================================
# AUDIT-FIX (Modo A): fetch OHLCV — semáforo de concurrencia + retry 429
# TOP_N=100 × 4 timeframes = 400 llamadas en un solo asyncio.gather() sin
# límite → RateLimitExceeded (HTTP 429) masivo y timeouts en Bitget.
# Ahora: máx LOBO_FETCH_CONCURRENCY concurrentes + retry exponencial en
# rate-limit/indisponibilidad + timeout por llamada.
# =====================================================================
FETCH_CONCURRENCY = int(os.environ.get('LOBO_FETCH_CONCURRENCY', '10'))
FETCH_TIMEOUT_S   = float(os.environ.get('LOBO_FETCH_TIMEOUT_S', '15'))

async def _fetch_symbol_async(exch, symbol):
    last_err = None
    for attempt in range(3):
        try:
            ohlcv_15m = await asyncio.wait_for(
                exch.fetch_ohlcv(symbol, timeframe=TIMEFRAME_PRINCIPAL,  limit=200), FETCH_TIMEOUT_S)
            ohlcv_4h  = await asyncio.wait_for(
                exch.fetch_ohlcv(symbol, timeframe=TIMEFRAME_CONFIRMACION,  limit=100), FETCH_TIMEOUT_S)
            ohlcv_5m  = await asyncio.wait_for(
                exch.fetch_ohlcv(symbol, timeframe=TIMEFRAME_MICRO, limit=200), FETCH_TIMEOUT_S)
            ohlcv_1d  = await asyncio.wait_for(
                exch.fetch_ohlcv(symbol, timeframe='1d', limit=60), FETCH_TIMEOUT_S)  # D1 real
            return symbol, ohlcv_15m, ohlcv_4h, ohlcv_5m, ohlcv_1d
        except (ccxt_async.RateLimitExceeded, ccxt_async.ExchangeNotAvailable) as e:
            # Retry con backoff SOLO en rate-limit/indisponibilidad (el wait ayuda)
            last_err = str(e)
            wait = 2 ** attempt
            log.warning("RL/NA %s (att %d/3): retry en %ds", symbol, attempt + 1, wait)
            await asyncio.sleep(wait)
        except asyncio.TimeoutError:
            # AUDIT-FIX: en timeout no hay beneficio en backoff largo; 1 retry corto
            # y se abandona (evita ~4.4s/símbolo en caída de red masiva).
            last_err = 'timeout'
            if attempt == 0:
                log.warning("Timeout %s (att %d/2)", symbol, attempt + 1)
                await asyncio.sleep(0.5)
            else:
                break
        except Exception:
            # Error no recuperable (símbolo inválido, etc.) → devolver vacío
            return symbol, None, None, None, None
    if last_err:
        log.warning("Fetch falló %s: %s", symbol, last_err)
    return symbol, None, None, None, None

async def fetch_all_ohlcv(symbols):
    exch = ccxt_async.bitget({
        'apiKey': API_KEY, 'secret': SECRET_KEY, 'password': PASSPHRASE,
        'enableRateLimit': True, 'options': {'defaultType': 'swap'},
    })
    sem = asyncio.Semaphore(FETCH_CONCURRENCY)

    async def _wrapped(s):
        async with sem:
            return await _fetch_symbol_async(exch, s)

    try:
        results = await asyncio.gather(*[_wrapped(s) for s in symbols])
    finally:
        await exch.close()
    return {r[0]: (r[1], r[2], r[3], r[4]) for r in results}

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
def _plan_tp_qty(qty: float, step: float, tp1_price: float, tp2_price: float,
                 tp1_pct: float = TP1_CLOSE_PCT, tp2_pct: float = TP2_CLOSE_PCT,
                 min_notional: float = MIN_ORDER_USDT) -> dict:
    """QA-FIX (2026-08-10): Planifica qty de TP1/TP2/TP3 como profit_plans.

    BUG AUDITADO (reporte usuario): al abrir posición, Bitget solo mostraba
    TP1 y TP3 (adjunto). Causa: TP2 = 30% del notional se saltaba cuando
    0.30*N < min_notional ($5) mientras TP1 (40%*N) sí pasaba el umbral.
    Rango afectado: N ∈ [12.50, 16.67) USDT de posición.

    Política MERGE: si TP2 no alcanza el notional mínimo, se fusiona TP2+TP3
    (60% de la qty) en UNA profit_plan a precio TP2 → la posición queda 100%
    cubierta en exchange. Si TP1 tampoco alcanza → fallback a un solo TP a
    precio TP1 (si la qty completa cumple el mínimo), si no → 'none'.

    Retorna dict: {tp1_qty, tp2_qty, tp3_qty, tp1_pct, tp2_pct, mode}
    mode ∈ {'normal', 'merge', 'fallback', 'none', 'invalid'}
    """
    def _ok(pq: float, px: float) -> bool:
        return pq >= step and (pq * px) >= min_notional

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

    # QA-FIX: TP1 con redondeo half-up al step (evita TP1=0 con steps gruesos:
    # ej. qty=2, step=1 → 0.8 contratos no es múltiplo → redondea a 1).
    tp1_qty = math.floor(qty * tp1_pct / step + 0.5) * step
    rem = qty - tp1_qty
    tp2_qty = math.floor(rem * (tp2_pct / (1 - tp1_pct)) / step) * step
    tp3_qty = max(qty - tp1_qty - tp2_qty, 0.0)

    mode = 'normal'
    merged = False
    if _ok(tp1_qty, tp1_price) and not _ok(tp2_qty, tp2_price):
        # MERGE: TP2+TP3 → una sola profit_plan a precio TP2 (60% de la qty)
        merged_qty = math.floor((tp2_qty + tp3_qty) / step) * step
        if merged_qty >= step and (merged_qty * tp2_price) >= min_notional:
            tp2_qty = merged_qty
            tp3_qty = 0.0
            mode = 'merge'
            merged = True
    if not merged and not _ok(tp1_qty, tp1_price):
        # Fallback: todo a TP1 (solo si alcanza el mínimo; si no, nada en exchange)
        if _ok(qty, tp1_price):
            tp1_qty = math.floor(qty / step) * step
            tp2_qty = 0.0
            tp3_qty = 0.0
            mode = 'fallback'
        else:
            tp1_qty = 0.0
            tp2_qty = 0.0
            tp3_qty = 0.0
            mode = 'none'
    elif not merged and tp2_qty < step:
        # QA-FIX: granularidad del contrato impide el split (qty < 2×step):
        # TP1 único cubre lo posible, el resto lo gestiona el bot localmente.
        tp2_qty = 0.0
        tp3_qty = 0.0
        mode = 'fallback' if tp1_qty >= step else 'none'
    return {'tp1_qty': tp1_qty, 'tp2_qty': tp2_qty, 'tp3_qty': tp3_qty,
            'tp1_pct': tp1_pct, 'tp2_pct': tp2_pct, 'mode': mode}


def _place_tp_plan(sym: str, tp_price: float, tp_qty: float, side: str,
                   max_retries: int = 3, refresh_on_price_error: bool = True) -> bool:
    """Coloca una take-profit plan order vía API directa (hedge mode).

    QA-FIX (2026-08-10): retry con backoff exponencial (2s, 4s) — mismo
    patrón que _place_sl_plan (BUG #4). Error 43030 (plan ya existe) se
    trata como ÉXITO (idempotencia): antes retornaba False y hacía que TP2
    se marcara como no colocado aunque el exchange ya tuviera el plan.

    QA-FIX (2026-08-13, AUDIT TP1/TP2/TP3): errores 45060/45061/45064/45065
    ("TP price vs current/order price") significan que el MARK ya superó el
    trigger (ventana de sleep(3s)+latencia tras la entrada en alts volátiles).
    Antes: 3 reintentos con el MISMO precio → fallo permanente → TP ausente.
    Ahora: si refresh_on_price_error, se relee el mark y se re-deriva el
    trigger (mark ± buffer de 0.15%) antes del siguiente intento.
    """
    if not exchange or PAPER_TRADE:
        return False
    last_err = None
    for attempt in range(1, max_retries + 1):
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
            last_err = str(e)
            if '43030' in last_err:
                log.info("TP plan ya existe %s @ %s (attempt %d) — ok", sym, tp_price, attempt)
                return True
            # 45060/45064: LONG con trigger <= mark/order. 45061/45065: SHORT.
            # El mark ya superó el trigger → re-derivar con precio fresco.
            if refresh_on_price_error and any(c in last_err for c in ('45060', '45061', '45064', '45065')):
                try:
                    ticker = exchange.fetch_ticker(sym)
                    mark = float(ticker.get('last', 0))
                except Exception:
                    mark = 0.0
                if mark > 0:
                    if side == 'long':
                        tp_price = max(tp_price, mark * 1.0015)
                    else:
                        tp_price = min(tp_price, mark * 0.9985)
                    tp_price = float(exchange.price_to_precision(sym, tp_price))
                    log.warning("TP plan %s @ %s: %s — refresh trigger a %s (mark=%.4f)",
                                sym, tp_price, e, tp_price, mark)
            if attempt < max_retries:
                wait = 2 ** attempt
                log.warning("TP plan attempt %d/%d falló %s @ %s: %s — retry en %ds",
                            attempt, max_retries, sym, tp_price, e, wait)
                time.sleep(wait)
            else:
                log.error("TP plan FAILED tras %d intentos %s @ %s: %s",
                          max_retries, sym, tp_price, e)
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


def _cancel_sl_plans(sym: str):
    """Cancela todos los loss_plan activos de un símbolo."""
    if not exchange or PAPER_TRADE:
        return
    try:
        market_info = exchange.market(sym)
        params = {
            'productType': 'usdt-futures',
            'symbol': market_info['id'].lower(),
            'planType': 'loss_plan',
        }
        pending = exchange.privateMixGetV2MixOrderOrdersPending(params)
        for plan in (pending.get('data', {}).get('entrustedList', []) or []):
            if plan.get('planType') == 'loss_plan':
                exchange.privateMixPostV2MixOrderCancelTpslOrder({
                    'symbol': market_info['id'].lower(),
                    'productType': 'usdt-futures',
                    'marginCoin': market_info['settleId'],
                    'planType': 'loss_plan',
                    'orderId': plan['orderId'],
                })
                log.info("Cancelado SL plan %s orderId=%s", sym, plan['orderId'])
    except Exception as e:
        log.warning("Error cancelando SL plans %s: %s", sym, e)


def _place_sl_plan(sym: str, sl_price: float, sl_qty: float, side: str,
                   max_retries: int = 3) -> bool:
    """Coloca una stop-loss plan order vía API directa (hedge mode).
    
    BUG #4 FIX: Reintentos con backoff exponencial (2s, 4s, 8s).
    
    Args:
        sym: símbolo del par
        sl_price: precio de stop-loss
        sl_qty: cantidad a cerrar
        side: 'long' o 'short' (lado de la posición, NO el lado de la orden)
        max_retries: número máximo de reintentos (default 3)
    
    Returns:
        True si se colocó correctamente
    """
    if not exchange or PAPER_TRADE:
        return False
    
    last_err = None
    for attempt in range(1, max_retries + 1):
        try:
            market_info = exchange.market(sym)
            # Para hedge mode: holdSide = lado de la posición que se cierra
            params = {
                'marginCoin': market_info['settleId'],
                'productType': 'usdt-futures',
                'symbol': market_info['id'].lower(),
                'planType': 'loss_plan',
                'triggerPrice': exchange.price_to_precision(sym, sl_price),
                'triggerType': 'mark_price',
                'holdSide': side,  # 'long' o 'short'
                'size': exchange.amount_to_precision(sym, sl_qty),
            }
            exchange.privateMixPostV2MixOrderPlaceTpslOrder(params)
            log.info("SL plan placed %s @ %s qty=%s side=%s (attempt %d/%d)",
                     sym, sl_price, sl_qty, side, attempt, max_retries)
            return True
        except Exception as e:
            last_err = str(e)
            if '43030' in last_err:
                # Plan ya existe en exchange — tratar como éxito
                log.info("SL plan ya existe %s @ %s (attempt %d)", sym, sl_price, attempt)
                return True
            if attempt < max_retries:
                wait = 2 ** attempt  # backoff: 2s, 4s
                log.warning("SL plan attempt %d/%d falló %s @ %s: %s — retry en %ds",
                            attempt, max_retries, sym, sl_price, e, wait)
                time.sleep(wait)
            else:
                log.error("SL plan FAILED tras %d intentos %s @ %s: %s",
                          max_retries, sym, sl_price, e)
    return False


def _calc_pnl_parcial(side: str, entry_price: float, qty_sold: float, exit_px: float) -> float:
    """Calcula PnL para una venta parcial (extraído de _pnl_parcial anidada)."""
    if side == 'long':
        return (exit_px - entry_price) * qty_sold
    else:
        return (entry_price - exit_px) * qty_sold


def _update_sl_to_be(sym: str, entry: dict, new_sl_price: float, reason: str = 'BE') -> bool:
    """Actualiza el SL de una posición en el exchange: cancela el viejo y coloca el nuevo.
    
    BUG-C1 FIX v2: Si el nuevo SL falla tras cancelar el viejo → CERRAR posición.
    La ventana sin SL en exchange es solo la latencia entre API calls (< 1s).
    
    Args:
        sym: símbolo
        entry: dict de TRADE_ENTRIES[sym]
        new_sl_price: nuevo precio de SL
        reason: 'BE', 'TRAIL', etc.
    
    Returns:
        True si se actualizó correctamente
    """
    if not exchange or PAPER_TRADE:
        # En paper mode solo actualiza in-memory
        entry['sl_price'] = new_sl_price
        if reason == 'BE':
            ALERTS_HISTORY[f"{sym}_be_price"] = new_sl_price
        elif reason == 'TRAIL':
            ALERTS_HISTORY[f"{sym}_trail"] = new_sl_price
        log.info("[PAPER] %s SL→%.4f (%s)", sym, new_sl_price, reason)
        return True

    side = entry.get('side', 'long')
    remaining_qty = float(entry.get('remaining_qty', entry.get('quantity', 0)))
    
    if remaining_qty <= 0:
        return False

    # --- Validar precio mark antes de colocar SL ---
    # Bitget: SL para LONG debe ser < mark_price; para SHORT debe ser > mark_price
    try:
        ticker = exchange.fetch_ticker(sym)
        mark_price = float(ticker['last'])
    except Exception:
        mark_price = 0

    if mark_price > 0:
        if side == 'long' and new_sl_price >= mark_price:
            # SL por encima del mark → inválido. Ajustar a mark - 0.3%
            adjusted = mark_price * 0.997
            # AUDIT-FIX 2026-08-09 (BUG-C): un TRAIL ajustado por precio
            # inválido NUNCA debe degradar el SL actual (p.ej. el BE recién
            # cargado tras TP2). Fuzz: 797/3000 combos degradaban el BE.
            # Para 'BE' el ajuste es necesario (única forma de tener SL);
            # para 'TRAIL' se mantiene el SL actual (ya válido en exchange).
            if reason == 'TRAIL' and adjusted < float(entry.get('sl_price', 0)):
                log.warning("[SL-ADJ] %s TRAIL %.4f inválido (mark=%.4f) y ajuste "
                            "%.4f PEOR que SL actual %.4f → se mantiene SL actual",
                            sym, new_sl_price, mark_price, adjusted,
                            entry.get('sl_price', 0))
                return False
            new_sl_price = adjusted
        elif side == 'short' and new_sl_price <= mark_price:
            adjusted = mark_price * 1.003
            if reason == 'TRAIL' and adjusted > float(entry.get('sl_price', 999999)):
                log.warning("[SL-ADJ] %s TRAIL %.4f inválido (mark=%.4f) y ajuste "
                            "%.4f PEOR que SL actual %.4f → se mantiene SL actual",
                            sym, new_sl_price, mark_price, adjusted,
                            entry.get('sl_price', 999999))
                return False
            new_sl_price = adjusted

    # 1) Cancelar SL viejo PRIMERO (Bitget solo permite 1 loss_plan por símbolo)
    _cancel_sl_plans(sym)
    
    # 2) Colocar NUEVO SL plan
    placed = _place_sl_plan(sym, new_sl_price, remaining_qty, side)
    
    if not placed:
        # CRITICAL: Sin SL en exchange → cerrar posición para evitar liquidación
        log.error("[REAL] %s SL UPDATE FALLÓ (%s→%.4f) — cerrando posición (sin SL en exchange)",
                  sym, reason, new_sl_price)
        _cerrar_pos_real(sym, side, remaining_qty)
        _full_cleanup(sym)
        send_telegram(
            f"❌ *{sym} CERRADA* ({reason} falló)\n"
            f"SL→{new_sl_price} falló — posición cerrada preventivamente"
        )
        return False
    
    # 3) Actualizar in-memory solo tras confirmar que el exchange tiene el nuevo SL
    entry['sl_price'] = new_sl_price
    if reason == 'BE':
        ALERTS_HISTORY[f"{sym}_be_price"] = new_sl_price
    elif reason == 'TRAIL':
        ALERTS_HISTORY[f"{sym}_trail"] = new_sl_price
    
    log.info("[REAL] %s SL→%.4f (%s) api_ok=%s remaining=%.4f", sym, new_sl_price, reason, placed, remaining_qty)
    return placed

def _fetch_plans_exchange(sym):
    """Consulta los plan orders TPSL ACTIVOS de un símbolo en Bitget.

    Replica EXACTA del patrón que el bot ya usa en _cancel_tp_plans/_cancel_sl_plans
    (2 llamadas a privateMixGetV2MixOrderOrdersPending, una por planType).

    Returns:
        (profit_plans, loss_plans): listas de dicts {'triggerPrice': float, 'size': float}
    """
    profit, loss = [], []
    if not exchange or PAPER_TRADE:
        return profit, loss
    try:
        market_info = exchange.market(sym)
        params = {'productType': 'usdt-futures', 'symbol': market_info['id'].lower()}
        for ptype, bucket in (('profit_plan', profit), ('loss_plan', loss)):
            p = dict(params, planType=ptype)
            pending = exchange.privateMixGetV2MixOrderOrdersPending(p)
            for plan in (pending.get('data', {}).get('entrustedList', []) or []):
                if plan.get('planType') != ptype:
                    continue
                try:
                    bucket.append({'triggerPrice': float(plan.get('triggerPrice', 0)),
                                   'size': float(plan.get('size', 0))})
                except (TypeError, ValueError):
                    continue
    except Exception as e:
        log.warning("[ADOP] Error consultando planes %s: %s", sym, e)
    return profit, loss


def _atr_est_15m(sym, entry_price):
    """ATR 15m estimado (para el trailing de posiciones adoptadas).

    Fuente: OHLCV real de Bitget (100 velas 15m). Si falla → default entry*1%
    (conservador, documentado como estimación).
    """
    default = entry_price * 0.01
    if not exchange or PAPER_TRADE:
        return default
    try:
        ohlcv = exchange.fetch_ohlcv(sym, timeframe='15m', limit=100)
        if not ohlcv or len(ohlcv) < 20:
            return default
        df = pd.DataFrame(ohlcv, columns=['ts', 'open', 'high', 'low', 'close', 'volume'])
        atr_series = _atr(df, period=14)
        val = float(atr_series.dropna().iloc[-1])
        return val if val > 0 else default
    except Exception as e:
        log.warning("[ADOP] ATR 15m falló %s: %s", sym, e)
        return default


def _sl_desde_posicion(pos, side, entry_price):
    """(AUDIT-FIX 2026-08-09) SL position-level de Bitget (stopLossPrice).

    Cuando el SL se ajusta en la UI de Bitget (arrastrar en el gráfico /
    set-position-tpsl), NO aparece como loss_plan en orders-pending: viaja en la
    propia posición (fetch_positions → stopLossPrice). Este fallback lo lee.
    Válido SOLO si está del lado correcto (long: < entry, short: > entry) y > 0;
    de lo contrario devuelve None (SL desconocido → no se adopta).
    """
    try:
        sl = float(pos.get('stopLossPrice') or 0)
    except (TypeError, ValueError):
        return None
    if sl <= 0:
        return None
    if side == 'long' and sl >= entry_price:
        return None
    if side == 'short' and sl <= entry_price:
        return None
    return sl


def adoptar_posiciones_exchange():
    """(AUDIT-FIX 2026-08-09) Adopta posiciones de Bitget NO registradas en
    TRADE_ENTRIES (huérfanas tras reinicio/deploy sin JSON persistido).

    Infiere el estado EXCLUSIVAMENTE desde datos reales del exchange:
      - fetch_positions(): entryPrice, contracts, leverage, liquidationPrice, side
      - Plan orders activos: triggerPrice/size de profit_plan y loss_plan
        (privateMixGetV2MixOrderOrdersPending — el mismo endpoint que ya usa el bot)

    Inferencia CONSERVADORA (Regla de Oro — nunca inventar):
      - partial_lvl por nº de profit_plans activos: 3→0, 2→1, 1→2
      - original_qty desde contracts/(1-TP1) o contracts/(1-TP1-TP2)
      - VALIDACIÓN CRUZADA: sum(size de profit_plans) ≈ contracts (tolerancia 5%)
        y sum(size de loss_plans) ≈ contracts → si NO cuadra → NO se adopta
      - Sin loss_plan / sin profit_plans / entryPrice inválido → NO se adopta

    Devuelve el nº de posiciones adoptadas.
    """
    if not exchange or PAPER_TRADE:
        return 0
    try:
        positions = exchange.fetch_positions()
    except Exception as e:
        log.error("[ADOP] Error fetch_positions: %s", e)
        return 0

    adoptadas = 0
    for pos in positions:
        sym = pos.get('symbol')
        if not sym or sym in TRADE_ENTRIES:
            continue
        try:
            contracts = float(pos.get('contracts', 0) or 0)
            if contracts <= 0:
                continue
            side = pos.get('side')
            if side not in ('long', 'short'):
                log.warning("[ADOP] %s side=%r — NO adoptada", sym, side)
                continue
            entry_price = float(pos.get('entryPrice', 0) or 0)
            if entry_price <= 0:
                log.warning("[ADOP] %s entryPrice=%r — NO adoptada", sym, pos.get('entryPrice'))
                continue
            lev = float(pos.get('leverage') or LEVERAGE)
            if lev <= 0:
                lev = LEVERAGE
            liq = float(pos.get('liquidationPrice') or 0)
            if liq <= 0:
                liq = entry_price * (1 - 1 / lev) if side == 'long' else entry_price * (1 + 1 / lev)
            ts = pos.get('timestamp')
            entry_time = datetime.fromtimestamp(ts / 1000) if ts else datetime.now()

            profit, loss = _fetch_plans_exchange(sym)
            # Diagnóstico: si orders-pending no trae loss_plan, ¿la posición
            # trae el SL como campo propio (position-level)? (para confirmar)
            if not loss and pos.get('stopLossPrice') is not None:
                log.info("[ADOP] %s sin loss_plan en orders-pending; posición trae "
                         "stopLossPrice=%r takeProfitPrice=%r",
                         sym, pos.get('stopLossPrice'), pos.get('takeProfitPrice'))
            if not loss:
                sl_pos = _sl_desde_posicion(pos, side, entry_price)
                if sl_pos is not None:
                    # SL de posición cubre la posición completa actual
                    loss_size = contracts
                    sl_es_position_level = True
                    log.info("[ADOP] %s SL position-level (stopLossPrice=%.4f) "
                             "— aceptado como SL real", sym, sl_pos)
                else:
                    log.warning("[ADOP] %s sin loss_plan activo — NO adoptada (SL desconocido)", sym)
                    continue
            else:
                loss_size = sum(p['size'] for p in loss)
                sl_es_position_level = False
            if abs(loss_size - contracts) > contracts * 0.05:
                log.warning("[ADOP] %s loss_size %.4f != contracts %.4f — NO adoptada",
                            sym, loss_size, contracts)
                continue
            if not profit:
                log.warning("[ADOP] %s sin profit_plans activos — NO adoptada (TPs no reconstruibles)", sym)
                continue

            # SL real del exchange (loss_plan vigente o position-level)
            if sl_es_position_level:
                sl_price = sl_pos
            elif side == 'long':
                sl_price = min(loss, key=lambda p: p['triggerPrice'])['triggerPrice']
            else:
                sl_price = max(loss, key=lambda p: p['triggerPrice'])['triggerPrice']

            # Nivel por nº de profit_plans + original_qty inferida
            n_profit = len(profit)
            if n_profit == 3:
                partial_lvl, original_qty = 0, contracts
            elif n_profit == 2:
                partial_lvl, original_qty = 1, contracts / (1 - TP1_CLOSE_PCT)
            elif n_profit == 1:
                partial_lvl, original_qty = 2, contracts / (1 - TP1_CLOSE_PCT - TP2_CLOSE_PCT)
            else:
                log.warning("[ADOP] %s %d profit_plans inesperados — NO adoptada", sym, n_profit)
                continue
            profit_size = sum(p['size'] for p in profit)
            if abs(profit_size - contracts) > contracts * 0.05:
                log.warning("[ADOP] %s profit_size %.4f != contracts %.4f (lvl=%d) — NO adoptada",
                            sym, profit_size, contracts, partial_lvl)
                continue

            # TPs: precios REALES de los planes; tp1 (ya ejecutado) estimado por fórmula
            sign = 1 if side == 'long' else -1
            profit.sort(key=lambda p: p['triggerPrice'], reverse=(side == 'short'))
            if n_profit == 3:
                tp1_price, tp2_price, tp3_price = (p['triggerPrice'] for p in profit)
            elif n_profit == 2:
                tp2_price, tp3_price = profit[0]['triggerPrice'], profit[1]['triggerPrice']
                tp1_price = entry_price * (1 + sign * TP1_PNL_TARGET / lev)
            else:
                tp3_price = profit[0]['triggerPrice']
                tp2_price = entry_price * (1 + sign * TP2_PNL_TARGET / lev)
                tp1_price = entry_price * (1 + sign * TP1_PNL_TARGET / lev)

            step = 0.01
            try:
                market_info = exchange.market(sym)
                step = market_info['limits']['amount']['min'] or market_info['precision']['amount']
            except Exception:
                pass

            atr_val = _atr_est_15m(sym, entry_price)
            remaining_qty = round(contracts, 8)
            entry_record = {
                'entry_time': entry_time, 'symbol': sym, 'side': side,
                'entry_price': entry_price, 'sl_price': sl_price, 'liq_price': liq,
                'leverage': lev, 'tp1_price': tp1_price, 'tp2_price': tp2_price,
                'tp3_price': tp3_price, 'quantity': round(original_qty, 8),
                'original_qty': round(original_qty, 8), 'remaining_qty': remaining_qty,
                'step': step, 'balance_before': 0.0, 'capital_futuros': 0.0,
                'atr_val': atr_val, 'size_usdt': round(contracts * entry_price / lev, 2),
                'risk_pct': 0.0, 'score': 0, 'rr': 0.0, 'adoptada': True,
            }
            TRADE_ENTRIES[sym] = entry_record
            PARTIAL_LEVEL[sym] = partial_lvl
            _save_trade_entries()
            _save_partial_level()
            log.info("[ADOP] %s adoptada lvl=%d entry=%.4f sl=%.4f orig=%.4f rem=%.4f "
                     "tp1≈%.4f tp2=%.4f tp3=%.4f lev=%.0f atr=%.4f",
                     sym, partial_lvl, entry_price, sl_price, original_qty, remaining_qty,
                     tp1_price, tp2_price, tp3_price, lev, atr_val)
            adoptadas += 1
        except Exception as e:
            log.error("[ADOP] Error adoptando %s: %s", sym, e)
    return adoptadas


def restaurar_tp_exchange():
    """Coloca TP1/TP2/Full y SL en exchange (plan orders) para posiciones abiertas post-reinicio."""
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
            _cancel_sl_plans(sym)

            # QA-FIX (2026-08-10): misma planificación con MERGE que la apertura.
            # Garantiza TP1+TP2 (o TP1+TP2-MERGE) tras reinicio, sin perder TP2.
            tp_plan = _plan_tp_qty(original_qty, step, tp1_p, tp2_p)
            tp1_qty = tp_plan['tp1_qty']
            tp2_qty = tp_plan['tp2_qty']
            tp3_qty = tp_plan['tp3_qty']

            # TP1 si aún no se ejecutó
            if cur_qty >= original_qty * 0.85 and tp1_qty >= step:
                tp1_qty = min(tp1_qty, math.floor(cur_qty / step) * step)
                if _place_tp_plan(sym, tp1_p, tp1_qty, side):
                    log.info("%s TP1 plan restaurado: %s @ %s", sym, tp1_qty, tp1_p)

            # TP2 (con MERGE+TP3 si aplica) si aún no se ejecutó
            if cur_qty >= original_qty * 0.45 and tp2_qty >= step:
                tp2_qty = min(tp2_qty, math.floor(cur_qty / step) * step)
                if _place_tp_plan(sym, tp2_p, tp2_qty, side):
                    log.info("%s TP2 plan restaurado: %s @ %s [%s]", sym, tp2_qty, tp2_p,
                             'MERGE+TP3' if tp_plan['mode'] == 'merge' else 'normal')

            # Full TP (restante) — solo en modo normal (en merge ya se cubrió)
            if tp_plan['mode'] == 'normal' and cur_qty >= original_qty * 0.15 and tp3_qty >= step:
                tp3_qty = min(tp3_qty, math.floor(cur_qty / step) * step)
                if _place_tp_plan(sym, tp_full, tp3_qty, side):
                    log.info("%s Full TP plan restaurado: %s @ %s", sym, tp3_qty, tp_full)

            # F9: Restaurar SL plan (si no se ejecutó TP2 aún → SL original, si TP2 ya ejecutó → BE)
            current_sl = float(ed.get('sl_price', 0))
            if current_sl > 0 and cur_qty >= step:
                sl_placed = _place_sl_plan(sym, current_sl, cur_qty, side)
                if sl_placed:
                    log.info("%s SL plan restaurado: %s @ %s (partial_lvl=%d)", sym, cur_qty, current_sl, PARTIAL_LEVEL.get(sym, 0))
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
    # Cancelar TP y SL plan orders en exchange
    _cancel_tp_plans(symbol)
    _cancel_sl_plans(symbol)

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

            sl_hit = (long_side and mark <= sl_price) or (short_side and mark >= sl_price)
            liq_hit = (long_side and mark <= liq_price) or (short_side and mark >= liq_price)
            tp_full_hit = (long_side and mark >= tp3_price) or (short_side and mark <= tp3_price)

            # ── SL / LIQ: cierre completo de lo que quede ──
            if sl_hit or liq_hit:
                if remaining_qty > 0:
                    pnl = _calc_pnl_parcial(side, entry_price,remaining_qty, mark)
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
                    pnl = _calc_pnl_parcial(side, entry_price,remaining_qty, tp3_price)
                    log.info("[PAPER] %s TP3 FULL | Entry=%.4f Exit=%.4f Qty=%.4f PnL=%.2f",
                             symbol, entry_price, tp3_price, remaining_qty, pnl)
                    guardar_trade_csv(entry, tp3_price, pnl, 0, pnl, 'TP3', 'tp3')
                    send_telegram(f"[PAPER] *{symbol} TP3 FULL*\nPnL: {pnl:.2f} USDT")
                _full_cleanup(symbol)
                continue

            # ── TP1: parcial 40% (nivel 0→1) ──
            if partial_lvl == 0 and step_p > 0 and remaining_qty >= step_p:
                tp1_price = float(entry.get('tp1_price', 0))
                if tp1_price != entry_price:
                    tp1_reached = (long_side and mark >= tp1_price) or (short_side and mark <= tp1_price)
                    if tp1_reached:
                        tp1_qty = ((original_qty * TP1_CLOSE_PCT) // step_p) * step_p
                        tp1_qty = min(tp1_qty, remaining_qty - step_p)  # Reservar al menos 1 step
                        if tp1_qty >= step_p:
                            pnl = _calc_pnl_parcial(side, entry_price,tp1_qty, tp1_price)
                            entry['remaining_qty'] = remaining_qty - tp1_qty
                            PARTIAL_LEVEL[symbol] = 1
                            ALERTS_HISTORY[f"{symbol}_tp1_sold"] = True
                            log.info("[PAPER] %s TP1 (40%%) | Entry=%.4f Exit=%.4f Qty=%.4f PnL=%.2f | Restan=%.4f",
                                     symbol, entry_price, tp1_price, tp1_qty, pnl, entry['remaining_qty'])
                            guardar_trade_csv(entry, tp1_price, pnl, 0, pnl, 'TP1_PARTIAL', 'tp1')
                            send_telegram(f"[PAPER] *{symbol} TP1 (40%)*\nPnL: {pnl:.2f} USDT")
                            _save_trade_entries()
                            _save_partial_level()

            # ── TP2: parcial 30% + BE (nivel 1→2) ──
            elif partial_lvl == 1 and step_p > 0 and remaining_qty >= step_p:
                tp2_price = float(entry.get('tp2_price', 0))
                if tp2_price != entry_price:
                    tp2_reached = (long_side and mark >= tp2_price) or (short_side and mark <= tp2_price)
                    if tp2_reached:
                        remaining_after_tp1 = original_qty - ((original_qty * TP1_CLOSE_PCT) // step_p) * step_p
                        tp2_qty = ((remaining_after_tp1 * TP2_CLOSE_PCT / (1 - TP1_CLOSE_PCT)) // step_p) * step_p
                        tp2_qty = min(tp2_qty, remaining_qty - step_p)
                        if tp2_qty >= step_p:
                            pnl = _calc_pnl_parcial(side, entry_price,tp2_qty, tp2_price)
                            entry['remaining_qty'] = remaining_qty - tp2_qty
                            PARTIAL_LEVEL[symbol] = 2
                            ALERTS_HISTORY[f"{symbol}_tp2_sold"] = True
                            # F9: Mover SL a Break Even (paper: solo in-memory)
                            _update_sl_to_be(symbol, entry, entry_price, reason='BE')
                            log.info("[PAPER] %s TP2 (30%%)+BE | Entry=%.4f Exit=%.4f Qty=%.4f PnL=%.2f | Restan=%.4f",
                                     symbol, entry_price, tp2_price, tp2_qty, pnl, entry['remaining_qty'])
                            guardar_trade_csv(entry, tp2_price, pnl, 0, pnl, 'TP2_PARTIAL', 'tp2')
                            send_telegram(f"[PAPER] *{symbol} TP2 (30%)+BE*\nPnL: {pnl:.2f} USDT | SL→Entry | Restan: {entry['remaining_qty']:.4f}")
                            _save_trade_entries()
                            _save_partial_level()

            # --- Timeout (cierra remanente si perdiendo) ---
            entry_time = entry.get('entry_time')
            if isinstance(entry_time, datetime) and profit_pct < 0:
                horas = (datetime.now() - entry_time).total_seconds() / 3600
                if horas >= LOBO_TIMEOUT_HORAS:
                    remaining_qty = float(entry.get('remaining_qty', entry.get('quantity', 0)))
                    if remaining_qty > 0:
                        pnl = _calc_pnl_parcial(side, entry_price,remaining_qty, mark)
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

            # --- Trailing stop: solo después de TP2 (partial_lvl >= 2) ---
            if PARTIAL_LEVEL.get(symbol, 0) >= 2 and profit_pct > 0:
                dist = LOBO_TRAIL_ATR_MULT * entry.get('atr_val', 0) * 1.5
                if dist > 0:
                    nuevo_sl = (PEAK_PRICES[symbol] - dist) if side == 'long' else (PEAK_PRICES[symbol] + dist)
                    # AUDIT-FIX 2026-08-09 (BUG-B): comparar la mejora contra el SL
                    # ACTUAL (entry['sl_price'], que tras BE = entry) en vez del default
                    # _trail=0. Antes, el primer trailing post-BE podía emitir un SL PEOR
                    # que el BE recién cargado (p.ej. 99.5 cuando BE=100 en lev alto).
                    ultimo_sl = entry.get('sl_price', 0 if side == 'long' else 999999)
                    mejora = (nuevo_sl - ultimo_sl) if side == 'long' else (ultimo_sl - nuevo_sl)
                    if mejora > (entry_price * 0.002):
                        _update_sl_to_be(symbol, entry, nuevo_sl, reason='TRAIL')
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
    positions_ok = False  # AUDIT-FIX: distingue "sin posiciones" de "fetch falló"
    try:
        all_positions = exchange.fetch_positions()
        positions_ok = True
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

            # AUDIT-FIX (P0): Si el exchange NO tiene la posición (fetch OK), el
            # estado local está desincronizado — p.ej. tras reinicio con JSON
            # persistido (incidente 2026-08-06 21:40:20: ETH 22002 → NetworkError
            # → insufficient balance, 3 intentos de cierre en el mismo segundo).
            # Limpiar local INMEDIATAMENTE sin lanzar órdenes de cierre.
            # Solo aplica si el fetch de posiciones fue exitoso (evita borrar
            # posiciones vivas cuando el fetch falla por red).
            if positions_ok and pos_data is None:
                if remaining_qty > 0:
                    pnl = (mark - entry_price) * remaining_qty if side == 'long' else (entry_price - mark) * remaining_qty
                    log.info("[REAL] %s Posición no existe en exchange — limpiando local. PnL≈%.2f", symbol, pnl)
                    guardar_trade_csv(entry, mark, pnl, 0, pnl, 'EXCHANGE_CLOSE', 'exchange')
                _full_cleanup(symbol)
                continue

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
                    # FIX 45110: Verificar notional mínimo (Bitget: 5 USDT)
                    hedge_notional = float(hedge_params.get('size_usdt', 0))
                    if hedge_notional < MIN_ORDER_USDT:
                        log.warning("[REAL v4] %s: Cobertura notional %.2f < mínimo %.2f — saltando",
                                    symbol, hedge_notional, MIN_ORDER_USDT)
                    else:
                        log.info("[REAL v4] %s: Activando cobertura %s lev=%.0fx notional=%.2f",
                                 symbol, hedge_params['side'], hedge_params['leverage'], hedge_notional)
                        HEDGE_ENTRIES[symbol] = hedge_params
                        # Abrir cobertura real
                        try:
                            exchange.set_leverage(int(hedge_params['leverage']), symbol)
                        except Exception:
                            pass
                        try:
                            # FIX: usar step del mercado en vez de hardcodear 1
                            try:
                                hedge_market = exchange.market(symbol)
                                hedge_step = hedge_market['limits']['amount']['min'] or hedge_market['precision']['amount'] or 1
                            except Exception:
                                hedge_step = 1
                            hedge_raw_qty = hedge_notional / mark
                            hedge_qty = math.ceil(hedge_raw_qty / hedge_step) * hedge_step
                            hs = hedge_params['side']
                            hedge_tp_str = str(exchange.price_to_precision(symbol, hedge_params['tp_price']))
                            hedge_sl_str = str(exchange.price_to_precision(symbol, hedge_params['sl_price']))
                            exchange.create_order(symbol, 'market', 'buy' if hs == 'long' else 'sell',
                                                  hedge_qty, params={
                                'marginCoin': 'USDT', 'marginMode': 'isolated', 'tradeSide': 'open',
                                'presetStopSurplusPrice': hedge_tp_str,
                                'presetStopLossPrice': hedge_sl_str,
                            })
                            log.info("[REAL v4] %s: Cobertura TP=%s SL=%s colocados en Bitget",
                                     symbol, hedge_tp_str, hedge_sl_str)
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
                    log.info("[REAL] %s TP1 EXCHANGE fill. Remaining=%.4f PnL≈%.2f",
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
                    # F9: Mover SL a Break Even en el exchange (ahora en TP2)
                    _update_sl_to_be(symbol, entry, entry_price, reason='BE')
                    log.info("[REAL] %s TP2 EXCHANGE fill → BE. Remaining=%.4f PnL≈%.2f",
                             symbol, exchange_qty, tp2_pnl)
                    guardar_trade_csv(entry, tp2_p, tp2_pnl, 0, tp2_pnl, 'TP2_EXCHANGE', 'tp2_exchange')
                    _save_trade_entries()
                    _save_partial_level()
                    # AUDIT-FIX 2026-08-09 (BUG-A doble-fire): tras detectar el fill del
                    # exchange, refrescar partial_lvl y remaining_qty locales. Sin esto,
                    # el bloque TP2-local (elif partial_lvl==1) re-ejecutaba el parcial
                    # en el mismo ciclo: 2ª orden BE redundante + cierre de más qty.
                    partial_lvl = PARTIAL_LEVEL.get(symbol, 0)
                    remaining_qty = float(entry.get('remaining_qty', entry.get('quantity', 0)))

            # Re-leer sl_price después de posible BE update por TP2 exchange fill
            sl_price = float(entry.get('sl_price', 0))

            sl_hit = (long_side and mark <= sl_price) or (short_side and mark >= sl_price)
            liq_hit = (long_side and mark <= liq_price) or (short_side and mark >= liq_price)
            tp_full_hit = (long_side and mark >= tp3_price) or (short_side and mark <= tp3_price)

            # ── SL / LIQ: cierre completo de lo que quede ──
            if sl_hit or liq_hit:
                if remaining_qty > 0:
                    pnl = _calc_pnl_parcial(side, entry_price,remaining_qty, mark)
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
                    pnl = _calc_pnl_parcial(side, entry_price,remaining_qty, tp3_price)
                    log.info("[REAL] %s TP3 FULL | Entry=%.4f Exit=%.4f Qty=%.4f PnL=%.2f",
                             symbol, entry_price, tp3_price, remaining_qty, pnl)
                    _cerrar_pos_real(symbol, side, remaining_qty)
                    guardar_trade_csv(entry, tp3_price, pnl, 0, pnl, 'TP3', 'tp3')
                    send_telegram(f"[REAL] *{symbol} TP3 FULL*\nPnL: {pnl:.2f} USDT")
                _full_cleanup(symbol)
                continue

            # ── TP1: parcial 40% (nivel 0→1) — local fallback si exchange no ejecutó ──
            if partial_lvl == 0 and step_p > 0 and remaining_qty >= step_p:
                tp1_price = float(entry.get('tp1_price', 0))
                if tp1_price != entry_price:
                    tp1_reached = (long_side and mark >= tp1_price) or (short_side and mark <= tp1_price)
                    if tp1_reached:
                        tp1_qty = ((original_qty * TP1_CLOSE_PCT) // step_p) * step_p
                        tp1_qty = min(tp1_qty, remaining_qty - step_p)
                        if tp1_qty >= step_p:
                            pnl = _calc_pnl_parcial(side, entry_price,tp1_qty, tp1_price)
                            cerrado = _cerrar_pos_real(symbol, side, tp1_qty)
                            if cerrado:
                                entry['remaining_qty'] = remaining_qty - tp1_qty
                                PARTIAL_LEVEL[symbol] = 1
                                ALERTS_HISTORY[f"{symbol}_tp1_sold"] = True
                                log.info("[REAL] %s TP1 LOCAL(40%%) | Entry=%.4f Exit=%.4f Qty=%.4f PnL=%.2f | Restan=%.4f",
                                         symbol, entry_price, tp1_price, tp1_qty, pnl, entry['remaining_qty'])
                                guardar_trade_csv(entry, tp1_price, pnl, 0, pnl, 'TP1_PARTIAL', 'tp1')
                                send_telegram(f"[REAL] *{symbol} TP1 (40%)*\nPnL: {pnl:.2f} USDT")
                                _save_trade_entries()
                                _save_partial_level()
                            else:
                                log.warning("[REAL] %s TP1 parcial falló (reintentará)", symbol)

            # ── TP2: parcial 30% + BE (nivel 1→2) ──
            elif partial_lvl == 1 and step_p > 0 and remaining_qty >= step_p:
                tp2_price = float(entry.get('tp2_price', 0))
                if tp2_price != entry_price:
                    tp2_reached = (long_side and mark >= tp2_price) or (short_side and mark <= tp2_price)
                    if tp2_reached:
                        remaining_after_tp1 = original_qty - ((original_qty * TP1_CLOSE_PCT) // step_p) * step_p
                        tp2_qty = ((remaining_after_tp1 * TP2_CLOSE_PCT / (1 - TP1_CLOSE_PCT)) // step_p) * step_p
                        tp2_qty = min(tp2_qty, remaining_qty - step_p)
                        if tp2_qty >= step_p:
                            pnl = _calc_pnl_parcial(side, entry_price,tp2_qty, tp2_price)
                            cerrado = _cerrar_pos_real(symbol, side, tp2_qty)
                            if cerrado:
                                entry['remaining_qty'] = remaining_qty - tp2_qty
                                PARTIAL_LEVEL[symbol] = 2
                                ALERTS_HISTORY[f"{symbol}_tp2_sold"] = True
                                # F9: Mover SL a Break Even en el exchange (ahora en TP2)
                                _update_sl_to_be(symbol, entry, entry_price, reason='BE')
                                log.info("[REAL] %s TP2 LOCAL(30%%)+BE | Entry=%.4f Exit=%.4f Qty=%.4f PnL=%.2f | Restan=%.4f",
                                         symbol, entry_price, tp2_price, tp2_qty, pnl, entry['remaining_qty'])
                                guardar_trade_csv(entry, tp2_price, pnl, 0, pnl, 'TP2_PARTIAL', 'tp2')
                                send_telegram(f"[REAL] *{symbol} TP2 (30%)+BE*\nPnL: {pnl:.2f} USDT | SL→Entry | Restan: {entry['remaining_qty']:.4f}")
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
                        pnl = _calc_pnl_parcial(side, entry_price,remaining_qty, mark)
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

            # --- Trailing stop: solo después de TP2 (partial_lvl >= 2) ---
            if PARTIAL_LEVEL.get(symbol, 0) >= 2 and profit_pct > 0:
                dist = LOBO_TRAIL_ATR_MULT * entry.get('atr_val', 0) * 1.5
                if dist > 0:
                    nuevo_sl = (PEAK_PRICES[symbol] - dist) if side == 'long' else (PEAK_PRICES[symbol] + dist)
                    # AUDIT-FIX 2026-08-09 (BUG-B): comparar la mejora contra el SL
                    # ACTUAL (entry['sl_price'], que tras BE = entry) en vez del default
                    # _trail=0. Antes, el primer trailing post-BE podía emitir un SL PEOR
                    # que el BE recién cargado (p.ej. 99.5 cuando BE=100 en lev alto).
                    ultimo_sl = entry.get('sl_price', 0 if side == 'long' else 999999)
                    mejora = (nuevo_sl - ultimo_sl) if side == 'long' else (ultimo_sl - nuevo_sl)
                    if mejora > (entry_price * 0.002):
                        # Actualizar SL en exchange (cancela anterior, coloca nuevo)
                        # AUDIT-FIX BUG-C: solo contar/loguear si SÍ se actualizó
                        # (un TRAIL bloqueado por SL inválido no es un trailing)
                        if _update_sl_to_be(symbol, entry, nuevo_sl, reason='TRAIL'):
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
    # AUDIT-FIX 2026-08-09: adoptar posiciones huérfanas de Bitget (sin JSON
    # persistido) ANTES de restaurar planes — así entran en la gestión normal
    # (TP2→BE, trailing) con los fixes A/B/C activos.
    try:
        n_adopt = adoptar_posiciones_exchange()
        if n_adopt > 0:
            log.info("Posiciones adoptadas del exchange: %d", n_adopt)
    except Exception as e:
        log.error("Error en adoptar_posiciones_exchange: %s", e)
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
                # FIX-AUDIT-8: reporte usaba status=='TP' que NUNCA ocurre (status reales:
                # TP1_PARTIAL/TP2_PARTIAL/TP3/SL/LIQ/Timeout/D1_INVALID/EXCHANGE_CLOSE).
                # Ahora: WR y PnL se calculan SOLO sobre cierres completos (sin doble conteo
                # de parciales TP1/TP2 que ya sumaron su PnL en la fila del cierre final).
                closed = [r for r in today_trades if r['status'] in
                          ('TP3', 'SL', 'LIQ', 'Timeout', 'D1_INVALID', 'EXCHANGE_CLOSE')]
                total = len(closed)
                tps = [r for r in closed if r['status'] == 'TP3']
                sls = [r for r in closed if r['status'] != 'TP3']
                pnl_total = sum(float(r['net_pnl']) for r in closed)
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
            log.info("Balance total=%.2f | Futuros(80%%)=%.2f | Liquidez(20%%)=%.2f",
                     balance_total, capital_fut,
                     capital_liquidez(balance_total))

            # FIX (Issue 3): Refresh dominancias en background al inicio de cada ciclo
            _schedule_bg_dominance_refresh()

            # ── Gestión de posiciones activas ──
            manage_escudo_pro_v3(balance_total)

            # ── FIX-AUDIT-7: KILL-SWITCH (pausa entradas tras racha de pérdidas) ──
            global KILL_UNTIL, CONSECUTIVE_LOSSES
            if time.time() < KILL_UNTIL:
                horas_rest = (KILL_UNTIL - time.time()) / 3600
                log.warning("KILL-SWITCH activo: %.1fh restantes (racha=%d pérdidas consecutivas)",
                            horas_rest, CONSECUTIVE_LOSSES)
                time.sleep(60)
                continue
            # AUDIT-FIX: loguear la racha que DISPARÓ el kill-switch (antes se
            # mostraba racha=0 porque se reseteaba justo después de armarse).
            if CONSECUTIVE_LOSSES >= LOBO_KILL_MAX_CONSEC_LOSSES:
                KILL_STREAK_AT_TRIGGER = CONSECUTIVE_LOSSES
                KILL_UNTIL = time.time() + LOBO_KILL_COOLDOWN_H * 3600
                CONSECUTIVE_LOSSES = 0
                log.warning("KILL-SWITCH ARMADO por racha de %d pérdidas — entradas pausadas %.0fh",
                            KILL_STREAK_AT_TRIGGER, LOBO_KILL_COOLDOWN_H)
                send_telegram(
                    f"🛑 *KILL-SWITCH ACTIVADO*\n"
                    f"{LOBO_KILL_MAX_CONSEC_LOSSES} pérdidas consecutivas\n"
                    f"Entradas pausadas {LOBO_KILL_COOLDOWN_H:.0f}h"
                )
                time.sleep(60)
                continue

            # ── Posiciones activas + margen real disponible ──
            try:
                positions = exchange.fetch_positions()
                busy_symbols = {p['symbol'] for p in positions if float(p['contracts']) > 0}
            except Exception:
                positions = []
                busy_symbols = set()
            if PAPER_TRADE:
                busy_symbols.update(TRADE_ENTRIES.keys())

            # FIX 40762: Calcular margen REAL descontando posiciones abiertas (reusa la lista ya fetched)
            margen_real = calcular_margen_real_disponible(balance_total, positions_list=positions)
            log.info("Ciclo [%s] Fut=%.2f MargenReal=%.2f Ocupados=%d",
                     now.strftime('%H:%M'), capital_fut, margen_real, len(busy_symbols))

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
                if LOBO_WHITELIST:  # D: restringir a criptos reales
                    top_symbols = [s for s in top_symbols if s.split('/')[0] in LOBO_WHITELIST]
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

            # BUG-M2 FIX: Calcular ventana_altcoins UNA vez antes del loop (no N veces)
            ventana_altcoins = check_btcd_elliott_ventana_altcoins()

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
                    ohlcv_15m, ohlcv_4h, ohlcv_5m, ohlcv_1d = ohlcv_data.get(symbol, (None, None, None, None))
                    if not ohlcv_15m or not ohlcv_4h:
                        continue
                    if len(ohlcv_15m) < 50 or len(ohlcv_4h) < 10:
                        continue

                    # FIX-AUDIT-1 (CRÍTICO): la última vela de CCXT es la ABIERTA (en formación).
                    # Recortarla evita repaint/lookahead: indicadores y precio calculados solo
                    # con velas CERRADAS. El precio de entrada = close de la última cerrada.
                    df_15m = pd.DataFrame(ohlcv_15m[:-1], columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                    df_4h  = pd.DataFrame(ohlcv_4h[:-1],  columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                    df_5m  = pd.DataFrame(ohlcv_5m[:-1],  columns=['timestamp', 'open', 'high', 'low', 'close', 'volume']) if ohlcv_5m and len(ohlcv_5m) > 1 else None
                    df_1d  = pd.DataFrame(ohlcv_1d[:-1],  columns=['timestamp', 'open', 'high', 'low', 'close', 'volume']) if ohlcv_1d and len(ohlcv_1d) > 1 else None

                    # F7: Solo evaluar al cierre de vela principal (15m), una vez por vela
                    if not es_nueva_vela_principal(df_15m, symbol):
                        continue

                    precio_actual = float(df_15m['close'].iloc[-1])
                    atr_val = float(_atr(df_15m, LOBO_ATR_PERIOD).iloc[-1])
                    if atr_val == 0 or pd.isna(atr_val):
                        continue

                    # v4.1: Evaluar señal BITLOBO — names ahora coinciden con timeframes reales
                    # df_15m → df_principal (15m), df_4h → df_confirmacion (4h), df_5m → df_micro (5m)
                    senal_long = evaluar_senal_bitlobo_v4(
                        symbol, df_15m, df_4h, precio_actual, atr_val, balance_total,
                        es_long=True, df_micro=df_5m, ventana_altcoins=ventana_altcoins,
                        margen_real_disponible=margen_real, df_d1=df_1d,
                    )

                    sweeps = detectar_sweep(df_15m)
                    hay_sweep_short = any(s['tipo'] == 'sweep_alcista_short' for s in sweeps)

                    fvgs = detectar_fvg(df_15m)
                    hay_fvg_bajista = any(f['tipo'] == 'bajista' for f in fvgs)
                    rsi_series = _rsi(df_15m['close'], LOBO_RSI_PERIOD)
                    try:
                        rsi_val_actual = float(rsi_series.iloc[-1])
                    except (IndexError, ValueError):
                        rsi_val_actual = 50.0
                    hay_rsi_sobrecompra = not pd.isna(rsi_val_actual) and rsi_val_actual > LOBO_RSI_OVERBOUGHT

                    condicion_short = hay_sweep_short or hay_rsi_sobrecompra  # v5: relax (sin FVG requerido)

                    senal_short = None
                    if condicion_short:
                        senal_short = evaluar_senal_bitlobo_v4(
                            symbol, df_15m, df_4h, precio_actual, atr_val, balance_total,
                            es_long=False, df_micro=df_5m, ventana_altcoins=ventana_altcoins,
                            margen_real_disponible=margen_real, df_d1=df_1d,
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
                        # FIX: usar margen_real para calcular riesgo real sobre capital disponible
                        riesgo_ajustado = (min_qty * precio_actual * abs(precio_actual - sl_price) / precio_actual) / max(margen_real, 0.01) * 100
                        if riesgo_ajustado > 10.0:
                            log.info("%s: riesgo %.1f%% > 10%% (margen_real=%.2f), saltando",
                                     symbol, riesgo_ajustado, margen_real)
                            continue
                        raw_qty = min_qty
                    qty = math.ceil(raw_qty / step) * step
                    actual_margin = (qty * precio_actual) / lev_calc

                    # FIX 40762: Verificar que el margen NO exceda el margen REAL disponible
                    # margen_real = capital_futuros - margen_lockeado en posiciones abiertas
                    max_margin_real = margen_real * 0.90  # Nunca usar >90% del disponible
                    if max_margin_real < MIN_ORDER_USDT / lev_calc:
                        log.info("%s: margen real %.2f USDT < minimo — sin capital para abrir", symbol, max_margin_real)
                        continue
                    if actual_margin > max_margin_real:
                        log.info("%s: margin %.2f > max %.2f (real disp.), ajustando qty",
                                 symbol, actual_margin, max_margin_real)
                        qty = math.floor((max_margin_real * lev_calc / precio_actual) / step) * step
                        actual_margin = (qty * precio_actual) / lev_calc

                    # Guard: si el cap redujo qty por debajo del mínimo, saltar
                    if qty < min_qty or qty <= 0:
                        log.info("%s: qty %.6f < min_qty %.6f tras cap — capital insuficiente, saltando", symbol, qty, min_qty)
                        continue

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
                        'risk_pct': round(actual_margin / max(margen_real, 0.01) * 100, 2),
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

                    # Entrada SIN presetStopSurplusPrice (QA-FIX 2026-08-13).
                    # ANTES: preset creaba pos_profit (TP de posición COMPLETA, no 30%)
                    # → doble cobertura (100% pos_profit + 70% profit_plans = 170%)
                    # y TP3 no aparecía como plan order en Bitget ("no cargan los 3").
                    # AHORA: los 3 TPs se cargan como profit_plan con sus qtys 40/30/30.
                    params = {
                        'marginCoin': 'USDT',
                        'marginMode': 'isolated',
                        'tradeSide': 'open',
                    }
                    try:
                        exchange.create_order(symbol, 'market', 'buy' if es_long else 'sell', qty, params=params)
                    except Exception as e:
                        log.error("Error orden %s %s: %s", side_name, symbol, e)
                        COOLDOWNS[symbol] = time.time() + 14400  # 4h cooldown tras error
                        continue

                    # Colocar TP1, TP2 y TP3 como plan orders en exchange
                    # QA-FIX (2026-08-10): planificación con MERGE + diagnóstico.
                    # QA-FIX (2026-08-13): TP3 ahora se coloca como profit_plan real.
                    trade_side = 'long' if es_long else 'short'
                    tp_plan = _plan_tp_qty(qty, step, tp1_price, tp2_price)
                    tp1_qty_plan = tp_plan['tp1_qty']
                    tp2_qty_plan = tp_plan['tp2_qty']
                    tp3_qty_plan = tp_plan['tp3_qty']
                    tp1_ok = False
                    tp2_ok = False
                    tp3_ok = False
                    sl_ok = False
                    # BUG #3 FIX: sleep(1) → sleep(3) para que el position se refleje en Bitget
                    time.sleep(3)

                    def _tp_cabe(pq: float, px: float) -> bool:
                        try:
                            return (pq >= step) and (pq * px) >= MIN_ORDER_USDT
                        except TypeError:
                            return False

                    if _tp_cabe(tp3_qty_plan, tp3_price):
                        tp3_ok = _place_tp_plan(symbol, tp3_price, tp3_qty_plan, trade_side)
                        if tp3_ok:
                            log.info("[REAL] %s TP3 plan: %s @ %s (%.0f%%)",
                                     symbol, tp3_qty_plan, tp3_price, tp_plan.get('tp3_pct', 0) * 100 or 30)
                    elif tp3_qty_plan >= step:
                        log.warning("[REAL] %s TP3 remanente %s @ %s no alcanza $%.0f "
                                    "→ gestión local del remanente",
                                    symbol, tp3_qty_plan, tp3_price, MIN_ORDER_USDT)

                    if _tp_cabe(tp1_qty_plan, tp1_price):
                        tp1_ok = _place_tp_plan(symbol, tp1_price, tp1_qty_plan, trade_side)
                        if tp1_ok:
                            log.info("[REAL] %s TP1 plan: %s @ %s (%.0f%%)",
                                     symbol, tp1_qty_plan, tp1_price, tp_plan['tp1_pct'] * 100)
                    if _tp_cabe(tp2_qty_plan, tp2_price):
                        tp2_ok = _place_tp_plan(symbol, tp2_price, tp2_qty_plan, trade_side)
                        if tp2_ok:
                            log.info("[REAL] %s TP2 plan: %s @ %s (%.0f%%) [%s]",
                                     symbol, tp2_qty_plan, tp2_price, tp_plan['tp2_pct'] * 100,
                                     'MERGE+TP3' if tp_plan['mode'] == 'merge' else 'normal')

                    # FALLBACK (solo si TP1 no cabe): colocar TP completo a precio TP1
                    # QA-FIX (2026-08-13): el fallback solo aplica si NINGÚN plan parcial
                    # fue colocado (TP1/TP2/TP3 ausentes por notional o rechazo).
                    if not (tp1_ok or tp2_ok or tp3_ok):
                        fallback_qty = math.floor(qty / step) * step
                        if fallback_qty >= step and fallback_qty * tp1_price >= MIN_ORDER_USDT:
                            tp1_ok = _place_tp_plan(symbol, tp1_price, fallback_qty, trade_side)
                            if tp1_ok:
                                log.info("[REAL] %s TP1 FALLBACK full qty=%s @ %s",
                                         symbol, fallback_qty, tp1_price)

                    # BUG #5 FIX: Leer qty REAL del position tras la orden market
                    real_qty = qty
                    try:
                        _pos_check = exchange.fetch_positions([symbol])
                        for _pc in _pos_check:
                            if float(_pc.get('contracts', 0)) > 0:
                                real_qty = float(_pc['contracts'])
                                if abs(real_qty - qty) / max(qty, 1e-10) > 0.1:
                                    log.warning("[REAL] %s Qty drift: signal=%.4f real=%.4f (slippage)",
                                                symbol, qty, real_qty)
                                break
                    except Exception as _e_qty:
                        log.warning("[REAL] %s No pudo leer qty real, usando signal qty: %s", symbol, _e_qty)

                    # BUG #4 FIX: _place_sl_plan ahora tiene retry 3x con backoff
                    sl_ok = _place_sl_plan(symbol, sl_price, real_qty, trade_side)
                    if sl_ok:
                        log.info("[REAL] %s SL plan: %s @ %s [EX]", symbol, real_qty, sl_price)
                    else:
                        # BUG #1 FIX: Si SL falla tras 3 reintentos → CERRAR posición
                        log.error("[REAL] %s SL PLAN FALLÓ — cerrando posición para evitar liquidación sin SL", symbol)
                        _cerrar_pos_real(symbol, trade_side, real_qty)
                        _full_cleanup(symbol)
                        send_telegram(
                            f"❌ *{symbol} ABORTADA*\n"
                            f"SL plan falló tras 3 reintentos\n"
                            f"Posición cerrada — sin protección en exchange"
                        )
                        continue

                    # BUG #2 FIX: Telegram muestra [EX] solo si sl_ok, sino [LOCAL]
                    sl_label = '[EX]' if sl_ok else '[LOCAL]'
                    tp3_label = '[EX]' if tp3_ok else ('[MERGE→TP2]' if tp_plan['mode'] == 'merge' else '[LOCAL]')
                    send_telegram(
                        f"*{symbol} {side_name}* (BITLOBO)\n"
                        f"Entry: `{exchange.price_to_precision(symbol, precio_actual)}`\n"
                        f"Lev: {lev_calc:.0f}x | Liq: `{exchange.price_to_precision(symbol, liq_price)}`\n"
                        f"SL: `{exchange.price_to_precision(symbol, sl_price)}` {sl_label}\n"
                        f"TP1(40%): `{exchange.price_to_precision(symbol, tp1_price)}` [{'EX' if tp1_ok else 'LOCAL'}]\n"
                        f"TP2(30%): `{exchange.price_to_precision(symbol, tp2_price)}` [{'EX' if tp2_ok else 'LOCAL'}]\n"
                        f"TP3(30%): `{exchange.price_to_precision(symbol, tp3_price)}` {tp3_label}\n"
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
# 13. FLASK HEALTHCHECK (FIX Issue 4 — Render uptime)
# =====================================================================
app: Optional[object] = None  # Se inicializa en _start_healthcheck_server()

def _create_flask_app():
    """Crea Flask app con endpoints /health y /status.
    FIX Bug#9: Usa jsonify() para Content-Type application/json correcto.
    """
    flask_app = Flask("lobobot_v3")

    @flask_app.route("/health")
    def health():
        """Render healthcheck: retorna 200 si el bot está vivo."""
        from flask import jsonify
        return jsonify({"status": "ok", "uptime": time.time() - _BOT_START_TIME}), 200

    @flask_app.route("/status")
    def status():
        """Endpoint informativo: estado del bot."""
        from flask import jsonify
        return jsonify({
            "positions": len(TRADE_ENTRIES),
            "daily_stats": DAILY_STATS,
            "active_symbols": list(SESSION_ACTIVE_SYMBOLS),
            "paper_mode": PAPER_TRADE,
        }), 200

    return flask_app

_BOT_START_TIME = time.time()

def _start_healthcheck_server():
    """Inicia Flask en un thread daemon separado para no bloquear el bot.
    FIX Bug#10: Usa log.info() en vez de print().
    """
    global app
    if not _FLASK_AVAILABLE:
        log.warning("Flask no disponible — healthcheck deshabilitado (pip install flask)")
        return
    port = int(os.environ.get("PORT", 10000))  # Render asigna PORT automáticamente
    app = _create_flask_app()
    t = threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=port, use_reloader=False, debug=False),
        daemon=True,
    )
    t.start()
    log.info("Healthcheck server escuchando en puerto %d", port)

# =====================================================================
# 14. ENTRY POINT
# =====================================================================
if __name__ == "__main__":
    log.info("LOBOBOT v4 iniciando en modo standalone...")
    _start_healthcheck_server()
    if exchange is None:
        init_exchange()
    main()
