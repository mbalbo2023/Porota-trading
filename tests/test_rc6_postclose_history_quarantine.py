from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_postclose_history_job_is_policy_quarantined_before_pii_client_creation():
    source = (ROOT / "rc6_postclose_history_job.py").read_text(encoding="utf-8")
    guard = "'reason': 'RC6_HISTORY_QUARANTINE'"
    client = "ProductionMarketReader(*_secret(), audit=store.audit_http)"
    assert guard in source
    assert source.index(guard) < source.index(client)


def test_preopen_requires_the_postclose_timer_to_be_disabled():
    source = (ROOT / "rc6_preopen.py").read_text(encoding="utf-8")
    assert "'porota-history-postclose-rc6.timer'" in source
    assert "def blocked_history_timers()" in source
    assert "'history_quarantine': blocked_history_timers()" in source


def test_systemd_unit_requires_explicit_recovery_authorization_and_timer_cannot_enable():
    service = (ROOT / "systemd/porota-history-postclose-rc6.service").read_text(encoding="utf-8")
    timer = (ROOT / "systemd/porota-history-postclose-rc6.timer").read_text(encoding="utf-8")
    assert "ConditionPathExists=/etc/porota/rc6-postclose-history-explicitly-authorized" in service
    assert "[Install]" not in timer
