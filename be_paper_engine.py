"""Motor paper independiente para observar PPI Produccion sin operar cuentas.

Usa exclusivamente Decimal y una base SQLite separada. Cada operacion queda
marcada PRODUCTION_PAPER/SIMULATED y los identificadores comienzan con PAPER-.
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from typing import Optional


SOURCE = "PRODUCTION_PAPER"
STRATEGY_VERSION = "paper-momentum-v1"
ZERO = Decimal("0")


def D(value, default="0") -> Decimal:
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal(default)


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


@dataclass(frozen=True)
class Quote:
    symbol: str
    asset_class: str
    settlement: str
    last: Decimal
    bid: Decimal
    ask: Decimal
    bid_size: Decimal
    ask_size: Decimal
    observed_at: str


class PaperStore:
    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.init_db()

    def connect(self):
        conn = sqlite3.connect(self.path, timeout=20)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def init_db(self):
        with self.connect() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS observer_state(
              id INTEGER PRIMARY KEY CHECK(id=1), mode TEXT NOT NULL,
              process_state TEXT NOT NULL, session_state TEXT NOT NULL,
              ppi_auth TEXT NOT NULL, heartbeat_at TEXT, last_market_data_at TEXT,
              real_orders_sent INTEGER NOT NULL DEFAULT 0,
              http_allowed INTEGER NOT NULL DEFAULT 0,
              http_blocked INTEGER NOT NULL DEFAULT 0,
              detail TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS market_snapshots(
              id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL,
              observed_at TEXT NOT NULL, symbol TEXT NOT NULL, asset_class TEXT NOT NULL,
              settlement TEXT NOT NULL, last TEXT NOT NULL, bid TEXT NOT NULL,
              ask TEXT NOT NULL, bid_size TEXT NOT NULL, ask_size TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS idx_snap_symbol ON market_snapshots(symbol,id);
            CREATE TABLE IF NOT EXISTS paper_decisions(
              id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL,
              strategy_version TEXT NOT NULL, decision_key TEXT NOT NULL UNIQUE,
              decided_at TEXT NOT NULL, symbol TEXT NOT NULL, action TEXT NOT NULL,
              score TEXT NOT NULL, reason TEXT NOT NULL, features_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS paper_positions(
              paper_id TEXT PRIMARY KEY, source TEXT NOT NULL, strategy_version TEXT NOT NULL,
              symbol TEXT NOT NULL, asset_class TEXT NOT NULL, settlement TEXT NOT NULL,
              status TEXT NOT NULL, quantity TEXT NOT NULL, entry_price TEXT NOT NULL,
              entry_cost TEXT NOT NULL, stop_price TEXT NOT NULL, target_price TEXT NOT NULL,
              opened_at TEXT NOT NULL, closed_at TEXT, exit_price TEXT, exit_cost TEXT,
              gross_pnl TEXT, net_pnl TEXT, close_reason TEXT, features_json TEXT NOT NULL,
              max_favorable TEXT NOT NULL DEFAULT '0', max_adverse TEXT NOT NULL DEFAULT '0');
            CREATE TABLE IF NOT EXISTS paper_fills(
              id INTEGER PRIMARY KEY AUTOINCREMENT, paper_id TEXT NOT NULL,
              source TEXT NOT NULL, side TEXT NOT NULL, filled_at TEXT NOT NULL,
              quantity TEXT NOT NULL, price TEXT NOT NULL, costs TEXT NOT NULL,
              slippage TEXT NOT NULL, FOREIGN KEY(paper_id) REFERENCES paper_positions(paper_id));
            CREATE TABLE IF NOT EXISTS paper_events(
              id INTEGER PRIMARY KEY AUTOINCREMENT, event_at TEXT NOT NULL,
              source TEXT NOT NULL, event_type TEXT NOT NULL, paper_id TEXT,
              detail TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS paper_learning_samples(
              paper_id TEXT PRIMARY KEY, source TEXT NOT NULL, strategy_version TEXT NOT NULL,
              feature_timestamp TEXT NOT NULL, features_json TEXT NOT NULL,
              label_timestamp TEXT, net_return_pct TEXT, outcome TEXT, duration_minutes INTEGER,
              FOREIGN KEY(paper_id) REFERENCES paper_positions(paper_id));
            CREATE TABLE IF NOT EXISTS paper_equity(
              id INTEGER PRIMARY KEY AUTOINCREMENT, measured_at TEXT NOT NULL,
              source TEXT NOT NULL, cash TEXT NOT NULL, exposure TEXT NOT NULL,
              unrealized_pnl TEXT NOT NULL, realized_pnl TEXT NOT NULL, equity TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS http_audit(
              id INTEGER PRIMARY KEY AUTOINCREMENT, happened_at TEXT NOT NULL,
              method TEXT NOT NULL, path TEXT NOT NULL, result TEXT NOT NULL);
            INSERT OR IGNORE INTO observer_state(id,mode,process_state,session_state,ppi_auth)
              VALUES(1,'PRODUCTION_PAPER','STOPPED','UNKNOWN','NOT_ATTEMPTED');
            """)

    def audit_http(self, method, path, result):
        with self.connect() as c:
            c.execute("INSERT INTO http_audit VALUES(NULL,?,?,?,?)",
                      (now_iso(), method, path, result))

    def state(self, **updates):
        allowed = {"process_state", "session_state", "ppi_auth", "heartbeat_at",
                   "last_market_data_at", "real_orders_sent", "http_allowed",
                   "http_blocked", "detail"}
        updates = {k: v for k, v in updates.items() if k in allowed}
        if not updates:
            return
        sql = ",".join(f"{k}=?" for k in updates)
        with self.connect() as c:
            c.execute(f"UPDATE observer_state SET {sql} WHERE id=1", tuple(updates.values()))

    def add_quote(self, q: Quote):
        with self.connect() as c:
            c.execute("""INSERT INTO market_snapshots VALUES(NULL,?,?,?,?,?,?,?,?,?,?)""",
                      (SOURCE, q.observed_at, q.symbol, q.asset_class, q.settlement,
                       str(q.last), str(q.bid), str(q.ask), str(q.bid_size), str(q.ask_size)))

    def prices(self, symbol: str, limit=30):
        with self.connect() as c:
            rows = c.execute("SELECT last FROM market_snapshots WHERE symbol=? ORDER BY id DESC LIMIT ?",
                             (symbol, limit)).fetchall()
        return [D(r[0]) for r in reversed(rows)]

    def open_position(self, symbol: str):
        with self.connect() as c:
            row = c.execute("SELECT * FROM paper_positions WHERE symbol=? AND status='OPEN'",
                            (symbol,)).fetchone()
        return dict(row) if row else None

    def open_positions(self):
        with self.connect() as c:
            return [dict(r) for r in c.execute(
                "SELECT * FROM paper_positions WHERE status='OPEN' ORDER BY opened_at")]

    def recent_closed(self, limit=50):
        with self.connect() as c:
            return [dict(r) for r in c.execute(
                "SELECT * FROM paper_positions WHERE status='CLOSED' ORDER BY closed_at DESC LIMIT ?",
                (limit,))]

    def record_decision(self, key: str, q: Quote, action: str, score: Decimal,
                        reason: str, features: dict) -> bool:
        try:
            with self.connect() as c:
                c.execute("""INSERT INTO paper_decisions VALUES(NULL,?,?,?,?,?,?,?,?,?)""",
                          (SOURCE, STRATEGY_VERSION, key, q.observed_at, q.symbol,
                           action, str(score), reason, json.dumps(features, ensure_ascii=False)))
            return True
        except sqlite3.IntegrityError:
            return False

    def event(self, kind: str, detail: str, paper_id=None):
        with self.connect() as c:
            c.execute("INSERT INTO paper_events VALUES(NULL,?,?,?,?,?)",
                      (now_iso(), SOURCE, kind, paper_id, detail[:1000]))


class PaperBroker:
    def __init__(self, store: PaperStore, initial_cash="1000000",
                 risk_pct="0.005", max_positions=3, fee_rate="0.00605",
                 slippage_bps="2", participation="0.10"):
        self.store = store
        self.initial_cash = D(initial_cash)
        self.risk_pct = D(risk_pct)
        self.max_positions = int(max_positions)
        self.fee_rate = D(fee_rate)
        self.slippage = D(slippage_bps) / D(10000)
        self.participation = D(participation)

    def _cost(self, price, qty, asset_class="ACCIONES"):
        """Costo de una punta; el spread ya vive en bid/ask y no se duplica."""
        rate = self.fee_rate
        try:
            import au_fee_schedule
            rate = D(au_fee_schedule.costo_por_tramo(asset_class))
        except Exception:
            pass
        return (D(price) * D(qty) * rate).quantize(Decimal("0.01"))

    def _cash(self):
        closed = self.store.recent_closed(100000)
        realized = sum((D(r.get("net_pnl")) for r in closed), ZERO)
        committed = sum((D(p["entry_price"]) * D(p["quantity"]) + D(p["entry_cost"])
                         for p in self.store.open_positions()), ZERO)
        return self.initial_cash + realized - committed

    def threshold(self):
        closed = self.store.recent_closed(20)
        if len(closed) < 5:
            return D("0.62")
        wins = sum(1 for p in closed if D(p.get("net_pnl")) > 0)
        rate = D(wins) / D(len(closed))
        return D("0.67") if rate < D("0.45") else D("0.58") if rate > D("0.65") else D("0.62")

    def decide(self, q: Quote):
        values = self.store.prices(q.symbol, 20)
        if len(values) < 8:
            return "HOLD", D("0"), f"Aprendiendo serie: {len(values)}/8 muestras", {"samples": len(values)}
        short = sum(values[-3:], ZERO) / D(3)
        long = sum(values[-8:], ZERO) / D(8)
        momentum = (short / long - 1) if long else ZERO
        spread = (q.ask / q.bid - 1) if q.bid else D("99")
        score = max(ZERO, min(D(1), D("0.5") + momentum * D(40) - spread * D(10)))
        features = {"sma3": str(short), "sma8": str(long), "momentum": str(momentum),
                    "spread": str(spread), "samples": len(values),
                    "paper_threshold": str(self.threshold())}
        if q.bid <= 0 or q.ask <= 0 or q.ask_size <= 0:
            return "HOLD", score, "Puntas o profundidad insuficientes", features
        if spread > D("0.02"):
            return "HOLD", score, "Spread superior al 2%", features
        if score < self.threshold():
            return "HOLD", score, "Score paper debajo del umbral adaptativo", features
        return "BUY", score, "Momentum positivo y friccion admisible", features

    def on_quote(self, q: Quote):
        if self._maybe_close(q):
            return
        if self.store.open_position(q.symbol):
            return
        action, score, reason, features = self.decide(q)
        bucket = q.observed_at[:16]
        key = f"{STRATEGY_VERSION}:{q.symbol}:{bucket}:{action}"
        if not self.store.record_decision(key, q, action, score, reason, features):
            return
        self.store.event("DECISION_PAPER", f"{q.symbol} {action}: {reason}")
        if action == "BUY":
            self._open(q, score, features)

    def _open(self, q: Quote, score: Decimal, features: dict):
        if len(self.store.open_positions()) >= self.max_positions:
            self.store.event("REJECTED_PAPER", f"{q.symbol}: maximo de posiciones paper")
            return
        entry = (q.ask * (1 + self.slippage)).quantize(Decimal("0.0001"))
        stop = entry * D("0.98")
        target = entry * D("1.035")
        unit_risk = entry - stop + self._cost(entry, 1, q.asset_class)
        by_risk = (self.initial_cash * self.risk_pct / unit_risk).to_integral_value(ROUND_DOWN)
        by_cash = (max(ZERO, self._cash()) / entry).to_integral_value(ROUND_DOWN)
        by_book = (q.ask_size * self.participation).to_integral_value(ROUND_DOWN)
        qty = min(by_risk, by_cash, by_book)
        if qty < 1:
            self.store.event("REJECTED_PAPER", f"{q.symbol}: capital/liquidez insuficiente")
            return
        paper_id = "PAPER-" + uuid.uuid4().hex
        cost = self._cost(entry, qty, q.asset_class)
        with self.store.connect() as c:
            c.execute("""INSERT INTO paper_positions
              (paper_id,source,strategy_version,symbol,asset_class,settlement,status,
               quantity,entry_price,entry_cost,stop_price,target_price,opened_at,features_json)
              VALUES(?,?,?,?,?,?,'OPEN',?,?,?,?,?,?,?)""",
              (paper_id, SOURCE, STRATEGY_VERSION, q.symbol, q.asset_class, q.settlement,
               str(qty), str(entry), str(cost), str(stop), str(target), q.observed_at,
               json.dumps(features, ensure_ascii=False)))
            c.execute("INSERT INTO paper_fills VALUES(NULL,?,?,?,?,?,?,?,?)",
                      (paper_id, SOURCE, "BUY_SIMULATED", q.observed_at, str(qty),
                       str(entry), str(cost), str(entry-q.ask)))
            c.execute("INSERT INTO paper_learning_samples VALUES(?,?,?,?,?,?,?,?,?)",
                      (paper_id, SOURCE, STRATEGY_VERSION, q.observed_at,
                       json.dumps(features, ensure_ascii=False), None, None, None, None))
            c.execute("INSERT INTO paper_events VALUES(NULL,?,?,?,?,?)",
                      (q.observed_at, SOURCE, "PAPER_FILLED_BUY", paper_id,
                       f"Compra simulada {qty} {q.symbol} @ {entry}"))

    def _maybe_close(self, q: Quote):
        p = self.store.open_position(q.symbol)
        if not p or q.bid <= 0 or q.bid_size <= 0:
            return False
        reason = None
        if q.bid <= D(p["stop_price"]):
            reason = "STOP_PAPER"
        elif q.bid >= D(p["target_price"]):
            reason = "TAKE_PROFIT_PAPER"
        else:
            try:
                opened = datetime.fromisoformat(p["opened_at"])
                age = (datetime.fromisoformat(q.observed_at) - opened).total_seconds() / 60
                if age >= int(os.getenv("PAPER_MAX_HOLD_MINUTES", "180")):
                    reason = "MAX_HOLD_PAPER"
            except Exception:
                pass
        if reason:
            self._close(p, q, reason)
            return True
        return False

    def _close(self, p: dict, q: Quote, reason: str):
        qty = D(p["quantity"])
        exit_price = (q.bid * (1 - self.slippage)).quantize(Decimal("0.0001"))
        exit_cost = self._cost(exit_price, qty, p.get("asset_class", "ACCIONES"))
        gross = (exit_price - D(p["entry_price"])) * qty
        net = gross - D(p["entry_cost"]) - exit_cost
        invested = D(p["entry_price"]) * qty
        ret = (net / invested * 100) if invested else ZERO
        try:
            duration = int((datetime.fromisoformat(q.observed_at) -
                            datetime.fromisoformat(p["opened_at"])).total_seconds() / 60)
        except Exception:
            duration = 0
        outcome = "WIN" if net > 0 else "LOSS" if net < 0 else "FLAT"
        with self.store.connect() as c:
            c.execute("""UPDATE paper_positions SET status='CLOSED',closed_at=?,exit_price=?,
                         exit_cost=?,gross_pnl=?,net_pnl=?,close_reason=? WHERE paper_id=?""",
                      (q.observed_at, str(exit_price), str(exit_cost), str(gross), str(net),
                       reason, p["paper_id"]))
            c.execute("INSERT INTO paper_fills VALUES(NULL,?,?,?,?,?,?,?,?)",
                      (p["paper_id"], SOURCE, "SELL_SIMULATED", q.observed_at, str(qty),
                       str(exit_price), str(exit_cost), str(q.bid-exit_price)))
            c.execute("""UPDATE paper_learning_samples SET label_timestamp=?,net_return_pct=?,
                         outcome=?,duration_minutes=? WHERE paper_id=?""",
                      (q.observed_at, str(ret), outcome, duration, p["paper_id"]))
            c.execute("INSERT INTO paper_events VALUES(NULL,?,?,?,?,?)",
                      (q.observed_at, SOURCE, "PAPER_FILLED_SELL", p["paper_id"],
                       f"Venta simulada {qty} {q.symbol} @ {exit_price}; PnL neto {net}"))

    def mark_equity(self, quotes: dict):
        exposure = unrealized = ZERO
        for p in self.store.open_positions():
            q = quotes.get(p["symbol"])
            mark = q.bid if q else D(p["entry_price"])
            qty = D(p["quantity"])
            exposure += mark * qty
            unrealized += (mark - D(p["entry_price"])) * qty - D(p["entry_cost"])
        realized = sum((D(p.get("net_pnl")) for p in self.store.recent_closed(100000)), ZERO)
        cash = self.initial_cash + realized - sum(
            (D(p["entry_price"])*D(p["quantity"])+D(p["entry_cost"])
             for p in self.store.open_positions()), ZERO)
        equity = cash + exposure
        with self.store.connect() as c:
            c.execute("INSERT INTO paper_equity VALUES(NULL,?,?,?,?,?,?,?)",
                      (now_iso(), SOURCE, str(cash), str(exposure), str(unrealized),
                       str(realized), str(equity)))
