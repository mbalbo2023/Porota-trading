"""R87 regression: dashboard cannot present a pending custody seal as PAPER GO."""

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

import bg_paper_dashboard as dashboard


def test_pending_custody_manifest_takes_precedence_over_paper_observer(tmp_path, monkeypatch):
    data = tmp_path / "data"
    (data / "database").mkdir(parents=True)
    (data / "operation_mode.json").write_text(
        json.dumps({"mode": "PENDING_CUSTODY_SEAL"}), encoding="utf-8"
    )
    monkeypatch.setattr(dashboard, "DB_PATH", str(data / "database" / "paper.sqlite"))
    monkeypatch.setattr(dashboard, "CONFIGURED_MODE", "PRODUCTION_PAPER")
    monkeypatch.setattr(dashboard, "MODE", "DETENIDO")
    monkeypatch.setattr(dashboard, "_table", lambda _name: True)
    monkeypatch.setattr(dashboard, "_rows", lambda *_a, **_kw: [{"mode": "PRODUCTION_PAPER"}])

    assert dashboard._effective_mode() == "PENDING_CUSTODY_SEAL"
    banner = dashboard.mode_banner()
    assert "CUSTODIA PENDIENTE" in banner
    assert "MODO SIMULACIÓN PRODUCTIVA" not in banner
    assert "PRODUCTION_PAPER" in banner  # mismatch clearly disclosed


def test_pending_custody_observer_fails_closed_without_manifest(tmp_path, monkeypatch):
    data = tmp_path / "data"
    (data / "database").mkdir(parents=True)
    monkeypatch.setattr(dashboard, "DB_PATH", str(data / "database" / "paper.sqlite"))
    monkeypatch.setattr(dashboard, "CONFIGURED_MODE", "PRODUCTION_PAPER")
    monkeypatch.setattr(dashboard, "MODE", "PRODUCTION_PAPER")
    monkeypatch.setattr(dashboard, "_table", lambda _name: True)
    monkeypatch.setattr(dashboard, "_rows", lambda *_a, **_kw: [{"mode": "PENDING_CUSTODY_SEAL"}])

    assert dashboard._effective_mode() == "PENDING_CUSTODY_SEAL"
    assert "CUSTODIA PENDIENTE" in dashboard.mode_banner()


def test_pending_custody_rejects_report_mutation_despite_stale_paper_observer(tmp_path, monkeypatch):
    data = tmp_path / "data"
    (data / "database").mkdir(parents=True)
    (data / "operation_mode.json").write_text(
        '{"mode":"PENDING_CUSTODY_SEAL"}', encoding="utf-8"
    )
    monkeypatch.setattr(dashboard, "DB_PATH", str(data / "database" / "paper.sqlite"))
    monkeypatch.setattr(dashboard, "CONFIGURED_MODE", "PRODUCTION_PAPER")
    monkeypatch.setattr(dashboard, "MODE", "PRODUCTION_PAPER")
    monkeypatch.setattr(dashboard, "_installed", False)
    monkeypatch.setattr(dashboard, "_rows", lambda *_a, **_kw: [
        {"mode": "PRODUCTION_PAPER", "real_orders_sent": 0}
    ])
    app = FastAPI()
    dashboard.install(app, lambda *_args: None)

    response = TestClient(app).post("/api/reports/action4", content="{}")
    assert response.status_code == 409
    assert "custodia" in response.text.lower()
    assert not (data / "reports").exists()


def test_configuration_card_never_shows_pending_custody_as_green(monkeypatch):
    monkeypatch.setattr(dashboard, "CONFIGURED_MODE", "PENDING_CUSTODY_SEAL")
    monkeypatch.setattr(dashboard, "MODE", "PENDING_CUSTODY_SEAL")
    monkeypatch.setattr(dashboard, "_effective_mode", lambda: "PENDING_CUSTODY_SEAL")
    html = dashboard.config_page()
    assert "card-red'>Modo autoritativo" in html
    assert "card-green'>Modo autoritativo" not in html
