"""R230: a concurrent PPI historical write must outrank opt-in DATA912.

This is a deterministic SQLite-only interleaving. It never calls a provider,
refreshes historical data from the network, or accesses production databases.
"""

import al_historical_ingest as hist
import ba_data912_history as data912


def test_data912_cannot_replace_ppi_committed_after_its_precheck(tmp_path, monkeypatch):
    """The final UPSERT must preserve source precedence inside its transaction."""
    # Exercise the expressly enabled legacy path.  Its default remains OFF.
    monkeypatch.setattr(data912, "DATA912_REFRESH_ENABLED", True)
    monkeypatch.setattr(hist, "HIST_DB_PATH", str(tmp_path / "history.sqlite"))
    hist.init_db()
    date = "2026-11-11"
    ppi = (date, 120.0, 121.0, 119.0, 120.0, 1000.0)
    data912_candle = (date, 90.0, 91.0, 89.0, 90.0, 1000.0)
    original_write = hist.guardar_velas
    interleavings = []

    def ppi_commits_just_before_data912_upsert(*args, **kwargs):
        assert args[3] == "DATA912"
        original_write("GGAL", "ACCIONES", [ppi], "PPI", False)
        interleavings.append("PPI_COMMITTED_AFTER_PRECHECK")
        return original_write(*args, **kwargs)

    monkeypatch.setattr(hist, "guardar_velas", ppi_commits_just_before_data912_upsert)
    data912._store_data912_candles("GGAL", "ACCIONES", [data912_candle])

    assert interleavings == ["PPI_COMMITTED_AFTER_PRECHECK"]
    with hist._conn() as connection:
        result = connection.execute(
            "SELECT source,close FROM market_historical_ohlcv WHERE symbol=? AND date=?",
            ("GGAL", date),
        ).fetchone()
    assert result == ("PPI", 120.0), (
        "A lower-authority DATA912 UPSERT replaced a newly committed PPI row",
        result,
    )
