#!/usr/bin/env python3
"""Despliegue transaccional del delta HF6 sobre el árbol efectivo HF5.

Mantiene HF5 durante build/tests, no crea backup, no toca Git, no copia
secretos/datos al staging y sólo persiste fuente después de validar la imagen.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pwd
import re
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.request import urlopen


HF5_VERSION = "17.0.0-rc3-hf5"
HF5_IMAGE = f"porota-trading-bot:{HF5_VERSION}"
HF6_VERSION = "17.0.0-rc3-hf6"
HF6_IMAGE = f"porota-trading-bot:{HF6_VERSION}"
BASE_HEAD = "60cfb13e03f3bfe6dbfcbe3158dca9273279fb7e"
BASE_EXPORT_MANIFEST_SHA256 = "f52db6c75dfdc40da3e325142d7efbed9ec3b4b05c2a4a2d21c83f702988eee8"
BASE_HF5_PAYLOAD_SHA256 = "8ee0b216fec8f00cdd4eaa0afe6f09770c8611b55fd2a63fa07fa0d60a52a39d"
OBSERVABILITY_REMOTE = "https://github.com/mbalbo2023/Porota-trading.git"
OBSERVABILITY_BRANCH = "runtime-observability"
MIN_FREE_BYTES = 5 * 1024**3
CONTAINERS = ("porota_production_observer", "porota_production_dashboard")
PERSIST_TABLES = ("paper_positions", "paper_fills", "paper_learning_samples",
                  "production_history", "candle_versions", "historical_raw_archive")


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def safe_relative(value):
    path = PurePosixPath(str(value))
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise RuntimeError(f"PAYLOAD_PATH_UNSAFE:{value}")
    if path.parts[0] in {".git", "data", ".secrets", ".env", "sre_vector_db", "model_cache"}:
        raise RuntimeError(f"PAYLOAD_PATH_FORBIDDEN:{value}")
    return path


def run(args, *, cwd=None, check=True, capture=False, timeout=None, echo_capture=True,
        display=None):
    command = [str(item) for item in args]
    print(f"[{now()}] RUN={display or ' '.join(command)}", flush=True)
    result = subprocess.run(command, cwd=str(cwd) if cwd else None, check=False,
                            text=True, stdout=subprocess.PIPE if capture else None,
                            stderr=subprocess.STDOUT if capture else None, timeout=timeout)
    if capture and echo_capture and result.stdout:
        print(result.stdout.rstrip(), flush=True)
    if check and result.returncode:
        raise RuntimeError(f"COMMAND_FAILED:{result.returncode}:{command[0]}")
    return result


def docker(*args, **kwargs):
    return run(("docker", *args), **kwargs)


def health(expected, timeout=180):
    deadline, last = time.monotonic() + timeout, "NO_ATTEMPT"
    while time.monotonic() < deadline:
        try:
            with urlopen("http://127.0.0.1:8000/health", timeout=5) as response:
                payload = json.loads(response.read(65536).decode("utf-8"))
            if response.status == 200 and payload.get("status") == "ok" and payload.get("version") == expected:
                return payload
            last = f"HTTP_{response.status}:{payload}"
        except Exception as exc:
            last = f"{type(exc).__name__}:{exc}"
        time.sleep(5)
    raise RuntimeError(f"HEALTH_TIMEOUT:{last}")


def container_images():
    result = {}
    for name in CONTAINERS:
        value = docker("inspect", "--format", "{{.Config.Image}}", name, capture=True)
        result[name] = value.stdout.strip().splitlines()[-1]
    return result


def database_state(repo):
    path = repo / "data/paper_v17/observer_v17.db"
    if not path.is_file() or path.is_symlink():
        raise RuntimeError("DATABASE_PATH_UNSAFE")
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=20)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only=ON")
        state = dict(connection.execute("""SELECT mode,process_state,session_state,ppi_auth,
          heartbeat_at,last_market_data_at,real_orders_sent FROM observer_state WHERE id=1""").fetchone())
        quick = connection.execute("PRAGMA quick_check").fetchone()[0]
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        counts = {name: connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                  for name in PERSIST_TABLES if name in tables}
        coherence = {
            "orphan_fills": connection.execute("""SELECT COUNT(*) FROM paper_fills f
              LEFT JOIN paper_positions p USING(paper_id) WHERE p.paper_id IS NULL""").fetchone()[0],
            "open_without_buy": connection.execute("""SELECT COUNT(*) FROM paper_positions p
              WHERE p.status='OPEN' AND NOT EXISTS(SELECT 1 FROM paper_fills f
              WHERE f.paper_id=p.paper_id AND f.side='BUY_SIMULATED')""").fetchone()[0],
            "closed_without_sell": connection.execute("""SELECT COUNT(*) FROM paper_positions p
              WHERE p.status='CLOSED' AND NOT EXISTS(SELECT 1 FROM paper_fills f
              WHERE f.paper_id=p.paper_id AND f.side='SELL_SIMULATED')""").fetchone()[0],
            "open_positions": connection.execute("SELECT COUNT(*) FROM paper_positions WHERE status='OPEN'").fetchone()[0],
        }
        workers = {}
        for name, table in (("supervisor", "paper_supervisor_state"),
                            ("exit_reader", "paper_exit_reader_state"),
                            ("candles", "candle_worker_state"),
                            ("scalping", "intraday_scalping_worker_state"),
                            ("telegram", "paper_notification_worker")):
            if table in tables:
                row = connection.execute(f"SELECT * FROM {table} WHERE id=1").fetchone()
                workers[name] = ({key: row[key] for key in row.keys()
                                  if key in {"state", "heartbeat_at"}} if row else {})
            else:
                workers[name] = {"state": "MISSING_TABLE"}
        error_types = ("DATA_ERROR", "EXIT_BOOK_ERROR", "EXIT_READER_LOGIN_ERROR",
                       "EXIT_READER_SESSION_INVALID", "PPI_SESSION_INVALID")
        placeholders = ",".join("?" for _ in error_types)
        recent_errors = connection.execute(f"""SELECT event_type,COUNT(*) count
          FROM paper_events WHERE event_type IN ({placeholders})
          AND julianday(event_at)>=julianday('now','-1 hour') GROUP BY event_type""",
          error_types).fetchall()
    finally:
        connection.close()
    return {"path": str(path), "inode": path.stat().st_ino, "quick_check": quick,
            "observer": state, "counts": counts, "coherence": coherence,
            "workers": workers, "ppi_errors_last_hour": [dict(row) for row in recent_errors]}


def operation_manifest(repo):
    return json.loads((repo / "data/operation_mode.json").read_text(encoding="utf-8"))


def validate_safe_runtime(repo, expected_version, expected_image, *, require_flat=False):
    health_payload = health(expected_version)
    images = container_images()
    if set(images.values()) != {expected_image}:
        raise RuntimeError(f"RUNTIME_IMAGE_MISMATCH:{images}")
    mode = operation_manifest(repo)
    if not (mode.get("mode") == "PRODUCTION_PAPER" and mode.get("execution") == "SIMULATED"
            and mode.get("apis", {}).get("PPI_ORDERS") == "BLOCKED"):
        raise RuntimeError(f"UNSAFE_OPERATION_MANIFEST:{mode}")
    db = database_state(repo)
    if db["quick_check"] != "ok" or int(db["observer"].get("real_orders_sent") or 0) != 0:
        raise RuntimeError(f"UNSAFE_DATABASE_STATE:{db}")
    if any(db["coherence"][key] for key in ("orphan_fills", "open_without_buy", "closed_without_sell")):
        raise RuntimeError(f"LEDGER_COHERENCE_FAILED:{db['coherence']}")
    if require_flat and db["coherence"]["open_positions"]:
        raise RuntimeError("OPEN_POSITIONS_BLOCK_HOTFIX_CUT")
    return {"health": health_payload, "images": images, "mode": mode, "database": db}


def read_payload(payload, expected_sha, staging):
    if digest(payload) != expected_sha.lower():
        raise RuntimeError("PAYLOAD_SHA256_MISMATCH")
    with tarfile.open(payload, "r:gz") as archive:
        members = archive.getmembers()
        if len(members) > 200 or sum(member.size for member in members) > 100 * 1024**2:
            raise RuntimeError("PAYLOAD_LIMIT_EXCEEDED")
        names = set()
        for member in members:
            path = safe_relative(member.name)
            if str(path) in names:
                raise RuntimeError(f"PAYLOAD_DUPLICATE_MEMBER:{path}")
            names.add(str(path))
            if member.issym() or member.islnk() or not (member.isfile() or member.isdir()):
                raise RuntimeError(f"PAYLOAD_MEMBER_UNSAFE:{path}")
            target = staging / Path(*path.parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise RuntimeError(f"PAYLOAD_MEMBER_UNREADABLE:{path}")
            with target.open("wb") as destination:
                shutil.copyfileobj(source, destination)
    manifest_path = staging / "HF6_PAYLOAD_MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (manifest.get("version") != HF6_VERSION or manifest.get("base_head") != BASE_HEAD
            or manifest.get("base_export_manifest_sha256") != BASE_EXPORT_MANIFEST_SHA256
            or manifest.get("base_hf5_payload_sha256") != BASE_HF5_PAYLOAD_SHA256
            or manifest.get("predeploy_backup") is not False):
        raise RuntimeError("PAYLOAD_MANIFEST_IDENTITY_INVALID")
    files = manifest.get("files")
    if not isinstance(files, list) or not files or len(files) > 100:
        raise RuntimeError("PAYLOAD_FILE_LIST_INVALID")
    expected_files = {"HF6_PAYLOAD_MANIFEST.json"}
    paths = set()
    for item in files:
        if not isinstance(item, dict):
            raise RuntimeError("PAYLOAD_FILE_ENTRY_INVALID")
        relative = safe_relative(item.get("path"))
        if str(relative) in paths:
            raise RuntimeError(f"PAYLOAD_DUPLICATE_PATH:{relative}")
        paths.add(str(relative))
        if (not re.fullmatch(r"[0-9a-f]{64}", str(item.get("target_sha256") or ""))
                or (item.get("baseline_sha256") is not None
                    and not re.fullmatch(r"[0-9a-f]{64}", str(item["baseline_sha256"])))
                or str(item.get("mode")) not in {"0644", "0664", "0755"}):
            raise RuntimeError(f"PAYLOAD_FILE_METADATA_INVALID:{relative}")
        expected_files.add(f"files/{relative}")
    actual_files = {member.name for member in members if member.isfile()}
    if actual_files != expected_files:
        raise RuntimeError("PAYLOAD_ARCHIVE_CONTENT_MISMATCH")
    return manifest


def validate_source_baseline(repo, manifest):
    export_manifest = repo / "HF5_EXPORT_MANIFEST.json"
    if (not export_manifest.is_file() or export_manifest.is_symlink()
            or digest(export_manifest) != BASE_EXPORT_MANIFEST_SHA256):
        raise RuntimeError("HF5_EFFECTIVE_EXPORT_IDENTITY_MISMATCH")
    for item in manifest.get("files", []):
        relative = safe_relative(item["path"])
        target = repo.joinpath(*relative.parts)
        baseline = item.get("baseline_sha256")
        if baseline is None:
            if target.exists():
                raise RuntimeError(f"NEW_FILE_ALREADY_EXISTS:{relative}")
        elif not target.is_file() or target.is_symlink() or digest(target) != baseline:
            raise RuntimeError(f"SOURCE_BASELINE_MISMATCH:{relative}")


def copy_candidate(repo, candidate):
    ignored = shutil.ignore_patterns(".git", ".env", ".secrets", "data", "sre_vector_db",
                                     "model_cache", "__pycache__", ".pytest_cache", "*.pyc")
    shutil.copytree(repo, candidate, ignore=ignored, symlinks=True)
    symlinks = [str(path.relative_to(candidate)) for path in candidate.rglob("*")
                if path.is_symlink()]
    if symlinks:
        raise RuntimeError(f"STAGING_SYMLINK_FORBIDDEN:{symlinks[:10]}")


def apply_to_candidate(candidate, extracted, manifest):
    for item in manifest["files"]:
        relative = safe_relative(item["path"])
        source = extracted / "files" / Path(*relative.parts)
        if not source.is_file() or source.is_symlink() or digest(source) != item["target_sha256"]:
            raise RuntimeError(f"PAYLOAD_FILE_INVALID:{relative}")
        target = candidate / Path(*relative.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        os.chmod(target, int(item.get("mode", "0644"), 8))


def image_version():
    result = docker("run", "--rm", "--network", "none", "--entrypoint", "python",
                    HF6_IMAGE, "-c", "import _version; print(_version.VERSION)", capture=True)
    return result.stdout.strip().splitlines()[-1]


def selected_environment(name):
    allowed = {"PAPER_RISK_PER_TRADE", "PAPER_MAX_OPEN_POSITIONS", "PAPER_MAX_HOLD_MINUTES",
               "PAPER_DAILY_SOFT_STOP_PCT", "MAX_DAILY_LOSS_PCT", "PAPER_AI_GATE_MODE",
               "PAPER_ECONOMIC_GATE_MODE", "PAPER_EXPECTANCY_POLICY",
               "PAPER_MARKET_REGIME_POLICY", "PAPER_SECTOR_CONCENTRATION_POLICY",
               "PAPER_FOCUS_SYMBOLS", "PAPER_SCALPING_MODE", "PPI_BACKGROUND_INGEST_SECONDS",
               "PAPER_NEWS_INGEST_ENABLED"}
    output = docker("inspect", "--format", "{{range .Config.Env}}{{println .}}{{end}}",
                    name, capture=True, echo_capture=False).stdout
    values = {}
    for raw in output.splitlines():
        key, separator, value = raw.partition("=")
        if separator and key in allowed:
            values[key] = value
    return values


def _at_or_after(value, threshold):
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            return False
        return stamp.astimezone(timezone.utc) >= threshold
    except (TypeError, ValueError):
        return False


def validate_hf6_runtime(repo, before, activated_after):
    runtime = validate_safe_runtime(repo, HF6_VERSION, HF6_IMAGE)
    required_workers = {"supervisor", "exit_reader", "candles", "scalping", "telegram"}
    deadline = time.monotonic() + 180
    after = runtime["database"]
    while time.monotonic() < deadline:
        ready = (_at_or_after(after["observer"].get("heartbeat_at"), activated_after)
                 and str(after["observer"].get("process_state") or "").upper()
                 not in {"", "STOPPED", "DEGRADED", "FAILED"}
                 and all(
                     str(after["workers"].get(name, {}).get("state") or "").upper()
                     not in {"", "STOPPED", "FAILED", "MISSING_TABLE"}
                     and _at_or_after(after["workers"].get(name, {}).get("heartbeat_at"),
                                      activated_after)
                     for name in required_workers))
        if ready:
            break
        time.sleep(3)
        after = database_state(repo)
    else:
        raise RuntimeError(f"WORKERS_NOT_READY:{after['workers']}")
    runtime["database"] = after
    if after["quick_check"] != "ok" or int(after["observer"].get("real_orders_sent") or 0) != 0:
        raise RuntimeError(f"POSTCUT_DATABASE_UNSAFE:{after}")
    if any(after["coherence"][key] for key in
           ("orphan_fills", "open_without_buy", "closed_without_sell")):
        raise RuntimeError(f"POSTCUT_LEDGER_COHERENCE_FAILED:{after['coherence']}")
    if after["inode"] != before["database"]["inode"]:
        raise RuntimeError("DATABASE_INODE_CHANGED")
    for table, count in before["database"]["counts"].items():
        if after["counts"].get(table, -1) < count:
            raise RuntimeError(f"PERSISTENT_ROWS_DECREASED:{table}")
    expected = {
        "PAPER_RISK_PER_TRADE": "0.002", "PAPER_MAX_OPEN_POSITIONS": "5",
        "PAPER_MAX_HOLD_MINUTES": "360", "PAPER_DAILY_SOFT_STOP_PCT": "1.5",
        "MAX_DAILY_LOSS_PCT": "2.5", "PAPER_AI_GATE_MODE": "OFF",
        "PAPER_ECONOMIC_GATE_MODE": "BINDING",
        "PAPER_EXPECTANCY_POLICY": "OBSERVATION_ONLY",
        "PAPER_MARKET_REGIME_POLICY": "ALERT_ONLY",
        "PAPER_SECTOR_CONCENTRATION_POLICY": "OBSERVATION_ONLY",
        "PAPER_SCALPING_MODE": "ACTIVE_PAPER", "PPI_BACKGROUND_INGEST_SECONDS": "7200",
        "PAPER_NEWS_INGEST_ENABLED": "false",
    }
    environment = selected_environment("porota_production_observer")
    for key, value in expected.items():
        if environment.get(key) != value:
            raise RuntimeError(f"RUNTIME_SETTING_MISMATCH:{key}")
    focus = environment.get("PAPER_FOCUS_SYMBOLS", "")
    if not all(symbol in focus.split(",") for symbol in
               ("AAPLD:CEDEARS:A-24HS", "AAPLC:CEDEARS:A-24HS")):
        raise RuntimeError("USD_FOCUS_NOT_ACTIVE")
    introspection = docker("exec", "porota_production_observer", "python",
                           "/app/ops_introspection_hf4.py", "--enqueue-critical",
                           capture=True, check=False, timeout=180)
    if introspection.returncode == 2 or "## CRITICAL" in (introspection.stdout or ""):
        raise RuntimeError("HF6_INTROSPECTION_CRITICAL")
    runtime["environment"] = environment
    runtime["introspection_returncode"] = introspection.returncode
    return runtime


def activate_candidate(candidate, repo):
    code = """import pathlib,sys
candidate=pathlib.Path(sys.argv[1]); repo=pathlib.Path(sys.argv[2])
sys.path.insert(0,str(candidate))
import porota_mode_manager as manager
manager.ROOT=repo; manager.DATA=repo/'data'; manager.MODE_FILE=manager.DATA/'operation_mode.json'
manager.IMAGE='porota-trading-bot:17.0.0-rc3-hf6'
manager.simulation()
"""
    run((sys.executable, "-c", code, candidate, repo), cwd=candidate, timeout=240)


def git_state(repo):
    prefix = ("git", "-c", f"safe.directory={repo}", "-C", repo)
    head = run((*prefix, "rev-parse", "HEAD"), capture=True).stdout.strip()
    branch = run((*prefix, "branch", "--show-current"), capture=True).stdout.strip()
    status = run((*prefix, "status", "--short"), capture=True).stdout.splitlines()
    return {"head": head, "branch": branch or "DETACHED", "status_short": status,
            "mutated_by_deploy": False}


def rollback(repo):
    outcome = {"attempted": True, "image": HF5_IMAGE}
    try:
        run((sys.executable, repo / "porota_mode_manager.py", "simulation"), cwd=repo, timeout=240)
        outcome["runtime"] = validate_safe_runtime(repo, HF5_VERSION, HF5_IMAGE)
        outcome["status"] = "OK"
    except Exception as exc:
        outcome.update(status="FAILED", error=f"{type(exc).__name__}:{exc}")
    return outcome


def atomic_write(path, content, mode, uid, gid):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.chown(temporary, uid, gid)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def persist_source(repo, extracted, manifest):
    owner = pwd.getpwnam("porotaadmin")
    previous, written = {}, []
    try:
        validate_source_baseline(repo, manifest)
        for item in manifest["files"]:
            relative = safe_relative(item["path"])
            target = repo / Path(*relative.parts)
            missing_parents = []
            parent = target.parent
            while parent != repo and not parent.exists():
                missing_parents.append(parent)
                parent = parent.parent
            for directory in reversed(missing_parents):
                directory.mkdir()
                os.chown(directory, owner.pw_uid, owner.pw_gid)
                os.chmod(directory, 0o755)
            if target.exists():
                stat = target.stat()
                previous[str(relative)] = (target.read_bytes(), stat.st_mode & 0o777, stat.st_uid, stat.st_gid)
            else:
                previous[str(relative)] = None
            source = extracted / "files" / Path(*relative.parts)
            content = source.read_bytes()
            mode = int(item.get("mode", "0644"), 8)
            atomic_write(target, content, mode, owner.pw_uid, owner.pw_gid)
            written.append(relative)
        for item in manifest["files"]:
            target = repo / Path(*safe_relative(item["path"]).parts)
            if digest(target) != item["target_sha256"]:
                raise RuntimeError(f"PERSISTED_HASH_MISMATCH:{item['path']}")
    except Exception:
        for relative in reversed(written):
            target = repo / Path(*relative.parts)
            old = previous[str(relative)]
            if old is None:
                try:
                    target.unlink()
                except FileNotFoundError:
                    pass
            else:
                atomic_write(target, *old)
        raise


def install_units(repo):
    publish_dir = repo / "data/introspection_publish"
    publish_dir.mkdir(parents=True, exist_ok=True)
    data_owner = (repo / "data").stat()
    os.chown(publish_dir, data_owner.st_uid, data_owner.st_gid)
    # Sólo contiene la vista sanitizada; lectura para el publicador sin dar
    # permisos sobre DB, ledger, introspección cruda ni secretos.
    os.chmod(publish_dir, 0o755)
    installed = []
    wrapper = repo / "scripts/porota-introspeccion-hf5-manual.sh"
    atomic_write(Path("/usr/local/sbin/porota-introspeccion-hf5-manual.sh"),
                 wrapper.read_bytes(), 0o755, 0, 0)
    for name in ("porota-introspeccion-hf5.service", "porota-introspeccion-hf5.timer",
                 "porota-introspection-publish.service", "porota-introspection-publish.timer"):
        source = repo / "deploy/systemd" / name
        target = Path("/etc/systemd/system") / name
        atomic_write(target, source.read_bytes(), 0o644, 0, 0)
        installed.append(name)
    run(("systemctl", "daemon-reload"))
    run(("systemctl", "enable", "--now", "porota-introspeccion-hf5.timer"))
    publisher_repo = Path("/var/lib/porota-observability/repo")
    publisher = {"installed": True, "enabled": False, "reason": "REPOSITORY_NOT_CONFIGURED"}
    if (publisher_repo / ".git").is_dir():
        branch = run(("sudo", "-n", "-u", "porotaadmin", "git", "-C", publisher_repo,
                      "branch", "--show-current"), capture=True).stdout.strip()
        if branch == OBSERVABILITY_BRANCH:
            run(("systemctl", "enable", "--now", "porota-introspection-publish.timer"))
            publisher = {"installed": True, "enabled": True, "branch": branch}
        else:
            publisher["reason"] = f"WRONG_BRANCH:{branch}"
    return {"units": installed, "publisher": publisher}


def configure_observability_repo():
    """Prepara un clon aislado; ningún Git toca el árbol fuente ni corre como root."""
    owner = pwd.getpwnam("porotaadmin")
    parent = Path("/var/lib/porota-observability")
    repository = parent / "repo"
    parent.mkdir(parents=True, exist_ok=True)
    os.chown(parent, owner.pw_uid, owner.pw_gid)
    os.chmod(parent, 0o750)

    def git(*args, **kwargs):
        return run(("sudo", "-n", "-u", "porotaadmin", "git", *args), **kwargs)

    created = False
    if not (repository / ".git").is_dir():
        if repository.exists() and any(repository.iterdir()):
            raise RuntimeError("OBSERVABILITY_DIRECTORY_NOT_EMPTY")
        if repository.exists():
            repository.rmdir()
        git("clone", "--filter=blob:none", "--no-checkout", OBSERVABILITY_REMOTE,
            repository, timeout=300,
            display="git clone --filter=blob:none --no-checkout <official-origin> <observability-repo>")
        created = True
    if git("-C", repository, "status", "--porcelain", capture=True,
           echo_capture=False).stdout.strip():
        raise RuntimeError("OBSERVABILITY_REPOSITORY_DIRTY")
    git("-C", repository, "remote", "set-url", "origin", OBSERVABILITY_REMOTE,
        display="git -C <observability-repo> remote set-url origin <official-origin>")
    remote_branch = git("-C", repository, "ls-remote", "--exit-code", "--heads",
                        "origin", OBSERVABILITY_BRANCH, check=False, capture=True,
                        echo_capture=False).returncode == 0
    if remote_branch:
        git("-C", repository, "fetch", "origin", OBSERVABILITY_BRANCH, timeout=300)
        local_branch = git("-C", repository, "show-ref", "--verify", "--quiet",
                           f"refs/heads/{OBSERVABILITY_BRANCH}", check=False).returncode == 0
        if local_branch:
            git("-C", repository, "switch", OBSERVABILITY_BRANCH)
        else:
            git("-C", repository, "switch", "-c", OBSERVABILITY_BRANCH,
                "--track", f"origin/{OBSERVABILITY_BRANCH}")
        git("-C", repository, "pull", "--ff-only", "origin", OBSERVABILITY_BRANCH,
            timeout=300)
    elif created:
        git("-C", repository, "switch", "--orphan", OBSERVABILITY_BRANCH)
        readme = repository / "README.runtime-observability.md"
        atomic_write(readme, (
            "# Porota runtime observability\n\n"
            "Rama exclusiva para snapshots sanitizados `latest` y diarios. "
            "No contiene secretos ni controla el runtime.\n").encode(),
            0o644, owner.pw_uid, owner.pw_gid)
        git("-C", repository, "add", "--", readme.name)
        git("-C", repository, "-c", "user.name=Porota Runtime",
            "-c", "user.email=runtime@porota.invalid", "commit",
            "-m", "observability: initialize runtime branch")
        git("-C", repository, "push", "-u", "origin", OBSERVABILITY_BRANCH,
            timeout=300)
    else:
        raise RuntimeError("OBSERVABILITY_BRANCH_MISSING_IN_EXISTING_CLONE")
    return {"status": "READY", "branch": OBSERVABILITY_BRANCH,
            "created_clone": created, "remote": "OFFICIAL_ORIGIN"}


def telegram_notify(repo, message):
    values = {}
    try:
        for raw in (repo / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() in {"TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"}:
                values[key.strip()] = value.strip().strip('"').strip("'")
        token, chat = values.get("TELEGRAM_BOT_TOKEN"), values.get("TELEGRAM_CHAT_ID")
        if not token or not chat:
            return "NOT_CONFIGURED"
        from urllib.parse import urlencode
        body = urlencode({"chat_id": chat, "text": message}).encode()
        from urllib.request import Request
        request = Request(f"https://api.telegram.org/bot{token}/sendMessage", data=body, method="POST")
        with urlopen(request, timeout=10) as response:
            return "DELIVERED" if response.status == 200 else f"HTTP_{response.status}"
    except Exception as exc:
        return "FAILED_" + type(exc).__name__


def write_result(repo, stamp, payload):
    owner = pwd.getpwnam("porotaadmin")
    directory = repo / "data/deployments"
    directory.mkdir(parents=True, exist_ok=True)
    os.chown(directory, owner.pw_uid, owner.pw_gid)
    os.chmod(directory, 0o750)
    target = directory / f"v17_rc3_hf6_deploy_{stamp}.json"
    atomic_write(target, (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode(),
                 0o640, owner.pw_uid, owner.pw_gid)
    return target


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--payload", required=True, type=Path)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--repo", default="/opt/porota-trading", type=Path)
    args = parser.parse_args(argv)
    if os.geteuid() != 0:
        raise SystemExit("ROOT_REQUIRED_VIA_SUDO_N")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    repo, payload = args.repo.resolve(), args.payload.resolve()
    result = {"schema": "porota-hf6-deploy-v1", "started_at": now(),
              "version": HF6_VERSION, "payload": payload.name,
              "payload_sha256": args.sha256.lower(), "predeploy_backup": False,
              "git_mutated": False, "database_deleted": False, "real_orders_expected": 0}
    cut_started = persisted = False
    staging_parent = Path("/opt/porota-staging")
    staging_parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    staging_root = Path(tempfile.mkdtemp(prefix=f"porota-hf6-{stamp}-", dir=staging_parent))
    try:
        if shutil.disk_usage(repo).free < MIN_FREE_BYTES:
            raise RuntimeError("INSUFFICIENT_FREE_SPACE_FOR_PARALLEL_BUILD")
        before = validate_safe_runtime(repo, HF5_VERSION, HF5_IMAGE, require_flat=True)
        result["before"] = before
        result["git_before"] = git_state(repo)
        extracted, candidate = staging_root / "payload", staging_root / "candidate"
        extracted.mkdir()
        manifest = read_payload(payload, args.sha256, extracted)
        validate_source_baseline(repo, manifest)
        copy_candidate(repo, candidate)
        apply_to_candidate(candidate, extracted, manifest)
        if json.loads((extracted / "HF6_PAYLOAD_MANIFEST.json").read_text())["version"] != HF6_VERSION:
            raise RuntimeError("STAGED_VERSION_MISMATCH")
        run((sys.executable, "-m", "compileall", "-q", candidate), timeout=180)
        docker("image", "inspect", HF5_IMAGE, capture=True)
        docker("build", "--pull=false", "-t", HF6_IMAGE, candidate, timeout=1800)
        if image_version() != HF6_VERSION:
            raise RuntimeError("IMAGE_INTERNAL_VERSION_MISMATCH")
        docker("run", "--rm", "--network", "none", "--entrypoint", "python",
               HF6_IMAGE, "-m", "compileall", "-q", "/app", timeout=300)
        docker("run", "--rm", "--network", "none", "--entrypoint", "pytest",
               HF6_IMAGE, "-q", timeout=1800)
        cut_started = True
        activated_after = datetime.now(timezone.utc)
        activate_candidate(candidate, repo)
        runtime = validate_hf6_runtime(repo, before, activated_after)
        result["runtime"] = runtime
        persist_source(repo, extracted, manifest)
        persisted = True
        try:
            result["observability_repository"] = configure_observability_repo()
        except Exception as exc:
            result["observability_repository"] = {
                "status": "WARNING", "error": f"{type(exc).__name__}:{exc}"}
        try:
            result["systemd"] = install_units(repo)
        except Exception as exc:
            result["systemd"] = {"status": "WARNING", "error": f"{type(exc).__name__}:{exc}"}
        try:
            disk = run((sys.executable, repo / "ops_disk_inventory_hf6.py", "--root", repo),
                       capture=True, check=False, timeout=120)
            result["disk_inventory_returncode"] = disk.returncode
        except Exception as exc:
            result["disk_inventory_error"] = f"{type(exc).__name__}:{exc}"
        result["telegram"] = telegram_notify(
            repo, "🟢 POROTA HF6 DESPLEGADO\nPRODUCTION_PAPER / SIMULATED\n"
                  "Economía BINDING · riesgo 0,2% · freno 1,5% · corte 2,5%\n"
                  "Órdenes reales: 0")
        result.update(status="HF6_OPERATIVE", finished_at=now(), source_persisted=True)
        path = write_result(repo, stamp, result)
        print(json.dumps({"status": result["status"], "result": str(path),
                          "version": HF6_VERSION, "real_orders_sent": 0}, separators=(",", ":")))
        return 0
    except Exception as exc:
        result.update(status="FAILED", failed_at=now(), error=f"{type(exc).__name__}:{exc}",
                      cut_started=cut_started, source_persisted=persisted)
        if cut_started and not persisted:
            result["rollback"] = rollback(repo)
        result["telegram"] = telegram_notify(
            repo, "🔴 POROTA HF6 — DESPLIEGUE FALLIDO\n"
                  f"Etapa con corte iniciado: {'sí' if cut_started else 'no'}\n"
                  f"Rollback: {result.get('rollback', {}).get('status', 'NO_APLICA')}\n"
                  "HF5 debe permanecer como referencia hasta revisar el JSON final.")
        path = write_result(repo, stamp, result)
        print(json.dumps({"status": "FAILED", "error": result["error"],
                          "result": str(path), "rollback": result.get("rollback", {}).get("status")},
                         separators=(",", ":")))
        return 1
    finally:
        if staging_root.is_dir() and staging_root.parent == Path("/opt/porota-staging"):
            shutil.rmtree(staging_root)


if __name__ == "__main__":
    raise SystemExit(main())
