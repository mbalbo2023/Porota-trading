from datetime import datetime, timedelta

from be_paper_engine import PaperBroker, PaperStore
from rc6_caucion_fresh_data_agent import DEFAULT_EXPECTED_TICKERS, evaluate_caucion_fresh_data_agent
from rc6_caucion_offer_adapter import CANONICAL_SCHEMA
from rc6_caucion_paper_bridge import run_verified_caucion_paper_cycle
from rc6_paper_obligation_snapshot import build_paper_only_obligation_snapshot

NOW = datetime.fromisoformat("2026-09-14T15:00:00-03:00")
START = datetime.fromisoformat("2026-09-14T14:30:00-03:00")
CUTOFF = datetime.fromisoformat("2026-09-14T16:30:00-03:00")
DEADLINE = datetime.fromisoformat("2026-09-15T16:30:00-03:00")


def canonical(ticker, *, observed_at=None):
    prefix = "PESOS" if ticker.startswith("PESOS") else "DOLAR"
    days = int(ticker[len(prefix):])
    currency = "ARS" if prefix == "PESOS" else "USD_MEP"
    minimum = "100000" if currency == "ARS" else "100"
    available = "100000" if currency == "ARS" else "1000"
    fee_principal = minimum
    return {
        "schema": CANONICAL_SCHEMA,
        "semantics_status": "VALIDATED",
        "semantic_proof": {
            "rate": "TNA_FRACTION_VALIDATED",
            "depth": "COLOCADORA_EXECUTABLE_PRINCIPAL_VALIDATED",
            "side": "COLOCADORA_SIDE_VALIDATED",
            "fees": "TOTAL_FEES_FOR_PRINCIPAL_VALIDATED",
            "maturity": "MATURITY_EXPLICIT_VALIDATED",
            "freshness": "PROVIDER_OBSERVED_AT_VALIDATED",
        },
        "ticker": ticker,
        "provider_instrument_id": "ppi-" + ticker.lower(),
        "market": "BYMA",
        "currency": currency,
        "settlement": "INMEDIATA",
        "side": "COLOCADORA",
        "operation": "COLOCAR-CAUCION",
        "term_days": days,
        "operable": True,
        "market_session_state": "OPEN",
        "annual_rate_fraction": "0.35",
        "available_principal": available,
        "principal_min": minimum,
        "principal_step": "1",
        "day_count_basis": 365,
        "fee_payment": "MATURITY",
        "quoted_total_fees": "10",
        "fee_quote_principal": fee_principal,
        "start_date": NOW.date().isoformat(),
        "maturity_at": (NOW + timedelta(days=days)).isoformat(),
        "observed_at": (observed_at or (NOW - timedelta(seconds=20))).isoformat(),
        "expiry_at": (NOW + timedelta(minutes=10)).isoformat(),
        "metadata_source": "PPI_API_AUTHENTICATED",
        "evidence_id": "e2e-" + ticker.lower(),
    }


def snapshots():
    return [canonical(t) for t in DEFAULT_EXPECTED_TICKERS]


def contract_statuses():
    return {t: "READY_PAPER_CANDIDATE" for t in DEFAULT_EXPECTED_TICKERS}


def fresh_gate(values):
    return evaluate_caucion_fresh_data_agent(
        values,
        now=NOW,
        heartbeat_at=(NOW - timedelta(seconds=5)).isoformat(),
        calendar_state="OPEN",
        cutoff_state="OPEN",
        contract_status_by_ticker=contract_statuses(),
    )


def paper_broker(tmp_path):
    return PaperBroker(
        PaperStore(str(tmp_path / "caucion-e2e.db")),
        initial_cash="1000000",
        daily_loss_pct="1",
        clock_fn=lambda: NOW,
        participation="1",
    )


def test_full_green_chain_places_only_simulated_caucion_and_keeps_real_orders_zero(tmp_path):
    broker = paper_broker(tmp_path)
    values = snapshots()
    gate = fresh_gate(values)
    assert gate["green"] is True
    obligations = build_paper_only_obligation_snapshot(
        broker, as_of=NOW, currency="ARS", liquidity_deadline=DEADLINE
    )
    assert obligations.complete is True

    result = run_verified_caucion_paper_cycle(
        broker,
        values,
        freshness_gate=gate,
        obligation_snapshot=obligations,
        currency="ARS",
        as_of=NOW,
        sweep_start_at=START,
        order_cutoff_at=CUTOFF,
        liquidity_deadline=DEADLINE,
        schedule_source="TEST_VERIFIED_BYMA_CAUCION_WINDOW",
        request_id="rc6-e2e-green-001",
        participation="1",
        max_quote_age_seconds=30,
    )

    assert result["status"] == "PLACED_SIMULATED"
    assert result["routing_allowed"] is False
    assert result["real_order_capability"] is False
    assert result["allocation"]["paper_id"].startswith("PAPER-")
    positions = broker.cauciones.positions()
    assert len(positions) == 1
    assert positions[0]["instrument_id"] == "PESOS1"
    assert positions[0]["source"] == "PRODUCTION_PAPER"
    with broker.store.connect() as c:
        state = c.execute("SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchone()
        assert state["mode"] == "PRODUCTION_PAPER"
        assert int(state["real_orders_sent"]) == 0


def test_stale_dynamic_turns_agent_red_and_bridge_holds_without_paper_placement(tmp_path):
    broker = paper_broker(tmp_path)
    values = snapshots()
    values[0] = canonical(DEFAULT_EXPECTED_TICKERS[0], observed_at=NOW - timedelta(seconds=301))
    gate = fresh_gate(values)
    assert gate["green"] is False
    obligations = build_paper_only_obligation_snapshot(
        broker, as_of=NOW, currency="ARS", liquidity_deadline=DEADLINE
    )
    result = run_verified_caucion_paper_cycle(
        broker,
        values,
        freshness_gate=gate,
        obligation_snapshot=obligations,
        currency="ARS",
        as_of=NOW,
        sweep_start_at=START,
        order_cutoff_at=CUTOFF,
        liquidity_deadline=DEADLINE,
        schedule_source="TEST_VERIFIED_BYMA_CAUCION_WINDOW",
        request_id="rc6-e2e-stale-001",
        participation="1",
        max_quote_age_seconds=30,
    )
    assert result["status"] == "HOLD"
    assert result["code"] == "FRESHNESS_GATE_RED"
    assert broker.cauciones.positions() == []


def test_incomplete_obligations_hold_even_with_green_freshness(tmp_path):
    broker = paper_broker(tmp_path)
    values = snapshots()
    gate = fresh_gate(values)
    with broker.store.connect() as c:
        c.execute("UPDATE observer_state SET real_orders_sent=1 WHERE id=1")
    obligations = build_paper_only_obligation_snapshot(
        broker, as_of=NOW, currency="ARS", liquidity_deadline=DEADLINE
    )
    assert obligations.complete is False
    result = run_verified_caucion_paper_cycle(
        broker,
        values,
        freshness_gate=gate,
        obligation_snapshot=obligations,
        currency="ARS",
        as_of=NOW,
        sweep_start_at=START,
        order_cutoff_at=CUTOFF,
        liquidity_deadline=DEADLINE,
        schedule_source="TEST_VERIFIED_BYMA_CAUCION_WINDOW",
        request_id="rc6-e2e-obligation-red-001",
        participation="1",
        max_quote_age_seconds=30,
    )
    assert result["status"] == "HOLD"
    assert result["code"] == "OBLIGATION_SNAPSHOT_INCOMPLETE"
    assert broker.cauciones.positions() == []
