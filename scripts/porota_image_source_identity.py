"""Verify actual image environment against the frozen source identity.

The expected SHA is never substituted for a missing observation. This gate
has no provider, database or order capability and does not rewrite history.
"""
import argparse
import json
import os
import re


def verify_source_identity(expected_sha, expected_tree, *, environ=None):
    actual = os.environ if environ is None else environ
    for value in (expected_sha, expected_tree):
        if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{40}', value):
            raise ValueError('IMAGE_EXPECTED_SOURCE_IDENTITY_INVALID')
    if actual.get('POROTA_BUILD_SHA') != expected_sha:
        raise ValueError('IMAGE_SOURCE_SHA_MISSING_OR_MISMATCHED')
    if actual.get('POROTA_CANDIDATE_TREE_SHA') != expected_tree:
        raise ValueError('IMAGE_SOURCE_TREE_MISSING_OR_MISMATCHED')
    return {'schema': 'porota.image-source-identity.v1', 'status': 'GREEN',
            'candidate_sha': expected_sha, 'candidate_tree_sha': expected_tree,
            'source': 'OBSERVED_IMAGE_ENVIRONMENT'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--expected-sha', required=True)
    parser.add_argument('--expected-tree', required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(verify_source_identity(args.expected_sha, args.expected_tree), sort_keys=True))
    except ValueError as error:
        print(json.dumps({'schema': 'porota.image-source-identity.v1', 'status': 'RED', 'reason': str(error)}))
        raise SystemExit(2)
