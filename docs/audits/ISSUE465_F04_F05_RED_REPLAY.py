"""Synthetic-only contract reproductions loaded from rejected PR #463 bytes."""
import json
import subprocess
import sys
import types

import pytest

BASE = "caf9bc94b4a1f436ad01a84a9e9e9e7a4a9e9423"


def historical_module(name, path):
    source = subprocess.check_output(["git", "show", BASE + ":" + path], text=True)
    module = types.ModuleType(name)
    module.__file__ = "git:" + BASE + ":" + path
    module.__package__ = name.rsplit(".", 1)[0]
    sys.modules[name] = module
    exec(compile(source, module.__file__, "exec"), module.__dict__)
    return module


def test_f04_nonempty_source_reports_require_nonempty_source_audit():
    historical_module("rc6_dynamic_universe.sources", "rc6_dynamic_universe/sources.py")
    live = historical_module("rc6_dynamic_universe.historical_live", "rc6_dynamic_universe/live.py")
    row = {"ticker": "S1", "instrument_type": "ACCIONES", "market": "BYMA", "currency": "ARS",
        "settlement": "A-24HS", "timestamp": "2026-10-05T13:20:00+00:00",
        "received_at": "2026-10-05T13:20:05+00:00", "price": 100, "price_unit": "PER_SHARE"}
    bundle = {"safety": {"mode": "SIMULATION", "real_orders_sent": 0, "real_routes": "NOT_CALLED"},
        "as_of": "2026-10-05T13:20:10+00:00", "session_open": "2026-10-05T13:30:00+00:00",
        "frozen_at": "2026-10-05T13:20:10+00:00", "preopen_cutoff": "2026-10-02T20:00:00+00:00",
        "sessions": ["2026-10-02"], "catalog": [row], "sources": {"PPI_API": {"records": [row]}}}
    report = live.run_shadow(bundle)
    assert report["source_reports"][0]["counts"]["seen"] == 1
    print(json.dumps({"source_report_count": len(report["source_reports"]),
        "source_audit_snapshot_count": len(report["source_audit"]["snapshots"]), "real_orders_sent": 0}))
    assert report["source_audit"]["snapshots"], "F04_RECEIVED_SOURCE_AUDIT_MUST_NOT_BE_EMPTY"


def test_f05_quota_failure_requires_explicit_pressure_metrics(tmp_path):
    module = historical_module("rc6_shadow_runtime.historical_persistence", "rc6_shadow_runtime/persistence.py")
    with module.EvidenceFiles(tmp_path) as files:
        for n in range(512):
            (tmp_path / f"synthetic-{n}.json").write_text("{}")
        with pytest.raises(ValueError, match="CAPACITY") as error:
            files.write("latest.json.gz", {"real_orders_sent": 0})
        print(json.dumps({"error": str(error.value), "has_pressure_metrics": hasattr(error.value, "metrics"),
            "synthetic_evidence_files": 512, "real_orders_sent": 0}))
        assert hasattr(error.value, "metrics"), "F05_QUOTA_FAILURE_MUST_EXPOSE_RETENTION_PRESSURE"
