"""HF6 Contract/Data v2: policy for progressive historical ingestion.

Historical collection and PAPER readiness are deliberately independent. A HOLD
family may accumulate verified history before contract/sizing/settlement rules
are ready.

Policy proven from the 02-Sep-2026 HF6 audit:
- 169 PPI payloads were PARTIAL but contained 31,654 valid daily bars;
- 157/169 partial identities already had >=90 valid bars;
- one malformed provider row must not invalidate all valid rows;
- no interpolation, forward-fill or synthetic OHLC is allowed;
- PPI remains primary; Data912 is historical batch fallback only for explicitly
  supported families and is never an execution/live-price source.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Mapping
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Argentina/Buenos_Aires")

HISTORY_DATA_FAMILIES = frozenset({
    "ACCIONES", "CEDEARS", "BONOS", "LETRAS", "ON", "OBLIGACIONES",
    "OPCIONES", "FUTUROS", "ETF", "ETFS", "INDICES",
})
SPECIALIZED_HISTORY_FAMILIES = frozenset({"CAUCIONES", "FCI", "FCIS", "LICITACIONES"})
DATA912_FALLBACK_FAMILIES = frozenset({"ACCIONES", "CEDEARS", "BONOS"})

MIN_CONTEXT_BARS = 30
PREFERRED_CONTEXT_BARS = 90
STRONG_CONTEXT_BARS = 180


@dataclass(frozen=True)
class RejectedRow:
    index: int
    reason: str


@dataclass(frozen=True)
class ValidatedHistory:
    valid_rows: tuple[dict, ...]
    rejected_rows: tuple[RejectedRow, ...]
    provider_rows: int
    first_date: str | None
    last_date: str | None

    @property
    def valid_count(self) -> int:
        return len(self.valid_rows)

    @property
    def rejected_count(self) -> int:
        return len(self.rejected_rows)

    @property
    def valid_ratio(self) -> float:
        return self.valid_count / self.provider_rows if self.provider_rows else 0.0

    @property
    def context_state(self) -> str:
        if self.valid_count >= STRONG_CONTEXT_BARS:
            return "STRONG_CONTEXT"
        if self.valid_count >= PREFERRED_CONTEXT_BARS:
            return "PREFERRED_CONTEXT"
        if self.valid_count >= MIN_CONTEXT_BARS:
            return "MINIMUM_CONTEXT"
        if self.valid_count:
            return "INSUFFICIENT_CONTEXT"
        return "NO_VALID_HISTORY"

    @property
    def storage_quality(self) -> str:
        if not self.provider_rows or not self.valid_count:
            return "EMPTY_OR_INVALID"
        if not self.rejected_count:
            return "VALID_PAYLOAD"
        return "VALID_ROWS_WITH_REJECTIONS"


def _dt(value) -> datetime:
    if value in (None, ""):
        raise ValueError("DATE_MISSING")
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError("DATE_INVALID") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TZ)
    return parsed


def _decimal(value, name: str, *, positive=False, nonnegative=False) -> Decimal:
    if value in (None, ""):
        raise ValueError(name + "_MISSING")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(name + "_INVALID") from exc
    if not result.is_finite():
        raise ValueError(name + "_NONFINITE")
    if positive and result <= 0:
        raise ValueError(name + "_NONPOSITIVE")
    if nonnegative and result < 0:
        raise ValueError(name + "_NEGATIVE")
    return result


def validate_provider_history(payload, *, as_of: datetime | None = None,
                              date_from=None, date_to=None) -> ValidatedHistory:
    """Validate PPI-style daily rows independently without repairing them."""
    if not isinstance(payload, list):
        return ValidatedHistory((), (RejectedRow(-1, "PAYLOAD_NOT_LIST"),), 0, None, None)

    at = as_of or datetime.now(TZ)
    if at.tzinfo is None:
        at = at.replace(tzinfo=TZ)
    seen: set[str] = set()
    accepted: list[tuple[datetime, dict]] = []
    rejected: list[RejectedRow] = []

    for index, row in enumerate(payload):
        try:
            if not isinstance(row, Mapping):
                raise ValueError("ROW_NOT_OBJECT")
            source_at = _dt(row.get("date"))
            if source_at > at:
                raise ValueError("DATE_IN_FUTURE")
            source_day = source_at.astimezone(TZ).date()
            if date_from is not None and source_day < date_from:
                raise ValueError("DATE_BEFORE_REQUEST")
            if date_to is not None and source_day > date_to:
                raise ValueError("DATE_AFTER_REQUEST")
            key = source_at.isoformat()
            if key in seen:
                raise ValueError("DUPLICATE_DATE")

            opening = _decimal(row.get("openingPrice"), "OPEN", positive=True)
            high = _decimal(row.get("max"), "HIGH", positive=True)
            low = _decimal(row.get("min"), "LOW", positive=True)
            close = _decimal(row.get("price"), "CLOSE", positive=True)
            _decimal(row.get("volume"), "VOLUME", nonnegative=True)
            if not low <= min(opening, close) <= max(opening, close) <= high:
                raise ValueError("OHLC_INCONSISTENT")

            accepted.append((source_at, dict(row)))
            seen.add(key)
        except ValueError as exc:
            rejected.append(RejectedRow(index, str(exc)))

    accepted.sort(key=lambda item: item[0])
    first = accepted[0][0].isoformat() if accepted else None
    last = accepted[-1][0].isoformat() if accepted else None
    return ValidatedHistory(
        tuple(row for _, row in accepted), tuple(rejected), len(payload), first, last
    )


def history_collection_capability(instrument_type: str, *, status: str,
                                  identity_complete: bool = True) -> str:
    """Return DATA capability only. This function never returns READY_PAPER."""
    family = str(instrument_type or "").upper()
    if str(status or "").upper() != "AVAILABLE":
        return "HOLD_NOT_AVAILABLE"
    if not identity_complete:
        return "HOLD_IDENTITY_INCOMPLETE"
    if family in HISTORY_DATA_FAMILIES:
        return "READONLY_HISTORY_ALLOWED"
    if family in SPECIALIZED_HISTORY_FAMILIES:
        return "SPECIALIZED_HISTORY_ADAPTER_REQUIRED"
    return "UNSUPPORTED_HISTORY_FAMILY"


def context_state(valid_rows: int) -> str:
    n = max(0, int(valid_rows or 0))
    if n >= STRONG_CONTEXT_BARS:
        return "STRONG_CONTEXT"
    if n >= PREFERRED_CONTEXT_BARS:
        return "PREFERRED_CONTEXT"
    if n >= MIN_CONTEXT_BARS:
        return "MINIMUM_CONTEXT"
    if n:
        return "INSUFFICIENT_CONTEXT"
    return "NO_VALID_HISTORY"


def data912_fallback_allowed(instrument_type: str) -> bool:
    """Historical fallback permission only; never execution permission."""
    return str(instrument_type or "").upper() in DATA912_FALLBACK_FAMILIES


def needs_data912_reconciliation(instrument_type: str, valid_rows: int,
                                 latest_state: str | None) -> bool:
    """Select a Porota identity for post-close Data912 batch repair.

    Preferred context is 90 valid daily bars. Stronger context can continue to
    be accumulated later, but Data912 is not hammered merely because an
    otherwise usable PPI series has fewer than 180 bars.
    """
    if not data912_fallback_allowed(instrument_type):
        return False
    state = str(latest_state or "").upper()
    if state in {"EMPTY_OR_INVALID", "ERROR"}:
        return True
    return max(0, int(valid_rows or 0)) < PREFERRED_CONTEXT_BARS


def retry_delay(status: str, attempt_count: int) -> timedelta:
    n = max(1, int(attempt_count or 1))
    state = str(status or "").upper()
    if state == "VALID_PAYLOAD":
        return timedelta(hours=24)
    if state == "VALID_ROWS_WITH_REJECTIONS":
        return timedelta(hours=min(24, 4 * n))
    if state in {"PARTIAL", "EMPTY_OR_INVALID"}:
        return timedelta(hours=min(24, 2 ** min(n, 4)))
    if state == "ERROR":
        return timedelta(minutes=min(360, 15 * (2 ** min(n - 1, 4))))
    return timedelta(hours=2)


def rank_history_target(*, context_state: str, latest_status: str | None,
                        last_attempt_at: datetime | None, attempt_count: int,
                        now: datetime | None = None) -> tuple:
    now = now or datetime.now(TZ)
    if now.tzinfo is None:
        now = now.replace(tzinfo=TZ)
    context_rank = {
        "NO_VALID_HISTORY": 0,
        "INSUFFICIENT_CONTEXT": 1,
        "MINIMUM_CONTEXT": 2,
        "PREFERRED_CONTEXT": 3,
        "STRONG_CONTEXT": 4,
    }.get(str(context_state or ""), 0)
    if last_attempt_at is None:
        return (context_rank, 0, datetime.min.replace(tzinfo=TZ))
    last = last_attempt_at if last_attempt_at.tzinfo else last_attempt_at.replace(tzinfo=TZ)
    due_at = last + retry_delay(latest_status or "", attempt_count)
    due_rank = 0 if due_at <= now else 1
    return (context_rank, due_rank, due_at)
