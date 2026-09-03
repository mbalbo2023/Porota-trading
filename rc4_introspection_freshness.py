"""RC4 freshness model for System -> Introspection.

A deep introspection file is historical evidence, not automatically the current
runtime state.  This module reconciles its timestamp/embedded observer heartbeat
with the live ``observer_state`` row supplied by the dashboard.

Pure module: no DB, filesystem, network or runtime mutation.
"""
from __future__ import annotations

from datetime import datetime, timezone


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


def reconcile(snapshot: dict | None, live_observer: dict | None, *, now=None,
              expected_deep_cadence_seconds: int=3600,
              supersede_tolerance_seconds: int=120) -> dict:
    snapshot=dict(snapshot or {})
    live=dict(live_observer or {})
    now_dt=_dt(now) if now is not None else datetime.now(timezone.utc)
    if now_dt is None:
        now_dt=datetime.now(timezone.utc)

    snapshot_at=_dt(snapshot.get("timestamp") or snapshot.get("generated_at") or
                    snapshot.get("recorded_at"))
    embedded=dict(snapshot.get("observer") or {})
    embedded_heartbeat=_dt(embedded.get("heartbeat_at"))
    live_heartbeat=_dt(live.get("heartbeat_at"))

    if snapshot_at is None:
        snapshot_state="NO_SNAPSHOT" if not snapshot else "SNAPSHOT_TIMESTAMP_INVALID"
        snapshot_age=None
    else:
        snapshot_age=max(0.0,(now_dt-snapshot_at).total_seconds())
        snapshot_state=("FRESH" if snapshot_age <= max(60,int(expected_deep_cadence_seconds)+300)
                        else "STALE")

    superseded=False
    delta=None
    if live_heartbeat is not None:
        reference=embedded_heartbeat or snapshot_at
        if reference is not None:
            delta=(live_heartbeat-reference).total_seconds()
            superseded=delta > max(0,int(supersede_tolerance_seconds))

    embedded_state=(str(embedded.get("process_state") or "UNKNOWN"),
                    str(embedded.get("session_state") or "UNKNOWN"))
    live_state=(str(live.get("process_state") or "UNKNOWN"),
                str(live.get("session_state") or "UNKNOWN"))
    state_changed=bool(live) and embedded_state != live_state
    if superseded and state_changed:
        display_state="SUPERSEDED_BY_LIVE_STATE"
    elif snapshot_state in {"NO_SNAPSHOT","SNAPSHOT_TIMESTAMP_INVALID","STALE"}:
        display_state=snapshot_state
    else:
        display_state="CURRENT_DEEP_SNAPSHOT"

    # The current-state card must use live observer evidence whenever it exists.
    current_observer=live if live else embedded
    return {
        "display_state":display_state,
        "snapshot_state":snapshot_state,
        "snapshot_at":snapshot.get("timestamp") or snapshot.get("generated_at") or snapshot.get("recorded_at"),
        "snapshot_age_seconds":snapshot_age,
        "live_heartbeat_at":live.get("heartbeat_at"),
        "live_newer_by_seconds":delta,
        "state_changed_since_snapshot":state_changed,
        "current_observer":current_observer,
        "snapshot_observer":embedded,
        "deep_verdict":snapshot.get("verdict","UNKNOWN") if snapshot else "NO_SNAPSHOT",
        "warning":(
            "El snapshot profundo es anterior al estado vivo; se conserva como evidencia histórica pero no describe el estado actual."
            if display_state=="SUPERSEDED_BY_LIVE_STATE" else
            "El snapshot profundo está vencido; no se presenta como salud actual."
            if display_state=="STALE" else
            "No existe snapshot profundo legible."
            if display_state in {"NO_SNAPSHOT","SNAPSHOT_TIMESTAMP_INVALID"} else ""
        ),
    }
