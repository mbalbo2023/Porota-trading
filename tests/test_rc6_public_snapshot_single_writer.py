"""A145: host pipeline owns the public JSON; scanner is a read-only consumer."""

import json
import os
import time

import bf_production_paper_observer as observer
from be_paper_engine import PaperStore


def _health(store):
    with store.connect() as c:
        return c.execute("SELECT state,detail FROM api_health "
                         "WHERE component='BYMA_STRUCTURED_CAPTURE'").fetchone()


def test_scanner_structured_capture_does_not_re_fetch_or_rewrite(tmp_path, monkeypatch):
    store = PaperStore(str(tmp_path / "paper.db"))
    observer._support_schema(store)
    snapshot = tmp_path / "rc6_public_sources_latest.json"
    snapshot.write_text(json.dumps({"sources": [{"source": "BYMA",
                                             "status": "SCRAPED_PUBLIC_DATA",
                                             "record_count": 5}]}), encoding="utf-8")
    before = (snapshot.read_bytes(), snapshot.stat().st_mtime_ns)
    monkeypatch.setattr(observer, "PUBLIC_CAPTURE_PATH", snapshot)
    monkeypatch.setattr(observer, "urlopen", lambda *_args, **_kw: (
        (_ for _ in ()).throw(AssertionError("No network for structured observation"))))

    observer._public_probe(store, structured=True)

    assert (snapshot.read_bytes(), snapshot.stat().st_mtime_ns) == before
    state, detail = _health(store)
    assert state == "VERDE"
    assert "single_writer=host_collector" in detail


def test_scanner_missing_or_stale_public_snapshot_fails_closed(tmp_path, monkeypatch):
    store = PaperStore(str(tmp_path / "paper.db"))
    observer._support_schema(store)
    snapshot = tmp_path / "rc6_public_sources_latest.json"
    monkeypatch.setattr(observer, "PUBLIC_CAPTURE_PATH", snapshot)
    observer._public_probe(store, structured=True)
    assert _health(store)[0] == "ROJO"

    snapshot.write_text('{"sources": []}', encoding="utf-8")
    old = time.time() - observer.PUBLIC_CAPTURE_MAX_AGE_SECONDS - 10
    os.utime(snapshot, (old, old))
    observer._public_probe(store, structured=True)
    assert _health(store)[0] == "ROJO"


def test_scanner_oversize_public_snapshot_fails_closed(tmp_path, monkeypatch):
    store = PaperStore(str(tmp_path / "paper.db"))
    observer._support_schema(store)
    snapshot = tmp_path / "rc6_public_sources_latest.json"
    snapshot.write_text('{"sources": []}', encoding="utf-8")
    monkeypatch.setattr(observer, "PUBLIC_CAPTURE_PATH", snapshot)
    monkeypatch.setattr(observer, "PUBLIC_CAPTURE_MAX_BYTES", 2)

    observer._public_probe(store, structured=True)

    assert _health(store)[0] == "ROJO"
