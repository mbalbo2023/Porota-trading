"""Every published canonical page must fail native acceptance on HTTP500."""
import pytest

from rc6_trader_dashboard.navigation import CANONICAL_PATHS
from scripts import rc6_zero_known_error_runtime_audit as audit


@pytest.mark.parametrize('broken', sorted(CANONICAL_PATHS))
def test_any_broken_canonical_page_blocks_native_acceptance_before_database(monkeypatch, broken):
    called = []
    def http(path):
        called.append(path)
        return (500, b'Internal Server Error') if path == broken else (200, b'{}')
    def forbidden():
        raise AssertionError('UNGUARDED_CANONICAL_HTTP_REACHED_DATABASE')
    monkeypatch.setattr(audit, '_http', http)
    monkeypatch.setattr(audit, '_surface_metrics', lambda path, body: {})
    monkeypatch.setattr(audit, '_assert_surface_metrics', lambda metrics: None)
    monkeypatch.setattr(audit, '_database_snapshot', forbidden)
    with pytest.raises(RuntimeError, match='DASHBOARD_HTTP_FAILURES=') as failure:
        audit.collect('canonical-route-negative')
    assert broken in str(failure.value) and broken in called


def test_required_legacy_and_truth_routes_are_retained():
    assert {'/vivo','/salud','/universo-operativo','/api/dashboard/truth'} <= set(audit.CORE_GET_PATHS)
    assert len(audit.CORE_GET_PATHS) == len(set(audit.CORE_GET_PATHS))


def test_partial_http_receipt_cannot_be_certified_as_all_routes_green(monkeypatch):
    from tests.test_ws_integ_03_runtime_audit import _snapshot
    monkeypatch.setenv('PAPER_SCALPING_MODE','ACTIVE_PAPER')
    monkeypatch.setenv('PAPER_CAUCION_SWEEP_MODE','ACTIVE_PAPER')
    snapshot = _snapshot()
    snapshot['http_status'].pop('/en-vivo/fuentes')
    with pytest.raises(AssertionError, match='DASHBOARD_HTTP_COVERAGE_INCOMPLETE'):
        audit._assert_snapshot(snapshot)
