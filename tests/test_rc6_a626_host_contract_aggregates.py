"""A626 host-side contract aggregate probe: mocked private RO mount, no SSH."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import uuid

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
    with pytest.raises(host.Blocked, match="HOST_V2_CURRENT_SNAPSHOT_INTEGRITY_INVALID"):
        host.probe(data_root=data, db=path, guard=lambda _: True)


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
