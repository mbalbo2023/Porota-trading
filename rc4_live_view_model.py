"""Pure RC4 view-model for the canonical /vivo operations page.

The module contains no DB/network access and cannot place orders.  It turns
already-persisted PAPER evidence into a stable UI payload so the dashboard can
show operations first, with mark freshness, P&L state, gate rationale and a
post-close deterministic lesson.

Important semantics:
* currencies are never aggregated together;
* an OPEN position without a fresh persisted ``paper_position_marks`` row has
  P&L state ``UNAVAILABLE``/``STALE`` rather than pretending to be current;
* lessons describe observed facts and the next analysis to perform.  They do
  not automatically relax/tighten strategy parameters;
* Scalping is identified from persisted ``execution_style`` evidence, not from
  ticker heuristics.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
from typing import Iterable

ZERO=Decimal("0")


def _D(value, default=None):
    try:
        result=Decimal(str(value))
    except (InvalidOperation,TypeError,ValueError):
        return default
    return result if result.is_finite() else default


def _dt(value):
    if not value:
        return None
    try:
        result=datetime.fromisoformat(str(value).replace("Z","+00:00"))
        if result.tzinfo is None:
            result=result.replace(tzinfo=timezone.utc)
        return result.astimezone(timezone.utc)
    except (TypeError,ValueError):
        return None


def _features(value) -> dict:
    if isinstance(value,dict):
        return dict(value)
    try:
        result=json.loads(value or "{}")
        return result if isinstance(result,dict) else {}
    except (TypeError,ValueError,json.JSONDecodeError):
        return {}


def _multiplier(position: dict) -> Decimal:
    features=_features(position.get("features_json"))
    value=_D(features.get("contract_cash_multiplier"),Decimal("1"))
    return value if value is not None and value > 0 else Decimal("1")


def execution_style(position: dict) -> str:
    return str(_features(position.get("features_json")).get("execution_style") or
               "STANDARD_PAPER").upper()


def mark_freshness(mark: dict | None, *, now=None,
                   fresh_seconds: int=180, stale_seconds: int=900) -> dict:
    mark=dict(mark or {})
    now_dt=_dt(now) if now is not None else datetime.now(timezone.utc)
    if now_dt is None:
        now_dt=datetime.now(timezone.utc)
    # book_at is the market timestamp; marked_at is when the engine persisted it.
    book=_dt(mark.get("book_at"))
    persisted=_dt(mark.get("marked_at"))
    evidence=book or persisted
    if evidence is None:
        return {"state":"NO_MARK","age_seconds":None,"mark_at":None,
                "marked_at":mark.get("marked_at")}
    age=max(Decimal("0"),Decimal(str((now_dt-evidence).total_seconds())))
    if age <= fresh_seconds:
        state="FRESH"
    elif age <= stale_seconds:
        state="AGING"
    else:
        state="STALE"
    return {"state":state,"age_seconds":float(age),
            "mark_at":mark.get("book_at") or mark.get("marked_at"),
            "marked_at":mark.get("marked_at")}


def position_pnl(position: dict, mark: dict | None) -> dict:
    """Return a currency-local net PAPER valuation, never FX-aggregate it."""
    status=str(position.get("status") or "").upper()
    if status == "CLOSED":
        value=_D(position.get("net_pnl"))
        return {"value":value,"kind":"REALIZED_NET",
                "state":_pnl_state(value)}
    mark=dict(mark or {})
    mark_price=_D(mark.get("mark_price"))
    if mark_price is None or mark_price <= 0:
        return {"value":None,"kind":"UNAVAILABLE_NO_MARK","state":"UNAVAILABLE"}
    quantity=_D(position.get("quantity"))
    entry=_D(position.get("entry_price"))
    entry_cost=_D(position.get("entry_cost"),ZERO)
    factor=_multiplier(position)
    if quantity is None or quantity <= 0 or entry is None or entry <= 0:
        return {"value":None,"kind":"UNAVAILABLE_POSITION_DATA","state":"UNAVAILABLE"}
    entry_notional=entry*quantity*factor
    mark_notional=mark_price*quantity*factor
    # Candidate1 estimates exit friction using the entry effective rate.  RC4
    # preserves that convention until the slippage/cost replay justifies a
    # different model.
    exit_cost=(entry_cost*mark_notional/entry_notional
               if entry_notional > 0 else ZERO)
    value=(mark_price-entry)*quantity*factor-entry_cost-exit_cost
    return {"value":value,"kind":"UNREALIZED_NET_MODELED_EXIT_COST",
            "state":_pnl_state(value),"modeled_exit_cost":exit_cost}


def _pnl_state(value) -> str:
    if value is None:
        return "UNAVAILABLE"
    value=_D(value)
    if value is None:
        return "UNAVAILABLE"
    return "POSITIVE" if value > 0 else "NEGATIVE" if value < 0 else "NEUTRAL"


def _gate_for_position(position: dict, gates: Iterable[dict]) -> dict:
    paper_id=position.get("paper_id")
    symbol=position.get("symbol")
    rows=[dict(x) for x in gates or ()]
    for row in reversed(rows):
        if paper_id and row.get("paper_id") == paper_id:
            return row
    for row in reversed(rows):
        if symbol and row.get("symbol") == symbol:
            return row
    return {}


def _learning_for(position: dict, samples: Iterable[dict]) -> dict:
    paper_id=position.get("paper_id")
    for row in samples or ():
        row=dict(row)
        if row.get("paper_id") == paper_id:
            return row
    return {}


def deterministic_lesson(position: dict, sample: dict | None=None) -> dict:
    """Produce an evidence-bounded post-close lesson.

    This is intentionally deterministic and conservative.  A lesson is a
    statement about what happened and what counterfactual should be examined;
    it is not an automatic parameter change.
    """
    if str(position.get("status") or "").upper() != "CLOSED":
        return {"state":"NOT_APPLICABLE_OPEN","code":"OPEN_POSITION",
                "text":"La operación sigue abierta; todavía no existe lección final."}
    sample=dict(sample or {})
    pnl=_D(position.get("net_pnl"),ZERO)
    reason=str(position.get("close_reason") or "UNKNOWN").upper()
    duration=sample.get("duration_minutes")
    mfe=_D(position.get("max_favorable"))
    mae=_D(position.get("max_adverse"))
    context=[]
    if duration is not None:
        context.append(f"duración={duration} min")
    if mfe is not None:
        context.append(f"MFE={mfe}")
    if mae is not None:
        context.append(f"MAE={mae}")
    suffix=(" ("+", ".join(context)+")") if context else ""

    if reason == "TARGET_PAPER":
        text="El objetivo cerró la posición con resultado neto positivo; conservar esta muestra para el replay de geometría y costos."
    elif reason == "STOP_PAPER":
        text="El stop cerró la posición; comparar MFE/MAE y barreras contrafácticas antes de modificar el stop."
    elif reason == "EOD_PAPER":
        text="La política de fin de rueda determinó el cierre; esta muestra debe entrar al replay de duración y barreras, no atribuirse sólo a la señal de entrada."
    elif reason == "MAX_HOLD_PAPER":
        text="La política de permanencia máxima determinó el cierre; usar esta muestra en el replay de MAX_HOLD antes de cambiar el umbral."
    elif reason == "DAILY_LOSS_PAPER":
        text="El límite de pérdida diaria determinó el cierre; no usar este resultado aislado para juzgar la señal de entrada."
    elif pnl > 0:
        text="La operación cerró con resultado neto positivo; revisar qué parte provino de movimiento de precio versus costos antes de reforzar la regla de entrada."
    elif pnl < 0:
        text="La operación cerró con resultado neto negativo; revisar señal, costo, spread, slippage, MFE/MAE y duración antes de recalibrar."
    else:
        text="La operación cerró sin resultado neto concluyente; no usar esta muestra para relajar umbrales."
    return {"state":"AVAILABLE","code":reason,"text":text+suffix,
            "outcome":sample.get("outcome"),"net_return_pct":sample.get("net_return_pct"),
            "duration_minutes":duration}


def operation_item(position: dict, *, mark=None, gate=None, sample=None,
                   exit_intent=None, now=None) -> dict:
    position=dict(position or {})
    mark=dict(mark or {})
    gate=dict(gate or {})
    exit_intent=dict(exit_intent or {})
    freshness=mark_freshness(mark,now=now)
    pnl=position_pnl(position,mark)
    # A stale mark must never look current even if the arithmetic is available.
    display_state=pnl["state"]
    if str(position.get("status") or "").upper()=="OPEN" and freshness["state"] in {"NO_MARK","STALE"}:
        display_state="STALE" if freshness["state"]=="STALE" else "UNAVAILABLE"
    features=_features(position.get("features_json"))
    return {
        "paper_id":position.get("paper_id"),"symbol":position.get("symbol"),
        "family":position.get("asset_class"),"currency":position.get("currency"),
        "market":position.get("market"),"settlement":position.get("settlement"),
        "status":position.get("status"),"execution_style":execution_style(position),
        "quantity":position.get("quantity"),"entry_price":position.get("entry_price"),
        "entry_cost":position.get("entry_cost"),"opened_at":position.get("opened_at"),
        "closed_at":position.get("closed_at"),"stop_price":position.get("stop_price"),
        "target_price":position.get("target_price"),"close_reason":position.get("close_reason"),
        "mark_price":mark.get("mark_price"),"mark_at":freshness.get("mark_at"),
        "marked_at":freshness.get("marked_at"),"mark_freshness":freshness["state"],
        "mark_age_seconds":freshness["age_seconds"],
        "pnl":pnl["value"],"pnl_kind":pnl["kind"],"pnl_state":display_state,
        "gate":{
            "technical":gate.get("technical_gate"),"ai":gate.get("ai_gate"),
            "patrimonial":gate.get("patrimonial_gate"),
            "final_result":gate.get("final_result"),"reason":gate.get("reason"),
            "evaluated_at":gate.get("evaluated_at"),
        },
        "exit_supervision":{
            "state":exit_intent.get("state"),"cause":exit_intent.get("cause"),
            "blocked_reason":exit_intent.get("blocked_reason"),
            "supervised_at":exit_intent.get("supervised_at"),
        },
        "risk":{
            "concurrent_preview":features.get("concurrent_risk_preview"),
            "concurrent_locked":features.get("concurrent_risk_locked"),
            "risk_budget":features.get("risk_budget"),
        },
        "features":features,
        "lesson":deterministic_lesson(position,sample),
    }


def build_live_model(*, positions, marks=(), gates=(), decisions=(),
                     learning_samples=(), exit_intents=(), scalping_candidates=(),
                     workers=(), now=None, closed_limit: int=20,
                     decision_limit: int=50) -> dict:
    """Return canonical /vivo sections in UX priority order."""
    positions=[dict(x) for x in positions or ()]
    marks_by={dict(x).get("paper_id"):dict(x) for x in marks or ()}
    samples_by={dict(x).get("paper_id"):dict(x) for x in learning_samples or ()}
    intents_by={dict(x).get("paper_id"):dict(x) for x in exit_intents or ()}
    gate_rows=[dict(x) for x in gates or ()]

    items=[]
    for position in positions:
        items.append(operation_item(
            position, mark=marks_by.get(position.get("paper_id")),
            gate=_gate_for_position(position,gate_rows),
            sample=samples_by.get(position.get("paper_id")),
            exit_intent=intents_by.get(position.get("paper_id")), now=now))
    opened=[x for x in items if str(x.get("status") or "").upper()=="OPEN"]
    closed=[x for x in items if str(x.get("status") or "").upper()=="CLOSED"]
    opened.sort(key=lambda x:_dt(x.get("opened_at")) or datetime.min.replace(tzinfo=timezone.utc),reverse=True)
    closed.sort(key=lambda x:_dt(x.get("closed_at")) or datetime.min.replace(tzinfo=timezone.utc),reverse=True)

    decision_rows=[]
    for row in sorted((dict(x) for x in decisions or ()),
                      key=lambda x:_dt(x.get("decided_at")) or datetime.min.replace(tzinfo=timezone.utc),
                      reverse=True)[:max(1,int(decision_limit))]:
        decision_rows.append({
            "decided_at":row.get("decided_at"),"symbol":row.get("symbol"),
            "action":row.get("action"),"score":row.get("score"),
            "reason":row.get("reason"),"features":_features(row.get("features_json")),
        })

    scalping_rows=[dict(x) for x in scalping_candidates or ()]
    scalp_positions=[x for x in items if x.get("execution_style")=="SCALPING_PAPER"]
    scalping={
        "evaluated":len(scalping_rows),
        "buy_candidates":sum(str(x.get("action") or "").upper()=="BUY_CANDIDATE" for x in scalping_rows),
        "open_positions":sum(str(x.get("status") or "").upper()=="OPEN" for x in scalp_positions),
        "closed_positions":sum(str(x.get("status") or "").upper()=="CLOSED" for x in scalp_positions),
        "recent_candidates":scalping_rows[:20],
    }

    pnl_by_currency={}
    for item in opened:
        if item.get("pnl") is None:
            continue
        cur=str(item.get("currency") or "UNKNOWN")
        pnl_by_currency[cur]=pnl_by_currency.get(cur,ZERO)+_D(item["pnl"],ZERO)

    return {
        "section_order":["open_operations","closed_operations","decisions","scalping","workers"],
        "open_operations":opened,
        "closed_operations":closed[:max(1,int(closed_limit))],
        "decisions":decision_rows,
        "scalping":scalping,
        "workers":[dict(x) for x in workers or ()],
        "open_pnl_by_currency":dict(sorted(pnl_by_currency.items())),
        "rejection_funnel_enabled":False,
    }


def serializable(model: dict) -> dict:
    def convert(value):
        if isinstance(value,Decimal): return str(value)
        if isinstance(value,dict): return {k:convert(v) for k,v in value.items()}
        if isinstance(value,(list,tuple)): return [convert(v) for v in value]
        return value
    return convert(model)
