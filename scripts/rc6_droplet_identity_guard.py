#!/usr/bin/env python3
"""Fail closed unless SSH reached the pinned DigitalOcean RC6 Droplet."""
from __future__ import annotations

import argparse
import re
import urllib.error
import urllib.request

BASE = 'http://169.254.169.254/metadata/v1/'


class IdentityRejected(RuntimeError):
    pass


def read_metadata(key, opener):
    if key not in {'id', 'region'}:
        raise IdentityRejected('METADATA_KEY_INVALID')
    request = urllib.request.Request(BASE + key, method='GET')
    try:
        with opener.open(request, timeout=3) as response:
            if response.status != 200:
                raise IdentityRejected('METADATA_HTTP_RED')
            value = response.read(65)
    except (OSError, urllib.error.URLError) as exc:
        raise IdentityRejected('METADATA_UNAVAILABLE') from exc
    if not value or len(value) > 64:
        raise IdentityRejected('METADATA_RESPONSE_INVALID')
    try:
        return value.decode('ascii').strip()
    except UnicodeDecodeError as exc:
        raise IdentityRejected('METADATA_RESPONSE_INVALID') from exc


def verify(expected_id, expected_region, opener=None):
    if (not re.fullmatch(r'[1-9][0-9]{5,11}', expected_id) or
            not re.fullmatch(r'[a-z]{3}[0-9]', expected_region)):
        raise IdentityRejected('EXPECTED_IDENTITY_INVALID')
    # Link-local metadata must not use inherited proxy settings.
    opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}))
    droplet_id = read_metadata('id', opener)
    region = read_metadata('region', opener)
    if droplet_id != expected_id or region != expected_region:
        raise IdentityRejected('DROPLET_IDENTITY_MISMATCH')
    return droplet_id, region


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-id', required=True)
    parser.add_argument('--expected-region', required=True)
    args = parser.parse_args(argv)
    try:
        droplet_id, region = verify(args.expected_id, args.expected_region)
    except IdentityRejected as exc:
        print(f'RC6_DROPLET_IDENTITY=RED|reason={exc}')
        return 2
    print(f'RC6_DROPLET_IDENTITY=GREEN|id={droplet_id}|region={region}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
