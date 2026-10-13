"""Offline V2 authority counterexamples; no GitHub or material producer."""
from datetime import datetime, timezone
import unittest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from scripts import rc6_delegated_renewal as v2


def at(value):
    return datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)


def pair(body, author, created):
    return {issue: {
        'id': 6000000000 + issue, 'user': {'login': author},
        'issue_url': 'https://api.github.com/repos/' + v2.REPO + '/issues/' + str(issue),
        'html_url': 'https://github.com/' + v2.REPO + '/issues/' + str(issue)
            + '#issuecomment-' + str(6000000000 + issue),
        'created_at': created, 'updated_at': created, 'body': body,
    } for issue in v2.ISSUES}


class DelegatedAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.key = Ed25519PrivateKey.from_private_bytes(b'\x01' * 32)
        self.grant = {
            'schema': v2.GRANT_SCHEMA, 'session': 'CODEX_RC6_UNIT',
            'source_sha': 'a' * 40, 'source_tree': 'b' * 40,
            'gate': 'G5P-acquire', 'branch': 'fix/rc6-unit',
            'launch_receipt_url': 'https://github.com/' + v2.REPO
                + '/issues/471#issuecomment-123',
            'controller_run_id': 123, 'nonce': 'c' * 32,
            'public_key_hex': self.key.public_key().public_bytes(
                Encoding.Raw, PublicFormat.Raw).hex(),
            'issued_at': '2026-10-13T23:00:00Z',
            'expires_at': '2026-10-14T06:00:00Z',
            'mode': 'PRODUCTION_PAPER / SIMULATION', 'real_orders_sent': 0,
            'deploy_owner': 'NOT_ACQUIRED', 'qualification_claimed': False,
        }
        self.grant_rows = pair(v2.wire(self.grant).decode(), 'mbalbo2023',
            '2026-10-13T23:00:01Z')

    def accepted_grant(self):
        return v2.verify_grant(self.grant_rows, now=at('2026-10-13T23:10:00Z'),
            source_sha='a' * 40, source_tree='b' * 40,
            gate='G5P-acquire', session='CODEX_RC6_UNIT', controller_run_id=123,
            launch_receipt_url=self.grant['launch_receipt_url'])

    def pulse_rows(self, digest, *, overrides=None):
        pulse = {'schema': v2.PULSE_SCHEMA, 'grant_sha256': digest,
            'controller_run_id': 123, 'candidate_run_id': 456,
            'candidate_run_attempt': 1, 'sequence': 1,
            'previous_sha256': '0' * 64,
            'issued_at': '2026-10-13T23:10:00Z',
            'expires_at': '2026-10-13T23:12:00Z'}
        pulse.update(overrides or {})
        pulse['signature_hex'] = self.key.sign(v2.wire(pulse)).hex()
        return pair(v2.wire(pulse).decode(), 'github-actions[bot]',
            '2026-10-13T23:10:01Z')

    def test_one_human_grant_one_signed_pulse_and_protected_ref(self):
        grant, digest = self.accepted_grant()
        rows = self.pulse_rows(digest)
        pulse, first = v2.verify_pulse(rows, grant=grant, grant_sha256=digest,
            candidate_run_id=456, now=at('2026-10-13T23:11:00Z'))
        self.assertEqual(pulse['sequence'], 1)
        self.assertEqual(len(first), 64)
        head = v2.verify_revocation_ref(previous_head='d' * 40,
            current_head='e' * 40, is_descendant=lambda old, new: True,
            has_revocation=lambda head: False, protection_verified=True)
        self.assertEqual(head, 'e' * 40)

    def test_bot_cannot_issue_grant_or_claim_deploy(self):
        self.grant_rows[471]['user']['login'] = 'github-actions[bot]'
        with self.assertRaisesRegex(v2.AuthorityV2Error, 'IMMUTABLE_PAIR'):
            self.accepted_grant()
        self.grant_rows[471]['user']['login'] = 'mbalbo2023'
        self.grant['deploy_owner'] = 'CODEX_RC6_UNIT'
        self.grant_rows = pair(v2.wire(self.grant).decode(), 'mbalbo2023',
            '2026-10-13T23:00:01Z')
        with self.assertRaisesRegex(v2.AuthorityV2Error, 'HUMAN_GRANT_INVALID'):
            self.accepted_grant()

    def test_edits_pair_mismatch_and_expiry_fail_closed(self):
        self.grant_rows[473]['updated_at'] = '2026-10-13T23:01:00Z'
        with self.assertRaisesRegex(v2.AuthorityV2Error, 'IMMUTABLE_PAIR'):
            self.accepted_grant()
        self.grant_rows[473]['updated_at'] = self.grant_rows[473]['created_at']
        self.grant_rows[473]['body'] += ' '
        with self.assertRaisesRegex(v2.AuthorityV2Error, 'PAIR_BODY_MISMATCH'):
            self.accepted_grant()
        self.grant_rows[473]['body'] = self.grant_rows[471]['body']
        with self.assertRaisesRegex(v2.AuthorityV2Error, 'HUMAN_GRANT_INVALID'):
            v2.verify_grant(self.grant_rows, now=at('2026-10-14T06:00:00Z'),
                source_sha='a' * 40, source_tree='b' * 40, gate='G5P-acquire',
                session='CODEX_RC6_UNIT', controller_run_id=123,
                launch_receipt_url=self.grant['launch_receipt_url'])

    def test_pulse_wrong_run_sequence_expiry_and_signature_fail_closed(self):
        grant, digest = self.accepted_grant()
        rows = self.pulse_rows(digest)
        for kwargs, reason in [({'candidate_run_id': 457}, 'FRESH_CHAINED_PULSE'),
                               ({'previous_sequence': 1}, 'PREVIOUS_CHAIN_REQUIRED'),
                               ({'now': at('2026-10-13T23:12:00Z')}, 'FRESH_CHAINED_PULSE')]:
            arguments = {'grant': grant, 'grant_sha256': digest,
                'candidate_run_id': 456, 'now': at('2026-10-13T23:11:00Z')}
            arguments.update(kwargs)
            with self.assertRaisesRegex(v2.AuthorityV2Error, reason):
                v2.verify_pulse(rows, **arguments)
        forged = self.pulse_rows(digest)
        for row in forged.values():
            body = v2.document(row['body'], v2.PULSE_KEYS)
            body['signature_hex'] = '0' * 128
            row['body'] = v2.wire(body).decode()
        with self.assertRaisesRegex(v2.AuthorityV2Error, 'SIGNATURE_INVALID'):
            v2.verify_pulse(forged, grant=grant, grant_sha256=digest,
                candidate_run_id=456, now=at('2026-10-13T23:11:00Z'))

    def test_ref_rewind_revocation_and_unverified_protection_stop(self):
        common = {'previous_head': 'd' * 40, 'current_head': 'e' * 40,
            'is_descendant': lambda old, new: True,
            'has_revocation': lambda head: False, 'protection_verified': True}
        for replacement, reason in [({'protection_verified': False}, 'PROTECTED_FRESH_REF'),
                                    ({'is_descendant': lambda old, new: False}, 'REF_REWOUND'),
                                    ({'has_revocation': lambda head: True}, 'HUMAN_REVOKED')]:
            arguments = {**common, **replacement}
            with self.assertRaisesRegex(v2.AuthorityV2Error, reason):
                v2.verify_revocation_ref(**arguments)


if __name__ == '__main__':
    unittest.main()
