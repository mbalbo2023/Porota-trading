"""RC6 host-side GitHub Issues capability broker.

Runs on the Droplet as the existing non-root POROTA admin user and uses that
user's authenticated `gh` session.  The broad underlying gh credential is NEVER
mounted into the critical-approval container.  Instead, this broker exposes a
local Unix socket with a deliberately tiny, fixed capability surface:

- health
- list_open_critical
- get_issue(number)
- comments(number)
- add_authorization_comment(number, approved, sender_hash)

The repository is hard-coded and callers cannot supply arbitrary GitHub paths,
HTTP methods, request bodies, workflow operations, contents operations or shell
commands.  Authorization comments are constructed by this broker itself.
"""
from __future__ import annotations

import json
import os
import re
import socketserver
import subprocess
from pathlib import Path
from typing import Any, Callable

REPOSITORY = "mbalbo2023/Porota-trading"
SOCKET_PATH = os.getenv(
    "POROTA_CRITICAL_GITHUB_SOCKET",
    "/run/porota-critical-approval-rc6/github.sock",
)
INCIDENT_PREFIX = "[POROTA][RED]"
AWAITING_MARKER = "HOTFIX_AUTHORIZATION=AWAITING"
APPROVED_MARKER = "HOTFIX_AUTHORIZATION=AUTHORIZED_TELEGRAM"
REJECTED_MARKER = "HOTFIX_AUTHORIZATION=REJECTED_TELEGRAM"
HASH_RE = re.compile(r"^[0-9a-f]{16}$")
MAX_REQUEST = 65536


class BrokerError(RuntimeError):
    pass


def _gh_api(method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
    if method not in {"GET", "POST"}:
        raise BrokerError("METHOD_NOT_ALLOWED")
    args = ["gh", "api", "--method", method, path]
    stdin = None
    if payload is not None:
        args += ["--input", "-"]
        stdin = json.dumps(payload, separators=(",", ":"))
    try:
        cp = subprocess.run(
            args,
            input=stdin,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BrokerError(f"GH_EXEC_FAILED:{type(exc).__name__}") from None
    if cp.returncode != 0:
        detail = " ".join((cp.stderr or "").strip().split())[:160]
        raise BrokerError(f"GH_API_FAILED:{detail}")
    raw = (cp.stdout or "").strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise BrokerError("GH_API_INVALID_JSON") from None


def _issue_number(value: Any) -> int:
    if isinstance(value, bool):
        raise BrokerError("INVALID_ISSUE_NUMBER")
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise BrokerError("INVALID_ISSUE_NUMBER") from None
    if number < 1 or number > 2_000_000_000:
        raise BrokerError("INVALID_ISSUE_NUMBER")
    return number


def _authorization_state(rows: list[dict[str, Any]]) -> str:
    for row in reversed(rows):
        body = str(row.get("body") or "")
        if APPROVED_MARKER in body:
            return "APPROVED"
        if REJECTED_MARKER in body:
            return "REJECTED"
    return "AWAITING"


class GithubIssueBroker:
    def __init__(self, api: Callable[[str, str, dict[str, Any] | None], Any] = _gh_api):
        self.api = api
        self.base = f"repos/{REPOSITORY}"

    def _issue(self, number: int) -> dict[str, Any]:
        row = self.api("GET", f"{self.base}/issues/{number}", None)
        if not isinstance(row, dict):
            raise BrokerError("INVALID_ISSUE_RESPONSE")
        return row

    def _comments(self, number: int) -> list[dict[str, Any]]:
        rows = self.api("GET", f"{self.base}/issues/{number}/comments?per_page=100", None)
        if not isinstance(rows, list):
            raise BrokerError("INVALID_COMMENTS_RESPONSE")
        return [r for r in rows if isinstance(r, dict)]

    def dispatch(self, request: dict[str, Any]) -> Any:
        op = str(request.get("op") or "")
        if op == "health":
            return {"status": "ok", "repository": REPOSITORY, "capability": "issues_only"}

        if op == "list_open_critical":
            rows = self.api(
                "GET",
                f"{self.base}/issues?state=open&per_page=50&sort=created&direction=desc",
                None,
            )
            if not isinstance(rows, list):
                raise BrokerError("INVALID_ISSUES_RESPONSE")
            result = []
            for row in rows:
                if not isinstance(row, dict) or row.get("pull_request"):
                    continue
                title = str(row.get("title") or "")
                body = str(row.get("body") or "")
                if title.startswith(INCIDENT_PREFIX) and AWAITING_MARKER in body:
                    result.append({
                        "number": int(row["number"]),
                        "title": title,
                        "body": body,
                        "state": str(row.get("state") or ""),
                    })
            return result

        if op == "get_issue":
            number = _issue_number(request.get("number"))
            row = self._issue(number)
            return {
                "number": int(row.get("number") or number),
                "title": str(row.get("title") or ""),
                "body": str(row.get("body") or ""),
                "state": str(row.get("state") or ""),
            }

        if op == "comments":
            number = _issue_number(request.get("number"))
            return self._comments(number)

        if op == "add_authorization_comment":
            number = _issue_number(request.get("number"))
            approved = request.get("approved")
            sender_hash = str(request.get("sender_hash") or "")
            if not isinstance(approved, bool):
                raise BrokerError("INVALID_DECISION")
            if not HASH_RE.fullmatch(sender_hash):
                raise BrokerError("INVALID_SENDER_HASH")

            issue = self._issue(number)
            title = str(issue.get("title") or "")
            body = str(issue.get("body") or "")
            state = str(issue.get("state") or "").lower()
            if not title.startswith(INCIDENT_PREFIX):
                raise BrokerError("ISSUE_NOT_CRITICAL")
            if state != "open" or AWAITING_MARKER not in body:
                raise BrokerError("ISSUE_NOT_AWAITING")
            if _authorization_state(self._comments(number)) != "AWAITING":
                raise BrokerError("ISSUE_ALREADY_DECIDED")

            marker = APPROVED_MARKER if approved else REJECTED_MARKER
            decision = "AUTORIZAR HOTFIX PAPER" if approved else "NO AUTORIZAR"
            comment = (
                f"{marker}\n\n"
                f"Decision: **{decision}**\n"
                "Origen: Telegram control RC6\n"
                f"Actor hash: `{sender_hash}`\n\n"
                "Esta autorización NO habilita órdenes reales, merge ni deploy. "
                "Sólo autoriza preparar corrección PAPER + tests/CI + PR."
            )
            result = self.api(
                "POST",
                f"{self.base}/issues/{number}/comments",
                {"body": comment},
            )
            return {"id": int(result.get("id") or 0)} if isinstance(result, dict) else {"id": 0}

        raise BrokerError("OPERATION_NOT_ALLOWED")


BROKER = GithubIssueBroker()


class Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        raw = self.rfile.readline(MAX_REQUEST + 1)
        if not raw or len(raw) > MAX_REQUEST:
            return
        try:
            request = json.loads(raw.decode("utf-8"))
            if not isinstance(request, dict):
                raise BrokerError("REQUEST_NOT_OBJECT")
            result = BROKER.dispatch(request)
            response = {"ok": True, "result": result}
        except Exception as exc:
            response = {"ok": False, "error": f"{type(exc).__name__}:{str(exc)[:180]}"}
        self.wfile.write((json.dumps(response, separators=(",", ":")) + "\n").encode("utf-8"))


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True


def main() -> None:
    auth = subprocess.run(
        ["gh", "auth", "status", "-h", "github.com"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=10,
        check=False,
    )
    if auth.returncode != 0:
        raise SystemExit("GH_AUTH_NOT_AVAILABLE")
    path = Path(SOCKET_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_socket():
        path.unlink()
    with Server(str(path), Handler) as server:
        os.chmod(path, 0o666)
        print(f"critical-github-broker: READY socket={path} capability=issues_only", flush=True)
        server.serve_forever(poll_interval=0.5)


if __name__ == "__main__":
    main()
