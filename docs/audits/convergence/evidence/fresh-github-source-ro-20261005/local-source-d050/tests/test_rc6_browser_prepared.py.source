"""Permanent preparation/consumer separation guards using native sealed data.

Protocol exit/deadline faults use a stand-in transport only and make no native
financial or browser acceptance claim. Positive data uses the real157 writer.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tests.rc6_browser_ipc import GateFailure, PROTOCOL, ProductClient, frame, source_inventory
from tests.rc6_browser_prepared import initialize_prepared_product, prepare_native_fixture
from tests.test_rc6_browser_product_ipc import complete_archive


@pytest.fixture(scope="module")
def prepared_normal(complete_archive, tmp_path_factory):
    root, index, _ = complete_archive
    return prepare_native_fixture(sys.executable, tmp_path_factory.mktemp("prepared-normal-parent") / "fixture",
        source_root=root, index=index, variant="NORMAL", python_version=f"{sys.version_info.major}.{sys.version_info.minor}")


def test_preparation_finishes_ten_native_ticks_before_readonly_rpc_and_keeps_original_caps(prepared_normal, complete_archive):
    root, index, _ = complete_archive
    receipt, launcher = prepared_normal["receipt"], prepared_normal["launcher_receipt"]
    assert receipt["product_environment"]["installed_count"] == 157 and receipt["source_pin_complete"]
    assert receipt["native_phase"] == "OPEN" and receipt["native_ticks"][0]["phase"] == "PREOPEN"
    assert len(receipt["native_ticks"]) == 10 and receipt["pointer"]["sequence"] == 10
    assert launcher["status"] == "COMPLETE" and launcher["returncode"] == 0 and launcher["reaped"]
    assert not launcher["cleanup_forced"] and launcher["source_hashes_modes_blobs_unchanged"]
    assert launcher["elapsed_seconds_including_startup_emission_and_reap"] <= launcher["preparation_cap_seconds"] == 90
    assert receipt["big_acceptance_claim"] is False and len(receipt["catalog_full_identities"]) == 25
    assert set(receipt["manifest"]["files"]) == {"report", "checkpoint", "status", "projection"}
    with ProductClient(sys.executable, root=root, index=index, require_complete_index=True) as product:
        source = initialize_prepared_product(product, prepared_normal)
        assert source["mode"] == "PREPARED_SMALL" and source["prepared_small_variant"] == "NORMAL"
        response = product.render("/en-vivo/posiciones", {"family": "FUTUROS"})
        assert "DLR/OCT26" in response["html"] and "WATCH_NO_QUOTE" in response["html"]
        assert response["pointer"] == receipt["pointer"] and response["source_cut"] == receipt["source_cut"]
        assert response["request_wall_seconds_including_ipc_html_json"] <= 1
        assert response["response_json_bytes"] < 4 * 1024**2
    assert product.finish_receipt["source_proof_pass"] and product.finish_receipt["native_custody_unchanged"]
    assert product.finish_receipt["big_acceptance_claim"] is False
    assert not product.driver_sqlite and not product.driver_network


def test_multifamily_rpc_cannot_generate_fixture_before_its_twenty_second_request():
    with ProductClient(sys.executable) as product:
        with pytest.raises(GateFailure, match="^SMALL_FIXTURE_REQUIRES_PREPARATION_OUTSIDE_RPC$"):
            product.request("initialize", mode="MULTIFAMILY")
    assert product.finish_receipt["source_proof_pass"]
    assert product.finish_receipt["custody_verification_scope"] == "NOT_EXERCISED"


def test_small_preparation_cannot_satisfy_unchanged_large_acceptance(prepared_normal, complete_archive):
    root, index, _ = complete_archive
    with ProductClient(sys.executable, root=root, index=index, require_complete_index=True) as product:
        with pytest.raises(GateFailure, match="^NATIVE_LARGE_REPORT_NOT_EXERCISED$"):
            product.request("initialize", mode="LARGE", database=str(prepared_normal["database"]), root=str(prepared_normal["root"]))
    assert product.finish_receipt["native_custody_unchanged"] and product.finish_receipt["source_proof_pass"]


@pytest.mark.parametrize("fault", ("foreign_source", "fake_scope", "preopen", "stale_pointer", "rehashed_tick", "currency", "custody", "manifest_type"))
def test_rehashed_prepared_receipt_cannot_change_native_source_scope_cut_identity_or_custody(prepared_normal, complete_archive, fault):
    root, index, _ = complete_archive
    value = deepcopy(prepared_normal["receipt"])
    if fault == "foreign_source": value["source_sha"] = "0" * 40
    elif fault == "fake_scope": value["scope"] = "BIG_ACCEPTED"
    elif fault == "preopen": value["native_phase"] = "PREOPEN"
    elif fault == "stale_pointer": value["pointer"]["sequence"] = 9
    elif fault == "rehashed_tick": value["native_ticks"][2]["generation_id"] = "0" * 32
    elif fault == "currency": value["catalog_full_identities"][0][3] = "USD"
    elif fault == "manifest_type": value["manifest"]["sequence"] = 10.0
    else: next(iter(value["custody_inventory"].values()))["sha256"] = "0" * 64
    raw = frame({"protocol": PROTOCOL, "id": 0, "ok": True, "result": value})
    path = prepared_normal["receipt_path"].with_name(f"fault-{fault}.json")
    path.write_bytes(raw)  # Fault only in a copied returned receipt, not data.
    launcher = deepcopy(prepared_normal["launcher_receipt"])
    launcher["stdout_sha256"] = hashlib.sha256(raw).hexdigest()
    launcher_path = prepared_normal["launcher_path"].with_name(f"fault-{fault}-launcher.json")
    launcher_path.write_text(json.dumps(launcher, sort_keys=True, indent=2) + "\n")
    with ProductClient(sys.executable, root=root, index=index, require_complete_index=True) as product:
        with pytest.raises(GateFailure, match="^PREPARED_"):
            product.request("initialize", mode="PREPARED_SMALL", database=str(prepared_normal["database"]),
                root=str(prepared_normal["root"]), prepared_receipt=str(path),
                prepared_receipt_sha256=hashlib.sha256(raw).hexdigest(), prepared_launcher=str(launcher_path),
                prepared_launcher_sha256=hashlib.sha256(launcher_path.read_bytes()).hexdigest())
    assert product.finish_receipt["source_proof_pass"]


def test_prepared_receipt_pin_rejects_changed_bytes_even_with_actual_native_data(prepared_normal, complete_archive):
    root, index, _ = complete_archive
    raw = prepared_normal["receipt_path"].read_bytes() + b" "
    path = prepared_normal["receipt_path"].with_name("fault-pin.json")
    path.write_bytes(raw)
    with ProductClient(sys.executable, root=root, index=index, require_complete_index=True) as product:
        with pytest.raises(GateFailure, match="^PREPARED_RECEIPT_PIN_CHANGED$"):
            product.request("initialize", mode="PREPARED_SMALL", database=str(prepared_normal["database"]),
                root=str(prepared_normal["root"]), prepared_receipt=str(path),
                prepared_receipt_sha256=prepared_normal["receipt_sha256"], prepared_launcher=str(prepared_normal["launcher_path"]),
                prepared_launcher_sha256=prepared_normal["launcher_sha256"])


@pytest.mark.parametrize("fault", ("exit1", "not_reaped", "forced_cleanup", "after_cap"))
def test_consumer_requires_observed_successful_launcher_after_data_ready(prepared_normal, complete_archive, fault):
    """Additive Source precision; separate from the original18 controls."""
    root, index, _ = complete_archive
    launcher = deepcopy(prepared_normal["launcher_receipt"])
    if fault == "exit1": launcher["returncode"] = 1
    elif fault == "not_reaped": launcher["reaped"] = False
    elif fault == "forced_cleanup": launcher["cleanup_forced"] = True
    else: launcher["elapsed_seconds_including_startup_emission_and_reap"] = 90.000001
    path = prepared_normal["launcher_path"].with_name(f"completion-fault-{fault}.json")
    path.write_text(json.dumps(launcher, sort_keys=True, indent=2) + "\n")
    supplied = {**prepared_normal, "launcher_path": path,
                "launcher_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    with ProductClient(sys.executable, root=root, index=index, require_complete_index=True) as product:
        with pytest.raises(GateFailure, match="^PREPARED_LAUNCHER_COMPLETION_REJECTED$"):
            initialize_prepared_product(product, supplied)
    assert product.finish_receipt["source_proof_pass"]


@pytest.mark.parametrize("destination", ("source", "existing", "parent_symlink"))
def test_native_preparation_cli_rejects_output_before_fixture_or_source_write(complete_archive, tmp_path, destination):
    root, index, _ = complete_archive
    if destination == "source": output = root / "must-not-create-fixture"
    elif destination == "existing":
        output = tmp_path / "existing"
        output.mkdir()
        (output / "keep.txt").write_text("unchanged\n")
    else:
        alias = tmp_path / "source-alias"
        alias.symlink_to(root, target_is_directory=True)
        output = alias / "must-not-create-fixture"
    before = source_inventory(root)
    out_before = source_inventory(output) if output.exists() else None
    completed = subprocess.run([sys.executable, "-I", "-B", str(root / "tests/ci_rc6_browser_prepare.py"),
        "--expected-python", sys.executable, "--index", str(index), "--output", str(output), "--variant", "NORMAL"],
        capture_output=True, timeout=20)
    assert completed.returncode == 2 and b"OUTPUT_MUST_BE_NEW_AND_OUTSIDE_SOURCES" in completed.stderr
    assert source_inventory(root) == before
    assert source_inventory(output) == out_before if out_before is not None else not output.exists()


def test_native_preparation_wrong_interpreter_rejects_before_any_fixture_output(complete_archive, tmp_path):
    root, index, _ = complete_archive
    output = tmp_path / "must-not-create-fixture"
    before = source_inventory(root)
    completed = subprocess.run([sys.executable, "-I", "-B", str(root / "tests/ci_rc6_browser_prepare.py"),
        "--expected-python", str(tmp_path / "wrong-python"), "--index", str(index),
        "--output", str(output), "--variant", "NORMAL"], capture_output=True, timeout=20)
    assert completed.returncode == 1 and b"PRODUCT_INTERPRETER_MISMATCH" in completed.stdout
    assert not output.exists() and source_inventory(root) == before


@pytest.mark.parametrize("transport_fault", ("exit1", "cleanup_hang"))
def test_preparation_transport_cannot_accept_valid_native_frame_with_failed_or_unreaped_process(
        prepared_normal, complete_archive, tmp_path, monkeypatch, transport_fault):
    """Protocol fault only; no stand-in financial payload/schema is generated."""
    import tests.rc6_browser_prepared as transport
    root, index, _ = complete_archive
    script = tmp_path / "protocol-only.py"
    script.write_text("import sys,time\n"
        "sys.stdout.buffer.write(open(sys.argv[1],'rb').read());sys.stdout.buffer.flush()\n"
        + ("sys.exit(1)\n" if transport_fault == "exit1" else "time.sleep(10)\n"))
    actual, processes = subprocess.Popen, []
    def faulty(_command, **kwargs):
        process = actual([sys.executable, "-I", "-B", str(script), str(prepared_normal["receipt_path"])], **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr(transport.subprocess, "Popen", faulty)
    if transport_fault == "cleanup_hang": monkeypatch.setattr(transport, "PREPARATION_SECONDS", 1.0)
    output = tmp_path / "protocol-only-output"
    with pytest.raises(GateFailure, match="^NATIVE_PREPARATION_(DID_NOT_EXIT_SUCCESSFULLY|DEADLINE)$"):
        prepare_native_fixture(sys.executable, output, source_root=root, index=index, variant="NORMAL")
    launcher = json.loads((tmp_path / "protocol-only-output-transport/launcher.json").read_text())
    assert launcher["status"] == "REJECTED" and launcher["reaped"]
    assert processes[0].poll() is not None and processes[0].stdout.closed and processes[0].stderr.closed
    assert not output.exists()
