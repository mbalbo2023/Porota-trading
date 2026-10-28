#!/usr/bin/env python3
"""Apply the proven RC6 mutable-minute/session policy to cf_intraday_scalping.py.

Deterministic source transformation used by CI before the source change is
activated. It does not access DB/network/orders and is idempotent.
"""
from __future__ import annotations
from pathlib import Path
import sys

IMPORT_ANCHOR = "from co_market_sessions_hf6 import byma_paper_spot_open\n"
IMPORT_LINE = "from fg_intraday_contract_policy_rc6 import classify_revision, previous_for_session\n"
START = "def persist_payload(store, record, points, *, received_at):\n"
END = "\n\ndef evaluate_candidate(store, record, *, at):\n"

NEW_FUNCTION = '''def persist_payload(store, record, points, *, received_at):
    identity = _identity(record)
    # Contract state is trading-session scoped. A rejection from a prior local
    # trading day must never poison the next session.
    previous = previous_for_session(_state(store, identity), received_at=received_at)
    stable = changed = inserted = refreshed = 0
    down_steps = sum(1 for left, right in zip(points, points[1:]) if right[2] < left[2])
    with store.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        for event_at, price, volume in points:
            existing = connection.execute("""SELECT price,volume FROM ppi_intraday_points
              WHERE symbol=? AND asset_class=? AND market=? AND currency=? AND settlement=? AND event_at=?""",
              (*identity, event_at)).fetchone()
            values = (format(price, "f"), format(volume, "f"))
            if existing:
                decision = classify_revision(
                    event_at=event_at, received_at=received_at,
                    old_price=existing[0], old_volume=existing[1],
                    new_price=values[0], new_volume=values[1])
                if decision["action"] == "SAME":
                    if decision["age_seconds"] > 120:
                        stable += 1
                    connection.execute("""UPDATE ppi_intraday_points SET last_verified_at=?
                      WHERE symbol=? AND asset_class=? AND market=? AND currency=? AND settlement=? AND event_at=?""",
                      (received_at, *identity, event_at))
                elif decision["action"] == "REFRESH_MUTABLE":
                    # PPI may finalize the still-forming recent minute. Persist
                    # the newest baseline now so it is not falsely rejected once
                    # the point ages beyond the mutable window.
                    connection.execute("""UPDATE ppi_intraday_points
                      SET price=?,volume=?,last_verified_at=?
                      WHERE symbol=? AND asset_class=? AND market=? AND currency=? AND settlement=? AND event_at=?""",
                      (*values, received_at, *identity, event_at))
                    refreshed += 1
                else:
                    # A genuine revision to a closed minute remains fail-closed.
                    changed += 1
                continue
            connection.execute("""INSERT INTO ppi_intraday_points VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
              (*identity, event_at, *values, received_at, received_at, "PPI_MARKETDATA_INTRADAY"))
            inserted += 1
        observations = (previous or {}).get("observations", 0) + 1
        prior_stable = (previous or {}).get("stable_overlap", 0)
        prior_changed = (previous or {}).get("changed_closed_points", 0)
        confirmed_now = (observations >= 2 and stable >= 5 and inserted >= 1
                         and down_steps >= 1 and changed == 0 and prior_changed == 0)
        state = ("CONFIRMED_INTERVAL_VOLUME" if confirmed_now or (
                    (previous or {}).get("state") == "CONFIRMED_INTERVAL_VOLUME"
                    and changed == 0 and prior_changed == 0)
                 else "REJECTED_MUTABLE_CLOSED_POINTS" if changed or prior_changed
                 else "PENDING_LIVE_CONFIRMATION")
        last_source = points[-1][0] if points else None
        detail = (f"observaciones={observations}; solapamiento_estable={stable}; "
                  f"cerrados_modificados={changed}; nuevos={inserted}; "
                  f"mutables_refrescados={refreshed}; descensos_volumen={down_steps}")
        connection.execute("""INSERT OR REPLACE INTO ppi_intraday_contract_state
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          (*identity, state, observations, max(prior_stable, stable), prior_changed + changed,
           inserted, last_source, received_at, detail))
    return {"state": state, "inserted": inserted, "stable": stable,
            "changed": changed, "refreshed": refreshed, "down_steps": down_steps}
'''


def apply(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    if "mutables_refrescados={refreshed}" in text and IMPORT_LINE.strip() in text:
        return False
    if IMPORT_LINE.strip() not in text:
        if IMPORT_ANCHOR not in text:
            raise SystemExit("IMPORT_ANCHOR_NOT_FOUND")
        text = text.replace(IMPORT_ANCHOR, IMPORT_ANCHOR + IMPORT_LINE, 1)
    a = text.find(START)
    b = text.find(END, a)
    if a < 0 or b < 0:
        raise SystemExit("PERSIST_PAYLOAD_BOUNDARY_NOT_FOUND")
    text = text[:a] + NEW_FUNCTION + text[b:]
    path.write_text(text, encoding="utf-8")
    return True


if __name__ == "__main__":
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "cf_intraday_scalping.py")
    changed = apply(target)
    print("SCALPING_SOURCE_PATCH=" + ("APPLIED" if changed else "ALREADY_APPLIED"))
