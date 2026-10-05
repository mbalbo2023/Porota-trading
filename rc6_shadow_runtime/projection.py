"""Immutable derived index inside a sealed SHADOW generation.

It contains presentation rows and complete cohort aggregates, never financial
authority. Queries open verified bytes in memory; source databases are untouched.
"""
from copy import deepcopy
import hashlib
import json
import sqlite3
import time
import zlib

from rc6_dynamic_universe.common import digest, stamp

SCHEMA = "rc6.shadow-ui-projection.v1"
EXPORT_SCHEMA = "rc6.shadow-ui-committed-projection.v1"
LEVEL = "WIRE_AND_PROJECTION_SEMANTICS"
MAX_ROWS = 200000
MAX_BYTES = 64 * 1024**2
MAX_ROW_BYTES = 256 * 1024
TABLES = {"projection_header", "projection_rows"}
KINDS = ("opportunities", "discovery", "tradeability", "exclusions", "events", "capacity",
         "families", "strategies", "signals", "experiments", "exits", "event-risk")
HEADER_KEYS = ("generation_id", "generation_schema", "sequence", "source_watermark",
               "configuration_fingerprint", "as_of", "safety")
PLAN_FIELDS = ("identity", "state", "rank", "strategy", "strategy_id", "strategy_source_at",
    "last_useful_observation_at", "signal_result", "economics_result", "risk_result", "warmup_progress",
    "tradeability_score", "rejection_reason", "rank_components", "selected_at", "promoted_at", "demoted_at",
    "revisit_seconds", "achieved_revisit_seconds", "discovery_age", "book_at", "usable_observation_fraction",
    "pipeline", "preopen_digest", "provenance", "entry_authority", "source", "family")
COUNTS = {"READY": "CATALOG_READY", "ELIGIBLE": "STRATEGY_ELIGIBLE", "TRADEABLE": "TRADEABLE",
    "DISCOVERY": "DISCOVERY_TOUCHED", "WARM": "WARM", "HOT": "HOT", "SIGNAL": "SIGNAL_CANDIDATE",
    "ECONOMICS": "ECONOMICS_PASS", "RISK": "RISK_PASS", "PAPER": "PAPER_OPENED"}


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str, allow_nan=False)


def cohort_id(row):
    # This is the presentation selector over a complete native cohort, not a
    # replacement for its native event/entity identity.
    from rc6_performance.common import digest as native_digest
    keys = ("session", "strategy_id", "strategy_version", "configuration_fingerprint", "registry_sha256")
    return native_digest([*[row.get(key) for key in keys], row.get("identity"), row.get("hour_art"), row.get("channel")])


def native_rows(report):
    for engine, plan in sorted(report.get("engines", {}).items()):
        for ordinal, row in enumerate(plan.get("telemetry", [])):
            yield "planner", {**{key: row[key] for key in PLAN_FIELDS if key in row}, "engine": engine}, False
        capacity = {key: plan[key] for key in ("capacity", "discovery", "opened_priority", "hot_count", "warm_count",
            "discovery_count", "exit_capacity_contract") if key in plan}
        capacity["engine"] = engine
        # Native revisits are indexed separately; only the visible ten are
        # embedded in a capacity presentation row.
        capacity["telemetry"] = plan.get("telemetry", [])[:10]
        yield "capacity", capacity, False
    family = report.get("family_routing")
    for row in family.get("families", []) if isinstance(family, dict) else family if isinstance(family, list) else []:
        yield "families", row, False
    signals = report.get("entry_signal_lab", {})
    for kind, key in (("signals", "cohorts"), ("experiments", "experiments")):
        rows = signals.get(key, [])
        if kind == "signals" and not rows and signals.get("experiments"):
            rows = signals["experiments"]
        for row in rows: yield kind, row, True
    for row in report.get("economic_exit_lab", {}).get("entries", []): yield "exits", row, True
    for row in report.get("event_risk", []) if isinstance(report.get("event_risk"), list) else []:
        yield "event-risk", row, False
    funnel = report.get("operational_funnel", {})
    for row in funnel.get("cohorts", []): yield "funnel_cohorts", row, True
    for row in funnel.get("by_currency_channel", []): yield "funnel_aggregates", row, True


def index_record(dataset, ordinal, row, native):
    raw = encoded(row)
    if len(raw.encode()) > MAX_ROW_BYTES:
        raise ValueError("SHADOW_PROJECTION_ROW_LIMIT")
    ident = row.get("identity") or (row.get("input") or row.get("entry") or {}).get("identity") or []
    if len(ident) == 5:
        ticker, family = ident[:2]
        settlement, currency, market = ident[2:] if native else (ident[4], ident[3], ident[2])
    else:
        ticker, family, currency, market, settlement = (row.get(key, "") for key in
            ("symbol", "family", "currency", "market", "settlement"))
    upper = lambda value: str(value or "").upper()
    inp = row.get("input") or row.get("entry") or {}
    strategy = row.get("strategy_id") or row.get("strategy") or inp.get("strategy_id") or inp.get("strategy_version") or row.get("strategy_version")
    state = row.get("state") or row.get("status")
    event_clocks = [stamp(row[key]).isoformat() for key in ("promoted_at", "demoted_at") if row.get(key)]
    event_at = max(event_clocks, default="")
    rank = row.get("rank")
    if isinstance(rank, bool) or not isinstance(rank, (int, float)): rank = None
    return (dataset, ordinal, upper(ticker), upper(family), upper(currency), upper(market), upper(settlement),
        upper(strategy), upper(row.get("channel")), upper(state), cohort_id(row) if dataset.startswith("funnel") else "",
        upper(row.get("session")), event_at, {"HOT": 0, "WARM": 1, "DISCOVERY": 2}.get(upper(state), 3),
        rank is None, rank, bool(row.get("rejection_reason")), sqlite3.Binary(zlib.compress(raw.encode())))


def report_header(report):
    result = {key: deepcopy(value) for key, value in report.items() if key not in
        {"engines", "frozen", "tradeability", "family_routing", "operational_funnel", "economic_exit_lab",
         "entry_signal_lab", "source_reports", "catalog_ready", "events", "native_ppi_errors"}}
    result["projection_scope"] = "DERIVED_HEADERS_AND_PAGED_ROWS; FULL_NATIVE_REPORT_NOT_MATERIALIZED"
    result["engines"] = {engine: {key: deepcopy(plan[key]) for key in ("capacity", "discovery", "opened_priority",
        "hot_count", "warm_count", "discovery_count", "exit_capacity_contract") if key in plan}
        for engine, plan in report.get("engines", {}).items()}
    for name in ("family_routing", "entry_signal_lab", "economic_exit_lab", "operational_funnel"):
        member = report.get(name, {})
        result[name] = {key: deepcopy(member[key]) for key in ("schema", "as_of", "status", "denominators", "by_currency_channel")
            if key in member} if isinstance(member, dict) else {"schema": "LEGACY_FAMILY_LIST", "as_of": report["as_of"]}
    return result


def logical_digest(connection, header):
    accumulator = hashlib.sha256(); raw = encoded(header).encode()+b"\n"; accumulator.update(raw); size = len(raw)
    for dataset, ordinal, payload in connection.execute("SELECT dataset,ordinal,payload_json FROM projection_rows ORDER BY dataset,ordinal"):
        raw = encoded([dataset, ordinal, decode_row(payload)]).encode()+b"\n"
        accumulator.update(raw); size += len(raw)
    return accumulator.hexdigest(), size


def build_projection(report, original_digests):
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript("""PRAGMA page_size=4096; PRAGMA journal_mode=OFF; PRAGMA user_version=1;
        CREATE TABLE projection_header(id INTEGER PRIMARY KEY CHECK(id=1),payload_json TEXT NOT NULL);
        CREATE TABLE projection_rows(dataset TEXT NOT NULL,ordinal INTEGER NOT NULL,ticker TEXT,family TEXT,currency TEXT,
          market TEXT,settlement TEXT,strategy TEXT,channel TEXT,state TEXT,cohort TEXT,session TEXT,event_at TEXT,
          priority INTEGER,rank_missing INTEGER,rank REAL,excluded INTEGER,payload_json BLOB NOT NULL,
          PRIMARY KEY(dataset,ordinal));
        CREATE INDEX query_scope ON projection_rows(dataset,priority,rank_missing,rank,ticker,ordinal);
        CREATE INDEX query_cohort ON projection_rows(dataset,cohort);
        """)
        count = 0; ordinals = {}
        for dataset, row, native in native_rows(report):
            if not isinstance(row, dict): raise ValueError("SHADOW_PROJECTION_NATIVE_ROW_INVALID")
            ordinal = ordinals.get(dataset, 0); ordinals[dataset] = ordinal+1; count += 1
            if count > MAX_ROWS: raise ValueError("SHADOW_PROJECTION_CARDINALITY_LIMIT")
            connection.execute("INSERT INTO projection_rows VALUES("+",".join("?" for _ in range(18))+")",
                               index_record(dataset, ordinal, row, native))
        header = {"schema": SCHEMA, **{key: deepcopy(report[key]) for key in HEADER_KEYS},
                  **{key: report[key] for key in report["safety"]},
                  "report": report_header(report), "derivation": dict(original_digests), "rows_count": count,
                  "dataset_counts": ordinals, "logical_encoding": "rc6.shadow-ui-projection-lines.v1"}
        connection.execute("INSERT INTO projection_header VALUES(1,?)", (encoded(header),)); connection.commit()
        raw = connection.serialize()
        if len(raw) > MAX_BYTES: raise ValueError("SHADOW_PROJECTION_DURABLE_LIMIT")
        logical_hash, logical_size = logical_digest(connection, header)
        return raw, header, {"payload_digest": logical_hash, "logical_bytes": logical_size, "storage_schema": SCHEMA}
    finally:
        connection.close()


def open_projection(raw):
    if len(raw) > MAX_BYTES: raise ValueError("SHADOW_PROJECTION_DURABLE_LIMIT")
    if (len(raw) < 100 or raw[:16] != b"SQLite format 3\x00"
            or int.from_bytes(raw[16:18], "big") != 4096
            or int.from_bytes(raw[28:32], "big") * 4096 != len(raw)):
        raise ValueError("SHADOW_PROJECTION_SCHEMA_UNSUPPORTED")
    connection = sqlite3.connect(":memory:")
    try:
        connection.deserialize(raw)
        connection.execute("PRAGMA query_only=ON"); connection.execute("PRAGMA trusted_schema=OFF")
        if connection.execute("PRAGMA user_version").fetchone()[0] != 1:
            raise ValueError("SHADOW_PROJECTION_SCHEMA_UNSUPPORTED")
        if {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")} != TABLES:
            raise ValueError("SHADOW_PROJECTION_SCHEMA_UNSUPPORTED")
        rows = connection.execute("SELECT payload_json FROM projection_header WHERE id=1").fetchall()
        if len(rows) != 1: raise ValueError("SHADOW_PROJECTION_HEADER_INVALID")
        from .persistence import _json
        header = _json(rows[0][0])
        if (header.get("schema") != SCHEMA or type(header.get("rows_count")) is not int
                or not 0 <= header["rows_count"] <= MAX_ROWS or not isinstance(header.get("dataset_counts"), dict)
                or any(type(value) is not int or value < 0 for value in header["dataset_counts"].values())
                or sum(header["dataset_counts"].values()) != header["rows_count"]):
            raise ValueError("SHADOW_PROJECTION_HEADER_INVALID")
        counts = dict(connection.execute("SELECT dataset,count(*) FROM projection_rows GROUP BY dataset"))
        if counts != header["dataset_counts"]:
            raise ValueError("SHADOW_PROJECTION_HEADER_INVALID")
        return connection, header
    except BaseException:
        connection.close(); raise


def _where(filters):
    conditions, values = [], []
    for key in ("family", "currency", "market", "settlement", "strategy", "channel", "state", "session", "cohort"):
        if filters.get(key):
            conditions.append(key+"=?"); values.append(str(filters[key]).lower() if key == "cohort" else str(filters[key]).upper())
    if filters.get("q"):
        conditions.append("instr(ticker,?)>0"); values.append(str(filters["q"]).upper())
    if filters.get("identity"):
        raw = filters["identity"]
        ident = json.loads(raw) if isinstance(raw, str) and raw.startswith("[") else raw.split("|") if isinstance(raw, str) else raw
        if not isinstance(ident, (tuple, list)) or len(ident) != 5: raise ValueError("SHADOW_PROJECTION_IDENTITY_INVALID")
        for key, value in zip(("ticker", "family", "market", "currency", "settlement"), ident):
            conditions.append(key+"=?"); values.append(str(value).upper())
    return (" AND "+" AND ".join(conditions) if conditions else ""), values


def decode_row(raw):
    decoded = zlib.decompressobj()
    value = decoded.decompress(raw, MAX_ROW_BYTES+1)
    if len(value) > MAX_ROW_BYTES or not decoded.eof or decoded.unused_data:
        raise ValueError("SHADOW_PROJECTION_ROW_LIMIT")
    from .persistence import _json
    result = _json(value)
    if not isinstance(result, dict): raise ValueError("SHADOW_PROJECTION_NATIVE_ROW_INVALID")
    return result


def query_projection(connection, header, *, filters, offset, limit, deadline):
    def guard():
        if deadline is not None and time.monotonic() >= deadline: raise ValueError("SHADOW_PROJECTION_QUERY_DEADLINE")
    guard()
    connection.set_progress_handler(lambda: int(deadline is not None and time.monotonic() >= deadline), 1000)
    pages = {}
    for kind in KINDS:
        dataset = "planner" if kind in ("opportunities", "discovery", "tradeability", "exclusions", "events") else "families" if kind == "strategies" else kind
        applied = {key: value for key, value in filters.items() if key not in {"cohort", "session", "funnel_offset"}
                   and (key != "state" or dataset == "planner")}
        where, values = _where(applied)
        extra = " AND excluded=1" if kind == "exclusions" else " AND event_at<>''" if kind == "events" else ""
        order = "event_at DESC,ordinal" if kind == "events" else "priority,rank_missing,rank,ticker,ordinal"
        total = connection.execute("SELECT count(*) FROM projection_rows WHERE dataset=?"+extra+where, [dataset, *values]).fetchone()[0]
        rows = [decode_row(row[0]) for row in connection.execute("SELECT payload_json FROM projection_rows WHERE dataset=?"+
            extra+where+" ORDER BY "+order+" LIMIT ? OFFSET ?", [dataset, *values, limit, offset])]
        source = "engines.telemetry" if dataset == "planner" else {"signals": "entry_signal_lab", "experiments": "entry_signal_lab",
            "exits": "economic_exit_lab", "families": "family_routing"}.get(dataset, dataset)
        source_schema = (header["report"].get(source) or {}).get("schema", header["report"].get("schema", ""))
        unavailable = dataset == "event-risk" and not header["dataset_counts"].get(dataset)
        unsupported = [key for key in applied if applied.get(key) and key in
            ({"channel"} if dataset == "planner" else
             {"family", "currency", "market", "settlement", "strategy", "channel", "q", "identity"} if dataset == "capacity" else
             {"currency", "market", "settlement", "strategy", "channel", "q", "identity"} if dataset == "families" else
             {"channel"})]
        if unsupported:
            unavailable = True; rows = []; total = 0
        pages[kind] = {"state": "NO_VERIFICADO" if unavailable else "AVAILABLE",
            "reason": "FILTER_NOT_PUBLISHED_FOR_DATASET:"+",".join(sorted(unsupported)) if unsupported else
                      "EVENT_RISK_NOT_PUBLISHED" if unavailable else "", "rows": rows, "total": total,
            "offset": offset, "limit": limit, "as_of": header["as_of"], "source_path": source, "source_schema": source_schema}
        if unsupported:
            pages[kind]["source_population_total"] = header["dataset_counts"].get(dataset, 0)
        guard()
    scoped = any(filters.get(key) for key in ("family", "strategy", "session", "cohort", "market", "settlement", "identity", "q"))
    where, values = _where({key: value for key, value in filters.items() if key != "state"})
    dataset = "funnel_cohorts" if scoped else "funnel_aggregates"
    total = connection.execute("SELECT count(*) FROM projection_rows WHERE dataset=?"+where, [dataset, *values]).fetchone()[0]
    ordering = "(channel<>'NATIVE_FACTUAL'),currency,cohort,ordinal"
    selected = connection.execute("SELECT payload_json FROM projection_rows WHERE dataset=?"+where+" ORDER BY "+ordering+" LIMIT 1", [dataset, *values]).fetchone()
    selected = decode_row(selected[0]) if selected else None
    groups_offset = int(filters.get("funnel_offset") or 0)
    if not 0 <= groups_offset <= 100000: raise ValueError("SHADOW_PROJECTION_OFFSET_INVALID")
    groups = [decode_row(row[0]) for row in connection.execute("SELECT payload_json FROM projection_rows WHERE dataset=?"+where+
        " ORDER BY "+ordering+" LIMIT ? OFFSET ?", [dataset, *values, 10, groups_offset])]
    guard()
    return pages, {"state": "AVAILABLE" if selected else "NO_VERIFICADO", "reason": "" if selected else "NO_MATCHING_COHORT",
        "counts": {name: selected.get("stages", {}).get(stage) for name, stage in COUNTS.items()} if selected else {},
        "selected": selected, "groups": groups, "total_groups": total, "groups_offset": groups_offset, "groups_limit": 10,
        "label": " · ".join(str(selected.get(key) or "NO_VERIFICADO") for key in
            (("currency", "channel", "symbol", "strategy_id", "session", "hour_art", "registry_sha256") if scoped else ("currency", "channel"))) if selected else "NO_VERIFICADO",
        "as_of": header["as_of"],
        "generation_id": header["generation_id"]}
