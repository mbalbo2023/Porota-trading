#!/usr/bin/env python3
"""Public, read-only RC6 PAPER snapshot server.

It deliberately exposes only the already-sanitized snapshot artifact.
It never opens the authenticated dashboard, observer database, PPI clients,
credentials, or any order route.
"""
from __future__ import annotations

import argparse
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DEFAULT_PATH = Path("/opt/porota-trading/data/paper_v17/snapshots/latest.json")

class SnapshotHandler(BaseHTTPRequestHandler):
    snapshot_path = DEFAULT_PATH

    def do_GET(self) -> None:
        if self.path.split("?", 1)[0] != "/latest.json":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            raw = self.snapshot_path.read_bytes()
            data = json.loads(raw.decode("utf-8"))
            if not isinstance(data, dict) or data.get("schema_version") != 1:
                raise ValueError("invalid snapshot")
        except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
            self.send_error(HTTPStatus.SERVICE_UNAVAILABLE)
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_HEAD(self) -> None:
        self.do_GET()

    def log_message(self, fmt: str, *args: object) -> None:
        print("RC6_PUBLIC_SNAPSHOT " + (fmt % args), flush=True)

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8089)
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_PATH)
    args = parser.parse_args()
    SnapshotHandler.snapshot_path = args.snapshot
    server = ThreadingHTTPServer((args.bind, args.port), SnapshotHandler)
    print(f"RC6_PUBLIC_SNAPSHOT_LISTENING={args.bind}:{args.port}", flush=True)
    server.serve_forever()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
