"""Hermetic failure-path checks for A155's exact, no-force image cleanup."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import rc6_a155_exact_image_reclaim as cleanup


class DockerFixture:
    def __init__(self):
        self.iid, self.tags, self.repos, self.digests = cleanup.TARGETS[0]
        self.tags = list(self.tags)
        self.layers = tuple(f'protected-{i}' for i in range(5)) + ('old-unique',)
        self.present = True
        self.calls = []
        self.refs = {'observer': ('container-id', cleanup.ACTIVE_IMAGES['porota_production_observer'], 'running', '0')}
        self.protected_snapshot = (dict(self.refs), {'active-image': tuple(f'protected-{i}' for i in range(5))})

    def containers(self):
        return dict(self.refs)

    def image(self, iid):
        if iid != self.iid or not self.present:
            return None
        return {'tags': list(self.tags), 'digest_repos': set(self.repos),
                'digest_count': self.digests, 'layers': self.layers}

    def command(self, args, **kwargs):
        self.calls.append(tuple(args))
        if args[:4] != ['docker', 'image', 'rm', '--no-prune']:
            raise AssertionError('unexpected Docker mutation')
        if args[4] == self.iid:
            if self.tags:
                raise AssertionError('ID deletion while tags remain')
            self.present = False
        else:
            self.tags.remove(args[4])
            # Deliberately keep digest-only image after the last tag.
        return ''

    def patches(self):
        return (patch.object(cleanup, 'containers', self.containers),
                patch.object(cleanup, 'image', self.image),
                patch.object(cleanup, 'protected', lambda: self.protected_snapshot),
                patch.object(cleanup, 'cmd', self.command),
                patch.object(cleanup, 'free', lambda: {'bytes': 4_000_000_000, 'free_inodes': 2_000_000}))


class ExactImageReclaimTests(unittest.TestCase):
    def exercise(self, fixture, target=None):
        patches = fixture.patches()
        for p in patches:
            p.start()
        try:
            receipt = {'removed_tags': [], 'removed_image_ids': [], 'checks': []}
            cleanup.reclaim_target(target or cleanup.TARGETS[0], fixture.protected_snapshot,
                                   set(fixture.protected_snapshot[1]['active-image']), receipt)
            return receipt
        finally:
            for p in reversed(patches):
                p.stop()

    def test_two_tags_then_digest_only_id_removed_without_force(self):
        f = DockerFixture()
        receipt = self.exercise(f)
        self.assertFalse(f.present)
        self.assertEqual(receipt['removed_tags'], list(cleanup.TARGETS[0][1]))
        self.assertEqual(receipt['removed_image_ids'], [f.iid])
        self.assertEqual(len(f.calls), 3)
        self.assertTrue(all('--force' not in call for call in f.calls))

    def test_external_alias_blocks_before_any_mutation(self):
        f = DockerFixture()
        f.tags.append('unrelated-owner:do-not-touch')
        with self.assertRaisesRegex(RuntimeError, 'TARGET_ALIAS_OR_LAYER_DRIFT'):
            self.exercise(f)
        self.assertEqual(f.calls, [])

    def test_new_container_reference_blocks_before_any_mutation(self):
        f = DockerFixture()
        f.refs['new'] = ('new-container', f.iid, 'exited', '0')
        # The independent all-container reference check catches even exited refs.
        with self.assertRaisesRegex(RuntimeError, 'TARGET_REFERENCED_BY_CONTAINER'):
            self.exercise(f)
        self.assertEqual(f.calls, [])

    def test_missing_image_id_blocks_before_any_mutation(self):
        f = DockerFixture()
        f.present = False
        with self.assertRaisesRegex(RuntimeError, 'TARGET_ALIAS_OR_LAYER_DRIFT'):
            self.exercise(f)
        self.assertEqual(f.calls, [])

    def test_barrier_inflight_rejected_and_idle_read_without_write(self):
        with tempfile.TemporaryDirectory() as d:
            lock = Path(d) / 'porota-rc6-deploy-v2.lock'
            barrier = Path(d) / 'porota-rc6-deploy-v2-attempt.json'
            lock.write_bytes(b'')
            lock.chmod(0o600)
            barrier.write_text(json.dumps({'status': 'INFLIGHT'}))
            barrier.chmod(0o600)
            with patch.object(cleanup, 'LOCK', lock), patch.object(cleanup, 'BARRIER', barrier):
                before = barrier.read_bytes()
                with self.assertRaisesRegex(RuntimeError, 'BARRIER_ACTIVE'):
                    cleanup.lock_existing(trusted_uid=os.getuid())
                self.assertEqual(barrier.read_bytes(), before)
                barrier.write_text(json.dumps({'status': 'IDLE'}))
                before = barrier.read_bytes()
                fd = cleanup.lock_existing(trusted_uid=os.getuid())
                os.close(fd)
                self.assertEqual(barrier.read_bytes(), before)

    def test_stale_container_id_prefix_is_rejected(self):
        cid = 'deadbeefdead' + '0' * 52
        others = ['a854c6930cdd' + '0' * 52, '0d566777f5dc' + '0' * 52]
        name = 'porota_production_observer'
        raw = '|'.join((cid, '/' + name, cleanup.ACTIVE_IMAGES[name], 'running', '0'))
        with patch.object(cleanup, 'cmd', side_effect=['\n'.join([cid, *others]), raw]):
            with self.assertRaisesRegex(RuntimeError, 'ACTIVE_CONTAINER_IDENTITY_RED'):
                cleanup.containers()


if __name__ == '__main__':
    unittest.main()
