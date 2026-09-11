import rc6_risk_gdelt_dashboard as panel


def test_gdelt_panel_reads_local_status_and_keeps_news_feed_off(monkeypatch):
    monkeypatch.setattr(panel.gdelt, 'latest_status', lambda: {
        'state': 'GREEN',
        'authority': 'SHADOW_ONLY',
        'run_id': 'GDELT-RC6-test',
        'started_at': '2026-09-11T00:00:00Z',
        'finished_at': '2026-09-11T00:01:00Z',
        'requested_event_types': 15,
        'successful_event_types': 15,
        'fetched_events': 42,
        'stored_events': 40,
        'events_total': 100,
        'latest_event_available_at': '2026-09-11T00:00:30Z',
    })
    html = panel.render_section()
    assert 'Event Risk estructurado — GDELT' in html
    assert 'Feed general de noticias: OFF intencional' in html
    assert 'SHADOW_ONLY' in html
    assert 'GDELT-RC6-test' in html
    assert '<thead>' in html


def test_gdelt_panel_never_collects_network(monkeypatch):
    monkeypatch.setattr(panel.gdelt, 'latest_status', lambda: {'state': 'NOT_RUN', 'authority': 'SHADOW_ONLY'})
    monkeypatch.setattr(panel.gdelt, 'run_once', lambda *a, **k: (_ for _ in ()).throw(AssertionError('network/run forbidden')))
    html = panel.render_section()
    assert 'NOT_RUN' in html
    assert 'Esta pantalla no dispara HTTP' in html


def test_gdelt_panel_fails_visible_on_store_read_error(monkeypatch):
    def boom():
        raise OSError('store unavailable')
    monkeypatch.setattr(panel.gdelt, 'latest_status', boom)
    html = panel.render_section()
    assert 'READ_ERROR' in html
    assert 'SHADOW_ONLY' in html
