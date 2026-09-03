"""RC4 backup coverage model.

Pure inventory logic for dashboard/predeploy.  A persistence target must never be
silently absent from the backup page: it is PROTECTED, STALE_BACKUP, NO_BACKUP,
NOT_CREATED or ERROR.  SQLite restore verification and non-SQLite stores are kept
separate to avoid fake equivalence.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class StorageTarget:
    key: str
    label: str
    kind: str
    path: str
    required: bool = True


CANONICAL_TARGETS = (
    StorageTarget("observer_db", "Observer / paper ledger", "SQLITE", "data/paper_v17/observer_v17.db"),
    StorageTarget("history_db", "History Store v2", "SQLITE", "data/market_history.db"),
    StorageTarget("sre_vector_db", "SRE Vector DB", "DIRECTORY", "sre_vector_db"),
)


def _parse(value):
    if not value:
        return None
    try:
        dt=datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt=dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def evaluate_target(target: StorageTarget, *, exists: bool, backup=None,
                    now=None, stale_after_seconds=36*3600) -> dict:
    backup=dict(backup or {})
    now_dt=_parse(now) or datetime.now(timezone.utc)
    last=_parse(backup.get("finished_at") or backup.get("created_at") or backup.get("started_at"))
    age=(now_dt-last).total_seconds() if last else None
    backup_state=str(backup.get("state") or "").upper()
    restore_state=str(backup.get("restore_check") or backup.get("quick_check") or "").lower()

    if not exists:
        state="NOT_CREATED"
        reason="El storage aún no existe; no presentar backup ficticio."
    elif backup_state in {"ERROR", "FAILED", "FAIL", "ROJO"}:
        state="ERROR"
        reason=str(backup.get("detail") or "La última copia reportó error.")
    elif not backup:
        state="NO_BACKUP"
        reason="Storage existente sin evidencia de backup."
    elif last is None:
        state="NO_BACKUP"
        reason="Existe registro de backup pero no timestamp utilizable."
    elif age > int(stale_after_seconds):
        state="STALE_BACKUP"
        reason=f"Última copia hace {int(age)} s; supera ventana {int(stale_after_seconds)} s."
    elif target.kind == "SQLITE" and restore_state not in {"ok", "pass", "passed"}:
        state="ERROR"
        reason="Backup SQLite sin restore/quick_check exitoso demostrable."
    else:
        state="PROTECTED"
        reason="Backup reciente con evidencia compatible con el tipo de storage."

    return {
        **asdict(target),
        "exists":bool(exists),
        "state":state,
        "reason":reason,
        "last_backup_at":last.isoformat() if last else None,
        "age_seconds":age,
        "backup_file":backup.get("backup_file") or backup.get("path"),
        "bytes":backup.get("bytes") or backup.get("size_bytes"),
        "sha256":backup.get("sha256"),
        "restore_check":backup.get("restore_check") or backup.get("quick_check"),
        "retention":backup.get("retention"),
        "next_run_at":backup.get("next_run_at"),
        "source":backup.get("source") or "SIN_EVIDENCIA",
    }


def coverage(targets=None, *, existence=None, backup_by_key=None, now=None) -> list[dict]:
    existence=dict(existence or {})
    backup_by_key=dict(backup_by_key or {})
    targets=tuple(targets or CANONICAL_TARGETS)
    return [evaluate_target(t, exists=bool(existence.get(t.key)),
                            backup=backup_by_key.get(t.key), now=now)
            for t in targets]


def release_blockers(rows) -> list[str]:
    blockers=[]
    for row in rows or []:
        if row.get("required") and row.get("exists") and row.get("state") in {"NO_BACKUP", "STALE_BACKUP", "ERROR"}:
            blockers.append(f"{row.get('key')}:{row.get('state')}")
    return blockers
