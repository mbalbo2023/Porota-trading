"""COM19031 T+1 source-only observer and verified postclose receipt guards."""
from datetime import datetime
from decimal import Decimal
import sqlite3
from zoneinfo import ZoneInfo

import pytest

import bf_production_paper_observer as observer
import bi_operational_services as services
from be_paper_engine import PaperStore, Quote

TZ = ZoneInfo("America/Argentina/Buenos_Aires")


def at(clock):
    return datetime.fromisoformat(clock).astimezone(TZ)


@pytest.mark.parametrize("clock,phase", [
    ("2026-10-30T17:30:00-03:00", "CLOSED"),
    ("2026-11-02T10:30:00-03:00", "OPEN"),
    ("2026-11-02T16:59:59-03:00", "OPEN"),
    ("2026-11-02T17:00:00-03:00", "EQUITY_T1_READONLY"),
    ("2026-11-02T17:30:00-03:00", "EQUITY_T1_READONLY"),
    ("2026-11-02T17:59:59-03:00", "EQUITY_T1_READONLY"),
    ("2026-11-02T18:00:00-03:00", "CLOSED"),
    ("2026-11-07T17:30:00-03:00", "CLOSED"),
])
def test_paper_core_remains_closed_during_source_only_equity(clock, phase):
    assert observer._market_phase(at(clock)) == phase
    assert observer._market_open(at(clock)) is (phase == "OPEN")
    assert observer._equity_t1_readonly_window(at(clock)) is (phase == "EQUITY_T1_READONLY")


@pytest.mark.parametrize("clock,expected", [
    ("2026-10-30T17:14:59-03:00", None),
    ("2026-10-30T17:15:00-03:00", "2026-10-30"),
    ("2026-11-02T17:30:00-03:00", None),
    ("2026-11-02T18:14:59-03:00", None),
    ("2026-11-02T18:15:00-03:00", "2026-11-02"),
])
def test_history_current_day_not_eligible_before_last_segment(clock, expected):
    date_result = observer._history_end_date(at(clock))
    assert (date_result.isoformat() if date_result else None) == expected


@pytest.mark.parametrize("clock,expected", [
    ("2026-10-30T17:14:59-03:00", False),
    ("2026-10-30T17:15:00-03:00", True),
    ("2026-11-02T17:30:00-03:00", False),
    ("2026-11-02T18:14:59-03:00", False),
    ("2026-11-02T18:15:00-03:00", True),
])
def test_background_ingest_does_not_read_partial_day(tmp_path, monkeypatch, clock, expected):
    store = PaperStore(str(tmp_path / "ingest.db"))
    observer._support_schema(store)
    monkeypatch.setattr(observer, "_history_cutoff_repair_complete", lambda: True)
    assert observer._background_ingest_due(store, now=at(clock)) is expected


def test_missing_eod_retries_bounded_and_exact_success_stops(tmp_path, monkeypatch):
    """A floor is only an earliest attempt; a later PPI receipt is required."""
    store = PaperStore(str(tmp_path / "late_eod_retry.db"))
    observer._support_schema(store)
    monkeypatch.setattr(observer, "_history_cutoff_repair_complete", lambda: True)
    start = at("2026-11-02T18:15:00-03:00")
    assert observer._history_daily_sync_due(store, start)
    assert observer._background_ingest_due(store, start)
    with store.connect() as conn:
        conn.execute("""INSERT INTO source_sync
            VALUES('PPI_PRODUCTION_HISTORY','AMARILLO',?,?,0,?)""",
            (start.isoformat(), None, "session=2026-11-02; SOURCE_PENDING"))
    assert not services._postclose_history_source_ready(store, start)
    assert not observer._history_daily_sync_due(store, at("2026-11-02T18:15:01-03:00"))
    assert not observer._background_ingest_due(store, at("2026-11-02T18:44:59-03:00"))
    at_1845 = at("2026-11-02T18:45:00-03:00")
    assert observer._history_daily_sync_due(store, at_1845)
    assert observer._background_ingest_due(store, at_1845)
    with store.connect() as conn:
        conn.execute("""UPDATE source_sync SET status='VERDE',
            last_attempt_at=?,last_success_at=?,items=1,detail=?
            WHERE source='PPI_PRODUCTION_HISTORY'""",
            (at_1845.isoformat(),at_1845.isoformat(),
             "session=2026-11-02; "
             "source_session_verified=2026-11-02:1/1; readiness=NONE"))
    assert services._postclose_history_source_ready(store, at_1845)
    assert not observer._history_daily_sync_due(store, at("2026-11-02T19:15:00-03:00"))
    assert not observer._background_ingest_due(store, at("2026-11-02T19:15:00-03:00"))


def test_failed_daily_sync_fences_second_attempt_same_tick(tmp_path, monkeypatch):
    store = PaperStore(str(tmp_path / "failed_eod_retry.db"))
    observer._support_schema(store)
    monkeypatch.setattr(observer, "_history_cutoff_repair_complete", lambda: True)
    monkeypatch.setattr(observer, "_source_sync_due", lambda *_args: False)
    calls = []
    def failing_history(*_args):
        calls.append("PPI_HISTORY")
        raise RuntimeError("PPI_EOD_NOT_YET_PUBLISHED")
    monkeypatch.setattr(observer, "_download_histories", failing_history)
    start = at("2026-11-02T18:15:00-03:00")
    observer._daily_sync(None, store, now=start)
    assert calls == ["PPI_HISTORY"]
    assert not observer._background_ingest_due(store, now=start)
    assert not observer._history_daily_sync_due(store, now=at("2026-11-02T18:44:59-03:00"))
    assert observer._history_daily_sync_due(store, now=at("2026-11-02T18:45:00-03:00"))
    with store.connect() as conn:
        source = conn.execute("""SELECT status,last_success_at,detail FROM source_sync
          WHERE source='PPI_PRODUCTION_HISTORY'""").fetchone()
    assert source[0] == "AMARILLO" and source[1] is None
    assert "SOURCE_PENDING_AFTER_ATTEMPT" in source[2]


def test_late_equity_source_exact_full_key_no_broker_routes(tmp_path, monkeypatch):
    store = PaperStore(str(tmp_path / "equity.db"))
    observer._support_schema(store)
    with store.connect() as conn:
        conn.execute("""CREATE TABLE candidate_identity_v2(
            ticker TEXT,instrument_type TEXT,market TEXT,currency TEXT,
            settlement TEXT,can_simulate INTEGER,status TEXT,detail TEXT,checked_at TEXT)""")
        for ticker, currency, settlement, family in [
            ("GGAL","ARS","A-24HS","ACCIONES"),
            ("YPFD","ARS","INMEDIATA","ACCIONES"),
            ("DUAL","ARS","A-24HS","ACCIONES"),
            ("DUAL","USD","A-24HS","ACCIONES"),
            ("TBOND","ARS","A-24HS","BONOS"),
        ]:
            identity = (ticker,family,"BYMA",currency,settlement)
            conn.execute("INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (*identity,"PPI_PRIMARY",ticker,"2026-11-02T17:30:00-03:00",
                 "a316","AVAILABLE","READY_PAPER_SPOT","{}"))
            conn.execute("INSERT INTO candidate_identity_v2 VALUES(?,?,?,?,?,?,?,?,?)",
                (*identity,1,"AVAILABLE","PIT","2026-11-02T17:30:00-03:00"))
    exact = ("GGAL","ACCIONES","BYMA","ARS","A-24HS")
    assert observer._equity_t1_readonly_identities(store) == [exact]
    observed = at("2026-11-02T17:30:00-03:00")
    calls = []
    def fake_source(_reader,_store,symbol,family,settlement,*,priority,identity):
        calls.append(identity)
        return Quote(symbol=symbol, asset_class=family, settlement=settlement,
            last=Decimal("100"), bid=Decimal("99"), ask=Decimal("101"),
            bid_size=Decimal("1000"), ask_size=Decimal("1000"),
            observed_at=observed.isoformat(), currency="ARS", market="BYMA",
            metadata_source="PPI_PRIMARY", book_at=observed.isoformat())
    monkeypatch.setattr(observer,"_read_scanner_quote",fake_source)
    class Reader:
        current=book=lambda *_:None
    result = observer._equity_t1_readonly_tick(Reader(),store,now=observed,clock_fn=lambda:observed)
    assert (result["read"],result["failed"],result["real_routes"]) == (1,0,[])
    assert calls == [exact]
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM market_snapshots").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM paper_decisions").fetchone()[0] == 0


def test_offhours_source_never_queries_ppi(tmp_path):
    store = PaperStore(str(tmp_path / "closed.db"))
    observer._support_schema(store)
    class Reader:
        def current(self,*args): raise AssertionError("OFFHOURS_PPI_CALL")
        book=current
    for clock in ("2026-11-02T16:59:59-03:00", "2026-11-02T18:00:00-03:00",
                  "2026-11-07T17:30:00-03:00"):
        assert observer._equity_t1_readonly_tick(Reader(),store,now=at(clock))["read"] == 0


@pytest.mark.parametrize("clock,phase,business,admitted", [
    ("2026-10-30T17:14:59-03:00","CLOSED",True,False),
    ("2026-10-30T17:15:00-03:00","CLOSED",True,True),
    ("2026-11-02T17:30:00-03:00","CLOSED",True,False),
    ("2026-11-02T18:14:59-03:00","CLOSED",True,False),
    ("2026-11-02T18:15:00-03:00","CLOSED",True,True),
    ("2026-11-02T18:15:00-03:00","EQUITY_T1_READONLY",True,False),
    ("2026-11-07T18:15:00-03:00","CLOSED",False,False),
])
def test_report_temporal_eligibility(clock,phase,business,admitted):
    assert services._postclose_report_admissible(phase,at(clock),business_day=business) is admitted


def test_eod_report_requires_actual_late_source_receipt(tmp_path):
    store = PaperStore(str(tmp_path / "report_source.db"))
    observer._support_schema(store)
    now = at("2026-11-02T18:20:00-03:00")
    assert not services._postclose_history_source_ready(store, now)
    with store.connect() as conn:
        conn.execute("""INSERT INTO source_sync
            VALUES('PPI_PRODUCTION_HISTORY','VERDE',?,?,1,?)""",
            ("2026-11-02T18:20:00-03:00", "2026-11-02T18:14:59-03:00",
             "session=2026-11-02; readiness=NONE"))
    assert not services._postclose_history_source_ready(store, now)
    with store.connect() as conn:
        conn.execute("""UPDATE source_sync SET last_success_at=?,detail=?
            WHERE source='PPI_PRODUCTION_HISTORY'""",
            ("2026-11-02T18:16:00-03:00","session=2026-10-30; readiness=NONE"))
    assert not services._postclose_history_source_ready(store, now)
    with store.connect() as conn:
        conn.execute("""UPDATE source_sync SET detail=? WHERE source='PPI_PRODUCTION_HISTORY'""",
                     ("session=2026-11-02; readiness=NONE",))
    assert not services._postclose_history_source_ready(store, now)
    with store.connect() as conn:
        conn.execute("""UPDATE source_sync SET detail=? WHERE source='PPI_PRODUCTION_HISTORY'""",
                     ("session=2026-11-02; "
                      "source_session_verified=2026-11-02:0/1; readiness=NONE",))
    assert not services._postclose_history_source_ready(store, now)
    with store.connect() as conn:
        conn.execute("""UPDATE source_sync SET detail=? WHERE source='PPI_PRODUCTION_HISTORY'""",
                     ("session=2026-11-02; "
                      "source_session_verified=2026-11-02:1/2; readiness=NONE",))
    assert not services._postclose_history_source_ready(store, now)
    with store.connect() as conn:
        conn.execute("""UPDATE source_sync SET detail=? WHERE source='PPI_PRODUCTION_HISTORY'""",
                     ("session=2026-11-02; "
                      "source_session_verified=2026-11-02:1/1; readiness=NONE",))
    assert services._postclose_history_source_ready(store, now)
    with store.connect() as conn:
        conn.execute("""UPDATE source_sync SET status='AMARILLO'
            WHERE source='PPI_PRODUCTION_HISTORY'""")
    assert not services._postclose_history_source_ready(store, now)


def test_requested_history_day_never_proves_provider_eod_row(tmp_path):
    """An authentic older PPI payload cannot be promoted via session=end."""
    history_db = tmp_path / "history.db"
    with sqlite3.connect(history_db) as conn:
        conn.execute("""CREATE TABLE history_canonical_v2(
            symbol TEXT,instrument_type TEXT,market TEXT,currency TEXT,
            settlement TEXT,date TEXT,price_basis TEXT,adjustment_basis TEXT,
            source TEXT,version_known_at TEXT)""")
    class Historical:
        def connect(self):
            return sqlite3.connect(history_db)
    history = Historical()
    key = ("GGAL","ACCIONES","BYMA","ARS","A-24HS")
    now = at("2026-11-02T18:45:00-03:00")
    def raw(day,known,source="PPI_PRODUCTION_HISTORY",symbol="GGAL"):
        with sqlite3.connect(history_db) as conn:
            conn.execute("DELETE FROM history_canonical_v2")
            conn.execute("""INSERT INTO history_canonical_v2 VALUES(
                ?,?,?,?,?,?,'RAW','RAW_NO_ADJUSTMENT',?,?)""",
                (symbol,*key[1:],day,source,known))
    assert observer._verified_ppi_eod_keys(history,[key],now.date(),now) == 0
    raw("2026-10-30", "2026-11-02T18:20:00-03:00")
    assert observer._verified_ppi_eod_keys(history,[key],now.date(),now) == 0
    raw("2026-11-02", "2026-11-02T18:14:59-03:00")
    assert observer._verified_ppi_eod_keys(history,[key],now.date(),now) == 0
    raw("2026-11-02", "2026-11-02T18:20:00-03:00",source="IOL")
    assert observer._verified_ppi_eod_keys(history,[key],now.date(),now) == 0
    raw("2026-11-02", "2026-11-02T18:20:00-03:00",symbol="DUAL")
    assert observer._verified_ppi_eod_keys(history,[key],now.date(),now) == 0
    raw("2026-11-02", "2026-11-02T19:00:00-03:00")
    assert observer._verified_ppi_eod_keys(history,[key],now.date(),now) == 0
    raw("2026-11-02", "2026-11-02T18:20:00-03:00")
    assert observer._verified_ppi_eod_keys(history,[key],now.date(),now) == 1
    other=("YPFD","ACCIONES","BYMA","ARS","A-24HS")
    assert observer._verified_ppi_eod_keys(history,[key,other],now.date(),now) == 1
    assert observer._verified_ppi_eod_keys(history,[key,key],now.date(),now) == 1


def test_service_tick_without_source_marks_provisional_no_report_or_summary(tmp_path, monkeypatch):
    store = PaperStore(str(tmp_path / "report_pending.db"))
    observer._support_schema(store)
    services.init_schema(store)
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):
            fixed = datetime(2026,11,2,18,30,tzinfo=services.TZ)
            return fixed if tz is None else fixed.astimezone(tz)
    monkeypatch.setattr(services,"datetime",Clock)
    monkeypatch.setattr(services,"_job_due",lambda _store,key,_ttl: key == "REPORTS")
    monkeypatch.setattr(services,"backups_suspended",lambda:True)
    monkeypatch.setattr(services,"ensure_reports",lambda *_args,**_kwargs:
        (_ for _ in ()).throw(AssertionError("PREMATURE_PDF")))
    monkeypatch.setattr(services,"send_closing_summary",lambda *_args:
        (_ for _ in ()).throw(AssertionError("PREMATURE_TELEGRAM")))
    assert services.service_tick(store,"CLOSED") == "PROVISIONAL_POSTCLOSE_SOURCE_PENDING"
    with store.connect() as conn:
        receipt = conn.execute("SELECT state,detail FROM operational_jobs WHERE job_key='REPORTS'").fetchone()
    assert tuple(receipt) == ("AMARILLO","PROVISIONAL_POSTCLOSE_SOURCE_PENDING")


def test_eod_cooldown_does_not_declare_closed_while_t1_active():
    assert observer._cooldown_session_state("CLOSED",at("2026-11-02T17:30:00-03:00")) == "BOOT_CALENDAR"
    assert observer._cooldown_session_state("CLOSED",at("2026-11-02T18:15:00-03:00")) == "MARKET_CLOSED"
