"""R162: purely offhost regressions for Predeploy V2 scoped cleanup.

All Docker commands below resolve to a fake executable in pytest's tmp_path;
these tests never connect to a Docker daemon, GitHub Actions, or a host.
"""

from __future__ import annotations

import os
import json
import subprocess
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/porota-predeploy-v2.yml"
SHA = "0123456789012345678901234567890123456789"
IMAGE = f"porota-predeploy-v2:{SHA}"
CID = "b" * 64


def _step(name: str) -> dict:
    steps = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["artifact-gate"]["steps"]
    matches = [item for item in steps if item.get("name") == name]
    assert len(matches) == 1
    return matches[0]


def _fake_docker(tmp_path: Path) -> tuple[Path, Path]:
    trace = tmp_path / "docker-calls.txt"
    foreign = tmp_path / "other-app-container"
    foreign.write_text("must remain", encoding="utf-8")
    (tmp_path / "foreign-image").write_text("FOREIGN_IMAGE_ALIVE", encoding="utf-8")
    binary = tmp_path / "docker"
    binary.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >> "$DOCKER_TRACE"\n'
        'case "$1 $2" in\n'
        '  "image inspect")\n'
        '    if [[ "$CASE" == "image_retag_race" || "$CASE" == "image_changed_on_recheck" || "$CASE" == "image_same_id_foreign_retag" ]]; then\n'
        '      count=0; [[ -f "$INSPECT_COUNT" ]] && count="$(<"$INSPECT_COUNT")"\n'
        '      count=$((count + 1)); printf "%s" "$count" > "$INSPECT_COUNT"\n'
        '      [[ "$count" -eq 2 ]] && printf "RETAGGED" > "$RETAG_MARKER"\n'
        '      if [[ "$CASE" == "image_changed_on_recheck" && "$count" -eq 2 ]]; then\n'
        '        echo "sha256:$(printf f%.0s {1..64})|$CANDIDATE_SHA|v2|[\\"$IMAGE\\"]"; exit 0; fi\n'
        '    fi\n'
        '    if [[ "$CASE" == "image_missing" ]]; then\n'
        '      echo "Error: No such image: $IMAGE" >&2; exit 1; fi\n'
        '    if [[ "$CASE" == "daemon_unknown" ]]; then echo "daemon unavailable" >&2; exit 2; fi\n'
        '    if [[ "$CASE" == "image_foreign" ]]; then\n'
        '      echo "sha256:$(printf a%.0s {1..64})|foreign|v2|[\\"$IMAGE\\"]"; exit 0; fi\n'
        '    if [[ "$CASE" == "image_shared" ]]; then\n'
        '      echo "sha256:$(printf a%.0s {1..64})|$CANDIDATE_SHA|v2|[\\"$IMAGE\\",\\"foreign:latest\\"]"; exit 0; fi\n'
        '    echo "sha256:$(printf a%.0s {1..64})|$CANDIDATE_SHA|v2|[\\"$IMAGE\\"]"; exit 0;;\n'
        '  "image rm")\n'
        '    if [[ "$CASE" == "image_retag_race" && "$3" == "$IMAGE" ]]; then\n'
        '      printf "FOREIGN_IMAGE_DELETED" > "$FOREIGN_IMAGE_MARKER"; fi\n'
        '    if [[ "$CASE" == "image_same_id_foreign_retag" && "$3" == "sha256:$(printf a%.0s {1..64})" ]]; then\n'
        '      printf "FOREIGN_IMAGE_DELETED" > "$FOREIGN_IMAGE_MARKER"; fi\n'
        '    [[ "$CASE" != "image_rm_fails" ]]; exit $?;;\n'
        'esac\n'
        'case "$1" in\n'
        '  create)\n'
        '    [[ "$CASE" == "name_collision" ]] && exit 44\n'
        '    [[ "$CASE" == "forged_cid" ]] && { echo "unknown-cid"; exit 0; }\n'
        '    printf b%.0s {1..64}; echo; exit 0;;\n'
        '  inspect)\n'
        '    if [[ "$3" == *State.ExitCode* ]]; then echo "0|false"; exit 0; fi\n'
        '    if [[ "$3" == *State.Running* ]]; then\n'
        '      if [[ "$CASE" == "start_fails_running" || "$CASE" == "stop_fails" || "$CASE" == "stop_still_running" ]]; then\n'
        '        if [[ "$CASE" != "stop_still_running" && -f "$STOP_SENTINEL" ]]; then echo false; else echo true; fi\n'
        '      else echo false; fi; exit 0; fi\n'
        '    if [[ "$CASE" == "native_foreign" ]]; then\n'
        '      echo "$CID|foreign|v2|1999"; exit 0; fi\n'
        '    echo "$CID|$CANDIDATE_SHA|v2|1999"; exit 0;;\n'
        '  start) [[ "$CASE" != "start_fails" && "$CASE" != "start_fails_running" && "$CASE" != "stop_fails" && "$CASE" != "stop_still_running" ]]; exit $?;;\n'
        '  stop) [[ "$CASE" == "stop_fails" ]] && exit 46; touch "$STOP_SENTINEL"; exit 0;;\n'
        '  rm) [[ "$*" != *"porota-native-"* ]] && exit 0\n'
        '      echo "FOREIGN_DELETE" > "$FOREIGN_MARKER"; exit 0;;\n'
        'esac\n'
        'exit 97\n', encoding="utf-8"
    )
    binary.chmod(0o700)
    return trace, foreign


def _env(tmp_path: Path, *, case: str, build: str, extract: str) -> tuple[dict[str, str], Path, Path]:
    runner = tmp_path / "runner"
    runner.mkdir()
    trace, foreign = _fake_docker(tmp_path)
    env = {
        **os.environ,
        "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
        "RUNNER_TEMP": str(runner),
        "GITHUB_RUN_ID": "1999",
        "GITHUB_RUN_ATTEMPT": "1",
        "CANDIDATE_SHA": SHA,
        "IMAGE": IMAGE,
        "CID": CID,
        "CASE": case,
        "RC6_BUILD_OUTCOME": build,
        "RC6_EXTRACT_OUTCOME": extract,
        "DOCKER_TRACE": str(trace),
        "FOREIGN_MARKER": str(foreign),
        "STOP_SENTINEL": str(tmp_path / "stopped"),
        "INSPECT_COUNT": str(tmp_path / "inspect-count"),
        "RETAG_MARKER": str(tmp_path / "retagged"),
        "FOREIGN_IMAGE_MARKER": str(tmp_path / "foreign-image"),
    }
    return env, trace, foreign


def _run_cleanup(tmp_path: Path, *, case: str, build: str = "success", extract: str = "success",
                 stage: str = "owned", stage_race: bool = False) -> tuple[int, str, list[str], Path, Path]:
    env, trace, foreign = _env(tmp_path, case=case, build=build, extract=extract)
    runner = Path(env["RUNNER_TEMP"])
    path = runner / "porota-predeploy-app-1999-1"
    if stage == "owned":
        path.mkdir(mode=0o700)
        (path / "owned-only").write_text("delete", encoding="utf-8")
        identity = path.stat()
        env.update({"RC6_EXTRACT_STAGE": str(path),
                    "RC6_EXTRACT_STAGE_ID": f"{identity.st_dev}:{identity.st_ino}:{os.getuid()}:700"})
    elif stage == "foreign_symlink":
        path.symlink_to(foreign)
        identity = foreign.stat()
        env.update({"RC6_EXTRACT_STAGE": str(path),
                    "RC6_EXTRACT_STAGE_ID": f"{identity.st_dev}:{identity.st_ino}:{os.getuid()}:700"})
    elif stage != "absent":
        raise AssertionError(stage)
    if stage_race:
        assert stage == "owned"
        foreign_stage = tmp_path / "foreign-stage"
        foreign_stage.mkdir(mode=0o700)
        (foreign_stage / "DO_NOT_DELETE").write_text("FOREIGN_WORKLOAD", encoding="utf-8")
        fake_stat = tmp_path / "stat"
        fake_stat.write_text(
            "#!/usr/bin/env bash\n"
            'receipt="$(/usr/bin/stat "$@")" || exit $?\n'
            'printf "%s\\n" "$receipt"\n'
            'if [[ "$*" == *"porota-predeploy-app-"* ]]; then\n'
            '  /usr/bin/mv -- "$RC6_EXTRACT_STAGE" "$RC6_EXTRACT_STAGE.owned-kept"\n'
            '  /usr/bin/mv -- "$FOREIGN_STAGE" "$RC6_EXTRACT_STAGE"\n'
            'fi\n',
            encoding="utf-8",
        )
        fake_stat.chmod(0o700)
        env["FOREIGN_STAGE"] = str(foreign_stage)
    outcome = subprocess.run(["bash", "-c", _step("Local cleanup")["run"]], cwd=tmp_path,
                             env=env, capture_output=True, text=True, timeout=5, check=False)
    calls = trace.read_text(encoding="utf-8").splitlines() if trace.exists() else []
    return outcome.returncode, outcome.stdout + outcome.stderr, calls, path, foreign


def test_cleanup_no_resource_must_have_two_explicit_skipped_receipts(tmp_path: Path) -> None:
    code, stdout, calls, stage, foreign = _run_cleanup(
        tmp_path, case="daemon_unknown", build="skipped", extract="skipped", stage="absent")
    assert code == 0, stdout
    assert "POROTA_PREDEPLOY_IMAGE_CLEANUP=GREEN|NO_RESOURCE" in stdout
    assert "POROTA_PREDEPLOY_STAGE_CLEANUP=GREEN|NO_RESOURCE" in stdout
    assert "POROTA_PREDEPLOY_LOCAL_CLEANUP=GREEN" in stdout
    assert calls == [] and not stage.exists() and foreign.read_text() == "must remain"


@pytest.mark.parametrize("case", ["image_foreign", "image_shared", "daemon_unknown", "image_missing"])
def test_cleanup_unknown_or_foreign_image_never_deleted(tmp_path: Path, case: str) -> None:
    code, stdout, calls, _, foreign = _run_cleanup(tmp_path, case=case)
    assert code == 79, (case, code, stdout)
    assert not any(call.startswith("image rm") for call in calls)
    assert foreign.read_text() == "must remain"


def test_cleanup_owned_image_and_stage_both_retained_hold79(tmp_path: Path) -> None:
    code, stdout, calls, stage, foreign = _run_cleanup(tmp_path, case="healthy")
    assert code == 79, stdout
    assert "POROTA_PREDEPLOY_IMAGE_CLEANUP=RETAINED|HOLD79" in stdout
    assert "POROTA_PREDEPLOY_STAGE_CLEANUP=RETAINED|HOLD79" in stdout
    assert not any(call.startswith("image rm") for call in calls)
    assert (stage / "owned-only").read_text() == "delete"
    assert foreign.read_text() == "must remain"


def test_retag_after_exact_image_receipt_cannot_delete_foreign_image(tmp_path: Path) -> None:
    code, stdout, calls, _, foreign = _run_cleanup(
        tmp_path, case="image_retag_race", build="success",
        extract="skipped", stage="absent")
    assert code == 79, stdout
    assert (tmp_path / "foreign-image").read_text() == "FOREIGN_IMAGE_ALIVE"
    assert not any(call.startswith("image rm") for call in calls)
    assert f"image rm {IMAGE}" not in calls
    assert foreign.read_text() == "must remain"


def test_same_id_new_foreign_sole_tag_is_not_removed(tmp_path: Path) -> None:
    # An immutable image ID does NOT anchor tag ownership: another same-UID
    # client can point a foreign sole tag to that ID after our label receipt.
    code, stdout, calls, _, foreign = _run_cleanup(
        tmp_path, case="image_same_id_foreign_retag", build="success",
        extract="skipped", stage="absent")
    assert code == 79, stdout
    assert (tmp_path / "foreign-image").read_text() == "FOREIGN_IMAGE_ALIVE"
    assert not any(call.startswith("image rm") for call in calls)
    assert foreign.read_text() == "must remain"


def test_changed_image_id_between_inspections_causes_hold_without_delete(tmp_path: Path) -> None:
    code, stdout, calls, _, foreign = _run_cleanup(
        tmp_path, case="image_changed_on_recheck", build="success",
        extract="skipped", stage="absent")
    assert code == 79, stdout
    assert not any(call.startswith("image rm") for call in calls)
    assert foreign.read_text() == "must remain"


def test_cleanup_owned_stage_never_follows_foreign_symlink(tmp_path: Path) -> None:
    code, stdout, calls, stage, foreign = _run_cleanup(tmp_path, case="healthy", stage="foreign_symlink")
    assert code == 79, stdout
    assert stage.is_symlink() and foreign.read_text() == "must remain"
    assert not any(call.startswith(("container ", "ps ", "system ", "volume ")) for call in calls)


def test_same_uid_stat_to_delete_swap_cannot_remove_foreign_stage(tmp_path: Path) -> None:
    # Deterministic TOCTOU regression: after a truthful stat receipt, another
    # same-UID process atomically places a foreign inode at the same path.
    code, stdout, calls, stage, foreign = _run_cleanup(
        tmp_path, case="healthy", build="skipped", extract="success",
        stage="owned", stage_race=True,
    )
    assert code == 79, stdout
    assert (stage / "DO_NOT_DELETE").read_text(encoding="utf-8") == "FOREIGN_WORKLOAD"
    assert (Path(str(stage) + ".owned-kept") / "owned-only").read_text() == "delete"
    assert foreign.read_text() == "must remain"
    assert not any(call.startswith(("container ", "ps ", "system ", "volume ")) for call in calls)


@pytest.mark.parametrize("build,extract", [("failure", "failure"), ("success", "failure")])
def test_missing_receipt_after_attempt_is_unknown_not_noop(tmp_path: Path,
                                                           build: str, extract: str) -> None:
    code, stdout, _, stage, foreign = _run_cleanup(
        tmp_path, case="image_missing", build=build, extract=extract, stage="absent")
    assert code == 79, stdout
    assert "NO_RESOURCE" not in stdout
    assert not stage.exists() and foreign.read_text() == "must remain"


def _run_native(tmp_path: Path, case: str) -> tuple[int, str, list[str], Path]:
    env, trace, foreign = _env(tmp_path, case=case, build="success", extract="success")
    # Extract and exercise only the function after its private runner stage
    # has been exclusively initialized by the parent workflow step.
    (Path(env["RUNNER_TEMP"]) / "porota-native-sizing-evidence" / "payload" / "tmp").mkdir(parents=True)
    code = _step("Exact image native sizing and generated catalog proof")["run"]
    assert "# RC6_A980_NATIVE_OWNER_BEGIN" in code
    assert "# RC6_A980_NATIVE_OWNER_END" in code
    function = code.split("# RC6_A980_NATIVE_OWNER_BEGIN", 1)[1].split(
        "# RC6_A980_NATIVE_OWNER_END", 1)[0]
    result = subprocess.run(["bash", "-c", "set -Eeuo pipefail\n" + function + "\nrun_native_proof cases true"],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=8, check=False)
    calls = trace.read_text(encoding="utf-8").splitlines() if trace.exists() else []
    return result.returncode, result.stdout + result.stderr, calls, foreign


@pytest.mark.parametrize("case,expected_remove", [
    ("healthy", True), ("name_collision", False), ("forged_cid", False),
    ("native_foreign", False), ("start_fails", True), ("start_fails_running", True),
    ("stop_fails", False), ("stop_still_running", False),
])
def test_native_owner_exact_cid_only_and_never_foreign_name(tmp_path: Path, case: str,
                                                             expected_remove: bool) -> None:
    code, stdout, calls, foreign = _run_native(tmp_path, case)
    assert (code == 0) == (case == "healthy"), (case, code, stdout)
    assert (f"rm {CID}" in calls) == expected_remove, (case, calls)
    assert not any("porota-native-" in call for call in calls if call.startswith("rm "))
    assert foreign.read_text() == "must remain"
    assert not any(call.startswith(("ps ", "container ls", "system ", "volume ")) for call in calls)
    if case == "name_collision":
        assert "create_outcome_unknown_no_cid" in stdout
        assert "NO_RESOURCE" not in stdout


@pytest.mark.parametrize("preexisting", [False, True])
def test_native_host_parent_remains_mode700_and_exclusive(tmp_path: Path, preexisting: bool) -> None:
    env, _, foreign = _env(tmp_path, case="healthy", build="success", extract="success")
    root = Path(env["RUNNER_TEMP"]) / "porota-native-sizing-evidence"
    if preexisting:
        root.mkdir(mode=0o700)
        (root / "FOREIGN").write_text("do-not-touch", encoding="utf-8")
    source = _step("Exact image native sizing and generated catalog proof")["run"]
    setup_only = source.split("cleanup_native_proof() {", 1)[0]
    result = subprocess.run(["bash", "-c", setup_only], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=5, check=False)
    assert foreign.read_text() == "must remain"
    assert result.returncode == 79, result.stdout + result.stderr
    assert "root_same_uid_path_swap_unresolved" in result.stdout
    if preexisting:
        assert (root / "FOREIGN").read_text() == "do-not-touch"
    else:
        assert not root.exists()
    assert 'mkdir -m 700 -- "$rc6_native_root"' in source  # retained, not admitted
    assert "-v \"$RUNNER_TEMP/porota-native-sizing-evidence/payload:/proof\"" in source


def test_unresolved_same_uid_native_stage_swap_is_held_before_any_write(tmp_path: Path) -> None:
    env, trace, foreign = _env(tmp_path, case="healthy", build="success", extract="success")
    root = Path(env["RUNNER_TEMP"]) / "porota-native-sizing-evidence"
    # A same-UID actor can swap any pathname after a successful stat; until a
    # descriptor-anchored protocol is reviewed, the entire stage must HOLD.
    script = _step("Exact image native sizing and generated catalog proof")["run"]
    result = subprocess.run(["bash", "-c", script], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=7, check=False)
    assert result.returncode == 79, result.stdout + result.stderr
    assert "root_same_uid_path_swap_unresolved" in result.stdout
    assert not root.exists() and not trace.exists()
    assert foreign.read_text() == "must remain"


def test_predeploy_lifecycle_no_elevation_or_global_docker_and_immutable_source_gate() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    local = _step("Local cleanup")
    assert local["if"] == "always()"
    assert local["id"] == "rc6_local_cleanup"
    assert 'steps.rc6_build_once.outcome' in source
    assert 'steps.rc6_extract_application.outcome' in source
    assert "source_image_provenance" in source
    assert "sudo " not in source
    for command in ("docker ps ", "docker container ls", "docker system prune",
                    "docker image prune", "docker image rm -f", "docker rm -f"):
        assert command not in source, command
    assert 'docker image rm "$IMAGE"' not in source
    assert 'docker image rm "$rc6_image_id"' not in source
    assert 'rm -rf -- "$RC6_EXTRACT_STAGE"' not in source
    assert "github.event.pull_request.number != 1087" in source  # R144 remains locked
    assert _step("Predeploy summary")["if"] == (
        "${{ success() && steps.rc6_local_cleanup.outcome == 'success' }}")


def test_predeploy_lifecycle_scripts_are_valid_bash() -> None:
    for name in ("Exact image native sizing and generated catalog proof",
                 "Local cleanup", "Verify required published native evidence"):
        script = _step(name)["run"]
        check = subprocess.run(["bash", "-n"], input=script, capture_output=True,
                               text=True, timeout=5, check=False)
        assert check.returncode == 0, (name, check.stderr)


def test_native_upload_is_exact_file_allowlist_not_mutable_directory() -> None:
    upload = _step("Upload predeploy evidence")
    assert upload["if"] == "${{ always() && steps.rc6_artifact_gate.outcome == 'success' }}"
    paths = upload["with"]["path"].splitlines()
    native = [item.strip() for item in paths
              if "porota-native-sizing-evidence/" in item]
    assert len(native) == 13
    assert all("/payload/" in item and item.endswith((".xml", ".json")) for item in native)
    assert all("*" not in item and not item.endswith("/") and not item.startswith("!") for item in native)
    source = _step("Verify required published native evidence")["run"]
    for forbidden in ("NATIVE_EVIDENCE_UNDECLARED_FILE", "REQUIRED_PREDEPLOY_EVIDENCE_ALIASED",
                      "PREDEPLOY_NATIVE_ALLOWLIST_DRIFT", "REQUIRED_PREDEPLOY_EVIDENCE_UNREADABLE"):
        assert forbidden in source


def _native_gate_fixture(tmp_path: Path, case: str) -> tuple[int, str]:
    runner = tmp_path / "runner"
    proof = runner / "porota-native-sizing-evidence" / "payload"
    proof.mkdir(parents=True)
    proof.parent.chmod(0o700)
    xmls = ("native-bound.xml", *(f"session/{name}.xml" for name in (
        "dashboard_auth_52", "publication_io_identity", "scalping_capacity",
        "scheduler_monitor_backup", "signals_risk_exit_ledger", "sources_readiness_network",
        "sql_shadow_wal", "treasury_caucion")))
    xml = ('<testsuites><testsuite tests="1" failures="0" errors="0" skipped="0">'
           '<testcase classname="rc6" name="verified"/></testsuite></testsuites>')
    for name in xmls:
        path = proof / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(xml, encoding="utf-8")
    normal = json.dumps({"status": "GREEN", "production_data_mounted": False})
    for name in ("session/stress.json", "catalog/full-catalog.json",
                 "cadence/cadence-fresh.json", "cadence/cadence-legacy.json"):
        path = proof / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(normal, encoding="utf-8")
    for name in ("porota-frozen-candidate.json", "porota-deploy-bundle-v2.tgz",
                 "porota-deploy-bundle-v2-manifest.json", "porota-predeploy-image.tar.gz"):
        (tmp_path / name).write_text("test", encoding="utf-8")
    (tmp_path / "porota-governed-tests.json").write_text(json.dumps({
        "status": "GREEN", "failures": 0, "errors": 0, "skipped": 0, "xfail": 0,
    }), encoding="utf-8")
    if case == "undeclared":
        (proof / "session" / "unreviewed.json").write_text("{}", encoding="utf-8")
    elif case == "symlink":
        target = tmp_path / "foreign-evidence.xml"
        target.write_text(xml, encoding="utf-8")
        (proof / "native-bound.xml").unlink()
        (proof / "native-bound.xml").symlink_to(target)
    elif case == "symlink_parent":
        foreign = tmp_path / "foreign-session"
        (proof / "session").rename(foreign)
        (proof / "session").symlink_to(foreign, target_is_directory=True)
    elif case == "unreadable":
        (proof / "native-bound.xml").chmod(0o000)
    elif case == "junit_summary_false_green":
        (proof / "native-bound.xml").write_text(
            '<testsuites><testsuite tests="1" failures="1" errors="0" skipped="0">'
            '<testcase classname="rc6" name="counter_mismatch"/></testsuite></testsuites>',
            encoding="utf-8",
        )
    elif case == "governed_boolean_false_green":
        (tmp_path / "porota-governed-tests.json").write_text(json.dumps({
            "status": "GREEN", "failures": False, "errors": 0, "skipped": 0, "xfail": 0,
        }), encoding="utf-8")
    elif case == "allowed_temp":
        (proof / "tmp").mkdir()
        (proof / "tmp" / "transient.json").write_text("{}", encoding="utf-8")
    elif case != "valid":
        raise AssertionError(case)
    code = _step("Verify required published native evidence")["run"]
    assert "RC6_NATIVE_EVIDENCE_OFFHOST_VALIDATOR_BEGIN" in code
    # Exercise the frozen parser even though the live gate deliberately
    # holds before any mutable proof path is read.
    code = code.split("# RC6_NATIVE_EVIDENCE_OFFHOST_VALIDATOR_BEGIN", 1)[1]
    # Move only test fixture paths; never touch fixed /tmp CI artifacts.
    code = code.replace("Path('/tmp/porota-", f"Path('{tmp_path}/porota-")
    result = subprocess.run(["bash", "-c", code], cwd=ROOT, env={
        **os.environ, "RUNNER_TEMP": str(runner)}, capture_output=True,
        text=True, timeout=8, check=False)
    return result.returncode, result.stdout + result.stderr


def test_native_live_evidence_gate_holds_before_mutable_path_read(tmp_path: Path) -> None:
    # The actual gate must not inspect or upload live filesystem paths while
    # same-UID stat->parse->upload TOCTOU lacks a sealed descriptor contract.
    runner = tmp_path / "runner"
    runner.mkdir()
    script = _step("Verify required published native evidence")["run"]
    result = subprocess.run(["bash", "-c", script], cwd=ROOT, env={
        **os.environ, "RUNNER_TEMP": str(runner)}, capture_output=True,
        text=True, timeout=5, check=False)
    assert result.returncode == 79, result.stdout + result.stderr
    assert "proof_same_uid_toctou_unresolved" in result.stdout
    assert list(runner.iterdir()) == []


@pytest.mark.parametrize("case,reason", [
    ("valid", "POROTA_REQUIRED_EVIDENCE=GREEN"),
    ("allowed_temp", "POROTA_REQUIRED_EVIDENCE=GREEN"),
    ("undeclared", "NATIVE_EVIDENCE_UNDECLARED_FILE"),
    ("symlink", "REQUIRED_PREDEPLOY_EVIDENCE_ALIASED"),
    ("symlink_parent", "REQUIRED_PREDEPLOY_EVIDENCE_ALIASED"),
    ("unreadable", "REQUIRED_PREDEPLOY_EVIDENCE_UNREADABLE"),
    ("junit_summary_false_green", "REQUIRED_NATIVE_JUNIT_NOT_GREEN"),
    ("governed_boolean_false_green", "REQUIRED_GOVERNED_JUNIT_NOT_GREEN"),
])
def test_native_receipts_never_alias_or_publish_undeclared_files(
    tmp_path: Path, case: str, reason: str,
) -> None:
    rc, output = _native_gate_fixture(tmp_path, case)
    assert (rc == 0) == (case in ("valid", "allowed_temp")), (case, rc, output)
    assert reason in output, (case, output)
