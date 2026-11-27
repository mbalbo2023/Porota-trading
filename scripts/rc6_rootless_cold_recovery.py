#!/usr/bin/env python3
"""Bounded ROOTLESS_SPLIT recovery of an existing FAILED COLD queue.

This Actions maintenance adapter targets the exact installed IOL image and
one failed COLD package. The archive algorithm is the immutable
rc6_cold2_successor module pinned by the transport. Mutation requires an
audited CLOSED session, a flat PAPER book and the stopped original pair.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sqlite3
import stat
import sys

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

# Fill only from the new validated Promote V2 receipt. Placeholder pins fail closed.
FROZEN_SOURCE_SHA = 'eba76703be86e6a6b1406db36e5cb4cb87b21330'
FROZEN_TREE = '5ac31c4b9cf2c16f477c08a6b5b3424f644a2667'
FROZEN_IMAGE = 'sha256:274b143fd323ab0b79aff90890f54a7f5ce0ed84c3323fedd385ce85f28e83d2'
FROZEN_ATTEMPT = '45921311817-1'
FROZEN_CIDS = {
    'observer': 'f6440b9039b39f4d3a8b0047d1140b64d7eccadd68e00174fc2f6b2198662a09',
    'dashboard': '0d4e1b7b56bd3621e5448a652cc7e45a1b1652c92e612fdc9e3f9abcf298c774',
}
PROVIDER = 'IOL_REST'
CONTROLLER_ROLES = frozenset({'iol_gateway', 'scanner', 'dynamic_shadow', 'exit_reader', 'notifications', 'candles'})
# Actions #45920208428 / artifact #15164002698 verified both immutable payloads.
FAILED_COLD = {
    'snapshot_id': 'ff43ea0325554d1f85c95626c59d1621',
    'manifest_sha256': '9794ef3e462444b15a3ff863329ff9c15a9314d5c86e7265da65d261a3d08bd7',
    'pack_sha256': 'dd0718808bd7ce0794d5e1201802f28eec801b391097e04bd2cc5e040910aea2',
    'pack_bytes': 211509986,
    'failure_code': 'SHADOW_COLD_HOST_MEMORY_RESERVE_BREACHED',
    'failure_phase': 'REPORT_VERIFY',
    'failed_computed_report': {
        'name': 'computed-d59321241924e175396ccece97185f188cf4ba70d6795881eaf6af860c6e15ed.json.gz',
        'bytes': 5453693,
        'sha256': 'f2c6eb035a3b2c70f5f391bc30df7abf7ae013f66b6558b180fa6aa4383de3d8',
    },
}
RESERVE = 2 * 1024**3
CLOSED = 'MARKET_CLOSED'
IOL_STOP_SESSIONS = frozenset((CLOSED, 'PREOPEN', 'OPEN', 'MARKET_OPEN', 'CHECKING',
                              'BOOT_COMMAND', 'BOOT_CALENDAR', 'BOOT_IOL_GATEWAY', 'BOOT_IOL_GATEWAY_IDENTITY'))
STOP_ACK = 'Observador detenido ordenadamente.'


class ColdHold(ValueError):
    """Public fixed-category fail-closed reason; no private host paths."""


class StopProofHold(ColdHold):
    """Keep only typed Docker stop predicates after the original runtime is restored."""

    def __init__(self, evidence):
        super().__init__('ROOTLESS_PAIR_NOT_CLEANLY_STOPPED')
        self.evidence = evidence


def demand(condition, reason):
    if not condition:
        raise ColdHold(reason)


def frozen_coordinates():
    """No checkout default, inferred runtime or historical receipt may authorize a stop."""
    demand(re.fullmatch('[a-f0-9]{40}', FROZEN_SOURCE_SHA) and
           re.fullmatch('[a-f0-9]{40}', FROZEN_TREE) and
           re.fullmatch('sha256:[a-f0-9]{64}', FROZEN_IMAGE) and
           re.fullmatch('[1-9][0-9]*-[1-9][0-9]*', FROZEN_ATTEMPT) and
           set(FROZEN_CIDS) == {'observer','dashboard'} and
           len(set(FROZEN_CIDS.values())) == 2 and
           all(re.fullmatch('[a-f0-9]{64}', cid) for cid in FROZEN_CIDS.values()),
           'FROZEN_IOL_PROMOTION_RECEIPT_REQUIRED')
    return {'source_sha':FROZEN_SOURCE_SHA, 'source_tree':FROZEN_TREE,
            'image_id':FROZEN_IMAGE, 'attempt':FROZEN_ATTEMPT, 'market_provider':PROVIDER}


def require_closed(now):
    """Use the existing exact release calendar; an unknown calendar is no proof."""
    try:
        import ak_byma_calendar as calendar
        from co_market_sessions_hf6 import A3_SESSIONS, BYMA_PAPER_SPOT, TZ, phase_for
        demand(now.tzinfo is not None and now.utcoffset() is not None,
               'COLD_MARKET_CALENDAR_UNVERIFIED')
        local = now.astimezone(TZ)
        demand(local.year in calendar.ANIOS_AUDITADOS, 'COLD_MARKET_CALENDAR_UNVERIFIED')
        operational = calendar.es_dia_habil_operativo(local.date())
        demand(type(operational) is bool, 'COLD_MARKET_CALENDAR_UNVERIFIED')
        phase = phase_for(BYMA_PAPER_SPOT, local) if operational else 'CLOSED'
        # A BYMA calendar holiday is insufficient evidence that A3 is closed.
        a3_closed = all(phase_for(session, local) == 'CLOSED' for session in set(A3_SESSIONS.values()))
    except (ImportError, OSError, TypeError, ValueError, AttributeError) as exc:
        if isinstance(exc, ColdHold):
            raise
        raise ColdHold('COLD_MARKET_CALENDAR_UNVERIFIED') from exc
    demand(phase == 'CLOSED' and a3_closed, 'COLD_CLOSED_REQUIRED')


def pinned_incident(successor, root, *, hash_pack):
    """Archive only the exact FAILED input already verified through Actions."""
    observed = successor.inspect(root, hash_pack=hash_pack)
    demand(all(type(observed.get(key)) is type(value) and observed[key] == value
               for key, value in FAILED_COLD.items()), 'COLD_FAILED_INCIDENT_IDENTITY_CHANGED')
    return observed


def flat_book(path, *, stopped=False, clean_exit=False, stopped_after=None, restore=False):
    """Read the already-deployed SQLite PAPER book; never mutate the DB/WAL."""
    try:
        before = path.lstat()
        demand(stat.S_ISREG(before.st_mode) and before.st_nlink == 1,
               'PAPER_DATABASE_ALIAS')
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=3)) as db:
            db.execute('PRAGMA query_only=ON')
            db.execute('PRAGMA busy_timeout=1000')
            rows = db.execute('SELECT mode,real_orders_sent,process_state,session_state,'
                              'heartbeat_at,ppi_auth,detail,last_market_data_at '
                              'FROM observer_state WHERE id=1').fetchall()
            work = db.execute('SELECT dataset_id FROM paper_workspace WHERE id=1').fetchall()
            demand(len(rows) == len(work) == 1 and bool(work[0][0]), 'PAPER_DATABASE_IDENTITY')
            mode, orders, process, session, heartbeat, ppi_auth, detail, last_market_data = rows[0]
            demand(mode == 'PRODUCTION_PAPER' and type(orders) is int and orders == 0,
                   'PAPER_ZERO_REQUIRED')
            demand(ppi_auth == 'NOT_ATTEMPTED', 'IOL_PAPER_PPI_ACCESS_DETECTED')
            now = datetime.now(timezone.utc)
            if not restore:
                require_closed(now)
                demand(session == CLOSED, 'COLD_OBSERVER_CLOSED_REQUIRED')
            if stopped:
                # Pinned observer writes STOPPED + this exact acknowledgement
                # without changing the last session. A separate recovery of
                # this own stop may see an IOL boot phase after a partial restart.
                demand(clean_exit is True and process == 'STOPPED' and
                       session in IOL_STOP_SESSIONS and detail == STOP_ACK,
                       'PAPER_STOP_ACK_UNVERIFIED')
                try:
                    stamp = datetime.fromisoformat(str(heartbeat).replace('Z','+00:00'))
                    lag = (now - stamp).total_seconds()
                    demand(isinstance(stopped_after,datetime) and
                           stopped_after.tzinfo is not None and stamp.tzinfo is not None and
                           stamp >= stopped_after and 0 <= lag <= 5400,
                           'PAPER_STOP_ACK_UNVERIFIED')
                except (ValueError,TypeError) as exc:
                    raise ColdHold('PAPER_STOP_ACK_UNVERIFIED') from exc
            else:
                from scripts import porota_rootless_host as frozen_host
                row = {'mode':mode,'real_orders_sent':orders,'process_state':process,
                       'session_state':session,'ppi_auth':ppi_auth,'detail':detail,
                       'heartbeat_at':heartbeat,'last_market_data_at':last_market_data}
                try:
                    classified = frozen_host.classify_runtime(
                        row, now=now, started=now-timedelta(seconds=120), provider=PROVIDER)
                except (ValueError,RuntimeError,TypeError) as exc:
                    raise ColdHold('PAPER_PROCESS_NOT_QUIESCIBLE') from exc
                demand(classified['status'] == 'READY' and
                       (restore or process == 'WAITING_MARKET' and classified['source_alert'] == 'NONE'),
                       'PAPER_PROCESS_NOT_QUIESCIBLE')
            # The original deployed engine treats EVERY non-terminal status as
            # exposure; testing only status='OPEN' falsely admitted EXIT_PENDING.
            demand(db.execute("SELECT 1 FROM paper_positions "
                              "WHERE status IS NULL OR status NOT IN ('CLOSED','CANCELLED_ADMIN') LIMIT 1")
                   .fetchone() is None, 'PAPER_EXPOSURE_NOT_FLAT')
            demand(db.execute("SELECT 1 FROM paper_cauciones "
                              "WHERE status IS NULL OR status NOT IN ('MATURED','CANCELLED_ADMIN') LIMIT 1")
                   .fetchone() is None, 'PAPER_EXPOSURE_NOT_FLAT')
            demand(db.execute("SELECT 1 FROM paper_future_positions "
                              "WHERE status IS NULL OR status!='CLOSED' LIMIT 1").fetchone() is None,
                   'PAPER_EXPOSURE_NOT_FLAT')
            demand(db.execute("SELECT 1 FROM paper_exit_intents "
                              "WHERE state IS NULL OR state NOT IN ('CLOSED','CANCELLED_ADMIN') LIMIT 1")
                   .fetchone() is None, 'PAPER_INFLIGHT_INTENT_PENDING')
            demand(db.execute("SELECT 1 FROM paper_future_exit_intents "
                              "WHERE state IS NULL OR state NOT IN ('CLOSED','CANCELLED_ADMIN') LIMIT 1")
                   .fetchone() is None, 'PAPER_INFLIGHT_INTENT_PENDING')
            columns = {row[1] for row in db.execute('PRAGMA table_info(paper_family_lifecycle)')}
            demand({'family', 'state'} <= columns, 'PAPER_FAMILY_LEDGER_UNVERIFIED')
            demand(db.execute("SELECT 1 FROM paper_family_lifecycle WHERE family='FCI' "
                              "AND (state IS NULL OR state!='REDEEMED') LIMIT 1").fetchone() is None,
                   'PAPER_EXPOSURE_NOT_FLAT')
            demand(db.execute("SELECT 1 FROM paper_family_lifecycle WHERE family='FUTUROS' "
                              "AND (state IS NULL OR state NOT IN ('CLOSE','EXPIRY','ADMIN_CANCEL')) LIMIT 1")
                   .fetchone() is None, 'PAPER_EXPOSURE_NOT_FLAT')
            demand(db.execute("SELECT 1 FROM paper_family_lifecycle "
                              "WHERE family IS NULL OR family NOT IN ('FCI','FUTUROS') LIMIT 1").fetchone() is None,
                   'PAPER_EXPOSURE_NOT_FLAT')
        after = path.lstat()
        demand((before.st_dev, before.st_ino) == (after.st_dev, after.st_ino),
               'PAPER_DATABASE_CHANGED')
        return {'state': 'PAPER_FLAT', 'mode': mode, 'real_orders_sent': orders,
                'process': process, 'session': session, 'ppi_auth':ppi_auth,
                'identity': [before.st_dev, before.st_ino]}
    except (OSError, sqlite3.Error, TypeError, IndexError) as exc:
        raise ColdHold('PAPER_DATABASE_UNVERIFIED') from exc


def resource_reserve(root):
    """Physical reserve and inode guard over the *actual* COLD filesystem."""
    cold = root/'data/paper_v17/artifacts/observer_v17.db/dynamic-shadow/cold-audit'
    target = root/'data/deploy'
    demand(cold.stat().st_dev == target.stat().st_dev, 'COLD_CROSS_DEVICE')
    data = os.statvfs(cold)
    demand(data.f_bavail * data.f_frsize >= RESERVE + 1024**2 and
           data.f_favail >= 16, 'COLD_DISK_RESERVE_UNMET')
    with open('/proc/meminfo', encoding='ascii') as stream:
        available = [int(line.split()[1]) * 1024 for line in stream
                     if line.startswith('MemAvailable:')]
    demand(len(available) == 1 and available[0] > 128 * 1024**2,
           'COLD_MEMORY_RESERVE_UNMET')
    return {'free_bytes': data.f_bavail * data.f_frsize,
            'free_inodes': data.f_favail}


def critical_broker_proof():
    """Independent rootful issues-only health; exact container, no PPI Watch."""
    target = 'porota_critical_approval_rc6'
    prefix = ['sudo','-n','/usr/bin/docker']
    def read(*args):
        try:
            proc = subprocess.run([*prefix,*args],capture_output=True,text=True,
                                  timeout=15,cwd='/')
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ColdHold('CRITICAL_BROKER_UNAVAILABLE') from exc
        demand(proc.returncode == 0 and len(proc.stdout) <= 32768,
               'CRITICAL_BROKER_UNAVAILABLE')
        return proc.stdout.strip()
    try:
        observed = json.loads(read('container','inspect',target))
        demand(type(observed) is list and len(observed) == 1, 'CRITICAL_BROKER_UNVERIFIED')
        item = observed[0]
        state = item.get('State',{})
        demand(item.get('Name') == '/'+target and item.get('RestartCount') == 0 and
               re.fullmatch('sha256:[a-f0-9]{64}', str(item.get('Image',''))) and
               state.get('Status') == 'running' and state.get('OOMKilled') is False and
               state.get('Health',{}).get('Status') == 'healthy',
               'CRITICAL_BROKER_UNHEALTHY')
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        if isinstance(exc, ColdHold):
            raise
        raise ColdHold('CRITICAL_BROKER_UNVERIFIED') from exc
    snippet = ("from fm_critical_approval_unix_runtime_rc6 import UnixGithubIssuesClient; "
               "h=UnixGithubIssuesClient().health(); "
               "assert h.get('status')=='ok' and h.get('capability')=='issues_only'; "
               "print('RC6_CRITICAL_ISSUES_ONLY=GREEN')")
    demand(read('container','exec',target,'python','-c',snippet) ==
           'RC6_CRITICAL_ISSUES_ONLY=GREEN', 'CRITICAL_BROKER_CAPABILITY_UNVERIFIED')
    return {'state':'HEALTHY_ISSUES_ONLY','restart_count':0}


def legacy_writers_absent():
    """Read-only proof that no old ROOTFUL observer/dashboard still writes."""
    # Docker name filters regex-match the INTERNAL leading slash on some
    # versions. A filter anchored without that slash can silently return zero
    # results even with a live writer. Enumerate and compare exact names.
    try:
        proc = subprocess.run(['sudo','-n','/usr/bin/docker','container','ls',
                               '--format','{{.Names}}'],
                              capture_output=True,text=True,timeout=15,cwd='/')
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ColdHold('LEGACY_DOCKER_INVENTORY_UNAVAILABLE') from exc
    demand(proc.returncode == 0 and len(proc.stdout) <= 65536,
           'LEGACY_DOCKER_INVENTORY_UNVERIFIED')
    names = proc.stdout.splitlines()
    demand(len(names) <= 1024 and all(name and len(name) <= 255 for name in names),
           'LEGACY_DOCKER_INVENTORY_UNVERIFIED')
    prohibited = {'porota_production_observer','porota_production_dashboard'}
    demand(not any(name.lstrip('/') in prohibited for name in names),
           'LEGACY_PAPER_WRITER_ACTIVE')
    return {'state':'NO_LEGACY_WRITERS','checked_roles':2}


STOP_WITNESS_NAME = 'cold-recover-stop.v1.json'


def _state_descriptor(state):
    """Confine the durable STOP witness to the rootless private state dir."""
    try:
        directory = os.open(state, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        info = os.fstat(directory)
        demand(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid() and
               stat.S_IMODE(info.st_mode) & 0o077 == 0,
               'COLD_STOP_WITNESS_DIRECTORY_UNSAFE')
        return directory
    except (OSError, PermissionError) as exc:
        raise ColdHold('COLD_STOP_WITNESS_DIRECTORY_UNSAFE') from exc


def _read_stop_witness(directory):
    try:
        file = os.open(STOP_WITNESS_NAME,os.O_RDONLY | os.O_NOFOLLOW,dir_fd=directory)
    except FileNotFoundError as exc:
        raise ColdHold('COLD_STOP_WITNESS_REQUIRED') from exc
    except OSError as exc:
        raise ColdHold('COLD_STOP_WITNESS_UNREADABLE') from exc
    with os.fdopen(file,'rb') as stream:
        info = os.fstat(stream.fileno())
        demand(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid() and
               info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600 and
               0 < info.st_size <= 4096, 'COLD_STOP_WITNESS_UNSAFE')
        raw = stream.read(4097)
        demand(len(raw) <= 4096,'COLD_STOP_WITNESS_UNSAFE')
    def distinct(items):
        value = {}
        for key, item in items:
            demand(key not in value,'COLD_STOP_WITNESS_UNSAFE')
            value[key] = item
        return value
    try:
        value = json.loads(raw,object_pairs_hook=distinct)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ColdHold('COLD_STOP_WITNESS_UNSAFE') from exc
    demand(type(value) is dict,'COLD_STOP_WITNESS_UNSAFE')
    return value


def arm_stop_witness(state, roles, operator_hash):
    """One-shot durable proof BEFORE any stop; no overwrite of prior incident."""
    import time
    token = secrets.token_hex(16)
    data = {'schema':'rc6.cold.stop-intent.v2', **frozen_coordinates(),
            'snapshot_id':FAILED_COLD['snapshot_id'],'manifest_sha256':FAILED_COLD['manifest_sha256'],
            'roles':roles,'operator_hash':operator_hash,
            'nonce':token,'created_at':time.time()}
    raw = (json.dumps(data,sort_keys=True,separators=(',',':'))+'\n').encode('utf-8')
    directory = _state_descriptor(state)
    try:
        try:
            file = os.open(STOP_WITNESS_NAME,os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                           os.O_NOFOLLOW,0o600,dir_fd=directory)
        except FileExistsError as exc:
            raise ColdHold('COLD_STOP_WITNESS_EXISTS') from exc
        with os.fdopen(file,'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(directory)
    finally:
        os.close(directory)
    return token


def verify_stop_witness(state, roles, operator_hash, *, with_receipt=False):
    """An independent resume is permitted ONLY for a recent OWNED COLD stop."""
    import time
    directory = _state_descriptor(state)
    try:
        data = _read_stop_witness(directory)
    finally:
        os.close(directory)
    demand(data.get('schema') == 'rc6.cold.stop-intent.v2' and
           all(data.get(key) == value for key,value in frozen_coordinates().items()) and
           data.get('snapshot_id') == FAILED_COLD['snapshot_id'] and
           data.get('manifest_sha256') == FAILED_COLD['manifest_sha256'] and
           data.get('roles') == roles == FROZEN_CIDS and
           data.get('operator_hash') == operator_hash and
           type(data.get('nonce')) is str and re.fullmatch('[0-9a-f]{32}',data['nonce']),
           'COLD_STOP_WITNESS_MISMATCH')
    stamp = data.get('created_at')
    demand(type(stamp) in (int,float) and 0 <= time.time()-stamp <= 5400,
           'COLD_STOP_WITNESS_STALE')
    return data if with_receipt else data['nonce']


def clear_stop_witness(state,token):
    """Clear only the exact successful incident after verified postflight."""
    directory = _state_descriptor(state)
    try:
        data = _read_stop_witness(directory)
        demand(data.get('nonce') == token,'COLD_STOP_WITNESS_MISMATCH')
        os.unlink(STOP_WITNESS_NAME,dir_fd=directory)
        os.fsync(directory)
    finally:
        os.close(directory)


def operator_custody(root, names=None):
    """Explicit UID1000 read/write/search proof; sudo READ_ONLY cannot prove it."""
    uid = os.geteuid()
    demand(uid == os.getuid() and uid == 1000, 'ROOTLESS_OPERATOR_REQUIRED')
    shadow = root/'data/paper_v17/artifacts/observer_v17.db/dynamic-shadow'
    source = shadow/'cold-audit'
    target = root/'data/deploy'
    if names is None:
        try:
            names = sorted(item.name for item in source.iterdir())
        except OSError as exc:
            raise ColdHold('COLD_OPERATOR_ACCESS_DENIED') from exc
    locations = [('data', root/'data', os.X_OK),
                 ('shadow', shadow, os.R_OK | os.X_OK),
                 ('source', source, os.R_OK | os.W_OK | os.X_OK),
                 ('archive_parent', target, os.W_OK | os.X_OK)]
    for name in names:
        locations.append(('source_file', source/name, os.R_OK))
    result = {'operator_uid_verified': True, 'source_owner': 'NO_VERIFICADO',
              'archive_parent_access': 'NO_VERIFICADO'}
    for label, path, access in locations:
        try:
            info = path.lstat()
            valid_kind = (stat.S_ISREG(info.st_mode) and info.st_nlink == 1) if label == 'source_file' \
                else stat.S_ISDIR(info.st_mode)
            demand(not path.is_symlink() and valid_kind, 'COLD_PATH_ALIAS')
            demand(os.access(path, access), 'COLD_OPERATOR_ACCESS_DENIED')
            if label == 'source':
                result['source_owner'] = 'MATCH_OPERATOR' if info.st_uid == uid else 'FOREIGN_UID'
            if label == 'archive_parent':
                result['archive_parent_access'] = 'ACCESS_OK'
        except (OSError, PermissionError) as exc:
            raise ColdHold('COLD_OPERATOR_ACCESS_DENIED') from exc
    demand(result['source_owner'] == 'MATCH_OPERATOR', 'COLD_OPERATOR_OWNERSHIP_UNVERIFIED')
    return result


def operator_state_witness(directory):
    """Opaque metadata fingerprint; do not read operator secrets or change pauses."""
    try:
        base = directory.lstat()
        demand(stat.S_ISDIR(base.st_mode) and not directory.is_symlink() and
               base.st_uid == os.geteuid(), 'OPERATOR_DIRECTORY_UNVERIFIED')
        children = list(directory.iterdir())
        demand(len(children) <= 128, 'OPERATOR_NAMESPACE_UNBOUNDED')
        entries = []
        for entry in sorted(children):
            info = entry.lstat()
            demand(not entry.is_symlink() and info.st_uid == os.geteuid() and
                   (stat.S_ISREG(info.st_mode) and info.st_nlink == 1 or
                    stat.S_ISDIR(info.st_mode)), 'OPERATOR_ENTRY_UNSAFE')
            entries.append((entry.name, info.st_dev, info.st_ino, info.st_size,
                            info.st_mtime_ns, info.st_ctime_ns, stat.S_IMODE(info.st_mode)))
        raw = json.dumps(entries,sort_keys=True,separators=(',',':')).encode()
        return hashlib.sha256(raw).hexdigest()
    except OSError as exc:
        raise ColdHold('OPERATOR_SNAPSHOT_UNAVAILABLE') from exc


def typed_admit(root, admission, expected_owned_ids):
    """Rootless-only replacement for the rootful _admit boundary hook.

    The original rootful implementation remains untouched. Every boundary in
    the original successor quarantiner re-invokes this exact callback.
    """
    actual = admission()
    valid_ids = type(expected_owned_ids) is dict and set(expected_owned_ids) == {'observer','dashboard'}
    valid_ids = valid_ids and all(type(cid) is str and re.fullmatch('[a-f0-9]{64}', cid)
                                  for cid in expected_owned_ids.values())
    demand(valid_ids and type(actual) is dict and actual.get('backend') == 'ROOTLESS_SPLIT'
           and actual.get('owned_ids') == expected_owned_ids
           and actual.get('owned_stopped') is True and actual.get('lock_held') is True
           and actual.get('mode') == 'PRODUCTION_PAPER'
           and actual.get('real_routes') == 'NOT_CALLED'
           and actual.get('order_routing_blocked') is True
           and actual.get('inflight_absent') is True and actual.get('critical_healthy') is True
           and actual.get('ppi_watch_accessed') is False and actual.get('boca_touched') is False
           and type(actual.get('real_orders_sent')) is int and actual['real_orders_sent'] == 0
           and actual.get('book_flat') is True, 'ROOTLESS_COLD_QUIESCENCE_REQUIRED')
    resource_reserve(root)


def quarantine_existing(successor, root, admission, expected_owned_ids, checkpoint=lambda _: None):
    """Bridge a typed rootless admission into an otherwise unmodified archive.

    This transient, single-process bridge deliberately leaves the immutable
    deployed COLD modules on disk unchanged. It only replaces the rootful
    admission hook; successor.inspect/quarantine and every fsync stay original.
    """
    original = successor.old._admit
    try:
        successor.old._admit = typed_admit
        return successor.quarantine(root, admission=admission,
                                    expected_owned_ids=expected_owned_ids, checkpoint=checkpoint)
    finally:
        successor.old._admit = original


def promotion_state(host_module, host):
    coordinates = frozen_coordinates()
    current, release = host_module.read_release_state(host.identity)
    cleanup = current.get('cleanup') or {}
    demand(all(current.get(key) == value for key,value in coordinates.items()) and
           all(release.get(key) == coordinates[key] for key in ('source_sha','source_tree','image_id')) and
           current.get('roles') == FROZEN_CIDS and current.get('phase') == 'COMPLETE' and
           cleanup.get('state') == 'COMPLETE' and current.get('cleanup_pending') is None,
           'FROZEN_RUNTIME_NOT_CURRENT')
    demand(host.boundary() == current.get('daemon_id'), 'FROZEN_ROOTLESS_DAEMON_CHANGED')
    host.check_inventory(current)
    return current


def frozen_release(host_module, host):
    current = promotion_state(host_module, host)
    roles = {role: host.check_role(current, role) for role in ('observer','dashboard')}
    demand(all(item.get('State',{}).get('Running') is True and
               item.get('State',{}).get('OOMKilled') is False for item in roles.values()),
           'RUNTIME_PAIR_NOT_RUNNING')
    demand(roles['dashboard']['State'].get('Health',{}).get('Status') == 'healthy',
           'DASHBOARD_NOT_HEALTHY')
    return current, roles


def restart_policy_witness(roles):
    """Capture the existing Docker restart policy exactly, never change it."""
    output = {}
    for role,item in roles.items():
        policy = (item.get('HostConfig') or {}).get('RestartPolicy') or {}
        name, maximum = policy.get('Name'), policy.get('MaximumRetryCount')
        demand(name in {'unless-stopped','always'} and type(maximum) is int,
               'RUNTIME_RESTART_POLICY_UNVERIFIED')
        output[role] = [name,maximum]
    return output


def controller_live(host_module, host, current):
    """Verify all six live IOL children and the actual local gateway registry."""
    observer = host.check_role(current,'observer')
    birth_floor = utc_stamp(observer['State'].get('StartedAt'))
    demand(birth_floor is not None, 'CONTROLLER_BIRTH_UNVERIFIED')
    result = host.docker.json('container', 'exec', current['roles']['observer'],
                              'python', '-c', host_module.RUNTIME_PROBE)
    now = datetime.now(timezone.utc)
    demand(type(result) is dict, 'CONTROLLER_PROBE_UNVERIFIED')
    health = result.get('controller')
    controller = host_module.classify_controller(health,
        source_sha=FROZEN_SOURCE_SHA, source_tree=FROZEN_TREE,
        now=now, started=birth_floor, provider=PROVIDER)
    demand(controller['status'] == 'READY' and
           set(controller.get('children', [])) == CONTROLLER_ROLES, 'CONTROLLER_IOL_ROLES_UNVERIFIED')
    children = health['children']
    owner = result.get('iol_gateway')
    demand(type(owner) is dict and owner.get('owner_pid') == children['iol_gateway']['pid'] and
           owner.get('registered') == {role:children[role]['pid'] for role in ('scanner','exit_reader')},
           'IOL_GATEWAY_REGISTRY_UNVERIFIED')
    demand(result.get('ppi_auth') == 'NOT_ATTEMPTED', 'IOL_PAPER_PPI_ACCESS_DETECTED')
    return controller


def read_shadow_status(root):
    """Use the deployed hash-envelope reader to prove zero routes, no guess."""
    from scripts import rc6_cold2_quarantine as original
    path = root/'data/paper_v17/artifacts/observer_v17.db/dynamic-shadow/status.json'
    status = original.envelope(path)
    demand(status.get('real_routes') == 'NOT_CALLED' and
           type(status.get('real_orders_sent')) is int and
           status['real_orders_sent'] == 0 and
           type(status.get('provider_requests')) is int and status['provider_requests'] == 0 and
           status.get('status') == 'FAIL_CLOSED', 'SHADOW_REAL_ROUTES_UNVERIFIED')
    return {'real_routes':'NOT_CALLED','real_orders_sent':0}


def stop_pair(host, current):
    """Allow the exact observer's child drain to finish before SIGKILL."""
    demand(current.get('roles') == FROZEN_CIDS, 'FROZEN_RUNTIME_NOT_CURRENT')
    windows = {}
    for role, grace, command_timeout in (('observer', 150, 180), ('dashboard', 65, 95)):
        before = datetime.now(timezone.utc)
        require_closed(before)
        host.docker.call('container', 'stop', '--time', str(grace),
                         current['roles'][role], timeout=command_timeout)
        windows[role] = (before, datetime.now(timezone.utc))
    return windows


def utc_stamp(value):
    """Parse a timezone-aware Docker timestamp without interpreting an empty epoch."""
    if not isinstance(value, str) or not value or len(value) > 64:
        return None
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if stamp.tzinfo is not None:
            return stamp.astimezone(timezone.utc)
    except ValueError:
        pass
    return None


def dashboard_uvicorn_shutdown(host, cid, window):
    """Prove ordered Uvicorn shutdown via bounded, private logs of one CID."""
    start, end = window
    command = ('/usr/bin/docker', '--host', f'unix://{host.identity.socket}',
               'container', 'logs', '--timestamps', '--since', start.isoformat(),
               '--until', end.isoformat(), '--tail', '128', cid)
    try:
        result = host.docker.run(command, check=False, text=True,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 timeout=12, env=host.identity.environment(), cwd='/')
    except (OSError, subprocess.SubprocessError):
        return False
    if (result.returncode != 0 or not isinstance(result.stdout, str) or
            not isinstance(result.stderr, str) or
            len(result.stdout) + len(result.stderr) > 65536):
        return False
    # Docker splits stdout and stderr. Uvicorn emits these lifecycle lines to
    # stderr; never expose arbitrary log text to Actions or the receipt.
    shutdown = []
    line_count = 0
    for output in (result.stdout, result.stderr):
        lines = output.splitlines()
        line_count += len(lines)
        # A full tail may have dropped the earlier shutdown and warnings.
        if line_count >= 128:
            return False
        for line in lines:
            stamp_text, _, message = line.partition(' ')
            # Uvicorn can print both positive final markers after timing out
            # and cancelling outstanding ASGI tasks. Those shutdowns are NOT
            # safe for a COLD archive, even though the process exited.
            if (re.search(r'\b(WARNING|ERROR|CRITICAL):', message) or
                    re.search(r'cancel|timeout|timed out|exception|traceback|destroyed|pending|resourcewarning',
                              message, re.IGNORECASE)):
                return False
            marker = (
                'SHUTTING' if message == 'INFO:     Shutting down' else
                'WAITING' if message == 'INFO:     Waiting for application shutdown.' else
                'COMPLETE' if message == 'INFO:     Application shutdown complete.' else
                'FINISHED' if re.fullmatch(r'INFO:     Finished server process \[[0-9]+\]',
                                             message) else None)
            if marker is None:
                continue
            stamp = utc_stamp(stamp_text)
            if stamp is None or not start <= stamp <= end:
                return False
            shutdown.append((stamp, marker))
    return (len(shutdown) == 4 and
            [item[1] for item in shutdown] ==
                ['SHUTTING', 'WAITING', 'COMPLETE', 'FINISHED'] and
            all(shutdown[i][0] <= shutdown[i + 1][0] for i in range(3)))


def dashboard_sigterm_proof(host, cid, item, previous, window):
    """Dashboard-only exception when a *specific* docker stop ended Uvicorn."""
    if (not isinstance(previous, dict) or not isinstance(item, dict) or
            not isinstance(window, (tuple, list)) or len(window) != 2):
        return False
    start, end = window
    if (not isinstance(start, datetime) or not isinstance(end, datetime) or
            start.tzinfo is None or end.tzinfo is None or
            not start <= end or not 0 <= (end - start).total_seconds() <= 95):
        return False
    before = previous.get('State')
    after = item.get('State')
    if not isinstance(before, dict) or not isinstance(after, dict):
        return False
    begun, finished = utc_stamp(after.get('StartedAt')), utc_stamp(after.get('FinishedAt'))
    if (item.get('Id') != cid or previous.get('Id') != cid or
            before.get('Running') is not True or
            before.get('StartedAt') != after.get('StartedAt') or
            begun is None or finished is None or begun >= start or
            not start <= finished <= end or
            type(previous.get('RestartCount')) is not int or
            item.get('RestartCount') != previous.get('RestartCount') or
            after.get('Running') is not False or after.get('Status') != 'exited' or
            after.get('Pid') != 0 or after.get('OOMKilled') is not False or
            after.get('ExitCode') != 143 or after.get('Error') != ''):
        return False
    return dashboard_uvicorn_shutdown(host, cid, (start, end))


def stopped_role_proof(item):
    """Fixed non-secret categories, never a raw Docker container inspection."""
    state = item.get('State')
    if type(state) is not dict:
        state = {}
    code = state.get('ExitCode')
    category = ('NON_INTEGER' if type(code) is not int else
                'ZERO' if code == 0 else
                'SIGTERM_143' if code == 143 else
                'SIGKILL_137' if code == 137 else 'OTHER_NONZERO')
    return {'running_false': state.get('Running') is False,
            'status_exited': state.get('Status') == 'exited',
            'pid_zero': type(state.get('Pid')) is int and state['Pid'] == 0,
            'oom_false': state.get('OOMKilled') is False,
            'exit_code_zero': type(code) is int and code == 0,
            'exit_code_category': category}


def stopped_admission(host_module, host, current, lock_held, *, expected_database=None,
                      expected_operator=None, stopped_after=None,
                      expected_dashboard=None, stop_window=None):
    """Recheck Docker source/CIDs + flat DB after stop at EACH archive boundary."""
    demand(lock_held is True, 'ROOTLESS_LOCK_REQUIRED')
    demand(host.boundary() == current.get('daemon_id'), 'FROZEN_ROOTLESS_DAEMON_CHANGED')
    host.check_inventory(current)
    pair = {role: host.check_role(current, role) for role in ('observer', 'dashboard')}
    evidence = {role: stopped_role_proof(item) for role, item in pair.items()}
    observer_ok = all(evidence['observer'][field] for field in (
        'running_false', 'status_exited', 'pid_zero', 'oom_false', 'exit_code_zero'))
    dashboard_base = all(evidence['dashboard'][field] for field in (
        'running_false', 'status_exited', 'pid_zero', 'oom_false'))
    dashboard_proven_143 = False
    if (dashboard_base and evidence['dashboard']['exit_code_category'] == 'SIGTERM_143'
            and isinstance(stop_window, dict) and 'dashboard' in stop_window):
        dashboard_proven_143 = dashboard_sigterm_proof(
            host, current['roles']['dashboard'], pair['dashboard'],
            expected_dashboard, stop_window['dashboard'])
    evidence['dashboard']['dashboard_sigterm_proven'] = dashboard_proven_143
    stopped = (observer_ok and dashboard_base and
               (evidence['dashboard']['exit_code_zero'] or dashboard_proven_143))
    if not stopped:
        raise StopProofHold(evidence)
    book = flat_book(host.root / host_module.DB_RELATIVE,
                     stopped=True, clean_exit=stopped, stopped_after=stopped_after)
    if expected_database is not None:
        demand(book['identity'] == expected_database, 'PAPER_DATABASE_CHANGED')
    status = read_shadow_status(host.root)
    critical = critical_broker_proof()
    legacy_writers_absent()
    operator_custody(host.root)
    if expected_operator is not None:
        demand(operator_state_witness(host.identity.operator) == expected_operator,
               'OPERATOR_PAUSE_OR_TIMER_DRIFT')
    # Both CIDs are exited with PID0; only the dashboard may have the narrowly
    # corroborated SIGTERM143. Flat PAPER book and STOP_ACK remain mandatory.
    # This is derived quiescence, never an unconditional admission boolean.
    no_inflight = stopped and book['state'] == 'PAPER_FLAT'
    routing_guard = status['real_routes'] == 'NOT_CALLED' and book['real_orders_sent'] == 0
    demand(routing_guard, 'SHADOW_REAL_ROUTES_UNVERIFIED')
    return {'backend':'ROOTLESS_SPLIT', 'owned_ids':current['roles'],
            'owned_stopped':stopped, 'lock_held':True, 'mode':book['mode'],
            'real_routes':status['real_routes'], 'order_routing_blocked':routing_guard,
            'inflight_absent':no_inflight,
            'critical_healthy':critical['state'] == 'HEALTHY_ISSUES_ONLY',
            'ppi_watch_accessed':False, 'boca_touched':False,
            'real_orders_sent':book['real_orders_sent'], 'book_flat':True}


def ensure_pair_restored(host, current, started):
    """Resolve even an UNKNOWN stop outcome and revive the same two CIDs."""
    demand(current.get('roles') == FROZEN_CIDS, 'FROZEN_RUNTIME_NOT_CURRENT')
    demand(host.boundary() == current.get('daemon_id'), 'FROZEN_ROOTLESS_DAEMON_CHANGED')
    host.check_inventory(current)
    failures = []
    observer_pre = host.inspect(current['roles']['observer'])
    already_running = observer_pre.get('State',{}).get('Running') is True
    # The stopped observer wrote a clean STOP_ACK heartbeat *after* the
    # recovery's initial started timestamp. Treating that pre-restart STOPPED
    # row as a new heartbeat makes the immutable postflight fail immediately.
    # Keep its existing PENDING/wait loop by fencing at the observer's restart.
    birth_floor = started - timedelta(days=30) if already_running else None
    for role in ('dashboard','observer'):
        cid = current['roles'][role]
        try:
            item = host.inspect(cid)
            # An observer seen running at the first probe may have stopped
            # before this second probe. Its next start is a new birth too.
            if role == 'observer' and (not already_running or
                                       item.get('State',{}).get('Running') is False):
                birth_floor = datetime.now(timezone.utc)
            if item.get('State', {}).get('Running') is False:
                host.docker.call('container','start',cid,timeout=95)
            else:
                demand(item.get('State', {}).get('Running') is True,
                       'RUNTIME_RESTORE_STATE_UNVERIFIED')
        except (OSError, ValueError, RuntimeError):
            failures.append(role)
    demand(not failures, 'RUNTIME_RESTART_FAILED')
    try:
        # The immutable controller's already-live children predate a resume
        # request. Postflight still requires fresh heartbeat/PID health, but
        # must not invent that those child PIDs were born during this request.
        postflight = host.postflight(current, birth_floor, budget=600)
    except (OSError, ValueError, RuntimeError) as exc:
        from scripts import porota_rootless_host as frozen_host
        category = str(exc)
        if isinstance(exc, frozen_host.Hold) and re.fullmatch(r'[A-Z0-9_]{5,100}',category):
            # A public, typed host failure is essential to distinguish a
            # transient boot from a PAPER/identity safety violation.
            raise ColdHold(category) from exc
        raise ColdHold('RUNTIME_POSTFLIGHT_UNVERIFIED') from exc
    demand(postflight.get('dashboard') == 'healthy' and postflight.get('orders_guard') == 'BLOCKED' and
           postflight.get('ppi_auth') == 'NOT_ATTEMPTED', 'IOL_RESTORATION_PROOF_INCOMPLETE')
    return {'state':'RESTORED_ORIGINAL_RUNTIME', 'roles':2, 'controller_roles':len(CONTROLLER_ROLES)}


def restored_receipt_proves_safe_skip(receipt):
    """Do not run a second recovery when the same Action already restored it."""
    return (type(receipt) is dict and
            receipt.get('schema') == 'rc6.rootless.cold-recovery.v1' and
            receipt.get('operation') == 'cold_recover' and
            receipt.get('state') == 'BLOQUEADO' and
            all(receipt.get(key) == value for key,value in frozen_coordinates().items()) and
            receipt.get('snapshot_id') == FAILED_COLD['snapshot_id'] and
            receipt.get('manifest_sha256') == FAILED_COLD['manifest_sha256'] and
            type(receipt.get('controller_roles')) is int and
            receipt['controller_roles'] == len(CONTROLLER_ROLES) and
            type(receipt.get('real_orders_sent')) is int and
            receipt['real_orders_sent'] == 0 and
            receipt.get('paper') == 'PAPER_FLAT' and
            receipt.get('critical') == 'HEALTHY_ISSUES_ONLY' and
            receipt.get('pack_content_hash_verified') is True and
            receipt.get('operator_state_unchanged') is True and
            receipt.get('restoration') == 'RESTORED_ORIGINAL_RUNTIME' and
            receipt.get('restoration_failure_signature') is None and
            receipt.get('runtime_validated') is True and
            receipt.get('ppi_watch_touched') is False and
            receipt.get('boca_touched') is False and
            receipt.get('transfer_cleanup') == 'COMPLETE')


def resume_only(host_module, host):
    """Recover exact stopped CIDs after uncertain SSH/process outcomes.

    No archive/queue mutation; no new image, containers, labels, or locks.
    Callable independently from the same Action when a previous run fails.
    """
    identity = host.identity
    started = datetime.now(timezone.utc)
    with host_module.user_lock(identity.state/'deploy.lock'):
        current = promotion_state(host_module,host)
        roles = {role:host.check_role(current,role) for role in ('observer','dashboard')}
        policy = restart_policy_witness(roles)
        operator = operator_state_witness(identity.operator)
        stop_receipt = verify_stop_witness(identity.state,current['roles'],operator,
                                            with_receipt=True)
        stop_token = stop_receipt['nonce']
        critical_broker_proof()
        observer = roles['observer'].get('State',{})
        if observer.get('Running') is False:
            demand(observer.get('ExitCode') == 0 and observer.get('Pid') == 0 and
                   observer.get('OOMKilled') is False, 'RUNTIME_UNSAFE_TO_RESUME')
            flat_book(host.root/host_module.DB_RELATIVE,stopped=True,clean_exit=True,restore=True,
                      stopped_after=datetime.fromtimestamp(stop_receipt['created_at'],timezone.utc))
        else:
            demand(observer.get('Running') is True, 'RUNTIME_UNSAFE_TO_RESUME')
            flat_book(host.root/host_module.DB_RELATIVE,restore=True)
        ensure_pair_restored(host,current,started)
        host.check_inventory(current)
        new_roles = {role:host.check_role(current,role) for role in ('observer','dashboard')}
        demand(restart_policy_witness(new_roles) == policy and
               operator_state_witness(identity.operator) == operator,
               'RUNTIME_OR_OPERATOR_POLICY_CHANGED')
        critical_broker_proof()
        clear_stop_witness(identity.state,stop_token)
    return {'schema':'rc6.rootless.cold-recovery.v1','operation':'cold_resume',
            'state':'RUNTIME_RESTORED',**frozen_coordinates(),
            'snapshot_id':FAILED_COLD['snapshot_id'],'manifest_sha256':FAILED_COLD['manifest_sha256'],
            'controller_roles':len(CONTROLLER_ROLES),'real_orders_sent':0,'runtime_validated':True,
            'restoration':'RESTORED_ORIGINAL_RUNTIME',
            'ppi_watch_touched':False,'boca_touched':False,
            'temporary_stage_writes':True}


def operate(operation, root):
    """One-shot script staged under the rootless SSH account from Actions."""
    coordinates = frozen_coordinates()
    from scripts import porota_rootless_host as host_module
    from scripts import rc6_cold2_successor as successor
    demand(root == Path('/opt/porota-trading'), 'ROOT_PATH_NOT_ALLOWED')
    host = host_module.Host(root)
    if operation == 'cold_resume':
        return resume_only(host_module,host)
    probe_stage = 'FROZEN_RELEASE'
    try:
        current, roles = frozen_release(host_module, host)
        probe_stage = 'CONTROLLER_LIVE'
        controller = controller_live(host_module, host, current)
        probe_stage = 'PAPER_BOOK'
        book = flat_book(root / host_module.DB_RELATIVE)
        probe_stage = 'ARCHIVE_INSPECT'
        before = pinned_incident(successor, root, hash_pack=True)
        names = before['source_names']
        probe_stage = 'OPERATOR_CUSTODY'
        custody = operator_custody(root, names)
        probe_stage = 'RESOURCE_RESERVE'
        resources = resource_reserve(root)
        probe_stage = 'RESTART_POLICY'
        policy = restart_policy_witness(roles)
        probe_stage = 'OPERATOR_STATE'
        operator = operator_state_witness(host.identity.operator)
        probe_stage = 'CRITICAL_BROKER'
        critical = critical_broker_proof()
        probe_stage = 'LEGACY_WRITERS'
        legacy = legacy_writers_absent()
    except (OSError, KeyError, ValueError, host_module.Hold) as exc:
        if isinstance(exc, json.JSONDecodeError):
            category = f'COLD_{probe_stage}_JSON_DECODE_ERROR'
        elif isinstance(exc, OSError):
            category = f'COLD_{probe_stage}_OSERROR'
        elif isinstance(exc, KeyError):
            category = f'COLD_{probe_stage}_KEYERROR'
        else:
            category = str(exc)
        if not re.fullmatch(r'[A-Z0-9_]{5,100}', category):
            category = f'COLD_{probe_stage}_INVALID_VALUE'
        errno = exc.errno if isinstance(exc, OSError) else None
        return {'schema':'rc6.rootless.cold-recovery.v1', 'operation':operation,
                'state':'BLOQUEADO', 'failure_signature':category,
                'failure_stage':probe_stage,
                'failure_errno':errno if type(errno) is int and 0 <= errno <= 255 else None,
                **coordinates,
                'real_orders_sent':None, 'mutations':0, 'ppi_watch_touched':False,
                'boca_touched':False}
    receipt = {'schema':'rc6.rootless.cold-recovery.v1', 'operation':operation,
               'state':'READ_ONLY_OBSERVED', **coordinates, 'roles':controller['children'],
               'controller_roles':len(CONTROLLER_ROLES), 'runtime_validated':True, 'paper':book['state'],
               'session':book['session'], 'cold_queue_failed':before['queue_failed'],
               'ppi_auth':book['ppi_auth'],
               'archive_complete':before['archive_complete'],
               'pack_content_hash_verified':True,
               **{key:before[key] for key in FAILED_COLD},
               'free_bytes':resources['free_bytes'],
               'free_inodes':resources['free_inodes'],
               'custody':custody, 'critical':critical['state'],
               'legacy_writers':legacy['state'],
               'restart_policies':policy, 'operator_state_unchanged':True,
               'real_orders_sent':0, 'mutations':0, 'temporary_stage_writes':True,
               'ppi_watch_touched':False, 'boca_touched':False}
    if operation == 'cold_inspect':
        return receipt
    demand(operation == 'cold_recover', 'COLD_OPERATION_INVALID')
    # We never stop healthy runtime unless there is an exact FAILED successor.
    demand(before['queue_failed'] is True and before['archive_complete'] is False,
           'FAILED_COLD_QUEUE_NOT_PRESENT')
    started = datetime.now(timezone.utc)
    stop_attempted = False
    stop_token = None
    result = None
    restore_failure = None
    recovery_failure = None
    recovery_stage = 'STOP_PAIR'
    with host_module.user_lock(host.identity.state/'deploy.lock'):
        # Fence against the lock-acquisition gap before touching runtime.
        current_recheck, roles_recheck = frozen_release(host_module, host)
        demand(current_recheck == current and restart_policy_witness(roles_recheck) == policy,
               'RUNTIME_IDENTITY_DRIFT')
        demand(operator_state_witness(host.identity.operator) == operator,
               'OPERATOR_PAUSE_OR_TIMER_DRIFT')
        critical_broker_proof()
        legacy_writers_absent()
        controller_live(host_module, host, current)
        demand(flat_book(root / host_module.DB_RELATIVE)['identity'] == book['identity'],
               'PAPER_DATABASE_CHANGED')
        pending = pinned_incident(successor, root, hash_pack=True)
        demand(pending['queue_failed'] is True and pending['archive_complete'] is False,
               'FAILED_COLD_QUEUE_NOT_PRESENT')
        # A restore from a separate Action is fenced to THIS incident.
        # Without a durable STOP witness, operator-paused containers MUST NOT
        # be restarted by the standalone cold_resume operation.
        stop_token = arm_stop_witness(host.identity.state,current['roles'],operator)
        try:
            stop_attempted = True
            stop_window = stop_pair(host, current)
            recovery_stage = 'POSTSTOP_ADMISSION'
            def admit_exact_incident():
                admission = stopped_admission(host_module, host, current, True,
                    expected_database=book['identity'], expected_operator=operator,
                    stopped_after=started, expected_dashboard=roles_recheck['dashboard'],
                    stop_window=stop_window)
                pinned = pinned_incident(successor, root, hash_pack=False)
                demand(pinned['queue_failed'] is True, 'FAILED_COLD_QUEUE_NOT_PRESENT')
                return admission

            admit_exact_incident()
            # The successor's archive algorithm and all its fsync boundaries
            # are preserved, under a typed rootless revalidation callback.
            recovery_stage = 'COLD_QUARANTINE'
            result = quarantine_existing(successor, root, admit_exact_incident,
                                         dict(current['roles']))
            recovery_stage = 'COLD_VERIFY'
            end = pinned_incident(successor, root, hash_pack=True)
            demand(end['archive_complete'] is True and end['queue_failed'] is False,
                   'COLD_QUARANTINE_INCOMPLETE')
        except (OSError, KeyError, ValueError, RuntimeError) as exc:
            recovery_failure = exc
        finally:
            # Even SSH uncertainty/partial STOP must restore the exact CIDs.
            if stop_attempted:
                try:
                    ensure_pair_restored(host, current, started)
                    host.check_inventory(current)
                    live_roles = {role: host.check_role(current,role)
                                  for role in ('observer','dashboard')}
                    demand(restart_policy_witness(live_roles) == policy and
                           operator_state_witness(host.identity.operator) == operator,
                           'RUNTIME_OR_OPERATOR_POLICY_CHANGED')
                    critical_broker_proof()
                    clear_stop_witness(host.identity.state,stop_token)
                except ColdHold as exc:
                    restore_failure = str(exc)
                except (OSError, ValueError, RuntimeError):
                    restore_failure = 'RUNTIME_RESTORE_UNVERIFIED'
        if restore_failure or recovery_failure:
            primary = str(recovery_failure) if recovery_failure else None
            if primary is not None and not re.fullmatch(r'[A-Z0-9_]{5,100}',primary):
                primary = 'COLD_RECOVERY_UNVERIFIED'
            secondary = restore_failure
            if secondary is not None and not re.fullmatch(r'[A-Z0-9_]{5,100}',secondary):
                secondary = 'RUNTIME_RESTORE_UNVERIFIED'
            stop_evidence = (recovery_failure.evidence if isinstance(recovery_failure, StopProofHold)
                             else None)
            return {**receipt, 'operation':'cold_recover', 'state':'BLOQUEADO',
                    'failure_signature':secondary or primary,
                    'recovery_failure_signature':primary,
                    'recovery_failure_stage':recovery_stage if primary else None,
                    'stopped_pair_evidence':stop_evidence,
                    'restoration_failure_signature':secondary,
                    'archive_complete':False,
                    'cold_queue_failed':None, 'runtime_validated':restore_failure is None,
                    'restoration':'RESTORED_ORIGINAL_RUNTIME' if restore_failure is None else 'NO_VERIFICADO',
                    'mutations':None}
    return {**receipt, 'operation':'cold_recover', 'state':'COLD_ARCHIVE_COMPLETE',
            'archive_state':result['state'], 'archive_complete':True,
            'cold_queue_failed':False, 'runtime_validated':True,
            'restoration':'RESTORED_ORIGINAL_RUNTIME',
            'controller_roles':len(CONTROLLER_ROLES), 'mutations':1}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('cold_inspect','cold_recover','cold_resume'))
    parser.add_argument('--root', type=Path, default=Path('/opt/porota-trading'))
    args = parser.parse_args(argv)
    try:
        result = operate(args.operation, args.root)
    except (ColdHold, ValueError, OSError, RuntimeError) as exc:
        reason = str(exc) if re.fullmatch('[A-Z0-9_]{5,100}', str(exc)) else 'COLD_OPERATION_UNVERIFIED'
        result = {'schema':'rc6.rootless.cold-recovery.v1','operation':args.operation,
                  'state':'BLOQUEADO','failure_signature':reason,
                  'real_orders_sent':None, 'ppi_watch_touched':False,'boca_touched':False}
    print(json.dumps(result, sort_keys=True))
    raise SystemExit(0 if result['state'] in ('READ_ONLY_OBSERVED','COLD_ARCHIVE_COMPLETE',
                                           'RUNTIME_RESTORED') else 2)


if __name__ == '__main__':
    main()
