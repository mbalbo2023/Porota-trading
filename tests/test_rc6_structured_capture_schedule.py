from pathlib import Path


def test_observer_runs_structured_byma_only_in_session_and_on_close_snapshot():
    source = Path("bf_production_paper_observer.py").read_text(encoding="utf-8")
    assert 'PUBLIC_STRUCTURED_CAPTURE_SECONDS = max(900' in source
    assert 'phase == "OPEN"' in source
    assert 'previous_phase == "OPEN" and phase == "CLOSED"' in source
    assert '_public_probe(store, structured=True)' in source
    assert 'collect_public_sources()' in source


def test_scheduler_exposes_cascade_cadences():
    source = Path("de_scheduler_catalog_hf6.py").read_text(encoding="utf-8")
    for key in ("PPI_FOCUS_QUOTES", "PPI_UNIVERSE_COVERAGE", "IOL_MISSING_FIELDS", "BYMA_STRUCTURED_CAPTURE"):
        assert key in source
