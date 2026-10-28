#!/usr/bin/env python3
"""Read and validate a runtime-evidence bundle from one fixed commit/blob."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys


SHA40 = re.compile(r"^[0-9a-f]{40}$")
DEFAULT_PATH = "runtime/evidence/latest.json"


class ConsumerError(RuntimeError):
    pass


def _git(repo: Path, *args: str, binary: bool = False) -> bytes | str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], check=False,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    if result.returncode:
        raise ConsumerError("GIT_OBJECT_UNAVAILABLE")
    return result.stdout if binary else result.stdout.decode("utf-8").strip()


def consume(repo: Path, commit_sha: str, blob_sha: str,
            path: str = DEFAULT_PATH) -> dict:
    if not SHA40.fullmatch(commit_sha) or not SHA40.fullmatch(blob_sha):
        raise ConsumerError("PIN_INVALID")
    if path != DEFAULT_PATH:
        raise ConsumerError("PATH_NOT_ALLOWLISTED")
    resolved = _git(repo, "rev-parse", f"{commit_sha}^{{commit}}")
    if resolved != commit_sha:
        raise ConsumerError("COMMIT_MISMATCH")
    observed_blob = _git(repo, "rev-parse", f"{commit_sha}:{path}")
    if observed_blob != blob_sha:
        raise ConsumerError("BLOB_MISMATCH")
    raw = _git(repo, "show", f"{commit_sha}:{path}", binary=True)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ConsumerError("BUNDLE_INVALID_JSON") from exc
    if not isinstance(payload, dict):
        raise ConsumerError("BUNDLE_NOT_OBJECT")
    from ops_runtime_evidence_rc6 import validate_bundle

    validate_bundle(payload)
    return {
        "commit_sha": commit_sha,
        "blob_sha": blob_sha,
        "path": path,
        "content_sha256": hashlib.sha256(raw).hexdigest(),
        "bundle": payload,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--commit-sha", required=True)
    parser.add_argument("--blob-sha", required=True)
    parser.add_argument("--path", default=DEFAULT_PATH)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    result = consume(args.repo.resolve(), args.commit_sha.lower(), args.blob_sha.lower(), args.path)
    encoded = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(encoded, encoding="utf-8")
    else:
        sys.stdout.write(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
