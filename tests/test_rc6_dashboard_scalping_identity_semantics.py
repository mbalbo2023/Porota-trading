from pathlib import Path


def test_scalping_dashboard_separates_global_worker_from_per_identity_intraday_state():
    source=Path("bg_paper_dashboard.py").read_text(encoding="utf-8")
    section=source.split("def scalping_page():",1)[1].split("def _is_today",1)[0]
    assert "PENDIENTE LIVE" in section
    assert "PPI INTRADAY NO SOPORTADO" in section
    assert "PPI_INTRADAY_UNSUPPORTED" in section
    assert "no significan que Scalping global esté caído" in section
    assert "el estado global del worker se muestra por separado" in section
    assert "readiness genérico no cambia" in section
