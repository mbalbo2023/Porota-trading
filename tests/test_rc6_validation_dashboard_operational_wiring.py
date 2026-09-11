import en_validation_project_dashboard_rc6 as dashboard


def test_operational_daily_section_has_real_headers_and_keeps_campaign_separate(monkeypatch):
    monkeypatch.setattr(
        dashboard.operational_daily,
        'collect',
        lambda **kwargs: {
            'mode': 'PRODUCTION_PAPER',
            'real_orders_sent': 0,
            'days': [{
                'date_ar': '2026-09-10',
                'event_count': 12,
                'event_types': {'CLOSE': 3, 'WAIT': 9},
                'paper_positions': 4,
                'opened': 1,
                'closed': 3,
                'decisions': 20,
                'fills': 4,
                'realized_net_pnl': '123.45',
            }],
        },
    )
    html = dashboard._operational_daily_section(limit_days=10)
    assert 'Actividad PAPER por jornada' in html
    assert '<thead>' in html and '<th>Fecha AR</th>' in html
    assert '2026-09-10' in html and '123.45' in html
    # The operational section may explain that it does not promote governance
    # milestones; what it must never do is render a campaign milestone as data.
    assert '<td>M0</td>' not in html
    assert 'No modifica ni promociona M0–M11.' in html


def test_empty_campaign_ledger_is_not_described_as_no_paper_activity():
    html = dashboard._campaign_daily_history([], limit_days=10)
    assert 'ledger de campaña RC6 todavía no tiene observaciones' in html
    assert 'no significa que no haya habido actividad PAPER' in html


def test_operational_failure_is_visible_and_never_fabricated(monkeypatch):
    def boom(**kwargs):
        raise RuntimeError('probe')
    monkeypatch.setattr(dashboard.operational_daily, 'collect', boom)
    html = dashboard._operational_daily_section(limit_days=10)
    assert 'Evidencia operacional no disponible' in html
    assert 'No se inventan jornadas' in html
