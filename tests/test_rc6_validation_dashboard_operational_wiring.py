import json

import pytest

import en_validation_project_dashboard_rc6 as dashboard


def _published_action4(tmp_path, monkeypatch, payload):
    reports = tmp_path / "reports"
    reports.mkdir(exist_ok=True)
    (reports / dashboard.action4_audit.FILENAME).write_text(
        json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(dashboard, "database_path", lambda: tmp_path / "paper.db")
    monkeypatch.setattr(dashboard, "artifact_root", lambda _: tmp_path)
    return dashboard._action4_section()


def _paper_audit():
    return {
        "schema_version": 1, "status": "PAPER_READ_ONLY",
        "dashboard_daily_report": {"periodo": "2026-11-07", "lessons": []},
        "separation": {"executed_closed": 2, "opened_simulated": 1},
        "safety": {"observer_mode": "PRODUCTION_PAPER", "sqlite": "READ_ONLY",
                   "real_orders_sent": 0, "broker_routes_called": False},
    }


def test_published_action4_ui_uses_verified_paper_zero_not_a_default(monkeypatch, tmp_path):
    payload = _paper_audit()
    assert dashboard.action4_audit.published_audit_is_safe(payload)
    html = _published_action4(tmp_path, monkeypatch, payload)
    assert "READ_ONLY · 0 reales" in html
    assert "card-green" in html
    assert "2026-11-07" in html


@pytest.mark.parametrize("field,value", [
    ("real_orders_sent", False), ("real_orders_sent", 0.0),
    ("real_orders_sent", "0"), ("real_orders_sent", None),
    ("real_orders_sent", 1), ("real_orders_sent", -1),
    ("observer_mode", "PRODUCTION_REAL"), ("observer_mode", None),
    ("sqlite", "READ_WRITE"), ("broker_routes_called", True),
    ("broker_routes_called", None),
])
def test_published_action4_ui_quarantines_unverified_safety(monkeypatch, tmp_path, field, value):
    payload = _paper_audit()
    payload["safety"][field] = value
    assert not dashboard.action4_audit.published_audit_is_safe(payload)
    html = _published_action4(tmp_path, monkeypatch, payload)
    assert "NO_VERIFICADO" in html
    assert "READ_ONLY · 0 reales" not in html
    assert "card-green" not in html


@pytest.mark.parametrize("invalid", [None, [], {"status": "PAPER_READ_ONLY"},
                                             {"status": "SUCCESS", "safety": {"real_orders_sent": 0}}])
def test_published_action4_ui_rejects_malformed_or_wrong_envelope(monkeypatch, tmp_path, invalid):
    assert not dashboard.action4_audit.published_audit_is_safe(invalid)
    html = _published_action4(tmp_path, monkeypatch, invalid)
    assert "NO_VERIFICADO" in html
    assert "card-green" not in html


@pytest.mark.parametrize("missing", ["separation", "dashboard_daily_report"])
def test_published_action4_ui_never_invents_missing_daily_totals(monkeypatch, tmp_path, missing):
    payload = _paper_audit()
    payload.pop(missing)
    # The shared safety predicate proves PAPER/0 only; the view additionally
    # requires its own daily-report shape before certifying any counts.
    assert dashboard.action4_audit.published_audit_is_safe(payload)
    html = _published_action4(tmp_path, monkeypatch, payload)
    assert "NO_VERIFICADO" in html
    assert "card-green" not in html


@pytest.mark.parametrize("field,bad", [
    ("executed_closed", None), ("executed_closed", False),
    ("executed_closed", 0.0), ("opened_simulated", "0"),
    ("opened_simulated", -1),
])
def test_published_action4_ui_requires_integer_simulated_counts(monkeypatch, tmp_path, field, bad):
    payload = _paper_audit()
    payload["separation"][field] = bad
    assert dashboard.action4_audit.published_audit_is_safe(payload)
    html = _published_action4(tmp_path, monkeypatch, payload)
    assert "NO_VERIFICADO" in html
    assert "card-green" not in html


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
                'realized_by_currency': {'ARS':'123.45','USD':'-5'},
            }],
        },
    )
    html = dashboard._operational_daily_section(limit_days=10)
    assert 'Actividad PAPER por jornada' in html
    assert '<thead>' in html and '<th>Fecha AR</th>' in html
    assert '2026-09-10' in html and '123,45' in html and 'USD -5,00' in html
    assert "class='positive'" in html and "class='negative'" in html
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
