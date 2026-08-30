"""Preflight contable: origen sólo lectura; copia temporal sin APIs ni motores."""
import argparse
import base64
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import sqlite3
import stat
import subprocess
import sys
import tempfile
from urllib.parse import quote

VERSION = 'v17-ledger-preflight-3'
HOST_DATA = '/opt/porota-trading/data'
DATABASE = '/observer/observer_production.db'
BOT = 'porota_trading_bot'
OBSERVER = 'porota_production_observer'
CONTAINER = 'porota_v17_ledger_preflight'
# Este diagnóstico compara una instalación histórica detenida; no identifica
# la imagen candidata del hotfix ni puede promoverla.
EXPECTED_LEGACY_OBSERVER_IMAGE = 'porota-trading-bot:16.3.5'
MAX_POSITIONS = 10000
MAX_FILLS = 100000
MAX_EXAMPLES = 10
MAX_COPY_BYTES = 16 * 1024 * 1024
COPY_CHUNK_BYTES = 1024 * 1024
TABLES = ('paper_positions', 'paper_fills', 'paper_spot_sales', 'paper_sale_receivables')


class PreflightStop(Exception):
    pass


def report_base():
    return {'diagnostic': VERSION, 'generated_at': datetime.now(timezone.utc).isoformat(),
        'status': 'NOT_STARTED', 'scope': 'Registros PAPER persistidos; sin APIs, cuentas o credenciales.',
        'database_modified': False, 'migration_performed': False, 'promotion_allowed': False,
        'counts': {}, 'issues': {}, 'examples': [], 'legacy_projection': {},
        'stage': 'NOT_STARTED',
        'read_mode': 'DIRECT',
        'reader_runtime': {'python': '.'.join(map(str, sys.version_info[:3])),
                           'sqlite': sqlite3.sqlite_version},
        'limits': ['No certifica movimientos, saldos, permisos o costos reales de PPI.',
                   'Sin exportar importes, posiciones individuales o contenido de features_json.',
                   'No migra ni repara registros. Los defaults legacy son hipótesis explícitas.',
                   'Contrasta salidas con entradas almacenadas; no certifica origen de entradas ni aranceles.',
                   'Sólo ledger spot: no certifica señales, sesiones, cauciones o producción.']}


def filesystem_evidence(path):
    """Sólo archivos conocidos; no lista directorios ni lee filas o payloads.

    Fotografía previa, no atómica con la transacción SQLite. El encabezado
    describe el modo persistido, no la integridad ni recuperación del WAL.
    """
    result = {}
    for label, item in (('database', path), ('wal', Path(str(path)+'-wal')),
                        ('shm', Path(str(path)+'-shm')),
                        ('rollback_journal', Path(str(path)+'-journal'))):
        row = {}
        try:
            info = item.stat()
            row.update(state='PRESENT', regular_file=stat.S_ISREG(info.st_mode),
                       bytes=info.st_size)
            if row['regular_file']:
                # Apertura de sólo lectura: no intenta crear archivos auxiliares.
                with item.open('rb') as stream:
                    row['read_open_succeeded'] = True
                    if label == 'database':
                        header = stream.read(20)
                        row['header_journal_mode'] = (
                            {b'\x02\x02':'WAL', b'\x01\x01':'ROLLBACK'}.get(header[18:20], 'UNKNOWN')
                            if header[:16] == b'SQLite format 3\x00' else 'NOT_SQLITE_HEADER')
        except FileNotFoundError:
            row.update(state='MISSING')
        except OSError as exc:
            row.update(state='ACCESS_ERROR', os_errno=exc.errno,
                       error_class=type(exc).__name__)
        result[label] = row
    return result


def sqlite_failure(exc):
    """Códigos nativos, nunca el mensaje SQL que podría contener datos."""
    code = getattr(exc, 'sqlite_errorcode', None)
    name = getattr(exc, 'sqlite_errorname', None)
    code = code if type(code) is int and 0 <= code < 65536 else None
    name = (name if isinstance(name, str) and
            re.fullmatch(r'SQLITE_[A-Z0-9_]{1,80}', name) else None)
    primary = code & 255 if code is not None else None
    category = {1:'SQL_ERROR', 3:'PERMISSION', 5:'BUSY', 6:'LOCKED',
                8:'READONLY', 10:'IO_ERROR', 11:'CORRUPT', 14:'CANNOT_OPEN',
                17:'SCHEMA', 23:'AUTHORIZATION', 26:'NOT_A_DATABASE'}.get(primary, 'OTHER')
    return {'error_class':type(exc).__name__, 'sqlite_errorcode':code,
            'sqlite_errorname':name, 'primary_code':primary, 'category':category}


def issue_code(exc):
    message = str(exc)
    if message.startswith('Costo de entrada parcial'):
        return 'PARTIAL_ENTRY_COST_ALLOCATION'
    if message.startswith('Precio agregado de cierre'):
        return 'AGGREGATE_EXIT_PRICE'
    if message.startswith('Venta parcial sin fill compatible'):
        return 'PARTIAL_FILL_IDENTITY_OR_SOURCE'
    if message.startswith(('Liquidación incompatible', 'Recibo pendiente', 'Fuente de liquidación')):
        return 'SALE_SETTLEMENT_TERMS'
    return 'SPOT_LEDGER_INCONSISTENT'


def collect(path):
    report = report_base()
    path = Path(path)
    issues = Counter()
    try:
        report['stage'] = 'FILE_METADATA'
        report['filesystem_evidence'] = filesystem_evidence(path)
        if not path.is_file():
            raise PreflightStop('DATABASE_NOT_FOUND')
        report['stage'] = 'IMPORT_READERS'
        # Sólo módulos de cálculo; no PaperStore, que inicializa/migra tablas.
        if Path(__file__).is_file():
            sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        import cd_spot_ledger as ledger
        from bs_instrument_contracts import decimal_value
        from cf_sale_settlement import validated_sale_settlement
        from decimal import Decimal
        report['stage'] = 'OPEN_SQLITE'
        uri = 'file:' + quote(str(path.resolve()), safe='/') + '?mode=ro'
        with closing(sqlite3.connect(uri, uri=True, timeout=5)) as c:
            c.row_factory = sqlite3.Row
            report['stage'] = 'SET_QUERY_ONLY'
            c.execute('PRAGMA query_only=ON')
            report['stage'] = 'BEGIN_READ_TRANSACTION'
            c.execute('BEGIN')
            report['stage'] = 'READ_SCHEMA'
            schemas = {r['name']: r for r in c.execute(
                "SELECT name,type,sql FROM sqlite_master WHERE name IN (?,?,?,?)", TABLES)}
            if any(r['type'] != 'table' or 'CREATE VIRTUAL TABLE' in (r['sql'] or '').upper()
                   for r in schemas.values()):
                raise PreflightStop('UNEXPECTED_TABLE_KIND')
            if not {'paper_positions', 'paper_fills'} <= set(schemas):
                raise PreflightStop('PAPER_TABLES_MISSING')
            report['stage'] = 'READ_COLUMNS'
            columns = {name: {r['name'] for r in c.execute('PRAGMA table_info(' + name + ')')}
                       for name in schemas}
            required = {'paper_positions': {'paper_id','source','status','symbol','asset_class',
                'settlement','quantity','entry_price','entry_cost','opened_at','closed_at',
                'exit_price','exit_cost','gross_pnl','net_pnl','close_reason','features_json'},
                'paper_fills': {'id','paper_id','source','side','quantity','price','costs','filled_at'},
                'paper_spot_sales': {'fill_id','paper_id','entry_cost','gross_pnl','net_pnl',
                    'net_proceeds','available_at','basis','reason'},
                'paper_sale_receivables': {'paper_id','currency','net_proceeds','available_at','basis'}}
            if any(not required[name] <= columns[name] for name in schemas):
                raise PreflightStop('UNSUPPORTED_LEDGER_SCHEMA')
            report['stage'] = 'COUNT_ROWS'
            for name, limit in (('paper_positions', MAX_POSITIONS), ('paper_fills', MAX_FILLS),
                                ('paper_spot_sales', MAX_FILLS), ('paper_sale_receivables', MAX_POSITIONS)):
                if name not in schemas:
                    report['counts'][name] = None
                    continue
                count = c.execute('SELECT COUNT(*) FROM (SELECT 1 FROM ' + name + ' LIMIT ?)', (limit+1,)).fetchone()[0]
                if count > limit:
                    raise PreflightStop('ROW_LIMIT_REQUIRES_PLANNED_AUDIT')
                report['counts'][name] = count
            defaults = {key: value for key, value in {'currency':'ARS', 'market':'BYMA',
                'currency_source':'LEGACY_ASSUMED_ARS'}.items() if key not in columns['paper_positions']}
            report['legacy_projection'] = defaults
            states = Counter()
            checked = valid = 0
            missing_receipts = 0
            report['stage'] = 'VALIDATE_POSITIONS'
            for ordinal, row in enumerate(c.execute('SELECT * FROM paper_positions ORDER BY opened_at,paper_id'), 1):
                p = dict(row, **defaults)
                states[p['status'] if p['status'] in {'OPEN','CLOSED'} else 'UNKNOWN'] += 1
                checked += 1
                try:
                    ledger.partition(c, p)
                    sales = ledger.sales(c, p['paper_id'])
                    if sales:
                        if ('paper_sale_receivables' in schemas and c.execute(
                                'SELECT 1 FROM paper_sale_receivables WHERE paper_id=?',
                                (p['paper_id'],)).fetchone()):
                            raise ValueError('Recibo simple incompatible con ventas parciales')
                        for sale in sales:
                            validated_sale_settlement(p['settlement'], sale['filled_at'],
                                                      sale['available_at'], sale['basis'])
                    elif p['status'] == 'CLOSED':
                        receipt = (c.execute('SELECT * FROM paper_sale_receivables WHERE paper_id=?',
                                             (p['paper_id'],)).fetchone()
                                   if 'paper_sale_receivables' in schemas else None)
                        if receipt is None:
                            missing_receipts += 1
                        else:
                            _, _, _, factor = ledger.entry_terms(p)
                            proceeds = Decimal(p['exit_price'])*Decimal(p['quantity'])*factor-Decimal(p['exit_cost'])
                            if (receipt['currency'] != p['currency'] or
                                    decimal_value(receipt['net_proceeds'], 'producido') != proceeds):
                                raise ValueError('Importes del recibo no concilian')
                            validated_sale_settlement(p['settlement'], p['closed_at'], receipt['available_at'], receipt['basis'])
                    valid += 1
                except (ValueError, TypeError, ArithmeticError, KeyError) as exc:
                    code = issue_code(exc)
                    issues[code] += 1
                    if len(report['examples']) < MAX_EXAMPLES:
                        report['examples'].append({'row_number': ordinal, 'issue': code})
            report['stage'] = 'CHECK_ORPHANS'
            orphan_fills = c.execute('''SELECT COUNT(*) FROM paper_fills f LEFT JOIN paper_positions p
                ON p.paper_id=f.paper_id WHERE p.paper_id IS NULL''').fetchone()[0]
            if orphan_fills:
                issues['ORPHAN_FILLS'] += orphan_fills
            if 'paper_spot_sales' in schemas:
                orphans = c.execute('''SELECT COUNT(*) FROM paper_spot_sales s
                    LEFT JOIN paper_positions p ON p.paper_id=s.paper_id
                    LEFT JOIN paper_fills f ON f.id=s.fill_id
                    WHERE p.paper_id IS NULL OR f.id IS NULL''').fetchone()[0]
                if orphans:
                    issues['ORPHAN_PARTIAL_SALES'] += orphans
            if 'paper_sale_receivables' in schemas:
                orphans = c.execute('''SELECT COUNT(*) FROM paper_sale_receivables r
                    LEFT JOIN paper_positions p ON p.paper_id=r.paper_id
                    WHERE p.paper_id IS NULL OR p.status IS NULL OR p.status<>'CLOSED' ''').fetchone()[0]
                if orphans:
                    issues['UNMATCHED_SALE_RECEIPTS'] += orphans
            report['counts'].update(positions_checked=checked, arithmetically_consistent=valid,
                positions_inconsistent=checked-valid, missing_closed_sale_receipts=missing_receipts,
                position_states=dict(states))
            report['issues'] = dict(issues)
            report['status'] = 'OBSERVED_REVIEW_REQUIRED' if issues or defaults or missing_receipts else 'OBSERVED_NO_DETECTED_INCONSISTENCIES'
            report['stage'] = 'END_READ_TRANSACTION'
            c.rollback()
            report['stage'] = 'DONE'
    except PreflightStop as exc:
        report.update(status='STOPPED', reason=str(exc))
    except sqlite3.Error as exc:
        report.update(status='STOPPED', reason='SQLITE_ERROR', sqlite_error=sqlite_failure(exc))
    except Exception as exc:
        report.update(status='STOPPED', reason=type(exc).__name__)
    report['completed_at'] = datetime.now(timezone.utc).isoformat()
    return report


def _no_source_sidecars(path):
    for suffix in ('-wal', '-shm', '-journal'):
        try:
            Path(str(path)+suffix).lstat()
        except FileNotFoundError:
            continue
        # Incluso un archivo vacío o enlace requiere otra evaluación.
        raise PreflightStop('SOURCE_AUXILIARY_PRESENT')


def _signature(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _source_signature(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise PreflightStop('SOURCE_NOT_REGULAR_FILE')
    if not 0 < info.st_size <= MAX_COPY_BYTES:
        raise PreflightStop('SOURCE_SIZE_OUTSIDE_COPY_LIMIT')
    return _signature(info)


def _stream_source(path, expected, destination=None):
    """Una pasada acotada del origen; nunca lo abre con SQLite ni para escribir."""
    _no_source_sidecars(path)
    if _source_signature(path) != expected:
        raise PreflightStop('SOURCE_CHANGED')
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
    total = 0
    digest = hashlib.sha256()
    with os.fdopen(os.open(path, flags), 'rb') as source:
        if _signature(os.fstat(source.fileno())) != expected:
            raise PreflightStop('SOURCE_CHANGED')
        while True:
            chunk = source.read(min(COPY_CHUNK_BYTES, expected[2]-total+1))
            if not chunk:
                break
            total += len(chunk)
            if total > expected[2]:
                raise PreflightStop('SOURCE_CHANGED')
            digest.update(chunk)
            if destination is not None:
                destination.write(chunk)
        if total != expected[2] or _signature(os.fstat(source.fileno())) != expected:
            raise PreflightStop('SOURCE_CHANGED')
    if _source_signature(path) != expected:
        raise PreflightStop('SOURCE_CHANGED')
    _no_source_sidecars(path)
    return digest.digest()


def _copy_digest(path):
    digest = hashlib.sha256()
    total = 0
    with path.open('rb') as source:
        while True:
            chunk = source.read(min(COPY_CHUNK_BYTES, MAX_COPY_BYTES-total+1))
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_COPY_BYTES:
                raise PreflightStop('COPY_SIZE_OUTSIDE_LIMIT')
            digest.update(chunk)
    return digest.digest()


def collect_copy(path, scratch_root='/tmp'):
    """Copia temporal autorizada: exige origen sin auxiliares y sin cambios detectados.

    No es backup online ni bloqueo de escritores. Aborta ante cambios observados;
    no recupera transacciones de WAL eliminados previamente. Sólo exporta el
    informe agregado. El lanzador monta scratch en tmpfs dentro del servidor.
    """
    report = report_base()
    report.update(read_mode='TEMPORARY_COPY',
                  scope='Auditoría PAPER sobre copia temporal local; origen sólo lectura.',
                  temporary_copy_created=False, temporary_copy_removed=None)
    report['limits'] = report['limits'] + [
        'Copia del archivo completo, sin exportarlo; sólo se consultan tablas PAPER.',
        'Comprobaciones de estabilidad por muestras; no bloqueo exclusivo de escritores.',
        'No recupera WAL previamente eliminados ni certifica integridad histórica.']
    evidence = {'bytes':0, 'copy_hash_matches':False, 'source_hash_rechecks':0}
    report['copy_evidence'] = evidence
    path = Path(path).absolute()
    temporary = None
    try:
        report['stage'] = 'SOURCE_PREFLIGHT'
        _no_source_sidecars(path)
        expected = _source_signature(path)
        report['source_filesystem_evidence'] = filesystem_evidence(path)
        header = report['source_filesystem_evidence']['database'].get('header_journal_mode')
        if header not in {'WAL','ROLLBACK'}:
            raise PreflightStop('SOURCE_HEADER_UNSUPPORTED')
        root = Path(scratch_root).resolve(strict=True)
        source_parent = path.parent.resolve(strict=True)
        if root == source_parent or root.is_relative_to(source_parent):
            raise PreflightStop('TEMPORARY_LOCATION_OVERLAPS_SOURCE')
        report['stage'] = 'CREATE_TEMPORARY_COPY'
        temporary = tempfile.TemporaryDirectory(prefix='porota-ledger-copy-', dir=root)
        copy_path = Path(temporary.name)/'observer_copy.db'
        with copy_path.open('xb') as destination:
            report['temporary_copy_created'] = True
            report['stage'] = 'COPY_SOURCE_BYTES'
            original_hash = _stream_source(path, expected, destination)
        evidence['bytes'] = expected[2]
        report['stage'] = 'VERIFY_COPY_BYTES'
        if _copy_digest(copy_path) != original_hash:
            raise PreflightStop('COPY_HASH_MISMATCH')
        evidence['copy_hash_matches'] = True
        report['stage'] = 'VERIFY_SOURCE_BEFORE_AUDIT'
        if _stream_source(path, expected) != original_hash:
            raise PreflightStop('SOURCE_HASH_CHANGED')
        evidence['source_hash_rechecks'] = 1
        report['stage'] = 'AUDIT_TEMPORARY_COPY'
        audit = collect(copy_path)  # SQLite sólo sobre la copia, mode=ro.
        report['stage'] = 'VERIFY_SOURCE_AFTER_AUDIT'
        if _stream_source(path, expected) != original_hash:
            raise PreflightStop('SOURCE_HASH_CHANGED')
        evidence['source_hash_rechecks'] = 2
        # Publicar resultados sólo después de verificar nuevamente el origen.
        for key in ('status','stage','counts','issues','examples','legacy_projection',
                    'reason','sqlite_error'):
            if key in audit:
                report[key] = audit[key]
        report['copy_filesystem_evidence'] = audit.get('filesystem_evidence', {})
    except PreflightStop as exc:
        report.update(status='STOPPED', reason=str(exc))
    except Exception as exc:
        report.update(status='STOPPED', reason=type(exc).__name__)
        if isinstance(exc, OSError):
            report['os_errno'] = exc.errno
    finally:
        if temporary is not None:
            try:
                temporary.cleanup()
                report['temporary_copy_removed'] = True
            except Exception:
                report.update(status='STOPPED', reason='TEMPORARY_COPY_CLEANUP_FAILED',
                              temporary_copy_removed=False)
        report['completed_at'] = datetime.now(timezone.utc).isoformat()
    return report


def read_report(copy_mode=False):
    previous = signal.getsignal(signal.SIGALRM)
    def expired(*args):
        raise PreflightStop('TIME_LIMIT_120_SECONDS')
    signal.signal(signal.SIGALRM, expired)
    signal.alarm(120)
    try:
        return collect_copy(DATABASE) if copy_mode else collect(DATABASE)
    except PreflightStop as exc:
        return dict(report_base(), status='STOPPED', reason=str(exc),
                    read_mode='TEMPORARY_COPY' if copy_mode else 'DIRECT')
    except Exception as exc:
        return dict(report_base(), status='STOPPED', reason=type(exc).__name__,
                    read_mode='TEMPORARY_COPY' if copy_mode else 'DIRECT')
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def docker(args, timeout=15):
    result = subprocess.run(['sudo','-n','docker',*args], capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise PreflightStop('DOCKER_COMMAND_FAILED_' + args[0].upper())
    return result.stdout


def host_report(archive):
    report = report_base()
    report['read_mode'] = 'TEMPORARY_COPY'
    created = None
    package_dir = None
    try:
        archive = Path(archive).resolve(strict=True)
        if not archive.is_file() or archive.suffix != '.zip':
            raise PreflightStop('USE_THE_SFTP_ZIP')
        template = '{"name":{{json .Name}},"running":{{json .State.Running}},"restart":{{json .HostConfig.RestartPolicy.Name}},"image":{{json .Image}},"tag":{{json .Config.Image}},"user":{{json .Config.User}}}'
        states = [json.loads(s) for s in docker(['inspect','--type','container','--format',template,BOT,OBSERVER]).splitlines()]
        if len(states) != 2 or {s['name'] for s in states} != {'/'+BOT,'/'+OBSERVER}:
            raise PreflightStop('UNEXPECTED_CONTAINERS')
        if any(s['running'] is not False or s['restart'] != 'no' for s in states):
            raise PreflightStop('ENGINES_MUST_REMAIN_STOPPED_NO_RESTART')
        observer = next(s for s in states if s['name'] == '/'+OBSERVER)
        if (observer['tag'] != EXPECTED_LEGACY_OBSERVER_IMAGE or observer['user'] != 'botuser'
                or not re.fullmatch(r'sha256:[0-9a-f]{64}', observer['image'])):
            raise PreflightStop('OBSERVER_INSTALLATION_CHANGED')
        mounts = json.loads(docker(['inspect','--type','container','--format','{{json .Mounts}}',OBSERVER]))
        data = [m for m in mounts if m.get('Destination') == '/app/data']
        if len(data) != 1 or data[0].get('Source') != HOST_DATA or data[0].get('Type') != 'bind':
            raise PreflightStop('OBSERVER_DATA_MOUNT_CHANGED')
        package_dir = tempfile.TemporaryDirectory(prefix='porota-ledger-preflight-')
        package = Path(package_dir.name)/'probe.zip'
        shutil.copyfile(archive, package)
        os.chmod(package, 0o444)
        args = ['create','--name',CONTAINER,'--pull','never','--network','none',
            '--read-only','--no-healthcheck','--user','botuser','--workdir','/tmp',
            '--cap-drop','ALL','--security-opt','no-new-privileges','--init',
            '--memory','256m','--cpus','1',
            '--tmpfs','/tmp:rw,nosuid,nodev,noexec,size=32m,mode=1777',
            '--env','PYTHONDONTWRITEBYTECODE=1','--env','PYTHONUNBUFFERED=1',
            '--mount',f'type=bind,src={package},dst=/run/porota_ledger.zip,readonly',
            '--mount',f'type=bind,src={HOST_DATA}/observer,dst=/observer,readonly',
            '--entrypoint','python',observer['image'],'/run/porota_ledger.zip','--copy-read']
        output = docker(args).strip()
        if not re.fullmatch(r'[0-9a-f]{64}', output):
            raise PreflightStop('UNEXPECTED_CREATE_RESULT_REVIEW_REQUIRED')
        created = output
        result = json.loads(docker(['start','--attach',created], timeout=150))
        if (not isinstance(result, dict) or result.get('diagnostic') != VERSION
                or result.get('read_mode') != 'TEMPORARY_COPY'):
            raise PreflightStop('INVALID_PROBE_OUTPUT')
        after = [json.loads(s) for s in docker(['inspect','--type','container','--format',template,BOT,OBSERVER]).splitlines()]
        if (len(after) != 2 or {s['name'] for s in after} != {'/'+BOT,'/'+OBSERVER}
                or any(s['running'] is not False or s['restart'] != 'no' for s in after)):
            raise PreflightStop('ENGINES_STATE_CHANGED_DURING_PROBE')
        report = result
        report['host_evidence'] = {'engines_stopped_before_probe': True,
            'engines_stopped_after_probe': True, 'copy_storage':'container_tmpfs',
            'observer_image': observer['image'], 'network': 'none',
            'observer_directory_readonly': True, 'credentials_or_env_mounted': False}
    except PreflightStop as exc:
        report.update(status='STOPPED', reason=str(exc))
    except Exception as exc:
        report.update(status='STOPPED', reason=type(exc).__name__)
    finally:
        if created is not None:
            try:
                docker(['rm','--force',created])
                report['temporary_container_removed'] = True
            except Exception:
                report.update(status='STOPPED', reason='TEMPORARY_CONTAINER_CLEANUP_FAILED', temporary_container_removed=False)
        if package_dir is not None:
            package_dir.cleanup()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--host', action='store_true')
    mode.add_argument('--read', action='store_true')
    mode.add_argument('--copy-read', action='store_true')
    args = parser.parse_args()
    report = host_report(sys.argv[0]) if args.host else read_report(copy_mode=args.copy_read)
    output = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
    print(output, flush=True)
    if args.host:
        print('\033]52;c;' + base64.b64encode(output.encode()).decode() + '\a', end='', flush=True)
    return 0


if __name__ == '__main__':
    main()
