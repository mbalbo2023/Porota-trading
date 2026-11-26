"""Exercise the installed image with fixtures in a private Docker sandbox."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os

from scripts.porota_rootless_host import CID, DB_RELATIVE, database_snapshot, decode, require


def run_host(manager, source_sha, source_tree, attempt):
    from scripts.porota_rootless_jobs import scheduler_lock, verified_host

    with scheduler_lock(manager.identity):
        manager, current, release = verified_host(manager.root, manager.identity)
        require((release["source_sha"], release["source_tree"], current["attempt"], current.get("market_provider")) ==
                (source_sha, source_tree, attempt, "IOL_REST"), "REPLAY_INSTALLED_RELEASE_MISMATCH")
        before = database_snapshot(manager.root / DB_RELATIVE)
        require(before == current["database"], "REPLAY_DATABASE_IDENTITY_UNVERIFIED")
        image = current["image_id"]
        name = "porota-rc6-iol-replay-" + attempt
        cid = manager.docker.call("container", "create", "--name", name, "--label", "io.porota.scope=rc6-iol-replay",
            "--network", "none", "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
            "--user", "1000:1000", "--cpus", "0.75", "--memory", "512m", "--pids-limit", "96",
            "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=192m", "--env", "POROTA_IOL_OFFHOST_IPC=1",
            "--no-healthcheck", "--entrypoint", "python", image, "-B", "-m", "scripts.rc6_iol_installed_replay")
        require(CID.fullmatch(cid), "REPLAY_CONTAINER_ID_INVALID")

        def inspect():
            item = manager.inspect(cid)
            require(item.get("Name") == "/" + name and item.get("Image") == image and
                    (item.get("Config", {}).get("Labels") or {}).get("io.porota.scope") == "rc6-iol-replay",
                    "REPLAY_CONTAINER_IDENTITY_UNVERIFIED")
            return item

        try:
            item = inspect()
            settings = item["HostConfig"]
            require(settings.get("NetworkMode") == "none" and settings.get("ReadonlyRootfs") is True and
                    not settings.get("Binds") and not settings.get("Mounts") and
                    all(mount.get("Type") == "tmpfs" and mount.get("Destination") == "/tmp"
                        for mount in item.get("Mounts", [])), "REPLAY_ISOLATION_UNVERIFIED")
            proc = manager.docker.command_result("container", "start", "--attach", cid, timeout=385)
            result = decode(proc.stdout.strip())
            require(result.get("schema") == "porota.iol.focus.v1" and
                    result.get("status") in {"PASS", "FAIL"} and proc.returncode == 0 and
                    inspect()["State"].get("OOMKilled") is False, "REPLAY_FIXTURE_RECEIPT_UNVERIFIED")
        finally:
            if inspect()["State"].get("Running") is True:
                manager.docker.call("container", "stop", "--time", "5", cid)
            manager.docker.call("container", "rm", cid)
        require(database_snapshot(manager.root / DB_RELATIVE) == before, "REPLAY_DATABASE_IDENTITY_CHANGED")
        result.update({"source_sha": source_sha, "source_tree": source_tree, "image_id": image,
                       "attempt": attempt, "dataset": "SYNTHETIC_ISOLATED", "network": "NONE",
                       "operational_mounts": 0, "real_orders_sent": 0,
                       "observed_at": datetime.now(timezone.utc).isoformat()})
        return result


def main():
    from scripts.rc6_iol_offhost import run_fixture_suite

    require(os.environ.get("POROTA_IOL_OFFHOST_IPC") == "1" and
            not any(os.getenv(key) for key in ("PAPER_V17_DB_PATH", "PPI_API_KEY", "IOL_PASSWORD", "DO_SSH_KEY")),
            "REPLAY_ISOLATION_REQUIRED")
    print(json.dumps(run_fixture_suite(), sort_keys=True))


if __name__ == "__main__":
    main()
