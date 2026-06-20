import os
import sys
import time
import logging
import json
from datetime import datetime, timedelta

import pandas as pd
import numpy as np
import joblib
from dotenv import load_dotenv
import ccxt

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from backtest.ml.features import build_features, FEATURE_COLS, get_feature_vector
from backtest.strategies.abracadabra_v1 import compute_indicators
from backtest.optimizations.proposed_v2 import compute_sl_tp_v2

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('bot_15m.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

TF = '15m'
THRESHOLD = 0.55
MAX_POSITIONS = 3
RISK_PCT = 0.015
LEVERAGE = 20.0
MAX_SL_PCT = 0.02
RR_RATIO = 2.0
COOLDOWN_MINS = 60
CANDLES_HIST = 500
CHECK_INTERVAL_SEC = 15 * 60
MAX_POS_HOURS = 12
TOP_SYMBOLS = 30
BE_TRIGGER_PCT = 0.01
BE_OFFSET_PCT = 0.003
TRAILING_DIST_PCT = 0.005

SL_TP_PARAMS = {
    'MAX_SL_PCT': MAX_SL_PCT,
    'RR_RATIO': RR_RATIO,
    'SL_LOOKBACK': 10,
    'use_atr_sl': False,
}

class Position:
    def __init__(self, symbol, side, entry_price, entry_time, sl, tp, size_usdt):
        self.symbol = symbol
        self.side = side
        self.entry_price = entry_price
        self.entry_time = entry_time
        self.sl = sl
        self.tp = tp
        self.size_usdt = size_usdt
        self.pnl_pct = 0.0
        self.pnl_usdt = 0.0
        self.exit_price = None
        self.exit_time = None
        self.exit_reason = None
        self.be_active = False
        self.peak_price = entry_price

    def update_pnl(self, current_price):
        if self.side == 'buy':
            self.pnl_pct = (current_price - self.entry_price) / self.entry_price
        else:
            self.pnl_pct = (self.entry_price - current_price) / self.entry_price
        self.pnl_usdt = self.size_usdt * self.pnl_pct * LEVERAGE
        # Track peak for trailing
        if self.side == 'buy':
            self.peak_price = max(self.peak_price, current_price)
        else:
            self.peak_price = min(self.peak_price, current_price)

    def update_be_trail(self):
        entry = self.entry_price
        side = self.side
        peak = self.peak_price
        pnl = (peak - entry) / entry if side == 'buy' else (entry - peak) / entry
        # Breakeven
        if BE_TRIGGER_PCT > 0 and not self.be_active and pnl >= BE_TRIGGER_PCT:
            self.be_active = True
            be_sl = entry * (1 + BE_OFFSET_PCT) if side == 'buy' else entry * (1 - BE_OFFSET_PCT)
            self.sl = max(self.sl, be_sl) if side == 'buy' else min(self.sl, be_sl)
            log.info(f"BE activado {self.symbol}: SL movido a {self.sl:.4f}")
        # Trailing
        if TRAILING_DIST_PCT > 0 and self.be_active:
            new_sl = peak * (1 - TRAILING_DIST_PCT) if side == 'buy' else peak * (1 + TRAILING_DIST_PCT)
            moved = new_sl > self.sl if side == 'buy' else new_sl < self.sl
            if moved:
                self.sl = new_sl

    def check_exit(self, high, low, ts):
        if self.side == 'buy':
            if low <= self.sl:
                self.exit_price = self.sl
                self.exit_reason = 'SL'
            elif high >= self.tp:
                self.exit_price = self.tp
                self.exit_reason = 'TP'
        else:
            if high >= self.sl:
                self.exit_price = self.sl
                self.exit_reason = 'SL'
            elif low <= self.tp:
                self.exit_price = self.tp
                self.exit_reason = 'TP'
        if self.exit_price:
            self.exit_time = ts
            self.update_pnl(self.exit_price)
            return True
        return False

    def check_timeout(self, current_price, ts):
        hours = (ts - self.entry_time).total_seconds() / 3600
        if hours >= MAX_POS_HOURS and self.pnl_pct is not None and self.pnl_pct <= 0:
            self.exit_price = current_price
            self.exit_time = ts
            self.exit_reason = 'TIMEOUT'
            self.update_pnl(current_price)
            return True
        return False

    def close(self, price, ts, reason='MANUAL'):
        self.exit_price = price
        self.exit_time = ts
        self.exit_reason = reason
        self.update_pnl(price)

    def to_dict(self):
        return {
            'symbol': self.symbol, 'side': self.side,
            'entry_price': self.entry_price, 'sl': self.sl, 'tp': self.tp,
            'size_usdt': self.size_usdt, 'pnl_usdt': round(self.pnl_usdt, 2),
            'exit_reason': self.exit_reason,
            'be_active': self.be_active,
            'entry_time': self.entry_time.isoformat() if self.entry_time else None,
            'exit_time': self.exit_time.isoformat() if self.exit_time else None,
        }


class BotRF15m:
    def __init__(self):
        api_key = os.getenv('BITGET_API_KEY')
        api_secret = os.getenv('BITGET_API_SECRET')
        api_password = os.getenv('BITGET_API_PASSWORD')

        if not all([api_key, api_secret, api_password]):
            log.error("Faltan API Keys. Crear archivo .env con BITGET_API_KEY, BITGET_API_SECRET, BITGET_API_PASSWORD")
            sys.exit(1)

        self.exchange = ccxt.bitget({
            'apiKey': api_key,
            'secret': api_secret,
            'password': api_password,
            'enableRateLimit': True,
            'options': {'defaultType': 'swap'},
        })

        model_path = os.path.join(BASE_DIR, 'backtest', 'models', 'rf_15m_20x.joblib')
        if not os.path.exists(model_path):
            log.error(f"Modelo no encontrado: {model_path}")
            sys.exit(1)
        self.model = joblib.load(model_path)
        log.info(f"Modelo cargado: {model_path}")

        self.positions = []
        self.cooldowns = {}
        self.balance = 0.0
        self.trade_log = []
        self.top_symbols = []
        self.ohlcv_cache = {}

    def fetch_top_symbols(self):
        try:
            tickers = self.exchange.fetch_tickers()
            usdt_futures = []
            for sym, t in tickers.items():
                if sym.endswith('/USDT:USDT') and t.get('quoteVolume', 0) and t['quoteVolume'] > 0:
                    usdt_futures.append((sym, t['quoteVolume']))
            usdt_futures.sort(key=lambda x: -x[1])
            self.top_symbols = [s for s, _ in usdt_futures[:TOP_SYMBOLS]]
            log.info(f"Top {len(self.top_symbols)} símbolos por volumen")
            return True
        except Exception as e:
            log.error(f"Error fetching tickers: {e}")
            return False

    def fetch_candles(self, symbol):
        try:
            raw = self.exchange.fetch_ohlcv(symbol, TF, limit=CANDLES_HIST)
            df = pd.DataFrame(raw, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            return df
        except Exception as e:
            log.error(f"Error fetching {symbol}: {e}")
            return None

    def get_prediction(self, symbol):
        df = self.ohlcv_cache.get(symbol)
        if df is None or len(df) < 100:
            return None
        try:
            df_feat = compute_indicators(df)
            df_feat = build_features(df_feat)
            df_feat = df_feat.dropna(subset=FEATURE_COLS)
            if len(df_feat) == 0:
                return None
            vec = get_feature_vector(df_feat, -1, FEATURE_COLS).reshape(1, -1)
            prob = self.model.predict_proba(vec)[0, 1]
            return float(prob)
        except Exception as e:
            log.error(f"Error predicting {symbol}: {e}")
            return None

    def place_order(self, symbol, side, price):
        try:
            self.exchange.set_leverage(LEVERAGE, symbol)
            notional = self.balance * RISK_PCT * LEVERAGE
            amount_contracts = self.exchange.amount_to_precision(symbol, notional / price)
            if float(amount_contracts) <= 0:
                return None
            order = self.exchange.create_market_order(symbol, side.lower(), float(amount_contracts))
            log.info(f"ORDEN {side.upper()} {symbol}: {amount_contracts} contracts @ {price} (notional={notional:.2f})")
            return order
        except Exception as e:
            log.error(f"Error placing {side} order for {symbol}: {e}")
            return None

    def close_position(self, position):
        try:
            side = 'sell' if position.side == 'buy' else 'buy'
            notional = abs(position.size_usdt * LEVERAGE)
            amount_contracts = self.exchange.amount_to_precision(position.symbol, notional / position.entry_price)
            order = self.exchange.create_market_order(position.symbol, side, float(amount_contracts))
            exit_price = position.exit_price or self.exchange.fetch_ticker(position.symbol)['last']
            position.close(exit_price, datetime.now(), position.exit_reason or 'MANUAL')
            self.balance += position.pnl_usdt
            if position.pnl_usdt < 0:
                self.cooldowns[position.symbol] = datetime.now() + timedelta(minutes=COOLDOWN_MINS)
            self.trade_log.append(position.to_dict())
            log.info(f"CIERRE {position.exit_reason} {position.symbol}: PnL={position.pnl_usdt:.2f} USDT")
            return True
        except Exception as e:
            log.error(f"Error closing {position.symbol}: {e}")
            return False

    def update_balance(self):
        try:
            balance_info = self.exchange.fetch_balance()
            self.balance = float(balance_info.get('USDT', {}).get('free', 0))
            return True
        except Exception as e:
            log.error(f"Error fetching balance: {e}")
            return False

    def check_positions(self):
        still_open = []
        for pos in self.positions:
            try:
                ticker = self.exchange.fetch_ticker(pos.symbol)
                price = ticker['last']
                high = ticker['high'] or price
                low = ticker['low'] or price
                ts = datetime.now()

                pos.update_pnl(price)
                pos.update_be_trail()
                pos.check_timeout(price, ts)
                if pos.check_exit(high, low, ts):
                    self.close_position(pos)
                    continue
                still_open.append(pos)
            except Exception as e:
                log.error(f"Error checking {pos.symbol}: {e}")
                still_open.append(pos)
        self.positions = still_open

    def scan_entries(self):
        log.info(f"Escaneando {len(self.top_symbols)} símbolos...")
        entries = []

        for symbol in self.top_symbols:
            if symbol in self.cooldowns and datetime.now() < self.cooldowns[symbol]:
                continue
            if any(p.symbol == symbol for p in self.positions):
                continue
            if len(self.positions) >= MAX_POSITIONS:
                break

            if symbol not in self.ohlcv_cache:
                df = self.fetch_candles(symbol)
                if df is not None:
                    self.ohlcv_cache[symbol] = df
            else:
                new = self.fetch_candles(symbol)
                if new is not None:
                    self.ohlcv_cache[symbol] = new

            prob = self.get_prediction(symbol)
            if prob is None:
                continue

            if prob >= THRESHOLD:
                entries.append((symbol, 'buy', prob))
            elif prob <= 1 - THRESHOLD:
                entries.append((symbol, 'sell', prob))

        entries.sort(key=lambda x: -abs(x[2] - 0.5))
        taken = 0
        for symbol, side, prob in entries:
            if taken + len(self.positions) >= MAX_POSITIONS:
                break
            if any(p.symbol == symbol for p in self.positions):
                continue

            df = self.ohlcv_cache.get(symbol)
            if df is None:
                continue
            price = df['close'].iloc[-1]
            params = dict(SL_TP_PARAMS)
            sl, tp = compute_sl_tp_v2(df.copy(), len(df)-1, side, price, params)

            order = self.place_order(symbol, side, price)
            if order:
                pos = Position(symbol, side, price, datetime.now(), sl, tp, self.balance * RISK_PCT)
                self.positions.append(pos)
                taken += 1
                log.info(f"ENTRADA {side.upper()} {symbol}: entry={price:.4f} sl={sl:.4f} tp={tp:.4f} prob={prob:.4f} be={BE_TRIGGER_PCT*100}% trail={TRAILING_DIST_PCT*100}%")

    def run_cycle(self):
        log.info("=" * 60)
        log.info(f"Ciclo: {datetime.now().isoformat()}")
        log.info(f"Posiciones abiertas: {len(self.positions)}")

        self.update_balance()
        log.info(f"Balance: {self.balance:.2f} USDT")

        if not self.top_symbols:
            log.info("Obteniendo top symbols...")
            if not self.fetch_top_symbols():
                log.warning("No se pudieron obtener símbolos, reintentando...")
                return

        self.check_positions()
        self.scan_entries()

        log.info(f"Posiciones activas: {len(self.positions)}")
        for p in self.positions:
            log.info(f"  {p.symbol} {p.side.upper()} entry={p.entry_price:.4f} sl={p.sl:.4f} tp={p.tp:.4f} pnl={p.pnl_usdt:.2f}")

        if len(self.trade_log) > 1000:
            self.trade_log = self.trade_log[-500:]
        self.save_state()

    def save_state(self):
        try:
            state = {
                'timestamp': datetime.now().isoformat(),
                'balance': self.balance,
                'positions': [p.to_dict() for p in self.positions],
                'cooldowns': {k: v.isoformat() for k, v in self.cooldowns.items()},
                'last_trades': self.trade_log[-20:],
            }
            with open('bot_state.json', 'w') as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            log.error(f"Error saving state: {e}")

    def run(self):
        log.info("Iniciando Bot RF 15m v2 (optimizado)")
        log.info(f"Threshold: {THRESHOLD}, Max pos: {MAX_POSITIONS}, Leverage: {LEVERAGE}x")
        log.info(f"Risk: {RISK_PCT*100}%, SL max: {MAX_SL_PCT*100}%, RR: {RR_RATIO}:1")
        log.info(f"BE: {BE_TRIGGER_PCT*100}% (offset {BE_OFFSET_PCT*100}%), Trailing: {TRAILING_DIST_PCT*100}%")

        self.update_balance()
        log.info(f"Balance inicial: {self.balance:.2f} USDT")

        if not self.fetch_top_symbols():
            log.error("No se pudieron obtener símbolos. Verificar API keys.")
            return

        while True:
            try:
                self.run_cycle()
            except KeyboardInterrupt:
                log.info("Bot detenido por usuario")
                # Close all positions
                for pos in list(self.positions):
                    pos.exit_reason = 'SHUTDOWN'
                    self.close_position(pos)
                self.save_state()
                break
            except Exception as e:
                log.error(f"Error en ciclo: {e}", exc_info=True)

            log.info(f"Esperando {CHECK_INTERVAL_SEC}s...")
            time.sleep(CHECK_INTERVAL_SEC)


if __name__ == '__main__':
    bot = BotRF15m()
    bot.run()
