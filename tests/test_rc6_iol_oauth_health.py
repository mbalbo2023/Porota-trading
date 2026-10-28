from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import json
import hashlib
import sqlite3

import pytest

import iol_mcp_readonly_adapter_rc6 as adapter
from scripts import rc6_iol_oauth_health as guardian
from scripts import porota_apply_host_control_plane_v2 as host
from scripts.porota_host_manifest_v2 import build_manifest


ROOT = Path(__file__).resolve().parents[1]


def test_new_guardian_cadence_is_frozen_and_installed_before_preopen(
        tmp_path,monkeypatch):
    names=('porota-iol-oauth-health-rc6.timer',
           'porota-iol-oauth-health-rc6.service',
           'porota-fast-functional-health-rc6.timer')
    source=tmp_path/'systemd'
    source.mkdir()
    for name in names:
        (source/name).write_bytes((ROOT/'systemd'/name).read_bytes())
    policy={'units':{
        'systemd/'+names[0]:{'role':'ACTIVE_TIMER','install':True,'enabled':True,
                             'active':True,'suspend_during_deploy':False},
        'systemd/'+names[1]:{'role':'ACTIVE_SERVICE','install':True},
        'systemd/'+names[2]:{'role':'ACTIVE_TIMER','install':True,'enabled':True,
                             'active':True}}}
    modes={'systemd/'+name:'100644' for name in names}
    manifest=build_manifest(tmp_path,policy,'a'*40,modes=modes)
    for name in names[:2]:
        row=next(item for item in manifest['units'] if item['unit_name']==name)
        assert row['install'] is True
        assert row['source_sha256']==hashlib.sha256((ROOT/'systemd'/name).read_bytes()).hexdigest()
    calls=[]
    def systemctl(*args,check=True):
        calls.append(args)
        if args[:2]==('systemctl','is-enabled'):
            return SimpleNamespace(stdout='enabled\n')
        if args[:2]==('systemctl','is-active'):
            return SimpleNamespace(stdout=('active\n' if args[-1]==names[0]
                                           else 'inactive\n'))
        return SimpleNamespace(stdout='')
    monkeypatch.setattr(host,'_run',systemctl)
    installed=tmp_path/'installed'
    report=host.apply(tmp_path,policy,installed,keep_active_timers_paused=True)
    assert report['status']=='GREEN'
    assert (installed/names[0]).read_bytes()==(ROOT/'systemd'/names[0]).read_bytes()
    assert (installed/names[1]).read_bytes()==(ROOT/'systemd'/names[1]).read_bytes()
    assert ('systemctl','daemon-reload') in calls
    assert not any(call[:2] in {('systemctl','start'),('systemctl','restart')} or
                   '--now' in call for call in calls)
    assert not any('ppi' in ' '.join(call).lower() for call in calls)
    workflow=(ROOT/'.github/workflows/porota-deploy-v2-promote.yml').read_text()
    assert workflow.index('scripts/porota_apply_host_control_plane_v2.py') < \
           workflow.index('python3 "$REPO/rc6_preopen.py"')


def test_iol_oauth_guardian_runs_offhours_and_premarket_without_market_polling() -> None:
    timer = (ROOT / "systemd/porota-iol-oauth-health-rc6.timer").read_text()
    assert "OnBootSec=2min" in timer
    assert "OnUnitActiveSec=10min" in timer
    assert "09:30:00 America/Argentina/Buenos_Aires" in timer
    assert "10:20:00 America/Argentina/Buenos_Aires" in timer
    assert "AccuracySec=30s" in timer


def test_iol_oauth_health_has_bounded_runtime_and_no_order_tool() -> None:
    service = (ROOT / "systemd/porota-iol-oauth-health-rc6.service").read_text()
    script = (ROOT / "scripts/rc6_iol_oauth_health.py").read_text()
    assert "TimeoutStartSec=60" in service
    assert "POROTA_IOL_OAUTH_REFRESH_SKEW_SECONDS=420" in service
    # A 15-minute access token is refreshed at the next 10-minute tick with
    # five minutes remaining (inside the seven-minute proactive skew).
    assert 900 - 600 < 420
    assert ".list_tools()" in script
    for forbidden in (
        "place_order(", "validate_order(", "place_caucion(",
        "subscribe_fci(", "redeem_fci(", "accept_ddjj(",
    ):
        assert forbidden not in script


def test_market_collector_remains_market_hours_only() -> None:
    timer = (ROOT / "systemd/porota-iol-shadow-collector-rc6.timer").read_text()
    assert "10:30..59:00 America/Argentina/Buenos_Aires" in timer
    assert "11..16:*:00 America/Argentina/Buenos_Aires" in timer
    assert "Persistent=false" in timer


def test_guardian_defers_to_live_collector_and_uses_telegram_outbox() -> None:
    script = (ROOT / "scripts/rc6_iol_oauth_health.py").read_text()
    assert 'ACTIVE_COLLECTOR_HEALTHY' in script
    assert 'MARKET_OPEN_LIVE_FRESH_COLLECTOR' in script
    assert 'mcp_probe_performed"] = False' in script
    assert "paper_notification_outbox" in script
    assert "IOL_MCP_REAUTH_REQUIRED" in script
    assert "priority,created_at,next_attempt_at" in script
    assert 'kind = "IOL_MCP_REAUTH_REQUIRED"' in script
    assert 'kind = "IOL_MCP_RETRYABLE_DEGRADED"' in script
    assert 'kind = "IOL_MCP_RECOVERED"' in script
    assert "(key, kind, body, 0, at, at)" in script
    assert "sendMessage" not in script


def test_guardian_never_enables_iol_execution_tools() -> None:
    script = (ROOT / "scripts/rc6_iol_oauth_health.py").read_text()
    for forbidden in (
        "place_order", "validate_order", "place_caucion",
        "subscribe_fci", "redeem_fci", "accept_ddjj",
        "get_portfolio", "get_balance",
    ):
        assert forbidden not in script


@pytest.fixture
def isolated_guardian(tmp_path, monkeypatch):
    market=tmp_path/'market'
    market.mkdir()
    store=tmp_path/'oauth.json'
    store.write_text(json.dumps({'issuer':'https://mcp.example/rpc',
        'tokens':{'access_token':'secret-access','refresh_token':'secret-refresh',
                  'expires_in':900},'token_obtained_at_epoch':0}))
    db=tmp_path/'paper.db'
    with sqlite3.connect(db) as c:
        c.execute('''CREATE TABLE paper_notification_outbox(
            event_key TEXT UNIQUE,kind TEXT,body TEXT,priority INTEGER,
            created_at TEXT,next_attempt_at TEXT)''')
    monkeypatch.setattr(guardian,'ROOT',market)
    monkeypatch.setattr(guardian,'STATE_FILE',market/'iol_oauth_guardian_state.json')
    monkeypatch.setattr(guardian,'DB',str(db))
    monkeypatch.setattr(guardian,'DEFAULT_STORE',store)
    monkeypatch.setattr(guardian,'_paper_safety',lambda:{'mode':'PRODUCTION_PAPER',
        'real_orders_sent':0})
    monkeypatch.setattr(guardian,'_collector_cache',lambda _:{'live_fresh':False})
    monkeypatch.setattr(guardian,'_market_open',lambda _:False)
    real_probe=guardian._probe_tools
    monkeypatch.setattr(guardian,'_probe_tools',lambda path:
                        real_probe(path,pause=lambda _:None))
    return store,db,market


def _alert_rows(db):
    with sqlite3.connect(db) as c:
        return c.execute('SELECT event_key,kind,body,priority FROM paper_notification_outbox '
                         'ORDER BY rowid').fetchall()


def test_transient_503_refresh_recovers_once_without_false_reauth(
        isolated_guardian,monkeypatch,capsys):
    store,db,market=isolated_guardian
    calls=[]
    class Client:
        def __init__(self,path,*,timeout_seconds):
            assert path==store and timeout_seconds==6
        def list_tools(self):
            calls.append('tools/list')
            if len(calls)==1:
                raise adapter.IOLMCPError('IOL_MCP_OAUTH_REFRESH_FAILED',
                                          status_code=503,failure_class='HTTP')
            return {'tools':[{'name':'get_caucion_rates'}]}
    monkeypatch.setattr(guardian,'OAuthStoreReadOnlyMCP',Client)
    assert guardian.main()==0
    result=json.loads(capsys.readouterr().out)
    state=json.loads((market/'iol_oauth_guardian_state.json').read_text())
    assert len(calls)==2 and result['state']=='AUTHENTICATED'
    assert result['probe_attempts']==2 and result['tools_discovered']==1
    assert result['recovered_transient']=={
        'state':'DEGRADED_RETRYABLE','reason':'IOL_MCP_OAUTH_REFRESH_FAILED',
        'failure_class':'HTTP','http_status':503}
    assert state['state']=='AUTHENTICATED' and state['probe_attempts']==2
    assert _alert_rows(db)==[]
    assert 'secret-access' not in json.dumps(result)
    assert 'secret-refresh' not in json.dumps(state)


@pytest.mark.parametrize('status,expected,attempts,kind',[
    (503,'DEGRADED_RETRYABLE',2,'IOL_MCP_RETRYABLE_DEGRADED'),
    (429,'DEGRADED_RETRYABLE',2,'IOL_MCP_RETRYABLE_DEGRADED'),
    (400,'REAUTH_REQUIRED',1,'IOL_MCP_REAUTH_REQUIRED'),
    (401,'REAUTH_REQUIRED',1,'IOL_MCP_REAUTH_REQUIRED'),
    (404,'DEGRADED_UNVERIFIED',1,'IOL_MCP_UNVERIFIED_DEGRADED'),
])
def test_persistent_refresh_failure_classifies_and_alerts_without_secrets(
        isolated_guardian,monkeypatch,capsys,status,expected,attempts,kind):
    _,db,market=isolated_guardian
    calls=[]
    class Client:
        def __init__(self,*_,**__): pass
        def list_tools(self):
            calls.append(1)
            raise adapter.IOLMCPError('IOL_MCP_OAUTH_REFRESH_FAILED',
                                      status_code=status,failure_class='HTTP')
    monkeypatch.setattr(guardian,'OAuthStoreReadOnlyMCP',Client)
    assert guardian.main()==2
    result=json.loads(capsys.readouterr().out)
    state=json.loads((market/'iol_oauth_guardian_state.json').read_text())
    assert len(calls)==attempts and result['state']==expected
    assert result['http_status']==status and state['failure_class']=='HTTP'
    rows=_alert_rows(db)
    assert len(rows)==1 and rows[0][1]==kind and rows[0][3]==0
    assert 'secret' not in rows[0][2]
    assert 'IOL_MCP_OAUTH_REFRESH_FAILED' in rows[0][2]
    # Repeated failures do not multiply an incident or send directly.
    assert guardian.main()==2
    capsys.readouterr()
    assert len(_alert_rows(db))==1
    assert json.loads((market/'iol_oauth_guardian_state.json').read_text())['incident_seq']==1


def test_transport_failure_exhaustion_is_retryable_not_reauth(
        isolated_guardian,monkeypatch,capsys):
    _,db,market=isolated_guardian
    calls=[]
    class Client:
        def __init__(self,*_,**__): pass
        def list_tools(self):
            calls.append(1)
            raise adapter.IOLMCPError('IOL_MCP_OAUTH_REFRESH_FAILED',
                                      failure_class='TRANSPORT')
    monkeypatch.setattr(guardian,'OAuthStoreReadOnlyMCP',Client)
    assert guardian.main()==2
    result=json.loads(capsys.readouterr().out)
    assert len(calls)==2 and result['state']=='DEGRADED_RETRYABLE'
    assert result['http_status'] is None
    assert json.loads((market/'iol_oauth_guardian_state.json').read_text())['state']== \
        'DEGRADED_RETRYABLE'
    assert _alert_rows(db)[0][1]=='IOL_MCP_RETRYABLE_DEGRADED'


def test_recovered_previous_false_reauth_emits_single_idempotent_closure(
        isolated_guardian,monkeypatch,capsys):
    _,db,market=isolated_guardian
    (market/'iol_oauth_guardian_state.json').write_text(json.dumps({
        'state':'REAUTH_REQUIRED','incident_seq':7,
        'last_reauth_required_at':'2026-10-28T21:15:09+00:00'}))
    class Client:
        def __init__(self,*_,**__): pass
        def list_tools(self): return {'tools':[{'name':'get_caucion_rates'}]}
    monkeypatch.setattr(guardian,'OAuthStoreReadOnlyMCP',Client)
    assert guardian.main()==0
    result=json.loads(capsys.readouterr().out)
    assert result['state']=='AUTHENTICATED' and result['recovery_alert']=='ENQUEUED'
    rows=_alert_rows(db)
    assert len(rows)==1 and rows[0][0]=='iol-mcp-recovered:7'
    assert rows[0][1]=='IOL_MCP_RECOVERED'
    assert guardian.main()==0
    capsys.readouterr()
    assert len(_alert_rows(db))==1


def test_backoff_respects_long_retry_after_and_never_retries_auth_rejection():
    assert guardian._retry_delay(adapter.IOLMCPError(
        'IOL_MCP_OAUTH_REFRESH_FAILED',status_code=429,retry_after=60)) is None
    assert guardian._failure(adapter.IOLMCPError(
        'IOL_MCP_OAUTH_REFRESH_FAILED',status_code=400))['state']=='REAUTH_REQUIRED'
    assert guardian._failure(adapter.IOLMCPError(
        'IOL_MCP_OAUTH_REFRESH_FAILED',failure_class='INVALID_JSON'))['state']== \
        'DEGRADED_UNVERIFIED'
