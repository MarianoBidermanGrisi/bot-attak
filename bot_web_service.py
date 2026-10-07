"""
bot_web_service.py — Web service wrapper para Render.com (canalBot).

¿QUÉ HACE ESTE ARCHIVO?
  1. Arranca el bot de trading (canalBot.main()) en un hilo daemon,
     con autoreinicio si se cae.
  2. Levanta un servidor web (Flask) con rutas de salud y webhook para
     que Render lo mantenga vivo y pueda recibir actualizaciones de Telegram.
  3. Manda un "heartbeat" (latido) cada 4 minutos a /health para que la
     instancia de Render no se duerma por inactividad.

VARIABLES DE ENTORNO QUE USA (solo estas 6 en Render):
  BITGET_API_KEY      - API Key de Bitget
  BITGET_SECRET_KEY   - Secret de Bitget
  BITGET_PASSPHRASE   - Passphrase de Bitget
  TELEGRAM_TOKEN      - Token del bot de Telegram
  TELEGRAM_CHAT_ID    - Chat de destino de las alertas
  WEBHOOK_URL         - https://coco-bot.onrender.com/webhook
                        (registro del webhook de Telegram; solo hace falta
                         si quieres RECIBIR comandos desde Telegram.
                         Para ENVIAR alertas NO es necesario: el bot sale
                         por HTTPS hacia api.telegram.org igualmente.)
  (PORT y RENDER_EXTERNAL_URL las pone Render automaticamente, no son
   variables configuradas por ti.)

ARRANQUE EN RENDER:
  Start Command:  python bot_web_service.py
  (usar UN SOLO proceso: el bot NO debe duplicarse entre workers)

COMPATIBILIDAD:
  - Se puede ejecutar en local:  python bot_web_service.py
  - Es compatible con Gunicorn (el hilo del bot arranca al importar),
    pero en ese caso usa 1 solo worker (WEB_CONCURRENCY=1).
"""

import os                      # Sistema - variables de entorno
import sys                     # Sistema - salida por stdout (Render la captura)
import time                    # Sistema - sleeps y timestamps
import json                    # Sistema - loguear actualizaciones Telegram
import asyncio                 # Sistema - bucle asincrono (usa el wrapper)
import logging                 # Sistema - logging estructurado (nunca print)
import threading               # Sistema - hilos (bot + heartbeat)
import requests                # BIBLIOTECA - peticiones HTTP (Telegram/Render)
from flask import Flask, request, jsonify, send_file  # BIBLIOTECA - servidor web

# ---------------------------------------------------------------------------
# LOGGING
#   Se configura ANTES de importar canalBot, porque logging.basicConfig solo
#   actua la primera vez. canalBot reutilizara estos mismos handlers y sus
#   mensajes saldran aqui (formato y nivel de INFO hacia arriba).
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    stream=sys.stdout,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("bot_web_service")

# Silenciar logs HTTP de los health checks (si no, ensucian cada 4 minutos)
logging.getLogger("werkzeug").setLevel(logging.CRITICAL)
logging.getLogger("gunicorn.access").setLevel(logging.CRITICAL)
logging.getLogger("gunicorn.error").setLevel(logging.CRITICAL)

# ---------------------------------------------------------------------------
# FLASK (DEBE crearse antes de arrancar cualquier hilo)
# ---------------------------------------------------------------------------
app = Flask(__name__)

# Token de Telegram (para registrar/desregistrar el webhook)
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")

# ---------------------------------------------------------------------------
# HEARTBEAT - latido contra /health cada 4 minutos (keep-alive en Render)
#   Render duerme los servicios gratuitos por inactividad; este latido lo
#   mantiene despierto. Usa RENDER_EXTERNAL_URL (la URL que asigna Render).
# ---------------------------------------------------------------------------
RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL", "")


def _self_heartbeat():
    """Hilo: GET a /health cada 240 s. Si no hay URL, no hace nada."""
    if not RENDER_URL:
        return
    url = f"{RENDER_URL}/health"
    while True:
        time.sleep(240)
        try:
            requests.get(url, timeout=10)
        except Exception:
            pass  # red caida momentanea: se reintenta en el proximo ciclo


# ---------------------------------------------------------------------------
# RUTAS DEL SERVIDOR WEB
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    """Pagina de bienvenida: solo confirma que el servicio esta en linea."""
    return "canalBot (SignalVWAP + Canal) -- en linea.", 200


@app.route("/health", methods=["GET"])
def health_check():
    """Salud del servicio: lo usan Render (ping) y el propio heartbeat."""
    return jsonify({
        "status": "running",
        "timestamp": time.time(),
        "bot": "canalBot",
    }), 200


@app.route("/webhook", methods=["POST"])
def telegram_webhook():
    """Recibe actualizaciones de Telegram (solo si registras WEBHOOK_URL).
    canalBot solo ENVIA alertas; aqui se aceptan y se descartan con 200 OK
    para que Telegram no reintente. Podria usarse para comandos futuros."""
    if request.is_json:
        update = request.get_json()
        logger.info(f"Telegram update: {json.dumps(update)}")
        return jsonify({"status": "ok"}), 200
    return jsonify({"error": "Request must be JSON"}), 400


@app.route("/trades")
def download_trades():
    """Descarga el historial de trades (trades.csv) que genera canalBot."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trades.csv")
    if os.path.exists(path):
        return send_file(path, as_attachment=True, download_name="trades.csv")
    return "No hay trades aun", 404


# ---------------------------------------------------------------------------
# CONFIGURACION DEL WEBHOOK DE TELEGRAM
#   Solo registra la URL donde Telegram mandara actualizaciones.
#   NO hace falta para enviar alertas (esas salen por HTTPS saliente).
# ---------------------------------------------------------------------------
def setup_telegram_webhook():
    """Registra WEBHOOK_URL (o RENDER_EXTERNAL_URL/webhook) en Telegram."""
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
        requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteWebhook",
            timeout=10,
        )
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


# ---------------------------------------------------------------------------
# LANZADOR DEL BOT (HILO CON AUTOREINICIO)
# ---------------------------------------------------------------------------
def run_bot():
    """Importa y ejecuta canalBot.main() en bucle.

    canalBot.main() (seccion [12] de canalBot.py) hace:
      - banner de arranque,
      - crea el cliente Bitget/Telegram (CanalBot),
      - lanza el escaneo TOP100 + estrategia SignalVWAP (loop_principal).

    Si termina con error, se espera 30 s y se vuelve a lanzar (uptime 99.9%).
    """
    try:
        from canalBot import main as canalbot_main
    except Exception as e:
        logger.error(f"Error importando canalBot: {e}")
        return

    while True:
        logger.info("Iniciando canalBot (main)...")
        try:
            canalbot_main()
        except Exception as e:
            logger.error(f"canalBot termino con error: {e}")
        logger.info("Reiniciando bot en 30 segundos...")
        time.sleep(30)


# ---------------------------------------------------------------------------
# ARRANQUE DE LOS HILOS (al importar el modulo -> compatible con Gunicorn)
#   Los hilos son daemon: mueren solos cuando Render apaga el proceso.
# ---------------------------------------------------------------------------
try:
    heartbeat_thread = threading.Thread(
        target=_self_heartbeat, daemon=True, name="Heartbeat")
    heartbeat_thread.start()
    logger.info("Heartbeat thread started (interval=4min)")
except Exception as e:
    logger.error(f"Error starting Heartbeat thread: {e}")

try:
    bot_thread = threading.Thread(
        target=run_bot, daemon=True, name="BotRunner")
    bot_thread.start()
    logger.info("BotRunner thread started (canalBot)")
except Exception as e:
    logger.error(f"Error starting BotRunner thread: {e}")


# ---------------------------------------------------------------------------
# INICIO DEL SISTEMA (ejecucion directa: python bot_web_service.py)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # Registrar el webhook de Telegram (opcional: solo para RECIBIR mensajes)
    setup_telegram_webhook()

    # Render asigna el puerto en la variable PORT (obligatorio escuchar ahi)
    port = int(os.environ.get("PORT", 5000))
    logger.info(f"Iniciando servidor Flask en el puerto {port}...")
    app.run(debug=False, host="0.0.0.0", port=port)
