"""A679 G2: fail-first orchestration for a future ONE-owner market feed.

These tests do not import the PPI SDK, connect to the broker, start real
subprocesses, touch PPI Watch or change the productive runtime mode.
"""

from unittest.mock import patch
from types import SimpleNamespace

import pytest

from bv_paper_runtime import ChildProcesses, main, run_clock


class FakeProcess:
    def __init__(self, pid):
        self.pid = pid
        self.exit_code = None

    def poll(self):
        return self.exit_code

    def terminate(self):
        self.exit_code = 0

    def wait(self, timeout):
        return self.exit_code


class SpawnRecorder:
    def __init__(self):
        self.calls = []

    def __call__(self, command, *, cwd):
        self.calls.append(tuple(command))
        return FakeProcess(len(self.calls))


def test_legacy_scanner_first_unchanged_without_gateway():
    clock = [100.0]
    spawn = SpawnRecorder()
    children = ChildProcesses({"scanner": ["scanner"],
                               "exit_reader": ["exit"]},
                              spawn=spawn, clock=lambda: clock[0])
    children.poll()
    assert spawn.calls == [("scanner",)]
    assert children.snapshot()["exit_reader"]["state"] == "STARTUP_WAIT"
    clock[0] += 26
    children.poll()
    assert spawn.calls == [("scanner",), ("exit",)]


def test_gateway_starts_before_consumers_and_unready_feed_blocks_them():
    clock = [100.0]
    spawn = SpawnRecorder()
    ready = [False]
    children = ChildProcesses({"scanner": ["scanner"],
                               "exit_reader": ["exit"],
                               "intraday_scalping": ["scalping"],
                               "ppi_gateway": ["fake-provider-no-network"]},
                              gateway_name="ppi_gateway",
                              gateway_ready=lambda: ready[0],
                              gateway_consumers=("scanner", "exit_reader", "intraday_scalping"),
                              spawn=spawn, clock=lambda: clock[0],
                              startup_grace_seconds=25)
    children.poll()
    assert spawn.calls == [("fake-provider-no-network",)]
    clock[0] += 26
    children.poll()
    assert spawn.calls == [("fake-provider-no-network",)]
    assert children.snapshot()["scanner"]["state"] == "WAITING_GATEWAY"
    assert children.snapshot()["exit_reader"]["state"] == "WAITING_GATEWAY"
    ready[0] = True
    children.poll()
    assert spawn.calls == [("fake-provider-no-network",), ("scanner",),
                           ("exit",), ("scalping",)]
    assert children.snapshot()["scanner"]["state"] == "RUNNING"


def test_gateway_child_without_explicit_proof_or_dependency_is_fail_closed():
    commands = {"ppi_gateway": ["not-a-real-gateway"], "scanner": ["scanner"]}
    with pytest.raises(ValueError, match="GATEWAY_ORPHANED_READINESS_CONTRACT"):
        ChildProcesses(commands)
    with pytest.raises(ValueError, match="GATEWAY_READINESS_MISSING"):
        ChildProcesses(commands, gateway_name="ppi_gateway")
    with pytest.raises(ValueError, match="GATEWAY_CONSUMER_INVALID"):
        ChildProcesses(commands, gateway_name="ppi_gateway", gateway_ready=lambda: True,
                       gateway_consumers=("not-an-active-consumer",))


def test_gateway_crash_or_failing_probe_never_spawns_new_consumer():
    clock = [100.0]
    spawn = SpawnRecorder()
    proof = [True]

    def ready():
        if proof[0] == "ERROR":
            raise OSError("fixture producer proof unavailable")
        return proof[0]

    children = ChildProcesses({"scanner": ["scanner"],
                               "ppi_gateway": ["fake-provider-no-network"]},
                              gateway_name="ppi_gateway", gateway_ready=ready,
                              gateway_consumers=("scanner",), spawn=spawn,
                              clock=lambda: clock[0], startup_grace_seconds=0)
    children.poll()  # First poll cannot spawn scanner even with premature truthy proof
    assert spawn.calls == [("fake-provider-no-network",)]
    proof[0] = "ERROR"
    children.poll()  # Probe exception fails CLOSED without crashing the clock
    assert spawn.calls == [("fake-provider-no-network",)]
    assert children.snapshot()["scanner"]["state"] == "WAITING_GATEWAY"
    spawn.calls.clear()
    producer = children.processes["ppi_gateway"]
    producer.exit_code = 1
    proof[0] = True
    children.poll()  # Stale callback must not hide child exit
    assert not spawn.calls
    assert children.snapshot()["ppi_gateway"]["state"] == "CRASH_BACKOFF"
    assert children.snapshot()["scanner"]["state"] == "WAITING_GATEWAY"


def test_productive_cli_cannot_start_unimplemented_gateway_process():
    with pytest.raises(ValueError, match="Argumentos desconocidos"):
        main(["--ppi-acquisition-worker"])


def test_gateway_unavailable_never_blocks_independent_exit_clock():
    clock = [100.0]
    spawn = SpawnRecorder()
    children = ChildProcesses({"ppi_gateway": ["fake-provider-no-network"],
                               "scanner": ["scanner"],
                               "exit_reader": ["exit"]},
                              gateway_name="ppi_gateway", gateway_ready=lambda: False,
                              gateway_consumers=("scanner", "exit_reader"),
                              spawn=spawn, clock=lambda: clock[0],
                              startup_grace_seconds=0)

    class Stop:
        count = 0

        def is_set(self):
            return self.count >= 3

        def wait(self, _):
            self.count += 1

    class Broker:
        session_policy = object()

        def supervise_futures(self, at):
            pass

        def mark_equity(self, _, *, as_of):
            pass

    class Supervisor:
        ticks = 0

        def __init__(self, *args, **kwargs):
            pass

        def tick(self):
            self.ticks += 1

        def heartbeat(self, *args):
            pass

    supervisor = Supervisor()
    with (patch("bv_paper_runtime.broker_from_environment", return_value=Broker()),
          patch("bv_paper_runtime.PositionExitSupervisor", return_value=supervisor),
          patch("bv_paper_runtime.publish_child_health", return_value=None),
          patch("rc6_performance.capture.ExitTelemetry", side_effect=ValueError("fixture"))):
        run_clock(SimpleNamespace(path="/tmp/a679-fake-no-db.sqlite"), children,
                  Stop(), clock_fn=lambda: "2026-11-05T00:00:00-03:00", interval=0)
    assert supervisor.ticks == 3
    assert spawn.calls == [("fake-provider-no-network",)]

