from pathlib import Path

RUNTIME=Path('scripts/porota_contract_evidence_rc6_runtime.sh').read_text(encoding='utf-8')
UNIT=Path('systemd/porota-contract-evidence-rc6.service').read_text(encoding='utf-8')


def test_reauth_only_follows_expired_session_and_collector_is_retried_separately():
    assert 'AUTH_STATE" == "BLOCKED_AUTH_SESSION_EXPIRED"' in RUNTIME
    assert 'rc6_ppi_web_reauth.py' in RUNTIME
    assert 'rc6_trusted_browser_contract_collector.py' in RUNTIME
    assert RUNTIME.index('rc6_ppi_web_reauth.py') < RUNTIME.rindex('collect_once')
    assert 'AUTHENTICATED_TRUSTED_DEVICE' in RUNTIME


def test_secret_contract_is_local_protected_and_not_embedded():
    assert 'PPI_WEB_SECRET_FILE=' in RUNTIME
    assert 'PPI_WEB_SECRET_OWNER_MISMATCH' in RUNTIME
    assert 'PPI_WEB_SECRET_MODE_MISMATCH' in RUNTIME
    assert "== \"600\"" in RUNTIME
    assert 'PPI_WEB_PASSWORD=' not in RUNTIME
    assert 'PPI_WEB_USERNAME=' not in RUNTIME
    assert 'EnvironmentFile=-/etc/porota/contract-evidence-rc6.conf' in UNIT


def test_2fa_and_missing_secret_remain_fail_closed():
    assert 'BLOCKED_AUTH_LOCAL_SECRET_MISSING' in RUNTIME
    assert 'AMARILLO_AUTH_BLOCKED' in RUNTIME
    assert 'REAL_ORDERS_SENT=0' in RUNTIME
    assert 'write_auth_state' in RUNTIME


def test_runtime_never_contains_order_actions_or_network_order_tests():
    lower=RUNTIME.lower()
    for forbidden in ('send_order','place_order','cancel_order','network_order_test'):
        assert forbidden not in lower
