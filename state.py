import sqlite3
import time
import os
from contextlib import contextmanager
from dataclasses import dataclass


@dataclass(frozen=True)
class BotOrder:
    client_order_id: str
    exchange_order_id: str | None
    symbol: str
    side: str
    status: str
    created_at: float


class StateStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        parent = os.path.dirname(os.path.abspath(db_path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._init_db()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS symbol_state (
                    symbol TEXT PRIMARY KEY,
                    peak_price REAL,
                    last_trail_sl REAL,
                    cooldown_until REAL DEFAULT 0,
                    last_known_pnl REAL,
                    status TEXT,
                    entry_price REAL,
                    side TEXT,
                    open_time REAL,
                    updated_at REAL NOT NULL
                )
                """
            )
            for col in ("entry_price REAL", "side TEXT", "open_time REAL"):
                try:
                    conn.execute(f"ALTER TABLE symbol_state ADD COLUMN {col}")
                except sqlite3.OperationalError:
                    pass
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS bot_orders (
                    client_order_id TEXT PRIMARY KEY,
                    exchange_order_id TEXT,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS instance_lock (
                    lock_name TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    expires_at REAL NOT NULL
                )
                """
            )

    def get_symbol_state(self, symbol: str) -> dict:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM symbol_state WHERE symbol = ?", (symbol,)).fetchone()
        return dict(row) if row else {}

    def list_symbol_states(self, statuses: tuple[str, ...] | None = None) -> list[dict]:
        with self._conn() as conn:
            if statuses:
                placeholders = ",".join("?" for _ in statuses)
                rows = conn.execute(f"SELECT * FROM symbol_state WHERE status IN ({placeholders})", statuses).fetchall()
            else:
                rows = conn.execute("SELECT * FROM symbol_state").fetchall()
        return [dict(row) for row in rows]

    def upsert_symbol_state(self, symbol: str, **values) -> None:
        current = self.get_symbol_state(symbol)
        merged = {
            "peak_price": current.get("peak_price"),
            "last_trail_sl": current.get("last_trail_sl"),
            "cooldown_until": current.get("cooldown_until", 0),
            "last_known_pnl": current.get("last_known_pnl"),
            "status": current.get("status"),
            "entry_price": current.get("entry_price"),
            "side": current.get("side"),
            "open_time": current.get("open_time"),
        }
        merged.update(values)
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO symbol_state(symbol, peak_price, last_trail_sl, cooldown_until, last_known_pnl, status, entry_price, side, open_time, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    peak_price=excluded.peak_price,
                    last_trail_sl=excluded.last_trail_sl,
                    cooldown_until=excluded.cooldown_until,
                    last_known_pnl=excluded.last_known_pnl,
                    status=excluded.status,
                    entry_price=excluded.entry_price,
                    side=excluded.side,
                    open_time=excluded.open_time,
                    updated_at=excluded.updated_at
                """,
                (
                    symbol,
                    merged["peak_price"],
                    merged["last_trail_sl"],
                    merged["cooldown_until"],
                    merged["last_known_pnl"],
                    merged["status"],
                    merged["entry_price"],
                    merged["side"],
                    merged["open_time"],
                    time.time(),
                ),
            )

    def clear_runtime_state(self, symbol: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE symbol_state SET peak_price = NULL, last_trail_sl = NULL, last_known_pnl = NULL, status = NULL, entry_price = NULL, side = NULL, open_time = NULL, updated_at = ? WHERE symbol = ?",
                (time.time(), symbol),
            )

    def is_in_cooldown(self, symbol: str) -> bool:
        state = self.get_symbol_state(symbol)
        return float(state.get("cooldown_until") or 0) > time.time()

    def set_cooldown(self, symbol: str, seconds: int) -> None:
        self.upsert_symbol_state(symbol, cooldown_until=time.time() + seconds)

    def record_order(self, client_order_id: str, exchange_order_id: str | None, symbol: str, side: str, status: str = "open") -> None:
        now = time.time()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO bot_orders(client_order_id, exchange_order_id, symbol, side, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(client_order_id) DO UPDATE SET
                    exchange_order_id=excluded.exchange_order_id,
                    status=excluded.status,
                    updated_at=excluded.updated_at
                """,
                (client_order_id, exchange_order_id, symbol, side, status, now, now),
            )

    def update_order_status(self, client_order_id: str, status: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE bot_orders SET status = ?, updated_at = ? WHERE client_order_id = ?",
                (status, time.time(), client_order_id),
            )

    def is_bot_order(self, client_order_id: str | None, prefix: str) -> bool:
        if not client_order_id or not client_order_id.startswith(prefix):
            return False
        with self._conn() as conn:
            row = conn.execute("SELECT 1 FROM bot_orders WHERE client_order_id = ?", (client_order_id,)).fetchone()
        return row is not None

    def acquire_lock(self, lock_name: str, owner_id: str, ttl_seconds: int = 90) -> bool:
        now = time.time()
        expires = now + ttl_seconds
        with self._conn() as conn:
            row = conn.execute("SELECT owner_id, expires_at FROM instance_lock WHERE lock_name = ?", (lock_name,)).fetchone()
            if row and row["expires_at"] > now and row["owner_id"] != owner_id:
                return False
            conn.execute(
                """
                INSERT INTO instance_lock(lock_name, owner_id, expires_at)
                VALUES (?, ?, ?)
                ON CONFLICT(lock_name) DO UPDATE SET owner_id=excluded.owner_id, expires_at=excluded.expires_at
                """,
                (lock_name, owner_id, expires),
            )
        return True
