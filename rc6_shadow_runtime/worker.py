"""Canonical periodic SHADOW worker over existing observations only.

No market-data client, trading writer, broker, fill, network transport or manual
bundle. A missing preopen/OPEN capacity remains an explicit closed gate.
"""
from datetime import datetime, timedelta, timezone
from contextlib import ExitStack
import gc
import json
import logging
import os
import resource
from pathlib import Path
import sqlite3
import time
import traceback

import ak_byma_calendar as calendar
from co_market_sessions_hf6 import (TZ, BYMA_PAPER_SPOT_OPEN,
    BYMA_PAPER_SPOT_CLOSE, byma_paper_spot_phase)
from rc6_dynamic_universe.common import digest, stamp
from rc6_dynamic_universe.live import run_shadow
from rc6_dynamic_universe.runtime import read_runtime
from rc6_dynamic_universe.sources import source_observations
from .persistence import EvidenceFiles, EvidencePayloadLimit, canonical_ascii_chunks
from .audit_spool import AuditSpool
from .checkpoint_parts import CheckpointParts
from .timing import POLL_SECONDS

LOG = logging.getLogger("dynamic_shadow")
VERSION = "WS-CLOSE-PROGRAMMING-05-RUNTIME-v2"
SOURCE_FILES = {"BYMA": "rc6_public_sources_latest.json",
                "IOL_MCP": "iol_shadow_latest.json",
                "IOL_FAMILY_REFERENCE": "iol_family_reference_latest.json"}


def release_idle_heap():
    """Return already-free glibc heap pages after the full report is released.

    This changes neither a live object nor a cgroup/host policy. The optional
    allocator capability and actual RSS delta are recorded, never assumed.
    """
    def rss():
        try:
            return next(int(line.split()[1]) for line in
                Path('/proc/self/status').read_text().splitlines() if line.startswith('VmRSS:'))
        except (OSError, ValueError, StopIteration):
            return None
    before = rss()
    started = time.monotonic()
    gc.collect()
    available = False
    try:
        import ctypes
        libc = ctypes.CDLL(None)
        trim = libc.malloc_trim
        trim.argtypes = [ctypes.c_size_t]
        trim.restype = ctypes.c_int
        trim(0)
        available = True
    except (AttributeError, OSError):
        pass
    return {'allocator_trim_available': available, 'rss_before_kib': before,
            'rss_after_kib': rss(), 'elapsed_seconds': round(time.monotonic()-started, 6)}


def session_context(as_of):
    """Existing audited BYMA calendar, never a synthetic weekday calendar."""
    at = stamp(as_of)
    local = at.astimezone(TZ)
    day = local.date()
    if local.time().replace(tzinfo=None) >= BYMA_PAPER_SPOT_CLOSE:
        day += timedelta(days=1)
    for _ in range(370):
        if calendar.es_dia_habil_operativo(day):
            break
        day += timedelta(days=1)
    else:
        raise ValueError("AUDITED_CALENDAR_UNAVAILABLE")
    previous = day - timedelta(days=1)
    for _ in range(370):
        if calendar.es_dia_habil_operativo(previous):
            break
        previous -= timedelta(days=1)
    else:
        raise ValueError("AUDITED_PREVIOUS_SESSION_UNAVAILABLE")
    return {"session": day.isoformat(),
        "opening": datetime.combine(day, BYMA_PAPER_SPOT_OPEN, TZ).astimezone(timezone.utc),
        "cutoff": datetime.combine(previous, BYMA_PAPER_SPOT_CLOSE, TZ).astimezone(timezone.utc),
        "phase": byma_paper_spot_phase(at)}


def _json_input(path, *, limit=8 * 1024**2):
    path = Path(path)
    if not path.exists():
        return None
    if not path.is_file() or path.stat().st_size > limit:
        raise ValueError("SHADOW_INPUT_SIZE_LIMIT")
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("SHADOW_INPUT_SIZE_LIMIT")
    return json.loads(raw)


def _fits_hot_report(value, limit_bytes=1024**2):
    size = 0
    encoder = json.JSONEncoder(sort_keys=True, separators=(",", ":"),
                               default=str, allow_nan=False)
    for fragment in encoder.iterencode(value):
        size += len(fragment.encode())
        if size > limit_bytes:
            return False
    return True


def _runtime_sql_status(diagnostics):
    """Small allowlisted SQL counters for the durable PAPER status only."""
    if not isinstance(diagnostics, dict):
        return None
    stages = diagnostics.get("stages", [])
    allowed = {"CATALOG_FULL", "OPEN_POSITIONS", "SCHEMA_TABLES",
               "INTRADAY_CONTRACTS", "INTRADAY_POINTS", "MARKET_SNAPSHOTS"}
    if not isinstance(stages, list):
        return None
    rows = [{key: stage[key] for key in ("stage", "elapsed_ms", "rows",
             "remaining_ms", "deadline_elapsed", "interrupted_by_deadline", "sqlite_errorname")
             if key in stage}
            for stage in stages[:6] if isinstance(stage, dict)
            and stage.get("stage") in allowed]
    plans = diagnostics.get("plans", {})
    return {"budget_ms": diagnostics.get("budget_ms"),
            "sql_elapsed_ms": diagnostics.get("sql_elapsed_ms"),
            "sql_remaining_ms": diagnostics.get("sql_remaining_ms"),
            "transform_elapsed_ms": diagnostics.get("transform_elapsed_ms"),
            "transform_expired_stage": diagnostics.get("transform_expired_stage"),
            "stages": rows,
            "plans": {name: [item[:120] for item in values[:3] if isinstance(item, str)]
                      for name, values in plans.items() if name in allowed
                      and isinstance(values, list)}}


def _durable_family_routing(report):
    if _fits_hot_report(report):
        return report
    authority = report.get("source_authority") or {}
    return {
        "schema": report.get("schema"), "as_of": report.get("as_of"),
        "status": report.get("status"), "catalog_count": report.get("catalog_count"),
        "catalog_preserved": report.get("catalog_preserved"),
        "instruments_scope": "FULL_COMPUTED_AUDIT",
        "instruments_total": len(report.get("instruments", [])),
        "families": [{"family": row.get("family"), "catalog_count": row.get("catalog_count"),
                      "status": row.get("status"), "entry_authority": row.get("entry_authority")}
                     for row in report.get("families", [])],
        "source_authority": {key: authority.get(key) for key in (
            "policy_version", "identity_primary", "identity_complement_policy",
            "catalog_identity_mutated", "iol_runtime_contract",
            "byma_runtime_contract", "byma_api")},
    }


def durable_exit_observations(lab):
    rows=[]
    for observation in reversed(lab.get('entries',[])[-100:]):
        entry=observation.get('entry') or {}
        label=observation.get('forward_label') or {}
        for variant in observation.get('variants',[])[:20]:
            result=variant.get('result') or {}
            measured=label.get('status')=='MEDIDO' and result.get('state')=='CLOSED'
            rows.append({**{k:entry.get(k) for k in ('paper_id','symbol','currency','market','settlement')},
                'family':entry.get('asset_class'),'opened_at':entry.get('opened_at'),
                'policy':variant.get('policy'),'state':'EVALUABLE' if measured else 'INSUFFICIENT_EVIDENCE',
                'modeled_net':result.get('net') if measured else None,
                'registered_at':observation.get('registered_at'),
                'entry_evidence_sha256':observation.get('entry_evidence_sha256'),
                'path_sha256':observation.get('path_sha256'),
                'coverage':label.get('status'),'path_observations':observation.get('path_observations'),
                'comparison_basis':'SAME_FACTUAL_ENTRY_PAPER_SENSITIVITY',
                'entry_authority':False,'economic_edge_validated':False})
            if len(rows)>=100: break
        if len(rows)>=100: break
    return {'status':lab.get('status'),'as_of':lab.get('as_of'),'rows':rows,
            'scope':'LATEST_100_POLICY_OBSERVATIONS; no portfolio feedback or continuous market path claim',
            'economic_edge_validated':False,'decision_effect':'NONE'}


def durable_report_view(report, *, catalog_ready_count, engine_views=None, family_view=None):
    """Persist a bounded selection contract; preserve full in-memory evaluation.

    Immutable preopen evidence and the causal checkpoint remain separate. The
    full computed report is stored and fingerprinted separately; large derived
    inventories are not repeated in the hot-path file.
    """
    base = ("schema", "mode", "as_of", "session", "phase", "status",
            "source_database_effect", "provider_requests", "provider_additional_budget",
            "production_limits_modified", "factual_execution", "real_orders_sent",
            "real_routes", "ppi_watch", "source_identity", "runtime_configuration",
            "capacity_policy", "checkpoint_reused", "preopen_immutable",
            "preopen_file", "preopen_quality", "capacity_open_status", "observation_execution",
            "active_paper_scanner_authority", "catalog_ready_count")
    view = {key: report[key] for key in base if key in report}
    view["catalog_ready_count"] = catalog_ready_count
    view["engines"] = {}
    for engine, plan in (() if engine_views is not None else report["engines"].items()):
        needed = {tuple(key) for key in plan["selected"]}
        needed.update(tuple(key) for key in plan.get("opened_priority", []))
        rows = [row for row in plan["telemetry"]
                if tuple(row["identity"]) in needed]
        if {tuple(row["identity"]) for row in rows} != needed:
            raise ValueError("SHADOW_SELECTED_TELEMETRY_INCOMPLETE")
        view["engines"][engine] = {
            "capacity": plan["capacity"],
            "preopen_digest": plan["preopen_digest"],
            "selected": plan["selected"],
            "opened_priority": plan.get("opened_priority", []),
            "telemetry": rows,
            "telemetry_total": len(plan["telemetry"]),
            "discovery": plan.get("discovery", {}),
        }
    if engine_views is not None:
        view['engines'] = engine_views
    if family_view is not None:
        view['family_routing'] = family_view
    elif "family_routing" in report:
        view["family_routing"] = _durable_family_routing(report["family_routing"])
    # The dashboard consumes this bounded aggregate, never the full audit.
    # Previously only its status survived, so every funnel count was absent.
    funnel = report.get("operational_funnel", {})
    view["operational_funnel"] = {key: funnel[key] for key in (
        "status", "as_of", "by_currency_channel", "counts") if key in funnel}
    view['economic_exit_lab']=durable_exit_observations(report.get('economic_exit_lab',{}))
    view["section_status"] = {key: report[key].get("status")
        for key in ("economic_exit_lab", "entry_signal_lab", "operational_funnel")
        if isinstance(report.get(key), dict)}
    return view


def _share_equal_frozen_rows(frozen):
    """Equal immutable rankings can share storage within this private tick.

    Engines retain independent envelopes/capacity fingerprints. Compare full
    values before sharing; no row, identity or provenance field is discarded.
    """
    rows = None
    for engine in frozen.get("frozen", {}).values():
        payload = engine.get("payload", {})
        candidate = payload.get("rows")
        if isinstance(candidate, list):
            if rows is not None and candidate == rows:
                payload["rows"] = rows
            else:
                rows = candidate
    return frozen


def durable_checkpoint_view(checkpoint):
    """Keep only causal state required by the next SHADOW tick."""
    view = {key: value for key, value in checkpoint.items()
            if key not in ("catalog_ready", "source_errors", "native_ppi_errors")}
    if "engines" in view:
        view["engines"] = {engine: {
            key: plan[key] for key in ("configuration_fingerprint", "planned_at", "instruments")
            if key in plan}
            for engine, plan in checkpoint["engines"].items()}
    return view


def shared_preopen_row_lists(report):
    """Only equal immutable preopen lists shared by engine envelopes are reusable."""
    if report.get("preopen_immutable") is not True:
        return ()
    groups = {}
    for engine in report.get("frozen", {}).values():
        if not isinstance(engine, dict):
            continue
        payload = engine.get("payload", {})
        rows = payload.get("rows") if isinstance(payload, dict) else None
        if type(rows) is list and len(rows) > 256:
            entry = groups.setdefault(id(rows), [rows, 0])
            entry[1] += 1
    return tuple(rows for rows, count in groups.values() if count > 1)


FUNNEL_TELEMETRY_FIELDS = frozenset(('identity', 'strategy', 'pipeline', 'state', 'source',
    'rejection_reason', 'promotion_reasons', 'promoted_at', 'discovery_age',
    'discovery_age_lower_bound', 'revisit_seconds', 'achieved_revisit_seconds',
    'time_to_warmup_seconds', 'event_to_promotion_seconds', 'warmup_progress'))
FAMILY_FIELDS = frozenset(('identity', 'strategy', 'engine', 'reason_codes', 'catalog_ready', 'readiness'))
CATALOG_FIELDS = frozenset(('ticker', 'instrument_type', 'market', 'currency', 'settlement', 'status', 'capability'))


def _project(row, fields):
    return {key: value for key, value in row.items() if key in fields}


def _funnel_engine_views(engines):
    return {engine: {**{key: value for key, value in plan.items() if key != 'telemetry'},
                     'telemetry': [_project(row, FUNNEL_TELEMETRY_FIELDS) for row in plan['telemetry']]}
            for engine, plan in engines.items()}


def _stage_preopen_rows(report, spool):
    nodes = {}
    wrappers = [engine.get('payload', {}) for engine in report.get('frozen', {}).values()]
    wrappers.append(report.get('tradeability', {}))
    for wrapper in wrappers:
        rows = wrapper.get('rows')
        if type(rows) is not list:
            continue
        key = id(rows)
        if key not in nodes:
            nodes[key] = spool.stage('preopen_rows', canonical_ascii_chunks(rows))
        wrapper['rows'] = nodes[key]


class ShadowRuntime:
    def __init__(self, database, *, evidence_root=None, history_database=None,
                 source_roots=None, capacity_path=None, policies=None,
                 maximum_bytes=128 * 1024**2, row_limit=20000, spool_audit=False):
        if type(spool_audit) is not bool:
            raise ValueError('SHADOW_SPOOL_MODE_INVALID')
        self.spool_audit = spool_audit
        self.database = Path(database).resolve(strict=True)
        self.history_database = Path(history_database).resolve() if history_database else None
        self.root = Path(evidence_root or (str(self.database) + ".shadow"))
        data_root = Path(os.getenv("DATA_DIR", str(self.database.parent.parent)))
        self.source_roots = list(dict.fromkeys(Path(p).resolve() for p in (
            source_roots if source_roots is not None else
            [data_root / "market", self.database.parent.parent / "market",
             Path(os.getenv("POROTA_IOL_SHADOW_ROOT", str(data_root / "market")))])))
        self.capacity_path = Path(capacity_path) if capacity_path else None
        self.policies = policies or {}
        from rc6_dynamic_universe.promotion import capacity_controller_from_environment
        self.capacity_controller = capacity_controller_from_environment(self.database)
        self.maximum_bytes, self.row_limit = maximum_bytes, row_limit
        self.source_paths = {source: [root / name for root in self.source_roots]
                             for source, name in SOURCE_FILES.items()}
        configured_iol = os.getenv("POROTA_IOL_SHADOW_CACHE_PATH", "").strip()
        if configured_iol:
            self.source_paths["IOL_MCP"].insert(0, Path(configured_iol).resolve())
        self.source_inputs = [p for paths in self.source_paths.values() for p in paths]
        self.source_inputs += [root / "rc6_ppi_capacity_latest.json" for root in self.source_roots]
        capacity_protected = self.capacity_controller.protected_paths
        configured_shadow = self.capacity_controller.environ.get("POROTA_CAPACITY_SHADOW_PATH")
        owned_latest = (self.root / "latest.json.gz").absolute()
        if configured_shadow and Path(configured_shadow).absolute() == owned_latest:
            # The factual selectors read this worker's own output. Only that
            # exact output role is writable; a policy/report/approval sharing
            # the same path remains protected, as do aliases handled by files.
            other_inputs = {Path(v).absolute() for k, v in self.capacity_controller.environ.items()
                if k.startswith("POROTA_CAPACITY_") and k.endswith("_PATH")
                and k != "POROTA_CAPACITY_SHADOW_PATH" and v}
            capacity_protected = [p for p in capacity_protected
                if p.absolute() != owned_latest or p.absolute() in other_inputs]
        protected = ([self.database, self.history_database, self.capacity_path]
                     + self.source_inputs + capacity_protected)
        self.files = EvidenceFiles(self.root, protected=protected, maximum_bytes=maximum_bytes)
        self.configuration = digest({"version": VERSION, "row_limit": row_limit,
            "provider_additional_requests": 0, "tick_seconds": 30,
            "policies": self.policies, "history": str(self.history_database),
            "sources": {k: list(map(str, v)) for k, v in self.source_paths.items()},
            "capacity": str(self.capacity_path)})

    def _metadata(self, at, since):
        c = sqlite3.connect(self.database.as_uri() + "?mode=ro", uri=True, timeout=.005)
        c.row_factory = sqlite3.Row
        try:
            c.execute("PRAGMA query_only=ON")
            end = time.monotonic() + .75
            c.set_progress_handler(lambda: int(time.monotonic() > end), 1000)
            tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            dataset = None
            if "paper_workspace" in tables:
                row = c.execute("SELECT dataset_id FROM paper_workspace WHERE id=1").fetchone()
                dataset = row[0] if row else None
            source_id = digest({"dataset": dataset, "path": str(self.database),
                "inode": self.database.stat().st_ino, "device": self.database.stat().st_dev})
            failures = []
            if "paper_events" in tables:
                for r in c.execute("""SELECT event_at,event_type,detail FROM paper_events
                        WHERE event_type IN ('INTRADAY_SCALPING_UNSUPPORTED','INTRADAY_SCALPING_ERROR')
                        AND julianday(event_at)>=julianday(?) AND julianday(event_at)<=julianday(?)
                        ORDER BY id DESC LIMIT 200""", (since, at.isoformat())):
                    # New collector diagnostics retain exact five-part identity.
                    marker = ";shadow_identity="
                    if marker not in r["detail"]:
                        failures.append({"received_at": r["event_at"], "reason": "LEGACY_ERROR_IDENTITY_UNVERIFIED"})
                        continue
                    detail, encoded = r["detail"].split(marker, 1)
                    key = json.loads(encoded)
                    code = detail.split(":", 1)[-1].strip()
                    if len(key) != 5 or not all(isinstance(s, str) and s for s in key):
                        raise ValueError("PPI_ERROR_IDENTITY_INVALID")
                    failures.append({"identity": key, "source_at": r["event_at"],
                        "received_at": r["event_at"], "source": "PPI_INTRADAY_DIAGNOSTIC",
                        "endpoint": "intraday", "useful": False, "native_reason": code,
                        "source_clock_basis": "native error occurrence; no market quote", "fields": {}})
            return source_id, failures
        finally:
            c.close()

    def _sources(self):
        reports, errors = {}, []
        for source, paths in self.source_paths.items():
            for path in paths:
                try:
                    value = _json_input(path)
                    if value is not None:
                        reports[source] = value
                        break
                except (OSError, ValueError, TypeError):
                    errors.append({"source": source, "reason": "SOURCE_UNAVAILABLE_OR_INVALID"})
        return reports, errors

    def _capacity(self, *, as_of=None, capacity_policy=None):
        if (capacity_policy or {}).get("status") == "APPROVED_DYNAMIC":
            return self.capacity_controller.validated_report(
                as_of, expected_fingerprint=capacity_policy["fingerprint"])
        paths = [self.capacity_path] if self.capacity_path else [
            p / "rc6_ppi_capacity_latest.json" for p in self.source_roots]
        for path in paths:
            value = _json_input(path)
            if value is not None:
                return value
        return {}

    def _radar(self, observations, old, at, opening):
        """Only observed last-trade timestamps count, never total venue volume.

        A repeated source clock cannot create a new business. Minute points
        retained prospectively support same-window spread/price/new-trade events.
        """
        cutoff = at - timedelta(minutes=10)
        points = {digest((o["identity"], o.get("source"), o["source_at"])): o for o in old.get("points", [])
                  if cutoff <= stamp(o["source_at"]) <= at}
        counters = dict(old.get("counters", {}))
        last = dict(old.get("last_trade", {}))
        for o in sorted(observations, key=lambda r: (r["source_at"], r["received_at"])):
            if not o.get("useful") or o.get("endpoint") not in {"current", "radar"}:
                continue
            when = stamp(o["source_at"])
            if when < max(opening, cutoff) or (at - when).total_seconds() > 120:
                continue
            ident = digest(o["identity"])
            previous = stamp(last[ident]) if ident in last else None
            if o.get("is_trade") and (previous is None or when > previous):
                counters[ident] = counters.get(ident, 0) + 1
                last[ident] = when.isoformat()
            # Preserve the originally observed count on repeat/revision.
            key = digest((o["identity"], o.get("source"), o["source_at"]))
            fields = dict(o.get("fields", {}))
            if o.get("is_trade"):
                fields["trades"] = points.get(key, {}).get("fields", {}).get("trades", counters.get(ident))
            points[key] = {**o, "endpoint": "radar", "fields": fields,
                "trade_count_basis": ("WORKER_OBSERVED_DISTINCT_NATIVE_LAST_TRADE_TIMESTAMPS"
                    if o.get("is_trade") else "NATIVE_SOURCE_FIELDS_ONLY; no inferred trade count")}
        if len(points) > self.row_limit:
            raise ValueError("SHADOW_RADAR_CAPACITY_REACHED")
        return {"points": list(points.values()), "counters": counters, "last_trade": last}

    def tick(self, as_of):
        tick_started = time.monotonic()
        measured_at = tick_started
        tick_cpu_started = time.process_time()
        measured_cpu_at = tick_cpu_started
        phase_seconds, phase_cpu_seconds, phase_peak_rss = {}, {}, {}
        storage_profile = {}
        allocator_phases = {}
        def stored(name, operation):
            wall, cpu = time.monotonic(), time.process_time()
            before = resource.getrusage(resource.RUSAGE_SELF)
            value = operation()
            after = resource.getrusage(resource.RUSAGE_SELF)
            storage_profile[name] = {'wall_seconds':round(time.monotonic()-wall, 6),
                'cpu_seconds':round(time.process_time()-cpu, 6),
                'major_faults':after.ru_majflt-before.ru_majflt,
                'block_outputs':after.ru_oublock-before.ru_oublock,
                'peak_rss_kib':after.ru_maxrss}
            return value
        def measured(name):
            nonlocal measured_at, measured_cpu_at
            ended = time.monotonic()
            cpu_ended = time.process_time()
            phase_seconds[name] = round(max(0, ended-measured_at), 3)
            phase_cpu_seconds[name] = round(max(0, cpu_ended-measured_cpu_at), 3)
            phase_peak_rss[name] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            measured_at, measured_cpu_at = ended, cpu_ended
        at = stamp(as_of)
        with self.files as files, ExitStack() as stack:
            spool = stack.enter_context(AuditSpool(files)) if self.spool_audit else None
            parts = stack.enter_context(CheckpointParts(files, canonical_ascii_chunks)) if spool else None
            engine_views = family_view = None
            capacity_policy = self.capacity_controller.state(at)
            configuration = digest({"runtime": self.configuration,
                "capacity_policy": capacity_policy["fingerprint"],
                "entry_signal_lab": "rc6.runtime-entry-signals.v1",
                "operational_funnel": "rc6.prospective-operational-funnel.v1"})
            previous = dict(parts.head) if parts else (files.read("checkpoint.json.gz") or {})
            if parts:
                allocator_phases['checkpoint_verified'] = release_idle_heap()
            if previous and stamp(previous["as_of"]) > at:
                raise ValueError("SHADOW_CHECKPOINT_FROM_FUTURE")
            self.last_sqlite_diagnostics = {}
            inputs = read_runtime(self.database, as_of=at, row_limit=self.row_limit,
                                  query_budget_seconds=2.0,
                                  diagnostics=self.last_sqlite_diagnostics)
            source_id, failures = self._metadata(at, previous.get("as_of", at.isoformat()))
            reuse = (previous.get("source_identity") == source_id and
                     previous.get("runtime_configuration") == configuration)
            if not reuse:
                previous = {}
            elif parts and previous.get('session') == session_context(at)['session']:
                previous['engines'] = parts.load('engines')
            # Source identity and runtime configuration are part of the durable
            # checkpoint, independent of the planner's capacity fingerprint.
            started = previous.get("started_at", at.isoformat())
            context = session_context(at)
            sources, source_errors = self._sources()
            capacity = self._capacity(as_of=at, capacity_policy=capacity_policy)
            measured("state_and_inputs")
            freeze_name = "preopen-" + context["session"] + ".json.gz"
            frozen = files.read(freeze_name, compact_preopen=True)
            if frozen and frozen["source_identity"] != source_id:
                raise ValueError("SHADOW_PREOPEN_SOURCE_IDENTITY_MISMATCH")
            if frozen is None and at < context["opening"]:
                from .preopen import build_preopen_inputs
                pre = build_preopen_inputs(self.database, self.history_database, as_of=at,
                    session_open=context["opening"], cutoff=context["cutoff"])
                bundle = {**inputs, **pre, "as_of": at.isoformat(),
                    "session_open": context["opening"].isoformat(),
                    "preopen_cutoff": context["cutoff"].isoformat(), "frozen_at": at.isoformat(),
                    "capacity_report": capacity, "capacity_policy": capacity_policy,
                    "policies": self.policies, "observations": []}
                pre_report = run_shadow(bundle, frozen_only=True, owned_frozen=True)
                frozen = {"schema": VERSION, "source_identity": source_id,
                    "frozen": pre_report["frozen"], "quality": pre["quality"],
                    "intraday_history": pre.get("intraday_history", [])}
                files.write(freeze_name, frozen, immutable=True)
                del pre_report
            if frozen is not None:
                _share_equal_frozen_rows(frozen)
            measured("preopen")
            base = {"schema": VERSION, "mode": "SHADOW", "as_of": at.isoformat(),
                "session": context["session"], "phase": context["phase"],
                "source_database_effect": "READ_ONLY", "provider_requests": 0,
                "provider_additional_budget": {"current": 0, "book": 0, "intraday": 0},
                "production_limits_modified": capacity_policy["production_limits_modified"],
                "factual_execution": "NOT_CALLED",
                "real_orders_sent": 0, "real_routes": "NOT_CALLED", "ppi_watch": "UNTOUCHED",
                "source_errors": source_errors, "native_ppi_errors": failures,
                "checkpoint_reused": reuse, "runtime_configuration": configuration,
                "capacity_policy": capacity_policy,
                "catalog_ready": inputs["catalog"],
                "source_identity": source_id}
            checkpoint = {**base, "started_at": started}
            ready_count = len(inputs['catalog'])
            preopen_digests = {key: value['digest']
                for key, value in (frozen or {}).get('frozen', {}).items()}
            if frozen is None:
                report = {**base, "status": "PREOPEN_SNAPSHOT_REQUIRED_DURING_SESSION",
                          "engines": {}, "catalog_ready_count": len(inputs["catalog"])}
            else:
                # Retain only causal radar points; re-reading a historical DB
                # row cannot create an event before this worker first existed.
                observations = [o for o in inputs["observations"]
                    if stamp(o["source_at"]) >= stamp(started)
                    and stamp(o["received_at"]) > stamp(previous.get("as_of", started))]
                observations += [o for o in failures if o.get("identity")]
                compatible = previous if previous.get("session") == context["session"] else {}
                source_reports = [source_observations(value, source=name, as_of=at)
                    for name, value in sources.items() if name != "IOL_FAMILY_REFERENCE"]
                external = [{**o, "endpoint": "radar"} for r in source_reports for o in r["observations"]
                    if o.get("identity") and o.get("source_at") and o.get("received_at")
                    and stamp(started) <= stamp(o["source_at"]) <= stamp(o["received_at"]) <= at
                    and stamp(o["received_at"]) > stamp(previous.get("as_of", started))]
                radar = self._radar(observations + external, compatible.get("radar", {}), at, context["opening"])
                checkpoint["radar"] = radar
                observations += radar["points"]
                bundle = {**inputs, "as_of": at.isoformat(),
                    "session_open": context["opening"].isoformat(),
                    "preopen_cutoff": context["cutoff"].isoformat(),
                    "frozen_at": frozen["frozen"]["SCALPING"]["payload"]["frozen_at"],
                    "sessions": frozen["frozen"]["SCALPING"]["payload"]["sessions"],
                    "rankings": frozen["frozen"]["SCALPING"]["payload"],
                    "frozen": frozen["frozen"], "intraday_history": frozen["intraday_history"],
                    "capacity_report": capacity, "capacity_policy": capacity_policy,
                    "policies": self.policies,
                    "observations": observations,
                    "source_observation_reports": source_reports,
                    "observation_not_before": started,
                    "observation_received_after": previous.get("as_of", started),
                    "sources": {k: v for k, v in sources.items() if k != "IOL_FAMILY_REFERENCE"}}
                result = run_shadow(bundle, previous=compatible)
                from .stages import enrich_pipeline
                result = enrich_pipeline(self.database, result, as_of=at, compact_missing=True)
                report = {**result, **base, "status": "SHADOW_OBSERVING",
                    "preopen_quality": frozen["quality"],
                    "preopen_immutable": True, "preopen_file": freeze_name,
                    "capacity_open_status": "NO_VERIFICADO" if not capacity else capacity.get("status"),
                    "observation_execution": "LOCAL_REUSE_ONLY; native PPI Intraday preserved",
                    "active_paper_scanner_authority": "FACTUAL_SIGNAL_AND_ADMISSION_UNCHANGED; sampling_policy="
                        + capacity_policy["status"] + "; shadow fills NOT_CALLED"}
                checkpoint["engines"] = result["engines"]
                # The planner has completed all reads and shared allocation
                # against its old state. New plans own their surviving values.
                # Do not retain the decoded old instruments beside the full
                # family/funnel audit; lab/entry/funnel keep their own roles.
                previous.pop("engines", None)
                compatible = bundle = observations = source_reports = None
                allocator_phases['planner_completed'] = release_idle_heap()
            measured("planner_pipeline")
            hot = report
            if spool:
                engine_views = durable_report_view(report, catalog_ready_count=ready_count)['engines']
                hot = {**base, 'status': report['status'],
                       'engines': _funnel_engine_views(report['engines'])}
                # Only explicit operational inputs survive the completed planner.
                if 'causal_discovery_evidence' in report:
                    hot['causal_discovery_evidence'] = report['causal_discovery_evidence']
                hot['catalog_ready'] = [_project(row, CATALOG_FIELDS) for row in inputs['catalog']]
                checkpoint = durable_checkpoint_view(checkpoint)
                result = compatible = bundle = external = observations = source_reports = None
                _stage_preopen_rows(report, spool)
                report['engines'] = spool.stage('engines', canonical_ascii_chunks(report['engines']))
                for key in ('source_reports', 'events'):
                    if key in report:
                        report[key] = spool.stage(key, canonical_ascii_chunks(report[key]))
                frozen = pre = pre_report = None
                allocator_phases['planner_staged'] = release_idle_heap()
            from .families import family_reports
            from .lab import evaluate_runtime_lab
            from .entry_signals import evaluate_runtime_entry_signals
            from .funnel import evaluate_runtime_funnel
            report["family_routing"] = family_reports(self.database, as_of=at,
                catalog=inputs["full_catalog"], sources=sources,compact_missing=True)
            measured("family_routing")
            if spool:
                family = report['family_routing']
                family_view = _durable_family_routing(family)
                hot['family_routing'] = {'families': family.get('families', []),
                    'instruments': [_project(row, FAMILY_FIELDS) for row in family.get('instruments', [])]}
                report['family_routing'] = spool.stage('family_routing', canonical_ascii_chunks(family))
                report['catalog_ready'] = spool.stage('catalog_ready', canonical_ascii_chunks(report['catalog_ready']))
                family = inputs = sources = base = None
                allocator_phases['family_staged'] = release_idle_heap()
            if parts and reuse:
                previous['lab'] = parts.load('lab')
            report["economic_exit_lab"], checkpoint["lab"] = evaluate_runtime_lab(
                self.database, as_of=at, previous=previous.get("lab"))
            previous.pop("lab", None)
            measured("economic_exit_lab")
            if parts and reuse:
                previous['entry_signals'] = parts.load('entry_signals')
            report["entry_signal_lab"], checkpoint["entry_signals"] = evaluate_runtime_entry_signals(
                self.database, as_of=at, previous=previous.get("entry_signals"))
            previous.pop("entry_signals", None)
            measured("entry_signal_lab")
            if parts and reuse:
                previous['funnel'] = parts.load('funnel')
            report["operational_funnel"], checkpoint["funnel"] = evaluate_runtime_funnel(
                self.database, as_of=at, planner_report=hot,
                entry_signal_report=report["entry_signal_lab"],
                exit_lab_report=report["economic_exit_lab"], previous=previous.get("funnel"),
                private_runtime=True)
            measured("operational_funnel")
            # Never call a signal, economics or risk result as an execution
            # callback. These reports cannot reach the factual broker.
            if spool:
                for key in ('economic_exit_lab', 'entry_signal_lab', 'operational_funnel'):
                    hot[key] = report[key]
                # Restore all bounded dashboard metadata from the actual audit.
                for key in ('preopen_immutable', 'preopen_file', 'preopen_quality',
                            'capacity_open_status', 'observation_execution', 'active_paper_scanner_authority'):
                    if key in report:
                        hot[key] = report[key]
            reusable_rows = () if spool else shared_preopen_row_lists(report)
            durable = durable_report_view(hot, catalog_ready_count=ready_count,
                engine_views=engine_views, family_view=family_view)
            causal = durable_checkpoint_view(checkpoint)
            # These inputs have no remaining caller. Releasing local references
            # preserves every value still owned by report/durable/causal, while
            # avoiding a previous tick and unused source data beside the audit.
            previous = compatible = bundle = inputs = sources = source_reports = observations = None
            capacity = frozen = checkpoint = result = base = None
            audit = stored('computed', lambda: (
                files.write_streamed_report(report, immutable_shared_rows=reusable_rows)
                if reusable_rows else files.write_streamed_report(report)))
            storage_profile['computed'].update(getattr(files,'report_write_profile',{}))
            durable["computed_report"] = audit
            stored('latest', lambda: files.write("latest.json.gz", durable))
            stored('checkpoint', lambda: files.write_checkpoint(causal) if spool
                   else files.write("checkpoint.json.gz", causal))
            measured("durable_serialization")
            status = {k: report[k] for k in ("schema", "as_of", "phase", "status", "mode",
                "provider_requests", "real_orders_sent", "real_routes", "source_database_effect")}
            status.update(configuration_fingerprint=configuration,
                preopen_digests=preopen_digests,
                catalog_ready_count=ready_count, report_digest=digest(durable),
                computed_report_digest=audit["sha256"], report_projection="SELECTION_CONTRACT_V1",
                provider_capacity_open="NO_VERIFICADO", ppi_watch="UNTOUCHED",
                capacity_policy_status=capacity_policy["status"],
                entry_signal_lab_status=report["entry_signal_lab"]["status"],
                operational_funnel_status=report["operational_funnel"]["status"])
            status["runtime_sql_diagnostics"] = _runtime_sql_status(self.last_sqlite_diagnostics)
            status['generation_timing'] = {'poll_seconds':POLL_SECONDS,
                'tick_seconds':round(time.monotonic()-tick_started,3),
                'tick_measurement_boundary':'BEFORE_STATUS_AND_CURRENT',
                'stages_seconds':phase_seconds,
                'tick_cpu_seconds':round(time.process_time()-tick_cpu_started,3),
                'stages_cpu_seconds':phase_cpu_seconds,
                'stages_process_peak_rss_kib':phase_peak_rss,
                'storage_profile':storage_profile,
                'allocator_phases':allocator_phases,
                'process_peak_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}
            if spool:
                status['generation_timing']['audit_staging'] = list(spool.profile)
            publish_started = time.monotonic()
            files.write("status.json", status)
            committed = files.publish_generation(durable, status)
            publish_completed = time.monotonic()
            files.prune_computed_reports(keep=audit["file"])
            # Separate receipt avoids a circular hash: the immutable cut is
            # already committed. These clocks never replace the source as_of.
            files.write("publication.json", {"schema":"rc6.publication-cost.v1",
                **committed, "source_as_of":at.isoformat(),
                "publish_completed_at":datetime.now(timezone.utc).isoformat(),
                "start_compute_monotonic":tick_started,
                "pointer_committed_monotonic":publish_completed,
                "total_seconds":round(publish_completed-tick_started,6),
                "total_cpu_seconds":round(time.process_time()-tick_cpu_started,6),
                "publish_seconds":round(publish_completed-publish_started,6),
                "prune_seconds":round(time.monotonic()-publish_completed,6),
                "poll_seconds":POLL_SECONDS})
            return hot


def run_worker(database, stop, *, clock_fn=None):
    """Existing runtime owns the process/restart loop; only evidence is written."""
    clock_fn = clock_fn or (lambda: datetime.now(timezone.utc).isoformat())
    try:
        os.nice(10)
    except OSError:
        pass
    worker = None
    while not stop.is_set():
        try:
            if worker is None:
                from cg_paper_workspace import artifact_root
                history = Path(os.getenv("HIST_DB_PATH", str(Path(os.getenv("DATA_DIR", "data")) / "market_history.db")))
                worker = ShadowRuntime(database,
                    evidence_root=artifact_root(database) / "dynamic-shadow",
                    history_database=history if history.exists() else None, spool_audit=True)
            report = worker.tick(clock_fn())
            try:
                LOG.info("DYNAMIC_SHADOW_RUNTIME status=%s phase=%s real_orders_sent=0 provider_requests=0",
                         report["status"], report["phase"])
            finally:
                # The full audit already lives in private committed bytes.
                # Do not retain it while the next tick allocates another one.
                del report
                try:
                    LOG.info("DYNAMIC_SHADOW_IDLE_MEMORY %s", json.dumps(release_idle_heap(), sort_keys=True))
                except Exception as memory_error:
                    # Optional allocator telemetry cannot revoke a committed
                    # financial cut or terminate the scanner's child loop.
                    LOG.warning("DYNAMIC_SHADOW_IDLE_MEMORY_UNAVAILABLE:%s", type(memory_error).__name__)
        except Exception as exc:
            # A bounded failed SHADOW read/write cannot stop the exit clock or
            # turn a stale prior report into current success. No source event.
            LOG.warning("DYNAMIC_SHADOW_RUNTIME_FAIL_CLOSED:%s", type(exc).__name__)
            if worker is not None:
                try:
                    failure = {"schema": VERSION, "as_of": clock_fn(),
                        "status": "FAIL_CLOSED", "error_class": type(exc).__name__,
                        "real_orders_sent": 0, "real_routes": "NOT_CALLED", "provider_requests": 0}
                    if isinstance(exc, EvidencePayloadLimit):
                        failure["evidence_file"] = exc.evidence_file
                        failure["evidence_measured_bytes"] = exc.measured_bytes
                        failure["evidence_limit_bytes"] = exc.limit_bytes
                    if isinstance(exc, sqlite3.Error):
                        code = getattr(exc, "sqlite_errorcode", None)
                        label = getattr(exc, "sqlite_errorname", None)
                        if type(code) is int:
                            failure["sqlite_errorcode"] = code
                        if isinstance(label, str) and label.startswith("SQLITE_"):
                            failure["sqlite_errorname"] = label[:48]
                    stage = getattr(exc, "rc6_stage", None)
                    if stage in {"CATALOG_FULL", "OPEN_POSITIONS", "SCHEMA_TABLES",
                                 "INTRADAY_CONTRACTS", "INTRADAY_POINTS",
                                 "MARKET_SNAPSHOTS", "COMPLETE"}:
                        failure["runtime_read_stage"] = stage
                        failure["runtime_deadline_expired"] = (
                            getattr(exc, "rc6_deadline_expired", None) is True)
                    if isinstance(exc, (sqlite3.Error, ValueError)):
                        # Only a reviewed source location is published. Never
                        # persist exception text, SQL, paths or a traceback.
                        allowed = {
                            "rc6_dynamic_universe/runtime.py": "runtime",
                            "rc6_shadow_runtime/worker.py": "worker",
                            "rc6_shadow_runtime/preopen.py": "preopen",
                            "rc6_dynamic_universe/promotion.py": "promotion",
                            "rc6_shadow_runtime/stages.py": "stages",
                            "rc6_shadow_runtime/families.py": "families",
                            "rc6_shadow_runtime/lab.py": "lab",
                            "rc6_shadow_runtime/entry_signals.py": "entry_signals",
                            "rc6_shadow_runtime/funnel.py": "funnel",
                            "rc6_shadow_runtime/source_reads.py": "source_reads",
                            "rc6_shadow_runtime/read_contract.py": "read_contract",
                            "rc6_shadow_runtime/persistence.py": "persistence",
                            "rc6_audit_evidence/sqlite_snapshot.py": "snapshot",
                            "rc6_dynamic_universe/common.py": "common",
                            "rc6_dynamic_universe/live.py": "live",
                        }
                        for frame, line in traceback.walk_tb(exc.__traceback__):
                            filename = frame.f_code.co_filename.replace("\\", "/")
                            for suffix, module in allowed.items():
                                if filename.endswith("/" + suffix):
                                    failure["error_module"] = module
                                    failure["error_line"] = line
                    failure["runtime_sql_diagnostics"] = _runtime_sql_status(
                        getattr(worker, "last_sqlite_diagnostics", None))
                    with worker.files as files:
                        files.write("status.json", failure)
                except (OSError, ValueError):
                    LOG.warning("DYNAMIC_SHADOW_STATUS_UNAVAILABLE")
        stop.wait(POLL_SECONDS)
