"""Bootstrap identity and proof counterexamples; no GitHub write."""
import json
import hashlib
import errno
import os
from pathlib import Path
import socket
import tempfile
import threading
import unittest

from scripts import rc6_a1_bootstrap as b


def env():
    return {'GITHUB_REPOSITORY':b.REPO,'GITHUB_REPOSITORY_ID':'123',
        'GITHUB_REPOSITORY_OWNER_ID':'456',
        'GITHUB_WORKFLOW_REF':b.WORKFLOW+'@'+b.BRANCH,
        'GITHUB_WORKFLOW_SHA':'a'*40,'GITHUB_SHA':'a'*40,
        'GITHUB_RUN_ID':'789','GITHUB_RUN_ATTEMPT':'1',
        'GITHUB_JOB':'isolated-probes','GITHUB_EVENT_NAME':'push',
        'GITHUB_REF':b.BRANCH}


class BootstrapTests(unittest.TestCase):
    def test_attestation_certificate_binding_and_edits(self):
        _, receipt = b.generate(env())
        c = receipt['context']
        digest = hashlib.sha256(b.wire(receipt)).hexdigest()
        cert = {
            'issuer':'https://token.actions.githubusercontent.com',
            'subjectAlternativeName':'https://github.com/'+c['GITHUB_WORKFLOW_REF'],
            'buildSignerDigest':c['GITHUB_WORKFLOW_SHA'],
            'githubWorkflowSHA':c['GITHUB_WORKFLOW_SHA'],
            'sourceRepositoryDigest':c['GITHUB_SHA'],
            'sourceRepositoryIdentifier':c['GITHUB_REPOSITORY_ID'],
            'sourceRepositoryOwnerIdentifier':c['GITHUB_REPOSITORY_OWNER_ID'],
            'githubWorkflowRepository':b.REPO,
            'sourceRepositoryRef':c['GITHUB_REF'],
            'githubWorkflowTrigger':c['GITHUB_EVENT_NAME'],
            'runInvocationURI':'https://github.com/'+b.REPO+'/actions/runs/789/attempts/1',
            'runnerEnvironment':'github-hosted',
        }
        verified=[{'verificationResult':{
            'verifiedTimestamps':[{'timestamp':'2026-10-14T00:00:00Z'}],
            'statement':{'predicateType':'https://slsa.dev/provenance/v1',
                         'subject':[{'name':'public-key.json','digest':{'sha256':digest}}]},
            'signature':{'certificate':cert}}}]
        self.assertEqual(b.verify_attestation_certificate(receipt, verified),digest)
        for field, wrong in [('sourceRepositoryIdentifier','999'),
                             ('githubWorkflowSHA','b'*40),
                             ('runInvocationURI',cert['runInvocationURI']+'2'),
                             ('sourceRepositoryRef','refs/heads/main'),
                             ('runnerEnvironment','self-hosted')]:
            changed=json.loads(json.dumps(verified))
            changed[0]['verificationResult']['signature']['certificate'][field]=wrong
            with self.subTest(field=field), self.assertRaisesRegex(
                    b.BootstrapError,'ATTESTATION_BINDING_INVALID'):
                b.verify_attestation_certificate(receipt,changed)
        changed=json.loads(json.dumps(verified))
        changed[0]['verificationResult']['statement']['subject'][0]['digest']['sha256']='0'*64
        with self.assertRaisesRegex(b.BootstrapError,'ATTESTATION_BINDING_INVALID'):
            b.verify_attestation_certificate(receipt,changed)

    def test_memory_key_live_challenge_and_no_private_receipt(self):
        with tempfile.TemporaryDirectory() as root:
            errors = []
            def server():
                try: b.serve(root,env(),max_seconds=4)
                except Exception as e: errors.append(str(e))
            t=threading.Thread(target=server)
            t.start()
            path=Path(root)/'challenge.sock'
            import time
            for _ in range(100):
                if path.exists(): break
                time.sleep(.01)
            try:
                proof=b.challenge(root)
            except PermissionError as error:
                if error.errno == errno.EPERM:
                    self.skipTest('local sandbox blocks AF_UNIX; runner must pass without skip')
                raise
            t.join(5)
            self.assertFalse(errors)
            self.assertFalse(t.is_alive())
            self.assertTrue(proof['challenge_verified'])
            raw=(Path(root)/'public-key.json').read_text()
            self.assertNotIn('private_key',raw)
            self.assertFalse(path.exists())
            self.assertEqual(oct(os.stat(root).st_mode & 0o777),'0o700')

    def test_context_spoof_pr_and_run_mismatch_rejected(self):
        for changed in ({'GITHUB_EVENT_NAME':'pull_request_target'},
                        {'GITHUB_SHA':'b'*40},
                        {'GITHUB_REF':'refs/heads/main'},
                        {'GITHUB_WORKFLOW_REF':b.WORKFLOW+'@refs/heads/main'}):
            with self.assertRaisesRegex(b.BootstrapError,'CONTEXT_INVALID'):
                b.generate({**env(),**changed})
        _,receipt=b.generate(env())
        run={'id':789,'run_attempt':1,'head_sha':'a'*40,'event':'push',
            'head_branch':b.BRANCH.removeprefix('refs/heads/'),
            'path':'.github/workflows/rc6-ci-a1-probes.yml'}
        jobs=[{'id':888,'name':'isolated-probes','run_id':789,
            'head_sha':'a'*40,'runner_name':'GitHub Actions 1'}]
        self.assertEqual(b.verify_run(receipt,run,jobs,
            expected_job='isolated-probes'),888)
        for bad_run in ({**run,'id':790},{**run,'run_attempt':2},
                        {**run,'head_sha':'b'*40},{**run,'event':'workflow_run'}):
            with self.assertRaisesRegex(b.BootstrapError,'RUN_OR_JOB_MISMATCH'):
                b.verify_run(receipt,bad_run,jobs,expected_job='isolated-probes')
