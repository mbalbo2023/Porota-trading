"""A778 OFFHOST-only tests: no SDK, PPI requests, real money or host writes."""

import threading
import unittest

from rc6_ppi_acquisition_worker import AcquisitionWorkerBlocked, run_injected_offhost


class FakeReadOnlyRPC:
    def __init__(self, *, denied=False, crash=False):
        self.denied = denied
        self.crash = crash
        self.events = []
        self.serving = threading.Event()
        self._shutdown = threading.Event()
        self.provider_calls = 0

    def start(self):
        self.events.append("start")
        if self.denied:
            raise PermissionError("PPI_UNIX_FEED_PRIVATE_PATH_REQUIRED")

    def serve_forever(self):
        self.events.append("serve")
        self.serving.set()
        if not self.crash:
            self._shutdown.wait(2)

    def shutdown(self):
        self.events.append("shutdown")
        self._shutdown.set()

    def close(self):
        self.events.append("close")


class FakeOwner:
    def __init__(self, *, rpc=None, mode="valid", login_denied=False):
        self._rpc = rpc or FakeReadOnlyRPC()
        self.mode = mode
        self.login_denied = login_denied
        self.open_calls = 0
        self.close_calls = 0
        self.provider_gets = 0

    def open_once(self):
        self.open_calls += 1
        if self.login_denied:
            raise AcquisitionWorkerBlocked("PPI_ACCOUNT_WATCH_CAPACITY_UNVERIFIED")

    def status(self):
        return {"state": "OPEN", "login_invocations_by_owner": self.open_calls,
                "paper_ready": self.mode == "unverified_paper",
                "real_order_routes_exposed": self.mode == "order_route",
                "stream_invoked": self.mode == "stream_started"}

    def close(self):
        self.close_calls += 1


class AcquisitionWorkerOffhostTests(unittest.TestCase):
    def test_default_off_never_calls_owner_factory_or_opens_socket(self):
        def forbidden_factory():
            self.fail("OFF must not instantiate SDK or RPC")
        result = run_injected_offhost(owner_factory=forbidden_factory,
                                      stop_event=threading.Event())
        self.assertEqual(result["state"], "OFF")
        self.assertFalse(result["owner_opened"])
        self.assertEqual(result["provider_calls_by_rpc"], 0)
        self.assertEqual(result["real_orders_sent"], 0)

    def test_single_injected_owner_starts_one_cached_rpc_and_stops_clean(self):
        owner = FakeOwner()
        stop = threading.Event()
        results = []
        errors = []

        def run():
            try:
                results.append(run_injected_offhost(
                    owner_factory=lambda: owner, stop_event=stop, enabled=True))
            except BaseException as error:
                errors.append(error)

        test_thread = threading.Thread(target=run)
        test_thread.start()
        self.assertTrue(owner._rpc.serving.wait(1))
        stop.set()
        test_thread.join(timeout=3)
        self.assertFalse(test_thread.is_alive())
        self.assertFalse(errors)
        self.assertEqual(results[0]["state"], "STOPPED")
        self.assertEqual(results[0]["provider_calls_by_rpc"], 0)
        self.assertEqual(owner.open_calls, 1)
        self.assertEqual(owner.close_calls, 1)
        self.assertEqual(owner.provider_gets, 0)
        self.assertEqual(owner._rpc.events,
                         ["start", "serve", "shutdown", "close"])

    def test_absent_external_capacity_denies_before_rpc_socket(self):
        owner = FakeOwner(login_denied=True)
        with self.assertRaisesRegex(AcquisitionWorkerBlocked,
                                    "PPI_ACCOUNT_WATCH_CAPACITY_UNVERIFIED"):
            run_injected_offhost(owner_factory=lambda: owner,
                                 stop_event=threading.Event(), enabled=True)
        self.assertEqual(owner._rpc.events, [])
        self.assertEqual(owner.provider_gets, 0)

    def test_unverified_paper_order_or_stream_authority_denied(self):
        for mode in ("unverified_paper", "order_route", "stream_started"):
            with self.subTest(mode=mode):
                owner = FakeOwner(mode=mode)
                with self.assertRaisesRegex(AcquisitionWorkerBlocked,
                                            "PPI_OFFHOST_OWNER_AUTHORITY_NOT_PROVEN"):
                    run_injected_offhost(owner_factory=lambda: owner,
                                         stop_event=threading.Event(), enabled=True)
                self.assertEqual(owner._rpc.events, [])
                self.assertEqual(owner.close_calls, 1)

    def test_invalid_private_socket_permission_keeps_provider_unused(self):
        owner = FakeOwner(rpc=FakeReadOnlyRPC(denied=True))
        with self.assertRaisesRegex(PermissionError,
                                    "PPI_UNIX_FEED_PRIVATE_PATH_REQUIRED"):
            run_injected_offhost(owner_factory=lambda: owner,
                                 stop_event=threading.Event(), enabled=True)
        self.assertEqual(owner._rpc.events, ["start", "close"])
        self.assertEqual(owner.close_calls, 1)
        self.assertEqual(owner.provider_gets, 0)

    def test_rpc_crash_denies_without_sdk_fallback_or_second_login(self):
        rpc = FakeReadOnlyRPC(crash=True)
        owner = FakeOwner(rpc=rpc)
        with self.assertRaisesRegex(AcquisitionWorkerBlocked,
                                    "PPI_OFFHOST_RPC_STOPPED_UNEXPECTEDLY"):
            run_injected_offhost(owner_factory=lambda: owner,
                                 stop_event=threading.Event(), enabled=True,
                                 poll_seconds=0.01)
        self.assertEqual(owner.open_calls, 1)
        self.assertEqual(owner.provider_gets, 0)
        self.assertEqual(owner.close_calls, 1)
        self.assertIn("close", rpc.events)

    def test_enabled_requires_valid_injected_contract_without_io(self):
        with self.assertRaisesRegex(AcquisitionWorkerBlocked,
                                    "PPI_OFFHOST_WORKER_INPUT_INVALID"):
            run_injected_offhost(owner_factory=None,
                                 stop_event=threading.Event(), enabled=True)


if __name__ == "__main__":
    unittest.main()
