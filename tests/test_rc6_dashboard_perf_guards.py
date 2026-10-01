from pathlib import Path


ROOT=Path(__file__).resolve().parents[1]


def test_annual_analysis_reuses_one_truth_projection_per_request():
    source=(ROOT/"rc6_annual_instrument_analysis.py").read_text()
    render=source.split("def render_page(",1)[1]
    assert "runtime_truth = truth_projection.build" in render
    assert "_render_family_readiness(catalog, runtime_truth)" in render
    assert "_render_reconciliation_evidence(runtime_truth)" in render


def test_annual_selector_cache_is_tied_to_history_file_signature():
    source=(ROOT/"rc6_annual_instrument_analysis.py").read_text()
    assert '_CATALOG_CACHE = {"signature": None, "rows": []}' in source
    assert "stat.st_mtime_ns" in source
    assert "stat.st_size" in source
    assert '_CATALOG_CACHE["signature"] == signature' in source


def test_universe_does_server_side_catalog_pagination():
    source=(ROOT/"bh_universe_dashboard_hf6.py").read_text()
    assert "LIMIT ? OFFSET ?" in source
    assert "SELECT COUNT(*) total FROM financial_instrument_catalog" in source
    assert "catalog_page=catalog[offset:offset+limit]" not in source


def test_universe_uses_latest_market_snapshot_instead_of_full_snapshot_counts():
    source=(ROOT/"bh_universe_dashboard_hf6.py").read_text()
    assert "MAX(id) id" in source
    assert "latest ON latest.id=s.id" in source
    assert "COUNT(*) snapshots" not in source


def test_universe_activity_counts_are_current_day_scoped():
    source=(ROOT/"bh_universe_dashboard_hf6.py").read_text()
    assert "day_start = datetime.combine" in source
    assert "WHERE decided_at>=?" in source
    assert "WHERE evaluated_at>=?" in source
