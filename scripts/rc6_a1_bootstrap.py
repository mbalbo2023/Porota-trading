"""Nonmaterial Ed25519 bootstrap/proof for a trusted Actions job.

The private key stays in this process.  The public receipt may be attested.
The diagnostic socket signs only one random challenge, never a grant/pulse.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import socket
import sys
import time

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from scripts.rc6_delegated_renewal import wire

REPO = 'mbalbo2023/Porota-trading'
BRANCH = 'refs/heads/work/rc6-ci-a1-ledger-v2-20261014'
WORKFLOW = REPO+'/.github/workflows/rc6-ci-a1-probes.yml'
DOMAIN = b'POROTA-RC6-A1-BOOTSTRAP\0'
SHA = re.compile(r'[0-9a-f]{40}\Z')


class BootstrapError(ValueError):
    pass


def require(ok, reason):
    if not ok:
        raise BootstrapError(reason)


def identity(env):
    """Environment is an input to be cross-checked with authenticated run API."""
    names = ('GITHUB_REPOSITORY','GITHUB_REPOSITORY_ID',
        'GITHUB_REPOSITORY_OWNER_ID','GITHUB_WORKFLOW_REF','GITHUB_WORKFLOW_SHA',
        'GITHUB_SHA','GITHUB_RUN_ID','GITHUB_RUN_ATTEMPT','GITHUB_JOB',
        'GITHUB_EVENT_NAME','GITHUB_REF')
    d = {key:env.get(key) for key in names}
    require(all(d.values()) and d['GITHUB_REPOSITORY'] == REPO
        and d['GITHUB_EVENT_NAME'] == 'push' and d['GITHUB_REF'] == BRANCH
        and d['GITHUB_WORKFLOW_REF'] == WORKFLOW+'@'+BRANCH
        and SHA.fullmatch(d['GITHUB_SHA'])
        and SHA.fullmatch(d['GITHUB_WORKFLOW_SHA'])
        and d['GITHUB_SHA'] == d['GITHUB_WORKFLOW_SHA']
        and all(str(d[k]).isdigit() and int(d[k]) > 0 for k in
            ('GITHUB_REPOSITORY_ID','GITHUB_REPOSITORY_OWNER_ID',
             'GITHUB_RUN_ID','GITHUB_RUN_ATTEMPT')),
        'A1_BOOTSTRAP_CONTEXT_INVALID')
    return d


def generate(env):
    context = identity(env)
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
    receipt = {'schema':'porota.rc6.a1-public-key.v1',
        'scope':'DEVELOPMENT_ONLY_NOT_G0_G8','context':context,
        'public_key_hex':public.hex(),'nonce_hex':secrets.token_hex(16),
        'real_orders_sent':0}
    return private,receipt


def serve(directory, env, max_seconds=120):
    private, receipt = generate(env)
    root = Path(directory)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(root,0o700)
    receipt_path = root/'public-key.json'
    with receipt_path.open('xb') as output:
        output.write(wire(receipt))
    socket_path = root/'challenge.sock'
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as server:
        server.bind(str(socket_path))
        os.chmod(socket_path,0o600)
        server.listen(1)
        server.settimeout(max_seconds)
        with server.accept()[0] as conn:
            challenge = conn.recv(64)
            require(len(challenge) == 32,'A1_CHALLENGE_SIZE_INVALID')
            conn.sendall(private.sign(DOMAIN+challenge+hashlib.sha256(
                wire(receipt)).digest()))
    socket_path.unlink(missing_ok=True)


def challenge(directory):
    root = Path(directory)
    receipt = json.loads((root/'public-key.json').read_bytes())
    value = secrets.token_bytes(32)
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as client:
        client.connect(str(root/'challenge.sock'))
        client.sendall(value)
        signature = client.recv(128)
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    Ed25519PublicKey.from_public_bytes(bytes.fromhex(receipt['public_key_hex'])).verify(
        signature,DOMAIN+value+hashlib.sha256(wire(receipt)).digest())
    return {'schema':'porota.rc6.a1-key-live-proof.v1',
        'receipt_sha256':hashlib.sha256(wire(receipt)).hexdigest(),
        'run_id':receipt['context']['GITHUB_RUN_ID'],
        'challenge_verified':True,'private_key_exported':False,
        'G0_G8_qualification':False,'real_orders_sent':0}


def verify_run(receipt, run, jobs, *, expected_job):
    """Authenticated Actions API objects, never an attacker-supplied run URL."""
    c = receipt['context']
    valid_jobs = [j for j in jobs if j.get('name') == expected_job
        and j.get('run_id') == int(c['GITHUB_RUN_ID'])
        and j.get('head_sha') == c['GITHUB_SHA']
        and j.get('runner_name')]
    require(run.get('id') == int(c['GITHUB_RUN_ID'])
        and run.get('run_attempt') == int(c['GITHUB_RUN_ATTEMPT'])
        and run.get('head_sha') == c['GITHUB_SHA']
        and run.get('event') == c['GITHUB_EVENT_NAME']
        and run.get('head_branch') == BRANCH.removeprefix('refs/heads/')
        and run.get('path') == '.github/workflows/rc6-ci-a1-probes.yml'
        and len(valid_jobs) == 1, 'A1_RUN_OR_JOB_MISMATCH')
    return valid_jobs[0]['id']


def verify_attestation_certificate(receipt, verified):
    """Policy check on output of a *successful* gh attestation verify invocation.

    This function does not validate Sigstore itself. The caller must first
    cryptographically verify with --repo, --signer-workflow, --signer-digest,
    --source-digest, --source-ref and --deny-self-hosted-runners.
    """
    c=receipt['context']
    digest=hashlib.sha256(wire(receipt)).hexdigest()
    expected_uri='https://github.com/'+c['GITHUB_WORKFLOW_REF']
    invocation=('https://github.com/'+REPO+'/actions/runs/'
        +c['GITHUB_RUN_ID']+'/attempts/'+c['GITHUB_RUN_ATTEMPT'])
    require(type(verified) is list and len(verified)==1,
        'A1_ATTESTATION_UNIQUE_REQUIRED')
    item=verified[0]
    result=item.get('verificationResult',{})
    cert=result.get('signature',{}).get('certificate',{})
    subjects=result.get('statement',{}).get('subject',[])
    require(result.get('verifiedTimestamps')
        and result.get('statement',{}).get('predicateType')
            == 'https://slsa.dev/provenance/v1'
        and subjects==[{'name':'public-key.json','digest':{'sha256':digest}}]
        and cert.get('issuer')=='https://token.actions.githubusercontent.com'
        and cert.get('subjectAlternativeName')==expected_uri
        and cert.get('buildSignerDigest')==c['GITHUB_WORKFLOW_SHA']
        and cert.get('githubWorkflowSHA')==c['GITHUB_WORKFLOW_SHA']
        and cert.get('sourceRepositoryDigest')==c['GITHUB_SHA']
        and cert.get('sourceRepositoryIdentifier')==c['GITHUB_REPOSITORY_ID']
        and cert.get('sourceRepositoryOwnerIdentifier')
            ==c['GITHUB_REPOSITORY_OWNER_ID']
        and cert.get('githubWorkflowRepository')==REPO
        and cert.get('sourceRepositoryRef')==c['GITHUB_REF']
        and cert.get('githubWorkflowTrigger')==c['GITHUB_EVENT_NAME']
        and cert.get('runInvocationURI')==invocation
        and cert.get('runnerEnvironment')=='github-hosted',
        'A1_ATTESTATION_BINDING_INVALID')
    return digest


if __name__ == '__main__':
    if len(sys.argv) != 3 or sys.argv[1] not in ('serve','challenge'):
        raise SystemExit('use: python -m scripts.rc6_a1_bootstrap serve|challenge DIR')
    if sys.argv[1] == 'serve':
        serve(sys.argv[2],os.environ)
    else:
        print(json.dumps(challenge(sys.argv[2]),sort_keys=True))
