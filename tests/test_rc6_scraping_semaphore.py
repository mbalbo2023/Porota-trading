from datetime import datetime, timezone, timedelta
from rc6_dom_coverage_reconciler import reconcile
from rc6_data_sla_policy import DataSLA
from rc6_scraping_semaphore import evaluate

NOW=datetime(2026,9,8,17,0,tzinfo=timezone.utc)
SLA=DataSLA(21600,21600,43200)

def test_complete_and_fresh_is_green():
    c=reconcile(family='CEDEARS',dom_ids=['A','B'],expected_ids=['A','B'],expected_source='fixture')
    s=evaluate(coverage=c,last_success_at=NOW-timedelta(hours=1),now=NOW,sla=SLA)
    assert s.color=='GREEN'
    assert s.complete_proven is True

def test_truncated_dom_is_yellow_even_if_fresh():
    c=reconcile(family='CEDEARS',dom_ids=[str(i) for i in range(50)],expected_ids=[str(i) for i in range(193)],expected_source='candidate_universe')
    s=evaluate(coverage=c,last_success_at=NOW-timedelta(hours=1),now=NOW,sla=SLA)
    assert c.status=='PARTIAL_TRUNCATED'
    assert s.color=='YELLOW'
    assert s.complete_proven is False

def test_unknown_expected_universe_is_gray_not_green():
    c=reconcile(family='LETRAS',dom_ids=[str(i) for i in range(30)])
    s=evaluate(coverage=c,last_success_at=NOW-timedelta(hours=1),now=NOW,sla=SLA)
    assert s.color=='GRAY'
    assert s.reason=='EXPECTED_UNIVERSE_NOT_PROVEN'

def test_stale_is_red_even_if_coverage_complete():
    c=reconcile(family='ACCIONES',dom_ids=['GGAL'],expected_ids=['GGAL'],expected_source='fixture')
    s=evaluate(coverage=c,last_success_at=NOW-timedelta(hours=13),now=NOW,sla=SLA)
    assert s.color=='RED'
    assert s.freshness_state=='STALE'

def test_no_success_is_red():
    c=reconcile(family='ACCIONES',dom_ids=['GGAL'],expected_ids=['GGAL'],expected_source='fixture')
    s=evaluate(coverage=c,last_success_at=None,now=NOW,sla=SLA)
    assert s.color=='RED'
