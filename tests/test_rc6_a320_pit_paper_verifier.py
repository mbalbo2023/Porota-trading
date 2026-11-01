"""A320 ten-family offhost fixtures: contract-path tests, NEVER real approval."""

import copy
import base64
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import rc6_a320_pit_paper_verifier as pit


DAY = "2026-11-03"
KEYS = {
    "providers": {
        "PPI": (b"p" * 32, "PPI_CAPTURE_OWNER"),
        "IOL": (b"i" * 32, "IOL_CAPTURE_OWNER"),
        "BYMA": (b"b" * 32, "BYMA_CAPTURE_OWNER"),
        "A3": (b"a" * 32, "A3_CAPTURE_OWNER"),
        "CAFCI": (b"c" * 32, "CAFCI_CAPTURE_OWNER"),
        "CNV": (b"n" * 32, "CNV_CAPTURE_OWNER"),
    },
    "decision": (b"d" * 32, "PAPER_DECISION_OWNER"),
    "fees": (b"f" * 32, "BROKER_FEE_OWNER"),
}
SYMBOLS = {
    "ACCIONES": ("GGAL", "ACCIONES", "BYMA"),
    "CEDEARS": ("AAPL", "CEDEARS", "BYMA"),
    "ETFS": ("MERV", "ETF", "BYMA"),
    "BONOS": ("AL30", "BONOS", "BYMA"),
    "LETRAS": ("S31O6", "LETRAS", "BYMA"),
    "OBLIGACIONES": ("YMCIO", "ON", "BYMA"),
    "OPCIONES": ("GGALC", "OPCIONES", "BYMA"),
    "FUTUROS": ("DLR/NOV26", "FUTUROS", "A3"),
    "CAUCIONES": ("CAU7D", "CAUCIONES", "BYMA"),
    "FCI": ("FONDO_A", "FCI", "CAFCI"),
}


def signed(body, role):
    role_key = (KEYS["providers"][pit._source_lineage(body["source"])]
                if role in {"primary", "independent", "allocation"} else KEYS[role])
    return pit._sign(body, *role_key)


def one_case(family, *, day=DAY):
    symbol, kind, market = SYMBOLS[family]
    identity = {"family": family, "symbol": symbol, "instrument_type": kind,
                "market": market, "currency": "ARS", "settlement": "INMEDIATA"}
    base_quote = {"bid": 100, "ask": 101, "bid_quantity": 200,
                  "ask_quantity": 200}
    if family == "CAUCIONES":
        base_quote.update(bid=30, ask=31)
    contract_document = {"publisher": "OFFHOST_SYNTHETIC_FIXTURE_ONLY",
                         "instrument": symbol, "effective_day": day}
    calendar_document = {"publisher": "OFFHOST_SYNTHETIC_FIXTURE_ONLY",
                         "market": market, "valid_from": day}
    contract = {**identity,
                "official_source_sha256": pit._sha(contract_document),
                "calendar_official_source_sha256": pit._sha(calendar_document),
                "price_basis": "RAW", "adjustment_basis": "RAW_NO_ADJUSTMENT",
                "unit_multiplier": "1", "volume_kind": pit.VOLUME_KIND_BY_FAMILY[family],
                "valid_from": day, "valid_through": "2026-11-30",
                "calendar_valid_from": day, "calendar_valid_through": "2026-11-30",
                "calendar_first_known_at_utc": "2026-11-01T18:00:00Z",
                "session_open_utc": day + "T13:00:00Z",
                "session_close_utc": day + "T21:00:00Z"}
    if family == "ACCIONES":
        contract.update(lot_size=1, tick_size="0.01")
    elif family == "CEDEARS":
        contract.update(lot_size=1, tick_size="0.01", cedear_ratio="20",
                        underlying_session_open_utc=day + "T14:30:00Z")
    elif family == "ETFS":
        contract.update(lot_size=1, tick_size="0.01", listing_from="2026-11-03",
                        nav_reference_only=True)
    elif family in {"BONOS", "LETRAS", "OBLIGACIONES"}:
        contract.update(nominal_basis=100, accrued_interest="0",
                        issuer_isin="AR0000000001", native_exit_verified=True,
                        native_maturity_verified=True, fixing_contract="BILL_FACE_VALUE",
                        coupon_contract="VERIFIED_CASHFLOWS")
    elif family == "OPCIONES":
        contract.update(strike="100", option_kind="CALL", expiry_day="2026-12-18",
                        contract_multiplier="100", exercise_risk_verified=True)
    elif family == "FUTUROS":
        contract.update(expiry_day="2026-12-01", contract_multiplier="1000",
                        margin_per_contract="1000", variation_mark_verified=True)
    elif family == "CAUCIONES":
        contract.update(term_days=7, maturity_day="2026-11-10",
                        day_count_basis=365, principal_unit="1000",
                        quote_unit="TNA_PERCENT", collateral_regime_verified=True,
                        counterparty_verified=True)
    elif family == "FCI":
        contract.update(fund_id="FUND_A", fund_class_id="CLASS_A",
                        fund_cutoff_at_utc=day + "T18:00:00Z", redemption_days=1,
                        nav_publication_contract="CAFCI_CLASS_NAV_PUBLICATION")
    primary_source = "CAFCI" if family == "FCI" else "A3" if family == "FUTUROS" else "PPI"
    primary_quote = {"nav": "100"} if family == "FCI" else base_quote
    primary_published = ("2026-11-02T21:00:00Z" if family == "FCI"
                         else day + "T14:00:00Z")
    if primary_source == "PPI":
        book = {"date": primary_published,
                "bids": [{"position": 1, "price": base_quote["bid"],
                          "quantity": base_quote["bid_quantity"]}],
                "offers": [{"position": 1, "price": base_quote["ask"],
                            "quantity": base_quote["ask_quantity"]}]}
        stamps = iter((datetime.fromisoformat(day + "T14:00:02+00:00"),
                       datetime.fromisoformat(day + "T14:00:05+00:00")))
        wire = pit._canonical(book)
        capture = pit.capture_ppi_book([identity], lambda *args: (book, wire),
                                       now=lambda: next(stamps), pause=lambda *_: None,
                                       signer_key=KEYS["providers"]["PPI"][0],
                                       signer_id=KEYS["providers"]["PPI"][1])
        primary = capture["captures"][0]
    else:
        raw = {"identity": identity, "quote": primary_quote}
        primary = signed({"schema": "rc6.a320.provider.capture.v1",
                          "identity": identity, "source": primary_source,
                          "source_url": "https://www.cafci.org.ar/informacionEstadistica.html"
                          if family == "FCI" else "https://a3mercados.com.ar/info-de-mercado/datos-de-mercado",
                          "capture_kind": "OFFICIAL_READONLY_CAPTURE",
                          "provenance": "AUTHENTIC_READONLY_CAPTURE",
                          "upstream_method": "GET", "license_state": "AUTHORIZED",
                          "sensor_id": "PRIMARY_SENSOR", "started_at_utc": day + "T14:00:02Z",
                          "first_known_at_utc": day + "T14:00:05Z",
                          "provider_at_utc": primary_published,
                          "raw_payload": raw, "raw_payload_sha256": pit._sha(raw),
                          "orders_sent": 0, "history_v2_writes": 0}, "primary")
    ref_source = "CNV" if family == "FCI" else "IOL"
    ref_url = ("https://www.cnv.gov.ar/SitioWeb/FondosComunesInversion/CuotaPartes"
               if family == "FCI" else "https://api.invertironline.com/")
    raw_ref = {"identity": identity, "quote": primary_quote,
               "official_contract_document": contract_document,
               "official_calendar_document": calendar_document, "contract": contract}
    reference = signed({"schema": "rc6.a320.provider.capture.v1",
                        "source": ref_source, "source_url": ref_url,
                        "identity": identity,
                        "capture_kind": "INDEPENDENT_OFFICIAL_READONLY_CAPTURE",
                        "provenance": "AUTHENTIC_READONLY_CAPTURE",
                        "upstream_method": "GET", "license_state": "AUTHORIZED",
                        "sensor_id": "INDEPENDENT_SENSOR", "started_at_utc": day + "T14:00:03Z",
                        "first_known_at_utc": day + "T14:00:06Z",
                        "provider_at_utc": day + "T14:00:01Z" if family != "FCI"
                        else "2026-11-02T21:00:00Z",
                        "raw_payload": raw_ref, "raw_payload_sha256": pit._sha(raw_ref),
                        "orders_sent": 0, "history_v2_writes": 0}, "independent")
    decision = signed({"identity": identity, "intent": "PAPER_ONLY",
                       "side": "SUBSCRIBE" if family == "FCI" else "PLACE"
                       if family == "CAUCIONES" else "BUY", "quantity": "1",
                       "risk_approval": "SIGNED_PAPER_ONLY_LIMITS",
                       "cash_available": "100000", "margin_available": "100000",
                       "position_available": "100",
                       "decision_at_utc": day + "T14:00:10Z",
                       "features_last_known_at_utc": day + "T14:00:06Z",
                       "inputs_sha256": {"primary": pit._sha(primary),
                                         "reference": pit._sha(reference)},
                       "orders_sent": 0, "real_order_id": None}, "decision")
    fee = signed({"identity": identity, "fee_source_kind": "BROKER_ACCOUNT_CONTRACT_READONLY",
                  "account_rate_status": "ACCOUNT_EXACT_VERIFIED",
                  "broker_fee_contract_sha256": "f" * 64,
                  "calculation_method": "CAUCION_ANNUALIZED_COMMISSION"
                  if family == "CAUCIONES" else "FCI_SUBSCRIPTION"
                  if family == "FCI" else "BOOK_NOTIONAL",
                  "first_known_at_utc": day + "T14:00:04Z",
                  "valid_from": day, "valid_through": "2026-11-30",
                  "commission_pct": "0.006", "vat_on_commission_pct": "0.21",
                  "market_rights_pct": "0.001", "min_commission": "0",
                  "other_charges_amount": "0", "orders_sent": 0}, "fees")
    case = {"family": family, "identity": identity,
            "primary_capture": primary, "independent_capture": reference,
            "paper_decision": decision, "fee_receipt": fee}
    if family == "FCI":
        allocation_raw = {"identity": identity, "fund_class_id": "CLASS_A",
                          "nav": "105"}
        case["post_cutoff_nav"] = signed({
            "identity": identity, "source": "CAFCI",
            "source_url": "https://www.cafci.org.ar/informacionEstadistica.html",
            "capture_kind": "INDEPENDENT_OFFICIAL_NAV_PUBLICATION",
            "upstream_method": "GET", "license_state": "AUTHORIZED",
            "started_at_utc": day + "T19:02:45Z",
            "first_known_at_utc": day + "T19:03:00Z",
            "provider_at_utc": day + "T19:01:00Z",
            "raw_payload": allocation_raw,
            "raw_payload_sha256": pit._sha(allocation_raw),
            "orders_sent": 0}, "allocation")
    return case


def bundle(*cases):
    return {"schema": pit.SCHEMA, "cases": list(cases)}


def resign(record, role, **changes):
    return signed({**{key: value for key, value in record.items()
                      if key != "attestation"}, **changes}, role)


class TestA320PITPaperVerifier(unittest.TestCase):
    def test_ten_family_contract_paths_but_zero_real_approvals(self):
        report = pit.review_bundle(bundle(*(one_case(f) for f in pit.FAMILIES)), KEYS)
        self.assertEqual(10, len(report["families"]))
        self.assertEqual(0, report["source_families_verified"])
        self.assertEqual(0, report["paper_families_verified"])
        for family in pit.FAMILIES:
            row = report["families"][family]
            self.assertEqual(1, row["case_count"], family)
            self.assertEqual(1, row["synthetic_source_contract_candidates"], (family, row))
            self.assertEqual(1, row["synthetic_paper_contract_candidates"], (family, row))
            self.assertEqual("NO_VERIFICADO", row["source_status"])
            self.assertEqual("NO_VERIFICADO", row["paper_status"])
            self.assertEqual(0, row["receipt_evaluations"][0]["paper"]["real_orders_sent"])

    def test_no_input_ten_denominators_present_all_negative(self):
        report = pit.review_bundle(bundle(), KEYS)
        self.assertEqual(10, len(report["families"]))
        self.assertTrue(all(row["case_count"] == 0 for row in report["families"].values()))
        self.assertEqual(0, report["paper_families_verified"])

    def test_duplicate_raw_pair_and_signed_decision_never_inflate_denominators(self):
        original = one_case("ACCIONES")
        report = pit.review_bundle(bundle(original, copy.deepcopy(original)), KEYS)
        row = report["families"]["ACCIONES"]
        self.assertEqual(2, row["case_count"])
        self.assertEqual(1, row["unique_source_receipt_pairs"])
        self.assertEqual(1, row["unique_signed_paper_decisions"])
        self.assertEqual(1, row["duplicate_case_count"])
        self.assertEqual(0, row["invalid_case_count"])
        self.assertEqual(1, row["synthetic_source_contract_candidates"])
        self.assertEqual(1, row["synthetic_paper_contract_candidates"])
        self.assertIn("A320_DUPLICATE_SOURCE_RECEIPT_PAIR", row["rejection_reasons"])
        self.assertIn("A320_DUPLICATE_SIGNED_PAPER_DECISION", row["rejection_reasons"])
        self.assertEqual(0, row["source_pit_rows_verified"])
        self.assertEqual(0, row["paper_sim_rows_verified"])

    def test_one_book_two_distinct_signed_paper_decisions(self):
        case = one_case("ACCIONES")
        second = copy.deepcopy(case)
        second["paper_decision"] = resign(second["paper_decision"], "decision",
                                           quantity="2")
        row = pit.review_bundle(bundle(case, second), KEYS)["families"]["ACCIONES"]
        self.assertEqual(1, row["synthetic_source_contract_candidates"])
        self.assertEqual(2, row["synthetic_paper_contract_candidates"])
        self.assertEqual(0, row["duplicate_case_count"])

    def test_missing_provider_key_negative_not_silently_skipped(self):
        limited = {"providers": {"PPI": KEYS["providers"]["PPI"]},
                   "decision": KEYS["decision"], "fees": KEYS["fees"]}
        row = pit.review_bundle(bundle(one_case("ACCIONES")), limited)["families"]["ACCIONES"]
        self.assertEqual(1, row["case_count"])
        self.assertEqual(1, row["invalid_case_count"])
        self.assertEqual(0, row["synthetic_source_contract_candidates"])
        self.assertIn("A320_PROVIDER_TRUST_ROOTS_NOT_INDEPENDENT", row["rejection_reasons"])

    def test_synthetic_cannot_promote_by_claiming_authentic(self):
        with self.assertRaisesRegex(ValueError, "PROTECTED_MAIN"):
            pit.review_bundle(bundle(one_case("ACCIONES")), KEYS,
                              evidence_mode="AUTHENTIC_OFFHOST_EVIDENCE")
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(2, pit.main(["verify-bundle", "--output", "/tmp/should_not_write_a320",
                                          "--mode", "AUTHENTIC_OFFHOST_EVIDENCE",
                                          "--bundle", "/tmp/nonexistent_a320"] ))

    def test_corrupted_hmac_rejected(self):
        case = one_case("ACCIONES")
        case["primary_capture"]["raw_book"]["bids"][0]["price"] = 999
        row = pit.review_bundle(bundle(case), KEYS)["families"]["ACCIONES"]
        self.assertEqual(0, row["synthetic_source_contract_candidates"])
        self.assertIn("A320_ATTESTATION_SIGNATURE_INVALID", row["rejection_reasons"])

    def test_eod_not_live_book_and_no_sdk_only_shortcut(self):
        for kind in ("HISTORICAL_EOD", "SDK_PARSED_BOOK_UNVERIFIED"):
            case = one_case("CEDEARS")
            case["primary_capture"] = resign(case["primary_capture"], "primary",
                                               capture_kind=kind)
            row = pit.review_bundle(bundle(case), KEYS)["families"]["CEDEARS"]
            self.assertEqual(0, row["synthetic_source_contract_candidates"])

    def test_older_provider_date_allowed_but_future_provider_rejected(self):
        case = one_case("BONOS")
        case["independent_capture"] = resign(case["independent_capture"], "independent",
                                              provider_at_utc=DAY + "T15:00:00Z")
        row = pit.review_bundle(bundle(case), KEYS)["families"]["BONOS"]
        self.assertEqual(0, row["synthetic_source_contract_candidates"])
        self.assertIn("A320_PROVIDER_LOOKAHEAD_OR_UNBOUNDED_CLOCK", row["rejection_reasons"])

    def test_stale_eight_hour_book_even_with_fresh_receive_clock_is_denied(self):
        case = one_case("ACCIONES")
        raw = copy.deepcopy(case["primary_capture"]["raw_book"])
        raw["date"] = DAY + "T13:00:00Z"
        wire = pit._canonical(raw)
        case["primary_capture"] = resign(
            case["primary_capture"], "primary", raw_book=raw,
            provider_at_utc=raw["date"], raw_book_sha256=pit._sha(raw),
            raw_http_body_sha256=pit.sha256(wire).hexdigest(),
            raw_http_body_base64=base64.b64encode(wire).decode("ascii"))
        row = pit.review_bundle(bundle(case), KEYS)["families"]["ACCIONES"]
        self.assertEqual(0, row["synthetic_source_contract_candidates"])
        self.assertIn("A320_MARKET_BOOK_PROVIDER_TIMESTAMP_STALE_OR_FUTURE",
                      row["rejection_reasons"])

    def test_stale_independent_book_does_not_corrobate_fresh_ppi(self):
        case = one_case("BONOS")
        case["independent_capture"] = resign(
            case["independent_capture"], "independent",
            provider_at_utc=DAY + "T13:00:00Z")
        row = pit.review_bundle(bundle(case), KEYS)["families"]["BONOS"]
        self.assertEqual(0, row["synthetic_source_contract_candidates"])
        self.assertIn("A320_MARKET_BOOK_PROVIDER_TIMESTAMP_STALE_OR_FUTURE",
                      row["rejection_reasons"])

    def test_book_fresh_for_capture_but_stale_at_later_paper_decision(self):
        case = one_case("ACCIONES")
        raw = copy.deepcopy(case["primary_capture"]["raw_book"])
        raw["date"] = DAY + "T13:58:50Z"
        wire = pit._canonical(raw)
        case["primary_capture"] = resign(
            case["primary_capture"], "primary", raw_book=raw,
            provider_at_utc=raw["date"], raw_book_sha256=pit._sha(raw),
            raw_http_body_sha256=pit.sha256(wire).hexdigest(),
            raw_http_body_base64=base64.b64encode(wire).decode("ascii"))
        case["paper_decision"] = resign(
            case["paper_decision"], "decision",
            inputs_sha256={"primary": pit._sha(case["primary_capture"]),
                            "reference": pit._sha(case["independent_capture"])},
            decision_at_utc=DAY + "T14:01:20Z")
        row = pit.review_bundle(bundle(case), KEYS)["families"]["ACCIONES"]
        self.assertEqual(1, row["synthetic_source_contract_candidates"])
        self.assertEqual(0, row["synthetic_paper_contract_candidates"])
        self.assertIn("A320_MARKET_BOOK_PROVIDER_TIMESTAMP_STALE_OR_FUTURE",
                      row["rejection_reasons"])

    def test_independent_must_not_be_self_copy_of_ppi(self):
        case = one_case("LETRAS")
        case["independent_capture"] = resign(case["independent_capture"], "independent",
                                              source="PPI")
        row = pit.review_bundle(bundle(case), KEYS)["families"]["LETRAS"]
        self.assertEqual(0, row["synthetic_source_contract_candidates"])
        self.assertIn("A320_PROVIDER_TRUST_ROOTS_NOT_INDEPENDENT", row["rejection_reasons"])

    def test_independent_mismatched_currency_or_basis_fails(self):
        for change in ("currency", "price_basis"):
            case = one_case("OBLIGACIONES")
            raw = case["independent_capture"]["raw_payload"]
            raw["contract"][change] = "USD" if change == "currency" else "ADJUSTED"
            case["independent_capture"] = resign(
                case["independent_capture"], "independent", raw_payload=raw,
                raw_payload_sha256=pit._sha(raw))
            row = pit.review_bundle(bundle(case), KEYS)["families"]["OBLIGACIONES"]
            self.assertEqual(0, row["synthetic_source_contract_candidates"])

    def test_etf_cannot_trade_before_listing(self):
        case = one_case("ETFS")
        raw = case["independent_capture"]["raw_payload"]
        raw["contract"]["listing_from"] = "2026-11-04"
        case["independent_capture"] = resign(case["independent_capture"], "independent",
                                              raw_payload=raw, raw_payload_sha256=pit._sha(raw))
        row = pit.review_bundle(bundle(case), KEYS)["families"]["ETFS"]
        self.assertEqual(0, row["synthetic_source_contract_candidates"])
        self.assertIn("A320_ETF_VENUE_OR_LISTING_UNVERIFIED", row["rejection_reasons"])

    def test_future_margin_and_derivative_contract_fail_closed(self):
        case = one_case("FUTUROS")
        raw = case["independent_capture"]["raw_payload"]
        raw["contract"]["variation_mark_verified"] = False
        case["independent_capture"] = resign(case["independent_capture"], "independent",
                                              raw_payload=raw, raw_payload_sha256=pit._sha(raw))
        self.assertEqual(0, pit.review_bundle(bundle(case), KEYS)["families"]["FUTUROS"]
                         ["synthetic_source_contract_candidates"])

    def test_derivative_contract_multipliers_not_ignored(self):
        for family, expected_gross in (("OPCIONES", "10100"), ("FUTUROS", "101000")):
            case = one_case(family)
            row = pit.review_bundle(bundle(case), KEYS)["families"][family]
            self.assertEqual(1, row["synthetic_paper_contract_candidates"])
            economics = row["receipt_evaluations"][0]["paper"]["economics"]
            self.assertEqual(expected_gross, economics["gross_notional"])

    def test_fixed_income_quote_is_nominal_per_hundred(self):
        case = one_case("BONOS")
        row = pit.review_bundle(bundle(case), KEYS)["families"]["BONOS"]
        economics = row["receipt_evaluations"][0]["paper"]["economics"]
        self.assertEqual("1.01", economics["gross_notional"])

    def test_volume_unit_mismatch_rejected_before_paper(self):
        case = one_case("OPCIONES")
        raw = case["independent_capture"]["raw_payload"]
        raw["contract"]["volume_kind"] = "QUANTITY"
        case["independent_capture"] = resign(case["independent_capture"], "independent",
                                              raw_payload=raw, raw_payload_sha256=pit._sha(raw))
        row = pit.review_bundle(bundle(case), KEYS)["families"]["OPCIONES"]
        self.assertEqual(0, row["synthetic_source_contract_candidates"])
        self.assertIn("A320_VOLUME_UNIT_UNVERIFIED", row["rejection_reasons"])

    def test_sell_without_signed_inventory_rejected(self):
        case = one_case("ACCIONES")
        case["paper_decision"] = resign(case["paper_decision"], "decision",
                                        side="SELL", position_available="0")
        row = pit.review_bundle(bundle(case), KEYS)["families"]["ACCIONES"]
        self.assertEqual(1, row["synthetic_source_contract_candidates"])
        self.assertEqual(0, row["synthetic_paper_contract_candidates"])

    def test_future_calendar_first_known_rejected(self):
        case = one_case("LETRAS")
        raw = case["independent_capture"]["raw_payload"]
        raw["contract"]["calendar_first_known_at_utc"] = DAY + "T19:00:00Z"
        case["independent_capture"] = resign(case["independent_capture"], "independent",
                                              raw_payload=raw, raw_payload_sha256=pit._sha(raw))
        row = pit.review_bundle(bundle(case), KEYS)["families"]["LETRAS"]
        self.assertEqual(0, row["synthetic_source_contract_candidates"])
        self.assertIn("A320_FUTURE_CALENDAR_LOOKAHEAD", row["rejection_reasons"])

    def test_cross_source_quote_must_reconcile(self):
        case = one_case("ACCIONES")
        raw = case["independent_capture"]["raw_payload"]
        raw["quote"]["ask"] = 200
        case["independent_capture"] = resign(case["independent_capture"], "independent",
                                              raw_payload=raw, raw_payload_sha256=pit._sha(raw))
        row = pit.review_bundle(bundle(case), KEYS)["families"]["ACCIONES"]
        self.assertEqual(0, row["synthetic_source_contract_candidates"])
        self.assertIn("A320_INDEPENDENT_PRICE_DISAGREEMENT", row["rejection_reasons"])

    def test_no_cross_provider_key_impersonation(self):
        case = one_case("FUTUROS")
        unsigned = {key: value for key, value in case["primary_capture"].items()
                    if key != "attestation"}
        case["primary_capture"] = pit._sign(unsigned, *KEYS["providers"]["PPI"])
        row = pit.review_bundle(bundle(case), KEYS)["families"]["FUTUROS"]
        self.assertEqual(0, row["synthetic_source_contract_candidates"])
        self.assertIn("A320_ATTESTATION_SIGNER_MISMATCH", row["rejection_reasons"])

    def test_even_mocked_main_cannot_promote_synthetic_fixtures(self):
        fake_main = {"GITHUB_REPOSITORY": "mbalbo2023/Porota-trading",
                     "GITHUB_REF": "refs/heads/main", "GITHUB_EVENT_NAME": "workflow_dispatch",
                     "A320_PIT_PAPER_VERIFY_ENABLED": "YES"}
        with mock.patch.dict("os.environ", fake_main, clear=True):
            with self.assertRaisesRegex(ValueError, "SYNTHETIC_SOURCE"):
                pit.review_bundle(bundle(one_case("ACCIONES")), KEYS,
                                  evidence_mode="AUTHENTIC_OFFHOST_EVIDENCE",
                                  trusted_main_run=True)

    def test_hmac_authentic_looking_sources_still_zero_without_external_public_signatures(self):
        case = one_case("ACCIONES")
        raw = case["independent_capture"]["raw_payload"]
        raw["official_contract_document"]["publisher"] = "UNVERIFIED_HMAC_ONLY"
        raw["official_calendar_document"]["publisher"] = "UNVERIFIED_HMAC_ONLY"
        raw["contract"]["official_source_sha256"] = pit._sha(raw["official_contract_document"])
        raw["contract"]["calendar_official_source_sha256"] = pit._sha(
            raw["official_calendar_document"])
        case["independent_capture"] = resign(case["independent_capture"], "independent",
                                              raw_payload=raw, raw_payload_sha256=pit._sha(raw))
        case["paper_decision"] = resign(
            case["paper_decision"], "decision",
            inputs_sha256={"primary": pit._sha(case["primary_capture"]),
                            "reference": pit._sha(case["independent_capture"])})
        environment = {"GITHUB_REPOSITORY": "mbalbo2023/Porota-trading",
                       "GITHUB_REF": "refs/heads/main", "GITHUB_EVENT_NAME": "workflow_dispatch",
                       "A320_PIT_PAPER_VERIFY_ENABLED": "YES"}
        with mock.patch.dict("os.environ", environment, clear=True):
            report = pit.review_bundle(bundle(case), KEYS,
                                       evidence_mode="AUTHENTIC_OFFHOST_EVIDENCE",
                                       trusted_main_run=True)
        row = report["families"]["ACCIONES"]
        self.assertEqual(1, row["case_count"])
        self.assertEqual(0, row["source_pit_rows_verified"])
        self.assertEqual(0, row["paper_sim_rows_verified"])
        self.assertIn("A320_INDEPENDENT_SENSOR_PUBLIC_SIGNATURE_VERIFICATION_REQUIRED",
                      row["rejection_reasons"])
        self.assertEqual("NOT_INTEGRATED_FAIL_CLOSED", report[
            "independent_public_sensor_verifier"])

    def test_option_exercise_risk_required(self):
        case = one_case("OPCIONES")
        raw = case["independent_capture"]["raw_payload"]
        raw["contract"]["exercise_risk_verified"] = False
        case["independent_capture"] = resign(case["independent_capture"], "independent",
                                              raw_payload=raw, raw_payload_sha256=pit._sha(raw))
        self.assertEqual(0, pit.review_bundle(bundle(case), KEYS)["families"]["OPCIONES"]
                         ["synthetic_source_contract_candidates"])

    def test_caucion_collateral_or_cash_denies_paper(self):
        case = one_case("CAUCIONES")
        raw = case["independent_capture"]["raw_payload"]
        raw["contract"]["counterparty_verified"] = False
        case["independent_capture"] = resign(case["independent_capture"], "independent",
                                              raw_payload=raw, raw_payload_sha256=pit._sha(raw))
        row = pit.review_bundle(bundle(case), KEYS)["families"]["CAUCIONES"]
        self.assertEqual(1, row["synthetic_source_contract_candidates"])
        self.assertEqual(0, row["synthetic_paper_contract_candidates"])

    def test_fci_cutoff_and_class_no_future_nav_as_decision_input(self):
        case = one_case("FCI")
        case["post_cutoff_nav"] = resign(case["post_cutoff_nav"], "independent",
                                         provider_at_utc=DAY + "T17:00:00Z")
        row = pit.review_bundle(bundle(case), KEYS)["families"]["FCI"]
        self.assertEqual(1, row["synthetic_source_contract_candidates"])
        self.assertEqual(0, row["synthetic_paper_contract_candidates"])
        case = one_case("FCI")
        raw = case["post_cutoff_nav"]["raw_payload"]
        raw["fund_class_id"] = "WRONG_CLASS"
        case["post_cutoff_nav"] = resign(case["post_cutoff_nav"], "independent",
                                         raw_payload=raw, raw_payload_sha256=pit._sha(raw))
        row = pit.review_bundle(bundle(case), KEYS)["families"]["FCI"]
        self.assertEqual(0, row["synthetic_paper_contract_candidates"])

    def test_public_hasta_fee_or_future_decision_only_source_candidate(self):
        for role_change in ("fee", "future_decision"):
            case = one_case("ACCIONES")
            if role_change == "fee":
                case["fee_receipt"] = resign(case["fee_receipt"], "fees",
                                             account_rate_status="PUBLIC_HASTA_ONLY")
            else:
                case["paper_decision"] = resign(case["paper_decision"], "decision",
                                                 features_last_known_at_utc=DAY + "T20:00:00Z")
            row = pit.review_bundle(bundle(case), KEYS)["families"]["ACCIONES"]
            self.assertEqual(1, row["synthetic_source_contract_candidates"])
            self.assertEqual(0, row["synthetic_paper_contract_candidates"])

    def test_private_bounded_capture_exact_key_and_raw_sha(self):
        target = one_case("ACCIONES")["identity"]
        raw = {"date": DAY + "T14:00:00Z", "bids": [], "offers": []}
        wire = pit._canonical(raw)
        ticks = iter((datetime.fromisoformat(DAY + "T14:00:02+00:00"),
                      datetime.fromisoformat(DAY + "T14:00:03+00:00")))
        fetch = mock.Mock(return_value=(raw, wire))
        receipt = pit.capture_ppi_book([target], fetch, now=lambda: next(ticks),
                                       pause=lambda _: None)
        fetch.assert_called_once_with(target["symbol"], target["instrument_type"],
                                      target["settlement"])
        self.assertEqual(pit.sha256(wire).hexdigest(), receipt["captures"][0]
                         ["raw_http_body_sha256"])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "private-receipt.json"
            pit._write_private_once(path, receipt)
            self.assertEqual(0o600, path.stat().st_mode & 0o777)
            with self.assertRaisesRegex(ValueError, "PRIVATE_DESTINATION_UNSAFE"):
                pit._write_private_once(path, receipt)
        with self.assertRaisesRegex(ValueError, "CAPTURE_TARGET_BUDGET"):
            pit.capture_ppi_book([target] * 11, fetch)

    def test_ppi_transport_route_and_redirect_denied(self):
        client = pit._PpiReadonlyRestBook("client", "key", "api-key", "secret")
        for method, path in (("POST", "/Order/Send"), ("GET", "/Account/Accounts"),
                             ("POST", "/MarketData/Book")):
            with self.assertRaisesRegex(ValueError, "HTTP_ROUTE_DENIED"):
                client._fetch(method, path)
        with self.assertRaisesRegex(ValueError, "PPI_LOGIN_REQUIRED"):
            client.book("GGAL", "ACCIONES", "INMEDIATA")
        client.close()


if __name__ == "__main__":
    unittest.main()
