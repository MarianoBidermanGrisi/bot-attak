# bot_web_service.py
# Bot Breakout+Reentry para Bitget - Versión autoiniciable para Render

import requests
import time
import json
import os
import sys
from datetime import datetime, timedelta
import numpy as np
import math
import csv
import itertools
import statistics
import random
import hmac
import hashlib
import base64
from flask import Flask, request, jsonify
import threading
import logging

# Configurar logging para Render
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# ---------------------------
# CONFIGURACIÓN BITGET
# ---------------------------
BITGET_API_KEY = os.environ.get('BITGET_API_KEY')
BITGET_SECRET_KEY = os.environ.get('BITGET_SECRET_KEY')
BITGET_PASSPHRASE = os.environ.get('BITGET_PASSPHRASE')
BITGET_REST_URL = "https://api.bitget.com"

# Verificar credenciales Bitget
if not BITGET_API_KEY or not BITGET_SECRET_KEY or not BITGET_PASSPHRASE:
    logger.warning("⚠️ Credenciales de Bitget no configuradas. El bot funcionará en modo demo.")
    BITGET_API_KEY = BITGET_API_KEY or "demo"
    BITGET_SECRET_KEY = BITGET_SECRET_KEY or "demo"
    BITGET_PASSPHRASE = BITGET_PASSPHRASE or "demo"

# Símbolos para futuros de Bitget (Mix Contracts) - versión reducida para pruebas
BITGET_SYMBOLS = [
    'BTCUSDT_UMCBL',
    'ETHUSDT_UMCBL',
    'LINKUSDT_UMCBL',
    'BNBUSDT_UMCBL',
    'SOLUSDT_UMCBL'
]

# ---------------------------
# CLASE BITGET API HELPER (SIMPLIFICADA)
# ---------------------------
class BitgetAPI:
    def __init__(self, api_key, secret_key, passphrase):
        self.api_key = api_key
        self.secret_key = secret_key
        self.passphrase = passphrase
        self.base_url = BITGET_REST_URL

    def get_klines(self, symbol, granularity="1m", limit=100):
        """Obtiene velas históricas desde Bitget"""
        if self.api_key == "demo":
            # Modo demo: generar datos simulados
            logger.info(f"📡 Modo demo: Datos simulados para {symbol}")
            return self._generate_demo_klines(limit)
        
        endpoint = "/api/v2/mix/market/candles"
        params = {
            "symbol": symbol,
            "granularity": granularity,
            "limit": limit
        }
        
        try:
            response = requests.get(f"{self.base_url}{endpoint}", params=params, timeout=10)
            data = response.json()
            
            if data.get('code') == '00000':
                return data
            else:
                logger.error(f"Error API Bitget: {data}")
                return self._generate_demo_klines(limit)
                
        except Exception as e:
            logger.error(f"Error obteniendo klines: {e}")
            return self._generate_demo_klines(limit)
    
    def _generate_demo_klines(self, limit):
        """Genera datos de velas para modo demo"""
        import random
        base_price = 100.0
        data = []
        
        for i in range(limit):
            open_price = base_price + random.uniform(-5, 5)
            close_price = open_price + random.uniform(-2, 2)
            high_price = max(open_price, close_price) + random.uniform(0, 3)
            low_price = min(open_price, close_price) - random.uniform(0, 3)
            
            timestamp = int(time.time() * 1000) - (limit - i) * 60000
            
            data.append([
                str(timestamp),
                str(open_price),
                str(high_price),
                str(low_price),
                str(close_price),
                str(random.uniform(1000, 10000))
            ])
        
        return {'code': '00000', 'data': data, 'msg': 'success'}

# ---------------------------
# BOT TRADING SIMPLIFICADO
# ---------------------------
class TradingBot:
    def __init__(self, config):
        self.config = config
        self.bitget = BitgetAPI(BITGET_API_KEY, BITGET_SECRET_KEY, BITGET_PASSPHRASE)
        self.operaciones_activas = {}
        self.senales_enviadas = set()
        self.total_operaciones = 0
        
        logger.info("🤖 Bot Trading inicializado (modo simplificado)")
        logger.info(f"📊 Analizando {len(config['symbols'])} símbolos")
        logger.info(f"⏰ Intervalo: cada {config['scan_interval_minutes']} minutos")
    
    def obtener_datos_mercado(self, simbolo, timeframe="15m", num_velas=100):
        """Obtiene datos de mercado"""
        try:
            # Mapear timeframe
            timeframe_map = {'1m': '1m', '5m': '5m', '15m': '15m', '30m': '30m', '1h': '1H'}
            bitget_tf = timeframe_map.get(timeframe, '15m')
            
            result = self.bitget.get_klines(simbolo, bitget_tf, num_velas)
            
            if result.get('code') != '00000' or not result.get('data'):
                return None
            
            datos = result['data']
            cierres = [float(v[4]) for v in datos if len(v) >= 5]
            maximos = [float(v[2]) for v in datos if len(v) >= 5]
            minimos = [float(v[3]) for v in datos if len(v) >= 5]
            
            if not cierres:
                return None
            
            return {
                'maximos': maximos,
                'minimos': minimos,
                'cierres': cierres,
                'precio_actual': cierres[-1] if cierres else 0,
                'timeframe': timeframe
            }
            
        except Exception as e:
            logger.error(f"Error obteniendo datos {simbolo}: {e}")
            return None
    
    def analizar_simbolo(self, simbolo):
        """Analiza un símbolo en busca de oportunidades"""
        try:
            # Obtener datos
            datos = self.obtener_datos_mercado(simbolo, "15m", 100)
            if not datos or len(datos['cierres']) < 20:
                return None
            
            cierres = datos['cierres']
            precio_actual = datos['precio_actual']
            
            # Calcular indicadores simples
            sma_rapida = sum(cierres[-10:]) / 10 if len(cierres) >= 10 else precio_actual
            sma_lenta = sum(cierres[-30:]) / 30 if len(cierres) >= 30 else precio_actual
            
            # Calcular RSI simple
            cambios = [cierres[i] - cierres[i-1] for i in range(1, min(15, len(cierres)))]
            ganancias = sum(g for g in cambios if g > 0)
            perdidas = abs(sum(p for p in cambios if p < 0))
            
            if perdidas == 0:
                rsi = 100
            else:
                rs = ganancias / perdidas if perdidas > 0 else 1
                rsi = 100 - (100 / (1 + rs))
            
            # Determinar señal
            señal = None
            if sma_rapida > sma_lenta and rsi < 70:
                señal = "LONG"
            elif sma_rapida < sma_lenta and rsi > 30:
                señal = "SHORT"
            
            if señal:
                logger.info(f"📈 {simbolo}: {señal} | Precio: {precio_actual:.4f} | RSI: {rsi:.1f}")
                
                # Calcular niveles
                if señal == "LONG":
                    entrada = precio_actual
                    stop_loss = entrada * 0.98
                    take_profit = entrada * 1.03
                else:
                    entrada = precio_actual
                    stop_loss = entrada * 1.02
                    take_profit = entrada * 0.97
                
                return {
                    'simbolo': simbolo,
                    'señal': señal,
                    'entrada': entrada,
                    'stop_loss': stop_loss,
                    'take_profit': take_profit,
                    'precio_actual': precio_actual,
                    'rsi': rsi,
                    'sma_rapida': sma_rapida,
                    'sma_lenta': sma_lenta
                }
            
            return None
            
        except Exception as e:
            logger.error(f"Error analizando {simbolo}: {e}")
            return None
    
    def escanear_mercado(self):
        """Escanea todos los símbolos"""
        logger.info("🔍 Iniciando escaneo de mercado...")
        señales = []
        
        for simbolo in self.config['symbols']:
            try:
                if simbolo in self.operaciones_activas:
                    continue
                
                analisis = self.analizar_simbolo(simbolo)
                if analisis:
                    señales.append(analisis)
                    
                    # Enviar señal por Telegram
                    self.enviar_señal_telegram(analisis)
                    
                    # Registrar operación
                    self.operaciones_activas[simbolo] = {
                        'señal': analisis['señal'],
                        'entrada': analisis['entrada'],
                        'timestamp': datetime.now().isoformat()
                    }
                    self.total_operaciones += 1
                    
            except Exception as e:
                logger.error(f"Error procesando {simbolo}: {e}")
                continue
        
        logger.info(f"✅ Escaneo completado. Señales encontradas: {len(señales)}")
        return señales
    
    def enviar_señal_telegram(self, analisis):
        """Envía señal por Telegram"""
        token = self.config.get('telegram_token')
        chat_ids = self.config.get('telegram_chat_ids', [])
        
        if not token or not chat_ids:
            return
        
        try:
            señal = analisis['señal']
            emoji = "🟢" if señal == "LONG" else "🔴"
            
            mensaje = f"""
{emoji} <b>SEÑAL {señal} - {analisis['simbolo']}</b>
💰 <b>Precio:</b> {analisis['precio_actual']:.4f}
🎯 <b>Entrada:</b> {analisis['entrada']:.4f}
🛑 <b>Stop Loss:</b> {analisis['stop_loss']:.4f}
🎯 <b>Take Profit:</b> {analisis['take_profit']:.4f}
📊 <b>RSI:</b> {analisis['rsi']:.1f}
⏰ <b>Hora:</b> {datetime.now().strftime('%H:%M:%S')}
🤖 <b>Bot Bitget Breakout</b>
            """
            
            for chat_id in chat_ids:
                url = f"https://api.telegram.org/bot{token}/sendMessage"
                payload = {
                    'chat_id': chat_id,
                    'text': mensaje,
                    'parse_mode': 'HTML'
                }
                requests.post(url, json=payload, timeout=10)
                
            logger.info(f"📨 Señal enviada para {analisis['simbolo']}")
            
        except Exception as e:
            logger.error(f"Error enviando Telegram: {e}")
    
    def ejecutar_ciclo(self):
        """Ejecuta un ciclo completo"""
        try:
            logger.info("\n" + "="*50)
            logger.info("🔄 EJECUTANDO CICLO DE TRADING")
            logger.info("="*50)
            
            señales = self.escanear_mercado()
            
            logger.info(f"\n📊 RESUMEN:")
            logger.info(f"   Operaciones activas: {len(self.operaciones_activas)}")
            logger.info(f"   Total operaciones: {self.total_operaciones}")
            
            if self.operaciones_activas:
                for simbolo, op in self.operaciones_activas.items():
                    logger.info(f"   • {simbolo}: {op['señal']} @ {op['entrada']:.4f}")
            
            logger.info("="*50)
            
            return len(señales)
            
        except Exception as e:
            logger.error(f"❌ Error en ciclo: {e}")
            return 0

# ---------------------------
# CONFIGURACIÓN
# ---------------------------
def crear_config():
    """Crea configuración del bot"""
    # Chat IDs de Telegram
    telegram_chat_ids_str = os.environ.get('TELEGRAM_CHAT_ID', '')
    telegram_chat_ids = [cid.strip() for cid in telegram_chat_ids_str.split(',') if cid.strip()]
    
    if not telegram_chat_ids:
        telegram_chat_ids = ['-1002272872445']  # Default chat
    
    return {
        'symbols': BITGET_SYMBOLS,
        'scan_interval_minutes': 5,
        'telegram_token': os.environ.get('TELEGRAM_TOKEN', ''),
        'telegram_chat_ids': telegram_chat_ids
    }

# ---------------------------
# FLASK APP
# ---------------------------

app = Flask(__name__)

# Inicializar bot
config = crear_config()
bot = TradingBot(config)
bot_thread = None
bot_running = False

def bot_loop():
    """Loop principal del bot"""
    global bot_running
    
    logger.info("🚀 Iniciando loop del bot...")
    bot_running = True
    
    try:
        while bot_running:
            try:
                # Ejecutar ciclo
                señales = bot.ejecutar_ciclo()
                
                # Esperar para próximo ciclo
                wait_time = config['scan_interval_minutes'] * 60
                logger.info(f"⏳ Próximo ciclo en {config['scan_interval_minutes']} minutos...")
                
                # Esperar en segmentos para poder detener
                for _ in range(wait_time):
                    if not bot_running:
                        break
                    time.sleep(1)
                    
            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.error(f"Error en loop: {e}")
                time.sleep(60)
                
    except Exception as e:
        logger.error(f"Error crítico: {e}")
    finally:
        bot_running = False
        logger.info("🛑 Loop del bot detenido")

@app.route('/')
def index():
    """Endpoint principal"""
    status = {
        'status': 'online',
        'service': 'Bitget Trading Bot',
        'bot_running': bot_running,
        'operaciones_activas': len(bot.operaciones_activas),
        'total_operaciones': bot.total_operaciones,
        'symbols': len(config['symbols']),
        'interval_minutes': config['scan_interval_minutes'],
        'timestamp': datetime.now().isoformat()
    }
    return jsonify(status)

@app.route('/health')
def health():
    """Health check para Render"""
    return jsonify({'status': 'healthy', 'timestamp': datetime.now().isoformat()})

@app.route('/start', methods=['POST'])
def start_bot():
    """Inicia el bot"""
    global bot_thread, bot_running
    
    if bot_running:
        return jsonify({'status': 'error', 'message': 'Bot ya está en ejecución'}), 400
    
    try:
        bot_thread = threading.Thread(target=bot_loop, daemon=True)
        bot_thread.start()
        
        # Esperar a que inicie
        time.sleep(2)
        
        return jsonify({
            'status': 'success',
            'message': 'Bot iniciado',
            'bot_running': bot_running,
            'thread_alive': bot_thread.is_alive() if bot_thread else False
        })
        
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/stop', methods=['POST'])
def stop_bot():
    """Detiene el bot"""
    global bot_running
    
    if not bot_running:
        return jsonify({'status': 'error', 'message': 'Bot no está en ejecución'}), 400
    
    bot_running = False
    time.sleep(2)
    
    return jsonify({
        'status': 'success',
        'message': 'Bot detenido',
        'bot_running': bot_running
    })

@app.route('/scan', methods=['POST'])
def scan_now():
    """Ejecuta un escaneo inmediato"""
    try:
        señales = bot.ejecutar_ciclo()
        return jsonify({
            'status': 'success',
            'señales': señales,
            'operaciones_activas': len(bot.operaciones_activas)
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/status')
def status():
    """Obtiene estado completo"""
    return jsonify({
        'bot_running': bot_running,
        'operaciones_activas': len(bot.operaciones_activas),
        'operaciones_lista': list(bot.operaciones_activas.keys()),
        'total_operaciones': bot.total_operaciones,
        'symbols_configurados': config['symbols'],
        'interval_minutes': config['scan_interval_minutes'],
        'timestamp': datetime.now().isoformat()
    })

# Iniciar bot automáticamente al desplegar
def iniciar_bot_auto():
    """Inicia el bot automáticamente después de un delay"""
    time.sleep(10)  # Esperar a que Flask esté listo
    
    global bot_thread, bot_running
    
    if not bot_running:
        logger.info("🤖 Iniciando bot automáticamente...")
        bot_thread = threading.Thread(target=bot_loop, daemon=True)
        bot_thread.start()
        logger.info("✅ Bot iniciado automáticamente")

# Iniciar thread para autoinicio
auto_start_thread = threading.Thread(target=iniciar_bot_auto, daemon=True)
auto_start_thread.start()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    logger.info(f"🚀 Servicio iniciando en puerto {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
