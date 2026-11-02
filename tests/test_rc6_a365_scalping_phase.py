"""A365: scalping error stage is bounded and never authorizes a PAPER fill.

No provider, host, secrets, Docker, network or trading account access.
"""
from __future__ import annotations

import inspect
import re
import unittest

import cf_intraday_scalping as scalping


RECORD = dict(ticker="SYNTH_GGAL", instrument_type="ACCIONES",
              market="BYMA", currency="ARS", settlement="A-24HS")


class ScalpingStageRegression(unittest.TestCase):
    def test_all_stages_are_explicit_and_closed(self):
        known = {"CAPABILITY", "READ_SCOPE", "PPI_READ", "NORMALIZE",
                 "SOURCE_VALIDATION", "PERSIST", "EVALUATE", "PAPER_GATE"}
        self.assertEqual(known, scalping.SCALPING_FAILURE_PHASES)
        for stage in known:
            self.assertEqual(stage, scalping._scalping_failure_phase(stage))
        self.assertEqual("UNKNOWN", scalping._scalping_failure_phase("RAW_PASSWORD=token"))
        self.assertEqual("UNKNOWN", scalping._scalping_failure_phase(None))

    def test_valueerror_at_provider_and_normalize_are_distinguishable(self):
        read = scalping._scalping_failure_detail(RECORD, "PPI_VALUEERROR", "PPI_READ")
        norm = scalping._scalping_failure_detail(RECORD, "PPI_VALUEERROR", "NORMALIZE")
        self.assertIn(";phase=PPI_READ;shadow_identity=", read)
        self.assertIn(";phase=NORMALIZE;shadow_identity=", norm)
        self.assertNotEqual(read, norm)
        self.assertTrue(read.startswith("SYNTH_GGAL: PPI_VALUEERROR;"))
        self.assertEqual(1, read.count("shadow_identity="))

    def test_transport_classification_not_changed(self):
        for code in ("PPI_HTTP_403", "PPI_HTTP_429", "PPI_SESSION_INVALID",
                     "PPI_BUDGET_BACKPRESSURE", "PPI_INSTRUMENT_NOT_FOUND"):
            text = scalping._scalping_failure_detail(RECORD, code, "PPI_READ")
            self.assertIn(": " + code + ";", text)

    def test_exception_message_never_exported(self):
        sensitive = "private-secret-in-vendor-error"
        local = ValueError(sensitive)
        line = scalping._scalping_failure_detail(
            RECORD, "PPI_" + type(local).__name__.upper(), "PERSIST")
        self.assertNotIn(sensitive, line)
        self.assertNotIn(str(local), line)
        self.assertIn(";phase=PERSIST;", line)

    def test_invalid_code_and_phase_fail_closed(self):
        value = scalping._scalping_failure_detail(
            RECORD, "PPI_VALUEERROR;PASSWORD=secret", "vendor-content-secret")
        self.assertIn("PPI_UNCLASSIFIED;phase=UNKNOWN;", value)
        self.assertNotIn("PASSWORD", value)
        self.assertNotIn("secret", value)

    def test_private_ledger_identity_still_exact_five_keys(self):
        detail = scalping._scalping_failure_detail(RECORD, "PPI_VALUEERROR", "CAPABILITY")
        self.assertTrue(detail.endswith(
            'shadow_identity=["SYNTH_GGAL","ACCIONES","BYMA","ARS","A-24HS"]'))

    def test_worker_phases_follow_existing_read_and_gate_flow(self):
        source = inspect.getsource(scalping.run_worker)
        stage_order = [
            'failure_phase = "CAPABILITY"',
            'failure_phase = "READ_SCOPE"',
            'failure_phase = "PPI_READ"',
            'failure_phase = "NORMALIZE"',
            'failure_phase = "SOURCE_VALIDATION"',
            'failure_phase = "PERSIST"',
            'failure_phase = "EVALUATE"',
            'failure_phase = "PAPER_GATE"',
        ]
        positions = [source.index(part) for part in stage_order]
        self.assertEqual(sorted(positions), positions)
        self.assertIn('_scalping_failure_detail(record, error_code, failure_phase)', source)
        self.assertIn('retry_read(lambda: reader.intraday(', source)
        self.assertIn('retries=0 if reprobe else 1', source)
        self.assertIn('promote_paper_candidate(store,record', source)

    def test_not_secret_bearing_middleware(self):
        source = inspect.getsource(scalping._scalping_failure_detail)
        self.assertNotIn("str(error)", source)
        self.assertNotIn("repr(", source)
        self.assertNotIn("reader.", source)
        self.assertNotIn("requests", source)
        self.assertNotIn("store.event", source)


if __name__ == "__main__":
    unittest.main()
