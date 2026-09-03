from __future__ import annotations

import ck_policy_gate_hf6 as gate


def _clear(monkeypatch):
    for key in (gate.EXPECTANCY_ENV, gate.REGIME_ENV, gate.SECTOR_ENV,
                gate.SECTOR_LIMIT_ENV):
        monkeypatch.delenv(key, raising=False)


def test_defaults_are_non_binding(monkeypatch):
    _clear(monkeypatch)
    assert gate.admission_block(
        expectancy_samples=[{"currency":"ARS","sample_state":"OBSERVATIONAL","empirical_expectancy":"-1"}],
        breadth={"state":"BEARISH_BREADTH"},
        sectors={"groups":[{"sector":"FINANCIERO","open_positions":9}],"unmapped_positions":0},
        candidate_sector="FINANCIERO",sector_mapping_verified=True) == ""


def test_expectancy_binding_uses_only_mature_sample(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv(gate.EXPECTANCY_ENV,"BINDING")
    thin=[{"currency":"ARS","sample_state":"INSUFFICIENT_SAMPLE","empirical_expectancy":"-1200"}]
    mature=[{"currency":"ARS","sample_state":"OBSERVATIONAL","empirical_expectancy":"-1200"}]
    assert gate.expectancy_block(thin) == ""
    assert gate.expectancy_block(mature) == "EXPECTANCY_NEGATIVE_ARS"


def test_regime_binding_does_not_treat_missing_sample_as_bearish(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv(gate.REGIME_ENV,"BINDING")
    assert gate.regime_block({"state":"INSUFFICIENT_SAMPLE"}) == ""
    assert gate.regime_block({"state":"BEARISH_BREADTH"}) == "BEARISH_BREADTH_NO_LONG_ENTRIES"
    assert gate.regime_block(None) == "REGIME_EVIDENCE_REQUIRED"


def test_sector_binding_requires_verified_mapping(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv(gate.SECTOR_ENV,"BINDING")
    observation={"groups":[],"unmapped_positions":0}
    assert gate.sector_block(observation,None,mapping_verified=False) == "SECTOR_MAPPING_REQUIRED"
    assert gate.sector_block(observation,"FINANCIERO",mapping_verified=False) == "SECTOR_MAPPING_REQUIRED"


def test_sector_binding_rejects_incomplete_open_book_mapping(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv(gate.SECTOR_ENV,"BINDING")
    observation={"groups":[{"sector":"FINANCIERO","open_positions":1}],"unmapped_positions":1}
    assert gate.sector_block(observation,"ENERGIA",mapping_verified=True) == "SECTOR_BOOK_MAPPING_INCOMPLETE"


def test_sector_binding_blocks_same_sector_but_allows_verified_diversifier(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv(gate.SECTOR_ENV,"BINDING")
    observation={"groups":[{"sector":"FINANCIERO","open_positions":2}],"unmapped_positions":0}
    assert gate.sector_block(observation,"FINANCIERO",limit=2,mapping_verified=True) == "SECTOR_CONCENTRATION_LIMIT_FINANCIERO"
    assert gate.sector_block(observation,"ENERGIA",limit=2,mapping_verified=True) == ""
