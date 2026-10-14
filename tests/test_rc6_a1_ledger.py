"""Ledger counterexamples, never material admission."""
from datetime import datetime, timezone
import json
import unittest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from scripts import rc6_a1_ledger as ledger


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

    def get(self, path):
        self.calls.append(path)
        if path == '/rulesets?includes_parents=true': return self.rules
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
            expected_rule_id=7,**kw)

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
