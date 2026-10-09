"""Read an exact, already sealed development artifact; never rerun a producer."""
from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).absolute().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.rc6_actions_custody import Github, canonical, digest, require, verify_owner


def inspect_sealed(raw, origin):
    require(len(raw) == origin['bytes'] <= 8 * 1024**2
            and digest(raw) == origin['sha256'], 'READOUT_ORIGINAL_CONTAINER_REQUIRED')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        infos = archive.infolist()
        require(0 < len(infos) <= 128 and len({i.filename for i in infos}) == len(infos)
                and sum(i.file_size for i in infos) <= 32 * 1024**2,
                'READOUT_BOUNDED_COMPLETE_CONTAINER_REQUIRED')
        # Reads validate the original CRCs. No extraction, execution or FIN
        # reconstruction is permitted; these are previously sealed copies.
        files = {i.filename: archive.read(i) for i in infos}
    result = json.loads(files['result.json'])
    require(result['schema'] == 'porota.rc6.development-checks.v1'
            and result['source_sha'] == origin['source_sha']
            and result['source_tree'] == origin['source_tree']
            and result['G0_G8_qualification'] is result['Product157_qualified'] is False,
            'READOUT_ORIGINAL_DEVELOPMENT_BINDING_REQUIRED')
    from scripts.rc6_development_checks import control_summary
    summary = control_summary(result)
    summary.update(schema='porota.rc6.sealed-development-readout.v1',
        origin=origin, epochs=[], producer_reexecuted=False, FIN_reconstructed=False,
        G0_G8_qualification=False, deployed=False, real_orders_sent=0)
    for epoch in result['epochs']:
        entry = next(row for row in control_summary(result)['epochs'] if row['epoch'] == epoch['epoch'])
        entry['failures'] = []
        manifest = json.loads(files[epoch['epoch'] + '/manifest.json'])
        require(manifest['schema'] == 'porota.rc6.generated-fixture-required-capture.v1'
                and manifest['actual_owned_fin_closed'] is True
                and manifest['binding']['candidate_sha'] == origin['source_sha']
                and manifest['binding']['candidate_tree'] == origin['source_tree'],
                'READOUT_ORIGINAL_SEALED_BINDING_REQUIRED')
        for row in manifest['files']:
            capture = files[epoch['epoch'] + '/' + row['capture_file']]
            require(digest(capture) == row['sha256']
                    and len(capture) == row['bytes'], 'READOUT_SEALED_COPY_REBOUND')
            if row['relative_source'] == 'junit.xml':
                require(epoch.get('junit') and digest(capture) == epoch['junit']['sha256'],
                        'READOUT_NATIVE_JUNIT_REBOUND')
                for case in ET.fromstring(capture).findall('.//testcase'):
                    for kind in ('failure', 'error', 'skipped'):
                        failure = case.find(kind)
                        if failure is not None and len(entry['failures']) < 8:
                            message = (failure.get('message', '') + '\n' + (failure.text or ''))
                            message = re.sub(r'https?://\S+', '[URL_REDACTED]', message)
                            entry['failures'].append({'classname': case.get('classname'),
                                'name': case.get('name'), 'kind': kind, 'text': message[:3500]})
        summary['epochs'].append(entry)
    if result.get('fullSource_unchanged') is False:
        from scripts.rc6_controlled_governed_runner import compare_source
        before = json.loads(files['source-before.index.json'])
        after = json.loads(files['source-after.index.json'])
        try:
            compare_source(before, after)
        except (ValueError, OSError) as error:
            summary['source_failure'] = str(error)[:8000]
    require(len(canonical(summary)) <= 96 * 1024, 'READOUT_SUMMARY_BOUND')
    return summary


def readout(github, plan, owner_check):
    require(plan['schema'] == 'porota.rc6.sealed-development-readout-plan.v1'
            and plan['repository'] == 'mbalbo2023/Porota-trading', 'READOUT_PLAN_REQUIRED')
    origin = plan['origin']
    owner_check()
    meta = github.request('/actions/artifacts/' + str(origin['artifact_id']))
    require(meta['digest'] == 'sha256:' + origin['sha256']
            and meta['size_in_bytes'] == origin['bytes'] and meta['expired'] is False
            and meta['workflow_run']['id'] == origin['run_id']
            and meta['workflow_run']['head_sha'] == origin['source_sha'],
            'READOUT_AUTHENTIC_ORIGIN_REQUIRED')
    run = github.request('/actions/runs/' + str(origin['run_id']) + '/attempts/1')
    require(run['head_sha'] == origin['source_sha'] and run['run_attempt'] == 1
            and run['status'] == 'completed' and run['conclusion'] == origin['conclusion'],
            'READOUT_TERMINAL_ORIGINAL_REQUIRED')
    owner_check()
    raw = github.request('/actions/artifacts/' + str(origin['artifact_id']) + '/zip',
                         binary=True, maximum=origin['bytes'])
    return inspect_sealed(raw, origin)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--source-sha', required=True)
    parser.add_argument('--source-tree', required=True)
    parser.add_argument('--owner-session', required=True)
    parser.add_argument('--launch-receipt-url', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    require(os.environ.get('GITHUB_ACTIONS') == 'true'
            and os.environ.get('GITHUB_REPOSITORY') == 'mbalbo2023/Porota-trading'
            and os.environ.get('GITHUB_ACTOR') == 'mbalbo2023'
            and os.environ.get('GITHUB_EVENT_NAME') == 'workflow_dispatch'
            and os.environ.get('GITHUB_RUN_ATTEMPT') == '1', 'READOUT_MANUAL_OWNER_REQUIRED')
    from scripts.rc6_controlled_governed_runner import git
    require(git(ROOT, 'rev-parse', 'HEAD').decode().strip() == args.source_sha
            and git(ROOT, 'rev-parse', 'HEAD^{tree}').decode().strip() == args.source_tree,
            'READOUT_EXACT_CONTROLLER_REQUIRED')
    raw = args.plan.read_bytes()
    github = Github(os.environ.get('GH_TOKEN'))
    check = lambda: verify_owner(github, args.launch_receipt_url, sha=args.source_sha,
        tree=args.source_tree, owner=args.owner_session, plan_sha256=digest(raw),
        authorization_field='RC6_EVIDENCE_READOUT_AUTHORIZATION')
    result = readout(github, json.loads(raw), check)
    args.output.write_bytes(canonical(result))
    print(canonical(result).decode(), flush=True)
    return 0


if __name__ == '__main__':
    sys.dont_write_bytecode = True
    raise SystemExit(main())
