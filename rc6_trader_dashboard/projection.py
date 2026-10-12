"""Request-scoped, bounded SQLite projection of canonical facts.

One read transaction, named columns, parameterized filters, ten rows and no
provider calls. Missing data never becomes a measured zero or new authority.
"""
from contextlib import AbstractContextManager
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from datetime import datetime, timedelta, timezone
from functools import cached_property
import hashlib
import json
import os
import re
from pathlib import Path
import sqlite3
import stat
from time import monotonic
from zoneinfo import ZoneInfo

from .generation import read_shadow

TZ = ZoneInfo("America/Argentina/Buenos_Aires")
UNKNOWN = "NO_VERIFICADO"
JSON_LIMIT = 32 * 1024
FILE_LIMIT = 1024 * 1024
ROW_LIMIT = 10


def funnel_cohort_id(row):
    """The producer's group key, using its native full identity order."""
    from rc6_performance.common import digest
    fields = ("session", "strategy_id", "strategy_version", "configuration_fingerprint", "registry_sha256")
    return digest([*[row.get(key) for key in fields], row.get("identity"), row.get("hour_art"), row.get("channel")])


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


def decimal_subtract(a, b):
    left, right = decimal_amount(a), decimal_amount(b)
    return str(left - right) if left is not None and right is not None else None


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


def verified_decision_snapshot(row, now):
    """Verify bounded immutable bytes and phase clocks before presentation."""
    text = row.get("payload_json")
    if (not isinstance(text, str) or len(text.encode("utf-8")) > JSON_LIMIT
            or hashlib.sha256(text.encode("utf-8")).hexdigest() != row.get("payload_sha256")):
        return {}
    payload = json_object(text)
    if (not payload or payload.get("decision_key") != row.get("decision_key")
            or payload.get("captured_at") != row.get("captured_at")
            or age(payload.get("captured_at"), now) is None):
        return {}
    runtime = payload.get("runtime") if isinstance(payload.get("runtime"), dict) else {}
    for clock in ("signal_at", "decision_at", "intent_at", "entry_fill_recorded_at", "entry_fill_committed_at", "admission_at"):
        value = payload.get(clock) or runtime.get(clock)
        if value is not None and age(value, now) is None:
            return {}
    try:
        from rc6_performance.common import decision_snapshot_phase
        decision_snapshot_phase(payload)
    except (ImportError, ValueError, TypeError, AttributeError, KeyError):
        return {}
    return payload


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
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            return UNKNOWN
        delta = now - stamp
        if delta < timedelta(0):
            return UNKNOWN
        return "FRESH" if delta <= timedelta(seconds=budget) else "STALE"
    except (ValueError, TypeError, OverflowError):
        return UNKNOWN


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
    "paper_future_positions": "lifecycle_id symbol currency market settlement side quantity cash_multiplier entry_price settlement_base_price last_mark_price margin_reserved entry_cost exit_cost variation_realized unrealized_pnl opened_at last_mark_at last_book_at expires_at status closed_at close_reason metadata_json",
    "paper_future_marks": "event_id lifecycle_id mark_price book_at observed_at is_settlement unrealized_pnl detail_json",
    "paper_future_exit_intents": "lifecycle_id state cause due_at blocked_reason supervised_at attempts",
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
    "history_canonical_v2": "ticker symbol family instrument_type market currency settlement date trading_date quality_state source_class source_ref source status row_count rows first_at last_at downloaded_at observed_at version_known_at last_checked_at known_at price_basis adjusted version_id payload_hash",
    "history_versions_v2": "id symbol instrument_type market currency settlement date price_basis adjustment_basis open high low close volume source adjusted provider_at observed_at version_known_at",
    "history_checks_v2": "version_id last_checked_at",
    "paper_learning_samples": "paper_id source strategy_version feature_timestamp label_timestamp net_return_pct outcome duration_minutes features_json",
    "report_registry": "id created_at generated_at report_type period_type period_key period_start period_end state pdf_path ai_path",
    "operational_jobs": "job_key job_name state started_at finished_at last_run_at next_run_at last_success_at detail",
    "paper_notification_worker": "id heartbeat_at state detail last_success_at last_error",
}


class Store(AbstractContextManager):
    def __init__(self, path, *, now=None, trace=None, shadow_filters=None):
        self.path = Path(path)
        self.now = now or datetime.now(timezone.utc)
        self.connection = None
        self.schema = {}
        self.errors = []
        self.query_count = 0
        self.trace = trace
        self.snapshot = None
        self.deadline = None
        self._shadow_filters = dict(shadow_filters) if shadow_filters is not None else None
        self._shadow_executor = None
        self._shadow_future = None

    def __enter__(self):
        self.deadline = monotonic() + 1.0
        try:
            from rc6_audit_evidence.sqlite_snapshot import readonly_copy
            from bs_instrument_contracts import register_exact_time_sql
            if self._shadow_filters is not None:
                # The sealed generation reader never touches the source SQLite
                # connection. Overlap these two independent captures, retaining
                # one request deadline and the exact normalized selection.
                shadow = Projection(self, self._shadow_filters)
                self._shadow_filters = dict(shadow.filters)
                self._shadow_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="rc6-dashboard-shadow")
                self._shadow_future = self._shadow_executor.submit(shadow._read_shadow)
            self.snapshot = readonly_copy(self.path, validate=False, deadline=self.deadline)
            self.connection = self.snapshot.__enter__()
            self.connection.row_factory = sqlite3.Row
            register_exact_time_sql(self.connection)
            self.connection.create_aggregate("decimal_sum", 1, DecimalSum)
            self.connection.create_function("decimal_add", 2, decimal_add, deterministic=True)
            self.connection.create_function("decimal_subtract", 2, decimal_subtract, deterministic=True)
            self.connection.create_function("decimal_sign", 1, decimal_sign, deterministic=True)
            self.connection.execute("PRAGMA query_only=ON")
            self.connection.execute("BEGIN")
            if self.trace:
                self.connection.set_trace_callback(self.trace)
        except (sqlite3.Error, OSError, ValueError, RuntimeError, ImportError):
            self.errors.append("DATABASE_READ_UNAVAILABLE")
            if self.snapshot:
                try:
                    self.snapshot.__exit__(None, None, None)
                except (ValueError, OSError, sqlite3.Error):
                    pass
                self.snapshot = None
            self.connection = None
        except BaseException as error:
            self.__exit__(type(error), error, error.__traceback__)
            raise
        return self

    def _join_shadow(self):
        executor, self._shadow_executor = self._shadow_executor, None
        if executor is not None:
            executor.shutdown(wait=True)

    def __exit__(self, *args):
        try:
            # Even a rejected snapshot or an exception while rendering must
            # join the bounded reader before the request releases its resources.
            self._join_shadow()
        finally:
            try:
                if self.snapshot:
                    try:
                        self.snapshot.__exit__(*args)
                    except (ValueError, OSError, sqlite3.Error):
                        self.errors.append("SOURCE_SNAPSHOT_REJECTED")
                elif self.connection:
                    self.connection.close()
            finally:
                self.connection = None
                if self.deadline is not None and monotonic() >= self.deadline:
                    if "SOURCE_SNAPSHOT_REJECTED" not in self.errors:
                        self.errors.append("SOURCE_SNAPSHOT_REJECTED")

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
        self.funnel_offset = max(0, min(100000, self.integer(self.filters.get("funnel_offset"), 0)))
        if "funnel_offset" in self.filters:
            self.filters["funnel_offset"] = str(self.funnel_offset)
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
        if (self.generation_reader is None and self.store._shadow_future is not None
                and self.filters == self.store._shadow_filters):
            return self.store._shadow_future.result()
        return self._read_shadow()

    def _read_shadow(self):
        try:
            from rc6_shadow_runtime.persistence import shadow_evidence_root
            root = shadow_evidence_root(self.store.path)
        except (ImportError, AttributeError):
            return {"state": UNKNOWN, "reason": "CANONICAL_SHADOW_ROOT_CONTRACT_UNAVAILABLE", "report": {}}
        except (OSError, ValueError):
            return {"state": UNKNOWN, "reason": "CANONICAL_SHADOW_ROOT_REJECTED", "report": {}}
        return read_shadow(root, self.generation_reader, filters=self.filters, offset=self.offset,
                           deadline=self.store.deadline)

    @cached_property
    def funnel_scope(self):
        """Cards and funnel consume one native selection; explicit filters never fall back."""
        if "funnel_scope" in self.shadow:
            return self.shadow["funnel_scope"]
        report = self.shadow["report"].get("operational_funnel")
        if not isinstance(report, dict):
            return {"state": UNKNOWN, "reason": "FUNNEL_NOT_PUBLISHED", "counts": {}, "groups": []}
        if report.get("schema") != "rc6.prospective-operational-funnel.v1":
            return {"state": "CONTRACT_ERROR", "reason": "FUNNEL_SCHEMA_UNSUPPORTED", "counts": {}, "groups": []}
        cohort_filters = {k: self.filters[k] for k in ("family", "strategy", "session", "cohort", "market", "settlement", "identity", "q") if self.filters.get(k)}
        member = "cohorts" if cohort_filters else "by_currency_channel"
        groups = report.get(member)
        if not isinstance(groups, list) or any(not isinstance(r, dict) or not isinstance(r.get("stages"), dict) for r in groups):
            return {"state": "CONTRACT_ERROR", "reason": "FUNNEL_GROUPS_CONTRACT_MISMATCH", "counts": {}, "groups": []}
        candidates = [r for r in groups if all(str(r.get(k, "")) == self.filters[k]
                      for k in ("currency", "channel") if self.filters.get(k))]
        aliases = {"strategy": "strategy_id"}
        def matches(row):
            for key, value in cohort_filters.items():
                if key == "cohort":
                    actual = funnel_cohort_id(row)
                elif key == "q":
                    if value.upper() not in str(row.get("symbol", "")).upper():
                        return False
                    continue
                elif key == "identity":
                    identity = row.get("identity")
                    actual = json.dumps([identity[0], identity[1], identity[4], identity[3], identity[2]], separators=(",", ":")) if isinstance(identity, list) and len(identity) == 5 else None
                    try:
                        if json.loads(actual or "null") != json.loads(value):
                            return False
                    except ValueError:
                        return False
                    continue
                else:
                    actual = row.get(aliases.get(key, key), "")
                if str(actual) != value:
                    return False
            return True
        candidates = [row for row in candidates if matches(row)]
        if not candidates:
            return {"state": "AVAILABLE" if not groups else UNKNOWN, "reason": "FUNNEL_SCOPE_NOT_PUBLISHED",
                    "counts": {}, "groups": groups, "as_of": report.get("as_of")}
        # Stable default independent of producer dict iteration; no aggregation
        # across currencies, channels or detailed cohorts is performed here.
        selected = min(candidates, key=lambda r: (r.get("channel") != "NATIVE_FACTUAL",
                       str(r.get("currency", "")), funnel_cohort_id(r) if "identity" in r else str(r.get("channel"))))
        names = {"READY": "CATALOG_READY", "ELIGIBLE": "STRATEGY_ELIGIBLE", "TRADEABLE": "TRADEABLE",
                 "DISCOVERY": "DISCOVERY_TOUCHED", "WARM": "WARM", "HOT": "HOT", "SIGNAL": "SIGNAL_CANDIDATE",
                 "ECONOMICS": "ECONOMICS_PASS", "RISK": "RISK_PASS", "PAPER": "PAPER_OPENED"}
        counts = {stage: selected["stages"].get(native) for stage, native in names.items()}
        if any(value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0) for value in counts.values()):
            return {"state": "CONTRACT_ERROR", "reason": "FUNNEL_STAGE_CARDINALITY_INVALID", "counts": {}, "groups": []}
        label = f"{selected.get('currency')} / {selected.get('channel')}"
        if cohort_filters:
            label += " · " + " / ".join(str(selected.get(k) or UNKNOWN) for k in ("session", "strategy_id", "symbol", "hour_art", "registry_sha256"))
        else:
            label += " · sesiones " + ", ".join(report.get("sessions_retained", []))
        return {"state": "AVAILABLE", "reason": "", "counts": counts, "selected": selected,
                "groups": candidates[self.funnel_offset:self.funnel_offset + 10], "total_groups": len(candidates),
                "groups_offset": self.funnel_offset, "groups_limit": 10, "label": label, "as_of": report.get("as_of"),
                "generation_id": self.shadow["pointer"]["generation_id"]}

    def daily_where(self, column):
        start = datetime.combine(self.now.astimezone(TZ).date(), datetime.min.time(), TZ)
        return f"rc6_instant_us({column})>=rc6_instant_us(?) AND rc6_instant_us({column})<rc6_instant_us(?)", (
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
        open_page = self.position_index()
        close_page = self.position_index(closed=True, today=True)
        if open_page.total is not None:
            result["open_positions"] = open_page.total
        if close_page.total is not None:
            result["closed_today"] = close_page.total
        # All SHADOW stages belong to this same committed cut.
        for stage, value in self.funnel_scope["counts"].items():
            if stage != "READY" and isinstance(value, int):
                result[stage] = value
        result["funnel_ready"] = self.funnel_scope["counts"].get("READY")
        result["funnel_scope"] = self.funnel_scope.get("label") or self.funnel_scope["reason"]
        return result

    def position_index(self, closed=False, limit=10, today=False):
        """Page the two ledgers by their own state at the exact request cut."""
        s = self.store
        selects, params = [], []
        identity = self.filters.get("identity")
        if identity:
            try:
                identity = json.loads(identity)
                if not isinstance(identity, list) or len(identity) != 5 or not all(isinstance(value, str) and value for value in identity):
                    raise ValueError
            except (ValueError, TypeError):
                return Page("paper_positions + paper_future_positions", reason="POSITION_FULL_IDENTITY_REQUIRED")
        for table, key, family in (("paper_positions", "paper_id", None),
                                   ("paper_future_positions", "lifecycle_id", "FUTUROS")):
            cols = s.columns(table)
            if not cols:
                continue
            required = {key, "opened_at", "closed_at", "status", "symbol", "currency", "market", "settlement"}
            if family is None:
                required.add("asset_class")
            if not required <= cols:
                return Page("paper_positions + paper_future_positions", reason="POSITION_IDENTITY_OR_CUT_SCHEMA_UNAVAILABLE")
            where = ["rc6_instant_us(opened_at)<=rc6_instant_us(?)"]
            args = [self.now.isoformat()]
            if closed:
                where.append("closed_at IS NOT NULL AND rc6_instant_us(closed_at)<=rc6_instant_us(?)")
            else:
                where.append("(closed_at IS NULL OR rc6_instant_us(closed_at)>rc6_instant_us(?))")
            args.append(self.now.isoformat())
            if identity:
                for column, value in zip(("symbol", "asset_class", "market", "currency", "settlement"), identity):
                    if column == "asset_class" and family:
                        where.append("?=? COLLATE NOCASE"); args.extend((family, value))
                    else:
                        where.append(f"{column}=? COLLATE NOCASE"); args.append(value)
            if closed and today:
                condition, values = self.daily_where("closed_at")
                where.append(condition); args.extend(values)
            for name, column in (("currency", "currency"), ("market", "market"), ("settlement", "settlement")):
                if self.filters.get(name):
                    where.append(f"{column}=? COLLATE NOCASE"); args.append(self.filters[name])
            if self.filters.get("family"):
                where.append("?=? COLLATE NOCASE" if family else "asset_class=? COLLATE NOCASE")
                args.extend((family, self.filters["family"]) if family else (self.filters["family"],))
            if self.filters.get("q"):
                where.append("symbol LIKE ?"); args.append("%" + str(self.filters["q"]) + "%")
            if self.filters.get("strategy"):
                if family:
                    where.append("?=?"); args.extend(("DLR_LONG_PAPER", self.filters["strategy"]))
                elif "strategy_version" in cols:
                    where.append("strategy_version=?"); args.append(self.filters["strategy"])
                else:
                    where.append("0")
            selects.append(f"SELECT '{table}' ledger,{key} ledger_id,opened_at,closed_at FROM {table} WHERE " + " AND ".join(where))
            params.extend(args)
        if not selects:
            return Page("paper_positions + paper_future_positions", reason="POSITION_LEDGERS_NOT_PUBLISHED")
        union = " UNION ALL ".join(selects)
        errors = len(s.errors)
        count = s.query("SELECT COUNT(*) n FROM (" + union + ")", params)
        ordering = "rc6_instant_us(closed_at) DESC" if closed else "rc6_instant_us(opened_at)"
        rows = s.query("SELECT ledger,ledger_id,opened_at,closed_at FROM (" + union + ") ORDER BY " + ordering + ",ledger,ledger_id LIMIT ? OFFSET ?", (*params, min(10, limit), self.offset))
        if len(s.errors) != errors:
            return Page("paper_positions + paper_future_positions", reason="POSITION_CUT_OR_READ_ERROR")
        return Page("paper_positions + paper_future_positions", rows, count[0]["n"] if count else None, "AVAILABLE", self.offset, self.now.isoformat())

    def positions(self, closed=False, limit=10, today=False):
        page = self.position_index(closed, limit, today)
        spot_keys = [r["ledger_id"] for r in page.rows if r["ledger"] == "paper_positions"]
        future_keys = [r["ledger_id"] for r in page.rows if r["ledger"] == "paper_future_positions"]
        result = {}
        if spot_keys:
            for row in self._spot_positions(closed, limit, today, keys=spot_keys).rows:
                row["ledger"] = "paper_positions"
                result[("paper_positions", row["paper_id"])] = row
        if future_keys:
            try:
                from rc6_paper_family_lifecycle import future_positions
                rows = future_positions(None, connection=self.store.connection, lifecycle_ids=future_keys, as_of=self.now)
                future_intents = {row["lifecycle_id"]: row for row in self.store.by_keys("paper_future_exit_intents", "lifecycle_id", future_keys)
                                  if age(row.get("supervised_at"), self.now) is not None}
                for row in rows:
                    metadata = json_object(row.pop("metadata_json", None))
                    ident = [row.get("symbol"), "FUTUROS", row.get("market"), row.get("currency"), row.get("settlement")]
                    row.update(paper_id=row["lifecycle_id"], family="FUTUROS", strategy=metadata.get("strategy_id") or "DLR_LONG_PAPER",
                               identity=json.dumps(ident, separators=(",", ":")), ledger="paper_future_positions",
                               source="paper_future_positions + paper_future_marks + paper_family_lifecycle_events",
                               unit="CONTRACTS", lifecycle="DLR_LONG_PAPER · specialized family lifecycle", features=metadata,
                               mark=row.get("last_mark_price"), mark_as_of=row.get("last_book_at"),
                               freshness=freshness(row.get("last_book_at"), self.now, 120),
                               unrealized_pnl=row.get("unrealized_pnl"), net_pnl=row.get("realized_pnl"),
                               gross_pnl=row.get("gross_realized_pnl"), reserve_policy="CONSERVATIVE_PAPER_NOTIONAL; broker margin NO_VERIFICADO",
                               notional=row.get("exposure"), normalized_exposure=row.get("exposure"),
                               remaining_quantity=row.get("quantity") if row.get("status") == "ACTIVE" else "0",
                               eod_at=metadata.get("eod_at"), maxhold_at=metadata.get("maxhold_at"),
                               stop_price=metadata.get("stop_price"), target_price=metadata.get("target_price"),
                               time_in_position_seconds=age(row.get("opened_at"), self.now),
                               executable_mark=UNKNOWN, exit_state=UNKNOWN, risk_contribution=UNKNOWN,
                               mfe="NO_MEDIDO", mae="NO_MEDIDO", entry_authority=False,
                               as_of=self.now.isoformat())
                    intent = future_intents.get(row["lifecycle_id"], {})
                    if intent:
                        row.update(exit_state=intent.get("state", UNKNOWN), exit_due_at=intent.get("due_at"),
                                   exit_reason=intent.get("blocked_reason") or intent.get("cause"),
                                   supervised_at=intent.get("supervised_at"), exit_cause=intent.get("cause"),
                                   exit_attempts=intent.get("attempts"), exit_freshness=freshness(intent.get("supervised_at"), self.now, 30))
                        if row["exit_freshness"] != "FRESH":
                            row.update(recorded_exit_state=row["exit_state"], exit_state=row["exit_freshness"])
                    result[("paper_future_positions", row["lifecycle_id"])] = row
            except (ValueError, TypeError, RuntimeError, sqlite3.Error, ImportError):
                return Page(page.source, state=UNKNOWN, reason="FUTURE_CANONICAL_PROJECTION_REJECTED")
        if any((r["ledger"], r["ledger_id"]) not in result for r in page.rows):
            return Page(page.source, state=UNKNOWN, reason="POSITION_PROJECTION_INCOMPLETE_AT_CUT")
        page.rows = [result[(r["ledger"], r["ledger_id"])] for r in page.rows]
        return page

    def _spot_positions(self, closed=False, limit=10, today=False, *, keys=None):
        s = self.store
        cols = s.columns("paper_positions")
        if not {"status", "paper_id", "opened_at"} <= cols:
            return Page("paper_positions", reason="POSITION_SCHEMA_UNAVAILABLE")
        where, params = ["status=?"], ["CLOSED" if closed else "OPEN"]
        if keys is not None:
            where.append("paper_id IN (" + ",".join("?" for _ in keys) + ")")
            params.extend(keys)
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
                      order="closed_at DESC,paper_id" if closed and "closed_at" in cols else "opened_at,paper_id", offset=0 if keys is not None else self.offset, limit=limit)
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
        if "decided_at" not in s.columns("paper_decisions"):
            return Page("paper_decisions", reason="DECISION_CUT_SCHEMA_UNAVAILABLE")
        page = s.page("paper_decisions", where="rc6_instant_us(decided_at)<=rc6_instant_us(?)", params=(self.now.isoformat(),),
                      order="id DESC" if "id" in s.columns("paper_decisions") else "", offset=self.offset)
        keys = [r.get("decision_key") for r in page.rows if r.get("decision_key")]
        gates = {r["decision_key"]: r for r in s.by_keys("trade_gate_evaluations", "decision_key", keys)}
        evidence = {r["decision_key"]: r for r in s.by_keys("decision_evidence_snapshots", "decision_key", keys)}
        payloads = {key: verified_decision_snapshot(row, self.now) for key, row in evidence.items()}
        links = {key: payload.get("inputs_used", {}).get("financial_admission_snapshot_key")
                 for key, payload in payloads.items() if isinstance(payload.get("inputs_used"), dict)}
        receipt_keys = [link for key, link in links.items() if isinstance(key, str) and link == "PAPER_ADMISSION:" + key]
        receipt_rows = {r["decision_key"]: r for r in s.by_keys("decision_evidence_snapshots", "decision_key", receipt_keys)}
        for row in page.rows:
            features = json_object(row.pop("features_json", None))
            gate = gates.get(row.get("decision_key"), {})
            if age(gate.get("evaluated_at"), self.now) is None:
                gate = {}
            payload = payloads.get(row.get("decision_key"), {})
            row.update(features=features, evidence=payload, gates=gate,
                       strategy=row.get("strategy_version"), as_of=row.get("decided_at"),
                       family=features.get("asset_class") or features.get("family"),
                       market=features.get("market"), currency=features.get("currency"), settlement=features.get("settlement"),
                       signal=gate.get("technical_gate", "NO_EVAL"), economics="NO_EVAL",
                       risk=gate.get("patrimonial_gate", "NOT_CALLED"),
                       final=gate.get("final_result", "OBSERVE_ONLY"),
                       reason=gate.get("reason") or row.get("reason"), score_is_probability=False)
            quote = payload.get("quote_used") if isinstance(payload.get("quote_used"), dict) else {}
            row.update(provider_clock=quote.get("book_at") or quote.get("trade_at"),
                       receipt_clock=quote.get("received_at") or quote.get("observed_at"),
                       source="paper_decisions + immutable decision_evidence_snapshots · input at decision_at",
                       decision_input_source=quote.get("source"), decision_input_identity={key: quote.get(key) for key in
                           ("symbol", "asset_class", "market", "currency", "settlement")},
                       evidence_captured_at=evidence.get(row.get("decision_key"), {}).get("captured_at"),
                       evidence_sha256=evidence.get(row.get("decision_key"), {}).get("payload_sha256"),
                       evidence_phase=payload.get("capture_phase", "NATIVE_DECISION") if payload else UNKNOWN,
                       evidence_state="DIGEST_VERIFIED_IMMUTABLE" if payload else UNKNOWN,
                       entry_fill_committed_at=payload.get("entry_fill_committed_at"),
                       panel_freshness_does_not_prove_decision_input=True)
            link = links.get(row.get("decision_key"))
            receipt_row = receipt_rows.get(link, {})
            receipt = verified_decision_snapshot(receipt_row, self.now)
            inputs = payload.get("inputs_used") if isinstance(payload.get("inputs_used"), dict) else {}
            native_quote = payload.get("quote_used") if isinstance(payload.get("quote_used"), dict) else {}
            admission_quote = receipt.get("quote_used") if isinstance(receipt.get("quote_used"), dict) else {}
            same_identity = all(native_quote.get(key) and native_quote.get(key) == admission_quote.get(key)
                                for key in ("symbol", "asset_class", "market", "currency", "settlement"))
            native_decision = payload.get("decision") if isinstance(payload.get("decision"), dict) else {}
            admission_decision = receipt.get("decision") if isinstance(receipt.get("decision"), dict) else {}
            linked = (receipt.get("capture_phase") == "ATOMIC_PAPER_ADMISSION"
                      and receipt.get("native_decision_key") == row.get("decision_key")
                      and receipt_row.get("payload_sha256") == inputs.get("financial_admission_snapshot_sha256")
                      and same_identity and native_decision.get("paper_id")
                      and native_decision.get("paper_id") == admission_decision.get("paper_id"))
            row.update(admission_state="DIGEST_VERIFIED_LINKED_ATOMIC_RECEIPT" if linked else UNKNOWN,
                       admission_reason="" if linked else "RECEIPT_NOT_AVAILABLE_AT_CUT_OR_REJECTED" if link else "NOT_PUBLISHED_FOR_THIS_DECISION",
                       admission_snapshot_key=link if linked else None,
                       admission_snapshot_sha256=receipt_row.get("payload_sha256") if linked else None,
                       admission_at=receipt.get("admission_at") if linked else None,
                       admission_captured_at=receipt.get("captured_at") if linked else None,
                       entry_fill_recorded_at=receipt.get("entry_fill_recorded_at") if linked else None)
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
        rows = s.query(f"SELECT {s.select('paper_equity_by_currency', 'e')} FROM paper_equity_by_currency e JOIN (SELECT currency,MAX(id) id FROM paper_equity_by_currency WHERE rc6_instant_us(measured_at)<=rc6_instant_us(?) GROUP BY currency) x ON e.id=x.id ORDER BY e.currency LIMIT 10", (self.now.isoformat(),))
        quality = ({r["currency"]: r for r in s.page("paper_valuation_quality",
                   where="rc6_instant_us(measured_at)<=rc6_instant_us(?)", params=(self.now.isoformat(),)).rows}
                   if "measured_at" in s.columns("paper_valuation_quality") else {})
        for row in rows:
            row.update(freshness=freshness(row["measured_at"], self.now),
                       valuation_state=quality.get(row["currency"], {}).get("state", UNKNOWN),
                       stale_positions=quality.get(row["currency"], {}).get("stale_positions"),
                       reserved=row.get("caucion_principal"), source="paper_equity_by_currency")
        return Page("paper_equity_by_currency + paper_valuation_quality", rows, len(rows), "AVAILABLE")

    def risk(self):
        s = self.store
        day = self.now.astimezone(TZ).date().isoformat()
        if not {"day", "evaluated_at"} <= s.columns("paper_daily_risk"):
            return Page("paper_daily_risk", reason="DAILY_RISK_CUT_SCHEMA_UNAVAILABLE")
        page = s.page("paper_daily_risk", where="day=? AND rc6_instant_us(evaluated_at)<=rc6_instant_us(?)",
                      params=(day, self.now.isoformat()), order="currency", offset=self.offset)
        for row in page.rows:
            row.update(as_of=row.get("evaluated_at"), freshness=freshness(row.get("evaluated_at"), self.now),
                       source="DailyRisk · paper_daily_risk")
            if row["freshness"] != "FRESH":
                row.update(recorded_state=row.get("state"), state=row["freshness"])
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
        from cg_paper_workspace import artifact_root
        health, read_state = self.bounded_file("runtime-health.json", root=artifact_root(s.path))
        children = health.get("children")
        valid_health = (read_state == "AVAILABLE" and health.get("schema") == "rc6.runtime-child-health.v1"
                        and health.get("mode") == "PRODUCTION_PAPER"
                        and type(health.get("real_orders_sent")) is int and health["real_orders_sent"] == 0
                        and health.get("real_routes") == "NOT_CALLED"
                        and isinstance(children, dict) and freshness(health.get("recorded_at"), self.now, 30) == "FRESH")
        child = children.get("dynamic_shadow", {}) if valid_health else {}
        if not isinstance(child, dict):
            child = {}
            valid_health = False
        child_state = child.get("state", UNKNOWN)
        if (child_state not in {"RUNNING", "STARTUP_WAIT", "SPAWN_FAILED", "CRASH_BACKOFF", "STOPPED"}
                or any(type(child.get(key)) is not int or child[key] < 0 for key in ("restarts", "spawn_failures"))
                or (child_state == "RUNNING" and (type(child.get("pid")) is not int or child["pid"] <= 0))
                or (child_state != "RUNNING" and child.get("pid") is not None)
                or child.get("last_error") not in {None, "CHILD_EXITED", "CHILD_SPAWN_FAILED"}):
            child_state = UNKNOWN
        provenance_valid = all(value is None or (isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value))
                               for value in (health.get("source_sha"), health.get("candidate_tree_sha")))
        for variable, field in (("POROTA_BUILD_SHA", "source_sha"), ("POROTA_CANDIDATE_TREE_SHA", "candidate_tree_sha")):
            expected = os.getenv(variable)
            if expected and health.get(field) != expected:
                provenance_valid = False
        if not provenance_valid:
            child_state = UNKNOWN
        status = self.shadow.get("status", {})
        gen_fresh = freshness(status.get("as_of"), self.now, 30)
        rows.append({"worker": "Selector dinámico SHADOW", "source": "runtime-health.json · child + CURRENT.json independently",
                     "state": child_state, "as_of": health.get("recorded_at") if valid_health else None,
                     "pid": child.get("pid"), "restarts": child.get("restarts"), "spawn_failures": child.get("spawn_failures"),
                     "last_error": child.get("last_error"), "generation_state": self.shadow["state"],
                     "generation_as_of": status.get("as_of"), "generation_freshness": gen_fresh,
                     "generation_id": self.shadow.get("pointer", {}).get("generation_id"),
                     "verification_level": self.shadow.get("verification_level", UNKNOWN),
                     "source_sha": health.get("source_sha") if provenance_valid else None,
                     "candidate_tree_sha": health.get("candidate_tree_sha") if provenance_valid else None,
                     "age_seconds": age(health.get("recorded_at"), self.now) if valid_health else None,
                     "freshness": "FRESH" if valid_health else UNKNOWN,
                     "operational_readiness": "PREOPEN_NON_OPERATIONAL" if self.shadow["report"].get("phase") == "PREOPEN" else UNKNOWN})
        budget = health.get("ppi_budget") if valid_health else {}
        budget = budget if isinstance(budget, dict) else {}
        budget_state = budget.get("status", UNKNOWN)
        if budget.get("schema") != "RC6_RUNTIME_BUDGET_SNAPSHOT_V1" or budget_state not in {"ABSENT", "OBSERVED", "DEGRADED", "UNVERIFIED"}:
            budget_state = UNKNOWN
        try:
            clock = budget.get("source_last_clock")
            budget_as_of = datetime.fromtimestamp(clock, timezone.utc).isoformat() if type(clock) in {int, float} else None
        except (ValueError, OverflowError, OSError):
            budget_as_of = None
        rows.append({"worker": "Presupuesto global PPI · observación", "source": "runtime-health.json · canonical runtime_budget_snapshot",
                     "state": budget_state, "as_of": budget_as_of, "freshness": freshness(budget_as_of, self.now, 30),
                     "age_seconds": budget.get("age_seconds"), "global_counters": budget.get("global"),
                     "exit_service": budget.get("exit_service"), "telemetry_retention": budget.get("telemetry_retention"),
                     "operational_readiness": UNKNOWN, "open_capacity": UNKNOWN})
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
            from .history import causal_history_sql, REQUIRED_VERSION_FIELDS
            cols = history_store.columns("history_versions_v2")
            identity = ("symbol", "instrument_type", "market", "currency", "settlement", "price_basis", "adjustment_basis")
            if not REQUIRED_VERSION_FIELDS <= cols or history_store.connection is None:
                return None
            cte, args = causal_history_sql(history_store.connection, self.now, self.filters)
            grouped = ",".join(identity)
            sources = "CASE WHEN COUNT(DISTINCT source)=1 THEN MIN(source) ELSE 'MULTIPLE_SOURCE_CLASSES' END"
            errors = len(history_store.errors)
            count = history_store.query(cte + f"SELECT COUNT(*) n FROM (SELECT 1 FROM causal_history GROUP BY {grouped})", args)
            checks = {"version_id", "last_checked_at"} <= history_store.columns("history_checks_v2")
            checked = "MAX(CASE WHEN rc6_instant_us(c.last_checked_at)<=rc6_instant_us(?) THEN c.last_checked_at END)" if checks else "NULL"
            join = " LEFT JOIN history_checks_v2 c ON c.version_id=h.id" if checks else ""
            rows = history_store.query(cte + f"SELECT {grouped},COUNT(*) row_count,MIN(date) date_from,MAX(date) date_to,MAX(observed_at) observed_at,MAX(version_known_at) version_known_at,{checked} last_checked_at,{sources} source FROM causal_history h{join} GROUP BY {grouped} ORDER BY {grouped} LIMIT 10 OFFSET ?", (*args, *((self.now.isoformat(),) if checks else ()), self.offset))
            if len(history_store.errors) != errors:
                return Page("history_versions_v2 · causal historical series", reason="HISTORY_CAUSAL_READ_REJECTED")
            for row in rows:
                row.update(family=row["instrument_type"], concept="HISTORICAL_PRICE_SERIES",
                           identity=json.dumps([row[k] for k in ("symbol", "instrument_type", "market", "currency", "settlement")], separators=(",", ":")),
                           as_of=row["version_known_at"], market_event_date=row["date_to"],
                           freshness=freshness(row["version_known_at"], self.now, 86400),
                           readiness_authority="NONE · histórico no gobierna readiness",
                           decision_input_authority="NONE · decisión consume su snapshot inmutable",
                           entry_authority=False)
            return Page("history_versions_v2 → canonical authority as_of · full identity / currency / price basis", rows,
                        count[0]["n"] if count else None, "AVAILABLE", self.offset, self.now.isoformat())
        page = canonical(s)
        if page is None:
            history_path = Path(os.getenv("HIST_DB_PATH", "data/market_history.db"))
            if history_path.is_file() and history_path.resolve() != s.path.resolve():
                with Store(history_path, now=self.now) as h:
                    page = canonical(h)
                    s.query_count += h.query_count
                if "SOURCE_SNAPSHOT_REJECTED" in h.errors:
                    s.errors.append("SOURCE_SNAPSHOT_REJECTED")
                    return Page("history_versions_v2", reason="HISTORY_SOURCE_SNAPSHOT_REJECTED")
        if page is None:
            return Page("history_canonical_v2 · full identity / currency / price basis",
                        reason="HISTORY_FULL_IDENTITY_SCHEMA_UNAVAILABLE; production_history is download-status only")
        return page

    def performance(self, today=False):
        s = self.store
        sources = []
        spot = s.columns("paper_positions")
        future = s.columns("paper_future_positions")
        shared = {"status", "currency", "symbol", "market", "settlement", "entry_cost", "exit_cost", "opened_at", "closed_at"}
        if spot:
            if not shared | {"asset_class", "strategy_version", "net_pnl", "gross_pnl"} <= spot:
                return Page("paper_positions", reason="PERFORMANCE_CURRENCY_ATTRIBUTION_UNAVAILABLE")
            sources.append("SELECT currency,symbol,market,settlement,asset_class family,strategy_version strategy,net_pnl,gross_pnl,entry_cost,exit_cost,opened_at,closed_at,status FROM paper_positions")
        if future:
            if not shared | {"variation_realized"} <= future:
                return Page("paper_future_positions", reason="PERFORMANCE_FUTURE_ACCOUNTING_SCHEMA_UNAVAILABLE")
            # Canonical close atomically freezes these amounts. Collateral and
            # its release are cash movements, never costs or PnL.
            sources.append("SELECT currency,symbol,market,settlement,'FUTUROS' family,'DLR_LONG_PAPER' strategy,decimal_subtract(variation_realized,decimal_add(entry_cost,exit_cost)) net_pnl,variation_realized gross_pnl,entry_cost,exit_cost,opened_at,closed_at,status FROM paper_future_positions")
        if not sources:
            return Page("paper_positions + paper_future_positions", reason="PERFORMANCE_LEDGERS_NOT_PUBLISHED")
        condition = "status='CLOSED' AND rc6_instant_us(opened_at)<=rc6_instant_us(?) AND rc6_instant_us(closed_at)<=rc6_instant_us(?)"
        params = [self.now.isoformat(), self.now.isoformat()]
        if today:
            day, args = self.daily_where("closed_at")
            condition += " AND " + day
            params.extend(args)
        for key in ("currency", "family", "market", "settlement", "strategy"):
            if self.filters.get(key):
                condition += f" AND {key}=? COLLATE NOCASE"
                params.append(self.filters[key])
        if self.filters.get("q"):
            condition += " AND symbol LIKE ?"
            params.append("%" + self.filters["q"] + "%")
        union = " UNION ALL ".join(sources)
        errors = len(s.errors)
        rows = s.query(f"""SELECT currency,family,strategy,COUNT(*) sample_size,
            decimal_sum(net_pnl) net_pnl,decimal_sum(gross_pnl) gross_pnl,
            decimal_sum(decimal_add(entry_cost,exit_cost)) costs,
            SUM(CASE WHEN decimal_sign(net_pnl)>0 THEN 1 ELSE 0 END) wins,
            SUM(CASE WHEN decimal_sign(net_pnl)<0 THEN 1 ELSE 0 END) losses,
            SUM(CASE WHEN decimal_sign(net_pnl) IS NULL THEN 1 ELSE 0 END) invalid_amounts,
            decimal_sum(CASE WHEN decimal_sign(net_pnl)>0 THEN net_pnl ELSE '0' END) positive_sum,
            decimal_sum(CASE WHEN decimal_sign(net_pnl)<0 THEN net_pnl ELSE '0' END) negative_sum,
            MAX(closed_at) as_of
            FROM ({union}) WHERE {condition}
            GROUP BY currency,family,strategy ORDER BY currency,family,strategy LIMIT 10 OFFSET ?""", (*params, self.offset))
        totals = s.query(f"SELECT COUNT(*) n FROM (SELECT 1 FROM ({union}) WHERE {condition} GROUP BY currency,family,strategy)", params)
        if len(s.errors) != errors:
            return Page("paper_positions + paper_future_positions", reason="PERFORMANCE_READ_OR_AMOUNT_REJECTED")
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
                       source="paper_positions + paper_future_positions · cierres comprometidos por moneda/estrategia/familia")
        return Page("paper_positions + paper_future_positions · CLOSED ledger aggregates", rows, totals[0]["n"] if totals else None, "AVAILABLE", self.offset)

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
