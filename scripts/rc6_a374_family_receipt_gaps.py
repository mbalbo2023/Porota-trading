"""A374: fail-closed, OFFHOST gap matrix for two genuine RC6 Market Watch ZIPs.

This is deliberately NOT an A320 positive signed PIT/decision verifier. A
Market Watch digest/snapshot proves a bounded observation, not market source
independence, first-known economics, PAPER fills, OOS edge or entry authority.
No network, broker, SQLite, provider, droplet or trading database access.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import stat
import sys
import zipfile

SCHEMA = "rc6.a374.family-receipt-gaps.v1"
FAMILIES = ("ACCIONES", "CEDEARS", "ETFS", "BONOS", "LETRAS",
            "OBLIGACIONES", "OPCIONES", "FUTUROS", "CAUCIONES", "FCI")
KEYS = ("symbol", "family", "market", "currency", "settlement")
COUNTERS = ("market_snapshots", "paper_decisions", "paper_events",
            "scalping_candidates", "paper_fills", "trade_gate_evaluations")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
SHA40 = re.compile(r"[0-9a-f]{40}\Z")
MAX_ARCHIVE_BYTES = 192 * 1024
MAX_TOTAL_DECOMPRESSED = 512 * 1024
MAX_MEMBERS = 16
MAX_MEMBER_BYTES = 128 * 1024
MAX_ROWS = 10
EXPECTED_SCOPE = "MAX_10_IDENTITIES_LAST_500_RECORDS_OPEN_PRIORITY_NOT_GLOBAL"


def _clock(raw):
    if not isinstance(raw, str):
        raise ValueError("A374_UNVERIFIED_TIMESTAMP")
    try:
        result = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("A374_UNVERIFIED_TIMESTAMP") from None
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("A374_UNVERIFIED_TIMESTAMP")
    return result.astimezone(timezone.utc)


def _artifact(path, expected_digest):
    if not isinstance(expected_digest, str) or not SHA256.fullmatch(expected_digest):
        raise ValueError("A374_DIGEST_INVALID")
    target = Path(path)
    if not target.is_file() or target.is_symlink():
        raise ValueError("A374_UNSAFE_ARTIFACT_PATH")
    st = target.stat()
    if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_ARCHIVE_BYTES:
        raise ValueError("A374_UNSAFE_ARTIFACT_PATH")
    raw = target.read_bytes()
    if sha256(raw).hexdigest() != expected_digest:
        raise ValueError("A374_ARTIFACT_SHA_MISMATCH")
    try:
        with zipfile.ZipFile(target) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_MEMBERS or len({i.filename for i in infos}) != len(infos):
                raise ValueError("A374_ARTIFACT_MEMBERS_INVALID")
            if any(i.filename.startswith("/") or ".." in i.filename.split("/")
                   or "\\" in i.filename or i.is_dir() or i.file_size > MAX_MEMBER_BYTES
                   or (i.external_attr >> 16) & 0o170000 == 0o120000
                   for i in infos):
                raise ValueError("A374_ARTIFACT_MEMBERS_INVALID")
            if sum(i.file_size for i in infos) > MAX_TOTAL_DECOMPRESSED:
                raise ValueError("A374_ARTIFACT_BUDGET_EXCEEDED")
            names = {i.filename for i in infos}
            if not {"snapshot.json", "dashboard_audit.json"}.issubset(names):
                raise ValueError("A374_REQUIRED_RECEIPT_ABSENT")
            snapshot = json.loads(archive.read("snapshot.json"))
            dashboard = json.loads(archive.read("dashboard_audit.json"))
    except (OSError, zipfile.BadZipFile, UnicodeDecodeError, json.JSONDecodeError,
            RuntimeError, KeyError):
        raise ValueError("A374_ARTIFACT_UNREADABLE") from None
    if not isinstance(snapshot, dict) or not isinstance(dashboard, dict):
        raise ValueError("A374_RECEIPT_SCHEMA_INVALID")
    return snapshot, dashboard


def _verify_receipt(snapshot, dashboard, product_sha):
    if snapshot.get("schema") != "rc6.market-watch-readonly.v1" or dashboard.get(
            "schema") != "rc6.operator-dashboard-readonly.v1":
        raise ValueError("A374_RECEIPT_SCHEMA_INVALID")
    prov = snapshot.get("provenance")
    if not isinstance(prov, dict) or prov.get("deploy_sha") != product_sha:
        raise ValueError("A374_PRODUCT_PROVENANCE_MISMATCH")
    if (prov.get("mode") != "PRODUCTION_PAPER" or type(prov.get("real_orders_sent")) is not int
            or prov["real_orders_sent"] != 0 or prov.get("real_order_routes") != "NOT_CALLED"):
        raise ValueError("A374_PAPER_SAFETY_UNVERIFIED")
    obs = snapshot.get("observer")
    if (not isinstance(obs, dict) or obs.get("mode") != "PRODUCTION_PAPER"
            or type(obs.get("real_orders_sent")) is not int or obs["real_orders_sent"] != 0):
        raise ValueError("A374_PAPER_SAFETY_UNVERIFIED")
    if type(snapshot.get("real_order_routes_called")) is not int or snapshot[
            "real_order_routes_called"] != 0:
        raise ValueError("A374_PAPER_SAFETY_UNVERIFIED")
    clock = _clock(snapshot.get("generated_at"))
    sample = dashboard.get("identity_clocks")
    if (not isinstance(sample, dict) or sample.get("scope") != EXPECTED_SCOPE
            or sample.get("status") != "BOUNDED" or not isinstance(sample.get("rows"), list)
            or len(sample["rows"]) > MAX_ROWS):
        raise ValueError("A374_SAMPLE_SCOPE_UNVERIFIED")
    seen = set()
    counts = {fam: {"sampled": 0, "book_fresh": 0, "trade_fresh": 0,
                    "causal_receipt_authority": "NO_VERIFICADO",
                    "paper_e2e_authority": "NO_VERIFICADO"} for fam in FAMILIES}
    missing = Counter()
    for row in sample["rows"]:
        if (not isinstance(row, dict) or any(not isinstance(row.get(k), str)
                or not row[k].strip() for k in KEYS) or row["family"] not in FAMILIES):
            raise ValueError("A374_FULL_IDENTITY_INVALID")
        key = tuple(row[k] for k in KEYS)
        if key in seen:
            raise ValueError("A374_DUPLICATE_IDENTITY_IN_SAMPLE")
        seen.add(key)
        counts[row["family"]]["sampled"] += 1
        for k, counter in (("book_freshness", "book_fresh"),
                           ("trade_freshness", "trade_fresh")):
            if row.get(k) not in {"FRESH", "STALE", "NO_VERIFICADO"}:
                raise ValueError("A374_SOURCE_FRESHNESS_INVALID")
            if row[k] == "FRESH":
                counts[row["family"]][counter] += 1
        for clock_field in ("received_at", "first_known_at"):
            value = row.get(clock_field)
            if not value:
                missing[clock_field] += 1
            elif _clock(value) > clock:
                raise ValueError("A374_CAUSAL_SAMPLE_CLOCK_IN_FUTURE")
        # A monitor freshness claim is never a cryptographic source proof.
        # Even a forged VERIFIED_PIT label cannot reduce the missing-proof count.
        if row.get("receipt_freshness") not in {"NO_VERIFICADO", "STALE", "FRESH", "VERIFIED_PIT"}:
            raise ValueError("A374_RECEIPT_FRESHNESS_INVALID")
        missing["independent_signed_pit_attestation"] += 1
    # A monitor's string FRESH / even VERIFIED_PIT is NOT an independent
    # authenticated provider + decision attestation. Only A320 can confirm.
    cursors = snapshot.get("cursors")
    if not isinstance(cursors, dict):
        raise ValueError("A374_CURSORS_UNAVAILABLE")
    sanitized = {}
    for name in COUNTERS:
        value = cursors.get(name)
        if type(value) is not int or value < 0:
            raise ValueError("A374_CURSORS_UNAVAILABLE")
        sanitized[name] = value
    return clock, counts, dict(missing), sanitized, len(seen), prov.get("image_id")


def analyze(first_path, second_path, *, first_sha256, second_sha256, product_sha):
    if not isinstance(product_sha, str) or not SHA40.fullmatch(product_sha):
        raise ValueError("A374_PRODUCT_SHA_INVALID")
    fst, d1 = _artifact(first_path, first_sha256)
    snd, d2 = _artifact(second_path, second_sha256)
    t1, c1, gaps1, k1, n1, image1 = _verify_receipt(fst, d1, product_sha)
    t2, c2, gaps2, k2, n2, image2 = _verify_receipt(snd, d2, product_sha)
    if t2 <= t1:
        raise ValueError("A374_RECEIPT_CLOCK_REVERSED")
    if not image1 or image1 != image2:
        raise ValueError("A374_RUNTIME_IMAGE_CHANGED")
    if fst.get("schema") != snd.get("schema"):
        raise ValueError("A374_RECEIPT_SCHEMA_CHANGED")
    delta = {k: k2[k] - k1[k] for k in COUNTERS}
    regressed = sorted(k for k, v in delta.items() if v < 0)
    family = {}
    for f in FAMILIES:
        family[f] = {
            "latest_sampled": c2[f]["sampled"],
            "earlier_sampled": c1[f]["sampled"],
            "latest_book_fresh": c2[f]["book_fresh"],
            "latest_trade_fresh": c2[f]["trade_fresh"],
            "sample_scope": "ONLY_TEN_PRIORITIZED_LATEST_ROWS_NOT_ALL_INSTRUMENTS",
            "coverage": "SAMPLED_UNVERIFIED" if c2[f]["sampled"] else "NOT_SAMPLED",
            "causal_pit": "NO_VERIFICADO_INDEPENDENT_SOURCE_ATTESTATION_REQUIRED",
            "paper_e2e": "NO_VERIFICADO_SIGNED_DECISION_AND_LEDGER_REQUIRED",
            "paper_entry_allowed_by_this_report": False,
        }
    return {
        "schema": SCHEMA,
        "status": "NO_VERIFICADO_E2E",
        "source": "TWO_GITHUB_MARKET_WATCH_ZIP_RECEIPTS_OFFHOST",
        "product_sha": product_sha,
        "first_generated_utc": t1.isoformat(),
        "second_generated_utc": t2.isoformat(),
        "first_zip_sha256": first_sha256,
        "second_zip_sha256": second_sha256,
        "sampled_first": n1,
        "sampled_second": n2,
        "latest_missing_clocks_or_receipt": gaps2,
        "first_missing_clocks_or_receipt": gaps1,
        "cursor_deltas_observed_only_not_trades": delta,
        "counter_regressions": regressed,
        "counter_regression_requires_review": bool(regressed),
        "families": family,
        "families_reviewed": len(FAMILIES),
        "families_pit_e2e_verified": 0,
        "economic_proof_not_derivable_from_counters": True,
        "positive_verifier_is_separate": "A320_AUTHENTICATED_SOURCE_AND_PAPER",
        "provider_requests": 0,
        "trading_db_access": False,
        "real_order_routes_called": 0,
        "paper_entry_authority": False,
        "readonly": True,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="RC6 A374 OFFHOST evidence gap matrix")
    parser.add_argument("--first", required=True)
    parser.add_argument("--second", required=True)
    parser.add_argument("--first-sha256", required=True)
    parser.add_argument("--second-sha256", required=True)
    parser.add_argument("--product-sha", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        result = analyze(args.first, args.second,
                         first_sha256=args.first_sha256,
                         second_sha256=args.second_sha256,
                         product_sha=args.product_sha)
        Path(args.output).write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
        print("RC6_A374_STATUS=" + result["status"])
        print("RC6_A374_SAMPLED_SECOND=" + str(result["sampled_second"]))
        return 0
    except (OSError, ValueError, TypeError):
        print("RC6_A374_STATUS=NO_VERIFICADO_INVALID_EVIDENCE")
        return 2


if __name__ == "__main__":
    sys.exit(main())
