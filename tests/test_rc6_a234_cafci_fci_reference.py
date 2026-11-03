"""A234: synthetic adversarial CAFCI XLSX; no network, broker or live data."""

from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
import unittest
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile

from rc6_cafci_fci_reference import (
    RUNTIME_ENTRY_AUTHORITY, REFERENCE_STATUS, SOURCE_URL,
    parse_cafci_daily_reference,
)


NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
HEADERS = {
    1: "Fondo", 5: "Fecha", 6: "Valor (mil cuotapartes)",
    19: "Codigo CNV", 21: "Código CAFCI", 24: "Sociedad Gerente",
    30: "Comision Ingreso", 34: "Comision Rescate",
    37: "Moneda Fondo", 38: "Plazo Liq.",
}
AT = datetime(2026, 10, 31, 18, 48, 28, tzinfo=timezone.utc)
LAST_MODIFIED = "Fri, 30 Oct 2026 23:20:00 GMT"
FILENAME = "20261030_Planilla_Diaria_A.xlsx"


def _col(index):
    result = ""
    while index:
        index, rem = divmod(index - 1, 26)
        result = chr(65 + rem) + result
    return result


def reference_row(**changes):
    fields = {
        1: "Fondo ABC - Clase A", 5: "30/10/26", 6: "1083.156",
        19: "1935", 21: "6489", 24: "Gestora ABC S.A.",
        30: "0", 34: "0", 37: "ARS", 38: "1", 44: "1000",
    }
    names = {"name": 1, "effective": 5, "nav_mil": 6, "cnv_id": 19,
             "cafci_id": 21, "manager": 24, "fee_in": 30, "fee_out": 34,
             "currency": 37, "settlement": 38, "minimum": 44}
    for key, value in changes.items():
        fields[names[key]] = value
    return fields


def synthetic_xlsx(*class_rows, header_override=None, with_formula=False):
    """Minimal in-memory XLSX fixture, not an externally observed source."""
    worksheet = ET.Element(f"{{{NS}}}worksheet")
    table = ET.SubElement(worksheet, f"{{{NS}}}sheetData")
    for n, values in [(8, {**HEADERS, **(header_override or {})})] + [
            (12 + offset, row) for offset, row in enumerate(class_rows)]:
        xmlrow = ET.SubElement(table, f"{{{NS}}}row", {"r": str(n)})
        for col, value in sorted(values.items()):
            if value is None:
                continue
            is_text = col in {1, 5, 24, 37} or n == 8
            cell = ET.SubElement(xmlrow, f"{{{NS}}}c", {
                "r": f"{_col(col)}{n}", "t": "inlineStr" if is_text else "n"
            })
            if with_formula and n == 12 and col == 6:
                ET.SubElement(cell, f"{{{NS}}}f").text = "1+1"
            if is_text:
                inline = ET.SubElement(cell, f"{{{NS}}}is")
                ET.SubElement(inline, f"{{{NS}}}t").text = str(value)
            else:
                ET.SubElement(cell, f"{{{NS}}}v").text = str(value)
    stream = BytesIO()
    with ZipFile(stream, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("xl/workbook.xml", "<workbook/>")
        archive.writestr("xl/worksheets/sheet1.xml", ET.tostring(worksheet))
    return stream.getvalue()


def parse(payload, **changes):
    args = {
        "captured_at": AT, "http_last_modified_at": LAST_MODIFIED,
        "expected_sha256": sha256(payload).hexdigest(),
        "source_filename": FILENAME, "source_url": SOURCE_URL,
    }
    return parse_cafci_daily_reference(payload, **{**args, **changes})


class CafciPublicReferenceTest(unittest.TestCase):
    def test_exact_nav_per_thousand_and_public_fee_are_not_broker_authority(self):
        report = parse(synthetic_xlsx(reference_row(fee_in="0.5", fee_out="2")))
        cls = report.classes[0]
        self.assertEqual(str(cls.nav_per_thousand), "1083.156")
        self.assertEqual(str(cls.nav_per_unit), "1.083156")
        self.assertEqual(cls.cafci_class_id, 6489)
        self.assertEqual(cls.cnv_fund_id, 1935)
        self.assertEqual(cls.subscription_fee_pct, cls.subscription_fee_pct.__class__("0.5"))
        self.assertEqual(cls.redemption_fee_pct, cls.redemption_fee_pct.__class__("2"))
        self.assertEqual(cls.currency_raw, "ARS")
        self.assertEqual(cls.settlement_days_reported, 1)
        self.assertEqual(cls.known_at, AT)  # not HTTP Last-Modified: PIT observed time
        self.assertEqual(cls.quality_holds, ())
        self.assertEqual(report.reference_status, REFERENCE_STATUS)
        self.assertFalse(report.runtime_entry_authority)
        self.assertFalse(RUNTIME_ENTRY_AUTHORITY)
        self.assertFalse(cls.runtime_entry_authority)
        self.assertFalse(report.fees_account_specific_verified)
        self.assertFalse(report.broker_cutoffs_verified)
        self.assertFalse(report.broker_receipts_verified)

    def test_missing_nav_and_stale_date_hold_without_fake_zero(self):
        report = parse(synthetic_xlsx(
            reference_row(nav_mil=None, effective="29/10/26", cafci_id="1001"),
            reference_row(nav_mil="(**)", effective=None, cafci_id="1002")))
        self.assertEqual(report.summary["nav_missing"], 2)
        self.assertEqual(report.summary["nav_empty_cell"], 1)
        self.assertEqual(report.summary["nav_placeholder"], 1)
        self.assertEqual(report.summary["nav_date_missing"], 1)
        self.assertIsNone(report.classes[0].nav_per_unit)
        self.assertIn("NAV_OLDER_THAN_REPORT", report.classes[0].quality_holds)
        self.assertIn("NAV_PLACEHOLDER", report.classes[1].quality_holds)

    def test_cafci_special_currency_and_liquidating_fund_sentinels_are_not_t0(self):
        report = parse(synthetic_xlsx(
            reference_row(currency="USB", settlement="99999", cafci_id="2001"),
            reference_row(currency="USD", settlement="-1", cafci_id="2002")))
        for cls in report.classes:
            self.assertIsNone(cls.settlement_days_reported)
            self.assertIn("SETTLEMENT_SENTINEL_OR_UNVERIFIED", cls.quality_holds)
        self.assertEqual(report.classes[0].currency_raw, "USB")
        self.assertIn("CURRENCY_UNMAPPED_TO_BROKER", report.classes[0].quality_holds)
        self.assertNotIn("CURRENCY_UNMAPPED_TO_BROKER", report.classes[1].quality_holds)

    def test_duplicate_id_and_header_drift_are_rejected(self):
        for payload, reason in [
            (synthetic_xlsx(reference_row(), reference_row()), "CLASS_ID_DUPLICATED"),
            (synthetic_xlsx(reference_row(), header_override={6: "Valor unitario"}),
             "SCHEMA_HEADER_DRIFT"),
        ]:
            with self.subTest(reason=reason), self.assertRaisesRegex(ValueError, reason):
                parse(payload)

    def test_untrusted_timestamp_and_source_identity_fail_closed(self):
        payload = synthetic_xlsx(reference_row())
        for overrides, reason in [
            ({"captured_at": "2026-10-31T18:00:00"}, "CAPTURED_AT_NAIVE"),
            ({"http_last_modified_at": "2026-11-01T00:00:00Z"},
             "LAST_MODIFIED_AFTER_CAPTURE"),
            ({"source_filename": "20261102_Planilla_Diaria_A.xlsx"},
             "REPORT_DATE_IN_FUTURE"),
            ({"source_url": "https://other.example/api"}, "SOURCE_URL_UNVERIFIED"),
            ({"expected_sha256": "a" * 64}, "SHA256_MISMATCH"),
            ({"expected_sha256": "invalid"}, "SHA256_INVALID"),
        ]:
            with self.subTest(reason=reason), self.assertRaisesRegex(ValueError, reason):
                parse(payload, **overrides)

    def test_formula_and_invalid_nav_or_date_are_rejected(self):
        for payload, reason in [
            (synthetic_xlsx(reference_row(), with_formula=True), "FORMULA_REJECTED"),
            (synthetic_xlsx(reference_row(nav_mil="abc")), "NAV_PER_THOUSAND_INVALID"),
            (synthetic_xlsx(reference_row(nav_mil="-0.5")), "NAV_PER_THOUSAND_INVALID"),
            (synthetic_xlsx(reference_row(effective="01/11/26")), "NAV_DATE_IN_FUTURE"),
            (synthetic_xlsx(reference_row(fee_in="101")), "FEE_PERCENT_OUT_OF_RANGE"),
        ]:
            with self.subTest(reason=reason), self.assertRaisesRegex(ValueError, reason):
                parse(payload)

    def test_no_classes_or_untrusted_payload_are_rejected(self):
        for payload, reason in [
            (synthetic_xlsx(), "NO_CLASS_REFERENCES"),
            (b"not an XLSX", "XLSX_INVALID"),
        ]:
            with self.subTest(reason=reason), self.assertRaisesRegex(ValueError, reason):
                parse(payload)

    def test_quality_holds_count_unique_class_ids_not_overlapping_reason_totals(self):
        # A237: a single reference can have stale NAV, unknown broker currency
        # and a liquidating/closed-fund settlement sentinel at the same time.
        # Report counts remain keyed to distinct CAFCI class IDs.
        report = parse(synthetic_xlsx(
            reference_row(cafci_id="5101", effective="29/10/26",
                          nav_mil=None, currency="USB", settlement="9999"),
            reference_row(cafci_id="5102", effective=None,
                          nav_mil="(**)", settlement="-1"),
            reference_row(cafci_id="5103", nav_mil="1025.0"),
        ))
        summary = report.summary
        self.assertEqual(summary["class_rows"], 3)
        self.assertEqual(summary["rows_with_holds"], 2)
        self.assertEqual(summary["classes_effective_on_report_date"], 1)
        self.assertEqual(summary["nav_missing"], 2)
        self.assertEqual(summary["nav_empty_cell"], 1)
        self.assertEqual(summary["nav_placeholder"], 1)
        self.assertEqual(summary["nav_date_missing"], 1)
        self.assertGreater(sum(len(cls.quality_holds) for cls in report.classes),
                           summary["rows_with_holds"])
        self.assertEqual({cls.cafci_class_id for cls in report.classes
                          if cls.quality_holds}, {5101, 5102})
        self.assertFalse(report.runtime_entry_authority)

    def test_class_data_without_exact_cafci_id_fails_closed(self):
        # A237: a row with visible class/NAV/manager/currency but missing ID
        # must not disappear silently from an apparently complete report.
        for missing_id in (None, "0", "not-an-id"):
            with self.subTest(missing_id=missing_id), self.assertRaisesRegex(
                    ValueError, "CAFCI_CLASS_ID_MISSING|CAFCI_CLASS_ID_INVALID"):
                parse(synthetic_xlsx(reference_row(cafci_id=missing_id)))


if __name__ == "__main__":
    unittest.main()
