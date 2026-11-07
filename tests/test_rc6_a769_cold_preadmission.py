"""A769 COLD2 pre-merge observation: no host writes and no synthetic GO."""

from contextlib import contextmanager
import base64
import gzip
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import rc6_paper_deploy_preflight as a205


BASE = Path(__file__).resolve().parents[1]
SHA = 'a' * 40
TREE = 'b' * 40


def build_bundle(*, sources=None, files=None):
    if sources is None:
        sources = {name: (BASE / name).read_text(encoding='utf-8')
                   for name in a205.COLD_SOURCES}
    if files is None:
        files = {name: {'sha256': hashlib.sha256(source.encode()).hexdigest(),
                        'source': source} for name, source in sources.items()}
    payload = {'schema': a205.COLD_SOURCE_BUNDLE_CONTRACT, 'files': files}
    packed = gzip.compress(json.dumps(payload, sort_keys=True,
                                      separators=(',', ':')).encode(), mtime=0)
    return base64.b64encode(packed).decode(), hashlib.sha256(packed).hexdigest()


def inject_bundle(monkeypatch, *, encoded=None, digest=None):
    actual, actual_sha = build_bundle()
    monkeypatch.setenv('RC6_A205_COLD_SOURCES_GZIP_B64',
                       encoded if encoded is not None else actual)
    monkeypatch.setenv('RC6_A205_COLD_SOURCES_SHA256',
                       digest if digest is not None else actual_sha)


def test_candidate_four_source_bytes_verified_exact_and_modules_restored(monkeypatch):
    inject_bundle(monkeypatch)
    sources, sha = a205._read_candidate_cold_sources()
    assert len(sources) == 4 and len(sha) == 64
    from scripts import rc6_cold2_quarantine as host_old
    with a205._candidate_cold_modules(sources) as (modules, complete):
        assert modules['rc6_cold2_quarantine'] is not host_old
        assert modules['rc6_cold2_successor'].old is modules['rc6_cold2_quarantine']
        assert complete.__code__.co_filename == (
            'candidate/scripts/rc6_cold2_apply_guard.py')
    from scripts import rc6_cold2_quarantine as restored
    assert restored is host_old


def test_candidate_module_rejects_import_time_mutation(monkeypatch, tmp_path):
    sources = {name: (BASE / name).read_text() for name in a205.COLD_SOURCES}
    sentinel = tmp_path / 'not-to-be-deleted'
    sentinel.write_text('durable')
    sources[a205.COLD_SOURCES[0]] += (
        '\nimport os\nos.unlink(' + repr(str(sentinel)) + ')\n')
    with pytest.raises(a205.UnsafeBook,
                       match='COLD_CANDIDATE_TOPLEVEL_NOT_DECLARATIVE'):
        with a205._candidate_cold_modules(sources):
            pass
    assert sentinel.read_text() == 'durable'


def test_candidate_complete_inspector_rejects_mutator(monkeypatch):
    sources = {name: (BASE / name).read_text() for name in a205.COLD_SOURCES}
    source = a205.COLD_SOURCES[3]
    original = sources[source]
    # This changes the exact function selected by AST, not the module entry.
    marker = "    return {'state':'COMPLETE_UNTOUCHED'"
    assert marker in original
    sources[source] = original.replace(marker,
        "    os.unlink('/tmp/forbidden-cold-preadmission')\n" + marker, 1)
    with pytest.raises(a205.UnsafeBook,
                       match='COLD_CANDIDATE_COMPLETE_INSPECTOR_NOT_READONLY'):
        with a205._candidate_cold_modules(sources):
            pass


@pytest.mark.parametrize('change', [
    'bundle_digest', 'payload_extra', 'file_changed', 'file_missing',
    'path_extra', 'syntactic_damage', 'unbounded_b64', 'wrong_zip',
])
def test_candidate_source_bundle_fails_closed(monkeypatch, change):
    encoded, digest = build_bundle()
    sources = {name: (BASE / name).read_text() for name in a205.COLD_SOURCES}
    files = {name: {'source': source,
                    'sha256': hashlib.sha256(source.encode()).hexdigest()}
             for name, source in sources.items()}
    if change == 'bundle_digest':
        digest = '0' * 64
    elif change == 'payload_extra':
        bundle = {'schema': a205.COLD_SOURCE_BUNDLE_CONTRACT,
                  'files': files, 'unreviewed': True}
        packed = gzip.compress(json.dumps(bundle).encode(), mtime=0)
        encoded, digest = (base64.b64encode(packed).decode(),
                           hashlib.sha256(packed).hexdigest())
    elif change == 'file_changed':
        files[a205.COLD_SOURCES[0]]['source'] += '\n# silently changed'
        encoded, digest = build_bundle(files=files)
    elif change == 'file_missing':
        files.pop(a205.COLD_SOURCES[0])
        encoded, digest = build_bundle(files=files)
    elif change == 'path_extra':
        files['scripts/rc6_unapproved.py'] = {'source': 'pass\n',
                                               'sha256': hashlib.sha256(b'pass\n').hexdigest()}
        encoded, digest = build_bundle(files=files)
    elif change == 'syntactic_damage':
        value = 'def invalid(\n'
        files[a205.COLD_SOURCES[0]] = {
            'source': value, 'sha256': hashlib.sha256(value.encode()).hexdigest()}
        encoded, digest = build_bundle(files=files)
    elif change == 'unbounded_b64':
        encoded = 'A' * 120000
    elif change == 'wrong_zip':
        blob = b'not-a-gzip'
        encoded, digest = base64.b64encode(blob).decode(), hashlib.sha256(blob).hexdigest()
    inject_bundle(monkeypatch, encoded=encoded, digest=digest)
    with pytest.raises((a205.UnsafeBook, ValueError, SyntaxError, OSError)):
        a205._read_candidate_cold_sources()


def fake_inspectors(*, mode='NONE', queue_state='FAILED', fail=None,
                    archive_true=True):
    """All dependencies read exact evidence; mutation APIs abort the test."""
    def no_write(*args, **kwargs):
        raise AssertionError('candidate COLD preadmission attempted a write')

    def old_archive(root):
        return {'old_cut_revoked': True, 'pack_sha256': '1' * 64,
                'manifest_sha256': '2' * 64}

    def legacy_check(*args, **kwargs):
        if fail:
            raise ValueError(fail)
        assert kwargs == {'archived_recovery': True, 'quiescence': None}
        return {'pack_sha256': '3' * 64, 'manifest_sha256': '4' * 64}

    old = SimpleNamespace(IDENT='historical', verify_original_archive=old_archive,
                          envelope=lambda *args, **kwargs: {'state': queue_state,
                              'snapshot_id': 'newer'}, quarantine=no_write)
    successor = SimpleNamespace(inspect=lambda *args, **kwargs: {
        'queue_failed': True, 'pack_sha256': '5' * 64,
        'manifest_sha256': '6' * 64}, quarantine=no_write)
    failed = SimpleNamespace(IDENT='original',
         original_location=lambda source, archive: archive if archive_true else source,
         checked=legacy_check,
         prior_archive_receipt=lambda *args, **kwargs: '7' * 64,
         run=no_write)

    @contextmanager
    def modules(_):
        yield ({'rc6_cold2_quarantine': old,
                'rc6_cold2_successor': successor,
                'rc6_failed_cold_fix_forward': failed},
               lambda root: {'state': 'COMPLETE_UNTOUCHED', 'old_cut_revoked': True,
                             'manifest_sha256': '8' * 64})
    return modules


def cold_fixture(monkeypatch, tmp_path, **kwargs):
    root = tmp_path / 'porota-test'
    source = (root / 'data/paper_v17/artifacts/observer_v17.db/'
              'dynamic-shadow/cold-audit')
    source.mkdir(parents=True)
    (root / 'data/deploy/rc6-failed-cold-original').mkdir(parents=True)
    monkeypatch.setattr(a205, '_read_candidate_cold_sources',
                        lambda: ({name: 'pinned source' for name in a205.COLD_SOURCES},
                                 '9' * 64))
    monkeypatch.setattr(a205, '_candidate_cold_modules',
                        fake_inspectors(**kwargs))
    return root, source


def test_premerge_green_attests_archives_and_missing_queue_not_idle(monkeypatch, tmp_path):
    root, source = cold_fixture(monkeypatch, tmp_path)
    before = list(root.rglob('*'))
    value = a205.inspect_cold(root, candidate_sha=SHA, candidate_tree=TREE)
    assert value['status'] == 'GREEN'
    assert value['queue'] == 'ABSENT_WITH_COMPLETE_PAIRS_VERIFIED'
    assert value['original_archive'] == value['failed_archive'] == 'PROVEN'
    assert value['candidate_sha'] == SHA and value['candidate_tree'] == TREE
    assert value['deploy_approved'] is False and value['host_mutations'] == 0
    assert list(root.rglob('*')) == before


@pytest.mark.parametrize('cause', [
    'COLD_ARCHIVED_RECOVERY_NOT_PROVEN',
    'COLD_PACK_HASH_CHANGED',
    'COLD_PRIOR_ARCHIVE_RECEIPT_CHANGED',
])
def test_r2_archived_recovery_or_hash_drift_never_looks_green(
        monkeypatch, tmp_path, cause):
    root, _ = cold_fixture(monkeypatch, tmp_path, fail=cause)
    value = a205.inspect_cold(root, candidate_sha=SHA, candidate_tree=TREE)
    assert value['status'] == 'BLOCKED_COLD'
    assert value['deploy_approved'] is False
    if cause == 'COLD_ARCHIVED_RECOVERY_NOT_PROVEN':
        assert value['reason'] == cause
    else:
        assert value['reason'] == 'COLD_PREADMISSION_EVIDENCE_UNVERIFIED'


def test_failed_archive_must_exist_and_be_selected(monkeypatch, tmp_path):
    root, _ = cold_fixture(monkeypatch, tmp_path, archive_true=False)
    result = a205.inspect_cold(root, candidate_sha=SHA, candidate_tree=TREE)
    assert result['status'] == 'BLOCKED_COLD'
    assert result['failed_archive'] == 'NO_VERIFICADO'


@pytest.mark.parametrize('state,output', [
    ('FAILED', 'FAILED_SUCCESSOR_PROVEN'),
    ('COMPLETE', 'COMPLETE_PROVEN'),
    ('OTHER', None),
])
def test_successor_queue_requires_exact_canonical_verifier(
        monkeypatch, tmp_path, state, output):
    root, source = cold_fixture(monkeypatch, tmp_path, queue_state=state)
    (source / 'queue.json').write_text('synthetic-test-only')
    result = a205.inspect_cold(root, candidate_sha=SHA, candidate_tree=TREE)
    if output is None:
        assert result['status'] == 'BLOCKED_COLD'
    else:
        assert result['status'] == 'GREEN' and result['queue'] == output


def test_symlink_queue_and_bad_sha_tree_fail_closed(monkeypatch, tmp_path):
    root, source = cold_fixture(monkeypatch, tmp_path)
    (source / 'queue.json').symlink_to(source / 'elsewhere')
    result = a205.inspect_cold(root, candidate_sha=SHA, candidate_tree=TREE)
    assert result['status'] == 'BLOCKED_COLD'
    with pytest.raises(a205.UnsafeBook, match='COLD_CANDIDATE_SHA_TREE_UNVERIFIED'):
        a205.inspect_cold(root, candidate_sha='invalid', candidate_tree=TREE)


def test_cli_book_green_never_masks_cold_failure(monkeypatch, capsys):
    monkeypatch.setattr(a205, '_load_signed_candidate_ledger', lambda: None)
    monkeypatch.setattr(a205, 'inspect_book', lambda *args, **kw: {
        'contract': 'RC6_A205_PAPER_DEPLOY_PREFLIGHT_V1',
        'status': 'FLAT_GREEN', 'reasons': [], 'barrier_status': 'ABSENT',
        'read_only': True, 'real_orders_sent': 0, 'host_mutations': 0})
    monkeypatch.setattr(a205, 'inspect_cold', lambda **kw: {
        'status': 'BLOCKED_COLD', 'reason': 'COLD_ARCHIVED_RECOVERY_NOT_PROVEN'})
    rc = a205.main(['--cold-precheck', '--candidate-sha', SHA,
                    '--candidate-tree', TREE])
    value = json.loads(capsys.readouterr().out)
    assert rc == 2 and value['status'] == 'BLOCKED_COLD'
    assert value['barrier_status'] == 'ABSENT'
    assert 'COLD_ARCHIVED_RECOVERY_NOT_PROVEN' in value['reasons']


def test_existing_workflow_reused_with_exact_source_and_no_host_mutation():
    yml = (BASE / '.github/workflows/rc6-a205-paper-book-readonly.yml').read_text()
    assert "rc6-a205-paper-book-ro-v1-*" in yml
    assert "git rev-parse HEAD^{tree}" in yml
    assert 'RC6_A205_COLD_SOURCES_GZIP_B64' in yml
    assert '--cold-precheck --candidate-sha' in yml
    assert 'python3 -B -' in yml and "RC6_A205_COLD_PREADMISSION_CONTRACT_RED" in yml
    assert 'NOT_DEPLOY_GO' in yml
    assert 'docker image rm' not in yml and 'docker prune' not in yml
    assert 'ssh ' in yml and 'sudo -n' in yml
