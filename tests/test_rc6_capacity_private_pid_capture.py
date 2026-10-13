"""Pure artifact/control replay regressions; fixtures grant no native authority."""
from __future__ import annotations

import copy
from datetime import timedelta
import errno
import hashlib
import io
import json
import stat
import zipfile

import pytest

from scripts import rc6_architectural_gates as gates
from scripts import rc6_contract_revisions as revisions
from scripts import rc6_material_pr_admission as admission
from scripts import rc6_native_namespace_filter as native
from scripts import rc6_owned_gate_lease as lease
from scripts import rc6_private_pid_boundary as boundary
from scripts import rc6_private_pid_capture_replay as replay
from scripts import rc6_privileged_custody as custody
from tests.test_rc6_architectural_gate_order import unit_probe_observations
from tests.test_rc6_private_pid_capture_replay import CaptureFormatFixture, kernel, process
from tests.test_rc6_released_successor import BRANCH, NOW, OWNER, PLAN, RUN, SHA, TREE, ReleasedDiagnosticApi, body, comment, scoped_row


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def captured_terminal():
    """Complete serialized format fixture; never authentic GitHub/native proof."""
    fixture = CaptureFormatFixture()
    closure = fixture.validate()
    signal = {"revision": boundary.REVISION, "scope": "capacity-probe",
        "status": "NATIVE_PRIVATE_PID_V2_AND_SCOPED_OWNER_ACCEPTANCE_PROVED", "proved": True,
        "privileged_launch_authorized": True, "boundary_observation_sha256": closure["boundary_observation_sha256"],
        "contract_sha256": closure["contract_sha256"], "approval_body_sha256": "e" * 64}
    receipt = {"binding": fixture.binding, "source_sha": fixture.request["source_sha"],
        "source_tree": fixture.request["source_tree"], "code_hashes": fixture.hashes,
        "native_custody_receipt": fixture.receipt, "privileged_signal_custody": signal}
    preserved = {"revision-custody-raw/manifest.json": fixture.raws["manifest.json"]}
    for row in fixture.manifest["files"]:
        preserved["revision-custody-raw/" + row["capture_file"]] = fixture.raws[row["relative_source"]]
    return fixture, receipt, preserved


def test_complete_original_capture_replay_is_documentary_and_has_no_live_authority(monkeypatch):
    _, receipt, preserved = captured_terminal()
    def forbidden(*args, **kwargs):
        pytest.fail("CAPTURED_JSON_MUST_NEVER_MINT_LIVE_AUTHORITY")
    for name in ("authenticate_revision", "validate_revision_approval"):
        monkeypatch.setattr(revisions, name, forbidden)
    for name in ("prove_custody", "verify_live_controller"):
        monkeypatch.setattr(custody, name, forbidden)
    monkeypatch.setattr(boundary, "boundary_witness", forbidden)
    result = gates.verify_capacity_private_pid_capture(receipt, preserved, code_hashes=receipt["code_hashes"])
    assert result["status"] == "CAPTURED_PRIVATE_PID_V2_CLOSURE_VERIFIED"
    assert result["root_cases"] == list(replay.CASES)
    assert all(result[name] is False for name in ("G0_GREEN", "in_process_witness_minted",
        "privileged_launch_authorized", "owner_contract_acceptance_granted", "qualification_claimed"))


@pytest.mark.parametrize("name", sorted(replay.REQUIRED))
def test_every_original_custody_raw_is_required_in_outer_artifact(name):
    fixture, receipt, preserved = captured_terminal()
    row = next(row for row in fixture.manifest["files"] if row["relative_source"] == name)
    del preserved["revision-custody-raw/" + row["capture_file"]]
    with pytest.raises(ValueError, match="CAPABILITY_V2_ORIGINAL_CUSTODY_MEMBER_MISSING"):
        gates.verify_capacity_private_pid_capture(receipt, preserved, code_hashes=receipt["code_hashes"])


@pytest.mark.parametrize("fault", ("missing_manifest", "duplicate_member", "path_escape", "raw_bytes",
    "signal_boundary", "signal_contract", "source_code", "native_receipt", "signal_scope", "missing_hash"))
def test_capture_cannot_be_rebound_or_replaced_by_signal_booleans(fault):
    fixture, receipt, preserved = captured_terminal()
    if fault == "missing_manifest":
        del preserved["revision-custody-raw/manifest.json"]
    elif fault in ("duplicate_member", "path_escape"):
        manifest = copy.deepcopy(fixture.manifest)
        if fault == "duplicate_member": manifest["files"].append(manifest["files"][0])
        else: manifest["files"][0]["capture_file"] = "../foreign.raw"
        preserved["revision-custody-raw/manifest.json"] = custody.wire(manifest)
    elif fault == "raw_bytes":
        preserved["revision-custody-raw/0000.raw"] += b" "
    elif fault in ("signal_boundary", "signal_contract", "signal_scope"):
        key = {"signal_boundary": "boundary_observation_sha256", "signal_contract": "contract_sha256",
            "signal_scope": "scope"}[fault]
        receipt["privileged_signal_custody"][key] = "f" * 64
    elif fault == "source_code":
        receipt["code_hashes"] = {**receipt["code_hashes"], boundary.MEMBER: "f" * 64}
    elif fault == "native_receipt":
        receipt["native_custody_receipt"]["controller_actual_FIN"]["pidfd_exit_observed"] = False
    else:
        receipt["code_hashes"] = {key: value for key, value in receipt["code_hashes"].items() if key != replay.MEMBER}
    with pytest.raises(ValueError, match="CAPABILITY_V2_|PIDV2_CAPTURE_"):
        gates.verify_capacity_private_pid_capture(receipt, preserved, code_hashes=receipt["code_hashes"])


def test_legacy_exact_eacces_still_passes_and_private_enoent_alone_is_rejected():
    probe, privilege = unit_probe_observations()
    assert gates.verify_capacity_probe_observations(probe, privilege) == {
        "actual_observations_coherent": True, "qualification_claimed": False}
    probe["checks"]["original_issuer_root_escape"]["errno"] = errno.ENOENT
    with pytest.raises(ValueError, match="CAPABILITY_EDQUOT_OR_SAME_UID_ESCAPE_NOT_REAL"):
        gates.verify_capacity_probe_observations(probe, privilege)
    probe["checks"].pop("original_issuer_root_escape")
    probe["checks"]["private_pid_boundary_v2"] = {"errno": errno.ENOENT}
    with pytest.raises(ValueError, match="CAPABILITY_PRIVATE_PID_V2_AUTHENTICATED_CAPTURE_GUARDIAN_ROOT_FIN_REPLAY_REQUIRED"):
        gates.verify_capacity_probe_observations(probe, privilege)


def quota_format_context():
    """Coherent captured quota *format*, independent of live/kernel authority."""
    fixture, receipt, _ = captured_terminal()
    closure = fixture.validate()
    request = copy.deepcopy(fixture.probe)
    request.update(schema="porota.rc6.capacity-root-request.v1", mode="capability",
        mount_namespace_inode=11, require_quota_ancestor_seal=True)
    request_raw = custody.wire(request); request_sha = digest(request_raw)
    receipt["root_request_sha256"] = request_sha
    custody_request = {**copy.deepcopy(fixture.request), "runtime_seconds": 290,
        "calibration_request": request["namespace_receipt"]["path"] + "/root-request.json",
        "calibration_request_sha256": request_sha, "boundary_revision": boundary.REVISION,
        "boundary_host_proc_device": 12, "require_quota_ancestor_seal": True}
    custody_raw = custody.wire(custody_request)
    born = copy.deepcopy(fixture.values["broker-birth.json"])
    born["request_sha256"] = digest(custody_raw)
    controller = born["controller"]; guardian = controller["actor"]
    ack = copy.deepcopy(fixture.values["broker-birth.observed.json"])
    ack["birth_sha256"] = digest(custody.wire(born))
    mapping = copy.deepcopy(fixture.values["private-broker.observed.json"])
    mapping["request_sha256"] = digest(custody_raw)
    mapping["external_PID1_ACK"] = ack
    private = copy.deepcopy(fixture.receipt["native_root_proof"]["controller"]["private_PID1"])
    ancestor = copy.deepcopy(fixture.values["private-parent-seal.json"])
    ancestor["scope"] = "ROOT_QUOTA_CUSTODY_ONLY"
    ancestor["boundary"]["owned_fin_log"] = {"descriptor": 4,
        "identity": [33, 999, 0, 0, stat.S_IFREG | 0o600], "mount_id": 55,
        "path": request["namespace_receipt"]["path"] + "/root-worker-native.log"}
    live = {**born, "source_sha": request["source_sha"], "source_tree": request["source_tree"],
        "code_hashes": fixture.hashes, "issuer": request["issuer"], "pid_boundary": private,
        "guardian_native_mapping": mapping, "calibration_request_sha256": request_sha}
    parent = copy.deepcopy(fixture.values["nonroot-hang.native.log"]["drop"]["native_filter"])
    parent["identity"].update(pid=3, native_tid=3, parent_pid=1, start_ticks=300,
        mount_namespace_inode=30)
    child = copy.deepcopy(parent)
    child["identity"].update(pid=8, native_tid=8, parent_pid=3, start_ticks=400)
    child["filters_before"] = parent["filters_after"]
    child["filters_after"] = child["filters_before"] + 1
    child["inherited"] = {"schema": native.INHERITANCE_SCHEMA, "identity": child["identity"],
        "parent_identity": parent["identity"], "parent_receipt_sha256": digest(native.canonical(parent)),
        "source_binding": child["source_binding"], "fork_exec_inheritance_observed": True,
        "filters_before_own_installation": child["filters_before"],
        "native_denials_before_own_installation": child["denial_probes"], "G0_G8_qualification": False}
    probe, privilege = unit_probe_observations()
    privilege.update(native_filter=parent, mount_namespace_inode=30, gid=1001)
    actor = process(8, parent=3, ns=20, ticks="400", group=controller["unit"], uid=1001,
        caps=dict.fromkeys(guardian["capabilities"], 0))
    actor.update(mount_namespace_inode=30, supplementary_groups=[], no_new_privileges=1,
        seccomp_mode=2, seccomp_filters=child["filters_after"])
    original_observation = fixture.receipt["native_root_proof"]["private_pid_boundary_v2"]["observation"]
    observed = {"schema": "porota.rc6.quota-private-pid-observation.v2", "revision": boundary.REVISION,
        "contract_sha256": boundary.contract_sha256(), "actor": actor,
        "mount_view": copy.deepcopy(original_observation["mount_view"]),
        "descriptors": copy.deepcopy(original_observation["descriptors"]), "external_issuer": mapping["issuer_actual"],
        "hidden_magiclinks": {name: {"errno": errno.ENOENT, "write_performed": False} for name in boundary.MAGICLINKS},
        "host_signal_errno": errno.ESRCH, "existing_ROOT_descriptor_denial": {
            "errno": errno.EACCES, "write_performed": False}, "ENOENT_alone_accepted": False,
        "launch_authority_granted": False, "qualification_claimed": False}
    probe["checks"].pop("original_issuer_root_escape")
    probe["checks"]["private_pid_boundary_v2"] = observed
    probe["native_filter"] = child
    worker_kernel = kernel(3, limit=270)
    guardian_kernel = kernel(4009, supervisor=guardian["pid"], limit=280)
    root = {"controller": {"external": controller, "private_PID1": private,
        "unit": controller["unit"], "ROOT_actor_boundary_certified": True,
        "quota_ancestor_sealed": True, "root_actor_seal": ancestor}, "worker_kernel": worker_kernel,
        "quota_ancestor_seal": ancestor, "quota_ancestor_mounts": {"fixture_only": True},
        "manager_sha256": custody.MANAGER_SHA256, "root_native_FIN_closed": True, "root_native_phase_green": True}
    original_worker = custody.wire({"schema": "porota.rc6.capacity-worker.v1", "request_sha256": request_sha,
        "report": {"privilege": privilege, "raw_files": []}})
    relay = {"schema": "porota.rc6.capacity-custody-worker.v1", "request_sha256": request_sha,
        "root_worker": {"path": "root-worker-native.log", "bytes": len(original_worker), "sha256": digest(original_worker)},
        "privileged_controller": root}
    private_relay_raw = custody.wire(relay)
    external = {"controller": controller, "private_broker_kernel": guardian_kernel,
        "private_PID1_actual_mapping": mapping, "private_PID1_native_pidfd_FIN": True,
        "private_broker_raw_sha256": digest(private_relay_raw), "original_manager_sha256": custody.MANAGER_SHA256,
        "actual_root_FIN_closed": True, "root_phase_green": True}
    relay["external_guardian"] = external
    receipt["root_controllers"] = {"privileged_controller": root, "external_guardian": external}
    receipt["root_service_actual_FIN"] = copy.deepcopy(fixture.fin_receipts["controller_actual_FIN"])
    values = {"root-request.json": request, "custody-request.json": custody_request,
        "broker-birth.json": born, "broker-birth.observed.json": ack, "broker-bridge.json": fixture.values["broker-bridge.json"],
        "guardian-controller.json": born, "live-root-controller.json": live,
        "private-broker-birth.json": {key: value for key, value in mapping.items() if key not in
            ("schema", "guardian_actual", "issuer_actual", "source_sha", "source_tree", "external_PID1_ACK")},
        "private-broker.observed.json": mapping, "quota-ancestor-seal.json": ancestor,
        "producer-native.log": relay, "loop-birth.json": {"actor_pid": 3, "actor_start_ticks": "300",
            "actor_mount_namespace_inode": 30, "boot_id": request["issuer"]["boot_id"]}}
    controls = {name: custody.wire(value) for name, value in values.items()}
    controls.update({"private-broker-native.log": private_relay_raw, "root-worker-native.log": original_worker})
    request_keys = ("source_sha", "source_tree", "binding", "owner_uid", "owner_gid", "issuer", "mount_namespace_inode",
        "boundary_revision", "boundary_host_proc_device", "require_quota_ancestor_seal")
    raw = {"supervisor-filter.json": custody.wire(parent), "quota-boundary-context.json": custody.wire({
        "request": {**{key: request[key] for key in request_keys}, "original_request_sha256": request_sha},
        "proof": {**live, "quota_ancestor_seal": ancestor}})}
    context = {"receipt": receipt, "controls": controls, "request_raw": request_raw, "required_raw": raw,
        "probe_kernel": kernel(8, supervisor=3, uid=1001, limit=60), "code_hashes": fixture.hashes,
        "revision_capture": closure}
    return probe, privilege, context


def test_complete_quota_context_requires_all_original_bytes_and_grants_no_native_authority(monkeypatch):
    probe, privilege, context = quota_format_context()
    def forbidden(*args, **kwargs):
        pytest.fail("DOCUMENTARY_QUOTA_REPLAY_CANNOT_TOUCH_LIVE_AUTHORITY")
    monkeypatch.setattr(custody, "verify_live_controller", forbidden)
    monkeypatch.setattr(revisions, "authenticate_revision", forbidden)
    assert gates.verify_capacity_probe_observations(probe, privilege, private_pid_context=context) == {
        "actual_observations_coherent": True, "qualification_claimed": False}
    assert gates.verify_capacity_quota_private_pid(probe, privilege, context) == {
        "quota_private_pid_branch_original_bytes_verified": True, "qualification_claimed": False}


@pytest.mark.parametrize("fault", ("context_missing", "original_context", "root_request", "source_binding", "root_fin",
    "guardian_fin", "ancestor", "live_root", "descriptor", "host_proc", "host_pid", "namespace_collision",
    "issuer_birth", "actor_pid", "actor_birth", "privileged_caps", "unsealed", "filter_parent", "filter_kernel_parent", "root_edquot_only",
    "existing_root_enoent", "signal_addressable", "hidden_magiclink", "old_eacces_fallback", "wrong_revision"))
def test_quota_private_pid_branch_rejects_complete_chain_attacks(fault):
    probe, privilege, context = quota_format_context()
    observed = probe["checks"]["private_pid_boundary_v2"]
    if fault == "context_missing": context.pop("revision_capture")
    elif fault == "original_context": context["required_raw"]["quota-boundary-context.json"] = b"{}"
    elif fault == "root_request": context["request_raw"] += b" "
    elif fault == "source_binding": context["revision_capture"]["binding"] = {}
    elif fault in ("root_fin", "guardian_fin"):
        key = "privileged_controller" if fault == "root_fin" else "external_guardian"
        name = "root_native_FIN_closed" if fault == "root_fin" else "actual_root_FIN_closed"
        context["receipt"]["root_controllers"][key][name] = False
    elif fault in ("ancestor", "live_root"):
        name = "quota-ancestor-seal.json" if fault == "ancestor" else "live-root-controller.json"
        context["controls"][name] = b"{}"
    elif fault == "descriptor": observed["descriptors"].append({"descriptor": 3})
    elif fault == "host_proc": observed["mount_view"]["proc_device"] = 12
    elif fault == "host_pid": observed["actor"]["pid_namespace_inode"] = 10
    elif fault == "namespace_collision": observed["actor"]["namespace_pids"] = [4000, 8]
    elif fault == "issuer_birth": observed["external_issuer"]["start_ticks"] = "999"
    elif fault == "actor_pid": observed["actor"]["pid"] = 99
    elif fault == "actor_birth": observed["actor"]["start_ticks"] = "999"
    elif fault == "privileged_caps": observed["actor"]["capabilities"]["CapEff"] = 1
    elif fault == "unsealed": observed["actor"]["no_new_privileges"] = 0
    elif fault == "filter_parent": probe["native_filter"]["inherited"]["parent_receipt_sha256"] = "f" * 64
    elif fault == "filter_kernel_parent": context["probe_kernel"]["supervisor_pid"] = 99
    elif fault == "root_edquot_only": probe["checks"]["private_pid_boundary_v2"] = {"errno": errno.ENOENT}
    elif fault == "existing_root_enoent": observed["existing_ROOT_descriptor_denial"]["errno"] = errno.ENOENT
    elif fault == "signal_addressable": observed["host_signal_errno"] = 0
    elif fault == "hidden_magiclink": observed["hidden_magiclinks"]["root"]["errno"] = errno.EACCES
    elif fault == "old_eacces_fallback": probe["checks"]["original_issuer_root_escape"] = {"errno": errno.EACCES}
    else: observed["revision"] = "PRIVATE_PID_BOUNDARY_V1"
    with pytest.raises(ValueError, match="CAPABILITY_|PIDV2_|NATIVE_FILTER_|ROOT_SEAL_"):
        gates.verify_capacity_probe_observations(probe, privilege, private_pid_context=context)


def owner_decision_format(monkeypatch):
    """Actual owner predicates, bounded mocked GET transport, no auth mint."""
    transport = ReleasedDiagnosticApi(monkeypatch)
    inventory = {"files": [{"path": boundary.MEMBER,
        "sha256": digest((admission.ROOT / boundary.MEMBER).read_bytes())}]}
    transport.rewrite_pair(SOURCE_MANIFEST_SHA256=digest(admission.wire(inventory)))
    monkeypatch.setattr(admission, "frozen_source_inventory", lambda *args: inventory)
    monkeypatch.setattr(gates, "productive_contract", lambda *args: PLAN)
    anchors = admission.administrative_anchors(OWNER, get=transport.read, now=NOW)
    auth = admission.fields(transport.launch["body"])
    scope = admission.diagnostic_scope("capacity-probe", admission.document(auth["CAPACITY_DIAGNOSTIC_SCOPE_JSON"]))
    run = copy.deepcopy(transport.documents["/actions/runs/" + str(RUN)])
    run.update(status="completed", conclusion="success", created_at=lease.text_stamp(NOW))
    transport.documents["/actions/runs/" + str(RUN)] = run
    receipt = {"binding": {"owner_id": OWNER}, "source_sha": SHA, "source_tree": TREE,
        "read_contract_sha256": PLAN, "privileged_signal_custody": {
            "approval_body_sha256": digest(transport.launch["body"].encode())}}
    before = {"schema": admission.DIAGNOSTIC_ADMISSION_SCHEMA,
        "authorization_schema": admission.DIAGNOSTIC_AUTHORIZATION_SCHEMA, "status": "ADMITTED_DIAGNOSTIC_NOT_STARTED",
        "owner_session": OWNER, "source_sha": SHA, "source_tree": TREE, "gate": "capacity-probe",
        "launch_receipt_url": transport.launch["html_url"], "launch_body_sha256": digest(transport.launch["body"].encode()),
        "source_manifest_sha256": auth["SOURCE_MANIFEST_SHA256"], "read_contract_sha256": PLAN,
        "scope": scope, "capability_prerequisite": None, "qualification_claimed": False,
        "diagnostic_owner_scope": admission.successor_diagnostic_scope(auth, OWNER, anchors),
        "contract_revision_binding": admission._diagnostic_revision_pointer(auth, transport.launch, SHA, TREE, OWNER, "capacity-probe"),
        "actions_origin": {"run": run}}
    after = {**copy.deepcopy(before), "status": "CAPTURE_ONLY_NATIVE_ALREADY_FINISHED"}
    preserved = {"controls/admission.json": admission.wire(before),
        "controls/after-outer-fin-admission.json": admission.wire(after)}
    closure = {"contract_sha256": boundary.contract_sha256()}
    return transport, receipt, preserved, run, scope, closure


def verify_owner(data, **kwargs):
    transport, receipt, preserved, run, scope, closure = data
    return gates.verify_capacity_revision_owner(receipt, preserved, run=run, branch=BRANCH,
        get=transport.read, scope=scope, revision_capture=closure, now=kwargs.get("now", NOW))


def test_owner_archive_replay_requires_explicit_pair_and_current_lease_without_mint(monkeypatch):
    data = owner_decision_format(monkeypatch)
    def forbidden(*args, **kwargs):
        pytest.fail("ARCHIVAL_ACCEPTANCE_CANNOT_MINT_A_PAST_LIVE_APPROVAL")
    monkeypatch.setattr(revisions, "authenticate_revision", forbidden)
    monkeypatch.setattr(revisions, "validate_revision_approval", forbidden)
    result = verify_owner(data)
    assert result["archived_acceptance_authenticated"] is True
    assert result["current_owner_and_revocation_checked"] is True
    assert result["scope"] == "DOCUMENTARY_CAPABILITY_REPLAY_ONLY"
    assert result["new_workload_authorization_granted"] is False
    assert result["qualification_claimed"] is False


def test_historical_acceptance_can_be_replayed_under_new_lease_but_never_renewed(monkeypatch):
    data = owner_decision_format(monkeypatch)
    transport, *_ = data
    later = NOW + timedelta(minutes=11)
    for issue in (471, 473):
        row = scoped_row(issue, 610000 + issue, later - timedelta(seconds=1), OWNER)
        transport.rows[issue].append(row)
        transport.documents["/issues/comments/" + str(row["id"])] = row
    def forbidden(*args, **kwargs):
        pytest.fail("HISTORICAL_REPLAY_IS_NOT_LEASE_RENEWAL")
    monkeypatch.setattr(revisions, "authenticate_revision", forbidden)
    assert verify_owner(data, now=later)["new_workload_authorization_granted"] is False


def artifact_format_for_source_order(tmp_path):
    """ZIP/transport format fixture stops before any native closure validation."""
    _, receipt, inner = captured_terminal()
    receipt["binding"] = {**receipt["binding"], "attempt_id": str(RUN) + ":1"}
    receipt["code_hashes"] = {**receipt["code_hashes"], "scripts/rc6_capacity_calibration.py": "d" * 64}
    receipt.update(schema="porota.rc6.capacity-calibration.v1", mode="capability", status="GREEN",
        read_contract_sha256=PLAN, actual_capability_proved=True, qualification_claimed=False,
        source_unchanged=True, payload_upload_safe=True,
        limits={"backing_image_bytes": 5 * 1024**3, "project_hard_limit_bytes": 512 * 1024**2,
            "residual_reserve_bytes": 4 * 1024**3}, cleanup={"namespace_removed": True},
        capability_checks=dict.fromkeys(("project_hard_limit_enforced", "same_uid_escape_blocked",
            "project_reassignment_blocked", "quota_mutation_blocked", "privilege_drop_seccomp_enforced"), True))
    binding = receipt["binding"]
    files, members = [], {}
    for index, (name, raw) in enumerate(inner.items()):
        member = str(index).zfill(4) + ".raw"
        files.append({"capture_file": member, "relative_source": name, "bytes": len(raw), "sha256": digest(raw)})
        members["sealed/" + member] = raw
    capture_raw = custody.wire({"binding": binding, "actual_owned_fin_closed": True, "phase_green": True, "files": files})
    native_fin_raw = custody.wire({"kernel": kernel(99, supervisor=3, uid=1001, limit=300)})
    producer_raw = custody.wire(receipt)
    result = {"schema": "porota.rc6.capacity-diagnostic-result.v1", "status": "GREEN_DIAGNOSTIC_ONLY",
        "gate": "capacity-probe", "source_sha": SHA, "source_tree": TREE,
        **dict.fromkeys(("qualification_claimed", "G0_G8_claimed", "financial_tick_executed",
            "runtime_validated", "final_candidate_eligible"), False), "real_orders_sent": 0,
        "producer_receipt": receipt, "producer_receipt_ref": {"path": "producer.json", "sha256": digest(producer_raw)},
        "outer_native_fin_ref": {"path": "outer-fin.json", "sha256": digest(native_fin_raw)},
        "capture_manifest_ref": {"path": "outer-capture.json", "sha256": digest(capture_raw)},
        "actual_outer_cleanup": {"binding": binding, "namespace_removed": True, "actual_owned_fin_closed": True,
            "phase_green": True, "foreign_paths_removed": 0, "runtime_paths_authorized": False,
            "capture_manifest_sha256": digest(capture_raw)}}
    result_raw = custody.wire(result)
    members.update({"diagnostic-result.json": result_raw, "producer.json": producer_raw,
        "outer-fin.json": native_fin_raw, "outer-capture.json": capture_raw})
    packed = io.BytesIO()
    with zipfile.ZipFile(packed, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, raw in members.items(): archive.writestr(name, raw)
    archive_raw = packed.getvalue(); artifact_id = 991
    reference = {"artifact_id": artifact_id, "run_id": RUN, "run_attempt": 1,
        "artifact_digest": "sha256:" + digest(archive_raw), "receipt_member": "diagnostic-result.json",
        "receipt_sha256": digest(result_raw)}
    stamp = lease.text_stamp(NOW)
    run = {"id": RUN, "head_sha": SHA, "head_branch": BRANCH, "run_attempt": 1, "status": "completed",
        "conclusion": "success", "path": gates.WORKFLOW, "event": "workflow_dispatch",
        "display_title": "RC6 material capacity-probe @ " + SHA,
        "repository": {"id": gates.REPOSITORY_ID, "full_name": gates.REPOSITORY},
        "head_repository": {"id": gates.REPOSITORY_ID}, "created_at": stamp, "run_started_at": stamp, "updated_at": stamp}
    artifact = {"id": artifact_id, "digest": reference["artifact_digest"], "size_in_bytes": len(archive_raw),
        "expired": False, "created_at": stamp, "expires_at": "2099-01-01T00:00:00Z",
        "workflow_run": {"id": RUN, "head_sha": SHA, "repository_id": gates.REPOSITORY_ID,
            "head_repository_id": gates.REPOSITORY_ID}}
    jobs = {"total_count": 1, "jobs": [{"id": 991, "run_id": RUN, "status": "completed", "conclusion": "success",
        "labels": ["ubuntu-24.04"], "runner_group_name": "GitHub Actions", "runner_name": "GitHub Actions 1",
        "started_at": stamp, "completed_at": stamp}]}
    routes = {"/actions/artifacts/" + str(artifact_id): artifact, "/actions/runs/" + str(RUN): run,
        "/actions/runs/" + str(RUN) + "/attempts/1": run,
        "/actions/runs/" + str(RUN) + "/attempts/1/jobs?per_page=100": jobs}
    def download(_identifier, path, **kwargs):
        path.write_bytes(archive_raw)
    return receipt, reference, routes.__getitem__, download


def test_actual_source_inventory_is_verified_before_any_custody_or_quota_replay(tmp_path, monkeypatch):
    receipt, reference, get, download = artifact_format_for_source_order(tmp_path)
    inventory = {"files": [{"path": name, "sha256": value} for name, value in receipt["code_hashes"].items()]}
    inventory["files"][0]["sha256"] = "f" * 64
    called = []
    def forbidden(*args, **kwargs):
        called.append(True)
        pytest.fail("NO_REPLAY_BEFORE_AUTHENTIC_SOURCE_INVENTORY")
    monkeypatch.setattr(admission, "frozen_source_inventory", lambda *args: inventory)
    monkeypatch.setattr(gates, "verify_capacity_private_pid_capture", forbidden)
    monkeypatch.setattr(gates, "verify_capacity_kernel_probe", forbidden)
    with pytest.raises(ValueError, match="CAPABILITY_SOURCE_CODE_HASHES_REBOUND"):
        gates.verify_capacity_capability_artifact(reference, source_sha=SHA, source_tree=TREE,
            read_contract_sha256=PLAN, evidence_root=tmp_path / "own-artifact", get=get,
            download=download, authenticated_branch=BRANCH)
    assert called == []


def test_authentic_source_proceeds_to_required_original_custody_replay(tmp_path, monkeypatch):
    receipt, reference, get, download = artifact_format_for_source_order(tmp_path)
    inventory = {"files": [{"path": name, "sha256": value} for name, value in receipt["code_hashes"].items()]}
    calls = []
    monkeypatch.setattr(admission, "frozen_source_inventory", lambda *args: inventory)
    class StopBeforeNativeValidation(ValueError):
        pass
    def require_original(actual, preserved, *, code_hashes):
        calls.append("ORIGINAL_CAPTURE_REPLAY_AFTER_SOURCE")
        assert actual["code_hashes"] == code_hashes == receipt["code_hashes"]
        assert "revision-custody-raw/manifest.json" in preserved
        raise StopBeforeNativeValidation("DECLARED_FORMAT_FIXTURE_HAS_NO_NATIVE_QUALIFICATION")
    monkeypatch.setattr(gates, "verify_capacity_private_pid_capture", require_original)
    monkeypatch.setattr(gates, "verify_capacity_kernel_probe", lambda *args, **kwargs: pytest.fail("Native capture still required"))
    with pytest.raises(StopBeforeNativeValidation):
        gates.verify_capacity_capability_artifact(reference, source_sha=SHA, source_tree=TREE,
            read_contract_sha256=PLAN, evidence_root=tmp_path / "own-artifact", get=get,
            download=download, authenticated_branch=BRANCH)
    assert calls == ["ORIGINAL_CAPTURE_REPLAY_AFTER_SOURCE"]


@pytest.mark.parametrize("fault", ("missing_before", "missing_after", "before_pointer", "after_pointer", "body_hash",
    "paired_body", "owner_edited", "scope", "source_manifest", "source_tree", "wrong_branch", "contract",
    "before_creation", "after_expiry", "unbounded", "current_source", "competing_owner", "revoked"))
def test_owner_replay_cannot_accept_stale_rebound_or_revoked_contract_decision(monkeypatch, fault):
    data = owner_decision_format(monkeypatch)
    transport, receipt, preserved, run, scope, closure = data
    if fault in ("missing_before", "missing_after"):
        del preserved["controls/admission.json" if fault == "missing_before" else "controls/after-outer-fin-admission.json"]
    elif fault in ("before_pointer", "after_pointer", "scope", "source_manifest", "source_tree", "wrong_branch"):
        name = "controls/admission.json" if fault != "after_pointer" else "controls/after-outer-fin-admission.json"
        row = admission.document(preserved[name])
        if fault in ("before_pointer", "after_pointer"): row["contract_revision_binding"]["approval_body_sha256"] = "f" * 64
        elif fault == "scope": row["scope"]["backing_image_bytes"] += 1
        elif fault == "source_manifest": row["source_manifest_sha256"] = "f" * 64
        elif fault == "source_tree": row["source_tree"] = "f" * 40
        else: row["actions_origin"]["run"]["head_branch"] = "main"
        preserved[name] = admission.wire(row)
    elif fault == "body_hash": receipt["privileged_signal_custody"]["approval_body_sha256"] = "f" * 64
    elif fault == "paired_body": transport.twin["body"] += "REAL_TRADING=APPROVED\n"
    elif fault == "owner_edited": transport.launch["updated_at"] = lease.text_stamp(NOW)
    elif fault == "contract": closure["contract_sha256"] = "f" * 64
    elif fault == "before_creation": run["created_at"] = lease.text_stamp(NOW - timedelta(seconds=30))
    elif fault == "after_expiry": run["created_at"] = lease.text_stamp(NOW + timedelta(minutes=10))
    elif fault == "unbounded":
        transport.rewrite_pair(SOURCE_LEASE_EXPIRES_UTC=lease.text_stamp(NOW + timedelta(hours=1)))
        for name in preserved:
            row = admission.document(preserved[name])
            row["launch_body_sha256"] = digest(transport.launch["body"].encode())
            row["contract_revision_binding"]["approval_body_sha256"] = row["launch_body_sha256"]
            preserved[name] = admission.wire(row)
        receipt["privileged_signal_custody"]["approval_body_sha256"] = digest(transport.launch["body"].encode())
    elif fault == "current_source": transport.documents["/git/ref/heads/" + BRANCH]["object"]["sha"] = "f" * 40
    elif fault == "competing_owner":
        for issue in (471, 473):
            row = scoped_row(issue, 510000 + issue, NOW - timedelta(seconds=1), "CODEX_FOREIGN_UNIT")
            transport.rows[issue].append(row)
            transport.documents["/issues/comments/" + str(row["id"])] = row
    else:
        row = comment(471, 510471, body({"SOURCE_SHA": SHA, "CONTRACT_REVISION": boundary.REVISION,
            "CONTRACT_SCOPE": "capacity-probe", "RC6_CONTRACT_REVISION_AUTHORIZATION": "REVOKED"}),
            lease.text_stamp(NOW - timedelta(seconds=1)))
        transport.rows[471].append(row)
        transport.documents["/issues/comments/" + str(row["id"])] = row
    with pytest.raises(ValueError):
        verify_owner(data)
