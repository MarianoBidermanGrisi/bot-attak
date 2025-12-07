# bot_web_service.py
# Bot Breakout+Reentry COMPLETO - Con órdenes REALES en Bitget

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
import hmac
import hashlib
import base64
from flask import Flask, request, jsonify
import threading
import logging

# Configurar logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ---------------------------
# CONFIGURACIÓN BITGET
# ---------------------------
BITGET_API_KEY = os.environ.get('BITGET_API_KEY')
BITGET_SECRET_KEY = os.environ.get('BITGET_SECRET_KEY')
BITGET_PASSPHRASE = os.environ.get('BITGET_PASSPHRASE')
BITGET_REST_URL = "https://api.bitget.com"

# Verificar credenciales
if not BITGET_API_KEY or not BITGET_SECRET_KEY or not BITGET_PASSPHRASE:
    logger.error("❌ CREDENCIALES BITGET NO CONFIGURADAS")
    logger.error("   Configura en Render:")
    logger.error("   - BITGET_API_KEY")
    logger.error("   - BITGET_SECRET_KEY")
    logger.error("   - BITGET_PASSPHRASE")
    sys.exit(1)

# Símbolos para trading
SYMBOLS = [
    'BTCUSDT_UMCBL',
    'ETHUSDT_UMCBL',
    'LINKUSDT_UMCBL',
    'BNBUSDT_UMCBL',
    'SOLUSDT_UMCBL'
]

# ---------------------------
# CLASE BITGET API (REAL)
# ---------------------------
class BitgetAPI:
    def __init__(self, api_key, secret_key, passphrase):
        self.api_key = api_key
        self.secret_key = secret_key
        self.passphrase = passphrase
        self.base_url = BITGET_REST_URL
        
        # Sincronizar tiempo
        self._sync_time()
        
        logger.info("✅ API Bitget inicializada (MODO REAL)")

    def _sync_time(self):
        """Sincroniza tiempo con servidor Bitget"""
        try:
            resp = requests.get(f"{self.base_url}/api/v2/public/time", timeout=5)
            server_time = resp.json()['data']['timestamp']
            local_time = int(time.time() * 1000)
            self.time_offset = server_time - local_time
            logger.info(f"⏰ Offset de tiempo: {self.time_offset}ms")
        except Exception as e:
            logger.error(f"Error sincronizando tiempo: {e}")
            self.time_offset = 0

    def _get_timestamp(self):
        """Timestamp en milisegundos"""
        return str(int(time.time() * 1000) + self.time_offset)

    def _sign(self, message):
        """Firma HMAC-SHA256"""
        return hmac.new(
            bytes(self.secret_key, 'utf-8'),
            bytes(message, 'utf-8'),
            hashlib.sha256
        ).digest()

    def _make_request(self, method, endpoint, params=None, data=None):
        """Realiza petición firmada"""
        timestamp = self._get_timestamp()
        
        if method == "GET" and params:
            query = '&'.join([f'{k}={v}' for k, v in sorted(params.items())])
            message = timestamp + method + endpoint + '?' + query
        else:
            body = json.dumps(data) if data else ''
            message = timestamp + method + endpoint + body
        
        signature = base64.b64encode(self._sign(message)).decode()
        
        headers = {
            'ACCESS-KEY': self.api_key,
            'ACCESS-SIGN': signature,
            'ACCESS-TIMESTAMP': timestamp,
            'ACCESS-PASSPHRASE': self.passphrase,
            'Content-Type': 'application/json'
        }
        
        url = self.base_url + endpoint
        
        try:
            if method == "GET":
                response = requests.get(url, headers=headers, params=params, timeout=10)
            else:
                response = requests.post(url, headers=headers, json=data, timeout=10)
            
            result = response.json()
            
            if result.get('code') != '00000':
                logger.error(f"Error API Bitget: {result}")
                return None
            
            return result
            
        except Exception as e:
            logger.error(f"Error en petición: {e}")
            return None

    def get_klines(self, symbol, interval="15m", limit=100):
        """Obtiene velas"""
        endpoint = "/api/v2/mix/market/candles"
        params = {
            "symbol": symbol,
            "granularity": interval,
            "limit": limit
        }
        return self._make_request("GET", endpoint, params=params)

    def get_contract_info(self, symbol):
        """Obtiene información del contrato"""
        endpoint = "/api/v2/mix/market/contracts"
        params = {"symbol": symbol}
        return self._make_request("GET", endpoint, params=params)

    def place_order(self, symbol, side, order_type, size, price=None):
        """Coloca orden en Bitget"""
        endpoint = "/api/v2/mix/order/place-order"
        
        data = {
            "symbol": symbol,
            "side": side,
            "ordType": order_type,
            "size": str(size),
            "marginMode": "isolated"
        }
        
        if price and order_type == "limit":
            data["price"] = str(price)
        
        return self._make_request("POST", endpoint, data=data)

    def place_tp_sl_order(self, symbol, plan_type, trigger_price, execute_price, size, side):
        """Coloca TP/SL"""
        endpoint = "/api/v2/mix/order/place-plan-order"
        
        data = {
            "symbol": symbol,
            "planType": plan_type,
            "triggerPrice": str(trigger_price),
            "executePrice": str(execute_price),
            "size": str(size),
            "side": side,
            "triggerType": "market_price"
        }
        
        return self._make_request("POST", endpoint, data=data)

# ---------------------------
# BOT DE TRADING REAL
# ---------------------------
class TradingBot:
    def __init__(self, config):
        self.config = config
        
        # Inicializar API Bitget
        self.api = BitgetAPI(BITGET_API_KEY, BITGET_SECRET_KEY, BITGET_PASSPHRASE)
        
        # Variables de trading
        self.margin_per_trade = 10  # $ por operación
        self.leverage = 10
        self.operaciones_activas = {}
        self.historial = []
        self.log_file = "bitget_trades.csv"
        
        self._init_log()
        logger.info("🤖 Bot de Trading REAL inicializado")

    def _init_log(self):
        """Inicializa archivo de log"""
        try:
            if not os.path.exists(self.log_file):
                with open(self.log_file, 'w', newline='', encoding='utf-8') as f:
                    writer = csv.writer(f)
                    writer.writerow([
                        'timestamp', 'symbol', 'signal', 'entry_price',
                        'stop_loss', 'take_profit', 'exit_price',
                        'result', 'pnl_percent', 'duration_min',
                        'order_id', 'size', 'margin', 'leverage'
                    ])
        except Exception as e:
            logger.error(f"Error inicializando log: {e}")

    def _log_trade(self, trade_data):
        """Registra trade en CSV"""
        try:
            with open(self.log_file, 'a', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow([
                    trade_data.get('timestamp', datetime.now().isoformat()),
                    trade_data.get('symbol', ''),
                    trade_data.get('signal', ''),
                    trade_data.get('entry_price', 0),
                    trade_data.get('stop_loss', 0),
                    trade_data.get('take_profit', 0),
                    trade_data.get('exit_price', 0),
                    trade_data.get('result', ''),
                    trade_data.get('pnl_percent', 0),
                    trade_data.get('duration_min', 0),
                    trade_data.get('order_id', ''),
                    trade_data.get('size', 0),
                    self.margin_per_trade,
                    self.leverage
                ])
        except Exception as e:
            logger.error(f"Error registrando trade: {e}")

    def _calculate_position_size(self, symbol, entry_price):
        """Calcula tamaño de posición para Bitget"""
        try:
            # Obtener info del contrato
            info = self.api.get_contract_info(symbol)
            if not info or not info.get('data'):
                return 0
            
            contract = info['data'][0]
            price_tick = float(contract['priceTick'])
            lot_size = float(contract['lotSz'])
            min_order = float(contract['minOrderSz'])
            
            # Calcular notional
            notional = self.margin_per_trade * self.leverage
            
            # Calcular tamaño
            size_raw = notional / entry_price
            size = round(size_raw / lot_size) * lot_size
            
            # Ajustar a límites
            if size < min_order:
                size = min_order
            
            return round(size, 6)
            
        except Exception as e:
            logger.error(f"Error calculando tamaño: {e}")
            return 0

    def execute_trade(self, signal_data):
        """Ejecuta trade REAL en Bitget"""
        symbol = signal_data['symbol']
        signal = signal_data['signal']
        entry = signal_data['entry_price']
        sl = signal_data['stop_loss']
        tp = signal_data['take_profit']
        
        # Calcular tamaño
        size = self._calculate_position_size(symbol, entry)
        if size <= 0:
            logger.error(f"Tamaño inválido para {symbol}")
            return False
        
        # Determinar lado
        side = "buy" if signal == "LONG" else "sell"
        tp_side = "sell" if signal == "LONG" else "buy"
        sl_side = "sell" if signal == "LONG" else "buy"
        
        logger.info(f"📤 Ejecutando orden REAL en Bitget:")
        logger.info(f"   Símbolo: {symbol}")
        logger.info(f"   Señal: {signal}")
        logger.info(f"   Entrada: {entry}")
        logger.info(f"   Tamaño: {size}")
        logger.info(f"   Margen: ${self.margin_per_trade}")
        logger.info(f"   Leverage: {self.leverage}x")
        
        try:
            # 1. ORDEN DE ENTRADA (MARKET)
            order_result = self.api.place_order(
                symbol=symbol,
                side=side,
                order_type="market",
                size=size
            )
            
            if not order_result:
                logger.error(f"Error colocando orden de entrada para {symbol}")
                return False
            
            order_id = order_result['data']['orderId']
            logger.info(f"✅ Orden colocada: {order_id}")
            
            # Pequeño delay
            time.sleep(1)
            
            # 2. TAKE PROFIT
            tp_result = self.api.place_tp_sl_order(
                symbol=symbol,
                plan_type="normal",
                trigger_price=tp,
                execute_price=tp,
                size=size,
                side=tp_side
            )
            
            if tp_result:
                logger.info(f"✅ TP colocado en {tp}")
            else:
                logger.warning(f"⚠ Error colocando TP")
            
            # 3. STOP LOSS
            sl_result = self.api.place_tp_sl_order(
                symbol=symbol,
                plan_type="normal",
                trigger_price=sl,
                execute_price=sl,
                size=size,
                side=sl_side
            )
            
            if sl_result:
                logger.info(f"✅ SL colocado en {sl}")
            else:
                logger.warning(f"⚠ Error colocando SL")
            
            # Registrar operación activa
            self.operaciones_activas[symbol] = {
                'signal': signal,
                'entry_price': entry,
                'stop_loss': sl,
                'take_profit': tp,
                'size': size,
                'order_id': order_id,
                'entry_time': datetime.now().isoformat(),
                'margin': self.margin_per_trade,
                'leverage': self.leverage
            }
            
            # Enviar confirmación a Telegram
            self._send_trade_confirmation(signal_data, order_id, size)
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Error ejecutando trade: {e}")
            return False

    def _send_trade_confirmation(self, signal, order_id, size):
        """Envía confirmación de trade a Telegram"""
        token = self.config.get('telegram_token')
        chat_ids = self.config.get('telegram_chat_ids', [])
        
        if not token or not chat_ids:
            return
        
        try:
            emoji = "🟢" if signal['signal'] == "LONG" else "🔴"
            
            message = f"""
{emoji} <b>ORDEN EJECUTADA EN BITGET</b>
📊 <b>Símbolo:</b> {signal['symbol']}
🎯 <b>Señal:</b> {signal['signal']}
💰 <b>Entrada:</b> {signal['entry_price']:.4f}
📏 <b>Tamaño:</b> {size}
💵 <b>Margen:</b> ${self.margin_per_trade}
📈 <b>Leverage:</b> {self.leverage}x
🛑 <b>Stop Loss:</b> {signal['stop_loss']:.4f}
🎯 <b>Take Profit:</b> {signal['take_profit']:.4f}
🆔 <b>Order ID:</b> {order_id}
⏰ <b>Hora:</b> {datetime.now().strftime('%H:%M:%S')}
🤖 <b>Bot Breakout+Reentry</b>
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
            logger.error(f"Error enviando confirmación: {e}")

    def analyze_symbol(self, symbol):
        """Analiza símbolo con estrategia Breakout+Reentry"""
        try:
            # Obtener datos
            result = self.api.get_klines(symbol, "15m", 100)
            if not result or not result.get('data'):
                return None
            
            data = result['data']
            closes = [float(c[4]) for c in data]
            highs = [float(c[2]) for c in data]
            lows = [float(c[3]) for c in data]
            
            if len(closes) < 50:
                return None
            
            current_price = closes[-1]
            
            # ESTRATEGIA BREAKOUT + REENTRY
            # 1. Calcular canal (20 periodos)
            lookback = 20
            if len(closes) < lookback:
                return None
            
            # Regresión lineal
            x = np.arange(len(closes[-lookback:]))
            y = np.array(closes[-lookback:])
            
            A = np.vstack([x, np.ones(len(x))]).T
            m, c = np.linalg.lstsq(A, y, rcond=None)[0]
            
            # Calcular desviación
            y_pred = m * x + c
            residuals = y - y_pred
            std = np.std(residuals)
            
            # Bandas del canal
            upper_band = y_pred + std
            lower_band = y_pred - std
            
            current_upper = upper_band[-1]
            current_lower = lower_band[-1]
            
            # Calcular ángulo de tendencia
            price_range = max(y) - min(y)
            if price_range > 0:
                angle = math.degrees(math.atan(m * len(x) / price_range))
            else:
                angle = 0
            
            # Calcular RSI simple
            changes = [closes[i] - closes[i-1] for i in range(1, len(closes))]
            gains = sum(max(change, 0) for change in changes[-14:])
            losses = sum(abs(min(change, 0)) for change in changes[-14:])
            
            if losses == 0:
                rsi = 100
            else:
                rs = gains / losses
                rsi = 100 - (100 / (1 + rs))
            
            # Calcular Stochastic
            period = 14
            if len(closes) >= period:
                lowest = min(lows[-period:])
                highest = max(highs[-period:])
                if highest != lowest:
                    stoch = 100 * (current_price - lowest) / (highest - lowest)
                else:
                    stoch = 50
            else:
                stoch = 50
            
            # DETECCIÓN DE SEÑAL
            signal = None
            
            # Condiciones para LONG (Breakout + Reentry)
            if (current_price > current_lower and  # Reingreso al canal
                angle > 10 and  # Tendencia alcista
                stoch < 30 and  # Oversold
                rsi < 40):  # No sobrecomprado
                
                # Verificar que estaba fuera del canal
                prev_price = closes[-2]
                if prev_price < current_lower:
                    signal = "LONG"
            
            # Condiciones para SHORT
            elif (current_price < current_upper and  # Reingreso al canal
                  angle < -10 and  # Tendencia bajista
                  stoch > 70 and  # Overbought
                  rsi > 60):  # Sobrecomprado
                
                # Verificar que estaba fuera del canal
                prev_price = closes[-2]
                if prev_price > current_upper:
                    signal = "SHORT"
            
            if signal:
                # Calcular niveles
                atr = np.mean([highs[i] - lows[i] for i in range(-14, 0)])
                
                if signal == "LONG":
                    entry = current_price
                    sl = entry - (atr * 1.5)
                    tp = entry + (atr * 2.5)
                else:
                    entry = current_price
                    sl = entry + (atr * 1.5)
                    tp = entry - (atr * 2.5)
                
                # Ratio riesgo/beneficio mínimo
                risk = abs(entry - sl)
                reward = abs(tp - entry)
                rr_ratio = reward / risk if risk > 0 else 0
                
                if rr_ratio < 1.5:
                    # Ajustar TP para mantener ratio mínimo
                    if signal == "LONG":
                        tp = entry + (risk * 1.5)
                    else:
                        tp = entry - (risk * 1.5)
                
                return {
                    'symbol': symbol,
                    'signal': signal,
                    'entry_price': round(entry, 6),
                    'stop_loss': round(sl, 6),
                    'take_profit': round(tp, 6),
                    'current_price': current_price,
                    'rsi': round(rsi, 1),
                    'stoch': round(stoch, 1),
                    'angle': round(angle, 1),
                    'channel_width': round((current_upper - current_lower) / current_price * 100, 2),
                    'timestamp': datetime.now().isoformat()
                }
            
            return None
            
        except Exception as e:
            logger.error(f"Error analizando {symbol}: {e}")
            return None

    def scan_market(self):
        """Escanea el mercado"""
        logger.info("🔍 Escaneando mercado (estrategia Breakout+Reentry)...")
        
        signals = []
        
        for symbol in self.config['symbols']:
            try:
                # Saltar si ya hay operación activa
                if symbol in self.operaciones_activas:
                    continue
                
                # Analizar símbolo
                analysis = self.analyze_symbol(symbol)
                
                if analysis:
                    logger.info(f"📈 Señal encontrada: {symbol} {analysis['signal']}")
                    logger.info(f"   Precio: {analysis['current_price']:.4f}")
                    logger.info(f"   RSI: {analysis['rsi']:.1f}, Stoch: {analysis['stoch']:.1f}")
                    logger.info(f"   Ángulo: {analysis['angle']:.1f}°, Canal: {analysis['channel_width']:.1f}%")
                    
                    signals.append(analysis)
                    
                    # EJECUTAR TRADE REAL
                    success = self.execute_trade(analysis)
                    
                    if success:
                        logger.info(f"✅ Trade ejecutado en Bitget para {symbol}")
                    else:
                        logger.error(f"❌ Error ejecutando trade para {symbol}")
                
            except Exception as e:
                logger.error(f"Error procesando {symbol}: {e}")
                continue
        
        logger.info(f"✅ Señales encontradas: {len(signals)}")
        return signals

    def run_cycle(self):
        """Ejecuta ciclo de trading"""
        try:
            logger.info("\n" + "="*60)
            logger.info("🔄 CICLO DE TRADING REAL")
            logger.info("="*60)
            
            signals = self.scan_market()
            
            logger.info(f"\n📊 RESUMEN:")
            logger.info(f"   Activas: {len(self.operaciones_activas)}")
            logger.info(f"   Señales: {len(signals)}")
            
            if self.operaciones_activas:
                for symbol, pos in self.operaciones_activas.items():
                    entry_time = datetime.fromisoformat(pos['entry_time'])
                    duration = (datetime.now() - entry_time).total_seconds() / 60
                    pnl = ((pos['current_price'] - pos['entry_price']) / pos['entry_price'] * 100) if 'current_price' in pos else 0
                    logger.info(f"   • {symbol}: {pos['signal']} @ {pos['entry_price']:.4f} ({duration:.1f}min) PnL: {pnl:.2f}%")
            
            logger.info("="*60)
            
            return len(signals)
            
        except Exception as e:
            logger.error(f"❌ Error en ciclo: {e}")
            return 0

# ---------------------------
# CONFIGURACIÓN
# ---------------------------
def create_config():
    """Crea configuración"""
    telegram_chat_ids_str = os.environ.get('TELEGRAM_CHAT_ID', '')
    chat_ids = [cid.strip() for cid in telegram_chat_ids_str.split(',') if cid.strip()]
    
    return {
        'symbols': SYMBOLS,
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
bot_running = True  # Siempre activo

def bot_loop():
    """Loop principal"""
    global bot_running
    
    logger.info("🚀 Bot de Trading REAL iniciado")
    logger.info(f"💵 Margen por operación: ${bot.margin_per_trade}")
    logger.info(f"📈 Apalancamiento: {bot.leverage}x")
    logger.info(f"📊 Símbolos: {len(bot.config['symbols'])}")
    logger.info("🎯 Estrategia: Breakout + Reentry con confirmación RSI/Stochastic")
    
    while bot_running:
        try:
            signals = bot.run_cycle()
            
            wait_min = config['scan_interval_minutes']
            logger.info(f"⏳ Próximo ciclo en {wait_min} minutos...")
            
            for _ in range(wait_min * 60):
                if not bot_running:
                    break
                time.sleep(1)
                
        except Exception as e:
            logger.error(f"Error en loop: {e}")
            time.sleep(60)

# Iniciar bot automáticamente
def auto_start():
    time.sleep(5)
    global bot_thread
    bot_thread = threading.Thread(target=bot_loop, daemon=True)
    bot_thread.start()

# Iniciar thread
start_thread = threading.Thread(target=auto_start, daemon=True)
start_thread.start()

@app.route('/')
def index():
    return jsonify({
        'status': 'online',
        'bot': 'Bitget Breakout+Reentry Bot',
        'trading': 'ACTIVO',
        'strategy': 'Breakout + Reentry con RSI/Stochastic',
        'margin_per_trade': f'${bot.margin_per_trade}',
        'leverage': f'{bot.leverage}x',
        'active_trades': len(bot.operaciones_activas),
        'symbols': bot.config['symbols']
    })

@app.route('/health')
def health():
    return jsonify({'status': 'healthy'})

@app.route('/trades')
def get_trades():
    active = []
    for symbol, pos in bot.operaciones_activas.items():
        active.append({
            'symbol': symbol,
            'signal': pos['signal'],
            'entry': pos['entry_price'],
            'sl': pos['stop_loss'],
            'tp': pos['take_profit'],
            'size': pos.get('size', 0),
            'order_id': pos.get('order_id', '')
        })
    
    return jsonify({
        'active_trades': active,
        'count': len(active)
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    logger.info(f"🚀 Servicio iniciado en puerto {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
