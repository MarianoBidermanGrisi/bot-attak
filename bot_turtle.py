"""
bot_turtle.py — Core Turtle Trading Bot Engine (4H Donchian Breakout)
====================================================================
Optimized params: SL=5.0x ATR, TS=4.0x ATR, DC=10/5, TO=96h, MaxPos=3
Uses: CCXT async, asyncio, pandas, numpy

This file contains the core bot logic. Imported by bot_web_service.py for Render.
"""
import os, sys, asyncio, logging, json, time
from datetime import datetime, timedelta

import pandas as pd
import numpy as np
import ccxt.async_support as ccxt

log = logging.getLogger('turtle_bot')

# ==========================================
# CONFIG
# ==========================================
# API keys (solo desde variables de entorno por seguridad)
API_KEY      = os.environ.get('BITGET_API_KEY', '')
API_SECRET   = os.environ.get('BITGET_API_SECRET', '')
API_PASSWORD = os.environ.get('BITGET_API_PASSWORD', '')

# Parámetros fijos del bot (hardcodeados, no requieren env vars)
TOP_N        = 27
MAX_POS      = 3
RISK_PCT     = 0.02
LEVERAGE     = 10
MIN_NOTIONAL = 5.0       # USDT mínimo por orden en Bitget
TIMEOUT_H    = 96
COOLDOWN_H   = 4
FEE_RATE     = 0.0006
CHECK_INTERVAL_MIN = 15
CANDLES_NEED = 100

# Turtle optimized params (fijos)
SL_MULT      = 5.0
TS_MULT      = 4.0
DC_IN        = 10
DC_OUT       = 5
ATR_PER      = 14
ST_PER       = 10
ST_MULT      = 3.0

# ==========================================
# INDICADORES (vectorizados)
# ==========================================
def calc_atr(high, low, close, period=ATR_PER):
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs()
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()

def calc_donchian(close, period):
    upper = close.rolling(period).max()
    lower = close.rolling(period).min()
    return upper, lower

def calc_supertrend(high, low, close, period=ST_PER, mult=ST_MULT):
    hl = (high + low) / 2
    atr_ = calc_atr(high, low, close, period)
    upper = hl + mult * atr_
    lower = hl - mult * atr_
    direction = np.ones(len(close))
    for i in range(period, len(close)):
        if close.iloc[i] > lower.iloc[i-1]:
            direction[i] = 1 if direction[i-1] == 1 else (
                1 if close.iloc[i] > upper.iloc[i-1] else -1
            )
        else:
            direction[i] = -1 if direction[i-1] == -1 else (
                -1 if close.iloc[i] < lower.iloc[i-1] else 1
            )
    return pd.Series(direction, index=close.index)

def prepare_4h_df(ohlcv_list):
    if not ohlcv_list or len(ohlcv_list) < CANDLES_NEED:
        return None
    df = pd.DataFrame(ohlcv_list, columns=['timestamp','open','high','low','close','volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df.set_index('timestamp', inplace=True)
    df = df.resample('4h').agg({
        'open':'first','high':'max','low':'min','close':'last','volume':'sum'
    }).dropna()
    if len(df) < CANDLES_NEED:
        return None
    df['atr'] = calc_atr(df['high'], df['low'], df['close'], ATR_PER)
    df['dc_entry_upper'], df['dc_entry_lower'] = calc_donchian(df['close'], DC_IN)
    df['dc_exit_upper'], df['dc_exit_lower'] = calc_donchian(df['close'], DC_OUT)
    df['st_dir'] = calc_supertrend(df['high'], df['low'], df['close'], ST_PER, ST_MULT)
    df['arrow'] = df['st_dir'] == 1
    df['above_green'] = df['close'] > df['dc_entry_upper']
    df['below_green'] = df['close'] < df['dc_entry_lower']
    df['fresh_breakout_up'] = df['above_green'] & ~df['above_green'].shift(1).fillna(False)
    df['fresh_breakout_dn'] = df['below_green'] & ~df['below_green'].shift(1).fillna(False)
    df.dropna(inplace=True)
    return df

def evaluate_signal(df):
    if df is None or len(df) < 5:
        return None, {'razon': 'datos_insuficientes'}
    last = df.iloc[-1]
    above = bool(last['above_green'])
    below = bool(last['below_green'])
    fresh_up = bool(last['fresh_breakout_up'])
    fresh_dn = bool(last['fresh_breakout_dn'])
    arrow = bool(last['arrow'])
    st_dir = int(last['st_dir'])
    score_long = 0
    score_short = 0
    razones_l, razones_s = [], []
    if above and arrow:
        if fresh_up:
            score_long = 4; razones_l.append('fresh_breakout_arrow')
        else:
            score_long = 3; razones_l.append('above_green_arrow')
    elif above and st_dir == 1:
        score_long = 2; razones_l.append('above_green_st')
    elif above:
        score_long = 1; razones_l.append('above_green_only')
    if below and not arrow and st_dir == -1:
        if fresh_dn:
            score_short = 4; razones_s.append('fresh_breakdown_anti')
        else:
            score_short = 3; razones_s.append('below_green_anti')
    elif below and st_dir == -1:
        score_short = 2; razones_s.append('below_green_st')
    elif below:
        score_short = 1; razones_s.append('below_green_only')
    det = {
        'price': round(last['close'], 8),
        'atr': round(last['atr'], 8),
        'dc_upper': round(last['dc_entry_upper'], 8),
        'dc_lower': round(last['dc_entry_lower'], 8),
        'score_long': score_long,
        'score_short': score_short,
        'razones': razones_l if score_long >= score_short else razones_s,
        'arrow': arrow,
    }
    if score_long >= 2 and score_long >= score_short:
        return 'long', det
    if score_short >= 2 and score_short > score_long:
        return 'short', det
    return None, det

# ==========================================
# EXCHANGE WRAPPER
# ==========================================
class BitgetExchange:
    def __init__(self):
        self.ex = None
        self._last_req = 0
        self._min_interval = 0.15

    async def _throttle(self):
        elapsed = time.time() - self._last_req
        if elapsed < self._min_interval:
            await asyncio.sleep(self._min_interval - elapsed)
        self._last_req = time.time()

    async def connect(self):
        self.ex = ccxt.bitget({
            'apiKey': API_KEY,
            'secret': API_SECRET,
            'password': API_PASSWORD,
            'enableRateLimit': True,
            'options': {'defaultType': 'swap'},
        })
        await self.ex.load_markets()
        log.info("Conectado a Bitget OK")

    async def close(self):
        if self.ex:
            await self.ex.close()

    async def fetch_top_symbols(self, n=TOP_N):
        await self._throttle()
        try:
            tickers = await self.ex.fetch_tickers()
            usdt_syms = []
            for sym, t in tickers.items():
                if sym.endswith('/USDT:USDT') and t.get('quoteVolume', 0) > 0:
                    usdt_syms.append((sym, t['quoteVolume']))
            usdt_syms.sort(key=lambda x: -x[1])
            top = [s[0] for s in usdt_syms[:n]]
            log.info(f"Top {len(top)} símbolos por volumen 24h")
            return top
        except Exception as e:
            log.error(f"fetch_top_symbols error: {e}")
            return []

    async def fetch_ohlcv_4h(self, symbol, limit=CANDLES_NEED):
        await self._throttle()
        try:
            return await self.ex.fetch_ohlcv(symbol, '4h', limit=limit)
        except Exception as e:
            log.warning(f"fetch_ohlcv {symbol}: {e}")
            return None

    async def get_usdt_balance(self):
        await self._throttle()
        try:
            balance = await self.ex.fetch_balance()
            usdt = balance.get('USDT', {}).get('free', 0)
            return float(usdt)
        except Exception as e:
            log.error(f"get_usdt_balance error: {e}")
            return 0.0

    async def set_leverage(self, symbol, leverage=LEVERAGE):
        await self._throttle()
        try:
            await self.ex.set_leverage(leverage, symbol)
        except Exception as e:
            log.debug(f"set_leverage {symbol}: {e}")

    def _amount_to_precision(self, symbol, amount):
        """Redondea cantidad al tamaño de contrato válido para el símbolo."""
        try:
            market = self.ex.market(symbol)
            precision = market.get('precision', {}).get('amount', 8)
            contract_size = float(market.get('contractSize', 1))
            # Convertir a contratos enteros
            contracts = amount / contract_size
            factor = 10 ** precision
            contracts = int(contracts * factor) / factor
            if contracts <= 0:
                contracts = 1 / factor  # mínimo 1 contracto si el cálculo da 0
            return max(contracts, market.get('limits', {}).get('amount', {}).get('min', 0.001))
        except Exception as e:
            log.warning(f"_amount_to_precision {symbol}: {e}, usando raw={amount}")
            return amount

    async def market_order(self, symbol, side, amount, reduce=False):
        await self._throttle()
        try:
            qty = self._amount_to_precision(symbol, amount)
            params = {'reduceOnly': reduce} if reduce else {}
            return await self.ex.create_market_order(symbol, side, qty, None, params)
        except Exception as e:
            log.error(f"market_order {symbol} {side} amount={amount}: {e}")
            return None

    async def place_stop_order(self, symbol, side, amount, stop_price, reduce=False):
        await self._throttle()
        try:
            qty = self._amount_to_precision(symbol, amount)
            params = {'stopPrice': stop_price, 'reduceOnly': reduce}
            return await self.ex.create_order(symbol, 'stop', side, qty, None, stop_price, params)
        except Exception as e:
            log.error(f"stop_order {symbol} {side} @{stop_price}: {e}")
            return None

    async def cancel_order(self, symbol, order_id):
        await self._throttle()
        try:
            await self.ex.cancel_order(order_id, symbol)
            return True
        except Exception as e:
            log.warning(f"cancel_order {order_id}: {e}")
            return False

    async def fetch_open_orders(self, symbol=None):
        await self._throttle()
        try:
            return await self.ex.fetch_open_orders(symbol)
        except Exception as e:
            log.error(f"fetch_open_orders: {e}")
            return []

    async def fetch_positions(self):
        await self._throttle()
        try:
            positions = await self.ex.fetch_positions()
            result = {}
            for p in positions:
                size = float(p.get('contracts', 0))
                if size != 0:
                    result[p['symbol']] = {
                        'side': 'long' if size > 0 else 'short',
                        'size': abs(size),
                        'entry_price': float(p.get('entryPrice', 0)),
                        'unrealized_pnl': float(p.get('unrealizedPnl', 0)),
                        'liquidation': float(p.get('liquidationPrice', 0)),
                    }
            return result
        except Exception as e:
            log.error(f"fetch_positions: {e}")
            return {}

# ==========================================
# POSITION TRACKER
# ==========================================
class Tracker:
    def __init__(self):
        self.positions = {}
        self.cooldowns = {}
        self.trades_log = []

    def in_cooldown(self, symbol):
        if symbol not in self.cooldowns:
            return False
        return datetime.now() < self.cooldowns[symbol]

    def add_position(self, symbol, side, entry_price, size_usdt, atr, arrow=True):
        sl_price = entry_price - atr * SL_MULT if side == 'long' else entry_price + atr * SL_MULT
        self.positions[symbol] = {
            'side': side,
            'entry_price': entry_price,
            'size_usdt': size_usdt,
            'atr_entry': atr,
            'sl': sl_price,
            'peak_price': entry_price,
            'entry_time': datetime.now(),
            'entry_arrow': arrow,
            'exit_orders': [],
        }

    def update_trail(self, symbol, current_price, atr):
        pos = self.positions.get(symbol)
        if not pos:
            return False
        if pos['side'] == 'long':
            pos['peak_price'] = max(pos['peak_price'], current_price)
            new_sl = pos['peak_price'] - atr * TS_MULT
            if new_sl > pos['sl']:
                pos['sl'] = new_sl
                return True
        else:
            pos['peak_price'] = min(pos['peak_price'], current_price)
            new_sl = pos['peak_price'] + atr * TS_MULT
            if new_sl < pos['sl']:
                pos['sl'] = new_sl
                return True
        return False

    def close_position(self, symbol, reason, exit_price, close_pnl=None):
        pos = self.positions.pop(symbol, None)
        if not pos:
            return
        pnl = close_pnl if close_pnl is not None else 0
        self.trades_log.append({
            'symbol': symbol, 'side': pos['side'],
            'entry': pos['entry_price'], 'exit': exit_price,
            'pnl': pnl, 'reason': reason,
            'entry_time': pos['entry_time'].isoformat(),
            'exit_time': datetime.now().isoformat(),
        })
        log.info(f"CERRAR {symbol} {pos['side'].upper()} reason={reason} "
                 f"entry={pos['entry_price']:.8f} exit={exit_price:.8f} pnl={pnl:.2f}")
        if pnl < 0:
            self.cooldowns[symbol] = datetime.now() + timedelta(hours=COOLDOWN_H)

# ==========================================
# MAIN BOT
# ==========================================
class TurtleBot:
    def __init__(self, on_trade=None):
        self.ex = BitgetExchange()
        self.tracker = Tracker()
        self.symbols = []
        self.dfs = {}
        self.running = True
        self.on_trade = on_trade  # callback por si web service quiere notificar

    async def init(self):
        await self.ex.connect()
        positions = await self.ex.fetch_positions()
        for sym, p in positions.items():
            log.info(f"Posición existente: {sym} {p['side']} entry={p['entry_price']}")
        self.symbols = await self.ex.fetch_top_symbols(TOP_N)
        if not self.symbols:
            log.error("No se obtuvieron símbolos. Abortando.")
            return False
        return True

    async def update_data(self):
        tasks = [self._fetch_and_prepare(sym) for sym in self.symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        valid = 0
        for sym, df in zip(self.symbols, results):
            if isinstance(df, Exception):
                log.warning(f"Error en {sym}: {df}")
                continue
            if df is not None:
                self.dfs[sym] = df
                valid += 1
        log.info(f"DataFrames: {valid}/{len(self.symbols)}")

    async def _fetch_and_prepare(self, symbol):
        ohlcv = await self.ex.fetch_ohlcv_4h(symbol, CANDLES_NEED)
        if not ohlcv:
            return None
        return prepare_4h_df(ohlcv)

    async def scan_signals(self):
        if len(self.tracker.positions) >= MAX_POS:
            return
        balance = await self.ex.get_usdt_balance()
        candidates = []
        for sym in self.symbols:
            if sym in self.tracker.positions:
                continue
            if self.tracker.in_cooldown(sym):
                continue
            df = self.dfs.get(sym)
            if df is None or len(df) < CANDLES_NEED:
                continue
            signal, det = evaluate_signal(df)
            if signal:
                price = det['price']
                atr = det['atr']
                score = det['score_long'] if signal == 'long' else det['score_short']
                candidates.append((sym, signal, price, atr, score, det))
        if not candidates:
            return
        candidates.sort(key=lambda x: -x[4])
        for sym, side, price, atr, score, det in candidates[:MAX_POS]:
            if len(self.tracker.positions) >= MAX_POS:
                break
            # Margen dinámico: usa RISK_PCT si alcanza mín. notional; si no, fuerza el mínimo
            min_margin = MIN_NOTIONAL / LEVERAGE
            margin_raw = balance * RISK_PCT
            margin = max(margin_raw, min_margin)
            # Nunca arriesgar más del 50% del balance en 1 trade
            margin = min(margin, balance * 0.5)
            if margin != margin_raw and margin > 0:
                log.info(f"Ajuste margen {sym}: {margin_raw:.2f}→{margin:.2f} (mín {MIN_NOTIONAL}U notional)")
            if margin <= 0:
                continue
            await self.ex.set_leverage(sym, LEVERAGE)
            qty = margin * LEVERAGE / price  # en base currency (raw); market_order ajusta precisión
            order = await self.ex.market_order(sym, 'buy' if side == 'long' else 'sell', qty)
            if order:
                sl_price = price - atr * SL_MULT if side == 'long' else price + atr * SL_MULT
                sl_side = 'sell' if side == 'long' else 'buy'
                try:
                    await self.ex.place_stop_order(sym, sl_side, qty, sl_price, reduce=True)
                except Exception as e:
                    log.warning(f"No se pudo colocar SL para {sym}: {e}")
                self.tracker.add_position(sym, side, price, margin, atr, arrow=det.get('arrow', True))
                balance -= margin

    async def manage_positions(self):
        for sym in list(self.tracker.positions.keys()):
            df = self.dfs.get(sym)
            if df is None or len(df) < 3:
                continue
            last = df.iloc[-1]
            high, low, close = last['high'], last['low'], last['close']
            atr = last['atr']
            pos = self.tracker.positions[sym]
            self.tracker.update_trail(sym, close, atr)
            exit_reason = None
            exit_price = None
            if pos['side'] == 'long':
                if low <= pos['sl']:
                    exit_reason = 'sl'; exit_price = min(pos['sl'], high)
                elif close < last['dc_exit_lower']:
                    exit_reason = 'redline'; exit_price = close
                elif pos.get('entry_arrow', True) and not bool(last['arrow']):
                    exit_reason = 'arrow_rev'; exit_price = close
            else:
                if high >= pos['sl']:
                    exit_reason = 'sl'; exit_price = max(pos['sl'], low)
                elif close > last['dc_exit_upper']:
                    exit_reason = 'redline'; exit_price = close
                elif not pos.get('entry_arrow', False) and bool(last['arrow']):
                    exit_reason = 'arrow_rev'; exit_price = close
            hours = (datetime.now() - pos['entry_time']).total_seconds() / 3600
            if hours >= TIMEOUT_H and not exit_reason:
                exit_reason = 'timeout'; exit_price = close
            if exit_reason:
                side_close = 'sell' if pos['side'] == 'long' else 'buy'
                qty = pos['size_usdt'] * LEVERAGE / pos['entry_price']  # base currency; market_order ajusta
                await self.ex.market_order(sym, side_close, qty, reduce=True)
                if pos['side'] == 'long':
                    pnl_pct = (exit_price - pos['entry_price']) / pos['entry_price']
                else:
                    pnl_pct = (pos['entry_price'] - exit_price) / pos['entry_price']
                pnl = pos['size_usdt'] * pnl_pct * LEVERAGE
                self.tracker.close_position(sym, exit_reason, exit_price, pnl)
                if self.on_trade:
                    self.on_trade(self.tracker.trades_log[-1])

    async def log_status(self):
        bal = await self.ex.get_usdt_balance()
        open_syms = list(self.tracker.positions.keys())
        log.info(f"Balance=${bal:.2f} Open={len(open_syms)} "
                 f"Trades={len(self.tracker.trades_log)} "
                 f"Syms={','.join(open_syms) if open_syms else 'none'}")

    async def save_state(self):
        state = {
            'trades': self.tracker.trades_log[-200:],
            'cooldowns': {k: v.isoformat() for k, v in self.tracker.cooldowns.items()},
            'timestamp': datetime.now().isoformat(),
        }
        try:
            with open('bot_state.json', 'w') as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            log.warning(f"save_state: {e}")

    async def load_state(self):
        try:
            with open('bot_state.json') as f:
                state = json.load(f)
            self.tracker.trades_log = state.get('trades', [])
            for sym, ts_str in state.get('cooldowns', {}).items():
                self.tracker.cooldowns[sym] = datetime.fromisoformat(ts_str)
        except FileNotFoundError:
            pass
        except Exception as e:
            log.warning(f"load_state: {e}")

    async def run(self):
        log.info("=" * 60)
        log.info("  TURTLE BOT — Donchian Breakout 4H (Optimizado)")
        log.info(f"  SL={SL_MULT}x TS={TS_MULT}x DC={DC_IN}/{DC_OUT} TO={TIMEOUT_H}h")
        log.info(f"  MaxPos={MAX_POS} Risk={RISK_PCT*100:.0f}% Lev={LEVERAGE}x")
        log.info(f"  Top={TOP_N} símbolos, check cada {CHECK_INTERVAL_MIN}min")
        log.info("=" * 60)

        if not await self.init():
            return

        await self.load_state()
        await self.update_data()
        await self.log_status()

        cycle = 0
        while self.running:
            try:
                cycle += 1
                await self.update_data()
                await self.manage_positions()
                await self.scan_signals()

                if cycle % 4 == 0:
                    await self.log_status()
                    await self.save_state()

                await asyncio.sleep(CHECK_INTERVAL_MIN * 60)

            except asyncio.CancelledError:
                log.info("Bot detenido por cancelación")
                break
            except Exception as e:
                log.error(f"Error en loop: {e}", exc_info=True)
                await asyncio.sleep(60)

        await self.ex.close()
        await self.save_state()
        log.info("Bot finalizado")

    def stop(self):
        self.running = False
