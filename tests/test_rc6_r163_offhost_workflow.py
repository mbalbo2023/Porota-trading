"""R163 HITO D: enforce isolation and non-authoritative offhost proof wiring.

These tests analyze workflow declarations; they are not Docker/Actions proof.
"""

import ast
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/rc6-r163-offhost-guard79-proof.yml"


def _workflow():
    return yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def _runs(job):
    return "\n".join(str(step.get("run", "")) for step in job.get("steps", []))


def _assert_no_foreign_or_global_commands(shell: str) -> None:
    """Static negative guard; runtime CID receipts are checked by the job."""
    for command in (r"\bsudo\b", r"\bssh\s", r"\bdocker\s+(?:system|image|volume|container)\s+(?:prune|rm)\b",
                    r"\bdocker\s+rm\s+-f\b", r"\bdocker\s+cp\b"):
        assert re.search(command, shell, flags=re.I) is None, command
    for match in re.finditer(r"\bdocker\s+rm\b[^\n]*", shell):
        assert match.group() == 'docker rm -v "$cid" >/dev/null; then'


_NATIVE_XMLS = (
    "native-bound.xml",
    "session/dashboard_auth_52.xml",
    "session/publication_io_identity.xml",
    "session/scalping_capacity.xml",
    "session/scheduler_monitor_backup.xml",
    "session/signals_risk_exit_ledger.xml",
    "session/sources_readiness_network.xml",
    "session/sql_shadow_wal.xml",
    "session/treasury_caucion.xml",
)


def _junit_guard_source():
    body = _runs(_workflow()["jobs"]["offhost-native-proof"])
    pattern = r"(?ms)^docker exec -i --user 1000:1000 \"\$source_cid\" python - /app/data <<'PY'\n(.*?)^PY$"
    match = re.search(pattern, body)
    assert match is not None, "Missing UID1000 pre-stop native JUnit guard"
    ast.parse(match.group(1))
    assert body.index(match.group(0)) < body.index('docker stop --time 10 "$source_cid"')
    return match.group(1)


def _make_junit_proofs(root: Path):
    for relative in _NATIVE_XMLS:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        testsuites = ET.Element("testsuites")
        suite = ET.SubElement(testsuites, "testsuite", {
            "tests": "1", "failures": "0", "errors": "0", "skipped": "0"})
        ET.SubElement(suite, "testcase", {"name": "synthetic"})
        ET.ElementTree(testsuites).write(target, encoding="utf-8", xml_declaration=True)


def _run_junit_guard(root: Path):
    return subprocess.run([sys.executable, "-", str(root)],
                          input=_junit_guard_source(), text=True,
                          capture_output=True, check=False)


def test_native_junit_guard_accepts_nine_nonempty_zero_skip_suites(tmp_path):
    _make_junit_proofs(tmp_path)
    result = _run_junit_guard(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "RC6_R163_JUNIT=OFFHOST_NINE_SUITES_VERIFIED" in result.stdout


@pytest.mark.parametrize("case", (
    "all_skipped", "xfail", "zero_cases", "counter_mismatch", "noninteger_counter",
    "missing_counter", "failure_node", "error_node", "missing_xml", "malformed_xml",
))
def test_native_junit_guard_rejects_adversarial_false_pass_receipts(tmp_path, case):
    _make_junit_proofs(tmp_path)
    target = tmp_path / _NATIVE_XMLS[0]
    if case == "missing_xml":
        target.unlink()
    elif case == "malformed_xml":
        target.write_text("<testsuite", encoding="utf-8")
    else:
        root = ET.parse(target).getroot()
        suite = root.find("testsuite")
        assert suite is not None
        testcase = suite.find("testcase")
        assert testcase is not None
        if case in ("all_skipped", "xfail"):
            suite.set("skipped", "1")
            ET.SubElement(testcase, "skipped", {"type": "pytest.xfail" if case == "xfail" else ""})
        elif case == "zero_cases":
            suite.set("tests", "0")
            suite.remove(testcase)
        elif case == "counter_mismatch":
            suite.set("tests", "2")
        elif case == "noninteger_counter":
            suite.set("tests", "1.0")
        elif case == "missing_counter":
            del suite.attrib["errors"]
        elif case == "failure_node":
            ET.SubElement(testcase, "failure")
        elif case == "error_node":
            ET.SubElement(testcase, "error")
        ET.ElementTree(root).write(target, encoding="utf-8")
    result = _run_junit_guard(tmp_path)
    assert result.returncode != 0, case


def test_all_bash_steps_have_valid_syntax_not_just_matching_policy_text():
    scripts = [(job_name, step["name"], step["run"])
               for job_name, job in _workflow()["jobs"].items()
               for step in job["steps"] if step.get("shell") == "bash" and "run" in step]
    assert len(scripts) >= 7
    for job_name, step_name, content in scripts:
        result = subprocess.run(["bash", "-n"], input=content,
                                text=True, capture_output=True, check=False)
        assert result.returncode == 0, (job_name, step_name, result.stderr)


def test_all_inline_python_heredocs_parse_and_have_no_accidental_interpolation():
    blocks = []
    for job_name, job in _workflow()["jobs"].items():
        for step in job["steps"]:
            if "run" not in step:
                continue
            content = step["run"]
            for match in re.finditer(r"(?ms)^python - <<'PY'\n(.*?)^PY$", content):
                blocks.append((job_name, step["name"], match.group(1)))
    assert len(blocks) >= 3
    for job_name, step_name, block in blocks:
        ast.parse(block, filename=f"{job_name}/{step_name}")
        assert "${{" not in block, (job_name, step_name)


@pytest.mark.parametrize("forbidden", (
    "sudo true", "ssh operator@remote", "docker rm -f orphan", "docker rm named-container",
    "docker volume rm candidate-volume", "docker container prune", "docker system prune",
))
def test_adversarial_global_or_named_cleanup_is_rejected_by_guard(forbidden):
    native = _runs(_workflow()["jobs"]["offhost-native-proof"])
    _assert_no_foreign_or_global_commands(native)
    with pytest.raises(AssertionError):
        _assert_no_foreign_or_global_commands(native + "\n" + forbidden)


@pytest.mark.parametrize("malformed_trigger", ("workflow_dispatch", "pull_request_target", "push"))
def test_unexpected_trigger_would_break_exact_pr_eligibility(malformed_trigger):
    trigger = _workflow()["on"]
    assert list(trigger) == ["pull_request"]
    assert malformed_trigger not in trigger


def test_probe_only_pr_trigger_is_independent_and_draft_eligible():
    wf = _workflow()
    assert wf["on"]["pull_request"]["branches"] == ["deploy/rc6-pr69-isolated-20260915"]
    assert list(wf["on"]) == ["pull_request"]
    assert "push" not in wf["on"] and "pull_request_target" not in wf["on"]
    assert wf["permissions"] == {"contents": "read"}
    assert wf["concurrency"]["cancel-in-progress"] == "false"
    assert "draft" not in wf["jobs"]["offhost-native-proof"].get("if", "").lower()
    assert "1087" in wf["jobs"]["offhost-native-proof"]["if"]
    assert "github.repository" in wf["jobs"]["offhost-native-proof"]["if"]
    assert set(wf["jobs"]) == {"python311-lock-closure", "offhost-native-proof", "offhost-independent-bytes"}


def test_no_product_host_permission_or_release_unlock_is_possible():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "R144" in text and "HOLD79" in text
    assert "PROBE_ONLY" in text and "automatic_go=false" in text
    for forbidden in ("sudo", "ssh ", "ssh@", "appleboy", "droplet", "docker system prune",
                      "docker image prune", "docker volume prune", "docker container prune",
                      "docker compose", "docker rm -f", "docker cp", "docker inspect --all"):
        assert forbidden.lower() not in _runs(_workflow()["jobs"]["offhost-native-proof"]).lower()
    assert "rc6-unified-paper-deploy" not in text
    assert "environment: production" not in text
    assert "actions/upload-artifact@v4" in text
    assert "github.event.pull_request.head.sha" in text


def test_exact_cid_owning_and_anonymous_rw_to_ro_volume():
    wf = _workflow()
    source = _runs(wf["jobs"]["offhost-native-proof"])
    assert "--mount type=volume,target=/app/data" in source
    assert "--volumes-from" in source and ":ro" in source
    assert "--read-only" in source and "--network none" in source
    assert "--user 1000:1000" in source and "--cap-drop ALL" in source
    assert "--security-opt no-new-privileges" in source
    assert "docker create" in source and "docker exec" in source
    assert "docker inspect" in source
    assert "docker rm" in source and "--volumes" in source
    assert "rc6_r163_native_volume_proof.py inspect" in source
    assert "rc6_r163_native_volume_proof.py attest" in source
    assert "rc6_r163_native_volume_proof.py verify" in source
    assert 'docker rm -v "$cid"' in source
    assert "docker volume rm" not in source
    _assert_no_foreign_or_global_commands(source)


def test_native_evidence_runs_all_five_real_stages_and_causal_replay():
    source = _runs(_workflow()["jobs"]["offhost-native-proof"])
    for needed in ("tests/test_rc6_shadow_native_bound.py",
                   "--full-catalog-check", "--output /app/data/catalog",
                   "--output /app/data/session", "--cadence-stage fresh",
                   "--cadence-stage legacy", "--junitxml=/app/data/native-bound.xml"):
        assert needed in source
    assert "RC6_NATIVE_EVIDENCE_PATHS" in source
    lock = _runs(_workflow()["jobs"]["python311-lock-closure"])
    assert "requirements.lock.txt" in lock and "pip check" in lock
    for test_name in (
        "test_rc6_a586_ppi_missing_lease_guard.py",
        "test_rc6_a667_static_quota_evidence_material.py",
        "test_rc6_a677_ppi_watch_upper_bound_v2.py",
        "test_rc6_capacity_promotion.py",
        "test_rc6_final_programming_runtime.py",
        "test_rc6_approved_factual_callers.py",
    ):
        assert test_name in lock
    assert "FAILED_HOST_TRANSITION_REPLAY" in lock
    assert "tests/test_rc6_deploy_quiescence.py" in lock
    assert _workflow()["jobs"]["python311-lock-closure"]["runs-on"] == "ubuntu-24.04"
    assert "python-version" in str(_workflow()["jobs"]["python311-lock-closure"])


def test_independent_runner_downloads_raw_rest_bytes_with_receipts_and_never_go():
    wf = _workflow()
    producer = wf["jobs"]["offhost-native-proof"]
    independent = wf["jobs"]["offhost-independent-bytes"]
    assert independent["needs"] == ["offhost-native-proof"]
    assert independent["runs-on"] == "ubuntu-24.04"
    assert independent["permissions"] == {"actions": "read", "contents": "read"}
    producer_runs = _runs(producer)
    runs = _runs(independent)
    assert "porota-artifact-byte-manifest-v1.json" in producer_runs
    assert "native-manifest.json" in producer_runs
    assert "actions/download-artifact@" not in str(independent)
    assert "downloadArtifact" in str(independent) or "actions/artifacts" in str(independent)
    # The final verifier intentionally requires a *completed* successful run;
    # this same-run job cannot truthfully satisfy that prerequisite.
    assert "rc6_r163_artifact_byte_verifier.py" not in runs
    assert "run[\"status\"] != \"completed\"" in runs
    assert "RC6_R163_POSTRUN=NO_VERIFICADO" in runs
    assert 'run["path"] == workflow_path or run["path"].startswith(workflow_path + "@")' in runs
    assert "proof.verify_volume_chain(" in runs
    assert "proof.verify_collector_manifest(" in runs
    assert "POSTRUN_TRUSTED_DEFAULT_WORKFLOW" in WORKFLOW.read_text()
    assert "RC6_ARTIFACT_ID" in runs and "GITHUB_RUN_ID" in runs
    assert "rc6-r163-rest-raw.zip" in str(independent)
    assert "automatic_go=false" in runs
    assert "PROBE_ONLY" in runs
    assert "R144" in WORKFLOW.read_text()
    assert "RC6_UNIFIED_PAPER_DEPLOY=GREEN" not in WORKFLOW.read_text()
