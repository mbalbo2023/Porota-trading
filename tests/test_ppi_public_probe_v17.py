"""Diagnóstico de contrato público: SDK real con transporte y Docker locales falsos."""
import base64
import hashlib
import importlib.util
import json
import logging
from pathlib import Path
import subprocess
import sys
from urllib.parse import parse_qs, urlsplit
import zipfile

import pytest
import requests

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def module(name,path):
    spec=importlib.util.spec_from_file_location(name,ROOT/path)
    result=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


probe=module('probe','scripts/v17_ppi_public_probe.py')
builder=module('builder','scripts/build_v17_ppi_probe.py')
CONFIG={'instrument_types':['ACCIONES','CAUCIONES','BONOS','OPCIONES','FUTUROS','FCI'],
        'markets':['BYMA','ROFEX'],'settlements':['INMEDIATA','A-24HS'],
        'quantity_types':['DINERO','PAPELES','CANTIDAD-TOTAL'],
        'operation_terms':['POR-EL-DÍA'],'operation_types':['PRECIO-LIMITE'],
        'operations':['COMPRA','VENTA','COLOCAR-CAUCIÓN']}
CONFIG_PATHS=dict(zip(('instrumenttypes','markets','settlements','quantitytypes',
                      'operationterms','operationtypes','operations'),CONFIG))
FIXTURE_KEY='PUBLIC_PROBE_KEY_FIXTURE_ONLY'
FIXTURE_SECRET='PUBLIC_PROBE_SECRET_FIXTURE_ONLY'
FIXTURE_TOKEN='PUBLIC_PROBE_TOKEN_FIXTURE_ONLY'


@pytest.fixture
def transport(tmp_path,monkeypatch):
    secret=tmp_path/'fixture.json'
    secret.write_text(json.dumps({'api_key':FIXTURE_KEY,'api_secret':FIXTURE_SECRET}))
    calls=[]
    responses={}
    def send(adapter,request,**kwargs):
        path=urlsplit(request.url).path
        query=parse_qs(urlsplit(request.url).query)
        calls.append((request.method,path,query))
        name=path.rsplit('/',1)[-1].lower()
        status=200
        if name=='loginapi':
            assert request.headers['ApiKey']==FIXTURE_KEY
            assert request.headers['ApiSecret']==FIXTURE_SECRET
            # La salida no debe contener impresiones incidentales del SDK.
            print(FIXTURE_TOKEN)
            logging.critical(FIXTURE_SECRET)
            value={'accessToken':FIXTURE_TOKEN,'refreshToken':'REFRESH_FIXTURE_ONLY'}
        elif name in CONFIG_PATHS:
            value=CONFIG[CONFIG_PATHS[name]]
        elif name=='searchinstrument':
            filters={'CAUCION':'CAU-FIXTURE-1','PESOS':'CAU-FIXTURE-2',
                     'DOLAR':'CAU-FIXTURE-3','ALUA':'ALUA'}
            value=[{'ticker':filters[query['ticker'][0]],'type':query['type'][0],
                    'market':'BYMA','currency':'Pesos','apiSecret':FIXTURE_SECRET}]
        elif name in {'book','current'}:
            assert query['ticker'][0] not in {'CAUCION','PESOS','DOLAR'}
            value={'date':'2026-08-28T14:00:00Z','price':35.5,
                   'bids':[{'position':1,'price':35,'quantity':100000}],
                   'offers':[{'position':1,'price':36,'quantity':100000}],
                   'accessToken':FIXTURE_TOKEN}
        else:
            raise AssertionError('Ruta inesperada: '+path)
        if name in responses:
            status,value=responses[name]
        response=requests.Response()
        response.status_code=status
        response._content=json.dumps(value).encode()
        response.url=request.url
        response.headers['Content-Type']='application/json'
        return response
    monkeypatch.setattr(requests.adapters.HTTPAdapter,'send',send)
    return secret,calls,responses


def test_un_login_config_todas_familias_y_solo_tickers_devueltos(transport,capsys):
    secret,calls,_=transport
    before=requests.sessions.Session.request
    report=probe.live_report(secret)
    assert report['status']=='COMPLETED_OBSERVATION'
    assert report['configuration']==CONFIG
    assert len(calls)==20 and sum(p.endswith('LoginApi') for _,p,_ in calls)==1
    assert len(report['quotes'])==4
    assert report['reader_metrics']['login_calls']==1
    assert report['promotion_allowed'] is False and report['data_certified'] is False
    assert report['caucion_readiness']=='HOLD_UNVERIFIED_TERMS'
    assert requests.sessions.Session.request is before
    output=json.dumps(report)
    assert all(secret not in output for secret in (FIXTURE_KEY,FIXTURE_SECRET,FIXTURE_TOKEN))
    assert 'annual_rate_fraction' not in output and 'available_principal' not in output
    assert capsys.readouterr().out==''


@pytest.mark.parametrize('status',[302,400,401,403,429,500])
def test_rechazo_detiene_sin_refresh_reintento_o_cuerpo_de_error(transport,status):
    secret,calls,responses=transport
    responses['instrumenttypes']=(status,{'error':FIXTURE_SECRET})
    report=probe.live_report(secret)
    assert report['status']=='STOPPED' and report['reason']=='HTTP_'+str(status)
    assert len(calls)==2
    assert not any('RefreshToken' in p for _,p,_ in calls)
    assert FIXTURE_SECRET not in json.dumps(report)


@pytest.mark.parametrize('value',[None,[],{'missing':'tokens'}])
def test_login_malformado_no_inicia_consultas(transport,value):
    secret,calls,responses=transport
    responses['loginapi']=(200,value)
    report=probe.live_report(secret)
    assert report['status']=='STOPPED' and len(calls)==1


@pytest.mark.parametrize('name,value',[
    ('instrumenttypes',{}),('instrumenttypes',[None]),
    ('searchinstrument',{}),('book',[]),('current',[]),
])
def test_forma_invalida_no_se_presenta_como_dato_certificado(transport,name,value):
    secret,_,responses=transport
    responses[name]=(200,value)
    report=probe.live_report(secret)
    assert report['status']=='STOPPED' and report['promotion_allowed'] is False


def test_busqueda_vacia_no_equivale_a_familia_inhabilitada(transport):
    secret,calls,responses=transport
    responses['searchinstrument']=(200,[])
    report=probe.live_report(secret)
    assert report['status']=='COMPLETED_OBSERVATION'
    assert all(r['status']=='EMPTY_FILTER_RESULT' for r in report['searches'])
    assert report['quotes']==[] and len(calls)==12


def test_identidad_incompleta_o_de_otra_familia_no_pide_book(transport):
    secret,calls,responses=transport
    responses['searchinstrument']=(200,[{'ticker':'WRONG','type':'BONOS','market':'BYMA'},
                                      {'ticker':'MISSING_TYPE'}])
    report=probe.live_report(secret)
    assert report['quotes']==[] and len(calls)==12


def test_familia_no_enumerada_no_se_sondea(transport):
    secret,calls,responses=transport
    responses['instrumenttypes']=(200,['ACCIONES'])
    report=probe.live_report(secret)
    assert all(r['status']=='NOT_ENUMERATED' for r in report['searches'][:3])
    assert not any(q.get('type')==['CAUCIONES'] for _,_,q in calls)


def test_plazo_no_enumerado_no_se_inventa(transport):
    secret,_,responses=transport
    responses['settlements']=(200,['A-24HS'])
    report=probe.live_report(secret)
    assert all(r['status']=='SETTLEMENT_NOT_ENUMERATED' for r in report['quotes'][:3])


@pytest.mark.parametrize('value',[[],{}, {'api_key':1,'api_secret':2}])
def test_secreto_invalido_no_produce_login(transport,value):
    secret,calls,_=transport
    secret.write_text(json.dumps(value))
    report=probe.live_report(secret)
    assert report['reason']=='INVALID_SECRET_FORMAT' and calls==[]


def test_no_busca_otras_credenciales_si_archivo_falta(transport):
    secret,calls,_=transport
    report=probe.live_report(secret.parent/'missing.json')
    assert report['reason']=='FileNotFoundError' and calls==[]


def test_transporte_no_permite_refresh_y_limita_cantidad(monkeypatch):
    report=probe.report_base()
    monkeypatch.setattr(requests.sessions.Session,'request',lambda *a,**k:type('R',(),{'status_code':200})())
    with probe.bounded_transport(report):
        with pytest.raises(probe.ProbeStop,match='NO_REFRESH'):
            requests.post('https://clientapi.portfoliopersonal.com/api/1.0/Account/RefreshToken')
        for _ in range(24):
            requests.get('https://clientapi.portfoliopersonal.com/api/1.0/Configuration/Markets')
        with pytest.raises(probe.ProbeStop,match='HTTP_LIMIT'):
            requests.get('https://clientapi.portfoliopersonal.com/api/1.0/Configuration/Markets')


@pytest.mark.parametrize('method,path',[
    ('get','Account/Accounts'),('get','Account/AvailableBalance'),
    ('post','Order/Budget'),('post','Order/Confirm'),('post','Order/Cancel'),
])
def test_barrera_real_impide_cuentas_y_ordenes_sin_red(monkeypatch,method,path):
    import bd_ppi_readonly_guard as guard
    calls=[]
    monkeypatch.setattr(requests.adapters.HTTPAdapter,'send',lambda *a,**k:calls.append(1))
    reader=guard.ProductionMarketReader(FIXTURE_KEY,FIXTURE_SECRET)
    try:
        with probe.bounded_transport(probe.report_base()):
            with pytest.raises((guard.ReadOnlyPolicyViolation,probe.ProbeStop)):
                getattr(requests,method)('https://clientapi.portfoliopersonal.com/api/1.0/'+path)
        assert calls==[]
    finally:
        reader.close()


def test_expiracion_total_se_reporta_y_restaura_handler(transport,monkeypatch):
    import signal
    import bd_ppi_readonly_guard as guard
    secret,calls,_=transport
    old=signal.getsignal(signal.SIGALRM)
    def expires(_):
        signal.getsignal(signal.SIGALRM)(signal.SIGALRM,None)
    monkeypatch.setattr(guard.ProductionMarketReader,'login_once',expires)
    report=probe.live_report(secret)
    assert report['reason']=='TIME_LIMIT_120_SECONDS' and calls==[]
    assert signal.getsignal(signal.SIGALRM) is old


def test_timeout_http_no_expone_texto_ni_reintenta(transport,monkeypatch):
    secret,calls,_=transport
    def timeout(*a,**k):
        calls.append('timeout')
        raise requests.Timeout(FIXTURE_SECRET)
    monkeypatch.setattr(requests.adapters.HTTPAdapter,'send',timeout)
    report=probe.live_report(secret)
    assert report['reason']=='Timeout' and calls==['timeout']
    assert FIXTURE_SECRET not in json.dumps(report)


def test_muestra_publica_acotada_sin_valores_no_finitos():
    sample=probe.public_sample({'offers':[{'price':float('nan'),'quantity':1}]*8,
                               'api_key':FIXTURE_KEY,'description':'x'*300})
    assert len(sample['offers'])==5 and sample['offers'][0]['price']=='NON_FINITE'
    assert len(sample['description'])==200 and sample['_omitted_fields']==1
    assert FIXTURE_KEY not in json.dumps(sample,allow_nan=False)


@pytest.fixture
def host(tmp_path,monkeypatch):
    archive=tmp_path/'probe.zip'
    builder.build(archive)
    states=[{'name':'/'+name,'running':False,'restart':'no','image':'sha256:'+'a'*64,
             'tag':probe.IMAGE_TAG,'user':'botuser'} for name in (probe.BOT,probe.OBSERVER)]
    mounts=[{'Destination':probe.SECRET,'Source':probe.HOST_SECRET,'RW':False,'Type':'bind'},
            {'Destination':'/app/data','Source':'/opt/porota-trading/data','RW':True,'Type':'bind'}]
    calls=[]
    failures={}
    def docker(args,**kwargs):
        calls.append(args)
        if args[0] in failures:
            raise failures[args[0]]
        if args[0]=='inspect':
            return json.dumps(mounts) if args[4]=='{{json .Mounts}}' else '\n'.join(map(json.dumps,states))
        if args[0]=='create': return 'b'*64
        if args[0]=='start': return json.dumps(dict(probe.report_base(),status='COMPLETED_OBSERVATION'))
        if args[0]=='rm': return 'b'*64
        raise AssertionError(args)
    monkeypatch.setattr(probe,'docker',docker)
    return archive,states,mounts,calls,failures


def test_host_solo_monta_paquete_y_secreto_con_imagen_exacta(host):
    archive,_,_,calls,_=host
    report=probe.host_report(archive)
    assert report['status']=='COMPLETED_OBSERVATION' and report['temporary_container_removed']
    create=next(a for a in calls if a[0]=='create')
    assert 'sha256:'+'a'*64 in create and '--read-only' in create and '--no-healthcheck' in create
    assert create[create.index('--entrypoint')+1]=='python'
    assert create[create.index('--user')+1]=='botuser'
    mounts=[create[i+1] for i,a in enumerate(create) if a=='--mount']
    assert len(mounts)==2 and all(m.endswith(',readonly') for m in mounts)
    assert not any('/app/data' in a or '/app/.env' in a for a in create)
    assert all('.Env' not in ' '.join(a) for a in calls)
    assert calls[-1]==['rm','--force','b'*64]
    assert not any(a[0] in {'stop','restart','update'} for a in calls)
    temporary=Path(mounts[0].split('src=',1)[1].split(',dst=',1)[0])
    assert not temporary.exists() and archive.exists()


def test_paquete_sftp_privado_no_cambia_permisos_del_original(host):
    archive,_,_,_,_=host
    archive.chmod(0o600)
    before=archive.read_bytes()
    assert probe.host_report(archive)['status']=='COMPLETED_OBSERVATION'
    assert archive.stat().st_mode & 0o777==0o600 and archive.read_bytes()==before


def test_bot_activo_tambien_bloquea_el_diagnostico(host):
    archive,states,_,calls,_=host
    states[0]['running']=True
    assert probe.host_report(archive)['reason']=='ENGINES_MUST_REMAIN_STOPPED_NO_RESTART'
    assert len(calls)==1


@pytest.mark.parametrize('field,value',[
    ('running',True),('restart','unless-stopped'),('tag','other'),
    ('user','root'),('image','bad-image'),
])
def test_host_no_arranca_si_cambio_estado_o_instalacion(host,field,value):
    archive,states,_,calls,_=host
    states[1][field]=value
    report=probe.host_report(archive)
    assert report['status']=='STOPPED'
    assert all(a[0]=='inspect' for a in calls)


@pytest.mark.parametrize('field,value',[('RW',True),('Source','/other/secret'),('Type','volume'),('Destination','/elsewhere')])
def test_host_no_busca_otro_secreto_ni_cambia_permisos(host,field,value):
    archive,_,mounts,calls,_=host
    mounts[0][field]=value
    assert probe.host_report(archive)['reason']=='OBSERVER_SECRET_MOUNT_CHANGED'
    assert all(a[0]=='inspect' for a in calls)


def test_nombre_ocupado_no_borra_contenedor_anterior(host):
    archive,_,_,calls,failures=host
    failures['create']=probe.ProbeStop('DOCKER_COMMAND_FAILED_CREATE')
    assert probe.host_report(archive)['status']=='STOPPED'
    assert not any(a[0] in {'start','rm'} for a in calls)


def test_timeout_limpia_solo_contenedor_propio(host):
    archive,_,_,calls,failures=host
    failures['start']=subprocess.TimeoutExpired(['docker','start'],150)
    report=probe.host_report(archive)
    assert report['status']=='STOPPED' and report['reason']=='TimeoutExpired'
    assert report['temporary_container_removed']
    assert calls[-1]==['rm','--force','b'*64]


def test_falla_limpieza_no_se_declara_exito(host):
    archive,_,_,_,failures=host
    failures['rm']=probe.ProbeStop('DOCKER_COMMAND_FAILED_RM')
    report=probe.host_report(archive)
    assert report['reason']=='TEMPORARY_CONTAINER_CLEANUP_FAILED'
    assert report['temporary_container_removed'] is False


def test_zip_reproducible_solo_fuentes_y_cli_sin_sdk_ni_login(tmp_path):
    a,b=tmp_path/'a.zip',tmp_path/'b.zip'
    builder.build(a)
    builder.build(b)
    assert a.read_bytes()==b.read_bytes()
    with zipfile.ZipFile(a) as z:
        assert set(z.namelist())=={'__main__.py','bd_ppi_readonly_guard.py','_version.py','MANIFEST.json'}
        manifest=json.loads(z.read('MANIFEST.json'))
        for name,entry in manifest['files'].items():
            assert z.read(name)==(ROOT/entry['source']).read_bytes()
            assert hashlib.sha256(z.read(name)).hexdigest()==entry['sha256']
    result=subprocess.run([sys.executable,'-S',str(a),'--help'],capture_output=True,text=True)
    assert result.returncode==0 and '--host' in result.stdout
    with pytest.raises(FileExistsError):
        builder.build(a)


def test_host_portapapeles_json_sin_escapes_dentro_del_resultado(host,monkeypatch,capsys):
    archive,_,_,_,_=host
    monkeypatch.setattr(sys,'argv',[str(archive),'--host'])
    assert probe.main()==0
    output=capsys.readouterr().out
    displayed,encoded=output.split('\033]52;c;')
    clipboard=base64.b64decode(encoded.rstrip('\a')).decode()
    assert json.loads(displayed)==json.loads(clipboard)


def test_solo_docker_usa_sudo_sin_password_y_no_hay_fallback(monkeypatch):
    calls=[]
    result=subprocess.CompletedProcess([],0,'FIXTURE','')
    def run(args,**kwargs):
        calls.append(args)
        return result
    monkeypatch.setattr(subprocess,'run',run)
    assert probe.docker(['inspect','TEST'])=='FIXTURE'
    assert calls==[['sudo','-n','docker','inspect','TEST']]
    result.returncode=1
    result.stderr='permission denied '+FIXTURE_SECRET
    with pytest.raises(probe.ProbeStop,match='^DOCKER_COMMAND_FAILED_INSPECT$'):
        probe.docker(['inspect','TEST'])
    assert len(calls)==2  # sin sudo Python, chmod, grupo Docker ni otro intento
