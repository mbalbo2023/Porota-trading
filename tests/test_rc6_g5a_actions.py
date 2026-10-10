"""Economic frontend guard regressions; no quota/image/native authority issuance.

HTTP bodies are synthetic READ_ONLY transport fixtures. No fixture constructs
opaque Source, ROOT, quota, namespace, lease or FIN authority, or launches a child.
"""
import ast
import io
from pathlib import Path
from types import SimpleNamespace
import urllib.error
import urllib.request

import pytest

from scripts import rc6_g5a_actions as actions
from scripts import rc6_material_pr_admission as admission


SHA, TREE = 'a' * 40, 'b' * 40
OWNER = 'CODEX_RC6_G5A_UNIT'
URL = 'https://github.com/mbalbo2023/Porota-trading/issues/471#issuecomment-123'


def arguments(mode='--prepare-only'):
    result = [mode, '--source-sha', SHA, '--source-tree', TREE, '--owner-session', OWNER,
        '--launch-receipt-url', URL, '--revision-approval-url', URL]
    return result + (['--python311', '/declared/python3.11'] if mode == '--prepare-only'
        else ['--prepared-root', '/declared/prepared'])


def deny_allocation(monkeypatch):
    for module, name in ((actions.tempfile, 'mkdtemp'), (actions.owned, 'create_namespace'),
            (actions.native, 'launch'), (actions.runner, 'read_input')):
        monkeypatch.setattr(module, name, lambda *a, **k: pytest.fail('NO_IO_BEFORE_CURRENT_API_AUTHORITY'))


@pytest.mark.parametrize('mode', ('--prepare-only', '--execute'))
def test_source_owner_factory_denial_precedes_output_manifest_install_or_native(mode, monkeypatch, capsys):
    deny_allocation(monkeypatch)
    calls = []
    def denied(_args):
        calls.append('fresh_Source_owner_contract_factory')
        raise ValueError('CONTRACT_OWNER_DECISION_NOT_ACCEPTED')
    monkeypatch.setattr(actions, 'fresh_context', denied)
    assert actions.main(arguments(mode)) == 1
    result = actions.calibration.decode(capsys.readouterr().out.encode())
    assert calls == ['fresh_Source_owner_contract_factory']
    assert result['reason'] == 'CONTRACT_OWNER_DECISION_NOT_ACCEPTED'
    assert result['original_RED_preserved'] and not result['G5_GREEN'] and not result['safe_payload_upload']


@pytest.mark.parametrize('mode', ('--prepare-only', '--execute'))
def test_actual_local_non_actions_process_cannot_create_or_launch_even_with_documentary_paths(mode, monkeypatch, capsys):
    deny_allocation(monkeypatch)
    monkeypatch.delenv('GITHUB_ACTIONS', raising=False)
    assert actions.main(arguments(mode)) == 1
    result = actions.calibration.decode(capsys.readouterr().out.encode())
    assert result['reason'] in ('G5A_ACTIONS_ACTUAL_SINGLE_THREAD_NONROOT_REQUIRED',
        'G5A_ACTIONS_ACTUAL_FIRST_CANONICAL_MANUAL_ACTIONS_REQUIRED')


@pytest.mark.parametrize('value', (None, True, {}, {'accepted': True, 'G5_GREEN': True},
    SimpleNamespace(source_sha=SHA, source_tree=TREE)))
def test_mapping_or_named_attributes_do_not_recreate_preparation_context(value, monkeypatch):
    deny_allocation(monkeypatch)
    with pytest.raises(ValueError, match='CURRENT_TYPED_API_SOURCE_CONTEXT_REQUIRED'):
        actions.prepare(SimpleNamespace(), value, object())
    with pytest.raises(ValueError, match='CURRENT_TYPED_API_SOURCE_CONTEXT_REQUIRED'):
        actions.execute(SimpleNamespace(), value, object())
    with pytest.raises(ValueError, match='CURRENT_TYPED_API_SOURCE_CONTEXT_REQUIRED'):
        actions.verify_preparation_artifacts(value, Path('/declared'), object())
    with pytest.raises(ValueError, match='CURRENT_TYPED_API_SOURCE_CONTEXT_REQUIRED'):
        actions.DecisionAPI(value, object(), operation_deadline=float('inf'))


def transport_issuer(monkeypatch):
    # Pure HTTP test identity; it is never used by a native authority factory.
    for name in ('getuid', 'geteuid', 'getgid', 'getegid'):
        monkeypatch.setattr(actions.os, name, lambda: 65534)
    monkeypatch.setattr(actions.os, 'listdir', lambda _path: ['declared-single-thread'])


class Response(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *args): self.close()


class HTTPTransport:
    def __init__(self, raw): self.raw, self.requests = raw, []
    def open(self, request, *, timeout):
        self.requests.append((request, timeout))
        return Response(self.raw)


def test_parent_readonly_transport_is_get_only_and_closed_buffer_is_wiped(monkeypatch):
    transport_issuer(monkeypatch)
    token = bytearray(b'DECLARED_UNIT_BYTES_NO_NATIVE_CREDENTIAL')
    transport = HTTPTransport(b'{"source":"synthetic-transport"}')
    reader = actions.ParentAPI(token, opener=transport)
    assert reader.request('/actions/artifacts/7') == {'source': 'synthetic-transport'}
    request, timeout = transport.requests[0]
    assert request.get_method() == 'GET' and request.data is None and 0 < timeout <= 30
    assert request.full_url == 'https://api.github.com/repos/' + admission.REPO + '/actions/artifacts/7'
    reader.close()
    assert token == bytearray(len(token))
    with pytest.raises(ValueError, match='CLOSED_READONLY_API_PATH_REQUIRED'):
        reader.request('/actions/artifacts/7')


@pytest.mark.parametrize('path', ('https://foreign.invalid/data', '//foreign.invalid/data', '/a/../data',
    '/%2e%2e/data', '/a/./data', '/a#fragment', '/a\nBearer secret', '/a%0aBearer', '/a\x7f'))
def test_transport_rejects_foreign_or_ambiguous_paths_without_network(path, monkeypatch):
    transport_issuer(monkeypatch)
    transport = HTTPTransport(b'{}'); reader = actions.ParentAPI(bytearray(b'UNIT_BYTES'), opener=transport)
    with pytest.raises(ValueError, match='CLOSED_READONLY_API_PATH_REQUIRED'):
        reader.request(path)
    assert not transport.requests
    reader.close()


@pytest.mark.parametrize('raw, reason', ((b'{"x":1,"x":2}', 'ADMISSION_DUPLICATE_JSON_KEY'),
    (b'{"x":NaN}', 'ADMISSION_NONFINITE_JSON'), (b'x' * (admission.CAP + 1), 'API_DOCUMENT_BOUND')))
def test_real_transport_duplicate_nonfinite_and_oversized_documents_fail_closed(raw, reason, monkeypatch):
    transport_issuer(monkeypatch)
    reader = actions.ParentAPI(bytearray(b'UNIT_BYTES'), opener=HTTPTransport(raw))
    with pytest.raises(ValueError, match=reason): reader.request('/actions/artifacts/7')
    reader.close()


def test_http_failure_retains_discriminant_without_url_bearer_or_response_body(monkeypatch):
    transport_issuer(monkeypatch)
    class Denied:
        def open(self, request, *, timeout):
            raise urllib.error.HTTPError(request.full_url + '/private-account', 403,
                'Bearer DECLARED_UNIT_SECRET', {}, None)
    reader = actions.ParentAPI(bytearray(b'DECLARED_UNIT_SECRET'), opener=Denied())
    with pytest.raises(ValueError) as caught: reader.request('/actions/artifacts/7')
    assert str(caught.value) == 'G5A_ACTIONS_API_HTTP_403'
    assert 'SECRET' not in str(caught.value) and 'private-account' not in str(caught.value)
    reader.close()


@pytest.mark.parametrize('size,digest,identifier', ((True, 'sha256:'+'a'*64, 7),
    (actions.gates.MAX_ARCHIVE_BYTES + 1, 'sha256:'+'a'*64, 7), (1, 'unknown', 7),
    (1, 'sha256:'+'a'*64, True)))
def test_prerequisite_download_unknown_or_excessive_bound_never_touches_destination(size, digest, identifier, monkeypatch):
    transport = HTTPTransport(b'{}'); reader = actions.ParentAPI(bytearray(b'UNIT_BYTES'), opener=transport)
    monkeypatch.setattr(actions.runner, 'canonical_path', lambda *_: pytest.fail('NO_DESTINATION_BEFORE_CLOSED_BOUND'))
    with pytest.raises(ValueError, match='PREREQUISITE_ARCHIVE_CLOSED_BOUND_REQUIRED'):
        reader.download(identifier, '/foreign-existing', expected_size=size, expected_digest=digest, deadline=1)
    assert not transport.requests
    reader.close()


def test_authenticated_parent_callback_is_restored_even_when_factory_denies():
    original = admission.api
    transport = HTTPTransport(b'{}'); reader = actions.ParentAPI(bytearray(b'UNIT_BYTES'), opener=transport)
    with pytest.raises(ValueError, match='OWNER_DENIED'):
        with actions.parent_api(reader):
            assert admission.api == reader.request
            raise ValueError('OWNER_DENIED')
    assert admission.api is original
    reader.close()


def test_continuation_cannot_be_constructed_directly_from_documentary_acceptance(monkeypatch):
    denied = {'accepted_utc': '2026-10-10T00:00:00Z', 'source_sha': SHA,
        'source_tree': TREE, 'owner_session': OWNER, 'CONTRACT_ACCEPTED': True}
    transport = HTTPTransport(b'{}')
    reader = actions.ParentAPI(bytearray(b'UNIT_BYTES'), opener=transport)
    monkeypatch.setattr(actions.continuation, 'validate_decision_documents',
        lambda *_: pytest.fail('NO_DECISION_API_WITHOUT_ORIGINAL_FACTORY'))
    with pytest.raises(ValueError, match='CURRENT_TYPED_API_SOURCE_CONTEXT_REQUIRED'):
        actions.DecisionAPI(denied, reader, operation_deadline=actions.time.monotonic() + 21600)
    assert not transport.requests
    reader.close()


def test_source_continuation_does_not_renew_acceptance_or_read_live_source_or_raw():
    parsed = ast.parse(Path(actions.__file__).read_text())
    decision = next(node for node in parsed.body if isinstance(node, ast.ClassDef) and node.name == 'DecisionAPI')
    methods = {node.name: node for node in decision.body if isinstance(node, ast.FunctionDef)}
    def labels(name):
        return {ast.unparse(node.func) for node in ast.walk(methods[name]) if isinstance(node, ast.Call)}
    assert 'validate_context' in labels('__init__')
    continued = set().union(*(labels(name) for name in methods if name != '__init__'))
    assert continued.isdisjoint({'validate_context', 'fresh_context', 'admission.admit_g5a_preparation',
        'admission.validate_g5a_setup_launch', 'revisions.authenticate_revision', 'runner.read_input',
        'exact_repo', 'native.launch', 'owned.create_namespace', 'raw_capture.capture'})
    assert {'admission._G5A_SETUP_CONTEXTS.get', 'os.getpid'} <= labels('accepted_row')
    assert 'continuation.validate_decision_documents' in labels('request')
    assert 'continuation.reject_revocations' in labels('request')
    assert 'admission.owned_gate_control_scope' in labels('owner_check')
    assert 'leases._http_budget' in labels('owner_check')
    monitor_call = next(node for node in ast.walk(methods['monitor_factory'])
        if isinstance(node, ast.Call) and ast.unparse(node.func) == 'leases.GateLeaseMonitor')
    keywords = {item.arg: ast.unparse(item.value) for item in monitor_call.keywords}
    assert keywords['get'] == 'self.request' and keywords['gate'] == "'Horizon'"
    assert 'g5a_revision' not in keywords and 'g5a_namespace_receipt' not in keywords
    assert 'owned.namespace_receipt' in labels('monitor_factory')


def test_source_continuation_keeps_original_operation_and_monitor_deadlines():
    parsed = ast.parse(Path(actions.__file__).read_text())
    execute = next(node for node in parsed.body if isinstance(node, ast.FunctionDef) and node.name == 'execute')
    first = execute.body[0]
    assert isinstance(first, ast.Assign) and ast.unparse(first.targets[0]) == 'operation_deadline'
    assert ast.unparse(first.value) == 'time.monotonic() + runner.HORIZON_SECONDS'
    decision = next(node for node in parsed.body if isinstance(node, ast.ClassDef) and node.name == 'DecisionAPI')
    deadline_method = next(node for node in decision.body if isinstance(node, ast.FunctionDef) and node.name == '_deadline')
    deadlines = {ast.unparse(node.func) for node in ast.walk(deadline_method) if isinstance(node, ast.Call)}
    assert {'self.accepted_row', 'time.monotonic', 'math.isfinite', 'min'} <= deadlines
    assert actions.runner.HORIZON_SECONDS == 21600 and actions.leases.HTTP_BUDGET_SECONDS == 5


def test_product_plan_preserves_original_hash_locks_four_sdists_and_installs_only311():
    commands = actions.product311_commands(Path('/declared/git'), Path('/declared/own'), '/reviewed/base311', 1234)
    assert [label for _, label, _ in commands] == ['venv311', 'locked311-0', 'locked311-1', 'pipcheck311']
    assert [limit for _, _, limit in commands] == [300, 1800, 1800, 300]
    assert commands[0][0][0] == '/reviewed/base311' and '--python-version' in commands[0][0]
    assert '3.11.16' in commands[0][0] and not any('312' in item for command, _, _ in commands for item in command)
    for command, _, _ in commands[1:3]:
        assert '--require-hashes' in command and '--no-cache-dir' in command and '--only-binary=:all:' in command
    assert '--no-binary=msgpack,ppi-client,signalrcoreppi,ta' in commands[2][0]
    assert '--no-build-isolation' in commands[2][0]
    assert commands[1][0][-1] == '/declared/git/requirements.build.lock.txt'
    assert commands[2][0][-1] == '/declared/git/requirements.lock.txt'


def test_public_git_preparation_never_inherits_control_bearer_and_does_not_mutate_parent_values():
    original = {'GIT_ASKPASS': '/declared/script', 'RC6_READONLY_GIT_TOKEN': 'DECLARED_UNIT_SECRET'}
    cleaned = actions.preparation_environment(['git', 'fetch'], original)
    assert original['RC6_READONLY_GIT_TOKEN'] == 'DECLARED_UNIT_SECRET'
    assert 'RC6_READONLY_GIT_TOKEN' not in cleaned and cleaned['GIT_ASKPASS'] == '/bin/false'
    assert cleaned['SSH_ASKPASS'] == '/bin/false' and cleaned['GIT_CONFIG_GLOBAL'] == '/dev/null'


@pytest.mark.parametrize('env,command', (({'GH_TOKEN':'DECLARED'}, ['git']),
    ({'GITHUB_TOKEN':'DECLARED'}, ['/declared/python']), ({'RC6_READONLY_GIT_TOKEN':'DECLARED'}, ['/declared/python'])))
def test_extra_child_credential_channels_are_closed(env, command):
    with pytest.raises(ValueError, match='CHILD_BEARER_FORBIDDEN|CREDENTIAL_OUTSIDE_GIT_FORBIDDEN'):
        actions.preparation_environment(command, env)


def test_current_base_or_local_interpreter_cannot_be_declared_product157(monkeypatch):
    monkeypatch.setattr(actions.importlib.metadata, 'distributions', lambda: pytest.fail('NO_METADATA_WITHOUT_PRODUCT311'))
    monkeypatch.setattr(actions.calibration, 'publish', lambda *a, **k: pytest.fail('NO_MANIFEST_WITHOUT_PRODUCT311'))
    with pytest.raises(ValueError, match='ACTUAL_PRODUCT311_MANIFEST_PROCESS_REQUIRED'):
        actions.record_interpreter(source_repo='/declared/git', source_root='/declared/export', source_sha=SHA,
            source_tree=TREE, binding={}, output='/declared/product.json')


@pytest.mark.parametrize('values', ({'payload_root':'/path\nunsafe=true'}, {'Payload':'value'}, {'safe_payload_upload':'x\runsafe'},
    {'control_root':'x'*4097}))
def test_workflow_outputs_reject_line_injection_or_unbounded_fields(values, monkeypatch):
    monkeypatch.setenv('GITHUB_OUTPUT', '/foreign-existing')
    monkeypatch.setattr(actions.os, 'open', lambda *a, **k: pytest.fail('NO_OUTPUT_FILE_BEFORE_LITERAL_GUARD'))
    with pytest.raises(ValueError, match='LITERAL_OUTPUT_FIELDS_REQUIRED'): actions.outputs(**values)


@pytest.mark.parametrize('error, expected', ((ValueError('ROOT_NATIVE_EDQUOT'), 'ROOT_NATIVE_EDQUOT'),
    (ValueError('ROOT_NATIVE_EDQUOT:/declared/private'), 'ROOT_NATIVE_EDQUOT'),
    (ValueError('secret-token=abc'), 'G5A_ACTIONS_NON_LITERAL_FAILURE'),
    (ValueError('Bearer abc'), 'G5A_ACTIONS_NON_LITERAL_FAILURE')))
def test_error_discriminant_preserves_literal_first_cause_without_private_text(error, expected):
    assert actions.signature(error) == expected


def test_source_frontend_keeps_authentication_native_artifacts_and_original_fin_in_security_order():
    parsed = ast.parse(Path(actions.__file__).read_text())
    functions = {node.name: node for node in parsed.body if isinstance(node, ast.FunctionDef)}
    def calls(name):
        return [label for _, label in sorted((node.lineno, ast.unparse(node.func))
            for node in ast.walk(functions[name]) if isinstance(node, ast.Call))]
    main = calls('main')
    assert main.index('fresh_context') < main.index('ParentAPI') < main.index('prepare')
    prep = calls('prepare')
    assert prep.index('validate_context') < prep.index('verify_preparation_artifacts') < prep.index('owned.create_namespace')
    assert prep.index('verify_preparation_artifacts') < prep.index('installed311')
    assert prep.index('original.complete_full_git') < prep.index('original.fetch_originals') < prep.index('export.observe_export')
    verify = calls('verify_preparation_artifacts')
    assert verify.index('validate_context') < verify.index('gates.verify_manifest') < verify.index('gates.verify_capacity_bootstrap_artifact')
    execute = calls('execute')
    assert execute.index('validate_context') < execute.index('prepared_document') < execute.index('native.authenticate_setup_launch')
    assert execute.index('os.environ.pop') < execute.index('native.launch')
    assert execute.index('native.launch') < execute.index('native.validate_native_result') < execute.index('raw_capture.retire_own_backing')
    assert execute.index('raw_capture.retire_own_backing') < execute.index('raw_capture.capture')
    assert not any('cleanup_namespace' in label or label.endswith('.unlink') for label in prep + execute)


def test_cli_has_no_skip_gate_acceptance_bool_limits_or_serialized_authority_options():
    parser = actions.parser()
    options = {item for action in parser._actions for item in action.option_strings}
    assert {'--prepare-only', '--execute', '--revision-approval-url', '--prepared-root'} <= options
    assert options.isdisjoint({'--accepted', '--allow-green', '--skip-G0', '--max-cycles', '--memory', '--authority-json'})
    assert parser.parse_args(arguments()).g5a_revision_approval_url == URL
    assert parser.parse_args(arguments('--execute')).prepared_root == Path('/declared/prepared')
