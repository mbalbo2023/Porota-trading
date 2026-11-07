"""A875: the historical WS-12 path is a RED-only offhost QA diagnostic.

The tests intentionally describe an isolated branch copy, not the release
workflow or a reusable green certification gate.
"""

from pathlib import Path
import fnmatch
import re
import os
import subprocess
import json

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ".github/workflows/rc6-ws-tests-12-current-contract.yml"
BRANCH = "work/rc6-a872-offhost-qa-20261107"


def workflow():
    raw = (ROOT / WORKFLOW_PATH).read_text(encoding="utf-8")
    # YAML BaseLoader preserves the Actions event key `on` as a string.
    return yaml.load(raw, Loader=yaml.BaseLoader), raw


def test_a875_is_only_push_triggered_by_owned_diagnostic_workflow_path():
    data, _ = workflow()
    assert data["on"] == {"push": {
        "branches": [BRANCH], "paths": [WORKFLOW_PATH]}}
    assert data["permissions"] == {"contents": "read"}
    assert list(data["jobs"]) == ["plan-offhost", "red-only-offhost", "aggregate-red"]
    planner = data["jobs"]["plan-offhost"]
    assert planner["name"] == "PLAN DIAGNOSTIC ONLY (NOT RELEASE)"
    assert planner["runs-on"] == "ubuntu-24.04"
    assert 1 <= int(planner["timeout-minutes"]) <= 6
    assert set(planner["outputs"]) == {
        "planned_batches", "collection_sha256", "ready"}
    job = data["jobs"]["red-only-offhost"]
    assert job["runs-on"] == "ubuntu-24.04"
    assert 1 <= int(job["timeout-minutes"]) <= 16
    assert job["needs"] == "plan-offhost"
    assert job["if"] == ("needs.plan-offhost.result == 'success' && "
                         "needs.plan-offhost.outputs.ready "
                         "== 'bounded_diagnostic_only'")
    strategy = job["strategy"]
    assert strategy["fail-fast"] == "false"
    assert int(strategy["max-parallel"]) == 4
    assert [int(i) for i in strategy["matrix"]["batch_offset"]] == list(range(0, 269, 8))
    reducer = data["jobs"]["aggregate-red"]
    assert reducer["needs"] == ["plan-offhost", "red-only-offhost"]
    assert reducer["if"] == "always()"
    assert 1 <= int(reducer["timeout-minutes"]) <= 16
    assert data["concurrency"]["cancel-in-progress"] == "false"


def test_a875_git_identity_and_no_provider_or_deploy_credentials():
    data, raw = workflow()
    for job_name in ("plan-offhost", "red-only-offhost", "aggregate-red"):
        steps = data["jobs"][job_name]["steps"]
        assert steps[0]["uses"] == "actions/checkout@v4"
        assert steps[0]["with"] == {"persist-credentials": "false", "fetch-depth": "1"}
        identity = next(s["run"] for s in steps
                        if s.get("name", "").startswith("Attest exact"))
        assert 'git rev-parse HEAD' in identity
        assert 'git rev-parse HEAD^{tree}' in identity
        assert '"$GITHUB_SHA"' in identity
        assert 'TREE_SHA=' in identity
        assert 'git status --porcelain' in identity
    assert "secrets." not in raw
    assert "id-token:" not in raw
    assert "contents: write" not in raw
    assert "packages: write" not in raw
    assert not any(re.search(r"\bdocker\s+(run|build|compose|system|image|container)\b",
                             line.lower()) for line in raw.splitlines()
                   if not line.lstrip().startswith("#"))
    # The only privileged process allowed is constructing a namespace on the
    # disposable Actions VM. Tests must drop UID, GID and all docker groups.
    for line in raw.splitlines():
        if re.search(r"\bsudo\b", line) and not line.lstrip().startswith("#"):
            assert any(allowed in line for allowed in (
                "sudo -n unshare --net", "sudo -n true", "command -v sudo"))
    assert "ssh " not in raw.lower()


def test_a875_network_namespace_fails_closed_with_probe_and_pytest_in_same_child():
    data, _ = workflow()
    steps = data["jobs"]["red-only-offhost"]["steps"]
    run_index = next(i for i, s in enumerate(steps)
                     if s.get("name") == "Run isolated bounded hash-only diagnostic")
    diagnostic = steps[run_index]["run"]
    assert "unshare --user --map-current-user --net --" in diagnostic
    assert "sudo -n unshare --net --" in diagnostic
    assert "setpriv --reuid" in diagnostic
    assert "--regid" in diagnostic
    assert "--clear-groups --no-new-privs --" in diagnostic
    assert "bash -Eeuo pipefail <<'ISOLATED_QA'" in diagnostic
    assert '"${namespace[@]}" env -i' in diagnostic
    assert "setpriv --no-new-privs -- bash -Eeuo pipefail" not in diagnostic
    assert "NoNewPrivs" in diagnostic
    assert "Groups:" in diagnostic
    assert "docker.sock" in diagnostic and "containerd.sock" in diagnostic
    assert "podman.sock" in diagnostic
    assert "AF_UNIX" in diagnostic
    assert "sock.settimeout(1)" in diagnostic
    assert "sudo -n true" in diagnostic
    assert "nsenter -t 1 -n -- true" in diagnostic
    assert diagnostic.index("NoNewPrivs") < diagnostic.index("socket.create_connection")
    assert diagnostic.index("nsenter -t 1 -n -- true") < diagnostic.index(
        "python -m scripts.rc6_ci_parallel_runner")
    for required in ('"PATH=$PATH"', '"HOME=$HOME"', '"GITHUB_SHA=$GITHUB_SHA"',
                     '"TREE_SHA=$TREE_SHA"', '"RUNNER_TEMP=$RUNNER_TEMP"'):
        assert required in diagnostic
    assert "socket.create_connection" in diagnostic
    assert "errno.ENETUNREACH" in diagnostic
    assert "errno.ENETDOWN" in diagnostic
    assert "errno.EHOSTUNREACH" in diagnostic
    assert diagnostic.index("socket.create_connection") < diagnostic.index(
        "python -m scripts.rc6_ci_parallel_runner")
    assert "NETWORK_ISOLATION=RED" in diagnostic
    assert "NETWORK_ISOLATION=GREEN" in diagnostic
    assert '"$(id -u)"' in diagnostic
    assert "exit 79" in diagnostic
    assert "python -m scripts.rc6_ci_parallel_runner" in diagnostic
    assert "--source-sha \"$GITHUB_SHA\"" in diagnostic
    assert "--source-tree-sha \"$TREE_SHA\"" in diagnostic
    assert "--bounded-shard-index 1" in diagnostic
    assert "--shards 2" in diagnostic
    assert "--batch-limit 8" in diagnostic
    assert '--batch-offset "${{ matrix.batch_offset }}"' in diagnostic
    assert "--timeout-seconds 90" in diagnostic
    assert "--diagnostics-dir" in diagnostic
    assert "--output-junit" not in diagnostic


def test_a883_preflight_collects_same_sha_inside_offline_namespace_before_matrix():
    data, _ = workflow()
    steps = data["jobs"]["plan-offhost"]["steps"]
    plan = next(s for s in steps
                if s.get("name") == "Plan exact shard1 inventory in isolated namespace")
    assert plan["id"] == "plan"
    run = plan["run"]
    assert "unshare --user --map-current-user --net --" in run
    assert "sudo -n unshare --net --" in run
    assert "setpriv --reuid" in run
    assert "--regid" in run
    assert "--clear-groups --no-new-privs --" in run
    assert '"${namespace[@]}" env -i' in run
    assert "setpriv --no-new-privs -- bash -Eeuo pipefail" not in run
    assert "NoNewPrivs" in run
    assert "Groups:" in run
    assert "docker.sock" in run and "containerd.sock" in run
    assert "podman.sock" in run
    assert "AF_UNIX" in run
    assert "sock.settimeout(1)" in run
    assert "sudo -n true" in run
    assert "nsenter -t 1 -n -- true" in run
    assert run.index("NoNewPrivs") < run.index("socket.create_connection")
    assert run.index("nsenter -t 1 -n -- true") < run.index(
        "python -m scripts.rc6_ci_parallel_runner")
    assert "socket.create_connection" in run
    assert run.index("socket.create_connection") < run.index(
        "python -m scripts.rc6_ci_parallel_runner")
    assert "NETWORK_ISOLATION=GREEN" in run and "NETWORK_ISOLATION=RED" in run
    assert "--bounded-shard-index 1" in run
    assert "--batch-offset 0 --batch-limit 1" in run
    assert "--timeout-seconds 90" in run
    assert "GITHUB_OUTPUT" in run
    assert "265 <= planned <= 272" in run
    assert "bounded_diagnostic_only" in run
    assert "exit 79" in run
    assert steps[-1]["if"] == "always()"
    assert "AUTOMATIC_GO=NO" in steps[-1]["run"]
    assert "exit 1" not in steps[-1]["run"]


def test_a875_only_sanitized_receipts_and_never_reports_green():
    data, raw = workflow()
    steps = data["jobs"]["red-only-offhost"]["steps"]
    diag = next(s["run"] for s in steps
                if s.get("name") == "Run isolated bounded hash-only diagnostic")
    assert 'if [ "$rc" -ne 2 ]; then' in diag
    assert 'receipt["planned_batches"] == int(os.environ["A883_PLAN_N"])' in diag
    assert 'receipt["collection_sha256"] == os.environ["A883_PLAN_COLLECTION"]' in diag
    assert 'automatic_go' in diag
    assert 'release_gate_activated' in diag
    assert 'runtime_authority' in diag
    assert '"$GITHUB_SHA"' in diag
    assert '"$TREE_SHA"' in diag
    upload = next(s for s in steps if s.get("uses") == "actions/upload-artifact@v4")
    assert upload["if"] == "always()"
    assert upload["with"]["path"] == "${{ runner.temp }}/rc6-a883-sanitized/"
    assert upload["with"]["name"] == "rc6-a883-shard1-${{ matrix.batch_offset }}-${{ github.sha }}"
    assert int(upload["with"]["retention-days"]) <= 7
    assert upload["with"]["if-no-files-found"] == "error"
    assert steps[-1]["if"] == "always()"
    assert "AUTOMATIC_GO=NO" in steps[-1]["run"]
    assert "exit 1" in steps[-1]["run"]
    aggregate_steps = data["jobs"]["aggregate-red"]["steps"]
    parser_install = next(s["run"] for s in aggregate_steps
                          if s.get("name") == "Install only pinned reducer parser")
    assert "python -m pip install" in parser_install
    assert "-c requirements.lock.txt PyYAML" in parser_install
    download = next(s for s in aggregate_steps
                    if s.get("uses") == "actions/download-artifact@v4")
    assert download["with"]["pattern"] == "rc6-a883-shard1-*-${{ github.sha }}"
    assert download["with"]["merge-multiple"] == "false"
    aggregator = next(s["run"] for s in aggregate_steps
                      if s.get("name") == "Verify 34 bounded matrix receipts RED-only")
    assert "python -m scripts.rc6_ci_bounded_matrix_quorum" in aggregator
    assert '"$GITHUB_SHA"' in aggregator and '"$TREE_SHA"' in aggregator
    assert "--expected-batches" in aggregator
    assert "--expected-collection-sha256" in aggregator
    assert 'if [ "$rc" -ne 2 ]; then' in aggregator
    assert aggregate_steps[-1]["if"] == "always()"
    assert "AUTOMATIC_GO=NO" in aggregate_steps[-1]["run"]
    assert "exit 1" in aggregate_steps[-1]["run"]
    assert "continue-on-error" not in raw
    assert "AUTOMATIC_GO=YES" not in raw


def test_a875_all_shell_steps_parse_without_running_commands():
    data, _ = workflow()
    for job in data["jobs"].values():
        for step in job["steps"]:
            if "run" in step:
                result = subprocess.run(["bash", "-n"], input=step["run"],
                                        text=True, capture_output=True, check=False)
                assert result.returncode == 0, (step["name"], result.stderr)


def test_a875_namespace_unavailable_blocks_before_running_any_python(tmp_path):
    data, _ = workflow()
    script = next(s["run"] for s in data["jobs"]["red-only-offhost"]["steps"]
                  if s.get("name") == "Run isolated bounded hash-only diagnostic")
    # All command doubles are confined to this test shell and no runner/test
    # module is imported. The named fallback cannot silently proceed.
    fakes = ("unshare() { return 1; }\n"
             "sudo() { return 1; }\n"
             "python() { echo PYTHON_EXECUTED; return 0; }\n")
    result = subprocess.run(["bash", "-c", fakes + script], cwd=ROOT,
                            env={"RUNNER_TEMP": str(tmp_path),
                                 "PATH": os.environ.get("PATH", "/usr/bin:/bin")},
                            capture_output=True, text=True, check=False, timeout=5)
    assert result.returncode == 79
    assert "NETWORK_ISOLATION=RED" in result.stdout
    assert "PYTHON_EXECUTED" not in result.stdout


def test_a883_planner_namespace_unavailable_blocks_before_pytest(tmp_path):
    data, _ = workflow()
    script = next(s["run"] for s in data["jobs"]["plan-offhost"]["steps"]
                  if s.get("name") == "Plan exact shard1 inventory in isolated namespace")
    fakes = ("unshare() { return 1; }\n"
             "sudo() { return 1; }\n"
             "python() { echo PYTHON_EXECUTED; return 0; }\n")
    result = subprocess.run(["bash", "-c", fakes + script], cwd=ROOT,
                            env={"RUNNER_TEMP": str(tmp_path),
                                 "PATH": os.environ.get("PATH", "/usr/bin:/bin")},
                            capture_output=True, text=True, check=False, timeout=5)
    assert result.returncode == 79
    assert "NETWORK_ISOLATION=RED" in result.stdout
    assert "PYTHON_EXECUTED" not in result.stdout


def test_a883_missing_nnp_setpriv_blocks_planner_and_batches(tmp_path):
    """Simulated successful unshare cannot bypass a failed NNP setup."""
    data, _ = workflow()
    fakes = tmp_path / "bin"
    fakes.mkdir()
    unshare = fakes / "unshare"
    unshare.write_text("#!/bin/sh\n"
        "while [ \"$1\" != -- ]; do shift; done\n"
        "shift\n\"$@\"\n", encoding="utf-8")
    unshare.chmod(0o755)
    setpriv = fakes / "setpriv"
    setpriv.write_text("#!/bin/sh\necho NNP_UNAVAILABLE\nexit 79\n", encoding="utf-8")
    setpriv.chmod(0o755)
    env = {"PATH": f"{fakes}:{os.environ.get('PATH', '/usr/bin:/bin')}",
           "HOME": str(tmp_path),
           "RUNNER_TEMP": str(tmp_path), "GITHUB_OUTPUT": str(tmp_path / "outputs"),
           "GITHUB_SHA": "a" * 40, "TREE_SHA": "b" * 40,
           "A883_PLAN_N": "269", "A883_PLAN_COLLECTION": "c" * 64}
    for job_name, step_name in (("plan-offhost",
                                 "Plan exact shard1 inventory in isolated namespace"),
                                ("red-only-offhost",
                                 "Run isolated bounded hash-only diagnostic")):
        script = next(s["run"] for s in data["jobs"][job_name]["steps"]
                      if s.get("name") == step_name)
        result = subprocess.run(["bash", "-c", script], cwd=ROOT, env=env,
                                capture_output=True, text=True, check=False, timeout=5)
        assert result.returncode == 79, (job_name, result.stdout, result.stderr)
        assert "NAMESPACE_UNAVAILABLE" in result.stdout
        assert "NETWORK_ISOLATION=GREEN" not in result.stdout
        assert "PYTHON_EXECUTED" not in result.stdout


def test_a883_local_nnp_process_attests_linux_kernel_bit():
    process = subprocess.run(["setpriv", "--no-new-privs", "--", "sh", "-c",
                              "grep '^NoNewPrivs:' /proc/self/status"],
                             capture_output=True, text=True, check=False, timeout=5)
    assert process.returncode == 0
    assert process.stdout.strip().split() == ["NoNewPrivs:", "1"]


@pytest.mark.parametrize("scenario,reason", [
    ("writable", "HOST_UNIX_SOCKET_WRITABLE"),
    ("connectable", "HOST_SOCKET_CONNECTABLE")])
def test_a883_host_unix_socket_access_is_fail_closed_for_both_jobs(scenario, reason):
    """Exercise exact YAML guard with simulated daemon, no actual socket/network."""
    data, _ = workflow()
    for job_name, step_name in (("plan-offhost",
                                 "Plan exact shard1 inventory in isolated namespace"),
                                ("red-only-offhost",
                                 "Run isolated bounded hash-only diagnostic")):
        script = next(s["run"] for s in data["jobs"][job_name]["steps"]
                      if s.get("name") == step_name)
        block = script.split("python - <<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
        assert block.startswith("import errno, os, socket, stat\n")
        start = block.index("paths = [")
        end = block.index("\nfor path in paths:", start)
        stub = ("\nclass FakeInfo: st_mode = stat.S_IFSOCK | 0o777\n"
                "os.stat = lambda path: FakeInfo()\n"
                f"os.access = lambda path, mode: {scenario == 'writable'}\n"
                "class FakeSock:\n"
                "    def settimeout(self, timeout): pass\n"
                "    def connect(self, path): pass\n"
                "    def close(self): pass\n"
                "socket.socket = lambda *args: FakeSock()\n")
        block = (block[:start] + "paths = [" + json.dumps("/emulated/daemon.sock") +
                 "]" + stub + block[end:])
        result = subprocess.run(["python", "-c", block], cwd=ROOT,
                                capture_output=True, text=True, check=False,
                                timeout=5)
        assert result.returncode == 79, (job_name, result.stdout, result.stderr)
        assert reason in result.stdout


def test_a875_push_has_no_other_matching_workflow_in_current_tree():
    """All workflow trigger files, not merely the edited one, are in scope."""
    changed = [WORKFLOW_PATH, "tests/test_rc6_a875_offhost_diagnostic_workflow.py",
               "scripts/rc6_ci_bounded_matrix_quorum.py",
               "tests/test_rc6_ci_bounded_matrix_quorum.py"]
    matches = []
    for path in sorted((ROOT / ".github/workflows").glob("*.y*ml")):
        data = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader) or {}
        events = data.get("on") or {}
        if isinstance(events, str):
            events = {events: {}}
        if isinstance(events, list):
            events = {event: {} for event in events}
        assert isinstance(events, dict), path
        assert "workflow_run" not in events, path  # No automatic post-run host jobs.
        if "push" not in events:
            continue
        trigger = events["push"] or {}
        assert isinstance(trigger, dict), path
        branches = trigger.get("branches", ["**"])
        if isinstance(branches, str):
            branches = [branches]
        if (not any(fnmatch.fnmatchcase(BRANCH, expression) for expression in branches
                    if not expression.startswith("!")) or
                any(fnmatch.fnmatchcase(BRANCH, expression[1:]) for expression in branches
                    if expression.startswith("!"))):
            continue
        ignored_branches = trigger.get("branches-ignore", [])
        if isinstance(ignored_branches, str):
            ignored_branches = [ignored_branches]
        if any(fnmatch.fnmatchcase(BRANCH, expression) for expression in ignored_branches):
            continue
        include = trigger.get("paths", [])
        if isinstance(include, str):
            include = [include]
        if include and not any(fnmatch.fnmatchcase(name, expr)
                               for name in changed for expr in include):
            continue
        ignore = trigger.get("paths-ignore", [])
        if isinstance(ignore, str):
            ignore = [ignore]
        if ignore and all(any(fnmatch.fnmatchcase(name, expr)
                              for expr in ignore) for name in changed):
            continue
        matches.append(path.relative_to(ROOT).as_posix())
    assert matches == [WORKFLOW_PATH]
