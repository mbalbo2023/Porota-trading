"""Discarded preopen plans and prior full audits must not pin worker memory."""
from datetime import timedelta
import weakref

from rc6_dynamic_universe import live
from rc6_shadow_runtime import worker as runtime
from tests.test_rc6_shadow_runtime_wiring import make_store, PRE, OPEN


def test_cold_tick_does_not_compute_a_second_discarded_full_plan(tmp_path, monkeypatch):
    store, _ = make_store(tmp_path, count=3)
    original = runtime.run_shadow
    full_calls = []
    frozen = []
    def observed(bundle, **kwargs):
        report = original(bundle, **kwargs)
        if not kwargs.get('frozen_only'):
            full_calls.append(1)
        else:
            frozen.append(report['frozen'])
        return report
    monkeypatch.setattr(runtime, 'run_shadow', observed)
    worker = runtime.ShadowRuntime(store.path, evidence_root=tmp_path / 'shadow', source_roots=[])
    worker.tick(PRE)
    assert len(full_calls) == 1
    assert len(frozen) == 1


def test_freeze_only_preserves_both_policy_payloads_and_digests():
    bundle = {'safety': {'mode': 'PRODUCTION_PAPER', 'real_orders_sent': 0,
                        'real_routes': 'NOT_CALLED'},
        'catalog': [dict(ticker='S0', instrument_type='ACCIONES', market='BYMA',
                         currency='ARS', settlement='A-24HS', status='AVAILABLE', capability='READY_PAPER_SPOT')],
        'as_of': PRE.isoformat(), 'session_open': OPEN.isoformat(),
        'preopen_cutoff': (PRE - timedelta(days=1)).isoformat(),
        'frozen_at': PRE.isoformat(), 'sessions': ['2026-10-02']}
    full = live.run_shadow(bundle)
    frozen = live.run_shadow(bundle, frozen_only=True)
    assert frozen['frozen'] == full['frozen']
    assert set(frozen['frozen']) == {'SCALPING', 'EQUITY_SPOT'}


def test_real_worker_releases_full_report_before_waiting_or_next_tick(tmp_path, monkeypatch):
    class Report(dict):
        pass
    references = []
    class Worker:
        def __init__(self, *_args, **_kwargs):
            pass
        def tick(self, _at):
            report = Report(status='SHADOW_OBSERVING', phase='CLOSED', full_audit=bytearray(1024**2))
            references.append(weakref.ref(report))
            return report
    class Stop:
        done = False
        def is_set(self):
            return self.done
        def wait(self, _seconds):
            assert references and references[-1]() is None
            self.done = True
    monkeypatch.setattr(runtime, 'ShadowRuntime', Worker)
    monkeypatch.setattr(runtime.os, 'nice', lambda _value: None)
    runtime.run_worker(tmp_path / 'paper.db', Stop(), clock_fn=lambda: PRE.isoformat())
