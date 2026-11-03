"""A617: adversarial offhost tests; no SDK/HTTP, writable operational DB or orders."""
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock
import uuid

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import rc6_a617_ppi_origin_selector_readonly as audit


def _db(tmp_path, *, selector=True, mode=True):
    db = tmp_path / "observer.sqlite"
    with sqlite3.connect(db) as conn:
        conn.executescript("""
          CREATE TABLE paper_workspace(
            id INTEGER,namespace TEXT,origin TEXT,imported_legacy INTEGER,dataset_id TEXT);
          CREATE TABLE financial_instrument_catalog(
            ticker TEXT, instrument_type TEXT, market TEXT, currency TEXT,
            settlement TEXT, status TEXT);
          CREATE TABLE market_snapshots(
            id INTEGER PRIMARY KEY, source TEXT, symbol TEXT, asset_class TEXT,
            market TEXT, currency TEXT, settlement TEXT, observed_at TEXT,
            trade_at TEXT, book_at TEXT, last_kind TEXT, last TEXT, bid TEXT,
            ask TEXT, bid_size TEXT, ask_size TEXT);
          CREATE TABLE universe_cycle_metrics(id INTEGER PRIMARY KEY, detail TEXT);
        """)
        conn.execute("INSERT INTO paper_workspace VALUES (1,?,?,0,?)",
          ("POROTA_PAPER_V17_FRESH", "FRESH_EMPTY", str(uuid.uuid4())))
        if selector:
            conn.execute("""CREATE TABLE candidate_identity_v2(
             ticker TEXT, instrument_type TEXT, market TEXT, currency TEXT,
             settlement TEXT, can_simulate INTEGER, status TEXT)""")
            conn.execute("""INSERT INTO candidate_identity_v2 VALUES
             ('GGAL','ACCIONES','BYMA','ARS','A-24HS',1,'AVAILABLE'),
             ('ABC','ACCIONES','BYMA','ARS','A-24HS',0,'HOLD')""")
        if mode:
            timestamp = (datetime.now(timezone.utc)-timedelta(seconds=4)).isoformat()
            conn.execute("""INSERT INTO market_snapshots
              (source,symbol,asset_class,market,currency,settlement,observed_at,
               trade_at,book_at,last_kind,last,bid,ask,bid_size,ask_size)
              VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              ("PRODUCTION_PAPER", "GGAL", "ACCIONES", "BYMA", "ARS", "A-24HS",
               timestamp, timestamp, timestamp, "TRADE", "100", "99", "101", "10", "10"))
        conn.execute("INSERT INTO universe_cycle_metrics(detail) VALUES (?)",
          ('scanner ' + json.dumps({"active_plan": {"cold": [],
            "active_budget": {"status": "FEASIBLE"},
            "ppi_cold_bootstrap_diagnostic": {"status": "NO_COLD_BOOTSTRAP_AUTHORITY",
             "selector_status": "QUERY_OK", "capacity_status": "OFF_BASELINE"}}}),))
    return db


def _receipt(root, *, altered=False, private=True, stamp=None):
    timestamp = stamp or (datetime.now(timezone.utc)-timedelta(seconds=4)).isoformat()
    record = {"symbol": "GGAL", "asset_class": "ACCIONES", "market": "BYMA",
      "currency": "ARS", "settlement": "A-24HS",
      "canonical_identity": ["GGAL", "ACCIONES", "BYMA", "ARS", "A-24HS"],
      "last": "100", "bid": "99", "ask": "101", "bid_size": "10", "ask_size": "10",
      "trade_at": timestamp, "book_at": timestamp, "received_at": timestamp,
      "last_kind": "TRADE", "state": "READY", "provider_origin": audit.ORIGIN}
    if altered:
        record["bid"] = "98"
    path = root / audit.RECEIPT
    path.write_text(json.dumps({"schema":audit.RECEIPT_SCHEMA,
      "producer": audit.PRODUCER, "provider_origin": audit.ORIGIN,
      "record_count":1, "records":[record]}), encoding="utf-8")
    path.chmod(0o600 if private else 0o644)
    return path


def _journal(root, *, corrupt=False):
    timestamp = (datetime.now(timezone.utc)-timedelta(seconds=4)).isoformat()
    book = {"native_date":timestamp,"bids":[{"price":"99","quantity":"10","position":0}],
            "offers":[{"price":"101","quantity":"10","position":0}]}
    event = {"symbol":"GGAL","asset_class":"ACCIONES","market":"BYMA",
      "currency":"ARS","settlement":"A-24HS",
      "canonical_identity":["GGAL","ACCIONES","BYMA","ARS","A-24HS"],
      "provider_origin":audit.ORIGIN, "source":"PPI",
      "observed_at":timestamp, "known_at":timestamp,
      "native_book_at":timestamp,"native_trade_at":timestamp,
      "normalized_book_sha256":audit._digest(book),"book":book,
      "quantity_unit":"NO_VERIFICADO","price_unit":"NO_VERIFICADO",
      "sequence":1,"previous_sha256":None}
    event["event_sha256"] = audit._digest(event)
    if corrupt:
        event["book"]["bids"][0]["quantity"] = "100000"
    path = root / audit.JOURNAL
    path.write_text(json.dumps({"schema":audit.JOURNAL_SCHEMA,
      "producer": audit.PRODUCER,"provider_origin":audit.ORIGIN,
      "record_count":1,"records":[event]}), encoding="utf-8")
    path.chmod(0o600)
    return path


def test_json_a385_zero_does_not_prove_zero_ppi_in_db_or_cold_selector(tmp_path):
    db = _db(tmp_path)
    before = db.stat().st_mtime_ns
    report = audit.diagnose(db, tmp_path, enforce_ro_mount=False)
    assert db.stat().st_mtime_ns == before, "read-only must not modify DB"
    assert report["state"] == "READ_ONLY_OBSERVED"
    assert report["selector"]["eligible_sql_exact_request"] == 1
    assert report["snapshots"]["paper_mode_rows"] == 1
    assert report["snapshots"]["ppi_source_label_rows"] == 0
    assert report["snapshots"]["paper_mode_provider_attested_rows"] == 0
    assert report["scanner_cycle"]["reported_cold_bootstrap_state"] == "NO_COLD_BOOTSTRAP_AUTHORITY"
    assert report["effective_ppi_lease"] == report["exit_budget_reserved"] == "NO_VERIFICADO"
    assert report["provider_gets"] == 0 and not report["orders_called"]
    assert not report["source_provenance_authorized"] and not report["admission_authority"]


def test_matching_primary_receipt_correlates_mode_but_never_confers_authority(tmp_path):
    db = _db(tmp_path)
    with sqlite3.connect(db) as conn:
        observed = conn.execute("SELECT observed_at FROM market_snapshots").fetchone()[0]
    _receipt(tmp_path, stamp=observed)
    _journal(tmp_path)
    result = audit.diagnose(db, tmp_path, enforce_ro_mount=False)
    assert result["scanner_receipt"]["record_count_asserted"] == 1
    assert result["scanner_receipt"]["fresh_native_trade_book_rows_structural"] == 1
    assert result["snapshots"]["paper_mode_exact_receipt_correlations"] == 1
    assert result["native_book_journal"]["hash_chain_structurally_valid"]
    assert not result["scanner_receipt"]["independently_authenticated"]
    assert not result["native_book_journal"]["independently_authenticated"]
    assert not result["causal_fill_verified"] and not result["admission_authority"]


def test_legacy_mode_snapshot_and_mismatched_receipt_remain_unbound(tmp_path):
    db = _db(tmp_path)
    _receipt(tmp_path, altered=True)
    result = audit.diagnose(db, tmp_path, enforce_ro_mount=False)
    assert result["snapshots"]["paper_mode_exact_receipt_correlations"] == 0
    assert result["snapshots"]["paper_mode_provider_attested_rows"] == 0


def test_reject_world_readable_private_receipt_and_symlink(tmp_path):
    db = _db(tmp_path)
    path = _receipt(tmp_path, private=False)
    result = audit.diagnose(db, tmp_path, enforce_ro_mount=False)
    assert result["scanner_receipt"]["state"] == "NO_VERIFICADO"
    assert result["scanner_receipt"]["reason"] == "PRIVATE_FILE_PERMISSIONS_OR_SIZE_HOLD"
    path.unlink()
    target = tmp_path / "untrusted.json"
    target.write_text("{}")
    path.symlink_to(target)
    result = audit.diagnose(db, tmp_path, enforce_ro_mount=False)
    assert result["scanner_receipt"]["reason"] == "PRIVATE_FILE_OPEN_HOLD"


def test_corrupt_journal_hash_chain_fails_closed(tmp_path):
    db = _db(tmp_path)
    _journal(tmp_path, corrupt=True)
    result = audit.diagnose(db, tmp_path, enforce_ro_mount=False)
    assert result["native_book_journal"]["state"] == "NO_VERIFICADO"
    assert result["native_book_journal"]["reason"] == "JOURNAL_CHAIN_HOLD"
    assert not result["native_book_journal"]["fill_authority"]


def test_zero_eligible_or_absent_selector_does_not_create_cold_calls(tmp_path):
    db = _db(tmp_path, selector=False, mode=False)
    result = audit.diagnose(db, tmp_path, enforce_ro_mount=False)
    assert result["selector"]["state"] == "SCHEMA_HOLD"
    assert result["selector"]["eligible_sql_exact_request"] is None
    assert result["provider_gets"] == 0
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE candidate_identity_v2(ticker TEXT,instrument_type TEXT,settlement TEXT,can_simulate INTEGER,status TEXT)")
    result = audit.diagnose(db, tmp_path, enforce_ro_mount=False)
    assert result["selector"]["eligible_sql_exact_request"] == 0
    assert result["provider_gets"] == 0


def test_unknown_db_is_no_verificado_even_with_structurally_valid_receipt(tmp_path):
    _receipt(tmp_path)
    result = audit.diagnose(tmp_path / "not-here.sqlite", tmp_path, enforce_ro_mount=False)
    assert result["state"] == "NO_VERIFICADO"
    assert result["reason"] == "DB_RO_QUERY_UNAVAILABLE"
    assert not result["db_opened"] and not result["admission_authority"]


def test_recent_sample_is_explicitly_bounded(tmp_path):
    db = _db(tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO market_snapshots(source,symbol) VALUES (?,?)",
                     ("PPI", "GGAL"))
    with mock.patch.object(audit, "MAX_SNAPSHOT_ROWS", 1):
        report = audit.diagnose(db, tmp_path, enforce_ro_mount=False)
    assert report["snapshots"]["recent_sampled"] == 1
    assert report["snapshots"]["recent_sample_truncated"] is True
    assert report["snapshots"]["ppi_source_label_rows"] == 1
    assert report["snapshots"]["ppi_source_label_provider_attested_rows"] == 0


def test_writable_mount_with_wal_shm_fail_closed_before_open(tmp_path):
    db = _db(tmp_path)
    wal = Path(str(db) + "-wal")
    shm = Path(str(db) + "-shm")
    wal.write_bytes(b"WAL-SENTINEL")
    shm.write_bytes(b"SHM-SENTINEL")
    before = [(path.stat().st_mtime_ns, path.read_bytes()) for path in (db, wal, shm)]
    with mock.patch.object(audit, "_mount_is_readonly", return_value=False):
        result = audit.diagnose(db, tmp_path)
    assert result["state"] == "NO_VERIFICADO"
    assert result["reason"] == "SQLITE_READONLY_MOUNT_REQUIRED"
    assert result["db_opened"] is False
    assert result["sqlite_ro_mount_protected"] is False
    assert [(path.stat().st_mtime_ns, path.read_bytes()) for path in (db, wal, shm)] == before


def test_wrong_fixed_db_identity_fails_closed(tmp_path):
    db = _db(tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE paper_workspace SET namespace='OTHER'")
    result = audit.diagnose(db, tmp_path, enforce_ro_mount=False)
    assert result["state"] == "NO_VERIFICADO"
    assert result["reason"] == "PAPER_V17_DATABASE_IDENTITY_HOLD"
    assert result["db_fixed_path_identity_verified"] is False
    assert not result["runtime_db_binding_verified"]


def test_legacy_selector_count_matches_observer_without_v2_uniqueness(tmp_path):
    db = _db(tmp_path, selector=False)
    with sqlite3.connect(db) as conn:
        conn.execute("""CREATE TABLE candidate_universe(
          ticker TEXT,instrument_type TEXT,settlement TEXT,
          can_simulate INTEGER,status TEXT)""")
        conn.execute("""INSERT INTO candidate_universe VALUES
          ('GGAL','ACCIONES','A-24HS',1,'AVAILABLE'),
          ('GGAL','ACCIONES','A-24HS',1,'AVAILABLE')""")
    result = audit.diagnose(db, tmp_path, enforce_ro_mount=False)
    assert result["selector"]["table"] == "candidate_universe"
    assert result["selector"]["eligible_sql_exact_request"] == 2
    assert result["selector"]["source_authority_qualified"] is False


def test_host_workflow_requires_private_ro_namespace_and_sanitizer_failclose():
    path = (Path(__file__).parents[1] / ".github" / "workflows" /
            "rc6-a617-ppi-origin-selector-readonly.yml")
    workflow = path.read_text(encoding="utf-8")
    assert "unshare --mount --propagation private" in workflow
    assert "mount -o remount,bind,ro /opt/porota-trading/data" in workflow
    assert "safe=failed_closed" in workflow
    assert "if: github.ref == 'refs/heads/main' && github.event_name == 'workflow_dispatch'" in workflow
    assert "immutable=1" not in workflow


class HostReadOnlyA617Tests(unittest.TestCase):
    """Run the same isolated fixtures with the Python standard library."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def test_a385_json_does_not_prove_db_zero(self):
        test_json_a385_zero_does_not_prove_zero_ppi_in_db_or_cold_selector(self.root)

    def test_receipt_correlates_without_authority(self):
        test_matching_primary_receipt_correlates_mode_but_never_confers_authority(self.root)

    def test_mismatch_remains_unbound(self):
        test_legacy_mode_snapshot_and_mismatched_receipt_remain_unbound(self.root)

    def test_private_receipt_permission_guard(self):
        test_reject_world_readable_private_receipt_and_symlink(self.root)

    def test_journal_integrity_guard(self):
        test_corrupt_journal_hash_chain_fails_closed(self.root)

    def test_selector_zero_keeps_get_zero(self):
        test_zero_eligible_or_absent_selector_does_not_create_cold_calls(self.root)

    def test_unknown_db_never_grants_authority(self):
        test_unknown_db_is_no_verificado_even_with_structurally_valid_receipt(self.root)

    def test_sample_cap_is_reported(self):
        test_recent_sample_is_explicitly_bounded(self.root)

    def test_wal_shm_mount_guard(self):
        test_writable_mount_with_wal_shm_fail_closed_before_open(self.root)

    def test_default_host_db_identity_guard(self):
        test_wrong_fixed_db_identity_fails_closed(self.root)

    def test_workflow_namespace_and_sanitizer_guards(self):
        test_host_workflow_requires_private_ro_namespace_and_sanitizer_failclose()

    def test_legacy_selector_matches_actual_fallback(self):
        test_legacy_selector_count_matches_observer_without_v2_uniqueness(self.root)


if __name__ == "__main__":
    unittest.main()
