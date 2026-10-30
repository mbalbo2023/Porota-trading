"""Exact review of the A116 authority-label correction; no broker/host access."""
import json
import sqlite3
from datetime import datetime

import pytest

import cp_contract_evidence_v2_hf6 as evidence
import bu_instrument_catalog as catalog
import cr_contract_evidence_v2_mass_hf6 as mass
import rc6_contract_bridge as bridge


DETECTED = "2026-10-27T03:00:34.204768+00:00"
REVIEWED = "2026-10-30T02:45:00+00:00"
REF = "https://github.com/mbalbo2023/Porota-trading/issues/732#issuecomment-6513076822"


class Store:
    def __init__(self, path):
        self.path = str(path)

    def connect(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        return db


def policy(ticker, authority):
    return {
        "side": "COLOCADORA", "term_days": ticker[5:],
        "minimum_principal": "100000", "paper_principal_step": "1",
        "day_count_basis": "365", "fee_payment": "UPFRONT",
        "paper_fill_policy": "LIVE_PPI_BID_PARTICIPATION_CAP",
        "fee_authority": authority, "policy_scope": "PRODUCTION_PAPER_SIMULATION_ONLY",
        "dynamic_execution_gate": "FRESH_PPI_PLACING_BID_REQUIRED",
        "readiness_guard": "PPI_EXACT_IDENTITY+SUPPORTED_TICKER+LIVE_BID_AT_EXECUTION",
    }


def fixture(tmp_path, tickers=("PESOS7",), *, identity=None, payload_change=None,
            old_authority=None, new_authority=None, source_ref=None):
    store = Store(tmp_path / "review.db")
    evidence.init_schema(store)
    for ticker in tickers:
        common = dict(family="CAUCIONES", ticker=ticker, market="BYMA", currency="ARS",
                      settlement="INMEDIATA", source_class="DERIVED_OFFICIAL_RULE",
                      source_ref=source_ref or evidence._FEE_POLICY_SOURCE)
        common.update(identity or {})
        old = policy(ticker, old_authority or evidence._FEE_AUTHORITY_OLD)
        new = policy(ticker, new_authority or evidence._FEE_AUTHORITY_NEW)
        new.update(payload_change or {})
        evidence.record_snapshot(store, **common, evidence=old,
                                 observed_at="2026-10-26T00:00:00+00:00")
        evidence.record_snapshot(store, **common, evidence=new, observed_at=DETECTED)
    with store.connect() as db:
        db.execute("CREATE TABLE observer_state(id INTEGER PRIMARY KEY,mode TEXT,real_orders_sent INTEGER)")
        db.execute("INSERT INTO observer_state VALUES(1,'PRODUCTION_PAPER',0)")
        rows = db.execute("SELECT * FROM contract_evidence_v2_changes WHERE status='CHANGED_REVIEW_REQUIRED'").fetchall()
    changes = [{key: row[key] for key in evidence._CHANGE_COLUMNS} for row in rows]
    return store, changes


def review(store, changes, **overrides):
    args = dict(changes=changes, reviewed_at=REVIEWED, reviewer="CODEX:RC6-A198",
                policy_source_commit=evidence._FEE_POLICY_SOURCE_COMMIT,
                source_run_id=40992555376, rationale_ref=REF)
    args.update(overrides)
    return evidence.review_caucion_fee_authority_changes(store, **args)


def pending(store):
    with store.connect() as db:
        return evidence.pending_material_changes(db)


def audit_rows(store):
    with store.connect() as db:
        return {table: [tuple(r) for r in db.execute("SELECT * FROM " + table)]
                for table in ("contract_evidence_v2_snapshots", "contract_evidence_v2_current",
                              "contract_evidence_v2_changes", "observer_state")}


def test_explicit_five_identity_review_is_append_only_and_idempotent(tmp_path):
    store, changes = fixture(tmp_path, ("PESOS1", "PESOS2", "PESOS7", "PESOS30", "PESOS120"))
    before = audit_rows(store)
    assert len(pending(store)) == 5  # recording corrected policy alone never acknowledges it
    result = review(store, changes)
    assert result["reviewed"] == result["appended"] == 5
    assert result["decision_effect"] == "EXACT_CHANGE_REVIEW_ONLY"
    assert result["real_routes"] == []
    assert pending(store) == set()
    assert audit_rows(store) == before  # original CHANGED_REVIEW_REQUIRED rows survive
    assert review(store, changes)["appended"] == 0
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM contract_evidence_v2_reviews").fetchone()[0] == 5
        statements = []
        db.set_trace_callback(statements.append)
        assert evidence.pending_material_changes(db) == set()
        assert all(s.lstrip().upper().startswith("SELECT") for s in statements)


def test_public_reconstruction_matches_exact_pesos7_runtime_hash_pair():
    # Hashes from read-only Action 40992555376. No runtime prices/account data.
    assert evidence.evidence_hash(policy("PESOS7", evidence._FEE_AUTHORITY_OLD)) == (
        "b37910f21f7e1ff73cc754ce40aab51d1fb9b991fd3a90075a97f58130968166")
    assert evidence.evidence_hash(policy("PESOS7", evidence._FEE_AUTHORITY_NEW)) == (
        "184c6225d8d6812b91eb48bd171d6919f495b5a16521132e50212579e3057a4f")


@pytest.mark.parametrize("field,value", [
    ("previous_hash", "0" * 64), ("current_hash", "0" * 64),
    ("ticker", "PESOS1"), ("currency", "USD_MEP"),
    ("market", "OTHER"), ("settlement", "A-24HS"),
    ("source_class", "PPI_STRUCTURED_API"), ("detected_at", REVIEWED),
])
def test_exact_manifest_mismatch_does_not_write_a_review(tmp_path, field, value):
    store, changes = fixture(tmp_path)
    before = audit_rows(store)
    changes[0][field] = value
    with pytest.raises(ValueError, match="EXACT_TRANSITION_REQUIRED"):
        review(store, changes)
    assert audit_rows(store) == before and len(pending(store)) == 1


@pytest.mark.parametrize("options", [
    {"identity": {"currency": "USD_MEP"}},
    {"identity": {"source_class": "PPI_STRUCTURED_API"}},
    {"source_ref": "OTHER_SOURCE"},
    {"payload_change": {"minimum_principal": "1"}},
    {"payload_change": {"term_days": "1"}},
    {"payload_change": {"commission_rate": "0.001"}},
    {"payload_change": {"dynamic_execution_gate": "NONE"}},
    {"payload_change": {"policy_scope": "REAL_TRADING"}},
    {"old_authority": "DIFFERENT_OLD_TARIFF"},
    {"new_authority": "DIFFERENT_NEW_TARIFF"},
])
def test_general_fee_or_contract_change_cannot_use_this_review(tmp_path, options):
    store, changes = fixture(tmp_path, **options)
    with pytest.raises(ValueError, match="EXACT_TRANSITION_REQUIRED"):
        review(store, changes)
    assert len(pending(store)) == 1


@pytest.mark.parametrize("override", [
    {"reviewed_at": "2026-10-26T00:00:00+00:00"},
    {"reviewed_at": "2026-10-30T02:40:00"},
    {"reviewed_at": "2099-10-30T02:40:00+00:00"},
    {"policy_source_commit": "main"}, {"source_run_id": True},
    {"reviewer": ""}, {"rationale_ref": "approved in chat"},
])
def test_incomplete_or_future_review_provenance_rejected(tmp_path, override):
    store, changes = fixture(tmp_path)
    with pytest.raises(ValueError, match="EXACT_TRANSITION_REQUIRED"):
        review(store, changes, **override)
    assert len(pending(store)) == 1


def test_nonpaper_workspace_cannot_record_review(tmp_path):
    store, changes = fixture(tmp_path)
    with store.connect() as db:
        db.execute("UPDATE observer_state SET real_orders_sent=1")
    with pytest.raises(ValueError, match="PAPER_SAFETY_REQUIRED"):
        review(store, changes)
    assert len(pending(store)) == 1


def test_batch_with_one_bad_identity_is_atomic(tmp_path):
    store, changes = fixture(tmp_path, ("PESOS1", "PESOS7"))
    changes[1]["current_hash"] = "0" * 64
    with pytest.raises(ValueError, match="EXACT_TRANSITION_REQUIRED"):
        review(store, changes)
    assert len(pending(store)) == 2
    with store.connect() as db:
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='contract_evidence_v2_reviews'").fetchone()


@pytest.mark.parametrize("damage", ["receipt_hash", "receipt_json", "policy", "snapshot", "missing_snapshot"])
def test_corrupted_receipt_or_evidence_fails_closed(tmp_path, damage):
    store, changes = fixture(tmp_path)
    review(store, changes)
    with store.connect() as db:
        if damage == "receipt_hash":
            db.execute("UPDATE contract_evidence_v2_reviews SET receipt_hash='bad'")
        elif damage == "receipt_json":
            db.execute("UPDATE contract_evidence_v2_reviews SET receipt_json='bad'")
        elif damage == "policy":
            payload = json.loads(db.execute("SELECT receipt_json FROM contract_evidence_v2_reviews").fetchone()[0])
            payload["policy"] = "IGNORE_ALL_FEES"
            db.execute("UPDATE contract_evidence_v2_reviews SET receipt_json=?,receipt_hash=?",
                       (evidence.canonical_json(payload), evidence.evidence_hash(payload)))
        elif damage == "snapshot":
            db.execute("UPDATE contract_evidence_v2_snapshots SET evidence_json='bad' WHERE evidence_hash=?",
                       (changes[0]["current_hash"],))
        else:
            db.execute("DELETE FROM contract_evidence_v2_snapshots WHERE evidence_hash=?",
                       (changes[0]["previous_hash"],))
    assert len(pending(store)) == 1


def test_new_contract_change_remains_pending_after_exact_old_review(tmp_path):
    store, changes = fixture(tmp_path)
    review(store, changes)
    current = policy("PESOS7", evidence._FEE_AUTHORITY_NEW)
    current["minimum_principal"] = "200000"
    evidence.record_snapshot(store, family="CAUCIONES", ticker="PESOS7", market="BYMA",
                             currency="ARS", settlement="INMEDIATA", source_class="DERIVED_OFFICIAL_RULE",
                             source_ref=evidence._FEE_POLICY_SOURCE, evidence=current, observed_at=REVIEWED)
    assert len(pending(store)) == 1
    with pytest.raises(ValueError, match="EVIDENCE_MISSING"):
        review(store, changes)  # latest source changed since the reviewed cut


def test_existing_receipt_cannot_be_overwritten(tmp_path):
    store, changes = fixture(tmp_path)
    review(store, changes)
    with pytest.raises(ValueError, match="RECEIPT_CONFLICT"):
        review(store, changes, reviewer="DIFFERENT_REVIEWER")
    assert pending(store) == set()


def incident_fixture(tmp_path):
    tickers = tuple(row[1] for row in evidence._A198_REVIEWED_CHANGES)
    store, _ = fixture(tmp_path, tickers)
    catalog.init_schema(store)
    with store.connect() as db:
        for change_id, ticker, _, _ in evidence._A198_REVIEWED_CHANGES:
            # Replay incident identities and IDs in an isolated SQLite fixture.
            db.execute("UPDATE contract_evidence_v2_changes SET change_id=? "
                       "WHERE ticker=? AND status='CHANGED_REVIEW_REQUIRED'", (change_id, ticker))
            primary = catalog.normalize_record({
                "ticker": ticker, "type": "CAUCIONES", "market": "BYMA",
                "currency": "Pesos", "nominalInPrice": 1,
                "_provider_instrument_type": "CAUCIONES", "_discovery_source": "PPI_PRIMARY",
            }, "INMEDIATA", REVIEWED, "PPI-FULL-CATALOG")
            catalog.persist(db, primary)
    return store


def completed_catalog(store):
    claims = bridge.complements_from_store(store, now=datetime.fromisoformat(REVIEWED))
    with store.connect() as db:
        primaries = [dict(row) for row in db.execute("SELECT * FROM financial_instrument_catalog")]
    completed = []
    for primary in primaries:
        primary["raw"] = json.loads(primary.pop("metadata_json"))
        claim = next(claim for claim in claims if claim["ticker"] == primary["ticker"])
        completed.append(catalog.complete_with_complement(primary, claim))
    return claims, completed


def test_normal_pipeline_reviews_only_known_incident_and_reaches_static_catalog(tmp_path):
    store = incident_fixture(tmp_path)
    before = audit_rows(store)
    claims, _ = completed_catalog(store)
    assert len(claims) == 5
    assert all("CHANGE_REVIEW_REQUIRED" in c["contract_bridge"]["gaps"] for c in claims)
    first = mass.collect(store, run_id="incident-review")
    assert first["caucion_fee_authority_review"]["status"] == "APPLIED"
    assert first["caucion_fee_authority_review"]["appended"] == 5
    assert first["real_routes_used"] == []
    assert pending(store) == set()
    claims, completed = completed_catalog(store)
    assert all(c["contract_bridge"]["status"] == "NORMALIZED" for c in claims)
    assert all(c["capability"] == "READY_PAPER_CAUCION_PLACING" for c in completed)
    assert all(c["paper_caucion_contract_v1"]["fee_authority"] == evidence._FEE_AUTHORITY_NEW
               for c in claims)
    after = audit_rows(store)
    # Collection legitimately appends PPI identity snapshots; original source
    # snapshots and CHANGED_REVIEW_REQUIRED audit rows remain byte-for-byte.
    for table in ("contract_evidence_v2_snapshots", "contract_evidence_v2_changes", "observer_state"):
        assert set(before[table]) <= set(after[table])
    second = mass.collect(store, run_id="incident-review-again")
    assert second["caucion_fee_authority_review"]["status"] == "ALREADY_APPLIED"
    assert second["caucion_fee_authority_review"]["appended"] == 0
    with store.connect() as db:
        receipts = [json.loads(r[0]) for r in db.execute("SELECT receipt_json FROM contract_evidence_v2_reviews")]
        assert len(receipts) == 5
        assert all(r["source_run_id"] == 40993210661 for r in receipts)
        assert all(r["policy_source_commit"] == "9f5a4de307a5493f1cb2c414cfbc2b1a8468efaa" for r in receipts)
        assert all(r["rationale_ref"].endswith("#issuecomment-6513673394") for r in receipts)
        # Static review creates no quote/depth and cannot manufacture an offer.
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='paper_caucion_live_book'").fetchone()


def test_known_migration_does_not_autoapprove_same_transition_on_other_change_ids(tmp_path):
    store, _ = fixture(tmp_path, tuple(row[1] for row in evidence._A198_REVIEWED_CHANGES))
    before = audit_rows(store)
    assert evidence.apply_reviewed_caucion_fee_authority_correction(store)["status"] == "NOT_APPLICABLE"
    assert len(pending(store)) == 5
    assert audit_rows(store) == before


@pytest.mark.parametrize("damage", ["partial", "hash", "timestamp", "source", "missing_snapshot", "not_paper"])
def test_known_migration_is_fail_closed_and_atomic_on_incident_mismatch(tmp_path, damage):
    store = incident_fixture(tmp_path)
    with store.connect() as db:
        if damage == "partial":
            db.execute("UPDATE contract_evidence_v2_changes SET change_id=1e6 WHERE change_id=36957")
        elif damage == "hash":
            db.execute("UPDATE contract_evidence_v2_changes SET current_hash='bad' WHERE change_id=36957")
        elif damage == "timestamp":
            db.execute("UPDATE contract_evidence_v2_changes SET detected_at=? WHERE change_id=36957", (REVIEWED,))
        elif damage == "source":
            db.execute("UPDATE contract_evidence_v2_changes SET source_class='PPI_STRUCTURED_API' WHERE change_id=36957")
        elif damage == "missing_snapshot":
            db.execute("DELETE FROM contract_evidence_v2_snapshots WHERE evidence_hash=?",
                       (evidence._A198_REVIEWED_CHANGES[0][2],))
        else:
            db.execute("UPDATE observer_state SET mode='SHADOW'")
    before = audit_rows(store)
    result = evidence.apply_reviewed_caucion_fee_authority_correction(store)
    assert result["status"] == "BLOCKED"
    assert result["appended"] == 0
    assert len(pending(store)) == 5
    assert audit_rows(store) == before
    with store.connect() as db:
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='contract_evidence_v2_reviews'").fetchone()


def test_future_material_change_still_blocks_bridge_and_catalog_after_normal_pipeline(tmp_path):
    store = incident_fixture(tmp_path)
    mass.collect(store, run_id="known-incident")
    changed = policy("PESOS7", evidence._FEE_AUTHORITY_NEW)
    changed["minimum_principal"] = "200000"
    evidence.record_snapshot(store, family="CAUCIONES", ticker="PESOS7", market="BYMA",
                             currency="ARS", settlement="INMEDIATA", source_class="DERIVED_OFFICIAL_RULE",
                             source_ref=evidence._FEE_POLICY_SOURCE, evidence=changed, observed_at=REVIEWED)
    assert evidence.apply_reviewed_caucion_fee_authority_correction(store)["status"] == "BLOCKED"
    # Even a subsequent source refresh returning to the previous values does
    # not erase the new material changes or extend the old exact review.
    mass.collect(store, run_id="later-source-refresh")
    assert pending(store) == {("CAUCIONES", "PESOS7", "BYMA", "ARS", "INMEDIATA")}
    claims, completed = completed_catalog(store)
    claim = next(c for c in claims if c["ticker"] == "PESOS7")
    assert "CHANGE_REVIEW_REQUIRED" in claim["contract_bridge"]["gaps"]
    current = next(c for c in completed if c["ticker"] == "PESOS7")
    assert current["capability"] != "READY_PAPER_CAUCION_PLACING"
    assert len([c for c in completed if c["capability"] == "READY_PAPER_CAUCION_PLACING"]) == 4
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM contract_evidence_v2_reviews").fetchone()[0] == 5
