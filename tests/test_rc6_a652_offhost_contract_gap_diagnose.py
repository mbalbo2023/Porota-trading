"""A652 offline RCA: no provider reads, no DB writes, no PAPER promotion."""

from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from cp_contract_evidence_v2_hf6 import evidence_hash
from scripts import rc6_a652_offhost_contract_gap_diagnose as a652


STAMP = "2026-11-04T19:20:17+00:00"
COUNTS = {
    "BONOS": (8, 4, 0, 4),
    "OBLIGACIONES": (45, 0, 0, 45),
    "OPCIONES": (576, 568, 4, 4),
    "FUTUROS": (166, 3, 163, 0),
    "CAUCIONES": (5, 0, 5, 0),
    "FCI": (21, 0, 0, 21),
}


def aggregate():
    return {
        "schema": a652.AGGREGATE_SCHEMA,
        "run_id": "41895877546",
        "data_collection_status": "COMPLETE",
        "a626_sql": {
            "status": "DIAGNOSTIC_ONLY", "measured_at_utc": STAMP,
            "capability_gaps_measured": 821,
            "gap_terms_diagnostic": {
                "state": "EVIDENCE_ONLY_NO_GO", "gap_total": 821,
                "by_family": {
                    family: dict(zip(("gap_rows", "offline_terms_present_not_admitted",
                                      "offline_terms_missing", "no_exact_current_payload"), counts))
                    for family, counts in COUNTS.items()
                },
            },
        },
    }


def record(family="OPCIONES", ticker="SENSITIVE_TICKER", *,
           evidence=None, source="IOL_STRUCTURED_API", source_ref="private:SENSITIVE_REF"):
    payload = evidence or {"currency": "ARS", "cash_multiplier": "100", "quantity_step": "1",
                           "minimum_quantity": "1", "expires_at": "2026-11-20T15:30:00-03:00",
                           "underlying": "SENSITIVE_UNDERLYING", "strike": "500", "option_right": "CALL"}
    return {
        "family": family, "ticker": ticker, "market": "BYMA", "currency": "ARS",
        "settlement": "INMEDIATA", "source_class": source, "source_ref": source_ref,
        "observed_at": STAMP, "evidence": payload, "evidence_hash": evidence_hash(payload),
    }


def case(row, capability="NEEDS_OPTION_CONTRACT"):
    return {
        "identity": {name: row[name] for name in a652.IDENTITY},
        "catalog_capability": capability, "records": [row],
    }


def sample(cases):
    return {
        "schema": a652.SAMPLE_SCHEMA, "a626_run_id": "41895877546",
        "measured_at_utc": STAMP, "cases": cases,
    }


def test_a626_575_counts_remain_unattributed_without_private_row_evidence():
    result = a652.diagnose(aggregate())
    assert result["a626_all_contract_gaps"] == 821
    assert result["a626_terms_present_not_admitted"] == 575
    assert result["sampled"] == 0
    assert result["unattributed"] == 575
    assert result["by_family"]["OPCIONES"]["unattributed_without_offhost_sample"] == 568
    assert result["by_family"]["FUTUROS"]["unattributed_without_offhost_sample"] == 3
    assert result["paper_instrument_ready"] is False
    assert result["ppi_full_five_key_verified"] is False
    assert result["causal_pit_book_verified"] is False
    assert result["runtime_real_orders_sent"] == "NO_VERIFICADO"
    assert result["mutation_operations"] == 0


def test_private_synthetic_sample_bridge_reasons_are_aggregated_without_identifiers():
    option = record()
    bond = record("BONOS", "PRIVATE_BOND", evidence={
        "currency": "ARS", "cash_multiplier": "0.01", "quantity_step": "1",
        "quantity_min": "1",
    })
    future = record("FUTUROS", "PRIVATE_FUTURE", evidence={
        "currency": "ARS", "cash_multiplier": "1", "quantity_step": "1",
        "minimum_quantity": "1", "expires_at": "2026-12-16T15:30:00-03:00",
        "underlying": "PRIVATE_UNDERLYING",
    }, source="PPI_OFFICIAL_DOCUMENTATION")
    result = a652.diagnose(aggregate(), sample([
        case(option), case(bond, "NEEDS_BOND_CONTRACT"),
        case(future, "NEEDS_FUTURE_CONTRACT"),
    ]))
    assert result["sampled"] == 3
    assert result["unattributed"] == 572
    assert result["sample_status"] == "CALLER_PROVIDED_OFFHOST_NOT_AUTHENTICATED"
    option_reasons = result["by_family"]["OPCIONES"]["sample_bridge_reasons"]
    assert option_reasons["OPTION_IOL_SERIES_CONTRACT_UNVERIFIED"] == 1
    # The bridge also reports missing option fields, because ignored IOL
    # chain terms cannot be promoted to a series-adjustment contract.
    assert option_reasons["MISSING:cash_multiplier"] == 1
    assert result["by_family"]["BONOS"]["sample_bridge_reasons"] == {
        "NORMALIZED_BUT_CATALOG_GAP_UNEXPLAINED": 1,
    }
    assert result["by_family"]["FUTUROS"]["sample_bridge_reasons"] == {
        "MISSING:paper_margin_policy": 1,
    }
    printable = json.dumps(result)
    for secret in ("SENSITIVE_TICKER", "SENSITIVE_REF", "PRIVATE_BOND",
                   "PRIVATE_FUTURE", "PRIVATE_UNDERLYING"):
        assert secret not in printable
    assert result["paper_instrument_ready"] is False


def test_a626_arithmetic_fail_closed_against_undercounted_category():
    original = aggregate()
    original["a626_sql"]["gap_terms_diagnostic"]["by_family"]["OPCIONES"]["gap_rows"] += 1
    with pytest.raises(a652.Rejected, match="A626_GAP_ARITHMETIC_INVALID"):
        a652.diagnose(original)


@pytest.mark.parametrize("mutate,reason", [
    (lambda s: s.__setitem__("a626_run_id", "bad"), "SAMPLE_PROVENANCE_CLAIM_MISMATCH"),
    (lambda s: s["cases"].append(s["cases"][0]), "SAMPLE_IDENTITY_DUPLICATE"),
    (lambda s: s["cases"][0]["records"][0].__setitem__("ticker", "BAD"),
     "SAMPLE_RECORD_SHAPE_OR_KEY_INVALID"),
    (lambda s: s["cases"][0]["records"][0]["evidence"].__setitem__("secret_api_key", "private"),
     "SAMPLE_SENSITIVE_FIELD"),
])
def test_untrusted_sample_fields_fail_closed(mutate, reason):
    value = sample([case(record())])
    mutate(value)
    with pytest.raises(a652.Rejected, match=reason):
        a652.diagnose(aggregate(), value)


def test_oversampled_family_is_rejected_even_if_other_cohorts_have_slots():
    cases = [case(record("BONOS", "BOND" + str(i), evidence={"currency": "ARS"}))
             for i in range(5)]
    with pytest.raises(a652.Rejected, match="SAMPLE_EXCEEDS_A626_COHORT"):
        a652.diagnose(aggregate(), sample(cases))


def test_bounded_cli_rejects_duplicate_keys_and_symlink_and_never_echoes_data(tmp_path, capsys):
    source = tmp_path / "aggregate.json"
    source.write_text(json.dumps(aggregate()), encoding="utf-8")
    assert a652.main(["--aggregate", str(source)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["unattributed"] == 575
    source.write_text('{"schema":"A","schema":"B","secret":"NEVER_ECHO"}', encoding="utf-8")
    assert a652.main(["--aggregate", str(source)]) == 2
    errors = capsys.readouterr()
    assert "DUPLICATE_JSON_KEY" in errors.err
    assert "NEVER_ECHO" not in errors.err
    alias = tmp_path / "alias.json"
    alias.symlink_to(source)
    assert a652.main(["--aggregate", str(alias)]) == 2
    assert "INPUT_FILE_UNTRUSTED" in capsys.readouterr().err


def test_json_nonfinite_and_naive_timestamp_fail_closed(tmp_path):
    source = tmp_path / "aggregate.json"
    source.write_text('{"m":NaN}')
    with pytest.raises(a652.Rejected, match="NONFINITE_JSON"):
        a652._read_private_json(source, a652.MAX_AGGREGATE_BYTES)
    value = aggregate()
    value["a626_sql"]["measured_at_utc"] = "2026-11-04T19:20:17"
    with pytest.raises(a652.Rejected, match="CLOCK_INVALID"):
        a652.diagnose(value)
