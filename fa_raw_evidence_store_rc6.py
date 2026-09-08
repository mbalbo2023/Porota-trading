"""RC6 Phase A: content-addressed raw evidence store for PPI History.

Additive only: no legacy deletion/migration. Raw objects are immutable,
content-addressed, atomically written and verified before reuse.
"""
from __future__ import annotations
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl, gzip, json, os, sqlite3, tempfile
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterator
RAW_STORAGE_ENV='PPI_HISTORY_RAW_STORAGE_MODE'; COORDINATOR_ENV='PPI_HISTORY_COORDINATOR_MODE'; EVIDENCE_ROOT_ENV='PPI_HISTORY_EVIDENCE_ROOT'; EXTERNAL_V1='EXTERNAL_V1'; LEGACY='LEGACY'
MANIFEST_DDL="""
CREATE TABLE IF NOT EXISTS ingest_manifests_v1(attempt_id TEXT PRIMARY KEY,source TEXT NOT NULL,symbol TEXT NOT NULL,instrument_type TEXT NOT NULL,settlement TEXT NOT NULL,requested_from TEXT,requested_to TEXT,attempted_at TEXT NOT NULL,quality TEXT NOT NULL,provider_rows INTEGER NOT NULL DEFAULT 0,valid_rows INTEGER NOT NULL DEFAULT 0,raw_sha256 TEXT NOT NULL,raw_object_relpath TEXT NOT NULL,raw_bytes INTEGER NOT NULL,compressed_bytes INTEGER NOT NULL,object_created INTEGER NOT NULL,legacy_row_key TEXT NOT NULL,metadata_json TEXT NOT NULL DEFAULT '{}',recorded_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_ingest_manifest_identity_v1 ON ingest_manifests_v1(symbol,instrument_type,settlement,attempted_at DESC);
CREATE INDEX IF NOT EXISTS idx_ingest_manifest_raw_v1 ON ingest_manifests_v1(raw_sha256,attempted_at DESC);
CREATE TABLE IF NOT EXISTS ingest_manifest_events_v1(id INTEGER PRIMARY KEY AUTOINCREMENT,attempt_id TEXT NOT NULL,event_at TEXT NOT NULL,event_type TEXT NOT NULL,detail_json TEXT NOT NULL DEFAULT '{}',FOREIGN KEY(attempt_id) REFERENCES ingest_manifests_v1(attempt_id));
CREATE INDEX IF NOT EXISTS idx_ingest_manifest_events_v1 ON ingest_manifest_events_v1(attempt_id,id);
"""
def _canonical_bytes(value:Any)->bytes:return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),default=str).encode('utf-8')
def _stamp(value:Any|None=None)->str:
    if value in (None,''):return datetime.now(timezone.utc).isoformat()
    parsed=value if isinstance(value,datetime) else datetime.fromisoformat(str(value).replace('Z','+00:00'))
    if parsed.tzinfo is None:parsed=parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat()
def raw_storage_mode(environ=None):
    env=os.environ if environ is None else environ; mode=str(env.get(RAW_STORAGE_ENV,LEGACY) or LEGACY).upper().strip()
    if mode not in {LEGACY,EXTERNAL_V1}:raise ValueError('PPI_HISTORY_RAW_STORAGE_MODE_INVALID')
    return mode
def coordinator_mode(environ=None):
    env=os.environ if environ is None else environ; mode=str(env.get(COORDINATOR_ENV,LEGACY) or LEGACY).upper().strip()
    if mode not in {LEGACY,EXTERNAL_V1}:raise ValueError('PPI_HISTORY_COORDINATOR_MODE_INVALID')
    return mode
def evidence_root(environ=None):
    env=os.environ if environ is None else environ; configured=str(env.get(EVIDENCE_ROOT_ENV,'') or '').strip()
    return Path(configured).expanduser().resolve() if configured else (Path(env.get('DATA_DIR','data'))/'evidence'/'ppi_history_v1').resolve()
def manifest_path(environ=None):return evidence_root(environ)/'manifest_v1.db'
def _connect_manifest(environ=None):
    path=manifest_path(environ); path.parent.mkdir(parents=True,exist_ok=True); c=sqlite3.connect(path,timeout=30); c.row_factory=sqlite3.Row; c.execute('PRAGMA journal_mode=WAL'); c.execute('PRAGMA synchronous=FULL'); c.execute('PRAGMA busy_timeout=30000'); c.execute('PRAGMA foreign_keys=ON'); c.executescript(MANIFEST_DDL); return c
def _raw_payload_from_wrapper(wrapper):
    if not isinstance(wrapper,dict):raise ValueError('PPI_HISTORY_EVIDENCE_WRAPPER_NOT_OBJECT')
    encoded=wrapper.get('payload_json')
    if not isinstance(encoded,str):raise ValueError('PPI_HISTORY_EVIDENCE_PAYLOAD_JSON_MISSING')
    try:return json.loads(encoded)
    except (TypeError,ValueError) as exc:raise ValueError('PPI_HISTORY_EVIDENCE_PAYLOAD_JSON_INVALID') from exc
def _object_relpath(digest):return Path('objects')/'sha256'/digest[:2]/f'{digest}.json.gz'
def _verify_object(path,digest):
    compressed_bytes=path.stat().st_size
    try:
        with gzip.open(path,'rb') as h:raw=h.read()
    except (OSError,EOFError) as exc:raise ValueError('PPI_HISTORY_RAW_OBJECT_CORRUPT') from exc
    if sha256(raw).hexdigest()!=digest:raise ValueError('PPI_HISTORY_RAW_OBJECT_HASH_MISMATCH')
    return len(raw),compressed_bytes
def _atomic_object_write(path,raw):
    path.parent.mkdir(parents=True,exist_ok=True); compressed=gzip.compress(raw,compresslevel=6,mtime=0); fd,tmp_name=tempfile.mkstemp(prefix='.porota-raw-',suffix='.tmp',dir=path.parent); tmp=Path(tmp_name)
    try:
        with os.fdopen(fd,'wb') as h:h.write(compressed);h.flush();os.fsync(h.fileno())
        os.replace(tmp,path); dfd=os.open(path.parent,os.O_RDONLY)
        try:os.fsync(dfd)
        finally:os.close(dfd)
        return len(compressed)
    finally:
        if tmp.exists():tmp.unlink()
@dataclass(frozen=True)
class RawEvidenceReceipt:
    attempt_id:str;raw_sha256:str;object_relpath:str;raw_bytes:int;compressed_bytes:int;object_created:bool;manifest_inserted:bool
def archive_ppi_history_wrapper(*,row_key:str,wrapper:dict,recorded_at:Any,quality:str,environ=None):
    if raw_storage_mode(environ)!=EXTERNAL_V1:raise RuntimeError('PPI_HISTORY_EXTERNAL_EVIDENCE_NOT_ENABLED')
    raw_payload=_raw_payload_from_wrapper(wrapper); raw_bytes=_canonical_bytes(raw_payload); digest=sha256(raw_bytes).hexdigest(); root=evidence_root(environ); relpath=_object_relpath(digest); object_path=root/relpath; object_created=False
    if object_path.exists(): observed_raw_bytes,compressed_bytes=_verify_object(object_path,digest)
    else:
        compressed_bytes=_atomic_object_write(object_path,raw_bytes); object_created=True; observed_raw_bytes,observed_compressed_bytes=_verify_object(object_path,digest)
        if observed_raw_bytes!=len(raw_bytes) or observed_compressed_bytes!=compressed_bytes:raise ValueError('PPI_HISTORY_RAW_OBJECT_POSTWRITE_VERIFY_FAILED')
    symbol=str(wrapper.get('symbol') or '').upper().strip(); instrument_type=str(wrapper.get('asset_class') or wrapper.get('instrument_type') or '').upper().strip(); settlement=str(wrapper.get('settlement') or '').upper().strip()
    if not symbol or not instrument_type or not settlement:raise ValueError('PPI_HISTORY_EVIDENCE_IDENTITY_INCOMPLETE')
    attempted_at=_stamp(recorded_at); legacy_row_key=str(row_key or '')
    if not legacy_row_key:raise ValueError('PPI_HISTORY_EVIDENCE_ROW_KEY_MISSING')
    attempt_id=sha256(_canonical_bytes(['PPI_HISTORY',legacy_row_key,digest,attempted_at])).hexdigest(); provider_rows=len(raw_payload) if isinstance(raw_payload,list) else 0; valid_rows=max(0,int(wrapper.get('valid_rows') or 0))
    metadata={'market_metadata':wrapper.get('metadata') if isinstance(wrapper.get('metadata'),dict) else {},'raw_encoding':'CANONICAL_JSON_GZIP_V1','ready_paper_implication':'NONE','legacy_archive_preserved':True,'legacy_new_writes':False}
    with _connect_manifest(environ) as c:
        cur=c.execute("INSERT OR IGNORE INTO ingest_manifests_v1(attempt_id,source,symbol,instrument_type,settlement,requested_from,requested_to,attempted_at,quality,provider_rows,valid_rows,raw_sha256,raw_object_relpath,raw_bytes,compressed_bytes,object_created,legacy_row_key,metadata_json,recorded_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(attempt_id,'PPI_HISTORY',symbol,instrument_type,settlement,str(wrapper.get('date_from') or '') or None,str(wrapper.get('date_to') or '') or None,attempted_at,str(quality or 'UNVERIFIED'),provider_rows,valid_rows,digest,str(relpath),len(raw_bytes),int(compressed_bytes),int(object_created),legacy_row_key,json.dumps(metadata,ensure_ascii=False,sort_keys=True,separators=(',',':'),default=str),_stamp()))
        manifest_inserted=cur.rowcount==1
        if manifest_inserted:c.execute("INSERT INTO ingest_manifest_events_v1(attempt_id,event_at,event_type,detail_json) VALUES(?,?,?,?)",(attempt_id,_stamp(),'RAW_STORED',json.dumps({'raw_sha256':digest,'object_created':object_created},sort_keys=True)))
    return RawEvidenceReceipt(attempt_id,digest,str(relpath),len(raw_bytes),int(compressed_bytes),object_created,manifest_inserted)
@dataclass(frozen=True)
class IngestLease:acquired:bool;path:str|None;reason:str
@contextmanager
def ppi_history_ingest_lease(*,environ=None)->Iterator[IngestLease]:
    if coordinator_mode(environ)!=EXTERNAL_V1:yield IngestLease(True,None,'LEGACY_NO_COORDINATOR');return
    root=evidence_root(environ);lock_dir=root/'locks';lock_dir.mkdir(parents=True,exist_ok=True);lock_path=lock_dir/'ppi_history_ingest_v1.lock';h=open(lock_path,'a+');acquired=False
    try:
        try:fcntl.flock(h.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB);acquired=True
        except BlockingIOError:yield IngestLease(False,str(lock_path),'ANOTHER_PPI_HISTORY_INGEST_ACTIVE');return
        yield IngestLease(True,str(lock_path),'ACQUIRED')
    finally:
        if acquired:fcntl.flock(h.fileno(),fcntl.LOCK_UN)
        h.close()
def manifest_metrics(*,environ=None):
    path=manifest_path(environ)
    if not path.exists():return {'attempts':0,'unique_raw_objects':0,'raw_bytes_referenced':0,'compressed_bytes_physical':0,'dedupe_ratio':0.0}
    with _connect_manifest(environ) as c:
        attempts=int(c.execute('SELECT COUNT(*) FROM ingest_manifests_v1').fetchone()[0]);unique=int(c.execute('SELECT COUNT(DISTINCT raw_sha256) FROM ingest_manifests_v1').fetchone()[0]);referenced=int(c.execute('SELECT COALESCE(SUM(raw_bytes),0) FROM ingest_manifests_v1').fetchone()[0]);physical=int(c.execute('SELECT COALESCE(SUM(compressed_bytes),0) FROM (SELECT raw_sha256,MAX(compressed_bytes) compressed_bytes FROM ingest_manifests_v1 GROUP BY raw_sha256)').fetchone()[0])
    return {'attempts':attempts,'unique_raw_objects':unique,'raw_bytes_referenced':referenced,'compressed_bytes_physical':physical,'dedupe_ratio':(attempts/unique) if unique else 0.0}
