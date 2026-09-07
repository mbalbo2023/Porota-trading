"""RC6 controlled E2E wrapper for the critical-approval gateway.

This module is intentionally narrower than the production control plane.  It can
see and authorize exactly one synthetic issue (configured by
POROTA_CRITICAL_E2E_ONLY_ISSUE, required to be #40 in the authorized workflow).
It has no PPI/order/Docker capability and exists only for the temporary E2E.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import fg_critical_approval_gateway_rc6 as base

E2E_TITLE_MARKER = "[E2E-TEST]"
E2E_BODY_MARKER = "E2E_TEST=true"


def e2e_issue_number() -> int:
    raw = os.getenv("POROTA_CRITICAL_E2E_ONLY_ISSUE", "").strip()
    try:
        number = int(raw)
    except (TypeError, ValueError):
        raise base.GatewayError("E2E_ONLY_ISSUE_REQUIRED") from None
    if number <= 0:
        raise base.GatewayError("E2E_ONLY_ISSUE_REQUIRED")
    return number


def _require_e2e_issue(issue: dict, expected: int) -> dict:
    if int(issue.get("number", -1)) != int(expected):
        raise base.GatewayError("E2E_WRONG_ISSUE")
    title = str(issue.get("title") or "")
    body = str(issue.get("body") or "")
    if not title.startswith(base.INCIDENT_PREFIX) or E2E_TITLE_MARKER not in title:
        raise base.GatewayError("E2E_TITLE_GUARD_FAILED")
    if E2E_BODY_MARKER not in body or "HOTFIX_AUTOMATION_ALLOWED=false" not in body:
        raise base.GatewayError("E2E_BODY_GUARD_FAILED")
    if base.AWAITING_MARKER not in body:
        raise base.GatewayError("E2E_AWAITING_MARKER_MISSING")
    return issue


class E2EGithubIssuesClient(base.GithubIssuesClient):
    def __init__(self, token, expected_issue: int, repository=base.REPOSITORY, opener=base.urlopen):
        super().__init__(token, repository=repository, opener=opener)
        self.expected_issue = int(expected_issue)

    def list_open_critical(self):
        issue = self.get_issue(self.expected_issue)
        if str(issue.get("state") or "").lower() != "open":
            return []
        return [issue]

    def get_issue(self, number):
        if int(number) != self.expected_issue:
            raise base.GatewayError("E2E_WRONG_ISSUE")
        issue = super().get_issue(number)
        return _require_e2e_issue(issue, self.expected_issue)

    def comments(self, number):
        if int(number) != self.expected_issue:
            raise base.GatewayError("E2E_WRONG_ISSUE")
        return super().comments(number)

    def add_authorization_comment(self, number, approved, sender_hash):
        if int(number) != self.expected_issue:
            raise base.GatewayError("E2E_WRONG_ISSUE")
        # Re-read before the only GitHub write in this E2E path.
        _require_e2e_issue(super().get_issue(number), self.expected_issue)
        return super().add_authorization_comment(number, approved, sender_hash)


class E2ECriticalApprovalGateway(base.CriticalApprovalGateway):
    expected_issue: int

    def __init__(self, github, telegram, store, expected_issue: int):
        super().__init__(github, telegram, store)
        self.expected_issue = int(expected_issue)

    def handle_callback(self, update):
        callback = update.get("callback_query") or {}
        data = str(callback.get("data") or "")
        try:
            _, raw_number = data.split(":", 1)
            number = int(raw_number)
        except (ValueError, TypeError):
            return "IGNORED"
        if number != self.expected_issue:
            callback_id = str(callback.get("id") or "")
            if callback_id:
                self.telegram.answer_callback(callback_id, "E2E: incidente fuera de alcance")
            return "E2E_OUT_OF_SCOPE"
        return super().handle_callback(update)


def _read_secret(path_env: str, error_code: str) -> str:
    path = Path(os.getenv(path_env, "").strip())
    if not str(path) or str(path) == "." or not path.is_file():
        raise base.GatewayError(error_code)
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        raise base.GatewayError(error_code)
    return value


def _validate_e2e_runtime():
    base._validate_runtime()
    if os.getenv("POROTA_CRITICAL_E2E", "false").lower() not in {"1", "true", "yes"}:
        raise base.GatewayError("E2E_MODE_DISABLED")
    expected = e2e_issue_number()
    # The 2026-09-07 authorization is explicitly scoped to synthetic Issue #40.
    if expected != 40:
        raise base.GatewayError("E2E_AUTHORIZATION_SCOPE_MISMATCH")
    if os.getenv("POROTA_CRITICAL_TELEGRAM_BOT_TOKEN") or os.getenv("POROTA_CRITICAL_TELEGRAM_CHAT_ID"):
        raise base.GatewayError("E2E_TELEGRAM_MUST_USE_FILES")
    return expected


def run():
    expected = _validate_e2e_runtime()
    github_token = base._token_from_file(base.GITHUB_TOKEN_FILE)
    telegram_token = _read_secret("POROTA_CRITICAL_TELEGRAM_TOKEN_FILE", "E2E_TELEGRAM_TOKEN_FILE_MISSING")
    telegram_chat = _read_secret("POROTA_CRITICAL_TELEGRAM_CHAT_FILE", "E2E_TELEGRAM_CHAT_FILE_MISSING")
    github = E2EGithubIssuesClient(github_token, expected)
    telegram = base.TelegramClient(telegram_token, telegram_chat)
    store = base.ApprovalStore()
    gateway = E2ECriticalApprovalGateway(github, telegram, store, expected)
    while True:
        try:
            gateway.scan_once()
            gateway.poll_callbacks_once()
        except Exception as exc:
            print(f"critical-approval-e2e: {type(exc).__name__}: {str(exc)[:160]}", flush=True)
        time.sleep(base.POLL_SECONDS)


if __name__ == "__main__":
    run()
