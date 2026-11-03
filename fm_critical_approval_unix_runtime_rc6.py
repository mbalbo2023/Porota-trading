"""RC6 permanent critical-approval runtime using a local Unix capability broker.

The container receives no GitHub token. GitHub access is delegated to the
host-side `fn_critical_github_proxy_rc6.py` through a Unix socket that exposes
only the fixed Issues operations needed by the approval gateway. Every broker
request carries a random local capability key mounted read-only into this
container; the broad host `gh` credential never enters the container.

Telegram uses the existing canonical POROTA bot, copied into control-plane
secret files. This process is allowed to consume `getUpdates` only when the
deployment preflight has proven it is the single callback consumer.
"""
from __future__ import annotations

import json
import os
import socket
import time
from pathlib import Path
from typing import Any

from fg_critical_approval_gateway_rc6 import (
    APPROVED_MARKER,
    REJECTED_MARKER,
    ApprovalStore,
    CriticalApprovalGateway,
    GatewayError,
    TelegramClient,
)

SOCKET_PATH = os.getenv(
    "POROTA_CRITICAL_GITHUB_SOCKET",
    "/run/control/github.sock",
).strip()
BROKER_TOKEN_FILE = os.getenv(
    "POROTA_CRITICAL_BROKER_TOKEN_FILE",
    "/run/secrets/broker_capability.token",
).strip()
TOKEN_FILE = os.getenv(
    "POROTA_CRITICAL_TELEGRAM_TOKEN_FILE",
    "/run/secrets/critical_telegram.token",
).strip()
CHAT_FILE = os.getenv(
    "POROTA_CRITICAL_TELEGRAM_CHAT_FILE",
    "/run/secrets/critical_telegram.chat",
).strip()
POLL_SECONDS = max(10, int(os.getenv("POROTA_CRITICAL_APPROVAL_POLL_SECONDS", "30")))


def _read_secret(path: str, label: str, min_len: int = 1) -> str:
    p = Path(path)
    if not p.is_file():
        raise GatewayError(f"{label}_FILE_MISSING")
    value = p.read_text(encoding="utf-8").strip()
    if len(value) < min_len:
        raise GatewayError(f"{label}_INVALID")
    return value


def _validate_runtime() -> None:
    if os.getenv("POROTA_CRITICAL_APPROVAL_ENABLED", "false").lower() not in {"1", "true", "yes"}:
        raise GatewayError("CRITICAL_APPROVAL_DISABLED")
    if os.getenv("POROTA_RUNTIME_MODE", "") != "PRODUCTION_PAPER":
        raise GatewayError("RUNTIME_MODE_NOT_PRODUCTION_PAPER")
    if os.getenv("POROTA_TELEGRAM_SINGLE_CONSUMER_ENFORCED", "false").lower() not in {"1", "true", "yes"}:
        raise GatewayError("TELEGRAM_SINGLE_CONSUMER_NOT_ENFORCED")
    forbidden = (
        "PPI_API_KEY", "PPI_API_SECRET", "PPI_API_KEY_PROD", "PPI_API_SECRET_PROD",
        "PPI_ACCOUNT_NUMBER", "PPI_PRODUCTION_SECRET_FILE", "GITHUB_TOKEN", "GH_TOKEN",
        "POROTA_GITHUB_ISSUE_CONTROL_TOKEN", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
    )
    if any(os.getenv(name) for name in forbidden):
        raise GatewayError("FORBIDDEN_CREDENTIAL_PRESENT_IN_CONTROL_PLANE")


class UnixGithubIssuesClient:
    def __init__(self, socket_path: str = SOCKET_PATH, capability_token: str | None = None):
        self.socket_path = socket_path
        self.capability_token = capability_token or _read_secret(
            BROKER_TOKEN_FILE, "BROKER_CAPABILITY", min_len=32
        )

    def _call(self, op: str, **kwargs: Any) -> Any:
        payload = {"op": op, "capability_token": self.capability_token, **kwargs}
        raw = (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")
        if len(raw) > 65536:
            raise GatewayError("BROKER_REQUEST_TOO_LARGE")
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(15)
                s.connect(self.socket_path)
                s.sendall(raw)
                chunks = bytearray()
                while b"\n" not in chunks:
                    part = s.recv(65536)
                    if not part:
                        break
                    chunks.extend(part)
                    if len(chunks) > 1_000_000:
                        raise GatewayError("BROKER_RESPONSE_TOO_LARGE")
        except (OSError, TimeoutError) as exc:
            raise GatewayError(f"BROKER_UNAVAILABLE:{type(exc).__name__}") from None
        if not chunks:
            raise GatewayError("BROKER_EMPTY_RESPONSE")
        try:
            response = json.loads(bytes(chunks).split(b"\n", 1)[0].decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise GatewayError("BROKER_INVALID_RESPONSE") from None
        if not isinstance(response, dict) or response.get("ok") is not True:
            detail = str(response.get("error") if isinstance(response, dict) else "")[:180]
            raise GatewayError(f"BROKER_ERROR:{detail}")
        return response.get("result")

    def health(self) -> dict[str, Any]:
        result = self._call("health")
        return result if isinstance(result, dict) else {}

    def list_open_critical(self) -> list[dict[str, Any]]:
        result = self._call("list_open_critical")
        return result if isinstance(result, list) else []

    def get_issue(self, number: int) -> dict[str, Any]:
        result = self._call("get_issue", number=int(number))
        return result if isinstance(result, dict) else {}

    def comments(self, number: int) -> list[dict[str, Any]]:
        result = self._call("comments", number=int(number))
        return result if isinstance(result, list) else []

    def authorization_state(self, number: int) -> str:
        for row in reversed(self.comments(number)):
            body = str(row.get("body") or "")
            if APPROVED_MARKER in body:
                return "APPROVED"
            if REJECTED_MARKER in body:
                return "REJECTED"
        return "AWAITING"

    def add_authorization_comment(self, number: int, approved: bool, sender_hash: str) -> dict[str, Any]:
        result = self._call(
            "add_authorization_comment",
            number=int(number),
            approved=bool(approved),
            sender_hash=str(sender_hash),
        )
        return result if isinstance(result, dict) else {}


def run() -> None:
    _validate_runtime()
    telegram_token = _read_secret(TOKEN_FILE, "CRITICAL_TELEGRAM_TOKEN")
    telegram_chat = _read_secret(CHAT_FILE, "CRITICAL_TELEGRAM_CHAT")
    github = UnixGithubIssuesClient()
    health = github.health()
    if health.get("status") != "ok" or health.get("capability") != "issues_only":
        raise GatewayError("BROKER_HEALTH_INVALID")
    gateway = CriticalApprovalGateway(
        github,
        TelegramClient(telegram_token, telegram_chat),
        ApprovalStore(),
    )
    print("critical-approval-runtime: READY mode=PRODUCTION_PAPER github=unix-issues-only+capability-key telegram=canonical-single-consumer", flush=True)
    while True:
        try:
            gateway.scan_once()
            gateway.poll_callbacks_once()
        except Exception as exc:
            print(f"critical-approval-runtime: {type(exc).__name__}: {str(exc)[:160]}", flush=True)
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    run()
