#!/usr/bin/env python3
"""Bounded real dashboard/PAPER exercise inside the exact, isolated image.

Uses fresh synthetic SQLite files and an ephemeral dashboard credential. No
provider credential or production volume is accepted. The workflow supplies
--network none. Results describe CI behavior, never the deployed database.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import secrets
import re
import socket
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request


def inspect_paper_database(path):
    path = Path(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("PAPER_DATABASE_IDENTITY")
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=3)) as connection:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        integrity = connection.execute("PRAGMA quick_check").fetchall()
        journal = connection.execute("PRAGMA journal_mode").fetchone()[0]
        rows = connection.execute("SELECT id,mode,real_orders_sent FROM observer_state").fetchall()
        if len(rows) != 1 or rows[0][0] != 1 or rows[0][1] != "PRODUCTION_PAPER":
            raise ValueError("PAPER_MODE_INVALID")
        counter = rows[0][2]
        if type(counter) is not int or counter != 0:
            raise ValueError("PAPER_COUNTER_NONZERO_OR_INVALID")
        if integrity != [("ok",)] or journal != "wal":
            raise ValueError("PAPER_SQLITE_INTEGRITY")
        connection.rollback()
    after = path.lstat()
    if (info.st_dev, info.st_ino) != (after.st_dev, after.st_ino):
        raise ValueError("PAPER_DATABASE_IDENTITY_CHANGED")
    return {"paper_mode": rows[0][1], "real_orders_sent": counter,
            "sqlite_integrity": "ok", "sqlite_journal_mode": journal}


def synthetic_environment(scratch, token):
    """Complete dashboard startup configuration, generated only for this CI run."""
    return {"PAPER_V17_DB_PATH": str(scratch / "paper.db"), "DATA_DIR": str(scratch),
            "DB_PATH": str(scratch / "dashboard.db"),
            "DASHBOARD_SESSION_STORE": str(scratch / "sessions.json"),
            "STARTUP_STATE_PATH": str(scratch / "startup.json"),
            "DASHBOARD_OPERATION_MODE": "PRODUCTION_PAPER",
            "PAPER_ECONOMIC_GATE_MODE": "SHADOW", "PAPER_AI_GATE_MODE": "OFF",
            "DASHBOARD_ACCESS_TOKEN": token, "DASHBOARD_HOST": "127.0.0.1",
            "DASHBOARD_PASSWORD": secrets.token_urlsafe(32), "DASHBOARD_PASSWORD_HASH": "",
            "DASHBOARD_SESSION_HOURS": "1", "ENVIRONMENT": "SANDBOX",
            "PYTHONDONTWRITEBYTECODE": "1"}


def run_functional(identity):
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    from scripts.porota_image_source_identity import verify_source_identity
    verify_source_identity(identity["source_sha"], identity["source_tree"])
    if any(os.environ.get(key) for key in (
            "PPI_API_KEY", "PPI_API_SECRET", "PPI_SECRET_KEY", "IOL_PASSWORD",
            "TELEGRAM_BOT_TOKEN", "GEMINI_API_KEY")):
        raise ValueError("PROVIDER_CREDENTIAL_FORBIDDEN_IN_CI")
    import _version
    if (_version.MODE, _version.EXECUTION, _version.REAL_ORDER_CAPABILITY) != (
            "PRODUCTION_PAPER", "SIMULATED", "BLOCKED"):
        raise ValueError("PAPER_SOURCE_POLICY")
    with tempfile.TemporaryDirectory(prefix="porota-functional-") as temporary:
        scratch = Path(temporary)
        paper = scratch / "paper.db"
        token = secrets.token_urlsafe(32)
        os.environ.update(synthetic_environment(scratch, token))
        from be_paper_engine import PaperBroker, PaperStore
        store = PaperStore(str(paper))
        broker = PaperBroker(store, ai_mode="OFF")
        if broker.economics_mode != "SHADOW" or broker.daily_risk is None:
            raise ValueError("PAPER_BROKER_POLICY")
        store.event("CI_FUNCTIONAL", "Synthetic PAPER transaction; no provider")
        broker.mark_equity({})
        # The real requests hooks must block order attempts before transport.
        import requests
        from bd_ppi_readonly_guard import ReadOnlyPolicyViolation, ReadOnlyTransportGuard
        guard = ReadOnlyTransportGuard(audit=store.audit_http).install()
        paths = ("/api/1.0/orders", "/api/1.0/orders/confirm", "/api/1.0/orders/cancel")
        try:
            for path in paths:
                try:
                    requests.post("https://clientapi.portfoliopersonal.com" + path,
                                  json={"synthetic": True}, timeout=1)
                except ReadOnlyPolicyViolation:
                    continue
                raise ValueError("REAL_ORDER_ROUTE_NOT_BLOCKED")
            if guard.calls_allowed != 0 or guard.calls_blocked != len(paths):
                raise ValueError("REAL_ORDER_TRANSPORT_REACHED")
        finally:
            guard.restore()
        with closing(sqlite3.connect(paper)) as connection:
            if connection.execute("SELECT count(*) FROM paper_events WHERE event_type='CI_FUNCTIONAL'").fetchone()[0] != 1:
                raise ValueError("PAPER_TRANSACTION_NOT_DURABLE")
            if connection.execute("SELECT count(*) FROM http_audit WHERE result='BLOCKED'").fetchone()[0] != len(paths):
                raise ValueError("ORDER_BLOCK_NOT_RECORDED")
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        environment = dict(os.environ, DASHBOARD_PORT=str(port))
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        base = "http://127.0.0.1:" + str(port)

        def get(path, authenticated=True):
            headers = {"Authorization": "Bearer " + token} if authenticated else {}
            with opener.open(urllib.request.Request(base + path, headers=headers), timeout=3) as response:
                return response.status, response.read(2 * 1024 * 1024)

        with (scratch / "dashboard.log").open("wb") as log:
            child = subprocess.Popen([sys.executable, "-B", str(root / "o_dashboard.py")],
                                     cwd=root, env=environment, stdout=log, stderr=log)
            try:
                deadline = time.monotonic() + 60
                while True:
                    if child.poll() is not None:
                        raise ValueError("DASHBOARD_EXITED_BEFORE_READY")
                    try:
                        status, body = get("/health", authenticated=False)
                        if status == 200 and json.loads(body).get("status") == "ok":
                            break
                    except (OSError, ValueError, urllib.error.URLError):
                        pass
                    if time.monotonic() >= deadline:
                        raise ValueError("DASHBOARD_STARTUP_TIMEOUT")
                    time.sleep(0.2)
                statuses = {"/health": status}
                for path in ("/api/dashboard/truth", "/vivo", "/salud"):
                    status, body = get(path)
                    if status != 200 or not body:
                        raise ValueError("DASHBOARD_FUNCTIONAL_HTTP")
                    if path == "/api/dashboard/truth" and not isinstance(json.loads(body), dict):
                        raise ValueError("DASHBOARD_TRUTH_RESPONSE")
                    statuses[path] = status
                try:
                    get("/api/dashboard/truth", authenticated=False)
                except urllib.error.HTTPError as error:
                    if error.code not in {401, 403}:
                        raise ValueError("DASHBOARD_AUTH_RESPONSE") from None
                else:
                    raise ValueError("DASHBOARD_AUTH_NOT_ENFORCED")
                database = inspect_paper_database(paper)
                return {"schema": "porota.functional.v1", "status": "PASS", **identity, **database,
                        "http": statuses, "order_routes_blocked": True,
                        "tested_order_routes": len(paths), "environment": "ISOLATED_SYNTHETIC_CI"}
            finally:
                if child.poll() is None:
                    child.terminate()
                    try:
                        child.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait(timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--image-id", required=True)
    args = parser.parse_args()
    identity = {"source_sha": args.source_sha, "source_tree": args.source_tree, "image_id": args.image_id}
    try:
        receipt = run_functional(identity)
    except Exception as error:
        code = str(error) if isinstance(error, ValueError) and re.fullmatch(r"[A-Z0-9_]+", str(error)) else "FUNCTIONAL_EXCEPTION"
        receipt = {"schema": "porota.functional.v1", "status": "FAIL", **identity,
                   "failure_code": code, "error_class": type(error).__name__}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, sort_keys=True) + "\n")
    print(json.dumps(receipt, sort_keys=True))
    return 0 if receipt["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
