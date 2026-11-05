"""A690 OFFHOST regression: OCI proof is mandatory for shared-host image cleanup.

These tests never invoke Docker, SSH, PPI, the Droplet, or a deploy.
An authentic image still requires the runtime/reference guards in the caller.
"""
import unittest

from scripts.rc6_offline_current_image_cleanup import _require_candidate_oci


COMMIT = 'a' * 40


def sample(labels=None):
    return {
        'Id': 'sha256:' + '1' * 64,
        'RepoTags': ['porota-predeploy-v2:' + COMMIT],
        'RepoDigests': [],
        'Config': {'Labels': labels},
    }


class TestA690OfflineOCIProvenance(unittest.TestCase):
    def test_exact_candidate_commit_labels_accepted_as_one_guard(self):
        valid = {'porota.predeploy': 'v2', 'porota.commit': COMMIT}
        self.assertIsNone(_require_candidate_oci(sample(valid), COMMIT))
        # The helper is one necessary guard, not by itself release authority.

    def test_missing_labels_never_grant_ownership(self):
        for raw in (None, {}, [], False, 'porota', {'porota.commit': COMMIT},
                    {'porota.predeploy': 'v2'}):
            with self.subTest(raw=raw):
                with self.assertRaisesRegex(ValueError, 'OCI_OWNERSHIP_UNVERIFIED'):
                    _require_candidate_oci(sample(raw), COMMIT)

    def test_foreign_or_contradictory_oci_labels_rejected(self):
        for raw in (
            {'porota.predeploy': 'v1', 'porota.commit': COMMIT},
            {'porota.predeploy': 'v2', 'porota.commit': 'b' * 40},
            {'porota.predeploy': 'v2', 'porota.commit': COMMIT, 'foreign': True},
        ):
            with self.subTest(raw=raw):
                with self.assertRaisesRegex(ValueError, 'OCI_OWNERSHIP_UNVERIFIED'):
                    _require_candidate_oci(sample(raw), COMMIT)

    def test_expected_commit_must_be_exact_lowercase_sha40(self):
        valid = {'porota.predeploy': 'v2', 'porota.commit': COMMIT}
        for invalid in ('', COMMIT[:-1], COMMIT.upper(), 'f' * 64, None):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ValueError, 'OCI_OWNERSHIP_UNVERIFIED'):
                    _require_candidate_oci(sample(valid), invalid)

    def test_malformed_alias_or_digest_inventory_rejected(self):
        valid = {'porota.predeploy': 'v2', 'porota.commit': COMMIT}
        for patch in ({'RepoTags': 'porota-predeploy-v2:' + COMMIT},
                      {'RepoTags': []}, {'RepoDigests': ''},
                      {'RepoDigests': False},
                      {'RepoDigests': ['boca:unknown', True]}):
            with self.subTest(patch=patch):
                doc = {**sample(valid), **patch}
                with self.assertRaisesRegex(ValueError, 'OCI_OWNERSHIP_UNVERIFIED'):
                    _require_candidate_oci(doc, COMMIT)

    def test_malformed_docker_inventory_rejected(self):
        for doc in (None, [], 'fake', {'Config': None},
                    {'Config': ['foreign']},
                    {'Config': {'Labels': 'not-a-dict'}}):
            with self.subTest(doc=doc):
                with self.assertRaisesRegex(ValueError, 'OCI_OWNERSHIP_UNVERIFIED'):
                    _require_candidate_oci(doc, COMMIT)


if __name__ == '__main__':
    unittest.main()
