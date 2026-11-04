"""A652 isolated OFFHOST replay: actual source functions, synthetic evidence.

This file does not import the running observer, connect to SQLite or PPI, or
declare runtime readiness.  The scheduler function is compiled from the actual
repository AST so the import-only test cannot instantiate broker resources.
"""

import ast
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import bu_instrument_catalog as catalog
from cp_contract_evidence_v2_hf6 import evidence_hash
from rc6_contract_bridge import normalize_group
from rc6_contract_reconciliation_guard import reconciliation_allowed


NOW = "2026-11-04T19:20:17+00:00"
KEY = ("APLC100O", "OPCIONES", "BYMA", "ARS", "INMEDIATA")


def synthetic_primary(*, settlement_emitted=False):
    raw = {"_discovery_source": "PPI_PRIMARY", "ticker": KEY[0],
           "type": KEY[1], "market": KEY[2], "currency": KEY[3]}
    if settlement_emitted:
        # Hypothetical provider-full-key positive; NOT a real SearchInstrument shape.
        raw["settlement"] = KEY[4]
    return {
        "ticker": KEY[0], "instrument_type": KEY[1], "market": KEY[2],
        "currency": KEY[3], "settlement": KEY[4], "settlement_source": "PPI_FIELD",
        "status": "AVAILABLE", "capability": "NEEDS_OPTION_CONTRACT",
        "last_seen_at": NOW, "raw": raw,
    }


def synthetic_v2_option():
    evidence = {"currency": KEY[3], "cash_multiplier": "10", "quantity_step": "1",
                "minimum_quantity": "1", "expires_at": "2026-12-18T15:30:00-03:00",
                "underlying": "AAPL", "strike": "100", "option_right": "CALL"}
    return {
        "ticker": KEY[0], "family": KEY[1], "market": KEY[2], "currency": KEY[3],
        "settlement": KEY[4], "source_class": "PPI_OFFICIAL_DOCUMENTATION",
        "source_ref": "synthetic:contract-terms-not-provider-current",
        "observed_at": NOW, "evidence_hash": evidence_hash(evidence),
        "evidence": evidence,
    }


def test_synthetic_non_iol_v2_options_can_be_normalized_then_projected():
    primary = synthetic_primary(settlement_emitted=True)
    bridge = normalize_group([synthetic_v2_option()],
                             now=datetime.fromisoformat(NOW))
    assert bridge["contract_bridge"]["status"] == "NORMALIZED"
    assert bridge["contract_bridge"]["gaps"] == []
    assert bridge["financial_contract_v17"]["metadata_source"] == "CONTRACT_EVIDENCE_V2_BOUND"
    assert reconciliation_allowed(primary, bridge, checked_at=NOW)
    projected = catalog.complete_with_complement(primary, bridge)
    assert projected["raw"]["financial_contract_v17"]["cash_multiplier"] == "1E+1"
    assert projected["capability"] == "READY_PAPER_OPTION_LONG"
    assert catalog.ppi_full_key_evidence_reason(projected) == ""


def test_actual_searchinstrument_shaped_primary_cannot_be_paper_admitted():
    primary = synthetic_primary(settlement_emitted=False)
    bridge = normalize_group([synthetic_v2_option()], now=datetime.fromisoformat(NOW))
    assert bridge["contract_bridge"]["status"] == "NORMALIZED"
    projected = catalog.complete_with_complement(primary, bridge)
    # A contract may be syntactically READY while the separate identity gate
    # rejects PAPER. The response did not attest settlement.
    assert projected["capability"] == "READY_PAPER_OPTION_LONG"
    assert catalog.ppi_full_key_evidence_reason(projected) == "PPI_FULL_KEY_NOT_PROVIDER_ATTESTED"
    assert catalog.quote_terms(projected, now=datetime.fromisoformat(NOW))["opening_block_reason"].startswith(
        "PPI_FULL_KEY_NOT_PROVIDER_ATTESTED")


def _actual_scheduler():
    """Compile just the real pure phase decision from the checked-out source."""
    source = Path(__file__).resolve().parents[1] / "bf_production_paper_observer.py"
    parsed = ast.parse(source.read_text(encoding="utf-8"))
    candidates = [node for node in parsed.body
                  if isinstance(node, ast.FunctionDef)
                  and node.name == "_periodic_complementary_reconcile"]
    assert len(candidates) == 1
    module = ast.fix_missing_locations(ast.Module(body=candidates, type_ignores=[]))
    code = compile(module, str(source), "exec")
    log = []
    store = SimpleNamespace(event=lambda *args: log.append(("event", args)))
    globals_ = {"time": SimpleNamespace(time=lambda: 1500.0),
                "COMPLEMENTARY_RECONCILE_SECONDS": 60,
                "OFFHOURS_RECONCILE_SECONDS": 60,
                "_reconcile_complementary_catalog": lambda x: log.append(("writer", x)),
                "_release_idle_catalog_heap": lambda: {"synthetic": True},
                "json": __import__("json")}
    exec(code, globals_)
    return globals_[candidates[0].name], store, log


def test_open_and_equity_t1_phase_skip_full_catalog_writer():
    scheduler, store, log = _actual_scheduler()
    for phase in ("OPEN", "EQUITY_T1_READONLY"):
        assert scheduler(store, phase, 0.0) == 0.0
        assert not log, "Never take the global catalog writer while live market work runs"


def test_closed_phase_can_reconcile_only_after_scheduler_interval():
    scheduler, store, log = _actual_scheduler()
    assert scheduler(store, "CLOSED", 1490.0) == 1490.0
    assert log == []
    assert scheduler(store, "CLOSED", 0.0) == 1500.0
    assert log[0] == ("writer", store)
    assert log[1][0] == "event"
    assert log[1][1][1].startswith("{")
