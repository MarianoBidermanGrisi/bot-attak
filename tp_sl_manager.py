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
        body_str = json.dumps(body)
        headers = self._generate_headers(timestamp, request_path, body_str)
        url = self.BASE_URL + request_path
        
        try:
            response = requests.post(url, headers=headers, data=body_str, timeout=self.TIMEOUT)
            data = response.json()
            if data.get("code") == "00000":
                return True, data
            else:
                logger.error(f"❌ Error TP/SL: {data}")
                return False, data
        except Exception as e:
            logger.error(f"❌ Excepción: {str(e)}")
            return False, {"code": "99999", "msg": str(e)}
    
    def place_take_profit(
        self, symbol: str, product_type: str, margin_coin: str,
        trigger_price: str, hold_side: str, size: str,
        trigger_type: str = "mark_price", execute_price: str = "0",
        client_oid: Optional[str] = None
    ) -> Tuple[bool, Dict[str, Any]]:
        body = {
            "symbol": symbol,
            "productType": product_type,
            "marginCoin": margin_coin,
            "planType": "profit_plan",
            "triggerPrice": trigger_price,
            "triggerType": trigger_type,
            "executePrice": execute_price,
            "holdSide": hold_side,
            "size": size,
            "clientOid": client_oid or f"tp_{int(time.time() * 1000)}"
        }
        logger.info(f"📌 TP para {symbol}: {trigger_price}")
        return self._make_request("/mix/order/place-tpsl-order", body)
    
    def place_stop_loss(
        self, symbol: str, product_type: str, margin_coin: str,
        trigger_price: str, hold_side: str, size: str,
        trigger_type: str = "mark_price", execute_price: str = "0",
        client_oid: Optional[str] = None
    ) -> Tuple[bool, Dict[str, Any]]:
        body = {
            "symbol": symbol,
            "productType": product_type,
            "marginCoin": margin_coin,
            "planType": "loss_plan",
            "triggerPrice": trigger_price,
            "triggerType": trigger_type,
            "executePrice": execute_price,
            "holdSide": hold_side,
            "size": size,
            "clientOid": client_oid or f"sl_{int(time.time() * 1000)}"
        }
        logger.info(f"🛑 SL para {symbol}: {trigger_price}")
        return self._make_request("/mix/order/place-tpsl-order", body)
    
    def place_tp_sl_combined(
        self, symbol: str, product_type: str, margin_coin: str,
        tp_price: str, sl_price: str, hold_side: str, size: str,
        trigger_type: str = "mark_price", execute_price: str = "0"
    ) -> Tuple[bool, bool, Dict[str, Any], Dict[str, Any]]:
        tp_success, tp_response = self.place_take_profit(
            symbol, product_type, margin_coin, tp_price, hold_side, size, trigger_type, execute_price
        )
        sl_success, sl_response = self.place_stop_loss(
            symbol, product_type, margin_coin, sl_price, hold_side, size, trigger_type, execute_price
        )
        return tp_success, sl_success, tp_response, sl_response


def create_tpsl_manager(api_key: str, secret_key: str, passphrase: str) -> BitgetTPSLManager:
    """Crea una instancia del gestor de TP/SL."""
    return BitgetTPSLManager(api_key, secret_key, passphrase)
