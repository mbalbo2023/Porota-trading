"""RC6 validation campaign ledger and path-to-production model.

This module is deliberately independent from the trading engine.  It never
changes trading parameters, never enables order routing and never turns a
percentage into authorization for real money.

The ledger is append-only JSONL with a SHA-256 hash chain.  Dashboard reads are
read-only; operational jobs may append evidence records through append_record().
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime
from hashlib import sha256
import fcntl
import json
import os
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Argentina/Buenos_Aires")
DEFAULT_ROOT = Path(os.getenv("POROTA_VALIDATION_ROOT", "/app/data/validation"))
LEDGER_NAME = "validation_campaign_rc6.jsonl"
VALID_STATES = {"GREEN", "YELLOW", "RED", "GRAY"}


@dataclass(frozen=True)
class Milestone:
    code: str
    name: str
    critical: bool
    goal: str
    exit_criteria: tuple[str, ...]


MILESTONES: tuple[Milestone, ...] = (
    Milestone("M0", "Infraestructura y capacidad", True,
              "Infraestructura estable, recuperable y con capacidad suficiente.",
              ("disco", "Docker", "backups/restore", "timers", "restart behavior")),
    Milestone("M1", "Safety invariants", True,
              "PAPER seguro y fail-closed, sin capacidad monetaria real.",
              ("PRODUCTION_PAPER", "SIMULATED", "real_orders_sent=0", "order routing blocked")),
    Milestone("M2", "Fuentes y contratos", True,
              "Identidad, contratos, settlement, calendario y fuentes con provenance.",
              ("PPI read-only", "Contract Evidence", "calendario", "instrument identity", "settlement")),
    Milestone("M3", "Calidad de mercado e históricos", True,
              "Históricos/candles interpretables, frescos y sin lookahead.",
              ("freshness", "depth/density", "sampled semantics", "no lookahead", "A3/PPI provenance")),
    Milestone("M4", "Estabilidad PAPER", True,
              "Varias ruedas interpretables, sin pérdida de estado ni incidentes críticos.",
              ("multiple sessions", "no anomalous restarts", "daily reconciliation", "useful alerts")),
    Milestone("M5", "Realismo de ejecución", True,
              "Simulación con bid/ask, costos, settlement, exits y trazabilidad ejecutable.",
              ("bid/ask", "fees/slippage", "settlement", "exits", "MFE/MAE measured")),
    Milestone("M6", "Evidencia estadística", True,
              "Forward Lab v2 y validación robusta antes de inferir edge.",
              ("HAC/panel", "block bootstrap by trading day", "cohorts", "leave-outs", "multiple testing/DSR")),
    Milestone("M7", "Operabilidad y accesibilidad", True,
              "Operación segura por tablet/Voice Access y observabilidad clara.",
              ("tablet", "Voice Access", "Telegram", "dashboard", "scheduler host truth")),
    Milestone("M8", "Campaña PAPER sostenida", True,
              "Campaña diaria con objetivos, evidencia, lecciones y sign-off por rueda.",
              ("daily objectives", "results", "lessons", "daily sign-off")),
    Milestone("M9", "Auditoría independiente y consenso", True,
              "Auditor, programación, trading, seguridad y DevOps convergen sobre evidencia.",
              ("audit findings closed", "consensus", "evidence links", "no unresolved P0")),
    Milestone("M10", "Governance candidate real-money", True,
              "Proyecto futuro explícito de permisos, límites, kill switch, canary y autorización humana.",
              ("new governance project", "permissions", "limits", "kill switch", "human authorization", "canary")),
    Milestone("M11", "Real-money", True,
              "Hito conceptual final; actualmente bloqueado por diseño.",
              ("explicit future authorization", "all prior gates GREEN", "separate real-money architecture")),
)

MILESTONE_BY_CODE = {m.code: m for m in MILESTONES}


def _canonical(data: dict[str, Any]) -> bytes:
    return json.dumps(data, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), default=str).encode("utf-8")


def _record_hash(record_without_hash: dict[str, Any]) -> str:
    return sha256(_canonical(record_without_hash)).hexdigest()


def ledger_path(root: Path | str | None = None) -> Path:
    base = Path(root) if root is not None else DEFAULT_ROOT
    return base / LEDGER_NAME


def load_records(root: Path | str | None = None, *, verify: bool = True) -> list[dict[str, Any]]:
    path = ledger_path(root)
    if not path.exists():
        return []
    result: list[dict[str, Any]] = []
    previous = "GENESIS"
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        item = json.loads(raw)
        if verify:
            expected = item.get("record_hash")
            body = dict(item)
            body.pop("record_hash", None)
            if item.get("previous_hash") != previous:
                raise ValueError(f"VALIDATION_LEDGER_CHAIN_BREAK:{number}")
            if expected != _record_hash(body):
                raise ValueError(f"VALIDATION_LEDGER_HASH_MISMATCH:{number}")
            previous = str(expected)
        result.append(item)
    return result


def append_record(payload: dict[str, Any], root: Path | str | None = None) -> dict[str, Any]:
    """Append one immutable validation evidence record.

    Caller supplies evidence; this function only validates structure, assigns
    sequence/timestamps and hash-chains the record.  It has no trading imports.
    """
    milestone = str(payload.get("milestone") or "").upper().strip()
    if milestone not in MILESTONE_BY_CODE:
        raise ValueError("VALIDATION_MILESTONE_INVALID")
    state = str(payload.get("state") or "GRAY").upper().strip()
    if state not in VALID_STATES:
        raise ValueError("VALIDATION_STATE_INVALID")
    pct = int(payload.get("compliance_pct", 0))
    if not 0 <= pct <= 100:
        raise ValueError("VALIDATION_PERCENT_INVALID")

    path = ledger_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.seek(0)
        lines = [line for line in handle.read().splitlines() if line.strip()]
        previous = "GENESIS"
        seq = 1
        if lines:
            last = json.loads(lines[-1])
            previous = str(last.get("record_hash") or "")
            seq = int(last.get("seq") or len(lines)) + 1
        now = datetime.now(TZ).isoformat(timespec="seconds")
        body = {
            "schema_version": 1,
            "seq": seq,
            "recorded_at": now,
            "date_ar": str(payload.get("date_ar") or datetime.now(TZ).date().isoformat()),
            "milestone": milestone,
            "objective": str(payload.get("objective") or MILESTONE_BY_CODE[milestone].goal),
            "expected_evidence": str(payload.get("expected_evidence") or ""),
            "observed_evidence": str(payload.get("observed_evidence") or ""),
            "compliance_pct": pct,
            "state": state,
            "expected": str(payload.get("expected") or ""),
            "observed": str(payload.get("observed") or ""),
            "deviation": str(payload.get("deviation") or ""),
            "root_cause": str(payload.get("root_cause") or ""),
            "lesson": str(payload.get("lesson") or ""),
            "decision": str(payload.get("decision") or ""),
            "blocker": str(payload.get("blocker") or ""),
            "next_action": str(payload.get("next_action") or ""),
            "owner": str(payload.get("owner") or "POROTA"),
            "evidence_ref": str(payload.get("evidence_ref") or ""),
            "commit_release": str(payload.get("commit_release") or ""),
            "critical_path": bool(payload.get("critical_path", MILESTONE_BY_CODE[milestone].critical)),
            "source": str(payload.get("source") or "MANUAL_OR_AUTOMATION_EVIDENCE"),
            "previous_hash": previous,
        }
        record = {**body, "record_hash": _record_hash(body)}
        handle.seek(0, 2)
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    return record


def latest_by_milestone(records: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in records:
        code = str(row.get("milestone") or "")
        if code in MILESTONE_BY_CODE:
            latest[code] = row
    return latest


def project_summary(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = list(records)
    latest = latest_by_milestone(rows)
    green = sum(1 for m in MILESTONES if latest.get(m.code, {}).get("state") == "GREEN")
    red_critical = [m.code for m in MILESTONES
                    if m.critical and latest.get(m.code, {}).get("state") == "RED"]
    # This count is descriptive only.  Weighted readiness remains disabled
    # until the operator explicitly approves milestone weights.
    completed_ratio = green / len(MILESTONES) if MILESTONES else 0.0
    return {
        "milestones_total": len(MILESTONES),
        "milestones_green": green,
        "descriptive_completion_pct": round(completed_ratio * 100, 1),
        "weighted_readiness_pct": None,
        "weighted_readiness_reason": "MILESTONE_WEIGHTS_NOT_OPERATOR_APPROVED",
        "critical_red": red_critical,
        "real_money_authorized": False,
        "real_money_state": "BLOCKED",
        "records": len(rows),
    }


def milestone_definitions() -> list[dict[str, Any]]:
    return [asdict(m) for m in MILESTONES]
