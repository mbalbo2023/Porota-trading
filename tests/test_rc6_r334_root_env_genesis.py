"""R334: fail closed on unreviewed bytes; never grant physical authority."""
import hashlib
import importlib.util
import os
from pathlib import Path

import pytest


# Synthetic credentials, never captured host values.
_DASHBOARD_AUTH = (b'DASHBOARD_ACCESS_TOKEN=synthetic-access-token-32-bytes=a==\n'
    b'DASHBOARD_USER=synthetic-user\n'
    b'DASHBOARD_PASSWORD_HASH=pbkdf2_sha256$240000$synthetic$salt=hash==\n'
    b'ENVIRONMENT=PRODUCTION\nDASHBOARD_SESSION_HOURS=168\n'
    b'DASHBOARD_MAX_LOGIN_ATTEMPTS=5\nDASHBOARD_LOCKOUT_MINUTES=15\n'
    b'DASHBOARD_SESSION_STORE=data/dashboard_sessions.json\n')


def test_realistic_dashboard_auth_fields_are_preserved_without_echo(capsys):
    proposal = build(_DASHBOARD_AUTH)
    for line in _DASHBOARD_AUTH.splitlines():
        assert line + b'\n' in proposal.payload
    assert 'synthetic' not in repr(proposal)
    assert capsys.readouterr() == ('', '')


def test_dashboard_requires_explicit_production_environment_and_observer_does_not():
    raw = _DASHBOARD_AUTH.replace(b'ENVIRONMENT=PRODUCTION\n', b'')
    with pytest.raises(module().GenesisHold):
        build(raw)
    observer = build(b'TELEGRAM_BOT_TOKEN=synthetic-only\n', 'observer')
    assert b'ENVIRONMENT=' not in observer.payload


@pytest.mark.parametrize('line', _DASHBOARD_AUTH.splitlines())
def test_observer_rejects_every_dashboard_auth_field(line):
    with pytest.raises(module().GenesisHold):
        build(line + b'\n', 'observer')


@pytest.mark.parametrize('raw', [b'DASHBOARD_USERNAME=synthetic\n',
    b'ENVIRONMENT=SANDBOX\n', b'ENVIRONMENT=LIVE\n', b'ENVIRONMENT=production\n',
    b'ENVIRONMENT=\n'])
def test_dashboard_rejects_unreviewed_name_or_nonproduction_auth_environment(raw):
    with pytest.raises(module().GenesisHold):
        build(raw)


def module():
    path = Path(__file__).parents[1] / 'scripts/rc6_r334_root_env_genesis.py'
    assert path.exists(), 'R334 pure transform is missing'
    import scripts.rc6_r334_root_env_genesis as genesis
    return genesis


def build(raw=b'ENVIRONMENT=PRODUCTION\nSERVER_TIMEZONE=America/Argentina/Buenos_Aires\n', role='dashboard'):
    return module().transform_legacy_env(role=role, source=raw,
        expected_source_sha256=hashlib.sha256(raw).hexdigest())


def test_injects_exact_safety_and_observer_schedule_without_granting_go():
    result = build(b'TELEGRAM_BOT_TOKEN=opaque=a==\n', 'observer')
    assert result.payload == (b'MARKET_OPEN_HOUR=10\nMARKET_OPEN_MINUTE=30\n'
        b'POROTA_RUNTIME_MODE=PRODUCTION_PAPER\nPPI_ORDERS=BLOCKED\n'
        b'REAL_ORDERS_ALLOWED=false\nTELEGRAM_BOT_TOKEN=opaque=a==\n')
    assert result.sha256 == hashlib.sha256(result.payload).hexdigest()
    assert result.host_go is False and result.deploy_authorized is False
    assert 'opaque' not in repr(result) and result.sha256 not in repr(result)


def test_only_identical_dashboard_timezone_duplicate_is_collapsed():
    raw = b'SERVER_TIMEZONE=America/Argentina/Buenos_Aires\n'
    assert build(b'ENVIRONMENT=PRODUCTION\n' + raw + raw).payload == build(
        b'ENVIRONMENT=PRODUCTION\n' + raw).payload
    with pytest.raises(module().GenesisHold):
        build(raw + raw, 'observer')


def test_drops_only_explicit_image_metadata():
    result = build(b'ENVIRONMENT=PRODUCTION\nPATH=/bin\nPYTHON_VERSION=3.12\nPYTHON_SHA256=abc\nPYTHONUNBUFFERED=1\n')
    assert result.dropped_keys == ('PATH', 'PYTHONUNBUFFERED', 'PYTHON_SHA256', 'PYTHON_VERSION')
    assert b'PATH=' not in result.payload


@pytest.mark.parametrize('raw', [
    b'UNKNOWN=value\n', b'PAPER_ARBITRARY=value\n', b'LD_PRELOAD=x\n',
    b'PYTHONPATH=x\n', b'REAL_TRADING=true\n', b'PPI_LIVE_ORDERS=false\n',
    b'POROTA_RUNTIME_MODE=LIVE\n', b'PPI_ORDERS=ALLOWED\n',
    b'REAL_ORDERS_ALLOWED=true\n', b'MARKET_OPEN_HOUR=11\n',
    b'MARKET_OPEN_MINUTE=0\n', b'SERVER_TIMEZONE=a\nSERVER_TIMEZONE=b\n',
    b'PPI_ORDERS=BLOCKED\nPPI_ORDERS=BLOCKED\n', b'A=1\r\n',
    b'\xff=x\n', b'A=x\x00\n', b'export A=x\n', b'# comment\n',
    b'\n', b'SERVER_TIMEZONE=x\vbad\n', 'SERVER_TIMEZONE=x\u2028bad\n'.encode(),
    b'SERVER_TIMEZONE=' + b'x' * 1025 + b'\n', b'x' * 65537,
])
def test_rejects_unsafe_unknown_conflicting_or_malformed_source(raw):
    with pytest.raises(module().GenesisHold) as error:
        build(raw)
    assert error.value.exit_code == 79
    assert repr(raw) not in str(error.value)


def test_requires_independent_exact_source_pin_and_role():
    for role, pin in [('dashboard', '0' * 64), ('critical', hashlib.sha256(b'A=x\n').hexdigest())]:
        with pytest.raises(module().GenesisHold):
            module().transform_legacy_env(role=role, source=b'A=x\n', expected_source_sha256=pin)


def test_dashboard_does_not_invent_schedule_and_preserves_unicode_equals():
    result = build('ENVIRONMENT=PRODUCTION\nSERVER_TIMEZONE=á=b==\n'.encode())
    assert b'MARKET_OPEN' not in result.payload
    assert 'SERVER_TIMEZONE=á=b==\n'.encode() in result.payload


def test_pair_digests_are_role_bound_and_no_output(capsys):
    raw = b'SERVER_TIMEZONE=UTC\n'
    dashboard_raw = b'ENVIRONMENT=PRODUCTION\n' + raw
    result = module().propose_env_pair(dashboard_source=dashboard_raw, observer_source=raw,
        dashboard_source_sha256=hashlib.sha256(dashboard_raw).hexdigest(),
        observer_source_sha256=hashlib.sha256(raw).hexdigest())
    assert result.dashboard.sha256 != result.observer.sha256
    assert result.host_go is False
    assert capsys.readouterr() == ('', '')


def test_missing_external_r271_exercises_local_contract_without_skip(monkeypatch):
    monkeypatch.delenv('RC6_R271_HELPER_SOURCE', raising=False)
    try:
        evidence = _check_r271_or_local_contract(monkeypatch)
    except pytest.skip.Exception:
        pytest.fail('missing external R271 must test local contract, not skip CI')
    assert evidence == 'LOCAL_CONTRACT_ONLY:R271_NO_VERIFICADO'


def test_r271_pinned_consumer_or_explicit_local_contract(monkeypatch):
    evidence = _check_r271_or_local_contract(monkeypatch)
    assert evidence in ('PINNED_R271_SEMANTICS_VALIDATED',
                        'LOCAL_CONTRACT_ONLY:R271_NO_VERIFICADO')


def _check_r271_or_local_contract(monkeypatch):
    path = os.environ.get('RC6_R271_HELPER_SOURCE')
    if not path:
        # A clean checkout lacks the separate R271 implementation. Exercise
        # the local byte contract instead; this is NOT external R271 evidence.
        dashboard = build(b'ENVIRONMENT=PRODUCTION\n')
        observer = build(b'TELEGRAM_BOT_TOKEN=synthetic-observer=a==\n', 'observer')
        assert dashboard.payload == (b'ENVIRONMENT=PRODUCTION\n'
            b'POROTA_RUNTIME_MODE=PRODUCTION_PAPER\nPPI_ORDERS=BLOCKED\n'
            b'REAL_ORDERS_ALLOWED=false\n')
        assert observer.payload == (b'MARKET_OPEN_HOUR=10\nMARKET_OPEN_MINUTE=30\n'
            b'POROTA_RUNTIME_MODE=PRODUCTION_PAPER\nPPI_ORDERS=BLOCKED\n'
            b'REAL_ORDERS_ALLOWED=false\nTELEGRAM_BOT_TOKEN=synthetic-observer=a==\n')
        assert not dashboard.host_go and not observer.host_go
        assert not dashboard.deploy_authorized and not observer.deploy_authorized
        return 'LOCAL_CONTRACT_ONLY:R271_NO_VERIFICADO'
    assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == (
        'd83550318fd4e38f88d072cc15f19fbef710fd0b752a226ec13c3f16e6c88aa5'
    ), 'R271 consumer source changed: review new pinned source before compatibility claim'
    spec = importlib.util.spec_from_file_location('r334_real_r271', path)
    consumer = importlib.util.module_from_spec(spec)
    import sys
    monkeypatch.setitem(sys.modules, spec.name, consumer)
    spec.loader.exec_module(consumer)
    for role in ('dashboard', 'observer'):
        proposal = build(_DASHBOARD_AUTH if role == 'dashboard' else
                         b'TELEGRAM_BOT_TOKEN=synthetic-observer=a==\n', role=role)
        monkeypatch.setattr(consumer, '_read_root_owned', lambda path, expected_mode: proposal.payload)
        consumer._verify_root_sealed_env('/var/lib/porota-rc6-oci-custody/generations/' +
            'a' * 32 + '/env/' + role + '.env', proposal.sha256)
    return 'PINNED_R271_SEMANTICS_VALIDATED'
