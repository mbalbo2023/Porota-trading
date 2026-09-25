from datetime import date
from pathlib import Path

import ek_history_freshness_metrics_rc5 as metrics


def test_freshness_metric_contract():
    metrics.assert_freshness_metric_contract()
    assert metrics._depth(30) == "LT90"
    assert metrics._depth(90) == "LT180"
    assert metrics._freshness(date(2025,12,9),date(2026,9,4))[0] == "STALE_UNVERIFIED_CALENDAR"


def test_dashboard_separates_any_depth_and_freshness():
    s=Path('bg_paper_dashboard.py').read_text(encoding='utf-8')
    assert 'freshness_qualified_metrics' in s
    assert 'Cobertura histórica operativa' in s
    assert 'Historia fresca ≥30' in s
    assert 'Historia fresca ≥90' in s
    assert 'Historia fresca ≥180' in s
    assert 'Profundidad y freshness son métricas distintas.' in s
    assert 'CLOSE_ONLY se informa por separado' in s
    assert 'no cuentan como fresh ≥90' in s
