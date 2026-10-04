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


IDENTITY_FIELDS = ("symbol", "asset_class", "settlement", "currency", "market")


def identity(value):
    parts = tuple(str(value.get(k) or "").strip().upper() for k in IDENTITY_FIELDS)
    if any(p in {"", "UNKNOWN", "NO_VERIFICADO"} for p in parts):
        raise ValueError("EXACT_IDENTITY_REQUIRED")
    return parts

