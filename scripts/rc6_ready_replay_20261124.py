"""Reproduce the spot quote clock for one ART day from the active PAPER DB."""
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys
import time

RELEASE = "bd6ae43b8343f459153fd492419245c32554996a"
TREE = "5121badc37804c9e073942de378f15dc9629c62b"
SCHEMA = "rc6.paper-quote-clock-replay.v1"
DAY_START = "2026-11-24T03:00:00+00:00"
DAY_END = "2026-11-25T03:00:00+00:00"
CLOSE_CUT = "2026-11-24T22:55:27.008722+00:00"
ROW_LIMIT = 20000
SAMPLE_KEYS = {("AL30", "BONOS", "BYMA", "ARS", "A-24HS"),
               ("GGAL", "ACCIONES", "BYMA", "ARS", "A-24HS")}


def aware_timestamp(value):
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return stamp if stamp.utcoffset() is not None else None
    except (AttributeError, TypeError, ValueError):
        return None


def replay():
    from be_paper_engine import PaperStore
    from bs_instrument_contracts import FAMILIES
    from cg_paper_workspace import CONTAINER_DB, database_path, identity_from_connection

    path = database_path()
    if (path != Path(CONTAINER_DB) or not path.is_file() or
            not all(Path(str(path) + suffix).is_file() for suffix in ("-wal", "-shm"))):
        raise ValueError("PAPER_DB_BINDING_UNAVAILABLE")
    book_ttl = int(os.environ.get("PAPER_BOOK_MAX_AGE_SECONDS", "120"))
    trade_ttl = int(os.environ.get("PAPER_TRADE_MAX_AGE_SECONDS", "900"))
    if not (0 < book_ttl <= 3600 and 0 < trade_ttl <= 7200):
        raise ValueError("TTL_UNVERIFIED")
    counters = Counter()
    families = defaultdict(Counter)
    exact_keys = set()
    fresh_keys = set()
    usable_keys = set()
    samples = {}
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0.25)) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("PRAGMA trusted_schema=OFF")
        db.execute("BEGIN")
        deadline = time.monotonic() + 5
        db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        identity_from_connection(db)
        state = db.execute("SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchone()
        if state is None or tuple(state) != ("PRODUCTION_PAPER", 0) or type(state[1]) is not int:
            raise ValueError("PAPER_ZERO_UNVERIFIED")
        rows = db.execute("""
            SELECT symbol,asset_class,market,currency,settlement,observed_at,
                   book_at,trade_at,last_kind,metadata_source,opening_block_reason,
                   last,bid,ask,bid_size,ask_size,NULL AS contract_json
            FROM market_snapshots INDEXED BY idx_shadow_snapshot_observed_jd
            WHERE julianday(observed_at)>=julianday(?) AND julianday(observed_at)<julianday(?)
              AND source='PRODUCTION_PAPER'
            ORDER BY julianday(observed_at) DESC, id DESC LIMIT ?
            """, (DAY_START, DAY_END, ROW_LIMIT + 1)).fetchall()
    for row in rows[:ROW_LIMIT]:
        counters["recorded_rows"] += 1
        try:
            quote = PaperStore.quote_from_snapshot(row)
            received = aware_timestamp(quote.observed_at)
            if received is None:
                raise ValueError("RECEIPT_TIME_UNVERIFIED")
            key = (quote.symbol, quote.asset_class, quote.market, quote.currency, quote.settlement)
            if quote.asset_class not in FAMILIES or any(not part or part == "UNKNOWN" for part in key):
                counters["incomplete_local_key"] += 1
                continue
            ttl = {"max_age_seconds": book_ttl, "max_trade_age_seconds": trade_ttl}
            at_receipt = quote.time_error(quote.observed_at, require_trade=True, **ttl)
            at_close = quote.time_error(CLOSE_CUT, require_trade=True, **ttl)
            before = quote.time_error((received - timedelta(microseconds=1)).isoformat(),
                                      require_trade=True, **ttl)
            amounts = (quote.last, quote.bid, quote.ask, quote.bid_size, quote.ask_size)
            two_sided = all(value.is_finite() and value > 0 for value in amounts) and quote.bid <= quote.ask
        except (AttributeError, ArithmeticError, TypeError, ValueError):
            counters["invalid_persisted_row"] += 1
            continue
        exact_keys.add(key)
        family = families[quote.asset_class]
        family["records"] += 1
        counters["at_receive:" + (at_receipt or "FRESH_PAIR")] += 1
        counters["at_cut:" + (at_close or "FRESH_PAIR")] += 1
        counters["before_receive:" + (before or "FRESH_PAIR")] += 1
        if not at_receipt:
            fresh_keys.add(key)
            family["fresh_pairs"] += 1
            if two_sided:
                usable_keys.add(key)
                family["fresh_two_sided_pairs"] += 1
        if key in SAMPLE_KEYS and key[0] not in samples:
            book = aware_timestamp(quote.book_at)
            trade = aware_timestamp(quote.trade_at)
            samples[key[0]] = {
                "key": key, "observed_at": received.isoformat(), "book_at": book.isoformat() if book else None,
                "trade_at": trade.isoformat() if trade else None, "at_receive": at_receipt or "FRESH_PAIR",
                "at_cut": at_close or "FRESH_PAIR", "before_receive": before or "FRESH_PAIR",
                "positive_two_sided_book": two_sided,
                "direct_provider_diagnostic": quote.opening_block_reason == "PPI_DIRECT_REST_OBSERVE_ONLY",
            }
    return {
        "schema": SCHEMA, "status": "OBSERVED", "read_only": True, "real_orders_sent": 0,
        "source": "market_snapshots", "as_of": "ORIGINAL_OBSERVED_AT",
        "checked_at": datetime.now(timezone.utc).isoformat(), "comparison_cut": CLOSE_CUT,
        "art_date": "2026-11-24", "truncated": len(rows) > ROW_LIMIT,
        "ttl_seconds": {"book_and_receive": book_ttl, "trade": trade_ttl},
        "exact_local_keys": len(exact_keys), "fresh_local_keys": len(fresh_keys),
        "fresh_two_sided_local_keys": len(usable_keys),
        "counts": dict(counters), "families": families, "samples": samples,
        "authority": "CLOCK_REPLAY_ONLY", "provider_requests": 0, "order_routes": 0,
    }


def host_program(monitor_source, replay_source):
    return f"""
import json
import sys
import types

monitor = types.ModuleType("_rc6_market_watch")
monitor.__file__ = "rc6_market_watch_readonly.py"
sys.modules[monitor.__name__] = monitor
exec(compile({monitor_source!r}, monitor.__file__, "exec"), monitor.__dict__)
context = monitor._rootless_context()
current = context.current
if (current.get("source_sha"), current.get("source_tree")) != ({RELEASE!r}, {TREE!r}):
    print('{{"status":"NO_VERIFICADO","reason":"RELEASE_BINDING_CHANGED"}}')
else:
    process = context.host.docker.command_result(
        "container", "exec", "-w", "/app", context.roles["observer"],
        "python", "-B", "-c", {replay_source!r}, "replay", timeout=14)
    stable = monitor._read_private_current(
        context.host.identity.state / "current.json", context.host.identity.uid) == current
    if stable and process.returncode == 0 and len(process.stdout) < 12000:
        receipt = json.loads(process.stdout)
        receipt.update(source_sha=current["source_sha"], source_tree=current["source_tree"])
        print(json.dumps(receipt, allow_nan=False, sort_keys=True))
    else:
        print('{{"status":"NO_VERIFICADO","reason":"BOUNDED_REPLAY_UNAVAILABLE"}}')
"""


def run():
    from scripts.porota_actions_host import connection, decode_receipt, ssh_run

    current = Path(__file__)
    monitor = current.with_name("rc6_market_watch_readonly.py")
    command = host_program(monitor.read_text(encoding="utf-8"), current.read_text(encoding="utf-8"))
    try:
        with connection(os.environ) as (ssh, _, _):
            result = ssh_run(ssh, ["timeout", "32", "/usr/bin/python3", "-B", "-"],
                             data=command.encode(), timeout=42)
        if result.returncode:
            raise ValueError("HOST_READ_UNAVAILABLE")
        receipt = decode_receipt(result.stdout)
        if receipt.get("status") == "OBSERVED" and (
            receipt.get("schema") != SCHEMA or receipt.get("read_only") is not True
            or receipt.get("real_orders_sent") != 0
            or receipt.get("source_sha") != RELEASE or receipt.get("source_tree") != TREE
        ):
            raise ValueError("INVALID_REPLAY_RECEIPT")
    except (OSError, ValueError):
        receipt = {"status": "NO_VERIFICADO", "reason": "HOST_READ_UNAVAILABLE"}
    print(json.dumps(receipt, allow_nan=False, sort_keys=True))
    return 0 if receipt.get("status") == "OBSERVED" else 2


if __name__ == "__main__":
    if sys.argv[1:] == ["replay"]:
        print(json.dumps(replay(), allow_nan=False, sort_keys=True))
    else:
        raise SystemExit(run())
