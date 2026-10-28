"""Byte-exact content-addressed archive for RC6 PPI historical evidence.

This module preserves the exact legacy ``historical_raw_archive.body_json``
representation while deduplicating the large embedded ``payload_json`` strings.
It performs no network or broker calls and contains no order capability.
"""
from __future__ import annotations

from datetime import datetime, timezone
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile

MODE_ENV = "PPI_HISTORY_RAW_STORAGE_MODE"
MODE = "EXTERNAL_EXACT_V1"
ROOT_ENV = "PPI_HISTORY_EVIDENCE_ROOT"

DDL = """
CREATE TABLE IF NOT EXISTS exact_attempts_v1(
  legacy_id INTEGER,
  origin TEXT NOT NULL,
  row_key TEXT NOT NULL,
  body_hash TEXT NOT NULL,
  recorded_at TEXT NOT NULL,
  quality TEXT NOT NULL,
  inner_sha256 TEXT NOT NULL,
  wrapper_json TEXT NOT NULL,
  body_sha256 TEXT NOT NULL,
  body_bytes INTEGER NOT NULL,
  inserted_at TEXT NOT NULL,
  PRIMARY KEY(origin,row_key,body_hash)
);
CREATE INDEX IF NOT EXISTS idx_exact_attempts_inner ON exact_attempts_v1(inner_sha256);
CREATE INDEX IF NOT EXISTS idx_exact_attempts_recorded ON exact_attempts_v1(recorded_at);
"""


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False, default=str)


def enabled(environ=None) -> bool:
    env = os.environ if environ is None else environ
    return str(env.get(MODE_ENV, "LEGACY") or "LEGACY").strip().upper() == MODE


def root(environ=None) -> Path:
    env = os.environ if environ is None else environ
    configured = str(env.get(ROOT_ENV, "") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path(env.get("DATA_DIR", "data")) / "evidence" / "ppi_history_exact_v1").resolve()


def manifest_path(environ=None) -> Path:
    return root(environ) / "manifest_exact_v1.db"


def object_path(digest: str, environ=None) -> Path:
    return root(environ) / "objects" / "sha256" / digest[:2] / f"{digest}.json.gz"


def _connect(environ=None) -> sqlite3.Connection:
    path = manifest_path(environ)
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path, timeout=60)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=FULL")
    c.execute("PRAGMA busy_timeout=60000")
    c.executescript(DDL)
    return c


def _atomic_gzip(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    packed = gzip.compress(raw, compresslevel=6, mtime=0)
    fd, name = tempfile.mkstemp(prefix=".porota-exact-", suffix=".tmp", dir=path.parent)
    tmp = Path(name)
    try:
        with os.fdopen(fd, "wb") as h:
            h.write(packed); h.flush(); os.fsync(h.fileno())
        os.replace(tmp, path)
        dfd = os.open(path.parent, os.O_RDONLY)
        try: os.fsync(dfd)
        finally: os.close(dfd)
    finally:
        if tmp.exists(): tmp.unlink()


def _put_inner(inner: str, environ=None) -> tuple[str, int, int, bool]:
    raw = inner.encode("utf-8", "surrogatepass")
    digest = hashlib.sha256(raw).hexdigest()
    path = object_path(digest, environ)
    created = False
    if not path.exists():
        _atomic_gzip(path, raw); created = True
    with gzip.open(path, "rb") as h:
        observed = h.read()
    if observed != raw or hashlib.sha256(observed).hexdigest() != digest:
        raise ValueError("PPI_HISTORY_EXACT_OBJECT_VERIFY_FAILED")
    return digest, len(raw), path.stat().st_size, created


def reconstruct(wrapper_json: str, inner_sha256: str, environ=None) -> str:
    wrapper = json.loads(wrapper_json)
    path = object_path(inner_sha256, environ)
    with gzip.open(path, "rb") as h:
        inner = h.read().decode("utf-8", "surrogatepass")
    if hashlib.sha256(inner.encode("utf-8", "surrogatepass")).hexdigest() != inner_sha256:
        raise ValueError("PPI_HISTORY_EXACT_OBJECT_HASH_MISMATCH")
    wrapper["payload_json"] = inner
    return canonical(wrapper)


def archive_exact(*, origin: str, row_key: str, body_hash: str,
                  recorded_at: str, quality: str, body_json: str,
                  legacy_id=None, environ=None) -> dict:
    outer = json.loads(body_json)
    if not isinstance(outer, dict) or not isinstance(outer.get("payload_json"), str):
        raise ValueError("PPI_HISTORY_EXACT_WRAPPER_INVALID")
    inner = outer.pop("payload_json")
    wrapper_json = canonical(outer)
    digest, raw_bytes, compressed_bytes, created = _put_inner(inner, environ)
    body_sha = hashlib.sha256(body_json.encode("utf-8", "surrogatepass")).hexdigest()
    rebuilt = reconstruct(wrapper_json, digest, environ)
    if rebuilt != body_json:
        raise ValueError("PPI_HISTORY_EXACT_ROUNDTRIP_MISMATCH")
    inserted_at = datetime.now(timezone.utc).isoformat()
    with _connect(environ) as c:
        c.execute("BEGIN IMMEDIATE")
        c.execute("""INSERT OR IGNORE INTO exact_attempts_v1
          (legacy_id,origin,row_key,body_hash,recorded_at,quality,inner_sha256,
           wrapper_json,body_sha256,body_bytes,inserted_at)
          VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
          (legacy_id, origin, row_key, body_hash, recorded_at, quality, digest,
           wrapper_json, body_sha, len(body_json.encode('utf-8','surrogatepass')), inserted_at))
    return {"inner_sha256": digest, "inner_raw_bytes": raw_bytes,
            "inner_compressed_bytes": compressed_bytes, "object_created": created,
            "body_sha256": body_sha}


def archive_wrapper(*, row_key: str, wrapper: dict, recorded_at: str,
                    quality: str, origin: str = "PPI_HISTORY", environ=None) -> dict:
    if not enabled(environ):
        raise RuntimeError("PPI_HISTORY_EXACT_EXTERNAL_NOT_ENABLED")
    body_json = canonical(wrapper)
    body_hash = hashlib.sha256(body_json.encode("utf-8")).hexdigest()
    return archive_exact(origin=origin, row_key=row_key, body_hash=body_hash,
                         recorded_at=recorded_at, quality=quality,
                         body_json=body_json, legacy_id=None, environ=environ)


def migrate_legacy(db_path: str, environ=None, progress_every: int = 1000) -> dict:
    src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=60)
    src.row_factory = sqlite3.Row
    total = inserted = 0
    try:
        for row in src.execute("SELECT id,origin,row_key,body_hash,recorded_at,quality,body_json FROM historical_raw_archive ORDER BY id"):
            total += 1
            archive_exact(origin=row['origin'], row_key=row['row_key'], body_hash=row['body_hash'],
                          recorded_at=row['recorded_at'], quality=row['quality'], body_json=row['body_json'],
                          legacy_id=row['id'], environ=environ)
            inserted += 1
            if progress_every and total % progress_every == 0:
                print(f"MIGRATE_PROGRESS={total}", flush=True)
    finally:
        src.close()
    return verify_legacy(db_path, environ) | {"migrated": inserted}


def verify_legacy(db_path: str, environ=None) -> dict:
    src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=60)
    src.row_factory = sqlite3.Row
    checked = mismatches = missing = 0
    try:
        with _connect(environ) as man:
            for row in src.execute("SELECT id,origin,row_key,body_hash,recorded_at,quality,body_json FROM historical_raw_archive ORDER BY id"):
                rec = man.execute("""SELECT * FROM exact_attempts_v1
                    WHERE origin=? AND row_key=? AND body_hash=?""",
                    (row['origin'], row['row_key'], row['body_hash'])).fetchone()
                if rec is None:
                    missing += 1; continue
                rebuilt = reconstruct(rec['wrapper_json'], rec['inner_sha256'], environ)
                checked += 1
                if (rebuilt != row['body_json'] or rec['recorded_at'] != row['recorded_at']
                    or rec['quality'] != row['quality'] or rec['body_hash'] != row['body_hash']):
                    mismatches += 1
    finally:
        src.close()
    return {"checked": checked, "missing": missing, "mismatches": mismatches,
            "evidence_lost": 0 if missing == 0 and mismatches == 0 else 1}


def metrics(environ=None) -> dict:
    r = root(environ)
    with _connect(environ) as c:
        attempts = c.execute("SELECT COUNT(*) FROM exact_attempts_v1").fetchone()[0]
        unique = c.execute("SELECT COUNT(DISTINCT inner_sha256) FROM exact_attempts_v1").fetchone()[0]
    object_bytes = sum(p.stat().st_size for p in (r / 'objects').rglob('*.gz')) if (r/'objects').exists() else 0
    manifest_bytes = manifest_path(environ).stat().st_size if manifest_path(environ).exists() else 0
    return {"attempts": attempts, "unique_objects": unique, "object_bytes": object_bytes,
            "manifest_bytes": manifest_bytes, "total_bytes": object_bytes + manifest_bytes}
