"""A411: local scalping failures cannot masquerade as PPI read failures.

Deterministic, network-free regression. The private ledger retains exact
identity; no provider request, trade or host action is performed here.
"""

import inspect
import unittest

import cf_intraday_scalping as scalping


RECORD = dict(ticker="SYNTHETIC_ONE", instrument_type="ACCIONES",
              market="BYMA", currency="ARS", settlement="A-24HS")


class A411ScalpingErrorOrigin(unittest.TestCase):
    def test_actual_read_boundary_keeps_ppi_classification(self):
        self.assertEqual("PPI_VALUEERROR",
                         scalping._scalping_error_code(ValueError("PRIVATE_VENDOR_BODY"),
                                                       "PPI_VALUEERROR"))
        self.assertEqual("PPI_SESSION_INVALID",
                         scalping._scalping_error_code(ValueError("PRIVATE_AUTH_BODY"),
                                                       "PPI_SESSION_INVALID"))

    def test_local_clock_and_normalizer_never_claim_ppi(self):
        for phase in ("CAPABILITY", "NORMALIZE", "SOURCE_VALIDATION", "PERSIST",
                      "EVALUATE", "PAPER_GATE"):
            code = scalping._scalping_error_code(ValueError("PRIVATE_LOCAL_CLOCK"), None)
            self.assertEqual("SCALPING_LOCAL_VALUEERROR", code)
            detail = scalping._scalping_failure_detail(RECORD, code, phase)
            self.assertIn("SCALPING_LOCAL_VALUEERROR;phase=" + phase, detail)
            self.assertNotIn("PPI_VALUEERROR", detail)
            self.assertNotIn("PRIVATE_", detail)
            self.assertEqual(1, detail.count("shadow_identity="))

    def test_untrusted_classifier_or_class_name_cannot_export_secrets(self):
        invalid = "PPI_VALUEERROR;TOKEN=PRIVATE"
        self.assertEqual("PPI_UNCLASSIFIED",
                         scalping._scalping_error_code(ValueError(invalid), invalid))
        self.assertIn("PPI_UNCLASSIFIED;phase=PPI_READ;",
                      scalping._scalping_failure_detail(RECORD, invalid, "PPI_READ"))
        fake_type = type("Malformed;TokenPRIVATE", (ValueError,), {})
        code = scalping._scalping_error_code(fake_type("PRIVATE_LOCAL"), None)
        self.assertEqual("SCALPING_LOCAL_EXCEPTION", code)
        valid_named_type = type("PRIVATEACCESSKEY", (ValueError,), {})
        self.assertEqual("SCALPING_LOCAL_EXCEPTION",
                         scalping._scalping_error_code(valid_named_type("ignored"), None))
        self.assertNotIn("PRIVATE", scalping._scalping_failure_detail(RECORD, code,
                                                                     "NORMALIZE"))

    def test_only_error_telemetry_changes_no_entry_or_routing_gate(self):
        source = inspect.getsource(scalping.run_worker)
        self.assertIn("error_code = _scalping_error_code(exc, read_error_code)", source)
        self.assertIn("if read_error_code == \"PPI_INSTRUMENT_NOT_FOUND\"", source)
        self.assertIn("if error_code == \"PPI_SESSION_INVALID\"", source)
        self.assertIn("promote_paper_candidate(store,record", source)
        classifier = inspect.getsource(scalping._scalping_error_code)
        self.assertNotIn("str(error)", classifier)
        self.assertNotIn("repr(", classifier)


if __name__ == "__main__":
    unittest.main()
