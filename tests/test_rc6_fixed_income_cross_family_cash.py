"""A238 synthetic cross-family PAPER cash replay (never market evidence).

One durable PaperStore combines a FUTUROS margin reserve, an FCI subscription,
and a BONOS buy plus a *synthetic* economic receipt paid in USD.  The test
does not call an order route, price feed, PPI Watch, host or deployment path.
Its historical events are created *offline*; only the read-side reconciliation
is then evaluated under the configured effective PAPER runtime path.
"""
from decimal import Decimal

from rc6_fixed_income_event_ledger import apply_verified_receipt
from rc6_fixed_income_native_redemption import init_schema as init_redemption_schema
from rc6_paper_family_lifecycle import FamilyPaperExecutor, apply_paper_event
from tests import test_rc6_fixed_income_native_redemption as fi_fixture
from tests.test_rc6_future_paper_engine import broker as make_broker, contract


FUTURE_OPENED = "2026-10-28T12:00:00-03:00"
FCI_SUBSCRIBED = "2026-10-28T13:00:00+00:00"
BEFORE_FI_CASH_BOOKED = "2026-10-29T15:59:59+00:00"
BOOKED = fi_fixture.BOOKED


def test_runtime_paper_cash_reconciles_future_fci_and_cross_currency_bond_once(
        tmp_path, monkeypatch):
    # Synthetic offline source/economic rights.  The unrelated configured DB
    # keeps this deterministic fixture outside runtime entry admission while
    # the historical, non-broker events are seeded.
    runtime_path = tmp_path / "paper.sqlite"
    monkeypatch.setenv("PAPER_V17_DB_PATH", str(tmp_path / "unrelated.sqlite"))
    paper = make_broker(tmp_path, [BOOKED])

    future = FamilyPaperExecutor(paper.store).open_future(
        contract(), lifecycle_id="PAPER-FUT-A238", event_id="OPEN-A238",
        entry_price="1500", quantity="1", entry_cost="2",
        occurred_at=FUTURE_OPENED, book_at=FUTURE_OPENED,
    )
    assert future["state"] == "ACTIVE"
    assert Decimal(future["margin_reserved"]) == Decimal("1500000")
    apply_paper_event(
        paper.store, lifecycle_id="FCI-A238", event_id="FCI-A238:SUB",
        family="FCI", instrument="SYNTHETIC_FCI", currency="ARS",
        to_state="SUBSCRIBE_REQUESTED", amount="-1000",
        occurred_at=FCI_SUBSCRIBED,
        detail={"paper_only": True, "real_routes_used": [],
                "fixture": "SYNTHETIC_NO_ENTRY_AUTHORITY"},
    )

    # Reuse A228's exact synthetic lot and entitlement construction, avoiding
    # a second, potentially inconsistent hand-written issuer-payment fixture.
    fi = fi_fixture.NativeRedemptionReviewTest(
        "test_obligaciones_review_is_read_only_and_currency_explicit")
    fi.store = paper.store
    fi.family = "BONOS"
    fi._make_paper_lot()
    init_redemption_schema(paper.store)
    receipt = fi._economic_receipt()
    receipt["event"]["payment_currency"] = "USD"
    raw = fi_fixture._raw(receipt)
    apply_verified_receipt(
        paper.store, receipt_bytes=raw, approved_sha256=fi_fixture._hash(raw),
        source_bytes=fi_fixture.PAYMENT, applied_at=BOOKED,
    )

    # A238 tests the *reader* of the configured PAPER database after all
    # synthetic fixture writes.  It grants no entry/rearm/exit authority.
    monkeypatch.setenv("PAPER_V17_DB_PATH", str(runtime_path))
    expected_ars = Decimal("10000000") - Decimal("1500002") - Decimal("1000") - Decimal("100.61")
    assert expected_ars == Decimal("8498897.39")
    assert paper._cash(as_of=BEFORE_FI_CASH_BOOKED, currency="ARS") == expected_ars
    assert paper._cash(as_of=BEFORE_FI_CASH_BOOKED, currency="USD") == Decimal("0")
    assert paper._cash(as_of=BOOKED, currency="ARS") == expected_ars
    assert paper._cash(as_of=BOOKED, currency="USD") == Decimal("100")
    # The execution-facing *cash calculation* also reconciles. Admission is a
    # separate fail-closed gate; this read alone authorizes no PAPER entry.
    assert paper._cash(as_of=BOOKED, currency="ARS", for_execution=True) == expected_ars
    assert paper._cash(as_of=BOOKED, currency="USD", for_execution=True) == Decimal("100")

    with paper.store.connect() as db:
        assert tuple(db.execute(
            "SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchone()) == (
                "PRODUCTION_PAPER", 0)
        assert db.execute("SELECT COUNT(*) FROM paper_fi_cash_entries").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM paper_fills WHERE side='SELL_SIMULATED'").fetchone()[0] == 0
        assert db.execute("SELECT status FROM paper_positions WHERE paper_id='PAPER-AL30'").fetchone()[0] == "OPEN"
        assert db.execute("SELECT status FROM paper_future_positions WHERE lifecycle_id='PAPER-FUT-A238'").fetchone()[0] == "ACTIVE"
        assert db.execute(
            "SELECT COUNT(*) FROM paper_fi_native_redemption_reviews").fetchone()[0] == 0
        assert db.execute(
            "SELECT COUNT(*) FROM paper_family_lifecycle_events").fetchone()[0] == 3
    # Idempotent replay: reading cash again inserts no payments, closes no FI
    # lot, and never turns the reviewed USD principal into ARS buying power.
    assert (paper._cash(as_of=BOOKED, currency="ARS"),
            paper._cash(as_of=BOOKED, currency="USD")) == (
                Decimal("8498897.39"), Decimal("100"))
    with paper.store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM paper_fi_cash_entries").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM paper_family_lifecycle_events").fetchone()[0] == 3
        assert db.execute("SELECT real_orders_sent FROM observer_state WHERE id=1").fetchone()[0] == 0
