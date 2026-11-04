"""A651-R20: exercise the exact in-workflow validators without SSH or providers."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github/workflows/rc6-a626-financial-contract-host-readonly.yml").read_text()
sys.path.insert(0, str(ROOT))

from scripts.rc6_a516_host_budget_metadata_ro import build_receipt

PRODUCT_SHA = "7e1e744d6ca9ab30c51b5747dd4467af5ece0145"
IMAGE_ID = "sha256:4a8f7306188979fe6976f400209151bd529bf0d9f46f068b50bbc54fc96fd70b"
FAMILIES = ("BONOS", "OBLIGACIONES", "OPCIONES", "FUTUROS", "CAUCIONES", "FCI")
FAMILY_FIELDS = (
    "catalog_active", "catalog_prelim_ready", "capability_gaps",
    "gap_ppi_catalog_claimed", "gap_complement_catalog_only",
    "gap_ticker_ambiguous", "gap_incomplete_five_key", "exact_current_v2_ppi",
    "exact_current_v2_complement_only", "ticker_only_v2_mismatch",
    "v2_current_absent", "historic_ppi_exact_without_current",
    "catalog_financial_contract_v17_declared", "catalog_fund_terms_v1_declared",
    "catalog_caucion_terms_v1_declared", "catalog_contract_conflicts_declared",
)


def _block(after: str) -> str:
    assert WORKFLOW.count(after) == 1
    return textwrap.dedent(WORKFLOW.split(after, 1)[1].split("\n          PY", 1)[0])


SQL_VALIDATOR = _block('RC6_A626_REMOTE_RC="$remote_rc" python3 - <<\'PY\'\n')
BUDGET_VALIDATOR = _block('RC6_A516_REMOTE_RC="$remote_rc" python3 - <<\'PY\'\n')
CONSOLIDATED_VALIDATOR = _block(
    "      - name: Build single sanitized SQL plus budget evidence receipt\n"
    "        if: always()\n"
    "        shell: bash\n"
    "        run: |\n"
    "          set -Eeuo pipefail\n"
    "          python3 - <<'PY'\n")


def _run(script: str, tmp_path: Path, **variables):
    env = dict(os.environ)
    env.update({
        "RUNNER_TEMP": str(tmp_path),
        "RC6_EXPECTED_PRODUCT_SHA": PRODUCT_SHA,
        "RC6_EXPECTED_IMAGE_ID": IMAGE_ID,
        "GITHUB_SHA": "193af4a4d3eb01d3eef6e2b7c5bf1f5a6361f292",
        "GITHUB_RUN_ID": "41858619038",
        **variables,
    })
    return subprocess.run([sys.executable, "-B", "-c", script], cwd=ROOT,
                          env=env, text=True, capture_output=True, timeout=10,
                          check=False)


def _sql_report():
    return {
        "schema": "rc6.a626.host-contract-aggregates-ro.v1",
        "runtime_path": "PAPER_V17_CANONICAL_RO",
        "status": "DIAGNOSTIC_ONLY", "private_ro_mount_verified": True,
        "paper_workspace_identity_verified": True, "paper_auto_enabled": False,
        "ppi_identity_authority_verified": False,
        "source_contract_terms_fresh_verified": False,
        "causal_pit_verified": False, "v2_payload_hash_checked": False,
        "host_db_writes": 0,
        "provider_gets": 0, "real_orders_sent_by_probe": 0,
        "host_runtime_order_count": "NO_VERIFICADO", "catalog_rows": 6,
        "v2_current_rows": 2, "v2_current_valid_rows": 2,
        "v2_current_invalid_reason_counts": {},
        "v2_current_integrity_verified": True,
        "v2_history_ppi_rows_scanned": 2,
        "v2_history_identity_bytes_scanned": 64,
        "historical_819_gap_match": False,
        "capability_gaps_measured": 0, "db_bytes": 12345,
        "wal_present": False, "shm_present": False,
        "journal_present": False, "measured_at_utc": "2026-11-04T12:00:00+00:00",
        "families": [dict(family=f, capability_codes={},
                          **{k: 0 for k in FAMILY_FIELDS}) for f in FAMILIES],
    }


def _budget_receipt():
    return build_receipt(
        "ABSENT", {"status": "ABSENT"}, "OTHER_OR_UNSET",
        policy_intent={"policy_file_mode": "OFF", "exec_environment_mode": "UNSET",
                       "auxiliary_requested_mode": "OFF", "policy_path_selector": "CONTAINER_DEFAULT"},
        pid1_launch_intent={"observer_pid1_command": "EXPECTED_BV_RUNTIME",
                            "observer_pid1_environment_mode": "UNSET",
                            "observer_pid1_policy_file_mode": "OFF",
                            "observer_pid1_launch_requested_mode": "OFF",
                            "observer_pid1_policy_selector": "CONTAINER_DEFAULT"},
    )


def _put_sql(tmp_path, receipt, rc="0"):
    (tmp_path / "a626-contract-aggregate.json").write_text(json.dumps(receipt))
    return _run(SQL_VALIDATOR, tmp_path, RC6_A626_REMOTE_RC=rc)


def _put_budget(tmp_path, receipt, rc="0", image=IMAGE_ID):
    raw = "RUNTIME_IMAGE_ID=" + image + "\n" + json.dumps(receipt) + "\n"
    (tmp_path / "a516-budget-stdout.private").write_text(raw)
    return _run(BUDGET_VALIDATOR, tmp_path, RC6_A516_REMOTE_RC=rc)


def test_single_existing_workflow_pins_host_probes_and_never_requests_get():
    assert WORKFLOW.count("  workflow_dispatch:") == 1
    assert "inputs.permit_readonly_host_probe == true" in WORKFLOW
    assert "group: rc6-a626-financial-contract-host-readonly" in WORKFLOW
    assert "group: rc6-unified-paper-deploy" in WORKFLOW
    assert "needs: offhost-focused-ci" in WORKFLOW
    assert WORKFLOW.count("cancel-in-progress: false") == 2
    assert "RC6_EXPECTED_PRODUCT_SHA: " + PRODUCT_SHA in WORKFLOW
    assert "RC6_EXPECTED_IMAGE_ID: " + IMAGE_ID in WORKFLOW
    assert "< scripts/rc6_a626_host_contract_aggregates.py" in WORKFLOW
    assert "< scripts/rc6_a516_host_budget_metadata_ro.py" in WORKFLOW
    assert "timeout 25 unshare --mount --propagation private" in WORKFLOW
    assert "sudo -n timeout 30 docker exec -i" in WORKFLOW
    assert "-e PYTHONDONTWRITEBYTECODE=1" in WORKFLOW
    assert "steps.a626_sql.outcome" not in WORKFLOW.split(
        "- name: Existing A516 v3 budget/PID1 metadata", 1)[1].split(
        "- name: Build single sanitized", 1)[0]
    assert "uses: actions/upload-artifact@v4" in WORKFLOW
    assert "if: always()" in WORKFLOW
    assert "a626-sanitized-only.json" in WORKFLOW
    assert "REDACTED_COUNT" in WORKFLOW
    for forbidden in ("ProductionMarketReader", "budget_from_environment",
                      "PPI_GET_SCOPE", "docker restart", "docker compose up",
                      "GetBook", "GetCurrent", "docker system prune"):
        assert forbidden not in WORKFLOW


def test_sql_full_receipt_remains_successfully_validated(tmp_path):
    p = _put_sql(tmp_path, _sql_report())
    assert p.returncode == 0, p.stdout + p.stderr
    saved = json.loads((tmp_path / "a626-sanitized-only.json").read_text())
    assert saved["status"] == "DIAGNOSTIC_ONLY"
    assert saved["v2_current_integrity_verified"] is True


def test_previous_main_legacy_full_schema_still_collects_without_new_attestation(tmp_path):
    report = _sql_report()
    for key in ("v2_current_integrity_verified", "v2_current_valid_rows",
                "v2_current_invalid_reason_counts"):
        report.pop(key)
    p = _put_sql(tmp_path, report)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "NO_VERIFICADO_LEGACY_FIELDS_ABSENT" in p.stdout
    saved = json.loads((tmp_path / "a626-sanitized-only.json").read_text())
    assert saved["status"] == "DIAGNOSTIC_ONLY"
    assert "v2_current_integrity_verified" not in saved
    assert _put_budget(tmp_path, _budget_receipt()).returncode == 0
    manifest_run = _run(CONSOLIDATED_VALIDATOR, tmp_path)
    assert manifest_run.returncode == 0
    combined = json.loads((tmp_path / "a626-consolidated-sanitized.json").read_text())
    assert combined["a626_current_integrity_evidence"] == "NO_VERIFICADO_LEGACY_FIELDS_ABSENT"
    assert combined["paper_instrument_ready"] is False


def test_sql_partial_preserves_counts_and_explicitly_fails_action(tmp_path):
    receipt = _sql_report()
    receipt.update(status="DIAGNOSTIC_PARTIAL_NO_GO",
                   v2_current_integrity_verified=False,
                   v2_current_valid_rows=1,
                   v2_current_invalid_reason_counts={
                       "HOST_V2_SNAPSHOT_OBSERVED_AT_REVERSED": 1})
    p = _put_sql(tmp_path, receipt)
    assert p.returncode == 4, p.stdout + p.stderr
    saved = json.loads((tmp_path / "a626-sanitized-only.json").read_text())
    assert saved["v2_current_invalid_reason_counts"] == receipt["v2_current_invalid_reason_counts"]
    assert saved["families"][0]["capability_codes"] == {"REDACTED_COUNT": 0}
    assert "DIAGNOSTIC_PARTIAL_NO_GO" in p.stdout


@pytest.mark.parametrize("change", (
    {"v2_current_valid_rows": 3},
    {"v2_current_invalid_reason_counts": {"LEAK_IDENTITY": 1}},
    {"v2_current_valid_rows": True},
    {"provider_gets": False},
    {"host_runtime_order_count": 0},
    {"instrument_ticker": "AL30"},
    {"v2_current_integrity_verified": None},
    {"measured_at_utc": "2026-11-04T12:00:00"},
    {"wal_present": "NO"},
))
def test_sql_rejects_forged_leaky_or_ambiguous_census(tmp_path, change):
    receipt = _sql_report()
    receipt.update(change)
    p = _put_sql(tmp_path, receipt)
    assert p.returncode != 0
    assert not (tmp_path / "a626-sanitized-only.json").exists()


@pytest.mark.parametrize("missing", ("wal_present", "shm_present", "journal_present",
                                      "measured_at_utc", "historical_819_gap_match"))
def test_sql_requires_all_factual_fields_even_when_status_full(tmp_path, missing):
    receipt = _sql_report()
    receipt.pop(missing)
    assert _put_sql(tmp_path, receipt).returncode != 0
    assert not (tmp_path / "a626-sanitized-only.json").exists()


def test_sql_never_publishes_database_derived_capability_keys(tmp_path):
    receipt = _sql_report()
    row = receipt["families"][0]
    row["capability_gaps"] = 1
    row["catalog_active"] = 1
    row["capability_codes"] = {"PRIVATE_ACCOUNT_PASSWORD_KEY": 1}
    receipt["capability_gaps_measured"] = 1
    p = _put_sql(tmp_path, receipt)
    assert p.returncode == 0, p.stdout + p.stderr
    saved = (tmp_path / "a626-sanitized-only.json").read_text()
    assert "PRIVATE_ACCOUNT_PASSWORD_KEY" not in saved
    assert "PRIVATE_ACCOUNT_PASSWORD_KEY" not in p.stdout
    assert json.loads(saved)["families"][0]["capability_codes"] == {
        "REDACTED_COUNT": 1}


@pytest.mark.parametrize("missing", (
    "v2_current_integrity_verified", "v2_current_valid_rows",
    "v2_current_invalid_reason_counts",
))
def test_sql_rejects_mixed_schema_full_or_partial(tmp_path, missing):
    receipt = _sql_report()
    receipt.pop(missing)
    assert _put_sql(tmp_path, receipt).returncode != 0
    receipt = _sql_report()
    receipt.update(status="DIAGNOSTIC_PARTIAL_NO_GO",
                   v2_current_integrity_verified=False,
                   v2_current_valid_rows=1,
                   v2_current_invalid_reason_counts={
                       "HOST_V2_SNAPSHOT_OBSERVED_AT_REVERSED": 1})
    receipt.pop(missing)
    assert _put_sql(tmp_path, receipt).returncode != 0


def test_sql_rejects_legacy_partial_without_quarantine_counters(tmp_path):
    receipt = _sql_report()
    for key in ("v2_current_integrity_verified", "v2_current_valid_rows",
                "v2_current_invalid_reason_counts"):
        receipt.pop(key)
    receipt["status"] = "DIAGNOSTIC_PARTIAL_NO_GO"
    assert _put_sql(tmp_path, receipt).returncode != 0


def test_remote_sql_failure_returns_closed_reason_only(tmp_path):
    p = _put_sql(tmp_path, {"status":"ERR", "reason":"HOST_BAD_TOKEN_ABC\nsecret"}, rc="255")
    assert p.returncode == 3, p.stderr
    saved = json.loads((tmp_path / "a626-sanitized-only.json").read_text())
    assert saved["status"] == "NO_VERIFICADO"
    assert saved["reason"] == "REMOTE_RO_NAMESPACE_OR_READ_UNAVAILABLE"


def test_budget_v3_receipt_is_metadata_only_even_with_pid1_off(tmp_path):
    p = _put_budget(tmp_path, _budget_receipt())
    assert p.returncode == 0, p.stdout + p.stderr
    saved = json.loads((tmp_path / "a516-sanitized-only.json").read_text())
    assert saved["schema"] == "rc6.a516.ppi.budget.metadata-only.v3"
    assert saved["observer_pid1_launch_requested_mode"] == "OFF"
    assert saved["budget_lease_acquired"] is False
    assert saved["provider_calls"] == 0
    assert saved["paper_ready"] is False


@pytest.mark.parametrize("bad", (
    "WRONG_IMAGE", "EXTRA_STDOUT", "FORGED_READY", "HOST_FAILED",
))
def test_budget_rejects_unpinned_forged_or_failed_receipts(tmp_path, bad):
    receipt = _budget_receipt()
    if bad == "FORGED_READY":
        receipt["paper_ready"] = True
    p = _put_budget(tmp_path, receipt, rc="99" if bad == "HOST_FAILED" else "0",
                    image="sha256:unknown" if bad == "WRONG_IMAGE" else IMAGE_ID)
    if bad == "EXTRA_STDOUT":
        output = tmp_path / "a516-budget-stdout.private"
        output.write_text(output.read_text() + "private-content\n")
        p = _run(BUDGET_VALIDATOR, tmp_path, RC6_A516_REMOTE_RC="0")
    assert p.returncode == 2
    saved = json.loads((tmp_path / "a516-sanitized-only.json").read_text())
    assert saved["state"] == "NO_VERIFICADO"
    assert saved["diagnosis"] == "HOST_PROBE_FAILED"
    assert "private-content" not in p.stdout


def test_one_consolidated_artifact_never_promotes_book_or_host_orders(tmp_path):
    assert _put_sql(tmp_path, _sql_report()).returncode == 0
    assert _put_budget(tmp_path, _budget_receipt()).returncode == 0
    p = _run(CONSOLIDATED_VALIDATOR, tmp_path)
    assert p.returncode == 0, p.stdout + p.stderr
    saved = json.loads((tmp_path / "a626-consolidated-sanitized.json").read_text())
    assert saved["data_collection_status"] == "COMPLETE"
    assert saved["ppi_current_book_authority"] == "BLOQUEADO"
    assert saved["ppi_current_book_provider_gets"] == 0
    assert saved["ppi_budget_leases_acquired"] == 0
    assert saved["host_db_writes_by_diagnostics"] == 0
    assert saved["runtime_real_orders_sent"] == "NO_VERIFICADO"
    assert saved["budget_probe_mount_scope"].endswith("NOT_ATTESTED_RO")
    assert saved["paper_instrument_ready"] is False


def test_one_artifact_retains_partial_physical_counts_without_go(tmp_path):
    receipt = _sql_report()
    receipt.update(status="DIAGNOSTIC_PARTIAL_NO_GO",
                   v2_current_integrity_verified=False,
                   v2_current_valid_rows=1,
                   v2_current_invalid_reason_counts={
                       "HOST_V2_SNAPSHOT_OBSERVED_AT_REVERSED": 1})
    assert _put_sql(tmp_path, receipt).returncode == 4
    assert _put_budget(tmp_path, _budget_receipt()).returncode == 0
    p = _run(CONSOLIDATED_VALIDATOR, tmp_path)
    assert p.returncode == 0, p.stdout + p.stderr
    saved = json.loads((tmp_path / "a626-consolidated-sanitized.json").read_text())
    assert saved["data_collection_status"] == "INCOMPLETE_NO_GO"
    assert saved["a626_sql"]["v2_current_invalid_reason_counts"] == {
        "HOST_V2_SNAPSHOT_OBSERVED_AT_REVERSED": 1}
    assert saved["a516_budget_metadata"]["state"] == "EVIDENCE_ONLY_NO_GO"


def test_one_artifact_uses_closed_fallback_when_both_host_probes_unavailable(tmp_path):
    p = _run(CONSOLIDATED_VALIDATOR, tmp_path)
    assert p.returncode == 0, p.stdout + p.stderr
    saved = json.loads((tmp_path / "a626-consolidated-sanitized.json").read_text())
    assert saved["data_collection_status"] == "INCOMPLETE_NO_GO"
    assert saved["a626_sql"]["status"] == "NO_VERIFICADO"
    assert saved["a516_budget_metadata"]["state"] == "NO_VERIFICADO"
    assert saved["ppi_current_book_authority"] == "NO_VERIFICADO"
