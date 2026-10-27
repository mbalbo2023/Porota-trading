"""Compare bounded IOL MCP OHLC anomalies with BYMADATA public history.

This offhost reader makes at most six account-free POST queries: two literal
BYMA ``homogeneous`` variants for at most three anomalous economic days.
The independent result can identify disagreement, but its payload contains
date/OHLC/nominal volume only. Symbol, T1/24hs, currency and variant semantics
remain request assertions, and neither response reconstructs publication PIT.
Every selected IOL row remains quarantined. No canonical or legacy write.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
from datetime import date, datetime, timezone
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from urllib.request import HTTPRedirectHandler, Request, build_opener

from cu_history_store_v2_hf6 import instant
from scripts.rc6_history_fresh_sidecar import _body, _day, _ohlc_range_conflict, preflight_destination
from scripts.rc6_history_fresh_verify import verify

ENDPOINT = ("https://open.bymadata.com.ar/vanoms-be-core/rest/api/"
            "bymadata/free/bnown/seriesHistoricas/especies")
MAX_DAYS = 3
MAX_BODY_BYTES = 256_000
FIELDS = {"open": "PrecioAper", "high": "precioMaximo", "low": "precioMinimo",
          "close": "precioCierre", "volume": "volumenNominal"}
ROW_KEYS = {"fecha", *FIELDS.values()}
TOP_KEYS = {"content", "data", "empty", "upgrade"}
PAGE_KEYS = {"page_number", "page_count", "page_size", "total_elements_count"}
ERROR_CODES = frozenset({
    "BYMA_HISTORY_REDIRECT_FORBIDDEN", "BYMA_HISTORY_TRANSPORT_INVALID",
    "BYMA_HISTORY_BODY_BUDGET", "BYMA_HISTORY_JSON_INVALID",
    "BYMA_HISTORY_DUPLICATE_KEY", "BYMA_HISTORY_RESPONSE_SHAPE_INVALID",
    "BYMA_HISTORY_DATE_MISMATCH", "BYMA_HISTORY_NUMBER_INVALID",
})


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        raise ValueError("BYMA_HISTORY_REDIRECT_FORBIDDEN")


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("BYMA_HISTORY_DUPLICATE_KEY")
        result[key] = value
    return result


def _nonfinite(_value):
    raise ValueError("BYMA_HISTORY_NUMBER_INVALID")


def _number(value):
    if type(value) not in (int, Decimal):
        raise ValueError("BYMA_HISTORY_NUMBER_INVALID")
    number = Decimal(value)
    if not number.is_finite() or number < 0:
        raise ValueError("BYMA_HISTORY_NUMBER_INVALID")
    return number


def parse_response(body, day):
    """Require exactly one date row; return numbers only inside the process."""
    if not isinstance(body, bytes) or len(body) > MAX_BODY_BYTES:
        raise ValueError("BYMA_HISTORY_BODY_BUDGET")
    try:
        payload = json.loads(body.decode("utf-8"), object_pairs_hook=_pairs,
                             parse_float=Decimal, parse_constant=_nonfinite)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("BYMA_HISTORY_JSON_INVALID") from exc
    if not isinstance(payload, dict) or set(payload) != TOP_KEYS or (
        payload["empty"] is not False or payload["upgrade"] is not False or
        not isinstance(payload["content"], dict) or
        set(payload["content"]) != PAGE_KEYS or
        type(payload["content"]["page_number"]) is not int or
        type(payload["content"]["page_count"]) is not int or
        type(payload["content"]["page_size"]) is not int or
        type(payload["content"]["total_elements_count"]) is not int or
        payload["content"]["page_number"] != 1 or
        payload["content"]["page_count"] != 1 or
        payload["content"]["total_elements_count"] != 1 or
        not 1 <= payload["content"]["page_size"] <= 1000 or
        not isinstance(payload["data"], list) or len(payload["data"]) != 1 or
        not isinstance(payload["data"][0], dict) or set(payload["data"][0]) != ROW_KEYS
    ):
        raise ValueError("BYMA_HISTORY_RESPONSE_SHAPE_INVALID")
    row = payload["data"][0]
    if not isinstance(row["fecha"], str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2} 00:00:00(?:\.0)?", row["fecha"]
    ) or row["fecha"][:10] != day:
        raise ValueError("BYMA_HISTORY_DATE_MISMATCH")
    try:
        if date.fromisoformat(row["fecha"][:10]).isoformat() != day:
            raise ValueError("BYMA_HISTORY_DATE_MISMATCH")
    except ValueError as exc:
        raise ValueError("BYMA_HISTORY_DATE_MISMATCH") from exc
    return {name: _number(row[field]) for name, field in FIELDS.items()}


def request_body(symbol, day, variant):
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9._-]{0,39}", symbol) or not (
        re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) and variant in {"0", "1"}
    ):
        raise ValueError("BYMA_HISTORY_REQUEST_INVALID")
    try:
        if date.fromisoformat(day).isoformat() != day:
            raise ValueError("BYMA_HISTORY_REQUEST_INVALID")
    except ValueError as exc:
        raise ValueError("BYMA_HISTORY_REQUEST_INVALID") from exc
    return _body({"symbol": symbol, "maturity": "2", "fromDate": day,
                  "toDate": day, "homogeneous": variant, "period": "1"})


def public_post(symbol, day, variant):
    """One exact HTTPS public history query; no retry or account material."""
    request = Request(ENDPOINT, data=request_body(symbol, day, variant), method="POST",
                      headers={"Accept": "application/json",
                               "Content-Type": "application/json", "User-Agent": "curl/8.5.0"})
    with build_opener(_NoRedirect()).open(request, timeout=10) as response:
        if (response.geturl() != ENDPOINT or response.status != 200 or
                response.headers.get_content_type() != "application/json"):
            raise ValueError("BYMA_HISTORY_TRANSPORT_INVALID")
        body = response.read(MAX_BODY_BYTES + 1)
    if len(body) > MAX_BODY_BYTES:
        raise ValueError("BYMA_HISTORY_BODY_BUDGET")
    return body


def _capture(path, clock):
    checked = verify(path, now=clock)
    if checked["captures"] != 1:
        raise ValueError("BYMA_HISTORY_CAPTURE_SCOPE_INVALID")
    uri = Path(path).absolute().as_uri() + "?mode=ro&immutable=1"
    with sqlite3.connect(uri, uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        capture = db.execute("SELECT * FROM fresh_captures_rc6").fetchone()
        if capture["source"] != "IOL_CONNECTOR" or capture["market"] != "BCBA" or (
            capture["settlement"] != "T1"
        ):
            raise ValueError("BYMA_HISTORY_CAPTURE_IDENTITY_UNVERIFIED")
        revisions = db.execute("SELECT * FROM fresh_row_revisions_rc6 ORDER BY retained_ordinal").fetchall()
        bars = json.loads(capture["retained_rows_json"])
    if checked["sidecar_sha256"] != verify(path, now=clock)["sidecar_sha256"]:
        raise ValueError("BYMA_HISTORY_CAPTURE_CHANGED")
    return checked, capture, bars, revisions


def compare(sidecar_path, days, *, post=None, now=None):
    """Only selected anomalous dates; result contains no provider prices."""
    clock = instant(now or datetime.now(timezone.utc))
    checked, capture, bars, revisions = _capture(sidecar_path, clock)
    if not isinstance(days, (tuple, list)) or not 1 <= len(days) <= MAX_DAYS or (
        len(set(days)) != len(days)
    ):
        raise ValueError("BYMA_HISTORY_DAY_BUDGET_INVALID")
    selected = {}
    for bar, revision in zip(bars, revisions):
        day = _day(bar, "IOL_CONNECTOR", instant(capture["first_known_at"])).isoformat()
        if day != revision["economic_day"]:
            raise ValueError("BYMA_HISTORY_REVISION_MISMATCH")
        if day in days:
            if day in selected or not _ohlc_range_conflict(bar, "IOL_CONNECTOR"):
                raise ValueError("BYMA_HISTORY_ANOMALY_REQUIRED")
            selected[day] = (bar, revision)
    if set(selected) != set(days):
        raise ValueError("BYMA_HISTORY_ANOMALY_REQUIRED")
    results = []
    for day in sorted(selected):
        bar, revision = selected[day]
        variants = []
        for variant in ("0", "1"):
            body = request_body(capture["symbol"], day, variant)
            item = {"requested_identity": {
                "symbol": capture["symbol"], "market": "BCBA", "maturity_code": "2",
                "maturity_label_from_public_ui": "24hs", "economic_day": day,
                "homogeneous_literal": variant,
                "iol_capture_currency_not_in_byma_request": capture["currency"],
                "iol_capture_settlement_not_in_byma_response": capture["settlement"],
                "semantics": "REQUEST_ASSERTION_ONLY"},
                "request_body_sha256": sha256(body).hexdigest(),
                "status": "UNAVAILABLE", "failure_reason": None,
                "observed_at": None, "response_sha256": None,
                "https_origin_observed": False,
                "matching_fields": [], "differing_fields": [], "byma_ohlc_valid": None,
                "response_identity_fields": [],
                "response_currency_verified": False, "response_settlement_verified": False,
                "adjustment_basis_verified": False,
                "provider_original_pit_verified": False}
            try:
                response = (post or public_post)(capture["symbol"], day, variant)
                observed = datetime.now(timezone.utc).isoformat()
                values = parse_response(response, day)
                item.update(status="OBSERVED", observed_at=observed,
                            response_sha256=sha256(response).hexdigest(),
                            https_origin_observed=post is None,
                            matching_fields=sorted(name for name in FIELDS
                                                   if Decimal(str(bar[name])) == values[name]),
                            differing_fields=sorted(name for name in FIELDS
                                                    if Decimal(str(bar[name])) != values[name]),
                            byma_ohlc_valid=(values["low"] <= min(values["open"], values["close"])
                                             and values["high"] >= max(values["open"], values["close"])))
            except Exception as exc:
                item["failure_reason"] = (str(exc) if isinstance(exc, ValueError) and
                                          str(exc) in ERROR_CODES else
                                          "BYMA_HISTORY_SOURCE_UNAVAILABLE")
            variants.append(item)
        results.append({"economic_day": day,
                        "iol_provider_row_index": revision["provider_row_index"],
                        "iol_canonical_row_sha256": revision["canonical_row_sha256"],
                        "iol_row_state": "QUARANTINED_OHLC_RANGE_CONFLICT",
                        "post_crosscheck_state": "QUARANTINED_REVIEW_REQUIRED",
                        "variants": variants})
    return {"state": "EVIDENCE_ONLY_NO_GO", "source": "IOL_CONNECTOR_BYMA_PUBLIC",
            "byma_endpoint": ENDPOINT, "capture_sha256": checked["sidecar_sha256"],
            "selected_days": len(results), "provider_post_invocations": len(results) * 2,
            "rows": results, "same_row_identity_provider_attested": False,
            "historical_currency_verified": False, "historical_settlement_verified": False,
            "adjustment_basis_verified": False, "provider_original_pit_verified": False,
            "history_v2_writes": 0, "execution_allowed": False,
            "ready_paper_implication": "NONE"}


def write_receipt(sidecar_path, days, receipt_path, *, post=None, now=None):
    destination = preflight_destination(receipt_path, protected_paths=(sidecar_path,))
    if not destination.parent.is_dir():
        raise ValueError("BYMA_HISTORY_RECEIPT_PARENT_MISSING")
    result = compare(sidecar_path, days, post=post, now=now)
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        output.write(_body(result) + b"\n")
        output.flush()
        os.fsync(output.fileno())
    directory = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sidecar", required=True, type=Path)
    parser.add_argument("--day", required=True, action="append")
    parser.add_argument("--receipt", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = write_receipt(args.sidecar, args.day, args.receipt)
    except (ValueError, OSError):
        parser.exit(2, "BYMA_HISTORY_CROSSCHECK_FAILED\n")
    print(json.dumps({"state": result["state"], "selected_days": result["selected_days"],
                      "provider_post_invocations": result["provider_post_invocations"],
                      "history_v2_writes": 0, "execution_allowed": False}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
