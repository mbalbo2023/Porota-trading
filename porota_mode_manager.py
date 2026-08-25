#!/usr/bin/env python3
"""Selector exclusivo de modo operativo para Porota Trading v16.3.4.

Uso:
  sudo python porota_mode_manager.py status
  sudo python porota_mode_manager.py simulation
  sudo python porota_mode_manager.py sandbox
  sudo python porota_mode_manager.py stop

Produccion real permanece fail-closed y exige dos habilitaciones independientes.
"""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
MODE_FILE = DATA / "operation_mode.json"
IMAGE = "porota-trading-bot:16.3.4"
TZ = ZoneInfo(os.getenv("SERVER_TIMEZONE", "America/Argentina/Buenos_Aires"))
KNOWN = ("porota_production_observer", "porota_production_dashboard",
         "porota_dashboard_preview", "porota_sandbox_engine", "porota_production_engine")


def run(*args, check=True, capture=False):
    return subprocess.run(args, cwd=ROOT, check=check, text=True,
                          stdout=subprocess.PIPE if capture else None,
                          stderr=subprocess.STDOUT if capture else None)


def env_file():
    result = {}
    path = ROOT / ".env"
    if not path.exists():
        return result
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip().strip('"').strip("'")
    return result


def write_mode(mode, engine, execution, apis, telegram="PENDING", detail=""):
    DATA.mkdir(parents=True, exist_ok=True)
    payload = {"schema_version": 1, "mode": mode, "engine": engine,
               "execution": execution, "apis": apis,
               "changed_at": datetime.now(TZ).isoformat(timespec="seconds"),
               "telegram_notification": telegram, "detail": detail}
    tmp = MODE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, MODE_FILE)
    return payload


def notify(message):
    env = env_file()
    token, chat = env.get("TELEGRAM_BOT_TOKEN"), env.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return "NO_CONFIGURADO"
    try:
        body = urlencode({"chat_id": chat, "text": message}).encode()
        req = Request(f"https://api.telegram.org/bot{token}/sendMessage", data=body,
                      method="POST")
        with urlopen(req, timeout=8) as response:
            return "ENTREGADO" if response.status == 200 else f"HTTP_{response.status}"
    except Exception as exc:
        return "FALLO_" + type(exc).__name__


def stop_engines():
    for name in KNOWN:
        run("docker", "rm", "-f", name, check=False, capture=True)
    run("docker", "compose", "stop", "-t", "20", "bot", "sre_vectordb",
        check=False, capture=True)


def dashboard_env(mode):
    source = DATA / "diagnosticos" / "dashboard_preview_v1633.env"
    if not source.exists():
        raise RuntimeError("Falta el archivo persistente de acceso al dashboard.")
    target = DATA / "diagnosticos" / "dashboard_mode_v1634.env"
    safe = []
    forbidden = ("PPI_", "TELEGRAM_", "GEMINI_", "IOL_", "ROFEX_")
    for line in source.read_text(encoding="utf-8", errors="replace").splitlines():
        if "=" not in line:
            continue
        key = line.split("=", 1)[0].strip()
        if not key.startswith(forbidden) and key not in {"DASHBOARD_OPERATION_MODE", "PAPER_DB_PATH"}:
            safe.append(line)
    safe += [f"DASHBOARD_OPERATION_MODE={mode}",
             "PAPER_DB_PATH=/app/data/observer/observer_production.db",
             "SERVER_TIMEZONE=America/Argentina/Buenos_Aires"]
    target.write_text("\n".join(safe) + "\n", encoding="utf-8")
    os.chmod(target, 0o600)
    return target


def start_dashboard(mode):
    env_path = dashboard_env(mode)
    run("docker", "rm", "-f", "porota_production_dashboard", check=False, capture=True)
    run("docker", "run", "-d", "--name", "porota_production_dashboard",
        "--restart", "unless-stopped", "--user", "botuser", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges:true", "-p", "127.0.0.1:8000:8000",
        "--env-file", str(env_path), "-v", f"{DATA}:/app/data",
        "--entrypoint", "python", IMAGE, "o_dashboard.py")


def simulation():
    stop_engines()
    write_mode("PRODUCTION_PAPER", "production_observer", "SIMULATED",
               {"PPI_PRODUCTION": "MARKET_DATA_READ_ONLY", "TELEGRAM": "MODE_NOTIFICATIONS_ONLY",
                "PPI_ORDERS": "BLOCKED", "GEMINI": "OFF_V1"}, detail="Iniciando")
    start_dashboard("PRODUCTION_PAPER")
    secret = ROOT / ".secrets" / "ppi_production.json"
    if not secret.exists():
        raise RuntimeError("Falta el secreto productivo de solo lectura.")
    run("docker", "run", "-d", "--name", "porota_production_observer",
        "--restart", "no", "--no-healthcheck", "--user", "botuser", "--read-only", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges:true", "--tmpfs", "/tmp:rw,noexec,nosuid,size=32m",
        "-e", "PPI_PRODUCTION_SECRET_FILE=/run/secrets/ppi_production.json",
        "-e", "PAPER_DB_PATH=/app/data/observer/observer_production.db",
        "-e", "SERVER_TIMEZONE=America/Argentina/Buenos_Aires",
        "-v", f"{DATA}:/app/data", "-v", f"{secret}:/run/secrets/ppi_production.json:ro",
        "--entrypoint", "python", IMAGE, "bf_production_paper_observer.py")
    status = notify("🟣 POROTA — MODO SIMULACIÓN PRODUCTIVA\nDatos reales de PPI Producción. Compras y ventas 100% simuladas. Órdenes reales: NINGUNA.")
    write_mode("PRODUCTION_PAPER", "production_observer", "SIMULATED",
               {"PPI_PRODUCTION": "MARKET_DATA_READ_ONLY", "TELEGRAM": "MODE_NOTIFICATIONS_ONLY",
                "PPI_ORDERS": "BLOCKED", "GEMINI": "OFF_V1"}, telegram=status,
               detail="Simulador productivo y dashboard independiente iniciados")


def sandbox():
    stop_engines()
    write_mode("SANDBOX", "trading_bot", "SANDBOX_ORDERS",
               {"PPI_SANDBOX": "MARKET_DATA_AND_SANDBOX_ORDERS", "TELEGRAM": "ACTIVE"},
               detail="Iniciando Sandbox")
    start_dashboard("SANDBOX")
    run("docker", "compose", "up", "-d", "sre_vectordb")
    run("docker", "compose", "run", "-d", "--name", "porota_sandbox_engine",
        "--no-deps", "bot")
    status = notify("🧪 POROTA — MODO SANDBOX\nLas órdenes se envían únicamente al entorno de pruebas de PPI. Dinero real: NINGUNO.")
    write_mode("SANDBOX", "trading_bot", "SANDBOX_ORDERS",
               {"PPI_SANDBOX": "MARKET_DATA_AND_SANDBOX_ORDERS", "TELEGRAM": "ACTIVE"},
               telegram=status, detail="Sandbox iniciado; sujeto a disponibilidad de PPI Sandbox")


def production(argv):
    gate = ROOT / ".secrets" / "production_real.enable"
    if "--confirm-real-money" not in argv or not gate.exists():
        raise RuntimeError("PRODUCCION REAL BLOQUEADA: requiere archivo de habilitacion y --confirm-real-money.")
    raise RuntimeError("PRODUCCION REAL AUN NO IMPLEMENTADA EN ESTE SELECTOR: permanece fail-closed.")


def stop():
    stop_engines()
    status = notify("⚪ POROTA — PLATAFORMA DETENIDA\nDashboard y motores detenidos; no se realizan llamadas ni operaciones.")
    write_mode("DETENIDO", "NONE", "NONE", {}, telegram=status,
               detail="Todos los motores detenidos")


def status():
    if MODE_FILE.exists():
        print(MODE_FILE.read_text(encoding="utf-8"))
    else:
        print(json.dumps({"mode": "DESCONOCIDO", "detail": "Sin manifiesto"}))
    run("docker", "ps", "-a", "--filter", "name=porota_", "--format",
        "{{.Names}} | {{.Image}} | {{.Status}}", check=False)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    action = argv[0].lower() if argv else "status"
    lock_path = DATA / "diagnosticos" / "porota_mode.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if action in ("simulation", "simulacion"):
            simulation()
        elif action == "sandbox":
            sandbox()
        elif action in ("production", "produccion"):
            production(argv[1:])
        elif action in ("stop", "detenido"):
            stop()
        elif action == "status":
            status()
        else:
            raise SystemExit("Uso: porota-mode <status|simulation|sandbox|stop|production>")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BlockingIOError:
        raise SystemExit("Otro cambio de modo esta en curso.")
    except Exception as exc:
        print(f"CAMBIO_DE_MODO=FALLO\nERROR={exc}", file=sys.stderr)
        raise SystemExit(1)
