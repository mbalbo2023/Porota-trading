"""A valid PPI payload cannot certify missing historical sessions by itself."""
import hashlib
import json
import sqlite3
import tracemalloc
from datetime import timedelta
import pytest

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


def _canonical_table(store):
    with store.connect() as db:
        db.execute("""CREATE TABLE history_canonical_v2(
          symbol TEXT,instrument_type TEXT,market TEXT,settlement TEXT,currency TEXT,
          price_basis TEXT,adjustment_basis TEXT,date TEXT,source TEXT NOT NULL,
          adjusted INTEGER NOT NULL,metadata_json TEXT NOT NULL)""")


def _canonical_row(store, identity, *, source="PPI_PRODUCTION_HISTORY",
                   provider_contract_id=None, day="2026-09-21"):
    with store.connect() as db:
        db.execute("INSERT INTO history_canonical_v2 VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                   (*identity, day, source, 0, json.dumps({
                       "history_quality": "FULL_OHLCV",
                       "provider_contract_id": provider_contract_id,
                   })))


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
            "settlement": "CI", "currency": currency, "price_basis": "RAW",
            "adjustment_basis": "RAW_NO_ADJUSTMENT", "adjusted": False,
            "payload_json": json.dumps([{"date": "2026-09-21", "close": close}])}


def _external_attempt(tmp_path, monkeypatch, *, close=100, recorded_at="2026-09-22T10:00:00+00:00",
                      row_key="one", quality="VALID_PAYLOAD", changes=None):
    import fb_raw_evidence_exact_v1 as exact

    monkeypatch.setenv("PPI_HISTORY_RAW_STORAGE_MODE", "EXTERNAL_EXACT_V1")
    monkeypatch.setenv("PPI_HISTORY_EVIDENCE_ROOT", str(tmp_path / "exact"))
    monkeypatch.setattr(repair, "_history_store", lambda: _Store(tmp_path / "no-legacy.db"))
    wrapper = {**_ppi_wrapper(close=close), "price_basis": "RAW",
               "adjustment_basis": "RAW_NO_ADJUSTMENT",
               "authority": "RAW_PROVIDER_EVIDENCE_ONLY"}
    wrapper.update(changes or {})
    exact.archive_wrapper(row_key=row_key, wrapper=wrapper, recorded_at=recorded_at,
                          quality=quality)
    return exact.manifest_path()


def _target():
    return ("TEST", "ACCIONES", "BYMA", "CI", "ARS", "RAW", "RAW_NO_ADJUSTMENT")


def test_external_manifest_selects_only_exact_verified_raw_object(tmp_path, monkeypatch):
    _external_attempt(tmp_path, monkeypatch, close=109)

    selected = repair._archive_payloads({_target()})

    assert selected == {_target(): ([{"date": "2026-09-21", "close": 109}],
                                    "2026-09-22T10:00:00+00:00")}


@pytest.mark.parametrize("field,replacement", [
    ("body_hash", "f" * 64),
    ("body_sha256", "f" * 64),
    ("body_bytes", 1),
])
def test_external_manifest_rejects_corrupted_full_body_commitment(
        tmp_path, monkeypatch, field, replacement):
    manifest = _external_attempt(tmp_path, monkeypatch)
    with sqlite3.connect(manifest) as db:
        db.execute(f"UPDATE exact_attempts_v1 SET {field}=?", (replacement,))

    assert repair._archive_payloads({_target()}) == {}


@pytest.mark.parametrize("changes", [
    {"price_basis": "ADJUSTED"},
    {"adjustment_basis": "SPLIT_ADJUSTED"},
    {"price_basis": None},
    {"market": None, "metadata": {"market": "BYMA"}},
    {"currency": "ARS", "metadata": {"currency": "USD"}},
    {"asset_class": "CEDEARS"},
    {"metadata": {"adjustment_basis": "SPLIT_ADJUSTED"}},
    {"authority": "PROMOTED_FULL_OHLC"},
    {"adjusted": True},
])
def test_external_manifest_cannot_infer_raw_basis_or_qualified_identity(
        tmp_path, monkeypatch, changes):
    _external_attempt(tmp_path, monkeypatch, changes=changes)

    assert repair._archive_payloads({_target()}) == {}


def test_external_manifest_rejects_future_payload_observed_before_cutoff(tmp_path, monkeypatch):
    _external_attempt(tmp_path, monkeypatch, recorded_at="2026-09-20T10:00:00+00:00")

    assert repair._archive_payloads({_target()}) == {}


def test_external_manifest_unvalidated_scanner_capture_cannot_be_salvaged(
        tmp_path, monkeypatch):
    _external_attempt(tmp_path, monkeypatch, quality="UNVALIDATED")

    assert repair._archive_payloads({_target()}) == {}


def test_external_manifest_rejects_oversized_gzip_inner_even_when_compressed_small(
        tmp_path, monkeypatch):
    bars = [{"date": "2026-09-21", "close": 100,
             "opaque": "x" * (repair.LEGACY_ARCHIVE_MAX_BODY_BYTES + 1)}]
    _external_attempt(tmp_path, monkeypatch, changes={"payload_json": json.dumps(bars)})

    assert repair._archive_payloads({_target()}) == {}


def test_external_manifest_rejects_deeply_nested_payload_without_crashing_run(
        tmp_path, monkeypatch):
    _external_attempt(tmp_path, monkeypatch, changes={
        "payload_json": "[" * 10000 + "0" + "]" * 10000})

    assert repair._archive_payloads({_target()}) == {}


def test_existing_manifest_with_missing_table_fails_closed(tmp_path, monkeypatch):
    import fb_raw_evidence_exact_v1 as exact

    monkeypatch.setenv("PPI_HISTORY_EVIDENCE_ROOT", str(tmp_path / "corrupt-manifest"))
    monkeypatch.setattr(repair, "_history_store", lambda: _Store(tmp_path / "no-legacy.db"))
    manifest = exact.manifest_path()
    manifest.parent.mkdir(parents=True)
    with sqlite3.connect(manifest) as db:
        db.execute("CREATE TABLE unrelated(value TEXT)")

    assert repair._archive_payloads({_target()}) == {}


def test_external_manifest_scans_latest_physical_attempts_only(tmp_path, monkeypatch):
    _external_attempt(tmp_path, monkeypatch, close=109, row_key="old-valid")
    _external_attempt(tmp_path, monkeypatch, close=999, row_key="new-unvalidated",
                      quality="UNVALIDATED")
    monkeypatch.setattr(repair, "LEGACY_ARCHIVE_SCAN_LIMIT", 1)

    assert repair._archive_payloads({_target()}) == {}


def test_external_manifest_uses_actual_utc_observation_time_not_string_order(
        tmp_path, monkeypatch):
    _external_attempt(tmp_path, monkeypatch, close=401, row_key="newer",
                      recorded_at="2026-09-23T00:00:00-04:00")
    _external_attempt(tmp_path, monkeypatch, close=301, row_key="older",
                      recorded_at="2026-09-23T08:00:00+05:00")

    selected = repair._archive_payloads({_target()})

    assert selected[_target()][0] == [{"date": "2026-09-21", "close": 401}]
    assert selected[_target()][1] == "2026-09-23T00:00:00-04:00"


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


@pytest.mark.parametrize("changes", [
    {"price_basis": "ADJUSTED"},
    {"adjustment_basis": "SPLIT_ADJUSTED"},
    {"adjusted": True},
    {"price_basis": None},
    {"market": None, "metadata": {"market": "BYMA"}},
    {"currency": "ARS", "metadata": {"currency": "USD"}},
    {"asset_class": "CEDEARS"},
])
def test_legacy_archive_cannot_ascribe_ambiguous_or_adjusted_bars_to_raw(
        tmp_path, monkeypatch, changes):
    import fb_raw_evidence_exact_v1 as exact

    store = _Store(tmp_path / "legacy.db")
    _archive_table(store)
    _archive_attempt(store, body={**_ppi_wrapper(), **changes})
    monkeypatch.setattr(repair, "_history_store", lambda: store)
    monkeypatch.setattr(exact, "manifest_path", lambda: tmp_path / "external-missing.db")

    assert repair._archive_payloads({_target()}) == {}


def test_legacy_archive_refuses_payload_observed_before_its_market_date(tmp_path, monkeypatch):
    import fb_raw_evidence_exact_v1 as exact

    store = _Store(tmp_path / "legacy.db")
    _archive_table(store)
    _archive_attempt(store, body=_ppi_wrapper(), recorded_at="2026-09-20T10:00:00+00:00")
    monkeypatch.setattr(repair, "_history_store", lambda: store)
    monkeypatch.setattr(exact, "manifest_path", lambda: tmp_path / "external-missing.db")

    assert repair._archive_payloads({_target()}) == {}


def test_legacy_archive_missing_required_columns_fails_closed(tmp_path, monkeypatch):
    import fb_raw_evidence_exact_v1 as exact

    store = _Store(tmp_path / "legacy-partial.db")
    with store.connect() as db:
        db.execute("CREATE TABLE historical_raw_archive(id INTEGER PRIMARY KEY,origin TEXT)")
        db.execute("INSERT INTO historical_raw_archive VALUES(1,'PPI_HISTORY')")
    monkeypatch.setattr(repair, "_history_store", lambda: store)
    monkeypatch.setattr(exact, "manifest_path", lambda: tmp_path / "external-missing.db")

    assert repair._archive_payloads({_target()}) == {}


def test_legacy_archive_does_not_materialize_oversized_body_before_guard(
        tmp_path, monkeypatch):
    import fb_raw_evidence_exact_v1 as exact

    store = _Store(tmp_path / "huge-legacy.db")
    _archive_table(store)
    with store.connect() as db:
        db.execute("""INSERT INTO historical_raw_archive
          (origin,row_key,body_hash,recorded_at,quality,body_json)
          VALUES('PPI_HISTORY','huge','unused','2026-09-22T10:00:00+00:00',
                 'VALID_PAYLOAD',zeroblob(?))""", (8 * 1024 * 1024,))
    monkeypatch.setattr(repair, "_history_store", lambda: store)
    monkeypatch.setattr(exact, "manifest_path", lambda: tmp_path / "external-missing.db")

    tracemalloc.start()
    try:
        assert repair._archive_payloads({_target()}) == {}
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 4 * 1024 * 1024  # SQLite must project NULL for the 8 MiB BLOB.


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


def test_missing_legacy_archive_db_is_never_created_by_readonly_fallback(tmp_path, monkeypatch):
    import fb_raw_evidence_exact_v1 as exact

    store = _Store(tmp_path / "history-not-created.db")
    monkeypatch.setattr(repair, "_history_store", lambda: store)
    monkeypatch.setattr(exact, "manifest_path", lambda: tmp_path / "external-missing.db")
    key = ("TEST", "ACCIONES", "BYMA", "CI", "ARS", "RAW", "RAW_NO_ADJUSTMENT")

    assert repair._archive_payloads({key}) == {}
    assert not store.path.exists()


@pytest.mark.parametrize("preexisting_cutoff_row", [False, True])
def test_main_replays_legacy_archive_without_login_and_records_archive_provenance(
        tmp_path, monkeypatch, preexisting_cutoff_row):
    import cg_paper_workspace as workspace
    import fb_raw_evidence_exact_v1 as exact

    identity = ("TEST", "ACCIONES", "BYMA", "CI", "ARS", "RAW", "RAW_NO_ADJUSTMENT")
    store = _Store(tmp_path / "history.db")
    _archive_table(store)
    _archive_attempt(store, body=_ppi_wrapper(close=105))
    _canonical_table(store)
    if preexisting_cutoff_row:
        _canonical_row(store, identity)
    monkeypatch.setattr(repair, "LOCK_PATH", tmp_path / "repair.lock")
    monkeypatch.setattr(repair, "_history_store", lambda: store)
    monkeypatch.setattr(workspace, "runtime_store", lambda: store)
    monkeypatch.setattr(exact, "manifest_path", lambda: tmp_path / "external-missing.db")
    monkeypatch.setattr(repair, "_targets", lambda _: [identity])
    monkeypatch.setattr(repair, "_latest", lambda *_: "2026-09-21" if preexisting_cutoff_row else None)
    calls = []

    def ingest(_, __, key, payload, start, *, source, observed_at):
        calls.append((key, payload, start.isoformat(), source, observed_at))
        _canonical_row(store, key)
        return (1, "2026-09-21")

    monkeypatch.setattr(repair, "_ingest", ingest)
    monkeypatch.delenv("RC6_HISTORY_ALLOW_PPI_GAP_REPAIR", raising=False)
    monkeypatch.delenv("RC6_HISTORY_QUARANTINE_REPEATED_PPI", raising=False)

    assert repair.main() == 1
    assert calls == [(identity, [{"date": "2026-09-21", "close": 105}],
                      (repair.BOOTSTRAP_START.isoformat() if preexisting_cutoff_row else "2026-09-21"),
                      "PPI_ARCHIVE", "2026-09-22T10:00:00+00:00")]
    with store.connect() as db:
        run = db.execute("SELECT state,archive_imports,ppi_queries,complete,failed FROM rc6_history_cutoff_repair_runs").fetchone()
        exact_state = db.execute("SELECT state,source FROM rc6_history_cutoff_repair_exact_v2").fetchone()
    assert run == ("PARTIAL", 1, 0, 0, 1)
    assert exact_state == ("ARCHIVE_PARTIAL_COVERAGE", "PPI_ARCHIVE")


def test_one_cutoff_day_does_not_prove_required_history_baseline(tmp_path):
    identity = ("TEST", "ACCIONES", "BYMA", "CI", "ARS", "RAW", "RAW_NO_ADJUSTMENT")
    store = _Store(tmp_path / "history.db")
    _canonical_table(store)
    _canonical_row(store, identity)

    coverage = repair._coverage(store, identity)

    assert coverage["complete"] is False
    assert coverage["latest"].isoformat() == "2026-09-21"
    assert coverage["reason"] == "BASELINE_CALENDAR_UNAUDITED"
    assert coverage["first_missing"] is None  # Unknown, not an invented 2025 BYMA session.
    assert coverage["repair_start"] == repair.BOOTSTRAP_START.isoformat()


def test_untrusted_iol_canonical_rows_cannot_certify_ppi_cutoff(tmp_path, monkeypatch):
    identity = ("TEST", "ACCIONES", "BYMA", "CI", "ARS", "RAW", "RAW_NO_ADJUSTMENT")
    store = _Store(tmp_path / "history.db")
    _canonical_table(store)
    _canonical_row(store, identity, source="IOL_HISTORY", provider_contract_id="test-contract")
    monkeypatch.setattr(repair.byma, "ANIOS_AUDITADOS", frozenset({2025, 2026}))

    coverage = repair._coverage(store, identity)

    assert coverage["complete"] is False
    assert coverage["reason"] == "NON_PPI_CANONICAL_HISTORY"


def test_unattested_ppi_canonical_rows_cannot_certify_cutoff(tmp_path, monkeypatch):
    identity = ("TEST", "ACCIONES", "BYMA", "CI", "ARS", "RAW", "RAW_NO_ADJUSTMENT")
    store = _Store(tmp_path / "history.db")
    _canonical_table(store)
    _canonical_row(store, identity)
    monkeypatch.setattr(repair.byma, "ANIOS_AUDITADOS", frozenset({2025, 2026}))

    coverage = repair._coverage(store, identity)

    assert coverage["complete"] is False
    assert coverage["reason"] == "PPI_PROVIDER_CONTRACT_UNVERIFIED"


def test_cutoff_coverage_requires_every_audited_day_and_pinned_ppi_contract(tmp_path, monkeypatch):
    import ct_ppi_history_salvage_hf6 as salvage

    identity = ("TEST", "ACCIONES", "BYMA", "CI", "ARS", "RAW", "RAW_NO_ADJUSTMENT")
    store = _Store(tmp_path / "history.db")
    _canonical_table(store)
    # Hypothetical future audit + contract; NOT today's physical provenance.
    monkeypatch.setattr(repair.byma, "ANIOS_AUDITADOS", frozenset({2025, 2026}))
    monkeypatch.setattr(salvage, "VERIFIED_PPI_HISTORY_CONTRACTS", frozenset({"fixture-pinned"}))
    current = repair.BOOTSTRAP_START
    expected = []
    while current <= repair.CUTOFF:
        if repair.byma.es_dia_habil_operativo(current):
            _canonical_row(store, identity, provider_contract_id="fixture-pinned",
                           day=current.isoformat())
            expected.append(current)
        current += timedelta(days=1)

    coverage = repair._coverage(store, identity)
    assert coverage["complete"] is True
    assert coverage["expected"] == coverage["present"] == len(expected)

    missing = expected[len(expected) // 2]
    with store.connect() as db:
        db.execute("DELETE FROM history_canonical_v2 WHERE date=?", (missing.isoformat(),))
    gap = repair._coverage(store, identity)
    assert gap["complete"] is False
    assert gap["missing"] == 1
    assert gap["first_missing"] == missing.isoformat()


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


def test_external_manifest_refuses_shadowed_rowid_before_payload_read(tmp_path, monkeypatch):
    manifest = _external_attempt(tmp_path, monkeypatch)
    with sqlite3.connect(manifest) as db:
        db.execute("ALTER TABLE exact_attempts_v1 ADD COLUMN rowid TEXT")
        db.execute("UPDATE exact_attempts_v1 SET rowid='1'")
    assert repair._archive_payloads({_target()}) == {}


def test_external_manifest_bounded_recorded_at_metadata(tmp_path, monkeypatch):
    manifest = _external_attempt(tmp_path, monkeypatch)
    with sqlite3.connect(manifest) as db:
        db.execute("UPDATE exact_attempts_v1 SET recorded_at=zeroblob(?)",
                   (8 * 1024 * 1024,))
    tracemalloc.start()
    try:
        assert repair._archive_payloads({_target()}) == {}
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 4 * 1024 * 1024


def test_history_coverage_rejects_more_than_bounded_rows(tmp_path):
    store = _Store(tmp_path / "bounded-coverage.db")
    _canonical_table(store)
    record = (*_target(), repair.CUTOFF.isoformat(), "PPI_PRODUCTION_HISTORY",
              0, '{"history_quality":"FULL_OHLCV"}')
    with store.connect() as db:
        db.executemany("INSERT INTO history_canonical_v2 VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                       [record] * (repair.LEGACY_ARCHIVE_SCAN_LIMIT + 1))
    coverage = repair._coverage(store, _target())
    assert coverage["complete"] is False
    assert coverage["reason"] == "CANONICAL_SQL_ROW_BUDGET_EXCEEDED"
    assert coverage["repair_start"] == repair.BOOTSTRAP_START.isoformat()


def test_history_coverage_restricts_prebootstrap_dates_in_sql(tmp_path):
    store = _Store(tmp_path / "windowed-coverage.db")
    _canonical_table(store)
    past = (*_target(), "2024-01-02", "PPI_PRODUCTION_HISTORY",
            0, '{"history_quality":"FULL_OHLCV"}')
    current = (*_target(), repair.CUTOFF.isoformat(), "PPI_PRODUCTION_HISTORY",
               0, '{"history_quality":"FULL_OHLCV"}')
    with store.connect() as db:
        db.executemany("INSERT INTO history_canonical_v2 VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                       [past] * (repair.LEGACY_ARCHIVE_SCAN_LIMIT + 1) + [current])
    coverage = repair._coverage(store, _target())
    assert coverage["complete"] is False
    assert coverage["reason"] == "BASELINE_CALENDAR_UNAUDITED"
    assert coverage["start"] == repair.CUTOFF


def test_history_coverage_oversized_metadata_is_not_materialized(tmp_path, monkeypatch):
    store = _Store(tmp_path / "oversized-metadata.db")
    _canonical_table(store)
    monkeypatch.setattr(repair.byma, "ANIOS_AUDITADOS", frozenset({2025, 2026}))
    with store.connect() as db:
        db.execute(
            "INSERT INTO history_canonical_v2 VALUES(?,?,?,?,?,?,?,?,?,?,zeroblob(?))",
            (*_target(), repair.CUTOFF.isoformat(), "PPI_PRODUCTION_HISTORY",
             0, 8 * 1024 * 1024))
    tracemalloc.start()
    try:
        coverage = repair._coverage(store, _target())
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert coverage["complete"] is False
    assert coverage["reason"] == "CANONICAL_PROVENANCE_UNVERIFIED"
    assert peak < 4 * 1024 * 1024
