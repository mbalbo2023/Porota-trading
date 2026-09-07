"""RC6 multi-source historical reconciliation policy (offline/shadow only).

This module deliberately does NOT write Porota databases and is not imported by
the live observer.  It defines the selection contract that must be proven
before any PPI/IOL/A3 multi-source canonical writer is authorized.

Selection order for one *complete* financial identity/date/price basis:
  1. exact identity and exact price basis;
  2. valid evidence only;
  3. completeness class;
  4. source authority;
  5. observation freshness only inside otherwise equivalent evidence.

No averaging is permitted.  RAW and ADJUSTED are separate series.  Ambiguous
identity or settlement is fail-closed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Iterable

QUALITY_ORDER = {
    "FULL_OHLCV": 0,
    "FULL_OHLC": 1,
    "CLOSE_ONLY": 2,
    "INVALID": 90,
    "IDENTITY_UNVERIFIED": 91,
    "SOURCE_DISCREPANCY": 92,
}
SOURCE_RANK = {
    "PPI_PRODUCTION_HISTORY": 10,
    "PPI_API": 10,
    "BYMA_EOD": 20,
    "BYMA": 20,
    "A3_CEM_CLOSING": 20,
    "IOL": 30,
    "DATA912": 50,
    "DATA912_POROTA_BATCH": 50,
    "YAHOO": 90,
}
ELIGIBLE_QUALITY = frozenset({"FULL_OHLCV", "FULL_OHLC", "CLOSE_ONLY"})
PRICE_BASES = frozenset({"RAW", "ADJUSTED"})


class HistoryReconciliationError(ValueError):
    pass


@dataclass(frozen=True)
class HistoryIdentity:
    symbol: str
    instrument_type: str
    market: str
    settlement: str
    date: str
    price_basis: str

    def normalized(self) -> "HistoryIdentity":
        return HistoryIdentity(
            str(self.symbol or "").strip().upper(),
            str(self.instrument_type or "").strip().upper(),
            str(self.market or "").strip().upper(),
            str(self.settlement or "").strip().upper(),
            str(self.date or "")[:10],
            str(self.price_basis or "").strip().upper(),
        )

    def validate(self) -> None:
        x=self.normalized()
        if any(v in {"", "UNKNOWN"} for v in (x.symbol,x.instrument_type,x.market,x.settlement,x.date)):
            raise HistoryReconciliationError("IDENTITY_UNVERIFIED")
        try:
            datetime.fromisoformat(x.date)
        except ValueError as exc:
            raise HistoryReconciliationError("DATE_INVALID") from exc
        if x.price_basis not in PRICE_BASES:
            raise HistoryReconciliationError("PRICE_BASIS_UNVERIFIED")


@dataclass(frozen=True)
class HistoryCandidate:
    identity: HistoryIdentity
    source: str
    observed_at: str
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: float | None
    payload_hash: str = ""
    provenance: str = ""

    def normalized_source(self) -> str:
        return str(self.source or "").strip().upper()


def _d(value) -> Decimal | None:
    if value is None:
        return None
    try:
        x=Decimal(str(value))
    except (InvalidOperation,ValueError,TypeError):
        return None
    return x if x.is_finite() else None


def classify(candidate: HistoryCandidate) -> str:
    """Classify without repairing or synthesizing missing fields."""
    try:
        candidate.identity.validate()
    except HistoryReconciliationError:
        return "IDENTITY_UNVERIFIED"
    close=_d(candidate.close)
    if close is None or close <= 0:
        return "INVALID"
    o,h,l,v=map(_d,(candidate.open,candidate.high,candidate.low,candidate.volume))
    for x in (o,h,l):
        if x is not None and x <= 0:
            return "INVALID"
    if v is not None and v < 0:
        return "INVALID"
    if o is not None and h is not None and l is not None:
        if not (l <= min(o,close) <= max(o,close) <= h):
            return "INVALID"
        return "FULL_OHLCV" if v is not None else "FULL_OHLC"
    # A partial OHLC shape is not a candle; only a pure close-only observation
    # is eligible for the separate low-completeness class.
    if any(x is not None for x in (o,h,l)):
        return "INVALID"
    return "CLOSE_ONLY"


def source_rank(source: str) -> int:
    s=str(source or "").strip().upper()
    for prefix,rank in SOURCE_RANK.items():
        if s.startswith(prefix): return rank
    return 100


def _observed_key(value: str) -> datetime:
    try:
        dt=datetime.fromisoformat(str(value).replace("Z","+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    if dt.tzinfo is None: dt=dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def exact_identity_match(a: HistoryIdentity, b: HistoryIdentity) -> bool:
    return a.normalized() == b.normalized()


def choose_winner(candidates: Iterable[HistoryCandidate]) -> dict:
    """Choose one candidate for one exact identity+basis, with audit reason.

    This function is pure: it performs no DB or network I/O.
    """
    items=list(candidates)
    if not items:
        return {"winner":None,"reason":"NO_EVIDENCE","evaluated":0}
    anchor=items[0].identity.normalized(); anchor.validate()
    for c in items:
        c.identity.validate()
        if not exact_identity_match(anchor,c.identity):
            raise HistoryReconciliationError("IDENTITY_MIXED_OR_PRICE_BASIS_MIXED")
    classified=[(c,classify(c)) for c in items]
    valid=[(c,q) for c,q in classified if q in ELIGIBLE_QUALITY]
    if not valid:
        return {"winner":None,"reason":"NO_VALID_EVIDENCE","evaluated":len(items),"classes":[q for _,q in classified]}
    valid.sort(key=lambda cq:(
        QUALITY_ORDER[cq[1]],
        source_rank(cq[0].source),
        -_observed_key(cq[0].observed_at).timestamp(),
        cq[0].normalized_source(),
        cq[0].payload_hash,
    ))
    winner,quality=valid[0]
    reason=f"QUALITY={quality};SOURCE_RANK={source_rank(winner.source)};PRICE_BASIS={anchor.price_basis}"
    return {
        "winner":winner,
        "quality":quality,
        "reason":reason,
        "evaluated":len(items),
        "classes":[q for _,q in classified],
        "canonical_key":anchor,
    }


def discrepancy(a: HistoryCandidate,b: HistoryCandidate,*,relative_tolerance:float=0.005) -> dict:
    """Compare two exact-identity observations; never average the result."""
    if not exact_identity_match(a.identity,b.identity):
        raise HistoryReconciliationError("IDENTITY_MIXED_OR_PRICE_BASIS_MIXED")
    ca,cb=_d(a.close),_d(b.close)
    if ca is None or cb is None or ca <= 0 or cb <= 0:
        return {"status":"INVALID","relative_close_diff":None}
    diff=abs(ca-cb)/max(abs(ca),abs(cb))
    return {
        "status":"SOURCE_DISCREPANCY" if diff > Decimal(str(relative_tolerance)) else "WITHIN_TOLERANCE",
        "relative_close_diff":float(diff),
        "action":"AUDIT_NO_AVERAGE" if diff > Decimal(str(relative_tolerance)) else "NO_ACTION",
    }


def assert_shadow_only() -> None:
    # Deliberate static capability declaration for CI and governance.
    assert not hasattr(HistoryCandidate,"append")
    assert PRICE_BASES == frozenset({"RAW","ADJUSTED"})
