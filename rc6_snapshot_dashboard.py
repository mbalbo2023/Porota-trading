"""Authenticated read-only endpoint for the latest RC6 operational snapshot."""
from __future__ import annotations

from pathlib import Path

from fastapi import Header, HTTPException, Query, Request
from fastapi.responses import FileResponse

def install(app, check_auth):
    @app.get("/api/rc6/snapshots/latest")
    def latest_snapshot(
        request: Request,
        token: str = Query(default=""),
        authorization: str | None = Header(default=None),
    ):
        try:
            check_auth(token, authorization, request.cookies.get("porota_dashboard_session"))
        except TypeError:
            check_auth(token, authorization)
        import bg_paper_dashboard as paper
        path = Path(paper.DB_PATH).parent / "snapshots" / "latest.json"
        if not path.is_file():
            raise HTTPException(404, "Snapshot RC6 no disponible")
        return FileResponse(
            path,
            media_type="application/json",
            filename="rc6_latest_snapshot.json",
            headers={"Cache-Control": "no-store"},
        )
