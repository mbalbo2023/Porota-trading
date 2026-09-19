"""Motor paper independiente para observar PPI Produccion sin operar cuentas.

Usa exclusivamente Decimal y una base SQLite separada. Cada operacion queda
marcada PRODUCTION_PAPER/SIMULATED y los identificadores comienzan con PAPER-.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone, timedelta
from decimal import Decimal, ROUND_DOWN
from typing import Optional
from zoneinfo import ZoneInfo

from bs_instrument_contracts import (CASH_CURRENCIES, InstrumentContract, SPOT_FAMILIES,
                                     aware_datetime, cash_currency, decimal_value, family_name)
from bt_caucion_paper import (CaucionBook, init_schema as init_financial_schema,
                              pending_proceeds, record_sale)
import cc_spot_liquidity as spot_liquidity
import cd_spot_ledger as spot_ledger

# RC6 operational scope: candidates outside shares/CEDEARs are observed only.
OPERATIONAL_FAMILIES = frozenset(
    part.strip().upper() for part in os.getenv("POROTA_OPERATIONAL_FAMILIES", "ACCIONES,CEDEARS").split(",")
    if part.strip()
)

def _family_operable(family: str) -> bool:
    return str(family or "").upper() in OPERATIONAL_FAMILIES


SOURCE = "PRODUCTION_PAPER"
STRATEGY_VERSION = "paper-momentum-v17.0-rc3-hf4"
# Snapshot contemporáneo de evidencia por decisión. No es un replay, no consulta
# fuentes externas y no tiene autoridad para modificar la acción PAPER.
DECISION_EVIDENCE_SCHEMA = "rc6.decision-inputs.v1"
EVIDENCE_SECRET_MARKERS = (
    "secret", "password", "token", "api_key", "apikey", "authorization", "cookie",
)
ZERO = Decimal("0")
TZ = ZoneInfo("America/Argentina/Buenos_Aires")


def D(value, default="0") -> Decimal:
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else Decimal(default)
    except Exception:
        return Decimal(default)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


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
    contract: Optional[InstrumentContract] = None
    currency: Optional[str] = None
    market: Optional[str] = None
    metadata_source: Optional[str] = None
    opening_block_reason: str = ""
    book_at: Optional[str] = None
    trade_at: Optional[str] = None
    last_kind: str = "UNKNOWN"

    def time_error(self, as_of, *, require_trade=False, max_age_seconds=120,
                   max_trade_age_seconds=None):
        """Hora del proveedor y recepción, nunca reemplazadas por la hora local.

        Un libro fresco sirve para salir aunque el último negocio sea antiguo.
        Para una entrada por señal, también se exige último negocio fresco.
        """
        try:
            at = aware_datetime(as_of)
            received = aware_datetime(self.observed_at, "recepción")
            if not 0 <= (at - received).total_seconds() <= max_age_seconds:
                return "RECEIPT_STALE_OR_FUTURE"
            fields = [(self.book_at, "BOOK", max_age_seconds)]
            if require_trade:
                if self.last_kind != "TRADE":
                    return "LAST_IS_NOT_A_TRADE"
                fields.append((self.trade_at, "TRADE",
                               max_age_seconds if max_trade_age_seconds is None
                               else max_trade_age_seconds))
            for value, label, allowed_age in fields:
                if not value:
                    return label + "_TIME_MISSING"
                source_at = aware_datetime(value, label)
                if source_at > received or source_at > at:
                    return label + "_TIME_FUTURE"
                if (at - source_at).total_seconds() > allowed_age:
                    return label + "_STALE"
        except (ValueError, TypeError):
            return "INVALID_OR_NAIVE_TIMESTAMP"
        return ""

    def monetary_identity(self):
        currency = cash_currency(self.currency)
        market = str(self.market or "").strip().upper()
        if not market or market == "UNKNOWN":
            raise ValueError("Falta mercado confirmado de la cotización")
        if self.contract and (self.contract.currency != currency or self.contract.market != market):
            raise ValueError("Moneda/mercado de la cotización contradice el contrato")
        return currency, market


def _evidence_safe(value, *, field_name=""):
    """Normaliza evidencia para hash y persiste sin secretos ni objetos mutables."""
    name = str(field_name or "").lower()
    if any(marker in name for marker in EVIDENCE_SECRET_MARKERS):
        return "[REDACTED]"
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {
            str(key): _evidence_safe(item, field_name=str(key))
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_evidence_safe(item) for item in value]
    if isinstance(value, set):
        return sorted(_evidence_safe(item) for item in value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def _decision_evidence_payload(q: Quote, decision_key: str, technical: str,
                               ai: str, patrimonial: str, final: str,
                               reason: str, paper_id, detail: dict) -> dict:
    """Capture only the data already in-memory at the gate write point.

    The collector remains the sole owner of market ingestion.  This payload is
    deliberately local and contemporaneous: missing fields remain missing.
    """
    contract = asdict(q.contract) if q.contract is not None else None
    return _evidence_safe({
        "schema": DECISION_EVIDENCE_SCHEMA,
        "decision_key": decision_key,
        "captured_at": q.observed_at,
        "decision": {
            "symbol": q.symbol,
            "technical_gate": technical,
            "ai_gate": ai,
            "patrimonial_gate": patrimonial,
            "final_result": final,
            "reason": str(reason),
            "paper_id": paper_id,
        },
        "quote_used": {
            "symbol": q.symbol,
            "asset_class": q.asset_class,
            "settlement": q.settlement,
            "currency": q.currency,
            "market": q.market,
            "last": q.last,
            "bid": q.bid,
            "ask": q.ask,
            "bid_size": q.bid_size,
            "ask_size": q.ask_size,
            "observed_at": q.observed_at,
            "book_at": q.book_at,
            "trade_at": q.trade_at,
            "last_kind": q.last_kind,
            "metadata_source": q.metadata_source,
            "financial_contract": contract,
        },
        "runtime": {
            "source": SOURCE,
            "strategy_version": STRATEGY_VERSION,
            "execution_mode": "PRODUCTION_PAPER_SIMULATED",
            "real_money_authorized": False,
        },
        "inputs_used": detail,
    })


class PaperStore:
    def __init__(self, path: str):
        self.path = path
        new_file = not os.path.exists(path)
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        # El runtime padre migra una única vez antes de crear hijos. Repetir
        # DDL/índices grandes desde scanner, velas y salida puede bloquear SQLite.
        runtime_schema_ready = os.getenv("POROTA_RUNTIME_SCHEMA_READY", "").strip() == "1"
        if new_file or not runtime_schema_ready:
            self.init_db()
        if new_file:
            from cg_paper_workspace import mark_new_database
            with self.connect() as c:
                mark_new_database(c)

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
            CREATE TABLE IF NOT EXISTS paper_position_marks(
              paper_id TEXT PRIMARY KEY, mark_price TEXT NOT NULL,
              book_at TEXT NOT NULL, marked_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS paper_valuation_quality(
              currency TEXT PRIMARY KEY, measured_at TEXT NOT NULL,
              state TEXT NOT NULL, stale_positions INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS ai_shadow_evaluations(
              id INTEGER PRIMARY KEY AUTOINCREMENT, evaluated_at TEXT NOT NULL,
              symbol TEXT NOT NULL, decision TEXT NOT NULL, score TEXT NOT NULL,
              veto_risk INTEGER NOT NULL, reason TEXT NOT NULL, model TEXT NOT NULL,
              input_json TEXT NOT NULL, raw_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS http_audit(
              id INTEGER PRIMARY KEY AUTOINCREMENT, happened_at TEXT NOT NULL,
              method TEXT NOT NULL, path TEXT NOT NULL, result TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS trade_gate_evaluations(
              id INTEGER PRIMARY KEY AUTOINCREMENT, evaluated_at TEXT NOT NULL,
              decision_key TEXT NOT NULL UNIQUE, symbol TEXT NOT NULL,
              technical_gate TEXT NOT NULL, ai_gate TEXT NOT NULL,
              patrimonial_gate TEXT NOT NULL, final_result TEXT NOT NULL,
              reason TEXT NOT NULL, paper_id TEXT, detail_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS decision_evidence_snapshots(
              decision_key TEXT PRIMARY KEY, captured_at TEXT NOT NULL,
              schema_version TEXT NOT NULL, payload_sha256 TEXT NOT NULL,
              payload_json TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS idx_decision_evidence_captured
              ON decision_evidence_snapshots(captured_at, decision_key);
            INSERT OR IGNORE INTO observer_state(id,mode,process_state,session_state,ppi_auth)
              VALUES(1,'PRODUCTION_PAPER','STOPPED','UNKNOWN','NOT_ATTEMPTED');
            """)
            # No reinterpretar históricos: los fills anteriores gastaron la
            # caja ARS del motor viejo. Se etiqueta esa suposición explícita.
            c.execute("BEGIN IMMEDIATE")
            for table, additions in {
                "paper_positions": {"currency": "TEXT NOT NULL DEFAULT 'ARS'",
                    "market": "TEXT NOT NULL DEFAULT 'BYMA'",
                    "currency_source": "TEXT NOT NULL DEFAULT 'LEGACY_ASSUMED_ARS'"},
                "market_snapshots": {"currency": "TEXT NOT NULL DEFAULT 'UNKNOWN'",
                    "market": "TEXT NOT NULL DEFAULT 'UNKNOWN'",
                    "book_at": "TEXT", "trade_at": "TEXT",
                    "last_kind": "TEXT NOT NULL DEFAULT 'UNKNOWN'",
                    "contract_json": "TEXT", "metadata_source": "TEXT",
                    "opening_block_reason": "TEXT NOT NULL DEFAULT ''"},
            }.items():
                columns = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
                for column, definition in additions.items():
                    if column not in columns:
                        c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            c.execute("CREATE INDEX IF NOT EXISTS idx_snapshot_identity ON market_snapshots(symbol,asset_class,settlement,currency,market,id)")
        spot_ledger.init_schema(self)
        # Fills PAPER consumen profundidad por snapshot. Esta tabla debe existir
        # también en una base nueva creada por los tests/runtime, antes del primer fill.
        spot_liquidity.init_schema(self)
        init_financial_schema(self)
        from bm_exit_supervisor import init_schema as init_exit_schema
        init_exit_schema(self)
        from bn_telegram_bus import init_schema as init_outbox_schema
        from bw_daily_risk import init_schema as init_risk_schema
        init_outbox_schema(self)
        init_risk_schema(self)
        from bl_candle_engine import init_schema as init_candle_schema
        init_candle_schema(self)

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
        try:
            currency, market = q.monetary_identity()
        except ValueError:
            currency, market = "UNKNOWN", q.market or "UNKNOWN"
        with self.connect() as c:
            c.execute("""INSERT INTO market_snapshots
              (source,observed_at,symbol,asset_class,settlement,last,bid,ask,bid_size,ask_size,currency,market,
               book_at,trade_at,last_kind,contract_json,metadata_source,opening_block_reason)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (SOURCE, q.observed_at, q.symbol, q.asset_class, q.settlement,
                       str(q.last), str(q.bid), str(q.ask), str(q.bid_size), str(q.ask_size),
                       currency, market, q.book_at, q.trade_at, q.last_kind,
                       json.dumps(asdict(q.contract), default=str) if q.contract else None,
                       q.metadata_source, q.opening_block_reason))

    def latest_quote(self, position):
        # La última respuesta recibida gana: una respuesta vacía/no válida no
        # permite rescatar silenciosamente una punta anterior que ya desapareció.
        with self.connect() as c:
            row = c.execute("""SELECT * FROM market_snapshots WHERE symbol=? AND asset_class=?
              AND settlement=? ORDER BY id DESC LIMIT 1""",
              tuple(position[k] for k in ("symbol", "asset_class", "settlement"))).fetchone()
        if not row:
            return None
        values = dict(row)
        spec = json.loads(values["contract_json"]) if values["contract_json"] else None
        return Quote(**{k: values[k] for k in ("symbol", "asset_class", "settlement", "observed_at", "currency", "market",
                                             "book_at", "trade_at", "last_kind", "metadata_source", "opening_block_reason")},
                     **{k: D(values[k]) for k in ("last", "bid", "ask", "bid_size", "ask_size")},
                     contract=InstrumentContract(**spec) if spec else None)

    def signal_prices(self, q, as_of, limit=20, window_minutes=30):
        """Negocios distintos, disponibles al decidir, de una sola identidad.

        Son muestras de último negocio; no se presentan como velas ni volumen.
        """
        end = aware_datetime(as_of)
        currency, market = q.monetary_identity()
        start = (end - timedelta(minutes=window_minutes)).isoformat()
        with self.connect() as c:
            rows = c.execute("""SELECT trade_at,last,observed_at FROM market_snapshots
              WHERE symbol=? AND asset_class=? AND settlement=? AND currency=? AND market=?
                AND last_kind='TRADE' AND trade_at IS NOT NULL
                AND julianday(trade_at)>=julianday(?) AND julianday(trade_at)<=julianday(?)
                AND julianday(observed_at)<=julianday(?)
              ORDER BY julianday(trade_at) DESC,id DESC""",
              (q.symbol,q.asset_class,q.settlement,currency,market,start,end.isoformat(),end.isoformat())).fetchall()
        unique = {}
        for row in rows:
            try:
                source_at = aware_datetime(row["trade_at"])
                if source_at > aware_datetime(row["observed_at"]):
                    continue
                if D(row["last"]) > 0:
                    unique.setdefault(source_at, D(row["last"]))
            except (ValueError, TypeError):
                continue
            if len(unique) >= limit:
                break
        return list(reversed(list(unique.values())))

    def prices(self, symbol: str, limit=30, *, asset_class=None, settlement=None, currency=None, market=None):
        filters, params = ["symbol=?"], [symbol]
        if asset_class is not None:
            filters.append("asset_class=?")
            params.append(asset_class)
        if settlement is not None:
            filters.append("settlement=?")
            params.append(settlement)
        for column, value in (("currency", currency), ("market", market)):
            if value is not None:
                filters.append(column + "=?")
                params.append(value)
        with self.connect() as c:
            rows = c.execute("SELECT last FROM market_snapshots WHERE " +
                             " AND ".join(filters) + " ORDER BY id DESC LIMIT ?",
                             (*params, limit)).fetchall()
        return [D(r[0]) for r in reversed(rows)]

    def open_position(self, symbol: str):
        with self.connect() as c:
            c.execute('BEGIN')
            row = c.execute("SELECT * FROM paper_positions WHERE symbol=? AND status='OPEN'",
                            (symbol,)).fetchone()
            return spot_ledger.partition(c,row)[0] if row else None

    def open_positions(self):
        with self.connect() as c:
            c.execute('BEGIN')
            return [spot_ledger.partition(c,r)[0] for r in c.execute(
                "SELECT * FROM paper_positions WHERE status='OPEN' ORDER BY opened_at")]

    def exit_positions(self):
        """Snapshot exclusivo de supervisión: abiertas válidas e inconsistentes.

        No sustituye open_positions/positions_at para caja, riesgo o informes.
        Un error de datos de una posición no ciega las salidas de las demás;
        errores de base/esquema se propagan, nunca producen una cartera vacía.
        """
        opened, invalid = [], []
        with self.connect() as c:
            c.execute('BEGIN')
            # Un estado desconocido tampoco puede desaparecer de la vigilancia
            # como si fuera un cierre; sólo CLOSED queda fuera de este reloj.
            for row in c.execute("SELECT * FROM paper_positions WHERE status<>'CLOSED' ORDER BY opened_at,paper_id"):
                try:
                    remaining = spot_ledger.partition(c,row)[0]
                    if remaining is None:
                        raise ValueError('Abierta sin remanente conciliado')
                    opened.append(remaining)
                except (ValueError,TypeError,ArithmeticError) as exc:
                    invalid.append((dict(row),f'SPOT_LEDGER_INVALID: {type(exc).__name__}: {exc}'[:240]))
        return opened, invalid

    def recent_closed(self, limit=50, *, strategy_version=None, closed_before=None):
        with self.connect() as c:
            c.execute('BEGIN')
            if strategy_version is not None:
                rows = [dict(r) for r in c.execute("""SELECT * FROM paper_positions
                  WHERE status='CLOSED' AND strategy_version=?
                  AND (? IS NULL OR julianday(closed_at)<=julianday(?)) ORDER BY closed_at DESC LIMIT ?""",
                  (strategy_version, closed_before, closed_before, limit))]
            else:
                rows = [dict(r) for r in c.execute(
                    "SELECT * FROM paper_positions WHERE status='CLOSED' ORDER BY closed_at DESC LIMIT ?",
                    (limit,))]
            for row in rows:
                spot_ledger.partition(c,row)
            return rows

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

    def record_ai(self, q: Quote, result: dict, features: dict):
        with self.connect() as c:
            c.execute("""INSERT INTO ai_shadow_evaluations
              (evaluated_at,symbol,decision,score,veto_risk,reason,model,input_json,raw_json)
              VALUES(?,?,?,?,?,?,?,?,?)""",
              (q.observed_at, q.symbol, str(result.get("decision", "VETO")),
               str(result.get("score", 0)), int(bool(result.get("veto", True))),
               str(result.get("reason", ""))[:800], str(result.get("model", ""))[:120],
               json.dumps(features, ensure_ascii=False, default=str),
               json.dumps(result.get("raw", result), ensure_ascii=False, default=str)[:8000]))

    def record_gates(self, q: Quote, decision_key: str, technical: str,
                     ai: str, patrimonial: str, final: str, reason: str,
                     paper_id=None, detail=None):
        """Conserva la secuencia completa; APPROVE de IA no equivale a fill."""
        detail = detail or {}
        economics = detail.get("economics") if isinstance(detail, dict) else None
        economics_mode = str(detail.get("economics_mode") or "BINDING").upper() if isinstance(detail, dict) else "BINDING"
        economic_violation = (economics_mode == "BINDING" and
                              (not isinstance(economics, dict) or economics.get("passed") is not True))
        contradiction = (final == "OPENED_SIMULATED" and
            (technical != "APPROVE" or patrimonial != "APPROVE" or economic_violation))
        if contradiction:
            # Nunca reescribir el fill: conservar la anomalía de forma
            # explícita para que introspección bloquee el GO.
            reason = "INVARIANTE_DE_PORTONES_INCUMPLIDA: " + str(reason)
        evidence = _decision_evidence_payload(
            q, decision_key, technical, ai, patrimonial, final, reason, paper_id, detail)
        evidence_json = json.dumps(
            evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        evidence_hash = hashlib.sha256(evidence_json.encode("utf-8")).hexdigest()
        with self.connect() as c:
            c.execute("""INSERT OR REPLACE INTO trade_gate_evaluations
              (evaluated_at,decision_key,symbol,technical_gate,ai_gate,
               patrimonial_gate,final_result,reason,paper_id,detail_json)
              VALUES(?,?,?,?,?,?,?,?,?,?)""",
              (q.observed_at, decision_key, q.symbol, technical, ai,
               patrimonial, final, str(reason)[:1000], paper_id,
               json.dumps(detail, ensure_ascii=False, default=str)[:8000]))
            # First capture wins. A later retry may refresh the mutable gate
            # projection, but it must never rewrite evidence from the instant
            # the decision was made.
            c.execute("""INSERT OR IGNORE INTO decision_evidence_snapshots
              (decision_key,captured_at,schema_version,payload_sha256,payload_json)
              VALUES(?,?,?,?,?)""",
              (decision_key, q.observed_at, DECISION_EVIDENCE_SCHEMA,
               evidence_hash, evidence_json))
            if contradiction:
                c.execute("INSERT INTO paper_events VALUES(NULL,?,?,?,?,?)",
                          (q.observed_at, SOURCE, "GATE_INVARIANT_ERROR", paper_id,
                           str(reason)[:1000]))


class PaperBroker:
    def __init__(self, store: PaperStore, initial_cash="1000000",
                 risk_pct="0.005", max_positions=3, fee_rate="0.00605",
                 slippage_bps="2", participation="0.10",
                 max_position_pct="0.25", max_total_exposure_pct="0.60",
                 ai_gate=None, require_ai=False, context_fn=None,
                 initial_cash_usd="0", initial_cash_by_currency=None,
                 clock_fn=None, session_policy=None, require_supervisor=False,
                 quote_max_age_seconds=120, trade_max_age_seconds=900,
                 signal_min_samples=8, signal_window_minutes=90,
                 score_threshold="0.62", ai_mode="BINDING",
                 economics_mode="SHADOW", min_net_reward_risk="1.20",
                 stop_loss_pct="0.02", target_gain_pct="0.035",
                 daily_loss_pct="2.5", daily_soft_stop_pct=None,
                 intraday_fee_rebate=None):
        self.store = store
        self.initial_cash = D(initial_cash)
        self.risk_pct = D(risk_pct)
        self.max_positions = int(max_positions)
        self.fee_rate = D(fee_rate)
        self.slippage = D(slippage_bps) / D(10000)
        self.participation = D(participation)
        self.max_position_pct = D(max_position_pct)
        self.max_total_exposure_pct = D(max_total_exposure_pct)
        self.ai_gate = ai_gate
        self.require_ai = bool(require_ai)
        self.context_fn = context_fn
        self.initial_cash_usd = D(initial_cash_usd)
        self.initial_balances = {key: ZERO for key in CASH_CURRENCIES}
        self.initial_balances.update(ARS=decimal_value(initial_cash, "capital ARS", nonnegative=True),
                                     USD=decimal_value(initial_cash_usd, "capital USD", nonnegative=True))
        for key, value in (initial_cash_by_currency or {}).items():
            self.initial_balances[cash_currency(key)] = decimal_value(value, "capital por moneda", nonnegative=True)
        self.cauciones = CaucionBook(store)
        # El esquema de liquidez se migra al iniciar el runtime. No se crea un
        # índice desde cada PaperBroker: scanner y supervisor arrancan en paralelo.
        # Sin reloj inyectado, llamadas directas son simulación por tiempo de
        # evento. El runtime vivo SIEMPRE inyecta reloj real y política de sesión.
        self.clock_fn = clock_fn
        self.session_policy = session_policy
        self.require_supervisor = require_supervisor
        self.quote_max_age_seconds = quote_max_age_seconds
        self.trade_max_age_seconds = trade_max_age_seconds
        self.signal_min_samples = max(3, int(signal_min_samples))
        self.signal_window_minutes = max(15, int(signal_window_minutes))
        self.score_threshold = decimal_value(score_threshold, "umbral de score", nonnegative=True)
        if self.score_threshold > 1:
            raise ValueError("El umbral de score debe estar entre 0 y 1")
        self.ai_mode = str(ai_mode or "BINDING").upper()
        self.economics_mode = str(economics_mode or "SHADOW").upper()
        if self.ai_mode not in {"BINDING", "SHADOW", "OFF"}:
            raise ValueError("PAPER_AI_GATE_MODE debe ser BINDING, SHADOW u OFF")
        if self.economics_mode not in {"BINDING", "SHADOW"}:
            raise ValueError("PAPER_ECONOMIC_GATE_MODE debe ser BINDING o SHADOW")
        self.min_net_reward_risk = decimal_value(min_net_reward_risk, "reward/risk neto", nonnegative=True)
        self.stop_loss_pct = decimal_value(stop_loss_pct, "stop", positive=True)
        self.target_gain_pct = decimal_value(target_gain_pct, "objetivo", positive=True)
        if intraday_fee_rebate is None:
            intraday_fee_rebate = os.getenv("PAPER_INTRADAY_FEE_REBATE", "true")
        requested_rebate = (intraday_fee_rebate if isinstance(intraday_fee_rebate, bool)
                            else str(intraday_fee_rebate).strip().lower()
                            in {"1", "true", "yes", "si", "sí"})
        # El beneficio solo puede anticiparse cuando el contrato obliga a
        # cerrar dentro de la jornada. Sin esa garantia se vuelve al costo
        # completo y conservador.
        self.intraday_fee_rebate = bool(
            requested_rebate and self.session_policy is not None
            and getattr(self.session_policy, "close_at_eod", False)
        )
        from bw_daily_risk import DailyRisk
        if daily_loss_pct is None:
            self.daily_risk = None
        else:
            # RC4: el runtime productivo pasa el soft-stop explícitamente desde env.
            # Un caller aislado que NO lo suministra conserva la semántica histórica:
            # DailyRisk usa el hard-stop como fallback interno pero lo marca como
            # soft_limit_explicit=False, evitando inventar una barrera distinta.
            hard_pct = decimal_value(daily_loss_pct, "pérdida diaria %", positive=True)
            self.daily_risk = DailyRisk(self, hard_pct, soft_limit_pct=daily_soft_stop_pct)

    def execution_time(self, q):
        return self.clock_fn() if self.clock_fn else q.observed_at

    def admission_error(self, q, at, *, connection=None):
        error = q.time_error(at, require_trade=True,
                             max_age_seconds=self.quote_max_age_seconds,
                             max_trade_age_seconds=self.trade_max_age_seconds)
        if error:
            return error
        if self.session_policy:
            error = self.session_policy.admission_error(q, at)
            if error:
                return error
        if self.require_supervisor:
            from bm_exit_supervisor import admission_error
            error = admission_error(self.store, at)
            if error:
                return error
        if self.daily_risk:
            try:
                currency, _ = q.monetary_identity()
            except ValueError:
                return 'DAILY_RISK_UNKNOWN_CURRENCY'
            return self.daily_risk.admission_error(currency,at,connection=connection,quotes={q.symbol:q})
        return ""

    def _cost(self, price, qty, asset_class="ACCIONES"):
        """Costo de una punta; el spread ya vive en bid/ask y no se duplica."""
        import au_fee_schedule
        family = family_name(asset_class)
        if family not in SPOT_FAMILIES:
            raise ValueError("La familia requiere un cálculo de costos específico")
        rate = D(au_fee_schedule.costo_por_tramo(family), "-1")
        if rate < 0:
            raise ValueError("tarifario no valido para el costo por tramo")
        return (D(price) * D(qty) * rate).quantize(Decimal("0.01"))

    def _leg_rate(self, asset_class, *, rebated=False):
        """Tasa de una punta; la reducida exige elegibilidad intradiaria."""
        import au_fee_schedule
        family = family_name(asset_class)
        if family not in SPOT_FAMILIES:
            raise ValueError("La familia requiere un cálculo de costos específico")
        if (rebated and self.intraday_fee_rebate
                and family in au_fee_schedule.FAMILIAS_CON_BONIFICACION_INTRADIARIA):
            rate = D(au_fee_schedule.costo_por_tramo_bonificado(family), "-1")
        else:
            rate = D(au_fee_schedule.costo_por_tramo(family), "-1")
        if rate < 0:
            raise ValueError("tarifario no valido para el costo por tramo")
        return rate

    @staticmethod
    def _quantity_in_budget(unit_amount, budget, extra_cost):
        """Mayor cantidad entera que cabe incluyendo costos redondeados."""
        if unit_amount <= 0 or budget <= 0:
            return ZERO
        low, high = 0, int(budget / unit_amount)
        while low < high:
            middle = (low + high + 1) // 2
            if unit_amount * middle + extra_cost(D(middle)) <= budget:
                low = middle
            else:
                high = middle - 1
        return D(low)

    @staticmethod
    def _position_multiplier(position):
        # Las posiciones antiguas conservan sus unidades originales. Cambiar
        # retrospectivamente su factor alteraría fills y resultados históricos.
        features = json.loads(position.get("features_json") or "{}")
        factor = D(features.get("contract_cash_multiplier", "1"), "-1")
        if factor <= 0:
            raise ValueError("Posición con factor monetario inválido")
        return factor

    def _positions_at(self, at, connection):
        """Estado económico en la fecha consultada, sin paginar el ledger."""
        return spot_ledger.positions_at(connection,at)

    def _cash(self, as_of=None, currency="ARS", *, connection=None, for_execution=False):
        """Caja disponible por moneda; no incluye ventas aún sin liquidar."""
        at = aware_datetime(as_of or (self.clock_fn() if self.clock_fn else now_iso()))
        currency = cash_currency(currency)
        if connection is None:
            with self.store.connect() as c:
                c.execute('BEGIN')
                return self._cash(at, currency, connection=c, for_execution=for_execution)
        if for_execution:
            # Consultar el pasado es válido; gastar después de retroceder el
            # reloj sobre movimientos ya registrados no lo es.
            for table, fields in (('paper_positions',('opened_at','closed_at')),
                                  ('paper_cauciones',('opened_at','settled_at'))):
                for row in connection.execute('SELECT '+','.join(fields)+' FROM '+table+' WHERE currency=?',(currency,)):
                    if any(value and aware_datetime(value)>at for value in row):
                        raise ValueError('CASH_CLOCK_ROLLBACK')
            if connection.execute("""SELECT 1 FROM paper_spot_sales s JOIN paper_fills f ON f.id=s.fill_id
                JOIN paper_positions p ON p.paper_id=s.paper_id
                WHERE p.currency=? AND julianday(f.filled_at)>julianday(?) LIMIT 1""",
                (currency,at.isoformat())).fetchone():
                raise ValueError('CASH_CLOCK_ROLLBACK')
        opened, closed = self._positions_at(at, connection)
        realized = sum((decimal_value(p['net_pnl'],'PnL cerrado') for p in closed if p['currency']==currency), ZERO)
        committed = sum((decimal_value(p['entry_price'],'precio',positive=True) *
                         decimal_value(p['quantity'],'cantidad',positive=True) * self._position_multiplier(p) +
                         decimal_value(p['entry_cost'],'costo',nonnegative=True)
                         for p in opened if p['currency']==currency), ZERO)
        return (self.initial_balances[currency] + realized - committed + self.cauciones.cash_effect(currency,at,connection=connection)
                - pending_proceeds(self.store, at, currency, connection=connection))

    def place_caucion(self, offer, principal, request_id, as_of=None, *, reserve="0"):
        """Colocación PAPER explícita; no coloca órdenes reales ni elige plazo."""
        return self.cauciones.place(
            offer, principal, request_id, self.clock_fn() if self.clock_fn else as_of or now_iso(),
            lambda currency, at, c: self._cash(as_of=at, currency=currency, connection=c, for_execution=True),
            reserve=D(reserve, "-1"), participation=self.participation,
            admission=(lambda c, currency, at, fees: self.daily_risk.projected_admission_error(currency,at,fees,connection=c))
                      if self.daily_risk else None)

    def settle_cauciones(self, as_of=None):
        return self.cauciones.settle_due(as_of or now_iso())

    def allocate_caucion(self, offers, policy, request_id, as_of=None):
        """Selecciona y coloca sólo en PAPER con presupuesto/política explícitos."""
        from ca_caucion_allocator import allocate
        return allocate(self,offers,policy,request_id,as_of=as_of)

    def allocate_conservative_caucion(self, offers, window, request_id, as_of=None):
        """Perfil ARS delegado; ventana/ofertas reales todavía deben validarse."""
        from ce_caucion_treasury import allocate_conservative
        return allocate_conservative(self,offers,window,request_id,as_of=as_of)

    def threshold(self, as_of=None):
        # Congelado por versión. PAPER observa resultados; no auto-promueve
        # parámetros a partir de una muestra corta y procíclica. La lectura
        # conserva la validación de integridad del ledger antes de exponerlo
        # al aprendizaje, aunque su win rate ya no cambie el umbral.
        self.store.recent_closed(20, strategy_version=STRATEGY_VERSION,
                                 closed_before=as_of)
        return self.score_threshold

    def decide(self, q: Quote):
        try:
            currency, market = q.monetary_identity()
            family = family_name(q.asset_class)
        except ValueError as exc:
            return "HOLD", ZERO, str(exc), {"samples": 0}
        if family not in SPOT_FAMILIES:
            return ("HOLD", ZERO,
                    f"{family}: requiere su ciclo financiero específico; "
                    "el ejecutor de contado no dimensiona prima ni garantía",
                    {"samples": 0, "family": family})
        if not _family_operable(family):
            return ("HOLD", ZERO,
                    f"{family}: fuera del alcance operativo RC6 (sólo acciones y CEDEARs)",
                    {"samples": 0, "family": family, "operational_scope": "DISABLED_BY_SCOPE"})
        if q.opening_block_reason:
            return "HOLD", ZERO, q.opening_block_reason, {"samples": 0}
        at = self.execution_time(q)
        error = self.admission_error(q, at)
        if error:
            return "HOLD", ZERO, error, {"samples": 0}
        if self.initial_balances[currency] <= 0:
            return "HOLD", ZERO, f"Sin capital asignado en {currency}; no se usa otra moneda/plaza", {"samples": 0}
        if D(q.bid) <= 0 or D(q.ask) < D(q.bid) or D(q.ask_size) <= 0:
            return "HOLD", ZERO, "Puntas o profundidad invalidas", {"samples": 0}
        values = self.store.signal_prices(
            q, at, limit=max(20, self.signal_min_samples),
            window_minutes=self.signal_window_minutes)
        if len(values) < self.signal_min_samples:
            return "HOLD", D("0"), (f"Aprendiendo serie: {len(values)}/"
                                      f"{self.signal_min_samples} muestras"), {"samples": len(values)}
        short = sum(values[-3:], ZERO) / D(3)
        long_span = min(8, len(values))
        long = sum(values[-long_span:], ZERO) / D(long_span)
        momentum = (short / long - 1) if long else ZERO
        spread = (q.ask / q.bid - 1) if q.bid else D("99")
        score = max(ZERO, min(D(1), D("0.5") + momentum * D(40) - spread * D(10)))
        features = {"sma3": str(short), f"sma{long_span}": str(long), "momentum": str(momentum),
                    "spread": str(spread), "samples": len(values),
                    "signal_window_minutes": self.signal_window_minutes,
                    "paper_threshold": str(self.threshold(at))}
        # Historical/candle features are intentionally SHADOW-only. They are
        # persisted beside the baseline decision and cannot alter BUY/HOLD.
        if os.getenv("PAPER_HISTORICAL_CANDLE_SHADOW", "ON").upper() in {"ON", "SHADOW", "TRUE", "1"}:
            try:
                import historical_candle_shadow_rc6
                shadow = historical_candle_shadow_rc6.collect(self.store, q, at)
                features["historical_candle_shadow"] = shadow
                features["decision_shadow"] = (
                    "BUY" if D(score) + D(shadow.get("shadow_score_delta", "0")) >= self.threshold(at)
                    else "HOLD"
                )
            except Exception as exc:
                features["historical_candle_shadow"] = {
                    "mode": "SHADOW", "state": "ERROR",
                    "decision_effect": "OBSERVE_ONLY",
                    "error": f"{type(exc).__name__}:{str(exc)[:180]}",
                }
        if D(q.bid) <= 0 or D(q.ask) < D(q.bid) or D(q.ask_size) <= 0:
            return "HOLD", score, "Puntas o profundidad insuficientes", features
        if spread > D("0.02"):
            return "HOLD", score, "Spread superior al 2%", features
        if score < self.threshold(at):
            return "HOLD", score, "Score paper debajo del umbral versionado", features
        # Contexto BCRA/macro: persistido sólo para candidatos BUY y sin
        # autoridad sobre la decisión. La fuente se actualiza fuera de rueda.
        if os.getenv("PAPER_MACRO_RISK_SHADOW", "ON").upper() in {"ON", "SHADOW", "TRUE", "1"}:
            try:
                import rc6_macro_risk_shadow
                features["macro_risk_shadow"] = rc6_macro_risk_shadow.collect()
            except Exception as exc:
                features["macro_risk_shadow"] = {
                    "mode": "SHADOW", "state": "ERROR",
                    "decision_effect": "OBSERVE_ONLY",
                    "reason": f"{type(exc).__name__}:{str(exc)[:180]}",
                }
        # Noticias GDELT: sólo cache local y sólo contexto reproducible.
        # Nunca consulta red en la rueda ni tiene autoridad de veto.
        if os.getenv("PAPER_GDELT_SHADOW", "ON").upper() in {"ON", "SHADOW", "TRUE", "1"}:
            try:
                import rc6_gdelt_shadow
                features["gdelt_risk_shadow"] = rc6_gdelt_shadow.collect()
            except Exception as exc:
                features["gdelt_risk_shadow"] = {
                    "mode": "SHADOW", "state": "ERROR",
                    "decision_effect": "OBSERVE_ONLY",
                    "reason": f"{type(exc).__name__}:{str(exc)[:180]}",
                }
        return "BUY", score, "Momentum positivo y friccion admisible", features

    def _economic_diagnostics(self, q):
        """Economía ex ante coherente con el cierre intradiario obligatorio."""
        entry = q.ask * (1 + self.slippage)
        stop = entry * (1 - self.stop_loss_pct)
        target = entry * (1 + self.target_gain_pct)
        stop_fill = stop * (1 - self.slippage)
        target_fill = target * (1 - self.slippage)
        full = self._leg_rate(q.asset_class, rebated=False)
        low = self._leg_rate(q.asset_class, rebated=True)
        # La bonificacion corresponde a la operacion de menor valor. En una
        # ganancia es la compra; en una perdida, la venta. Si no es elegible,
        # low == full y se conserva el modelo previo.
        net_reward = target_fill - entry - entry * low - target_fill * full
        net_loss = entry - stop_fill + entry * full + stop_fill * low
        ratio = net_reward / net_loss if net_loss > 0 else ZERO
        breakeven = net_loss / (net_loss + max(ZERO, net_reward)) if net_loss > 0 else D(1)
        passed = net_reward > 0 and ratio >= self.min_net_reward_risk
        try:
            import rc6_cost_settlement_takeprofit_shadow as shadow_costs
            tax_shadow = shadow_costs.published_cost_diagnostic(net_reward)
        except Exception as exc:
            tax_shadow = {"state": "ERROR", "rate": None, "amount": None,
                          "effect": type(exc).__name__}
        return {
            "modeled_tariff": ("PPI_INTRADAY_REBATE_ON_SMALLER_LEG"
                               if self.intraday_fee_rebate else
                               "FULL_PER_LEG_CONSERVATIVE_NO_INTRADAY_REBATE"),
            "full_leg_rate": str(full),
            "rebated_leg_rate": str(low),
            "net_reward_per_unit": str(net_reward),
            "net_loss_per_unit": str(net_loss),
            "net_reward_risk": str(ratio),
            "breakeven_win_rate": str(breakeven),
            "minimum_net_reward_risk": str(self.min_net_reward_risk),
            "passed": passed,
            "published_costs_note": tax_shadow,
            "personal_taxes": "OUT_OF_SCOPE",
        }

    def on_quote(self, q: Quote, *, allow_new_openings=True,
                 opening_block_reason=""):
        if self._maybe_close(q):
            return
        if self.store.open_position(q.symbol):
            return
        if not allow_new_openings:
            return
        action, score, reason, features = self.decide(q)
        bucket = q.observed_at[:16]
        key = f"{STRATEGY_VERSION}:{q.symbol}:{q.asset_class}:{q.settlement}:{q.currency}:{q.market}:{bucket}:{action}"
        if not self.store.record_decision(key, q, action, score, reason, features):
            return
        self.store.event("DECISION_PAPER", f"{q.symbol} {action}: {reason}")
        if action == "BUY":
            try:
                economics = self._economic_diagnostics(q)
            except ValueError as exc:
                features["economics"] = {"passed": False, "error": str(exc)}
                self.store.record_gates(
                    q, key, "APPROVE", "NOT_EVALUATED", "NOT_EVALUATED",
                    "BLOCKED", f"Economía no evaluable: {exc}", detail=features)
                self.store.event("ECONOMIC_GATE_UNSUPPORTED", f"{q.symbol}: {exc}")
                return
            features["economics"] = economics
            features["economics_mode"] = self.economics_mode
            if not economics["passed"]:
                self.store.event("ECONOMIC_GATE_" + self.economics_mode,
                                 f"{q.symbol}: reward/risk neto {economics['net_reward_risk']}")
                if self.economics_mode == "BINDING":
                    self.store.record_gates(q, key, "APPROVE", "NOT_EVALUATED",
                                            "NOT_EVALUATED", "BLOCKED",
                                            "Economía ex ante no supera el mínimo versionado",
                                            detail=features)
                    return
            ai_result = "NOT_USED"
            if self.ai_gate is None and self.require_ai and self.ai_mode == "BINDING":
                self.store.event("AI_VETO_PAPER", f"{q.symbol}: Gemini no disponible; porton cerrado")
                self.store.record_gates(q, key, "APPROVE", "VETO", "NOT_EVALUATED",
                                        "BLOCKED", "Gemini no disponible; portón cerrado",
                                        detail=features)
                return
            if self.ai_gate is not None and self.ai_mode != "OFF":
                try:
                    context = self.context_fn(q.symbol) if self.context_fn else {}
                    ai = self.ai_gate.evaluate(q, score, features, context)
                except Exception as exc:
                    ai = {"decision": "VETO", "score": 0, "veto": True,
                          "reason": f"Gemini no disponible: {type(exc).__name__}: {str(exc)[:300]}",
                          "model": "NO_DISPONIBLE", "raw": {}}
                self.store.record_ai(q, ai, features)
                ai_result = str(ai.get("decision", "VETO"))
                features["gemini"] = {key: ai.get(key) for key in
                                       ("decision", "score", "veto", "reason", "model")}
                if ((ai.get("decision") != "APPROVE" or ai.get("veto", True))
                        and self.ai_mode == "BINDING"):
                    self.store.event("AI_VETO_PAPER", f"{q.symbol}: {ai.get('reason')}")
                    self.store.record_gates(q, key, "APPROVE", str(ai.get("decision", "VETO")),
                                            "NOT_EVALUATED", "BLOCKED",
                                            str(ai.get("reason") or "Gemini vetó la apertura"),
                                            detail=features)
                    return
            opened, gate_reason, paper_id = self._open(q, score, features)
            self.store.record_gates(
                q, key, "APPROVE", ai_result,
                "APPROVE" if opened else "BLOCKED",
                "OPENED_SIMULATED" if opened else "BLOCKED", gate_reason,
                paper_id=paper_id, detail=features)

    def _open(self, q: Quote, score: Decimal, features: dict):
        at = self.execution_time(q)
        error = self.admission_error(q, at)
        if error:
            return False, error, None
        if q.opening_block_reason:
            return False, q.opening_block_reason, None
        try:
            family = family_name(q.asset_class)
            currency, market = q.monetary_identity()
        except ValueError as exc:
            return False, str(exc), None
        if family not in SPOT_FAMILIES:
            return False, f"{family} requiere su ciclo financiero específico; no se compra como una acción", None
        if not _family_operable(family):
            return False, f"{family} fuera del alcance operativo RC6 (sólo acciones y CEDEARs)", None
        if market != "BYMA":
            return False, "Ejecutor de contado pendiente para este mercado", None

        # RC6: learning policies are evaluated on every candidate. Observation/SHADOW
        # persists a counterfactual only; explicit BINDING grants veto authority
        # and fails closed if required evidence is unavailable.
        try:
            import ck_policy_gate_hf6 as policy_gate
            import es_policy_context_rc6 as policy_context_provider
            policy_context = policy_context_provider.collect(
                self.store, at=at, candidate={
                    "symbol": q.symbol, "family": family, "market": market,
                    "currency": currency, "settlement": q.settlement,
                })
            policy_evaluation = policy_gate.evaluate(
                expectancy_samples=policy_context.get("expectancy_samples"),
                breadth=policy_context.get("breadth"),
                sectors=policy_context.get("sectors"),
                candidate_sector=policy_context.get("candidate_sector"),
            )
            features["policy_context_source"] = policy_context.get("source")
            features["policy_evaluation"] = policy_evaluation
            if policy_evaluation.get("execute_block"):
                self.store.event("POLICY_GATE_BLOCKED",
                                 f"{q.symbol}: {policy_evaluation.get('verdict')}")
                return False, str(policy_evaluation.get("verdict")), None
        except Exception as exc:
            # A provider/programming error must only stop entries when one of the
            # a learning policy was explicitly promoted to BINDING_PAPER authority.
            try:
                import ck_policy_gate_hf6 as policy_gate
                modes = policy_gate.active_policies()
            except Exception:
                modes = {}
            features["policy_error"] = f"{type(exc).__name__}:{str(exc)[:240]}"
            if any(str(modes.get(key) or "").upper() == "BINDING"
                   for key in ("expectancy", "regime", "sector_concentration")):
                return False, "POLICY_CONTEXT_UNAVAILABLE_BINDING", None

        capital, capital_source = self._risk_capital(currency)
        factor, step = D(1), D(1)
        if q.contract is not None:
            spec = q.contract
            if ((spec.symbol, spec.family, spec.settlement) != (q.symbol, family, q.settlement)
                    or spec.currency != currency or spec.market != market):
                return False, "Contrato incompatible con esta cotización y libro de caja", None
            factor, step = spec.cash_multiplier, spec.quantity_step
            if step < 1 or step != step.to_integral_value():
                return False, "Este ejecutor de contado requiere cantidades enteras", None
        elif family in {"BONOS", "LETRAS", "OBLIGACIONES"}:
            return False, "Falta confirmar factor de precio/VN y lote del instrumento de renta fija", None
        if D(q.bid) <= 0 or D(q.ask) < D(q.bid) or D(q.ask_size) <= 0:
            return False, "Puntas o profundidad invalidas para una compra simulada", None
        if self.store.open_position(q.symbol):
            return False, "Ya existe una posicion abierta del simbolo", None
        from de_concurrent_risk_capacity_hf6 import derive_emergency_position_cap, emergency_position_guard
        from dh_paper_dynamic_risk_gate_hf6 import (
            ConcurrentRiskGateError, candidate_stop_risk, portfolio_capacity, snapshot_dict)
        if self.daily_risk is None:
            return False, "CONCURRENT_RISK_NOT_CONFIGURED", None
        try:
            emergency_cap, emergency_cap_source = derive_emergency_position_cap(
                soft_stop_pct=self.daily_risk.soft_limit_pct,
                risk_per_trade_fraction=self.risk_pct,
                configured=os.getenv("PAPER_EMERGENCY_MAX_OPEN_POSITIONS", "AUTO"),
            )
        except ValueError:
            return False, "EMERGENCY_POSITION_CAP_CONFIG_INVALID", None
        opened_now = self.store.open_positions()
        if emergency_position_guard(len(opened_now), emergency_cap=emergency_cap):
            self.store.event("REJECTED_PAPER", f"{q.symbol}: cap técnico anti-runaway")
            return False, "EMERGENCY_POSITION_CAP", None
        entry = (q.ask * (1 + self.slippage)).quantize(Decimal("0.0001"))
        if entry <= 0:
            return False, "Precio de entrada no representable", None
        stop = entry * (1 - self.stop_loss_pct)
        target = entry * (1 + self.target_gain_pct)
        modeled_stop_fill = (stop * (1 - self.slippage)).quantize(Decimal("0.0001"))
        # Incluye ambos tramos y deslizamiento de salida, sin afirmar que un
        # stop garantice este precio ante gaps o falta de liquidez. La cantidad
        # se acota por el menor entre el riesgo por trade y la capacidad
        # concurrente que queda hasta el soft stop diario.
        try:
            concurrent_before = portfolio_capacity(
                self, currency, at, quotes={q.symbol:q})
        except ConcurrentRiskGateError as exc:
            return False, str(exc), None
        per_trade_risk_budget = capital * self.risk_pct
        concurrent_risk_budget = min(
            per_trade_risk_budget, concurrent_before.remaining_before_candidate)
        if concurrent_risk_budget <= 0:
            self.store.event("REJECTED_PAPER", f"{q.symbol}: riesgo concurrente agotado")
            return False, "CONCURRENT_RISK_BUDGET_EXHAUSTED", None
        by_risk = self._quantity_in_budget(
            (entry - modeled_stop_fill) * factor, concurrent_risk_budget,
            lambda qty: self._cost(entry * factor, qty, q.asset_class) +
                        self._cost(modeled_stop_fill * factor, qty, q.asset_class))
        by_cash = self._quantity_in_budget(
            entry * factor, max(ZERO, self._cash(as_of=at, currency=currency)),
            lambda qty: self._cost(entry * factor, qty, q.asset_class))
        try:
            with self.store.connect() as c:
                by_book = spot_liquidity.available(c,q,'BUY_SIMULATED',self.participation).to_integral_value(ROUND_DOWN)
        except ValueError as exc:
            return False, str(exc), None
        by_position_cap = (capital * self.max_position_pct / (entry * factor)).to_integral_value(ROUND_DOWN)
        current_exposure = sum((D(p["entry_price"]) * D(p["quantity"]) * self._position_multiplier(p)
                                for p in self.store.open_positions() if p["currency"] == currency), ZERO)
        exposure_remaining = max(
            ZERO, capital * self.max_total_exposure_pct - current_exposure
        )
        by_total_cap = (exposure_remaining / (entry * factor)).to_integral_value(ROUND_DOWN)
        qty = (min(by_risk, by_cash, by_book, by_position_cap, by_total_cap) / step).to_integral_value(ROUND_DOWN) * step
        max_hold_minutes = int(os.getenv("PAPER_MAX_HOLD_MINUTES", "360"))
        features["exit_policy"] = {
            "mode": "SIMULATED",
            "stop_loss_price": str(stop),
            "take_profit_price": str(target),
            "max_hold_minutes": max_hold_minutes,
            "end_of_day": bool(getattr(self.session_policy, "close_at_eod", False)),
            "execution": "SUPERVISED_EXIT_READER",
            "meaning": "Una salida requiere libro fresco y se registra; nunca envía una orden real.",
        }
        features.update({
            "book_source_at": q.book_at, "trade_source_at": q.trade_at,
            "received_at": q.observed_at, "last_kind": q.last_kind,
            "contract_cash_multiplier": str(factor), "contract_quantity_step": str(step),
            "financial_contract": asdict(q.contract) if q.contract else None,
            "capital_currency": currency, "market": market,
            "risk_capital": str(capital), "risk_capital_source": capital_source,
            "initial_capital": str(self.initial_balances[currency]),
            "risk_budget": str(concurrent_risk_budget),
            "per_trade_risk_budget": str(per_trade_risk_budget),
            "concurrent_risk_before": snapshot_dict(concurrent_before),
            "emergency_position_cap": emergency_cap,
            "emergency_position_cap_source": emergency_cap_source,
            "max_position_pct": str(self.max_position_pct),
            "max_total_exposure_pct": str(self.max_total_exposure_pct),
            "qty_by_risk": str(by_risk),
            "qty_by_cash": str(by_cash),
            "qty_by_liquidity": str(by_book),
            "qty_by_position_cap": str(by_position_cap),
            "qty_by_total_cap": str(by_total_cap),
        })
        if currency == "ARS":
            features.update(initial_capital_ars=str(capital), risk_budget_ars=str(concurrent_risk_budget))
        if qty < 1:
            self.store.event("REJECTED_PAPER", f"{q.symbol}: capital/liquidez insuficiente")
            limits = {
                "riesgo": by_risk, "caja": by_cash, "profundidad": by_book,
                "tope_posicion": by_position_cap, "exposicion_total": by_total_cap,
            }
            binding = min(limits, key=lambda name: limits[name])
            return False, (f"Portón patrimonial/liquidez: {binding} dejó cantidad "
                           f"ejecutable en {limits[binding]}"), None
        candidate_risk_value = candidate_stop_risk(
            self, entry_price=entry, stop_price=stop, quantity=qty,
            cash_multiplier=factor, asset_class=q.asset_class)
        try:
            concurrent_preview = portfolio_capacity(
                self, currency, at, candidate_risk=candidate_risk_value,
                quotes={q.symbol:q})
        except ConcurrentRiskGateError as exc:
            return False, str(exc), None
        if not concurrent_preview.admitted:
            self.store.event("REJECTED_PAPER", f"{q.symbol}: {concurrent_preview.reason}")
            return False, concurrent_preview.reason, None
        features.update(
            candidate_stop_risk=str(candidate_risk_value),
            concurrent_risk_preview=snapshot_dict(concurrent_preview))
        paper_id = "PAPER-" + uuid.uuid4().hex
        cost = self._cost(entry * factor, qty, q.asset_class)
        with self.store.connect() as c:
            # Serializa contra otras compras y colocaciones de caución.
            # No se hace red ni IA dentro de esta transacción.
            c.execute("BEGIN IMMEDIATE")
            at = self.execution_time(q)
            error = self.admission_error(q, at, connection=c)
            if error:
                return False, error, None
            try:
                if qty > spot_liquidity.available(c,q,'BUY_SIMULATED',self.participation):
                    return False, 'SPOT_BOOK_DEPTH_CHANGED', None
            except ValueError as exc:
                return False, str(exc), None
            opened = spot_ledger.positions_at(c, at)[0]
            if emergency_position_guard(len(opened), emergency_cap=emergency_cap):
                return False, "EMERGENCY_POSITION_CAP", None
            try:
                concurrent_locked = portfolio_capacity(
                    self, currency, at, candidate_risk=candidate_risk_value,
                    connection=c, quotes={q.symbol:q})
            except ConcurrentRiskGateError as exc:
                return False, str(exc), None
            if not concurrent_locked.admitted:
                return False, concurrent_locked.reason, None
            features["concurrent_risk_locked"] = snapshot_dict(concurrent_locked)
            exposure_now = sum((D(p["entry_price"]) * D(p["quantity"]) * self._position_multiplier(p)
                                for p in opened if p["currency"] == currency), ZERO)
            if (any(p["symbol"] == q.symbol for p in opened)
                    or entry * qty * factor + cost > self._cash(as_of=at, currency=currency, connection=c, for_execution=True)
                    or exposure_now + entry * qty * factor > capital * self.max_total_exposure_pct):
                return False, "Caja, identidad o exposición cambiaron antes de registrar la compra", None
            c.execute("""INSERT INTO paper_positions
              (paper_id,source,strategy_version,symbol,asset_class,settlement,status,
               quantity,entry_price,entry_cost,stop_price,target_price,opened_at,features_json,currency,market,currency_source)
              VALUES(?,?,?,?,?,?,'OPEN',?,?,?,?,?,?,?,?,?,?)""",
              (paper_id, SOURCE, STRATEGY_VERSION, q.symbol, q.asset_class, q.settlement,
               str(qty), str(entry), str(cost), str(stop), str(target), at,
               json.dumps(features, ensure_ascii=False, default=str), currency, market,
               q.metadata_source or "EXPLICIT_QUOTE"))
            fill = c.execute("INSERT INTO paper_fills VALUES(NULL,?,?,?,?,?,?,?,?)",
                      (paper_id, SOURCE, "BUY_SIMULATED", at, str(qty),
                       str(entry), str(cost), str(entry-q.ask)))
            spot_liquidity.record(c,fill.lastrowid,q)
            c.execute("INSERT INTO paper_learning_samples VALUES(?,?,?,?,?,?,?,?,?)",
                      (paper_id, SOURCE, STRATEGY_VERSION, q.observed_at,
                       json.dumps(features, ensure_ascii=False, default=str), None, None, None, None))
            c.execute("INSERT INTO paper_events VALUES(NULL,?,?,?,?,?)",
                      (at, SOURCE, "PAPER_FILLED_BUY", paper_id,
                       f"Compra simulada {qty} {q.symbol} @ {entry}"))
            if self.daily_risk:
                self.daily_risk.evaluate(at,connection=c,quotes={q.symbol:q})
        return True, "Todos los portones aprobaron; compra simulada registrada", paper_id

    def _risk_capital(self, currency):
        """No aumenta riesgo por ganancias; sí reduce tamaño tras drawdown."""
        initial = self.initial_balances[currency]
        with self.store.connect() as c:
            row = c.execute("""SELECT equity FROM paper_equity_by_currency
              WHERE currency=? ORDER BY id DESC LIMIT 1""", (currency,)).fetchone()
        if not row:
            return initial, "INITIAL_NO_EQUITY_SNAPSHOT"
        equity = max(ZERO, decimal_value(row[0], "equity", nonnegative=True))
        return min(initial, equity), "LATEST_EQUITY_CAPPED_AT_INITIAL"

    def _maybe_close(self, q: Quote):
        p = self.store.open_position(q.symbol)
        if not p:
            return False
        from bm_exit_supervisor import PositionExitSupervisor
        at = self.execution_time(q)
        supervisor = PositionExitSupervisor(self, clock_fn=lambda: at,
            session_policy=self.session_policy,
            max_hold_minutes=int(os.getenv("PAPER_MAX_HOLD_MINUTES", "360")))
        return supervisor.supervise(p, q, at).state == "CLOSED"

    def _close(self, p: dict, q: Quote, reason: str, *, as_of=None):
        """Fill total o parcial, limitado por lotes y profundidad compartida.

        True confirma un fill, no necesariamente el cierre de la posición.
        El supervisor comprueba remanente y estado en el ledger.
        """
        at = as_of or self.execution_time(q)
        if q.time_error(at, max_age_seconds=self.quote_max_age_seconds):
            return False
        if self.session_policy and self.session_policy.execution_error(p, at):
            return False
        if (p['symbol'],p['asset_class'],p['settlement']) != (q.symbol,q.asset_class,q.settlement):
            return False
        try:
            if q.monetary_identity() != (p['currency'],p['market']):
                self.store.event('EXIT_PENDING_IDENTITY_MISMATCH','Moneda/mercado distintos del fill original',p['paper_id'])
                return False
            factor = self._position_multiplier(p)
            if q.contract and (q.contract.cash_multiplier != factor or
                    q.contract.symbol != q.symbol or q.contract.settlement != q.settlement or
                    q.contract.family != family_name(q.asset_class) or q.contract.currency != p['currency']):
                return False
            step = decimal_value(json.loads(p['features_json'] or '{}').get('contract_quantity_step','1'),
                                 'lote de salida',positive=True)
            if q.contract and q.contract.quantity_step != step:
                return False
        except (ValueError,TypeError):
            return False
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            at = self.execution_time(q) if self.clock_fn else at
            if q.time_error(at,max_age_seconds=self.quote_max_age_seconds):
                return False
            if self.session_policy and self.session_policy.execution_error(p,at):
                return False
            root = c.execute('SELECT * FROM paper_positions WHERE paper_id=?',(p['paper_id'],)).fetchone()
            if not root or root['status']!='OPEN':
                return False
            root = dict(root)
            if any(root[k]!=p[k] for k in ('symbol','asset_class','settlement','currency','market','features_json')):
                return False
            current, prior = spot_ledger.partition(c,root)
            # Snapshot viejo: no consumir otra porción con un reintento ciego.
            if not current or any(current[k]!=p[k] for k in ('quantity','entry_cost','entry_price')):
                return False
            try:
                observed = aware_datetime(at)
                opened = aware_datetime(root['opened_at'])
                if observed < opened or aware_datetime(q.book_at) < opened:
                    return False
                if any(aware_datetime(r['closed_at'])>observed for r in prior):
                    return False
                depth = spot_liquidity.available(c,q,'SELL_SIMULATED',self.participation)
            except (ValueError,TypeError):
                return False
            remaining = decimal_value(current['quantity'],'remanente',positive=True)
            if remaining % step:
                return False
            qty = (min(remaining,depth)/step).to_integral_value(rounding=ROUND_DOWN)*step
            if qty <= 0:
                c.execute('INSERT INTO paper_events VALUES(NULL,?,?,?,?,?)',
                    (at,SOURCE,'EXIT_PENDING_NO_LIQUIDITY',p['paper_id'],'Sin profundidad para un lote de salida'))
                return False
            final = qty==remaining
            # Reparto monetario en centavos, con residuo exacto en el último fill.
            from bt_caucion_paper import modeled_sale_settlement
            entry_cost = spot_ledger.allocated_entry_cost(
                D(root['quantity']), D(root['entry_cost']),
                D(root['quantity']) - remaining,
                D(root['entry_cost']) - D(current['entry_cost']), qty)
            exit_price = (q.bid*(1-self.slippage)).quantize(Decimal('0.0001'))
            if exit_price <= 0:
                return False
            exit_cost = self._cost(exit_price*factor,qty,p['asset_class'])
            rebate = ZERO
            family = family_name(p['asset_class'])
            same_day = opened.astimezone(TZ).date() == observed.astimezone(TZ).date()
            if (self.intraday_fee_rebate and same_day and p['market'] == 'BYMA'):
                import au_fee_schedule
                if family in au_fee_schedule.FAMILIAS_CON_BONIFICACION_INTRADIARIA:
                    full = self._leg_rate(family, rebated=False)
                    low = self._leg_rate(family, rebated=True)
                    matched_buy = D(root['entry_price']) * qty * factor
                    matched_sell = exit_price * qty * factor
                    # Por fills parciales se acredita por la porcion
                    # emparejada. Es conservador frente a netear fills mixtos
                    # y nunca concede mas que la bonificacion oficial.
                    rebate = (min(matched_buy, matched_sell) * (full-low)).quantize(
                        Decimal('0.01'))
                    exit_cost = max(ZERO, exit_cost-rebate)
            gross = (exit_price-D(root['entry_price']))*qty*factor
            net = gross-entry_cost-exit_cost
            proceeds = exit_price*qty*factor-exit_cost
            intent = c.execute('SELECT cause FROM paper_exit_intents WHERE paper_id=?',(p['paper_id'],)).fetchone()
            if intent and intent[0]:
                reason = intent[0]
            fill = c.execute('INSERT INTO paper_fills VALUES(NULL,?,?,?,?,?,?,?,?)',
                (p['paper_id'],SOURCE,'SELL_SIMULATED',at,str(qty),str(exit_price),str(exit_cost),str(q.bid-exit_price)))
            if not final or prior:
                available = modeled_sale_settlement(p['settlement'],at)
                c.execute('INSERT INTO paper_spot_sales VALUES(?,?,?,?,?,?,?,?,?)',
                    (fill.lastrowid,p['paper_id'],str(entry_cost),str(gross),str(net),str(proceeds),available,
                     'PAPER_CONSERVATIVE_CALENDAR' if available else 'PENDING_CONFIRMATION',reason))
            else:
                # Mantiene el formato histórico del cierre de un solo fill.
                record_sale(c,p['paper_id'],p['settlement'],at,proceeds,currency=p['currency'])
            spot_liquidity.record(c,fill.lastrowid,q)
            total_net = net + sum((D(r['net_pnl']) for r in prior),ZERO)
            if final:
                total_gross = gross + sum((D(r['gross_pnl']) for r in prior),ZERO)
                total_cost = exit_cost + sum((D(r['exit_cost']) for r in prior),ZERO)
                avg_exit = ((exit_price*qty + sum((D(r['exit_price'])*D(r['quantity']) for r in prior),ZERO)) /
                            D(root['quantity']))
                c.execute('''UPDATE paper_positions SET status='CLOSED',closed_at=?,exit_price=?,
                    exit_cost=?,gross_pnl=?,net_pnl=?,close_reason=? WHERE paper_id=?''',
                    (at,str(avg_exit),str(total_cost),str(total_gross),str(total_net),reason,p['paper_id']))
                invested = D(root['entry_price'])*D(root['quantity'])*factor
                ret = total_net/invested*100
                outcome = 'WIN' if total_net>0 else 'LOSS' if total_net<0 else 'FLAT'
                duration = int((observed-opened).total_seconds()/60)
                c.execute('''UPDATE paper_learning_samples SET label_timestamp=?,net_return_pct=?,
                    outcome=?,duration_minutes=? WHERE paper_id=?''',
                    (at,str(ret),outcome,duration,p['paper_id']))
            state = 'CLOSED' if final else 'EXIT_PARTIAL'
            detail = '' if final else f'Remanente {remaining-qty}; espera otra profundidad ejecutable'
            c.execute('''INSERT INTO paper_exit_intents VALUES(?,?,?,?,?,?,1)
                ON CONFLICT(paper_id) DO UPDATE SET state=excluded.state,
                blocked_reason=excluded.blocked_reason,cause=COALESCE(paper_exit_intents.cause,excluded.cause),
                due_at=COALESCE(paper_exit_intents.due_at,excluded.due_at),
                supervised_at=excluded.supervised_at,attempts=paper_exit_intents.attempts+1''',
                (p['paper_id'],state,reason,at,detail,at))
            c.execute('INSERT INTO paper_events VALUES(NULL,?,?,?,?,?)',
                (at,SOURCE,'PAPER_FILLED_SELL',p['paper_id'],
                 f'Venta simulada {qty} {q.symbol} @ {exit_price}; PnL de este fill {net}; '
                 f'remanente {remaining-qty}; posición {state}'))
            if rebate > 0:
                c.execute('INSERT INTO paper_events VALUES(NULL,?,?,?,?,?)',
                    (at,SOURCE,'PPI_INTRADAY_FEE_REBATE',p['paper_id'],
                     f'Bonificación PAPER sobre punta menor emparejada: {rebate}; '
                     'derechos de mercado conservados'))
            if self.daily_risk:
                self.daily_risk.evaluate(at,connection=c,quotes={q.symbol:q})
        return True

    def mark_equity(self, quotes: dict, as_of=None):
        # Un snapshot financiero consistente mientras lector/escáner/reloj
        # escriben concurrentemente. No hay red ni IA dentro de este bloqueo.
        with self.store.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            return self._mark_equity_locked(quotes, as_of, c)

    def _mark_equity_locked(self, quotes, as_of, c):
        measured_at = as_of or now_iso()
        opened, closed = self._positions_at(aware_datetime(measured_at), c)
        results, quality, new_marks = {}, {}, []
        last_marks = {r["paper_id"]: r for r in c.execute("SELECT * FROM paper_position_marks")}
        for currency in sorted(CASH_CURRENCIES):
            exposure = unrealized = ZERO
            stale = 0
            for p in (p for p in opened if p["currency"] == currency):
                q = (quotes.get((p["symbol"], p["asset_class"], p["settlement"], currency, p["market"]))
                     or quotes.get((p["symbol"], p["asset_class"], p["settlement"])) or quotes.get(p["symbol"]))
                try:
                    stored = self.store.latest_quote(p)
                    if (stored is not None and aware_datetime(stored.observed_at) <= aware_datetime(measured_at)
                            and (q is None or aware_datetime(stored.observed_at) >= aware_datetime(q.observed_at))):
                        q = stored
                    usable = (q is not None and q.monetary_identity() == (currency, p["market"]) and
                              (q.symbol, q.asset_class, q.settlement) == (p["symbol"], p["asset_class"], p["settlement"]) and D(q.bid) > 0 and not q.time_error(measured_at, max_age_seconds=self.quote_max_age_seconds))
                except (ValueError, TypeError):
                    usable = False
                if usable:
                    mark = q.bid
                    new_marks.append((p["paper_id"],str(mark),q.book_at,measured_at))
                else:
                    previous = last_marks.get(p["paper_id"])
                    if previous and max(aware_datetime(previous['book_at']),aware_datetime(previous['marked_at'])) > aware_datetime(measured_at):
                        previous = None
                    mark = D(previous["mark_price"]) if previous else D(p["entry_price"])
                    stale += 1
                qty = D(p["quantity"]) * self._position_multiplier(p)
                exposure += mark * qty
                unrealized += (mark - D(p["entry_price"])) * qty - D(p["entry_cost"])
            realized = sum((D(p.get("net_pnl")) for p in closed if p["currency"] == currency), ZERO)
            caucion = self.cauciones.valuation(measured_at, currency, connection=c)
            unrealized += caucion["unrealized"]
            realized += caucion["realized"]
            cash = self._cash(as_of=measured_at, currency=currency, connection=c)
            receivable = pending_proceeds(self.store, measured_at, currency, connection=c)
            equity = cash + exposure + receivable + caucion["principal"] + caucion["accrued"]
            results[currency] = {"cash": cash, "exposure": exposure, "pending_proceeds": receivable,
                                 "caucion_principal": caucion["principal"], "caucion_accrued": caucion["accrued"],
                                 "unrealized_pnl": unrealized, "realized_pnl": realized, "equity": equity}
            quality[currency] = ("STALE_MARKS" if stale else "CURRENT", stale)
        c.executemany("INSERT OR REPLACE INTO paper_position_marks VALUES(?,?,?,?)",new_marks)
        for currency, values in results.items():
            c.execute("INSERT OR REPLACE INTO paper_valuation_quality VALUES(?,?,?,?)",
                      (currency,measured_at,*quality[currency]))
            c.execute("INSERT INTO paper_equity_by_currency VALUES(NULL,?,?,?,?,?,?,?,?,?,?)",
                      (measured_at, currency, *(str(value) for value in values.values())))
        ars = results["ARS"]
        # Compatibilidad: la serie histórica principal conserva sólo ARS.
        # No sumar dólares, MEP y CCL sin una conversión valuada explícita.
        c.execute("INSERT INTO paper_equity VALUES(NULL,?,?,?,?,?,?,?)",
                  (measured_at, SOURCE, str(ars["cash"]), str(ars["exposure"]), str(ars["unrealized_pnl"]),
                   str(ars["realized_pnl"]), str(ars["equity"])))
        return results
