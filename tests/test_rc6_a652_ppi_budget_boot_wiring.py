"""OFFHOST boot-wiring regressions: zero provider calls, no budget activation."""

from datetime import datetime, timezone

import pytest

import porota_mode_manager as manager
import rc6_ppi_global_budget as budget_module
from cg_paper_workspace import artifact_root
from rc6_ppi_global_budget import (
    BudgetBackpressure,
    budget_from_environment,
    static_shared_capacity_state,
)


def _fixture_boot(tmp_path, monkeypatch, config):
    monkeypatch.setattr(manager, "DATA", tmp_path / "data")
    (manager.DATA / "diagnosticos").mkdir(parents=True)
    monkeypatch.setattr(manager, "env_file", lambda: dict(config))
    return manager.observer_runtime_env()


def test_observer_receives_explicit_selectors_only_and_dashboard_does_not(tmp_path, monkeypatch):
    paths = {
        "POROTA_CAPACITY_POLICY_PATH": "/app/ops/policy/rc6-dynamic-capacity-v1.json",
        "POROTA_CAPACITY_REPORT_PATH": "/app/data/ppi-capacity/report.json",
        "POROTA_CAPACITY_RECOMMENDATION_PATH": "/app/data/ppi-capacity/recommendation.json",
        "POROTA_CAPACITY_APPROVAL_PATH": "/app/operator/ppi-capacity/approval.json",
        "POROTA_CAPACITY_SHADOW_PATH": "/app/data/ppi-capacity/latest.json.gz",
        "POROTA_PPI_STATIC_SHARED_AUTHORITY_PATH": "/app/operator/ppi-capacity/shared-v2.json",
    }
    boot = _fixture_boot(tmp_path, monkeypatch, {
        **paths, "POROTA_DYNAMIC_CAPACITY_MODE": "approved",
        "PPI_API_KEY": "never-in-observer-file",
        "POROTA_BUILD_SHA": "untrusted-operator-override",
    })
    text = boot.read_text(encoding="utf-8")
    assert "POROTA_DYNAMIC_CAPACITY_MODE=APPROVED\n" in text
    for key, value in paths.items():
        assert f"{key}={value}\n" in text
    assert "never-in-observer-file" not in text
    assert "untrusted-operator-override" not in text
    assert boot.stat().st_mode & 0o777 == 0o600

    # Independent dashboard environment must never inherit broker capacity
    # selectors, even from a stale dashboard_preview_v1633.env file.
    preview = manager.DATA / "diagnosticos" / "dashboard_preview_v1633.env"
    preview.write_text("DASHBOARD_ACCESS_TOKEN=synthetic\n" +
        "POROTA_DYNAMIC_CAPACITY_MODE=APPROVED\n" +
        "POROTA_PPI_STATIC_SHARED_AUTHORITY_PATH=/app/operator/ppi-capacity/shared-v2.json\n" +
        "POROTA_CAPACITY_APPROVAL_PATH=/app/operator/ppi-capacity/approval.json\n" +
        "PPI_API_KEY=never-in-dashboard-file\n", encoding="utf-8")
    dashboard = manager.dashboard_env("PRODUCTION_PAPER").read_text(encoding="utf-8")
    assert "DASHBOARD_ACCESS_TOKEN=synthetic" in dashboard
    assert "never-in-dashboard-file" not in dashboard
    assert all(key + "=" not in dashboard for key in manager.PPI_BUDGET_OBSERVER_SELECTORS)


def test_no_explicit_authority_preserves_absent_sidecar(tmp_path, monkeypatch):
    boot = _fixture_boot(tmp_path, monkeypatch, {})
    text = boot.read_text(encoding="utf-8")
    assert all(key + "=" not in text for key in manager.PPI_BUDGET_OBSERVER_SELECTORS)
    for key in manager.PPI_BUDGET_OBSERVER_SELECTORS:
        monkeypatch.delenv(key, raising=False)
    database = tmp_path / "paper_v17" / "observer_v17.db"
    sidecar = artifact_root(database) / "ppi-budget/global.sqlite"
    assert budget_from_environment(database) is None
    assert not sidecar.exists() and not sidecar.parent.exists()


def test_static_selector_cannot_create_authority_without_review_pin(tmp_path, monkeypatch):
    # Freeze the missing-reviewer scenario even if a future independent
    # approval pins a real signer; this fixture never creates a signing key.
    monkeypatch.setattr(budget_module, "PINNED_STATIC_SHARED_SIGNER_KEY_SHA256", None)
    boot = _fixture_boot(tmp_path, monkeypatch, {
        "POROTA_PPI_STATIC_SHARED_AUTHORITY_PATH": "/app/operator/ppi-capacity/shared-v2.json"
    })
    assert "POROTA_PPI_STATIC_SHARED_AUTHORITY_PATH=" in boot.read_text()
    database = tmp_path / "paper_v17" / "observer_v17.db"
    sidecar = artifact_root(database) / "ppi-budget/global.sqlite"
    with pytest.raises(BudgetBackpressure, match="PPI_STATIC_SHARED_SIGNER_UNPINNED"):
        static_shared_capacity_state(database, {
            "POROTA_PPI_STATIC_SHARED_AUTHORITY_PATH": "/app/operator/ppi-capacity/shared-v2.json"
        }, as_of=datetime.now(timezone.utc))
    assert not sidecar.exists() and not sidecar.parent.exists()


@pytest.mark.parametrize("config, reason", [
    ({"POROTA_DYNAMIC_CAPACITY_MODE": "LIVE"}, "RC6_PPI_BUDGET_MODE_INVALID"),
    ({"POROTA_CAPACITY_APPROVAL_PATH": "./approval.json"}, "RC6_PPI_BUDGET_PATH_NOT_ABSOLUTE"),
    ({"POROTA_PPI_STATIC_SHARED_AUTHORITY_PATH": "/app/operator/ok\nPPI_API_KEY=secret"},
     "RC6_PPI_BUDGET_SELECTOR_INVALID"),
    ({"POROTA_CAPACITY_POLICY_PATH": "/app/data/ok\x00bad"},
     "RC6_PPI_BUDGET_SELECTOR_INVALID"),
])
def test_invalid_capacity_selector_is_rejected_before_observer_stop(tmp_path, monkeypatch, config, reason):
    monkeypatch.setattr(manager, "DATA", tmp_path / "data")
    (manager.DATA / "diagnosticos").mkdir(parents=True)
    monkeypatch.setattr(manager, "env_file", lambda: config)
    monkeypatch.setattr(manager, "simulation_image", lambda: "synthetic-image")

    def cannot_stop():
        raise AssertionError("healthy observer would have been stopped")

    monkeypatch.setattr(manager, "stop_engines", cannot_stop)
    with pytest.raises(RuntimeError, match=reason):
        manager.simulation()
    assert not (manager.DATA / "diagnosticos" / "observer_runtime_v17.env").exists()
