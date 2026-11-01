import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from scripts import rc6_action4_weekly_consumer as consumer


TZ = ZoneInfo("America/Argentina/Buenos_Aires")
PROVENANCE = {"workflow_commit": "a" * 40, "image_label_commit": "b" * 40,
              "container_id": "c" * 64, "image_id": "sha256:" + "d" * 64}


class Response:
    def __init__(self, status, value):
        self.status = status
        self.value = json.dumps(value).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, _limit):
        return self.value


def source():
    now = datetime.now(timezone.utc)
    end = (now - timedelta(minutes=1)).astimezone(TZ)
    return {"schema": "RC6_ACTION4_WEEKLY_SOURCE_V1", "status": "COMPLETE",
            "source": "/api/reports/action4/source", "mode": "PRODUCTION_PAPER",
            "real_orders_sent": 0, "generated_at_utc": now.isoformat(),
            "max_rows_per_set": 500,
            "window": {"from_local": (end - timedelta(days=7)).isoformat(),
                       "to_local": end.isoformat()},
            "counts": {"closed": 0, "opened": 0, "decisions": 2},
            "operations": [], "decisions_by_action": {"HOLD": 1, "BLOCKED_SOURCE": 1},
            "pnl_by_currency": {}}


def test_weekly_action_uses_only_http_and_reads_back_exact_published_artifact():
    data = source()
    calls = []
    published = []

    def open_request(request, timeout):
        assert timeout == 20
        assert request.headers["Authorization"] == "Bearer fixture-token"
        calls.append((request.get_method(), request.full_url))
        if len(calls) == 1:
            return Response(200, data)
        if len(calls) == 2:
            published.append(json.loads(request.data))
            return Response(200, {"status": "STORED"})
        return Response(200, published[0])

    result = consumer.run(token="fixture-token", run_id=123, run_number=4,
                          **PROVENANCE, opener=open_request)
    assert result["status"] == "GREEN"
    assert result["source_counts"]["decisions"] == 2
    assert result["database_access_from_action"] == "NO"
    assert calls == [("GET", consumer.BASE + "/api/reports/action4/source"),
                     ("POST", consumer.BASE + "/api/reports/action4"),
                     ("GET", consumer.BASE + "/api/reports/action4")]
    assert published[0]["decision_snapshot_scope"] == "COMPLETE_PAPER_DECISIONS_TABLE_WINDOW_ONLY"
    assert published[0]["origin"]["image_label_commit"] == "b" * 40
    assert published[0]["origin"]["workflow_commit"] == "a" * 40
    assert published[0]["separation"]["hold"] == 1
    assert published[0]["separation"]["rejected_blocked"] == 1


def test_weekly_action_never_posts_a_partial_or_unsafe_source():
    calls = []

    def partial(request, timeout):
        calls.append(request.get_method())
        return Response(206, {"status": "PARTIAL", "reason": "WINDOW_ROW_BUDGET_EXCEEDED",
                              "lower_bound_rows": {"decisions": 501}})

    result = consumer.run(token="fixture-token", run_id=123, run_number=4,
                          **PROVENANCE, opener=partial)
    assert result["status"] == "PARTIAL" and calls == ["GET"]

    data = {**source(), "real_orders_sent": 1}
    calls.clear()

    def unsafe(request, timeout):
        calls.append(request.get_method())
        return Response(200, data)

    with pytest.raises(ValueError, match="ACTION4_WEEKLY_SOURCE_INCOMPLETE"):
        consumer.run(token="fixture-token", run_id=123, run_number=4,
                     **PROVENANCE, opener=unsafe)
    assert calls == ["GET"]
