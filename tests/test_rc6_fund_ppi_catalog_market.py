"""Exact PPI catalog venue compatibility does not grant FCI runtime authority."""
from dataclasses import replace

import pytest

from rc6_fund_paper_allocator import assess_fund_cash_parking
from rc6_fund_paper_strategy import assess_paper_fund_strategy, evidence_binding_sha256
from tests.test_rc6_fund_paper_strategy import STAMP, inputs


@pytest.mark.parametrize("market", ["FCI", "BYMA"])
def test_exact_local_fund_venue_is_consistent_with_catalog_contract(market):
    evidence, budget, binding, risk = inputs()
    terms = replace(evidence.class_evidence.terms, market=market)
    evidence = replace(evidence, class_evidence=replace(evidence.class_evidence, terms=terms))
    binding = replace(binding, binding_sha256=evidence_binding_sha256(evidence))

    # A venue match alone never authenticates an external source or authorizes
    # the offline allocator to write to the production PAPER book.
    allocation = assess_fund_cash_parking(evidence, budget, as_of=STAMP)
    assert allocation.status == "HOLD"
    assert allocation.reason_codes == ("FCI_EXTERNAL_PROVENANCE_NOT_AUTHENTICATED",)
    assert allocation.runtime_entry_authority is False

    candidate = assess_paper_fund_strategy(evidence, budget, binding, risk, as_of=STAMP)
    assert candidate.status == "PAPER_STRATEGY_CANDIDATE"
    assert candidate.entry_authority is False
    assert candidate.real_routes_used == ()


def test_local_venue_does_not_relax_exact_class_binding():
    evidence, budget, binding, risk = inputs()
    terms = replace(evidence.class_evidence.terms, market="BYMA")
    evidence = replace(evidence, class_evidence=replace(
        evidence.class_evidence, terms=terms, class_key="ANOTHER.CLASS"))
    binding = replace(binding, class_key="ANOTHER.CLASS",
                      binding_sha256=evidence_binding_sha256(evidence))
    candidate = assess_paper_fund_strategy(evidence, budget, binding, risk, as_of=STAMP)
    assert candidate.status == "HOLD"
    assert "FCI_EXACT_CLASS_MISMATCH" in candidate.reasons
    assert candidate.entry_authority is False


def test_changed_venue_requires_a_new_matching_upstream_binding():
    evidence, budget, binding, risk = inputs()
    evidence = replace(evidence, class_evidence=replace(evidence.class_evidence,
        terms=replace(evidence.class_evidence.terms, market="BYMA")))
    candidate = assess_paper_fund_strategy(evidence, budget, binding, risk, as_of=STAMP)
    assert candidate.status == "HOLD"
    assert "FCI_STRATEGY_RECEIPT_IDENTITY_OR_PAYLOAD_MISMATCH" in candidate.reasons
    assert candidate.entry_authority is False
