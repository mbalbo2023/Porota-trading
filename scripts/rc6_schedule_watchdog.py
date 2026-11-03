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
DEDUPE_PAGE_SIZE = 5
DEDUPE_MAX_COMMENTS = 500
SAFE_ERROR_CLASSES = frozenset({
    "A358_INVALID_ENDPOINT", "A358_API_RESPONSE_TOO_LARGE",
    "A358_INVALID_POST_TARGET", "A358_DATE_ART_INVALID",
    "A358_COMMENTS_SHAPE_INVALID", "A358_UNBOUNDED_DEDUPE",
    "A358_REPORT_TOO_LARGE", "A358_COMMENT_NOT_CREATED",
})


class A358SafeError(ValueError):
    """Expose a stable code, never a provider error body, URL or token."""

    def __init__(self, code):
        self.code = code if code in SAFE_ERROR_CLASSES else "A358_UNCLASSIFIED_ERROR"
        super().__init__(self.code)


def safe_error_class(exc):
    if isinstance(exc, A358SafeError):
        return exc.code
    if isinstance(exc, HTTPError):
        return "A358_HTTP_ERROR"
    if isinstance(exc, URLError):
        return "A358_NETWORK_ERROR"
    if isinstance(exc, json.JSONDecodeError):
        return "A358_JSON_ERROR"
    if isinstance(exc, OSError):
        return "A358_OS_ERROR"
    if isinstance(exc, KeyError):
        return "A358_KEY_ERROR"
    if isinstance(exc, TypeError):
        return "A358_TYPE_ERROR"
    return "A358_VALUE_ERROR"


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


def api_page_covers_art_day(runs, now, *, page_size=100):
    """A full first API page proves coverage only if it crosses ART midnight."""
    if not isinstance(runs, list) or len(runs) > page_size or now.tzinfo is None:
        return False
    stamps = []
    for run in runs:
        stamp = parse_clock(run.get("created_at")) if isinstance(run, dict) else None
        if stamp is None or stamp > now.astimezone(timezone.utc):
            return False
        stamps.append(stamp)
    if any(previous < following for previous, following in zip(stamps, stamps[1:])):
        return False
    if len(runs) < page_size:
        return True
    local = now.astimezone(ART)
    midnight_art = datetime(local.year, local.month, local.day, tzinfo=ART)
    return stamps[-1] < midnight_art.astimezone(timezone.utc)


def classify(runs, now, *, workflow_state=None, grace_minutes=GRACE_MINUTES,
             api_day_covered=True):
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
              "genuine_in_session": 0, "off_session_runs": 0,
              "missing_mature_run_lower_bound": 0, "api_day_covered": bool(api_day_covered),
              "detection_basis": None,
              "last_genuine_run_at_utc": None, "reason": None, "status": "NO_VERIFICADO"}
    if not expected:
        result.update(status="NOT_APPLICABLE_CLOSED", reason="WEEKEND_OR_NONSESSION")
        return result
    if workflow_state in {"disabled_manually", "disabled_inactivity", "disabled_fork", "deleted"}:
        result.update(status="P1_WORKFLOW_DISABLED", reason="WORKFLOW_NOT_ACTIVE")
        return result
    valid_by_id = {}
    for run in runs:
        if not isinstance(run, dict):
            continue
        if (run.get("event") != "schedule" or run.get("run_attempt") != 1
                or run.get("head_branch") != "main"):
            continue
        stamp = parse_clock(run.get("created_at"))
        if stamp is not None and stamp <= now and stamp.astimezone(ART).date() == local.date():
            run_id = run.get("id")
            if isinstance(run_id, bool) or not isinstance(run_id, int) or run_id <= 0:
                result.update(reason="GENUINE_RUN_ID_MISSING_OR_INVALID")
                return result
            if run_id in valid_by_id and valid_by_id[run_id] != stamp:
                result.update(reason="GENUINE_RUN_ID_CONFLICT")
                return result
            valid_by_id[run_id] = stamp
    valid = sorted(valid_by_id.values())
    result["genuine_runs_today"] = len(valid)
    if valid:
        result["last_genuine_run_at_utc"] = valid[-1].isoformat().replace("+00:00", "Z")
    allowed_start = expected[0]
    allowed_end = expected[-1] + timedelta(minutes=grace_minutes)
    result["genuine_in_session"] = sum(allowed_start <= stamp <= allowed_end for stamp in valid)
    result["off_session_runs"] = len(valid) - result["genuine_in_session"]
    mature = [s for s in expected if s + timedelta(minutes=grace_minutes) <= now]
    # A delayed run does NOT prove each individual cron slot ran. For alarms,
    # only compare current absence since the last genuine creation timestamp.
    last = valid[-1] if valid else expected[0] - timedelta(minutes=1)
    late = [s for s in mature if s > last]
    result["overdue_slots_after_last_run"] = len(late)
    result["mature_expected_slots"] = len(mature)
    result["grace_minutes"] = grace_minutes
    # Cardinality alone gives a conservative lower bound; an event created
    # after the final market slot cannot erase earlier uncreated schedule runs.
    result["missing_mature_run_lower_bound"] = max(0, len(mature) - len(valid))
    if not api_day_covered:
        result.update(status="NO_VERIFICADO", reason="SCHEDULE_API_DAY_INCOMPLETE")
    elif result["missing_mature_run_lower_bound"] >= 2:
        result.update(status="P1_SCHEDULE_SILENT", reason="TWO_MATURE_SLOTS_WITHOUT_RUN",
                      detection_basis="MATURE_RUN_COUNT_LOWER_BOUND")
    elif len(late) >= 2:
        result.update(status="P1_SCHEDULE_SILENT", reason="TWO_MATURE_SLOTS_WITHOUT_RUN",
                      detection_basis="OVERDUE_SINCE_LAST_RUN")
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
        raise A358SafeError("A358_INVALID_ENDPOINT")
    request = Request(url, headers={
        "Accept": "application/vnd.github+json", "User-Agent": "rc6-a358-readonly",
        "X-GitHub-Api-Version": "2022-11-28", "Authorization": "Bearer " + token})
    with urlopen(request, timeout=18) as response:
        raw = response.read(MAX_REPLY + 1)
    if len(raw) > MAX_REPLY:
        raise A358SafeError("A358_API_RESPONSE_TOO_LARGE")
    return json.loads(raw)


def _post(url, data, token):
    if url != f"https://api.github.com/repos/{REPO}/issues/{ISSUE}/comments":
        raise A358SafeError("A358_INVALID_POST_TARGET")
    request = Request(url, data=json.dumps(data).encode(), method="POST", headers={
        "Accept": "application/vnd.github+json", "Content-Type": "application/json",
        "User-Agent": "rc6-a358-readonly", "X-GitHub-Api-Version": "2022-11-28",
        "Authorization": "Bearer " + token})
    with urlopen(request, timeout=18) as response:
        return response.status


def dedupe_issue(status, receipt, token, *, get=_get, post=_post):
    if status not in {"P1_SCHEDULE_SILENT", "P1_WORKFLOW_DISABLED"}:
        return "NOT_REQUIRED"
    date_art = receipt.get("date_art")
    try:
        if not isinstance(date_art, str) or len(date_art) != 10:
            raise A358SafeError("A358_DATE_ART_INVALID")
        midnight_art = datetime.fromisoformat(date_art).replace(tzinfo=ART)
        if midnight_art.date().isoformat() != date_art:
            raise A358SafeError("A358_DATE_ART_INVALID")
    except ValueError as exc:
        raise A358SafeError("A358_DATE_ART_INVALID") from exc
    # GitHub expects UTC timestamps. 00:00 ART is 03:00 UTC, not 00:00 UTC.
    since = midnight_art.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    marker = f"<!--RC6-A358:{date_art}:{status}-->"
    api = f"https://api.github.com/repos/{REPO}/issues/{ISSUE}/comments"
    # A438: tiny pages keep each GitHub response below MAX_REPLY. A full-page
    # boundary must not be mistaken for the end; ambiguity fails closed.
    found_end = False
    max_pages = DEDUPE_MAX_COMMENTS // DEDUPE_PAGE_SIZE
    for page in range(1, max_pages + 1):
        comments = get(api + f"?since={since}&per_page={DEDUPE_PAGE_SIZE}&page={page}", token)
        if not isinstance(comments, list) or len(comments) > DEDUPE_PAGE_SIZE:
            raise A358SafeError("A358_COMMENTS_SHAPE_INVALID")
        if any(not isinstance(c, dict) or not isinstance(c.get("body"), str)
               for c in comments):
            raise A358SafeError("A358_COMMENTS_SHAPE_INVALID")
        if any(marker in c["body"] for c in comments):
            return "ALREADY_REPORTED"
        if len(comments) < DEDUPE_PAGE_SIZE:
            found_end = True
            break
    if not found_end:
        raise A358SafeError("A358_UNBOUNDED_DEDUPE")
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
        raise A358SafeError("A358_REPORT_TOO_LARGE")
    response_code = post(api, {"body": body}, token)
    if response_code != 201:
        raise A358SafeError("A358_COMMENT_NOT_CREATED")
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
        observed_at = datetime.now(timezone.utc)
        result = classify(runs, observed_at, workflow_state=state,
                          api_day_covered=api_page_covers_art_day(runs, observed_at))
        if args.notify:
            result["notification"] = dedupe_issue(result["status"], result, token)
        exit_code = 2 if result["status"].startswith("P1_") else 0
    except (HTTPError, URLError, OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        exit_code = 3
        result["notification"] = "NO_VERIFICADO"
        result["notification_error_class"] = safe_error_class(exc)
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print("RC6_A358_STATUS=" + result["status"])
    print("RC6_A358_REASON=" + str(result["reason"]))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())


