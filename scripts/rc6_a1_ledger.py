"""Isolated V2 ledger reader. No material gate imports this module.

The reader deliberately refuses to infer a protected ref from a caller-owned
boolean.  Its transport must fetch the current GitHub rules and negative
permission-probe receipt.  A missing proof, 403 or a race is terminal.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from scripts import rc6_delegated_renewal as a0

REPO = 'mbalbo2023/Porota-trading'
HUMAN = 'rc6-control-human-v2'
PULSE = 'rc6-control-pulses-v2'
SCHEMA = 'porota.rc6.pulse-ledger.v2'
HEX40 = re.compile(r'[0-9a-f]{40}\Z')
HEX64 = re.compile(r'[0-9a-f]{64}\Z')


class LedgerError(ValueError):
    pass


def need(ok, reason):
    if not ok:
        raise LedgerError(reason)


def canonical(value):
    return a0.wire(value)


class GitHubRead:
    """Real HTTPS transport with physical-attempt metrics, no local authority cache."""
    def __init__(self, token=None, timeout=8):
        self.token, self.timeout, self.events = token, timeout, []

    def get(self, path):
        need(path.startswith('/') and '?' not in path.split('#')[0] or
             path == '/rulesets?includes_parents=true', 'A1_PATH_INVALID')
        started = time.monotonic()
        req = Request('https://api.github.com/repos/' + REPO + path,
            headers={'Accept':'application/vnd.github+json',
                     'X-GitHub-Api-Version':'2022-11-28',
                     **({'Authorization':'Bearer '+self.token} if self.token else {})})
        status = 0
        size = 0
        try:
            with urlopen(req, timeout=self.timeout) as response:
                status = response.status
                raw = response.read(1024*1024+1)
                size = len(raw)
                need(size <= 1024*1024 and status == 200, 'A1_HTTP_INVALID')
                return json.loads(raw)
        except HTTPError as error:
            status = error.code
            raise LedgerError('A1_HTTP_UNAVAILABLE_'+str(status)) from error
        except (OSError, ValueError) as error:
            raise LedgerError('A1_HTTP_UNAVAILABLE') from error
        finally:
            self.events.append({'path':path, 'status':status, 'bytes':size,
                'elapsed_ms':round((time.monotonic()-started)*1000, 3)})


def effective_protection(rule_rows, *, ref, bot_probe):
    """Require an actual API ruleset and an independently witnessed bot denial.

    The negative probe must be a separate authentic Actions receipt verified
    by the caller, with an exact ruleset id/digest and 403 response.  This
    parser is necessary but not sufficient to prove those external facts.
    """
    need(isinstance(rule_rows, list) and isinstance(bot_probe, dict),
         'A1_PROTECTION_UNAVAILABLE')
    matched = [r for r in rule_rows if r.get('enforcement') == 'active'
        and r.get('target') == 'branch' and r.get('name') == 'rc6-human-v2'
        and r.get('id') == bot_probe.get('ruleset_id')]
    need(len(matched) == 1 and bot_probe.get('result') == 'DENIED_403'
        and bot_probe.get('ref') == ref
        and bot_probe.get('verified_run') is True
        and bot_probe.get('bot_actor') == 'github-actions[bot]',
        'A1_HUMAN_REF_NOT_PROVEN')
    r = matched[0]
    rules = {v.get('type') for v in r.get('rules', [])}
    need({'non_fast_forward','deletion','required_signatures'} <= rules
         and r.get('bypass_actors') == [], 'A1_REF_RULES_INSUFFICIENT')
    return r['id']


def follow_linear(get, *, new_head, old_head, limit=2048):
    """Return every new commit in oldest-first order, rejecting merges/rewinds."""
    need(HEX40.fullmatch(new_head) and (old_head is None or HEX40.fullmatch(old_head)),
         'A1_HEAD_INVALID')
    result, seen, cursor = [], set(), new_head
    while cursor != old_head:
        need(cursor not in seen and len(result) < limit, 'A1_LEDGER_CYCLE_OR_LIMIT')
        seen.add(cursor)
        row = get('/git/commits/'+cursor)
        need(row.get('sha') == cursor and len(row.get('parents', [])) <= 1,
             'A1_COMMIT_OR_PARENT_INVALID')
        result.append(row)
        parents = row['parents']
        need(parents or old_head is None, 'A1_LEDGER_REWIND')
        if not parents:
            break
        cursor = parents[0]['sha']
    need(old_head is None or cursor == old_head, 'A1_LEDGER_REWIND')
    return list(reversed(result))


def verify_pulse_commit(row, *, public_key_hex, grant_digest, candidate_run_id,
                        prior_sha, prior_sequence, now, source_sha, source_tree):
    need(row.get('sha') and HEX40.fullmatch(row['sha']), 'A1_PULSE_COMMIT_INVALID')
    try:
        body = json.loads(row['message'])
        sig = bytes.fromhex(body.pop('signature_hex'))
        key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
        key.verify(sig, b'POROTA-RC6-PULSE-LEDGER-V2\0'+canonical(body))
    except (KeyError, ValueError, InvalidSignature, TypeError) as error:
        raise LedgerError('A1_PULSE_SIGNATURE_INVALID') from error
    need(body.get('schema') == SCHEMA
        and body.get('grant_sha256') == grant_digest
        and body.get('candidate_run_id') == candidate_run_id
        and body.get('source_sha') == source_sha
        and body.get('source_tree') == source_tree
        and body.get('sequence') == prior_sequence+1
        and body.get('previous_sha256') == (prior_sha or '0'*64),
        'A1_PULSE_BINDING_OR_REPLAY')
    issued, expires = a0.instant(body['issued_at']), a0.instant(body['expires_at'])
    need(issued <= now < expires <= issued + timedelta(seconds=120)
        and a0.instant(row['created_at']) <= now,
        'A1_PULSE_TIME_INVALID')
    return hashlib.sha256(canonical({**body,'signature_hex':sig.hex()})).hexdigest()


def observe(get, *, previous_human_head, grant_id, human_ref, pulse_ref,
            protection_probe, expected_rule_id):
    """Double-read human HEAD; every intervening revocation is terminal.

    A changing HEAD is RED; the caller may begin a *new* observation under its
    original deadline. This function never treats a stale snapshot as GREEN.
    """
    rules = get('/rulesets?includes_parents=true')
    rule = effective_protection(rules, ref=HUMAN, bot_probe=protection_probe)
    need(rule == expected_rule_id, 'A1_PROTECTION_CHANGED')
    h1 = get('/git/ref/heads/'+HUMAN)['object']['sha']
    new = follow_linear(get, new_head=h1, old_head=previous_human_head)
    for item in new:
        try:
            doc = json.loads(item['message'])
        except (ValueError, KeyError, TypeError) as error:
            raise LedgerError('A1_HUMAN_EVENT_INVALID') from error
        need(doc.get('schema') == 'porota.rc6.human-control.v2'
             and item.get('verification', {}).get('verified') is True,
             'A1_HUMAN_EVENT_UNAUTHENTICATED')
        if doc.get('kind') == 'REVOKE' and doc.get('grant_id') == grant_id:
            raise LedgerError('A1_HUMAN_REVOKED')
    p = get('/git/ref/heads/'+PULSE)['object']['sha']
    pulse = get('/git/commits/'+p)
    h2 = get('/git/ref/heads/'+HUMAN)['object']['sha']
    need(h1 == h2, 'A1_REVOCATION_RACE')
    return {'human_head':h2, 'pulse_head':p, 'pulse':pulse,
        'new_human_commits':len(new),'ruleset_id':rule}
