#!/usr/bin/env python3
"""Cheap encoder comparison against the published dfc240a Git objects.

This publication-only comparison is not BIG, focal, runtime or promotion
evidence. The default uses in-memory synthetic roles. The optional small native
fixture produces its own offline Source/publications in an owned temporary
directory and removes that directory on completion. Neither mode calls a
provider. Its two in-memory comparison packs check the mutable-header pattern
without replacing any material workload. RAW payloads are never emitted.
"""
import argparse
import cProfile
from collections import Counter
from contextlib import closing
from decimal import Decimal
import hashlib
import importlib.util
import json
from pathlib import Path
import resource
import subprocess
import sys
import tempfile
import time


BASE_SHA = "dfc240a478cf08e0c6a9b0ba1b760307b09a9565"
CLOCK = "2026-10-05T16:00:00.000001+00:00"
MUTABLE = {"status", "cross_payload_hashes", "evidence_retention",
           "report_digest", "checkpoint_digest"}
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _frozen(name, path):
    raw = subprocess.check_output(["git", "show", BASE_SHA + ":" + path], cwd=ROOT)
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(name, loader=None))
    module.__package__ = "rc6_shadow_runtime"
    sys.modules[name] = module
    exec(compile(raw, path, "exec"), module.__dict__)
    return module


def _roles(size):
    basis = {"label": "native factual identity " * 20, "as_of": CLOCK,
             "typed": [-0.0, 0.0, False, 0, "\u00d1/\0\U0001f600", None]}
    engines = {}
    for engine in range(5):
        instruments = {
            str(index): {"identity": (f"T{index:04d}", "ACCIONES", "BYMA", "ARS", "A-24HS"),
                         "attempts": [["intraday", CLOCK, True]], "basis": basis,
                         "samples": [CLOCK] * 5, "rank": index,
                         "warmup": {"observations": index, "target": 3},
                         "delta": -0.0 if index % 2 else 0.0, "book_at": None}
            for index in range(size)}
        engines[str(engine)] = {"instruments": instruments,
                               "basis": {"source": "PAPER"}, "planned_at": CLOCK}
    return {"report": {"engines": engines, "as_of": CLOCK, "mode": "SHADOW",
                       "real_orders_sent": 0, "real_routes": "NOT_CALLED",
                       "factual_summary": {"stages": Counter({"READY": 5 * size}),
                                           "net": Decimal("-0.00")}, "status": "OBSERVING"},
            "checkpoint": {"engines": engines, "as_of": CLOCK, "status": "OBSERVING"},
            "status": {"as_of": CLOCK, "status": "OBSERVING"}}


def _run(module, size, *, native_values=None, mutable=MUTABLE):
    values = _roles(size) if native_values is None else {
        role: dict(value) for role, value in native_values.items()}
    phases, entered, census, native_caches = {}, {}, {}, {}
    roles_by_identity = {id(value): role for role, value in values.items()}

    def observe(phase, edge):
        if edge == "ENTER":
            entered[phase] = time.perf_counter(), time.process_time()
        else:
            state = phases.setdefault(phase, {"calls": 0, "inclusive_seconds": 0.0,
                                              "inclusive_cpu_seconds": 0.0})
            state["calls"] += 1
            wall, cpu = entered.pop(phase)
            state["inclusive_seconds"] += time.perf_counter() - wall
            state["inclusive_cpu_seconds"] += time.process_time() - cpu
            if phase == "COUNT":
                # Observe the original closed count boundary. No constructor,
                # admission method or operation is replaced for this census.
                frame = sys._getframe(2)
                builder, value = frame.f_locals["self"], frame.f_locals["value"]
                census[roles_by_identity[id(value)]] = {
                    "unique_containers": len(builder.objects),
                    "container_incoming_edges": sum(builder.incoming.values())}
            if phase == "CAPTURE":
                frame = sys._getframe(2)
                builder, value = frame.f_locals["builder"], frame.f_locals["value"]
                native_caches[roles_by_identity[id(value)]] = {
                    "field_tokens": len(getattr(builder, "_native_field_tokens", None) or {}),
                    "field_token_bytes": getattr(builder, "_native_field_token_bytes", 0),
                    "key_schemas": len(getattr(builder, "_native_key_schemas", None) or {}),
                    "key_schema_bytes": getattr(builder, "_native_key_schema_bytes", 0)}

    token = module.packed._STORAGE_PHASE_OBSERVER.set(observe)
    observation = module._ReplayObservation(
        {"healthy": True, "constructor_closes": 3, "report_puts_returned": 3})
    observer_token = module._PUBLICATION_REPLAY_OBSERVATION.set(observation)
    health_token = module._PUBLICATION_REPLAY_HEALTH.set(observation.shared)
    profiler = cProfile.Profile()
    profiler.enable()
    start = time.perf_counter()
    process = time.process_time()
    try:
        prepared, _ = module.prepare_publication_storage(values, mutable=mutable,
            durable_limit=32 * 1024**2, expansion_limit=512 * 1024**2)
    finally:
        module.packed._STORAGE_PHASE_OBSERVER.reset(token)
        module._PUBLICATION_REPLAY_OBSERVATION.reset(observer_token)
        module._PUBLICATION_REPLAY_HEALTH.reset(health_token)
    prepare_seconds = time.perf_counter() - start
    prepare_cpu_seconds = time.process_time() - process
    wires, proof_seconds, encode_seconds, proof_cpu_seconds, encode_cpu_seconds = [], 0.0, 0.0, 0.0, 0.0
    for step in range(2):
        for value in values.values():
            value.pop("cross_payload_hashes", None)
        start = time.perf_counter()
        process = time.process_time()
        hashes = {role: prepared[role].metrics(values[role])[0]
                  for role in ("report", "checkpoint")}
        proof_seconds += time.perf_counter() - start
        proof_cpu_seconds += time.process_time() - process
        for value in values.values():
            value["cross_payload_hashes"] = hashes
        payloads = {}
        for role in values:
            if role == "status":
                values[role].update(report_digest=payloads["report"],
                                    checkpoint_digest=payloads["checkpoint"])
            start = time.perf_counter()
            process = time.process_time()
            wire, payloads[role] = prepared[role].encode(values[role])
            encode_seconds += time.perf_counter() - start
            encode_cpu_seconds += time.process_time() - process
            raw = json.dumps(wire, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, default=str, allow_nan=False).encode()
            wires.append((role, payloads[role], len(raw), hashlib.sha256(raw).hexdigest()))
        if step == 0:
            for role in ("report", "status"):
                values[role]["evidence_retention"] = {
                    "status": "RETENTION_OK", "files": 7, "bytes": 123456}
    profiler.disable()
    # Profile self-time is exclusive; stage time is inclusive. Never add
    # nested profile totals to the measured preparation/encoding wall time.
    counters = {}
    for entry in profiler.getstats():
        code = entry.code
        name = code.co_name if hasattr(code, "co_name") else str(code)
        if name.endswith("builtins.sorted>"):
            name = "sorted"
        elif code is module.packed.json.JSONEncoder.__init__.__code__:
            name = "json_encoder_construction"
        elif name.endswith("_json.encode_basestring_ascii>"):
            name = "json_string_encoder"
        if name in {"_canonical", "_record", "_named", "_count", "_append",
                    "_slots", "_proof", "_expanded", "eligible_role", "matches",
                    "_room", "_root_volatile", "_small_plain", "_scalar",
                    "_binding_scalar", "_shape", "native_callback_free_role", "sorted",
                    "json_encoder_construction", "json_string_encoder"}:
            state = counters.setdefault(name, {"calls": 0, "exclusive_seconds": 0.0,
                                               "inclusive_seconds": 0.0})
            state["calls"] += entry.callcount
            state["exclusive_seconds"] += entry.inlinetime
            state["inclusive_seconds"] += entry.totaltime
    memo = getattr(prepared["report"], "_publication_records", None)
    return {"prepare_inclusive_seconds": prepare_seconds,
            "prepare_inclusive_cpu_seconds": prepare_cpu_seconds,
            "cross_hashes_inclusive_seconds": proof_seconds,
            "cross_hashes_inclusive_cpu_seconds": proof_cpu_seconds,
            "encode_inclusive_seconds": encode_seconds, "storage_phases": phases,
            "encode_inclusive_cpu_seconds": encode_cpu_seconds,
            "role_graph_census": census,
            "native_cache_census": native_caches,
            "functions": counters, "replay_counters": observation.counts,
            "record_cache": {"hits": getattr(memo, "hits", 0),
                             "misses": getattr(memo, "misses", 0),
                             "retained_bytes": getattr(memo, "bytes", 0)},
            "wire_receipts": wires}


def _string_scalars(prior, candidate, count):
    """One control and one C-encoder pass, never a material workload."""
    values = [f"{index:064x}-\u00d1/\0\U0001f600\ud800\udfff" for index in range(count)]
    states = []
    for encode in (prior._canonical,
                   lambda value: candidate._NATIVE_STRING_ENCODER(value).encode("ascii")):
        digest, logical_bytes = hashlib.sha256(), 0
        wall, cpu = time.perf_counter(), time.process_time()
        for value in values:
            raw = encode(value)
            digest.update(raw)
            logical_bytes += len(raw)
        states.append({"inclusive_seconds": time.perf_counter() - wall,
                       "inclusive_cpu_seconds": time.process_time() - cpu,
                       "logical_bytes": logical_bytes, "logical_sha256": digest.hexdigest()})
    if any(state["logical_sha256"] != states[0]["logical_sha256"]
           or state["logical_bytes"] != states[0]["logical_bytes"] for state in states):
        raise SystemExit("PUBLICATION_NATIVE_STRING_SCALAR_IDENTITY_MISMATCH")
    return {"exact_native_string_values": count, "baseline": states[0], "candidate": states[1],
            "exact_canonical_bytes_and_digest_match": True,
            "material_or_runtime_validation": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog-size", type=int, default=128)
    parser.add_argument("--scalar-string-count", type=int, default=0)
    parser.add_argument("--native-fixture-size", type=int)
    parser.add_argument("--native-stress-fixture-size", type=int)
    parser.add_argument("--native-workload-root", type=Path)
    args = parser.parse_args()
    if not 1 <= args.catalog_size <= 512:
        parser.error("cheap probe requires 1..512 synthetic catalogue rows")
    if not 0 <= args.scalar_string_count <= 65536:
        parser.error("cheap scalar probe requires at most 65536 exact strings")
    if args.native_fixture_size is not None and not 1 <= args.native_fixture_size <= 256:
        parser.error("cheap native fixture requires 1..256 catalogue rows")
    if args.native_stress_fixture_size is not None:
        if args.native_fixture_size is not None or not 1 <= args.native_stress_fixture_size <= 256:
            parser.error("use one cheap native fixture, at most 256 catalogue rows")
        if args.native_workload_root is None or args.native_workload_root.exists():
            parser.error("native stress probe requires a new controller-owned workload directory")
    from rc6_shadow_runtime import publication_storage
    prior_packed = _frozen("rc6_shadow_runtime._probe_prior_packed", "rc6_shadow_runtime/packed_storage.py")
    prior = _frozen("rc6_shadow_runtime._probe_prior_publication", "rc6_shadow_runtime/publication_storage.py")
    prior.packed = prior_packed
    values, mutable, native_source = None, MUTABLE, None
    if args.native_fixture_size is not None or args.native_stress_fixture_size is not None:
        captured = []
        original = publication_storage.prepare_publication_storage
        def capture(native_values, **kwargs):
            # Keep only the latest already produced small fixture payload.
            # This wrapper does not alter Source/query/productive allowances.
            captured[:] = [(native_values, kwargs["mutable"])]
            return original(native_values, **kwargs)
        publication_storage.prepare_publication_storage = capture
        try:
            if args.native_stress_fixture_size is None:
                from tests.rc6_dashboard_native_fixture import native_fixture
                with tempfile.TemporaryDirectory(prefix="rc6-publication-algorithm-") as directory:
                    fixture = native_fixture(Path(directory), count=args.native_fixture_size,
                                             with_future=False, with_spot=False)
                    assert fixture.cut["report"]["real_orders_sent"] == 0
                    assert fixture.cut["report"]["real_routes"] == "NOT_CALLED"
            else:
                from scripts.rc6_issue465_stress import fixture_database, source_custody_snapshot, AT
                from rc6_shadow_runtime.worker import ShadowRuntime
                args.native_workload_root.mkdir(mode=0o700)
                database = args.native_workload_root / "source.sqlite"
                store = fixture_database(database, catalog_count=args.native_stress_fixture_size,
                                         observations_per_identity=5)
                with closing(store.connect()) as connection, connection:
                    catalog = connection.execute("SELECT COUNT(*) FROM financial_instrument_catalog").fetchone()[0]
                    observations = connection.execute("SELECT COUNT(*) FROM ppi_intraday_points").fetchone()[0]
                before = source_custody_snapshot(database)
                worker = ShadowRuntime(database, evidence_root=args.native_workload_root / "shadow", source_roots=[])
                report = worker.tick(AT)
                after = source_custody_snapshot(database)
                assert catalog == args.native_stress_fixture_size
                assert observations == 5 * args.native_stress_fixture_size
                assert report["real_orders_sent"] == 0 and report["real_routes"] == "NOT_CALLED"
                assert before == after, "PUBLICATION_PROBE_SOURCE_CUSTODY_CHANGED"
                native_source = {"catalog": catalog, "observations": observations,
                    "source_unchanged": True, "source_sha256": before[""]["sha256"],
                    "source_sidecars": sorted(before), "native_ticks": 1,
                    "factual_material_qualification": False}
            if captured:
                values, mutable = captured[0]
            else:
                raise ValueError("NATIVE_PUBLICATION_PAYLOAD_NOT_CAPTURED")
        finally:
            publication_storage.prepare_publication_storage = original
    baseline = _run(prior, args.catalog_size, native_values=values, mutable=mutable)
    candidate = _run(publication_storage, args.catalog_size, native_values=values, mutable=mutable)
    if baseline["wire_receipts"] != candidate["wire_receipts"]:
        raise SystemExit("PUBLICATION_CANONICAL_WIRE_IDENTITY_MISMATCH")
    strings = (_string_scalars(prior_packed, publication_storage.packed, args.scalar_string_count)
               if args.scalar_string_count else None)
    print(json.dumps({"schema": "rc6.publication-algorithm-probe.v1",
                      "base_sha": BASE_SHA, "python": sys.version,
                      "synthetic_catalog_size": args.catalog_size,
                      "native_fixture_size": args.native_fixture_size,
                      "native_stress_fixture_size": args.native_stress_fixture_size,
                      "native_source": native_source,
                      "string_scalar_probe": strings,
                      "packs_per_publication": 2, "exact_wire_and_logical_digest_match": True,
                      "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
                      "material_or_runtime_validation": False,
                      "baseline": baseline, "candidate": candidate}, sort_keys=True))


if __name__ == "__main__":
    main()
