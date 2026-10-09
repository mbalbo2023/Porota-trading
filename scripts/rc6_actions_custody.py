"""Preserve exact Actions ZIPs in verified, recoverable draft-release assets.

This control operation never produces a qualification receipt or modifies runtime.
There is no overwrite/delete path. A partial copy can only resume by verifying
the existing bytes. Container digests, CRCs and every original member are checked.
"""
from __future__ import annotations

import argparse
import base64
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile

REPO = 'mbalbo2023/Porota-trading'
ROOT = Path(__file__).absolute().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
API = 'https://api.github.com/repos/' + REPO
MAX_ZIP = 64 * 1024**2
MAX_MEMBER = 128 * 1024**2
MAX_EXPANDED = 1024**3


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        result = super().redirect_request(request, fp, code, msg, headers, newurl)
        require(urllib.parse.urlparse(newurl).scheme == 'https', 'CUSTODY_NON_TLS_REDIRECT')
        if urllib.parse.urlparse(newurl).netloc != urllib.parse.urlparse(request.full_url).netloc:
            result.remove_header('Authorization')
        return result


class Github:
    def __init__(self, token):
        require(bool(token), 'CUSTODY_ACTIONS_TOKEN_REQUIRED')
        self.token = token
        self.opener = urllib.request.build_opener(SafeRedirect())

    def request(self, path, *, method='GET', body=None, maximum=4 * 1024**2, binary=False):
        url = path if path.startswith('https://') else API + path
        parsed = urllib.parse.urlparse(url)
        require(parsed.netloc in ('api.github.com', 'uploads.github.com') and parsed.scheme == 'https',
                'CUSTODY_AUTHENTICATED_ENDPOINT_REQUIRED')
        headers = {'Authorization': 'Bearer ' + self.token, 'X-GitHub-Api-Version': '2022-11-28',
                   'Accept': 'application/octet-stream' if binary else 'application/vnd.github+json'}
        if body is not None:
            headers['Content-Type'] = 'application/zip' if binary else 'application/json'
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=60) as response:
                raw = response.read(maximum + 1)
        except urllib.error.HTTPError as error:
            # Error URLs can contain SAS credentials. Never log them.
            raise ValueError('CUSTODY_HTTP_' + str(error.code)) from None
        require(len(raw) <= maximum, 'CUSTODY_TRANSPORT_BOUND')
        return raw if binary else json.loads(raw)


def verify_owner(github, url, *, sha, tree, owner, now=None, plan_sha256=None,
                 authorization_field='RC6_EVIDENCE_CUSTODY_AUTHORIZATION'):
    from scripts import rc6_material_pr_admission as admission
    require(authorization_field in ('RC6_EVIDENCE_CUSTODY_AUTHORIZATION',
        'RC6_DEVELOPMENT_CHECKS_AUTHORIZATION'), 'CUSTODY_UNKNOWN_CONTROL_OPERATION')
    match = re.fullmatch(r'https://github\.com/' + REPO + r'/issues/471#issuecomment-([0-9]+)', url)
    require(match is not None, 'CUSTODY_OWNER_RECEIPT_URL_REQUIRED')
    receipt = github.request('/issues/comments/' + match[1])
    require(receipt['html_url'] == url and receipt['issue_url'].endswith('/issues/471')
            and receipt['user']['login'] == 'mbalbo2023' and receipt['created_at'] == receipt['updated_at'],
            'CUSTODY_AUTHENTIC_UNEDITED_OWNER_REQUIRED')
    fields = {}
    for line in receipt['body'].splitlines():
        item = re.fullmatch(r'([A-Z][A-Z0-9_]*|real_orders_sent)=([^\r\n]*)', line)
        if item:
            require(item[1] not in fields, 'CUSTODY_DUPLICATE_AUTHORITY_FIELD')
            fields[item[1]] = item[2]
    require(fields.get(authorization_field) == 'APPROVED'
            and fields.get('SOURCE_SHA') == sha and fields.get('SOURCE_TREE') == tree
            and fields.get('WRITE_OWNER') == fields.get('SESSION_SUCCESSOR') == owner
            and fields.get('DEPLOY_OWNER') == 'NOT_ACQUIRED' and fields.get('RELEASED') == 'false'
            and fields.get('real_orders_sent') == '0', 'CUSTODY_EXACT_SCOPED_AUTHORIZATION_REQUIRED')
    if plan_sha256 is not None:
        key = 'CUSTODY_PLAN_SHA256' if authorization_field == 'RC6_EVIDENCE_CUSTODY_AUTHORIZATION' else 'DEVELOPMENT_PLAN_SHA256'
        require(fields.get(key) == plan_sha256, 'CUSTODY_EXACT_PLAN_AUTHORIZATION_REQUIRED')
    current = now or datetime.now(timezone.utc)
    expires = datetime.fromisoformat(fields['SOURCE_LEASE_EXPIRES_UTC'].replace('Z', '+00:00'))
    created = datetime.fromisoformat(receipt['created_at'].replace('Z', '+00:00'))
    require(created <= current < expires and (expires-created).total_seconds() <= 1200,
            'CUSTODY_LEASE_EXPIRED_OR_UNBOUNDED')
    twin_id = fields.get('OWNER_RECEIPT_473')
    require(twin_id is not None and twin_id.isdigit(), 'CUSTODY_OWNER_PAIR_REQUIRED')
    twin = github.request('/issues/comments/' + twin_id)
    require(twin['issue_url'].endswith('/issues/473') and twin['user']['login'] == 'mbalbo2023'
            and twin['created_at'] == twin['updated_at']
            and digest(twin['body'].encode()) == fields.get('OWNER_RECEIPT_473_SHA256'),
            'CUSTODY_AUTHENTIC_PAIR_DIGEST_REQUIRED')
    twin_fields = admission.fields(twin['body'])
    twin_created = datetime.fromisoformat(twin['created_at'].replace('Z', '+00:00'))
    twin_expires = datetime.fromisoformat(twin_fields['SOURCE_LEASE_EXPIRES_UTC'].replace('Z', '+00:00'))
    require(twin_fields.get('WRITE_OWNER') == twin_fields.get('SESSION_SUCCESSOR') == owner
            and twin_fields.get('RELEASED') == 'false' and twin_fields.get('DEPLOY_OWNER') == 'NOT_ACQUIRED'
            and twin_fields.get('SOURCE_SHA') == sha and twin_fields.get('SOURCE_TREE') == tree
            and twin_fields.get('MODE') == 'PRODUCTION_PAPER / SIMULATION'
            and twin_fields.get('real_orders_sent') == '0'
            and twin_created <= current < twin_expires and (twin_expires-twin_created).total_seconds() <= 1200,
            'CUSTODY_OWNER_PAIR_NOT_LIVE')
    # A still-fresh comment may have been superseded. Read complete updated
    # timelines from the authentic administrative transfer in both Issues.
    anchors = admission.administrative_anchors(owner, get=github.request)
    for issue in (471, 473):
        timeline = admission.recent(issue, anchors[issue], current, get=github.request)
        admission.latest_writer(timeline, issue, sha, tree, owner, current)
    return digest(receipt['body'].encode())


def load_original_index(github, plan):
    origin = plan['index']
    row = github.request('/contents/' + urllib.parse.quote(origin['path'], safe='/') + '?ref=' + origin['source_sha'])
    raw = base64.b64decode(row['content'], validate=False)
    blob = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
    require(row['sha'] == blob == origin['git_blob'] and len(raw) == origin['bytes']
            and digest(raw) == origin['sha256'], 'CUSTODY_ORIGINAL_INDEX_REBOUND')
    result = {}
    for item in csv.DictReader(io.StringIO(raw.decode('utf-8'))):
        key = (int(item['artifact_id']), item['path'])
        require(key not in result, 'CUSTODY_ORIGINAL_INDEX_DUPLICATE')
        result[key] = {'bytes': int(item['uncompressed_bytes']), 'sha256': item['sha256']}
    return result


def verify_zip(raw, artifact, index):
    require(len(raw) == artifact['bytes'] <= MAX_ZIP and digest(raw) == artifact['sha256'],
            'CUSTODY_CONTAINER_BYTES_OR_DIGEST_MISMATCH')
    expected = {name: value for (ident, name), value in index.items() if ident == artifact['artifact_id']}
    members = []
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        infos = archive.infolist()
        require(len(infos) == len(expected) == artifact['member_count'] and len(infos) <= 10000,
                'CUSTODY_COMPLETE_MEMBER_SET_REQUIRED')
        require(len({item.filename for item in infos}) == len(infos), 'CUSTODY_ZIP_DUPLICATE_MEMBER')
        require(sum(item.file_size for item in infos) <= MAX_EXPANDED, 'CUSTODY_ZIP_EXPANDED_BOUND')
        for info in infos:
            require(info.filename in expected and info.file_size <= MAX_MEMBER,
                    'CUSTODY_ZIP_MEMBER_SET_OR_SIZE_REBOUND')
            # Reading each member checks its CRC. Nothing is extracted or executed.
            body = archive.read(info)
            require(len(body) == expected[info.filename]['bytes'] and digest(body) == expected[info.filename]['sha256'],
                    'CUSTODY_ORIGINAL_MEMBER_DIGEST_MISMATCH')
            members.append({'path': info.filename, 'bytes': len(body), 'sha256': digest(body),
                            'crc32': f'{info.CRC:08x}'})
    return members


def archive_plan(github, plan, *, owner_check=lambda: None):
    require(plan['schema'] == 'porota.rc6.actions-custody-plan.v1' and plan['repository'] == REPO,
            'CUSTODY_PLAN_SCHEMA_REQUIRED')
    require(0 < len(plan['artifacts']) == len({a['artifact_id'] for a in plan['artifacts']}) <= 64,
            'CUSTODY_ARTIFACT_SET_DUPLICATED_OR_UNBOUNDED')
    index = load_original_index(github, plan)
    releases = github.request('/releases?per_page=100')
    matches = [row for row in releases if row['tag_name'] == plan['draft_release_tag']]
    require(len(matches) <= 1, 'CUSTODY_RELEASE_AMBIGUOUS')
    owner_check()
    release = matches[0] if matches else github.request('/releases', method='POST', body=canonical({
        'tag_name': plan['draft_release_tag'], 'target_commitish': plan['target_commitish'], 'draft': True,
        'prerelease': True, 'name': 'RC6 RAW custody — evidence only',
        'body': 'Recoverable exact historical Actions ZIPs; no build, qualification, promotion or deploy.\n'
                + 'Original index SHA256: ' + plan['index']['sha256']}))
    require(release['draft'] is True and release['target_commitish'] == plan['target_commitish'],
            'CUSTODY_DRAFT_EVIDENCE_DESTINATION_REQUIRED')
    receipts = []
    for artifact in plan['artifacts']:
        owner_check()
        meta = github.request('/actions/artifacts/' + str(artifact['artifact_id']))
        require(meta.get('digest') == 'sha256:' + artifact['sha256'] and meta['size_in_bytes'] == artifact['bytes']
                and meta['workflow_run']['id'] == artifact['run_id']
                and meta['workflow_run']['head_sha'] == artifact['source_sha'] and not meta['expired'],
                'CUSTODY_ACTIONS_ORIGIN_REBOUND_OR_EXPIRED')
        raw = github.request('/actions/artifacts/' + str(artifact['artifact_id']) + '/zip',
                             maximum=artifact['bytes'], binary=True)
        members = verify_zip(raw, artifact, index)
        name = f"actions-{artifact['artifact_id']}-{artifact['sha256']}.zip"
        assets = github.request('/releases/' + str(release['id']) + '/assets?per_page=100')
        existing = [item for item in assets if item['name'] == name]
        require(len(existing) <= 1, 'CUSTODY_ASSET_AMBIGUOUS')
        if existing:
            asset = existing[0]
        else:
            owner_check()
            asset = github.request('https://uploads.github.com/repos/' + REPO + '/releases/'
                                   + str(release['id']) + '/assets?name=' + urllib.parse.quote(name),
                                   method='POST', body=raw, binary=True)
            asset = json.loads(asset)
        recovered = github.request('/releases/assets/' + str(asset['id']), maximum=artifact['bytes'], binary=True)
        verify_zip(recovered, artifact, index)
        require(asset['size'] == len(recovered), 'CUSTODY_RECOVERY_SIZE_MISMATCH')
        receipts.append({**artifact, 'durable_destination': {'release_id': release['id'], 'asset_id': asset['id'],
                         'api_url': API + '/releases/assets/' + str(asset['id'])},
                         'recoverable_sha256': digest(recovered), 'recovery_read_verified': True, 'members': members})
        print(json.dumps({'artifact_id': artifact['artifact_id'], 'asset_id': asset['id'], 'verified': True}), flush=True)
    result = {'schema': 'porota.rc6.actions-custody-receipt.v1', 'artifacts': receipts,
            'release_id': release['id'], 'release_url': release['html_url'], 'G0_G8_qualification': False,
            'artifact_validated': False, 'deployed': False, 'real_orders_sent': 0}
    receipt_raw = canonical(result)
    receipt_name = 'custody-receipt-' + digest(receipt_raw) + '.json'
    assets = github.request('/releases/' + str(release['id']) + '/assets?per_page=100')
    matching = [item for item in assets if item['name'] == receipt_name]
    require(len(matching) <= 1, 'CUSTODY_RECEIPT_ASSET_AMBIGUOUS')
    owner_check()
    asset = matching[0] if matching else github.request(
        'https://uploads.github.com/repos/' + REPO + '/releases/' + str(release['id'])
        + '/assets?name=' + receipt_name, method='POST', body=receipt_raw)
    recovered = github.request('/releases/assets/' + str(asset['id']), maximum=len(receipt_raw), binary=True)
    require(recovered == receipt_raw, 'CUSTODY_DURABLE_RECEIPT_READBACK_MISMATCH')
    result['durable_receipt'] = {'asset_id': asset['id'], 'sha256': digest(receipt_raw),
                                'api_url': API + '/releases/assets/' + str(asset['id'])}
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--source-sha', required=True)
    parser.add_argument('--source-tree', required=True)
    parser.add_argument('--owner-session', required=True)
    parser.add_argument('--launch-receipt-url', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    require(os.environ.get('GITHUB_ACTIONS') == 'true' and os.environ.get('GITHUB_REPOSITORY') == REPO
            and os.environ.get('GITHUB_ACTOR') == 'mbalbo2023' and os.environ.get('GITHUB_EVENT_NAME') == 'workflow_dispatch'
            and os.environ.get('GITHUB_RUN_ATTEMPT') == '1', 'CUSTODY_EXPLICIT_CANONICAL_ACTIONS_ONLY')
    github = Github(os.environ.get('GH_TOKEN'))
    plan_raw = args.plan.read_bytes()
    owner_check = lambda: verify_owner(github, args.launch_receipt_url, sha=args.source_sha,
                                      tree=args.source_tree, owner=args.owner_session,
                                      plan_sha256=digest(plan_raw))
    owner_check()
    plan = json.loads(plan_raw)
    result = archive_plan(github, plan, owner_check=owner_check)
    result.update(source_sha=args.source_sha, source_tree=args.source_tree, plan_sha256=digest(plan_raw),
                  owner_session=args.owner_session, launch_receipt_url=args.launch_receipt_url)
    args.output.write_bytes(canonical(result))


if __name__ == '__main__':
    main()
