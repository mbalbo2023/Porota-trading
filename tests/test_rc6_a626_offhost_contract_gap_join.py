"""A626 OFFHOST exact-identity diagnostic: no orders, provider GET or DB mutation."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from scripts import rc6_a626_offhost_contract_gap_join as gap


def _hash(data):
    return hashlib.sha256(json.dumps(
        data, sort_keys=True, ensure_ascii=False,
        separators=(",", ":")).encode("utf-8")).hexdigest()


def _db(tmp_path):
    path = tmp_path / "sanitized-offhost-copy.db"
    con = sqlite3.connect(path)
    con.executescript("""
    CREATE TABLE financial_instrument_catalog(
      ticker TEXT, instrument_type TEXT, market TEXT, currency TEXT,
      settlement TEXT, settlement_source TEXT, status TEXT, capability TEXT,
      metadata_json TEXT);
    CREATE TABLE contract_evidence_v2_snapshots(
      snapshot_id INTEGER PRIMARY KEY, family TEXT, ticker TEXT,
      market TEXT, currency TEXT, settlement TEXT,
      source_class TEXT, evidence_hash TEXT, observed_at TEXT,
      evidence_json TEXT);
    CREATE TABLE contract_evidence_v2_current(
      family TEXT, ticker TEXT, market TEXT, currency TEXT,
      settlement TEXT, source_class TEXT, snapshot_id INTEGER,
      evidence_hash TEXT, observed_at TEXT);
    """)
    return path, con


def _catalog(con, fam, symbol, currency="ARS", settlement="CI",
             source="PPI_FIELD", capability="NEEDS_CONTRACT", metadata=None):
    normalized_metadata = {"_discovery_source":
                           "PPI_PRIMARY" if source == "PPI_FIELD" else "IOL_COMPLEMENTARY"}
    normalized_metadata.update(metadata or {})
    con.execute("""INSERT INTO financial_instrument_catalog
       VALUES (?,?,?,?,?,?,?,?,?)""", (
        symbol, fam, "BYMA", currency, settlement, source,
        "AVAILABLE", capability, json.dumps(normalized_metadata)))


def _v2(con, sid, fam, symbol, fields, *,
        currency="ARS", settlement="CI", source="PPI_AUTHENTICATED_XHR"):
    h = _hash(fields)
    now = "2026-11-03T15:00:00+00:00"
    identity = (fam, symbol, "BYMA", currency, settlement, source)
    con.execute("INSERT INTO contract_evidence_v2_snapshots VALUES (?,?,?,?,?,?,?,?,?,?)",
                (sid, *identity, h, now, json.dumps(fields, ensure_ascii=False)))
    con.execute("INSERT INTO contract_evidence_v2_current VALUES (?,?,?,?,?,?,?,?,?)",
                (*identity, sid, h, now))


def _receipt(path):
    raw = path.read_bytes()
    rec = path.parent / "copy-receipt.json"
    rec.write_text(json.dumps({
        "schema": gap.COPY_SCHEMA, "copy_complete": True,
        "wal_safe_quiescence_verified": True, "runtime_source": False,
        "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}),
        encoding="utf-8")
    return rec


def _valid_six_families(tmp_path):
    path, con = _db(tmp_path)
    _catalog(con, "BONOS", "BND-AL30", metadata={
        "financial_contract_v17": {"cash_multiplier": 0.01}})
    _catalog(con, "ON", "CORP-YMCJO", currency="USD")
    _catalog(con, "OPCIONES", "CALL-GFGC")
    _catalog(con, "FUTUROS", "FUT-A3")
    _catalog(con, "CAUCIONES", "PESOS1")
    _catalog(con, "FCI", "FUND-A", currency="USD", metadata={
        "paper_family_contract_v1": {"cutoff_time": "12:00"}})
    _v2(con, 1, "BONOS", "BND-AL30", {
        "market": "BYMA", "currency": "ARS", "settlement": "CI",
        "evidence_scope": "PARTIAL_AUTHENTICATED_XHR"})
    _v2(con, 2, "OPCIONES", "CALL-GFGC", {
        "underlying": "GGAL"}, source="IOL_STRUCTURED_API")
    _v2(con, 3, "FUTUROS", "FUT-A3", {
        "underlying": "A3-PRIMARY", "expiry_at": "2026-12-31",
        "cash_multiplier": 1000, "quantity_min": 1,
        "quantity_step": 1, "settlement_method": "CASH"})
    _v2(con, 4, "FUTUROS", "FUT-A3", {
        "underlying": "A3-CONFLICT", "expiry_at": "2026-12-31",
        "cash_multiplier": 1000, "quantity_min": 1,
        "quantity_step": 1, "settlement_method": "CASH"},
        source="PPI_STRUCTURED_API")
    _v2(con, 5, "CAUCIONES", "PESOS1", {
        "book_quantity_unit_status": "BOOK_QUANTITY_UNIT_UNVERIFIED",
        "evidence_scope": "PARTIAL_PPI_5KEY_ONLY"})
    # Same symbol, wrong currency: cannot satisfy USD fund's five-key.
    _v2(con, 6, "FCI", "FUND-A", {"subscription_min": 100}, currency="ARS")
    con.commit()
    con.close()
    return path, _receipt(path)


def _families(report):
    return {row["family"]: row for row in report["families"]}


def test_six_families_exact_five_key_and_aggregate_only(tmp_path):
    path, receipt = _valid_six_families(tmp_path)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    report = gap.inspect(path, receipt)
    after = hashlib.sha256(path.read_bytes()).hexdigest()
    assert before == after == report["source_sha256"]
    assert report["capability_gaps_measured"] == 6
    assert report["catalog_rows"] == 6
    assert report["v2_current_rows"] == 6
    assert report["historical_819_gaps_match"] is False
    assert report["status"] == "DIAGNOSTIC_ONLY"
    assert not report["runtime_verified"]
    assert not report["ppi_provenance_authenticity_verified"]
    assert report["provider_gets"] == report["host_writes"] == report["real_orders_sent"] == 0
    assert report["paper_auto_enabled"] is False
    f = _families(report)
    assert f["BONOS"]["exact_v2_ppi"] == 1
    assert f["BONOS"]["ppi_partial_or_unverified"] == 1
    assert f["BONOS"]["ppi_missing_required_fields"] == 1
    assert f["BONOS"]["catalog_financial_contract_v17_declared"] == 1
    assert f["OBLIGACIONES"]["v2_absent"] == 1
    assert f["OPCIONES"]["exact_v2_complement_only"] == 1
    assert f["FUTUROS"]["ppi_field_conflicts"] == 1
    assert f["CAUCIONES"]["ppi_partial_or_unverified"] == 1
    assert f["FCI"]["ticker_only_v2_mismatch"] == 1
    assert f["FCI"]["catalog_paper_family_contract_v1_declared"] == 1
    dump = json.dumps(report, sort_keys=True)
    for sensitive in ("BND-AL30", "CORP-YMCJO", "CALL-GFGC",
                      "FUT-A3", "PESOS1", "FUND-A", "A3-CONFLICT"):
        assert sensitive not in dump


def test_idempotent_repeat_report_does_not_mutate_copy(tmp_path):
    path, receipt = _valid_six_families(tmp_path)
    first = gap.inspect(path, receipt)
    second = gap.inspect(path, receipt)
    assert first == second
    assert not Path(str(path) + "-wal").exists()
    assert not Path(str(path) + "-shm").exists()


def test_wrong_digest_fails_before_database_query(tmp_path):
    path, receipt = _valid_six_families(tmp_path)
    value = json.loads(receipt.read_text(encoding="utf-8"))
    value["sha256"] = "0" * 64
    receipt.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(gap.Rejected, match="COPY_DIGEST_MISMATCH"):
        gap.inspect(path, receipt)


def test_reject_wal_sidecar_without_touching_copy(tmp_path):
    path, receipt = _valid_six_families(tmp_path)
    Path(str(path) + "-wal").write_bytes(b"not a checkpointed standalone copy")
    with pytest.raises(gap.Rejected, match="COPY_UNCHECKPOINTED_SIDECAR_PRESENT"):
        gap.inspect(path, receipt)


def test_symlink_is_not_a_wal_safe_copy(tmp_path):
    path, receipt = _valid_six_families(tmp_path)
    link = tmp_path / "symlink-copy.db"
    link.symlink_to(path)
    with pytest.raises(gap.Rejected, match="COPY_PATH_UNSAFE"):
        gap.inspect(link, receipt)


def test_missing_v2_table_rejected_even_with_valid_digest(tmp_path):
    path, con = _db(tmp_path)
    _catalog(con, "BONOS", "BND")
    con.execute("DROP TABLE contract_evidence_v2_current")
    con.commit()
    con.close()
    with pytest.raises(gap.Rejected, match="COPY_TABLES_MISSING"):
        gap.inspect(path, _receipt(path))


def test_orphan_current_cannot_be_interpreted_as_absent_contract(tmp_path):
    path, con = _db(tmp_path)
    _catalog(con, "BONOS", "BND")
    con.execute("""INSERT INTO contract_evidence_v2_current VALUES
       ('BONOS','BND','BYMA','ARS','CI','PPI_AUTHENTICATED_XHR',999,'abcd','2026-11-03')""")
    con.commit()
    con.close()
    with pytest.raises(gap.Rejected, match="V2_CURRENT_SNAPSHOT_ORPHAN"):
        gap.inspect(path, _receipt(path))


def test_tampered_current_snapshot_five_key_is_blocked(tmp_path):
    path, con = _db(tmp_path)
    _catalog(con, "BONOS", "BND")
    _v2(con, 1, "BONOS", "BND", {"market": "BYMA"})
    con.execute("UPDATE contract_evidence_v2_current SET currency='USD'")
    con.commit()
    con.close()
    with pytest.raises(gap.Rejected, match="V2_CURRENT_SNAPSHOT_IDENTITY_MISMATCH"):
        gap.inspect(path, _receipt(path))


def test_tampered_snapshot_payload_hash_is_blocked(tmp_path):
    path, con = _db(tmp_path)
    _catalog(con, "BONOS", "BND")
    _v2(con, 1, "BONOS", "BND", {"market": "BYMA"})
    con.execute("UPDATE contract_evidence_v2_snapshots SET evidence_json=?", (
        json.dumps({"market": "FALSE"}),))
    con.commit()
    con.close()
    with pytest.raises(gap.Rejected, match="V2_EVIDENCE_HASH_MISMATCH"):
        gap.inspect(path, _receipt(path))


def test_incomplete_receipt_is_not_standalone_offhost_copy(tmp_path):
    path, receipt = _valid_six_families(tmp_path)
    data = json.loads(receipt.read_text(encoding="utf-8"))
    data["wal_safe_quiescence_verified"] = False
    receipt.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(gap.Rejected, match="COPY_CUSTODY_RECEIPT_INVALID"):
        gap.inspect(path, receipt)


def test_copy_receipt_never_turns_diagnostic_into_paper_admission(tmp_path):
    path, receipt = _valid_six_families(tmp_path)
    report = gap.inspect(path, receipt)
    assert report["source_copy_receipt_claim_only"]
    assert report["status"] != "READY"
    assert not report["paper_auto_enabled"]


def test_unknown_currency_is_not_exact_five_key_even_when_same_in_v2(tmp_path):
    path, con = _db(tmp_path)
    _catalog(con, "BONOS", "ALIAS-UNKNOWN", currency="UNKNOWN")
    _v2(con, 1, "BONOS", "ALIAS-UNKNOWN",
        {"cash_multiplier": 0.01}, currency="UNKNOWN")
    con.commit()
    con.close()
    row = _families(gap.inspect(path, _receipt(path)))["BONOS"]
    assert row["catalog_incomplete_five_key"] == 1
    assert row["exact_v2_ppi"] == 0
    assert row["capability_gaps"] == 1


def test_usd_and_usd_mep_cannot_cross_match_or_infer_fx(tmp_path):
    path, con = _db(tmp_path)
    _catalog(con, "BONOS", "BND-USD", currency="USD")
    _v2(con, 1, "BONOS", "BND-USD", {
        "payment_currency": "USD_MEP", "cash_multiplier": 1.0},
        currency="USD_MEP")
    con.commit()
    con.close()
    report = gap.inspect(path, _receipt(path))
    row = _families(report)["BONOS"]
    assert row["exact_v2_ppi"] == 0
    assert row["ticker_only_v2_mismatch"] == 1
    assert "fx_rate" not in json.dumps(report)


def test_dangling_wal_symlink_is_rejected(tmp_path):
    path, receipt = _valid_six_families(tmp_path)
    Path(str(path) + "-wal").symlink_to(tmp_path / "missing-sidecar")
    with pytest.raises(gap.Rejected, match="COPY_UNCHECKPOINTED_SIDECAR_PRESENT"):
        gap.inspect(path, receipt)


def test_archived_ppi_snapshot_without_current_is_not_active_contract(tmp_path):
    path, con = _db(tmp_path)
    _catalog(con, "ON", "CORP-OLD", currency="USD")
    _v2(con, 1, "ON", "CORP-OLD", {
        "cash_multiplier": 1.0, "evidence_scope": "ARCHIVED"},
        currency="USD")
    con.execute("DELETE FROM contract_evidence_v2_current")
    con.commit()
    con.close()
    report = gap.inspect(path, _receipt(path))
    row = _families(report)["OBLIGACIONES"]
    assert row["v2_absent"] == 1
    assert row["historic_ppi_exact_without_current"] == 1
    assert row["exact_v2_ppi"] == 0
    assert report["v2_current_rows"] == 0


@pytest.mark.parametrize("catalog_family,evidence_family", [
    ("OBLIGACIONES", "ON"),
    ("ON", "OBLIGACIONES"),
    ("FCI", "FCI_LOCAL"),
    ("FCI_LOCAL", "FCI"),
])
def test_family_alias_never_confers_exact_ppi_authority(tmp_path, catalog_family, evidence_family):
    path, con = _db(tmp_path)
    _catalog(con, catalog_family, "ALIAS-ONLY-1")
    _v2(con, 1, evidence_family, "ALIAS-ONLY-1", {
        "market": "BYMA", "currency": "ARS", "settlement": "CI",
        "evidence_scope": "PARTIAL_PPI_5KEY_ONLY"})
    con.commit()
    con.close()
    row = _families(gap.inspect(path, _receipt(path)))[gap._family(catalog_family)]
    assert row["capability_gaps"] == 1
    assert row["exact_v2_ppi"] == 0
    assert row["alias_current_only_no_authority"] == 1
    assert row["ticker_only_v2_mismatch"] == 1
    assert row["ppi_fields_present_not_accredited"] == 0


def test_alias_family_difference_between_current_and_snapshot_is_rejected(tmp_path):
    path, con = _db(tmp_path)
    _catalog(con, "OBLIGACIONES", "ALIAS-TAMPER-1")
    _v2(con, 1, "ON", "ALIAS-TAMPER-1", {
        "market": "BYMA", "currency": "ARS", "settlement": "CI"})
    con.execute("UPDATE contract_evidence_v2_current SET family='OBLIGACIONES'")
    con.commit()
    con.close()
    with pytest.raises(gap.Rejected, match="V2_CURRENT_SNAPSHOT_IDENTITY_MISMATCH"):
        gap.inspect(path, _receipt(path))


def test_historical_family_alias_cannot_claim_exact_ppi_evidence(tmp_path):
    path, con = _db(tmp_path)
    _catalog(con, "FCI", "FUND-HISTORY-1")
    _v2(con, 1, "FCI_LOCAL", "FUND-HISTORY-1", {
        "currency": "ARS", "subscription_min": 100})
    con.execute("DELETE FROM contract_evidence_v2_current")
    con.commit()
    con.close()
    row = _families(gap.inspect(path, _receipt(path)))["FCI"]
    assert row["capability_gaps"] == 1
    assert row["historic_ppi_exact_without_current"] == 0
    assert row["exact_v2_ppi"] == 0
    assert row["v2_absent"] == 1


def test_same_raw_family_on_identity_still_matches_ppi_exactly(tmp_path):
    path, con = _db(tmp_path)
    _catalog(con, "ON", "CORP-EXACT-1", currency="USD")
    _v2(con, 1, "ON", "CORP-EXACT-1", {
        "market": "BYMA", "currency": "USD", "settlement": "CI",
        "evidence_scope": "PARTIAL_PPI_5KEY_ONLY"}, currency="USD")
    con.commit()
    con.close()
    row = _families(gap.inspect(path, _receipt(path)))["OBLIGACIONES"]
    assert row["exact_v2_ppi"] == 1
    assert row["alias_current_only_no_authority"] == 0
    assert row["ppi_partial_or_unverified"] == 1
