"""R163 HITO D: enforce isolation and non-authoritative offhost proof wiring.

These tests analyze workflow declarations; they are not Docker/Actions proof.
"""

import ast
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys
from types import SimpleNamespace
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


_RUNNER_JOBS = (
    "python311-lock-closure",
    "offhost-native-proof",
    "offhost-independent-bytes",
)


def _runner_guard(job_name: str) -> dict:
    steps = _workflow()["jobs"][job_name]["steps"]
    guard = steps[0]  # First, before checkout, image build, container or ZIP.
    assert guard["name"] == "Reject non-GitHub-hosted runner (HOLD79)"
    assert guard["shell"] == "bash"
    assert guard.get("env") == {"RC6_RUNNER_ENVIRONMENT": "${{ runner.environment }}"}
    assert "continue-on-error" not in guard and "if" not in guard
    assert "uses" not in guard
    assert "exit 79" in guard["run"]
    return guard


@pytest.mark.parametrize("job_name", _RUNNER_JOBS)
def test_every_runner_job_fails_closed_before_any_work_if_not_github_hosted(job_name):
    job = _workflow()["jobs"][job_name]
    assert job["runs-on"] == "ubuntu-24.04"
    script = _runner_guard(job_name)["run"]
    assert "RC6_R163_RUNNER=GITHUB_HOSTED" in script
    assert "docker " not in script and "curl " not in script
    # After the guard, actions may build/inspect Docker or download the ZIP;
    # an `if:`-skipped check would not be an enforcement step.
    assert len(job["steps"]) > 1
    assert "checkout" in str(job["steps"][1]).lower()


@pytest.mark.parametrize("environment,expected", (
    ("github-hosted", 0),
    ("self-hosted", 79),
    ("", 79),
    ("github-hosted ", 79),
    ("GITHUB-HOSTED", 79),
    ("github-hosted\nself-hosted", 79),
))
def test_executable_runner_identity_guard_rejects_untrusted_context(environment, expected):
    script = _runner_guard("offhost-native-proof")["run"]
    env = os.environ.copy()
    env["RC6_RUNNER_ENVIRONMENT"] = environment
    result = subprocess.run(["bash", "-c", script], env=env,
                            text=True, capture_output=True, check=False)
    assert result.returncode == expected
    if expected:
        assert result.stdout == ""
        assert result.stderr.strip() == "RC6_R163_RUNNER=HOLD79|untrusted_runner_environment"
    else:
        assert result.stdout.strip() == "RC6_R163_RUNNER=GITHUB_HOSTED|context_verified_only"


def test_runner_guard_retains_read_only_permissions_and_no_host_write_exceptions():
    wf = _workflow()
    assert wf["permissions"] == {"contents": "read"}
    for job_name in _RUNNER_JOBS:
        step = _runner_guard(job_name)
        assert step["env"] == {"RC6_RUNNER_ENVIRONMENT": "${{ runner.environment }}"}
        expected = ({"actions": "read", "contents": "read"}
                    if job_name == "offhost-independent-bytes"
                    else {"contents": "read"})
        assert wf["jobs"][job_name]["permissions"] == expected
        assert "sudo" not in step["run"] and "docker" not in step["run"]


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


def _raw_rest_digest_contract_source():
    job = _workflow()["jobs"]["offhost-independent-bytes"]
    body = _runs(job)
    match = re.search(r"(?ms)^# RC6_R163_DIGEST_CONTRACT_BEGIN\n(.*?)^# RC6_R163_DIGEST_CONTRACT_END$", body)
    assert match is not None, "Missing executable raw REST vs Action upload digest contract"
    ast.parse(match.group(1))
    return match.group(1)


def _run_raw_rest_digest_contract(archive: bytes, action_output: str, rest_digest: str):
    script = _raw_rest_digest_contract_source()
    scope = {"hashlib": hashlib, "re": re, "archive": archive,
             "artifact": {"digest": rest_digest},
             "os": SimpleNamespace(environ={"RC6_ARTIFACT_DIGEST": action_output})}
    exec(compile(script, "R163_REST_DIGEST_CONTRACT", "exec"), scope)


def test_action_upload_v4_bare_hex_and_rest_prefixed_digest_bind_same_raw_zip():
    archive = b"exact raw REST ZIP bytes (synthetic test fixture)"
    sha = hashlib.sha256(archive).hexdigest()
    _run_raw_rest_digest_contract(archive, sha, "sha256:" + sha)


@pytest.mark.parametrize("case", (
    "action_prefixed", "action_wrong_hash", "action_uppercase", "action_blank",
    "rest_bare", "rest_wrong_hash", "rest_double_prefix",
))
def test_raw_zip_digest_contract_rejects_wrong_prefix_or_bytes(case):
    archive = b"exact raw REST ZIP bytes (synthetic test fixture)"
    sha = hashlib.sha256(archive).hexdigest()
    action, rest = sha, "sha256:" + sha
    if case == "action_prefixed":
        action = "sha256:" + sha
    elif case == "action_wrong_hash":
        action = "0" * 64
    elif case == "action_uppercase":
        action = sha.upper()
    elif case == "action_blank":
        action = ""
    elif case == "rest_bare":
        rest = sha
    elif case == "rest_wrong_hash":
        rest = "sha256:" + "0" * 64
    elif case == "rest_double_prefix":
        rest = "sha256:sha256:" + sha
    with pytest.raises(AssertionError):
        _run_raw_rest_digest_contract(archive, action, rest)


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


@pytest.mark.parametrize("relative,overrides,direct_cases", (
    ("native-bound.xml", {"tests": "2"}, 1),
    ("session/treasury_caucion.xml", {"skipped": "1"}, 1),
    ("session/sql_shadow_wal.xml", {"errors": "1"}, 1),
    ("session/dashboard_auth_52.xml", {"failures": "1"}, 1),
    ("session/publication_io_identity.xml", {"tests": "0"}, 0),
    ("session/scalping_capacity.xml", {"tests": "1"}, 2),
    ("session/scheduler_monitor_backup.xml", {"tests": "1000000000000"}, 1),
))
def test_native_junit_count_hold_emits_bounded_path_and_exact_small_counts(
        tmp_path, relative, overrides, direct_cases):
    """A real HOLD79 must identify its offending JUnit without leaking XML content."""
    _make_junit_proofs(tmp_path)
    target = tmp_path / relative
    xml = ET.parse(target).getroot()
    suite = xml.find("testsuite")
    assert suite is not None
    suite.attrib.update(overrides)
    for testcase in list(suite.findall("testcase")):
        suite.remove(testcase)
    for index in range(direct_cases):
        ET.SubElement(suite, "testcase", {"name": f"synthetic-{index}"})
    ET.ElementTree(xml).write(target, encoding="utf-8")

    result = _run_junit_guard(tmp_path)
    assert result.returncode == 79
    expected_counts = {"tests": "1", "failures": "0", "errors": "0", "skipped": "0"}
    expected_counts.update(overrides)
    if len(expected_counts["tests"]) > 9:
        expected_counts["tests"] = ">999999999"
    detail = (f"RC6_R163_JUNIT_DETAIL=HOLD79|relative_path={relative}|suite_index=0"
              f"|tests={expected_counts['tests']}|testcases_direct={direct_cases}"
              f"|testcases_descendant={direct_cases}"
              f"|failures={expected_counts['failures']}|errors={expected_counts['errors']}"
              f"|skipped={expected_counts['skipped']}")
    assert result.stdout == ""
    assert result.stderr.splitlines() == [
        detail,
        "RC6_R163_JUNIT=HOLD79|JUNIT_COUNTS_NOT_ZERO_SKIP",
    ]


def test_native_junit_count_hold_reports_offending_suite_index(tmp_path):
    _make_junit_proofs(tmp_path)
    relative = "session/treasury_caucion.xml"
    target = tmp_path / relative
    xml = ET.parse(target).getroot()
    invalid = ET.SubElement(xml, "testsuite", {
        "tests": "2", "failures": "0", "errors": "0", "skipped": "0",
    })
    ET.SubElement(invalid, "testcase", {"name": "synthetic"})
    ET.ElementTree(xml).write(target, encoding="utf-8")

    result = _run_junit_guard(tmp_path)
    assert result.returncode == 79
    assert result.stdout == ""
    assert result.stderr.splitlines() == [
        "RC6_R163_JUNIT_DETAIL=HOLD79|relative_path=session/treasury_caucion.xml"
        "|suite_index=1|tests=2|testcases_direct=1|testcases_descendant=1"
        "|failures=0|errors=0|skipped=0",
        "RC6_R163_JUNIT=HOLD79|JUNIT_COUNTS_NOT_ZERO_SKIP",
    ]


def test_native_junit_count_hold_distinguishes_nested_from_direct_cases(tmp_path):
    _make_junit_proofs(tmp_path)
    target = tmp_path / "session/sources_readiness_network.xml"
    xml = ET.parse(target).getroot()
    suite = xml.find("testsuite")
    assert suite is not None
    testcase = suite.find("testcase")
    assert testcase is not None
    suite.remove(testcase)
    wrapper = ET.SubElement(suite, "properties")
    wrapper.append(testcase)
    ET.ElementTree(xml).write(target, encoding="utf-8")

    result = _run_junit_guard(tmp_path)
    assert result.returncode == 79
    assert result.stderr.splitlines() == [
        "RC6_R163_JUNIT_DETAIL=HOLD79|relative_path=session/sources_readiness_network.xml"
        "|suite_index=0|tests=1|testcases_direct=0|testcases_descendant=1"
        "|failures=0|errors=0|skipped=0",
        "RC6_R163_JUNIT=HOLD79|JUNIT_COUNTS_NOT_ZERO_SKIP",
    ]


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
    assert "draft" not in wf["jobs"]["offhost-native-proof"].get("if", "").lower()
    assert "1087" in wf["jobs"]["offhost-native-proof"]["if"]
    assert "github.repository" in wf["jobs"]["offhost-native-proof"]["if"]
    assert set(wf["jobs"]) == {"python311-lock-closure", "offhost-native-proof", "offhost-independent-bytes"}


def test_ready_transition_after_release_does_not_restart_offhost_probe_builds():
    wf = _workflow()
    # GitHub pull_request activity types are an allowlist: the READY transition
    # happens after exact-SHA B/C and D/E, so A must not trigger another A/B.
    assert wf["on"]["pull_request"]["types"] == [
        "opened", "synchronize", "reopened",
    ]
    # A still reaches native/HOLD79 on DRAFT commits to seed trusted B/C.
    assert "draft" not in wf["jobs"]["python311-lock-closure"].get("if", "").lower()
    assert "draft" not in wf["jobs"]["offhost-native-proof"].get("if", "").lower()


def test_new_pr_commit_cancels_superseded_probe_without_skipping_draft_native():
    wf = _workflow()
    # Two synchronize events with distinct head SHAs must use the same PR lock.
    # The canonical release/deploy mutex is unrelated and must not be changed.
    assert wf["concurrency"] == {
        "group": "rc6-r163-offhost-${{ github.event.pull_request.number }}",
        "cancel-in-progress": "${{ github.event.action == 'synchronize' }}",
    }
    assert wf["on"]["pull_request"]["types"] == [
        "opened", "synchronize", "reopened",
    ]
    native = wf["jobs"]["offhost-native-proof"]
    assert native["needs"] == ["python311-lock-closure"]
    assert "needs.python311-lock-closure.result == 'success'" in native["if"]
    # This native path must still reach the intentional privacy HOLD79 on DRAFT:
    # trusted-main R206 B/C currently depend on that A failure for release proof.
    assert "draft" not in native["if"].lower()
    assert "ready_for_review" not in native["if"]
    independent = wf["jobs"]["offhost-independent-bytes"]
    assert independent["needs"] == ["offhost-native-proof"]
    assert "needs.offhost-native-proof.result == 'success'" in independent["if"]


@pytest.mark.parametrize("action,should_cancel", (
    ("synchronize", True),
    ("ready_for_review", False),
    ("reopened", False),
    ("opened", False),
))
def test_same_sha_pr_state_changes_do_not_cancel_running_native(action, should_cancel):
    # GitHub Actions accepts an expression here. Evaluate this restricted
    # one-comparison gate against the actual pull_request event actions.
    expression = _workflow()["concurrency"]["cancel-in-progress"]
    predicate = re.fullmatch(
        r"\$\{\{\s*github\.event\.action\s*==\s*'([a-z_]+)'\s*\}\}",
        expression,
    )
    assert predicate is not None
    assert (action == predicate.group(1)) is should_cancel


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
    assert "proof.verify_public_inspects_v2(" in runs
    assert "proof.verify_volume_chain(" not in runs  # private raw never leaves producer
    assert "proof.verify_collector_manifest(" in runs
    assert "POSTRUN_TRUSTED_DEFAULT_WORKFLOW" in WORKFLOW.read_text()
    assert "RC6_ARTIFACT_ID" in runs and "GITHUB_RUN_ID" in runs
    assert "rc6-r163-rest-raw.zip" in str(independent)
    assert "automatic_go=false" in runs
    assert "PROBE_ONLY" in runs
    assert "R144" in WORKFLOW.read_text()
    assert "RC6_UNIFIED_PAPER_DEPLOY=GREEN" not in WORKFLOW.read_text()


def test_r203_private_inspect_is_not_uploaded_and_public_v2_is_failclosed():
    wf = _workflow()
    native = _runs(wf["jobs"]["offhost-native-proof"])
    independently_downloaded = _runs(wf["jobs"]["offhost-independent-bytes"])
    assert 'mkdir -m 700 "$proof_root/private"' in native
    for role in ("source", "collector"):
        assert (f'docker inspect "$' +
                ("source_cid" if role == "source" else "collector_cid") +
                f'" > "$proof_root/private/{role}-inspect.json"') in native
        assert (f'docker inspect "$' +
                ("source_cid" if role == "source" else "collector_cid") +
                f'" > "$proof_root/evidence/{role}-inspect.json"') not in native
    assert 'rc6_r163_native_volume_proof.py project' in native
    assert '--producer-public "$proof_root/evidence/source-inspect.json"' in native
    assert '--collector-public "$proof_root/evidence/collector-inspect.json"' in native
    assert 'rc6_r163_native_volume_proof.py privacy' in native
    assert 'proof.challenge_privacy_patterns(' in native
    assert 'proof.verify_probe_zip_privacy(' in native
    assert "RC6_R191_PUBLIC_INSPECT=HOLD79" not in native
    assert "RC6_R203_NATIVE=OFFHOST_PROBE_ONLY|release_go=NO" in native
    assert "proof.verify_public_inspects_v2(" in independently_downloaded
    assert "proof.verify_probe_zip_privacy(" in independently_downloaded
    assert "private Docker inspect SHA256 preimages" in independently_downloaded
    assert "POSTRUN_TRUSTED_DEFAULT_WORKFLOW" in WORKFLOW.read_text()


def test_r203_native_proof_requires_offhost_python_green_before_artifact_work():
    wf = _workflow()
    native = wf["jobs"]["offhost-native-proof"]
    assert native["needs"] == ["python311-lock-closure"]
    assert "needs.python311-lock-closure.result == 'success'" in native["if"]
    assert "github.event.pull_request.number == 1087" in native["if"]
    assert "github.event.pull_request.head.repo.full_name == github.repository" in native["if"]
    assert wf["jobs"]["offhost-independent-bytes"]["needs"] == ["offhost-native-proof"]
    assert native["timeout-minutes"] == "120"



def _r205_signals_diagnostic_python() -> str:
    native = _runs(_workflow()["jobs"]["offhost-native-proof"])
    begin = native.split("# RC6_R205_SIGNALS_DIAG_BEGIN\n", 1)
    assert len(begin) == 2, "R205 diagnostic missing"
    embedded = begin[1].split("<<'PY'; then\n", 1)
    assert len(embedded) == 2, "R205 bounded heredoc missing"
    script = embedded[1].split("\nPY\n", 1)
    assert len(script) == 2, "R205 heredoc not terminated"
    assert "RC6_R205_SIGNALS_DIAG_END" in script[1]
    ast.parse(script[0], filename="R205_SIGNALS_READ_ONLY_DIAGNOSTIC")
    return script[0] + "\n"


def _r205_run_diagnostic(xml_path: Path, monkeypatch, capsys,
                        *, emulate_owned_uid1000: bool) -> tuple[str, int]:
    """Run exact embedded diagnostic on XML fixtures, never Docker/host."""
    original_fstat = os.fstat

    def fixture_fstat(fd):
        metadata = original_fstat(fd)
        identity = 1000 if emulate_owned_uid1000 else 999
        return SimpleNamespace(st_uid=identity, st_gid=identity,
                               st_nlink=metadata.st_nlink,
                               st_size=metadata.st_size,
                               st_mode=metadata.st_mode)

    payload = _r205_signals_diagnostic_python()
    with monkeypatch.context() as scope:
        scope.setattr(os, "fstat", fixture_fstat)
        scope.setattr(sys, "argv", ["-", str(xml_path)])
        try:
            exec(compile(payload, "R205_SIGNALS_READ_ONLY_DIAGNOSTIC", "exec"),
                 {"__name__": "__main__"})
            rc = 0
        except SystemExit as exc:
            rc = exc.code if type(exc.code) is int else 79
    output = capsys.readouterr()
    assert output.err == ""
    return output.out, rc


def test_r205_signals_failure_diagnostic_bounded_and_no_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    xml = tmp_path / "signals.xml"
    xml.write_bytes(
        b'<testsuite tests="2" failures="1" errors="0" skipped="0">'
        b'<testcase classname="tests.test_rc6_owned_funnel_details" '
        b'name="test_causal_risk_case"><failure type="AssertionError">'
        b'CANARY_SECRET_XML_TRACEBACK_NEVER_PRINT'
        b'</failure></testcase>'
        b'<testcase classname="tests.test_exit_ledger_isolation_v17" '
        b'name="test_safe"/></testsuite>'
    )
    stdout, code = _r205_run_diagnostic(
        xml, monkeypatch, capsys, emulate_owned_uid1000=True)
    assert code == 0
    assert "RC6_R205_SIGNALS_JUNIT=HOLD79|cases=2|bad=1" in stdout
    assert "RC6_R205_SIGNALS_CASE=HOLD79|index=0|kind=failure" in stdout
    assert "classname=" not in stdout and "name=" not in stdout
    assert "type=" not in stdout
    assert "CANARY_SECRET_XML_TRACEBACK_NEVER_PRINT" not in stdout
    assert str(xml) not in stdout and len(stdout) < 500


def test_r205_signals_diagnostic_never_prints_opaque_xml_attributes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    xml = tmp_path / "signals.xml"
    xml.write_bytes(
        b'<testsuite tests="2" failures="1" errors="1">'
        b'<testcase classname="ghp_AbCdEfG1234567890abcdefghijklmnopqrstuvwxy" '
        b'name="AKIAIOSFODNN7EXAMPLE"><failure type="https://invalid.example/token">'
        b'PRIVATE_XML_DETAILS</failure></testcase>'
        b'<testcase classname="/private/unsafe-name" '
        b'name="CANARY_SECRET_TESTCASE"><error type="ErrorSecret">'
        b'CANARY_SECRET_XML_TRACE</error></testcase></testsuite>'
    )
    stdout, code = _r205_run_diagnostic(
        xml, monkeypatch, capsys, emulate_owned_uid1000=True)
    assert code == 0
    assert stdout.count("RC6_R205_SIGNALS_CASE=") == 2
    assert "RC6_R205_SIGNALS_CASE=HOLD79|index=0|kind=failure" in stdout
    assert "RC6_R205_SIGNALS_CASE=HOLD79|index=1|kind=error" in stdout
    for forbidden in ("ghp_AbCd", "AKIAIOSF", "https://", "/private/unsafe-name",
                      "CANARY_SECRET", "PRIVATE_XML_DETAILS", "ErrorSecret",
                      "classname=", "name=", "type=", str(xml)):
        assert forbidden not in stdout
    assert len(stdout) < 500


@pytest.mark.parametrize("case", (
    "unowned_uid", "invalid_xml", "missing_file", "symlink", "empty_xml",
))
def test_r205_signals_diagnostic_is_failclosed_without_public_data(
    case: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    xml = tmp_path / "signals.xml"
    if case == "symlink":
        victim = tmp_path / "CANARY_SECRET_PRIVATE"
        victim.write_text("CANARY_SECRET_PRIVATE_FILE")
        xml.symlink_to(victim)
    elif case != "missing_file":
        xml.write_bytes(
            b'<testsuite tests="1"><testcase classname="safe" name="safe">'
            b'<failure type="AssertionError">CANARY_SECRET_BODY'
            b'</failure></testcase></testsuite>'
            if case not in ("invalid_xml", "empty_xml") else (
                b'<testsuite unclosed CANARY_SECRET_BODY'
                if case == "invalid_xml" else b""
            )
        )
    stdout, code = _r205_run_diagnostic(
        xml, monkeypatch, capsys,
        emulate_owned_uid1000=case != "unowned_uid")
    assert code == 79
    assert stdout == "RC6_R205_SIGNALS_JUNIT=HOLD79|diagnostic_unavailable\n"
    assert "CANARY_SECRET" not in stdout


def test_r205_signals_native_failure_never_bypasses_exit79_or_cleanup() -> None:
    native = _runs(_workflow()["jobs"]["offhost-native-proof"])
    assert "if ! timeout 1200 docker exec --user 1000:1000" in native
    assert "python scripts/rc6_postdeploy_stress.py --output /app/data/session" in native
    assert "python - /app/data/session/signals_risk_exit_ledger.xml" in native
    start = native.index("# RC6_R205_SIGNALS_DIAG_BEGIN")
    end = native.index("# RC6_R205_SIGNALS_DIAG_END", start)
    after = native[end:end + 120]
    assert "exit 79" in after
    assert "owned_cid" in native and "trap cleanup_owned EXIT" in native
    assert "2>/dev/null <<'PY'; then" in native
    assert "case.get(" not in native[start:end]
    assert "node.get(" not in native[start:end]
    assert "node.text" not in native[start:end]
    assert "|kind=" in native[start:end]



def _r205_fast_python() -> str:
    wf = _workflow()
    steps = wf["jobs"]["python311-lock-closure"]["steps"]
    step = next(x for x in steps if
                x.get("name") == "R205 signals risk exit ledger exact 191 fixture diagnostic (offline)")
    source = step["run"]
    assert source.count("python - <<'PY'") == 1
    body = source.split("python - <<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
    ast.parse(body, filename="R205_PY311_191_FAST")
    return body + "\n"


def test_r205_fast_replay_exact_stress_source_list_and_isolated_params() -> None:
    script = _r205_fast_python()
    module = ast.parse(script)
    fast_paths = next(ast.literal_eval(item.value) for item in module.body
                      if isinstance(item, ast.Assign) and any(
                          isinstance(target, ast.Name) and target.id == "PATHS"
                          for target in item.targets))
    original = ast.parse((ROOT / "scripts/rc6_postdeploy_stress.py").read_text())
    original_groups = next(ast.literal_eval(item.value) for item in original.body
                           if isinstance(item, ast.Assign) and any(
                               isinstance(target, ast.Name) and target.id == "GROUPS"
                               for target in item.targets))
    assert tuple(fast_paths) == tuple(original_groups["signals_risk_exit_ledger"])
    assert len(fast_paths) == 10
    assert 'PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"' in script
    assert '"PAPER_HISTORICAL_CANDLE_SHADOW": "OFF"' in script
    assert '"PAPER_SECTOR_CONCENTRATION_POLICY": "OBSERVATION_ONLY"' in script
    assert '"PAPER_AI_GATE_MODE": "OFF"' in script
    assert '"-q",' in script and '"no:cacheprovider"' in script
    assert "timeout=300" in script and "stdout=private_log" in script
    assert "stderr=private_log" in script and "TemporaryDirectory" in script
    assert "CANARY_SECRET_" not in script
    assert "RC6_R205_SIGNALS_FAST=HOLD79" in script
    assert "RC6_R205_SIGNALS_FAST=PY311_OFFHOST_191_CASES" in script


@pytest.mark.parametrize("fail", (False, True))
def test_r205_fast_replay_xml_fixture_never_logs_private_body(
    fail: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    cases = []
    for index in range(191):
        cls = ("ghp_AbCdEfG1234567890abcdefghijklmnopqrstuvwxy"
               if index == 3 else "tests.test_exit_ledger_isolation_v17")
        name = "AKIAIOSFODNN7EXAMPLE" if index == 3 else f"test_safe_{index}"
        case = f'<testcase classname="{cls}" name="{name}">'
        if fail and index == 3:
            case += ('<failure type="https://invalid.example/token">'
                     'CANARY_SECRET_TRACEBACK_NEVER_PRINT</failure>')
        case += "</testcase>"
        cases.append(case)
    xml = (f'<testsuite tests="191" failures="{int(fail)}" '
           f'errors="0" skipped="0">' + "".join(cases) + "</testsuite>").encode()

    def fake_run(cmd, **kwargs):
        if cmd[1] == "-c":
            assert kwargs["timeout"] <= 90
            assert kwargs["stdout"] is kwargs["stderr"]
            # Simulate a validated private collector receipt for the red case.
            Path(cmd[3]).write_text(
                '{"schema":"rc6_r205_collect_v1","index":3,'
                '"module_ordinal":4,"source_line":123,'
                '"collected":191,"module_count":10}')
            return SimpleNamespace(returncode=0)
        assert cmd[:6] == [sys.executable, "-m", "pytest",
                            "-q", "-p", "no:cacheprovider"]
        assert kwargs["timeout"] == 300
        assert kwargs["stdout"] is kwargs["stderr"]
        assert "GITHUB_TOKEN" not in kwargs["env"]
        assert kwargs["env"]["PAPER_AI_GATE_MODE"] == "OFF"
        report = next(value.removeprefix("--junitxml=") for value in cmd
                      if value.startswith("--junitxml="))
        Path(report).write_bytes(xml)
        return SimpleNamespace(returncode=int(fail))

    monkeypatch.setattr(subprocess, "run", fake_run)
    try:
        exec(compile(_r205_fast_python(), "R205_PY311_191_FAST", "exec"),
             {"__name__": "__main__"})
        rc = 0
    except SystemExit as exc:
        rc = exc.code if type(exc.code) is int else 79
    output = capsys.readouterr()
    assert output.err == ""
    assert "CANARY_SECRET_TRACEBACK_NEVER_PRINT" not in output.out
    assert str(tmp_path) not in output.out
    if fail:
        assert rc == 79
        assert "RC6_R205_SIGNALS_FAST=HOLD79|cases=191|bad=1" in output.out
        assert "RC6_R205_SIGNALS_FAST_CASE=HOLD79|index=3|kind=failure" in output.out
        assert ("RC6_R205_SIGNALS_ISOLATE=HOLD79|index=3|"
                "module_ordinal=4|source_line=123") in output.out
        for forbidden in ("ghp_AbCd", "AKIAIOSF", "https://", "classname=",
                          "name=", "type="):
            assert forbidden not in output.out
    else:
        assert rc == 0
        assert output.out.strip() == (
            "RC6_R205_SIGNALS_FAST=PY311_OFFHOST_191_CASES|skipped=0|release_go=NO")



@pytest.mark.parametrize("case", (
    "no_xml", "empty_xml", "wrong_case_count", "wrong_suite_counter",
    "bad_xml", "symlink", "skipped_case", "exit_code_only",
    "nested_skipped", "rogue_failure", "rogue_testcase", "nested_suite",
))
def test_r205_fast_junit_rejects_missing_corrupt_or_inconsistent_receipts(
    case: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """No stdout/log/XML leak and no false offhost green after corrupt JUnit."""
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    real_cases = 190 if case == "wrong_case_count" else 191
    cases = []
    for index in range(real_cases):
        node = ('<skipped type="ghp_AbCdEfG1234567890abcdefghijklmnopqrstuvwxy">'
                'CANARY_SECRET_LOG</skipped>'
                if case == "skipped_case" and index == 3 else (
                    '<system-out><skipped type="AKIAIOSFODNN7EXAMPLE">'
                    'CANARY_SECRET_NESTED</skipped></system-out>'
                    if case == "nested_skipped" and index == 3 else ""
                ))
        cls = ("AKIAIOSFODNN7EXAMPLE" if index == 3
               else "tests.test_safe")
        cases.append(f'<testcase classname="{cls}" name="test_{index}">'
                     f'{node}</testcase>')
    reported_tests = 190 if case == "wrong_suite_counter" else 191
    if case == "rogue_testcase":
        report_xml = (
            '<testsuites><testsuite tests="0" failures="0" errors="0" '
            'skipped="0"/>' + "".join(cases) + '</testsuites>'
        ).encode()
    elif case == "nested_suite":
        report_xml = (
            '<testsuites><testsuite tests="0" failures="0" errors="0" '
            'skipped="0"><testsuite tests="191" failures="0" errors="0" '
            'skipped="0">' + "".join(cases) + '</testsuite>'
            '</testsuite></testsuites>'
        ).encode()
    else:
        report_xml = (
            f'<testsuite tests="{reported_tests}" failures="0" errors="0" '
            f'skipped="{int(case == "skipped_case")}">' +
            "".join(cases) +
            ('<failure type="ghp_AbCdEfG1234567890abcdefghijklmnopqrstuvwxy">'
             'CANARY_SECRET_ROGUE</failure>' if case == "rogue_failure" else '') +
            "</testsuite>"
        ).encode()

    def fake_run(cmd, **kwargs):
        if cmd[1] == "-c":
            assert kwargs["timeout"] <= 90
            assert kwargs["stdout"] is kwargs["stderr"]
            # Malformed/skipped fixtures never become public proof.
            return SimpleNamespace(returncode=79)
        report = Path(next(item.removeprefix("--junitxml=") for item in cmd
                           if item.startswith("--junitxml=")))
        assert kwargs["timeout"] == 300
        if case == "empty_xml":
            report.write_bytes(b"")
        elif case == "bad_xml":
            report.write_bytes(b"<testsuite CANARY_SECRET_BROKEN")
        elif case == "symlink":
            private = Path(tmp_path) / "CANARY_SECRET_PRIVATE_XML"
            private.write_bytes(report_xml)
            report.symlink_to(private)
        elif case != "no_xml":
            report.write_bytes(report_xml)
        return SimpleNamespace(returncode=1 if case == "exit_code_only" else 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    try:
        exec(compile(_r205_fast_python(), "R205_PY311_191_FAST", "exec"),
             {"__name__": "__main__"})
        rc = 0
    except SystemExit as exc:
        rc = exc.code if type(exc.code) is int else 79
    output = capsys.readouterr()
    assert rc == 79
    assert output.err == ""
    assert "RC6_R205_SIGNALS_FAST=HOLD79|" in output.out
    assert "PY311_OFFHOST_191_CASES" not in output.out
    assert "CANARY_SECRET" not in output.out
    assert "ghp_AbCd" not in output.out and "AKIAIOSF" not in output.out
    assert str(tmp_path) not in output.out
    assert len(output.out) < 500
    if case == "skipped_case":
        assert "|bad=1" in output.out
    if case == "exit_code_only":
        assert "|bad=0" in output.out


def test_r205_raw_junit_privacy_gate_always_holds_before_seal_or_upload() -> None:
    """Raw JUnit includes untrusted XML attributes; R206 projection required."""
    steps = _workflow()["jobs"]["offhost-native-proof"]["steps"]
    nstage = next(i for i, item in enumerate(steps)
                  if item.get("name") == "Run native stages in one UID1000 anonymous volume")
    stop = next(i for i, item in enumerate(steps)
                if item.get("name") == "R205 raw JUnit privacy hold before any upload (R206 required)")
    seal = next(i for i, item in enumerate(steps)
                if item.get("name") == "Seal all evidence before upload (artifact ID assigned later)")
    upload = next(i for i, item in enumerate(steps)
                  if item.get("uses", "").startswith("actions/upload-artifact@"))
    assert nstage < stop < seal < upload
    guard = steps[stop]
    assert guard["shell"] == "bash" and "continue-on-error" not in guard
    assert "if" not in guard and "uses" not in guard
    assert "set -Eeuo pipefail" in guard["run"]
    assert "RC6_R205_JUNIT_PRIVACY=HOLD79|raw_junit_not_projected" in guard["run"]
    assert "exit 79" in guard["run"]
    assert "if:" not in guard["run"] and "exit 0" not in guard["run"]
    assert steps[upload]["uses"] == "actions/upload-artifact@v4"
    assert "if" not in steps[upload] and "continue-on-error" not in steps[upload]


@pytest.mark.parametrize("receipt_case", (
    "valid", "missing", "wrong_count", "wrong_index", "unsafe_extra",
    "string_ordinal", "bad_line", "collector_red",
))
def test_r205b_collector_receipt_produces_only_numeric_indices(
    receipt_case: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    source_xml = (
        '<testsuite tests="191" failures="1" errors="0" skipped="0">' +
        "".join(
            f'<testcase classname="ghp_AbCdEfG1234567890abcdefghijklmnopqrstuvwxy" '
            f'name="AKIAIOSFODNN7EXAMPLE">'
            + ('<failure type="https://invalid.example/credential">'
               'CANARY_SECRET_PRIVATE</failure>' if i == 83 else '')
            + '</testcase>' for i in range(191)
        ) + '</testsuite>'
    ).encode()
    collection_seen = []

    def fake_run(cmd, **kwargs):
        assert kwargs["stdout"] is kwargs["stderr"]
        assert "GITHUB_TOKEN" not in kwargs["env"]
        if cmd[1] == "-c":
            collection_seen.append(True)
            assert kwargs["timeout"] <= 90
            assert "pytest_collection_finish" in cmd[2]
            assert int(cmd[4]) == 83
            assert "tests/test_rc6_go_startup_readiness.py" in cmd[5]
            if receipt_case != "missing":
                document = {
                    "schema": "rc6_r205_collect_v1",
                    "index": 83, "module_ordinal": 4, "source_line": 123,
                    "collected": 191, "module_count": 10,
                }
                if receipt_case == "wrong_count":
                    document["collected"] = 190
                elif receipt_case == "wrong_index":
                    document["index"] = 82
                elif receipt_case == "unsafe_extra":
                    document["nodeid"] = "ghp_AbCdEfG1234567890abcdefghijklmnopqrstuvwxy"
                elif receipt_case == "string_ordinal":
                    document["module_ordinal"] = "AKIAIOSFODNN7EXAMPLE"
                elif receipt_case == "bad_line":
                    document["source_line"] = -1
                Path(cmd[3]).write_text(__import__("json").dumps(document))
            return SimpleNamespace(returncode=79 if receipt_case == "collector_red"
                                   else 0)
        assert kwargs["timeout"] == 300
        report = next(value.removeprefix("--junitxml=") for value in cmd
                      if value.startswith("--junitxml="))
        Path(report).write_bytes(source_xml)
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(subprocess, "run", fake_run)
    try:
        exec(compile(_r205_fast_python(), "R205_PY311_191_FAST", "exec"),
             {"__name__": "__main__"})
        rc = 0
    except SystemExit as exc:
        rc = exc.code if type(exc.code) is int else 79
    output = capsys.readouterr()
    assert rc == 79 and output.err == ""
    assert collection_seen == [True]
    assert "RC6_R205_SIGNALS_FAST=HOLD79|cases=191|bad=1" in output.out
    assert "RC6_R205_SIGNALS_FAST_CASE=HOLD79|index=83|kind=failure" in output.out
    if receipt_case == "valid":
        assert ("RC6_R205_SIGNALS_ISOLATE=HOLD79|index=83|"
                "module_ordinal=4|source_line=123") in output.out
    else:
        assert "RC6_R205_SIGNALS_ISOLATE=HOLD79|collection_unavailable" in output.out
    for secret in ("ghp_AbCd", "AKIAIOSF", "https://", "CANARY_SECRET",
                   "nodeid", "classname=", "name=", "type=", str(tmp_path)):
        assert secret not in output.out
    assert len(output.out) < 600


def test_r205b_collector_uses_pytest_session_items_and_enforces_ten_file_order() -> None:
    script = ast.parse(_r205_fast_python())
    collector = next(ast.literal_eval(item.value) for item in script.body
                     if isinstance(item, ast.Assign) and any(
                         isinstance(name, ast.Name) and name.id == "COLLECTOR_CODE"
                         for name in item.targets))
    ast.parse(collector)
    assert "pytest_collection_finish" in collector
    assert "session.items" in collector
    assert "len(items) != 191" in collector
    assert "ordinals != sorted(ordinals)" in collector
    assert "source_line" in collector
    assert '["--collect-only", "-q", "-p", "no:cacheprovider"' in collector
    assert "print(" not in collector
    assert "item.nodeid" not in collector
    assert "case.get(" not in collector
    assert "write_text" in collector


def test_r206_offhost_exact_nine_projection_nodes_and_71_cases_failclosed() -> None:
    """All synthetic R206 privacy regressions execute, no silent deselection."""
    steps = _workflow()["jobs"]["python311-lock-closure"]["steps"]
    matches = [
        (index, step) for index, step in enumerate(steps)
        if step.get("name") == (
            "R206 XML and JSON opaque native projection regression (offhost TDD)"
        )
    ]
    assert len(matches) == 1
    index, step = matches[0]
    assert step["shell"] == "bash"
    assert "if" not in step and "continue-on-error" not in step
    assert "R203" in steps[index - 1]["name"]
    assert "R205" in steps[index + 1]["name"]

    run = step["run"]
    marker = "python - <<'PY'\n"
    assert run.count(marker) == 1
    assert run.strip().endswith("PY")
    script = run.split(marker, 1)[1].rsplit("\nPY", 1)[0]
    syntax = ast.parse(script)
    assignments = [
        node.value for node in syntax.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "NODES"
                for target in node.targets)
    ]
    assert len(assignments) == 1
    expected = tuple(
        "tests/test_rc6_r163_native_volume_proof.py::" + name
        for name in (
            "test_r206_raw_junit_opaque_attribute_is_not_public_evidence",
            "test_r206_raw_json_opaque_field_is_not_public_evidence",
            "test_r206_collector_projects_private_fixture_to_exact_public_schema",
            "test_r206_public_schema_rejects_tampered_synthetic_bytes",
            "test_r206_private_junit_invalid_counts_or_negative_nodes_hold",
            "test_r206_private_json_red_or_bool_in_place_of_zero_holds",
            "test_r206_private_junit_rejects_unaccounted_or_nested_testcase",
            "test_r206_private_json_rejects_nonfinite_exponent_in_discarded_field",
            "test_r206_stress_requires_complete_ordered_positive_junit_scenarios",
        )
    )
    assert ast.literal_eval(assignments[0]) == expected
    assert sum(
        isinstance(node, ast.Constant) and type(node.value) is int
        and node.value == 71
        for node in ast.walk(syntax)
    ) >= 3
    assert 'len(cases) != 71' in script
    assert 'sum(len(suite.findall("testcase")) for suite in suites) != 71' in script
    assert 'totals["tests"] != 71' in script
    assert 'totals["skipped"] != 0' in script
    assert 'proc.returncode != 0' in script
    assert 'next(root.iter(tag), None) is not None' in script
    assert "TemporaryFile() as private_log" in script
    assert "stdout=private_log" in script and "stderr=private_log" in script
    assert "R206_PRIVACY=HOLD79|tests=71" in script
    assert "R206_PRIVACY=PY311_71_GREEN|release_go=NO" in script
    assert "raise SystemExit(79)" in script
    assert "RC6_R205_JUNIT_PRIVACY=HOLD79" in _runs(
        _workflow()["jobs"]["offhost-native-proof"]
    )
