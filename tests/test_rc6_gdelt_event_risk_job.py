import inspect
import sqlite3

import rc6_gdelt_event_risk_job as m


def _safety_db(path):
    c=sqlite3.connect(path)
    c.execute('CREATE TABLE observer_state(id INTEGER PRIMARY KEY,mode TEXT,real_orders_sent INTEGER)')
    c.execute("INSERT INTO observer_state VALUES(1,'PRODUCTION_PAPER',0)")
    c.commit(); c.close()


def test_persists_structured_shadow_evidence_only(tmp_path,monkeypatch):
    safety=tmp_path/'observer.db'; store=tmp_path/'gdelt.db'; _safety_db(safety)
    def fake_collect_shadow(**kwargs):
        event_type=kwargs['event_type']
        return [{
            'event_id':'gdelt:test-'+event_type,
            'event_type':event_type,
            'first_seen_at':'2026-09-10T10:00:00Z',
            'published_at':'2026-09-10T09:59:00Z',
            'available_to_engine_at':'2026-09-10T10:00:00Z',
            'source':'GDELT_DOC:example.com',
            'source_tier':'TIER_C_SINGLE_SOURCE',
            'provenance_url':'https://example.com/event',
            'payload_hash':'abc123',
            'title':'Russia sanctions drive oil prices higher',
            'source_domain':'example.com',
            'region':'GLOBAL',
            'confirmed_at':None,
            'retracted_at':None,
            'entities':(),
            'exposures':(),
        }]
    monkeypatch.setattr(m,'collect_shadow',fake_collect_shadow)
    out=m.run_once(db_path=str(store),safety_db_path=str(safety),event_types=['WAR_ESCALATION'],maxrecords=5)
    assert out['state']=='GREEN'
    assert out['authority']=='SHADOW_ONLY'
    assert out['generic_news_feed']=='INTENTIONALLY_OFF_UNTOUCHED'
    c=sqlite3.connect(store)
    assert c.execute('select count(*) from gdelt_event_risk_events').fetchone()[0]==1
    assert c.execute('select authority from gdelt_event_risk_events').fetchone()[0]=='SHADOW_ONLY'
    assert c.execute('select title,source_domain from gdelt_event_risk_events').fetchone()==('Russia sanctions drive oil prices higher','example.com')
    headlines=m.latest_events(str(store),limit=10)
    assert headlines[0]['title']=='Russia sanctions drive oil prices higher'
    assert headlines[0]['authority']=='SHADOW_ONLY'
    c.close()


def test_default_ingestion_is_bounded():
    assert len(m.DEFAULT_EVENT_TYPES) <= m.MAX_EVENT_TYPES_PER_RUN
    assert m.DEFAULT_MAXRECORDS == 5
    assert len(m.DEFAULT_EVENT_TYPES) * m.DEFAULT_MAXRECORDS <= 50


def test_dashboard_event_limit_is_bounded(tmp_path):
    try:
        m.latest_events(str(tmp_path / "missing.db"), limit=101)
    except m.GDELTEventRiskJobError as exc:
        assert "EVENT_LIST_LIMIT_OUT_OF_RANGE" in str(exc)
    else:
        raise AssertionError("expected dashboard list limit guard")


def test_fails_closed_on_real_orders(tmp_path):
    safety=tmp_path/'observer.db'; store=tmp_path/'gdelt.db'; _safety_db(safety)
    c=sqlite3.connect(safety); c.execute('update observer_state set real_orders_sent=1 where id=1'); c.commit(); c.close()
    try:
        m.run_once(db_path=str(store),safety_db_path=str(safety),event_types=['WAR_ESCALATION'])
    except m.GDELTEventRiskJobError as exc:
        assert 'PAPER_SAFETY_INVARIANT_FAILED' in str(exc)
    else:
        raise AssertionError('expected fail closed')


def test_no_generic_news_or_broker_order_imports():
    source=inspect.getsource(m)
    forbidden=['g_news_feed','financial_news','ppi_client','send_order','place_order','buy_order','sell_order']
    assert not any(x in source.lower() for x in forbidden)


def test_latest_status_marks_stale_evidence_explicitly(tmp_path):
    safety = tmp_path / "observer.db"
    store = tmp_path / "gdelt.db"
    _safety_db(safety)

    def empty_collect(**_kwargs):
        return []

    original = m.collect_shadow
    m.collect_shadow = empty_collect
    try:
        m.run_once(db_path=str(store), safety_db_path=str(safety),
                   event_types=["WAR_ESCALATION"], maxrecords=5)
    finally:
        m.collect_shadow = original

    status = m.latest_status(
        str(store), now=m.datetime(2030, 1, 1, tzinfo=m.timezone.utc)
    )
    assert status["state"] == "STALE"
    assert status["freshness"] == "STALE"
    assert status["last_run_state"] == "GREEN"
    assert status["authority"] == "SHADOW_ONLY"
