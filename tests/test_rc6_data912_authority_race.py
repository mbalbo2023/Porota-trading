"""R234: advisory Data912 writes cannot displace authoritative history.

All cases use an isolated SQLite fixture.  No provider, broker, production DB,
or remote endpoint is invoked.
"""

import pytest

import al_historical_ingest as hist
import ba_data912_history as data912


DAY = "2026-11-11"
NEXT_DAY = "2026-11-12"
PPI_CANDLE = (DAY, 120.0, 121.0, 119.0, 120.0, 1000.0)
DATA912_CANDLE = (DAY, 90.0, 91.0, 89.0, 90.0, 1000.0)


def test_data912_cannot_replace_ppi_committed_after_its_precheck(tmp_path, monkeypatch):
    """Catch a PPI commit between Data912 precheck and the final UPSERT."""
    monkeypatch.setattr(data912, "DATA912_REFRESH_ENABLED", True)
    monkeypatch.setattr(hist, "HIST_DB_PATH", str(tmp_path / "history.sqlite"))
    hist.init_db()
    original_write = hist.guardar_velas
    interleavings = []

    def ppi_commits_just_before_data912_upsert(*args, **kwargs):
        assert args[3] == "DATA912"
        original_write("GGAL", "ACCIONES", [PPI_CANDLE], "PPI", False)
        interleavings.append("PPI_COMMITTED_AFTER_PRECHECK")
        return original_write(*args, **kwargs)

    monkeypatch.setattr(hist, "guardar_velas", ppi_commits_just_before_data912_upsert)
    written, protected = data912._store_data912_candles(
        "GGAL", "ACCIONES", [DATA912_CANDLE]
    )

    assert interleavings == ["PPI_COMMITTED_AFTER_PRECHECK"]
    assert written == 0  # The guarded transaction must not count a rejected row.
    with hist._conn() as connection:
        result = connection.execute(
            "SELECT source,close FROM market_historical_ohlcv WHERE symbol=? AND date=?",
            ("GGAL", DAY),
        ).fetchone()
    assert result == ("PPI", 120.0), (
        "DATA912 replaced newly committed PPI history", result
    )


@pytest.mark.parametrize("previous_source", ("PPI", "BYMA", "IOL", "YAHOO", "UNKNOWN"))
def test_data912_writer_preserves_every_other_source(
    tmp_path, monkeypatch, previous_source
):
    """Unknown and other sources must not be silently replaced by Data912."""
    monkeypatch.setattr(hist, "HIST_DB_PATH", str(tmp_path / "history.sqlite"))
    hist.init_db()
    if previous_source == "IOL":
        # The legacy writer intentionally blocks new IOL rows; an older IOL
        # historical record is inserted solely to test read protection.
        with hist._conn() as conn:
            conn.execute(
                """INSERT INTO market_historical_ohlcv
                   (symbol,asset_class,date,open,high,low,close,volume,source,adjusted)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                ("GGAL", "ACCIONES", *PPI_CANDLE, previous_source, 0),
            )
    else:
        assert hist.guardar_velas(
            "GGAL", "ACCIONES", [PPI_CANDLE], previous_source, False
        ) == 1

    assert hist.guardar_velas(
        "GGAL", "ACCIONES", [DATA912_CANDLE], "DATA912", False
    ) == 0
    with hist._conn() as connection:
        result = connection.execute(
            "SELECT source,close FROM market_historical_ohlcv WHERE symbol=? AND date=?",
            ("GGAL", DAY),
        ).fetchone()
    assert result == (previous_source, 120.0)


def test_data912_counts_only_rows_written_and_can_refresh_its_own(tmp_path, monkeypatch):
    """A protected day is excluded from counts; Data912-owned rows may update."""
    monkeypatch.setattr(hist, "HIST_DB_PATH", str(tmp_path / "history.sqlite"))
    hist.init_db()
    hist.guardar_velas("GGAL", "ACCIONES", [PPI_CANDLE], "PPI", False)
    second = (NEXT_DAY, 98.0, 101.0, 97.0, 100.0, 500.0)

    assert hist.guardar_velas(
        "GGAL", "ACCIONES", [DATA912_CANDLE, second], "DATA912", False
    ) == 1
    refreshed = (NEXT_DAY, 108.0, 111.0, 107.0, 110.0, 510.0)
    assert hist.guardar_velas("GGAL", "ACCIONES", [refreshed], "DATA912", False) == 1
    with hist._conn() as conn:
        result = conn.execute(
            "SELECT date,source,close FROM market_historical_ohlcv "
            "WHERE symbol=? ORDER BY date", ("GGAL",)
        ).fetchall()
    assert result == [(DAY, "PPI", 120.0), (NEXT_DAY, "DATA912", 110.0)]


def test_adjusted_history_remains_fail_closed_on_data912_collision(tmp_path, monkeypatch):
    """The existing incompatible adjusted/raw check remains blocking."""
    monkeypatch.setattr(hist, "HIST_DB_PATH", str(tmp_path / "history.sqlite"))
    hist.init_db()
    hist.guardar_velas("GGAL", "ACCIONES", [PPI_CANDLE], "BYMA", True)
    with pytest.raises(ValueError, match="LEGACY_HISTORY_PRICE_BASIS_COLLISION"):
        hist.guardar_velas("GGAL", "ACCIONES", [DATA912_CANDLE], "DATA912", False)
    with hist._conn() as conn:
        assert conn.execute(
            "SELECT source,adjusted,close FROM market_historical_ohlcv WHERE symbol=?",
            ("GGAL",)
        ).fetchone() == ("BYMA", 1, 120.0)
