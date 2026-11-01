#!/usr/bin/env python3
"""Consume the bounded authenticated RC6 dashboard source and publish Action 4.

GitHub Actions invokes this inside the already installed dashboard image.  It
uses only HTTP and never opens SQLite, sends an order, or reads a broker token.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone

from rc6_action4_audit import (WEEKLY_SOURCE_PATH, validate_weekly_payload,
                               weekly_payload_from_source)


BASE = "http://127.0.0.1:8000"


def _request(path, token, *, method="GET", payload=None, opener=urllib.request.urlopen):
    data = None if payload is None else json.dumps(payload, sort_keys=True,
                                                    ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Authorization": "Bearer " + token, "Cache-Control": "no-cache",
                 **({"Content-Type": "application/json"} if data is not None else {})})
    with opener(request, timeout=20) as response:
        raw = response.read(5 * 1024 * 1024 + 1)
        if len(raw) > 5 * 1024 * 1024:
            raise ValueError("ACTION4_WEEKLY_HTTP_BUDGET_EXCEEDED")
        return response.status, json.loads(raw.decode("utf-8"))


def run(*, token, run_id, run_number, workflow_commit, image_label_commit,
        container_id, image_id, opener=urllib.request.urlopen):
    if (not token or not str(run_id).isdigit() or not str(run_number).isdigit() or
            not re.fullmatch(r"[0-9a-f]{40}", str(workflow_commit)) or
            not re.fullmatch(r"[0-9a-f]{40}", str(image_label_commit)) or
            not re.fullmatch(r"[0-9a-f]{64}", str(container_id)) or
            not re.fullmatch(r"sha256:[0-9a-f]{64}", str(image_id))):
        raise ValueError("ACTION4_WEEKLY_ACTION_IDENTITY_UNVERIFIED")
    status, source = _request(WEEKLY_SOURCE_PATH, token, opener=opener)
    if (status == 206 and source.get("status") == "PARTIAL"):
        return {"status": "PARTIAL", "reason": source.get("reason"),
                "source_counts_lower_bound": source.get("lower_bound_rows")}
    if status != 200:
        raise ValueError("ACTION4_WEEKLY_SOURCE_HTTP_UNVERIFIED")
    payload = weekly_payload_from_source(source, run_id=run_id,
                                         run_number=run_number,
                                         workflow_commit=workflow_commit,
                                         image_label_commit=image_label_commit,
                                         container_id=container_id, image_id=image_id)
    validate_weekly_payload(payload, now=datetime.now(timezone.utc))
    code, receipt = _request("/api/reports/action4", token, method="POST",
                             payload=payload, opener=opener)
    if code != 200 or receipt.get("status") not in {"STORED", "ALREADY_STORED"}:
        raise ValueError("ACTION4_WEEKLY_PUBLISH_UNVERIFIED")
    code, stored = _request("/api/reports/action4", token, opener=opener)
    if code != 200 or stored != payload:
        raise ValueError("ACTION4_WEEKLY_DOWNLOAD_MISMATCH")
    return {"status": "GREEN", "origin_run_id": str(run_id),
            "source_sha256": payload["source_sha256"],
            "window": payload["window"], "source_counts": payload["source_counts"],
            "real_orders_sent": 0, "database_access_from_action": "NO"}


def main():
    result = run(token=os.getenv("DASHBOARD_ACCESS_TOKEN", ""),
                 run_id=os.getenv("ACTION_RUN_ID", ""),
                 run_number=os.getenv("ACTION_RUN_NUMBER", ""),
                 workflow_commit=os.getenv("ACTION_WORKFLOW_COMMIT", ""),
                 image_label_commit=os.getenv("ACTION_IMAGE_LABEL_COMMIT", ""),
                 container_id=os.getenv("ACTION_CONTAINER_ID", ""),
                 image_id=os.getenv("ACTION_IMAGE_ID", ""))
    print("RC6_ACTION4_WEEKLY=" + json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "GREEN" else 2


if __name__ == "__main__":
    sys.exit(main())
