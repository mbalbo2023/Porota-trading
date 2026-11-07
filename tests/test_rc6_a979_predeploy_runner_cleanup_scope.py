"""A979: Predeploy V2 cleanup may only affect its own ephemeral resources."""

import os
import json
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/porota-predeploy-v2.yml"


def _predeploy_cleanup() -> str:
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = data["jobs"]["artifact-gate"]["steps"]
    matches = [step for step in steps if step.get("name") == "Local cleanup"]
    assert len(matches) == 1
    assert matches[0]["if"] == "always()"
    assert matches[0]["shell"] == "bash"
    return matches[0]["run"]


def test_predeploy_cleanup_never_enumerates_containers_by_shared_image() -> None:
    """An image ancestry match is not proof of Porota container ownership."""
    cleanup = _predeploy_cleanup()
    assert "docker ps" not in cleanup
    assert "docker container rm" not in cleanup
    assert "docker rm" not in cleanup
    assert "xargs" not in cleanup


def test_predeploy_cleanup_retains_only_no_force_candidate_tag_cleanup() -> None:
    cleanup = _predeploy_cleanup()
    assert 'docker image rm "$IMAGE"' in cleanup
    assert 'if [[ "$rc6_image_state" != GREEN || "$rc6_stage_state" != GREEN ]]; then' in cleanup
    assert "echo 'POROTA_PREDEPLOY_LOCAL_CLEANUP=WARN|image_or_stage_cleanup_unverified'\n  exit 79" in cleanup
    assert "docker image rm -f" not in cleanup
    assert "docker system prune" not in cleanup
    assert "docker image prune" not in cleanup
    assert "docker volume prune" not in cleanup


def _exercise_runner_docker_cleanup(tmp_path: Path, case: str) -> tuple[int, str, list[str], bool]:
    """Run the isolated Docker branch only, never the private-stage cleanup."""
    cleanup = _predeploy_cleanup()
    assert "# RC6_A979_OWNED_IMAGE_BEGIN" in cleanup
    assert "# RC6_A979_OWNED_IMAGE_END" in cleanup
    commands = cleanup.split("# RC6_A979_OWNED_IMAGE_BEGIN", 1)[1].split(
        "# RC6_A979_OWNED_IMAGE_END", 1)[0]
    foreign = tmp_path / "foreign-container-alive"
    foreign.write_text("FOREIGN_RUNNING", encoding="utf-8")
    trace = tmp_path / "docker.calls"
    binary = tmp_path / "docker"
    binary.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >> "$DOCKER_TRACE"\n'
        'if [[ "$1 $2" == "image inspect" ]]; then\n'
        '  if [[ "$RC6_FAKE_CASE" == "inspect_failure" ]]; then exit 7; fi\n'
        '  case "$RC6_FAKE_CASE" in\n'
        '    foreign_label) echo "sha256:$(printf a%.0s {1..64})|foreign|v2";;\n'
        '    *) echo "sha256:$(printf a%.0s {1..64})|0123456789012345678901234567890123456789|v2";;\n'
        '  esac\n'
        '  exit 0\n'
        'fi\n'
        'if [[ "$1 $2" == "container ls" ]]; then\n'
        '  if [[ "$RC6_FAKE_CASE" == "shared_image" ]]; then echo foreign-id; fi\n'
        '  exit 0\n'
        'fi\n'
        'if [[ "$1 $2" == "image rm" ]]; then\n'
        '  if [[ "$RC6_FAKE_CASE" == "rm_failure" ]]; then exit 8; fi\n'
        '  exit 0\n'
        'fi\n'
        'if [[ "$1" == "rm" ]]; then rm -f "$FOREIGN_MARKER"; exit 0; fi\n'
        "exit 97\n", encoding="utf-8")
    binary.chmod(0o700)
    env = {**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
           "IMAGE": "porota-predeploy-v2:0123456789012345678901234567890123456789",
           "CANDIDATE_SHA": "0123456789012345678901234567890123456789",
           "RC6_FAKE_CASE": case, "FOREIGN_MARKER": str(foreign),
           "DOCKER_TRACE": str(trace)}
    result = subprocess.run(["bash", "-c", commands], env=env,
                            cwd=tmp_path, timeout=3, check=False,
                            capture_output=True, text=True)
    assert foreign.read_text(encoding="utf-8") == "FOREIGN_RUNNING"
    calls = trace.read_text(encoding="utf-8").splitlines() if trace.exists() else []
    return result.returncode, result.stdout, calls, foreign.exists()


def test_predeploy_cleanup_never_untags_foreign_or_shared_image(tmp_path: Path) -> None:
    for case in ("foreign_label", "shared_image", "inspect_failure"):
        case_dir = tmp_path / case
        case_dir.mkdir()
        code, stdout, calls, foreign_alive = _exercise_runner_docker_cleanup(case_dir, case)
        # The isolated snippet reports WARN; the whole workflow then exits 79
        # after its separate private-stage cleanup, checked structurally below.
        assert code == 0
        assert "POROTA_PREDEPLOY_IMAGE_CLEANUP=WARN" in stdout
        assert not any("image rm" in x or x.startswith("rm ") for x in calls)
        assert foreign_alive


def test_predeploy_cleanup_reports_failed_image_removal_truthfully(tmp_path: Path) -> None:
    code, stdout, calls, _ = _exercise_runner_docker_cleanup(tmp_path, "rm_failure")
    assert code == 0
    assert "POROTA_PREDEPLOY_IMAGE_CLEANUP=WARN" in stdout
    assert sum("image rm" in x for x in calls) == 1


def test_predeploy_cleanup_exact_private_image_only(tmp_path: Path) -> None:
    code, stdout, calls, foreign_alive = _exercise_runner_docker_cleanup(tmp_path, "healthy")
    assert code == 0
    assert "POROTA_PREDEPLOY_IMAGE_CLEANUP=GREEN" in stdout
    assert sum("image rm" in x for x in calls) == 1
    assert foreign_alive


def test_predeploy_containers_have_explicit_creation_cleanup() -> None:
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    source = "\n".join(step.get("run", "") for step in data["jobs"]["artifact-gate"]["steps"])
    assert 'docker rm -f' not in source
    assert '--label "porota.commit=$CANDIDATE_SHA"' in source
    assert '--label "porota.run_id=$GITHUB_RUN_ID"' in source
    assert 'docker rm "$rc6_extract_container_id"' in source
    assert 'docker run --rm --pull never' in source
    # A980: predictable names do not prove ownership: a failed create from an
    # existing foreign name must not let the EXIT trap erase that container.
    assert 'docker rm -f "porota-native-cases-$GITHUB_RUN_ID"' not in source
    assert 'docker rm "$rc6_name"' not in source
    assert 'docker start -a "$rc6_name"' not in source
    assert 'docker inspect -f \'{{.State.ExitCode}}|{{.State.OOMKilled}}\' "$rc6_name"' not in source
    assert 'rc6_new_id="$(docker create --name "$rc6_name"' in source
    assert 'docker rm "$rc6_container_id"' in source
    assert 'trap cleanup_native_proof EXIT' in source


def _native_proof_shell(tmp_path: Path, case: str, stage: str = "cases") -> tuple[int, list[str], bool]:
    """Exercise only the native function with fake Docker; never call host Docker."""
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = data["jobs"]["artifact-gate"]["steps"]
    source = next(s["run"] for s in steps
                  if s.get("name") == "Exact image native sizing and generated catalog proof")
    assert "# RC6_A980_NATIVE_OWNER_BEGIN" in source
    assert "# RC6_A980_NATIVE_OWNER_END" in source
    functions = source.split("# RC6_A980_NATIVE_OWNER_BEGIN", 1)[1].split(
        "# RC6_A980_NATIVE_OWNER_END", 1)[0]
    trace, foreign = tmp_path / "docker.calls", tmp_path / "foreign-running"
    foreign.write_text("foreign", encoding="utf-8")
    fake_docker = tmp_path / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >> "$DOCKER_TRACE"\n'
        'case "$1" in\n'
        ' create)\n'
        '   if [[ "$RC6_FAKE_CASE" == "name_collision" ]]; then exit 44; fi\n'
        '   printf "b%.0s" {1..64}; echo; exit 0;;\n'
        ' start) [[ "$RC6_FAKE_CASE" != "start_failure" && "$RC6_FAKE_CASE" != "running" && "$RC6_FAKE_CASE" != "stop_failure" ]]; exit $?;;\n'
        ' inspect)\n'
        '   if [[ "$3" == *State.ExitCode* ]]; then echo "0|false"; exit 0; fi\n'
        '   if [[ "$3" == *State.Running* ]]; then\n'
        '     if [[ "$RC6_FAKE_CASE" == "running" || "$RC6_FAKE_CASE" == "stop_failure" ]]; then echo true; else echo false; fi; exit 0\n'
        '   fi\n'
        '   if [[ "$RC6_FAKE_CASE" == "wrong_owner" ]]; then\n'
        '     printf "b%.0s" {1..64}; echo "|foreign|v2|1999"; exit 0\n'
        '   fi\n'
        '   printf "b%.0s" {1..64}; echo "|0123456789012345678901234567890123456789|v2|1999"; exit 0;;\n'
        ' stop) [[ "$RC6_FAKE_CASE" != "stop_failure" ]]; exit $?;;\n'
        ' rm)\n'
        '   for arg in "$@"; do\n'
        '     if [[ "$arg" == "porota-native-$RC6_STAGE-1999" ]]; then rm -f "$FOREIGN_MARKER"; fi\n'
        '   done\n'
        '   exit 0;;\n'
        'esac\n'
        "exit 97\n", encoding="utf-8")
    fake_docker.chmod(0o700)
    env = {**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
           "RUNNER_TEMP": str(tmp_path), "RC6_TMPDIR": str(tmp_path), "GITHUB_RUN_ID": "1999",
           "CANDIDATE_SHA": "0123456789012345678901234567890123456789",
           "IMAGE": "porota-predeploy-v2:0123456789012345678901234567890123456789",
           "RC6_STAGE": stage, "RC6_FAKE_CASE": case,
           "FOREIGN_MARKER": str(foreign), "DOCKER_TRACE": str(trace)}
    execution = 'run_native_proof "' + stage + '" true'
    if stage == "session":
        execution += ' &\nwait "$!"'
    done = subprocess.run(["bash", "-c", "set -Eeuo pipefail\n" + functions + "\n" + execution],
                          env=env, cwd=tmp_path, capture_output=True,
                          text=True, timeout=5, check=False)
    calls = trace.read_text(encoding="utf-8").splitlines() if trace.exists() else []
    return done.returncode, calls, foreign.exists()


@pytest.mark.parametrize("stage", ["cases", "session"])
def test_native_create_name_collision_leaves_foreign_container(tmp_path: Path, stage: str) -> None:
    code, calls, foreign_alive = _native_proof_shell(tmp_path, "name_collision", stage)
    assert code != 0
    assert foreign_alive
    assert any(s.startswith("create ") for s in calls)
    assert not any(s.startswith("rm ") for s in calls)


@pytest.mark.parametrize("stage", ["cases", "session"])
def test_native_failed_start_cleans_only_created_id(tmp_path: Path, stage: str) -> None:
    code, calls, foreign_alive = _native_proof_shell(tmp_path, "start_failure", stage)
    assert code != 0
    assert foreign_alive
    assert any(s.startswith("rm ") and s.endswith("b" * 64) for s in calls)
    assert not any("porota-native-" in s for s in calls if s.startswith("rm "))


def test_native_wrong_owner_never_deleted(tmp_path: Path) -> None:
    code, calls, foreign_alive = _native_proof_shell(tmp_path, "wrong_owner")
    assert code != 0
    assert foreign_alive
    assert not any(s.startswith("rm ") for s in calls)


def test_native_success_uses_only_created_id(tmp_path: Path) -> None:
    code, calls, foreign_alive = _native_proof_shell(tmp_path, "success")
    assert code == 0
    assert foreign_alive
    assert any(s.startswith("rm ") and s.endswith("b" * 64) for s in calls)
    assert not any("porota-native-" in s for s in calls if s.startswith("rm "))


@pytest.mark.parametrize("case,expect_stop,expect_remove", (
    ("running", True, True), ("stop_failure", True, False),
))
def test_native_running_cleanup_stops_only_owned_id_then_no_force_removes(
    tmp_path: Path, case: str, expect_stop: bool, expect_remove: bool
) -> None:
    code, calls, foreign_alive = _native_proof_shell(tmp_path, case)
    assert code != 0  # Timed-out/failed native process is never certified.
    assert foreign_alive
    assert any(s.startswith("stop --time 10 ") and s.endswith("b" * 64) for s in calls) == expect_stop
    assert any(s.startswith("rm ") and s.endswith("b" * 64) for s in calls) == expect_remove
    assert not any(s.startswith("rm -f ") or "porota-native-" in s for s in calls if s.startswith("rm "))


def _extract_app_shell(tmp_path: Path, case: str) -> tuple[int, str, list[str], bool]:
    steps = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["artifact-gate"]["steps"]
    script = next(s["run"] for s in steps if s.get("name") == "Extract exact image application filesystem")
    stage = tmp_path / "stage"
    stage.mkdir(mode=0o700)
    foreign = tmp_path / "foreign-container-running"
    foreign.write_text("FOREIGN", encoding="utf-8")
    binary = tmp_path / "docker"
    trace = tmp_path / "docker.calls"
    binary.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >> "$DOCKER_TRACE"\n'
        'case "$1" in\n'
        ' create)\n'
        '   if [[ "$RC6_FAKE_CASE" == "create_failure" ]]; then exit 44; fi\n'
        '   if [[ "$RC6_FAKE_CASE" == "forged_id" ]]; then echo foreign-container; exit 0; fi\n'
        '   printf "c%.0s" {1..64}; echo; exit 0;;\n'
        ' inspect)\n'
        '   if [[ "$RC6_FAKE_CASE" == "wrong_owner" ]]; then\n'
        '      printf "c%.0s" {1..64}; echo "|FOREIGN|v2|1999"; exit 0; fi\n'
        '   printf "c%.0s" {1..64}; echo "|0123456789012345678901234567890123456789|v2|1999"; exit 0;;\n'
        ' cp)\n'
        '   if [[ "$RC6_FAKE_CASE" == "copy_failure" ]]; then exit 45; fi\n'
        '   touch "$RC6_TMPDIR/porota-predeploy-app/Dockerfile"; exit 0;;\n'
        ' rm)\n'
        '   for arg in "$@"; do\n'
        '     if [[ "$arg" == "foreign-container" ]]; then rm -f "$FOREIGN_MARKER"; fi\n'
        '   done\n'
        '   if [[ "$RC6_FAKE_CASE" == "rm_failure" ]]; then exit 46; fi\n'
        '   exit 0;;\n'
        'esac\n'
        'exit 97\n', encoding="utf-8",
    )
    binary.chmod(0o700)
    env = {**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
           "RC6_TMPDIR": str(stage), "GITHUB_RUN_ID": "1999",
           "CANDIDATE_SHA": "0123456789012345678901234567890123456789",
           "IMAGE": "porota-predeploy-v2:0123456789012345678901234567890123456789",
           "RC6_FAKE_CASE": case, "FOREIGN_MARKER": str(foreign), "DOCKER_TRACE": str(trace)}
    result = subprocess.run(["bash", "-c", script], env=env, cwd=tmp_path,
                            capture_output=True, text=True, timeout=5)
    calls = trace.read_text(encoding="utf-8").splitlines() if trace.exists() else []
    return result.returncode, result.stdout, calls, foreign.exists()


@pytest.mark.parametrize("case,expect_remove", (
    ("create_failure", False), ("forged_id", False),
    ("wrong_owner", False), ("copy_failure", True),
    ("rm_failure", True), ("healthy", True),
))
def test_extract_container_attests_ownership_before_nonforce_cleanup(
    tmp_path: Path, case: str, expect_remove: bool
) -> None:
    code, stdout, calls, foreign_alive = _extract_app_shell(tmp_path, case)
    assert (code == 0) == (case == "healthy"), (case, code, stdout)
    assert foreign_alive
    assert any(s.startswith("rm ") and s.endswith("c" * 64) for s in calls) == expect_remove
    assert not any(s.startswith("rm -f ") for s in calls)
    assert ("POROTA_EXACT_ARTIFACT_EXTRACT=GREEN" in stdout) == (case == "healthy")


def test_predeploy_uses_one_private_runner_staging_root_not_global_tmp() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    assert "/tmp/porota-" not in source
    data = yaml.safe_load(source)
    steps = data["jobs"]["artifact-gate"]["steps"]
    bootstrap = [s for s in steps if s.get("name") == "Initialize private runner staging"]
    assert len(bootstrap) == 1
    assert "RUNNER_TEMP" in bootstrap[0]["run"]
    assert "mkdir -m 700" in bootstrap[0]["run"]
    assert "GITHUB_ENV" in bootstrap[0]["run"]
    cleanup = _predeploy_cleanup()
    assert "# RC6_A980_PRIVATE_STAGE_BEGIN" in cleanup
    assert "# RC6_A980_PRIVATE_STAGE_END" in cleanup
    assert "rm -rf /tmp" not in cleanup


def test_predeploy_uploads_only_exact_named_evidence_not_writable_directories() -> None:
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = data["jobs"]["artifact-gate"]["steps"]
    main_upload = next(s for s in steps if s.get("name") == "Upload predeploy evidence")
    assert main_upload["if"] == "${{ always() && steps.rc6_artifact_gate.outcome == 'success' }}"
    for name in ("Upload predeploy evidence", "Upload sanitized RCA diagnostic (never promotion input)"):
        step = next(s for s in steps if s.get("name") == name)
        paths = step["with"]["path"].splitlines()
        assert paths
        for path in paths:
            path = path.strip()
            assert path and not path.endswith("/")
            assert "*" not in path and "!" not in path
            assert "${{ runner.temp }}/porota-v2-" in path


def test_predeploy_never_attests_green_before_cleanup_result() -> None:
    steps = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["artifact-gate"]["steps"]
    names = [step.get("name") for step in steps]
    cleanup = names.index("Local cleanup")
    summary = names.index("Predeploy summary")
    assert cleanup < summary
    assert steps[cleanup]["id"] == "rc6_local_cleanup"
    assert steps[summary]["if"] == "${{ success() && steps.rc6_local_cleanup.outcome == 'success' }}"
    assert "POROTA_PREDEPLOY_V2=GREEN" not in "\n".join(
        step.get("run", "") for step in steps[:cleanup]
    )


def _exercise_native_evidence_gate(tmp_path: Path, case: str) -> tuple[int, str]:
    steps = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["artifact-gate"]["steps"]
    gate = next(s["run"] for s in steps if s.get("name") == "Verify required published native evidence")
    proof = tmp_path / "porota-native-sizing-evidence"
    xml_names = (
        "native-bound.xml",
        *(f"session/{name}.xml" for name in (
            "dashboard_auth_52", "publication_io_identity", "scalping_capacity",
            "scheduler_monitor_backup", "signals_risk_exit_ledger",
            "sources_readiness_network", "sql_shadow_wal", "treasury_caucion")),
    )
    xml = '<testsuites><testsuite tests="1"><testcase classname="native" name="valid" /></testsuite></testsuites>'
    for name in xml_names:
        path = proof / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(xml, encoding="utf-8")
    valid_receipt = json.dumps({"status": "GREEN", "production_data_mounted": False})
    for name in ("session/stress.json", "cadence/cadence-fresh.json", "cadence/cadence-legacy.json"):
        path = proof / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(valid_receipt, encoding="utf-8")
    path = proof / "catalog/full-catalog.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"status":"GREEN"}', encoding="utf-8")
    for name in (
        "porota-frozen-candidate.json", "porota-deploy-bundle-v2.tgz",
        "porota-deploy-bundle-v2-manifest.json", "porota-predeploy-image.tar.gz",
        "porota-artifact-manifest.json", "porota-host-control-plane.json",
        "porota-dependency-repro.json", "porota-host-manifest-v2.json",
        "porota-host-policy-v2.json", "porota-governed-tests.xml",
        "porota-test-collection.txt", "porota-test-exclusions.txt",
        "porota-negative-fixtures.json",
    ):
        (tmp_path / name).write_text("proof", encoding="utf-8")
    (tmp_path / "porota-governed-tests.json").write_text(
        json.dumps({"status":"GREEN", "failures":0, "errors":0, "skipped":0, "xfail":0}),
        encoding="utf-8",
    )
    if case == "undeclared":
        (proof / "session/new-native-receipt.json").write_text('{"status":"GREEN"}', encoding="utf-8")
    elif case == "symlink":
        foreign = tmp_path.parent / "foreign-native-receipt.xml"
        foreign.write_text(xml, encoding="utf-8")
        (proof / "native-bound.xml").unlink()
        (proof / "native-bound.xml").symlink_to(foreign)
    elif case == "symlink_parent":
        foreign = tmp_path.parent / "foreign-native-session"
        (proof / "session").rename(foreign)
        (proof / "session").symlink_to(foreign, target_is_directory=True)
    env = {**os.environ, "RC6_TMPDIR": str(tmp_path),
           "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"]}
    done = subprocess.run(["bash", "-c", gate], cwd=WORKFLOW.parents[2],
                          env=env, capture_output=True, text=True, timeout=10)
    return done.returncode, done.stdout + done.stderr


def test_predeploy_native_publish_gate_rejects_omitted_or_aliased_receipt(tmp_path: Path) -> None:
    for case, expected in (
        ("valid", "POROTA_REQUIRED_EVIDENCE=GREEN"),
        ("undeclared", "NATIVE_EVIDENCE_UNDECLARED_FILE"),
        ("symlink", "REQUIRED_PREDEPLOY_EVIDENCE_ALIASED"),
        ("symlink_parent", "REQUIRED_PREDEPLOY_EVIDENCE_ALIASED"),
    ):
        case_root = tmp_path / case
        case_root.mkdir()
        code, output = _exercise_native_evidence_gate(case_root, case)
        assert (code == 0) == (case == "valid"), (case, code, output)
        assert expected in output, (case, output)


def test_predeploy_native_gate_fixture_inherits_python_without_session_path() -> None:
    # This suite must remain portable on the hosted Actions runner as well as
    # a developer's shell; never bake the transient test environment path in.
    source = Path(__file__).read_text(encoding="utf-8")
    assert "/workspace/" + "scratch/" not in source
    assert "Path(sys.executable).parent" in source


def _private_stage_bootstrap() -> str:
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = data["jobs"]["artifact-gate"]["steps"]
    matches = [step for step in steps if step.get("name") == "Initialize private runner staging"]
    assert len(matches) == 1
    return matches[0]["run"]


def _private_stage_env(tmp_path: Path) -> tuple[dict[str, str], Path, Path]:
    runner = tmp_path / "runner"
    runner.mkdir()
    stage = runner / "porota-v2-1999-1"
    github_env = tmp_path / "github-env"
    github_env.write_text("", encoding="utf-8")
    env = {
        **os.environ,
        "RUNNER_TEMP": str(runner),
        "GITHUB_RUN_ID": "1999",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_ENV": str(github_env),
    }
    return env, stage, github_env


def test_private_stage_exclusive_create_rejects_foreign_preexisting_path(tmp_path: Path) -> None:
    env, stage, github_env = _private_stage_env(tmp_path)
    stage.mkdir(mode=0o700)
    sentinel = stage / "FOREIGN_DATA"
    sentinel.write_text("must-not-touch", encoding="utf-8")
    done = subprocess.run(["bash", "-c", _private_stage_bootstrap()],
                          env=env, cwd=tmp_path, capture_output=True, text=True, timeout=5)
    assert done.returncode == 79
    assert "POROTA_PRIVATE_STAGE=RED" in done.stdout
    assert sentinel.read_text(encoding="utf-8") == "must-not-touch"
    assert github_env.read_text(encoding="utf-8") == ""


def test_private_stage_success_requires_mode700_exact_identity(tmp_path: Path) -> None:
    env, stage, github_env = _private_stage_env(tmp_path)
    done = subprocess.run(["bash", "-c", _private_stage_bootstrap()],
                          env=env, cwd=tmp_path, capture_output=True, text=True, timeout=5)
    assert done.returncode == 0, done.stderr
    assert stage.is_dir() and not stage.is_symlink()
    assert stat.S_IMODE(stage.stat().st_mode) == 0o700
    assert "POROTA_PRIVATE_STAGE=GREEN" in done.stdout
    expected_identity = f"{stage.stat().st_dev}:{stage.stat().st_ino}:{os.getuid()}:700"
    assert github_env.read_text(encoding="utf-8") == (
        f"RC6_TMPDIR={stage}\nRC6_TMPDIR_ID={expected_identity}\n"
    )


def _private_stage_cleanup_fragment() -> str:
    cleanup = _predeploy_cleanup()
    return cleanup.split("# RC6_A980_PRIVATE_STAGE_BEGIN", 1)[1].split(
        "# RC6_A980_PRIVATE_STAGE_END", 1)[0]


@pytest.mark.parametrize("case", ("valid", "wrong_identity", "symlink", "missing_identity"))
def test_private_stage_cleanup_never_removes_unverified_or_foreign_path(
    tmp_path: Path, case: str
) -> None:
    env, stage, _ = _private_stage_env(tmp_path)
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    foreign_sentinel = foreign / "keep-me"
    foreign_sentinel.write_text("FOREIGN", encoding="utf-8")
    if case == "symlink":
        stage.symlink_to(foreign, target_is_directory=True)
        stage_id = f"{foreign.stat().st_dev}:{foreign.stat().st_ino}:{os.getuid()}:700"
    else:
        stage.mkdir(mode=0o700)
        stage_id = f"{stage.stat().st_dev}:{stage.stat().st_ino}:{os.getuid()}:700"
        (stage / "owned-only").write_text("POROTA", encoding="utf-8")
    if case == "wrong_identity":
        stage_id = stage_id.replace(":700", ":777")
    elif case == "missing_identity":
        stage_id = ""
    env.update({"RC6_TMPDIR": str(stage), "RC6_TMPDIR_ID": stage_id})
    done = subprocess.run(["bash", "-c", "set -Eeuo pipefail\n" + _private_stage_cleanup_fragment()],
                          env=env, cwd=tmp_path, capture_output=True, text=True, timeout=5)
    assert done.returncode == 0, done.stderr
    assert foreign_sentinel.read_text(encoding="utf-8") == "FOREIGN"
    if case == "valid":
        assert "POROTA_PREDEPLOY_STAGE_CLEANUP=GREEN" in done.stdout
        assert not stage.exists()
    else:
        assert "POROTA_PREDEPLOY_STAGE_CLEANUP=WARN" in done.stdout
        assert stage.exists() or stage.is_symlink()
