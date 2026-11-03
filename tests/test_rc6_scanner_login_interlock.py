"""A119: a crashing SQLite temp sort must not generate another login POST."""
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

from cg_paper_workspace import artifact_root
from rc6_scanner_login_interlock import ScannerLoginInterlock


BOOT = "12345678-1234-1234-1234-123456789abc"
NEXT_BOOT = "abcdef12-1234-1234-1234-123456789abc"
NS = 1_000_000_000


def setup(tmp_path, *, cooldown=900):
    database = tmp_path / "paper_v17" / "observer_v17.db"
    root = artifact_root(database)
    root.mkdir(parents=True)
    clock = [10_000 * NS]
    boot = [BOOT]

    def guard(seconds=cooldown):
        return ScannerLoginInterlock(database, seconds,
                                     monotonic_ns=lambda: clock[0],
                                     boot_id=lambda: boot[0])

    return root, clock, boot, guard


def test_full_restart_cycle_spends_one_attempt_per_900_seconds(tmp_path, monkeypatch):
    _, clock, _, guard = setup(tmp_path)
    calls = []
    # Migration from the already-active A92 scanner quarantines the first
    # installation; a missing state never causes a surprise login on deploy.
    assert guard().reserve() == "COOLDOWN"
    clock[0] += 899 * NS
    assert guard().reserve() == "COOLDOWN"
    clock[0] += NS
    assert guard().reserve() == "ALLOWED"
    calls.append("first_post")
    # Model SQLITE_FULL terminating this process after successful login.
    monkeypatch.setattr("time.time", lambda: 99_999_999_999)
    assert guard().reserve() == "COOLDOWN"  # fresh scanner, forward wall jump
    clock[0] -= NS
    assert guard().reserve() == "UNAVAILABLE"  # monotonic rollback
    clock[0] += 900 * NS + NS
    assert guard().reserve() == "ALLOWED"
    calls.append("second_post")
    assert len(calls) == 2


def test_reboot_quarantines_full_window_and_corrupt_marker_fails_closed(tmp_path):
    root, clock, boot, guard = setup(tmp_path)
    assert guard().reserve() == "COOLDOWN"
    clock[0] = 12 * NS  # monotonic clock reset by host boot
    boot[0] = NEXT_BOOT
    assert guard().reserve() == "COOLDOWN"
    clock[0] += 899 * NS
    assert guard().reserve() == "COOLDOWN"
    clock[0] += NS
    assert guard().reserve() == "ALLOWED"
    marker = root / "scanner-login-interlock-v1.json"
    marker.write_text("{truncated")
    assert guard().reserve() == "UNAVAILABLE"
    marker.unlink()
    marker.symlink_to(root / "another-file")
    assert guard().reserve() == "UNAVAILABLE"


def test_marker_absent_or_lost_renews_full_quarantine(tmp_path):
    root, clock, _, guard = setup(tmp_path)
    assert guard().reserve() == "COOLDOWN"
    marker = root / "scanner-login-interlock-v1.json"
    marker.unlink()
    clock[0] += 100 * NS
    assert guard().reserve() == "COOLDOWN"
    clock[0] += 899 * NS
    assert guard().reserve() == "COOLDOWN"
    clock[0] += NS
    assert guard().reserve() == "ALLOWED"


def test_guard_does_not_use_sqlite_and_denies_failed_durability(tmp_path, monkeypatch):
    import sqlite3
    import rc6_scanner_login_interlock as interlock

    database = tmp_path / "paper_v17" / "observer_v17.db"
    guard = interlock.ScannerLoginInterlock(database, 900,
        monotonic_ns=lambda: 123456789000000000, boot_id=lambda: BOOT)
    assert guard.reserve() == "UNAVAILABLE"  # no artifact directory yet
    artifact_root(database).mkdir(parents=True)
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **kw:
                        pytest.fail("interlock touched SQLite under SQLITE_FULL"))
    original_fsync = interlock.os.fsync
    monkeypatch.setattr(interlock.os, "fsync", lambda fd:
                        (_ for _ in ()).throw(OSError("disk full")))
    assert guard.reserve() == "UNAVAILABLE"  # no network on failed fsync
    monkeypatch.setattr(interlock.os, "fsync", original_fsync)
    assert guard.reserve() == "COOLDOWN"


def test_lowering_configuration_cannot_shorten_prior_lease(tmp_path):
    root, clock, _, guard = setup(tmp_path, cooldown=1800)
    assert guard().reserve() == "COOLDOWN"
    clock[0] += 900 * NS
    assert guard(300).reserve() == "COOLDOWN"
    clock[0] += 900 * NS
    assert guard(300).reserve() == "ALLOWED"
    marker = json.loads((root / "scanner-login-interlock-v1.json").read_text())
    assert marker["kind"] == "ATTEMPT" and marker["cooldown_ns"] == 300 * NS


def test_actual_process_restart_and_competing_scanner_claims(tmp_path):
    _, clock, _, guard = setup(tmp_path)
    script = ("import sys; from rc6_scanner_login_interlock import ScannerLoginInterlock; "
              "print(ScannerLoginInterlock(sys.argv[1],900,"
              "monotonic_ns=lambda:int(sys.argv[2]),"
              "boot_id=lambda:'12345678-1234-1234-1234-123456789abc').reserve())")
    database = str(tmp_path / "paper_v17" / "observer_v17.db")
    def restarted(tick):
        return subprocess.run([sys.executable, "-c", script, database, str(tick)],
                              text=True, capture_output=True, check=True).stdout.strip()

    assert restarted(clock[0]) == "COOLDOWN"
    assert restarted(clock[0] + 900 * NS) == "ALLOWED"
    assert restarted(clock[0] + 901 * NS) == "COOLDOWN"
    clock[0] += 1800 * NS
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: guard().reserve(), range(2)))
    assert outcomes.count("ALLOWED") == 1
    assert set(outcomes) <= {"ALLOWED", "COOLDOWN", "UNAVAILABLE"}


def test_manual_login_command_cannot_bypass_interlock(tmp_path, monkeypatch):
    import bf_production_paper_observer as observer

    class Store:
        path = str(tmp_path / "paper_v17" / "observer_v17.db")
        def __init__(self): self.states = []
        def state(self, **kwargs): self.states.append(kwargs)

    store = Store()
    outcomes = []
    monkeypatch.setattr(observer, "_finish_command", lambda *args: outcomes.append(args))
    monkeypatch.setattr(observer, "_secret", lambda:
                        pytest.fail("manual command reached credentials without lease"))
    class Deny:
        def reserve(self): return "COOLDOWN"
    assert observer._run_command(store, None, (1, "LOGIN_AND_SYNC"),
                                 login_interlock=Deny()) is None
    assert outcomes[0][2] == "COOLDOWN"
    assert store.states[-1]["real_orders_sent"] == 0
