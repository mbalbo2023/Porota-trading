"""RC4 policy gates: evaluate always; only BINDING has blocking authority.

SHADOW/OBSERVATION modes always persist a counterfactual verdict.  When a
policy is explicitly BINDING, missing evidence is itself fail-closed rather
than silently treated as PASS.
"""
from __future__ import annotations
import os
from decimal import Decimal, InvalidOperation

ZERO = Decimal("0")
EXPECTANCY_ENV = "PAPER_EXPECTANCY_POLICY"
REGIME_ENV = "PAPER_MARKET_REGIME_POLICY"
SECTOR_ENV = "PAPER_SECTOR_CONCENTRATION_POLICY"
SECTOR_LIMIT_ENV = "PAPER_MAX_POSITIONS_PER_SECTOR"


def _mode(name, default):
    return str(os.getenv(name, default) or default).strip().upper()


def _d(value, label):
    try:
        out = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(label + " inválido") from exc
    if not out.is_finite():
        raise ValueError(label + " no finito")
    return out


def expectancy_verdict(samples):
    usable = [r for r in (samples or []) if isinstance(r, dict)
              and str(r.get("sample_state")) == "OBSERVATIONAL"]
    if not usable:
        return {"state":"INSUFFICIENT_EVIDENCE",
                "reason":"EXPECTANCY_INSUFFICIENT_SAMPLE","would_block":False}
    for row in usable:
        if _d(row.get("empirical_expectancy", 0), "expectancy") < ZERO:
            return {"state":"FAIL",
                    "reason":"EXPECTANCY_NEGATIVE_" + str(row.get("currency") or "ARS").upper(),
                    "would_block":True}
    return {"state":"PASS","reason":"EXPECTANCY_NON_NEGATIVE","would_block":False}


def regime_verdict(obs):
    if (not isinstance(obs, dict) or
            str(obs.get("state") or "") in {"", "INSUFFICIENT_SAMPLE"}):
        return {"state":"INSUFFICIENT_EVIDENCE",
                "reason":"REGIME_INSUFFICIENT_SAMPLE","would_block":False}
    blocked = str(obs.get("state")) == "BEARISH_BREADTH"
    return {"state":"FAIL" if blocked else "PASS",
            "reason":"BEARISH_BREADTH_NO_LONG_ENTRIES" if blocked else "REGIME_ACCEPTABLE",
            "would_block":blocked}


def sector_verdict(obs, candidate_sector=None, *, limit=None):
    sector = str(candidate_sector or "").strip()
    if not isinstance(obs, dict) or not sector:
        return {"state":"INSUFFICIENT_EVIDENCE",
                "reason":"SECTOR_UNMAPPED","would_block":False}
    cap = int(limit if limit is not None else os.getenv(SECTOR_LIMIT_ENV, "2"))
    count = 0
    for group in obs.get("groups", ()) or ():
        if str(group.get("sector") or "").strip() == sector:
            count = int(group.get("open_positions") or 0)
            break
    blocked = count >= cap
    return {"state":"FAIL" if blocked else "PASS",
            "reason":f"SECTOR_CONCENTRATION_LIMIT_{sector.upper()}" if blocked else "SECTOR_CAPACITY_AVAILABLE",
            "would_block":blocked,"limit":cap,"open_positions":count,"sector":sector}


def _authorized_gate(verdict, authority):
    row = dict(verdict, authority=authority)
    evidence_block = authority == "BINDING" and row.get("state") == "INSUFFICIENT_EVIDENCE"
    row["evidence_block"] = evidence_block
    row["execute_block"] = bool(authority == "BINDING" and (row.get("would_block") or evidence_block))
    if evidence_block:
        row["execute_reason"] = str(row.get("reason") or "POLICY_EVIDENCE_REQUIRED") + "_BINDING"
    else:
        row["execute_reason"] = row.get("reason") if row["execute_block"] else ""
    return row


def evaluate(*, expectancy_samples=None, breadth=None, sectors=None,
             candidate_sector=None, sector_limit=None):
    gates = {
        "expectancy": _authorized_gate(expectancy_verdict(expectancy_samples),
                                       _mode(EXPECTANCY_ENV, "OBSERVATION_ONLY")),
        "regime": _authorized_gate(regime_verdict(breadth),
                                   _mode(REGIME_ENV, "ALERT_ONLY")),
        "sector": _authorized_gate(sector_verdict(sectors, candidate_sector, limit=sector_limit),
                                   _mode(SECTOR_ENV, "OBSERVATION_ONLY")),
    }
    reason = next((g["execute_reason"] for g in gates.values() if g["execute_block"]), "")
    return {
        "gates": gates,
        "would_block": any(bool(g.get("would_block")) for g in gates.values()),
        "evidence_block": any(bool(g.get("evidence_block")) for g in gates.values()),
        "execute_block": bool(reason),
        "verdict": reason or "PASS",
    }


def active_policies():
    return {
        "expectancy": _mode(EXPECTANCY_ENV, "OBSERVATION_ONLY"),
        "regime": _mode(REGIME_ENV, "ALERT_ONLY"),
        "sector_concentration": _mode(SECTOR_ENV, "OBSERVATION_ONLY"),
        "sector_limit": int(os.getenv(SECTOR_LIMIT_ENV, "2")),
    }
