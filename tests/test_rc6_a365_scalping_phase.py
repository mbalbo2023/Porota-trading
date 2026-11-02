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



class A369ScalpingWorkerExceptionFlow(unittest.TestCase):
    """End-to-end fake batch, no provider/DB/network/positions."""

    def _run(self, fail_stage):
        from contextlib import ExitStack
        from unittest.mock import patch
        import sys
        import types

        class FakeStop:
            done = False
            def is_set(self):
                return self.done
            def wait(self, seconds):
                self.done = True

        class FakeStore:
            def __init__(self):
                self.events = []
            def event(self, *args):
                self.events.append(args)
            def audit_http(self, *args):
                raise AssertionError("no PPI HTTP may be called")

        class FakeReader:
            budget_enabled = False
            metrics = {"http_blocked": 0}
            last_read_error_code = None
            def __init__(self, *args, **kwargs):
                pass
            def login_once(self):
                pass
            def intraday(self, *args):
                if fail_stage == "PPI_READ":
                    raise ValueError("PRIVATE_VENDOR_SECRET_EXAMPLE")
                return [{"date": "2026-11-02T14:05:00-03:00",
                         "price": 101, "volume": 12}]
            def close(self):
                pass

        class FakeCache:
            entries = {}
            maximum = 24
            evictions = 0
            def restore(self, record, state):
                return None
            def decision(self, capability, *, at, fingerprint):
                return {"allowed": True, "reprobe": False}

        fake_guard = types.ModuleType("bd_ppi_readonly_guard")
        fake_guard.ProductionMarketReader = FakeReader
        fake_guard.retry_read = lambda call, *, retries: call()
        fake_guard.classify_read_error = lambda error: "PPI_VALUEERROR"
        fake_guard.instrument_not_found = lambda error: False
        fake_observer = types.ModuleType("bf_production_paper_observer")
        fake_observer._secret = lambda: ("SYNTHETIC_ONLY", "SYNTHETIC_ONLY")

        store = FakeStore()
        with ExitStack() as stack:
            stack.enter_context(patch.dict(sys.modules, {
                "bd_ppi_readonly_guard": fake_guard,
                "bf_production_paper_observer": fake_observer}))
            for name, value in {
                "init_schema": lambda store: None,
                "_market_open": lambda at: True,
                "_request_capability_state": lambda store, record: None,
                "_capability_fingerprint": lambda record, selection: "synthetic",
                "IntradayCapabilityCache": FakeCache,
                "_heartbeat": lambda *args, **kwargs: True,
                "select_runtime_batch": lambda store, *, limit, cursor, at: (
                    [dict(RECORD)], 0, 1,
                    {"dynamic": False, "cadence_seconds": 180}),
            }.items():
                stack.enter_context(patch.object(scalping, name, value))
            if fail_stage == "NORMALIZE":
                def normalize_payload(*args, **kwargs):
                    raise ValueError("PRIVATE_NORMALIZER_SECRET_EXAMPLE")
                stack.enter_context(patch.object(
                    scalping, "normalize_payload", normalize_payload))
            scalping.run_worker(store, FakeStop(),
                clock_fn=lambda: "2026-11-02T14:05:00-03:00")
        matches = [event for event in store.events
                   if event[0] == "INTRADAY_SCALPING_ERROR"]
        self.assertEqual(1, len(matches))
        detail = matches[0][1]
        self.assertIn(": PPI_VALUEERROR;phase=" + fail_stage + ";", detail)
        self.assertNotIn("PRIVATE_", detail)
        self.assertNotIn("SECRET_EXAMPLE", detail)
        self.assertIn("shadow_identity=", detail)

    def test_actual_except_labels_pyi_read_valueerror(self):
        self._run("PPI_READ")

    def test_actual_except_labels_local_normalize_valueerror(self):
        self._run("NORMALIZE")



if __name__ == "__main__":
    unittest.main()
