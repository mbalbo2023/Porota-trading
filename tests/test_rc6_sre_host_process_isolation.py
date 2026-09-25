from pathlib import Path

HOST_PROBES = (
    "systemd/porota-fast-functional-health-rc6.service",
    "systemd/porota-full-db-integrity-rc6.service",
    "systemd/porota-candle-integrity-rc6.service",
    "systemd/porota-preopen-rc6.service",
)


def test_host_probes_never_exec_python_inside_observer():
    for raw in HOST_PROBES:
        text = Path(raw).read_text(encoding="utf-8")
        assert "docker exec" not in text, raw
        assert "/usr/bin/python3" in text, raw


def test_candle_integrity_keeps_same_db_and_readonly_program():
    text = Path("systemd/porota-candle-integrity-rc6.service").read_text(encoding="utf-8")
    assert "PAPER_V17_DB_PATH=/opt/porota-trading/data/paper_v17/observer_v17.db" in text
    assert "ExecStart=/usr/bin/python3 /opt/porota-trading/rc6_candle_integrity.py" in text
    assert "Nice=10" in text


def test_full_integrity_is_postclose_only():
    service = Path("systemd/porota-full-db-integrity-rc6.service").read_text(encoding="utf-8")
    timer = Path("systemd/porota-full-db-integrity-rc6.timer").read_text(encoding="utf-8")
    assert "/usr/local/lib/porota-sre-rc6/rc6_full_db_integrity.py" in service
    assert "Nice=10" in service
    assert "OnCalendar=Mon..Fri *-*-* 17:20:00 America/Argentina/Buenos_Aires" in timer
    assert "Persistent=true" in timer


def test_preopen_uses_hardened_host_wrapper():
    text = Path("systemd/porota-preopen-rc6.service").read_text(encoding="utf-8")
    assert "PYTHONPATH=/opt/porota-trading" in text
    assert "/usr/local/lib/porota-sre-rc6/rc6_preopen_host_hardened.py" in text


def test_candle_integrity_avoids_intraday_full_db_scan():
    source = Path("rc6_candle_integrity.py").read_text(encoding="utf-8")
    assert "PRAGMA quick_check" not in source
    assert "FULL_SCAN_POSTCLOSE_ONLY" in source
    assert "full_integrity_check_performed" in source


def test_operational_sre_telemetry_defers_full_db_scan_to_postclose():
    source = Path("bi_operational_services.py").read_text(encoding="utf-8")
    collect = source.split("def collect_sre", 1)[1].split("def create_backup", 1)[0]
    assert "PRAGMA quick_check" not in collect
    assert "FULL_SCAN_POSTCLOSE_ONLY" in collect
    assert "full_integrity_check_performed" in collect
