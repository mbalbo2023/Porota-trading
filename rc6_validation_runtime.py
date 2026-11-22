"""Read-only safety and infrastructure evidence for the daily validation campaign."""
from datetime import datetime, timezone
import json
from pathlib import Path
import re

from rc6_scheduler_snapshot import EXPORT_UNIT, freshness_budget, instant


POSTCLOSE_UNIT = "porota-snapshot-postclose-rc6.timer"
PROOF_SOURCE = "porota.rootless.postclose-validation.v1"
IDENTITY = {
    "candidate_sha": r"[0-9a-f]{40}",
    "candidate_tree_sha": r"[0-9a-f]{40}",
    "deployment_sha": r"[0-9a-f]{40}",
    "image_id": r"sha256:[0-9a-f]{64}",
    "download_receipt_sha256": r"[0-9a-f]{64}",
}


def read_object(path, max_bytes=256 * 1024):
    try:
        with Path(path).open("rb") as handle:
            raw = handle.read(max_bytes + 1)
        if len(raw) <= max_bytes:
            value = json.loads(raw)
            if isinstance(value, dict):
                return value
    except (OSError, ValueError, TypeError, RecursionError):
        pass
    return {}


def bind_deploy(data, *, build, tree, now):
    marker = read_object(data / "deploy" / "CURRENT_STATE_V2.json")
    recorded = instant(marker.get("recorded_at"))
    if (marker.get("validation_status") != "VALIDATED_RUNTIME"
            or marker.get("deployment_backend") != "ROOTLESS_SPLIT"
            or marker.get("candidate_sha") != build or marker.get("candidate_tree_sha") != tree
            or recorded is None or recorded > now
            or any(not re.fullmatch(pattern, str(marker.get(key) or ""))
                   for key, pattern in IDENTITY.items())):
        return {}
    return marker


def scheduler_status(data, now):
    snapshot = read_object(data / "scheduler" / "systemd_timers.json")
    captured = instant(snapshot.get("recorded_at"))
    rows = snapshot.get("timers")
    if (snapshot.get("schema") != "porota-scheduler-systemd-v1" or snapshot.get("state") != "OK"
            or not isinstance(rows, list) or captured is None):
        return "NO_VERIFICADO"
    if not 0 <= (now - captured).total_seconds() <= freshness_budget(captured, now, len(rows)):
        return "STALE"
    required = (EXPORT_UNIT, POSTCLOSE_UNIT)
    for unit in required:
        matches = [r for r in rows if isinstance(r, dict) and r.get("unit") == unit]
        if len(matches) != 1:
            return "NO_VERIFICADO"
        row = matches[0]
        if row.get("enabled_state") != "enabled" or row.get("active_state") != "active":
            return "NO_VERIFICADO"
        # The export must have run successfully. The postclose service can
        # be in progress at this observation, so its outcome is separate.
        if unit == EXPORT_UNIT and (row.get("last_result") != "success"
                                    or str(row.get("exec_main_status")) != "0"
                                    or not row.get("last_trigger")):
            return "NO_VERIFICADO"
    return "VERIFICADO"


def observations(data, observer, proof, *, build, tree, now=None):
    now = now or datetime.now(timezone.utc)
    marker = bind_deploy(data, build=build, tree=tree, now=now)
    mode = read_object(data / "operation_mode.json")
    apis = mode.get("apis")
    apis = apis if isinstance(apis, dict) else {}
    stamp = instant(proof.get("observed_at"))
    bound = (bool(marker) and proof.get("source") == PROOF_SOURCE
             and all(proof.get(key) == marker[key] for key in IDENTITY)
             and stamp is not None and 0 <= (now - stamp).total_seconds() <= 120)
    beat = instant(observer.get("heartbeat_at"))
    live = (bound and beat is not None and beat >= instant(marker["recorded_at"])
            and 0 <= (now - beat).total_seconds() <= 120)
    criteria = {
        "PRODUCTION_PAPER": live and observer.get("mode") == mode.get("mode") == marker.get("mode") == "PRODUCTION_PAPER",
        "SIMULATED": live and mode.get("execution") == "SIMULATED",
        "real_orders_sent=0": (live and type(observer.get("real_orders_sent")) is int
                               and observer["real_orders_sent"] == 0
                               and type(marker.get("real_orders_sent")) is int
                               and marker["real_orders_sent"] == 0),
        "order routing blocked": (live and apis.get("PPI_ORDERS") == marker.get("real_order_capability")
                                  == proof.get("orders_guard") == "BLOCKED"
                                  and marker.get("real_order_routes") in {"NOT_CALLED", "BLOCKED"}),
    }
    policy = read_object(Path(__file__).with_name("ops") / "policy" / "rc6-disk-housekeeping-v1.json")
    disk_policy = policy.get("disk") or {}
    reserve = disk_policy.get("green_free_bytes")
    disk = (bound and type(reserve) is int and reserve > 0
            and all(type(proof.get(k)) is int and proof[k] >= reserve
                    for k in ("data_free_bytes", "docker_free_bytes")))
    infrastructure = {
        "disco": "VERIFICADO" if disk else "NO_VERIFICADO",
        "Docker": "VERIFICADO" if bound and proof.get("rootless_pair") is True else "NO_VERIFICADO",
        "backups/restore": ("SUSPENDIDO_POR_OPERADOR" if (policy.get("backups") or {}).get("suspended") is True
                            else "NO_VERIFICADO"),
        "timers": scheduler_status(data, now),
        "restart behavior": ("VERIFICADO" if live and proof.get("startup_verified") is True
                             else "NO_VERIFICADO"),
    }
    identity = {key: marker[key] for key in IDENTITY} if bound else {}
    return {"M0": infrastructure, "M1": {k: "VERIFICADO" if v else "NO_VERIFICADO"
                                          for k, v in criteria.items()},
            "release": identity, "observed_at": stamp.isoformat() if bound else None,
            "heartbeat_at": beat.isoformat() if live else None}
