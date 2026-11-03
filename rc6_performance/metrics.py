"""Currency-separated realized metrics and explicit evidence gaps."""
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from statistics import median
from zoneinfo import ZoneInfo

from .common import number, stamp, identity, digest
from .costs import realized_round_trip_cost

ZERO = Decimal(0)
ART = ZoneInfo("America/Argentina/Buenos_Aires")


def auc(scores, outcomes):
    positives = sum(outcomes)
    negatives = len(outcomes) - positives
    if not positives or not negatives:
        return None
    # Pairwise ties receive half a point; no probability interpretation.
    pos = [s for s, outcome in zip(scores, outcomes) if outcome]
    neg = [s for s, outcome in zip(scores, outcomes) if not outcome]
    return float(sum((Decimal(1) if p > n else Decimal("0.5") if p == n else ZERO)
                     for p in pos for n in neg) / (positives * negatives))


def distribution(values):
    values = sorted(number(x) for x in values)
    if not values:
        return {"n": 0, "p05": None, "p50": None, "p95": None, "min": None, "max": None}
    def percentile(fraction):
        index = (len(values) - 1) * number(fraction)
        left = int(index)
        right = min(left + 1, len(values) - 1)
        return values[left] + (values[right] - values[left]) * (index - left)
    return {"n": len(values), "p05": percentile("0.05"), "p50": percentile("0.5"),
            "p95": percentile("0.95"), "min": values[0], "max": values[-1]}


def trade_metrics(trades):
    net = [number(t["net_pnl"]) for t in trades]
    gross = [number(t["gross_pnl"]) for t in trades]
    costs = [number(t["entry_cost"]) + number(t["exit_cost"]) for t in trades]
    notional = [number(t["entry_price"]) * number(t["quantity"]) * number(t.get("contract_cash_multiplier", 1)) for t in trades]
    winners, losers = [n for n in net if n > 0], [n for n in net if n < 0]
    total_loss = -sum(losers, ZERO)
    returns = [n / amount for n, amount in zip(net, notional)]
    favorable, adverse = [], []
    for trade in trades:
        excursion = trade.get("mfe_mae") or {}
        if excursion.get("status") == "MEDIDO":
            favorable.append(number(excursion["mfe_exec_return"]))
            adverse.append(number(excursion["mae_exec_return"]))
    scored = [t for t in trades if t.get("decision_score") is not None]
    cumulative = peak = drawdown = ZERO
    for trade in sorted(trades, key=lambda t: (stamp(t["closed_at"]), t["paper_id"])):
        cumulative += number(trade["net_pnl"])
        peak = max(peak, cumulative)
        drawdown = min(drawdown, cumulative - peak)
    daily = defaultdict(lambda: ZERO)
    for trade in trades:
        daily[stamp(trade["closed_at"]).astimezone(ART).date()] += number(trade["net_pnl"])
    daily_cumulative = daily_peak = daily_drawdown = ZERO
    for day in sorted(daily):
        daily_cumulative += daily[day]
        daily_peak = max(daily_peak, daily_cumulative)
        daily_drawdown = min(daily_drawdown, daily_cumulative-daily_peak)
    return {"n": len(trades), "wins": len(winners), "losses": len(losers),
            "gross": sum(gross, ZERO), "costs": sum(costs, ZERO), "net": sum(net, ZERO),
            "win_rate": len(winners) / len(trades) if trades else None,
            "profit_factor": sum(winners, ZERO) / total_loss if total_loss else None,
            "average_win": sum(winners, ZERO) / len(winners) if winners else None,
            "average_loss": sum(losers, ZERO) / len(losers) if losers else None,
            "expectancy_money": sum(net, ZERO) / len(trades) if trades else None,
            "mean_trade_return": sum(returns, ZERO) / len(returns) if returns else None,
            "entry_turnover": sum(notional, ZERO), "realized_daily_drawdown": daily_drawdown,
            "roundtrip_turnover": sum((number(t.get("turnover")) if t.get("turnover") is not None else
                                      number(t["entry_price"])*number(t["quantity"])*number(t.get("contract_cash_multiplier", 1))*2 + number(t["gross_pnl"])
                                      for t in trades), ZERO),
            "exit_frequencies": {reason: sum(t["close_reason"] == reason for t in trades)/len(trades)
                                 for reason in ("STOP_PAPER", "TAKE_PROFIT_PAPER", "EOD_PAPER", "MAX_HOLD_PAPER")} if trades else {},
            "opportunities": "NO_VERIFICADO",
            "realized_trade_drawdown": drawdown,
            "median_roundtrip_cost_rate": median([c / n for c, n in zip(costs, notional)]) if trades else None,
            "mfe_observed_median": median(favorable) if favorable else None,
            "mae_observed_median": median(adverse) if adverse else None,
            "mfe_ge_5pct": sum(n >= Decimal("0.05") for n in favorable),
            "score_auc": auc([number(t["decision_score"]) for t in scored], [number(t["net_pnl"]) > 0 for t in scored]),
            "return_tails": distribution(returns), "equity_drawdown": "NO_VERIFICADO"}


def audit_master(master, *, sessions):
    if (master.get("read_only") is not True or master.get("network_calls_performed") is not False or
            master.get("broker_calls_performed") is not False or
            master.get("safety") != {"mode": "PRODUCTION_PAPER", "real_orders_sent": 0}):
        raise ValueError("PAPER_READ_ONLY_SOURCE_REQUIRED")
    days = [date.fromisoformat(str(d)) for d in sessions]
    if not days or days != sorted(set(days)):
        raise ValueError("EXPLICIT_ORDERED_SESSIONS_REQUIRED")
    cut = stamp(master["generated_at"]).astimezone(ART)
    if cut.date() < days[-1] or (cut.date() == days[-1] and cut.hour < 17):
        raise ValueError("INCOMPLETE_SOURCE_CUTOFF")
    all_trades = master["trades"]
    if len({t["paper_id"] for t in all_trades}) != len(all_trades):
        raise ValueError("DUPLICATE_POSITION")
    selected = [t for t in all_trades if stamp(t["closed_at"]).astimezone(ART).date() in set(days)]
    reconciliation, cohorts = [], defaultdict(list)
    for trade in selected:
        # Contract multiplier defaults to 1 only in the audited equity families.
        if trade["asset_class"] not in {"ACCIONES", "CEDEARS", "ETFS"} and not trade.get("contract_cash_multiplier"):
            raise ValueError("CONTRACT_MULTIPLIER_REQUIRED")
        reconciliation.append({"paper_id": trade["paper_id"], **realized_round_trip_cost(trade, trade["fills"])})
        day = stamp(trade["closed_at"]).astimezone(ART).date()
        for dimension, key in (("currency", trade["currency"]), ("day", day.isoformat()),
                               ("week", str(day.isocalendar()[:2])), ("strategy", trade["strategy_version"]),
                               ("version", trade["strategy_version"]), ("strategy_id", trade.get("strategy_id", "NO_VERIFICADO")),
                               ("symbol", trade["symbol"]), ("family", trade["asset_class"]),
                               ("entry_hour", str(stamp(trade["opened_at"]).astimezone(ART).hour)),
                               ("exit_reason", trade["close_reason"])):
            cohorts[(dimension, key, trade["currency"])].append(trade)
    currencies = sorted({t["currency"] for t in selected})
    for currency in currencies:
        for day in days:
            cohorts.setdefault(("day", day.isoformat(), currency), [])
    return {"mode": "READ_ONLY_OFFLINE", "real_order_routes": [], "n_sessions": len(days),
            "sessions": [d.isoformat() for d in days], "closed_count": len(selected),
            "fills_count": sum(len(t["fills"]) for t in selected),
            "by_currency": {c: trade_metrics([t for t in selected if t["currency"] == c]) for c in currencies},
            "cohorts": [{"dimension": dim, "key": key, "currency": cur, **trade_metrics(ts)}
                        for (dim, key, cur), ts in sorted(cohorts.items())],
            "reconciliation": reconciliation,
            "partial_sell_trades": sum(sum(f["side"] == "SELL_SIMULATED" for f in t["fills"]) > 1 for t in selected),
            "hash_verified_trades": sum(t.get("evidence_hash_valid") is True for t in selected),
            "source_canonical_sha256": digest(master),
            "closed_trades_per_session": {c: sum(t["currency"] == c for t in selected)/len(days) for c in currencies},
            "limitations": ["realized spot P&L, not account equity", "currencies never added",
                            "MFE/MAE observed, not guaranteed executable", "no economic promotion authority"]}


def historical_sessions():
    """Only this audited BYMA window. This is not a universal holiday calendar."""
    start, end = date(2026, 9, 7), date(2026, 10, 2)
    return [(start + timedelta(days=i)).isoformat() for i in range((end-start).days+1)
            if (start + timedelta(days=i)).weekday() < 5]


def decision_funnel(snapshots):
    seen, stages, reasons, identities, gaps = set(), defaultdict(int), defaultdict(int), set(), defaultdict(int)
    lineage = []
    for snapshot in snapshots:
        from .common import decision_snapshot_phase
        if decision_snapshot_phase(snapshot) == "ATOMIC_PAPER_ADMISSION":
            continue
        key = snapshot["decision_key"]
        if key in seen:
            continue
        seen.add(key)
        q, d = snapshot["quote_used"], snapshot["decision"]
        try:
            ident = identity(q)
            identities.add(ident)
        except ValueError:
            ident = None
            gaps["exact_identity"] += 1
        features = snapshot.get("inputs_used") or {}
        action = d.get("action") or features.get("candidate", {}).get("action")
        is_candidate = d.get("technical_gate") == "APPROVE" or action == "BUY" and d.get("technical_gate") != "NOT_CANDIDATE"
        final = d.get("final_result")
        stages["OBSERVED_DECISIONS"] += 1
        if features.get("candidate"):
            stages["FEATURES_READY"] += 1
        if is_candidate:
            stages["BUY_CANDIDATE"] += 1
        if final == "OPENED_SIMULATED":
            stages["ACCEPTED"] += 1
            stages["OPENED"] += 1
        elif is_candidate and final == "BLOCKED":
            stages["REJECTED"] += 1
        reason = d.get("reason_code") or features.get("reason_code") or structured_reason(d.get("reason", ""), features)
        if final != "OPENED_SIMULATED":
            reasons[reason] += 1
        row = {"decision_key": key, "identity": ident, "paper_id": d.get("paper_id"),
               "strategy_version": snapshot.get("runtime", {}).get("strategy_version"),
               "strategy_id": snapshot.get("runtime", {}).get("strategy_id"),
               "quote_received_at": q.get("observed_at"), "quote_source_at": q.get("book_at"),
               "signal_at": snapshot.get("signal_at"), "decision_at": snapshot.get("decision_at"),
               "intent_at": snapshot.get("intent_at"),
               "entry_fill_committed_at": snapshot.get("entry_fill_committed_at"),
               "legacy_captured_at": snapshot.get("captured_at"), "score": d.get("score") if d.get("score") is not None else features.get("candidate", {}).get("score"),
               "reason_code": reason, "raw_reason": d.get("reason"), "final_result": final,
               "reason_code_source": "NATIVE_GATE" if d.get("reason_code") or features.get("reason_code") else "LEGACY_HEURISTIC",
               "git_sha": snapshot.get("runtime", {}).get("git_sha"),
               "configuration_fingerprint": snapshot.get("runtime", {}).get("configuration_fingerprint"),
               "snapshot_sha256": digest(snapshot)}
        missing = [k for k in ("signal_at", "decision_at", "git_sha", "configuration_fingerprint") if not row[k]]
        for field in missing:
            gaps[field] += 1
        row["missing_evidence"] = missing
        lineage.append(row)
    return {"decisions": len(seen), "observed_identities": len(identities), "stages": dict(stages),
            "rejection_reasons": dict(reasons), "evidence_gaps": dict(gaps), "lineage": lineage,
            "catalog_universe": "NO_VERIFICADO", "signal_opportunities": "NO_VERIFICADO",
            "acceptance_rate": stages["ACCEPTED"]/stages["BUY_CANDIDATE"] if stages["BUY_CANDIDATE"] else None,
            "note": "decision keys are evaluations, not independent profitable opportunities"}


def structured_reason(raw, features):
    text = str(raw).upper()
    codes = (("STALE", "stale_quote"), ("BOOK_TIME", "stale_book"),
             ("PROFUNDIDAD", "insufficient_depth"), ("DEPTH", "insufficient_depth"),
             ("SPREAD", "spread_too_wide"), ("ECONOM", "economic_edge_insufficient"),
             ("CAJA", "insufficient_cash"), ("CAPITAL", "insufficient_cash"),
             ("RISK", "risk_limit"), ("RIESGO", "risk_limit"), ("EOD", "EOD_too_close"),
             ("CONTRACT", "contract_not_ready"), ("CONTRATO", "contract_not_ready"),
             ("COOLDOWN", "cooldown"), ("DUPLICATE", "duplicate_position"),
             ("APRENDIENDO SERIE", "insufficient_samples"), ("INSUFFICIENT", "insufficient_samples"),
             ("SCORE", "score_below_threshold"))
    for marker, code in codes:
        if marker in text:
            return code
    return "unmapped_reason"


def signal_evaluation(labels):
    measured = [x for x in labels if x.get("status") == "MEDIDO"]
    grouped = defaultdict(list)
    for label in measured:
        ident = tuple(label["identity"])
        grouped[(label["horizon_seconds"], ident[1], ident[3])].append(label)
    reports = []
    for (horizon, family, currency), rows in sorted(grouped.items()):
        returns = [number(x["gross_forward_return"]) for x in rows]
        scored = [x for x in rows if x.get("score") is not None]
        reports.append({"horizon_seconds": horizon, "family": family, "currency": currency,
                        "observations": len(rows), "hit_rate_gross": sum(r > 0 for r in returns)/len(rows),
                        "gross_expectancy": sum(returns, ZERO)/len(rows), "forward_returns": distribution(returns),
                        "mfe": distribution(x["mfe_observed"] for x in rows),
                        "mae": distribution(x["mae_observed"] for x in rows),
                        "auc_gross": auc([number(x["score"]) for x in scored], [number(x["gross_forward_return"]) > 0 for x in scored]),
                        "net_expectancy": "NO_VERIFICADO", "precision_recall": "requires explicit prediction contract",
                        "calibration_curve": "not applicable: score is not a probability",
                        "economic_edge_validated": False})
    return {"reports": reports, "unmeasured": len(labels)-len(measured), "sample_is_independent": False}


def exit_latencies(probes, fills):
    grouped = defaultdict(dict)
    for probe in probes:
        grouped[probe["paper_id"]].setdefault(probe["stage"], probe["at"])
    result, missing = defaultdict(list), defaultdict(int)
    for paper_id, stages in grouped.items():
        sells = sorted([f for f in fills if f["paper_id"] == paper_id and f["side"] == "SELL_SIMULATED"],
                       key=lambda f: stamp(f.get("filled_at", f.get("at"))))
        endpoints = {"condition": stages.get("CONDITION"), "intent": stages.get("INTENT_COMMITTED"),
                     "first_fill": sells[0].get("filled_at", sells[0].get("at")) if sells else None,
                     "final_fill": stages.get("FINAL_FILL")}
        for left, right in (("condition", "intent"), ("intent", "first_fill"), ("first_fill", "final_fill"), ("condition", "final_fill")):
            key = left + "_to_" + right
            if not endpoints[left] or not endpoints[right]:
                missing[key] += 1
                continue
            elapsed = (stamp(endpoints[right])-stamp(endpoints[left])).total_seconds()
            if elapsed < 0:
                missing[key + "_clock_order_invalid"] += 1
                continue
            result[key].append(Decimal(str(elapsed)))
    stats = {}
    for key, values in result.items():
        ordered = sorted(values)
        def quantile(p):
            idx = Decimal(str(p))*(len(ordered)-1)
            low = int(idx)
            high = min(low+1, len(ordered)-1)
            return ordered[low]+(ordered[high]-ordered[low])*(idx-low)
        stats[key] = {"n": len(ordered), "p50": quantile(.5), "p95": quantile(.95), "p99": quantile(.99), "max": ordered[-1]}
    return {"seconds": stats, "missing": dict(missing), "causal_sqlite_attribution": "NO_VERIFICADO"}
