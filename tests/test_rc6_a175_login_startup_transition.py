"""Regression: Gateway local retry clears DEGRADED before PAPER engine init."""

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
            if len(self.states) >= 64:
                raise AssertionError("A175_STARTUP_STATE_UNBOUNDED")
            self.current.update(changes)
            self.states.append(dict(self.current))

        def event(self, kind, detail):
            self.events.append(kind)

        def audit_http(self, *_args):
            pass

    store = Store()
    class Reader:
        def __init__(self):
            self.closed = False

        def login_once(self):
            raise AssertionError("SCANNER_CHILD_SECOND_LOGIN_FORBIDDEN")

        def close(self):
            self.closed = True

    # Replace the connection actually used by scanner.run, not the retired
    # SDK/interlock seam. A bounded clock prevents a broken retry from
    # exhausting CI RAM while retaining the real startup state transitions.
    from rc6_ppi_gateway_reader_adapter import SharedFeedUnavailable
    import rc6_ppi_gateway_reader_adapter as gateway
    reader = Reader()
    attaches = []
    clock = [1000.0]
    sleeps = []

    def connect_gateway(database, *, consumer):
        assert database == store.path and consumer == "scanner"
        attaches.append(consumer)
        if len(attaches) == 1:
            raise SharedFeedUnavailable("PPI_GATEWAY_UNAVAILABLE")
        return reader

    def bounded_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) > 8:
            raise AssertionError("A175_UNBOUNDED_GATEWAY_RETRY")
        clock[0] += seconds

    observed = []

    def inspect_before_engine(*_args, **_kwargs):
        observed.append(dict(store.current))
        raise ReachedEngine

    monkeypatch.setenv('POROTA_RUNTIME_SCHEMA_READY', '1')
    monkeypatch.setattr(scanner, 'STOP', False)
    monkeypatch.setattr(scanner, 'PaperStore', lambda _path: store)
    monkeypatch.setattr(gateway, 'connect_paper_gateway_reader', connect_gateway)
    monkeypatch.setattr(scanner, '_claim_command', lambda _store: None)
    monkeypatch.setattr(scanner, '_market_phase', lambda: 'CLOSED')
    monkeypatch.setattr(scanner, '_announce_phase', lambda *_args: None)
    monkeypatch.setattr(scanner, '_secret', lambda: ('read_key', 'read_secret'))
    monkeypatch.setattr(scanner, '_health', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(bv_paper_runtime, 'broker_from_environment', inspect_before_engine)
    monkeypatch.setattr(scanner.time, 'time', lambda: clock[0])
    monkeypatch.setattr(scanner.time, 'sleep', bounded_sleep)
    monkeypatch.setattr(scanner.signal, 'signal', lambda *_args: None)
    monkeypatch.setattr(scanner.faulthandler, 'enable', lambda: None)
    monkeypatch.setattr(scanner.faulthandler, 'dump_traceback_later', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(scanner.faulthandler, 'cancel_dump_traceback_later', lambda: None)

    with pytest.raises(ReachedEngine):
        scanner.run()

    assert any(s['process_state'] == 'DEGRADED' and s['ppi_auth'] == 'COOLDOWN'
               for s in store.states)
    assert store.events == ['PPI_GATEWAY_ATTACHED']
    assert attaches == ['scanner', 'scanner']
    assert reader.closed
    assert len(sleeps) <= 8
    assert len(observed) == 1
    assert observed[0]['process_state'] == 'STARTING'
    assert observed[0]['ppi_auth'] == 'OK'
    assert observed[0]['session_state'] == 'CLOSED'
    assert observed[0]['real_orders_sent'] == 0
    assert observed[0]['heartbeat_at']
