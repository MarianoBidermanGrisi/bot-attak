#!/usr/bin/env python3
"""
bot_web_service.py — Punto de entrada para Render Web Service (LOBOBOT v4)
==========================================================================
Importa lobobot_v3 (BITLOBO v4 con F1-F12 + D2-D9) y ejecuta:
  1. Servidor Flask (health checks, uptime, config)
  2. Bot de trading BITLOBO v4 en segundo plano (thread + asyncio)

Uso en Render (Procfile):
    web: gunicorn bot_web_service:app --timeout 120 --workers 1 --threads 2

Uso local:
    python bot_web_service.py

Endpoints:
    GET /         → "LOBOBOT v4 - online"
    GET /health   → JSON status + config BITLOBO v4
    GET /status   → JSON bot status + uptime
    GET /config   → JSON config completa de las 22 reglas
"""
import os
import sys
import time
import json
import logging
import threading

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("web")
logging.getLogger("werkzeug").setLevel(logging.WARNING)

# ── Importar lobobot_v3 (v4) ──────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lobobot_v3 as lobobot

# ── Flask App ──────────────────────────────────────────────────
try:
    from flask import Flask, jsonify
except ImportError:
    log.error("Flask no instalado. pip install flask gunicorn")
    raise

app = Flask(__name__)

# Estado global del web service
BOT_ACTIVE = False
BOT_STARTED_AT = None

# ── Endpoints ──────────────────────────────────────────────────
@app.route("/")
def index():
    return "LOBOBOT v4 (BITLOBO F1-F12 + D2-D9) - online", 200

@app.route("/health")
def health():
    uptime = round(time.time() - BOT_STARTED_AT, 1) if BOT_STARTED_AT else 0
    return jsonify({
        "status": "running",
        "bot": "lobobot_v4",
        "strategy": "BITLOBO_22_REGLAS",
        "active": BOT_ACTIVE,
        "uptime_seconds": uptime,
        "paper_mode": lobobot.PAPER_TRADE,
        "top_n": lobobot.TOP_N,
        "active_positions": len(lobobot.TRADE_ENTRIES),
    })

@app.route("/status")
def status_handler():
    uptime = round(time.time() - BOT_STARTED_AT, 1) if BOT_STARTED_AT else 0
    return jsonify({
        "bot_active": BOT_ACTIVE,
        "uptime_seconds": uptime,
        "started_at": BOT_STARTED_AT,
        "paper_mode": lobobot.PAPER_TRADE,
        "active_symbols": list(lobobot.TRADE_ENTRIES.keys()),
        "active_count": len(lobobot.TRADE_ENTRIES),
        "cooldown_count": len(lobobot.COOLDOWNS),
        "hedge_active": list(lobobot.HEDGE_ENTRIES.keys()),
        "partial_levels": dict(lobobot.PARTIAL_LEVEL),
    })

@app.route("/config")
def config_handler():
    return jsonify({
        # Escaneo
        "top_n": lobobot.TOP_N,
        "timeframes": {
            "principal_15m": lobobot.TIMEFRAME_PRINCIPAL,
            "confirmacion_4h": lobobot.TIMEFRAME_CONFIRMACION,
            "micro_5m": lobobot.TIMEFRAME_MICRO,
        },
        # Capital split (F1)
        "capital_split": {
            "liquidez_pct": round(lobobot.LOBO_LIQUIDEZ_PCT * 100, 1),
            "futuros_pct": round(lobobot.LOBO_FUTUROS_PCT * 100, 1),
        },
        # Reglas BITLOBO v4 (22 puntos)
        "rules": {
            "R1_impulso": {
                "min_velas": lobobot.LOBO_IMPULSO_MIN_VELAS,
                "max_velas": lobobot.LOBO_IMPULSO_MAX_VELAS,
                "pendiente_min_pct": lobobot.LOBO_IMPULSO_PEND_MIN * 100,
            },
            "R2_sma100_tolerancia_atr": lobobot.LOBO_SMA100_TOL_ATR,
            "R3_adx": {
                "periodo": lobobot.LOBO_ADX_PERIOD,
                "rango": [lobobot.LOBO_ADX_MIN, lobobot.LOBO_ADX_MAX],
                "descendente_velas": lobobot.LOBO_ADX_DESC_VELAS,
            },
            "R5_rsi": {
                "periodo": lobobot.LOBO_RSI_PERIOD,
                "oversold": lobobot.LOBO_RSI_OVERSOLD,
                "overbought": lobobot.LOBO_RSI_OVERBOUGHT,
            },
            "R6_fvg": {
                "min_gap_atr": lobobot.LOBO_FVG_MIN_GAP_ATR,
                "max_velas_sin_rellenar": lobobot.LOBO_FVG_MAX_VELAS,
            },
            "R7_order_block": {
                "min_mov_atr": lobobot.LOBO_OB_MIN_MOV_ATR,
                "lookback": lobobot.LOBO_OB_LOOKBACK,
            },
            "R8_sweep": {
                "lookback": lobobot.LOBO_SWEEP_LOOKBACK,
                "max_penetracion_atr": lobobot.LOBO_SWEEP_MAX_PEN_ATR,
            },
            "R9_absorcion": {
                "mecha_min_atr": lobobot.LOBO_MECHA_MIN_ATR,
                "cuerpo_mecha_ratio": lobobot.LOBO_MECHA_CUERPO_RATIO,
            },
            "D2_expanded_flat": "+2 pts si encontrado",
            "D3_choch": "+1 pts Change of Character",
            "D4_microfractalidad": "+1 pts 5+ ondas en 5m",
            "D5_flat_continuacion": "+1 pts lateral post-ruptura",
        },
        # Riesgo (F8)
        "risk": {
            "risk_pct": round(lobobot.LOBO_RISK_PCT * 100, 2),
            "risk_pct_exceptional": round(lobobot.LOBO_RISK_PCT_EXCEP * 100, 2),
            "max_positions": lobobot.LOBO_MAX_POSITIONS,
            "paper_trade": lobobot.PAPER_TRADE,
        },
        # TP/SL (F12 PnL-based)
        "tp_sl": {
            "tp1_pnl_target": round(lobobot.TP1_PNL_TARGET * 100, 1),
            "tp2_pnl_target": round(lobobot.TP2_PNL_TARGET * 100, 1),
            "tp3_pnl_target": round(lobobot.TP3_PNL_TARGET * 100, 1),
            "tp1_close_pct": round(lobobot.TP1_CLOSE_PCT * 100, 1),
            "tp2_close_pct": round(lobobot.TP2_CLOSE_PCT * 100, 1),
            "sl_atr": lobobot.LOBO_SL_ATR,
        },
        # F3: Apalancamiento dinámico + F4: Cobertura
        "leverage_hedge": {
            "hedge_enabled": lobobot.LOBO_HEDGE_ENABLED,
            "hedge_margin_pct": round(lobobot.LOBO_HEDGE_MARGIN_PCT * 100, 1),
            "hedge_trigger_pct": round(lobobot.LOBO_HEDGE_TRIGGER_PCT * 100, 1),
        },
        # Scoring
        "scoring": {
            "max_score": 22,
            "min_score": lobobot.LOBO_SCORE_MIN,
        },
    })

# ── Iniciar bot en segundo plano ───────────────────────────────
def _start_bot():
    global BOT_ACTIVE, BOT_STARTED_AT
    BOT_STARTED_AT = time.time()
    BOT_ACTIVE = True
    log.info("LOBOBOT v4 worker started in background thread")
    try:
        if lobobot.exchange is None:
            lobobot.init_exchange()
        lobobot.main()
    except Exception as e:
        log.error("LOBOBOT v4 worker error: %s", e, exc_info=True)
    finally:
        BOT_ACTIVE = False
        log.info("LOBOBOT v4 worker stopped")

bot_thread = threading.Thread(target=_start_bot, daemon=True, name="LOBOBOT_v4")
bot_thread.start()
log.info("LOBOBOT v4 thread launched from bot_web_service")

# ── Entry point directo ────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    log.info("Starting Flask on 0.0.0.0:%d", port)
    app.run(host="0.0.0.0", port=port, debug=False)
