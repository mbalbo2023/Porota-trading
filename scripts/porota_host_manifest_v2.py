#!/usr/bin/env python3
"""Build a machine-readable RC6 host control-plane manifest from Git + workflow."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

UNIT_RE = re.compile(r"(?:ops/)?systemd/(porota-[A-Za-z0-9_.@-]+\.(?:service|timer))")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def tracked_modes(repo_root: Path) -> dict[str, str]:
    raw = subprocess.check_output(["git", "-C", str(repo_root), "ls-files", "-s", "-z"])
    result = {}
    for record in raw.split(b"\0"):
        if not record:
            continue
        left, path = record.decode("utf-8").split("\t", 1)
        mode = left.split()[0]
        result[path] = mode
    return result


def build_manifest(repo_root: Path, workflow_path: Path, source_sha: str, *, modes: dict[str, str] | None = None) -> dict:
    modes = tracked_modes(repo_root) if modes is None else dict(modes)
    tracked = sorted(
        p for p in modes
        if (p.startswith("systemd/") or p.startswith("ops/systemd/"))
        and p.endswith((".service", ".timer"))
        and "rc6" in Path(p).name.lower()
    )
    workflow = workflow_path.read_text(encoding="utf-8")
    referenced_names = sorted(set(UNIT_RE.findall(workflow)))
    by_name = {Path(p).name: p for p in tracked}

    untracked = sorted(name for name in referenced_names if name not in by_name)
    entries = []
    for path in tracked:
        name = Path(path).name
        referenced = name in referenced_names
        enable_now = bool(re.search(r"systemctl\s+enable\s+--now[^\n]*\b" + re.escape(name) + r"\b", workflow))
        enabled = enable_now or bool(re.search(r"systemctl\s+enable[^\n]*\b" + re.escape(name) + r"\b", workflow))
        active = enable_now or bool(re.search(r"systemctl\s+(?:start|restart)[^\n]*\b" + re.escape(name) + r"\b", workflow))
        src = repo_root / path
        entries.append({
            "category": "HOST_CONTROL_PLANE",
            "source_path": path,
            "source_sha256": sha256(src),
            "source_git_mode": modes[path],
            "source_git_sha": source_sha,
            "unit_name": name,
            "unit_kind": "timer" if name.endswith(".timer") else "service",
            "managed_by_canonical_deploy": referenced,
            "install_target": f"/etc/systemd/system/{name}" if referenced else None,
            "expected_enabled": enabled if referenced else None,
            "expected_active": active if referenced else None,
            "classification": "CANONICAL_DEPLOY" if referenced else "TRACKED_UNMANAGED_REVIEW",
        })

    return {
        "schema_version": 1,
        "status": "GREEN" if not untracked else "FAILED_UNTRACKED_WORKFLOW_INPUT",
        "source_git_sha": source_sha,
        "canonical_workflow": workflow_path.relative_to(repo_root).as_posix(),
        "counts": {
            "tracked_rc6_units": len(tracked),
            "canonical_deploy_units": sum(e["managed_by_canonical_deploy"] for e in entries),
            "tracked_unmanaged_review": sum(not e["managed_by_canonical_deploy"] for e in entries),
            "referenced_untracked": len(untracked),
        },
        "referenced_untracked": untracked,
        "units": entries,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--workflow", default=".github/workflows/rc6-pr69-isolated-transactional-deploy-20260915.yml")
    ap.add_argument("--source-sha", default="")
    ap.add_argument("--json-out")
    args = ap.parse_args()
    root = Path(args.repo_root).resolve()
    source_sha = args.source_sha or subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
    ).strip()
    result = build_manifest(root, root / args.workflow, source_sha)
    payload = json.dumps(result, indent=2, sort_keys=True)
    print(payload)
    if args.json_out:
        Path(args.json_out).write_text(payload + "\n", encoding="utf-8")
    print("POROTA_HOST_MANIFEST_V2=" + result["status"])
    return 0 if result["status"] == "GREEN" else 1


if __name__ == "__main__":
    raise SystemExit(main())
