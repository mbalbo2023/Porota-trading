"""Exercise the cheap negative preflight against real Git trees."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from scripts.rc6_fast_source_preflight import validate


class FastSourcePreflight(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.git("init", "-q")
        self.git("config", "user.name", "RC6 test")
        self.git("config", "user.email", "test@example.invalid")
        self.matrix = self.root / "docs/audits/convergence/REQUIREMENT_CLOSURE_MATRIX.json"
        self.matrix.parent.mkdir(parents=True)
        self.matrix.write_text(json.dumps({"path_evolution": {
            "docs/audits/convergence/REQUIREMENT_CLOSURE_MATRIX.json": {
                "reason": "fixture", "requirement_ids": ["U11"]
            }}}))
        self.git("add", ".")
        self.git("commit", "-qm", "base")
        self.base = self.git("rev-parse", "HEAD")

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.root), *args], text=True).strip()

    def commit(self, path, explained=False):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("def test_example(): pass\n" if path.startswith("tests/") else "safe\n")
        if explained:
            matrix = json.loads(self.matrix.read_text())
            matrix["path_evolution"][path] = {"reason": "fixture", "requirement_ids": ["U11"]}
            self.matrix.write_text(json.dumps(matrix))
        self.git("add", ".")
        self.git("commit", "-qm", "source")
        return {"source_sha": self.git("rev-parse", "HEAD"),
                "source_tree": self.git("rev-parse", "HEAD^{tree}"),
                "development_base_sha": self.base, "cheap_files": []}

    def test_missing_evolution_fails_before_material(self):
        admission = self.commit("ops/new_control.sh")
        with self.assertRaisesRegex(ValueError, "FAST_PREFLIGHT_UNEXPLAINED_SOURCE_EVOLUTION"):
            validate(self.root, admission)

    def test_new_test_must_be_in_cheap_and_green_grants_no_credit(self):
        path = "tests/test_new_guard.py"
        admission = self.commit(path, explained=True)
        with self.assertRaisesRegex(ValueError, "FAST_PREFLIGHT_CHANGED_TEST_OUTSIDE_CHEAP"):
            validate(self.root, admission)
        admission["cheap_files"] = [path]
        observed = validate(self.root, admission)
        self.assertEqual(observed["status"], "NEGATIVE_PREFLIGHT_GREEN_NO_GATE_CREDIT")
        self.assertEqual(observed["changed_paths"], 2)


if __name__ == "__main__":
    unittest.main()
