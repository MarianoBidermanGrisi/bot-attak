"""
Web service wrapper para Render.com.

Mantiene Flask activo para health checks y ejecuta el bot en un subprocess.
Por defecto lanza la copia v2 en combined_strategy_v2/live_bot.py, sin cambiar
el Start Command de Render. Para volver al bot original, define BOT_VERSION=original.
"""

import os
import sys
import time
import json
import logging
import threading
import subprocess
import requests
from flask import Flask, request, jsonify

# Silenciar completamente logs HTTP de health checks (werkzeug + gunicorn)
logging.getLogger("werkzeug").setLevel(logging.CRITICAL)
logging.getLogger("gunicorn.access").setLevel(logging.CRITICAL)
logging.getLogger("gunicorn.error").setLevel(logging.CRITICAL)

# Configuración de Logging
logging.basicConfig(
    level=logging.INFO,
    stream=sys.stdout,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Inicializar Flask
app = Flask(__name__)

# Token de Telegram desde variables de entorno
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BOT_VERSION = os.environ.get('BOT_VERSION', 'v2').lower()
BOT_SCRIPT_OVERRIDE = os.environ.get('BOT_SCRIPT_PATH')
BOT_PROCESS = None
BOT_STARTED_AT = None


def resolve_bot_script():
    """Devuelve el script del bot manteniendo compatibilidad con la config actual de Render."""
    if BOT_SCRIPT_OVERRIDE:
        return BOT_SCRIPT_OVERRIDE if os.path.isabs(BOT_SCRIPT_OVERRIDE) else os.path.join(BASE_DIR, BOT_SCRIPT_OVERRIDE)
    if BOT_VERSION == 'original':
        return os.path.join(BASE_DIR, "combined_strategy_bot.py")
    return os.path.join(BASE_DIR, "combined_strategy_v2", "live_bot.py")

# ==============================================================
#  HEARTBEAT INTERNO (keep-alive sin cron-job)
# ==============================================================
def _self_heartbeat():
    """Ping /health cada ~4 min para mantener Render activo.
    Sin logs en exito — solo WARNING si falla."""
    port = os.environ.get('PORT', '5000')
    url = f"http://localhost:{port}/health"
    while True:
        time.sleep(240)
        try:
            requests.get(url, timeout=10)
        except Exception:
            pass  # Silencio total en exito

# ==============================================================
#  RUTAS DEL SERVIDOR WEB (FLASK)
# ==============================================================
@app.route('/')
def index():
    """Ping basico - Render lo usa para verificar que el servicio responde."""
    return "Combined Strategy Bot + Render - en linea.", 200

@app.route('/health', methods=['GET'])
def health_check():
    """Health Check Path para Render."""
    bot_script = resolve_bot_script()
    bot_running = BOT_PROCESS is not None and BOT_PROCESS.poll() is None
    return jsonify({
        "status": "running",
        "timestamp": time.time(),
        "bot": "combined_strategy",
        "bot_version": BOT_VERSION,
        "bot_script": bot_script,
        "bot_process_running": bot_running,
        "bot_pid": BOT_PROCESS.pid if bot_running else None,
        "bot_started_at": BOT_STARTED_AT
    }), 200

@app.route('/webhook', methods=['POST'])
def telegram_webhook():
    """Recibe updates de Telegram vía webhook (POST JSON)."""
    if request.is_json:
        update = request.get_json()
        logger.info(f"Telegram update: {json.dumps(update)}")
        return jsonify({"status": "ok"}), 200
    return jsonify({"error": "Request must be JSON"}), 400

# ==============================================================
#  CONFIGURACIÓN DE WEBHOOK
# ==============================================================
def setup_telegram_webhook():
    """Configura el webhook de Telegram usando RENDER_EXTERNAL_URL."""
    if not TELEGRAM_TOKEN:
        logger.warning("No hay TELEGRAM_TOKEN configurado.")
        return

    webhook_url = os.environ.get('WEBHOOK_URL')
    if not webhook_url:
        render_url = os.environ.get('RENDER_EXTERNAL_URL')
        if render_url:
            webhook_url = f"{render_url}/webhook"
        else:
            logger.warning("RENDER_EXTERNAL_URL no definida; webhook omitido.")
            return

    try:
        logger.info(f"Registrando webhook Telegram: {webhook_url}")
        requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteWebhook", timeout=10)
        time.sleep(1)
        r = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook?url={webhook_url}", timeout=10)
        if r.status_code == 200:
            logger.info("Webhook de Telegram registrado correctamente")
        else:
            logger.error(f"Error al registrar webhook: {r.status_code} - {r.text}")
    except Exception as e:
        logger.error(f"Excepcion al configurar webhook: {e}")

# ==============================================================
#  LANZADOR DEL BOT (SUBPROCESO)
# ==============================================================
def run_bot():
    """Ejecuta el bot en un proceso separado en background y lo reinicia si cae."""
    global BOT_PROCESS, BOT_STARTED_AT

    script_path = resolve_bot_script()
    
    if not os.path.exists(script_path):
        logger.error(f"Archivo no encontrado: {script_path}")
        return

    while True:
        logger.info(f"Iniciando {script_path} en background...")
        try:
            BOT_PROCESS = subprocess.Popen([sys.executable, "-u", script_path], cwd=os.path.dirname(script_path))
            BOT_STARTED_AT = time.time()
            return_code = BOT_PROCESS.wait()
            logger.error(f"El proceso del bot termino con codigo {return_code}. Reiniciando en 10 segundos...")
        except Exception as e:
            logger.error(f"Error al intentar ejecutar el bot: {e}")
        finally:
            BOT_PROCESS = None
        time.sleep(10)

# Iniciar heartbeat interno (keep-alive silencioso)
heartbeat_thread = threading.Thread(target=_self_heartbeat, daemon=True, name="Heartbeat")
heartbeat_thread.start()
logger.info("Internal heartbeat thread started (interval=4min)")

# Iniciar el hilo del bot automáticamente (compatible con Gunicorn en Render)
bot_thread = threading.Thread(target=run_bot, daemon=True, name="BotRunner")
bot_thread.start()

# ==============================================================
#  INICIO DEL SISTEMA (LOCAL)
# ==============================================================
if __name__ == '__main__':
    setup_telegram_webhook()
    port = int(os.environ.get('PORT', 5000))
    logger.info(f"Iniciando servidor Flask en el puerto {port}...")
    app.run(debug=False, host='0.0.0.0', port=port)
