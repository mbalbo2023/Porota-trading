"""A1093 P0: named contract writer cannot grant exact-CID stop custody."""

import sys

import pytest

from scripts import rc6_contract_runner_lifecycle as contract


def test_writer_stop_requires_independent_birth_attestation_before_docker(monkeypatch):
    calls = []
    monkeypatch.setattr(contract, 'docker', lambda *a, **kw: calls.append(a))
    with pytest.raises(contract.ContractWriterCustodyHold) as error:
        contract.stop('a' * 40)
    assert error.value.exit_code == 79
    assert str(error.value) == 'CONTRACT_WRITER_EXACT_BIRTH_HOLD79'
    assert calls == []


def test_writer_main_emits_typed_hold79_not_generic_exit1(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(contract, 'docker', lambda *a, **kw: calls.append(a))
    monkeypatch.setattr(sys, 'argv', ['lifecycle.py', '--candidate-sha', 'a' * 40])
    assert contract.main() == 79
    assert capsys.readouterr().out.strip() == 'CONTRACT_WRITER_EXACT_BIRTH_HOLD79'
    assert calls == []
