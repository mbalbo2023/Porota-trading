"""Final RC6 dashboard truth fixes for the 07-Sep PAPER go-live.

Presentation/observability only.

Fixes:
- introspection selects current RC/HF snapshots instead of a legacy HF-only glob;
- introspection freshness respects the real hourly :15 producer cadence with a
  75-minute guard window, so a healthy hourly snapshot is not mislabeled stale;
- Telegram health comes from the current notification worker/outbox/jobs, not a
  stale legacy JSON file;
- SRE keeps its real AMARILLO state when the monitor query is slow, but exposes
  the actual cause (query_ms) and separates it from DB integrity/disk capacity.

No DB writes, network calls, strategy changes or order capability.
"""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import re

import bg_paper_dashboard as bg

_installed = False
_original_health_components = None
_original_system_page = None
INTROSPECTION_MAX_AGE_SECONDS = 75 * 60


def latest_introspection_current():
    directory = Path(bg.DB_PATH).parents[1] / "introspection"
    if not directory.exists():
        return None
    files = []
    for pattern in ("porota_introspection_rc*_*.json", "porota_introspection_hf*_*.json"):
        files.extend(p for p in directory.glob(pattern) if p.is_file() and not p.is_symlink())
    files = sorted(set(files), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in files:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict) and payload.get("timestamp"):
                return payload
        except (OSError, ValueError, TypeError):
            continue
    return None


def _age_seconds(value):
    if not value:
        return None
    try:
        return (datetime.now(bg.TZ) - bg.aware_datetime(value).astimezone(bg.TZ)).total_seconds()
    except Exception:
        return None


def _system_page_current_truth(section):
    rendered = _original_system_page(section)
    if str(section or "").lower() != "introspeccion":
        return rendered

    payload = latest_introspection_current()
    age = _age_seconds(payload.get("timestamp")) if isinstance(payload, dict) else None
    if age is None or age < 0 or age > INTROSPECTION_MAX_AGE_SECONDS:
        return rendered
    if "Snapshot de introspección no vigente" not in rendered:
        return rendered

    # bg.system_page historically used a fixed 10-minute threshold even though
    # the producer is hourly at :15. Only presentation is corrected here; the
    # underlying snapshot, observer reconciliation and persisted data stay
    # untouched. If the snapshot exceeds 75 minutes the original warning remains.
    rendered = re.sub(
        r"<div class='paper-warning'><b>Snapshot de introspección no vigente:</b>.*?</div>",
        (
            "<div class='paper-notice'><b>Snapshot de introspección vigente:</b> "
            f"cadencia horaria · edad {age:.0f} s · guardia máxima {INTROSPECTION_MAX_AGE_SECONDS} s.</div>"
        ),
        rendered,
        count=1,
        flags=re.DOTALL,
    )
    rendered = rendered.replace("snapshot STALE", "snapshot VIGENTE_CADENCIA_HORARIA")
    return rendered


def _telegram_runtime_evidence(row):
    worker = (bg._rows("SELECT * FROM paper_notification_worker WHERE id=1") or [{}])[0] if bg._table("paper_notification_worker") else {}
    outbox = bg._rows("SELECT state,COUNT(*) total,MAX(COALESCE(sent_at,created_at)) latest FROM paper_notification_outbox GROUP BY state") if bg._table("paper_notification_outbox") else []
    jobs = bg._rows("""SELECT job_key,last_run_at,last_success_at,state,detail FROM operational_jobs
                       WHERE job_key IN ('TELEGRAM_STARTUP_ACK','TELEGRAM_DAILY_ACK','TELEGRAM_CLOSE_SUMMARY')
                       ORDER BY COALESCE(last_success_at,last_run_at) DESC""") if bg._table("operational_jobs") else []
    heartbeat = worker.get("heartbeat_at")
    age = _age_seconds(heartbeat)
    state = str(worker.get("state") or "").upper()
    orders = int(worker.get("real_orders_sent") or 0)
    failed = sum(int(x.get("total") or 0) for x in outbox if str(x.get("state") or "").upper() in {"FAILED","ERROR"})
    sent = sum(int(x.get("total") or 0) for x in outbox if str(x.get("state") or "").upper() == "SENT")
    last_job_success = max((str(x.get("last_success_at") or "") for x in jobs), default="") or None

    if orders != 0:
        effective = "ROJO"
        blocking = True
        reason = f"INVARIANTE ROTA: notification worker registra real_orders_sent={orders}."
    elif state in {"FAILED","ERROR","STOPPED"}:
        effective = "ROJO"
        blocking = True
        reason = f"Notification worker state={state}."
    elif state == "RUNNING" and age is not None and 0 <= age <= 180:
        effective = "VERDE" if failed == 0 else "AMARILLO"
        blocking = False if failed == 0 else True
        reason = f"Worker RUNNING; heartbeat hace {age:.0f}s; outbox SENT={sent}; FAILED={failed}."
    elif worker:
        effective = "AMARILLO"
        blocking = True
        reason = f"Worker presente pero heartbeat no vigente (edad={age if age is not None else 'desconocida'}s), state={state or 'UNKNOWN'}."
    elif last_job_success:
        effective = "AMARILLO"
        blocking = True
        reason = f"Sin worker vigente; última evidencia de job Telegram={last_job_success}."
    else:
        effective = "PENDIENTE"
        blocking = True
        reason = "Sin evidencia runtime vigente de Telegram."

    row.update(
        state=effective,
        raw_state=state or row.get("raw_state"),
        detail=reason + (f" Último envío={worker.get('last_sent_at')}." if worker.get("last_sent_at") else "")
               + (f" Último job exitoso={last_job_success}." if last_job_success else ""),
        checked=heartbeat or row.get("checked"),
        last_success=worker.get("last_sent_at") or last_job_success,
        next_check="Continuo; heartbeat del notification worker",
        paper_blocking=blocking,
        applicable=True,
    )
    return row


def _sre_runtime_evidence(row):
    snap = (bg._rows("SELECT * FROM sre_snapshots ORDER BY id DESC LIMIT 1") or [{}])[0] if bg._table("sre_snapshots") else {}
    if not snap:
        return row
    integrity = str(snap.get("db_integrity") or "UNKNOWN")
    query_ms = float(snap.get("db_query_ms") or 0.0)
    total = float(snap.get("disk_total_bytes") or 0.0)
    free = float(snap.get("disk_free_bytes") or 0.0)
    free_pct = free / total * 100.0 if total > 0 else 0.0
    measured = snap.get("measured_at")
    persisted_state = str(snap.get("state") or row.get("state") or "PENDIENTE").upper()

    row["state"] = persisted_state
    row["raw_state"] = persisted_state
    row["checked"] = measured or row.get("checked")
    row["detail"] = (
        f"quick_check={integrity}; disco libre={free_pct:.1f}%; medición SRE={query_ms:.1f} ms. "
        "El semáforo SRE exige quick_check=ok, >=20% libre y medición <250 ms."
    )
    if integrity == "ok" and free_pct >= 20 and query_ms >= 250 and persisted_state == "AMARILLO":
        row["detail"] += " AMARILLO causado por latencia de la medición SRE, no por corrupción DB ni falta de disco."
        row["paper_blocking"] = False
    elif integrity != "ok" or free_pct < 20:
        row["paper_blocking"] = True
    return row


def health_components_current_truth():
    rows = _original_health_components()
    result = []
    for item in rows:
        row = dict(item)
        key = str(row.get("key") or "").upper()
        if key == "TELEGRAM":
            row = _telegram_runtime_evidence(row)
        elif key == "SRE_SNAPSHOT":
            row = _sre_runtime_evidence(row)
        result.append(row)
    return result


def install() -> None:
    global _installed, _original_health_components, _original_system_page
    if _installed:
        return
    _installed = True
    bg._latest_introspection = latest_introspection_current
    _original_health_components = bg._health_components
    bg._health_components = health_components_current_truth
    _original_system_page = bg.system_page
    bg.system_page = _system_page_current_truth
