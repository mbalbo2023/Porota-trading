#!/usr/bin/env python3
"""Selector exclusivo de modo operativo para Porota Trading v17 candidata.

Uso:
  sudo python porota_mode_manager.py status
  sudo python porota_mode_manager.py simulation
  sudo python porota_mode_manager.py sandbox
  sudo python porota_mode_manager.py stop

Produccion real permanece deshabilitada permanentemente por politica del proyecto.
"""

from __future__ import annotations

import fcntl
import base64
import hashlib
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
from cg_paper_workspace import DB_ENV, CONTAINER_DB, IMAGE


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
MODE_FILE = DATA / "operation_mode.json"
TZ = ZoneInfo(os.getenv("SERVER_TIMEZONE", "America/Argentina/Buenos_Aires"))
KNOWN = ("porota_production_observer", "porota_production_dashboard",
         "porota_dashboard_preview", "porota_sandbox_engine", "porota_production_engine")
PAPER_DEFAULTS = {
    "PAPER_INITIAL_CAPITAL_ARS": "1000000", "PAPER_INITIAL_CAPITAL_USD": "1000",
    "PAPER_INITIAL_CAPITAL_USD_MEP": "1000", "PAPER_INITIAL_CAPITAL_USD_CCL": "1000",
    "PAPER_RISK_PER_TRADE": "0.002", "PAPER_MAX_OPEN_POSITIONS": "5", "PAPER_EMERGENCY_MAX_OPEN_POSITIONS": "AUTO",
    "PAPER_MAX_POSITION_PCT": "0.25", "PAPER_MAX_TOTAL_EXPOSURE_PCT": "0.60",
    "PAPER_MAX_HOLD_MINUTES": "360", "PAPER_ACTIVE_SYMBOL_LIMIT": "20",
    "PAPER_FOCUS_MINIMUM_FOR_OPENINGS": "4",
    "PAPER_READINESS_CHECK_SECONDS": "300",
    # RC6: el freno blando deja de abrir; el límite duro recién entonces
    # ordena liquidar. El operador autorizó ampliar el duro a 2,5%.
    "PAPER_DAILY_SOFT_STOP_PCT": "1.5",
    "MAX_DAILY_LOSS_PCT": "2.5",
    "PAPER_SIGNAL_MIN_SAMPLES": "6", "PAPER_SIGNAL_WINDOW_MINUTES": "90",
    "PAPER_SCORE_THRESHOLD": "0.62", "PAPER_BOOK_MAX_AGE_SECONDS": "120",
    "PAPER_TRADE_MAX_AGE_SECONDS": "900", "PAPER_AI_GATE_MODE": "OFF",
    "PAPER_ECONOMIC_GATE_MODE": "SHADOW",
    # Decisiones explícitas del operador para RC6. Expectancy y régimen conservan
    # su autoridad observacional; concentración sectorial es BINDING para nuevas entradas.
    "PAPER_EXPECTANCY_POLICY": "OBSERVATION_ONLY",
    "PAPER_MARKET_REGIME_POLICY": "ALERT_ONLY",
    "PAPER_SECTOR_CONCENTRATION_POLICY": "BINDING",
    "PAPER_MAX_POSITIONS_PER_SECTOR": "2",
    "PAPER_MIN_NET_REWARD_RISK": "1.20",
    "PAPER_STOP_LOSS_PCT": "0.02", "PAPER_TARGET_GAIN_PCT": "0.05",
    "PAPER_INTRADAY_FEE_REBATE": "true",
    "PPI_BACKGROUND_INGEST_SECONDS": "7200",
    "PPI_HISTORY_RAW_STORAGE_MODE": "EXTERNAL_EXACT_V1",
    "PPI_HISTORY_EVIDENCE_ROOT": "/app/data/evidence/ppi_history_exact_v1",
    "PAPER_NEWS_INGEST_ENABLED": "false",
    # Foco configurable. AAPLD/AAPLC agregan identidades MEP/CCL sin quitar
    # conserva las identidades de foco validadas; catálogo, libro y portones siguen
    # siendo obligatorios antes de cualquier fill PAPER.
    "PAPER_FOCUS_SYMBOLS": (
        "GGAL:ACCIONES:A-24HS,YPFD:ACCIONES:A-24HS,"
        "PAMP:ACCIONES:A-24HS,BMA:ACCIONES:A-24HS,"
        "BBAR:ACCIONES:A-24HS,SUPV:ACCIONES:A-24HS,"
        "CEPU:ACCIONES:A-24HS,AAPL:CEDEARS:A-24HS,"
        "AAPLD:CEDEARS:A-24HS,AAPLC:CEDEARS:A-24HS"
    ),
    "PAPER_SCALPING_MODE": "ACTIVE_OBSERVE",
    "PAPER_INTRADAY_SCAN_SECONDS": "180", "PAPER_INTRADAY_BATCH_LIMIT": "24",
    "PAPER_SCALPING_MIN_NET_MARGIN": "0.005",
    "PAPER_SCALPING_MAX_SPREAD": "0.005",
    "PAPER_SCALPING_SCORE_THRESHOLD": "0.68",
    "PAPER_SCALPING_RISK_PER_TRADE": "0.001",
    "PAPER_SCALPING_MAX_OPEN_POSITIONS": "1",
    "PAPER_SCALPING_MAX_HOLD_MINUTES": "30",
    "PAPER_SCALPING_STOP_LOSS_PCT": "0.008",
    "PAPER_SCALPING_TARGET_GAIN_PCT": "0.02",
}
RC6_FROZEN_PAPER_SETTINGS = {
    # Resultado de la rueda 31/08: estas barreras no pueden heredarse de un
    # .env viejo porque volvería a abrir con economía negativa o riesgo obsoleto.
    "PAPER_RISK_PER_TRADE": "0.002",
    "PAPER_MAX_OPEN_POSITIONS": "5",
    "PAPER_EMERGENCY_MAX_OPEN_POSITIONS": "AUTO",
    "PAPER_MAX_HOLD_MINUTES": "360",
    "PAPER_DAILY_SOFT_STOP_PCT": "1.5",
    "MAX_DAILY_LOSS_PCT": "2.5",
    "PAPER_ECONOMIC_GATE_MODE": "SHADOW",
    "PAPER_EXPECTANCY_POLICY": "OBSERVATION_ONLY",
    "PAPER_MARKET_REGIME_POLICY": "ALERT_ONLY",
    "PAPER_SECTOR_CONCENTRATION_POLICY": "BINDING",
    "PAPER_MAX_POSITIONS_PER_SECTOR": "2",
    "PAPER_FOCUS_SYMBOLS": (
        "GGAL:ACCIONES:A-24HS,YPFD:ACCIONES:A-24HS,"
        "PAMP:ACCIONES:A-24HS,BMA:ACCIONES:A-24HS,"
        "BBAR:ACCIONES:A-24HS,SUPV:ACCIONES:A-24HS,"
        "CEPU:ACCIONES:A-24HS,AAPL:CEDEARS:A-24HS,"
        "AAPLD:CEDEARS:A-24HS,AAPLC:CEDEARS:A-24HS"
    ),
    "PAPER_TARGET_GAIN_PCT": "0.05",
    "PAPER_SCALPING_MODE": "ACTIVE_OBSERVE",
    "PAPER_INTRADAY_SCAN_SECONDS": "180",
    "PAPER_INTRADAY_BATCH_LIMIT": "24",
    "PAPER_SCALPING_MIN_NET_MARGIN": "0.005",
    "PAPER_SCALPING_MAX_SPREAD": "0.005",
    "PAPER_SCALPING_SCORE_THRESHOLD": "0.68",
    "PPI_BACKGROUND_INGEST_SECONDS": "7200",
    "PPI_HISTORY_RAW_STORAGE_MODE": "EXTERNAL_EXACT_V1",
    "PPI_HISTORY_EVIDENCE_ROOT": "/app/data/evidence/ppi_history_exact_v1",
    "PAPER_NEWS_INGEST_ENABLED": "false",
    "PAPER_SCALPING_RISK_PER_TRADE": "0.001",
    "PAPER_SCALPING_MAX_OPEN_POSITIONS": "1",
    "PAPER_SCALPING_MAX_HOLD_MINUTES": "30",
    "PAPER_SCALPING_STOP_LOSS_PCT": "0.008",
    "PAPER_SCALPING_TARGET_GAIN_PCT": "0.02",
}


def paper_settings(env):
    values = {key: env.get(key, "").strip() or default for key, default in PAPER_DEFAULTS.items()}
    values.update(RC6_FROZEN_PAPER_SETTINGS)
    return values


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
            raw = response.read().decode("utf-8", errors="replace")
            payload = json.loads(raw)
            result = payload.get("result") or {}
            message_id = result.get("message_id")
            sent_ok = response.status == 200 and payload.get("ok") is True and message_id is not None

        # Telegram's sendMessage response proves API acceptance, not that the
        # configured destination is the chat the operator is actually watching.
        # Verify the same destination without logging the token or chat id.
        chat_req = Request(
            f"https://api.telegram.org/bot{token}/getChat?{urlencode({'chat_id': chat})}",
            method="GET",
        )
        with urlopen(chat_req, timeout=8) as chat_response:
            chat_raw = chat_response.read().decode("utf-8", errors="replace")
            chat_payload = json.loads(chat_raw)
            chat_result = chat_payload.get("result") or {}
            chat_ok = chat_response.status == 200 and chat_payload.get("ok") is True
            chat_type = chat_result.get("type")
        fingerprint = hashlib.sha256(str(chat).encode("utf-8")).hexdigest()[:12]
        delivered = sent_ok and chat_ok
        DATA.mkdir(parents=True, exist_ok=True)
        delivery = {
            "status": "ENTREGADO" if delivered else (
                "API_ACEPTADO_CHAT_NO_VERIFICADO" if sent_ok else f"HTTP_{response.status}"
            ),
            "message_id": message_id,
            "chat_verified": bool(chat_ok),
            "chat_type": chat_type if chat_ok else None,
            "chat_fingerprint": fingerprint,
            "sent_at": datetime.now(TZ).isoformat(timespec="seconds"),
        }
        tmp = DATA / "telegram_last_delivery.json.tmp"
        tmp.write_text(json.dumps(delivery, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, DATA / "telegram_last_delivery.json")
        return delivery["status"]
    except Exception as exc:
        return "FALLO_" + type(exc).__name__


def stop_engines(include_dashboard=True):
    for name in KNOWN:
        if not include_dashboard and name in {"porota_production_dashboard", "porota_dashboard_preview"}:
            continue
        run("docker", "rm", "-f", name, check=False, capture=True)
    run("docker", "compose", "stop", "-t", "20", "bot",
        check=False, capture=True)


def dashboard_env(mode):
    source = DATA / "diagnosticos" / "dashboard_preview_v1633.env"
    if not source.exists():
        raise RuntimeError("Falta el archivo persistente de acceso al dashboard.")
    target = DATA / "diagnosticos" / "dashboard_mode_v17.env"
    safe = []
    forbidden = ("PPI_", "TELEGRAM_", "GEMINI_", "IOL_", "ROFEX_")
    for line in source.read_text(encoding="utf-8", errors="replace").splitlines():
        if "=" not in line:
            continue
        key = line.split("=", 1)[0].strip()
        if not key.startswith(forbidden) and key not in {"DASHBOARD_OPERATION_MODE", "PAPER_DB_PATH", DB_ENV, *PAPER_DEFAULTS}:
            safe.append(line)
    safe += [f"DASHBOARD_OPERATION_MODE={mode}",
             f"{DB_ENV}={CONTAINER_DB}",
             "DASHBOARD_REFRESH_SECONDS=30",
             "SERVER_TIMEZONE=America/Argentina/Buenos_Aires"]
    safe += [f"{key}={value}" for key, value in paper_settings(env_file()).items()]
    target.write_text("\n".join(safe) + "\n", encoding="utf-8")
    os.chmod(target, 0o600)
    return target


def observer_runtime_env():
    """Archivo 0600 del observador sin IA intradiaria."""
    env = env_file()
    target = DATA / "diagnosticos" / "observer_runtime_v17.env"
    values = {
        "TELEGRAM_BOT_TOKEN": env.get("TELEGRAM_BOT_TOKEN", "").strip(),
        "TELEGRAM_CHAT_ID": env.get("TELEGRAM_CHAT_ID", "").strip(),
    }
    values.update(paper_settings(env))
    # Contract Evidence recolecta PPI read-only para todas las familias auditables.\n    # Nunca habilita decisiones ni órdenes; las familias fuera de alcance siguen fail-closed.\n    values["POROTA_CONTRACT_EVIDENCE_MODE"] = env.get("POROTA_CONTRACT_EVIDENCE_MODE", "ENABLED").strip().upper() or "ENABLED"\n    # RC6 settlement hotfix: autoridad explícita sólo en el observer PAPER.
    # El módulo de settlement permanece fail-closed fuera de este runtime.
    values["PAPER_T1_FULL_DATE_RELEASE"] = "true"
    values[DB_ENV] = CONTAINER_DB
    target.write_text("\n".join(f"{name}={value}" for name, value in values.items()) + "\n",
                      encoding="utf-8")
    os.chmod(target, 0o600)
    return target


def start_dashboard(mode):
    env_path = dashboard_env(mode)
    run("docker", "rm", "-f", "porota_production_dashboard", check=False, capture=True)
    # The dashboard image is immutable, but the deployment host is the canonical
    # source staged by the transactional workflow. Mount only the RC6 dashboard
    # modules read-only so a stale /app copy can never mask the exact candidate.
    # The observer and PPI Watch remain separate owners and are not mounted here.
    runtime_sources = (
        "rc6_annual_instrument_analysis.py",
        "rc6_family_readiness.py",
        "rc6_dashboard_responsive_ux.py",
        "rc6_ppi_iol_reconciliation_rc6.py",
        "rc6_cauciones_shadow_evidence.py",
        "eq_dashboard_table_layout_rc6.py",
        "er_dashboard_table_semantics_rc6.py",
        "iol_shadow_observation_rc6.py",
        "iol_shadow_collector_rc6.py",
        "iol_mcp_readonly_adapter_rc6.py",
    )
    source_mounts = []
    for filename in runtime_sources:
        source = ROOT / filename
        if not source.is_file():
            raise RuntimeError("RC6_DASHBOARD_SOURCE_MISSING:" + filename)
        source_mounts.extend((
            "--mount",
            f"type=bind,source={source},target=/app/{filename},readonly",
        ))
    run("docker", "run", "-d", "--name", "porota_production_dashboard",
        "--pull", "never", "--restart", "unless-stopped", "--user", "botuser", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges:true", "-p", "127.0.0.1:8000:8000",
        "--env-file", str(env_path), "-v", f"{DATA}:/app/data", *source_mounts,
        "--entrypoint", "python", IMAGE, "o_dashboard.py")
    # Defensive source synchronization for the live container. The image is
    # checked separately by the deploy workflow; this second guard prevents a
    # stale /app copy from surviving container recreation. Files remain
    # read-only at the container's effective runtime user and only dashboard
    # modules are touched.
    for filename in runtime_sources:
        source = ROOT / filename
        run("docker", "cp", str(source),
            f"porota_production_dashboard:/app/{filename}")


def simulation():
    stop_engines()
    write_mode("PRODUCTION_PAPER", "production_observer", "SIMULATED",
               {"PPI_PRODUCTION": "MARKET_DATA_READ_ONLY", "TELEGRAM": "MODE_NOTIFICATIONS_ONLY",
                "PPI_ORDERS": "BLOCKED", "PYTHON_MATH_ENGINE": "ACTIVE"}, detail="Iniciando")
    start_dashboard("PRODUCTION_PAPER")
    secret = ROOT / ".secrets" / "ppi_production.json"
    if not secret.exists():
        raise RuntimeError("Falta el secreto productivo de solo lectura.")
    runtime_env = observer_runtime_env()
    # RC6 worker provenance: the candidate image is verified by the deploy
    # workflow before this manager is invoked. Remove any previous observer and
    # run only that immutable image; then fail closed if PID 1 exits immediately.
    removed = run("docker", "rm", "-f", "porota_production_observer", check=False, capture=True)
    print("RC6_OBSERVER_REMOVE=" + removed.stdout.strip())
    created = run("docker", "run", "-d", "--name", "porota_production_observer",
        "--pull", "never", "--restart", "unless-stopped", "--no-healthcheck",
        "--user", "botuser", "--read-only", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges:true", "--tmpfs", "/tmp:rw,noexec,nosuid,size=32m",
        "-e", "PPI_PRODUCTION_SECRET_FILE=/run/secrets/ppi_production.json",
        "-e", f"{DB_ENV}={CONTAINER_DB}",
        "-e", "MARKET_OPEN_HOUR=10",
        "-e", "MARKET_OPEN_MINUTE=30",
        "-e", "MARKET_CLOSE_HOUR=17",
        "-e", "MARKET_CLOSE_MINUTE=0",
        "-e", "PAPER_PREOPEN_MINUTES=15",
        "-e", "PPI_LOGIN_COOLDOWN_SECONDS=900",
        "-e", "PPI_HISTORY_BATCH_LIMIT=40",
        "-e", "DATA_DIR=/app/data",
        "-e", "SERVER_TIMEZONE=America/Argentina/Buenos_Aires",
        "--env-file", str(runtime_env),
        "-e", "PAPER_SCALPING_MODE=ACTIVE_OBSERVE",
        "-v", f"{DATA}:/app/data", "-v", f"{secret}:/run/secrets/ppi_production.json:ro",
        "--entrypoint", "python", IMAGE, "bv_paper_runtime.py", capture=True)
    print("RC6_OBSERVER_CREATED_ID=" + created.stdout.strip())
    state = run("docker", "inspect", "-f", "{{.State.Status}}",
                 "porota_production_observer", capture=True).stdout.strip()
    if state != "running":
        logs = run("docker", "logs", "--tail", "80", "porota_production_observer",
                   check=False, capture=True).stdout.strip()
        raise RuntimeError(f"RC6_OBSERVER_EXITED:{state}:{logs[-4000:]}")
    for filename in ("cf_intraday_scalping.py", "co_market_sessions_hf6.py"):
        expected = hashlib.sha256((ROOT / filename).read_bytes()).hexdigest()
        actual = run("docker", "exec", "porota_production_observer",
                     "sha256sum", f"/app/{filename}", capture=True).stdout.strip().split()[0]
        if actual != expected:
            run("docker", "rm", "-f", "porota_production_observer", check=False, capture=True)
            raise RuntimeError(f"RC6_RUNTIME_SOURCE_MISMATCH:{filename}:expected={expected}:running={actual}")
    status = notify("✅ POROTA TRADING — SISTEMA ACTIVO NUEVAMENTE\n🟣 Modo simulación productiva. Datos reales de PPI Producción; decisiones intradiarias determinísticas en Python; IA desactivada. Compras y ventas 100% simuladas. Órdenes reales: NINGUNA.\nHistorial PAPER v17 independiente; sin traslado de saldos, posiciones ni aprendizaje anteriores.")
    write_mode("PRODUCTION_PAPER", "production_observer", "SIMULATED",
               {"PPI_PRODUCTION": "MARKET_DATA_READ_ONLY", "TELEGRAM": "MODE_NOTIFICATIONS_ONLY",
                "PPI_ORDERS": "BLOCKED", "PYTHON_MATH_ENGINE": "ACTIVE"}, telegram=status,
               detail="Fuera de rueda ingiere históricos por lotes; preapertura sincroniza; rueda abierta simula")


def sandbox():
    stop_engines()
    write_mode("SANDBOX", "trading_bot", "SANDBOX_ORDERS",
               {"PPI_SANDBOX": "MARKET_DATA_AND_SANDBOX_ORDERS", "TELEGRAM": "ACTIVE"},
               detail="Iniciando Sandbox")
    start_dashboard("SANDBOX")
    run("docker", "compose", "run", "-d", "--name", "porota_sandbox_engine",
        "--no-deps", "bot")
    status = notify("🧪 POROTA — MODO SANDBOX\nLas órdenes se envían únicamente al entorno de pruebas de PPI. Dinero real: NINGUNO.")
    write_mode("SANDBOX", "trading_bot", "SANDBOX_ORDERS",
               {"PPI_SANDBOX": "MARKET_DATA_AND_SANDBOX_ORDERS", "TELEGRAM": "ACTIVE"},
               telegram=status, detail="Sandbox iniciado; sujeto a disponibilidad de PPI Sandbox")


def production(argv):
    del argv
    raise RuntimeError(
        "PRODUCCION REAL DESHABILITADA PERMANENTEMENTE: Porota opera solamente "
        "SANDBOX o PRODUCTION_PAPER con ejecucion simulada."
    )


def stop():
    # DETENIDO se refiere a motores; el dashboard continúa 24x7.
    stop_engines(include_dashboard=False)
    start_dashboard("DETENIDO")
    status = notify("⚪ POROTA — MOTORES DETENIDOS\nDashboard 24x7 activo; no se realizan llamadas PPI ni operaciones.")
    write_mode("DETENIDO", "NONE", "NONE", {}, telegram=status,
               detail="Todos los motores detenidos; dashboard 24x7 activo")


def copy_output(text):
    """OSC 52 para Termius/terminal: evita seleccionar texto manualmente."""
    if os.getenv("POROTA_COPY_OUTPUT", "1").strip().lower() in {"0", "false", "no", "off"}:
        return
    try:
        encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
        print(f"\033]52;c;{encoded}\a", end="")
    except Exception:
        pass


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
    summary = (f"POROTA_MODE=OK\nACCION={action.upper()}\n"
               f"MANIFIESTO={MODE_FILE}\nDASHBOARD_24X7=SI")
    print(summary)
    copy_output(summary)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BlockingIOError:
        raise SystemExit("Otro cambio de modo esta en curso.")
    except Exception as exc:
        print(f"CAMBIO_DE_MODO=FALLO\nERROR={exc}", file=sys.stderr)
        raise SystemExit(1)