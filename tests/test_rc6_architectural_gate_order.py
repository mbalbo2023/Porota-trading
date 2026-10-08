"""Cheap counterexamples for promotion admission; no material producer runs."""
import copy
import hashlib
import io
import json
from pathlib import Path
import zipfile

import pytest

from scripts import rc6_architectural_gates as gates
from scripts import rc6_material_big as big
from scripts import rc6_material_focal as focal
from rc6_shadow_runtime.read_contract import DEFAULT_READ_CONTRACT


SOURCE_SHA = "a" * 40
SOURCE_TREE = "b" * 40
CONTRACT = DEFAULT_READ_CONTRACT.fingerprint()

def preserved_heavy_fixture():
    return [{'nodeid':name+'::test_counter_case[unit_only]',
        'classname':name[:-3].replace('/','.'),'name':'test_counter_case[unit_only]'}
        for name in sorted(gates.G6_PRESERVED_HEAVY_FILES)]


def receipt(gate):
    index = gates.GATES.index(gate)
    row = {"schema": gates.SCHEMA, "gate": gate, "status": "GREEN", "source_sha": SOURCE_SHA,
        "source_tree": SOURCE_TREE, "read_contract_sha256": CONTRACT, "native_exit_code": 0,
        "native_fin_closed": True, "source_unchanged": True, "mode": "PRODUCTION_PAPER / SIMULATION",
        "real_orders_sent": 0, "real_routes": "NOT_CALLED", "ppi_watch": "UNTOUCHED",
        "DEPLOY_OWNER": "NOT_ACQUIRED", "runtime_validated": False, "runner_class": gates.RUNNER_CLASS,
        "runner_receipt": {"github_hosted": True, "runner_os": "Linux", "runner_arch": "X64"},
        "run_id": 111 + index, "run_attempt": 1, "workflow_path": gates.WORKFLOW,
        "started_utc": "2026-10-08T12:%02d:00Z" % index,
        "completed_utc": "2026-10-08T12:%02d:59Z" % index, "checks": {"native": True}}
    if gate == "G0":
        row["checks"] = {name: True for name in ("ownership", "inventory", "closure_matrix", "compile_import",
            "paper_invariants", "ppi_watch_invariant", "full_git_fsck", "full_source", "capacity_preflight")}
        row['preserved_heavy_corpora']={epoch:preserved_heavy_fixture() for epoch in ('311','312')}
    if gate in ("G1.311", "G1.312", "G2", "G3", "G6.311", "G6.312"):
        row.update(test_cases=1, identity_verified=True, failures=0, errors=0, skipped=0, xfail=0,
            python_epoch="312" if gate in ("G1.312", "G3", "G6.312") else "311")
    if gate in ('G1.311','G1.312'):
        row['checks']['scoped_infrastructure_positive_probe']=True
    if gate == "G4":
        row["checks"].update(source_snapshot_single_capture_per_tick=True, capacity_live=True, authenticated_cleanup=True)
        row["resources"] = {"catalog": 12000, "observations": 60000, "factual_paper_exits": 5,
            "hard_limit_seconds": 90, "qualification_limit_seconds": 75, "elapsed_seconds": 74.99,
            "peak_rss_bytes": 2 * 1024**3 - 1, "evidence_bytes": 128 * 1024**2, "retained_entries": 100000}
    if gate == "G5":
        row["required_material_gates"] = ["retention", "Horizon", "browser"]
        row["checks"].update(retention=True, Horizon=True, browser=True)
    if gate.startswith("G6."):
        row["scope"] = "repository-root automatic pytest discovery"
        row["checks"].update(full_source=True, closure_matrix=True)
        row['required_preserved_heavy_corpus']=preserved_heavy_fixture()
    return row


def chain(target="G7"):
    return [receipt(name) for name in gates.PREREQUISITES[target]]


def admit(rows, target="G7"):
    return gates.validate_chain(rows, source_sha=SOURCE_SHA, source_tree=SOURCE_TREE, target_gate=target,
        read_contract_sha256=CONTRACT)


def test_green_chain_authorizes_only_next_gate_not_runtime_or_deploy():
    result = admit(chain())
    assert result["status"] == "GREEN" and result["target_gate"] == "G7"
    assert result["runtime_validated"] is result["final_candidate_eligible"] is False
    assert result["DEPLOY_OWNER"] == "NOT_ACQUIRED" and result["workflow_success_alone_accepted"] is False


@pytest.mark.parametrize('epoch',['311','312'])
def test_a_local_fail_closed_probe_does_not_qualify_intrafase_cleanup(epoch):
    row=receipt('G1.'+epoch)
    row['checks'].pop('scoped_infrastructure_positive_probe')
    with pytest.raises(ValueError,match='ACTUAL_SCOPED_INFRASTRUCTURE_PROBE_REQUIRED'):
        gates.validate_receipt(row,source_sha=SOURCE_SHA,source_tree=SOURCE_TREE)


@pytest.mark.parametrize('missing',sorted(gates.G1_REQUIRED_CHEAP_FILES))
def test_cheap_pass_cannot_substitute_for_any_required_architectural_guard_module(missing):
    with pytest.raises(ValueError,match='REQUIRED_CHEAP_ARCHITECTURAL_CORPUS_MISSING'):
        gates.validate_g1_files(sorted(gates.G1_REQUIRED_CHEAP_FILES-{missing}))


@pytest.mark.parametrize('heavy',sorted(gates.G1_HEAVY_FILES))
def test_cheap_architectural_corpus_cannot_include_heavy_big_or_liveness_module(heavy):
    with pytest.raises(ValueError,match='REQUIRED_CHEAP_ARCHITECTURAL_CORPUS_MISSING_OR_HEAVY'):
        gates.validate_g1_files(sorted(gates.G1_REQUIRED_CHEAP_FILES)+[heavy])


@pytest.mark.parametrize('field,value',[('status','BLOCKED'),('actual_positive',False),
    ('kernel_census_verified',False),('namespace_removed',False),('python_minor','3.10'),
    ('source_sha','c'*40),('source_tree','d'*40)])
def test_missing_kernel_capability_or_probe_binding_cannot_promote_g1(field,value):
    probe={'schema':'porota.rc6.scoped-infrastructure-probe.v1','status':'GREEN','actual_positive':True,
        'kernel_census_verified':True,'namespace_removed':True,'source_sha':SOURCE_SHA,'source_tree':SOURCE_TREE,
        'python_minor':'3.11','python_version':'3.11.16','binding':{'candidate_sha':SOURCE_SHA,
        'candidate_tree':SOURCE_TREE,'producer':'focal311','attempt_id':'112:1',
        'owner_id':'CODEX_RC6_ARCHITECTURAL_RCA_20261008_1205UTC','workload_fingerprint':'1'*64,
        'runner_class':gates.RUNNER_CLASS},'whole_phase_or_big_ECHILD_guard_changed':False,
        'original_tests_or_fixture_results_changed':False,'real_orders_sent':0}
    probe[field]=value
    with pytest.raises(ValueError,match='PROBE_RED_OR_REBOUND'):
        gates.verify_scoped_infrastructure_probe(probe,receipt('G1.311'),diagnostics_raw=b'',diagnostics_index={})


def test_external_governed_import_rejects_cross_source_before_accessing_native_bytes(tmp_path):
    verified=admit(chain())
    verified['authenticated_receipts']=chain()
    verified['source_sha']='c'*40
    with pytest.raises(ValueError,match='EXACT_SOURCE_SHA_TREE_MISMATCH'):
        gates.import_verified_governed(verified,output_root=tmp_path)


def test_external_governed_import_requires_closed_full_predecessor_chain(tmp_path):
    verified=admit(chain())
    verified['authenticated_receipts']=chain()[:-1]
    with pytest.raises(ValueError,match='PREREQUISITE_MISSING_PENDING_OR_RED'):
        gates.import_verified_governed(verified,output_root=tmp_path)


@pytest.mark.parametrize("missing", gates.PREREQUISITES["G7"])
def test_every_required_stage_is_mandatory(missing):
    with pytest.raises(ValueError, match="PREREQUISITE_MISSING"):
        admit([row for row in chain() if row["gate"] != missing])


@pytest.mark.parametrize("field,value", [("status", "RED"), ("status", "PENDING"), ("native_exit_code", 3),
    ("native_exit_code", False), ("native_fin_closed", False), ("source_unchanged", False),
    ("source_sha", "c" * 40), ("source_tree", "d" * 40), ("real_orders_sent", False),
    ("real_orders_sent", 1), ("real_routes", "CALLED"), ("ppi_watch", "CHANGED"),
    ("runtime_validated", True), ("run_attempt", 2), ("runner_class", "larger-custom-runner"),
    ("read_contract_sha256", "e" * 64)])
def test_workflow_success_cannot_override_native_contract_or_safety(field, value):
    rows = chain()
    rows[1][field] = value
    with pytest.raises(ValueError):
        admit(rows)


def test_overlapping_gates_fail_instead_of_running_full_suite_concurrently():
    rows = chain()
    rows[-2]["started_utc"] = rows[-3]["started_utc"]
    with pytest.raises(ValueError, match="ORDER_VIOLATION"):
        admit(rows)


def test_duplicate_receipt_cannot_hide_a_missing_epoch():
    with pytest.raises(ValueError, match="DUPLICATE_STAGE"):
        admit(chain() + [receipt("G1.311")])


@pytest.mark.parametrize("field,value", [("elapsed_seconds", 75.001), ("elapsed_seconds", float("nan")),
    ("hard_limit_seconds", 91), ("qualification_limit_seconds", 90), ("catalog", 11999),
    ("observations", 59999), ("factual_paper_exits", 4), ("peak_rss_bytes", 2 * 1024**3),
    ("evidence_bytes", 128 * 1024**2 + 1), ("retained_entries", 100001)])
def test_material_limits_and_headroom_are_not_relaxed(field, value):
    row = receipt("G4")
    row["resources"][field] = value
    with pytest.raises(ValueError, match="HEADROOM_QUALIFICATION_RED"):
        gates.validate_receipt(row, source_sha=SOURCE_SHA, source_tree=SOURCE_TREE)


def cases():
    rows = [{"nodeid": "tests/test_a.py::test_a", "classname": "tests.test_a", "name": "test_a"}]
    return {"items": rows, "pytest_exit_code": 0}


def junit(case='<testcase classname="tests.test_a" name="test_a"/>', **counts):
    values = {"tests": 1, "failures": 0, "errors": 0, "skipped": 0, **counts}
    return ('<testsuite ' + ' '.join('%s="%s"' % pair for pair in values.items()) + '>' + case + '</testsuite>').encode()


def test_junit_identity_green_and_closed_fin_red_are_distinct():
    assert gates.validate_junit(cases(), cases(), junit())["cases"] == 1
    raw = junit('<testcase classname="tests.test_a" name="test_a"><failure/></testcase>', failures=1)
    assert gates.validate_junit(cases(), cases(), raw, require_green=False)["failure"] == 1
    with pytest.raises(ValueError, match="NATIVE_TEST_RED"):
        gates.validate_junit(cases(), cases(), raw)


@pytest.mark.parametrize("raw", [junit('<testcase classname="tests.test_a" name="alien"/>'),
    junit('<testcase classname="tests.test_a" name="test_a"/>' * 2, tests=2),
    junit(tests=6215), junit('<testcase classname="tests.test_a" name="test_a"><error/></testcase>', errors=1),
    junit('<testcase classname="tests.test_a" name="test_a"><skipped/></testcase>', skipped=1)])
def test_junit_counter_identity_errors_skips_and_truncated_collection_stay_red(raw):
    with pytest.raises(ValueError):
        gates.validate_junit(cases(), cases(), raw)


def test_equal_counts_with_different_collected_nodes_are_rejected():
    execution = cases()
    execution["items"][0]["nodeid"] = "tests/test_other.py::test_a"
    with pytest.raises(ValueError, match="COLLECTION_EXECUTION_IDENTITY"):
        gates.validate_junit(cases(), execution, junit())


@pytest.mark.parametrize('omission',['module','parameter'])
def test_g6_cannot_omit_original_heavy_module_or_parameter_even_if_its_own_junit_is_green(omission):
    required=preserved_heavy_fixture()
    required.append(dict(required[0],nodeid=required[0]['nodeid'].replace('unit_only','second_parameter'),
        name='test_counter_case[second_parameter]'))
    actual=required[1:] if omission=='module' else required[:-1]
    if omission=='module':actual=[row for row in actual if row['nodeid'].split('::',1)[0]!=required[0]['nodeid'].split('::',1)[0]]
    report={'items':actual,'pytest_exit_code':0}
    xml=''.join('<testcase classname="'+row['classname']+'" name="'+row['name']+'"/>' for row in actual)
    with pytest.raises(ValueError,match='PARAMETER_IDENTITY_COVERAGE_MISMATCH'):
        gates.verify_preserved_heavy_coverage(required,report,report,junit(xml,tests=len(actual)))


def test_g6_complete_original_heavy_corpus_requires_exact_collection_execution_and_junit():
    required=preserved_heavy_fixture();report={'items':required,'pytest_exit_code':0}
    xml=''.join('<testcase classname="'+row['classname']+'" name="'+row['name']+'"/>' for row in required)
    result=gates.verify_preserved_heavy_coverage(required,report,report,junit(xml,tests=len(required)))
    assert result['required_original_heavy_node_count']==3
    assert result['required_modules']==sorted(gates.G6_PRESERVED_HEAVY_FILES)
    assert result['collection_execution_JUnit_identity_verified']


def test_g6_heavy_ledger_cannot_be_replaced_after_authentic_g0_or_between_epochs():
    rows=chain();g6=next(row for row in rows if row['gate']=='G6.312')
    g6['required_preserved_heavy_corpus'][0]['nodeid']+='-other'
    with pytest.raises(ValueError,match='NOT_BOUND_TO_AUTHENTIC_G0_SAME_SHA_TREE'):
        admit(rows)


@pytest.mark.parametrize("name", ["../other.json", "/other.json", "a/../other.json", "a\\other.json"])
def test_artifact_paths_cannot_escape_or_alias(tmp_path, name):
    path = tmp_path / "bad.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(name, b"{}")
    with pytest.raises(ValueError, match="UNSAFE_OR_DUPLICATE"):
        gates.archive_members(path)


def test_artifact_member_digest_must_match_exact_raw_bytes(tmp_path):
    path = tmp_path / "native.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("native.json", b'{"status":"RED"}\n')
    row = receipt("G0")
    row["native_evidence"] = {"admission": {"path": "native.json", "sha256": "0" * 64}}
    with pytest.raises(ValueError, match="NATIVE_EVIDENCE_BYTES_MISMATCH"):
        gates.verify_native_evidence(row, path)


def test_api_success_and_green_summary_without_native_raw_are_blocked(tmp_path):
    path = tmp_path / "empty.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("transport.json", b'{"conclusion":"success"}\n')
    with pytest.raises(ValueError, match="NATIVE_EVIDENCE_MISSING"):
        gates.verify_native_evidence(receipt("G2"), path)


def api_origin():
    reference={"artifact_id":123,"artifact_digest":"sha256:"+"c"*64,"run_id":111,"run_attempt":1}
    repository={"full_name":gates.REPOSITORY,"id":gates.REPOSITORY_ID}
    run={"id":111,"head_sha":SOURCE_SHA,"run_attempt":1,"status":"completed","conclusion":"success",
        "path":gates.WORKFLOW,"repository":repository,"head_repository":repository,
        "run_started_at":"2026-10-08T12:00:00Z","updated_at":"2026-10-08T12:20:00Z"}
    artifact={"id":123,"digest":reference["artifact_digest"],"size_in_bytes":400,"expired":False,
        "workflow_run":{"id":111,"head_sha":SOURCE_SHA,"repository_id":gates.REPOSITORY_ID,
            "head_repository_id":gates.REPOSITORY_ID},
        "created_at":"2026-10-08T12:19:00Z","expires_at":"2099-01-01T00:00:00Z"}
    return reference,artifact,run,copy.deepcopy(run)


def test_actions_digest_commit_tree_and_first_attempt_are_independent_of_success():
    reference,artifact,run,attempt=api_origin()
    gates.artifact_origin(reference,artifact,run,attempt,source_sha=SOURCE_SHA)
    artifact["digest"]="sha256:"+"d"*64
    with pytest.raises(ValueError,match="DIGEST_OR_ORIGIN"):
        gates.artifact_origin(reference,artifact,run,attempt,source_sha=SOURCE_SHA)


@pytest.mark.parametrize("field,value",[("conclusion","failure"),("status","queued"),
    ("head_sha","e"*40),("run_attempt",2),("path","unreviewed.yml")])
def test_api_origin_cannot_be_rebound_to_other_source_or_rerun(field,value):
    reference,artifact,run,attempt=api_origin()
    attempt[field]=value
    with pytest.raises(ValueError,match="ACTIONS_ORIGIN"):
        gates.artifact_origin(reference,artifact,run,attempt,source_sha=SOURCE_SHA)


def hosted_job():
    return {"total_count":1,"jobs":[{"id":789,"run_id":111,"status":"completed","conclusion":"success",
        "labels":["ubuntu-24.04"],"runner_group_name":"GitHub Actions","runner_name":"GitHub Actions 1000005391",
        "started_at":"2026-10-08T12:00:00Z","completed_at":"2026-10-08T12:20:00Z"}]}


@pytest.mark.parametrize("field,value",[("labels",["ubuntu-24.04-8core"]),("runner_group_name","Self-hosted"),
    ("runner_name","arbitrary-more-powerful"),("conclusion","failure"),("run_id",112)])
def test_runner_receipt_must_agree_with_actual_actions_job(field,value):
    jobs=hosted_job()
    assert gates.actions_runner_origin(jobs,run_id=111)["id"]==789
    jobs["jobs"][0][field]=value
    with pytest.raises(ValueError,match="CANONICAL_HOSTED_RUNNER"):
        gates.actions_runner_origin(jobs,run_id=111)


def test_concurrent_material_jobs_cannot_qualify_one_ordered_gate():
    jobs=hosted_job()
    jobs["jobs"].append(copy.deepcopy(jobs["jobs"][0]));jobs["total_count"]=2
    with pytest.raises(ValueError,match="SINGLE_ORDERED_JOB"):
        gates.actions_runner_origin(jobs,run_id=111)


def test_unmodified_original_template_acceptance_order_is_preserved():
    import runpy
    from scripts import rc6_controlled_governed_runner as runner
    root=Path(__file__).absolute().parents[1]
    original=root/runner.RECORD_WRAPPER_MEMBER
    assert hashlib.sha256(original.read_bytes()).hexdigest()==runner.RECORD_WRAPPER_SHA256
    guard=runpy.run_path(str(original))["static_native_source_barrier"]
    assert guard((root/"scripts/rc6_controlled_governed_runner.py").read_bytes())[
        "phase_finalization_guard_before_product_import_capture"] is True


def diagnostic_pack(tmp_path, count=3):
    from types import SimpleNamespace
    from scripts import rc6_material_carrier as carrier
    namespace=SimpleNamespace(path=tmp_path,binding={"candidate_sha":SOURCE_SHA,"candidate_tree":SOURCE_TREE})
    controls=tmp_path/"execution-fixture-lifecycle-controls";controls.mkdir(mode=0o700)
    selected=[]
    for index in range(count):
        path=controls/('%05d.json'%index);path.write_bytes(b'{"actual_control":true}\n')
        selected.append(path.relative_to(tmp_path).as_posix())
    paths=carrier.pack_phase_diagnostics(namespace,tmp_path,'execution',selected)
    archive,index=map(lambda name:tmp_path/name,paths)
    return archive,json.loads(index.read_bytes()),carrier


def test_more_than_4096_fixture_controls_remain_two_artifact_members_with_exact_raw(tmp_path):
    archive,index,carrier=diagnostic_pack(tmp_path,4097)
    assert carrier.verify_diagnostic_pack(archive,index)["member_count"]==4097
    assert index["original_bytes_rewritten"] is False and index["member_count"]==4097
    assert carrier.verify_diagnostic_pack(archive.read_bytes(),index)['member_count']==4097
    with zipfile.ZipFile(archive) as packed:
        assert packed.read(index["members"][4000]["path"])==b'{"actual_control":true}\n'


@pytest.mark.parametrize("field,value",[("member_count",2),("archive_sha256","0"*64),
    ("uncompressed_bytes",0)])
def test_diagnostic_index_counters_and_archive_digest_fail_closed(tmp_path,field,value):
    archive,index,carrier=diagnostic_pack(tmp_path)
    index[field]=value
    with pytest.raises(ValueError,match="DIAGNOSTIC_PACK"):
        carrier.verify_diagnostic_pack(archive,index)


def test_diagnostic_raw_member_hash_cannot_be_rebound(tmp_path):
    archive,index,carrier=diagnostic_pack(tmp_path)
    index["members"][1]["sha256"]="0"*64
    with pytest.raises(ValueError,match="MEMBER_HASH_MISMATCH"):
        carrier.verify_diagnostic_pack(archive,index)


def test_diagnostic_original_all11_is_preserved_and_size_bound(tmp_path):
    archive,index,carrier=diagnostic_pack(tmp_path)
    index['members'][0]['original_all11']['st_size']+=1
    with pytest.raises(ValueError,match='ORIGINAL_ALL11_REBOUND'):
        carrier.verify_diagnostic_pack(archive,index)


def test_actual_carrier_capacity_binding_is_accepted_by_productive_capacity_policy(monkeypatch):
    from types import SimpleNamespace
    from scripts import rc6_material_carrier as carrier
    from scripts import rc6_heavy_test_preflight as capacity
    monkeypatch.setenv('GITHUB_RUN_ID','111');monkeypatch.setenv('GITHUB_RUN_ATTEMPT','1')
    args=SimpleNamespace(source_sha=SOURCE_SHA,source_tree=SOURCE_TREE,
        owner_session='CODEX_RC6_ARCHITECTURAL_RCA_20261008_1205UTC')
    binding=carrier.capacity_binding(args,'BIG-browser')
    before=copy.deepcopy(binding)
    assert capacity.validate_binding(binding) is None
    assert before==binding
    assert binding['runner_class']=='github-hosted/ubuntu-24.04'


def carrier_capacity_fixture(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from scripts import rc6_material_carrier as carrier
    from scripts import rc6_heavy_test_preflight as capacity
    monkeypatch.setenv('GITHUB_RUN_ID','111');monkeypatch.setenv('GITHUB_RUN_ATTEMPT','1')
    args=SimpleNamespace(source_sha=SOURCE_SHA,source_tree=SOURCE_TREE,repo_root=Path(__file__).absolute().parents[1],
        owner_session='CODEX_RC6_ARCHITECTURAL_RCA_20261008_1205UTC')
    binding=carrier.capacity_binding(args,'focal311')
    measurement={'allocated_bytes':1024**3,'retained_entries':12,
        'assertion_scope':'CONTROLLED_UNIT_FIXTURE_ONLY_NOT_MATERIAL_PEAK_EVIDENCE'}
    peak={'schema':'porota.rc6.comparable-capacity-peak.v1',
        **{key:binding[key] for key in ('producer','runner_class','workload_fingerprint')},
        'peak_allocated_bytes':measurement['allocated_bytes'],'evidence':{
            'uri':'https://github.com/mbalbo2023/Porota-trading/issues/471',
            'measurement':measurement,'sha256':capacity.digest(measurement)}}
    args.capacity_peaks={'focal311':peak}
    controls=tmp_path/'controls';controls.mkdir()
    return args,SimpleNamespace(control=controls),carrier,capacity


def test_carrier_consumes_real_nested_capacity_receipt_then_rechecks_live(tmp_path,monkeypatch):
    args,run,carrier,capacity=carrier_capacity_fixture(tmp_path,monkeypatch)
    binding,peak,live=carrier.heavy_preflight(args,run,tmp_path,'focal311','real-unit-capacity')
    before=json.loads((run.control/'real-unit-capacity.capacity-before.json').read_bytes())
    saved=json.loads((run.control/'real-unit-capacity.capacity-live.json').read_bytes())
    assert 'status' not in before and 'status' not in live
    assert before['capacity']['status']==live['capacity']['status']=='GREEN'
    assert live==saved and live['binding']==binding and live['comparable_peak']==peak
    assert live['prior_receipt_sha256']==before['receipt_sha256']
    assert live['filesystem']['filesystem_device']==tmp_path.stat().st_dev
    assert before['receipt_sha256']==capacity.digest({key:value for key,value in before.items() if key!='receipt_sha256'})


def test_carrier_blocks_a_real_nested_red_capacity_result_before_live_or_producer(tmp_path,monkeypatch):
    args,run,carrier,capacity=carrier_capacity_fixture(tmp_path,monkeypatch)
    measured=capacity.measure_filesystem(tmp_path)
    measured['free_bytes']=0
    monkeypatch.setattr(capacity,'measure_filesystem',lambda _path:measured)
    def forbidden(**_kwargs):
        raise AssertionError('RED capacity cannot reach the live check or producer')
    monkeypatch.setattr(capacity,'validate_live_receipt',forbidden)
    with pytest.raises(ValueError,match='CAPACITY_PREFLIGHT_BLOCKED_BEFORE_PRODUCER'):
        carrier.heavy_preflight(args,run,tmp_path,'focal311','real-unit-red')
    before=json.loads((run.control/'real-unit-red.capacity-before.json').read_bytes())
    assert 'status' not in before and before['capacity']['status']=='BLOCKED'
    assert not (run.control/'real-unit-red.capacity-live.json').exists()


def carrier_capacity_v2_fixture(tmp_path,monkeypatch):
    args,run,carrier,capacity=carrier_capacity_fixture(tmp_path,monkeypatch)
    original_binding=carrier.capacity_binding
    def diagnostic_binding(args,producer):
        return dict(original_binding(args,producer),runner_class='DIAGNOSTIC')
    monkeypatch.setattr(carrier,'capacity_binding',diagnostic_binding)
    bound=carrier.capacity_binding(args,'focal311')
    measured={'allocated_bytes':1024**3,'retained_entries':12,
        'origin_runner_class':'DIAGNOSTIC','scope':'OWN_CLOSED_RETAINED_NAMESPACE_OR_OBSERVED_COMPONENTS',
        'assertion_scope':'CONTROLLED_UNIT_FIXTURE_ONLY_NOT_MATERIAL_PEAK_EVIDENCE'}
    uri='https://github.com/mbalbo2023/Porota-trading/issues/471'
    proof={'uri':uri,'sha256':'f'*64}
    peak={'schema':capacity.ENVELOPE_SCHEMA,'target':{key:bound[key]
        for key in ('producer','runner_class','workload_fingerprint')},
        'four_GiB_residual_reserve_excluded_from_envelope':True,
        'local_measurement_relabelled_as_target_runner':False,'reference_retention_GREEN_claimed':False,
        'temporal_peak_guarantee_claimed':False,
        'reference':{'measurement_kind':'OBSERVED_ALLOCATED_HIGH_WATER',
            'measurement_scope':measured['scope'],'measurement_complete':True,'runner_class':'DIAGNOSTIC',
            'producer':'CONTROLLED_UNIT_ONLY','source_sha':SOURCE_SHA,'source_tree':SOURCE_TREE,
            'allocated_bytes':measured['allocated_bytes'],'retained_entries':measured['retained_entries'],
            'evidence':{'uri':uri,'raw_member':'unit-only-measurement.json','raw_member_sha256':'1'*64,
                'container_sha256':'2'*64,'measurement_sha256':capacity.digest(measured),'measurement':measured}},
        'comparison':{'schema':'porota.rc6.capacity-comparison-proof.v1',
            'candidate_sha':SOURCE_SHA,'candidate_tree':SOURCE_TREE,
            'dominance':'REFERENCE_GRAPH_PLUS_ENUMERATED_BOUNDED_ADDITIONS',
            'source_manifest_sha256':'3'*64,'cheap_files_sha256':'4'*64,'producer_graph_sha256':proof['sha256'],
            'checks':{key:True for key in capacity.COMPARISON_CHECKS},'unknown_components':[],
            'reference_graph_evidence':proof.copy(),'target_graph_evidence':proof.copy(),
            'additional_bound_components':[{'name':'UNIT_ONLY_CONTROL_BOUND',
                'model':'CEIL4096_REGULAR_PAYLOAD_OR_EXACT_CODE_WRITE_BOUND',
                'assumptions':['CONTROLLED_UNIT_STRUCTURE_ONLY_NO_MATERIAL_CAPACITY_CLAIM'],
                'quantity':2,'unit_bound_bytes':4096,'allocated_bound_bytes':8192,'evidence':proof.copy()}]},
        'admission_envelope_bytes':measured['allocated_bytes']+8192}
    args.capacity_peaks={'focal311':peak}
    return args,run,carrier,capacity


def test_carrier_v2_real_live_receipt_keeps_original_allocation_distinct_from_admission_bound(tmp_path,monkeypatch):
    args,run,carrier,capacity=carrier_capacity_v2_fixture(tmp_path,monkeypatch)
    bound,peak,live=carrier.heavy_preflight(args,run,tmp_path,'focal311','real-unit-v2')
    assert 'peak_allocated_bytes' not in peak
    assert live['comparable_peak']['reference']['allocated_bytes']==1024**3
    assert live['capacity']['expected_peak_bytes']==1024**3+8192
    assert live['capacity']['required_free_bytes']==1024**3+8192+4*1024**3
    assert live['binding']==bound and live['comparable_peak']==peak
    assert capacity.envelope_allocated_bytes(peak)==peak['admission_envelope_bytes']
    assert not peak['local_measurement_relabelled_as_target_runner']


def test_archived_capacity_replay_checks_actual_before_live_hash_binding_and_arithmetic(tmp_path,monkeypatch):
    monkeypatch.setenv('GITHUB_ACTIONS','true')
    args,run,carrier,capacity=carrier_capacity_fixture(tmp_path,monkeypatch)
    _bound,_peak,live=carrier.heavy_preflight(args,run,tmp_path,'focal311','archived-real-unit')
    before=json.loads((run.control/'archived-real-unit.capacity-before.json').read_bytes())
    assert gates.verify_archived_capacity_pair(before,live,receipt('G0'),producer='focal311')==live
    for fault in ('digest','free_bytes','candidate','prior','boot','filesystem','stale'):
        broken=copy.deepcopy(live)
        if fault=='digest':broken['receipt_sha256']='0'*64
        elif fault=='free_bytes':broken['filesystem']['free_bytes']=0
        elif fault=='candidate':broken['binding']['candidate_sha']='c'*40
        elif fault=='prior':broken['prior_receipt_sha256']='d'*64
        elif fault=='boot':broken['measurement_boot_id']='different-original-kernel-boot'
        elif fault=='filesystem':broken['filesystem']['mount_id']+=1
        elif fault=='stale':broken['measured_at_unix_ns']=before['measured_at_unix_ns']+61*10**9
        if fault!='digest':broken['receipt_sha256']=capacity.digest({key:value for key,value in broken.items()
            if key!='receipt_sha256'})
        with pytest.raises(ValueError):
            gates.verify_archived_capacity_pair(before,broken,receipt('G0'),producer='focal311')


def test_real_diagnostic_v2_capacity_cannot_be_relabelled_as_a_canonical_g0_receipt(tmp_path,monkeypatch):
    monkeypatch.setenv('GITHUB_ACTIONS','true')
    args,run,carrier,capacity=carrier_capacity_v2_fixture(tmp_path,monkeypatch)
    _bound,_peak,live=carrier.heavy_preflight(args,run,tmp_path,'focal311','archived-real-diagnostic')
    before=json.loads((run.control/'archived-real-diagnostic.capacity-before.json').read_bytes())
    assert before['binding']['runner_class']==live['binding']['runner_class']=='DIAGNOSTIC'
    with pytest.raises(ValueError,match='ACTUAL_SOURCE_RUN_OR_WORKLOAD_REBOUND'):
        gates.verify_archived_capacity_pair(before,live,receipt('G0'),producer='focal311')


def test_g0_capacity_flag_alone_cannot_substitute_for_native_capacity_launch_inventory(tmp_path):
    archive=tmp_path/'no-native-capacity.zip'
    with zipfile.ZipFile(archive,'w'):pass
    with pytest.raises(ValueError,match='ACTUAL_CAPACITY_INDEX_REQUIRED'):
        gates.verify_g0_capacity_index({'status':'GREEN','capacity_preflight':True},receipt('G0'),archive)


def test_actual_producer_cannot_consume_a_stale_capacity_launch_proof(tmp_path):
    from scripts import rc6_material_carrier as carrier
    controls=tmp_path/'controls';controls.mkdir()
    calls=[]
    manager={'pre_capture_kernel_state':lambda:calls.append('read-kernel'),
        'managed_native_child':lambda *_args,**_kwargs:calls.append('forbidden-producer')}
    run=carrier.OwnedRunner(manager,controls)
    run.pending_capacity=[{'label':'expired-live-measurement','measured_at_unix_ns':carrier.time.time_ns()-61*10**9}]
    with pytest.raises(ValueError,match='CAPACITY_PROOF_STALE_BEFORE_ACTUAL_LAUNCH'):
        run(['never-launched'],cwd=tmp_path,label='tiny-stale-guard',limit=1)
    assert calls==['read-kernel'] and not (controls/'tiny-stale-guard.launch-intent.json').exists()


def test_g0_capacity_protocol_roundtrip_preserves_real_tiny_native_fin_and_launch_measurements(tmp_path,monkeypatch):
    """Exercise the protocol with tiny children, without claiming a prepared product."""
    import runpy
    import sys
    monkeypatch.setenv('GITHUB_ACTIONS','true')
    args,fixture_run,carrier,capacity=carrier_capacity_fixture(tmp_path,monkeypatch)
    original=copy.deepcopy(args.capacity_peaks['focal311'])
    for producer in ('bootstrap','full-gov311','full-gov312'):
        bound=carrier.capacity_binding(args,producer);peak=copy.deepcopy(original)
        peak.update({key:bound[key] for key in ('producer','runner_class','workload_fingerprint')})
        if producer=='bootstrap':peak['preparation_scope']=copy.deepcopy(carrier.PREPARATION_SCOPE)
        args.capacity_peaks[producer]=peak
    manager=runpy.run_path(str(args.repo_root/'scripts/rc6_controlled_native_child_manager.py'))
    run=carrier.OwnedRunner(manager,fixture_run.control)
    temporary=tmp_path/'tiny-native-temporary';temporary.mkdir(mode=0o700)
    labels=('venv311','venv312','locked311-0','locked311-1','locked312-0','locked312-1',
        'fullGit-original-inputs','G0-fsck311','G0-fsck312','G0-admission311','G0-admission312')
    for label in labels:
        if label.startswith('G0-admission'):
            carrier.heavy_preflight(args,run,tmp_path,'full-gov'+label[-3:],'unit-'+label)
        else:
            carrier.preparation_capacity(args,run,tmp_path,'unit-'+label,
                additional_storage=(('pip_temporary',temporary),))
        actual=run([sys.executable,'-I','-B','-c','pass'],cwd=tmp_path,label=label,limit=5)
        assert actual['returncode']==0 and actual['kernel']['actual_child_reaped']
    commands=[]
    for label in labels:
        intent=run.control/(label+'.launch-intent.json');kernel=run.control/(label+'.kernel.json')
        commands.append({'label':label,'intent':{'path':'controls/'+intent.name,'sha256':hashlib.sha256(carrier.read(intent)).hexdigest()},
            'kernel':{'path':'controls/'+kernel.name,'sha256':hashlib.sha256(carrier.read(kernel)).hexdigest()}})
    transition={'schema':'rc6.capacity-guarded-full-git-transition.v1','source_sha':SOURCE_SHA,'source_tree':SOURCE_TREE,
        'bootstrap_was_shallow':False,'full_git_after_guarded_fetch':True,'same_HEAD_tree_unchanged':True,
        'native_fetch_label':None,'assertion_scope':'UNIT_PROTOCOL_ONLY_NO_REAL_PRODUCT_FULL_GIT_CLAIM'}
    transition_sha=carrier.save(run.control/'fullGit-bootstrap-to-qualified.json',transition)
    archive=tmp_path/'tiny-native-protocol.zip'
    with zipfile.ZipFile(archive,'w') as packed:
        for path in sorted(run.control.iterdir()):packed.writestr('controls/'+path.name,carrier.read(path))
    index={'schema':'rc6.G0-actual-capacity-launch-index.v1','source_sha':SOURCE_SHA,'source_tree':SOURCE_TREE,
        'capacity_records':run.capacity_records,'commands':commands,'preparation_scope':carrier.PREPARATION_SCOPE,
        'full_git_transition':{'path':'controls/fullGit-bootstrap-to-qualified.json','sha256':transition_sha},
        'assertion_scope':'UNIT_PROTOCOL_ONLY_NOT_A_REAL_VENV_PIP_FULLSOURCE_OR_G0_PASS'}
    result=gates.verify_g0_capacity_index(index,receipt('G0'),archive)
    assert result['actual_native_commands']==11 and result['actual_capacity_proofs']==29
    assert result['promotion_claimed'] is False
    omitted=copy.deepcopy(index);omitted['commands']=omitted['commands'][:-1]
    with pytest.raises(ValueError,match='PRODUCER_OR_FIN_NOT_VERIFIED'):
        gates.verify_g0_capacity_index(omitted,receipt('G0'),archive)


def test_bootstrap_unknown_peak_blocks_before_any_venv_or_pip_creation(tmp_path,monkeypatch):
    args,run,carrier,_capacity=carrier_capacity_fixture(tmp_path,monkeypatch)
    args.python311='controlled-never-launched';args.python312='controlled-never-launched'
    def forbidden(*_args,**_kwargs):
        raise AssertionError('Unknown bootstrap peak must block before a preparation command')
    with pytest.raises(ValueError,match='COMPARABLE_BOOTSTRAP_PEAK_REQUIRED_BEFORE_PREPARATION'):
        carrier.installed_env(args,forbidden,tmp_path)
    assert not (tmp_path/'product311').exists() and not (tmp_path/'product312').exists()


def test_bootstrap_unknown_peak_blocks_full_git_fetch_before_any_git_mutation(tmp_path,monkeypatch):
    args,run,carrier,_capacity=carrier_capacity_fixture(tmp_path,monkeypatch)
    def forbidden(*_args,**_kwargs):raise AssertionError('Unknown capacity cannot run a full fetch')
    monkeypatch.setattr(carrier,'git',forbidden)
    with pytest.raises(ValueError,match='COMPARABLE_BOOTSTRAP_PEAK_REQUIRED_BEFORE_PREPARATION'):
        carrier.complete_full_git(args,run,tmp_path)
    assert not (tmp_path/'full-git-askpass.py').exists()

def real_insufficient_peak(args,carrier,capacity,path,producer):
    binding=carrier.capacity_binding(args,producer)
    measured=capacity.measure_filesystem(path)
    measurement={'allocated_bytes':measured['free_bytes']+1,'retained_entries':1,
        'assertion_scope':'UNIT_REJECTION_BOUND_ONLY_NOT_MEASURED_MATERIAL_PEAK'}
    return {'schema':'porota.rc6.comparable-capacity-peak.v1',
        **{key:binding[key] for key in ('producer','runner_class','workload_fingerprint')},
        'peak_allocated_bytes':measurement['allocated_bytes'],'preparation_scope':copy.deepcopy(carrier.PREPARATION_SCOPE),
        'evidence':{'uri':'https://github.com/mbalbo2023/Porota-trading/issues/471',
            'measurement':measurement,'sha256':capacity.digest(measurement)}}

def git_fixture_files(root,carrier):
    return {path.relative_to(root).as_posix():{'bytes_sha256':hashlib.sha256(carrier.read(path)).hexdigest(),
        'stat':tuple(getattr(path.stat(),name) for name in carrier.FIELDS)}
        for path in sorted((root/'.git').rglob('*')) if path.is_file()}

def test_insufficient_live_capacity_physically_blocks_full_git_fetch_and_preserves_git(tmp_path,monkeypatch,tiny_frozen_source):
    args,fixture,carrier,capacity=carrier_capacity_fixture(tmp_path,monkeypatch)
    root,sha,tree,*_=tiny_frozen_source;args.repo_root=root;args.source_sha=sha;args.source_tree=tree
    policy=capacity.load_policy();monkeypatch.setattr(capacity,'load_policy',lambda _path=None:policy)
    args.capacity_peaks['bootstrap']=real_insufficient_peak(args,carrier,capacity,tmp_path,'bootstrap')
    before=git_fixture_files(root,carrier);calls=[];sentinel=tmp_path/'FETCH_WAS_CALLED'
    class FetchSentinel:
        control=fixture.control
        def __call__(self,*_args,**_kwargs):
            calls.append('fetch');sentinel.write_bytes(b'physical launch reached');raise AssertionError('fetch must remain NOT_CALLED')
    with pytest.raises(ValueError,match='CAPACITY_PREFLIGHT_BLOCKED_BEFORE_PRODUCER'):
        carrier.complete_full_git(args,FetchSentinel(),tmp_path)
    receipt=json.loads(next(fixture.control.glob('*.capacity-before.json')).read_bytes())
    assert receipt['capacity']['status']=='BLOCKED'
    assert receipt['filesystem']['free_bytes']<receipt['comparable_peak']['peak_allocated_bytes']+4*1024**3
    assert calls==[] and not sentinel.exists() and not (tmp_path/'full-git-askpass.py').exists()
    assert git_fixture_files(root,carrier)==before

def test_insufficient_live_capacity_physically_blocks_g7_full_git_fetch_and_preserves_git(tmp_path,monkeypatch,tiny_frozen_source):
    args,_fixture,carrier,capacity=carrier_capacity_fixture(tmp_path,monkeypatch)
    root,sha,tree,*_=tiny_frozen_source;args.repo_root=root;args.source_sha=sha;args.source_tree=tree
    from scripts import porota_predeploy_cleanup as cleanup
    policy=capacity.load_policy();monkeypatch.setattr(capacity,'load_policy',lambda _path=None:policy)
    peak=real_insufficient_peak(args,carrier,capacity,tmp_path,'predeploy')
    context={'candidate_sha':sha,'candidate_tree':tree};owner={'private_root':str(tmp_path)}
    monkeypatch.setattr(cleanup,'execution_context',lambda *_args:context)
    monkeypatch.setattr(cleanup,'cli_owner',lambda *_args:(owner,{}))
    monkeypatch.setattr(gates.subprocess,'check_output',lambda *_args,**_kwargs:str(tmp_path))
    before=git_fixture_files(root,carrier);calls=[];sentinel=tmp_path/'G7_FETCH_WAS_CALLED'
    def forbidden(*_args,**_kwargs):
        calls.append('fetch');sentinel.write_bytes(b'physical launch reached');raise AssertionError('G7 fetch must remain NOT_CALLED')
    monkeypatch.setattr(carrier,'OwnedRunner',forbidden)
    authority={'status':'ADMITTED_NATIVE_NOT_STARTED','gate':'predeploy','source_sha':sha,'source_tree':tree,
        'owner_session':args.owner_session,'capacity_peaks':{'predeploy':peak}}
    with pytest.raises(ValueError,match='CAPACITY_'):
        gates.complete_predeploy_full_git(authority,scope=tmp_path,repo=root,environ={})
    receipt=json.loads((tmp_path/'porota-g7-full-git-workspace-capacity-before.json').read_bytes())
    assert receipt['capacity']['status']=='BLOCKED'
    assert receipt['filesystem']['free_bytes']<receipt['comparable_peak']['peak_allocated_bytes']+4*1024**3
    assert calls==[] and not sentinel.exists() and not (tmp_path/'g7-full-git-askpass.py').exists()
    assert git_fixture_files(root,carrier)==before


@pytest.fixture
def tiny_frozen_source(tmp_path):
    import subprocess
    from scripts import rc6_material_pr_admission as admission
    root=tmp_path/'tiny-frozen-Git';root.mkdir()
    (root/'plain.py').write_bytes(b'VALUE = 1\n')
    (root/'entry.py').write_bytes(b'ENTRY = True\n');(root/'entry.py').chmod(0o755)
    def git(*args):return subprocess.run(['git','-C',str(root),*args],check=True,capture_output=True).stdout
    git('init','-q');git('add','.');git('-c','user.name=RC6 unit','-c','user.email=unit@example.invalid',
        'commit','-q','--no-gpg-sign','-m','Controlled unit only')
    sha=git('rev-parse','HEAD').decode().strip();tree=git('rev-parse','HEAD^{tree}').decode().strip()
    inventory=admission.frozen_source_inventory(root,sha,tree)
    cheap=sorted(gates.G1_REQUIRED_CHEAP_FILES)
    manifest={'schema':admission.CAPACITY_COMPARISON_MANIFEST_SCHEMA,'source_sha':sha,'source_tree':tree,
        'source_manifest_sha256':hashlib.sha256(admission.wire(inventory)).hexdigest(),
        'cheap_files_sha256':hashlib.sha256(admission.wire(cheap)).hexdigest(),
        'status':'BLOCKED_UNKNOWN_COMPONENTS','unknown_components':['bootstrap_sdists_temporal_allocation'],
        'records':[]}
    fields={'SOURCE_MANIFEST_SHA256':manifest['source_manifest_sha256'],'CHEAP_FILES_JSON':json.dumps(cheap),
        'CAPACITY_PEAKS_JSON':'{}','CAPACITY_COMPARISON_MANIFEST_JSON':json.dumps(manifest)}
    return root,sha,tree,inventory,manifest,fields,admission


def test_source_manifest_uses_frozen_git_bytes_modes_and_oids_not_workspace_attributes(tiny_frozen_source):
    root,sha,tree,inventory,_manifest,_fields,admission=tiny_frozen_source
    records={row['path']:row for row in inventory['files']}
    assert records['entry.py']['mode']=='100755' and records['plain.py']['mode']=='100644'
    (root/'entry.py').chmod(0o600);(root/'plain.py').write_bytes(b'DIRTY_WORKSPACE_NOT_FROZEN_SOURCE\n')
    assert admission.frozen_source_inventory(root,sha,tree)==inventory
    assert records['plain.py']['sha256']==hashlib.sha256(b'VALUE = 1\n').hexdigest()


def test_unknown_capacity_comparison_stops_before_bootstrap_despite_exact_source_manifest(tiny_frozen_source):
    root,sha,tree,_inventory,_manifest,fields,admission=tiny_frozen_source
    with pytest.raises(ValueError,match='UNKNOWN_COMPONENTS_BEFORE_BOOTSTRAP'):
        admission.verify_capacity_comparison_manifest(fields,sha,tree,repo=root)


@pytest.mark.parametrize('fault',['source','cheap','tree'])
def test_capacity_manifest_requires_actual_frozen_source_and_reviewed_cheap_fingerprints(tiny_frozen_source,fault):
    root,sha,tree,_inventory,manifest,fields,admission=tiny_frozen_source
    if fault=='source':
        fields['SOURCE_MANIFEST_SHA256']=manifest['source_manifest_sha256']='0'*64
    elif fault=='cheap':manifest['cheap_files_sha256']='0'*64
    elif fault=='tree':manifest['source_tree']='0'*40
    fields['CAPACITY_COMPARISON_MANIFEST_JSON']=json.dumps(manifest)
    with pytest.raises(ValueError,match='DIGEST_MISMATCH|FINGERPRINT_MISMATCH|MANIFEST_REQUIRED'):
        admission.verify_capacity_comparison_manifest(fields,sha,tree,repo=root)


def test_self_declared_verified_hash_only_comparison_never_authorizes_v2(tiny_frozen_source):
    root,sha,tree,_inventory,manifest,fields,admission=tiny_frozen_source
    manifest.update(status='VERIFIED',unknown_components=[],records=[])
    fields['CAPACITY_COMPARISON_MANIFEST_JSON']=json.dumps(manifest)
    with pytest.raises(ValueError,match='HASH_ONLY_COMPARISON_EVIDENCE_BLOCKED'):
        admission.verify_capacity_comparison_manifest(fields,sha,tree,repo=root)


def test_verified_inline_coherence_does_not_replace_original_git_transport_and_exhaustive_graph(tiny_frozen_source):
    root,sha,tree,_inventory,manifest,fields,admission=tiny_frozen_source
    raw='Controlled unit RAW only; no temporal peak or external authenticity claim.'
    manifest.update(status='VERIFIED',unknown_components=[],records=[{'uri':
        'https://github.com/mbalbo2023/Porota-trading/blob/'+sha+'/plain.py',
        'sha256':hashlib.sha256(raw.encode()).hexdigest(),'raw_utf8':raw}])
    fields['CAPACITY_COMPARISON_MANIFEST_JSON']=json.dumps(manifest)
    with pytest.raises(ValueError,match='VERIFIED_COMPARISON_PROOF_BLOCK_UNSUPPORTED'):
        admission.verify_capacity_comparison_manifest(fields,sha,tree,repo=root)

def diagnostic_unit_launch(tiny_frozen_source,monkeypatch,gate='capacity-probe'):
    root,sha,tree,_inventory,manifest,_fields,admission=tiny_frozen_source
    monkeypatch.setattr(gates,'productive_contract',lambda _root:CONTRACT)
    image,hard_limit=(5*1024**3,512*1024**2) if gate=='capacity-probe' else (26*1024**3,20*1024**3)
    scope={'schema':'porota.rc6.capacity-diagnostic-scope.v1','mode':gate,'backing_image_bytes':image,
        'project_hard_limit_bytes':hard_limit,'residual_reserve_bytes':4*1024**3,
        'financial_tick_allowed':False,'qualification_claimed':False}
    fields={'RC6_CAPACITY_DIAGNOSTIC_AUTHORIZATION':'APPROVED','WORKSTREAM_ID':admission.WORKSTREAM,
        'DIAGNOSTIC_MODE':gate,'SOURCE_WIP':admission.DIAGNOSTIC_BRANCH,'SOURCE_SHA':sha,'SOURCE_TREE':tree,
        'SESSION_SUCCESSOR':admission.SUCCESSOR_OWNER,'WRITE_OWNER':admission.SUCCESSOR_OWNER,
        'INTEGRATION_OWNER':admission.SUCCESSOR_OWNER,'DEPLOY_OWNER':'NOT_ACQUIRED','RELEASED':'false',
        'MODE':'PRODUCTION_PAPER / SIMULATION','real_orders_sent':'0','READ_CONTRACT_SHA256':CONTRACT,
        'SOURCE_MANIFEST_SHA256':manifest['source_manifest_sha256'],
        'CAPACITY_DIAGNOSTIC_SCOPE_JSON':json.dumps(scope),'CAPACITY_DIAGNOSTIC_PREREQUISITES_JSON':'null'}
    row={'user':{'login':'mbalbo2023'},'issue_url':'https://api.github.com/repos/'+admission.REPO+'/issues/471',
        'created_at':'2026-10-08T16:30:00Z','updated_at':'2026-10-08T16:30:00Z',
        'body':'\n'.join(key+'='+value for key,value in fields.items())+'\n'}
    return root,sha,tree,row,scope,fields,admission

def test_diagnostic_authority_is_exact_wip_source_and_explicitly_not_candidate_qualification(tiny_frozen_source,monkeypatch):
    root,sha,tree,row,scope,_fields,admission=diagnostic_unit_launch(tiny_frozen_source,monkeypatch)
    _auth,observed,prior=admission.diagnostic_launch_fields(row,sha,tree,admission.SUCCESSOR_OWNER,'capacity-probe',repo=root)
    assert observed==scope and prior is None and observed['qualification_claimed'] is False
    assert set(admission.DIAGNOSTIC_GATES).isdisjoint(gates.GATES)
    assert scope['backing_image_bytes']!=scope['project_hard_limit_bytes']

@pytest.mark.parametrize('fault',['edited','owner','source','tree','contract','manifest','branch','deploy'])
def test_diagnostic_owner_hash_or_mode_cannot_be_rebound(tiny_frozen_source,monkeypatch,fault):
    root,sha,tree,row,_scope,fields,admission=diagnostic_unit_launch(tiny_frozen_source,monkeypatch)
    if fault=='edited':row['updated_at']='2026-10-08T16:31:00Z'
    else:
        key={'owner':'WRITE_OWNER','source':'SOURCE_SHA','tree':'SOURCE_TREE','contract':'READ_CONTRACT_SHA256',
            'manifest':'SOURCE_MANIFEST_SHA256','branch':'SOURCE_WIP','deploy':'DEPLOY_OWNER'}[fault]
        fields[key]='wrong';row['body']='\n'.join(key+'='+value for key,value in fields.items())+'\n'
    with pytest.raises(ValueError,match='DIAGNOSTIC|CONTRACT_REBOUND|SOURCE_MANIFEST'):
        admission.diagnostic_launch_fields(row,sha,tree,admission.SUCCESSOR_OWNER,'capacity-probe',repo=root)

@pytest.mark.parametrize('field,value',[('backing_image_bytes',6*1024**3),('project_hard_limit_bytes',1024**3),
    ('residual_reserve_bytes',0),('financial_tick_allowed',True),('qualification_claimed',True)])
def test_diagnostic_outer_backing_inner_project_and_reserve_cannot_be_relaxed(tiny_frozen_source,monkeypatch,field,value):
    _root,_sha,_tree,_row,scope,_fields,admission=diagnostic_unit_launch(tiny_frozen_source,monkeypatch)
    scope[field]=value
    with pytest.raises(ValueError,match='QUOTA_SCOPE_REBOUND'):admission.diagnostic_scope('capacity-probe',scope)

def test_diagnostic_bootstrap_cannot_start_without_capability_artifact_reference(tiny_frozen_source,monkeypatch):
    root,sha,tree,row,_scope,_fields,admission=diagnostic_unit_launch(tiny_frozen_source,monkeypatch,'capacity-calibration')
    with pytest.raises(ValueError,match='ACTUAL_CAPABILITY_ARTIFACT'):
        admission.diagnostic_launch_fields(row,sha,tree,admission.SUCCESSOR_OWNER,'capacity-calibration',repo=root)

@pytest.mark.parametrize('field,value',[('GITHUB_RUN_ATTEMPT','2'),('GITHUB_SHA','c'*40),
    ('GITHUB_WORKFLOW_SHA','d'*40),('RUNNER_ENVIRONMENT','self-hosted')])
def test_diagnostic_rerun_or_alternate_runner_blocks_before_any_actions_payload_read(monkeypatch,field,value):
    from scripts import rc6_material_pr_admission as admission
    environment={'GITHUB_EVENT_NAME':'workflow_dispatch','GITHUB_RUN_ATTEMPT':'1','GITHUB_SHA':SOURCE_SHA,
        'GITHUB_WORKFLOW_SHA':SOURCE_SHA,'GITHUB_REF':'refs/heads/'+admission.DIAGNOSTIC_BRANCH,
        'GITHUB_WORKFLOW_REF':admission.REPO+'/.github/workflows/rc6-unified-candidate-tests.yml@refs/heads/'+admission.DIAGNOSTIC_BRANCH,
        'RUNNER_ENVIRONMENT':'github-hosted','RUNNER_OS':'Linux','RUNNER_ARCH':'X64','GITHUB_RUN_ID':'111'}
    for key,content in environment.items():monkeypatch.setenv(key,content)
    monkeypatch.setenv(field,value);reads=[]
    monkeypatch.setattr(admission,'api',lambda path:reads.append(path))
    with pytest.raises(ValueError,match='FIRST_ATTEMPT_STANDARD_ACTIONS'):
        admission.diagnostic_actions_origin(SOURCE_SHA,'capacity-probe')
    assert reads==[]

@pytest.mark.parametrize('moved',[476,477])
def test_diagnostic_never_moves_or_accepts_a_changed_candidate_pr(monkeypatch,moved):
    from scripts import rc6_material_pr_admission as admission
    def get(path):
        if path=='':return {'id':admission.REPO_ID,'full_name':admission.REPO,'private':False}
        if path.startswith('/git/ref/'):return {'object':{'sha':SOURCE_SHA}}
        if path=='/git/commits/'+SOURCE_SHA:
            return {'sha':SOURCE_SHA,'tree':{'sha':SOURCE_TREE},'parents':[{'sha':admission.RECOVERY_HEAD}]}
        if path=='/git/commits/'+admission.RECOVERY_HEAD:
            return {'sha':admission.RECOVERY_HEAD,'tree':{'sha':admission.RECOVERY_TREE}}
        number=int(path.rsplit('/',1)[1]);head=admission.RECOVERY_HEAD if number==476 else admission.GUARDS_HEAD
        return {'number':number,'state':'open','draft':True,'merged':False,'merged_at':None,
            'user':{'login':'mbalbo2023'},'head':{'sha':'f'*40 if number==moved else head,
                'ref':admission.BRANCH if number==476 else 'governance/rc6-error-learning-runner-20261007',
                'repo':{'id':admission.REPO_ID}},'base':{'ref':admission.BASE if number==476 else admission.BRANCH}}
    monkeypatch.setattr(admission,'api',get)
    with pytest.raises(ValueError,match='MUST_NOT_MOVE_OR_PROMOTE'):
        admission.fresh_diagnostic_source(SOURCE_SHA,SOURCE_TREE)

def test_candidate_tooling_download_is_blocked_without_a_comparison_that_accounts_for_toolcache(tmp_path,monkeypatch):
    from scripts import rc6_material_carrier as carrier
    from scripts import rc6_material_pr_admission as admission
    path=tmp_path/'admission.json'
    row={'status':'ADMITTED_NATIVE_NOT_STARTED','source_sha':SOURCE_SHA,'source_tree':SOURCE_TREE,
        'launch_receipt_url':'unit-only','owner_session':'unit-only','gate':'cheap','capacity_peaks':{}}
    carrier.save(path,row);monkeypatch.setattr(admission,'admit',lambda **_kwargs:row)
    with pytest.raises(ValueError,match='PYTHON_TOOLING_CAPACITY_UNKNOWN_BEFORE_ACTION_DOWNLOAD'):
        carrier.tooling_capacity(path,tmp_path,{})
    assert not (tmp_path/'tooling-controls').exists()

def test_actual_diagnostic_parent_binding_is_accepted_by_calibration_admission_without_sdk_or_quota_launch(tmp_path,monkeypatch):
    args,_run,carrier,_capacity=carrier_capacity_fixture(tmp_path,monkeypatch)
    from scripts import rc6_material_pr_admission as admission
    from scripts import rc6_capacity_calibration as calibration
    monkeypatch.setenv('GITHUB_REPOSITORY',admission.REPO)
    binding=carrier.capacity_binding(args,'capacity-probe')
    scope={'schema':'porota.rc6.capacity-diagnostic-scope.v1','mode':'capacity-probe',
        'backing_image_bytes':5*1024**3,'project_hard_limit_bytes':512*1024**2,'residual_reserve_bytes':4*1024**3,
        'financial_tick_allowed':False,'qualification_claimed':False}
    authority={'schema':'porota.rc6.capacity-diagnostic-admission.v1','status':'ADMITTED_DIAGNOSTIC_NOT_STARTED',
        'gate':'capacity-probe','source_sha':args.source_sha,'source_tree':args.source_tree,
        'owner_session':args.owner_session,'scope':scope,'qualification_claimed':False,'material_gates':[],
        'real_orders_sent':0,'mode':'PRODUCTION_PAPER / SIMULATION','real_routes':'NOT_CALLED','ppi_watch':'UNTOUCHED',
        'DEPLOY_OWNER':'NOT_ACQUIRED','actions_origin':{'run':{'id':111}}}
    assert calibration.verify_admission(authority,binding,'capability')==authority
    assert calibration.limits_for('capability')=={key:scope[key] for key in
        ('backing_image_bytes','project_hard_limit_bytes','residual_reserve_bytes')}
    assert binding['owner_id']==authority['owner_session'] and 'owner' not in binding

def unit_probe_observations():
    import errno
    checks={name:{'errno':errno.EPERM,'denied':True} for name in
        ('project_id_change','inheritance_clear','setflags','quota_mutation')}
    checks.update({name:{'return':-1,'errno':errno.EPERM,'denied':True} for name in
        ('high32_project_id_change','high32_setflags','high32_prctl')})
    checks.update(edquot={'errno':errno.EDQUOT,'actual_positive':True,'probe_limit_bytes':1024**2},
        outside_write={'errno':errno.EROFS,'readonly':True},supervisor_fd_escape={'errno':errno.EACCES,'denied':True},
        original_issuer_root_escape={'errno':errno.EACCES,'denied':True,'issuer_pid':123,
            'operation':'open_own_namespace_through_original_issuer_root'})
    return {'actual_positive':True,'same_uid':1001,'checks':checks,'project_attributes_unchanged':True,
        'source_financial_code_called':False}, {'uid':1001,'capabilities_zero':True,'no_new_privileges':True,
            'seccomp_mode':2,'mount_namespace_inode':1234}

def test_diagnostic_errno_raw_validation_never_claims_actual_probe_execution():
    probe,privilege=unit_probe_observations()
    result=gates.verify_capacity_probe_observations(probe,privilege)
    assert result=={'actual_observations_coherent':True,'qualification_claimed':False}

@pytest.mark.parametrize('fault',['edquot','outside_write','supervisor_fd_escape','project_id_change','inheritance_clear',
    'original_issuer_root_escape','high32_project_id_change','high32_setflags','high32_prctl',
    'setflags','quota_mutation','source_financial_code_called','project_attributes_unchanged',
    'uid','capabilities_zero','no_new_privileges','seccomp_mode','mount_namespace_inode'])
def test_diagnostic_green_boolean_cannot_replace_real_errno_privilege_and_escape_controls(fault):
    probe,privilege=unit_probe_observations()
    if fault in probe['checks']:probe['checks'][fault]['errno']=0
    elif fault in probe:probe[fault]=not probe[fault]
    elif fault in ('uid','seccomp_mode','mount_namespace_inode'):privilege[fault]=0
    else:privilege[fault]=False
    with pytest.raises(ValueError,match='CAPABILITY_'):
        gates.verify_capacity_probe_observations(probe,privilege)


def test_native_epoch_peak_cannot_be_assumed_to_cover_compound_bootstrap(tmp_path,monkeypatch):
    args,run,carrier,_capacity=carrier_capacity_fixture(tmp_path,monkeypatch)
    args.capacity_peaks['bootstrap']=copy.deepcopy(args.capacity_peaks['focal311'])
    with pytest.raises(ValueError,match='BOOTSTRAP_PEAK_COMPLETE_PREPARATION_SCOPE_NOT_VERIFIED'):
        carrier.preparation_capacity(args,run,tmp_path,'unit-bootstrap')
    assert not tuple(run.control.glob('unit-bootstrap*.json'))


def test_bootstrap_measures_real_source_and_namespace_filesystems_before_preparation(tmp_path,monkeypatch):
    args,run,carrier,capacity=carrier_capacity_fixture(tmp_path,monkeypatch)
    bound=carrier.capacity_binding(args,'bootstrap')
    peak=copy.deepcopy(args.capacity_peaks['focal311'])
    peak.update({key:bound[key] for key in ('producer','runner_class','workload_fingerprint')})
    peak['preparation_scope']={'locked_python_epochs':['311','312'],'installed_distribution_count_per_epoch':157,
        'original_git_objects':19,'full_git':True,'full_source':True,
        'pip_fetch_source_and_git_preparation_included':True}
    args.capacity_peaks['bootstrap']=peak
    temporary=tmp_path/'owned-bootstrap-temporary';temporary.mkdir(mode=0o700)
    actual=carrier.preparation_capacity(args,run,tmp_path,'real-unit-bootstrap',
        additional_storage=(('pip_temporary',temporary),))
    assert [item['storage_role'] for item in actual]==['bootstrap_namespace','candidate_git_objects','pip_temporary']
    assert [item['measured_path'] for item in actual]==list(map(str,[tmp_path,args.repo_root,temporary]))
    assert all(item['binding']==bound and 'status' not in item['capacity_live']
        and item['capacity_live']['capacity']['status']=='GREEN'
        and item['capacity_live']['filesystem']['filesystem_device']==Path(item['measured_path']).stat().st_dev
        for item in actual)
    assert len(tuple(run.control.glob('real-unit-bootstrap-*.capacity-live.json')))==3


def test_diagnostic_unsafe_members_are_blocked_even_if_outer_digest_matches(tmp_path):
    from scripts import rc6_material_carrier as carrier
    archive=tmp_path/'unsafe.raw'
    item=zipfile.ZipInfo('../foreign.json');item.create_system=3;item.external_attr=0o100600<<16
    with zipfile.ZipFile(archive,'w') as packed:packed.writestr(item,b'{}')
    index={"schema":"rc6.phase-diagnostics-lossless-pack.v1","member_count":1,"uncompressed_bytes":2,
        "archive_sha256":hashlib.sha256(archive.read_bytes()).hexdigest(),
        "members":[{"path":"../foreign.json","bytes":2,"sha256":hashlib.sha256(b'{}').hexdigest()}]}
    with pytest.raises(ValueError,match="UNSAFE_OR_REBOUND_MEMBER"):
        carrier.verify_diagnostic_pack(archive,index)


def test_focal_classification_preserves_big_as_final_full_governed_obligation():
    node = next(iter(focal.MATERIAL_NODES))
    assert focal.classify_node(node)["stage"] == "G6"
    assert focal.classify_node(node + "[parameter]")["stage"] == "G6"
    assert focal.classify_node("tests/test_issue465_stress.py::test_small") is None


def source_receipts():
    receipt = {"primary_captures": 1, "read_contract_sha256": CONTRACT, "source_unchanged": True,
        "cleanup_complete": True, "capture_seconds": .01, "verification_seconds": .01, "cleanup_seconds": .01,
        "private_image_guard": "rc6.immutable-private-image.v1", "private_image_unchanged": True,
        "private_image_sha256": {"": "3" * 64},
        "source_path_sha256": "1" * 64, "additional_captures": [],
        "query_consumers": [{"consumer": "families", "shared_primary_capture": True, "query_seconds": .1}]}
    return {"source_read_receipts": [{"phase": phase, "receipt": copy.deepcopy(receipt)}
        for phase in ("BOUNDED_READ", "PREOPEN", "OPEN")]}


def test_one_productive_capture_per_tick_is_materially_required():
    shadow = source_receipts()
    assert all(big.source_capture_checks(shadow, DEFAULT_READ_CONTRACT).values())
    shadow["source_read_receipts"][1]["receipt"]["primary_captures"] = 7
    assert big.source_capture_checks(shadow, DEFAULT_READ_CONTRACT)["source_snapshot_single_capture_per_tick"] is False


@pytest.mark.parametrize("field,value", [("read_contract_sha256", "2" * 64), ("source_unchanged", False),
    ("cleanup_complete", False), ("capture_seconds", 2.1), ("private_image_unchanged", False),
    ("private_image_guard", "none"), ("private_image_sha256", {"": "wrong"})])
def test_source_contract_and_custody_are_checked_for_each_cycle(field, value):
    shadow = source_receipts()
    shadow["source_read_receipts"][2]["receipt"][field] = value
    assert not all(big.source_capture_checks(shadow, DEFAULT_READ_CONTRACT).values())


def test_fixture_only_family_budget_cannot_promote_native_big():
    shadow = source_receipts()
    shadow["source_read_receipts"][0]["receipt"]["query_consumers"][0]["query_seconds"] = .51
    assert big.source_capture_checks(shadow, DEFAULT_READ_CONTRACT)["source_queries_obey_productive_budgets"] is False


def test_original_point_five_guard_reads_real_default_contract():
    root = Path(__file__).absolute().parents[1]
    class Reader:
        require = staticmethod(gates.require)
        capture = staticmethod(lambda path: (path.read_bytes(), {}))
    proof = big.original_families_budget(Reader, root)
    assert proof["query_budget_seconds"] == .5 and proof["productive_contract_sha256"] == CONTRACT


def test_automatic_push_is_cheap_and_predeploy_only_ready_with_bound_g6():
    import yaml
    root = Path(__file__).absolute().parents[1]
    predeploy = yaml.safe_load((root / gates.PREDEPLOY).read_text())
    carrier = yaml.safe_load((root / gates.WORKFLOW).read_text())
    assert predeploy.get("on", predeploy.get(True))["pull_request"]["types"] == ["ready_for_review"]
    assert "ready_for_review" not in carrier.get("on", carrier.get(True))["pull_request"]["types"]
    steps = predeploy["jobs"]["artifact-gate"]["steps"]
    admission = next(i for i, step in enumerate(steps) if "G0 through G6" in step.get("name", ""))
    bootstrap = next(i for i, step in enumerate(steps) if "Initialize proven private" in step.get("name", ""))
    assert admission < bootstrap
    assert "--target-gate G7" in steps[admission]["run"]
    external = next(step for step in steps if "external G6" in step.get("name", ""))
    assert "import_verified_governed" in external["run"] and "supervise-pytest" not in external["run"]
