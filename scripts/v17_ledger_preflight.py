"""Preflight contable de sólo lectura: no migra ni abre APIs o motores."""
import argparse
import base64
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
from urllib.parse import quote

VERSION = 'v17-ledger-preflight-1'
HOST_DATA = '/opt/porota-trading/data'
DATABASE = '/observer/observer_production.db'
BOT = 'porota_trading_bot'
OBSERVER = 'porota_production_observer'
CONTAINER = 'porota_v17_ledger_preflight'
MAX_POSITIONS = 10000
MAX_FILLS = 100000
MAX_EXAMPLES = 10
TABLES = ('paper_positions', 'paper_fills', 'paper_spot_sales', 'paper_sale_receivables')


class PreflightStop(Exception):
    pass


def report_base():
    return {'diagnostic': VERSION, 'generated_at': datetime.now(timezone.utc).isoformat(),
        'status': 'NOT_STARTED', 'scope': 'Registros PAPER persistidos; sin APIs, cuentas o credenciales.',
        'database_modified': False, 'migration_performed': False, 'promotion_allowed': False,
        'counts': {}, 'issues': {}, 'examples': [], 'legacy_projection': {},
        'limits': ['No certifica movimientos, saldos, permisos o costos reales de PPI.',
                   'Sin exportar importes, posiciones individuales o contenido de features_json.',
                   'No migra ni repara registros. Los defaults legacy son hipótesis explícitas.',
                   'Contrasta salidas con entradas almacenadas; no certifica origen de entradas ni aranceles.',
                   'Sólo ledger spot: no certifica señales, sesiones, cauciones o producción.']}


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
    if not path.is_file():
        report.update(status='STOPPED', reason='DATABASE_NOT_FOUND')
        return report
    # Sólo estos módulos de cálculo; no PaperStore, que inicializa/migra tablas.
    if Path(__file__).is_file():
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import cd_spot_ledger as ledger
    from bs_instrument_contracts import decimal_value
    from cf_sale_settlement import validated_sale_settlement
    from decimal import Decimal
    issues = Counter()
    try:
        uri = 'file:' + quote(str(path.resolve()), safe='/') + '?mode=ro'
        with closing(sqlite3.connect(uri, uri=True, timeout=5)) as c:
            c.row_factory = sqlite3.Row
            c.execute('PRAGMA query_only=ON')
            c.execute('BEGIN')
            schemas = {r['name']: r for r in c.execute(
                "SELECT name,type,sql FROM sqlite_master WHERE name IN (?,?,?,?)", TABLES)}
            if any(r['type'] != 'table' or 'CREATE VIRTUAL TABLE' in (r['sql'] or '').upper()
                   for r in schemas.values()):
                raise PreflightStop('UNEXPECTED_TABLE_KIND')
            if not {'paper_positions', 'paper_fills'} <= set(schemas):
                raise PreflightStop('PAPER_TABLES_MISSING')
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
            c.rollback()
    except PreflightStop as exc:
        report.update(status='STOPPED', reason=str(exc))
    except Exception as exc:
        report.update(status='STOPPED', reason=type(exc).__name__)
    report['completed_at'] = datetime.now(timezone.utc).isoformat()
    return report


def read_report():
    previous = signal.getsignal(signal.SIGALRM)
    def expired(*args):
        raise PreflightStop('TIME_LIMIT_120_SECONDS')
    signal.signal(signal.SIGALRM, expired)
    signal.alarm(120)
    try:
        return collect(DATABASE)
    except PreflightStop as exc:
        return dict(report_base(), status='STOPPED', reason=str(exc))
    except Exception as exc:
        return dict(report_base(), status='STOPPED', reason=type(exc).__name__)
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
        if (observer['tag'] != 'porota-trading-bot:16.3.5' or observer['user'] != 'botuser'
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
            '--tmpfs','/tmp:rw,nosuid,nodev,noexec,size=32m',
            '--env','PYTHONDONTWRITEBYTECODE=1','--env','PYTHONUNBUFFERED=1',
            '--mount',f'type=bind,src={package},dst=/run/porota_ledger.zip,readonly',
            '--mount',f'type=bind,src={HOST_DATA}/observer,dst=/observer,readonly',
            '--entrypoint','python',observer['image'],'/run/porota_ledger.zip','--read']
        output = docker(args).strip()
        if not re.fullmatch(r'[0-9a-f]{64}', output):
            raise PreflightStop('UNEXPECTED_CREATE_RESULT_REVIEW_REQUIRED')
        created = output
        result = json.loads(docker(['start','--attach',created], timeout=150))
        if not isinstance(result, dict) or result.get('diagnostic') != VERSION:
            raise PreflightStop('INVALID_PROBE_OUTPUT')
        report = result
        report['host_evidence'] = {'engines_stopped_before_probe': True,
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
    args = parser.parse_args()
    report = host_report(sys.argv[0]) if args.host else read_report()
    output = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
    print(output, flush=True)
    if args.host:
        print('\033]52;c;' + base64.b64encode(output.encode()).decode() + '\a', end='', flush=True)
    return 0


if __name__ == '__main__':
    main()
