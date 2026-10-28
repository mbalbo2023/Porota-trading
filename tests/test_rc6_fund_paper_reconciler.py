"""Synthetic receipt arithmetic on the actual durable PAPER FCI lifecycle."""
from dataclasses import replace
from decimal import Decimal
import sqlite3

import pytest

from be_paper_engine import PaperBroker, PaperStore
from rc6_fund_paper_reconciler import (
    ENTRY_AUTHORITY, REAL_ROUTES_USED, FundClassEvidence, FundReceipt,
    reconcile_fund_store,
)
from rc6_paper_family_lifecycle import PaperFundTerms, apply_paper_event, fund_cash_effect


SHA = "a" * 64
CLASS = "SYNTHETIC.FUND.A"
AS_OF = "2026-10-27T12:15:00+00:00"


def setup_fund(tmp_path):
    store = PaperStore(str(tmp_path / "fund.db"))
    broker = PaperBroker(store, initial_cash="2000")
    terms = PaperFundTerms(CLASS, "FCI", "ARS", "FCI", "INMEDIATA",
                           "SYNTHETIC_EXACT_CLASS", paper_subscription_policy="INTERNAL_RISK_BUDGET_BY_AMOUNT")
    evidence = FundClassEvidence(terms, CLASS, "SYNTHETIC_REGULATION:CLASS_A",
                                 SHA, "2026-10-27T11:00:00+00:00")
    return store, broker, terms, evidence


def receipt(event_id, kind, source_kind, *, at, nav=None, units=None, fee=None,
            cash=None, nav_effective_at=None, nav_reference=None):
    return FundReceipt(event_id, kind, CLASS, "ARS", source_kind,
                       "SYNTHETIC_RECEIPT:" + event_id, SHA,
                       f"2026-10-27T12:{at:02d}:00+00:00",
                       f"2026-10-27T12:{at:02d}:30+00:00",
                       nav_effective_at=nav_effective_at, nav_reference=nav_reference,
                       nav_per_unit=nav, units=units, explicit_fee=fee,
                       cash_amount=cash)


def event(store, state, number, amount="0", detail=None):
    return apply_paper_event(store, lifecycle_id="fund-1", event_id=f"e{number}",
                             family="FCI", instrument=CLASS, currency="ARS",
                             to_state=state, amount=amount,
                             occurred_at=f"2026-10-27T12:{number * 2:02d}:00+00:00",
                             detail=detail)


def complete_cycle(tmp_path):
    store, broker, terms, evidence = setup_fund(tmp_path)
    broker.family_paper.subscribe_fund(
        terms, lifecycle_id="fund-1", event_id="e0", amount="1000",
        occurred_at="2026-10-27T12:00:00+00:00")
    event(store, "PENDING", 1)
    event(store, "NAV_APPLIED", 2)
    event(store, "SETTLED", 3)
    event(store, "REDEEM_REQUESTED", 4, detail={"units": "99"})
    event(store, "REDEEMED", 5, amount="1080")
    receipts = [
        receipt("e2", "NAV_APPLIED", "FUND_ADMINISTRATOR_NAV", at=3,
                nav=Decimal("10"), nav_effective_at="2026-10-27T12:02:00+00:00"),
        receipt("e3", "SETTLED", "BROKER_FUND_ALLOCATION", at=5,
                nav=Decimal("10"), units=Decimal("99"), fee=Decimal("10"), cash=Decimal("1000"),
                nav_effective_at="2026-10-27T12:02:00+00:00", nav_reference="SYNTHETIC_RECEIPT:e2"),
        receipt("e5", "REDEEMED", "BROKER_FUND_PAYMENT", at=9,
                nav=Decimal("11"), units=Decimal("99"), fee=Decimal("9"), cash=Decimal("1080"),
                nav_effective_at="2026-10-27T12:08:00+00:00",
                nav_reference="SYNTHETIC_REDEMPTION_NAV:e5"),
    ]
    return store, broker, evidence, receipts


def test_fci_real_paper_lifecycle_reconciles_only_with_three_distinct_receipts(tmp_path):
    store, broker, evidence, receipts = complete_cycle(tmp_path)
    result = reconcile_fund_store(store.path, "fund-1", receipts, evidence, as_of=AS_OF)
    assert (result.state, result.subscription_ars, result.units, result.nav_per_unit) == (
        "REDEEMED", Decimal("1000"), Decimal("99"), Decimal("10"))
    assert (result.reserved_cash_ars, result.redemption_cash_ars, result.net_pnl_ars) == (
        Decimal(0), Decimal("1080"), Decimal("80"))
    assert result.status == "PAPER_INPUT_RECONCILIATION_ONLY"
    assert result.entry_authority is False and ENTRY_AUTHORITY is False and REAL_ROUTES_USED == ()
    assert len(result.evidence_references) == 4
    assert fund_cash_effect(store, "ARS", AS_OF) == Decimal("80")
    assert broker._cash(AS_OF, "ARS") == Decimal("2080")
    with store.connect() as db:
        assert db.execute("SELECT real_orders_sent FROM observer_state").fetchone()[0] == 0


def test_open_fci_reserves_cash_without_inventing_nav_or_profit(tmp_path):
    store, broker, terms, evidence = setup_fund(tmp_path)
    broker.family_paper.subscribe_fund(
        terms, lifecycle_id="fund-1", event_id="e0", amount="1000",
        occurred_at="2026-10-27T12:00:00+00:00")
    result = reconcile_fund_store(store.path, "fund-1", [], evidence, as_of=AS_OF)
    assert result.state == "SUBSCRIBE_REQUESTED"
    assert result.reserved_cash_ars == Decimal("-1000")
    assert result.units is None and result.nav_per_unit is None
    assert result.net_pnl_ars is None
    assert broker._cash(AS_OF, "ARS") == Decimal("1000")


@pytest.mark.parametrize("mutation,code", [
    (lambda rs: rs[:2], "FCI_RECEIPT_IDENTITY_OR_SOURCE_MISMATCH"),
    (lambda rs: [replace(rs[0], class_key="OTHER"), *rs[1:]], "FCI_RECEIPT_IDENTITY_OR_SOURCE_MISMATCH"),
    (lambda rs: [replace(rs[0], source_at="2026-10-27T12:07:00+00:00"), *rs[1:]], "FCI_RECEIPT_PIT_INVALID"),
    (lambda rs: [rs[0], replace(rs[1], explicit_fee=None), rs[2]], "FCI_SUBSCRIPTION_FEE_UNKNOWN"),
    (lambda rs: [rs[0], replace(rs[1], units=Decimal("100")), rs[2]], "FCI_ALLOCATION_ARITHMETIC_MISMATCH"),
    (lambda rs: [rs[0], replace(rs[1], nav_reference="OTHER_NAV"), rs[2]], "FCI_ALLOCATION_NAV_OR_CASH_MISMATCH"),
    (lambda rs: [replace(rs[0], nav_effective_at="2026-10-27T12:06:00+00:00"), *rs[1:]], "FCI_NAV_EFFECTIVE_TIME_INVALID"),
    (lambda rs: [*rs[:2], replace(rs[2], cash_amount=Decimal("1081"))], "FCI_REDEMPTION_ARITHMETIC_MISMATCH"),
    (lambda rs: [*rs, rs[-1]], "FCI_RECEIPT_DUPLICATE_OR_INVALID"),
])
def test_fci_rejects_missing_mismatched_future_or_fabricated_receipt(tmp_path, mutation, code):
    store, _, evidence, receipts = complete_cycle(tmp_path)
    with pytest.raises(ValueError, match=code):
        reconcile_fund_store(store.path, "fund-1", mutation(receipts), evidence, as_of=AS_OF)


def test_fci_rejects_legacy_nav_cash_and_ambiguous_class_terms(tmp_path):
    store, _, evidence, receipts = complete_cycle(tmp_path)
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE paper_family_lifecycle_events SET amount='100' WHERE event_id='e2'")
    with pytest.raises(ValueError, match="FCI_INTERMEDIATE_EVENT_CANNOT_MOVE_CASH"):
        reconcile_fund_store(store.path, "fund-1", receipts, evidence, as_of=AS_OF)
    with pytest.raises(ValueError, match="FCI_CLASS_IDENTITY_MISMATCH"):
        reconcile_fund_store(store.path, "fund-1", receipts,
                             replace(evidence, class_key="OTHER"), as_of=AS_OF)


def test_fci_rejects_history_not_known_at_requested_cut(tmp_path):
    store, _, evidence, receipts = complete_cycle(tmp_path)
    with pytest.raises(ValueError, match="FCI_EVENT_PIT_INVALID"):
        reconcile_fund_store(store.path, "fund-1", receipts, evidence,
                             as_of="2026-10-27T12:04:30+00:00")


def test_fci_durable_root_inconsistent_with_events_is_not_accepted(tmp_path):
    store, _, evidence, receipts = complete_cycle(tmp_path)
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE paper_family_lifecycle SET ledger_total='999' WHERE lifecycle_id='fund-1'")
    with pytest.raises(ValueError, match="FCI_DURABLE_TOTAL_OR_CLOCK_MISMATCH"):
        reconcile_fund_store(store.path, "fund-1", receipts, evidence, as_of=AS_OF)


def test_fci_without_class_specific_regulation_has_no_economic_authority(tmp_path):
    store, _, evidence, receipts = complete_cycle(tmp_path)
    with pytest.raises(ValueError, match="FCI_REGULATION_REFERENCE_REQUIRED"):
        reconcile_fund_store(store.path, "fund-1", receipts,
                             replace(evidence, regulation_reference=""), as_of=AS_OF)
    with pytest.raises(ValueError, match="FCI_REGULATION_NOT_KNOWN_BEFORE_SUBSCRIPTION"):
        reconcile_fund_store(store.path, "fund-1", receipts,
                             replace(evidence, known_at="2026-10-27T12:01:00+00:00"), as_of=AS_OF)
