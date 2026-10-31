"""Synthetic JUnit replay, including skipped/duplicate/missing cases and shards."""

import copy
from pathlib import Path
import tempfile
import unittest
from xml.etree import ElementTree as ET

from scripts import rc6_ci_junit_quorum as quorum
from scripts import rc6_ci_pytest_identity as identity
from scripts import rc6_ci_shard_quorum as shards


SHA = "a" * 40
TREE = "b" * 40
NODES = [f"tests/test_provenance.py::test_case[{i}]" for i in range(11)]


def xml_for(path, nodeids, *, status=None, bad_hash=False, missing_property=False,
            duplicate_property=False, extra_testcase=False):
    suites = ET.Element("testsuites")
    suite = ET.SubElement(suites, "testsuite", tests=str(len(nodeids)),
                          failures="0", errors="0", skipped="0")
    for nodeid in nodeids:
        case = ET.SubElement(suite, "testcase", name="synthetic-case")
        properties = ET.SubElement(case, "properties")
        if not missing_property:
            sha = "0" * 64 if bad_hash else identity.nodeid_digest(nodeid)
            ET.SubElement(properties, "property", name=identity.PROPERTY, value=sha)
            if duplicate_property:
                ET.SubElement(properties, "property", name=identity.PROPERTY, value=sha)
        if status:
            ET.SubElement(case, status, type="pytest.xfail" if status == "skipped" else "")
    if extra_testcase:
        ET.SubElement(suite, "testcase", name="uncollected")
    path.write_bytes(ET.tostring(suites, encoding="utf-8", xml_declaration=True))


class TestJUnitNodeidQuorum(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.m = shards.manifest(SHA, TREE, NODES)
        self.xml = self.root / "governed.xml"
        xml_for(self.xml, NODES)

    def verify(self, *, manifest=None, exit_code=0):
        return quorum.verify_junit(self.m, self.m if manifest is None else manifest,
                                   self.xml, exit_code)

    def test_exact_xml_sha_tree_nodeid_proof_passes_without_go(self):
        receipt = self.verify()
        self.assertEqual((receipt["discovered"], receipt["executed"]), (11, 11))
        self.assertEqual(receipt["status"], "GREEN_JUNIT_NODEID_PROOF")
        self.assertFalse(receipt["automatic_go"] or receipt["runtime_authority"])
        self.assertEqual(len(receipt["junit_xml_sha256"]), 64)
        self.assertNotIn("test_case", str(receipt))

    def test_wrong_sha_tree_or_mutated_manifest_is_red(self):
        for changed in (shards.manifest("c" * 40, TREE, NODES),
                        shards.manifest(SHA, "d" * 40, NODES),
                        shards.manifest(SHA, TREE, NODES[:-1])):
            with self.subTest(changed=changed["collection_sha256"]):
                with self.assertRaises(quorum.JunitQuorumRed):
                    self.verify(manifest=changed)
        bad = copy.deepcopy(self.m)
        bad["collection_sha256"] = "0" * 64
        with self.assertRaises(quorum.JunitQuorumRed):
            self.verify(manifest=bad)

    def test_missing_duplicate_extra_or_wrong_hash_is_red(self):
        cases = [dict(nodeids=NODES[:-1]), dict(nodeids=NODES + NODES[:1]),
                 dict(nodeids=NODES, extra_testcase=True),
                 dict(nodeids=NODES, missing_property=True),
                 dict(nodeids=NODES, duplicate_property=True),
                 dict(nodeids=NODES, bad_hash=True)]
        for kwargs in cases:
            with self.subTest(kwargs=kwargs):
                xml_for(self.xml, **kwargs)
                with self.assertRaises(quorum.JunitQuorumRed):
                    self.verify()

    def test_failure_error_skip_xfail_and_nonzero_pytest_are_red(self):
        for status in ("failure", "error", "skipped"):
            with self.subTest(status=status):
                xml_for(self.xml, NODES, status=status)
                with self.assertRaises(quorum.JunitQuorumRed):
                    self.verify()
        xml_for(self.xml, NODES)
        for exit_code in (1, 2, 5, True):
            with self.subTest(exit_code=exit_code):
                with self.assertRaises(quorum.JunitQuorumRed):
                    self.verify(exit_code=exit_code)

    def test_invalid_xml_symlink_and_missing_file_are_red(self):
        self.xml.write_bytes(b"<testsuite><testcase>")
        with self.assertRaises(quorum.JunitQuorumRed):
            self.verify()
        self.xml.unlink()
        with self.assertRaises(quorum.JunitQuorumRed):
            self.verify()
        victim = self.root / "victim.xml"
        xml_for(victim, NODES)
        self.xml.symlink_to(victim)
        with self.assertRaises(quorum.JunitQuorumRed):
            self.verify()

    def test_malformed_untrusted_types_are_rejected_without_trace(self):
        with self.assertRaises(quorum.JunitQuorumRed):
            quorum.verify_junit(self.m, self.m, None, 0)
        with self.assertRaises(quorum.JunitQuorumRed):
            quorum.verify_junit(self.m, self.m, self.xml, 0,
                                required_nodeids=[[], {}])
        with self.assertRaises(quorum.JunitQuorumRed):
            quorum.verify_parallel(self.m, [], True, self.root / "output.xml")
        with self.assertRaises(quorum.JunitQuorumRed):
            quorum.verify_parallel(self.m, [], 2, self.root / "output.xml")

    def test_two_shards_combine_exact_suite_and_preserve_every_case(self):
        groups = shards.partition(self.m, 2)
        reports = []
        for i, nodeids in enumerate(groups):
            path = self.root / f"shard-{i}.xml"
            xml_for(path, nodeids)
            reports.append({"shard_index": i,
                            "manifest": shards.manifest(SHA, TREE, nodeids),
                            "junit_file": path, "pytest_exit_code": 0})
        output = self.root / "aggregate.xml"
        receipt = quorum.verify_parallel(self.m, reports, 2, output)
        self.assertEqual((receipt["discovered"], receipt["executed"]), (11, 11))
        self.assertEqual(len(list(ET.parse(output).iter("testcase"))), 11)
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        self.assertFalse(receipt["automatic_go"])

        with self.assertRaises(quorum.JunitQuorumRed):
            quorum.verify_parallel(self.m, reports, 2, reports[0]["junit_file"])

        for corrupt in (reports[:-1], [reports[0], reports[0]],
                        [{**reports[0], "shard_index": 1}, reports[1]]):
            with self.subTest(corrupt=corrupt):
                with self.assertRaises(quorum.JunitQuorumRed):
                    quorum.verify_parallel(self.m, corrupt, 2, output)
        self.assertEqual(len(list(ET.parse(output).iter("testcase"))), 11)

    def test_duplicate_test_across_shards_and_missing_case_are_red(self):
        groups = shards.partition(self.m, 2)
        cases = []
        for i, items in enumerate(groups):
            path = self.root / f"s-{i}.xml"
            xml_for(path, items)
            cases.append({"shard_index": i,
                          "manifest": shards.manifest(SHA, TREE, items),
                          "junit_file": path, "pytest_exit_code": 0})
        cases[1]["manifest"] = shards.manifest(SHA, TREE,
                                                groups[1][:-1] + groups[0][:1])
        with self.assertRaises(quorum.JunitQuorumRed):
            quorum.verify_parallel(self.m, cases, 2, self.root / "output.xml")
        cases[1]["manifest"] = shards.manifest(SHA, TREE, groups[1])
        xml_for(cases[1]["junit_file"], groups[1][:-1])
        with self.assertRaises(quorum.JunitQuorumRed):
            quorum.verify_parallel(self.m, cases, 2, self.root / "output.xml")


if __name__ == "__main__":
    unittest.main()
