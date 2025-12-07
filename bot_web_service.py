# bot_web_service.py
# Bot Breakout+Reentry para Bitget - Con sistema de cierre automático

import requests
import time
import json
import os
import sys
from datetime import datetime, timedelta
import numpy as np
import math
import csv
import random
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
BITGET_API_KEY = os.environ.get('BITGET_API_KEY', 'demo')
BITGET_SECRET_KEY = os.environ.get('BITGET_SECRET_KEY', 'demo')
BITGET_PASSPHRASE = os.environ.get('BITGET_PASSPHRASE', 'demo')

# Símbolos para futuros de Bitget
BITGET_SYMBOLS = [
    'BTCUSDT_UMCBL',
    'ETHUSDT_UMCBL',
    'LINKUSDT_UMCBL',
    'BNBUSDT_UMCBL',
    'SOLUSDT_UMCBL'
]

# ---------------------------
# BOT TRADING CON CIERRE AUTOMÁTICO
# ---------------------------
class TradingBot:
    def __init__(self, config):
        self.config = config
        self.operaciones_activas = {}
        self.historial_operaciones = []
        self.total_operaciones = 0
        self.log_file = "trading_log.csv"
        
        # Inicializar archivo de log
        self._init_log_file()
        
        logger.info("🤖 Bot Trading inicializado con cierre automático")
        logger.info(f"📊 Símbolos: {len(config['symbols'])}")
        logger.info(f"⏰ Intervalo: {config['scan_interval_minutes']} minutos")
    
    def _init_log_file(self):
        """Inicializa archivo CSV para logs"""
        try:
            if not os.path.exists(self.log_file):
                with open(self.log_file, 'w', newline='', encoding='utf-8') as f:
                    writer = csv.writer(f)
                    writer.writerow([
                        'timestamp', 'symbol', 'signal', 'entry_price',
                        'stop_loss', 'take_profit', 'exit_price',
                        'result', 'pnl_percent', 'duration_min',
                        'reason'
                    ])
        except Exception as e:
            logger.error(f"Error inicializando log: {e}")
    
    def _log_operation(self, operation):
        """Registra operación en CSV"""
        try:
            with open(self.log_file, 'a', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow([
                    operation.get('timestamp', datetime.now().isoformat()),
                    operation.get('symbol', ''),
                    operation.get('signal', ''),
                    operation.get('entry_price', 0),
                    operation.get('stop_loss', 0),
                    operation.get('take_profit', 0),
                    operation.get('exit_price', 0),
                    operation.get('result', ''),
                    operation.get('pnl_percent', 0),
                    operation.get('duration_min', 0),
                    operation.get('reason', '')
                ])
        except Exception as e:
            logger.error(f"Error registrando operación: {e}")
    
    def check_close_conditions(self):
        """Verifica condiciones de cierre para operaciones activas"""
        if not self.operaciones_activas:
            return []
        
        closed_ops = []
        now = datetime.now()
        
        for symbol, position in list(self.operaciones_activas.items()):
            try:
                # Obtener precio actual simulado (en modo demo)
                current_price = self._get_simulated_price(symbol, position['entry_price'])
                
                signal = position['signal']
                entry = position['entry_price']
                sl = position['stop_loss']
                tp = position['take_profit']
                entry_time = datetime.fromisoformat(position['entry_time'])
                
                # Calcular duración
                duration_min = (now - entry_time).total_seconds() / 60
                
                # Verificar condiciones de cierre
                result = None
                reason = ""
                
                if signal == "LONG":
                    if current_price >= tp:
                        result = "TP"
                        reason = "Take Profit alcanzado"
                    elif current_price <= sl:
                        result = "SL"
                        reason = "Stop Loss alcanzado"
                else:  # SHORT
                    if current_price <= tp:
                        result = "TP"
                        reason = "Take Profit alcanzado"
                    elif current_price >= sl:
                        result = "SL"
                        reason = "Stop Loss alcanzado"
                
                # Timeout después de 30 minutos
                if duration_min > 30 and not result:
                    result = "TIMEOUT"
                    reason = "Timeout (30+ minutos)"
                
                if result:
                    # Calcular PnL
                    if signal == "LONG":
                        pnl_percent = ((current_price - entry) / entry) * 100
                    else:
                        pnl_percent = ((entry - current_price) / entry) * 100
                    
                    # Crear registro de cierre
                    closed_op = {
                        'timestamp': now.isoformat(),
                        'symbol': symbol,
                        'signal': signal,
                        'entry_price': entry,
                        'stop_loss': sl,
                        'take_profit': tp,
                        'exit_price': current_price,
                        'result': result,
                        'pnl_percent': pnl_percent,
                        'duration_min': duration_min,
                        'reason': reason
                    }
                    
                    # Registrar en CSV
                    self._log_operation(closed_op)
                    
                    # Agregar al historial
                    self.historial_operaciones.append(closed_op)
                    
                    # Remover de activas
                    closed_ops.append(symbol)
                    del self.operaciones_activas[symbol]
                    
                    # Enviar notificación
                    self._send_close_notification(closed_op)
                    
                    logger.info(f"📊 {symbol} CERRADO: {result} | PnL: {pnl_percent:.2f}%")
                    
            except Exception as e:
                logger.error(f"Error verificando cierre {symbol}: {e}")
                continue
        
        return closed_ops
    
    def _get_simulated_price(self, symbol, base_price):
        """Simula precio actual para modo demo"""
        # Variación aleatoria del ±2%
        variation = random.uniform(-0.02, 0.02)
        return base_price * (1 + variation)
    
    def _send_close_notification(self, operation):
        """Envía notificación de cierre por Telegram"""
        token = self.config.get('telegram_token')
        chat_ids = self.config.get('telegram_chat_ids', [])
        
        if not token or not chat_ids:
            return
        
        try:
            emoji = "✅" if operation['result'] == "TP" else "❌" if operation['result'] == "SL" else "⏰"
            color = "🟢" if operation['pnl_percent'] > 0 else "🔴"
            
            message = f"""
{color} <b>OPERACIÓN CERRADA - {operation['symbol']}</b>
{emoji} <b>Resultado:</b> {operation['result']}
📊 <b>Señal:</b> {operation['signal']}
💰 <b>Entrada:</b> {operation['entry_price']:.4f}
🎯 <b>Salida:</b> {operation['exit_price']:.4f}
📈 <b>PnL:</b> {operation['pnl_percent']:+.2f}%
⏰ <b>Duración:</b> {operation['duration_min']:.1f} min
📝 <b>Razón:</b> {operation['reason']}
            """
            
            for chat_id in chat_ids:
                url = f"https://api.telegram.org/bot{token}/sendMessage"
                payload = {
                    'chat_id': chat_id,
                    'text': message,
                    'parse_mode': 'HTML'
                }
                requests.post(url, json=payload, timeout=10)
                
        except Exception as e:
            logger.error(f"Error enviando notificación: {e}")
    
    def scan_market(self):
        """Escanea el mercado en busca de nuevas señales"""
        logger.info("🔍 Escaneando mercado...")
        
        # Primero verificar cierres
        closed = self.check_close_conditions()
        if closed:
            logger.info(f"📊 Operaciones cerradas: {len(closed)}")
        
        # Buscar nuevas señales (solo si no hay operación activa en ese símbolo)
        new_signals = []
        
        for symbol in self.config['symbols']:
            try:
                # Saltar si ya hay operación activa en este símbolo
                if symbol in self.operaciones_activas:
                    continue
                
                # Simular análisis (en producción usarías datos reales)
                if random.random() > 0.7:  # 30% de probabilidad de señal
                    signal = "LONG" if random.random() > 0.5 else "SHORT"
                    current_price = random.uniform(90, 110)
                    
                    if signal == "LONG":
                        entry = current_price
                        sl = entry * 0.98
                        tp = entry * 1.03
                    else:
                        entry = current_price
                        sl = entry * 1.02
                        tp = entry * 0.97
                    
                    signal_data = {
                        'symbol': symbol,
                        'signal': signal,
                        'entry_price': entry,
                        'stop_loss': sl,
                        'take_profit': tp,
                        'current_price': current_price,
                        'timestamp': datetime.now().isoformat()
                    }
                    
                    new_signals.append(signal_data)
                    
                    # Registrar como operación activa
                    self.operaciones_activas[symbol] = {
                        'signal': signal,
                        'entry_price': entry,
                        'stop_loss': sl,
                        'take_profit': tp,
                        'entry_time': datetime.now().isoformat()
                    }
                    
                    self.total_operaciones += 1
                    
                    # Enviar señal
                    self._send_signal_notification(signal_data)
                    
                    logger.info(f"📈 {symbol}: {signal} @ {entry:.4f}")
                    
            except Exception as e:
                logger.error(f"Error analizando {symbol}: {e}")
                continue
        
        logger.info(f"✅ Nuevas señales: {len(new_signals)}")
        return new_signals
    
    def _send_signal_notification(self, signal):
        """Envía notificación de señal por Telegram"""
        token = self.config.get('telegram_token')
        chat_ids = self.config.get('telegram_chat_ids', [])
        
        if not token or not chat_ids:
            return
        
        try:
            emoji = "🟢" if signal['signal'] == "LONG" else "🔴"
            
            message = f"""
{emoji} <b>SEÑAL {signal['signal']} - {signal['symbol']}</b>
💰 <b>Precio:</b> {signal['current_price']:.4f}
🎯 <b>Entrada:</b> {signal['entry_price']:.4f}
🛑 <b>Stop Loss:</b> {signal['stop_loss']:.4f}
🎯 <b>Take Profit:</b> {signal['take_profit']:.4f}
⏰ <b>Hora:</b> {datetime.now().strftime('%H:%M:%S')}
🤖 <b>Bot Bitget Auto-Trading</b>
            """
            
            for chat_id in chat_ids:
                url = f"https://api.telegram.org/bot{token}/sendMessage"
                payload = {
                    'chat_id': chat_id,
                    'text': message,
                    'parse_mode': 'HTML'
                }
                requests.post(url, json=payload, timeout=10)
                
        except Exception as e:
            logger.error(f"Error enviando señal: {e}")
    
    def run_cycle(self):
        """Ejecuta un ciclo completo del bot"""
        try:
            logger.info("\n" + "="*50)
            logger.info("🔄 CICLO DE TRADING")
            logger.info("="*50)
            
            signals = self.scan_market()
            
            logger.info(f"\n📊 RESUMEN:")
            logger.info(f"   Activas: {len(self.operaciones_activas)}")
            logger.info(f"   Historial: {len(self.historial_operaciones)}")
            logger.info(f"   Total: {self.total_operaciones}")
            
            if self.operaciones_activas:
                for symbol, pos in self.operaciones_activas.items():
                    entry_time = datetime.fromisoformat(pos['entry_time'])
                    duration = (datetime.now() - entry_time).total_seconds() / 60
                    logger.info(f"   • {symbol}: {pos['signal']} @ {pos['entry_price']:.4f} ({duration:.1f}min)")
            
            logger.info("="*50)
            
            return len(signals)
            
        except Exception as e:
            logger.error(f"❌ Error en ciclo: {e}")
            return 0

# ---------------------------
# CONFIGURACIÓN
# ---------------------------
def create_config():
    """Crea configuración del bot"""
    telegram_chat_ids_str = os.environ.get('TELEGRAM_CHAT_ID', '')
    chat_ids = [cid.strip() for cid in telegram_chat_ids_str.split(',') if cid.strip()]
    
    if not chat_ids:
        chat_ids = ['-1002272872445']
    
    return {
        'symbols': BITGET_SYMBOLS,
        'scan_interval_minutes': 5,
        'telegram_token': os.environ.get('TELEGRAM_TOKEN', ''),
        'telegram_chat_ids': chat_ids
    }

# ---------------------------
# FLASK APP
# ---------------------------

app = Flask(__name__)

# Inicializar bot
config = create_config()
bot = TradingBot(config)
bot_thread = None
bot_running = False

def bot_loop():
    """Loop principal del bot"""
    global bot_running
    bot_running = True
    
    logger.info("🚀 Bot iniciado")
    
    while bot_running:
        try:
            signals = bot.run_cycle()
            
            # Esperar para próximo ciclo
            wait_min = config['scan_interval_minutes']
            logger.info(f"⏳ Próximo ciclo en {wait_min} minutos...")
            
            for _ in range(wait_min * 60):
                if not bot_running:
                    break
                time.sleep(1)
                
        except Exception as e:
            logger.error(f"Error en loop: {e}")
            time.sleep(60)
    
    logger.info("🛑 Bot detenido")

@app.route('/')
def index():
    """Endpoint principal"""
    status = {
        'status': 'online',
        'bot': 'Bitget Auto-Trading',
        'bot_running': bot_running,
        'active_operations': len(bot.operaciones_activas),
        'total_operations': bot.total_operaciones,
        'symbols': len(config['symbols']),
        'interval_minutes': config['scan_interval_minutes'],
        'timestamp': datetime.now().isoformat()
    }
    return jsonify(status)

@app.route('/health')
def health():
    """Health check"""
    return jsonify({'status': 'healthy'})

@app.route('/start', methods=['POST'])
def start_bot():
    """Inicia el bot"""
    global bot_thread, bot_running
    
    if bot_running:
        return jsonify({'status': 'error', 'message': 'Ya está ejecutándose'})
    
    bot_thread = threading.Thread(target=bot_loop, daemon=True)
    bot_thread.start()
    
    return jsonify({
        'status': 'success',
        'message': 'Bot iniciado',
        'running': True
    })

@app.route('/stop', methods=['POST'])
def stop_bot():
    """Detiene el bot"""
    global bot_running
    
    bot_running = False
    return jsonify({
        'status': 'success',
        'message': 'Bot detenido',
        'running': False
    })

@app.route('/scan', methods=['POST'])
def scan_now():
    """Ejecuta escaneo inmediato"""
    try:
        signals = bot.run_cycle()
        return jsonify({
            'status': 'success',
            'signals_found': signals,
            'active_ops': len(bot.operaciones_activas)
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})

@app.route('/status')
def get_status():
    """Estado detallado"""
    active_list = []
    for symbol, pos in bot.operaciones_activas.items():
        entry_time = datetime.fromisoformat(pos['entry_time'])
        duration = (datetime.now() - entry_time).total_seconds() / 60
        active_list.append({
            'symbol': symbol,
            'signal': pos['signal'],
            'entry': pos['entry_price'],
            'duration_min': round(duration, 1)
        })
    
    return jsonify({
        'bot_running': bot_running,
        'active_count': len(bot.operaciones_activas),
        'active_operations': active_list,
        'history_count': len(bot.historial_operaciones),
        'total_operations': bot.total_operaciones,
        'scan_interval': config['scan_interval_minutes']
    })

@app.route('/clear', methods=['POST'])
def clear_operations():
    """Limpia operaciones antiguas"""
    try:
        # Cerrar todas las operaciones activas por timeout
        now = datetime.now()
        cleared = []
        
        for symbol, pos in list(bot.operaciones_activas.items()):
            entry_time = datetime.fromisoformat(pos['entry_time'])
            duration = (now - entry_time).total_seconds() / 60
            
            closed_op = {
                'timestamp': now.isoformat(),
                'symbol': symbol,
                'signal': pos['signal'],
                'entry_price': pos['entry_price'],
                'stop_loss': pos['stop_loss'],
                'take_profit': pos['take_profit'],
                'exit_price': bot._get_simulated_price(symbol, pos['entry_price']),
                'result': 'CLEARED',
                'pnl_percent': 0,
                'duration_min': duration,
                'reason': 'Limpieza manual'
            }
            
            bot._log_operation(closed_op)
            bot.historial_operaciones.append(closed_op)
            cleared.append(symbol)
            del bot.operaciones_activas[symbol]
        
        return jsonify({
            'status': 'success',
            'cleared': len(cleared),
            'operations': cleared
        })
        
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})

# Iniciar bot automáticamente
def auto_start():
    """Inicia el bot automáticamente"""
    time.sleep(5)
    
    global bot_thread, bot_running
    
    if not bot_running:
        logger.info("🤖 Inicio automático del bot...")
        bot_thread = threading.Thread(target=bot_loop, daemon=True)
        bot_thread.start()

# Iniciar thread de autoinicio
start_thread = threading.Thread(target=auto_start, daemon=True)
start_thread.start()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    logger.info(f"🚀 Servicio iniciado en puerto {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
