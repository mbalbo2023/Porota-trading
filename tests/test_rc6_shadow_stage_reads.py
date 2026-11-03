"""SHADOW stage reads preserve economics and skip unavailable book paths."""
from datetime import datetime, timezone
import sqlite3

import pytest

from rc6_shadow_runtime.stages import enrich_pipeline


@pytest.mark.parametrize(("at", "ready", "expects_book_read"), [
    ("2026-10-14T23:00:00+00:00", True, False),
    ("2026-10-14T15:00:00+00:00", False, False),
    ("2026-10-14T15:00:00+00:00", True, True),
])
def test_native_books_are_read_only_when_economics_can_use_them(
        tmp_path, at, ready, expects_book_read):
    database = tmp_path / "paper.db"
    with sqlite3.connect(database):
        pass
    # The absent books table makes an unnecessary scan fail visibly. A native
    # signal can still be reported after close without reading a book.
    row = {"identity": ["ABC", "ACCIONES", "BYMA", "ARS", "A-24HS"],
           "pipeline": {"SIGNAL_READY": ready}, "entry_authority": False}
    report = {"engines": {"SCALPING": {"telemetry": [row]}}}
    if expects_book_read:
        with pytest.raises(sqlite3.OperationalError, match="market_snapshots"):
            enrich_pipeline(database, report, as_of=datetime.fromisoformat(at))
    else:
        result = enrich_pipeline(database, report, as_of=datetime.fromisoformat(at))
        assert result["engines"]["SCALPING"]["telemetry"][0]["economics_shadow"]["status"] == "NO_VERIFICADO"
        assert result["engines"]["SCALPING"]["telemetry"][0]["factual_execution"] == "NOT_CALLED"
