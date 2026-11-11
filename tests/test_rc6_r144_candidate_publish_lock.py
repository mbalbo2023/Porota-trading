"""R144: source-level GitHub job gate, no dispatch or Docker execution."""

from pathlib import Path
import json
import math
import re

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/porota-predeploy-v2.yml"
CANDIDATE_REF = "integration/rc6-a1115-r87-unified-paper-candidate-20261110"

_OLD_DRAFT_ONLY = re.compile(
    r"^\$\{\{\s*!github\.event\.pull_request\.draft\s*\}\}$"
)
_EXACT_PUBLISH_LOCK = re.compile(
    r"^\$\{\{\s*github\.event_name\s*==\s*'pull_request'\s*&&\s*"
    r"!github\.event\.pull_request\.draft\s*&&\s*"
    r"github\.event\.pull_request\.head\.ref\s*!=\s*'(?P<ref>[^']+)'\s*\}\}$"
)
_STRICT_PUBLISH_LOCK = re.compile(
    r"^\$\{\{\s*github\.event_name\s*==\s*'pull_request'\s*&&\s*"
    r"toJSON\(github\.event\.pull_request\.draft\)\s*==\s*'false'\s*&&\s*"
    r"toJSON\(github\.event\.pull_request\.head\.ref\)\s*!=\s*'null'\s*&&\s*"
    r"github\.event\.pull_request\.head\.ref\s*!=\s*''\s*&&\s*"
    r"toJSON\(github\.event\.pull_request\.head\.sha\)\s*!=\s*'null'\s*&&\s*"
    r"github\.event\.pull_request\.head\.sha\s*!=\s*''\s*&&\s*"
    r"github\.event\.pull_request\.number\s*>\s*0\s*&&\s*"
    r"github\.event\.pull_request\.head\.ref\s*!=\s*'(?P<ref>[^']+)'\s*\}\}$"
)
_HARDENED_PUBLISH_LOCK = re.compile(
    r"^\$\{\{\s*github\.event_name\s*==\s*'pull_request'\s*&&\s*"
    r"toJSON\(github\.event\.pull_request\.draft\)\s*==\s*'false'\s*&&\s*"
    r"startsWith\(toJSON\(github\.event\.pull_request\.head\.ref\),\s*'\"'\)\s*&&\s*"
    r"github\.event\.pull_request\.head\.ref\s*!=\s*''\s*&&\s*"
    r"startsWith\(toJSON\(github\.event\.pull_request\.head\.sha\),\s*'\"'\)\s*&&\s*"
    r"github\.event\.pull_request\.head\.sha\s*!=\s*''\s*&&\s*"
    r"toJSON\(github\.event\.pull_request\.number\)\s*>\s*0\s*&&\s*"
    r"!contains\(toJSON\(github\.event\.pull_request\.number\),\s*'\.'\)\s*&&\s*"
    r"!contains\(toJSON\(github\.event\.pull_request\.number\),\s*'e'\)\s*&&\s*"
    r"github\.event\.pull_request\.number\s*!=\s*1087\s*&&\s*"
    r"github\.event\.pull_request\.head\.ref\s*!=\s*'(?P<ref>[^']+)'\s*\}\}$"
)


def _source():
    # BaseLoader preserves GitHub's literal `on` key (unlike YAML 1.1 booleans).
    return yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def _gh_positive_number(value):
    """Model the old `number > 0` loose numeric coercion for our inputs."""
    if value is None:
        return False
    if type(value) is str:
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return False
    return type(value) in (bool, int, float) and value > 0


def _gh_not_candidate(value):
    return type(value) is not str or value.casefold() != CANDIDATE_REF.casefold()


def _gate_allows(*, event_name, ref, draft, sha, number):
    """Limited semantics for reviewed expressions; not a GitHub Actions run."""
    expression = _source()["jobs"]["artifact-gate"]["if"]
    if _OLD_DRAFT_ONLY.fullmatch(expression):
        return not draft
    guarded = _EXACT_PUBLISH_LOCK.fullmatch(expression)
    if guarded is not None:
        assert guarded.group("ref") == CANDIDATE_REF
        # GitHub loosely compares missing ref (null) != this nonnumeric string.
        return (event_name.casefold() == "pull_request" and not draft and
                (ref is None or ref.casefold() != guarded.group("ref").casefold()))
    guarded = _STRICT_PUBLISH_LOCK.fullmatch(expression)
    if guarded is not None:
        assert guarded.group("ref") == CANDIDATE_REF
        return (event_name.casefold() == "pull_request" and type(draft) is bool and
                draft is False and ref is not None and ref != "" and
                sha is not None and sha != "" and _gh_positive_number(number) and
                _gh_not_candidate(ref))
    guarded = _HARDENED_PUBLISH_LOCK.fullmatch(expression)
    assert guarded is not None, "unknown job expression; require independent review"
    assert guarded.group("ref") == CANDIDATE_REF
    pr_number_is_json_integer = (type(number) is int or
                                 (type(number) is float and math.isfinite(number) and
                                  number.is_integer()))
    return (event_name.casefold() == "pull_request" and type(draft) is bool and
            draft is False and type(ref) is str and ref != "" and
            type(sha) is str and sha != "" and pr_number_is_json_integer and
            number > 0 and number != 1087 and _gh_not_candidate(ref))


@pytest.mark.parametrize("ref,draft,event_name,expected", [
    (CANDIDATE_REF, False, "pull_request", False),
    (CANDIDATE_REF, True, "pull_request", False),
    (CANDIDATE_REF.upper(), False, "pull_request", False),
    (CANDIDATE_REF.replace("candidate", "CaNdIdAtE"), False, "pull_request", False),
    (CANDIDATE_REF + "-other", False, "pull_request", True),
    ("feature/unrelated-safe-pr", False, "pull_request", True),
    ("feature/unrelated-safe-pr", True, "pull_request", False),
    ("feature/unrelated-safe-pr", False, "push", False),
])
def test_candidate_publish_lock_truth_table(ref, draft, event_name, expected):
    assert _gate_allows(ref=ref, draft=draft, event_name=event_name,
                        sha="a" * 40, number=1099) is expected


@pytest.mark.parametrize("case,fields", [
    ("missing_draft", {"draft": None}),
    ("string_draft", {"draft": "false"}),
    ("numeric_draft", {"draft": 0}),
    ("missing_ref", {"ref": None}),
    ("empty_ref", {"ref": ""}),
    ("missing_sha", {"sha": None}),
    ("empty_sha", {"sha": ""}),
    ("missing_pr_number", {"number": None}),
    ("zero_pr_number", {"number": 0}),
    ("numeric_ref", {"ref": 17}),
    ("numeric_sha", {"sha": 17}),
    ("boolean_pr_number", {"number": True}),
    ("string_pr_number", {"number": "1099"}),
    ("fractional_pr_number", {"number": 1099.5}),
])
def test_candidate_publish_lock_rejects_malformed_pull_request(case, fields):
    payload = {"event_name": "pull_request", "draft": False,
               "ref": "feature/unrelated-safe-pr",
               "sha": "a" * 40, "number": 1099}
    payload.update(fields)
    assert _gate_allows(**payload) is False, case


def test_canonical_pr_number_remains_held_after_branch_rename():
    assert _gate_allows(event_name="pull_request", draft=False,
                        ref="feature/renamed-candidate-branch", sha="a" * 40,
                        number=1087) is False


def test_publish_lock_preserves_only_pull_request_trigger_and_fail_closed_native_owner():
    workflow = _source()
    assert list(workflow["on"]) == ["pull_request"]
    # Required offhost release evidence must be non-skippable when R144
    # intentionally skips the original candidate job.
    assert list(workflow["jobs"]) == ["artifact-gate", "release-evidence-required"]
    steps = workflow["jobs"]["artifact-gate"]["steps"]
    native = next(s for s in steps if s.get("name") ==
                  "Exact image native sizing and generated catalog proof")
    cleanup = next(s for s in steps if s.get("name") == "Local cleanup")
    summary = next(s for s in steps if s.get("name") == "Predeploy summary")
    # R162 resolves two unconditional HOLDs only with exact-CID/label and
    # job-created-stage evidence; unverified resources still exit 79.
    assert "# RC6_A980_NATIVE_OWNER_BEGIN" in native["run"]
    assert 'docker rm "$rc6_container_id"' in native["run"]
    assert 'docker rm "$rc6_name"' not in native["run"]
    assert 'unknown_cid_or_foreign_labels_no_delete' in native["run"]
    assert "exit 79" in native["run"]
    assert "NO_RESOURCE|build_not_started" in cleanup["run"]
    assert "NO_RESOURCE|extract_not_started" in cleanup["run"]
    assert "unknown_or_foreign_image_no_delete" in cleanup["run"]
    assert "unknown_or_foreign_stage_no_delete" in cleanup["run"]
    assert "exit 79" in cleanup["run"]
    assert summary["if"] == "${{ success() && steps.rc6_local_cleanup.outcome == 'success' }}"
