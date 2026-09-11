"""RC6 deterministic A3 identity mapper (offline/shadow only).

Maps only symbol forms whose equivalence can be proven deterministically.
Initial scope is A3 US-dollar futures. It performs no network/DB I/O and
never promotes an instrument to READY_PAPER.

Observed contracts:
- POROTA/PPI style: DLR/SEP26
- A3 compact style: DLR092026

Anything outside the explicit grammar is ALIGNMENT_UNVERIFIED.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

MONTHS = {
    1: "JAN", 2: "FEB", 3: "MAR", 4: "APR", 5: "MAY", 6: "JUN",
    7: "JUL", 8: "AUG", 9: "SEP", 10: "OCT", 11: "NOV", 12: "DEC",
}
MONTH_NUM = {v: k for k, v in MONTHS.items()}
MONTH_NUM.update({"ENE": 1, "ABR": 4, "AGO": 8, "DIC": 12})


class A3IdentityError(ValueError):
    pass


@dataclass(frozen=True)
class A3AlignedIdentity:
    porota_symbol: str
    a3_symbol: str
    instrument_type: str
    market: str
    expiry_year: int
    expiry_month: int
    alignment_status: str = "EXACT_DETERMINISTIC"
    canonical_write: str = "DENY"


def _year4(two_or_four: str) -> int:
    raw = str(two_or_four)
    if len(raw) == 2:
        year = 2000 + int(raw)
    elif len(raw) == 4:
        year = int(raw)
    else:
        raise A3IdentityError("A3_YEAR_UNVERIFIED")
    if not 2020 <= year <= 2099:
        raise A3IdentityError("A3_YEAR_UNVERIFIED")
    return year


def parse_porota_dlr(symbol: str) -> tuple[int, int]:
    s = str(symbol or "").strip().upper()
    match = re.fullmatch(r"DLR/([A-Z]{3})(\d{2}|\d{4})", s)
    if not match or match.group(1) not in MONTH_NUM:
        raise A3IdentityError("A3_ALIGNMENT_UNVERIFIED")
    return _year4(match.group(2)), MONTH_NUM[match.group(1)]


def parse_a3_dlr(symbol: str) -> tuple[int, int]:
    s = str(symbol or "").strip().upper()
    match = re.fullmatch(r"DLR(0[1-9]|1[0-2])(20\d{2})", s)
    if not match:
        raise A3IdentityError("A3_ALIGNMENT_UNVERIFIED")
    return int(match.group(2)), int(match.group(1))


def align_dlr(*, porota_symbol: str, a3_symbol: str) -> A3AlignedIdentity:
    py, pm = parse_porota_dlr(porota_symbol)
    ay, am = parse_a3_dlr(a3_symbol)
    if (py, pm) != (ay, am):
        raise A3IdentityError("A3_EXPIRY_MISMATCH")
    canonical = f"DLR/{MONTHS[pm]}{str(py)[-2:]}"
    compact = f"DLR{pm:02d}{py:04d}"
    return A3AlignedIdentity(canonical, compact, "FUTUROS", "A3", py, pm)


def porota_to_a3_dlr(symbol: str) -> str:
    year, month = parse_porota_dlr(symbol)
    return f"DLR{month:02d}{year:04d}"


def a3_to_porota_dlr(symbol: str) -> str:
    year, month = parse_a3_dlr(symbol)
    return f"DLR/{MONTHS[month]}{str(year)[-2:]}"


def alignment_or_unverified(*, porota_symbol: str, a3_symbol: str) -> dict:
    try:
        value = align_dlr(porota_symbol=porota_symbol, a3_symbol=a3_symbol)
        return {"status": value.alignment_status, "identity": value, "canonical_write": "DENY"}
    except A3IdentityError as exc:
        return {"status": "ALIGNMENT_UNVERIFIED", "reason": str(exc), "identity": None, "canonical_write": "DENY"}


def assert_shadow_only() -> None:
    assert A3AlignedIdentity.__dataclass_fields__["canonical_write"].default == "DENY"
