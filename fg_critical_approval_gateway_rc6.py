"""RC6 fail-closed critical incident approval gateway for PRODUCTION_PAPER.

Control-plane only: no PPI imports, no order capability, no Docker socket and no
runtime mutation. It reads POROTA critical GitHub issues, sends an authorization
prompt through a DEDICATED Telegram bot, and writes only an approval/rejection
comment back to the issue.

The dedicated bot requirement is a safety invariant: POROTA's trading runtime
already has one canonical Telegram getUpdates consumer. Reusing that bot token
here would create competing offsets and could lose callbacks. Therefore this
process accepts only POROTA_CRITICAL_TELEGRAM_* variables and must never receive
the observer's TELEGRAM_BOT_TOKEN.

Approval scope is deliberately narrow: AUTORIZAR HOTFIX PAPER means permission
to prepare a branch + correction + tests/CI + PR. It does NOT authorize merge,
deploy, restart, real orders, credentials changes or release promotion.

GitHub credential: fine-grained token limited to this repository with
Metadata:read and Issues:read/write only. Contents/Actions/Admin are not needed.
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
GITHUB_TOKEN_FILE = os.getenv("POROTA_GITHUB_ISSUE_TOKEN_FILE", "/run/secrets/github_issue_control.token").strip()
POLL_SECONDS = max(10, int(os.getenv("POROTA_CRITICAL_APPROVAL_POLL_SECONDS", "30")))
DB_PATH = os.getenv("POROTA_CRITICAL_APPROVAL_DB", "data/paper_v17/critical_approval_rc6.db").strip()
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


def _token_from_file(path):
    p = Path(path)
    if not p.is_file():
        raise GatewayError("GITHUB_ISSUE_TOKEN_FILE_MISSING")
    token = p.read_text(encoding="utf-8").strip()
    if not token:
        raise GatewayError("GITHUB_ISSUE_TOKEN_EMPTY")
    return token


class GithubIssuesClient:
    """Narrow API client: GET issues/comments and POST issue comments only."""
    def __init__(self, token, repository=REPOSITORY, opener=urlopen):
        if not token or "/" not in repository:
            raise ValueError("Invalid GitHub issue-control configuration")
        self.token, self.repository, self.opener = token, repository, opener
        self.base = f"https://api.github.com/repos/{repository}"

    def _request(self, method, path, payload=None):
        body = None if payload is None else json.dumps(payload).encode()
        req = Request(self.base + path, data=body, method=method, headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "PorotaCriticalApprovalRC6/1",
            "Content-Type": "application/json",
        })
        return _json_request(self.opener, req)

    def list_open_critical(self):
        q = urlencode({"state":"open", "per_page":50, "sort":"created", "direction":"desc"})
        rows = self._request("GET", "/issues?" + q)
        return [r for r in rows if not r.get("pull_request")
                and str(r.get("title", "")).startswith(INCIDENT_PREFIX)
                and AWAITING_MARKER in str(r.get("body") or "")]

    def get_issue(self, number):
        return self._request("GET", f"/issues/{int(number)}")

    def comments(self, number):
        rows = self._request("GET", f"/issues/{int(number)}/comments?per_page=100")
        return rows if isinstance(rows, list) else []

    def authorization_state(self, number):
        for row in reversed(self.comments(number)):
            body = str(row.get("body") or "")
            if APPROVED_MARKER in body:
                return "APPROVED"
            if REJECTED_MARKER in body:
                return "REJECTED"
        return "AWAITING"

    def add_authorization_comment(self, number, approved, sender_hash):
        marker = APPROVED_MARKER if approved else REJECTED_MARKER
        decision = "AUTORIZAR HOTFIX PAPER" if approved else "NO AUTORIZAR"
        body = (f"{marker}\n\nDecision: **{decision}**\nOrigen: Telegram control RC6\n"
                f"Actor hash: `{sender_hash}`\n\n"
                "Esta autorización NO habilita órdenes reales, merge ni deploy. "
                "Sólo autoriza preparar corrección PAPER + tests/CI + PR.")
        return self._request("POST", f"/issues/{int(number)}/comments", {"body": body})


class TelegramClient:
    def __init__(self, token, chat_id, opener=urlopen):
        if not token or not chat_id:
            raise ValueError("Dedicated Telegram not configured")
        self.token, self.chat_id, self.opener = token, str(chat_id), opener
        self.base = f"https://api.telegram.org/bot{token}"

    def _call(self, method, payload=None, http_method="POST"):
        body = None if payload is None else json.dumps(payload).encode()
        req = Request(self.base + "/" + method, data=body, method=http_method,
                      headers={"Content-Type":"application/json"})
        result = _json_request(self.opener, req)
        if result.get("ok") is not True:
            raise GatewayError("TELEGRAM_INVALID_RESPONSE")
        return result.get("result")

    def send_incident(self, issue):
        n = int(issue["number"])
        text = ("🔴 POROTA — INCIDENTE CRÍTICO\n\n"
                f"GitHub #{n}: {str(issue.get('title') or '')[:220]}\n\n"
                "El diagnóstico quedó registrado en GitHub. Ninguna corrección se aplica "
                "hasta tu autorización. Órdenes reales permanecen bloqueadas.\n\n"
                "¿Autorizás preparar HOTFIX PAPER + tests/CI + PR?")
        keyboard = {"inline_keyboard":[[
            {"text":"✅ AUTORIZAR HOTFIX PAPER", "callback_data":f"{CALLBACK_APPROVE}:{n}"},
            {"text":"❌ NO AUTORIZAR", "callback_data":f"{CALLBACK_REJECT}:{n}"},
        ]]}
        return self._call("sendMessage", {"chat_id":self.chat_id,"text":text,"reply_markup":keyboard})

    def send_text(self, text):
        return self._call("sendMessage", {"chat_id":self.chat_id,"text":text})

    def answer_callback(self, callback_id, text):
        return self._call("answerCallbackQuery", {"callback_query_id":callback_id,
                                                   "text":text[:180],"show_alert":False})

    def updates(self, offset):
        q = urlencode({"offset":int(offset),"timeout":0,
                       "allowed_updates":json.dumps(["callback_query"])})
        req = Request(self.base + "/getUpdates?" + q, method="GET")
        result = _json_request(self.opener, req)
        if result.get("ok") is not True:
            raise GatewayError("TELEGRAM_UPDATES_INVALID_RESPONSE")
        return result.get("result") or []


class ApprovalStore:
    def __init__(self, path=DB_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS critical_approval_state(
              issue_number INTEGER PRIMARY KEY, first_seen_at INTEGER NOT NULL,
              notified_at INTEGER, decision TEXT NOT NULL DEFAULT 'AWAITING',
              decided_at INTEGER, actor_hash TEXT NOT NULL DEFAULT '',
              github_comment_id INTEGER, detail TEXT NOT NULL DEFAULT '')""")
            c.execute("""CREATE TABLE IF NOT EXISTS critical_approval_meta(
              key TEXT PRIMARY KEY, value TEXT NOT NULL)""")

    def connect(self):
        c = sqlite3.connect(str(self.path), timeout=10)
        c.row_factory = sqlite3.Row
        return c

    def ensure_issue(self, number):
        with self.connect() as c:
            c.execute("INSERT OR IGNORE INTO critical_approval_state(issue_number,first_seen_at) VALUES(?,?)",
                      (int(number), int(time.time())))

    def was_notified(self, number):
        with self.connect() as c:
            r = c.execute("SELECT notified_at FROM critical_approval_state WHERE issue_number=?",
                          (int(number),)).fetchone()
        return bool(r and r[0])

    def mark_notified(self, number):
        with self.connect() as c:
            c.execute("UPDATE critical_approval_state SET notified_at=? WHERE issue_number=?",
                      (int(time.time()), int(number)))

    def decide(self, number, decision, actor_hash, comment_id=None):
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            r = c.execute("SELECT decision FROM critical_approval_state WHERE issue_number=?",
                          (int(number),)).fetchone()
            if r and r[0] not in ("", "AWAITING"):
                return False
            c.execute("INSERT OR IGNORE INTO critical_approval_state(issue_number,first_seen_at) VALUES(?,?)",
                      (int(number), int(time.time())))
            c.execute("""UPDATE critical_approval_state SET decision=?,decided_at=?,actor_hash=?,
              github_comment_id=?,detail='RECORDED_IN_GITHUB' WHERE issue_number=?""",
                      (decision,int(time.time()),actor_hash,comment_id,int(number)))
            return True

    def offset(self):
        with self.connect() as c:
            r = c.execute("SELECT value FROM critical_approval_meta WHERE key='telegram_offset'").fetchone()
        return int(r[0]) if r else 0

    def set_offset(self, value):
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
            n = int(issue["number"]); self.store.ensure_issue(n)
            if self.github.authorization_state(n) != "AWAITING" or self.store.was_notified(n):
                continue
            self.telegram.send_incident(issue); self.store.mark_notified(n); sent += 1
        return sent

    def handle_callback(self, update):
        cb = update.get("callback_query") or {}
        data = str(cb.get("data") or "")
        sender = str((cb.get("from") or {}).get("id") or "")
        callback_id = str(cb.get("id") or "")
        if sender != self.telegram.chat_id:
            if callback_id: self.telegram.answer_callback(callback_id, "No autorizado")
            return "UNAUTHORIZED"
        try:
            action, raw = data.split(":",1); number = int(raw)
        except (ValueError, TypeError):
            return "IGNORED"
        if action not in {CALLBACK_APPROVE,CALLBACK_REJECT}:
            return "IGNORED"
        issue = self.github.get_issue(number)
        if not str(issue.get("title") or "").startswith(INCIDENT_PREFIX):
            self.telegram.answer_callback(callback_id,"Incidente no válido"); return "INVALID_ISSUE"
        if str(issue.get("state") or "").lower() != "open" or AWAITING_MARKER not in str(issue.get("body") or ""):
            self.telegram.answer_callback(callback_id,"Incidente cerrado o no autorizable"); return "NOT_AWAITING"
        existing = self.github.authorization_state(number)
        if existing != "AWAITING":
            self.telegram.answer_callback(callback_id,f"Ya registrado: {existing}"); return "ALREADY_DECIDED"
        approved = action == CALLBACK_APPROVE
        actor_hash = hashlib.sha256(sender.encode()).hexdigest()[:16]
        comment = self.github.add_authorization_comment(number,approved,actor_hash)
        decision = "APPROVED" if approved else "REJECTED"
        if not self.store.decide(number,decision,actor_hash,
                                 comment.get("id") if isinstance(comment,dict) else None):
            self.telegram.answer_callback(callback_id,"Decisión ya registrada"); return "ALREADY_DECIDED"
        self.telegram.answer_callback(callback_id,"Autorización registrada" if approved else "Rechazo registrado")
        if approved:
            self.telegram.send_text(f"✅ GitHub #{number}: AUTORIZAR HOTFIX PAPER registrado.\n"
                                    "Ahora puede prepararse rama + corrección + tests/CI + PR.\n"
                                    "NO se autorizó merge, deploy ni órdenes reales.")
        else:
            self.telegram.send_text(f"🛑 GitHub #{number}: NO AUTORIZAR registrado. No se prepara ningún hotfix.")
        return decision

    def poll_callbacks_once(self):
        offset = self.store.offset(); updates = self.telegram.updates(offset); handled = 0
        for update in updates:
            offset = max(offset, int(update.get("update_id",-1))+1)
            if update.get("callback_query"):
                self.handle_callback(update); handled += 1
        if updates: self.store.set_offset(offset)
        return handled


def _validate_runtime():
    if os.getenv("POROTA_CRITICAL_APPROVAL_ENABLED","false").lower() not in {"1","true","yes"}:
        raise GatewayError("CRITICAL_APPROVAL_DISABLED")
    if os.getenv("POROTA_RUNTIME_MODE","") != "PRODUCTION_PAPER":
        raise GatewayError("RUNTIME_MODE_NOT_PRODUCTION_PAPER")
    forbidden = ("PPI_API_KEY","PPI_API_SECRET","PPI_API_KEY_PROD","PPI_API_SECRET_PROD",
                 "PPI_ACCOUNT_NUMBER","PPI_PRODUCTION_SECRET_FILE")
    if any(os.getenv(name) for name in forbidden):
        raise GatewayError("PPI_CREDENTIAL_PRESENT_IN_CONTROL_PLANE")
    # Never share the observer bot token with this independent getUpdates loop.
    if os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_CHAT_ID"):
        raise GatewayError("OBSERVER_TELEGRAM_CREDENTIAL_PRESENT_IN_CONTROL_PLANE")


def run():
    _validate_runtime()
    github_token = _token_from_file(GITHUB_TOKEN_FILE)
    telegram_token = os.getenv("POROTA_CRITICAL_TELEGRAM_BOT_TOKEN","").strip()
    telegram_chat = os.getenv("POROTA_CRITICAL_TELEGRAM_CHAT_ID","").strip()
    if not telegram_token or not telegram_chat:
        raise GatewayError("DEDICATED_TELEGRAM_NOT_CONFIGURED")
    gateway = CriticalApprovalGateway(GithubIssuesClient(github_token),
                                      TelegramClient(telegram_token,telegram_chat),
                                      ApprovalStore())
    while True:
        try:
            gateway.scan_once(); gateway.poll_callbacks_once()
        except Exception as exc:
            print(f"critical-approval-gateway: {type(exc).__name__}: {str(exc)[:160]}", flush=True)
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    run()
