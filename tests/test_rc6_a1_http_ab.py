"""Offline, bounded control-plane transport experiment. No material gate."""
from datetime import timedelta
import pytest

from scripts import rc6_a1_http_ab as probe
from scripts import rc6_delegated_renewal as v2


def test_v1_actual_pagination_and_v2_model_have_counted_fresh_reads():
    receipt = probe.experiment()
    assert receipt['live_v2_reduction_measured'] is False
    assert receipt['v1_attempts'] == 57
    assert receipt['v2_model_attempts'] == 24
    assert [entry['revoked'] for entry in receipt['observations']] == [False, False, True]
    assert all(entry['v1']['attempts'] == 19 for entry in receipt['observations'])
    assert all(entry['v2_model']['attempts'] == 8 for entry in receipt['observations'])


def test_current_authority_never_falls_back_to_stale_bytes_on_transport_loss():
    rows = probe.fixture()
    now = probe.BASE+timedelta(minutes=31)
    with pytest.raises(ConnectionError, match='UNAVAILABLE'):
        probe.baseline(rows, now, fail_on='/issues/471/comments?')
    with pytest.raises(ConnectionError, match='UNAVAILABLE'):
        probe.modeled_v2(now, fail_on='/git/ref/heads/rc6-control-human')


def test_protected_ref_revocation_stops_v2_model():
    now = probe.BASE+timedelta(minutes=31)
    with pytest.raises(v2.AuthorityV2Error, match='HUMAN_REVOKED'):
        probe.modeled_v2(now, revoked=True)
    receipt = probe.experiment()
    assert receipt['observations'][-1]['revoked'] is True


def test_fixture_count_is_explicit_and_is_not_real_issue_content():
    rows = probe.fixture()
    assert {issue: len(value) for issue, value in rows.items()} == probe.COUNTS
    assert all(row['body'] == 'OBSERVATION_ONLY' for values in rows.values() for row in values)
