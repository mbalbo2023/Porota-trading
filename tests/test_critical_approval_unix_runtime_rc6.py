import pytest

import fm_critical_approval_unix_runtime_rc6 as mod


def clean(monkeypatch):
    for name in (
        "PPI_API_KEY", "PPI_API_SECRET", "PPI_API_KEY_PROD", "PPI_API_SECRET_PROD",
        "PPI_ACCOUNT_NUMBER", "PPI_PRODUCTION_SECRET_FILE", "GITHUB_TOKEN", "GH_TOKEN",
        "POROTA_GITHUB_ISSUE_CONTROL_TOKEN", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("POROTA_CRITICAL_APPROVAL_ENABLED", "true")
    monkeypatch.setenv("POROTA_RUNTIME_MODE", "PRODUCTION_PAPER")
    monkeypatch.setenv("POROTA_TELEGRAM_SINGLE_CONSUMER_ENFORCED", "true")


def test_runtime_accepts_safe_paper_control_plane(monkeypatch):
    clean(monkeypatch)
    mod._validate_runtime()


def test_runtime_requires_single_consumer_enforcement(monkeypatch):
    clean(monkeypatch)
    monkeypatch.setenv("POROTA_TELEGRAM_SINGLE_CONSUMER_ENFORCED", "false")
    with pytest.raises(mod.GatewayError, match="TELEGRAM_SINGLE_CONSUMER_NOT_ENFORCED"):
        mod._validate_runtime()


def test_runtime_refuses_github_token_inside_container(monkeypatch):
    clean(monkeypatch)
    monkeypatch.setenv("GH_TOKEN", "broad-token-must-stay-on-host")
    with pytest.raises(mod.GatewayError, match="FORBIDDEN_CREDENTIAL_PRESENT"):
        mod._validate_runtime()


def test_runtime_refuses_ppi_credentials(monkeypatch):
    clean(monkeypatch)
    monkeypatch.setenv("PPI_API_KEY", "forbidden")
    with pytest.raises(mod.GatewayError, match="FORBIDDEN_CREDENTIAL_PRESENT"):
        mod._validate_runtime()


def test_runtime_refuses_generic_observer_telegram_env(monkeypatch):
    clean(monkeypatch)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "forbidden-generic")
    with pytest.raises(mod.GatewayError, match="FORBIDDEN_CREDENTIAL_PRESENT"):
        mod._validate_runtime()
