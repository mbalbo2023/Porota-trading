"""Contrato de aceptación del dashboard RC3 y de la política PAPER-only."""

from datetime import datetime
from pathlib import Path

import pytest

import bg_paper_dashboard as dashboard
import cg_paper_workspace as workspace
import o_dashboard
import porota_mode_manager as mode_manager


def _paper_snapshot():
    now = datetime.now(dashboard.TZ).isoformat()
    balances = [
        {"currency": currency, "cash": value, "pending_proceeds": "0",
         "caucion_principal": "0", "realized_pnl": "0", "unrealized_pnl": "0",
         "equity": value}
        for currency, value in {
            "ARS": "1000000", "USD": "1000", "USD_MEP": "1000", "USD_CCL": "1000"
        }.items()
    ]
    return {
        "state": {"heartbeat_at": now, "ppi_auth": "OK", "real_orders_sent": 0},
        "closed": [], "realized": [], "open": [], "spot_state": "READY",
        "caucion_state": "READY", "balances_by_currency": balances,
        "valuation_quality": [
            {"currency": row["currency"], "state": "CURRENT", "measured_at": now}
            for row in balances
        ],
    }


def test_home_exposes_four_cash_ledgers_and_daily_simulated_summary(monkeypatch):
    monkeypatch.setattr(dashboard, "snapshot", _paper_snapshot)
    monkeypatch.setattr(dashboard, "_table", lambda name, path=None: True)
    monkeypatch.setattr(
        dashboard, "_rows",
        lambda sql, params=(), path=None:
            [{"db_integrity": "ok"}] if "sre_snapshots" in sql else [],
    )
    monkeypatch.setattr(dashboard, "_health_components", lambda: [
        {"key": "TELEGRAM", "state": "VERDE", "applicable": True},
        {"key": "PPI_PRODUCTION_AUTH", "state": "VERDE", "applicable": True},
    ])
    page = dashboard.home_page()
    for currency in ("ARS", "USD", "USD_MEP", "USD_CCL"):
        assert f"Patrimonio paper {currency}" in page
    assert "Resumen simulado del día" in page
    assert "Compras simuladas" in page and "Ventas simuladas" in page
    assert "Cuenta fills PAPER, no órdenes enviadas a PPI" in page
    assert "Órdenes reales" in page and ">0<" in page


def test_health_distinguishes_pending_from_not_applicable(monkeypatch):
    monkeypatch.setattr(dashboard, "MODE", "PRODUCTION_PAPER")
    assert dashboard._mode_applies("SIMULACIÓN PRODUCTIVA")
    assert not dashboard._mode_applies("SANDBOX")
    assert dashboard._effective_health_state(
        None, present=False, applicable=True
    ) == "PENDIENTE"
    assert dashboard._effective_health_state(
        None, present=False, applicable=False
    ) == "NO_APLICA"
    assert "PENDIENTE" in dashboard._health_status("PENDIENTE")
    assert "NO_APLICA" in dashboard._health_status("NO_APLICA")


def test_real_money_mode_is_permanently_disabled():
    with pytest.raises(RuntimeError, match="DESHABILITADA PERMANENTEMENTE"):
        mode_manager.production(["--confirm-real-money"])


def test_release_version_and_image_are_consistent():
    from _version import VERSION, IMAGE
    assert dashboard.VERSION == o_dashboard.VERSION == VERSION
    assert workspace.IMAGE == IMAGE
    root = Path(__file__).resolve().parents[1]
    compose = (root / "docker-compose.yml").read_text()
    mode = (root / "porota_mode_manager.py").read_text()
    assert "NO es el contrato canónico de PRODUCTION_PAPER" in compose
    assert "from cg_paper_workspace import DB_ENV, CONTAINER_DB, IMAGE" in mode
    assert 'IMAGE, "o_dashboard.py"' in mode
    assert 'IMAGE, "bv_paper_runtime.py"' in mode
