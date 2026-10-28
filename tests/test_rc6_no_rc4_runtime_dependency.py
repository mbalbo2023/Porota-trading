from pathlib import Path


def test_rc6_preopen_has_no_rc4_runtime_dependency():
    text = Path('rc6_preopen.py').read_text(encoding='utf-8').lower()
    assert 'rc4' not in text
    assert 'required_rc6_timers' in text
    assert 'network_order_test_performed' in text


def test_rc6_active_units_do_not_reference_rc4():
    for name in (
        'porota-functional-health-rc6.service',
        'porota-history-postclose-rc6.service',
        'porota-host-general-backup-rc6.service',
        'porota-preopen-rc6.service',
        'porota-candle-integrity-rc6.service',
        'porota-a3-history-rc6@.service',
    ):
        text = (Path('systemd') / name).read_text(encoding='utf-8').lower()
        assert 'rc4' not in text
