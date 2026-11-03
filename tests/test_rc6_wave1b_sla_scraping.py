from datetime import datetime, timedelta, timezone
import pytest

from rc6_data_sla_policy import DataSLA
from rc6_dom_coverage_reconciler import reconcile
from rc6_scraping_semaphore import evaluate


def test_sla_ordering_fail_closed():
    with pytest.raises(ValueError):
        DataSLA(21600, 100, 21600)
    with pytest.raises(ValueError):
        DataSLA(21600, 21600, 100)


def test_complete_and_fresh_is_green():
    cov = reconcile(
        family='CEDEARS',
        dom_ids=['A','B'],
        expected_ids=['A','B'],
        expected_source='independent-fixture',
    )
    now = datetime(2026, 9, 8, 21, 0, tzinfo=timezone.utc)
    result = evaluate(
        coverage=cov,
        last_success_at=now - timedelta(minutes=5),
        now=now,
        sla=DataSLA(21600,21600,43200),
    )
    assert result.color == 'GREEN'


def test_truncated_coverage_is_yellow():
    cov = reconcile(
        family='CEDEARS',
        dom_ids=[f'C{i}' for i in range(50)],
        expected_ids=[f'C{i}' for i in range(193)],
        expected_source='independent-fixture',
    )
    now = datetime(2026, 9, 8, 21, 0, tzinfo=timezone.utc)
    result = evaluate(
        coverage=cov,
        last_success_at=now - timedelta(minutes=5),
        now=now,
        sla=DataSLA(21600,21600,43200),
    )
    assert result.color == 'YELLOW'
    assert result.coverage_state == 'COVERAGE_YELLOW'


def test_unknown_expected_is_gray_not_green():
    cov = reconcile(family='LETRAS', dom_ids=['X'], expected_ids=None)
    now = datetime(2026, 9, 8, 21, 0, tzinfo=timezone.utc)
    result = evaluate(
        coverage=cov,
        last_success_at=now - timedelta(minutes=5),
        now=now,
        sla=DataSLA(21600,21600,43200),
    )
    assert result.color == 'GRAY'


def test_stale_or_no_success_is_red():
    cov = reconcile(
        family='ACCIONES', dom_ids=['GGAL'], expected_ids=['GGAL'],
        expected_source='independent-fixture')
    now = datetime(2026, 9, 8, 21, 0, tzinfo=timezone.utc)
    stale = evaluate(
        coverage=cov,
        last_success_at=now - timedelta(hours=13),
        now=now,
        sla=DataSLA(21600,21600,43200),
    )
    assert stale.color == 'RED'
    none = evaluate(
        coverage=cov,last_success_at=None,now=now,
        sla=DataSLA(21600,21600,43200))
    assert none.color == 'RED'
