#!/usr/bin/env python3
"""Independent PAPER alert if GitHub's RC6 market schedule goes silent.

This systemd timer job reads GitHub's public Actions metadata, never the trading
database or a provider. Its own state file is separate from trading SQLite.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import stat
import tempfile
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from _version import MODE, EXECUTION, REAL_ORDER_CAPABILITY
from bn_telegram_bus import DeliveryError, TelegramTransport
import ak_byma_calendar as calendar


RUNS_URL = ("https://api.github.com/repos/mbalbo2023/Porota-trading/actions/"
            "workflows/rc6-market-watch-readonly.yml/runs?event=schedule&per_page=3")
JOBS_URL = "https://api.github.com/repos/mbalbo2023/Porota-trading/actions/runs/{}/jobs?per_page=20"
WATCH_STEP = "Collect bounded read-only host truth"
STATE = Path("/app/data/paper_v17/market_watch_silence.json")
ART = ZoneInfo("America/Argentina/Buenos_Aires")
WORKFLOW = ".github/workflows/rc6-market-watch-readonly.yml"
MAX_AGE_SECONDS = 25 * 60  # Two ten-minute intervals plus five minutes of jitter.
COM19031_EFFECTIVE = date(2026, 11, 2)
COM19031_REVIEWED_THROUGH = date(2026, 12, 31)


def active_window(now: datetime) -> bool:
    local = now.astimezone(ART)
    minutes = local.hour * 60 + local.minute
    # First expected scheduled run is 10:37 ART. Give two full intervals.
    # COM19031 regular T+1 equity/CEDEAR trading lasts to 18:00; this is
    # schedule-silence observation, never admission of caucion/FI/other routes.
    end = (18 * 60 + 15 if COM19031_EFFECTIVE <= local.date() <= COM19031_REVIEWED_THROUGH
           else 17 * 60 + 15)
    return (local.year in calendar.ANIOS_AUDITADOS and
            0 <= (local.date()-calendar.CALENDARIO_ACTUALIZADO_EL).days <= 60 and
            calendar.es_dia_habil_operativo(local.date()) and
            11 * 60 + 2 <= minutes < end)


def _bounded_github_json(url: str, *, opener):
    request = Request(url, headers={"Accept": "application/vnd.github+json",
                                     "User-Agent": "porota-rc6-paper-silence/1"})
    with opener(request, timeout=6) as response:
        raw = response.read(131073)
    if len(raw) > 131072:
        raise ValueError("GITHUB_RESPONSE_OVERSIZE")
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ValueError("GITHUB_RESPONSE_INVALID")
    return result


def latest_schedule(*, opener=urlopen):
    payload = _bounded_github_json(RUNS_URL, opener=opener)
    runs = payload.get("workflow_runs")
    if not isinstance(runs, list):
        raise ValueError("GITHUB_RESPONSE_INVALID")
    if not runs:
        return None
    # A queued/in-progress current run is not silence if the previous genuine
    # watch probe completed inside SLA. Bound the search to three schedule runs
    # (the 25m freshness window includes at most three ten-minute intervals).
    for run in runs[:3]:
        if not isinstance(run, dict) or run.get("event") != "schedule" or (
                run.get("path") != WORKFLOW or run.get("head_branch") != "main"):
            raise ValueError("GITHUB_RUN_IDENTITY_INVALID")
        if run.get('status') != 'completed' or run.get('run_attempt') != 1:
            continue
        # A completed *workflow* with a skipped watch job does not prove the
        # host was probed. Require a successful Collect step in that run.
        run_id = run.get("id")
        if type(run_id) is not int or run_id <= 0:
            raise ValueError("GITHUB_RUN_IDENTITY_INVALID")
        jobs = _bounded_github_json(JOBS_URL.format(run_id), opener=opener).get("jobs")
        if not isinstance(jobs, list):
            raise ValueError("GITHUB_JOBS_INVALID")
        for job in jobs:
            if (not isinstance(job, dict) or job.get("name") != "watch"
                    or job.get("status") != "completed" or job.get("conclusion") != "success"):
                continue
            for step in job.get("steps", []):
                if (isinstance(step, dict) and step.get("name") == WATCH_STEP
                        and step.get("status") == "completed"
                        and step.get("conclusion") == "success"):
                    stamp = datetime.fromisoformat(str(step["started_at"]).replace("Z", "+00:00"))
                    if stamp.tzinfo is None:
                        raise ValueError("GITHUB_RUN_CLOCK_INVALID")
                    return stamp.astimezone(timezone.utc)
    return None


def classify(now: datetime, last: datetime | None) -> str:
    if last is None:
        return "SILENT"
    age = (now - last).total_seconds()
    return "OK" if 0 <= age <= MAX_AGE_SECONDS else "SILENT"


def load_state(path=STATE):
    try:
        meta = path.lstat()
    except FileNotFoundError:
        return {}
    if not stat.S_ISREG(meta.st_mode) or meta.st_size > 4096:
        raise ValueError("STATE_UNVERIFIED")
    with path.open("rb") as stream:
        record = json.loads(stream.read(4097))
    message_id = record.get("message_id") if isinstance(record, dict) else None
    if (not isinstance(record, dict) or
            record.get("schema") != "rc6.market-watch-silence.v1" or
            record.get("status") not in {"OK", "SILENT", "UNKNOWN"} or
            not isinstance(message_id, str) or not message_id.isdigit() or
            int(message_id) <= 0):
        raise ValueError("STATE_UNVERIFIED")
    return record


def save_state(path: Path, status: str, message_id: str, now: datetime):
    record = {"schema": "rc6.market-watch-silence.v1", "status": status,
              "acknowledged_at": now.isoformat(), "message_id": message_id}
    fd, name = tempfile.mkstemp(prefix=".market-watch-silence-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(record, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def tick(*, now=None, path=STATE, fetch=latest_schedule, send=None):
    now = now or datetime.now(timezone.utc)
    if (MODE, EXECUTION, REAL_ORDER_CAPABILITY) != (
            "PRODUCTION_PAPER", "SIMULATED", "BLOCKED"):
        return {"status": "BLOCKED_NON_PAPER", "delivery": "NOT_CALLED"}
    if not active_window(now):
        return {"status": "OUTSIDE_WINDOW", "delivery": "NOT_CALLED"}
    try:
        last = fetch()
        status = classify(now, last)
    except (OSError, ValueError, KeyError, TypeError, TimeoutError):
        status, last = "UNKNOWN", None
    try:
        prior = load_state(path)
    except (OSError, ValueError, TypeError):
        return {"status": "STATE_UNVERIFIED", "delivery": "NOT_CALLED"}
    if status == prior.get("status") or (status == "OK" and not prior):
        return {"status": status, "delivery": "NO_TRANSITION"}
    if send is None:
        token, chat = os.getenv("TELEGRAM_BOT_TOKEN", ""), os.getenv("TELEGRAM_CHAT_ID", "")
        if not token or not chat:
            return {"status": status, "delivery": "NOT_CONFIGURED"}
        send = TelegramTransport(token, chat)
    body = ("POROTA RC6 | SIMULACIÓN | MONITOR_SCHEDULE_" + status + "\n"
            "GitHub Actions schedule; PAPER/SHADOW ONLY; este watchdog no llama rutas de órdenes.\n"
            "Última ejecución programada: " + (last.isoformat() if last else "NO_VERIFICADO")
            + "\nUTC: " + now.isoformat()
            + "\nID: RC6-MARKET-WATCH-SILENCE-" + status)
    try:
        mid = send(body)
        if not isinstance(mid, str) or not mid.isdigit() or int(mid) <= 0:
            raise ValueError("MISSING_MESSAGE_ID")
        save_state(path, status, mid, now)
    except (DeliveryError, OSError, ValueError, TypeError):
        return {"status": status, "delivery": "UNVERIFIED"}
    return {"status": status, "delivery": "SENT", "message_id": mid}


if __name__ == "__main__":
    result = tick()
    print(json.dumps(result, sort_keys=True))
    raise SystemExit(0 if result["delivery"] in {"SENT", "NO_TRANSITION", "NOT_CALLED"}
                     and result["status"] not in {"STATE_UNVERIFIED", "BLOCKED_NON_PAPER"}
                     else 2)
