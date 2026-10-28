from pathlib import Path

SOURCE=Path("rc6_annual_instrument_analysis.py").read_text(encoding="utf-8")


def test_analysis_render_does_not_materialize_full_history_catalog():
    render=SOURCE[SOURCE.index("def render_page"): ]
    assert "catalog = _catalog()" not in render
    assert "families = _families()" in render
    assert "identities = _identities_for_family(family)" in render


def test_analysis_identity_selector_is_family_scoped():
    assert "def _identities_for_family(family)" in SOURCE
    section=SOURCE[SOURCE.index("def _identities_for_family"):SOURCE.index("def _identity")]
    assert "WHERE UPPER(instrument_type)=?" in section
    assert "SELECT DISTINCT symbol,market,settlement" in section


def test_analysis_reuses_one_runtime_truth_projection_per_page():
    render=SOURCE[SOURCE.index("def render_page"):]
    assert render.count("truth_projection.build(_runtime_rows, _runtime_table)") == 1
    assert "_render_family_readiness(truth=runtime_truth)" in render
    assert "_render_reconciliation_evidence(runtime_truth)" in render
