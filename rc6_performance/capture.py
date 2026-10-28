"""Bounded read-only source capture; evidence has its own database and writer lock.

Starts at the current tail, never reimports production history. Source reads
require WAL, query_only, a statement deadline, row/byte limits and PAPER safety.
"""
import hashlib
import json
import logging
import os
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from .common import canonical, digest, stamp

LOG = logging.getLogger("rc6_performance")
TABLES = ("decision_evidence_snapshots", "market_snapshots", "paper_fills", "paper_events", "universe_cycle_metrics")
CONFIG_KEYS = ("PAPER_SIGNAL_MIN_SAMPLES", "PAPER_SIGNAL_WINDOW_MINUTES", "PAPER_BOOK_MAX_AGE_SECONDS",
               "PAPER_SCORE_THRESHOLD", "PAPER_STOP_LOSS_PCT", "PAPER_TARGET_GAIN_PCT", "PAPER_MAX_HOLD_MINUTES",
               "PAPER_ECONOMIC_GATE_MODE", "PAPER_AI_GATE_MODE", "PAPER_EOD_POLICY", "PAPER_INTRADAY_FEE_REBATE")


def capture_context():
    inputs = {key: os.environ.get(key) for key in CONFIG_KEYS}
    sha = os.environ.get("POROTA_BUILD_SHA")
    if not sha or len(sha) != 40 or any(c not in "0123456789abcdef" for c in sha):
        sha = "NO_VERIFICADO"
    return {"mode": "SHADOW", "decision_effect": "NONE", "real_order_routes": [],
            "capture_git_sha": sha, "configured_inputs_sha256": digest(inputs),
            "configuration_fingerprint": "NO_VERIFICADO",
            "note": "capture provenance does not rewrite historical decision provenance"}


class EvidenceStore:
    def __init__(self, path, *, maximum_bytes=128*1024*1024):
        self.path = Path(path).resolve()
        self.maximum_bytes = maximum_bytes
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            with closing(sqlite3.connect(self.path.as_uri()+"?mode=ro", uri=True, timeout=.005)) as existing:
                tables = {r[0] for r in existing.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables - {"events", "cursors", "state", "exit_probes"}:
                raise ValueError("SEPARATE_EVIDENCE_DATABASE_REQUIRED")
        with closing(self.connect()) as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript("""
                CREATE TABLE IF NOT EXISTS events(
                  id INTEGER PRIMARY KEY, event_key TEXT NOT NULL UNIQUE,
                  source_table TEXT NOT NULL, source_rowid INTEGER NOT NULL,
                  recorded_at TEXT NOT NULL, payload_sha256 TEXT NOT NULL, payload_json TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS cursors(
                  source_key TEXT NOT NULL, source_table TEXT NOT NULL, last_rowid INTEGER NOT NULL,
                  PRIMARY KEY(source_key,source_table));
                CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY,value_json TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS exit_probes(
                  paper_id TEXT NOT NULL, stage TEXT NOT NULL, at TEXT NOT NULL,
                  detail_json TEXT NOT NULL, PRIMARY KEY(paper_id,stage));
            """)

    def connect(self):
        c = sqlite3.connect(self.path, timeout=.005)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA busy_timeout=5")
        return c

    def at_capacity(self):
        return sum(p.stat().st_size for p in (self.path, Path(str(self.path)+"-wal"), Path(str(self.path)+"-shm")) if p.exists()) >= self.maximum_bytes

    def probe(self, paper_id, stage, at, detail):
        if self.at_capacity():
            return False
        try:
            stamp(at)
            with closing(self.connect()) as c, c:
                c.execute("INSERT OR IGNORE INTO exit_probes VALUES(?,?,?,?)",
                          (paper_id, stage, at, canonical(detail)))
            return True
        except (OSError, ValueError, sqlite3.Error):
            return False


class ExitTelemetry:
    """Optional diagnostics; failure has no authority over the exit executor."""
    def __init__(self, path):
        self.store = EvidenceStore(path)

    def record(self, paper_id, stage, at, cause=None):
        if not self.store.probe(paper_id, stage, at, {"cause": cause, "mode": "SHADOW", "real_order_routes": []}):
            LOG.warning("EXIT_TELEMETRY_DROPPED:stage=%s", stage)


class BoundedCapture:
    def __init__(self, source, evidence, *, rows_per_table=100, seconds=.15, maximum_payload_bytes=65536):
        self.source = Path(source).resolve()
        self.evidence = evidence
        if self.source == evidence.path:
            raise ValueError("SEPARATE_EVIDENCE_DATABASE_REQUIRED")
        if not 1 <= rows_per_table <= 500 or not 0 < seconds <= 1:
            raise ValueError("BOUNDED_SOURCE_BUDGET_REQUIRED")
        self.rows, self.seconds, self.max_payload = rows_per_table, seconds, maximum_payload_bytes
        stat = self.source.stat()
        self.source_key = digest([str(self.source), stat.st_dev, stat.st_ino])

    def read_batch(self):
        if self.evidence.at_capacity():
            return {"status": "EVIDENCE_CAPACITY_REACHED", "rows": 0}
        deadline = time.monotonic() + self.seconds
        data, cursors, starts, dropped, serialized_bytes = [], {}, {}, 0, 0
        with closing(self.evidence.connect()) as target:
            prior = {r["source_table"]: r["last_rowid"] for r in target.execute(
                "SELECT * FROM cursors WHERE source_key=?", (self.source_key,))}
        with closing(sqlite3.connect(self.source.as_uri()+"?mode=ro", uri=True, timeout=.005)) as source:
            source.row_factory = sqlite3.Row
            source.execute("PRAGMA query_only=ON")
            source.execute("PRAGMA busy_timeout=5")
            source.set_progress_handler(lambda: int(time.monotonic() > deadline), 200)
            if source.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal":
                raise ValueError("SOURCE_WAL_REQUIRED")
            source.execute("BEGIN")
            state = source.execute("SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchone()
            if not state or tuple(state) != ("PRODUCTION_PAPER", 0):
                raise ValueError("PAPER_SOURCE_SAFETY_REQUIRED")
            tables = {r[0] for r in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table in TABLES:
                if table not in tables:
                    continue
                if table not in prior:
                    tail = source.execute(f'SELECT COALESCE(MAX(rowid),0) FROM "{table}"').fetchone()[0]
                    cursors[table] = tail
                    starts[table] = {"status": "START_AT_CURRENT_TAIL", "rowid": tail}
                    continue
                if table == "paper_fills":
                    query = """SELECT f.rowid AS _rowid,f.*,p.symbol,p.asset_class,p.settlement,p.currency,p.market,
                        p.quantity AS position_quantity,p.strategy_version,p.entry_price,p.entry_cost,p.features_json,
                        p.status AS position_status,p.closed_at,p.exit_cost,p.gross_pnl,p.net_pnl,p.close_reason
                        FROM paper_fills f JOIN paper_positions p USING(paper_id)
                        WHERE f.rowid>? ORDER BY f.rowid LIMIT ?"""
                else:
                    query = f'SELECT rowid AS _rowid,* FROM "{table}" WHERE rowid>? ORDER BY rowid LIMIT ?'
                rows = source.execute(query, (prior[table], self.rows)).fetchall()
                cursors[table] = prior[table]
                for row in rows:
                    payload = dict(row)
                    if table == "decision_evidence_snapshots":
                        actual = hashlib.sha256(payload["payload_json"].encode()).hexdigest()
                        if actual != payload["payload_sha256"]:
                            raise ValueError("IMMUTABLE_DECISION_HASH_MISMATCH")
                    text = canonical(payload)
                    if len(text.encode()) > self.max_payload:
                        dropped += 1
                        cursors[table] = row["_rowid"]
                        continue
                    if serialized_bytes + len(text.encode()) > 1024*1024:
                        break
                    serialized_bytes += len(text.encode())
                    cursors[table] = row["_rowid"]
                    data.append((digest([self.source_key, table, row["_rowid"]]), table, row["_rowid"], text))
            source.rollback()
        at = datetime.now(timezone.utc).isoformat()
        report = {"status": "CAPTURED", "rows": len(data), "oversize_rows_not_captured": dropped,
                  "started_at_tail": starts, "source_key": self.source_key,
                  "source_read_seconds": self.seconds-(deadline-time.monotonic()), "context": capture_context()}
        with closing(self.evidence.connect()) as target, target:
            for event_key, table, rowid, payload in data:
                target.execute("INSERT OR IGNORE INTO events VALUES(NULL,?,?,?,?,?,?)",
                               (event_key, table, rowid, at, hashlib.sha256(payload.encode()).hexdigest(), payload))
            for table, cursor in cursors.items():
                target.execute("INSERT OR REPLACE INTO cursors VALUES(?,?,?)", (self.source_key, table, cursor))
            target.execute("INSERT OR REPLACE INTO state VALUES('last_capture',?)", (canonical(report),))
        return report


def run_worker(source, stop):
    import fcntl
    os.nice(10)
    evidence_path = str(source)+".performance.sqlite"
    # Locks only this new evidence writer, never the trading DB or PPI Watch.
    with open(evidence_path+".writer.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        capture = BoundedCapture(source, EvidenceStore(evidence_path))
        last_report = 0.0
        while not stop.is_set():
            try:
                report = capture.read_batch()
                if report["status"] != "CAPTURED":
                    LOG.warning("PERFORMANCE_CAPTURE:%s", report["status"])
                elif time.monotonic()-last_report >= 300:
                    from .report import evidence_report
                    snapshot = evidence_report(evidence_path, event_limit=1000)
                    # Bounded atomic report, outside the trading DB.
                    report_path = Path(evidence_path+".latest.json")
                    temporary = report_path.with_name(report_path.name+".tmp")
                    temporary.write_text(canonical(snapshot)+"\n")
                    os.replace(temporary, report_path)
                    last_report = time.monotonic()
            except (sqlite3.Error, ValueError, OSError) as exc:
                LOG.warning("PERFORMANCE_CAPTURE_UNVERIFIED:%s", type(exc).__name__)
            stop.wait(5)
