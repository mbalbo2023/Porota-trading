"""Local, offline regressions for the PAPER V3 material path."""
import unittest
import copy
from datetime import date, datetime, timedelta, timezone
from email.message import Message
import hashlib
import io
import gzip
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import urllib.error
from contextlib import nullcontext, redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

from scripts.rc6_material_horizon_producer import require_paper_v3_archive_policy
from scripts.rc6_g5p_frames import FrameRecorder, verify_frames, wire, capacity
from scripts.rc6_g5p_model import build_model, model_wire
from scripts.rc6_g5p_evidence import verify_material
from rc6_shadow_runtime.archive_physical_model import horizon_schedule
from rc6_shadow_runtime.archive_components import MEMBERS
from rc6_shadow_runtime.archive_components import require_native_v4_write_capability
from rc6_shadow_runtime.archive_format_contract import (
    SOURCE_MEMBERS, writer_codec_restorer_binding)
from scripts import rc6_architectural_gates as gates
from scripts.porota_validate_deploy_artifact import validate as validate_artifact
from scripts.porota_predeploy_binding import BindingRejected, verify_g8_report
from scripts import rc6_material_carrier as carrier
from scripts import rc6_material_pr_admission as admission
from scripts import rc6_contract_revisions as revisions
from scripts.rc6_g5p_model import DOCUMENT_SHA256
from scripts import rc6_owned_gate_lease as lease
from scripts import rc6_material_derive as derive
from scripts import rc6_g5p_reference as reference
from scripts import rc6_heavy_test_preflight as physical_capacity
from scripts import porota_predeploy_cleanup as cleanup


class G5PBootstrapCapacityTests(unittest.TestCase):
    def test_cheap_owner_reads_share_server_revalidated_cache_and_keep_403_red(self):
        path='/issues/471/comments?since=2026-10-12T00%3A00%3A00Z&page=1'
        headers=Message();headers['ETag']='W/"cheap-owner-v1"'
        class Response:
            status=200
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def __init__(self):self.headers=headers
            def read(self,size):return b'[{"id":1}]'
        missing=urllib.error.HTTPError('https://api.github.com/private',304,
                                       'not modified',Message(),None)
        forbidden=urllib.error.HTTPError('https://api.github.com/private',403,
                                         'rate limit',Message(),None)
        replies=iter((Response(),missing,forbidden));requests=[]
        def request(value,timeout):
            requests.append(value.get_header('If-none-match'))
            item=next(replies)
            if isinstance(item,BaseException):raise item
            return item
        with admission._HTTP_REVALIDATION_LOCK:
            prior=admission._HTTP_REVALIDATION_CACHE.copy()
            prior_bytes=admission._HTTP_REVALIDATION_BYTES
            admission._HTTP_REVALIDATION_CACHE.clear()
            admission._HTTP_REVALIDATION_BYTES=0
        try:
            with patch.dict(os.environ,{'GH_TOKEN':'local-test-token','RC6_GATE':'cheap'}), \
                 patch.object(admission.urllib.request,'urlopen',side_effect=request):
                self.assertEqual(admission.api(path),[{'id':1}])
                self.assertEqual(lease.api_get(path,time.monotonic()+10),[{'id':1}])
                with self.assertRaises(urllib.error.HTTPError) as red:
                    admission.api(path)
                self.assertEqual(red.exception.code,403)
            self.assertEqual(requests,[None,'W/"cheap-owner-v1"','W/"cheap-owner-v1"'])
        finally:
            with admission._HTTP_REVALIDATION_LOCK:
                admission._HTTP_REVALIDATION_CACHE.clear()
                admission._HTTP_REVALIDATION_CACHE.update(prior)
                admission._HTTP_REVALIDATION_BYTES=prior_bytes

    def test_post_fin_native_red_names_only_bounded_failed_nodes_and_literal(self):
        with tempfile.TemporaryDirectory() as temporary:
            log=Path(temporary)/'native.log'
            log.write_text('https://private.example/?token=SECRET\n'
                'FAILED tests/test_focal.py::TestGate::test_contract[param-secret] - SECRET detail\n'
                'FAILED tests/test_focal.py::TestGate::test_contract[other] - duplicate\n'
                'ValueError: EXACT_NATIVE_FAILURE:SECRET detail\n')
            summary=carrier.native_red_log_index(log,{'returncode':1,'timed_out':False,
                'supervisor_errors':[],'residual_descendants_observed':[],
                'adopted_descendants_reaped':[]},'focal311-execution')
            self.assertEqual(summary['failed_test_nodes'],
                             ['tests/test_focal.py::TestGate::test_contract'])
            self.assertEqual(summary['literal_exception'],
                             {'class':'ValueError','reason':'EXACT_NATIVE_FAILURE'})
            self.assertEqual(summary['native_exit_code'],1)
            self.assertEqual(summary['failed_test_node_count'],1)
            self.assertEqual(summary['real_orders_sent'],0)
            self.assertNotIn('SECRET',json.dumps(summary))
            self.assertNotIn('private.example',json.dumps(summary))

    def test_post_fin_native_red_index_counts_beyond_bounded_node_sample(self):
        with tempfile.TemporaryDirectory() as temporary:
            log=Path(temporary)/'native.log'
            log.write_text(''.join(f'FAILED tests/test_focal.py::test_case_{index} - SECRET\n'
                                   for index in range(40)))
            summary=carrier.native_red_log_index(log,{'returncode':1,'timed_out':False,
                'supervisor_errors':[],'residual_descendants_observed':[],
                'adopted_descendants_reaped':[]},'focal311-execution')
            self.assertEqual(summary['failed_test_node_count'],40)
            self.assertEqual(len(summary['failed_test_nodes']),32)
            self.assertNotIn('SECRET',json.dumps(summary))

    def test_admission_http_failure_exposes_status_and_numeric_limits_only(self):
        headers=Message()
        headers['X-RateLimit-Remaining']='0'
        headers['X-RateLimit-Reset']='1791753600'
        headers['Retry-After']='secret retry value'
        error=urllib.error.HTTPError('https://api.github.com/private?token=SECRET',403,
                                      'SECRET response',headers,None)
        observation=admission.sanitized_http_failure(error)
        self.assertEqual(observation,{'http_status':403,
            'http_limit_counters':{'rate_limit_remaining':0,
                                   'rate_limit_reset_epoch':1791753600},
            'transport_cause_class':None})
        self.assertNotIn('SECRET',json.dumps(observation))
        self.assertEqual(carrier.sanitized_carrier_http_failure(error),observation)
        self.assertEqual(admission.sanitized_http_failure(
            urllib.error.URLError(TimeoutError('SECRET'))),
            {'http_status':None,'http_limit_counters':{},'transport_cause_class':'TimeoutError'})
        self.assertEqual(carrier.sanitized_carrier_http_failure(
            urllib.error.URLError(TimeoutError('SECRET'))),
            {'http_status':None,'http_limit_counters':{},'transport_cause_class':'TimeoutError'})

    def test_g5p_owner_reads_revalidate_etag_and_never_reuse_on_403(self):
        path='/issues/471/comments?since=2026-10-11T00%3A00%3A00Z&page=1'
        requests=[]
        class Response:
            status=200
            def __init__(self,body,etag):
                self.body=body
                self.headers=Message()
                self.headers['ETag']=etag
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def read(self,size):return self.body
        first=Response(b'{"body":"RELEASED=false"}', 'W/"owner-v1"')
        changed=Response(b'{"body":"RELEASED=true"}', 'W/"owner-v2"')
        not_modified=urllib.error.HTTPError('https://api.github.com/private',304,
            'Not Modified',Message(),None)
        forbidden=urllib.error.HTTPError('https://api.github.com/private',403,
            'rate limit',Message(),None)
        replies=iter((first,not_modified,not_modified,changed,not_modified,
                      forbidden,not_modified))
        def request(request,timeout):
            requests.append(request.get_header('If-none-match'))
            response=next(replies)
            if isinstance(response,BaseException):raise response
            return response
        with admission._HTTP_REVALIDATION_LOCK:
            original_cache=admission._HTTP_REVALIDATION_CACHE.copy()
            original_bytes=admission._HTTP_REVALIDATION_BYTES
            admission._HTTP_REVALIDATION_CACHE.clear()
            admission._HTTP_REVALIDATION_BYTES=0
        try:
            with patch.dict(os.environ,{'GH_TOKEN':'local-test-token','RC6_GATE':'G5P-acquire'}), \
                 patch.object(admission.urllib.request,'urlopen',side_effect=request):
                self.assertEqual(admission.api(path)['body'],'RELEASED=false')
                self.assertEqual(admission.api(path)['body'],'RELEASED=false')
                self.assertEqual(lease.api_get(path,time.monotonic()+10)['body'],'RELEASED=false')
                self.assertEqual(lease.api_get(path,time.monotonic()+10)['body'],'RELEASED=true')
                self.assertEqual(admission.api(path)['body'],'RELEASED=true')
                with self.assertRaises(urllib.error.HTTPError) as red:
                    lease.api_get(path,time.monotonic()+10)
                self.assertEqual(red.exception.code,403)
                with self.assertRaisesRegex(lease.LeaseControlFailure,
                                            'OWNED_LEASE_304_WITHOUT_VERIFIED_CACHE'):
                    lease.api_get('/issues/473/comments?page=1',time.monotonic()+10)
            self.assertEqual(requests,[None,'W/"owner-v1"','W/"owner-v1"',
                                       'W/"owner-v1"','W/"owner-v2"',
                                       'W/"owner-v2"',None])
        finally:
            with admission._HTTP_REVALIDATION_LOCK:
                admission._HTTP_REVALIDATION_CACHE.clear()
                admission._HTTP_REVALIDATION_CACHE.update(original_cache)
                admission._HTTP_REVALIDATION_BYTES=original_bytes

    def test_post_handoff_source_evolution_accounts_for_every_20261011_gap(self):
        matrix=json.loads((Path(__file__).resolve().parents[1]/
            'docs/audits/convergence/REQUIREMENT_CLOSURE_MATRIX.json').read_bytes())
        known_unexplained=""".dockerignore
.github/actions/rc6-g5a-upload-parts/action.yml
.github/workflows/rc6-current-host-contract-evidence-readonly-audit-20260910.yml
cg_paper_workspace.py
docs/governance/RC6_ARCHITECTURAL_DEVELOPMENT_RECEIPTS_20261008.json
docs/governance/RC6_ARCHITECTURAL_RCA_20261008.md
docs/governance/RC6_CAPACITY_PROBE_RCA_37823993377.json
docs/governance/RC6_CAPACITY_PROBE_RCA_37830339583.json
docs/governance/RC6_CAPACITY_PROBE_RCA_37845108215.json
docs/governance/RC6_ERROR_REGISTRY.md
docs/governance/RC6_G0_G5_CONTRACT_PROPOSAL_2026-10-10.md
docs/governance/RC6_G5A_INTEGRATION_STATUS_2026-10-11.md
docs/governance/RC6_G5A_ROOT_QUOTA_FIN_CLOSURE_2026-10-11.md
docs/governance/RC6_G5A_RUNNER_RCA_AND_HANDOFF_2026-10-11.md
docs/governance/RC6_G5A_STATIC_WRITER_SITES_2026-10-11.csv
docs/governance/RC6_HORIZON_CAPACITY_ASSESSMENT_20261008.json
docs/governance/RC6_OWNER_RECEIPT_INCIDENT_20261008.json
docs/governance/RC6_PRIVILEGED_SIGNAL_CUSTODY_ASSESSMENT_20261008.json
docs/governance/RC6_SIGNED_MODULE_AVAILABILITY_20261008.json
docs/governance/RC6_WIP_DURABILITY_20261008.json
docs/governance/rc6-architecture-closure-20261009/ECONOMIC_VALIDATION.json
docs/governance/rc6-architecture-closure-20261009/ERROR_MATRIX.md
docs/governance/rc6-architecture-closure-20261009/RUNNER_DECISION.md
docs/governance/rc6-architecture-closure-20261009/ZERO_COST_CONTRACT.md
ops/policy/rc6-actions-custody-20261009.json
ops/policy/rc6-development-checks-v1.json
ops/policy/rc6-development-readout-20261009.json
ops/policy/rc6-heavy-test-governance-v1.json
ops/policy/rc6-native-prerequisites-v1.json
ops/policy/rc6-product-resource-compatibility-v1.json
ops/policy/rc6-product-resource-compatibility-v2.json
rc6_shadow_runtime/archive_format_contract.py
rc6_shadow_runtime/archive_physical_model.py
rc6_shadow_runtime/exact_binary_storage.py
rc6_shadow_runtime/read_contract.py
scripts/rc6_actions_custody.py
scripts/rc6_architectural_gates.py
scripts/rc6_archive_reader_review.py
scripts/rc6_authenticated_fixture_lifecycle.py
scripts/rc6_capacity_calibration.py
scripts/rc6_capacity_comparison.py
scripts/rc6_cas_original_comparison.py
scripts/rc6_certification_contract.py
scripts/rc6_contract_revisions.py
scripts/rc6_development_checks.py
scripts/rc6_development_parallel_epoch.py
scripts/rc6_development_readout.py
scripts/rc6_ephemeral_api_credential.py
scripts/rc6_g5_contract.py
scripts/rc6_g5_historical_certification.py
scripts/rc6_g5a_actions.py
scripts/rc6_g5a_aggregate_issuer.py
scripts/rc6_g5a_carrier.py
scripts/rc6_g5a_custody_replay.py
scripts/rc6_g5a_family.py
scripts/rc6_g5a_host_capacity.py
scripts/rc6_g5a_native_feasibility.py
scripts/rc6_g5a_native_runner.py
scripts/rc6_g5a_parts.py
scripts/rc6_g5a_raw_capture.py
scripts/rc6_g5a_source_context.py
scripts/rc6_g5a_writer_inventory.py
scripts/rc6_g5b_evidence.py
scripts/rc6_g5b_native_readers.py
scripts/rc6_g5b_source_certify.py
scripts/rc6_g5p_dispatch_bridge.py
scripts/rc6_g5p_evidence.py
scripts/rc6_g5p_frames.py
scripts/rc6_g5p_model.py
scripts/rc6_g5p_reference.py
scripts/rc6_heavy_test_preflight.py
scripts/rc6_host_capacity_readonly.py
scripts/rc6_native_namespace_filter.py
scripts/rc6_native_prerequisites.py
scripts/rc6_owned_g5a_continuation.py
scripts/rc6_owned_gate_lease.py
scripts/rc6_owner_receipts.py
scripts/rc6_private_pid_boundary.py
scripts/rc6_private_pid_capture_replay.py
scripts/rc6_privileged_custody.py
scripts/rc6_product_resource_compatibility.py
scripts/rc6_publication_algorithm_probe.py
scripts/rc6_pytest_fixture_lifecycle.py
scripts/rc6_root_actor_seal.py
scripts/rc6_scoped_infrastructure_lease.py
scripts/rc6_trusted_quota_setup.py
tests/rc6_fixture_sqlite.py
tests/test_porota_validate_deploy_artifact.py
tests/test_rc6_actions_custody.py
tests/test_rc6_architectural_gate_order.py
tests/test_rc6_archive_grouped_wire.py
tests/test_rc6_archive_physical_model.py
tests/test_rc6_archive_reader_review.py
tests/test_rc6_archive_segment_reuse.py
tests/test_rc6_archive_slice_reuse.py
tests/test_rc6_authenticated_fixture_lifecycle.py
tests/test_rc6_candidate_authorization.py
tests/test_rc6_capacity_bootstrap_capture.py
tests/test_rc6_capacity_calibration.py
tests/test_rc6_capacity_comparison.py
tests/test_rc6_capacity_private_pid_capture.py
tests/test_rc6_cas_original_comparison.py
tests/test_rc6_certification_contract.py
tests/test_rc6_contract_revisions.py
tests/test_rc6_development_history.py
tests/test_rc6_development_parallel_epoch.py
tests/test_rc6_development_readout.py
tests/test_rc6_development_rejection.py
tests/test_rc6_ephemeral_api_credential.py
tests/test_rc6_final_family_source_policy.py
tests/test_rc6_focal_raw_custody.py
tests/test_rc6_funnel_algorithm.py
tests/test_rc6_g5_contract.py
tests/test_rc6_g5a_actions.py
tests/test_rc6_g5a_admission.py
tests/test_rc6_g5a_aggregate_issuer.py
tests/test_rc6_g5a_carrier.py
tests/test_rc6_g5a_custody_replay.py
tests/test_rc6_g5a_host_capacity.py
tests/test_rc6_g5a_native_runner.py
tests/test_rc6_g5a_parts.py
tests/test_rc6_g5a_privileged_transport.py
tests/test_rc6_g5a_quota_setup.py
tests/test_rc6_g5a_raw_capture.py
tests/test_rc6_g5a_release_wiring.py
tests/test_rc6_g5a_source_context.py
tests/test_rc6_g5b_evidence.py
tests/test_rc6_g5p_dispatch_bridge.py
tests/test_rc6_g5p_paper_v3.py
tests/test_rc6_governed_evidence_validation.py
tests/test_rc6_heavy_test_preflight.py
tests/test_rc6_horizon_model_admission.py
tests/test_rc6_host_capacity_readonly.py
tests/test_rc6_material_focal_evidence.py
tests/test_rc6_native_namespace_filter.py
tests/test_rc6_native_prerequisites.py
tests/test_rc6_owned_g5a_continuation.py
tests/test_rc6_owned_gate_lease.py
tests/test_rc6_owner_receipts.py
tests/test_rc6_private_pid_boundary.py
tests/test_rc6_private_pid_capture_replay.py
tests/test_rc6_privileged_custody.py
tests/test_rc6_product_resource_compatibility.py
tests/test_rc6_publication_architecture.py
tests/test_rc6_pytest_fixture_lifecycle.py
tests/test_rc6_raw_packaging_custody.py
tests/test_rc6_released_successor.py
tests/test_rc6_root_actor_seal.py
tests/test_rc6_scoped_infrastructure_lease.py
tests/test_rc6_shadow_operational_funnel.py
tests/test_rc6_single_source_tick.py
tests/test_rc6_source_schema_startup.py
tests/test_rc6_stress_phase_timings.py
tests/test_rc6_temporal_query_plan.py""".splitlines()
        self.assertEqual(len(known_unexplained),154)
        requirements={row['id'] for row in matrix['requirements']}
        for path in known_unexplained:
            with self.subTest(path=path):
                binding=matrix['path_evolution'].get(path)
                self.assertIsNotNone(binding)
                self.assertTrue(binding['reason'].strip())
                self.assertTrue(binding['requirement_ids'])
                self.assertLessEqual(set(binding['requirement_ids']),requirements)
        self.assertFalse(any('G5P_GREEN=true' in matrix['path_evolution'][path]['reason']
                             for path in known_unexplained))

    def test_owner_http_error_preserves_only_sanitized_status_after_control_red(self):
        headers=Message()
        headers['X-RateLimit-Remaining']='0'
        headers['X-RateLimit-Reset']='1791753600'
        headers['Retry-After']='60'
        for failure,reason,status in (
            (urllib.error.HTTPError('https://api.github.com/private',403,'forbidden',headers,None),
             'OWNED_LEASE_HTTP_STATUS_403',403),
            (TimeoutError('timed out'),'OWNED_LEASE_HTTP_TIMEOUT',None),
            (ValueError('OWNERSHIP_COMMENT_FUTURE_TIMESTAMP:secret-unstructured-detail'),
             'OWNERSHIP_COMMENT_FUTURE_TIMESTAMP',None)):
            with tempfile.TemporaryDirectory() as temp:
                monitor=lease.GateLeaseMonitor(temp,source_sha='a'*40,source_tree='b'*40,
                    owner_session='CODEX_G5P_TEST',gate='G5P-acquire',
                    launch_receipt_url='https://github.com/mbalbo2023/Porota-trading/issues/471#issuecomment-1',
                    run_id=123,run_attempt=1)
                monitor.command_label='locked312-0'
                public=io.StringIO()
                with redirect_stdout(public):
                    self.assertEqual(str(monitor._failure(failure)),reason)
                detail=json.loads((Path(temp)/'owner-locked312-0.first-error.json').read_bytes())
                exposed=json.loads(public.getvalue())
                self.assertEqual(detail['reason'],reason)
                self.assertEqual(exposed['reason'],reason)
                self.assertEqual(exposed['command_label'],'locked312-0')
                self.assertEqual(exposed['http_status'],status)
                self.assertEqual(detail['http_status'],status)
                self.assertEqual(detail['exception_class'],type(failure).__name__)
                if status==403:
                    self.assertEqual(detail['http_limit_counters'],{
                        'rate_limit_remaining':0,'rate_limit_reset_epoch':1791753600,
                        'retry_after_seconds':60})
                self.assertNotIn('https://api.github.com/private',json.dumps(detail))
                self.assertNotIn('https://api.github.com/private',public.getvalue())
                self.assertNotIn('secret-unstructured-detail',public.getvalue())

    def test_full_git_original_preflight_red_preserves_only_finished_private_controls(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            control=root/'controls-only';control.mkdir()
            commands=root/'command-raw';commands.mkdir()
            epoch=root/'rc6g311-abcdefgh';epoch.mkdir(mode=0o700)
            preparation=epoch/'preparation';preparation.mkdir(mode=0o700)
            (preparation/'selected-epoch-original-preflight.log').write_text('first child error\n')
            source=epoch/'source';source.mkdir()
            (source/'product.py').write_text('product')
            run=SimpleNamespace(control=control,raw=commands,sealed_groups=[],unknown=False,
                manager={'managed_custody_closed':lambda kernel:True},
                lease_stop={'namespace':None,'kernel':{},'label':'fullGit-original-inputs'})
            candidate=SimpleNamespace(gate='G5P-acquire',repo_root=root,source_sha='a'*40,source_tree='b'*40)
            with patch.object(carrier.runpy,'run_path',return_value={'bundle':lambda **kwargs:kwargs['groups']}):
                groups=carrier.preserve_closed_lease_stop(candidate,run,root)
                self.assertIn(('preparation-error311',preparation),groups)
                self.assertFalse(any(path==source for _label,path in groups))
                preparation.chmod(0o777)
                with self.assertRaisesRegex(ValueError,'G5P_BUILDER_DIAGNOSTIC_CONTROL_OWNER_INVALID'):
                    carrier.preserve_closed_lease_stop(candidate,run,root)

    def test_original_full_git_builder_derives_only_exact_pr492_authority_literals(self):
        scope={'pr':492,'branch':'fix/rc6-g5p-paper-v3-20261011',
            'base_branch':'deploy/rc6-pr69-isolated-20260915',
            'base_sha':'da697c6e6c2274579f9e4a112fabc4327475dd35',
            'base_tree':'96a112a55ed779df3f7056b1e30d81aac8d0b791',
            'authorization_schema':'porota.rc6.candidate-launch.v1'}
        def save_raw(path,raw):
            path.write_bytes(raw)
            return hashlib.sha256(raw).hexdigest()
        source=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            kwargs={'python311':'/tmp/controlled-python311',
                'python312':'/tmp/controlled-python312','owner_uid':os.geteuid(),
                'read':lambda path:path.read_bytes(),'save_raw':save_raw}
            candidate=derive.prepare(source,root/'candidate',candidate_scope=scope,**kwargs)
            legacy=derive.prepare(source,root/'legacy',**kwargs)
            new=Path(candidate['builder_path']).read_text()
            old=Path(legacy['builder_path']).read_text()
            self.assertIn("row['branch'] == 'fix/rc6-g5p-paper-v3-20261011'",new)
            self.assertIn("row['PR'] == 492",new)
            self.assertIn("row['PR'] == 476",old)
            self.assertNotIn("row['PR'] == 492",old)
            self.assertTrue(candidate['builder_only_exact_candidate_authority_literals_changed'])
            self.assertFalse(candidate['builder_all_function_AST_unchanged'])
            self.assertTrue(legacy['builder_all_function_AST_unchanged'])
            self.assertEqual(candidate['original_pins'],legacy['original_pins'])
            for changed in ({**scope,'pr':493},{**scope,'branch':'ops/rc6-sre-deep-20261011'},
                            {**scope,'base_sha':'0'*40}):
                with self.assertRaisesRegex(ValueError,'G5P_BUILDER_EXACT_AUTHENTICATED_PR_SCOPE_REQUIRED'):
                    derive.prepare(source,root/('invalid-'+str(changed['pr'])+'-'+changed['branch'].split('/')[0]
                        +'-'+changed['base_sha'][0]),candidate_scope=changed,**kwargs)
            original=(source/derive.BUILDER).read_text()
            with self.assertRaisesRegex(ValueError,'G5P_BUILDER_EXACT_ORIGINAL_AUTHORITY_SITE_REQUIRED'):
                derive.authority_literal_derivation(original.replace("row['PR'] == 476",
                    "row['PR'] == 477"),scope)

    def test_g5p_paired_owner_renewal_restarts_at_most_two_complete_reads(self):
        args={'source_sha':'a'*40,'source_tree':'b'*40,
            'launch_receipt_url':'https://github.com/mbalbo2023/Porota-trading/issues/471#issuecomment-1',
            'owner_session':'CODEX_G5P_TEST','gate':'G5P-acquire'}
        with patch.object(admission,'_admit_g5p_once',side_effect=[
            ValueError('OWNERSHIP_COMMENT_FUTURE_TIMESTAMP'),{'status':'ADMITTED_NATIVE_NOT_STARTED'}]) as read:
            row=admission.admit_g5p(**args)
            self.assertEqual(row['owner_read_consistency_restart_count'],1)
            self.assertEqual(read.call_count,2)
            self.assertIsNone(read.call_args_list[0].kwargs['now'])
            self.assertIsNotNone(read.call_args_list[1].kwargs['now'].tzinfo)
        with patch.object(admission,'_admit_g5p_once',side_effect=[
            ValueError('OWNERSHIP_COMMENT_FUTURE_TIMESTAMP'),
            ValueError('OWNERSHIP_COMMENT_FUTURE_TIMESTAMP'),
            {'status':'ADMITTED_NATIVE_NOT_STARTED'}]) as read:
            row=admission.admit_g5p(**args)
            self.assertEqual(row['owner_read_consistency_restart_count'],2)
            self.assertEqual(read.call_count,3)
            self.assertTrue(all(call.kwargs['now'].tzinfo is not None
                for call in read.call_args_list[1:]))
        with patch.object(admission,'_admit_g5p_once',side_effect=[
            ValueError('OWNERSHIP_COMMENT_FUTURE_TIMESTAMP'),
            ValueError('G5P_LONG_RUN_CONTRACT_DECISION_REVOKED')]) as read:
            with self.assertRaisesRegex(ValueError,'CONTRACT_DECISION_REVOKED'):
                admission.admit_g5p(**args)
            self.assertEqual(read.call_count,2)
        with patch.object(admission,'_admit_g5p_once',side_effect=ValueError(
                'OWNERSHIP_COMMENT_FUTURE_TIMESTAMP')) as read:
            with self.assertRaisesRegex(ValueError,'FUTURE_TIMESTAMP'):
                admission.admit_g5p(**args,now=datetime.now(timezone.utc))
            read.assert_called_once()
        with patch.object(admission,'_admit_g5p_once',side_effect=ValueError(
                'OWNERSHIP_COMMENT_FUTURE_TIMESTAMP')) as read:
            with self.assertRaisesRegex(ValueError,'FUTURE_TIMESTAMP'):
                admission.admit_g5p(**args)
            self.assertEqual(read.call_count,3)
        with patch.object(admission,'_admit_g5p_once',side_effect=ValueError(
                'LATEST_WRITER_OR_DEPLOY_OWNER_CONFLICT')) as read:
            with self.assertRaisesRegex(ValueError,'DEPLOY_OWNER_CONFLICT'):
                admission.admit_g5p(**args)
            read.assert_called_once()

    def test_paper_v3_carrier_restarts_only_concurrent_owner_read(self):
        scope={'source_sha':'a'*40,'source_tree':'b'*40,'owner_session':'CODEX_G5P_TEST',
               'launch_receipt_url':'https://github.com/mbalbo2023/Porota-trading/issues/471#issuecomment-1'}
        calls=[]
        def admit(**kwargs):
            calls.append(kwargs)
            if len(calls)<3:raise ValueError('OWNERSHIP_COMMENT_FUTURE_TIMESTAMP')
            return {'status':'ADMITTED_NATIVE_NOT_STARTED'}
        result=carrier.bounded_v3_owner_readmit(admit,gate='cheap',
            release_profile='PAPER_V3_ONLY',**scope)
        self.assertEqual(result['status'],'ADMITTED_NATIVE_NOT_STARTED')
        self.assertEqual(len(calls),3)
        self.assertTrue(all(call=={**scope,'gate':'cheap'} for call in calls))
        for profile,gate,reason,expected in (
                ('PAPER_V3_ONLY','cheap','LATEST_WRITER_OR_DEPLOY_OWNER_CONFLICT',1),
                ('FULL_G5','cheap','OWNERSHIP_COMMENT_FUTURE_TIMESTAMP',1),
                ('PAPER_V3_ONLY','G5P-acquire','OWNERSHIP_COMMENT_FUTURE_TIMESTAMP',1),
                ('PAPER_V3_ONLY','cheap','OWNERSHIP_COMMENT_FUTURE_TIMESTAMP',3)):
            seen=[]
            def blocked(**kwargs):
                seen.append(kwargs)
                raise ValueError(reason)
            with self.assertRaisesRegex(ValueError,reason):
                carrier.bounded_v3_owner_readmit(blocked,gate=gate,
                    release_profile=profile,**scope)
            self.assertEqual(len(seen),expected)

    def test_g5p_owned_lease_http_budget_is_finite_and_legacy_unchanged(self):
        self.assertEqual(lease.http_budget_for_gate('G5P-acquire'),45)
        self.assertEqual(lease.http_budget_for_gate('G5P-certify'),45)
        self.assertEqual(lease.http_budget_for_gate('cheap','PAPER_V3_ONLY'),45)
        self.assertEqual(lease.http_budget_for_gate('cheap'),5)
        self.assertEqual(lease.http_budget_for_gate('cheap','FULL_G5'),5)
        self.assertEqual(lease.http_budget_for_gate('full-gov311'),5)
        self.assertLess(lease.G5P_HTTP_BUDGET_SECONDS,lease.API_REFRESH_SECONDS)
        with lease._http_budget(time.monotonic()+20,
                                lease.http_budget_for_gate('G5P-acquire')):
            pass
        with self.assertRaisesRegex(lease.LeaseControlFailure,'OWNED_LEASE_HTTP_TOTAL_BUDGET_EXPIRED'):
            with lease._http_budget(time.monotonic()+20,
                                    lease.http_budget_for_gate('full-gov311')):
                pass
        with self.assertRaisesRegex(lease.LeaseControlFailure,'OWNED_LEASE_HTTP_TOTAL_BUDGET_EXPIRED'):
            with lease._http_budget(time.monotonic()+46,lease.G5P_HTTP_BUDGET_SECONDS):
                pass
        now=datetime.now(timezone.utc)
        scope={'fresh_ownership':{
            issue:{'comment_id':issue,'lease_expires_utc':
                   (now+timedelta(minutes=10)).isoformat().replace('+00:00','Z')}
            for issue in ('471','473')}}
        for gate,budget,profile in (('G5P-acquire',45,None),('full-gov311',5,None),
                                    ('cheap',45,'PAPER_V3_ONLY'),('cheap',5,None)):
            with tempfile.TemporaryDirectory() as temp:
                monitor=lease.GateLeaseMonitor(temp,source_sha='a'*40,
                    source_tree='b'*40,owner_session='CODEX_G5P_TEST',gate=gate,
                    launch_receipt_url='https://github.com/mbalbo2023/Porota-trading/issues/471#issuecomment-1',
                    run_id=123,run_attempt=1,clock=lambda:now,release_profile=profile)
                captured=[]
                def budget_context(_deadline, seconds):
                    captured.append(seconds)
                    return nullcontext()
                with patch.object(lease,'_http_budget',side_effect=budget_context),\
                     patch.object(lease,'_scope',return_value=scope),\
                     patch.object(lease,'prove_lease_coverage',return_value={}),\
                     patch.object(monitor,'_store',return_value='0'*64) as saved:
                    monitor._observe('prelaunch')
                self.assertEqual(captured,[budget])
                self.assertEqual(saved.call_args.args[0]['value']['http_total_budget_seconds'],budget)

    def test_cheap_v3_monitor_requires_authenticated_profile(self):
        now=datetime.now(timezone.utc)
        context={'gate':'cheap','source_sha':'a'*40,'source_tree':'b'*40,
                 'owner_session':'CODEX_G5P_TEST','release_profile':'PAPER_V3_ONLY'}
        with patch.object(admission,'owned_gate_control_scope',return_value={
                'release_profile':'FULL_G5'}) as reader:
            with self.assertRaisesRegex(lease.LeaseControlFailure,
                                        'OWNED_LEASE_PROFILE_NOT_AUTHENTICATED'):
                lease._scope(context,now,lambda _path:None)
            self.assertNotIn('release_profile',reader.call_args.kwargs)
        with patch.object(admission,'owned_gate_control_scope',return_value={
                'release_profile':'PAPER_V3_ONLY'}):
            self.assertEqual(lease._scope(context,now,lambda _path:None)['release_profile'],
                             'PAPER_V3_ONLY')
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(lease.LeaseControlFailure,
                                        'OWNED_LEASE_ONLY_CHEAP_V3_PROFILE_MAY_EXTEND_BUDGET'):
                lease.GateLeaseMonitor(temp,source_sha='a'*40,source_tree='b'*40,
                    owner_session='CODEX_G5P_TEST',gate='full-gov311',release_profile='PAPER_V3_ONLY',
                    launch_receipt_url='https://github.com/mbalbo2023/Porota-trading/issues/471#issuecomment-1',
                    run_id=123,run_attempt=1)

    def test_owned_lease_replays_bare_releases_and_only_authenticated_disjoint_writer(self):
        opened=datetime(2026,10,11,19,0,tzinfo=timezone.utc)
        stamp=lambda at:at.isoformat().replace('+00:00','Z')
        owner='CODEX_G5P_TEST';sre='WORK_RC6_SRE_20261011_1740Z'
        context={'source_sha':'a'*40,'source_tree':'b'*40,'owner_session':owner}
        def row(issue,identifier,at,fields):
            return {'id':identifier,'created_at':stamp(at),'updated_at':stamp(at),
                'user':{'login':'mbalbo2023'},
                'issue_url':f'https://api.github.com/repos/mbalbo2023/Porota-trading/issues/{issue}',
                'body':'\n'.join(key+'='+value for key,value in fields.items())}
        def current_fields(lease_end,*,released=False):
            return {'WORKSTREAM_ID':'WS-G5P','SESSION_SUCCESSOR':owner,
                'WRITE_OWNER':'RELEASED' if released else owner,
                'INTEGRATION_OWNER':'NOT_ACQUIRED' if released else owner,
                'DEPLOY_OWNER':'NOT_ACQUIRED','RELEASED':str(released).lower(),
                'SOURCE_SHA':'a'*40,'SOURCE_TREE':'b'*40,
                'SOURCE_LEASE_EXPIRES_UTC':stamp(lease_end),
                'MODE':'PRODUCTION_PAPER / SIMULATION','real_orders_sent':'0'}
        def rows(issue):
            return [
                row(issue,issue*10+1,opened-timedelta(minutes=12),
                    {'WORKSTREAM_ID':'WS-OLD','SESSION_SUCCESSOR':'CODEX_OLD',
                     'WRITE_OWNER':'CODEX_OLD','INTEGRATION_OWNER':'CODEX_OLD','RELEASED':'false'}),
                row(issue,issue*10+2,opened-timedelta(minutes=11),
                    {'WORKSTREAM_ID':'WS-OLD','SESSION_SUCCESSOR':'CODEX_OLD',
                     'WRITE_OWNER':'RELEASED','INTEGRATION_OWNER':'NOT_ACQUIRED','RELEASED':'true'}),
                row(issue,issue*10+3,opened-timedelta(minutes=10),
                    {'WORKSTREAM_ID':'WS-SRE','SESSION_SUCCESSOR':sre,
                     'WRITE_OWNER':sre,'INTEGRATION_OWNER':sre,
                     'DEPLOY_OWNER':'NOT_ACQUIRED','RELEASED':'false'}),
                row(issue,issue*10+4,opened,current_fields(opened+timedelta(minutes=10)))]
        timelines={issue:rows(issue) for issue in (471,473)}
        scope={'fresh_ownership':{str(issue):{
            'authenticated_disjoint_active_writers':[
                {'session':sre,'comment_id':issue*10+3,
                 'branch':'ops/rc6-sre-deep-20261011','paths':['scripts/ops/rc6_sre_*']}]
            } for issue in (471,473)}}
        end=opened+timedelta(minutes=2)
        proof=lease.prove_lease_coverage(timelines,context,opened,end,
            workstream='WS-G5P',authenticated_scope=scope)
        self.assertEqual(proof['471']['authenticated_disjoint_writer_ids'][sre],4713)
        changed=copy.deepcopy(scope)
        changed['fresh_ownership']['471']['authenticated_disjoint_active_writers'][0]['comment_id']=9999
        with self.assertRaisesRegex(lease.LeaseControlFailure,'FOREIGN_RECORD_OVERLAPS_PRODUCER'):
            lease.prove_lease_coverage(timelines,context,opened,end,
                workstream='WS-G5P',authenticated_scope=changed)
        overlapping=copy.deepcopy(timelines)
        overlapping[471].append(row(471,9998,opened+timedelta(minutes=1),
            {'WORKSTREAM_ID':'WS-UNKNOWN','SESSION_SUCCESSOR':'CODEX_UNKNOWN',
             'WRITE_OWNER':'CODEX_UNKNOWN','INTEGRATION_OWNER':'CODEX_UNKNOWN',
             'RELEASED':'false'}))
        with self.assertRaisesRegex(lease.LeaseControlFailure,'FOREIGN_RECORD_OVERLAPS_PRODUCER'):
            lease.prove_lease_coverage(overlapping,context,opened,end,
                workstream='WS-G5P',authenticated_scope=scope)
        gap=copy.deepcopy(timelines)
        gap[471].extend([
            row(471,9996,opened+timedelta(seconds=20),
                current_fields(opened+timedelta(minutes=10),released=True)),
            row(471,9997,opened+timedelta(seconds=40),
                current_fields(opened+timedelta(minutes=10)))])
        with self.assertRaisesRegex(lease.LeaseControlFailure,'OWNED_LEASE_OWNER_RELEASE_OVERLAPS_PRODUCER'):
            lease.prove_lease_coverage(gap,context,opened,end,
                workstream='WS-G5P',authenticated_scope=scope)

    def test_g5p_lease_only_counts_valid_owner_receipts_after_historical_bad_pair(self):
        opened=datetime(2026,10,11,19,48,tzinfo=timezone.utc)
        stamp=lambda at:at.isoformat().replace('+00:00','Z')
        owner='CODEX_G5P_TEST'
        context={'source_sha':'a'*40,'source_tree':'b'*40,'owner_session':owner}
        def row(issue,identifier,at,fields):
            return {'id':identifier,'created_at':stamp(at),'updated_at':stamp(at),
                'user':{'login':'mbalbo2023'},
                'issue_url':f'https://api.github.com/repos/mbalbo2023/Porota-trading/issues/{issue}',
                'body':'\n'.join(key+'='+value for key,value in fields.items())}
        def fields(expiry):
            return {'WORKSTREAM_ID':'WS-RC6-G5P-MATERIAL-20261011',
                'SESSION':owner,'SESSION_SUCCESSOR':owner,'WRITE_OWNER':owner,
                'INTEGRATION_OWNER':owner,'DEPLOY_OWNER':'NOT_ACQUIRED',
                'RELEASED':'false','SOURCE_SHA':'a'*40,'SOURCE_TREE':'b'*40,
                'SOURCE_LEASE_EXPIRES_UTC':stamp(expiry),
                'MODE':'PRODUCTION_PAPER / SIMULATION','real_orders_sent':'0'}
        timelines={}
        for issue in (471,473):
            bad=fields(opened+timedelta(minutes=19))
            valid=fields(opened+timedelta(minutes=16))
            approval={key:value for key,value in valid.items() if key!='SESSION'}
            approval['SOURCE_LEASE_EXPIRES_UTC']='2026-10-11T2026-10-11T20:04:00Z'
            approval['CONTRACT_ACCEPTANCE_SCHEMA']='porota.rc6.contract-revision-acceptance.v2'
            launch={**approval,'AUTHORIZATION_SCHEMA':'porota.rc6.candidate-launch.v1'}
            launch.pop('CONTRACT_ACCEPTANCE_SCHEMA')
            timelines[issue]=[
                row(issue,issue*10+1,opened-timedelta(minutes=3),bad),
                row(issue,issue*10+2,opened-timedelta(minutes=2),valid),
                row(issue,issue*10+3,opened-timedelta(minutes=1),approval),
                row(issue,issue*10+4,opened-timedelta(seconds=30),launch)]
        proof=lease.prove_lease_coverage(timelines,context,opened,opened+timedelta(minutes=1),
            workstream='WS-RC6-G5P-MATERIAL-20261011')
        self.assertEqual(proof['471']['comment_ids'],[4712])
        self.assertEqual(proof['473']['comment_ids'],[4732])
        missing=copy.deepcopy(timelines)
        missing[471]=[item for item in missing[471] if item['id']!=4712]
        with self.assertRaisesRegex(lease.LeaseControlFailure,'OWNED_LEASE_CONTINUITY_GAP'):
            lease.prove_lease_coverage(missing,context,opened,opened+timedelta(minutes=1),
                workstream='WS-RC6-G5P-MATERIAL-20261011')
        during=copy.deepcopy(timelines)
        during[471].append(row(471,9999,opened+timedelta(seconds=10),
            fields(opened+timedelta(minutes=22))))
        with self.assertRaisesRegex(lease.LeaseControlFailure,'OWNED_LEASE_UNBOUNDED_INTERVAL'):
            lease.prove_lease_coverage(during,context,opened,opened+timedelta(minutes=1),
                workstream='WS-RC6-G5P-MATERIAL-20261011')
        unsigned=copy.deepcopy(timelines)
        owner_without_session=fields(opened+timedelta(minutes=16))
        owner_without_session.pop('SESSION')
        unsigned[471].append(row(471,9998,opened+timedelta(seconds=10),owner_without_session))
        with self.assertRaisesRegex(lease.LeaseControlFailure,'G5P_LEASE_ONLY_EXPLICIT_OWNER_RECEIPT'):
            lease.prove_lease_coverage(unsigned,context,opened,opened+timedelta(minutes=1),
                workstream='WS-RC6-G5P-MATERIAL-20261011')

    def test_disjoint_sre_successor_release_preserves_g5p_lease_and_exact_bytes(self):
        opened=datetime(2026,10,12,1,0,tzinfo=timezone.utc)
        stamp=lambda at:at.isoformat().replace('+00:00','Z')
        owner='CODEX_G5P_TEST';old=admission._SRE_OLD_OWNER;new=admission._SRE_NEW_OWNER
        context={'source_sha':'a'*40,'source_tree':'b'*40,'owner_session':owner}
        def row(issue,identifier,at,fields):
            return {'id':identifier,'created_at':stamp(at),'updated_at':stamp(at),
                'user':{'login':'mbalbo2023'},
                'issue_url':f'https://api.github.com/repos/mbalbo2023/Porota-trading/issues/{issue}',
                'body':'\n'.join(key+'='+value for key,value in fields.items())}
        def owner_fields():
            return {'WORKSTREAM_ID':'WS-RC6-G5P-MATERIAL-20261011',
                'SESSION':owner,'SESSION_SUCCESSOR':owner,'WRITE_OWNER':owner,
                'INTEGRATION_OWNER':owner,'DEPLOY_OWNER':'NOT_ACQUIRED','RELEASED':'false',
                'SOURCE_SHA':'a'*40,'SOURCE_TREE':'b'*40,
                'SOURCE_LEASE_EXPIRES_UTC':stamp(opened+timedelta(minutes=16)),
                'MODE':'PRODUCTION_PAPER / SIMULATION','real_orders_sent':'0'}
        paths='scripts/ops/rc6_sre_*,tests/test_rc6_sre_*'
        def sre_fields(session):
            return {'WORKSTREAM_ID':'WS-RC6-SRE-REPAIR-DISK-DEEP-20261011',
                'SESSION':session,'SESSION_SUCCESSOR':session,'WRITE_OWNER':session,
                'INTEGRATION_OWNER':session,'DEPLOY_OWNER':'NOT_ACQUIRED','RELEASED':'false',
                'BRANCH':'ops/rc6-sre-deep-20261011','SOURCE_SHA':'c'*40,
                'BASE_SHA':admission.BASE_SHA,
                'PR':'https://github.com/mbalbo2023/Porota-trading/pull/494',
                'PATHS':paths,'MODE':'PRODUCTION_PAPER / SIMULATION','real_orders_sent':'0'}
        timelines={};scope={'fresh_ownership':{}};revisions={}
        for issue in (471,473):
            prior=row(issue,issue*10+1,opened-timedelta(minutes=2),sre_fields(old))
            owned=row(issue,issue*10+2,opened-timedelta(minutes=1),owner_fields())
            released=row(issue,issue*10+3,opened+timedelta(seconds=30),{
                'WORKSTREAM_ID':'WS-RC6-SRE-REPAIR-DISK-DEEP-20261011',
                'SESSION':old,'SESSION_SUCCESSOR':new,'WRITE_OWNER':'RELEASED',
                'INTEGRATION_OWNER':'RELEASED','DEPLOY_OWNER':'RELEASED','RELEASED':'true'})
            successor=row(issue,issue*10+4,opened+timedelta(minutes=1),sre_fields(new))
            timelines[issue]=[prior,owned,released,successor]
            release_hash=hashlib.sha256(released['body'].encode()).hexdigest()
            writer_hash=hashlib.sha256(successor['body'].encode()).hexdigest()
            revisions[issue]=(released['id'],release_hash,successor['id'],writer_hash)
            scope['fresh_ownership'][str(issue)]={
                'authenticated_sre_successor':{
                    'released_session':old,'successor_session':new,
                    'release_comment_id':released['id'],'release_body_sha256':release_hash,
                    'successor_comment_id':successor['id'],'successor_body_sha256':writer_hash},
                'authenticated_disjoint_active_writers':[{'session':new,
                    'comment_id':successor['id'],'branch':'ops/rc6-sre-deep-20261011',
                    'paths':paths.split(',')}]}
        end=opened+timedelta(minutes=2)
        with patch.dict(admission._SRE_SUCCESSION,revisions):
            proof=lease.prove_lease_coverage(timelines,context,opened,end,
                workstream='WS-RC6-G5P-MATERIAL-20261011',authenticated_scope=scope)
            self.assertEqual(proof['471']['comment_ids'],[4712])
            self.assertEqual(proof['473']['authenticated_disjoint_writer_ids'][new],4734)
            missing=copy.deepcopy(timelines);missing[471]=[x for x in missing[471] if x['id']!=4713]
            with self.assertRaisesRegex(lease.LeaseControlFailure,'SRE_SUCCESSION_TIMELINE_REQUIRED'):
                lease.prove_lease_coverage(missing,context,opened,end,
                    workstream='WS-RC6-G5P-MATERIAL-20261011',authenticated_scope=scope)
            altered=copy.deepcopy(timelines)
            altered[471][2]['body']=altered[471][2]['body'].replace('SESSION='+old,'SESSION='+new)
            with self.assertRaisesRegex(lease.LeaseControlFailure,'SRE_SUCCESSION_BYTES_REBOUND'):
                lease.prove_lease_coverage(altered,context,opened,end,
                    workstream='WS-RC6-G5P-MATERIAL-20261011',authenticated_scope=scope)
            overlap=copy.deepcopy(timelines)
            overlap[473][3]['body']=overlap[473][3]['body'].replace(paths,'scripts/rc6_material_carrier.py')
            with self.assertRaisesRegex(lease.LeaseControlFailure,'SRE_SUCCESSION_BYTES_REBOUND'):
                lease.prove_lease_coverage(overlap,context,opened,end,
                    workstream='WS-RC6-G5P-MATERIAL-20261011',authenticated_scope=scope)
            missing_scope=copy.deepcopy(scope)
            del missing_scope['fresh_ownership']['471']['authenticated_sre_successor']
            with self.assertRaisesRegex(lease.LeaseControlFailure,'DISJOINT_SCOPE_REBOUND'):
                lease.prove_lease_coverage(timelines,context,opened,end,
                    workstream='WS-RC6-G5P-MATERIAL-20261011',authenticated_scope=missing_scope)

    def test_disjoint_documentary_pair_cannot_revoke_g5p_or_borrow_candidate_scope(self):
        opened=datetime(2026,10,12,2,26,tzinfo=timezone.utc)
        now=opened+timedelta(minutes=4)
        stamp=lambda at:at.isoformat().replace('+00:00','Z')
        owner='CODEX_G5P_TEST';source='a'*40;tree='b'*40
        context={'source_sha':source,'source_tree':tree,'owner_session':owner}
        def row(issue,identifier,at,body):
            return {'id':identifier,'created_at':stamp(at),'updated_at':stamp(at),
                'user':{'login':'mbalbo2023'},
                'issue_url':f'https://api.github.com/repos/mbalbo2023/Porota-trading/issues/{issue}',
                'html_url':f'https://github.com/mbalbo2023/Porota-trading/issues/{issue}#issuecomment-{identifier}',
                'body':body}
        def body(values):
            return '\n'.join(key+'='+value for key,value in values.items())
        own={'WORKSTREAM_ID':'WS-RC6-G5P-MATERIAL-20261011','SESSION':owner,
            'SESSION_SUCCESSOR':owner,'WRITE_OWNER':owner,'INTEGRATION_OWNER':owner,
            'DEPLOY_OWNER':'NOT_ACQUIRED','RELEASED':'false','SOURCE_SHA':source,
            'SOURCE_TREE':tree,'SOURCE_LEASE_EXPIRES_UTC':stamp(opened+timedelta(minutes=16)),
            'MODE':'PRODUCTION_PAPER / SIMULATION','real_orders_sent':'0'}
        pilot={'WORKSTREAM_ID':'WS-RC6-BRIDGE-20261012',
            'SESSION':admission._DOC_BRIDGE_SESSION,
            'SESSION_SUCCESSOR':admission._DOC_BRIDGE_SESSION,
            'WRITE_OWNER':admission._DOC_BRIDGE_SESSION,
            'INTEGRATION_OWNER':'NOT_ACQUIRED','DEPLOY_OWNER':'NOT_ACQUIRED',
            'RELEASED':'false','BRANCH':admission._DOC_BRIDGE_BRANCH,
            'BASE_BRANCH':'main','BASE_SHA':'db020762be06ba71ce1d077c8d3642f4f47b627a',
            'PATHS':admission._DOC_BRIDGE_PATH,
            'SCOPE':'New isolated documentary arithmetic pilot only; no overlap with G5P PR492 or SRE PR494; no merge/deploy/host.',
            'ISSUE':'https://github.com/mbalbo2023/Porota-trading/issues/495',
            'SOURCE_LEASE_EXPIRES_UTC':stamp(opened+timedelta(minutes=34)),
            'MODE':'PRODUCTION_PAPER / SIMULATION',
            'real_orders_sent':'0 (policy, not fresh runtime measurement)',
            'PPI_WATCH':'UNTOUCHED','FIX_FORWARD_ONLY':'true'}
        timelines={471:[row(471,47101,opened-timedelta(minutes=1),body(own))],
                   473:[row(473,47301,opened-timedelta(minutes=1),body(own))]}
        twin=row(473,47399,opened+timedelta(minutes=2),body(pilot))
        first=row(471,47199,opened+timedelta(minutes=2,seconds=1),
            body(dict(pilot,OWNER_RECEIPT_473='47399')))
        timelines[471].append(first);timelines[473].append(twin)
        pinned={issue:(item['id'],hashlib.sha256(item['body'].encode()).hexdigest())
            for issue,item in ((471,first),(473,twin))}
        with patch.dict(admission._DOC_BRIDGE_INITIAL,pinned):
            paired=admission.paired_documentary_writer_ids(timelines,source,owner)
            self.assertEqual(paired,{471:[47199],473:[47399]})
            fresh=admission.latest_writers(timelines,source,tree,owner,now)
            self.assertEqual(fresh['471']['authenticated_documentary_writer_ids'],[47199])
            self.assertEqual(fresh['473']['authenticated_disjoint_active_writers'][0]['paths'],
                             [admission._DOC_BRIDGE_PATH])
            proof=lease.prove_lease_coverage(timelines,context,opened,now,
                workstream='WS-RC6-G5P-MATERIAL-20261011',
                authenticated_scope={'fresh_ownership':fresh})
            self.assertEqual(proof['471']['comment_ids'],[47101])
            self.assertEqual(proof['473']['authenticated_disjoint_writer_ids']
                             [admission._DOC_BRIDGE_SESSION],47399)
            renewed=copy.deepcopy(timelines)
            longer=dict(pilot,SOURCE_LEASE_EXPIRES_UTC=stamp(opened+timedelta(minutes=40)))
            renewed[473].append(row(473,473100,opened+timedelta(minutes=3),body(longer)))
            renewed[471].append(row(471,471100,opened+timedelta(minutes=3,seconds=1),
                body(dict(longer,OWNER_RECEIPT_473='473100'))))
            self.assertEqual(admission.paired_documentary_writer_ids(renewed,source,owner),
                             {471:[47199,471100],473:[47399,473100]})
            renewed_scope=admission.latest_writers(renewed,source,tree,owner,now)
            self.assertEqual(renewed_scope['471']['authenticated_disjoint_active_writers'][0]
                             ['comment_id'],471100)
            lease.prove_lease_coverage(renewed,context,opened,now,
                workstream='WS-RC6-G5P-MATERIAL-20261011',
                authenticated_scope={'fresh_ownership':renewed_scope})
            orphan=copy.deepcopy(renewed);orphan[471].pop()
            with self.assertRaisesRegex(ValueError,'ORPHAN_OR_INITIAL_PAIR_MISSING'):
                admission.paired_documentary_writer_ids(orphan,source,owner)
            for issue,field,value in ((473,'PATHS','scripts/rc6_material_carrier.py'),
                                      (471,'OWNER_RECEIPT_473','99999'),
                                      (473,'DEPLOY_OWNER',admission._DOC_BRIDGE_SESSION),
                                      (471,'INTEGRATION_OWNER',admission._DOC_BRIDGE_SESSION)):
                with self.subTest(issue=issue,field=field):
                    changed=copy.deepcopy(timelines)
                    changed[issue][-1]['body']=changed[issue][-1]['body'].replace(
                        field+'='+pilot.get(field,'47399'),field+'='+value)
                    with self.assertRaises(ValueError):
                        admission.paired_documentary_writer_ids(changed,source,owner)
            missing=copy.deepcopy(timelines);missing[473].pop()
            with self.assertRaises(ValueError):
                admission.paired_documentary_writer_ids(missing,source,owner)
            altered=copy.deepcopy(fresh)
            altered['471']['authenticated_documentary_writer_ids']=[]
            with self.assertRaisesRegex(lease.LeaseControlFailure,'DOCUMENTARY_PAIR_SCOPE_REBOUND'):
                lease.prove_lease_coverage(timelines,context,opened,now,
                    workstream='WS-RC6-G5P-MATERIAL-20261011',
                    authenticated_scope={'fresh_ownership':altered})

    def test_missing_memory_cgroup_uses_measured_vm_and_process_limits(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);v2=root/'memory.max';v1=root/'memory.limit_in_bytes'
            meminfo=root/'meminfo'
            def observe(available_kib,*,rlimit=-1):
                meminfo.write_text(f'MemTotal: 8388608 kB\nMemAvailable: {available_kib} kB\n')
                return carrier.v3_memory_capacity(v2_path=v2,v1_path=v1,
                    meminfo_path=meminfo,physical_bytes=8*1024**3,rlimit_as=rlimit)
            good=observe(6*1024**2)
            self.assertEqual(good['status'],'GREEN')
            self.assertEqual(good['source'],'PHYSICAL_MEMORY_AND_RLIMIT_AS_NO_CGROUP_CONTROLLER')
            self.assertEqual(good['cgroup_memory_max'],'absent')
            self.assertEqual(observe(1024**2)['status'],'BLOCKED')
            self.assertEqual(observe(6*1024**2,rlimit=1024**3)['status'],'BLOCKED')
            v2.write_text(str(1024**3))
            self.assertEqual(observe(6*1024**2)['status'],'BLOCKED')
            v2.unlink();v1.write_text(str(1024**3))
            self.assertEqual(observe(6*1024**2)['status'],'BLOCKED')
            v1.unlink();meminfo.write_text('MemTotal: 8388608 kB\n')
            self.assertEqual(carrier.v3_memory_capacity(v2_path=v2,v1_path=v1,
                meminfo_path=meminfo,physical_bytes=8*1024**3,rlimit_as=-1)['status'],'BLOCKED')

    def test_long_acquisition_uses_actual_first_run_start_and_live_readmission(self):
        opened=datetime.now(timezone.utc)-timedelta(minutes=25)
        started=opened+timedelta(minutes=2)
        current=opened+timedelta(minutes=25)
        stamp=lambda value:value.isoformat().replace('+00:00','Z')
        url='https://github.com/mbalbo2023/Porota-trading/issues/471#issuecomment-15'
        common={
            'SOURCE_LEASE_EXPIRES_UTC':stamp(opened+timedelta(minutes=10)),
            'CONTRACT_ACCEPTANCE_SCHEMA':revisions.SCHEMA,
            'RC6_CONTRACT_REVISION_AUTHORIZATION':'APPROVED',
            'CONTRACT_DECISION_ORIGIN':'EXPLICIT_OWNER_DECISION',
            'CONTRACT_REVISION':'PAPER_V3_ONLY_G5P_V1',
            'CONTRACT_SCOPE':'G5P_V3_MATERIAL',
            'CONTRACT_DOCUMENT_SHA256':DOCUMENT_SHA256,
            'CONTRACT_GATES_ALREADY_GREEN':'false',
            'NATIVE_EQUIVALENCE_FROM_APPROVAL_CLAIMED':'false',
            'FINAL_CANDIDATE_ELIGIBLE':'false',
            'DEPLOY_AUTHORIZATION':'NOT_GRANTED',
            'CONTRACT_ACCEPTANCE_LIFECYCLE':revisions.G5P_LONG_RUN_LIFECYCLE}
        twin_body='\n'.join(k+'='+v for k,v in common.items())+'\n'
        decision_body=twin_body+'OWNER_RECEIPT_473=16\nOWNER_RECEIPT_473_SHA256='+hashlib.sha256(twin_body.encode()).hexdigest()+'\n'
        decision={'id':15,'html_url':url,'body':decision_body,
                  'created_at':stamp(opened),'updated_at':stamp(opened)}
        twin={'id':16,'body':twin_body,'created_at':stamp(opened),'updated_at':stamp(opened)}
        run={'id':123,'run_attempt':1,'head_sha':'a'*40,'event':'workflow_dispatch',
             'status':'in_progress','conclusion':None,
             'path':'.github/workflows/rc6-unified-candidate-tests.yml',
             'repository':{'id':admission.REPO_ID},'run_started_at':stamp(started)}
        class Github:
            def request(self,_path):
                return {'/issues/comments/15':decision,'/issues/comments/16':twin,
                        '/actions/runs/123/attempts/1':run}[_path]
        binding={'revision':'PAPER_V3_ONLY_G5P_V1','scope':'G5P_V3_MATERIAL',
                 'source_sha':'a'*40,'source_tree':'b'*40,'contract_sha256':DOCUMENT_SHA256}
        with patch.dict(os.environ,{'GITHUB_RUN_ID':'123','GITHUB_RUN_ATTEMPT':'1','GITHUB_ACTIONS':'true'}),\
             patch.object(revisions,'verify_owner',return_value=hashlib.sha256(decision_body.encode()).hexdigest()) as verify,\
             patch.object(revisions,'_g5p_decision_not_revoked'):
            context=revisions.authenticate_revision(Github(),url,owner_session='OWNER',now=current,**binding)
            self.assertEqual(verify.call_args.kwargs['decision_activation_utc'],started)
            self.assertIs(revisions.validate_revision_approval(context,now=current,**binding),context)
            with self.assertRaisesRegex(ValueError,'CONTRACT_ACCEPTANCE_EXPIRED'):
                revisions.validate_revision_approval(context,now=current+timedelta(seconds=31),**binding)
            # Real GitHub reads can consume more than the 30-second transient
            # window. Issue it after the last revocation read, never at entry.
            ticks=iter((current,current+timedelta(seconds=31),current+timedelta(seconds=33)))
            class SlowClock(datetime):
                @classmethod
                def now(cls,tz=None):return next(ticks)
            with patch.object(revisions,'datetime',SlowClock),\
                 patch.object(revisions,'_g5p_decision_not_revoked') as revocations:
                slow=revisions.authenticate_revision(Github(),url,owner_session='OWNER',**binding)
                self.assertEqual(revocations.call_args.kwargs['now'],current+timedelta(seconds=31))
            self.assertIs(revisions.validate_revision_approval(slow,
                now=current+timedelta(seconds=34),**binding),slow)
            with self.assertRaisesRegex(ValueError,'CONTRACT_ACCEPTANCE_EXPIRED'):
                revisions.validate_revision_approval(slow,
                    now=current+timedelta(seconds=64),**binding)
            ticks=iter((current,current+timedelta(seconds=31),started+timedelta(hours=6,seconds=1)))
            with patch.object(revisions,'datetime',SlowClock),\
                 self.assertRaisesRegex(ValueError,'G5P_LONG_RUN_CONTEXT_ISSUE_OUTSIDE_ACTUAL_RUN'):
                revisions.authenticate_revision(Github(),url,owner_session='OWNER',**binding)
            fresh=revisions.authenticate_revision(Github(),url,owner_session='OWNER',
                now=datetime.now(timezone.utc),**binding)
            with patch.object(admission,'authenticate_g5p_revision',return_value=fresh) as recheck:
                renewed=admission.refresh_g5p_revision_for_final_admission(
                    {'G5P_REVISION_APPROVAL_URL':url},context,'a'*40,'b'*40,'OWNER')
                self.assertIs(renewed,fresh)
                recheck.assert_called_once()
            changed=SimpleNamespace(approval_url=url,approval_body_sha256='0'*64,
                paired_comment_id=fresh.paired_comment_id,
                paired_body_sha256=fresh.paired_body_sha256)
            with patch.object(admission,'authenticate_g5p_revision',return_value=changed),\
                 self.assertRaisesRegex(ValueError,'DECISION_BYTES_CHANGED_DURING_LONG_ADMISSION'):
                admission.refresh_g5p_revision_for_final_admission(
                    {'G5P_REVISION_APPROVAL_URL':url},context,'a'*40,'b'*40,'OWNER')
            run['event']='pull_request'
            with patch.dict(os.environ,{'RC6_GATE':'cheap','GITHUB_EVENT_NAME':'pull_request'}):
                cheap=revisions.authenticate_revision(Github(),url,owner_session='OWNER',
                    now=current,**binding)
                self.assertIs(revisions.validate_revision_approval(cheap,now=current,**binding),cheap)
            with patch.dict(os.environ,{'RC6_GATE':'focal311','GITHUB_EVENT_NAME':'pull_request'}),\
                 self.assertRaisesRegex(ValueError,'G5P_LONG_RUN_ACTUAL_ACTIONS_OR_SOURCE_REBOUND'):
                revisions.authenticate_revision(Github(),url,owner_session='OWNER',now=current,**binding)
            run['event']='workflow_dispatch'
            run['run_started_at']=stamp(opened+timedelta(minutes=11))
            with self.assertRaisesRegex(ValueError,'G5P_LONG_RUN_INITIAL_DECISION_NOT_LIVE'):
                revisions.authenticate_revision(Github(),url,owner_session='OWNER',now=current,**binding)

    def test_long_run_later_contract_revocation_is_a_veto(self):
        now=datetime(2026,10,11,18,30,tzinfo=timezone.utc)
        created='2026-10-11T18:00:00Z'
        decision={'created_at':created}
        revoked={'created_at':'2026-10-11T18:20:00Z',
            'body':'SOURCE_SHA='+'a'*40+'\nCONTRACT_REVISION=PAPER_V3_ONLY_G5P_V1\n'
                   'RC6_CONTRACT_REVISION_AUTHORIZATION=REVOKED\n'}
        github=SimpleNamespace(request=lambda path:None)
        with patch.object(admission,'administrative_anchors',return_value={471:{},473:{}}),\
             patch.object(admission,'recent',return_value=[revoked]):
            with self.assertRaisesRegex(ValueError,'G5P_LONG_RUN_CONTRACT_DECISION_REVOKED'):
                revisions._g5p_decision_not_revoked(github,source_sha='a'*40,
                    revision='PAPER_V3_ONLY_G5P_V1',decision=decision,
                    owner_session='OWNER',now=now)

    def test_legacy_contract_still_expires_without_long_run_lifecycle(self):
        now=datetime(2026,10,11,18,0,tzinfo=timezone.utc)
        expires=(now+timedelta(minutes=10)).isoformat()
        common='\n'.join((
            'CONTRACT_ACCEPTANCE_SCHEMA='+revisions.SCHEMA,
            'RC6_CONTRACT_REVISION_AUTHORIZATION=APPROVED',
            'CONTRACT_DECISION_ORIGIN=EXPLICIT_OWNER_DECISION',
            'CONTRACT_REVISION=PRIVATE_PID_BOUNDARY_V2','CONTRACT_SCOPE=capacity-probe',
            'CONTRACT_DOCUMENT_SHA256='+'c'*64,'CONTRACT_GATES_ALREADY_GREEN=false',
            'NATIVE_EQUIVALENCE_FROM_APPROVAL_CLAIMED=false',
            'FINAL_CANDIDATE_ELIGIBLE=false','DEPLOY_AUTHORIZATION=NOT_GRANTED',
            'SOURCE_LEASE_EXPIRES_UTC='+expires))+'\n'
        decision_body=common+'OWNER_RECEIPT_473=16\nOWNER_RECEIPT_473_SHA256='+hashlib.sha256(common.encode()).hexdigest()+'\n'
        decision={'body':decision_body};twin={'id':16,'body':common,
            'created_at':now.isoformat(),'updated_at':now.isoformat()}
        class Github:
            def request(self,path):return {'/issues/comments/15':decision,'/issues/comments/16':twin}[path]
        url='https://github.com/mbalbo2023/Porota-trading/issues/471#issuecomment-15'
        binding={'revision':'PRIVATE_PID_BOUNDARY_V2','scope':'capacity-probe',
                 'source_sha':'a'*40,'source_tree':'b'*40,'contract_sha256':'c'*64}
        with patch.object(revisions,'verify_owner',return_value=hashlib.sha256(decision_body.encode()).hexdigest()) as verify:
            context=revisions.authenticate_revision(Github(),url,owner_session='OWNER',now=now,**binding)
        self.assertNotIn('decision_activation_utc',verify.call_args.kwargs)
        with self.assertRaisesRegex(ValueError,'CONTRACT_ACCEPTANCE_EXPIRED'):
            revisions.validate_revision_approval(context,now=now+timedelta(minutes=11),**binding)

    def test_acquisition_has_no_gate_credit_and_certification_keeps_g0_g4(self):
        manifest={'schema':gates.MANIFEST_SCHEMA,'source_sha':'a'*40,'source_tree':'b'*40,
            'release_profile':'PAPER_V3_ONLY','owner_session':'OWNER',
            'g5p_revision_approval_url':'https://github.com/mbalbo2023/Porota-trading/issues/471#issuecomment-1',
            'g5p_acquisition':None,'artifacts':[]}
        accepted=admission.validate_g5p_acquisition_manifest(manifest,'a'*40,'b'*40,
            'OWNER',manifest['g5p_revision_approval_url'])
        self.assertEqual(accepted['authenticated_receipts'],[])
        self.assertFalse(accepted['material_gate_credit'])
        self.assertEqual(gates.PAPER_V3_PREREQUISITES['G5P'],
            ('G0','G1.311','G1.312','G2','G3','G4'))
        self.assertTrue(carrier.browser_prerequisite_satisfied('G5P-acquire',[]))
        self.assertFalse(carrier.browser_prerequisite_satisfied('G5P-certify',[]))
        self.assertFalse(carrier.browser_prerequisite_satisfied('Horizon',[]))
        self.assertTrue(carrier.browser_prerequisite_satisfied('G5P-certify',
            [{'gate':'G4','required_browser_material_green':True}]))
        for changed in (dict(manifest,artifacts=[{'gate':'G4'}]),
                        dict(manifest,source_sha='c'*40),
                        dict(manifest,g5p_acquisition={'run_id':123}),
                        dict(manifest,qualification_claimed=True)):
            with self.subTest(changed=changed),self.assertRaisesRegex(
                    ValueError,'G5P_ACQUISITION_MUST_BE_EXACT_NONCERTIFYING_EMPTY_MANIFEST'):
                admission.validate_g5p_acquisition_manifest(changed,'a'*40,'b'*40,
                    'OWNER',manifest['g5p_revision_approval_url'])
        accepted=SimpleNamespace(approval_url=manifest['g5p_revision_approval_url'])
        row={'prerequisites_manifest':manifest,'capacity_peaks':{}}
        with patch.dict(os.environ,{'GITHUB_EVENT_NAME':'workflow_dispatch',
                                    'GITHUB_ACTIONS':'true','GITHUB_RUN_ATTEMPT':'1'}),\
             patch.object(admission,'comment',return_value={'body':'GATES_AUTHORIZED=G5P-acquire'}),\
             patch.object(admission,'authenticate_g5p_revision',return_value=accepted),\
             patch.object(admission,'_admit_source_scope',return_value=dict(row)):
            staged=admission.admit_g5p(source_sha='a'*40,source_tree='b'*40,
                launch_receipt_url='https://example.invalid/launch',owner_session='OWNER',
                gate='G5P-acquire')
        self.assertEqual(staged['g5p_material_stage'],'ACQUISITION_ONLY')
        self.assertFalse(staged['acquisition_prerequisites']['material_gate_credit'])
        with patch.dict(os.environ,{'GITHUB_EVENT_NAME':'workflow_dispatch',
                                    'GITHUB_ACTIONS':'true','GITHUB_RUN_ATTEMPT':'1'}),\
             patch.object(admission,'comment',return_value={'body':'GATES_AUTHORIZED=G5P-certify'}),\
             patch.object(admission,'authenticate_g5p_revision',return_value=accepted),\
             patch.object(admission,'_admit_source_scope',return_value=dict(row)):
            with self.assertRaisesRegex(ValueError,'G5P_CERTIFICATION_REQUIRES_ACTUAL_G0_G4_ARTIFACTS'):
                admission.admit_g5p(source_sha='a'*40,source_tree='b'*40,
                    launch_receipt_url='https://example.invalid/launch',owner_session='OWNER',
                    gate='G5P-certify')

    def test_tooling_requires_live_v3_checks_and_exact_python_pin(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        root=Path(temp.name);admission=root/'admission.json'
        authority={'status':'ADMITTED_NATIVE_NOT_STARTED','source_sha':'a'*40,
            'source_tree':'b'*40,'launch_receipt_url':'https://example.invalid/owner',
            'owner_session':'OWNER','gate':'G5P-acquire','release_profile':'PAPER_V3_ONLY',
            'capacity_peaks':{}}
        admission.write_bytes(carrier.canonical(authority))
        calls=[]
        def measured(_args,run,path,label,*,bootstrap=False):
            self.assertTrue(bootstrap)
            calls.append((str(path),label))
            record={'label':label};run.capacity_records.append(record)
            return {},record,{'capacity':{'status':'GREEN'}}
        environ={'RUNNER_TEMP':temp.name,'RUNNER_TOOL_CACHE':temp.name}
        with patch('scripts.rc6_material_pr_admission.admit',return_value=authority),\
             patch('scripts.rc6_material_carrier.g5p_preflight',side_effect=measured),\
             patch('scripts.rc6_material_carrier.heavy_preflight',side_effect=AssertionError('legacy peak')):
            records=carrier.tooling_capacity(admission,Path(__file__).resolve().parents[1],environ)
        self.assertEqual(len(calls),4)
        self.assertEqual(len(records),4)
        saved=carrier.document((root/'tooling-capacity.json').read_bytes())
        self.assertEqual(saved['release_profile'],'PAPER_V3_ONLY')
        self.assertEqual(saved['reviewed_cpython_tooling']['python_versions'],['3.11.16','3.12.14'])
        workflow=(Path(__file__).resolve().parents[1]/'.github/workflows/rc6-unified-candidate-tests.yml').read_text()
        expected=saved['reviewed_cpython_tooling']
        self.assertTrue(carrier.reviewed_g5p_python_tooling(workflow,expected))
        self.assertFalse(carrier.reviewed_g5p_python_tooling(
            workflow.replace('python-version: "3.12.14"','python-version: "3.12.13"',1),expected))

    def test_preparation_checks_every_storage_path_and_legacy_still_needs_peak(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        root=Path(temp.name);extra=root/'temporary';extra.mkdir()
        args=SimpleNamespace(gate='G5P-certify',release_profile='PAPER_V3_ONLY',
                             repo_root=root,capacity_peaks={})
        run=SimpleNamespace(capacity_records=[],pending_capacity=[])
        def measured(_args,runner,path,label,*,bootstrap=False):
            self.assertTrue(bootstrap)
            record={'label':label};runner.pending_capacity=[record]
            return {},record,{'capacity':{'status':'GREEN'}}
        with patch('scripts.rc6_material_carrier.g5p_preflight',side_effect=measured),\
             patch('scripts.rc6_material_carrier.heavy_preflight',side_effect=AssertionError('legacy peak')):
            result=carrier.preparation_capacity(args,run,root,'before-install',
                additional_storage=(('pip_temporary',extra),))
        self.assertEqual([row['storage_role'] for row in result],
                         ['bootstrap_namespace','candidate_git_objects','pip_temporary'])
        self.assertEqual(len(run.pending_capacity),3)
        args.gate='cheap';args.release_profile='FULL_G5'
        with self.assertRaisesRegex(ValueError,'KNOWN_COMPARABLE_BOOTSTRAP_PEAK_REQUIRED'):
            carrier.preparation_capacity(args,run,root,'before-install')

    def test_bootstrap_reserve_is_stricter_than_native(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        root=Path(temp.name);control=root/'controls';control.mkdir()
        args=SimpleNamespace(gate='G5P-acquire',repo_root=root,source_sha='a'*40,
                             source_tree='b'*40,owner_session='OWNER')
        run=SimpleNamespace(control=control,capacity_records=[],pending_capacity=[])
        fs=SimpleNamespace(f_frsize=1,f_bavail=6*1024**3,f_favail=100000,f_files=100000)
        listing=b'100644 blob '+b'0'*40+b' 1\tproduct.py\n'
        with patch.dict(os.environ,{'GITHUB_RUN_ID':'123','GITHUB_RUN_ATTEMPT':'1'}),\
             patch('scripts.rc6_material_carrier.git',return_value=listing),\
             patch('scripts.rc6_material_carrier.os.statvfs',return_value=fs):
            carrier.g5p_preflight(args,run,root,'native')
            with self.assertRaisesRegex(ValueError,'G5P_V3_ACTUAL_CAPACITY_PREFLIGHT_BLOCKED'):
                carrier.g5p_preflight(args,run,root,'bootstrap',bootstrap=True)
        self.assertEqual(carrier.document((control/'native.g5p-capacity.json').read_bytes())['status'],'GREEN')
        blocked=carrier.document((control/'bootstrap.g5p-capacity.json').read_bytes())
        self.assertEqual(blocked['status'],'BLOCKED')
        self.assertEqual(blocked['bootstrap_reserve_bytes'],4*1024**3)
        self.assertEqual(blocked['blocked_reasons'],
            ['BOOTSTRAP_OR_NATIVE_FREE_BYTES_BELOW_MEASURED_ENVELOPE'])

    def test_first_capacity_red_preserves_the_original_measurement(self):
        workflow=(Path(__file__).resolve().parents[1]/
            '.github/workflows/rc6-unified-candidate-tests.yml').read_text()
        admission=workflow.split('name: Preserve bounded admission controls on every terminal path',1)[1]
        admission=admission.split('name: Preserve native custody controls on every terminal path',1)[0]
        self.assertIn('${{ steps.admit.outputs.control_root }}/tooling-controls/*.json',
            admission)
        self.assertIn('if: always()',admission)


class PaperV3ProducerTests(unittest.TestCase):
    def policy(self, **changes):
        values = dict(maximum_files=512, maximum_bytes=128 * 1024**2,
                      archive_maximum_bytes=512 * 1024**2,
                      archive_format="COMPONENT_V3")
        values.update(changes)
        return SimpleNamespace(**values)

    def test_effective_paper_factory_policy_admitted(self):
        self.assertIsNone(require_paper_v3_archive_policy(self.policy()))

    def test_v4_and_weakened_budgets_rejected(self):
        for changes in (dict(archive_format="COMPONENT_V4"),
                        dict(archive_format="TAR_V2"),
                        dict(maximum_files=513),
                        dict(maximum_bytes=129 * 1024**2),
                        dict(archive_maximum_bytes=513 * 1024**2)):
            with self.subTest(changes=changes):
                with self.assertRaisesRegex(ValueError, "CANONICAL_FACTORY_POLICY_MISMATCH"):
                    require_paper_v3_archive_policy(self.policy(**changes))


class OriginalFrameTests(unittest.TestCase):
    def complete_capture(self, varying=None):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        parent = Path(temp.name)
        originals = {name: (gzip.compress((name + ":original").encode(), mtime=0,
                                        compresslevel=1) if name.endswith(".gz")
                            else (name + ":original").encode()) for name in MEMBERS}
        binding = dict(source_sha="a" * 40, source_tree="b" * 40,
                       owner_session="TEST_OWNER", run_id=123, run_attempt=1,
                       writer_binding_sha256="c" * 64)
        with patch("scripts.rc6_g5p_frames.capacity", return_value={}):
            recorder = FrameRecorder(parent / "frames", **binding)
            cuts = []
            for index, clock in enumerate(horizon_schedule(date(2026, 10, 5))):
                if varying is not None:
                    originals = varying(index)
                as_of = clock.isoformat()
                receipt = {"source_as_of": as_of, "generation_id": f"{index + 1:032x}"}
                recorder.record(index, as_of=as_of, originals=originals,
                                receipt=receipt, restored=originals)
                hashes = {name: hashlib.sha256(value).hexdigest()
                          for name, value in originals.items()}
                cuts.append({"tick_index": index, "as_of": as_of,
                             "phase": "PREOPEN" if index == 0 else "OPEN" if index <= 770 else "CLOSED",
                             "sequence": index + 1, "generation_id": f"{index + 1:032x}",
                             "member_bytes": {name: len(value) for name, value in originals.items()},
                             "original_member_sha256": hashes,
                             "original_manifest_sha256": hashes["manifest.json"],
                             "original_manifest_clock": as_of,
                             "original_generation_custody_unchanged": True,
                             "real_orders_sent": 0, "real_routes": "NOT_CALLED",
                             "archive_residence": {"allocated_bytes_including_directories": 4096,
                                                    "logical_bytes_including_directories": 1024,
                                                    "pending_temporaries_or_intents": 0},
                             "verified_native_archive_head": {"contracted_horizon_seconds": 32400,
                                                               "recovery_margin_seconds": 3600},
                             "recipe_bytes": 64, "native_receipt": receipt})
            manifest = recorder.finish(native_fin_closed=True)
        return parent / "frames", manifest, cuts, binding

    def test_growing_original_gzip_and_sqlite_pages_replay_under_bounded_raw(self):
        report = bytearray()
        checkpoint = bytearray()
        def originals(index):
            report.extend(hashlib.sha256(b"report" + index.to_bytes(4, "big")).digest() * 4)
            checkpoint.extend(hashlib.sha256(b"checkpoint" + index.to_bytes(4, "big")).digest() * 2)
            projection = bytearray(8192)
            projection[4096:4128] = hashlib.sha256(b"page" + index.to_bytes(4, "big")).digest()
            midpoint = len(report) // 2
            report_wire = (gzip.compress(bytes(report[:midpoint]), mtime=0, compresslevel=1)
                           + gzip.compress(bytes(report[midpoint:]), mtime=0, compresslevel=1))
            return {"report.json.gz": report_wire,
                    "checkpoint.json.gz": gzip.compress(bytes(checkpoint), mtime=0, compresslevel=1),
                    "projection.sqlite": bytes(projection),
                    "manifest.json": ("manifest:" + str(index)).encode(),
                    "status.json": ("status:" + str(index)).encode()}
        with patch("scripts.rc6_g5p_frames.MAX_CAPTURE_BYTES", 2 * 1024**2):
            root, manifest, cuts, binding = self.complete_capture(originals)
            self.assertLess(manifest["captured_bytes"], 2 * 1024**2)
            self.assertGreater(sum(cut["member_bytes"]["report.json.gz"]
                                   for cut in cuts), 10 * manifest["captured_bytes"])
            checked = verify_frames(manifest,
                read_frame=lambda name: (root / name).read_bytes(), cuts=cuts, **binding)
            self.assertEqual(checked["verified_original_cuts"], 1202)

    def test_delta_base_and_sqlite_page_tampering_rejected_after_frame_rehash(self):
        root, manifest, cuts, binding = self.complete_capture()
        for member, key, value in (("report.json.gz", "expanded_base_sha256", "0" * 64),
                                   ("checkpoint.json.gz", "gzip_part_bytes", [0]),
                                   ("projection.sqlite", "changed_pages", [{"index": 3,
                                                                                 "raw_base64": "AA=="}])):
            with self.subTest(member=member):
                altered = copy.deepcopy(manifest)
                name = "cut-0010.json.gz"
                frame = json.loads(gzip.decompress((root / name).read_bytes()))
                frame["originals"][member][key] = value
                changed = gzip.compress(wire(frame), mtime=0)
                altered["frames"][10]["bytes"] = len(changed)
                altered["frames"][10]["sha256"] = hashlib.sha256(changed).hexdigest()
                with self.assertRaisesRegex(ValueError,
                                            "G5P_FRAME_(GZIP_BASE|GZIP_PART_BOUNDARY|SQLITE_PAGE_INDEX)_REBOUND"):
                    verify_frames(altered,
                        read_frame=lambda item: changed if item == name else (root / item).read_bytes(),
                        cuts=cuts, **binding)

    def test_noncanonical_gzip_and_separate_capture_bounds_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            original = {name:(gzip.compress(b"member", mtime=0, compresslevel=1)
                              if name.endswith(".gz") else b"member") for name in MEMBERS}
            binding = dict(source_sha="a" * 40, source_tree="b" * 40,
                           owner_session="TEST_OWNER", run_id=123, run_attempt=1,
                           writer_binding_sha256="c" * 64)
            as_of = "2026-10-05T13:20:00+00:00"
            receipt = {"source_as_of": as_of}
            with patch("scripts.rc6_g5p_frames.capacity", return_value={}):
                altered = dict(original)
                altered["report.json.gz"] = gzip.compress(b"member", mtime=1, compresslevel=1)
                with self.assertRaisesRegex(ValueError, "G5P_ORIGINAL_GZIP_REENCODE_NOT_BYTE_EXACT"):
                    FrameRecorder(parent / "noncanonical", **binding).record(0, as_of=as_of,
                        originals=altered, receipt=receipt, restored=altered)
                for limit, field in (("MAX_FRAME_BYTES", "FRAME_BYTES"),
                                     ("MAX_CAPTURE_BYTES", "CAPTURE_TOTAL")):
                    with self.subTest(limit=limit), patch("scripts.rc6_g5p_frames." + limit, 1):
                        recorder = FrameRecorder(parent / limit, **binding)
                        with self.assertRaisesRegex(ValueError,
                                                    "G5P_ORIGINAL_" + field + ".*cut=0:actual=[0-9]+:limit=1"):
                            recorder.record(0, as_of=as_of, originals=original,
                                            receipt=receipt, restored=original)
                        self.assertFalse(recorder.frames)

    def test_all_original_1202_bytes_and_first_run_bind(self):
        root, manifest, cuts, binding = self.complete_capture()
        result = verify_frames(manifest, read_frame=lambda name: (root / name).read_bytes(),
                               cuts=cuts, **binding)
        self.assertEqual(result["verified_original_cuts"], 1202)
        self.assertFalse(result["qualification_claimed"])

    def test_partial_extra_and_rebound_manifest_fail_closed(self):
        root, manifest, cuts, binding = self.complete_capture()
        for changed in (
            dict(frames=manifest["frames"][:-1], actual_original_cuts=1201),
            dict(frames=manifest["frames"] + [manifest["frames"][-1]], actual_original_cuts=1203),
            dict(owner_session="OTHER_OWNER"),
            dict(run_attempt=2),
            dict(writer_binding_sha256="d" * 64),
            dict(source_tree="d" * 40),
        ):
            altered = copy.deepcopy(manifest)
            altered.update(changed)
            with self.subTest(changed=list(changed)):
                with self.assertRaises(ValueError):
                    verify_frames(altered, read_frame=lambda name: (root / name).read_bytes(),
                                  cuts=cuts, **binding)

    def test_fifth_member_zip_corruption_and_original_clock_rejected(self):
        root, manifest, cuts, binding = self.complete_capture()
        cases = []
        changed = copy.deepcopy(cuts); changed[800]["member_bytes"].pop("projection.sqlite")
        cases.append((manifest, changed, lambda name: (root / name).read_bytes()))
        changed = copy.deepcopy(cuts); changed[771]["as_of"] = changed[770]["as_of"]
        cases.append((manifest, changed, lambda name: (root / name).read_bytes()))
        cases.append((manifest, cuts,
                      lambda name: b"corrupt" if name == "cut-0010.json.gz" else (root / name).read_bytes()))
        for altered, rows, reader in cases:
            with self.assertRaises((ValueError, KeyError)):
                verify_frames(altered, read_frame=reader, cuts=rows, **binding)

    def test_unlinked_open_frame_cannot_be_marked_captured(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/"frames"
            originals={name:(gzip.compress(b"original",mtime=0,compresslevel=1)
                             if name.endswith(".gz") else b"original") for name in MEMBERS}
            with patch("scripts.rc6_g5p_frames.capacity",return_value={}):
                recorder=FrameRecorder(root,source_sha="a"*40,source_tree="b"*40,
                    owner_session="TEST_OWNER",run_id=123,run_attempt=1,
                    writer_binding_sha256="c"*64)
                real_fsync=os.fsync
                def unlink_during_fsync(descriptor):
                    target=root/"cut-0000.json.gz"
                    if target.exists():
                        target.unlink()
                    real_fsync(descriptor)
                with patch("scripts.rc6_g5p_frames.os.fsync",side_effect=unlink_during_fsync):
                    with self.assertRaises(OSError):
                        recorder.record(0,as_of="2026-10-05T13:20:00+00:00",
                            originals=originals,restored=originals,
                            receipt={"source_as_of":"2026-10-05T13:20:00+00:00"})
                self.assertEqual(recorder.frames,[])

    def native(self,cuts,binding):
        return {"schema":"rc6.native-archive-v3-profile.v2",
                "source_sha":binding["source_sha"],"source_tree":binding["source_tree"],
                "archive_format":"COMPONENT_V3","private_comparison_active":False,
                "ticks_requested":1201,"native_ticks_executed":1202,
                "archive_restore_count":1202,"restarts":[361,841],
                "execution_complete":True,"native_horizon_contract_verified":True,
                "horizon_complete":True,"complete":True,"acceptance_complete":True,
                "code_source_unchanged":True,"source_database_unchanged":True,
                "import_graph_verified":True,"source_index_and_tar_unchanged":True,
                "native_child_infrastructure":{"native_finalizer_closed":True,
                    "source_sha":binding["source_sha"],"source_tree":binding["source_tree"]},
                "g5p_original_frames_captured":1202,
                "g5p_writer_binding_sha256":binding["writer_binding_sha256"],
                "live_maximum_bytes":128*1024**2,"live_maximum_files":512,
                "archive_maximum_bytes":512*1024**2,"archive_maximum_files":32768,
                "peak_live":{"logical_bytes_including_directories":1024,
                    "allocated_bytes_including_directories":4096,"entries_excluding_root":5},
                "peak_archive":{"logical_bytes_including_directories":1024,
                    "allocated_bytes_including_directories":4096,"entries_excluding_root":5},
                "filesystem_reserve_observation":{"reserve_bytes":2*1024**3,
                    "minimum_free_inode_percent":10,"minimum_observed_free_bytes":3*1024**3,
                    "minimum_observed_free_inode_percent":50},
                "peak_rss_bytes":64*1024**2,
                "peak_scratch_native_policy_occupied_bytes":4096,"cuts":cuts}

    def test_acquisition_model_replays_complete_original_frames_without_green(self):
        root, manifest, cuts, binding = self.complete_capture()
        native = self.native(cuts,binding)
        manifest_raw = wire(manifest)
        native["g5p_frames_manifest_sha256"] = hashlib.sha256(manifest_raw).hexdigest()
        model = build_model(wire(native),manifest_raw,
            read_frame=lambda name:(root/name).read_bytes(),
            owner_session=binding["owner_session"],
            writer_binding_sha256=binding["writer_binding_sha256"])
        self.assertEqual(model["status"],"ACQUISITION_COMPLETE_NOT_CERTIFICATION")
        self.assertFalse(model["G5P_GREEN_claimed"])
        self.assertEqual(model["actual_original_cuts"],1202)
        for field,value in (("archive_format","COMPONENT_V4"),
                            ("native_ticks_executed",1201),
                            ("archive_maximum_bytes",513*1024**2),
                            ("restarts",[361])):
            altered=copy.deepcopy(native);altered[field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):
                build_model(wire(altered),manifest_raw,
                    read_frame=lambda name:(root/name).read_bytes(),
                    owner_session=binding["owner_session"],
                    writer_binding_sha256=binding["writer_binding_sha256"])

    def test_material_requires_prior_distinct_authentic_model_and_fin(self):
        root, manifest, cuts, binding = self.complete_capture()
        native=self.native(cuts,binding)
        manifest_raw=wire(manifest)
        native["g5p_frames_manifest_sha256"]=hashlib.sha256(manifest_raw).hexdigest()
        native_raw=wire(native)
        model=build_model(native_raw,manifest_raw,
            read_frame=lambda name:(root/name).read_bytes(),
            owner_session=binding["owner_session"],
            writer_binding_sha256=binding["writer_binding_sha256"])
        model["run_id"]=99
        model_raw=model_wire(model)
        captured={"Horizon":wire({"source_sha":binding["source_sha"],
                    "source_tree":binding["source_tree"],
                    "native_result_sha256":hashlib.sha256(native_raw).hexdigest(),
                    "native_result_bytes":len(native_raw),"native_horizon_validated":True,
                    "physical_custody_closed":True,
                    "native_finalizer5_green_before_payload_reads":True,
                    "original_native_acceptance_checks_executed":True,"errors":[]}),
                  "native_Horizon":native_raw,
                  "g5p_owned_fin":wire(native["native_child_infrastructure"]),
                  "g5p_frames_manifest":manifest_raw,
                  "g5p_acquisition_model":model_raw,"ownership_index":b"{}"}
        row={"gate":"G5P","release_profile":"PAPER_V3_ONLY",
             "contract_revision":"PAPER_V3_ONLY_G5P_V1",
             "source_sha":binding["source_sha"],"source_tree":binding["source_tree"],
             "owner_session":binding["owner_session"],"run_id":123,"run_attempt":1}
        origin={"source_sha":binding["source_sha"],"run_id":99,"run_attempt":1,
                "exact_model_sha256":hashlib.sha256(model_raw).hexdigest(),
                "actions_zip_authenticated":True}
        writer={"native_runtime_archive_format":"COMPONENT_V3",
                "native_v4_write_enabled":False,
                "binding_sha256":binding["writer_binding_sha256"]}
        with patch("scripts.rc6_g5p_evidence.writer_codec_restorer_binding",return_value=writer):
            result=verify_material(row,captured,
                read_frame=lambda name:(root/name).read_bytes(),
                source_root=Path(__file__).resolve().parents[1],model_origin=origin)
            self.assertTrue(result["G5P_material_verified"])
            for changed in (
                dict(origin,actions_zip_authenticated=False),
                dict(origin,run_id=123),
                dict(origin,exact_model_sha256="0"*64)):
                with self.subTest(changed=changed),self.assertRaises(ValueError):
                    verify_material(row,captured,
                        read_frame=lambda name:(root/name).read_bytes(),
                        source_root=Path(__file__).resolve().parents[1],model_origin=changed)
            altered=dict(captured,g5p_owned_fin=wire({"native_finalizer_closed":False}))
            with self.assertRaises(ValueError):
                verify_material(row,altered,read_frame=lambda name:(root/name).read_bytes(),
                    source_root=Path(__file__).resolve().parents[1],model_origin=origin)
            with self.assertRaises(ValueError):
                verify_material(dict(row,release_profile="FULL_G5"),captured,
                    read_frame=lambda name:(root/name).read_bytes(),
                    source_root=Path(__file__).resolve().parents[1],model_origin=origin)


class ProfileAndPhysicalGuards(unittest.TestCase):
    def test_prior_actions_zip_digest_is_checked_before_any_model_payload(self):
        now=datetime.now(timezone.utc)
        def at(hours):
            return (now+timedelta(hours=hours)).isoformat().replace("+00:00","Z")
        repository={"full_name":"mbalbo2023/Porota-trading","id":gates.REPOSITORY_ID}
        execution={"id":123,"head_sha":"a"*40,"run_attempt":1,
                   "status":"completed","conclusion":"success",
                   "path":gates.WORKFLOW,"repository":repository,
                   "head_repository":repository,
                   "run_started_at":at(-3),"updated_at":at(-2)}
        expected=b"valid"
        reference={"kind":"G5P_V3_ACQUISITION_V1",
                   "artifact_id":456,
                   "artifact_digest":"sha256:"+hashlib.sha256(expected).hexdigest(),
                   "run_id":123,"run_attempt":1}
        artifact={"id":456,"digest":reference["artifact_digest"],
                  "expired":False,"size_in_bytes":len(expected),
                  "workflow_run":{"id":123,"head_sha":"a"*40,
                    "repository_id":gates.REPOSITORY_ID,
                    "head_repository_id":gates.REPOSITORY_ID},
                  "created_at":at(-2.5),"expires_at":at(24)}
        job={"id":789,"run_id":123,"status":"completed","conclusion":"success",
             "labels":["ubuntu-24.04"],"runner_group_name":"GitHub Actions",
             "runner_name":"GitHub Actions 3","started_at":at(-3),
             "completed_at":at(-2)}
        api={"/actions/artifacts/456":artifact,"/actions/runs/123":execution,
             "/actions/runs/123/attempts/1":execution,
             "/actions/runs/123/attempts/1/jobs?per_page=100":{
                 "total_count":1,"jobs":[job]}}
        with tempfile.TemporaryDirectory() as temp:
            def wrong_download(identifier,path,**kwargs):
                Path(path).write_bytes(b"false")
            with self.assertRaisesRegex(ValueError,"ZIP_DIGEST_CHANGED"):
                gates.authenticate_g5p_model_reference(reference,source_sha="a"*40,
                    source_tree="b"*40,owner_session="TEST_OWNER",
                    before_utc=at(-1),evidence_root=Path(temp)/"prior.zip",
                    get=api.__getitem__,download=wrong_download)

    def test_profile_crossing_or_missing_g5p_cannot_admit_g6(self):
        base={"read_contract_sha256":"f"*64,
              "started_utc":"2026-10-11T00:00:00Z",
              "completed_utc":"2026-10-11T00:00:00Z"}
        with patch.object(gates,"validate_receipt",side_effect=lambda row,**kwargs:row):
            for receipts,profile in (([dict(base,gate="G5")],"PAPER_V3_ONLY"),
                                     ([dict(base,gate="G5P",release_profile="PAPER_V3_ONLY")],"FULL_G5"),
                                     ([dict(base,gate="G6.311")],"PAPER_V3_ONLY")):
                with self.subTest(profile=profile,receipts=receipts),self.assertRaises(ValueError):
                    gates.validate_chain(receipts,source_sha="a"*40,source_tree="b"*40,
                                         target_gate="G7",release_profile=profile)
            with self.assertRaisesRegex(ValueError,"GATE_UNKNOWN_TARGET_FOR_PROFILE"):
                gates.validate_chain([],source_sha="a"*40,source_tree="b"*40,
                                     target_gate="G5",release_profile="PAPER_V3_ONLY")

    def test_actual_v3_source_blocks_v4_and_binds_artifact_bytes(self):
        source=Path(__file__).resolve().parents[1]
        raw={name:(source/name).read_bytes() for name in SOURCE_MEMBERS}
        binding=writer_codec_restorer_binding(raw)
        self.assertEqual(binding["native_runtime_archive_format"],"COMPONENT_V3")
        self.assertFalse(binding["native_v4_write_enabled"])
        with self.assertRaisesRegex(ValueError,"RETENTION_V4_WRITE_APPROVAL"):
            require_native_v4_write_capability()
        changed=dict(raw)
        changed["rc6_shadow_runtime/archive_components.py"]=changed[
            "rc6_shadow_runtime/archive_components.py"].replace(
                b"return self.build_v3(source, manifest, manifest_sha)",
                b"return self.build_v4(source, manifest, manifest_sha)",1)
        with self.assertRaisesRegex(ValueError,"EFFECTIVE_NATIVE_V3_DISPATCH"):
            writer_codec_restorer_binding(changed)
        with tempfile.TemporaryDirectory() as temp:
            artifact=Path(temp)
            for name in SOURCE_MEMBERS:
                target=artifact/name
                target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(source/name,target)
            admission={"schema":"rc6.architectural-gate-admission.v1",
                       "status":"GREEN","target_gate":"G7",
                       "release_profile":"PAPER_V3_ONLY",
                       "source_sha":"a"*40,"source_tree":"b"*40,
                       "verified_prerequisites":["G0","G4","G5P","G6.311","G6.312"]}
            envelope={"schema":"rc6.g5p-writer-binding.v1",
                      "release_profile":"PAPER_V3_ONLY",
                      "source_sha":"a"*40,"source_tree":"b"*40,
                      "writer_codec_restorer_binding":binding}
            result=validate_artifact(source,artifact,list(SOURCE_MEMBERS),
                g5_writer_binding=envelope,g7_verified_admission=admission,
                candidate_sha="a"*40,tree_sha="b"*40)
            self.assertEqual(result["g5_writer_byte_binding"]["status"],
                             "EXACT_BYTES_MATCHED_NOT_PROMOTION")
            self.assertEqual(result["g5_writer_byte_binding"]["release_profile"],
                             "PAPER_V3_ONLY")
            crossed=validate_artifact(source,artifact,list(SOURCE_MEMBERS),
                g5_writer_binding=envelope,
                g7_verified_admission=dict(admission,release_profile="FULL_G5"),
                candidate_sha="a"*40,tree_sha="b"*40)
            self.assertEqual(crossed["g5_writer_byte_binding"]["status"],"RED")
            (artifact/"rc6_shadow_runtime/worker.py").write_bytes(
                raw["rc6_shadow_runtime/worker.py"]+b"\n# tampered\n")
            tampered=validate_artifact(source,artifact,list(SOURCE_MEMBERS),
                g5_writer_binding=envelope,g7_verified_admission=admission,
                candidate_sha="a"*40,tree_sha="b"*40)
            self.assertEqual(tampered["g5_writer_byte_binding"]["status"],"RED")

    def test_native_reserve_and_inode_shortage_block_before_frame_write(self):
        with patch("scripts.rc6_g5p_frames.os.statvfs",
                   return_value=SimpleNamespace(f_frsize=4096,f_files=10000,
                       f_favail=9999,f_bavail=100)):
            with self.assertRaisesRegex(ValueError,"RESERVE_OR_INODES_INSUFFICIENT"):
                capacity(Path("/tmp"),4096)
        with patch("scripts.rc6_g5p_frames.os.statvfs",
                   return_value=SimpleNamespace(f_frsize=4096,f_files=10000,
                       f_favail=100,f_bavail=10**9)):
            with self.assertRaisesRegex(ValueError,"RESERVE_OR_INODES_INSUFFICIENT"):
                capacity(Path("/tmp"),4096)

    def test_g8_secondary_must_bind_primary_g7_profile_and_exact_bytes(self):
        binding={"repository":"mbalbo2023/Porota-trading",
                 "workflow_path":".github/workflows/porota-predeploy-v2.yml",
                 "workflow_id":7,"run_id":11,"run_attempt":1,
                 "candidate_sha":"a"*40,"event":"pull_request",
                 "artifact_id":31,"artifact_digest":"sha256:"+"d"*64,
                 "artifact_name":"primary","artifact_size_bytes":1234,
                 "created_at":"2026-10-11T00:00:00Z",
                 "expires_at":"2026-10-18T00:00:00Z",
                 "execution_state":"COMPLETED_SUCCESS"}
        primary=dict(binding,execution_state="IN_PROGRESS_EVIDENCE_ONLY")
        frozen={"g7_admission_sha256":"e"*64,"image_id":"sha256:"+"f"*64}
        report={"schema":"porota.published-primary-evidence-only.v1","status":"GREEN",
                "architectural_gate":"G8","release_profile":"PAPER_V3_ONLY",
                "G8_scope":"EXACT_PUBLISHED_PRIMARY_BYTE_AND_IMAGE_REPLAY_EVIDENCE_ONLY",
                "G8_material_source":"porota_published_artifact_evidence.main",
                "G7_authenticated_admission_sha256":"e"*64,
                "candidate_tree_sha":"b"*40,"primary":primary,
                "G8_primary_artifact_id":31,"G8_primary_artifact_digest":"sha256:"+"d"*64,
                "promotable":False,"production_actions":"NOT_CALLED",
                "real_orders_sent":0,"real_routes":"NOT_CALLED",
                "DEPLOY_OWNER":"NOT_ACQUIRED","G8_deploy_authorization_claimed":False,
                "image_rebuilt":False,"loaded_image_id":frozen["image_id"],
                "frozen_image_id":frozen["image_id"],
                "rootfs":{"status":"GREEN"},
                "actual_image_execution":{"status":"GREEN"},
                "byte_verification":{"status":"GREEN"}}
        payload=wire(report)
        files={"published-primary-verification.json":payload}
        index={"schema":"porota.secondary-evidence-inventory.v1",
               "promotable":False,"primary":primary,
               "files":{"published-primary-verification.json":{
                   "bytes":len(payload),"sha256":hashlib.sha256(payload).hexdigest()}},
               "total_payload_bytes":len(payload)}
        self.assertEqual(verify_g8_report(report,index,files,
            binding=binding,frozen=frozen,tree_sha="b"*40)["status"],"GREEN")
        for altered in (
            dict(report,release_profile="FULL_G5"),
            dict(report,G7_authenticated_admission_sha256="0"*64),
            dict(report,G8_primary_artifact_digest="sha256:"+"0"*64),
            dict(report,DEPLOY_OWNER="ACQUIRED"),
            dict(report,actual_image_execution={"status":"RED"})):
            with self.subTest(changed=altered),self.assertRaises(BindingRejected):
                verify_g8_report(altered,index,files,binding=binding,
                                 frozen=frozen,tree_sha="b"*40)
        with self.assertRaises(BindingRejected):
            verify_g8_report(report,index,dict(files,**{
                "published-primary-verification.json":payload+b"tamper"}),
                binding=binding,frozen=frozen,tree_sha="b"*40)
class PaperV3PhysicalAdmission(unittest.TestCase):
    def setUp(self):
        self.binding = {
            "candidate_sha": "a" * 40, "candidate_tree": "b" * 40,
            "producer": "focal311", "attempt_id": "123:1", "owner_id": "OWNER",
            "workload_fingerprint": "c" * 64,
            "runner_class": "github-hosted/ubuntu-24.04",
        }
        self.bound = 4096 + 512 * 1024**2 + 128 * 1024**2 + 512 * 1024**2 + 2 * 1024**3 + 128 * 1024**2
        self.row = {
            "schema": physical_capacity.PAPER_V3_PHYSICAL_SCHEMA, "status": "GREEN",
            "release_profile": "PAPER_V3_ONLY", "source_sha": "a" * 40,
            "source_tree": "b" * 40, "owner_session": "OWNER",
            "run_id": 123, "run_attempt": 1, "producer": "focal311",
            "workload_fingerprint": "c" * 64,
            "runner_class": "github-hosted/ubuntu-24.04", "stage": "focal311",
            "G5A_project_quota_required": False, "real_orders_sent": 0,
            "memory_ceiling_at_least_native_rss": True,
            "memory_observation": {"status": "GREEN", "required_bytes": 2 * 1024**3,
                                   "effective_available_bytes": 16 * 1024**3},
            "blocked_reasons": [], "minimum_free_inode_ratio": 0.1,
            "native_reserve_bytes": 2 * 1024**3,
            "bootstrap_reserve_bytes": None,
            "archive_v3_limit_bytes": 512 * 1024**2,
            "live_v3_limit_bytes": 128 * 1024**2,
            "native_scratch_limit_bytes": 512 * 1024**2,
            "raw_capture_and_closed_stage_upper_bytes": 2 * 1024**3,
            "control_and_log_envelope_bytes": 128 * 1024**2,
            "predeploy_build_planning_bytes": 0,
            "source_export_upper_bytes": 4096,
            "admission_envelope_bytes": self.bound,
            "measured_free_bytes": self.bound + 5 * 1024**3,
            "measured_free_inodes": 90000, "measured_total_inodes": 100000,
            "measured_at_unix_ns": time.time_ns(), "measured_path": "/tmp",
        }

    def test_exact_live_source_envelope_is_admissible_without_a_historical_peak(self):
        physical_capacity.validate_comparable_peak(self.row, self.binding, self.bound)
        self.assertEqual(physical_capacity.envelope_allocated_bytes(self.row), self.bound)

    def test_source_owner_attempt_and_policy_rebounds_fail_closed(self):
        changes = (
            ("source_sha", "d" * 40), ("source_tree", "d" * 40),
            ("owner_session", "OTHER"), ("run_attempt", 2),
            ("producer", "BIG-browser"), ("workload_fingerprint", "d" * 64),
            ("stage", "BIG-browser"),
            ("release_profile", "FULL_G5"), ("G5A_project_quota_required", True),
            ("memory_ceiling_at_least_native_rss", False),
            ("archive_v3_limit_bytes", 256 * 1024**2),
            ("native_reserve_bytes", 0), ("measured_free_inodes", 1000),
            ("measured_free_bytes", self.bound),
            ("admission_envelope_bytes", self.bound - 4096),
            ("measured_at_unix_ns", time.time_ns() + 61 * 10**9),
        )
        for field, value in changes:
            with self.subTest(field=field):
                mutated = copy.deepcopy(self.row)
                mutated[field] = value
                with self.assertRaises(ValueError):
                    physical_capacity.validate_comparable_peak(mutated, self.binding, self.bound)

    def test_bootstrap_requires_four_gib_after_the_measured_envelope(self):
        row = copy.deepcopy(self.row)
        row["bootstrap_reserve_bytes"] = 4 * 1024**3
        row["measured_free_bytes"] = self.bound + 3 * 1024**3
        with self.assertRaisesRegex(ValueError, "PREFLIGHT_PAPER_V3_SOURCE_ENVELOPE_REBOUND"):
            physical_capacity.validate_comparable_peak(row, self.binding, self.bound)

    def test_bootstrap_producer_needs_the_complete_original_preparation_scope(self):
        row = copy.deepcopy(self.row)
        binding = dict(self.binding, producer="bootstrap")
        row.update(producer="bootstrap", preparation_scope=carrier.PREPARATION_SCOPE,
                   bootstrap_reserve_bytes=4 * 1024**3)
        physical_capacity.validate_comparable_peak(row, binding, self.bound)
        row["preparation_scope"] = {"full_git": True}
        with self.assertRaisesRegex(ValueError, "PREFLIGHT_PAPER_V3_COMPLETE_BOOTSTRAP_SCOPE_REQUIRED"):
            physical_capacity.validate_comparable_peak(row, binding, self.bound)

    def test_predeploy_uses_an_explicit_build_budget_and_keeps_the_four_gib_reserve(self):
        row = copy.deepcopy(self.row)
        binding = dict(self.binding, producer="predeploy")
        row.update(stage="predeploy", producer="predeploy",
                   predeploy_build_planning_bytes=4 * 1024**3,
                   admission_envelope_bytes=self.bound + 4 * 1024**3,
                   measured_free_bytes=self.bound + 9 * 1024**3,
                   bootstrap_reserve_bytes=4 * 1024**3)
        physical_capacity.validate_comparable_peak(row, binding, self.bound + 4 * 1024**3)
        row["predeploy_build_planning_bytes"] = 0
        with self.assertRaisesRegex(ValueError, "PREFLIGHT_PAPER_V3_SOURCE_ENVELOPE_REBOUND"):
            physical_capacity.validate_comparable_peak(row, binding, self.bound + 4 * 1024**3)

    def test_policy_cannot_relabel_the_v3_envelope_or_drop_g7_budget(self):
        policy = physical_capacity.load_policy()
        physical_capacity.validate_paper_v3_policy(policy)
        for change in ({"source_derived_envelope_is_historical_peak": True},
                       {"predeploy_build_planning_bytes": 0},
                       {"original_FIN_and_gate_receipts_remain_required": False}):
            with self.subTest(change=change):
                modified = copy.deepcopy(policy)
                modified["paper_v3_capacity"].update(change)
                with self.assertRaisesRegex(ValueError, "PREFLIGHT_PAPER_V3_POLICY_CHANGED_OR_MISSING"):
                    physical_capacity.validate_paper_v3_policy(modified)

    def test_receipt_rejects_a_different_filesystem_path(self):
        policy = physical_capacity.load_policy()
        with patch.object(carrier, "v3_memory_capacity", return_value=self.row["memory_observation"]), \
             self.assertRaisesRegex(ValueError, "PREFLIGHT_PAPER_V3_PHYSICAL_PATH_REBOUND"):
            physical_capacity.build_receipt(policy=policy, path=Path("/var/tmp"),
                                   expected_peak_bytes=self.bound,
                                   binding=self.binding, comparable_peak=self.row)

    def test_paper_v3_focal_uses_live_physical_guard_and_legacy_keeps_prior_peak(self):
        policy = physical_capacity.load_policy()
        fs = SimpleNamespace(f_frsize=4096, f_bavail=25_000_000,
                             f_favail=90_000, f_files=100_000)
        measured = {"free_bytes": 25_000_000 * 4096,
                    "free_inodes": 90_000, "total_inodes": 100_000,
                    "filesystem_type": "ext4", "allocation_unit_bytes": 4096,
                    "filesystem_device": 1, "mount_id": 1,
                    "path_identity": [1, 2, 1001, 1001, 16877]}
        memory = {"status": "GREEN", "required_bytes": 2 * 1024**3,
                  "effective_available_bytes": 16 * 1024**3,
                  "cgroup_memory_max": "17179869184"}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            owner = SimpleNamespace(source_sha="a" * 40, source_tree="b" * 40,
                                    owner_session="OWNER", gate="focal311",
                                    release_profile="PAPER_V3_ONLY", capacity_peaks={},
                                    repo_root=root)
            run = SimpleNamespace(control=root, capacity_records=[], pending_capacity=[])
            with patch.dict(os.environ, {"GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1"}), \
                 patch.object(carrier, "git", return_value=b"100644 blob " + b"d" * 40 + b" 4096\tfile\n"), \
                 patch.object(carrier.os, "statvfs", return_value=fs), \
                 patch.object(carrier, "v3_memory_capacity", return_value=memory), \
                 patch.object(physical_capacity, "load_policy", return_value=policy), \
                 patch.object(physical_capacity, "measure_filesystem", return_value=measured), \
                 patch.object(physical_capacity, "machine_snapshot", return_value={"github_actions": True}):
                binding, envelope, live = carrier.heavy_preflight(
                    owner, run, root, "focal311", "focal311-test")
            self.assertEqual(binding["producer"], "focal311")
            self.assertEqual(envelope["schema"], physical_capacity.PAPER_V3_PHYSICAL_SCHEMA)
            self.assertEqual(live["capacity"]["status"], "GREEN")
            self.assertEqual(live["admission_basis"],
                             "SOURCE_DERIVED_PAPER_V3_PHYSICAL_ENVELOPE_WITH_LIVE_RECHECK")
            self.assertEqual(len(run.capacity_records), 1)
            self.assertEqual(run.pending_capacity, run.capacity_records)
            self.assertFalse((root / "focal311-test-source-envelope.g5p-physical_capacity.json").exists())
            before = json.loads((root / "focal311-test.capacity-before.json").read_text())
            gates.verify_archived_capacity_pair(before, live,
                {"source_sha": owner.source_sha, "source_tree": owner.source_tree,
                 "run_id": 123, "run_attempt": 1}, producer="focal311")
            owner.release_profile = "FULL_G5"
            with self.assertRaisesRegex(ValueError, "KNOWN_COMPARABLE_PEAK_REQUIRED_BEFORE_PRODUCER"):
                carrier.heavy_preflight(owner, run, root, "focal311", "legacy-test")

    def test_exact_paper_v3_cheap_authority_uses_physical_path_only_after_contract_acceptance(self):
        owner = "CODEX_RC6_G5P_MATERIAL_20261011_1737Z"
        launch_url = "https://github.com/mbalbo2023/Porota-trading/issues/471#issuecomment-10"
        approval_url = "https://github.com/mbalbo2023/Porota-trading/issues/471#issuecomment-11"
        auth = {"SESSION_SUCCESSOR": owner, "GATES_AUTHORIZED": "cheap",
                "CAPACITY_PEAKS_JSON": "{}", "CHEAP_FILES_JSON": "[]",
                "RELEASE_PROFILE": "PAPER_V3_ONLY"}
        issued = SimpleNamespace(owner_session=owner, approval_url=approval_url)
        launch = {"body": "exact launch", "created_at": "2026-10-12T01:00:00Z",
                  "html_url": launch_url}
        successors = {issue: {"body": "SUCCESSION_KIND=EXPLICIT_USER_AUTHORIZATION_AFTER_AUTHENTIC_RELEASE\n",
                              "created_at": "2026-10-12T00:59:00Z",
                              "html_url": "https://github.com/mbalbo2023/Porota-trading/issues/" + str(issue)}
                      for issue in (471, 473)}
        scope = {"pr": 492, "branch": "fix/rc6-g5p-paper-v3-20261011",
                 "base_sha": "d" * 40}
        event = {"number": 492, "action": "reopened",
                 "pull_request": {"head": {"sha": "a" * 40, "ref": scope["branch"]},
                                  "base": {"sha": scope["base_sha"]},
                                  "updated_at": "2026-10-12T01:01:00Z"}}
        with patch.dict(os.environ, {"GITHUB_REPOSITORY": admission.REPO,
                                  "GITHUB_REPOSITORY_ID": str(admission.REPO_ID),
                                  "GITHUB_RUN_ATTEMPT": "1", "GITHUB_EVENT_NAME": "pull_request"}), \
             patch.object(admission.os, "getuid", return_value=1001), \
             patch.object(admission.os, "geteuid", return_value=1001), \
             patch.object(admission, "actual_event", return_value=(event, {})), \
             patch.object(admission, "recovery_anchors", return_value={}), \
             patch.object(admission, "administrative_anchors", return_value=successors), \
             patch.object(admission, "comment", return_value=launch), \
             patch.object(admission, "recent", return_value=[]), \
             patch.object(admission, "launch_fields", return_value=auth), \
             patch.object(admission, "candidate_scope", return_value=scope), \
             patch.object(admission, "fresh_source"), \
             patch.object(admission, "authenticate_g5p_revision", return_value=issued) as accepted, \
             patch.object(admission, "refresh_g5p_revision_for_final_admission", return_value=issued), \
             patch.object(admission, "latest_writer", return_value={}), \
             patch.object(admission, "ops_admission", return_value={}), \
             patch.object(admission, "dedup_admission", return_value={}), \
             patch.object(admission, "verify_capacity_comparison_manifest",
                          side_effect=ValueError("LEGACY_COMPARISON_MUST_NOT_RUN")) as legacy:
            row = admission._admit_source_scope(source_sha="a" * 40,
                                                launch_receipt_url=launch_url,
                                                gate="cheap", now=datetime(2026, 10, 12, 1, 2,
                                                                            tzinfo=timezone.utc))
            self.assertEqual(row["release_profile"], "PAPER_V3_ONLY")
            self.assertTrue(row["capacity_source_comparison"]
                            ["stage_physical_source_envelope_and_live_recheck_required"])
            accepted.assert_called_once()
            legacy.assert_not_called()
            auth["RELEASE_PROFILE"] = "FULL_G5"
            with self.assertRaisesRegex(ValueError, "LEGACY_COMPARISON_MUST_NOT_RUN"):
                admission._admit_source_scope(source_sha="a" * 40,
                    launch_receipt_url=launch_url, gate="cheap",
                    now=datetime(2026, 10, 12, 1, 2, tzinfo=timezone.utc))

    def test_g7_measures_each_actual_storage_role_only_with_paper_v3_authority(self):
        policy = physical_capacity.load_policy()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            authority = {"status": "ADMITTED_NATIVE_NOT_STARTED", "gate": "predeploy",
                         "source_sha": "a" * 40, "source_tree": "b" * 40,
                         "owner_session": "OWNER", "capacity_peaks": {},
                         "release_profile": "PAPER_V3_ONLY",
                         "g5p_revision_approval_url": "https://github.com/mbalbo2023/Porota-trading/issues/471#issuecomment-1"}
            envelope = dict(self.row, stage="predeploy", producer="predeploy",
                            predeploy_build_planning_bytes=4 * 1024**3,
                            admission_envelope_bytes=self.bound + 4 * 1024**3)
            context = {"candidate_sha": "a" * 40, "candidate_tree": "b" * 40}
            with patch.dict(os.environ, {"GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1"}), \
                 patch.object(cleanup, "execution_context", return_value=context), \
                 patch.object(cleanup, "cli_owner", return_value=({"private_root": str(root)}, {})), \
                 patch.object(cleanup, "safe_absolute", side_effect=lambda path: Path(path)), \
                 patch.object(gates.subprocess, "check_output", return_value=str(root)), \
                 patch.object(physical_capacity, "load_policy", return_value=policy), \
                 patch.object(carrier, "g5p_preflight", return_value=(self.binding, envelope, {})) as physical, \
                 patch.object(physical_capacity, "build_receipt", return_value={"receipt_sha256": "c" * 64}) as receipt, \
                 patch.object(physical_capacity, "validate_live_receipt", return_value={"capacity": {"status": "GREEN"}}), \
                 patch.object(cleanup, "publish"):
                result = gates.predeploy_capacity_gate(authority, scope=root, repo=root,
                                                        environ={}, stage="build")
                self.assertEqual(result["status"], "GREEN")
                self.assertEqual(len(result["measured_storage"]), 3)
                self.assertEqual(physical.call_count, 3)
                self.assertEqual(receipt.call_count, 3)
                denied = dict(authority, g5p_revision_approval_url=None)
                with self.assertRaisesRegex(ValueError, "G7_PAPER_V3_EXACT_CONTRACT_APPROVAL_REQUIRED"):
                    gates.predeploy_capacity_gate(denied, scope=root, repo=root,
                                                  environ={}, stage="build")
                legacy = dict(authority, release_profile="FULL_G5")
                with self.assertRaisesRegex(ValueError, "G7_CAPACITY_KNOWN_COMPARABLE_PREDEPLOY_PEAK_REQUIRED"):
                    gates.predeploy_capacity_gate(legacy, scope=root, repo=root,
                                                  environ={}, stage="build")

    def test_predeploy_v2_preserves_actual_capacity_receipts_before_private_cleanup(self):
        workflow = (Path(__file__).resolve().parents[1] /
                    ".github/workflows/porota-predeploy-v2.yml").read_text()
        controls = workflow.index("- name: Preserve factual G7 capacity controls without promotion")
        primary = workflow.index("- name: Upload predeploy evidence")
        cleanup_step = workflow.index("- name: Local cleanup")
        self.assertLess(controls, cleanup_step)
        self.assertLess(primary, cleanup_step)
        block = workflow[controls:primary]
        self.assertIn("if: always() && env.POROTA_PREDEPLOY_TMP != ''", block)
        self.assertIn("id: g7_capacity_controls", block)
        self.assertIn("porota-predeploy-v2-capacity-controls-", block)
        self.assertIn("${{ env.POROTA_PREDEPLOY_TMP }}/porota-g7-*-capacity-*.json", block)
        self.assertIn("${{ env.POROTA_PREDEPLOY_TMP }}/g7-*.g5p-capacity.json", block)
        self.assertIn("- name: Require published G7 capacity controls on green path", block)
        self.assertIn("if: success()", block)
        self.assertIn("steps.g7_capacity_controls.outputs.artifact-id", block)
        self.assertIn("steps.g7_capacity_controls.outputs.artifact-digest", block)
        self.assertIn("POROTA_G7_CAPACITY_CONTROLS_PROMOTABLE=false", block)
        self.assertNotIn("id: frozen_primary", block)



class OrderedGateReferenceTests(unittest.TestCase):
    def test_closed_raw_reference_binds_all_three_cheap_receipts_and_rejects_rebound(self):
        source="a"*40;tree="b"*40
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/"carrier").mkdir()
            rows=[]
            for gate in reference.ORDERED_GATE_RECEIPTS["cheap"]:
                path="carrier/"+gate+".receipt.json"
                raw=wire({"gate":gate,"status":"GREEN","source_sha":source,
                          "source_tree":tree,"run_id":123,"run_attempt":1})
                (root/path).write_bytes(raw)
                digest=hashlib.sha256(raw).hexdigest()
                rows.append({"stored_path":path,"stored_sha256":digest,
                             "original_sha256":digest})
            (root/"raw-lossless.manifest.json").write_bytes(wire({
                "schema":"rc6.supplementary-owned-raw-lossless.v1",
                "source_sha":source,"source_tree":tree,"gate":"cheap",
                "files":rows,"file_count":len(rows)}))
            kwargs={"stage":"cheap","source_sha":source,"source_tree":tree,
                    "run_id":123,"run_attempt":1,"artifact_id":456,
                    "artifact_digest":"sha256:"+"c"*64}
            result=reference.ordered_gate_proposal(root,**kwargs)
            self.assertEqual([item["path"] for item in result["receipts"]],
                             ["carrier/"+gate+".receipt.json"
                              for gate in reference.ORDERED_GATE_RECEIPTS["cheap"]])
            self.assertEqual(result["artifact_id"],456)
            self.assertNotIn("gate_credit",result)
            (root/"carrier/G1.312.receipt.json").write_bytes(b"{}")
            with self.assertRaisesRegex(ValueError,
                                        "ORDERED_GATE_CLOSED_RECEIPT_NOT_GREEN_OR_REBOUND"):
                reference.ordered_gate_proposal(root,**kwargs)

    def test_workflow_only_proposes_after_raw_upload(self):
        workflow=(Path(__file__).resolve().parents[1]/
                  ".github/workflows/rc6-unified-candidate-tests.yml").read_text()
        raw=workflow.index("id: original_raw")
        proposed=workflow.index("- name: Propose exact ordered gate receipt references from closed RAW")
        self.assertLess(raw,proposed)
        self.assertIn("if: success() && steps.original_raw.outputs.artifact-id != ''",workflow[proposed:])
        self.assertIn("--artifact-digest \"$RC6_RAW_ARTIFACT_DIGEST\"",workflow[proposed:])


if __name__ == "__main__":
    unittest.main()
