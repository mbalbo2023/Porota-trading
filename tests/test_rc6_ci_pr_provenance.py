"""Future predeploy PR provenance cannot become an owner or deployment GO."""

import unittest

from scripts import rc6_ci_pr_provenance as provenance


SHA = "c75d971d541ef7658ea12f844b9476fbeed239da"
BASE_SHA = "b5215ad83aa4d8a490095e7347fa565f1550a532"


def pr_event():
    return {"action": "synchronize", "repository": {"full_name": provenance.REPO},
            "pull_request": {"number": 775, "draft": False, "merged": False,
                             "state": "open", "body": "untrusted body MUST NOT COPY",
                             "base": {"ref": provenance.BASE, "sha": BASE_SHA},
                             "head": {"sha": SHA, "repo": {"full_name": provenance.REPO}}}}


class TestPRProvenance(unittest.TestCase):
    def test_exact_open_internal_pr_only(self):
        receipt = provenance.verify(pr_event(), expected_sha=SHA)
        self.assertEqual(receipt["candidate_sha"], SHA)
        self.assertEqual(receipt["base_sha"], BASE_SHA)
        self.assertEqual(receipt["status"], "PR_SOURCE_IDENTITY_VERIFIED")
        self.assertFalse(any(receipt[key] for key in
                             ("owner_go_verified", "tree_verified", "artifact_verified",
                              "runtime_verified", "automatic_go")))
        self.assertNotIn("untrusted body", str(receipt))

    def test_fork_pr_rejected(self):
        event = pr_event()
        event["pull_request"]["head"]["repo"]["full_name"] = "outside/unsafe"
        with self.assertRaises(provenance.PRProvenanceRed):
            provenance.verify(event, expected_sha=SHA)

    def test_stale_head_or_wrong_base_rejected(self):
        for component, path, value in (
                ("head", "sha", "a" * 40),
                ("base", "ref", "main"),
                ("base", "sha", "not-a-sha")):
            with self.subTest(path=path):
                event = pr_event()
                event["pull_request"][component][path] = value
                with self.assertRaises(provenance.PRProvenanceRed):
                    provenance.verify(event, expected_sha=SHA)

    def test_draft_merged_closed_or_unknown_action_rejected(self):
        for key, value in (("draft", True), ("merged", True), ("state", "closed")):
            event = pr_event()
            event["pull_request"][key] = value
            with self.assertRaises(provenance.PRProvenanceRed):
                provenance.verify(event, expected_sha=SHA)
        event = pr_event()
        event["action"] = "edited"
        with self.assertRaises(provenance.PRProvenanceRed):
            provenance.verify(event, expected_sha=SHA)

    def test_missing_or_bad_fields_are_fail_closed(self):
        variants = []
        e = pr_event()
        del e["pull_request"]["head"]["repo"]
        variants.append(e)
        e = pr_event()
        e["pull_request"]["number"] = True
        variants.append(e)
        e = pr_event()
        e["pull_request"]["head"]["sha"] = None
        variants.append(e)
        variants.append({})
        for value in variants:
            with self.assertRaises(provenance.PRProvenanceRed):
                provenance.verify(value, expected_sha=SHA)

    def test_all_known_pr_triggers_accepted_but_never_authorize(self):
        for action in provenance.ACTIONS:
            event = pr_event()
            event["action"] = action
            self.assertFalse(provenance.verify(event, expected_sha=SHA)["automatic_go"])

    def test_parallel_pilot_exact_pr776_candidate_activates_without_go(self):
        event = pr_event()
        event["pull_request"]["number"] = provenance.PILOT_PR_NUMBER
        event["pull_request"]["head"]["ref"] = provenance.PILOT_HEAD_REF
        event["pull_request"]["base"]["repo"] = {"full_name": provenance.REPO}
        receipt = provenance.select_parallel_pilot(event, expected_sha=SHA)
        self.assertEqual(receipt["selection"], "EXACT_PR776_BRANCH")
        self.assertEqual(receipt["status"], "PARALLEL_DIAGNOSTIC_ONLY")
        for field in ("owner_go_verified", "automatic_go", "runtime_authority",
                      "tree_verified"):
            self.assertFalse(receipt[field])

    def test_parallel_pilot_foreign_branch_fork_draft_and_false_opt_in_red(self):
        baseline = pr_event()
        baseline["pull_request"]["number"] = provenance.PILOT_PR_NUMBER
        baseline["pull_request"]["head"]["ref"] = provenance.PILOT_HEAD_REF
        baseline["pull_request"]["base"]["repo"] = {"full_name": provenance.REPO}
        cases = (('pr_number', lambda e:e['pull_request'].__setitem__('number', 775)),
                 ('head_ref', lambda e:e['pull_request']['head'].__setitem__('ref','other')),
                 ('head_repo', lambda e:e['pull_request']['head']['repo'].__setitem__('full_name','fork/foreign')),
                 ('base_repo', lambda e:e['pull_request']['base']['repo'].__setitem__('full_name','fork/foreign')),
                 ('draft', lambda e:e['pull_request'].__setitem__('draft',True)))
        for label, change in cases:
            with self.subTest(label=label):
                event = {**baseline, "pull_request": {
                    **baseline["pull_request"],
                    "base": {**baseline["pull_request"]["base"],
                             "repo": dict(baseline["pull_request"]["base"]["repo"])},
                    "head": {**baseline["pull_request"]["head"],
                             "repo": dict(baseline["pull_request"]["head"]["repo"])}
                }}
                change(event)
                with self.assertRaises(provenance.PRProvenanceRed):
                    provenance.select_parallel_pilot(event, expected_sha=SHA)

    def test_explicit_opt_in_still_requires_authentic_internal_pr(self):
        event = pr_event()
        event['pull_request']['head']['ref'] = 'feature/other-coherent-candidate'
        event['pull_request']['base']['repo'] = {'full_name':provenance.REPO}
        with self.assertRaises(provenance.PRProvenanceRed):
            provenance.select_parallel_pilot(event, expected_sha=SHA,
                                             operator_opt_in='0')
        receipt = provenance.select_parallel_pilot(event, expected_sha=SHA,
                                                   operator_opt_in='1')
        self.assertEqual(receipt['selection'],'REPOSITORY_OPT_IN')
        self.assertFalse(receipt['automatic_go'])
        event['pull_request']['head']['repo']['full_name']='outside/fork'
        with self.assertRaises(provenance.PRProvenanceRed):
            provenance.select_parallel_pilot(event, expected_sha=SHA,
                                             operator_opt_in='1')


if __name__ == "__main__":
    unittest.main()
