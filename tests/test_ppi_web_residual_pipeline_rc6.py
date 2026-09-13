import importlib.util
import sys
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "ops" / "ppi_web_residual_pipeline_rc6.py"
spec = importlib.util.spec_from_file_location("ppi_web_residual_pipeline_rc6", MODULE_PATH)
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


def raw(**overrides):
    base = {
        "symbol": "AL30",
        "instrument_type": "BONOS",
        "market": "BYMA",
        "settlement": "A-48HS",
        "residual_class": "PARTIAL_VALID",
        "source_url": "https://trading.portfoliopersonal.com/mercados/bonos",
        "fields": {
            "currency": "ARS",
            "maturity_date": "2030-07-09",
        },
    }
    base.update(overrides)
    return base


def test_gate_fails_while_api_writer_active():
    with pytest.raises(RuntimeError, match="writer is still active"):
        mod.execution_gate("COMPLETED", mod.READONLY_CONFIRM, True)


def test_gate_fails_before_api_completion():
    with pytest.raises(RuntimeError, match="not terminal"):
        mod.execution_gate("RUNNING", mod.READONLY_CONFIRM, False)


def test_gate_requires_explicit_read_only_confirmation():
    with pytest.raises(RuntimeError, match="read-only confirmation"):
        mod.execution_gate("COMPLETED", "", False)


def test_gate_passes_only_after_closeout_and_readonly_confirmation():
    mod.execution_gate("COMPLETED", mod.READONLY_CONFIRM, False)


def test_normalize_rejects_non_https_source():
    with pytest.raises(ValueError, match="must be https"):
        mod.normalize_record(raw(source_url="http://example.invalid"))


def test_contract_validation_for_bond_is_complete():
    record = mod.normalize_record(raw())
    assert mod.validate_contract(record) == []


def test_contract_validation_detects_missing_option_fields():
    row = raw(
        symbol="GGALC9000OC",
        instrument_type="OPCIONES",
        fields={"underlying": "GGAL", "option_type": "CALL", "strike": 9000},
    )
    missing = mod.validate_contract(mod.normalize_record(row))
    assert "expiry_date" in missing


def test_paginate_is_lossless_and_deterministic():
    items = [{"n": i} for i in range(5)]
    pages = list(mod.paginate(items, 2))
    assert [len(p) for p in pages] == [2, 2, 1]
    assert [x["n"] for p in pages for x in p] == list(range(5))


def test_reconcile_never_overwrites_api_covered_identity():
    record = mod.normalize_record(raw())
    accepted, skipped = mod.reconcile([record], {record.identity_key})
    assert accepted == []
    assert skipped == [record]


def test_reconcile_rejects_duplicate_web_identity():
    record = mod.normalize_record(raw())
    with pytest.raises(ValueError, match="duplicate web identity"):
        mod.reconcile([record, record], set())


def test_process_capture_reports_contract_gaps_without_synthesizing():
    option = raw(
        symbol="GGALC9000OC",
        instrument_type="OPCIONES",
        fields={"underlying": "GGAL", "option_type": "CALL", "strike": 9000},
    )
    result = mod.process_capture([option], set())
    assert result["mode"] == "PPI_WEB_READ_ONLY"
    assert result["accepted_rows"] == 1
    assert result["incomplete_contracts"][0]["missing"] == ["expiry_date"]


def test_source_contains_no_order_execution_or_mutating_http_calls():
    source = MODULE_PATH.read_text(encoding="utf-8").lower()
    forbidden = [
        "requests.post(", "requests.put(", "requests.delete(", "requests.patch(",
        "place_order", "send_order", "buy_order", "sell_order", "cancel_order",
    ]
    assert not [token for token in forbidden if token in source]
