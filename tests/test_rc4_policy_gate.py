from __future__ import annotations

import ck_policy_gate_hf6 as gate


def _clear(monkeypatch):
    for key in (gate.EXPECTANCY_ENV, gate.REGIME_ENV, gate.SECTOR_ENV,
                gate.SECTOR_LIMIT_ENV):
        monkeypatch.delenv(key, raising=False)


def _by_policy(result):
    return {row["policy"]: row for row in result["assessments"]}


def test_shadow_evaluates_without_authority(monkeypatch):
    _clear(monkeypatch)
    result = gate.evaluate_policies(
        expectancy_samples=[{"currency":"ARS","sample_state":"OBSERVATIONAL","empirical_expectancy":"-1"}],
        breadth={"state":"BEARISH_BREADTH"},
        sectors={"groups":[{"sector":"FINANCIERO","open_positions":2}],"unmapped_positions":0},
        candidate_sector="FINANCIERO",sector_limit=2,sector_mapping_verified=True)
    by = _by_policy(result)
    assert by["EXPECTANCY"]["would_block"] is True
    assert by["REGIME"]["would_block"] is True
    assert by["SECTOR"]["would_block"] is True
    assert all(row["execute_block"] is False for row in by.values())
    assert result["execute_block_any"] is False
    assert gate.admission_block(
        expectancy_samples=[{"currency":"ARS","sample_state":"OBSERVATIONAL","empirical_expectancy":"-1"}],
        breadth={"state":"BEARISH_BREADTH"},
        sectors={"groups":[],"unmapped_positions":0},
        candidate_sector="ENERGIA",sector_mapping_verified=True) == ""


def test_expectancy_binding_blocks_only_mature_negative_sample(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv(gate.EXPECTANCY_ENV,"BINDING")
    thin=[{"currency":"ARS","sample_state":"INSUFFICIENT_SAMPLE","empirical_expectancy":"-1200"}]
    mature=[{"currency":"ARS","sample_state":"OBSERVATIONAL","empirical_expectancy":"-1200"}]
    thin_result = gate.evaluate_policies(expectancy_samples=thin)
    mature_result = gate.evaluate_policies(expectancy_samples=mature)
    thin_row = _by_policy(thin_result)["EXPECTANCY"]
    assert thin_row["evaluable"] is False
    assert thin_row["binding_eligible"] is False
    assert thin_row["execute_block"] is False
    assert mature_result["execute_block_reason"] == "EXPECTANCY_NEGATIVE_ARS"


def test_regime_binding_is_fail_closed_when_evidence_is_not_evaluable(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv(gate.REGIME_ENV,"BINDING")
    short = gate.evaluate_policies(breadth={"state":"INSUFFICIENT_SAMPLE"})
    bearish = gate.evaluate_policies(breadth={"state":"BEARISH_BREADTH"})
    missing = gate.evaluate_policies(breadth=None)
    assert short["execute_block_reason"] == "INSUFFICIENT_SAMPLE"
    assert _by_policy(short)["REGIME"]["would_block"] is False
    assert bearish["execute_block_reason"] == "BEARISH_BREADTH_NO_LONG_ENTRIES"
    assert missing["execute_block_reason"] == "EVIDENCE_REQUIRED"


def test_sector_binding_requires_verified_mapping(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv(gate.SECTOR_ENV,"BINDING")
    observation={"groups":[],"unmapped_positions":0}
    no_map=gate.evaluate_policies(
        sectors=observation,candidate_sector=None,sector_mapping_verified=False)
    assert no_map["execute_block_reason"] == "SECTOR_MAPPING_REQUIRED"
    assert _by_policy(no_map)["SECTOR"]["would_block"] is False


def test_sector_binding_rejects_incomplete_open_book_mapping(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv(gate.SECTOR_ENV,"BINDING")
    result=gate.evaluate_policies(
        sectors={"groups":[{"sector":"FINANCIERO","open_positions":1}],"unmapped_positions":1},
        candidate_sector="ENERGIA",sector_mapping_verified=True)
    assert result["execute_block_reason"] == "SECTOR_BOOK_MAPPING_INCOMPLETE"


def test_sector_shadow_counterfactual_and_binding_authority(monkeypatch):
    _clear(monkeypatch)
    observation={"groups":[{"sector":"FINANCIERO","open_positions":2}],"unmapped_positions":0}
    shadow=gate.evaluate_policies(
        sectors=observation,candidate_sector="FINANCIERO",sector_limit=2,
        sector_mapping_verified=True)
    sector=_by_policy(shadow)["SECTOR"]
    assert sector["would_block"] is True
    assert sector["execute_block"] is False
    monkeypatch.setenv(gate.SECTOR_ENV,"BINDING")
    binding=gate.evaluate_policies(
        sectors=observation,candidate_sector="FINANCIERO",sector_limit=2,
        sector_mapping_verified=True)
    assert binding["execute_block_reason"] == "SECTOR_CONCENTRATION_LIMIT_FINANCIERO"


def test_positive_verified_evidence_is_binding_eligible(monkeypatch):
    _clear(monkeypatch)
    result=gate.evaluate_policies(
        expectancy_samples=[{"currency":"ARS","sample_state":"OBSERVATIONAL","empirical_expectancy":"1"}],
        breadth={"state":"MIXED_OR_POSITIVE"},
        sectors={"groups":[],"unmapped_positions":0},candidate_sector="ENERGIA",
        sector_mapping_verified=True)
    assert result["all_binding_eligible"] is True
    assert result["would_block_any"] is False
