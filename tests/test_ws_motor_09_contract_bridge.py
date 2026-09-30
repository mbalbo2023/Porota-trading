import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from dataclasses import replace

import pytest
import bf_production_paper_observer as observer
import bu_instrument_catalog as catalog
import cp_contract_evidence_v2_hf6 as evidence
import cq_family_contract_rules_hf6 as rules
import rc6_contract_bridge as bridge
import rc6_iol_family_reference as iol
from be_paper_engine import PaperStore, PaperBroker, Quote, D
from bs_instrument_contracts import contract_from_metadata
from bq_exit_policy import PaperSessionPolicy
from rc6_multisource_discovery import ppi_query_plan
from rc6_source_consolidation import parse_public_payload

NOW = datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc)


def record(**changes):
    payload = {"currency": "ARS", "cash_multiplier": "0.01",
               "quantity_step": "1", "quantity_min": "1"}
    payload.update(changes)
    return dict(family="BONOS", ticker="GD30", market="BYMA", settlement="A-24HS",
        source_class="IOL_STRUCTURED_API", source_ref="fixture:independent-contract-terms",
        observed_at=NOW.isoformat(), evidence=payload, evidence_hash=evidence.evidence_hash(payload))


def store(tmp_path):
    s = PaperStore(str(tmp_path / "paper.db"))
    observer._support_schema(s)
    return s


def test_bridge_preserves_missing_minimum_and_exact_factor():
    r = record()
    normalized = bridge.normalize_group([r], now=NOW)
    c = contract_from_metadata("GD30", "BONOS", normalized["financial_contract_v17"])
    assert c.notional("87010", 1) == Decimal("870.10")
    assert not any(x in normalized["contract_bridge"]["gaps"] for x in ("isin", "duration", "tir"))
    broken = record(quantity_min=None)
    assert bridge.normalize_group([broken], now=NOW)["financial_contract_v17"] is None


@pytest.mark.parametrize("change", ["currency", "future", "changed", "hash", "source", "multiplier"])
def test_bridge_fail_closed_essential_variants(change):
    r=record(); rows=[r]
    if change=="currency": rows.append(record(currency="USD"))
    if change=="future": r["observed_at"]=(NOW+timedelta(seconds=1)).isoformat()
    if change=="changed": r["change_pending"]=True
    if change=="hash": r["evidence_hash"]="0"*64
    if change=="source": r["source_class"]="POROTA_LEGACY_EVIDENCE"
    if change=="multiplier": rows.append(record(cash_multiplier="1"))
    assert bridge.normalize_group(rows, now=NOW)["financial_contract_v17"] is None


@pytest.mark.parametrize("family", ["CAUCIONES"])
def test_bridge_does_not_disguise_specialized_executor_as_spot(family):
    r=record();r["family"]=family
    normalized=bridge.normalize_group([r], now=NOW)
    assert normalized["financial_contract_v17"] is None
    assert normalized["paper_caucion_contract_v1"] is None
    assert normalized["contract_bridge"]["status"] == "BLOCKED"
    assert normalized["financial_contract_v17"] is None


def test_fci_bridge_reaches_connected_subscription_capability():
    payload = {
        "currency": "ARS", "subscription_min": "1000", "subscription_step": "1",
    }
    r = dict(family="FCI", ticker="ADCAP.AP.A", market="FCI", currency="ARS",
             settlement="INMEDIATA", source_class="PPI_OFFICIAL_DOCUMENTATION",
             source_ref="ppi:supermercado-fci", observed_at=NOW.isoformat(),
             evidence=payload, evidence_hash=evidence.evidence_hash(payload))
    claim = bridge.normalize_group([r], now=NOW)
    assert claim["financial_contract_v17"] is None
    assert claim["paper_family_contract_v1"]["subscription_min"] == "1E+3"
    primary = catalog.normalize_record(
        {"ticker": "ADCAP.AP.A", "type": "FCI", "market": "FCI", "currency": "ARS"},
        "INMEDIATA", NOW.isoformat(), "ppi-primary")
    completed = catalog.complete_with_complement(primary, claim)
    assert completed["capability"] == "READY_PAPER_FCI_SUBSCRIPTION"
    assert catalog.contract_for(completed).subscription_amount("1000") == Decimal("1000")


def test_future_bridge_uses_one_published_margin_as_conservative_floor():
    payload = {
        "currency": "ARS", "cash_multiplier": "1000", "quantity_min": "1",
        "quantity_step": "1", "underlying": "USDARS",
        "expiry_at": "2026-12-31T15:00:00-03:00", "margin_requirement": "160000",
    }
    r = dict(family="FUTUROS", ticker="DLR/DIC26", market="ROFEX", currency="ARS",
             settlement="INMEDIATA", source_class="CLEARING_OFFICIAL",
             source_ref="argentina-clearing:margin-contracts-551", observed_at=NOW.isoformat(),
             evidence=payload, evidence_hash=evidence.evidence_hash(payload))
    claim = bridge.normalize_group([r], now=NOW)
    contract = contract_from_metadata("DLR/DIC26", "FUTUROS", claim["financial_contract_v17"])
    assert contract.initial_margin == contract.maintenance_margin == Decimal("160000")


def test_stored_evidence_reaches_real_reconciler_candidate_lookup_and_paper(tmp_path, monkeypatch):
    # Fully synthetic market/quantity rule; no claim that the real GD30 minimum
    # has been captured. Exercises production functions and a private SQLite DB.
    s=store(tmp_path)
    primary=catalog.normalize_record({"ticker":"GD30","type":"BONOS","market":"BYMA","currency":"ARS"}, "A-24HS", NOW.isoformat(), "fixture-ppi-snapshot")
    with s.connect() as c: catalog.persist(c, primary)
    r=record()
    evidence.record_snapshot(s,family=r["family"],ticker=r["ticker"],market=r["market"],settlement=r["settlement"],source_class=r["source_class"],source_ref=r["source_ref"],observed_at=r["observed_at"],evidence=r["evidence"])
    original=bridge.complements_from_store
    monkeypatch.setattr(bridge,"complements_from_store",lambda st:original(st,now=NOW))
    monkeypatch.setattr(observer,"now_iso",lambda:NOW.isoformat())
    monkeypatch.setattr(observer,"complementary_discovery",lambda _:[])
    fresh=catalog.complementary_is_fresh
    monkeypatch.setattr(catalog,"complementary_is_fresh",lambda r,**kw:fresh(r,now=NOW))
    assert observer._reconcile_complementary_catalog(s)==1
    assert ("GD30","BONOS","A-24HS") in observer._eligible_symbols(s)
    row=catalog.lookup(s,"GD30","BONOS","A-24HS")
    terms=catalog.quote_terms(row,now=NOW)
    assert terms["opening_block_reason"]==""
    assert terms["contract"].quantity_step==D(1)
    # The PAPER identity follows the catalogue's 14-day LKG window; dynamic
    # quote/session freshness remains enforced outside this static contract.
    assert catalog.quote_terms(row,now=NOW+timedelta(days=2))["opening_block_reason"]==""
    assert catalog.quote_terms(row,now=NOW+timedelta(days=15))["opening_block_reason"]=="PPI_FRESHNESS_STALE"
    sector_file=tmp_path / "sector.csv"
    sector_file.write_text("ticker,family,market,currency,settlement,sector,source,reviewed\nGD30,BONOS,BYMA,ARS,A-24HS,SOVEREIGN_FIXTURE,fixture,true\n")
    monkeypatch.setenv("POROTA_SECTOR_MAP_PATH",str(sector_file))
    broker=PaperBroker(s,initial_cash="1000000",risk_pct="0.005",max_positions=5,max_position_pct="1",max_total_exposure_pct="1",clock_fn=lambda:NOW.isoformat(),session_policy=PaperSessionPolicy(),require_supervisor=False,daily_loss_pct="2.5")
    q=Quote("GD30","BONOS","A-24HS",D(87000),D(86900),D(87000),D(10000),D(10000),NOW.isoformat(),book_at=NOW.isoformat(),trade_at=NOW.isoformat(),last_kind="TRADE",**terms)
    opened, reason, paper_id=broker._open(q,D("0.9"),{})
    assert opened,reason
    later=NOW+timedelta(minutes=1)
    broker.clock_fn=lambda:later.isoformat()
    close=replace(q,observed_at=later.isoformat(),book_at=later.isoformat(),trade_at=later.isoformat(),bid=D(88000),ask=D(88100),last=D(88050))
    assert broker._close(s.open_positions()[0],close,"FIXTURE_CLOSE",as_of=later.isoformat())
    with s.connect() as c:
        assert [r[0] for r in c.execute("SELECT side FROM paper_fills WHERE paper_id=? ORDER BY id",(paper_id,))]==["BUY_SIMULATED","SELL_SIMULATED"]
        assert c.execute("SELECT COUNT(*) FROM complementary_contract_retry WHERE ticker='GD30'").fetchone()[0]==0


def test_runtime_reconciler_ingests_iol_mcp_cache_into_evidence_v2(tmp_path, monkeypatch):
    s = store(tmp_path)
    primary = catalog.normalize_record(
        {"ticker": "GD30", "type": "BONOS", "market": "BYMA", "currency": "ARS"},
        "A-24HS", NOW.isoformat(), "fixture-ppi-snapshot")
    with s.connect() as c:
        catalog.persist(c, primary)
    market_root = tmp_path / "market"
    market_root.mkdir()
    payload = {
        "schema": "rc6-iol-family-reference-v1", "refreshed_at": NOW.isoformat(),
        "records": [{
            "ticker": "GD30", "instrument_type": "BONOS", "market": "BYMA",
            "currency": "ARS", "settlement": "A-24HS",
            "financial_contract_v17": {
                "currency": "ARS", "cash_multiplier": "0.01",
                "quantity_step": "1", "minimum_quantity": "1",
            },
        }], "fci": [], "cauciones": {},
    }
    (market_root / "iol_family_reference_latest.json").write_text(
        json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("POROTA_MARKET_DATA_ROOT", str(market_root))
    monkeypatch.setattr(observer, "now_iso", lambda: NOW.isoformat())
    observer._reconcile_complementary_catalog(s)
    rows = evidence.current_records(
        s, family="BONOS", ticker="GD30", currency="ARS", settlement="A-24HS")
    assert rows and rows[0]["source_class"] == "IOL_STRUCTURED_API"
    assert rows[0]["evidence"]["freshness_basis"] == "CAPTURE_TIMESTAMP_STATIC_ONLY"


def test_catalog_cannot_bypass_blocked_candidate(tmp_path):
    s=store(tmp_path)
    r=catalog.normalize_complementary_record(dict(ticker="TEST",instrument_type="ACCIONES",market="BYMA",currency="ARS",settlement="A-24HS",source="IOL_COMPLEMENTARY"),NOW.isoformat(),"fixture")
    with s.connect() as c:
        catalog.persist(c,r);catalog.sync_candidate_universe(c,NOW.isoformat())
    assert not observer._eligible_symbols(s)
    assert catalog.quote_terms(catalog.lookup(s,"TEST","ACCIONES","A-24HS"),now=NOW)["opening_block_reason"]


def test_minimum_and_increment_are_distinct():
    c=contract_from_metadata("X","BONOS",dict(currency="ARS",market="BYMA",settlement="A-24HS",cash_multiplier="0.01",quantity_step="10",minimum_quantity="100",metadata_source="fixture"))
    with pytest.raises(ValueError,match="mínimo"): c.quantity(90)
    with pytest.raises(ValueError,match="lote"): c.quantity(105)
    assert c.notional(100,110)==110


def test_rotation_covers_every_prefix_without_33_day_gap():
    for width in range(1,37):
        coverage=set()
        for i in range((36+width-1)//width):
            coverage.update(q[0] for q in ppi_query_plan(day=date(2026,9,28)+timedelta(days=i),prefixes_per_family=width) if q[5]=="BONOS")
        assert len(coverage)==36


def test_public_parser_keeps_more_than_500_and_rejects_provider_error():
    rows=[{"symbol":f"X{i}","last":1} for i in range(701)]
    assert parse_public_payload("BYMA","fixture",json.dumps(rows).encode())["record_count"]==701
    assert parse_public_payload("BYMA","fixture",json.dumps({"data":rows,"error":"unavailable"}).encode())["record_count"]==0
    assert parse_public_payload("BYMA","fixture",json.dumps(rows).encode(),500)["record_count"]==0


def test_one_nominal_simulation_enables_explicit_paper_policy_not_broker_terms():
    a=dict(type="TIT. PUBLICOS",currency="ARS",market="BCBA",term="T1",units_per_lot=100)
    s=dict(nominals=1,dirty_price_per100=56.4,amount_invested_ars=870.1)
    q=dict(unit_price=870.1,trade={"lot_price":{"value":87010}})
    contract=iol._fixed_contract("GD30",a,{},s,q)
    assert contract["quantity_step"] == "1"
    assert contract["broker_quantity_step"] == "NO_VERIFICADO"
    assert contract["paper_quantity_policy"] == "ONE_NOMINAL_SIMULATION_UNIT"
    a["order_terms"]=dict(quantity_step="1",minimum_quantity="1",source_ref="fixture:separate-rule")
    assert iol._fixed_contract("GD30",a,{},s,q)["quantity_step"]=="1"


def test_future_dynamic_observation_is_stale():
    assert rules._stale_dynamic(["tna"], {"tna": {
        "observed_at": NOW.isoformat(),
        "provider_timestamp": (NOW + timedelta(seconds=1)).isoformat(),
        "freshness_basis": "PROVIDER_TIMESTAMP",
    }}, NOW) == ["tna"]


def test_essential_conflict_is_not_resolved_by_source_priority():
    primary=catalog.normalize_record({"ticker":"GD30","type":"BONOS","market":"BYMA","currency":"ARS"},"A-24HS",NOW.isoformat(),"fixture")
    comp=bridge.normalize_group([record()],now=NOW)
    first=catalog.complete_with_complement(primary,comp)
    conflict=dict(comp,financial_contract_v17={**comp["financial_contract_v17"],"cash_multiplier":"1"})
    assert catalog.complete_with_complement(first,conflict)["capability"]=="CONTRACT_SOURCE_CONFLICT"


def test_review_required_invalidates_an_existing_bridge_contract():
    primary=catalog.normalize_record({"ticker":"GD30","type":"BONOS","market":"BYMA","currency":"ARS"},"A-24HS",NOW.isoformat(),"fixture")
    good=bridge.normalize_group([record()],now=NOW)
    ready=catalog.complete_with_complement(primary,good)
    changed=record(); changed["change_pending"]=True
    blocked=catalog.complete_with_complement(ready,bridge.normalize_group([changed],now=NOW))
    assert blocked["capability"]=="CONTRACT_EVIDENCE_REVIEW_REQUIRED"
    # Even an old READY capability cannot override a newly invalid contract.
    blocked["capability"]="READY_PAPER_BONOS"
    assert catalog.quote_terms(blocked,now=NOW)["opening_block_reason"]=="CONTRACT_EVIDENCE_REVIEW_REQUIRED"


def test_analytical_fields_are_enrichment_without_lifecycle_relaxation():
    assert "conversion_ratio" not in rules.FAMILY_CONTRACT_FIELDS["CEDEARS"]
    assert "conversion_ratio" in rules.FAMILY_ENRICHMENT_FIELDS["CEDEARS"]
    for family in ("FCI","FCI_LOCAL","FCI_EXTERIOR"):
        assert not {"manager","custodian"} & rules.FAMILY_CONTRACT_FIELDS[family]
        assert {"currency","subscription_min","subscription_step","cutoff_time","redemption_term","nav_unit"} <= rules.FAMILY_CONTRACT_FIELDS[family]
        assert "fee_schedule" not in rules.FAMILY_CONTRACT_FIELDS[family]
    assert {"coupon_terms","amortization_terms","maturity_date"} <= rules.FAMILY_CONTRACT_FIELDS["BONOS"]


def caucion_record():
    r=record(currency="ARS",side="COLOCADORA",annual_rate_fraction="0.159",
        start_date="2026-09-28",maturity_at="2026-09-30T11:00:00-03:00",quoted_at=NOW.isoformat(),
        available_principal="10000000",minimum_principal="100000",principal_step="1",day_count_basis=365,
        fee_payment="MATURITY",quoted_total_fees="10",fee_quote_principal="100000",operable=True,market_session_state="OPEN")
    r.update(family="CAUCIONES",ticker="PESOS2")
    return r


def test_caucion_bridge_real_paper_lifecycle_no_reuse_of_locked_cash(tmp_path):
    s=store(tmp_path)
    primary=catalog.normalize_record({"ticker":"PESOS2","type":"CAUCIONES","market":"BYMA","currency":"ARS"},"A-24HS",NOW.isoformat(),"fixture-ppi")
    with s.connect() as c: catalog.persist(c,primary)
    broker=PaperBroker(s,initial_cash="1000000",daily_loss_pct="2.5",require_supervisor=False,clock_fn=lambda:NOW.isoformat())
    r=caucion_record()
    evidence.record_snapshot(s,family=r["family"],ticker=r["ticker"],market=r["market"],settlement=r["settlement"],source_class=r["source_class"],source_ref=r["source_ref"],observed_at=r["observed_at"],evidence=r["evidence"])
    placed=broker.place_caucion_from_evidence(primary,"100000","fixture-caucion",as_of=NOW.isoformat())
    assert placed["status"]=="OPEN"
    assert broker._cash(as_of=NOW.isoformat())==D("900000")
    same=broker.place_caucion_from_evidence(primary,"100000","fixture-caucion",as_of=NOW.isoformat())
    assert same["paper_id"]==placed["paper_id"]
    broker.settle_cauciones("2026-09-30T11:00:00-03:00")
    assert broker.cauciones.positions()[0]["status"]=="MATURED"
    assert broker._cash(as_of="2026-09-30T11:00:00-03:00")==D("1000077.12")
    broker.settle_cauciones("2026-09-30T11:00:00-03:00")
    assert broker._cash(as_of="2026-09-30T11:00:00-03:00")==D("1000077.12")


@pytest.mark.parametrize("field",["principal_step","quoted_at","available_principal"])
def test_caucion_bridge_missing_essential_does_not_create_offer(field):
    primary=catalog.normalize_record({"ticker":"PESOS2","type":"CAUCIONES","market":"BYMA","currency":"ARS"},"A-24HS",NOW.isoformat(),"fixture-ppi")
    r=caucion_record();r["evidence"].pop(field);r["evidence_hash"]=evidence.evidence_hash(r["evidence"])
    with pytest.raises(ValueError,match="CAUCION_MISSING"):
        bridge.caucion_offer_from_evidence([r],primary,now=NOW)


def test_ars_caucion_uses_existing_paper_tariff_without_duplicate_fee_evidence():
    primary=catalog.normalize_record({"ticker":"PESOS2","type":"CAUCIONES","market":"BYMA","currency":"ARS"},"A-24HS",NOW.isoformat(),"fixture-ppi")
    r=caucion_record();r["evidence"].pop("quoted_total_fees");r["evidence"].pop("fee_quote_principal")
    r["evidence_hash"]=evidence.evidence_hash(r["evidence"])
    offer=bridge.caucion_offer_from_evidence([r],primary,now=NOW)
    assert offer.quoted_total_fees is None
    interest,fees,net=offer.economics("100000")
    assert interest > fees and net > 0


def test_usd_caucion_still_requires_explicit_fee_budget():
    primary=catalog.normalize_record({"ticker":"USD1","type":"CAUCIONES","market":"BYMA","currency":"USD"},"A-24HS",NOW.isoformat(),"fixture-ppi")
    r=caucion_record();r.update(ticker="USD1")
    r["evidence"]["currency"]="USD"
    r["evidence"].pop("quoted_total_fees");r["evidence"].pop("fee_quote_principal")
    r["evidence_hash"]=evidence.evidence_hash(r["evidence"])
    with pytest.raises(ValueError,match="CAUCION_MISSING"):
        bridge.caucion_offer_from_evidence([r],primary,now=NOW)


def test_option_price_base_quantity_and_adjusted_series_cannot_be_inferred():
    chain={"underlying":"GGAL","options":[dict(symbol="OPT",strike_price=6000,option_type="C",expiration="2026-10-16T15:30:00",volume=1)]}
    info=dict(market="BCBA",currency="ARS",units_per_lot=1)
    standard=iol._option_records(chain,{"OPT":info},NOW.isoformat(),underlying_info={"type":"ACCIONES","currency":"ARS"})[0]["financial_contract_v17"]
    assert standard["cash_multiplier"] == "100"
    assert standard["broker_quantity_step"] == "NO_VERIFICADO"
    info.update(order_terms=dict(quantity_step=1,minimum_quantity=1,source_ref="fixture"),premium_basis="PER_CONTRACT")
    assert iol._option_records(chain,{"OPT":info},NOW.isoformat(),underlying_info={"type":"ACCIONES"})[0]["financial_contract_v17"]["cash_multiplier"]=="100"
    info["adjusted_series_unverified"]=True
    assert iol._option_records(chain,{"OPT":info},NOW.isoformat(),underlying_info={"type":"ACCIONES"})[0]["financial_contract_v17"] is None


def test_byma_full_capture_reconciles_source_count_and_preserves_dimensions(monkeypatch):
    import rc6_source_consolidation as source

    class Response:
        status=200
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def read(self,n):
            rows=[
                dict(
                    symbol=f"X-{i}", denominationCcy="ARS", settlementType="2",
                    trade=100, tradeHour="17:00:09", quantityBid=50, quantityOffer=70,
                )
                for i in range(600)
            ]
            return json.dumps(dict(
                content=dict(page_number=1,page_count=1,total_elements_count=600),
                data=rows,
            )).encode()

    seen=[]
    def fetch(request,timeout):
        payload=json.loads(request.data)
        seen.append(payload)
        return Response()

    monkeypatch.setattr(source,"urlopen",fetch)
    result=source._byma_post("https://fixture.invalid","bonds","BONOS")
    assert seen==[{"page_size":source.BYMA_FULL_CAPTURE_PAGE_SIZE}]
    assert result["source_record_count"]==result["record_count"]==600
    assert result["expected_total"]==600
    row=result["records"][0]
    assert row["currency"]=="ARS" and row["settlement_code"]=="2"
    assert row["bid_size"]==50 and row["ask_size"]==70
    assert "timestamp" not in row and row["provider_time_only"]=="17:00:09"


def test_missing_enrichment_does_not_bypass_essential_or_closed_market():
    base={k:1 for k in rules.FAMILY_CONTRACT_FIELDS["CEDEARS"]}
    base.update(market="BYMA",currency="ARS",settlement="A-24HS",operable=True,market_session_state="OPEN")
    r=dict(source_class="PPI_STRUCTURED_API",observed_at=NOW.isoformat(),evidence=base)
    assert rules.evaluate_family("CEDEARS",[r],now=NOW)["status"]=="READY_PAPER_CANDIDATE"
    base.pop("currency")
    assert rules.evaluate_family("CEDEARS",[r],now=NOW)["status"]=="MISSING_CONTRACT"
    base["currency"]="ARS";base["market_session_state"]="CLOSED"
    # Contract readiness does not duplicate PaperSessionPolicy/Quote admission.
    assert rules.evaluate_family("CEDEARS",[r],now=NOW)["status"]=="READY_PAPER_CANDIDATE"


def test_two_ppi_settlements_resolve_by_operation_and_consumers_agree(tmp_path):
    s=store(tmp_path)
    with s.connect() as c:
        for term in ("INMEDIATA","A-24HS"):
            catalog.persist(c,catalog.normalize_record(dict(ticker="TEST",type="ACCIONES",market="BYMA",currency="ARS"),term,NOW.isoformat(),"fixture"))
        catalog.sync_candidate_universe(c,NOW.isoformat())
    assert set(observer._eligible_symbols(s))=={("TEST","ACCIONES","INMEDIATA"),("TEST","ACCIONES","A-24HS")}
    for term in ("INMEDIATA","A-24HS"):
        assert catalog.quote_terms(catalog.lookup(s,"TEST","ACCIONES",term),now=NOW)["opening_block_reason"]==""
    with s.connect() as c:
        catalog.persist(c,catalog.normalize_record(dict(ticker="TEST",type="ACCIONES",market="BYMA",currency="USD"),"A-24HS",NOW.isoformat(),"fixture"))
        catalog.sync_candidate_universe(c,NOW.isoformat())
    assert set(observer._eligible_symbols(s))=={("TEST","ACCIONES","INMEDIATA")}
    assert catalog.lookup(s,"TEST","ACCIONES","A-24HS") is None


def test_distinct_currencies_are_preserved_as_distinct_full_key_claims(tmp_path):
    s=store(tmp_path)
    for currency,source in (("ARS","IOL_STRUCTURED_API"),("USD","BYMA_STRUCTURED_API")):
        r=record(currency=currency)
        evidence.record_snapshot(s,family=r["family"],ticker=r["ticker"],market=r["market"],settlement=r["settlement"],source_class=source,source_ref=r["source_ref"],observed_at=r["observed_at"],evidence=r["evidence"])
    claims=bridge.complements_from_store(s,now=NOW)
    assert {x["currency"] for x in claims}=={"ARS","USD"}
    assert all(x["ticker"]=="GD30" and x["financial_contract_v17"] is not None
               and "IDENTITY_CONFLICT" not in x["contract_bridge"]["gaps"]
               for x in claims)


def test_numeric_representation_is_not_a_contract_conflict():
    primary=catalog.normalize_record({"ticker":"GD30","type":"BONOS","market":"BYMA","currency":"ARS"},"A-24HS",NOW.isoformat(),"fixture")
    comp=bridge.normalize_group([record()],now=NOW)
    first=catalog.complete_with_complement(primary,comp)
    equivalent=dict(comp,financial_contract_v17={**comp["financial_contract_v17"],"cash_multiplier":".0100"})
    assert catalog.complete_with_complement(first,equivalent)["capability"].startswith("READY_PAPER_")
