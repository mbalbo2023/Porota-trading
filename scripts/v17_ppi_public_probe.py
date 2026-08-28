"""Consulta PPI puntual; --host la ejecuta aislada en la imagen del observador.

Sin cuentas, presupuestos, órdenes, .env, bases de datos ni arranque del motor.
Los filtros de descubrimiento y sus resultados no certifican contratos.
"""
import argparse
import base64
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import importlib.metadata
import json
import logging
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
from urllib.parse import quote, urlsplit

VERSION = 'v17-public-probe-1'
SECRET = '/run/secrets/ppi_production.json'
HOST_SECRET = '/opt/porota-trading/.secrets/ppi_production.json'
OBSERVER = 'porota_production_observer'
BOT = 'porota_trading_bot'
PROBE_NAME = 'porota_v17_ppi_probe'
IMAGE_TAG = 'porota-trading-bot:16.3.5'
CONFIG_NAMES = ('instrument_types','markets','settlements','quantity_types',
                'operation_terms','operation_types','operations')
QUERIES = (('CAUCION','CAUCIONES'),('PESOS','CAUCIONES'),
           ('DOLAR','CAUCIONES'),('ALUA','ACCIONES'))
PUBLIC_FIELDS = set(('ticker symbol description name type instrumenttype market currency '
    'currencycode settlement date price quantity position offers bids volume openingprice '
    'max min last bid ask rate annualrate tna rateunit rate_unit term termdays term_days days '
    'plazo maturitydate maturity expirationdate expiry expiration duedate daycountbasis '
    'day_count_basis operation operationtype contractmultiplier multiplier lotsize lot '
    'minquantity minimumquantity quantitystep pricefactor priceunit nominalvalue').split())


class ProbeStop(Exception):
    pass


class Discard:
    def write(self, value):
        return len(value)
    def flush(self):
        pass


def public_sample(value, depth=0):
    """Exportación acotada de campos públicos; no imprime cuerpos de error."""
    if depth > 4:
        return {'truncated':True}
    if isinstance(value,dict):
        result = {k:public_sample(v,depth+1) for k,v in value.items()
                  if isinstance(k,str) and k.lower() in PUBLIC_FIELDS}
        result['_omitted_fields'] = len(value)-len(result)
        return result
    if isinstance(value,list):
        return [public_sample(v,depth+1) for v in value[:5]]
    if isinstance(value,str):
        return value[:200]
    if value is None or isinstance(value,(bool,int)):
        return value
    if isinstance(value,float):
        import math
        return value if math.isfinite(value) else 'NON_FINITE'
    return 'UNSUPPORTED_VALUE_TYPE'


def report_base():
    return {'diagnostic':VERSION,'generated_at':datetime.now(timezone.utc).isoformat(),
        'status':'NOT_STARTED','scope':'Autenticación y datos públicos; sin cuentas ni órdenes.',
        'configuration':{},'searches':[],'quotes':[],'http':[],
        'caucion_readiness':'HOLD_UNVERIFIED_TERMS','data_certified':False,
        'promotion_allowed':False,
        'limits':['Filtros no equivalen a instrumentos; vacío no prueba indisponibilidad.',
                  'Muestras de hasta 5 filas/niveles; no es catálogo ni feed completo.',
                  'No interpreta price como TNA, quantity como capital ni lado como colocadora.',
                  'No verifica costos, saldo, acreditación, plazo ni sesión ejecutable.']}


def collect(reader, report):
    report['stage']='LOGIN'
    reader.login_once()
    report['stage']='CONFIGURATION'
    config=reader.market_configuration()
    if (not isinstance(config,dict) or set(config)!=set(CONFIG_NAMES)
            or any(not isinstance(v,list) or len(v)>100 or any(
                not isinstance(s,str) or not s.strip() or len(s)>100 for s in v)
                for v in config.values())):
        raise ProbeStop('INVALID_CONFIGURATION')
    report['configuration']=config
    candidates=[]
    seen=set()
    for query,family in QUERIES:
        item={'filter':query,'family':family,'market':'BYMA','status':'NOT_QUERIED'}
        report['searches'].append(item)
        if family not in config['instrument_types'] or 'BYMA' not in config['markets']:
            item['status']='NOT_ENUMERATED'
            continue
        report['stage']='SEARCH_'+query
        rows=reader.search_instruments(query,family,name=query,market='BYMA')
        if not isinstance(rows,list) or any(not isinstance(r,dict) for r in rows):
            raise ProbeStop('INVALID_SEARCH_SHAPE')
        item.update(status='FILTER_RESULTS' if rows else 'EMPTY_FILTER_RESULT',
                    count=len(rows),sample=public_sample(rows))
        # Sólo identidades devueltas; nunca pedir un book para el texto del filtro.
        for r in rows:
            ticker=r.get('ticker')
            if (not isinstance(ticker,str) or not ticker.strip() or len(ticker)>80
                    or r.get('type')!=family or r.get('market')!='BYMA'):
                continue
            identity=(ticker,family)
            if identity not in seen:
                seen.add(identity)
                candidates.append(identity)
                break  # un candidato nuevo por filtro; máximo cuatro en total
    for ticker,family in candidates:
        settlement='INMEDIATA' if family=='CAUCIONES' else 'A-24HS'
        item={'ticker':ticker,'family':family,'query_settlement':settlement,
              'settlement_certified':False,'status':'NOT_QUERIED'}
        report['quotes'].append(item)
        if settlement not in config['settlements']:
            item['status']='SETTLEMENT_NOT_ENUMERATED'
            continue
        for method in ('book','current'):
            report['stage']=method.upper()+'_'+ticker
            value=getattr(reader,method)(quote(ticker,safe=''),family,settlement)
            if not isinstance(value,dict):
                raise ProbeStop('INVALID_'+method.upper()+'_SHAPE')
            item[method]=public_sample(value)
        item['status']='OBSERVED_UNINTERPRETED'
    report.update(status='COMPLETED_OBSERVATION',stage='DONE')


@contextmanager
def bounded_transport(report):
    """Detener el diagnóstico antes de que el SDK reintente un rechazo HTTP."""
    import requests
    original=requests.sessions.Session.request
    def request(session,method,url,**kwargs):
        path=urlsplit(url).path.rstrip('/').lower()
        if method.upper()=='POST' and path!='/api/1.0/account/loginapi':
            raise ProbeStop('NO_REFRESH_OR_SECOND_AUTH_FLOW')
        if len(report['http'])>=24:
            raise ProbeStop('HTTP_LIMIT')
        item={'method':method.upper(),'path':path,'status':None}
        report['http'].append(item)
        response=original(session,method,url,**kwargs)
        item['status']=response.status_code
        if response.status_code!=200:
            raise ProbeStop('HTTP_'+str(response.status_code))
        return response
    requests.sessions.Session.request=request
    try:
        yield
    finally:
        requests.sessions.Session.request=original


def live_report(secret_path=SECRET):
    report=report_base()
    reader=None
    previous_level=logging.root.manager.disable
    previous_handler=signal.getsignal(signal.SIGALRM)
    def expired(*_):
        raise ProbeStop('TIME_LIMIT_120_SECONDS')
    signal.signal(signal.SIGALRM,expired)
    signal.alarm(120)
    try:
        logging.disable(logging.CRITICAL)
        with redirect_stdout(Discard()),redirect_stderr(Discard()):
            # Ninguna importación del motor, dotenv, Telegram o lector de cuentas.
            if Path(__file__).is_file():
                sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
            from bd_ppi_readonly_guard import ProductionMarketReader
            report['sdk_version']=importlib.metadata.version('ppi-client')
            report['stage']='SECRET_FILE'
            with open(secret_path,encoding='utf-8') as handle:
                credentials=json.load(handle)
            if not isinstance(credentials,dict) or any(
                    not isinstance(credentials.get(k),str) or not credentials[k].strip()
                    for k in ('api_key','api_secret')):
                raise ProbeStop('INVALID_SECRET_FORMAT')
            reader=ProductionMarketReader(credentials['api_key'],credentials['api_secret'])
            del credentials
            with bounded_transport(report):
                collect(reader,report)
    except ProbeStop as exc:
        report.update(status='STOPPED',reason=str(exc))
    except Exception as exc:
        report.update(status='STOPPED',reason=type(exc).__name__)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM,previous_handler)
        if reader is not None:
            report['reader_metrics']=reader.metrics
            reader.close()
        logging.disable(previous_level)
    return report


def docker(args, *, timeout=15):
    # El operador ya verificó sudo -n docker. No requiere sudo para Python.
    result=subprocess.run(['sudo','-n','docker',*args],capture_output=True,text=True,timeout=timeout)
    if result.returncode:
        raise ProbeStop('DOCKER_COMMAND_FAILED_'+args[0].upper())
    return result.stdout


def host_report(archive):
    """No lee el secreto en el host; lo monta sólo en el contenedor creado aquí."""
    report=report_base()
    created=None
    package_dir=None
    try:
        archive=Path(archive).resolve(strict=True)
        if not archive.is_file() or archive.suffix!='.zip':
            raise ProbeStop('USE_THE_SFTP_ZIP')
        template='{"name":{{json .Name}},"running":{{json .State.Running}},"restart":{{json .HostConfig.RestartPolicy.Name}},"image":{{json .Image}},"tag":{{json .Config.Image}},"user":{{json .Config.User}}}'
        states=[json.loads(s) for s in docker(['inspect','--type','container','--format',template,BOT,OBSERVER]).splitlines()]
        if len(states)!=2 or {s['name'] for s in states}!={'/'+BOT,'/'+OBSERVER}:
            raise ProbeStop('UNEXPECTED_CONTAINERS')
        if any(s['running'] is not False or s['restart']!='no' for s in states):
            raise ProbeStop('ENGINES_MUST_REMAIN_STOPPED_NO_RESTART')
        observer=next(s for s in states if s['name']=='/'+OBSERVER)
        import re
        if observer['tag']!=IMAGE_TAG or observer['user']!='botuser' or not re.fullmatch(r'sha256:[0-9a-f]{64}',observer['image']):
            raise ProbeStop('OBSERVER_INSTALLATION_CHANGED')
        mounts=json.loads(docker(['inspect','--type','container','--format','{{json .Mounts}}',OBSERVER]))
        secrets=[m for m in mounts if m.get('Destination')==SECRET]
        if len(secrets)!=1 or secrets[0].get('Source')!=HOST_SECRET or secrets[0].get('RW') is not False or secrets[0].get('Type')!='bind':
            raise ProbeStop('OBSERVER_SECRET_MOUNT_CHANGED')
        # La transferencia SFTP puede crear el ZIP con modo 600. Copiar sólo
        # nuestro paquete a un temporal legible, sin chmod del original/secreto.
        package_dir=tempfile.TemporaryDirectory(prefix='porota-ppi-probe-')
        package=Path(package_dir.name)/'probe.zip'
        shutil.copyfile(archive,package)
        os.chmod(package,0o444)
        # create falla ante nombre ocupado; nunca borrar/reutilizar un contenedor previo.
        args=['create','--name',PROBE_NAME,'--pull','never','--network','bridge',
            '--read-only','--no-healthcheck','--user','botuser','--workdir','/tmp',
            '--cap-drop','ALL','--security-opt','no-new-privileges','--init',
            '--tmpfs','/tmp:rw,nosuid,nodev,noexec,size=32m',
            '--env','PYTHONDONTWRITEBYTECODE=1','--env','PYTHONUNBUFFERED=1',
            '--mount',f'type=bind,src={package},dst=/run/porota_probe.zip,readonly',
            '--mount',f'type=bind,src={HOST_SECRET},dst={SECRET},readonly',
            '--entrypoint','python',observer['image'],'/run/porota_probe.zip','--live']
        output=docker(args).strip()
        if not re.fullmatch(r'[0-9a-f]{64}',output):
            raise ProbeStop('UNEXPECTED_CREATE_RESULT_REVIEW_REQUIRED')
        created=output
        result=json.loads(docker(['start','--attach',created],timeout=150))
        if not isinstance(result,dict) or result.get('diagnostic')!=VERSION:
            raise ProbeStop('INVALID_PROBE_OUTPUT')
        report=result
        report['host_evidence']={'engines_stopped_before_probe':True,
            'observer_image':observer['image'],'secret_mount_readonly':True,
            'data_and_env_mounted':False}
    except ProbeStop as exc:
        report.update(status='STOPPED',reason=str(exc))
    except Exception as exc:
        report.update(status='STOPPED',reason=type(exc).__name__)
    finally:
        if created is not None:
            try:
                docker(['rm','--force',created])
                report['temporary_container_removed']=True
            except Exception:
                report.update(status='STOPPED',reason='TEMPORARY_CONTAINER_CLEANUP_FAILED',
                              temporary_container_removed=False)
        if package_dir is not None:
            package_dir.cleanup()
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--host',action='store_true',help='Inspección y contenedor temporal; requiere permiso Docker.')
    mode.add_argument('--live',action='store_true',help='Un login y lecturas de mercado en el contenedor.')
    args=parser.parse_args()
    report=host_report(sys.argv[0]) if args.host else live_report()
    output=json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)
    print(output,flush=True)
    if args.host:
        encoded=base64.b64encode(output.encode()).decode()
        print('\033]52;c;'+encoded+'\a',end='',flush=True)
    # start --attach debe devolver también diagnósticos STOPPED para poder copiarlos.
    return 0


if __name__=='__main__':
    raise SystemExit(main())
