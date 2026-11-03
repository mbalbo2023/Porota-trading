"""The local EOD archive can certify postclose without PPI authentication."""

from datetime import datetime, timezone
import sqlite3

import ak_byma_calendar as calendar
import bf_production_paper_observer as observer
from rc6_local_eod_history import _runtime_block


def test_cooldown_closed_heartbeat_admits_only_audited_postclose(monkeypatch):
    monday = datetime(2026, 10, 26, 19, 15, tzinfo=observer.TZ)
    state = observer._cooldown_session_state("CLOSED", monday)
    assert state == "MARKET_CLOSED"

    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("""CREATE TABLE observer_state(id INTEGER PRIMARY KEY, mode TEXT,
        real_orders_sent INTEGER, session_state TEXT, heartbeat_at TEXT)""")
    db.execute("CREATE TABLE paper_positions(status TEXT)")
    db.execute("INSERT INTO observer_state VALUES(1, 'PRODUCTION_PAPER', 0, ?, ?)",
               (state, monday.isoformat()))
    assert _runtime_block(db, monday.astimezone(timezone.utc)) is None
    db.execute("UPDATE observer_state SET session_state='BOOT_CALENDAR' WHERE id=1")
    assert _runtime_block(db, monday.astimezone(timezone.utc)) == "RUNTIME_CLOSED_UNVERIFIED"
    db.execute("UPDATE observer_state SET session_state=?,real_orders_sent=1 WHERE id=1", (state,))
    try:
        _runtime_block(db, monday.astimezone(timezone.utc))
    except ValueError as exc:
        assert str(exc) == "LOCAL_EOD_PAPER_SAFETY_REQUIRED"
    else:
        raise AssertionError("a real order must block local EOD")
    db.close()

    before = monday.replace(hour=17, minute=14)
    assert observer._cooldown_session_state("CLOSED", before) == "BOOT_CALENDAR"
    assert observer._cooldown_session_state("OPEN", monday) == "BOOT_CALENDAR"
    assert observer._cooldown_session_state("CLOSED", monday.replace(day=25)) == "BOOT_CALENDAR"
    assert observer._cooldown_session_state("CLOSED", monday.replace(year=2027)) == "BOOT_CALENDAR"
    monkeypatch.setattr(calendar, "es_dia_habil_operativo", lambda _day: 1 / 0)
    assert observer._cooldown_session_state("CLOSED", monday) == "BOOT_CALENDAR"
