from __future__ import annotations

from pathlib import Path

import bg_paper_dashboard as bg
import fg_intraday_contract_policy_rc6 as contract_policy
import fq_dashboard_swing_options_telemetry_rc6 as ui


FAMILY_OVERVIEW_STRINGS = (
    "Acciones y CEDEAR",
    "Renta fija",
    "Actividad real de análisis por familia",
    "Contratos y readiness de opciones",
    "Fondos locales y exterior",
    "Licitaciones y canjes",
)


def _fake_rows(sql, params=()):
    text = " ".join(str(sql).split()).upper()
    if "FROM CANDIDATE_UNIVERSE" in text and "OPCIONES" in text:
        return [{"total": 437, "available": 437, "can_simulate": 0}]
    if "FROM MARKET_SNAPSHOTS" in text and "OPCIONES" in text:
        return [{"snapshots": 12, "symbols": 4, "last_snapshot": "2026-09-12T00:00:00+00:00"}]
    if "FROM PPI_INTRADAY_CONTRACT_STATE" in text:
        return [{"state": "PENDING_LIVE_CONFIRMATION", "n": 4}]
    if "FROM PAPER_POSITIONS" in text:
        return []
    return []


def test_final_routes_keep_clean_eod_page_and_add_shadow_sections(monkeypatch):
    original_trading = bg.trading_page
    original_scalping = bg.scalping_page
    original_installed = ui._installed
    try:
        monkeypatch.setattr(
            bg,
            "trading_page",
            lambda section="": (
                "<main><h1>Trading — Estrategias</h1>"
                "<section id='rc6-eod-overnight-final-route'>"
                "<h2>EOD / Overnight</h2><b>CURRENT_EOD</b></section></main>"
                if section == "estrategias"
                else f"<main>{section}</main>"
            ),
        )
        monkeypatch.setattr(bg, "scalping_page", lambda: "<main><h1>Scalping</h1></main>")
        monkeypatch.setattr(bg, "_rows", _fake_rows)
        monkeypatch.setattr(bg, "_table", lambda name: name in {"paper_positions", "ppi_intraday_contract_state"})
        monkeypatch.setattr(ui, "_revision_snapshot", lambda: {
            "state": "NO_EVIDENCE", "total_valid": 0, "invalid_events": 0,
            "refresh_mutable": 0, "reject_closed_revision": 0,
            "threshold_seconds": 120, "age_p50_seconds": None,
            "age_p95_seconds": None, "age_max_seconds": None,
            "latest_received_at": None, "by_symbol": {},
        })
        ui._installed = False
        ui.install()

        strategies = bg.trading_page("estrategias")
        assert strategies.count("EOD / Overnight") == 1
        assert "CURRENT_EOD" in strategies
        assert "SWING_PAPER — evaluación overnight" in strategies
        assert "SHADOW_ONLY" in strategies
        assert "EOD exit binding" in strategies
        assert "NO — CURRENT_EOD permanece vigente" in strategies
        for text in FAMILY_OVERVIEW_STRINGS:
            assert text not in strategies

        options = bg.trading_page("opciones")
        assert "Opciones — telemetría de datos y contrato" in options
        assert "READ_ONLY / OBSERVABILITY_ONLY" in options
        assert "437" in options

        scalping = bg.scalping_page()
        assert "Calidad temporal de velas — telemetría RC6" in scalping
        assert "120 s" in scalping
        assert "OBSERVABILITY_ONLY" in scalping
    finally:
        bg.trading_page = original_trading
        bg.scalping_page = original_scalping
        ui._installed = original_installed


def test_overlay_has_no_execution_surface_and_installs_after_p0():
    src = Path("fq_dashboard_swing_options_telemetry_rc6.py").read_text(encoding="utf-8").lower()
    for forbidden in (
        "requests.post(", "requests.put(", "requests.delete(",
        "send_order(", "place_order(", "/operar", "insert into", "update ", "delete from",
    ):
        assert forbidden not in src

    layout = Path("eq_dashboard_table_layout_rc6.py").read_text(encoding="utf-8")
    assert layout.index("eod_route_fix.install()") < layout.index("strategy_telemetry.install()")

    swing = Path("fi_swing_paper_shadow_rc6.py").read_text(encoding="utf-8")
    assert 'SHADOW_MODE = "SHADOW_ONLY"' in swing
    assert "eod_exit_binding=False" in swing
    assert "real_execution_allowed=False" in swing


def test_scalping_telemetry_remains_best_effort_and_threshold_contract_unchanged():
    src = Path("cf_intraday_scalping.py").read_text(encoding="utf-8")
    assert "SCALPING_INTRADAY_REVISION_TELEMETRY" in src
    assert "Telemetry is best-effort" in src
    assert "DEFAULT_MUTABLE_SECONDS" in src
    assert contract_policy.DEFAULT_MUTABLE_SECONDS == 120
