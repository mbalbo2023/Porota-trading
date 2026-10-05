"""U24: source tree, WAL bytes, deadlines, aliases and bounded private reads."""
from pathlib import Path
import hashlib
import os
import sqlite3
import time

import pytest

from rc6_audit_evidence.sqlite_snapshot import SnapshotError, readonly_copy
from rc6_audit_evidence.package import Budget, Limits, readonly_snapshot


def inventory(folder):
    result={}
    for p in folder.iterdir():
        info=p.stat()
        descriptor=os.open(p,os.O_RDONLY|os.O_NOATIME)
        with os.fdopen(descriptor,'rb') as stream: digest=hashlib.sha256(stream.read()).hexdigest()
        result[p.name]=(digest,info.st_mode,info.st_size,info.st_ino,
                        info.st_atime_ns,info.st_mtime_ns,info.st_ctime_ns)
    return result


def wal_transport(tmp_path):
    original=tmp_path/'writer.sqlite'
    writer=sqlite3.connect(original)
    writer.execute('PRAGMA journal_mode=WAL')
    writer.executescript("""CREATE TABLE observer_state(id,mode,real_orders_sent);
      INSERT INTO observer_state VALUES(1,'PRODUCTION_PAPER',0);
      CREATE TABLE paper_positions(x);CREATE TABLE paper_fills(x);
      INSERT INTO paper_fills VALUES('pending-wal-row');""")
    writer.commit()
    source=tmp_path/'readonly-source';source.mkdir()
    target=source/'source.sqlite'
    target.write_bytes(original.read_bytes())
    Path(str(target)+'-wal').write_bytes(Path(str(original)+'-wal').read_bytes())
    writer.close()
    return source,target


def test_U24_main_wal_without_shm_reads_pending_rows_and_changes_no_source_metadata(tmp_path,monkeypatch):
    folder,target=wal_transport(tmp_path)
    before=inventory(folder)
    original=sqlite3.connect
    def guarded(path,*args,**kwargs):
        assert str(target) not in str(path), 'SQLite must never open the source'
        return original(path,*args,**kwargs)
    monkeypatch.setattr(sqlite3,'connect',guarded)
    with readonly_snapshot(target,Budget(Limits())) as copied:
        assert copied.execute('SELECT x FROM paper_fills').fetchone()[0]=='pending-wal-row'
    assert inventory(folder)==before
    assert not Path(str(target)+'-shm').exists()


def test_U24_source_directory_and_files_with_no_write_permissions(tmp_path):
    folder,target=wal_transport(tmp_path)
    for member in folder.iterdir():member.chmod(0o400)
    folder.chmod(0o500)
    before=inventory(folder)
    try:
        with readonly_copy(target,validate=False) as copied:
            assert copied.execute('SELECT COUNT(*) FROM paper_fills').fetchone()[0]==1
        assert inventory(folder)==before
    finally:
        folder.chmod(0o700)
        for member in folder.iterdir():member.chmod(0o600)


def test_U24_source_copy_honors_deadline_and_byte_budget(tmp_path):
    _,target=wal_transport(tmp_path)
    with pytest.raises(SnapshotError,match='TIME_BUDGET'):
        with readonly_copy(target,deadline=time.monotonic()-1):pass
    with pytest.raises(SnapshotError,match='SOURCE_BYTE_BUDGET'):
        with readonly_copy(target,max_source_bytes=1):pass


@pytest.mark.parametrize('deadline',[float('inf'),float('nan')])
def test_U24_nonfinite_deadline_cannot_disable_the_time_budget(tmp_path,deadline):
    _,target=wal_transport(tmp_path)
    with pytest.raises(SnapshotError,match='BOUNDED_TIME_BUDGET_REQUIRED'):
        with readonly_copy(target,deadline=deadline):pass


def test_U24_source_change_during_stream_capture_is_not_certified(tmp_path,monkeypatch):
    folder,target=wal_transport(tmp_path)
    import rc6_audit_evidence.sqlite_snapshot as snapshots
    original=snapshots._read
    changed=False
    def change(member,expected,**options):
        nonlocal changed
        result=original(member,expected,**options)
        if not changed:
            changed=True
            wal=Path(str(target)+'-wal')
            with wal.open('ab') as stream:stream.write(b'changed-during-copy')
        return result
    monkeypatch.setattr(snapshots,'_read',change)
    with pytest.raises(SnapshotError,match='SOURCE_SNAPSHOT_BUSY'):
        with readonly_copy(target):pass
    assert not Path(str(target)+'-shm').exists()


def test_U24_growing_source_cannot_write_beyond_the_inventory_byte_budget(tmp_path):
    import rc6_audit_evidence.sqlite_snapshot as snapshots
    source=tmp_path/'growing.bin';source.write_bytes(b'committed-prefix')
    expected=snapshots._metadata(source.stat())
    class Destination:
        captured=0
        def write(self,chunk):
            self.captured+=len(chunk)
            with source.open('ab') as writer:writer.write(b'x'*(2*1024*1024))
    destination=Destination()
    with pytest.raises(SnapshotError,match='SOURCE_SNAPSHOT_BUSY'):
        snapshots._read(source,expected,deadline=time.monotonic()+1,destination=destination)
    assert destination.captured==expected[4]


def test_U24_platform_without_atime_preservation_fails_closed(tmp_path,monkeypatch):
    _,target=wal_transport(tmp_path)
    monkeypatch.delattr(os,'O_NOATIME')
    with pytest.raises(SnapshotError,match='SOURCE_OWNER_CAPTURE_REQUIRED'):
        with readonly_copy(target):pass


@pytest.mark.parametrize('kind',['symlink','hardlink'])
def test_U24_sidecar_alias_is_rejected_before_sqlite(tmp_path,kind):
    _,target=wal_transport(tmp_path)
    wal=Path(str(target)+'-wal');other=tmp_path/'original-wal'
    wal.rename(other)
    if kind=='symlink':wal.symlink_to(other)
    else:os.link(other,wal)
    with pytest.raises(SnapshotError,match='UNALIASED'):
        with readonly_copy(target):pass
