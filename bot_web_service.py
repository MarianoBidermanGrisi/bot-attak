"""
BotBB — Web service wrapper para Render.com.
Importa y ejecuta BotBBEngine en un hilo daemon.
Compatible con Gunicorn.
"""

import os
import sys
import time
import json
import asyncio
import logging
import threading
import requests
from flask import Flask, request, jsonify

# Silenciar logs HTTP de health checks
logging.getLogger("werkzeug").setLevel(logging.CRITICAL)
logging.getLogger("gunicorn.access").setLevel(logging.CRITICAL)
logging.getLogger("gunicorn.error").setLevel(logging.CRITICAL)

# Configuracion de Logging
logging.basicConfig(
    level=logging.INFO,
    stream=sys.stdout,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Inicializar Flask
app = Flask(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")

# ==============================================================
#  HEARTBEAT (keep-alive contra URL publica de Render)
# ==============================================================
RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL", "")


def _self_heartbeat():
    if not RENDER_URL:
        return
    url = f"{RENDER_URL}/health"
    while True:
        time.sleep(240)
        try:
            requests.get(url, timeout=10)
        except Exception:
            pass


# ==============================================================
#  RUTAS DEL SERVIDOR WEB (FLASK)
# ==============================================================
@app.route("/")
def index():
    return "BotBB + Render -- en linea.", 200


@app.route("/health", methods=["GET"])
def health_check():
    return jsonify({
        "status": "running",
        "timestamp": time.time(),
        "bot": "BotBB",
    }), 200


@app.route("/webhook", methods=["POST"])
def telegram_webhook():
    if request.is_json:
        update = request.get_json()
        logger.info(f"Telegram update: {json.dumps(update)}")
        return jsonify({"status": "ok"}), 200
    return jsonify({"error": "Request must be JSON"}), 400


# ==============================================================
#  CONFIGURACION DE WEBHOOK
# ==============================================================
def setup_telegram_webhook():
    if not TELEGRAM_TOKEN:
        logger.warning("No hay TELEGRAM_TOKEN configurado.")
        return

    webhook_url = os.environ.get("WEBHOOK_URL")
    if not webhook_url:
        render_url = os.environ.get("RENDER_EXTERNAL_URL")
        if render_url:
            webhook_url = f"{render_url}/webhook"
        else:
            logger.warning("RENDER_EXTERNAL_URL no definida -- webhook omitido.")
            return

    try:
        logger.info(f"Registrando webhook Telegram: {webhook_url}")
        requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteWebhook", timeout=10)
        time.sleep(1)
        r = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook?url={webhook_url}",
            timeout=10,
        )
        if r.status_code == 200:
            logger.info("Webhook de Telegram registrado correctamente")
        else:
            logger.error(f"Error al registrar webhook: {r.status_code} -- {r.text}")
    except Exception as e:
        logger.error(f"Excepcion al configurar webhook: {e}")


# ==============================================================
#  LANZADOR DEL BOT (DIRECTO EN THREAD)
# ==============================================================
def run_bot():
    """Importa y ejecuta BotBBEngine con autoreinicio."""
    from botbb_engine import BotBBEngine

    while True:
        logger.info("Iniciando BotBBEngine...")
        try:
            engine = BotBBEngine()
            asyncio.run(engine.run())  # engine.run() es async
        except Exception as e:
            logger.error(f"BotBBEngine termino con error: {e}")
        logger.info("Reiniciando bot en 30 segundos...")
        time.sleep(30)


# Iniciar heartbeat interno
heartbeat_thread = threading.Thread(target=_self_heartbeat, daemon=True, name="Heartbeat")
heartbeat_thread.start()
logger.info("Heartbeat thread started (interval=4min)")

# Iniciar el hilo del bot automaticamente (compatible con Gunicorn)
bot_thread = threading.Thread(target=run_bot, daemon=True, name="BotRunner")
bot_thread.start()

# ==============================================================
#  INICIO DEL SISTEMA (LOCAL)
# ==============================================================
if __name__ == "__main__":
    setup_telegram_webhook()
    port = int(os.environ.get("PORT", 5000))
    logger.info(f"Iniciando servidor Flask en el puerto {port}...")
    app.run(debug=False, host="0.0.0.0", port=port)
