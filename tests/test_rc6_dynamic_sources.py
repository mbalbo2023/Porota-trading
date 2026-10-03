"""Source adapters prove timestamp, identity and unit boundaries without I/O."""
from copy import deepcopy
from datetime import datetime, timezone

import pytest

from rc6_dynamic_universe.sources import audit_sources, source_observations


AT = "2026-10-02T14:31:00+00:00"


def row(**values):
    return {"symbol": "NEW", "family": "ACCIONES", "market": "BYMA", "currency": "ARS",
            "term": "T1", "timestamp": "2026-10-02T14:30:40+00:00",
            "captured_at": "2026-10-02T14:30:45+00:00", "last": 100,
            "price_unit": "PER_SHARE", "volume": 1000, "volume_unit": "SHARES", **values}


def observations(records, **kwargs):
    return source_observations({"records": records}, source="BYMA", as_of=AT, **kwargs)


def test_raw_byma_snapshot_preserves_exact_identity_provider_and_capture_time():
    payload = {"collected_at": "2026-10-02T14:30:45+00:00", "sources": [{
        "source": "BYMA", "status": "SCRAPED_PUBLIC_DATA", "records": [row()], "errors": []}]}
    result = source_observations(payload, source="BYMA", as_of=AT)
    observed = result["observations"][0]
    assert observed["identity"] == ["NEW", "ACCIONES", "BYMA", "ARS", "A-24HS"]
    assert observed["source_at"] == "2026-10-02T14:30:40+00:00"
    assert observed["received_at"] == "2026-10-02T14:30:45+00:00"
    assert observed["useful"] is True
    assert observed["decision_effect"] == "OBSERVE_ONLY"
    assert observed["live_decision_authority"] is False


@pytest.mark.parametrize("provider_time", [None, "14:30", "2026-10-02T14:30:40"])
def test_capture_timestamp_never_substitutes_for_missing_aware_provider_time(provider_time):
    observed = observations([row(timestamp=provider_time)])["observations"][0]
    assert observed["source_at"] is None
    assert observed["received_at"] is not None
    assert observed["reason"] == "PROVIDER_TIMESTAMP_MISSING_OR_AMBIGUOUS"
    assert not observed["useful"]


@pytest.mark.parametrize("key,value", [("currency", ""), ("term", ""), ("market", ""),
                                      ("family", ""), ("term", "1"), ("currency", "Pesos")])
def test_identity_ambiguity_excluded_without_modifying_source_catalogue(key, value):
    snapshot = row(**{key: value})
    original = deepcopy(snapshot)
    report = observations([snapshot])
    assert snapshot == original
    assert report["observations"][0]["identity"] is None
    assert report["observations"][0]["reason"] == "EXACT_IDENTITY_AMBIGUOUS"
    assert report["counts"] == {"seen": 1, "useful": 0, "rejected": 1}


@pytest.mark.parametrize("values", [{"ticker": "OTHER"}, {"asset_type": "BONOS"}, {"settlement": "T0"}])
def test_conflicting_identity_evidence_cannot_be_silently_coalesced(values):
    observed = observations([row(**values)])["observations"][0]
    assert observed["identity"] is None
    assert observed["reason"] == "EXACT_IDENTITY_AMBIGUOUS"


@pytest.mark.parametrize("snapshot", [{"records": None}, {"sources": None},
                                     {"sources": [{"source": "BYMA", "records": "non-json-like-content"}]}])
def test_malformed_source_containers_are_reported_without_fabricated_provider_counts(snapshot):
    report = source_observations(snapshot, source="BYMA", as_of=AT)
    assert report["observations"] == []
    assert report["status"] == "PARTIAL_SOURCE_ERRORS"
    assert report["errors"]
    assert report["provider_available"] is None


@pytest.mark.parametrize("values,reason", [
    ({"timestamp": "2026-10-02T14:32:00+00:00"}, "PROVIDER_TIMESTAMP_IN_FUTURE"),
    ({"captured_at": "2026-10-02T14:32:00+00:00"}, "NOT_AVAILABLE_AT_CUTOFF"),
    ({"captured_at": "2026-10-02T14:30:30+00:00"}, "PROVIDER_TIMESTAMP_AFTER_RECEIPT"),
    ({"timestamp": "2026-10-02T14:28:00+00:00"}, "STALE_QUOTES"),
])
def test_promotion_inputs_are_point_in_time_and_age_bounded(values, reason):
    observed = observations([row(**values)])["observations"][0]
    assert observed["reason"] == reason
    assert not observed["useful"]


def test_iol_runtime_uses_provider_quote_time_not_fresh_publication_label():
    snapshot = {"schema_version": 3, "refreshed_at": "2026-10-02T14:30:50+00:00",
                "cache_state": "LIVE_FRESH", "symbols": [row(
                    family="", asset_type="CEDEAR", market="BCBA", state="READY",
                    timestamp=None, quote={"provider_observed_at": "2026-10-02T14:20:00-00:00",
                                           "last": 100, "price_unit": "PER_UNIT"})]}
    report = source_observations(snapshot, source="IOL_MCP", as_of=AT)
    observed = report["observations"][0]
    assert observed["identity"][1] == "CEDEARS"
    assert observed["identity"][2] == "BYMA"
    assert observed["reason"] == "STALE_QUOTES"


def test_nominal_volume_and_cash_depth_are_not_guessed_from_raw_fields():
    observed = observations([row(price_unit=None, volume_unit=None, bid=99, ask=101,
                                bid_size=40, ask_size=50)])["observations"][0]
    assert observed["fields"] == {"spread_bps": pytest.approx(202.020202)}
    assert observed["units"] == {}
    assert "depth" not in observed["fields"]


def test_volume_semantics_must_be_explicit_before_rvol_input_is_usable():
    ordinary = observations([row()])["observations"][0]
    cumulative = observations([row(volume_semantics="CUMULATIVE_SESSION")])["observations"][0]
    assert "cumulative_volume" not in ordinary["fields"]
    assert cumulative["fields"]["cumulative_volume"] == 1000
    assert cumulative["units"]["cumulative_volume"] == "SHARES"


def test_explicit_units_preserved_without_converting_across_families():
    observed = observations([row(family="BONOS", price_unit="PER_100_NOMINALS",
                                volume_unit="NOMINALS", depth=200,
                                depth_unit="NOMINALS")])["observations"][0]
    assert observed["fields"]["price"] == 100
    assert observed["units"] == {"price": "PER_100_NOMINALS", "volume": "NOMINALS", "depth": "NOMINALS"}


def test_partial_provider_failures_are_visible_and_do_not_become_provider_zero():
    snapshot = {"sources": [{"source": "BYMA", "records": [row()],
                             "errors": ["options:TimeoutError", "cedears:HTTPError"]}]}
    report = source_observations(snapshot, source="BYMA", as_of=AT)
    assert report["status"] == "PARTIAL_SOURCE_ERRORS"
    assert len(report["errors"]) == 2
    assert report["counts"]["useful"] == 1
    assert report["provider_available"] is None
    assert report["real_orders_sent"] == 0
    assert report["real_order_routes"] == "NOT_CALLED"


def test_per_instrument_native_capability_gap_is_preserved():
    report = observations([row(state="UNAVAILABLE", reason="PPI_INSTRUMENT_NOT_FOUND"), row(symbol="OTHER")])
    assert report["counts"]["useful"] == 1
    assert report["observations"][0]["native_reason"] == "PPI_INSTRUMENT_NOT_FOUND"
    assert report["observations"][0]["useful"] is False
    assert report["observations"][1]["useful"] is True


def test_ppi_dom_needs_explicit_columns_and_does_not_invent_market_or_settlement():
    payload = {"generated_at": "2026-10-02T14:30:45+00:00", "routes": [{
        "requested": "/Cotizaciones/Acciones", "reached": True, "tables": [{
            "materializable": True, "headers": ["Ticker", "Moneda", "Ultimo", "Timestamp", "price_unit"],
            "rows": [["NEW", "ARS", "100", "2026-10-02T14:30:40Z", "PER_SHARE"]]}]}]}
    report = source_observations(payload, source="PPI_AUTHENTICATED_WEB", as_of=AT)
    assert report["observations"][0]["reason"] == "EXACT_IDENTITY_AMBIGUOUS"
    assert report["observations"][0]["identity"] is None


def test_ppi_dom_complete_explicit_table_is_shadow_discovery_only():
    payload = {"generated_at": "2026-10-02T14:30:45+00:00", "routes": [{
        "requested": "/Cotizaciones/Acciones", "reached": True, "tables": [{
            "materializable": True,
            "headers": ["Ticker", "Moneda", "Mercado", "Plazo", "Ultimo", "Timestamp", "price_unit"],
            "rows": [["NEW", "ARS", "BYMA", "T1", "100", "2026-10-02T14:30:40Z", "PER_SHARE"]]}]}]}
    report = source_observations(payload, source="PPI_WEB", as_of=AT)
    assert report["observations"][0]["useful"]
    assert report["observations"][0]["decision_effect"] == "OBSERVE_ONLY"


def test_closed_market_evidence_never_establishes_session_capacity_or_realtime_coverage():
    audit = audit_sources()
    byma = audit["sources"]["BYMA_PUBLIC_SCRAPER"]
    assert byma["runtime_evidence"]["record_count"] == 1
    assert byma["runtime_evidence"]["market_phase"] == "CLOSED_SATURDAY"
    assert byma["freshness"] == "NO_VERIFICADO_DURING_SESSION"
    assert byma["configured_cadence_is_confirmed_latency"] is False
    assert audit["sufficiently_fresh_transversal_radar"] == "NO_VERIFICADO"
    assert audit["sources"]["PPI_API"]["capacity_limit"] == "NO_VERIFICADO"


def test_source_adapter_owns_no_provider_or_order_transport():
    import ast
    from pathlib import Path
    tree = ast.parse(Path("rc6_dynamic_universe/sources.py").read_text())
    imports = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    imports |= {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    assert imports == {"collections.abc", "datetime", "common"}
    report = source_observations({}, source="BYMA", as_of=datetime(2026, 10, 2, tzinfo=timezone.utc))
    assert report["status"] == "SOURCE_UNAVAILABLE"
    assert report["provider_state"] == "NO_VERIFICADO"


def test_snapshot_audit_requires_cutoff_and_rejects_unaudited_source():
    with pytest.raises(ValueError, match="CUTOFF"):
        audit_sources({"IOL": {}})
    with pytest.raises(ValueError, match="SOURCE_NOT_AUDITED"):
        source_observations({}, source="UNAVAILABLE_NEW_PROVIDER", as_of=AT)
    with pytest.raises(ValueError, match="AWARE_TIMESTAMP_REQUIRED"):
        source_observations({}, source="BYMA", as_of="2026-10-02T14:31:00")
