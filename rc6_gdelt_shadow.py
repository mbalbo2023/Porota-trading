"""GDELT DOC v2 en modo Shadow para RC6.

La red se usa únicamente en refresh().  El motor y el dashboard consumen
collect(), que lee el cache local.  Ninguna salida de este módulo tiene
autoridad sobre entradas, salidas, tamaño ni órdenes.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlencode
from urllib.request import Request, urlopen

CACHE_VERSION = 1
DEFAULT_ENDPOINT = "https://api.gdeltproject.org/api/v2/doc/doc"
DEFAULT_QUERY = "Argentina OR BYMA"
MAX_RESULTS = 25


def cache_path(root: Path | str | None = None) -> Path:
    if root is not None:
        return Path(root) / "gdelt_shadow_latest.json"
    configured = os.getenv("POROTA_GDELT_CACHE_PATH", "").strip()
    return Path(configured) if configured else Path("/app/data/macro/gdelt_shadow_latest.json")


def _write_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                            prefix=".gdelt-", suffix=".tmp", delete=False) as handle:
        json.dump(payload, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    os.replace(temporary, path)


def _article(item: dict) -> dict | None:
    url = str(item.get("url") or "").strip()
    title = str(item.get("title") or "").strip()
    if not url or not title:
        return None
    return {
        "id": hashlib.sha256((url + "\n" + title).encode("utf-8")).hexdigest()[:24],
        "url": url,
        "title": title[:500],
        "domain": str(item.get("domain") or "")[:160],
        "language": str(item.get("language") or "")[:48],
        "seendate": str(item.get("seendate") or "")[:64],
        "socialimage": str(item.get("socialimage") or "")[:500],
    }


def refresh(root: Path | str | None = None, *, opener=urlopen) -> dict:
    """Consulta GDELT en una ventana limitada y publica un cache read-only."""
    query = os.getenv("POROTA_GDELT_QUERY", DEFAULT_QUERY).strip() or DEFAULT_QUERY
    timeout = max(2, min(int(os.getenv("POROTA_GDELT_TIMEOUT_SECONDS", "8")), 20))
    params = {
        "query": query,
        "mode": "ArtList",
        "format": "json",
        "maxrecords": str(MAX_RESULTS),
        "timespan": "1d",
        "sort": "HybridRel",
    }
    now = datetime.now(timezone.utc).isoformat()
    try:
        request = Request(DEFAULT_ENDPOINT + "?" + urlencode(params),
                          headers={"User-Agent": "Porota-RC6-Shadow/1.0"})
        with opener(request, timeout=timeout) as response:
            raw = json.loads(response.read().decode("utf-8"))
        seen: set[str] = set()
        articles = []
        for item in raw.get("articles", []) if isinstance(raw, dict) else []:
            row = _article(item) if isinstance(item, dict) else None
            if row and row["id"] not in seen:
                seen.add(row["id"])
                articles.append(row)
            if len(articles) >= MAX_RESULTS:
                break
        payload = {
            "schema_version": CACHE_VERSION,
            "refreshed_at": now,
            "mode": "SHADOW",
            "state": "READY" if articles else "INSUFFICIENT_DATA",
            "decision_effect": "OBSERVE_ONLY",
            "source": "GDELT_DOC_V2",
            "query": query,
            "window": "1d",
            "articles": articles,
        }
    except Exception as exc:
        payload = {
            "schema_version": CACHE_VERSION,
            "refreshed_at": now,
            "mode": "SHADOW",
            "state": "UNAVAILABLE",
            "decision_effect": "OBSERVE_ONLY",
            "source": "GDELT_DOC_V2",
            "query": query,
            "articles": [],
            "reason": f"{type(exc).__name__}:{str(exc)[:180]}",
        }
    _write_atomic(cache_path(root), payload)
    return payload


def collect(root: Path | str | None = None) -> dict:
    """Lee exclusivamente la última observación local; jamás accede a red."""
    try:
        payload = json.loads(cache_path(root).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        payload = {}
    if payload.get("schema_version") != CACHE_VERSION:
        return {"mode": "SHADOW", "state": "UNAVAILABLE",
                "decision_effect": "OBSERVE_ONLY", "source": "GDELT_DOC_V2",
                "reason": "CACHE_MISSING_OR_INVALID", "articles": []}
    return {
        "mode": "SHADOW",
        "state": payload.get("state", "UNAVAILABLE"),
        "decision_effect": "OBSERVE_ONLY",
        "source": "GDELT_DOC_V2",
        "refreshed_at": payload.get("refreshed_at"),
        "query": payload.get("query"),
        "articles_count": len(payload.get("articles") or []),
    }
