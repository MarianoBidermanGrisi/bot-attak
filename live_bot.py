import logging
import os
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
    from .signals import generate_signals
    from .state import StateStore
except ImportError:
    from config import BotConfig, validate_live_env
    from indicators import calc_two_pole, calc_zlema, calculate_all_indicators
    from risk import build_trade_plan
    from signals import generate_signals
    from state import StateStore


LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "combined_strategy_v2.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler()],
)
log = logging.getLogger(__name__)


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
    if cfg.dry_run:
        log.info("DRY RUN update SL %s %s -> %.8f", symbol, side, new_sl)
        return True
    try:
        params = {
            "symbol": clean_bitget_symbol(symbol),
            "marginCoin": "USDT",
            "productType": "USDT-FUTURES",
            "planType": "pos_loss",
            "stopLossTriggerPrice": str(exchange.price_to_precision(symbol, new_sl)),
            "stopLossTriggerType": "fill_price",
            "holdSide": "long" if side == "long" else "short",
        }
        exchange.private_mix_post_v2_mix_order_place_pos_tpsl(params)
        return True
    except Exception as exc:
        log.error("SL update failed %s: %s", symbol, exc)
        return False


def close_position(exchange, cfg: BotConfig, symbol: str, side: str, reason: str) -> bool:
    if cfg.dry_run:
        log.info("DRY RUN close %s %s reason=%s", symbol, side, reason)
        return True
    try:
        exchange.private_mix_post_v2_mix_order_close_positions(
            {
                "symbol": clean_bitget_symbol(symbol),
                "productType": "USDT-FUTURES",
                "marginCoin": "USDT",
                "holdSide": side,
            }
        )
        return True
    except Exception as exc:
        log.error("Close failed %s: %s", symbol, exc)
        return False


def manage_open_orders(exchange, cfg: BotConfig, state: StateStore) -> set[str]:
    busy = set()
    try:
        orders = exchange.fetch_open_orders()
    except Exception as exc:
        log.error("Open order fetch failed: %s", exc)
        return busy

    for order in orders:
        symbol = order["symbol"]
        busy.add(symbol)
        client_id = extract_client_order_id(order)
        if order.get("type") != "limit" or not state.is_bot_order(client_id, cfg.order_prefix):
            continue
        opened_ms = float(order.get("timestamp") or (order.get("info") or {}).get("cTime") or 0)
        age_min = (time.time() - opened_ms / 1000) / 60 if opened_ms else 0
        if age_min >= cfg.limit_order_expiry_minutes:
            if cfg.dry_run:
                log.info("DRY RUN cancel aged bot limit %s %s", symbol, client_id)
            else:
                exchange.cancel_order(order["id"], symbol)
            state.update_order_status(client_id, "cancelled")
            log.info("Cancelled expired bot limit %s age=%.1fm", symbol, age_min)
    return busy


def manage_open_positions(exchange, cfg: BotConfig, state: StateStore) -> set[str]:
    try:
        positions = exchange.fetch_positions()
    except Exception as exc:
        log.error("Position fetch failed: %s", exc)
        return set()

    active = {p["symbol"] for p in positions if float(p.get("contracts") or 0) > 0}

    for saved in state.list_symbol_states(("open", "be")):
        symbol = saved["symbol"]
        if symbol in active:
            continue
        last_pnl = saved.get("last_known_pnl")
        won_or_protected = saved.get("status") == "be" or (last_pnl is not None and float(last_pnl) > 0)
        state.set_cooldown(symbol, 3600 if won_or_protected else 14400)
        state.clear_runtime_state(symbol)
        log.info("Position %s no longer active. Cooldown=%ss", symbol, 3600 if won_or_protected else 14400)

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
        if age_h >= cfg.max_position_age_hours:
            if close_position(exchange, cfg, symbol, side, "max_age"):
                state.set_cooldown(symbol, 14400)
                send_telegram(f"*{symbol} closed by max age* PnL: {profit_pct * 100:.2f}%")
            continue

        try:
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe=cfg.timeframe, limit=300)
            df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
            atr = float(ta.atr(df["high"], df["low"], df["close"], length=14).iloc[-1])
            df["ZLEMA"] = calc_zlema(df["close"], cfg.zl_length)
            df["Two_P"], df["Two_PP"] = calc_two_pole(df["close"], cfg.tp_filter_len)
            live = df.iloc[-1]
        except Exception as exc:
            log.warning("Indicator management failed %s: %s", symbol, exc)
            atr = 0.0
            live = None

        if cfg.enable_early_exit and live is not None and profit_pct < -0.005:
            if side == "long" and (live["close"] < live["ZLEMA"] or live["Two_P"] < live["Two_PP"]):
                if close_position(exchange, cfg, symbol, side, "early_exit"):
                    state.set_cooldown(symbol, 14400)
                continue
            if side == "short" and (live["close"] > live["ZLEMA"] or live["Two_P"] > live["Two_PP"]):
                if close_position(exchange, cfg, symbol, side, "early_exit"):
                    state.set_cooldown(symbol, 14400)
                continue

        if atr > 0:
            be_trigger = (atr * 1.5) / entry
            if profit_pct >= be_trigger and current.get("status") != "be":
                if update_stop_loss(exchange, cfg, symbol, side, entry):
                    state.upsert_symbol_state(symbol, status="be", last_trail_sl=entry)
                    send_telegram(f"*{symbol} breakeven activated* trigger={be_trigger * 100:.2f}%")

            current = state.get_symbol_state(symbol)
            if current.get("status") == "be":
                profit_at_peak = (peak - entry) / entry if side == "long" else (entry - peak) / entry
                atr_profit = profit_at_peak / (atr / entry) if atr > 0 else 0
                if atr_profit >= 2.7:
                    trail_dist = (atr * 0.2) / peak
                elif atr_profit >= 2.2:
                    trail_dist = (atr * 0.5) / peak
                else:
                    trail_dist = (atr * 1.0) / peak
                trail_sl = peak * (1 - trail_dist) if side == "long" else peak * (1 + trail_dist)
                last_trail = current.get("last_trail_sl")
                moved = last_trail is None or (side == "long" and trail_sl > float(last_trail)) or (side == "short" and trail_sl < float(last_trail))
                valid = (side == "long" and trail_sl > entry * 1.001) or (side == "short" and trail_sl < entry * 0.999)
                if moved and valid and update_stop_loss(exchange, cfg, symbol, side, trail_sl):
                    state.upsert_symbol_state(symbol, last_trail_sl=trail_sl)
                    send_telegram(f"*{symbol} trailing updated* SL={trail_sl:.6f}")

    return active


def scan_and_place(exchange, cfg: BotConfig, state: StateStore, busy_symbols: set[str]) -> None:
    if len(busy_symbols) >= cfg.max_open_positions:
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

    for symbol in top_symbols:
        if symbol in busy_symbols or len(busy_symbols) >= cfg.max_open_positions or state.is_in_cooldown(symbol):
            continue
        try:
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe=cfg.timeframe, limit=500)
            df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
            if len(df) < 300:
                continue
            df = generate_signals(calculate_all_indicators(df, cfg), cfg)
            last_closed = df.iloc[-2]
            if not bool(last_closed["Master_Buy"] or last_closed["Master_Sell"]):
                continue

            side = "long" if bool(last_closed["Master_Buy"]) else "short"
            ticker = exchange.fetch_ticker(symbol)
            live_price = float(ticker["last"])
            market = exchange.market(symbol)
            min_amount = float(((market.get("limits") or {}).get("amount") or {}).get("min") or 1e-8)
            plan, reason = build_trade_plan(side, live_price, float(last_closed["ATR14"]), balance, cfg, min_amount)
            if not plan:
                log.info("%s rejected: %s", symbol, reason)
                continue
            order_qty = float(exchange.amount_to_precision(symbol, plan.qty))
            if order_qty <= 0:
                log.info("%s rejected: amount_to_precision rounded qty to zero", symbol)
                continue

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
                log.info(
                    "DRY RUN %s %s limit entry=%.8f sl=%.8f tp=%.8f qty=%.8f trigger=%s est_risk=%.2f margin=%.2f",
                    symbol,
                    order_side,
                    plan.entry,
                    plan.stop_loss,
                    plan.take_profit,
                    order_qty,
                    last_closed["Signal_Trigger"],
                    plan.risk_usdt,
                    plan.notional / cfg.leverage,
                )
                exchange_order_id = None
            else:
                exchange.set_leverage(int(cfg.leverage), symbol)
                order = exchange.create_order(symbol, "limit", order_side, order_qty, plan.entry, params=params)
                exchange_order_id = order.get("id")
            state.record_order(client_id, exchange_order_id, symbol, side)
            busy_symbols.add(symbol)
            send_telegram(
                f"*{symbol} {side.upper()} v2 LIMIT*\n"
                f"Entry: `{plan.entry:.6f}`\nSL: `{plan.stop_loss:.6f}`\nTP: `{plan.take_profit:.6f}`\n"
                f"Estimated Risk: `{plan.risk_usdt:.2f} USDT` | Margin aprox: `{plan.notional / cfg.leverage:.2f} USDT`\n"
                f"R/R: `{plan.rr:.2f}` | Trigger: `{last_closed['Signal_Trigger']}`"
            )
        except Exception as exc:
            log.error("Scan failed %s: %s", symbol, exc)


def main() -> None:
    cfg = BotConfig()
    state = StateStore(cfg.state_db_path)
    owner_id = f"{os.environ.get('RENDER_INSTANCE_ID', 'local')}-{os.getpid()}"
    exchange = setup_exchange(cfg)
    last_report_day = datetime.now().day

    while True:
        try:
            if not state.acquire_lock("combined_strategy_v2", owner_id):
                log.warning("Another bot instance owns the lock. Sleeping.")
                time.sleep(60)
                continue
            now = datetime.now()
            if now.hour == 0 and now.day != last_report_day:
                send_telegram("*Combined Strategy v2 daily heartbeat*")
                last_report_day = now.day
            busy = manage_open_positions(exchange, cfg, state)
            busy.update(manage_open_orders(exchange, cfg, state))
            scan_and_place(exchange, cfg, state, busy)
            time.sleep(60)
        except Exception as exc:
            log.error("Main loop failed: %s", exc)
            time.sleep(60)


if __name__ == "__main__":
    main()
