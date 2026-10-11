"""Actions frontend for explicitly accepted G5A bounded evidence acquisition.

Preparation starts with the genuine Source/API factory and authenticates the
original G0--G4 and5/512→26/20 artifacts before installing PRODUCT311. The
prepared manifest is documentary transport only. Execute authenticates again
in the actual PRODUCT311 process; only original native FIN permits RAW capture.
This entrypoint grants no contractual acceptance, G5 GREEN, deploy or cleanup.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import ctypes
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.metadata
import math
import os
from pathlib import Path
import platform
import re
import resource
import runpy
import stat
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).absolute().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True
from scripts import rc6_architectural_gates as gates
from scripts import rc6_authenticated_fixture_lifecycle as owned
from scripts import rc6_capacity_calibration as calibration
from scripts import rc6_ephemeral_api_credential as credential
from scripts import rc6_g5a_carrier as native
from scripts import rc6_g5a_native_runner as runner
from scripts import rc6_material_carrier as original
from scripts import rc6_material_pr_admission as admission
from scripts import rc6_owned_gate_lease as leases
from scripts import rc6_owned_g5a_continuation as continuation
from scripts.porota_artifact_http import RemoveAuthorizationRedirect, remaining

MEMBER = 'scripts/rc6_g5a_actions.py'
PREPARED_SCHEMA = 'rc6.g5a-actions-documentary-preparation.v1'
PREPARED_FIELDS = frozenset({'schema', 'source_sha', 'source_tree', 'binding', 'owner_session',
    'launch_receipt_url', 'revision_approval_url', 'run_id', 'run_attempt', 'source_repo', 'source_root',
    'source_index', 'python311', 'interpreter_manifest', 'prerequisites_manifest', 'native_parent',
    'native_controls', 'authority_serialized', 'G5_GREEN', 'qualification_claimed', 'real_orders_sent',
    'preparation_only', 'original19_verified', 'full_git_verified', 'whole_source_verified',
    'installed_epochs', 'original_bootstrap_epochs_authenticated'})
SECRET_ENV = ('GH_TOKEN', 'GITHUB_TOKEN')
ERROR = re.compile(r'[A-Z][A-Z0-9_]{0,191}\Z')
_OWN_OUTPUTS = {}


def require(ok, signature):
    if not ok: raise ValueError(signature)


def signature(error):
    reason = str(error).partition(':')[0]
    return reason if ERROR.fullmatch(reason) else 'G5A_ACTIONS_NON_LITERAL_FAILURE'


def issuer():
    """No checkout, output allocation or producer precedes this guard."""
    require(os.getresuid() == (os.getuid(),) * 3 and os.getuid() > 0
        and os.getresgid() == (os.getgid(),) * 3 and os.getgid() > 0
        and sys.platform == 'linux' and len(os.listdir('/proc/self/task')) == 1,
        'G5A_ACTIONS_ACTUAL_SINGLE_THREAD_NONROOT_REQUIRED')
    require(os.environ.get('GITHUB_ACTIONS') == 'true'
        and os.environ.get('GITHUB_REPOSITORY') == admission.REPO
        and os.environ.get('GITHUB_REPOSITORY_ID') == str(admission.REPO_ID)
        and os.environ.get('GITHUB_EVENT_NAME') == 'workflow_dispatch'
        and os.environ.get('GITHUB_RUN_ATTEMPT') == '1',
        'G5A_ACTIONS_ACTUAL_FIRST_CANONICAL_MANUAL_ACTIONS_REQUIRED')


class ParentAPI:
    """Bounded real READ_ONLY HTTP; bearer stays in the single NONROOT issuer."""
    def __init__(self, token, *, opener=None):
        require(type(token) is bytearray and 0 < len(token) <= credential.MAXIMUM_BYTES
            and all(32 < value < 127 for value in token), 'G5A_ACTIONS_PARENT_BOUNDED_API_BYTES_REQUIRED')
        self._token = token
        self._closed = False
        self._opener = opener or urllib.request.build_opener(RemoveAuthorizationRedirect())

    def _request(self, path):
        require(os.getuid() == os.geteuid() > 0 and os.getgid() == os.getegid() > 0
            and len(os.listdir('/proc/self/task')) == 1, 'G5A_ACTIONS_PARENT_API_NONROOT_REQUIRED')
        require(type(path) is str and (path == '' or path.startswith('/'))
            and not any(ord(value) < 32 or ord(value) == 127 for value in path)
            and not self._closed, 'G5A_ACTIONS_CLOSED_READONLY_API_PATH_REQUIRED')
        parsed = urllib.parse.urlsplit(path)
        decoded = urllib.parse.unquote(parsed.path)
        require(not parsed.scheme and not parsed.netloc and not parsed.fragment
            and not any(part in ('.', '..') for part in decoded.split('/'))
            and not any(ord(value) < 32 or ord(value) == 127 for value in decoded),
            'G5A_ACTIONS_CLOSED_READONLY_API_PATH_REQUIRED')
        return urllib.request.Request('https://api.github.com/repos/' + admission.REPO + path,
            headers={'Authorization': 'Bearer ' + self._token.decode('ascii'),
                'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'})

    def request(self, path, deadline=None):
        deadline = time.monotonic() + 30 if deadline is None else deadline
        try:
            with self._opener.open(self._request(path), timeout=remaining(deadline)) as response:
                raw = response.read(admission.CAP + 1)
            remaining(deadline)
            require(0 < len(raw) <= admission.CAP, 'G5A_ACTIONS_API_DOCUMENT_BOUND')
            return admission.document(raw)
        except urllib.error.HTTPError as error:
            raise ValueError('G5A_ACTIONS_API_HTTP_' + str(error.code)) from None
        except urllib.error.URLError:
            raise ValueError('G5A_ACTIONS_API_TRANSPORT_UNAVAILABLE') from None

    def download(self, artifact_id, output, *, expected_size, expected_digest, deadline):
        require(type(artifact_id) is int and artifact_id > 0 and type(expected_size) is int
            and 0 < expected_size <= gates.MAX_ARCHIVE_BYTES and type(expected_digest) is str
            and re.fullmatch(r'sha256:[0-9a-f]{64}', expected_digest),
            'G5A_ACTIONS_PREREQUISITE_ARCHIVE_CLOSED_BOUND_REQUIRED')
        output = runner.canonical_path(str(output))
        require(not os.path.lexists(output), 'G5A_ACTIONS_NEW_OWN_ARTIFACT_REQUIRED')
        digest, total = hashlib.sha256(), 0
        # Failed downloads remain in the own attempt: no path deletion fallback.
        try:
            with self._opener.open(self._request('/actions/artifacts/' + str(artifact_id) + '/zip'),
                    timeout=remaining(deadline)) as response:
                descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
                with os.fdopen(descriptor, 'wb') as destination:
                    while True:
                        remaining(deadline)
                        block = response.read(min(1024**2, expected_size + 1 - total))
                        if not block: break
                        total += len(block)
                        require(total <= expected_size, 'G5A_ACTIONS_ARTIFACT_BYTES_EXCEEDED')
                        digest.update(block); destination.write(block)
                    destination.flush(); os.fsync(destination.fileno())
            remaining(deadline)
        except urllib.error.HTTPError as error:
            raise ValueError('G5A_ACTIONS_ARTIFACT_HTTP_' + str(error.code)) from None
        except urllib.error.URLError:
            raise ValueError('G5A_ACTIONS_ARTIFACT_TRANSPORT_UNAVAILABLE') from None
        require(total == expected_size and 'sha256:' + digest.hexdigest() == expected_digest,
            'G5A_ACTIONS_ACTUAL_ARTIFACT_BYTES_REBOUND')
        return {'bytes': total, 'digest': expected_digest, 'authorization_forwarded_to_redirect': False}

    def close(self):
        credential.wipe(self._token)
        self._closed = True


@contextmanager
def parent_api(reader):
    """Use real in-memory HTTP for the original factory; no JSON authority."""
    previous = admission.api
    admission.api = reader.request
    try: yield
    finally: admission.api = previous


def fresh_context(args):
    issuer()
    return admission.admit_g5a_preparation(source_sha=args.source_sha, source_tree=args.source_tree,
        launch_receipt_url=args.launch_receipt_url, owner_session=args.owner_session,
        revision_approval_url=args.g5a_revision_approval_url)


def validate_context(context):
    require(type(context) is admission.G5ASetupLaunchContext,
        'G5A_ACTIONS_CURRENT_TYPED_API_SOURCE_CONTEXT_REQUIRED')
    return admission.validate_g5a_setup_launch(context, source_sha=context.source_sha,
        source_tree=context.source_tree, owner_session=context.owner_session,
        launch_receipt_url=context.launch_receipt_url)


class DecisionAPI:
    """Same-operation immutable acceptance plus current API-only ownership.

    Creation requires the real fresh factory. Later observations keep that
    original decision and the unchanged process binding; they do not refresh
    acceptance, authorize a new ROOT operation or read live producer files.
    The caller's original monitor supplies its one five-second HTTP deadline.
    """
    def __init__(self, context, reader, *, operation_deadline):
        row = validate_context(context)
        require(type(reader) is ParentAPI and not reader._closed,
            'G5A_ACTIONS_ACTUAL_PARENT_API_CONTINUATION_REQUIRED')
        require(type(operation_deadline) in (int, float) and math.isfinite(operation_deadline)
            and 0 < operation_deadline - time.monotonic() <= runner.HORIZON_SECONDS,
            'G5A_ACTIONS_ORIGINAL_HORIZON21600_OPERATION_DEADLINE_REQUIRED')
        self.context, self.reader, self.operation_deadline = context, reader, operation_deadline
        self._admission_wire = context.admission_wire
        self._launch_body_sha256 = row['launch_body_sha256']
        self._decision_deadline, self._fallback_deadline = None, None
        self._decision_documents = {}

    def accepted_row(self):
        """Check only existing immutable memory; this issues no acceptance."""
        context = self.context
        registered = admission._G5A_SETUP_CONTEXTS.get(id(context))
        require(type(context) is admission.G5ASetupLaunchContext
            and registered is not None and registered[0] is context
            and context.pid == os.getpid()
            and all(getattr(context, name) == value for name, value in registered[1].items())
            and context.admission_wire == self._admission_wire,
            'G5A_ACTIONS_ORIGINAL_IN_PROCESS_ACCEPTED_CONTEXT_REBOUND')
        return context.admission

    def _deadline(self, deadline):
        self.accepted_row()
        now = time.monotonic()
        require(now < self.operation_deadline,
            'G5A_ACTIONS_ORIGINAL_HORIZON21600_DEADLINE_EXHAUSTED')
        if deadline is None:
            if self._fallback_deadline is None or self._fallback_deadline <= now:
                self._fallback_deadline = min(self.operation_deadline, now + leases.HTTP_BUDGET_SECONDS)
            deadline = self._fallback_deadline
        require(type(deadline) in (int, float) and math.isfinite(deadline)
            and 0 < deadline - now <= leases.HTTP_BUDGET_SECONDS,
            'G5A_ACTIONS_ORIGINAL_FIVE_SECOND_OWNER_HTTP_DEADLINE_REQUIRED')
        return min(deadline, self.operation_deadline)

    def request(self, path, deadline=None):
        effective = self._deadline(deadline)
        if effective != self._decision_deadline:
            documents = {}
            def decision_get(pointer):
                value = self.reader.request(pointer, deadline=effective)
                documents[pointer] = value
                return value
            continuation.validate_decision_documents(self.context.revision, decision_get)
            # Current bytes are retained in issuer memory only. JSON cannot
            # recreate the existing context or extend this operation's deadline.
            self._decision_documents, self._decision_deadline = documents, effective
        value = self.reader.request(path, deadline=effective)
        for issue in (471, 473):
            if path.startswith('/issues/' + str(issue) + '/comments?'):
                require(type(value) is list, 'G5A_ACTIONS_OWNER_TIMELINE_ARRAY_REQUIRED')
                continuation.reject_revocations(self.context.revision,
                    {471: value if issue == 471 else [], 473: value if issue == 473 else []})
        return value

    def owner_check(self, label):
        require(type(label) is str and re.fullmatch('[a-z][a-z0-9-]{0,63}', label),
            'G5A_ACTIONS_LITERAL_SAME_OPERATION_OWNER_PHASE_REQUIRED')
        deadline = self._deadline(time.monotonic() + leases.HTTP_BUDGET_SECONDS)
        calls = {}
        def record_get(path):
            if path not in calls: calls[path] = self.request(path, deadline=deadline)
            return calls[path]
        arguments = {key: getattr(self.context, key) for key in
            ('source_sha', 'source_tree', 'owner_session', 'launch_receipt_url')}
        with leases._http_budget(deadline):
            admission.owned_gate_control_scope(**arguments, gate='Horizon',
                now=datetime.now(timezone.utc) + timedelta(seconds=deadline - time.monotonic()), get=record_get)
            observed = admission.owned_gate_control_scope(**arguments, gate='Horizon',
                now=datetime.now(timezone.utc), get=lambda path: calls[path])
        require(observed.get('launch_body_sha256') == self._launch_body_sha256
            and observed.get('source_sha') == self.context.source_sha
            and observed.get('source_tree') == self.context.source_tree
            and observed.get('owner_session') == self.context.owner_session
            and observed.get('run_attempt') == 1,
            'G5A_ACTIONS_CURRENT_SAME_OPERATION_EXCLUSIVE_OWNER_REBOUND')
        return observed

    def monitor_factory(self, namespace, label):
        self.accepted_row()
        require(label in ('g5-reader-product157-preflight', 'g5-reader-collection',
                          'g5-reader-execution'),
            'G5A_ACTIONS_ORIGINAL_READER_OWNER_MONITOR_SCOPE_REQUIRED')
        receipt = owned.namespace_receipt(namespace)
        require(receipt['binding'] == self.context.binding,
            'G5A_ACTIONS_ORIGINAL_READER_NAMESPACE_BINDING_REBOUND')
        controls = _new_directory(namespace.path / 'g5b-owner-monitor')
        # The existing regular monitor verifies fresh ownership and FIN. Its
        # GET callback preserves the accepted decision without reminting it.
        return leases.GateLeaseMonitor(controls, source_sha=self.context.source_sha,
            source_tree=self.context.source_tree, owner_session=self.context.owner_session,
            launch_receipt_url=self.context.launch_receipt_url, gate='Horizon', get=self.request)


def exact_repo(args):
    repo = runner.canonical_path(str(args.repo_root))
    require(repo == ROOT and Path(__file__).absolute() == repo / MEMBER,
        'G5A_ACTIONS_EXECUTED_ORIGINAL_GIT_ROOT_REQUIRED')
    original.module_pin(repo, MEMBER, args.source_sha)
    require(original.git(repo, 'rev-parse', 'HEAD', 'HEAD^{tree}').splitlines()
        == [args.source_sha.encode(), args.source_tree.encode()]
        and not original.git(repo, 'status', '--porcelain=v1', '--untracked-files=all')
        and not os.path.lexists(repo / '.git/objects/info/alternates'),
        'G5A_ACTIONS_EXACT_CLEAN_ORIGINAL_GIT_SOURCE_REQUIRED')
    return repo


def _private_parent(path):
    path = runner.canonical_path(str(path)); info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and (info.st_uid, info.st_gid) == (os.getuid(), os.getgid())
        and not stat.S_IMODE(info.st_mode) & 0o022 and not path.is_relative_to(ROOT),
        'G5A_ACTIONS_EXTERNAL_OWN_PARENT_REQUIRED')
    return path


def _new_directory(path):
    path = runner.canonical_path(str(path))
    require(not os.path.lexists(path), 'G5A_ACTIONS_EXCLUSIVE_OWN_DIRECTORY_REQUIRED')
    path.mkdir(mode=0o700)
    return path


def verify_preparation_artifacts(context, controls, reader):
    """Original native ZIP replay; this function creates no fixture or image."""
    row = validate_context(context)
    prerequisites = gates.verify_manifest(row['prerequisites_manifest'], source_sha=context.source_sha,
        source_tree=context.source_tree, target_gate='G5', evidence_root=controls / 'g0-g4-raw',
        get=reader.request, download=reader.download)
    require(prerequisites.get('status') == 'GREEN' and prerequisites.get('target_gate') == 'G5',
        'G5A_ACTIONS_ORIGINAL_G0_G4_NOT_AUTHENTICATED')
    bootstrap = gates.verify_capacity_bootstrap_artifact(row['g5a_bootstrap_reference'],
        capability_reference=row['g5a_capability_reference'], source_sha=context.source_sha,
        source_tree=context.source_tree, read_contract_sha256=gates.productive_contract(ROOT),
        evidence_root=controls / 'accepted-capability-bootstrap', get=reader.request, download=reader.download,
        authenticated_branch=row['candidate_scope']['branch'], source_repository=ROOT)
    calibration.publish(controls / 'original-prerequisites.json', row['prerequisites_manifest'])
    calibration.publish(controls / 'authenticated-prerequisite-controls.json', {
        'schema': 'rc6.g5a-actions-original-prerequisite-controls.v1', 'source_sha': context.source_sha,
        'source_tree': context.source_tree, 'verified_G0_G4': prerequisites,
        'verified_capability_before_bootstrap': {key: value for key, value in bootstrap.items()
            if key not in ('receipt_raw', 'verified_capability_prerequisite')},
        'authority_serialized': False, 'G5_GREEN': False, 'qualification_claimed': False, 'real_orders_sent': 0})
    return row


class PreparationRunner(original.OwnedRunner):
    """Every preparation command keeps the original manager/own FIN/live lease."""
    def __init__(self, manager, controls, namespace, context, *, aggregate_root,
                 aggregate_probe, aggregate_project_id):
        super().__init__(manager, controls)
        self.namespace, self.context = namespace, context
        self.aggregate_root = aggregate_root
        self.aggregate_probe = aggregate_probe
        self.aggregate_project_id = aggregate_project_id

    def __call__(self, argv, *, cwd, label, limit=300, env=None, namespace=None):
        require(namespace is None, 'G5A_ACTIONS_PREPARATION_FOREIGN_NAMESPACE_FORBIDDEN')
        self.manager['pre_capture_kernel_state']()
        validate_context(self.context)
        command_namespace = owned.create_namespace(self.namespace.path, self.context.binding)
        self.control = _new_directory(command_namespace.path / 'g5a-owner-monitor')
        self.lease_context = {key: getattr(self.context, key) for key in
            ('source_sha', 'source_tree', 'owner_session', 'launch_receipt_url')}
        self.lease_context.update(gate='Horizon', g5a_namespace_receipt=owned.namespace_receipt(command_namespace),
            g5a_revision=self.context.revision)
        from scripts import rc6_g5a_aggregate_issuer as aggregate
        env = aggregate.stage_environment(self.aggregate_root,
            preparation_environment(argv, env), project_id=self.aggregate_project_id,
            stage='preparation')
        command = aggregate.guarded_stage_command(stage='preparation', root=self.aggregate_root,
            outside_control=self.aggregate_probe, project_id=self.aggregate_project_id,
            cwd=cwd, argv=argv, python=sys.executable)
        aggregate.require_stage_topology(self.aggregate_root, stage='preparation',
            project_id=self.aggregate_project_id,
            write_roots={'controls': self.control, 'namespace': command_namespace.path,
                'manager_logs': self.raw, 'cwd': cwd})
        return super().__call__(command, cwd=cwd, label=label, limit=limit,
            env=env, namespace=command_namespace)


def preparation_environment(argv, env):
    """Public Git transport needs no control bearer in a preparation child."""
    env = dict(env or {})
    require(not any(key in env for key in SECRET_ENV), 'G5A_ACTIONS_PREPARATION_CHILD_BEARER_FORBIDDEN')
    if 'RC6_READONLY_GIT_TOKEN' in env:
        require(type(argv) is list and argv and argv[0] == 'git',
            'G5A_ACTIONS_READONLY_GIT_CREDENTIAL_OUTSIDE_GIT_FORBIDDEN')
        env.pop('RC6_READONLY_GIT_TOKEN')
        env.update(GIT_ASKPASS='/bin/false', SSH_ASKPASS='/bin/false', GIT_CONFIG_GLOBAL='/dev/null')
    return env


def product311_commands(repo, private, base, owner_uid):
    """Closed original installer; only311 executes G5A, with both bootstrap epochs proved."""
    python = str(private / 'venv/bin/python')
    return [
        ([str(base), '-I', '-B', str(repo / 'scripts/rc6_material_environment.py'), '--root', str(private / 'venv'),
            '--owner-uid', str(owner_uid), '--python-version', original.VERSIONS['311'],
            '--receipt', str(private / 'creation.json')], 'venv311', 300),
        ([python, '-I', '-B', '-m', 'pip', 'install', '--disable-pip-version-check', '--no-cache-dir',
            '--require-hashes', '--only-binary=:all:', '-r', str(repo / 'requirements.build.lock.txt')], 'locked311-0', 1800),
        ([python, '-I', '-B', '-m', 'pip', 'install', '--disable-pip-version-check', '--no-cache-dir',
            '--require-hashes', '--only-binary=:all:', '--no-binary=msgpack,ppi-client,signalrcoreppi,ta',
            '--no-build-isolation', '-r', str(repo / 'requirements.lock.txt')], 'locked311-1', 1800),
        ([python, '-I', '-B', '-m', 'pip', 'check'], 'pipcheck311', 300)]


def installed311(args, run, root):
    base = calibration.verify_python(args.python311, '311')
    require(sys.executable == base and platform.python_version() == original.VERSIONS['311']
        and sys.prefix == sys.base_prefix, 'G5A_ACTIONS_REVIEWED_BASE311_PROCESS_REQUIRED')
    original.preparation_capacity(args, run, root, 'before-g5a-product311')
    private = _new_directory(root / 'product311')
    temporary = _new_directory(private / 'bootstrap-temp')
    env = {'TMPDIR': str(temporary), 'TEMP': str(temporary), 'TMP': str(temporary)}
    for command, label, limit in product311_commands(args.repo_root, private, base, os.getuid()):
        original.preparation_capacity(args, run, root, 'before-g5a-' + label,
            additional_storage=(('bootstrap_temporary', temporary),))
        result = run(command, cwd=root, label=label, limit=limit, env=env)
        require(result['returncode'] == 0, 'G5A_ACTIONS_ORIGINAL_PRODUCT311_INSTALL_RED')
    return str(private / 'venv/bin/python')


def record_interpreter(*, source_repo, source_root, source_sha, source_tree, binding, output):
    """Measure the current157 interpreter after managed FIN; never issue authority."""
    require(os.getuid() == os.geteuid() > 0 and os.getgid() == os.getegid() > 0
        and platform.python_version() == '3.11.16' and sys.prefix != sys.base_prefix
        and platform.system() == 'Linux' and platform.machine() == 'x86_64',
        'G5A_ACTIONS_ACTUAL_PRODUCT311_MANIFEST_PROCESS_REQUIRED')
    repo, source, binary, output = map(lambda value: runner.canonical_path(str(value)),
        (source_repo, source_root, sys.executable, output))
    require(repo == ROOT and binary.is_relative_to(Path(sys.prefix)) and not binary.is_relative_to(repo)
        and not binary.is_relative_to(source) and not output.is_relative_to(repo) and not output.is_relative_to(source)
        and not os.path.lexists(output), 'G5A_ACTIONS_DOCUMENTARY_INTERPRETER_EXTERNAL_INPUTS_REQUIRED')
    require(type(binding) is dict and binding.get('candidate_sha') == source_sha
        and binding.get('candidate_tree') == source_tree and binding.get('producer') == 'Horizon-private-original-cas',
        'G5A_ACTIONS_DOCUMENTARY_INTERPRETER_SOURCE_BINDING_REQUIRED')
    packages = sorted([[row.metadata['Name'], row.version] for row in importlib.metadata.distributions()])
    policy = calibration.decode(runner.read_input(source / 'ops/policy/rc6-supply-chain-v1.json'))
    calibration.validate_product_closure(packages, policy)
    info = binary.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o755
        and (info.st_uid, info.st_gid) == (os.getuid(), os.getgid()),
        'G5A_ACTIONS_ACTUAL_OWN_PRODUCT311_BINARY_REQUIRED')
    documentary = {'schema': 'rc6.g5a-preexisting-product157-interpreter.v1', 'source_sha': source_sha,
        'source_tree': source_tree, 'binding': binding, 'python_epoch': '311', 'python_version': platform.python_version(),
        'executable': sys.executable, 'prefix': sys.prefix, 'base_prefix': sys.base_prefix,
        'installed_distribution_count': len(packages), 'installed_packages_sha256': calibration.digest(calibration.wire(packages)),
        'identity': calibration.identity(info), 'executable_sha256': calibration.digest(runner.read_input(binary, maximum=128 * 1024**2)),
        'source_lock_sha256': {name: calibration.digest(runner.read_input(source / name)) for name in runner.SOURCE_LOCKS},
        'authority_serialized': False, 'G5_GREEN': False, 'qualification_claimed': False, 'real_orders_sent': 0}
    calibration.publish(output, documentary)
    return documentary


def prepare(args, context, reader):
    row = validate_context(context)
    from scripts import rc6_g5a_writer_inventory as writers
    # This is deliberately before even a Git identity subprocess. The current
    # workflow's checkout and parent Git callers make it a native NO-GO until
    # a fresh ROOT-owned project encloses Source and every process writer.
    writers.require_closed(writers.audit(ROOT, project_root=args.work_parent))
    args.repo_root = exact_repo(args)
    base = calibration.verify_python(args.python311, '311')
    require(sys.executable == base and platform.python_version() == '3.11.16' and sys.prefix == sys.base_prefix,
        'G5A_ACTIONS_PREPARE_REVIEWED_BASE311_REQUIRED')
    from scripts.rc6_g5a_host_capacity import require_host_stage_admission
    require_host_stage_admission(stage='preparation')
    parent = _private_parent(args.work_parent or Path.home())
    require(len(os.fsencode(parent)) <= 24, 'G5A_ACTIONS_ORIGINAL_SHORT_OWNED_PARENT_REQUIRED')
    root = Path(tempfile.mkdtemp(prefix='r6g-', dir=parent))
    require(len(os.fsencode(root)) <= 37, 'G5A_ACTIONS_SHORT_ORIGINAL_PREPARATION_NAMESPACE_REQUIRED')
    controls = _new_directory(root / 'controls-only')
    outputs(control_root=controls, safe_payload_upload='false')
    # Native ZIP validation precedes fixture namespaces, installer, fetch/export and images.
    row = verify_preparation_artifacts(context, controls, reader)
    from scripts import rc6_g5a_aggregate_issuer as aggregate
    quota_attributes = calibration.project_attributes(parent)
    quota_project_id = quota_attributes['project_id']
    aggregate.require_stage_topology(parent, stage='preparation',
        project_id=quota_project_id,
        write_roots={'controls': controls, 'preparation': root})
    probe_root = parent.parent / 'g5a-probe'
    args.gate = 'Horizon'; args.capacity_peaks = row['capacity_peaks']
    args.prerequisites_manifest = row['prerequisites_manifest']; args.require_pr_admission = True
    probe = type('CapacityControl', (), {'control': controls})()
    original.preparation_capacity(args, probe, root, 'before-original-g5a-preparation-namespace')
    namespace = owned.create_namespace(root, context.binding)
    manager_pin = original.module_pin(ROOT, 'scripts/rc6_controlled_native_child_manager.py', context.source_sha)
    require(manager_pin['sha256'] == owned.DRIVER_SHA256, 'G5A_ACTIONS_ORIGINAL_NATIVE_MANAGER_REBOUND')
    manager = runpy.run_path(str(ROOT / manager_pin['path'])); manager['pre_capture_kernel_state']()
    run = PreparationRunner(manager, controls, namespace, context,
        aggregate_root=parent, aggregate_probe=probe_root,
        aggregate_project_id=quota_project_id)
    original.complete_full_git(args, run, namespace.path)
    python = installed311(args, run, namespace.path)
    original.preparation_capacity(args, run, namespace.path, 'before-g5a-original19')
    original.fetch_originals(args, run, namespace.path)
    original.preparation_capacity(args, run, namespace.path, 'before-g5a-fullGit-fsck')
    result = run(['git', '--no-replace-objects', '-C', str(ROOT), 'fsck', '--full', '--strict'],
        cwd=namespace.path, label='fullGit-fsck', limit=600)
    require(result['returncode'] == 0, 'G5A_ACTIONS_ORIGINAL_FULL_GIT_FSCK_RED')
    original.module_pin(ROOT, original.EXPORT_MEMBER, context.source_sha)
    original.preparation_capacity(args, run, namespace.path, 'before-g5a-wholeSource-export')
    result = run([python, '-I', '-B', str(ROOT / original.EXPORT_MEMBER), '--repo', str(ROOT),
        '--sha', context.source_sha, '--source', str(namespace.path / 'source'), '--raw', str(namespace.path / 'pin')],
        cwd=namespace.path, label='wholeGit-export', limit=300)
    require(result['returncode'] == 0, 'G5A_ACTIONS_ORIGINAL_WHOLE_SOURCE_EXPORT_RED')
    interpreter_manifest = root / 'product311-interpreter.json'
    invocation = "import sys;sys.path.insert(0,sys.argv[1]);from scripts import rc6_g5a_actions as a;" \
        "a.record_interpreter(source_repo=sys.argv[1],source_root=sys.argv[2],source_sha=sys.argv[3]," \
        "source_tree=sys.argv[4],binding=a.calibration.decode(sys.argv[5].encode()),output=sys.argv[6])"
    original.preparation_capacity(args, run, namespace.path, 'before-g5a-actual157-metadata')
    result = run([python, '-I', '-B', '-c', invocation, str(ROOT), str(namespace.path / 'source'), context.source_sha,
        context.source_tree, calibration.wire(context.binding).decode(), str(interpreter_manifest)],
        cwd=namespace.path, label='actual157-metadata', limit=60)
    require(result['returncode'] == 0, 'G5A_ACTIONS_ACTUAL_PRODUCT157_CLOSURE_RED')
    from scripts import rc6_g5a_source_context as export
    exported = export.observe_export(source_repo=ROOT, source_root=namespace.path / 'source',
        index=namespace.path / 'pin/source.index.json', source_sha=context.source_sha,
        source_tree=context.source_tree, binding=context.binding)
    export.validate(exported, source_repo=ROOT, source_root=namespace.path / 'source',
        index=namespace.path / 'pin/source.index.json', source_sha=context.source_sha,
        source_tree=context.source_tree, binding=context.binding)
    exact_repo(args)
    document = {'schema': PREPARED_SCHEMA, 'source_sha': context.source_sha, 'source_tree': context.source_tree,
        'binding': context.binding, 'owner_session': context.owner_session, 'launch_receipt_url': context.launch_receipt_url,
        'revision_approval_url': args.g5a_revision_approval_url, 'run_id': int(os.environ['GITHUB_RUN_ID']), 'run_attempt': 1,
        'source_repo': str(ROOT), 'source_root': str(namespace.path / 'source'), 'source_index': str(namespace.path / 'pin/source.index.json'),
        'python311': python, 'interpreter_manifest': str(interpreter_manifest),
        'prerequisites_manifest': str(controls / 'original-prerequisites.json'),
        'native_parent': str(_new_directory(root / 'native-parent')), 'native_controls': str(_new_directory(root / 'native-controls')),
        'authority_serialized': False, 'G5_GREEN': False, 'qualification_claimed': False, 'real_orders_sent': 0,
        'preparation_only': True, 'original19_verified': True, 'full_git_verified': True, 'whole_source_verified': True,
        'installed_epochs': ['311'], 'original_bootstrap_epochs_authenticated': ['311', '312']}
    require(set(document) == PREPARED_FIELDS, 'G5A_ACTIONS_PREPARED_DOCUMENT_CLOSED_FIELDS_REQUIRED')
    manifest = root / 'prepared.json'; calibration.publish(manifest, document)
    outputs(g5a_prepared_manifest=manifest, g5a_python311=python, prepared_root=root, product_python=python,
        control_root=controls, safe_payload_upload='false')
    return document


def prepared_document(path, context):
    row = calibration.decode(runner.read_input(path))
    require(type(row) is dict and set(row) == PREPARED_FIELDS and row.get('schema') == PREPARED_SCHEMA
        and (row['source_sha'], row['source_tree'], row['owner_session'], row['launch_receipt_url'])
            == (context.source_sha, context.source_tree, context.owner_session, context.launch_receipt_url)
        and row['revision_approval_url'] == context.revision.approval_url and row['binding'] == context.binding
        and type(row['run_id']) is int and row['run_id'] == int(os.environ['GITHUB_RUN_ID'])
        and type(row['run_attempt']) is int and row['run_attempt'] == 1
        and row['installed_epochs'] == ['311'] and row['original_bootstrap_epochs_authenticated'] == ['311', '312']
        and row['source_repo'] == str(ROOT) and row['python311'] == sys.executable
        and type(row['real_orders_sent']) is int and row['real_orders_sent'] == 0
        and all(row[key] is False for key in ('authority_serialized', 'G5_GREEN', 'qualification_claimed'))
        and all(row[key] is True for key in ('preparation_only', 'original19_verified', 'full_git_verified', 'whole_source_verified')),
        'G5A_ACTIONS_PREPARED_DOCUMENT_CANNOT_AUTHORIZE_OR_REBIND')
    return row


def execute(args, context, reader):
    operation_deadline = time.monotonic() + runner.HORIZON_SECONDS
    validate_context(context)
    from scripts import rc6_g5a_writer_inventory as writers
    writers.require_closed(writers.audit(ROOT, project_root=None))
    exact_repo(args)
    require(platform.python_version() == '3.11.16' and sys.prefix != sys.base_prefix,
        'G5A_ACTIONS_EXECUTE_ACTUAL_PREEXISTING_PRODUCT311_REQUIRED')
    manifest = args.prepared_manifest if args.prepared_manifest is not None else args.prepared_root / 'prepared.json'
    prepared = prepared_document(manifest, context)
    from scripts.rc6_g5a_host_capacity import require_host_stage_admission
    require_host_stage_admission(stage='preparation')
    controls = Path(prepared['native_controls'])
    outputs(control_root=controls, safe_payload_upload='false')
    decision = DecisionAPI(context, reader, operation_deadline=operation_deadline)
    authority = native.authenticate_setup_launch(context, source_root=prepared['source_root'], source_repo=ROOT,
        source_index=prepared['source_index'], python311=prepared['python311'], interpreter_manifest=prepared['interpreter_manifest'],
        prerequisites_manifest=prepared['prerequisites_manifest'], parent=prepared['native_parent'], controls=controls,
        filter_plan_sha256=calibration.digest(runner.read_input(ROOT / 'ops/policy/rc6-native-prerequisites-v1.json')),
        get=decision.request, download=reader.download)
    transfer = bytearray(reader._token)
    for key in SECRET_ENV: os.environ.pop(key, None)
    try:
        result = native.launch(authority, token=transfer, operation_deadline=operation_deadline)
    finally:
        credential.wipe(transfer)
    native.validate_native_result(result)
    from scripts import rc6_g5a_raw_capture as raw_capture
    retired = raw_capture.retire_own_backing(result)
    destination = controls / 'source-original-raw'
    captured = raw_capture.capture(retired, destination=destination)
    documentary = captured.documentary
    require(documentary.get('G5_GREEN') is False and documentary.get('qualification_claimed') is False
        and type(documentary.get('actual_original_cuts')) is int and 0 <= documentary['actual_original_cuts'] <= 1202,
        'G5A_ACTIONS_CAPTURE_CANNOT_GRANT_G5_GREEN')
    red_reason = acquisition_error(documentary, result)
    terminal = {'schema': 'rc6.g5a-actions-evidence-acquisition.v1',
        'status': 'RED_EVIDENCE_CAPTURED' if red_reason else 'EVIDENCE_ACQUIRED_NOT_QUALIFIED',
        'source_sha': context.source_sha, 'source_tree': context.source_tree,
        'actual_original_cuts': documentary['actual_original_cuts'], 'original_red_reason': red_reason,
        'capture_manifest_sha256': calibration.digest(runner.read_input(destination / 'manifest.json')),
        'actual_outer_ROOT_worker_guardian_FIN_verified': True, 'G5_GREEN': False, 'qualification_claimed': False,
        'G0_G8_qualification_claimed': False, 'cleanup_authority_granted': False, 'real_orders_sent': 0,
        'DEPLOY_OWNER': 'NOT_ACQUIRED', 'runtime_validated': False}
    calibration.publish(controls / 'g5a-actions-result.json', terminal)
    outputs(control_root=controls, safe_payload_upload='true', payload_root=destination,
        g5a_capture_manifest=destination / 'manifest.json')
    if red_reason: raise ValueError(red_reason)
    from scripts import rc6_g5b_native_readers as native_readers
    from scripts import rc6_g5a_host_capacity as host_capacity
    from scripts import rc6_g5a_parts as parts
    from scripts import rc6_g5a_family as family
    def capacity_preflight(path, phase):
        host_capacity.require_host_stage_admission(stage='reader-'+phase)
        raise ValueError('G5A_HOST_STAGE_AGGREGATE_ENFORCEMENT_NOT_VERIFIED')
    from scripts import rc6_g5a_aggregate_issuer as aggregate
    aggregate_root = Path(prepared['source_root']).parents[2]
    aggregate_id = calibration.project_attributes(aggregate_root)['project_id']
    aggregate.require_stage_topology(aggregate_root, stage='reader-collection',
        project_id=aggregate_id,
        write_roots={'controls': controls, 'readers': aggregate_root/'readers'})
    review = native_readers.execute_post_fin_review(captured, source_root=ROOT, python311=prepared['python311'],
        control_root=controls/'native-readers', deadline=operation_deadline,
        owner_check=decision.owner_check, capacity_preflight=capacity_preflight,
        monitor_factory=decision.monitor_factory, aggregate_root=aggregate_root,
        aggregate_probe=aggregate_root.parent/'g5a-probe', aggregate_project_id=aggregate_id)
    packed = parts.package(captured)
    accepted = decision.accepted_row()
    assembled = family.assemble_draft(captured, packed, review, result, root=controls, approval=context.revision,
        capability_reference=accepted['g5a_capability_reference'], reader=reader,
        branch=accepted['candidate_scope']['branch'])
    categories = {category: [row['part_index'] for row in packed.documentary['parts'] if row['category']==category]
        for category in ('frames','runtime')}
    outputs(parts_root=packed.frames_root, runtimeparts_root=packed.runtimeparts_root,
        parts_count=max(categories['frames'],default=-1)+1,
        runtimeparts_count=max(categories['runtime'],default=-1)+1,
        parts_start=min(categories['frames'],default=0),runtimeparts_start=min(categories['runtime'],default=0),
        package_manifest=packed.path/'g5a-source-parts-manifest.json',
        package_manifest_sha256=packed.documentary['package_manifest']['sha256'],
        family_draft=assembled['draft'],family_draft_sha256=assembled['draft_sha256'],primary_root=controls)
    return terminal


def acquisition_error(documentary, result):
    """Discriminate an original RED; this pure classifier grants no authority."""
    report = documentary.get('worker_report', {}).get('report', {})
    if report.get('status') != 'EVIDENCE_ACQUIRED_ONLY_NO_G5_GREEN':
        return signature(ValueError(report.get('reason', 'G5A_ACTIONS_ORIGINAL_WORKER_RED')))
    kernel = result.get('kernel', {})
    if kernel.get('timed_out') is True: return 'G5A_ACTIONS_ORIGINAL_NATIVE_TIMEOUT'
    from scripts.rc6_controlled_native_child_manager import managed_phase_green
    if not managed_phase_green(kernel): return 'G5A_ACTIONS_ORIGINAL_OUTER_NATIVE_RED'
    monitor = result.get('lease')
    if getattr(monitor, 'stop_reason', None): return signature(ValueError(monitor.stop_reason))
    if documentary.get('actual_original_cuts') != 1202 or documentary.get('complete_original_1202_frame_set_observed') is not True:
        return 'G5A_ACTIONS_COMPLETE_ORIGINAL1202_FRAMES_MISSING'
    return None


def outputs(**values):
    require(all(re.fullmatch(r'[a-z][a-z0-9_]{0,63}', key) and '\n' not in str(value)
        and '\r' not in str(value) and len(str(value)) <= 4096 for key, value in values.items()),
        'G5A_ACTIONS_LITERAL_OUTPUT_FIELDS_REQUIRED')
    _OWN_OUTPUTS.update({key: str(value) for key, value in values.items()})
    target = os.environ.get('GITHUB_OUTPUT')
    if not target: return
    descriptor = os.open(target, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW | os.O_CLOEXEC)
    with os.fdopen(descriptor, 'a') as stream:
        for key, value in values.items(): stream.write(key + '=' + str(value) + '\n')


def parser():
    value = argparse.ArgumentParser(description=__doc__)
    mode = value.add_mutually_exclusive_group(required=True)
    mode.add_argument('--prepare-only', action='store_true'); mode.add_argument('--execute', action='store_true')
    value.add_argument('--repo-root', type=Path, default=ROOT)
    for name in ('source-sha', 'source-tree', 'launch-receipt-url', 'owner-session'):
        value.add_argument('--' + name, required=True)
    value.add_argument('--g5a-revision-approval-url', '--revision-approval-url', dest='g5a_revision_approval_url', required=True)
    value.add_argument('--python311'); value.add_argument('--work-parent', type=Path)
    value.add_argument('--prepared-manifest', type=Path)
    value.add_argument('--prepared-root', type=Path)
    return value


def main(argv=None):
    args = parser().parse_args(argv); reader = None; code = 1
    _OWN_OUTPUTS.clear()
    try:
        # The real factory is mandatory FIRST: no prepared JSON or control path can grant authority.
        context = fresh_context(args)
        require((args.prepare_only and args.python311 is not None
                and args.prepared_manifest is None and args.prepared_root is None)
            or (args.execute and (args.prepared_manifest is not None) != (args.prepared_root is not None)
                and args.python311 is None and args.work_parent is None),
            'G5A_ACTIONS_CLOSED_PREPARE_OR_EXECUTE_INPUTS_REQUIRED')
        os.umask(0o022)
        require(ctypes.CDLL(None, use_errno=True).prctl(4, 0, 0, 0, 0) == 0,
            'G5A_ACTIONS_PARENT_DUMPABLE_ZERO_REQUIRED')
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        token = os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
        require(type(token) is str and bool(token), 'G5A_ACTIONS_PARENT_READONLY_TOKEN_REQUIRED')
        reader = ParentAPI(bytearray(token.encode('ascii'))); token = None
        with parent_api(reader):
            result = prepare(args, context, reader) if args.prepare_only else execute(args, context, reader)
        code = 0
        print(calibration.wire({'status': 'PREPARED_NOT_AUTHORITY' if args.prepare_only else result['status'],
            'source_sha': args.source_sha, 'source_tree': args.source_tree, 'G5_GREEN': False,
            'qualification_claimed': False, 'real_orders_sent': 0}).decode(), flush=True)
    except BaseException as error:
        sealed_payload = _OWN_OUTPUTS.get('safe_payload_upload') == 'true'
        failure = {'status': 'RED', 'reason': signature(error), 'original_RED_preserved': True,
            'safe_payload_upload': sealed_payload, 'G5_GREEN': False, 'qualification_claimed': False,
            'foreign_resources_removed': 0, 'real_orders_sent': 0}
        # Only a previously created own control root; never discover or scan a failed producer.
        if 'control_root' in _OWN_OUTPUTS:
            try:
                controls = native._owned_directory(_OWN_OUTPUTS['control_root'])
                calibration.publish(controls / 'g5a-actions-error.json', failure)
                outputs(safe_payload_upload='true' if sealed_payload else 'false')
            except BaseException:
                failure['error_control_preservation'] = 'G5A_ACTIONS_OWN_CONTROL_WRITE_UNAVAILABLE'
        print(calibration.wire(failure).decode(), flush=True)
    finally:
        if reader is not None: reader.close()
        for key in SECRET_ENV: os.environ.pop(key, None)
    return code


if __name__ == '__main__': raise SystemExit(main())
