"""Bounded read-only RCA counterexamples; never launch an RC6 producer.

Run with stdlib Python from the repository. The numbers labelled REPORTED are
antecedents, not measurements of this source. No quota/custody claim is made.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys
import zlib

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from rc6_shadow_runtime.projection import encoded, index_record, row_dictionary


def main():
    pre = datetime(2026, 10, 5, 13, 20, tzinfo=timezone.utc)
    start = pre + timedelta(minutes=15)
    clocks = [pre] + [start + timedelta(seconds=30*i) for i in range(1201)]
    phases = ["PREOPEN"] + ["OPEN" if t.hour < 20 else "CLOSED" for t in clocks[1:]]
    counts = {p: phases.count(p) for p in ("PREOPEN", "OPEN", "CLOSED")}
    assert counts == {"PREOPEN": 1, "OPEN": 770, "CLOSED": 431}
    limit = 512*1024**2
    reported_conditional = 499376128 + 819200
    reported_regular, reported_anchor = 442368, 1777664
    margin = limit - reported_conditional
    premium = reported_anchor - reported_regular
    erase = margin // premium + 1
    assert erase == 28 and premium*(erase-1) <= margin < premium*erase
    fixed_closed_anchors = sum(p == "CLOSED" and seq % 32 == 0
                               for seq, p in enumerate(phases, 1))
    assert fixed_closed_anchors == 13
    # This demonstrates dictionary coupling on unchanged rows, not the native
    # 6000-cohort financial workload or the reported seven-cut saving.
    reports = [{"engines": {"synthetic": {"telemetry": [{
        "diagnostic_clock": f"2026-10-05T20:01:{s}+00:00", "label": "sample"
    }]}}} for s in ("00", "30")]
    da, db = [row_dictionary(r) for r in reports]
    assert da != db and zlib.adler32(da) != zlib.adler32(db)
    a, b = [], []
    originals = [{"identity": [f"SYNTHETIC{i}", "AUDIT", "M", "ARS", "CI"],
                  "state": "WARM", "value": "unchanged"} for i in range(8)]
    for i, row in enumerate(originals):
        pa = bytes(index_record("planner", i, row, False, dictionary=da)[-1])
        pb = bytes(index_record("planner", i, row, False, dictionary=db)[-1])
        assert zlib.decompressobj(zdict=da).decompress(pa).decode() == encoded(row)
        assert zlib.decompressobj(zdict=db).decompress(pb).decode() == encoded(row)
        assert pa != pb and pa[2:6] != pb[2:6]
        a.append(pa); b.append(pb)
    result = {
        "scope": "READ_ONLY_SYNTHETIC_COUNTEREXAMPLES_AND_ARITHMETIC",
        "qualification_claimed": False, "financial_workload_executed": False,
        "source_sha": "ecad18b6010e3b7e874a00edc72644b3dcc78790",
        "python": sys.version.split()[0],
        "schedule": {"counts": counts, "cuts": len(clocks),
                     "first": pre.isoformat(), "last": clocks[-1].isoformat(),
                     "fixed_closed_anchors": fixed_closed_anchors,
                     "fixed_anchors_are_upper_bound": False},
        "retention": {"first_possible_time_expiry": (pre+timedelta(hours=10)).isoformat(),
                      "historical_last_cut": "2026-10-05T22:18:30+00:00",
                      "credit_for_unproved_dependency_or_pack_GC": 0},
        "capacity_arithmetic": {"limit_bytes": limit, "mean_bytes_per_cut_before_controls": limit/1202,
                                "REPORTED_conditional_total_bytes": reported_conditional,
                                "REPORTED_conditional_headroom_bytes": margin,
                                "REPORTED_anchor_premium_bytes": premium,
                                "additional_anchors_erasing_REPORTED_headroom": erase,
                                "all_closed_as_REPORTED_anchors_counterexample_bytes": 431*reported_anchor,
                                "counterexample_is_prediction": False},
        "synthetic_dictionary_churn": {"rows": 8, "unchanged_decoded_rows": 8,
                                       "changed_encoded_rows": sum(x != y for x, y in zip(a,b)),
                                       "dictionary_sha256": [hashlib.sha256(d).hexdigest() for d in (da,db)],
                                       "blob_size_pairs": [[len(x),len(y)] for x,y in zip(a,b)],
                                       "bytes_after_dictionary_header_equal": sum(x[6:] == y[6:] for x,y in zip(a,b))},
        "assertion_groups_passed": 6, "real_orders_sent": 0,
    }
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
