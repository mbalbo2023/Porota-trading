#!/usr/bin/env python3
"""Offline hashed-manifest retention/gap inspection; NEVER purge or ingest.

Example (synthetic/offhost only):
  python scripts/rc6_source_retention_dryrun.py --retention-manifest manifest.json \
    --expected-input-sha256 <preverified_sha256> --as-of 2026-11-02T15:00:00Z

The hash flag binds the byte input supplied by an independent capture; it
does not itself prove the capture was authentic. No output is written to disk.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rc6_source_retention_dryrun import (  # noqa: E402
    HEX64, MAX_MANIFEST_JSON_BYTES, incremental_gap_dryrun, retention_dryrun,
)


def read_verified_json(path: Path, expected_sha256: str) -> dict:
    """Read one bounded, regular, nonlinked offline capture without mutation."""
    if not isinstance(expected_sha256, str) or not HEX64.fullmatch(expected_sha256):
        raise ValueError("SOURCE_DRYRUN_EXPECTED_INPUT_SHA_INVALID")
    path = Path(path).absolute()
    if path.is_symlink():
        raise ValueError("SOURCE_DRYRUN_SYMLINK_NOT_ALLOWED")
    try:
        flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
        with os.fdopen(os.open(path, flags), "rb") as handle:
            before = os.fstat(handle.fileno())
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                    or not 2 <= before.st_size <= MAX_MANIFEST_JSON_BYTES):
                raise ValueError("SOURCE_DRYRUN_REGULAR_BOUNDED_COPY_REQUIRED")
            raw = handle.read(MAX_MANIFEST_JSON_BYTES + 1)
            after = os.fstat(handle.fileno())
        current_path = path.lstat()
    except OSError:
        raise ValueError("SOURCE_DRYRUN_INPUT_COPY_UNAVAILABLE") from None
    if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            or (before.st_dev, before.st_ino) != (current_path.st_dev, current_path.st_ino)
            or not stat.S_ISREG(current_path.st_mode)
            or len(raw) != before.st_size):
        raise ValueError("SOURCE_DRYRUN_INPUT_CHANGED")
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("SOURCE_DRYRUN_INPUT_SHA_MISMATCH")
    try:
        parsed = json.loads(raw)
    except (UnicodeError, ValueError):
        raise ValueError("SOURCE_DRYRUN_INPUT_JSON_INVALID") from None
    if not isinstance(parsed, dict):
        raise ValueError("SOURCE_DRYRUN_INPUT_OBJECT_REQUIRED")
    return parsed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--retention-manifest", type=Path)
    group.add_argument("--gap-evidence", type=Path)
    parser.add_argument("--expected-input-sha256", required=True)
    parser.add_argument("--as-of", required=True, help="Explicit aware UTC/ART point-in-time")
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--checkpoint-sha256")
    args = parser.parse_args(argv)
    try:
        candidate = read_verified_json(args.retention_manifest or args.gap_evidence,
                                       args.expected_input_sha256)
        if args.checkpoint is None and args.checkpoint_sha256 is not None:
            raise ValueError("SOURCE_DRYRUN_CHECKPOINT_PATH_REQUIRED")
        if args.checkpoint is not None and args.checkpoint_sha256 is None:
            raise ValueError("SOURCE_DRYRUN_CHECKPOINT_SHA_REQUIRED")
        checkpoint = (read_verified_json(args.checkpoint, args.checkpoint_sha256)
                      if args.checkpoint is not None else None)
        report = (retention_dryrun(candidate, as_of=args.as_of, checkpoint=checkpoint)
                  if args.retention_manifest is not None
                  else incremental_gap_dryrun(candidate, as_of=args.as_of,
                                              checkpoint=checkpoint))
    except ValueError as exc:
        print(json.dumps({"status": "BLOQUEADO", "reason": str(exc),
                          "source_mutated": False, "provider_calls": 0,
                          "host_purge_allowed": False, "real_orders_sent": 0},
                         sort_keys=True))
        return 2
    print(json.dumps(report, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
