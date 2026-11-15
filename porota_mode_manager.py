#!/usr/bin/env python3
"""Selector exclusivo de modo operativo para Porota Trading v17 candidata.

Uso:
  python porota_mode_manager.py status
  python porota_mode_manager.py simulation
  python porota_mode_manager.py sandbox
  python porota_mode_manager.py stop

GitHub Actions invoca el manager como porotaadmin; sólo la API root-owned
/usr/local/sbin/porota-rc6-oci-custody recibe sudo -n. Un checkout no se
ejecuta como root para operar contenedores.

Produccion real permanece deshabilitada permanentemente por politica del proyecto.
"""

from __future__ import annotations

import fcntl
import base64
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo
from cg_paper_workspace import DB_ENV, CONTAINER_DB, IMAGE
from scripts.rc6_operator_paper_rearm import OPERATOR_DIR_HOST


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
MODE_FILE = DATA / "operation_mode.json"
TZ = ZoneInfo(os.getenv("SERVER_TIMEZONE", "America/Argentina/Buenos_Aires"))


def operator_readonly_mount():
    """Create/attest a root-only host authority outside writable PAPER DATA.

    The authorization file itself is absent until a later exact, authenticated
    GitHub Actions rearm. Creating an empty readonly mount never grants GO.
    """
    if os.geteuid() != 0:
        raise RuntimeError("PAPER_OPERATOR_ROOT_REQUIRED")
    root = OPERATOR_DIR_HOST
    if os.path.islink(root) or os.path.islink(root.parent):
        raise RuntimeError("PAPER_OPERATOR_DIRECTORY_ALIAS")
    root.mkdir(mode=0o755, exist_ok=True)
    observed = os.lstat(root)
    if (not stat.S_ISDIR(observed.st_mode) or observed.st_uid != 0 or
            observed.st_mode & 0o022 or observed.st_mode & 0o555 != 0o555):
        raise RuntimeError("PAPER_OPERATOR_DIRECTORY_UNTRUSTED")
    return ("--mount", f"type=bind,source={root},target=/app/operator,readonly")
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
    "PAPER_SCALPING_MODE": "ACTIVE_PAPER",
    "PAPER_INTRADAY_SCAN_SECONDS": "180", "PAPER_INTRADAY_BATCH_LIMIT": "24",
    "PAPER_SCALPING_MIN_NET_MARGIN": "0.005",
    "PAPER_SCALPING_MAX_SPREAD": "0.005",
    "PAPER_SCALPING_SCORE_THRESHOLD": "0.68",
    "PAPER_SCALPING_RISK_PER_TRADE": "0.001",
    "PAPER_SCALPING_MAX_OPEN_POSITIONS": "1",
    "PAPER_SCALPING_MAX_HOLD_MINUTES": "30",
    "PAPER_SCALPING_STOP_LOSS_PCT": "0.008",
    "PAPER_SCALPING_TARGET_GAIN_PCT": "0.02",
    "PAPER_CAUCION_SWEEP_MODE": "ACTIVE_PAPER",
    "PAPER_CAUCION_SWEEP_START_MINUTES_BEFORE_CLOSE": "30",
    "PAPER_CAUCION_SWEEP_CUTOFF_MINUTES_BEFORE_CLOSE": "5",
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
    "PAPER_SCALPING_MODE": "ACTIVE_PAPER",
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
    "PAPER_CAUCION_SWEEP_MODE": "ACTIVE_PAPER",
    "PAPER_CAUCION_SWEEP_START_MINUTES_BEFORE_CLOSE": "30",
    "PAPER_CAUCION_SWEEP_CUTOFF_MINUTES_BEFORE_CLOSE": "5",
}


# Only explicit non-secret selector keys cross the host environment into the
# PAPER observer. Forwarding a path never approves capacity or creates a ledger.
PPI_BUDGET_OBSERVER_SELECTORS = (
    "POROTA_DYNAMIC_CAPACITY_MODE",
    "POROTA_CAPACITY_POLICY_PATH",
    "POROTA_CAPACITY_REPORT_PATH",
    "POROTA_CAPACITY_RECOMMENDATION_PATH",
    "POROTA_CAPACITY_APPROVAL_PATH",
    "POROTA_CAPACITY_SHADOW_PATH",
    "POROTA_PPI_STATIC_SHARED_AUTHORITY_PATH",
)


def ppi_budget_observer_selectors(env):
    """Forward explicit, validated selectors only; never broker credentials."""
    values = {}
    for key in PPI_BUDGET_OBSERVER_SELECTORS:
        raw = env.get(key)
        if raw is None:
            continue
        if not isinstance(raw, str) or any(ord(ch) < 32 or ord(ch) == 127 for ch in raw):
            raise RuntimeError("RC6_PPI_BUDGET_SELECTOR_INVALID")
        value = raw.strip()
        if not value:
            continue
        if key == "POROTA_DYNAMIC_CAPACITY_MODE":
            value = value.upper()
            if value not in {"OFF", "SHADOW", "APPROVED"}:
                raise RuntimeError("RC6_PPI_BUDGET_MODE_INVALID")
        elif not Path(value).is_absolute():
            raise RuntimeError("RC6_PPI_BUDGET_PATH_NOT_ABSOLUTE")
        values[key] = value
    return values


def paper_settings(env):
    values = {key: env.get(key, "").strip() or default for key, default in PAPER_DEFAULTS.items()}
    values.update(RC6_FROZEN_PAPER_SETTINGS)
    return values


def run(*args, check=True, capture=False):
    # Compatibility HOLD only: all Docker operations are delegated to the
    # independently installed typed helper. No candidate-owned arbitrary
    # subprocess runner remains as an alternate privilege path.
    del args, check, capture
    raise SystemExit(79)


HOST_CUSTODY_HELPER = "/usr/local/sbin/porota-rc6-oci-custody"


def _helper_call(*args, attempt=None):
    """Call an installed, independently root-owned, typed host API only.

    Untrusted checkout source never executes as root and never supplies Docker
    arguments. Only a separately verified host installation may hold custody.
    """
    valid = (args in (("manager", "begin"),
                      ("manager", "verify-observer-sources"),
                      ("manager", "stop", "observer")) or
             len(args) == 3 and args[:2] in (("manager", "retire"),
                                               ("manager", "birth"),
                                               ("manager", "verify-newborn")) and
             args[2] in ("dashboard", "observer") or
             len(args) == 4 and args[:2] == ("manager", "verify-image") and
             re.fullmatch(r"porota-trading-bot:17\.0\.0-rc6-candidate-[0-9a-f]{40}",
                          args[2]) is not None and
             re.fullmatch(r"sha256:[0-9a-f]{64}", args[3]) is not None)
    is_begin = args == ("manager", "begin")
    # A per-generation token is an expectation, never a permission. The
    # root-owned helper verifies it against current intent under its lock on
    # *every* call, rejecting a new attempt interleaved after this begin.
    if (not valid or
            (attempt is not None if is_begin else
             not (type(attempt) is str and
                  re.fullmatch(r"[0-9a-f]{32}", attempt) is not None))):
        raise SystemExit(79)
    command = ("/usr/bin/sudo", "-n", HOST_CUSTODY_HELPER, *args,
               *(("--attempt", attempt) if not is_begin else ()))
    try:
        result = subprocess.run(command, cwd="/", check=False, text=True,
                                timeout=120, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL,
                                env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"})
    except (OSError, subprocess.SubprocessError) as exc:
        raise SystemExit(79) from exc
    answer = result.stdout.strip() if isinstance(result.stdout, str) else ""
    valid_reply = (re.fullmatch(
        r"RC6_CUSTODY=(GENESIS|CONTINUITY)\nRC6_CUSTODY_ATTEMPT=[0-9a-f]{32}", answer)
        is not None if is_begin else answer == "RC6_CUSTODY=OK")
    if result.returncode != 0 or not valid_reply:
        raise SystemExit(79)
    return answer


class RootHelperSession:
    """Unprivileged typed client; the root-owned installer is authority."""

    def __init__(self):
        answer = _helper_call("manager", "begin")
        mode, attempt = answer.splitlines()
        self._phase = mode.partition("=")[2]
        self._attempt = attempt.partition("=")[2]

    def verify_candidate(self, tag, digest):
        _helper_call("manager", "verify-image", tag, digest,
                     attempt=self._attempt)

    def retire(self, role):
        if self._phase == "GENESIS":
            # No predecessor has authenticated birth custody. Names, labels,
            # images and a healthy legacy unit cannot authorize its removal.
            return
        _helper_call("manager", "retire", role, attempt=self._attempt)

    def birth(self, role):
        # The helper must create + record the CIDFILE and journal as one typed
        # attempt. No Docker argument, CID, mount or birth authority enters
        # candidate-controlled code.
        _helper_call("manager", "birth", role, attempt=self._attempt)
        _helper_call("manager", "verify-newborn", role, attempt=self._attempt)

    def verify_observer_sources(self):
        _helper_call("manager", "verify-observer-sources", attempt=self._attempt)

    def stop_observer(self):
        _helper_call("manager", "stop", "observer", attempt=self._attempt)


def _begin_custody():
    return RootHelperSession()


def _custody_session(session):
    return _begin_custody() if session is None else session


def simulation_image(*, _session=None):
    """Verify the frozen Deploy identity with root-owned host authority."""
    candidate = os.getenv("POROTA_DEPLOY_CANDIDATE_IMAGE", "")
    expected = os.getenv("POROTA_DEPLOY_CANDIDATE_IMAGE_ID", "")
    if not candidate and not expected:
        if _session is not None:
            raise SystemExit(79)
        return IMAGE
    if (re.fullmatch(r"porota-trading-bot:17\.0\.0-rc6-candidate-[0-9a-f]{40}",
                     candidate) is None or
            re.fullmatch(r"sha256:[0-9a-f]{64}", expected) is None):
        raise RuntimeError("RC6_DEPLOY_CANDIDATE_IMAGE_INVALID")
    _custody_session(_session).verify_candidate(candidate, expected)
    return candidate


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


def stop_engines(include_dashboard=True, *, _session=None):
    session = _custody_session(_session)
    session.retire("observer")
    if include_dashboard:
        session.retire("dashboard")
    # Legacy preview, sandbox and compose names have no birth-proof role;
    # neither inspection nor a friendly-looking name permits stopping them.


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
        if not key.startswith(forbidden) and key not in {"DASHBOARD_OPERATION_MODE", "PAPER_DB_PATH", DB_ENV,
                "POROTA_BUILD_SHA", "POROTA_CANDIDATE_TREE_SHA", *PAPER_DEFAULTS,
                *PPI_BUDGET_OBSERVER_SELECTORS}:
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
    budget_selectors = ppi_budget_observer_selectors(env)
    target = DATA / "diagnosticos" / "observer_runtime_v17.env"
    values = {
        "TELEGRAM_BOT_TOKEN": env.get("TELEGRAM_BOT_TOKEN", "").strip(),
        "TELEGRAM_CHAT_ID": env.get("TELEGRAM_CHAT_ID", "").strip(),
    }
    values.update(paper_settings(env))
    values.update(budget_selectors)
    # Contract Evidence recolecta PPI read-only para todas las familias auditables.\n    # Nunca habilita decisiones ni órdenes; las familias fuera de alcance siguen fail-closed.\n    values["POROTA_CONTRACT_EVIDENCE_MODE"] = env.get("POROTA_CONTRACT_EVIDENCE_MODE", "ENABLED").strip().upper() or "ENABLED"\n    # RC6 settlement hotfix: autoridad explícita sólo en el observer PAPER.
    # El módulo de settlement permanece fail-closed fuera de este runtime.
    values["PAPER_T1_FULL_DATE_RELEASE"] = "true"
    values[DB_ENV] = CONTAINER_DB
    target.write_text("\n".join(f"{name}={value}" for name, value in values.items()) + "\n",
                      encoding="utf-8")
    os.chmod(target, 0o600)
    return target


def start_dashboard(mode, image=IMAGE, *, _session=None, _prior_retired=False):
    # Exact image digest, mounts, environment and Docker CLI are rooted in the
    # installed helper's immutable stage manifest. Caller-supplied `image` is
    # retained for old API compatibility but is never a mutation authority.
    del image
    session = _custody_session(_session)
    if mode != "PRODUCTION_PAPER":
        raise SystemExit(79)
    dashboard_env(mode)
    if not _prior_retired:
        session.retire("dashboard")
    session.birth("dashboard")
    # No Docker CLI, name, CIDFILE, mount list or runtime hash is assembled by
    # this candidate code. Helper `birth` and `verify-newborn` do the checks.


def simulation(*, _session=None):
    # Pure configuration validation can reject an unsafe selector without
    # touching Docker or writing runtime files, even while custody is absent.
    ppi_budget_observer_selectors(env_file())
    session = _custody_session(_session)
    # A root-owned three-role attempt is required before even inspecting the
    # image or writing a local env file. No env flag can bypass this gate.
    image = simulation_image(_session=session)
    secret = ROOT / ".secrets" / "ppi_production.json"
    if not secret.exists():
        raise RuntimeError("Falta el secreto productivo de solo lectura.")
    observer_runtime_env()
    stop_engines(_session=session)
    write_mode("PENDING_CUSTODY_SEAL", "NONE", "NONE",
               {"PPI_PRODUCTION": "MARKET_DATA_READ_ONLY", "TELEGRAM": "MODE_NOTIFICATIONS_ONLY",
                "PPI_ORDERS": "BLOCKED", "PYTHON_MATH_ENGINE": "ACTIVE"}, detail="Iniciando")
    start_dashboard("PRODUCTION_PAPER", image=image, _session=session, _prior_retired=True)
    session.birth("observer")
    session.verify_observer_sources()
    # A1031 DEPLOY_OWNER alone seals and validates all 3 birth roles through
    # Deploy V2. This manager publishes only an explicit Iniciando/PENDING state;
    # no activation notification or READY evidence comes from two births.


def sandbox():
    # PAPER/SHADOW only. The legacy compose sandbox has no authenticated
    # birth role, so it must never be started or retired on a shared daemon.
    raise SystemExit(79)


def production(argv):
    del argv
    raise RuntimeError(
        "PRODUCCION REAL DESHABILITADA PERMANENTEMENTE: Porota opera solamente "
        "SANDBOX o PRODUCTION_PAPER con ejecucion simulada."
    )


def stop(*, _session=None):
    session = _custody_session(_session)
    # Helper requires a sealed exact-CID observer, never a name or a pending
    # genesis role. Dashboard and the critical Telegram plane remain intact.
    session.stop_observer()
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
    # An unauthenticated global Docker inventory would expose unrelated
    # applications and still could not establish a container's birth owner.


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    action = argv[0].lower() if argv else "status"
    if action == "sandbox":
        sandbox()  # PAPER-only HOLD before lock-file or Docker side effects.
    if action in ("production", "produccion"):
        production(argv[1:])
    lock_path = DATA / "diagnosticos" / "porota_mode.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Local mode decisions are serialized *before* invoking the trusted
        # helper. The host helper separately locks and binds its root intent.
        session = (_begin_custody() if action in
                   ("simulation", "simulacion", "stop", "detenido") else None)
        if action in ("simulation", "simulacion"):
            simulation(_session=session)
        elif action in ("stop", "detenido"):
            stop(_session=session)
        elif action == "status":
            status()
        else:
            raise SystemExit("Uso: porota-mode <status|simulation|sandbox|stop|production>")
    result = ("PENDING_CUSTODY_SEAL" if action in ("simulation", "simulacion")
              else "NO_VERIFICADO" if action == "status" else "OK")
    summary = (f"POROTA_MODE={result}\nACCION={action.upper()}\n"
               f"MANIFIESTO={MODE_FILE}\nDASHBOARD_24X7=NO_VERIFICADO")
    print(summary)
    copy_output(summary)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BlockingIOError:
        raise SystemExit("Otro cambio de modo esta en curso.")
    except Exception as exc:
        if getattr(exc, "exit_code", None) == 79:
            print("CAMBIO_DE_MODO=HOLD\nERROR=RC6_OCI_CUSTODY_HOLD79", file=sys.stderr)
            raise SystemExit(79)
        print(f"CAMBIO_DE_MODO=FALLO\nERROR={exc}", file=sys.stderr)
        raise SystemExit(1)
