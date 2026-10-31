#!/usr/bin/env python3
"""Hash-bound, non-destructive OFFHOST retention review with replay checkpoints.

The index lists sequential batch-000000.json, batch-000001.json, etc., with
SHA-256, versions_count, first_version_id and last_version_id. Every batch
uses the original rc6_source_retention_dryrun manifest shape. Capture the
index and batch files independently from the same verified, quiescent copy.
This command does not produce deletion authority, even after all batches pass.
The JSON next_checkpoint_template can be supplied again with its SHA-256; it
is only a deterministic cursor, not evidence of database transactions.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import stat
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rc6_retention_batch_dryrun import review_batches  # noqa: E402
from scripts.rc6_source_retention_dryrun import read_verified_json  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--expected-index-sha256", required=True)
    parser.add_argument("--batch-directory", type=Path, required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--checkpoint-sha256")
    parser.add_argument("--batch-limit", type=int, default=8)
    args = parser.parse_args(argv)
    try:
        if (args.checkpoint is None) != (args.checkpoint_sha256 is None):
            raise ValueError("RETENTION_BATCH_CHECKPOINT_HASH_PAIR_REQUIRED")
        base = args.batch_directory.absolute()
        try:
            state = base.lstat()
        except OSError:
            raise ValueError("RETENTION_BATCH_DIRECTORY_UNAVAILABLE") from None
        if not stat.S_ISDIR(state.st_mode) or base.is_symlink():
            raise ValueError("RETENTION_BATCH_DIRECTORY_MUST_BE_REAL")
        index = read_verified_json(args.index, args.expected_index_sha256)
        checkpoint = (read_verified_json(args.checkpoint, args.checkpoint_sha256)
                      if args.checkpoint is not None else None)
        report = review_batches(index, as_of=args.as_of,
                                read_batch=lambda name, digest:
                                    read_verified_json(base / name, digest),
                                checkpoint=checkpoint, batch_limit=args.batch_limit)
    except ValueError as exc:
        print(json.dumps({"status": "BLOQUEADO", "reason": str(exc),
                          "host_purge_allowed": False,
                          "source_mutated": False, "real_orders_sent": 0},
                         sort_keys=True))
        return 2
    print(json.dumps(report, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
