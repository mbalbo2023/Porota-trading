from datetime import datetime
from pathlib import Path

import bf_production_paper_observer as observer


def test_observer_reads_host_structured_byma_only_in_session_and_on_close():
    source = Path("bf_production_paper_observer.py").read_text(encoding="utf-8")
    assert 'PUBLIC_STRUCTURED_CAPTURE_SECONDS = max(900' in source
    assert 'phase == "OPEN"' in source
    # The 17:00 transition to read-only equity T+1 is NOT source closure.
    assert 'source_open = phase in {"OPEN", "EQUITY_T1_READONLY"}' in source
    assert 'closing_snapshot = previous_source_open and not source_open' in source
    assert 'previous_source_open = source_open' in source
    assert 'if should_capture or closing_snapshot:' in source
    assert '_public_probe(store, structured=True)' in source
    assert 'PUBLIC_CAPTURE_PATH.stat()' in source
    assert 'single_writer=host_collector' in source
    assert 'collect_public_sources()' not in source


def test_structured_capture_finishes_after_t1_source_close_not_spot_close():
    times = (
        datetime(2026, 11, 2, 16, 59, tzinfo=observer.TZ),
        datetime(2026, 11, 2, 17, 1, tzinfo=observer.TZ),
        datetime(2026, 11, 2, 18, 1, tzinfo=observer.TZ),
    )
    phases = [observer._market_phase(at) for at in times]
    assert phases == ["OPEN", "EQUITY_T1_READONLY", "CLOSED"]
    source_open = [phase in {"OPEN", "EQUITY_T1_READONLY"} for phase in phases]
    assert source_open == [True, True, False]
    # No premature closing snapshot at 17:00; one true close after 18:00.
    closing_snapshots = [previous and not current for previous, current in
                         zip(source_open, source_open[1:])]
    assert closing_snapshots == [False, True]


def test_scheduler_exposes_cascade_cadences():
    source = Path("de_scheduler_catalog_hf6.py").read_text(encoding="utf-8")
    for key in ("PPI_FOCUS_QUOTES", "PPI_UNIVERSE_COVERAGE", "IOL_MISSING_FIELDS", "BYMA_STRUCTURED_CAPTURE"):
        assert key in source
