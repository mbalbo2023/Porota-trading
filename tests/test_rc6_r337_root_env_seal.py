"""R337 local internals only: no host authority, no private input fixtures."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys

import pytest


def mod():
    path = Path(__file__).parents[1] / 'scripts/rc6_r337_root_env_seal.py'
    assert path.exists(), 'R337 sealer boundary missing'
    import scripts.rc6_r337_root_env_seal as boundary
    return boundary


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def fixture():
    doc = dict(schema=1, action='SEAL_ENV_PAIR', candidate_sha='a'*40,
        candidate_tree='b'*40, generation='c'*32, nonce='d'*32,
        issued_at='2026-11-13T15:00:00Z', expires_at='2026-11-13T15:05:00Z',
        normalizer_sha256='c7060a643b0a72ee6b0416fd38967c5c741abe340667b8988e79dd3768f12fb8',
        sealer_sha256='e'*64, source_sha256={'dashboard': sha(b'd'), 'observer': sha(b'o')},
        output_sha256={'dashboard': sha(b'dashboard'), 'observer': sha(b'observer')},
        paper_mode='PRODUCTION_PAPER', real_orders_allowed=False)
    raw = json.dumps(doc, sort_keys=True, separators=(',', ':')).encode()
    pins = dict(intent_sha256=sha(raw), candidate_sha='a'*40, candidate_tree='b'*40,
                nonce='d'*32, sealer_sha256='e'*64, now='2026-11-13T15:01:00Z')
    return doc, raw, pins


def intent():
    _, raw, pins = fixture()
    return mod()._validate_intent(raw, **pins)


def test_public_entrypoint_always_holds_without_side_effects(capsys):
    assert mod().main([]) == 79
    assert capsys.readouterr() == ('', '')


@pytest.mark.parametrize('field,value', [('action', 'DEPLOY'), ('nonce', '1'*32),
    ('candidate_sha', '1'*40), ('candidate_tree', '1'*40),
    ('normalizer_sha256', '1'*64), ('sealer_sha256', '1'*64),
    ('paper_mode', 'LIVE'), ('real_orders_allowed', True),
    ('expires_at', '2026-11-13T15:01:00Z'), ('issued_at', '2026-11-13T15:02:00Z'),
    ('expires_at', '2026-11-13T16:00:00Z'), ('extra', 'unreviewed')])
def test_intent_rejects_changed_or_unsafe_independent_authority(field, value):
    doc, _, pins = fixture()
    doc[field] = value
    raw = json.dumps(doc).encode()
    pins['intent_sha256'] = sha(raw)
    with pytest.raises(mod().SealHold):
        mod()._validate_intent(raw, **pins)


def test_intent_rejects_duplicate_fields_and_bad_external_pin():
    _, raw, pins = fixture()
    with pytest.raises(mod().SealHold):
        mod()._validate_intent(raw + b' ', **pins)
    raw = raw[:-1] + b',"schema":1}'
    pins['intent_sha256'] = sha(raw)
    with pytest.raises(mod().SealHold):
        mod()._validate_intent(raw, **pins)


@pytest.fixture
def directory(tmp_path, monkeypatch):
    monkeypatch.setattr(mod(), '_ROOT_UID', os.getuid())
    tmp_path.chmod(0o700)
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        yield tmp_path, fd
    finally:
        os.close(fd)


def test_legacy_read_pins_inode_mode_bytes_and_rejects_symlink(directory):
    path, fd = directory
    leaf = path / 'dashboard_mode_v17.env'
    leaf.write_bytes(b'opaque=a==\n')
    leaf.chmod(0o600)
    assert mod()._read_legacy(fd, 'dashboard', sha(b'opaque=a==\n')) == b'opaque=a==\n'
    leaf.chmod(0o644)
    with pytest.raises(mod().SealHold):
        mod()._read_legacy(fd, 'dashboard', sha(b'opaque=a==\n'))
    leaf.unlink()
    leaf.symlink_to('/etc/passwd')
    with pytest.raises(mod().SealHold):
        mod()._read_legacy(fd, 'dashboard', '0'*64)


def test_legacy_read_detects_inplace_race(directory, monkeypatch):
    path, fd = directory
    leaf = path / 'observer_runtime_v17.env'
    leaf.write_bytes(b'old')
    leaf.chmod(0o600)
    real_read = os.read
    def racing_read(open_fd, size):
        result = real_read(open_fd, size)
        leaf.write_bytes(b'new')
        return result
    monkeypatch.setattr(mod().os, 'read', racing_read)
    with pytest.raises(mod().SealHold):
        mod()._read_legacy(fd, 'observer', sha(b'old'))


def test_absolute_ancestor_walk_rejects_symlink_or_writable_ancestor(directory):
    path, _ = directory
    (path / 'target').mkdir()
    (path / 'alias').symlink_to(path / 'target')
    with pytest.raises(mod().SealHold):
        mod()._open_physical_directory(str(path / 'alias'))
    path.chmod(0o777)
    try:
        with pytest.raises(mod().SealHold):
            mod()._open_physical_directory(str(path / 'target'))
    finally:
        path.chmod(0o700)
    for bad in ('relative/path', '/var/../tmp', '//var/lib', '/var/./lib'):
        with pytest.raises(mod().SealHold):
            mod()._open_physical_directory(bad)


def test_transaction_pair_0400_receipt_last_and_no_clobber(directory):
    path, fd = directory
    result = mod()._stage_pair(fd, intent(), b'dashboard', b'observer')
    generation = path / ('c'*32)
    assert (generation / 'env/dashboard.env').read_bytes() == b'dashboard'
    assert (generation / 'env/observer.env').read_bytes() == b'observer'
    assert stat.S_IMODE((generation / 'env/dashboard.env').stat().st_mode) == 0o400
    assert stat.S_IMODE((generation / 'receipt.json').stat().st_mode) == 0o400
    receipt = json.loads((generation / 'receipt.json').read_bytes())
    assert receipt['host_go'] is False
    assert receipt['r271_semantics'] == 'NO_VERIFICADO'
    assert result.host_go is False and 'dashboard' not in repr(result)
    with pytest.raises(mod().SealHold):
        mod()._stage_pair(fd, intent(), b'dashboard', b'observer')
    assert (generation / 'env/dashboard.env').read_bytes() == b'dashboard'
    assert sorted(p.name for p in path.iterdir()) == ['c'*32]


def test_transaction_cleanup_partial_own_stage_only(directory, monkeypatch):
    path, fd = directory
    (path / 'unrelated').mkdir()
    original = mod()._write_exclusive
    def fail_observer(directory_fd, name, raw):
        if name == 'observer.env':
            raise OSError('synthetic private failure')
        return original(directory_fd, name, raw)
    monkeypatch.setattr(mod(), '_write_exclusive', fail_observer)
    with pytest.raises(mod().SealHold) as caught:
        mod()._stage_pair(fd, intent(), b'dashboard', b'observer')
    assert 'synthetic' not in str(caught.value)
    assert sorted(p.name for p in path.iterdir()) == ['unrelated']


def test_transaction_rejects_digest_and_preexisting_symlink(directory):
    path, fd = directory
    with pytest.raises(mod().SealHold):
        mod()._stage_pair(fd, intent(), b'tampered', b'observer')
    (path / ('c'*32)).symlink_to('/etc')
    with pytest.raises(mod().SealHold):
        mod()._stage_pair(fd, intent(), b'dashboard', b'observer')
    assert (path / ('c'*32)).is_symlink()


def test_nonce_stage_collision_is_not_removed(directory):
    path, fd = directory
    name = '.r337-' + 'c'*32 + '-' + 'd'*32
    (path / name).mkdir()
    (path / name / 'unique').write_bytes(b'evidence')
    with pytest.raises(mod().SealHold):
        mod()._stage_pair(fd, intent(), b'dashboard', b'observer')
    assert (path / name / 'unique').read_bytes() == b'evidence'


def test_dependency_absent_or_changed_never_executes(directory):
    path, fd = directory
    with pytest.raises(mod().SealHold):
        mod()._load_pinned_r334(fd)
    leaf = path / 'rc6_r334_root_env_genesis.py'
    leaf.write_bytes(b'raise RuntimeError("must not execute")')
    leaf.chmod(0o400)
    with pytest.raises(mod().SealHold):
        mod()._load_pinned_r334(fd)
