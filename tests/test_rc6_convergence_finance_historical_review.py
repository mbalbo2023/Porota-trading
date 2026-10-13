"""Independent semantic attacks on synthetic historical package evidence."""
from datetime import timedelta
from decimal import Decimal
import hashlib
import json

import pytest

from rc6_audit_evidence import EvidenceError, verify_package
from rc6_audit_evidence.package import canonical
from rc6_performance.common import stamp
from tests.test_issue465_historical_package import export, records, rewrite, source


def test_r75_duplicate_fill_cannot_pass_when_all_public_digests_and_the_pin_are_rebound(tmp_path):
    database = source(tmp_path)
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    package = tmp_path/"duplicate-fill-package"
    export(database, package)
    rows = records(package/"fills.jsonl")
    rows[-1]["fill_id"] = rows[0]["fill_id"]
    rewrite(package, "fills.jsonl", rows)
    new_pin = hashlib.sha256((package/"manifest.json").read_bytes()).hexdigest()
    with pytest.raises(EvidenceError, match="DUPLICATE_OR_INVALID_FILL"):
        verify_package(package, expected_manifest_sha256=new_pin)
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before


def test_r76_fill_one_microsecond_before_entry_is_rejected_despite_complete_semantic_rehash(tmp_path):
    database = source(tmp_path, include_second_currency=False)
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    package = tmp_path/"clock-order-package"
    export(database, package)
    position = records(package/"positions.jsonl")[0]
    rows = records(package/"fills.jsonl")
    rows[0]["filled_at"] = (stamp(position["entry_at"])-timedelta(microseconds=1)).isoformat()
    rewrite(package, "fills.jsonl", rows)
    new_pin = hashlib.sha256((package/"manifest.json").read_bytes()).hexdigest()
    with pytest.raises(EvidenceError, match="FILL_CLOCK_ORDER"):
        verify_package(package, expected_manifest_sha256=new_pin)
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before


@pytest.mark.parametrize("mutation,reason", [
    ("entry_cost", "LEDGER_COST_RECONCILIATION"),
    ("opening_quantity", "OPENING_QUANTITY_MISMATCH"),
    ("fill_cost", "LEDGER_COST_RECONCILIATION"),
])
def test_r74_inconsistent_cost_or_quantity_is_rejected_after_all_public_hashes_and_pin_are_rebound(
        tmp_path, mutation, reason):
    database = source(tmp_path, include_second_currency=False)
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    package = tmp_path / "inconsistent-financial-package"
    original = export(database, package)
    control = verify_package(package, expected_manifest_sha256=original["manifest_sha256"])
    assert control["status"] == "RECOMPUTED"
    assert control["currencies"]["ARS"]["net_pnl"] == "3"

    if mutation == "fill_cost":
        rows = records(package / "fills.jsonl")
        opening = next(row for row in rows if row["side"] == "BUY_SIMULATED")
        opening["costs"] = str(Decimal(opening["costs"]) + 1)
        # Keep the charge's own component declaration consistent. Rejection
        # must come from independent ledger reconciliation, not its row hash.
        opening["cost_components"]["aggregate_explicit_charge"] = opening["costs"]
        rewrite(package, "fills.jsonl", rows)
    else:
        rows = records(package / "positions.jsonl")
        if mutation == "entry_cost":
            rows[0]["ledger"]["entry_cost"] = str(Decimal(rows[0]["ledger"]["entry_cost"]) + 1)
        else:
            rows[0]["position_quantity"] = str(Decimal(rows[0]["position_quantity"]) + 1)
        rewrite(package, "positions.jsonl", rows)
    new_pin = hashlib.sha256((package / "manifest.json").read_bytes()).hexdigest()
    assert new_pin != original["manifest_sha256"]
    with pytest.raises(EvidenceError, match=reason):
        verify_package(package, expected_manifest_sha256=new_pin)
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before


def test_r78_coordinated_currency_relabel_rejects_original_pin_and_retains_unknown_source_with_rebound_pin(
        tmp_path):
    database = source(tmp_path, include_second_currency=False)
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    package = tmp_path / "currency-trust-boundary-package"
    original = export(database, package)
    original_pin = original["manifest_sha256"]
    control = verify_package(package, expected_manifest_sha256=original_pin)
    assert control["status"] == "RECOMPUTED" and set(control["currencies"]) == {"ARS"}
    rows = records(package / "positions.jsonl")
    commitment, position_id = rows[0]["source_row_commitment"], rows[0]["position_id"]
    rows[0]["currency"] = "USD_MEP"
    rewrite(package, "positions.jsonl", rows)
    manifest_path = package / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["currencies"] = ["USD_MEP"]
    manifest_path.write_text(canonical(manifest) + "\n")
    rebound_pin = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    assert rebound_pin != original_pin
    with pytest.raises(EvidenceError, match="MANIFEST_DIGEST"):
        verify_package(package, expected_manifest_sha256=original_pin)

    # A writer controlling both sanitized rows and the supplied external pin
    # can relabel a supported currency. No FX transaction or source authority
    # is established by the new envelope's internally consistent arithmetic.
    checked = verify_package(package, expected_manifest_sha256=rebound_pin)
    assert checked["status"] == "RECOMPUTED"
    assert set(checked["currencies"]) == {"USD_MEP"}
    assert checked["currencies"]["USD_MEP"]["net_pnl"] == control["currencies"]["ARS"]["net_pnl"]
    assert records(package / "positions.jsonl")[0]["source_row_commitment"] == commitment
    assert records(package / "positions.jsonl")[0]["position_id"] == position_id
    assert checked["source_authentication"] == "EXTERNAL_EVIDENCE_PENDING_UNTIL_SOURCE_OWNER_RECONCILIATION"
    assert checked["economic_edge_validated"] is False
    assert checked["currency_totals_combined"] is False
    assert checked["real_orders_sent"] == 0 and checked["real_routes"] == "NOT_CALLED"
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
