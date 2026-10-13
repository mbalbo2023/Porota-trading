"""Proposed RC6 delegated lease V2 verifier, isolated from V1 gate admission.

No function in this module issues authority or writes GitHub state.  A human
grant and an independently signed controller pulse are both required.  The
material carrier must not import this module until the V2 contract, protected
control ref and workflow provenance have been separately accepted.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import re

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

ISSUES = (471, 473)
REPO = 'mbalbo2023/Porota-trading'
GRANT_SCHEMA = 'porota.rc6.delegated-grant.v2'
PULSE_SCHEMA = 'porota.rc6.delegated-pulse.v2'
GRANT_KEYS = frozenset(('schema', 'session', 'source_sha', 'source_tree',
    'gate', 'branch', 'launch_receipt_url', 'controller_run_id', 'nonce',
    'public_key_hex', 'issued_at', 'expires_at', 'mode', 'real_orders_sent',
    'deploy_owner', 'qualification_claimed'))
PULSE_KEYS = frozenset(('schema', 'grant_sha256', 'controller_run_id',
    'candidate_run_id', 'candidate_run_attempt', 'sequence', 'previous_sha256',
    'issued_at', 'expires_at', 'signature_hex'))
SHA256 = re.compile(r'[0-9a-f]{64}\Z')
SHA1 = re.compile(r'[0-9a-f]{40}\Z')


class AuthorityV2Error(ValueError):
    """Fail closed without leaking a token, response body or private key."""


def need(condition, reason):
    if not condition:
        raise AuthorityV2Error(reason)


def wire(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'),
        ensure_ascii=True, allow_nan=False) + '\n').encode('ascii')


def instant(value):
    need(type(value) is str and re.fullmatch(
        r'20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z', value),
        'V2_EXACT_UTC_REQUIRED')
    try:
        return datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
    except ValueError as error:
        raise AuthorityV2Error('V2_VALID_UTC_REQUIRED') from error


def document(body, keys):
    need(type(body) is str and 0 < len(body.encode('utf-8')) <= 8192,
        'V2_BOUNDED_BODY_REQUIRED')
    def pairs(items):
        result = {}
        for key, value in items:
            need(key not in result, 'V2_DUPLICATE_JSON_KEY')
            result[key] = value
        return result
    try:
        value = json.loads(body, object_pairs_hook=pairs,
            parse_constant=lambda _value: need(False, 'V2_NONFINITE_JSON'))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise AuthorityV2Error('V2_CANONICAL_JSON_REQUIRED') from error
    need(type(value) is dict and value.keys() == keys and body.encode() == wire(value),
        'V2_EXACT_CANONICAL_FIELDS_REQUIRED')
    return value


def paired_comments(rows, author):
    need(type(rows) is dict and set(rows) == set(ISSUES), 'V2_BOTH_ISSUES_REQUIRED')
    bodies = []
    for issue in ISSUES:
        row = rows[issue]
        need(type(row) is dict and type(row.get('id')) is int and row['id'] > 0
            and row.get('user', {}).get('login') == author
            and row.get('issue_url') == 'https://api.github.com/repos/' + REPO
                + '/issues/' + str(issue)
            and row.get('html_url') == 'https://github.com/' + REPO
                + '/issues/' + str(issue) + '#issuecomment-' + str(row['id'])
            and row.get('created_at') == row.get('updated_at'),
            'V2_AUTHENTIC_IMMUTABLE_PAIR_REQUIRED')
        instant(row['created_at'])
        bodies.append(row.get('body'))
    need(bodies[0] == bodies[1], 'V2_PAIR_BODY_MISMATCH')
    return bodies[0], tuple(instant(rows[i]['created_at']) for i in ISSUES)


def verify_grant(rows, *, now, source_sha, source_tree, gate, session,
                 controller_run_id, launch_receipt_url):
    """Authenticate a finite human grant, never a bot-issued owner transfer."""
    need(type(now) is datetime and now.tzinfo is not None and now.utcoffset() == timedelta(0),
        'V2_AWARE_UTC_NOW_REQUIRED')
    body, created = paired_comments(rows, 'mbalbo2023')
    grant = document(body, GRANT_KEYS)
    issued, expires = instant(grant['issued_at']), instant(grant['expires_at'])
    need(grant['schema'] == GRANT_SCHEMA and grant['source_sha'] == source_sha
        and grant['source_tree'] == source_tree and SHA1.fullmatch(source_sha)
        and SHA1.fullmatch(source_tree) and grant['gate'] == gate
        and grant['session'] == session and grant['controller_run_id'] == controller_run_id
        and type(controller_run_id) is int and controller_run_id > 0
        and grant['launch_receipt_url'] == launch_receipt_url
        and type(grant['branch']) is str and grant['branch'] not in ('main', '')
        and type(grant['nonce']) is str and re.fullmatch(r'[0-9a-f]{32}', grant['nonce'])
        and type(grant['public_key_hex']) is str
        and SHA256.fullmatch(grant['public_key_hex'])
        and grant['mode'] == 'PRODUCTION_PAPER / SIMULATION'
        and type(grant['real_orders_sent']) is int and grant['real_orders_sent'] == 0
        and grant['deploy_owner'] == 'NOT_ACQUIRED'
        and grant['qualification_claimed'] is False
        and issued <= min(created) <= max(created) <= issued + timedelta(minutes=2)
        and issued <= now < expires <= issued + timedelta(hours=8),
        'V2_EXACT_HUMAN_GRANT_INVALID')
    return grant, hashlib.sha256(body.encode()).hexdigest()


def verify_pulse(rows, *, grant, grant_sha256, candidate_run_id, now,
                 previous_sha256=None, previous_sequence=0):
    """Verify one chained pulse for the one candidate run and two Issues."""
    need(type(now) is datetime and now.tzinfo is not None and now.utcoffset() == timedelta(0),
        'V2_AWARE_UTC_NOW_REQUIRED')
    need(type(previous_sequence) is int and previous_sequence >= 0
        and (previous_sha256 is None if previous_sequence == 0 else
             type(previous_sha256) is str and SHA256.fullmatch(previous_sha256)),
        'V2_PREVIOUS_CHAIN_REQUIRED')
    body, created = paired_comments(rows, 'github-actions[bot]')
    pulse = document(body, PULSE_KEYS)
    issued, expires = instant(pulse['issued_at']), instant(pulse['expires_at'])
    need(pulse['schema'] == PULSE_SCHEMA and pulse['grant_sha256'] == grant_sha256
        and pulse['controller_run_id'] == grant['controller_run_id']
        and pulse['candidate_run_id'] == candidate_run_id
        and type(candidate_run_id) is int and candidate_run_id > 0
        and type(pulse['candidate_run_attempt']) is int and pulse['candidate_run_attempt'] == 1
        and type(pulse['sequence']) is int and pulse['sequence'] == previous_sequence + 1
        and pulse['previous_sha256'] == (previous_sha256 if previous_sequence else '0' * 64)
        and type(pulse['signature_hex']) is str
        and re.fullmatch(r'[0-9a-f]{128}', pulse['signature_hex'])
        and issued <= min(created) <= max(created) <= issued + timedelta(seconds=30)
        and issued <= now < expires <= issued + timedelta(seconds=120)
        and expires <= instant(grant['expires_at']),
        'V2_FRESH_CHAINED_PULSE_REQUIRED')
    signed = {key: value for key, value in pulse.items() if key != 'signature_hex'}
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(grant['public_key_hex'])).verify(
            bytes.fromhex(pulse['signature_hex']), wire(signed))
    except (InvalidSignature, ValueError) as error:
        raise AuthorityV2Error('V2_CONTROLLER_SIGNATURE_INVALID') from error
    return pulse, hashlib.sha256(body.encode()).hexdigest()


def verify_revocation_ref(*, previous_head, current_head, is_descendant,
                          has_revocation, protection_verified):
    """The caller must obtain fresh GitHub ref/commit/protection responses."""
    need(protection_verified is True and type(current_head) is str
        and SHA1.fullmatch(current_head), 'V2_PROTECTED_FRESH_REF_REQUIRED')
    if previous_head is not None:
        need(type(previous_head) is str and SHA1.fullmatch(previous_head)
            and is_descendant(previous_head, current_head) is True,
            'V2_REVOCATION_REF_REWOUND')
    need(has_revocation(current_head) is False, 'V2_HUMAN_REVOKED')
    return current_head
