"""Offline, fail-closed CAFCI daily FCI class reference parser.

Only a captured public XLSX and independently supplied HTTP provenance enter this
module.  It never fetches a provider, maps a CAFCI class to a PPI account, proves
an account fee/cutoff, records a broker receipt, or authorizes a trade.

Observed public 2026-10-30 report: 4,274 class IDs, 3,791 class dates of
2026-10-30, five empty NAV cells and two "(**)" NAV placeholders, 11 nonzero
subscription fees, and 18 nonzero redemption fees.  These are NOT row gates.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from io import BytesIO
from email.utils import parsedate_to_datetime
import re
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile


SOURCE_URL = "https://api.pub.cafci.org.ar/pb_get"
REFERENCE_STATUS = "REFERENCE_ONLY"
RUNTIME_ENTRY_AUTHORITY = False
MAX_COMPRESSED_BYTES = 4_000_000
MAX_UNCOMPRESSED_BYTES = 20_000_000
MAX_ZIP_FILES = 40
MAX_ROWS = 8_000
MAX_CLASSES = 6_000

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_FILE_NAME = re.compile(r"^(20\d{6})_Planilla_Diaria_A\.xlsx$")
_COLUMN_NAME = re.compile(r"^([A-Z]+)([1-9][0-9]*)$")
_HEADER = {
    1: "Fondo", 5: "Fecha", 6: "Valor (mil cuotapartes)",
    19: "Codigo CNV", 21: "Código CAFCI", 24: "Sociedad Gerente",
    30: "Comision Ingreso", 34: "Comision Rescate",
    37: "Moneda Fondo", 38: "Plazo Liq.",
}


@dataclass(frozen=True)
class CafciClassReference:
    class_name: str
    cafci_class_id: int
    cnv_fund_id: int
    management_company: str
    currency_raw: str
    nav_effective_date: date | None
    nav_per_thousand: Decimal | None
    nav_per_unit: Decimal | None
    subscription_fee_pct: Decimal
    redemption_fee_pct: Decimal
    settlement_days_reported: int | None
    minimum_subscription_reported: Decimal | None
    quality_holds: tuple[str, ...]
    known_at: datetime
    source_sha256: str
    source_url: str = SOURCE_URL
    authority: str = REFERENCE_STATUS
    runtime_entry_authority: bool = False


@dataclass(frozen=True)
class CafciDailyReference:
    report_date: date
    source_filename: str
    source_sha256: str
    captured_at: datetime
    http_last_modified_at: datetime
    classes: tuple[CafciClassReference, ...]
    reference_status: str = REFERENCE_STATUS
    runtime_entry_authority: bool = False
    fees_account_specific_verified: bool = False
    broker_cutoffs_verified: bool = False
    broker_receipts_verified: bool = False

    @property
    def summary(self) -> dict[str, object]:
        """Reference counts only; NEVER a readiness/permission decision."""
        return {
            "source_status": REFERENCE_STATUS,
            "runtime_entry_authority": False,
            "class_rows": len(self.classes),
            "classes_effective_on_report_date": sum(
                row.nav_effective_date == self.report_date for row in self.classes
            ),
            "nav_missing": sum(row.nav_per_unit is None for row in self.classes),
            "nav_placeholder": sum("NAV_PLACEHOLDER" in row.quality_holds
                                   for row in self.classes),
            "nav_empty_cell": sum("NAV_EMPTY_CELL" in row.quality_holds
                                  for row in self.classes),
            "nav_date_missing": sum(row.nav_effective_date is None for row in self.classes),
            "nonzero_subscription_fee_classes": sum(
                row.subscription_fee_pct > 0 for row in self.classes
            ),
            "nonzero_redemption_fee_classes": sum(
                row.redemption_fee_pct > 0 for row in self.classes
            ),
            "rows_with_holds": sum(bool(row.quality_holds) for row in self.classes),
        }


def _utc(value: datetime | str, label: str) -> datetime:
    try:
        parsed = (parsedate_to_datetime(value) if isinstance(value, str) and "," in value
                  else datetime.fromisoformat(value.replace("Z", "+00:00"))
                  if isinstance(value, str) else value)
    except (ValueError, TypeError, IndexError) as exc:
        raise ValueError(f"CAFCI_{label}_INVALID") from exc
    if not isinstance(parsed, datetime) or parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"CAFCI_{label}_NAIVE_OR_INVALID")
    return parsed.astimezone(timezone.utc)


def _column(cell_ref: str) -> int:
    match = _COLUMN_NAME.fullmatch(cell_ref)
    if not match:
        raise ValueError("CAFCI_CELL_REFERENCE_INVALID")
    index = 0
    for character in match.group(1):
        index = index * 26 + ord(character) - 64
    if not 1 <= index <= 47:
        raise ValueError("CAFCI_COLUMN_RANGE_INVALID")
    return index


def _xlsx_rows(payload: bytes) -> list[dict[int, str]]:
    if not isinstance(payload, bytes) or not 0 < len(payload) <= MAX_COMPRESSED_BYTES:
        raise ValueError("CAFCI_XLSX_SIZE_OR_TYPE_INVALID")
    try:
        with ZipFile(BytesIO(payload)) as archive:
            members = archive.infolist()
            if (len(members) > MAX_ZIP_FILES or len({x.filename for x in members}) != len(members)
                    or sum(x.file_size for x in members) > MAX_UNCOMPRESSED_BYTES):
                raise ValueError("CAFCI_XLSX_ZIP_BUDGET_OR_DUPLICATE")
            names = set(archive.namelist())
            if not {"[Content_Types].xml", "xl/workbook.xml", "xl/worksheets/sheet1.xml"}.issubset(names) or any(
                    name.lower().endswith((".bin", ".vba")) or "externallink" in name.lower()
                    for name in names):
                raise ValueError("CAFCI_XLSX_SHEET_OR_ACTIVE_CONTENT_INVALID")
            strings: list[str] = []
            if "xl/sharedStrings.xml" in names:
                root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
                for element in root.findall(f"{_NS}si"):
                    strings.append("".join(node.text or "" for node in element.iter(f"{_NS}t")))
            root = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    except (BadZipFile, OSError, ET.ParseError, RuntimeError, KeyError) as exc:
        raise ValueError("CAFCI_XLSX_INVALID") from exc
    sheet = root.find(f"{_NS}sheetData")
    if sheet is None:
        raise ValueError("CAFCI_SHEET_DATA_MISSING")
    rows: list[dict[int, str]] = []
    prior_row = 0
    for row in sheet.findall(f"{_NS}row"):
        rownum = int(row.attrib.get("r", "0"))
        if not prior_row < rownum <= MAX_ROWS:
            raise ValueError("CAFCI_ROW_RANGE_OR_ORDER_INVALID")
        prior_row = rownum
        values: dict[int, str] = {0: str(rownum)}
        for cell in row.findall(f"{_NS}c"):
            if cell.find(f"{_NS}f") is not None:
                raise ValueError("CAFCI_FORMULA_REJECTED")
            col = _column(cell.attrib.get("r", ""))
            if col in values:
                raise ValueError("CAFCI_DUPLICATE_CELL")
            kind = cell.attrib.get("t", "n")
            if kind == "inlineStr":
                content = cell.find(f"{_NS}is")
                value = "" if content is None else "".join(
                    node.text or "" for node in content.iter(f"{_NS}t")
                )
            elif kind in ("n", "str", "s"):
                raw = cell.findtext(f"{_NS}v") or ""
                if kind == "s":
                    try:
                        value = strings[int(raw)]
                    except (ValueError, IndexError) as exc:
                        raise ValueError("CAFCI_SHARED_STRING_INVALID") from exc
                else:
                    value = raw
            else:
                raise ValueError("CAFCI_UNSUPPORTED_CELL_TYPE")
            if len(value) > 500:
                raise ValueError("CAFCI_CELL_VALUE_TOO_LARGE")
            values[col] = value.strip()
        rows.append(values)
    return rows


def _decimal(value: str | None, code: str, *, missing_allowed: bool = False) -> Decimal | None:
    if not value:
        if missing_allowed:
            return None
        raise ValueError(f"CAFCI_{code}_MISSING")
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"CAFCI_{code}_INVALID") from exc
    if not number.is_finite() or number < 0:
        raise ValueError(f"CAFCI_{code}_INVALID")
    return number


def _integer(value: str | None, code: str, *, allow_zero: bool = False) -> int:
    number = _decimal(value, code)
    assert number is not None
    if number != number.to_integral_value() or (not allow_zero and number == 0):
        raise ValueError(f"CAFCI_{code}_INVALID")
    return int(number)


def parse_cafci_daily_reference(
    payload: bytes, *, captured_at: datetime | str,
    http_last_modified_at: datetime | str, expected_sha256: str,
    source_filename: str, source_url: str = SOURCE_URL,
) -> CafciDailyReference:
    """Parse an exact, already captured official XLSX into non-trading references.

    `captured_at` is the earliest knowledge time. HTTP Last-Modified describes
    the object, NOT proof the observer knew any individual NAV at that time.
    """
    captured = _utc(captured_at, "CAPTURED_AT")
    modified = _utc(http_last_modified_at, "LAST_MODIFIED_AT")
    if modified > captured:
        raise ValueError("CAFCI_LAST_MODIFIED_AFTER_CAPTURE")
    if source_url != SOURCE_URL:
        raise ValueError("CAFCI_SOURCE_URL_UNVERIFIED")
    if not isinstance(expected_sha256, str) or not re.fullmatch(r"[a-f0-9]{64}", expected_sha256):
        raise ValueError("CAFCI_SHA256_INVALID")
    if not isinstance(payload, bytes) or sha256(payload).hexdigest() != expected_sha256:
        raise ValueError("CAFCI_SHA256_MISMATCH")
    match = _FILE_NAME.fullmatch(source_filename) if isinstance(source_filename, str) else None
    if not match:
        raise ValueError("CAFCI_SOURCE_FILENAME_INVALID")
    report_date = datetime.strptime(match.group(1), "%Y%m%d").date()
    if report_date > captured.date():
        raise ValueError("CAFCI_REPORT_DATE_IN_FUTURE")
    rows = _xlsx_rows(payload)
    header = next((row for row in rows if row[0] == "8"), None)
    if header is None or any(header.get(key) != name for key, name in _HEADER.items()):
        raise ValueError("CAFCI_SCHEMA_HEADER_DRIFT")
    result: list[CafciClassReference] = []
    ids: set[int] = set()
    for row in rows:
        if int(row[0]) <= 9:
            continue
        if not row.get(21):
            if any(row.get(key) for key in (5, 6, 19, 24, 30, 34, 37, 38)):
                raise ValueError("CAFCI_CLASS_ID_MISSING")
            continue
        if len(result) >= MAX_CLASSES:
            raise ValueError("CAFCI_CLASS_BUDGET_EXCEEDED")
        cid = _integer(row.get(21), "CLASS_ID")
        if cid in ids:
            raise ValueError("CAFCI_CLASS_ID_DUPLICATED")
        ids.add(cid)
        name = row.get(1, "").strip()
        manager = row.get(24, "").strip()
        currency = row.get(37, "").strip()
        if not name or not manager or not currency:
            raise ValueError("CAFCI_CLASS_IDENTITY_INCOMPLETE")
        raw_date = row.get(5, "")
        try:
            effective = datetime.strptime(raw_date, "%d/%m/%y").date() if raw_date else None
        except ValueError as exc:
            raise ValueError("CAFCI_NAV_DATE_INVALID") from exc
        if effective is not None and (effective > report_date or effective > captured.date()):
            raise ValueError("CAFCI_NAV_DATE_IN_FUTURE")
        raw_nav = row.get(6, "")
        # The public daily workbook uses a documented-looking marker "(**)"
        # for some rows.  We may retain its absence, never invent the NAV.
        if raw_nav == "(**)":
            value_mil = None
        else:
            value_mil = _decimal(raw_nav, "NAV_PER_THOUSAND", missing_allowed=True)
        fees_in = _decimal(row.get(30), "SUBSCRIPTION_FEE")
        fees_out = _decimal(row.get(34), "REDEMPTION_FEE")
        if fees_in > 100 or fees_out > 100:
            raise ValueError("CAFCI_FEE_PERCENT_OUT_OF_RANGE")
        # -1 is another observed sentinel for funds in liquidation.
        settlement = (-1 if row.get(38) == "-1" else
                      _integer(row.get(38), "SETTLEMENT_DAYS", allow_zero=True))
        holds = []
        if value_mil is None:
            holds.append("NAV_PLACEHOLDER" if raw_nav == "(**)" else "NAV_EMPTY_CELL")
        elif value_mil == 0:
            holds.append("NAV_ZERO")
        if effective is None:
            holds.append("NAV_EFFECTIVE_DATE_MISSING")
        elif effective < report_date:
            holds.append("NAV_OLDER_THAN_REPORT")
        if currency not in {"ARS", "USD"}:
            holds.append("CURRENCY_UNMAPPED_TO_BROKER")
        if settlement < 0 or settlement > 365:
            # CAFCI publishes -1 and 999/9999/99999 sentinels.
            # None is not T+0 or a valid broker redemption calendar.
            holds.append("SETTLEMENT_SENTINEL_OR_UNVERIFIED")
        result.append(CafciClassReference(
            class_name=name, cafci_class_id=cid,
            cnv_fund_id=_integer(row.get(19), "FUND_CNV_ID"),
            management_company=manager, currency_raw=currency,
            nav_effective_date=effective,
            nav_per_thousand=value_mil,
            nav_per_unit=value_mil / Decimal(1000) if value_mil is not None else None,
            subscription_fee_pct=fees_in, redemption_fee_pct=fees_out,
            settlement_days_reported=settlement if 0 <= settlement <= 365 else None,
            minimum_subscription_reported=_decimal(
                row.get(44), "MINIMUM_SUBSCRIPTION", missing_allowed=True),
            quality_holds=tuple(holds), known_at=captured,
            source_sha256=expected_sha256,
        ))
    if not result:
        raise ValueError("CAFCI_NO_CLASS_REFERENCES")
    return CafciDailyReference(report_date, source_filename, expected_sha256,
                               captured, modified, tuple(result))
