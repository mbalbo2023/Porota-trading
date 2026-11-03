"""Regression for A174: a successful login must leave cooldown DEGRADED."""

import pytest


def test_cooldown_then_login_publishes_starting_before_slow_paper_init(monkeypatch):
    import bf_production_paper_observer as scanner
    import bv_paper_runtime

    class ReachedEngine(BaseException):
        pass

    class Store:
        path = '/unused/paper.db'

        def __init__(self):
            self.current = {}
            self.states = []
            self.events = []

        def state(self, **changes):
            self.current.update(changes)
            self.states.append(dict(self.current))

        def event(self, kind, detail):
            self.events.append(kind)

        def audit_http(self, *_args):
            pass

    store = Store()
    decisions = iter(('COOLDOWN', 'ALLOWED'))

    class Interlock:
        def __init__(self, *_args):
            pass

        def reserve(self):
            return next(decisions)

    class Reader:
        def __init__(self, *_args, **_kwargs):
            pass

        def login_once(self):
            pass

        def close(self):
            pass

    observed = []

    def inspect_before_engine(*_args, **_kwargs):
        observed.append(dict(store.current))
        raise ReachedEngine

    monkeypatch.setenv('POROTA_RUNTIME_SCHEMA_READY', '1')
    monkeypatch.setattr(scanner, 'STOP', False)
    monkeypatch.setattr(scanner, 'PaperStore', lambda _path: store)
    monkeypatch.setattr(scanner, 'ScannerLoginInterlock', Interlock)
    monkeypatch.setattr(scanner, 'ProductionMarketReader', Reader)
    monkeypatch.setattr(scanner, '_claim_command', lambda _store: None)
    monkeypatch.setattr(scanner, '_market_phase', lambda: 'CLOSED')
    monkeypatch.setattr(scanner, '_announce_phase', lambda *_args: None)
    monkeypatch.setattr(scanner, '_secret', lambda: ('read_key', 'read_secret'))
    monkeypatch.setattr(scanner, '_health', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(bv_paper_runtime, 'broker_from_environment', inspect_before_engine)
    monkeypatch.setattr(scanner.time, 'sleep', lambda _seconds: None)
    monkeypatch.setattr(scanner.signal, 'signal', lambda *_args: None)
    monkeypatch.setattr(scanner.faulthandler, 'enable', lambda: None)
    monkeypatch.setattr(scanner.faulthandler, 'dump_traceback_later', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(scanner.faulthandler, 'cancel_dump_traceback_later', lambda: None)

    with pytest.raises(ReachedEngine):
        scanner.run()

    assert any(s['process_state'] == 'DEGRADED' and s['ppi_auth'] == 'COOLDOWN'
               for s in store.states)
    assert store.events == ['PPI_LOGIN']
    assert len(observed) == 1
    assert observed[0]['process_state'] == 'STARTING'
    assert observed[0]['ppi_auth'] == 'OK'
    assert observed[0]['session_state'] == 'CLOSED'
    assert observed[0]['real_orders_sent'] == 0
    assert observed[0]['heartbeat_at']
