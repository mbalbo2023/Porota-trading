# Appended to pilot.py so the existing read-only path and imports are reused.
from collections import defaultdict


def safe(value):
    if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9 ._/+()-]{1,80}", value):
        return value
    return "NO_VERIFICADO"


def open_census(path):
    db = readonly(path)
    deadline = time.monotonic() + 24
    db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
    db.execute("BEGIN")
    return db


def catalog():
    with closing(open_census(RELEASE_DATA / "paper_v17/observer_v17.db")) as db:
        records = db.execute("""SELECT ticker,instrument_type,market,currency,settlement,status,
            coalesce(CASE WHEN json_valid(metadata_json)
                 THEN coalesce(json_extract(metadata_json,'$._discovery_source'),
                          json_extract(metadata_json,'$.raw._discovery_source')) END,'PPI_PRIMARY'),
            CASE WHEN json_valid(metadata_json)
                 THEN json_extract(metadata_json,'$.raw._provider_instrument_type') END
            FROM financial_instrument_catalog
            ORDER BY ticker,instrument_type,market,currency,settlement LIMIT 50001""").fetchall()
        if len(records) > 50000:
            raise ValueError("CATALOG_BUDGET")
        result = [list(map(safe, record)) for record in records]
        archive_counts = dict(db.execute("SELECT origin,count(*) FROM historical_raw_archive GROUP BY 1"))
    return result, {safe(key): value for key, value in archive_counts.items()}


def archive():
    grouped = {}
    combinations = Counter()
    versions = defaultdict(set)
    reasons = Counter()
    with closing(open_census(exact.manifest_path())) as db:
        rows = db.execute("""SELECT row_key,recorded_at,quality,inner_sha256,
            CASE WHEN length(CAST(wrapper_json AS BLOB)) BETWEEN 2 AND 16384
                 THEN wrapper_json END AS wrapper_json
            FROM exact_attempts_v1 WHERE origin='PPI_HISTORY' LIMIT 650001""")
        for index, row in enumerate(rows):
            if index == 650000:
                raise ValueError("MANIFEST_BUDGET")
            try:
                wrapper = json.loads(row["wrapper_json"])
                identity = tuple(map(safe, (wrapper["symbol"],
                    wrapper.get("instrument_type") or wrapper["asset_class"],
                    wrapper.get("market"), wrapper.get("currency"), wrapper["settlement"])))
                if identity[0] == "NO_VERIFICADO" or identity[1] == "NO_VERIFICADO":
                    raise ValueError("IDENTITY_UNVERIFIED")
                start = date.fromisoformat(wrapper["date_from"]).isoformat()
                end = date.fromisoformat(wrapper["date_to"]).isoformat()
                if end < start:
                    raise ValueError("DATE_WINDOW_UNVERIFIED")
                observed_at = history.instant(row["recorded_at"]).isoformat()
                key = (*identity, safe(row["quality"]))
                record = grouped.setdefault(key, [0, start, end, observed_at, 0, 0, 0, 0])
                record[0] += 1
                record[1] = min(record[1], start)
                record[2] = max(record[2], end)
                record[3] = max(record[3], observed_at)
                record[4] += start <= repair.CUTOFF.isoformat() and end >= repair.BOOTSTRAP_START.isoformat()
                record[5] += end > repair.CUTOFF.isoformat()
                record[6] += end >= "2026-10-01"
                record[7] += end >= "2026-11-01"
                combinations[(key[1], key[5])] += 1
                versions[key].add(row["inner_sha256"])
            except (KeyError, TypeError, ValueError):
                reasons["ARCHIVE_METADATA_UNVERIFIED"] += 1
    for key, items in versions.items():
        grouped[key].append(len(items))
    return [list(key) + values for key, values in sorted(grouped.items())], [
        [*key, value] for key, value in sorted(combinations.items())], dict(reasons)


def coverage():
    with closing(open_census(RELEASE_DATA / "market_history.db")) as db:
        count = db.execute("""SELECT count(*) FROM history_canonical_v2""").fetchone()[0]
        groups = [[*map(safe, row[:7]), *row[7:]] for row in db.execute("""SELECT
            symbol,instrument_type,market,currency,settlement,source,price_basis,
            count(*),min(date),max(date) FROM history_canonical_v2 GROUP BY 1,2,3,4,5,6,7
            LIMIT 50001""")]
        if len(groups) > 50000:
            raise ValueError("HISTORY_BUDGET")
        tables = [safe(r[0]) for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    return count, groups, tables


try:
    catalog_rows, legacy = catalog()
    archive_rows, by_family_and_quality, rejected = archive()
    canonical_count, history_rows, history_tables = coverage()
    print(json.dumps({
        "schema": "rc6.history.archive-census.v1",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "provider_requests": 0, "production_sql_writes": 0,
        "catalog": catalog_rows, "legacy_archive_origins": legacy, "archive": archive_rows,
        "archive_by_family_and_quality": by_family_and_quality, "archive_rejected": rejected,
        "canonical_count": canonical_count, "canonical": history_rows, "history_tables": history_tables,
    }, separators=(",", ":")))
except (OSError, ValueError, sqlite3.Error) as exc:
    print("PILOT_ERROR:" + type(exc).__name__)
    raise SystemExit(2)
