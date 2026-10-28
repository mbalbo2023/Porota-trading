import unittest
from unittest.mock import patch

from scripts import rc6_a172_docker_ownership_ro as audit


def image(*, tags, digests=(), layers=('sha256:' + '1' * 64,), size=800_000_000):
    return {'tags': list(tags), 'digests': list(digests), 'layers': list(layers), 'size': size}


class A172DockerOwnershipTest(unittest.TestCase):
    def setUp(self):
        self.old = 'sha256:' + 'a' * 64
        self.critical = 'sha256:' + 'c' * 64
        self.shared = 'sha256:' + '1' * 64
        self.unique = 'sha256:' + '2' * 64
        self.containers = {
            audit.A171_CONTAINER_IDS['/porota_production_observer']:
                (audit.A171_IMAGE, '/porota_production_observer', 'running', True),
            audit.A171_CONTAINER_IDS['/porota_production_dashboard']:
                (audit.A171_IMAGE, '/porota_production_dashboard', 'running', True),
            '3' * 64: (self.critical, '/porota_critical_approval_rc6', 'running', True),
        }
        self.images = {
            audit.A171_IMAGE: image(tags=(audit.STABLE, *sorted(audit.OWNED_TAGS)),
                                     layers=(self.shared, self.unique)),
            self.critical: image(tags=('porota-critical-approval:rc6',), layers=(self.shared,)),
            self.old: image(tags=('porota-predeploy-v2:' + 'a' * 40,),
                            digests=('porota-predeploy-v2@sha256:' + '9' * 64,),
                            layers=(self.shared,)),
        }

    def snapshot(self):
        with patch.object(audit, 'inspect_containers', return_value=self.containers), \
             patch.object(audit, 'inspect_images', return_value=self.images):
            return audit.snapshot()

    def test_a171_is_protected_even_with_two_exact_candidate_aliases(self):
        rows = {row['image_id']: row for row in self.snapshot()['images']}
        self.assertEqual(rows[audit.A171_IMAGE]['all_container_refs'], 2)
        self.assertEqual(rows[audit.A171_IMAGE]['cleanup_class'], 'PROTECTED_REFERENCED')
        self.assertEqual(rows[audit.A171_IMAGE]['aliases']['exact_a171_alias_count'], 2)
        self.assertEqual(rows[self.old]['cleanup_class'], 'REQUIRES_EXACT_PROVENANCE_REVIEW')
        self.assertEqual(rows[self.old]['layers_shared_with_referenced_count'], 1)
        self.assertEqual(rows[self.old]['unique_diffid_count'], 0)

    def test_unknown_alias_disqualifies_old_image(self):
        self.images[self.old]['tags'].append('foreign-registry/owner:latest')
        row = {row['image_id']: row for row in self.snapshot()['images']}[self.old]
        self.assertEqual(row['cleanup_class'], 'PROTECTED_OTHER_ALIAS')
        self.assertEqual(row['aliases']['other_alias_count'], 1)
        self.assertNotIn('foreign-registry', str(row))

    def test_unknown_digest_disqualifies_old_image(self):
        self.images[self.old]['digests'].append('another-owner@sha256:' + '3' * 64)
        row = {row['image_id']: row for row in self.snapshot()['images']}[self.old]
        self.assertEqual(row['cleanup_class'], 'PROTECTED_OTHER_ALIAS')
        self.assertEqual(row['aliases']['other_digest_count'], 1)
        self.assertNotIn('another-owner', str(row))

    def test_stopped_foreign_container_is_still_an_owner(self):
        self.containers['4' * 64] = (self.old, '/another-service', 'exited', False)
        row = {row['image_id']: row for row in self.snapshot()['images']}[self.old]
        self.assertEqual(row['cleanup_class'], 'PROTECTED_REFERENCED')
        self.assertEqual(row['all_container_refs'], 1)
        self.assertEqual(self.snapshot()['other_container_count'], 1)

    def test_missing_exact_runtime_is_unverified(self):
        self.containers[audit.A171_CONTAINER_IDS['/porota_production_observer']] = \
            (self.old, '/porota_production_observer', 'running', True)
        with self.assertRaisesRegex(audit.Unverified, 'A171_RUNTIME_IDENTITY_DRIFT'):
            self.snapshot()

    def test_complete_inventory_cannot_hide_missing_image(self):
        del self.images[self.critical]
        with self.assertRaisesRegex(audit.Unverified, 'CONTAINER_IMAGE_MISSING'):
            self.snapshot()

    def test_repeated_diffid_keeps_image_ownership_but_counts_each_layer(self):
        self.images[self.old]['layers'] = [self.shared, self.shared]
        row = {row['image_id']: row for row in self.snapshot()['images']}[self.old]
        self.assertEqual(row['layer_count'], 2)
        self.assertEqual(row['distinct_layer_count'], 1)
        self.assertEqual(row['layers_shared_with_referenced_count'], 1)


if __name__ == '__main__':
    unittest.main()
