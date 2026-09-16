from __future__ import annotations

import json

import rc6_gdelt_shadow as gdelt


def test_collect_reads_local_cache_only(tmp_path):
    path = gdelt.cache_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema_version": 1,
        "state": "READY",
        "refreshed_at": "2026-09-16T10:00:00+00:00",
        "query": "Argentina OR BYMA",
        "articles": [{"id": "a"}, {"id": "b"}],
    }), encoding="utf-8")

    result = gdelt.collect(tmp_path)

    assert result["mode"] == "SHADOW"
    assert result["decision_effect"] == "OBSERVE_ONLY"
    assert result["articles_count"] == 2


def test_refresh_failure_is_observe_only(tmp_path):
    def offline(*_args, **_kwargs):
        raise OSError("offline")

    result = gdelt.refresh(tmp_path, opener=offline)

    assert result["state"] == "UNAVAILABLE"
    assert result["decision_effect"] == "OBSERVE_ONLY"
