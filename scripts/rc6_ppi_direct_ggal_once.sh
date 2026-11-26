#!/usr/bin/env bash
set -Eeuo pipefail

report=$(python3 - "$@" <<'HOST_PY'
import fcntl
import json
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess
import sys
import time

IMAGE = 'sha256:b2e7dcd7668c41f16659886fd38ce1333ce6cb4688cacd63eedbc81825537067'
PRODUCT = '8fd9070728b89ae7891758f4b463baef28d875f8'
HOST_LOCK_WAIT_S = 30
COOLDOWN_WAIT_LIMIT_S = 20 * 60

PROBE = r'''
from contextlib import closing, redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from importlib.metadata import version
import fcntl
import io
import json
import math
import os
from pathlib import Path
import re
import select
import signal
import sqlite3
import stat
import time
from zoneinfo import ZoneInfo

from requests.adapters import HTTPAdapter
from urllib3.util import Retry
from bd_ppi_readonly_guard import ppi_rate_limit_headers, ppi_retry_after_seconds
from bf_production_paper_observer import _market_phase, _secret
from bv_paper_runtime import ppi_gateway_lifetime_lease
from cg_paper_workspace import database_path
from bs_instrument_contracts import cash_currency
from rc6_multisource_discovery import canonical_family, canonical_market
from ppi_client.api.constants import ACCOUNT_LOGIN_API, MARKETDATA_BOOK, MARKETDATA_CURRENT
from ppi_client.ppi import PPI
from ppi_client.ppi_api_client import PPIClient
from rc6_ppi_gateway_reader_adapter import gateway_socket_path
from rc6_ppi_global_budget import PaperDirectReadBudget
from rc6_ppi_shared_feed import ReadOnlyUnixFeedClient
from rc6_ppi_shared_session_owner import AUTH_COOLDOWN_SECONDS
from rc6_scanner_login_interlock import ScannerLoginInterlock

ART = ZoneInfo('America/Argentina/Buenos_Aires')
KEY = ('GGAL', 'ACCIONES', 'BYMA', 'ARS', 'A-24HS')
BASE = 'https://clientapi.portfoliopersonal.com/api/'
WORKER = (b'bv_paper_runtime.py', b'--ppi-gateway-worker')


def require(ok, code):
    if not ok:
        raise RuntimeError(code)


def reason(error):
    value = str(error)
    return value if re.fullmatch(r'(?:PPI|RC6)_[A-Z0-9_]{1,90}', value) else type(error).__name__


def paper_state(db):
    with closing(sqlite3.connect(db.as_uri() + '?mode=ro', uri=True, timeout=1)) as con:
        con.execute('PRAGMA query_only=ON')
        con.execute('BEGIN')
        mode, real, auth, last = con.execute(
            'SELECT mode,real_orders_sent,ppi_auth,last_market_data_at FROM observer_state WHERE id=1'
        ).fetchone()
        require(mode == 'PRODUCTION_PAPER' and type(real) is int and real == 0, 'RC6_PAPER_CERO_NO_ACREDITADO')
        exposure = sum(con.execute(f'SELECT count(*) FROM {table} WHERE status=?', (state,)).fetchone()[0]
                       for table, state in (('paper_positions','OPEN'),
                                            ('paper_future_positions','ACTIVE'), ('paper_cauciones','OPEN')))
        last_mark = valid_clock(last)
        return {'mode': mode, 'real_orders_sent': real, 'open_paper': exposure,
                'auth_ok': auth == 'OK', 'last_market_art': last_mark.astimezone(ART).isoformat() if last_mark else None}


def process_argv(pid):
    values = Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\x00')
    return tuple(value.rsplit(b'/', 1)[-1] for value in values[:-1])


def owner_status(path):
    return ReadOnlyUnixFeedClient(socket_path=path, timeout_seconds=0.7).owner_status()


def login_wait(interlock):
    directory = os.open(interlock.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        marker = interlock._read(directory)
    finally:
        os.close(directory)
    now = interlock.monotonic_ns()
    require(marker and marker['boot_id'] == interlock.boot_id() and now >= marker['monotonic_ns'],
            'PPI_RELOJ_LOGIN_NO_VERIFICADO')
    return max(0, math.ceil((marker['monotonic_ns'] + max(interlock.cooldown_ns, marker['cooldown_ns']) - now) / 1e9))


def budget_wait(budget):
    fd = os.open(budget.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        meta = os.fstat(fd)
        require(stat.S_ISREG(meta.st_mode) and meta.st_nlink == 1 and not meta.st_mode & 0o022,
                'PPI_PRESUPUESTO_NO_VERIFICADO')
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('PPI_GET_EN_VUELO') from None
        return max(0, math.ceil(budget._load_breaker(fd)['hold_until'] - budget.clock()))
    finally:
        os.close(fd)


def valid_clock(value):
    if type(value) is not str or len(value) > 50:
        return None
    try:
        mark = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return mark if mark.utcoffset() is not None else None
    except ValueError:
        return None


def number(value):
    if type(value) not in (int, float):
        return None
    try:
        return value if math.isfinite(value) else None
    except OverflowError:
        return None


def best_side(payload, field, highest):
    levels = payload.get(field)
    if not isinstance(levels, list):
        return {'levels': None}
    valid = [(price, qty) for level in levels if isinstance(level, dict)
             and (price := number(level.get('price'))) is not None and price > 0
             and (qty := number(level.get('quantity'))) is not None and qty > 0]
    if not valid:
        return {'levels': len(levels), 'positive_levels': 0}
    best = (max if highest else min)(valid, key=lambda pair: pair[0])
    return {'levels': len(levels), 'positive_levels': len(valid), 'price': best[0], 'quantity': best[1]}


def describe(payload, received, endpoint):
    if not isinstance(payload, dict):
        return {'json_object': False}
    at = valid_clock(payload.get('date'))
    desc = {'json_object': True, 'date_art': at.astimezone(ART).isoformat() if at else None,
            'age_s': round((received - at).total_seconds(), 3) if at else None,
            'settlement_field': 'settlement' in payload}
    if endpoint == 'Current':
        return {**desc, 'price': number(payload.get('price')), 'price_type': type(payload.get('price')).__name__,
                'volume': number(payload.get('volume'))}
    return {**desc, 'bid': best_side(payload, 'bids', True), 'offer': best_side(payload, 'offers', False)}


def send(rest, method, uri, heads, receipt):
    started = time.monotonic()
    with rest.get_session() as session:
        adapter = session.get_adapter(BASE)
        require(isinstance(adapter, HTTPAdapter), 'PPI_ADAPTADOR_NO_VERIFICADO')
        adapter.max_retries = Retry(total=0, connect=0, read=0, redirect=0, status=0)
        receipt['attempted'] = True
        with session.request(method, BASE + uri, headers=heads, timeout=(3, 3),
                             allow_redirects=False, stream=True, verify=True) as response:
            receipt.update(http=response.status_code,
                           retry_after_s=ppi_retry_after_seconds(response.headers),
                           rate_limit=ppi_rate_limit_headers(response.headers))
            content = response.headers.get('Content-Type', '').split(';', 1)[0].strip().lower()
            receipt['content_type'] = content if content in ('application/json','application/problem+json','text/html','text/plain') else None
            body = bytearray()
            for chunk in response.iter_content(chunk_size=8192):
                body.extend(chunk)
                if len(body) > 1048576:
                    raise RuntimeError('PPI_RESPUESTA_EXCEDE_LIMITE')
            at = datetime.now(timezone.utc)
            receipt.update(received_at_utc=at.isoformat(), bytes=len(body),
                           latency_ms=round((time.monotonic() - started) * 1000))
            if response.status_code != 200:
                return None, at
            try:
                return json.loads(body), at
            except ValueError:
                return None, at


def direct_pair(rest, keypair, budget, out):
    heads = {'AuthorizedClient': rest.authorized_client, 'ClientKey': rest.client_key,
             'User-Agent': rest.USER_AGENT, 'x-tracking-id': rest.TRACKING_ID, 'Accept': 'application/json'}
    login = out['login'] = {'attempted': False}
    auth, _ = send(rest, 'POST', ACCOUNT_LOGIN_API,
                   {**heads, 'ApiKey': keypair[0], 'ApiSecret': keypair[1], 'Content-Type': 'application/json'}, login)
    if isinstance(auth, list) and len(auth) == 1:
        auth = auth[0]
    token = auth.get('accessToken') if isinstance(auth, dict) else None
    if login.get('http') != 200 or not isinstance(token, str) or not token or len(token) > 16000:
        return 'PPI_LOGIN_SIN_TOKEN_VALIDO'
    heads['Authorization'] = 'Bearer ' + token
    ticker, family, _, _, settlement = KEY
    for endpoint, template in (('Book', MARKETDATA_BOOK), ('Current', MARKETDATA_CURRENT)):
        evidence = out[endpoint] = {'attempted': False}
        lease = budget.acquire(endpoint.lower(), priority='DISCOVERY')['lease']
        with budget.wire_scope(lease):
            budget.start(lease)
            try:
                body, at = send(rest, 'GET', template.format(ticker, family, settlement), heads, evidence)
            finally:
                budget.finish(lease, status_code=evidence.get('http'), retry_after_seconds=evidence.get('retry_after_s'))
        if evidence.get('http') != 200:
            return 'PPI_RESPUESTA_HTTP_DISTINTA_DE_200'
        evidence['data'] = describe(body, at, endpoint)
    return 'PPI_DOS_RESPUESTAS_HTTP_200'


def observe_restart(parent, path, old, interlock):
    deadline = time.monotonic() + 52
    while time.monotonic() < deadline:
        try:
            children = Path(f'/proc/{parent}/task/{parent}/children').read_text().split()
            for pid in map(int, children):
                if pid != old and process_argv(pid)[-2:] == WORKER:
                    try:
                        current = owner_status(path)
                        if current.get('owner_pid') == pid and current.get('session_recovery_pending') is False:
                            return {'state': 'PPI_AUTH_RESTORED'}
                    except Exception:
                        pass
                    remaining = login_wait(interlock)
                    if remaining > 0:
                        return {'state': 'PPI_PROCESS_RESTARTED_AUTH_COOLDOWN', 'interlock_remaining_s': remaining}
        except (OSError, RuntimeError, ValueError):
            pass
        time.sleep(1)
    return {'state': 'PPI_RESTART_NOT_VERIFIED'}


def alarm_deadline(_signal, _frame):
    raise RuntimeError('PPI_SONDA_PLAZO_EXCEDIDO')


def probe(out):
    out.update(key=list(KEY), at_art=datetime.now(ART).isoformat(), real_order_routes_called=0)
    db = database_path()
    out['before'] = paper_state(db)
    require(out['before']['open_paper'] == 0, 'RC6_POSICIONES_PAPER_ABIERTAS')
    out['market_phase'] = _market_phase()
    require(out['market_phase'] in {'OPEN', 'CLOSED'}, 'RC6_RUEDA_EN_TRANSICION')
    require(version('ppi-client') == '1.3.0', 'PPI_SDK_DISTINTO_AL_COMPARADO')
    sdk = PPIClient(PPI.version, False).get_rest_client()
    credentials = _secret()
    path = gateway_socket_path(db)
    budget = PaperDirectReadBudget(db)
    interlock = ScannerLoginInterlock(db, AUTH_COOLDOWN_SECONDS)
    local_remaining = budget_wait(budget)
    out['before'].update(local_get_wait_s=local_remaining, get_wait_s=local_remaining,
                         login_wait_s=login_wait(interlock))
    require(out['before']['get_wait_s'] == 0 and out['before']['login_wait_s'] == 0,
            'PPI_PAUSA_ACTIVA')
    try:
        os.lstat(path)
    except FileNotFoundError:
        require(out['market_phase'] == 'CLOSED', 'PPI_GATEWAY_SOCKET_AUSENTE_EN_RUEDA')
        out['lease_path'] = 'ABSENT_SOCKET_EXCLUSIVE_DIAGNOSTIC'
        with ppi_gateway_lifetime_lease(path):
            require(paper_state(db)['open_paper'] == 0, 'RC6_EXPOSICION_CAMBIO')
            require(budget_wait(budget) == 0 and login_wait(interlock) == 0, 'PPI_PAUSA_ACTIVA')
            require(interlock.reserve() == 'ALLOWED', 'PPI_INTERLOCK_LOGIN_BLOQUEADO')
            signal.signal(signal.SIGALRM, alarm_deadline)
            signal.setitimer(signal.ITIMER_REAL, 23)
            try:
                out['state'] = direct_pair(sdk, credentials, budget, out)
            finally:
                signal.setitimer(signal.ITIMER_REAL, 0)
                out['after'] = paper_state(db)
        return
    out['lease_path'] = 'SUPERVISED_GATEWAY_HANDOVER'
    remote = owner_status(path)
    remaining = remote.get('native_backoff_remaining_seconds')
    require(type(remaining) is int and remaining >= 0, 'PPI_PAUSA_NO_VERIFICADA')
    local_remaining = budget_wait(budget)
    out['before'].update(gateway_get_wait_s=remaining, local_get_wait_s=local_remaining,
                         get_wait_s=max(remaining, local_remaining))
    out['before']['login_wait_s'] = login_wait(interlock)
    require(remote.get('session_recovery_pending') is False and
            out['before']['get_wait_s'] == 0 and out['before']['login_wait_s'] == 0, 'PPI_PAUSA_ACTIVA')
    old = remote['owner_pid']
    terminated = False
    pidfd = os.pidfd_open(old)
    try:
        require(process_argv(old)[-2:] == WORKER, 'RC6_PID_GATEWAY_NO_VERIFICADO')
        source = Path(f'/proc/{old}/status').read_text()
        match = re.search(r'^PPid:\s+(\d+)$', source, re.MULTILINE)
        require(match is not None, 'RC6_SUPERVISOR_NO_VERIFICADO')
        parent = int(match.group(1))
        require(process_argv(parent)[-1:] == (b'bv_paper_runtime.py',), 'RC6_SUPERVISOR_NO_VERIFICADO')
        current = owner_status(path)
        require((current['owner_pid'], current['session_generation']) ==
                (old, remote['session_generation']), 'PPI_GATEWAY_CAMBIO_ANTES_DE_ENTREGA')
        require(current.get('native_backoff_remaining_seconds') == 0 and
                current.get('session_recovery_pending') is False, 'PPI_PAUSA_ACTIVA')
        poll = select.poll()
        poll.register(pidfd, select.POLLIN)
        signal.pidfd_send_signal(pidfd, signal.SIGTERM)
        terminated = True
        out['gateway_sigterm'] = True
        require(bool(poll.poll(7500)), 'RC6_GATEWAY_NO_CERRO_EN_VENTANA')
        with ppi_gateway_lifetime_lease(path):
            require(paper_state(db)['open_paper'] == 0, 'RC6_EXPOSICION_CAMBIO')
            require(budget_wait(budget) == 0, 'PPI_PAUSA_ACTIVA_TRAS_CIERRE')
            require(interlock.reserve() == 'ALLOWED', 'PPI_INTERLOCK_LOGIN_BLOQUEADO')
            signal.signal(signal.SIGALRM, alarm_deadline)
            signal.setitimer(signal.ITIMER_REAL, 23)
            try:
                out['state'] = direct_pair(sdk, credentials, budget, out)
            finally:
                signal.setitimer(signal.ITIMER_REAL, 0)
    finally:
        os.close(pidfd)
        if terminated:
            out['recovery'] = observe_restart(parent, path, old, interlock)
            try:
                out['after'] = paper_state(db)
            except Exception:
                out['after'] = {'state': 'RC6_CONTADOR_FINAL_NO_VERIFICADO'}


if __name__ == '__main__':
    out = {'state': 'RC6_SONDA_INCOMPLETA', 'test': 'PPI_REST_DIRECT_GGAL', 'gateway_sigterm': False}
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        try:
            probe(out)
        except Exception as error:
            out['state'] = reason(error)
    print(json.dumps(out, ensure_ascii=False, separators=(',', ':')))
'''


def host_lock_holders(meta):
    """Read kernel lock metadata without printing any process arguments."""
    identity = (os.major(meta.st_dev), os.minor(meta.st_dev), meta.st_ino)
    holders = []
    seen = set()
    try:
        with open('/proc/locks', encoding='ascii') as lock_table:
            for line in lock_table:
                fields = line.split()
                if len(fields) != 8 or fields[1:3] != ['FLOCK', 'ADVISORY']:
                    continue
                _, _, _, mode, pid_text, device_inode, _, _ = fields
                parts = device_inode.split(':')
                if mode not in ('READ', 'WRITE') or len(parts) != 3:
                    continue
                try:
                    if (int(parts[0], 16), int(parts[1], 16), int(parts[2])) != identity:
                        continue
                    pid = int(pid_text)
                except ValueError:
                    continue
                key = (pid, mode)
                if pid <= 0 or key in seen:
                    continue
                seen.add(key)
                result = {'pid': pid, 'mode': 'SHARED' if mode == 'READ' else 'EXCLUSIVE'}
                try:
                    with open(f'/proc/{pid}/cgroup', encoding='utf-8') as cgroup:
                        units = cgroup.read(4096).replace('\n', '/').split('/')
                    unit = next((part for part in reversed(units)
                                 if re.fullmatch(r'porota-[a-z0-9-]{1,95}\.service', part)), None)
                    if unit is not None:
                        result['unit'] = unit
                except (OSError, UnicodeError):
                    pass
                holders.append(result)
                if len(holders) == 8:
                    break
    except (OSError, UnicodeError):
        return None
    return holders


def wait_for_host_lock(fd, meta, *, wait_s=HOST_LOCK_WAIT_S):
    started = time.monotonic()
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True, {'waited_s': 0, 'initial_holders': None}
    except BlockingIOError:
        initial_holders = host_lock_holders(meta)
    # Pass the original open file description. The parent retains the lock
    # after flock exits, so there is no unlock/reacquire gap before Docker.
    try:
        waiter = subprocess.run(
            ['flock', '--exclusive', '--timeout', str(wait_s), '--conflict-exit-code', '75', str(fd)],
            pass_fds=(fd,), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=wait_s + 2, check=False,
        )
        status = waiter.returncode
    except subprocess.TimeoutExpired:
        status = 75
    evidence = {'waited_s': round(time.monotonic() - started, 1), 'initial_holders': initial_holders,
                'wait_kind': 'BLOCKING_FLOCK'}
    if status == 0:
        return True, evidence
    if status != 75:
        raise RuntimeError('RC6_FLOCK_ESPERA_FALLIDA')
    evidence['last_holders'] = host_lock_holders(meta)
    return False, evidence


def lock_matches_path(path, fd, uid):
    opened = os.fstat(fd)
    named = path.lstat()
    return (stat.S_ISREG(named.st_mode) and named.st_uid == uid and not named.st_mode & 0o077
            and opened.st_nlink == named.st_nlink == 1
            and (opened.st_dev, opened.st_ino) == (named.st_dev, named.st_ino))


def host_probe(progress, lock_wait_s=HOST_LOCK_WAIT_S):
    uid = os.getuid()
    account = pwd.getpwuid(uid)
    if uid != 1000 or account.pw_name != 'porotaadmin':
        return {'state': 'RC6_USAR_TERMINAL_POROTAADMIN'}
    state = Path(account.pw_dir) / '.local/state/porota-rc6'
    lock_path = state / 'deploy.lock'
    progress['stage'] = 'DEPLOY_LOCK_OPEN'
    fd = os.open(lock_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        progress['stage'] = 'DEPLOY_LOCK_VALIDATE'
        meta = os.fstat(fd)
        if not stat.S_ISREG(meta.st_mode) or meta.st_uid != uid or meta.st_mode & 0o077 or meta.st_nlink != 1:
            return {'state': 'RC6_LOCK_HOST_NO_CONFIABLE'}
        progress['stage'] = 'DEPLOY_LOCK_WAIT'
        acquired, lock_evidence = wait_for_host_lock(fd, meta, wait_s=lock_wait_s)
        if not acquired:
            return {'state': 'RC6_HOST_OCUPADO', 'host_lock': lock_evidence}
        progress['stage'] = 'DEPLOY_LOCK_RECHECK'
        if not lock_matches_path(lock_path, fd, uid):
            return {'state': 'RC6_LOCK_HOST_CAMBIO', 'host_lock': lock_evidence}
        progress['stage'] = 'ROOTLESS_DOCKER_SOCKET'
        # Same endpoint as Identity.socket in scripts/porota_rootless_host.py.
        sock = Path(f'/run/user/{uid}/porota-rc6-docker.sock')
        socket_meta = sock.lstat()
        if not stat.S_ISSOCK(socket_meta.st_mode) or socket_meta.st_uid != uid:
            return {'state': 'RC6_DOCKER_ROOTLESS_NO_VERIFICADO'}
        docker = ['docker', '-H', 'unix://' + str(sock)]

        def run(*args, code=None, timeout=12):
            return subprocess.run(docker + list(args), input=code, text=True,
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                  timeout=timeout, check=True).stdout

        progress['stage'] = 'ROOTLESS_DOCKER_INFO'
        if 'rootless' not in run('info', '--format', '{{json .SecurityOptions}}'):
            return {'state': 'RC6_DOCKER_ROOTLESS_NO_VERIFICADO'}
        progress['stage'] = 'OBSERVER_LOOKUP'
        cid = run('ps','--no-trunc','-q','--filter','label=io.porota.project=rc6',
                  '--filter','label=io.porota.role=observer').strip()
        if not re.fullmatch(r'[a-f0-9]{64}', cid):
            return {'state': 'RC6_OBSERVER_NO_UNICO'}
        progress['stage'] = 'OBSERVER_RELEASE'
        if run('inspect','--format','{{.Image}}|{{.State.Running}}',cid).strip() != IMAGE + '|true':
            return {'state': 'RC6_RELEASE_DISTINTO_DEL_VERIFICADO'}
        progress['stage'] = 'OBSERVER_EXEC'
        result = json.loads(run('exec','-i',cid,'python','-B','-',code=PROBE,timeout=112))
        if type(result) is not dict or result.get('test') != 'PPI_REST_DIRECT_GGAL':
            return {'state': 'RC6_SALIDA_DEL_PROBE_NO_VERIFICADA'}
        result['deployment_sha'] = PRODUCT
        result['host_lock'] = lock_evidence
        return result
    finally:
        os.close(fd)


def run_host_attempt(lock_wait_s=HOST_LOCK_WAIT_S):
    progress = {'stage': 'HOST_IDENTITY'}
    try:
        return host_probe(progress, lock_wait_s)
    except Exception as error:
        return {'state': 'RC6_PROBE_HOST_INCOMPLETO', 'error_class': type(error).__name__, **progress}


def run_with_cooldown_wait():
    deadline = time.monotonic() + COOLDOWN_WAIT_LIMIT_S
    report = run_host_attempt()
    checks = 1
    while report.get('state') == 'PPI_PAUSA_ACTIVA' and report.get('gateway_sigterm') is False:
        before = report.get('before')
        if not isinstance(before, dict):
            break
        delays = (before.get('get_wait_s'), before.get('login_wait_s'))
        if any(type(delay) is not int or delay < 0 for delay in delays) or max(delays) == 0:
            break
        delay = max(delays)
        if time.monotonic() + delay >= deadline:
            report['cooldown_wait_limit_reached'] = True
            break
        # The previous attempt already closed its host-lock descriptor.
        print(f'Pausa activa. Próxima comprobación en {delay} s.', file=sys.stderr, flush=True)
        time.sleep(delay)
        remaining_s = int(deadline - time.monotonic())
        if remaining_s <= 0:
            report['cooldown_wait_limit_reached'] = True
            break
        report = run_host_attempt(min(HOST_LOCK_WAIT_S, remaining_s))
        checks += 1
    report['preflight_checks'] = checks
    return report


if sys.argv[1:] == ['--wait-cooldown']:
    report = run_with_cooldown_wait()
elif not sys.argv[1:]:
    report = run_host_attempt()
else:
    report = {'state': 'RC6_OPCION_PROBE_INVALIDA'}
print(json.dumps(report, ensure_ascii=False, separators=(',', ':')))
HOST_PY
) || report='{"state":"RC6_PROBE_SIN_RESULTADO"}'

printf '%s\n' "$report"
if [ -t 1 ]; then
  printf '\033]52;c;%s\007' "$(printf '%s' "$report" | base64 -w0)"
fi
