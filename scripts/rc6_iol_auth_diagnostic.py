"""Inspect both RC6 IOL auth contexts without renewing or copying a token."""

from __future__ import annotations

from datetime import datetime, timezone
import math
import os
import stat
import time

from iol_rest_readonly_rc6 import IOLRestError, IOLRestReadOnly
from scripts.porota_rootless_host import DB_RELATIVE, database_snapshot, decode, no_alias, require


SCHEMA = "porota.iol.auth-diagnostic.v1"
OWNER_PROBE = """import json, os
from scripts.porota_rootless_host import observe_iol_owner
assert os.getenv('POROTA_RC6_MARKET_PROVIDER') == 'IOL_REST'
print(json.dumps({'source_sha': os.getenv('POROTA_BUILD_SHA'),
                  'source_tree': os.getenv('POROTA_CANDIDATE_TREE_SHA'),
                  'owner': observe_iol_owner('/app/data/paper_v17/observer_v17.db')}))
"""


def _owner_status(manager, current):
    try:
        observed = decode(manager.docker.call("container", "exec", current["roles"]["observer"],
                                               "python", "-B", "-c", OWNER_PROBE, timeout=5))
    except (RuntimeError, OSError, ValueError):
        return {"state": "NO_VERIFICADO"}
    if not isinstance(observed, dict) or any(observed.get(key) != current[key] for key in ("source_sha", "source_tree")):
        return {"state": "NO_VERIFICADO"}
    owner = observed.get("owner")
    if not isinstance(owner, dict) or type(owner.get("wire_attempts")) is not int or owner["wire_attempts"] < 0:
        return {"state": "NO_VERIFICADO"}
    return {"state": "AF_UNIX_CONTROL_OBSERVED", "cached_session_current": owner.get("authenticated") is True,
            "wire_attempts": owner["wire_attempts"], "provider_access_tested": False}


def _time_value(value):
    if type(value) in (int, float) and 0 < value < 4102444800 and math.isfinite(value):
        return datetime.fromtimestamp(value, timezone.utc).isoformat()
    return None


def _file_identity(info):
    return info.st_dev, info.st_ino, info.st_mtime_ns


def _inspect_health_cache(client):
    """Bind cache metadata to a consistent, privately held file observation."""
    try:
        directory = no_alias(client.directory)
        meta = directory.stat()
        if not stat.S_ISDIR(meta.st_mode) or meta.st_uid != os.geteuid() or meta.st_mode & 0o077:
            return {"state": "CUSTODY_UNVERIFIED"}, None
        before = no_alias(client.store).stat()
        cached = client._cached()
        if _file_identity(before) != _file_identity(client.store.stat()):
            return {"state": "CHANGED_DURING_READ"}, None
    except FileNotFoundError:
        return {"state": "MISSING"}, None
    except (IOLRestError, OSError, RuntimeError):
        return {"state": "CUSTODY_UNVERIFIED"}, None

    now = time.time()
    retry = cached.get("retry_at")
    renew_at = _time_value(cached.get("renew_at"))
    retry_at = _time_value(retry)
    if renew_at and client._current(cached, now):
        state = "LOCAL_SESSION_CURRENT"
    elif retry_at:
        state = "OAUTH_BACKOFF_ACTIVE" if retry > now else "OAUTH_BACKOFF_ELAPSED"
    elif renew_at:
        state = "BEARER_RENEWAL_DUE"
    else:
        state = "NO_USABLE_BEARER"
    return ({"state": state, "file_updated_at": _time_value(before.st_mtime),
             "renew_at": renew_at, "retry_at": retry_at},
            cached if state == "LOCAL_SESSION_CURRENT" else None)


def _asset_receipt(client, cached):
    if cached is None:
        return {"state": "NOT_PROBED_NO_CURRENT_CACHED_BEARER", "http_status": None}
    client.active = cached
    try:
        asset = client.call("get_asset_info", {"symbol": "GD30", "market": "BCBA"})
    except IOLRestError as error:
        status = error.status_code if type(error.status_code) is int and 100 <= error.status_code < 600 else None
        if str(error) == "IOL_REST_AUTH_ON_HOLD":
            return {"state": "NOT_PROBED_BEARER_RENEWAL_DUE", "http_status": None}
        reasons = {401: "ASSET_ENDPOINT_REJECTED_CACHED_BEARER", 403: "ASSET_ENDPOINT_FORBIDDEN",
                   404: "ASSET_ENDPOINT_NOT_FOUND", 429: "ASSET_ENDPOINT_RATE_LIMITED"}
        return {"state": reasons.get(status, "ASSET_ENDPOINT_HTTP_ERROR" if status else "ASSET_RESPONSE_UNVERIFIED"),
                "http_status": status}
    valid = (asset.get("symbol"), asset.get("market"), asset.get("type"), asset.get("currency"), asset.get("term")) == (
        "GD30", "BCBA", "BONOS", "ARS", "T1")
    return {"state": "REFERENCE_GD30_VERIFIED" if valid else "ASSET_IDENTITY_UNVERIFIED", "http_status": None}


def observe_auth(manager, current, *, probe_asset=True):
    """Accept only a verified IOL release under the caller's shared release lock."""
    require(current.get("state") == "VALIDADO_RUNTIME" and current.get("market_provider") == "IOL_REST",
            "INSTALLED_IOL_RELEASE_REQUIRED")
    before = database_snapshot(manager.root / DB_RELATIVE)
    require(before == current.get("database"), "DIAGNOSTIC_DATABASE_IDENTITY_CHANGED")
    owner = _owner_status(manager, current)
    health = IOLRestReadOnly(manager.identity.state / "iol-rest", allow_renew=False)
    cache, cached = _inspect_health_cache(health)
    endpoint = (_asset_receipt(health, cached) if probe_asset else
                {"state": "NOT_PROBED_READ_ONLY_EVIDENCE", "http_status": None})
    after = database_snapshot(manager.root / DB_RELATIVE)
    require(after == before, "DIAGNOSTIC_DATABASE_IDENTITY_CHANGED")
    return {
        "schema": SCHEMA, "state": "OBSERVED", "observed_at": datetime.now(timezone.utc).isoformat(),
        **{key: current[key] for key in ("source_sha", "source_tree", "image_id", "attempt")},
        "read_only": True, "real_orders_sent": after["real_orders_sent"], "owner": owner,
        "independent_health": {"cache": cache, "asset": endpoint},
        "oauth_grants_sent": 0, "ppi_watch_queried": False,
    }
