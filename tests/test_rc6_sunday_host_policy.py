from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import bu_instrument_catalog as catalog

ROOT = Path(__file__).resolve().parents[1]
TZ = ZoneInfo('America/Argentina/Buenos_Aires')


def test_us_labor_day_blocks_only_declared_apple_cedear_focus():
    at = datetime(2026, 9, 7, 10, 15, tzinfo=TZ)
    for symbol in ('AAPL', 'AAPLD', 'AAPLC'):
        assert catalog.rc6_underlying_opening_block(symbol, 'CEDEARS', at) == 'UNDERLYING_MARKET_CLOSED: US_LABOR_DAY'
    assert catalog.rc6_underlying_opening_block('GGAL', 'ACCIONES', at) == ''
    assert catalog.rc6_underlying_opening_block('AAPL', 'CEDEARS', datetime(2026, 9, 8, 10, 15, tzinfo=TZ)) == ''


def test_catalog_quote_terms_wires_opening_block_helper():
    source = (ROOT / 'bu_instrument_catalog.py').read_text(encoding='utf-8')
    assert 'holiday_reason = rc6_underlying_opening_block' in source
    assert 'opening_block_reason' in source


def test_rc6_monotonic_timers_do_not_persist():
    for name in ('porota-functional-health-rc6.timer',
                 'porota-history-postclose-rc6.timer',
                 'porota-candle-integrity-rc6.timer'):
        text = (ROOT / 'systemd' / name).read_text(encoding='utf-8')
        assert 'OnUnitActiveSec=' in text
        assert 'Persistent=' not in text
        assert 'rc4' not in text.lower()


def test_rc6_preopen_and_backup_calendar_timers_are_explicit():
    pre = (ROOT / 'systemd' / 'porota-preopen-rc6.timer').read_text(encoding='utf-8')
    backup = (ROOT / 'systemd' / 'porota-host-general-backup-rc6.timer').read_text(encoding='utf-8')
    assert '10:15:00 America/Argentina/Buenos_Aires' in pre
    assert 'Persistent=' not in pre
    assert '04:15:00 America/Argentina/Buenos_Aires' in backup
    assert 'Persistent=true' in backup


def test_rc6_new_host_services_have_no_rc4_runtime_reference():
    names = (
        'porota-functional-health-rc6.service',
        'porota-history-postclose-rc6.service',
        'porota-host-general-backup-rc6.service',
        'porota-preopen-rc6.service',
        'porota-candle-integrity-rc6.service',
    )
    for name in names:
        text = (ROOT / 'systemd' / name).read_text(encoding='utf-8').lower()
        assert 'rc4' not in text


def test_rc6_host_scripts_keep_order_capability_blocked():
    names = ('rc6_preopen.py', 'rc6_candle_integrity.py', 'rc6_functional_health.py',
             'rc6_postclose_history_job.py', 'rc6_host_general_backup_job.py')
    forbidden = ('send_order(', 'new_order(', 'replace_order(', 'cancel_order(')
    for name in names:
        text = (ROOT / name).read_text(encoding='utf-8')
        assert not any(token in text for token in forbidden)
    assert 'network_order_test_performed' in (ROOT / 'rc6_preopen.py').read_text(encoding='utf-8')
    assert 'PRAGMA query_only=ON' in (ROOT / 'rc6_candle_integrity.py').read_text(encoding='utf-8')
    assert 'PRAGMA query_only=ON' in (ROOT / 'rc6_functional_health.py').read_text(encoding='utf-8')
