"""#462 S/T: canonical family caller, financial priority and source boundaries."""
from copy import deepcopy
from contextlib import closing
from datetime import timedelta
import ast
import json
from pathlib import Path
import sqlite3

import pytest

from bs_instrument_contracts import FAMILIES
from rc6_dynamic_universe.routing import strategy_route
from rc6_shadow_runtime.families import family_reports
from rc6_shadow_runtime.source_authority import resolve_field, source_rank
from rc6_source_consolidation import consolidate
from rc6_ppi_iol_reconciliation_rc6 import reconcile
from tests.test_rc6_shadow_family_runtime import (
    AT, instrument, clocked, fixture_db, snapshot, source_row, by_ticker,
)


POLICY_FIELDS = {"policy_version", "strategy_owner", "lifecycle_owner", "strategy_status",
    "observation_policy", "discovery_policy", "cadence_class", "source_authority",
    "tradeability_selection_policy", "deep_analysis_eligibility", "reason_codes", "entry_authority"}


def fixed_row(record, **values):
    return source_row(record, quote_basis_nominal=clocked(100, source="PPI"),
        quantity_step_nominal=clocked(1, source="PPI"), minimum_nominal=clocked(1, source="PPI"),
        cash_multiplier=clocked(.01, source="PPI"), **values)


def quote_claim(record, **values):
    return {**record, "symbol": record["ticker"], "family": record["instrument_type"],
        "term": record["settlement"], "source_at": AT.isoformat(), "received_at": AT.isoformat(),
        "last": 100, "price_unit": "PER_SHARE", **values}


@pytest.mark.parametrize("family", sorted(FAMILIES))
def test_policy_complete_for_each_family_without_assigned_entry_authority(family):
    route = strategy_route(family)
    assert POLICY_FIELDS <= route.keys()
    assert route["entry_authority"] is False and route["financial_family_quotas"] is False
    assert all(route[field] for field in POLICY_FIELDS - {"reason_codes", "entry_authority"})
    if family not in {"ACCIONES", "CEDEARS", "ETFS"}:
        assert route["strategy_status"] == "STRATEGY_NOT_VALIDATED"
        assert route["status"] == "OBSERVE_ONLY"
        assert not route["equity_scanner_eligible"] and not route["scalping_scanner_eligible"]
        assert "STRATEGY_NOT_VALIDATED" in route["reason_codes"]
        assert not strategy_route(family, scalping=True)["generic_equity"]


def test_empty_catalog_still_emits_all_family_policies_and_unknown_fails_closed(tmp_path):
    path = fixture_db(tmp_path, [])
    report = family_reports(path, as_of=AT, catalog=[])
    assert {row["family"] for row in report["families"]} == FAMILIES
    assert all(POLICY_FIELDS <= row.keys() and row["catalog_count"] == 0 for row in report["families"])
    assert report["fixed_income_observation_universe"]["selected"] == []
    assert strategy_route("FAMILY_NOT_VALIDATED")["entry_authority"] is False
    assert strategy_route("FAMILY_NOT_VALIDATED")["strategy_status"] == "STRATEGY_NOT_VALIDATED"


def test_fixed_income_rank_is_liquidity_then_maturity_inside_family_currency(tmp_path):
    catalog = [instrument("A_WIDE", "BONOS"), instrument("Z_TIGHT", "BONOS"),
               instrument("B_NEAR", "BONOS"), instrument("A_USD", "BONOS", currency="USD"),
               instrument("LETTER", "LETRAS"), instrument("ON", "OBLIGACIONES")]
    path = fixture_db(tmp_path, catalog)
    rows = []
    for record in catalog:
        snapshot(path, record, bid=99 if record["ticker"] == "A_WIDE" else 99.95,
                 ask=101 if record["ticker"] == "A_WIDE" else 100.05)
        days = 10 if record["ticker"] == "B_NEAR" else 30
        rows.append(fixed_row(record, maturity_at=clocked((AT+timedelta(days=days)).isoformat(), source="PPI"),
            yield_to_maturity=clocked(.05, source="PPI"), duration=clocked(1.5, source="PPI"),
            parity=clocked(.95, source="PPI"), curve=clocked("ARS_SOVEREIGN", source="PPI"), carry=clocked(.01, source="PPI")))
    report = family_reports(path, as_of=AT, catalog=catalog, sources={"family_evidence": rows})
    selected = report["fixed_income_observation_universe"]["selected"]
    ars_bonds = [row for row in selected if row["ranking_scope"][:3] == ["BONOS", "BYMA", "ARS"]]
    assert [row["identity"][0] for row in ars_bonds] == ["B_NEAR", "Z_TIGHT", "A_WIDE"]
    assert [row["rank"] for row in ars_bonds] == [1, 2, 3]
    assert next(row for row in selected if row["identity"][0] == "A_USD")["rank"] == 1
    for row in selected:
        assert row["entry_authority"] is False and not row["selection_is_directional_signal"]
        components = row["rank_components"]
        assert components["yield"]["source"] == "PPI"
        assert components["duration"]["value"] == 1.5
        assert components["curve"]["value"] == "ARS_SOVEREIGN"
        assert components["depth_cash"] is None  # Unknown quantity units are not cash depth.
    assert not report["fixed_income_observation_universe"]["yield_is_buy_signal"]


def test_newer_complement_preserves_ppi_contract_and_conflict_is_review_not_selection(tmp_path):
    record = instrument("BOND", "BONOS")
    path = fixture_db(tmp_path, [record])
    snapshot(path, record)
    ppi = fixed_row(record)
    for value in ppi.values():
        if isinstance(value, dict) and "value" in value:
            value["received_at"] = value["source_at"] = (AT-timedelta(seconds=30)).isoformat()
    iol = source_row(record, quote_basis_nominal=clocked(1000, source="IOL"))
    original = deepcopy([ppi, iol])
    report = family_reports(path, as_of=AT, catalog=[record], sources={"family_evidence": [ppi, iol]})
    terms = by_ticker(report)["BOND"]["handler_result"]["terms"]
    assert terms["quote_basis_nominal"]["value"] == 100 and terms["quote_basis_nominal"]["source"] == "PPI"
    assert terms["quote_basis_nominal"]["review_required"] is True
    assert len(terms["quote_basis_nominal"]["conflicts"]) == 1
    assert report["fixed_income_observation_universe"]["selected"] == []
    assert report["fixed_income_observation_universe"]["excluded"][0]["entry_authority"] is False
    assert [ppi, iol] == original


def test_compatible_missing_book_field_complements_with_own_source_clock(tmp_path):
    record = instrument("GGAL", "ACCIONES")
    path = fixture_db(tmp_path, [record])
    snapshot(path, record, ask=0)
    iol = quote_claim(record, last=None, ask=100.05)
    quote = by_ticker(family_reports(path, as_of=AT, catalog=[record], sources={"IOL_MCP": {"symbols": [iol]}}))["GGAL"]["handler_result"]["quote"]
    assert quote["source"] == "PPI" and quote["price"] == 100
    assert quote["field_provenance"]["ask"]["source"] == "IOL"
    assert quote["field_provenance"]["ask"]["source_at"] == AT.isoformat()
    assert quote["field_provenance"]["ask"]["authority"] == "IOL_COMPLEMENT"
    assert quote["tradeable"] is True
    assert not quote["review_required"]


def test_conflicting_quote_units_deny_tradeability_and_do_not_replace_primary(tmp_path):
    record = instrument("GGAL", "ACCIONES")
    path = fixture_db(tmp_path, [record])
    snapshot(path, record, ask=0)
    iol = quote_claim(record, last=None, ask=100.05, price_unit="PER_CONTRACT")
    report = family_reports(path, as_of=AT, catalog=[record], sources={"IOL": {"symbols": [iol]}})
    quote = by_ticker(report)["GGAL"]["handler_result"]["quote"]
    assert quote["price_unit"] == "PER_SHARE"
    assert quote["review_required"] and not quote["tradeable"]


def test_missing_book_clock_cannot_borrow_trade_or_capture_clock(tmp_path):
    record = instrument("GGAL", "ACCIONES")
    path = fixture_db(tmp_path, [record])
    snapshot(path, record)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("UPDATE market_snapshots SET book_at=NULL")
    quote = by_ticker(family_reports(path, as_of=AT, catalog=[record]))["GGAL"]["handler_result"]["quote"]
    assert quote["observable"] and quote["source_at"] == AT.isoformat()
    assert quote["book_status"] == "NO_VERIFICADO" and quote["book_at"] is None
    assert not quote["tradeable"]


def test_scoped_unavailable_iol_and_ambiguous_identity_never_become_provider_zero(tmp_path):
    record = instrument("GGAL", "ACCIONES")
    path = fixture_db(tmp_path, [record])
    snapshot(path, record)
    unavailable = quote_claim(record, state="UNAVAILABLE", reason="SOURCE_UNAVAILABLE", source_path="options:GGAL")
    ambiguous = quote_claim(record, symbol="OTHER")
    report = family_reports(path, as_of=AT, catalog=[record], sources={"IOL": {"symbols": [unavailable, ambiguous]}})
    assert by_ticker(report)["GGAL"]["handler_result"]["quote"]["price"] == 100
    authority = report["source_authority"]
    assert authority["catalog_identity_mutated"] is False
    assert authority["iol_runtime_contract"] == "EXISTING_COLLECTOR_CACHE_ONLY"
    assert authority["identity_review"][0]["identity_assigned"] is False
    scoped = authority["source_path_status"][0]
    assert scoped["source"] == "IOL" and scoped["source_path"] == "options:GGAL"
    assert scoped["provider_available"] is None
    assert scoped["scope"] == "AFFECTED_SOURCE_PATH_IDENTITY_ONLY"


def test_option_priority_prefers_economic_structure_and_liquidity_over_ticker(tmp_path):
    catalog = [instrument("GGAL", "ACCIONES"), instrument("A_WIDE", "OPCIONES", settlement="INMEDIATA"),
               instrument("Z_TIGHT", "OPCIONES", settlement="INMEDIATA"), instrument("B_FAR", "OPCIONES", settlement="INMEDIATA")]
    path = fixture_db(tmp_path, catalog)
    snapshot(path, catalog[0])
    rows = []
    for record in catalog[1:]:
        snapshot(path, record, price=2, bid=2, ask=2.015 if record["ticker"] == "A_WIDE" else 2.005)
        rows.append(source_row(record, underlying=clocked("GGAL", source="PPI"),
            strike=clocked(105 if record["ticker"] == "B_FAR" else 100, source="PPI"),
            strike_unit=clocked("PER_SHARE", source="PPI"), expires_at=clocked((AT+timedelta(days=10)).isoformat(), source="PPI")))
    report = family_reports(path, as_of=AT, catalog=catalog, sources={"family_evidence": rows})
    selected = report["option_observation_universe"]["selected"]
    assert [row["identity"][0] for row in selected] == ["Z_TIGHT", "A_WIDE", "B_FAR"]
    assert [row["structural_priority"] for row in selected] == [1, 2, 3]
    assert all(not row["entry_authority"] and row["iv"]["status"] == "NO_VERIFICADO" for row in selected)
    assert by_ticker(report)["Z_TIGHT"]["handler_result"]["selection"]["structural_priority"] == 1


def test_caucion_fci_event_signatures_are_causal_and_never_scanner_authority(tmp_path):
    catalog = [instrument("CAU", "CAUCIONES"), instrument("FUND", "FCI")]
    path = fixture_db(tmp_path, catalog)
    rows = [source_row(catalog[0], annual_rate_fraction=clocked(.25), term_days=clocked(3),
            available_principal=clocked(10000), maturity_at=clocked((AT+timedelta(days=3)).isoformat())),
            source_row(catalog[1], nav=clocked(10), nav_date=clocked(AT.date().isoformat()),
            subscription_status=clocked("OPEN"), redemption_term=clocked("T+1"), cutoff_time=clocked("15:00 ART"))]
    before = by_ticker(family_reports(path, as_of=AT, catalog=catalog, sources={"family_evidence": rows}))
    again = by_ticker(family_reports(path, as_of=AT+timedelta(seconds=30), catalog=catalog, sources={"family_evidence": rows}))
    for ticker in ("CAU", "FUND"):
        result = before[ticker]["handler_result"]
        assert result["cadence_class"].startswith("EVENT_DRIVEN")
        assert result["event_signature"] == again[ticker]["handler_result"]["event_signature"]
        assert not result["intraday_equity_scanner"] and result["entry_authority"] is False
    future = deepcopy(rows)
    future[1]["nav"] = clocked(11, received_at=AT+timedelta(seconds=1))
    cutoff = by_ticker(family_reports(path, as_of=AT, catalog=catalog, sources={"family_evidence": rows+future}))
    assert cutoff["FUND"]["handler_result"]["terms"]["nav"]["value"] == 10
    observed = by_ticker(family_reports(path, as_of=AT+timedelta(seconds=1), catalog=catalog, sources={"family_evidence": rows+future}))
    assert observed["FUND"]["handler_result"]["event_signature"] != before["FUND"]["handler_result"]["event_signature"]


def test_resolver_latest_invalid_path_cannot_resurrect_old_values_or_suppress_other_path():
    def candidate(value, source, seconds=0, **kwargs):
        return {"value": value, "source": source, "source_at": (AT+timedelta(seconds=seconds)).isoformat(),
                "received_at": (AT+timedelta(seconds=seconds)).isoformat(), **kwargs}
    resolved = resolve_field([candidate(100, "PPI", -30), candidate(None, "PPI", valid=False),
                             candidate(101, "IOL")], as_of=AT)
    assert resolved["value"] == 101 and resolved["authority"] == "IOL_COMPLEMENT"
    assert next(row for row in resolved["candidates"] if row["source"] == "PPI")["rejection_reason"] == "SOURCE_UNAVAILABLE"
    assert resolved["provider_state"] == "NO_VERIFICADO"
    conflict = resolve_field([candidate(100, "PPI"), candidate(110, "PPI")], as_of=AT)
    assert conflict["value"] is None and conflict["review_required"]
    assert conflict["conflicts"][0]["reason"] == "DUPLICATE_SOURCE_EVIDENCE_CONFLICT"
    assert source_rank("UNVERIFIED_PPI_HYPOTHESIS") == 3


@pytest.mark.parametrize("native", [None, "15:04", "2026-10-02T15:04:00"])
def test_fresh_capture_does_not_validate_legacy_provider_clock(native):
    ppi = {**quote_claim(instrument("GGAL", "ACCIONES")), "source_at": None,
           "provider_observed_at": native, "observed_at": AT.isoformat()}
    result = reconcile(ppi, {}, now=AT)
    assert result["freshness"]["PPI"] == "UNKNOWN"
    assert result["field_provenance"]["last"]["source_at"] is None
    assert result["field_provenance"]["last"]["freshness"] == "NO_VERIFICADO"
    assert result["identity_overwritten"] is False


def test_legacy_consolidation_distinguishes_currencies_and_preserves_duplicate_conflicts(monkeypatch):
    monkeypatch.setattr("rc6_source_consolidation._now", lambda: AT.isoformat())
    ars = quote_claim(instrument("SAME", "ACCIONES"), last=100)
    usd = quote_claim(instrument("SAME", "ACCIONES", currency="USD"), last=10)
    result = consolidate([ars, usd], [{**ars, "bid": 99.9}])
    assert len(result["rows"]) == 2
    rows = {row["currency"]: row for row in result["rows"]}
    assert rows["ARS"]["validated_effective_fields"]["last"]["value"] == 100
    assert rows["USD"]["validated_effective_fields"]["last"]["value"] == 10
    assert rows["ARS"]["validated_effective_fields"]["bid"]["source"] == "IOL"
    assert rows["USD"]["effective_fields"]["bid"]["value"] is None
    conflicted = consolidate([ars, {**ars, "last": 150}], [])
    row = conflicted["rows"][0]
    assert row["review_status"] == "CONFLICT_REVIEW_REQUIRED"
    assert row["duplicate_conflicts"]["PPI"]
    assert row["effective_fields"]["last"]["value"] is None
    assert not row["selection_eligible"] and not row["entry_authority"]
    assert len(row["source_candidates"]["PPI"]) == 2


def test_legacy_ambiguous_identity_remains_reference_and_native_timestamp_beats_capture(monkeypatch):
    monkeypatch.setattr("rc6_source_consolidation._now", lambda: AT.isoformat())
    record = quote_claim(instrument("GGAL", "ACCIONES"))
    missing = {key: value for key, value in record.items() if key != "currency"}
    row = consolidate([missing], [{**record, "last": 101}])["rows"][0]
    assert row["canonical_identity"] is None and row["currency"] is None
    assert not row["selection_eligible"]
    stale = {**record, "source_at": None, "timestamp": (AT-timedelta(hours=1)).isoformat(), "observed_at": AT.isoformat()}
    row = consolidate([stale], [])["rows"][0]
    assert row["freshness"]["PPI"] == "STALE"
    assert row["validated_effective_fields"]["last"]["value"] is None
    assert row["field_provenance"]["last"]["candidates"][0]["source_at"] == stale["timestamp"]


def test_collector_never_injects_capture_as_provider_timestamp():
    tree = ast.parse(Path("scripts/rc6_iol_shadow_collect.py").read_text())
    injections = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute) and node.func.attr == "setdefault"
        and node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == "provider_observed_at"]
    assert not injections
    assert 'row.setdefault("captured_at", observed)' in Path("scripts/rc6_iol_shadow_collect.py").read_text()


def test_canonical_worker_calls_family_policy_and_persists_source_authority(tmp_path):
    from tests.test_rc6_shadow_runtime_wiring import make_store, PRE
    from rc6_shadow_runtime.worker import ShadowRuntime
    store, _ = make_store(tmp_path, count=3)
    worker = ShadowRuntime(store.path, evidence_root=tmp_path / "shadow", source_roots=[])
    report = worker.tick(PRE)
    assert {row["family"] for row in report["family_routing"]["families"]} == FAMILIES
    assert report["family_routing"]["source_authority"]["identity_primary"] == "PPI_CATALOG"
    with worker.files as files:
        durable = files.read("latest.json.gz")
    assert durable["family_routing"] == json.loads(json.dumps(report["family_routing"]))
    assert durable["real_orders_sent"] == 0 and durable["provider_requests"] == 0


def test_unknown_complement_quote_units_remain_unverified_for_deep_tradeability(tmp_path):
    record = instrument("GGAL", "ACCIONES")
    path = fixture_db(tmp_path, [record])
    snapshot(path, record, ask=0)
    iol = quote_claim(record, last=None, ask=100.05, price_unit=None)
    quote = by_ticker(family_reports(path, as_of=AT, catalog=[record], sources={"IOL": {"symbols": [iol]}}))["GGAL"]["handler_result"]["quote"]
    assert quote["price"] == 100 and quote["ask"] == 100.05  # Reference evidence remains visible.
    assert quote["price_basis_status"] == "NO_VERIFICADO" and not quote["tradeable"]


def test_reconciliation_rejects_incompatible_complement_identity_and_normalizes_aliases():
    ppi = quote_claim(instrument("GGAL", "ACCIONES"), bid=99, ask=101, book_at=AT.isoformat())
    compatible = {**ppi, "market": "BCBA", "settlement": "T1", "term": "T1"}
    result = reconcile(ppi, compatible, now=AT)
    assert result["identity_conflicts"] == []
    assert result["identity_primary"]["market"] == "BYMA"
    wrong = {**ppi, "currency": "USD", "bid": 10}
    result = reconcile({**ppi, "bid": None}, wrong, now=AT)
    assert result["contract_state"] == "BLOCKED_CONFLICT"
    assert result["effective_fields"]["bid"]["value"] is None
    assert result["identity_primary"]["currency"] == "ARS" and result["identity_overwritten"] is False
    assert not result["selection_eligible"]


def test_resolver_duplicate_evidence_is_bounded_and_preserves_review():
    candidates = [{"value": value, "source": "PPI", "source_at": AT.isoformat(), "received_at": AT.isoformat()} for value in range(100)]
    result = resolve_field(candidates, as_of=AT)
    assert result["input_count"] == 100
    assert result["value"] is None and result["review_required"]
    assert result["evidence_truncated"] and len(result["candidates"]) <= 16


def test_primary_collector_uses_trade_clock_not_receipt_and_keeps_native_book(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    import scripts.rc6_iol_shadow_collect as collector
    record = instrument("GGAL", "ACCIONES")
    path = fixture_db(tmp_path, [record])
    current = datetime.now(timezone.utc)-timedelta(seconds=1)
    snapshot(path, record, source_at=current, received_at=current, book_at=current-timedelta(hours=1))
    monkeypatch.setattr(collector, "DEFAULT_ROOT", tmp_path)
    monkeypatch.setattr(collector, "DEFAULT_DB", str(path))
    monkeypatch.delenv("POROTA_PRIMARY_LAST_CACHE_PATH", raising=False)
    monkeypatch.delenv("POROTA_OBSERVER_DB", raising=False)
    values, metadata = collector._primary_snapshot()
    assert values["GGAL"]["provider_observed_at"] == current.isoformat()
    assert values["GGAL"]["book_at"] == (current-timedelta(hours=1)).isoformat()
    assert metadata["provider_clock_basis"] == "NATIVE_TRADE_AT"
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE market_snapshots SET trade_at=NULL")
    values, metadata = collector._primary_snapshot()
    assert values == {} and metadata["provider_available"] is None
    assert metadata["identity_reviews"][0]["reason"] == "NATIVE_QUOTE_CLOCK_MISSING_STALE_OR_UNAVAILABLE"
    assert collector._parse_time("2026-10-02T15:04:00") is None


def test_primary_collector_does_not_pick_one_currency_for_ambiguous_symbol(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    import scripts.rc6_iol_shadow_collect as collector
    catalog = [instrument("SAME", "ACCIONES"), instrument("SAME", "ACCIONES", currency="USD")]
    path = fixture_db(tmp_path, catalog)
    current = datetime.now(timezone.utc)-timedelta(seconds=1)
    for record in catalog:
        snapshot(path, record, source_at=current, received_at=current)
    monkeypatch.setattr(collector, "DEFAULT_ROOT", tmp_path)
    monkeypatch.setattr(collector, "DEFAULT_DB", str(path))
    monkeypatch.delenv("POROTA_PRIMARY_LAST_CACHE_PATH", raising=False)
    monkeypatch.delenv("POROTA_OBSERVER_DB", raising=False)
    values, metadata = collector._primary_snapshot()
    assert values == {} and metadata["provider_available"] is None
    review = next(row for row in metadata["identity_reviews"] if row["reason"] == "MULTIPLE_CURRENCY_MARKET_SETTLEMENT_IDENTITIES")
    assert len(review["identities"]) == 2 and review["identity_assigned"] is False


def test_primary_cache_capture_and_legacy_scalar_never_fabricate_native_identity(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    import scripts.rc6_iol_shadow_collect as collector
    current = datetime.now(timezone.utc)-timedelta(seconds=1)
    path = tmp_path / "primary_last.json"
    path.write_text(json.dumps({"source": "PPI", "market": "BYMA", "captured_at": current.isoformat(),
                               "quotes_by_symbol": {"GGAL": 100}}))
    monkeypatch.setenv("POROTA_PRIMARY_LAST_CACHE_PATH", str(path))
    values, metadata = collector._primary_snapshot()
    assert values == {} and metadata["capture_clock_basis"] == "AVAILABILITY_ONLY"
    assert metadata["identity_reviews"][0]["identity_assigned"] is False
    assert metadata["provider_available"] is None


def test_full_evidence_preserves_currency_catalog_and_native_source_identity(tmp_path, monkeypatch):
    import scripts.rc6_full_universe_evidence as evidence
    catalog = [instrument("SAME", "ACCIONES"), instrument("SAME", "ACCIONES", currency="USD")]
    path = fixture_db(tmp_path, catalog)
    for record, price in zip(catalog, [100, 10]):
        snapshot(path, record, price=price, bid=price*.999, ask=price*1.001)
    monkeypatch.setattr(evidence, "DB", str(path))
    monkeypatch.setattr(evidence, "ROOT", tmp_path)
    monkeypatch.setattr(evidence, "now", lambda: AT)
    monkeypatch.delenv("POROTA_PRIMARY_LAST_CACHE_PATH", raising=False)
    assert len(evidence.catalog_rows()) == 2
    source = evidence.primary_rows(AT)
    assert source["SAME"]["canonical_identity"] is None
    report = evidence.build()
    assert report["universe"]["catalog_count"] == report["universe"]["ledger_count"] == 2
    prices = {row["currency"]: float(row["sources"]["PPI"]["fields"]["last"]) for row in report["instruments"]}
    assert prices == {"ARS": 100, "USD": 10}
    assert all(not row["entry_authority"] for row in report["instruments"])
    assert all(row["sources"]["PPI"]["identity"] == row["canonical_identity"] for row in report["instruments"])


def test_full_evidence_public_capture_is_not_freshness_or_paper_authority(tmp_path, monkeypatch):
    import scripts.rc6_full_universe_evidence as evidence
    record = instrument("GGAL", "ACCIONES")
    path = fixture_db(tmp_path, [record])
    monkeypatch.setattr(evidence, "DB", str(path))
    monkeypatch.setattr(evidence, "ROOT", tmp_path)
    monkeypatch.setattr(evidence, "now", lambda: AT)
    monkeypatch.delenv("POROTA_PRIMARY_LAST_CACHE_PATH", raising=False)
    public_path = tmp_path / "rc6_public_sources_latest.json"
    public = {"collected_at": AT.isoformat(), "sources": [{"source": "BYMA", "observed_at": AT.isoformat(),
        "records": [quote_claim(record, source_at=None)]}]}
    public_path.write_text(json.dumps(public))
    source, metadata = evidence.byma_rows()
    assert evidence.source_view(source["GGAL"], "BYMA", AT)["freshness"]["state"] == "UNKNOWN"
    assert metadata["capture_clock_basis"] == "AVAILABILITY_ONLY"
    public["sources"][0]["records"][0]["source_at"] = AT.isoformat()
    public_path.write_text(json.dumps(public))
    report = evidence.build()
    row = report["instruments"][0]
    assert row["sources"]["BYMA"]["freshness"]["state"] == "FRESH"
    assert row["status"] == "READY_SHADOW_PARTIAL" and not row["paper_auto_enabled"]
    assert not row["sources"]["BYMA"]["entry_authority"]


def test_full_evidence_unavailable_iol_is_path_scoped_with_native_reference_visible(tmp_path, monkeypatch):
    import scripts.rc6_full_universe_evidence as evidence
    record = instrument("GGAL", "ACCIONES")
    monkeypatch.setattr(evidence, "ROOT", tmp_path)
    row = quote_claim(record, state="UNAVAILABLE", source_path="IOL:quote:GGAL")
    (tmp_path / "iol_shadow_latest.json").write_text(json.dumps({"symbols": [row]}))
    view = evidence.source_view(evidence.iol_rows()["GGAL"], "IOL", AT)
    assert view["present"] and view["structured"] and view["freshness"]["state"] == "FRESH"
    assert not view["selection_eligible"] and view["provider_available"] is None
    assert view["source_path"] == "IOL:quote:GGAL"


def test_composite_ppi_identity_label_does_not_grant_iol_field_primary_authority(tmp_path):
    record = instrument("BOND", "BONOS")
    path = fixture_db(tmp_path, [record])
    snapshot(path, record)
    ppi = fixed_row(record)
    composite = source_row(record, quote_basis_nominal=clocked(1000, source="PPI_SEARCH_INSTRUMENT_DESCRIPTION+IOL_ASSET_INFO"))
    report = family_reports(path, as_of=AT, catalog=[record], sources={"family_evidence": [ppi, composite]})
    field = by_ticker(report)["BOND"]["handler_result"]["terms"]["quote_basis_nominal"]
    assert field["value"] == 100 and field["authority"] == "PPI_PRIMARY"
    other = next(row for row in field["candidates"] if "+IOL" in row["source"])
    assert other["authority"] == "IOL_COMPLEMENT"
    assert other["field_origin_status"] == "COMPOSITE_COMPLEMENT_ORIGIN"
    assert field["review_required"]
    assert source_rank("PPI_SEARCH_INSTRUMENT_DESCRIPTION+BYMA_OPTION_CONTRACT_2026") != 0


def test_source_paths_are_namespaced_by_source_and_cannot_displace_primary():
    ppi = {"source": "PPI", "source_path": "current", "value": 100,
           "source_at": (AT-timedelta(seconds=30)).isoformat(), "received_at": (AT-timedelta(seconds=30)).isoformat()}
    iol = {"source": "IOL", "source_path": "current", "value": 110,
           "source_at": AT.isoformat(), "received_at": AT.isoformat()}
    field = resolve_field([ppi, iol], as_of=AT)
    assert field["value"] == 100 and field["source"] == "PPI" and field["review_required"]
    assert {row["source"] for row in field["candidates"]} == {"PPI", "IOL"}


def test_dynamic_field_capture_clock_without_native_basis_stays_unverified(tmp_path):
    record = instrument("AAPL", "CEDEARS")
    path = fixture_db(tmp_path, [record])
    snapshot(path, record)
    row = source_row(record, ccl={"value": 1300, "source": "IOL", "observed_at": AT.isoformat(), "received_at": AT.isoformat()})
    features = by_ticker(family_reports(path, as_of=AT, catalog=[record], sources={"family_evidence": [row]}))["AAPL"]["handler_result"]["cedear_features"]
    assert features["ccl"]["value"] is None and features["ccl"]["status"] == "NO_VERIFICADO"


def test_static_catalog_availability_never_becomes_a_provider_event_clock(tmp_path):
    record = instrument("BOND", "BONOS")
    path = fixture_db(tmp_path, [record])
    snapshot(path, record)
    metadata = {"financial_contract_v17": {"family": "BONOS", "cash_multiplier": .01,
        "quantity_step": 1, "minimum_quantity": 1, "metadata_source": "PPI",
        "fixed_income_evidence": {"quote_basis_nominal": 100},
        "fixed_income_analytics": {"yield": .05}}}
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("UPDATE financial_instrument_catalog SET metadata_json=?", (json.dumps(metadata),))
    fixed = by_ticker(family_reports(path, as_of=AT, catalog=[record]))["BOND"]["handler_result"]
    basis = fixed["terms"]["quote_basis_nominal"]
    assert fixed["nominal_contract_status"] == "VERIFIED_NOMINAL_CONTRACT"
    assert basis["value"] == 100 and basis["received_at"] == AT.isoformat()
    assert basis["source_at"] is None and basis["native_clock_status"] == "NO_VERIFICADO"
    assert basis["age_seconds"] is None
    assert fixed["analytics"]["yield"]["value"] is None  # The nested dynamic field cannot borrow quote clocks.


def test_legacy_strict_selection_requires_actual_primary_origin_and_receipt_clock():
    record = quote_claim(instrument("GGAL", "ACCIONES"), source="PPI", bid=99, ask=101, book_at=AT.isoformat())
    assert reconcile(record, {}, now=AT)["selection_eligible"]
    assert consolidate([record], [], as_of=AT)["rows"][0]["selection_eligible"]
    no_receipt = {key: value for key, value in record.items() if key != "received_at"}
    reconciled = reconcile(no_receipt, {}, now=AT)
    assert not reconciled["selection_eligible"]
    assert reconciled["field_provenance"]["last"]["freshness"] == "NO_VERIFICADO"
    consolidated = consolidate([no_receipt], [], as_of=AT)["rows"][0]
    assert not consolidated["selection_eligible"]
    assert consolidated["validated_effective_fields"]["last"]["value"] is None
    mixed = {**record, "source": "PPI+IOL"}
    reconciled = reconcile(mixed, {}, now=AT)
    assert not reconciled["selection_eligible"] and reconciled["identity_primary"] is None
    assert reconciled["field_provenance"]["last"]["source"] == "PPI+IOL"
    assert reconciled["field_provenance"]["last"]["authority"] == "IOL_COMPLEMENT"
    consolidated = consolidate([mixed], [], as_of=AT)["rows"][0]
    assert not consolidated["selection_eligible"]
    assert consolidated["identity_primary_source"] == "COMPLEMENT_REFERENCE_ONLY"
    assert consolidated["field_provenance"]["last"]["authority"] == "IOL_COMPLEMENT"
    assert consolidated["review_status"] == "CONFLICT_REVIEW_REQUIRED"


@pytest.mark.parametrize("invalid_price", [True, float("nan"), float("inf"), 0])
def test_legacy_strict_selection_cannot_promote_non_price_values(invalid_price):
    record = quote_claim(instrument("GGAL", "ACCIONES"), source="PPI", last=invalid_price,
                         bid=99, ask=101, book_at=AT.isoformat())
    assert not reconcile(record, {}, now=AT)["selection_eligible"]
    consolidated = consolidate([record], [], as_of=AT)["rows"][0]
    assert not consolidated["selection_eligible"]
    json.dumps(consolidated, allow_nan=False)
    if isinstance(invalid_price, float):
        assert consolidated["source_candidates"]["PPI"][0]["last"]["status"] == "NO_VERIFICADO_NON_FINITE_NUMBER"


@pytest.mark.parametrize("fixture_case", [
    test_missing_book_clock_cannot_borrow_trade_or_capture_clock,
    test_static_catalog_availability_never_becomes_a_provider_event_clock,
])
def test_source_fixture_writers_are_closed_before_family_capture_without_gc(tmp_path, monkeypatch, fixture_case):
    """Exercise the actual fixture mutations, holding their writers strongly."""
    connections = []
    original_connect = sqlite3.connect
    original_capture = family_reports
    captured = []
    def tracked_connect(*args, **kwargs):
        connection = original_connect(*args, **kwargs)
        connections.append(connection)
        return connection
    def assert_closed():
        assert len(connections) >= 3  # Database, snapshot, native fixture mutation.
        for connection in connections:
            with pytest.raises(sqlite3.ProgrammingError, match="closed"):
                connection.execute("SELECT 1")
    def capture(*args, **kwargs):
        assert_closed()
        captured.append(True)
        return original_capture(*args, **kwargs)
    monkeypatch.setattr(sqlite3, "connect", tracked_connect)
    monkeypatch.setitem(globals(), "family_reports", capture)
    fixture_case(tmp_path)
    assert captured == [True]
    assert_closed()
