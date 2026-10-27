"""Audit one private IOL MCP capture against IOL's public historical table.

The public GET is account-free and bounded. It can corroborate displayed
OHLCV values, including contradictory ones; it cannot repair them, certify a
historical currency/settlement/adjustment basis, or reconstruct publication
time. This module never modifies the capture, legacy history, or history-v2.
Only a sanitized, private receipt is written to a new offhost destination.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from cu_history_store_v2_hf6 import instant
from scripts.rc6_history_fresh_sidecar import (
    ART, MAX_ROWS, _body, _day, _ohlc_range_conflict, preflight_destination,
)
from scripts.rc6_history_fresh_verify import verify

PUBLIC_HOST = "https://iol.invertironline.com"
PUBLIC_LIMIT_BYTES = 2_000_000
PUBLIC_CODES = frozenset({
    "IOL_PUBLIC_REDIRECT_FORBIDDEN", "IOL_PUBLIC_TRANSPORT_UNVERIFIED",
    "IOL_PUBLIC_BODY_INVALID", "IOL_PUBLIC_TABLE_IDENTITY_UNVERIFIED",
    "IOL_PUBLIC_ROW_BUDGET_INVALID", "IOL_PUBLIC_ROW_SHAPE_INVALID",
    "IOL_PUBLIC_DAY_INVALID", "IOL_PUBLIC_DUPLICATE_DAY",
    "IOL_PUBLIC_NUMBER_INVALID",
})
HEADERS = ("Fecha Cotización", "Apertura", "Máximo", "Mínimo", "Cierre",
           "Cierre ajustado", "Volumen Monto", "Volumen Nominal")


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        raise ValueError("IOL_PUBLIC_REDIRECT_FORBIDDEN")


class _HistoryTable(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.symbols = []
        self.tables = 0
        self.in_table = False
        self.section = None
        self.in_cell = False
        self.cell = ""
        self.cells = []
        self.headings = []
        self.rows = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "input" and attributes.get("id") == "Simbolo":
            self.symbols.append(attributes.get("value"))
        if tag == "table" and attributes.get("id") == "tbcotizaciones":
            self.tables += 1
            self.in_table = True
        elif self.in_table and tag in {"thead", "tbody"}:
            self.section = tag
        elif self.in_table and tag == "tr":
            self.cells = []
        elif self.in_table and tag in {"th", "td"}:
            self.in_cell = True
            self.cell = ""

    def handle_data(self, data):
        if self.in_cell:
            self.cell += data

    def handle_endtag(self, tag):
        if self.in_table and tag in {"th", "td"} and self.in_cell:
            self.cells.append(" ".join(self.cell.split()))
            self.in_cell = False
        elif self.in_table and tag == "tr" and self.cells:
            if self.section == "thead":
                self.headings.append(tuple(self.cells))
            elif self.section == "tbody":
                self.rows.append(tuple(self.cells))
            self.cells = []
        elif self.in_table and tag in {"thead", "tbody"}:
            self.section = None
        elif self.in_table and tag == "table":
            self.in_table = False


def _decimal(value):
    if not re.fullmatch(r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?", value):
        raise ValueError("IOL_PUBLIC_NUMBER_INVALID")
    try:
        number = Decimal(value.replace(",", ""))
    except InvalidOperation as exc:
        raise ValueError("IOL_PUBLIC_NUMBER_INVALID") from exc
    if not number.is_finite() or number < 0:
        raise ValueError("IOL_PUBLIC_NUMBER_INVALID")
    return number


def parse_public_history(html, symbol):
    if not isinstance(html, bytes) or len(html) > PUBLIC_LIMIT_BYTES:
        raise ValueError("IOL_PUBLIC_BODY_INVALID")
    try:
        document = html.decode("utf-8")
    except UnicodeError as exc:
        raise ValueError("IOL_PUBLIC_BODY_INVALID") from exc
    parser = _HistoryTable()
    parser.feed(document)
    if (parser.in_table or parser.tables != 1 or parser.symbols != [symbol] or
            parser.headings != [HEADERS]):
        raise ValueError("IOL_PUBLIC_TABLE_IDENTITY_UNVERIFIED")
    if not parser.rows or len(parser.rows) > MAX_ROWS:
        raise ValueError("IOL_PUBLIC_ROW_BUDGET_INVALID")
    result = {}
    for row in parser.rows:
        if len(row) != len(HEADERS):
            raise ValueError("IOL_PUBLIC_ROW_SHAPE_INVALID")
        try:
            day = datetime.strptime(row[0], "%m/%d/%Y").date().isoformat()
        except ValueError as exc:
            raise ValueError("IOL_PUBLIC_DAY_INVALID") from exc
        if day in result:
            raise ValueError("IOL_PUBLIC_DUPLICATE_DAY")
        values = tuple(_decimal(item) for item in row[1:])
        result[day] = values
    return result


def public_url(symbol, market):
    if market not in {"BCBA", "NYSE", "NASDAQ", "AMEX"} or not (
        symbol and len(symbol) <= 40 and all(c.isupper() or c.isdigit() or c in "._-" for c in symbol)
    ):
        raise ValueError("IOL_PUBLIC_IDENTITY_INVALID")
    return PUBLIC_HOST + "/Titulo/DatosHistoricos?" + urlencode({"mercado": market, "simbolo": symbol})


def fetch_public_history(symbol, market):
    """Make exactly one public GET; never attach login, cookies, or account data."""
    url = public_url(symbol, market)
    request = Request(url, headers={"User-Agent": "curl/8.5.0"}, method="GET")
    with build_opener(_NoRedirect()).open(request, timeout=10) as response:
        if response.geturl() != url or response.status != 200 or not (
            response.headers.get_content_type() == "text/html"
        ):
            raise ValueError("IOL_PUBLIC_TRANSPORT_UNVERIFIED")
        body = response.read(PUBLIC_LIMIT_BYTES + 1)
    if len(body) > PUBLIC_LIMIT_BYTES:
        raise ValueError("IOL_PUBLIC_BODY_INVALID")
    return body


def _comparisons(bar, public):
    raw = (bar["open"], bar["high"], bar["low"], bar["close"], bar["volume"])
    displayed = (public[0], public[1], public[2], public[3], public[6])
    return "EXACT_DISPLAYED_OHLCV" if all(
        Decimal(str(value)) == shown for value, shown in zip(raw, displayed)
    ) else "DISPLAYED_FIELD_DIFFERENCE"


def audit(sidecar_path, *, public_get=None, now=None):
    """Return a value-free row audit; the caller may write its sanitized receipt."""
    clock = instant(now or datetime.now(timezone.utc))
    checked = verify(sidecar_path, now=clock)
    if checked["captures"] != 1 or checked["row_revisions"] != checked["retained_rows"]:
        raise ValueError("IOL_RECONCILE_CAPTURE_SCOPE_INVALID")
    path = Path(sidecar_path).absolute()
    uri = path.as_uri() + "?mode=ro&immutable=1"
    with sqlite3.connect(uri, uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        capture = db.execute("SELECT * FROM fresh_captures_rc6").fetchone()
        if capture["source"] != "IOL_CONNECTOR":
            raise ValueError("IOL_RECONCILE_SOURCE_INVALID")
        revisions = db.execute("SELECT * FROM fresh_row_revisions_rc6 ORDER BY retained_ordinal").fetchall()
        bars = json.loads(capture["retained_rows_json"])
    if len(bars) > MAX_ROWS or checked["sidecar_sha256"] != verify(path, now=clock)["sidecar_sha256"]:
        raise ValueError("IOL_RECONCILE_CAPTURE_CHANGED")
    symbol, market = capture["symbol"], capture["market"]
    url = public_url(symbol, market)
    public_rows = None
    public_status = "UNAVAILABLE"
    public_failure_reason = None
    public_observed_at = None
    public_sha = None
    try:
        body = (public_get or fetch_public_history)(symbol, market)
        observed = datetime.now(timezone.utc).isoformat()
        public_rows = parse_public_history(body, symbol)
        public_observed_at = observed
        public_sha = sha256(body).hexdigest()
        public_status = ("HTTPS_PUBLIC_TABLE_OBSERVED" if public_get is None
                         else "SUPPLIED_TABLE_OBSERVATION")
    except Exception as exc:
        # Do not print provider error text, page HTML, URLs from a redirect, or
        # transport metadata. No public assertion enters the audit on failure.
        public_failure_reason = (str(exc) if isinstance(exc, ValueError) and
                                 str(exc) in PUBLIC_CODES else
                                 "IOL_PUBLIC_SOURCE_UNAVAILABLE")
    duplicate_days = {}
    for index, bar in enumerate(bars):
        day = _day(bar, "IOL_CONNECTOR", instant(capture["first_known_at"])).isoformat()
        duplicate_days.setdefault(day, []).append(index)
    counts = {"staged_review_required": 0, "quarantined_rows": 0,
              "ohlc_range_conflict_rows": 0, "duplicate_day_rows": 0,
              "public_exact_displayed_ohlcv": 0, "public_displayed_field_difference": 0,
              "public_missing_day": 0}
    sample = []
    for index, (bar, revision) in enumerate(zip(bars, revisions)):
        day = revision["economic_day"]
        conflict = _ohlc_range_conflict(bar, "IOL_CONNECTOR")
        duplicate = len(duplicate_days[day]) > 1
        state = "QUARANTINED" if conflict or duplicate else "STAGED_REVIEW_REQUIRED"
        reason = ("OHLC_RANGE_CONFLICT" if conflict else
                  "DUPLICATE_DAY" if duplicate else "REVIEW_REQUIRED")
        counts["ohlc_range_conflict_rows"] += int(conflict)
        counts["duplicate_day_rows"] += int(duplicate)
        counts["quarantined_rows" if state == "QUARANTINED" else "staged_review_required"] += 1
        comparison = "NO_PUBLIC_OBSERVATION"
        if public_rows is not None:
            comparison = (_comparisons(bar, public_rows[day]) if day in public_rows
                          else "PUBLIC_DAY_MISSING")
            counts[{"EXACT_DISPLAYED_OHLCV": "public_exact_displayed_ohlcv",
                    "DISPLAYED_FIELD_DIFFERENCE": "public_displayed_field_difference",
                    "PUBLIC_DAY_MISSING": "public_missing_day"}[comparison]] += 1
        if state == "QUARANTINED" and len(sample) < 20:
            sample.append({"provider_row_index": revision["provider_row_index"],
                           "economic_day": day, "reason": reason,
                           "public_comparison": comparison,
                           "canonical_row_sha256": revision["canonical_row_sha256"]})
    return {"state": "EVIDENCE_ONLY_NO_GO", "source": "IOL_CONNECTOR",
            "identity": [capture[name] for name in ("symbol", "instrument_type", "market",
                                                    "currency", "settlement")],
            "identity_semantics": "REQUEST_ASSERTION_ONLY", "public_source_url": url,
            "public_source_state": public_status, "public_observed_at": public_observed_at,
            "public_failure_reason": public_failure_reason,
            "public_body_sha256": public_sha, "public_rows": len(public_rows or {}),
            "public_origin_attested": public_status == "HTTPS_PUBLIC_TABLE_OBSERVED",
            "public_source_relation": "SAME_PROVIDER_CORROBORATION_ONLY",
            "provider_original_pit_verified": False,
            "historical_currency_verified": False, "historical_settlement_verified": False,
            "adjustment_basis_verified": False, "mcp_bar_time_semantics": "ECONOMIC_TIMESTAMP_ONLY",
            "first_known_at": capture["first_known_at"],
            "first_known_at_semantics": "LOCAL_IMPORT_OBSERVATION_ONLY",
            "sidecar_sha256": checked["sidecar_sha256"],
            "retained_rows": len(bars), "counts": counts,
            "quarantine_sample": sample, "quarantine_sample_limit": 20,
            "quarantine_sample_truncated": counts["quarantined_rows"] > len(sample),
            "history_v2_writes": 0, "execution_allowed": False,
            "ready_paper_implication": "NONE"}


def write_receipt(sidecar_path, receipt_path, *, public_get=None, now=None):
    destination = preflight_destination(receipt_path, protected_paths=(sidecar_path,))
    if not destination.parent.is_dir():
        raise ValueError("IOL_RECONCILE_RECEIPT_PARENT_MISSING")
    result = audit(sidecar_path, public_get=public_get, now=now)
    content = (_body(result) + b"\n")
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        output.write(content)
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
    parser.add_argument("--receipt", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = write_receipt(args.sidecar, args.receipt)
    except (ValueError, OSError):
        parser.exit(2, "IOL_RECONCILE_FAILED\n")
    print(json.dumps({"state": result["state"], "retained_rows": result["retained_rows"],
                      "quarantined_rows": result["counts"]["quarantined_rows"],
                      "public_source_state": result["public_source_state"],
                      "history_v2_writes": 0, "execution_allowed": False}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
