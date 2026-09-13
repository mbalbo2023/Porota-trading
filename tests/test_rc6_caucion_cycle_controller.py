from datetime import datetime, timedelta
from decimal import Decimal

from be_paper_engine import PaperStore
from rc6_caucion_cycle_controller import evaluate_and_persist_caucion_cycle
from rc6_caucion_fresh_data_agent import DEFAULT_EXPECTED_TICKERS
from rc6_caucion_intraday_opportunity import OpportunityPolicy, OpportunityReference
from rc6_caucion_offer_adapter import CANONICAL_SCHEMA
from rc6_caucion_readiness_state import effective_state
from rc6_caucion_schedule_gate import CaucionScheduleEvidence

NOW = datetime.fromisoformat("2026-09-14T15:00:00-03:00")
DEADLINE = NOW + timedelta(days=2)


def canonical(ticker):
    prefix = "PESOS" if ticker.startswith("PESOS") else "DOLAR"
    days = int(ticker[len(prefix):])
    currency = "ARS" if prefix == "PESOS" else "USD_MEP"
    minimum = "100000" if currency == "ARS" else "100"
    available = "500000" if currency == "ARS" else "1000"
    return {
        "schema": CANONICAL_SCHEMA,
        "semantics_status":"VALIDATED",
        "semantic_proof":{
            "rate":"TNA_FRACTION_VALIDATED",
            "depth":"COLOCADORA_EXECUTABLE_PRINCIPAL_VALIDATED",
            "side":"COLOCADORA_SIDE_VALIDATED",
            "fees":"TOTAL_FEES_FOR_PRINCIPAL_VALIDATED",
            "maturity":"MATURITY_EXPLICIT_VALIDATED",
            "freshness":"PROVIDER_OBSERVED_AT_VALIDATED",
        },
        "ticker":ticker,
        "provider_instrument_id":"ppi-"+ticker.lower(),
        "market":"BYMA",
        "currency":currency,
        "settlement":"INMEDIATA",
        "side":"COLOCADORA",
        "operation":"COLOCAR-CAUCION",
        "term_days":days,
        "operable":True,
        "market_session_state":"OPEN",
        "annual_rate_fraction":"0.40",
        "available_principal":available,
        "principal_min":minimum,
        "principal_step":"1",
        "day_count_basis":365,
        "fee_payment":"MATURITY",
        "quoted_total_fees":"5",
        "fee_quote_principal":minimum,
        "start_date":NOW.date().isoformat(),
        "maturity_at":(NOW+timedelta(days=days)).isoformat(),
        "observed_at":(NOW-timedelta(seconds=10)).isoformat(),
        "expiry_at":(NOW+timedelta(minutes=10)).isoformat(),
        "metadata_source":"PPI_API_AUTHENTICATED",
        "evidence_id":"cycle-"+ticker.lower(),
    }


def schedule(currency, *, broker_verified=True):
    day=NOW.date().isoformat()
    return CaucionScheduleEvidence(
        business_date=day,
        currency=currency,
        operation="COLOCAR-CAUCION",
        opens_at=day+"T10:30:00-03:00",
        market_closes_at=day+"T17:00:00-03:00",
        order_cutoff_at=day+"T17:00:00-03:00",
        byma_source="TEST_BYMA_VERSIONED",
        broker_source="TEST_PPI_VERSIONED" if broker_verified else "",
        broker_cutoff_verified=broker_verified,
        evidence_version="TEST-SCHEDULE-V1-"+currency,
    )


def schedules(*, usd_verified=True, ars_verified=True):
    return {
        "ARS": schedule("ARS", broker_verified=ars_verified),
        "USD_MEP": schedule("USD_MEP", broker_verified=usd_verified),
    }


def statuses():
    return {t:"READY_PAPER_CANDIDATE" for t in DEFAULT_EXPECTED_TICKERS}


def policy():
    return OpportunityPolicy(
        version="TEST-OPPORTUNITY-V1",
        currency="ARS",
        minimum_net_annual_rate_fraction=Decimal("0.30"),
        minimum_advantage_bps=Decimal("500"),
        minimum_reference_samples=8,
        reference_max_age_seconds=3600,
        maximum_quote_age_seconds=30,
    )


def refs():
    return {
        t:OpportunityReference(
            ticker=t,
            currency="ARS" if t.startswith("PESOS") else "USD_MEP",
            net_annual_rate_fraction=Decimal("0.20"),
            observed_at=(NOW-timedelta(minutes=5)).isoformat(),
            sample_count=20,
            source="TEST_REFERENCE",
        ) for t in DEFAULT_EXPECTED_TICKERS
    }


def cycle(store, snapshots=None, schedule_map=None, opportunity=True):
    return evaluate_and_persist_caucion_cycle(
        store,
        snapshots or [canonical(t) for t in DEFAULT_EXPECTED_TICKERS],
        now=NOW,
        heartbeat_at=(NOW-timedelta(seconds=5)).isoformat(),
        schedule_evidence_by_currency=schedule_map or schedules(),
        contract_status_by_ticker=statuses(),
        references=refs() if opportunity else None,
        opportunity_policy=policy() if opportunity else None,
        liquidity_deadline=DEADLINE if opportunity else None,
    )


def test_green_schedule_and_all_ten_persist_single_ready_truth_and_find_ars_opportunity(tmp_path):
    store=PaperStore(str(tmp_path/"cycle.db"))
    result=cycle(store)
    assert result["schedule"]["state"]=="OPEN"
    assert set(result["schedule"]["per_currency"])=={"ARS","USD_MEP"}
    assert result["freshness_gate"]["green"] is True
    assert result["readiness"]["state"]=="READY_PAPER"
    assert result["readiness"]["ready_paper_count"]==10
    assert result["opportunity"]["status"] in {"OPPORTUNITY_CANDIDATE","HOLD"}
    assert result["real_order_capability"] is False
    reread=effective_state(store,now=NOW)
    assert reread["evidence_id"]==result["freshness_gate"]["evidence_id"]


def test_unverified_usd_ppi_cutoff_turns_entire_ten_ticker_gate_red(tmp_path):
    store=PaperStore(str(tmp_path/"cycle-red.db"))
    result=cycle(store, schedule_map=schedules(usd_verified=False))
    assert result["schedule"]["state"]=="HOLD"
    assert result["schedule"]["per_currency"]["ARS"]["state"]=="OPEN"
    assert result["schedule"]["per_currency"]["USD_MEP"]["reason"]=="PPI_BROKER_CUTOFF_UNVERIFIED"
    assert result["freshness_gate"]["green"] is False
    assert result["readiness"]["state"]=="HOLD"
    assert result["readiness"]["ready_paper_count"]==0
    assert result["opportunity"]["code"]=="CAUCION_SPECIALIZED_READINESS_NOT_GREEN"


def test_single_ars_schedule_cannot_green_multi_currency_universe(tmp_path):
    store=PaperStore(str(tmp_path/"cycle-one-schedule.db"))
    result=evaluate_and_persist_caucion_cycle(
        store,[canonical(t) for t in DEFAULT_EXPECTED_TICKERS],
        now=NOW,heartbeat_at=NOW.isoformat(),schedule_evidence=schedule("ARS"),
        contract_status_by_ticker=statuses(),
    )
    assert result["schedule"]["reason"]=="MULTI_CURRENCY_SCHEDULE_EVIDENCE_REQUIRED"
    assert result["readiness"]["state"]=="HOLD"
    assert result["freshness_gate"]["green"] is False


def test_missing_one_ticker_turns_ready_count_to_zero(tmp_path):
    store=PaperStore(str(tmp_path/"cycle-missing.db"))
    values=[canonical(t) for t in DEFAULT_EXPECTED_TICKERS[:-1]]
    result=cycle(store, snapshots=values, opportunity=False)
    assert result["freshness_gate"]["green"] is False
    assert result["readiness"]["ready_paper_count"]==0
    assert any(x.startswith("MISSING_TICKER:") for x in result["freshness_gate"]["reasons"])


def test_green_readiness_without_opportunity_policy_remains_hold_for_intraday_execution(tmp_path):
    store=PaperStore(str(tmp_path/"cycle-no-policy.db"))
    result=cycle(store, opportunity=False)
    assert result["readiness"]["state"]=="READY_PAPER"
    assert result["opportunity"]["status"]=="HOLD"
    assert result["opportunity"]["code"]=="OPPORTUNITY_POLICY_NOT_CONFIGURED"
