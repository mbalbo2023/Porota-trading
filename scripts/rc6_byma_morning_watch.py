#!/usr/bin/env python3
"""Morning BYMA authority watch for RC6 (read-only, OBSERVE_ONLY).

The job fetches only official BYMA pages plus the existing public structured
capture, extracts stable decision-relevant signals, and compares them with the
previous morning snapshot.  A detected change never edits policy, never grants
READY_PAPER and never calls an order route.  It creates evidence for pre-open
review so official changes are noticed before the trading window.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable
from urllib.request import Request, urlopen


SCHEMA = "porota-byma-morning-watch-v1"
DEFAULT_ROOT = Path("/opt/porota-trading/data/market")
URLS = {
    "COMMUNICATIONS": "https://www.byma.com.ar/mercado/regulacion/circulares-y-comunicados",
    "HOURS": "https://www.byma.com.ar/mercado/horarios",
    "CALENDAR": "https://www.byma.com.ar/mercado/calendario-bursatil",
    "OPTIONS": "https://www.byma.com.ar/productos/productos-financieros/opciones",
}
KEYWORDS = (
    "horario", "negociacion", "negociación", "liquidacion", "liquidación",
    "opciones", "series de opciones", "caucion", "caución", "futuros",
    "calendario", "vencimiento", "comunicado", "circular",
)


class VisibleText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag.lower() in {"script", "style", "svg", "noscript"}:
            self._skip += 1

    def handle_endtag(self, tag):
        if tag.lower() in {"script", "style", "svg", "noscript"} and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            value = " ".join(str(data).split())
            if value:
                self.parts.append(value)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _visible_text(body: bytes) -> str:
    parser = VisibleText()
    parser.feed(body.decode("utf-8", errors="replace"))
    return " ".join(parser.parts)


def _stable_page_signature(body: bytes) -> dict:
    text = html.unescape(_visible_text(body))
    normalized = " ".join(text.split())
    communication_numbers = sorted(
        set(re.findall(r"(?<!\d)(?:18|19)\d{3}(?!\d)", normalized)),
        reverse=True,
    )
    sentences = re.split(r"(?<=[.!?])\s+|\s{2,}", normalized)
    relevant = sorted(set(
        item.strip()[:500]
        for item in sentences
        if item.strip() and any(word in item.lower() for word in KEYWORDS)
    ))
    stable = json.dumps({
        "communications": communication_numbers[:80],
        "relevant": relevant[:250],
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "sha256": _sha(stable),
        "communication_numbers": communication_numbers[:80],
        "relevant_excerpt_count": len(relevant[:250]),
    }


def _default_fetch(url: str) -> bytes:
    request = Request(url, headers={
        "User-Agent": "Porota-RC6-BYMA-Morning-Watch/1.0",
        "Accept": "text/html,application/json;q=0.9",
    })
    with urlopen(request, timeout=20) as response:
        if not 200 <= int(getattr(response, "status", 200)) < 400:
            raise RuntimeError(f"HTTP_{getattr(response, 'status', 'UNKNOWN')}")
        return response.read(2_000_000)


def _structured_byma_signature(snapshot_path: Path) -> dict:
    """Read the exact BYMA block produced by the shared public-source scraper.

    This function performs no network access.  Instrument discovery belongs to
    rc6_public_source_capture; the morning watcher only fingerprints that same
    persisted result and watches the official authority pages for rule changes.
    """
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    block = next((row for row in payload.get("sources", [])
                  if str(row.get("source") or "").upper() == "BYMA"), None)
    if not isinstance(block, dict):
        raise ValueError("BYMA_STRUCTURED_BLOCK_MISSING")
    records = block.get("records", []) if isinstance(block.get("records"), list) else []
    identities = sorted({
        "|".join((
            str(row.get("family") or "").upper(),
            str(row.get("symbol") or row.get("ticker") or "").upper(),
            str(row.get("currency") or "").upper(),
            str(row.get("maturity") or row.get("settlement") or "").upper(),
        ))
        for row in records if isinstance(row, dict)
        and str(row.get("symbol") or row.get("ticker") or "").strip()
    })
    return {
        "status": block.get("status"),
        "record_count": len(records),
        "identity_count": len(identities),
        "identity_sha256": _sha("\n".join(identities)),
        "observed_at": block.get("observed_at"),
        "capture_collected_at": payload.get("collected_at"),
        "scrape_method": block.get("scrape_method"),
        "endpoints": block.get("endpoints") or [],
        "errors": block.get("errors") or ([block.get("error")] if block.get("error") else []),
    }


def collect(*, fetch: Callable[[str], bytes] = _default_fetch,
            include_structured: bool = True,
            structured_snapshot: Path | None = None) -> dict:
    observed = _now()
    pages = {}
    errors = []
    for name, url in URLS.items():
        try:
            pages[name] = {
                "url": url,
                "status": "OK",
                **_stable_page_signature(fetch(url)),
            }
        except Exception as exc:
            pages[name] = {"url": url, "status": "ERROR",
                           "error": f"{type(exc).__name__}:{str(exc)[:180]}"}
            errors.append(f"{name}:{type(exc).__name__}")
    structured = {}
    if include_structured:
        try:
            snapshot = structured_snapshot or (DEFAULT_ROOT / "rc6_public_sources_latest.json")
            structured = _structured_byma_signature(Path(snapshot))
            if structured.get("status") != "SCRAPED_PUBLIC_DATA":
                errors.append("OPEN_DATA:BYMA_NOT_STRUCTURED")
            if int(structured.get("record_count") or 0) <= 0:
                errors.append("OPEN_DATA:BYMA_EMPTY")
            if structured.get("errors"):
                errors.extend("OPEN_DATA:" + str(item) for item in structured.get("errors", []))
        except Exception as exc:
            structured = {"status": "ERROR",
                          "error": f"{type(exc).__name__}:{str(exc)[:180]}"}
            errors.append(f"OPEN_DATA:{type(exc).__name__}")
    signature_payload = {
        "pages": {
            key: {
                "sha256": value.get("sha256"),
                "communication_numbers": value.get("communication_numbers", []),
                "status": value.get("status"),
            }
            for key, value in sorted(pages.items())
        },
        "structured": {
            "status": structured.get("status"),
            "record_count": structured.get("record_count"),
            "identity_count": structured.get("identity_count"),
            "identity_sha256": structured.get("identity_sha256"),
        },
    }
    return {
        "schema": SCHEMA,
        "observed_at": observed,
        "pages": pages,
        "structured_byma": structured,
        "signature": _sha(json.dumps(signature_payload, sort_keys=True, ensure_ascii=False)),
        "errors": errors,
        "decision_effect": "OBSERVE_ONLY",
        "real_money_authorized": False,
    }


def compare(previous: dict | None, current: dict) -> dict:
    if not previous:
        return {"state": "BASELINE_CREATED", "changed_components": []}
    changed = []
    previous_pages = previous.get("pages") if isinstance(previous.get("pages"), dict) else {}
    current_pages = current.get("pages") if isinstance(current.get("pages"), dict) else {}
    for name in sorted(set(previous_pages) | set(current_pages)):
        before = previous_pages.get(name, {})
        after = current_pages.get(name, {})
        if (before.get("status"), before.get("sha256"), before.get("communication_numbers")) != (
                after.get("status"), after.get("sha256"), after.get("communication_numbers")):
            changed.append(name)
    before_struct = previous.get("structured_byma") or {}
    after_struct = current.get("structured_byma") or {}
    if (
        before_struct.get("status"),
        before_struct.get("identity_count"),
        before_struct.get("identity_sha256"),
    ) != (
        after_struct.get("status"),
        after_struct.get("identity_count"),
        after_struct.get("identity_sha256"),
    ):
        changed.append("OPEN_DATA")
    if current.get("errors"):
        state = "DEGRADED"
    elif changed:
        state = "CHANGED_REVIEW_REQUIRED"
    else:
        state = "NO_CHANGE"
    return {"state": state, "changed_components": changed}


def persist(root: Path, current: dict) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    latest = root / "byma_morning_watch_latest.json"
    history = root / "byma_morning_watch_history.jsonl"
    previous = None
    if latest.exists():
        try:
            previous = json.loads(latest.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            previous = None
    verdict = compare(previous, current)
    payload = dict(current, **verdict)
    tmp = latest.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    tmp.replace(latest)
    with history.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "observed_at": payload["observed_at"],
            "state": payload["state"],
            "changed_components": payload["changed_components"],
            "signature": payload["signature"],
            "errors": payload.get("errors", []),
            "decision_effect": "OBSERVE_ONLY",
        }, ensure_ascii=False, sort_keys=True) + "\n")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(DEFAULT_ROOT))
    parser.add_argument("--structured-snapshot", default="")
    parser.add_argument("--no-structured", action="store_true")
    args = parser.parse_args()
    root = Path(args.root)
    structured_snapshot = (
        Path(args.structured_snapshot)
        if args.structured_snapshot
        else root / "rc6_public_sources_latest.json"
    )
    payload = persist(
        root,
        collect(
            include_structured=not args.no_structured,
            structured_snapshot=structured_snapshot,
        ),
    )
    print("BYMA_MORNING_WATCH=" + json.dumps({
        "state": payload["state"],
        "changed_components": payload["changed_components"],
        "errors": payload.get("errors", []),
        "observed_at": payload["observed_at"],
        "latest": str(Path(args.root) / "byma_morning_watch_latest.json"),
        "decision_effect": "OBSERVE_ONLY",
        "real_money_authorized": False,
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
