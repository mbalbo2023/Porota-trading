"""Small strict contracts shared by the SHADOW planners."""
from datetime import datetime, timezone
import hashlib
import json
import math


def stamp(value):
    at = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("AWARE_TIMESTAMP_REQUIRED")
    return at.astimezone(timezone.utc)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False, default=str).encode()).hexdigest()


def identity(record):
    keys = ("ticker", "instrument_type", "market", "currency", "settlement")
    result = tuple(str(record.get(k) or "").strip().upper() for k in keys)
    if any(not x or x == "UNKNOWN" for x in result):
        raise ValueError("EXACT_IDENTITY_REQUIRED")
    return result


def number(value, *, minimum=0):
    result = float(value)
    if isinstance(value, bool) or not math.isfinite(result) or result < minimum:
        raise ValueError("INVALID_NUMBER")
    return result


def percentile(values, fraction):
    ordered = sorted(values)
    if not ordered:
        return None
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]
