"""Strict quantities, aware clocks and reproducible, non-secret provenance."""
import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal


def number(value, *, positive=False, nonnegative=False):
    if isinstance(value, bool) or value is None:
        raise ValueError("INVALID_NUMBER")
    try:
        result = Decimal(str(value))
    except Exception as exc:
        raise ValueError("INVALID_NUMBER") from exc
    if not result.is_finite() or (positive and result <= 0) or (nonnegative and result < 0):
        raise ValueError("INVALID_NUMBER")
    return result


def stamp(value):
    result = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("AWARE_TIMESTAMP_REQUIRED")
    return result.astimezone(timezone.utc)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def decision_snapshot_phase(payload):
    """Distinguish an immutable financial receipt from its native decision.

    Both phases use the existing canonical evidence store. A receipt recorded
    inside the financial transaction cannot claim to know the later COMMIT
    clock, and must never count as another signal or another trade.
    """
    phase = payload.get("capture_phase", "NATIVE_DECISION")
    if phase == "NATIVE_DECISION":
        return phase
    if phase != "ATOMIC_PAPER_ADMISSION":
        raise ValueError("IMMUTABLE_ADMISSION_PHASE_INVALID")
    key, native = payload.get("decision_key"), payload.get("native_decision_key")
    if not isinstance(key, str) or not key:
        raise ValueError("IMMUTABLE_ADMISSION_KEY_INVALID")
    if native is not None:
        if not isinstance(native, str) or not native or key != "PAPER_ADMISSION:" + native:
            raise ValueError("IMMUTABLE_ADMISSION_KEY_INVALID")
    elif not key.startswith(("PAPER_FILL:", "PAPER_FUTURE_FILL:")):
        raise ValueError("IMMUTABLE_ADMISSION_KEY_INVALID")
    runtime = payload.get("runtime") or {}
    if payload.get("entry_fill_committed_at") is not None or runtime.get("entry_fill_committed_at") is not None:
        raise ValueError("IMMUTABLE_ADMISSION_COMMIT_CLOCK_FABRICATED")
    recorded = stamp(payload.get("entry_fill_recorded_at"))
    if stamp(payload.get("captured_at")) != recorded or stamp(payload.get("admission_at")) > recorded:
        raise ValueError("IMMUTABLE_ADMISSION_CLOCK_INVALID")
    for field in ("signal_at", "decision_at", "intent_at"):
        if payload.get(field) is not None and stamp(payload[field]) > recorded:
            raise ValueError("IMMUTABLE_ADMISSION_CLOCK_INVALID")
    return phase


IDENTITY_FIELDS = ("symbol", "asset_class", "settlement", "currency", "market")


def identity(value):
    parts = tuple(str(value.get(k) or "").strip().upper() for k in IDENTITY_FIELDS)
    if any(p in {"", "UNKNOWN", "NO_VERIFICADO"} for p in parts):
        raise ValueError("EXACT_IDENTITY_REQUIRED")
    return parts
