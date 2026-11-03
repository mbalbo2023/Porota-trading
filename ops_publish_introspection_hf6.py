#!/usr/bin/env python3
"""Publica una vista sanitaria mínima de la introspección Porota.

La salida es apta para un repositorio privado: usa listas permitidas explícitas,
no copia logs, rutas, posiciones, cuerpos HTTP, metadatos crudos ni secretos.
GitHub es sólo una copia de observabilidad y nunca una dependencia del runtime.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


MAX_DAILY_SNAPSHOTS = 24
DEFAULT_HOURLY_RETENTION_DAYS = 90
RETENTION_TZ = ZoneInfo("America/Argentina/Buenos_Aires")
EVENT_NAME = re.compile(r"^[A-Z0-9_:-]{1,80}$")
CAUCION_REQUIREMENTS = {
    "instrument_id", "currency", "annual_rate_fraction", "start_date",
    "maturity_at", "quoted_at", "available_principal", "minimum_principal",
    "principal_step", "day_count_basis", "fee_payment", "metadata_source",
    "frozen_at", "reserve_cash", "maximum_cash_fraction", "maximum_principal",
    "liquidity_deadline", "maximum_quote_age_seconds", "participation",
    "minimum_net_profit", "ranking", "session_open_at", "session_close_at",
    "session_source",
}


def _scalar(value, default=None):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return default


def _pick(source, names):
    source = source if isinstance(source, dict) else {}
    return {name: _scalar(source.get(name)) for name in names}


def _named_counts(values, name_key="state", count_key="count"):
    clean = []
    for value in values if isinstance(values, list) else []:
        name = str(value.get(name_key, "")) if isinstance(value, dict) else ""
        if EVENT_NAME.fullmatch(name):
            clean.append({name_key: name, count_key: int(value.get(count_key) or 0)})
    return clean[:50]


def sanitize(report):
    """Construye una nueva estructura; nunca elimina claves sobre el original."""
    if not isinstance(report, dict):
        raise ValueError("INTROSPECTION_NOT_AN_OBJECT")
    observer = _pick(report.get("observer"), (
        "mode", "process_state", "session_state", "ppi_auth", "heartbeat_at",
        "last_market_data_at", "real_orders_sent", "http_allowed", "http_blocked",
    ))
    workers = {}
    for name, value in (report.get("workers") or {}).items():
        if EVENT_NAME.fullmatch(str(name).upper()):
            workers[str(name)] = _pick(value, ("state", "heartbeat_at", "heartbeat_age_seconds"))
    ppi_errors = []
    for row in report.get("ppi_errors_1h") or []:
        event_type = str(row.get("event_type", "")) if isinstance(row, dict) else ""
        if EVENT_NAME.fullmatch(event_type):
            ppi_errors.append({"event_type": event_type, "count": int(row.get("count") or 0)})
    currency_funnel = []
    for row in report.get("currency_funnel") or []:
        currency = str(row.get("currency", "")) if isinstance(row, dict) else ""
        if EVENT_NAME.fullmatch(currency):
            currency_funnel.append({
                "currency": currency,
                "observed_symbols": int(row.get("observed_symbols") or 0),
                "last_observed_at": _scalar(row.get("last_observed_at")),
            })
    cauciones = _pick(report.get("caucion_readiness"), (
        "declared", "queries", "observed_contracts", "complete_offers",
        "placed_today", "state",
    ))
    for field in ("missing_offer_requirements", "missing_policy_requirements"):
        cauciones[field] = [
            str(item) for item in (report.get("caucion_readiness") or {}).get(field, [])
            if str(item) in CAUCION_REQUIREMENTS
        ]
    public = {
        "schema": "porota-introspection-public-v1",
        "version": _scalar(report.get("version")),
        "timestamp": _scalar(report.get("timestamp")),
        "verdict": _scalar(report.get("verdict")),
        "quick_check": _scalar(report.get("quick_check")),
        "observer": observer,
        "trading": _pick(report.get("trading"), (
            "decisions_1h", "buys_1h", "sells_1h", "open", "closed_today", "net_pnl_today",
        )),
        "coherence": _pick(report.get("coherence"), (
            "orphan_fills", "open_without_buy", "closed_without_sell",
            "open_without_exit_intent", "open_over_max_hold",
        )),
        "workers": workers,
        "ingestion": _pick(report.get("ingestion"), (
            "last_market_date", "last_download", "instruments", "rows", "raw",
            "candle_versions", "latest_candle_known_at",
        )),
        "economics": _pick(report.get("economics"), ("evaluated_1h", "blocked_1h", "opened_1h")),
        "scalping": _pick(report.get("scalping"), (
            "evaluated_1h", "rejected_1h", "approved_1h", "paper_positions_today",
        )),
        "learning_expectancy": [
            _pick(item, ("currency", "samples", "wins", "losses", "zeros", "win_rate_pct",
                         "average_win", "average_loss", "empirical_expectancy", "net_total",
                         "profit_factor", "sample_state", "minimum_sample", "binding"))
            for item in (report.get("learning_expectancy") or [])[:10]
            if isinstance(item, dict)
        ],
        "currency_funnel": currency_funnel,
        "market_regime_observation": _pick(report.get("market_regime_observation"), (
            "state", "symbols", "rising", "falling", "unchanged", "minimum_symbols",
            "policy", "binding", "method",
        )),
        "sector_concentration": {
            **_pick(report.get("sector_concentration"), (
                "state", "mapped_positions", "unmapped_positions",
                "maximum_observed_concentration", "policy", "limit", "binding",
            )),
            "groups": [
                _pick(item, ("sector", "open_positions"))
                for item in (report.get("sector_concentration") or {}).get("groups", [])[:30]
                if isinstance(item, dict)
            ],
        },
        "caucion_readiness": cauciones,
        "family_readiness": [
            {
                **_pick(item, ("instrument_type", "declared", "queries", "observed_count",
                               "ready_paper_count", "discovery_status", "checked_at",
                               "excluded_by_default")),
                "capabilities": _named_counts(item.get("capabilities"), "capability", "count"),
            }
            for item in (report.get("family_readiness") or [])[:30]
            if isinstance(item, dict)
        ],
        "ppi_errors_1h": ppi_errors[:20],
        "telegram": {
            str(key): int(value or 0) for key, value in (report.get("telegram") or {}).items()
            if EVENT_NAME.fullmatch(str(key).upper())
        },
        "storage": _pick(report.get("storage"), (
            "filesystem_total", "filesystem_used", "filesystem_free",
            "filesystem_used_pct", "database_bytes", "wal_bytes",
        )),
        "github_publication": _pick(report.get("github_publication"), (
            "status", "recorded_at", "branch", "day", "detail", "retention_days",
        )),
        "warnings": [str(item)[:120] for item in report.get("warnings") or []][:30],
        "anomalies": [str(item)[:120] for item in report.get("anomalies") or []][:30],
        "changes": [str(item)[:160] for item in report.get("changes") or []][:30],
    }
    if not public["timestamp"]:
        raise ValueError("INTROSPECTION_WITHOUT_TIMESTAMP")
    return public


def atomic_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def record_publication_status(output_dir, status, *, day=None, detail=None,
                              retention_days=DEFAULT_HOURLY_RETENTION_DAYS):
    if status not in {"PUBLISHED", "UNCHANGED", "FAILED"}:
        raise ValueError("INVALID_PUBLICATION_STATUS")
    if day is not None and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(day)):
        raise ValueError("INVALID_PUBLICATION_DAY")
    if detail is not None and not re.fullmatch(r"[A-Z0-9_:-]{1,80}", str(detail).upper()):
        raise ValueError("INVALID_PUBLICATION_DETAIL")
    if retention_days < 1:
        raise ValueError("RETENTION_DAYS_MUST_BE_POSITIVE")
    payload = {
        "schema": "porota-introspection-publication-status-v1",
        "status": status,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "branch": "runtime-observability",
        "day": day,
        "detail": str(detail).upper() if detail else None,
        "retention_days": retention_days,
    }
    target = output_dir / "publication_status.json"
    atomic_json(target, payload)
    return target


def source_file(directory):
    candidates = sorted(directory.glob("porota_introspection_hf*_*.json"))
    if not candidates:
        raise FileNotFoundError("NO_INTROSPECTION_SNAPSHOT")
    return candidates[-1]


def _timestamp(value):
    result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("INTROSPECTION_TIMESTAMP_WITHOUT_TIMEZONE")
    return result


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _valid_daily_consolidation(path, day):
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return False
    if not isinstance(payload, dict):
        return False
    if payload.get("schema") != "porota-introspection-daily-v1" or payload.get("date") != day:
        return False
    snapshots = payload.get("snapshots")
    if not isinstance(snapshots, list) or not snapshots:
        return False
    try:
        return all(
            isinstance(item, dict)
            and _timestamp(item.get("timestamp")).date().isoformat() == day
            for item in snapshots
        )
    except (ValueError, TypeError):
        return False


def prune_hourly(input_dir, output_dir, retention_days=DEFAULT_HOURLY_RETENTION_DAYS, now=None):
    """Elimina sólo horarios antiguos ya protegidos por un consolidado diario válido.

    El manifiesto se escribe primero como PLANNED y se reemplaza atómicamente al
    finalizar. Así, una interrupción nunca deja una eliminación sin rastro previo.
    """
    if retention_days < 1:
        raise ValueError("RETENTION_DAYS_MUST_BE_POSITIVE")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("RETENTION_NOW_MUST_HAVE_TIMEZONE")
    cutoff = (now.astimezone(RETENTION_TZ).date() - timedelta(days=retention_days)).isoformat()
    daily_validity = {}
    candidates = []
    skipped = []
    for source in sorted(input_dir.glob("porota_introspection_hf*_*.json")):
        if source.is_symlink() or not source.is_file():
            skipped.append({"file": source.name, "reason": "UNSAFE_FILE_TYPE"})
            continue
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
            day = _timestamp(payload.get("timestamp")).date().isoformat()
        except (OSError, ValueError, TypeError):
            skipped.append({"file": source.name, "reason": "INVALID_SNAPSHOT"})
            continue
        if day >= cutoff:
            continue
        daily = output_dir / "daily" / f"{day}.json"
        if day not in daily_validity:
            daily_validity[day] = _valid_daily_consolidation(daily, day)
        valid = daily_validity[day]
        if not valid:
            skipped.append({"file": source.name, "reason": "NO_VALID_DAILY_CONSOLIDATION"})
            continue
        files = [source]
        companion = source.with_suffix(".md")
        if companion.is_symlink():
            skipped.append({"file": companion.name, "reason": "UNSAFE_FILE_TYPE"})
        elif companion.is_file():
            files.append(companion)
        candidates.extend({
            "_path": str(path),
            "file": path.name,
            "day": day,
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
        } for path in files)

    if not candidates:
        return {
            "status": "NOTHING_TO_PRUNE", "retention_days": retention_days,
            "cutoff_exclusive": cutoff, "deleted_files": 0, "deleted_bytes": 0,
            "skipped": skipped,
        }

    audit_dir = output_dir / "retention"
    audit_stamp = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    audit = audit_dir / f"retention_{audit_stamp}.json"
    sequence = 1
    while audit.exists():
        audit = audit_dir / f"retention_{audit_stamp}_{sequence}.json"
        sequence += 1
    manifest = {
        "schema": "porota-introspection-retention-v1",
        "state": "PLANNED",
        "created_at": now.astimezone(timezone.utc).isoformat(),
        "retention_days": retention_days,
        "cutoff_exclusive": cutoff,
        "scope": "HOURLY_INTROSPECTION_ONLY",
        "protected": ["database", "ledger", "history", "candles", "learning", "daily_snapshots"],
        "planned_files": [
            {key: item[key] for key in ("file", "day", "bytes", "sha256")}
            for item in candidates
        ],
        "deleted_files": [],
        "failures": [],
        "skipped": skipped,
    }
    atomic_json(audit, manifest)
    for item in candidates:
        path = Path(item["_path"])
        try:
            path.unlink()
            manifest["deleted_files"].append({key: item[key] for key in (
                "file", "day", "bytes", "sha256")})
        except OSError as exc:
            manifest["failures"].append({"file": item["file"], "error": type(exc).__name__})
            break
    manifest["state"] = "COMPLETED" if not manifest["failures"] else "PARTIAL"
    manifest["deleted_bytes"] = sum(item["bytes"] for item in manifest["deleted_files"])
    manifest["completed_at"] = datetime.now(timezone.utc).isoformat()
    atomic_json(audit, manifest)
    if manifest["failures"]:
        raise RuntimeError(f"RETENTION_PARTIAL:{audit.name}")
    return {
        "status": "PRUNED", "retention_days": retention_days,
        "cutoff_exclusive": cutoff, "deleted_files": len(manifest["deleted_files"]),
        "deleted_bytes": manifest["deleted_bytes"], "audit": audit.name,
        "skipped": skipped,
    }


def publish(input_dir, output_dir):
    source = source_file(input_dir)
    clean = sanitize(json.loads(source.read_text(encoding="utf-8")))
    stamp = _timestamp(clean["timestamp"])
    day = stamp.date().isoformat()
    latest = output_dir / "latest.json"
    daily = output_dir / "daily" / f"{day}.json"
    history = {"schema": "porota-introspection-daily-v1", "date": day, "snapshots": []}
    if daily.exists():
        if not _valid_daily_consolidation(daily, day):
            raise ValueError("EXISTING_DAILY_CONSOLIDATION_INVALID")
        previous = json.loads(daily.read_text(encoding="utf-8"))
        history["snapshots"] = list(previous["snapshots"])
    hour_key = stamp.strftime("%Y-%m-%dT%H")
    history["snapshots"] = [
        item for item in history["snapshots"]
        if str(item.get("timestamp", ""))[:13] != hour_key
    ]
    history["snapshots"].append(clean)
    history["snapshots"] = sorted(history["snapshots"], key=lambda item: item["timestamp"])[-MAX_DAILY_SNAPSHOTS:]
    atomic_json(latest, clean)
    atomic_json(daily, history)
    return source, latest, daily


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, default=Path("/app/data/introspection"))
    parser.add_argument("--output-dir", type=Path, default=Path("/app/data/introspection_publish"))
    parser.add_argument("--prune-only", action="store_true")
    parser.add_argument("--retention-days", type=int, default=DEFAULT_HOURLY_RETENTION_DAYS)
    parser.add_argument("--record-status", choices=("PUBLISHED", "UNCHANGED", "FAILED"))
    parser.add_argument("--day")
    parser.add_argument("--detail")
    args = parser.parse_args(argv)
    if args.record_status:
        target = record_publication_status(
            args.output_dir, args.record_status, day=args.day, detail=args.detail,
            retention_days=args.retention_days)
        print(json.dumps({"status": "RECORDED", "file": target.name}, separators=(",", ":")))
        return 0
    if args.prune_only:
        result = prune_hourly(args.input_dir, args.output_dir, args.retention_days)
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return 0
    source, latest, daily = publish(args.input_dir, args.output_dir)
    print(json.dumps({"status": "OK", "source": source.name,
                      "latest": latest.name, "daily": daily.name}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
