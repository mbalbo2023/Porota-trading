#!/usr/bin/env python3
"""Bounded ROOTLESS_SPLIT recovery of an existing FAILED COLD queue.

This is an Actions-only one-shot host maintenance adapter. The immutable
deployed image is neither rebuilt nor replaced. The archive algorithm remains
in the *deployed-source* rc6_cold2_successor module, pinned by the transport.
No market-calendar veto: only physical PAPER, flat-book, operator, Docker and
integrity gates can deny a potentially destructive recovery.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sqlite3
import stat
import sys

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

# Taken from the *verified* Deploy V2 #43758003307, not main or product HEAD.
FROZEN_SOURCE_SHA = '21b6d6b3882a35f1f5ad0469df739c5b0d6d2f76'
FROZEN_TREE = 'b83cc6c167b0b80837099fe035f83311947a744e'
FROZEN_CIDS = {
    'observer': 'fffe3a7f286bef92bdc02076d8b76adb1631299645c879fea0786af9d3d908fa',
    'dashboard': '66775f99bf999b23e433f8930f1a041727002915cd43faf3eacf8e70e653943f',
}
RESERVE = 2 * 1024**3


class ColdHold(ValueError):
    """Public fixed-category fail-closed reason; no private host paths."""


def demand(condition, reason):
    if not condition:
        raise ColdHold(reason)


def flat_book(path, *, stopped=False, clean_exit=False):
    """Read the already-deployed SQLite PAPER book; never mutate the DB/WAL."""
    try:
        before = path.lstat()
        demand(stat.S_ISREG(before.st_mode) and before.st_nlink == 1,
               'PAPER_DATABASE_ALIAS')
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=3)) as db:
            db.execute('PRAGMA query_only=ON')
            db.execute('PRAGMA busy_timeout=1000')
            rows = db.execute('SELECT mode,real_orders_sent,process_state,session_state,'
                              'heartbeat_at,ppi_auth,detail '
                              'FROM observer_state WHERE id=1').fetchall()
            work = db.execute('SELECT dataset_id FROM paper_workspace WHERE id=1').fetchall()
            demand(len(rows) == len(work) == 1 and bool(work[0][0]), 'PAPER_DATABASE_IDENTITY')
            mode, orders, process, session, heartbeat, ppi_auth, detail = rows[0]
            demand(mode == 'PRODUCTION_PAPER' and type(orders) is int and orders == 0,
                   'PAPER_ZERO_REQUIRED')
            if stopped:
                demand(clean_exit is True and
                       ((process == 'STOPPED' and session in
                         {'OPEN','MARKET_OPEN','CLOSED','MARKET_CLOSED','PREOPEN'}) or
                        (process in {'RUNNING','WAITING_MARKET','READY_PREOPEN','DEGRADED'} and
                         session in {'OPEN','MARKET_OPEN','CLOSED','MARKET_CLOSED','PREOPEN'})),
                       'PAPER_STOP_ACK_UNVERIFIED')
            else:
                demand((process == 'RUNNING' and session == 'MARKET_OPEN') or
                       (process == 'WAITING_MARKET' and session in {'CLOSED','MARKET_CLOSED'}) or
                       (process == 'READY_PREOPEN' and session == 'PREOPEN') or
                       (process == 'DEGRADED' and ppi_auth in {'ERROR','COOLDOWN'} and
                        session in {'MARKET_OPEN','OPEN','CLOSED','MARKET_CLOSED','PREOPEN'} and
                        isinstance(detail,str) and detail.startswith(
                            ('Login fallo:','PPI en espera de reintento;',
                             'Login local bloqueado por interlock'))),
                       'PAPER_PROCESS_NOT_QUIESCIBLE')
                try:
                    stamp = datetime.fromisoformat(str(heartbeat).replace('Z','+00:00'))
                    delta = (datetime.now(timezone.utc) - stamp).total_seconds()
                    demand(stamp.tzinfo is not None and 0 <= delta <= 180,
                           'PAPER_HEARTBEAT_UNVERIFIED')
                except (ValueError, TypeError) as exc:
                    raise ColdHold('PAPER_HEARTBEAT_UNVERIFIED') from exc
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
                'process': process, 'session': session,
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
    for name in ('porota_production_observer','porota_production_dashboard'):
        try:
            proc = subprocess.run(['sudo','-n','/usr/bin/docker','container','ls',
                                   '--format','{{.Names}}','--filter','name=^'+name+'$'],
                                  capture_output=True,text=True,timeout=15,cwd='/')
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ColdHold('LEGACY_DOCKER_INVENTORY_UNAVAILABLE') from exc
        demand(proc.returncode == 0 and len(proc.stdout) <= 1024 and
               all(entry == name for entry in proc.stdout.splitlines()),
               'LEGACY_DOCKER_INVENTORY_UNVERIFIED')
        demand(not proc.stdout.strip(), 'LEGACY_PAPER_WRITER_ACTIVE')
    return {'state':'NO_LEGACY_WRITERS','checked_roles':2}


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


def frozen_release(host_module, host):
    identity = host.identity
    current, release = host_module.read_release_state(identity)
    demand(current['source_sha'] == FROZEN_SOURCE_SHA and current['source_tree'] == FROZEN_TREE
           and current['roles'] == FROZEN_CIDS and
           release['source_sha'] == FROZEN_SOURCE_SHA, 'FROZEN_RUNTIME_NOT_CURRENT')
    host.boundary()
    host.check_inventory(current)
    roles = {role: host.check_role(current, role) for role in ('observer','dashboard')}
    demand(all(item.get('State',{}).get('Running') is True and
               item.get('State',{}).get('OOMKilled') is False for item in roles.values()),
           'RUNTIME_PAIR_NOT_RUNNING')
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
    """Require eight live, fresh children of the immutable observer process."""
    result = host.docker.json('container', 'exec', current['roles']['observer'],
                              'python', '-c', host_module.RUNTIME_PROBE)
    now = datetime.now(timezone.utc)
    # Existing already-running children predate this diagnostic.
    from datetime import timedelta
    birth_floor = now - timedelta(days=30)
    controller = host_module.classify_controller(result.get('controller'),
        source_sha=FROZEN_SOURCE_SHA, source_tree=FROZEN_TREE,
        now=now, started=birth_floor)
    demand(controller['status'] == 'READY' and
           len(controller.get('children', [])) == 8, 'CONTROLLER_EIGHT_ROLES_UNVERIFIED')
    return controller


def read_shadow_status(root):
    """Use the deployed hash-envelope reader to prove zero routes, no guess."""
    from scripts import rc6_cold2_quarantine as original
    path = root/'data/paper_v17/artifacts/observer_v17.db/dynamic-shadow/status.json'
    status = original.envelope(path)
    demand(status.get('real_routes') == 'NOT_CALLED' and
           type(status.get('real_orders_sent')) is int and
           status['real_orders_sent'] == 0 and
           status.get('status') == 'FAIL_CLOSED', 'SHADOW_REAL_ROUTES_UNVERIFIED')
    return {'real_routes':'NOT_CALLED','real_orders_sent':0}


def stopped_admission(host_module, host, current, lock_held, *, expected_database=None,
                      expected_operator=None):
    """Recheck Docker source/CIDs + flat DB after stop at EACH archive boundary."""
    demand(lock_held is True, 'ROOTLESS_LOCK_REQUIRED')
    host.boundary()
    host.check_inventory(current)
    pair = {role: host.check_role(current, role) for role in ('observer', 'dashboard')}
    stopped = all(item.get('State',{}).get('Running') is False and
               item.get('State',{}).get('Status') == 'exited' and
               item.get('State',{}).get('Pid') == 0 and
               item.get('State',{}).get('OOMKilled') is False and
               type(item.get('State',{}).get('ExitCode')) is int and
               item.get('State',{}).get('ExitCode') == 0 for item in pair.values())
    demand(stopped, 'ROOTLESS_PAIR_NOT_CLEANLY_STOPPED')
    book = flat_book(host.root / host_module.DB_RELATIVE,
                     stopped=True, clean_exit=stopped)
    if expected_database is not None:
        demand(book['identity'] == expected_database, 'PAPER_DATABASE_CHANGED')
    status = read_shadow_status(host.root)
    critical = critical_broker_proof()
    legacy_writers_absent()
    operator_custody(host.root)
    if expected_operator is not None:
        demand(operator_state_witness(host.identity.operator) == expected_operator,
               'OPERATOR_PAUSE_OR_TIMER_DRIFT')
    # Both CIDs are now exited with kernel Pid=0, exit 0, no other containers
    # exist on this scoped daemon, and the PAPER book has no liabilities.
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
    failures = []
    observer_pre = host.inspect(current['roles']['observer'])
    already_running = observer_pre.get('State',{}).get('Running') is True
    for role in ('dashboard','observer'):
        cid = current['roles'][role]
        try:
            item = host.inspect(cid)
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
        from datetime import timedelta
        birth_floor = started - timedelta(days=30) if already_running else started
        host.postflight(current, birth_floor, budget=600)
    except (OSError, ValueError, RuntimeError) as exc:
        raise ColdHold('RUNTIME_POSTFLIGHT_UNVERIFIED') from exc
    return {'state':'RESTORED_ORIGINAL_RUNTIME', 'roles':2, 'controller_roles':8}


def resume_only(host_module, host):
    """Recover exact stopped CIDs after uncertain SSH/process outcomes.

    No archive/queue mutation; no new image, containers, labels, or locks.
    Callable independently from the same Action when a previous run fails.
    """
    identity = host.identity
    started = datetime.now(timezone.utc)
    with host_module.user_lock(identity.state/'deploy.lock'):
        current, release = host_module.read_release_state(identity)
        demand(current['source_sha'] == FROZEN_SOURCE_SHA and
               current['source_tree'] == FROZEN_TREE and current['roles'] == FROZEN_CIDS and
               release['source_sha'] == FROZEN_SOURCE_SHA,
               'FROZEN_RUNTIME_NOT_CURRENT')
        host.boundary()
        host.check_inventory(current)
        roles = {role:host.check_role(current,role) for role in ('observer','dashboard')}
        policy = restart_policy_witness(roles)
        operator = operator_state_witness(identity.operator)
        critical_broker_proof()
        observer = roles['observer'].get('State',{})
        if observer.get('Running') is False:
            demand(observer.get('ExitCode') == 0 and observer.get('Pid') == 0 and
                   observer.get('OOMKilled') is False, 'RUNTIME_UNSAFE_TO_RESUME')
            flat_book(host.root/host_module.DB_RELATIVE,stopped=True,clean_exit=True)
        else:
            demand(observer.get('Running') is True, 'RUNTIME_UNSAFE_TO_RESUME')
            flat_book(host.root/host_module.DB_RELATIVE)
        ensure_pair_restored(host,current,started)
        host.check_inventory(current)
        new_roles = {role:host.check_role(current,role) for role in ('observer','dashboard')}
        demand(restart_policy_witness(new_roles) == policy and
               operator_state_witness(identity.operator) == operator,
               'RUNTIME_OR_OPERATOR_POLICY_CHANGED')
        critical_broker_proof()
    return {'schema':'rc6.rootless.cold-recovery.v1','operation':'cold_resume',
            'state':'RUNTIME_RESTORED','source_sha':FROZEN_SOURCE_SHA,
            'controller_roles':8,'real_orders_sent':0,'runtime_validated':True,
            'restoration':'RESTORED_ORIGINAL_RUNTIME',
            'ppi_watch_touched':False,'boca_touched':False,
            'temporary_stage_writes':True}


def operate(operation, root):
    """One-shot script staged under the rootless SSH account from Actions."""
    from scripts import porota_rootless_host as host_module
    from scripts import rc6_cold2_successor as successor
    demand(root == Path('/opt/porota-trading'), 'ROOT_PATH_NOT_ALLOWED')
    host = host_module.Host(root)
    if operation == 'cold_resume':
        return resume_only(host_module,host)
    try:
        current, roles = frozen_release(host_module, host)
        controller = controller_live(host_module, host, current)
        book = flat_book(root / host_module.DB_RELATIVE)
        before = successor.inspect(root, hash_pack=True)
        names = before['source_names']
        custody = operator_custody(root, names)
        resources = resource_reserve(root)
        policy = restart_policy_witness(roles)
        operator = operator_state_witness(host.identity.operator)
        critical = critical_broker_proof()
        legacy = legacy_writers_absent()
    except (OSError, KeyError, ValueError, host_module.Hold) as exc:
        category = str(exc) if isinstance(exc, (ColdHold, host_module.Hold, ValueError)) else 'HOST_PROBE_UNVERIFIED'
        if not re.fullmatch(r'[A-Z0-9_]{5,100}', category):
            category = 'HOST_PROBE_UNVERIFIED'
        return {'schema':'rc6.rootless.cold-recovery.v1', 'operation':operation,
                'state':'BLOQUEADO', 'failure_signature':category,
                'real_orders_sent':None, 'mutations':0, 'ppi_watch_touched':False,
                'boca_touched':False}
    receipt = {'schema':'rc6.rootless.cold-recovery.v1', 'operation':operation,
               'state':'READ_ONLY_OBSERVED', 'source_sha':FROZEN_SOURCE_SHA,
               'source_tree':FROZEN_TREE, 'roles':list(controller.get('children',[])),
               'controller_roles':8, 'runtime_validated':True, 'paper':book['state'],
               'session':book['session'], 'cold_queue_failed':before['queue_failed'],
               'archive_complete':before['archive_complete'],
               'pack_content_hash_verified':True,
               'failure_code':before['failure_code'],
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
    result = None
    restore_failure = None
    recovery_failure = None
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
        successor.inspect(root, hash_pack=True)
        try:
            stop_attempted = True
            for role in ('observer','dashboard'):
                cid = current['roles'][role]
                host.docker.call('container','stop','--time','65',cid,timeout=95)
            stopped_admission(host_module, host, current, True,
                              expected_database=book['identity'], expected_operator=operator)
            # The successor's archive algorithm and all its fsync boundaries
            # are preserved, under a typed rootless revalidation callback.
            result = quarantine_existing(successor, root,
                lambda: stopped_admission(host_module, host, current, True,
                    expected_database=book['identity'], expected_operator=operator),
                dict(current['roles']))
            end = successor.inspect(root, hash_pack=True)
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
                except ColdHold as exc:
                    restore_failure = str(exc)
                except (OSError, ValueError, RuntimeError):
                    restore_failure = 'RUNTIME_RESTORE_UNVERIFIED'
        if restore_failure or recovery_failure:
            reason = restore_failure or str(recovery_failure)
            if not re.fullmatch('[A-Z0-9_]{5,100}', reason):
                reason = 'COLD_RECOVERY_UNVERIFIED'
            return {**receipt, 'operation':'cold_recover', 'state':'BLOQUEADO',
                    'failure_signature':reason, 'archive_complete':False,
                    'cold_queue_failed':None, 'runtime_validated':restore_failure is None,
                    'restoration':'RESTORED_ORIGINAL_RUNTIME' if restore_failure is None else 'NO_VERIFICADO',
                    'mutations':None}
    return {**receipt, 'operation':'cold_recover', 'state':'COLD_ARCHIVE_COMPLETE',
            'archive_state':result['state'], 'archive_complete':True,
            'cold_queue_failed':False, 'runtime_validated':True,
            'restoration':'RESTORED_ORIGINAL_RUNTIME',
            'controller_roles':8, 'mutations':1}


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
                                           'RUNTIME_RESTORED') else 79)


if __name__ == '__main__':
    main()
