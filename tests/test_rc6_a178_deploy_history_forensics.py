"""Prevent the A178 deploy regression census from losing historical runs.

The source is a frozen read-only GitHub Actions query of two workflow IDs
on the RC6 productive branch, not an operational-go or runtime receipt.
"""
from collections import Counter
import csv
from datetime import datetime, timezone
from pathlib import Path
import unittest


PATH = (Path(__file__).resolve().parents[1] / "docs/audit/data" /
        "rc6_deploy_actions_20260915_20261029.csv")
OLD, V2 = "358890088", "367852515"


class HistoricalDeployForensics(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with PATH.open(newline="", encoding="utf-8") as stream:
            cls.rows = list(csv.DictReader(stream))

    def test_census_is_complete_consistent_and_has_no_duplication(self):
        self.assertEqual(len(self.rows), 458)
        self.assertEqual(len({row["run_id"] for row in self.rows}), 458)
        self.assertEqual(Counter(row["workflow_id"] for row in self.rows),
                         {OLD: 292, V2: 166})
        self.assertEqual(Counter(row["conclusion"] for row in self.rows), {
            "success": 128, "failure": 317, "cancelled": 10,
            "skipped": 2, "startup_failure": 1})
        self.assertEqual([row["created_at_utc"] for row in self.rows],
                         sorted(row["created_at_utc"] for row in self.rows))
        for row in self.rows:
            self.assertEqual(row["actions_url"],
                "https://github.com/mbalbo2023/Porota-trading/actions/runs/" + row["run_id"])
            dt = datetime.fromisoformat(row["created_at_utc"].replace("Z", "+00:00"))
            self.assertEqual(dt.tzinfo, timezone.utc)

    def test_historical_inflection_is_derived_from_actions_not_guessed(self):
        old = [row for row in self.rows if row["workflow_id"] == OLD]
        before = [row for row in old if "2026-09-18" <= row["created_at_utc"][:10] <= "2026-09-21"]
        after = [row for row in old if "2026-09-22" <= row["created_at_utc"][:10] <= "2026-09-25"]
        self.assertEqual((len(before), Counter(x["conclusion"] for x in before)["success"]), (36, 28))
        self.assertEqual((len(after), Counter(x["conclusion"] for x in after)["failure"]), (161, 116))
        v2 = [row for row in self.rows if row["workflow_id"] == V2]
        latest = [row for row in v2 if "2026-10-27" <= row["created_at_utc"][:10] <= "2026-10-29"]
        self.assertEqual(len(latest), 13)
        self.assertTrue(all(row["conclusion"] == "failure" for row in latest))


if __name__ == "__main__":
    unittest.main()
