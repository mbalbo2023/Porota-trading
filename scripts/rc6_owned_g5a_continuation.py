"""Same-operation owner continuity; serialized observations grant no authority.

The factory authenticates an original lease-monitor snapshot through the
private ROOT oracle before Seal. Later reads are restricted to the same
read-only control inode and the same live native custody. They preserve the
original decision; they cannot launch another operation or mint an approval.
"""
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import stat

from scripts import rc6_owned_gate_lease as leases
from scripts import rc6_material_pr_admission as admission

SCHEMA = 'porota.rc6.g5a-same-operation-pulse.v1'
PREFIX = 'g5a-pulse-'
MAX_PULSES = 362
_CONTEXTS = {}


def validate_decision_documents(approval, get):
    """Check an existing immutable decision, without issuing fresh acceptance."""
    identifier = approval.approval_url.rsplit('-', 1)[1]
    for issue, comment_id, expected in ((471, identifier, approval.approval_body_sha256),
            (473, approval.paired_comment_id, approval.paired_body_sha256)):
        row = get('/issues/comments/' + str(comment_id))
        require(type(row) is dict and row.get('id') == int(comment_id)
                and row.get('user', {}).get('login') == 'mbalbo2023'
                and row.get('issue_url') == 'https://api.github.com/repos/' + admission.REPO + '/issues/' + str(issue)
                and row.get('created_at') == row.get('updated_at')
                and hashlib.sha256(row.get('body', '').encode()).hexdigest() == expected,
                'G5A_CONTINUATION_OWNER_DECISION_EDITED_DELETED_OR_REBOUND')


def reject_revocations(approval, timelines):
    for issue in (471, 473):
        for row in timelines[issue]:
            fields = admission.fields(row.get('body', ''))
            if fields.get('SOURCE_SHA') == approval.source_sha and fields.get('CONTRACT_REVISION') == approval.revision:
                require(fields.get('CONTRACT_REVISION_REVOKED') not in ('true', 'REVOKED')
                        and fields.get('RC6_CONTRACT_REVISION_AUTHORIZATION') not in ('REVOKED', 'DENIED')
                        and fields.get('CONTRACT_DECISION_REVOKED') not in ('true', 'REVOKED'),
                        'G5A_CONTINUATION_CONTRACT_OWNER_REVOCATION')


def require(value, reason):
    if not value:
        raise ValueError(reason)


def _root_identity(path):
    path = Path(path)
    require(path.is_absolute() and str(path) == os.path.abspath(path)
            and not any(node.is_symlink() for node in (path, *path.parents)),
            'G5A_CONTINUATION_LITERAL_OWN_ROOT_REQUIRED')
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o700,
            'G5A_CONTINUATION_PRIVATE_MONITOR_ROOT_REQUIRED')
    return (info.st_dev, info.st_ino, info.st_uid, info.st_gid)


def _read(path, *, root_identity):
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
            and (info.st_dev, info.st_uid, info.st_gid) ==
                (root_identity[0], root_identity[2], root_identity[3])
            and stat.S_IMODE(info.st_mode) == 0o600
            and 0 < info.st_size <= leases.MAX_OBJECT_BYTES,
            'G5A_CONTINUATION_OWN_SINGLELINK_CONTROL_REQUIRED')
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NOATIME | os.O_CLOEXEC)
    try:
        before = tuple(getattr(info, field) for field in admission.STAT11)
        raw = os.read(descriptor, leases.MAX_OBJECT_BYTES + 1)
        require(len(raw) == info.st_size
                and before == tuple(getattr(os.fstat(descriptor), field) for field in admission.STAT11)
                == tuple(getattr(path.lstat(), field) for field in admission.STAT11),
                'G5A_CONTINUATION_CONTROL_CHANGED_DURING_READ')
    finally:
        os.close(descriptor)
    return raw


def replay_pulse(pulse, *, read_object, binding, nonce, now, expected_context=None, approval=None):
    """Documentary replay only. Neither this function nor its result authorizes."""
    require(type(pulse) is dict and pulse.get('schema') == SCHEMA
            and pulse.get('binding') == binding and pulse.get('namespace_nonce') == nonce
            and type(pulse.get('sequence')) is int and 1 <= pulse['sequence'] <= MAX_PULSES
            and pulse.get('G5_GREEN') is False and pulse.get('new_launch_authorized') is False,
            'G5A_CONTINUATION_PULSE_SOURCE_SCOPE_REBOUND')
    used, objects = 0, {}
    def load(identifier):
        nonlocal used
        require(type(identifier) is str and re.fullmatch('[0-9a-f]{64}', identifier),
                'G5A_CONTINUATION_OBJECT_IDENTIFIER_REQUIRED')
        if identifier not in objects:
            raw = read_object(identifier)
            require(type(raw) is bytes and 0 < len(raw) <= leases.MAX_OBJECT_BYTES
                    and hashlib.sha256(raw).hexdigest() == identifier,
                    'G5A_CONTINUATION_OBJECT_BYTES_REBOUND')
            used += len(raw)
            require(used <= leases.MAX_EVIDENCE_BYTES, 'G5A_CONTINUATION_CONTROL_BYTE_BOUND')
            objects[identifier] = admission.document(raw)
        return objects[identifier]
    def response(identifier):
        wrapped = load(identifier)
        if wrapped.get('kind') == 'json':
            return wrapped['value']
        require(wrapped.get('kind') == 'array' and type(wrapped.get('members')) is list,
                'G5A_CONTINUATION_RESPONSE_TYPE_REQUIRED')
        rows = []
        for item in wrapped['members']:
            wrapped_item = load(item)
            require(wrapped_item.get('kind') == 'json', 'G5A_CONTINUATION_COMMENT_TYPE_REQUIRED')
            rows.append(wrapped_item['value'])
        require(leases.digest(leases.wire(rows)) == wrapped.get('semantic_sha256'),
                'G5A_CONTINUATION_TIMELINE_REBOUND')
        return rows
    wrapped = load(pulse.get('snapshot'))
    require(wrapped.get('kind') == 'snapshot', 'G5A_CONTINUATION_SNAPSHOT_REQUIRED')
    snapshot = wrapped['value']
    context = snapshot.get('context')
    require(snapshot.get('schema') == leases.SNAPSHOT_SCHEMA and type(context) is dict
            and context.get('source_sha') == binding['candidate_sha']
            and context.get('source_tree') == binding['candidate_tree']
            and context.get('owner_session') == binding['owner_id']
            and context.get('gate') == 'Horizon' and context.get('run_attempt') == 1
            and (expected_context is None or context == expected_context)
            and snapshot.get('stage') in ('prelaunch', 'poll')
            and snapshot.get('http_total_budget_seconds') == leases.HTTP_BUDGET_SECONDS,
            'G5A_CONTINUATION_SAME_RUNNING_HORIZON_CONTEXT_REQUIRED')
    checked, began = (admission.stamp(snapshot.get(key)) for key in ('read_utc', 'started_utc'))
    require(0 <= (now - checked).total_seconds() <= leases.API_REFRESH_SECONDS + leases.HTTP_BUDGET_SECONDS
            and 0 <= (checked - began).total_seconds() <= leases.HTTP_BUDGET_SECONDS
            and type(snapshot.get('elapsed_seconds')) in (float, int)
            and 0 <= snapshot['elapsed_seconds'] <= leases.HTTP_BUDGET_SECONDS,
            'G5A_CONTINUATION_API_OBSERVATION_STALE')
    references = snapshot.get('api_response_objects')
    require(type(references) is dict and references, 'G5A_CONTINUATION_API_DOCUMENTS_REQUIRED')
    calls = {path: response(identifier) for path, identifier in references.items()}
    if approval is not None:
        validate_decision_documents(approval, lambda path: calls[path])
        reject_revocations(approval, leases._timelines(calls))
    scope = leases._scope(context, checked, lambda path: calls[path])
    require(leases.digest(leases.wire(scope)) == snapshot.get('scope_sha256')
            and all(now < admission.stamp(owner['lease_expires_utc'])
                    for owner in scope['fresh_ownership'].values()),
            'G5A_CONTINUATION_CURRENT_OWNER_OR_LEASE_REVOKED')
    proof = snapshot.get('lease_coverage')
    require(type(proof) is dict and set(proof) == {'471', '473'}
            and all(type(record) is dict and record.get('comment_ids')
                and admission.stamp(record.get('covered_until_utc')) > now for record in proof.values()),
            'G5A_CONTINUATION_CONTINUOUS_LEASE_COVERAGE_REQUIRED')
    return {'context': context, 'calls': calls, 'scope': scope,
            'read_utc': checked, 'sequence': pulse['sequence'], 'documentary_only': True}


class NativeG5AContinuation:
    __slots__ = ('pid', 'root', 'identity', 'binding', 'nonce', 'context', 'controller',
                 'preparation', 'custody', 'filter_binding', 'sequence', 'observed_utc')
    def __new__(cls, *args, **kwargs):
        raise ValueError('G5A_CONTINUATION_NATIVE_AUTHENTIC_FACTORY_REQUIRED')
    def __setattr__(self, name, value):
        raise AttributeError('G5A_CONTINUATION_AUTHORITY_IMMUTABLE')


def _latest(root, identity):
    names = sorted(path.name for path in root.iterdir() if path.name.startswith(PREFIX))
    require(0 < len(names) <= MAX_PULSES and all(re.fullmatch(r'g5a-pulse-[0-9]{4}\.json', name)
            for name in names), 'G5A_CONTINUATION_BOUNDED_MONITOR_PULSES_REQUIRED')
    return admission.document(_read(root / names[-1], root_identity=identity))


def bind_continuation(*, preparation, trusted_controller, reader, binding, monitor_root):
    """Fresh native ROOT factory, called before closing the authenticated oracle."""
    from scripts import rc6_g5_contract as g5
    from scripts.rc6_contract_revisions import validate_revision_approval
    request, _ = g5._trusted_api(trusted_controller, role='worker')
    registered = g5._PREPARATIONS.get(id(preparation))
    require(type(preparation) is g5.TrustedG5APreparation and registered is not None
            and registered[0] is preparation and preparation.pid == os.getpid()
            and preparation.reader is reader and reader.controller is trusted_controller
            and preparation.binding_wire == g5.components.canonical(binding),
            'G5A_CONTINUATION_CURRENT_TRUSTED_PREPARATION_REQUIRED')
    reader._validate()
    validate_revision_approval(preparation.approval, revision=g5.REVISION, scope=g5.SCOPE,
        source_sha=binding['candidate_sha'], source_tree=binding['candidate_tree'],
        contract_sha256=g5.CONTRACT_SHA256)
    root = Path(monitor_root)
    require(root == Path(request['namespace_receipt']['path']) / 'g5a-owner-monitor',
            'G5A_CONTINUATION_FOREIGN_MONITOR_ROOT_VETO')
    identity = _root_identity(root)
    require(identity[2:] == (request['owner_uid'], request['owner_gid']),
            'G5A_CONTINUATION_ACTUAL_ISSUER_OWN_CONTROL_REQUIRED')
    nonce = request['namespace_receipt']['namespace_nonce']
    pulse = _latest(root, identity)
    replay = replay_pulse(pulse, read_object=lambda identifier: _read(
        root / (leases.OBJECT_PREFIX + identifier + '.json'), root_identity=identity),
        binding=binding, nonce=nonce, now=datetime.now(timezone.utc))
    # Authenticate current API facts directly; file metadata is never provenance.
    scope = leases._scope(replay['context'], datetime.now(timezone.utc), reader.request)
    validate_decision_documents(preparation.approval, reader.request)
    require(scope['source_sha'] == binding['candidate_sha']
            and scope['source_tree'] == binding['candidate_tree']
            and scope['owner_session'] == binding['owner_id'],
            'G5A_CONTINUATION_AUTHENTIC_CURRENT_API_SCOPE_REQUIRED')
    result = object.__new__(NativeG5AContinuation)
    values = dict(pid=os.getpid(), root=str(root), identity=identity, binding=dict(binding), nonce=nonce,
        context=replay['context'], controller=trusted_controller, preparation=preparation,
        custody=None, filter_binding=None, sequence=replay['sequence'], observed_utc=replay['read_utc'])
    for name, value in values.items(): object.__setattr__(result, name, value)
    _CONTEXTS[id(result)] = (result, values)
    return result


def activate_continuation(context, *, custody, binding, filter_binding):
    from scripts import rc6_g5_contract as g5
    registered = _CONTEXTS.get(id(context))
    require(type(context) is NativeG5AContinuation and registered is not None
            and registered[0] is context and context.pid == os.getpid()
            and context.binding == binding and context.custody is None,
            'G5A_CONTINUATION_SAME_OPERATION_ACTIVATION_REQUIRED')
    require(type(custody) is g5.NativeCustodyContext, 'G5A_CONTINUATION_TYPED_NATIVE_CUSTODY_REQUIRED')
    custody.validate(binding=binding)
    g5.require_sealed_nonroot(source_sha=binding['candidate_sha'], source_tree=binding['candidate_tree'],
                             filter_binding=filter_binding)
    object.__setattr__(context, 'custody', custody)
    object.__setattr__(context, 'filter_binding', dict(filter_binding))
    registered[1].update(custody=custody, filter_binding=dict(filter_binding))
    validate_continuation(context, binding=binding)


def validate_continuation(context, *, binding):
    from scripts import rc6_g5_contract as g5
    registered = _CONTEXTS.get(id(context))
    require(type(context) is NativeG5AContinuation and registered is not None
            and registered[0] is context and context.pid == os.getpid()
            and all(getattr(context, name) == value for name, value in registered[1].items())
            and context.binding == binding and context.custody is not None,
            'G5A_CONTINUATION_CURRENT_LIVE_TYPED_AUTHORITY_REQUIRED')
    context.custody.validate(binding=binding)
    g5.require_sealed_nonroot(source_sha=binding['candidate_sha'], source_tree=binding['candidate_tree'],
                             filter_binding=context.filter_binding)
    root = Path(context.root)
    require(_root_identity(root) == context.identity and os.statvfs(root).f_flag & os.ST_RDONLY,
            'G5A_CONTINUATION_MONITOR_ROOT_NOT_READONLY_OR_REBOUND')
    pulse = _latest(root, context.identity)
    replay = replay_pulse(pulse, read_object=lambda identifier: _read(
        root / (leases.OBJECT_PREFIX + identifier + '.json'), root_identity=context.identity),
        binding=binding, nonce=context.nonce, now=datetime.now(timezone.utc), expected_context=context.context,
        approval=context.preparation.approval)
    require(replay['sequence'] >= context.sequence and replay['read_utc'] >= context.observed_utc,
            'G5A_CONTINUATION_OBSERVATION_ROLLBACK')
    object.__setattr__(context, 'sequence', replay['sequence'])
    object.__setattr__(context, 'observed_utc', replay['read_utc'])
    registered[1].update(sequence=replay['sequence'], observed_utc=replay['read_utc'])
    return {'same_operation_only': True, 'new_launch_authorized': False, 'G5_GREEN': False}
