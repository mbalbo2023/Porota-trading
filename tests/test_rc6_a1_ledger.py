"""Ledger counterexamples, never material admission."""
from datetime import datetime, timezone
import json
import os
import errno
import socket
import subprocess
import tempfile
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit,parse_qs
import unittest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from scripts import rc6_a1_ledger as ledger
from scripts import rc6_a1_http_ab as offline
from scripts import rc6_material_pr_admission as v1


def stamp(t):
    return datetime.strptime(t, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.old, self.rev, self.latest, self.pulse = ('a'*40, 'b'*40, 'c'*40, 'd'*40)
        self.grant_id = 'g'*32
        self.rules = [{'id':7,'name':'rc6-human-v2','enforcement':'active',
            'target':'branch','bypass_actors':[],
            'rules':[{'type':t} for t in
                ('non_fast_forward','deletion','required_signatures')]}]
        self.probe = {'ruleset_id':7,'result':'DENIED_403',
            'ref':ledger.HUMAN,'verified_run':True,
            'bot_actor':'github-actions[bot]'}
        event = lambda kind, grant: json.dumps({'schema':'porota.rc6.human-control.v2',
            'kind':kind,'grant_id':grant})
        self.commits = {self.old:{'sha':self.old,'parents':[],
            'message':event('GENESIS',self.grant_id),
            'verification':{'verified':True}},
            self.rev:{'sha':self.rev,'parents':[{'sha':self.old}],
            'message':event('REVOKE',self.grant_id),
            'verification':{'verified':True}},
            self.latest:{'sha':self.latest,'parents':[{'sha':self.rev}],
            'message':event('OTHER','x'*32),'verification':{'verified':True}},
            self.pulse:{'sha':self.pulse,'parents':[{'sha':self.old}],
            'message':'{}','created_at':'2026-10-14T00:10:00Z'}}
        self.calls = []
        self.head_reads = 0
        self.run = {'id':789,'head_sha':'a'*40,'run_attempt':1,
            'event':'push','status':'in_progress',
            'path':'.github/workflows/rc6-ci-a1-probes.yml'}
        self.job = {'id':888,'run_id':789,'status':'in_progress'}

    def get(self, path):
        self.calls.append(path)
        if path == '/rulesets?includes_parents=true': return self.rules
        if path == '/actions/runs/789': return self.run
        if path == '/actions/jobs/888': return self.job
        if path == '/git/ref/heads/'+ledger.HUMAN:
            self.head_reads += 1
            return {'object':{'sha':self.latest}}
        if path == '/git/ref/heads/'+ledger.PULSE:
            return {'object':{'sha':self.pulse}}
        if path.startswith('/git/commits/'):
            return self.commits[path.rsplit('/',1)[-1]]
        raise AssertionError(path)

    def observe(self, **kw):
        return ledger.observe(self.get, previous_human_head=self.old,
            grant_id=self.grant_id, human_ref=ledger.HUMAN,
            pulse_ref=ledger.PULSE, protection_probe=self.probe,
            expected_rule_id=7,controller_run_id=789,
            controller_job_id=888,source_sha='a'*40,**kw)

    def test_hidden_revocation_in_intermediate_commit_is_terminal(self):
        with self.assertRaisesRegex(ledger.LedgerError,'HUMAN_REVOKED'):
            self.observe()
        self.assertIn('/git/commits/'+self.rev,self.calls)

    def test_absent_or_unproven_rules_are_terminal(self):
        self.rules = []
        with self.assertRaisesRegex(ledger.LedgerError,'HUMAN_REF_NOT_PROVEN'):
            self.observe()
        self.rules = [{'id':7,'name':'rc6-human-v2','enforcement':'active',
            'target':'branch','bypass_actors':[],
            'rules':[{'type':'non_fast_forward'}]}]
        with self.assertRaisesRegex(ledger.LedgerError,'REF_RULES_INSUFFICIENT'):
            self.observe()
        self.probe['verified_run'] = False
        with self.assertRaisesRegex(ledger.LedgerError,'HUMAN_REF_NOT_PROVEN'):
            self.observe()

    def test_double_read_race_and_rewind(self):
        self.commits[self.rev]['message'] = json.dumps({
            'schema':'porota.rc6.human-control.v2','kind':'OTHER',
            'grant_id':self.grant_id})
        original = self.get
        def raced(path):
            if path == '/git/ref/heads/'+ledger.HUMAN and self.head_reads:
                return {'object':{'sha':'e'*40}}
            return original(path)
        self.get = raced
        with self.assertRaisesRegex(ledger.LedgerError,'REVOCATION_RACE'):
            self.observe()
        with self.assertRaisesRegex(ledger.LedgerError,'LEDGER_REWIND'):
            ledger.follow_linear(original,new_head=self.latest,old_head='f'*40)

    def test_signed_pulse_replay_future_and_signature(self):
        key = Ed25519PrivateKey.from_private_bytes(b'\x02'*32)
        public = key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw).hex()
        body = {'schema':ledger.SCHEMA,'grant_sha256':'a'*64,
            'candidate_run_id':123,'source_sha':'b'*40,'source_tree':'c'*40,
            'sequence':1,'previous_sha256':'0'*64,
            'issued_at':'2026-10-14T00:10:00Z',
            'expires_at':'2026-10-14T00:12:00Z'}
        body['signature_hex'] = key.sign(
            b'POROTA-RC6-PULSE-LEDGER-V2\0'+ledger.canonical(body)).hex()
        row = {'sha':self.pulse,'message':json.dumps(body),
            'created_at':'2026-10-14T00:10:01Z'}
        args = dict(public_key_hex=public,grant_digest='a'*64,
            candidate_run_id=123,prior_sha=None,prior_sequence=0,
            source_sha='b'*40,source_tree='c'*40)
        with self.assertRaisesRegex(ledger.LedgerError,'PULSE_TIME_INVALID'):
            ledger.verify_pulse_commit(row,now=stamp('2026-10-14T00:10:00Z'),**args)
        result = ledger.verify_pulse_commit(row,now=stamp('2026-10-14T00:11:00Z'),**args)
        self.assertEqual(len(result),64)
        with self.assertRaisesRegex(ledger.LedgerError,'PULSE_BINDING_OR_REPLAY'):
            ledger.verify_pulse_commit(row,now=stamp('2026-10-14T00:11:00Z'),
                **{**args,'prior_sequence':1,'prior_sha':result})
        body['signature_hex']='0'*128
        row['message']=json.dumps(body)
        with self.assertRaisesRegex(ledger.LedgerError,'PULSE_SIGNATURE_INVALID'):
            ledger.verify_pulse_commit(row,now=stamp('2026-10-14T00:11:00Z'),**args)

    def test_controller_loss_is_terminal(self):
        self.run['status']='completed'
        with self.assertRaisesRegex(ledger.LedgerError,'CONTROLLER_NOT_LIVE'):
            self.observe()
        self.run['status']='in_progress'
        self.job['status']='completed'
        with self.assertRaisesRegex(ledger.LedgerError,'CONTROLLER_NOT_LIVE'):
            self.observe()

    def test_handoff_before_six_hours_never_extends_human_ceiling(self):
        key=Ed25519PrivateKey.from_private_bytes(b'\x03'*32)
        public=key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw).hex()
        grant={'sha256':'a'*64,'source_sha':'b'*40,'source_tree':'c'*40,
            'gate':'G5P-acquire','candidate_run_id':456,
            'candidate_run_attempt':1,'controller_run_id':123,
            'expires_at':'2026-10-14T07:00:00Z','hop':0,'max_hops':1}
        body={'schema':'porota.rc6.handoff.v2','grant_sha256':'a'*64,
            'source_sha':'b'*40,'source_tree':'c'*40,'gate':'G5P-acquire',
            'candidate_run_id':456,'candidate_run_attempt':1,'old_run_id':123,
            'new_run_id':124,'new_public_key_hex':'d'*64,
            'prior_sequence':350,'prior_pulse_sha256':'e'*64,
            'grant_ceiling':grant['expires_at'],'hop':1,
            'issued_at':'2026-10-14T05:50:00Z'}
        def signed(value):
            return {**value,'signature_hex':key.sign(
                b'POROTA-RC6-HANDOFF-V2\0'+ledger.canonical(value)).hex()}
        kw=dict(old_public_key_hex=public,grant=grant,prior_sequence=350,
            prior_pulse_sha256='e'*64,old_job_started=stamp('2026-10-14T00:00:00Z'),
            old_job_live=True,successor_attestation_verified=True)
        result=ledger.verify_handoff(signed(body),
            now=stamp('2026-10-14T05:50:05Z'),**kw)
        self.assertEqual(result['grant_ceiling'],grant['expires_at'])
        with self.assertRaisesRegex(ledger.LedgerError,'GAP_OR_SCOPE'):
            ledger.verify_handoff(signed(body),now=stamp('2026-10-14T07:00:00Z'),**kw)
        with self.assertRaisesRegex(ledger.LedgerError,'GAP_OR_SCOPE'):
            ledger.verify_handoff(signed({**body,'grant_ceiling':'2026-10-14T09:00:00Z'}),
                now=stamp('2026-10-14T05:50:05Z'),**kw)
        with self.assertRaisesRegex(ledger.LedgerError,'PROVENANCE_OR_LIVENESS'):
            ledger.verify_handoff(signed(body),now=stamp('2026-10-14T05:50:05Z'),
                **{**kw,'old_job_live':False})
        with self.assertRaisesRegex(ledger.LedgerError,'GAP_OR_SCOPE'):
            ledger.verify_handoff(signed(body),now=stamp('2026-10-14T05:50:05Z'),
                **{**kw,'prior_sequence':351})
class PhysicalHttpTests(unittest.TestCase):
    def test_same_synthetic_issues_over_real_loopback_http(self):
        """Physical GET/bytes/P95, not an authenticated GitHub benchmark."""
        rows=offline.fixture()
        old='a'*40
        pulse='d'*40
        rules=[{'id':7,'name':'rc6-human-v2','enforcement':'active',
            'target':'branch','bypass_actors':[],
            'rules':[{'type':t} for t in
            ('non_fast_forward','deletion','required_signatures')]}]
        state={'rules':rules,'revoked':False,'failure':None,'requests':[],
            'human_head':old}
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_GET(self):
                started=time.monotonic()
                path=urlsplit(self.path)
                status=200
                try:
                    if state['failure']=='403':
                        status=403
                        data={'message':'denied'}
                    elif state['failure']=='304':
                        status=304
                        data={}
                    elif path.path.startswith('/issues/'):
                        issue=int(path.path.split('/')[2])
                        query=parse_qs(path.query)
                        since=v1.stamp(query['since'][0])
                        page=int(query['page'][0])
                        selected=[r for r in rows[issue]
                            if v1.effective_stamp(r)>since]
                        data=selected[(page-1)*100:page*100]
                    elif path.path=='/rulesets': data=state['rules']
                    elif path.path=='/actions/runs/789':
                        data={'id':789,'head_sha':'a'*40,'run_attempt':1,
                              'event':'push','status':'in_progress',
                              'path':'.github/workflows/rc6-ci-a1-probes.yml'}
                    elif path.path=='/actions/jobs/888':
                        data={'id':888,'run_id':789,'status':'in_progress'}
                    elif path.path=='/git/ref/heads/'+ledger.HUMAN:
                        data={'object':{'sha':state['human_head']}}
                    elif path.path=='/git/ref/heads/'+ledger.PULSE:
                        data={'object':{'sha':pulse}}
                    elif path.path=='/git/commits/'+pulse:
                        data={'sha':pulse,'message':'{}','parents':[{'sha':old}]}
                    elif path.path=='/git/commits/'+('b'*40):
                        data={'sha':'b'*40,'parents':[{'sha':old}],
                              'message':json.dumps({'schema':'porota.rc6.human-control.v2',
                                'kind':'REVOKE','grant_id':'g'*32}),
                              'verification':{'verified':True}}
                    elif path.path=='/git/commits/'+('c'*40):
                        data={'sha':'c'*40,'parents':[{'sha':'b'*40}],
                              'message':json.dumps({'schema':'porota.rc6.human-control.v2',
                                'kind':'OTHER','grant_id':'x'*32}),
                              'verification':{'verified':True}}
                    else:
                        status=404;data={'error':'unknown path'}
                    raw=json.dumps(data).encode()
                    state['requests'].append({'path':self.path,'status':status,
                        'bytes':len(raw),'ms':round((time.monotonic()-started)*1000,3)})
                    self.send_response(status)
                    self.send_header('Content-Type','application/json')
                    self.send_header('Content-Length',str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                except BrokenPipeError:
                    pass
        try:
            server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        except PermissionError as error:
            if error.errno==errno.EPERM:
                self.skipTest('local sandbox blocks sockets; runner must have no skip')
            raise
        worker=threading.Thread(target=server.serve_forever,daemon=True)
        worker.start()
        url='http://127.0.0.1:'+str(server.server_port)
        try:
            from urllib.request import urlopen
            def physical(path):
                with urlopen(url+path,timeout=5) as response:
                    return json.load(response)
            def run_a():
                start=len(state['requests'])
                begin=time.monotonic()
                matches=[]
                for issue in (471,473):
                    matches.extend(v1.recent(issue,rows[issue][0],
                        offline.BASE+offline.timedelta(minutes=31),
                        get=physical))
                calls=state['requests'][start:]
                return {'GET':len(calls),'bytes':sum(x['bytes'] for x in calls),
                    'latency_ms':round((time.monotonic()-begin)*1000,3),
                    'P95_ms':sorted(x['ms'] for x in calls)[
                        min(len(calls)-1,int(.95*len(calls)))],
                    'revoked':any(x['body']=='REVOKED=true' for x in matches)}
            def run_b():
                start=len(state['requests'])
                begin=time.monotonic()
                client=ledger.GitHubRead(timeout=5,base_url=url)
                reason=None
                try:
                    ledger.observe(client.get,previous_human_head=old,
                        grant_id='g'*32,human_ref=ledger.HUMAN,
                        pulse_ref=ledger.PULSE,
                        protection_probe={'ruleset_id':7,'result':'DENIED_403',
                          'ref':ledger.HUMAN,'verified_run':True,
                          'bot_actor':'github-actions[bot]'},
                        expected_rule_id=7,controller_run_id=789,
                        controller_job_id=888,source_sha='a'*40)
                except ledger.LedgerError as e:
                    reason=str(e)
                calls=state['requests'][start:]
                return {'GET':len(calls),'bytes':sum(x['bytes'] for x in calls),
                    'latency_ms':round((time.monotonic()-begin)*1000,3),
                    'P95_ms':sorted(x['ms'] for x in calls)[
                        min(len(calls)-1,int(.95*len(calls)))],
                    'reason':reason}
            normal_a,normal_b=run_a(),run_b()
            self.assertEqual((normal_a['GET'],normal_b['GET']),(19,7))
            self.assertIsNone(normal_b['reason'])
            state['revoked']=True
            rows[471][-1]['body']='REVOKED=true'
            rows[471][-1]['updated_at']=offline.utc(
                offline.BASE+offline.timedelta(minutes=30))
            state['human_head']='c'*40
            revoked_a,revoked_b=run_a(),run_b()
            self.assertTrue(revoked_a['revoked'])
            self.assertEqual(revoked_b['reason'],'A1_HUMAN_REVOKED')
            def stop_child(detector):
                with tempfile.TemporaryDirectory() as directory:
                    fin=os.path.join(directory,'FIN')
                    program=("import signal,time,sys\n"
                        "def finish(*args):\n open(sys.argv[1],'w').write('FIN'); sys.exit(0)\n"
                        "signal.signal(signal.SIGTERM,finish)\n"
                        "while True: time.sleep(.01)\n")
                    child=subprocess.Popen([sys.executable,'-c',program,fin])
                    time.sleep(.04)
                    started=time.monotonic()
                    try:
                        result=detector()
                        assert result.get('revoked') or result.get('reason')=='A1_HUMAN_REVOKED'
                    finally:
                        child.terminate()
                        child.wait(timeout=3)
                    assert os.path.exists(fin) and child.returncode==0
                    return round((time.monotonic()-started)*1000,3)
            term_fin={'v1_ms':stop_child(run_a),'v2_ms':stop_child(run_b)}
            state['failure']='403'
            failed_b=run_b()
            self.assertEqual(failed_b['reason'],'A1_HTTP_UNAVAILABLE_403')
            state['failure']='304'
            stale_b=run_b()
            self.assertEqual(stale_b['reason'],'A1_HTTP_UNAVAILABLE_304')
            summary={'schema':'rc6.a1.physical-loopback.v1',
                'scope':'SYNTHETIC_GITHUB_RESPONSES_REAL_HTTP_NOT_LIVE_API',
                'normal':{'v1':normal_a,'v2':normal_b},
                'revoked':{'v1':revoked_a,'v2':revoked_b},
                'http403':failed_b,'http304':stale_b,
                'revocation_to_FIN_tiny_child':term_fin,
                'writes':0,'real_orders_sent':0,'G0_G8_qualification':False}
            target=os.environ.get('RC6_A1_METRICS_PATH')
            if target:
                with open(target,'w') as output:
                    json.dump(summary,output,sort_keys=True)
            print('A1_PHYSICAL_HTTP_METRICS='+json.dumps(summary,sort_keys=True))
        finally:
            server.shutdown();server.server_close()
