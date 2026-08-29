"""
BotBB Engine — Motor async + Estrategia para Bitget.
Bollinger Bands + MACD Overlay + Heikin Ashi.
LONG y SHORT. Timeframe 5min.

Optimizado: async completo, semaforos, memoria reducida.
"""

import os
import sys
import csv
import json
import math
import decimal
import time
import asyncio
import logging
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
from datetime import datetime
from io import BytesIO
from typing import Optional

import aiohttp

import matplotlib
matplotlib.use('Agg')
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
    "bb_length":            20,
    "bb_mult":              2.0,
    "macd_fast":            12,
    "macd_slow":            26,
    "macd_signal":          9,
    "confirmation_window":  8,
    # --- Entrada ---
    "sl_buffer_pct":        0.0005,
    "rr_ratio":             2.0,
    # --- Gestion ---
    "risk_pct":             0.07,
    "be_trigger_pct":       0.004,
    "be_offset_pct":        0.002,
    "trailing_dist_pct":    0.007,
    "leverage":             10.0,
    "max_open_positions":   5,
    # --- Cooldown ---
    "max_consecutive_losses": 4,
    "cooldown_hours":       4,
    # --- Escaneo ---
    "scan_interval_sec":    300,
    "top_symbols_count":    100,
    "ohlcv_limit":          100,
    "timeframe":            "5m",
    # --- Concurrencia ---
    "max_concurrent_fetches": 10,
}


# ==========================================================
# BOTBBENGINE — Async optimizado
# ==========================================================
class BotBBEngine:
    """Motor de ejecucion + Estrategia async para Bitget."""

    __slots__ = (
        "cfg", "exchange", "semaphore", "_aio_session",
        "alerts_history", "peak_prices", "cooldowns", "session_active",
        "trade_entries", "trail_counts", "premature_sl_monitor", "adverse_prices",
        "consecutive_losses", "cooldown_until", "last_scan_time",
        "api_key", "secret_key", "passphrase",
        "telegram_token", "telegram_chat_id",
        "trades_csv", "trade_entries_path", "premature_sl_csv", "price_paths_dir",
        "TRADE_CSV_HEADERS", "PREMATURE_CSV_HEADERS",
    )

    def __init__(self, config: dict = None):
        self.cfg = {**DEFAULT_CONFIG, **(config or {})}
        self.exchange = None
        self.semaphore = asyncio.Semaphore(self.cfg["max_concurrent_fetches"])
        self._aio_session: Optional[aiohttp.ClientSession] = None

        # Memoria de sesion
        self.alerts_history: dict = {}
        self.peak_prices: dict = {}
        self.cooldowns: dict = {}
        self.session_active: set = set()
        self.trade_entries: dict = {}
        self.trail_counts: dict = {}
        self.premature_sl_monitor: dict = {}
        self.adverse_prices: dict = {}

        # Cooldown global
        self.consecutive_losses: int = 0
        self.cooldown_until: Optional[float] = None
        self.last_scan_time: float = 0.0

        # Credenciales
        self.api_key = os.environ.get("BITGET_API_KEY", "")
        self.secret_key = os.environ.get("BITGET_SECRET_KEY", "")
        self.passphrase = os.environ.get("BITGET_PASSPHRASE", "")
        self.telegram_token = os.environ.get("TELEGRAM_TOKEN", "")
        self.telegram_chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

        # Archivos
        base = os.path.dirname(os.path.abspath(__file__))
        self.trades_csv = os.path.join(base, "trades.csv")
        self.trade_entries_path = os.path.join(base, "trade_entries.json")
        self.premature_sl_csv = os.path.join(base, "premature_sl.csv")
        self.price_paths_dir = os.path.join(base, "price_paths")
        os.makedirs(self.price_paths_dir, exist_ok=True)

        # CSV Headers
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
    # LIFECYCLE
    # ==========================================================
    async def start(self):
        """Inicializa el aiohttp session y conecta al exchange."""
        self._aio_session = aiohttp.ClientSession()
        return await self._connect()

    async def stop(self):
        """Cierra todo de forma ordenada."""
        if self.exchange:
            try:
                await self.exchange.close()
            except Exception:
                pass
            self.exchange = None
        if self._aio_session:
            await self._aio_session.close()
            self._aio_session = None
        log.info("BotBB detenido y conexiones cerradas.")

    # ==========================================================
    # CONEXION (async via to_thread)
    # ==========================================================
    async def _connect(self) -> bool:
        def _sync():
            exch = ccxt.bitget({
                "apiKey": self.api_key,
                "secret": self.secret_key,
                "password": self.passphrase,
                "enableRateLimit": True,
                "options": {"defaultType": "swap"},
            })
            exch.load_markets()
            return exch

        try:
            self.exchange = await asyncio.to_thread(_sync)
            log.info("Conexion exitosa a Bitget.")
            await self._load_trade_entries()
            return True
        except AuthenticationError as e:
            log.critical(f"[AUTH] Credenciales invalidas: {e}")
            return False
        except PermissionDenied as e:
            log.critical(f"[PERM] Sin permisos: {e}")
            return False
        except RateLimitExceeded:
            log.warning("[429] Rate limit al conectar. Reintentando en 5s...")
            await asyncio.sleep(5)
            return await self._connect()
        except NetworkError as e:
            log.warning(f"[NET] Error de red: {e}")
            return False
        except Exception as e:
            log.critical(f"Error de conexion: {e}")
            return False

    # ==========================================================
    # WRAPPERS — ccxt sync → async (sin bloquear event loop)
    # ==========================================================
    async def _exch_call(self, method: str, *args, **kwargs):
        """Ejecuta un metodo ccxt sync en thread pool con semaforo."""
        async with self.semaphore:
            fn = getattr(self.exchange, method)
            return await asyncio.to_thread(fn, *args, **kwargs)

    async def _exch_call_await(self, method: str, *args, **kwargs):
        """Ejecuta un metodo ccxt async nativo con semaforo."""
        async with self.semaphore:
            fn = getattr(self.exchange, method)
            return await fn(*args, **kwargs)

    # ==========================================================
    # BALANCE (async)
    # ==========================================================
    async def get_balance(self) -> float:
        try:
            data = await self._exch_call("fetch_balance")
            return float(data["total"].get("USDT", 0))
        except RateLimitExceeded:
            log.warning("[429] get_balance: Rate limit.")
            await asyncio.sleep(5)
            return 0.0
        except NetworkError:
            log.warning("[NET] get_balance: Error de red.")
            return 0.0
        except ExchangeError as e:
            log.error(f"[500] get_balance: {e}")
            return 0.0
        except Exception as e:
            log.error(f"Error obteniendo balance: {e}")
            return 0.0

    # ==========================================================
    # TOP SYMBOLS POR VOLUMEN (async)
    # ==========================================================
    async def get_top_symbols(self, n: int = 100) -> list:
        try:
            tickers = await self._exch_call("fetch_tickers")
            ranked = [
                (s, float(t.get("quoteVolume", 0)))
                for s, t in tickers.items()
                if s.endswith("/USDT:USDT")
            ]
            ranked.sort(key=lambda x: x[1], reverse=True)
            return [s for s, _ in ranked[:n]]
        except RateLimitExceeded:
            log.warning("[429] get_top_symbols: Rate limit.")
            await asyncio.sleep(5)
            return []
        except NetworkError:
            log.warning("[NET] get_top_symbols: Error de red.")
            return []
        except ExchangeError as e:
            log.error(f"[500] get_top_symbols: {e}")
            return []
        except Exception as e:
            log.error(f"Error fetching top symbols: {e}")
            return []

    # ==========================================================
    # FETCH OHLCV — async nativo + semaforo
    # ==========================================================
    async def _fetch_single(self, exch, symbol: str, timeframe: str, limit: int):
        """Descarga OHLCV de un simbolo con semaforo de concurrencia."""
        async with self.semaphore:
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
        """Descarga batch de OHLCV con concurrencia controlada."""
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
            return {r[0]: r[1] for r in results if r[1] is not None}
        finally:
            await exch.close()

    # ==========================================================
    # TELEGRAM — async con aiohttp
    # ==========================================================
    async def send_telegram(self, message: str):
        if not self.telegram_token or not self.telegram_chat_id:
            log.warning("[TG] Faltan TELEGRAM_TOKEN o TELEGRAM_CHAT_ID.")
            return
        if not self._aio_session:
            log.warning("[TG] aiohttp session no inicializada para send_telegram.")
            return
        url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
        try:
            async with self._aio_session.post(
                url,
                data={"chat_id": self.telegram_chat_id, "text": message, "parse_mode": "Markdown"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    log.warning(f"[TG] HTTP {resp.status} enviando mensaje: {body[:200]}")
        except asyncio.TimeoutError:
            log.warning("[TG] Timeout enviando mensaje (10s).")
        except Exception as e:
            log.warning(f"[TG] Error enviando mensaje: {type(e).__name__}: {e}")

    async def send_telegram_photo(self, buf: BytesIO, caption: str = "") -> bool:
        if not self.telegram_token or not self.telegram_chat_id:
            log.warning("[TG] Faltan TELEGRAM_TOKEN o TELEGRAM_CHAT_ID en variables de entorno.")
            return False
        if not self._aio_session:
            log.warning("[TG] aiohttp session no inicializada. El bot no pudo enviar foto.")
            return False
        if not buf:
            log.warning("[TG] Buffer de imagen vacío.")
            return False
        try:
            buf.seek(0)
            url = f"https://api.telegram.org/bot{self.telegram_token}/sendPhoto"
            form = aiohttp.FormData()
            form.add_field("chat_id", self.telegram_chat_id)
            if caption:
                form.add_field("caption", caption[:1024])
                form.add_field("parse_mode", "Markdown")
            form.add_field("photo", buf.read(), filename="chart.png", content_type="image/png")
            async with self._aio_session.post(url, data=form, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                if resp.status == 200:
                    log.info("[TG] Grafico enviado a Telegram.")
                    return True
                body = await resp.text()
                log.warning(f"[TG] Error enviando foto: HTTP {resp.status} | {body[:200]}")
                return False
        except asyncio.TimeoutError:
            log.warning("[TG] Timeout enviando foto (60s).")
            return False
        except Exception as e:
            log.warning(f"[TG] Error enviando foto: {type(e).__name__}: {e}")
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
        v0_idx: int = None,
        confirm_idx: int = None,
    ) -> Optional[BytesIO]:
        """Genera grafico PNG dark-theme con BB, entrada, SL, TP, V0 y Confirmacion."""
        try:
            if df is None or len(df) < 30:
                log.warning(f"[CHART] Datos insuficientes para graficar {symbol}")
                return None

            bb_upper_full, bb_basis_full, bb_lower_full = self.calculate_bb(df["close"])
            ha_df = self.heikin_ashi(df)

            center = entry_idx if entry_idx is not None else len(df) // 2
            before, after = 40, 15
            s = max(0, center - before)
            e = min(len(df), center + after)

            # Velas Heikin Ashi para el gráfico
            o_w = ha_df["ha_open"].values[s:e]
            h_w = ha_df["ha_high"].values[s:e]
            l_w = ha_df["ha_low"].values[s:e]
            c_w = ha_df["ha_close"].values[s:e]
            bb_u = bb_upper_full.values[s:e]
            bb_b = bb_basis_full.values[s:e]
            bb_l = bb_lower_full.values[s:e]
            n_w = e - s
            x = np.arange(n_w)

            local_entry = (entry_idx - s) if entry_idx is not None else None
            local_v0 = (v0_idx - s) if v0_idx is not None else None
            local_confirm = (confirm_idx - s) if confirm_idx is not None else None

            fig, ax = plt.subplots(figsize=(14, 7), facecolor='#1a1a1a')
            ax.set_facecolor('#1a1a1a')
            ax.tick_params(colors='white', labelsize=8)
            ax.grid(True, color='#333333', linewidth=0.3, alpha=0.5)
            for spine in ax.spines.values():
                spine.set_color('#333333')

            # Velas Heikin Ashi
            for i in range(n_w):
                color = '#26A69A' if c_w[i] >= o_w[i] else '#FF4444'
                ax.plot([x[i], x[i]], [l_w[i], h_w[i]], color=color, linewidth=0.8)
                body_bottom = min(o_w[i], c_w[i])
                body_height = abs(c_w[i] - o_w[i])
                if body_height < (h_w[i] - l_w[i]) * 0.001:
                    body_height = (h_w[i] - l_w[i]) * 0.003
                rect = plt.Rectangle((x[i] - 0.35, body_bottom), 0.7, body_height,
                                      facecolor=color, edgecolor=color, linewidth=0.5)
                ax.add_patch(rect)

            # Bollinger Bands
            valid_u = ~np.isnan(bb_u)
            valid_l = ~np.isnan(bb_l)
            ax.plot(x[valid_u], bb_u[valid_u], color='#FF0000', linewidth=1.0, label='BB Upper')
            ax.plot(x[valid_l], bb_l[valid_l], color='#26A69A', linewidth=1.0, label='BB Lower')

            for i in range(1, n_w):
                if np.isnan(bb_b[i]) or np.isnan(bb_b[i-1]):
                    continue
                color = '#26A69A' if c_w[i] >= bb_b[i] else '#FF4444'
                ax.plot([x[i-1], x[i]], [bb_b[i-1], bb_b[i]], color=color, linewidth=1.2)

            # Entry / SL / TP
            ax.axhline(y=entry_price, color='#FFD700', linestyle='--', linewidth=1.5, alpha=0.8,
                       label=f'Entry {entry_price:.4f}')
            ax.axhline(y=sl_price, color='#FF0000', linestyle='--', linewidth=1.5, alpha=0.8,
                       label=f'SL {sl_price:.4f}')
            ax.axhline(y=tp_price, color='#00FF00', linestyle='--', linewidth=1.5, alpha=0.8,
                       label=f'TP {tp_price:.4f}')

            # Marcador ENTRY
            if local_entry is not None and 0 <= local_entry < n_w:
                rng = h_w[local_entry] - l_w[local_entry] if h_w[local_entry] != l_w[local_entry] else entry_price * 0.002
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

            # Marcador V0 (vela que toca la banda)
            if local_v0 is not None and 0 <= local_v0 < n_w:
                v0_color = '#FF6600'  # naranja para V0
                candle_range = h_w[local_v0] - l_w[local_v0]
                if candle_range < entry_price * 0.001:
                    candle_range = entry_price * 0.003
                # V0: LONG toca lower (marcador abajo), SHORT toca upper (marcador arriba)
                if side == "long":
                    v0_y = l_w[local_v0] - candle_range * 0.3
                    v0_symbol = 'v'
                    v0_va = 'top'
                else:
                    v0_y = h_w[local_v0] + candle_range * 0.3
                    v0_symbol = '^'
                    v0_va = 'bottom'
                ax.plot(x[local_v0], v0_y, marker=v0_symbol, color=v0_color,
                        markersize=10, zorder=5)
                ax.annotate('V0', xy=(x[local_v0], v0_y),
                            xytext=(x[local_v0], v0_y + (candle_range * 0.4 if side == "long" else -candle_range * 0.4)),
                            fontsize=8, color=v0_color, fontweight='bold',
                            ha='center', va=v0_va)

            # Marcador CONF (vela de confirmacion)
            if local_confirm is not None and 0 <= local_confirm < n_w:
                conf_color = '#00BFFF'  # azul celeste para confirmacion
                candle_range_c = h_w[local_confirm] - l_w[local_confirm]
                if candle_range_c < entry_price * 0.001:
                    candle_range_c = entry_price * 0.003
                # CONF: misma logica direccional que V0
                if side == "long":
                    conf_y = l_w[local_confirm] - candle_range_c * 0.3
                    conf_symbol = 'v'
                    conf_va = 'top'
                else:
                    conf_y = h_w[local_confirm] + candle_range_c * 0.3
                    conf_symbol = '^'
                    conf_va = 'bottom'
                ax.plot(x[local_confirm], conf_y, marker=conf_symbol, color=conf_color,
                        markersize=10, zorder=5)
                ax.annotate('CONF', xy=(x[local_confirm], conf_y),
                            xytext=(x[local_confirm], conf_y + (candle_range_c * 0.4 if side == "long" else -candle_range_c * 0.4)),
                            fontsize=8, color=conf_color, fontweight='bold',
                            ha='center', va=conf_va)

            side_label = "LONG" if side == "long" else "SHORT"
            titulo = f"{symbol} | {side_label} | Entry: {entry_price:.6f} | SL: {sl_price:.6f} | TP: {tp_price:.6f}"
            ax.set_title(titulo, color='white', fontsize=12, fontweight='bold', pad=10)
            ax.set_ylabel('Precio (USDT)', color='white', fontsize=9)
            ax.legend(loc='upper left', fontsize=8, facecolor='#1a1a1a', edgecolor='#444',
                      labelcolor='white')
            ax.set_xlim(-1, n_w)

            step = max(1, n_w // 8)
            ticks = list(range(0, n_w, step))
            labels = [f'+{i}c' for i in ticks]
            ax.set_xticks(ticks)
            ax.set_xticklabels(labels, color='white', fontsize=8)

            plt.tight_layout()

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
    # UPDATE STOP LOSS EN BITGET (async)
    # ==========================================================
    async def _update_stop_loss(self, symbol: str, side: str, new_sl: float) -> bool:
        try:
            new_sl_fmt = await self._exch_call("price_to_precision", symbol, new_sl)
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
            await self._exch_call("private_mix_post_v2_mix_order_place_pos_tpsl", params)
            return True
        except RateLimitExceeded:
            log.warning(f"[429] _update_stop_loss {symbol}: Rate limit.")
            await asyncio.sleep(5)
            return False
        except BadRequest as e:
            log.error(f"[400] _update_stop_loss {symbol}: {e}")
            return False
        except NetworkError:
            log.warning(f"[NET] _update_stop_loss {symbol}: Error de red.")
            return False
        except ExchangeError as e:
            log.error(f"[500] _update_stop_loss {symbol}: {e}")
            return False
        except Exception as e:
            log.error(f"Error actualizando SL {symbol}: {e}")
            return False

    # ==========================================================
    # INDICADORES — Puros (CPU, no async necesario)
    # ==========================================================
    @staticmethod
    def heikin_ashi(df: pd.DataFrame) -> pd.DataFrame:
        """Convierte OHLCV regular a Heikin Ashi (vectorizado con numpy)."""
        df = df.copy()
        o = df["open"].values.astype(np.float64)
        h = df["high"].values.astype(np.float64)
        l = df["low"].values.astype(np.float64)
        c = df["close"].values.astype(np.float64)

        ha_close = (o + h + l + c) * 0.25
        n = len(df)

        if n == 0:
            df["ha_close"] = ha_close
            df["ha_open"] = np.float64(0.0)
            df["ha_high"] = h
            df["ha_low"] = l
            return df

        init = (o[0] + c[0]) * 0.5

        if n <= 500:
            powers_2 = np.power(2.0, np.arange(n, dtype=np.float64))
            decay = 0.5 ** np.arange(n, dtype=np.float64)
            hc_weighted = ha_close * powers_2
            cum_hcw = np.cumsum(hc_weighted)
            ha_open = np.empty(n, dtype=np.float64)
            ha_open[0] = init
            ha_open[1:] = decay[1:] * (init + cum_hcw[:-1])
        else:
            ha_open = np.empty(n, dtype=np.float64)
            ha_open[0] = init
            half = np.float64(0.5)
            for i in range(1, n):
                ha_open[i] = (ha_open[i - 1] + ha_close[i - 1]) * half

        ha_high = np.maximum(np.maximum(h, ha_open), ha_close)
        ha_low = np.minimum(np.minimum(l, ha_open), ha_close)

        df["ha_close"] = ha_close
        df["ha_open"] = ha_open
        df["ha_high"] = ha_high
        df["ha_low"] = ha_low
        return df

    def calculate_bb(self, close: pd.Series):
        """Calcula Bollinger Bands. Retorna: (upper, basis, lower)"""
        length = self.cfg["bb_length"]
        mult = self.cfg["bb_mult"]
        basis = close.rolling(length).mean()
        dev = mult * close.rolling(length).std()
        upper = basis + dev
        lower = basis - dev
        return upper, basis, lower

    @staticmethod
    def _ema_tv(close: pd.Series, period: int) -> pd.Series:
        """EMA con inicializacion SMA igual que ta.ema de TradingView."""
        result = pd.Series(np.nan, index=close.index, dtype=float)
        if len(close) < period:
            return result
        result.iloc[period - 1] = close.iloc[:period].mean()
        alpha = 2.0 / (period + 1)
        for i in range(period, len(close)):
            result.iloc[i] = close.iloc[i] * alpha + result.iloc[i - 1] * (1.0 - alpha)
        return result

    def calculate_macd_overlay(self, close: pd.Series) -> pd.Series:
        """Retorna Serie booleana: True = Signal verde (MACD >= Signal)."""
        fast = self._ema_tv(close, self.cfg["macd_fast"])
        slow = self._ema_tv(close, self.cfg["macd_slow"])
        macd = fast - slow
        signal = macd.rolling(self.cfg["macd_signal"]).mean()
        return macd >= signal

    # ==========================================================
    # ESTRATEGIA: DETECCION DE SENAL (CPU puro)
    # ==========================================================
    def detect_signal(self, df: pd.DataFrame):
        """Detecta senal LONG o SHORT. Retorna (side, sl, tp, entry_idx, v0_idx, confirm_idx) o None."""
        min_candles = self.cfg["bb_length"] + self.cfg["macd_slow"] + self.cfg["macd_signal"] + self.cfg["confirmation_window"] + 5
        if len(df) < min_candles:
            return None

        # Usar .values para operaciones vectorizadas y evitar copia innecesaria
        bb_upper, bb_basis, bb_lower = self.calculate_bb(df["close"])
        macd_green = self.calculate_macd_overlay(df["close"])
        ha_df = self.heikin_ashi(df)

        warmup = max(self.cfg["bb_length"], self.cfg["macd_slow"] + self.cfg["macd_signal"]) + 2
        ha_slice = ha_df.iloc[warmup:].reset_index(drop=True)
        bb_upper_s = bb_upper.iloc[warmup:].reset_index(drop=True)
        bb_basis_s = bb_basis.iloc[warmup:].reset_index(drop=True)
        bb_lower_s = bb_lower.iloc[warmup:].reset_index(drop=True)
        macd_green_s = macd_green.iloc[warmup:].reset_index(drop=True)

        # Extras del df original (para SL con low/high regular y entry con open)
        reg_low = df["low"].iloc[warmup:].reset_index(drop=True)
        reg_high = df["high"].iloc[warmup:].reset_index(drop=True)
        reg_open = df["open"].iloc[warmup:].reset_index(drop=True)

        if len(ha_slice) < self.cfg["confirmation_window"] + 2:
            return None

        # Pre-cargar arrays numpy para acceso rapido (evita overhead de pandas iloc)
        ha_low_arr = ha_slice["ha_low"].values
        ha_high_arr = ha_slice["ha_high"].values
        ha_close_arr = ha_slice["ha_close"].values
        ha_open_arr = ha_slice["ha_open"].values
        bb_upper_arr = bb_upper_s.values
        bb_lower_arr = bb_lower_s.values
        bb_basis_arr = bb_basis_s.values
        macd_arr = macd_green_s.values
        close_arr = df["close"].iloc[warmup:].reset_index(drop=True).values
        reg_low_arr = reg_low.values
        reg_high_arr = reg_high.values
        reg_open_arr = reg_open.values

        n = len(ha_slice)
        window = self.cfg["confirmation_window"]
        sl_buf = self.cfg["sl_buffer_pct"]
        bb_len = self.cfg["bb_length"]

        # Intentar LONG primero, luego SHORT (mismo orden que original)
        result = self._scan_side_arrays(
            "long", ha_low_arr, ha_high_arr, ha_close_arr, ha_open_arr,
            bb_upper_arr, bb_basis_arr, bb_lower_arr, macd_arr,
            close_arr, reg_low_arr, reg_high_arr, reg_open_arr, n, window, sl_buf, bb_len
        )
        if result:
            side, sl, tp, entry_idx, v0_idx, confirm_idx = result
            return (side, sl, tp, entry_idx + warmup, v0_idx + warmup, confirm_idx + warmup)

        result = self._scan_side_arrays(
            "short", ha_low_arr, ha_high_arr, ha_close_arr, ha_open_arr,
            bb_upper_arr, bb_basis_arr, bb_lower_arr, macd_arr,
            close_arr, reg_low_arr, reg_high_arr, reg_open_arr, n, window, sl_buf, bb_len
        )
        if result:
            side, sl, tp, entry_idx, v0_idx, confirm_idx = result
            return (side, sl, tp, entry_idx + warmup, v0_idx + warmup, confirm_idx + warmup)

        return None

    def _scan_side_arrays(
        self, side, ha_low, ha_high, ha_close, ha_open,
        bb_upper, bb_basis, bb_lower, macd_green,
        close, reg_low, reg_high, reg_open, n, window, sl_buf, bb_len
    ):
        """Escaneo sobre arrays numpy puros (sin pandas)."""
        max_v0 = n - 2
        freshness = window + 2
        min_v0 = max(n - freshness, bb_len)

        for v0_idx in range(max_v0, min_v0, -1):
            if side == "long":
                if np.isnan(bb_lower[v0_idx]) or ha_low[v0_idx] > bb_lower[v0_idx]:
                    continue
                for offset in range(1, window + 1):
                    v_idx = v0_idx + offset
                    if v_idx >= n:
                        break
                    if np.isnan(bb_basis[v_idx]):
                        continue
                    signal_green = close[v_idx] >= bb_basis[v_idx]
                    if ha_close[v_idx] > ha_open[v_idx] and macd_green[v_idx] and signal_green:
                        # REGLA: Si la vela CONF toca la banda inferior, anular señal LONG
                        if not np.isnan(bb_lower[v_idx]) and ha_low[v_idx] <= bb_lower[v_idx]:
                            continue
                        entry_idx = v_idx + 1
                        if entry_idx >= n:
                            continue
                        entry_price = reg_open[entry_idx]
                        if entry_price <= 0:
                            continue
                        basis_at_entry = bb_basis[entry_idx]
                        if np.isnan(basis_at_entry) or close[entry_idx] < basis_at_entry:
                            continue
                        sl_raw = reg_low[v_idx] * (1 - sl_buf)
                        sl_dist = (entry_price - sl_raw) / entry_price
                        if sl_dist <= 0 or sl_dist > 0.10:
                            continue
                        sl = entry_price * (1 - sl_dist)
                        tp = entry_price + 2 * (entry_price - sl)
                        min_gap = entry_price * 0.0001
                        if sl >= entry_price - min_gap:
                            continue
                        return ("long", sl, tp, entry_idx, v0_idx, v_idx)
            else:
                if np.isnan(bb_upper[v0_idx]) or ha_high[v0_idx] < bb_upper[v0_idx]:
                    continue
                for offset in range(1, window + 1):
                    v_idx = v0_idx + offset
                    if v_idx >= n:
                        break
                    if np.isnan(bb_basis[v_idx]):
                        continue
                    signal_red = close[v_idx] < bb_basis[v_idx]
                    if ha_close[v_idx] < ha_open[v_idx] and not macd_green[v_idx] and signal_red:
                        # REGLA: Si la vela CONF toca la banda superior, anular señal SHORT
                        if not np.isnan(bb_upper[v_idx]) and ha_high[v_idx] >= bb_upper[v_idx]:
                            continue
                        entry_idx = v_idx + 1
                        if entry_idx >= n:
                            continue
                        entry_price = reg_open[entry_idx]
                        if entry_price <= 0:
                            continue
                        basis_at_entry = bb_basis[entry_idx]
                        if np.isnan(basis_at_entry) or close[entry_idx] >= basis_at_entry:
                            continue
                        sl_raw = reg_high[v_idx] * (1 + sl_buf)
                        sl_dist = (sl_raw - entry_price) / entry_price
                        if sl_dist <= 0 or sl_dist > 0.10:
                            continue
                        sl = entry_price * (1 + sl_dist)
                        tp = entry_price - 2 * (sl - entry_price)
                        min_gap = entry_price * 0.0001
                        if sl <= entry_price + min_gap:
                            continue
                        return ("short", sl, tp, entry_idx, v0_idx, v_idx)

        return None

    # ==========================================================
    # SCAN DE SENALES (async)
    # ==========================================================
    async def scan_signals(self, symbols: list) -> list:
        """Descarga OHLCV async y busca senales."""
        signals = []
        if not symbols:
            return signals

        try:
            ohlcv_data = await self.fetch_ohlcv_batch(symbols, self.cfg["timeframe"], self.cfg["ohlcv_limit"])
        except RateLimitExceeded:
            log.warning("[429] scan_signals: Rate limit descargando velas.")
            return signals
        except NetworkError:
            log.warning("[NET] scan_signals: Error de red.")
            return signals
        except ExchangeError as e:
            log.error(f"[500] scan_signals: {e}")
            return signals
        except Exception as e:
            log.error(f"Error descargando OHLCV: {e}")
            return signals

        for symbol in symbols:
            if symbol in self.session_active:
                continue
            data = ohlcv_data.get(symbol)
            if not data or len(data) < 20:
                continue
            try:
                df = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close", "volume"])
                result = self.detect_signal(df)
                if result:
                    side, sl, tp, entry_idx, v0_idx, confirm_idx = result
                    signals.append({
                        "symbol": symbol,
                        "side": side,
                        "sl_price": sl,
                        "tp_price": tp,
                        "entry_idx": entry_idx,
                        "v0_idx": v0_idx,
                        "confirm_idx": confirm_idx,
                        "df": df,
                    })
                    log.info(f"Senal detectada: {symbol} {side.upper()} | SL={sl:.6f} TP={tp:.6f}")
            except Exception as e:
                log.error(f"Error detectando senal en {symbol}: {e}")
                continue

        return signals

    # ==========================================================
    # OPEN POSITION (async)
    # ==========================================================
    async def open_position(
        self,
        symbol: str,
        side: str,
        sl_price: float,
        tp_price: float,
        balance: float = None,
        df: pd.DataFrame = None,
        entry_idx: int = None,
        v0_idx: int = None,
        confirm_idx: int = None,
    ) -> bool:
        if symbol in self.session_active:
            log.debug(f"{symbol} ya tiene posicion activa. Saltando.")
            return False

        if balance is None:
            balance = await self.get_balance()
        if balance <= 0:
            log.warning(f"Balance insuficiente para {symbol}")
            return False

        try:
            ticker = await self._exch_call("fetch_ticker", symbol)
            price = float(ticker["last"])

            strategy_entry = float(df.iloc[entry_idx]["open"]) if df is not None and entry_idx is not None and entry_idx < len(df) else price

            if not all(math.isfinite(v) for v in [sl_price, tp_price, strategy_entry, price]):
                log.warning(f"{symbol} precio invalido (NaN/Inf). Saltando.")
                return False

            if side == "long":
                sl_dist = (strategy_entry - sl_price) / strategy_entry
            else:
                sl_dist = (sl_price - strategy_entry) / strategy_entry
            if sl_dist <= 0 or sl_dist > 0.10:
                log.warning(f"{symbol} SL invalido ({sl_dist*100:.1f}%). Saltando.")
                return False

            # --- VALIDAR TP vs PRECIO ACTUAL ---
            if side == "long" and tp_price <= price:
                log.warning(f"{symbol} LONG: TP ({tp_price:.4f}) <= precio actual ({price:.4f}). Saltando.")
                return False
            if side == "short" and tp_price >= price:
                log.warning(f"{symbol} SHORT: TP ({tp_price:.4f}) >= precio actual ({price:.4f}). Saltando.")
                return False

            # --- CALCULO DE QTY (identico al codigo original) ---
            risk_pct = self.cfg.get("risk_pct", 0.02)
            leverage = self.cfg["leverage"]
            target_margin = balance * risk_pct
            pos_value = target_margin * leverage
            raw_qty = pos_value / price

            market = await self._exch_call("market", symbol)
            precision = market["precision"]["amount"]
            step = market["limits"]["amount"]["min"] or (10 ** -precision)

            # Floor al step mas cercano (como el original)
            qty = (raw_qty // step) * step

            # Proteccion: si qty=0 usar minimo
            if qty <= 0:
                qty = step

            actual_margin = (qty * price) / leverage

            # Si el margen real se pasa del target, bajar un step
            if actual_margin > target_margin:
                qty -= step
                if qty <= 0:
                    qty = step
                actual_margin = (qty * price) / leverage

            log.info(f"⚖️ {symbol} | Target: {target_margin:.2f} | Real: {actual_margin:.2f} | Qty: {qty}")

            # --- ENVIO DE ORDER (sync directo, identico al codigo original) ---
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
            await self.send_telegram(msg)
            log.info(f"{symbol} {side.upper()} | Entry={fmt_price} SL={fmt_sl} TP={fmt_tp} | Qty={qty} | Margin={actual_margin:.2f}")

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
            await self._save_trade_entries()
            self.session_active.add(symbol)

            # Grafico async
            if df is not None:
                try:
                    buf = await asyncio.to_thread(
                        self.generar_grafico_signal, symbol, df, side, strategy_entry, sl_price, tp_price, entry_idx, v0_idx, confirm_idx
                    )
                    if buf:
                        caption = f"*{symbol} {side.upper()}*\nEntry: `{fmt_price}` | SL: `{fmt_sl}` | TP: `{fmt_tp}`"
                        sent = await self.send_telegram_photo(buf, caption)
                        if not sent:
                            log.warning(f"[CHART] No se pudo enviar grafico de {symbol} a Telegram.")
                    else:
                        log.warning(f"[CHART] grafico_signal retorno None para {symbol}.")
                except Exception as e:
                    log.warning(f"[CHART] Error generando/enviando grafico para {symbol}: {e}")

            return True

        except BadRequest as e:
            log.error(f"[400] open_position {symbol}: {e}")
            return False
        except RateLimitExceeded:
            log.warning(f"[429] open_position {symbol}: Rate limit.")
            await asyncio.sleep(5)
            return False
        except AuthenticationError as e:
            log.critical(f"[AUTH] open_position {symbol}: {e}")
            return False
        except PermissionDenied as e:
            log.critical(f"[PERM] open_position {symbol}: {e}")
            return False
        except NetworkError:
            log.warning(f"[NET] open_position {symbol}: Error de red.")
            return False
        except ExchangeError as e:
            log.error(f"[500] open_position {symbol}: {e}")
            return False
        except Exception as e:
            log.error(f"Error abriendo {symbol}: {e}")
            return False

    # ==========================================================
    # MANAGE POSITIONS (async)
    # ==========================================================
    async def manage_positions(self, balance: float = None):
        if balance is None:
            balance = await self.get_balance()

        try:
            positions = await self._exch_call("fetch_positions")
            active_symbols = [p["symbol"] for p in positions if float(p["contracts"]) > 0]

            # 1. Detectar posiciones cerradas
            for sym in list(self.session_active):
                if sym not in active_symbols:
                    self.cooldowns[sym] = time.time() + 3600
                    log.info(f"{sym} CERRADA. Cooldown 1h activado.")
                    await self._process_closed_position(sym)
                    self._cleanup_symbol(sym)

            # 2. Gestionar posiciones abiertas
            for pos in positions:
                symbol = pos["symbol"]
                side = pos["side"]
                if float(pos["contracts"]) == 0:
                    continue

                entry = float(pos["entryPrice"])
                mark = float(pos["markPrice"])
                profit_pct = (mark - entry) / entry if side == "long" else (entry - mark) / entry

                # Adverse price tracking
                if symbol not in self.adverse_prices:
                    self.adverse_prices[symbol] = mark
                elif side == "long":
                    self.adverse_prices[symbol] = min(self.adverse_prices[symbol], mark)
                else:
                    self.adverse_prices[symbol] = max(self.adverse_prices[symbol], mark)

                # Peak price tracking
                if symbol not in self.peak_prices:
                    self.peak_prices[symbol] = mark
                elif side == "long":
                    self.peak_prices[symbol] = max(self.peak_prices[symbol], mark)
                else:
                    self.peak_prices[symbol] = min(self.peak_prices[symbol], mark)

                # 3. Break Even
                if profit_pct >= self.cfg["be_trigger_pct"]:
                    if not self.alerts_history.get(f"{symbol}_be", False):
                        if side == "long":
                            new_sl = entry * (1 + self.cfg["be_offset_pct"])
                        else:
                            new_sl = entry * (1 - self.cfg["be_offset_pct"])
                        if await self._update_stop_loss(symbol, side, new_sl):
                            self.alerts_history[f"{symbol}_be"] = True
                            self.alerts_history[f"{symbol}_be_price"] = new_sl
                            log.info(f"{symbol} BE+ activado (offset {self.cfg['be_offset_pct']*100:.1f}%)")
                            await self.send_telegram(f"*{symbol}* BE+ (offset {self.cfg['be_offset_pct']*100:.1f}%)")

                # 4. Trailing Stop (solo despues de BE)
                if self.alerts_history.get(f"{symbol}_be", False):
                    peak = self.peak_prices[symbol]
                    if side == "long":
                        nuevo_sl = peak * (1 - self.cfg["trailing_dist_pct"])
                        ultimo = self.alerts_history.get(f"{symbol}_trail", 0)
                        if nuevo_sl > ultimo:
                            if await self._update_stop_loss(symbol, side, nuevo_sl):
                                self.alerts_history[f"{symbol}_trail"] = nuevo_sl
                                self.trail_counts[symbol] = self.trail_counts.get(symbol, 0) + 1
                                log.info(f"{symbol} Trail -> {nuevo_sl:.6f}")
                    else:
                        nuevo_sl = peak * (1 + self.cfg["trailing_dist_pct"])
                        ultimo = self.alerts_history.get(f"{symbol}_trail", 999999)
                        if nuevo_sl < ultimo:
                            if await self._update_stop_loss(symbol, side, nuevo_sl):
                                self.alerts_history[f"{symbol}_trail"] = nuevo_sl
                                self.trail_counts[symbol] = self.trail_counts.get(symbol, 0) + 1
                                log.info(f"{symbol} Trail -> {nuevo_sl:.6f}")

                # 5. Monitoreo de SL prematuro
                for mon_sym in list(self.premature_sl_monitor.keys()):
                    mon = self.premature_sl_monitor[mon_sym]
                    hours_since = (datetime.now() - datetime.fromisoformat(mon["sl_time"])).total_seconds() / 3600
                    if hours_since > 24:
                        self._save_premature_sl(mon, False)
                        del self.premature_sl_monitor[mon_sym]
                        continue
                    try:
                        ticker = await self._exch_call("fetch_ticker", mon_sym)
                        curr = float(ticker["last"])
                        if (mon["side"] == "long" and curr >= mon["tp_price"]) or \
                           (mon["side"] == "short" and curr <= mon["tp_price"]):
                            self._save_premature_sl(mon, True, datetime.now())
                            log.info(f"{mon_sym}: SL prematuro alcanzo TP despues del SL")
                            del self.premature_sl_monitor[mon_sym]
                    except (RateLimitExceeded, NetworkError, ExchangeError, Exception):
                        continue

        except RateLimitExceeded:
            log.warning("[429] manage_positions: Rate limit.")
            await asyncio.sleep(5)
        except NetworkError:
            log.warning("[NET] manage_positions: Error de red.")
        except ExchangeError as e:
            log.error(f"[500] manage_positions: {e}")
        except Exception as e:
            log.error(f"Error en manage_positions: {e}")

    # ==========================================================
    # CLOSE POSITION (async)
    # ==========================================================
    async def close_position(self, symbol: str) -> bool:
        try:
            await self._exch_call("close_position", symbol)
            log.info(f"{symbol} cerrada manualmente.")
            return True
        except RateLimitExceeded:
            log.warning(f"[429] close_position {symbol}: Rate limit.")
            await asyncio.sleep(5)
            return False
        except BadRequest as e:
            log.error(f"[400] close_position {symbol}: {e}")
            return False
        except NetworkError:
            log.warning(f"[NET] close_position {symbol}: Error de red.")
            return False
        except ExchangeError as e:
            log.error(f"[500] close_position {symbol}: {e}")
            return False
        except Exception as e:
            log.error(f"Error cerrando {symbol}: {e}")
            return False

    # ==========================================================
    # PROCESAMIENTO DE CIERRE (async)
    # ==========================================================
    async def _process_closed_position(self, sym: str):
        try:
            await asyncio.sleep(2)
            trades = await self._exch_call("fetch_my_trades", sym, limit=20)
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

            await self.send_telegram(f"*{sym} CERRADA*\nPnL: {net:.2f} USDT ({status})\nFees: -{trade_fees:.2f}")
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
            await self._save_trade_entries()

        except RateLimitExceeded:
            log.warning(f"[429] _process_closed_position {sym}: Rate limit.")
        except NetworkError:
            log.warning(f"[NET] _process_closed_position {sym}: Error de red.")
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
        if net_pnl >= 0:
            if self.consecutive_losses > 0:
                log.info(f"Trade ganador. Perdidas reseteadas ({self.consecutive_losses} -> 0)")
            self.consecutive_losses = 0
        else:
            self.consecutive_losses += 1
            log.info(f"Perdida consecutiva #{self.consecutive_losses}")
            if self.consecutive_losses >= self.cfg["max_consecutive_losses"]:
                self.cooldown_until = time.time() + self.cfg["cooldown_hours"] * 3600
                log.warning(f"{self.cfg['max_consecutive_losses']} perdidas. Pausa {self.cfg['cooldown_hours']}h.")
                # Telegram se envia desde manage_positions (async)

    def is_on_cooldown(self) -> bool:
        if self.cooldown_until is None:
            return False
        if time.time() >= self.cooldown_until:
            log.info("Cooldown finalizado. Reanudando.")
            self.cooldown_until = None
            self.consecutive_losses = 0
            return False
        remaining = (self.cooldown_until - time.time()) / 60
        log.debug(f"En pausa. Faltan {remaining:.0f} min.")
        return True

    # ==========================================================
    # PERSISTENCIA (mixta: sync para archivos, async para trade_entries)
    # ==========================================================
    async def _save_trade_entries(self):
        def _sync():
            data = {sym: e.copy() for sym, e in self.trade_entries.items()}
            with open(self.trade_entries_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        await asyncio.to_thread(_sync)

    async def _load_trade_entries(self):
        def _sync():
            if not os.path.exists(self.trade_entries_path):
                return {}
            with open(self.trade_entries_path, "r", encoding="utf-8") as f:
                return json.load(f)
        try:
            data = await asyncio.to_thread(_sync)
            if data:
                self.trade_entries.update(data)
                log.info(f"Cargadas {len(data)} entradas desde trade_entries.json")
        except Exception as ex:
            log.error(f"Error cargando trade_entries: {ex}")

    def _save_trade_csv(self, entry, exit_price, raw_pnl, fees, net, status, reason, entry_dt: datetime):
        now = datetime.now()
        duration = (now - entry_dt).total_seconds() / 3600
        ep = entry["entry_price"]
        sym = entry["symbol"]

        row = {
            "entry_time": entry_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "exit_time": now.strftime("%Y-%m-%d %H:%M:%S"),
            "symbol": sym,
            "side": entry["side"],
            "entry_price": ep,
            "exit_price": exit_price,
            "sl_price": entry["sl_price"],
            "tp_price": entry["tp_price"],
            "sl_pct": round(abs(ep - entry["sl_price"]) / ep * 100, 2),
            "tp_pct": round(abs(entry["tp_price"] - ep) / ep * 100, 2),
            "quantity": entry["quantity"],
            "balance_before": round(entry["balance_before"], 2),
            "balance_after": round(entry["balance_before"] + net, 2),
            "pnl": round(raw_pnl, 2),
            "fees": round(fees, 2),
            "net_pnl": round(net, 2),
            "status": status,
            "duration_hours": round(duration, 2),
            "close_reason": reason,
            "be_triggered": 1 if self.alerts_history.get(f"{sym}_be", False) else 0,
            "be_price": round(self.alerts_history.get(f"{sym}_be_price", 0), 4),
            "trail_count": self.trail_counts.get(sym, 0),
            "trail_peak_price": round(self.peak_prices.get(sym, ep), 4),
            "trail_final_sl": round(self.alerts_history.get(f"{sym}_trail", entry["sl_price"]), 4),
            "entry_weekday": entry_dt.weekday(),
            "entry_hour": entry_dt.hour,
            "size_usdt": entry.get("size_usdt", 0),
            "risk_pct": entry.get("risk_pct", 0),
            "max_favorable_pct": round(abs(self.peak_prices.get(sym, ep) - ep) / ep * 100, 2),
            "max_adverse_pct": round(abs(self.adverse_prices.get(sym, ep) - ep) / ep * 100, 2),
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
    # UTILITIES (async)
    # ==========================================================
    async def get_open_symbols(self) -> set:
        try:
            positions = await self._exch_call("fetch_positions")
            return {p["symbol"] for p in positions if float(p["contracts"]) > 0}
        except (RateLimitExceeded, NetworkError, ExchangeError, Exception):
            return set()

    def is_cooling_down(self, symbol: str) -> bool:
        if symbol in self.cooldowns:
            if time.time() < self.cooldowns[symbol]:
                return True
            del self.cooldowns[symbol]
        return False

    async def get_position_count(self) -> int:
        return len(await self.get_open_symbols())

    async def can_open(self) -> bool:
        return (await self.get_position_count()) < self.cfg["max_open_positions"]

    # ==========================================================
    # RUN — Loop principal ASYNC
    # ==========================================================
    async def run(self):
        """
        Loop principal async:
        - Cada ~15s: gestiona posiciones (BE, trailing, cierres)
        - Cada 5 min: escanea TOP 100 y busca senales
        """
        if not await self.start():
            return

        # Sincronizar session_active con posiciones abiertas
        try:
            open_pos = await self.get_open_symbols()
            self.session_active = open_pos
            if open_pos:
                log.info(f"Posiciones abiertas detectadas al arrancar: {open_pos}")
            else:
                log.info("Sin posiciones abiertas al arrancar.")
        except Exception as e:
            log.warning(f"No se pudieron sincronizar posiciones: {e}")

        self.last_scan_time = 0
        log.info(f"BotBB arrancado | TF={self.cfg['timeframe']} | TOP={self.cfg['top_symbols_count']} | MaxPos={self.cfg['max_open_positions']} | Semaphore={self.cfg['max_concurrent_fetches']}")

        try:
            while True:
                try:
                    balance = await self.get_balance()

                    # Gestionar posiciones cada ciclo
                    await self.manage_positions(balance)

                    # Escanear senales cada scan_interval_sec
                    elapsed = time.time() - self.last_scan_time
                    if elapsed >= self.cfg["scan_interval_sec"]:
                        if not self.is_on_cooldown() and await self.can_open():
                            log.info("Escaneando TOP %d simbolos...", self.cfg["top_symbols_count"])
                            top = await self.get_top_symbols(self.cfg["top_symbols_count"])
                            if top:
                                signals = await self.scan_signals(top)
                                for sig in signals:
                                    if await self.can_open() and not self.is_on_cooldown():
                                        await self.open_position(
                                            symbol=sig["symbol"],
                                            side=sig["side"],
                                            sl_price=sig["sl_price"],
                                            tp_price=sig["tp_price"],
                                            balance=balance,
                                            df=sig.get("df"),
                                            entry_idx=sig.get("entry_idx"),
                                            v0_idx=sig.get("v0_idx"),
                                            confirm_idx=sig.get("confirm_idx"),
                                        )
                                if not signals:
                                    log.info("Sin senales en este escaneo.")
                        elif self.is_on_cooldown():
                            log.debug("En cooldown. Saltando escaneo.")
                        self.last_scan_time = time.time()

                    await asyncio.sleep(15)

                except RateLimitExceeded:
                    log.warning("[429] Ciclo principal: Rate limit. Esperando 30s...")
                    await asyncio.sleep(30)
                except NetworkError:
                    log.warning("[NET] Ciclo principal: Error de red. Reconectando en 15s...")
                    await asyncio.sleep(15)
                    try:
                        await self._connect()
                    except Exception:
                        pass
                except AuthenticationError as e:
                    log.critical(f"[AUTH] Credenciales invalidas. Deteniendo. {e}")
                    break
                except PermissionDenied as e:
                    log.critical(f"[AUTH] Sin permisos. Deteniendo. {e}")
                    break
                except ExchangeError as e:
                    log.error(f"[500] Ciclo principal: {e}. Continuando...")
                    await asyncio.sleep(15)
                except Exception as e:
                    log.error(f"Error en ciclo principal: {e}")
                    await asyncio.sleep(15)

        except KeyboardInterrupt:
            log.info("Bot detenido por el usuario.")
        finally:
            await self.stop()


# ==========================================================
# MAIN
# ==========================================================
if __name__ == "__main__":
    engine = BotBBEngine()
    asyncio.run(engine.run())
