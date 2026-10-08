"""Verify ordered, exact-Source RC6 gates from authenticated Actions bytes.

A workflow conclusion is transport evidence only. Native receipts, collection,
JUnit, FIN and material resource controls must independently agree. This module
does not dispatch a workflow, edit GitHub, acquire DEPLOY_OWNER or deploy.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import runpy
import stat
import subprocess
import time
import sys
import xml.etree.ElementTree as ET
import zipfile

ROOT=Path(__file__).absolute().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

REPOSITORY = "mbalbo2023/Porota-trading"
REPOSITORY_ID = 1338680554
WORKFLOW = ".github/workflows/rc6-unified-candidate-tests.yml"
PREDEPLOY = ".github/workflows/porota-predeploy-v2.yml"
SCHEMA = "rc6.architectural-gate-receipt.v1"
MANIFEST_SCHEMA = "rc6.architectural-gates-artifacts.v1"
RUNNER_CLASS = "github-hosted/ubuntu-24.04"
SHA = re.compile(r"[0-9a-f]{40}")
DIGEST = re.compile(r"[0-9a-f]{64}")
MAX_ARCHIVE_BYTES = 256 * 1024**2
MAX_MEMBER_BYTES = 16 * 1024**2
MAX_GATE_RECEIPT_BYTES = 1024**2
MAX_MEMBERS = 4096
GATES = ("G0", "G1.311", "G1.312", "G2", "G3", "G4", "G5", "G6.311", "G6.312", "G7", "G8")
PREREQUISITES = {
    "G0": (), "G1.311": ("G0",), "G1.312": ("G0",),
    "G2": ("G0", "G1.311", "G1.312"),
    "G3": ("G0", "G1.311", "G1.312", "G2"),
    "G4": ("G0", "G1.311", "G1.312", "G2", "G3"),
    "G5": ("G0", "G1.311", "G1.312", "G2", "G3", "G4"),
    "G6.311": ("G0", "G1.311", "G1.312", "G2", "G3", "G4", "G5"),
    "G6.312": ("G0", "G1.311", "G1.312", "G2", "G3", "G4", "G5", "G6.311"),
    "G7": ("G0", "G1.311", "G1.312", "G2", "G3", "G4", "G5", "G6.311", "G6.312"),
    "G8": ("G0", "G1.311", "G1.312", "G2", "G3", "G4", "G5", "G6.311", "G6.312", "G7"),
}
ALIASES = {"focal311": "G2", "focal312": "G3", "BIG-browser": "G4", "Horizon": "G5",
           "full-gov311": "G6.311", "full-gov312": "G6.312", "predeploy": "G7"}
G1_REQUIRED_CHEAP_FILES = frozenset({
    'tests/test_rc6_single_source_tick.py', 'tests/test_rc6_native_source_reads.py',
    'tests/test_rc6_shadow_preopen_runtime.py', 'tests/test_rc6_publication_architecture.py',
    'tests/test_rc6_temporal_query_plan.py', 'tests/test_rc6_funnel_algorithm.py',
    'tests/test_rc6_stress_phase_timings.py', 'tests/test_rc6_heavy_test_preflight.py',
    'tests/test_rc6_authenticated_fixture_lifecycle.py', 'tests/test_rc6_scoped_infrastructure_lease.py',
    'tests/test_rc6_predeploy_scoped_cleanup.py', 'tests/test_deploy_v2_frozen_artifact_contract.py',
    'tests/test_rc6_architectural_gate_order.py',
    'tests/test_rc6_pytest_fixture_lifecycle.py', 'tests/test_rc6_archive_segment_reuse.py',
})
G1_HEAVY_FILES = frozenset({
    'tests/test_issue465_stress.py',
    'tests/test_rc6_convergence_budget_liveness.py',
})


def require(value, reason):
    if not value:
        raise ValueError(reason)


def validate_g1_files(files):
    require(type(files) is list and len(files)==len(set(files))
        and all(type(name) is str and re.fullmatch(r'tests/test_[A-Za-z0-9_]+\.py',name) for name in files)
        and G1_HEAVY_FILES.isdisjoint(files) and G1_REQUIRED_CHEAP_FILES.issubset(files),
        'G1_REQUIRED_CHEAP_ARCHITECTURAL_CORPUS_MISSING_OR_HEAVY')
    return files


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def document(raw):
    def pairs(rows):
        result = {}
        for key, value in rows:
            require(key not in result, "GATE_DUPLICATE_JSON_KEY")
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: require(False, "GATE_NONFINITE_JSON"))


def stamp(value):
    require(isinstance(value, str) and re.fullmatch(
        r"20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z", value),
        "GATE_UTC_STAMP_REQUIRED")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def utc():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def finite(value, upper):
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= upper


def positive(value):
    return type(value) is int and value > 0


def validate_junit(collection, execution, raw, *, require_green=True):
    """Case identity is mandatory even when summaries/counts happen to agree."""
    require(type(collection) is dict and type(execution) is dict
            and collection.get("items") == execution.get("items") and collection.get("items"),
            "GATE_COLLECTION_EXECUTION_IDENTITY_MISMATCH")
    rows = collection["items"]
    require(all(type(row) is dict and all(type(row.get(key)) is str and row[key]
                for key in ("nodeid", "classname", "name")) for row in rows), "GATE_INVALID_CASE_IDENTITY")
    require(len({row["nodeid"] for row in rows}) == len(rows), "GATE_DUPLICATE_COLLECTED_NODE")
    root = ET.fromstring(raw)
    cases = list(root.iter("testcase"))
    expected = Counter((row["classname"], row["name"]) for row in rows)
    actual = Counter((case.get("classname"), case.get("name")) for case in cases)
    require(expected == actual and all(count == 1 for count in actual.values()),
            "GATE_COLLECTION_JUNIT_IDENTITY_MISMATCH")
    counts = {tag: sum(case.find(tag) is not None for case in cases)
              for tag in ("failure", "error", "skipped")}
    for suite in root.iter("testsuite"):
        own = list(suite.findall("testcase"))
        if own:
            require(int(suite.get("tests", -1)) == len(own)
                    and all(int(suite.get(key, -1)) == sum(case.find(tag) is not None for case in own)
                            for key, tag in (("failures", "failure"), ("errors", "error"), ("skipped", "skipped"))),
                    "GATE_JUNIT_SUMMARY_MISMATCH")
    if require_green:
        require(not any(counts.values()) and collection.get("pytest_exit_code") == 0
                and execution.get("pytest_exit_code") == 0
                and type(collection.get('pytest_exit_code')) is type(execution.get('pytest_exit_code')) is int,
                "GATE_NATIVE_TEST_RED")
    return {"cases": len(cases), **counts, "junit_sha256": hashlib.sha256(raw).hexdigest(),
            "nodeids_sha256": hashlib.sha256(("\n".join(row["nodeid"] for row in rows) + "\n").encode()).hexdigest()}


def validate_receipt(row, *, source_sha, source_tree, read_contract_sha256=None):
    require(type(row) is dict and row.get("schema") == SCHEMA and row.get("gate") in GATES,
            "GATE_RECEIPT_SCHEMA_REQUIRED")
    require(SHA.fullmatch(source_sha) and SHA.fullmatch(source_tree)
            and row.get("source_sha") == source_sha and row.get("source_tree") == source_tree,
            "GATE_EXACT_SOURCE_SHA_TREE_MISMATCH")
    require(row.get("status") == "GREEN" and row.get("native_exit_code") == 0
            and type(row.get("native_exit_code")) is int and row.get("native_fin_closed") is True
            and row.get("source_unchanged") is True, "GATE_NATIVE_RED_OR_FIN_SOURCE_UNKNOWN")
    require(row.get("mode") == "PRODUCTION_PAPER / SIMULATION" and row.get("real_orders_sent") == 0
            and type(row.get("real_orders_sent")) is int and row.get("real_routes") in ("NOT_CALLED", "BLOCKED")
            and row.get("ppi_watch") == "UNTOUCHED" and row.get("DEPLOY_OWNER") == "NOT_ACQUIRED"
            and row.get("runtime_validated") is False, "GATE_PAPER_OR_RUNTIME_SCOPE_MISMATCH")
    require(row.get("runner_class") == RUNNER_CLASS and row.get("runner_receipt", {}).get("github_hosted") is True
            and row["runner_receipt"].get("runner_os") == "Linux"
            and row["runner_receipt"].get("runner_arch") == "X64", "GATE_CANONICAL_RUNNER_RECEIPT_REQUIRED")
    require(DIGEST.fullmatch(row.get("read_contract_sha256", "")) is not None
            and (read_contract_sha256 is None or row["read_contract_sha256"] == read_contract_sha256),
            "GATE_PRODUCTIVE_READ_CONTRACT_MISMATCH")
    checks = row.get("checks")
    require(type(checks) is dict and bool(checks) and all(type(value) is bool and value for value in checks.values()),
            "GATE_NATIVE_CHECKS_MISSING_OR_RED")
    require(positive(row.get("run_id")) and row.get("run_attempt") == 1
            and type(row.get("run_attempt")) is int and row.get("workflow_path") in (WORKFLOW, PREDEPLOY),
            "GATE_ORIGIN_OR_BLIND_RERUN_REJECTED")
    require(stamp(row.get("started_utc")) <= stamp(row.get("completed_utc")), "GATE_INVALID_TIME_ORDER")
    if row["gate"] == "G0":
        require(all(checks.get(name) is True for name in ("ownership", "inventory", "closure_matrix", "compile_import",
                "paper_invariants", "ppi_watch_invariant", "full_git_fsck", "full_source", "capacity_preflight")),
                "G0_STATIC_ADMISSION_INCOMPLETE")
    if row["gate"] in ("G1.311", "G1.312", "G2", "G3", "G6.311", "G6.312"):
        require(positive(row.get("test_cases")) and row.get("identity_verified") is True
                and all(type(row.get(key)) is int and row[key] == 0 for key in ("failures", "errors", "skipped", "xfail")),
                "GATE_DUAL_TEST_OR_CASE_IDENTITY_RED")
        epoch = "312" if row["gate"] in ("G1.312", "G3", "G6.312") else "311"
        require(row.get("python_epoch") == epoch, "GATE_PYTHON_EPOCH_MISMATCH")
    if row['gate'] in ('G1.311','G1.312'):
        require(checks.get('scoped_infrastructure_positive_probe') is True,
            'G1_ACTUAL_SCOPED_INFRASTRUCTURE_PROBE_REQUIRED')
    if row["gate"] == "G4":
        resource = row.get("resources", {})
        require(resource.get("catalog") == 12000 and resource.get("observations") == 60000
                and resource.get("factual_paper_exits") == 5 and resource.get("hard_limit_seconds") == 90
                and resource.get("qualification_limit_seconds") == 75
                and finite(resource.get("elapsed_seconds"), 75)
                and type(resource.get("peak_rss_bytes")) is int and 0 <= resource["peak_rss_bytes"] < 2*1024**3
                and type(resource.get("evidence_bytes")) is int and 0 <= resource["evidence_bytes"] <= 128*1024**2
                and type(resource.get("retained_entries")) is int and 0 <= resource["retained_entries"] <= 100000
                and checks.get("source_snapshot_single_capture_per_tick") is True
                and checks.get("capacity_live") is True and checks.get("authenticated_cleanup") is True,
                "G4_MATERIAL_RESOURCE_OR_HEADROOM_QUALIFICATION_RED")
    if row["gate"] == "G5":
        require(row.get("required_material_gates") == ["retention", "Horizon", "browser"]
                and all(checks.get(name) is True for name in ("retention", "Horizon", "browser")),
                "G5_REQUIRED_MATERIAL_GATES_INCOMPLETE")
    if row["gate"] in ("G6.311", "G6.312"):
        require(row.get("scope") == "repository-root automatic pytest discovery"
                and checks.get("full_source") is True and checks.get("closure_matrix") is True,
                "G6_FULL_GOVERNED_SCOPE_INCOMPLETE")
    return row


def validate_chain(receipts, *, source_sha, source_tree, target_gate, read_contract_sha256=None):
    target_gate = ALIASES.get(target_gate, target_gate)
    require(target_gate in GATES, "GATE_UNKNOWN_TARGET")
    by_gate = {}
    for row in receipts:
        validate_receipt(row, source_sha=source_sha, source_tree=source_tree,
                         read_contract_sha256=read_contract_sha256)
        gate = row["gate"]
        require(gate not in by_gate, "GATE_DUPLICATE_STAGE_RECEIPT")
        by_gate[gate] = row
    required = PREREQUISITES[target_gate]
    require(all(gate in by_gate for gate in required), "GATE_PREREQUISITE_MISSING_PENDING_OR_RED")
    require(len({by_gate[gate]["read_contract_sha256"] for gate in required}) <= 1,
            "GATE_PRODUCTIVE_READ_CONTRACT_CHANGED_WITHIN_SHA")
    for gate in required:
        for previous in PREREQUISITES[gate]:
            require(previous in by_gate and stamp(by_gate[previous]["completed_utc"]) <= stamp(by_gate[gate]["started_utc"]),
                    "GATE_ORDER_VIOLATION")
    return {"schema": "rc6.architectural-gate-admission.v1", "status": "GREEN", "target_gate": target_gate,
            "source_sha": source_sha, "source_tree": source_tree, "verified_prerequisites": list(required),
            "read_contract_sha256": by_gate[required[0]]["read_contract_sha256"] if required else read_contract_sha256,
            "workflow_success_alone_accepted": False, "DEPLOY_OWNER": "NOT_ACQUIRED",
            "final_candidate_eligible": False, "runtime_validated": False, "real_orders_sent": 0}


def archive_members(path):
    """No extraction or traversal of a path supplied by an artifact."""
    with zipfile.ZipFile(path) as archive:
        members = {}
        require(len(archive.infolist()) <= MAX_MEMBERS, "GATE_ARTIFACT_MEMBER_LIMIT")
        total = 0
        for item in archive.infolist():
            name = item.filename.rstrip("/")
            parsed = PurePosixPath(name)
            mode = item.external_attr >> 16
            require(name and not parsed.is_absolute() and ".." not in parsed.parts and "\\" not in name
                    and parsed.as_posix() == name and not any(ord(c) < 32 for c in name)
                    and name not in members and not item.flag_bits & 1
                    and not stat.S_ISLNK(mode) and stat.S_IFMT(mode) in (0, stat.S_IFREG, stat.S_IFDIR),
                    "GATE_UNSAFE_OR_DUPLICATE_ARTIFACT_MEMBER")
            members[name] = item
            total += item.file_size
            require(total <= MAX_ARCHIVE_BYTES, "GATE_UNPACKED_EVIDENCE_BOUND")
        return members


def capture_member(path, name, *, maximum=MAX_MEMBER_BYTES):
    members = archive_members(path)
    require(name in members and not members[name].is_dir() and 0 < members[name].file_size <= maximum,
            "GATE_REQUIRED_PAYLOAD_MEMBER_MISSING_OR_OVERSIZED")
    with zipfile.ZipFile(path) as archive:
        raw = archive.read(members[name])
    require(len(raw) == members[name].file_size, "GATE_ARTIFACT_MEMBER_BYTES_MISMATCH")
    return raw


def artifact_origin(reference, artifact, run, attempt, *, source_sha):
    require(type(reference) is dict and positive(reference.get("artifact_id"))
            and re.fullmatch(r"sha256:[0-9a-f]{64}", reference.get("artifact_digest", ""))
            and positive(reference.get("run_id")) and reference.get("run_attempt") == 1
            and type(reference.get('run_attempt')) is int,
            "GATE_ARTIFACT_REFERENCE_INVALID")
    for row in (run, attempt):
        require(row.get("id") == reference["run_id"] and row.get("head_sha") == source_sha
                and row.get("run_attempt") == reference["run_attempt"] and row.get("status") == "completed"
                and row.get("conclusion") == "success" and row.get("path") in (WORKFLOW, PREDEPLOY)
                and row.get("repository", {}).get("full_name") == REPOSITORY
                and row["repository"].get("id") == REPOSITORY_ID
                and row.get("head_repository", {}).get("id") == REPOSITORY_ID,
                "GATE_ACTIONS_ORIGIN_NOT_COMPLETED_EXACT_SOURCE")
    origin = artifact.get("workflow_run", {})
    require(artifact.get("id") == reference["artifact_id"] and artifact.get("digest") == reference["artifact_digest"]
            and artifact.get("expired") is False and type(artifact.get("size_in_bytes")) is int
            and 0 < artifact["size_in_bytes"] <= MAX_ARCHIVE_BYTES
            and origin.get("id") == reference["run_id"] and origin.get("head_sha") == source_sha
            and origin.get("repository_id") == origin.get("head_repository_id") == REPOSITORY_ID,
            "GATE_ARTIFACT_EXACT_DIGEST_OR_ORIGIN_MISMATCH")
    require(stamp(attempt["run_started_at"]) <= stamp(artifact["created_at"]) <= stamp(attempt["updated_at"])
            and stamp(artifact["expires_at"]) > datetime.now(timezone.utc), "GATE_ARTIFACT_TIME_CUSTODY_INVALID")


def actions_runner_origin(jobs, *, run_id):
    require(type(jobs) is dict and type(jobs.get('jobs')) is list
        and jobs.get('total_count')==len(jobs['jobs'])<=100,'GATE_ACTIONS_JOB_PAGINATION_INCOMPLETE')
    actual=[row for row in jobs['jobs'] if row.get('conclusion')!='skipped']
    require(len(actual)==1,'GATE_SINGLE_ORDERED_JOB_REQUIRED')
    job=actual[0]
    require(job.get('run_id')==run_id and positive(job.get('id')) and job.get('status')=='completed'
        and job.get('conclusion')=='success' and job.get('labels')==['ubuntu-24.04']
        and job.get('runner_group_name')=='GitHub Actions'
        and type(job.get('runner_name')) is str and job['runner_name'].startswith('GitHub Actions '),
        'GATE_ACTIONS_CANONICAL_HOSTED_RUNNER_REQUIRED')
    require(stamp(job.get('started_at'))<=stamp(job.get('completed_at')),'GATE_ACTIONS_JOB_TIME_INVALID')
    return job


def verify_manifest(manifest, *, source_sha, source_tree, target_gate, evidence_root, get=None, download=None):
    """Authenticate ZIP bytes before trusting their self-described gate status."""
    if get is None or download is None:
        from scripts.porota_artifact_http import api_get, download_artifact
        get = get or api_get
        download = download or download_artifact
    require(type(manifest) is dict and manifest.get("schema") == MANIFEST_SCHEMA
            and manifest.get("source_sha") == source_sha and manifest.get("source_tree") == source_tree
            and type(manifest.get("artifacts")) is list and len(manifest["artifacts"]) <= 16,
            "GATE_PREREQUISITE_ARTIFACT_MANIFEST_INVALID")
    root = Path(evidence_root)
    require(root.is_absolute() and not any(path.is_symlink() for path in (root, *root.parents))
            and not os.path.lexists(root), "GATE_FRESH_OWNED_EVIDENCE_NAMESPACE_REQUIRED")
    root.mkdir(mode=0o700)
    receipts, files, origins = [], {}, []
    for reference in manifest["artifacts"]:
        require(type(reference) is dict and positive(reference.get("artifact_id")), "GATE_ARTIFACT_REFERENCE_INVALID")
        identifier = reference["artifact_id"]
        artifact = get("/actions/artifacts/" + str(identifier))
        run = get("/actions/runs/" + str(reference.get("run_id")))
        attempt = get("/actions/runs/" + str(reference.get("run_id")) + "/attempts/" + str(reference.get("run_attempt")))
        artifact_origin(reference, artifact, run, attempt, source_sha=source_sha)
        job=actions_runner_origin(get('/actions/runs/'+str(reference['run_id'])+'/attempts/1/jobs?per_page=100'),
            run_id=reference['run_id'])
        path = root / (str(identifier) + ".zip")
        download(identifier, path, expected_size=artifact["size_in_bytes"], expected_digest=artifact["digest"],
                 deadline=time.monotonic() + 300)
        require(path.stat().st_size == artifact["size_in_bytes"]
                and "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest() == artifact["digest"],
                "GATE_DOWNLOADED_ARTIFACT_BYTES_MISMATCH")
        archive_members(path)
        require(type(reference.get("receipts")) is list and bool(reference["receipts"]),
                "GATE_EXACT_RECEIPT_MEMBERS_REQUIRED")
        for member in reference["receipts"]:
            require(type(member) is dict and type(member.get("path")) is str
                    and DIGEST.fullmatch(member.get("sha256", "")), "GATE_RECEIPT_MEMBER_REFERENCE_INVALID")
            raw = capture_member(path, member["path"], maximum=MAX_GATE_RECEIPT_BYTES)
            require(hashlib.sha256(raw).hexdigest() == member["sha256"], "GATE_RECEIPT_MEMBER_DIGEST_MISMATCH")
            row = document(raw)
            require(row.get("run_id") == reference["run_id"] and row.get("run_attempt") == reference["run_attempt"]
                    and row.get("workflow_path") == run["path"], "GATE_PAYLOAD_EXECUTION_ORIGIN_REBOUND")
            row = validate_receipt(row, source_sha=source_sha, source_tree=source_tree,
                read_contract_sha256=productive_contract(ROOT))
            require(stamp(job['started_at'])<=stamp(row['started_utc'])<=stamp(row['completed_utc'])
                <=stamp(job['completed_at']),'GATE_NATIVE_TIMES_OUTSIDE_ACTUAL_ACTIONS_JOB')
            verify_native_evidence(row, path)
            receipts.append(row)
            files[row["gate"]] = {"archive": str(path), "receipt_member": member["path"],
                                   "artifact_id": identifier, "artifact_digest": artifact["digest"],
                                   "run_id": run["id"], "run_attempt": run["run_attempt"]}
        origins.append({"artifact_id": identifier, "digest": artifact["digest"], "run_id": run["id"],
                        "run_attempt": run["run_attempt"], "downloaded_exact_bytes_verified": True,
                        'actual_job_runner':job})
    result = validate_chain(receipts, source_sha=source_sha, source_tree=source_tree, target_gate=target_gate)
    result.update(artifact_origins=origins, authenticated_receipts=receipts, evidence_files=files)
    return result


def verify_archived_capacity_pair(before,live,row,*,producer):
    """Replay measured admission bytes without probing a historical runner path."""
    from scripts import rc6_heavy_test_preflight as capacity
    from scripts import rc6_material_carrier as carrier
    policy=capacity.load_policy(ROOT/'ops/policy/rc6-heavy-test-governance-v1.json')
    for receipt in (before,live):
        require(type(receipt) is dict and receipt.get('schema')==capacity.SCHEMA
            and receipt.get('receipt_sha256')==capacity.digest({key:value for key,value in receipt.items()
                if key!='receipt_sha256'}),'G0_CAPACITY_RAW_RECEIPT_DIGEST_CHANGED')
        binding=receipt.get('binding');capacity.validate_binding(binding)
        require(binding['candidate_sha']==row['source_sha'] and binding['candidate_tree']==row['source_tree']
            and binding['producer']==producer and binding['runner_class']==RUNNER_CLASS
            and binding['attempt_id']==str(row['run_id'])+':'+str(row['run_attempt'])
            and binding['workload_fingerprint']==carrier.workload_fingerprint(producer),
            'G0_CAPACITY_ACTUAL_SOURCE_RUN_OR_WORKLOAD_REBOUND')
        require(receipt.get('policy_sha256')==capacity.digest(policy)
            and receipt.get('policy_version')==policy['version']
            and receipt.get('destructive_action_performed') is False
            and receipt.get('real_orders_sent')==0 and type(receipt['real_orders_sent']) is int
            and receipt.get('promotion_or_runtime_validation_claimed') is False
            and receipt.get('machine',{}).get('github_actions') is True,
            'G0_CAPACITY_POLICY_OR_ACTUAL_ACTIONS_SCOPE_REBOUND')
        measured=receipt.get('filesystem');require(type(measured) is dict,'G0_CAPACITY_FILESYSTEM_MEASUREMENT_MISSING')
        identity=measured.get('path_identity')
        require(positive(measured.get('filesystem_device')) and positive(measured.get('mount_id'))
            and type(identity) is list and len(identity)==5 and all(type(value) is int and value>=0 for value in identity)
            and identity[0]==measured['filesystem_device'] and identity[1]>0,
            'G0_CAPACITY_ACTUAL_FILESYSTEM_IDENTITY_MISSING')
        peak=capacity.envelope_allocated_bytes(receipt.get('comparable_peak'))
        capacity.validate_comparable_peak(receipt['comparable_peak'],binding,peak)
        if receipt['comparable_peak']['schema']==capacity.ENVELOPE_SCHEMA:
            require(measured.get('allocation_unit_bytes')==4096 and type(measured['allocation_unit_bytes']) is int
                and measured.get('filesystem_type')=='ext4','G0_CAPACITY_BOUND_MODEL_NOT_ACTUAL_CANONICAL_FILESYSTEM')
        recomputed=capacity.evaluate_capacity(**{key:measured.get(key) for key in
            ('free_bytes','total_inodes','free_inodes')},expected_peak_bytes=peak,
            residual_reserve_bytes=policy['local_capacity']['residual_reserve_bytes'],
            minimum_free_inode_ratio=policy['local_capacity']['minimum_free_inode_ratio'])
        require(receipt.get('capacity')==recomputed and recomputed['status']=='GREEN',
            'G0_CAPACITY_MEASURED_RESERVE_OR_INODES_RED')
        require(type(receipt.get('measured_path')) is str and Path(receipt['measured_path']).is_absolute()
            and type(receipt.get('measurement_boot_id')) is str and bool(receipt['measurement_boot_id'])
            and positive(receipt.get('measurement_pid')) and positive(receipt.get('measured_at_unix_ns')),
            'G0_CAPACITY_ACTUAL_MEASUREMENT_IDENTITY_MISSING')
    require(before['binding']==live['binding'] and before['comparable_peak']==live['comparable_peak']
        and before['measured_path']==live['measured_path']
        and before['measurement_boot_id']==live['measurement_boot_id']
        and all(before['filesystem'].get(key)==live['filesystem'].get(key)
            for key in ('filesystem_device','mount_id','path_identity'))
        and live.get('prior_receipt_sha256')==before['receipt_sha256']
        and 0<=live['measured_at_unix_ns']-before['measured_at_unix_ns']<=60*10**9,
        'G0_CAPACITY_LIVE_FILESYSTEM_OR_ORIGINAL_RECEIPT_REBOUND')
    return live


def verify_g0_capacity_index(index,row,archive):
    from scripts.rc6_controlled_native_child_manager import managed_phase_green
    from scripts import rc6_material_carrier as carrier
    require(type(index) is dict and index.get('schema')=='rc6.G0-actual-capacity-launch-index.v1'
        and index.get('source_sha')==row['source_sha'] and index.get('source_tree')==row['source_tree']
        and index.get('preparation_scope')==carrier.PREPARATION_SCOPE,
        'G0_BOOTSTRAP_ACTUAL_CAPACITY_INDEX_REQUIRED')
    def native(reference):
        require(type(reference) is dict and type(reference.get('path')) is str
            and reference['path'].startswith('controls/') and DIGEST.fullmatch(reference.get('sha256','')),
            'G0_CAPACITY_ORIGINAL_CONTROL_REFERENCE_REQUIRED')
        raw=capture_member(archive,reference['path'])
        require(hashlib.sha256(raw).hexdigest()==reference['sha256'],'G0_CAPACITY_ORIGINAL_CONTROL_BYTES_CHANGED')
        return document(raw)
    records=index.get('capacity_records');commands=index.get('commands')
    require(type(records) is list and 1<=len(records)<=256 and type(commands) is list and 1<=len(commands)<=128,
        'G0_CAPACITY_ACTUAL_LAUNCH_INVENTORY_MISSING')
    verified={}
    for record in records:
        require(type(record) is dict,'G0_CAPACITY_ACTUAL_LAUNCH_RECORD_REQUIRED')
        label=record.get('label')
        require(type(label) is str and re.fullmatch('[a-zA-Z0-9_.-]+',label) and label not in verified,
            'G0_CAPACITY_LAUNCH_PROOF_DUPLICATE_OR_INVALID')
        before=native(record.get('before'));live=native(record.get('live'))
        producer=live.get('binding',{}).get('producer')
        require(producer in ('bootstrap','full-gov311','full-gov312'),'G0_CAPACITY_UNEXPECTED_PRODUCER')
        verify_archived_capacity_pair(before,live,row,producer=producer)
        require(record.get('binding')==live['binding'] and record.get('measured_path')==live['measured_path']
            and record.get('measured_at_unix_ns')==live['measured_at_unix_ns'],
            'G0_CAPACITY_INDEX_DIFFERENT_FROM_ORIGINAL_MEASUREMENT')
        if producer=='bootstrap':
            require(live['comparable_peak'].get('preparation_scope')==index['preparation_scope'],
                'G0_CAPACITY_COMPOUND_BOOTSTRAP_PROOF_MISSING')
        verified[label]=live
    required={'venv311','venv312','locked311-0','locked311-1','locked312-0','locked312-1',
        'fullGit-original-inputs','G0-fsck311','G0-fsck312','G0-admission311','G0-admission312'}
    observed=set()
    for command in commands:
        require(type(command) is dict,'G0_CAPACITY_ACTUAL_NATIVE_COMMAND_REQUIRED')
        label=command.get('label')
        require(type(label) is str and label not in observed,'G0_CAPACITY_COMMAND_DUPLICATED')
        observed.add(label);intent=native(command.get('intent'));kernel=native(command.get('kernel'))
        require(managed_phase_green(kernel) and kernel.get('command')==intent.get('argv')
            and kernel.get('owned_cleanup_management_bound_seconds')==5,
            'G0_CAPACITY_PREPARATION_ACTUAL_NATIVE_FIN_RED')
        proofs=intent.get('capacity_launch_proofs')
        require(type(proofs) is list and len(proofs)==len(set(proofs)) and all(name in verified for name in proofs),
            'G0_CAPACITY_COMMAND_PROOF_MISSING_OR_REBOUND')
        heavy=label in required or label.startswith('original-fetch-')
        if heavy:
            require(bool(proofs),'G0_CAPACITY_PRODUCER_STARTED_WITHOUT_LIVE_PREFLIGHT')
            producer='full-gov'+label[-3:] if label.startswith('G0-admission') else 'bootstrap'
            require(all(verified[name]['binding']['producer']==producer for name in proofs),
                'G0_CAPACITY_PREPARATION_PEAK_ASSUMED_FROM_DIFFERENT_PRODUCER')
            if producer=='bootstrap':
                require(any(name.endswith('-bootstrap_namespace') for name in proofs)
                    and any(name.endswith('-candidate_git_objects') for name in proofs),
                    'G0_CAPACITY_ACTUAL_NAMESPACE_AND_GIT_FILESYSTEM_NOT_MEASURED')
            if label.startswith(('venv','locked')):
                require(any(name.endswith(('-bootstrap_temporary','-pip_temporary')) for name in proofs),
                    'G0_CAPACITY_ACTUAL_PIP_TEMPORARY_FILESYSTEM_NOT_MEASURED')
        launch_ns=intent.get('intent_recorded_unix_ns')
        require(positive(launch_ns) and all(0<=launch_ns-verified[name]['measured_at_unix_ns']<=60*10**9
            for name in proofs),'G0_CAPACITY_PREFLIGHT_STALE_AT_ACTUAL_PRODUCER_LAUNCH')
    require(required.issubset(observed),'G0_CAPACITY_PREPARATION_PRODUCER_OR_FIN_NOT_VERIFIED')
    return {'status':'GREEN','actual_capacity_proofs':len(verified),'actual_native_commands':len(observed),
        'compound_bootstrap_and_both_epoch_capacity_launches_verified':True,'promotion_claimed':False}


def verify_native_evidence(row, archive):
    evidence = row.get("native_evidence")
    require(type(evidence) is dict and bool(evidence), "GATE_NATIVE_EVIDENCE_MISSING")
    captured = {}
    for role, reference in evidence.items():
        require(type(reference) is dict and type(reference.get("path")) is str
                and DIGEST.fullmatch(reference.get("sha256", "")), "GATE_NATIVE_EVIDENCE_REFERENCE_INVALID")
        raw = capture_member(archive, reference["path"])
        require(hashlib.sha256(raw).hexdigest() == reference["sha256"], "GATE_NATIVE_EVIDENCE_BYTES_MISMATCH")
        captured[role] = raw
    if row['gate'] in ('G1.311','G1.312'):
        require(all(name in captured for name in ('scoped_infrastructure_probe',
            'scoped_probe_diagnostics_archive','scoped_probe_diagnostics_index')),
            'G1_ACTUAL_SCOPED_INFRASTRUCTURE_RAW_MISSING')
        verify_scoped_infrastructure_probe(document(captured['scoped_infrastructure_probe']),row,
            diagnostics_raw=captured['scoped_probe_diagnostics_archive'],
            diagnostics_index=document(captured['scoped_probe_diagnostics_index']))
    if row['gate']=='G0':
        require(all(name in captured for name in ('inventory','FIP','capacity_index','admission311','admission312',
            'admission311_kernel','admission312_kernel')),'G0_NATIVE_STATIC_EVIDENCE_REQUIRED')
        verify_g0_capacity_index(document(captured['capacity_index']),row,archive)
        inventory=document(captured['inventory'])
        fip=document(captured['FIP'])
        require(fip.get('candidate_sha')==row['source_sha'] and fip.get('candidate_tree')==row['source_tree']
            and fip.get('software_status')=='INVENTORY_NOT_EXECUTED'
            and len(fip.get('requirements',[]))==55 and len(fip.get('scenarios',[]))==80,
            'G0_NATIVE_CLOSURE_MATRIX_BINDING_REBOUND')
        require(inventory.get('source_sha')==row['source_sha'] and inventory.get('source_tree')==row['source_tree']
            and inventory.get('inventory') and inventory.get('automatic_discovery_arguments')==
                ['.','--ignore=test_a3_primary_readonly_hf6.py'], 'G0_NATIVE_FULL_SOURCE_INVENTORY_REBOUND')
        from scripts.rc6_controlled_native_child_manager import managed_phase_green
        for epoch in ('311','312'):
            native=document(captured['admission'+epoch]);control=document(captured['admission'+epoch+'_kernel'])
            require(native.get('schema')=='rc6.architectural-native-static.v1'
                and native.get('source_sha')==row['source_sha'] and native.get('source_tree')==row['source_tree']
                and positive(native.get('compiled_product_files'))
                and native.get('native_exit_code')==0 and type(native.get('native_exit_code')) is int
                and native.get('native_fixtures_started') is False
                and native.get('source_namespace_exact_before_after') is True
                and native.get('closure_before_fixture',{}).get('installed_total')==157
                and native.get('inet_socket_attempts')==[] and native.get('unexpected_product_imports')==[]
                and finalization_closed(native.get('child_infrastructure_finalization',{}))
                and managed_phase_green(control.get('kernel',{})), 'G0_COMPILE_IMPORT_CLOSURE_OR_FIN_RED')
    if row["gate"] in ("G1.311", "G1.312", "G2", "G3", "G6.311", "G6.312"):
        require(all(role in captured for role in ("collection", "execution", "junit")), "GATE_NATIVE_TEST_EVIDENCE_INCOMPLETE")
        collection, execution = document(captured["collection"]), document(captured["execution"])
        counts = validate_junit(collection, execution, captured["junit"])
        if row['gate'] in ('G1.311','G1.312'):
            validate_g1_files(sorted({node['nodeid'].split('::',1)[0] for node in collection['items']}))
        require(counts["cases"] == row["test_cases"], "GATE_NATIVE_RECEIPT_CASE_COUNT_REBOUND")
        require(all(node.get("source_sha") == row["source_sha"] and node.get("source_tree") == row["source_tree"]
                    and node.get("source_namespace_exact_before_after") is True
                    and node.get('inet_socket_attempts')==[] and node.get('unexpected_product_imports')==[]
                    and node.get('closure_before_fixture',{}).get('installed_total')==157
                    and node.get('closure_before_fixture',{}).get('status')=='GREEN'
                    and finalization_closed(node.get("child_infrastructure_finalization", {}))
                    for node in (collection, execution)), "GATE_NATIVE_SOURCE_OR_FIN_EVIDENCE_REBOUND")
        if row['gate'] in ('G1.311','G1.312','G2','G3'):
            from scripts.rc6_material_focal import classify_node
            require(collection.get('complete_corpus')==execution.get('complete_corpus')
                ==row.get('complete_focal_corpus')
                and collection.get('deferred_material_nodes')==execution.get('deferred_material_nodes')
                ==row.get('deferred_material_nodes')
                and row.get('deferred_nodes_claimed_executed') is False,
                'GATE_FOCAL_COMPLETE_CORPUS_LEDGER_REBOUND')
            corpus=collection['complete_corpus'];deferred=collection['deferred_material_nodes']
            require(Counter(node['nodeid'] for node in corpus)==
                Counter(node['nodeid'] for node in collection['items'])+Counter(node['nodeid'] for node in deferred)
                and all(classify_node(node['nodeid'])=={'stage':node.get('stage'),'reason':node.get('reason')}
                    and node.get('stage')=='G6' for node in deferred),
                'GATE_FOCAL_ORIGINAL_CORPUS_UNION_COVERAGE_MISMATCH')
        from scripts.rc6_controlled_native_child_manager import managed_phase_green
        from scripts import rc6_controlled_governed_runner as governed_runner
        wrapper=ROOT/governed_runner.RECORD_WRAPPER_MEMBER
        require(hashlib.sha256(wrapper.read_bytes()).hexdigest()==governed_runner.RECORD_WRAPPER_SHA256,
            'ORIGINAL_GOVERNED_ACCEPTANCE_PREDICATES_CHANGED')
        original=runpy.run_path(str(wrapper))
        for phase in ("collection", "execution"):
            require(phase+"_kernel" in captured, "GATE_ACTUAL_NATIVE_KERNEL_EVIDENCE_REQUIRED")
            control=document(captured[phase+"_kernel"])
            kernel=control.get("kernel",control)
            accepted=(original['native_kernel_closed'](kernel) and type(kernel.get('returncode')) is int
                and kernel['returncode']==0 and kernel.get('phase_acceptance_deadline_seconds')==5400
                and kernel.get('owned_group_signal_observations')==[]
                if row['gate'].startswith('G6.') else managed_phase_green(kernel)
                and kernel.get('launcher_management_deadline_seconds')==5400)
            require(accepted and kernel.get("owned_cleanup_management_bound_seconds")==5
                and kernel.get('pid')==document(captured[phase]).get('pid'),
                "GATE_ACTUAL_NATIVE_KERNEL_FIN_OR_LOGICAL_EXIT_RED")
        if row["gate"] in ("G6.311","G6.312"):
            require(all(name in captured for name in ("governed","FIP","native_execution",
                'source_before','source_after','records_before','records_after')),
                "G6_NATIVE_FULL_GOVERNED_EVIDENCE_INCOMPLETE")
            governed=document(captured["governed"]);native=document(captured["native_execution"])
            require(governed.get("status")=="GREEN" and governed.get("candidate_sha")==row["source_sha"]
                and governed.get("candidate_tree")==row["source_tree"]
                and governed.get("junit_sha256")==counts["junit_sha256"]
                and governed.get("discovered")==governed.get("executed")==counts["cases"]
                and governed.get("scope")=="repository-root automatic pytest discovery"
                and native.get("source_namespace_exact_before_each_phase_and_after") is True
                and native.get("source_bytes_modes_blobs_unchanged") is True
                and native.get("overlay_count")==0 and type(native.get('overlay_count')) is int
                and native.get('source_before_index_sha256')==hashlib.sha256(captured['source_before']).hexdigest()
                and native.get('source_after_index_sha256')==hashlib.sha256(captured['source_after']).hexdigest()
                and native.get('Record157_bytes_versions_original10_equal_before_after') is True
                and native.get('Record157_original_predicates',{}).get('path')==governed_runner.RECORD_WRAPPER_MEMBER
                and native['Record157_original_predicates'].get('sha256')==governed_runner.RECORD_WRAPPER_SHA256
                and native['Record157_original_predicates'].get('before_sha256')==hashlib.sha256(captured['records_before']).hexdigest()
                and native['Record157_original_predicates'].get('after_sha256')==hashlib.sha256(captured['records_after']).hexdigest(),
                "G6_NATIVE_WHOLE_GOVERNED_SOURCE_CLOSURE_REBOUND")
            before,after=document(captured['source_before']),document(captured['source_after'])
            require(positive(native.get('tracked_files')) and native['tracked_files']==len(before.get('files',{}))
                ==len(after.get('files',{})) and all(snapshot.get('source_sha')==row['source_sha']
                    and snapshot.get('source_tree')==row['source_tree'] for snapshot in (before,after)),
                'G6_COMPLETE_SOURCE_PIN_REBOUND')
            governed_runner.compare_source(before,after)
            records_before,records_after=document(captured['records_before']),document(captured['records_after'])
            require(all(len(snapshot.get('records',[]))==157
                and len({record.get('name') for record in snapshot['records']})==157
                for snapshot in (records_before,records_after)),'G6_ACTUAL_ORIGINAL_RECORD157_COUNT_REBOUND')
            original['compare_records157'](records_before,records_after)
            fip=document(captured['FIP'])
            require(fip.get('candidate_sha')==row['source_sha'] and fip.get('candidate_tree')==row['source_tree']
                and fip.get('software_status')=='EXECUTED_NATIVE_GREEN'
                and fip.get('test_execution',{}).get('junit_sha256')==counts['junit_sha256']
                and fip['test_execution'].get('executed_unique_cases')==counts['cases']
                and fip['test_execution'].get('distinct_attack_ids')==80
                and fip['test_execution'].get('distinct_requirement_ids')==55,
                'G6_ORIGINAL_FIP_COHORTS_OR_SOURCE_BINDING_REBOUND')
    if row["gate"] == "G4":
        require(all(name in captured for name in ('BIG','native_BIG','browser')), "G4_NATIVE_BIG_PAYLOAD_REQUIRED")
        big = document(captured["BIG"])
        require(big.get("source_sha") == row["source_sha"] and big.get("source_tree") == row["source_tree"]
                and big.get("status") == "GREEN" and big.get("native_FIN_payload_safe") is True
                and bool(big.get("checks")) and all(value is True for value in big["checks"].values()),
                "G4_NATIVE_BIG_RECEIPT_RED_OR_REBOUND")
        native=document(captured['native_BIG']);browser=document(captured['browser']);resources=row['resources']
        require(native.get('catalog_count')==resources['catalog'] and native.get('observations_materialized')==resources['observations']
            and native.get('shadow',{}).get('elapsed_seconds')==resources['elapsed_seconds']
            and native.get('resource_gates',{}).get('actual_evidence_bytes')==resources['evidence_bytes']
            and native['resource_gates'].get('actual_rss_bytes')==resources['peak_rss_bytes']
            and browser.get('status')=='GREEN' and browser.get('catalog_full_identities')==12000
            and browser.get('observations')==60000 and browser.get('network_attempts')==browser.get('provider_requests')==0
            and browser.get('source_custody_inventory_unchanged') is True
            and browser.get('tracked_source_hashes_and_modes_unchanged') is True,
            'G4_NATIVE_RESOURCE_OR_BROWSER_MATERIAL_REBOUND')
    if row['gate']=='G5':
        require(all(name in captured for name in ('Horizon','native_Horizon')),'G5_NATIVE_HORIZON_RETENTION_EVIDENCE_REQUIRED')
        horizon=document(captured['Horizon']);native=document(captured['native_Horizon'])
        require(horizon.get('source_sha')==row['source_sha'] and horizon.get('source_tree')==row['source_tree']
            and horizon.get('native_horizon_validated') is True and horizon.get('physical_custody_closed') is True
            and horizon.get('native_finalizer5_green_before_payload_reads') is True
            and horizon.get('original_native_acceptance_checks_executed') is True,
            'G5_ORIGINAL_HORIZON_FIN_OR_RETENTION_RED')
        require(horizon.get('native_result_sha256')==hashlib.sha256(captured['native_Horizon']).hexdigest()
            and horizon.get('native_result_bytes')==len(captured['native_Horizon']) and horizon.get('errors')==[]
            and native.get('source_sha')==row['source_sha'] and native.get('source_tree')==row['source_tree']
            and native.get('ticks_requested')==1201 and native.get('native_ticks_executed')==1202
            and all(native.get(name) is True for name in ('execution_complete','native_horizon_contract_verified',
                'horizon_complete','complete','acceptance_complete','code_source_unchanged','source_database_unchanged',
                'import_graph_verified','source_index_and_tar_unchanged')),
            'G5_NATIVE_HORIZON_ACCEPTANCE_BYTES_REBOUND')
    return captured


def finalization_closed(row):
    return (type(row) is dict and row.get("status")=="GREEN" and row.get("finalization_thread_finished") is True
        and row.get("kernel_echild_before_phase_return") is True
        and row.get("signal_guard_installed_and_witnessed") is True
        and row.get("forced_termination_attempted") is False and row.get("signal_vetoed") is False
        and row.get("termination_signal_attempts")==row.get("errors")==[]
        and finite(row.get("wall_seconds"),5) and row.get("management_bound_seconds")==5)


def verify_scoped_infrastructure_probe(probe,row,*,diagnostics_raw,diagnostics_index):
    """Require actual intra-phase reclamation; local fail-closed is not promotion."""
    from scripts import rc6_heavy_test_preflight as capacity
    from scripts.rc6_controlled_native_child_manager import managed_phase_green
    binding=probe.get('binding')
    capacity.validate_binding(binding)
    expected_epoch='3.12' if row['gate']=='G1.312' else '3.11'
    require(probe.get('schema')=='porota.rc6.scoped-infrastructure-probe.v1'
        and probe.get('status')=='GREEN' and probe.get('actual_positive') is True
        and probe.get('kernel_census_verified') is True and probe.get('namespace_removed') is True
        and probe.get('source_sha')==binding['candidate_sha']==row['source_sha']
        and probe.get('source_tree')==binding['candidate_tree']==row['source_tree']
        and probe.get('python_minor')==expected_epoch and probe.get('python_version','').startswith(expected_epoch+'.')
        and binding['runner_class']==RUNNER_CLASS
        and binding['attempt_id']==str(row['run_id'])+':'+str(row['run_attempt'])
        and binding['producer']=='focal'+row['python_epoch']
        and probe.get('whole_phase_or_big_ECHILD_guard_changed') is False
        and probe.get('original_tests_or_fixture_results_changed') is False
        and probe.get('real_orders_sent')==0 and type(probe.get('real_orders_sent')) is int,
        'G1_ACTUAL_SCOPED_INFRASTRUCTURE_PROBE_RED_OR_REBOUND')
    kernel=probe.get('native_owned_fin',{})
    require(managed_phase_green(kernel) and kernel.get('launcher_management_deadline_seconds')==20
        and kernel.get('owned_cleanup_management_bound_seconds')==5
        and finalization_closed(probe.get('producer_original_finalizer',{})),
        'G1_SCOPED_INFRASTRUCTURE_ORIGINAL_NATIVE_FIN_RED')
    summary=probe.get('fixture_lifecycle',{});before=probe.get('intrafase_before_original_finalizer',{})
    session=summary.get('session_infrastructure',{});birth=session.get('positively_observed_tracker_birth')
    require(summary.get('authenticated_original_factory_scopes')==3
        and summary.get('closed_and_removed_scopes')==3
        and before.get('authenticated_original_factory_scopes')==3
        and before.get('closed_and_removed_scopes')==3
        and type(before.get('reclaimed_allocated_bytes')) is int
        and before['reclaimed_allocated_bytes']>=6*1024**2
        and positive(session.get('positive_fixture_lease_observations'))
        and session.get('binding')==binding and type(birth) is dict and positive(birth.get('pid'))
        and session.get('outstanding_scoped_resource_registrations')==0
        and session.get('blockers')==[] and session.get('original_phase_or_big_ECHILD_guard_changed') is False
        and session.get('global_finalizer_or_stop_called') is False,
        'G1_SCOPED_INFRASTRUCTURE_INTRAPHASE_RECLAMATION_NOT_PROVED')
    cleanup=probe.get('cleanup',{})
    require(cleanup.get('schema')=='porota.rc6.generated-fixture-scoped-cleanup.v1'
        and cleanup.get('binding')==binding and cleanup.get('namespace_removed') is True
        and cleanup.get('actual_owned_fin_closed') is True and cleanup.get('phase_green') is True
        and cleanup.get('capture_manifest_sha256')==probe.get('capture_manifest_sha256')
        and cleanup.get('foreign_paths_removed')==0 and cleanup.get('link_targets_followed') is False
        and type(cleanup.get('retained_entries_before')) is int and 0<=cleanup['retained_entries_before']<=100000,
        'G1_SCOPED_INFRASTRUCTURE_AUTHENTICATED_CLEANUP_RED')
    sources=probe.get('source_records',[])
    expected_sources={'scripts/rc6_scoped_infrastructure_lease.py','scripts/rc6_authenticated_fixture_lifecycle.py',
        'scripts/rc6_pytest_fixture_lifecycle.py','scripts/rc6_controlled_governed_runner.py',
        'scripts/rc6_controlled_native_child_manager.py'}
    require(type(sources) is list and len(sources)==len(expected_sources)
        and {item.get('path') for item in sources}==expected_sources,'G1_SCOPED_INFRASTRUCTURE_COMPLETE_SOURCE_REQUIRED')
    for item in sources:
        raw=(ROOT/item['path']).read_bytes()
        require(item.get('sha256')==hashlib.sha256(raw).hexdigest() and item.get('bytes')==len(raw),
            'G1_SCOPED_INFRASTRUCTURE_PRODUCTIVE_SOURCE_BYTES_REBOUND')
    # The inner pack is never extracted. Its own inventory/hash/CRC checks are
    # replayed, then each mandatory original RAW member is independently hashed.
    from scripts import rc6_material_carrier as carrier
    carrier.verify_diagnostic_pack(diagnostics_raw,diagnostics_index)
    with zipfile.ZipFile(io.BytesIO(diagnostics_raw)) as packed:
        prefix='focal/execution-scoped-infrastructure-probe/probe-sealed-evidence/'
        manifest_raw=packed.read(prefix+'manifest.json')
        require(hashlib.sha256(manifest_raw).hexdigest()==probe['capture_manifest_sha256'],
            'G1_SCOPED_INFRASTRUCTURE_CAPTURE_MANIFEST_REBOUND')
        manifest=document(manifest_raw)
        required=probe.get('raw_required_hashes')
        require(type(required) is list and bool(required)
            and len(required)==len(manifest.get('files',[])), 'G1_SCOPED_INFRASTRUCTURE_RAW_INVENTORY_INCOMPLETE')
        for proof,original in zip(required,manifest['files']):
            require(proof.get('path')==original.get('relative_source')
                and proof.get('capture_file')==original.get('capture_file')
                and proof.get('sha256')==original.get('sha256') and proof.get('bytes')==original.get('bytes'),
                'G1_SCOPED_INFRASTRUCTURE_RAW_MANIFEST_REBOUND')
            raw=packed.read(prefix+proof['capture_file'])
            require(hashlib.sha256(raw).hexdigest()==proof['sha256'] and len(raw)==proof['bytes'],
                'G1_SCOPED_INFRASTRUCTURE_ORIGINAL_RAW_BYTES_REBOUND')
    return True


def import_verified_governed(verified, *, output_root, epoch="311"):
    """Preserve G6's original bytes; no G7 pytest launch is asserted."""
    require(type(verified) is dict and verified.get('status')=='GREEN'
        and verified.get('target_gate')=='G7' and epoch in ('311','312'),
        'G7_EXTERNAL_G6_ORDERED_ADMISSION_REQUIRED')
    ordered=validate_chain(verified['authenticated_receipts'], source_sha=verified['source_sha'],
        source_tree=verified['source_tree'], target_gate='G7', read_contract_sha256=productive_contract(ROOT))
    require(ordered['verified_prerequisites']==verified.get('verified_prerequisites'),
        'G7_EXTERNAL_G6_PREDECESSOR_CHAIN_REBOUND')
    gate="G6."+epoch
    rows=[row for row in verified["authenticated_receipts"] if row["gate"]==gate]
    require(len(rows)==1 and gate in verified["verified_prerequisites"],"G7_EXTERNAL_G6_AUTHENTICATED_RECEIPT_REQUIRED")
    row=rows[0];origin=verified["evidence_files"][gate];archive=Path(origin["archive"])
    require(origin.get('run_id')==row['run_id'] and origin.get('run_attempt')==row['run_attempt']
        and positive(origin.get('artifact_id')), 'G7_EXTERNAL_G6_NATIVE_ORIGIN_REBOUND')
    require("sha256:"+hashlib.sha256(archive.read_bytes()).hexdigest()==origin["artifact_digest"],
        "G7_EXTERNAL_G6_ARCHIVE_CHANGED")
    native=verify_native_evidence(row,archive)
    output=Path(output_root)
    require(output.is_dir() and not output.is_symlink(),"G7_OWNED_PRIVATE_OUTPUT_REQUIRED")
    preserved={}
    for role,name in (("governed","porota-governed-tests.json"),("junit","porota-governed-tests.xml")):
        require(not os.path.lexists(output/name),"G7_EXTERNAL_G6_OUTPUT_MUST_BE_NEW")
        with (output/name).open("xb") as stream:
            stream.write(native[role]);stream.flush();os.fsync(stream.fileno())
        preserved[name]={"sha256":hashlib.sha256(native[role]).hexdigest(),"bytes":len(native[role])}
    scope=document(native["governed"])
    exclusions="\n".join(scope["exclusions"])+("\n" if scope["exclusions"] else "")
    with (output/"porota-test-exclusions.txt").open("x",encoding="utf-8") as stream:stream.write(exclusions)
    receipt={"schema":"rc6.predeploy-external-full-governed-origin.v1","status":"GREEN",
        "source_sha":row["source_sha"],"source_tree":row["source_tree"],"gate":gate,
        "artifact_id":origin["artifact_id"],"artifact_digest":origin["artifact_digest"],
        "native_run_id":origin["run_id"],"native_run_attempt":origin["run_attempt"],"preserved_bytes":preserved,
        "full_governed_execution_origin":"AUTHENTICATED_G6_ACTIONS_NATIVE_PAYLOAD",
        "G7_pytest_execution_claimed":False,"G7_native_FIN_claimed":False,"source_suite_reexecuted":False,
        "workflow_success_alone_accepted":False,"real_orders_sent":0}
    with (output/"porota-external-governed-origin.json").open("xb") as stream:stream.write(canonical(receipt))
    return receipt


def productive_contract(root):
    import runpy
    values = runpy.run_path(str(Path(root) / "rc6_shadow_runtime/read_contract.py"))
    return values["DEFAULT_READ_CONTRACT"].fingerprint()


def predeploy_capacity_gate(authority,*,scope,repo,environ,stage):
    """Measure both actual storage filesystems immediately before G7 producers."""
    from scripts import rc6_heavy_test_preflight as capacity
    from scripts import rc6_material_carrier as carrier
    from scripts import porota_predeploy_cleanup as cleanup
    from types import SimpleNamespace
    require(stage in ('environment-bootstrap','locked-build','locked-runtime','build','artifact-export'),
        'G7_CAPACITY_UNKNOWN_PRODUCER_STAGE')
    context=cleanup.execution_context(Path(repo),environ)
    owner,_=cleanup.cli_owner(Path(scope),Path(repo),context,environ)
    require(authority.get('status')=='ADMITTED_NATIVE_NOT_STARTED' and authority.get('gate')=='predeploy'
        and authority.get('source_sha')==context['candidate_sha']
        and authority.get('source_tree')==context['candidate_tree'], 'G7_CAPACITY_EXACT_OWNER_SOURCE_REQUIRED')
    peak=authority.get('capacity_peaks',{}).get('predeploy')
    require(type(peak) is dict,'G7_CAPACITY_KNOWN_COMPARABLE_PREDEPLOY_PEAK_REQUIRED')
    args=SimpleNamespace(source_sha=context['candidate_sha'],source_tree=context['candidate_tree'],
        owner_session=authority['owner_session'])
    binding=carrier.capacity_binding(args,'predeploy')
    policy=capacity.load_policy(Path(repo)/'ops/policy/rc6-heavy-test-governance-v1.json')
    docker_root=subprocess.check_output(['docker','info','--format','{{.DockerRootDir}}'],
        env=dict(environ),text=True,timeout=10).strip()
    paths=[('workspace',Path(owner['private_root'])),('docker',cleanup.safe_absolute(docker_root))]
    if environ.get('TMPDIR'):
        temporary=cleanup.safe_absolute(environ['TMPDIR'])
        require(temporary.is_relative_to(Path(owner['private_root'])),'G7_TEMPORARY_ROOT_OUTSIDE_OWN_NAMESPACE')
        paths.append(('temporary',temporary))
    checks=[]
    for label,path in paths:
        before=capacity.build_receipt(policy=policy,path=path,expected_peak_bytes=capacity.envelope_allocated_bytes(peak),
            binding=binding,comparable_peak=peak)
        cleanup.publish(Path(owner['private_root'])/('porota-g7-'+stage+'-'+label+'-capacity-before.json'),before)
        live=capacity.validate_live_receipt(path=path,receipt=before,binding=binding,policy=policy)
        cleanup.publish(Path(owner['private_root'])/('porota-g7-'+stage+'-'+label+'-capacity-live.json'),live)
        checks.append({'storage_role':label,'measured_path':str(path),'actual_live_receipt':live})
    return {'schema':'rc6.predeploy-producer-live-capacity.v1','status':'GREEN','stage':stage,
        'source_sha':context['candidate_sha'],'source_tree':context['candidate_tree'],'binding':binding,
        'storage_filesystems_measured_independently':True,'measured_storage':checks,
        'real_orders_sent':0,'DEPLOY_OWNER':'NOT_ACQUIRED','producer_started':False}


def receipt_base(gate, *, source_sha, source_tree, read_contract_sha256, started_utc, checks,
                 native_exit_code=0, native_fin_closed=True, source_unchanged=True, **extra):
    """Transport envelope only: callers provide actual native checks/evidence."""
    require(os.environ.get("GITHUB_ACTIONS") == "true" and os.environ.get("GITHUB_REPOSITORY") == REPOSITORY
            and os.environ.get("GITHUB_REPOSITORY_ID") == str(REPOSITORY_ID)
            and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted", "GATE_CANONICAL_ACTIONS_RUNNER_REQUIRED")
    return {"schema": SCHEMA, "gate": gate, "status": "GREEN" if native_exit_code == 0 and all(checks.values()) else "RED",
            "source_sha": source_sha, "source_tree": source_tree, "read_contract_sha256": read_contract_sha256,
            "started_utc": started_utc, "completed_utc": utc(), "checks": checks,
            "native_exit_code": native_exit_code, "native_fin_closed": native_fin_closed, "source_unchanged": source_unchanged,
            "run_id": int(os.environ["GITHUB_RUN_ID"]), "run_attempt": int(os.environ["GITHUB_RUN_ATTEMPT"]),
            "workflow_path": os.environ["GITHUB_WORKFLOW_REF"].split("@", 1)[0].removeprefix(REPOSITORY + "/"),
            "runner_class": RUNNER_CLASS, "runner_receipt": {"github_hosted": True,
                "runner_os": os.environ.get("RUNNER_OS"), "runner_arch": os.environ.get("RUNNER_ARCH"),
                "logical_cpus": os.cpu_count(), "uname": list(os.uname())},
            "mode": "PRODUCTION_PAPER / SIMULATION", "real_orders_sent": 0, "real_routes": "NOT_CALLED",
            "ppi_watch": "UNTOUCHED", "DEPLOY_OWNER": "NOT_ACQUIRED", "runtime_validated": False,
            "final_candidate_eligible": False, **extra}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--target-gate", choices=GATES, required=True)
    source=parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--manifest-json")
    source.add_argument("--admission-json",type=Path)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--json-out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.admission_json:
            from scripts.porota_predeploy_cleanup import read_file
            raw,_=read_file(args.admission_json,maximum=256*1024,mode=0o600)
            authority=document(raw)
            require(authority.get("status")=="ADMITTED_NATIVE_NOT_STARTED" and authority.get("gate")=="predeploy"
                and authority.get("source_sha")==args.source_sha and authority.get("source_tree")==args.source_tree,
                "G7_EXACT_SCOPED_OWNER_ADMISSION_REQUIRED")
            manifest=authority["prerequisites_manifest"]
        else:manifest=document(args.manifest_json)
        result = verify_manifest(manifest, source_sha=args.source_sha,
            source_tree=args.source_tree, target_gate=args.target_gate, evidence_root=args.evidence_root)
        require(not os.path.lexists(args.json_out), "GATE_NEW_RECEIPT_PATH_REQUIRED")
        with args.json_out.open("xb") as stream:
            stream.write(canonical(result)); stream.flush(); os.fsync(stream.fileno())
        print(json.dumps({key: result[key] for key in ("status", "target_gate", "source_sha", "verified_prerequisites")}), flush=True)
        return 0
    except (ValueError, OSError, KeyError, TypeError, zipfile.BadZipFile, ET.ParseError) as error:
        reason = str(error).partition(":")[0]
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,191}", reason):
            reason = "NON_LITERAL_GATE_ADMISSION_FAILURE"
        print(json.dumps({"status": "BLOCKED", "reason": reason, "target_gate": args.target_gate,
                          "native_started": False, "real_orders_sent": 0, "DEPLOY_OWNER": "NOT_ACQUIRED"}), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
