"""Canonical periodic SHADOW worker over existing observations only.

No market-data client, trading writer, broker, fill, network transport or manual
bundle. A missing preopen/OPEN capacity remains an explicit closed gate.
"""
from datetime import datetime, timedelta, timezone
import json
import logging
import math
import os
from pathlib import Path
import sqlite3
import time

import ak_byma_calendar as calendar
from co_market_sessions_hf6 import (TZ, BYMA_PAPER_SPOT_OPEN,
    BYMA_PAPER_SPOT_CLOSE, byma_paper_spot_phase)
from rc6_dynamic_universe.common import digest, stamp
from rc6_dynamic_universe.live import run_shadow
from rc6_dynamic_universe.runtime import read_runtime
from rc6_dynamic_universe.sources import source_observations, source_reason_code, sanitize_source_errors
from .persistence import (EvidenceFiles, failure_reason, shadow_evidence_root,
                          shadow_archive_root, shadow_archive_maximum_bytes,
                          DEFAULT_MAXIMUM_FILES, GENERATION_SCHEMA)

LOG = logging.getLogger("dynamic_shadow")
VERSION = "WS-FIX-AUDIT-08-RUNTIME-v4"
SOURCE_FILES = {"BYMA": "rc6_public_sources_latest.json",
                "IOL_MCP": "iol_shadow_latest.json",
                "IOL_FAMILY_REFERENCE": "iol_family_reference_latest.json"}


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


class ShadowRuntime:
    def __init__(self, database, *, evidence_root=None, history_database=None,
                 source_roots=None, capacity_path=None, policies=None,
                 maximum_bytes=128 * 1024**2, maximum_files=DEFAULT_MAXIMUM_FILES,
                 row_limit=20000, fault_inject=None, archive_root=None,
                 archive_maximum_bytes=512 * 1024**2, query_budget_seconds=0.5):
        if (isinstance(query_budget_seconds, bool) or not isinstance(query_budget_seconds, (int, float))
                or not math.isfinite(query_budget_seconds) or not 0 < query_budget_seconds <= 2):
            raise ValueError("INVALID_READ_BUDGET")
        self.query_budget_seconds = float(query_budget_seconds)
        self.database = Path(database).resolve(strict=True)
        self.history_database = Path(history_database).resolve() if history_database else None
        self.root = Path(evidence_root) if evidence_root is not None else shadow_evidence_root(self.database)
        # Explicit construction supports isolated offline fixtures without an
        # archive. Operational construction uses the canonical factory below.
        archive_root = Path(archive_root).absolute() if archive_root is not None else None
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
        owned_outputs = {(self.root / name).absolute() for name in ("latest.json.gz", "CURRENT.json")}
        owned_outputs.add(self.root.absolute())
        if configured_shadow and Path(configured_shadow).absolute() in owned_outputs:
            # The factual selectors read this worker's own output. Only that
            # exact output role is writable; a policy/report/approval sharing
            # the same path remains protected, as do aliases handled by files.
            other_inputs = {Path(v).absolute() for k, v in self.capacity_controller.environ.items()
                if k.startswith("POROTA_CAPACITY_") and k.endswith("_PATH")
                and k != "POROTA_CAPACITY_SHADOW_PATH" and v}
            capacity_protected = [p for p in capacity_protected
                if p.absolute() not in owned_outputs or p.absolute() in other_inputs]
        protected = ([self.database, self.history_database, self.capacity_path]
                     + self.source_inputs + capacity_protected)
        self.files = EvidenceFiles(self.root, protected=protected, maximum_bytes=maximum_bytes,
            maximum_files=maximum_files, fault_inject=fault_inject,
            archive_root=archive_root, archive_maximum_bytes=archive_maximum_bytes)
        self.configuration = digest({"version": VERSION, "row_limit": row_limit,
            "query_budget_seconds": self.query_budget_seconds,
            "provider_additional_requests": 0, "tick_seconds": 30,
            "policies": self.policies, "history": str(self.history_database),
            "sources": {k: list(map(str, v)) for k, v in self.source_paths.items()},
            "capacity": str(self.capacity_path), "evidence_schema": GENERATION_SCHEMA,
            "evidence_root": str(self.root), "retention": {"maximum_bytes": maximum_bytes,
                "maximum_files": maximum_files, "archive_maximum_bytes": archive_maximum_bytes},
            "archive_configured": archive_root is not None,
            "archive_root": str(archive_root) if archive_root is not None else None})

    @classmethod
    def from_environment(cls, database, **kwargs):
        """Canonical worker construction reused by read-only predeploy gates."""
        history = Path(os.getenv("HIST_DB_PATH", str(Path(os.getenv("DATA_DIR", "data")) / "market_history.db")))
        kwargs.setdefault("history_database", history if history.exists() else None)
        kwargs.setdefault("archive_root", shadow_archive_root(database))
        kwargs.setdefault("archive_maximum_bytes", shadow_archive_maximum_bytes())
        return cls(database, **kwargs)

    def configuration_fingerprint(self, as_of, *, capacity_policy=None):
        """Read-only configuration identity shared by tick and health gates."""
        policy = capacity_policy if capacity_policy is not None else self.capacity_controller.state(stamp(as_of))
        return digest({"runtime": self.configuration, "capacity_policy": policy["fingerprint"],
            "entry_signal_lab": "rc6.runtime-entry-signals.v1",
            "operational_funnel": "rc6.prospective-operational-funnel.v1"})

    def _metadata(self, at, since):
        c = sqlite3.connect(self.database.as_uri() + "?mode=ro", uri=True, timeout=.005)
        c.row_factory = sqlite3.Row
        try:
            c.execute("PRAGMA query_only=ON")
            end = time.monotonic() + .15
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
                        "endpoint": "intraday", "useful": False, "native_reason": source_reason_code(code),
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
                        reports[source] = sanitize_source_errors(value)
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
        if max(len(points), len(counters), len(last)) > self.row_limit:
            raise ValueError("SHADOW_RADAR_CAPACITY_REACHED")
        for o in sorted(observations, key=lambda r: (r["source_at"], r["received_at"])):
            if not o.get("useful") or o.get("endpoint") not in {"current", "radar"}:
                continue
            when = stamp(o["source_at"])
            if when < max(opening, cutoff) or (at - when).total_seconds() > 120:
                continue
            ident = digest(o["identity"])
            previous = stamp(last[ident]) if ident in last else None
            if o.get("is_trade") and (previous is None or when > previous):
                if ((ident not in counters and len(counters) >= self.row_limit)
                        or (ident not in last and len(last) >= self.row_limit)):
                    raise ValueError("SHADOW_RADAR_CAPACITY_REACHED")
                counters[ident] = counters.get(ident, 0) + 1
                last[ident] = when.isoformat()
            # Preserve the originally observed count on repeat/revision.
            key = digest((o["identity"], o.get("source"), o["source_at"]))
            if key not in points and len(points) >= self.row_limit:
                raise ValueError("SHADOW_RADAR_CAPACITY_REACHED")
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
        at = stamp(as_of)
        with self.files as files:
            capacity_policy = self.capacity_controller.state(at)
            configuration = self.configuration_fingerprint(at, capacity_policy=capacity_policy)
            committed = files.read_writer_generation(checkpoint=True)
            previous = committed["checkpoint"] if committed else {}
            if previous and stamp(previous["as_of"]) > at:
                raise ValueError("SHADOW_CHECKPOINT_FROM_FUTURE")
            inputs = read_runtime(self.database, as_of=at, row_limit=self.row_limit,
                                  query_budget_seconds=self.query_budget_seconds)
            source_id, failures = self._metadata(at, previous.get("as_of", at.isoformat()))
            reuse = (previous.get("source_identity") == source_id and
                     previous.get("runtime_configuration") == configuration)
            if not reuse:
                previous = {}
            # Source identity and runtime configuration are part of the durable
            # checkpoint, independent of the planner's capacity fingerprint.
            started = previous.get("started_at", at.isoformat())
            context = session_context(at)
            sources, source_errors = self._sources()
            capacity = self._capacity(as_of=at, capacity_policy=capacity_policy)
            freeze_name = "preopen-" + context["session"] + ".json.gz"
            frozen = files.read(freeze_name)
            if frozen and frozen["source_identity"] != source_id:
                raise ValueError("SHADOW_PREOPEN_SOURCE_IDENTITY_MISMATCH")
            if frozen is None and context["cutoff"] < at < context["opening"]:
                from .preopen import build_preopen_inputs
                pre = build_preopen_inputs(self.database, self.history_database, as_of=at,
                    session_open=context["opening"], cutoff=context["cutoff"])
                bundle = {**inputs, **pre, "as_of": at.isoformat(),
                    "session_open": context["opening"].isoformat(),
                    "preopen_cutoff": context["cutoff"].isoformat(), "frozen_at": at.isoformat(),
                    "capacity_report": capacity, "capacity_policy": capacity_policy,
                    "policies": self.policies, "observations": []}
                pre_report = run_shadow(bundle, freeze_only=True)
                frozen = {"schema": VERSION, "source_identity": source_id,
                    "frozen": pre_report["frozen"], "quality": pre["quality"],
                    "intraday_history": pre.get("intraday_history", [])}
                files.write(freeze_name, frozen, immutable=True)
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
            if frozen is None:
                report = {**base, "status": ("PREOPEN_SNAPSHOT_PENDING_AFTER_SESSION_CUTOFF"
                          if at <= context["cutoff"] else "PREOPEN_SNAPSHOT_REQUIRED_DURING_SESSION"),
                          "engines": {}, "catalog_ready_count": len(inputs["catalog"])}
                # Even a closed preopen gate must account for actual received
                # sources; it cannot claim an empty audit over nonempty input.
                from rc6_dynamic_universe.sources import native_source_reports, audit_sources
                report["source_reports"] = native_source_reports(inputs["observations"], as_of=at) + [
                    source_observations(value, source=name, as_of=at)
                    for name, value in sources.items() if name != "IOL_FAMILY_REFERENCE"]
                report["source_audit"] = audit_sources(reports=report["source_reports"], as_of=at)
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
                    "source_native_observations": inputs["observations"],
                    "source_observation_reports": source_reports,
                    "observation_not_before": started,
                    "observation_received_after": previous.get("as_of", started),
                    "sources": {k: v for k, v in sources.items() if k != "IOL_FAMILY_REFERENCE"}}
                result = run_shadow(bundle, previous=compatible)
                from .stages import enrich_pipeline
                result = enrich_pipeline(self.database, result, as_of=at)
                report = {**result, **base, "status": "SHADOW_OBSERVING",
                    "preopen_quality": frozen["quality"],
                    "preopen_immutable": True, "preopen_file": freeze_name,
                    "capacity_open_status": "NO_VERIFICADO" if not capacity else capacity.get("status"),
                    "observation_execution": "LOCAL_REUSE_ONLY; native PPI Intraday preserved",
                    "active_paper_scanner_authority": "FACTUAL_SIGNAL_AND_ADMISSION_UNCHANGED; sampling_policy="
                        + capacity_policy["status"] + "; shadow fills NOT_CALLED"}
                checkpoint["engines"] = result["engines"]
            from .families import family_reports
            from .lab import evaluate_runtime_lab
            from .entry_signals import evaluate_runtime_entry_signals
            from .funnel import evaluate_runtime_funnel
            report["family_routing"] = family_reports(self.database, as_of=at,
                catalog=inputs["full_catalog"], sources=sources)
            report["economic_exit_lab"], checkpoint["lab"] = evaluate_runtime_lab(
                self.database, as_of=at, previous=previous.get("lab"))
            report["entry_signal_lab"], checkpoint["entry_signals"] = evaluate_runtime_entry_signals(
                self.database, as_of=at, previous=previous.get("entry_signals"))
            report["operational_funnel"], checkpoint["funnel"] = evaluate_runtime_funnel(
                self.database, as_of=at, planner_report=report,
                entry_signal_report=report["entry_signal_lab"],
                exit_lab_report=report["economic_exit_lab"], previous=previous.get("funnel"),
                return_encoded_checkpoint=True)
            # Never call a signal, economics or risk result as an execution
            # callback. These reports cannot reach the factual broker.
            status = {k: report[k] for k in ("schema", "as_of", "phase", "status", "mode",
                "provider_requests", "real_orders_sent", "real_routes", "source_database_effect")}
            status.update(configuration_fingerprint=configuration,
                preopen_digests={k: v["digest"] for k, v in (frozen or {}).get("frozen", {}).items()},
                catalog_ready_count=len(inputs["catalog"]),
                provider_capacity_open="NO_VERIFICADO", ppi_watch="UNTOUCHED",
                capacity_policy_status=capacity_policy["status"],
                entry_signal_lab_status=report["entry_signal_lab"]["status"],
                operational_funnel_status=report["operational_funnel"]["status"])
            generation = files.commit_generation(report, checkpoint, status,
                source_watermark={"source_identity": source_id, "as_of": at.isoformat(),
                    "previous_as_of": previous.get("as_of"), "started_at": started},
                configuration_fingerprint=configuration, _take_payloads=True)
            return generation["report"]


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
                worker = ShadowRuntime.from_environment(database)
            report = worker.tick(clock_fn())
            LOG.info("DYNAMIC_SHADOW_RUNTIME status=%s phase=%s real_orders_sent=0 provider_requests=0",
                     report["status"], report["phase"])
            del report
        except Exception as exc:
            # A bounded failed SHADOW read/write cannot stop the exit clock or
            # turn a stale prior report into current success. No source event.
            LOG.warning("DYNAMIC_SHADOW_RUNTIME_FAIL_CLOSED:%s:%s", type(exc).__name__,
                        failure_reason(exc))
            if worker is not None:
                try:
                    with worker.files as files:
                        files.record_failure(as_of=clock_fn(), error=exc)
                except (OSError, ValueError):
                    LOG.warning("DYNAMIC_SHADOW_STATUS_UNAVAILABLE")
        stop.wait(30)
