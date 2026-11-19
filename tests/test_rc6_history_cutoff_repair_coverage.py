"""A valid PPI payload cannot certify missing historical sessions by itself."""
import hashlib
import json
import sqlite3

from scripts import rc6_history_cutoff_repair_once as repair


class _Store:
    def __init__(self, path):
        self.path = path

    def connect(self):
        return sqlite3.connect(self.path)


def _archive_table(store):
    with store.connect() as db:
        db.execute("""CREATE TABLE historical_raw_archive(
          id INTEGER PRIMARY KEY,origin TEXT NOT NULL,row_key TEXT NOT NULL,
          body_hash TEXT NOT NULL,recorded_at TEXT NOT NULL,quality TEXT NOT NULL,
          body_json TEXT NOT NULL)""")


def _archive_attempt(store, *, body, origin="PPI_HISTORY", quality="VALID_PAYLOAD",
                     recorded_at="2026-09-22T10:00:00+00:00", valid_hash=True):
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(encoded.encode()).hexdigest() if valid_hash else "f" * 64
    with store.connect() as db:
        db.execute("""INSERT INTO historical_raw_archive
          (origin,row_key,body_hash,recorded_at,quality,body_json) VALUES(?,?,?,?,?,?)""",
          (origin, "synthetic-exact-key", digest, recorded_at, quality, encoded))


def _ppi_wrapper(currency="ARS", close=100):
    return {"symbol": "TEST", "instrument_type": "ACCIONES", "market": "BYMA",
            "settlement": "CI", "currency": currency,
            "payload_json": json.dumps([{"date": "2026-09-21", "close": close}])}


def test_legacy_raw_archive_recovers_exact_ppi_identity_when_external_manifest_absent(tmp_path, monkeypatch):
    import fb_raw_evidence_exact_v1 as exact

    store = _Store(tmp_path / "history.db")
    _archive_table(store)
    _archive_attempt(store, body=_ppi_wrapper(close=101))
    _archive_attempt(store, body=_ppi_wrapper(close=999), valid_hash=False)
    _archive_attempt(store, body=_ppi_wrapper(currency="USD", close=888))
    before = store.path.read_bytes()
    monkeypatch.setattr(repair, "_history_store", lambda: store)
    monkeypatch.setattr(exact, "manifest_path", lambda: tmp_path / "external-missing.db")
    key = ("TEST", "ACCIONES", "BYMA", "CI", "ARS", "RAW", "RAW_NO_ADJUSTMENT")

    selected = repair._archive_payloads({key})

    assert set(selected) == {key}
    assert selected[key][0] == [{"date": "2026-09-21", "close": 101}]
    assert selected[key][1] == "2026-09-22T10:00:00+00:00"
    assert store.path.read_bytes() == before


def test_legacy_archive_rejects_wrong_hash_non_ppi_origin_and_unknown_identity(tmp_path, monkeypatch):
    import fb_raw_evidence_exact_v1 as exact

    store = _Store(tmp_path / "history.db")
    _archive_table(store)
    _archive_attempt(store, body=_ppi_wrapper(), valid_hash=False)
    _archive_attempt(store, body=_ppi_wrapper(), origin="IOL_HISTORY")
    _archive_attempt(store, body={**_ppi_wrapper(), "market": "UNKNOWN"})
    _archive_attempt(store, body=_ppi_wrapper(), quality="UNVERIFIED")
    monkeypatch.setattr(repair, "_history_store", lambda: store)
    monkeypatch.setattr(exact, "manifest_path", lambda: tmp_path / "external-missing.db")
    key = ("TEST", "ACCIONES", "BYMA", "CI", "ARS", "RAW", "RAW_NO_ADJUSTMENT")

    assert repair._archive_payloads({key}) == {}


def test_legacy_archive_deduplicates_to_latest_valid_attempt(tmp_path, monkeypatch):
    import fb_raw_evidence_exact_v1 as exact

    store = _Store(tmp_path / "history.db")
    _archive_table(store)
    _archive_attempt(store, body=_ppi_wrapper(close=100), recorded_at="2026-09-21T10:00:00+00:00")
    _archive_attempt(store, body=_ppi_wrapper(close=102), recorded_at="2026-09-22T10:00:00+00:00")
    monkeypatch.setattr(repair, "_history_store", lambda: store)
    monkeypatch.setattr(exact, "manifest_path", lambda: tmp_path / "external-missing.db")
    key = ("TEST", "ACCIONES", "BYMA", "CI", "ARS", "RAW", "RAW_NO_ADJUSTMENT")

    selected = repair._archive_payloads({key})

    assert selected[key][0] == [{"date": "2026-09-21", "close": 102}]
    assert selected[key][1] == "2026-09-22T10:00:00+00:00"


def test_legacy_archive_prefers_latest_observation_time_not_insert_order(tmp_path, monkeypatch):
    import fb_raw_evidence_exact_v1 as exact

    store = _Store(tmp_path / "history.db")
    _archive_table(store)
    _archive_attempt(store, body=_ppi_wrapper(close=123), recorded_at="2026-09-23T10:00:00+00:00")
    # A backfilled older observation can have a larger SQLite id.
    _archive_attempt(store, body=_ppi_wrapper(close=111), recorded_at="2026-09-22T10:00:00+00:00")
    monkeypatch.setattr(repair, "_history_store", lambda: store)
    monkeypatch.setattr(exact, "manifest_path", lambda: tmp_path / "external-missing.db")
    key = ("TEST", "ACCIONES", "BYMA", "CI", "ARS", "RAW", "RAW_NO_ADJUSTMENT")

    selected = repair._archive_payloads({key})

    assert selected[key][0] == [{"date": "2026-09-21", "close": 123}]
    assert selected[key][1] == "2026-09-23T10:00:00+00:00"


def test_legacy_archive_scan_budget_refuses_unbounded_payload_sweep(tmp_path, monkeypatch):
    import fb_raw_evidence_exact_v1 as exact

    store = _Store(tmp_path / "history.db")
    _archive_table(store)
    _archive_attempt(store, body=_ppi_wrapper())
    monkeypatch.setattr(repair, "_history_store", lambda: store)
    monkeypatch.setattr(repair, "LEGACY_ARCHIVE_MAX_SCANNED_BYTES", 16, raising=False)
    monkeypatch.setattr(exact, "manifest_path", lambda: tmp_path / "external-missing.db")
    key = ("TEST", "ACCIONES", "BYMA", "CI", "ARS", "RAW", "RAW_NO_ADJUSTMENT")

    assert repair._archive_payloads({key}) == {}


def test_legacy_archive_scans_only_latest_physical_ids_not_all_rows_until_match(tmp_path, monkeypatch):
    import fb_raw_evidence_exact_v1 as exact

    store = _Store(tmp_path / "history.db")
    _archive_table(store)
    _archive_attempt(store, body=_ppi_wrapper(close=100))
    _archive_attempt(store, body=_ppi_wrapper(close=101), origin="IOL_HISTORY")
    _archive_attempt(store, body=_ppi_wrapper(close=102), origin="BYMA_HISTORY")
    monkeypatch.setattr(repair, "_history_store", lambda: store)
    monkeypatch.setattr(repair, "LEGACY_ARCHIVE_SCAN_LIMIT", 2)
    monkeypatch.setattr(exact, "manifest_path", lambda: tmp_path / "external-missing.db")
    key = ("TEST", "ACCIONES", "BYMA", "CI", "ARS", "RAW", "RAW_NO_ADJUSTMENT")

    assert repair._archive_payloads({key}) == {}


def test_main_replays_legacy_archive_without_login_and_records_archive_provenance(tmp_path, monkeypatch):
    import cg_paper_workspace as workspace
    import fb_raw_evidence_exact_v1 as exact

    identity = ("TEST", "ACCIONES", "BYMA", "CI", "ARS", "RAW", "RAW_NO_ADJUSTMENT")
    store = _Store(tmp_path / "history.db")
    _archive_table(store)
    _archive_attempt(store, body=_ppi_wrapper(close=105))
    monkeypatch.setattr(repair, "LOCK_PATH", tmp_path / "repair.lock")
    monkeypatch.setattr(repair, "_history_store", lambda: store)
    monkeypatch.setattr(workspace, "runtime_store", lambda: store)
    monkeypatch.setattr(exact, "manifest_path", lambda: tmp_path / "external-missing.db")
    monkeypatch.setattr(repair, "_targets", lambda _: [identity])
    monkeypatch.setattr(repair, "_latest", lambda *_: None)
    calls = []

    def coverage(*_):
        return {"complete": bool(calls), "present": len(calls),
                "latest": "2026-09-21" if calls else None,
                "missing": 0, "reason": "COVERAGE_COMPLETE" if calls else "NO_VALID_HISTORY"}

    def ingest(_, __, key, payload, start, *, source, observed_at):
        calls.append((key, payload, start.isoformat(), source, observed_at))
        return (1, "2026-09-21")

    monkeypatch.setattr(repair, "_coverage", coverage)
    monkeypatch.setattr(repair, "_ingest", ingest)
    monkeypatch.delenv("RC6_HISTORY_ALLOW_PPI_GAP_REPAIR", raising=False)
    monkeypatch.delenv("RC6_HISTORY_QUARANTINE_REPEATED_PPI", raising=False)

    assert repair.main() == 0
    assert calls == [(identity, [{"date": "2026-09-21", "close": 105}],
                      "2026-09-21", "PPI_ARCHIVE", "2026-09-22T10:00:00+00:00")]
    with store.connect() as db:
        run = db.execute("SELECT state,archive_imports,ppi_queries,complete,failed FROM rc6_history_cutoff_repair_runs").fetchone()
        exact_state = db.execute("SELECT state,source FROM rc6_history_cutoff_repair_exact_v2").fetchone()
    assert run == ("COMPLETE", 1, 0, 1, 0)
    assert exact_state == ("COMPLETE", "PPI_ARCHIVE")


def test_live_ppi_rows_with_interior_gap_remain_partial(tmp_path, monkeypatch):
    import bd_ppi_readonly_guard as guard
    import bf_production_paper_observer as observer
    import cg_paper_workspace as workspace

    identity = ("TEST", "ACCIONES", "BYMA", "CI", "ARS", "RAW", "RAW_NO_ADJUSTMENT")
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
    monkeypatch.setattr(observer, "now_iso", lambda: "2026-09-21T20:00:00+00:00")

    class Reader:
        attempts = 0

        def __init__(self, *_):
            pass

        def login_once(self):
            raise AssertionError("SECOND_PPI_LOGIN_FORBIDDEN")

        def history(self, *_):
            self.attempts += 1
            if self.attempts == 1:
                raise json.JSONDecodeError("gateway response", "<", 0)
            return [{"date": "2026-09-21"}]

        def close(self):
            raise AssertionError("SCANNER_OWNS_SHARED_SESSION")

    monkeypatch.setattr(guard, "ProductionMarketReader", lambda *_: (_ for _ in ()).throw(
        AssertionError("SECOND_PPI_CLIENT_FORBIDDEN")))
    monkeypatch.setenv("RC6_HISTORY_ALLOW_PPI_GAP_REPAIR", "APPROVED")
    monkeypatch.delenv("RC6_HISTORY_QUARANTINE_REPEATED_PPI", raising=False)
    monkeypatch.setenv("RC6_HISTORY_REQUEST_PAUSE_SECONDS", "0")
    shared = Reader()
    requested = []

    def budget(identity, date_from, date_to):
        requested.append((identity, date_from.isoformat(), date_to.isoformat()))
        return True

    assert repair.main(shared_reader=shared, reserve_history_get=budget) == 1
    with store.connect() as c:
        state = c.execute("SELECT state,source,valid_rows,error_class FROM rc6_history_cutoff_repair_exact_v2").fetchone()
        run = c.execute("SELECT state,complete,failed,ppi_queries FROM rc6_history_cutoff_repair_runs").fetchone()
    assert state == ("PPI_PARTIAL_COVERAGE", "PPI", 2, "MISSING_BYMA_SESSIONS")
    assert run == ("PARTIAL", 0, 1, 2)
    assert shared.attempts == 2
    assert requested == [(identity, "2026-09-18", "2026-09-21")] * 2


def test_gap_flag_without_scanner_reader_cannot_authenticate_or_use_ppi(tmp_path, monkeypatch, capsys):
    import bd_ppi_readonly_guard as guard
    import cg_paper_workspace as workspace

    key = ("TEST", "ACCIONES", "BYMA", "CI", "ARS", "RAW", "RAW_NO_ADJUSTMENT")
    store = _Store(tmp_path / "history.db")
    monkeypatch.setattr(repair, "LOCK_PATH", tmp_path / "repair.lock")
    monkeypatch.setattr(repair, "_history_store", lambda: store)
    monkeypatch.setattr(workspace, "runtime_store", lambda: store)
    monkeypatch.setattr(repair, "_targets", lambda _: [key])
    monkeypatch.setattr(repair, "_archive_payloads", lambda _: {})
    monkeypatch.setattr(repair, "_latest", lambda *_: None)
    monkeypatch.setattr(repair, "_coverage", lambda *_: {
        "complete": False, "first_missing": None, "present": 0,
        "latest": None, "missing": 0, "reason": "NO_VALID_HISTORY"})
    monkeypatch.setattr(guard, "ProductionMarketReader", lambda *_: (_ for _ in ()).throw(
        AssertionError("SECOND_PPI_CLIENT_FORBIDDEN")))
    monkeypatch.setenv("RC6_HISTORY_ALLOW_PPI_GAP_REPAIR", "APPROVED")
    monkeypatch.delenv("RC6_HISTORY_QUARANTINE_REPEATED_PPI", raising=False)

    assert repair.main() == 1
    with store.connect() as db:
        run = db.execute("SELECT state,ppi_queries,complete,failed FROM rc6_history_cutoff_repair_runs").fetchone()
    assert run == ("PARTIAL", 0, 0, 1)
    assert "BLOCKED_NO_SHARED_SCANNER_SESSION_OR_BUDGET" in capsys.readouterr().out


def test_shared_reader_never_performs_unreserved_history_get(tmp_path, monkeypatch):
    import cg_paper_workspace as workspace

    key = ("TEST", "ACCIONES", "BYMA", "CI", "ARS", "RAW", "RAW_NO_ADJUSTMENT")
    store = _Store(tmp_path / "history.db")
    monkeypatch.setattr(repair, "LOCK_PATH", tmp_path / "repair.lock")
    monkeypatch.setattr(repair, "_history_store", lambda: store)
    monkeypatch.setattr(workspace, "runtime_store", lambda: store)
    monkeypatch.setattr(repair, "_targets", lambda _: [key])
    monkeypatch.setattr(repair, "_archive_payloads", lambda _: {})
    monkeypatch.setattr(repair, "_latest", lambda *_: "2026-09-20")
    monkeypatch.setattr(repair, "_coverage", lambda *_: {
        "complete": False, "first_missing": "2026-09-21", "present": 1,
        "latest": "2026-09-20", "missing": 1, "reason": "CUTOFF_NOT_REACHED"})
    monkeypatch.setenv("RC6_HISTORY_ALLOW_PPI_GAP_REPAIR", "APPROVED")
    monkeypatch.delenv("RC6_HISTORY_QUARANTINE_REPEATED_PPI", raising=False)

    class SharedReader:
        def history(self, *_):
            raise AssertionError("UNRESERVED_PPI_GET_FORBIDDEN")

    calls = []

    def budget(identity, start, end):
        calls.append((identity, start.isoformat(), end.isoformat()))
        return 1  # Truthy integers are not valid budget receipts.

    assert repair.main(shared_reader=SharedReader(), reserve_history_get=budget) == 1
    with store.connect() as db:
        run = db.execute("SELECT state,ppi_queries,complete,failed FROM rc6_history_cutoff_repair_runs").fetchone()
    assert calls == [(key, "2026-09-21", "2026-09-21")]
    assert run == ("PARTIAL", 0, 0, 1)


def test_quarantine_flag_cannot_turn_unverified_cutoff_into_complete(tmp_path, monkeypatch, capsys):
    import cg_paper_workspace as workspace

    key = ("TEST", "ACCIONES", "BYMA", "CI", "ARS", "RAW", "RAW_NO_ADJUSTMENT")
    store = _Store(tmp_path / "history.db")
    with store.connect() as db:
        db.execute("""CREATE TABLE financial_instrument_catalog(
          ticker TEXT,instrument_type TEXT,market TEXT,settlement TEXT,currency TEXT,
          status TEXT,capability TEXT)""")
        db.execute("INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?,?,?)",
                   ("TEST", "ACCIONES", "BYMA", "CI", "ARS", "AVAILABLE", "HISTORY"))
    monkeypatch.setattr(repair, "LOCK_PATH", tmp_path / "repair.lock")
    monkeypatch.setattr(repair, "_history_store", lambda: store)
    monkeypatch.setattr(workspace, "runtime_store", lambda: store)
    monkeypatch.setattr(repair, "_targets", lambda _: [key])
    monkeypatch.setattr(repair, "_archive_payloads", lambda _: {})
    monkeypatch.setattr(repair, "_latest", lambda *_: None)
    monkeypatch.setattr(repair, "_coverage", lambda *_: {
        "complete": False, "first_missing": None, "present": 0,
        "latest": None, "missing": 0, "reason": "NO_VALID_HISTORY"})
    monkeypatch.delenv("RC6_HISTORY_ALLOW_PPI_GAP_REPAIR", raising=False)
    monkeypatch.setenv("RC6_HISTORY_QUARANTINE_REPEATED_PPI", "APPROVED")

    assert repair.main() == 1
    with store.connect() as db:
        run = db.execute("SELECT state,complete,failed FROM rc6_history_cutoff_repair_runs").fetchone()
        catalog = db.execute("SELECT status,capability FROM financial_instrument_catalog").fetchone()
    assert run == ("PARTIAL", 0, 1)
    assert catalog == ("AVAILABLE", "HISTORY")
    assert "BLOCKED_REPEATED_PPI_EVIDENCE_UNVERIFIED" in capsys.readouterr().out
