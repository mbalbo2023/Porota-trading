import json
from decimal import Decimal

from be_paper_engine import PaperBroker, PaperStore, Quote
from bs_instrument_contracts import InstrumentContract
from rc6_paper_family_lifecycle import future_positions


D = Decimal


def contract():
    return InstrumentContract(
        "DLR/OCT26", "FUTUROS", "ARS", "A3", "INMEDIATA",
        D("1000"), D("1"), "CONTRACT_EVIDENCE_V2_BOUND",
        expires_at="2026-10-30T15:00:00-03:00",
        minimum_quantity=D("1"),
        paper_margin_policy="CONSERVATIVE_NOTIONAL_RATE",
        paper_margin_rate=D("1"),
        underlying="DOLAR_A3500",
    )


def quote(at, bid="1499", ask="1500", size="10", *, spec=None):
    return Quote(
        "DLR/OCT26", "FUTUROS", "INMEDIATA",
        D(bid), D(bid), D(ask), D(size), D(size), at,
        contract=spec if spec is not None else contract(),
        currency="ARS", market="A3", metadata_source="PPI_CATALOG:TEST",
        book_at=at, trade_at=at, last_kind="TRADE",
    )


def broker(tmp_path, clock):
    store = PaperStore(str(tmp_path / "paper.sqlite"))
    return PaperBroker(
        store, initial_cash="10000000", risk_pct="0.05",
        max_positions=10, participation="1",
        max_position_pct="1", max_total_exposure_pct="1",
        clock_fn=lambda: clock[0], session_policy=None,
        require_supervisor=False, ai_mode="OFF", economics_mode="SHADOW",
        quote_max_age_seconds=120, trade_max_age_seconds=900,
        daily_loss_pct="5", daily_soft_stop_pct="4",
    )


def test_future_open_mark_eod_close_uses_specialized_ledger_and_no_real_routes(tmp_path):
    clock = ["2026-10-05T12:00:00-03:00"]
    b = broker(tmp_path, clock)
    q = quote(clock[0])
    opened, reason, lifecycle_id = b._open_future(q, D("0.80"), {"samples": 8})
    assert opened is True
    assert reason == "FUTURES_PAPER_OPENED_SIMULATED"
    assert lifecycle_id.startswith("PAPER-FUT-")
    assert b.store.open_position("DLR/OCT26") is None

    rows = future_positions(b.store, "ARS", active_only=True)
    assert len(rows) == 1
    assert rows[0]["market"] == "A3"
    assert rows[0]["status"] == "ACTIVE"
    assert D(rows[0]["margin_reserved"]) > 0
    assert D(b._cash(as_of=clock[0], currency="ARS")) < D("10000000")

    clock[0] = "2026-10-05T13:00:00-03:00"
    b._on_future_quote(quote(clock[0], bid="1520", ask="1521"))
    active = future_positions(b.store, "ARS", active_only=True)
    assert len(active) == 1
    assert D(active[0]["unrealized_pnl"]) > 0
    risk = b.daily_risk.evaluate(clock[0], quotes={"DLR/OCT26": quote(clock[0], bid="1520", ask="1521")})
    assert risk["ARS"]["state"] == "READY"

    clock[0] = "2026-10-05T14:50:00-03:00"
    b._on_future_quote(quote(clock[0], bid="1520", ask="1521"))
    assert future_positions(b.store, "ARS", active_only=True) == []
    closed = future_positions(b.store, "ARS")
    assert len(closed) == 1
    assert closed[0]["status"] == "CLOSED"
    metadata = json.loads(closed[0]["metadata_json"])
    assert metadata["terminal_state"] == "CLOSE"
    assert D(metadata["realized_pnl"]) != 0

    with b.store.connect() as c:
        events = [dict(row) for row in c.execute(
            "SELECT * FROM paper_family_lifecycle_events WHERE family='FUTUROS'")]
    assert events
    for event in events:
        detail = json.loads(event["detail_json"] or "{}")
        if "real_routes_used" in detail:
            assert detail["real_routes_used"] == []


def test_future_daily_risk_uses_marks_and_fails_stale(tmp_path):
    clock = ["2026-10-05T12:00:00-03:00"]
    b = broker(tmp_path, clock)
    q = quote(clock[0])
    opened, _, _ = b._open_future(q, D("0.80"), {"samples": 8})
    assert opened

    clock[0] = "2026-10-05T12:01:00-03:00"
    down = quote(clock[0], bid="1490", ask="1491")
    b._on_future_quote(down)
    ready = b.daily_risk.evaluate(clock[0], quotes={"DLR/OCT26": down})["ARS"]
    assert ready["state"] in {"READY", "LATCHED"}
    assert D(ready["daily_pnl"]) < 0

    # No fabricated mark: once the last A3/PPI book ages beyond the configured
    # TTL, the risk state blocks new admissions instead of carrying price.
    clock[0] = "2026-10-05T12:04:00-03:00"
    stale = b.daily_risk.evaluate(clock[0], quotes={})["ARS"]
    assert stale["state"] in {"STALE_MARKS", "LATCHED"}


def test_future_binding_economics_fails_closed_because_exact_fixed_costs_are_not_proved(tmp_path):
    clock = ["2026-10-05T12:00:00-03:00"]
    b = broker(tmp_path, clock)
    b.economics_mode = "BINDING"
    opened, reason, lifecycle_id = b._open_future(
        quote(clock[0]), D("0.80"), {"samples": 8})
    assert opened is False
    assert reason == "FUTURES_EXACT_COST_MODEL_REQUIRED_FOR_BINDING_ECONOMICS"
    assert lifecycle_id is None


def test_future_contract_can_only_be_restored_for_exit_not_new_entry(tmp_path):
    clock = ["2026-10-05T12:00:00-03:00"]
    b = broker(tmp_path, clock)
    opened, _, _ = b._open_future(quote(clock[0]), D("0.80"), {"samples": 8})
    assert opened

    clock[0] = "2026-10-05T14:50:00-03:00"
    no_live_contract = quote(clock[0], bid="1510", ask="1511", spec=None)
    no_live_contract = Quote(
        no_live_contract.symbol, no_live_contract.asset_class, no_live_contract.settlement,
        no_live_contract.last, no_live_contract.bid, no_live_contract.ask,
        no_live_contract.bid_size, no_live_contract.ask_size, no_live_contract.observed_at,
        contract=None, currency="ARS", market="A3",
        metadata_source="PPI_CATALOG:TEST",
        book_at=clock[0], trade_at=clock[0], last_kind="TRADE")
    b._on_future_quote(no_live_contract)
    assert future_positions(b.store, "ARS", active_only=True) == []
