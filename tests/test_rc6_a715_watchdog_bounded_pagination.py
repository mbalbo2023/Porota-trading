"""A715: bounded GitHub schedule pagination, synthetic/offhost only."""
from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import rc6_schedule_watchdog as a358


OBSERVED = datetime(2026, 11, 6, 15, 40, tzinfo=timezone.utc)


def one(run_id, created_at="2026-11-06T15:15:00Z", **changes):
    result = {
        "id": run_id,
        "created_at": created_at,
        "event": "schedule",
        "run_attempt": 1,
        "head_branch": "main",
        "status": "completed",
    }
    result.update(changes)
    return result


class BoundedSchedulePaginationTest(unittest.TestCase):
    def test_day_utc_filter_is_exact_art_midnight_and_small_page(self):
        seen = []

        def get(url, token):
            self.assertEqual(token, "fake-token")
            seen.append(url)
            return {"workflow_runs": [], "total_count": 0}

        runs, complete = a358.fetch_schedule_runs_for_art_day(
            "fake-token", OBSERVED, get=get
        )
        self.assertEqual([], runs)
        self.assertTrue(complete)
        self.assertEqual(1, len(seen))
        url = seen[0]
        self.assertIn("/workflows/384354765/runs?event=schedule", url)
        self.assertIn("created=2026-11-06T03%3A00%3A00Z..2026-11-07T02%3A59%3A59Z", url)
        self.assertIn("&per_page=10&page=1", url)
        self.assertNotIn("fake-token", url)

    def test_two_pages_exact_total_and_unique_runs_prove_coverage(self):
        visited = []
        rows = [one(i) for i in range(1, 12)]

        def get(url, token):
            visited.append(url)
            page = len(visited)
            return {
                "workflow_runs": rows[0:10] if page == 1 else rows[10:11],
                "total_count": 11,
            }

        runs, complete = a358.fetch_schedule_runs_for_art_day(
            "fake-token", OBSERVED, get=get
        )
        self.assertTrue(complete)
        self.assertEqual(11, len(runs))
        self.assertEqual(2, len(visited))
        self.assertIn("&page=2", visited[-1])

    def test_overcap_fails_closed_without_extra_api_pages(self):
        calls = []

        def get(url, token):
            calls.append(url)
            return {
                "workflow_runs": [one(i) for i in range(1, 11)],
                "total_count": 121,
            }

        runs, complete = a358.fetch_schedule_runs_for_art_day(
            "fake-token", OBSERVED, get=get
        )
        self.assertFalse(complete)
        self.assertEqual([], runs)
        self.assertEqual(1, len(calls))
        self.assertEqual("NO_VERIFICADO",
                         a358.classify(runs, OBSERVED, api_day_covered=complete)["status"])

    def test_missing_total_count_and_truncated_page_are_incomplete(self):
        for fixture in (
            {"workflow_runs": []},
            {"workflow_runs": [one(1)], "total_count": 12},
            {"workflow_runs": "not-a-list", "total_count": 0},
        ):
            with self.subTest(fixture=fixture):
                runs, complete = a358.fetch_schedule_runs_for_art_day(
                    "fake-token", OBSERVED, get=lambda *_: fixture
                )
                self.assertFalse(complete)
                self.assertEqual("NO_VERIFICADO", a358.classify(
                    runs, OBSERVED, api_day_covered=complete)["status"])

    def test_moving_total_count_between_pages_fails_closed(self):
        fixtures = [
            {"workflow_runs": [one(i) for i in range(1, 11)], "total_count": 11},
            {"workflow_runs": [one(11)], "total_count": 12},
        ]
        it = iter(fixtures)
        runs, complete = a358.fetch_schedule_runs_for_art_day(
            "fake-token", OBSERVED, get=lambda *_: next(it)
        )
        self.assertFalse(complete)
        self.assertEqual(10, len(runs))

    def test_duplicate_id_across_pages_is_not_complete(self):
        fixtures = [
            {"workflow_runs": [one(i) for i in range(1, 11)], "total_count": 11},
            {"workflow_runs": [one(1)], "total_count": 11},
        ]
        it = iter(fixtures)
        runs, complete = a358.fetch_schedule_runs_for_art_day(
            "fake-token", OBSERVED, get=lambda *_: next(it)
        )
        self.assertFalse(complete)
        self.assertEqual(10, len(runs))

    def test_outside_art_day_future_and_wrong_event_are_incomplete(self):
        invalid_rows = (
            one(1, "2026-11-06T02:59:59Z"),
            one(1, "2026-11-07T03:00:00Z"),
            one(1, "2026-11-06T15:41:00Z"),
            one(1, event="workflow_dispatch"),
        )
        for invalid in invalid_rows:
            with self.subTest(invalid=invalid):
                runs, complete = a358.fetch_schedule_runs_for_art_day(
                    "fake-token", OBSERVED,
                    get=lambda *_: {"workflow_runs": [invalid], "total_count": 1},
                )
                self.assertFalse(complete)
                self.assertEqual([], runs)

    def test_no_false_p1_from_api_incompleteness(self):
        result = a358.classify([], OBSERVED, workflow_state="active",
                                api_day_covered=False)
        self.assertEqual("NO_VERIFICADO", result["status"])
        self.assertEqual("SCHEDULE_API_DAY_INCOMPLETE", result["reason"])
        self.assertEqual(0, result["genuine_runs_today"])

    def test_manual_event_can_never_replace_genuine_schedule(self):
        manual = one(10, event="workflow_dispatch")
        result = a358.classify([manual], OBSERVED, api_day_covered=True)
        self.assertEqual(0, result["genuine_runs_today"])
        self.assertEqual("P1_SCHEDULE_SILENT", result["status"])

    def test_reply_over_512_kib_still_fails_safely_without_network(self):
        oversized = b"x" * (a358.MAX_REPLY + 1)
        with patch.object(a358, "urlopen", return_value=io.BytesIO(oversized)):
            with self.assertRaises(a358.A358SafeError) as ctx:
                a358._get(
                    "https://api.github.com/repos/mbalbo2023/Porota-trading/actions/"
                    "workflows/384354765/runs?per_page=10",
                    "fake-token",
                )
        self.assertEqual("A358_API_RESPONSE_TOO_LARGE", ctx.exception.code)
        self.assertEqual("A358_API_RESPONSE_TOO_LARGE",
                         a358.safe_error_class(ctx.exception))


if __name__ == "__main__":
    unittest.main()
