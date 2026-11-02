"""RC6 A358: GitHub-only independent heartbeat for Market Watch schedule.

Reads GitHub Actions API, never connects to a broker, droplet, or trading DB.
It cannot make GitHub's own schedule reliable; an external consumer must also
monitor its alerts and absence. Every output is sanitized and bounded.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

REPO = "mbalbo2023/Porota-trading"
WORKFLOW_ID = 384354765
ISSUE = 732
ART = ZoneInfo("America/Argentina/Buenos_Aires")
MAX_REPLY = 512 * 1024
GRACE_MINUTES = 12


def parse_clock(value):
    if not isinstance(value, str):
        return None
    try:
        d = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return d.astimezone(timezone.utc) if d.tzinfo else None
    except ValueError:
        return None


def slots(day):
    """Only useful Market Watch slots; ignore before 10:30 ART."""
    base = datetime(day.year, day.month, day.day, tzinfo=ART)
    if base.weekday() >= 5:
        return []
    minutes = [(h, m) for h in range(10, 18) for m in (7, 17, 27, 37, 47, 57)
               if (h > 10 or m >= 30)]
    minutes.append((18, 7))
    return [base.replace(hour=h, minute=m).astimezone(timezone.utc)
            for h, m in minutes]


def classify(runs, now, *, workflow_state=None, grace_minutes=GRACE_MINUTES):
    if now.tzinfo is None:
        raise ValueError("A358_CLOCK_NAIVE")
    now = now.astimezone(timezone.utc)
    local = now.astimezone(ART)
    expected = slots(local.date())
    result = {"schema": "rc6.a358.schedule-watch.v1", "date_art": local.date().isoformat(),
              "observed_at_utc": now.isoformat().replace("+00:00", "Z"),
              "read_only": True, "workflow_id": WORKFLOW_ID,
              "workflow_state": workflow_state or "NO_VERIFICADO",
              "event_required": "schedule", "first_attempt_required": 1,
              "real_order_routes_called": 0, "host_access": False,
              "genuine_runs_today": 0, "overdue_slots_after_last_run": 0,
              "last_genuine_run_at_utc": None, "reason": None, "status": "NO_VERIFICADO"}
    if not expected:
        result.update(status="NOT_APPLICABLE_CLOSED", reason="WEEKEND_OR_NONSESSION")
        return result
    if workflow_state in {"disabled_manually", "disabled_inactivity", "disabled_fork", "deleted"}:
        result.update(status="P1_WORKFLOW_DISABLED", reason="WORKFLOW_NOT_ACTIVE")
        return result
    valid = []
    for run in runs:
        if not isinstance(run, dict):
            continue
        if (run.get("event") != "schedule" or run.get("run_attempt") != 1
                or run.get("head_branch") != "main"):
            continue
        stamp = parse_clock(run.get("created_at"))
        if stamp is not None and stamp <= now and stamp.astimezone(ART).date() == local.date():
            valid.append(stamp)
    valid = sorted(set(valid))
    result["genuine_runs_today"] = len(valid)
    if valid:
        result["last_genuine_run_at_utc"] = valid[-1].isoformat().replace("+00:00", "Z")
    mature = [s for s in expected if s + timedelta(minutes=grace_minutes) <= now]
    # A delayed run does NOT prove each individual cron slot ran. For alarms,
    # only compare current absence since the last genuine creation timestamp.
    last = valid[-1] if valid else expected[0] - timedelta(minutes=1)
    late = [s for s in mature if s > last]
    result["overdue_slots_after_last_run"] = len(late)
    result["mature_expected_slots"] = len(mature)
    result["grace_minutes"] = grace_minutes
    if len(late) >= 2:
        result.update(status="P1_SCHEDULE_SILENT", reason="TWO_MATURE_SLOTS_WITHOUT_RUN")
    elif len(valid) >= 2:
        gaps = [(a - b).total_seconds() for a, b in zip(valid[1:], valid[:-1])]
        result["max_observed_gap_seconds"] = int(max(gaps)) if gaps else 0
        if gaps and gaps[-1] <= 1200 and len(late) == 0:
            result.update(status="RECENT_SCHEDULE_OBSERVED", reason="TWO_GENUINE_FIRST_ATTEMPTS")
        else:
            result.update(status="NO_VERIFICADO", reason="GAP_OR_INSUFFICIENT_CADENCE")
    else:
        result.update(status="NO_VERIFICADO", reason="INSUFFICIENT_SCHEDULE_EVIDENCE")
    return result


def _get(url, token):
    if not url.startswith(f"https://api.github.com/repos/{REPO}/"):
        raise ValueError("A358_INVALID_ENDPOINT")
    request = Request(url, headers={
        "Accept": "application/vnd.github+json", "User-Agent": "rc6-a358-readonly",
        "X-GitHub-Api-Version": "2022-11-28", "Authorization": "Bearer " + token})
    with urlopen(request, timeout=18) as response:
        raw = response.read(MAX_REPLY + 1)
    if len(raw) > MAX_REPLY:
        raise ValueError("A358_API_RESPONSE_TOO_LARGE")
    return json.loads(raw)


def _post(url, data, token):
    if url != f"https://api.github.com/repos/{REPO}/issues/{ISSUE}/comments":
        raise ValueError("A358_INVALID_POST_TARGET")
    request = Request(url, data=json.dumps(data).encode(), method="POST", headers={
        "Accept": "application/vnd.github+json", "Content-Type": "application/json",
        "User-Agent": "rc6-a358-readonly", "X-GitHub-Api-Version": "2022-11-28",
        "Authorization": "Bearer " + token})
    with urlopen(request, timeout=18) as response:
        return response.status


def dedupe_issue(status, receipt, token, *, get=_get, post=_post):
    if status not in {"P1_SCHEDULE_SILENT", "P1_WORKFLOW_DISABLED"}:
        return "NOT_REQUIRED"
    marker = f"<!--RC6-A358:{receipt['date_art']}:{status}-->"
    api = f"https://api.github.com/repos/{REPO}/issues/{ISSUE}/comments"
    since = receipt["date_art"] + "T00:00:00Z"
    # Bounded scan; ambiguity fails closed, never duplicates P1 comments.
    found_end = False
    for page in range(1, 6):
        comments = get(api + f"?since={since}&per_page=100&page={page}", token)
        if not isinstance(comments, list) or len(comments) > 100:
            raise ValueError("A358_COMMENTS_SHAPE_INVALID")
        if any(marker in str(c.get("body", "")) for c in comments if isinstance(c, dict)):
            return "ALREADY_REPORTED"
        if len(comments) < 100:
            found_end = True
            break
    if not found_end:
        raise ValueError("A358_UNBOUNDED_DEDUPE")
    body = (f"{marker}\n## A358 P1 — Market Watch schedule ({receipt['date_art']} ART)\n"
            f"**Estado:** \x60{status}\x60. **RCA del trigger externo:** \x60NO_VERIFICADO\x60. "
            "Un cron declarado no equivale a ejecuciones reales.\n\n"
            f"Corte UTC: \x60{receipt['observed_at_utc']}\x60; workflow state: "
            f"\x60{receipt['workflow_state']}\x60; runs genuinos hoy: "
            f"\x60{receipt['genuine_runs_today']}\x60; intervalos maduros sin run posterior: "
            f"\x60{receipt['overdue_slots_after_last_run']}\x60; grace: "
            f"\x60{receipt.get('grace_minutes', GRACE_MINUTES)}min\x60.\n\n"
            "**Acción:** revisar habilitación del workflow y actor; reparar la "
            "cadencia en el plano de control, sin atribuir culpa a GitHub sin evidencia; "
            "exigir dos \x60event=schedule\x60 genuinos y recibos de host independientes. "
            "Sin SSH/DB/PPI/broker/órdenes ni Deploy. \x60VALIDADO_RUNTIME=NO_VERIFICADO\x60.\n")
    if len(body) > 2000:
        raise ValueError("A358_REPORT_TOO_LARGE")
    response_code = post(api, {"body": body}, token)
    if response_code != 201:
        raise ValueError("A358_COMMENT_NOT_CREATED")
    return "ISSUE_COMMENT_CREATED"


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--output", required=True)
    p.add_argument("--notify", action="store_true")
    args = p.parse_args(argv)
    token = os.environ.get("GH_TOKEN", "")
    result = {"schema": "rc6.a358.schedule-watch.v1", "status": "NO_VERIFICADO",
              "reason": "A358_API_UNAVAILABLE", "read_only": True, "host_access": False,
              "real_order_routes_called": 0}
    exit_code = 0
    try:
        if not token or len(token) < 10:
            raise ValueError("A358_TOKEN_UNAVAILABLE")
        root = f"https://api.github.com/repos/{REPO}/actions"
        meta = _get(root + f"/workflows/{WORKFLOW_ID}", token)
        state = str(meta.get("state", "NO_VERIFICADO"))
        if state not in {"active", "disabled_manually", "disabled_inactivity", "disabled_fork", "deleted", "NO_VERIFICADO"}:
            state = "NO_VERIFICADO"
        payload = _get(root + f"/workflows/{WORKFLOW_ID}/runs?event=schedule&per_page=100", token)
        runs = payload.get("workflow_runs")
        if not isinstance(runs, list) or len(runs) > 100:
            raise ValueError("A358_RUNS_SHAPE_INVALID")
        result = classify(runs, datetime.now(timezone.utc), workflow_state=state)
        if args.notify:
            result["notification"] = dedupe_issue(result["status"], result, token)
        exit_code = 2 if result["status"].startswith("P1_") else 0
    except (HTTPError, URLError, OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        exit_code = 3
        result["notification"] = "NO_VERIFICADO"
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print("RC6_A358_STATUS=" + result["status"])
    print("RC6_A358_REASON=" + str(result["reason"]))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
