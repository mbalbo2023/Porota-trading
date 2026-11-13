"""R337 local internals only: no host authority, no private input fixtures."""
import hashlib
import fcntl
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
def test_intent_rejects_changed_or_unsafe_consistency_fields(field, value):
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


def test_transaction_refuses_lock_contention_without_creating_stage(directory):
    path, fd = directory
    lock = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(mod().SealHold):
            mod()._stage_pair(fd, intent(), b'dashboard', b'observer')
        assert list(path.iterdir()) == []
    finally:
        os.close(lock)


def test_racing_generation_winner_is_preserved(directory, monkeypatch):
    path, fd = directory
    real_publish = mod()._rename_no_replace
    def racing_publish(parent, source, target):
        winner = path / target
        winner.mkdir()
        (winner / 'unique').write_bytes(b'winner')
        return real_publish(parent, source, target)
    monkeypatch.setattr(mod(), '_rename_no_replace', racing_publish)
    with pytest.raises(mod().SealHold):
        mod()._stage_pair(fd, intent(), b'dashboard', b'observer')
    assert (path / ('c'*32) / 'unique').read_bytes() == b'winner'
    assert sorted(p.name for p in path.iterdir()) == ['c'*32]


def test_receipt_failure_is_last_and_only_incomplete_stage_is_cleaned(directory, monkeypatch):
    path, fd = directory
    real_write = mod()._write_exclusive
    def fail_receipt(stage_fd, name, raw):
        if name == 'receipt.json':
            stage = next(path.iterdir())
            assert (stage / 'env/dashboard.env').read_bytes() == b'dashboard'
            assert (stage / 'env/observer.env').read_bytes() == b'observer'
            raise OSError('private receipt failure')
        return real_write(stage_fd, name, raw)
    monkeypatch.setattr(mod(), '_write_exclusive', fail_receipt)
    with pytest.raises(mod().SealHold):
        mod()._stage_pair(fd, intent(), b'dashboard', b'observer')
    assert list(path.iterdir()) == []


def test_postpublication_fsync_failure_never_rolls_back_generation(directory, monkeypatch):
    path, fd = directory
    real_sync = os.fsync
    def fail_parent_sync(target_fd):
        if target_fd == fd:
            raise OSError('private postpublication failure')
        return real_sync(target_fd)
    monkeypatch.setattr(mod().os, 'fsync', fail_parent_sync)
    with pytest.raises(mod().SealHold):
        mod()._stage_pair(fd, intent(), b'dashboard', b'observer')
    assert (path / ('c'*32) / 'receipt.json').is_file()
    assert list(p.name for p in path.iterdir()) == ['c'*32]


def test_pinned_dependency_and_optional_r271_semantics(directory, monkeypatch):
    path, fd = directory
    normalizer_source = os.environ.get('RC6_R334_NORMALIZER_SOURCE')
    if not normalizer_source:
        # Clean CI checkout: do not skip or pretend the combined build exists.
        assert mod().main([]) == 79
        with pytest.raises(mod().SealHold):
            mod()._load_pinned_r334(fd)
        return
    normalizer_raw = Path(normalizer_source).read_bytes()
    assert sha(normalizer_raw) == 'c7060a643b0a72ee6b0416fd38967c5c741abe340667b8988e79dd3768f12fb8'
    (path / 'rc6_r334_root_env_genesis.py').write_bytes(normalizer_raw)
    (path / 'rc6_r334_root_env_genesis.py').chmod(0o400)
    dashboard_source = b'ENVIRONMENT=PRODUCTION\nDASHBOARD_USER=synthetic\n'
    observer_source = b'TELEGRAM_BOT_TOKEN=synthetic=a==\n'
    expected_dashboard = (b'DASHBOARD_USER=synthetic\nENVIRONMENT=PRODUCTION\n'
        b'POROTA_RUNTIME_MODE=PRODUCTION_PAPER\nPPI_ORDERS=BLOCKED\nREAL_ORDERS_ALLOWED=false\n')
    expected_observer = (b'MARKET_OPEN_HOUR=10\nMARKET_OPEN_MINUTE=30\n'
        b'POROTA_RUNTIME_MODE=PRODUCTION_PAPER\nPPI_ORDERS=BLOCKED\nREAL_ORDERS_ALLOWED=false\n'
        b'TELEGRAM_BOT_TOKEN=synthetic=a==\n')
    for name, raw in [('dashboard_mode_v17.env', dashboard_source),
                      ('observer_runtime_v17.env', observer_source)]:
        (path / name).write_bytes(raw)
        (path / name).chmod(0o600)
    doc, _, pins = fixture()
    doc['source_sha256'] = dict(dashboard=sha(dashboard_source), observer=sha(observer_source))
    doc['output_sha256'] = dict(dashboard=sha(expected_dashboard), observer=sha(expected_observer))
    raw = json.dumps(doc).encode()
    pins['intent_sha256'] = sha(raw)
    approved = mod()._validate_intent(raw, **pins)
    pair = mod()._propose_fixed_sources(fd, fd, approved)
    assert pair.dashboard.payload == expected_dashboard
    assert pair.observer.payload == expected_observer
    mod()._stage_pair(fd, approved, pair.dashboard.payload, pair.observer.payload)
    consumer_source = os.environ.get('RC6_R271_HELPER_SOURCE')
    if not consumer_source:
        assert mod()._StageResult().r271_semantics == 'NO_VERIFICADO'
        return
    assert sha(Path(consumer_source).read_bytes()) == 'd83550318fd4e38f88d072cc15f19fbef710fd0b752a226ec13c3f16e6c88aa5'
    spec = importlib.util.spec_from_file_location('r337_fixture_r271', consumer_source)
    consumer = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, consumer)
    spec.loader.exec_module(consumer)
    for role in ('dashboard', 'observer'):
        sealed = (path / ('c'*32) / 'env' / (role + '.env')).read_bytes()
        monkeypatch.setattr(consumer, '_read_root_owned', lambda path, expected_mode: sealed)
        consumer._verify_root_sealed_env(mod().GENERATIONS_DIRECTORY + '/' + 'c'*32 +
            '/env/' + role + '.env', sha(sealed))
