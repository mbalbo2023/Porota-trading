"""Dispatch one authenticated R367 GENESIS operation from the trusted R321 runner."""

import base64
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request

from scripts import rc6_r163_trusted_postrun_byte_audit as r163
from scripts import rc6_r163_artifact_byte_verifier as r163_bytes
from scripts import rc6_ci_release_required_guard as r252
from scripts import rc6_r295_host_bootstrap_guard as r295
from scripts import rc6_genesis_launcher_authority as r362
from scripts import rc6_r302_bootstrap_receipt_preflight as r302
from scripts import rc6_r305_root_pin_transition_guard as r305
from scripts import rc6_r309_bounded_mux_host as r309
from scripts import rc6_r340_trusted_root_env_controller as r340


_HEX32 = re.compile(r'[0-9a-f]{32}\Z')
_HEX64 = re.compile(r'[0-9a-f]{64}\Z')
_ID = re.compile(r'[1-9][0-9]{0,19}\Z')
_JWT = re.compile(r'[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\Z')
_SOURCE_KEYS = frozenset(('helper', 'custody', 'cf_intraday_scalping.py', 'co_market_sessions_hf6.py'))


class ControlHold(RuntimeError):
    """A protocol refusal; raw evidence and remote text must stay private."""


def _require(condition):
    if not condition:
        raise ControlHold('GENESIS_CONTROL_UNVERIFIED')


def _canonical(doc):
    return (json.dumps(doc, ensure_ascii=True, sort_keys=True, allow_nan=False,
                       separators=(',', ':')) + '\n').encode('ascii')


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _b64(raw):
    return base64.b64encode(raw).decode('ascii')


def _oidc_token(env, audience):
    """Obtain a GitHub OIDC token bound to the exact unsigned request. No redirects."""
    endpoint = env.get('ACTIONS_ID_TOKEN_REQUEST_URL')
    bearer = env.get('ACTIONS_ID_TOKEN_REQUEST_TOKEN')
    _require(type(endpoint) is str and 0 < len(endpoint) <= 4096 and
             type(bearer) is str and 0 < len(bearer) <= 32768 and bearer.isascii() and
             all('!' <= c <= '~' for c in bearer))
    parts = urllib.parse.urlsplit(endpoint)
    _require(parts.scheme == 'https' and parts.hostname and
             (parts.hostname.endswith('.actions.githubusercontent.com') or
              parts.hostname.endswith('.actions.github.com')) and
             parts.netloc == parts.hostname and not parts.fragment and '/_apis/' in parts.path.lower() and
             not any(key.lower() == 'audience' for key, _ in urllib.parse.parse_qsl(parts.query)))
    query = parts.query + ('&' if parts.query else '') + urllib.parse.urlencode({'audience': audience})
    url = parts._replace(query=query).geturl()
    request = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + bearer,
                                                   'Accept': 'application/json'})
    with urllib.request.build_opener(r163._NoRedirect()).open(request, timeout=8) as response:
        _require(response.status == 200 and response.geturl() == url)
        doc = r295._strict_json(response.read(32769), limit=32768)
    token = doc.get('value') if type(doc) is dict else None
    _require(type(token) is str and len(token) <= 24576 and _JWT.fullmatch(token))
    return token


def _load_release(env):
    """The only source of release bytes is R340's current R252/R163 verification."""
    captured = []
    receipt = r340.verify_authenticated_release(
        env, checkout_sha=r340._checkout_head(), github=r163.GitHubReadOnlyREST(env['GITHUB_TOKEN']),
        trusted_release=r252.verify_trusted_release, include_material=True, consume_material=captured.append)
    _require(len(captured) == 1 and type(captured[0]) is r340.VerifiedInstallMaterial)
    return receipt, captured[0]


def _job_log(github, job_id):
    """Read one Actions job log. The bearer stays on the API origin."""
    route = '/repos/' + r252.REPO + f'/actions/jobs/{job_id}/logs'
    try:
        with github._opener.open(github._api_request(route), timeout=15) as response:
            return github._read(response, limit=2 * 1024**2)
    except urllib.error.HTTPError as redirect:
        location = redirect.headers.get('Location')
        _require(redirect.code == 302 and r163.allowed_storage_redirect(location))
    with github._opener.open(urllib.request.Request(location), timeout=15) as response:
        return github._read(response, limit=2 * 1024**2)


def _g8(env, release, *, initial, previous):
    """Bind the required Predeploy PR job and its R252 proof to R340's exact D/E."""
    run_id, attempt = (r252._positive_decimal(env[key]) for key in
                       ('RC6_R367_PREDEPLOY_RUN_ID', 'RC6_R367_PREDEPLOY_RUN_ATTEMPT'))
    github = r163.GitHubReadOnlyREST(env['GITHUB_TOKEN'])
    root = '/repos/' + r252.REPO + f'/actions/runs/{run_id}'
    pinned = root + f'/attempts/{attempt}'
    candidate = release['candidate_sha']
    for path in (root, pinned):
        run = github.get_json(path)
        prs = run['pull_requests']
        _require(run['id'] == run_id and run['run_attempt'] == attempt and
                 run['head_sha'] == candidate and run['head_branch'] == r252.CANDIDATE_BRANCH and
                 run['event'] == 'pull_request' and run['status'] == 'completed' and run['conclusion'] == 'success' and
                 run['name'] == 'Porota Predeploy V2' and run['path'] == r163_bytes.PREDEPLOY_WORKFLOW and
                 run['repository']['full_name'] == run['head_repository']['full_name'] == r252.REPO and
                 type(prs) is list and len(prs) == 1 and prs[0]['number'] == r340.PR_NUMBER and
                 prs[0]['head']['sha'] == candidate and prs[0]['head']['ref'] == r252.CANDIDATE_BRANCH and
                 prs[0]['base']['ref'] == r340.PRODUCT)
    listing = github.get_json(pinned + '/jobs?per_page=100')
    jobs = listing['jobs']
    _require(type(jobs) is list and 0 < len(jobs) == listing['total_count'] <= 100)
    selected = [job for job in jobs if job['name'] == 'RC6 required release evidence (offhost)']
    _require(len(selected) == 1)
    job = selected[0]
    _require(type(job['id']) is int and job['id'] > 0 and job['run_id'] == run_id and job['run_attempt'] == attempt
             and job['head_sha'] == candidate and job['status'] == 'completed' and job['conclusion'] == 'success')
    steps = [step for step in job['steps'] if step['name'] == 'Verify pinned R252 trusted RELEASE receipt']
    _require(len(steps) == 1 and steps[0]['status'] == 'completed' and steps[0]['conclusion'] == 'success')
    source = release['source_material']
    proof = {'status': 'TRUSTED_RELEASE_BYTES_BOUND', 'mode': 'PR_CANDIDATE', 'candidate_sha': candidate,
             'candidate_tree_sha': release['candidate_tree_sha'], 'product_sha': '',
             'release_run_id': source['release']['run_id'], 'release_run_attempt': source['release']['run_attempt'],
             'release_artifact_id': source['release']['artifact_id'], 'release_artifact_digest': source['release']['artifact_digest'],
             'trusted_main_audit_run_id': source['postcompletion']['run_id'],
             'trusted_main_audit_run_attempt': source['postcompletion']['run_attempt'],
             'artifact_contents_verified_by_this_guard': True, 'owner_go_verified': False,
             'runtime_verified': False, 'automatic_go': False}
    record = {'run_id': run_id, 'run_attempt': attempt, 'job_id': job['id'], 'attestation_sha256': _sha(_canonical(proof))}
    if initial:
        log = _job_log(github, job['id'])
        # Action logs prefix each stdout line with a UTC timestamp. Compare the
        # parsed R252 object with exact scalar types; never export remote logs.
        rows = []
        for line in log.splitlines():
            message = re.sub(rb'^\d{4}-\d{2}-\d{2}T[0-9:.]+Z ', b'', line, count=1)
            if message.startswith(b'{') and b'TRUSTED_RELEASE_BYTES_BOUND' in message:
                rows.append(_canonical(r295._strict_json(message, limit=8192)))
        _require(rows == [_canonical(proof)])
    else:
        # Completed job IDs and attempts identify immutable logs. Reopen the
        # latest run and the successful step at each subsequent authority check.
        _require(record == previous)
    return record


def _authority(env, operation, release, *, initial):
    from scripts import rc6_genesis_control_authority as authority
    if operation == 'PREPARE_ENV':
        return authority.verify_prepare(env, release)
    verified = authority.verify_apply(env, release, initial=initial)
    # Only immutable authority fields go on the wire. Reactions or unrelated
    # REST presentation fields must not invalidate a fix-forward transaction.
    snapshot = dict(verified)
    for key in ('pin_owner_comment', 'install_owner_comment'):
        original = snapshot[key]
        fields = ('id', 'node_id', 'issue_url', 'author_association', 'created_at', 'updated_at', 'body')
        comment = {field: original[field] for field in fields if field in original}
        comment['user'] = {field: original['user'][field] for field in ('id', 'node_id', 'login', 'type')
                           if field in original['user']}
        snapshot[key] = comment
    return snapshot


def _release_wire(receipt, material):
    sources = material.source_bytes
    _require(set(sources) == _SOURCE_KEYS and all(type(raw) is bytes for raw in sources.values()))
    return {'boundary': asdict(material.boundary), 'source_b64': {role: _b64(raw) for role, raw in sources.items()},
            'postcompletion_receipt_b64': _b64(material.postcompletion_receipt_bytes), 'receipt': receipt}


def _signed_request(env, operation, step, snapshot, package_sha, release):
    fields = dict(snapshot)
    witness = fields.pop('witness_raw', None)
    if witness is not None:
        fields['witness_b64'] = _b64(witness)
    fields.update(actor_id=env['GITHUB_ACTOR_ID'], dispatch_run_id=env['GITHUB_RUN_ID'],
                  dispatch_run_attempt=env['GITHUB_RUN_ATTEMPT'], custodian_sha256=package_sha)
    document = {'schema': 'RC6_R367_HOST_REQUEST_V1', 'operation': operation, 'step': step,
                'authority': fields, 'release': release}
    unsigned = _canonical(document)
    _require(len(unsigned) <= 1024**2)
    fields['host_identity_token'] = _oidc_token(env, 'urn:porota:r367:' + _sha(unsigned))
    wire = _canonical(document)
    _require(len(wire) <= 1024**2)
    return wire


def checked_receipt(raw, *, step, request, authority):
    """Project only the correlated, canonical, public host receipt."""
    try:
        _require(type(step) is str and step in ('INITIAL', 'POST_D_E', 'SEAL_ENV', 'PIN', 'INSTALL'))
        doc = r295._strict_json(raw, limit=4096)
        keys = {'schema', 'step', 'request_sha256', 'proposal_id', 'commitment'}
        if step in ('POST_D_E', 'SEAL_ENV'):
            keys.update(('manifest_b64', 'manifest_sha256', 'generation_commitment'))
        _require(type(doc) is dict and set(doc) == keys and _canonical(doc) == raw and
                 doc['schema'] == 'RC6_R367_HOST_RECEIPT_V1' and doc['step'] == step and
                 doc['request_sha256'] == _sha(request) and type(doc['proposal_id']) is str and
                 _HEX32.fullmatch(doc['proposal_id']) and type(doc['commitment']) is str and
                 _HEX64.fullmatch(doc['commitment']))
        if step != 'INITIAL':
            _require(doc['proposal_id'] == authority['proposal_id'] and
                     doc['commitment'] == authority['commitment'])
        if step in ('POST_D_E', 'SEAL_ENV'):
            _require(type(doc['manifest_b64']) is str and type(doc['generation_commitment']) is str and
                     _HEX64.fullmatch(doc['generation_commitment']))
            manifest_raw = base64.b64decode(doc['manifest_b64'], validate=True)
            _require(_b64(manifest_raw) == doc['manifest_b64'])
            manifest = r305.validate_root_manifest(manifest_raw, expected_digest=doc['manifest_sha256'])
            _require(manifest['candidate_sha'] == authority['binding']['candidate_sha'] and
                     manifest['candidate_tree'] == authority['binding']['candidate_tree'])
            if step == 'SEAL_ENV':
                _require(doc['manifest_sha256'] == authority['root_manifest_sha256'] and
                         doc['generation_commitment'] == authority['generation_commitment'])
    except (KeyError, ValueError, TypeError, r295.BootstrapHold, r305.PinHold):
        raise ControlHold('GENESIS_HOST_RECEIPT_UNVERIFIED') from None
    return doc


def checked_promotion_admission(raw, *, request, expected, now):
    """Validate a proposed admission contract; do not authenticate or authorize it.

    The caller must supply independently verified expectations, including the
    request digest, live admission-purpose authority and R340 D/E material.
    Matching a dict is not proof of its provenance. R321 artifact authentication,
    live revocation checks, physical measurements and protected nonce consumption
    remain outside this pure function. Repeated validation grants no authority.
    This function is deliberately not wired into dispatch or Promote.
    """
    from scripts.rc6_genesis_control_authority import R321_PATH

    try:
        bound = {'request_sha256', 'source', 'producer', 'release', 'host', 'authority'}
        flags = {'host_control_verified', 'deploy_authorized', 'runtime_verified', 'real_orders_allowed'}
        _require(type(expected) is dict and set(expected) == bound and
                 type(request) is bytes and 0 < len(request) <= 1024**2)
        doc = r295._strict_json(raw, limit=4096)
        _require(type(doc) is dict and set(doc) == bound | flags | {
            'schema', 'step', 'observed_at', 'expires_at', 'checks'} and _canonical(doc) == raw)
        _require(doc['schema'] == 'RC6_PROMOTION_ADMISSION_V1' and
                 doc['step'] == 'PROMOTION_ADMISSION' and all(doc[key] is False for key in flags))
        # Canonical comparison preserves scalar types: true must not equal 1.
        _require(all(_canonical(doc[key]) == _canonical(expected[key]) for key in bound) and
                 type(doc['request_sha256']) is str and doc['request_sha256'] == _sha(request))

        source, producer, release, host, authority = (doc[key] for key in
                                                       ('source', 'producer', 'release', 'host', 'authority'))
        _require(type(source) is dict and set(source) == {
            'repository', 'trusted_main_sha', 'candidate_sha', 'candidate_tree'} and
            source['repository'] == r252.REPO and all(type(source[key]) is str and
                r295._HEX40.fullmatch(source[key]) for key in ('trusted_main_sha', 'candidate_sha', 'candidate_tree')))
        _require(type(producer) is dict and set(producer) == {
            'workflow', 'ref', 'event', 'run_id', 'run_attempt'} and producer['workflow'] == R321_PATH and
            producer['ref'] == 'refs/heads/main' and producer['event'] == 'workflow_dispatch' and
            all(type(producer[key]) is str and _ID.fullmatch(producer[key]) for key in ('run_id', 'run_attempt')))

        _require(type(release) is dict and set(release) == {'d', 'e', 'image_id', 'e_receipt_sha256'})
        for role in ('d', 'e'):
            record = release[role]
            _require(type(record) is dict and set(record) == {
                'run_id', 'run_attempt', 'artifact_id', 'artifact_digest'} and
                all(type(record[key]) is int and 0 < record[key] < 10**20
                    for key in ('run_id', 'run_attempt', 'artifact_id')) and
                type(record['artifact_digest']) is str and
                re.fullmatch(r'sha256:[0-9a-f]{64}', record['artifact_digest']))
        _require(type(release['image_id']) is str and re.fullmatch(r'sha256:[0-9a-f]{64}', release['image_id']) and
                 type(release['e_receipt_sha256']) is str and _HEX64.fullmatch(release['e_receipt_sha256']))

        host_hashes = {'host_identity_sha256', 'custodian_sha256', 'root_manifest_sha256',
                       'daemon_id_sha256', 'dashboard_env_sha256', 'observer_env_sha256'}
        _require(type(host) is dict and set(host) == host_hashes | {'files'} and
                 all(type(host[key]) is str and _HEX64.fullmatch(host[key]) for key in host_hashes))
        # Reuse the existing manifest semantics and exact bytes/digest check.
        # This verifies consistency with a pinned manifest, not files on a host.
        r305.validate_root_manifest(_canonical({
            'schema': 'RC6_ROOT_INSTALL_MANIFEST_V1', 'candidate_sha': source['candidate_sha'],
            'candidate_tree': source['candidate_tree'], 'paper_mode': 'PRODUCTION_PAPER',
            'real_orders_allowed': False, 'files': host['files']}),
            expected_digest=host['root_manifest_sha256'])

        _require(type(authority) is dict and set(authority) == {
            'purpose', 'lease_id', 'admission_go_id', 'nonce', 'proposal_id', 'commitment',
            'generation_commitment', 'issued_at', 'expires_at', 'approval_expires_at'} and
            authority['purpose'] == 'PROMOTION_ADMISSION' and
            all(type(authority[key]) is int and 0 < authority[key] < 10**20
                for key in ('lease_id', 'admission_go_id')) and
            all(type(authority[key]) is str and _HEX32.fullmatch(authority[key])
                for key in ('nonce', 'proposal_id')) and
            all(type(authority[key]) is str and _HEX64.fullmatch(authority[key])
                for key in ('commitment', 'generation_commitment')))
        _require(type(now) is datetime and now.tzinfo is not None and now.utcoffset() is not None)
        issued, observed, expiry, authority_expiry, approval_expiry = (
            r295._utc_timestamp(value) for value in (authority['issued_at'], doc['observed_at'],
                doc['expires_at'], authority['expires_at'], authority['approval_expires_at']))
        _require(issued <= observed <= now.astimezone(timezone.utc) < expiry <=
                 min(authority_expiry, approval_expiry))

        checks = doc['checks']
        _require(type(checks) is dict and set(checks) == {
            'protected_files', 'approval', 'env_generation', 'daemon', 'control_prerequisites'})
        for result in checks.values():
            _require(type(result) is dict and set(result) == {'result', 'evidence_sha256'} and
                     result['result'] is True and type(result['evidence_sha256']) is str and
                     _HEX64.fullmatch(result['evidence_sha256']))
    except (ControlHold, KeyError, TypeError, ValueError, OverflowError, RecursionError,
            r295.BootstrapHold, r305.PinHold):
        raise ControlHold('PROMOTION_ADMISSION_CONTRACT_UNVERIFIED') from None
    return doc


def _steps(env, verb):
    operation = env.get('RC6_R321_OPERATION')
    if verb == '--prepare-env' and operation == 'PREPARE_ENV':
        phase = env.get('RC6_R367_PREPARE_PHASE')
        _require(phase in ('INITIAL', 'POST_D_E'))
        return (phase,)
    _require(verb == '--apply-genesis' and operation == 'APPLY_GENESIS')
    return ('SEAL_ENV', 'PIN', 'INSTALL')


def _transition_pin(host, env, release, snapshot, manifest_raw, *, recheck):
    """Use R305's exact NEW gate, or verify its already completed receipt without writing."""
    binding = snapshot['binding']
    context = r305.PinContext(binding['trusted_main_sha'], release['publisher_main_tree_sha'],
                              binding['candidate_sha'], binding['candidate_tree'], snapshot['root_manifest_sha256'],
                              snapshot['witness_artifact_sha256'], env['RC6_EXPECTED_DO_ID'], env['RC6_EXPECTED_DO_REGION'])
    manifest = r305.validate_root_manifest(manifest_raw, expected_digest=context.manifest_sha256)
    witness = r305.validate_independent_witness(snapshot['witness_raw'], expected_digest=context.witness_artifact_sha256,
                                                manifest=manifest, context=context)
    go_id = snapshot['pin_go_id']
    def fresh_pin():
        current = recheck()
        return current['pin_owner_comment'], current['binding']['trusted_main_sha'], current['witness_artifact_sha256']

    r305._identity(host, context)
    if r305._exists(host, r305.ROOT_TRANSITION_RECEIPT):
        r305._preflight_dirs(host, require_lock=True)
        r305._bootstrap_receipt(host)
        receipt = r305._transition_receipt(context, original_go_id=go_id, witness=witness)
        r305._stat(host, r305.ROOT_TRANSITION_RECEIPT, mode=0o400, links=1)
        r305._sha_file(host, r305.ROOT_TRANSITION_RECEIPT, _sha(receipt))
        r305._stat(host, r305.ROOT_OLD_ARCHIVE, mode=0o400, links=1)
        r305._sha_file(host, r305.ROOT_OLD_ARCHIVE, _sha(r305._new_pin_bytes(r305.HISTORICAL_D_PIN)))
        _require(not r305._exists(host, r305.ROOT_STAGE) and not r305._exists(host, r305.ROOT_TRANSITION_RECEIPT_STAGE))
        r305._snapshot_pin(host, context.manifest_sha256, links=1)
        fresh_pin()
        return
    # Any partial lock/archive without its completed receipt is rejected by
    # R305 NEW. Its separately authorized RESUME is never synthesized here.
    result = r305.execute_root_pin_transition(host, context, manifest_raw=manifest_raw,
        witness_raw=snapshot['witness_raw'], owner_comment=snapshot['pin_owner_comment'],
        owner_go_id=go_id, recheck_authority=fresh_pin)
    _require(result == {'schema': 'RC6_R305_ROOT_PIN_TRANSITION_FILES_ONLY_V1',
                        'status': 'PIN_TRANSITION_FILES_ONLY_NOT_HOST_GO', 'root_manifest_sha256': context.manifest_sha256,
                        'candidate_sha': context.candidate_sha, 'owner_go_comment_id': go_id,
                        'host_control_verified': False, 'real_orders_allowed': False})


def dispatch(env, verb):
    from scripts import rc6_r321_root_install_actions as r321
    steps = _steps(env, verb)
    r321.validate_dispatch_context(env)
    operation = env['RC6_R321_OPERATION']
    selectors = ('LEASE_ID', 'PREPARE_GO_ID') if operation == 'PREPARE_ENV' else (
        'LEASE_ID', 'PIN_GO_ID', 'INSTALL_GO_ID', 'ENV_GO_ID', 'PREDEPLOY_RUN_ID', 'PREDEPLOY_RUN_ATTEMPT')
    for field in selectors:
        value = env.get('RC6_R367_' + field)
        _require(type(value) is str and _ID.fullmatch(value))
    release = payload = None
    if steps[0] != 'INITIAL':
        receipt, material = _load_release(env)
        release = dict(receipt, release_e_receipt_raw=material.postcompletion_receipt_bytes)
        if steps == ('POST_D_E',):
            payload = _release_wire(receipt, material)
    snapshot = _authority(env, operation, release, initial=True)
    gate = _g8(env, release, initial=True, previous=None) if operation == 'APPLY_GENESIS' else None
    if gate is not None:
        snapshot['g8'] = gate
    binding = snapshot['binding']
    _require(binding['trusted_main_sha'] == env['GITHUB_SHA'] and
             binding['host_identity_sha256'] == r321.R363_HOST_IDENTITY_SHA256)
    if release is not None:
        _require(release['publisher_main_sha'] == binding['trusted_main_sha'] and
                 release['candidate_sha'] == binding['candidate_sha'] and
                 release['candidate_tree_sha'] == binding['candidate_tree'])
    from scripts.rc6_genesis_custodian_package import build_custodian
    package_sha = _sha(build_custodian())

    def fresh_authority():
        # Each call reopens GitHub authority after the previous I/O. A changed
        # lease, GO, witness, release identity or proposal ends this dispatch.
        current = _authority(env, operation, release, initial=False)
        if gate is not None:
            current['g8'] = _g8(env, release, initial=False, previous=gate)
        _require(current == snapshot)
        return current

    def recheck():
        fresh_authority()
        return True

    with r309.R367CustodianTransport(env, operation=operation, custodian_sha256=package_sha,
                                     host_identity_sha256=binding['host_identity_sha256']) as host:
        for step in steps:
            if step == 'PIN':
                # This separate mux uses R305's original fixed command allowlist
                # on the same protected endpoint already checked for UID 1000.
                r302.validate_environment(env)
                _require(r362.host_identity_sha256(env) == r321.R363_HOST_IDENTITY_SHA256)
                manifest_raw = base64.b64decode(observed['manifest_b64'], validate=True)
                with r309.R309BoundedMuxHost(env, operation='R305') as pin_host:
                    _transition_pin(pin_host, env, release, snapshot, manifest_raw, recheck=fresh_authority)
            request = None
            def build_request():
                # R309 calls this after verifying the SSH endpoint, package and
                # live GO. GitHub read latency must not age the short-lived JWT.
                nonlocal request
                _require(request is None)
                request = _signed_request(env, operation, step, snapshot, package_sha, payload)
                return request
            raw = host.invoke(build_request, recheck_authority=recheck)
            _require(request is not None)
            observed = checked_receipt(raw, step=step, request=request, authority=snapshot)
            recheck()
    return {'schema': 'RC6_R367_ACTIONS_RECEIPT_V1', 'operation': operation,
            'run_id': env['GITHUB_RUN_ID'], 'run_attempt': env['GITHUB_RUN_ATTEMPT'],
            'receipt': observed, 'host_control_verified': False, 'runtime_verified': False,
            'deploy_authorized': False, 'real_orders_allowed': False}
