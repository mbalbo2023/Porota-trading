"""F-04: source audit links the actual caller evidence and cannot claim emptiness."""
from copy import deepcopy

import pytest

from rc6_dynamic_universe.common import digest
from rc6_dynamic_universe.live import run_shadow
from rc6_dynamic_universe.sources import audit_sources, native_source_reports, source_observations
from rc6_shadow_runtime.source_authority import resolve_field


AT = "2026-10-05T13:20:10+00:00"


def quote(**changes):
    return {"ticker": "S1", "instrument_type": "ACCIONES", "market": "BYMA",
        "currency": "ARS", "settlement": "A-24HS", "price": 100, "price_unit": "PER_SHARE",
        "timestamp": "2026-10-05T13:20:00+00:00", "received_at": "2026-10-05T13:20:05+00:00",
        "source_path": "fixture/current/S1", "provenance": {"capture_digest": "capture-A"}, **changes}


def bundle(**changes):
    return {"safety": {"mode": "SIMULATION", "real_orders_sent": 0, "real_routes": "NOT_CALLED"},
        "as_of": AT, "session_open": "2026-10-05T13:30:00+00:00", "frozen_at": AT,
        "preopen_cutoff": "2026-10-02T20:00:00+00:00", "sessions": ["2026-10-02"],
        "catalog": [quote()], **changes}


def assert_links(report):
    audit, reports = report["source_audit"], report["source_reports"]
    assert audit["source_reports_digest"] == digest(reports)
    assert audit["source_report_count"] == len(reports)
    for index, source in enumerate(reports):
        link = audit["snapshots"][str(index)]
        assert link["report_pointer"] == f"/source_reports/{index}"
        assert link["report_digest"] == digest(source)
        assert link["counts"] == source["counts"]
        assert "observations" not in link
    assert audit["live_decision_authority"] is False
    assert audit["real_orders_sent"] == 0


def test_original_f04_nonempty_ppi_reports_cannot_have_empty_audit():
    report = run_shadow(bundle(sources={"PPI_API": {"records": [quote()]}}))
    assert report["source_reports"][0]["counts"]["seen"] == 1
    assert report["source_audit"]["snapshots"]
    assert_links(report)


def test_native_ppi_only_is_audited_without_reingesting_observations():
    native = {"identity": ["S1", "ACCIONES", "BYMA", "ARS", "A-24HS"],
        "source": "PPI_MARKETDATA_INTRADAY", "endpoint": "intraday",
        "source_at": "2026-10-05T13:20:00+00:00", "received_at": "2026-10-05T13:20:05+00:00",
        "source_path": "sqlite:paper_intraday_observations", "useful": True,
        "fields": {"price": 100}, "units": {"price": "NO_VERIFICADO"}, "intraday_confirmed": False}
    report = run_shadow(bundle(observations=[native]))
    rows = report["source_reports"][0]["observations"]
    assert rows == [native]
    assert report["source_reports"][0]["native_input"] is True
    assert report["source_reports"][0]["counts"]["seen"] == 1
    assert_links(report)


def test_ppi_iol_disagreement_preserves_primary_path_units_clocks_and_conflict():
    snapshots = {"PPI_API": {"records": [quote()]},
        "IOL": {"symbols": [quote(price=120, source_path="fixture/iol/S1", conflicts=["PRICE_DISCREPANCY"])]}}
    report = run_shadow(bundle(sources=snapshots))
    reports = report["source_reports"]
    candidates = [{"value": row["fields"]["price"], "unit": row["units"]["price"],
        **{key: row[key] for key in ("source", "source_path", "source_at", "received_at")}}
        for source in reports for row in source["observations"]]
    resolved = resolve_field(candidates, as_of=AT)
    assert resolved["source"] == "PPI_API" and resolved["value"] == 100
    assert resolved["review_required"] and not resolved["entry_authority"]
    assert reports[1]["observations"][0]["conflicts"] == ["PRICE_DISCREPANCY"]
    assert reports[0]["observations"][0]["provenance"] == {"capture_digest": "capture-A"}
    assert_links(report)


def test_byma_scraper_remains_observe_only_and_linked():
    report = run_shadow(bundle(sources={"BYMA": {"records": [quote()]}}))
    row = report["source_reports"][0]["observations"][0]
    assert row["source"] == "BYMA_PUBLIC_SCRAPER"
    assert row["decision_effect"] == "OBSERVE_ONLY"
    assert row["live_decision_authority"] is False
    assert_links(report)


@pytest.mark.parametrize("change,reason", [
    ({"timestamp": "2026-10-05T13:00:00+00:00"}, "STALE_QUOTES"),
    ({"timestamp": None}, "PROVIDER_TIMESTAMP_MISSING_OR_AMBIGUOUS"),
    ({"timestamp": "2026-10-05T13:21:00+00:00"}, "PROVIDER_TIMESTAMP_IN_FUTURE"),
    ({"price_unit": None}, "NUMERIC_FIELDS_OR_UNITS_UNVERIFIED"),
])
def test_rejected_source_still_has_truthful_nonempty_audit(change, reason):
    report = run_shadow(bundle(sources={"PPI_API": {"records": [quote(**change)]}}))
    row = report["source_reports"][0]["observations"][0]
    assert row["reason"] == reason and not row["useful"]
    assert row["source_at"] != row["received_at"]
    assert report["source_audit"]["snapshots"]["0"]["counts"]["rejected"] == 1
    assert_links(report)


def test_partial_iol_is_visible_with_unknown_availability_instead_of_provider_zero():
    report = run_shadow(bundle(sources={"IOL": {"symbols": [quote()], "errors": ["options:TimeoutError"]}}))
    source = report["source_reports"][0]
    assert source["status"] == "PARTIAL_SOURCE_ERRORS"
    assert source["errors"] and source["counts"]["seen"] == 1
    assert source["provider_available"] is None
    assert report["source_audit"]["snapshots"]["0"]["provider_available"] is None
    assert_links(report)


def test_missing_source_has_explicit_report_and_empty_input_has_explicit_audit():
    missing = run_shadow(bundle(sources={"IOL": {}}))
    assert missing["source_reports"][0]["status"] == "SOURCE_UNAVAILABLE"
    assert missing["source_audit"]["snapshots"]["0"]["status"] == "SOURCE_UNAVAILABLE"
    empty = run_shadow(bundle())
    assert empty["source_audit"]["status"] == "NO_SOURCE_REPORTS"
    assert empty["source_audit"]["source_reports_digest"] == digest([])


def test_passed_reports_are_single_authority_and_original_inputs_are_unchanged():
    source = source_observations({"records": [quote()]}, source="PPI_API", as_of=AT)
    original = deepcopy(source)
    report = run_shadow(bundle(source_observation_reports=[source], sources={"IOL": {}}))
    assert source == original
    assert [item["source"] for item in report["source_reports"]] == ["PPI_API"]
    assert report["source_reports"][0]["runtime_ingestion"]["accepted"] == 1
    assert_links(report)


@pytest.mark.parametrize("mutation,reason", [
    (lambda report: report["counts"].update(seen=7), "COUNTS_MISMATCH"),
    (lambda report: report.update(as_of="2026-10-05T13:20:11+00:00"), "CUTOFF_MISMATCH"),
    (lambda report: report.update(observations=[None]), "OBSERVATION_SHAPE"),
])
def test_incoherent_source_reports_are_rejected(mutation, reason):
    report = source_observations({"records": [quote()]}, source="PPI_API", as_of=AT)
    mutation(report)
    with pytest.raises(ValueError, match=reason):
        audit_sources(reports=[report], as_of=AT)


def test_digest_changes_when_provenance_or_clock_changes():
    source = source_observations({"records": [quote()]}, source="PPI_API", as_of=AT)
    first = audit_sources(reports=[source], as_of=AT)["source_reports_digest"]
    source["observations"][0]["provenance"] = {"capture_digest": "capture-B"}
    second = audit_sources(reports=[source], as_of=AT)["source_reports_digest"]
    source["observations"][0]["source_at"] = "2026-10-05T13:19:59+00:00"
    third = audit_sources(reports=[source], as_of=AT)["source_reports_digest"]
    assert len({first, second, third}) == 3


def test_native_missing_metadata_is_preserved_without_fabricating_freshness_or_units():
    row = {"source": "PPI_MARKETDATA_CURRENT", "source_at": None,
        "received_at": AT, "useful": False, "fields": {"price": 100}}
    report = native_source_reports([row], as_of=AT)[0]
    assert report["observations"] == [row]
    link = audit_sources(reports=[report], as_of=AT)["snapshots"]["0"]
    assert link["path_provenance_missing"] == 1
    assert "units" not in report["observations"][0]


def test_source_audit_capacity_is_bounded_and_never_silently_truncates(monkeypatch):
    import rc6_dynamic_universe.sources as sources
    monkeypatch.setattr(sources, "MAX_SOURCE_AUDIT_OBSERVATIONS", 2)
    with pytest.raises(ValueError, match="CAPACITY"):
        native_source_reports([{"source": "PPI"}] * 3, as_of=AT)
    reports = native_source_reports([{"source": "PPI"}] * 2, as_of=AT)
    with pytest.raises(ValueError, match="CAPACITY"):
        audit_sources(reports=reports * 2, as_of=AT)
