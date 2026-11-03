"""Independent recomputation from sanitized fills, without the trading engine.

The exporter copies recorded ledger totals; this module reconstructs cashflows
from each fill. No fee model, strategy, broker import or source DB is needed.
"""
import json
import re
from collections import Counter, defaultdict
from decimal import Decimal, localcontext
from pathlib import Path

from .package import (ART, ADMIN_VOID_AUTHORITIES, ADMIN_VOID_REASON, A207_ADMIN_AUTHORITY,
                      CALCULATION, EXIT_CODES, FAMILIES,
                      FILES, FILES_V2, METHOD, METHOD_V2, SCHEMA, SCHEMA_V2, Budget, EvidenceError, Limits,
                      _safe_file, bounds, canonical, cohort_sessions, decimal,
                      money, sha256, stamp, token)

POSITION_FIELDS = {
    "position_id", "strategy", "strategy_version", "symbol", "family", "market", "currency", "settlement",
    "entry_at", "exit_at", "entry_session", "exit_session", "lifecycle", "position_quantity", "cash_multiplier",
    "multiplier_status", "units", "units_per_lot", "units_per_lot_status", "exit_reason", "exit_reason_status",
    "ledger", "lineage", "source_row_commitment", "row_sha256",
}
FILL_FIELDS = {
    "fill_id", "position_id", "side", "filled_at", "position_fill_index", "session", "quantity", "price", "costs",
    "cost_components", "cost_components_status", "slippage_price_delta", "slippage_accounting",
    "provider_at", "provider_clock_status", "source_row_commitment", "row_sha256",
}
MANIFEST_FIELDS = {
    "schema", "calculation_version", "mode", "source_access", "real_orders_sent", "real_routes", "runtime_mutation",
    "source_safety", "cohort", "session_rows", "limits", "counts", "currencies", "currency_totals_combined", "files",
    "missing_evidence", "source_authentication", "truncated", "invented_rows", "calibration_or_edge_claim",
}
MANIFEST_FIELDS_V2 = MANIFEST_FIELDS | {"admin_void_exclusions"}
VOID_FIELDS = {
    "position_id", "family", "currency", "opened_at", "cancelled_at",
    "entry_session", "admin_cancel_day", "lifecycle", "authority",
    "close_reason", "source_proof", "sell_simulated_fills", "realized_pnl",
    "a207_batch_commitment", "a207_full_batch_complete",
    "source_row_commitment", "source_receipt_commitment",
    "source_buy_commitment", "source_event_commitment", "row_sha256",
}


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise EvidenceError("DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def _json(data):
    try:
        return json.loads(data, object_pairs_hook=_pairs, parse_constant=lambda _: (_ for _ in ()).throw(EvidenceError("INVALID_JSON_NUMBER")))
    except (ValueError, TypeError, UnicodeError, RecursionError) as error:
        if isinstance(error, EvidenceError):
            raise
        raise EvidenceError("INVALID_PACKAGE_JSON") from None


def _rows(data, maximum, budget):
    lines = data.splitlines(keepends=True)
    if len(lines) > maximum:
        raise EvidenceError("PACKAGE_ROW_BUDGET_EXHAUSTED")
    rows = []
    for line in lines:
        budget.check()
        if len(line) > budget.limits.row_bytes:
            raise EvidenceError("ROW_BYTE_BUDGET_EXHAUSTED")
        row = _json(line)
        if not isinstance(row, dict) or line != (canonical(row) + "\n").encode():
            raise EvidenceError("CANONICAL_PACKAGE_REQUIRED")
        rows.append(row)
    return rows


def _record(row, fields, commitment_prefix):
    if set(row) != fields:
        raise EvidenceError("SANITIZED_SCHEMA_MISMATCH")
    record = {k: v for k, v in row.items() if k != "row_sha256"}
    if sha256(canonical(record).encode()) != row["row_sha256"]:
        raise EvidenceError("ROW_HASH_MISMATCH")
    if not isinstance(row["source_row_commitment"], str) or not re.fullmatch(commitment_prefix + r"_[0-9a-f]{64}", row["source_row_commitment"]):
        raise EvidenceError("SOURCE_COMMITMENT_REQUIRED")


def _recompute(positions, fills, sessions, budget):
    by_position, by_currency = {}, defaultdict(list)
    seen_fill_ids, grouped = set(), defaultdict(list)
    _, cutoff = bounds(sessions)
    for position in positions:
        budget.check()
        _record(position, POSITION_FIELDS, "srcpos")
        ident = position["position_id"]
        if not isinstance(ident, str) or not re.fullmatch(r"pos_[0-9a-f]{64}", ident) or ident in by_position:
            raise EvidenceError("DUPLICATE_OR_INVALID_POSITION")
        for field in ("strategy", "strategy_version", "symbol", "family", "market", "currency", "settlement"):
            token(position[field])
        if position["family"] not in FAMILIES:
            raise EvidenceError("SPECIALIZED_LEDGER_REQUIRED")
        if position["currency"] not in {"ARS", "USD", "USD_MEP", "USD_CCL"}:
            raise EvidenceError("CURRENCY_IDENTITY_REQUIRED")
        opened = stamp(position["entry_at"])
        closed = stamp(position["exit_at"]) if position["exit_at"] is not None else None
        if (opened >= cutoff or position["entry_session"] != opened.astimezone(ART).date().isoformat()
                or position["lifecycle"] not in {"CLOSED", "OPEN_AT_CUTOFF"}
                or (position["lifecycle"] == "CLOSED") != (closed is not None)):
            raise EvidenceError("POSITION_LIFECYCLE_MISMATCH")
        if closed is not None and (closed < opened or closed >= cutoff or position["exit_session"] not in sessions
                                   or position["exit_session"] != closed.astimezone(ART).date().isoformat()):
            raise EvidenceError("POSITION_CLOCK_ORDER")
        if closed is None and (position["exit_session"] is not None or position["exit_reason"] is not None):
            raise EvidenceError("POSITION_LIFECYCLE_MISMATCH")
        decimal(position["cash_multiplier"], positive=True)
        decimal(position["position_quantity"], positive=True)
        expected_units = "SHARES" if position["family"] in {"ACCIONES", "CEDEARS", "ETFS"} else "CONTRACT_QUANTITY"
        if (position["units"] != expected_units or position["units_per_lot"] is not None
                or position["units_per_lot_status"] != "NO_VERIFICADO"
                or position["multiplier_status"] not in {"SPOT_SHARE_CASH_CONVENTION", "EXPLICIT_SOURCE"}
                or (position["multiplier_status"] == "SPOT_SHARE_CASH_CONVENTION"
                    and (expected_units != "SHARES" or decimal(position["cash_multiplier"]) != 1))):
            raise EvidenceError("UNITS_PROVENANCE_MISMATCH")
        expected_reason_status = "OPEN" if closed is None else "NO_VERIFICADO" if position["exit_reason"] == "LEGACY_REASON_UNMAPPED" else "NATIVE_CODE"
        if (position["exit_reason_status"] != expected_reason_status or (closed is not None
                and position["exit_reason"] not in EXIT_CODES | {"LEGACY_REASON_UNMAPPED"})):
            raise EvidenceError("EXIT_REASON_PROVENANCE_MISMATCH")
        if set(position["ledger"]) != {"entry_cost", "exit_cost", "gross_pnl", "net_pnl"}:
            raise EvidenceError("LEDGER_SCHEMA_MISMATCH")
        if closed is None and any(position["ledger"][name] is not None for name in ("exit_cost", "gross_pnl", "net_pnl")):
            raise EvidenceError("OPEN_LEDGER_CLOSED_AMOUNTS_FORBIDDEN")
        lineage = position["lineage"]
        if not isinstance(lineage, dict) or set(lineage) != {"clocks", "hashes", "status"}:
            raise EvidenceError("LINEAGE_SCHEMA_MISMATCH")
        clocks = lineage["clocks"]
        if set(clocks) != {"signal_started_at", "signal_at", "decision_at", "intent_at", "entry_fill_committed_at"}:
            raise EvidenceError("LINEAGE_SCHEMA_MISMATCH")
        prior = None
        for name in ("signal_started_at", "signal_at", "decision_at", "intent_at", "entry_fill_committed_at"):
            if clocks[name] is not None:
                at = stamp(clocks[name])
                if at >= cutoff or (prior is not None and at < prior):
                    raise EvidenceError("LINEAGE_CLOCK_ORDER")
                if closed is not None and at > closed:
                    raise EvidenceError("LINEAGE_AFTER_POSITION_EXIT")
                if name in {"signal_started_at", "signal_at", "decision_at"} and at > opened:
                    raise EvidenceError("LINEAGE_AFTER_ENTRY_SIGNAL")
                if name == "entry_fill_committed_at" and at < opened:
                    raise EvidenceError("LINEAGE_COMMIT_BEFORE_ENTRY")
                prior = at
        hashes = lineage["hashes"]
        if set(hashes) != {"git_sha", "candidate_tree_sha", "configuration_fingerprint", "manifest_sha256"}:
            raise EvidenceError("LINEAGE_SCHEMA_MISMATCH")
        for name, value in hashes.items():
            length = 40 if name in {"git_sha", "candidate_tree_sha"} else 64
            if value is not None and (not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{" + str(length) + "}", value)):
                raise EvidenceError("LINEAGE_HASH_INVALID")
        expected_lineage_status = "SOURCE_FIELDS_PRESENT" if all(hashes.values()) and all(clocks.values()) else "NO_VERIFICADO"
        if lineage["status"] != expected_lineage_status:
            raise EvidenceError("LINEAGE_PROVENANCE_MISMATCH")
        by_position[ident] = position
    for fill in fills:
        budget.check()
        _record(fill, FILL_FIELDS, "srcfill")
        ident = fill["fill_id"]
        if not isinstance(ident, str) or not re.fullmatch(r"fill_[0-9a-f]{64}", ident) or ident in seen_fill_ids:
            raise EvidenceError("DUPLICATE_OR_INVALID_FILL")
        seen_fill_ids.add(ident)
        position = by_position.get(fill["position_id"])
        if position is None:
            raise EvidenceError("ORPHAN_FILL")
        at = stamp(fill["filled_at"])
        if (at < stamp(position["entry_at"]) or at >= cutoff
                or (position["exit_at"] is not None and at > stamp(position["exit_at"]))
                or fill["session"] != at.astimezone(ART).date().isoformat()):
            raise EvidenceError("FILL_CLOCK_ORDER")
        if fill["side"] not in {"BUY_SIMULATED", "SELL_SIMULATED"}:
            raise EvidenceError("PAPER_FILL_REQUIRED")
        if type(fill["position_fill_index"]) is not int or fill["position_fill_index"] < 1:
            raise EvidenceError("FILL_ORDER_REQUIRED")
        costs = decimal(fill["costs"], nonnegative=True)
        components = fill["cost_components"]
        if (not isinstance(components, dict) or set(components) != {"aggregate_explicit_charge", "commission", "rights", "vat"}
                or decimal(components["aggregate_explicit_charge"], nonnegative=True) != costs
                or any(components[k] is not None for k in ("commission", "rights", "vat"))
                or fill["cost_components_status"] != "NO_VERIFICADO"
                or fill["slippage_accounting"] != "EMBEDDED_IN_EXECUTION_PRICE"
                or fill["provider_at"] is not None or fill["provider_clock_status"] != "NO_VERIFICADO"):
            raise EvidenceError("COST_OR_CLOCK_PROVENANCE_MISMATCH")
        decimal(fill["slippage_price_delta"], nonnegative=True)
        decimal(fill["quantity"], positive=True)
        decimal(fill["price"], positive=True)
        grouped[fill["position_id"]].append(fill)
    if positions != sorted(positions, key=lambda p: p["position_id"]) or fills != sorted(fills, key=lambda f: (f["position_id"], f["position_fill_index"])):
        raise EvidenceError("DETERMINISTIC_ORDER_REQUIRED")

    results = []
    with localcontext() as context:
        # Three bounded operands can carry 129 significant digits; 100,000
        # terms add at most five more. Keep cashflows exact before subtraction.
        context.prec = 160
        for ident, position in by_position.items():
            budget.check()
            legs = grouped[ident]
            if not legs or legs[0]["side"] != "BUY_SIMULATED" or legs[0]["filled_at"] != position["entry_at"]:
                raise EvidenceError("OPENING_FILL_MISSING")
            bought = sold = purchases = sales = buy_costs = sell_costs = Decimal(0)
            prior = None
            buys = sells = 0
            for index, fill in enumerate(legs, 1):
                if fill["position_fill_index"] != index or (prior is not None and stamp(fill["filled_at"]) < prior):
                    raise EvidenceError("FILL_ORDER_REQUIRED")
                prior = stamp(fill["filled_at"])
                qty, price = decimal(fill["quantity"]), decimal(fill["price"])
                cash = qty * price * decimal(position["cash_multiplier"])
                if fill["side"] == "BUY_SIMULATED":
                    buys += 1
                    bought += qty
                    purchases += cash
                    buy_costs += decimal(fill["costs"])
                else:
                    sells += 1
                    sold += qty
                    sales += cash
                    sell_costs += decimal(fill["costs"])
                    if sold > bought:
                        raise EvidenceError("OVER_SALE_OR_MISSING_ENTRY_FILL")
                    if sold == bought and index != len(legs):
                        raise EvidenceError("LIFECYCLE_REOPEN_AFTER_FULL_LIQUIDATION")
            if bought != decimal(position["position_quantity"]):
                raise EvidenceError("OPENING_QUANTITY_MISMATCH")
            closed = position["lifecycle"] == "CLOSED"
            if (closed and (bought != sold or legs[-1]["side"] != "SELL_SIMULATED"
                            or legs[-1]["filled_at"] != position["exit_at"])) or (not closed and sold >= bought):
                raise EvidenceError("CLOSING_QUANTITY_MISMATCH")
            charges = buy_costs + sell_costs
            gross, net = (sales - purchases, sales - purchases - charges) if closed else (None, None)
            if decimal(position["ledger"]["entry_cost"], nonnegative=True) != buy_costs:
                raise EvidenceError("LEDGER_COST_RECONCILIATION")
            if closed:
                for name, actual in (("exit_cost", sell_costs), ("gross_pnl", gross), ("net_pnl", net)):
                    if position["ledger"][name] is None or decimal(position["ledger"][name]) != actual:
                        raise EvidenceError("LEDGER_MONEY_RECONCILIATION")
                if gross - charges != net:
                    raise EvidenceError("GROSS_COSTS_NET_MISMATCH")
                by_currency[position["currency"]].append((position, gross, charges, net))
            results.append({"position_id": ident, "currency": position["currency"], "lifecycle": position["lifecycle"],
                            "fills": len(legs), "buy_fills": buys, "sell_fills": sells,
                            "bought_quantity": money(bought), "sold_quantity": money(sold), "remaining_quantity": money(bought - sold),
                            "entry_notional": money(purchases), "exit_notional": money(sales), "costs": money(charges),
                            "gross_pnl": money(gross) if gross is not None else None,
                            "net_pnl": money(net) if net is not None else None, "partial_exit": sells > 1})
        currencies = {}
        for currency in sorted({p["currency"] for p in positions}):
            trades = by_currency[currency]
            wins = sum(net > 0 for _, _, _, net in trades)
            profit = sum((max(net, Decimal(0)) for _, _, _, net in trades), Decimal(0))
            loss = -sum((min(net, Decimal(0)) for _, _, _, net in trades), Decimal(0))
            currencies[currency] = {
                "closed_positions": len(trades), "open_survivors": sum(p["currency"] == currency and p["lifecycle"] == "OPEN_AT_CUTOFF" for p in positions),
                "gross_pnl": money(sum((gross for _, gross, _, _ in trades), Decimal(0))),
                "costs": money(sum((cost for _, _, cost, _ in trades), Decimal(0))),
                "net_pnl": money(sum((net for _, _, _, net in trades), Decimal(0))),
                "winners": wins, "win_rate": money(Decimal(wins) / len(trades)) if trades else None,
                "profit_factor": money(profit / loss) if loss else None,
                "profit_factor_status": "COMPUTED" if loss else "NO_LOSING_CLOSED_POSITIONS" if trades else "NO_CLOSED_POSITIONS",
                "exit_reasons": dict(sorted(Counter(p["exit_reason"] for p, _, _, _ in trades).items())),
            }
    return results, currencies


def _verify_admin_voids(voids, positions, sessions, budget):
    start, cutoff = bounds(sessions)
    seen = {p["position_id"] for p in positions}
    if any(not isinstance(row, dict) or set(row) != VOID_FIELDS for row in voids):
        raise EvidenceError("ADMIN_VOID_SCHEMA_MISMATCH")
    if voids != sorted(voids, key=lambda row: row.get("position_id", "")):
        raise EvidenceError("ADMIN_VOID_DETERMINISTIC_ORDER_REQUIRED")
    for void in voids:
        budget.check()
        if not isinstance(void, dict) or set(void) != VOID_FIELDS:
            raise EvidenceError("ADMIN_VOID_SCHEMA_MISMATCH")
        item = dict(void)
        digest = item.pop("row_sha256")
        if digest != sha256(canonical(item).encode()):
            raise EvidenceError("ADMIN_VOID_ROW_HASH_MISMATCH")
        ident = void["position_id"]
        if (not isinstance(ident, str) or not re.fullmatch(r"pos_[0-9a-f]{64}", ident)
                or ident in seen):
            raise EvidenceError("ADMIN_VOID_DUPLICATE_POSITION")
        seen.add(ident)
        a207 = void["authority"] == A207_ADMIN_AUTHORITY
        if (token(void["family"]) not in FAMILIES
                or token(void["currency"]) not in {"ARS", "USD", "USD_MEP", "USD_CCL"}
                or void["lifecycle"] != "CANCELLED_ADMIN_NO_EXECUTION"
                or not isinstance(void["authority"], str)
                or void["authority"] not in ADMIN_VOID_AUTHORITIES
                or void["close_reason"] != ADMIN_VOID_REASON
                or void["source_proof"] != (
                    "A207_SEALED_BATCH_SPOT_MEMBERSHIP_ONLY_FULL_BATCH_UNVERIFIED" if a207
                    else "BOUNDED_READ_ONLY_SOURCE_RECEIPT_VERIFIED")
                or void["a207_full_batch_complete"] != (
                    "NO_VERIFICADO" if a207 else "NOT_APPLICABLE")
                or type(void["sell_simulated_fills"]) is not int
                or void["sell_simulated_fills"] != 0
                or void["realized_pnl"] is not None):
            raise EvidenceError("ADMIN_VOID_ECONOMIC_TREATMENT_MISMATCH")
        if (a207 and (not isinstance(void["a207_batch_commitment"], str)
                      or not re.fullmatch(r"srcbatch_[0-9a-f]{64}",
                                          void["a207_batch_commitment"]))
                or not a207 and void["a207_batch_commitment"] is not None):
            raise EvidenceError("ADMIN_VOID_BATCH_MEMBERSHIP_COMMITMENT_MISSING")
        opened, cancelled = stamp(void["opened_at"]), stamp(void["cancelled_at"])
        if (opened >= cutoff or cancelled < start or cancelled >= cutoff or cancelled < opened
                or void["entry_session"] != opened.astimezone(ART).date().isoformat()
                or void["admin_cancel_day"] != cancelled.astimezone(ART).date().isoformat()):
            raise EvidenceError("ADMIN_VOID_CLOCK_MISMATCH")
        for key, prefix in (("source_row_commitment", "srcvoid"),
                            ("source_receipt_commitment", "srcadmin"),
                            ("source_buy_commitment", "srcbuy"),
                            ("source_event_commitment", "srcevent")):
            value = void[key]
            if not isinstance(value, str) or not re.fullmatch(prefix + r"_[0-9a-f]{64}", value):
                raise EvidenceError("ADMIN_VOID_SOURCE_COMMITMENT_MISSING")


def verify_package(directory, *, expected_manifest_sha256=None, limits=None, _deadline=None):
    """Reject corrupt, incomplete, mixed, over-budget or arithmetically false evidence."""
    limits = limits or Limits()
    budget = Budget(limits)
    if _deadline is not None:
        budget.deadline = min(budget.deadline, _deadline)
    directory = Path(directory).absolute()
    if not directory.is_dir() or directory.resolve() != directory:
        raise EvidenceError("REGULAR_PACKAGE_DIRECTORY_REQUIRED")
    entries = set()
    for entry in directory.iterdir():
        entries.add(entry.name)
        if len(entries) > len(FILES_V2):
            raise EvidenceError("PACKAGE_FILE_SET_MISMATCH")
    if entries not in (FILES, FILES_V2):
        raise EvidenceError("PACKAGE_FILE_SET_MISMATCH")
    payloads, total = {}, 0
    for name in sorted(entries):
        budget.check()
        path = _safe_file(directory / name)
        size = path.stat().st_size
        if name.endswith(".json") and size > limits.row_bytes:
            raise EvidenceError("ROW_BYTE_BUDGET_EXHAUSTED")
        total += size
        if total > limits.bytes:
            raise EvidenceError("BYTE_BUDGET_EXHAUSTED")
        with path.open("rb") as source:
            data = source.read(min(limits.bytes, size) + 1)
        if len(data) != size:
            raise EvidenceError("PACKAGE_SIZE_CHANGED")
        payloads[name] = data
    manifest_data = payloads["manifest.json"]
    if expected_manifest_sha256 is not None and sha256(manifest_data) != expected_manifest_sha256:
        raise EvidenceError("MANIFEST_DIGEST_MISMATCH")
    manifest = _json(manifest_data)
    if not isinstance(manifest, dict) or manifest_data != (canonical(manifest) + "\n").encode():
        raise EvidenceError("CANONICAL_PACKAGE_REQUIRED")
    version_v2 = manifest.get("schema") == SCHEMA_V2
    if (not isinstance(manifest.get("schema"), str)
            or manifest.get("schema") not in {SCHEMA, SCHEMA_V2}
            or entries != (FILES_V2 if version_v2 else FILES)
            or set(manifest) != (MANIFEST_FIELDS_V2 if version_v2 else MANIFEST_FIELDS)):
        raise EvidenceError("SANITIZED_MANIFEST_SCHEMA_MISMATCH")
    if (manifest.get("calculation_version") != CALCULATION
            or manifest.get("mode") != "PAPER/SHADOW"
            or manifest.get("source_access") != "READ_ONLY/mode=ro/query_only/SELECT_ONLY"
            or type(manifest.get("real_orders_sent")) is not int or manifest.get("real_orders_sent") != 0
            or manifest.get("real_routes") != "NOT_CALLED"
            or manifest.get("runtime_mutation") is not False or manifest.get("truncated") is not False
            or type(manifest.get("invented_rows")) is not int or manifest.get("invented_rows") != 0
            or manifest.get("currency_totals_combined") is not False or manifest.get("calibration_or_edge_claim") is not False
            or manifest.get("source_safety") != "SNAPSHOT_PRODUCTION_PAPER_ZERO_REAL_ORDERS"
            or manifest.get("source_authentication") != "EXTERNAL_EVIDENCE_PENDING_UNTIL_SOURCE_OWNER_RECONCILIATION"):
        raise EvidenceError("PACKAGE_SAFETY_CONTRACT_MISMATCH")
    if not isinstance(manifest["limits"], dict) or set(manifest["limits"]) != {"positions", "fills", "bytes", "row_bytes", "seconds"}:
        raise EvidenceError("MANIFEST_BUDGET_MISMATCH")
    declared_limits = Limits(**manifest["limits"])
    if total > declared_limits.bytes or any(len(payloads[name]) > declared_limits.row_bytes for name in FILES if name.endswith(".json")):
        raise EvidenceError("MANIFEST_BUDGET_MISMATCH")
    if not isinstance(manifest["cohort"], dict) or set(manifest["cohort"]) != {"sessions", "session_count", "timezone", "cutoff_exclusive", "selection"}:
        raise EvidenceError("COHORT_CONTRACT_MISMATCH")
    sessions = cohort_sessions(manifest["cohort"]["sessions"])
    expected_selection = ("OVERLAPPING_SPOT_POSITIONS_WITH_ADMIN_VOIDS_V2"
                          if version_v2 else "OVERLAPPING_SPOT_POSITIONS")
    if (type(manifest["cohort"]["session_count"]) is not int or manifest["cohort"]["session_count"] != 20
            or manifest["cohort"]["timezone"] != str(ART) or manifest["cohort"]["selection"] != expected_selection
            or manifest["cohort"]["cutoff_exclusive"] != bounds(sessions)[1].isoformat()
            or manifest["cohort"]["sessions"] != sessions):
        raise EvidenceError("COHORT_CONTRACT_MISMATCH")
    if set(manifest.get("files", {})) != entries - {"manifest.json"}:
        raise EvidenceError("MANIFEST_FILE_SET_MISMATCH")
    for name, detail in manifest["files"].items():
        if (not isinstance(detail, dict) or set(detail) != {"sha256", "bytes", "rows"}
                or type(detail["bytes"]) is not int or type(detail["rows"]) is not int
                or detail["bytes"] != len(payloads[name]) or detail["sha256"] != sha256(payloads[name])):
            raise EvidenceError("FILE_DIGEST_MISMATCH")
    methodology = _json(payloads["methodology.json"])
    if methodology != (METHOD_V2 if version_v2 else METHOD):
        raise EvidenceError("CALCULATION_VERSION_MISMATCH")
    positions = _rows(payloads["positions.jsonl"], limits.positions, budget)
    fills = _rows(payloads["fills.jsonl"], limits.fills, budget)
    voids = _rows(payloads["voids.jsonl"], limits.positions, budget) if version_v2 else []
    if (len(positions) > declared_limits.positions or len(fills) > declared_limits.fills
            or len(positions) + len(voids) > declared_limits.positions
            or any(len(line) > declared_limits.row_bytes for name in (
                       ("positions.jsonl", "fills.jsonl", "voids.jsonl") if version_v2
                       else ("positions.jsonl", "fills.jsonl"))
                   for line in payloads[name].splitlines(keepends=True))):
        raise EvidenceError("MANIFEST_BUDGET_MISMATCH")
    if not positions and not voids:
        raise EvidenceError("EMPTY_COHORT_EXTERNAL_EVIDENCE_PENDING")
    counts = {"positions": len(positions), "fills": len(fills),
              "closed_positions": sum(p.get("lifecycle") == "CLOSED" for p in positions),
              "open_survivors": sum(p.get("lifecycle") == "OPEN_AT_CUTOFF" for p in positions)}
    if version_v2:
        counts["admin_voids"] = len(voids)
    if (not isinstance(manifest["counts"], dict) or any(type(v) is not int for v in manifest["counts"].values())
            or manifest["counts"] != counts or manifest["files"]["positions.jsonl"]["rows"] != len(positions)
            or manifest["files"]["fills.jsonl"]["rows"] != len(fills) or manifest["files"]["methodology.json"]["rows"] != 1
            or (version_v2 and manifest["files"]["voids.jsonl"]["rows"] != len(voids))):
        raise EvidenceError("ROW_COUNT_MISMATCH")
    results, currencies = _recompute(positions, fills, sessions, budget)
    if version_v2:
        _verify_admin_voids(voids, positions, sessions, budget)
        has_a207 = any(v["authority"] == A207_ADMIN_AUTHORITY for v in voids)
        if manifest["admin_void_exclusions"] != {
                "count": len(voids), "trade_pnl_effect": "NONE",
                "source_proof": ("A207_SPOT_BATCH_MEMBERSHIP_FULL_BATCH_UNVERIFIED"
                                 if has_a207 else "BOUNDED_READ_ONLY_SOURCE_RECEIPT_VERIFIED"),
                "external_authentication": "SOURCE_OWNER_RECONCILIATION_REQUIRED"}:
            raise EvidenceError("ADMIN_VOID_MANIFEST_MISMATCH")
    expected_missing = ["ACCOUNT_SPECIFIC_FEES", "COMMISSION_RIGHTS_VAT_BREAKDOWN", "FILL_PROVIDER_CLOCKS",
                        "UNITS_PER_LOT", "CASH_EQUITY_RECONCILIATION", "CORPORATE_ACTIONS", "SPECIALIZED_NONSPOT_LEDGERS"]
    if any(p["lineage"]["status"] == "NO_VERIFICADO" for p in positions):
        expected_missing.append("POSITION_CLOCK_OR_SOURCE_LINEAGE")
    if any(p["strategy"] == "NO_VERIFICADO" for p in positions):
        expected_missing.append("STRATEGY_ID")
    if any(p["exit_reason_status"] == "NO_VERIFICADO" for p in positions):
        expected_missing.append("LEGACY_EXIT_REASON_MAPPING")
    if version_v2:
        expected_missing.append("ADMIN_VOID_EXTERNAL_SOURCE_OWNER_RECONCILIATION")
        if any(v["authority"] == A207_ADMIN_AUTHORITY for v in voids):
            expected_missing.append("A207_BATCH_FULL_TARGET_SET_COMPLETENESS_NO_VERIFICADO")
    if manifest["missing_evidence"] != expected_missing:
        raise EvidenceError("MISSING_EVIDENCE_PROVENANCE_MISMATCH")
    session_rows = {session: {"fills": sum(f["session"] == session for f in fills),
                              "closed_positions": sum(p["exit_session"] == session for p in positions),
                              "source_coverage": "NO_VERIFICADO"}
                    for session in sessions}
    if version_v2:
        for session in sessions:
            session_rows[session]["admin_voids"] = sum(v["admin_cancel_day"] == session for v in voids)
    if manifest.get("session_rows") != session_rows:
        raise EvidenceError("SESSION_ROW_COUNT_MISMATCH")
    if manifest["currencies"] != sorted(currencies):
        raise EvidenceError("CURRENCY_MANIFEST_MISMATCH")
    report = {"schema": ("rc6.independent-20session-recomputation.v2" if version_v2
                       else "rc6.independent-20session-recomputation.v1"),
            "status": ("RECOMPUTED_ADMIN_VOIDS_NO_PERFORMANCE" if version_v2 and not positions
                       else "RECOMPUTED"),
            "manifest_sha256": sha256(manifest_data), "calculation_version": CALCULATION,
            "source_authentication": "EXTERNAL_EVIDENCE_PENDING_UNTIL_SOURCE_OWNER_RECONCILIATION",
            "counts": counts, "currencies": currencies, "positions": results,
            "currency_totals_combined": False, "gross_costs_equals_net": True,
            "real_orders_sent": 0, "real_routes": "NOT_CALLED", "economic_edge_validated": False}
    if version_v2:
        report["administrative_voids"] = voids
    return report
