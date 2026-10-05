"""View adapters to existing facts and a coherent future SHADOW cut."""
from datetime import datetime
import heapq
from itertools import islice
import json
import os
import re
import stat
from pathlib import Path
import hashlib

from .projection import Page, UNKNOWN, age, freshness, json_object, contract_labels, validated_metrics, decimal_amount

SHADOW_ADAPTER_SCHEMA = "rc6.trader-dashboard.shadow-adapter.v2"
POLICY_LABELS = {"OFF_BASELINE": "OFF", "SHADOW_BASELINE": "SHADOW",
                 "APPROVED_DYNAMIC": "APPROVED_DYNAMIC", "BASELINE_FAIL_CLOSED": "BASELINE_FAIL_CLOSED"}


class _RankedRow:
    def __init__(self, key, sequence, row):
        self.order = (key, sequence)
        self.row = row

    def __lt__(self, other):
        # A max heap keeps only the requested sorted prefix.
        return self.order > other.order


class _PlannerPage:
    def __init__(self, offset, kind):
        self.offset, self.kind = offset, kind
        self.heap, self.total = [], 0

    def append(self, row):
        if self.kind == "events":
            # Clock strings are canonical UTC; invert rank using exact instant.
            from bs_instrument_contracts import utc_microseconds
            key = (-utc_microseconds(row["as_of"]),)
        else:
            key = ({"HOT": 0, "WARM": 1, "DISCOVERY": 2}.get(row.get("state"), 3),
                   row["rank"] if row.get("rank") is not None else 10**9, row.get("symbol", ""))
        item = _RankedRow(key, self.total, row)
        self.total += 1
        if len(self.heap) < self.offset + 10:
            heapq.heappush(self.heap, item)
        elif item.order < self.heap[0].order:
            heapq.heapreplace(self.heap, item)

    def page(self):
        return [item.row for item in sorted(self.heap, key=lambda item: item.order)[self.offset:]]


def policy_state(p):
    report = p.shadow["report"]
    policy = report.get("capacity_policy")
    if not isinstance(policy, dict) or freshness(report.get("as_of"), p.now, 30) != "FRESH":
        return UNKNOWN
    return POLICY_LABELS.get(policy.get("status"), UNKNOWN)


def scoped_lab_row(raw, payload, name, p):
    """Display explicit native lab fields; planner and lab identity orders differ."""
    row = dict(raw)
    inp = raw.get("input") if name == "entry_signal_lab" else raw.get("entry")
    inp = inp if isinstance(inp, dict) else {}
    identity = raw.get("identity") or inp.get("identity")
    if isinstance(identity, (list, tuple)) and len(identity) == 5:
        symbol, family, settlement, currency, market = identity
        row.update(symbol=symbol, family=family, settlement=settlement, currency=currency, market=market,
                   identity=json.dumps([symbol, family, market, currency, settlement], separators=(",", ":")))
    elif inp.get("symbol"):
        row.update(symbol=inp.get("symbol"), family=inp.get("asset_class"), settlement=inp.get("settlement"),
                   currency=inp.get("currency"), market=inp.get("market"))
        if all(row.get(k) for k in ("symbol", "family", "market", "currency", "settlement")):
            row["identity"] = json.dumps([row[k] for k in ("symbol", "family", "market", "currency", "settlement")], separators=(",", ":"))
    cohort = name == "entry_signal_lab" and "variant" in raw and "observations" in raw
    state = "PROSPECTIVE_LABEL_OBSERVED; EDGE_NO_VERIFICADO" if cohort else "SHADOW_PROSPECTIVE_EVALUATION; EDGE_NO_VERIFICADO"
    row.update(strategy=raw.get("strategy_id") or inp.get("strategy_id") or inp.get("strategy_version"),
               paper_id=inp.get("paper_id") or inp.get("id"),
               sample_size=raw.get("observations", raw.get("path_observations")),
               registry_version=raw.get("registry_sha256") or inp.get("registry_sha256"),
               state=raw.get("status") or state, mode="SHADOW", entry_authority=False,
               as_of=payload.get("as_of"), source=f"committed SHADOW · {name}",
               generation_id=p.shadow["pointer"]["generation_id"], adapter_schema=SHADOW_ADAPTER_SCHEMA,
               score_is_probability=False, promotion=False)
    # These are bounded prospective excursion observations, never continuous
    # MFE/MAE measurements or an OOS claim.
    fraction = decimal_amount(raw.get("hit_rate_gross"))
    row.update(mfe=UNKNOWN, mae=UNKNOWN, calibration=UNKNOWN,
               hit_rate=str(fraction * 100) if fraction is not None and 0 <= fraction <= 1 else None,
               auc=raw.get("auc_gross") if cohort else None, hour=raw.get("entry_hour_art"),
               reason=payload.get("status") or UNKNOWN)
    return row


def filtered_rows(p, rows):
    for row in rows:
        if any(p.filters.get(key) and str(row.get(key, "")).upper() != str(p.filters[key]).upper()
               for key in ("family", "currency", "market", "strategy", "settlement", "channel")):
            continue
        if p.filters.get("q") and str(p.filters["q"]).upper() not in str(row.get("symbol", "")).upper():
            continue
        yield row


def shadow_rows(p, kind):
    cut = p.shadow
    report = cut["report"]
    if not report:
        return Page("CURRENT.json → read_committed_generation (#466)", state=cut["state"], reason=cut["reason"])
    rows = []
    if kind in {"opportunities", "discovery", "tradeability", "exclusions", "events", "capacity"}:
        if kind != "capacity":
            rows = _PlannerPage(p.offset, kind)
        engines = report.get("engines", {})
        if not isinstance(engines, dict):
            return Page("committed SHADOW", reason="GENERATION_REPORT_SHAPE_UNAVAILABLE")
        for engine, plan in engines.items():
            if not isinstance(plan, dict):
                return Page("committed SHADOW", state="CONTRACT_ERROR", reason="PLANNER_ENGINE_SCHEMA_UNSUPPORTED")
            if not isinstance(plan.get("telemetry"), list) or any(not isinstance(row, dict) for row in plan["telemetry"]):
                return Page("committed SHADOW", state="CONTRACT_ERROR", reason="PLANNER_TELEMETRY_SCHEMA_UNSUPPORTED")
            if kind == "capacity":
                if not all(isinstance(plan.get(field), dict) for field in ("discovery", "capacity")):
                    return Page("committed SHADOW", state="CONTRACT_ERROR", reason="CAPACITY_PLAN_SCHEMA_UNSUPPORTED")
                discovery = json_object(plan.get("discovery", {}))
                capacity = json_object(plan.get("capacity", {}))
                open_verified = report.get("capacity_open_status") == "OPEN_EVIDENCE_VERIFIED" and freshness(report.get("as_of"), p.now, 30) == "FRESH"
                policy = report.get("capacity_policy")
                if not isinstance(policy, dict) or not isinstance(policy.get("baseline_limits"), dict):
                    return Page("committed SHADOW", state="CONTRACT_ERROR", reason="CAPACITY_POLICY_SCHEMA_UNSUPPORTED")
                revisits = [{"identity": value.get("identity"), "state": value.get("state"),
                             "planned_seconds": value.get("revisit_seconds"),
                             "achieved_seconds": value.get("achieved_revisit_seconds")}
                            for value in islice(plan["telemetry"], 10)]
                rows.append({**discovery, "engine": engine, "source": "committed SHADOW · engines.capacity",
                             "state": capacity.get("status", UNKNOWN), "as_of": report.get("as_of"),
                             "policy_state": policy_state(p), "policy_status": policy.get("status"),
                             "open_evidence": "OPEN_EVIDENCE_VERIFIED" if open_verified else UNKNOWN,
                             "safe_capacity": capacity.get("safe_limit") if open_verified else None,
                             "recommended_capacity": capacity.get("safe_limit"),
                             "baseline": policy["baseline_limits"].get(engine),
                             "endpoint_budgets": capacity.get("slots_by_endpoint"),
                             "opened_priority": plan.get("opened_priority"), "capacity": capacity,
                             "planned_revisit": discovery.get("revisit_seconds", revisits),
                             "achieved_revisit": discovery.get("achieved_revisit_seconds", revisits),
                             "exit_capacity": plan.get("exit_capacity_contract") or policy.get("exit_capacity_contract"),
                             "hot": plan.get("hot_count"), "warm": plan.get("warm_count"), "discovery": plan.get("discovery_count"),
                             "reason": "Capacidad OPEN no certificada al corte fresco" if not open_verified else None})
                continue
            if not isinstance(plan.get("telemetry"), list):
                return Page("committed SHADOW", state="CONTRACT_ERROR", reason="PLANNER_TELEMETRY_SCHEMA_UNSUPPORTED")
            for raw in plan.get("telemetry", []):
                if not isinstance(raw, dict):
                    continue
                identity = raw.get("identity")
                if (not isinstance(identity, (tuple, list)) or len(identity) != 5
                        or not all(isinstance(value, str) and value for value in identity)
                        or (raw.get("rank") is not None and type(raw["rank"]) is not int)):
                    return Page("committed SHADOW", state="CONTRACT_ERROR", reason="PLANNER_IDENTITY_OR_RANK_CONTRACT_INVALID")
                # Planner identity order is ticker/family/market/currency/settlement.
                symbol, family, market, currency, settlement = identity
                row = {**raw, "symbol": symbol, "family": family, "market": market, "currency": currency,
                       "settlement": settlement, "identity": json.dumps(identity, separators=(",", ":")),
                       "engine": engine, "entry_authority": False,
                       "as_of": raw.get("strategy_source_at") or raw.get("last_useful_observation_at"),
                       "signal": raw.get("signal_result", "NO_EVAL"), "economics": raw.get("economics_result", "NO_EVAL"),
                       "risk": raw.get("risk_result", "NOT_CALLED"), "warmup": raw.get("warmup_progress"),
                       "tradeability": raw.get("tradeability_score"), "reason": " · ".join(raw.get("rejection_reason", [])),
                       "freshness": freshness(raw.get("strategy_source_at") or raw.get("last_useful_observation_at"), p.now),
                       "generation_id": cut["pointer"]["generation_id"], "configuration_fingerprint": cut["manifest"]["configuration_fingerprint"]}
                if row["freshness"] != "FRESH":
                    row["freshness"] = freshness(row["as_of"], p.now)
                if kind == "exclusions" and not raw.get("rejection_reason"):
                    continue
                if kind == "events":
                    if not raw.get("promoted_at") and not raw.get("demoted_at"):
                        continue
                    from bs_instrument_contracts import utc_microseconds
                    clocks = [value for value in (raw.get("promoted_at"), raw.get("demoted_at")) if value]
                    if any(age(value, p.now) is None for value in clocks):
                        return Page("committed SHADOW", state="CONTRACT_ERROR", reason="PLANNER_EVENT_CLOCK_INVALID")
                    row["as_of"] = max(clocks, key=utc_microseconds)
                if p.filters.get("state") and row.get("state") != p.filters["state"]:
                    continue
                if any(p.filters.get(k) and str(row.get(k, "")).upper() != str(p.filters[k]).upper() for k in ("family", "currency", "market", "strategy", "settlement")):
                    continue
                if p.filters.get("q") and p.filters["q"].upper() not in symbol.upper():
                    continue
                rows.append(row)
        if kind != "capacity":
            return Page("CURRENT.json · committed SHADOW generation", rows.page(), rows.total, "AVAILABLE", p.offset,
                        report.get("as_of"), "SHADOW; entry_authority=false")
    else:
        name = {"families": "family_routing", "experiments": "entry_signal_lab", "signals": "entry_signal_lab",
                "exits": "economic_exit_lab", "event-risk": "event_risk", "strategies": "family_routing"}[kind]
        payload = report.get(name)
        if name in {"entry_signal_lab", "economic_exit_lab"}:
            expected_schema = "rc6.runtime-entry-signals.v1" if name == "entry_signal_lab" else "rc6.runtime-shadow-lab.v2"
            if not isinstance(payload, dict) or payload.get("schema") != expected_schema:
                return Page(f"committed SHADOW · {name}", state="CONTRACT_ERROR", reason="LAB_SCHEMA_UNSUPPORTED")
            member = {"signals": "cohorts", "experiments": "experiments", "exits": "entries"}[kind]
            if not isinstance(payload.get(member), list):
                return Page(f"committed SHADOW · {name}", state="CONTRACT_ERROR", reason="LAB_ROWS_CONTRACT_MISMATCH")
            if any(not isinstance(row, dict) for row in payload[member]):
                return Page(f"committed SHADOW · {name}", state="CONTRACT_ERROR", reason="LAB_ROW_SCHEMA_UNSUPPORTED")
            rows = (scoped_lab_row(row, payload, name, p) for row in payload[member])
            if kind == "signals" and not payload[member] and payload.get("experiments"):
                # Pending experiments are present evidence, with labels/OOS
                # explicitly absent until the prospective horizon completes.
                if not isinstance(payload["experiments"], list) or any(not isinstance(row, dict) for row in payload["experiments"]):
                    return Page(f"committed SHADOW · {name}", state="CONTRACT_ERROR", reason="LAB_ROWS_CONTRACT_MISMATCH")
                rows = (scoped_lab_row(row, payload, name, p) for row in payload["experiments"])
        elif name == "family_routing" and isinstance(payload, dict) and payload.get("schema") == "RC6_SHADOW_FAMILY_RUNTIME_V2":
            if not isinstance(payload.get("families"), list) or any(not isinstance(row, dict) or not row.get("family") for row in payload["families"]):
                return Page("committed SHADOW · family_routing", state="CONTRACT_ERROR", reason="FAMILY_ROUTING_SCHEMA_UNSUPPORTED")
            rows = ({**value, "state": value.get("status"), "entry_authority": False, "as_of": payload.get("as_of"),
                     "source": "committed SHADOW · family_routing", "mode": "SHADOW"}
                    for value in payload["families"])
        elif isinstance(payload, list):
            rows = ({**row, "entry_authority": False, "as_of": row.get("as_of") or report.get("as_of"),
                     "source": f"committed SHADOW · {name}", "mode": "SHADOW"} for row in payload if isinstance(row, dict))
        elif payload is None and name == "event_risk":
            return Page("committed SHADOW · event_risk", reason="EVENT_RISK_NOT_PUBLISHED")
        else:
            return Page(f"committed SHADOW · {name}", state="CONTRACT_ERROR", reason="SHADOW_DATASET_SCHEMA_UNSUPPORTED")
        selected, total = [], 0
        for row in filtered_rows(p, rows):
            if p.offset <= total < p.offset + 10:
                selected.append(row)
            total += 1
        return Page("CURRENT.json · committed SHADOW generation", selected, total, "AVAILABLE", p.offset,
                    report.get("as_of"), "SHADOW; entry_authority=false")
    return Page("CURRENT.json · committed SHADOW generation", rows[p.offset:p.offset + 10], len(rows),
                "AVAILABLE",
                p.offset, report.get("as_of"), "SHADOW; entry_authority=false")


def contracts(p):
    instruments = p.catalog()
    if not p.filters.get("identity") or len(instruments.rows) != 1:
        instruments.reason = "SELECT_EXACT_IDENTITY_FOR_CONTRACT"
        return instruments
    row = instruments.rows[0]
    s = p.store
    required = {"family", "ticker", "market", "currency", "settlement", "snapshot_id", "evidence_hash"}
    if not required <= s.columns("contract_evidence_v2_current") or "evidence_json" not in s.columns("contract_evidence_v2_snapshots"):
        return Page("contract_evidence_v2_current", reason="CONTRACT_EVIDENCE_UNAVAILABLE")
    keys = json.loads(row["identity"])
    columns = s.select("contract_evidence_v2_current", "c")
    evidence = s.select("contract_evidence_v2_snapshots", "v")
    page_rows = s.query(f"SELECT {columns},CASE WHEN length(CAST(v.evidence_json AS BLOB))<=32768 THEN v.evidence_json END evidence_json FROM contract_evidence_v2_current c JOIN contract_evidence_v2_snapshots v ON v.snapshot_id=c.snapshot_id AND v.evidence_hash=c.evidence_hash AND v.ticker=c.ticker AND v.family=c.family AND v.market=c.market AND v.currency=c.currency AND v.settlement=c.settlement WHERE c.ticker=? AND c.family=? AND c.market=? AND c.currency=? AND c.settlement=? ORDER BY c.source_class LIMIT 10", keys)
    for evidence_row in page_rows:
        payload = json_object(evidence_row.pop("evidence_json", None))
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        verified = hashlib.sha256(encoded).hexdigest() == evidence_row.get("evidence_hash")
        display_payload = validated_metrics(payload, p.now) if verified else {}
        evidence_row.update(symbol=row["symbol"], contract=display_payload if verified else None,
                            metadata=contract_labels(display_payload) if verified else {}, state=payload.get("state", UNKNOWN) if verified else UNKNOWN,
                            reason="DIGEST_VERIFIED_SOURCE_EVIDENCE" if verified else "CONTRACT_EVIDENCE_DIGEST_MISMATCH",
                            as_of=evidence_row.get("observed_at"), source=evidence_row.get("source_class"),
                            freshness=freshness(evidence_row.get("observed_at"), p.now, 14 * 86400),
                            entry_authority=UNKNOWN)
    return Page("contract_evidence_v2_current → exact immutable snapshot", page_rows, len(page_rows), "AVAILABLE")


def instrument_market(p):
    instruments = p.catalog()
    if not p.filters.get("identity") or len(instruments.rows) != 1:
        return Page("market_snapshots", reason="SELECT_EXACT_IDENTITY_FOR_MARKET")
    r = instruments.rows[0]
    cols = p.store.columns("market_snapshots")
    if not {"symbol", "asset_class", "market", "currency", "settlement", "id"} <= cols:
        return Page("market_snapshots", reason="EXACT_QUOTE_IDENTITY_UNAVAILABLE")
    page = p.store.page("market_snapshots", where="symbol=? AND asset_class=? AND market=? AND currency=? AND settlement=?",
                        params=(r["symbol"], r["family"], r["market"], r["currency"], r["settlement"]), order="id DESC", limit=1)
    for row in page.rows:
        row.update(family=row.get("asset_class"), as_of=row.get("book_at") or row.get("observed_at"),
                   provider_clock=row.get("book_at"), receipt_clock=row.get("received_at") or row.get("observed_at"),
                   freshness=freshness(row.get("book_at"), p.now), entry_authority=UNKNOWN)
    return page


def config(p):
    # Only allowlisted non-secret settings; no process-environment dumping.
    names = ("DASHBOARD_REFRESH_SECONDS", "SERVER_TIMEZONE", "PAPER_SCALPING_MODE", "PAPER_ECONOMIC_GATE_MODE",
             "PAPER_ACTIVE_SYMBOL_LIMIT", "PAPER_DAILY_LOSS_LIMIT_PCT", "PAPER_MAX_HOLD_MINUTES")
    rows = [{"setting": name, "value": os.getenv(name, UNKNOWN), "origin": "configured environment" if name in os.environ else UNKNOWN,
             "state": "CONFIGURED" if name in os.environ else UNKNOWN} for name in names]
    return Page("non-secret environment allowlist (configuration, not runtime authority)", rows, len(rows), "AVAILABLE")


def artifact(p, kind):
    files = {"scheduler": "scheduler/systemd_timers.json", "backups": "backup_status.json",
             "system": "ops/CURRENT_STATE_V2.json", "reports": "reports/postclose_review_latest.json"}
    name, root = files[kind], None
    if kind == "scheduler":
        target = Path(os.getenv("POROTA_SCHEDULER_STATE_PATH", "data/scheduler/systemd_timers.json"))
        name, root = target.name, target.parent
    payload, state = p.bounded_file(name, root=root)
    keys = {"scheduler": "timers", "backups": "stores", "system": "checks", "reports": "operations"}
    rows = payload.get(keys[kind], [])
    if isinstance(rows, dict):
        rows = [{"component": k, **v} if isinstance(v, dict) else {"component": k, "state": v} for k, v in rows.items()]
    if not isinstance(rows, list):
        rows = []
    result = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        # No arbitrary recursive payload dump: detail columns are allowlisted.
        result.append({**row, "as_of": row.get("as_of") or payload.get("captured_at") or payload.get("as_of"),
                       "source": files[kind], "freshness": freshness(row.get("as_of") or payload.get("captured_at") or payload.get("as_of"), p.now, 3600)})
    return Page(files[kind], result[p.offset:p.offset + 10], len(result) if state == "AVAILABLE" else None,
                state, p.offset, payload.get("as_of") or payload.get("captured_at"), state)


def logs(p):
    # Read only the tail of one fixed snapshot, never invoke journalctl/systemd.
    configured = os.getenv("POROTA_SHARED_LOG_DIR") or os.getenv("LOG_DIR")
    root = Path(configured) if configured else Path("data/logs") if Path("data/logs").is_dir() else p.store.path.parent / "logs"
    target = next((root / name for name in ("observer_runtime.log", "bot_runtime.log", "dashboard_runtime.log", "trading_bot.log", "observer.log") if (root / name).is_file() and not (root / name).is_symlink()), root / "observer_runtime.log")
    try:
        with os.fdopen(os.open(target, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW), "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                return Page("sanitized log snapshot", reason="LOG_NOT_REGULAR")
            stream.seek(0, 2)
            size = stream.tell()
            stream.seek(max(0, size - 64 * 1024))
            raw = stream.read(64 * 1024).decode("utf-8", "replace")
        lines = raw.splitlines()[-10:]
        rows = []
        for line in lines:
            clean = "[REDACTED] sensitive log line" if re.search(r"(?i)\b(authorization|bearer|token|api[_-]?key|secret|password|cookie|account[_-]?id|cbu|cuit)\b", line) else line
            rows.append({"line": clean[:1500], "source": "sanitized observer.log tail", "state": "HISTORICAL_LOG"})
        return Page("bounded sanitized log snapshot (10 lines / 64 KiB tail)", rows, len(rows), "AVAILABLE")
    except OSError:
        return Page("sanitized log snapshot", reason="LOG_NOT_PUBLISHED")


def family_summary(p):
    s = p.store
    if not {"instrument_type", "status", "can_simulate", "checked_at"} <= s.columns("candidate_identity_v2"):
        return Page("candidate_identity_v2", reason="CANONICAL_READINESS_NOT_PUBLISHED")
    count = s.query("SELECT COUNT(DISTINCT instrument_type) n FROM candidate_identity_v2")
    rows = s.query("""SELECT instrument_type family,COUNT(*) candidates,
        SUM(CASE WHEN can_simulate=1 AND upper(status)='AVAILABLE' THEN 1 ELSE 0 END) ready,
        MAX(checked_at) as_of FROM candidate_identity_v2 GROUP BY instrument_type
        ORDER BY instrument_type LIMIT 10 OFFSET ?""", (p.offset,))
    for row in rows:
        row.update(source="candidate_identity_v2", status=UNKNOWN, entry_authority=UNKNOWN,
                   strategy=UNKNOWN, lifecycle_owner=UNKNOWN, blocker="Routing de estrategia pendiente de reconciliación #466")
    return Page("candidate_identity_v2 · readiness (not strategy permission)", rows, count[0]["n"] if count else None, "AVAILABLE", p.offset)
