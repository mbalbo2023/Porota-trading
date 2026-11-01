"""Exercise the main PR guard with owner receipts, without GitHub or host calls."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/checkpoint-attestation.yml"
TEMPLATE = ROOT / ".github/pull_request_template.md"
BASE = "e" * 40
URL = "https://github.com/mbalbo2023/Porota-trading/issues/732#issuecomment-6556855371"
BODY = "\n".join((
    "Workstream ID: RC6_A281_MAIN_GOVERNANCE",
    "Owner declaration URL: " + URL,
    "Work branch: fix/rc6-a281-main-pr-governance",
    "Base SHA: " + BASE,
    "Scope paths: .github/pull_request_template.md, .github/workflows/checkpoint-attestation.yml, tests/test_rc6_main_pr_governance.py",
))
FILES = {".github/pull_request_template.md",
         ".github/workflows/checkpoint-attestation.yml",
         "tests/test_rc6_main_pr_governance.py"}


def guard():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    start = workflow.index("          python3 - <<'PY'\n",
                           workflow.index("- name: Require current main PR workstream declaration"))
    start += len("          python3 - <<'PY'\n")
    end = workflow.index("\n          PY", start)
    code = "\n".join(line.removeprefix("          ")
                     for line in workflow[start:end].splitlines())
    namespace = {"__name__": "rc6_guard_test"}
    exec(compile(code, str(WORKFLOW), "exec"), namespace)
    return namespace


def event(body=BODY, base=BASE, branch="fix/rc6-a281-main-pr-governance"):
    return {"pull_request": {"body": body, "head": {"ref": branch},
                             "base": {"ref": "main", "sha": base},
                             "changed_files": 3, "number": 781}}


def receipt(*, body="RC6_A281_MAIN_GOVERNANCE WRITE_OWNER " + " ".join(FILES),
            login="mbalbo2023", url=URL, issue="732"):
    return {"html_url": url, "issue_url": "https://api.github.com/repos/mbalbo2023/Porota-trading/issues/" + issue,
            "user": {"login": login}, "body": body}


class MainPrGovernanceTest(unittest.TestCase):
    def test_template_matches_guard_and_has_no_autofilled_evidence(self):
        template = TEMPLATE.read_text(encoding="utf-8")
        for name in ("Workstream ID", "Owner declaration URL", "Work branch",
                     "Base SHA", "Scope paths", "Checkpoint ID",
                     "Continuity checkpoint path", "Checkpoint commit SHA",
                     "RC6 baseline SHA", "Safety invariants"):
            self.assertIn(name + ": ", template)
        self.assertNotIn("2026-09-12/1", template)
        self.assertNotIn("Safety invariants: PRODUCTION_PAPER", template)

    def test_valid_pr_owner_and_scope(self):
        self.assertEqual(guard()["validate"](event(), receipt(), FILES),
                         "RC6_A281_MAIN_GOVERNANCE")

    def test_missing_duplicate_or_placeholder_metadata_fails_closed(self):
        validate = guard()["validate"]
        bodies = (BODY.replace("Workstream ID: RC6_A281_MAIN_GOVERNANCE\n", ""),
                  BODY + "\nWorkstream ID: RC6_A281_MAIN_GOVERNANCE",
                  BODY.replace("Base SHA: " + BASE, "Base SHA: [fill]"))
        for body in bodies:
            with self.subTest(body=body[-40:]), self.assertRaises(ValueError):
                validate(event(body), receipt(), FILES)

    def test_branch_and_base_must_match_actual_pr(self):
        validate = guard()["validate"]
        for changed in (event(base="f" * 40), event(branch="fix/other")):
            with self.assertRaisesRegex(ValueError, "MAIN_PR_BRANCH_OR_BASE_MISMATCH"):
                validate(changed, receipt(), FILES)

    def test_wrong_owner_url_or_issue_receipt_cannot_borrow_a_comment(self):
        validate = guard()["validate"]
        for wrong in (receipt(login="someone-else"), receipt(issue="485"),
                      receipt(url=URL.replace("6556855371", "6556855372"))):
            with self.assertRaisesRegex(ValueError, "MAIN_PR_OWNER_RECEIPT_UNVERIFIED"):
                validate(event(), wrong, FILES)

    def test_comment_must_declare_workstream_write_owner_and_scope(self):
        validate = guard()["validate"]
        for text in ("WRITE_OWNER .github/pull_request_template.md",
                     "RC6_A281_MAIN_GOVERNANCE READ_ONLY " + " ".join(FILES),
                     "RC6_A281_MAIN_GOVERNANCE WRITE_OWNER unrelated-file"):
            with self.assertRaisesRegex(ValueError, "MAIN_PR_OWNER_RECEIPT_UNVERIFIED"):
                validate(event(), receipt(body=text), FILES)

    def test_cross_repo_and_path_traversal_fail(self):
        validate = guard()["validate"]
        for text in (BODY.replace("mbalbo2023/Porota-trading", "other/repo"),
                     BODY.replace("Scope paths: .github/pull_request_template.md, ",
                                  "Scope paths: ../secret, ")):
            with self.assertRaises(ValueError):
                validate(event(text), receipt(), FILES)

    def test_hidden_or_undeclared_changed_path_fails_closed(self):
        validate = guard()["validate"]
        for paths in (FILES | {"secrets.json"}, FILES - {"tests/test_rc6_main_pr_governance.py"}):
            with self.assertRaisesRegex(ValueError, "MAIN_PR_CHANGED_PATHS_OUTSIDE_OWNER_SCOPE"):
                validate(event(), receipt(), paths)

    def test_actual_pr_files_pagination_and_rename(self):
        changed_paths = guard()["changed_paths"]
        normal = [{"filename": path, "status": "modified"} for path in sorted(FILES)]
        self.assertEqual(changed_paths(event(), lambda page: normal if page == 1 else []), FILES)
        renamed = [{"filename": "new.py", "previous_filename": "old.py", "status": "renamed"}]
        one = event()
        one["pull_request"]["changed_files"] = 1
        self.assertEqual(changed_paths(one, lambda _: renamed), {"new.py", "old.py"})
        many = event()
        many["pull_request"]["changed_files"] = 101
        first = [{"filename": "file-%03d.py" % i} for i in range(100)]
        second = [{"filename": "file-100.py"}]
        self.assertEqual(changed_paths(many, lambda page: first if page == 1 else second),
                         {row["filename"] for row in first + second})
        with self.assertRaisesRegex(ValueError, "MAIN_PR_FILES_PAGE_INVALID"):
            changed_paths(many, lambda page: first if page == 1 else [])

    def test_missing_page_duplicate_and_large_pr_fail_closed(self):
        changed_paths = guard()["changed_paths"]
        e = event()
        with self.assertRaisesRegex(ValueError, "MAIN_PR_FILES_PAGINATION_MISMATCH"):
            changed_paths(e, lambda _: [{"filename": "only.py"}])
        with self.assertRaisesRegex(ValueError, "MAIN_PR_FILES_DUPLICATE"):
            changed_paths(e, lambda _: [{"filename": "same.py"}] * 3)
        e["pull_request"]["changed_files"] = 301
        with self.assertRaisesRegex(ValueError, "MAIN_PR_FILES_BUDGET_OR_COUNT_INVALID"):
            changed_paths(e, lambda _: [])


if __name__ == "__main__":
    unittest.main()
