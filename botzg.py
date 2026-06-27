#!/usr/bin/env python3
"""
BOTZG — MoneyZG 1-Min Crypto Scalping Bot (Monolith)
=====================================================
Estrategia exacta del video: MACD(12,26,9) + SMA(200) + Heikin Ashi
SL=0.1% TP=0.15% Apalancamiento=5x Mercado=Market

Usage:
    python botzg.py backtest     Run backtest on TOP 50 Bitget symbols
    python botzg.py live         Start live trading (requires API keys)
    python botzg.py screen       Show TOP symbols by volume
    python botzg.py web          Start Flask web service (for Render)

Env vars (or .env):
    BITGET_API_KEY, BITGET_SECRET_KEY, BITGET_PASSPHRASE
    PAPER_TRADE=true|false (default: false)
    All other settings have defaults (see CONFIG section).
"""
from __future__ import annotations
import os, sys, time, json, math, asyncio, logging, threading, warnings
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Literal
from dotenv import load_dotenv
load_dotenv()

import numpy as np
import pandas as pd
pd.set_option("future.no_silent_downcasting", True)

# ──────────────────────────────────────────────────────────────
# SECTION 1 — CONFIG
# ──────────────────────────────────────────────────────────────
# Exchange
BITGET_API_KEY      = os.environ.get("BITGET_API_KEY", "")
BITGET_SECRET_KEY    = os.environ.get("BITGET_SECRET_KEY", "")
BITGET_PASSPHRASE    = os.environ.get("BITGET_PASSPHRASE", "")

# Strategy (MoneyZG del video)
ENTRY_TIMEFRAME     = os.environ.get("ENTRY_TIMEFRAME", "1m")
MACD_FAST           = int(os.environ.get("MACD_FAST", "12"))
MACD_SLOW           = int(os.environ.get("MACD_SLOW", "26"))
MACD_SIGNAL         = int(os.environ.get("MACD_SIGNAL", "9"))
SMA_LONG            = int(os.environ.get("SMA_LONG", "200"))

# Risk (del video)
LEVERAGE            = float(os.environ.get("LEVERAGE", "5"))
SL_PCT              = float(os.environ.get("SL_PCT", "0.001"))       # 0.1%
TP_MIN_PCT          = float(os.environ.get("TP_MIN_PCT", "0.0015"))  # 0.15% (1.5x SL)
TP_MAX_PCT          = float(os.environ.get("TP_MAX_PCT", "0.0020"))  # 0.20% (2x SL)
RISK_PCT            = float(os.environ.get("RISK_PCT", "0.10"))
MAX_POSITIONS       = int(os.environ.get("MAX_POSITIONS", "3"))
MAX_TRADES_DAY      = int(os.environ.get("MAX_TRADES_DAY", "999999"))

# Screener
TOP_N               = int(os.environ.get("TOP_N", "50"))
MIN_VOLUME_USD      = float(os.environ.get("MIN_VOLUME_USD", "1_000_000"))
QUOTE_CURRENCY      = os.environ.get("QUOTE_CURRENCY", "USDT")

# Backtest
BACKTEST_DAYS       = int(os.environ.get("BACKTEST_DAYS", "30"))
BACKTEST_TIMEFRAME  = os.environ.get("BACKTEST_TIMEFRAME", "1m")

# Live
POLL_INTERVAL_SEC   = int(os.environ.get("POLL_INTERVAL_SEC", "60"))
PAPER_TRADE         = os.environ.get("PAPER_TRADE", "false").lower() == "true"

# Paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(BASE_DIR, "moneyzg_bot.log")
DATA_DIR = os.path.join(BASE_DIR, "_bt_data")

OHLCV_COLS = ["timestamp", "open", "high", "low", "close", "volume"]

# Whitelist — symbols con >50% WR en backtest 30d 1m (29/44)
# Si TOP_N supera esta lista, solo se usará el subconjunto disponible
SYMBOL_WHITELIST: set[str] = {
    "AGLD/USDT:USDT", "HYPE/USDT:USDT", "XLM/USDT:USDT",
    "XAG/USDT:USDT",  "SYN/USDT:USDT",  "SOXL/USDT:USDT",
    "SUI/USDT:USDT",  "CL/USDT:USDT",   "SNDK/USDT:USDT",
    "MRVL/USDT:USDT", "ADA/USDT:USDT",  "LAB/USDT:USDT",
    "MAGMA/USDT:USDT","ZEC/USDT:USDT",  "NBIS/USDT:USDT",
    "QCOM/USDT:USDT", "INTC/USDT:USDT", "XRP/USDT:USDT",
    "SOL/USDT:USDT",  "MU/USDT:USDT",   "AAOI/USDT:USDT",
    "DRAM/USDT:USDT", "CRCL/USDT:USDT", "ALLO/USDT:USDT",
    "NEAR/USDT:USDT", "VELVET/USDT:USDT","AVAX/USDT:USDT",
    "TAO/USDT:USDT",  "PEPE/USDT:USDT",
}


def validate_config():
    """Raise ValueError if API keys missing (unless SKIP_API_CHECK is set)."""
    if not all([BITGET_API_KEY, BITGET_SECRET_KEY, BITGET_PASSPHRASE]):
        if not os.environ.get("SKIP_API_CHECK"):
            raise ValueError(
                "Bitget API keys missing. Set BITGET_API_KEY, "
                "BITGET_SECRET_KEY, BITGET_PASSPHRASE env vars."
            )

# ──────────────────────────────────────────────────────────────
# SECTION 2 — LOGGER
# ──────────────────────────────────────────────────────────────
def setup_logger(name: str = "botzg") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:  # ya configurado
        return logger
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setLevel(logging.INFO)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logger.addHandler(fh)

    sh = logging.StreamHandler(sys.stdout)
    sh.setLevel(logging.INFO)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    logger.propagate = False
    return logger


log = setup_logger()

# ──────────────────────────────────────────────────────────────
# SECTION 3 — INDICATORS (vectorized)
# ──────────────────────────────────────────────────────────────
def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def _sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period).mean()


def heikin_ashi(df: pd.DataFrame) -> pd.DataFrame:
    """Convierte velas OHLC a Heikin Ashi. Retorna df con columnas ha_*."""
    ha = pd.DataFrame(index=df.index)
    ha["ha_close"] = (df["open"] + df["high"] + df["low"] + df["close"]) / 4

    ha_open = [float(df["open"].iloc[0])]
    for i in range(1, len(df)):
        ha_open.append((ha_open[i-1] + ha["ha_close"].iloc[i-1]) / 2)
    ha["ha_open"] = ha_open

    ha["ha_high"] = ha[["ha_open", "ha_close"]].max(axis=1)
    ha["ha_high"] = pd.concat([ha["ha_high"], df["high"]], axis=1).max(axis=1)

    ha["ha_low"] = ha[["ha_open", "ha_close"]].min(axis=1)
    ha["ha_low"] = pd.concat([ha["ha_low"], df["low"]], axis=1).min(axis=1)

    ha["ha_bull"] = ha["ha_close"] > ha["ha_open"]
    ha["ha_bear"] = ha["ha_close"] < ha["ha_open"]
    ha["ha_no_low_wick"] = ha["ha_low"] >= ha["ha_open"]
    ha["ha_no_high_wick"] = ha["ha_high"] <= ha["ha_open"]
    return ha


def macd_indicator(series: pd.Series,
                   fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    """
    MACD(12,26,9). Retorna df con macd_line, signal_line, histogram y flags de cruce.
    """
    fast_ema = _ema(series, fast)
    slow_ema = _ema(series, slow)
    macd_line = fast_ema - slow_ema
    signal_line = _sma(macd_line, signal)
    hist = macd_line - signal_line

    out = pd.DataFrame({"macd": macd_line, "macd_signal": signal_line,
                        "macd_hist": hist}, index=series.index)
    out["macd_hist_up"] = hist > hist.shift(1)
    out["macd_hist_dn"] = hist < hist.shift(1)
    out["macd_above_zero"] = hist > 0
    out["macd_below_zero"] = hist < 0
    out["macd_cross_above"] = (macd_line > signal_line) & (macd_line.shift(1) <= signal_line.shift(1))
    out["macd_cross_below"] = (macd_line < signal_line) & (macd_line.shift(1) >= signal_line.shift(1))
    out["macd_above_signal"] = macd_line > signal_line
    out["macd_below_signal"] = macd_line < signal_line
    return out


def atr_indicator(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range."""
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


def compute_all(df: pd.DataFrame,
                macd_fast: int = 12, macd_slow: int = 26,
                macd_signal: int = 9, sma_long: int = 200,
                atr_period: int = 14) -> pd.DataFrame:
    """Aplica TODOS los indicadores al DataFrame OHLCV."""
    df = df.copy()

    ha = heikin_ashi(df)
    for col in ha.columns:
        df[col] = ha[col]

    df["sma_200"] = _sma(df["close"], sma_long)
    df["ha_sma_200"] = _sma(df["ha_close"], sma_long)

    m = macd_indicator(df["close"], macd_fast, macd_slow, macd_signal)
    for col in m.columns:
        df[col] = m[col]

    m_ha = macd_indicator(df["ha_close"], macd_fast, macd_slow, macd_signal)
    for col in m_ha.columns:
        df[f"ha_{col}"] = m_ha[col]

    df["atr"] = atr_indicator(df, atr_period)
    df["ha_trend_up"] = df["ha_bull"] & df["ha_no_low_wick"]
    df["ha_trend_dn"] = df["ha_bear"] & df["ha_no_high_wick"]
    df["above_sma200"] = df["close"] > df["sma_200"]
    df["below_sma200"] = df["close"] < df["sma_200"]
    df["ha_above_sma200"] = df["ha_close"] > df["ha_sma_200"]
    df["ha_below_sma200"] = df["ha_close"] < df["ha_sma_200"]
    return df

# ──────────────────────────────────────────────────────────────
# SECTION 4 — STRATEGY
# ──────────────────────────────────────────────────────────────
Signal = Literal["long", "short", "none"]


@dataclass
class SignalResult:
    signal: Signal = "none"
    entry_price: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    reason: str = ""


class MoneyZGStrategy:
    """
    Estrategia exacta del video MoneyZG:
      LONG  = HA_close > SMA200 + MACD cruza arriba + vela HA verde
      SHORT = HA_close < SMA200 + MACD cruza abajo + vela HA roja
      SL    = 0.1% fijo
      TP    = 0.15% fijo (1.5x SL)
    """

    def __init__(self):
        self.daily_trades = 0
        self.current_day = None
        log.info("Strategy: MACD(%d,%d,%d) SMA(%d) SL=%.1f%% TP=%.2f%% (RR 1:%.1f)",
                 MACD_FAST, MACD_SLOW, MACD_SIGNAL, SMA_LONG,
                 SL_PCT * 100, TP_MIN_PCT * 100, TP_MIN_PCT / SL_PCT)

    def reset_day(self, timestamp: int):
        day = timestamp // 86400
        if day != self.current_day:
            self.current_day = day
            self.daily_trades = 0

    def evaluate(self, row: dict) -> SignalResult:
        res = SignalResult()

        # LONG
        if (row.get("ha_above_sma200") and
                row.get("macd_cross_above") and
                row.get("ha_bull")):
            entry = float(row.get("ha_close") or row["close"])
            res.signal = "long"
            res.entry_price = entry
            res.stop_loss = entry * (1 - SL_PCT)
            res.take_profit = entry * (1 + TP_MIN_PCT)
            res.reason = "HA>SMA200 MACDdotGREEN HAverde"
            return res

        # SHORT
        if (row.get("ha_below_sma200") and
                row.get("macd_cross_below") and
                row.get("ha_bear")):
            entry = float(row.get("ha_close") or row["close"])
            res.signal = "short"
            res.entry_price = entry
            res.stop_loss = entry * (1 + SL_PCT)
            res.take_profit = entry * (1 - TP_MIN_PCT)
            res.reason = "HA<SMA200 MACDdotRED HAroja"
            return res

        return res

    def prepare(self, df):
        """Alias: compute_all with strategy params."""
        return compute_all(df, MACD_FAST, MACD_SLOW, MACD_SIGNAL, SMA_LONG)

# ──────────────────────────────────────────────────────────────
# SECTION 5 — EXCHANGE (async Bitget via ccxt.pro)
# ──────────────────────────────────────────────────────────────
class Bitget:
    """Async wrapper around ccxt.pro.bitget with rate-limit guard."""

    def __init__(self):
        self._ex = None
        self._last = 0.0
        self._gap = 0.15

    async def __aenter__(self):
        await self.init()
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def init(self):
        if self._ex:
            return
        import ccxt.pro as ccxtpro
        self._ex = ccxtpro.bitget({
            "apiKey": BITGET_API_KEY,
            "secret": BITGET_SECRET_KEY,
            "password": BITGET_PASSPHRASE,
            "enableRateLimit": True,
            "options": {"defaultType": "swap", "sandbox": PAPER_TRADE},
        })
        if PAPER_TRADE:
            log.info("PAPER TRADE MODE - no real orders")
        await self._throttle()
        await self._ex.load_markets()
        log.info("Bitget ready - %d markets", len(self._ex.markets))

    async def close(self):
        if self._ex:
            await self._ex.close()
            self._ex = None

    async def _throttle(self):
        import time as ttime
        elapsed = ttime.time() - self._last
        if elapsed < self._gap:
            await asyncio.sleep(self._gap - elapsed)
        self._last = ttime.time()

    async def ohlcv(self, symbol: str, tf: str = "1m", limit: int = 500) -> list:
        await self._throttle()
        try:
            return await self._ex.fetch_ohlcv(symbol, tf, limit=limit)
        except Exception as e:
            log.error("ohlcv %s %s: %s", symbol, tf, e)
            return []

    async def tickers(self) -> dict:
        await self._throttle()
        try:
            return await self._ex.fetch_tickers()
        except Exception as e:
            log.error("tickers: %s", e)
            return {}

    async def balance(self) -> dict:
        await self._throttle()
        try:
            return await self._ex.fetch_balance()
        except Exception as e:
            log.error("balance: %s", e)
            return {}

    async def set_leverage(self, symbol: str, lev: float = LEVERAGE):
        await self._throttle()
        try:
            await self._ex.set_leverage(lev, symbol)
        except Exception as e:
            log.warning("set_leverage %s: %s", symbol, e)

    async def market_order(self, symbol: str, side: str, amount: float,
                           reduce: bool = False, params: dict | None = None) -> dict:
        await self._throttle()
        if params is None:
            params = {}
        if reduce:
            params["reduceOnly"] = True
        if PAPER_TRADE:
            log.info("[PAPER] %s %s %.6f %s", side.upper(), symbol, amount, params)
            return {"id": f"paper_{int(time.time())}", "status": "closed",
                    "symbol": symbol, "side": side, "amount": amount}
        try:
            order = await self._ex.create_order(symbol, "market", side, amount, params=params)
            log.info("ORDER %s %s %.6f id=%s", side, symbol, amount, order.get("id"))
            return order
        except Exception as e:
            log.error("order %s %s: %s", side, symbol, e)
            return {}

    async def positions(self, symbols: list[str] | None = None) -> list[dict]:
        await self._throttle()
        try:
            return await self._ex.fetch_positions(symbols)
        except Exception as e:
            log.error("positions: %s", e)
            return []

# ──────────────────────────────────────────────────────────────
# SECTION 6 — SCREENER
# ──────────────────────────────────────────────────────────────
async def get_top_symbols(exchange: Bitget) -> list[tuple[str, float]]:
    """Return [(symbol, volume_usd), ...] sorted desc."""
    tickers = await exchange.tickers()
    candidates = []
    for sym, t in tickers.items():
        if not sym.endswith(f"/{QUOTE_CURRENCY}:{QUOTE_CURRENCY}"):
            continue
        vol = (float(t.get("quoteVolume") or 0) or
               float(t.get("baseVolume") or 0) * float(t.get("last") or 0))
        if vol >= MIN_VOLUME_USD:
            candidates.append((sym, vol))
    candidates.sort(key=lambda x: x[1], reverse=True)
    top = candidates[:TOP_N]
    log.info("Screener: TOP %d - %s ...", len(top),
             ", ".join(s for s, _ in top[:5]))
    return top


async def top_symbols_list(exchange: Bitget) -> list[str]:
    top = [s for s, _ in await get_top_symbols(exchange)]
    # Filtrar por whitelist si está definida
    if SYMBOL_WHITELIST:
        filtered = [s for s in top if s in SYMBOL_WHITELIST]
        if filtered:
            log.info("Whitelist filter: %d/%d symbols kept", len(filtered), len(top))
            return filtered
    return top

# ──────────────────────────────────────────────────────────────
# SECTION 7 — BACKTESTER
# ──────────────────────────────────────────────────────────────
LOOKAHEAD_MAX = 500  # max candles to scan per trade (~8h at 1m)


def _to_ts(val, fallback: int = 0) -> int:
    """Convierte timestamp a segundos UNIX (acepta Timestamp, ms, s, o int)."""
    if isinstance(val, pd.Timestamp):
        return int(val.timestamp())
    if isinstance(val, (int, float, np.integer, np.floating)):
        val = int(val)
        return val // 1000 if val > 1e12 else int(val)
    return fallback


@dataclass
class Trade:
    entry_time: int = 0
    exit_time: int = 0
    side: str = ""
    entry_price: float = 0.0
    exit_price: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    pnl_pct: float = 0.0
    pnl_usd: float = 0.0
    reason: str = ""
    status: str = "open"  # "win" | "loss" | "open"


@dataclass
class BacktestResult:
    trades: list[Trade] = field(default_factory=list)
    total_return_pct: float = 0.0
    sharpe: float = 0.0
    max_dd: float = 0.0
    profit_factor: float = 0.0
    win_rate: float = 0.0
    total_trades: int = 0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    expectancy: float = 0.0


class Backtester:
    def __init__(self, capital: float = 10_000.0):
        self.capital = capital
        self.strategy = MoneyZGStrategy()

    def run(self, df: pd.DataFrame) -> BacktestResult:
        res = BacktestResult()
        df = df.reset_index(drop=True)
        n = len(df)

        for i in range(SMA_LONG, n):
            row = df.iloc[i].to_dict()
            ts = _to_ts(row.get("timestamp"), i)
            self.strategy.reset_day(ts)
            sig = self.strategy.evaluate(row)
            if sig.signal == "none":
                continue
            if self.strategy.daily_trades >= MAX_TRADES_DAY:
                continue

            t = Trade(
                entry_time=ts, side=sig.signal,
                entry_price=sig.entry_price,
                stop_loss=sig.stop_loss,
                take_profit=sig.take_profit,
                reason=sig.reason,
            )

            exit_idx, exit_price, status = self._simulate(df, i, sig)
            if exit_idx is not None:
                t.exit_time = _to_ts(df.iloc[exit_idx].get("timestamp"), exit_idx)
                t.exit_price = exit_price
                t.status = status
                if sig.signal == "long":
                    t.pnl_pct = (exit_price - sig.entry_price) / sig.entry_price * 100
                else:
                    t.pnl_pct = (sig.entry_price - exit_price) / sig.entry_price * 100
                t.pnl_usd = self.capital * (t.pnl_pct / 100)
                self.capital += t.pnl_usd
                self.strategy.daily_trades += 1
                res.trades.append(t)

        self._metrics(res)
        return res

    def _simulate(self, df, entry_idx, sig):
        for j in range(entry_idx + 1, min(entry_idx + LOOKAHEAD_MAX, len(df))):
            c = df.iloc[j]
            high, low = float(c["high"]), float(c["low"])
            if sig.signal == "long":
                if low <= sig.stop_loss:
                    return j, sig.stop_loss, "loss"
                if high >= sig.take_profit:
                    return j, sig.take_profit, "win"
            else:
                if high >= sig.stop_loss:
                    return j, sig.stop_loss, "loss"
                if low <= sig.take_profit:
                    return j, sig.take_profit, "win"
        return None, None, "open"

    def _metrics(self, res: BacktestResult):
        trades = res.trades
        res.total_trades = len(trades)
        if not trades:
            return

        wins = [t for t in trades if t.status == "win"]
        losses = [t for t in trades if t.status == "loss"]
        res.win_rate = len(wins) / len(trades) * 100
        res.avg_win = sum(t.pnl_pct for t in wins) / len(wins) if wins else 0
        res.avg_loss = sum(t.pnl_pct for t in losses) / len(losses) if losses else 0

        gp = sum(t.pnl_usd for t in wins) if wins else 0
        gl = abs(sum(t.pnl_usd for t in losses)) if losses else 0
        res.profit_factor = gp / gl if gl > 0 else float("inf")
        start_cap = 10_000.0
        res.total_return_pct = (sum(t.pnl_usd for t in trades) / start_cap) * 100
        res.expectancy = (res.win_rate / 100 * res.avg_win -
                          (1 - res.win_rate / 100) * abs(res.avg_loss))

        eq = [start_cap]
        for t in trades:
            eq.append(eq[-1] + t.pnl_usd)
        s = pd.Series(eq)
        peak = s.expanding().max()
        dd = (s - peak) / peak
        res.max_dd = dd.min() * 100

        if len(eq) > 1:
            rets = s.pct_change().dropna()
            if rets.std() > 0:
                res.sharpe = rets.mean() / rets.std() * math.sqrt(365)

    @staticmethod
    def print_report(res: BacktestResult):
        sep = "=" * 50
        print(f"\n{sep}")
        print(f"  MONEYZG SCALPING - BACKTEST REPORT")
        print(f"{sep}")
        print(f"  Total Trades      : {res.total_trades}")
        print(f"  Win Rate          : {res.win_rate:.1f}%")
        print(f"  Profit Factor     : {res.profit_factor:.2f}")
        print(f"  Sharpe Ratio      : {res.sharpe:.2f}")
        print(f"  Max Drawdown      : {res.max_dd:.2f}%")
        print(f"  Total Return      : {res.total_return_pct:.2f}%")
        print(f"  Avg Win           : {res.avg_win:.2f}%")
        print(f"  Avg Loss          : {res.avg_loss:.2f}%")
        print(f"  Expectancy        : {res.expectancy:.2f}%")
        print(f"{sep}")

# ──────────────────────────────────────────────────────────────
# SECTION 8 — BOTZG MAIN CLASS
# ──────────────────────────────────────────────────────────────
class BOTZG:
    """Main bot: live(), backtest(), screen(), web_worker()."""

    def __init__(self):
        self.strategy = MoneyZGStrategy()
        self._running = False

    # ── LIVE ─────────────────────────────────────────────
    async def live(self):
        log.info("BOTZG live starting - paper=%s timeframe=%s poll=%ds",
                 PAPER_TRADE, ENTRY_TIMEFRAME, POLL_INTERVAL_SEC)
        async with Bitget() as ex:
            self._running = True
            while self._running:
                try:
                    await self._tick(ex)
                except Exception as e:
                    log.error("Tick error: %s", e, exc_info=True)
                await asyncio.sleep(POLL_INTERVAL_SEC)

    def stop(self):
        self._running = False

    async def _tick(self, ex: Bitget):
        syms = await top_symbols_list(ex)
        if not syms:
            return

        positions = await ex.positions(syms)
        active = {p["symbol"] for p in positions
                  if float(p.get("contracts", 0) or 0) > 0}

        for sym in syms:
            if sym in active:
                continue
            if len(active) >= MAX_POSITIONS:
                break
            await self._eval_symbol(ex, sym)

    async def _eval_symbol(self, ex: Bitget, symbol: str):
        ohlcv = await ex.ohlcv(symbol, ENTRY_TIMEFRAME, limit=250)
        if not ohlcv or len(ohlcv) < SMA_LONG:
            return

        df = pd.DataFrame(ohlcv, columns=OHLCV_COLS)
        df = compute_all(df)
        last = df.iloc[-1].to_dict()

        sig = self.strategy.evaluate(last)
        if sig.signal == "none":
            return

        log.info("SIGNAL %s - %s  entry=%.6f  SL=%.6f  TP=%.6f  (%s)",
                 sig.signal.upper(), symbol, sig.entry_price,
                 sig.stop_loss, sig.take_profit, sig.reason)

        bal = await ex.balance()
        usdt = float(bal.get("USDT", {}).get("free", 0))
        if usdt <= 0:
            log.warning("No USDT balance")
            return

        risk_amt = usdt * RISK_PCT
        diff = abs(sig.entry_price - sig.stop_loss)
        if diff <= 0:
            return
        qty = risk_amt / diff

        await ex.set_leverage(symbol, LEVERAGE)

        side = "buy" if sig.signal == "long" else "sell"
        sl_trigger = sig.stop_loss
        tp_trigger = sig.take_profit

        order = await ex.market_order(symbol, side, qty, params={
            "stopLoss": {"triggerPrice": sl_trigger},
            "takeProfit": {"triggerPrice": tp_trigger},
        })
        if order.get("id"):
            log.info("ORDER PLACED %s %s qty=%.6f id=%s",
                     side.upper(), symbol, qty, order["id"])

    # ── BACKTEST ─────────────────────────────────────────
    async def backtest(self):
        log.info("=" * 50)
        log.info("BACKTEST - %s  %d days  TOP %d symbols",
                 BACKTEST_TIMEFRAME, BACKTEST_DAYS, TOP_N)

        os.makedirs(DATA_DIR, exist_ok=True)
        async with Bitget() as ex:
            top = await top_symbols_list(ex)
            if not top:
                log.warning("No symbols from screener")
                return

            # Download/cache data
            now_ms = int(time.time() * 1000)
            since = now_ms - BACKTEST_DAYS * 24 * 60 * 60 * 1000

            for sym in top:
                fname = sym.replace("/", "_").replace(":", "_") + ".csv"
                fpath = os.path.join(DATA_DIR, fname)
                if os.path.exists(fpath):
                    df = pd.read_csv(fpath)
                    if len(df) >= BACKTEST_DAYS * 1000:
                        log.info("Cache OK %s (%d rows)", fname, len(df))
                        continue

                all_ohlcv = []
                s = since
                limit = 1000
                while s < now_ms:
                    ohlcv = await ex.ohlcv(sym, BACKTEST_TIMEFRAME, limit=limit)
                    if not ohlcv:
                        break
                    all_ohlcv.extend(ohlcv)
                    s = ohlcv[-1][0] + 60000
                    if len(all_ohlcv) > BACKTEST_DAYS * 1440:
                        break
                if all_ohlcv:
                    pd.DataFrame(all_ohlcv, columns=OHLCV_COLS).to_csv(fpath, index=False)
                    log.info("Saved %s - %d rows", fname, len(all_ohlcv))

        # Run backtest on each cached file
        results = []
        for fname in os.listdir(DATA_DIR):
            if not fname.endswith(".csv"):
                continue
            df = pd.read_csv(os.path.join(DATA_DIR, fname))
            if len(df) < SMA_LONG + 50:
                continue
            df = compute_all(df)
            bt = Backtester()
            r = bt.run(df)

            sym = fname.replace("_", "/").replace(".csv", "")
            sym = sym.replace("/USDT/USDT", "/USDT:USDT")
            if r.total_trades > 0:
                results.append({
                    "symbol": sym,
                    "trades": r.total_trades,
                    "win_rate": round(r.win_rate, 1),
                    "profit_factor": round(r.profit_factor, 2),
                    "sharpe": round(r.sharpe, 2),
                    "max_dd": round(r.max_dd, 1),
                    "return_pct": round(r.total_return_pct, 1),
                })

        # Print summary
        summary = pd.DataFrame(results).sort_values("sharpe", ascending=False)
        print("\n" + "=" * 80)
        print("  BOTZG BACKTEST SUMMARY")
        print("=" * 80)
        if not summary.empty:
            print(summary.to_string(index=False))
            print("=" * 80)
            print(f"  Symbols: {len(results)}")
            print(f"  Avg Sharpe: {summary['sharpe'].mean():.2f}")
            print(f"  Avg Win Rate: {summary['win_rate'].mean():.1f}%")
            print(f"  Avg PF: {summary['profit_factor'].mean():.2f}")
            print(f"  Total PnL sum: {summary['return_pct'].sum():.1f}%")
        else:
            print("  No trades generated.")
        print("=" * 80)

        spath = os.path.join(BASE_DIR, "botzg_results.json")
        with open(spath, "w") as f:
            json.dump(results, f, indent=2)
        log.info("Results saved to %s", spath)

    # ── SCREEN ───────────────────────────────────────────
    async def screen(self):
        async with Bitget() as ex:
            top = await get_top_symbols(ex)
        print(f"\n{'Symbol':<25} {'24h Volume (USDT)':<20}")
        print("-" * 45)
        for sym, vol in top[:30]:
            print(f"{sym:<25} {vol:<20,.0f}")
        print(f"\nTotal: {len(top)} symbols")

    # ── WEB WORKER (runs in thread) ──────────────────────
    @staticmethod
    def web_worker():
        """Run bot.live() in a new event loop (for Flask thread)."""
        bot = BOTZG()
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(bot.live())
        except Exception as e:
            log.error("Web worker error: %s", e, exc_info=True)
        finally:
            loop.close()


# ──────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────
def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    cmd = sys.argv[1]
    bot = BOTZG()

    try:
        if cmd == "live":
            validate_config()
            asyncio.run(bot.live())
        elif cmd == "backtest":
            warnings.filterwarnings('ignore')
            try:
                validate_config()
            except ValueError:
                pass
            asyncio.run(bot.backtest())
        elif cmd == "screen":
            warnings.filterwarnings('ignore')
            try:
                validate_config()
            except ValueError:
                pass
            asyncio.run(bot.screen())
        elif cmd == "web":
            # Import here to avoid needing Flask if not used
            from flask import Flask
            log.info("Starting Flask web service...")
            _run_web()
        else:
            print(f"Unknown command: {cmd}")
            print("Usage: python botzg.py live|backtest|screen|web")
    except KeyboardInterrupt:
        bot.stop()
        log.info("BOTZG stopped")


def _run_web():
    """Start Flask server with bot in background thread."""
    import flask
    from flask import Flask, jsonify

    app = Flask(__name__)
    bot_active = [False]
    bot_started_at = [None]

    @app.route("/")
    def index():
        return "BOTZG - MoneyZG Scalping Bot - online", 200

    @app.route("/health")
    def health():
        uptime = round(time.time() - bot_started_at[0], 1) if bot_started_at[0] else 0
        return jsonify({
            "status": "running",
            "bot": "botzg",
            "active": bot_active[0],
            "uptime": uptime,
        })

    @app.route("/status")
    def status():
        uptime = round(time.time() - bot_started_at[0], 1) if bot_started_at[0] else 0
        return jsonify({
            "bot_active": bot_active[0],
            "uptime_seconds": uptime,
        })

    def _run_bot():
        bot_active[0] = True
        bot_started_at[0] = time.time()
        log.info("BOTZG worker started")
        BOTZG.web_worker()

    t = threading.Thread(target=_run_bot, daemon=True, name="BOTZG")
    t.start()

    port = int(os.environ.get("PORT", 8000))
    log.info("Flask server on 0.0.0.0:%d", port)
    app.run(host="0.0.0.0", port=port, debug=False)


if __name__ == "__main__":
    main()
