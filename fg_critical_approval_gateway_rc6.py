"""RC6 critical incident approval gateway for PRODUCTION_PAPER.

This process is deliberately separated from the trading observer.  It has no PPI
imports, no order capability and no Docker socket.  Its only external writes are:

* Telegram Bot API: send an incident authorization prompt / acknowledge a tap.
* GitHub Issues API: append an immutable approval/rejection marker to an existing
  POROTA critical incident issue.

A Telegram approval DOES NOT merge, deploy, restart or modify trading code.  It
only records the operator's authorization in GitHub.  The POROTA Health Watch / a
human-controlled GitHub workflow may subsequently prepare a PAPER hotfix and PR.
Deployment remains a separate authorization.

Required GitHub credential: a fine-grained token restricted to this repository
with Metadata:read and Issues:read/write only.  Contents/Actions/Administration
permissions are neither required nor used.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

REPOSITORY = os.getenv("POROTA_HOTFIX_REPOSITORY", "mbalbo2023/Porota-trading").strip()
GITHUB_TOKEN_FILE = os.getenv(
    "POROTA_GITHUB_ISSUE_TOKEN_FILE", "/run/secrets/github_issue_control.token"
).strip()
POLL_SECONDS = max(10, int(os.getenv("POROTA_CRITICAL_APPROVAL_POLL_SECONDS", "30")))
DB_PATH = os.getenv(
    "POROTA_CRITICAL_APPROVAL_DB", "data/paper_v17/critical_approval_rc6.db"
).strip()
INCIDENT_PREFIX = "[POROTA][RED]"
AWAITING_MARKER = "HOTFIX_AUTHORIZATION=AWAITING"
APPROVED_MARKER = "HOTFIX_AUTHORIZATION=AUTHORIZED_TELEGRAM"
REJECTED_MARKER = "HOTFIX_AUTHORIZATION=REJECTED_TELEGRAM"
CALLBACK_APPROVE = "CHFOK"
CALLBACK_REJECT = "CHFNO"


class GatewayError(RuntimeError):
    pass


def _json_request(opener, request, timeout=15):
    try:
        with opener(request, timeout=timeout) as response:
            raw = response.read()
            return json.loads(raw) if raw else {}
    except HTTPError as exc:
        try:
            detail = json.loads(exc.read()).get("message", "")
        except Exception:
            detail = ""
        raise GatewayError(f"HTTP_{exc.code}:{detail[:120]}") from None


def _token_from_file(path: str) -> str:
    token_path = Path(path)
    if not token_path.is_file():
        raise GatewayError("GITHUB_ISSUE_TOKEN_FILE_MISSING")
    token = token_path.read_text(encoding="utf-8").strip()
    if not token:
        raise GatewayError("GITHUB_ISSUE_TOKEN_EMPTY")
    return token


class GithubIssuesClient:
    """Narrow client: only GET issues/comments and POST issue comments."""

    def __init__(self, token: str, repository: str = REPOSITORY, opener=urlopen):
        if "/" not in repository or not token:
            raise ValueError("Invalid GitHub issue-control configuration")
        self.token = token
        self.repository = repository
        self.opener = opener
        self.base = f"https://api.github.com/repos/{repository}"

    def _request(self, method: str, path: str, payload=None):
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(
            self.base + path,
            data=body,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "PorotaCriticalApprovalRC6/1",
                "Content-Type": "application/json",
            },
        )
        return _json_request(self.opener, request)

    def list_open_critical(self):
        query = urlencode({"state": "open", "per_page": 50, "sort": "created", "direction": "desc"})
        rows = self._request("GET", "/issues?" + query)
        result = []
        for row in rows if isinstance(rows, list) else []:
            if row.get("pull_request"):
                continue
            if not str(row.get("title", "")).startswith(INCIDENT_PREFIX):
                continue
            if AWAITING_MARKER not in str(row.get("body") or ""):
                continue
            result.append(row)
        return result

    def get_issue(self, number: int):
        return self._request("GET", f"/issues/{int(number)}")

    def comments(self, number: int):
        rows = self._request("GET", f"/issues/{int(number)}/comments?per_page=100")
        return rows if isinstance(rows, list) else []

    def authorization_state(self, number: int):
        for row in reversed(self.comments(number)):
            body = str(row.get("body") or "")
            if APPROVED_MARKER in body:
                return "APPROVED"
            if REJECTED_MARKER in body:
                return "REJECTED"
        return "AWAITING"

    def add_authorization_comment(self, number: int, approved: bool, sender_hash: str):
        marker = APPROVED_MARKER if approved else REJECTED_MARKER
        decision = "AUTORIZAR HOTFIX PAPER" if approved else "NO AUTORIZAR"
        body = (
            f"{marker}\n\n"
            f"Decision: **{decision}**\n"
            "Origen: Telegram control RC6\n"
            f"Actor hash: `{sender_hash}`\n\n"
            "Esta autorización NO habilita órdenes reales, merge ni deploy. "
            "Sólo autoriza preparar corrección PAPER + tests/CI + PR."
        )
        return self._request("POST", f"/issues/{int(number)}/comments", {"body": body})


class TelegramClient:
    def __init__(self, token: str, chat_id: str, opener=urlopen):
        if not token or not chat_id:
            raise ValueError("Telegram not configured")
        self.token = token
        self.chat_id = str(chat_id)
        self.opener = opener
        self.base = f"https://api.telegram.org/bot{token}"

    def _call(self, method: str, payload=None, *, http_method="POST"):
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(
            self.base + "/" + method,
            data=body,
            method=http_method,
            headers={"Content-Type": "application/json"},
        )
        result = _json_request(self.opener, request)
        if result.get("ok") is not True:
            raise GatewayError("TELEGRAM_INVALID_RESPONSE")
        return result.get("result")

    def send_incident(self, issue):
        number = int(issue["number"])
        title = str(issue.get("title") or "")[:220]
        text = (
            "🔴 POROTA — INCIDENTE CRÍTICO\n\n"
            f"GitHub #{number}: {title}\n\n"
            "El diagnóstico quedó registrado en GitHub. Ninguna corrección se aplica "
            "hasta tu autorización. Órdenes reales permanecen bloqueadas.\n\n"
            "¿Autorizás preparar HOTFIX PAPER + tests/CI + PR?"
        )
        keyboard = {"inline_keyboard": [[
            {"text": "✅ AUTORIZAR HOTFIX PAPER", "callback_data": f"{CALLBACK_APPROVE}:{number}"},
            {"text": "❌ NO AUTORIZAR", "callback_data": f"{CALLBACK_REJECT}:{number}"},
        ]]}
        return self._call("sendMessage", {
            "chat_id": self.chat_id,
            "text": text,
            "reply_markup": keyboard,
        })

    def send_text(self, text: str):
        return self._call("sendMessage", {"chat_id": self.chat_id, "text": text})

    def answer_callback(self, callback_id: str, text: str):
        return self._call("answerCallbackQuery", {
            "callback_query_id": callback_id,
            "text": text[:180],
            "show_alert": False,
        })

    def updates(self, offset: int):
        query = urlencode({"offset": int(offset), "timeout": 0, "allowed_updates": json.dumps(["callback_query"])})
        request = Request(self.base + "/getUpdates?" + query, method="GET")
        result = _json_request(self.opener, request)
        if result.get("ok") is not True:
            raise GatewayError("TELEGRAM_UPDATES_INVALID_RESPONSE")
        return result.get("result") or []


class ApprovalStore:
    def __init__(self, path: str = DB_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS critical_approval_state(
              issue_number INTEGER PRIMARY KEY,
              first_seen_at INTEGER NOT NULL,
              notified_at INTEGER,
              decision TEXT NOT NULL DEFAULT 'AWAITING',
              decided_at INTEGER,
              actor_hash TEXT NOT NULL DEFAULT '',
              github_comment_id INTEGER,
              detail TEXT NOT NULL DEFAULT '')""")
            c.execute("""CREATE TABLE IF NOT EXISTS critical_approval_meta(
              key TEXT PRIMARY KEY, value TEXT NOT NULL)""")

    def connect(self):
        c = sqlite3.connect(str(self.path), timeout=10)
        c.row_factory = sqlite3.Row
        return c

    def ensure_issue(self, number: int):
        with self.connect() as c:
            c.execute("INSERT OR IGNORE INTO critical_approval_state(issue_number,first_seen_at) VALUES(?,?)",
                      (int(number), int(time.time())))

    def was_notified(self, number: int):
        with self.connect() as c:
            row = c.execute("SELECT notified_at FROM critical_approval_state WHERE issue_number=?",
                            (int(number),)).fetchone()
        return bool(row and row[0])

    def mark_notified(self, number: int):
        with self.connect() as c:
            c.execute("UPDATE critical_approval_state SET notified_at=? WHERE issue_number=?",
                      (int(time.time()), int(number)))

    def decide(self, number: int, decision: str, actor_hash: str, comment_id=None):
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT decision FROM critical_approval_state WHERE issue_number=?",
                            (int(number),)).fetchone()
            if row and row[0] not in ("", "AWAITING"):
                return False
            c.execute("INSERT OR IGNORE INTO critical_approval_state(issue_number,first_seen_at) VALUES(?,?)",
                      (int(number), int(time.time())))
            c.execute("""UPDATE critical_approval_state SET decision=?,decided_at=?,actor_hash=?,
              github_comment_id=?,detail='RECORDED_IN_GITHUB' WHERE issue_number=?""",
                      (decision, int(time.time()), actor_hash, comment_id, int(number)))
            return True

    def offset(self):
        with self.connect() as c:
            row = c.execute("SELECT value FROM critical_approval_meta WHERE key='telegram_offset'").fetchone()
        return int(row[0]) if row else 0

    def set_offset(self, value: int):
        with self.connect() as c:
            c.execute("""INSERT INTO critical_approval_meta(key,value) VALUES('telegram_offset',?)
              ON CONFLICT(key) DO UPDATE SET value=excluded.value""", (str(int(value)),))


@dataclass
class CriticalApprovalGateway:
    github: GithubIssuesClient
    telegram: TelegramClient
    store: ApprovalStore

    def scan_once(self):
        sent = 0
        for issue in self.github.list_open_critical():
            number = int(issue["number"])
            self.store.ensure_issue(number)
            if self.github.authorization_state(number) != "AWAITING":
                continue
            if self.store.was_notified(number):
                continue
            self.telegram.send_incident(issue)
            self.store.mark_notified(number)
            sent += 1
        return sent

    def handle_callback(self, update):
        callback = update.get("callback_query") or {}
        data = str(callback.get("data") or "")
        sender = str((callback.get("from") or {}).get("id") or "")
        callback_id = str(callback.get("id") or "")
        if sender != self.telegram.chat_id:
            if callback_id:
                self.telegram.answer_callback(callback_id, "No autorizado")
            return "UNAUTHORIZED"
        try:
            action, raw_number = data.split(":", 1)
            number = int(raw_number)
        except (ValueError, TypeError):
            return "IGNORED"
        if action not in {CALLBACK_APPROVE, CALLBACK_REJECT}:
            return "IGNORED"
        issue = self.github.get_issue(number)
        if not str(issue.get("title") or "").startswith(INCIDENT_PREFIX):
            self.telegram.answer_callback(callback_id, "Incidente no válido")
            return "INVALID_ISSUE"
        if str(issue.get("state") or "").lower() != "open" or AWAITING_MARKER not in str(issue.get("body") or ""):
            self.telegram.answer_callback(callback_id, "Incidente cerrado o no autorizable")
            return "NOT_AWAITING"
        existing = self.github.authorization_state(number)
        if existing != "AWAITING":
            self.telegram.answer_callback(callback_id, f"Ya registrado: {existing}")
            return "ALREADY_DECIDED"
        approved = action == CALLBACK_APPROVE
        actor_hash = hashlib.sha256(sender.encode("utf-8")).hexdigest()[:16]
        comment = self.github.add_authorization_comment(number, approved, actor_hash)
        comment_id = comment.get("id") if isinstance(comment, dict) else None
        decision = "APPROVED" if approved else "REJECTED"
        if not self.store.decide(number, decision, actor_hash, comment_id):
            self.telegram.answer_callback(callback_id, "Decisión ya registrada")
            return "ALREADY_DECIDED"
        self.telegram.answer_callback(callback_id, "Autorización registrada" if approved else "Rechazo registrado")
        if approved:
            self.telegram.send_text(
                f"✅ GitHub #{number}: AUTORIZAR HOTFIX PAPER registrado.\n"
                "Ahora puede prepararse rama + corrección + tests/CI + PR.\n"
                "NO se autorizó merge, deploy ni órdenes reales."
            )
        else:
            self.telegram.send_text(
                f"🛑 GitHub #{number}: NO AUTORIZAR registrado. No se prepara ningún hotfix."
            )
        return decision

    def poll_callbacks_once(self):
        offset = self.store.offset()
        updates = self.telegram.updates(offset)
        handled = 0
        for update in updates:
            next_offset = int(update.get("update_id", -1)) + 1
            if next_offset > offset:
                offset = next_offset
            if update.get("callback_query"):
                self.handle_callback(update)
                handled += 1
        if updates:
            self.store.set_offset(offset)
        return handled


def _validate_runtime():
    if os.getenv("POROTA_CRITICAL_APPROVAL_ENABLED", "false").lower() not in {"1", "true", "yes"}:
        raise GatewayError("CRITICAL_APPROVAL_DISABLED")
    if os.getenv("POROTA_RUNTIME_MODE", "") != "PRODUCTION_PAPER":
        raise GatewayError("RUNTIME_MODE_NOT_PRODUCTION_PAPER")
    # This control-plane process must never receive PPI credentials.
    forbidden = (
        "PPI_API_KEY", "PPI_API_SECRET", "PPI_API_KEY_PROD", "PPI_API_SECRET_PROD",
        "PPI_ACCOUNT_NUMBER", "PPI_PRODUCTION_SECRET_FILE",
    )
    if any(os.getenv(name) for name in forbidden):
        raise GatewayError("PPI_CREDENTIAL_PRESENT_IN_CONTROL_PLANE")


def run():
    _validate_runtime()
    token = _token_from_file(GITHUB_TOKEN_FILE)
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    telegram_chat = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not telegram_token or not telegram_chat:
        raise GatewayError("TELEGRAM_NOT_CONFIGURED")
    gateway = CriticalApprovalGateway(
        GithubIssuesClient(token), TelegramClient(telegram_token, telegram_chat), ApprovalStore()
    )
    while True:
        try:
            gateway.scan_once()
            gateway.poll_callbacks_once()
        except Exception as exc:
            # Never print tokens/URLs; exception classes/codes are enough for service logs.
            print(f"critical-approval-gateway: {type(exc).__name__}: {str(exc)[:160]}", flush=True)
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    run()
