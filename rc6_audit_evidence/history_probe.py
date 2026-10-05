"""Offline migration/recovery evidence using only temporary synthetic stores.

Run: python -m rc6_audit_evidence.history_probe --output evidence.json
There is no source-path option, connector call, production DB or publish step.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from hashlib import sha256
import json
import multiprocessing
import os
from pathlib import Path
import resource
import sqlite3
import sys
import tempfile
import threading
import time
import tracemalloc
import zipfile

import ct_ppi_history_salvage_hf6 as salvage
import cu_history_store_v2_hf6 as history
import ea_history_close_series_hf2 as closes
from rc6_audit_evidence import sqlite_snapshot as snapshots


class Store:
    def __init__(self, path, *, exit_after_rejections=False):
        self.path = str(path)
        self.exit_after_rejections = exit_after_rejections

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=1)
        connection.row_factory = sqlite3.Row
        statements = []
        connection.set_trace_callback(statements.append)
        try:
            yield connection
            connection.commit()
            if self.exit_after_rejections and any(
                text.lstrip().startswith('INSERT INTO history_row_rejections_v2')
                for text in statements
            ):
                os._exit(86)
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()


def inventory(path):
    result = {}
    for suffix in ('', '-wal', '-shm', '-journal'):
        member = Path(str(path) + suffix)
        if not member.exists():
            continue
        info = member.lstat()
        result[suffix or 'MAIN'] = {
            'bytes': info.st_size, 'allocated_bytes': info.st_blocks * 512,
            'mode': oct(info.st_mode), 'inode': info.st_ino,
            'nlink': info.st_nlink, 'atime_ns': info.st_atime_ns,
            'mtime_ns': info.st_mtime_ns, 'ctime_ns': info.st_ctime_ns,
            'sha256': snapshots._read(member, snapshots._metadata(info),
                                       deadline=time.monotonic() + 5),
        }
    return result


def build_legacy(builder):
    connection = sqlite3.connect(builder)
    connection.execute('PRAGMA journal_mode=WAL')
    connection.execute('PRAGMA wal_autocheckpoint=0')
    connection.executescript('''
      CREATE TABLE history_versions_v2(id INTEGER PRIMARY KEY, symbol, instrument_type,
        market, settlement, date, open, high, low, close, volume, source, adjusted,
        observed_at, payload_hash, metadata_json);
      CREATE TABLE history_canonical_v2(symbol, instrument_type, market, settlement,
        date, close, PRIMARY KEY(symbol,instrument_type,market,settlement,date));
      CREATE TABLE history_close_versions_v1(id INTEGER PRIMARY KEY, symbol,
        instrument_type, market, settlement, date, close, source, quality,
        observed_at, raw_row_hash, metadata_json);
      CREATE TABLE history_close_canonical_v1(symbol, instrument_type, market,
        settlement, date, close, PRIMARY KEY(symbol,instrument_type,market,settlement,date));
      CREATE TABLE unrelated_evidence(value); INSERT INTO unrelated_evidence VALUES('preserved');
    ''')
    specifications = [
        ('A', 'ARS', 100, False, '2026-10-01T16:00:00Z'),
        ('A', 'USD_MEP', 1, False, '2026-10-01T17:00:00Z'),
        ('B', None, 100, False, '2026-10-01T16:00:00Z'),
        ('C', None, 100, False, '2026-10-01T16:00:00Z'),
        ('D', 'USD', 100, False, '2026-10-01T16:00:00Z'),
        ('E', 'ARS', 100, False, '2026-10-01T16:00:00'),
        ('F', 'ARS', float('inf'), False, '2026-10-01T16:00:00Z'),
        ('A', 'ARS', 101, False, '2026-10-01T18:00:00Z'),
        ('A', 'ARS', 50, True, '2026-10-01T19:00:00Z'),
    ]
    for index, (symbol, currency, close, adjusted, known) in enumerate(specifications, 1):
        meta = {'currency': currency} if currency else {}
        high = close + 2
        low = close - .5 if close == 1 else close - 2
        connection.execute('INSERT INTO history_versions_v2 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (index, symbol, 'ACCIONES', 'BYMA', 'A-24HS', '2026-09-30', close, high,
             low, close, 1000, 'YAHOO' if adjusted else 'PPI_API', int(adjusted), known,
             'legacy-hash-' + str(index), json.dumps(meta)))
        connection.execute('INSERT OR REPLACE INTO history_canonical_v2 VALUES(?,?,?,?,?,?)',
                           (symbol, 'ACCIONES', 'BYMA', 'A-24HS', '2026-09-30', close))
    for index, (symbol, currency, amount) in enumerate([('A', 'ARS', 100), ('B', None, 100), ('H', 'USD', 1)], 1):
        meta = {'currency': currency, 'rejection_reason_full_ohlc': 'OPEN_MISSING'} if currency else {}
        connection.execute('INSERT INTO history_close_versions_v1 VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
            (index, symbol, 'ACCIONES', 'BYMA', 'A-24HS', '2026-09-30', amount,
             closes.SOURCE, closes.QUALITY, '2026-10-01T16:00:00Z', 'raw-' + str(index), json.dumps(meta)))
        connection.execute('INSERT INTO history_close_canonical_v1 VALUES(?,?,?,?,?,?)',
                           (symbol, 'ACCIONES', 'BYMA', 'A-24HS', '2026-09-30', amount))
    connection.commit()
    return connection


def transport(builder, destination, *, include_shm):
    # The selected source is a frozen filesystem transport. SQLite has never
    # opened this source; the separate fixture-builder process may close safely.
    for suffix in ('', '-wal', '-shm') if include_shm else ('', '-wal'):
        member = Path(str(builder) + suffix)
        target = Path(str(destination) + suffix)
        with target.open('xb') as output:
            snapshots._read(member, snapshots._metadata(member.stat()),
                            deadline=time.monotonic() + 5, destination=output)


@contextmanager
def diagnostic_connection(path):
    journal=Path(str(path)+'-journal')
    if not journal.exists() or not journal.stat().st_size:
        with snapshots.readonly_copy(path,deadline=time.monotonic()+5) as connection:
            yield connection
        return
    # The source copy helper correctly rejects a crashed hot journal. Diagnose
    # it on a second private copy, allowing SQLite recovery only in that copy.
    # This function has no external input and is limited to this probe's files.
    before=inventory(path)
    with tempfile.TemporaryDirectory(prefix='rc6-journal-diagnostic-') as temporary:
        target=Path(temporary)/'diagnostic.sqlite'
        for suffix in ('','-wal','-journal'):
            member=Path(str(path)+suffix)
            if member.exists():
                with Path(str(target)+suffix).open('xb') as output:
                    snapshots._read(member,snapshots._metadata(member.stat()),
                                    deadline=time.monotonic()+5,destination=output)
        connection=sqlite3.connect(target)
        connection.row_factory=sqlite3.Row
        try:
            connection.execute('PRAGMA query_only=ON')
            yield connection
        finally:
            connection.close()
    assert inventory(path)==before


def counts(path):
    if not Path(path).exists():
        return {}
    with diagnostic_connection(path) as connection:
        available = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        names = ('history_versions_v2', 'history_canonical_v2', 'history_batch_attempts_v2',
                 'history_batch_rows_v2', 'history_close_versions_v1', 'history_close_canonical_v1',
                 'history_attempt_ledger_v2', 'history_row_rejections_v2')
        result = {name: connection.execute('SELECT COUNT(*) FROM ' + name).fetchone()[0]
                  for name in names if name in available}
        if 'history_ingest_sagas_v2' in available:
            result['saga_state'] = connection.execute('SELECT state FROM history_ingest_sagas_v2').fetchone()[0]
        return result


def migrated_content(path):
    with snapshots.readonly_copy(path, deadline=time.monotonic() + 5) as connection:
        tables = ('history_versions_v2', 'history_canonical_v2', 'history_close_versions_v1',
                  'history_close_canonical_v1', 'history_migration_quarantine_v2',
                  'history_close_migration_quarantine_v2')
        content = {name: [dict(row) for row in connection.execute('SELECT * FROM ' + name + ' ORDER BY 1')]
                   for name in tables}
        reasons = {name: [row['reason'] for row in rows]
                   for name, rows in content.items() if 'quarantine' in name}
        preserved = {name: connection.execute('SELECT COUNT(*) FROM ' + name).fetchone()[0]
                     for name in ('history_versions_v2_legacy', 'history_close_versions_v1_legacy')}
        preserved['unrelated_evidence'] = connection.execute('SELECT value FROM unrelated_evidence').fetchone()[0]
        digest = sha256(json.dumps(content, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        return {'semantic_sha256': digest, 'quarantine_reasons': reasons, 'preserved': preserved}


def ingest(observer, archive):
    return salvage.ingest_ppi_payload(observer, symbol='AUDIT', instrument_type='ACCIONES',
        market='BYMA', currency='ARS', settlement='A-24HS',
        payload=[{'date':'2026-09-30T17:00:00-03:00', 'openingPrice':100, 'max':102, 'min':98, 'price':100, 'volume':1000},
                 {'date':'2026-09-29T17:00:00-03:00', 'openingPrice':0, 'max':102, 'min':98, 'price':100, 'volume':1000}],
        attempted_at='2026-10-01T16:00:00Z', history_store=archive)


def crash_child(folder, stage):
    observer = Store(Path(folder) / 'observer.sqlite', exit_after_rejections=stage == 'REJECTIONS_COMMIT')
    archive = Store(Path(folder) / 'history.sqlite')
    if stage == 'VERSION_INSERT':
        history._prefer = lambda *_: os._exit(79)
    if stage == 'FULL_COMMIT':
        original = history.append_many
        def stop(*args, **kwargs):
            original(*args, **kwargs)
            os._exit(81)
        history.append_many = stop
    if stage == 'CLOSE_COMMIT':
        original = closes.append_many
        def stop(*args, **kwargs):
            original(*args, **kwargs)
            os._exit(82)
        closes.append_many = stop
    if stage == 'OBSERVER_ATTEMPT_COMMIT':
        original = salvage._append_attempt_once
        def stop(*args, **kwargs):
            original(*args, **kwargs)
            os._exit(83)
        salvage._append_attempt_once = stop
    checkpoint = {'FULL_CHECKPOINT':'FULL_COMMITTED', 'CLOSE_CHECKPOINT':'CLOSE_COMMITTED',
                  'FINAL_CHECKPOINT':'OBSERVER_COMMITTED'}.get(stage)
    if checkpoint:
        original_step = salvage._step
        def stop(store, key, state, result):
            original_step(store, key, state, result)
            if state == checkpoint:
                os._exit({'FULL_CHECKPOINT':84, 'CLOSE_CHECKPOINT':85, 'FINAL_CHECKPOINT':87}[stage])
        salvage._step = stop
    ingest(observer, archive)
    os._exit(0)


def migration_crash_child(source, destination, mapping):
    original=history.append_candle
    def stop(*args,**kwargs):
        original(*args,**kwargs)
        os._exit(88)
    history.append_candle=stop
    history.migrate_copy(source,destination,currency_map=mapping,seconds=10)
    os._exit(0)


class PeakDisk:
    def __init__(self, root):
        self.root = root
        self.logical = self.allocated = 0
        self.samples = 0
        self.error = None
        self.lock = threading.Lock()
        self.done = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def sample(self):
        logical = allocated = 0
        def walk_error(error):
            if not isinstance(error,FileNotFoundError):raise error
        # Private scratch is routinely removed between directory enumeration
        # and descent. walk handles that race without losing retained siblings.
        for directory,_,members in os.walk(self.root,onerror=walk_error):
            for name in members:
                path=Path(directory)/name
                try:
                    info = path.stat()
                    logical += info.st_size
                    allocated += info.st_blocks * 512
                except FileNotFoundError:
                    pass
        with self.lock:
            self.logical = max(self.logical, logical)
            self.allocated = max(self.allocated, allocated)
            self.samples += 1

    def run(self):
        try:
            while not self.done.wait(.005):
                self.sample()
        except Exception as error:
            self.error=type(error).__name__


def run_probe():
    started, cpu_started = time.monotonic(), time.process_time()
    tracemalloc.start()
    original_tempdir = tempfile.TemporaryDirectory
    with original_tempdir(prefix='rc6-history-offline-') as temporary:
        root = Path(temporary)
        private_copies = root / 'private-copies'
        private_copies.mkdir()
        snapshots.tempfile.TemporaryDirectory = lambda **kwargs: original_tempdir(dir=private_copies, **kwargs)
        peak = PeakDisk(root)
        peak.thread.start()
        try:
            builder = root / 'fixture-builder.sqlite'
            writer = build_legacy(builder)
            sources = [root / 'legacy-with-shm.sqlite', root / 'legacy-without-shm.sqlite']
            transport(builder, sources[0], include_shm=True)
            transport(builder, sources[1], include_shm=False)
            writer.close()
            before = {source.name: inventory(source) for source in sources}
            mapping = {('A','ACCIONES','BYMA','A-24HS'):['ARS','USD_MEP'],
                       ('C','ACCIONES','BYMA','A-24HS'):['ARS','USD'],
                       ('D','ACCIONES','BYMA','A-24HS'):'ARS'}
            migrations = []
            for index, source in enumerate([*sources, sources[0]]):
                destination = root / ('destination-' + str(index) + '.sqlite')
                phase_started = time.monotonic()
                result = history.migrate_copy(source, destination, currency_map=mapping, seconds=10)
                result.update(destination_inventory=inventory(destination), counts=counts(destination),
                              content=migrated_content(destination), wall_seconds=time.monotonic()-phase_started)
                assert result['migrated_rows'] == 4 and result['quarantined_rows'] == 5
                assert result['close_only'] == {'migrated_rows':2, 'quarantined_rows':1}
                assert result['counts']['history_canonical_v2'] == 3
                migrations.append(result)
                peak.sample()
            assert len({entry['content']['semantic_sha256'] for entry in migrations}) == 1
            existing = root / 'destination-0.sqlite'
            existing_before = inventory(existing)
            try:
                history.migrate_copy(sources[0], existing, currency_map=mapping)
                raise AssertionError('Existing destination was overwritten')
            except ValueError as error:
                assert str(error) == 'HISTORY_NEW_COPY_DESTINATION_REQUIRED'
            assert inventory(existing) == existing_before
            failed_destination=root/'interrupted-migration.sqlite'
            child=multiprocessing.get_context('fork').Process(target=migration_crash_child,
                args=(sources[0],failed_destination,mapping))
            child.start();child.join(timeout=5)
            if child.is_alive():
                child.terminate();child.join(timeout=2)
                raise AssertionError('Migration child exceeded its bounded deadline')
            assert child.exitcode==88
            partial_counts=counts(failed_destination)
            assert partial_counts['history_versions_v2']==1
            resumed_destination=root/'fresh-destination-after-crash.sqlite'
            resumed=history.migrate_copy(sources[0],resumed_destination,currency_map=mapping,seconds=10)
            resumed_content=migrated_content(resumed_destination)
            assert resumed_content['semantic_sha256']==migrations[0]['content']['semantic_sha256']
            migration_recovery={'exit_code':child.exitcode,'partial_destination_preserved':True,
                'partial_destination_counts':partial_counts,'partial_destination_publishable':False,
                'resume_method':'NEW_DESTINATION_FROM_UNCHANGED_OFFLINE_SOURCE',
                'resumed_result':resumed,'resumed_content':resumed_content,
                'provider_ingestion_repeated':False}
            after = {source.name: inventory(source) for source in sources}
            assert after == before
            crashes = []
            stages = ('VERSION_INSERT', 'FULL_COMMIT', 'FULL_CHECKPOINT', 'CLOSE_COMMIT',
                      'CLOSE_CHECKPOINT', 'OBSERVER_ATTEMPT_COMMIT', 'REJECTIONS_COMMIT', 'FINAL_CHECKPOINT')
            for stage in stages:
                folder = root / stage
                folder.mkdir()
                phase_started = time.monotonic()
                child = multiprocessing.get_context('fork').Process(target=crash_child, args=(folder, stage))
                child.start()
                child.join(timeout=5)
                if child.is_alive():
                    child.terminate()
                    child.join(timeout=2)
                    raise AssertionError('Child exceeded the bounded recovery deadline')
                expected_exits={'VERSION_INSERT':79,'FULL_COMMIT':81,'FULL_CHECKPOINT':84,
                    'CLOSE_COMMIT':82,'CLOSE_CHECKPOINT':85,'OBSERVER_ATTEMPT_COMMIT':83,
                    'REJECTIONS_COMMIT':86,'FINAL_CHECKPOINT':87}
                assert child.exitcode==expected_exits[stage]
                observer, archive = Store(folder / 'observer.sqlite'), Store(folder / 'history.sqlite')
                crashed_images={'history':inventory(archive.path),'observer':inventory(observer.path)}
                committed_before_retry = {'history':counts(archive.path), 'observer':counts(observer.path)}
                assert crashed_images=={'history':inventory(archive.path),'observer':inventory(observer.path)}
                expected_saga={'VERSION_INSERT':'STARTED','FULL_COMMIT':'STARTED',
                    'FULL_CHECKPOINT':'FULL_COMMITTED','CLOSE_COMMIT':'FULL_COMMITTED',
                    'CLOSE_CHECKPOINT':'CLOSE_COMMITTED','OBSERVER_ATTEMPT_COMMIT':'CLOSE_COMMITTED',
                    'REJECTIONS_COMMIT':'CLOSE_COMMITTED','FINAL_CHECKPOINT':'OBSERVER_COMMITTED'}
                prior_history,prior_observer=committed_before_retry['history'],committed_before_retry['observer']
                assert prior_history['saga_state']==expected_saga[stage]
                assert prior_history.get('history_versions_v2',0)==int(stage!='VERSION_INSERT')
                assert prior_history.get('history_close_versions_v1',0)==int(stage not in {'VERSION_INSERT','FULL_COMMIT','FULL_CHECKPOINT'})
                assert prior_observer.get('history_attempt_ledger_v2',0)==int(stage in {'OBSERVER_ATTEMPT_COMMIT','REJECTIONS_COMMIT','FINAL_CHECKPOINT'})
                assert prior_observer.get('history_row_rejections_v2',0)==int(stage in {'REJECTIONS_COMMIT','FINAL_CHECKPOINT'})
                first = ingest(observer, archive)
                repeated = ingest(observer, archive)
                recovered = {'history':counts(archive.path), 'observer':counts(observer.path)}
                assert first['attempt_id'] == repeated['attempt_id']
                assert recovered['history']['history_versions_v2'] == recovered['history']['history_close_versions_v1'] == 1
                assert recovered['history']['saga_state'] == 'OBSERVER_COMMITTED'
                assert recovered['observer']['history_attempt_ledger_v2'] == recovered['observer']['history_row_rejections_v2'] == 1
                crashes.append({'stage':stage, 'exit_code':child.exitcode,
                    'crash_image_inventory':crashed_images,'diagnostic_source_unchanged':True,
                    'committed_before_retry':committed_before_retry, 'after_retry_and_repeat':recovered,
                    'repeat_new_versions':repeated['versions_appended'],
                    'repeat_new_close_versions':repeated['close_only_versions_appended'],
                    'wall_seconds':time.monotonic()-phase_started})
                peak.sample()
            # Two actual local probe bundles let the resource report include
            # concurrent old/new bundle disk occupation. These contain only
            # synthetic counters, not DBs or provider/account evidence.
            bundles = []
            payload = json.dumps({'migration':migrations, 'recovery':crashes}, sort_keys=True).encode()
            for name in ('bundle-a.zip', 'bundle-b.zip'):
                bundle = root / name
                with zipfile.ZipFile(bundle, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
                    archive.writestr('synthetic-evidence.json', payload)
                bundles.append({'bytes':bundle.stat().st_size,
                                'sha256':sha256(bundle.read_bytes()).hexdigest()})
            peak.sample()
            peak.done.set()
            peak.thread.join(timeout=1)
            if peak.thread.is_alive() or peak.error is not None:
                raise AssertionError('OFFLINE_DISK_SAMPLER_INCOMPLETE')
            after={source.name:inventory(source) for source in sources}
            assert after==before
            filesystem = os.statvfs(root)
            _, python_peak = tracemalloc.get_traced_memory()
            result = {'schema':'rc6.history-offline-dry-run.v1', 'corpus':'SYNTHETIC_OFFLINE',
                'runtime_verified':False, 'production_history_coverage':'NO_VERIFICADO',
                'source_before':before, 'source_after':after, 'source_unchanged':before == after,
                'migration':migrations, 'same_destination_rejected_without_mutation':True,
                'migration_crash_recovery':migration_recovery,
                'semantic_repeat_equal':True, 'process_crash_recovery':crashes,
                'resources':{'wall_seconds':time.monotonic()-started,
                    'cpu_seconds_parent':time.process_time()-cpu_started,
                    'peak_disk_logical_bytes':peak.logical, 'peak_disk_allocated_bytes':peak.allocated,
                    'disk_sampling_interval_seconds':.005, 'disk_samples_completed':peak.samples,
                    'disk_sampler_status':'COMPLETED_WITHOUT_ERRORS', 'python_traced_peak_bytes':python_peak,
                    'peak_rss_parent_bytes':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024,
                    'peak_rss_child_bytes':resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss*1024,
                    'available_local_disk_bytes':filesystem.f_bavail*filesystem.f_frsize,
                    'probe_bundle_copies':bundles,
                    'production_image_bytes':'NO_VERIFICADO', 'production_reserve_bytes':'NO_VERIFICADO',
                    'production_capacity_gate':'NO_VERIFICADO; requires actual image + reserve + measured copy + two bundles'},
                'source_code_sha256':{**{name:sha256(Path(module.__file__).read_bytes()).hexdigest()
                    for name,module in [('history',history), ('salvage',salvage), ('closes',closes), ('snapshot',snapshots)]},
                    'probe':sha256(Path(__file__).read_bytes()).hexdigest()}}
            return result
        finally:
            peak.done.set()
            peak.thread.join(timeout=1)
            snapshots.tempfile.TemporaryDirectory = original_tempdir
            tracemalloc.stop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    arguments = parser.parse_args()
    # Connector/network use is a programming error in this fixture-only probe.
    def offline(event, _args):
        if event in {'socket.connect', 'socket.getaddrinfo'}:
            raise AssertionError('NETWORK_FORBIDDEN_IN_OFFLINE_HISTORY_PROBE')
    sys.addaudithook(offline)
    result = run_probe()
    arguments.output.write_text(json.dumps(result, sort_keys=True, indent=2) + '\n', encoding='utf-8')
    os.chmod(arguments.output, 0o644)
    print(json.dumps({'source_unchanged':result['source_unchanged'],
                      'migration_runs':len(result['migration']),
                      'process_crash_stages':len(result['process_crash_recovery']),
                      'resources':result['resources']}, sort_keys=True))


if __name__ == '__main__':
    main()
