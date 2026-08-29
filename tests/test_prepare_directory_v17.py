import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import v17_prepare_directory as prep


@pytest.fixture
def host(tmp_path, monkeypatch):
    import os
    root = tmp_path/'porota-trading'
    data = root/'data'
    data.mkdir(parents=True)
    monkeypatch.setattr(prep, 'ROOT', root)
    monkeypatch.setattr(prep, 'EXPECTED_ID', os.geteuid())
    rows = [dict(name='/'+name, running=False, restart='no',
                 image='sha256:'+'a'*64, user='botuser') for name in prep.ENGINES]
    monkeypatch.setattr(prep.subprocess, 'run', lambda *a, **k:SimpleNamespace(
        returncode=0, stdout='\n'.join(map(json.dumps,rows))))
    return root


def test_prepares_exact_empty_directory_and_removes_probe(host):
    report = prep.prepare()
    target = host/'data/paper_v17'
    assert report['status'] == 'PREPARED'
    assert report['directory_created'] is True
    assert report['write_probe_created'] is True and report['write_probe_removed'] is True
    assert list(target.iterdir()) == []
    assert report['directory']['mode'] == '0750'
    assert report['database_created'] is False and report['legacy_database_touched'] is False


def test_second_run_is_idempotent(host):
    assert prep.prepare()['status'] == 'PREPARED'
    second = prep.prepare()
    assert second['status'] == 'ALREADY_PREPARED'
    assert second['directory_created'] is False


def test_existing_database_stops_without_opening_or_changing_it(host):
    target = host/'data/paper_v17'
    target.mkdir(mode=0o750)
    db = target/'observer_v17.db'
    db.write_bytes(b'DO NOT TOUCH')
    report = prep.prepare()
    assert report['reason'] == 'NEW_DATABASE_ALREADY_EXISTS'
    assert db.read_bytes() == b'DO NOT TOUCH'
    assert report['database_opened'] is False


def test_nonempty_target_stops(host):
    target = host/'data/paper_v17'
    target.mkdir(mode=0o750)
    (target/'unexpected').write_text('fixture')
    assert prep.prepare()['reason'] == 'TARGET_NOT_EMPTY'


def test_symlink_target_stops(host):
    (host/'data/paper_v17').symlink_to(host/'elsewhere')
    assert prep.prepare()['reason'] == 'TARGET_IS_SYMLINK'


def test_wrong_caller_stops_before_creation(host, monkeypatch):
    monkeypatch.setattr(prep.os, 'geteuid', lambda:prep.EXPECTED_ID + 1)
    assert prep.prepare()['reason'] == 'CALLER_MUST_BE_UID_GID_1000'
    assert not (host/'data/paper_v17').exists()


def test_active_engine_stops_before_creation(host, monkeypatch):
    rows = [dict(name='/'+name, running=(index == 0), restart='no',
                 image='sha256:'+'a'*64, user='botuser')
            for index,name in enumerate(prep.ENGINES)]
    monkeypatch.setattr(prep.subprocess, 'run', lambda *a, **k:SimpleNamespace(
        returncode=0, stdout='\n'.join(map(json.dumps,rows))))
    assert prep.prepare()['reason'] == 'ENGINES_MUST_REMAIN_STOPPED_NO_RESTART'
    assert not (host/'data/paper_v17').exists()


def test_required_flag_prevents_host_operations(host, monkeypatch):
    calls = []
    monkeypatch.setattr(prep, 'prepare', lambda:calls.append(True))
    with pytest.raises(SystemExit):
        prep.main([])
    assert calls == []
