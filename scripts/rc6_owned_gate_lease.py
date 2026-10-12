"""Bounded API-only lease observations around an unchanged native supervisor.

Only the supervisor's progress callback runs during a producer. It creates no
child or thread and reads no Source/event/producer RAW. A control failure raises
into the original TERM2/FIN5 path. Historical replay uses recorded UTC, never a
past lease's validity against the reviewer's current time.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import stat
import time
import urllib.error
import urllib.request

from scripts import rc6_material_pr_admission as admission
from scripts import rc6_controlled_native_child_manager as native

SCHEMA = 'porota.rc6.owned-gate-lease.v1'
SNAPSHOT_SCHEMA = 'porota.rc6.owned-gate-lease-snapshot.v1'
HTTP_BUDGET_SECONDS = 5
G5P_HTTP_BUDGET_SECONDS = 45
API_REFRESH_SECONDS = 60
LOCAL_POLL_SECONDS = 5
MAX_EVIDENCE_BYTES = 16 * 1024**2
MAX_OBJECT_BYTES = 1024**2
OBJECT_PREFIX = 'owner-object-'
SHA = re.compile('[0-9a-f]{64}')
_WRITTEN_OBJECTS = {}
_WRITTEN_CONTROL_BYTES = {}


class LeaseControlFailure(ValueError):
    """Irreversible control RED; the original supervisor closes its own child."""


def require(ok, reason):
    if not ok:
        raise LeaseControlFailure(reason)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def wire(value):
    return admission.wire(value)


def utc():
    return datetime.now(timezone.utc)


def text_stamp(value):
    return value.isoformat().replace('+00:00', 'Z')


def http_budget_for_gate(gate):
    """Keep legacy pulse timing; bound G5P's complete live owner/API walk."""
    return G5P_HTTP_BUDGET_SECONDS if gate in ('G5P-acquire', 'G5P-certify') else HTTP_BUDGET_SECONDS


def _private_write(path, raw):
    path = Path(path)
    details = path.parent.lstat()
    require(stat.S_ISDIR(details.st_mode) and details.st_uid == os.geteuid()
        and stat.S_IMODE(details.st_mode) == 0o700
        and not any(part.is_symlink() for part in (path.parent, *path.parent.parents)),
        'OWNED_LEASE_PRIVATE_CONTROL_ROOT_REQUIRED')
    store_key = (str(path.parent), details.st_dev, details.st_ino, details.st_uid)
    written = _WRITTEN_CONTROL_BYTES.setdefault(store_key, 0)
    require(type(raw) is bytes and 0 < len(raw) and written + len(raw) <= MAX_EVIDENCE_BYTES,
        'OWNED_LEASE_TOTAL_CONTROL_BYTE_BOUND')
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    _WRITTEN_CONTROL_BYTES[store_key] = written + len(raw)


@contextmanager
def _http_budget(deadline, budget_seconds=HTTP_BUDGET_SECONDS):
    """Enforce one elapsed deadline even for DNS/TLS or trickling HTTP bodies."""
    remaining = deadline - time.monotonic()
    require(budget_seconds in (HTTP_BUDGET_SECONDS, G5P_HTTP_BUDGET_SECONDS)
        and 0 < remaining <= budget_seconds, 'OWNED_LEASE_HTTP_TOTAL_BUDGET_EXPIRED')
    previous = signal.getsignal(signal.SIGALRM)
    require(signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0), 'OWNED_LEASE_EXISTING_ALARM_CONFLICT')
    def expired(_number, _frame):
        raise LeaseControlFailure('OWNED_LEASE_HTTP_TOTAL_BUDGET_EXPIRED')
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, remaining)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def api_get(path, deadline):
    token = os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
    require(bool(token), 'OWNED_LEASE_READONLY_TOKEN_REQUIRED')
    require(type(path) is str and (path == '' or path.startswith('/'))
        and '\n' not in path and '\r' not in path, 'OWNED_LEASE_CANONICAL_API_PATH_REQUIRED')
    remaining = deadline - time.monotonic()
    require(0 < remaining <= G5P_HTTP_BUDGET_SECONDS, 'OWNED_LEASE_HTTP_TOTAL_BUDGET_EXPIRED')
    conditional = os.environ.get('RC6_GATE') in ('G5P-acquire', 'G5P-certify')
    key = (hashlib.sha256(token.encode()).digest(), path) if conditional else None
    cached = admission._conditional_cache_get(key) if conditional else None
    headers = {'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28'}
    if cached is not None: headers['If-None-Match'] = cached[0]
    request = urllib.request.Request('https://api.github.com/repos/' + admission.REPO + path,
        headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=remaining) as response:
            status = getattr(response, 'status', 200)
            if status == 304:
                require(cached is not None, 'OWNED_LEASE_304_WITHOUT_VERIFIED_CACHE')
                return admission.document(cached[1])
            require(status == 200, 'OWNED_LEASE_API_HTTP_200_OR_VERIFIED_304_REQUIRED')
            raw = response.read(MAX_OBJECT_BYTES + 1)
            etag = response.headers.get('ETag') if conditional else None
    except urllib.error.HTTPError as error:
        if error.code == 304:
            require(cached is not None, 'OWNED_LEASE_304_WITHOUT_VERIFIED_CACHE')
            return admission.document(cached[1])
        raise
    require(0 < len(raw) <= MAX_OBJECT_BYTES, 'OWNED_LEASE_API_RESPONSE_BYTE_BOUND')
    value = admission.document(raw)
    if conditional and type(etag) is str and re.fullmatch(r'(?:W/)?"[A-Za-z0-9._:/+=-]{1,200}"', etag):
        admission._conditional_cache_put(key, etag, raw)
    return value


def _scope(context, checked, get):
    reader = admission.owned_diagnostic_control_scope if context.get('gate') in admission.DIAGNOSTIC_GATES \
        else admission.owned_gate_control_scope
    return reader(**context, now=checked, get=get)


def _timelines(calls):
    result = {471: [], 473: []}
    for path, value in calls.items():
        for issue in result:
            if path.startswith('/issues/' + str(issue) + '/comments?'):
                require(type(value) is list, 'OWNED_LEASE_TIMELINE_ARRAY_REQUIRED')
                result[issue].extend(value)
    return result


def prove_lease_coverage(timelines, context, start, end, *, workstream=None,
                         authenticated_scope=None):
    """Both immutable same-Source lease unions must cover the complete interval."""
    require(start <= end, 'OWNED_LEASE_TIME_ORDER_INVALID')
    g5p = workstream == 'WS-RC6-G5P-MATERIAL-20261011'
    try:
        documentary = admission.paired_documentary_writer_ids(
            timelines, context['source_sha'], context['owner_session'])
    except ValueError as error:
        raise LeaseControlFailure(str(error)) from error
    covered = {}
    for issue in (471, 473):
        rows = timelines.get(issue)
        require(type(rows) is list and rows, 'OWNED_LEASE_BOTH_ISSUE_TIMELINES_REQUIRED')
        # _scope has already authenticated each still-active disjoint writer,
        # including #491's inherited exact scope and deploy-owner release.
        # A generic foreign row can never mint an exception here.
        owner_scope = (authenticated_scope or {}).get('fresh_ownership', {}).get(str(issue), {})
        disjoint = owner_scope.get('authenticated_disjoint_active_writers', [])
        require(type(disjoint) is list, 'OWNED_LEASE_DISJOINT_SCOPE_REQUIRED')
        documentary_ids=owner_scope.get('authenticated_documentary_writer_ids', [])
        require(type(documentary_ids) is list and documentary_ids==documentary[issue],
            'OWNED_LEASE_DOCUMENTARY_PAIR_SCOPE_REBOUND')
        succession=owner_scope.get('authenticated_sre_successor')
        expected_succession=admission._SRE_SUCCESSION[issue]
        successor_authenticated=(type(succession) is dict
            and succession.get('released_session')==admission._SRE_OLD_OWNER
            and succession.get('successor_session')==admission._SRE_NEW_OWNER
            and succession.get('release_comment_id')==expected_succession[0]
            and succession.get('release_body_sha256')==expected_succession[1]
            and succession.get('successor_comment_id')==expected_succession[2]
            and succession.get('successor_body_sha256')==expected_succession[3])
        allowed = {}
        for item in disjoint:
            require(type(item) is dict, 'OWNED_LEASE_DISJOINT_SCOPE_REBOUND')
            sre_item=(item.get('session')==admission._SRE_OLD_OWNER
                or successor_authenticated and item.get('session')==admission._SRE_NEW_OWNER)
            doc_item=(item.get('session')==admission._DOC_BRIDGE_SESSION
                and item.get('comment_id') in documentary_ids)
            require(type(item) is dict and type(item.get('comment_id')) is int
                and item['comment_id'] > 0 and type(item.get('session')) is str
                and item['session'] != context['owner_session']
                and (sre_item or doc_item)
                and item.get('branch') == ('ops/rc6-sre-deep-20261011' if sre_item
                    else admission._DOC_BRIDGE_BRANCH)
                and type(item.get('paths')) is list and item['paths']
                and all(type(path) is str for path in item['paths'])
                and len(item['paths']) == len(set(item['paths']))
                and (set(item['paths']) <= admission._DISJOINT_SRE_PATHS if sre_item
                    else item['paths']==[admission._DOC_BRIDGE_PATH])
                and item['session'] not in allowed,
                'OWNED_LEASE_DISJOINT_SCOPE_REBOUND')
            allowed[item['session']] = item['comment_id']
        spans = []; states = {}; state_ids = {};succession_seen=set()
        for row in sorted(rows, key=lambda item: (admission.effective_stamp(item), item['id'])):
            if successor_authenticated and row['id'] in (expected_succession[0],expected_succession[2]):
                expected_hash=(expected_succession[1] if row['id']==expected_succession[0]
                               else expected_succession[3])
                require(hashlib.sha256(row.get('body','').encode()).hexdigest()==expected_hash,
                    'OWNED_LEASE_SRE_SUCCESSION_BYTES_REBOUND')
                succession_seen.add(row['id'])
            values = admission.fields(row.get('body', ''))
            # G5P approval and launch records repeat the owner and expiry for
            # binding, but only a SESSION owner receipt can grant lease time.
            if g5p and values.get('SESSION') is None and (
                    values.get('CONTRACT_ACCEPTANCE_SCHEMA') == 'porota.rc6.contract-revision-acceptance.v2'
                    or values.get('AUTHORIZATION_SCHEMA') == 'porota.rc6.candidate-launch.v1'):
                require(row['created_at'] == row['updated_at']
                    and row['user']['login'] == 'mbalbo2023'
                    and row['issue_url'].endswith('/issues/' + str(issue)),
                    'OWNED_LEASE_IMMUTABLE_SCOPE_OR_MODE_REQUIRED')
                continue
            owners = [values[key] for key in ('WRITE_OWNER', 'INTEGRATION_OWNER') if key in values
                and values[key] not in ('RELEASED', 'NOT_ACQUIRED', 'NONE', 'null')]
            if values.get('RELEASED') == 'true' and not owners and values.get('WRITE_OWNER') == 'RELEASED':
                # Several authenticated historical releases use the bare
                # RELEASED literal. It revokes that session's spans; it must
                # also update the replayed state instead of leaving a false
                # active foreign writer behind.
                released = values.get('SESSION') or values.get('SESSION_SUCCESSOR')
                prior = states.get(released)
                require(type(released) is str and prior is not None
                    and values.get('WORKSTREAM_ID') == prior.get('WORKSTREAM_ID'),
                    'OWNED_LEASE_AUTHENTIC_PRIOR_OWN_RELEASE_REQUIRED')
                owners = [released]
            foreign = any(owner not in (context['owner_session'], 'RELEASED', 'NOT_ACQUIRED', 'NONE', 'null')
                for owner in owners)
            effective = admission.effective_stamp(row)
            if effective <= end and owners:
                require(row['created_at'] == row['updated_at'] and row['user']['login'] == 'mbalbo2023'
                    and row['issue_url'].endswith('/issues/' + str(issue)),
                    'OWNED_LEASE_IMMUTABLE_SCOPE_OR_MODE_REQUIRED')
                if values.get('RELEASED') == 'true':
                    require(set(owners) == {values.get('SESSION') or values.get('SESSION_SUCCESSOR')},
                        'OWNED_LEASE_AUTHENTIC_OWN_SESSION_RELEASE_REQUIRED')
                    require(effective < start or context['owner_session'] not in owners,
                        'OWNED_LEASE_OWNER_RELEASE_OVERLAPS_PRODUCER')
                if foreign and effective >= start and values.get('RELEASED') != 'true':
                    require(all(identifier in allowed and
                        (row['id'] in documentary_ids and admission.disjoint_documentary_writer(
                            values, context['source_sha'], context['owner_session'])
                        or row['id'] == allowed[identifier] or admission.disjoint_sre_writer(
                            values, context['source_sha'], context['owner_session'],
                            successor_authenticated=successor_authenticated))
                        for identifier in owners if identifier != context['owner_session']),
                        'OWNED_LEASE_FOREIGN_RECORD_OVERLAPS_PRODUCER')
                if context['owner_session'] in owners and effective >= start:
                    require(values.get('WRITE_OWNER') == values.get('INTEGRATION_OWNER')
                            == values.get('SESSION_SUCCESSOR', values.get('SESSION')) == context['owner_session']
                        and values.get('SOURCE_SHA') == context['source_sha']
                        and values.get('SOURCE_TREE') == context['source_tree']
                        and values.get('DEPLOY_OWNER') == 'NOT_ACQUIRED' and values.get('RELEASED') == 'false'
                        and values.get('MODE') == 'PRODUCTION_PAPER / SIMULATION'
                        and values.get('real_orders_sent') == '0',
                        'OWNED_LEASE_CURRENT_SOURCE_OR_OWNER_CHANGED_DURING_PRODUCER')
                for identifier in owners:
                    prior = states.get(identifier)
                    if values.get('RELEASED') == 'true' and prior is not None:
                        require(values.get('WORKSTREAM_ID') == prior.get('WORKSTREAM_ID'),
                            'OWNED_LEASE_EXPLICIT_RELEASE_WORKSTREAM_REBOUND')
                    states[identifier] = values
                    state_ids[identifier] = row['id']
                if values.get('RELEASED') == 'true' and context['owner_session'] in owners:
                    require(values.get('WRITE_OWNER') == values.get('INTEGRATION_OWNER')
                            == values.get('SESSION_SUCCESSOR', values.get('SESSION')) == context['owner_session']
                        and values.get('WORKSTREAM_ID') == (workstream or admission.WORKSTREAM)
                        and values.get('DEPLOY_OWNER') == 'NOT_ACQUIRED'
                        and values.get('MODE') == 'PRODUCTION_PAPER / SIMULATION'
                        and values.get('real_orders_sent') == '0'
                        and re.fullmatch('[0-9a-f]{40}', values.get('SOURCE_SHA', ''))
                        and re.fullmatch('[0-9a-f]{40}', values.get('SOURCE_TREE', '')),
                        'OWNED_LEASE_AUTHENTIC_PRIOR_OWN_RELEASE_REQUIRED')
                    if values.get('SOURCE_LEASE_EXPIRES_UTC') is not None:
                        admission.stamp(values['SOURCE_LEASE_EXPIRES_UTC'])
                    # A pre-interval release revokes all prior own lease spans.
                    # It grants none, even when it quotes an unexpired lease.
                    # Coverage must come from a later explicit reacquisition.
                    if context['owner_session'] in owners:spans.clear()
                    continue
            if values.get('SOURCE_LEASE_EXPIRES_UTC') is None or values.get('WRITE_OWNER') != context['owner_session']:
                continue
            if values.get('SOURCE_SHA') != context['source_sha'] or values.get('SOURCE_TREE') != context['source_tree']:
                continue
            if g5p:
                require(values.get('SESSION') == context['owner_session'],
                    'G5P_LEASE_ONLY_EXPLICIT_OWNER_RECEIPT')
            require(row['created_at'] == row['updated_at'] and row['user']['login'] == 'mbalbo2023'
                and row['issue_url'].endswith('/issues/' + str(issue))
                and values.get('WORKSTREAM_ID') == (workstream or admission.WORKSTREAM)
                and values.get('SESSION_SUCCESSOR', values.get('SESSION')) == context['owner_session']
                and values.get('INTEGRATION_OWNER') == context['owner_session']
                and values.get('DEPLOY_OWNER') == 'NOT_ACQUIRED' and values.get('RELEASED') == 'false'
                and values.get('MODE') == 'PRODUCTION_PAPER / SIMULATION' and values.get('real_orders_sent') == '0',
                'OWNED_LEASE_IMMUTABLE_SCOPE_OR_MODE_REQUIRED')
            begin, expiry = admission.stamp(row['created_at']), admission.stamp(values['SOURCE_LEASE_EXPIRES_UTC'])
            if not timedelta(0) < expiry - begin <= timedelta(minutes=20):
                # An invalid prelaunch receipt supplies no coverage. A later
                # valid, explicit receipt must cover the complete interval.
                # Invalid receipts issued during the run remain a hard veto.
                if g5p and begin < start:
                    continue
                require(False, 'OWNED_LEASE_UNBOUNDED_INTERVAL')
            if begin <= end:
                spans.append((begin, expiry, row['id']))
        if successor_authenticated:
            require(succession_seen=={expected_succession[0],expected_succession[2]},
                'OWNED_LEASE_SRE_SUCCESSION_TIMELINE_REQUIRED')
        require(all(identifier == context['owner_session'] or values.get('RELEASED') == 'true'
            or allowed.get(identifier) == state_ids.get(identifier)
            for identifier, values in states.items()), 'OWNED_LEASE_FOREIGN_RECORD_OVERLAPS_PRODUCER')
        cursor, identifiers = start, []
        for begin, expiry, identifier in sorted(spans):
            if expiry <= cursor:
                continue
            if begin > cursor:
                break
            cursor = expiry
            identifiers.append(identifier)
        require(cursor > end and identifiers, 'OWNED_LEASE_CONTINUITY_GAP')
        covered[str(issue)] = {'covered_until_utc': text_stamp(cursor), 'comment_ids': identifiers}
        if allowed:
            covered[str(issue)]['authenticated_disjoint_writer_ids'] = allowed
    return covered


class GateLeaseMonitor:
    def __init__(self, root, *, source_sha, source_tree, owner_session, gate, launch_receipt_url,
                 run_id=None, run_attempt=None, get=None, clock=None, monotonic=None, diagnostic_binding=None,
                 g5a_namespace_receipt=None, g5a_revision=None):
        self.root = Path(root)
        identity = self.root.lstat()
        self.store_key = (str(self.root), identity.st_dev, identity.st_ino, identity.st_uid)
        self.context = {'source_sha': source_sha, 'source_tree': source_tree, 'owner_session': owner_session,
            'gate': gate, 'launch_receipt_url': launch_receipt_url,
            'run_id': int(os.environ['GITHUB_RUN_ID']) if run_id is None else run_id,
            'run_attempt': int(os.environ['GITHUB_RUN_ATTEMPT']) if run_attempt is None else run_attempt}
        self.http_budget_seconds = http_budget_for_gate(gate)
        require(re.fullmatch('[0-9a-f]{40}', source_sha) and re.fullmatch('[0-9a-f]{40}', source_tree)
            and isinstance(owner_session, str) and re.fullmatch(r'CODEX_[A-Z0-9_]{1,159}', owner_session)
            and gate in (*admission.GATES, *admission.DIAGNOSTIC_GATES)
            and self.context['run_attempt'] == 1 and type(self.context['run_attempt']) is int,
            'OWNED_LEASE_EXACT_FIRST_SOURCE_SCOPE_REQUIRED')
        if gate in admission.DIAGNOSTIC_GATES:
            require(type(diagnostic_binding) is dict and (owner_session == admission.SUCCESSOR_OWNER
                or diagnostic_binding.get('authorization_schema') == 'porota.rc6.capacity-diagnostic-launch.v2'),
                'OWNED_DIAGNOSTIC_FULL_ADMISSION_PINS_REQUIRED')
            self.context['diagnostic_binding'] = diagnostic_binding
        else:
            require(diagnostic_binding is None, 'OWNED_LEASE_DIAGNOSTIC_PINS_OUTSIDE_DIAGNOSTIC_SCOPE')
        self.get, self.clock, self.monotonic = get or api_get, clock or utc, monotonic or time.monotonic
        self.objects, self.object_bytes, self.events = {}, 0, []
        self.last_scope, self.last_calls, self.last_observation = None, None, None
        self.started, self.pid, self.command_label, self.stop_reason = None, None, None, None
        self.launch_intent, self.native_deadline, self.previous_clock = None, None, None
        self.last_local_poll, self.last_api_poll, self.final_path = None, None, None
        self.seen_owner_versions = {}
        self.g5a_namespace_receipt = g5a_namespace_receipt
        self.g5a_revision = g5a_revision
        require((g5a_namespace_receipt is None) == (g5a_revision is None),
                'OWNED_G5A_NAMESPACE_AND_TYPED_DECISION_BOTH_REQUIRED')
        if g5a_namespace_receipt is not None:
            from scripts import rc6_contract_revisions as revisions
            from scripts import rc6_g5_contract as g5
            revisions.validate_revision_approval(g5a_revision, revision=g5.REVISION, scope=g5.SCOPE,
                source_sha=source_sha, source_tree=source_tree, contract_sha256=g5.CONTRACT_SHA256,
                now=self.clock())
            binding = g5a_namespace_receipt.get('binding', {})
            require(gate == 'Horizon' and binding.get('candidate_sha') == source_sha
                and binding.get('candidate_tree') == source_tree and binding.get('owner_id') == owner_session
                and binding.get('producer') == 'Horizon-private-original-cas'
                and g5a_namespace_receipt.get('cleanup_authority_granted') is False
                and re.fullmatch('[0-9a-f]{32}', str(g5a_namespace_receipt.get('namespace_nonce', '')))
                and self.root == Path(g5a_namespace_receipt['path']) / 'g5a-owner-monitor',
                'OWNED_G5A_EXACT_NAMESPACE_MONITOR_BINDING_REQUIRED')

    def _store(self, value):
        raw = wire(value)
        require(0 < len(raw) <= MAX_OBJECT_BYTES, 'OWNED_LEASE_OBJECT_BYTE_BOUND')
        identifier = digest(raw)
        if identifier not in self.objects:
            require(self.object_bytes + len(raw) <= MAX_EVIDENCE_BYTES, 'OWNED_LEASE_EVIDENCE_BYTE_BOUND')
            path = self.root / (OBJECT_PREFIX + identifier + '.json')
            written = _WRITTEN_OBJECTS.setdefault(self.store_key, {})
            if identifier not in written:
                require(sum(size for size, _ in written.values()) + len(raw) <= MAX_EVIDENCE_BYTES,
                    'OWNED_LEASE_SESSION_EVIDENCE_BYTE_BOUND')
                _private_write(path, raw)
                details = path.lstat()
                written[identifier] = len(raw), tuple(getattr(details, key) for key in admission.STAT11)
            else:
                details = path.lstat()
                require(written[identifier] == (len(raw), tuple(getattr(details, key) for key in admission.STAT11)),
                    'OWNED_LEASE_DEDUP_CONTROL_OBJECT_CHANGED')
            self.objects[identifier] = raw
            self.object_bytes += len(raw)
        return identifier

    def _response(self, value):
        if type(value) is list:
            members = [self._store({'kind': 'json', 'value': item}) for item in value]
            return self._store({'kind': 'array', 'members': members, 'semantic_sha256': digest(wire(value))})
        return self._store({'kind': 'json', 'value': value})

    def _failure(self, error):
        if self.stop_reason is None:
            reason = str(error).partition(':')[0]
            if re.fullmatch('[A-Z][A-Z0-9_]{0,191}', reason):
                self.stop_reason = reason
            elif isinstance(error, urllib.error.HTTPError) and 100 <= error.code <= 599:
                self.stop_reason = 'OWNED_LEASE_HTTP_STATUS_' + str(error.code)
            elif isinstance(error, TimeoutError) or (isinstance(error, urllib.error.URLError)
                    and isinstance(error.reason, TimeoutError)):
                self.stop_reason = 'OWNED_LEASE_HTTP_TIMEOUT'
            elif isinstance(error, urllib.error.URLError):
                self.stop_reason = 'OWNED_LEASE_HTTP_TRANSPORT_FAILURE'
            else:
                self.stop_reason = 'OWNED_LEASE_CONTROL_UNAVAILABLE'
            if self.command_label is not None:
                # Preserve only a sanitized classification after control RED;
                # a URL, header, token or response body is never copied.
                safe_http_headers = {}
                if isinstance(error, urllib.error.HTTPError):
                    for header, key in (('X-RateLimit-Remaining', 'rate_limit_remaining'),
                                        ('X-RateLimit-Reset', 'rate_limit_reset_epoch'),
                                        ('Retry-After', 'retry_after_seconds')):
                        raw = error.headers.get(header) if error.headers is not None else None
                        if raw is not None and re.fullmatch(r'[0-9]{1,12}', raw):
                            safe_http_headers[key] = int(raw)
                detail = {'schema': 'porota.rc6.owner-control-first-error.v1',
                    'command_label': self.command_label, 'reason': self.stop_reason,
                    'exception_class': type(error).__name__,
                    'http_status': error.code if isinstance(error, urllib.error.HTTPError) else None,
                    'http_limit_counters': safe_http_headers,
                    'transport_cause_class': (type(error.reason).__name__
                        if isinstance(error, urllib.error.URLError) else None),
                    'qualification_claimed': False, 'real_orders_sent': 0}
                try:
                    _private_write(self.root / ('owner-' + self.command_label + '.first-error.json'), wire(detail))
                except (OSError, LeaseControlFailure):
                    pass
        return LeaseControlFailure(self.stop_reason)

    def _local(self):
        now, monotonic_now = self.clock(), self.monotonic()
        if self.previous_clock is not None:
            prior_wall, prior_mono = self.previous_clock
            require(now >= prior_wall and monotonic_now >= prior_mono
                and abs((now - prior_wall).total_seconds() - (monotonic_now - prior_mono)) <= 2,
                'OWNED_LEASE_CLOCK_DISCONTINUITY')
        self.previous_clock = now, monotonic_now
        if self.last_scope is not None:
            require(all(now < admission.stamp(record['lease_expires_utc'])
                for record in self.last_scope['fresh_ownership'].values()), 'OWNED_LEASE_EXPIRED_DURING_PRODUCER')
        if self.native_deadline is not None:
            require(monotonic_now < self.native_deadline, 'OWNED_LEASE_NATIVE_MANAGEMENT_DEADLINE')
        return now, monotonic_now

    def _observe(self, stage):
        budget_entered = time.monotonic()
        started, entered = self._local()
        allowance = self.http_budget_seconds
        if self.last_scope is not None:
            allowance = min(allowance, *( (admission.stamp(record['lease_expires_utc']) - started).total_seconds()
                for record in self.last_scope['fresh_ownership'].values()))
        if self.native_deadline is not None:
            allowance = min(allowance, self.native_deadline - entered)
        require(0 < allowance <= self.http_budget_seconds, 'OWNED_LEASE_HTTP_TOTAL_BUDGET_EXPIRED')
        # Capture the real budget origin before UTC/clock reads. Adding the
        # allowance to a later clock could run beyond an expiring lease or the
        # unchanged native deadline by the time already spent in this method.
        deadline = budget_entered + allowance
        calls = {}
        def record_get(path):
            if path not in calls:
                require(time.monotonic() < deadline, 'OWNED_LEASE_HTTP_TOTAL_BUDGET_EXPIRED')
                calls[path] = self.get(path, deadline)
            return calls[path]
        with _http_budget(deadline, self.http_budget_seconds):
            if self.g5a_revision is not None:
                from scripts import rc6_owned_g5a_continuation as continuation
                continuation.validate_decision_documents(self.g5a_revision, record_get)
            # The first pass may only be more conservative than its actual
            # completion. The second pass replays exactly the captured API
            # documents at actual completed UTC; it performs no HTTP/Source IO.
            _scope(self.context, started + timedelta(seconds=allowance), record_get)
            finished = self.clock()
            scope = _scope(self.context, finished, lambda path: calls[path])
            if self.g5a_revision is not None:
                continuation.reject_revocations(self.g5a_revision, _timelines(calls))
            versions = {}
            for issue, rows in _timelines(calls).items():
                for row in rows:
                    values = admission.fields(row.get('body', ''))
                    if any(key in values for key in ('WRITE_OWNER', 'INTEGRATION_OWNER', 'DEPLOY_OWNER',
                                                     'SOURCE_LEASE_EXPIRES_UTC', 'RELEASED')):
                        versions[(issue, row['id'])] = digest(wire({key: row[key] for key in
                            ('id', 'user', 'issue_url', 'created_at', 'updated_at', 'body')}))
            require(all(versions.get(key) == version for key, version in self.seen_owner_versions.items()),
                'OWNED_LEASE_CONTROL_COMMENT_DELETED_OR_EDITED')
            reference = {path: self._response(value) for path, value in sorted(calls.items())}
            proof = prove_lease_coverage(_timelines(calls), self.context,
                self.launch_intent or finished, finished, workstream=scope.get('workstream_id'),
                authenticated_scope=scope)
            snapshot = {'schema': SNAPSHOT_SCHEMA, 'stage': stage, 'context': self.context,
                'started_utc': text_stamp(started), 'read_utc': text_stamp(finished),
                'http_total_budget_seconds': self.http_budget_seconds,
                'elapsed_seconds': self.monotonic() - entered, 'api_response_objects': reference,
                'scope_sha256': digest(wire(scope)),
                'latest_owners': {issue: {'comment_id': record['comment_id'],
                    'lease_expires_utc': record['lease_expires_utc']}
                    for issue, record in scope['fresh_ownership'].items()}, 'lease_coverage': proof}
            require(0 <= snapshot['elapsed_seconds'] <= self.http_budget_seconds,
                'OWNED_LEASE_HTTP_TOTAL_BUDGET_EXPIRED')
            identifier = self._store({'kind': 'snapshot', 'value': snapshot})
        self.events.append(identifier)
        self.last_scope, self.last_calls, self.last_observation = scope, calls, identifier
        self.seen_owner_versions.update(versions)
        self.last_api_poll = self.monotonic()
        self.previous_clock = finished, self.last_api_poll
        if self.g5a_namespace_receipt is not None and stage in ('prelaunch', 'poll'):
            from scripts import rc6_owned_g5a_continuation as continuation
            sequence = len(self.events)
            require(sequence <= continuation.MAX_PULSES, 'OWNED_G5A_FINITE_PULSE_BOUND')
            pulse = {'schema': continuation.SCHEMA, 'sequence': sequence, 'snapshot': identifier,
                'binding': self.g5a_namespace_receipt['binding'],
                'namespace_nonce': self.g5a_namespace_receipt['namespace_nonce'],
                'new_launch_authorized': False, 'G5_GREEN': False}
            _private_write(self.root / (continuation.PREFIX + str(sequence).zfill(4) + '.json'), wire(pulse))
        return scope

    def prelaunch(self, label):
        require(self.command_label is None and re.fullmatch('[a-zA-Z0-9_.-]+', label),
            'OWNED_LEASE_FRESH_COMMAND_MONITOR_REQUIRED')
        self.command_label = label
        try:
            self._observe('prelaunch')
            self.launch_intent = self.clock()
            self._local()
            return self.last_scope
        except BaseException as error:
            raise self._failure(error) from error

    def progress(self, stage, pid, entered, deadline, _fd):
        try:
            require(self.command_label is not None and stage in ('started', 'poll')
                and type(pid) is int and pid > 0 and type(deadline) in (int, float)
                and math.isfinite(deadline), 'OWNED_LEASE_ACTUAL_NATIVE_CALLBACK_REQUIRED')
            if stage == 'started':
                require(self.started is None, 'OWNED_LEASE_NATIVE_STARTED_TWICE')
                self.pid, self.started, self.native_deadline = pid, self.clock(), deadline
            require(self.pid == pid and self.native_deadline == deadline,
                'OWNED_LEASE_NATIVE_PID_OR_DEADLINE_REBOUND')
            now, monotonic_now = self._local()
            require(self.last_api_poll is not None and monotonic_now - self.last_api_poll <= API_REFRESH_SECONDS,
                'OWNED_LEASE_CONTROL_OBSERVATION_STALE')
            if monotonic_now - self.last_api_poll >= API_REFRESH_SECONDS - LOCAL_POLL_SECONDS:
                self._observe('poll')
            self.last_local_poll = self.monotonic()
        except BaseException as error:
            raise self._failure(error) from error

    def after_fin(self, kernel, *, readmit=None):
        """FIN remains original; post-FIN admission can never reset an earlier RED."""
        require(native.managed_custody_closed(kernel), 'OWNED_LEASE_FIN_UNKNOWN_PAYLOAD_VETO')
        closed = self.clock()
        require(kernel['pid'] == self.pid and self.started is not None,
            'OWNED_LEASE_ACTUAL_FIN_PID_REBOUND')
        # The original manager's own deadline is meaningful only while live.
        self.native_deadline = None
        observed, readmitted = False, readmit is None
        try:
            self._observe('post-fin')
            observed = True
        except BaseException as error:
            self._failure(error)
        # A control STOP must still attempt full Source admission after FIN.
        # Its failure cannot prevent the issuer's separate loop/RAW finalizer.
        if readmit is not None:
            try:
                readmit()
                readmitted = True
            except BaseException as error:
                self._failure(error)
        revalidated = observed and readmitted
        proof = None
        try:
            proof = prove_lease_coverage(_timelines(self.last_calls or {}), self.context,
                self.launch_intent, closed, workstream=(self.last_scope or {}).get('workstream_id'),
                authenticated_scope=self.last_scope)
        except BaseException as error:
            self._failure(error)
        receipt = {'schema': SCHEMA, 'context': self.context, 'command_label': self.command_label,
            'status': 'GREEN' if self.stop_reason is None and revalidated and native.managed_phase_green(kernel) else 'RED',
            'launch_intent_utc': text_stamp(self.launch_intent), 'native_started_utc': text_stamp(self.started),
            'native_fin_utc': text_stamp(closed), 'actual_pid': self.pid,
            'kernel_sha256': digest(wire(kernel)), 'original_manager_sha256':
                '55325b3108e175a42b87ebe544fd307fa45ffd29b6f7ab471443803ad9ba53b8',
            'physical_fin_closed': True, 'post_fin_readmission_verified': revalidated,
            'lease_coverage': proof, 'stop_reason': self.stop_reason, 'snapshots': self.events,
            'object_count': len(self.objects), 'deduplicated_object_bytes': self.object_bytes,
            'http_total_budget_seconds': self.http_budget_seconds, 'api_refresh_seconds': API_REFRESH_SECONDS,
            'local_poll_seconds': LOCAL_POLL_SECONDS, 'real_orders_sent': 0,
            'qualification_claimed': False, 'continuous_unobserved_GitHub_state_claimed': False}
        raw = wire(receipt)
        require(self.object_bytes + len(raw) <= MAX_EVIDENCE_BYTES, 'OWNED_LEASE_EVIDENCE_BYTE_BOUND')
        self.final_path = self.root / ('owner-' + self.command_label + '.json')
        _private_write(self.final_path, raw)
        return receipt


def _require_issuer_actor():
    require(os.getuid() == os.geteuid() > 0, 'OWNED_DIAGNOSTIC_NONROOT_ISSUER_REQUIRED')


def _read_private_control(path):
    path = Path(path)
    require(path.is_absolute() and not any(item.is_symlink() for item in (path, *path.parents)),
        'OWNED_DIAGNOSTIC_CONTROL_ALIAS_FORBIDDEN')
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NOATIME | os.O_CLOEXEC)
    try:
        before = os.fstat(descriptor)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == os.geteuid() and before.st_nlink == 1
            and stat.S_IMODE(before.st_mode) == 0o600 and 0 < before.st_size <= MAX_OBJECT_BYTES,
            'OWNED_DIAGNOSTIC_PRIVATE_CONTROL_REQUIRED')
        chunks, size = [], 0
        while part := os.read(descriptor, min(65536, MAX_OBJECT_BYTES + 1 - size)):
            chunks.append(part)
            size += len(part)
            require(size <= MAX_OBJECT_BYTES, 'OWNED_DIAGNOSTIC_CONTROL_BYTE_BOUND')
        after = os.fstat(descriptor)
        require(len(b''.join(chunks)) == before.st_size
            and tuple(getattr(before, key) for key in admission.STAT11)
            == tuple(getattr(after, key) for key in admission.STAT11)
            == tuple(getattr(path.lstat(), key) for key in admission.STAT11),
            'OWNED_DIAGNOSTIC_CONTROL_ALL11_CHANGED')
        return b''.join(chunks)
    finally:
        os.close(descriptor)


class DiagnosticIssuerMonitor:
    """API token and callback stay in the NONROOT issuer, outside the worker.

    The calibrator calls after_fin even for a control RED, then completes its
    independently authenticated loop/RAW custody. This class never unmounts,
    deletes a namespace or reports an inner FIN/cleanup on the caller's behalf.
    """
    def __init__(self, args, *, control_root):
        _require_issuer_actor()
        literal = os.environ.get('RC6_CALIBRATION_ADMISSION_JSON')
        require(type(literal) is str and literal, 'OWNED_DIAGNOSTIC_ISSUER_ADMISSION_REQUIRED')
        self.args, self.control_root = args, Path(control_root)
        self.previous = admission.document(_read_private_control(Path(literal)))
        require(self.previous.get('source_sha') == args.source_sha
            and self.previous.get('source_tree') == args.source_tree
            and self.previous.get('gate') == ('capacity-probe' if args.mode == 'capability' else 'capacity-calibration')
            and (self.previous.get('owner_session') == admission.SUCCESSOR_OWNER
                or self.previous.get('authorization_schema') == 'porota.rc6.capacity-diagnostic-launch.v2')
            and self.previous.get('status') == 'ADMITTED_DIAGNOSTIC_NOT_STARTED'
            and self.previous.get('qualification_claimed') is False,
            'OWNED_DIAGNOSTIC_ISSUER_EXACT_ADMISSION_REQUIRED')
        require(Path(args.source_root).absolute() == admission.ROOT,
            'OWNED_DIAGNOSTIC_FULL_ADMISSION_ACTUAL_SOURCE_ROOT_REQUIRED')
        require(self.control_root == Path(args.output) / 'owned-lease-controls'
            and self.control_root.is_absolute()
            and not any(item.is_symlink() for item in (self.control_root, *self.control_root.parents))
            and not os.path.lexists(self.control_root), 'OWNED_DIAGNOSTIC_FRESH_ISSUER_CONTROL_ROOT_REQUIRED')
        self.control_root.mkdir(mode=0o700)
        self.pins = admission.diagnostic_control_binding(self.previous)
        self.receipt, self.physical_fin_closed = None, False
        self._full_admit('initial')
        context = {key: self.previous[key] for key in
            ('source_sha', 'source_tree', 'owner_session', 'gate', 'launch_receipt_url')}
        self.monitor = GateLeaseMonitor(self.control_root, **context, diagnostic_binding=self.pins)

    def _full_admit(self, stage):
        context = {key: self.previous[key] for key in
            ('source_sha', 'source_tree', 'launch_receipt_url', 'owner_session', 'gate')}
        if stage == 'post-fin' and self.previous.get('authorization_schema') == \
                'porota.rc6.capacity-diagnostic-launch.v2':
            require(self.physical_fin_closed is True, 'OWNED_DIAGNOSTIC_CAPTURE_ONLY_REQUIRES_ACTUAL_FIN')
            context['native_finished'] = True
        fresh = admission.admit_diagnostic(**context)
        require(admission.diagnostic_control_binding(fresh) == self.pins
            and fresh.get('source', {}).get('repository_public_verified') is True
            and fresh.get('source', {}).get('anonymous_source_origin') == 'https://github.com/' + admission.REPO + '.git',
            'OWNED_DIAGNOSTIC_SOURCE_OR_AUTHORITY_REBOUND')
        _private_write(self.control_root / (stage + '-full-admission.json'), wire(fresh))
        return fresh

    def before_launch(self, label):
        self._full_admit('prelaunch')
        return self.monitor.prelaunch(label)

    def progress(self, stage, pid, entered, deadline, fd):
        return self.monitor.progress(stage, pid, entered, deadline, fd)

    __call__ = progress

    def after_fin(self, kernel):
        require(native.managed_custody_closed(kernel), 'OWNED_DIAGNOSTIC_ISSUER_FIN_UNKNOWN_PAYLOAD_VETO')
        self.physical_fin_closed = True
        self.receipt = self.monitor.after_fin(kernel, readmit=lambda: self._full_admit('post-fin'))
        if self.receipt['status'] != 'GREEN':
            raise LeaseControlFailure('OWNED_DIAGNOSTIC_CONTROL_RED_AFTER_ACTUAL_FIN')
        return self.receipt

    @property
    def evidence_refs(self):
        require(self.monitor.started is None or self.physical_fin_closed,
            'OWNED_DIAGNOSTIC_ACTIVE_NATIVE_CONTROL_READ_FORBIDDEN')
        result = []
        for path in sorted(self.control_root.iterdir()):
            raw = _read_private_control(path)
            result.append({'path': path.relative_to(Path(self.args.output)).as_posix(),
                'sha256': digest(raw), 'bytes': len(raw)})
        require(sum(row['bytes'] for row in result) <= MAX_EVIDENCE_BYTES,
            'OWNED_LEASE_TOTAL_CONTROL_BYTE_BOUND')
        return result


def issuer_factory(args, *, control_root):
    return DiagnosticIssuerMonitor(args, control_root=control_root)


def replay_lease_evidence(receipt, *, read_object, kernel, source_sha, source_tree, run_id, run_attempt,
                          expected_label=None):
    """Replay launch/control facts at historical UTC; no present lease check."""
    require(type(receipt) is dict and receipt.get('schema') == SCHEMA and receipt.get('status') == 'GREEN'
        and receipt.get('physical_fin_closed') is True and receipt.get('post_fin_readmission_verified') is True
        and receipt.get('stop_reason') is None and receipt.get('real_orders_sent') == 0
        and type(receipt.get('real_orders_sent')) is int and receipt.get('qualification_claimed') is False
        and receipt.get('continuous_unobserved_GitHub_state_claimed') is False,
        'OWNED_LEASE_ARCHIVED_CONTROL_RED_OR_MISSING')
    context = receipt.get('context')
    require(type(context) is dict and context.get('source_sha') == source_sha and context.get('source_tree') == source_tree
        and context.get('run_id') == run_id and context.get('run_attempt') == run_attempt == 1
        and isinstance(context.get('owner_session'), str)
        and re.fullmatch(r'CODEX_[A-Z0-9_]{1,159}', context['owner_session'])
        and (expected_label is None or receipt.get('command_label') == expected_label),
        'OWNED_LEASE_ARCHIVED_SOURCE_RUN_OR_LABEL_REBOUND')
    require(native.managed_phase_green(kernel) and receipt.get('kernel_sha256') == digest(wire(kernel))
        and receipt.get('actual_pid') == kernel.get('pid')
        and receipt.get('original_manager_sha256') == '55325b3108e175a42b87ebe544fd307fa45ffd29b6f7ab471443803ad9ba53b8',
        'OWNED_LEASE_ARCHIVED_ACTUAL_NATIVE_FIN_REBOUND')
    budget_seconds = http_budget_for_gate(context.get('gate'))
    require(receipt.get('http_total_budget_seconds') == budget_seconds
        and receipt.get('api_refresh_seconds') == API_REFRESH_SECONDS
        and receipt.get('local_poll_seconds') == LOCAL_POLL_SECONDS,
        'OWNED_LEASE_ARCHIVED_CONTROL_BUDGET_REBOUND')
    cache = {}
    def load(identifier):
        require(type(identifier) is str and SHA.fullmatch(identifier), 'OWNED_LEASE_ARCHIVED_OBJECT_REFERENCE_INVALID')
        if identifier not in cache:
            raw = read_object(identifier)
            require(type(raw) is bytes and 0 < len(raw) <= MAX_OBJECT_BYTES and digest(raw) == identifier,
                'OWNED_LEASE_ARCHIVED_OBJECT_BYTES_CHANGED')
            require(sum(len(value[0]) for value in cache.values()) + len(raw) <= MAX_EVIDENCE_BYTES,
                'OWNED_LEASE_EVIDENCE_BYTE_BOUND')
            cache[identifier] = raw, admission.document(raw)
        return cache[identifier][1]
    def response(identifier):
        value = load(identifier)
        if value.get('kind') == 'json':
            return value['value']
        require(value.get('kind') == 'array' and type(value.get('members')) is list,
            'OWNED_LEASE_ARCHIVED_RESPONSE_TYPE_INVALID')
        rows = []
        for item in value['members']:
            decoded = load(item)
            require(decoded.get('kind') == 'json', 'OWNED_LEASE_ARCHIVED_COMMENT_OBJECT_INVALID')
            rows.append(decoded['value'])
        require(digest(wire(rows)) == value.get('semantic_sha256'), 'OWNED_LEASE_ARCHIVED_TIMELINE_REBOUND')
        return rows
    launch, started, closed = (admission.stamp(receipt.get(key)) for key in
        ('launch_intent_utc', 'native_started_utc', 'native_fin_utc'))
    require(launch <= started <= closed, 'OWNED_LEASE_ARCHIVED_NATIVE_TIME_ORDER_INVALID')
    identifiers = receipt.get('snapshots')
    require(type(identifiers) is list and len(identifiers) >= 2 and len(set(identifiers)) == len(identifiers),
        'OWNED_LEASE_ARCHIVED_LAUNCH_AND_POST_FIN_REQUIRED')
    previous, final_calls, final_proof = None, None, None
    for position, identifier in enumerate(identifiers):
        wrapped = load(identifier)
        require(wrapped.get('kind') == 'snapshot', 'OWNED_LEASE_ARCHIVED_SNAPSHOT_TYPE_INVALID')
        snapshot = wrapped['value']
        require(snapshot.get('schema') == SNAPSHOT_SCHEMA and snapshot.get('context') == context
            and snapshot.get('http_total_budget_seconds') == budget_seconds
            and type(snapshot.get('elapsed_seconds')) in (int, float)
            and math.isfinite(snapshot['elapsed_seconds']) and 0 <= snapshot['elapsed_seconds'] <= budget_seconds,
            'OWNED_LEASE_ARCHIVED_SNAPSHOT_SCOPE_OR_BUDGET_REBOUND')
        read = admission.stamp(snapshot.get('read_utc'))
        began = admission.stamp(snapshot.get('started_utc'))
        require(began <= read and (read - began).total_seconds() <= budget_seconds
            and (previous is None or 0 <= (read - previous).total_seconds() <= API_REFRESH_SECONDS + budget_seconds),
            'OWNED_LEASE_ARCHIVED_OBSERVATION_STALE_OR_TIME_REBOUND')
        stage = snapshot.get('stage')
        require(stage == ('prelaunch' if position == 0 else 'post-fin' if position == len(identifiers)-1 else 'poll'),
            'OWNED_LEASE_ARCHIVED_LAUNCH_FIN_STAGE_REBOUND')
        if position == 0:
            require(read <= launch and (launch - read).total_seconds() <= LOCAL_POLL_SECONDS,
                'OWNED_LEASE_ARCHIVED_LAUNCH_OUTSIDE_FRESH_CONTROL')
        if position == len(identifiers)-1:
            require(began >= closed, 'OWNED_LEASE_ARCHIVED_POST_FIN_PRECEDES_ACTUAL_FIN')
        references = snapshot.get('api_response_objects')
        require(type(references) is dict and references, 'OWNED_LEASE_ARCHIVED_API_DOCUMENTS_REQUIRED')
        calls = {path: response(ref) for path, ref in references.items()}
        scope = _scope(context, read, lambda path: calls[path])
        require(digest(wire(scope)) == snapshot.get('scope_sha256')
            and snapshot.get('latest_owners') == {issue: {'comment_id': record['comment_id'],
                'lease_expires_utc': record['lease_expires_utc']} for issue, record in scope['fresh_ownership'].items()},
            'OWNED_LEASE_ARCHIVED_AUTHENTIC_SCOPE_REBOUND')
        proof = prove_lease_coverage(_timelines(calls), context, launch if position else read, read,
            workstream=scope.get('workstream_id'), authenticated_scope=scope)
        require(proof == snapshot.get('lease_coverage'), 'OWNED_LEASE_ARCHIVED_INTERVAL_PROOF_REBOUND')
        previous, final_calls, final_proof = read, calls, proof
    proof = prove_lease_coverage(_timelines(final_calls), context, launch, closed,
        workstream=scope.get('workstream_id'), authenticated_scope=scope)
    require(proof == receipt.get('lease_coverage') and receipt.get('object_count') == len(cache)
        and receipt.get('deduplicated_object_bytes') == sum(len(value[0]) for value in cache.values()),
        'OWNED_LEASE_ARCHIVED_COVERAGE_OR_DEDUP_COUNTER_REBOUND')
    return {'historical_launch_and_lease_union_verified': True, 'post_fin_readmission_verified': True,
        'past_lease_compared_to_current_time': False, 'real_orders_sent': 0, 'qualification_claimed': False}
