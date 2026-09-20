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


def test_news_table_starts_with_ten_rows_and_reveals_more_in_batches(monkeypatch):
    monkeypatch.setattr(panel.gdelt, 'latest_status', lambda: {
        'state': 'GREEN', 'authority': 'SHADOW_ONLY'
    })
    monkeypatch.setattr(panel.gdelt, 'latest_events', lambda limit: [
        {
            'event_id': f'id-{i}', 'event_type': 'CENTRAL_BANK',
            'published_at': f'2026-09-20T10:{i:02d}:00Z',
            'title': f'Central bank rate decision affects bond markets {i}',
            'source_domain': 'news.example',
            'provenance_url': f'https://news.example/{i}',
            'authority': 'SHADOW_ONLY',
        } for i in range(25)
    ])
    rendered = panel.render_section()
    assert rendered.count("class='gdelt-more-row' hidden style='display:none!important'") == 15
    news_table = rendered.split("id='rc6-gdelt-news-table'", 1)[1].split("</table>", 1)[0]
    assert news_table.count("<tr") == 26  # one header plus 25 candidate rows
    assert "Mostrar más" in rendered
    assert "slice(0,10)" in rendered
    assert "row.style.removeProperty('display')" in rendered
    assert "SHADOW / OBSERVE_ONLY" in rendered


def test_news_table_escapes_headline_and_rejects_unsafe_link(monkeypatch):
    monkeypatch.setattr(panel.gdelt, 'latest_status', lambda: {'state': 'GREEN'})
    monkeypatch.setattr(panel.gdelt, 'latest_events', lambda limit: [{
        'event_type': 'CENTRAL_BANK', 'published_at': '2026-09-20T10:00:00Z',
        'title': '<script>alert(1)</script>', 'source_domain': 'bad.example',
        'provenance_url': 'javascript:alert(1)',
    }])
    rendered = panel.render_section()
    assert '<script>alert(1)</script>' not in rendered
    assert 'href=\'javascript:' not in rendered
