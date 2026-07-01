#!/usr/bin/env python3
"""
bot_web_service.py — Punto de entrada para Render Web Service (LOBOBOT v2)
==========================================================================
Importa lobobot v2 (BITLOBO formalizado) y ejecuta:
  1. Servidor Flask (health checks, uptime, config)
  2. Bot de trading BITLOBO v2 en segundo plano (thread + asyncio)

Uso en Render (Procfile):
    web: gunicorn bot_web_service:app --timeout 120 --workers 1 --threads 2

Uso local:
    python bot_web_service.py

Endpoints:
    GET /         → "LOBOBOT v2 - online"
    GET /health   → JSON status + config BITLOBO v2
    GET /status   → JSON bot status + uptime
    GET /config   → JSON config completa de las 18 reglas
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

# ── Importar el monolito v2 ────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lobobot

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
    return "LOBOBOT v2 (BITLOBO FORMALIZADO) - online", 200

@app.route("/health")
def health():
    uptime = round(time.time() - BOT_STARTED_AT, 1) if BOT_STARTED_AT else 0
    return jsonify({
        "status": "running",
        "bot": "lobobot_v2",
        "strategy": "BITLOBO_18_REGLAS",
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
    })

@app.route("/config")
def config_handler():
    return jsonify({
        # Escaneo
        "top_n": lobobot.TOP_N,
        "timeframes": {
            "h4": lobobot.TIMEFRAME_4H,
            "d1": lobobot.TIMEFRAME_D1,
        },
        # Reglas BITLOBO
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
        },
        # Riesgo
        "risk": {
            "risk_pct": round(lobobot.LOBO_RISK_PCT * 100, 2),
            "max_positions": lobobot.LOBO_MAX_POSITIONS,
            "leverage": lobobot.LEVERAGE,
            "paper_trade": lobobot.PAPER_TRADE,
        },
        # TP/SL
        "tp_sl": {
            "tp1_size_pct": lobobot.LOBO_TP1_SIZE * 100,
            "tp2_size_pct": lobobot.LOBO_TP2_SIZE * 100,
            "tp3_size_pct": lobobot.LOBO_TP3_SIZE * 100,
            "tp2_atr_mult": lobobot.LOBO_TP2_ATR_MULT,
            "tp3_atr_mult": lobobot.LOBO_TP3_ATR_MULT,
        },
        # BE + Trailing
        "be_trailing": {
            "be_trigger_pct": round(lobobot.LOBO_BE_TRIGGER_PCT * 100, 2),
            "be_offset_pct": round(lobobot.LOBO_BE_OFFSET_PCT * 100, 2),
            "trail_atr_mult": lobobot.LOBO_TRAIL_ATR_MULT,
        },
    })

# ── Iniciar bot en segundo plano ───────────────────────────────
def _start_bot():
    global BOT_ACTIVE, BOT_STARTED_AT
    BOT_STARTED_AT = time.time()
    BOT_ACTIVE = True
    log.info("LOBOBOT v2 worker started in background thread")
    try:
        if lobobot.exchange is None:
            lobobot.init_exchange()
        lobobot.main()
    except Exception as e:
        log.error("LOBOBOT v2 worker error: %s", e, exc_info=True)
    finally:
        BOT_ACTIVE = False
        log.info("LOBOBOT v2 worker stopped")

bot_thread = threading.Thread(target=_start_bot, daemon=True, name="LOBOBOT_v2")
bot_thread.start()
log.info("LOBOBOT v2 thread launched from bot_web_service")

# ── Entry point directo ────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    log.info("Starting Flask on 0.0.0.0:%d", port)
    app.run(host="0.0.0.0", port=port, debug=False)
