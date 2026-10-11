"""Local, offline regressions for the PAPER V3 material path."""
import unittest
import copy
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
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


class G5PBootstrapCapacityTests(unittest.TestCase):
    def test_long_acquisition_uses_actual_first_run_start_and_live_readmission(self):
        opened=datetime(2026,10,11,18,0,tzinfo=timezone.utc)
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
            'CONTRACT_DOCUMENT_SHA256':'c'*64,
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
                 'source_sha':'a'*40,'source_tree':'b'*40,'contract_sha256':'c'*64}
        with patch.dict(os.environ,{'GITHUB_RUN_ID':'123','GITHUB_RUN_ATTEMPT':'1','GITHUB_ACTIONS':'true'}),\
             patch.object(revisions,'verify_owner',return_value=hashlib.sha256(decision_body.encode()).hexdigest()) as verify,\
             patch.object(revisions,'_g5p_decision_not_revoked'):
            context=revisions.authenticate_revision(Github(),url,owner_session='OWNER',now=current,**binding)
            self.assertEqual(verify.call_args.kwargs['decision_activation_utc'],started)
            self.assertIs(revisions.validate_revision_approval(context,now=current,**binding),context)
            with self.assertRaisesRegex(ValueError,'CONTRACT_ACCEPTANCE_EXPIRED'):
                revisions.validate_revision_approval(context,now=current+timedelta(seconds=31),**binding)
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
    def complete_capture(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        parent = Path(temp.name)
        originals = {name: (name + ":original").encode() for name in MEMBERS}
        binding = dict(source_sha="a" * 40, source_tree="b" * 40,
                       owner_session="TEST_OWNER", run_id=123, run_attempt=1,
                       writer_binding_sha256="c" * 64)
        with patch("scripts.rc6_g5p_frames.capacity", return_value={}):
            recorder = FrameRecorder(parent / "frames", **binding)
            cuts = []
            for index, clock in enumerate(horizon_schedule(date(2026, 10, 5))):
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
            originals={name:b"original" for name in MEMBERS}
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
if __name__ == "__main__":
    unittest.main()
