"""View adapters to existing facts and a coherent future SHADOW cut."""
from datetime import datetime, timezone
import json
import os
import re
import stat
from pathlib import Path
import hashlib
from co_market_sessions_hf6 import byma_paper_spot_phase

from .projection import Page, UNKNOWN, age, freshness, json_object, contract_labels, validated_metrics


def current_open_capacity(p, plan, capacity):
    """Display only an unexpired SHADOW recommendation from the live OPEN cut."""
    if (plan.get("phase") != "OPEN" or byma_paper_spot_phase(p.now) != "OPEN"
            or capacity.get("status") != "SHADOW_RECOMMENDATION"
            or capacity.get("mode") != "SHADOW" or capacity.get("real_orders_sent") != 0
            or capacity.get("real_routes") != "NOT_CALLED"
            or capacity.get("production_limit_modified") is not False):
        return False
    limit = capacity.get("safe_limit")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        return False
    if any(not isinstance(capacity.get(key), str) or not re.fullmatch(r"[0-9a-f]{64}", capacity[key])
           for key in ("evidence_digest", "configuration_fingerprint")):
        return False
    if freshness(capacity.get("validated_as_of"), p.now, 120) != "FRESH":
        return False
    try:
        expires = datetime.fromisoformat(str(capacity.get("expires_at")).replace("Z", "+00:00"))
        return expires.tzinfo is not None and expires.utcoffset() is not None and p.now < expires
    except (TypeError, ValueError, OverflowError):
        return False


def shadow_rows(p, kind):
    cut = p.shadow
    if not p.shadow_fresh:
        return Page("CURRENT.json → committed SHADOW generation", state=UNKNOWN,
                    reason=cut.get("reason") or "SHADOW_CUT_NOT_FRESH")
    report = cut["report"]
    if not report:
        return Page("CURRENT.json → read_committed_generation (#466)", state=cut["state"], reason=cut["reason"])
    rows = []
    if kind in {"opportunities", "discovery", "tradeability", "exclusions", "events", "capacity"}:
        engines = report.get("engines", {})
        if not isinstance(engines, dict):
            return Page("committed SHADOW", reason="GENERATION_REPORT_SHAPE_UNAVAILABLE")
        for engine, plan in engines.items():
            if not isinstance(plan, dict):
                continue
            if kind == "capacity":
                discovery = json_object(plan.get("discovery", {}))
                capacity = json_object(plan.get("capacity", {}))
                open_verified = current_open_capacity(p, plan, capacity)
                # The detail must not expose a historical limit as if it were
                # usable now merely because the committed report retains it.
                capacity_detail = (capacity if open_verified else {
                    key: capacity.get(key) for key in ("status", "reason_codes", "evidence_digest",
                                                   "validated_as_of", "expires_at") if key in capacity})
                rows.append({**discovery, "engine": engine, "source": "committed SHADOW · engines.capacity",
                             "state": "SHADOW_RECOMMENDATION" if open_verified else UNKNOWN, "as_of": report.get("as_of"),
                             "safe_capacity": capacity.get("safe_limit") if open_verified else None,
                             "baseline": report.get("capacity_policy", {}).get("baseline"),
                             "endpoint_budgets": capacity.get("slots_by_endpoint") if open_verified else None,
                             "opened_priority": plan.get("opened_priority"), "capacity": capacity_detail,
                             "planned_revisit": None, "achieved_revisit": None,
                             "hot": plan.get("hot_count"), "warm": plan.get("warm_count"), "discovery": plan.get("discovery_count"),
                             "reason": ("Recomendación SHADOW medida; política de admisión independiente" if open_verified
                                        else "Capacidad OPEN vigente no verificada")})
                continue
            for raw in plan.get("telemetry", []):
                if not isinstance(raw, dict):
                    continue
                identity = raw.get("identity")
                if not isinstance(identity, (tuple, list)) or len(identity) != 5:
                    continue
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
                    row["as_of"] = max(str(raw.get("promoted_at") or ""), str(raw.get("demoted_at") or ""))
                if p.filters.get("state") and row.get("state") != p.filters["state"]:
                    continue
                if any(p.filters.get(k) and str(row.get(k, "")).upper() != str(p.filters[k]).upper() for k in ("family", "currency", "market", "strategy", "settlement")):
                    continue
                if p.filters.get("q") and p.filters["q"].upper() not in symbol.upper():
                    continue
                rows.append(row)
        if kind == "events":
            rows.sort(key=lambda row: row.get("as_of") or "", reverse=True)
        elif kind not in {"capacity"}:
            rows.sort(key=lambda row: ({"HOT": 0, "WARM": 1, "DISCOVERY": 2}.get(row.get("state"), 3), row.get("rank") or 10**9, row.get("symbol", "")))
    elif kind in {"signals", "experiments"}:
        entry = report.get("entry_signal_lab")
        if not isinstance(entry, dict):
            return Page("CURRENT.json · entry_signal_lab", state=UNKNOWN,
                        reason="ENTRY_SIGNAL_SUMMARY_NOT_PUBLISHED")
        cohorts = entry.get("cohorts") if isinstance(entry.get("cohorts"), list) else []
        rows = [{"family": row.get("family"), "currency": row.get("currency"),
                 "strategy": row.get("strategy_id"), "registry_version": row.get("registry_sha256"),
                 "state": UNKNOWN, "sample_size": row.get("observations"), "score": None,
                 "entry_authority": False, "as_of": entry.get("as_of"),
                 "observations": row.get("observations"),
                 "reason": "Cohorte SHADOW medida; score no es probabilidad ni edge validado",
                 "cohort_total": entry.get("cohort_total"),
                 "cohorts_truncated": entry.get("cohorts_truncated")}
                for row in cohorts if isinstance(row, dict)]
        return Page("CURRENT.json · entry_signal_lab · top 10 cohortes",
                    rows[p.offset:p.offset + 10], len(rows),
                    "AVAILABLE" if rows else UNKNOWN,
                    p.offset, entry.get("as_of"),
                    "Publicadas " + str(len(rows)) + "/" + str(entry.get("cohort_total")) +
                    " cohortes; registros de evaluador=" + str(entry.get("registry_total")) +
                    "; decisiones nativas observadas=" + str(entry.get("native_decisions_observed")))
    else:
        name = {"families": "family_routing", "experiments": "entry_signal_lab", "signals": "entry_signal_lab",
                "exits": "economic_exit_lab", "event-risk": "event_risk", "strategies": "family_routing"}[kind]
        payload = report.get(name)
        if isinstance(payload, dict):
            payload = payload.get("rows") or payload.get("families") or payload.get("evaluations") or payload.get("registry")
        if isinstance(payload, list):
            rows = [{**row, "entry_authority": False, "as_of": row.get("as_of") or report.get("as_of"),
                     "source": f"committed SHADOW · {name}", "mode": "SHADOW"} for row in payload if isinstance(row, dict)]
    return Page("CURRENT.json · committed SHADOW generation", rows[p.offset:p.offset + 10], len(rows),
                "AVAILABLE" if rows or kind in {"opportunities", "events"} else UNKNOWN,
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
    errors = len(s.errors)
    page_rows = s.query(f"SELECT {columns},CASE WHEN length(CAST(v.evidence_json AS BLOB))<=32768 THEN v.evidence_json END evidence_json FROM contract_evidence_v2_current c JOIN contract_evidence_v2_snapshots v ON v.snapshot_id=c.snapshot_id AND v.evidence_hash=c.evidence_hash AND v.ticker=c.ticker AND v.family=c.family AND v.market=c.market AND v.currency=c.currency AND v.settlement=c.settlement WHERE c.ticker=? AND c.family=? AND c.market=? AND c.currency=? AND c.settlement=? ORDER BY c.source_class LIMIT 10", keys)
    if len(s.errors) != errors:
        return Page("contract_evidence_v2_current", reason="CONTRACT_EVIDENCE_READ_ERROR")
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
                   freshness=freshness(row.get("book_at"), p.now, 120),
                   book_freshness=freshness(row.get('book_at'),p.now,120),
                   trade_freshness=freshness(row.get('trade_at'),p.now,120),
                   receipt_freshness=freshness(row.get('received_at'),p.now,120),entry_authority=UNKNOWN)
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
    captured = (payload.get("recorded_at") if kind == "scheduler" else
                payload.get("captured_at") or payload.get("recorded_at") or payload.get("as_of"))
    # One-minute exporter timer: three missed cycles are stale. Other
    # artifacts retain their independent hourly display budget.
    budget = 180 if kind == "scheduler" else 3600
    snapshot_freshness = freshness(captured, p.now, budget)
    snapshot_fresh = snapshot_freshness == "FRESH"

    def systemd_time(value):
        if not isinstance(value, str) or not value.endswith(" UTC"):
            return None
        try:
            return datetime.strptime(value, "%a %Y-%m-%d %H:%M:%S UTC").replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            return None

    for row in rows:
        if not isinstance(row, dict):
            continue
        if kind == "scheduler":
            # A stale snapshot is historical, and an active timer cannot hide
            # a failed service. It proves scheduling, not work completion.
            enabled, active = row.get("enabled_state"), row.get("active_state")
            outcome = row.get("last_result")
            current = state == "AVAILABLE" and payload.get("state") == "OK" and snapshot_fresh
            row_state = ("SERVICE_FAILED" if outcome not in {None, "", "success", "unknown"} else
                         "TIMER_ACTIVE" if enabled == "enabled" and active == "active" and outcome == "success" else
                         "SUSPENDED" if enabled in {"masked", "disabled"} and active == "inactive" else UNKNOWN)
            row = {**row, "state": row_state if current else UNKNOWN,
                   "enabled": enabled, "active": active,
                   "next_run": systemd_time(row.get("next_elapse")),
                   "last_run": systemd_time(row.get("last_trigger")),
                   "description": row.get("description"),
                   "reason": ("Service result: " + str(outcome)[:64]) if outcome not in {None, "success"} else None}
        # No arbitrary recursive payload dump: detail columns are allowlisted.
        result.append({**row, "as_of": row.get("as_of") or captured,
                       "source": files[kind], "freshness": freshness(row.get("as_of") or captured, p.now, budget)})
    page_state = state
    if kind == "scheduler":
        page_state = (snapshot_freshness if state == "AVAILABLE" and payload.get("state") == "OK"
                      else UNKNOWN)
    return Page(files[kind], result[p.offset:p.offset + 10], len(result) if state == "AVAILABLE" else None,
                page_state, p.offset, captured, state)


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
    errors = len(s.errors)
    count = s.query("SELECT COUNT(DISTINCT instrument_type) n FROM candidate_identity_v2")
    rows = s.query("""SELECT instrument_type family,COUNT(*) candidates,
        SUM(CASE WHEN can_simulate=1 AND upper(status)='AVAILABLE' THEN 1 ELSE 0 END) ready,
        MAX(checked_at) as_of FROM candidate_identity_v2 GROUP BY instrument_type
        ORDER BY instrument_type LIMIT 10 OFFSET ?""", (p.offset,))
    if len(s.errors) != errors:
        return Page("candidate_identity_v2", reason="FAMILY_SUMMARY_READ_ERROR")
    for row in rows:
        row.update(source="candidate_identity_v2", status=UNKNOWN, entry_authority=UNKNOWN,
                   strategy=UNKNOWN, lifecycle_owner=UNKNOWN, blocker="Routing de estrategia pendiente de reconciliación #466",
                   scope="Agregado por familia; sin identidad exacta ni mercado, moneda o plazo únicos",
                   reason="RUNTIME_READY agregado no certifica estrategia ni autoridad de entrada")
    return Page("candidate_identity_v2 · readiness (not strategy permission)", rows, count[0]["n"] if count else None, "AVAILABLE", p.offset)
