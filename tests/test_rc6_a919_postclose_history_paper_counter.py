"""A919: postclose history must not mistake missing PAPER evidence for zero.

Synthetic SQLite and fake reader only. No scheduler, PPI or host access.
"""

from datetime import datetime
import json
import sqlite3

import pytest

import rc6_postclose_history_job as job


class _FixedClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 11, 6, 19, 0, tzinfo=job.TZ)


class _Store:
    def __init__(self, path):
        self.path = path

    def connect(self):
        return sqlite3.connect(self.path)

    def audit_http(self, *_args, **_kwargs):
        raise AssertionError("no actual HTTP in A919 synthetic test")


def _db(tmp_path, counter):
    path = tmp_path / "synthetic-postclose.db"
    # BLOB affinity preserves deliberately malformed storage types for a
    # safety-boundary test; production SQLite commonly uses INTEGER.
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE observer_state(id INTEGER PRIMARY KEY, mode TEXT, "
                   "session_state TEXT, real_orders_sent BLOB)")
        db.execute("INSERT INTO observer_state VALUES(1,?,?,?)",
                   ("PRODUCTION_PAPER", "MARKET_CLOSED", counter))
        db.execute("CREATE TABLE production_history_attempts(attempted_at TEXT, state TEXT)")
        db.execute("CREATE TABLE source_sync(source TEXT, status TEXT)")
        db.execute("INSERT INTO source_sync VALUES('PPI_PRODUCTION_HISTORY','VERDE')")
    return _Store(path)


def _base(monkeypatch, store):
    monkeypatch.setenv("RC6_POSTCLOSE_HISTORY_RECOVERY", "APPROVED")
    monkeypatch.setattr(job, "datetime", _FixedClock)
    monkeypatch.setattr(job.byma, "es_dia_habil_operativo", lambda _day: True)
    monkeypatch.setattr(job, "runtime_store", lambda: store)
    monkeypatch.setattr(job, "_support_schema", lambda _store: None)
    monkeypatch.setattr(job, "ProductionMarketReader", lambda *_a, **_kw:
                        pytest.fail("unauthorized PPI client attempted"))
    monkeypatch.setattr(job, "_download_histories", lambda *_a, **_kw:
                        pytest.fail("unauthorized history download attempted"))


@pytest.mark.parametrize("invalid", [None, "0", "oops", -1, 1])
def test_preflight_rejects_unverified_paper_count_before_cutoff_or_provider(
        monkeypatch, tmp_path, capsys, invalid):
    store = _db(tmp_path, invalid)
    _base(monkeypatch, store)
    cutoff = []
    monkeypatch.setattr(job, "_history_cutoff_repair_complete",
                        lambda: cutoff.append("called") or False)

    assert job.main() == 2
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["status"] == "BLOCKED"
    assert receipt["reason"] == "RUNTIME_NOT_CLOSED_PAPER_SAFE"
    assert receipt.get("history_writes_started") is not True
    assert cutoff == [], "counter is checked before any cutoff or provider work"


def test_boolean_false_runtime_row_is_not_integer_zero(monkeypatch, capsys):
    """Adversarial Python DB adapter may expose bool without SQLite affinity."""
    class _Reader:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, sql):
            assert "observer_state" in sql
            return self

        def fetchone(self):
            return ("PRODUCTION_PAPER", "MARKET_CLOSED", False)

    class _FalseCountStore:
        def connect(self):
            return _Reader()

    _base(monkeypatch, _FalseCountStore())
    monkeypatch.setattr(job, "_history_cutoff_repair_complete",
                        lambda: pytest.fail("counter false reached cutoff"))
    assert job.main() == 2
    assert json.loads(capsys.readouterr().out)["reason"] == "RUNTIME_NOT_CLOSED_PAPER_SAFE"


def test_exact_integer_zero_does_not_override_existing_history_quarantine(
        monkeypatch, tmp_path, capsys):
    store = _db(tmp_path, 0)
    _base(monkeypatch, store)
    monkeypatch.setattr(job, "_history_cutoff_repair_complete", lambda: False)

    assert job.main() == 2
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["reason"] == "HISTORY_CUTOFF_REPAIR_INCOMPLETE"
    assert receipt["status"] == "BLOCKED"


@pytest.mark.parametrize("after_download", [None, "0", "oops", -1, 1, 0, "DELETE_ROW"])
def test_postread_counter_never_reports_false_green_after_synthetic_download(
        monkeypatch, tmp_path, capsys, after_download):
    store = _db(tmp_path, 0)
    _base(monkeypatch, store)
    monkeypatch.setattr(job, "_history_cutoff_repair_complete", lambda: True)
    monkeypatch.setattr(job, "init_history_schema", lambda _store: None)
    monkeypatch.setattr(job, "default_history_store", lambda: object())
    monkeypatch.setattr(job, "_secret", lambda: ("OFFLINE", "OFFLINE"))

    calls = []
    class _FakeReader:
        def __init__(self, *_args, **_kwargs):
            pass

        def login_once(self):
            calls.append("offline_login")

        def close(self):
            calls.append("close")

    def _fake_download(_reader, _store, *, incremental_only):
        assert incremental_only is True
        calls.append("synthetic_only")
        with store.connect() as db:
            if after_download == "DELETE_ROW":
                db.execute("DELETE FROM observer_state WHERE id=1")
            else:
                db.execute("UPDATE observer_state SET real_orders_sent=? WHERE id=1",
                           (after_download,))

    monkeypatch.setattr(job, "ProductionMarketReader", _FakeReader)
    monkeypatch.setattr(job, "_download_histories", _fake_download)
    expected_green = type(after_download) is int and after_download == 0

    result = job.main()
    receipt = json.loads(capsys.readouterr().out)
    assert (result == 0) is expected_green
    assert (receipt["status"] == "GREEN") is expected_green
    if expected_green:
        assert receipt["real_orders_sent"] == 0
    elif type(after_download) is int and after_download != 0:
        assert receipt["real_orders_sent"] == after_download
    else:
        assert receipt["real_orders_sent"] is None
    assert calls == ["offline_login", "synthetic_only", "close"]


@pytest.mark.parametrize("field,after_download", [
    ("mode", "PRODUCTION_REAL"),
    ("mode", None),
    ("mode", "PRODUCTION_PAPER "),
    ("session_state", "MARKET_OPEN"),
    ("session_state", None),
    ("session_state", "MARKET_CLOSED "),
])
def test_postread_paper_mode_and_session_drift_are_red_despite_integer_zero(
        monkeypatch, tmp_path, capsys, field, after_download):
    """A post-download report must attest PAPER/CLOSED at its final SQLite read."""
    store = _db(tmp_path, 0)
    _base(monkeypatch, store)
    monkeypatch.setattr(job, "_history_cutoff_repair_complete", lambda: True)
    monkeypatch.setattr(job, "init_history_schema", lambda _store: None)
    monkeypatch.setattr(job, "default_history_store", lambda: object())
    monkeypatch.setattr(job, "_secret", lambda: ("OFFLINE", "OFFLINE"))

    class _FakeReader:
        def __init__(self, *_args, **_kwargs):
            pass

        def login_once(self):
            pass

        def close(self):
            pass

    def _synthetic_download(_reader, _store, *, incremental_only):
        assert incremental_only is True
        assert field in ("mode", "session_state")
        with store.connect() as db:
            db.execute(f"UPDATE observer_state SET {field}=? WHERE id=1",
                       (after_download,))

    monkeypatch.setattr(job, "ProductionMarketReader", _FakeReader)
    monkeypatch.setattr(job, "_download_histories", _synthetic_download)

    assert job.main() == 2
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["status"] == "RED"
    assert receipt["real_orders_sent"] == 0
    assert receipt["source_sync_state"] == "VERDE"


def test_default_quarantine_remains_prior_to_sqlite_or_provider(monkeypatch, capsys):
    monkeypatch.delenv("RC6_POSTCLOSE_HISTORY_RECOVERY", raising=False)
    monkeypatch.setattr(job, "runtime_store", lambda: pytest.fail("DB opened under quarantine"))
    monkeypatch.setattr(job, "ProductionMarketReader", lambda *_a, **_k:
                        pytest.fail("PPI opened under quarantine"))
    assert job.main() == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["reason"] == "RC6_HISTORY_QUARANTINE"
    assert receipt["status"] == "BLOCKED_BY_POLICY"
