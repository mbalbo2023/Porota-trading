"""The private worker may consume its checkpoint, preserving the public API."""
from copy import deepcopy
import json
from datetime import timedelta

import pytest

from rc6_performance.common import canonical
from rc6_shadow_runtime.funnel import evaluate_runtime_funnel
from rc6_shadow_runtime.worker import ShadowRuntime
from tests.test_rc6_shadow_runtime_wiring import make_store, PRE


def test_private_funnel_preserves_every_report_field_and_causal_hash(tmp_path):
    store, _ = make_store(tmp_path, count=6)
    worker = ShadowRuntime(store.path, evidence_root=tmp_path / 'shadow', source_roots=[])
    planner = worker.tick(PRE)
    previous = None
    for n in range(3):
        at = PRE + timedelta(seconds=30*n)
        before = canonical(previous)
        expected, checkpoint = evaluate_runtime_funnel(store.path, as_of=at, planner_report=planner,
                                                       previous=previous)
        assert canonical(previous) == before
        owned = json.loads(canonical(previous))  # exact private writer/read boundary
        actual, actual_checkpoint = evaluate_runtime_funnel(store.path, as_of=at, planner_report=planner,
            previous=owned, private_runtime=True)
        assert canonical(actual) == canonical(expected)
        assert canonical(actual_checkpoint) == canonical(checkpoint)
        if owned is not None:
            assert actual_checkpoint is owned
        # Public reports remain detached from the causal checkpoint.
        if expected['state_reaches']:
            expected['state_reaches'][0]['stage'] = 'CALLER_CHANGE'
            assert 'CALLER_CHANGE' not in canonical(checkpoint)
        previous = checkpoint


def test_private_funnel_still_revokes_invalid_checkpoint(tmp_path):
    store, _ = make_store(tmp_path, count=3)
    planner = ShadowRuntime(store.path, evidence_root=tmp_path / 'shadow', source_roots=[]).tick(PRE)
    _, prior = evaluate_runtime_funnel(store.path, as_of=PRE, planner_report=planner)
    prior['events_recorded'] += 1
    public, cp1 = evaluate_runtime_funnel(store.path, as_of=PRE, planner_report=planner, previous=deepcopy(prior))
    private, cp2 = evaluate_runtime_funnel(store.path, as_of=PRE, planner_report=planner,
                                          previous=deepcopy(prior), private_runtime=True)
    assert private['checkpoint_invalidation'] == 'FUNNEL_CHECKPOINT_INVALID'
    assert canonical(private) == canonical(public)
    assert canonical(cp1) == canonical(cp2)


def test_planner_reads_verified_freeze_without_copying_its_payload(tmp_path, monkeypatch):
    from rc6_dynamic_universe import orchestrator
    store, _ = make_store(tmp_path, count=3)
    worker = ShadowRuntime(store.path, evidence_root=tmp_path / 'shadow', source_roots=[])
    original = orchestrator.deepcopy
    copies = []
    def observed(value):
        if isinstance(value, dict) and 'rows' in value and 'frozen_at' in value:
            copies.append(value)
        return original(value)
    monkeypatch.setattr(orchestrator, 'deepcopy', observed)
    report = worker.tick(PRE)
    assert not copies
    for engine, plan in report['engines'].items():
        frozen_rows = {tuple(r['identity']): r for r in report['frozen'][engine]['payload']['rows']}
        for row in plan['telemetry']:
            assert row['rank_components'] == frozen_rows[tuple(row['identity'])]['components']
    # The next invocation validates the same immutable bytes and advances.
    worker.tick(PRE + timedelta(seconds=30))


def test_equal_freeze_rows_share_storage_without_merging_different_evidence():
    from rc6_dynamic_universe.common import digest
    from rc6_shadow_runtime.worker import _share_equal_frozen_rows
    rows = [{'identity': ['ONE', 'ACCIONES', 'BYMA', 'ARS', 'A-24HS'],
             'provenance': {'cutoff': '2026-10-02'}}]
    frozen = {'frozen': {
        'SCALPING': {'payload': {'rows': rows, 'capacity_fingerprint': 'one'}},
        'EQUITY_SPOT': {'payload': {'rows': deepcopy(rows), 'capacity_fingerprint': 'two'}},
        'DIFFERENT': {'payload': {'rows': rows + [{'identity': ['OTHER']}]}}}}
    before = digest(frozen)
    _share_equal_frozen_rows(frozen)
    assert digest(frozen) == before
    assert frozen['frozen']['SCALPING']['payload']['rows'] is frozen['frozen']['EQUITY_SPOT']['payload']['rows']
    assert frozen['frozen']['DIFFERENT']['payload']['rows'] is not rows
