"""Fail-closed adversarial contract for future, opt-in shard execution."""

import copy
import unittest

from scripts import rc6_ci_shard_quorum as shards


SHA = "c75d971d541ef7658ea12f844b9476fbeed239da"
TREE = "6315fe2add325a3eaedec2334612734a439d51d1"
NODEIDS = [f"tests/test_example.py::test_case[{i}]" for i in range(12)]


def fixtures(shard_count=3):
    m = shards.manifest(SHA, TREE, NODEIDS)
    reports = []
    for i, items in enumerate(shards.partition(m, shard_count)):
        reports.append({"shard_index": i, "source_sha": SHA,
                        "source_tree_sha": TREE,
                        "collection_sha256": m["collection_sha256"],
                        "executed_nodeids": items,
                        "pytest_exit_code": 0, "junit_testcases": len(items),
                        "failures": 0, "errors": 0, "skipped": 0,
                        "xfail": 0, "junit_sha256": f"{i + 1:064x}"})
    return m, reports


class TestShardQuorum(unittest.TestCase):
    def test_complete_quorum_counts_every_case_and_has_no_runtime_go(self):
        m, results = fixtures()
        receipt = shards.quorum(m, 3, results)
        self.assertEqual((receipt["discovered"], receipt["executed"]), (12, 12))
        self.assertEqual(receipt["shards"], 3)
        self.assertEqual(receipt["status"], "GREEN_QUORUM_OFFLINE_ONLY")
        self.assertIs(receipt["automatic_go"], False)
        self.assertIs(receipt["runtime_authority"], False)

    def test_partition_stable_across_input_order_and_balanced(self):
        a = shards.manifest(SHA, TREE, NODEIDS)
        b = shards.manifest(SHA, TREE, list(reversed(NODEIDS)))
        self.assertEqual(a, b)
        self.assertEqual(shards.partition(a, 5), shards.partition(b, 5))
        self.assertEqual(sorted(sum(shards.partition(a, 5), [])), sorted(NODEIDS))
        self.assertEqual([len(x) for x in shards.partition(a, 5)], [3, 3, 2, 2, 2])

    def test_source_tree_change_invalidates_collection_digest(self):
        a = shards.manifest(SHA, TREE, NODEIDS)
        b = shards.manifest("a" * 40, TREE, NODEIDS)
        c = shards.manifest(SHA, "b" * 40, NODEIDS)
        self.assertNotEqual(a["collection_sha256"], b["collection_sha256"])
        self.assertNotEqual(a["collection_sha256"], c["collection_sha256"])
        receipt = fixtures()[1]
        with self.assertRaises(shards.ShardQuorumRed):
            shards.quorum(b, 3, receipt)

    def test_missing_shard_is_red(self):
        m, results = fixtures()
        with self.assertRaises(shards.ShardQuorumRed):
            shards.quorum(m, 3, results[:-1])

    def test_duplicate_shard_is_red(self):
        m, results = fixtures()
        results[1] = copy.deepcopy(results[0])
        with self.assertRaises(shards.ShardQuorumRed):
            shards.quorum(m, 3, results)

    def test_missing_test_with_false_green_count_is_red(self):
        m, results = fixtures()
        results[0]["executed_nodeids"] = results[0]["executed_nodeids"][:-1]
        results[0]["junit_testcases"] -= 1
        with self.assertRaises(shards.ShardQuorumRed):
            shards.quorum(m, 3, results)

    def test_duplicate_test_across_shards_is_red(self):
        m, results = fixtures()
        results[1]["executed_nodeids"][0] = results[0]["executed_nodeids"][0]
        with self.assertRaises(shards.ShardQuorumRed):
            shards.quorum(m, 3, results)

    def test_added_test_is_red(self):
        m, results = fixtures()
        results[0]["executed_nodeids"].append("tests/test_extra.py::test_uncollected")
        results[0]["junit_testcases"] += 1
        with self.assertRaises(shards.ShardQuorumRed):
            shards.quorum(m, 3, results)

    def test_skip_xfail_failure_error_or_pytest_red_always_block(self):
        for field in ("skipped", "xfail", "failures", "errors", "pytest_exit_code"):
            with self.subTest(field=field):
                m, results = fixtures()
                results[2][field] = 1
                with self.assertRaises(shards.ShardQuorumRed):
                    shards.quorum(m, 3, results)

    def test_junit_count_mismatch_and_missing_digest_block(self):
        for field, value in (("junit_testcases", 999), ("junit_sha256", "INVALID")):
            m, results = fixtures()
            results[0][field] = value
            with self.assertRaises(shards.ShardQuorumRed):
                shards.quorum(m, 3, results)

    def test_collection_duplicates_and_unsafe_nodeids_block(self):
        for ids in (["x", "x"], ["test\nleak"], [], ["\x1b[31m"]):
            with self.subTest(ids=ids):
                with self.assertRaises(shards.ShardQuorumRed):
                    shards.manifest(SHA, TREE, ids)

    def test_tampered_manifest_or_bad_shard_count_blocks(self):
        m, _ = fixtures()
        bad = copy.deepcopy(m)
        bad["nodeids"].pop()
        with self.assertRaises(shards.ShardQuorumRed):
            shards.partition(bad, 3)
        for number in (0, -1, True, 17, 100):
            with self.subTest(count=number):
                with self.assertRaises(shards.ShardQuorumRed):
                    shards.partition(m, number)


if __name__ == "__main__":
    unittest.main()
