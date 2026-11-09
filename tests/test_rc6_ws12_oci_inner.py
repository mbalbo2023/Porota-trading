"""Trusted-main OCI inner harness: real scopes, private execution, no shell."""

from pathlib import Path

import pytest

from scripts import rc6_ci_ws12_oci_inner as inner


SHA = "a" * 40
TREE = "b" * 40


@pytest.fixture
def source(tmp_path):
    repo = tmp_path / "source"
    (repo / "ops/policy").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "test_a3_primary_readonly_hf6.py").write_text("def test_legacy(): pass\n")
    (repo / "tests/test_a3_primary_readonly_hf6.py").write_text("def test_successor(): pass\n")
    (repo / "ops/policy/test-policy.yaml").write_text("""
test_governance:
  automatic_discovery_required: true
  manual_test_file_allowlist_as_primary_ci: false
productive_scope:
  runner: pytest
  roots: [.]
  exclusions:
    - path: test_a3_primary_readonly_hf6.py
      classification: SUPERSEDED_DUPLICATE_MODULE
      reason: duplicate import-file conflict
      successor: tests/test_a3_primary_readonly_hf6.py
      owner: WS-INFRA-05
""")
    return repo


def test_full_and_subset_scopes_keep_automatic_root_discovery(source):
    assert inner.productive_args(source) == [".", "--ignore=test_a3_primary_readonly_hf6.py"]
    assert inner.SUBSET_ARGS == ["tests/"]
    steps = inner.plan(SHA, TREE, source, Path("/work/evidence"), "python3")
    assert [step.phase for step in steps] == ["collect_full", "collect_subset", "execute_full"]
    assert "--collect-only" in steps[0].argv
    assert "--collect-only" in steps[1].argv
    assert "--collect-only" not in steps[2].argv
    assert "--ignore=test_a3_primary_readonly_hf6.py" in steps[2].argv
    assert "tests/" in steps[1].argv
    assert "--junitxml=/work/evidence/junit.xml" in steps[2].argv
    assert "-I" in steps[2].argv
    assert "/control/scripts/rc6_ci_ws12_trusted_pytest_launch.py" in steps[2].argv
    assert "-p" not in steps[2].argv
    assert "--runxfail" not in steps[2].argv


@pytest.mark.parametrize("policy_change", [
    lambda p: p.replace("automatic_discovery_required: true", "automatic_discovery_required: false"),
    lambda p: p.replace("roots: [.]", "roots: [tests/]"),
    lambda p: p.replace("test_a3_primary_readonly_hf6.py\n      classification", "../private.py\n      classification"),
    lambda p: p.replace("owner: WS-INFRA-05", "owner: ''"),
    lambda p: p.replace("successor: tests/test_a3_primary_readonly_hf6.py", "successor: tests/missing.py"),
])
def test_policy_drift_is_hold_before_candidate_execution(source, policy_change):
    path = source / "ops/policy/test-policy.yaml"
    path.write_text(policy_change(path.read_text()))
    with pytest.raises(inner.InnerHold):
        inner.productive_args(source)


def test_environment_sanitizer_never_forwards_actions_tokens(source):
    inherited = {"GITHUB_TOKEN": "SYNTHETIC_SECRET", "ACTIONS_RUNTIME_TOKEN": "SECRET2",
                 "PYTEST_ADDOPTS": "--runxfail", "PYTEST_PLUGINS": "malicious", "PATH": "/evil"}
    out = inner.pytest_env(SHA, TREE, "/work/evidence/manifest.json", inherited)
    assert set(out) == {"PATH", "LC_ALL", "HOME", "PYTHONDONTWRITEBYTECODE",
                        "PYTEST_DISABLE_PLUGIN_AUTOLOAD", "RC6_CI_IDENTITY_PROOF",
                        "RC6_CI_SOURCE_SHA", "RC6_CI_SOURCE_TREE_SHA", "RC6_CI_MANIFEST_PATH"}
    assert out["PATH"] == "/usr/local/bin:/usr/bin:/bin"
    assert "SECRET" not in repr(out)
    assert out["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"


def test_failed_inner_step_does_not_invoke_later_tests(source, tmp_path):
    calls = []
    def fail_first(step, cwd, env, timeout):
        calls.append(step.phase)
        return 1
    result = inner.run(source, tmp_path / "out", SHA, TREE,
                       python="python3", invoke=fail_first)
    assert result == 79
    assert calls == ["collect_full"]
    assert (tmp_path / "out").is_dir()


def test_successful_invocations_still_do_not_issue_release_certificate(source, tmp_path):
    calls = []
    def fake_success(step, cwd, env, timeout):
        calls.append(step.phase)
        return 0
    result = inner.run(source, tmp_path / "out", SHA, TREE,
                       python="python3", invoke=fake_success)
    assert result == 0
    assert calls == ["collect_full", "collect_subset", "execute_full"]
    assert not (tmp_path / "out" / "attestation.json").exists()
