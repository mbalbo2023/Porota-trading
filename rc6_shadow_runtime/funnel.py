"""Durable prospective operational stages, independent of trading authority.

Catalog/state reaches, evaluations, distinct inputs and positions have different
denominators. A SHADOW reach never claims to have caused a factual fill. Native
spot fills and the dedicated futures cash/variation ledger are reconciled
separately; currencies are never added together.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from decimal import Decimal

from rc6_performance.common import canonical, digest, identity, number, stamp, decision_snapshot_phase
from rc6_performance.costs import realized_round_trip_cost
from rc6_performance.metrics import distribution
from .entry_signals import ART, _fingerprint, _json, _native_payload, _read_rows, _signal_input_fingerprint
from .serialization import encode_storage, decode_storage, SCHEMA as STORAGE_SCHEMA

SCHEMA = "rc6.prospective-operational-funnel.v1"
DISCOVERY_EVIDENCE_SCHEMA = "rc6.causal-discovery-evidence.v1"
STAGES = ("CATALOG_READY", "STRATEGY_ELIGIBLE", "TRADEABLE", "DISCOVERY_TOUCHED", "WARM", "HOT",
          "SIGNAL_EVALUATED", "SIGNAL_CANDIDATE", "ECONOMICS_PASS", "ECONOMICS_FAIL", "RISK_PASS", "RISK_FAIL",
          "PAPER_OPENED", "EXIT_REASON", "NET_PNL")
MAX_DETAILS = 512
MAX_IDENTITIES = 30000
MAX_EVENTS = 120000
MAX_POSITIONS = 4096
MAX_CHECKPOINT_BYTES = 32 * 1024**2
MAX_EXPANDED_CHECKPOINT_BYTES = 64 * 1024**2
TABLES = ("decision_evidence_snapshots", "paper_fills", "paper_family_lifecycle_events")


def encode_funnel_checkpoint(value):
    try:
        return encode_storage(value, durable_limit=MAX_CHECKPOINT_BYTES,
                              expansion_limit=MAX_EXPANDED_CHECKPOINT_BYTES)
    except ValueError as error:
        if "CAPACITY" in str(error): raise ValueError("FUNNEL_CHECKPOINT_CAPACITY_EXCEEDED") from error
        raise


def decode_funnel_checkpoint(value):
    return decode_storage(value, durable_limit=MAX_CHECKPOINT_BYTES,
                          expansion_limit=MAX_EXPANDED_CHECKPOINT_BYTES)


def planner_identity(parts):
    """Explicit adapter; planner and performance tuple orders differ."""
    if not isinstance(parts, (list, tuple)) or len(parts) != 5:
        raise ValueError("FUNNEL_EXACT_IDENTITY_REQUIRED")
    return identity(dict(zip(("symbol", "asset_class", "market", "currency", "settlement"), parts)))


def _native_identity(record):
    return identity(record)


def _strategy(engine, row=None):
    if engine == "SCALPING":
        return "SCALPING_BASELINE"
    if engine == "EQUITY_SPOT":
        return "SPOT_MOMENTUM_BASELINE"
    return (row or {}).get("strategy") or engine


def _event(checkpoint, *, ident, strategy, stage, event_at, as_of, event_id, channel,
           clock_basis, reasons=(), detail=None, session=None, strategy_version=None,
           configuration_fingerprint=None, registry_sha256=None):
    if stage not in STAGES:
        raise ValueError("FUNNEL_UNKNOWN_STAGE")
    clock = stamp(event_at)
    if clock > as_of:
        raise ValueError("FUNNEL_EVENT_CLOCK_FUTURE")
    if not strategy:
        raise ValueError("FUNNEL_NATIVE_STRATEGY_REQUIRED")
    session = session or clock.astimezone(ART).date().isoformat()
    version = strategy_version or "NO_VERIFICADO"
    config = configuration_fingerprint or "NO_VERIFICADO"
    registry = registry_sha256 or "NOT_APPLICABLE"
    key = digest([session, tuple(ident), strategy, version, config, registry, stage, channel, str(event_id)])
    if key in checkpoint["event_keys"]:
        return False
    if len(checkpoint["event_keys"]) >= MAX_EVENTS:
        raise ValueError("FUNNEL_EVENT_CAPACITY_EXCEEDED")
    entity = digest([session, tuple(ident), strategy, version, config, registry, channel])
    if entity not in checkpoint["reaches"] and len(checkpoint["reaches"]) >= MAX_IDENTITIES:
        raise ValueError("FUNNEL_IDENTITY_CAPACITY_EXCEEDED")
    checkpoint["event_keys"][key] = session
    reach = checkpoint["reaches"].setdefault(entity, {"session": session, "identity": list(ident),
        "family": ident[1], "currency": ident[3], "strategy_id": strategy, "channel": channel,
        "strategy_version": version, "configuration_fingerprint": config, "registry_sha256": registry,
        "stages": {}, "reasons": []})
    reach["stages"].setdefault(stage, clock.isoformat())
    reach["reasons"] = list(dict.fromkeys(reach["reasons"] + list(reasons)))[:64]
    group_key = digest([session, strategy, version, config, registry, tuple(ident), clock.astimezone(ART).hour, channel])
    group = checkpoint["cohorts"].setdefault(group_key, {"session": session, "strategy_id": strategy,
        "family": ident[1], "symbol": ident[0], "identity": list(ident), "settlement": ident[2], "market": ident[4],
        "strategy_version": version, "configuration_fingerprint": config, "registry_sha256": registry,
        "hour_art": clock.astimezone(ART).hour, "currency": ident[3],
        "channel": channel, "stages": {}, "reasons": {}, "net_pnl": "0", "gross_pnl": "0", "costs": "0"})
    group["stages"][stage] = group["stages"].get(stage, 0) + 1
    for reason in set(reasons):
        group["reasons"][str(reason)] = group["reasons"].get(str(reason), 0) + 1
    row = {"event_id": str(event_id), "identity": list(ident), "family": ident[1], "currency": ident[3],
        "strategy_id": strategy, "stage": stage, "event_at": clock.isoformat(), "session": session,
        "strategy_version": version, "configuration_fingerprint": config, "registry_sha256": registry,
        "channel": channel, "clock_basis": clock_basis, "reason_codes": list(reasons),
        "entry_authority": False, "detail": _json(detail or {})}
    row["evidence_sha256"] = digest(row)
    if stage == "NET_PNL":
        for target, field in (("net_pnl", "net"), ("gross_pnl", "gross"), ("costs", "costs")):
            group[target] = str(number(group[target]) + number(row["detail"][field]))
    checkpoint["details"].append(row)
    checkpoint["details"] = checkpoint["details"][-MAX_DETAILS:]
    checkpoint["events_recorded"] += 1
    return True


def _planner_stages(checkpoint, report, at):
    session = report.get("session") or at.astimezone(ART).date().isoformat()
    observation_stats, reasons = [], Counter()
    for engine, plan in report.get("engines", {}).items():
        for row in plan.get("telemetry", []):
            ident = planner_identity(row["identity"])
            strategy = _strategy(engine, row)
            pipeline = row.get("pipeline") or {}
            state = (plan.get("instruments") or {}).get(digest(row["identity"]), {})
            exclusion = list(row.get("rejection_reason") or [])
            reasons.update(exclusion)
            for stage in ("CATALOG_READY", "STRATEGY_ELIGIBLE", "TRADEABLE"):
                if pipeline.get(stage) is True:
                    _event(checkpoint, ident=ident, strategy=strategy, stage=stage, event_at=at, as_of=at,
                        event_id="first-prospective-reach", channel="UNIVERSE_SHADOW", session=session,
                        clock_basis="ACTUAL_RUNTIME_STATE_OBSERVATION; frozen input cutoff preserved",
                        reasons=exclusion, detail={"preopen_digest": plan.get("preopen_digest"),
                            "configuration_fingerprint": plan.get("configuration_fingerprint"),
                            "planner_strategy": row.get("strategy")})
            touched = state.get("last_touched_at")
            attempts = state.get("attempts", [])
            if touched:
                if stamp(touched) < stamp(checkpoint["started_at"]):
                    checkpoint["gaps"]["DISCOVERY_BEFORE_FUNNEL_WATERMARK"] = checkpoint["gaps"].get("DISCOVERY_BEFORE_FUNNEL_WATERMARK", 0) + 1
                else:
                    _event(checkpoint, ident=ident, strategy=strategy, stage="DISCOVERY_TOUCHED", event_at=touched,
                        as_of=at, event_id="first-native-touch", channel="UNIVERSE_SHADOW", session=session,
                        clock_basis="NATIVE_SOURCE_RECEIPT; selection intent is separate", reasons=exclusion,
                        detail={"source_at": state.get("source_at"), "source": state.get("source")})
            opened_priority = list(ident) in [[k[0], k[1], k[4], k[3], k[2]] for k in plan.get("opened_priority", [])]
            if row.get("state") in {"WARM", "HOT"} and not opened_priority:
                promotion = state.get("promoted_at") or row.get("promoted_at")
                if promotion and stamp(promotion) >= stamp(checkpoint["started_at"]):
                    _event(checkpoint, ident=ident, strategy=strategy, stage=row["state"], event_at=promotion,
                        as_of=at, event_id="first-state-reach", channel="UNIVERSE_SHADOW", session=session,
                        clock_basis="ACTUAL_PROSPECTIVE_PLANNER_TRANSITION", reasons=row.get("promotion_reasons", []),
                        detail={"warmup_complete_at": state.get("warmup_complete_at"), "opened_priority": False})
            if row.get("state") == "HOT" and opened_priority:
                checkpoint["gaps"].setdefault("OPENED_PRIORITY_NOT_WARMUP_EVIDENCE", 0)
            # Attempts use distinct original native source clocks. A repeated
            # receipt with an unchanged provider timestamp is not a new sample.
            clocks = {p[1] for p in attempts if len(p) >= 3}
            useful = {p[1] for p in attempts if len(p) >= 3 and p[2]}
            observation_stats.append({"identity": list(ident), "family": ident[1], "strategy_id": strategy,
                "currency": ident[3], "source": row.get("source"), "state": row.get("state"),
                "attempts": len(attempts), "distinct_observations": len(clocks), "useful_distinct_observations": len(useful),
                "useful_fraction": sum(bool(p[2]) for p in attempts if len(p) >= 3) / len(attempts) if attempts else None,
                "distinct_fraction": len(clocks) / len(attempts) if attempts else None,
                "discovery_age_seconds": row.get("discovery_age"), "age_is_lower_bound": row.get("discovery_age_lower_bound"),
                "planned_revisit_seconds": row.get("revisit_seconds"), "achieved_revisit_seconds": row.get("achieved_revisit_seconds"),
                "warmup_seconds": row.get("time_to_warmup_seconds"), "event_to_promotion_seconds": row.get("event_to_promotion_seconds"),
                "warmup_progress": row.get("warmup_progress"), "exclusion_reasons": exclusion,
                "missed_late_discovery": "NO_VERIFICADO_EXTERNAL_RUNTIME_ONLY"})
    # Specialized families stay explicit even if they have no equity plan.
    family = report.get("family_routing") or {}
    known = {(r["identity"][0], r["identity"][1], r["identity"][2], r["identity"][3], r["identity"][4])
             for plan in report.get("engines", {}).values() for r in plan.get("telemetry", [])}
    family_by_identity = {tuple(r.get("identity", [])): r for r in family.get("instruments", [])}
    for catalog in report.get("catalog_ready", []):
        raw = tuple(catalog.get(k) for k in ("ticker", "instrument_type", "market", "currency", "settlement"))
        ident = planner_identity(raw)
        if catalog.get("status") != "AVAILABLE" or not str(catalog.get("capability", "")).startswith("READY_PAPER"):
            raise ValueError("FUNNEL_EXPLICIT_READY_CATALOG_REQUIRED")
        if raw in known:
            continue
        instrument = family_by_identity.get(raw, {})
        _event(checkpoint, ident=ident, strategy=instrument.get("strategy") or instrument.get("engine") or
            ("futures-dlr-paper-v1" if ident[1] == "FUTUROS" else "SPECIALIZED_LIFECYCLE"), stage="CATALOG_READY",
            event_at=at, as_of=at, event_id="first-prospective-reach", channel="FAMILY_OBSERVE_ONLY", session=session,
            clock_basis="EXPLICIT_UNCHANGED_READY_CATALOG_OBSERVED_BY_RUNTIME", reasons=instrument.get("reason_codes", []),
            detail={"capability": catalog["capability"], "entry_authority": False})
    for instrument in family.get("instruments", []):
        try:
            raw = instrument["identity"]
            ident = planner_identity(raw)
            if tuple(raw) in known:
                continue
            if instrument.get("catalog_ready") is True or instrument.get("readiness") == "READY_PAPER":
                _event(checkpoint, ident=ident, strategy=instrument.get("strategy") or instrument.get("engine") or "SPECIALIZED_LIFECYCLE",
                    stage="CATALOG_READY", event_at=at, as_of=at, event_id="first-prospective-reach", channel="FAMILY_OBSERVE_ONLY",
                    session=session, clock_basis="ACTUAL_RUNTIME_CATALOG_OBSERVATION", reasons=instrument.get("reason_codes", []))
            reasons.update(instrument.get("reason_codes", []))
        except (ValueError, KeyError, TypeError):
            reasons["EXACT_IDENTITY_REQUIRED"] += 1
    return observation_stats, dict(reasons), family.get("families", [])


def measure_discovery_outcomes(evidence, *, as_of):
    """Accept only externally demonstrated causal discovery opportunities.

    A delayed quote or a later price increase does not create an opportunity.
    The contract requires a predeclared deadline, eligibility fixed before the
    event, native source/availability clocks and an explicit coverage contract for
    an absence claim. This consumption path needs no development after OPEN.
    """
    at = stamp(as_of)
    if not isinstance(evidence, list) or len(evidence) > 2000:
        raise ValueError("CAUSAL_DISCOVERY_EVIDENCE_BUDGET")
    measured, unverified, seen = [], [], set()
    for row in evidence:
        try:
            if (row.get("schema") != DISCOVERY_EVIDENCE_SCHEMA or
                    row.get("evidence_digest") != digest({k: v for k, v in row.items() if k != "evidence_digest"})):
                raise ValueError("CAUSAL_DISCOVERY_EVIDENCE_HASH_OR_SCHEMA_INVALID")
            ident = identity(row["identity"])
            source, available = stamp(row["event_at"]), stamp(row["available_at"])
            frozen, deadline = stamp(row["policy_registered_at"]), stamp(row["deadline_at"])
            if (not row.get("source") or not row.get("strategy_id") or not row.get("policy_version") or
                    row.get("strategy_eligible") is not True or not frozen < source <= available <= at or deadline <= available):
                raise ValueError("CAUSAL_DISCOVERY_CONTRACT_UNVERIFIED")
            key = digest([ident, row.get("strategy_id"), row["event_at"], row["policy_version"]])
            if key in seen:
                continue
            seen.add(key)
            touched = stamp(row["discovery_touched_at"]) if row.get("discovery_touched_at") else None
            if touched and not available <= touched <= at:
                raise ValueError("CAUSAL_DISCOVERY_TOUCH_CLOCK_INVALID")
            if touched:
                status = "LATE_DISCOVERY" if touched > deadline else "TIMELY_DISCOVERY"
            elif deadline <= at and row.get("continuous_coverage_verified") is True:
                start, through = stamp(row["coverage_started_at"]), stamp(row["coverage_through_at"])
                coverage_available = stamp(row["coverage_available_at"])
                if not start <= available <= deadline <= through <= coverage_available <= at or not row.get("coverage_digest"):
                    raise ValueError("CAUSAL_DISCOVERY_COVERAGE_CLOCK_OR_PROVENANCE_INVALID")
                status = "MISSED_DISCOVERY_WITH_VERIFIED_COVERAGE"
            else:
                raise ValueError("CAUSAL_DISCOVERY_COVERAGE_INCOMPLETE")
            measured.append({"identity": list(ident), "strategy_id": row.get("strategy_id"), "status": status,
                "event_at": source.isoformat(), "deadline_at": deadline.isoformat(),
                "discovery_touched_at": touched.isoformat() if touched else None,
                "delay_seconds": (touched - available).total_seconds() if touched else None,
                "source": row["source"], "evidence_digest": row["evidence_digest"], "policy_version": row["policy_version"]})
        except (ValueError, KeyError, TypeError) as exc:
            unverified.append({"reason": str(exc) if isinstance(exc, ValueError) else "CAUSAL_DISCOVERY_INPUT_UNAVAILABLE"})
    return {"status": "MEASURED_CAUSAL_SUBSET" if measured else "NO_VERIFICADO_EXTERNAL_RUNTIME_ONLY",
            "measured": measured, "unverified": unverified, "all_market_movements_detected": False,
            "evidence_basis": "EXPLICIT_EXTERNAL_NATIVE_EVENT_AND_COVERAGE_CONTRACT; no reconstructed opportunities"}


def _decision_stages(checkpoint, row, at):
    payload = _native_payload(row, at, require_signal=False)
    if decision_snapshot_phase(payload) == "ATOMIC_PAPER_ADMISSION":
        return
    decision = payload["decision"]
    runtime, inputs = payload["runtime"], payload.get("inputs_used") or {}
    ident = identity(payload["quote_used"])
    native_at = payload.get("decision_at") or runtime["decision_at"]
    if stamp(native_at) <= stamp(checkpoint["started_at"]):
        raise ValueError("DECISION_BEFORE_FUNNEL_WATERMARK")
    strategy = runtime["strategy_id"]
    reason = decision.get("reason_code") or inputs.get("reason_code") or "NATIVE_REASON_UNAVAILABLE"
    kwargs = dict(ident=ident, strategy=strategy, event_at=native_at, as_of=at, event_id=row["decision_key"],
                  channel="NATIVE_FACTUAL", clock_basis="NATIVE_DECISION_CLOCK", reasons=[reason],
                  strategy_version=runtime.get("strategy_version"),
                  configuration_fingerprint=runtime.get("configuration_fingerprint"))
    signal_at = payload.get("signal_at") or runtime.get("signal_at")
    if signal_at:
        added = _event(checkpoint, stage="SIGNAL_EVALUATED", detail={"signal_at": signal_at,
            "decision_key": row["decision_key"], "source_payload_sha256": row["payload_sha256"],
            "configuration_fingerprint": runtime["configuration_fingerprint"]}, **kwargs)
        if added:
            checkpoint["evaluations"] += 1
            # Exclude worker/decision clocks and outputs from native-input key.
            input_key = _signal_input_fingerprint(payload)
            if input_key not in checkpoint["distinct_inputs"]:
                checkpoint["distinct_inputs"][input_key] = stamp(native_at).astimezone(ART).date().isoformat()
    else:
        checkpoint["gaps"]["NATIVE_SIGNAL_CLOCK_UNAVAILABLE"] = checkpoint["gaps"].get("NATIVE_SIGNAL_CLOCK_UNAVAILABLE", 0) + 1
    action = decision.get("action")
    candidate = action in {"BUY", "BUY_CANDIDATE"} or action is None and decision.get("technical_gate") == "APPROVE"
    if candidate and signal_at:
        _event(checkpoint, stage="SIGNAL_CANDIDATE", detail={"action": action, "score": decision.get("score") or
            (inputs.get("candidate") or {}).get("score"), "score_is_probability": False}, **kwargs)
    economics = inputs.get("economics")
    scalping_not_evaluated = (strategy == "SCALPING_BASELINE" and isinstance(economics, dict) and
                             "modeled_roundtrip_fraction" not in economics and economics.get("execution_enabled") is False)
    if isinstance(economics, dict) and isinstance(economics.get("passed"), bool) and not scalping_not_evaluated:
        _event(checkpoint, stage="ECONOMICS_PASS" if economics["passed"] else "ECONOMICS_FAIL",
            detail={"gate_mode": inputs.get("economics_mode", "NATIVE"), "native_result": economics}, **kwargs)
    else:
        checkpoint["gaps"]["ECONOMICS_NOT_EVALUATED_OR_UNAVAILABLE"] = checkpoint["gaps"].get("ECONOMICS_NOT_EVALUATED_OR_UNAVAILABLE", 0) + 1
    gate = decision.get("patrimonial_gate")
    if gate in {"APPROVE", "BLOCKED", "REJECT", "VETO"}:
        _event(checkpoint, stage="RISK_PASS" if gate == "APPROVE" else "RISK_FAIL",
            detail={"native_gate": gate, "scope": "NATIVE_PATRIMONIAL_ADMISSION; includes risk/cash/contract gates",
                "daily_risk_inferred": False}, **kwargs)
    else:
        checkpoint["gaps"]["RISK_NOT_EVALUATED_OR_UNAVAILABLE"] = checkpoint["gaps"].get("RISK_NOT_EVALUATED_OR_UNAVAILABLE", 0) + 1
    return payload


def _position_strategy(row, *, futures=False):
    features = json.loads(row.get("metadata_json" if futures else "features_json") or "{}")
    if futures:
        features = features.get("features") or features
    lineage = features.get("performance_lineage") or {}
    return lineage.get("strategy_id") or ("futures-dlr-paper-v1" if futures else row.get("strategy_version")), features


def _spot_fills(checkpoint, source, at):
    for fill in source["rows"].get("paper_fills", []):
        if fill.get("_oversize"):
            raise ValueError("FUNNEL_FILL_ROW_OVERSIZE")
        paper_id = fill.get("paper_id")
        position = source["positions"].get(paper_id)
        if not position:
            raise ValueError("FUNNEL_NATIVE_POSITION_UNAVAILABLE")
        ident = identity(position)
        if ident[1] == "FUTUROS":
            raise ValueError("FUTURES_CANNOT_USE_SPOT_FUNNEL_LEDGER")
        native_at = stamp(fill["filled_at"])
        if not stamp(checkpoint["started_at"]) < native_at <= at:
            checkpoint["gaps"]["FILL_BEFORE_WATERMARK_OR_FUTURE"] = checkpoint["gaps"].get("FILL_BEFORE_WATERMARK_OR_FUTURE", 0) + 1
            checkpoint["cursors"]["paper_fills"] = fill["_rowid"]
            continue
        strategy, features = _position_strategy(position)
        if not strategy:
            raise ValueError("FUNNEL_NATIVE_POSITION_STRATEGY_UNAVAILABLE")
        if paper_id not in checkpoint["spot_positions"] and len(checkpoint["spot_positions"]) >= MAX_POSITIONS:
            raise ValueError("FUNNEL_POSITION_CAPACITY_EXCEEDED")
        record = checkpoint["spot_positions"].setdefault(paper_id, {"identity": list(ident), "strategy_id": strategy, "fills": []})
        if tuple(record["identity"]) != ident:
            raise ValueError("FUNNEL_POSITION_IDENTITY_MISMATCH")
        if not any(f["id"] == fill["id"] for f in record["fills"]):
            record["fills"].append({k: v for k, v in fill.items() if not k.startswith("_")})
        lineage = features.get("performance_lineage") or {}
        kwargs = dict(ident=ident, strategy=strategy, as_of=at, event_id=paper_id, channel="NATIVE_FACTUAL", reasons=[],
            strategy_version=lineage.get("strategy_version") or position.get("strategy_version"),
            configuration_fingerprint=lineage.get("configuration_fingerprint"))
        if fill["side"] == "BUY_SIMULATED":
            _event(checkpoint, stage="PAPER_OPENED", event_at=native_at, clock_basis="NATIVE_DURABLE_BUY_FILL",
                detail={"paper_id": paper_id, "fill_id": fill["id"], "opened_at": position["opened_at"],
                    "upstream_inferred": False}, **kwargs)
        elif fill["side"] != "SELL_SIMULATED":
            raise ValueError("FUNNEL_SIMULATED_FILL_REQUIRED")
        if position["status"] == "CLOSED" and fill["side"] == "SELL_SIMULATED":
            closed = stamp(position["closed_at"])
            if closed > at or closed < stamp(position["opened_at"]):
                raise ValueError("FUNNEL_CLOSE_CLOCK_INVALID")
            _event(checkpoint, stage="EXIT_REASON", event_at=closed, clock_basis="NATIVE_TERMINAL_SPOT_LEDGER",
                detail={"paper_id": paper_id, "exit_reason": position["close_reason"],
                    "last_exit_fill_at": native_at.isoformat()}, **kwargs)
            factor = features.get("contract_cash_multiplier", 1 if ident[1] in {"ACCIONES", "CEDEARS", "ETFS"} else None)
            try:
                result = realized_round_trip_cost({**position, "contract_cash_multiplier": factor}, record["fills"])
                _event(checkpoint, stage="NET_PNL", event_at=closed, clock_basis="NATIVE_ALL_FILLS_RECONCILED",
                    detail={"paper_id": paper_id, **result}, **kwargs)
                record["reconciled"] = True
                record["completed_at"] = closed.isoformat()
            except ValueError as exc:
                record["net_status"] = "NO_VERIFICADO"
                record["net_reason"] = str(exc)
        checkpoint["cursors"]["paper_fills"] = fill["_rowid"]


def _future_events(checkpoint, source, at):
    from rc6_paper_family_lifecycle import future_position_contract
    for event in source["rows"].get("paper_family_lifecycle_events", []):
        if event.get("_oversize"):
            raise ValueError("FUNNEL_LIFECYCLE_ROW_OVERSIZE")
        life = event["lifecycle_id"]
        row = source["futures"].get(life)
        if event["family"] != "FUTUROS":
            checkpoint["gaps"]["SPECIALIZED_NONFUTURES_EVENT_NO_EXACT_INPUT_CONTRACT"] = checkpoint["gaps"].get("SPECIALIZED_NONFUTURES_EVENT_NO_EXACT_INPUT_CONTRACT", 0) + 1
            checkpoint["cursors"]["paper_family_lifecycle_events"] = event["_rowid"]
            continue
        if row is None:
            raise ValueError("FUNNEL_FUTURES_POSITION_UNAVAILABLE")
        contract = future_position_contract(row)
        ident = identity({**row, "asset_class": "FUTUROS"})
        ledger = source["future_lifecycles"].get(life)
        if not ledger or (ledger["instrument"], ledger["currency"], ledger["family"]) != (contract.symbol, contract.currency, contract.family):
            raise ValueError("FUNNEL_FUTURES_EVENT_IDENTITY_MISMATCH")
        if (contract.symbol, contract.currency, contract.market, contract.settlement) != (ident[0], ident[3], ident[4], ident[2]):
            raise ValueError("FUNNEL_FUTURES_CONTRACT_IDENTITY_MISMATCH")
        occurred = stamp(event["occurred_at"])
        if not stamp(checkpoint["started_at"]) < occurred <= at:
            checkpoint["gaps"]["FUTURES_EVENT_BEFORE_WATERMARK_OR_FUTURE"] = checkpoint["gaps"].get("FUTURES_EVENT_BEFORE_WATERMARK_OR_FUTURE", 0) + 1
            checkpoint["cursors"]["paper_family_lifecycle_events"] = event["_rowid"]
            continue
        detail = json.loads(event["detail_json"])
        if detail.get("real_routes_used") or detail.get("mode") != "PRODUCTION_PAPER" or detail.get("execution") != "SIMULATION":
            raise ValueError("FUNNEL_FUTURES_PAPER_EVENT_REQUIRED")
        metadata = json.loads(row["metadata_json"])
        if (detail.get("metadata_source") != contract.metadata_source or
                (event["to_state"] in {"OPEN", "MARGIN_RESERVED"} and
                 (detail.get("contract_snapshot_sha256") != metadata["contract_snapshot_sha256"] or
                  digest(detail.get("financial_contract")) != digest(metadata["financial_contract"])))):
            raise ValueError("FUNNEL_FUTURES_EVENT_CONTRACT_PROVENANCE_MISMATCH")
        strategy, features = _position_strategy(row, futures=True)
        if life not in checkpoint["future_positions"] and len(checkpoint["future_positions"]) >= MAX_POSITIONS:
            raise ValueError("FUNNEL_FUTURES_CAPACITY_EXCEEDED")
        position = checkpoint["future_positions"].setdefault(life, {"identity": list(ident), "events": [], "opened": False})
        if tuple(position["identity"]) != ident:
            raise ValueError("FUNNEL_FUTURES_POSITION_IDENTITY_MISMATCH")
        if not any(e["event_id"] == event["event_id"] for e in position["events"]):
            position["events"].append({"event_id": event["event_id"], "to_state": event["to_state"],
                "amount": event["amount"], "occurred_at": event["occurred_at"], "detail": detail})
        lineage = features.get("performance_lineage") or {}
        kwargs = dict(ident=ident, strategy=strategy, as_of=at, event_id=life, channel="NATIVE_FACTUAL", reasons=[],
            strategy_version=lineage.get("strategy_version"),
            configuration_fingerprint=lineage.get("configuration_fingerprint"))
        if event["to_state"] == "OPEN":
            position["opened"] = True
            _event(checkpoint, stage="PAPER_OPENED", event_at=occurred, clock_basis="NATIVE_SPECIALIZED_FUTURES_OPEN",
                detail={"lifecycle_id": life, "contract_sha256": detail.get("contract_snapshot_sha256"),
                    "full_notional_reserve": row["margin_reserved"], "upstream_inferred": False}, **kwargs)
        if event["to_state"] in {"CLOSE", "EXPIRY"}:
            if not row["closed_at"] or stamp(row["closed_at"]) != occurred or row["status"] != "CLOSED":
                raise ValueError("FUNNEL_FUTURES_TERMINAL_CLOCK_MISMATCH")
            _event(checkpoint, stage="EXIT_REASON", event_at=occurred, clock_basis="NATIVE_SPECIALIZED_FUTURES_TERMINAL",
                detail={"lifecycle_id": life, "exit_reason": detail.get("reason"), "terminal_state": event["to_state"]}, **kwargs)
            if position["opened"]:
                qty, factor = number(row["quantity"], positive=True), number(row["cash_multiplier"], positive=True)
                gross = (number(detail["exit_price"], positive=True) - number(row["entry_price"], positive=True)) * qty * factor
                costs = number(row["entry_cost"], nonnegative=True) + number(row["exit_cost"], nonnegative=True)
                net = gross - costs
                totals = sum((number(e["amount"]) for e in position["events"]), Decimal(0))
                reserves = [e for e in position["events"] if e["to_state"] == "MARGIN_RESERVED"]
                terminal = [e for e in position["events"] if e["to_state"] in {"CLOSE", "EXPIRY"}]
                if (len(reserves) != 1 or len(terminal) != 1 or
                        number(detail["gross_realized_pnl"]) != gross or number(detail["net_realized_pnl"]) != net or
                        number(row["variation_realized"]) != gross or abs(totals - net) > Decimal(".01") or
                        ledger["state"] != event["to_state"] or stamp(ledger["updated_at"]) != occurred or
                        abs(number(ledger["ledger_total"]) - net) > Decimal(".01")):
                    raise ValueError("FUNNEL_FUTURES_CASH_VARIATION_RECONCILIATION")
                _event(checkpoint, stage="NET_PNL", event_at=occurred, clock_basis="NATIVE_FUTURES_VARIATION_RESERVE_CLOSE_CASH_RECONCILED",
                    detail={"lifecycle_id": life, "gross": str(gross), "costs": str(costs), "net": str(net),
                        "currency": ident[3], "cash_multiplier": str(factor), "cash_effect": str(totals),
                        "reserve_released_once": True, "variation_double_counted": False, "spot_ledger_used": False}, **kwargs)
                position["reconciled"] = True
                position["completed_at"] = occurred.isoformat()
            else:
                position["net_status"] = "NO_VERIFICADO_EXISTING_ENTRY_BEFORE_WATERMARK"
        checkpoint["cursors"]["paper_family_lifecycle_events"] = event["_rowid"]


def _shadow_entries(checkpoint, report, at):
    for record in (report or {}).get("experiments", []):
        inp = record["input"]
        ident = tuple(inp["identity"])
        if not stamp(checkpoint["started_at"]) < stamp(inp["decision_at"]) <= at:
            continue
        for name, output in record["variants"].items():
            result = output["decision"]
            if result.get("status") != "EVALUATED":
                continue
            kwargs = dict(ident=ident, strategy=inp["strategy_id"], event_at=inp["decision_at"], as_of=at,
                event_id=record["decision_key"] + ":" + name, channel="SHADOW_ENTRY_EXPERIMENT",
                clock_basis="SAME_SNAPSHOT_NATIVE_INPUT_CLOCK; no factual causality", reasons=[result["reason"]],
                detail={"variant": name, "registry_sha256": inp["registry_sha256"], "input_sha256": output["input_sha256"],
                    "entry_authority": False, "score_is_probability": False},
                strategy_version=inp.get("strategy_version"),
                configuration_fingerprint=inp.get("native_configuration_fingerprint"), registry_sha256=inp["registry_sha256"])
            _event(checkpoint, stage="SIGNAL_EVALUATED", **kwargs)
            if result.get("accepted") is True:
                _event(checkpoint, stage="SIGNAL_CANDIDATE", **kwargs)


def evaluate_runtime_funnel(database, *, as_of, planner_report, entry_signal_report=None,
                            exit_lab_report=None, previous=None, row_limit=500, return_encoded_checkpoint=False):
    """Return report/state; caller owns the existing atomic evidence writer."""
    at = stamp(as_of)
    stored_previous = isinstance(previous, dict) and previous.get("schema") == STORAGE_SCHEMA
    previous = decode_funnel_checkpoint(previous)
    if not isinstance(planner_report, dict) or (planner_report.get("real_orders_sent", 0) != 0 or
            planner_report.get("real_routes", "NOT_CALLED") != "NOT_CALLED"):
        raise ValueError("FUNNEL_SHADOW_PAPER_REPORT_REQUIRED")
    fingerprint = digest({"schema": SCHEMA, "bounds": [row_limit, MAX_DETAILS, MAX_IDENTITIES, MAX_EVENTS, MAX_POSITIONS],
        "native_stages": STAGES, "identity_order": "symbol,asset_class,settlement,currency,market",
        "cohort_contract": "EXACT_IDENTITY_NATIVE_VERSION_CONFIGURATION_AND_FROZEN_REGISTRY",
        "distinct_input_contract": "PROVIDER_CLOCK_VALUES; receipt and decision clock churn excluded"})
    invalidation = None
    if previous:
        if not stored_previous:
            encode_funnel_checkpoint(previous)
        if (previous.get("schema") != SCHEMA or
                previous.get("checkpoint_sha256") != _fingerprint(previous)):
            invalidation = "FUNNEL_CHECKPOINT_INVALID"
        elif previous.get("configuration_fingerprint") != fingerprint:
            invalidation = "FUNNEL_CONFIGURATION_CHANGED"
        elif stamp(previous["last_as_of"]) > at:
            raise ValueError("FUNNEL_CHECKPOINT_FROM_FUTURE")
    prior = None if invalidation else previous
    source = _read_rows(database, as_of=at, tables=TABLES, cursors=(prior or {}).get("cursors"),
        source_key=(prior or {}).get("source_key"), row_limit=row_limit, join_positions=True)
    if source["bootstrap"]:
        if prior:
            invalidation = "FUNNEL_SOURCE_CHANGED_OR_CURSOR_REVERSED"
        checkpoint = {"schema": SCHEMA, "source_key": source["source_key"], "started_at": at.isoformat(),
            "last_as_of": at.isoformat(), "configuration_fingerprint": fingerprint, "cursors": source["tails"],
            "event_keys": {}, "reaches": {}, "cohorts": {}, "details": [], "events_recorded": 0,
            "spot_positions": {}, "future_positions": {}, "gaps": {}, "evaluations": 0, "distinct_inputs": {}}
        status = "START_AT_CURRENT_TAIL"
    else:
        # A verified codec decode owns a fresh JSON graph. Reusing that graph
        # cannot mutate the caller's encoded checkpoint; plain legacy state
        # keeps its original defensive/canonical copy contract.
        checkpoint = prior if stored_previous else _json(prior)
        status = "PROSPECTIVE_OPERATIONAL_FUNNEL_EVALUATED"
        for row in source["rows"].get("decision_evidence_snapshots", []):
            try:
                _decision_stages(checkpoint, row, at)
            except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
                reason = str(exc) if isinstance(exc, ValueError) else "NATIVE_FUNNEL_INPUT_INVALID"
                if reason in {"IMMUTABLE_DECISION_HASH_MISMATCH", "IMMUTABLE_DECISION_KEY_MISMATCH"}:
                    raise
                checkpoint["gaps"][reason] = checkpoint["gaps"].get(reason, 0) + 1
            checkpoint["cursors"]["decision_evidence_snapshots"] = row["_rowid"]
        _spot_fills(checkpoint, source, at)
        _future_events(checkpoint, source, at)
        _shadow_entries(checkpoint, entry_signal_report, at)
    observations, exclusions, families = _planner_stages(checkpoint, planner_report, at)
    checkpoint["last_as_of"] = at.isoformat()
    # Retain two complete session aggregates; raw detailed evidence has its
    # independent cap. Never claim full lifetime coverage after retention.
    sessions = sorted(set(checkpoint["event_keys"].values()))
    retained = set(sessions[-2:])
    for name in ("event_keys", "distinct_inputs"):
        checkpoint[name] = {k: v for k, v in checkpoint[name].items() if v in retained}
    for name in ("reaches", "cohorts"):
        checkpoint[name] = {k: v for k, v in checkpoint[name].items() if v["session"] in retained}
    for name in ("spot_positions", "future_positions"):
        checkpoint[name] = {k: v for k, v in checkpoint[name].items() if not v.get("completed_at") or
            stamp(v["completed_at"]).astimezone(ART).date().isoformat() in retained}
    checkpoint["details"] = [r for r in checkpoint["details"] if r["session"] in retained]
    checkpoint["checkpoint_sha256"] = _fingerprint(checkpoint)
    stored_checkpoint = encode_funnel_checkpoint(checkpoint)
    cohorts = list(checkpoint["cohorts"].values())
    totals = defaultdict(lambda: {"stages": Counter(), "net_pnl": Decimal(0), "gross_pnl": Decimal(0), "costs": Decimal(0)})
    for group in cohorts:
        key = (group["currency"], group["channel"])
        totals[key]["stages"].update(group["stages"])
        for name in ("net_pnl", "gross_pnl", "costs"):
            totals[key][name] += number(group[name])
    discovery = defaultdict(list)
    for observation in observations:
        if observation["discovery_age_seconds"] is not None:
            discovery[(observation["family"], observation["strategy_id"], observation["currency"])].append(observation["discovery_age_seconds"])
    report = {"schema": SCHEMA, "as_of": at.isoformat(), "status": status, "mode": "SHADOW",
        "entry_authority": False, "decision_effect": "NONE", "real_orders_sent": 0, "real_routes": "NOT_CALLED",
        "real_order_routes": [], "provider_requests": 0, "source_database_effect": "READ_ONLY", "legacy_history_backfill": False,
        "configuration_fingerprint": fingerprint, "checkpoint_sha256": checkpoint["checkpoint_sha256"],
        "checkpoint_invalidation": invalidation, "native_stage_order": list(STAGES), "sessions_retained": sorted(retained),
        "source_read_truncated": source["truncated"], "missing_source_tables": source["missing_tables"],
        "denominators": {"native_evaluations": sum(g["stages"].get("SIGNAL_EVALUATED", 0) for g in cohorts if g["channel"] == "NATIVE_FACTUAL"),
            "distinct_frozen_native_inputs": len(checkpoint["distinct_inputs"]), "evaluation_scope": "retained sessions",
            "distinct_input_basis": "NATIVE_MARKET_VALUES_AND_PROVIDER_CLOCKS; receipt/decision churn excluded",
            "lifetime_evaluations_since_watermark": checkpoint["evaluations"],
            "stage_identity_reaches": len(checkpoint["reaches"]), "events_recorded": checkpoint["events_recorded"],
            "independent_opportunities": "NOT_CLAIMED", "sample_is_independent": False},
        "by_currency_channel": [{"currency": key[0], "channel": key[1], **dict(value)} for key, value in sorted(totals.items())],
        "cohorts": cohorts, "state_reaches": list(checkpoint["reaches"].values()), "lineage": checkpoint["details"],
        "source_gaps": checkpoint["gaps"], "exclusions": exclusions, "family_policies": families,
        "observation_metrics": observations,
        "discovery_age": [{"family": k[0], "strategy_id": k[1], "currency": k[2], **distribution(v)} for k, v in sorted(discovery.items())],
        "detail_scope": {"retained_details": len(checkpoint["details"]), "maximum_details": MAX_DETAILS,
            "details_truncated": checkpoint["events_recorded"] > len(checkpoint["details"]),
            "session_aggregates_preserved": True, "retention_sessions": 2},
        "unreconciled_spot_positions": [{"paper_id": key, "reason": row.get("net_reason", "NATIVE_TERMINAL_FILL_PENDING")}
            for key, row in checkpoint["spot_positions"].items() if not row.get("reconciled")],
        "unreconciled_futures_positions": [{"lifecycle_id": key, "reason": row.get("net_status", "NATIVE_TERMINAL_EVENT_PENDING")}
            for key, row in checkpoint["future_positions"].items() if not row.get("reconciled")],
        "exit_lab_status": (exit_lab_report or {}).get("status", "NO_VERIFICADO"),
        "late_missed_discovery": measure_discovery_outcomes(planner_report.get("causal_discovery_evidence", []), as_of=at),
        "shadow_to_factual_causality": "NOT_CLAIMED",
        "currencies_added_together": False, "factual_exit_policy_effect": "NONE"}
    return (report if return_encoded_checkpoint else _json(report)), stored_checkpoint if return_encoded_checkpoint else checkpoint
