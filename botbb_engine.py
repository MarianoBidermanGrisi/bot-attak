"""
BotBB Engine — Motor de ejecucion + Estrategia para Bitget.
Bollinger Bands + MACD Overlay + Heikin Ashi.
LONG y SHORT. Timeframe 5min.
"""

import os
import sys
import csv
import json
import math
import time
import asyncio
import logging
import requests
import numpy as np
import pandas as pd
import ccxt
import ccxt.async_support as ccxt_async
from ccxt import (
    BadRequest,
    AuthenticationError,
    PermissionDenied,
    RateLimitExceeded,
    ExchangeError,
    ExchangeNotAvailable,
    NetworkError,
    RequestTimeout,
    DDoSProtection,
)
from datetime import datetime, timedelta
from io import BytesIO
import matplotlib
matplotlib.use('Agg')  # Backend sin GUI
import matplotlib.pyplot as plt

# ==========================================================
# LOGGING
# ==========================================================
LOG_TO_FILE = os.environ.get("BOT_LOG_TO_FILE", "1") == "1"
LOG_LEVEL = os.environ.get("BOT_LOG_LEVEL", "INFO")

_handlers = [logging.StreamHandler()]
if LOG_TO_FILE:
    _log_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "botbb.log")
    _handlers.append(logging.FileHandler(_log_file, encoding="utf-8"))

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=_handlers,
)
log = logging.getLogger("botbb")

# ==========================================================
# CONFIG DEFAULTS
# ==========================================================
DEFAULT_CONFIG = {
    # --- Estrategia ---
    "bb_length":            20,      # Bollinger Bands periodo
    "bb_mult":              2.0,     # Bollinger Bands desviacion
    "macd_fast":            12,      # MACD EMA rapida
    "macd_slow":            26,      # MACD EMA lenta
    "macd_signal":          9,       # MACD signal SMA
    "confirmation_window":  8,       # Max velas para confirmar
    # --- Entrada ---
    "sl_buffer_pct":        0.0005,  # SL buffer 0.05%
    "rr_ratio":             2.0,     # Risk:Reward 1:2
    # --- Gestion ---
    "be_trigger_pct":       0.004,   # BE al 0.4%
    "be_offset_pct":        0.002,   # BE offset +0.2%
    "trailing_dist_pct":    0.007,   # Trailing 0.7% del pico
    "leverage":             10.0,    # Apalancamiento fijo
    "max_open_positions":   5,       # Maximo simultaneo
    # --- Cooldown ---
    "max_consecutive_losses": 4,     # Perdidas consecutivas
    "cooldown_hours":       4,       # Horas de pausa
    # --- Escaneo ---
    "scan_interval_sec":    300,     # Cada 5 min
    "top_symbols_count":    100,     # Top volumen
    "ohlcv_limit":          100,     # Velas a descargar
    "timeframe":            "5m",    # Timeframe
}


# ==========================================================
# BOTBBENGINE
# ==========================================================
class BotBBEngine:
    """
    Motor de ejecucion + Estrategia para Bitget.
    Bollinger Bands + MACD Overlay + Heikin Ashi.
    """

    def __init__(self, config: dict = None):
        self.cfg = {**DEFAULT_CONFIG, **(config or {})}
        self.exchange = None

        # --- Memoria de sesion ---
        self.alerts_history: dict = {}
        self.peak_prices: dict = {}
        self.cooldowns: dict = {}
        self.session_active: set = set()
        self.trade_entries: dict = {}
        self.trail_counts: dict = {}
        self.premature_sl_monitor: dict = {}
        self.adverse_prices: dict = {}

        # --- Cooldown por perdidas consecutivas (global) ---
        self.consecutive_losses = 0
        self.cooldown_until = None
        self.last_scan_time = 0

        # --- Credenciales ---
        self.api_key = os.environ.get("BITGET_API_KEY", "")
        self.secret_key = os.environ.get("BITGET_SECRET_KEY", "")
        self.passphrase = os.environ.get("BITGET_PASSPHRASE", "")
        self.telegram_token = os.environ.get("TELEGRAM_TOKEN", "")
        self.telegram_chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

        # --- Archivos ---
        base = os.path.dirname(os.path.abspath(__file__))
        self.trades_csv = os.path.join(base, "trades.csv")
        self.trade_entries_path = os.path.join(base, "trade_entries.json")
        self.premature_sl_csv = os.path.join(base, "premature_sl.csv")
        self.price_paths_dir = os.path.join(base, "price_paths")
        os.makedirs(self.price_paths_dir, exist_ok=True)

        # --- CSV Headers ---
        self.TRADE_CSV_HEADERS = [
            "entry_time", "exit_time", "symbol", "side", "entry_price", "exit_price",
            "sl_price", "tp_price", "sl_pct", "tp_pct", "quantity",
            "balance_before", "balance_after", "pnl", "fees", "net_pnl",
            "status", "duration_hours", "close_reason",
            "be_triggered", "be_price", "trail_count", "trail_peak_price", "trail_final_sl",
            "entry_weekday", "entry_hour", "size_usdt", "risk_pct",
            "max_favorable_pct", "max_adverse_pct",
        ]
        self.PREMATURE_CSV_HEADERS = [
            "entry_time", "sl_time", "symbol", "side", "entry_price", "sl_price",
            "tp_price", "sl_pct", "tp_reached", "tp_reached_time", "hours_to_tp_after_sl",
            "entry_weekday", "entry_hour", "hit_be_before_sl", "max_favorable_before_sl",
        ]

    # ==========================================================
    # ERROR HANDLER — Bitget / ccxt
    # ==========================================================
    def _handle_exchange_error(self, context: str, e: Exception) -> str:
        """
        Maneja errores especificos de Bitget/ccxt.
        Retorna el nivel de gravedad: 'critical', 'retry', 'skip', 'ignore'.
        """
        msg = getattr(e, "message", str(e))
        code = getattr(e, "code", None)

        # --- Autenticacion (401xx) ---
        if isinstance(e, AuthenticationError):
            log.critical(f"[AUTH] {context}: API key/secret/passphrase invalido. Codigo: {code} | {msg}")
            return "critical"

        # --- Permisos (403xx) ---
        if isinstance(e, PermissionDenied):
            log.critical(f"[PERM] {context}: Sin permisos o IP no whitelist. Codigo: {code} | {msg}")
            return "critical"

        # --- Parametros invalidos (400xx) ---
        if isinstance(e, BadRequest):
            log.error(f"[400] {context}: Parametros invalidos. Codigo: {code} | {msg}")
            return "skip"

        # --- Rate Limit (429xx) ---
        if isinstance(e, RateLimitExceeded):
            log.warning(f"[429] {context}: Rate limit alcanzado. Reintentando en 5s...")
            time.sleep(5)
            return "retry"

        # --- DDoS Protection ---
        if isinstance(e, DDoSProtection):
            log.warning(f"[DDOS] {context}: Proteccion DDoS activada. Reintentando en 10s...")
            time.sleep(10)
            return "retry"

        # --- Timeout de red ---
        if isinstance(e, RequestTimeout):
            log.warning(f"[TIMEOUT] {context}: Timeout de conexion. Reintentando en 3s...")
            time.sleep(3)
            return "retry"

        # --- Error de red ---
        if isinstance(e, NetworkError):
            log.warning(f"[NET] {context}: Error de red. Reintentando en 5s... | {msg}")
            time.sleep(5)
            return "retry"

        # --- Exchange no disponible ---
        if isinstance(e, ExchangeNotAvailable):
            log.warning(f"[DOWN] {context}: Exchange no disponible. Reintentando en 10s... | {msg}")
            time.sleep(10)
            return "retry"

        # --- Error generico del exchange (500xx) ---
        if isinstance(e, ExchangeError):
            log.error(f"[500] {context}: Error del exchange. Codigo: {code} | {msg}")
            return "skip"

        # --- Cualquier otra excepcion ---
        log.error(f"[UNK] {context}: {type(e).__name__}: {msg}")
        return "skip"

    # ==========================================================
    # CONEXION
    # ==========================================================
    def connect(self) -> bool:
        try:
            self.exchange = ccxt.bitget({
                "apiKey": self.api_key,
                "secret": self.secret_key,
                "password": self.passphrase,
                "enableRateLimit": True,
                "options": {"defaultType": "swap"},
            })
            log.info("Conexion exitosa a Bitget.")
            self._load_trade_entries()
            return True
        except AuthenticationError as e:
            log.critical(f"[AUTH] API key/secret/passphrase invalido: {e}")
            return False
        except PermissionDenied as e:
            log.critical(f"[PERM] Sin permisos o IP no whitelist: {e}")
            return False
        except RateLimitExceeded as e:
            log.warning(f"[429] Rate limit al conectar. Reintentando en 5s...")
            time.sleep(5)
            return self.connect()
        except NetworkError as e:
            log.warning(f"[NET] Error de red al conectar: {e}")
            return False
        except Exception as e:
            log.critical(f"Error de conexion: {e}")
            return False

    def shutdown(self):
        if self.exchange:
            try:
                self.exchange.close()
            except Exception:
                pass
            self.exchange = None
            log.info("Conexion cerrada.")

    # ==========================================================
    # BALANCE
    # ==========================================================
    def get_balance(self) -> float:
        try:
            data = self.exchange.fetch_balance()
            return float(data["total"].get("USDT", 0))
        except RateLimitExceeded as e:
            log.warning(f"[429] get_balance: Rate limit. {e}")
            time.sleep(5)
            return 0.0
        except NetworkError as e:
            log.warning(f"[NET] get_balance: Error de red. {e}")
            return 0.0
        except ExchangeError as e:
            log.error(f"[500] get_balance: {e}")
            return 0.0
        except Exception as e:
            log.error(f"Error obteniendo balance: {e}")
            return 0.0

    # ==========================================================
    # TOP SYMBOLS POR VOLUMEN
    # ==========================================================
    def get_top_symbols(self, n: int = 100) -> list:
        try:
            tickers = self.exchange.fetch_tickers()
            ranked = [
                (s, float(t.get("quoteVolume", 0)))
                for s, t in tickers.items()
                if s.endswith("/USDT:USDT")
            ]
            ranked.sort(key=lambda x: x[1], reverse=True)
            return [s for s, _ in ranked[:n]]
        except RateLimitExceeded as e:
            log.warning(f"[429] get_top_symbols: Rate limit. {e}")
            time.sleep(5)
            return []
        except NetworkError as e:
            log.warning(f"[NET] get_top_symbols: Error de red. {e}")
            return []
        except ExchangeError as e:
            log.error(f"[500] get_top_symbols: {e}")
            return []
        except Exception as e:
            log.error(f"Error fetching top symbols: {e}")
            return []

    # ==========================================================
    # FETCH ASINCRONO DE OHLCV
    # ==========================================================
    async def _fetch_single(self, exch, symbol: str, timeframe: str, limit: int):
        try:
            ohlcv = await exch.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
            return symbol, ohlcv
        except RateLimitExceeded:
            await asyncio.sleep(2)
            return symbol, None
        except NetworkError:
            await asyncio.sleep(1)
            return symbol, None
        except Exception:
            return symbol, None

    async def fetch_ohlcv_batch(self, symbols: list, timeframe: str = "5m", limit: int = 100) -> dict:
        exch = ccxt_async.bitget({
            "apiKey": self.api_key,
            "secret": self.secret_key,
            "password": self.passphrase,
            "enableRateLimit": True,
            "options": {"defaultType": "swap"},
        })
        try:
            tasks = [self._fetch_single(exch, s, timeframe, limit) for s in symbols]
            results = await asyncio.gather(*tasks)
            return {r[0]: r[1] for r in results}
        finally:
            await exch.close()

    def fetch_ohlcv_sync(self, symbols: list, timeframe: str = "5m", limit: int = 100) -> dict:
        return asyncio.run(self.fetch_ohlcv_batch(symbols, timeframe, limit))

    # ==========================================================
    # TELEGRAM
    # ==========================================================
    def send_telegram(self, message: str):
        if not self.telegram_token or not self.telegram_chat_id:
            return
        try:
            url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
            requests.post(
                url,
                data={"chat_id": self.telegram_chat_id, "text": message, "parse_mode": "Markdown"},
                timeout=10,
            )
            log.info(f"TG: {message[:80].replace(chr(10), ' ')}...")
        except requests.exceptions.Timeout:
            log.warning("[TG] Timeout enviando mensaje Telegram.")
        except Exception as e:
            log.warning(f"[TG] Error enviando mensaje: {e}")

    # ==========================================================
    # TELEGRAM — ENVIAR FOTO
    # ==========================================================
    def send_telegram_photo(self, buf: BytesIO, caption: str = "") -> bool:
        """Envia una imagen PNG a Telegram via sendPhoto."""
        if not self.telegram_token or not self.telegram_chat_id:
            return False
        if not buf:
            return False
        try:
            buf.seek(0)
            url = f"https://api.telegram.org/bot{self.telegram_token}/sendPhoto"
            files = {"photo": ("chart.png", buf.read(), "image/png")}
            data = {"chat_id": self.telegram_chat_id}
            if caption:
                data["caption"] = caption[:1024]
                data["parse_mode"] = "Markdown"
            r = requests.post(url, files=files, data=data, timeout=60)
            if r.status_code == 200:
                log.info("[TG] Grafico enviado a Telegram.")
                return True
            else:
                log.warning(f"[TG] Error enviando foto: HTTP {r.status_code}")
                return False
        except requests.exceptions.Timeout:
            log.warning("[TG] Timeout enviando foto a Telegram.")
            return False
        except Exception as e:
            log.warning(f"[TG] Error enviando foto: {e}")
            return False

    # ==========================================================
    # GENERAR GRAFICO DE SENAL (BB + Heikin Ashi — 1 panel)
    # ==========================================================
    def generar_grafico_signal(
        self,
        symbol: str,
        df: pd.DataFrame,
        side: str,
        entry_price: float,
        sl_price: float,
        tp_price: float,
        entry_idx: int = None,
    ) -> BytesIO:
        """
        Genera grafico Telegram con matplotlib puro (sin compresion):
          - 1 panel, sin volumen
          - Velas Heikin Ashi
          - Bollinger Bands: upper (rojo), lower (verde/teal)
          - BB Basis dinamica: verde cuando close >= basis, rojo cuando close < basis
          - Lineas Entry (dorado), SL (rojo), TP (verde)
          - Marcador ENTRY: flecha + linea vertical punteada
        Retorna BytesIO con PNG o None si hay error.
        """
        try:
            if df is None or len(df) < 30:
                log.warning(f"[CHART] Datos insuficientes para graficar {symbol}")
                return None

            # --- Indicadores en dataset completo ---
            bb_upper_full, bb_basis_full, bb_lower_full = self.calculate_bb(df["close"])
            df_ha_full = self.heikin_ashi(df)

            # --- Ventana alrededor de la entrada (40 antes, 15 despues) ---
            center = entry_idx if entry_idx is not None else len(df) // 2
            before, after = 40, 15
            s = max(0, center - before)
            e = min(len(df), center + after)

            # Recortar arrays
            ha_o = df_ha_full["ha_open"].values[s:e]
            ha_h = df_ha_full["ha_high"].values[s:e]
            ha_l = df_ha_full["ha_low"].values[s:e]
            ha_c = df_ha_full["ha_close"].values[s:e]
            bb_u = bb_upper_full.values[s:e]
            bb_b = bb_basis_full.values[s:e]
            bb_l = bb_lower_full.values[s:e]
            c_w = df["close"].values[s:e]
            n_w = e - s
            x = np.arange(n_w)

            # Indice local de entrada
            local_entry = (entry_idx - s) if entry_idx is not None else None

            # --- Figure (1 panel, estilo nightclouds) ---
            fig, ax = plt.subplots(figsize=(14, 7), facecolor='#1a1a1a')
            ax.set_facecolor('#1a1a1a')
            ax.tick_params(colors='white', labelsize=8)
            ax.grid(True, color='#333333', linewidth=0.3, alpha=0.5)
            for spine in ax.spines.values():
                spine.set_color('#333333')

            # --- Velas Heikin Ashi ---
            for i in range(n_w):
                color = '#26A69A' if ha_c[i] >= ha_o[i] else '#FF4444'
                ax.plot([x[i], x[i]], [ha_l[i], ha_h[i]], color=color, linewidth=0.8)
                body_bottom = min(ha_o[i], ha_c[i])
                body_height = abs(ha_c[i] - ha_o[i])
                if body_height < (ha_h[i] - ha_l[i]) * 0.001:
                    body_height = (ha_h[i] - ha_l[i]) * 0.003
                rect = plt.Rectangle((x[i] - 0.35, body_bottom), 0.7, body_height,
                                      facecolor=color, edgecolor=color, linewidth=0.5)
                ax.add_patch(rect)

            # --- Bollinger Bands ---
            valid_u = ~np.isnan(bb_u)
            valid_l = ~np.isnan(bb_l)
            ax.plot(x[valid_u], bb_u[valid_u], color='#FF0000', linewidth=1.0, label='BB Upper')
            ax.plot(x[valid_l], bb_l[valid_l], color='#26A69A', linewidth=1.0, label='BB Lower')

            # BB Basis dinamica
            for i in range(1, n_w):
                if np.isnan(bb_b[i]) or np.isnan(bb_b[i-1]):
                    continue
                color = '#26A69A' if c_w[i] >= bb_b[i] else '#FF4444'
                ax.plot([x[i-1], x[i]], [bb_b[i-1], bb_b[i]], color=color, linewidth=1.2)

            # --- Entry / SL / TP ---
            ax.axhline(y=entry_price, color='#FFD700', linestyle='--', linewidth=1.5, alpha=0.8,
                       label=f'Entry {entry_price:.4f}')
            ax.axhline(y=sl_price, color='#FF0000', linestyle='--', linewidth=1.5, alpha=0.8,
                       label=f'SL {sl_price:.4f}')
            ax.axhline(y=tp_price, color='#00FF00', linestyle='--', linewidth=1.5, alpha=0.8,
                       label=f'TP {tp_price:.4f}')

            # --- Marcador ENTRY ---
            if local_entry is not None and 0 <= local_entry < n_w:
                # Flecha justo debajo (LONG) o encima (SHORT) de la linea Entry
                rng = ha_h[local_entry] - ha_l[local_entry] if ha_h[local_entry] != ha_l[local_entry] else entry_price * 0.002
                offset = rng * 0.5
                marker_y = entry_price - offset if side == "long" else entry_price + offset
                marker_color = '#00FF00' if side == 'long' else '#FF4444'
                marker_symbol = '^' if side == 'long' else 'v'
                ax.plot(x[local_entry], marker_y, marker=marker_symbol, color=marker_color,
                        markersize=14, zorder=5)
                ax.axvline(x=x[local_entry], color=marker_color, linestyle=':', linewidth=1.2, alpha=0.7)
                ax.annotate('ENTRY', xy=(x[local_entry], entry_price),
                            xytext=(x[local_entry] + 2, entry_price),
                            fontsize=10, color=marker_color, fontweight='bold',
                            arrowprops=dict(arrowstyle='->', color=marker_color, lw=1.5))

            # --- Titulo y formato ---
            side_label = "LONG" if side == "long" else "SHORT"
            titulo = f"{symbol} | {side_label} | Entry: {entry_price:.6f} | SL: {sl_price:.6f} | TP: {tp_price:.6f}"
            ax.set_title(titulo, color='white', fontsize=12, fontweight='bold', pad=10)
            ax.set_ylabel('Precio (USDT)', color='white', fontsize=9)
            ax.legend(loc='upper left', fontsize=8, facecolor='#1a1a1a', edgecolor='#444',
                      labelcolor='white')
            ax.set_xlim(-1, n_w)

            # X-axis labels
            step = max(1, n_w // 8)
            ticks = list(range(0, n_w, step))
            labels = [f'+{i}c' for i in ticks]
            ax.set_xticks(ticks)
            ax.set_xticklabels(labels, color='white', fontsize=8)

            plt.tight_layout()

            # --- Guardar en buffer ---
            buf = BytesIO()
            plt.savefig(buf, format="png", dpi=100, bbox_inches="tight", facecolor="#1a1a1a")
            buf.seek(0)
            plt.close(fig)
            log.info(f"[CHART] Grafico generado para {symbol}")
            return buf

        except Exception as e:
            log.error(f"[CHART] Error generando grafico para {symbol}: {e}")
            return None

    # ==========================================================
    # UPDATE STOP LOSS EN BITGET
    # ==========================================================
    def _update_stop_loss(self, symbol: str, side: str, new_sl: float) -> bool:
        try:
            new_sl_fmt = self.exchange.price_to_precision(symbol, new_sl)
            clean_symbol = symbol.split(":")[0].replace("/", "")
            params = {
                "symbol": clean_symbol,
                "marginCoin": "USDT",
                "productType": "USDT-FUTURES",
                "planType": "pos_loss",
                "stopLossTriggerPrice": str(new_sl_fmt),
                "stopLossTriggerType": "fill_price",
                "holdSide": "long" if side == "long" else "short",
            }
            self.exchange.private_mix_post_v2_mix_order_place_pos_tpsl(params)
            return True
        except RateLimitExceeded as e:
            log.warning(f"[429] _update_stop_loss {symbol}: Rate limit. {e}")
            time.sleep(5)
            return False
        except BadRequest as e:
            log.error(f"[400] _update_stop_loss {symbol}: Parametros invalidos. {e}")
            return False
        except NetworkError as e:
            log.warning(f"[NET] _update_stop_loss {symbol}: Error de red. {e}")
            return False
        except ExchangeError as e:
            log.error(f"[500] _update_stop_loss {symbol}: {e}")
            return False
        except Exception as e:
            log.error(f"Error actualizando SL {symbol}: {e}")
            return False

    # ==========================================================
    # INDICADORES: HEIKIN ASHI
    # ==========================================================
    def heikin_ashi(self, df: pd.DataFrame) -> pd.DataFrame:
        """Convierte OHLCV regular a Heikin Ashi."""
        df = df.copy()
        ha_close = (df["open"] + df["high"] + df["low"] + df["close"]) / 4
        ha_open = pd.Series(0.0, index=df.index)
        ha_open.iloc[0] = (df.iloc[0]["open"] + df.iloc[0]["close"]) / 2
        for i in range(1, len(df)):
            ha_open.iloc[i] = (ha_open.iloc[i - 1] + ha_close.iloc[i - 1]) / 2
        df["ha_close"] = ha_close
        df["ha_open"] = ha_open
        df["ha_high"] = pd.concat([df["high"], ha_open, ha_close], axis=1).max(axis=1)
        df["ha_low"] = pd.concat([df["low"], ha_open, ha_close], axis=1).min(axis=1)
        return df

    # ==========================================================
    # INDICADORES: BOLLINGER BANDS
    # ==========================================================
    def calculate_bb(self, close: pd.Series):
        """
        Calcula Bollinger Bands.
        Retorna: (upper, basis, lower)
        """
        length = self.cfg["bb_length"]
        mult = self.cfg["bb_mult"]
        basis = close.rolling(length).mean()
        dev = mult * close.rolling(length).std()
        upper = basis + dev
        lower = basis - dev
        return upper, basis, lower

    # ==========================================================
    # INDICADORES: MACD OVERLAY (booleano verde/rojo)
    # ==========================================================
    @staticmethod
    def _ema_tv(close: pd.Series, period: int) -> pd.Series:
        """
        EMA con inicializacion SMA igual que ta.ema de TradingView.
        """
        result = pd.Series(np.nan, index=close.index, dtype=float)
        if len(close) < period:
            return result
        # Primera EMA = SMA de los primeros `period` valores
        result.iloc[period - 1] = close.iloc[:period].mean()
        alpha = 2.0 / (period + 1)
        for i in range(period, len(close)):
            result.iloc[i] = close.iloc[i] * alpha + result.iloc[i - 1] * (1.0 - alpha)
        return result

    def calculate_macd_overlay(self, close: pd.Series) -> pd.Series:
        """
        Retorna Serie booleana: True = MACD >= Signal (verde), False = rojo.
        EMA inicializada con SMA como en TradingView.
        """
        fast = self._ema_tv(close, self.cfg["macd_fast"])
        slow = self._ema_tv(close, self.cfg["macd_slow"])
        macd = fast - slow
        signal = macd.rolling(self.cfg["macd_signal"]).mean()
        return macd >= signal

    # ==========================================================
    # ESTRATEGIA: DETECCION DE SENAL
    # ==========================================================
    def detect_signal(self, df: pd.DataFrame):
        """
        Detecta senal LONG o SHORT en un DataFrame OHLCV.
        Retorna: (side, sl_price, tp_price, entry_idx) o None
        """
        min_candles = self.cfg["bb_length"] + self.cfg["macd_slow"] + self.cfg["macd_signal"] + self.cfg["confirmation_window"] + 5
        if len(df) < min_candles:
            return None

        df = df.copy()

        # Indicadores sobre close regular
        df["bb_upper"], df["bb_basis"], df["bb_lower"] = self.calculate_bb(df["close"])
        df["macd_green"] = self.calculate_macd_overlay(df["close"])

        # Heikin Ashi
        df = self.heikin_ashi(df)

        # Eliminar NaN del warmup
        warmup = max(self.cfg["bb_length"], self.cfg["macd_slow"] + self.cfg["macd_signal"]) + 2
        df = df.iloc[warmup:].reset_index(drop=True)

        if len(df) < self.cfg["confirmation_window"] + 2:
            return None

        # --- Intentar LONG ---
        long = self._check_signal(df, "long")
        if long:
            return long

        # --- Intentar SHORT ---
        short = self._check_signal(df, "short")
        if short:
            return short

        return None

    def _check_signal(self, df: pd.DataFrame, side: str):
        """
        Escanea V0 (trigger) y luego V1-V8 (confirmacion).
        SOLO busca V0 en las ultimas N velas para garantizar senal fresca.
        Retorna (side, sl_price, tp_price) o None.
        """
        window = self.cfg["confirmation_window"]
        n = len(df)
        max_v0 = n - 2  # Necesitamos al menos V0 + confirmacion + 1 vela

        # === RESTRICCION DE FREScura: V0 solo en las ultimas ~10 velas ===
        # Esto garantiza que la confirmacion y entrada sean recientes
        freshness = window + 2  # ~10 velas = 50 min en TF 5m
        min_v0 = max(n - freshness, self.cfg["bb_length"])

        # === BB Basis para verificar color de la signal line ===
        basis = df["close"].rolling(self.cfg["bb_length"]).mean()

        # Escanear de mas reciente a mas antiguo (buscar la senal mas fresca)
        for v0_idx in range(max_v0, min_v0, -1):
            v0 = df.iloc[v0_idx]

            if side == "long":
                # V0: vela HA toca banda inferior (cuerpo o mecha)
                if pd.isna(v0["bb_lower"]) or v0["ha_low"] > v0["bb_lower"]:
                    continue

                # V1-V8: buscar vela verde + MACD verde + signal verde (close >= basis)
                for offset in range(1, window + 1):
                    v_idx = v0_idx + offset
                    if v_idx >= n:
                        break
                    v = df.iloc[v_idx]
                    close_at_v = df["close"].iloc[v_idx]
                    basis_at_v = basis.iloc[v_idx]
                    signal_is_green = not pd.isna(basis_at_v) and close_at_v >= basis_at_v
                    if v["ha_close"] > v["ha_open"] and v["macd_green"] and signal_is_green:
                        # Confirmacion encontrada
                        confirm = df.iloc[v_idx]
                        entry_idx = v_idx + 1
                        # ENTRY solo en la siguiente vela a la confirmacion (no mas tarde)
                        if entry_idx < n - 1:
                            continue  # ya paso la vela de entrada, senal obsoleta
                        if entry_idx >= n:
                            entry_price = df.iloc[n - 1]["close"]
                        else:
                            entry_price = df.iloc[entry_idx]["open"]
                        if entry_price <= 0:
                            continue
                        # Validar signal line VERDE en el punto de entrada (usando close)
                        idx_entry = entry_idx if entry_idx < n else n - 1
                        basis_at_entry = basis.iloc[idx_entry]
                        close_at_entry = df["close"].iloc[idx_entry]
                        if pd.isna(basis_at_entry) or close_at_entry < basis_at_entry:
                            continue  # Signal no es verde en entry, saltar
                        # SL: low de la vela anterior a entry (regular) con buffer
                        sl_raw = confirm["low"] * (1 - self.cfg["sl_buffer_pct"])
                        # Calcular distancia SL real desde entry
                        sl_dist = (entry_price - sl_raw) / entry_price
                        # Si SL esta muy lejos o del lado equivocado, usar % fijo del entry
                        if sl_dist <= 0 or sl_dist > 0.10:
                            sl_dist = min(max(sl_dist, 0.005), 0.03)  # clamp 0.5%-3%
                        sl = entry_price * (1 - sl_dist)
                        tp = entry_price + 2 * (entry_price - sl)
                        # Validar SL: debe estar POR DEBAJO del entry con margen minimo
                        min_gap = entry_price * 0.0001  # 0.01% min gap
                        if sl >= entry_price - min_gap:
                            continue
                        # Validar distancia SL minima (0.1%) y maxima (10%)
                        sl_dist_final = (entry_price - sl) / entry_price
                        if sl_dist_final < 0.001 or sl_dist_final > 0.10:
                            continue
                        return ("long", sl, tp, entry_idx)

            else:  # short
                # V0: vela HA toca banda superior (cuerpo o mecha)
                if pd.isna(v0["bb_upper"]) or v0["ha_high"] < v0["bb_upper"]:
                    continue

                # V1-V8: buscar vela roja + MACD rojo + signal roja (close < basis)
                for offset in range(1, window + 1):
                    v_idx = v0_idx + offset
                    if v_idx >= n:
                        break
                    v = df.iloc[v_idx]
                    close_at_v = df["close"].iloc[v_idx]
                    basis_at_v = basis.iloc[v_idx]
                    signal_is_red = not pd.isna(basis_at_v) and close_at_v < basis_at_v
                    if v["ha_close"] < v["ha_open"] and not v["macd_green"] and signal_is_red:
                        # Confirmacion encontrada
                        confirm = df.iloc[v_idx]
                        entry_idx = v_idx + 1
                        # ENTRY solo en la siguiente vela a la confirmacion (no mas tarde)
                        if entry_idx < n - 1:
                            continue  # ya paso la vela de entrada, senal obsoleta
                        if entry_idx >= n:
                            entry_price = df.iloc[n - 1]["close"]
                        else:
                            entry_price = df.iloc[entry_idx]["open"]
                        if entry_price <= 0:
                            continue
                        # Validar signal line ROJA en el punto de entrada (usando close)
                        idx_entry = entry_idx if entry_idx < n else n - 1
                        basis_at_entry = basis.iloc[idx_entry]
                        close_at_entry = df["close"].iloc[idx_entry]
                        if pd.isna(basis_at_entry) or close_at_entry >= basis_at_entry:
                            continue  # Signal no es roja en entry, saltar
                        # SL: high de la vela anterior a entry (regular) con buffer
                        sl_raw = confirm["high"] * (1 + self.cfg["sl_buffer_pct"])
                        # Calcular distancia SL real desde entry
                        sl_dist = (sl_raw - entry_price) / entry_price
                        # Si SL esta muy lejos o del lado equivocado, usar % fijo del entry
                        if sl_dist <= 0 or sl_dist > 0.10:
                            sl_dist = min(max(sl_dist, 0.005), 0.03)  # clamp 0.5%-3%
                        sl = entry_price * (1 + sl_dist)
                        tp = entry_price - 2 * (sl - entry_price)
                        # Validar SL: debe estar POR ENCIMA del entry con margen minimo
                        min_gap = entry_price * 0.0001  # 0.01% min gap
                        if sl <= entry_price + min_gap:
                            continue
                        # Validar distancia SL minima (0.1%) y maxima (10%)
                        sl_dist_final = (sl - entry_price) / entry_price
                        if sl_dist_final < 0.001 or sl_dist_final > 0.10:
                            continue
                        return ("short", sl, tp, entry_idx)

        return None

    # ==========================================================
    # ESTRATEGIA: SCAN DE SENSLES
    # ==========================================================
    def scan_signals(self, symbols: list) -> list:
        """
        Descarga OHLCV para multiples simbolos y busca senales.
        Retorna lista de dicts: [{symbol, side, sl_price, tp_price, df}, ...]
        """
        signals = []
        if not symbols:
            return signals

        try:
            ohlcv_data = self.fetch_ohlcv_sync(symbols, self.cfg["timeframe"], self.cfg["ohlcv_limit"])
        except RateLimitExceeded as e:
            log.warning(f"[429] scan_signals: Rate limit descargando velas. {e}")
            return signals
        except NetworkError as e:
            log.warning(f"[NET] scan_signals: Error de red descargando velas. {e}")
            return signals
        except ExchangeError as e:
            log.error(f"[500] scan_signals: Error del exchange descargando velas. {e}")
            return signals
        except Exception as e:
            log.error(f"Error descargando OHLCV: {e}")
            return signals

        for symbol in symbols:
            # Saltar simbolos con operativa abierta
            if symbol in self.session_active:
                continue

            data = ohlcv_data.get(symbol)
            if not data or len(data) < 20:
                continue

            try:
                df = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close", "volume"])
                result = self.detect_signal(df)
                if result:
                    side, sl, tp, entry_idx = result
                    signals.append({
                        "symbol": symbol,
                        "side": side,
                        "sl_price": sl,
                        "tp_price": tp,
                        "entry_idx": entry_idx,
                        "df": df,
                    })
                    log.info(f"Senal detectada: {symbol} {side.upper()} | SL={sl:.6f} TP={tp:.6f}")
            except RateLimitExceeded as e:
                log.warning(f"[429] scan_signals {symbol}: Rate limit. Saltando.")
                continue
            except NetworkError as e:
                log.warning(f"[NET] scan_signals {symbol}: Error de red. Saltando.")
                continue
            except Exception as e:
                log.error(f"Error detectando senal en {symbol}: {e}")
                continue

        return signals

    # ==========================================================
    # OPEN POSITION (margen minimo Bitget)
    # ==========================================================
    def open_position(
        self,
        symbol: str,
        side: str,
        sl_price: float,
        tp_price: float,
        balance: float = None,
        df: pd.DataFrame = None,
        entry_idx: int = None,
    ) -> bool:
        # Doble guard: no abrir si ya hay posicion activa en este symbol
        if symbol in self.session_active:
            log.debug(f"{symbol} ya tiene posicion activa. Saltando.")
            return False

        if balance is None:
            balance = self.get_balance()
        if balance <= 0:
            log.warning(f"Balance insuficiente para {symbol}")
            return False

        try:
            # --- Margen minimo por simbolo ---
            market = self.exchange.market(symbol)
            min_qty = market["limits"]["amount"]["min"] or (10 ** -market["precision"]["amount"])
            price = self.exchange.fetch_ticker(symbol)["last"]

            # Bitget requiere minimo 5 USDT en valor nominal para futures
            MIN_NOTIONAL = 5.0
            min_qty_from_notional = MIN_NOTIONAL / price
            # Usar el maximo entre min_qty del mercado y min_qty por notional
            effective_min_qty = max(min_qty, min_qty_from_notional)

            # Redondear hacia ARRIBA a precision del mercado (ceil) para no quedar por debajo de 5 USDT
            prec = market["precision"]["amount"]
            factor = 10 ** prec
            effective_min_qty = math.ceil(effective_min_qty * factor) / factor
            effective_min_qty = float(self.exchange.amount_to_precision(symbol, effective_min_qty))

            min_margin = (effective_min_qty * price) / self.cfg["leverage"]

            if min_margin > balance:
                log.warning(f"Margen minimo {min_margin:.2f} > balance {balance:.2f} para {symbol}")
                return False

            qty = effective_min_qty
            actual_margin = min_margin

            # --- Precio de entrada de la estrategia (open de la vela de confirmacion) ---
            strategy_entry = float(df.iloc[entry_idx]["open"]) if df is not None and entry_idx is not None and entry_idx < len(df) else price

            # --- Validacion SL (respecto al precio de la estrategia) ---
            if side == "long":
                sl_dist = (strategy_entry - sl_price) / strategy_entry
            else:
                sl_dist = (sl_price - strategy_entry) / strategy_entry
            if sl_dist <= 0 or sl_dist > 0.10:
                log.warning(f"{symbol} SL invalido ({sl_dist*100:.1f}%). Saltando.")
                return False

            # --- Crear orden con SL/TP precargado ---
            # Bitget requiere "buy"/"sell", no "long"/"short"
            ccxt_side = "buy" if side == "long" else "sell"
            params = {
                "marginCoin": "USDT",
                "marginMode": "isolated",
                "tradeSide": "open",
                "presetStopSurplusPrice": str(self.exchange.price_to_precision(symbol, tp_price)),
                "presetStopLossPrice": str(self.exchange.price_to_precision(symbol, sl_price)),
            }
            self.exchange.create_order(symbol, "market", ccxt_side, qty, params=params)

            fmt_price = self.exchange.price_to_precision(symbol, price)
            fmt_sl = self.exchange.price_to_precision(symbol, sl_price)
            fmt_tp = self.exchange.price_to_precision(symbol, tp_price)

            msg = (
                f"*{symbol} {side.upper()}*\n"
                f"Entrada: `{fmt_price}`\n"
                f"SL: `{fmt_sl}`\n"
                f"TP: `{fmt_tp}` (1:{int(self.cfg['rr_ratio'])})\n"
                f"Qty: `{qty}` | Margin: `{actual_margin:.2f}` USDT"
            )
            self.send_telegram(msg)
            log.info(f"{symbol} {side.upper()} | Entry={fmt_price} SL={fmt_sl} TP={fmt_tp} | Qty={qty} | Margin={actual_margin:.2f}")

            # --- Guardar entrada ---
            self.trade_entries[symbol] = {
                "entry_time": datetime.now().isoformat(),
                "symbol": symbol,
                "side": side,
                "entry_price": price,
                "sl_price": sl_price,
                "tp_price": tp_price,
                "quantity": qty,
                "balance_before": balance,
                "size_usdt": round(actual_margin, 2),
                "risk_pct": round(actual_margin / balance * 100, 2),
            }
            self._save_trade_entries()
            self.session_active.add(symbol)

            # --- Generar y enviar grafico a Telegram ---
            try:
                if df is not None:
                    # Usar el precio de entrada de la estrategia (open de la vela), no el live ticker
                    strategy_entry = float(df.iloc[entry_idx]["open"]) if entry_idx is not None and entry_idx < len(df) else price
                    buf = self.generar_grafico_signal(symbol, df, side, strategy_entry, sl_price, tp_price, entry_idx)
                    if buf:
                        caption = f"*{symbol} {side.upper()}*\nEntry: {fmt_price} | SL: {fmt_sl} | TP: {fmt_tp}"
                        self.send_telegram_photo(buf, caption)
            except Exception as e:
                log.warning(f"[CHART] Error generando grafico para {symbol}: {e}")

            return True

        except BadRequest as e:
            log.error(f"[400] open_position {symbol}: Parametros invalidos. {e}")
            return False
        except RateLimitExceeded as e:
            log.warning(f"[429] open_position {symbol}: Rate limit. Reintentando en 5s...")
            time.sleep(5)
            return False
        except AuthenticationError as e:
            log.critical(f"[AUTH] open_position {symbol}: Credenciales invalidas. {e}")
            return False
        except PermissionDenied as e:
            log.critical(f"[PERM] open_position {symbol}: Sin permisos. {e}")
            return False
        except NetworkError as e:
            log.warning(f"[NET] open_position {symbol}: Error de red. {e}")
            return False
        except ExchangeError as e:
            log.error(f"[500] open_position {symbol}: {e}")
            return False
        except Exception as e:
            log.error(f"Error abriendo {symbol}: {e}")
            return False

    # ==========================================================
    # MANAGE POSITIONS (BE + TRAILING + CIERRE)
    # ==========================================================
    def manage_positions(self, balance: float = None):
        if balance is None:
            balance = self.get_balance()

        try:
            positions = self.exchange.fetch_positions()
            active_symbols = [p["symbol"] for p in positions if float(p["contracts"]) > 0]

            # --- 1. Detectar posiciones cerradas ---
            for sym in list(self.session_active):
                if sym not in active_symbols:
                    self.cooldowns[sym] = time.time() + 3600
                    log.info(f"{sym} CERRADA. Cooldown 1h activado.")
                    self._process_closed_position(sym)
                    self._cleanup_symbol(sym)

            # --- 2. Gestionar posiciones abiertas ---
            for pos in positions:
                symbol = pos["symbol"]
                side = pos["side"]
                if float(pos["contracts"]) == 0:
                    continue

                entry = float(pos["entryPrice"])
                mark = float(pos["markPrice"])
                profit_pct = (mark - entry) / entry if side == "long" else (entry - mark) / entry

                # --- Adverse price tracking ---
                if symbol not in self.adverse_prices:
                    self.adverse_prices[symbol] = mark
                else:
                    if side == "long":
                        self.adverse_prices[symbol] = min(self.adverse_prices[symbol], mark)
                    else:
                        self.adverse_prices[symbol] = max(self.adverse_prices[symbol], mark)

                # --- Peak price tracking ---
                if symbol not in self.peak_prices:
                    self.peak_prices[symbol] = mark
                else:
                    if side == "long":
                        self.peak_prices[symbol] = max(self.peak_prices[symbol], mark)
                    else:
                        self.peak_prices[symbol] = min(self.peak_prices[symbol], mark)

                # --- 3. Break Even (0.4% trigger, 0.2% offset) ---
                if profit_pct >= self.cfg["be_trigger_pct"]:
                    if not self.alerts_history.get(f"{symbol}_be", False):
                        if side == "long":
                            new_sl = entry * (1 + self.cfg["be_offset_pct"])
                        else:
                            new_sl = entry * (1 - self.cfg["be_offset_pct"])
                        if self._update_stop_loss(symbol, side, new_sl):
                            self.alerts_history[f"{symbol}_be"] = True
                            self.alerts_history[f"{symbol}_be_price"] = new_sl
                            log.info(f"{symbol} BE+ activado (offset {self.cfg['be_offset_pct']*100:.1f}%)")
                            self.send_telegram(f"*{symbol}* BE+ (offset {self.cfg['be_offset_pct']*100:.1f}%)")

                # --- 4. Trailing Stop (0.7% fijo del pico, solo despues de BE) ---
                if self.alerts_history.get(f"{symbol}_be", False):
                    peak = self.peak_prices[symbol]
                    if side == "long":
                        nuevo_sl = peak * (1 - self.cfg["trailing_dist_pct"])
                        ultimo = self.alerts_history.get(f"{symbol}_trail", 0)
                        if nuevo_sl > ultimo:
                            if self._update_stop_loss(symbol, side, nuevo_sl):
                                self.alerts_history[f"{symbol}_trail"] = nuevo_sl
                                self.trail_counts[symbol] = self.trail_counts.get(symbol, 0) + 1
                                log.info(f"{symbol} Trail -> {nuevo_sl:.6f}")
                    else:
                        nuevo_sl = peak * (1 + self.cfg["trailing_dist_pct"])
                        ultimo = self.alerts_history.get(f"{symbol}_trail", 999999)
                        if nuevo_sl < ultimo:
                            if self._update_stop_loss(symbol, side, nuevo_sl):
                                self.alerts_history[f"{symbol}_trail"] = nuevo_sl
                                self.trail_counts[symbol] = self.trail_counts.get(symbol, 0) + 1
                                log.info(f"{symbol} Trail -> {nuevo_sl:.6f}")

                # --- 5. Monitoreo de SL prematuro ---
                for mon_sym in list(self.premature_sl_monitor.keys()):
                    mon = self.premature_sl_monitor[mon_sym]
                    hours_since = (datetime.now() - datetime.fromisoformat(mon["sl_time"])).total_seconds() / 3600
                    if hours_since > 24:
                        self._save_premature_sl(mon, False)
                        del self.premature_sl_monitor[mon_sym]
                        continue
                    try:
                        ticker = self.exchange.fetch_ticker(mon_sym)
                        curr = ticker["last"]
                        if (mon["side"] == "long" and curr >= mon["tp_price"]) or \
                           (mon["side"] == "short" and curr <= mon["tp_price"]):
                            self._save_premature_sl(mon, True, datetime.now())
                            log.info(f"{mon_sym}: SL prematuro alcanzo TP despues del SL")
                            del self.premature_sl_monitor[mon_sym]
                    except RateLimitExceeded:
                        time.sleep(2)
                        continue
                    except NetworkError:
                        continue
                    except ExchangeError:
                        continue
                    except Exception:
                        continue

        except RateLimitExceeded as e:
            log.warning(f"[429] manage_positions: Rate limit. {e}")
            time.sleep(5)
        except NetworkError as e:
            log.warning(f"[NET] manage_positions: Error de red. {e}")
        except ExchangeError as e:
            log.error(f"[500] manage_positions: {e}")
        except Exception as e:
            log.error(f"Error en manage_positions: {e}")

    # ==========================================================
    # CLOSE POSITION (manual)
    # ==========================================================
    def close_position(self, symbol: str) -> bool:
        try:
            self.exchange.close_position(symbol)
            log.info(f"{symbol} cerrada manualmente.")
            return True
        except RateLimitExceeded as e:
            log.warning(f"[429] close_position {symbol}: Rate limit. {e}")
            time.sleep(5)
            return False
        except BadRequest as e:
            log.error(f"[400] close_position {symbol}: Parametros invalidos. {e}")
            return False
        except NetworkError as e:
            log.warning(f"[NET] close_position {symbol}: Error de red. {e}")
            return False
        except ExchangeError as e:
            log.error(f"[500] close_position {symbol}: {e}")
            return False
        except Exception as e:
            log.error(f"Error cerrando {symbol}: {e}")
            return False

    # ==========================================================
    # PROCESAMIENTO DE CIERRE
    # ==========================================================
    def _process_closed_position(self, sym: str):
        try:
            time.sleep(2)
            trades = self.exchange.fetch_my_trades(sym, limit=20)
            if not trades:
                return

            trade_pnl, trade_fees, last_closing = 0.0, 0.0, None
            for t in reversed(trades):
                if float(t["info"].get("profit", 0)) != 0:
                    last_closing = t
                    break

            if not last_closing:
                return

            order_id = last_closing.get("order") or last_closing["info"].get("orderId")
            for t in trades:
                if (t.get("order") or t["info"].get("orderId")) == order_id:
                    trade_pnl += float(t["info"].get("profit", 0))
                    if "fee" in t and t["fee"]:
                        trade_fees += abs(float(t["fee"].get("cost", 0)))

            net = trade_pnl - trade_fees
            status = "TP" if trade_pnl > 0 else ("SL" if trade_pnl < 0 else "BE")
            reason = "tp" if trade_pnl > 0 else ("sl" if trade_pnl < 0 else "be")

            self.send_telegram(f"*{sym} CERRADA*\nPnL: {net:.2f} USDT ({status})\nFees: -{trade_fees:.2f}")

            # --- Actualizar cooldown global por perdidas consecutivas ---
            self.record_trade_result(net)

            entry = self.trade_entries.pop(sym, None)
            if entry:
                exit_px = float(last_closing.get("price", 0))
                entry_dt = datetime.fromisoformat(entry["entry_time"]) if isinstance(entry["entry_time"], str) else entry["entry_time"]
                self._save_trade_csv(entry, exit_px, trade_pnl, trade_fees, net, status, reason, entry_dt)
                if trade_pnl < 0:
                    self.premature_sl_monitor[sym] = {
                        "entry_time": entry["entry_time"],
                        "sl_time": datetime.now().isoformat(),
                        "symbol": sym,
                        "side": entry["side"],
                        "entry_price": entry["entry_price"],
                        "sl_price": entry["sl_price"],
                        "tp_price": entry["tp_price"],
                        "hit_be_before_sl": self.alerts_history.get(f"{sym}_be", False),
                        "max_favorable_before_sl": self.peak_prices.get(sym, entry["entry_price"]),
                    }
            self._save_trade_entries()

        except RateLimitExceeded as e:
            log.warning(f"[429] _process_closed_position {sym}: Rate limit. {e}")
        except NetworkError as e:
            log.warning(f"[NET] _process_closed_position {sym}: Error de red. {e}")
        except ExchangeError as e:
            log.error(f"[500] _process_closed_position {sym}: {e}")
        except Exception as e:
            log.error(f"Error procesando cierre de {sym}: {e}")

    def _cleanup_symbol(self, sym: str):
        self.peak_prices.pop(sym, None)
        self.adverse_prices.pop(sym, None)
        self.alerts_history.pop(f"{sym}_be", None)
        self.alerts_history.pop(f"{sym}_be_price", None)
        self.alerts_history.pop(f"{sym}_trail", None)
        self.trail_counts.pop(sym, None)
        self.session_active.discard(sym)

    # ==========================================================
    # COOLDOWN POR PERDIDAS CONSECUTIVAS
    # ==========================================================
    def record_trade_result(self, net_pnl: float):
        """Registra resultado del trade. Actualiza cooldown global."""
        if net_pnl >= 0:
            # Ganancia (TP o BE) -> resetear contador
            if self.consecutive_losses > 0:
                log.info(f"Trade ganador. Contador de perdidas reseteado ({self.consecutive_losses} -> 0)")
            self.consecutive_losses = 0
        else:
            # Perdida (SL) -> incrementar contador
            self.consecutive_losses += 1
            log.info(f"Perdida consecutiva #{self.consecutive_losses}")
            if self.consecutive_losses >= self.cfg["max_consecutive_losses"]:
                self.cooldown_until = time.time() + self.cfg["cooldown_hours"] * 3600
                log.warning(f"{self.cfg['max_consecutive_losses']} perdidas consecutivas. Pausa {self.cfg['cooldown_hours']}h.")
                self.send_telegram(f"*PAUSA* {self.cfg['max_consecutive_losses']} perdidas seguidas. Pausa {self.cfg['cooldown_hours']}h.")

    def is_on_cooldown(self) -> bool:
        """Verifica si el bot esta en pausa por perdidas consecutivas."""
        if self.cooldown_until is None:
            return False
        if time.time() >= self.cooldown_until:
            log.info("Cooldown por perdidas finalizado. Reanudando operaciones.")
            self.cooldown_until = None
            self.consecutive_losses = 0
            return False
        remaining = (self.cooldown_until - time.time()) / 60
        log.debug(f"En pausa. Faltan {remaining:.0f} min.")
        return True

    # ==========================================================
    # PERSISTENCIA
    # ==========================================================
    def _save_trade_entries(self):
        try:
            data = {}
            for sym, e in self.trade_entries.items():
                data[sym] = e.copy()
            with open(self.trade_entries_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as ex:
            log.error(f"Error guardando trade_entries: {ex}")

    def _load_trade_entries(self):
        try:
            if not os.path.exists(self.trade_entries_path):
                return
            with open(self.trade_entries_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.trade_entries.update(data)
            log.info(f"Cargadas {len(data)} entradas desde trade_entries.json")
        except Exception as ex:
            log.error(f"Error cargando trade_entries: {ex}")

    def _save_trade_csv(self, entry, exit_price, raw_pnl, fees, net, status, reason, entry_dt: datetime):
        now = datetime.now()
        duration = (now - entry_dt).total_seconds() / 3600
        balance_after = entry["balance_before"] + net
        ep = entry["entry_price"]
        side = entry["side"]

        row = {
            "entry_time": entry_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "exit_time": now.strftime("%Y-%m-%d %H:%M:%S"),
            "symbol": entry["symbol"],
            "side": side,
            "entry_price": ep,
            "exit_price": exit_price,
            "sl_price": entry["sl_price"],
            "tp_price": entry["tp_price"],
            "sl_pct": round(abs(ep - entry["sl_price"]) / ep * 100, 2),
            "tp_pct": round(abs(entry["tp_price"] - ep) / ep * 100, 2),
            "quantity": entry["quantity"],
            "balance_before": round(entry["balance_before"], 2),
            "balance_after": round(balance_after, 2),
            "pnl": round(raw_pnl, 2),
            "fees": round(fees, 2),
            "net_pnl": round(net, 2),
            "status": status,
            "duration_hours": round(duration, 2),
            "close_reason": reason,
            "be_triggered": 1 if self.alerts_history.get(f"{entry['symbol']}_be", False) else 0,
            "be_price": round(self.alerts_history.get(f"{entry['symbol']}_be_price", 0), 4),
            "trail_count": self.trail_counts.get(entry["symbol"], 0),
            "trail_peak_price": round(self.peak_prices.get(entry["symbol"], ep), 4),
            "trail_final_sl": round(self.alerts_history.get(f"{entry['symbol']}_trail", entry["sl_price"]), 4),
            "entry_weekday": entry_dt.weekday(),
            "entry_hour": entry_dt.hour,
            "size_usdt": entry.get("size_usdt", 0),
            "risk_pct": entry.get("risk_pct", 0),
            "max_favorable_pct": round(abs(self.peak_prices.get(entry["symbol"], ep) - ep) / ep * 100, 2),
            "max_adverse_pct": round(abs(self.adverse_prices.get(entry["symbol"], ep) - ep) / ep * 100, 2),
        }
        write_header = not os.path.exists(self.trades_csv)
        try:
            with open(self.trades_csv, "a", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=self.TRADE_CSV_HEADERS)
                if write_header:
                    w.writeheader()
                w.writerow(row)
        except Exception:
            pass

    def _save_premature_sl(self, mon, reached, reached_time=None):
        ep = mon["entry_price"]
        entry_dt = datetime.fromisoformat(mon["entry_time"]) if isinstance(mon["entry_time"], str) else mon["entry_time"]
        sl_dt = datetime.fromisoformat(mon["sl_time"]) if isinstance(mon["sl_time"], str) else mon["sl_time"]
        row = {
            "entry_time": entry_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "sl_time": sl_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "symbol": mon["symbol"],
            "side": mon["side"],
            "entry_price": ep,
            "sl_price": mon["sl_price"],
            "tp_price": mon["tp_price"],
            "sl_pct": round(abs(ep - mon["sl_price"]) / ep * 100, 2),
            "tp_reached": "Yes" if reached else "No",
            "tp_reached_time": reached_time.strftime("%Y-%m-%d %H:%M:%S") if reached_time else "",
            "hours_to_tp_after_sl": round((reached_time - sl_dt).total_seconds() / 3600, 2) if reached_time else "",
            "entry_weekday": entry_dt.weekday(),
            "entry_hour": entry_dt.hour,
            "hit_be_before_sl": "Yes" if mon.get("hit_be_before_sl") else "No",
            "max_favorable_before_sl": round(mon.get("max_favorable_before_sl", 0), 4),
        }
        write_header = not os.path.exists(self.premature_sl_csv)
        try:
            with open(self.premature_sl_csv, "a", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=self.PREMATURE_CSV_HEADERS)
                if write_header:
                    w.writeheader()
                w.writerow(row)
        except Exception:
            pass

    # ==========================================================
    # UTILITIES
    # ==========================================================
    def get_open_symbols(self) -> set:
        try:
            positions = self.exchange.fetch_positions()
            return {p["symbol"] for p in positions if float(p["contracts"]) > 0}
        except RateLimitExceeded:
            time.sleep(3)
            return set()
        except NetworkError:
            return set()
        except ExchangeError:
            return set()
        except Exception:
            return set()

    def is_cooling_down(self, symbol: str) -> bool:
        if symbol in self.cooldowns:
            if time.time() < self.cooldowns[symbol]:
                return True
            del self.cooldowns[symbol]
        return False

    def get_position_count(self) -> int:
        return len(self.get_open_symbols())

    def can_open(self) -> bool:
        return self.get_position_count() < self.cfg["max_open_positions"]

    # ==========================================================
    # RUN (loop principal)
    # ==========================================================
    def run(self):
        """
        Loop principal:
        - Cada 15s: gestiona posiciones (BE, trailing, cierres)
        - Cada 5 min: escanea TOP 100 y busca senales
        """
        if not self.connect():
            return

        # --- Sincronizar session_active con posiciones abiertas en Bitget ---
        try:
            open_pos = self.get_open_symbols()
            self.session_active = open_pos
            if open_pos:
                log.info(f"Posiciones abiertas detectadas al arrancar: {open_pos}")
            else:
                log.info("Sin posiciones abiertas al arrancar.")
        except Exception as e:
            log.warning(f"No se pudieron sincronizar posiciones abiertas: {e}")

        self.last_scan_time = 0
        log.info(f"BotBB arrancado | TF={self.cfg['timeframe']} | TOP={self.cfg['top_symbols_count']} | MaxPos={self.cfg['max_open_positions']}")

        while True:
            try:
                balance = self.get_balance()

                # --- Gestionar posiciones cada ciclo ---
                self.manage_positions(balance)

                # --- Escanear senales cada 5 min ---
                elapsed = time.time() - self.last_scan_time
                if elapsed >= self.cfg["scan_interval_sec"]:
                    if not self.is_on_cooldown() and self.can_open():
                        log.info("Escaneando TOP %d simbolos...", self.cfg["top_symbols_count"])
                        top = self.get_top_symbols(self.cfg["top_symbols_count"])
                        if top:
                            signals = self.scan_signals(top)
                            for sig in signals:
                                if self.can_open() and not self.is_on_cooldown():
                                    self.open_position(
                                        symbol=sig["symbol"],
                                        side=sig["side"],
                                        sl_price=sig["sl_price"],
                                        tp_price=sig["tp_price"],
                                        balance=balance,
                                        df=sig.get("df"),
                                        entry_idx=sig.get("entry_idx"),
                                    )
                            if not signals:
                                log.info("Sin senales en este escaneo.")
                    elif self.is_on_cooldown():
                        log.debug("En cooldown. Saltando escaneo.")
                    self.last_scan_time = time.time()

                time.sleep(15)

            except KeyboardInterrupt:
                log.info("Bot detenido por el usuario.")
                self.shutdown()
                return
            except RateLimitExceeded as e:
                log.warning(f"[429] Ciclo principal: Rate limit. Esperando 30s...")
                time.sleep(30)
            except NetworkError as e:
                log.warning(f"[NET] Ciclo principal: Error de red. Reconectando en 15s...")
                time.sleep(15)
                try:
                    self.connect()
                except Exception:
                    pass
            except AuthenticationError as e:
                log.critical(f"[AUTH] Ciclo principal: Credenciales invalidas. Deteniendo. {e}")
                self.shutdown()
                return
            except PermissionDenied as e:
                log.critical(f"[PERM] Ciclo principal: Sin permisos. Deteniendo. {e}")
                self.shutdown()
                return
            except ExchangeError as e:
                log.error(f"[500] Ciclo principal: Error del exchange. Continuando...")
                time.sleep(15)
            except Exception as e:
                log.error(f"Error en ciclo principal: {e}")
                time.sleep(15)


# ==========================================================
# MAIN
# ==========================================================
if __name__ == "__main__":
    engine = BotBBEngine()
    engine.run()
