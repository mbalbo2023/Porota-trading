"""Import one offhost IOL MCP historical response into a private RC6 evidence sidecar.

The response file is the JSON ``structuredContent`` from
``iol_invertironline_get_price_history``: ``{"bars": [...]}``. The separate
manifest is a *request assertion*, not a provider attestation, for example::

    {"version": 1, "declared_source": "IOL_MCP",
     "request": {"symbol": "GGAL", "market": "BCBA",
                 "from_date": "2025-10-27", "to_date": "2026-10-26"},
     "target": ["GGAL", "ACCIONES", "BCBA", "ARS", "T1"]}

Only one exact query, at most 1000 bars and the current 365-day ART window are
accepted. The sidecar's first_known_at is this import's observation time; it
does not reconstruct the provider's historical publication or revision time.
No currency, settlement, adjustment basis, or authenticated MCP origin is
inferred from the request assertion. Nothing writes legacy or history-v2 data.
Keep response, manifest, and SQLite private; publish only the sanitized receipt.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import stat
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

from rc6_data_retention import daily_start
from scripts.rc6_history_fresh_sidecar import (
    ART, MAX_PAYLOAD_BYTES, MAX_ROWS, _body, _day, _ohlc_range_conflict,
    capture, preflight,
    preflight_destination,
)
from scripts.rc6_history_fresh_verify import verify

MAX_MANIFEST_BYTES = 4096
BAR_KEYS = {"date", "time", "open", "high", "low", "close", "volume"}
MARKETS = {"BCBA", "NYSE", "NASDAQ", "AMEX"}


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("IOL_MCP_JSON_DUPLICATE_KEY")
        result[key] = value
    return result


def _invalid_number(_constant):
    raise ValueError("IOL_MCP_JSON_NONFINITE")


def _document(path, limit):
    path = Path(path).absolute()
    if path.resolve() != path or any(parent.is_symlink() for parent in path.parents):
        raise ValueError("IOL_MCP_INPUT_ALIAS_FORBIDDEN")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("IOL_MCP_INPUT_ALIAS_FORBIDDEN")
            if info.st_size > limit:
                raise ValueError("IOL_MCP_INPUT_BUDGET_EXCEEDED")
            raw = source.read(limit + 1)
    except OSError as exc:
        raise ValueError("IOL_MCP_INPUT_UNAVAILABLE") from exc
    if len(raw) > limit:
        raise ValueError("IOL_MCP_INPUT_BUDGET_EXCEEDED")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs,
                           parse_constant=_invalid_number)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("IOL_MCP_JSON_INVALID") from exc
    return path, value, sha256(raw).hexdigest()


def _exact_keys(value, expected):
    return isinstance(value, dict) and set(value) == expected


def _manifest(value, clock):
    if not _exact_keys(value, {"version", "declared_source", "request", "target"}):
        raise ValueError("IOL_MCP_MANIFEST_INVALID")
    if type(value["version"]) is not int or value["version"] != 1 or value["declared_source"] != "IOL_MCP":
        raise ValueError("IOL_MCP_MANIFEST_INVALID")
    request = value["request"]
    if not _exact_keys(request, {"symbol", "market", "from_date", "to_date"}):
        raise ValueError("IOL_MCP_REQUEST_INVALID")
    target = value["target"]
    if not isinstance(target, list) or len(target) != 5 or not all(
        isinstance(part, str) and re.fullmatch(r"[A-Z0-9][A-Z0-9._-]{0,39}", part)
        for part in target
    ):
        raise ValueError("IOL_MCP_IDENTITY_INVALID")
    if target[2] not in MARKETS or request["symbol"] != target[0] or request["market"] != target[2]:
        raise ValueError("IOL_MCP_REQUEST_IDENTITY_CONFLICT")
    target = preflight([target], "IOL_CONNECTOR")[0]
    start, end = daily_start(clock), clock.astimezone(ART).date()
    if request["from_date"] != start.isoformat() or request["to_date"] != end.isoformat():
        raise ValueError("IOL_MCP_REQUEST_WINDOW_MISMATCH")
    return target, start, end


def _response(value, start, end, clock):
    if not _exact_keys(value, {"bars"}) or not isinstance(value["bars"], list):
        raise ValueError("IOL_MCP_RESPONSE_SHAPE_INVALID")
    bars = value["bars"]
    anomalies = []
    if len(bars) > MAX_ROWS or len(_body(bars)) > MAX_PAYLOAD_BYTES:
        raise ValueError("IOL_MCP_RESPONSE_BUDGET_EXCEEDED")
    for index, bar in enumerate(bars):
        if not _exact_keys(bar, BAR_KEYS):
            raise ValueError("IOL_MCP_BAR_SHAPE_INVALID")
        if not isinstance(bar["date"], str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", bar["date"]):
            raise ValueError("IOL_MCP_BAR_DATE_INVALID")
        if type(bar["time"]) is not int:
            raise ValueError("IOL_MCP_BAR_CLOCK_INVALID")
        try:
            day = _day(bar, "IOL_CONNECTOR", clock)
        except (ValueError, TypeError, OverflowError) as exc:
            raise ValueError("IOL_MCP_BAR_CLOCK_INVALID") from exc
        if not start <= day <= end:
            raise ValueError("IOL_MCP_BAR_OUTSIDE_REQUEST")
        for field in BAR_KEYS - {"date", "time"}:
            number = bar[field]
            if type(number) not in (int, float) or not math.isfinite(number) or number < 0:
                raise ValueError("IOL_MCP_BAR_NUMBER_INVALID")
        # Some actual MCP bars report close outside low/high. Preserve those
        # original values as evidence; the sidecar quarantines the capture.
        if _ohlc_range_conflict(bar, "IOL_CONNECTOR"):
            anomalies.append({"provider_row_index": index, "economic_day": day.isoformat()})
    return bars, anomalies


def import_response(manifest_path, response_path, sidecar_path, receipt_path, *, now=None):
    """Stage an exact local MCP export and create a sanitized private receipt."""
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None or abs((datetime.now(timezone.utc) - clock).total_seconds()) > 300:
        raise ValueError("IOL_MCP_CLOCK_UNVERIFIED")
    manifest_path, manifest, manifest_hash = _document(manifest_path, MAX_MANIFEST_BYTES)
    response_path, response, response_hash = _document(response_path, MAX_PAYLOAD_BYTES)
    if manifest_path == response_path:
        raise ValueError("IOL_MCP_INPUT_ALIAS_FORBIDDEN")
    target, start, end = _manifest(manifest, clock)
    bars, anomalies = _response(response, start, end, clock)
    sidecar_path = preflight_destination(sidecar_path, protected_paths=(manifest_path, response_path, receipt_path))
    receipt_path = preflight_destination(receipt_path, protected_paths=(manifest_path, response_path, sidecar_path))
    if not receipt_path.parent.is_dir():
        raise ValueError("IOL_MCP_RECEIPT_PARENT_MISSING")
    result = capture(sidecar_path, [target], "IOL_CONNECTOR", lambda *_: bars,
                     now=clock, protected_paths=(manifest_path, response_path, receipt_path))
    checked = verify(sidecar_path)
    canonical_hash = sha256(_body(bars)).hexdigest()
    if (checked["state"] != "EVIDENCE_ONLY_NO_GO" or checked["captures"] != 1 or
            result["results"][0]["canonical_payload_sha256"] != canonical_hash or
            checked["ohlc_range_conflict_rows"] != len(anomalies) or
            (anomalies and result["results"][0]["status"] != "QUARANTINED")):
        raise ValueError("IOL_MCP_LOCAL_INTEGRITY_INVALID")
    result.pop("generation_path")
    result["local_fetch_invocations"] = result.pop("fetch_invocations")
    result.update({
        "declared_request": {"symbol": target[0], "market": target[2],
                             "from_date": start.isoformat(), "to_date": end.isoformat()},
        "manifest_sha256": manifest_hash,
        "response_file_sha256": response_hash,
        "sidecar_sha256": checked["sidecar_sha256"],
        "mcp_requests_during_import": 0,
        "provider_origin_attested": False,
        "request_identity_provider_attested": False,
        "transport_raw_hash_verified": False,
        "provider_original_pit_verified": False,
        "settlement_verified_from_bars": False,
        "currency_verified_from_bars": False,
        "adjustment_basis_verified": False,
        "first_known_at_semantics": "LOCAL_IMPORT_OBSERVATION_ONLY",
        "ohlc_range_conflicts": {"count": len(anomalies),
                                 "sample": anomalies[:20], "sample_limit": 20,
                                 "sample_truncated": len(anomalies) > 20},
    })
    receipt = (json.dumps(result, sort_keys=True, indent=2) + "\n").encode("utf-8")
    descriptor = os.open(receipt_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        output.write(receipt)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--response", required=True, type=Path)
    parser.add_argument("--sidecar", required=True, type=Path)
    parser.add_argument("--receipt", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = import_response(args.manifest, args.response, args.sidecar, args.receipt)
    except (ValueError, OSError) as exc:
        code = str(exc) if isinstance(exc, ValueError) and str(exc).startswith(("IOL_MCP_", "FRESH_")) else "IOL_MCP_IMPORT_ERROR"
        parser.exit(2, code + "\n")
    print(json.dumps({"state": result["state"], "source": result["source"],
                      "retained_rows": result["results"][0]["retained_rows"],
                      "capture_status": result["results"][0]["status"],
                      "ohlc_range_conflict_rows": result["ohlc_range_conflicts"]["count"],
                      "history_v2_writes": 0, "execution_allowed": False}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
