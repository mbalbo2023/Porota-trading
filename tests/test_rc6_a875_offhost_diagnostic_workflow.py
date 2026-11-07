"""A875: the historical WS-12 path is a RED-only offhost QA diagnostic.

The tests intentionally describe an isolated branch copy, not the release
workflow or a reusable green certification gate.
"""

from pathlib import Path
import fnmatch
import re
import os
import subprocess

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
    assert list(data["jobs"]) == ["red-only-offhost"]
    job = data["jobs"]["red-only-offhost"]
    assert job["runs-on"] == "ubuntu-24.04"
    assert 1 <= int(job["timeout-minutes"]) <= 25
    assert data["concurrency"]["cancel-in-progress"] == "false"


def test_a875_git_identity_and_no_provider_or_deploy_credentials():
    data, raw = workflow()
    job = data["jobs"]["red-only-offhost"]
    steps = job["steps"]
    assert steps[0]["uses"] == "actions/checkout@v4"
    assert steps[0]["with"] == {"persist-credentials": "false", "fetch-depth": "1"}
    identity = next(s["run"] for s in steps if s.get("name") == "Attest exact checkout")
    assert 'git rev-parse HEAD' in identity
    assert 'git rev-parse HEAD^{tree}' in identity
    assert '"$GITHUB_SHA"' in identity
    assert 'TREE_SHA=' in identity
    assert 'git status --porcelain' in identity
    assert "secrets." not in raw
    assert "id-token:" not in raw
    assert "contents: write" not in raw
    assert "packages: write" not in raw
    assert "docker " not in raw.lower()
    # The only privileged process allowed is creating a namespace on the
    # disposable Actions VM.  pytest must be dropped back to runner UID.
    assert not re.search(r"sudo\s+(?!-n\s+unshare\s+--net)", raw)
    assert "ssh " not in raw.lower()


def test_a875_network_namespace_fails_closed_with_probe_and_pytest_in_same_child():
    data, _ = workflow()
    steps = data["jobs"]["red-only-offhost"]["steps"]
    run_index = next(i for i, s in enumerate(steps)
                     if s.get("name") == "Run isolated bounded hash-only diagnostic")
    diagnostic = steps[run_index]["run"]
    assert "unshare --user --map-current-user --net --" in diagnostic
    assert "sudo -n unshare --net --" in diagnostic
    assert "runuser --preserve-environment -u" in diagnostic
    assert "bash -Eeuo pipefail <<'ISOLATED_QA'" in diagnostic
    assert '"${namespace[@]}" env -i' in diagnostic
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
    assert "--timeout-seconds 90" in diagnostic
    assert "--diagnostics-dir" in diagnostic
    assert "--output-junit" not in diagnostic


def test_a875_only_sanitized_receipts_and_never_reports_green():
    data, raw = workflow()
    steps = data["jobs"]["red-only-offhost"]["steps"]
    diag = next(s["run"] for s in steps
                if s.get("name") == "Run isolated bounded hash-only diagnostic")
    assert 'if [ "$rc" -ne 2 ]; then' in diag
    assert 'automatic_go' in diag
    assert 'release_gate_activated' in diag
    assert 'runtime_authority' in diag
    assert '"$GITHUB_SHA"' in diag
    assert '"$TREE_SHA"' in diag
    upload = next(s for s in steps if s.get("uses") == "actions/upload-artifact@v4")
    assert upload["if"] == "always()"
    assert upload["with"]["path"] == "${{ runner.temp }}/rc6-a875-sanitized/"
    assert int(upload["with"]["retention-days"]) <= 7
    assert upload["with"]["if-no-files-found"] == "error"
    assert steps[-1]["if"] == "always()"
    assert "AUTOMATIC_GO=NO" in steps[-1]["run"]
    assert "exit 1" in steps[-1]["run"]
    assert "continue-on-error" not in raw
    assert "AUTOMATIC_GO=YES" not in raw


def test_a875_all_shell_steps_parse_without_running_commands():
    data, _ = workflow()
    for step in data["jobs"]["red-only-offhost"]["steps"]:
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


def test_a875_push_has_no_other_matching_workflow_in_current_tree():
    """All workflow trigger files, not merely the edited one, are in scope."""
    changed = [WORKFLOW_PATH, "tests/test_rc6_a875_offhost_diagnostic_workflow.py"]
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
