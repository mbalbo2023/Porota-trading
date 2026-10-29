"""Exact FCI strategy inputs never become runtime entry authorization."""
from dataclasses import replace
from decimal import Decimal

import pytest

from rc6_fund_paper_allocator import FundAllocationBudget, FundAllocationEvidence, FundNavMark
from rc6_fund_paper_reconciler import FundClassEvidence
from rc6_fund_paper_strategy import (
    PAPER_ONLY, RUNTIME_ENTRY_AUTHORITY, REAL_ROUTES_USED,
    FundCashStrategyRisk, FundSourceBinding, assess_paper_fund_strategy,
    evidence_binding_sha256,
)
from rc6_paper_family_lifecycle import PaperFundTerms


STAMP = "2026-10-27T12:00:00+00:00"  # 09:00 ART; the fund class cutoff is later.
SHA = "a" * 64


def inputs():
    terms = PaperFundTerms(
        "FCI.TEST.C1", "FCI", "ARS", "FCI", "INMEDIATA",
        "PPI_AUTHENTICATED_CLASS_TERMS", subscription_min=Decimal("1000"),
        subscription_step=Decimal("0.01"),
        broker_subscription_min="PPI_AUTHENTICATED_EXACT_ARS_1000",
        broker_subscription_step="PPI_AUTHENTICATED_EXACT_ARS_0_01")
    ce = FundClassEvidence(terms, terms.symbol, "PPI_AUTHENTICATED_CLASS_RULES", SHA,
                           "2026-10-26T18:00:00+00:00")
    nav = FundNavMark(Decimal("12.5"), "2026-10-26T16:00:00+00:00",
                      "2026-10-26T18:00:00+00:00", "2026-10-26T18:01:00+00:00",
                      "PPI_AUTHENTICATED_OFFICIAL_NAV", SHA)
    evidence = FundAllocationEvidence(
        ce, "PPI_AUTHENTICATED", "2026-10-27", "PPI_AUTHENTICATED_CALENDAR",
        SHA, "2026-10-26T19:00:00+00:00", "14:30",
        "America/Argentina/Buenos_Aires", 2, Decimal("0"),
        "PPI_AUTHENTICATED_EXACT_ACCOUNT_FEE", SHA,
        "2026-10-26T19:00:00+00:00", nav)
    budget = FundAllocationBudget(Decimal("2000"), Decimal("250"),
                                  Decimal("1500"), Decimal("1000"), 4, 3)
    proof = FundSourceBinding(terms.symbol, evidence.provenance,
                              evidence_binding_sha256(evidence),
                              "2026-10-27T11:59:00+00:00",
                              "TRUSTED_UPSTREAM_ATTESTATION_TEST")
    risk = FundCashStrategyRisk(Decimal("200"), Decimal("250"), 600, Decimal("0.6"))
    return evidence, budget, proof, risk


def test_research_candidate_never_authorizes_runtime_or_broker():
    e, b, proof, risk = inputs()
    result = assess_paper_fund_strategy(e, b, proof, risk, as_of=STAMP)
    assert result.status == "PAPER_STRATEGY_CANDIDATE"
    assert result.reasons == ()
    assert result.class_key == "FCI.TEST.C1"
    assert result.amount_ars == Decimal("1000")
    assert result.source_binding_sha256 == proof.binding_sha256
    assert result.paper_only is PAPER_ONLY is True
    assert result.entry_authority is RUNTIME_ENTRY_AUTHORITY is False
    assert result.real_routes_used == REAL_ROUTES_USED == ()


@pytest.mark.parametrize("change,code", [
    (lambda e, b, p, r: (replace(e, provenance="UNVERIFIED_EXTERNAL"), b, p, r),
     "FCI_STRATEGY_AUTHENTICATED_SOURCE_REQUIRED"),
    (lambda e, b, p, r: (e, b, None, r), "FCI_STRATEGY_UPSTREAM_RECEIPT_MISSING"),
    (lambda e, b, p, r: (e, b, replace(p, class_key="FCI.OTHER"), r),
     "FCI_STRATEGY_RECEIPT_IDENTITY_OR_PAYLOAD_MISMATCH"),
    (lambda e, b, p, r: (replace(e, last_nav=replace(e.last_nav,
                                                    nav_per_unit=Decimal("14"))), b, p, r),
     "FCI_STRATEGY_RECEIPT_IDENTITY_OR_PAYLOAD_MISMATCH"),
    (lambda e, b, p, r: (e, b, replace(p, observed_at="2026-10-26T11:59:00+00:00"), r),
     "FCI_STRATEGY_SOURCE_RECEIPT_STALE_OR_FUTURE"),
    (lambda e, b, p, r: (e, b, replace(p, observed_at="2026-10-27T12:01:00+00:00"), r),
     "FCI_STRATEGY_SOURCE_RECEIPT_STALE_OR_FUTURE"),
    (lambda e, b, p, r: (e, b, replace(p, authenticated_adapter_id=""), r),
     "FCI_STRATEGY_SOURCE_ADAPTER_MISSING"),
    (lambda e, b, p, r: (e, b, p, replace(r, known_liabilities_ars=Decimal("900"))),
     "FCI_STRATEGY_LIABILITY_OR_EXPOSURE_LIMIT"),
    (lambda e, b, p, r: (e, b, p, replace(r, max_fund_exposure_fraction=Decimal("0.4"))),
     "FCI_STRATEGY_LIABILITY_OR_EXPOSURE_LIMIT"),
    (lambda e, b, p, r: (e, b, p, replace(r, max_source_age_seconds=90000)),
     "FCI_STRATEGY_SOURCE_AGE_POLICY_INVALID"),
    (lambda e, b, p, r: (replace(e, subscription_fee_ars=Decimal("5")), b, p, r),
     "FCI_NONZERO_FEE_NOT_MODELED_IN_LEDGER"),
    (lambda e, b, p, r: (replace(e, cutoff_local_hhmm="09:00"), b, p, r),
     "FCI_CLASS_CUTOFF_PASSED"),
    (lambda e, b, p, r: (replace(e, last_nav=None), b, p, r),
     "FCI_NAV_POINT_IN_TIME_MISSING"),
    (lambda e, b, p, r: (e, b, p, replace(r, max_fund_exposure_fraction=Decimal("1.1"))),
     "FCI_STRATEGY_RISK_LIMITS_INVALID"),
])
def test_missing_stale_nonzero_fee_forged_receipt_and_risk_hold(change, code):
    result = assess_paper_fund_strategy(*change(*inputs()), as_of=STAMP)
    assert result.status == "HOLD"
    assert result.amount_ars is None and result.entry_authority is False
    assert code in result.reasons


def test_synthetic_fixture_not_misclassified_as_live_even_with_self_digest():
    e, b, proof, risk = inputs()
    synthetic = replace(e, provenance="SYNTHETIC_TEST_ONLY")
    claimed = replace(proof, source_kind=synthetic.provenance,
                      binding_sha256=evidence_binding_sha256(synthetic))
    result = assess_paper_fund_strategy(synthetic, b, claimed, risk, as_of=STAMP)
    assert result.status == "HOLD"
    assert "FCI_STRATEGY_AUTHENTICATED_SOURCE_REQUIRED" in result.reasons


def test_broker_terms_must_be_exact_not_internal_synthetic_policy():
    e, b, proof, risk = inputs()
    old = replace(e.class_evidence.terms,
                  paper_subscription_policy="INTERNAL_RISK_BUDGET_BY_AMOUNT",
                  subscription_min=None, subscription_step=None,
                  broker_subscription_min="NO_VERIFICADO", broker_subscription_step="NO_VERIFICADO")
    changed = replace(e, class_evidence=replace(e.class_evidence, terms=old))
    updated = replace(proof, binding_sha256=evidence_binding_sha256(changed))
    result = assess_paper_fund_strategy(changed, b, updated, risk, as_of=STAMP)
    assert result.status == "HOLD"
    assert "FCI_STRATEGY_BROKER_AMOUNT_TERMS_UNVERIFIED" in result.reasons


def test_missing_evidence_and_invalid_clock_fail_closed():
    _, b, proof, risk = inputs()
    decision = assess_paper_fund_strategy(None, b, proof, risk, as_of=STAMP)
    assert decision.status == "HOLD"
    with pytest.raises(ValueError, match="FCI_STRATEGY_AS_OF_INVALID"):
        assess_paper_fund_strategy(None, b, proof, risk, as_of="unknown")
