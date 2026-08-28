import base64
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import zipfile

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts import v17_preinstall_readonly as probe
from scripts import build_v17_preinstall as builder


def states(**changes):
    return [dict(name='/'+name, running=False, restart='no',
                 image='sha256:'+'a'*64, user='botuser', **changes) for name in probe.ENGINES]


@pytest.fixture
def host(tmp_path, monkeypatch):
    root = tmp_path/'project'
    (root/'data').mkdir(parents=True)
    monkeypatch.setattr(probe, 'ROOT', root)
    calls = []
    def inspect(command, **kwargs):
        calls.append(command)
        assert command[:6] == ['sudo','-n','docker','inspect','--type','container']
        assert command[-2:] == list(probe.ENGINES)
        assert kwargs['timeout'] == 20
        return SimpleNamespace(returncode=0, stdout='\n'.join(map(json.dumps,states())))
    monkeypatch.setattr(probe.subprocess, 'run', inspect)
    return root, calls


def test_only_reads_metadata_does_not_create_new_paths_or_open_files(host, monkeypatch):
    root, calls = host
    old = root/'data/observer'
    old.mkdir()
    db = old/'observer_production.db'
    db.write_bytes(b'PRIVATE_FIXTURE')
    before = db.read_bytes()
    def forbidden(*args, **kwargs):
        pytest.fail('No abrir archivos ni bases')
    monkeypatch.setattr(Path, 'open', forbidden)
    report = probe.collect()
    assert report['status'] == 'COMPLETED_HOST_METADATA'
    assert report['paths']['new_directory']['state'] == 'MISSING'
    assert not (root/'data/paper_v17').exists()
    assert len(calls) == 1
    assert report['caller']['effective_uid'] >= 0
    assert report['data_filesystem']['available_bytes'] >= 0
    assert all(report[k] is False for k in ('installation_performed','database_opened',
        'files_created','permissions_changed','engines_started','promotion_allowed'))
    assert 'PRIVATE_FIXTURE' not in json.dumps(report)
    # Path.open está bloqueado; el diagnóstico sólo hizo lstat/statvfs/access.
    with open(db, 'rb') as stream:
        assert stream.read() == before


@pytest.mark.parametrize('target', ['project','data','new_directory','new_database'])
def test_symlink_is_reported_without_following_it(host, target):
    root, _ = host
    paths = {'project':root,'data':root/'data','new_directory':root/'data/paper_v17',
             'new_database':root/'data/paper_v17/observer_v17.db'}
    path = paths[target]
    if path.exists():
        path.rename(path.with_name(path.name+'_saved'))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to('/fixture-missing-target')
    report = probe.collect()
    assert report['reason'] == 'UNEXPECTED_PATH_KIND'
    assert report['paths'][target]['kind'] == 'SYMLINK'


@pytest.mark.parametrize('target', ['project','data'])
def test_missing_required_parent_stops(host, target):
    root, _ = host
    path = root if target == 'project' else root/'data'
    path.rename(path.with_name(path.name+'_saved'))
    assert probe.collect()['reason'] == 'REQUIRED_DIRECTORY_MISSING_OR_INVALID'


def test_existing_new_database_not_opened_replaced_or_adopted(host):
    root, _ = host
    directory = root/'data/paper_v17'
    directory.mkdir()
    database = directory/'observer_v17.db'
    database.write_bytes(b'EXISTING FILE DO NOT TOUCH')
    report = probe.collect()
    assert report['status'] == 'OBSERVED_EXISTING_V17_PATH_REVIEW'
    assert database.read_bytes() == b'EXISTING FILE DO NOT TOUCH'


def test_file_occupying_new_directory_stops(host):
    root, _ = host
    (root/'data/paper_v17').write_text('fixture')
    assert probe.collect()['reason'] == 'NEW_DIRECTORY_PATH_OCCUPIED'


def test_directory_occupying_new_database_stops(host):
    root, _ = host
    (root/'data/paper_v17/observer_v17.db').mkdir(parents=True)
    assert probe.collect()['reason'] == 'NEW_DATABASE_PATH_OCCUPIED'


def test_metadata_permission_error_is_not_retried_or_exposed(host, monkeypatch):
    calls = []
    def denied(path):
        calls.append(path)
        raise PermissionError(13, 'PRIVATE_MARKER')
    monkeypatch.setattr(probe, 'metadata', denied)
    report = probe.collect()
    assert report['stage'] == 'FIXED_PATH_METADATA' and report['os_errno'] == 13
    assert report['status'] == 'STOPPED' and len(calls) == 1
    assert 'PRIVATE_MARKER' not in json.dumps(report)


def test_caller_access_false_is_not_a_successful_botuser_write_test(host, monkeypatch):
    monkeypatch.setattr(probe.os, 'access', lambda *a, **k:False)
    report = probe.collect()
    assert report['status'] == 'COMPLETED_HOST_METADATA'
    assert report['caller']['data_write_search_access'] is False
    assert report['promotion_allowed'] is False


@pytest.mark.parametrize('change', [{'running':True}, {'restart':'unless-stopped'},
                                   {'user':'root'}, {'image':'unverified'}, {'name':'/other'}])
def test_unexpected_or_active_engines_stop_without_action(host, monkeypatch, change):
    rows = states()
    rows[0].update(change)
    monkeypatch.setattr(probe.subprocess, 'run', lambda *a, **k:SimpleNamespace(
        returncode=0, stdout='\n'.join(map(json.dumps,rows))))
    report = probe.collect()
    assert report['status'] == 'STOPPED' and report['paths'] == {}
    assert report['engines_started'] is False


@pytest.mark.parametrize('payload', ['', 'PRIVATE_MARKER', '{}', '[]', '{}\n{}'])
def test_bad_docker_output_does_not_leak_content_or_retry(host, monkeypatch, payload):
    calls = []
    def fail(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout=payload)
    monkeypatch.setattr(probe.subprocess, 'run', fail)
    report = probe.collect()
    assert report['status'] == 'STOPPED' and len(calls) == 1
    assert 'PRIVATE_MARKER' not in json.dumps(report)


def test_docker_denial_no_fallback(host, monkeypatch):
    calls = []
    def fail(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=1, stdout='PRIVATE_MARKER')
    monkeypatch.setattr(probe.subprocess, 'run', fail)
    report = probe.collect()
    assert report['reason'] == 'DOCKER_INSPECT_FAILED' and len(calls) == 1
    assert 'PRIVATE_MARKER' not in json.dumps(report)


@pytest.mark.parametrize('failure', [PermissionError(13,'PRIVATE_MARKER'),
                                    subprocess.TimeoutExpired('PRIVATE_MARKER',20)])
def test_os_error_or_timeout_is_bounded_and_sanitized(host, monkeypatch, failure):
    def fail(*args, **kwargs):
        raise failure
    monkeypatch.setattr(probe.subprocess, 'run', fail)
    report = probe.collect()
    assert report['status'] == 'STOPPED'
    assert 'PRIVATE_MARKER' not in json.dumps(report)


def test_stdout_and_clipboard_contain_same_report(host, capsys):
    assert probe.main(['--host']) == 0
    output = capsys.readouterr().out
    visible, encoded = output.split('\033]52;c;')
    assert json.loads(visible) == json.loads(base64.b64decode(encoded.removesuffix('\a')))


def test_no_flag_means_no_host_operations(host):
    with pytest.raises(SystemExit):
        probe.main([])
    assert host[1] == []


def test_archive_is_reproducible_and_contains_only_standalone_reader(tmp_path):
    first, second = tmp_path/'first.zip', tmp_path/'second.zip'
    builder.build(first)
    builder.build(second)
    assert first.read_bytes() == second.read_bytes()
    with zipfile.ZipFile(first) as archive:
        assert set(archive.namelist()) == {'__main__.py','MANIFEST.json'}
        assert archive.read('__main__.py') == builder.SOURCE.read_bytes()
    with pytest.raises(FileExistsError):
        builder.build(first)
    result = subprocess.run([sys.executable,'-I','-S',str(first),'--help'],
                            capture_output=True,text=True,check=True)
    assert '--host' in result.stdout
