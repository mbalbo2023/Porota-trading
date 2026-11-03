import json
from dataclasses import replace
from decimal import Decimal

from be_paper_engine import D, PaperBroker, PaperStore, Quote
from bs_instrument_contracts import InstrumentContract
from bq_exit_policy import PaperSessionPolicy
from dh_paper_dynamic_risk_gate_hf6 import candidate_stop_risk, position_stop_risk


def option_quote(*, at="2026-10-15T11:00:00-03:00", bid="19", ask="20",
                 bid_size="20", ask_size="20", last="19.5"):
    contract=InstrumentContract(
        "GFGC7000OC","OPCIONES","ARS","BYMA","INMEDIATA",
        D("100"),D("1"),"IOL_OPTIONS_CHAIN+IOL_ASSET_INFO+BYMA_OPTION_LOT_POLICY_2026",
        expires_at="2026-10-16T15:30:00-03:00",
        underlying="GGAL",strike=D("7000"),option_right="CALL")
    return Quote(
        "GFGC7000OC","OPCIONES","INMEDIATA",D(last),D(bid),D(ask),
        D(bid_size),D(ask_size),at,contract=contract,currency="ARS",market="BYMA",
        metadata_source="PPI_IDENTITY_PLUS_IOL_COMPLEMENTARY",
        book_at=at,trade_at=at,last_kind="TRADE")


def broker(tmp_path, *, clock="2026-10-15T11:00:00-03:00", risk_pct="0.005"):
    store=PaperStore(str(tmp_path/"paper.db"))
    return PaperBroker(
        store,initial_cash="1000000",risk_pct=risk_pct,
        max_positions=5,max_position_pct="1",max_total_exposure_pct="1",
        clock_fn=lambda:clock,session_policy=PaperSessionPolicy(),
        require_supervisor=False,daily_loss_pct="2.5")


def test_long_option_candidate_risk_reserves_full_premium_not_stop_distance(tmp_path):
    b=broker(tmp_path)
    risk=candidate_stop_risk(
        b,entry_price=D("20"),stop_price=D("19.6"),quantity=D("2"),
        cash_multiplier=D("100"),asset_class="OPCIONES")
    expected_premium=D("20")*D("2")*D("100")
    assert risk > expected_premium
    # A 2% stop model alone would be only 80 pesos before costs, which must
    # never be used as the option's maximum-loss budget.
    assert risk > D("80")


def test_option_open_sizes_against_full_premium_and_records_only_simulated_fill(tmp_path):
    q=option_quote()
    b=broker(tmp_path)
    opened,reason,paper_id=b._open(q,D("0.9"),{})
    assert opened, reason
    assert paper_id
    positions=b.store.open_positions()
    assert len(positions)==1
    p=positions[0]
    assert p["asset_class"]=="OPCIONES"
    # 0.5% of ARS 1m = ARS 5k. Each contract consumes ARS 2k premium plus
    # entry costs, so no more than two contracts can be admitted.
    assert D(p["quantity"]) <= D("2")
    features=json.loads(p["features_json"])
    assert features["contract_cash_multiplier"]=="100"
    assert features["financial_contract"]["option_right"]=="CALL"
    assert D(features["candidate_stop_risk"]) >= D(p["entry_price"])*D(p["quantity"])*D("100")
    with b.store.connect() as c:
        fills=[dict(r) for r in c.execute("SELECT * FROM paper_fills WHERE paper_id=?",(paper_id,))]
    assert [f["side"] for f in fills]==["BUY_SIMULATED"]


def test_option_position_risk_remains_full_premium_after_open(tmp_path):
    q=option_quote()
    b=broker(tmp_path)
    assert b._open(q,D("0.9"),{})[0]
    p=b.store.open_positions()[0]
    risk=position_stop_risk(b,p)
    assert risk >= D(p["entry_price"])*D(p["quantity"])*D("100")


def test_option_close_uses_contract_multiplier_and_simulated_sell_only(tmp_path):
    q=option_quote()
    b=broker(tmp_path)
    opened,reason,_=b._open(q,D("0.9"),{})
    assert opened, reason
    p=b.store.open_positions()[0]
    later=replace(
        q,observed_at="2026-10-15T11:01:00-03:00",
        book_at="2026-10-15T11:01:00-03:00",
        trade_at="2026-10-15T11:01:00-03:00",
        bid=D("21"),ask=D("22"),last=D("21.5"))
    # Live runtime owns execution time through its injected clock. Advance the
    # fixture clock together with the new book instead of asking _close to
    # trust an as_of that is ahead of the runtime clock.
    b.clock_fn=lambda: later.observed_at
    assert b._close(p,later,"TEST_OPTION",as_of=later.observed_at)
    closed=b.store.recent_closed()[0]
    assert closed["asset_class"]=="OPCIONES"
    with b.store.connect() as c:
        sides=[r[0] for r in c.execute(
            "SELECT side FROM paper_fills WHERE paper_id=? ORDER BY id",(closed["paper_id"],))]
    assert sides==["BUY_SIMULATED","SELL_SIMULATED"]


def test_option_expired_contract_is_rejected_by_session_before_open(tmp_path):
    q=option_quote(at="2026-10-16T15:30:00-03:00")
    b=broker(tmp_path,clock=q.observed_at)
    opened,reason,paper_id=b._open(q,D("0.9"),{})
    assert not opened
    assert paper_id is None
    assert reason=="OPTION_EXPIRED"


def test_option_without_explicit_contract_never_opens(tmp_path):
    q=replace(option_quote(),contract=None)
    b=broker(tmp_path)
    opened,reason,paper_id=b._open(q,D("0.9"),{})
    assert not opened and paper_id is None
    assert "contrato financiero explícito" in reason
