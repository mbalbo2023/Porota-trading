from pathlib import Path


def test_history_dashboard_separates_daily_history_from_intraday_candles():
    source=Path("bg_paper_dashboard.py").read_text(encoding="utf-8")
    assert "Última barra intradiaria" in source
    assert "Última muestra de mercado" in source
    assert "Worker de velas" in source
    assert "Histórico diario y velas intradiarias son capas distintas" in source
    assert "MAX(bar_end) last_bar" in source
    assert "MAX(event_at) last_event" in source


def test_history_dashboard_exposes_rotation_feasibility_without_increasing_limit():
    source=Path("bg_paper_dashboard.py").read_text(encoding="utf-8")
    assert "Foco intradiario" in source
    assert "Rotación universo completo" in source
    assert "rotation_feasible" in source
    assert "no aumentar el lote a ciegas" in source
    assert "último recomendado" in source
