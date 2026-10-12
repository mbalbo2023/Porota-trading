"""Read-only bounded post-repair and SQLite lock owner attribution."""
from __future__ import annotations

import collections
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import time

DB = Path("/opt/porota-trading/data/paper_v17/observer_v17.db")
NAMES = ("porota_critical_approval_rc6", "porota_production_observer",
         "porota_production_dashboard")


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def cmd(*args, timeout=15):
    try:
        p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=timeout, check=False)
        return p.returncode, p.stdout.decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        return 124, ""


def sha(raw):
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()


def docker():
    rows = {}
    for name in NAMES:
        status, out = cmd("docker", "inspect", name, timeout=25)
        if status:
            rows[name] = {"exit": status}
            continue
        x = json.loads(out)[0]
        s = x["State"]
        pid = s.get("Pid")
        row = {"id": x["Id"], "image_id": x["Image"],
               "health": (s.get("Health") or {}).get("Status"),
               "running": s["Running"], "restart_count": x["RestartCount"],
               "init": x["HostConfig"].get("Init"), "pids_limit": x["HostConfig"]["PidsLimit"],
               "oom_killed": s["OOMKilled"]}
        if name == NAMES[0] and pid:
            try:
                rel = (Path("/proc") / str(pid) / "cgroup").read_text().splitlines()[0].split(":", 2)[-1]
                group = Path("/sys/fs/cgroup") / rel.lstrip("/")
                row["pids"] = {key: (group / key).read_text().strip()
                               for key in ("pids.current", "pids.max", "pids.events")}
                child = []
                for path in Path("/proc").iterdir():
                    if not path.name.isdigit():
                        continue
                    try:
                        cg = (path / "cgroup").read_text()
                        if rel not in cg:
                            continue
                        status = (path / "status").read_text()
                        name_match = re.search(r"(?m)^Name:\s*(.+)$", status)
                        state_match = re.search(r"(?m)^State:\s*(.+)$", status)
                        child.append((name_match.group(1) if name_match else "?",
                                      state_match.group(1) if state_match else "?"))
                    except OSError:
                        pass
                row["process_classes"] = dict(collections.Counter(
                    name + "|" + state for name, state in child))
                row["task_count"] = len(child)
            except OSError as exc:
                row["cgroup_error"] = type(exc).__name__
        rows[name] = row
    return rows


def sqlite_state():
    c = sqlite3.connect("file:" + str(DB) + "?mode=ro", uri=True, timeout=2)
    try:
        c.execute("PRAGMA query_only=ON")
        return list(c.execute(
            "SELECT mode,real_orders_sent FROM observer_state WHERE id=1"
        ).fetchone())
    finally:
        c.close()


def lock_snapshot(inodes):
    found = []
    try:
        lines = Path("/proc/locks").read_text().splitlines()
    except OSError:
        return found
    for line in lines:
        parts = line.split()
        if len(parts) < 6:
            continue
        dev_inode = parts[5].split(":")
        if len(dev_inode) != 3:
            continue
        try:
            device = (int(dev_inode[0], 16), int(dev_inode[1], 16))
            inode = int(dev_inode[2])
            pid = int(parts[4])
        except ValueError:
            continue
        label = inodes.get((device, inode))
        if not label:
            continue
        process = Path("/proc") / str(pid)
        try:
            status = process.joinpath("status").read_text()
            parent = int(re.search(r"(?m)^PPid:\s*(\d+)", status).group(1))
            comm = process.joinpath("comm").read_text().strip()[:32]
            cgroup = process.joinpath("cgroup").read_text()
            owner = ("OBSERVER" if "porota_production_observer" in cgroup
                     else "DASHBOARD" if "porota_production_dashboard" in cgroup
                     else "OTHER")
        except (OSError, AttributeError):
            parent, comm, owner = None, "UNKNOWN", "UNKNOWN"
        found.append({"file_class": label, "pid": pid, "ppid": parent,
                      "comm": comm, "owner": owner,
                      "mode": parts[3], "start": parts[6] if len(parts) > 6 else None})
    return found


def top_level(root, max_entries=35):
    path = Path(root)
    if not path.is_dir():
        return {"exists": False}
    rows = []
    children = list(path.iterdir())
    for child in children[:max_entries]:
        try:
            st = child.lstat()
            if stat.S_ISLNK(st.st_mode):
                rows.append({"class": "SYMLINK", "name_sha256": sha(child.name),
                             "blocks_bytes": st.st_blocks * 512})
                continue
            if not stat.S_ISDIR(st.st_mode) and not stat.S_ISREG(st.st_mode):
                continue
            code, out = cmd("du", "-x", "-s", "-B1", str(child), timeout=8)
            size = int(out.split()[0]) if code == 0 and out.split() else None
            rows.append({"class": "DIR" if stat.S_ISDIR(st.st_mode) else "FILE",
                         "name": child.name if root in
                            ("/var/cache", "/opt/porota-builds", "/opt/porota-staging")
                            else None,
                         "name_sha256": sha(child.name), "inode": st.st_ino,
                         "dev": st.st_dev, "nlink": st.st_nlink,
                         "blocks_bytes": size, "mtime_utc":
                            dt.datetime.fromtimestamp(st.st_mtime, dt.timezone.utc).isoformat()})
        except (OSError, ValueError):
            pass
    return {"exists": True, "total_entries": len(children), "inspected_entries": min(len(children), max_entries), "entries": sorted(
        rows, key=lambda x: -(x["blocks_bytes"] or 0))[:max_entries]}


def main():
    fs = os.statvfs("/")
    report = {"schema": "porota.rc6.sre.post-and-sqlite-locks.v1",
              "captured_utc": now(), "host_mutation": False,
              "root_free_bytes": fs.f_bavail * fs.f_frsize,
              "root_free_inodes": fs.f_ffree,
              "paper": sqlite_state(), "containers": docker()}
    inodes = {}
    for suffix, label in (("", "DB"), ("-wal", "WAL"), ("-shm", "SHM")):
        try:
            st = os.stat(str(DB) + suffix)
            inodes[((os.major(st.st_dev), os.minor(st.st_dev)), st.st_ino)] = label
        except OSError:
            pass
    samples = []
    for _ in range(40):
        samples.append({"utc": now(), "locks": lock_snapshot(inodes)})
        time.sleep(1)
    report["lock_samples"] = samples
    report["lock_owner_counts"] = dict(collections.Counter(
        (x["file_class"] + "|" + x["mode"] + "|" + x["owner"])
        for sample in samples for x in sample["locks"]))
    # Docker logs writes to stderr, so obtain a merged stream without raw export.
    try:
        p = subprocess.run(["docker", "logs", "--timestamps", "--since", "10m",
                            "--tail", "2500", "porota_production_observer"],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=25, check=False)
        lines = p.stdout.decode("utf-8", "replace").splitlines()
        report["sqlite_log"] = {
            "exit": p.returncode, "lines": len(lines),
            "locked_line_count": sum("database is locked" in line.lower() for line in lines),
            "last_lock_line_utc": next((line[:35] for line in reversed(lines)
                                         if "database is locked" in line.lower()), None),
            "raw_sha256": hashlib.sha256(p.stdout).hexdigest(),
        }
    except subprocess.TimeoutExpired:
        report["sqlite_log"] = {"exit": 124}
    for root in ("/var/cache", "/opt/porota-builds", "/opt/porota-staging",
                 "/opt/porota-trading/data"):
        report["inventory_" + root.replace("/", "_")] = top_level(root)
    report["completed_utc"] = now()
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
