"""A386: independent GitHub schedule-enqueue receipt, without host access.

This script does not invoke brokers, the Droplet, a database, or GitHub APIs.
Only a first-attempt ``schedule`` event on the default branch counts as a
native scheduler observation. Push/PR/manual runs are diagnostics, never proof
that native scheduling has recovered.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping


SCHEMA = "rc6.schedule-enqueue-canary.v1"
EXPECTED_REPOSITORY = "mbalbo2023/Porota-trading"
EXPECTED_CRON = "23,53 13-21 * * 1-5"
EXPECTED_BRANCH_REF = "refs/heads/main"
_SHA = re.compile(r"[a-f0-9]{40}\Z")
_RUN_ID = re.compile(r"[0-9]{1,20}\Z")
_ALLOWED_EVENTS = frozenset({"schedule", "workflow_dispatch", "push", "pull_request"})


def _bounded_string(env: Mapping[str, str], key: str, max_chars: int = 128) -> str:
    raw = str(env.get(key, ""))
    if len(raw) > max_chars or not re.fullmatch(r"[a-zA-Z0-9_./,: *-]*", raw):
        raise ValueError(f"invalid {key} syntax")
    return raw


def _positive_int(env: Mapping[str, str], key: str) -> int:
    raw = str(env.get(key, ""))
    if not _RUN_ID.fullmatch(raw):
        raise ValueError(f"invalid {key}")
    result = int(raw)
    if result < 1:
        raise ValueError(f"invalid {key}")
    return result


def derive_receipt(
    env: Mapping[str, str], now: datetime | None = None
) -> dict[str, object]:
    """Classify the event using only GitHub's immutable run context.

    The receipt deliberately omits names, tokens, provider details and payload
    contents, and can never attest PAPER runtime or any other trading state.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    utc = now.astimezone(timezone.utc)
    event = _bounded_string(env, "GITHUB_EVENT_NAME", 32)
    expression = _bounded_string(env, "GITHUB_EVENT_SCHEDULE")
    ref = _bounded_string(env, "GITHUB_REF")
    sha = _bounded_string(env, "GITHUB_SHA", 40)
    repo = _bounded_string(env, "GITHUB_REPOSITORY")
    if event not in _ALLOWED_EVENTS:
        raise ValueError("unsupported GitHub event")
    if not _SHA.fullmatch(sha):
        raise ValueError("invalid GitHub SHA")
    if repo != EXPECTED_REPOSITORY:
        raise ValueError("unexpected GitHub repository")
    attempt = _positive_int(env, "GITHUB_RUN_ATTEMPT")
    run_id = _positive_int(env, "GITHUB_RUN_ID")
    genuine = (
        event == "schedule"
        and expression == EXPECTED_CRON
        and ref == EXPECTED_BRANCH_REF
        and attempt == 1
    )
    if event == "schedule" and not genuine:
        classification = "INVALID_NATIVE_SCHEDULE_CONTEXT"
    elif genuine:
        classification = "NATIVE_SCHEDULE_EVENT_SEEN"
    else:
        classification = "DIAGNOSTIC_EVENT_ONLY"

    return {
        "schema": SCHEMA,
        "read_only": True,
        "safety_scope": "GITHUB_RUN_METADATA_ONLY",
        "repository": repo,
        "run_id": run_id,
        "run_attempt": attempt,
        "github_event": event,
        "event_schedule": expression if event == "schedule" else None,
        "workflow_sha": sha,
        "workflow_ref": ref,
        "observed_utc": utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "observed_utc_in_nominal_window": (
            utc.weekday() < 5 and 13 <= utc.hour <= 21
        ),
        "native_schedule_event": genuine,
        "classification": classification,
        "market_watch_scheduler_recovered": "NO_VERIFICADO",
        "paper_runtime": "NO_VERIFICADO",
        "real_orders_sent": "NO_VERIFICADO",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    receipt = derive_receipt(os.environ)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print("A386_CANARY_CLASSIFICATION=" + str(receipt["classification"]))
    print("A386_CANARY_EVENT=" + str(receipt["github_event"]))
    print("A386_CANARY_RUN_ID=" + str(receipt["run_id"]))
    return 2 if receipt["classification"] == "INVALID_NATIVE_SCHEDULE_CONTEXT" else 0


if __name__ == "__main__":
    raise SystemExit(main())
