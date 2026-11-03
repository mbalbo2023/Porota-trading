"""Exact canonical checkpoint metrics without whole-document string copies."""
import copy
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import math
from unittest.mock import patch

import pytest

from rc6_performance.common import canonical
from rc6_shadow_runtime.persistence import checkpoint_metrics


@pytest.mark.parametrize('payload', [
    {}, {'checkpoint_sha256': 'old'},
    {'á': ['漢字', '😀', '\\', '\n', -0.0, 1.25, True, 1, None]},
    {'nested': {'checkpoint_sha256': 'this nested field stays in the hash'}},
    {'decimal': Decimal('1.230'), 'clock': datetime(2026, 10, 17, tzinfo=timezone.utc)},
    {'rows': [{'key': str(n), 'value': 'á😀' * 30} for n in range(600)]},
    {'checkpoint_sha256': {'invalid': 'still measured exactly'}, 'n': [1, 2]},
    {'tuple': (1, 2), 'keys': {1: 'numeric', 2: 'still numeric'}},
])
def test_digest_actual_size_and_signed_size_are_exact(payload):
    before = copy.deepcopy(payload)
    body = {k: v for k, v in payload.items() if k != 'checkpoint_sha256'}
    expected = hashlib.sha256(canonical(body).encode()).hexdigest()
    measured = checkpoint_metrics(payload)
    assert measured == {'sha256': expected,
                        'canonical_bytes': len(canonical(payload).encode()),
                        'signed_canonical_bytes': len(canonical({**body, 'checkpoint_sha256': expected}).encode())}
    assert payload == before


def test_every_call_hashes_actual_current_values_without_memo():
    payload = {'nested': {'rows': ['one']}}
    first = checkpoint_metrics(payload)
    payload['nested']['rows'][0] = 'two'
    second = checkpoint_metrics(payload)
    assert second['sha256'] != first['sha256']
    assert second['sha256'] == hashlib.sha256(canonical(payload).encode()).hexdigest()


@pytest.mark.parametrize('bad', [math.nan, math.inf, -math.inf])
def test_nonfinite_values_still_fail_closed(bad):
    with pytest.raises(ValueError):
        checkpoint_metrics({'bad': bad})


def test_circular_values_still_fail_closed():
    payload = {'rows': []}
    payload['rows'].append(payload)
    with pytest.raises(ValueError, match='Circular reference'):
        checkpoint_metrics(payload)


def test_metrics_never_pass_a_large_tree_to_the_c_encoder():
    original = json.JSONEncoder.encode
    calls = []
    def bounded(self, value):
        result = original(self, value)
        calls.append(len(result.encode()))
        assert calls[-1] <= 65536
        return result
    # Broad top-level maps and two retained sessions reproduce the copied
    # checkpoint working set without changing any production capacity limit.
    value = {'reaches': {str(n): {'session': str(n % 2), 'identity': ['á', 'CEDEARS', 'A-24HS', 'USD', 'BYMA'],
                               'stages': {'CATALOG_READY': '2026-10-17T10:00:00+00:00'}} for n in range(2000)}}
    with patch.object(json.JSONEncoder, 'encode', bounded):
        result = checkpoint_metrics(value)
    assert result['canonical_bytes'] > 100000
    assert calls and max(calls) <= 65536
