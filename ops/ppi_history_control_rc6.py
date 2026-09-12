#!/usr/bin/env python3
"""Read-only control/status probes for the durable RC6 PPI history runner."""
from __future__ import annotations

import os
import sqlite3
import sys

import bf_production_paper_observer as observer
from cv_history_store_adapter_hf6 import default_history_store

RUN_ID = "PPI-HIST-20260912-001"
PIDFILE = "/tmp/porota-ppi-fullfamily-history.pid"
EXPECTED = 1960


def norm(v):
    return str(v or "").strip().upper()


def pick(cols, *names):
    for name in names:
        if name in cols:
            return name
    return None


def universe(store):
    with store.connect() as c:
        cols = [r[1] for r in c.execute("PRAGMA table_info(ppi_argentina_api_shadow)")]
        sym = pick(cols, "ticker", "symbol")
        fam = pick(cols, "instrument_type", "family", "type")
        mkt = pick(cols, "market")
        stl = pick(cols, "settlement")
        cur = pick(cols, "currency", "moneda")
        if not all((sym, fam, mkt, stl)):
            raise RuntimeError("SHADOW_IDENTITY_COLUMNS_MISSING")
        selected = [sym, fam, mkt, stl] + ([cur] if cur else [])
        q = (
            "SELECT " + ",".join('"' + x + '"' for x in selected)
            + " FROM ppi_argentina_api_shadow WHERE market IN ('BYMA','ROFEX','A3','OTC') "
              "AND instrument_type NOT IN ('ACCIONES-USA','FCI-EXTERIOR')"
        )
        rows = c.execute(q).fetchall()
    keys = []
    currencies = {}
    for row in rows:
        key = (norm(row[0]), norm(row[1]), norm(row[2]), norm(row[3]))
        keys.append(key)
        currencies.setdefault(key, set()).add(norm(row[4]) if cur else "")
    collisions = sum(1 for values in currencies.values() if len(values) > 1)
    return len(rows), len(set(keys)), collisions


def safety(store):
    with store.connect() as c:
        row = c.execute("SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchone()
    if not row:
        raise RuntimeError("OBSERVER_STATE_MISSING")
    value = f"{row[0]}|{int(row[1] or 0)}"
    if row[0] != "PRODUCTION_PAPER" or int(row[1] or 0) != 0:
        raise RuntimeError("SAFETY_INVARIANT_BROKEN:" + value)
    return value


def history_integrity(hstore):
    with hstore.connect() as c:
        qc = c.execute("PRAGMA quick_check").fetchone()[0]
        rows = int(c.execute("SELECT count(*) FROM history_canonical_v2").fetchone()[0] or 0)
        dup = int(c.execute("""SELECT count(*) FROM (
          SELECT symbol,instrument_type,market,settlement,date,count(*) n
          FROM history_canonical_v2
          GROUP BY symbol,instrument_type,market,settlement,date HAVING n>1)""").fetchone()[0] or 0)
    return str(qc), dup, rows


def pid_alive():
    try:
        with open(PIDFILE, encoding="utf-8") as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)
        return pid
    except Exception:
        return 0


def preflight():
    store = observer.runtime_store()
    hstore = default_history_store()
    with store.connect() as c:
        oqc = c.execute("PRAGMA quick_check").fetchone()[0]
    rows, keys, collisions = universe(store)
    safe = safety(store)
    hqc, dup, hrows = history_integrity(hstore)
    print(f"OBSERVER_QUICK_CHECK={oqc}")
    print(f"SAFETY={safe}")
    print(f"SHADOW_ROWS={rows}")
    print(f"SHADOW_KEYS={keys}")
    print(f"CURRENCY_COLLISIONS={collisions}")
    print(f"HISTORY_QUICK_CHECK={hqc}")
    print(f"CANONICAL_DUPLICATES={dup}")
    print(f"CANONICAL_ROWS={hrows}")
    assert oqc == "ok"
    assert rows == EXPECTED and keys == EXPECTED and collisions == 0
    assert hqc == "ok" and dup == 0
    print("PREFLIGHT=PASS")


def status():
    store = observer.runtime_store()
    pid = pid_alive()
    values = {
        "STATUS": "NONE", "BATCH": 0, "HEARTBEAT": "", "ROWS": 0,
        "DONE": 0, "PENDING": 0, "ERRORS": 0, "SAFETY": safety(store),
        "DETAIL": "runtime_row_missing", "PID": pid,
    }
    try:
        with store.connect() as c:
            exists = c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ppi_history_ingest_runtime'").fetchone()
            row = c.execute("""SELECT status,last_committed_batch,heartbeat,rows_canonical,
              done_identities,pending_identities,error_identities,safety,detail
              FROM ppi_history_ingest_runtime WHERE run_id=?""", (RUN_ID,)).fetchone() if exists else None
        if row:
            values.update({
                "STATUS": str(row[0]), "BATCH": int(row[1] or 0), "HEARTBEAT": str(row[2] or ""),
                "ROWS": int(row[3] or 0), "DONE": int(row[4] or 0), "PENDING": int(row[5] or 0),
                "ERRORS": int(row[6] or 0), "SAFETY": str(row[7] or ""), "DETAIL": str(row[8] or ""),
            })
    except sqlite3.Error as exc:
        values["DETAIL"] = "status_sqlite_error:" + type(exc).__name__
    for key in ("STATUS","BATCH","HEARTBEAT","ROWS","DONE","PENDING","ERRORS","SAFETY","PID","DETAIL"):
        print(f"{key}={values[key]}")


def integrity():
    store = observer.runtime_store()
    hstore = default_history_store()
    safe = safety(store)
    rows, keys, collisions = universe(store)
    hqc, dup, hrows = history_integrity(hstore)
    print(f"SAFETY={safe}")
    print(f"SHADOW_ROWS={rows} SHADOW_KEYS={keys} CURRENCY_COLLISIONS={collisions}")
    print(f"HISTORY_QUICK_CHECK={hqc} CANONICAL_DUPLICATES={dup} CANONICAL_ROWS={hrows}")
    print(f"PID={pid_alive()}")
    assert rows == EXPECTED and keys == EXPECTED and collisions == 0
    assert hqc == "ok" and dup == 0
    print("INTEGRITY=PASS")


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else "status"
    if action == "preflight":
        preflight()
    elif action == "status":
        status()
    elif action == "integrity":
        integrity()
    else:
        raise SystemExit("usage: ppi_history_control_rc6.py [preflight|status|integrity]")


if __name__ == "__main__":
    main()
