"""A668: inert inspect-only guards for the A667 REPORT_VERIFY memory failure.

All bytes are synthetic and local. Never execute a lifecycle mutator, deploy
workflow, provider request, or production file operation in these tests.
"""

import gzip
import hashlib
import os
from pathlib import Path

import pytest

from scripts import rc6_cold2_quarantine as old
from scripts import rc6_cold2_successor as successor
from tests.test_rc6_cold2_quarantine import put


IDENT = "e7f8eb9dfc954b4c99346a14ed280e4f"
CUT = "b3d10eb98e51b04c2d11039dd3fd98394348b6078c1369f99e3a1371903a07eb"
SEALED = 1792485190.2664664
MEMORY = "SHADOW_COLD_HOST_MEMORY_RESERVE_BREACHED"


@pytest.fixture(autouse=True)
def _forbid_any_quarantine_execution(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("A668 is diagnostic-only: quarantine mutators are forbidden")
    monkeypatch.setattr(successor, "quarantine", forbidden)
    monkeypatch.setattr(old, "quarantine", forbidden)


def _fixture(tmp_path: Path, *, reports=1, code=MEMORY, phase="REPORT_VERIFY",
             error_class="ValueError", fail_after=20.0, report_after=19.0,
             cut_orders=0, pack_corrupt=False, queue_bad=False):
    root = tmp_path / "host"
    root.mkdir(mode=0o700)
    shadow = root / "data/paper_v17/artifacts/observer_v17.db/dynamic-shadow"
    source = shadow / "cold-audit"
    source.mkdir(parents=True, mode=0o700)
    shadow.chmod(0o700)
    (root / "data/deploy").mkdir(parents=True, mode=0o700)

    pack = source / (IDENT + ".pack")
    raw_pack = b"synthetic immutable audit original" * 200
    pack.write_bytes(raw_pack)
    pack.chmod(0o600)
    st = pack.stat()
    manifest = {
        "epoch": "rc6.audit-actual-values.v1", "snapshot_id": IDENT,
        "pack": pack.name, "pack_bytes": st.st_size,
        "pack_sha256": hashlib.sha256(raw_pack).hexdigest(),
        "pack_identity": [st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns],
        "source_as_of": "2026-11-04T20:19:00+00:00",
        "configuration_fingerprint": "c" * 64,
        "sealed_at": SEALED, "deadline_at": SEALED + 30.0,
    }
    manifest_hash = old.digest(manifest)
    put(source / ("manifest-" + IDENT + ".json"), manifest, cold=True)
    put(source / ("receipt-" + IDENT + ".json"), {
        "state": "FAILED", "snapshot_id": IDENT,
        "manifest_sha256": manifest_hash, "error_class": error_class,
        "error_code": code, "error_phase": phase,
        "failed_at": SEALED + fail_after,
    }, cold=True)
    put(source / "queue.json", {
        "state": "FAILED", "snapshot_id": IDENT,
        "manifest_sha256": "0" * 64 if queue_bad else manifest_hash,
    }, cold=True)
    for other in ("d" * 32, "e" * 32):
        historical = {"snapshot_id": other, "role": "synthetic-complete"}
        put(source / ("manifest-" + other + ".json"), historical, cold=True)
        put(source / ("receipt-" + other + ".json"), {
            "state": "COMPLETE", "snapshot_id": other,
            "manifest_sha256": old.digest(historical),
        }, cold=True)
    put(shadow / "CURRENT.json", {
        "generation_id": CUT, "file": "cut-" + CUT + ".json.gz",
    })
    put(shadow / ("cut-" + CUT + ".json.gz"), {
        "report": {"computed_report": {
            "schema": "rc6.computed-audit-reference.v2", "state": "PENDING",
            "snapshot_id": IDENT, "manifest_sha256": manifest_hash,
            "deadline_at": SEALED + 30.0,
            "source_as_of": manifest["source_as_of"],
            "configuration_fingerprint": manifest["configuration_fingerprint"],
        }},
        "status": {"real_orders_sent": cut_orders, "real_routes": "NOT_CALLED"},
    }, zipped=True)
    put(shadow / "status.json", {
        "status": "FAIL_CLOSED", "real_orders_sent": 0,
        "real_routes": "NOT_CALLED",
    })
    for index in range(reports):
        raw_report = ("synthetic failed report " + str(index)).encode() * 300
        name = "computed-" + hashlib.sha256(raw_report).hexdigest() + ".json.gz"
        report = shadow / name
        report.write_bytes(gzip.compress(raw_report))
        report.chmod(0o600)
        os.utime(report, (SEALED + report_after, SEALED + report_after))
    if pack_corrupt:
        pack.write_bytes(b"corrupt pack after manifest hash")
    return root, source


def _bytes(root: Path):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*") if path.is_file()}


def test_a668_exact_memory_report_verify_can_be_inspected_without_mutations(tmp_path):
    root, source = _fixture(tmp_path)
    before = _bytes(root)
    value = successor.inspect(root)
    assert value["snapshot_id"] == IDENT
    assert value["queue_failed"] and not value["archive_complete"]
    assert value["failed_computed_report"]["name"].startswith("computed-")
    computed_bytes = (source.parent / value["failed_computed_report"]["name"]).read_bytes()
    assert value["failed_computed_report"]["sha256"] == hashlib.sha256(computed_bytes).hexdigest()
    assert _bytes(root) == before
    assert (source / "queue.json").is_file()
    assert not (root / "data/deploy" / ("rc6-failed-cold-" + IDENT)).exists()


@pytest.mark.parametrize("invalid", ["missing_report", "ambiguous_report",
    "stale_report", "unknown_phase", "wrong_error_code", "wrong_class",
    "late_failed_at", "real_order_cut", "corrupt_pack", "queue_mismatch"])
def test_a668_memory_verify_must_reject_weak_evidence_and_preserve_queue(tmp_path, invalid):
    options = {}
    if invalid == "missing_report":
        options["reports"] = 0
    elif invalid == "ambiguous_report":
        options["reports"] = 2
    elif invalid == "stale_report":
        options["report_after"] = -80.0
    elif invalid == "unknown_phase":
        options["phase"] = "REPORT_WRITE"
    elif invalid == "wrong_error_code":
        options["code"] = "UNCLASSIFIED"
    elif invalid == "wrong_class":
        options["error_class"] = "RuntimeError"
    elif invalid == "late_failed_at":
        options["fail_after"] = 38.0
    elif invalid == "real_order_cut":
        options["cut_orders"] = 1
    elif invalid == "corrupt_pack":
        options["pack_corrupt"] = True
    else:
        options["queue_bad"] = True
    root, source = _fixture(tmp_path, **options)
    before = _bytes(root)
    with pytest.raises((ValueError, FileNotFoundError)):
        successor.inspect(root)
    assert _bytes(root) == before
    assert (source / "queue.json").is_file()
    assert not (root / "data/deploy" / ("rc6-failed-cold-" + IDENT)).exists()


def test_a668_source_hard_gate_keeps_memory_report_write_excluded():
    text = Path(successor.__file__).read_text()
    assert "('SHADOW_COLD_HOST_MEMORY_RESERVE_BREACHED', 'REPORT_VERIFY')" in text
    assert "('SHADOW_COLD_HOST_MEMORY_RESERVE_BREACHED', 'REPORT_WRITE')" not in text
    assert "_failed_report(shadow, archive" in text
