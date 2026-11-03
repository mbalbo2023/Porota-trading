#!/usr/bin/env python3
"""Require exact A171/A174 frozen artifacts before orphan image cleanup."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sys


PROVEN = (
    dict(sha='b6e6ce75f29fe117aaa42b0bfcd9386ea93a0aa0',
         tree='ac82bcb2617748294c9950a009864dbf95e336f4',
         artifact=12858223304,
         digest='sha256:d15a8f66713142da43e380a4cb59395bc4eed3c52ac7e256058991fb5a5c5982',
         bytes=300905092,
         deploy_sha='ff2775933b1565bb3a43fa130f9aa6359ecbdf48'),
    dict(sha='4ce63a68c668cadd2a0aa7e8aded44ae4a3c46a6',
         tree='18693659202fc1a5cc0fa98113f0a1f9ad8a416d',
         artifact=12852892173,
         digest='sha256:d639fe45132422fc21c223ae289ef00f4d35553771926399a44cee42c98f3665',
         bytes=300726421,
         deploy_sha='d9e2a4d7e55b27e9799ae6db44dfda9a013912c6'),
)


def verify(predeploy, artifacts, deploy, target, *, now=None):
    now = now or datetime.now(timezone.utc)
    if (predeploy.get('status') != 'completed' or predeploy.get('conclusion') != 'success' or
            predeploy.get('head_sha') != target['sha'] or
            predeploy.get('head_commit', {}).get('tree_id') != target['tree'] or
            deploy.get('status') != 'completed' or deploy.get('conclusion') != 'failure' or
            deploy.get('head_sha') != target['deploy_sha'] or
            deploy.get('head_commit', {}).get('tree_id') != target['tree']):
        raise ValueError('HISTORICAL_RUN_PROVENANCE_RED')
    matches = [item for item in artifacts.get('artifacts', [])
               if item.get('id') == target['artifact']]
    if len(matches) != 1:
        raise ValueError('HISTORICAL_ARTIFACT_MISSING')
    item = matches[0]
    if (item.get('name') != 'porota-predeploy-v2-' + target['sha'] or
            item.get('digest') != target['digest'] or
            item.get('size_in_bytes') != target['bytes'] or
            item.get('expired') is not False):
        raise ValueError('HISTORICAL_ARTIFACT_PROVENANCE_RED')
    expiry = datetime.fromisoformat(item['expires_at'].replace('Z', '+00:00'))
    if expiry <= now:
        raise ValueError('HISTORICAL_ARTIFACT_EXPIRED')
    return {'sha': target['sha'], 'artifact_id': item['id'], 'digest': item['digest']}


def main(argv):
    if len(argv) != 6:
        raise SystemExit('expected predeploy/artifacts/deploy triples for two targets')
    facts = []
    for i, target in enumerate(PROVEN):
        facts.append(verify(*(json.loads(Path(argv[i*3+j]).read_text()) for j in range(3)), target))
    print(json.dumps({'schema': 'RC6_A175_ARTIFACT_PROVENANCE_V1',
                      'status': 'GREEN', 'historical_artifacts': facts}, sort_keys=True))


if __name__ == '__main__':
    main(sys.argv[1:])
