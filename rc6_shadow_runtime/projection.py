"""Immutable derived index inside a sealed SHADOW generation.

It contains presentation rows and complete cohort aggregates, never financial
authority. Queries open verified bytes in memory; source databases are untouched.
"""
from copy import deepcopy
import base64
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
MAX_LOGICAL_BYTES = 512 * 1024**2
LEGACY_ENCODING = "rc6.shadow-ui-projection-lines.v1"
MERKLE_ENCODING = "rc6.shadow-ui-projection-merkle.v1"
ROW_CODEC = "ZLIB_CANONICAL_JSON_DICTIONARY_V1"
LEGACY_ROW_CODEC = "ZLIB_CANONICAL_JSON_V1"
MAX_DICTIONARY_BYTES = 32768
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


def index_record(dataset, ordinal, row, native, *, raw=None, dictionary=None, _compressor=None):
    raw = encoded(row) if raw is None else raw
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
    if rank is not None: rank = float(rank)
    # Copy only a never-started encoder within this one immutable derivation.
    # Each row still owns an independent zlib stream with the exact dictionary.
    compressor = _compressor.copy() if _compressor is not None else (
        zlib.compressobj(level=1, zdict=dictionary) if dictionary is not None else zlib.compressobj(level=1))
    compressed = compressor.compress(raw.encode())+compressor.flush()
    return (dataset, ordinal, upper(ticker), upper(family), upper(currency), upper(market), upper(settlement),
        upper(strategy), upper(row.get("channel")), upper(state), cohort_id(row) if dataset.startswith("funnel") else "",
        upper(row.get("session")), event_at, {"HOT": 0, "WARM": 1, "DISCOVERY": 2}.get(upper(state), 3),
        int(rank is None), rank, int(bool(row.get("rejection_reason"))), sqlite3.Binary(compressed))


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
    dictionary = row_dictionary_from_header(header)
    if header["logical_encoding"] == MERKLE_ENCODING:
        accumulators, sizes, counts = {}, {}, {}
        for record in connection.execute("SELECT * FROM projection_rows ORDER BY dataset,ordinal"):
            dataset, ordinal = record[:2]
            raw = encoded([*record[:-1], decode_row(record[-1], dictionary=dictionary)]).encode()+b"\n"
            accumulators.setdefault(dataset, hashlib.sha256()).update(raw)
            sizes[dataset] = sizes.get(dataset, 0)+len(raw); counts[dataset] = counts.get(dataset, 0)+1
            if sum(sizes.values()) > MAX_LOGICAL_BYTES: raise ValueError("SHADOW_PROJECTION_LOGICAL_LIMIT")
        tree = row_tree(accumulators, sizes, counts)
        if tree != header.get("row_tree"): raise ValueError("SHADOW_PROJECTION_DERIVATION_MISMATCH")
        return tree_digest(header, tree)
    if header["logical_encoding"] != LEGACY_ENCODING: raise ValueError("SHADOW_PROJECTION_SCHEMA_UNSUPPORTED")
    accumulator = hashlib.sha256(); raw = encoded(header).encode()+b"\n"; accumulator.update(raw); size = len(raw)
    for record in connection.execute("SELECT * FROM projection_rows ORDER BY dataset,ordinal"):
        dataset, ordinal = record[:2]; value = decode_row(record[-1], dictionary=dictionary)
        native = dataset in {"signals", "experiments", "exits", "funnel_cohorts", "funnel_aggregates"}
        if tuple(record[:-1]) != index_record(dataset, ordinal, value, native)[:-1]:
            raise ValueError("SHADOW_PROJECTION_DERIVATION_MISMATCH")
        raw = encoded([dataset, ordinal, value]).encode()+b"\n"
        accumulator.update(raw); size += len(raw)
    return accumulator.hexdigest(), size


def row_tree(accumulators, sizes, counts):
    return [{"dataset": dataset, "sha256": accumulators[dataset].hexdigest(), "logical_bytes": sizes[dataset],
             "rows": counts[dataset]} for dataset in sorted(accumulators)]


def tree_digest(header, tree):
    raw = encoded(header).encode()+b"\n"
    summary = {"encoding": MERKLE_ENCODING, "header_sha256": hashlib.sha256(raw).hexdigest(), "row_tree": tree}
    size = len(raw)+sum(row["logical_bytes"] for row in tree)
    if size > MAX_LOGICAL_BYTES: raise ValueError("SHADOW_PROJECTION_LOGICAL_LIMIT")
    return hashlib.sha256(encoded(summary).encode()).hexdigest(), size


class PreparedProjection:
    """Derive immutable native rows once within one publisher transaction."""
    def __init__(self, report, *, row_codec=ROW_CODEC):
        if row_codec not in {ROW_CODEC, LEGACY_ROW_CODEC}: raise ValueError("SHADOW_PROJECTION_SCHEMA_UNSUPPORTED")
        self.row_codec = row_codec
        self.dictionary = row_dictionary(report) if row_codec == ROW_CODEC else None
        compressor = zlib.compressobj(level=1, zdict=self.dictionary) if self.dictionary is not None else zlib.compressobj(level=1)
        self.connection = sqlite3.connect(":memory:")
        connection = self.connection
        connection.executescript("""PRAGMA page_size=4096; PRAGMA journal_mode=OFF; PRAGMA user_version=1; PRAGMA temp_store=MEMORY;
        CREATE TABLE projection_header(id INTEGER PRIMARY KEY CHECK(id=1),payload_json TEXT NOT NULL);
        CREATE TABLE projection_rows(dataset TEXT NOT NULL,ordinal INTEGER NOT NULL,ticker TEXT,family TEXT,currency TEXT,
          market TEXT,settlement TEXT,strategy TEXT,channel TEXT,state TEXT,cohort TEXT,session TEXT,event_at TEXT,
          priority INTEGER,rank_missing INTEGER,rank REAL,excluded INTEGER,payload_json BLOB NOT NULL,
          PRIMARY KEY(dataset,ordinal));
        CREATE INDEX query_scope ON projection_rows(dataset,priority,rank_missing,rank,ticker,ordinal);
        CREATE INDEX query_cohort ON projection_rows(dataset,cohort);
        CREATE INDEX query_funnel ON projection_rows(dataset,(channel<>'NATIVE_FACTUAL'),currency,cohort,ordinal);
        CREATE INDEX query_events ON projection_rows(dataset,event_at DESC,ordinal);
        """)
        count = 0; ordinals = {}; accumulators, sizes = {}, {}
        for dataset, row, native in native_rows(report):
            if not isinstance(row, dict): raise ValueError("SHADOW_PROJECTION_NATIVE_ROW_INVALID")
            ordinal = ordinals.get(dataset, 0); ordinals[dataset] = ordinal+1; count += 1
            if count > MAX_ROWS: raise ValueError("SHADOW_PROJECTION_CARDINALITY_LIMIT")
            raw = encoded(row)
            record = index_record(dataset, ordinal, row, native, raw=raw, dictionary=self.dictionary, _compressor=compressor)
            line = encoded(list(record[:-1]))[:-1].encode()+b","+raw.encode()+b"]\n"
            accumulators.setdefault(dataset, hashlib.sha256()).update(line)
            sizes[dataset] = sizes.get(dataset, 0)+len(line)
            if sum(sizes.values()) > MAX_LOGICAL_BYTES: raise ValueError("SHADOW_PROJECTION_LOGICAL_LIMIT")
            connection.execute("INSERT INTO projection_rows VALUES("+",".join("?" for _ in range(18))+")",
                               record)
        self.count, self.ordinals, self.tree = count, ordinals, row_tree(accumulators, sizes, ordinals)

    def build(self, report, original_digests, *, logical_encoding=MERKLE_ENCODING):
        connection = self.connection
        header = {"schema": SCHEMA, **{key: deepcopy(report[key]) for key in HEADER_KEYS},
                  **{key: report[key] for key in report["safety"]},
                  "report": report_header(report), "derivation": dict(original_digests), "rows_count": self.count,
                  "dataset_counts": self.ordinals, "logical_encoding": logical_encoding}
        if logical_encoding == MERKLE_ENCODING: header["row_tree"] = self.tree
        if self.row_codec == ROW_CODEC:
            header["row_codec"] = {"codec": ROW_CODEC, "dictionary": base64.b64encode(self.dictionary).decode("ascii"),
                                   "sha256": hashlib.sha256(self.dictionary).hexdigest()}
        if connection.execute("SELECT count(*) FROM projection_header").fetchone()[0]:
            connection.execute("UPDATE projection_header SET payload_json=? WHERE id=1", (encoded(header),))
        else:
            connection.execute("INSERT INTO projection_header VALUES(1,?)", (encoded(header),))
        connection.commit()
        # Eliminate freed overflow pages from the in-memory preparation; only
        # compact actual bytes are reserved and published.
        connection.execute("VACUUM")
        raw = connection.serialize()
        if len(raw) > MAX_BYTES: raise ValueError("SHADOW_PROJECTION_DURABLE_LIMIT")
        logical_hash, logical_size = tree_digest(header, self.tree) if logical_encoding == MERKLE_ENCODING else logical_digest(connection, header)
        return raw, header, {"payload_digest": logical_hash, "logical_bytes": logical_size, "storage_schema": SCHEMA,
                             "logical_encoding": logical_encoding, "row_codec": self.row_codec}

    def close(self):
        self.connection.close()

    def __del__(self):
        if hasattr(self, "connection"): self.connection.close()


def build_projection(report, original_digests, *, logical_encoding=MERKLE_ENCODING, row_codec=ROW_CODEC):
    builder = PreparedProjection(report, row_codec=row_codec)
    try: return builder.build(report, original_digests, logical_encoding=logical_encoding)
    finally: builder.close()


def open_projection(raw):
    if len(raw) > MAX_BYTES: raise ValueError("SHADOW_PROJECTION_DURABLE_LIMIT")
    if (len(raw) < 100 or raw[:16] != b"SQLite format 3\x00"
            or int.from_bytes(raw[16:18], "big") != 4096
            or int.from_bytes(raw[28:32], "big") * 4096 != len(raw)):
        raise ValueError("SHADOW_PROJECTION_SCHEMA_UNSUPPORTED")
    connection = sqlite3.connect(":memory:")
    try:
        connection.deserialize(raw)
        connection.execute("PRAGMA temp_store=MEMORY")
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
        row_dictionary_from_header(header)
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


def row_dictionary(report):
    samples = []
    for _, plan in sorted(report.get("engines", {}).items()): samples.extend(plan.get("telemetry", [])[:4])
    samples.extend(report.get("operational_funnel", {}).get("cohorts", [])[:4])
    samples.extend(report.get("operational_funnel", {}).get("by_currency_channel", [])[:2])
    raw = (encoded(list(PLAN_FIELDS))+"\n"+"\n".join(encoded(row) for row in samples)).encode()
    return raw[-MAX_DICTIONARY_BYTES:]


def row_dictionary_from_header(header):
    record = header.get("row_codec")
    if record is None: return None
    if (not isinstance(record, dict) or set(record) != {"codec", "dictionary", "sha256"} or record["codec"] != ROW_CODEC
            or not isinstance(record["dictionary"], str) or len(record["dictionary"]) > 4*((MAX_DICTIONARY_BYTES+2)//3)
            or not isinstance(record["sha256"], str)):
        raise ValueError("SHADOW_PROJECTION_SCHEMA_UNSUPPORTED")
    raw = base64.b64decode(record["dictionary"], validate=True)
    if not 0 < len(raw) <= MAX_DICTIONARY_BYTES or hashlib.sha256(raw).hexdigest() != record["sha256"]:
        raise ValueError("SHADOW_PROJECTION_DICTIONARY_INVALID")
    return raw


def decode_row(raw, *, dictionary=None):
    decoded = zlib.decompressobj(zdict=dictionary) if dictionary is not None else zlib.decompressobj()
    value = decoded.decompress(raw, MAX_ROW_BYTES+1)
    if len(value) > MAX_ROW_BYTES or not decoded.eof or decoded.unused_data:
        raise ValueError("SHADOW_PROJECTION_ROW_LIMIT")
    from .persistence import _json
    result = _json(value)
    if not isinstance(result, dict): raise ValueError("SHADOW_PROJECTION_NATIVE_ROW_INVALID")
    return result


def query_projection(connection, header, *, filters, offset, limit, deadline):
    dictionary = row_dictionary_from_header(header)
    def guard():
        if deadline is not None and time.monotonic() >= deadline: raise ValueError("SHADOW_PROJECTION_QUERY_DEADLINE")
    guard()
    connection.set_progress_handler(lambda: int(deadline is not None and time.monotonic() >= deadline), 1000)
    pages, queries = {}, {}
    for kind in KINDS:
        dataset = "planner" if kind in ("opportunities", "discovery", "tradeability", "exclusions", "events") else "families" if kind == "strategies" else kind
        applied = {key: value for key, value in filters.items() if key not in {"cohort", "session", "funnel_offset"}
                   and (key != "state" or dataset == "planner")}
        where, values = _where(applied)
        extra = " AND excluded=1" if kind == "exclusions" else " AND event_at<>''" if kind == "events" else ""
        order = "event_at DESC,ordinal" if kind == "events" else "priority,rank_missing,rank,ticker,ordinal"
        key = (dataset, extra, where, tuple(values), order, limit, offset)
        if key not in queries:
            total = connection.execute("SELECT count(*) FROM projection_rows WHERE dataset=?"+extra+where, [dataset, *values]).fetchone()[0]
            rows = [decode_row(row[0], dictionary=dictionary) for row in connection.execute("SELECT payload_json FROM projection_rows WHERE dataset=?"+
                extra+where+" ORDER BY "+order+" LIMIT ? OFFSET ?", [dataset, *values, limit, offset])]
            queries[key] = total, rows
        total, rows = queries[key]
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
    selected = decode_row(selected[0], dictionary=dictionary) if selected else None
    groups_offset = int(filters.get("funnel_offset") or 0)
    if not 0 <= groups_offset <= 100000: raise ValueError("SHADOW_PROJECTION_OFFSET_INVALID")
    groups = [decode_row(row[0], dictionary=dictionary) for row in connection.execute("SELECT payload_json FROM projection_rows WHERE dataset=?"+where+
        " ORDER BY "+ordering+" LIMIT ? OFFSET ?", [dataset, *values, 10, groups_offset])]
    guard()
    return pages, {"state": "AVAILABLE" if selected else "NO_VERIFICADO", "reason": "" if selected else "NO_MATCHING_COHORT",
        "counts": {name: selected.get("stages", {}).get(stage) for name, stage in COUNTS.items()} if selected else {},
        "selected": selected, "groups": groups, "total_groups": total, "groups_offset": groups_offset, "groups_limit": 10,
        "label": " · ".join(str(selected.get(key) or "NO_VERIFICADO") for key in
            (("currency", "channel", "symbol", "strategy_id", "session", "hour_art", "registry_sha256") if scoped else ("currency", "channel"))) if selected else "NO_VERIFICADO",
        "as_of": header["as_of"],
        "generation_id": header["generation_id"]}
