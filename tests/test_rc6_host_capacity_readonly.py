"""Cheap unit guards. Synthetic fixtures never qualify the actual Droplet."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from scripts import rc6_host_capacity_readonly as audit


SOURCE = {"sha": "a" * 40, "tree": "b" * 40, "collector_sha256": "c" * 64,
          "owner_session": "unit-owner", "run_id": "123", "run_attempt": "1"}
AT = "2026-10-10T18:00:00+00:00"
IMAGE = "sha256:" + "d" * 64


@pytest.fixture
def receipt(monkeypatch):
    identity = {"host_repository_sha": "e" * 40, "repository_version": {"MODE": "PRODUCTION_PAPER", "REAL_ORDER_CAPABILITY": "BLOCKED"},
                "operation_mode": "PRODUCTION_PAPER", "execution": "SIMULATED", "real_order_route_capability": "BLOCKED",
                "recorded_real_order_routes": "NOT_CALLED", "recorded_candidate_sha": "f" * 40,
                "recorded_deploy_sha": "e" * 40, "recorded_image_id": IMAGE, "deploy_state_recorded_at": AT}
    cg = {"state": "OBSERVED", "path_sha256": "f" * 64, "memory_current": 1000, "memory_peak": 2000,
          "memory_max": "UNLIMITED", "memory_swap_current": 0, "memory_swap_max": "UNLIMITED",
          "pids_current": 2, "pids_max": "UNLIMITED", "cpu_quota_usec": "UNLIMITED", "cpu_period_usec": 100000,
          "cpu_stat": {"usage_usec": 1, "nr_throttled": 0, "throttled_usec": 0},
          "memory_stat": dict.fromkeys(("anon", "file", "shmem", "kernel", "pagetables", "slab"), 1),
          "memory_events": dict.fromkeys(("oom", "oom_kill", "high", "max"), 0)}
    containers = [{"role": name, "state": "OBSERVED", "container_id": "a" * 64, "image_id": IMAGE, "running": True,
                   "started_at": AT, "restart_count": 0, "memory_limit_bytes": 0, "nano_cpus": 0, "pids_limit": None,
                   "readonly_rootfs": False, "process": {"pid": 42, "start_ticks": 123, "cpu_ticks": 10, "rss_bytes": 500},
                   "cgroup": cg, "reported_logical_image_bytes": 1000} for name in audit.CONTAINERS]
    safety = {"state": "OBSERVED", "mode": "PRODUCTION_PAPER", "real_orders_sent": 0, "heartbeat_at": AT,
              "open_mode": "MODE_RO_QUERY_ONLY", "write_seal": {"abi": 3, "filesystem_writes_denied": True, "wal_ignored": False}}
    sample = {"observed_at": AT, "monotonic_ns": 1000000, "online_cpu_count": 1, "clock_ticks_per_second": 100,
              "cpu_total_ticks": 100, "cpu_idle_iowait_ticks": 50, "load_average": [0.1, 0.2, 0.3],
              "memory_bytes": dict.fromkeys(("MemTotal", "MemAvailable", "MemFree", "Buffers", "Cached", "SReclaimable", "Shmem", "Dirty", "Writeback", "SwapTotal", "SwapFree"), 10000),
              "process_count": 2, "process_rss_sum_bytes": 500, "processes_disappeared_during_sample": 0,
              "rss_sum_metric": "SUM_RSS_INCLUDES_SHARED_PAGES_NOT_UNIQUE_HOST_MEMORY"}
    filesystem = {"state": "OBSERVED", "device_major": 8, "device_minor": 1, "filesystem_type": "ext4", "mount_id": 25,
                  "allocation_unit_bytes": 4096, "total_bytes": 25 * 1024**3, "available_bytes": 10 * 1024**3,
                  "free_bytes": 10 * 1024**3, "inode_total": 10000, "inode_available": 2000, "inode_free_percent": 20.0, "readonly": False}
    units = [{key: name if key == "Id" else "active" if key in ("ActiveState", "LoadState", "SubState", "UnitFileState", "Result") else 0
              for key in audit.SERVICE_PROPERTIES} for name in audit.UNITS]
    monkeypatch.setattr(audit, "utcnow", lambda: AT)
    monkeypatch.setattr(audit.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(audit, "runtime_identity", lambda: deepcopy(identity))
    monkeypatch.setattr(audit, "containers", lambda: deepcopy(containers))
    monkeypatch.setattr(audit, "safety_child", lambda: deepcopy(safety))
    monkeypatch.setattr(audit, "sample", lambda: deepcopy(sample))
    monkeypatch.setattr(audit, "filesystem", lambda role, path: {"role": role, **filesystem})
    monkeypatch.setattr(audit, "docker_filesystem", lambda: {"role": "DOCKER", **filesystem})
    monkeypatch.setattr(audit, "file_metadata", lambda role, path: {"role": role, "state": "ABSENT"})
    monkeypatch.setattr(audit, "services", lambda: {"state": "OBSERVED", "units": units})
    monkeypatch.setattr(audit, "image_inventory", lambda: {"state": "OBSERVED", "unique_image_count": 2, "identity_set_sha256": "e" * 64,
                                                         "physical_unique_layer_bytes": None, "physical_layer_load_state": "NO_VERIFICADO_NO_HEAVY_SCAN"})
    monkeypatch.setattr(audit, "bounded_read", lambda path, limit=65536: "unit-boot-id")
    return audit.collect(SOURCE)


def test_complete_observations_never_grant_release_or_host_margin(receipt):
    assert audit.validate_public_receipt(receipt, SOURCE) is receipt
    assert receipt["host_release_admission"] == "NO_VERIFICADO"
    assert receipt["host_margin_green"] is False
    assert "EXACT_CANDIDATE_PROFILE_AND_CONCURRENT_PEAK_REQUIRED" in receipt["blockers"]
    assert "EXACT_ARTIFACT_TRANSFER_AND_LAYER_LOAD_REQUIRED" in receipt["blockers"]
    assert receipt["resource_reserves"]["memory_os_services_reserve_bytes"] is None
    assert receipt["invariants"]["recurrent_additional_usd"] == 0


@pytest.mark.parametrize("field,value", [
    ("host_release_admission", "GREEN"), ("host_margin_green", True),
    ("source", {**SOURCE, "sha": "f" * 40}),
])
def test_observation_cannot_forge_green_or_source(receipt, field, value):
    receipt[field] = value
    with pytest.raises(audit.ObservationError):
        audit.validate_public_receipt(receipt, SOURCE)


@pytest.mark.parametrize("attack", ["arbitrary_nested_key", "profile_path", "token_text", "foreign_container", "foreign_unit", "dropped_safety", "changed_reserve"])
def test_public_artifact_rejects_sensitive_and_foreign_payloads(receipt, attack):
    if attack == "arbitrary_nested_key":
        receipt["runtime_containers"][0][0]["ConfigEnv"] = ["123456789"]
    elif attack == "profile_path":
        receipt["runtime_identities"][0]["execution"] = "/home/owner/browser-profile"
    elif attack == "token_text":
        receipt["runtime_identities"][0]["execution"] = "Bearer-cannot-export"
    elif attack == "foreign_container":
        receipt["runtime_containers"][0][0]["role"] = "ppi_watch"
    elif attack == "foreign_unit":
        receipt["services_and_timers"]["units"][0]["Id"] = "ppi-watch.service"
    elif attack == "dropped_safety":
        receipt["safety_samples"].pop()
    else:
        receipt["resource_reserves"]["disk_residual_bytes"] -= 1
    with pytest.raises(audit.ObservationError):
        audit.validate_public_receipt(receipt, SOURCE)


def test_stale_heartbeat_nonzero_orders_recycled_pid_and_image_are_discriminant(receipt):
    receipt["safety_samples"][-1]["heartbeat_at"] = "2026-10-10T17:00:00+00:00"
    receipt["safety_samples"][-1]["real_orders_sent"] = 1
    receipt["runtime_containers"][-1][0]["process"]["start_ticks"] += 1
    receipt["runtime_containers"][-1][0]["image_id"] = "sha256:" + "e" * 64
    failures = audit.blockers(receipt)
    assert "LIVE_RUNTIME_HEARTBEAT_NOT_FRESH" in failures
    assert "LIVE_PAPER_SAFETY_REJECTED" in failures
    assert "LIVE_RUNTIME_IDENTITY_CHANGED_DURING_AUDIT" in failures
    assert "LIVE_IMAGE_VS_RECORDED_IMAGE_UNVERIFIED" in failures
    with pytest.raises(audit.ObservationError, match="AUDIT_BLOCKERS_NOT_RECOMPUTED"):
        audit.validate_public_receipt(receipt, SOURCE)
    receipt["blockers"] = failures
    audit.validate_public_receipt(receipt, SOURCE)  # A truthful RED can be retained.


def test_lifetime_cgroup_peak_is_not_candidate_concurrent_peak(receipt):
    receipt["runtime_containers"][0][0]["cgroup"]["memory_peak"] = 777777
    assert receipt["resource_reserves"]["candidate_memory_peak_bytes"] is None
    assert receipt["host_margin_green"] is False
    audit.validate_public_receipt(receipt, SOURCE)


def test_incomplete_sampling_and_unavailable_write_seal_cannot_hide_red(receipt):
    receipt["host_samples"].pop()
    receipt["cpu_intervals"] = audit.cpu_intervals(receipt["host_samples"])
    receipt["safety_samples"][0] = {"state": "NO_VERIFICADO", "error_code": "SAFETY_LANDLOCK_UNAVAILABLE"}
    receipt["blockers"] = audit.blockers(receipt)
    assert "HOST_RESOURCE_OBSERVATION_UNAVAILABLE" in receipt["blockers"]
    assert "SAFETY_LANDLOCK_UNAVAILABLE" in receipt["blockers"]
    audit.validate_public_receipt(receipt, SOURCE)


def test_seal_failure_prevents_even_opening_the_live_database(tmp_path):
    connected = []
    def deny():
        raise audit.ObservationError("SAFETY_LANDLOCK_UNAVAILABLE")
    with pytest.raises(audit.ObservationError, match="SAFETY_LANDLOCK_UNAVAILABLE"):
        audit.safety_row(tmp_path / "unread.db", seal=deny, connect=lambda *a, **kw: connected.append(True))
    assert connected == []


@pytest.mark.parametrize("seal", [{"filesystem_writes_denied": False, "wal_ignored": False},
                                  {"filesystem_writes_denied": True, "wal_ignored": True}])
def test_no_write_seal_or_wal_ignoring_never_opens_database(tmp_path, seal):
    connected = []
    with pytest.raises(audit.ObservationError, match="SAFETY_WRITE_SEAL_NOT_VERIFIED"):
        audit.safety_row(tmp_path / "unread.db", seal=lambda: seal, connect=lambda *a, **kw: connected.append(True))
    assert connected == []


def test_original_sqlite_vm_budget_is_discriminant_without_db_mutation(tmp_path):
    # Actual own SQLite work, not a replay of the live host or a quota proof.
    path = tmp_path / 'bounded-unit.db'
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE observer_state(id INTEGER, mode TEXT, real_orders_sent INTEGER, heartbeat_at TEXT)')
        connection.executemany('INSERT INTO observer_state VALUES(2,\'PRODUCTION_PAPER\',0,?)', [(AT,)] * 2000)
        connection.execute('INSERT INTO observer_state VALUES(1,\'PRODUCTION_PAPER\',0,?)', (AT,))
    before = path.read_bytes()
    with pytest.raises(audit.ObservationError, match='SAFETY_SQLITE_ORIGINAL_VM_BUDGET_EXCEEDED'):
        audit.safety_row(path, seal=lambda: {'abi': 3, 'filesystem_writes_denied': True, 'wal_ignored': False})
    assert path.read_bytes() == before


def test_safety_query_is_single_indexed_row_with_query_only_and_mode_ro(tmp_path):
    # Local fixture is a unit test of the query contract, not native host safety.
    path = tmp_path / "unit.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE observer_state(id INTEGER PRIMARY KEY, mode TEXT, real_orders_sent INTEGER, heartbeat_at TEXT)")
    conn.execute("INSERT INTO observer_state VALUES(1,'PRODUCTION_PAPER',0,?)", (AT,))
    conn.execute("INSERT INTO observer_state VALUES(2,'PRIVATE_ROW',999,?)", (AT,))
    conn.commit()
    conn.close()
    before = path.read_bytes()
    calls = []
    def connect(database_uri, **kwargs):
        assert database_uri == f"file:{path}?mode=ro"
        assert "immutable" not in database_uri
        c = sqlite3.connect(database_uri, **kwargs)
        c.set_trace_callback(calls.append)
        return c
    value = audit.safety_row(path, seal=lambda: {"abi": 3, "filesystem_writes_denied": True, "wal_ignored": False}, connect=connect)
    assert value["mode"] == "PRODUCTION_PAPER" and value["real_orders_sent"] == 0
    assert calls == ["PRAGMA query_only=ON", "PRAGMA busy_timeout=200", "PRAGMA query_only", audit.QUERY]
    assert path.read_bytes() == before
    assert "PRIVATE_ROW" not in json.dumps(value)


@pytest.mark.parametrize("argv", [
    ["docker", "exec", "porota_production_observer", "python", "-c", "1"],
    ["docker", "inspect", "porota_production_observer"],
    ["systemctl", "restart", "porota-preopen-rc6.service"],
    ["cat", "/proc/1/environ"], ["du", "-s", "/var/lib/docker"],
])
def test_mutating_or_unbounded_commands_are_rejected_before_execution(monkeypatch, argv):
    called = []
    monkeypatch.setattr(audit.subprocess, "run", lambda *a, **kw: called.append(a))
    with pytest.raises(audit.ObservationError, match="COMMAND_NOT_ALLOWLISTED"):
        audit.run(argv)
    assert called == []


def test_cpu_interval_arithmetic_and_no_guest_double_count():
    samples = [{"cpu_total_ticks": 100, "cpu_idle_iowait_ticks": 80, "monotonic_ns": 100},
               {"cpu_total_ticks": 200, "cpu_idle_iowait_ticks": 105, "monotonic_ns": 200}]
    assert audit.cpu_intervals(samples) == [{"elapsed_ns": 100, "busy_percent": 75.0}]


def test_bounded_file_reads_refuse_symlinks_and_oversize(tmp_path):
    target = tmp_path / "file"
    target.write_text("hello")
    alias = tmp_path / "alias"
    alias.symlink_to(target)
    with pytest.raises(OSError):
        audit.bounded_read(alias)
    with pytest.raises(audit.ObservationError, match="OBSERVATION_INPUT_TOO_LARGE"):
        audit.bounded_read(target, 4)
    assert audit.bounded_read(target, 5) == "hello"


def test_sanitized_manifest_binds_the_public_bytes(receipt, tmp_path, monkeypatch):
    private = tmp_path / "private.json"
    private.write_text(json.dumps(receipt))
    output = tmp_path / "evidence.json"
    # The fixture factory patched reads; restore the actual safe local reader.
    monkeypatch.undo()
    argv = ["--source-sha", SOURCE["sha"], "--source-tree", SOURCE["tree"], "--collector-sha256", SOURCE["collector_sha256"],
            "--owner-session", SOURCE["owner_session"], "--run-id", SOURCE["run_id"], "--run-attempt", SOURCE["run_attempt"],
            "--sanitize-evidence", str(private), "--output", str(output)]
    assert audit.main(argv) == 0
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["evidence_sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    assert manifest["source"] == SOURCE and manifest["host_margin_green"] is False
    assert manifest["evidence_bytes"] == len(output.read_bytes())


@pytest.mark.parametrize("private_text,status,expected", [
    ("DO_BINDING_MISSING", 125, "DO_BINDING_MISSING"),
    ("WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED! offending-host.example 192.0.2.99", 255, "SSH_HOST_KEY_MISMATCH"),
    ("secret-user@private-host.example: Permission denied (publickey).", 255, "SSH_KEY_NOT_ACCEPTED"),
    ("Load key \"/home/private-user/secret-key\": invalid format", 255, "SSH_KEY_FORMAT_INVALID"),
    ("sudo: a password is required: never-export-this", 1, "REMOTE_SUDO_NONINTERACTIVE_NOT_AUTHORIZED"),
    ("sudo: a terminal is required to read the password: hostname.example", 1, "REMOTE_SUDO_NONINTERACTIVE_NOT_AUTHORIZED"),
    ("timeout: failed to run command ‘python3’: No such file or directory /home/user", 127, "REMOTE_PYTHON_UNAVAILABLE"),
    ("ssh: connect to host private-ip port 22: Connection timed out", 255, "TIMEOUT"),
    ("anything-private", 124, "TIMEOUT"),
    ("Could not resolve hostname private-host.example", 255, "SSH_HOST_NAME_UNRESOLVED"),
    ("unexpected sensitive account 123456789 token credential", 255, "UNKNOWN_TRANSPORT_FAILURE"),
])
def test_closed_transport_first_cause_never_exports_private_text(private_text, status, expected):
    raw = private_text.encode()
    result = audit.classify_transport_error(raw, status)
    assert result["error_code"] == expected
    assert result["stderr_bytes"] == len(raw)
    assert result["stderr_sha256"] == hashlib.sha256(raw).hexdigest()
    assert result["raw_stderr_exported"] is False
    public = json.dumps(result)
    for private_value in ("offending-host.example", "192.0.2.99", "secret-user", "private-host.example", "/home/", "123456789", "credential"):
        assert private_value not in public


def test_remote_audit_errno_is_discriminant_but_unknown_code_cannot_leak():
    result = audit.classify_transport_error(b"RC6_HOST_READONLY_AUDIT_FAILED:OBSERVATION_OSERROR:ERRNO=13\n", 1)
    assert result["error_code"] == "REMOTE_AUDIT_REJECTED"
    assert result["remote_error_code"] == "OBSERVATION_OSERROR" and result["linux_errno"] == 13
    fake = audit.classify_transport_error(b"RC6_HOST_READONLY_AUDIT_FAILED:OWNER_SECRET_TOKEN\n", 1)
    assert fake["error_code"] == "UNKNOWN_TRANSPORT_FAILURE" and fake["remote_error_code"] is None
    assert "OWNER_SECRET" not in json.dumps(fake)


def test_oversize_transport_stderr_is_not_exported_or_falsely_hashed(tmp_path):
    private = tmp_path / "transport.stderr"
    private.write_bytes(b"secret-host-account" * 4096)
    output = tmp_path / "error.json"
    result = audit.preserve_transport_error(private, 255, SOURCE, output)
    assert result["error_code"] == "SSH_STDERR_BOUND_EXCEEDED"
    assert result["stderr_bytes"] == private.stat().st_size
    assert result["stderr_sha256"] is None
    assert result["stderr_digest_origin"] == "NOT_CAPTURED_BOUND_EXCEEDED"
    assert b"secret-host" not in output.read_bytes()
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["evidence_sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()


def test_transport_error_artifact_binds_exact_source_and_hash_only(tmp_path):
    private = tmp_path / "transport.stderr"
    private.write_bytes(b"private-owner@private-host: Permission denied (publickey).\n")
    output = tmp_path / "error.json"
    result = audit.preserve_transport_error(private, 255, SOURCE, output)
    assert result["source"] == SOURCE and result["error_code"] == "SSH_KEY_NOT_ACCEPTED"
    assert result["host_margin_green"] is False and result["host_release_admission"] == "NO_VERIFICADO"
    assert b"private-owner" not in output.read_bytes() and b"private-host" not in output.read_bytes()


def test_existing_workflow_is_exact_dispatch_only_with_public_allowlist():
    import yaml
    root = Path(__file__).resolve().parents[1]
    workflow = root / ".github/workflows/rc6-current-host-contract-evidence-readonly-audit-20260910.yml"
    text = workflow.read_text()
    parsed = yaml.safe_load(text)
    trigger = parsed.get("on", parsed.get(True))
    assert set(trigger) == {"workflow_dispatch"}
    assert set(trigger["workflow_dispatch"]["inputs"]) == {"source_sha", "source_tree", "owner_session"}
    steps = parsed["jobs"]["audit"]["steps"]
    for step in steps:
        if "uses" in step:
            assert audit.SHA40.fullmatch(step["uses"].split("@")[1])
    checkout = steps[0]["with"]
    assert checkout == {"ref": "${{ inputs.source_sha }}", "fetch-depth": 1, "persist-credentials": False}
    assert 'test "$RC6_CONTROLLER_SHA" = "$RC6_SOURCE_SHA"' in text
    assert 'test "$(git rev-parse HEAD^{tree})" = "$RC6_SOURCE_TREE"' in text
    assert "StrictHostKeyChecking=yes" in text
    upload = next(step for step in steps if step.get("uses", "").startswith("actions/upload-artifact@"))
    assert upload["with"]["path"] == "${{ runner.temp }}/rc6-host-audit-public/*.json"
    assert "docker exec" not in "\n".join(step.get("run", "") for step in steps)
    assert "rc6-host-audit-private/*" not in upload["with"]["path"]
    assert parsed["concurrency"]["cancel-in-progress"] is False
    for step in steps:
        if "run" in step:
            parsed_shell = audit.subprocess.run(["bash", "-n"], input=step["run"], text=True, capture_output=True)
            assert parsed_shell.returncode == 0, parsed_shell.stderr
