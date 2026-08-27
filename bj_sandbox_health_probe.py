"""Una única autenticación Sandbox, sin cuenta, históricos ni órdenes."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from ppi_client.ppi import PPI

from be_paper_engine import PaperStore
from bf_production_paper_observer import _health, _support_schema
from c_ppi_client import ResilientPPIClient


TZ = ZoneInfo(os.getenv("SERVER_TIMEZONE", "America/Argentina/Buenos_Aires"))
DB_PATH = os.getenv("PAPER_DB_PATH", "data/observer/observer_production.db")


def run():
    store = PaperStore(DB_PATH)
    _support_schema(store)
    key = os.getenv("PPI_API_KEY", "").strip()
    secret = os.getenv("PPI_API_SECRET", "").strip()
    checked = datetime.now(TZ)
    next_check = checked + timedelta(hours=6)
    calls = 0
    http = "SIN_RESPUESTA"
    started = time.perf_counter()
    original = requests.sessions.Session.request

    def one_request(self, method, url, **kwargs):
        nonlocal calls, http
        calls += 1
        if calls > 1:
            raise RuntimeError("PROBE_HTTP_LIMIT_EXCEEDED")
        kwargs["timeout"] = (10, 30)
        response = original(self, method, url, **kwargs)
        http = str(response.status_code)
        return response

    state, detail = "ROJO", ""
    try:
        if not key or not secret:
            raise RuntimeError("Credenciales Sandbox ausentes")
        requests.sessions.Session.request = one_request
        client = PPI(sandbox=True)
        wrapper = ResilientPPIClient.__new__(ResilientPPIClient)
        wrapper.is_sandbox = True
        wrapper._configure_sandbox_sdk(client)
        client.account.login_api(key, secret)
        state = "VERDE"
        detail = "Autenticación Sandbox correcta; no se consultó cuenta y no se enviaron órdenes."
    except Exception as exc:
        detail = (f"Autenticación Sandbox falló: {type(exc).__name__}: {str(exc)[:360]}. "
                  "La conectividad y Producción se informan por separado.")
    finally:
        requests.sessions.Session.request = original
    elapsed = round(time.perf_counter() - started, 3)
    detail += (f" HTTP={http}; llamadas={calls}; tiempo={elapsed}s; "
               f"próximo chequeo no antes de {next_check.isoformat(timespec='seconds')}.")
    _health(store, "PPI_SANDBOX_AUTH", state, detail, "PPI Sandbox", success=state == "VERDE")
    result = {"state": state, "http": http, "calls": calls, "elapsed_seconds": elapsed,
              "checked_at": checked.isoformat(timespec="seconds"),
              "next_check_at": next_check.isoformat(timespec="seconds"),
              "account_queried": False, "orders_sent": 0}
    output = Path("data/informe_api_ppi_sandbox.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("SANDBOX_AUTH=" + state)
    print("HTTP_FINAL=" + http)
    print("LLAMADAS_HTTP=" + str(calls))
    print("CUENTA_CONSULTADA=NO")
    print("ORDENES_ENVIADAS=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
