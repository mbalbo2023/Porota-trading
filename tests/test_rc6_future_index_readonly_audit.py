"""Adversarial offline proof: no catalogue row is an executable future/index."""

import json
from datetime import datetime, timezone
import subprocess
import sys

import pytest

import rc6_future_index_readonly_audit as audit


AS_OF = "2026-10-30T17:00:00+00:00"  # 14:00 ART, DLR/OCT26 still before 15:00 cutoff.


def row(ticker="DLR/NOV26", *, family="FUTUROS", market="ROFEX",
        currency="ARS", settlement="INMEDIATA", status="AVAILABLE",
        source="PPI_PRIMARY", seen="2026-10-30T16:59:59+00:00",
        capability="NEEDS_FUTURES_MARGIN_AND_CONTRACT", conflicts=None):
    return {
        "ticker": ticker, "instrument_type": family, "market": market,
        "currency": currency, "settlement": settlement,
        "settlement_source": "PPI_FIELD", "status": status,
        "capability": capability, "last_seen_at": seen,
        "metadata_json": json.dumps({"_discovery_source": source,
                                     "_contract_conflicts": conflicts or []}),
    }


def test_exact_dlr_is_static_only_not_ready_and_cannot_infer_broker_margin():
    result = audit.audit_catalog([row()], as_of=AS_OF)
    assert result["future_rows"] == 1
    assert result["reasons"] == {"DLR_STATIC_CONTRACT_ONLY_DYNAMIC_UNVERIFIED": 1}
    assert result["paper_tradable_count"] == 0
    assert result["operational_readiness"] == "NO_VERIFICADO"
    assert result["real_routes_used"] == []
    assert result["examples"][0]["paper_tradable"] is False
    assert result["as_of_art"] == "2026-10-30T14:00:00-03:00"


def test_expired_and_unsupported_2027_or_spread_are_not_promoted():
    rows = [row("DLR/OCT26"), row("DLR/ENE27"),
            row("DLR/NOV26-DLR/DIC26")]
    result = audit.audit_catalog(rows, as_of="2026-10-30T18:00:00+00:00")
    assert result["reasons"]["FUTURE_SERIES_EXPIRED"] == 1
    assert result["reasons"]["FUTURE_SERIES_CONTRACT_NOT_REVIEWED"] == 2
    assert result["paper_tradable_count"] == 0


@pytest.mark.parametrize("change,reason", [
    ({"source": "IOL_COMPLEMENTARY"}, "PPI_PRIMARY_SOURCE_UNPROVED"),
    ({"seen": "2026-10-30T17:00:01+00:00"}, "CATALOG_CLOCK_MISSING_OR_FUTURE"),
    ({"seen": "2026-10-30T17:00:00"}, "CATALOG_CLOCK_MISSING_OR_FUTURE"),
    ({"status": "STALE"}, "CATALOG_NOT_AVAILABLE"),
    ({"market": "BYMA"}, "FUTURE_A3_ARS_EXACT_IDENTITY_REQUIRED"),
    ({"currency": "USD"}, "FUTURE_A3_ARS_EXACT_IDENTITY_REQUIRED"),
    ({"capability": "READY_PAPER"}, "FUTURE_CAPABILITY_NOT_REVIEWED"),
    ({"conflicts": ["margin"]}, "FUTURE_CONTRACT_CONFLICT_UNREVIEWED"),
])
def test_material_unknowns_fail_closed_independently(change, reason):
    result = audit.audit_catalog([row(**change)], as_of=AS_OF)
    assert result["reasons"] == {reason: 1}


def test_missing_exact_provenance_or_malformed_metadata_fail_closed():
    no_settlement = row()
    no_settlement["settlement_source"] = "INFERRED"
    bad_meta = row("DLR/DIC26")
    bad_meta["metadata_json"] = "{broken"
    result = audit.audit_catalog([no_settlement, bad_meta], as_of=AS_OF)
    assert result["reasons"] == {
        "FUTURE_SETTLEMENT_PROVENANCE_UNPROVED": 1,
        "CATALOG_METADATA_INVALID": 1,
    }


def test_duplicate_full_key_is_ambiguity_even_when_the_payloads_match():
    same = row()
    result = audit.audit_catalog([same, dict(same)], as_of=AS_OF)
    assert result["scoped_rows"] == 2
    assert result["distinct_full_identities"] == 1
    assert result["ambiguous_full_identities"] == 1
    assert result["reasons"] == {"DUPLICATE_EXACT_IDENTITY": 2}


def test_incomplete_future_is_counted_in_denominator_but_not_unique_identity():
    missing = row()
    missing["settlement"] = "NO_VERIFICADO"
    result = audit.audit_catalog([missing], as_of=AS_OF)
    assert result["scoped_rows"] == result["future_rows"] == 1
    assert result["distinct_full_identities"] == 0
    assert result["reasons"] == {"FULL_IDENTITY_INCOMPLETE": 1}


def test_indices_are_context_only_never_direct_tradeable_orders():
    mer = row("MERVAL", family="INDICES", market="BYMA",
              capability="REFERENCE_INDEX_ONLY")
    result = audit.audit_catalog([mer], as_of=AS_OF)
    assert result["index_rows"] == 1
    assert result["reasons"] == {"INDEX_CONTEXT_ONLY_LIVE_VALUE_UNPROVED": 1}
    assert result["paper_tradable_count"] == 0
    assert result["real_routes_used"] == []


def test_denominator_excludes_other_families_and_limits_output_examples():
    rows = [row(), row("MERVAL", family="INDICES", market="BYMA"),
            row("GGAL", family="ACCIONES", market="BYMA")]
    result = audit.audit_catalog(rows, as_of=AS_OF, sample_limit=1)
    assert result["supplied_rows"] == 3
    assert result["scoped_rows"] == 2
    assert len(result["examples"]) == 1
    assert len(result["input_sha256"]) == 64


@pytest.mark.parametrize("rows,at,limit", [
    ("invalid", AS_OF, 10), ([], "2026-10-30T17:00:00", 10),
    ([], AS_OF, 21), ([None], AS_OF, 10),
])
def test_bad_input_is_rejected_without_partial_readiness(rows, at, limit):
    with pytest.raises(ValueError):
        audit.audit_catalog(rows, as_of=at, sample_limit=limit)


def test_full_snapshot_digest_is_stable_and_scoped_output_has_no_raw_metadata():
    rows = [row()]
    first = audit.audit_catalog(rows, as_of=datetime(2026, 10, 30, 17, tzinfo=timezone.utc))
    again = audit.audit_catalog(rows, as_of=AS_OF)
    assert first == again
    assert "metadata_json" not in json.dumps(first)
    assert first["paper_only"] is True


def test_cli_reads_only_explicit_snapshot_and_prints_bounded_report(tmp_path):
    source = tmp_path / "reviewed-catalog.json"
    source.write_text(json.dumps({"rows": [row()]}), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-m", "rc6_future_index_readonly_audit",
         "--catalog-json", str(source), "--as-of", AS_OF, "--samples", "0"],
        check=True, capture_output=True, text=True,
    )
    report = json.loads(result.stdout)
    assert report["reasons"] == {"DLR_STATIC_CONTRACT_ONLY_DYNAMIC_UNVERIFIED": 1}
    assert report["examples"] == []
    assert result.stderr == ""
