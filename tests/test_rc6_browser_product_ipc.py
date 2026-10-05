"""Real locked child handlers and bounded transport failures, without Chromium."""
import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tarfile
from time import sleep

import pytest

from tests.rc6_browser_ipc import (
    GateFailure, MAX_FRAME, PROTOCOL, ProductClient, frame, parse_frame, git_tree_digest,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def complete_archive(tmp_path_factory):
    base = tmp_path_factory.mktemp("native-product-complete-source")
    archive, extracted = base / "source.tar", base / "source"
    extracted.mkdir()
    with archive.open("wb") as output:
        subprocess.run(["git", "archive", "HEAD"], cwd=ROOT, stdout=output, check=True)
    with tarfile.open(archive) as members:
        members.extractall(extracted, filter="data")
    raw_tree = subprocess.check_output(["git", "ls-tree", "-rz", "HEAD"], cwd=ROOT)
    modes, blobs = {}, {}
    for member in raw_tree.split(b"\0"):
        if member:
            identity, name = member.split(b"\t", 1)
            mode, kind, blob = identity.decode().split()
            assert kind == "blob"
            modes[name.decode()], blobs[name.decode()] = mode, blob
    index = base / "source.index.json"
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    tree = subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=ROOT, text=True).strip()
    raw_commit = subprocess.check_output(["git", "cat-file", "commit", "HEAD"], cwd=ROOT)
    index.write_text(json.dumps({"schema": "rc6.complete-archive-source-pin.v1", "source_sha": sha,
        "source_tree": tree, "tar_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(), "overlay_count": 0,
        "raw_git_commit_sha256": hashlib.sha256(raw_commit).hexdigest(), "modes": modes, "blob_ids": blobs,
        "files": {str(path.relative_to(extracted)): hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in extracted.rglob("*") if path.is_file()}}))
    return extracted, index, tree


@pytest.fixture(scope="module")
def native_product():
    # The actual PAPER caller/publisher fixture runs only in the locked child.
    # The parent uses only stdlib transport and never starts Playwright.
    with ProductClient(sys.executable, diagnostic=True) as product:
        initial = product.request("initialize", mode="NORMAL")
        yield product, initial


def test_product_environment_is_exact_lock_and_declared_interpreter_before_native_handlers(native_product):
    product, initial = native_product
    environment = product.product_environment
    assert environment["role"] == "PRODUCT" and environment["installed_count"] == environment["locked_count"] == 157
    assert environment["sys_executable"] == sys.executable
    assert not environment["missing"] and not environment["extra"] and not environment["wrong_versions"]
    assert len(initial["canonical_paths"]) == 49 and len(initial["legacy"]) == 22
    assert initial["native_generation_roles"] == ["checkpoint", "projection", "report", "status"]


def test_stdio_native_handler_runs_each_request_and_preserves_cut_filters_and_body(native_product):
    product, initial = native_product
    responses = [product.render("/en-vivo/oportunidades", {"currency": "ARS"}) for _ in range(2)]
    assert responses[0]["html"] == responses[1]["html"]
    for response in responses:
        assert response["pointer"] == initial["pointer"] and response["source_cut"] == initial["source_cut"]
        assert response["filters"] == {"currency": "ARS"}
        assert response["html_bytes"] == len(response["html"].encode())
        assert response["html_bytes"] < response["response_json_bytes"] < MAX_FRAME
        assert response["request_wall_seconds_including_ipc_html_json"] >= response["native_elapsed_seconds"]
        assert "WIRE_AND_PROJECTION_SEMANTICS" in response["html"]
        assert response["native_verified_queries"] == 1
        assert set(response["native_role_payload_digests"]) == {"report", "checkpoint", "status", "projection"}
    selected = product.render("/en-vivo/oportunidades", {"currency": "ARS", "q": "T024"})
    assert selected["filters"]["q"] == "T024" and "T024" in selected["html"]
    assert selected["pointer"] == initial["pointer"] and selected["html"] != responses[0]["html"]


def test_child_real_active_future_and_durable_intent_are_visible_without_driver_native_import(native_product):
    product, _ = native_product
    response = product.render("/en-vivo/posiciones", {"family": "FUTUROS"})
    assert "DLR/OCT26" in response["html"] and "WATCH_NO_QUOTE" in response["html"]
    assert response["filters"] == {"family": "FUTUROS"}
    assert not product.driver_network and not product.driver_sqlite


def test_native_health_missing_evidence_stays_rejected_without_fabricated_health(native_product):
    product, _ = native_product
    with pytest.raises(GateFailure, match="^PRODUCT_HEALTH_SOURCE_INDEX_REQUIRED$"):
        product.health()


def test_driver_module_import_does_not_import_product_or_playwright():
    program = """
import sys,json
sys.path.insert(0,sys.argv[1])
import tests.ci_rc6_projection_large_browser
import tests.ci_trader_terminal_browser
import tests.ci_rc6_projection_browser_diagnostic
for name in sys.modules:
 assert not name.startswith(('rc6_trader_dashboard','rc6_shadow_runtime','be_paper_engine','rc6_paper_family_lifecycle','playwright'))
print(json.dumps({'driver_imported_product':False,'playwright_started':False}))
"""
    completed = subprocess.run([sys.executable, "-I", "-B", "-c", program, str(ROOT)], capture_output=True, text=True, timeout=10)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {"driver_imported_product": False, "playwright_started": False}


def test_wrong_interpreter_flag_rejects_before_fixture_or_native_import(tmp_path):
    command = [sys.executable, "-I", "-B", str(ROOT / "tests/ci_rc6_browser_product.py"),
               "--expected-python", str(tmp_path / "wrong-interpreter")]
    completed = subprocess.run(command, capture_output=True, timeout=10)
    assert completed.returncode == 1
    response = parse_frame(completed.stdout)
    assert response["ok"] is False and response["id"] == 0
    assert response["error"]["gate"] == "PRODUCT_INTERPRETER_MISMATCH"
    assert not list(tmp_path.iterdir())


def test_protocol_wrong_response_id_rejects_native_response_entirely(monkeypatch):
    with ProductClient(sys.executable) as product:
        original_read = product._read
        def wrong_id(deadline):
            response = original_read(deadline)
            response["id"] += 1  # Wire fault, not a financial payload fixture.
            return response
        monkeypatch.setattr(product, "_read", wrong_id)
        with pytest.raises(GateFailure, match="^PRODUCT_IPC_RESPONSE_ID$"):
            product.request("health")
        monkeypatch.setattr(product, "_read", original_read)


def test_process_death_never_returns_a_previous_body_and_joins_all_resources():
    product = ProductClient(sys.executable)
    with pytest.raises(GateFailure, match="^PRODUCT_IPC_PROCESS_DIED$|^PRODUCT_IPC_PREMATURE_EOF$"):
        with product:
            product.process.kill()
            product.process.wait(timeout=5)
            product.render("/instrumentos", {})
    assert product.process.poll() is not None and not product.stderr_thread.is_alive()
    assert product.pending == bytearray() and product.finish_receipt is None


def test_ipc_delay_rejects_real_native_body_against_total_original_one_second(native_product, monkeypatch):
    product, _ = native_product
    original_read = product._read
    def delayed(deadline):
        result = original_read(deadline)
        # The actual native return and clocks are unchanged. Delay only this
        # receipt over stdio, with a real bounded pause and no falsified clock.
        sleep(1.02)
        return result
    monkeypatch.setattr(product, "_read", delayed)
    with pytest.raises(GateFailure, match="^RENDER_EXCEEDS_REQUEST_BUDGET$") as rejected:
        product.render("/instrumentos", {})
    assert rejected.value.details["elapsed_seconds"] > 1
    assert "html" not in rejected.value.details


@pytest.mark.parametrize("raw", (
    b'{"protocol":"rc6.browser-product-ipc.v1","id":1,"id":2}\n',
    b'{"protocol":"rc6.browser-product-ipc.v1","id":true}\n',
    b'{"protocol":"rc6.browser-product-ipc.v1","id":1,"value":NaN}\n',
    b'{"protocol":"wrong","id":1}\n',
    b'{}',
    b'X' * MAX_FRAME,
))
def test_transport_contract_rejects_invalid_frames_without_truncation(raw):
    with pytest.raises(GateFailure):
        parse_frame(raw)


def test_response_budget_counts_actual_utf8_json_bytes_without_truncation():
    value = {"protocol": PROTOCOL, "id": 1, "text": "á" * (MAX_FRAME // 2)}
    with pytest.raises(GateFailure, match="^PRODUCT_IPC_FRAME_BUDGET_OR_FRAMING$"):
        frame(value)


def test_whole_raw_source_pin_binds_actual_git_blobs_modes_and_tree_before_native_imports(complete_archive):
    extracted, index, tree = complete_archive
    with ProductClient(sys.executable, root=extracted, index=index) as product:
        assert git_tree_digest(product.source_before) == tree
        assert product.source_index["candidate_tree_sha"] == tree
        assert product.product_environment["installed_count"] == 157
    assert product.finish_receipt["source_proof_pass"]
    assert product.finish_receipt["source_pin_complete"]
    assert product.finish_receipt["custody_verification_scope"] == "NOT_EXERCISED"
    assert product.finish_receipt["native_custody_unchanged"] is False


def test_source_mode_mutation_rejects_whole_proof_even_when_bytes_are_unchanged(complete_archive):
    extracted, index, _ = complete_archive
    target = extracted / ".env.example"
    before, original_mode = hashlib.sha256(target.read_bytes()).hexdigest(), target.stat().st_mode & 0o777
    product = ProductClient(sys.executable, root=extracted, index=index)
    try:
        with pytest.raises(GateFailure, match="^WHOLE_SOURCE_HASHES_OR_MODES_CHANGED$"):
            with product:
                target.chmod(0o600)
        assert hashlib.sha256(target.read_bytes()).hexdigest() == before
        assert product.finish_receipt["source_proof_pass"] is False
        assert product.process.poll() is not None and not product.stderr_thread.is_alive()
    finally:
        target.chmod(original_mode)


def test_native_diagnostic_finish_proves_fresh_four_role_reads_and_source_modes(tmp_path):
    with ProductClient(sys.executable, diagnostic=True) as product:
        product.request("initialize", mode="NORMAL")
        for _ in range(2):
            product.render("/en-vivo/oportunidades", {"currency": "ARS"})
    proof = product.finish_receipt
    assert proof["source_proof_pass"] and proof["native_custody_unchanged"]
    assert proof["tracked_source_hashes_and_modes_unchanged"]
    assert not proof["network_attempts"] and not proof["source_sqlite_attempts"]
    assert all(row["matches_archived_blob"] for row in proof["imported_product_modules"])
    assert not proof["unexpected_product_imports"]
    members = [row for row in proof["stage_aggregates"] if row["label"] == "member_bytes" and row["render"] is not None]
    for render in (0, 1):
        selected = [row for row in members if row["render"] == render]
        assert {row["metadata_last_declared"]["role"] for row in selected} == {"report", "checkpoint", "status", "projection"}
        assert all(row["calls"] == row["completed_calls"] == 1 for row in selected)
    assert product.process.returncode == 0 and not product.stderr_thread.is_alive()
