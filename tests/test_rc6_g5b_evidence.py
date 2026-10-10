"""Cheap Source transport regressions, never native quota/G5 qualification."""
import copy
from datetime import datetime, timezone
import hashlib
import io
from pathlib import Path
import zipfile

import pytest

from rc6_shadow_runtime import archive_components as components
from scripts import rc6_architectural_gates as gates
from scripts import rc6_g5b_evidence as g5b
from scripts import rc6_g5_historical_certification as historical


def manifest():
    frames = [{'cut_index': index, 'path': f'cut-{index:04d}.json.gz', 'bytes': 2, 'sha256': components.sha(b'{}')}
        for index in range(1202)]
    return {'schema': g5b.PARTS_SCHEMA, 'source_sha': 'a'*40, 'source_tree': 'b'*40,
        'run_id': 17, 'run_attempt': 1, 'writer_codec_restorer_binding_sha256': 'c'*64, 'actual_original_cuts': 1202,
        'parts': [{'artifact_id': 19, 'run_id': 17, 'run_attempt': 1, 'name': 'original-frames-000',
            'digest': 'sha256:'+'d'*64, 'size_in_bytes': 1024, 'members': frames}]}


def validate(value):
    return g5b.validate_parts_manifest(value, source_sha='a'*40, source_tree='b'*40,
        run_id=17, run_attempt=1, writer_binding_sha256='c'*64)


def test_exact_all1202_manifest_is_data_only_not_approval():
    value = manifest()
    assert len(validate(value)) == 1202
    with pytest.raises(ValueError, match='AUTHENTICATED_ACTIONS_CAPTURE_REQUIRED'):
        g5b.registered_archive(value)
    with pytest.raises(ValueError, match='FACTORY_REQUIRED'):
        historical.HistoricalG5Certification(value)
    with pytest.raises(ValueError, match='AUTHENTIC_FINISHED_OPERATION_CERTIFICATION'):
        historical.validate_historical_certification(value, row={})


@pytest.mark.parametrize('mutation', ['missing', 'duplicate', 'renamed', 'oversized', 'boolean_attempt',
    'source', 'writer', 'foreign_run', 'part_size', 'nonsha'])
def test_parts_reject_partial_rebound_or_relaxed_limits(mutation):
    value = manifest(); part = value['parts'][0]
    if mutation == 'missing': part['members'].pop()
    if mutation == 'duplicate': part['members'][1]['cut_index'] = 0
    if mutation == 'renamed': part['members'][0]['path'] = '../cut-0000.json.gz'
    if mutation == 'oversized': part['members'][0]['bytes'] = 16*1024**2+1
    if mutation == 'boolean_attempt': part['run_attempt'] = True
    if mutation == 'source': value['source_sha'] = 'e'*40
    if mutation == 'writer': value['writer_codec_restorer_binding_sha256'] = 'e'*64
    if mutation == 'foreign_run': part['run_id'] = 18
    if mutation == 'part_size': part['size_in_bytes'] = 256*1024**2+1
    if mutation == 'nonsha': part['digest'] = 'SHA256:'+'d'*64
    with pytest.raises(ValueError, match='G5B_'):
        validate(value)


def _api_fixture(zip_bytes, *, artifact_id=19):
    run = {'id': 17, 'head_sha': 'a'*40, 'run_attempt': 1, 'status': 'completed', 'conclusion': 'success',
        'path': gates.WORKFLOW, 'repository': {'id': gates.REPOSITORY_ID, 'full_name': gates.REPOSITORY},
        'head_repository': {'id': gates.REPOSITORY_ID}, 'run_started_at': '2026-10-10T00:00:00Z',
        'updated_at': '2026-10-10T00:01:00Z'}
    artifact = {'id': artifact_id, 'name': 'original-frames-000', 'digest': 'sha256:'+components.sha(zip_bytes),
        'expired': False, 'size_in_bytes': len(zip_bytes), 'created_at': '2026-10-10T00:00:30Z',
        'expires_at': '2099-10-10T00:00:00Z', 'workflow_run': {'id': 17, 'head_sha': 'a'*40,
            'repository_id': gates.REPOSITORY_ID, 'head_repository_id': gates.REPOSITORY_ID}}
    job = {'id': 21, 'run_id': 17, 'status': 'completed', 'conclusion': 'success', 'labels': ['ubuntu-24.04'],
        'runner_group_name': 'GitHub Actions', 'runner_name': 'GitHub Actions 21',
        'started_at': run['run_started_at'], 'completed_at': run['updated_at']}
    rows = {f'/actions/artifacts/{artifact_id}': artifact, '/actions/runs/17': run,
        '/actions/runs/17/attempts/1': run, '/actions/runs/17/attempts/1/jobs?per_page=100': {'total_count': 1, 'jobs': [job]}}
    calls = []
    def get(path):
        calls.append(path); return copy.deepcopy(rows[path])
    def download(identifier, destination, **kwargs):
        assert identifier == artifact_id and kwargs['expected_size'] == len(zip_bytes)
        assert kwargs['expected_digest'] == artifact['digest']
        destination.write_bytes(zip_bytes)  # Existing transport's umask mode.
    return get, download, calls, artifact


def test_real_small_zip_capture_checks_exact_api_origin_and_bytes(tmp_path):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive: archive.writestr('cut-0000.json.gz', b'{}')
    raw = output.getvalue(); get, download, calls, artifact = _api_fixture(raw)
    root = tmp_path/'own'; root.mkdir(mode=0o700)
    reference = {'artifact_id': 19, 'artifact_digest': artifact['digest'], 'run_id': 17, 'run_attempt': 1}
    path, identity, sha, _, _, _ = g5b._download_authentic(reference, source_sha='a'*40, root=root,
        get=get, download=download, expected_name=artifact['name'])
    assert sha == hashlib.sha256(raw).hexdigest()
    assert path.stat().st_mode & 0o777 == 0o600
    assert gates.capture_member(path, 'cut-0000.json.gz') == b'{}'
    assert len(calls) == 4
    # This tests genuine own bytes against a mocked API fixture. It never
    # asserts that those fixtures are authentic native execution evidence.
    assert identity == g5b._file_identity(path)


def test_changed_download_is_rejected_even_when_api_metadata_says_success(tmp_path):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive: archive.writestr('cut-0000.json.gz', b'{}')
    raw = output.getvalue(); get, _, _, artifact = _api_fixture(raw)
    root = tmp_path/'own'; root.mkdir(mode=0o700)
    def changed(identifier, destination, **kwargs): destination.write_bytes(raw+b'corrupt')
    with pytest.raises(ValueError, match='ZIP_BYTES_REBOUND'):
        g5b._download_authentic({'artifact_id': 19, 'artifact_digest': artifact['digest'], 'run_id': 17, 'run_attempt': 1},
            source_sha='a'*40, root=root, get=get, download=changed)


def _owner_decisions():
    from scripts import rc6_contract_revisions as revisions
    from scripts import rc6_g5_contract as contract
    fields = {'CONTRACT_ACCEPTANCE_SCHEMA': revisions.SCHEMA, 'RC6_CONTRACT_REVISION_AUTHORIZATION': 'APPROVED',
        'CONTRACT_DECISION_ORIGIN': 'EXPLICIT_OWNER_DECISION', 'CONTRACT_REVISION': contract.REVISION,
        'CONTRACT_SCOPE': contract.SCOPE, 'CONTRACT_DOCUMENT_SHA256': contract.CONTRACT_SHA256,
        'CONTRACT_GATES_ALREADY_GREEN': 'false', 'NATIVE_EQUIVALENCE_FROM_APPROVAL_CLAIMED': 'false',
        'FINAL_CANDIDATE_ELIGIBLE': 'false', 'DEPLOY_AUTHORIZATION': 'NOT_GRANTED', 'SOURCE_SHA': 'a'*40,
        'SOURCE_TREE': 'b'*40, 'WRITE_OWNER': 'owner', 'INTEGRATION_OWNER': 'owner', 'SESSION_SUCCESSOR': 'owner',
        'DEPLOY_OWNER': 'NOT_ACQUIRED', 'RELEASED': 'false', 'MODE': 'PRODUCTION_PAPER / SIMULATION',
        'real_orders_sent': '0', 'SOURCE_LEASE_EXPIRES_UTC': '2026-10-10T00:20:00Z', 'WORKSTREAM_ID': 'unit-only'}
    def row(issue, identifier, body):
        return {'id': identifier, 'html_url': f'https://github.com/{gates.REPOSITORY}/issues/{issue}#issuecomment-{identifier}',
            'issue_url': f'https://api.github.com/repos/{gates.REPOSITORY}/issues/{issue}', 'user': {'login': 'mbalbo2023'},
            'created_at': '2026-10-10T00:00:00Z', 'updated_at': '2026-10-10T00:00:00Z', 'body': body}
    twin = row(473, 23, '\n'.join(f'{key}={value}' for key, value in fields.items()))
    primary = row(471, 22, '\n'.join(f'{key}={value}' for key, value in
        {**fields, 'OWNER_RECEIPT_473': '23', 'OWNER_RECEIPT_473_SHA256': components.sha(twin['body'].encode())}.items()))
    pointer = {'approval_url': primary['html_url'], 'approval_body_sha256': components.sha(primary['body'].encode()),
        'paired_comment_id': 23, 'paired_body_sha256': components.sha(twin['body'].encode()),
        'accepted_utc': '2026-10-10T00:05:00Z'}
    return pointer, {'/issues/comments/22': primary, '/issues/comments/23': twin}


def test_historical_parser_checks_real_launch_window_without_minting_revision(monkeypatch):
    from scripts import rc6_contract_revisions as revisions
    from scripts import rc6_g5_contract as contract
    monkeypatch.setattr(revisions, 'authenticate_revision', lambda *a, **k: pytest.fail('Past approval must not be minted'))
    pointer, rows = _owner_decisions()
    args = dict(source_sha='a'*40, source_tree='b'*40, contract_sha256=contract.CONTRACT_SHA256,
        owner='owner', get=lambda path: rows[path], actual_run_started=datetime(2026, 10, 10, 0, 1, tzinfo=timezone.utc),
        native_started=datetime(2026, 10, 10, 0, 6, tzinfo=timezone.utc))
    actual, fields = historical._decision(pointer, **args)
    assert len(actual) == 2 and fields['CONTRACT_GATES_ALREADY_GREEN'] == 'false'
    args['actual_run_started'] = datetime(2026, 10, 10, 0, 20, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match='OUTSIDE_INITIAL_ACCEPTANCE'):
        historical._decision(pointer, **args)


def test_historical_decision_edits_and_pair_changes_fail():
    from scripts import rc6_g5_contract as contract
    pointer, rows = _owner_decisions()
    rows['/issues/comments/23']['body'] += '\nCONTRACT_GATES_ALREADY_GREEN=true'
    with pytest.raises(ValueError, match='EDITED_DELETED_OR_REBOUND'):
        historical._decision(pointer, source_sha='a'*40, source_tree='b'*40, contract_sha256=contract.CONTRACT_SHA256,
            owner='owner', get=lambda path: rows[path], actual_run_started=datetime(2026,10,10,0,1,tzinfo=timezone.utc),
            native_started=datetime(2026,10,10,0,6,tzinfo=timezone.utc))


def test_native_root_acceptance_uses_actual_utc_during_trusted_setup_not_before_manager_start():
    from scripts import rc6_g5_contract as contract
    pointer, rows = _owner_decisions()
    pointer['accepted_utc'] = '2026-10-10T00:05:00+00:00'
    actual, _ = historical._decision(pointer, source_sha='a'*40, source_tree='b'*40,
        contract_sha256=contract.CONTRACT_SHA256, owner='owner', get=lambda path: rows[path],
        actual_run_started=datetime(2026,10,10,0,1,tzinfo=timezone.utc),
        native_started=datetime(2026,10,10,0,4,tzinfo=timezone.utc),
        native_closed=datetime(2026,10,10,6,4,tzinfo=timezone.utc))
    assert len(actual) == 2


def test_historical_capture_factory_rejects_json_without_ever_reading_github():
    with pytest.raises(ValueError, match='AUTHENTICATED_ACTIONS_CAPTURE_REQUIRED'):
        historical.authenticate_historical_certification({'Source': 'a'*40},
            get=lambda _: pytest.fail('No native capture authority'))


def test_finished_source_and_artifact_join_cannot_use_json_authority(tmp_path):
    with pytest.raises(ValueError, match='QUALIFICATION_CONTEXT_REQUIRED'):
        gates.validate_g5_writer_artifact({'status': 'GREEN'}, source_sha='a'*40, source_tree='b'*40,
            source_root=tmp_path, artifact_root=tmp_path)
    with pytest.raises(ValueError, match='FACTORY_REQUIRED'):
        g5b.VerifiedG5Source({}, archive_context=None, report={}, historical_certification=None)


def test_post_root_fin_review_rejects_metadata_instead_of_actual_capture(tmp_path):
    from scripts import rc6_g5b_native_readers as native
    with pytest.raises(ValueError, match='OPAQUE_PINNED_STREAM_REQUIRED'):
        native.execute_post_fin_review({'ROOT_FIN': True}, source_root=tmp_path, python311=Path('/usr/bin/python'),
            control_root=tmp_path/'controls', deadline=1, owner_check=lambda _: None,
            capacity_preflight=lambda *a: {}, monitor_factory=lambda *a: {})
