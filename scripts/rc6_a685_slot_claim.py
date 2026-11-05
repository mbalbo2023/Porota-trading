#!/usr/bin/env python3
"""A685: fail-closed, pre-SSH GitHub Actions claim for an A670 ART slot.

This code never imports a broker SDK, invokes PPI, accesses the droplet, or
modifies the runtime budget. The job-level Deploy V2 mutex is the sole race
serializer; a successfully uploaded claim artifact is a durable *attempt*
witness, not an atomic lock or proof of provider usage.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import json
import os
from pathlib import Path
import re
import stat
from typing import Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


REPOSITORY = "mbalbo2023/Porota-trading"
CONTROL_REF = "refs/heads/main"
WORKFLOW_PATH = ".github/workflows/rc6-a669-ppi-current-book-20261105.yml"
WORKFLOW_REF = f"{REPOSITORY}/{WORKFLOW_PATH}@{CONTROL_REF}"
WORKFLOW_FILE = WORKFLOW_PATH.rsplit("/", 1)[-1]
SLOTS = frozenset({"1045", "1120", "1430", "1630"})
ART = ZoneInfo("America/Argentina/Buenos_Aires")
SHA40 = re.compile(r"[a-f0-9]{40}\Z")
CLAIM_RE = re.compile(r"rc6-a670-claim-(\d{4}-\d{2}-\d{2})-(1045|1120|1430|1630)\Z")
MAX_RUNS = 100
MAX_HTTP_BYTES = 1_000_000


class Hold(Exception):
    """A bounded, public reason requiring zero SSH and zero PPI GET."""


@dataclass(frozen=True)
class Claim:
    art_date: date
    slot: str
    run_id: int
    run_number: int
    source_sha: str

    @property
    def name(self) -> str:
        return f"rc6-a670-claim-{self.art_date.isoformat()}-{self.slot}"


def _int_field(value: object) -> int:
    if isinstance(value, bool):
        raise Hold("A670_CLAIM_RUN_ID_INVALID")
    try:
        number = int(str(value))
    except (ValueError, TypeError):
        raise Hold("A670_CLAIM_RUN_ID_INVALID") from None
    if not 0 < number < 10**19:
        raise Hold("A670_CLAIM_RUN_ID_INVALID")
    return number


def prepare_claim(env: Mapping[str, str], now: datetime) -> Claim:
    if (env.get("GITHUB_REPOSITORY") != REPOSITORY
            or env.get("GITHUB_REF") != CONTROL_REF
            or env.get("GITHUB_WORKFLOW_REF") != WORKFLOW_REF
            or env.get("GITHUB_EVENT_NAME") not in {"schedule", "workflow_dispatch"}):
        raise Hold("A670_CLAIM_NONCANONICAL_CONTROL_PLANE")
    slot = env.get("RC6_A669_SLOT", "")
    if slot not in SLOTS:
        raise Hold("A670_CLAIM_SLOT_UNVERIFIED")
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise Hold("A670_CLAIM_UTC_CLOCK_INVALID")
    try:
        day = date.fromisoformat(env.get("RC6_A670_ART_DATE", ""))
    except (ValueError, TypeError):
        raise Hold("A670_CLAIM_ART_DATE_INVALID") from None
    if day != now.astimezone(ART).date():
        raise Hold("A670_CLAIM_ART_DATE_MISMATCH")
    if env.get("GITHUB_RUN_ATTEMPT") != "1":
        raise Hold("A670_CLAIM_RERUN_FORBIDDEN")
    sha = env.get("GITHUB_SHA", "")
    if SHA40.fullmatch(sha) is None:
        raise Hold("A670_CLAIM_SOURCE_SHA_INVALID")
    return Claim(day, slot, _int_field(env.get("GITHUB_RUN_ID")),
                 _int_field(env.get("GITHUB_RUN_NUMBER")), sha)


def _github_json(url: str, token: str) -> object:
    if not token:
        raise Hold("A670_CLAIM_ACTIONS_TOKEN_ABSENT")
    req = Request(url, headers={
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "RC6-A685-slot-claim",
    })
    try:
        with urlopen(req, timeout=8) as response:
            raw = response.read(MAX_HTTP_BYTES + 1)
        if len(raw) > MAX_HTTP_BYTES:
            raise Hold("A670_CLAIM_ACTIONS_REPLY_UNBOUNDED")
        return json.loads(raw)
    except (HTTPError, URLError, OSError, TimeoutError, ValueError, UnicodeError) as exc:
        raise Hold("A670_CLAIM_ACTIONS_API_UNAVAILABLE") from None


def _as_int(value: object) -> int:
    if type(value) is not int or value < 0:
        raise Hold("A670_CLAIM_ACTIONS_SCHEMA_INVALID")
    return value


def _list_body(fetch: Callable[[str], object], url: str,
               collection: str) -> tuple[int, list]:
    body = fetch(url)
    if not isinstance(body, dict):
        raise Hold("A670_CLAIM_ACTIONS_SCHEMA_INVALID")
    total = _as_int(body.get("total_count"))
    items = body.get(collection)
    if not isinstance(items, list) or len(items) > MAX_RUNS:
        raise Hold("A670_CLAIM_ACTIONS_SCHEMA_INVALID")
    return total, items


def _art_window(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, tzinfo=ART).astimezone(timezone.utc)
    end = (datetime.combine(day + timedelta(days=1), time.min,
                            tzinfo=ART).astimezone(timezone.utc)
           - timedelta(seconds=1))
    return start, end


def _run_date(value: object) -> date:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise Hold("A670_CLAIM_RUN_TIMESTAMP_INVALID")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise Hold("A670_CLAIM_RUN_TIMESTAMP_INVALID") from None
    return dt.astimezone(ART).date()


def no_prior_attempt(claim: Claim,
                     fetch: Callable[[str], object]) -> None:
    """Fail closed if another eligible same-day run may have sent a GET.

    The job-level ``rc6-unified-paper-deploy`` mutex MUST wrap the entire
    check → claim-artifact upload → provider section. Artifacts alone do not
    serialize or reserve anything. Missing evidence for an earlier run is HOLD.
    """
    start, end = _art_window(claim.art_date)
    query = urlencode({
        "branch": "main", "per_page": "100", "page": "1",
        "created": f"{start.isoformat().replace('+00:00', 'Z')}.."
                   f"{end.isoformat().replace('+00:00', 'Z')}",
    })
    url = (f"https://api.github.com/repos/{REPOSITORY}/actions/workflows/"
           f"{WORKFLOW_FILE}/runs?{query}")
    total, runs = _list_body(fetch, url, "workflow_runs")
    # Day+branch filters are server-side; truncated results cannot be trusted.
    if total > MAX_RUNS or len(runs) != total:
        raise Hold("A670_CLAIM_RUN_HISTORY_AMBIGUOUS")
    current_seen = False
    seen_ids: set[int] = set()
    peers: list[dict] = []
    for run in runs:
        if not isinstance(run, dict):
            raise Hold("A670_CLAIM_ACTIONS_SCHEMA_INVALID")
        rid = _as_int(run.get("id"))
        number = _as_int(run.get("run_number"))
        if rid in seen_ids or rid <= 0 or number <= 0:
            raise Hold("A670_CLAIM_RUN_HISTORY_AMBIGUOUS")
        seen_ids.add(rid)
        if (_run_date(run.get("created_at")) != claim.art_date
                or run.get("head_branch") != "main"
                or not str(run.get("path", "")).startswith(WORKFLOW_PATH + "@")):
            raise Hold("A670_CLAIM_RUN_HISTORY_AMBIGUOUS")
        if rid == claim.run_id:
            if (number != claim.run_number
                    or run.get("event") not in {"schedule", "workflow_dispatch"}):
                raise Hold("A670_CLAIM_RUN_ID_MISMATCH")
            current_seen = True
            continue
        if run.get("event") not in {"schedule", "workflow_dispatch"}:
            continue  # pull_request/static tests have no host capture job.
        peers.append(run)
    if not current_seen:
        raise Hold("A670_CLAIM_RUN_ID_NOT_VISIBLE")
    # A completed newer-number run may have entered the protected section
    # first; do not assume run_number is execution order.
    for run in peers:
        rid = run["id"]
        aurl = (f"https://api.github.com/repos/{REPOSITORY}/actions/"
                f"runs/{rid}/artifacts?per_page=100")
        count, artifacts = _list_body(fetch, aurl, "artifacts")
        if count != len(artifacts):
            raise Hold("A670_CLAIM_ARTIFACT_LIST_AMBIGUOUS")
        claims = []
        for artifact in artifacts:
            if not isinstance(artifact, dict):
                raise Hold("A670_CLAIM_ACTIONS_SCHEMA_INVALID")
            name = artifact.get("name")
            if isinstance(name, str) and name.startswith("rc6-a670-claim-"):
                match = CLAIM_RE.fullmatch(name)
                if not match or match.group(1) != claim.art_date.isoformat():
                    raise Hold("A670_CLAIM_ARTIFACT_LIST_AMBIGUOUS")
                if artifact.get("expired") is not False:
                    raise Hold("A670_CLAIM_ARTIFACT_LIST_AMBIGUOUS")
                claims.append(name)
        if len(claims) != 1:
            # For an earlier run, no claim might mean a legacy run completed a
            # provider GET but lost its artifact. Do not infer zero GET.
            if (run["run_number"] < claim.run_number
                    or run.get("status") == "completed"):
                raise Hold("A670_CLAIM_PRIOR_ATTEMPT_AMBIGUOUS")
            # Newer run still in contracts / waiting for this same mutex.
            if claims:
                raise Hold("A670_CLAIM_ARTIFACT_LIST_AMBIGUOUS")
            continue
        if claims[0] == claim.name:
            raise Hold("A670_CLAIM_DUPLICATE_ART_SLOT")


def _write_claim(path: Path, claim: Claim) -> None:
    payload = {
        "schema": "rc6.a685.control.slot-claim.v1",
        "art_date": claim.art_date.isoformat(),
        "slot": claim.slot,
        "run_id": claim.run_id,
        "run_number": claim.run_number,
        "source_sha": claim.source_sha,
        "ppi_gets_by_claim": 0,
        "provider_payload_included": False,
    }
    contents = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    try:
        if os.write(fd, contents) != len(contents):
            raise Hold("A670_CLAIM_FILE_WRITE_INCOMPLETE")
        os.fsync(fd)
    finally:
        os.close(fd)


def _append_claim_output(output: Path, claim: Claim) -> None:
    """Bind upload-artifact name to this verified claim, never user input."""
    if output.is_symlink() or not output.is_file():
        raise Hold("A670_CLAIM_OUTPUT_PATH_UNSAFE")
    fd = os.open(output, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise Hold("A670_CLAIM_OUTPUT_PATH_UNSAFE")
        line = f"artifact_name={claim.name}\n".encode("ascii")
        if os.write(fd, line) != len(line):
            raise Hold("A670_CLAIM_OUTPUT_WRITE_INCOMPLETE")
    finally:
        os.close(fd)


def main() -> int:
    now = datetime.now(timezone.utc)
    try:
        claim = prepare_claim(os.environ, now)
        token = os.environ.get("GH_TOKEN", "")
        no_prior_attempt(claim, lambda url: _github_json(url, token))
        dest_dir = Path("evidence")
        if dest_dir.is_symlink() or (dest_dir.exists() and not dest_dir.is_dir()):
            raise Hold("A670_CLAIM_FILE_PATH_UNSAFE")
        dest_dir.mkdir(mode=0o700, exist_ok=True)
        _write_claim(Path("evidence/a670_slot_claim.json"), claim)
        _append_claim_output(Path(os.environ.get("GITHUB_OUTPUT", "")), claim)
    except (Hold, OSError, OverflowError) as exc:
        reason = str(exc) if isinstance(exc, Hold) else "A670_CLAIM_LOCAL_IO_HOLD"
        print(f"A670_SLOT_CLAIM=HOLD|REASON={reason}|SSH=NO|PPI_GETS=0")
        return 2
    print(f"A670_SLOT_CLAIM=READY_FOR_IMMUTABLE_UPLOAD|SLOT={claim.slot}|ART_DATE={claim.art_date}|SSH=NO|PPI_GETS=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
