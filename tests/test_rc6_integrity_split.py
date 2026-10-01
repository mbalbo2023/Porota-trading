from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_candle_integrity_never_runs_full_db_quick_check():
    candle=(ROOT/"rc6_candle_integrity.py").read_text(encoding="utf-8")
    full=(ROOT/"rc6_full_db_integrity.py").read_text(encoding="utf-8")
    assert "PRAGMA quick_check" not in candle
    assert "DELEGATED_TO_RC6_FULL_DB_INTEGRITY" in candle
    assert "PRAGMA quick_check" in full


def test_full_db_integrity_is_postclose_low_priority_and_non_persistent():
    service=(ROOT/"systemd/porota-full-db-integrity-rc6.service").read_text(encoding="utf-8")
    timer=(ROOT/"systemd/porota-full-db-integrity-rc6.timer").read_text(encoding="utf-8")
    assert "TimeoutStartSec=15min" in service
    assert "Nice=15" in service
    assert "IOSchedulingClass=idle" in service
    assert "17:20:00 America/Argentina/Buenos_Aires" in timer
    assert "Persistent=true" in timer


def test_integrity_split_remains_read_only():
    candle=(ROOT/"rc6_candle_integrity.py").read_text(encoding="utf-8")
    full=(ROOT/"rc6_full_db_integrity.py").read_text(encoding="utf-8")
    for source in (candle,full):
        assert "mode=ro" in source
        assert "PRAGMA query_only=ON" in source
        assert "UPDATE " not in source
        assert "DELETE " not in source


def test_full_db_main_defers_expensive_scan_during_market_hours():
    full=(ROOT/"rc6_full_db_integrity.py").read_text(encoding="utf-8")
    assert "def probe(db_path=DB, *, defer_when_market_open=False)" in full
    assert "DEFERRED_MARKET_OPEN" in full
    assert "probe(defer_when_market_open=True)" in full
