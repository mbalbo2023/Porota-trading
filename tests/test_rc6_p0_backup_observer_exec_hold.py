"""A1093: legacy Docker exec by observer NAME cannot authorize backup deletion."""

import sys

import pytest

from scripts import rc6_679_backup_suspension as retire


def test_apply_guard_holds_before_untrusted_observer_exec(monkeypatch, tmp_path):
    monkeypatch.setattr(retire, 'ROOT', tmp_path / 'missing-host-root')
    calls = []
    monkeypatch.setattr(retire, 'run', lambda *args: calls.append(args))
    with pytest.raises(retire.BackupObserverCustodyHold) as error:
        retire.guard('a' * 40, apply=True)
    assert str(error.value) == 'BACKUP_OBSERVER_EXEC_NAME_HOLD79'
    assert error.value.exit_code == 79
    assert calls == []


def test_apply_cli_returns_79_before_ever_reading_plan_or_deleting(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(retire, 'ROOT', tmp_path)
    monkeypatch.setattr(sys, 'argv', [
        'retire', '--candidate-sha', 'b' * 40,
        '--apply-plan', str(tmp_path / 'inexistent-plan.json')])
    calls = []
    monkeypatch.setattr(retire, 'inventory', lambda: calls.append('inventory'))
    assert retire.main() == 79
    assert capsys.readouterr().out.strip() == 'BACKUP_OBSERVER_EXEC_NAME_HOLD79'
    assert calls == []
    assert list(tmp_path.iterdir()) == []
