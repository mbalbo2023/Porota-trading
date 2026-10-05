"""Native small-family/health contract guards, separate from browser capacity."""
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import subprocess
import sys
from time import sleep
from urllib.parse import parse_qs, urlsplit

from bs4 import BeautifulSoup
import pytest

from tests.rc6_browser_ipc import GateFailure, ProductClient, parse_frame, protected_bytes, source_inventory
from tests.rc6_browser_coverage import observe_health, verified_scope
from tests.test_rc6_browser_product_ipc import complete_archive


@pytest.fixture(scope="module")
def multifamily_product():
    with ProductClient(sys.executable) as product:
        source = product.request("initialize", mode="MULTIFAMILY")
        yield product, source


def test_multifamily_native_catalog_preserves_supported_dlr_and_actual_page_denominators(multifamily_product):
    from bs_instrument_contracts import FAMILIES
    product, source = multifamily_product
    assert set(source["catalog_families"]) == FAMILIES
    assert source["catalog_families"]["FUTUROS"] == 3
    assert all(count == 35 for family, count in source["catalog_families"].items() if family != "FUTUROS")
    for family in sorted(source["catalog_families"]):
        first = product.render("/en-vivo", {"family": family})
        scope = verified_scope(first, family, 0)
        total, selected, counts = scope["total_groups"], scope["selected"], scope["counts"]
        offsets = [0, 10, total-10] if total >= 30 else list(range(0, total, 10))
        assert total >= 30 if family != "FUTUROS" else total >= 3
        seen = set()
        for offset in offsets:
            response = first if offset == 0 else product.render("/en-vivo", {"family": family, "funnel_offset": str(offset)})
            native = verified_scope(response, family, offset)
            assert native["selected"] == selected and native["counts"] == counts and native["total_groups"] == total
            assert response["pointer"] == source["pointer"] and response["source_cut"] == source["source_cut"]
            soup = BeautifulSoup(response["html"], "html.parser")
            links = soup.select("nav[aria-label='Grupos del embudo'] a[href*='cohort=']")
            ids = [parse_qs(urlsplit(link["href"]).query)["cohort"][-1] for link in links]
            assert ids == [group["cohort"] for group in native["groups"]]
            assert not seen.intersection(ids)
            seen.update(ids)
        chosen = native["groups"][-1]
        if family == "FUTUROS":
            assert {group["row"]["symbol"] for group in native["groups"]} == {"DLR/OCT26", "DLR/NOV26", "DLR/DIC26"}
        exact = product.render("/en-vivo", {"family": family, "cohort": chosen["cohort"]})
        exact_scope = verified_scope(exact, family, 0)
        assert exact_scope["total_groups"] == 1 and exact_scope["selected"] == chosen["row"]
        narrowed = product.render("/en-vivo", {"family": family, "cohort": chosen["cohort"],
            "currency": chosen["row"]["currency"], "channel": chosen["row"]["channel"]})
        scoped = verified_scope(narrowed, family, 0)
        assert scoped["selected"] == exact_scope["selected"] and scoped["counts"] == exact_scope["counts"]
        assert scoped["total_groups"] == 1 and narrowed["pointer"] == source["pointer"]
        assert narrowed["source_cut"] == source["source_cut"]


@pytest.mark.parametrize("fault", ("clock", "generation", "family", "offset", "currency", "channel"))
def test_actual_native_family_wire_rejects_clock_cut_identity_or_cursor_drift(multifamily_product, fault):
    product, _ = multifamily_product
    response = deepcopy(product.render("/en-vivo", {"family": "CEDEARS"}))
    if fault == "clock": response["native_funnel_page"]["as_of"] = "2026-10-05T15:59:59+00:00"
    elif fault == "generation": response["native_funnel_page"]["generation_id"] = "0"*32
    elif fault == "family": response["native_funnel_page"]["groups"][0]["row"]["identity"][1] = "FUTUROS"
    elif fault == "offset": response["native_funnel_page"]["groups_offset"] = 10
    elif fault == "currency": response["filters"]["currency"] = "USD"
    else: response["filters"]["channel"] = "UNPUBLISHED_TEST_CHANNEL"
    with pytest.raises(GateFailure):
        verified_scope(response, "CEDEARS", 0)


def test_completed_native_producer_health_is_conservative_without_invented_pid_or_clock(complete_archive):
    root, index, _ = complete_archive
    with ProductClient(sys.executable, root=root, index=index, require_complete_index=True) as product:
        product.request("initialize", mode="NORMAL")
        observed = observe_health(product)
        assert observed["consumer_state"] == "NO_VERIFICADO" and not observed["runtime_alive_claim"]
        assert observed["health_authority"] == "NO_VERIFICADO"
        assert observed["native_details"]["remote_error_class"] == "FileNotFoundError"
        assert observed["request_wall_seconds_including_ipc"] <= 2
    assert product.finish_receipt["source_proof_pass"] and product.finish_receipt["native_custody_unchanged"]


def test_live_health_uses_actual_native_child_canonical_publisher_and_reaps_it(complete_archive, tmp_path, monkeypatch):
    root, index, _ = complete_archive
    outsider = tmp_path / "inherited-capacity-input.json"
    outsider.write_text('{"scope":"MUST_NOT_BE_A_HEALTH_INPUT"}\n')
    before = outsider.stat(), protected_bytes(outsider)
    monkeypatch.setenv("POROTA_CAPACITY_REPORT_PATH", str(outsider))
    # The real input_paths API admits every matching key; this additional
    # binding is a configuration fault, not a fabricated financial payload.
    monkeypatch.setenv("POROTA_CAPACITY_EXTERNAL_PATH", str(outsider))
    with ProductClient(sys.executable, root=root, index=index, require_complete_index=True) as product:
        source = product.request("initialize", mode="HEALTH_LIVE")
        observed = observe_health(product, require_live=True)
        native = observed["native_result"]
        assert native["status"] == "GREEN" and native["provider_capacity_open"] == "NO_VERIFICADO"
        assert native["generation_id"] == source["pointer"]["generation_id"]
        assert datetime.fromisoformat(native["generation_as_of"]) == datetime.fromisoformat(source["source_cut"])
        assert observed["request_wall_seconds_including_ipc"] <= 2
    fixture = product.finish_receipt["offline_live_health_fixture"]
    assert fixture["health_observation"]["before"] == fixture["health_observation"]["after"]
    assert fixture["producer_ready"]["pid"] == fixture["producer_pid"]
    assert fixture["producer_ready"]["environment"]["installed_count"] == 157
    assert set(fixture["producer_ready"]["configured_capacity_paths"]) == {
        "POROTA_CAPACITY_POLICY_PATH", "POROTA_CAPACITY_SHADOW_PATH"}
    assert before == (outsider.stat(), protected_bytes(outsider))
    running = fixture["health_published_running"]["children"]["dynamic_shadow"]
    assert running["pid"] == fixture["producer_pid"] and running["state"] == "RUNNING"
    assert datetime.fromisoformat(fixture["producer_ready"]["as_of"]) >= datetime.fromisoformat(running["started_at"])
    assert fixture["producer_exitcode"] == 0 and fixture["producer_reaped"] and not fixture["producer_cleanup_forced"]
    assert fixture["health_published_stopped"]["children"]["dynamic_shadow"]["state"] == "STOPPED"
    assert not fixture["runtime_live_after_fixture_cleanup"]


def test_health_failure_transport_delay_still_obeys_total_two_seconds(complete_archive, monkeypatch):
    root, index, _ = complete_archive
    with ProductClient(sys.executable, root=root, index=index, require_complete_index=True) as product:
        product.request("initialize", mode="NORMAL")
        original = product._read
        def delayed(deadline):
            actual = original(deadline)
            sleep(2.02)  # Fault in receipt transport; native result/clocks unchanged.
            return actual
        monkeypatch.setattr(product, "_read", delayed)
        with pytest.raises(GateFailure, match="^HEALTH_EXCEEDS_REQUEST_BUDGET$"):
            observe_health(product)
        monkeypatch.setattr(product, "_read", original)


def test_native_health_cleanup_publisher_failure_still_reaps_child_and_restores_environment(complete_archive, tmp_path, monkeypatch):
    """Fault only after a real native tick; no fabricated PID/cut/publisher."""
    import os
    from tests.rc6_browser_ipc import read_source_index
    from tests.rc6_dashboard_native_fixture import LiveHealthFixture
    root, index, _ = complete_archive
    before = dict(os.environ)
    fixture = LiveHealthFixture(tmp_path, root, index, executable=sys.executable,
        source_index=read_source_index(index, root))
    with pytest.raises(RuntimeError, match="^TEST_STOP_PUBLISHER_FAILURE$"):
        with fixture:
            assert fixture.process.poll() is None and fixture.ready["source_proof_pass"]
            def rejected(*_args, **_kwargs):
                raise RuntimeError("TEST_STOP_PUBLISHER_FAILURE")
            monkeypatch.setattr(fixture, "publish", rejected)
    assert fixture.process.poll() == 0 and fixture.closed
    assert fixture.process.stdout.closed and fixture.process.stdin.closed
    assert dict(os.environ) == before


@pytest.mark.parametrize("destination", ("source", "existing", "parent_symlink"))
def test_health_cli_rejected_output_never_writes_failure_into_source_or_alias(complete_archive, tmp_path, destination):
    root, index, _ = complete_archive
    if destination == "source":
        output = root / "rejected-health-output"
    elif destination == "existing":
        output = tmp_path / "existing"
        output.mkdir()
        (output / "keep.json").write_text("keep\n")
    else:
        alias = tmp_path / "source-alias"
        alias.symlink_to(root, target_is_directory=True)
        output = alias / "rejected-health-output"
    before = source_inventory(root)
    files_before = source_inventory(output) if output.exists() else None
    result = subprocess.run([sys.executable, "-I", "-B", str(root / "tests/ci_rc6_browser_health.py"),
        "--product-python", sys.executable, "--product-python-version", f"{sys.version_info.major}.{sys.version_info.minor}",
        "--index", str(index), "--output", str(output)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 2 and "OUTPUT_MUST_BE_NEW_AND_OUTSIDE_SOURCES" in result.stderr
    assert source_inventory(root) == before
    if files_before is None:
        assert not output.exists()
    else:
        assert source_inventory(output) == files_before


def test_health_cli_runs_real_private_shadow_and_reports_joined_custody_proof(complete_archive, tmp_path):
    """Functional157 driver role; final Playwright158/browser remains separate."""
    root, index, _ = complete_archive
    output = tmp_path / "health-native-cli"
    before = source_inventory(root)
    result = subprocess.run([sys.executable, "-I", "-B", str(root / "tests/ci_rc6_browser_health.py"),
        "--product-python", sys.executable, "--product-python-version", f"{sys.version_info.major}.{sys.version_info.minor}",
        "--index", str(index), "--output", str(output)], capture_output=True, text=True, timeout=40)
    assert result.returncode == 0, (result.stdout, result.stderr)
    receipt = json.loads((output / "health-gate.json").read_text())
    assert receipt["status"] == "GREEN" and not receipt["big_render_acceptance_claim"]
    assert receipt["product_environment"]["installed_count"] == 157
    assert receipt["product_proof"]["source_proof_pass"] and receipt["product_proof"]["native_custody_unchanged"]
    assert receipt["tracked_source_hashes_and_modes_unchanged"] and source_inventory(root) == before
    producer = receipt["product_proof"]["offline_live_health_fixture"]
    assert producer["producer_exitcode"] == 0 and producer["producer_reaped"]
    assert not producer["producer_cleanup_forced"] and not receipt["runtime_live_after_fixture_cleanup"]


@pytest.mark.parametrize("control", (b"", b"INVALID_START\n"), ids=("eof", "invalid_start"))
def test_real_health_child_rejects_start_control_before_any_native_tick_or_database(complete_archive, tmp_path, control):
    """Actual157/source child, negative protocol stage; no financial authority."""
    root, index, _ = complete_archive
    database = tmp_path / "must-not-create.db"
    before = source_inventory(root)
    result = subprocess.run([sys.executable, "-I", "-B", str(root / "tests/ci_rc6_browser_product.py"),
        "--expected-python", sys.executable, "--expected-python-version", f"{sys.version_info.major}.{sys.version_info.minor}",
        "--index", str(index), "--require-complete-index", "--health-worker-database", str(database)],
        input=control, capture_output=True, timeout=20)
    response = parse_frame(result.stdout)
    assert result.returncode == 1 and response["ok"] is False
    assert response["error"]["gate"] == "NATIVE_HEALTH_SUPERVISOR_START_REQUIRED"
    assert not database.exists() and source_inventory(root) == before
