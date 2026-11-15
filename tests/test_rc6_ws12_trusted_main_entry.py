"""A1004: main-only WS12 entry must not self-certify candidate QA."""

from pathlib import Path
import importlib.util
import json
import subprocess

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "scripts/rc6_ci_ws12_main_entry.py"
WORKFLOW = ROOT / ".github/workflows/rc6-ws-tests-12-current-contract.yml"


def entry_module():
    spec = importlib.util.spec_from_file_location("rc6_ws12_main_entry", ENTRY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def valid_context():
    return {
        "GITHUB_REPOSITORY": "mbalbo2023/Porota-trading",
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_REF_PROTECTED": "true",
        "GITHUB_WORKFLOW_REF": (
            "mbalbo2023/Porota-trading/.github/workflows/"
            "rc6-ws-tests-12-current-contract.yml@refs/heads/main"
        ),
        "GITHUB_WORKFLOW_SHA": "a" * 40,
        "GITHUB_SHA": "a" * 40,
        "RC6_CANDIDATE_SHA": "b" * 40,
        "RC6_CANDIDATE_TREE_SHA": "c" * 40,
        "RUNNER_ENVIRONMENT": "github-hosted",
        "GITHUB_RUN_ID": "123456789",
        "GITHUB_RUN_ATTEMPT": "1",
    }


def test_main_dispatch_is_accepted_only_as_hold(capsys):
    result = entry_module().main(valid_context())
    assert result == 79
    assert capsys.readouterr() == (
        "RC6_WS12_CONTROL=MAIN_CONTEXT_VERIFIED_HOLD_NO_OCI|"
        "qa_certified=false|automatic_go=false\n", ""
    )


@pytest.mark.parametrize("change", [
    {"GITHUB_REPOSITORY": "other/repo"},
    {"GITHUB_EVENT_NAME": "pull_request"},
    {"GITHUB_REF": "refs/heads/candidate"},
    {"GITHUB_REF_PROTECTED": "false"},
    {"GITHUB_REF_PROTECTED": ""},
    {"GITHUB_REF_PROTECTED": "TRUE"},
    {"GITHUB_WORKFLOW_REF": "mbalbo2023/Porota-trading/.github/workflows/rc6-ws-tests-12-current-contract.yml@refs/heads/feature"},
    {"GITHUB_WORKFLOW_SHA": "d" * 40},
    {"RUNNER_ENVIRONMENT": "self-hosted"},
    {"GITHUB_RUN_ID": "0"},
    {"GITHUB_RUN_ATTEMPT": "0"},
    {"RC6_CANDIDATE_SHA": "main"},
    {"RC6_CANDIDATE_TREE_SHA": ""},
    {"RC6_CANDIDATE_SHA": "a" * 40},
])
def test_forged_or_incomplete_context_rejected_without_leaking_values(change, capsys):
    context = valid_context()
    context.update(change)
    context["GITHUB_TOKEN"] = "SYNTHETIC_SECRET"
    result = entry_module().main(context)
    assert result == 79
    out = capsys.readouterr()
    assert out == (
        "RC6_WS12_CONTROL=UNTRUSTED_CONTEXT|qa_certified=false|"
        "automatic_go=false\n", ""
    )
    assert "SYNTHETIC_SECRET" not in out[0]




@pytest.mark.parametrize("protected", ["false", "", "TRUE", None])
def test_entry_rejects_unprotected_ref_without_runtime_access(protected, capsys):
    good_env = valid_context()
    assert entry_module().main(good_env) == 79
    assert "MAIN_CONTEXT_VERIFIED_HOLD_NO_OCI" in capsys.readouterr().out
    bad_env = dict(good_env)
    if protected is None:
        bad_env.pop("GITHUB_REF_PROTECTED")
    else:
        bad_env["GITHUB_REF_PROTECTED"] = protected
    assert entry_module().main(bad_env) == 79
    assert capsys.readouterr() == (
        "RC6_WS12_CONTROL=UNTRUSTED_CONTEXT|qa_certified=false|automatic_go=false\n", ""
    )
