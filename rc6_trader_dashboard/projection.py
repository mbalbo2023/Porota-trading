"""Request-scoped, bounded SQLite projection of canonical facts.

One read transaction, named columns, parameterized filters, ten rows and no
provider calls. Missing data never becomes a measured zero or new authority.
"""
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from datetime import datetime, timedelta, timezone
from functools import cached_property
import json
import os
from pathlib import Path
import sqlite3
import stat
from zoneinfo import ZoneInfo

from .generation import read_shadow

TZ = ZoneInfo("America/Argentina/Buenos_Aires")
UNKNOWN = "NO_VERIFICADO"
JSON_LIMIT = 32 * 1024
FILE_LIMIT = 1024 * 1024
ROW_LIMIT = 10


def contract_labels(metadata):
    """Display aliases only; never infer a missing economic or order term."""
    result = dict(metadata)
    aliases = {"multiplier": ("cash_multiplier",), "step": ("quantity_step", "principal_step"),
               "minimum": ("minimum_quantity", "minimum_principal", "subscription_min"),
               "maturity": ("maturity_at", "maturity_date"), "expiry": ("expires_at", "expiry_at"),
               "right": ("option_right", "put_call"), "rate": ("annual_rate_fraction",), "tenor": ("term_days",)}
    for display, candidates in aliases.items():
        if display not in result:
            result[display] = next((result[key] for key in candidates if key in result), None)
    return result


def decimal_amount(value):
    try:
        amount = Decimal(str(value))
        return amount if amount.is_finite() and not isinstance(value, bool) else None
    except (ValueError, InvalidOperation):
        return None


class DecimalSum:
    def __init__(self):
        self.total = Decimal(0)
        self.valid = True

    def step(self, value):
        amount = decimal_amount(value)
        if amount is None:
            self.valid = False
        else:
            self.total += amount

    def finalize(self):
        return str(self.total) if self.valid else None


def decimal_add(a, b):
    left, right = decimal_amount(a), decimal_amount(b)
    return str(left + right) if left is not None and right is not None else None


def decimal_sign(value):
    amount = decimal_amount(value)
    return (1 if amount > 0 else -1 if amount < 0 else 0) if amount is not None else None


def json_object(value):
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or len(value.encode("utf-8")) > JSON_LIMIT:
        return {}
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except (ValueError, TypeError):
        return {}


def age(value, now):
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            return None
        seconds = (now - stamp).total_seconds()
        return round(seconds, 1) if seconds >= 0 else None
    except (ValueError, TypeError):
        return None


def freshness(value, now, budget=180):
    seconds = age(value, now)
    return UNKNOWN if seconds is None else "FRESH" if seconds <= budget else "STALE"


def validated_metrics(payload, now):
    """Keep derived metrics only when their own authority and clock validate."""
    result = dict(payload)
    by_metric = result.get("metric_provenance", {})
    for metric in ("yield", "duration", "parity", "accrued", "iv", "greeks", "open_interest"):
        provenance = by_metric.get(metric, {}) if isinstance(by_metric, dict) else {}
        valid = (isinstance(provenance, dict) and provenance.get("validated") is True
                 and provenance.get("source") and freshness(provenance.get("as_of"), now) == "FRESH")
        if not valid:
            result.pop(metric, None)
    return result


@dataclass
class Page:
    source: str
    rows: list[dict] = field(default_factory=list)
    total: int | None = None
    state: str = UNKNOWN
    offset: int = 0
    as_of: str | None = None
    reason: str = ""


# Column sets are explicit. Arbitrary table/column names never come from HTTP.
TABLE_FIELDS = {
    "observer_state": "id mode process_state session_state ppi_auth heartbeat_at last_market_data_at real_orders_sent detail",
    "paper_positions": "paper_id symbol asset_class market currency settlement source strategy_version status side quantity entry_price entry_cost stop_price target_price opened_at closed_at exit_price exit_cost gross_pnl net_pnl close_reason features_json max_favorable max_adverse",
    "paper_decisions": "id source strategy_version decision_key decided_at symbol action score reason features_json",
    "trade_gate_evaluations": "id evaluated_at decision_key symbol technical_gate ai_gate patrimonial_gate final_result reason paper_id detail_json",
    "decision_evidence_snapshots": "decision_key captured_at schema_version payload_sha256 payload_json",
    "paper_position_marks": "paper_id mark_price book_at marked_at",
    "paper_exit_intents": "paper_id state cause due_at blocked_reason supervised_at attempts",
    "paper_spot_sales": "fill_id paper_id entry_cost gross_pnl net_pnl net_proceeds available_at basis reason",
    "paper_fills": "id paper_id source side filled_at quantity price costs slippage",
    "paper_equity_by_currency": "id measured_at currency cash exposure pending_proceeds caucion_principal caucion_accrued unrealized_pnl realized_pnl equity",
    "paper_valuation_quality": "currency measured_at state stale_positions",
    "paper_daily_risk": "day currency baseline_equity limit_pct loss_budget last_equity daily_pnl state latched_at evaluated_at detail",
    "paper_supervisor_state": "id heartbeat_at state detail",
    "paper_exit_reader_state": "id heartbeat_at state detail",
    "paper_runtime_state": "id heartbeat_at state detail",
    "intraday_scalping_worker_state": "id heartbeat_at state selected successful failed candidates real_orders_sent detail",
    "api_health": "component state detail checked_at last_success_at source",
    "source_sync": "source status last_attempt_at last_success_at items detail",
    "financial_instrument_catalog": "ticker instrument_type market currency settlement description last_seen_at status capability metadata_json",
    "candidate_identity_v2": "ticker instrument_type market currency settlement can_simulate status detail checked_at",
    "contract_evidence_v2_current": "family ticker market currency settlement source_class snapshot_id evidence_hash observed_at",
    "contract_evidence_v2_snapshots": "snapshot_id family ticker market currency settlement source_class observed_at evidence_hash evidence_json",
    "market_snapshots": "id source observed_at symbol asset_class market currency settlement last bid ask bid_size ask_size book_at trade_at received_at",
    "ppi_intraday_contract_state": "symbol asset_class market currency settlement state observations stable_overlap changed_closed_points last_source_at checked_at detail",
    "scalping_candidates": "id evaluated_at symbol asset_class market currency settlement action score price volume points reason economics_json",
    "paper_family_lifecycle": "lifecycle_id family instrument currency state updated_at ledger_total metadata_json",
    "paper_family_lifecycle_events": "event_id lifecycle_id family from_state to_state amount occurred_at detail_json",
    "paper_cauciones": "paper_id currency principal annual_rate_fraction interest_days day_count_basis opened_at maturity_at settled_at status total_fees gross_interest net_interest features_json",
    "paper_caucion_allocations": "id evaluated_at decision_json paper_id",
    "production_history": "symbol instrument_type settlement date_from date_to downloaded_at row_count",
    "history_canonical_v2": "ticker symbol family instrument_type market currency settlement trading_date quality_state source_class source_ref source status row_count rows first_at last_at downloaded_at observed_at known_at",
    "paper_learning_samples": "paper_id source strategy_version feature_timestamp label_timestamp net_return_pct outcome duration_minutes features_json",
    "report_registry": "id created_at generated_at report_type period_type period_key period_start period_end state pdf_path ai_path",
    "operational_jobs": "job_key job_name state started_at finished_at last_run_at next_run_at last_success_at detail",
    "paper_notification_worker": "id heartbeat_at state detail last_success_at last_error",
}


class Store(AbstractContextManager):
    def __init__(self, path, *, now=None, trace=None):
        self.path = Path(path)
        self.now = now or datetime.now(timezone.utc)
        self.connection = None
        self.schema = {}
        self.errors = []
        self.query_count = 0
        self.trace = trace

    def __enter__(self):
        try:
            self.connection = sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.15)
            self.connection.row_factory = sqlite3.Row
            self.connection.create_aggregate("decimal_sum", 1, DecimalSum)
            self.connection.create_function("decimal_add", 2, decimal_add, deterministic=True)
            self.connection.create_function("decimal_sign", 1, decimal_sign, deterministic=True)
            self.connection.execute("PRAGMA query_only=ON")
            self.connection.execute("BEGIN")
            if self.trace:
                self.connection.set_trace_callback(self.trace)
        except sqlite3.Error:
            self.errors.append("DATABASE_READ_UNAVAILABLE")
        return self

    def __exit__(self, *args):
        if self.connection:
            self.connection.close()

    def query(self, sql, params=()):
        self.query_count += 1
        if self.connection is None:
            return []
        try:
            return [dict(row) for row in self.connection.execute(sql, params)]
        except sqlite3.Error:
            self.errors.append("QUERY_UNAVAILABLE")
            return []

    def columns(self, table):
        if table not in TABLE_FIELDS:
            raise ValueError("UNCLASSIFIED_DATASET")
        if table not in self.schema:
            self.schema[table] = {row["name"] for row in self.query(f'PRAGMA table_info("{table}")')}
        return self.schema[table]

    def select(self, table, alias=""):
        cols = self.columns(table)
        prefix = alias + "." if alias else ""
        values = []
        for name in TABLE_FIELDS[table].split():
            if name not in cols:
                continue
            column = f'{prefix}"{name}"'
            if name.endswith("_json") or name == "detail":
                values.extend((f"CASE WHEN length(CAST({column} AS BLOB)) <= {JSON_LIMIT} THEN {column} END AS {name}",
                               f"length(CAST({column} AS BLOB)) > {JSON_LIMIT} AS {name}_oversize"))
            else:
                values.append(column)
        return ",".join(values)

    def page(self, table, *, where="", params=(), order="", offset=0, limit=10):
        columns = self.select(table)
        if not columns:
            return Page(table, offset=offset, reason="DATASET_NOT_PUBLISHED")
        condition = " WHERE " + where if where else ""
        errors = len(self.errors)
        count = self.query(f'SELECT COUNT(*) n FROM "{table}"{condition}', params)
        rows = self.query(f'SELECT {columns} FROM "{table}"{condition}' +
                          (" ORDER BY " + order if order else "") + " LIMIT ? OFFSET ?",
                          (*params, min(ROW_LIMIT, max(1, limit)), max(0, offset)))
        if len(self.errors) != errors:
            return Page(table, offset=offset, reason="DATASET_SCHEMA_OR_READ_ERROR")
        return Page(table, rows, count[0]["n"] if count else None, "AVAILABLE", offset)

    def one(self, table, where="id=1", params=()):
        columns = self.select(table)
        if not columns:
            return {}
        return next(iter(self.query(f'SELECT {columns} FROM "{table}" WHERE {where} LIMIT 1', params)), {})

    def by_keys(self, table, column, keys):
        if not keys or column not in self.columns(table):
            return []
        return self.query(f'SELECT {self.select(table)} FROM "{table}" WHERE "{column}" IN (' +
                          ",".join("?" for _ in keys) + ") LIMIT ?", (*keys, 10 * 20))


class Projection:
    def __init__(self, store, filters=None, *, generation_reader=None):
        self.store = store
        self.filters = dict(filters or {})
        self.now = store.now
        self.offset = max(0, min(100000, self.integer(self.filters.get("offset"), 0)))
        self.generation_reader = generation_reader

    @staticmethod
    def integer(value, default):
        try:
            return int(value)
        except (ValueError, TypeError):
            return default

    @cached_property
    def runtime(self):
        state = self.store.one("observer_state")
        return {**state, "heartbeat_age": age(state.get("heartbeat_at"), self.now),
                "heartbeat_freshness": freshness(state.get("heartbeat_at"), self.now, 30),
                "market_age": age(state.get("last_market_data_at"), self.now),
                "market_freshness": freshness(state.get("last_market_data_at"), self.now)}

    @cached_property
    def shadow(self):
        root = Path(os.getenv("POROTA_SHADOW_RUNTIME_ROOT", str(self.store.path) + ".shadow"))
        return read_shadow(root, self.generation_reader)

    def daily_where(self, column):
        start = datetime.combine(self.now.astimezone(TZ).date(), datetime.min.time(), TZ)
        return f"julianday({column})>=julianday(?) AND julianday({column})<julianday(?)", (
            start.isoformat(), (start + timedelta(days=1)).isoformat())

    def instrument_filter(self, alias="c"):
        prefix = alias + "." if alias else ""
        where, params = [], []
        for name, column in (("family", "instrument_type"), ("market", "market"),
                             ("currency", "currency"), ("settlement", "settlement")):
            value = self.filters.get(name)
            if value:
                where.append(f"{prefix}{column}=? COLLATE NOCASE")
                params.append(value)
        if self.filters.get("q"):
            where.append(f"({prefix}ticker LIKE ? ESCAPE '\\' OR {prefix}description LIKE ? ESCAPE '\\')")
            needle = str(self.filters["q"]).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            params.extend(["%" + needle + "%"] * 2)
        return where, params

    def catalog(self, limit=10, families=()):
        s = self.store
        exact = {"ticker", "instrument_type", "market", "currency", "settlement"}
        if not exact <= s.columns("financial_instrument_catalog"):
            return Page("financial_instrument_catalog", reason="EXACT_CATALOG_IDENTITY_UNAVAILABLE")
        where, params = self.instrument_filter()
        if families:
            where.append("c.instrument_type IN (" + ",".join("?" for _ in families) + ")")
            params.extend(families)
        ready = exact | {"can_simulate", "status", "checked_at", "detail"}
        has_ready = ready <= s.columns("candidate_identity_v2")
        key = self.filters.get("identity")
        if key:
            try:
                values = json.loads(key)
                if not isinstance(values, list) or len(values) != 5 or not all(isinstance(v, str) for v in values):
                    raise ValueError
            except (ValueError, TypeError):
                return Page("financial_instrument_catalog", reason="EXACT_IDENTITY_REQUIRED")
            where.extend(f"c.{column}=?" for column in ("ticker", "instrument_type", "market", "currency", "settlement"))
            params.extend(values)
        readiness = self.filters.get("state")
        exact_join = " AND ".join(f"r.{c}=c.{c}" for c in ("ticker", "instrument_type", "market", "currency", "settlement"))
        ready_expression = "r.can_simulate=1 AND upper(r.status)='AVAILABLE'"
        if readiness in {"RUNTIME_READY", "NO_READY"}:
            where.append(("" if readiness == "RUNTIME_READY" else "NOT ") +
                         f"EXISTS(SELECT 1 FROM candidate_identity_v2 r WHERE {exact_join} AND {ready_expression})"
                         if has_ready else "0" if readiness == "RUNTIME_READY" else "1")
        clause = " WHERE " + " AND ".join(where) if where else ""
        errors = len(s.errors)
        count = s.query("SELECT COUNT(*) n FROM financial_instrument_catalog c" + clause, params)
        page_sql = f"SELECT {s.select('financial_instrument_catalog','c')} FROM financial_instrument_catalog c{clause} ORDER BY c.ticker,c.instrument_type,c.market,c.currency,c.settlement LIMIT ? OFFSET ?"
        rows = s.query(page_sql, (*params, min(10, limit), self.offset))
        # At most ten exact keys are joined in one query, never ticker alone.
        if rows and has_ready:
            predicate = " OR ".join("(" + " AND ".join(f"{c}=?" for c in ("ticker", "instrument_type", "market", "currency", "settlement")) + ")" for _ in rows)
            args = tuple(r[c] for r in rows for c in ("ticker", "instrument_type", "market", "currency", "settlement"))
            candidates = s.query(f"SELECT {s.select('candidate_identity_v2')} FROM candidate_identity_v2 WHERE {predicate} LIMIT 20", args)
            mapping = {}
            duplicates = set()
            for candidate in candidates:
                exact_key = tuple(candidate[c] for c in ("ticker", "instrument_type", "market", "currency", "settlement"))
                if exact_key in mapping:
                    duplicates.add(exact_key)
                mapping[exact_key] = candidate
            for ambiguous in duplicates:
                mapping.pop(ambiguous)
        else:
            mapping = {}
        for row in rows:
            identity = tuple(row[c] for c in ("ticker", "instrument_type", "market", "currency", "settlement"))
            candidate = mapping.get(identity, {})
            row.update(family=row["instrument_type"], symbol=row["ticker"],
                       identity=json.dumps(identity, separators=(",", ":")),
                       readiness="RUNTIME_READY" if candidate.get("can_simulate") == 1 and candidate.get("status", "").upper() == "AVAILABLE" else "NO_READY" if candidate else UNKNOWN,
                       readiness_as_of=candidate.get("checked_at"), reason=candidate.get("detail"),
                       source="financial_instrument_catalog · PPI identity; candidate_identity_v2 readiness",
                       freshness=freshness(row.get("last_seen_at"), self.now, 14 * 86400),
                       entry_authority=UNKNOWN)
            row["metadata"] = contract_labels(validated_metrics(json_object(row.pop("metadata_json", None)), self.now))
            row["strategy_route"] = row["metadata"].get("strategy_route")
        if len(s.errors) != errors:
            return Page("financial_instrument_catalog + candidate_identity_v2", reason="CATALOG_READ_ERROR")
        return Page("financial_instrument_catalog + candidate_identity_v2", rows,
                    count[0]["n"] if count else None, "AVAILABLE", self.offset)

    def counts(self):
        s = self.store
        result = {}
        if {"can_simulate", "status"} <= s.columns("candidate_identity_v2"):
            row = s.query("SELECT COUNT(*) total,SUM(CASE WHEN can_simulate=1 AND upper(status)='AVAILABLE' THEN 1 ELSE 0 END) ready FROM candidate_identity_v2")
            if row:
                result.update(READY=row[0]["ready"] or 0, catalog_candidates=row[0]["total"])
        if "status" in s.columns("paper_positions"):
            row = s.query("SELECT COUNT(*) n FROM paper_positions WHERE status='OPEN'")
            if row:
                result["open_positions"] = row[0]["n"]
            if "closed_at" in s.columns("paper_positions"):
                condition, params = self.daily_where("closed_at")
                row = s.query("SELECT COUNT(*) n FROM paper_positions WHERE status='CLOSED' AND " + condition, params)
                if row:
                    result["closed_today"] = row[0]["n"]
        # All SHADOW stages belong to this same committed cut.
        funnel = self.shadow["report"].get("operational_funnel", {})
        if isinstance(funnel, dict):
            stages = funnel.get("counts", funnel)
            stages = stages if isinstance(stages, dict) else {}
            for stage in ("ELIGIBLE", "TRADEABLE", "DISCOVERY", "WARM", "HOT", "SIGNAL", "ECONOMICS", "RISK", "PAPER"):
                value = stages.get(stage)
                if isinstance(value, int) and value >= 0:
                    result[stage] = value
        return result

    def positions(self, closed=False, limit=10, today=False):
        s = self.store
        cols = s.columns("paper_positions")
        if not {"status", "paper_id", "opened_at"} <= cols:
            return Page("paper_positions", reason="POSITION_SCHEMA_UNAVAILABLE")
        where, params = ["status=?"], ["CLOSED" if closed else "OPEN"]
        if today and "closed_at" in cols:
            condition, args = self.daily_where("closed_at")
            where.append(condition)
            params.extend(args)
        for key, column in (("family", "asset_class"), ("currency", "currency"), ("market", "market"),
                            ("strategy", "strategy_version"), ("settlement", "settlement")):
            if self.filters.get(key) and column in cols:
                where.append(f"{column}=? COLLATE NOCASE")
                params.append(self.filters[key])
        if self.filters.get("q") and "symbol" in cols:
            where.append("symbol LIKE ?")
            params.append("%" + self.filters["q"] + "%")
        page = s.page("paper_positions", where=" AND ".join(where), params=params,
                      order="closed_at DESC,paper_id" if closed and "closed_at" in cols else "opened_at,paper_id", offset=self.offset, limit=limit)
        keys = [r["paper_id"] for r in page.rows]
        marks = {r["paper_id"]: r for r in s.by_keys("paper_position_marks", "paper_id", keys)}
        intents = {r["paper_id"]: r for r in s.by_keys("paper_exit_intents", "paper_id", keys)}
        sales = s.by_keys("paper_spot_sales", "paper_id", keys)
        supervisor = self.workers().rows if keys and not closed else []
        for row in page.rows:
            features = json_object(row.pop("features_json", None))
            row["features"] = features
            row.update(family=row.get("asset_class"), strategy=row.get("strategy_version"),
                       mark=marks.get(row["paper_id"], {}).get("mark_price"),
                       mark_as_of=marks.get(row["paper_id"], {}).get("book_at"),
                       exit_state=intents.get(row["paper_id"], {}).get("state"),
                       exit_reason=intents.get(row["paper_id"], {}).get("blocked_reason"),
                       exit_due_at=intents.get(row["paper_id"], {}).get("due_at"),
                       supervised_at=intents.get(row["paper_id"], {}).get("supervised_at"),
                       source=row.get("source") or "paper_positions",
                       side=row.get("side") or "NO_VERIFICADO", unit=features.get("quantity_unit"),
                       lifecycle=features.get("lifecycle_owner"), risk_contribution=features.get("risk_contribution"),
                       bid_depth=features.get("exit_bid_depth"), eod_at=features.get("eod_deadline"),
                       maxhold_at=features.get("max_hold_deadline"),
                       executable_mark="NO_VERIFICADO", unrealized_pnl=features.get("unrealized_pnl"),
                       mfe="NO_MEDIDO", mae="NO_MEDIDO", workers=supervisor)
            # Only measured trajectory evidence may qualify MFE/MAE.
            trajectory = features.get("trajectory_provenance")
            if isinstance(trajectory, dict) and trajectory.get("validated") is True and trajectory.get("source") and trajectory.get("as_of"):
                row.update(mfe=features.get("measured_mfe", "NO_MEDIDO"), mae=features.get("measured_mae", "NO_MEDIDO"))
            row["freshness"] = freshness(row.get("mark_as_of"), self.now)
            row["time_in_position_seconds"] = age(row.get("opened_at"), self.now)
            if closed and row.get("closed_at"):
                try:
                    end = datetime.fromisoformat(row["closed_at"].replace("Z", "+00:00"))
                    row["time_in_position_seconds"] = age(row.get("opened_at"), end)
                except (ValueError, TypeError):
                    row["time_in_position_seconds"] = None
            for prefix, deadline in (("maxhold", row.get("maxhold_at")), ("eod", row.get("eod_at"))):
                try:
                    end = datetime.fromisoformat(str(deadline).replace("Z", "+00:00"))
                    row[prefix + "_remaining"] = max(0, (end - self.now).total_seconds()) if end.tzinfo else None
                except (ValueError, TypeError):
                    row[prefix + "_remaining"] = None
            row["partial_sales"] = [r for r in sales if r["paper_id"] == row["paper_id"]]
        return page

    def decisions(self):
        s = self.store
        page = s.page("paper_decisions", order="id DESC" if "id" in s.columns("paper_decisions") else "", offset=self.offset)
        keys = [r.get("decision_key") for r in page.rows if r.get("decision_key")]
        gates = {r["decision_key"]: r for r in s.by_keys("trade_gate_evaluations", "decision_key", keys)}
        evidence = {r["decision_key"]: r for r in s.by_keys("decision_evidence_snapshots", "decision_key", keys)}
        for row in page.rows:
            features = json_object(row.pop("features_json", None))
            gate = gates.get(row.get("decision_key"), {})
            payload = json_object(evidence.get(row.get("decision_key"), {}).get("payload_json"))
            row.update(features=features, evidence=payload, gates=gate,
                       strategy=row.get("strategy_version"), as_of=row.get("decided_at"),
                       family=features.get("asset_class") or features.get("family"),
                       market=features.get("market"), currency=features.get("currency"), settlement=features.get("settlement"),
                       signal=gate.get("technical_gate", "NO_EVAL"), economics="NO_EVAL",
                       risk=gate.get("patrimonial_gate", "NOT_CALLED"),
                       final=gate.get("final_result", "OBSERVE_ONLY"),
                       reason=gate.get("reason") or row.get("reason"), score_is_probability=False)
            detail = json_object(gate.get("detail_json"))
            economics = detail.get("economics", {})
            if isinstance(economics, dict):
                row["economics"] = economics.get("state", "NO_EVAL")
                row["expected_cost"] = economics.get("expected_cost")
            row["previous_state"] = features.get("previous_state")
        return page

    def balances(self):
        s = self.store
        if not {"id", "currency", "measured_at"} <= s.columns("paper_equity_by_currency"):
            return Page("paper_equity_by_currency", reason="CURRENCY_LEDGER_UNAVAILABLE")
        rows = s.query(f"SELECT {s.select('paper_equity_by_currency', 'e')} FROM paper_equity_by_currency e JOIN (SELECT currency,MAX(id) id FROM paper_equity_by_currency GROUP BY currency) x ON e.id=x.id ORDER BY e.currency LIMIT 10")
        quality = {r["currency"]: r for r in s.page("paper_valuation_quality").rows}
        for row in rows:
            row.update(freshness=freshness(row["measured_at"], self.now),
                       valuation_state=quality.get(row["currency"], {}).get("state", UNKNOWN),
                       stale_positions=quality.get(row["currency"], {}).get("stale_positions"),
                       reserved=row.get("caucion_principal"), source="paper_equity_by_currency")
        return Page("paper_equity_by_currency + paper_valuation_quality", rows, len(rows), "AVAILABLE")

    def risk(self):
        s = self.store
        day = self.now.astimezone(TZ).date().isoformat()
        page = s.page("paper_daily_risk", where="day=?", params=(day,), order="currency", offset=self.offset) if "day" in s.columns("paper_daily_risk") else Page("paper_daily_risk")
        for row in page.rows:
            row.update(as_of=row.get("evaluated_at"), freshness=freshness(row.get("evaluated_at"), self.now),
                       source="DailyRisk · paper_daily_risk")
        return page

    def workers(self, critical=True):
        s = self.store
        rows = []
        datasets = (("paper_supervisor_state", "Supervisor de salidas"), ("paper_exit_reader_state", "Lector de salidas"),
                    ("intraday_scalping_worker_state", "Scalping"))
        for table, label in datasets:
            row = s.one(table)
            stamp = row.get("heartbeat_at")
            rows.append({**row, "worker": label, "source": table, "as_of": stamp,
                         "age_seconds": age(stamp, self.now), "freshness": freshness(stamp, self.now, 30),
                         "state": row.get("state", UNKNOWN) if freshness(stamp, self.now, 30) == "FRESH" else freshness(stamp, self.now, 30)})
        runtime = self.runtime
        rows.append({"worker": "Runtime PAPER factual", "source": "observer_state", "as_of": runtime.get("heartbeat_at"),
                     "age_seconds": runtime.get("heartbeat_age"), "freshness": runtime["heartbeat_freshness"],
                     "state": runtime.get("process_state", UNKNOWN) if runtime["heartbeat_freshness"] == "FRESH" else runtime["heartbeat_freshness"]})
        risk = self.risk()
        rows.append({"worker": "Riesgo diario", "source": risk.source,
                     "state": " · ".join(str(r.get("currency")) + ":" + str(r.get("state", UNKNOWN)) for r in risk.rows) or UNKNOWN,
                     "as_of": max((r.get("evaluated_at", "") for r in risk.rows), default=None)})
        status = self.shadow.get("status", {})
        rows.append({"worker": "Selector dinámico SHADOW", "source": "CURRENT.json · committed generation",
                     "state": self.shadow["state"], "as_of": status.get("as_of"),
                     "age_seconds": age(status.get("as_of"), self.now),
                     "freshness": freshness(status.get("as_of"), self.now, 30)})
        if not critical:
            row = s.one("paper_notification_worker")
            rows.append({**row, "worker": "Notificaciones", "source": "paper_notification_worker"})
        return Page("canonical worker states", rows, len(rows), "AVAILABLE")

    def sources(self):
        page = self.store.page("api_health", order="component" if "component" in self.store.columns("api_health") else "", offset=self.offset)
        for row in page.rows:
            primary = any(str(row.get(key, "")).upper().startswith("PPI") for key in ("source", "component"))
            row.update(role="PRIMARY" if primary else "COMPLEMENT / OBSERVE_ONLY",
                       as_of=row.get("checked_at"), provider_clock=None, receipt_clock=row.get("checked_at"),
                       freshness=freshness(row.get("checked_at"), self.now), scope=row.get("component"),
                       lkg=row.get("last_success_at"))
            if row["freshness"] != "FRESH":
                row["state"] = row["freshness"]
        if page.total == 0 or page.total is None:
            page.rows = [{"component": label, "role": role, "state": "SOURCE_UNAVAILABLE", "scope": label,
                          "source": "api_health", "freshness": UNKNOWN} for label, role in (
                ("PPI", "PRIMARY"), ("IOL", "COMPLEMENT"), ("BYMA public", "OBSERVE_ONLY"), ("A3 / ROFEX", "OBSERVE_ONLY"))]
            page.total = None
            page.state = UNKNOWN
        return page

    def history(self):
        s = self.store
        def canonical(history_store):
            cols = history_store.columns("history_canonical_v2")
            identity = ("symbol", "instrument_type", "market", "settlement")
            if not set(identity) | {"trading_date", "observed_at"} <= cols:
                return None
            grouped = ",".join(identity)
            sources = "CASE WHEN COUNT(DISTINCT source_class)=1 THEN MIN(source_class) ELSE 'MULTIPLE_SOURCE_CLASSES' END" if "source_class" in cols else "'NO_VERIFICADO'"
            quality = "SUM(CASE WHEN quality_state='FULL_OHLC' THEN 1 ELSE 0 END) full_ohlc," if "quality_state" in cols else ""
            count = history_store.query(f"SELECT COUNT(*) n FROM (SELECT 1 FROM history_canonical_v2 GROUP BY {grouped})")
            rows = history_store.query(f"SELECT {grouped},COUNT(*) row_count,MIN(trading_date) date_from,MAX(trading_date) date_to,MAX(observed_at) observed_at,{quality}{sources} source FROM history_canonical_v2 GROUP BY {grouped} ORDER BY {grouped} LIMIT 10 OFFSET ?", (self.offset,))
            return Page("history_canonical_v2 · historical identities / source clocks", rows,
                        count[0]["n"] if count else None, "AVAILABLE", self.offset)
        page = canonical(s)
        if page is None:
            history_path = Path(os.getenv("HIST_DB_PATH", "data/market_history.db"))
            if history_path.is_file() and history_path.resolve() != s.path.resolve():
                with Store(history_path, now=self.now) as h:
                    page = canonical(h)
                    s.query_count += h.query_count
        if page is None:
            page = s.page("production_history", offset=self.offset)
        for row in page.rows:
            row.update(family=row.get("family") or row.get("instrument_type"),
                       as_of=row.get("downloaded_at") or row.get("observed_at"),
                       freshness=freshness(row.get("downloaded_at") or row.get("observed_at"), self.now, 86400),
                       readiness_authority="NONE · histórico no gobierna readiness")
        return page

    def performance(self, today=False):
        s = self.store
        required = {"status", "currency", "asset_class", "strategy_version", "net_pnl", "gross_pnl", "entry_cost", "exit_cost"}
        if not required <= s.columns("paper_positions"):
            return Page("paper_positions", reason="PERFORMANCE_CURRENCY_ATTRIBUTION_UNAVAILABLE")
        condition, params = "status='CLOSED'", ()
        if today:
            day, params = self.daily_where("closed_at")
            condition += " AND " + day
        rows = s.query(f"""SELECT currency,asset_class family,strategy_version strategy,COUNT(*) sample_size,
            decimal_sum(net_pnl) net_pnl,decimal_sum(gross_pnl) gross_pnl,
            decimal_sum(decimal_add(entry_cost,exit_cost)) costs,
            SUM(CASE WHEN decimal_sign(net_pnl)>0 THEN 1 ELSE 0 END) wins,
            SUM(CASE WHEN decimal_sign(net_pnl)<0 THEN 1 ELSE 0 END) losses,
            SUM(CASE WHEN decimal_sign(net_pnl) IS NULL THEN 1 ELSE 0 END) invalid_amounts,
            decimal_sum(CASE WHEN decimal_sign(net_pnl)>0 THEN net_pnl ELSE '0' END) positive_sum,
            decimal_sum(CASE WHEN decimal_sign(net_pnl)<0 THEN net_pnl ELSE '0' END) negative_sum,
            MAX(closed_at) as_of
            FROM paper_positions WHERE {condition}
            GROUP BY currency,asset_class,strategy_version ORDER BY currency,asset_class,strategy_version LIMIT 10 OFFSET ?""", (*params, self.offset))
        totals = s.query(f"SELECT COUNT(*) n FROM (SELECT 1 FROM paper_positions WHERE {condition} GROUP BY currency,asset_class,strategy_version)", params)
        for row in rows:
            n = row["sample_size"]
            net = decimal_amount(row["net_pnl"])
            positive, negative = decimal_amount(row["positive_sum"]), decimal_amount(row["negative_sum"])
            valid = row["invalid_amounts"] == 0
            row.update(win_rate=100 * row["wins"] / n if n and valid else None,
                       expectancy=str(net / n) if n and net is not None else None,
                       profit_factor=str(positive / -negative) if valid and positive is not None and negative else "NO_DEFINIDO",
                       avg_win=str(positive / row["wins"]) if row["wins"] and positive is not None and valid else None,
                       avg_loss=str(negative / row["losses"]) if row["losses"] and negative is not None and valid else None,
                       drawdown=UNKNOWN, edge="NO_VERIFICADO · requiere OOS y muestra suficiente",
                       source="paper_positions · agregados de ledger por moneda/estrategia/familia")
        return Page("paper_positions · CLOSED ledger aggregates", rows, totals[0]["n"] if totals else None, "AVAILABLE", self.offset)

    def bounded_file(self, name, *, root=None):
        path = (root or self.store.path.parent) / name
        try:
            with os.fdopen(os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW), "rb") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    return {}, "EVIDENCE_NOT_REGULAR"
                data = stream.read(FILE_LIMIT + 1)
            if len(data) > FILE_LIMIT:
                return {}, "EVIDENCE_SIZE_LIMIT"
            payload = json.loads(data)
            return (payload, "AVAILABLE") if isinstance(payload, dict) else ({}, "EVIDENCE_INVALID")
        except (OSError, ValueError, TypeError):
            return {}, "EVIDENCE_NOT_PUBLISHED"
