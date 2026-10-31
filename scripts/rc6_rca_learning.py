"""A212: deterministic, non-authoritative RCA incident grouping for CI.

This is an *offline* analyzer of structured GitHub Actions failure evidence.
It never reads logs, contacts a provider or host, changes a workflow, or issues
an operational GO.  A matching fingerprint is evidence of recurrence, not
proof that two failures have the same root cause.

Input format: {"schema_version": 1, "events": [structured_event, ...]}.
No free-text messages, stack traces, account IDs, or runtime data are accepted.
The registry is a separately reviewed mapping from signatures to RCA, fix,
guard and regression test; the default registry deliberately has no entries.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from zoneinfo import ZoneInfo


SCHEMA_VERSION = 1
MAX_INCIDENT_BYTES = 2 * 1024 * 1024
MAX_REGISTRY_BYTES = 256 * 1024
MAX_EVENTS = 10_000
ART = ZoneInfo("America/Argentina/Buenos_Aires")
STATES = frozenset({"PROPUESTO", "DESARROLLADO", "COMMITTEADO",
                    "EN_GITHUB", "ARTEFACTO_VALIDADO", "DESPLEGADO",
                    "VALIDADO_RUNTIME", "NO_VERIFICADO", "BLOQUEADO"})
EVENT_FIELDS = frozenset({"component", "error_code", "contract_version",
                          "failure_phase", "family", "process_state",
                          "schema_identity", "run_id", "source_sha",
                          "timestamp_utc", "test_nodeid", "evidence_sha256"})
REQUIRED_EVENT_FIELDS = frozenset({"component", "failure_phase", "run_id",
                                   "source_sha", "timestamp_utc"})
REGISTRY_FIELDS = frozenset({"fingerprint", "rca_id", "fix_commit_sha",
                             "guard_path", "test_path", "evidence_state"})


class UnsafeRCAInput(ValueError):
    """Invalid or potentially sensitive evidence; never echo input values."""


def _reject() -> None:
    raise UnsafeRCAInput("RC6_RCA_INPUT_REJECTED")


def _checked(value: object, pattern: str, *, limit: int = 64) -> str:
    if not isinstance(value, str) or not value or len(value) > limit:
        _reject()
    if re.fullmatch(pattern, value, flags=re.ASCII) is None:
        _reject()
    return value


def _hex(value: object, size: int) -> str:
    return _checked(value, rf"[0-9a-f]{{{size}}}", limit=size)


def _utc(value: object) -> tuple[str, str]:
    # Source and display chronology must both be explicit; no local host clock.
    if not isinstance(value, str) or len(value) > 40:
        _reject()
    try:
        source = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _reject()
    if source.tzinfo is None or source.utcoffset() != timezone.utc.utcoffset(source):
        _reject()
    iso_utc = source.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return iso_utc, source.astimezone(ART).isoformat()


def _event(event: object) -> tuple[dict, dict]:
    if not isinstance(event, dict) or not REQUIRED_EVENT_FIELDS <= event.keys():
        _reject()
    if set(event) - EVENT_FIELDS:
        _reject()  # Reject free-text "message", "token", "stack", etc.
    component = _checked(event["component"], r"[a-z][a-z0-9_-]{1,63}")
    phase = _checked(event["failure_phase"], r"[A-Z][A-Z0-9_]{1,63}")
    error_code = _checked(event.get("error_code", "UNKNOWN"),
                          r"[A-Z][A-Z0-9_]{1,63}")
    contract = _checked(event.get("contract_version", "UNSPECIFIED"),
                        r"[A-Za-z0-9_.-]{1,64}")
    family = _checked(event.get("family", "UNSPECIFIED"), r"[A-Z][A-Z0-9_]{1,63}")
    state = _checked(event.get("process_state", "UNSPECIFIED"),
                     r"[A-Z][A-Z0-9_]{1,63}")
    schema = _checked(event.get("schema_identity", "UNSPECIFIED"),
                      r"[A-Za-z0-9_.-]{1,64}")
    sha = _hex(event["source_sha"], 40)
    run_id = event["run_id"]
    if type(run_id) is not int or not 0 < run_id < 10**17:
        _reject()
    when_utc, when_art = _utc(event["timestamp_utc"])
    nodeid = event.get("test_nodeid")
    if nodeid is not None and (not isinstance(nodeid, str) or not nodeid
                               or len(nodeid) > 2048):
        _reject()
    digest = event.get("evidence_sha256")
    if digest is not None:
        digest = _hex(digest, 64)

    # Preserve distinctions that change meaning (family, process state, schema).
    # Exclude SHA and test name so repeats across releases/tests are visible.
    dimensions = {"component": component, "error_code": error_code,
                  "contract_version": contract, "failure_phase": phase,
                  "family": family, "process_state": state,
                  "schema_identity": schema}
    payload = json.dumps({"v": SCHEMA_VERSION, **dimensions},
                         sort_keys=True, separators=(",", ":")).encode("ascii")
    fingerprint = "sha256:" + hashlib.sha256(payload).hexdigest()
    observation = {"run_id": run_id, "source_sha": sha,
                   "timestamp_utc": when_utc, "timestamp_art": when_art,
                   "test_nodeid_sha256": hashlib.sha256(nodeid.encode("utf-8")).hexdigest()
                   if nodeid is not None else None,
                   "evidence_sha256": digest}
    return {"fingerprint": fingerprint, **dimensions}, observation


def _repo_path(value: object) -> str:
    path = _checked(value, r"[A-Za-z0-9_./-]{3,160}", limit=160)
    if path.startswith("/") or any(part in {".", "..", ""} for part in path.split("/")):
        _reject()
    return path


def _registry(data: object) -> dict[str, dict]:
    if not isinstance(data, dict) or set(data) != {"schema_version", "signatures"}:
        _reject()
    entries = data["signatures"]
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        _reject()
    if not isinstance(entries, list) or len(entries) > 1000:
        _reject()
    indexed = {}
    for raw in entries:
        if not isinstance(raw, dict) or set(raw) != REGISTRY_FIELDS:
            _reject()
        signature = _checked(raw["fingerprint"], r"sha256:[0-9a-f]{64}", limit=71)
        if signature in indexed:
            _reject()
        value = {"rca_id": _checked(raw["rca_id"], r"RC6-[A-Z0-9_-]{2,64}"),
                 "fix_commit_sha": _hex(raw["fix_commit_sha"], 40),
                 "guard_path": _repo_path(raw["guard_path"]),
                 "test_path": _repo_path(raw["test_path"]),
                 "evidence_state": raw["evidence_state"]}
        if value["evidence_state"] not in STATES:
            _reject()
        indexed[signature] = value
    return indexed


def analyze(data: object, registry: object | None = None) -> dict:
    """Pure, deterministic RCA grouping; no operational decision authority."""
    if not isinstance(data, dict) or set(data) != {"schema_version", "events"}:
        _reject()
    if type(data["schema_version"]) is not int or data["schema_version"] != SCHEMA_VERSION:
        _reject()
    if not isinstance(data["events"], list) or len(data["events"]) > MAX_EVENTS:
        _reject()
    known = _registry(registry if registry is not None else
                      {"schema_version": 1, "signatures": []})
    buckets: dict[str, dict] = {}
    for raw in data["events"]:
        signature, observation = _event(raw)
        fp = signature["fingerprint"]
        if fp not in buckets:
            buckets[fp] = {"signature": signature, "events": []}
        buckets[fp]["events"].append(observation)

    groups = []
    unique_reports = 0
    for fingerprint in sorted(buckets):
        record = buckets[fingerprint]
        observations = record["events"]
        seen = set()
        retained = []
        for item in observations:
            key = json.dumps(item, sort_keys=True, separators=(",", ":"))
            if key not in seen:
                seen.add(key)
                retained.append(item)
        retained.sort(key=lambda x: (x["timestamp_utc"], x["run_id"],
                                     x["source_sha"], x["test_nodeid_sha256"] or ""))
        unique_reports += len(retained)
        match = known.get(fingerprint) if record["signature"]["error_code"] != "UNKNOWN" else None
        groups.append({"fingerprint": fingerprint,
                       "dimensions": {k: v for k, v in record["signature"].items()
                                      if k != "fingerprint"},
                       "reported_failures": len(observations),
                       "unique_reports": len(retained),
                       "different_runs": len({x["run_id"] for x in retained}),
                       "classification": "REGISTRY_MATCH_REVIEW_REQUIRED" if match else "UNKNOWN",
                       "recurrent_known_signature": bool(match),
                       "root_cause_confirmed_by_analyzer": False,
                       "registry_rca": match,
                       "observations": retained})

    return {"schema_version": SCHEMA_VERSION,
            "evidence_state": "NO_VERIFICADO",
            "analysis_mode": "READ_ONLY",
            "go_decision": "NO_AUTOMATIC_GO",
            "auto_merge": False, "auto_deploy": False, "auto_trade": False,
            "input_failures": len(data["events"]),
            "unique_reports": unique_reports,
            "distinct_signatures": len(groups),
            "confirmed_rca_count": 0,
            "groups": groups}


def _json_file(path: Path, max_bytes: int) -> object:
    try:
        if not path.is_file() or path.is_symlink() or path.stat().st_size > max_bytes:
            _reject()
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        _reject()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--registry", type=Path, default=Path(__file__).resolve().parents[1] /
                        "ops/policy/rc6-rca-signatures-v1.json")
    args = parser.parse_args(argv)
    temp_path = None
    try:
        result = analyze(_json_file(args.input, MAX_INCIDENT_BYTES),
                         _json_file(args.registry, MAX_REGISTRY_BYTES))
        target = args.output
        if target.is_symlink() or (target.exists() and not target.is_file()):
            _reject()
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                         dir=target.parent, prefix=".rc6-rca-",
                                         delete=False) as temp:
            temp_path = Path(temp.name)
            os.chmod(temp_path, 0o600)
            json.dump(result, temp, indent=2, sort_keys=True)
            temp.write("\n")
        os.replace(temp_path, target)
    except (UnsafeRCAInput, OSError, ValueError):
        print("RC6_RCA_ANALYSIS=RED|reason=INPUT_OR_OUTPUT_INVALID", file=sys.stderr)
        return 2
    finally:
        # Never strand a partially written local diagnostic, even if the
        # target directory fills up.  This path is created by tempfile only.
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
    print(f"RC6_RCA_ANALYSIS=GREEN|groups={result['distinct_signatures']}|"
          f"reports={result['unique_reports']}|automatic_go=NO")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
