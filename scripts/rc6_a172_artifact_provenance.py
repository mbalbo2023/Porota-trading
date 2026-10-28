#!/usr/bin/env python3
"""Require two exact, still available historical Predeploy artifacts before cleanup."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sys


PROVEN = (
    dict(sha='2325eba329878c62a61cb47bb66d33ccf279bd4a',
         tree='e66e3756769e297687a062bec2fa6e8599c0a275',
         artifact=12780414186,
         digest='sha256:606ee774060a3d0be83eafa2cd81e2817213dc4ce1ae962f2d56531363a2ca06',
         bytes=299773668,
         deploy_sha='6676267140907a17e342a664f718cfdcf9b3ce72'),
    dict(sha='33ea6d365c47d415fc5480a23b1338c208c2a29f',
         tree='6879e49ae3a02f64f6c52582147ca76e55576776',
         artifact=12849642784,
         digest='sha256:b227d8266d619846cae87e8e8ea23c2713fd2652582f03787510d6b231320f28',
         bytes=300715445,
         deploy_sha='418ca230df29e43cdc29e13b930e5c55de2caf62'),
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
    print(json.dumps({'schema': 'RC6_A172_ARTIFACT_PROVENANCE_V1',
                      'status': 'GREEN', 'historical_artifacts': facts}, sort_keys=True))


if __name__ == '__main__':
    main(sys.argv[1:])
