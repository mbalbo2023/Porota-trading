#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

RUNTIME_DB = Path("/opt/porota-trading/data/paper_v17/observer_v17.db")
RUNTIME_CONTAINERS = (
    "porota_production_observer",
    "porota_production_dashboard",
    "porota_critical_approval_rc6",
)


@dataclass(frozen=True)
class FileCandidate:
    path: str
    size_bytes: int
    mtime: float
    reason: str


def load_policy(path: str | Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if int(data.get("schema_version", 0)) != 1:
        raise ValueError("unsupported disk policy schema")
    return data


def disk_free_bytes(path: str = "/") -> int:
    return int(shutil.disk_usage(path).free)


def inode_free_percent(path: str = "/") -> float:
    stat = os.statvfs(path)
    if stat.f_files <= 0:
        return 100.0
    return 100.0 * float(stat.f_ffree) / float(stat.f_files)


def disk_band(free_bytes: int, policy: dict) -> str:
    disk = policy["disk"]
    if free_bytes < int(disk["critical_free_bytes"]):
        return "CRITICAL"
    if free_bytes < int(disk["post_cleanup_min_free_bytes"]):
        return "RED"
    if free_bytes < int(disk["green_free_bytes"]):
        return "YELLOW"
    return "GREEN"


def _path_size(path: Path) -> int:
    try:
        if path.is_symlink():
            return 0
        if path.is_file():
            return int(path.stat().st_size)
        total = 0
        for base, _, files in os.walk(path, followlinks=False):
            for name in files:
                p = Path(base) / name
                try:
                    if not p.is_symlink():
                        total += int(p.stat().st_size)
                except OSError:
                    pass
        return total
    except OSError:
        return 0


def tmp_candidates(policy: dict, now: float | None = None) -> list[FileCandidate]:
    cfg = policy["tmp"]
    root = Path(cfg["root"])
    prefixes = tuple(str(x) for x in cfg["prefixes"])
    min_age = int(cfg["min_age_days"]) * 86400
    now = time.time() if now is None else float(now)
    rows: list[FileCandidate] = []
    if not root.exists():
        return rows
    for p in root.iterdir():
        if not p.name.startswith(prefixes):
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        if now - float(st.st_mtime) <= min_age:
            continue
        rows.append(
            FileCandidate(
                path=str(p),
                size_bytes=_path_size(p),
                mtime=float(st.st_mtime),
                reason=f"TMP_OLDER_THAN_{cfg['min_age_days']}D",
            )
        )
    return sorted(rows, key=lambda x: (x.mtime, x.path))


def backup_plan(
    root: str | Path,
    pattern: str,
    keep_newest: int,
    pinned_names: Iterable[str] = (),
) -> tuple[list[FileCandidate], list[FileCandidate]]:
    root = Path(root)
    pinned = set(str(x) for x in pinned_names)
    rows: list[FileCandidate] = []
    if root.exists():
        for p in root.glob(pattern):
            if not p.is_file():
                continue
            st = p.stat()
            rows.append(
                FileCandidate(
                    path=str(p),
                    size_bytes=int(st.st_size),
                    mtime=float(st.st_mtime),
                    reason="BACKUP",
                )
            )
    rows.sort(key=lambda x: (x.mtime, x.path), reverse=True)
    keep_n = max(1, int(keep_newest))
    keep_paths = {row.path for row in rows[:keep_n]}
    keep_paths.update(
        row.path for row in rows if Path(row.path).name in pinned
    )
    kept = [row for row in rows if row.path in keep_paths]
    delete = [
        FileCandidate(
            path=row.path,
            size_bytes=row.size_bytes,
            mtime=row.mtime,
            reason="BACKUP_RETENTION_EXCESS",
        )
        for row in rows
        if row.path not in keep_paths
    ]
    return kept, delete


def all_backup_plans(policy: dict) -> dict[str, dict[str, list[FileCandidate]]]:
    out: dict[str, dict[str, list[FileCandidate]]] = {}
    for name in ("general", "paper", "history"):
        cfg = policy["backups"][name]
        kept, delete = backup_plan(
            cfg["root"],
            cfg["glob"],
            cfg["keep_newest"],
            cfg.get("pinned_names", []),
        )
        out[name] = {"kept": kept, "delete": delete}
    return out


def _run(
    args: list[str],
    *,
    timeout: int = 60,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=check,
    )


def _runtime_db_state() -> dict:
    c = sqlite3.connect(f"file:{RUNTIME_DB}?mode=ro", uri=True, timeout=3)
    c.execute("PRAGMA query_only=ON")
    mode, orders = c.execute(
        "SELECT mode,real_orders_sent FROM observer_state WHERE id=1"
    ).fetchone()
    c.close()
    return {"mode": str(mode), "real_orders_sent": int(orders)}


def _container_state() -> dict[str, dict]:
    states: dict[str, dict] = {}
    fmt = "{{.State.Status}}|{{.State.OOMKilled}}|{{.RestartCount}}|{{.Image}}"
    for name in RUNTIME_CONTAINERS:
        cp = _run(["docker", "inspect", "-f", fmt, name], timeout=20)
        status, oom, restarts, image = cp.stdout.strip().split("|", 3)
        states[name] = {
            "status": status,
            "oom_killed": oom.lower() == "true",
            "restart_count": int(restarts),
            "image": image,
        }
    return states


def _ppi_watch_state() -> list[str]:
    shell = r"""
for unit in $(systemctl list-unit-files --no-legend 2>/dev/null | awk 'tolower($1) ~ /ppi/ && tolower($1) ~ /watch/ {print $1}' | sort); do
  printf '%s|%s|%s\n' "$unit" "$(systemctl is-enabled "$unit" 2>/dev/null || true)" "$(systemctl is-active "$unit" 2>/dev/null || true)"
done
"""
    cp = _run(["bash", "-lc", shell], timeout=30)
    return [line for line in cp.stdout.splitlines() if line.strip()]


def runtime_snapshot() -> dict:
    return {
        "observer": _runtime_db_state(),
        "containers": _container_state(),
        "ppi_watch": _ppi_watch_state(),
    }


def validate_runtime(snapshot: dict, policy: dict) -> None:
    safety = policy["safety"]
    observer = snapshot["observer"]
    if observer["mode"] != safety["required_runtime_mode"]:
        raise RuntimeError(
            f"runtime mode {observer['mode']} != {safety['required_runtime_mode']}"
        )
    if observer["real_orders_sent"] != int(safety["required_real_orders_sent"]):
        raise RuntimeError("real_orders_sent invariant failed")
    for name, row in snapshot["containers"].items():
        if row["status"] != "running":
            raise RuntimeError(f"{name} not running")
        if row["oom_killed"]:
            raise RuntimeError(f"{name} OOMKilled")


def compare_runtime(before: dict, after: dict, policy: dict) -> None:
    validate_runtime(after, policy)
    if before["observer"] != after["observer"]:
        raise RuntimeError("observer safety state changed during housekeeping")
    if before["containers"] != after["containers"]:
        raise RuntimeError("container state/image/restart count changed during housekeeping")
    if policy["safety"]["ppi_watch_must_remain_untouched"]:
        if before["ppi_watch"] != after["ppi_watch"]:
            raise RuntimeError("PPI Watch state changed during housekeeping")


def _candidate_open_references(candidates: list[FileCandidate]) -> list[str]:
    if not candidates:
        return []
    if shutil.which("lsof") is None:
        raise RuntimeError("lsof required for cleanup but not installed")
    cp = _run(["lsof", "-nP"], timeout=45, check=False)
    paths = {row.path for row in candidates}
    hits: list[str] = []
    for line in cp.stdout.splitlines():
        for path in paths:
            if path in line:
                hits.append(line)
                break
    return hits


def _delete_candidate(row: FileCandidate) -> None:
    p = Path(row.path)
    if not p.exists() and not p.is_symlink():
        return
    if p.is_dir() and not p.is_symlink():
        shutil.rmtree(p)
    else:
        p.unlink()


def _clean_apt(policy: dict) -> list[str]:
    actions: list[str] = []
    if policy["apt"].get("clean_cache"):
        _run(["apt-get", "clean"], timeout=120)
        actions.append("APT_CACHE_CLEAN")
    if policy["apt"].get("clean_lists"):
        root = Path("/var/lib/apt/lists")
        if root.exists():
            for p in root.iterdir():
                if p.name in {"lock", "partial", "auxfiles"}:
                    continue
                if p.is_dir() and not p.is_symlink():
                    shutil.rmtree(p)
                else:
                    p.unlink(missing_ok=True)
        actions.append("APT_LISTS_CLEAN")
    return actions


def _vacuum_journal(policy: dict) -> str:
    target = int(policy["journal"]["max_bytes"])
    cp = _run(
        ["journalctl", f"--vacuum-size={target}"],
        timeout=120,
        check=False,
    )
    if cp.returncode != 0:
        raise RuntimeError(f"journal vacuum failed: {cp.stdout.strip()}")
    return cp.stdout.strip()


def _docker_observation() -> str:
    if shutil.which("docker") is None:
        return "DOCKER_UNAVAILABLE"
    cp = _run(["docker", "system", "df"], timeout=45, check=False)
    return cp.stdout.strip()


def execute(mode: str, policy: dict) -> tuple[dict, int]:
    before_free = disk_free_bytes("/")
    before_inodes = inode_free_percent("/")
    before_band = disk_band(before_free, policy)
    runtime_before = runtime_snapshot()
    validate_runtime(runtime_before, policy)

    effective_mode = mode
    if mode == "auto":
        effective_mode = (
            "cleanup"
            if before_free < int(policy["disk"]["auto_cleanup_below_bytes"])
            else "audit"
        )

    tmp = tmp_candidates(policy)
    plans = all_backup_plans(policy)
    backup_delete = [
        row
        for group in plans.values()
        for row in group["delete"]
    ]

    result = {
        "schema_version": 1,
        "requested_mode": mode,
        "effective_mode": effective_mode,
        "before": {
            "free_bytes": before_free,
            "inode_free_percent": before_inodes,
            "band": before_band,
        },
        "policy": {
            "paper_keep_newest": policy["backups"]["paper"]["keep_newest"],
            "history_keep_newest": policy["backups"]["history"]["keep_newest"],
            "general_keep_newest": policy["backups"]["general"]["keep_newest"],
            "tmp_min_age_days": policy["tmp"]["min_age_days"],
            "journal_max_bytes": policy["journal"]["max_bytes"],
        },
        "candidates": {
            "tmp": [asdict(x) for x in tmp],
            "backups": {
                name: {
                    "kept": [asdict(x) for x in group["kept"]],
                    "delete": [asdict(x) for x in group["delete"]],
                }
                for name, group in plans.items()
            },
        },
        "deleted": [],
        "actions": [],
        "docker": _docker_observation(),
        "runtime_before": runtime_before,
    }

    if effective_mode == "cleanup":
        all_candidates = tmp + backup_delete
        open_refs = _candidate_open_references(all_candidates)
        if open_refs:
            result["open_references"] = open_refs
            raise RuntimeError("cleanup candidates have open file references")

        protected = [Path(x).resolve() for x in policy["backups"]["protected_paths"]]
        for row in backup_delete:
            rp = Path(row.path).resolve()
            if any(parent == rp or parent in rp.parents for parent in protected):
                raise RuntimeError(f"protected backup path selected: {rp}")

        for row in tmp + backup_delete:
            _delete_candidate(row)
            result["deleted"].append(asdict(row))

        result["actions"].extend(_clean_apt(policy))
        result["actions"].append("JOURNAL_VACUUM")
        result["journal_vacuum_output"] = _vacuum_journal(policy)

    after_free = disk_free_bytes("/")
    after_inodes = inode_free_percent("/")
    runtime_after = runtime_snapshot()
    compare_runtime(runtime_before, runtime_after, policy)

    result["after"] = {
        "free_bytes": after_free,
        "inode_free_percent": after_inodes,
        "band": disk_band(after_free, policy),
    }
    result["runtime_after"] = runtime_after
    result["space_recovered_bytes"] = after_free - before_free
    result["status"] = "GREEN"

    exit_code = 0
    if effective_mode == "cleanup":
        if after_free < int(policy["disk"]["post_cleanup_min_free_bytes"]):
            result["status"] = "RED"
            result["reason"] = "POST_CLEANUP_FREE_BELOW_MINIMUM"
            exit_code = 43
    elif before_free < int(policy["disk"]["critical_free_bytes"]):
        result["status"] = "RED"
        result["reason"] = "CRITICAL_FREE_SPACE_AUDIT_ONLY"
        exit_code = 44

    return result, exit_code


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--policy", required=True)
    ap.add_argument("--mode", choices=("audit", "cleanup", "auto"), default="audit")
    ap.add_argument("--json-out")
    args = ap.parse_args()

    policy = load_policy(args.policy)
    try:
        result, exit_code = execute(args.mode, policy)
    except Exception as exc:
        result = {
            "schema_version": 1,
            "requested_mode": args.mode,
            "status": "RED",
            "error": f"{type(exc).__name__}: {exc}",
        }
        exit_code = 50

    payload = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.json_out:
        Path(args.json_out).write_text(payload, encoding="utf-8")
    print(payload, end="")
    print(
        "POROTA_DISK_HOUSEKEEPING="
        f"{result.get('status','RED')}|requested={args.mode}"
        f"|effective={result.get('effective_mode','UNKNOWN')}"
        f"|free_after={result.get('after',{}).get('free_bytes','UNKNOWN')}"
        f"|recovered={result.get('space_recovered_bytes','UNKNOWN')}"
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
