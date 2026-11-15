"""R126: legacy materializers must never start on the unified candidate.

These are offline configuration-contract tests, not a GitHub Actions runner.
The narrow expression interpreter rejects unsupported syntax and absent context;
no workflow shell block is executed (bash receives only its syntax-check flag).
"""

import ast
from fnmatch import fnmatchcase
from pathlib import Path
import subprocess
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
CANDIDATE = "integration/rc6-a1115-r87-unified-paper-candidate-20261110"
WORKFLOWS = (
    ".github/workflows/rc6-wave16-materialize-safe-housekeeping-20260909.yml",
    ".github/workflows/rc6-wave18-materialize-safe-control-plane-20260909.yml",
)


def _workflow(path):
    # BaseLoader preserves the Actions key `on` instead of YAML 1.1's True.
    return yaml.load((ROOT / path).read_text(), Loader=yaml.BaseLoader)


def _trigger_allows(document, event, ref, changed_paths, *, diff_unavailable=False):
    """Model the branch/tag boundary separately from fail-open path filtering."""
    config = document["on"].get(event)
    if event != "push" or not isinstance(config, dict):
        return False
    if ref.startswith("refs/heads/") and ref != "refs/heads/":
        name = ref[len("refs/heads/"):]
        if "tags" in config and not any(
            key in config for key in ("branches", "branches-ignore")
        ):
            return False
        positive, negative = "branches", "branches-ignore"
    elif ref.startswith("refs/tags/") and ref != "refs/tags/":
        name = ref[len("refs/tags/"):]
        if any(key in config for key in ("branches", "branches-ignore")) and not any(
            key in config for key in ("tags", "tags-ignore")
        ):
            return False
        positive, negative = "tags", "tags-ignore"
    else:
        return False
    if positive in config and not any(fnmatchcase(name, p) for p in config[positive]):
        return False
    if any(fnmatchcase(name, p) for p in config.get(negative, ())):
        return False
    # GitHub does not evaluate paths for tag pushes. Diff failure may bypass
    # paths for branches, but never overrides the branch exclusion above.
    if ref.startswith("refs/tags/") or diff_unavailable:
        return True
    return any(
        fnmatchcase(path, pattern)
        for path in changed_paths for pattern in config.get("paths", ("**",))
    )


def _job_allows(document, *, event=None, ref=None):
    """Evaluate the actual guard's subset for the ASCII ref fixtures below.

    GitHub ignores case in string comparison and startsWith; this remains an
    offline contract model, not a general replacement for its expression engine.
    https://docs.github.com/en/actions/reference/workflows-and-actions/expressions
    """
    expression = document["jobs"]["materialize"].get("if")
    if expression is None:
        return True  # The pre-fix unguarded job really is admitted.
    expression = expression.strip()
    if expression.startswith("${{") and expression.endswith("}}"):
        expression = expression[3:-2].strip()
    # YAML preserves more-indented lines in folded scalars. Parentheses allow
    # that legal multi-line Actions expression in this narrow Python parser.
    expression = expression.replace("&&", " and ").replace("||", " or ")
    tree = ast.parse("(" + expression + ")", mode="eval")
    context = {"event_name": event or "", "ref": ref or ""}

    def interpret(node):
        if isinstance(node, ast.Expression):
            return interpret(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (str, bool)):
            return node.value
        if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                and node.value.id == "github" and node.attr in context):
            return context[node.attr]
        if isinstance(node, ast.BoolOp) and isinstance(node.op, (ast.And, ast.Or)):
            values = [bool(interpret(value)) for value in node.values]
            return all(values) if isinstance(node.op, ast.And) else any(values)
        if isinstance(node, ast.Compare) and len(node.ops) == len(node.comparators) == 1:
            left, right = interpret(node.left), interpret(node.comparators[0])
            if isinstance(left, str) and isinstance(right, str):
                left, right = left.lower(), right.lower()
            if isinstance(node.ops[0], ast.Eq):
                return left == right
            if isinstance(node.ops[0], ast.NotEq):
                return left != right
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "startsWith" and len(node.args) == 2
                and not node.keywords):
            value, prefix = [interpret(arg) for arg in node.args]
            return (isinstance(value, str) and isinstance(prefix, str)
                    and value.lower().startswith(prefix.lower()))
        raise AssertionError("Unsupported guard expression: " + ast.dump(node))

    return bool(interpret(tree))


class LegacyPushNoHostExclusionTests(unittest.TestCase):
    def test_candidate_push_is_excluded_even_when_path_diff_is_unavailable(self):
        for path in WORKFLOWS:
            for fallback in (False, True):
                with self.subTest(path=path, diff_unavailable=fallback):
                    self.assertFalse(_trigger_allows(
                        _workflow(path), "push", "refs/heads/" + CANDIDATE,
                        [path], diff_unavailable=fallback))

    def test_job_independently_rejects_candidate_if_trigger_filter_is_bypassed(self):
        for path in WORKFLOWS:
            with self.subTest(path=path):
                self.assertFalse(_job_allows(
                    _workflow(path), event="push", ref="refs/heads/" + CANDIDATE))

    def test_case_only_candidate_branches_are_conservatively_job_denied(self):
        # Explicit R126 ruling accepts this extra restriction: distinct case-
        # variant branches inside Porota do not run these two legacy writers.
        for path in WORKFLOWS:
            for ref in (
                "refs/heads/integration/RC6-a1115-r87-unified-paper-candidate-20261110",
                "refs/heads/INTEGRATION/rc6-a1115-r87-unified-paper-candidate-20261110",
            ):
                with self.subTest(path=path, ref=ref):
                    document = _workflow(path)
                    # Trigger glob is case-sensitive; the independent job
                    # expression is case-insensitive and therefore denies it.
                    self.assertTrue(_trigger_allows(document, "push", ref, [path]))
                    self.assertFalse(_job_allows(document, event="push", ref=ref))

    def test_job_fails_closed_on_missing_or_unknown_event_and_ref(self):
        for path in WORKFLOWS:
            for event, ref in (
                (None, None), ("push", None), (None, "refs/heads/normal"),
                ("workflow_dispatch", "refs/heads/normal"),
                ("pull_request", "refs/heads/normal"),
                ("unknown", "refs/heads/normal"),
                ("push", "refs/pull/1087/merge"), ("push", "unknown"),
            ):
                with self.subTest(path=path, event=event, ref=ref):
                    self.assertFalse(_job_allows(_workflow(path), event=event, ref=ref))

    def test_normal_and_nearby_branch_pushes_remain_eligible(self):
        for path in WORKFLOWS:
            for branch in ("deploy-prep/legacy", CANDIDATE + "-other", "main"):
                ref = "refs/heads/" + branch
                with self.subTest(path=path, branch=branch):
                    document = _workflow(path)
                    self.assertTrue(_trigger_allows(document, "push", ref, [path]))
                    self.assertTrue(_job_allows(document, event="push", ref=ref))
                    self.assertFalse(_trigger_allows(document, "push", ref, ["unrelated.py"]))

    def test_existing_tag_push_behavior_is_preserved(self):
        for path in WORKFLOWS:
            for tag in ("v17.0.0-rc6", "releases/rc6", CANDIDATE):
                ref = "refs/tags/" + tag
                with self.subTest(path=path, tag=tag):
                    document = _workflow(path)
                    self.assertTrue(_trigger_allows(document, "push", ref, []))
                    self.assertTrue(_job_allows(document, event="push", ref=ref))

    def test_no_new_dispatch_or_pr_close_event_is_registered(self):
        for path in WORKFLOWS:
            with self.subTest(path=path):
                self.assertEqual(set(_workflow(path)["on"]), {"push"})

    def test_yaml_and_existing_shell_blocks_parse_without_execution(self):
        for path in WORKFLOWS:
            document = _workflow(path)
            self.assertEqual(set(document["jobs"]), {"materialize"})
            for step in document["jobs"]["materialize"]["steps"]:
                if "run" in step:
                    with self.subTest(path=path, step=step.get("name")):
                        result = subprocess.run(
                            ["bash", "-n"], input=step["run"], text=True,
                            capture_output=True, check=False)
                        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
