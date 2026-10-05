"""Large native PIT histories: true bars, bounded work and unchanged sources."""
from collections import Counter
from contextlib import contextmanager
import socket
import sqlite3
from time import monotonic, process_time

import pytest

from cu_history_store_v2_hf6 import Candle, append_many
from rc6_shadow_runtime import preopen
from tests.test_rc6_history_snapshot_copy import inventory
from tests.test_rc6_shadow_preopen_runtime import _make_database, AS_OF, OPEN, CUT


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("NETWORK_FORBIDDEN_IN_LARGE_NATIVE_HISTORY")
    monkeypatch.setattr(socket.socket, "connect", forbidden)


@pytest.fixture(scope="module")
def large_native_history(tmp_path_factory):
    root = tmp_path_factory.mktemp("native-12k-60k-history")
    assets = [{"ticker": f"L{index:05d}", "instrument_type": "ACCIONES", "market": "BYMA",
               "currency": "ARS", "settlement": "A-24HS"} for index in range(12000)]
    trading = _make_database(root/"trading.sqlite", catalogue=assets)
    folder = root/"historical-source"
    folder.mkdir()
    history = folder/"history.sqlite"
    class Store:
        @contextmanager
        def connect(self):
            connection = sqlite3.connect(history)
            connection.row_factory = sqlite3.Row
            try:
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
            finally:
                connection.close()
    batches, rows = [], []
    started = monotonic()
    for revision in range(5):
        for asset in assets:
            rows.append(Candle(asset["ticker"], "ACCIONES", "BYMA", "A-24HS", "2026-09-30",
                100, 106, 99, 100+revision, 10+revision, "PPI_API", False,
                f"2026-10-01T16:00:0{revision}Z", {"volume_unit": "SHARES"}, currency="ARS",
                volume_kind="QUANTITY", provider_at="2026-09-30T20:00:00Z"))
            if len(rows) == 10000:
                result = append_many(Store(), rows)
                batches.append({"rows": result["committed_rows"], "elapsed_seconds": monotonic()-started})
                rows = []
    assert not rows and sum(batch["rows"] for batch in batches) == 60000
    with Store().connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM history_versions_v2").fetchone()[0] == 60000
        assert connection.execute("SELECT COUNT(*) FROM history_canonical_v2").fetchone()[0] == 12000
    return {"trading": trading, "history": history, "folder": folder,
            "inventory": inventory(folder), "batches": batches, "measurements": []}


def _read(fixture, *, cutoff=CUT):
    started = monotonic()
    cpu_started = process_time()
    result = preopen.build_preopen_inputs(fixture["trading"], fixture["history"],
        as_of=AS_OF, session_open=OPEN, cutoff=cutoff)
    elapsed = monotonic()-started
    fixture["measurements"].append({"cutoff": cutoff, "wall_seconds": elapsed,
        "cpu_seconds": process_time()-cpu_started,
        "rows_read": result["quality"]["source_rows_read"].get("history_versions_v2", 0),
        "accepted_history": len(result["history"]),
        "volumes": sorted({row["volume"] for row in result["history"]}),
        "status": result["quality"]["status"], "unavailable_sources": result["quality"]["unavailable_sources"],
        "truncated_sources": result["quality"]["truncated_sources"]})
    return result, elapsed


def test_NEW_large_native_60k_revisions_select_exact_12k_bars_under_two_seconds(large_native_history, monkeypatch, record_property):
    fixture = large_native_history
    original = sqlite3.connect
    def guarded(path, *args, **kwargs):
        assert str(fixture["history"]) not in str(path), "SQLite must never open the historical source"
        return original(path, *args, **kwargs)
    monkeypatch.setattr(sqlite3, "connect", guarded)
    preopen._stamp.cache_clear()
    result, elapsed = _read(fixture)
    assert elapsed <= 2.0
    assert result["quality"]["unavailable_sources"] == result["quality"]["truncated_sources"] == []
    assert result["quality"]["source_rows_read"]["history_versions_v2"] == 12000
    assert len(result["history"]) == 12000
    assert set(Counter(row["ticker"] for row in result["history"]).values()) == {1}
    assert {row["volume"] for row in result["history"]} == {14}
    assert {row["session"] for row in result["history"]} == {"2026-09-30"}
    assert {row["currency"] for row in result["history"]} == {"ARS"}
    assert {row["volume_unit"] for row in result["history"]} == {"SHARES"}
    assert inventory(fixture["folder"]) == fixture["inventory"]
    record_property("large_native_measurement", str(fixture["measurements"][-1]))


def test_NEW_large_native_PIT_cut_selects_earlier_revision_without_counting_versions_as_sessions(large_native_history, record_property):
    fixture = large_native_history
    preopen._stamp.cache_clear()
    result, elapsed = _read(fixture, cutoff="2026-10-01T16:00:02.999999Z")
    assert elapsed <= 2.0
    assert len(result["history"]) == 12000
    assert {row["volume"] for row in result["history"]} == {12}
    assert {row["version_known_at"] for row in result["history"]} == {"2026-10-01T16:00:02.000000+00:00"}
    assert result["quality"]["unavailable_sources"] == result["quality"]["truncated_sources"] == []
    assert inventory(fixture["folder"]) == fixture["inventory"]
    record_property("large_native_measurement", str(fixture["measurements"][-1]))


def test_NEW_large_native_processing_timeout_publishes_no_partial_history_and_preserves_source(large_native_history, monkeypatch, record_property):
    fixture = large_native_history
    visited = []
    original = preopen._clock_pair
    def costly_validation(*args, **kwargs):
        # Exercise the real monotonic wall deadline with bounded CPU work,
        # rather than advancing a simulated clock or sleeping past the limit.
        validation_end = monotonic()+.0005
        while monotonic() < validation_end:
            pass
        visited.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(preopen, "_clock_pair", costly_validation)
    result, elapsed = _read(fixture)
    assert elapsed <= 2.0
    assert elapsed >= 1.9
    assert 1 <= len(visited) < 12000
    assert result["history"] == []
    assert result["quality"]["status"] == "NO_VERIFICADO"
    assert result["quality"]["unavailable_sources"] == ["history:TIME_BUDGET_EXHAUSTED"]
    assert inventory(fixture["folder"]) == fixture["inventory"]
    record_property("large_native_measurement", str(fixture["measurements"][-1]))
    record_property("large_native_validations_before_deadline", len(visited))
    record_property("large_native_fixture_batches", str(fixture["batches"]))
    record_property("large_native_source_inventory", str(fixture["inventory"]))
