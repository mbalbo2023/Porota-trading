import inspect
from pathlib import Path
import rc6_ppi_web_reauth as m


def test_authenticated_url_only_accepts_trading_non_order_pages():
    assert m.authenticated_url('https://trading.portfoliopersonal.com/')
    assert m.authenticated_url('https://trading.portfoliopersonal.com/Cotizaciones/Bonos')
    assert not m.authenticated_url('https://cuenta.portfoliopersonal.com/login')
    assert not m.authenticated_url('https://trading.portfoliopersonal.com/Operar/Bonos')


def test_safe_page_blocks_order_confirm_cancel_and_foreign_hosts():
    assert m.safe_page_url('https://cuenta.portfoliopersonal.com/login')
    assert m.safe_page_url('https://trading.portfoliopersonal.com/Cotizaciones/Acciones')
    assert not m.safe_page_url('https://trading.portfoliopersonal.com/Operar/Acciones')
    assert not m.safe_page_url('https://trading.portfoliopersonal.com/Orden/123')
    assert not m.safe_page_url('https://evil.example/login')


def test_secret_parser_is_local_and_does_not_emit_values(tmp_path):
    p=tmp_path/'ppi.env'; p.write_text('PPI_WEB_USERNAME=user\nPPI_WEB_PASSWORD=pass\n',encoding='utf-8')
    assert m.parse_secret(Path(p)) == ('user','pass')
    out=m.status_payload('BLOCKED_AUTH_2FA_REQUIRED',attempts=1)
    assert 'user' not in out and 'pass' not in out
    assert 'credentials_exposed": false' in out.lower()


def test_source_has_no_db_broker_or_order_execution_capability():
    src=inspect.getsource(m)
    assert 'sqlite3' not in src
    assert 'PaperBroker' not in src
    assert 'send_order' not in src
    assert 'place_order' not in src
    assert 'cancel_order' not in src


def test_2fa_status_is_fail_closed_and_noninteractive():
    payload=m.status_payload('BLOCKED_AUTH_2FA_REQUIRED',attempts=2)
    assert 'BLOCKED_AUTH_2FA_REQUIRED' in payload
    assert 'real_orders_sent": 0' in payload
