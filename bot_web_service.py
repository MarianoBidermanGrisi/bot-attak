#!/usr/bin/env python3
"""
bot_web_service.py — Punto de entrada para Render Web Service.
=============================================================
Importa el monolito BOTZG y ejecuta:
  1. Servidor Flask (health checks, uptime)
  2. Bot de trading en segundo plano (thread + asyncio)

Uso en Render:
    gunicorn bot_web_service:app

Endpoints:
    GET /         → "BOTZG - online"
    GET /health   → JSON status
    GET /status   → JSON bot status + uptime
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

# ── Importar el monolito ──────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import botzg

# ── Flask App ─────────────────────────────────────────────────
try:
    from flask import Flask, jsonify
except ImportError:
    log.error("Flask no instalado. Ejecuta: pip install flask gunicorn")
    raise

app = Flask(__name__)

# Estado global del bot
BOT_ACTIVE = False
BOT_STARTED_AT = None


@app.route("/")
def index():
    return "BOTZG - MoneyZG Scalping Bot - online", 200


@app.route("/health")
def health():
    uptime = round(time.time() - BOT_STARTED_AT, 1) if BOT_STARTED_AT else 0
    return jsonify({
        "status": "running",
        "bot": "botzg",
        "active": BOT_ACTIVE,
        "uptime": uptime,
        "strategy": f"MACD({botzg.MACD_FAST},{botzg.MACD_SLOW},{botzg.MACD_SIGNAL}) SMA({botzg.SMA_LONG})",
        "sl_pct": botzg.SL_PCT * 100,
        "tp_pct": botzg.TP_MIN_PCT * 100,
        "leverage": botzg.LEVERAGE,
        "paper_mode": botzg.PAPER_TRADE,
    })


@app.route("/status")
def status_handler():
    uptime = round(time.time() - BOT_STARTED_AT, 1) if BOT_STARTED_AT else 0
    return jsonify({
        "bot_active": BOT_ACTIVE,
        "uptime_seconds": uptime,
        "started_at": BOT_STARTED_AT,
        "paper_mode": botzg.PAPER_TRADE,
    })


@app.route("/config")
def config_handler():
    """Devuelve la configuración actual del bot."""
    return jsonify({
        "timeframe": botzg.ENTRY_TIMEFRAME,
        "macd": {"fast": botzg.MACD_FAST, "slow": botzg.MACD_SLOW, "signal": botzg.MACD_SIGNAL},
        "sma_long": botzg.SMA_LONG,
        "sl_pct": botzg.SL_PCT * 100,
        "tp_min_pct": botzg.TP_MIN_PCT * 100,
        "leverage": botzg.LEVERAGE,
        "max_positions": botzg.MAX_POSITIONS,
        "max_trades_day": botzg.MAX_TRADES_DAY,
        "paper_trade": botzg.PAPER_TRADE,
        "poll_interval_sec": botzg.POLL_INTERVAL_SEC,
        "top_n": botzg.TOP_N,
    })


# ── Iniciar bot en segundo plano ──────────────────────────────
def _start_bot():
    global BOT_ACTIVE, BOT_STARTED_AT
    BOT_STARTED_AT = time.time()
    BOT_ACTIVE = True
    log.info("BOTZG worker started in background thread")

    try:
        # web_worker() crea su propio event loop y corre bot.live()
        botzg.BOTZG.web_worker()
    except Exception as e:
        log.error("BOTZG worker error: %s", e, exc_info=True)
    finally:
        BOT_ACTIVE = False
        log.info("BOTZG worker stopped")


bot_thread = threading.Thread(target=_start_bot, daemon=True, name="BOTZG")
bot_thread.start()
log.info("BOTZG thread launched")


# ── Entry point ───────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    log.info("Starting Flask on 0.0.0.0:%d", port)
    app.run(host="0.0.0.0", port=port, debug=False)
