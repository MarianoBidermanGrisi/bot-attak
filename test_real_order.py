"""
Módulo de prueba para operaciones reales en Bitget
Abre una orden de prueba con SL y TP para verificar la conexión y ejecución
"""

import os
import time
import hmac
import hashlib
import base64
import json
import requests
from datetime import datetime

# Configuración de la API de Bitget
API_KEY = os.environ.get('BITGET_API_KEY', '')
SECRET_KEY = os.environ.get('BITGET_SECRET_KEY', '')
PASSPHRASE = os.environ.get('BITGET_PASSPHRASE', '')

# Base URL de Bitget Futures
BASE_URL = "https://api.bitget.com"

# Configuración de la operación de prueba
SYMBOL = "BTCUSDT"          # Par a operar
MARGIN_COIN = "USDT"        # Moneda de margen
SIZE = "0.001"              # Tamaño mínimo (ajustar según el par)
SIDE = "open_long"          # open_long, open_short, close_long, close_short
LEVERAGE = "5"              # Apalancamiento


def get_timestamp():
    """Obtiene timestamp en milisegundos"""
    return str(int(time.time() * 1000))


def sign(message, secret_key):
    """Firma el mensaje con HMAC-SHA256"""
    mac = hmac.new(
        bytes(secret_key, encoding='utf8'),
        bytes(message, encoding='utf-8'),
        digestmod='sha256'
    )
    return base64.b64encode(mac.digest()).decode()


def get_headers(method, request_path, body=""):
    """Genera headers con autenticación para Bitget"""
    timestamp = get_timestamp()
    
    if body:
        message = timestamp + method.upper() + request_path + body
    else:
        message = timestamp + method.upper() + request_path
    
    signature = sign(message, SECRET_KEY)
    
    return {
        "ACCESS-KEY": API_KEY,
        "ACCESS-SIGN": signature,
        "ACCESS-TIMESTAMP": timestamp,
        "ACCESS-PASSPHRASE": PASSPHRASE,
        "Content-Type": "application/json",
        "locale": "en-US"
    }


def get_current_price():
    """Obtiene el precio actual del par"""
    endpoint = f"/api/v2/mix/market/ticker?symbol={SYMBOL}&productType=USDT-FUTURES"
    url = BASE_URL + endpoint
    
    try:
        response = requests.get(url, timeout=10)
        data = response.json()
        if data.get("code") == "00000":
            return float(data["data"][0]["lastPr"])
    except Exception as e:
        print(f"❌ Error obteniendo precio: {e}")
    return None


def set_leverage():
    """Configura el apalancamiento antes de abrir la posición"""
    endpoint = "/api/v2/mix/account/set-leverage"
    body = {
        "symbol": SYMBOL,
        "productType": "USDT-FUTURES",
        "marginCoin": MARGIN_COIN,
        "leverage": LEVERAGE,
        "holdSide": "long"  # Configurar para long
    }
    body_str = json.dumps(body)
    headers = get_headers("POST", endpoint, body_str)
    
    try:
        response = requests.post(BASE_URL + endpoint, headers=headers, data=body_str, timeout=10)
        data = response.json()
        print(f"📊 Leverage configurado: {data}")
        return data.get("code") == "00000"
    except Exception as e:
        print(f"❌ Error configurando leverage: {e}")
        return False


def open_test_order():
    """
    Abre una orden de prueba con Stop Loss y Take Profit
    Retorna True si la orden se ejecutó correctamente
    """
    print("\n" + "="*60)
    print("🚀 INICIANDO PRUEBA DE OPERACIÓN REAL EN BITGET")
    print("="*60)
    
    # Verificar credenciales
    if not all([API_KEY, SECRET_KEY, PASSPHRASE]):
        print("❌ ERROR: Faltan credenciales de Bitget en variables de entorno")
        print("   Requeridas: BITGET_API_KEY, BITGET_SECRET_KEY, BITGET_PASSPHRASE")
        return False
    
    print(f"✅ Credenciales encontradas")
    print(f"📌 Par: {SYMBOL}")
    print(f"📌 Tamaño: {SIZE}")
    print(f"📌 Apalancamiento: {LEVERAGE}x")
    
    # Obtener precio actual
    current_price = get_current_price()
    if not current_price:
        print("❌ No se pudo obtener el precio actual")
        return False
    
    print(f"💰 Precio actual: {current_price} USDT")
    
    # Calcular SL y TP (ejemplo: 1% SL, 2% TP para long)
    sl_price = round(current_price * 0.99, 2)   # -1% Stop Loss
    tp_price = round(current_price * 1.02, 2)   # +2% Take Profit
    
    print(f"🛡️ Stop Loss: {sl_price} USDT (-1%)")
    print(f"🎯 Take Profit: {tp_price} USDT (+2%)")
    
    # Configurar apalancamiento
    set_leverage()
    
    # Preparar la orden con SL y TP
    endpoint = "/api/v2/mix/order/place-order"
    
    order_body = {
        "symbol": SYMBOL,
        "productType": "USDT-FUTURES",
        "marginMode": "crossed",
        "marginCoin": MARGIN_COIN,
        "size": SIZE,
        "side": "buy",
        "tradeSide": "open",
        "orderType": "market",
        "presetStopSurplusPrice": str(tp_price),  # Take Profit
        "presetStopLossPrice": str(sl_price),      # Stop Loss
        "clientOid": f"test_order_{int(time.time())}"
    }
    
    body_str = json.dumps(order_body)
    headers = get_headers("POST", endpoint, body_str)
    
    print("\n📤 Enviando orden...")
    print(f"   Body: {json.dumps(order_body, indent=2)}")
    
    try:
        response = requests.post(
            BASE_URL + endpoint,
            headers=headers,
            data=body_str,
            timeout=15
        )
        
        result = response.json()
        print(f"\n📥 Respuesta de Bitget:")
        print(json.dumps(result, indent=2))
        
        if result.get("code") == "00000":
            order_id = result.get("data", {}).get("orderId", "N/A")
            print("\n" + "="*60)
            print("✅ ¡ORDEN EJECUTADA EXITOSAMENTE!")
            print(f"   Order ID: {order_id}")
            print(f"   SL configurado: {sl_price}")
            print(f"   TP configurado: {tp_price}")
            print("="*60 + "\n")
            return True
        else:
            error_msg = result.get("msg", "Error desconocido")
            print(f"\n❌ Error en la orden: {error_msg}")
            return False
            
    except Exception as e:
        print(f"\n❌ Excepción al enviar orden: {e}")
        return False


def get_open_positions():
    """Obtiene las posiciones abiertas para verificar"""
    endpoint = f"/api/v2/mix/position/all-position?productType=USDT-FUTURES&marginCoin={MARGIN_COIN}"
    headers = get_headers("GET", endpoint)
    
    try:
        response = requests.get(BASE_URL + endpoint, headers=headers, timeout=10)
        data = response.json()
        
        if data.get("code") == "00000":
            positions = data.get("data", [])
            print(f"\n📊 Posiciones abiertas: {len(positions)}")
            for pos in positions:
                if float(pos.get("total", 0)) > 0:
                    print(f"   {pos.get('symbol')}: {pos.get('holdSide')} - Size: {pos.get('total')}")
            return positions
    except Exception as e:
        print(f"❌ Error obteniendo posiciones: {e}")
    return []


def run_test():
    """
    Función principal para ejecutar la prueba.
    Llamar esta función cuando el bot esté completamente inicializado.
    """
    print("\n" + "*"*60)
    print("*  MÓDULO DE PRUEBA DE OPERACIÓN REAL - BITGET FUTURES")
    print("*  ⚠️  ADVERTENCIA: Esta operación usará DINERO REAL")
    print("*"*60 + "\n")
    
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"⏰ Timestamp: {timestamp}")
    
    # Ejecutar la prueba
    success = open_test_order()
    
    if success:
        # Verificar posiciones después de abrir
        time.sleep(2)
        get_open_positions()
    
    return success


# Para integrar con el bot principal
def on_bot_ready():
    """
    Hook para llamar cuando el bot esté listo.
    Agregar esta llamada al final de la inicialización del bot.
    """
    print("\n🤖 Bot inicializado - Ejecutando prueba de operación real...")
    return run_test()


if __name__ == "__main__":
    # Ejecutar directamente para pruebas
    run_test()
