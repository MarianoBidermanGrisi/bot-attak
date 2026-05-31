import logging
import os
import sys
import time
import uuid
from datetime import datetime

import ccxt
import pandas as pd
import pandas_ta as ta
import requests

try:
    from .config import BotConfig, validate_live_env
    from .indicators import calc_two_pole, calc_zlema, calculate_all_indicators
    from .risk import build_trade_plan
    from .signals import build_signal_options, generate_signals
    from .state import StateStore
except ImportError:
    from config import BotConfig, validate_live_env
    from indicators import calc_two_pole, calc_zlema, calculate_all_indicators
    from risk import build_trade_plan
    from signals import build_signal_options, generate_signals
    from state import StateStore


LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "combined_strategy_v2.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)


class _ConsoleFilter(logging.Filter):
    """Solo muestra en consola: orden limit, posicion abierta, cierre, BE/trailing."""
    def filter(self, record):
        msg = record.getMessage()
        if not msg:
            return False
        skip = (
            ">>> CYCLE", "<<< CYCLE",
            "SCAN CYCLE", "SCAN SKIP", "SCAN STOP",
            "--- OPEN ORDERS",
            "insufficient data",
            "NO SIGNAL for",
            "Another bot instance owns the lock",
            "Order still within expiry window",
            "Skipped: not a",
            "POSITION MANAGEMENT",
            "--- POSITION [",
            "| Mark:",
            "  Indicators:",
            "  Cond:",
            "CLEANUP:",
            "=====", "-----",
            "  Order:",
            "BE check:",
            "BE/Trail check skipped",
            "Bot limit order",
            "TRADE REJECTED",
        )
        return not any(p in msg for p in skip)


for _h in logging.root.handlers:
    if isinstance(_h, logging.StreamHandler):
        _h.addFilter(_ConsoleFilter())


def send_telegram(msg: str) -> None:
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": msg, "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception as exc:
        log.warning("Telegram send failed: %s", exc)


def setup_exchange(cfg: BotConfig):
    validate_live_env()
    exchange = ccxt.bitget(
        {
            "apiKey": os.environ["BITGET_API_KEY"],
            "secret": os.environ["BITGET_SECRET_KEY"],
            "password": os.environ["BITGET_PASSPHRASE"],
            "enableRateLimit": True,
            "options": {"defaultType": "swap"},
        }
    )
    exchange.load_markets()
    log.info("Bitget connected. dry_run=%s", cfg.dry_run)
    return exchange


def clean_bitget_symbol(symbol: str) -> str:
    return symbol.split(":")[0].replace("/", "")


def new_client_order_id(prefix: str, symbol: str) -> str:
    safe_symbol = clean_bitget_symbol(symbol).replace("USDT", "")[:12]
    return f"{prefix}-{safe_symbol}-{int(time.time())}-{uuid.uuid4().hex[:6]}"


def extract_client_order_id(order: dict) -> str | None:
    info = order.get("info") or {}
    return order.get("clientOrderId") or order.get("clientOid") or info.get("clientOid") or info.get("clientOrderId")


def update_stop_loss(exchange, cfg: BotConfig, symbol: str, side: str, new_sl: float) -> bool:
    sym_clean = clean_bitget_symbol(symbol)
    if cfg.dry_run:
        log.info("  [DRY RUN] update_stop_loss: %s | side=%s | new_sl=%.8f", symbol, side, new_sl)
        return True
    try:
        params = {
            "symbol": sym_clean,
            "marginCoin": "USDT",
            "productType": "USDT-FUTURES",
            "planType": "pos_loss",
            "stopLossTriggerPrice": str(exchange.price_to_precision(symbol, new_sl)),
            "stopLossTriggerType": "fill_price",
            "holdSide": "long" if side == "long" else "short",
        }
        log.info("  Sending SL update: %s | side=%s | sl=%.8f | params=%s", symbol, side, new_sl, params)
        exchange.private_mix_post_v2_mix_order_place_pos_tpsl(params)
        log.info("  SL update SUCCESS: %s | new_sl=%.8f", symbol, new_sl)
        return True
    except Exception as exc:
        log.error("  SL UPDATE FAILED: %s | side=%s | new_sl=%.8f | error=%s", symbol, side, new_sl, exc)
        return False


def close_position(exchange, cfg: BotConfig, symbol: str, side: str, reason: str) -> bool:
    sym_clean = clean_bitget_symbol(symbol)
    log.info("  => CLOSE ATTEMPT: %s | side=%s | reason=%s", symbol, side, reason)
    if cfg.dry_run:
        log.info("  [DRY RUN] close_position skipped: %s | reason=%s", symbol, reason)
        return True
    try:
        log.info("  Sending close request: symbol=%s side=%s", sym_clean, side)
        exchange.private_mix_post_v2_mix_order_close_positions(
            {
                "symbol": sym_clean,
                "productType": "USDT-FUTURES",
                "marginCoin": "USDT",
                "holdSide": side,
            }
        )
        log.info("  CLOSE SUCCESS: %s | side=%s | reason=%s", symbol, side, reason)
        return True
    except Exception as exc:
        log.error("  CLOSE FAILED: %s | side=%s | reason=%s | error=%s", symbol, side, reason, exc)
        return False


def manage_open_orders(exchange, cfg: BotConfig, state: StateStore) -> set[str]:
    busy = set()
    try:
        orders = exchange.fetch_open_orders()
    except Exception as exc:
        log.error("OPEN ORDERS FETCH FAILED: %s", exc, exc_info=True)
        return busy

    log.info("--- OPEN ORDERS (%d total) ---", len(orders))
    for order in orders:
        symbol = order["symbol"]
        busy.add(symbol)
        client_id = extract_client_order_id(order)
        order_type = order.get("type")
        order_side = order.get("side")
        order_status = order.get("status")
        order_price = order.get("price")
        order_amount = order.get("amount")
        order_filled = order.get("filled", 0)
        log.info("  Order: %s | type=%s | side=%s | status=%s | price=%s | amount=%s | filled=%s | clientOid=%s",
                 symbol, order_type, order_side, order_status, order_price, order_amount, order_filled, client_id)

        if order_type != "limit" or not state.is_bot_order(client_id, cfg.order_prefix):
            if order_type != "limit":
                log.debug("    Skipped: not a limit order (type=%s)", order_type)
            else:
                log.debug("    Skipped: not a bot order (prefix=%s)", cfg.order_prefix)
            continue

        opened_ms = float(order.get("timestamp") or (order.get("info") or {}).get("cTime") or 0)
        age_min = (time.time() - opened_ms / 1000) / 60 if opened_ms else 0
        log.info("    Bot limit order age=%.1fm (expiry=%dmin)", age_min, cfg.limit_order_expiry_minutes)

        if age_min >= cfg.limit_order_expiry_minutes:
            log.info("    => CANCELLING expired order: %s | age=%.1fm | clientOid=%s", symbol, age_min, client_id)
            if cfg.dry_run:
                log.info("    [DRY RUN] cancel skipped")
            else:
                try:
                    exchange.cancel_order(order["id"], symbol)
                    log.info("    => CANCELLED: %s | id=%s", symbol, order["id"])
                except Exception as exc:
                    log.error("    => CANCEL FAILED: %s | id=%s | error=%s", symbol, order["id"], exc)
            state.update_order_status(client_id, "cancelled")
        else:
            log.debug("    Order still within expiry window")

    log.info("--- OPEN ORDERS END (%d busy symbols) ---", len(busy))
    return busy


def manage_open_positions(exchange, cfg: BotConfig, state: StateStore) -> set[str]:
    try:
        positions = exchange.fetch_positions()
    except Exception as exc:
        log.error("POSITIONS FETCH FAILED: %s", exc, exc_info=True)
        return set()

    active_syms = {p["symbol"] for p in positions if float(p.get("contracts") or 0) > 0}
    tracked = state.list_symbol_states(("open", "be"))
    log.info("=" * 70)
    log.info("POSITION MANAGEMENT | Exchange active: %d | State tracked: %d",
             len(active_syms), len(tracked))
    log.info("=" * 70)

    # --- Clean up positions no longer active on exchange ---
    for saved in tracked:
        symbol = saved["symbol"]
        if symbol in active_syms:
            continue
        last_pnl = saved.get("last_known_pnl")
        won_or_protected = saved.get("status") == "be" or (last_pnl is not None and float(last_pnl) > 0)
        cooldown = 3600 if won_or_protected else 14400
        state.set_cooldown(symbol, cooldown)
        state.clear_runtime_state(symbol)
        log.info("CLEANUP: %s no longer active | last_pnl=%s | status=%s | cooldown=%ds",
                 symbol, last_pnl, saved.get("status"), cooldown)

    # --- Manage each active position ---
    for pos in positions:
        symbol = pos["symbol"]
        side = pos.get("side")
        contracts = float(pos.get("contracts") or 0)
        if contracts <= 0:
            continue

        entry = float(pos["entryPrice"])
        mark = float(pos["markPrice"])
        profit_pct = (mark - entry) / entry if side == "long" else (entry - mark) / entry
        current = state.get_symbol_state(symbol)
        peak = current.get("peak_price")
        if peak is None:
            peak = mark
        peak = max(float(peak), mark) if side == "long" else min(float(peak), mark)
        preserved_status = current.get("status") if current.get("status") in {"open", "be"} else "open"
        state.upsert_symbol_state(symbol, peak_price=peak, last_known_pnl=profit_pct, status=preserved_status)

        open_ms = float(pos.get("timestamp") or (pos.get("info") or {}).get("cTime") or 0)
        age_h = (time.time() - open_ms / 1000) / 3600 if open_ms else 0

        log.info("--- POSITION [%s] %s ---", symbol, side.upper())
        log.info("  Entry: %.6f | Mark: %.6f | PnL: %+.4f%% | Peak: %.6f | Age: %.2fh",
                 entry, mark, profit_pct * 100, peak, age_h)

        # --- Max age check ---
        if age_h >= cfg.max_position_age_hours:
            log.info("  => MAX AGE TRIGGERED: age=%.2fh >= %.2fh | closing...", age_h, cfg.max_position_age_hours)
            if close_position(exchange, cfg, symbol, side, "max_age"):
                state.set_cooldown(symbol, 14400)
                log.info("  => CLOSED by max age | Symbol=%s | PnL=%+.4f%%", symbol, profit_pct * 100)
                send_telegram(f"*{symbol} closed by max age* PnL: {profit_pct * 100:.2f}%")
            else:
                log.error("  => FAILED to close by max age: %s", symbol)
            continue

        # --- Fetch indicators for management decisions ---
        try:
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe=cfg.timeframe, limit=300)
            df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
            atr = float(ta.atr(df["high"], df["low"], df["close"], length=14).iloc[-1])
            df["ZLEMA"] = calc_zlema(df["close"], cfg.zl_length)
            df["Two_P"], df["Two_PP"] = calc_two_pole(df["close"], cfg.tp_filter_len)
            live = df.iloc[-1]
            log.info("  Indicators: close=%.6f | ZLEMA=%.6f | Two_P=%.6f | Two_PP=%.6f | ATR=%.6f",
                     live["close"], live["ZLEMA"], live["Two_P"], live["Two_PP"], atr)
            log.info("  Cond: close>ZLEMA=%s | Two_P>Two_PP=%s | close<ZLEMA=%s | Two_P<Two_PP=%s",
                     bool(live["close"] > live["ZLEMA"]), bool(live["Two_P"] > live["Two_PP"]),
                     bool(live["close"] < live["ZLEMA"]), bool(live["Two_P"] < live["Two_PP"]))
        except Exception as exc:
            log.warning("Indicator fetch failed %s: %s", symbol, exc)
            atr = 0.0
            live = None

        if current.get("peak_price") is None:
            log.info("  => POSITION OPENED: %s | side=%s | entry=%.6f | contracts=%.8f | mark=%.6f | age=%.2fh | PnL=%+.4f%%",
                     symbol, side, entry, contracts, mark, age_h, profit_pct * 100)
            if live is not None:
                log.info("     [i] close=%.6f | ZLEMA=%.6f | Two_P=%.6f | Two_PP=%.6f | ATR=%.6f",
                         live["close"], live["ZLEMA"], live["Two_P"], live["Two_PP"], atr)
                log.info("     [c] close>ZLEMA=%s | Two_P>Two_PP=%s",
                         bool(live["close"] > live["ZLEMA"]), bool(live["Two_P"] > live["Two_PP"]))

        # --- Early exit check ---
        if cfg.enable_early_exit and live is not None and profit_pct < -0.005:
            early_long = side == "long" and (live["close"] < live["ZLEMA"] or live["Two_P"] < live["Two_PP"])
            early_short = side == "short" and (live["close"] > live["ZLEMA"] or live["Two_P"] > live["Two_PP"])
            if early_long or early_short:
                log.info("  => EARLY EXIT TRIGGERED: PnL=%+.4f%% (below -0.5%%)", profit_pct * 100)
                if side == "long":
                    log.info("     close(%.6f) < ZLEMA(%.6f)=%s | Two_P(%.6f) < Two_PP(%.6f)=%s",
                             live["close"], live["ZLEMA"], bool(live["close"] < live["ZLEMA"]),
                             live["Two_P"], live["Two_PP"], bool(live["Two_P"] < live["Two_PP"]))
                else:
                    log.info("     close(%.6f) > ZLEMA(%.6f)=%s | Two_P(%.6f) > Two_PP(%.6f)=%s",
                             live["close"], live["ZLEMA"], bool(live["close"] > live["ZLEMA"]),
                             live["Two_P"], live["Two_PP"], bool(live["Two_P"] > live["Two_PP"]))
                if close_position(exchange, cfg, symbol, side, "early_exit"):
                    state.set_cooldown(symbol, 14400)
                    log.info("  => CLOSED by early exit | %s | PnL=%+.4f%%", symbol, profit_pct * 100)
                continue
            else:
                log.debug("  Early exit check: not triggered (PnL=%+.4f%%)", profit_pct * 100)

        # --- Breakeven and trailing ---
        if atr > 0:
            be_trigger = (atr * 1.5) / entry
            log.info("  BE check: profit_pct=%+.4f%% | be_trigger=%.4f%% | status=%s",
                     profit_pct * 100, be_trigger * 100, current.get("status"))

            if profit_pct >= be_trigger and current.get("status") != "be":
                log.info("  => BREAKEVEN ACTIVATING: profit(%.4f%%) >= trigger(%.4f%%)",
                         profit_pct * 100, be_trigger * 100)
                if update_stop_loss(exchange, cfg, symbol, side, entry):
                    state.upsert_symbol_state(symbol, status="be", last_trail_sl=entry)
                    log.info("  => BREAKEVEN ACTIVATED | %s | SL moved to entry=%.6f", symbol, entry)
                    send_telegram(f"*{symbol} breakeven activated* trigger={be_trigger * 100:.2f}%")
                else:
                    log.error("  => BREAKEVEN FAILED to update SL for %s", symbol)

            current = state.get_symbol_state(symbol)
            if current.get("status") == "be":
                profit_at_peak = (peak - entry) / entry if side == "long" else (entry - peak) / entry
                atr_profit = profit_at_peak / (atr / entry) if atr > 0 else 0

                if atr_profit >= 2.7:
                    trail_dist = (atr * 0.2) / peak
                    trail_tier = "tight (0.2x ATR)"
                elif atr_profit >= 2.2:
                    trail_dist = (atr * 0.5) / peak
                    trail_tier = "medium (0.5x ATR)"
                else:
                    trail_dist = (atr * 1.0) / peak
                    trail_tier = "loose (1.0x ATR)"

                trail_sl = peak * (1 - trail_dist) if side == "long" else peak * (1 + trail_dist)
                last_trail = current.get("last_trail_sl")
                moved = last_trail is None or (side == "long" and trail_sl > float(last_trail)) or (side == "short" and trail_sl < float(last_trail))
                valid = (side == "long" and trail_sl > entry * 1.001) or (side == "short" and trail_sl < entry * 0.999)

                log.info("  Trailing: profit_at_peak=%.4f%% | atr_profit=%.2f | tier=%s",
                         profit_at_peak * 100, atr_profit, trail_tier)
                log.info("    peak=%.6f | trail_dist=%.6f%% | new_trail_sl=%.6f | last_trail_sl=%s",
                         peak, trail_dist * 100, trail_sl, last_trail)
                log.info("    moved=%s | valid=%s | side=%s", moved, valid, side)

                if moved and valid and update_stop_loss(exchange, cfg, symbol, side, trail_sl):
                    state.upsert_symbol_state(symbol, last_trail_sl=trail_sl)
                    log.info("  => TRAIL UPDATED: %s | SL: %.6f -> %.6f (%.4f%%)",
                             symbol, float(last_trail) if last_trail else entry, trail_sl,
                             abs(trail_sl - float(last_trail if last_trail else entry)) / entry * 100)
                    send_telegram(f"*{symbol} trailing updated* SL={trail_sl:.6f}")
        else:
            log.info("  BE/Trail check skipped: ATR=0")

    log.info("-" * 70)
    log.info("POSITION MANAGEMENT END | Active: %d", len(active_syms))
    log.info("-" * 70)
    return active_syms


def scan_and_place(exchange, cfg: BotConfig, state: StateStore, busy_symbols: set[str]) -> None:
    if len(busy_symbols) >= cfg.max_open_positions:
        log.info("SCAN SKIP: max positions reached (%d/%d)", len(busy_symbols), cfg.max_open_positions)
        return

    balance = float(exchange.fetch_balance()["total"].get("USDT", 0))
    tickers = exchange.fetch_tickers()
    top_symbols = [
        item[0]
        for item in sorted(
            [(s, float(t.get("quoteVolume") or 0)) for s, t in tickers.items() if s.endswith("/USDT:USDT")],
            key=lambda x: x[1],
            reverse=True,
        )[:100]
    ]

    log.info("=" * 70)
    log.info("SCAN CYCLE | Balance: %.2f USDT | Top symbols: %d | Slots: %d/%d",
             balance, len(top_symbols), len(busy_symbols), cfg.max_open_positions)
    log.info("=" * 70)

    symbols_scanned = 0
    signals_found = 0
    orders_placed = 0

    for symbol in top_symbols:
        if symbol in busy_symbols:
            continue
        if len(busy_symbols) >= cfg.max_open_positions:
            log.info("SCAN STOP: max positions reached mid-scan (%d/%d)", len(busy_symbols), cfg.max_open_positions)
            break
        if state.is_in_cooldown(symbol):
            continue

        symbols_scanned += 1
        try:
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe=cfg.timeframe, limit=500)
            df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
            if len(df) < 300:
                log.debug("SKIP %s: insufficient data (%d bars)", symbol, len(df))
                continue
            opts = build_signal_options(use_volume=cfg.use_volume_filter)
            df = generate_signals(calculate_all_indicators(df, cfg), cfg, options=opts)
            last_closed = df.iloc[-2]
            last_row = df.iloc[-1]

            if not bool(last_closed["Master_Buy"]) and not bool(last_closed["Master_Sell"]):
                continue

            signals_found += 1
            side = "long" if bool(last_closed["Master_Buy"]) else "short"

            ticker = exchange.fetch_ticker(symbol)
            live_price = float(ticker["last"])

            market = exchange.market(symbol)
            min_amount = float(((market.get("limits") or {}).get("amount") or {}).get("min") or 1e-8)
            plan, reason = build_trade_plan(side, live_price, float(last_closed["ATR14"]), balance, cfg, min_amount)
            if not plan:
                log.info("  => TRADE REJECTED: %s | reason: %s", symbol, reason)
                continue

            order_qty = float(exchange.amount_to_precision(symbol, plan.qty))
            if order_qty <= 0:
                log.info("  => TRADE REJECTED: %s | amount_to_precision rounded qty to zero (raw=%.8f)", symbol, plan.qty)
                continue

            log.info("--- INDICATORS [%s] %s ---", symbol, side.upper())
            log.info("  Price: close=%.6f | VMA=%.6f | ST_dir=%s | MACD=%.6f | MACD_sig=%.6f",
                     last_closed["close"], last_closed["VMA"], last_closed["ST_dir"],
                     last_closed["MACD"], last_closed["MACD_sig"])
            log.info("  ZLEMA=%.6f | ZL_Upper=%.6f | ZL_Lower=%.6f | zl_trend_state=%s",
                     last_closed["ZLEMA"], last_closed["ZL_Upper"], last_closed["ZL_Lower"],
                     last_closed.get("zl_trend_state", "N/A"))
            log.info("  Two_P=%.6f | Two_PP=%.6f | ATR14=%.6f | Vol_Anomaly=%s",
                     last_closed["Two_P"], last_closed["Two_PP"], last_closed["ATR14"],
                     bool(last_closed["Vol_Anomaly"]))
            log.info("  => SIGNAL DETECTED: Trigger=%s | st_buy=%s zl_buy=%s tp_buy=%s st_sell=%s zl_sell=%s tp_sell=%s | Master_Buy=%s Master_Sell=%s | Vol_Anomaly=%s",
                     last_closed["Signal_Trigger"],
                     bool(last_closed["st_buy"]), bool(last_closed["zl_buy"]), bool(last_closed["tp_buy"]),
                     bool(last_closed["st_sell"]), bool(last_closed["zl_sell"]), bool(last_closed["tp_sell"]),
                     bool(last_closed["Master_Buy"]), bool(last_closed["Master_Sell"]),
                     bool(last_closed["Vol_Anomaly"]))
            log.info("  Live price: %.6f (vs signal bar close: %.6f)", live_price, last_closed["close"])

            # --- Log trade plan details ---
            log.info("  => TRADE PLAN ACCEPTED: %s %s", symbol, side.upper())
            log.info("     Entry: %.6f | SL: %.6f | TP: %.6f", plan.entry, plan.stop_loss, plan.take_profit)
            log.info("     SL distance: %.4f%% | TP distance: %.4f%% | R/R: %.2f | Risk: %.2f USDT",
                     plan.sl_pct * 100, plan.tp_pct * 100, plan.rr, plan.risk_usdt)
            log.info("     Qty: %.6f | Notional: %.6f | Margin: %.6f USDT",
                     order_qty, plan.notional, plan.notional / cfg.leverage)

            order_side = "buy" if side == "long" else "sell"
            client_id = new_client_order_id(cfg.order_prefix, symbol)
            params = {
                "marginCoin": "USDT",
                "marginMode": "isolated",
                "tradeSide": "open",
                "clientOid": client_id,
                "presetStopSurplusPrice": str(exchange.price_to_precision(symbol, plan.take_profit)),
                "presetStopLossPrice": str(exchange.price_to_precision(symbol, plan.stop_loss)),
            }
            if cfg.dry_run:
                log.info("  => DRY RUN ORDER: %s %s | clientOid=%s", symbol, order_side, client_id)
                log.info("     Entry=%.8f | SL=%.8f | TP=%.8f | Qty=%.8f | Trigger=%s",
                         plan.entry, plan.stop_loss, plan.take_profit,
                         order_qty, last_closed["Signal_Trigger"])
                exchange_order_id = None
            else:
                exchange.set_leverage(int(cfg.leverage), symbol)
                log.info("  => PLACING LIVE ORDER: %s %s | clientOid=%s | qty=%.6f @ %.6f",
                         symbol, order_side, client_id, order_qty, plan.entry)
                order = exchange.create_order(symbol, "limit", order_side, order_qty, plan.entry, params=params)
                exchange_order_id = order.get("id")
                log.info("  => ORDER PLACED: %s | exchangeOrderId=%s", symbol, exchange_order_id)

            state.record_order(client_id, exchange_order_id, symbol, side)
            busy_symbols.add(symbol)
            orders_placed += 1
            send_telegram(
                f"*{symbol} {side.upper()} v2 LIMIT*\n"
                f"Entry: `{plan.entry:.6f}`\nSL: `{plan.stop_loss:.6f}`\nTP: `{plan.take_profit:.6f}`\n"
                f"Estimated Risk: `{plan.risk_usdt:.2f} USDT` | Margin aprox: `{plan.notional / cfg.leverage:.2f} USDT`\n"
                f"R/R: `{plan.rr:.2f}` | Trigger: `{last_closed['Signal_Trigger']}`"
            )
        except Exception as exc:
            log.error("SCAN ERROR %s: %s", symbol, exc, exc_info=True)

    log.info("-" * 70)
    log.info("SCAN CYCLE END | Scanned: %d | Signals: %d | Orders placed: %d | Active: %d/%d",
             symbols_scanned, signals_found, orders_placed, len(busy_symbols), cfg.max_open_positions)
    log.info("-" * 70)


def main() -> None:
    cfg = BotConfig()
    state = StateStore(cfg.state_db_path)
    owner_id = f"{os.environ.get('RENDER_INSTANCE_ID', 'local')}-{os.getpid()}"
    exchange = setup_exchange(cfg)
    last_report_day = datetime.now().day
    cycle_num = 0

    log.info("#" * 70)
    log.info("#")
    log.info("#  BOT COMBINED STRATEGY v2 - INICIADO")
    log.info("#  PID: %s | Dry-run: %s | Max positions: %d | Timeframe: %s",
             owner_id, cfg.dry_run, cfg.max_open_positions, cfg.timeframe)
    log.info("#  Logging level: INFO (full trade diagnostics enabled)")
    log.info("#  ZL length: %d | ZL mult: %.1f | TP filter: %d",
             cfg.zl_length, cfg.zl_mult, cfg.tp_filter_len)
    log.info("#  Early exit: %s | Max age: %.1fh | Limit discount: %.2f%%",
             cfg.enable_early_exit, cfg.max_position_age_hours, cfg.limit_discount_pct * 100)
    log.info("#  Risk per trade: %.2f%% | Min margin: %.2f USDT | Max margin frac: %.2f%%",
             cfg.risk_per_trade * 100, cfg.min_margin_usdt, cfg.max_margin_fraction * 100)
    log.info("#")
    log.info("#" * 70)

    while True:
        cycle_num += 1
        try:
            if not state.acquire_lock("combined_strategy_v2", owner_id):
                log.warning("CYCLE %d: Another bot instance owns the lock. Sleeping 60s.", cycle_num)
                time.sleep(60)
                continue

            now = datetime.now()
            if now.hour == 0 and now.day != last_report_day:
                send_telegram("*Combined Strategy v2 daily heartbeat*")
                last_report_day = now.day

            log.info("")
            log.info(">>> CYCLE %d START <<<  %s", cycle_num, now.strftime("%Y-%m-%d %H:%M:%S"))

            busy = manage_open_positions(exchange, cfg, state)
            busy.update(manage_open_orders(exchange, cfg, state))
            scan_and_place(exchange, cfg, state, busy)

            log.info("<<< CYCLE %d END >>>  Active symbols: %d | Next cycle in 60s",
                     cycle_num, len(busy))
            log.info("")
            time.sleep(60)
        except Exception as exc:
            log.error("CYCLE %d MAIN LOOP FAILED: %s", cycle_num, exc, exc_info=True)
            time.sleep(60)

    log.info("BOT FINALIZED")


if __name__ == "__main__":
    main()
