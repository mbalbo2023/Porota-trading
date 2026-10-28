from datetime import date

from ek_history_freshness_metrics_rc5 import _business_gap_2026


def test_cedear_history_freshness_uses_underlying_us_business_days():
    # El lunes 07/09/2026 es Labor Day US, aunque BYMA tenga rueda.
    assert _business_gap_2026(date(2026, 9, 4), date(2026, 9, 8), "ACCIONES") == 2
    assert _business_gap_2026(date(2026, 9, 4), date(2026, 9, 8), "CEDEARS") == 1
