"""R367 runner ingress, authenticated dispatch and public receipt boundary."""

from contextlib import ExitStack, redirect_stdout
import base64
from datetime import datetime, timezone
import hashlib
import io
import json
import unittest
from unittest.mock import patch
import urllib.parse

from scripts import rc6_r321_root_install_actions as r321
from scripts import rc6_genesis_control_actions as actions


def canonical(doc):
    return (json.dumps(doc, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode('ascii')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


BINDING = {'trusted_main_sha': '1' * 40, 'candidate_sha': '2' * 40, 'candidate_tree': '3' * 40,
           'host_identity_sha256': r321.R363_HOST_IDENTITY_SHA256, 'run_id': '30', 'run_attempt': '1'}
INITIAL = {'binding': BINDING, 'nonce': 'a' * 32, 'lease_id': 4, 'prepare_go_id': 6,
           'issued_at': '2026-11-14T10:00:00Z', 'expires_at': '2026-11-14T10:30:00Z'}
PROPOSAL = 'b' * 32
COMMITMENT = 'c' * 64
PRIVATE = 'runner-secret-must-not-be-logged'
ENV = {'RC6_R321_OPERATION': 'PREPARE_ENV', 'RC6_R367_PREPARE_PHASE': 'INITIAL',
       'GITHUB_TOKEN': PRIVATE, 'GITHUB_ACTOR_ID': '151845499', 'GITHUB_RUN_ID': '30',
       'GITHUB_RUN_ATTEMPT': '1', 'GITHUB_SHA': BINDING['trusted_main_sha'],
       'GITHUB_EVENT_NAME': 'workflow_dispatch', 'GITHUB_REPOSITORY': 'mbalbo2023/Porota-trading',
       'GITHUB_REF': 'refs/heads/main', 'GITHUB_ACTOR': 'mbalbo2023', 'GITHUB_TRIGGERING_ACTOR': 'mbalbo2023',
       'RC6_R367_LEASE_ID': '4', 'RC6_R367_PREPARE_GO_ID': '6'}


def manifest(binding=BINDING):
    return canonical({'schema': 'RC6_ROOT_INSTALL_MANIFEST_V1', 'candidate_sha': binding['candidate_sha'],
                      'candidate_tree': binding['candidate_tree'], 'paper_mode': 'PRODUCTION_PAPER',
                      'real_orders_allowed': False,
                      'files': {k: '1' * 64 for k in ('helper', 'custody', 'profiles', 'approval', 'unit_template')}})


def host_receipt(step, raw, binding=BINDING):
    doc = {'schema': 'RC6_R367_HOST_RECEIPT_V1', 'step': step, 'request_sha256': sha(raw),
           'proposal_id': PROPOSAL, 'commitment': COMMITMENT}
    if step in ('POST_D_E', 'SEAL_ENV'):
        data = manifest(binding)
        doc.update(manifest_b64=base64.b64encode(data).decode('ascii'), manifest_sha256=sha(data),
                   generation_commitment='d' * 64)
    return doc


class GenesisControl(unittest.TestCase):
    def test_g8_accepts_only_live_pr_run_and_unique_r252_job_receipt_of_this_de(self):
        from copy import deepcopy
        from scripts import rc6_ci_release_required_guard as r252
        root = '/repos/' + r252.REPO
        latest = root + '/actions/runs/81'
        attempt = latest + '/attempts/2'
        run = {'id': 81, 'workflow_id': 366199103, 'run_attempt': 2, 'event': 'pull_request',
               'name': 'Porota Predeploy V2', 'head_sha': BINDING['candidate_sha'],
               'head_branch': r252.CANDIDATE_BRANCH, 'path': '.github/workflows/porota-predeploy-v2.yml',
               'status': 'completed', 'conclusion': 'success', 'repository': {'full_name': r252.REPO},
               'head_repository': {'full_name': r252.REPO}, 'pull_requests': [{'number': 1087,
                   'head': {'sha': BINDING['candidate_sha'], 'ref': r252.CANDIDATE_BRANCH},
                   'base': {'ref': actions.r340.PRODUCT}}]}
        job = {'id': 82, 'name': 'RC6 required release evidence (offhost)', 'run_id': 81, 'run_attempt': 2,
               'head_sha': BINDING['candidate_sha'], 'status': 'completed', 'conclusion': 'success',
               'steps': [{'name': 'Verify pinned R252 trusted RELEASE receipt', 'status': 'completed',
                          'conclusion': 'success'}]}
        release = {'candidate_sha': BINDING['candidate_sha'], 'candidate_tree_sha': BINDING['candidate_tree'],
                   'source_material': {'release': {'run_id': 44, 'run_attempt': 2, 'artifact_id': 50,
                                                   'artifact_digest': 'sha256:' + 'b' * 64},
                                       'postcompletion': {'run_id': 51, 'run_attempt': 1}}}
        proof = {'status': 'TRUSTED_RELEASE_BYTES_BOUND', 'mode': 'PR_CANDIDATE', 'product_sha': '',
                 'candidate_sha': BINDING['candidate_sha'], 'candidate_tree_sha': BINDING['candidate_tree'],
                 'release_run_id': 44, 'release_run_attempt': 2, 'release_artifact_id': 50,
                 'release_artifact_digest': 'sha256:' + 'b' * 64, 'trusted_main_audit_run_id': 51,
                 'trusted_main_audit_run_attempt': 1, 'artifact_contents_verified_by_this_guard': True,
                 'owner_go_verified': False, 'runtime_verified': False, 'automatic_go': False}
        env = {**ENV, 'RC6_R367_PREDEPLOY_RUN_ID': '81', 'RC6_R367_PREDEPLOY_RUN_ATTEMPT': '2'}
        for scenario in ('good', 'skipped_job', 'skipped_step', 'latest_attempt_changed',
                         'wrong_pr', 'duplicate', 'foreign_sha', 'wrong_release', 'duplicate_log'):
            with self.subTest(scenario=scenario):
                records = {latest: deepcopy(run), attempt: deepcopy(run),
                           attempt + '/jobs?per_page=100': {'total_count': 1, 'jobs': [deepcopy(job)]}}
                jobs = records[attempt + '/jobs?per_page=100']
                if scenario == 'skipped_job':
                    jobs['jobs'][0]['conclusion'] = 'skipped'
                if scenario == 'skipped_step':
                    jobs['jobs'][0]['steps'][0]['conclusion'] = 'skipped'
                if scenario == 'latest_attempt_changed':
                    records[latest]['run_attempt'] = 3
                if scenario == 'wrong_pr':
                    records[latest]['pull_requests'][0]['number'] = 100
                if scenario == 'duplicate':
                    jobs['jobs'].append(dict(job, id=83))
                    jobs['total_count'] = 2
                if scenario == 'foreign_sha':
                    jobs['jobs'][0]['head_sha'] = '0' * 40
                p = proof if scenario != 'wrong_release' else {**proof, 'trusted_main_audit_run_id': 52}
                line = b'2026-11-14T01:02:03.1234567Z ' + canonical(p)
                with patch.object(actions.r163, 'GitHubReadOnlyREST') as api, \
                        patch.object(actions, '_job_log', return_value=line * (2 if scenario == 'duplicate_log' else 1),
                                     create=True) as log:
                    api.return_value.get_json.side_effect = lambda path: records[path]
                    if scenario == 'good':
                        got = actions._g8(env, release, initial=True, previous=None)
                        self.assertEqual((got['run_id'], got['run_attempt'], got['job_id']), (81, 2, 82))
                        self.assertEqual(got['attestation_sha256'], sha(canonical(proof)))
                        again = actions._g8(env, release, initial=False, previous=got)
                        self.assertEqual(again, got)
                        log.assert_called_once()
                    else:
                        with self.assertRaises(actions.ControlHold):
                            actions._g8(env, release, initial=True, previous=None)

    def test_post_and_apply_reopen_r340_material_and_keep_sources_on_the_post_channel(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from test_rc6_r340_trusted_root_env_controller import authenticated_material_fixture
        from scripts import rc6_ci_release_required_guard as r252
        from test_rc6_r252_trusted_receipt_bridge import S, T

        for operation in ('PREPARE_ENV', 'APPLY_GENESIS'):
            with self.subTest(operation=operation), TemporaryDirectory() as tmp, ExitStack() as cases:
                reader, event, verify_d_e, payload, e_raw, _, _ = authenticated_material_fixture(Path(tmp))
                env = {**ENV, **event, 'RC6_R321_OPERATION': operation, 'RC6_R367_PREPARE_PHASE': 'POST_D_E',
                       'RC6_R367_PIN_GO_ID': '7', 'RC6_R367_INSTALL_GO_ID': '8', 'RC6_R367_ENV_GO_ID': '9',
                       'RC6_R367_PREDEPLOY_RUN_ID': '81', 'RC6_R367_PREDEPLOY_RUN_ATTEMPT': '2'}
                binding = {**BINDING, 'trusted_main_sha': event['GITHUB_SHA'], 'candidate_sha': S, 'candidate_tree': T}
                decisions = {**INITIAL, 'binding': binding, 'proposal_id': PROPOSAL, 'commitment': COMMITMENT,
                             'generation_commitment': 'd' * 64, 'root_manifest_sha256': sha(manifest(binding))}
                cases.enter_context(patch.object(actions.r163, 'GitHubReadOnlyREST', return_value=reader))
                cases.enter_context(patch.object(actions.r340, '_checkout_head', return_value=env['GITHUB_SHA']))
                cases.enter_context(patch.object(r252, 'verify_trusted_release', side_effect=verify_d_e))
                cases.enter_context(patch('scripts.rc6_genesis_custodian_package.build_custodian', return_value=b'zip'))
                api = 'verify_prepare' if operation == 'PREPARE_ENV' else 'verify_apply'
                decisions.update(pin_owner_comment={'user': {}}, install_owner_comment={'user': {}})
                authority = cases.enter_context(patch('scripts.rc6_genesis_control_authority.' + api, return_value=decisions))
                g8 = cases.enter_context(patch.object(actions, '_g8', return_value={'run_id': 34}, create=True))
                cases.enter_context(patch.object(actions, '_oidc_token', return_value='jwt.body.signature'))
                cases.enter_context(patch.object(actions.r302, 'validate_environment'))
                cases.enter_context(patch.object(actions.r362, 'host_identity_sha256',
                                                  return_value=r321.R363_HOST_IDENTITY_SHA256))
                cases.enter_context(patch.object(actions.r309, 'R309BoundedMuxHost'))
                mux = cases.enter_context(patch.object(actions.r309, 'R367CustodianTransport'))
                events, wire = [], []
                def invoke(build, *, recheck_authority):
                    self.assertIs(recheck_authority(), True)
                    raw = build()
                    request = json.loads(raw)
                    events.append(request['step'])
                    wire.append(request)
                    return canonical(host_receipt(request['step'], raw, binding))
                mux.return_value.__enter__.return_value.invoke.side_effect = invoke
                pin = cases.enter_context(patch.object(actions, '_transition_pin',
                                                       side_effect=lambda *args, **kw: events.append('R305')))
                output = cases.enter_context(redirect_stdout(io.StringIO()))
                verb = '--prepare-env' if operation == 'PREPARE_ENV' else '--apply-genesis'
                self.assertEqual(r321.main([verb], env=env), 0, output.getvalue())
                for call in authority.call_args_list:
                    self.assertEqual(call.args[1]['release_e_receipt_raw'], e_raw)
                if operation == 'PREPARE_ENV':
                    self.assertEqual(events, ['POST_D_E'])
                    pin.assert_not_called()
                    transfer = wire[0]['release']
                    import tarfile
                    from scripts import rc6_r326_root_bundle_provenance as r326
                    with tarfile.open(fileobj=io.BytesIO(payload['porota-deploy-bundle-v2.tgz']), mode='r:gz') as tar:
                        self.assertEqual({role: base64.b64decode(data, validate=True)
                                          for role, data in transfer['source_b64'].items()},
                                         {role: tar.extractfile(name).read() for name, role in r326.SOURCE_PATHS.items()})
                    self.assertEqual(base64.b64decode(transfer['postcompletion_receipt_b64']), e_raw)
                    self.assertEqual(transfer['boundary']['candidate_tree'], T)
                    g8.assert_not_called()
                else:
                    self.assertEqual(events, ['SEAL_ENV', 'R305', 'PIN', 'INSTALL'])
                    self.assertTrue(all(row['release'] is None for row in wire))
                    pin.assert_called_once()
                    self.assertEqual(pin.call_args.args[4], manifest(binding))
                    self.assertGreaterEqual(g8.call_count, 4)
                self.assertNotIn('source_b64', output.getvalue())
                self.assertNotIn('authority', output.getvalue())
                self.assertNotIn(PRIVATE, output.getvalue())

    def test_actual_r305_pin_engine_resumes_only_verified_completion(self):
        from tests import test_rc6_r305_root_pin_transition_guard as prior
        context = prior.context()
        comment = prior.go_comment(now=datetime.now(timezone.utc))
        authority = {'binding': {'trusted_main_sha': context.main_sha, 'candidate_sha': context.candidate_sha,
                                 'candidate_tree': context.candidate_tree},
                     'root_manifest_sha256': context.manifest_sha256,
                     'witness_artifact_sha256': context.witness_artifact_sha256, 'pin_go_id': comment['id'],
                     'witness_raw': prior.witness(prior.manifest()), 'pin_owner_comment': comment}
        env = {'RC6_EXPECTED_DO_ID': context.do_id, 'RC6_EXPECTED_DO_REGION': context.do_region}
        release = {'publisher_main_tree_sha': context.main_tree}
        host = prior.FakeRootHost()
        refreshed = []
        def recheck():
            refreshed.append(1)
            return authority
        def run():
            actions._transition_pin(host, env, release, authority, prior.manifest(), recheck=recheck)
        run()
        self.assertGreater(len(refreshed), 2)
        self.assertIn(actions.r305.ROOT_TRANSITION_RECEIPT, host.files)
        host.calls.clear()
        run()
        self.assertTrue(all('/usr/bin/' + verb not in cmd for cmd, *_ in host.calls
                            for verb in ('install ', 'mkdir ', 'ln ', 'mv ', 'rm ')))
        host.files.pop(actions.r305.ROOT_TRANSITION_RECEIPT)
        host.calls.clear()
        with self.assertRaises((actions.ControlHold, actions.r305.PinHold)):
            run()
        self.assertTrue(all('/usr/bin/' + verb not in cmd for cmd, *_ in host.calls
                            for verb in ('install ', 'mkdir ', 'ln ', 'mv ', 'rm ')))

    def test_initial_dispatch_binds_exact_request_to_oidc_and_rechecks_without_reading_de(self):
        requests, audiences = [], []
        def mint(_env, audience):
            audiences.append(audience)
            return 'signed.header.signature'
        def invoke(build, *, recheck_authority):
            self.assertFalse(audiences, 'OIDC must be minted after host preflight and current authority')
            self.assertIs(recheck_authority(), True)
            raw = build()
            requests.append(raw)
            return canonical(host_receipt('INITIAL', raw))
        with ExitStack() as cases:
            release = cases.enter_context(patch.object(actions, '_load_release'))
            check = cases.enter_context(patch('scripts.rc6_genesis_control_authority.verify_prepare',
                                               return_value=INITIAL))
            build = cases.enter_context(patch('scripts.rc6_genesis_custodian_package.build_custodian',
                                               return_value=b'reviewed fixed zipapp'))
            cases.enter_context(patch.object(actions, '_oidc_token', side_effect=mint))
            mux = cases.enter_context(patch.object(actions.r309, 'R367CustodianTransport'))
            mux.return_value.__enter__.return_value.invoke.side_effect = invoke
            output = cases.enter_context(redirect_stdout(io.StringIO()))
            self.assertEqual(r321.main(['--prepare-env'], env=ENV), 0, output.getvalue())
        release.assert_not_called()
        build.assert_called_once_with()
        self.assertEqual([call.args for call in check.call_args_list], [(ENV, None)] * 3)
        package_sha = sha(b'reviewed fixed zipapp')
        mux.assert_called_once_with(ENV, operation='PREPARE_ENV', custodian_sha256=package_sha,
                                    host_identity_sha256=r321.R363_HOST_IDENTITY_SHA256)
        wire = json.loads(requests[0])
        self.assertEqual(wire['step'], 'INITIAL')
        self.assertIsNone(wire['release'])
        self.assertEqual(wire['authority']['custodian_sha256'], package_sha)
        token = wire['authority'].pop('host_identity_token')
        self.assertEqual(audiences, ['urn:porota:r367:' + sha(canonical(wire))])
        public = json.loads(output.getvalue())
        self.assertEqual(public['receipt'], host_receipt('INITIAL', requests[0]))
        for key in ('deploy_authorized', 'host_control_verified', 'runtime_verified', 'real_orders_allowed'):
            self.assertIs(public[key], False)
        for secret in (token, PRIVATE, INITIAL['nonce']):
            self.assertNotIn(secret, output.getvalue())
        self.assertNotIn(PRIVATE.encode(), requests[0])

    def test_initial_authority_verified_by_real_control_reader_reaches_only_fixed_custodian(self):
        from tests import test_rc6_genesis_control_authority as control
        # Reuse GitHub issue/ref fixtures, including its verified INITIAL lease,
        # rather than supplying an already-approved object to dispatch.
        reading = control.ControlTest()
        reading.setUp()
        self.addCleanup(reading.doCleanups)
        observed = []
        def respond(build, *, recheck_authority):
            self.assertIs(recheck_authority(), True)
            raw = build()
            observed.append(json.loads(raw)['authority'])
            return canonical(host_receipt('INITIAL', raw))
        with patch.object(r321, 'R363_HOST_IDENTITY_SHA256', control.HOST), \
                patch('scripts.rc6_genesis_custodian_package.build_custodian', return_value=b'fixture'), \
                patch.object(actions, '_oidc_token', return_value='header.body.sig'), \
                patch.object(actions.r309, 'R367CustodianTransport') as host, redirect_stdout(io.StringIO()) as output:
            host.return_value.__enter__.return_value.invoke.side_effect = respond
            self.assertEqual(r321.main(['--prepare-env'], env=reading.env), 0, output.getvalue())
            self.assertEqual(observed[0]['binding']['prepare_go_id'], control.G1)
            self.assertEqual(observed[0]['public_versions']['1093']['comments'], 232)
            reading.docs['/issues/comments/' + str(control.G1)]['author_association'] = 'MEMBER'
            self.assertEqual(r321.main(['--prepare-env'], env=reading.env), 79)
            self.assertEqual(len(observed), 1)

    def test_missing_prepare_authority_stale_recheck_or_ambiguous_remote_reply_produces_only_hold(self):
        for scenario in ('missing', 'expired', 'lost_reply', 'changed_after_response'):
            with self.subTest(scenario=scenario), ExitStack() as cases:
                calls = []
                def verify(*unused):
                    calls.append(True)
                    if scenario == 'missing' or scenario == 'expired' and len(calls) == 2:
                        raise RuntimeError(PRIVATE)
                    if scenario == 'changed_after_response' and len(calls) == 3:
                        return {**INITIAL, 'nonce': 'd' * 32}
                    return INITIAL
                def invoke(build, *, recheck_authority):
                    recheck_authority()
                    raw = build()
                    if scenario == 'lost_reply':
                        raise RuntimeError(PRIVATE)
                    return canonical(host_receipt('INITIAL', raw))
                cases.enter_context(patch('scripts.rc6_genesis_control_authority.verify_prepare', side_effect=verify))
                cases.enter_context(patch('scripts.rc6_genesis_custodian_package.build_custodian', return_value=b'zip'))
                cases.enter_context(patch.object(actions, '_oidc_token', return_value='jwt.body.signature'))
                mux = cases.enter_context(patch.object(actions.r309, 'R367CustodianTransport'))
                mux.return_value.__enter__.return_value.invoke.side_effect = invoke
                output = cases.enter_context(redirect_stdout(io.StringIO()))
                self.assertEqual(r321.main(['--prepare-env'], env=ENV), 79)
                self.assertEqual(output.getvalue(),
                                 'RC6_R367=HOLD79|physical_state=NO_VERIFICADO|deploy_authorized=false\n')
                if scenario == 'missing':
                    mux.assert_not_called()

    def test_unknown_phase_or_confused_cli_never_reaches_authority_or_host(self):
        for argv, env in ((['--apply-genesis'], ENV),
                          (['--prepare-env'], {**ENV, 'RC6_R367_PREPARE_PHASE': '../unknown'}),
                          (['--prepare-env'], {**ENV, 'RC6_R367_PREPARE_PHASE': 'POST_D_E',
                                               'RC6_R367_PREPARE_GO_ID': ''}),
                          (['--apply-genesis'], {**ENV, 'RC6_R321_OPERATION': 'APPLY_GENESIS'})):
            with self.subTest(argv=argv), patch.object(actions, '_load_release') as release, \
                    patch.object(actions, '_authority') as authority, redirect_stdout(io.StringIO()) as log:
                self.assertEqual(r321.main(argv, env=env), 79)
            release.assert_not_called()
            authority.assert_not_called()
            self.assertNotIn(PRIVATE, log.getvalue())

    def test_prepare_receipt_rejects_wrong_step_request_proposal_or_private_extras(self):
        expected = {**INITIAL, 'proposal_id': PROPOSAL, 'commitment': COMMITMENT}
        raw = b'request bytes'
        original = host_receipt('POST_D_E', raw)
        for changes in ({'step': 'INSTALL'}, {'request_sha256': '0' * 64}, {'commitment': '0' * 64},
                        {'proposal_id': '0' * 32}, {'generation_commitment': 'x'},
                        {'manifest_sha256': '0' * 64}, {'manifest_b64': '***'}, {'private': PRIVATE}):
            with self.subTest(changes=list(changes)), self.assertRaises(actions.ControlHold):
                actions.checked_receipt(canonical({**original, **changes}), step='POST_D_E',
                                        request=raw, authority=expected)
        self.assertEqual(actions.checked_receipt(canonical(original), step='POST_D_E', request=raw,
                                                 authority=expected), original)

    def test_root_manifest_foreign_candidate_and_noncanonical_receipt_are_rejected(self):
        foreign = json.loads(manifest())
        foreign['candidate_sha'] = 'f' * 40
        injected = canonical(foreign)
        raw = b'request'
        original = host_receipt('POST_D_E', raw)
        foreign_receipt = dict(original, manifest_b64=base64.b64encode(injected).decode(),
                               manifest_sha256=sha(injected))
        for wire in (canonical(foreign_receipt), canonical(original) + b'\n',
                     canonical(original).replace(b'"step":', b'"step":"POST_D_E","step":')):
            with self.assertRaises(actions.ControlHold):
                actions.checked_receipt(wire, step='POST_D_E', request=raw,
                                        authority={**INITIAL, 'proposal_id': PROPOSAL, 'commitment': COMMITMENT})

    def test_oidc_bearer_never_sent_to_redirect_or_non_actions_endpoint(self):
        for rogue in ('http://pipelines.actions.githubusercontent.com/_apis/oidc?x=y',
                      'https://pipelines.actions.githubusercontent.com.attacker.example/_apis/oidc',
                      'https://user@pipelines.actions.githubusercontent.com/_apis/oidc',
                      'https://pipelines.actions.githubusercontent.com:8443/_apis/oidc',
                      'https://pipelines.actions.githubusercontent.com/_apis/oidc?audience=previous'):
            with self.subTest(endpoint=rogue), patch('urllib.request.build_opener') as opener, \
                    self.assertRaises(actions.ControlHold):
                actions._oidc_token({'ACTIONS_ID_TOKEN_REQUEST_URL': rogue,
                                     'ACTIONS_ID_TOKEN_REQUEST_TOKEN': 'secret'}, 'urn:porota:r367:' + '3' * 64)
            opener.assert_not_called()

    def test_oidc_fetch_uses_audience_without_redirects_or_bearer_in_url(self):
        class Response(io.BytesIO):
            status = 200
            def geturl(self):
                return requests[0].full_url
        requests = []
        def respond(req, *, timeout):
            requests.append(req)
            self.assertEqual(timeout, 8)
            return Response(b'{"count":1,"value":"a.b.c"}')
        endpoint = 'https://pipelines.actions.githubusercontent.com/pod/_apis/OIDC/12?api-version=2.0'
        env = {'ACTIONS_ID_TOKEN_REQUEST_URL': endpoint, 'ACTIONS_ID_TOKEN_REQUEST_TOKEN': PRIVATE}
        aud = 'urn:porota:r367:' + 'a' * 64
        with patch('urllib.request.build_opener') as opener:
            opener.return_value.open.side_effect = respond
            self.assertEqual(actions._oidc_token(env, aud), 'a.b.c')
        self.assertIsInstance(opener.call_args.args[0], actions.r163._NoRedirect)
        self.assertIsNone(opener.call_args.args[0].redirect_request(requests[0], None, 307, '', {},
                                                                     'https://attacker.example/'))
        self.assertEqual(requests[0].get_header('Authorization'), 'Bearer ' + PRIVATE)
        self.assertNotIn(PRIVATE, requests[0].full_url)
        self.assertEqual(urllib.parse.parse_qs(urllib.parse.urlsplit(requests[0].full_url).query),
                         {'api-version': ['2.0'], 'audience': [aud]})
