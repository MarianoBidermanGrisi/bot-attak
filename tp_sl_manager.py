"""
tp_sl_manager.py
Módulo dedicado exclusivamente para la gestión de Take Profit (TP) y Stop Loss (SL)
utilizando el endpoint correcto de Bitget API v2.
Endpoint: POST /api/v2/mix/order/place-tpsl-order
planType válido: profit_plan, loss_plan, moving_plan, pos_profit, pos_loss
REGLA DE ORO: Este módulo NO modifica la lógica de trading existente.
"""
import hmac
import hashlib
import base64
import time
import json
import logging
import math
import requests
from typing import Optional, Dict, Any, Tuple

logger = logging.getLogger(__name__)


class BitgetTPSLManager:
    """Clase para gestionar órdenes de TP/SL con Bitget API v2."""
    BASE_URL = "https://api.bitget.com"
    TIMEOUT = 10

    def __init__(self, api_key: str, secret_key: str, passphrase: str):
        self.api_key = api_key
        self.secret_key = secret_key
        self.passphrase = passphrase

    def _generate_headers(self, timestamp: str, request_path: str, body: str) -> Dict[str, str]:
        message = timestamp + "POST" + request_path + body
        signature = base64.b64encode(
            hmac.new(
                self.secret_key.encode('utf-8'),
                message.encode('utf-8'),
                hashlib.sha256
            ).digest()
        ).decode('utf-8')
        return {
            "ACCESS-KEY": self.api_key,
            "ACCESS-SIGN": signature,
            "ACCESS-TIMESTAMP": timestamp,
            "ACCESS-PASSPHRASE": self.passphrase,
            "Content-Type": "application/json",
            "locale": "en-US"
        }

    def _make_request(self, endpoint: str, body: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        timestamp = str(int(time.time() * 1000))
        request_path = f"/api/v2{endpoint}"
        body_str = json.dumps(body, separators=(',', ':'))
        headers = self._generate_headers(timestamp, request_path, body_str)
        url = self.BASE_URL + request_path
        try:
            response = requests.post(url, headers=headers, data=body_str, timeout=self.TIMEOUT)
            data = response.json()
            if data.get("code") == "00000":
                logger.info(f"✅ TP/SL colocado exitosamente: {data}")
                return True, data
            else:
                logger.error(f"❌ Error TP/SL: {data}")
                return False, data
        except Exception as e:
            logger.error(f"❌ Excepción en TP/SL: {str(e)}")
            return False, {"code": "99999", "msg": str(e)}

    def _obtener_precision_adaptada(self, price: float) -> int:
        """
        Obtiene la precisión adaptativa basada en el precio para evitar redondeo a cero.
        Para símbolos con precios muy pequeños (SHIBUSDT, PEPE, ENS, XLM, etc.), la precisión
        de priceScale no es suficiente. Este método calcula la precisión necesaria
        para mantener al menos 4-6 dígitos significativos.
        """
        try:
            price = float(price)
            
            # Para precios < 1, siempre usar alta precisión para evitar redondeo a cero
            if price < 1:
                if price < 0.00001:
                    return 12  # Para PEPE, SHIB y similares
                elif price < 0.0001:
                    return 10  # Para memecoins extremos
                elif price < 0.001:
                    return 8   # Para memecoins y precios muy pequeños
                elif price < 0.01:
                    return 7   # Para precios como ENS (~0.008)
                elif price < 0.1:
                    return 6   # Para precios como PHA (~0.1)
                elif price < 1:
                    return 5   # Para precios como XLM (~0.2)
            else:
                # Para precios >= 1, usar 4 decimales como mínimo
                return 4
        except Exception as e:
            logger.error(f"Error calculando precisión adaptativa: {e}")
            return 8  # Fallback seguro

    def _redondear_precio_manual(self, price: float, precision: int, trade_direction: str = None) -> str:
        """
        Redondea el precio con una precisión específica, asegurando que sea un múltiplo válido.
        IMPORTANTE: Para la API de Bitget, el precio debe ser un múltiplo del priceStep.
        
        Para Stop Loss:
        - LONG: El SL debe redondearse hacia ABAJO (menor que el precio de entrada)
        - SHORT: El SL debe redondearse hacia ARRIBA (mayor que el precio de entrada)
        """
        try:
            price = float(price)
            if price == 0:
                return "0.0"
            
            tick_size = 10 ** (-precision)
            precio_redondeado = round(price / tick_size) * tick_size
            
            # AJUSTE INTELIGENTE PARA STOP LOSS
            if trade_direction and trade_direction in ['LONG', 'SHORT']:
                precio_redondeado = float(f"{precio_redondeado:.{precision}f}")
                
                if trade_direction == 'LONG':
                    # Para LONG: SL debe ser menor que precio de entrada
                    if precio_redondeado >= price:
                        precio_redondeado = math.floor(price / tick_size) * tick_size
                elif trade_direction == 'SHORT':
                    # Para SHORT: SL debe ser mayor que precio de entrada
                    if precio_redondeado <= price:
                        precio_redondeado = math.ceil(price / tick_size) * tick_size
            
            # Formatear para evitar errores de punto flotante
            precio_formateado = f"{precio_redondeado:.{precision}f}"
            
            # Verificar que no sea cero
            if float(precio_formateado) == 0.0 and price > 0:
                nueva_precision = precision + 4
                return self._redondear_precio_manual(price, nueva_precision, trade_direction)
            
            return precio_formateado
        except Exception as e:
            logger.error(f"Error redondeando precio manualmente: {e}")
            return str(price)

    def place_take_profit(
        self, symbol: str, product_type: str, margin_coin: str,
        trigger_price: float, hold_side: str, size: str,
        trigger_type: str = "mark_price", execute_price: str = "0",
        client_oid: Optional[str] = None
    ) -> Tuple[bool, Dict[str, Any]]:
        """
        Colocar orden de Take Profit con precisión adaptativa.
        """
        # Usar precisión adaptativa para precios muy pequeños
        precision = self._obtener_precision_adaptada(trigger_price)
        trigger_price_formatted = self._redondear_precio_manual(trigger_price, precision)
        
        body = {
            "symbol": symbol,
            "productType": product_type,
            "marginCoin": margin_coin,
            "planType": "profit_plan",
            "triggerPrice": trigger_price_formatted,
            "triggerType": trigger_type,
            "executePrice": execute_price,
            "holdSide": hold_side,
            "size": size,
            "clientOid": client_oid or f"tp_{int(time.time() * 1000)}",
            # PARÁMETROS OBLIGATORIOS PARA API V2
            "delegateType": "1",  # 0 = límite, 1 = mercado
            "stopSurplusTriggerType": "mark_price"
        }
        
        logger.info(f"📌 TP para {symbol}: precio={trigger_price}, formatted={trigger_price_formatted}, precision={precision}")
        return self._make_request("/mix/order/place-tpsl-order", body)

    def place_stop_loss(
        self, symbol: str, product_type: str, margin_coin: str,
        trigger_price: float, hold_side: str, size: str,
        trigger_type: str = "mark_price", execute_price: str = "0",
        client_oid: Optional[str] = None
    ) -> Tuple[bool, Dict[str, Any]]:
        """
        Colocar orden de Stop Loss con precisión adaptativa y redondeo correcto.
        """
        # Usar precisión adaptativa para precios muy pequeños
        precision = self._obtener_precision_adaptada(trigger_price)
        
        # Determinar dirección para redondeo correcto del SL
        trade_direction = 'LONG' if hold_side == 'long' else 'SHORT'
        trigger_price_formatted = self._redondear_precio_manual(trigger_price, precision, trade_direction)
        
        body = {
            "symbol": symbol,
            "productType": product_type,
            "marginCoin": margin_coin,
            "planType": "loss_plan",
            "triggerPrice": trigger_price_formatted,
            "triggerType": trigger_type,
            "executePrice": execute_price,
            "holdSide": hold_side,
            "size": size,
            "clientOid": client_oid or f"sl_{int(time.time() * 1000)}",
            # PARÁMETROS OBLIGATORIOS PARA API V2
            "delegateType": "1",  # 0 = límite, 1 = mercado
            "stopLossTriggerType": "mark_price"
        }
        
        logger.info(f"🛑 SL para {symbol}: precio={trigger_price}, formatted={trigger_price_formatted}, precision={precision}, direccion={trade_direction}")
        return self._make_request("/mix/order/place-tpsl-order", body)

    def place_tp_sl_combined(
        self, symbol: str, product_type: str, margin_coin: str,
        tp_price: float, sl_price: float, hold_side: str, size: str,
        trigger_type: str = "mark_price", execute_price: str = "0"
    ) -> Tuple[bool, bool, Dict[str, Any], Dict[str, Any]]:
        """
        Colocar TP y SL juntos con precisión adaptativa y manejo de errores.
        """
        # Primero intentar colocar TP
        tp_success, tp_response = self.place_take_profit(
            symbol, product_type, margin_coin, tp_price, hold_side, size, trigger_type, execute_price
        )
        
        # Si TP falla, intentar reintentar con precios más precisos
        if not tp_success:
            logger.warning(f"⚠️ TP inicial falló, reintentando con alta precisión para {symbol}...")
            tp_success, tp_response = self.place_take_profit(
                symbol, product_type, margin_coin, tp_price, hold_side, size, trigger_type, execute_price
            )
        
        # Esperar un poco entre TP y SL
        time.sleep(0.5)
        
        # Luego colocar SL
        sl_success, sl_response = self.place_stop_loss(
            symbol, product_type, margin_coin, sl_price, hold_side, size, trigger_type, execute_price
        )
        
        # Si SL falla, intentar reintentar con precios más precisos
        if not sl_success:
            logger.warning(f"⚠️ SL inicial falló, reintentando con alta precisión para {symbol}...")
            sl_success, sl_response = self.place_stop_loss(
                symbol, product_type, margin_coin, sl_price, hold_side, size, trigger_type, execute_price
            )
        
        return tp_success, sl_success, tp_response, sl_response


def create_tpsl_manager(api_key: str, secret_key: str, passphrase: str) -> BitgetTPSLManager:
    """Crea una instancia del gestor de TP/SL."""
    return BitgetTPSLManager(api_key, secret_key, passphrase)
