#!/usr/bin/env python3
"""A725: correlate immutable GitHub receipts; never infer PAPER readiness.

Inputs are downloaded GitHub artifacts and job logs. This module has no
network, Docker, SSH, SQLite, market-provider, or trading dependencies.
"""
from __future__ import annotations

import argparse
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import re

SCHEMA = "rc6.a725.weekend-offhost.v1"
FAMILIES = ("ACCIONES", "CEDEARS", "ETFS", "BONOS", "LETRAS",
            "OBLIGACIONES", "OPCIONES", "FUTUROS", "CAUCIONES", "FCI")
PINS = {
    "a617": (42249258042, "0dbb04d4fe75c8d762ceaea8ca68a5682f4b19cd",
             "rc6.a617.ppi-origin-selector-readonly.v1"),
    "a655": (42249370373, "0dbb04d4fe75c8d762ceaea8ca68a5682f4b19cd",
             "rc6.a668.option-ppi-key-root-readonly.v6"),
    "a668": (42249481087, "0dbb04d4fe75c8d762ceaea8ca68a5682f4b19cd",
             "rc6.a668.nonoption-native-fivekey-readonly.v1"),
    "a627": (42253745205, "8165109cb5d30416ffbf4bcda1f9275bf5531d83",
             "rc6.a627.schedule-causality-offhost.v1"),
    "a724": (42265610943, "b2d84bfe969d083761d1159fc0840c7eecf32d5a",
             "rc6.a724.postfailed-physical-readonly.v1"),
    "r2": (42260034260, "7ac6e368bd42137817e3271839c35f579bdbbbbe", None),
}
MAX_ARTIFACT = 65536
MAX_LOG = 2 * 1024 * 1024
SCHEDULER_FINDINGS = frozenset({
    "P1_SCHEDULE_SILENT", "NO_CONFIRMED_TWO_SLOT_SILENCE", "NO_VERIFICADO"})
SCANNER_STATES = frozenset({
    "ABSENT", "NO_VERIFICADO", "SCHEMA_ASSERTED", "READ_ONLY_OBSERVED", "SCHEMA_HOLD"})
JOURNAL_STATES = frozenset({
    "ABSENT", "NO_VERIFICADO", "SCHEMA_ASSERTED", "READ_ONLY_OBSERVED",
    "SCHEMA_HOLD", "STRUCTURAL_HASH_CHAIN_VALIDATED"})
MARKER_RELATIONS = frozenset({"UNKNOWN", "R1_LAST_GO", "FAILED_R2", "OTHER_EXACT_SHA"})
BARRIER_STATES = frozenset({"ABSENT", "IDLE", "INFLIGHT", "CONTRACT_ONLY"})
COLD_SUCCESSORS = frozenset({
    "NO_VERIFICADO", "REVOKED_OR_UNIDENTIFIED", "NO_CURRENT_FAILED_QUEUE",
    "QUEUE_NOT_BOUND", "MANIFEST_RECEIPT_NOT_BOUND", "PACK_METADATA_REJECTED",
    "NO_VERIFICADO_PACK_AND_REPORT_HASH_REQUIRED"})
SCHEDULER_RECEIPTS = frozenset({"SUSPENDED", "SUSPENDING", "RESTORED", "OTHER"})


class Hold(ValueError):
    pass


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise Hold("DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def _json(raw):
    return json.loads(raw, object_pairs_hook=_unique,
                      parse_constant=lambda _value: (_ for _ in ()).throw(Hold("NAN_JSON")))


def _bounded(path: Path, limit: int):
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= limit:
        raise Hold("INPUT_ABSENT_OR_UNSAFE")
    return path.read_bytes()


def _receipt(folder: Path, schema: str, *, source: str):
    files = list(folder.rglob("*.json")) if folder.is_dir() and not folder.is_symlink() else []
    expected = ({"a617_ppi_origin_selector.json", "a617_schedule_gate.json"}
                if source == "a617" else None)
    if (len(files) != (2 if expected else 1)
            or expected is not None and {p.name for p in files} != expected
            or any(len(p.relative_to(folder).parts) > 3 for p in files)):
        raise Hold("ARTIFACT_COUNT_OR_PATH")
    by_name = {p.name: p for p in files}
    primary = by_name["a617_ppi_origin_selector.json"] if expected else files[0]
    raw = _bounded(primary, MAX_ARTIFACT)
    data = _json(raw)
    if type(data) is not dict or data.get("schema") != schema:
        raise Hold("ARTIFACT_SCHEMA")
    if data.get("provider_gets") != 0 or data.get("read_only") is not True:
        raise Hold("NONZERO_PROVIDER_OR_NON_READONLY")
    if data.get("orders_called", data.get("order_routes_called", False)) is not False:
        raise Hold("ORDERS_ROUTE_UNVERIFIED")
    if data.get("ppi_watch_touched", False) is not False:
        raise Hold("PPI_WATCH_ACCESS_UNVERIFIED")
    gate_digest = None
    if expected:
        companion = _bounded(by_name["a617_schedule_gate.json"], MAX_ARTIFACT)
        gate = _json(companion)
        if (type(gate) is not dict or gate.get("schema") != "rc6.a657.a617-schedule-gate.v1"
                or gate.get("read_only") is not True
                or gate.get("gate") != "MANUAL_READ_ONLY_FALLBACK"
                or gate.get("active") is not True
                or gate.get("scheduled_slot_art") is not None
                or gate.get("delay_seconds") is not None
                or gate.get("host_probe_state") != "NOT_ATTESTED_BY_GATE"
                or type(gate.get("provider_gets_by_gate")) is not int
                or gate["provider_gets_by_gate"] != 0
                or gate.get("order_routes_called_by_gate") is not False
                or gate.get("ppi_watch_touched_by_gate") is not False):
            raise Hold("A617_COMPANION_GATE_UNTRUSTED")
        stamp = gate.get("observed_at_art")
        try:
            seen = datetime.fromisoformat(stamp) if type(stamp) is str and len(stamp) <= 48 else None
        except ValueError:
            seen = None
        if seen is None or seen.utcoffset() is None or seen.date().isoformat() != "2026-11-06":
            raise Hold("A617_COMPANION_CLOCK_UNTRUSTED")
        gate_digest = sha256(companion).hexdigest()
    return data, sha256(raw).hexdigest(), gate_digest


def _run(path: Path, run_id: int, expected_sha: str, *, failure=False):
    value = _json(_bounded(path, MAX_ARTIFACT))
    if (type(value) is not dict or type(value.get("id")) is not int
            or value["id"] != run_id or value.get("head_sha") != expected_sha
            or value.get("event") not in {"workflow_dispatch", "push"}
            or value.get("status") != "completed"
            or value.get("conclusion") != ("failure" if failure else "success")):
        raise Hold("RUN_METADATA_MISMATCH")
    return {"run_id": run_id, "head_sha": expected_sha,
            "conclusion": value["conclusion"], "event": value["event"]}


def _enum(value, allowed):
    """Only exact source vocabulary; never echo arbitrary diagnostic strings."""
    if value is None:
        return "NO_VERIFICADO"
    if type(value) is not str or value not in allowed:
        raise Hold("SOURCE_DIAGNOSTIC_ENUM_UNTRUSTED")
    return value


def _child_enum(value, child, field, allowed):
    node = value.get(child)
    if type(node) is not dict:
        raise Hold("SOURCE_DIAGNOSTIC_SHAPE_UNTRUSTED")
    return _enum(node.get(field), allowed)


def _r2_logs(path: Path):
    lines = _bounded(path, MAX_LOG).decode("utf-8", errors="replace").splitlines()
    reasons = sorted({cause for cause in
                      ("COLD_ARCHIVED_RECOVERY_NOT_PROVEN", "SCHEDULER_STOPPED_CLOSED_FLAT_RED")
                      if any(re.search(r"Z ValueError: " + cause + r"$", line) for line in lines)})
    cleanup = None
    for line in lines:
        if len(line) > 8192 or "POROTA_DEPLOY_FAILURE_CLEANUP_V1" not in line:
            continue
        if not re.match(r"^\d{4}-\d\d-\d\dT[^ ]+Z \{", line):
            continue
        try:
            row = _json(line.split("Z ", 1)[1])
        except (ValueError, TypeError):
            continue
        if (type(row) is not dict or row.get("schema") != "POROTA_DEPLOY_FAILURE_CLEANUP_V1"
                or row.get("candidate_sha") != "a16a31c7bc4634362870c731273e084eea9d357d"
                or row.get("status") != "GREEN" or row.get("removed_tags") != []
                or row.get("staging_removed") is not True):
            continue
        reasons_map = row.get("retained_reasons")
        if (type(reasons_map) is dict and len(reasons_map) <= 2
                and set(reasons_map.values()) == {"NONCANDIDATE_ALIAS_OR_DIGEST"}
                and type(row.get("space_recovered")) is int):
            cleanup = {"status": "STAGING_ONLY", "retained_tags": len(reasons_map),
                       "retained_reason": "NONCANDIDATE_ALIAS_OR_DIGEST",
                       "observed_recovered_bytes": row["space_recovered"]}
    return {"confirmed_errors": reasons,
            "cleanup": cleanup if cleanup is not None else "NO_VERIFICADO"}


def consolidate(root: Path):
    report = {"schema": SCHEMA, "state": "NO_VERIFICADO", "read_only": True,
              "provider_gets": 0, "host_access": False, "ppi_watch_touched": False,
              "real_orders_sent_by_action": 0, "order_routes_called": False,
              "paper_ready_certified": False, "causal_pit_certified": False,
              "sources": {}, "families": [], "holds": []}
    loaded = {}
    safe_diagnostics = {}
    for key, (run_id, pin, schema) in PINS.items():
        try:
            meta = _run(root / "metadata" / (str(run_id) + ".json"), run_id,
                        pin, failure=(key == "r2"))
            if schema is not None:
                receipt, digest, gate_digest = _receipt(root / "artifacts" / key,
                                                       schema, source=key)
                if (key == "a627" and receipt.get("source_status") != "VERIFIED_API"
                        or key != "a627" and key != "a724"
                        and receipt.get("state") != "READ_ONLY_OBSERVED"
                        or key == "a724" and receipt.get("state") != "DIAGNOSTIC_ONLY"):
                    raise Hold("SOURCE_DIAGNOSTIC_UNVERIFIED")
                if key == "a655":
                    coverage = receipt.get("status_coverage")
                    if (type(coverage) is not dict or set(coverage) !=
                            {"BONOS", "OBLIGACIONES", "OPCIONES", "FUTUROS", "CAUCIONES", "FCI"}
                            or any(type(row) is not dict or type(row.get("active_gap")) is not int
                                   or not 0 <= row["active_gap"] <= 100000
                                   for row in coverage.values())
                            or sum(row["active_gap"] for row in coverage.values()) !=
                            receipt.get("catalog_active_gap_total")):
                        raise Hold("GAP_DENOMINATOR_UNVERIFIED")
                if key == "a668":
                    targets = receipt.get("target_by_family")
                    if (type(targets) is not dict or set(targets) !=
                            {"BONOS", "OBLIGACIONES", "FCI"}
                            or any(type(n) is not int or not 0 <= n <= 100000
                                   for n in targets.values())
                            or sum(targets.values()) != receipt.get("target_total")):
                        raise Hold("NATIVE_TARGET_DENOMINATOR_UNVERIFIED")
                if key == "a724" and (receipt.get("mutations") != 0 or
                                       receipt.get("runtime_running_verified") is not False):
                    raise Hold("POSTFAIL_HOST_SAFETY_UNVERIFIED")
                if key == "a617":
                    safe_diagnostics[key] = {
                        "scanner_receipt": _child_enum(receipt, "scanner_receipt", "state", SCANNER_STATES),
                        "native_book_journal": _child_enum(receipt, "native_book_journal", "state", JOURNAL_STATES)}
                if key == "a627":
                    slots = receipt.get("due_slots")
                    if type(slots) is not int or not 0 <= slots <= 365:
                        raise Hold("SCHEDULER_DENOMINATOR_UNTRUSTED")
                    safe_diagnostics[key] = {
                        "finding": _enum(receipt.get("source_scheduler_finding"), SCHEDULER_FINDINGS),
                        "due_slots": slots}
                if key == "a724":
                    unreferenced = receipt.get("unreferenced_total")
                    if type(unreferenced) is not int or not -1 <= unreferenced <= 128:
                        raise Hold("POSTFAIL_IMAGE_COUNT_UNTRUSTED")
                    safe_diagnostics[key] = {
                        "marker_relation": _child_enum(receipt, "marker", "relation", MARKER_RELATIONS),
                        "barrier_state": _child_enum(receipt, "barrier", "status", BARRIER_STATES),
                        "cold_successor": _child_enum(receipt, "cold", "successor_predicate", COLD_SUCCESSORS),
                        "scheduler_receipt": _child_enum(receipt, "scheduler", "receipt_status", SCHEDULER_RECEIPTS),
                        "unreferenced_images": unreferenced}
                loaded[key] = receipt
                meta["receipt_sha256"] = digest
                if gate_digest is not None:
                    meta["a617_schedule_gate_sha256"] = gate_digest
            report["sources"][key] = {**meta, "source_integrity": "GREEN"}
        except (Hold, OSError, TypeError, UnicodeError, json.JSONDecodeError) as exc:
            reason = str(exc) if isinstance(exc, Hold) else "INPUT_READ_OR_PARSE_HOLD"
            report["sources"][key] = {"source_integrity": "NO_VERIFICADO", "reason": reason,
                                       "run_id": run_id}
            report["holds"].append(key + ":" + reason)

    a655, a668 = loaded.get("a655", {}), loaded.get("a668", {})
    gaps = a655.get("status_coverage", {})
    target = a668.get("target_by_family", {})
    for family in FAMILIES:
        item = {"family": family, "causal_pit": "NO_VERIFICADO",
                "paper_readiness": "NO_VERIFICADO", "real_market_replay": "NO_VERIFICADO",
                "active_contract_gap": None, "nonoption_native_target": None}
        if family in gaps and type(gaps[family]) is dict:
            n = gaps[family].get("active_gap")
            if type(n) is int and 0 <= n <= 100000:
                item["active_contract_gap"] = n
        if family in target and type(target[family]) is int:
            item["nonoption_native_target"] = target[family]
        report["families"].append(item)

    if "a617" in loaded:
        report["ppi"] = {"lease_effective": "NO_VERIFICADO",
                         "exit_budget_reserved": "NO_VERIFICADO",
                         **safe_diagnostics["a617"]}
    if "a627" in loaded:
        report["schedules"] = {"source": "GITHUB_ACTIONS_METADATA_ONLY",
                               **safe_diagnostics["a627"],
                               "host_timer_verified": False}
    if "a724" in loaded:
        report["physical_postfailure"] = {
            "state": "DIAGNOSTIC_ONLY",
            **safe_diagnostics["a724"],
            "runtime_running_certified": False}
    r2_log_verified = False
    try:
        report["r2_failure"] = _r2_logs(root / "logs" / "r2.log")
        if len(report["r2_failure"]["confirmed_errors"]) != 2:
            raise Hold("R2_ROOT_CAUSE_LOG_MISSING")
        if report["r2_failure"]["cleanup"] == "NO_VERIFICADO":
            raise Hold("R2_CLEANUP_LOG_MISSING")
        r2_log_verified = True
    except (Hold, OSError, UnicodeError):
        # The independently verified R2 run metadata still says failure.
        # Job log availability is an optional evidence channel, never a GO.
        report["r2_failure"] = {"state": "NO_VERIFICADO",
                                "run_conclusion": "failure" if
                                report["sources"]["r2"]["source_integrity"] == "GREEN"
                                else "NO_VERIFICADO",
                                "cleanup": "NO_VERIFICADO", "root_cause": "NO_VERIFICADO"}
    report["artifact_integrity"] = "GREEN" if not report["holds"] else "NO_VERIFICADO"
    report["r2_log_integrity"] = "GREEN" if r2_log_verified else "NO_VERIFICADO"
    report["state"] = ("NO_VERIFICADO" if report["holds"] else
                       "OFFHOST_RECONCILED" if r2_log_verified else
                       "OFFHOST_RECONCILED_PARTIAL")
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = consolidate(args.input_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print("A725_OFFHOST_INTEGRITY=" + result["artifact_integrity"])
    print("A725_R2_LOG_INTEGRITY=" + result["r2_log_integrity"])
    print("A725_FAMILIES=10|PIT=NO_VERIFICADO|PROVIDER_GETS=0|HOST_ACCESS=0")
    return 0 if result["artifact_integrity"] == "GREEN" else 2


if __name__ == "__main__":
    raise SystemExit(main())
