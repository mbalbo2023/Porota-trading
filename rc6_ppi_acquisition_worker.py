"""A778 OFFHOST-only lifecycle for the already-existing G1 PPI acquisition owner.

This module has no production CLI entry point and is not imported by the RC6
runtime.  Only an explicitly injected owner can run; the default is OFF.  The
owner retains sole control of authentication, capacity and provider GETs, while
the existing AF_UNIX server serves cached, read-only observations.

An isolated G2 production cutover requires independent capacity/catalog
attestation, reader API parity and a separate committee-approved release.
"""

from __future__ import annotations

import threading
from typing import Callable, Any


SCHEMA = "rc6.ppi.acquisition-worker.offhost.v1"


class AcquisitionWorkerBlocked(RuntimeError):
    """Fail-closed lifecycle or authority problem; no fallback PPI reader."""


def run_injected_offhost(
    *,
    owner_factory: Callable[[], Any],
    stop_event: Any,
    enabled: bool = False,
    poll_seconds: float = 0.05,
) -> dict:
    """Exercise G1 owner and socket lifecycle without enabling production G2.

    A real worker is intentionally NOT wired here.  The factory and stop event
    are always injected from an OFFHOST test; neither is selected by an env
    var or a default network client.  The entire RPC surface remains cached:
    no consumer triggers an HTTP request or a second SDK login.
    """
    if enabled is not True:
        return {"schema": SCHEMA, "state": "OFF", "owner_opened": False,
                "provider_calls_by_rpc": 0, "real_orders_sent": 0}
    if (not callable(owner_factory) or not callable(getattr(stop_event, "wait", None))
            or not callable(getattr(stop_event, "is_set", None))
            or isinstance(poll_seconds, bool)
            or not isinstance(poll_seconds, (int, float))
            or not 0 < poll_seconds <= 1):
        raise AcquisitionWorkerBlocked("PPI_OFFHOST_WORKER_INPUT_INVALID")

    owner = owner_factory()
    if (not callable(getattr(owner, "open_once", None))
            or not callable(getattr(owner, "status", None))
            or not callable(getattr(owner, "close", None))):
        raise AcquisitionWorkerBlocked("PPI_OFFHOST_OWNER_CONTRACT_INVALID")

    opened = False
    server_started = False
    server = None
    listener = None
    listener_entered = threading.Event()
    listener_errors: list[str] = []
    failure = None
    try:
        owner.open_once()  # existing owner decides whether quota and catalog permit this
        opened = True
        evidence = owner.status()
        if (not isinstance(evidence, dict)
                or evidence.get("state") != "OPEN"
                or evidence.get("login_invocations_by_owner") != 1
                or evidence.get("paper_ready") is not False
                or evidence.get("real_order_routes_exposed") is not False
                or evidence.get("stream_invoked") is not False):
            raise AcquisitionWorkerBlocked("PPI_OFFHOST_OWNER_AUTHORITY_NOT_PROVEN")

        # Reuse the G1 private transport; there is no alternate socket parser,
        # provider GET route, queue, polling scheduler or SDK constructor here.
        server = getattr(owner, "_rpc", None)
        if not all(callable(getattr(server, name, None)) for name in
                   ("start", "serve_forever", "shutdown", "close")):
            raise AcquisitionWorkerBlocked("PPI_OFFHOST_RPC_CONTRACT_INVALID")
        server.start()  # G1 requires private parent 0700 and socket 0600
        server_started = True

        def serve() -> None:
            listener_entered.set()
            try:
                server.serve_forever()
            except BaseException as error:
                listener_errors.append(type(error).__name__)

        listener = threading.Thread(target=serve, name="ppi-offhost-cached-rpc",
                                    daemon=True)
        listener.start()
        if not listener_entered.wait(2):
            raise AcquisitionWorkerBlocked("PPI_OFFHOST_RPC_DID_NOT_START")
        while not stop_event.wait(poll_seconds):
            if listener_errors or not listener.is_alive():
                raise AcquisitionWorkerBlocked("PPI_OFFHOST_RPC_STOPPED_UNEXPECTEDLY")
        if listener_errors:
            raise AcquisitionWorkerBlocked("PPI_OFFHOST_RPC_FAILED")
    except BaseException as error:
        failure = error
    finally:
        cleanup_failed = False
        if server_started and listener is not None and listener.is_alive():
            try:
                server.shutdown()
            except BaseException:
                cleanup_failed = True
        if listener is not None:
            try:
                listener.join(timeout=2)
            except BaseException:
                cleanup_failed = True
        if server is not None:
            try:
                server.close()
            except BaseException:
                cleanup_failed = True
        if opened:
            try:
                owner.close()
            except BaseException:
                cleanup_failed = True
        if cleanup_failed:
            # Never return STOPPED if an HTTP guard or cached socket remains.
            failure = AcquisitionWorkerBlocked(
                "PPI_OFFHOST_RPC_OR_GUARD_CLEANUP_UNVERIFIED"
            )
        if listener is not None and listener.is_alive():
            failure = AcquisitionWorkerBlocked("PPI_OFFHOST_RPC_THREAD_STILL_ALIVE")
    if failure is not None:
        raise failure
    return {"schema": SCHEMA, "state": "STOPPED", "owner_opened": True,
            "provider_calls_by_rpc": 0, "real_orders_sent": 0}


__all__ = ["run_injected_offhost", "AcquisitionWorkerBlocked", "SCHEMA"]
