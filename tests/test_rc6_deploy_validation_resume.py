"""Pure negative fixtures for validation-only continuation; never contact a host."""
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import rc6_deploy_validation_resume as resume

IMAGE='sha256:'+'a'*64
EXPECTED='sha256:'+'b'*64
MARKERS=['POROTA_FROZEN_ARTIFACT_VERIFY=GREEN',
         'RC6_FULL_CONTRACT_RECONCILIATION=GREEN',
         'POROTA_ZERO_KNOWN_ERROR_RUNTIME_AUDIT=GREEN|immediate',
         'RC6_BYMA_MORNING_REFRESH=GREEN',
         '"status": "NOT_DUE"','"reason": "BYMA_NON_OPERATIONAL_DAY"',
         'PPI_WATCH_BEFORE=',
         f'POROTA_BUILD_ONCE_PROMOTION=GREEN|expected_image={EXPECTED}|loaded_image={IMAGE}',
         '##[error]Process completed with exit code 1.']

def fake_log():
    return '\n'.join('2026-10-03T14:05:00.0000000Z '+s for s in MARKERS)+'\n'

def fixture():
    tail='''for phase in T_MINUS_45 T_MINUS_10; do
  PREOPEN_OUTPUT='{}'
  PREOPEN_RC=0
'''
    tail+='\n'.join(line[10:] for line in resume.OLD_GATE.splitlines())+'\ndone\n'
    tail+='''sudo -n python3 - "$REPO/data/deploy/CURRENT_STATE_V2.json" <<'PY'
payload={"validation_status":"VALIDATED_RUNTIME"}
import os,tempfile
print(payload)
PY
echo 'RC6_FINAL_HOUSEKEEPING=GREEN'
echo "RC6_DEPLOY_V2=GREEN|candidate=$CANDIDATE_SHA|deploy=$DEPLOY_SHA|image=$RUNTIME_IMAGE_ID|expected_image=$EXPECTED_IMAGE_ID"
REMOTE
'''
    return 'name: fixture\n'+''.join('          '+line+'\n' for line in tail.splitlines())

class ResumeGuards(unittest.TestCase):
    def test_actual_output_signature_not_echoed_commands(self):
        self.assertEqual(resume.validate_failure(fake_log()),(EXPECTED,IMAGE))
        echoed='##[group]Run shell\n'+fake_log()+'##[endgroup]\n'
        with self.assertRaises(RuntimeError):
            resume.validate_failure(echoed)

    def test_missing_failure_evidence_is_rejected(self):
        for marker in MARKERS:
            with self.assertRaises(RuntimeError):
                resume.validate_failure(fake_log().replace(marker,'REMOVED',1))

    def test_process_failure_or_later_failure_not_relabelled(self):
        for marker in ['RC6_PREOPEN_T_MINUS_45=RED','RC6_EXIT139_SOAK=GREEN']:
            with self.assertRaises(RuntimeError):
                resume.validate_failure(fake_log()+marker+'\n')

    def test_unreviewed_source_and_duplicate_anchor_are_blocking(self):
        source=fixture()
        with self.assertRaises(RuntimeError):
            resume.patch_workflow(source)
        duplicate=source+resume.OLD_GATE
        with patch.object(resume,'SUPPORTED_WORKFLOW_BLOB',resume.git_blob(duplicate)):
            with self.assertRaises(RuntimeError):resume.patch_workflow(duplicate)

    def test_exact_patch_does_not_change_other_bytes(self):
        source=fixture()
        with patch.object(resume,'SUPPORTED_WORKFLOW_BLOB',resume.git_blob(source)):
            fixed=resume.patch_workflow(source)
        self.assertEqual(fixed.replace(resume.NEW_GATE,resume.OLD_GATE),source)
        self.assertNotIn('echo "RC6_PREOPEN_${phase}=GREEN"',fixed)
        self.assertIn('--return-code "$PREOPEN_RC"',fixed)

    def test_tail_certifies_only_after_cleanup_and_does_not_repromote(self):
        source=fixture()
        config=dict(remote_dir='/tmp/porota-deploy-v2-resume-42',candidate='a'*40,tree='b'*40,
                    product='c'*40,runtime_image=IMAGE,expected_image=EXPECTED,image_tar_sha='d'*64,
                    failed_run=1,predeploy_run=2,artifact=3,digest='sha256:'+'e'*64)
        with patch.object(resume,'SUPPORTED_WORKFLOW_BLOB',resume.git_blob(source)),patch.dict(os.environ,{'GITHUB_RUN_ID':'42'}):
            script=resume.make_remote_script(source,config)
        self.assertLess(script.index('RC6_FINAL_HOUSEKEEPING'),script.index('"validation_status":"VALIDATED_RUNTIME"'))
        self.assertIn('validation_resume_run_id',script)
        self.assertIn('--baseline "$AUDIT_BASE"',script)
        self.assertNotIn('RC6_DEPLOY_V2=GREEN',script)
        for forbidden in ('docker load','docker build ','porota_mode_manager.py','docker stop porota_production_'):
            self.assertNotIn(forbidden,script)

    def test_injected_promotion_in_tail_is_rejected(self):
        source=fixture().replace('          echo \'RC6_FINAL_HOUSEKEEPING=GREEN\'','          docker load')
        config=dict(remote_dir='/tmp/x',candidate='a'*40,tree='b'*40,product='c'*40,
                    runtime_image=IMAGE,expected_image=EXPECTED,image_tar_sha='d'*64,
                    failed_run=1,predeploy_run=2,artifact=3,digest='sha256:'+'e'*64)
        with patch.object(resume,'SUPPORTED_WORKFLOW_BLOB',resume.git_blob(source)),patch.dict(os.environ,{'GITHUB_RUN_ID':'42'}):
            with self.assertRaisesRegex(RuntimeError,'REPLAY_WOULD_REPROMOTE'):
                resume.make_remote_script(source,config)

if __name__=='__main__':unittest.main()
