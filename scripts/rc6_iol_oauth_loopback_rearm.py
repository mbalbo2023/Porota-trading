#!/usr/bin/env python3
"""One-time IOL MCP OAuth consent on loopback; never touches market/trading routes.

The Action prepares a root-owned OAuth registration, then serves /start and
/callback on 127.0.0.1:8765 for a bounded interval. The existing token store is
not modified unless authorization-code exchange AND read-only MCP smoke pass.
No URL, state, PKCE verifier, token or account value is sent to CI logs.
"""

from __future__ import annotations

import argparse
import base64
import fcntl
import hashlib
import json
import os
import secrets
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


ISSUER = "https://mcp.invertironline.com"
REDIRECT = "http://127.0.0.1:8765/callback"
HOST, PORT = "127.0.0.1", 8765
TTL_SECONDS = 1800
STORE_DIR = Path("/root/.config/porota")
STORE = STORE_DIR / "iol_mcp_oauth_bootstrap.json"
PENDING = STORE_DIR / "iol_mcp_oauth_pending_rearm.json"
STATUS = STORE_DIR / "iol_mcp_oauth_rearm_status.json"
PAPER_DB = "/opt/porota-trading/data/paper_v17/observer_v17.db"

# Even if the web consent mode is misselected, never persist a credential with
# executable trading methods. The official IOL MCP lists these as state-changing.
WRITE_METHODS = frozenset({
    "accept_ddjj", "place_order", "cancel_order", "place_caucion",
    "subscribe_fci", "redeem_fci", "create_stop_loss_or_take_profit",
    "delete_stop_loss_or_take_profit",
})


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=True, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        os.chmod(path, 0o600)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read_json(path: Path) -> dict:
    obj = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise ValueError("expected object")
    return obj


def paper_safe() -> None:
    con = sqlite3.connect(f"file:{PAPER_DB}?mode=ro", uri=True, timeout=3)
    try:
        con.execute("PRAGMA query_only=ON")
        mode, sent = con.execute(
            "SELECT mode, real_orders_sent FROM observer_state WHERE id=1"
        ).fetchone()
    finally:
        con.close()
    if mode != "PRODUCTION_PAPER" or int(sent or 0) != 0:
        raise RuntimeError("PAPER/0 safety gate failed")


def http_json(url: str, body: dict, headers: dict | None = None) -> tuple[int, dict, dict]:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers=headers or {"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as response:
        content = response.read(2_000_000).decode("utf-8", errors="replace")
        return response.status, dict(response.headers), parse_rpc(content)


def parse_rpc(content: str) -> dict:
    """Accept JSON or Streamable HTTP SSE, but never accept an unparsed 200."""
    try:
        value = json.loads(content)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass
    for line in content.splitlines():
        if line.startswith("data:"):
            try:
                value = json.loads(line[5:].strip())
                if isinstance(value, dict) and ("result" in value or "error" in value):
                    return value
            except json.JSONDecodeError:
                continue
    raise RuntimeError("MCP response could not be verified")


def mcp_smoke(token: str) -> int:
    headers = {
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    status, init_headers, initial = http_json(
        ISSUER,
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "porota-rc6-readonly-rearm", "version": "1.0"},
        }},
        headers,
    )
    if status not in (200, 202) or initial.get("error") or "result" not in initial:
        raise RuntimeError("MCP initialize failed")
    session = init_headers.get("Mcp-Session-Id") or init_headers.get("mcp-session-id")
    if session:
        headers["Mcp-Session-Id"] = session
    status, _, listed = http_json(
        ISSUER, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}, headers
    )
    tools = (listed.get("result") or {}).get("tools")
    if status not in (200, 202) or not isinstance(tools, list) or not tools:
        raise RuntimeError("MCP tools/list not verified")
    names = {str(tool.get("name", "")).lower() for tool in tools if isinstance(tool, dict)}
    if names & WRITE_METHODS:
        raise RuntimeError("IOL consent is not SOLO LECTURA")
    return len(tools)


def create_authorization(state: dict) -> str:
    fields = {
        "response_type": "code",
        "client_id": state["client_registration"]["client_id"],
        "redirect_uri": REDIRECT,
        "code_challenge": state["pkce_challenge"],
        "code_challenge_method": "S256",
        "state": state["oauth_state"],
        "resource": ISSUER,
    }
    return ISSUER + "/authorize?" + urllib.parse.urlencode(fields)


def exchange_code(state: dict, code: str) -> dict:
    fields = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT,
        "client_id": state["client_registration"]["client_id"],
        "code_verifier": state["pkce_verifier"],
        "resource": ISSUER,
    }
    req = urllib.request.Request(
        ISSUER + "/token",
        data=urllib.parse.urlencode(fields).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as response:
        tokens = json.loads(response.read(2_000_000).decode())
    if not isinstance(tokens, dict) or not isinstance(tokens.get("access_token"), str):
        raise RuntimeError("OAuth exchange produced no valid access token")
    if not isinstance(tokens.get("refresh_token"), str):
        raise RuntimeError("OAuth exchange produced no refresh token")
    return tokens


def persist_success(state: dict, tokens: dict) -> int:
    count = mcp_smoke(tokens["access_token"])
    # Serialize against the existing observer collector's OAuth refresh lock.
    lock = STORE.with_suffix(STORE.suffix + ".refresh.lock")
    fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        paper_safe()
        existing = read_json(STORE) if STORE.exists() else {}
        updated = dict(existing)
        updated.update({
            "issuer": ISSUER, "redirect_uri": REDIRECT,
            "client_registration": state["client_registration"],
            "tokens": tokens, "token_endpoint": ISSUER + "/token",
            "token_obtained_at_epoch": time.time(),
        })
        # An expiry from the old credential must never be carried into a new one.
        updated.pop("token_expires_at_epoch", None)
        try:
            expires = float(tokens.get("expires_in", 0))
            if expires > 0:
                updated["token_expires_at_epoch"] = updated["token_obtained_at_epoch"] + expires
        except (TypeError, ValueError):
            pass
        updated.pop("pkce_verifier", None)
        updated.pop("oauth_state", None)
        atomic_json(STORE, updated)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    return count


class Handler(BaseHTTPRequestHandler):
    should_stop = False

    def log_message(self, *_args: object) -> None:
        pass

    def respond(self, ok: bool, message: str) -> None:
        body = ("<html><body><h2>" + message + "</h2><p>Podés volver a ChatGPT.</p></body></html>").encode()
        self.send_response(200 if ok else 400)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        try:
            state = read_json(PENDING)
            if time.time() > float(state["expires_at_epoch"]):
                self.respond(False, "El enlace caducó. No se modificaron los tokens.")
                return
            path = urllib.parse.urlsplit(self.path)
            if path.path in ("/", "/start"):
                self.send_response(302)
                self.send_header("Location", create_authorization(state))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return
            if path.path != "/callback":
                self.respond(False, "Ruta desconocida")
                return
            q = urllib.parse.parse_qs(path.query)
            if not secrets.compare_digest(q.get("state", [""])[0], state["oauth_state"]):
                self.respond(False, "Estado OAuth inválido")
                return
            code = q.get("code", [""])[0]
            if not code:
                self.respond(False, "El consentimiento fue cancelado")
                return
            tokens = exchange_code(state, code)
            count = persist_success(state, tokens)
            atomic_json(STATUS, {"phase": "verified", "tools_readonly": count, "timestamp": int(time.time())})
            self.respond(True, "Conexión IOL de solo lectura verificada")
            self.server.should_stop = True
        except urllib.error.HTTPError as error:
            atomic_json(STATUS, {"phase": "http_error", "code": error.code, "timestamp": int(time.time())})
            self.respond(False, "IOL rechazó la autorización (HTTP " + str(error.code) + ")")
            self.server.should_stop = True
        except Exception:
            atomic_json(STATUS, {"phase": "error", "timestamp": int(time.time())})
            self.respond(False, "IOL no pudo verificarse. Los tokens previos se conservaron.")
            self.server.should_stop = True


def serve() -> int:
    try:
        server = HTTPServer((HOST, PORT), Handler)
        server.timeout = 1
        server.should_stop = False
        atomic_json(STATUS, {"phase": "ready", "timestamp": int(time.time()), "pid": os.getpid()})
        pending = read_json(PENDING)
        while time.time() < float(pending["expires_at_epoch"]) and not server.should_stop:
            server.handle_request()
        if not server.should_stop:
            atomic_json(STATUS, {"phase": "expired", "timestamp": int(time.time())})
        server.server_close()
        return 0
    except Exception:
        atomic_json(STATUS, {"phase": "listen_error", "timestamp": int(time.time())})
        return 1
    finally:
        PENDING.unlink(missing_ok=True)
        # This file is only a root-owned temporary helper, not an RC6 deployment.
        Path(__file__).unlink(missing_ok=True)


def existing_registration() -> dict:
    """Reuse the prior consent client only when its identity and redirect match.

    Provider-side client registration can be denied while an existing client ID
    remains authorized for another browser consent. No refresh token is reused
    for the new authorization-code exchange, and the old store stays untouched.
    """
    data = read_json(STORE)
    registration = data.get("client_registration")
    if (
        data.get("issuer") != ISSUER
        or data.get("redirect_uri") != REDIRECT
        or not isinstance(registration, dict)
        or not isinstance(registration.get("client_id"), str)
        or not registration["client_id"]
        or registration.get("token_endpoint_auth_method") != "none"
    ):
        raise RuntimeError("prior client registration not reusable")
    redirects = registration.get("redirect_uris")
    if isinstance(redirects, list) and REDIRECT not in redirects:
        raise RuntimeError("prior client redirect differs")
    return registration


def prepare() -> int:
    paper_safe()
    with socket.socket() as check:
        check.bind((HOST, PORT))
    if PENDING.exists():
        try:
            if float(read_json(PENDING).get("expires_at_epoch", 0)) > time.time():
                raise RuntimeError("another OAuth session is active")
        except ValueError:
            raise RuntimeError("invalid pending OAuth session") from None
    reg_req = {
        "client_name": "Porota RC6 SOLO LECTURA temporary consent",
        "redirect_uris": [REDIRECT],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }
    registration_source = "NEW_CLIENT"
    try:
        status, _, registration = http_json(ISSUER + "/register", reg_req)
        if status not in (200, 201) or not registration.get("client_id"):
            raise RuntimeError("IOL client registration failed")
    except urllib.error.HTTPError as error:
        # /register 403 alone is not evidence that an existing client cannot
        # obtain a new authorization-code grant through the user's browser.
        # Reuse is allowed ONLY for the same known OAuth issuer/redirect.
        if error.code != 403:
            raise
        registration = existing_registration()
        registration_source = "EXISTING_VERIFIED_CLIENT"
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = {
        "issuer": ISSUER, "client_registration": registration,
        "pkce_verifier": verifier, "pkce_challenge": challenge,
        "oauth_state": secrets.token_urlsafe(32),
        "expires_at_epoch": time.time() + TTL_SECONDS,
    }
    atomic_json(PENDING, state)
    STATUS.unlink(missing_ok=True)
    with open(os.devnull, "wb") as sink:
        child = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "serve"],
            stdin=sink, stdout=sink, stderr=sink, start_new_session=True,
        )
    for _ in range(40):
        time.sleep(0.1)
        if STATUS.exists():
            receipt = read_json(STATUS)
            if receipt.get("phase") == "ready" and receipt.get("pid") == child.pid:
                print("IOL_REARM=READY")
                print("LOCAL_URL=http://127.0.0.1:8765/start")
                print("TTL_MINUTES=30")
                print("TOKEN_STORE_UNCHANGED=YES")
                print("REAL_ORDERS_SENT=0")
                print("CLIENT_SOURCE=" + registration_source)
                return 0
            if receipt.get("phase") == "listen_error":
                break
        if child.poll() is not None:
            break
    print("IOL_REARM=NOT_READY")
    return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("prepare", "serve"))
    args = parser.parse_args()
    try:
        sys.exit(prepare() if args.mode == "prepare" else serve())
    except Exception as exc:
        # A sanitized exception class only; HTTP bodies, tokens, and URLs stay private.
        print("IOL_REARM=ERROR:" + type(exc).__name__)
        sys.exit(1)
