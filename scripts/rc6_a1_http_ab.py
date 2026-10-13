"""Nonmaterial HTTP A/B fixture: actual V1 paginator vs proposed V2 read plan.

Every GET is counted, including repeat observations. The V2 side is a modeled
transport with the A0 cryptographic verifier, not an installed gate/controller.
It cannot substantiate a production API reduction or material qualification.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from urllib.parse import parse_qs

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from scripts import rc6_delegated_renewal as v2
from scripts import rc6_material_pr_admission as v1

BASE = datetime(2026, 10, 13, 22, tzinfo=timezone.utc)
SOURCE_SHA, SOURCE_TREE = 'a' * 40, 'b' * 40
COUNTS = {471: 999, 473: 861}


def utc(value):
    return value.isoformat().replace('+00:00', 'Z')


def row(issue, identifier, created, body='OBSERVATION_ONLY'):
    return {'id': identifier, 'created_at': utc(created), 'updated_at': utc(created),
        'body': body, 'issue_url': 'https://api.github.com/repos/' + v2.REPO
            + '/issues/' + str(issue),
        'html_url': 'https://github.com/' + v2.REPO + '/issues/' + str(issue)
            + '#issuecomment-' + str(identifier),
        'user': {'login': 'mbalbo2023'}}


def fixture():
    # Shapes/counts are deterministic approximations of the observed Issue sizes.
    # These are synthetic records, never historical owner receipts.
    return {issue: [row(issue, issue * 100000 + index,
        BASE + timedelta(seconds=index)) for index in range(count)]
        for issue, count in COUNTS.items()}


class Transport:
    def __init__(self, fail_on=None):
        self.paths = []
        self.fail_on = fail_on

    def get(self, path, value):
        self.paths.append(path)
        if self.fail_on is not None and path.startswith(self.fail_on):
            raise ConnectionError('A1_SYNTHETIC_TRANSPORT_UNAVAILABLE')
        return value

    def summary(self):
        return {'attempts': len(self.paths), 'resource_counts': {
            name: sum(path.startswith(name) for path in self.paths)
            for name in ('/issues/471/comments?', '/issues/473/comments?',
                '/issues/comments/', '/git/ref/', '/git/commits/')}}


def baseline(rows, now, *, fail_on=None):
    transport = Transport(fail_on)
    outcome = {}
    for issue in v2.ISSUES:
        def read(path):
            query = parse_qs(path.partition('?')[2])
            since = v1.stamp(query['since'][0])
            page = int(query['page'][0])
            selected = [item for item in rows[issue]
                if v1.effective_stamp(item) > since]
            data = selected[(page-1)*100:page*100]
            return transport.get(path, data)
        seen = v1.recent(issue, rows[issue][0], now, get=read)
        outcome[issue] = any(item['body'] == 'REVOKED=true' for item in seen)
    return transport.summary(), any(outcome.values())


def signed_pair(issue_rows, body, author, created):
    return {issue: {**row(issue, 6000000000+issue, created, body),
        'user': {'login': author}} for issue in issue_rows}


def modeled_v2(now, *, revoked=False, fail_on=None, previous_head=None,
               previous_sha256=None, previous_sequence=0):
    """Eight fresh GETs. No response is reused as current authority."""
    transport = Transport(fail_on)
    key = Ed25519PrivateKey.from_private_bytes(b'\x01'*32)  # Public test vector only.
    grant = {'schema': v2.GRANT_SCHEMA, 'session': 'CODEX_A1_OFFLINE_FIXTURE',
        'source_sha': SOURCE_SHA, 'source_tree': SOURCE_TREE, 'gate': 'G5P-acquire',
        'branch': 'work/offline-fixture',
        'launch_receipt_url': 'https://github.com/' + v2.REPO
            + '/issues/471#issuecomment-123', 'controller_run_id': 123,
        'nonce': 'c'*32, 'public_key_hex': key.public_key().public_bytes(
            Encoding.Raw, PublicFormat.Raw).hex(),
        'issued_at': utc(BASE), 'expires_at': utc(BASE+timedelta(hours=8)),
        'mode': 'PRODUCTION_PAPER / SIMULATION', 'real_orders_sent': 0,
        'deploy_owner': 'NOT_ACQUIRED', 'qualification_claimed': False}
    body = v2.wire(grant).decode()
    grants = signed_pair(v2.ISSUES, body, 'mbalbo2023', BASE+timedelta(seconds=1))
    for issue in v2.ISSUES:
        transport.get('/issues/comments/'+str(grants[issue]['id']), grants[issue])
    validated, grant_digest = v2.verify_grant(grants, now=now,
        source_sha=SOURCE_SHA, source_tree=SOURCE_TREE, gate='G5P-acquire',
        session=grant['session'], controller_run_id=123,
        launch_receipt_url=grant['launch_receipt_url'])
    pulse = {'schema': v2.PULSE_SCHEMA, 'grant_sha256': grant_digest,
        'controller_run_id': 123, 'candidate_run_id': 456,
        'candidate_run_attempt': 1, 'sequence': previous_sequence+1,
        'previous_sha256': previous_sha256 or '0'*64,
        'issued_at': utc(now-timedelta(seconds=2)),
        'expires_at': utc(now+timedelta(seconds=118))}
    pulse['signature_hex'] = key.sign(v2.wire(pulse)).hex()
    pulses = signed_pair(v2.ISSUES, v2.wire(pulse).decode(),
        'github-actions[bot]', now-timedelta(seconds=1))
    human_head = format(previous_sequence+10, '040x')
    pulse_head = format(previous_sequence+20, '040x')
    # The two refs and their commit metadata must be read fresh; this fixture
    # asserts count and ordering, but cannot prove GitHub branch protection.
    transport.get('/git/ref/heads/rc6-control-human', human_head)
    transport.get('/git/commits/'+human_head, {'revoked': revoked})
    transport.get('/git/ref/heads/rc6-control-pulses', pulse_head)
    transport.get('/git/commits/'+pulse_head,
        {'pulse_sha256': hashlib.sha256(v2.wire(pulse)).hexdigest()})
    for issue in v2.ISSUES:
        transport.get('/issues/comments/'+str(pulses[issue]['id']), pulses[issue])
    _pulse, digest = v2.verify_pulse(pulses, grant=validated,
        grant_sha256=grant_digest, candidate_run_id=456, now=now,
        previous_sha256=previous_sha256, previous_sequence=previous_sequence)
    v2.verify_revocation_ref(previous_head=previous_head,
        current_head=human_head, is_descendant=lambda old, new: old != new,
        has_revocation=lambda head: revoked, protection_verified=True)
    return transport.summary(), digest, human_head


def experiment():
    rows = fixture()
    outcomes = []
    previous_sha256 = previous_head = None
    for index in range(3):
        now = BASE+timedelta(minutes=31+index)
        revocation = index == 2
        if revocation:
            rows[471][-1]['body'] = 'REVOKED=true'
            rows[471][-1]['updated_at'] = utc(now-timedelta(seconds=1))
        old, old_revoked = baseline(rows, now)
        try:
            new, previous_sha256, previous_head = modeled_v2(now,
                revoked=revocation, previous_sha256=previous_sha256,
                previous_head=previous_head, previous_sequence=index)
            new_revoked = False
        except v2.AuthorityV2Error as error:
            # On the revocation observation the model must refuse the grant.
            if str(error) != 'V2_HUMAN_REVOKED' or not revocation:
                raise
            new = {'attempts': 8}
            new_revoked = True
        assert old_revoked == new_revoked == revocation
        outcomes.append({'observation': index+1, 'revoked': revocation,
            'v1': old, 'v2_model': new})
    return {'schema': 'porota.rc6.a1-offline-http-comparison.v1',
        'scope': 'SYNTHETIC_FIXTURE_NOT_LIVE_API_NOT_MATERIAL_GATE',
        'issue_shape': COUNTS, 'observations': outcomes,
        'v1_attempts': sum(x['v1']['attempts'] for x in outcomes),
        'v2_model_attempts': sum(x['v2_model']['attempts'] for x in outcomes),
        'live_v2_reduction_measured': False, 'real_orders_sent': 0}


if __name__ == '__main__':
    result = experiment()
    print(json.dumps(result, sort_keys=True))
