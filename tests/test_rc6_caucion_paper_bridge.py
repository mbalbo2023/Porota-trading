from datetime import datetime

import pytest

import rc6_caucion_paper_bridge as bridge
from di_caucion_cash_sweep_runtime_hf6 import ObligationSnapshot
from rc6_caucion_offer_adapter import CANONICAL_SCHEMA

NOW = datetime.fromisoformat("2026-09-14T15:00:00-03:00")


def gate(**changes):
    value = {
        "name": "CAUCION_FRESH_DATA_AGENT_GREEN",
        "family": "CAUCIONES",
        "green": True,
        "contract_status": "READY_PAPER_CANDIDATE",
        "real_order_capability": False,
        "evidence_id": "fresh-001",
    }
    value.update(changes)
    return value


def snapshot(**changes):
    value = {
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
        "ticker": "PESOS1",
        "provider_instrument_id": "ppi-caucion-pesos-1",
        "market": "BYMA",
        "currency": "ARS",
        "settlement": "INMEDIATA",
        "side": "COLOCADORA",
        "operation": "COLOCAR-CAUCION",
        "term_days": 1,
        "operable": True,
        "market_session_state": "OPEN",
        "annual_rate_fraction": "0.35",
        "available_principal": "500000",
        "principal_min": "100000",
        "principal_step": "1",
        "day_count_basis": 365,
        "fee_payment": "MATURITY",
        "quoted_total_fees": "50",
        "fee_quote_principal": "100000",
        "start_date": "2026-09-14",
        "maturity_at": "2026-09-15T15:00:00-03:00",
        "observed_at": "2026-09-14T14:59:30-03:00",
        "expiry_at": "2026-09-14T15:05:00-03:00",
        "metadata_source": "PPI_API_AUTHENTICATED",
        "evidence_id": "ev-caucion-001",
    }
    value.update(changes)
    return value


def obligation(complete=True):
    return ObligationSnapshot(NOW.isoformat(), "PAPER_LEDGER_TEST", complete, ())


def kwargs(**changes):
    value = dict(
        freshness_gate=gate(),
        obligation_snapshot=obligation(),
        currency="ARS",
        as_of=NOW,
        sweep_start_at="2026-09-14T14:30:00-03:00",
        order_cutoff_at="2026-09-14T16:30:00-03:00",
        liquidity_deadline="2026-09-15T16:30:00-03:00",
        schedule_source="TEST_VERIFIED_SCHEDULE",
        request_id="paper-caucion-001",
    )
    value.update(changes)
    return value


def test_green_gate_and_canonical_snapshot_delegate_only_to_existing_paper_sweep(monkeypatch):
    called = {}
    def fake_sweep(broker, offers, **options):
        called["broker"] = broker
        called["offers"] = offers
        called["options"] = options
        return {"status": "PLACED_SIMULATED", "code": "TEST_ONLY", "allocation": {"paper_id": "PAPER-1"}}
    monkeypatch.setattr(bridge, "run_paper_sweep", fake_sweep)
    broker = object()
    result = bridge.run_verified_caucion_paper_cycle(broker, [snapshot()], **kwargs())
    assert result["status"] == "PLACED_SIMULATED"
    assert result["real_order_capability"] is False
    assert result["routing_allowed"] is False
    assert result["canonical_offer_count"] == 1
    assert called["broker"] is broker
    assert called["offers"][0].instrument_id == "PESOS1"
    assert called["offers"][0].annual_rate_fraction == bridge.offer_from_canonical_snapshot(snapshot(), now=NOW).annual_rate_fraction


@pytest.mark.parametrize("bad_gate,code", [
    (None, "FRESHNESS_GATE_MISSING"),
    (gate(green=False), "FRESHNESS_GATE_RED"),
    (gate(contract_status="MISSING_DYNAMIC"), "CONTRACT_GATE_NOT_READY_PAPER_CANDIDATE"),
    (gate(real_order_capability=True), "REAL_ORDER_CAPABILITY_MUST_BE_ZERO"),
    (gate(evidence_id=""), "FRESHNESS_GATE_EVIDENCE_ID_MISSING"),
])
def test_non_green_gate_never_calls_sweep(monkeypatch, bad_gate, code):
    monkeypatch.setattr(bridge, "run_paper_sweep", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run")))
    result = bridge.run_verified_caucion_paper_cycle(object(), [snapshot()], **kwargs(freshness_gate=bad_gate))
    assert result["status"] == "HOLD"
    assert result["code"] == code


def test_raw_or_ambiguous_snapshot_blocks_entire_cycle(monkeypatch):
    monkeypatch.setattr(bridge, "run_paper_sweep", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run")))
    result = bridge.run_verified_caucion_paper_cycle(object(), [snapshot(price="17.6")], **kwargs())
    assert result["status"] == "HOLD"
    assert result["code"] == "CAUCION_CANONICAL_SNAPSHOT_INVALID"


def test_duplicate_snapshot_blocks_cycle(monkeypatch):
    monkeypatch.setattr(bridge, "run_paper_sweep", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run")))
    result = bridge.run_verified_caucion_paper_cycle(object(), [snapshot(), snapshot()], **kwargs())
    assert result["status"] == "HOLD"
    assert result["code"] == "DUPLICATE_CANONICAL_CAUCION_SNAPSHOT"


def test_missing_obligation_snapshot_blocks_before_sweep(monkeypatch):
    monkeypatch.setattr(bridge, "run_paper_sweep", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run")))
    result = bridge.run_verified_caucion_paper_cycle(object(), [snapshot()], **kwargs(obligation_snapshot=None))
    assert result["status"] == "HOLD"
    assert result["code"] == "OBLIGATION_SNAPSHOT_MISSING"


def test_incomplete_obligation_snapshot_is_fail_closed_by_existing_runtime():
    class Store:
        def connect(self):
            raise AssertionError("cash must not be read before obligation completeness")
    class Broker:
        store = Store()
    result = bridge.run_verified_caucion_paper_cycle(
        Broker(), [snapshot()], **kwargs(obligation_snapshot=obligation(False))
    )
    assert result["status"] == "HOLD"
    assert result["code"] == "OBLIGATION_SNAPSHOT_INCOMPLETE"


def test_bridge_invariants_are_paper_only():
    bridge.assert_paper_bridge_invariants()
    assert bridge.PAPER_BRIDGE_ORDER_ROUTING_ALLOWED is False
