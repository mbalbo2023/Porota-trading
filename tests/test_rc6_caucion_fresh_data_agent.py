from datetime import datetime, timedelta

from rc6_caucion_fresh_data_agent import (
    DEFAULT_EXPECTED_TICKERS,
    GATE_NAME,
    evaluate_caucion_fresh_data_agent,
)
from rc6_caucion_offer_adapter import CANONICAL_SCHEMA

NOW = datetime.fromisoformat("2026-09-14T15:00:00-03:00")


def canonical(ticker):
    prefix = "PESOS" if ticker.startswith("PESOS") else "DOLAR"
    days = int(ticker[len(prefix):])
    currency = "ARS" if prefix == "PESOS" else "USD_MEP"
    minimum = "100000" if currency == "ARS" else "100"
    available = "500000" if currency == "ARS" else "1000"
    fee_principal = minimum
    maturity = NOW + timedelta(days=days)
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
        "quoted_total_fees": "1",
        "fee_quote_principal": fee_principal,
        "start_date": NOW.date().isoformat(),
        "maturity_at": maturity.isoformat(),
        "observed_at": (NOW - timedelta(seconds=20)).isoformat(),
        "expiry_at": (NOW + timedelta(minutes=10)).isoformat(),
        "metadata_source": "PPI_API_AUTHENTICATED",
        "evidence_id": "ev-" + ticker.lower(),
    }


def statuses(value="READY_PAPER_CANDIDATE"):
    return {ticker: value for ticker in DEFAULT_EXPECTED_TICKERS}


def evaluate(snapshots=None, **changes):
    params = dict(
        canonical_snapshots=snapshots or [canonical(t) for t in DEFAULT_EXPECTED_TICKERS],
        now=NOW,
        heartbeat_at=(NOW - timedelta(seconds=10)).isoformat(),
        calendar_state="OPEN",
        cutoff_state="OPEN",
        contract_status_by_ticker=statuses(),
    )
    params.update(changes)
    return evaluate_caucion_fresh_data_agent(**params)


def test_all_ten_fresh_validated_identities_turn_aggregate_gate_green():
    result = evaluate()
    assert result["name"] == GATE_NAME
    assert result["green"] is True
    assert result["contract_status"] == "READY_PAPER_CANDIDATE"
    assert result["real_order_capability"] is False
    assert result["reasons"] == []
    assert set(result["tickers"]) == set(DEFAULT_EXPECTED_TICKERS)


def test_missing_identity_is_red():
    snapshots = [canonical(t) for t in DEFAULT_EXPECTED_TICKERS[:-1]]
    result = evaluate(snapshots)
    assert result["green"] is False
    assert any(reason.startswith("MISSING_TICKER:") for reason in result["reasons"])


def test_stale_dynamic_snapshot_is_red():
    snapshots = [canonical(t) for t in DEFAULT_EXPECTED_TICKERS]
    snapshots[0]["observed_at"] = (NOW - timedelta(seconds=301)).isoformat()
    result = evaluate(snapshots)
    assert result["green"] is False
    assert any(reason.startswith("INVALID_DYNAMIC:") for reason in result["reasons"])


def test_stale_heartbeat_is_red():
    result = evaluate(heartbeat_at=(NOW - timedelta(seconds=301)).isoformat())
    assert result["green"] is False
    assert "HEARTBEAT_STALE_OR_FUTURE" in result["reasons"]


def test_calendar_or_cutoff_closed_is_red():
    assert evaluate(calendar_state="CLOSED")["green"] is False
    assert evaluate(cutoff_state="CLOSED")["green"] is False


def test_one_contract_not_ready_blocks_aggregate_green():
    state = statuses()
    state[DEFAULT_EXPECTED_TICKERS[0]] = "MISSING_DYNAMIC"
    result = evaluate(contract_status_by_ticker=state)
    assert result["green"] is False
    assert any(reason.startswith("CONTRACT_NOT_READY:") for reason in result["reasons"])


def test_raw_ambiguous_field_cannot_enter_green_gate():
    snapshots = [canonical(t) for t in DEFAULT_EXPECTED_TICKERS]
    snapshots[3]["volume"] = 123456
    result = evaluate(snapshots)
    assert result["green"] is False
    assert any(reason.startswith("INVALID_DYNAMIC:") for reason in result["reasons"])
