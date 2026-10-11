"""Source-owned G5A transport assembly; only G5B can certify finished evidence."""
from __future__ import annotations
import argparse
import ctypes
import hashlib
import os
from pathlib import Path
import re
import stat
import sys
import time

ROOT = Path(__file__).absolute().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from scripts import rc6_capacity_calibration as io
from scripts import rc6_g5a_native_runner as runner
from scripts import rc6_g5b_evidence as evidence
from scripts import rc6_architectural_gates as gates

MAX_TOTAL = 128 * 1024**2
MAX_MEMBER = 16 * 1024**2

def require(ok, reason):
    if not ok: raise ValueError(reason)

def publish(root, name, raw, *, written):
    root = runner.canonical_path(str(root))
    details = root.lstat()
    require(os.getuid() == os.geteuid() > 0 and os.getgid() == os.getegid()
        and (details.st_uid, details.st_gid) == (os.getuid(), os.getgid())
        and stat.S_ISDIR(details.st_mode) and stat.S_IMODE(details.st_mode) == 0o700
        and type(name) is str and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,200}', name),
        'G5A_FAMILY_OWN0700_LITERAL_CONTROL_ROOT_REQUIRED')
    require(type(written) is int and 0 <= written < MAX_TOTAL
        and type(raw) is bytes and 0 < len(raw) <= MAX_MEMBER
        and written + len(raw) <= MAX_TOTAL, 'G5A_FAMILY_ORIGINAL_BOUNDED_SMALL_CONTROLS_REQUIRED')
    path = Path(root) / name
    require(not path.exists() and not any(node.is_symlink() for node in (path, *path.parents)),
        'G5A_FAMILY_FRESH_NOFOLLOW_CONTROL_REQUIRED')
    io.require_capacity(io.filesystem(root), MAX_TOTAL - written)
    io.publish_bytes(path, raw)
    return {'path': name, 'bytes': len(raw), 'sha256': io.digest(raw)}, written + len(raw)

def capture_private_pid_prerequisite(reference, *, root, controls, written, reader, source_sha, source_tree, branch):
    from scripts import rc6_private_pid_capture_replay as replay
    verified = gates.verify_capacity_capability_artifact(reference, source_sha=source_sha, source_tree=source_tree,
        read_contract_sha256=gates.productive_contract(ROOT), evidence_root=root/'g0-private-pid-input',
        get=reader.request, download=reader.download, authenticated_branch=branch, source_repository=ROOT)
    archive = root/'g0-private-pid-input'/'capability.zip'
    result = io.decode(gates.capture_member(archive, reference['receipt_member']))
    outer = io.decode(gates.capture_member(archive, result['capture_manifest_ref']['path']))
    original = {}
    for row in outer['files']:
        if not row['relative_source'].startswith('revision-custody-raw/'): continue
        raw = gates.capture_member(archive, 'sealed/'+row['capture_file'])
        require(len(raw) == row['bytes'] and io.digest(raw) == row['sha256'],
            'G5A_FAMILY_AUTHENTIC_G0_PRIVATE_PID_RAW_REBOUND')
        original[row['relative_source'].removeprefix('revision-custody-raw/')] = raw
    manifest = io.decode(original['manifest.json'])
    mapping = {row['relative_source']: row['capture_file'] for row in manifest['files']}
    files = {}
    for number, name in enumerate(sorted({'manifest.json', *replay.REQUIRED})):
        raw = original['manifest.json'] if name == 'manifest.json' else original[mapping[name]]
        ref, written = publish(controls, 'g0-private-pid-'+str(number).zfill(4)+'.raw', raw, written=written)
        files[name] = {**ref,'path':'controls/'+ref['path']}
    return {'scope':'G0_NATIVE_BOUNDARY_PREREQUISITE','receipt':verified['receipt']['native_custody_receipt'],
        'capacity_receipt':verified['receipt'],'files':files}, written


def assemble_draft(capture, packed, review, native_result, *, root, approval, capability_reference, reader, branch):
    from scripts import rc6_g5a_raw_capture as captures
    from scripts import rc6_g5a_parts as parts
    from scripts import rc6_g5a_carrier as carrier
    from scripts import rc6_owned_gate_lease as leases
    carrier.validate_native_result(native_result)
    documentary = captures.validate_capture(capture, require_complete_original_1202=True)
    package = parts.validate_package(packed)
    root = runner.canonical_path(str(root))
    require(root == Path(native_result['controls']) and root.lstat().st_uid == os.getuid()
        and str(capture.path) == package['payload_root'], 'G5A_FAMILY_ACTUAL_ISSUED_SOURCE_ROOT_REQUIRED')
    require(review.get('status') == 'ACTUAL_POST_ROOT_FIN_NATIVE_READERS_CAPTURED_NOT_G5'
        and review.get('source_sha') == documentary['source_sha']
        and review.get('source_tree') == documentary['source_tree'], 'G5A_FAMILY_ACTUAL_SOURCE_NATIVE_READER_CAPTURE_REQUIRED')
    family = root / 'family'; controls = root / 'controls'
    require(not os.path.lexists(family) and not os.path.lexists(controls), 'G5A_FAMILY_EXCLUSIVE_NEW_CONTROL_DIRECTORIES_REQUIRED')
    family.mkdir(mode=0o700); controls.mkdir(mode=0o700)
    written, references = 0, {}
    for role, reference in package['Source_RAW_roles'].items():
        references[role] = {'path': capture.path.relative_to(root).as_posix()+'/'+reference['path'],
            'bytes': reference['bytes'], 'sha256': reference['sha256']}
    for role, reference in review['roles'].items():
        raw = runner.read_input(reference['path'], expected_sha256=reference['sha256'], maximum=MAX_MEMBER)
        require(len(raw) == reference['bytes'], 'G5A_FAMILY_NATIVE_READER_RAW_BYTES_REBOUND')
        ref, written = publish(family, role+'.raw', raw, written=written)
        references[role] = {**ref, 'path': 'family/'+ref['path']}
    native_raw = capture.read_original('project/result.json')
    horizon = {'schema': 'rc6.g5a-source-horizon-fin-transport.v2',
        'source_sha': documentary['source_sha'], 'source_tree': documentary['source_tree'],
        'native_result_sha256': io.digest(native_raw), 'native_result_bytes': len(native_raw),
        'native_finalizer5_green_before_payload_reads': True, 'physical_custody_closed': True,
        'errors': [], 'G5_GREEN': False, 'qualification_claimed': False}
    ref, written = publish(family, 'Horizon.raw', io.wire(horizon), written=written)
    references['Horizon'] = {**ref, 'path': 'family/'+ref['path']}
    commands, monitors = [], [native_result['lease'], *review['current_owner_monitors']]
    labels = ('g5a-original1202', 'g5-reader-collection', 'g5-reader-execution')
    for monitor, label in zip(monitors, labels, strict=True):
        require(type(monitor) is leases.GateLeaseMonitor and monitor.command_label == label
            and monitor.stop_reason is None, 'G5A_FAMILY_ACTUAL_NATIVE_SOURCE_OWNER_MONITOR_REQUIRED')
        raw = runner.read_input(monitor.final_path, maximum=MAX_MEMBER)
        receipt = io.decode(raw)
        require(receipt.get('status') == 'GREEN' and receipt.get('physical_fin_closed') is True,
            'G5A_FAMILY_ORIGINAL_MONITOR_FIN_RED')
        ref, written = publish(controls, label+'.lease.json', raw, written=written)
        kernel = (native_result['kernel'] if label == labels[0]
            else io.decode(runner.read_input(review['roles']['g5b_reader_'+('collection' if label == labels[1] else 'execution')+'_kernel']['path'])))
        ref_kernel, written = publish(controls, label+'.kernel.json', leases.wire(kernel), written=written)
        commands.append({'label': label, 'lease': {**ref, 'path': 'controls/'+ref['path']},
            'kernel': {**ref_kernel, 'path': 'controls/'+ref_kernel['path']}})
        for name in sorted(monitor.objects):
            path = monitor.root / (leases.OBJECT_PREFIX+name+'.json')
            raw = runner.read_input(path, maximum=leases.MAX_OBJECT_BYTES)
            target = controls/path.name
            if target.exists():
                require(runner.read_input(target) == raw, 'G5A_FAMILY_OWNER_OBJECT_HASH_COLLISION')
            else:
                _, written = publish(controls, path.name, raw, written=written)
    index = {'schema':'porota.rc6.owned-gate-lease-index.v1', 'source_sha':documentary['source_sha'],
        'source_tree':documentary['source_tree'], 'run_id':int(os.environ['GITHUB_RUN_ID']), 'run_attempt':1,
        'commands':commands, 'object_prefix':'controls/'+leases.OBJECT_PREFIX}
    ref_index, written = publish(family, 'ownership-index.json', io.wire(index), written=written)
    custody_controls = {}
    from scripts.rc6_g5a_custody_replay import CONTROL_NAMES
    for name in sorted(CONTROL_NAMES):
        path = (native_result['request']['g5a_prerequisites_manifest_path']
            if name == 'g5a-prerequisites-manifest.json' else native_result['namespace'].path/name)
        raw = runner.read_input(path, maximum=MAX_MEMBER)
        ref, written = publish(controls, 'custody-'+name, raw, written=written)
        custody_controls[name] = {**ref, 'path':'controls/'+ref['path']}
    private_capture, written = capture_private_pid_prerequisite(capability_reference, root=root, controls=controls,
        written=written, reader=reader, source_sha=documentary['source_sha'], source_tree=documentary['source_tree'], branch=branch)
    custody = {'schema':'rc6.g5a-custody-captured.v2', 'source_sha':documentary['source_sha'],
        'source_tree':documentary['source_tree'], 'binding':documentary['binding'],
        'native_result':{key:native_result[key] for key in ('kernel','request','custody_request','service_state','actual_root_service_FIN')},
        'original_raw_capture':documentary, 'controls':custody_controls,
        'private_pid_capture': private_capture}
    ref, written = publish(family, 'g5a_root_custody.raw', io.wire(custody), written=written)
    references['g5a_root_custody'] = {**ref, 'path':'family/'+ref['path']}
    quota = evidence.capture_document(capture.read_original(parts.SOURCE_ROLES['g5a_quota']))
    pointer = {'approval_url':approval.approval_url, 'approval_body_sha256':approval.approval_body_sha256,
        'paired_comment_id':approval.paired_comment_id, 'paired_body_sha256':approval.paired_body_sha256,
        'accepted_utc':quota['owner_decision_lifecycle']['accepted_utc']}
    require(set(references) == evidence.REQUIRED_ROLES-{'g5b_frame_parts','g5b_complete'},
        'G5A_FAMILY_COMPLETE_ACTUAL_NATIVE_ROLES_REQUIRED')
    draft = {'schema':'rc6.g5a-source-family-draft.v2', 'source_sha':documentary['source_sha'],
        'source_tree':documentary['source_tree'], 'roles':references, 'owner_revision_decision':pointer,
        'ownership_index':{**ref_index, 'path':'family/'+ref_index['path']},
        'started_utc':io.decode(runner.read_input(native_result['lease'].final_path))['launch_intent_utc'],
        'G5_GREEN':False, 'qualification_claimed':False}
    ref, _ = publish(root, 'g5a-family-draft.json', io.wire(draft), written=written)
    return {'draft':str(root/ref['path']), 'draft_sha256':ref['sha256'], 'primary_root':str(root)}

def seal_family(manifest, *, manifest_sha256, draft_path, draft_sha256, reader):
    from scripts import rc6_g5a_parts as parts
    joined = parts.seal_documentary_uploaded_parts(manifest, expected_manifest_sha256=manifest_sha256,
        get=reader.request, run_id=int(os.environ['GITHUB_RUN_ID']))
    raw = runner.read_input(draft_path, expected_sha256=draft_sha256, maximum=MAX_MEMBER)
    draft = io.decode(raw); root = Path(draft_path).parent
    require(draft.get('schema') == 'rc6.g5a-source-family-draft.v2'
        and draft.get('G5_GREEN') is False and draft.get('qualification_claimed') is False
        and draft['source_sha'] == joined['frame_manifest']['source_sha']
        and draft['source_tree'] == joined['frame_manifest']['source_tree'], 'G5A_FAMILY_EXACT_SOURCE_DRAFT_REQUIRED')
    captures = {}
    for role, ref in draft['roles'].items():
        require(type(ref.get('path')) is str and not Path(ref['path']).is_absolute()
            and '..' not in Path(ref['path']).parts, 'G5A_FAMILY_CANONICAL_RAW_REFERENCE_REQUIRED')
        captures[role] = runner.read_input(root/ref['path'], expected_sha256=ref['sha256'], maximum=MAX_MEMBER)
        require(len(captures[role]) == ref['bytes'], 'G5A_FAMILY_ACTUAL_RAW_SIZE_REBOUND')
    captures['g5b_frame_parts'] = io.wire(joined['frame_manifest'])
    complete = evidence.build_capture_envelope(captures, source_sha=draft['source_sha'], source_tree=draft['source_tree'],
        owner_revision_decision=draft['owner_revision_decision'])
    captures['g5b_complete'] = io.wire(complete)
    refs = dict(draft['roles']); written = 0
    for role in ('g5b_frame_parts','g5b_complete'):
        ref, written = publish(root/'family', role+'.raw', captures[role], written=written)
        refs[role] = {**ref, 'path':'family/'+ref['path']}
    receipt = gates.receipt_base('G5',source_sha=draft['source_sha'],source_tree=draft['source_tree'],
        read_contract_sha256=gates.productive_contract(ROOT),started_utc=draft['started_utc'],
        checks={'awaiting_full_native_G5B':False},native_evidence={**refs,'ownership_index':draft['ownership_index']},
        contract_revision='G5A_BOUNDED_EVIDENCE_V2',source_unchanged=False,native_exit_code=0)
    receipt['status'] = 'EVIDENCE_ACQUIRED_NOT_QUALIFIED'
    publish(root, 'G5.acquisition.receipt.json', io.wire(receipt), written=written)
    return receipt

def main(argv=None):
    from scripts.rc6_g5a_actions import issuer, ParentAPI, outputs, signature
    issuer()
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('package-manifest','package-manifest-sha256','family-draft','family-draft-sha256'):
        parser.add_argument('--'+name, required=True)
    args = parser.parse_args(argv)
    try:
        require(ctypes.CDLL(None,use_errno=True).prctl(4,0,0,0,0)==0,'G5A_FAMILY_PARENT_DUMPABLE_ZERO_REQUIRED')
        token = os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
        require(type(token) is str and bool(token),'G5A_FAMILY_READONLY_PARENT_API_REQUIRED')
        reader = ParentAPI(bytearray(token, 'ascii'))
        try:
            seal_family(Path(args.package_manifest),manifest_sha256=args.package_manifest_sha256,
                draft_path=Path(args.family_draft),draft_sha256=args.family_draft_sha256,reader=reader)
        finally:
            reader.close()
        outputs(family_sealed='true',primary_root=Path(args.family_draft).parent)
        return 0
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(io.wire({'status':'RED','reason':signature(error),'G5_GREEN':False,'real_orders_sent':0}).decode())
        return 1

if __name__ == '__main__': raise SystemExit(main())
