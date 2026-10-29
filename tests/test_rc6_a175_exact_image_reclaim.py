import copy
from datetime import datetime, timezone
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
from types import SimpleNamespace
import stat
import tempfile
import unittest
from unittest.mock import patch

from scripts import rc6_a175_artifact_provenance as artifact
from scripts import rc6_a175_exact_image_reclaim as reclaim


class ArtifactProofTest(unittest.TestCase):
    def setUp(self):
        self.target = artifact.PROVEN[0]
        self.pre = {'status': 'completed', 'conclusion': 'success',
                    'head_sha': self.target['sha'],
                    'head_commit': {'tree_id': self.target['tree']}}
        self.deploy = {'status': 'completed', 'conclusion': 'failure',
                       'head_sha': self.target['deploy_sha'],
                       'head_commit': {'tree_id': self.target['tree']}}
        self.arts = {'artifacts': [{'id': self.target['artifact'],
            'name': 'porota-predeploy-v2-' + self.target['sha'],
            'digest': self.target['digest'], 'size_in_bytes': self.target['bytes'],
            'expired': False, 'expires_at': '2026-11-04T00:03:00Z'}]}

    def test_exact_artifact_and_deploy_provenance(self):
        self.assertEqual(artifact.verify(self.pre, self.arts, self.deploy, self.target,
            now=datetime(2026, 10, 28, tzinfo=timezone.utc))['artifact_id'],
            self.target['artifact'])

    def test_tampered_digest_expired_and_wrong_deploy_tree_rejected(self):
        bad = copy.deepcopy(self.arts)
        bad['artifacts'][0]['digest'] = 'sha256:' + '0'*64
        with self.assertRaisesRegex(ValueError, 'PROVENANCE_RED'):
            artifact.verify(self.pre, bad, self.deploy, self.target)
        with self.assertRaisesRegex(ValueError, 'EXPIRED'):
            artifact.verify(self.pre, self.arts, self.deploy, self.target,
                now=datetime(2026, 11, 5, tzinfo=timezone.utc))
        bad_deploy = copy.deepcopy(self.deploy)
        bad_deploy['head_commit']['tree_id'] = '0'*40
        with self.assertRaisesRegex(ValueError, 'RUN_PROVENANCE_RED'):
            artifact.verify(self.pre, self.arts, bad_deploy, self.target)

    def test_cli_accepts_two_exact_triples_and_rejects_five_files(self):
        paths = []
        with tempfile.TemporaryDirectory() as tmp:
            for i, target in enumerate(artifact.PROVEN):
                pre = {'status': 'completed', 'conclusion': 'success',
                       'head_sha': target['sha'],
                       'head_commit': {'tree_id': target['tree']}}
                arts = {'artifacts': [{'id': target['artifact'],
                    'name': 'porota-predeploy-v2-' + target['sha'],
                    'digest': target['digest'], 'size_in_bytes': target['bytes'],
                    'expired': False, 'expires_at': '2099-11-04T00:03:00Z'}]}
                deploy = {'status': 'completed', 'conclusion': 'failure',
                          'head_sha': target['deploy_sha'],
                          'head_commit': {'tree_id': target['tree']}}
                for j, record in enumerate((pre, arts, deploy)):
                    path = Path(tmp)/f'{i}-{j}.json'
                    path.write_text(json.dumps(record))
                    paths.append(str(path))
            with redirect_stdout(io.StringIO()) as out:
                artifact.main(paths)
            self.assertEqual(json.loads(out.getvalue())['status'], 'GREEN')
            with self.assertRaises(SystemExit):
                artifact.main(paths[:-1])


class ExactImageAdmissionTest(unittest.TestCase):
    def test_only_two_exact_tags_and_two_matching_digest_repositories(self):
        sha = reclaim.TARGETS[0][0]
        tags = sorted(reclaim.exact_tag_set(sha))
        digests = ['porota-predeploy-v2@sha256:' + '1'*64,
                   'porota-trading-bot@sha256:' + '2'*64]
        self.assertEqual(reclaim.exact_target_aliases(sha, tags, digests, initial=True),
                         set(tags))
        for bad_tags, bad_digests in (
            (tags + ['external/repo:latest'], digests),
            (tags, digests + ['external/repo@sha256:' + '3'*64]),
            (tags, digests[:1]),
        ):
            with self.assertRaisesRegex(reclaim.Rejected, 'DRIFT'):
                reclaim.exact_target_aliases(sha, bad_tags, bad_digests, initial=True)

    def test_two_targets_can_share_all_eight_nonactive_diffids(self):
        shared = ['sha256:' + str(i)*64 for i in range(1, 6)]
        private = ['sha256:' + format(i, 'x')*64 for i in range(6, 14)]
        layers = {reclaim.ACTIVE: shared*3 + private[:0],
                  reclaim.CRITICAL: shared*3,
                  reclaim.TARGETS[0][1]: shared*3 + private + private[:4],
                  reclaim.TARGETS[1][1]: shared*3 + private + private[:4]}
        self.assertEqual(len(layers[reclaim.TARGETS[0][1]]), 27)
        with patch.object(reclaim, 'image_record', side_effect=lambda i:([], [], layers[i])):
            reclaim.initial_layers(set(layers))

    def test_new_stopped_container_blocks_before_deletion(self):
        ids = sorted(reclaim.OWNED) + ['f'*64]
        with patch.object(reclaim, 'read_barrier_absent'), \
             patch.object(reclaim, 'paper_mode'), \
             patch.object(reclaim, 'sealed_state'), \
             patch.object(reclaim, 'docker', return_value='\n'.join(ids)):
            with self.assertRaisesRegex(reclaim.Rejected, 'CONTAINER_SET_CHANGED'):
                reclaim.protected_state({reclaim.ACTIVE, reclaim.CRITICAL})

    def test_exact_digest_continuation_and_no_force_or_prune(self):
        state = {}
        for sha, iid in reclaim.TARGETS:
            state[iid] = {'tags': set(reclaim.exact_tag_set(sha)),
                          'digests': {'porota-predeploy-v2@sha256:' + sha + '0'*24,
                                      'porota-trading-bot@sha256:' + sha + '1'*24}}
        state[reclaim.ACTIVE] = {'tags': {reclaim.STABLE} |
            reclaim.exact_tag_set(reclaim.A175_SHA),
            'digests': set()}
        state[reclaim.CRITICAL] = {'tags': set(), 'digests': set()}
        calls = []
        free = [4_414_152_704]

        def docker(*args):
            calls.append(args)
            self.assertEqual(args[:3], ('image', 'rm', '--no-prune'))
            target = args[-1]
            iid = next((i for i, row in state.items() if target in row['tags']
                        or target in row['digests'] or i == target), None)
            self.assertIsNotNone(iid)
            if target == iid:
                self.assertFalse(state[iid]['tags'] or state[iid]['digests'])
                del state[iid]
                free[0] += 700_000_000
            elif target in state[iid]['tags']:
                state[iid]['tags'].remove(target)
            else:
                state[iid]['digests'].remove(target)
        def image_record(iid):
            row = state[iid]
            return sorted(row['tags']), sorted(row['digests']), []
        def protected_state(expected):
            self.assertEqual(set(state), expected)
            return True

        with patch.object(reclaim, 'metadata', return_value={'id':'594077619','region':'nyc1'}), \
             patch.object(reclaim.os, 'open', return_value=3), \
             patch.object(reclaim.os, 'fstat', return_value=SimpleNamespace(
                 st_mode=stat.S_IFREG|0o600, st_uid=0, st_nlink=1)), \
             patch.object(reclaim.os, 'close'), \
             patch.object(reclaim.fcntl, 'flock'), \
             patch.object(reclaim, 'protected_state', side_effect=protected_state), \
             patch.object(reclaim, 'initial_layers'), \
             patch.object(reclaim, 'image_record', side_effect=image_record), \
             patch.object(reclaim, 'current_image_ids', side_effect=lambda:set(state)), \
             patch.object(reclaim, 'docker', side_effect=docker), \
             patch.object(reclaim.shutil, 'disk_usage', side_effect=lambda _:
                 SimpleNamespace(free=free[0])):
            report = reclaim.reclaim({})
        self.assertEqual(report['status'], 'GREEN')
        self.assertEqual(len(report['removed_tags']), 4)
        self.assertEqual(report['removed_digests_count'], 4)
        self.assertEqual(len(report['removed_image_ids']), 2)
        self.assertEqual(report['observed_delta_bytes'], 1_400_000_000)
        self.assertEqual(set(state), {reclaim.ACTIVE, reclaim.CRITICAL})
        self.assertTrue(all('--force' not in args and 'prune' not in args for args in calls))

    def test_shared_digest_alias_blocks_before_any_mutation(self):
        ids={reclaim.ACTIVE,reclaim.CRITICAL,*[iid for _,iid in reclaim.TARGETS]}
        rows={iid:([], [], []) for iid in ids}
        rows[reclaim.TARGETS[0][1]]=([],['porota-predeploy-v2@sha256:'+'a'*64],[])
        rows[reclaim.ACTIVE]=( [],['porota-predeploy-v2@sha256:'+'a'*64],[])
        with patch.object(reclaim,'image_record',side_effect=lambda iid:rows[iid]):
            with self.assertRaisesRegex(reclaim.Rejected,'TARGET_SHARED_DIGEST_ALIAS'):
                reclaim.no_shared_digest_aliases(ids)


if __name__ == '__main__':
    unittest.main()
