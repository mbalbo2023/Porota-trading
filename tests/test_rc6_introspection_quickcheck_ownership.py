from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def test_hourly_introspection_does_not_run_full_sqlite_quick_check():
    source=(ROOT/"ops_introspection_rc6.py").read_text(encoding="utf-8")
    assert 'one(c, "PRAGMA quick_check"' not in source
    assert '"quick_check": "DELEGATED_TO_RC6_FULL_DB_INTEGRITY"' in source
    assert '"quick_check_executed": False' in source
    assert "sqlite_quick_check_not_ok" not in source


def test_full_db_job_remains_the_only_quick_check_owner():
    full=(ROOT/"rc6_full_db_integrity.py").read_text(encoding="utf-8")
    candle=(ROOT/"rc6_candle_integrity.py").read_text(encoding="utf-8")
    intro=(ROOT/"ops_introspection_rc6.py").read_text(encoding="utf-8")
    assert "PRAGMA quick_check" in full
    assert "PRAGMA quick_check" not in candle
    assert 'one(c, "PRAGMA quick_check"' not in intro


def test_introspection_preserves_paper_safety_checks():
    source=(ROOT/"ops_introspection_rc6.py").read_text(encoding="utf-8")
    assert "real_orders_sent_nonzero" in source
    assert "paper_notification_outbox" in source
