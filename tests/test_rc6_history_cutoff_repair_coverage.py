"""A valid PPI payload cannot certify missing historical sessions by itself."""
import sqlite3

from scripts import rc6_history_cutoff_repair_once as repair


class _Store:
    def __init__(self, path):
        self.path = path

    def connect(self):
        return sqlite3.connect(self.path)


def test_live_ppi_rows_with_interior_gap_remain_partial(tmp_path, monkeypatch):
    import bd_ppi_readonly_guard as guard
    import bf_production_paper_observer as observer
    import cg_paper_workspace as workspace

    identity = ("TEST", "ACCIONES", "BYMA", "CI")
    store = _Store(tmp_path / "history.db")
    monkeypatch.setattr(repair, "LOCK_PATH", tmp_path / "repair.lock")
    monkeypatch.setattr(repair, "_history_store", lambda: store)
    monkeypatch.setattr(workspace, "runtime_store", lambda: store)
    monkeypatch.setattr(repair, "_targets", lambda _: [identity])
    monkeypatch.setattr(repair, "_archive_payloads", lambda _: {})
    monkeypatch.setattr(repair, "_latest", lambda *_: "2026-09-21")
    monkeypatch.setattr(repair, "_coverage", lambda *_: {
        "complete": False, "first_missing": "2026-09-18", "present": 2,
        "latest": "2026-09-21", "missing": 1, "reason": "MISSING_BYMA_SESSIONS"})
    monkeypatch.setattr(repair, "_record_exact", lambda *a: None)
    monkeypatch.setattr(repair, "_ingest", lambda *a, **k: (1, "2026-09-21"))
    monkeypatch.setattr(repair.time, "sleep", lambda _: None)
    monkeypatch.setattr(observer, "_secret", lambda: ("key", "secret"))
    monkeypatch.setattr(observer, "now_iso", lambda: "2026-09-21T20:00:00+00:00")

    class Reader:
        def __init__(self, *_):
            pass

        def login_once(self):
            pass

        def history(self, *_):
            return [{"date": "2026-09-21"}]

        def close(self):
            pass

    monkeypatch.setattr(guard, "ProductionMarketReader", Reader)
    monkeypatch.setenv("RC6_HISTORY_ALLOW_PPI_GAP_REPAIR", "APPROVED")
    monkeypatch.delenv("RC6_HISTORY_QUARANTINE_REPEATED_PPI", raising=False)
    monkeypatch.setenv("RC6_HISTORY_REQUEST_PAUSE_SECONDS", "0")

    assert repair.main() == 1
    with store.connect() as c:
        state = c.execute("SELECT state,source,valid_rows,error_class FROM rc6_history_cutoff_repair").fetchone()
        run = c.execute("SELECT state,complete,failed,ppi_queries FROM rc6_history_cutoff_repair_runs").fetchone()
    assert state == ("PPI_PARTIAL_COVERAGE", "PPI", 2, "MISSING_BYMA_SESSIONS")
    assert run == ("PARTIAL", 0, 1, 0)
