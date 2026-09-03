#!/usr/bin/env python3
"""Fail a release source tree when runtime/builder debris is present.

This is a source/export guard, not a cleanup routine: it never deletes files.
"""
from __future__ import annotations

import argparse
from pathlib import Path

FORBIDDEN_NAMES = {":memory:.ses", ".env", "Secret.txt", "ACCESO_DASHBOARD_DROPLET.txt"}
FORBIDDEN_SUFFIXES = (".db", ".db-wal", ".db-shm", ".ses", ".bak")
FORBIDDEN_FRAGMENTS = (".pre-hf6", ".pre-hf6v2-", ".pre-rc")
FORBIDDEN_DIRS = {"data", "sre_vector_db", "model_cache", ".secrets", "__pycache__", ".pytest_cache"}


def violations(root: Path) -> list[str]:
    root=root.resolve()
    bad=[]
    for path in root.rglob("*"):
        rel=path.relative_to(root)
        if any(part in FORBIDDEN_DIRS for part in rel.parts):
            if path.is_file():
                bad.append(str(rel))
            continue
        if not path.is_file():
            continue
        name=path.name
        if name in FORBIDDEN_NAMES or name.endswith(FORBIDDEN_SUFFIXES) or any(x in name for x in FORBIDDEN_FRAGMENTS):
            bad.append(str(rel))
    return sorted(set(bad))


def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument("root", nargs="?", default=".")
    args=parser.parse_args()
    bad=violations(Path(args.root))
    if bad:
        print("RC4_RELEASE_TREE=FAIL")
        for item in bad:
            print("FORBIDDEN="+item)
        return 3
    print("RC4_RELEASE_TREE=OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
