from __future__ import annotations

import ast
from pathlib import Path

import fl_trader_dashboard_shadow_rc6 as overlay


def test_swing_snapshot_never_infers_swing_from_eod(monkeypatch):
    monkeypatch.setattr(
        overlay,
        "_recent_positions",
        lambda: [
            {"id": 1, "status": "CLOSED", "exit_reason": "EOD_PAPER", "features_json": "{}"},
            {"id": 2, "status": "OPEN", "features_json": '{"execution_style":"SCALPING_PAPER"}'},
        ],
    )
    data = overlay._swing_snapshot()
    assert data["mode"] == "SHADOW_ONLY"
    assert data["explicit_swing"] == 0
    assert data["intraday"] == 1
    assert data["eod_closed"] == 1
    assert data["runtime_state"] == "EVIDENCE_PENDING"
    assert data["real_execution_allowed"] is False


def test_swing_snapshot_reports_only_explicit_style(monkeypatch):
    monkeypatch.setattr(
        overlay,
        "_recent_positions",
        lambda: [
            {"id": 3, "status": "OPEN", "features_json": '{"execution_style":"SWING_PAPER"}'},
        ],
    )
    data = overlay._swing_snapshot()
    assert data["explicit_swing"] == 1
    assert data["runtime_state"] == "EXPLICIT_STYLE_PRESENT"
    assert data["economics_model"] == "SWING_NON_INTRADAY"


def test_caucion_dashboard_stays_shadow_and_auto_off(monkeypatch):
    monkeypatch.setattr(overlay.bg, "_table", lambda name: False)
    monkeypatch.setenv("CAUCIONES_AUTO_PLACEMENT", "false")
    html = overlay.caucion_shadow_section()
    assert "SHADOW_ONLY" in html
    assert "CAUCIONES_AUTO_PLACEMENT" in html
    assert ">OFF<" in html
    assert "Ejecución real permitida por esta capa" in html
    assert "<b>NO</b>" in html
    assert "<form" not in html.lower()
    assert "<button" not in html.lower()


def test_scalping_section_labels_metrics_observability_only(monkeypatch):
    monkeypatch.setattr(
        overlay,
        "_revision_snapshot",
        lambda: {
            "state": "EVIDENCE_AVAILABLE",
            "total_valid": 10,
            "invalid_events": 0,
            "refresh_mutable": 6,
            "reject_closed_revision": 4,
            "threshold_seconds": 120,
            "age_p50_seconds": 80,
            "age_p95_seconds": 155,
            "age_max_seconds": 170,
            "latest_received_at": "2026-09-11T19:30:00-03:00",
            "by_symbol": {"GGAL": 4, "YPFD": 2},
        },
    )
    html = overlay.scalping_revision_section()
    assert "telemetría RC6" in html
    assert "El umbral operativo sigue en 120 s" in html
    assert "REJECT_CLOSED_REVISION" in html
    assert "p95" in html
    assert "GGAL" in html
    assert "<form" not in html.lower()


def test_install_places_sections_in_operator_context(monkeypatch):
    monkeypatch.setattr(overlay, "_installed", False)
    monkeypatch.setattr(overlay.bg, "scalping_page", lambda: "<main><h1>Scalping</h1></main>")
    monkeypatch.setattr(overlay.bg, "trading_page", lambda section="": f"<main><h1>{section}</h1></main>")
    monkeypatch.setattr(overlay.bg, "live_page", lambda: "<main><h1>En vivo</h1></main>")
    monkeypatch.setattr(overlay, "scalping_revision_section", lambda: "<section id='scalp-extra'>S</section>")
    monkeypatch.setattr(overlay, "swing_shadow_section", lambda: "<section id='swing-extra'>W</section>")
    monkeypatch.setattr(overlay, "caucion_shadow_section", lambda: "<section id='caucion-extra'>C</section>")
    monkeypatch.setattr(overlay, "live_operator_section", lambda: "<section id='live-extra'>L</section>")

    overlay.install()

    assert "scalp-extra" in overlay.bg.scalping_page()
    assert "swing-extra" in overlay.bg.trading_page("estrategias")
    assert "caucion-extra" in overlay.bg.trading_page("cauciones")
    assert "swing-extra" not in overlay.bg.trading_page("acciones-cedears")
    assert "caucion-extra" not in overlay.bg.trading_page("renta-fija")
    assert "live-extra" in overlay.bg.live_page()


def test_overlay_imports_have_no_broker_or_network_clients():
    source = Path("fl_trader_dashboard_shadow_rc6.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert "requests" not in imported
    assert "c_ppi_client" not in imported
    assert "be_paper_engine" not in imported
    assert "bm_exit_supervisor" not in imported
