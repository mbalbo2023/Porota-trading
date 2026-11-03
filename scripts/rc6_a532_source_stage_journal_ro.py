#!/usr/bin/env python3
"""A532: fixed-enum SOURCE_CYCLE stage diagnoses from existing host journal.

This is a bounded diagnostic, NOT proof of provider delivery or timer cadence.
The raw journal MESSAGE is read ONLY on the host and never exported. Only the
pre-existing dispatcher marker RC6_SOURCE_CYCLE_DISPATCH= is considered;
anything ambiguous fails closed. No provider, DB, PPI Watch or order calls.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import selectors
import subprocess
import sys
import time
from zoneinfo import ZoneInfo


SCHEMA = "rc6.a532.source-cycle-stage-journal-readonly.v1"
ART = ZoneInfo("America/Argentina/Buenos_Aires")
UNIT = "porota-iol-shadow-collector-rc6.service"
MARKER = "RC6_SOURCE_CYCLE_DISPATCH="
LOOKBACK_MINUTES = 45
MAX_JOURNAL_LINES = 80
MAX_JOURNAL_BYTES = 64 * 1024
MAX_RECORD_BYTES = 4096
MAX_WAIT_SECONDS = 4
STAMP = re.compile(r"[0-9]{1,20}\Z")
RESULT_STATES = frozenset({"COMPLETE", "DEGRADED", "BLOCKED", "NOT_DUE"})
STAGE_NAMES = ("iol", "public", "evidence")
STAGE_STATES = frozenset({
    "OK", "NOT_DUE_TTL", "EXECUTION_UNVERIFIED", "FAILED", "COLLECTOR_NOT_COMPLETED",
    "NOT_DUE_LATE_EXACT_CONTRACT", "OUTPUT_NOT_REPUBLISHED", "OUTPUT_UNVERIFIED",
    "NOT_REPORTED",
})
# A542: IOL is optional, and absent OAuth is a diagnostic failure of IOL
# ONLY; PUBLIC/EVIDENCE retain independent TTL execution. Never promote this
# marker to provider readiness or accept it as COMPLETE.
IOL_STAGE_STATES = STAGE_STATES | frozenset({"COMPLEMENT_AUTH_UNAVAILABLE"})
REASONS = frozenset({
    "PAPER_OBSERVE_ONLY", "COM19031_EXACT_LATE_T1_OBSERVE_ONLY",
    "PAPER_DATABASE_UNVERIFIED", "PAPER_DATABASE_IDENTITY_UNVERIFIED", "PAPER_ZERO_REQUIRED",
    "SOURCE_CYCLE_LOCK_BUSY", "SOURCE_CYCLE_LOCK_UNSAFE", "SOURCE_CYCLE_LOCK_PATH_UNSAFE",
    "SOURCE_CYCLE_OUTPUT_ROOT_UNSAFE", "SOURCE_CYCLE_STATE_UNSAFE", "SOURCE_CYCLE_STATE_INVALID",
    "SOURCE_CYCLE_STATE_SCHEMA_INVALID", "SOURCE_CYCLE_SESSION_DATE_INVALID",
    "SOURCE_CYCLE_CLOCK_INVALID", "SOURCE_CYCLE_STATE_ALIAS", "SOURCE_CYCLE_OUTPUT_UNSAFE",
    "OBSERVE_ONLY_REQUIRED", "SOURCE_CYCLE_PATH_OVERRIDE_DIVERGED",
    "NOT_DUE_PHASE", "OTHER_OR_UNVERIFIED", "NOT_REPORTED",
})
AGE_BUCKETS = frozenset({"WITHIN_2M", "WITHIN_10M", "WITHIN_45M", "UNKNOWN"})


def default_receipt() -> dict[str, object]:
    return {
        "schema": SCHEMA, "read_only": True, "host_metadata_only": True,
        "raw_journal_exported": False,
        "journal_message_access_scope": "HOST_ONLY_IF_AVAILABLE",
        "database_opened": False, "provider_calls": 0,
        "ppi_watch_touched": False, "order_routes_called": False,
        "real_orders_sent_by_action": 0, "paper_ready": False,
        "journal_scope": "EXACT_SOURCE_CYCLE_SERVICE_LAST_45M",
        "marker": "NO_VERIFICADO", "reported_status": "NO_VERIFICADO",
        "reported_reason": "NOT_REPORTED", "stage_iol": "NOT_REPORTED",
        "stage_public": "NOT_REPORTED", "stage_evidence": "NOT_REPORTED",
        "marker_age": "UNKNOWN", "cadence_verified": False,
        "ppi_book_verified": False,
    }


def journal_command(now: datetime) -> list[str]:
    """Fixed unit and bounded time, no user-provided shell or source target."""
    if now.tzinfo is None:
        raise ValueError("AWARE_CLOCK_REQUIRED")
    since = (now - timedelta(minutes=LOOKBACK_MINUTES)).astimezone(timezone.utc)
    until = now.astimezone(timezone.utc)
    return [
        "/usr/bin/journalctl", "--quiet", "--no-pager", "--output=json",
        "--output-fields=__REALTIME_TIMESTAMP,_SYSTEMD_UNIT,MESSAGE",
        "--unit", UNIT, "--grep=^RC6_SOURCE_CYCLE_DISPATCH=",
        f"--since={since.strftime('%Y-%m-%d %H:%M:%S UTC')}",
        f"--until={until.strftime('%Y-%m-%d %H:%M:%S UTC')}",
        f"--lines={MAX_JOURNAL_LINES}",
    ]


def read_journal_bounded(args: list[str]) -> bytes | None:
    """Bound in-memory host-side read by bytes and deadline; never relay raw."""
    proc = None
    watcher = selectors.DefaultSelector()
    try:
        proc = subprocess.Popen(args, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                close_fds=True)
        assert proc.stdout is not None
        os.set_blocking(proc.stdout.fileno(), False)
        watcher.register(proc.stdout, selectors.EVENT_READ)
        chunks: list[bytes] = []
        length = 0
        deadline = time.monotonic() + MAX_WAIT_SECONDS
        eof = False
        while not eof:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            if not watcher.select(timeout=remaining):
                return None
            chunk = os.read(proc.stdout.fileno(), 4096)
            if not chunk:
                eof = True
                break
            length += len(chunk)
            if length > MAX_JOURNAL_BYTES:
                return None
            chunks.append(chunk)
        if proc.wait(timeout=max(0.01, deadline - time.monotonic())) != 0:
            return None
        return b"".join(chunks)
    except (OSError, ValueError, subprocess.TimeoutExpired, AssertionError):
        return None
    finally:
        watcher.close()
        if proc is not None:
            if proc.poll() is None:
                proc.kill()
            try:
                proc.wait(timeout=1)
            except (OSError, subprocess.TimeoutExpired):
                pass
            if proc.stdout is not None:
                proc.stdout.close()


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value = {}
    for name, item in pairs:
        if name in value:
            raise ValueError("DUPLICATE_JSON_KEY")
        value[name] = item
    return value


def _parsed_marker(message: str) -> tuple[str, str, dict[str, str]] | None:
    if not isinstance(message, str) or len(message) > 2048 or not message.startswith(MARKER):
        return None
    data = json.loads(message[len(MARKER):], object_pairs_hook=_unique)
    if not isinstance(data, dict) or data.get("status") not in RESULT_STATES:
        return None
    stage_data = data.get("stages")
    if not isinstance(stage_data, dict) or not set(stage_data).issubset(STAGE_NAMES):
        return None
    if any(not isinstance(val, str) or val not in (
            IOL_STAGE_STATES if name == "iol" else STAGE_STATES) - {"NOT_REPORTED"}
           for name, val in stage_data.items()):
        return None
    state = data["status"]
    if state in {"COMPLETE", "DEGRADED"} and not stage_data:
        return None
    if state == "COMPLETE" and any(val not in {"OK", "NOT_DUE_TTL"}
                                   for val in stage_data.values()):
        return None
    if state == "DEGRADED" and all(val in {"OK", "NOT_DUE_TTL"}
                                   for val in stage_data.values()):
        return None
    if state in {"BLOCKED", "NOT_DUE"} and stage_data:
        return None
    if (state in {"COMPLETE", "DEGRADED"} and
            (type(data.get("real_orders_sent")) is not int or data["real_orders_sent"] != 0)):
        return None
    reason = data.get("reason")
    if state == "BLOCKED":
        if isinstance(reason, str) and reason.startswith("SOURCE_CYCLE_PATH_OVERRIDE_DIVERGED:"):
            safe_reason = "SOURCE_CYCLE_PATH_OVERRIDE_DIVERGED"
        else:
            safe_reason = reason if reason in REASONS else "OTHER_OR_UNVERIFIED"
    elif state == "NOT_DUE":
        safe_reason = "NOT_DUE_PHASE"
    else:
        safe_reason = reason if reason in REASONS else "OTHER_OR_UNVERIFIED"
    return state, safe_reason, {name: stage_data.get(name, "NOT_REPORTED")
                                for name in STAGE_NAMES}


def summarize_journal(raw: bytes | None, *, now: datetime) -> dict[str, object]:
    """Consume raw bytes only on host and return fixed-enum advisory evidence."""
    receipt = default_receipt()
    if raw is None or not isinstance(raw, bytes) or len(raw) > MAX_JOURNAL_BYTES:
        return receipt
    if now.tzinfo is None:
        return receipt
    latest: tuple[datetime, tuple[str, str, dict[str, str]]] | None = None
    lines = raw.splitlines()
    if len(lines) > MAX_JOURNAL_LINES:
        return receipt
    try:
        for line in lines:
            if len(line) > MAX_RECORD_BYTES:
                return receipt
            event = json.loads(line.decode("utf-8"), object_pairs_hook=_unique)
            if not isinstance(event, dict) or event.get("_SYSTEMD_UNIT") != UNIT:
                continue
            message = event.get("MESSAGE")
            if not isinstance(message, str) or not message.startswith(MARKER):
                continue
            stamp = event.get("__REALTIME_TIMESTAMP")
            if not isinstance(stamp, str) or not STAMP.fullmatch(stamp):
                return receipt
            time_at = datetime.fromtimestamp(int(stamp) / 1_000_000, timezone.utc)
            age = (now.astimezone(timezone.utc) - time_at).total_seconds()
            if not 0 <= age <= LOOKBACK_MINUTES * 60:
                return receipt
            if time_at.astimezone(ART).date() != now.astimezone(ART).date():
                continue
            parsed = _parsed_marker(message)
            if parsed is None:
                return receipt
            if latest is None or latest[0] < time_at:
                latest = time_at, parsed
        if latest is None:
            return receipt
        time_at, (state, reason, stages) = latest
        age = (now.astimezone(timezone.utc) - time_at).total_seconds()
        receipt.update(marker="OBSERVED_UNATTESTED", reported_status=state,
                       reported_reason=reason, stage_iol=stages["iol"],
                       stage_public=stages["public"], stage_evidence=stages["evidence"],
                       marker_age=("WITHIN_2M" if age < 120 else
                                   "WITHIN_10M" if age < 600 else "WITHIN_45M"))
    except (UnicodeError, ValueError, TypeError, OverflowError):
        return default_receipt()
    return receipt


def sanitize_receipt(raw: bytes) -> dict[str, object]:
    """Allowlist runner-output fields exactly; no strings from host can escape."""
    fallback = default_receipt()
    if not isinstance(raw, bytes) or len(raw) > 4096:
        return fallback
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique)
        if not isinstance(value, dict) or set(value) != set(fallback):
            return fallback
        for field in set(fallback) - {"marker", "reported_status", "reported_reason",
                                     "stage_iol", "stage_public", "stage_evidence", "marker_age"}:
            if type(value[field]) is not type(fallback[field]) or value[field] != fallback[field]:
                return fallback
        if (value["marker"] not in {"OBSERVED_UNATTESTED", "NO_VERIFICADO"} or
            value["reported_status"] not in RESULT_STATES | {"NO_VERIFICADO"} or
            value["reported_reason"] not in REASONS or
            value["marker_age"] not in AGE_BUCKETS or
            value["stage_iol"] not in IOL_STAGE_STATES or
            any(value[key] not in STAGE_STATES for key in
                ("stage_public", "stage_evidence"))):
            return fallback
        if value["marker"] == "NO_VERIFICADO":
            return value if value == fallback else fallback
        if value["reported_status"] == "NO_VERIFICADO" or value["marker_age"] == "UNKNOWN":
            return fallback
        if value["reported_status"] in {"NOT_DUE", "BLOCKED"} and any(
                value["stage_" + name] != "NOT_REPORTED" for name in STAGE_NAMES):
            return fallback
        if value["reported_status"] == "COMPLETE" and any(
                value["stage_" + name] not in {"OK", "NOT_DUE_TTL", "NOT_REPORTED"}
                for name in STAGE_NAMES):
            return fallback
        return value
    except (UnicodeError, ValueError, TypeError):
        return fallback


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["host-probe"]:
        now = datetime.now(timezone.utc)
        result = summarize_journal(read_journal_bounded(journal_command(now)), now=now)
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    if args == ["sanitize"]:
        try:
            with Path("evidence/.a532-untrusted.json").open("rb") as handle:
                raw = handle.read(4097)
        except OSError:
            raw = b""
        safe = sanitize_receipt(raw)
        Path("evidence/a532-source-stages.json").write_text(
            json.dumps(safe, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8")
        print("A532_JOURNAL_MARKER=" + safe["marker"])
        return 0
    print("A532_READONLY_USAGE_ERROR", file=sys.stderr)
    return 64


if __name__ == "__main__":
    raise SystemExit(main())
