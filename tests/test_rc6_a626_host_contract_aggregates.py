"""A626 host-side contract aggregate probe: mocked private RO mount, no SSH."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from scripts import rc6_a626_host_contract_aggregates as host


def _fixture(tmp_path):
    data = tmp_path / "data"
    path = data / "paper_v17" / "observer_v17.db"
    path.parent.mkdir(parents=True)
    con = sqlite3.connect(path)
    con.executescript("""
      CREATE TABLE paper_workspace(
       id INTEGER,namespace TEXT,dataset_id TEXT,origin TEXT,
       imported_legacy INTEGER,created_at TEXT);
      CREATE TABLE financial_instrument_catalog(
       ticker TEXT,instrument_type TEXT,market TEXT,currency TEXT,
       settlement TEXT,settlement_source TEXT,status TEXT,
       capability TEXT,metadata_json TEXT);
      CREATE TABLE contract_evidence_v2_current(
       family TEXT,ticker TEXT,market TEXT,currency TEXT,settlement TEXT,
       source_class TEXT,snapshot_id INTEGER,evidence_hash TEXT,observed_at TEXT);
      CREATE TABLE contract_evidence_v2_snapshots(
       snapshot_id INTEGER PRIMARY KEY,family TEXT,ticker TEXT,
       market TEXT,currency TEXT,settlement TEXT,source_class TEXT,
       evidence_hash TEXT,observed_at TEXT);
      INSERT INTO paper_workspace VALUES
       (1,'POROTA_PAPER_V17_FRESH','00000000-0000-4000-8000-000000000001',
        'FRESH_EMPTY',0,'2026-11-03T12:00:00+00:00');
    """)
    return data, path, con


def _cat(con, fam, ticker, *, currency="ARS", source="PPI_FIELD",
         status="AVAILABLE", metadata=None, capability="NEEDS_CONTRACT"):
    m = {"_discovery_source": "PPI_PRIMARY" if source == "PPI_FIELD"
         else "IOL_COMPLEMENTARY"}
    m.update(metadata or {})
    con.execute("INSERT INTO financial_instrument_catalog VALUES (?,?,?,?,?,?,?,?,?)",
                (ticker, fam, "BYMA", currency, "CI", source, status,
                 capability, json.dumps(m)))


def _snap(con, sid, fam, ticker, *, source="PPI_AUTHENTICATED_XHR",
          currency="ARS", current=True):
    ident = (fam, ticker, "BYMA", currency, "CI", source)
    observed = "2026-11-03T15:00:00+00:00"
    digest = "f" * 64
    con.execute("INSERT INTO contract_evidence_v2_snapshots VALUES (?,?,?,?,?,?,?,?,?)",
                (sid, *ident, digest, observed))
    if current:
        con.execute("INSERT INTO contract_evidence_v2_current VALUES (?,?,?,?,?,?,?,?,?)",
                    (*ident, sid, digest, observed))


def _get(tmp_path):
    data, path, con = _fixture(tmp_path)
    _cat(con, "BONOS", "AL30", metadata={
        "financial_contract_v17": {"cash_multiplier": 0.01}})
    _cat(con, "ON", "YMCJO", currency="USD")
    _cat(con, "OPCIONES", "GFGC", source="IOL_COMPLEMENTARY")
    _cat(con, "FUTUROS", "DLR")
    _cat(con, "CAUCIONES", "PESOS1")
    _cat(con, "FCI", "FUND-USD", currency="USD")
    _snap(con, 1, "BONOS", "AL30")
    _snap(con, 2, "ON", "YMCJO", currency="USD", current=False)
    _snap(con, 3, "OPCIONES", "GFGC", source="IOL_STRUCTURED_API")
    _snap(con, 4, "FUTUROS", "DLR")
    _snap(con, 5, "CAUCIONES", "PESOS1")
    _snap(con, 6, "FCI", "FUND-USD", currency="ARS")
    con.commit()
    con.close()
    return data, path


def _row(report, family):
    return next(r for r in report["families"] if r["family"] == family)


def _assert_partial_no_go(report, reasons):
    assert report["status"] == "DIAGNOSTIC_PARTIAL_NO_GO"
    assert report["v2_current_integrity_verified"] is False
    assert report["v2_current_invalid_reason_counts"] == reasons
    assert (report["v2_current_valid_rows"] + sum(reasons.values())
            == report["v2_current_rows"])
    assert report["ppi_identity_authority_verified"] is False
    assert report["source_contract_terms_fresh_verified"] is False
    assert report["causal_pit_verified"] is False
    assert report["paper_auto_enabled"] is False
    assert report["host_db_writes"] == report["provider_gets"] == 0


def test_host_guard_fails_before_open_when_no_private_ro_mount(tmp_path):
    data, path, _ = _fixture(tmp_path)
    assert not host.private_ro_mount_verified(data)
    with pytest.raises(host.Blocked, match="HOST_PRIVATE_RO_MOUNT_UNVERIFIED"):
        host.probe(data_root=data, db=path)


def test_six_families_aggregate_only_no_mutation_or_tickers(tmp_path):
    data, path = _get(tmp_path)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    report = host.probe(data_root=data, db=path, guard=lambda _: True)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert report["capability_gaps_measured"] == 6
    assert report["catalog_rows"] == 6
    assert report["v2_current_rows"] == 5
    assert report["v2_current_valid_rows"] == 5
    assert report["v2_current_integrity_verified"] is True
    assert report["v2_current_invalid_reason_counts"] == {}
    assert report["v2_history_ppi_rows_scanned"] >= 5
    assert report["v2_history_identity_bytes_scanned"] > 0
    assert report["historical_819_gap_match"] is False
    assert report["status"] == "DIAGNOSTIC_ONLY"
    assert report["private_ro_mount_verified"]
    assert report["paper_workspace_identity_verified"]
    assert not report["ppi_identity_authority_verified"]
    assert not report["paper_auto_enabled"]
    assert report["host_db_writes"] == report["provider_gets"] == 0
    assert _row(report, "BONOS")["catalog_financial_contract_v17_declared"] == 1
    assert _row(report, "BONOS")["exact_current_v2_ppi"] == 1
    assert _row(report, "OBLIGACIONES")["historic_ppi_exact_without_current"] == 1
    assert _row(report, "OBLIGACIONES")["v2_current_absent"] == 1
    assert _row(report, "OPCIONES")["exact_current_v2_complement_only"] == 1
    assert _row(report, "FCI")["ticker_only_v2_mismatch"] == 1
    payload = json.dumps(report, sort_keys=True)
    for name in ("AL30", "YMCJO", "GFGC", "DLR", "PESOS1", "FUND-USD"):
        assert name not in payload


def test_wal_without_shm_stops_before_sqlite_open(tmp_path):
    data, path = _get(tmp_path)
    Path(str(path) + "-wal").write_bytes(b"unsafe live WAL")
    with pytest.raises(host.Blocked, match="HOST_WAL_SHM_MISSING_FAILCLOSED"):
        host.probe(data_root=data, db=path, guard=lambda _: True)


def test_sidecar_symlink_rejected(tmp_path):
    data, path = _get(tmp_path)
    Path(str(path) + "-shm").symlink_to(data / "missing-sidecar")
    with pytest.raises(host.Blocked, match="HOST_DB_SIDECAR_ALIAS_UNSAFE"):
        host.probe(data_root=data, db=path, guard=lambda _: True)


def test_workspace_namespace_not_ppi_and_bad_origin_failclosed(tmp_path):
    data, path, con = _fixture(tmp_path)
    _cat(con, "BONOS", "AL30")
    con.execute("UPDATE paper_workspace SET origin='IMPORTED_LEGACY'")
    con.commit()
    con.close()
    with pytest.raises(host.Blocked, match="HOST_PAPER_WORKSPACE_UNVERIFIED"):
        host.probe(data_root=data, db=path, guard=lambda _: True)


def test_orphan_v2_does_not_reduce_missing_contract_count(tmp_path):
    data, path, con = _fixture(tmp_path)
    _cat(con, "BONOS", "AL30")
    con.execute("""INSERT INTO contract_evidence_v2_current VALUES
      ('BONOS','AL30','BYMA','ARS','CI','PPI_AUTHENTICATED_XHR',
       999,'f','2026-11-03T15:00:00+00:00')""")
    con.commit()
    con.close()
    report = host.probe(data_root=data, db=path, guard=lambda _: True)
    _assert_partial_no_go(report, {"HOST_V2_SNAPSHOT_REFERENCE_MISSING": 1})
    assert report["capability_gaps_measured"] == 1
    assert _row(report, "BONOS")["exact_current_v2_ppi"] == 0
    assert _row(report, "BONOS")["v2_current_absent"] == 1


def test_cross_currency_never_matches_ppi_current(tmp_path):
    data, path, con = _fixture(tmp_path)
    _cat(con, "BONOS", "AL30", currency="USD")
    _snap(con, 1, "BONOS", "AL30", currency="USD_MEP")
    con.commit()
    con.close()
    row = _row(host.probe(data_root=data, db=path, guard=lambda _: True), "BONOS")
    assert row["exact_current_v2_ppi"] == 0
    assert row["ticker_only_v2_mismatch"] == 1


def test_unknown_identity_never_counts_as_five_key(tmp_path):
    data, path, con = _fixture(tmp_path)
    _cat(con, "BONOS", "BND", currency="UNKNOWN")
    _snap(con, 1, "BONOS", "BND", currency="UNKNOWN")
    con.commit()
    con.close()
    row = _row(host.probe(data_root=data, db=path, guard=lambda _: True), "BONOS")
    assert row["gap_incomplete_five_key"] == 1
    assert row["exact_current_v2_ppi"] == 0


def test_idempotent_host_readonly_diagnostic(tmp_path):
    data, path = _get(tmp_path)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    a = host.probe(data_root=data, db=path, guard=lambda _: True)
    b = host.probe(data_root=data, db=path, guard=lambda _: True)
    a.pop("measured_at_utc", None)
    b.pop("measured_at_utc", None)
    assert a == b
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_positive_private_ro_mount_guard_requires_isolated_ns(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    mount_info = ("12 1 0:42 / " + str(data) +
                  " ro,relatime - tmpfs tmpfs ro\n")
    real_stat = host.os.stat
    real_read = Path.read_text
    def fake_stat(path, *args, **kwargs):
        if str(path) == "/proc/self/ns/mnt":
            return SimpleNamespace(st_ino=10001)
        if str(path) == "/proc/1/ns/mnt":
            return SimpleNamespace(st_ino=10000)
        return real_stat(path, *args, **kwargs)
    def fake_read(self, *args, **kwargs):
        if str(self) == "/proc/self/mountinfo":
            return mount_info
        return real_read(self, *args, **kwargs)
    with patch.object(host.os, "stat", side_effect=fake_stat), patch.object(
            Path, "read_text", new=fake_read):
        assert host.private_ro_mount_verified(data)
        mount_info += ("13 12 0:42 / " + str(data / "paper_v17") +
                       " rw,relatime - tmpfs tmpfs rw\n")
        assert not host.private_ro_mount_verified(data)


def test_workflow_host_never_runs_on_pull_request(tmp_path):
    del tmp_path
    text = (ROOT / ".github/workflows/rc6-a626-financial-contract-host-readonly.yml"
            ).read_text(encoding="utf-8")
    assert "github.event_name == 'workflow_dispatch'" in text
    assert "github.ref_name == 'main'" in text
    assert "inputs.permit_readonly_host_probe == true" in text
    assert "sudo -n timeout 25 unshare --mount --propagation private" in text
    assert "mount -o remount,bind,ro /opt/porota-trading/data" in text
    assert "exec python3 -B -" in text
    # The SQL census, and not unrelated bounded metadata diagnostics in this
    # workflow, must stay inside the private read-only mount namespace.
    # In particular A516 may query pre-existing container metadata with a
    # separately pinned, non-provider Docker command under its own guards.
    sql_step = text.split(
        "      - name: Remote private mount namespace RO bounded SQL aggregates\n", 1
    )[1].split("\n      - name:", 1)[0]
    assert "docker exec" not in sql_step


def test_historical_reconciliation_streams_once_not_n_queries(tmp_path):
    data, path, con = _fixture(tmp_path)
    _cat(con, "ON", "CORP-OLD", currency="USD")
    for n in range(1000):
        _snap(con, n + 1, "ON", "CORP-" + str(n), current=False,
              currency="USD")
    con.commit()
    con.row_factory = sqlite3.Row
    statements = []
    con.set_trace_callback(statements.append)
    # Raw ON matches raw ON. An OBLIGACIONES alias would not be exact.
    requested = {("ON", "CORP-999", "BYMA", "USD", "CI")}
    found, scanned, consumed = host._historical_ppi_memberships(con, requested)
    con.close()
    assert found == requested
    assert scanned == 1000
    assert consumed > 1000
    assert len([x for x in statements
                if "FROM contract_evidence_v2_snapshots" in x]) == 1


def test_historical_stream_respects_row_and_byte_budgets(tmp_path):
    data, path, con = _fixture(tmp_path)
    for n in range(4):
        _snap(con, n + 1, "BONOS", "BND-" + str(n), current=False)
    con.commit()
    con.row_factory = sqlite3.Row
    with patch.object(host, "MAX_HISTORY_ROWS", 2):
        with pytest.raises(host.Blocked, match="HOST_HISTORY_PPI_ROWS_BUDGET"):
            host._historical_ppi_memberships(
                con, {("BONOS", "BND-3", "BYMA", "ARS", "CI")})
    with patch.object(host, "MAX_HISTORY_IDENTITY_BYTES", 5):
        with pytest.raises(host.Blocked, match="HOST_HISTORY_PPI_IDENTITY_BYTES_BUDGET"):
            host._historical_ppi_memberships(
                con, {("BONOS", "BND-3", "BYMA", "ARS", "CI")})
    con.close()


def test_alias_only_on_current_cannot_be_exact_ppi(tmp_path):
    data, path, con = _fixture(tmp_path)
    _cat(con, "ON", "CORP-ALIAS", currency="USD")
    _snap(con, 1, "OBLIGACIONES", "CORP-ALIAS", currency="USD")
    con.commit()
    con.close()
    row = _row(host.probe(data_root=data, db=path, guard=lambda _: True),
               "OBLIGACIONES")
    assert row["exact_current_v2_ppi"] == 0
    assert row["ticker_only_v2_mismatch"] == 1


def test_alias_only_fci_local_cannot_be_exact_ppi(tmp_path):
    data, path, con = _fixture(tmp_path)
    _cat(con, "FCI_LOCAL", "FUND-ALIAS")
    _snap(con, 1, "FCI", "FUND-ALIAS")
    con.commit()
    con.close()
    row = _row(host.probe(data_root=data, db=path, guard=lambda _: True), "FCI")
    assert row["exact_current_v2_ppi"] == 0
    assert row["ticker_only_v2_mismatch"] == 1


def test_mixed_family_current_snapshot_fails_integrity(tmp_path):
    data, path, con = _fixture(tmp_path)
    _cat(con, "ON", "CORP-MIX", currency="USD")
    _snap(con, 1, "ON", "CORP-MIX", currency="USD")
    con.execute("UPDATE contract_evidence_v2_current SET family='OBLIGACIONES'")
    con.commit()
    con.close()
    report = host.probe(data_root=data, db=path, guard=lambda _: True)
    _assert_partial_no_go(report, {"HOST_V2_SNAPSHOT_FIVE_KEY_MISMATCH": 1})
    assert _row(report, "OBLIGACIONES")["exact_current_v2_ppi"] == 0


@pytest.mark.parametrize("table,column,value,reason", [
    ("contract_evidence_v2_current", "snapshot_id", 999,
     "HOST_V2_SNAPSHOT_REFERENCE_MISSING"),
    ("contract_evidence_v2_current", "family", "OBLIGACIONES",
     "HOST_V2_SNAPSHOT_FIVE_KEY_MISMATCH"),
    ("contract_evidence_v2_snapshots", "family", None,
     "HOST_V2_SNAPSHOT_FIVE_KEY_MISMATCH"),
    ("contract_evidence_v2_current", "source_class", "IOL_STRUCTURED_API",
     "HOST_V2_SNAPSHOT_SOURCE_CLASS_MISMATCH"),
    ("contract_evidence_v2_current", "evidence_hash", "0" * 64,
     "HOST_V2_SNAPSHOT_EVIDENCE_HASH_MISMATCH"),
])
def test_current_snapshot_integrity_reasons_are_specific_and_private(
        tmp_path, table, column, value, reason):
    data, path = _get(tmp_path)
    con = sqlite3.connect(path)
    # Identifiers are fixed literals above, never runtime/user input.
    con.execute(f"UPDATE {table} SET {column}=? WHERE ticker='AL30'", (value,))
    con.commit()
    con.close()
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    report = host.probe(data_root=data, db=path, guard=lambda _: True)
    _assert_partial_no_go(report, {reason: 1})
    assert report["catalog_rows"] == 6
    assert report["capability_gaps_measured"] == 6
    assert _row(report, "BONOS")["exact_current_v2_ppi"] == 0
    payload = json.dumps(report, sort_keys=True)
    assert "AL30" not in payload
    assert "BYMA" not in payload
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


@pytest.mark.parametrize("current_timestamp", [
    "2026-11-03T15:00:00Z",
    "2026-11-03T16:00:00+01:00",  # Equal UTC instant, different syntax.
    "2026-11-03T15:00:01+00:00",  # Same immutable hash, latest observation.
    "2026-11-03T12:00:01-03:00",  # Later observation in ART.
])
def test_reobserved_identical_hash_preserves_snapshot_first_seen_timestamp(
        tmp_path, current_timestamp):
    data, path = _get(tmp_path)
    con = sqlite3.connect(path)
    con.execute("UPDATE contract_evidence_v2_current SET observed_at=? WHERE ticker='AL30'",
                (current_timestamp,))
    con.commit()
    con.close()
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    result = host.probe(data_root=data, db=path, guard=lambda _: True)
    assert result["status"] == "DIAGNOSTIC_ONLY"
    assert result["v2_current_integrity_verified"] is True
    assert result["v2_current_invalid_reason_counts"] == {}
    assert _row(result, "BONOS")["exact_current_v2_ppi"] == 1
    assert result["ppi_identity_authority_verified"] is False
    assert result["causal_pit_verified"] is False
    assert result["host_db_writes"] == result["provider_gets"] == 0
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


@pytest.mark.parametrize("table,timestamp,expected_reason", [
    ("contract_evidence_v2_current", "2026-11-03T14:59:59+00:00",
     "HOST_V2_SNAPSHOT_OBSERVED_AT_REVERSED"),
    ("contract_evidence_v2_current", "2026-11-03T14:00:00-00:30",
     "HOST_V2_SNAPSHOT_OBSERVED_AT_REVERSED"),
    ("contract_evidence_v2_current", "2026-11-03T15:00:01",
     "HOST_V2_SNAPSHOT_OBSERVED_AT_INVALID"),
    ("contract_evidence_v2_snapshots", "2026-11-03T15:00:01",
     "HOST_V2_SNAPSHOT_OBSERVED_AT_INVALID"),
    ("contract_evidence_v2_current", "2026-11-03Tbad",
     "HOST_V2_SNAPSHOT_OBSERVED_AT_INVALID"),
    ("contract_evidence_v2_current", None,
     "HOST_V2_SNAPSHOT_OBSERVED_AT_INVALID"),
])
def test_timestamp_reversal_or_untrusted_clock_remains_failclosed(
        tmp_path, table, timestamp, expected_reason):
    data, path = _get(tmp_path)
    con = sqlite3.connect(path)
    con.execute(f"UPDATE {table} SET observed_at=? WHERE ticker='AL30'", (timestamp,))
    con.commit()
    con.close()
    report = host.probe(data_root=data, db=path, guard=lambda _: True)
    _assert_partial_no_go(report, {expected_reason: 1})
    assert _row(report, "BONOS")["exact_current_v2_ppi"] == 0
    assert report["capability_gaps_measured"] == 6


def test_multiple_invalid_current_rows_do_not_contaminate_819_denominator(tmp_path):
    data, path = _get(tmp_path)
    con = sqlite3.connect(path)
    con.execute("""UPDATE contract_evidence_v2_current SET observed_at=?
                   WHERE family='BONOS'""", ("2026-11-03T14:59:59Z",))
    con.execute("""UPDATE contract_evidence_v2_current SET evidence_hash=?
                   WHERE family='OPCIONES'""", ("0" * 64,))
    con.commit()
    con.close()
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    report = host.probe(data_root=data, db=path, guard=lambda _: True)
    _assert_partial_no_go(report, {
        "HOST_V2_SNAPSHOT_OBSERVED_AT_REVERSED": 1,
        "HOST_V2_SNAPSHOT_EVIDENCE_HASH_MISMATCH": 1,
    })
    assert report["v2_current_rows"] == 5
    assert report["v2_current_valid_rows"] == 3
    assert report["catalog_rows"] == 6
    assert report["capability_gaps_measured"] == 6
    assert report["historical_819_gap_match"] is False
    assert _row(report, "BONOS")["exact_current_v2_ppi"] == 0
    assert _row(report, "OPCIONES")["exact_current_v2_complement_only"] == 0
    assert _row(report, "FUTUROS")["exact_current_v2_ppi"] == 1
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_historical_alias_only_does_not_match(tmp_path):
    data, path, con = _fixture(tmp_path)
    _cat(con, "ON", "CORP-HIST", currency="USD")
    _snap(con, 1, "OBLIGACIONES", "CORP-HIST", currency="USD",
          current=False)
    con.commit()
    con.close()
    row = _row(host.probe(data_root=data, db=path, guard=lambda _: True),
               "OBLIGACIONES")
    assert row["historic_ppi_exact_without_current"] == 0
    assert row["v2_current_absent"] == 1


def test_literal_fci_local_is_counted_in_fci_bucket(tmp_path):
    data, path, con = _fixture(tmp_path)
    _cat(con, "FCI_LOCAL", "FUND-EXACT")
    _snap(con, 1, "FCI_LOCAL", "FUND-EXACT")
    con.commit()
    con.close()
    row = _row(host.probe(data_root=data, db=path, guard=lambda _: True), "FCI")
    assert row["exact_current_v2_ppi"] == 1
    assert row["capability_gaps"] == 1
