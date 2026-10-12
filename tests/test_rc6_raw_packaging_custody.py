"""Cheap byte-backed custody and import-closure counterexamples; no Docker."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import zlib

import pytest

from scripts import porota_artifact_provenance as provenance
from scripts.porota_build_deploy_bundle_v2 import build_bundle
from scripts.porota_validate_deploy_artifact import is_runtime_relevant, validate


HELPERS = ('__init__.py', 'history_probe.py', 'package.py', 'recompute.py', 'sqlite_scratch.py', 'sqlite_snapshot.py')
RAW = ('notes/rc6-evidence/native-red/checkpoint.json', 'rc6_audit_evidence/HISTORY_CONVERGENCE.md',
       'rc6_audit_evidence/history_convergence/native-red.json',
       'rc6_audit_evidence/history_convergence/probes/original_probe.py')
IGNORE = 'docs/\n' + '\n'.join(provenance.RAW_EVIDENCE_ROOTS) + '\n'


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args], stderr=subprocess.PIPE).decode().strip()


def write(repo, rel, content):
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    path.chmod(0o644)


def commit(repo):
    git(repo, 'add', '.')
    git(repo, '-c', 'user.name=Custody Regression', '-c', 'user.email=custody@example.invalid', 'commit', '-qm', 'synthetic')
    return git(repo, 'rev-parse', 'HEAD')


@pytest.fixture
def candidate(tmp_path, monkeypatch):
    repo = tmp_path / 'repo'
    repo.mkdir()
    git(repo, 'init', '-q')
    write(repo, '.dockerignore', 'docs/\n')
    write(repo, 'app.py', 'from rc6_audit_evidence import recompute\n')
    for name in HELPERS:
        write(repo, 'rc6_audit_evidence/' + name, 'VALUE = 1\n')
    for name in RAW:
        write(repo, name, 'VALUE = 1\n' if name.endswith('.py') else 'original RED evidence\n')
    original = commit(repo)
    original_tree = git(repo, 'rev-parse', 'HEAD^{tree}')
    # Only the synthetic origin IDs differ. All actual Git, ancestry, tree,
    # blob, SHA256, representation and import-closure predicates execute.
    monkeypatch.setattr(provenance, 'RAW_CUSTODY_SOURCE_SHA', original)
    monkeypatch.setattr(provenance, 'RAW_CUSTODY_TREE_SHA', original_tree)
    write(repo, '.dockerignore', IGNORE)
    head = commit(repo)
    return repo, original, original_tree, head


def manifest(candidate):
    return provenance.create_source_manifest(candidate[0])


def image_fixture(repo, source, folder):
    folder.mkdir()
    for row in source['files']:
        if row['image_required']:
            target = folder / row['path']
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(repo / row['path'], target)
    metadata = folder / provenance.SOURCE_MANIFEST_NAME
    metadata.write_bytes(provenance.canonical_bytes(source))
    metadata.chmod(0o644)


def test_custody_rehashes_original_payloads_and_keeps_fullsource_and_all_six_helpers(candidate, tmp_path):
    repo, original, tree, _head = candidate
    source = manifest(candidate)
    rows = {row['path']: row for row in source['files']}
    assert set(rows) == set(git(repo, 'ls-files').splitlines())
    custody = source['raw_evidence_custody']
    assert custody['origin_sha'] == original and custody['origin_tree'] == tree
    assert custody['file_count'] == len(RAW) and custody['fullSource_original_preserved'] is True
    for row in custody['files']:
        raw = subprocess.check_output(['git', '--no-replace-objects', '-C', str(repo), 'cat-file', 'blob',
                                       original + ':' + row['path']])
        assert row['bytes'] == len(raw) and row['sha256'] == hashlib.sha256(raw).hexdigest()
        assert rows[row['path']]['bundle_required'] is rows[row['path']]['image_required'] is False
        assert rows[row['path']]['runtime_relevant'] is False
    for helper in HELPERS:
        row = rows['rc6_audit_evidence/' + helper]
        assert row['bundle_required'] is row['image_required'] is row['runtime_relevant'] is True
    source_path = tmp_path / 'source.json'
    source_path.write_bytes(provenance.canonical_bytes(source))
    bundle = tmp_path / 'bundle.tgz'
    bundle_meta = tmp_path / 'bundle.json'
    build_bundle(repo, bundle, bundle_meta, source_path)
    assert provenance.validate_bundle(repo, bundle, bundle_meta, source_path)['status'] == 'GREEN'
    with tarfile.open(bundle) as archive:
        names = set(archive.getnames())
        assert not names.intersection(RAW)
        assert all('rc6_audit_evidence/' + helper in names for helper in HELPERS)
        embedded = json.loads(archive.extractfile(provenance.SOURCE_MANIFEST_NAME).read())
        assert set(RAW).issubset(row['path'] for row in embedded['files'])
    image = tmp_path / 'image'
    image_fixture(repo, source, image)
    assert provenance.validate_image_files(image, source, source_path.read_bytes())['status'] == 'GREEN'
    expected = [row['path'] for row in source['files'] if row['runtime_relevant']]
    assert validate(repo, image, expected)['status'] == 'GREEN'
    assert all((repo / path).is_file() for path in RAW)


@pytest.mark.parametrize('fault', ['mutated', 'removed_one', 'removed_all', 'new_raw', 'broad_ignore'])
def test_no_uncustodied_source_or_helper_exclusion_can_be_packaged(candidate, fault):
    repo = candidate[0]
    if fault == 'mutated':
        write(repo, RAW[0], 'changed unarchived bytes\n')
    elif fault == 'removed_one':
        (repo / RAW[0]).unlink()
    elif fault == 'removed_all':
        for name in RAW:
            (repo / name).unlink()
    elif fault == 'new_raw':
        write(repo, 'notes/rc6-evidence/unarchived/checkpoint.json', 'new unarchived bytes\n')
    else:
        write(repo, '.dockerignore', IGNORE + 'rc6_audit_evidence/\n')
    commit(repo)
    with pytest.raises(provenance.ProvenanceError, match='CUSTODY_|RUNTIME_SOURCE_EXCLUDED'):
        manifest(candidate)


def test_missing_original_git_payload_cannot_be_replaced_by_a_hash_index(candidate, tmp_path):
    repo, original, _tree, _head = candidate
    shallow = tmp_path / 'shallow'
    subprocess.check_output(['git', 'clone', '--depth=1', '--quiet', repo.as_uri(), str(shallow)], stderr=subprocess.PIPE)
    for path in git(shallow, 'ls-files').splitlines():
        (shallow / path).chmod(0o644)
    with pytest.raises(provenance.ProvenanceError, match='GIT_SOURCE_AUTHORITY_UNAVAILABLE'):
        provenance.create_source_manifest(shallow)
    assert not git(shallow, 'rev-list', 'HEAD').splitlines() == [original]


def test_git_replace_cannot_rebind_the_original_custody(candidate):
    repo, original, _tree, head = candidate
    git(repo, 'replace', original, head)
    assert manifest(candidate)['raw_evidence_custody']['origin_sha'] == original


def test_hash_shaped_original_blob_name_is_not_a_verified_payload(candidate):
    repo, original, _tree, _head = candidate
    oid = git(repo, 'rev-parse', original + ':' + RAW[0])
    target = repo / '.git/objects' / oid[:2] / oid[2:]
    old = zlib.decompress(target.read_bytes())
    forged = old.replace(b'original RED evidence', b'forged   RED evidence')
    assert len(old) == len(forged) and forged != old
    target.chmod(0o600)
    target.write_bytes(zlib.compress(forged))
    with pytest.raises(provenance.ProvenanceError, match='RAW_EVIDENCE_CUSTODY_ORIGINAL_BLOB_CHANGED'):
        manifest(candidate)


@pytest.mark.parametrize('source', [
    'import rc6_audit_evidence.history_convergence.probes.original_probe\n',
    'from rc6_audit_evidence.history_convergence.probes import original_probe\n',
    'import importlib\nimportlib.import_module("rc6_audit_evidence.history_convergence.probes.original_probe")\n',
])
def test_runtime_import_into_excluded_raw_invalidates_import_closure(candidate, tmp_path, source):
    repo = candidate[0]
    write(repo, 'app.py', source)
    commit(repo)
    source_manifest = manifest(candidate)
    image = tmp_path / 'image'
    image_fixture(repo, source_manifest, image)
    result = validate(repo, image, [row['path'] for row in source_manifest['files'] if row['runtime_relevant']])
    assert result['status'] == 'FAILED'
    assert any(row['expected_path'] == RAW[-1] for row in result['missing_local_imports'])


def test_relative_helper_import_cannot_hide_a_raw_dependency(candidate, tmp_path):
    repo = candidate[0]
    write(repo, 'rc6_audit_evidence/recompute.py', 'from .history_convergence.probes import original_probe\n')
    commit(repo)
    source = manifest(candidate)
    image = tmp_path / 'image'
    image_fixture(repo, source, image)
    result = validate(repo, image, [row['path'] for row in source['files'] if row['runtime_relevant']])
    assert result['status'] == 'FAILED'
    assert any(row['expected_path'] == RAW[-1] for row in result['missing_local_imports'])


def test_raw_role_selection_preserves_the_rest_of_the_package():
    assert not is_runtime_relevant('rc6_audit_evidence/history_convergence/probes/original_probe.py')
    assert all(is_runtime_relevant('rc6_audit_evidence/' + name) for name in HELPERS)
    assert provenance.is_bundle_path('rc6_audit_evidence/next_native_helper.py')
    assert provenance.dockerignore_exclusion('rc6_audit_evidence/recompute.py', IGNORE) is None
