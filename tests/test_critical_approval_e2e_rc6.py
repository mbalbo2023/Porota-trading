import pytest

import fg_critical_approval_gateway_rc6 as base
import fh_critical_approval_e2e_rc6 as e2e


def synthetic_issue(number=40):
    return {
        "number": number,
        "title": "[POROTA][RED][E2E-TEST] synthetic",
        "body": (
            "E2E_TEST=true\n"
            "HOTFIX_AUTOMATION_ALLOWED=false\n"
            "HOTFIX_AUTHORIZATION=AWAITING\n"
        ),
        "state": "open",
    }


def test_e2e_issue_number_required(monkeypatch):
    monkeypatch.delenv("POROTA_CRITICAL_E2E_ONLY_ISSUE", raising=False)
    with pytest.raises(base.GatewayError, match="E2E_ONLY_ISSUE_REQUIRED"):
        e2e.e2e_issue_number()


def test_only_synthetic_issue_40_is_accepted():
    assert e2e._require_e2e_issue(synthetic_issue(), 40)["number"] == 40
    with pytest.raises(base.GatewayError, match="E2E_WRONG_ISSUE"):
        e2e._require_e2e_issue(synthetic_issue(41), 40)


def test_real_incident_without_e2e_markers_is_rejected():
    issue = {
        "number": 40,
        "title": "[POROTA][RED] real incident",
        "body": "HOTFIX_AUTHORIZATION=AWAITING",
        "state": "open",
    }
    with pytest.raises(base.GatewayError, match="E2E_TITLE_GUARD_FAILED"):
        e2e._require_e2e_issue(issue, 40)


def test_e2e_body_must_disable_hotfix_automation():
    issue = synthetic_issue()
    issue["body"] = "E2E_TEST=true\nHOTFIX_AUTHORIZATION=AWAITING"
    with pytest.raises(base.GatewayError, match="E2E_BODY_GUARD_FAILED"):
        e2e._require_e2e_issue(issue, 40)


class FakeTelegram:
    def __init__(self):
        self.chat_id = "123"
        self.answers = []

    def answer_callback(self, callback_id, text):
        self.answers.append((callback_id, text))


class NeverGithub:
    def get_issue(self, number):
        raise AssertionError("GitHub must not be touched for out-of-scope callback")


def test_callback_for_other_issue_fails_before_github(tmp_path):
    gateway = e2e.E2ECriticalApprovalGateway(
        NeverGithub(), FakeTelegram(), base.ApprovalStore(str(tmp_path / "e2e.db")), 40
    )
    update = {
        "callback_query": {
            "id": "cb",
            "data": "CHFOK:41",
            "from": {"id": "123"},
        }
    }
    assert gateway.handle_callback(update) == "E2E_OUT_OF_SCOPE"


def test_authorized_e2e_runtime_is_hard_scoped_to_40(monkeypatch):
    monkeypatch.setenv("POROTA_CRITICAL_APPROVAL_ENABLED", "true")
    monkeypatch.setenv("POROTA_RUNTIME_MODE", "PRODUCTION_PAPER")
    monkeypatch.setenv("POROTA_CRITICAL_E2E", "true")
    monkeypatch.setenv("POROTA_CRITICAL_E2E_ONLY_ISSUE", "41")
    for name in (
        "PPI_API_KEY", "PPI_API_SECRET", "PPI_API_KEY_PROD", "PPI_API_SECRET_PROD",
        "PPI_ACCOUNT_NUMBER", "PPI_PRODUCTION_SECRET_FILE", "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID", "POROTA_CRITICAL_TELEGRAM_BOT_TOKEN",
        "POROTA_CRITICAL_TELEGRAM_CHAT_ID",
    ):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(base.GatewayError, match="E2E_AUTHORIZATION_SCOPE_MISMATCH"):
        e2e._validate_e2e_runtime()


def test_e2e_refuses_inline_telegram_secrets(monkeypatch):
    monkeypatch.setenv("POROTA_CRITICAL_APPROVAL_ENABLED", "true")
    monkeypatch.setenv("POROTA_RUNTIME_MODE", "PRODUCTION_PAPER")
    monkeypatch.setenv("POROTA_CRITICAL_E2E", "true")
    monkeypatch.setenv("POROTA_CRITICAL_E2E_ONLY_ISSUE", "40")
    monkeypatch.setenv("POROTA_CRITICAL_TELEGRAM_BOT_TOKEN", "do-not-inline")
    for name in (
        "PPI_API_KEY", "PPI_API_SECRET", "PPI_API_KEY_PROD", "PPI_API_SECRET_PROD",
        "PPI_ACCOUNT_NUMBER", "PPI_PRODUCTION_SECRET_FILE", "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID", "POROTA_CRITICAL_TELEGRAM_CHAT_ID",
    ):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(base.GatewayError, match="E2E_TELEGRAM_MUST_USE_FILES"):
        e2e._validate_e2e_runtime()
