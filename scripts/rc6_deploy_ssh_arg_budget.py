#!/usr/bin/env python3
"""Bound all source-bearing Deploy V2 SSH command arguments before host contact."""
from __future__ import annotations

import base64
import gzip
import lzma
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_REMOTE_ARG_BYTES = 110000
# Covers the fixed SSH command text, quoted identifiers, policy names and
# future small options. The largest observed exact argument is < 103 KB.
COMMAND_OVERHEAD_BYTES = 4096


def encoded_size(path: str, *, compressed: bool = False,
                 xz: bool = False) -> int:
    source = (ROOT / path).read_bytes()
    if compressed and xz:
        raise ValueError('SSH_ARG_COMPRESSION_AMBIGUOUS')
    if compressed:
        source = gzip.compress(source, compresslevel=9, mtime=0)
    if xz:
        source = lzma.compress(source, preset=6)
    return len(base64.b64encode(source))


def budgets(sizes: dict[str, int]) -> dict[str, int]:
    required = {'mutex', 'stage', 'audit', 'audit_gzip', 'a125_gzip', 'a143_gzip', 'a143_xz',
                'host_policy', 'host_policy_gzip', 'disk_policy', 'image_cleanup', 'housekeeping'}
    if (set(sizes) != required or any(type(value) is not int or value <= 0
                                      for value in sizes.values())):
        raise ValueError('SSH_ARG_SOURCE_INVENTORY_RED')
    o = COMMAND_OVERHEAD_BYTES
    m = sizes['mutex']
    return {
        # These three sources travel over SSH stdin as validated Python
        # literals; only their digests and the mutex live in the remote argv.
        'classify': m + o + 64,
        'drain_caucion': m + sizes['audit'] + sizes['a125_gzip'] + o,
        # A175 daily NO_GO is validated by staged scheduler -> staged
        # quiescence, both carried in the *same* drain command before install.
        'drain_a175': m + sizes['a125_gzip'] + sizes['a143_xz'] + o,
        'suspend_or_recover': m + sizes['a143_gzip'] + sizes['host_policy_gzip'] + o,
        'preclean': m + sizes['disk_policy'] + sizes['image_cleanup'] +
                    sizes['housekeeping'] + o,
        'transfer': m + sizes['stage'] + o,
    }


def require_budget(sizes: dict[str, int]) -> dict[str, int]:
    observed = budgets(sizes)
    if any(value > MAX_REMOTE_ARG_BYTES for value in observed.values()):
        raise ValueError('SSH_REMOTE_SINGLE_ARGUMENT_TOO_LARGE')
    return observed


def source_sizes() -> dict[str, int]:
    return {
        'mutex': encoded_size('scripts/rc6_deploy_host_mutex.py'),
        'stage': encoded_size('scripts/rc6_deploy_stage_transfer.py'),
        'audit': encoded_size('cb_caucion_audit.py'),
        'audit_gzip': encoded_size('cb_caucion_audit.py',compressed=True),
        'a125_gzip': encoded_size('scripts/rc6_deploy_scheduler_suspend.py',
                                   compressed=True),
        'a143_gzip': encoded_size('scripts/rc6_deploy_quiescence.py',
                                   compressed=True),
        'a143_xz': encoded_size('scripts/rc6_deploy_quiescence.py',xz=True),
        'host_policy': encoded_size('ops/policy/host-control-plane-reconciliation-v2.json'),
        'host_policy_gzip': encoded_size('ops/policy/host-control-plane-reconciliation-v2.json',compressed=True),
        'disk_policy': encoded_size('ops/policy/rc6-disk-housekeeping-v1.json'),
        'image_cleanup': encoded_size('scripts/rc6_pretransfer_image_cleanup.py'),
        'housekeeping': encoded_size('scripts/rc6_disk_housekeeping.py'),
    }


if __name__ == '__main__':
    try:
        values = require_budget(source_sizes())
    except (OSError, ValueError) as exc:
        print('RC6_A166_SSH_ARG_BUDGET=RED|' + str(exc).split(':', 1)[0])
        raise SystemExit(2)
    print('RC6_A166_SSH_ARG_BUDGET=GREEN|max=' + str(max(values.values())) +
          '|limit=' + str(MAX_REMOTE_ARG_BYTES))
