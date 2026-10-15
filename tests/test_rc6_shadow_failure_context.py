"""The SHADOW fail-closed status retains bounded, non-sensitive SQLite evidence."""
import sqlite3

from be_paper_engine import PaperStore
from cg_paper_workspace import artifact_root
from rc6_shadow_runtime.persistence import EvidenceFiles
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
