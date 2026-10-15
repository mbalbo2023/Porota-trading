"""The SHADOW fail-closed status retains bounded, non-sensitive SQLite evidence."""
import sqlite3

from be_paper_engine import PaperStore
from cg_paper_workspace import artifact_root
from rc6_shadow_runtime.persistence import EvidenceFiles, EvidencePayloadLimit
import rc6_shadow_runtime.worker as shadow


class OneTick:
    def __init__(self):
        self.stopped = False

    def is_set(self):
        return self.stopped

    def wait(self, _seconds):
        self.stopped = True


def test_shadow_sqlite_interrupt_stays_closed_with_bounded_location(tmp_path, monkeypatch):
    store = PaperStore(str(tmp_path / "paper.db"))
    connection = sqlite3.connect(":memory:")
    connection.set_progress_handler(lambda: 1, 1)
    try:
        connection.execute("SELECT 1")
    except sqlite3.OperationalError as exc:
        interrupted = exc
    else:
        raise AssertionError("SQLite progress handler did not interrupt")
    finally:
        connection.close()
    interrupted.rc6_stage = "CATALOG_FULL"
    interrupted.rc6_deadline_expired = True

    monkeypatch.setattr(shadow.os, "nice", lambda *_: None)

    def fail(*_args, **_kwargs):
        raise interrupted

    monkeypatch.setattr(shadow.ShadowRuntime, "tick", fail)
    shadow.run_worker(store.path, OneTick(), clock_fn=lambda: "2026-10-14T22:00:00+00:00")
    root = artifact_root(store.path) / "dynamic-shadow"
    with EvidenceFiles(root) as files:
        status = files.read("status.json")
    assert status["status"] == "FAIL_CLOSED"
    assert status["sqlite_errorname"] == "SQLITE_INTERRUPT"
    assert status["runtime_read_stage"] == "CATALOG_FULL"
    assert status["runtime_deadline_expired"] is True
    assert status["error_module"] == "worker" and status["error_line"] > 0
    assert status["real_orders_sent"] == 0 and status["real_routes"] == "NOT_CALLED"
    assert "interrupted" not in str(status).lower()

def test_shadow_value_error_records_only_allowlisted_site(tmp_path, monkeypatch):
    store = PaperStore(str(tmp_path / "paper.db"))
    monkeypatch.setattr(shadow.os, "nice", lambda *_: None)

    def fail(*_args, **_kwargs):
        raise ValueError("private-token=DO_NOT_PUBLISH")

    monkeypatch.setattr(shadow.ShadowRuntime, "tick", fail)
    shadow.run_worker(store.path, OneTick(), clock_fn=lambda: "2026-10-15T01:00:00+00:00")
    root = artifact_root(store.path) / "dynamic-shadow"
    with EvidenceFiles(root) as files:
        status = files.read("status.json")
    assert status["status"] == "FAIL_CLOSED"
    assert status["error_class"] == "ValueError"
    assert status["error_module"] == "worker" and status["error_line"] > 0
    assert status["real_orders_sent"] == 0 and status["real_routes"] == "NOT_CALLED"
    assert "private-token" not in str(status).lower()
    assert "DO_NOT_PUBLISH" not in str(status)


def test_shadow_payload_limit_reports_only_bounded_file_and_sizes(tmp_path, monkeypatch):
    store = PaperStore(str(tmp_path / "paper.db"))
    with EvidenceFiles(tmp_path / "small", payload_limit=128) as files:
        try:
            files.write("latest.json.gz", {"secret": "DO_NOT_PUBLISH" * 12})
        except EvidencePayloadLimit as exc:
            assert exc.evidence_file == "latest.json.gz"
            assert exc.measured_bytes > exc.limit_bytes == 128
            assert not (tmp_path / "small" / "latest.json.gz").exists()
        else:
            raise AssertionError("Payload should exceed the bounded evidence limit")

    monkeypatch.setattr(shadow.os, "nice", lambda *_: None)
    def fail(*_args, **_kwargs):
        raise EvidencePayloadLimit("preopen-private-token-DO_NOT_PUBLISH.json.gz", 70 * 1024**2, 64 * 1024**2)
    monkeypatch.setattr(shadow.ShadowRuntime, "tick", fail)
    shadow.run_worker(store.path, OneTick(), clock_fn=lambda: "2026-10-15T03:00:00+00:00")
    with EvidenceFiles(artifact_root(store.path) / "dynamic-shadow") as files:
        status = files.read("status.json")
    assert status["status"] == "FAIL_CLOSED" and status["error_class"] == "EvidencePayloadLimit"
    assert status["evidence_file"] == "preopen"
    assert status["evidence_measured_bytes"] == 70 * 1024**2
    assert status["evidence_limit_bytes"] == 64 * 1024**2
    assert status["real_orders_sent"] == 0 and status["real_routes"] == "NOT_CALLED"
    assert "DO_NOT_PUBLISH" not in str(status)
