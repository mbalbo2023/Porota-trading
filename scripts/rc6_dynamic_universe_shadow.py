#!/usr/bin/env python3
"""Replay/live-report the separate observation engines without trading writes.

Accept a private evidence bundle and optional existing SQLite runtime source.
Output/checkpoint is a separate artifact; capacity/config changes invalidate it.
No secrets, login, remote fetch, order client or factual parameter mutation.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rc6_dynamic_universe.runtime import read_runtime
from rc6_dynamic_universe.live import run_shadow


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--db")
    args = parser.parse_args()
    source, target, checkpoint = map(lambda p: Path(p).resolve(), (args.input, args.out, args.checkpoint))
    inputs = {source} | ({Path(args.db).resolve()} if args.db else set())
    # Temporary/lock aliases are writes too: e.g. input=report.json.tmp must
    # never be replaced while atomically publishing report.json.
    writes = {target, checkpoint, target.with_suffix(target.suffix+".tmp"),
              checkpoint.with_suffix(checkpoint.suffix+".tmp"),
              checkpoint.with_suffix(checkpoint.suffix+".lock")}
    if len(writes) != 5 or writes & inputs:
        raise ValueError("OUTPUT_MUST_BE_SEPARATE_FROM_INPUT")
    bundle = json.loads(source.read_text())
    if args.db:
        bundle.update(read_runtime(args.db, as_of=bundle["as_of"]))
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    with checkpoint.with_suffix(checkpoint.suffix+".lock").open("a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        previous = json.loads(checkpoint.read_text()) if checkpoint.exists() else {}
        report = run_shadow(bundle, previous=previous)
        for path, payload in ((target, report), (checkpoint, {"engines": report["engines"], "frozen": report["frozen"]})):
            path.parent.mkdir(parents=True, exist_ok=True)
            # O_EXCL random temporary names cannot follow a stale symlink or
            # overwrite an existing hardlinked source/DB through a fixed .tmp.
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                    prefix="."+path.name+".", suffix=".tmp", delete=False) as stream:
                tmp = Path(stream.name)
                try:
                    json.dump(payload, stream, sort_keys=True, default=str, allow_nan=False)
                    stream.flush()
                    os.fsync(stream.fileno())
                except BaseException:
                    tmp.unlink(missing_ok=True)
                    raise
            try:
                tmp.replace(path)
            finally:
                tmp.unlink(missing_ok=True)
    print(json.dumps({"status": "SHADOW_REPORT", "real_orders_sent": 0, "real_routes": "NOT_CALLED",
                      "engines": list(report["engines"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
