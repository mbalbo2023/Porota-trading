import os

import ck_policy_gate_hf6 as gate
import porota_mode_manager as modes


def _without(name):
    old = os.environ.pop(name, None)
    return old


def _restore(name, old):
    if old is not None:
        os.environ[name] = old
    else:
        os.environ.pop(name, None)


def test_sector_policy_defaults_to_binding_in_gate_and_mode_manager():
    old = _without(gate.SECTOR_ENV)
    try:
        assert gate.active_policies()["sector_concentration"] == "BINDING"
        assert modes.PRODUCTION_PAPER["PAPER_SECTOR_CONCENTRATION_POLICY"] == "BINDING"
        assert modes.SANDBOX["PAPER_SECTOR_CONCENTRATION_POLICY"] == "BINDING"
    finally:
        _restore(gate.SECTOR_ENV, old)


def test_binding_blocks_new_entry_at_sector_limit():
    old = _without(gate.SECTOR_ENV)
    try:
        result = gate.evaluate(
            sectors={"groups": [{"sector": "BANKS", "open_positions": 2}]},
            candidate_sector="BANKS",
            sector_limit=2,
        )
        sector = result["gates"]["sector"]
        assert sector["authority"] == "BINDING"
        assert sector["would_block"] is True
        assert sector["execute_block"] is True
        assert result["execute_block"] is True
        assert result["verdict"] == "SECTOR_CONCENTRATION_LIMIT_BANKS"
    finally:
        _restore(gate.SECTOR_ENV, old)


def test_binding_is_fail_closed_when_candidate_sector_evidence_is_missing():
    old = _without(gate.SECTOR_ENV)
    try:
        result = gate.evaluate(sectors=None, candidate_sector=None, sector_limit=2)
        sector = result["gates"]["sector"]
        assert sector["authority"] == "BINDING"
        assert sector["state"] == "INSUFFICIENT_EVIDENCE"
        assert sector["evidence_block"] is True
        assert sector["execute_block"] is True
        assert result["execute_block"] is True
        assert result["verdict"] == "SECTOR_UNMAPPED_BINDING"
    finally:
        _restore(gate.SECTOR_ENV, old)


def test_binding_allows_capacity_below_limit():
    old = _without(gate.SECTOR_ENV)
    try:
        result = gate.evaluate(
            sectors={"groups": [{"sector": "ENERGY", "open_positions": 1}]},
            candidate_sector="ENERGY",
            sector_limit=2,
        )
        sector = result["gates"]["sector"]
        assert sector["authority"] == "BINDING"
        assert sector["execute_block"] is False
        assert result["execute_block"] is False
    finally:
        _restore(gate.SECTOR_ENV, old)
