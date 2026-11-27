"""Inspect both RC6 IOL auth contexts without renewing or copying a token."""

from __future__ import annotations

from datetime import datetime, timezone
import math
import os
import stat
import time
from urllib.error import HTTPError

from iol_rest_readonly_rc6 import API, IOLRestError, IOLRestReadOnly
from scripts.porota_rootless_host import DB_RELATIVE, database_snapshot, decode, no_alias, require


SCHEMA = "porota.iol.auth-diagnostic.v1"
OWNER_PROBE = """import json, os
from scripts.porota_rootless_host import observe_iol_owner
assert os.getenv('POROTA_RC6_MARKET_PROVIDER') == 'IOL_REST'
print(json.dumps({'source_sha': os.getenv('POROTA_BUILD_SHA'),
                  'source_tree': os.getenv('POROTA_CANDIDATE_TREE_SHA'),
                  'owner': observe_iol_owner('/app/data/paper_v17/observer_v17.db')}))
"""


class _SingleReferenceGET:
    """Observe one GET on the existing no-redirect IOL transport."""

    def __init__(self, opener, route="/broker/assets/GD30?market=BCBA"):
        self.opener = opener
        self.route = route
        self.attempts = 0
        self.http_status = None

    def open(self, request, *, timeout):
        if (self.attempts or request.get_method() != "GET" or
                request.full_url != API + self.route):
            raise PermissionError("IOL_DIAGNOSTIC_ROUTE_DENIED")
        self.attempts += 1
        try:
            response = self.opener.open(request, timeout=timeout)
        except HTTPError as error:
            self._status(error.code)
            raise
        self._status(response.getcode())
        return response

    def _status(self, value):
        if type(value) is int and 100 <= value <= 599:
            self.http_status = value


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


def observe_gd30_market_pair(state_dir):
    """Record only shape and clock evidence, within two GETs and zero grants."""
    client = IOLRestReadOnly(state_dir / "iol-rest", allow_renew=False)
    cache, cached = _inspect_health_cache(client)
    if cached is None:
        return {"state": "NO_CURRENT_CACHED_BEARER", "cache": cache, "wire_attempts": 0,
                "endpoints": {}}
    client.active = cached
    opener = client.opener
    endpoints = {}
    attempts = 0
    for endpoint in ("quote", "depth"):
        route = f"/broker/market-data/GD30/{endpoint}?market=BCBA&term=T1"
        transport = _SingleReferenceGET(opener, route)
        client.opener = transport
        try:
            payload = client.read_market_data(endpoint, "GD30", "BCBA", "T1")
        except IOLRestError as error:
            endpoints[endpoint] = {"state": "READ_HOLD", "http_status": transport.http_status}
            attempts += transport.attempts
            if not transport.attempts or error.status_code in (401, 403, 429):
                break
            continue
        attempts += transport.attempts
        exact = (isinstance(payload, dict) and
                 (payload.get("symbol"), payload.get("market"), payload.get("settlement_term")) == ("GD30", "BCBA", "T1"))
        currency = payload.get("currency") if exact else None
        matched = exact and (currency == "ARS" or endpoint == "depth" and currency is None)
        item = {"state": "OBSERVED" if matched else "RESPONSE_IDENTITY_UNVERIFIED",
                "http_status": transport.http_status, "currency_explicit_and_matching": currency == "ARS"}
        if matched:
            value = payload.get("last_updated")
            if isinstance(value, str):
                try:
                    updated = datetime.fromisoformat(value)
                    if updated.utcoffset() is not None and updated <= datetime.now(timezone.utc):
                        item["provider_update_at"] = updated.astimezone(timezone.utc).isoformat()
                except (ValueError, OverflowError):
                    pass
            if endpoint == "depth":
                item["two_sided"] = all(isinstance(payload.get(side), list) and payload[side]
                                        for side in ("bids", "asks"))
            else:
                prices = payload.get("prices")
                item["current_present"] = isinstance(prices, dict) and type(prices.get("current")) in (int, float)
        endpoints[endpoint] = item
    return {"state": "OBSERVED", "cache": cache, "wire_attempts": attempts, "endpoints": endpoints}


def observe_auth(manager, current, *, probe_asset=True):
    """Accept only a verified IOL release under the caller's shared release lock."""
    require(current.get("state") == "VALIDADO_RUNTIME" and current.get("market_provider") == "IOL_REST",
            "INSTALLED_IOL_RELEASE_REQUIRED")
    before = database_snapshot(manager.root / DB_RELATIVE)
    require(before == current.get("database"), "DIAGNOSTIC_DATABASE_IDENTITY_CHANGED")
    owner = _owner_status(manager, current)
    health = IOLRestReadOnly(manager.identity.state / "iol-rest", allow_renew=False)
    transport = _SingleReferenceGET(health.opener)
    health.opener = transport
    cache, cached = _inspect_health_cache(health)
    endpoint = (_asset_receipt(health, cached) if probe_asset else
                {"state": "NOT_PROBED_READ_ONLY_EVIDENCE", "http_status": None})
    endpoint["http_status"] = transport.http_status
    after = database_snapshot(manager.root / DB_RELATIVE)
    require(after == before, "DIAGNOSTIC_DATABASE_IDENTITY_CHANGED")
    return {
        "schema": SCHEMA, "state": "OBSERVED", "observed_at": datetime.now(timezone.utc).isoformat(),
        **{key: current[key] for key in ("source_sha", "source_tree", "image_id", "attempt")},
        "read_only": True, "real_orders_sent": after["real_orders_sent"], "owner": owner,
        "independent_health": {"cache": cache, "asset": endpoint},
        "oauth_grants_sent": 0, "market_requests_generated": transport.attempts, "ppi_watch_queried": False,
    }
