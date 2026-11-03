"""Reproducible offline regressions for RC6 safety and evidence findings."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import _version
import ao_startup_gate as startup
import ar_telegram_commands as commands
import bh_paper_gemini as gemini
import c_ppi_client as ppi
import ck_policy_gate_hf6 as policy
import cy_market_source_arbitration_hf6 as arbitration
import es_policy_context_rc6 as context
import iol_shadow_observation_rc6 as iol
import rc6_fast_functional_health as health
import rc6_ppi_iol_reconciliation_rc6 as reconciliation


def test_product_release_refuses_real_start_and_sdk_choke_points(monkeypatch, tmp_path):
    assert _version.MODE == 'PRODUCTION_PAPER'
    assert _version.REAL_ORDER_CAPABILITY == 'BLOCKED'
    monkeypatch.setattr(startup, 'STARTUP_STATE_PATH', str(tmp_path / 'start.json'))
    assert startup.autorizar(startup.MODO_REAL, origen='OFFLINE_FIXTURE')['ok'] is False
    (tmp_path / 'start.json').write_text(json.dumps({'modo': startup.MODO_REAL}))
    assert startup._recargar_desde_archivo() is False
    monkeypatch.setattr(startup, 'STARTUP_REQUIRE_AUTH', False)
    monkeypatch.setenv('ENVIRONMENT', 'PRODUCTION')
    assert startup.esperar_autorizacion() == startup.MODO_DETENIDO
    monkeypatch.setattr(startup, 'estado_actual', lambda: {'modo': startup.MODO_REAL})
    monkeypatch.setattr(startup, 'registrar_paso', lambda *a: None)
    assert startup.interceptar_orden('GGAL', 1, 100, 'COMPRA')['simulada']
    wrapper = ppi.ResilientPPIClient.__new__(ppi.ResilientPPIClient)
    wrapper.client = SimpleNamespace(orders=SimpleNamespace(
        confirm=lambda *a: pytest.fail('SDK confirm'),
        cancel_order=lambda *a: pytest.fail('SDK cancel'),
        budget=lambda *a: pytest.fail('SDK budget')))
    wrapper.account_number = 'OFFLINE'
    assert wrapper.confirm_order('OFFLINE', 1, 100, 'GGAL', [])['simulada'] is True
    for operation in (
        lambda: wrapper.cancel_order('1'),
        lambda: wrapper.budget_order('OFFLINE', 1, 100, 'GGAL'),
        lambda: wrapper.sell_at_market('GGAL', 1),
    ):
        with pytest.raises(RuntimeError, match='REAL_ORDER_CAPABILITY_BLOCKED'):
            operation()


def test_paper_restart_authorizes_simulation_before_releasing_halt(monkeypatch):
    import p_risk_guardian as guardian
    events = []
    monkeypatch.setattr(commands, '_enviar', lambda *a, **k: None)
    monkeypatch.setattr(commands, '_registrar', lambda *a, **k: None)
    monkeypatch.setattr(guardian, 'clear_halt', lambda: events.append('clear'))
    monkeypatch.setattr(startup, 'esta_en_simulacion', lambda: False)
    monkeypatch.setattr(startup, 'autorizar', lambda modo, **kwargs:
                        (events.append(modo) or {'ok': True}))
    assert commands.ejecutar_rearranque(None)['ok'] is True
    assert events == [startup.MODO_SIMULACION, 'clear']
    events.clear()
    monkeypatch.setattr(startup, 'autorizar', lambda *a, **k:
                        {'ok': False, 'motivo': 'BLOCKED'})
    assert commands.ejecutar_rearranque(None)['ok'] is False
    assert events == []


def test_sector_binding_recounts_after_another_writer_commits(tmp_path, monkeypatch):
    db = tmp_path / 'paper.db'
    with sqlite3.connect(db) as c:
        c.execute('CREATE TABLE paper_positions (paper_id TEXT,symbol TEXT,asset_class TEXT,market TEXT,currency TEXT,settlement TEXT,features_json TEXT,status TEXT)')
        c.execute('INSERT INTO paper_positions VALUES (?,?,?,?,?,?,?,?)',
                  ('1', 'GGAL', 'ACCIONES', 'BYMA', 'ARS', 'A-24HS', '{}', 'OPEN'))
    candidate = {'symbol': 'BBAR', 'family': 'ACCIONES', 'market': 'BYMA',
                 'currency': 'ARS', 'settlement': 'A-24HS'}
    with sqlite3.connect(db) as first:
        first.row_factory = sqlite3.Row
        preview = context.locked_sector_context(first, candidate)
    assert policy.sector_verdict(preview['sectors'], preview['candidate_sector'])['state'] == 'PASS'
    with sqlite3.connect(db) as other:
        other.execute('INSERT INTO paper_positions VALUES (?,?,?,?,?,?,?,?)',
                      ('2', 'BMA', 'ACCIONES', 'BYMA', 'ARS', 'A-24HS', '{}', 'OPEN'))
    with sqlite3.connect(db) as locked:
        locked.row_factory = sqlite3.Row
        locked.execute('BEGIN IMMEDIATE')
        recheck = context.locked_sector_context(locked, candidate)
        verdict = policy.sector_verdict(recheck['sectors'], recheck['candidate_sector'])
    assert verdict['reason'] == 'SECTOR_CONCENTRATION_LIMIT_FINANCIERO'
    assert verdict['open_positions'] == 2
    assert 'locked_sector_context(c,' in Path('be_paper_engine.py').read_text()


@pytest.mark.parametrize('value', [float('nan'), float('inf'), float('-inf')])
def test_nonfinite_shadow_numbers_do_not_match_or_promote(value):
    result = reconciliation.reconcile({'last': value}, {'last': 100})
    assert result['fields']['last']['state'] != 'MATCH'
    assert result['shadow_promotion'] is False
    assert result['contract_state'] == 'BLOCKED_INVALID_NUMERIC'
    assert arbitration.compare_background_numeric(value, 100, tolerance_pct=2) == 'BACKGROUND_COMPARISON_INCOMPLETE'
    assert iol._quote_summary({'last': value})['last'] is None


def test_zero_base_disagreement_is_not_match():
    result = reconciliation.reconcile({'last': 0}, {'last': 100})
    assert result['fields']['last']['state'] == 'DIVERGENCE'
    assert result['contract_state'] == 'BLOCKED_CONFLICT'


@pytest.mark.parametrize('score', ['NaN', True])
def test_gemini_nonfinite_or_boolean_score_fails_closed(score):
    with pytest.raises(ValueError, match='score Gemini'):
        gemini.GeminiPaperGate._parse(
            SimpleNamespace(text=json.dumps({'decision':'APPROVE','veto':False,'score':score})),
            'offline-fixture')


@pytest.mark.parametrize('real_orders,heartbeat', [
    (None, '2026-10-14T12:00:00+00:00'),
    (0, '2026-10-14T12:01:00+00:00'),
    (0, '2026-10-14T12:00:00'),
])
def test_fast_health_unknown_count_or_clock_is_red(tmp_path, real_orders, heartbeat):
    db = tmp_path / 'observer.db'
    with sqlite3.connect(db) as c:
        c.execute('CREATE TABLE observer_state (id INTEGER PRIMARY KEY, mode TEXT, process_state TEXT, session_state TEXT, ppi_auth TEXT, real_orders_sent INTEGER, heartbeat_at TEXT, last_market_data_at TEXT)')
        c.execute('INSERT INTO observer_state VALUES(1,?,?,?,?,?,?,?)',
                  ('PRODUCTION_PAPER','RUNNING','OPEN','OK',real_orders,heartbeat,heartbeat))
    result = health.probe(str(db), now=datetime(2026,10,14,12,0,tzinfo=timezone.utc))
    assert result['status'] == 'RED'
