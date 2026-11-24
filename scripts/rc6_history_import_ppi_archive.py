"""Recover daily PPI observations from the content-addressed historical archive."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import closing
from datetime import date, datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3

import cp_history_ingest_policy_hf6 as policy
import cu_history_store_v2_hf6 as history
from cv_history_store_adapter_hf6 import HistoricalStore
import fb_raw_evidence_exact_v1 as exact
from rc6_multisource_discovery import canonical_family, canonical_settlement
from scripts.rc6_history_import_source_reported import _capture_sha, _source_store

MAX_BODY_BYTES = 2 * 1024 * 1024
MAX_MANIFEST_ROWS = 1_000_000
MAX_BARS_PER_CAPTURE = 4096
VALID_FAMILIES = policy.OPERATIONAL_HISTORY_FAMILIES | policy.LEGACY_HISTORY_FAMILIES


def _manifest(path):
    if path.resolve(strict=True) != path or not path.is_file():
        raise ValueError("HISTORY_ARCHIVE_MANIFEST_PATH_UNVERIFIED")
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    return db


def _series(record, wrapper):
    symbol = wrapper["symbol"]
    provider_family = wrapper.get("instrument_type") or wrapper["asset_class"]
    term = wrapper["settlement"]
    if not all(isinstance(value, str) and history.PPI_REPORTED_SELECTOR.fullmatch(value)
               for value in (symbol, provider_family, term)):
        raise ValueError("HISTORY_ARCHIVE_SELECTOR_INVALID")
    if (wrapper.get("instrument_type") and wrapper.get("asset_class") and
            wrapper["instrument_type"] != wrapper["asset_class"]):
        raise ValueError("HISTORY_ARCHIVE_SELECTOR_CONFLICT")
    settlement = canonical_settlement(term)
    family = canonical_family(provider_family)
    if family not in VALID_FAMILIES or settlement not in history.PPI_REPORTED_SETTLEMENTS:
        raise ValueError("HISTORY_ARCHIVE_SELECTOR_UNSUPPORTED")
    start = date.fromisoformat(wrapper["date_from"])
    end = date.fromisoformat(wrapper["date_to"])
    observed = history.instant(record["recorded_at"])
    if (not 0 <= (end - start).days <= 366 or end > observed.astimezone(history.ART).date()
            or observed > datetime.now(timezone.utc)):
        raise ValueError("HISTORY_ARCHIVE_WINDOW_INVALID")
    key = json.loads(record["row_key"])
    if not isinstance(key, list):
        raise ValueError("HISTORY_ARCHIVE_KEY_INVALID")
    old = [symbol, provider_family, term, record["recorded_at"]]
    market = wrapper.get("market")
    currency = wrapper.get("currency")
    full = [[symbol, provider_family, market, currency, term],
            start.isoformat(), end.isoformat(), record["recorded_at"]]
    repair = [symbol, provider_family, market, term, currency, "RAW",
              "RAW_NO_ADJUSTMENT", record["recorded_at"]]
    if key not in (old, full, repair):
        raise ValueError("HISTORY_ARCHIVE_KEY_CONFLICT")
    return (symbol, family, settlement, provider_family), start, end, observed


def _archive_index(db):
    series = defaultdict(dict)
    rejected = Counter()
    rows = db.execute("""SELECT row_key,body_hash,inner_sha256,recorded_at,
        CASE WHEN length(CAST(wrapper_json AS BLOB)) <= 65536 THEN wrapper_json END wrapper_json
        FROM exact_attempts_v1 WHERE origin='PPI_HISTORY' LIMIT ?""", (MAX_MANIFEST_ROWS + 1,))
    for count, record in enumerate(rows, 1):
        if count > MAX_MANIFEST_ROWS:
            raise ValueError("HISTORY_ARCHIVE_MANIFEST_BUDGET")
        try:
            wrapper = json.loads(record["wrapper_json"])
            identity, start, end, observed = _series(record, wrapper)
            selector = (wrapper.get("instrument_type") or wrapper["asset_class"], wrapper["settlement"])
            attempt = (observed, record["row_key"], record["body_hash"])
            # The window is part of validation. A repeated payload acquired
            # with different bounds may establish additional eligible dates.
            payload_key = (record["inner_sha256"], start, end, selector)
            existing = series[identity].get(payload_key)
            if existing is None or attempt < existing:
                series[identity][payload_key] = attempt
        except (AttributeError, KeyError, TypeError, ValueError):
            rejected["HISTORY_ARCHIVE_MANIFEST_ENTRY_INVALID"] += 1
    return series, rejected


def _body(db, attempt, environ):
    row = db.execute("""SELECT wrapper_json,inner_sha256,body_bytes,body_sha256
        FROM exact_attempts_v1 WHERE origin='PPI_HISTORY' AND row_key=? AND body_hash=?""", attempt[1:]).fetchone()
    if row is None or not 0 < row["body_bytes"] <= MAX_BODY_BYTES:
        raise ValueError("HISTORY_ARCHIVE_BODY_INVALID")
    encoded = exact.reconstruct(row["wrapper_json"], row["inner_sha256"], environ,
                                max_body_bytes=row["body_bytes"]).encode("utf-8", "surrogatepass")
    if len(encoded) != row["body_bytes"] or sha256(encoded).hexdigest() != row["body_sha256"] or row["body_sha256"] != attempt[2]:
        raise ValueError("HISTORY_ARCHIVE_BODY_HASH_MISMATCH")
    wrapper = json.loads(encoded)
    payload = json.loads(wrapper["payload_json"])
    if not isinstance(payload, list) or len(payload) > MAX_BARS_PER_CAPTURE:
        raise ValueError("HISTORY_ARCHIVE_PAYLOAD_INVALID")
    return payload, wrapper, row["inner_sha256"]


def _candles(payload, identity, bounds, attempt, wrapper, inner):
    symbol, family, settlement, provider_family = identity
    start, end = bounds
    observed, _, body_hash = attempt
    checked = policy.validate_provider_history(payload, as_of=observed, date_from=start, date_to=end)
    rejected = Counter(row.reason for row in checked.rejected_rows)
    duplicate_days = {policy._dt(payload[row.index]["date"]).astimezone(history.ART).date().isoformat()
                      for row in checked.rejected_rows if row.reason == "DUPLICATE_DATE"}
    candidates, conflicting = {}, set()
    for raw in checked.valid_rows:
        provider_at = policy._dt(raw["date"])
        stamp = provider_at.astimezone(history.ART).date().isoformat()
        if stamp in duplicate_days:
            continue
        # A response may declare a different ticker. The request alone cannot
        # override contradictory provider identity.
        if any(field in raw and str(raw[field]).strip().upper() != symbol for field in ("ticker", "symbol")):
            rejected["HISTORY_ARCHIVE_TICKER_CONFLICT"] += 1
            continue
        try:
            values = history.validate_daily_values(
                date=stamp, open=raw["openingPrice"], high=raw["max"], low=raw["min"],
                close=raw["price"], volume=raw["volume"], known_at=observed,
                market=wrapper.get("market"), family=family, settlement=settlement,
            )
        except ValueError as error:
            rejected[str(error)] += 1
            continue
        explicit = {name: raw[name] for name in ("currency", "quoteCurrency", "moneda",
            "priceBasis", "adjustmentBasis", "adjusted") if name in raw}
        signature = (*values.values(), history.utc(provider_at), _capture_sha(explicit))
        candle = history.Candle(
            symbol=symbol, instrument_type=family, market="PPI", currency="UNKNOWN",
            settlement=settlement, date=stamp, source=history.PPI_REPORTED,
            price_basis=history.SOURCE_REPORTED,
            adjustment_basis=history.source_reported_adjustment(history.PPI_REPORTED, provider_family),
            observed_at=observed, provider_at=provider_at, **values,
            metadata={"archive_sha256": body_hash, "provider_response_sha256": inner,
                      "request_family": provider_family,
                      "request_settlement": wrapper["settlement"], "reported_attributes": explicit,
                      "readiness_implication": "NONE"},
        )
        previous = candidates.get(stamp)
        if previous is not None and signature != previous[0]:
            conflicting.add(stamp)
        candidates[stamp] = signature, candle
    for stamp in conflicting:
        del candidates[stamp]
        rejected["HISTORY_DUPLICATE_DAY_CONFLICT"] += 1
    return candidates, rejected


def import_ppi_archive(store, *, environ=None):
    """Replay verified rows in acquisition order and emit coverage per selector."""
    if getattr(store, "source_reported_only", False) is not True:
        raise ValueError("HISTORY_STORE_SERIES_SCOPE_MISMATCH")
    if getattr(store, "existing_history_identity", None) is None:
        history.init_schema(store)
    report = []
    totals = Counter()
    with closing(_manifest(exact.manifest_path(environ))) as db:
        index, invalid = _archive_index(db)
        for identity, captures in sorted(index.items()):
            seen_days = {}
            observed_dates = set()
            counts, rejected = Counter(), Counter()
            for (_, start, end, _), attempt in sorted(captures.items(), key=lambda item: item[1]):
                try:
                    rows, wrapper, inner = _body(db, attempt, environ)
                    counts.update(objects_verified=1, received=len(rows))
                    candidates, reasons = _candles(rows, identity, (start, end), attempt, wrapper, inner)
                except (EOFError, OSError, TypeError, ValueError):
                    rejected["HISTORY_ARCHIVE_OBJECT_UNVERIFIED"] += 1
                    continue
                rejected.update(reasons)
                observed_dates.update(candidates)
                day_changes = {day: (signature, candle) for day, (signature, candle) in candidates.items()
                               if day not in seen_days or seen_days[day] != signature}
                if not day_changes:
                    continue
                ordered = [day_changes[day][1] for day in sorted(day_changes)]
                attempt_key = _capture_sha(["ppi-exact-reference-v1", *identity, *attempt[1:]])
                receipt = history.append_many(store, ordered, attempt_key=attempt_key)
                seen_days.update({day: item[0] for day, item in day_changes.items()})
                counts.update({key: receipt[key] for key in ("committed_rows", "versions_appended", "canonical_updates")})
            totals.update(counts)
            report.append({"series": identity, **counts, "days": len(observed_dates),
                           "first_date": min(observed_dates, default=None),
                           "last_date": max(observed_dates, default=None), "rejected": dict(rejected)})
    with store.connect() as destination:
        if destination.execute("PRAGMA quick_check(1)").fetchone()[0] != "ok":
            raise ValueError("HISTORY_REFERENCE_SQLITE_CHECK_FAILED")
        committed = destination.execute("""SELECT count(*) FROM history_canonical_v2
            WHERE price_basis=? AND source=?""", (history.SOURCE_REPORTED, history.PPI_REPORTED)).fetchone()[0]
    return {"schema": "rc6.history.ppi-reference-import.v1", **totals, "stored_rows": committed,
            "manifests_rejected": dict(invalid), "series": report, "quick_check": "ok",
            "provider_requests": 0, "readiness_implication": "NONE"}


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--destination", type=Path, required=True)
    cli.add_argument("--existing-history", action="store_true")
    args = cli.parse_args()
    store = (HistoricalStore(str(args.destination)).for_source_reported_history() if args.existing_history
             else _source_store(args.destination))
    print(json.dumps(import_ppi_archive(store), ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
