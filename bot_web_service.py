import ccxt
import pandas as pd
import time
import requests
import os
import sys
import json
from datetime import datetime
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from io import BytesIO
from flask import Flask, request, jsonify
import matplotlib
matplotlib.use('Agg')
import threading
import logging

# Configurar logging básico
logging.basicConfig(level=logging.INFO, stream=sys.stdout, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ==========================================
#        CONFIGURACION (Tus datos)
# ==========================================
def crear_config_desde_entorno():
    """Configuración desde variables de entorno - Minimal para Render"""
    # Telegram: soporte para múltiples IDs separados por coma
    telegram_chat_ids_str = os.environ.get('TELEGRAM_CHAT_ID', '')
    telegram_chat_ids = [cid.strip() for cid in telegram_chat_ids_str.split(',') if cid.strip()]
    
    return {
        # Bitget API credentials (leer desde variables de entorno de Render)
        'bitget_api_key': os.environ.get('BITGET_API_KEY'),
        'bitget_api_secret': os.environ.get('BITGET_SECRET_KEY'),
        'bitget_passphrase': os.environ.get('BITGET_PASSPHRASE'),
        
        # Telegram credentials (leer desde variables de entorno de Render)
        'telegram_token': os.environ.get('TELEGRAM_TOKEN'),
        'telegram_chat_ids': telegram_chat_ids
    }

TELEGRAM_TOKEN = '8406173543:AAFIuYlFd3jtAF1Q6SNntUGn1PopgkZ7S0k'
TELEGRAM_CHAT_ID = '2108159591'

MARGEN_USDT = 1 
PALANCA_ESTRICTA = 10
MEMORIA_FILE = 'memoria_bot.json'

# ==========================================
#        FILTROS AVANZADOS - CONFIGURACION
# ==========================================
NUM_MONEDAS_ESCANEAR = 200
MIN_VOLATILIDAD_PCT = 1.0

# Configuración RSI
RSI_PERIODO = 14
RSI_OVERSOLD = 40
RSI_OVERBOUGHT = 60

# Configuración Medias Móviles Adaptativas
SMA_RAPIDA = 9
SMA_LENTA = 21

# Cooldown entre operaciones
COOLDOWN_OPERACION = 180

# Fuerza mínima de señal
MIN_FUERZA_SENAL = 6

# 1️⃣ Obtener configuración desde variables de entorno (Render)
config = crear_config_desde_entorno()

# 2️⃣ Inicializar exchange con las credenciales mapeadas
exchange = ccxt.bitget({
    'apiKey': config['bitget_api_key'],           
    'secret': config['bitget_api_secret'],        
    'password': config['bitget_passphrase'],      
    'options': {'defaultType': 'swap'},
    'enableRateLimit': True
})

def enviar_telegram(msg, config=None):
    """
    Envía mensaje a Telegram usando configuración desde entorno.
    Soporta múltiples chat IDs.
    """
    try:
        # Si no se pasa config, intentar obtenerla (para compatibilidad)
        if config is None:
            config = crear_config_desde_entorno()
        
        token = config.get('telegram_token')
        chat_ids = config.get('telegram_chat_ids', [])
        
        # Validar que hay credenciales para enviar
        if not token or not chat_ids:
            logger.warning("Credenciales de Telegram no configuradas. Mensaje no enviado.")
            return False
        
        # URL corregida (sin espacios extra)
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        
        # Enviar a todos los chat IDs configurados
        for chat_id in chat_ids:
            try:
                response = requests.post(
                    url, 
                    data={
                        'chat_id': chat_id, 
                        'text': msg, 
                        'parse_mode': 'Markdown'
                    }, 
                    timeout=10
                )
                if response.status_code == 200:
                    logger.info(f"Mensaje enviado a Telegram (chat_id: {chat_id})")
                else:
                    logger.warning(f"Telegram API error {response.status_code}: {response.text}")
            except requests.exceptions.RequestException as e:
                logger.error(f"Error enviando a chat_id {chat_id}: {e}")
                continue  # Continuar con el siguiente chat_id si hay múltiples
                
        return True
        
    except Exception as e:
        logger.error(f"Error critico en enviar_telegram: {e}")
        return False

def a_decimal_estricto(numero, precision_raw):
    if numero is None: return None
    if isinstance(precision_raw, float):
        precision_str = format(precision_raw, 'f').rstrip('0')
        decimales = len(precision_str.split('.')[1]) if '.' in precision_str else 0
    else:
        decimales = int(precision_raw)
    valor = Decimal(str(numero)).quantize(Decimal(str(10**-decimales)), rounding=ROUND_DOWN)
    return str(valor)

def cargar_memoria():
    if os.path.exists(MEMORIA_FILE):
        with open(MEMORIA_FILE, 'r') as f:
            return json.load(f)
    return {"operaciones_activas": [], "ultima_operacion_time": 0}

def guardar_memoria(datos):
    with open(MEMORIA_FILE, 'w') as f:
        json.dump(datos, f, indent=4)

def obtener_balance_real():
    try:
        balance = exchange.fetch_balance()
        for item in balance['info']:
            if item['marginCoin'] == 'USDT':
                return float(item['available'])
    except: return 0.0
    return 0.0

def detectar_order_blocks(df):
    if df['close'].iloc[-2] > df['open'].iloc[-2] and df['close'].iloc[-3] < df['open'].iloc[-3]:
        return "DEMANDA"
    if df['close'].iloc[-2] < df['open'].iloc[-2] and df['close'].iloc[-3] > df['open'].iloc[-3]:
        return "OFERTA"
    return "NEUTRO"

# ==========================================
#        FILTRO 1: TENDENCIA H1 (RSI + MM ADAPTATIVAS)
# ==========================================
def calcular_rsi(df, periodo=14):
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=periodo).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=periodo).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

def verificar_tendencia_h1(symbol):
    try:
        bars_1h = exchange.fetch_ohlcv(symbol, timeframe='1h', limit=50)
        if len(bars_1h) < 50:
            return "LATERAL"
        
        df_1h = pd.DataFrame(bars_1h, columns=['ts', 'open', 'high', 'low', 'close', 'vol'])
        
        rsi = calcular_rsi(df_1h, RSI_PERIODO).iloc[-1]
        ema_rapida = df_1h['close'].ewm(span=SMA_RAPIDA, adjust=False).mean().iloc[-1]
        ema_lenta = df_1h['close'].ewm(span=SMA_LENTA, adjust=False).mean().iloc[-1]
        precio_actual = df_1h['close'].iloc[-1]
        
        puntos_alcista = 0
        puntos_bajista = 0
        
        if rsi < RSI_OVERSOLD:
            puntos_alcista += 2
        elif rsi > RSI_OVERBOUGHT:
            puntos_bajista += 2
        
        if ema_rapida > ema_lenta:
            puntos_alcista += 2
        else:
            puntos_bajista += 2
        
        if precio_actual > ema_rapida and precio_actual > ema_lenta:
            puntos_alcista += 1
        elif precio_actual < ema_rapida and precio_actual < ema_lenta:
            puntos_bajista += 1
        
        if puntos_alcista >= 4:
            return "ALCISTA"
        elif puntos_bajista >= 4:
            return "BAJISTA"
        else:
            return "LATERAL"
            
    except Exception as e:
        print(f"Error calculando tendencia H1: {e}")
        return "LATERAL"

# ==========================================
#        FILTRO 2: VOLATILIDAD MÍNIMA
# ==========================================
def verificar_volatilidad(df):
    rango_porcentaje = (df['high'].max() - df['low'].min()) / df['close'].iloc[-1] * 100
    
    tr = pd.concat([
        df['high'] - df['low'], 
        (df['high'] - df['close'].shift()).abs(), 
        (df['low'] - df['close'].shift()).abs()
    ], axis=1).max(axis=1)
    atr = tr.rolling(14).mean().iloc[-1]
    atr_porcentaje = (atr / df['close'].iloc[-1]) * 100
    
    return rango_porcentaje >= MIN_VOLATILIDAD_PCT or atr_porcentaje >= (MIN_VOLATILIDAD_PCT / 2)

# ==========================================
#        FILTRO 6: FUERZA DE SEÑAL
# ==========================================
def calcular_fuerza_senal(zona, ratio_vol, precio, df):
    fuerza = 0
    
    if ratio_vol > 2.5:
        fuerza += 3
    elif ratio_vol > 1.8:
        fuerza += 2
    elif ratio_vol > 1.2:
        fuerza += 1
    
    if zona == "DEMANDA" and precio > df['high'].iloc[-2]:
        fuerza += 2
    elif zona == "OFERTA" and precio < df['low'].iloc[-2]:
        fuerza += 2
    
    if zona == "DEMANDA" and df['close'].iloc[-1] > df['open'].iloc[-1]:
        fuerza += 1
    elif zona == "OFERTA" and df['close'].iloc[-1] < df['open'].iloc[-1]:
        fuerza += 1
    
    sma_20 = df['close'].rolling(20).mean().iloc[-1]
    if zona == "DEMANDA" and df['close'].iloc[-1] > sma_20:
        fuerza += 1
    elif zona == "OFERTA" and df['close'].iloc[-1] < sma_20:
        fuerza += 1
    
    return fuerza

# ==========================================
#        FILTRO COOLDOWN
# ==========================================
def verificar_cooldown(memoria):
    tiempo_actual = time.time()
    tiempo_desde_ultima = tiempo_actual - memoria.get('ultima_operacion_time', 0)
    
    if tiempo_desde_ultima < COOLDOWN_OPERACION:
        print(f"Cooldown activo: {COOLDOWN_OPERACION - int(tiempo_desde_ultima)}s restantes")
        return False
    return True

def escanear_mercado():
    try:
        memoria = cargar_memoria()
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] --- SINCRONIZANDO ---")
        
        posiciones = exchange.fetch_positions()
        posiciones_reales = [p['symbol'] for p in posiciones if float(p.get('contracts', 0)) > 0]
        memoria['operaciones_activas'] = list(set(posiciones_reales))
        guardar_memoria(memoria)

        saldo = obtener_balance_real()
        activas_str = ", ".join([s.split(':')[0] for s in memoria['operaciones_activas']]) if memoria['operaciones_activas'] else "NINGUNA"
        
        print(f"SALDO DISPONIBLE: {saldo:.2f} USDT")
        print(f"ACTIVAS ({len(memoria['operaciones_activas'])}): {activas_str}")
        print("-" * 60)

        tickers = exchange.fetch_tickers()
        monedas = sorted([{'s': s, 'v': t['quoteVolume']} for s, t in tickers.items() if ':USDT' in s],
                        key=lambda x: x['v'], reverse=True)[:NUM_MONEDAS_ESCANEAR]

        print(f"Escaneando las primeras {NUM_MONEDAS_ESCANEAR} monedas por volumen...")
        
        for item in monedas:
            symbol = item['s']
            if symbol in memoria['operaciones_activas']: continue

            try:
                bars = exchange.fetch_ohlcv(symbol, timeframe='15m', limit=50)
                if len(bars) < 50:
                    continue
                    
                df = pd.DataFrame(bars, columns=['ts', 'open', 'high', 'low', 'close', 'vol'])
                
                precio = df['close'].iloc[-1]
                zona = detectar_order_blocks(df)
                vol_actual = df['vol'].iloc[-1]
                vol_prom = df['vol'].mean()
                ratio_vol = vol_actual / vol_prom

                if not verificar_volatilidad(df):
                    print(f" {symbol.split(':')[0]:<10} | Volatilidad insuficiente", end="\r")
                    continue
                
                tendencia = verificar_tendencia_h1(symbol)
                fuerza = calcular_fuerza_senal(zona, ratio_vol, precio, df)
                
                print(f" {symbol.split(':')[0]:<10} | P: {precio:<10.4f} | Vol: {ratio_vol:>4.1f}x | Zona: {zona:<6} | Tend: {tendencia:<8} | Fza: {fuerza}/7", end="\r")

                señal_valida = False
                
                if zona == "DEMANDA" and precio > df['high'].iloc[-2] and vol_actual > vol_prom:
                    if tendencia in ["ALCISTA", "LATERAL"]:
                        if fuerza >= MIN_FUERZA_SENAL:
                            if verificar_cooldown(memoria):
                                abrir_operacion(symbol, 'buy', precio, df, memoria, tendencia, fuerza)
                                señal_valida = True
                                
                elif zona == "OFERTA" and precio < df['low'].iloc[-2] and vol_actual > vol_prom:
                    if tendencia in ["BAJISTA", "LATERAL"]:
                        if fuerza >= MIN_FUERZA_SENAL:
                            if verificar_cooldown(memoria):
                                abrir_operacion(symbol, 'sell', precio, df, memoria, tendencia, fuerza)
                                señal_valida = True
                
                time.sleep(0.05)
            except: continue

        print(f"\nEscaneo de las {NUM_MONEDAS_ESCANEAR} monedas completado.")

    except Exception as e:
        print(f"\nError General: {e}")

def abrir_operacion(symbol, side, entrada, df, memoria, tendencia, fuerza):
    try:
        print(f"\nSEÑAL CONFIRMADA: {side.upper()} en {symbol}")
        print(f"   Tendencia H1: {tendencia} | Fuerza: {fuerza}/7")
        print(f"   Aplicando REGLA DE ORO: {MARGEN_USDT} USDT x{PALANCA_ESTRICTA}...")
        
        # ==========================================
        # VERIFICAR APALANCAMIENTO ANTES DE OPERAR
        # ==========================================
        try:
            exchange.set_leverage(PALANCA_ESTRICTA, symbol, params={'marginCoin': 'USDT'})
            print(f"   Apalancamiento {PALANCA_ESTRICTA}x configurado")
        except Exception as e:
            print(f"RECHAZADA: {symbol} no permite x{PALANCA_ESTRICTA} exacto.")
            print(f"   Error: {e}")
            return

        exchange.load_markets()
        market = exchange.market(symbol)
        
        # ==========================================
        # CALCULO ESTRICTO DE 1 USDT x20
        # ==========================================
        precision_amount = market['precision']['amount']
        
        # Intento 1: cálculo directo
        cant_tokens_base = (MARGEN_USDT * PALANCA_ESTRICTA) / entrada
        cant_tokens = a_decimal_estricto(cant_tokens_base, precision_amount)
        
        # Calcular margen real con este cálculo
        valor_posicion_1 = float(cant_tokens) * entrada
        margen_real_1 = valor_posicion_1 / PALANCA_ESTRICTA
        
        # ==========================================
        # CALCULO ALTERNATIVO - INTENTO 2
        # ==========================================
        if abs(margen_real_1 - MARGEN_USDT) > 0.000001:
            valor_objetivo = MARGEN_USDT * PALANCA_ESTRICTA
            decimales = len(str(precision_amount).split('.')[-1]) if '.' in str(precision_amount) else 0
            
            cant_tokens_alt = Decimal(str(valor_objetivo)).quantize(Decimal(str(10**-decimales)), rounding=ROUND_HALF_UP)
            valor_posicion_alt = float(cant_tokens_alt) * entrada
            margen_real_alt = valor_posicion_alt / PALANCA_ESTRICTA
            
            print(f"   Intento 1: {cant_tokens} tokens -> {margen_real_1:.6f} USDT")
            print(f"   Intento 2: {cant_tokens_alt} tokens -> {margen_real_alt:.6f} USDT")
            
            if abs(margen_real_alt - MARGEN_USDT) < abs(margen_real_1 - MARGEN_USDT):
                cant_tokens = str(cant_tokens_alt)
                margen_real = margen_real_alt
            else:
                margen_real = margen_real_1
        else:
            margen_real = margen_real_1
        
        print(f"   Tokens: {cant_tokens} | Precio: {entrada} USDT")
        print(f"   Valor posicion: {float(cant_tokens) * entrada:.4f} USDT")
        print(f"   Margen calculado: {margen_real:.6f} USDT")
        
        # ==========================================
        # VERIFICACIÓN CON TOLERANCIA (máx 0.04 USDT)
        # ==========================================
        diferencia = abs(margen_real - MARGEN_USDT)
        TOLERANCIA_MAX = 0.04  # Aceptar hasta 0.04 USDT de diferencia
        
        if diferencia > TOLERANCIA_MAX:
            print(f"RECHAZADA: Margen = {margen_real:.6f} USDT")
            print(f"   REGLA DE ORO: diferencia maxima {TOLERANCIA_MAX} USDT")
            print(f"   Diferencia actual: {diferencia:.6f} USDT")
            return
        
        if diferencia > 0.000001:
            print(f"Advertencia: Margen = {margen_real:.6f} USDT (diferencia: {diferencia:.6f})")
            print(f"   Dentro de tolerancia ({TOLERANCIA_MAX} USDT) - continuar...")
        else:
            print(f"   REGLA DE ORO CUMPLIDA: {margen_real:.6f} USDT exacto")
        
        # ==========================================
        # CALCULAR SL Y TP
        # ==========================================
        tr = pd.concat([df['high'] - df['low'], (df['high'] - df['close'].shift()).abs(), (df['low'] - df['close'].shift()).abs()], axis=1).max(axis=1)
        atr = tr.rolling(14).mean().iloc[-1]
        rango = df['high'].max() - df['low'].min()

        sl = entrada - (atr * 2) if side == 'buy' else entrada + (atr * 2)
        tp = entrada + (rango * 0.24) if side == 'buy' else entrada - (rango * 0.24)

        sl_str = a_decimal_estricto(sl, market['precision']['price'])
        tp_str = a_decimal_estricto(tp, market['precision']['price'])

        params = {
            'marginCoin': 'USDT', 'marginMode': 'isolated', 'tradeSide': 'open',
            'presetStopSurplusPrice': tp_str, 'presetStopLossPrice': sl_str
        }

        # ==========================================
        # EJECUTAR ORDEN
        # ==========================================
        print(f"   Ejecutando orden...")
        resultado = exchange.create_order(symbol, 'market', side, float(cant_tokens), params=params)
        
        # ==========================================
        # VERIFICACIÓN POST-OPERACIÓN - CRÍTICA
        # ==========================================
        time.sleep(1)
        
        posiciones = exchange.fetch_positions()
        posicion_encontrada = None
        for pos in posiciones:
            if pos['symbol'] == symbol and float(pos.get('contracts', 0)) > 0:
                posicion_encontrada = pos
                break
        
        if posicion_encontrada is None:
            print(f"Advertencia: No se encontro posicion despues de abrir")
            return
            
        margen_verificado = float(posicion_encontrada.get('initialMargin', 0))
        apalancamiento_verificado = float(posicion_encontrada.get('leverage', 0))
        
        print(f"   VERIFICACIÓN POST-OPERACIÓN:")
        print(f"   Margen en exchange: {margen_verificado:.6f} USDT")
        print(f"   Apalancamiento: {apalancamiento_verificado}x")
        
        # ==========================================
        # VERIFICAR QUE SEA 1 USDT x20 EXACTO
        # ==========================================
        TOLERANCIA_POST = 0.04  # Tolerancia de 0.04 USDT
        margen_ok = abs(margen_verificado - MARGEN_USDT) < TOLERANCIA_POST
        palanca_ok = apalancamiento_verificado == PALANCA_ESTRICTA
        
        if not palanca_ok:
            # El apalancamiento no es 20x - CERRAR INMEDIATAMENTE
            print(f"VERIFICACIÓN FALLIDA!")
            print(f"   Apalancamiento: {apalancamiento_verificado}x (esperado: {PALANCA_ESTRICTA}x)")
            print(f"   Cerrando posicion inmediatamente...")
            
            try:
                cerrar_params = {
                    'marginCoin': 'USDT', 
                    'marginMode': 'isolated', 
                    'tradeSide': 'close'
                }
                if side == 'buy':
                    exchange.create_order(symbol, 'market', 'sell', float(cant_tokens), params=cerrar_params)
                else:
                    exchange.create_order(symbol, 'market', 'buy', float(cant_tokens), params=cerrar_params)
                print(f"   Posicion cerrada")
            except Exception as e:
                print(f"   Error al cerrar: {e}")
            
            enviar_telegram(f"OPERACIÓN RECHAZADA POR EXCHANGE\n"
                          f"Par: {symbol}\n"
                          f"El exchange no respetó la REGLA DE ORO\n"
                          f"Apalancamiento: {apalancamiento_verificado}x (esperado: {PALANCA_ESTRICTA}x)\n"
                          f"Posicion cancelada")
            return
        
        # Verificar también si el margen excede la tolerancia
        if not margen_ok:
            print(f"Advertencia: Margen excede tolerancia: {margen_verificado:.4f} USDT")
            print(f"   Cerrando posicion...")
            
            try:
                cerrar_params = {
                    'marginCoin': 'USDT', 
                    'marginMode': 'isolated', 
                    'tradeSide': 'close'
                }
                if side == 'buy':
                    exchange.create_order(symbol, 'market', 'sell', float(cant_tokens), params=cerrar_params)
                else:
                    exchange.create_order(symbol, 'market', 'buy', float(cant_tokens), params=cerrar_params)
                print(f"   Posicion cerrada")
            except Exception as e:
                print(f"   Error al cerrar: {e}")
            
            enviar_telegram(f"MARGEN EXCEDE TOLERANCIA\n"
                          f"Par: {symbol}\n"
                          f"Margen: {margen_verificado:.4f} (tolerancia: {TOLERANCIA_POST})\n"
                          f"Apalancamiento: {apalancamiento_verificado}x\n"
                          f"Posicion cancelada")
            return
        
        print(f"   VERIFICACIÓN PASADA: {margen_verificado:.6f} USDT x{apalancamiento_verificado}")
        
        memoria['operaciones_activas'].append(symbol)
        memoria['ultima_operacion_time'] = time.time()
        guardar_memoria(memoria)
        
        enviar_telegram(f"REGLA DE ORO EXACTA\n"
                       f"Par: {symbol}\n"
                       f"Lado: {side.upper()}\n"
                       f"Margen: {margen_verificado:.6f} USDT (x{apalancamiento_verificado})\n"
                       f"Tendencia H1: {tendencia}\n"
                       f"Fuerza Señal: {fuerza}/7\n"
                       f"SL: {sl_str} | TP: {tp_str}\n"
                       f"Posicion abierta exitosamente")
        
        print(f"Exito: {side.upper()} abierto en {symbol}")
        print(f"   SL: {sl_str} | TP: {tp_str}")
        print(f"   REGLA DE ORO: {margen_verificado:.6f} USDT x{apalancamiento_verificado}")
        
    except Exception as e:
        print(f"Error: {e}")

# =========================================
# FLASK APP PARA RENDER
# =========================================

app = Flask(__name__)

# Variables globales para estado del bot
bot_state = {
    "operaciones_activas": [],
    "ultima_operacion_time": 0,
    "total_operaciones": 0,
    "bitget_conectado": True
}

def run_bot_loop():
    """Ejecuta el bot en un hilo separado"""
    logger.info("Iniciando hilo del bot de trading...")
    while True:
        try:
            escanear_mercado()
            logger.info("Ciclo de escaneo completado")
            time.sleep(30)  # Esperar 30 segundos entre ciclos
        except Exception as e:
            logger.error(f"Error en el hilo del bot: {e}", exc_info=True)
            time.sleep(60)

# Iniciar hilo del bot solo si está configurado correctamente
try:
    if config['bitget_api_key'] and config['bitget_api_secret']:
        bot_thread = threading.Thread(target=run_bot_loop, daemon=True)
        bot_thread.start()
        logger.info("Bot de trading iniciado en hilo separado")
    else:
        logger.warning("Credenciales de Bitget no configuradas. Bot no iniciado.")
except Exception as e:
    logger.warning(f"Error iniciando bot: {e}. Solo ejecutando web service.")

@app.route('/')
def index():
    return "Bot Breakout + Reentry con integración Bitget está en línea.", 200

@app.route('/webhook', methods=['POST'])
def telegram_webhook():
    if request.is_json:
        update = request.get_json()
        logger.info(f"Update recibido: {json.dumps(update)}")
        return jsonify({"status": "ok"}), 200
    return jsonify({"error": "Request must be JSON"}), 400

@app.route('/health', methods=['GET'])
def health_check():
    """Endpoint para verificar el estado del bot"""
    try:
        memoria = cargar_memoria()
        status = {
            "status": "running",
            "timestamp": datetime.now().isoformat(),
            "operaciones_activas": len(memoria.get('operaciones_activas', [])),
            "total_operaciones": bot_state["total_operaciones"],
            "bitget_conectado": bot_state["bitget_conectado"]
        }
        return jsonify(status), 200
    except Exception as e:
        logger.error(f"Error en health check: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

# Configuración automática del webhook
def setup_telegram_webhook():
    token = os.environ.get('TELEGRAM_TOKEN')
    if not token:
        logger.warning("No hay token de Telegram configurado")
        return
    
    webhook_url = os.environ.get('WEBHOOK_URL')
    if not webhook_url:
        render_url = os.environ.get('RENDER_EXTERNAL_URL')
        if render_url:
            webhook_url = f"{render_url}/webhook"
        else:
            logger.warning("No hay URL de webhook configurada")
            return
    
    try:
        logger.info(f"Configurando webhook Telegram en: {webhook_url}")
        # Eliminar webhook anterior
        requests.get(f"https://api.telegram.org/bot{token}/deleteWebhook", timeout=10)
        time.sleep(1)
        # Configurar nuevo webhook
        response = requests.get(f"https://api.telegram.org/bot{token}/setWebhook?url={webhook_url}", timeout=10)
        
        if response.status_code == 200:
            logger.info("Webhook de Telegram configurado correctamente")
        else:
            logger.error(f"Error configurando webhook: {response.status_code} - {response.text}")
    except Exception as e:
        logger.error(f"Error configurando webhook: {e}")

if __name__ == '__main__':
    logger.info("Iniciando aplicación Flask...")
    
    # Mostrar configuración inicial
    print("="*60)
    print("     BOT ESTRATEGIA 4.5 - CON FILTROS AVANZADOS")
    print("     REGLA DE ORO: 1 USDT | x10 | POSICIÓN EXACTA")
    print("="*60)
    print(f"\nFiltros activos:")
    print(f"   - Filtro 1: Tendencia H1 (RSI + EMAs)")
    print(f"   - Filtro 2: Volatilidad minima ({MIN_VOLATILIDAD_PCT}%)")
    print(f"   - Filtro 6: Fuerza de señal (min {MIN_FUERZA_SENAL}/7)")
    print(f"   - Cooldown: {COOLDOWN_OPERACION}s entre operaciones")
    print(f"   - Monedas a escanear: {NUM_MONEDAS_ESCANEAR}")
    print("-"*60)
    
    setup_telegram_webhook()
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port)
