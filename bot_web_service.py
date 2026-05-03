"""
Web service wrapper para botabracadabra.py.
Este archivo permite desplegar el bot en Render.com manteniendo un servidor web activo.
Basado en la estructura de bot_web_service.py.
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

# ==============================================================
#  RUTAS DEL SERVIDOR WEB (FLASK)
# ==============================================================
@app.route('/')
def index():
    """Ping básico — Render lo usa para verificar que el servicio responde."""
    return "✅ Bot Abracadabra + Render — en línea.", 200

@app.route('/health', methods=['GET'])
def health_check():
    """Health Check Path para Render."""
    return jsonify({
        "status": "running",
        "timestamp": time.time(),
        "bot": "abracadabra"
    }), 200

@app.route('/webhook', methods=['POST'])
def telegram_webhook():
    """Recibe updates de Telegram vía webhook (POST JSON)."""
    if request.is_json:
        update = request.get_json()
        logger.info(f"📩 Telegram update: {json.dumps(update)}")
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
            logger.warning("⚠️ RENDER_EXTERNAL_URL no definida — webhook omitido.")
            return

    try:
        logger.info(f"🔗 Registrando webhook Telegram: {webhook_url}")
        requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteWebhook", timeout=10)
        time.sleep(1)
        r = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook?url={webhook_url}", timeout=10)
        if r.status_code == 200:
            logger.info("✅ Webhook de Telegram registrado correctamente")
        else:
            logger.error(f"❌ Error al registrar webhook: {r.status_code} — {r.text}")
    except Exception as e:
        logger.error(f"❌ Excepción al configurar webhook: {e}")

# ==============================================================
#  LANZADOR DEL BOT (SUBPROCESO)
# ==============================================================
def run_bot():
    """Ejecuta botabracadabra.py en un proceso separado en background. 
    Se reinicia automáticamente si se cae."""
    script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "botabracadabra.py")
    
    if not os.path.exists(script_path):
        logger.error(f"❌ Archivo no encontrado: {script_path}")
        return

    while True:
        logger.info(f"🚀 Iniciando {script_path} en background...")
        try:
            # Ejecutamos el bot en un proceso hijo. 
            # Omitimos stdout/stderr explícitos para heredar los de Flask/Render correctamente.
            process = subprocess.Popen([sys.executable, script_path])
            
            # Esperamos a que el proceso termine
            process.wait()
            logger.error("❌ El proceso de botabracadabra.py ha terminado inesperadamente. Reiniciando en 10 segundos...")
        except Exception as e:
            logger.error(f"❌ Error al intentar ejecutar botabracadabra.py: {e}")
            
        time.sleep(10)

# Iniciar el hilo del bot automáticamente (compatible con Gunicorn en Render)
bot_thread = threading.Thread(target=run_bot, daemon=True)
bot_thread.start()

# ==============================================================
#  INICIO DEL SISTEMA (LOCAL)
# ==============================================================
if __name__ == '__main__':
    setup_telegram_webhook()
    port = int(os.environ.get('PORT', 5000))
    logger.info(f"🌐 Iniciando servidor Flask en el puerto {port}...")
    app.run(debug=False, host='0.0.0.0', port=port)
