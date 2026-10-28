from pathlib import Path


def test_observer_reads_host_structured_byma_only_in_session_and_on_close():
    source = Path("bf_production_paper_observer.py").read_text(encoding="utf-8")
    assert 'PUBLIC_STRUCTURED_CAPTURE_SECONDS = max(900' in source
    assert 'phase == "OPEN"' in source
    assert 'previous_phase == "OPEN" and phase == "CLOSED"' in source
    assert '_public_probe(store, structured=True)' in source
    assert 'PUBLIC_CAPTURE_PATH.stat()' in source
    assert 'single_writer=host_collector' in source
    assert 'collect_public_sources()' not in source


def test_scheduler_exposes_cascade_cadences():
    source = Path("de_scheduler_catalog_hf6.py").read_text(encoding="utf-8")
    for key in ("PPI_FOCUS_QUOTES", "PPI_UNIVERSE_COVERAGE", "IOL_MISSING_FIELDS", "BYMA_STRUCTURED_CAPTURE"):
        assert key in source
