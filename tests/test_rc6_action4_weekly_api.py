from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import bg_paper_dashboard as dashboard
from be_paper_engine import PaperStore
from rc6_action4_audit import weekly_payload_from_source


def test_weekly_http_source_publish_download_and_idempotence(tmp_path, monkeypatch):
    database = tmp_path / "paper.db"
    PaperStore(str(database))
    monkeypatch.setattr(dashboard, "DB_PATH", str(database))
    monkeypatch.setattr(dashboard, "_installed", False)
    monkeypatch.setattr(dashboard, "_effective_mode", lambda: "PRODUCTION_PAPER")

    def authorize(_token, authorization, _cookie):
        if authorization != "Bearer fixture-token":
            raise HTTPException(401, "authentication required")

    app = FastAPI()
    dashboard.install(app, authorize)
    with TestClient(app) as client:
        assert client.get("/api/reports/action4/source").status_code == 401
        headers = {"Authorization": "Bearer fixture-token"}
        source_response = client.get("/api/reports/action4/source", headers=headers)
        assert source_response.status_code == 200
        source = source_response.json()
        assert source["status"] == "COMPLETE" and source["counts"]["closed"] == 0
        payload = weekly_payload_from_source(source, run_id=123, run_number=1,
                                             workflow_commit="a" * 40,
                                             image_label_commit="b" * 40,
                                             container_id="c" * 64,
                                             image_id="sha256:" + "d" * 64)
        posted = client.post("/api/reports/action4", headers=headers, json=payload)
        assert posted.status_code == 200 and posted.json()["status"] == "STORED"
        same = client.post("/api/reports/action4", headers=headers, json=payload)
        assert same.status_code == 200 and same.json()["status"] == "ALREADY_STORED"
        changed_source = {**source, "generated_at_utc":
                          (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat()}
        changed = weekly_payload_from_source(
            changed_source, run_id=123, run_number=1, workflow_commit="a" * 40,
            image_label_commit="b" * 40, container_id="c" * 64,
            image_id="sha256:" + "d" * 64)
        assert client.post("/api/reports/action4", headers=headers,
                           json=changed).status_code == 409
        downloaded = client.get("/api/reports/action4", headers=headers)
        assert downloaded.status_code == 200 and downloaded.json() == payload

        monkeypatch.setattr(dashboard, "build_weekly_source", lambda *_: {
            "status": "PARTIAL", "reason": "WINDOW_ROW_BUDGET_EXCEEDED"})
        partial = client.get("/api/reports/action4/source", headers=headers)
        assert partial.status_code == 206
        assert partial.json()["reason"] == "WINDOW_ROW_BUDGET_EXCEEDED"
