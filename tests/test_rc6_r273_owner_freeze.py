"""R273: a mutable PR never creates a Docker PROBE without owner freeze."""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/rc6-r163-offhost-guard79-proof.yml"
PRODUCT = "deploy/rc6-pr69-isolated-20260915"
REPO = "mbalbo2023/Porota-trading"
FREEZE = "rc6-offhost-owner-freeze"


def _dot(data):
    if isinstance(data, dict):
        return SimpleNamespace(**{name: _dot(value) for name, value in data.items()})
    return data


def _condition(source, *, action="labeled", label=FREEZE,
               sender="mbalbo2023", triggering_actor="mbalbo2023",
               draft=True, state="open", pr_number=1087, base=PRODUCT,
               base_repo=REPO, head_repo=REPO, python_result="success"):
    raw = source.strip()
    if not (raw.startswith("${{") and raw.endswith("}}")):
        raise AssertionError(f"GitHub expression missing: {raw!r}")
    expr = raw[3:-2].replace("&&", " and ").replace("||", " or ").strip()
    expr = re.sub(r"\btrue\b", "True", re.sub(r"\bfalse\b", "False", expr))
    expr = expr.replace("needs.python311-lock-closure.result", "needs.python_job.result")
    github = _dot({
        "event_name": "pull_request", "repository": REPO,
        "actor": sender, "triggering_actor": triggering_actor,
        "event": {
            "action": action,
            "label": {"name": label},
            "sender": {"login": sender},
            "pull_request": {
                "number": pr_number, "draft": draft, "state": state,
                "base": {"ref": base, "repo": {"full_name": base_repo}},
                "head": {"repo": {"full_name": head_repo}},
            },
        },
    })
    # Evaluate only the reviewed, bounded GitHub-expression syntax in this
    # workflow, never evaluate a response from GitHub or user-supplied code.
    return bool(eval(expr, {"__builtins__": {}}, {
        "github": github, "needs": _dot({"python_job": {"result": python_result}}),
    }))


class OwnerFreezeWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = WORKFLOW.read_text(encoding="utf-8")
        cls.workflow = yaml.load(cls.raw, Loader=yaml.BaseLoader)

    def test_pr_edits_keep_lightweight_trigger_without_image(self):
        events = self.workflow["on"]["pull_request"]["types"]
        self.assertEqual(events, [
            "opened", "synchronize", "reopened", "labeled", "unlabeled",
        ])
        for action in ("opened", "synchronize", "reopened", "unlabeled"):
            with self.subTest(action=action):
                self.assertFalse(_condition(
                    self.workflow["jobs"]["python311-lock-closure"]["if"],
                    action=action,
                ))
                self.assertFalse(_condition(
                    self.workflow["jobs"]["offhost-native-proof"]["if"],
                    action=action,
                ))

    def test_authenticated_owner_draft_freeze_enables_exact_existing_jobs(self):
        jobs = self.workflow["jobs"]
        self.assertEqual(list(jobs), [
            "python311-lock-closure", "offhost-native-proof",
            "offhost-independent-bytes",
        ])
        self.assertTrue(_condition(jobs["python311-lock-closure"]["if"]))
        self.assertTrue(_condition(jobs["offhost-native-proof"]["if"]))
        self.assertFalse(_condition(jobs["offhost-native-proof"]["if"],
                                    python_result="skipped"))

    def test_unauthorized_or_stale_freeze_cannot_start_image(self):
        paths = self.workflow["jobs"]
        variants = [
            {"label": "rc6-owner-freeze-impostor"},
            {"sender": "untrusted-maintainer"},
            {"triggering_actor": "untrusted-rerunner"},
            {"draft": False},
            {"state": "closed"},
            {"pr_number": 1088},
            {"base": "main"},
            {"base_repo": "attacker/Porota-trading"},
            {"head_repo": "attacker/Porota-trading"},
        ]
        for variant in variants:
            with self.subTest(variant=variant):
                self.assertFalse(_condition(
                    paths["python311-lock-closure"]["if"], **variant))
                self.assertFalse(_condition(
                    paths["offhost-native-proof"]["if"], **variant))

    def test_commit_replacement_cancels_frozen_inflight_probe(self):
        concurrency = self.workflow["concurrency"]
        self.assertEqual(concurrency["group"],
                         "rc6-r163-offhost-${{ github.event.pull_request.number }}")
        for action, label, should_cancel in (
            ("synchronize", FREEZE, True),
            ("unlabeled", FREEZE, True),
            ("unlabeled", "unrelated-label", False),
            ("labeled", FREEZE, False),
            ("opened", FREEZE, False),
        ):
            with self.subTest(action=action, label=label):
                self.assertEqual(_condition(concurrency["cancel-in-progress"],
                                            action=action, label=label),
                                 should_cancel)

    def test_native_image_identity_and_r205_privacy_remain_in_chain(self):
        jobs = self.workflow["jobs"]
        native = jobs["offhost-native-proof"]
        self.assertEqual(native["needs"], ["python311-lock-closure"])
        step_names = [step["name"] for step in native["steps"]]
        self.assertEqual(step_names[2], "Strict candidate identity")
        self.assertIn("Build one ephemeral PROBE image, never push", step_names)
        self.assertIn("R205 raw JUnit privacy hold before any upload (R206 required)",
                      step_names)
        self.assertIn('test "$(git rev-parse HEAD)" = "$CANDIDATE_SHA"',
                      native["steps"][2]["run"])
        self.assertIn("git rev-parse HEAD^{tree}", native["steps"][2]["run"])
        self.assertTrue(_condition(native["if"]))


if __name__ == "__main__":
    unittest.main()
