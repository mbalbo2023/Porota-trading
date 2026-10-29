"""Adversarial ownership and durable provenance for A175 postfailure audit."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import rc6_a175_docker_ownership_ro as audit


def image(sha, *, stable=False, tags=None, digests=None, layers=None):
    tags = tags if tags is not None else [
        'porota-predeploy-v2:'+sha,
        'porota-trading-bot:17.0.0-rc6-candidate-'+sha]
    return dict(tags=([audit.STABLE] if stable else [])+tags,
                digests=digests if digests is not None else [
                    'porota-predeploy-v2@sha256:'+'b'*64,
                    'porota-trading-bot@sha256:'+'c'*64],
                layers=layers if layers is not None else [
                    'sha256:'+'1'*64,'sha256:'+'2'*64],size=800_000_000)


class OwnershipTests(unittest.TestCase):
    def setUp(self):
        self.dashboard='d'*64
        self.critical='c'*64
        self.containers={
            audit.A175_OBSERVER_ID:(audit.A175_IMAGE,'/porota_production_observer','running',True),
            self.dashboard:(audit.A175_IMAGE,'/porota_production_dashboard','running',True),
            self.critical:(audit.A171_IMAGE,'/porota_critical_approval_rc6','running',True),
        }
        self.images={
            audit.A175_IMAGE:image(audit.A175_CANDIDATE,stable=True),
            audit.A174_IMAGE:image(audit.A174_CANDIDATE,digests=[
                'porota-predeploy-v2@sha256:'+'a'*64,
                'porota-trading-bot@sha256:'+'d'*64],
                layers=['sha256:'+'1'*64,'sha256:'+'3'*64]),
            audit.A171_IMAGE:image(audit.A171_CANDIDATE,digests=[
                'porota-predeploy-v2@sha256:'+'e'*64,
                'porota-trading-bot@sha256:'+'f'*64]),
        }

    def rows(self):
        with patch.object(audit,'inspect_containers',return_value=self.containers),\
             patch.object(audit,'inspect_images',return_value=self.images):
            return audit.snapshot()

    def test_all_container_refs_protect_a175_and_critical_a171(self):
        report=self.rows()
        rows={r['image_id']:r for r in report['images']}
        self.assertEqual(rows[audit.A175_IMAGE]['all_container_refs'],2)
        self.assertEqual(rows[audit.A171_IMAGE]['ref_container_ids'],[self.critical])
        self.assertEqual(rows[audit.A171_IMAGE]['cleanup_class'],'PROTECTED_REFERENCED')
        self.assertEqual(rows[audit.A175_IMAGE]['cleanup_class'],'PROTECTED_REFERENCED')
        self.assertEqual(report['all_containers'][self.critical]['image_id'],audit.A171_IMAGE)
        self.assertEqual(rows[audit.A174_IMAGE]['cleanup_class'],'CANDIDATE_ONLY_REVIEW_PROVENANCE')
        self.assertEqual(rows[audit.A174_IMAGE]['unique_diffid_count'],1)

    def test_old_image_external_alias_digest_or_stable_blocks_review(self):
        for kind,token in (('tags','foreign/owner:latest'),
                           ('digests','foreign/owner@sha256:'+'a'*64),
                           ('tags',audit.STABLE)):
            self.images[audit.A174_IMAGE][kind].append(token)
            with self.subTest(kind=kind,token=token):
                rows={r['image_id']:r for r in self.rows()['images']}
                self.assertNotEqual(rows[audit.A174_IMAGE]['cleanup_class'],
                                    'CANDIDATE_ONLY_REVIEW_PROVENANCE')
                self.assertNotIn('foreign/owner',str(rows))
            self.images[audit.A174_IMAGE][kind].remove(token)

    def test_shared_digest_and_unpaired_rc6_repo_veto(self):
        digest=self.images[audit.A174_IMAGE]['digests'][0]
        self.images[audit.A171_IMAGE]['digests'].append(digest)
        row={r['image_id']:r for r in self.rows()['images']}[audit.A174_IMAGE]
        self.assertEqual(row['aliases']['shared_digest_alias_count'],1)
        self.assertEqual(row['cleanup_class'],'PROTECTED_OTHER_ALIAS')
        self.images[audit.A171_IMAGE]['digests'].remove(digest)
        self.images[audit.A174_IMAGE]['tags']=['porota-predeploy-v2:'+audit.A174_CANDIDATE]
        row={r['image_id']:r for r in self.rows()['images']}[audit.A174_IMAGE]
        self.assertFalse(row['aliases']['digest_repositories_paired_with_tags'])
        self.assertEqual(row['cleanup_class'],'PROTECTED_OTHER_ALIAS')

    def test_stopped_container_and_new_reference_veto(self):
        self.containers['e'*64]=(audit.A174_IMAGE,'/unrelated-service','exited',False)
        row={r['image_id']:r for r in self.rows()['images']}[audit.A174_IMAGE]
        self.assertEqual(row['cleanup_class'],'PROTECTED_REFERENCED')
        self.assertEqual(row['ref_container_ids'],['e'*64])
        self.assertEqual(self.rows()['all_containers']['e'*64]['owner'],'OTHER')

    def test_missing_or_drifted_image_or_container_fails_closed(self):
        del self.images[audit.A171_IMAGE]
        with self.assertRaisesRegex(audit.Unverified,'CONTAINER_IMAGE_MISSING'):
            self.rows()
        self.images[audit.A171_IMAGE]=image(audit.A171_CANDIDATE)
        self.containers[audit.A175_OBSERVER_ID]=(audit.A174_IMAGE,
            '/porota_production_observer','running',True)
        with self.assertRaisesRegex(audit.Unverified,'A175_RUNTIME_IDENTITY_DRIFT'):
            self.rows()

    def test_sealed_receipt_rejects_symlink_and_duplicate_hardlink(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'receipt';path.write_text('{}');path.chmod(0o600)
            link=Path(d)/'alias';link.symlink_to(path)
            with patch.object(audit.os,'fstat',side_effect=lambda fd: type('S',(),{
                    'st_mode':0o100600,'st_nlink':1,'st_uid':0,'st_size':2})()):
                with self.assertRaises(OSError): audit.sealed_json(link,4096)
            os.link(path,Path(d)/'hardlink')
            with self.assertRaisesRegex(audit.Unverified,'DURABLE_RECEIPT_PATH_UNSAFE'):
                audit.sealed_json(path,4096)

    def test_stage_absence_and_foreign_file_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'stage'
            with patch.object(audit,'STAGING',path):
                self.assertEqual(audit.stage_state(),{'present':False})
                path.write_text('foreign')
                with self.assertRaisesRegex(audit.Unverified,'A175_STAGING_TYPE_CHANGED'):
                    audit.stage_state()


if __name__=='__main__': unittest.main()
