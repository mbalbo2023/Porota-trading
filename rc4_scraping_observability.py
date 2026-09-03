"""RC4 read-only scraping / Contract Evidence observability model.

Consumes sanitized Contract Evidence v2 run/snapshot/change rows and produces a
UI-safe history.  It never exposes cookies/tokens/raw HTML and never interprets
collection success as READY_PAPER.
"""
from __future__ import annotations

from datetime import datetime, timezone

SECRET_WORDS=("token","secret","password","cookie","authorization","session","account")


def _parse(value):
    if not value:
        return None
    try:
        dt=datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt=dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (TypeError,ValueError):
        return None


def _safe_text(value, limit=500):
    text=str(value or "").replace("\n"," ").replace("\r"," ")[:limit]
    lower=text.lower()
    if any(word in lower for word in SECRET_WORDS):
        return "[REDACTED_SENSITIVE_DETAIL]"
    return text


def run_view(row, *, now=None, cadence_seconds=None) -> dict:
    row=dict(row or {})
    started=_parse(row.get("started_at")); finished=_parse(row.get("finished_at"))
    now_dt=_parse(now) or datetime.now(timezone.utc)
    age=(now_dt-(finished or started)).total_seconds() if (finished or started) else None
    cadence=None if cadence_seconds is None else max(1,int(cadence_seconds))
    stale_after=None if cadence is None else cadence*2
    state=str(row.get("state") or "SIN_EVIDENCIA").upper()
    auth=str(row.get("auth_state") or "UNKNOWN").upper()

    if auth not in {"AUTHENTICATED","NOT_REQUIRED","OK"}:
        visual="HOLD"; cause="AUTH_OR_2FA_BLOCKED"
    elif state in {"ERROR","FAILED","FAIL"}:
        visual="ERROR"; cause="COLLECTION_ERROR"
    elif state in {"BLOCKED_AUTH","BLOCKED","HOLD"}:
        visual="HOLD"; cause=state
    elif age is not None and stale_after is not None and age>stale_after:
        visual="STALE"; cause="EVIDENCE_STALE"
    elif state in {"OK","SUCCESS","PARTIAL"}:
        visual="OK" if state=="OK" else "PARTIAL"; cause="COLLECTION_COMPLETED"
    else:
        visual="UNKNOWN"; cause="UNNORMALIZED_RUN_STATE"

    return {
        "run_id":_safe_text(row.get("run_id"),120),
        "job_key":_safe_text(row.get("job_key"),120),
        "source_class":_safe_text(row.get("source_class"),120),
        "started_at":started.isoformat() if started else None,
        "finished_at":finished.isoformat() if finished else None,
        "duration_seconds":((finished-started).total_seconds() if started and finished else None),
        "age_seconds":age,
        "cadence_seconds":cadence,
        "state":state,"visual_state":visual,"cause":cause,"auth_state":auth,
        "observed":int(row.get("observed") or 0),
        "recorded":int(row.get("recorded") or 0),
        "changed":int(row.get("changed") or 0),
        "conflicts":int(row.get("conflicts") or 0),
        "blocked":int(row.get("blocked") or 0),
        "errors":int(row.get("errors") or 0),
        "detail":_safe_text(row.get("detail")),
        "automatic_ready_paper":False,
    }


def snapshot_view(row) -> dict:
    row=dict(row or {})
    return {
        "family":_safe_text(row.get("family"),80).upper(),
        "ticker":_safe_text(row.get("ticker"),120).upper(),
        "market":_safe_text(row.get("market"),80).upper(),
        "source_class":_safe_text(row.get("source_class"),120),
        "source_ref":_safe_text(row.get("source_ref"),300),
        "observed_at":row.get("observed_at"),
        "effective_at":row.get("effective_at"),
        "evidence_hash":_safe_text(row.get("evidence_hash"),80),
        "automatic_ready_paper":False,
    }


def change_view(row) -> dict:
    row=dict(row or {})
    return {
        "family":_safe_text(row.get("family"),80).upper(),
        "ticker":_safe_text(row.get("ticker"),120).upper(),
        "market":_safe_text(row.get("market"),80).upper(),
        "source_class":_safe_text(row.get("source_class"),120),
        "detected_at":row.get("detected_at"),
        "status":_safe_text(row.get("status"),100),
        "detail":_safe_text(row.get("detail")),
        "requires_review":str(row.get("status") or "").upper() in {"CHANGED_REVIEW_REQUIRED","CONFLICT_REVIEW_REQUIRED"},
    }


def summary(runs, changes=()) -> dict:
    runs=list(runs or []); changes=list(changes or [])
    return {
        "runs":len(runs),
        "errors":sum(int(r.get("errors") or 0) for r in runs),
        "blocked":sum(int(r.get("blocked") or 0) for r in runs),
        "recorded":sum(int(r.get("recorded") or 0) for r in runs),
        "changed":sum(int(r.get("changed") or 0) for r in runs),
        "conflicts":sum(int(r.get("conflicts") or 0) for r in runs),
        "review_required":sum(1 for c in changes if str(c.get("status") or "").upper() in {"CHANGED_REVIEW_REQUIRED","CONFLICT_REVIEW_REQUIRED"}),
        "ready_paper_promotions":0,
    }
