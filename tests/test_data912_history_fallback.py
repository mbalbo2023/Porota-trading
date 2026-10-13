from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import al_historical_ingest as hist
import ba_data912_history as data912
import m_instrument_universe as universe


def _candles(count=100, price=1000.0, volume=10000.0):
    # A daily archive contains sessions; calendar days previously inserted
    # weekend bars merely to reach this fixture's intended row count.
    rows=[]
    day=datetime.now(timezone.utc).astimezone(ZoneInfo('America/Argentina/Buenos_Aires')).date()-timedelta(days=1)
    while len(rows)<count:
        if day.weekday()<5:rows.append((day.isoformat(),price,price,price,price,volume))
        day-=timedelta(days=1)
    return list(reversed(rows))


def test_archived_instruments_require_minimum_history(tmp_path, monkeypatch):
    database = tmp_path / "market_history.db"
    monkeypatch.setattr(hist, "HIST_DB_PATH", str(database))
    hist.init_db()
    hist.guardar_velas("GGAL", "ACCIONES", _candles(100), "DATA912", False)
    hist.guardar_velas("NUEVO", "ACCIONES", _candles(20), "DATA912", False)

    archived = data912.archived_instruments(min_candles=90)

    assert [item["symbol"] for item in archived] == ["GGAL"]


def test_liquidity_filter_prefers_ppi_when_available(tmp_path, monkeypatch):
    database = tmp_path / "market_history.db"
    monkeypatch.setattr(hist, "HIST_DB_PATH", str(database))
    hist.init_db()
    hist.guardar_velas("GGAL", "ACCIONES", _candles(), "DATA912", False)

    class PPIAvailable:
        calls = 0

        def get_historical_series(self, *_args, **_kwargs):
            self.calls += 1
            return [{"price": 1000.0, "volume": 10000.0}]

    instrument = universe.Instrument("GGAL", "ACCIONES", "A-24HS", "ACCIONES")
    monkeypatch.setattr(universe, "load_watchlist", lambda: [instrument])
    ppi = PPIAvailable()
    selected = universe.filter_by_liquidity([instrument], ppi)

    assert [item.ticker for item in selected] == ["GGAL"]
    assert ppi.calls == 1


def test_empty_data912_history_is_not_counted_as_success(tmp_path, monkeypatch):
    database = tmp_path / "market_history.db"
    state = tmp_path / "state.json"
    monkeypatch.setattr(hist, "HIST_DB_PATH", str(database))
    monkeypatch.setattr(data912, "STATE_PATH", str(state))
    monkeypatch.setattr(data912, "GROUPS", {
        "ACCIONES": ("/live", "/historical/{ticker}")})
    monkeypatch.setattr(data912, "_session", lambda: object())

    def fake_get(_client, path):
        if path == "/live":
            return [{"symbol": "VACIO", "c": 1000, "v": 10000,
                     "currency":"ARS", "volume_kind":"QUANTITY"}]
        return []

    monkeypatch.setattr(data912, "_get_json", fake_get)

    try:
        data912.refresh()
    except RuntimeError:
        pass

    with hist._conn() as connection:
        run = connection.execute(
            "SELECT symbols_ok, symbols_failed FROM ingest_runs ORDER BY run_id DESC LIMIT 1"
        ).fetchone()
    assert run == (0, 1)


def test_scheduler_does_not_register_retired_historical_writers():
    import az_maintenance_scheduler

    scheduler = az_maintenance_scheduler.build_scheduler()
    job_ids = {job.id for job in scheduler.get_jobs()}
    assert job_ids.isdisjoint({
        "maintenance_historical_startup_catchup",
        "maintenance_historical_catchup",
        "maintenance_historical_refresh",
    })
    assert az_maintenance_scheduler.INTERNAL_HISTORICAL_WRITE_JOBS == {
        "historical_refresh",
        "historical_refresh_if_needed",
    }
