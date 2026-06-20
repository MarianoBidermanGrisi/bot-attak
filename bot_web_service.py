import os
import sys
import time
import json
import logging
import threading
from flask import Flask, jsonify

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger(__name__)

app = Flask(__name__)

BOT_THREAD = None
BOT_STARTED_AT = None
BOT_ACTIVE = False

@app.route('/')
def index():
    return "Bot RF 15m - en linea.", 200

@app.route('/health')
def health():
    return jsonify({
        "status": "running",
        "timestamp": time.time(),
        "bot": "rf_15m",
        "bot_active": BOT_ACTIVE,
        "bot_started_at": BOT_STARTED_AT,
    }), 200

@app.route('/status')
def status():
    uptime = time.time() - BOT_STARTED_AT if BOT_STARTED_AT else 0
    return jsonify({
        "bot_active": BOT_ACTIVE,
        "uptime_seconds": round(uptime),
        "started_at": BOT_STARTED_AT,
    }), 200

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

def run_bot():
    global BOT_ACTIVE, BOT_STARTED_AT
    try:
        os.chdir(PROJECT_ROOT)
        if PROJECT_ROOT not in sys.path:
            sys.path.insert(0, PROJECT_ROOT)
        from bot_rf_15m import BotRF15m
        bot = BotRF15m()
        BOT_STARTED_AT = time.time()
        BOT_ACTIVE = True
        log.info("Bot RF 15m iniciado correctamente")
        bot.run()
    except Exception as e:
        log.error(f"Error fatal en bot: {e}", exc_info=True)
    finally:
        BOT_ACTIVE = False

bot_thread = threading.Thread(target=run_bot, daemon=True, name="BotRF15m")
bot_thread.start()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    log.info(f"Iniciando servidor web en puerto {port}")
    app.run(debug=False, host='0.0.0.0', port=port)
