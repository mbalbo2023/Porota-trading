from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

import rc6_operational_alerts as a


def db(path=':memory:'):
    c = sqlite3.connect(path)
    c.execute('''CREATE TABLE paper_notification_outbox (
        id INTEGER PRIMARY KEY AUTOINCREMENT, event_key TEXT NOT NULL UNIQUE,
        kind TEXT NOT NULL, body TEXT NOT NULL, priority INTEGER NOT NULL,
        created_at TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'PENDING',
        next_attempt_at TEXT NOT NULL)''')
    a.init_schema(c)
    return c
def test_red_once():
    c=db(); assert a.enqueue_transition(c,component='PPI_PRODUCTION_AUTH',previous_state='VERDE',state='ROJO',detail='Credenciales invalidas.',checked_at='t',last_success_at='ok',real_orders_sent=0); assert not a.enqueue_transition(c,component='PPI_PRODUCTION_AUTH',previous_state='ROJO',state='COOLDOWN',detail='x',checked_at='t2',last_success_at='ok'); assert c.execute('select count(*) from paper_notification_outbox').fetchone()[0]==1
def test_recovery_once():
    c=db(); assert a.enqueue_transition(c,component='PPI_PRODUCTION_AUTH',previous_state='ROJO',state='VERDE',detail='ok',checked_at='t'); assert 'no habilita producción real' in c.execute('select body from paper_notification_outbox').fetchone()[0]
def test_noncritical_silent(): assert not a.enqueue_transition(db(),component='PAPER_FOCUS_COVERAGE',previous_state='VERDE',state='ROJO',detail='x',checked_at='t')
def test_sanitize():
    c=db(); a.enqueue_transition(c,component='PPI_PRODUCTION_AUTH',previous_state='VERDE',state='ROJO',detail='token=abcdef012345 secret=xyzxyzxyz',checked_at='t'); body=c.execute('select body from paper_notification_outbox').fetchone()[0]; assert 'abcdef012345' not in body and 'xyzxyzxyz' not in body


AT = datetime(2026, 11, 26, 16, tzinfo=timezone.utc)


def market_cycle(connection, minute, state, previous_state=None):
    return a.enqueue_transition(connection, component=a.MARKETDATA_COMPONENT,
                                previous_state=previous_state, state=state,
                                detail='1/1 instrumentos con cotización útil.' if state == 'VERDE' else 'sin cotización',
                                checked_at=(AT + timedelta(minutes=minute)).isoformat(),
                                last_success_at=AT.isoformat(), source='PPI Producción')


def outbox(connection):
    return connection.execute('SELECT event_key,kind,body FROM paper_notification_outbox ORDER BY id').fetchall()


def test_marketdata_flapping_keeps_one_open_notice_until_two_distinct_valid_cycles():
    with db() as connection:
        assert market_cycle(connection, 0, 'ROJO')
        assert not market_cycle(connection, 60, 'VERDE', 'ROJO')
        assert not market_cycle(connection, 61, 'RATE_LIMIT', 'VERDE')
        assert not market_cycle(connection, 62, 'VERDE', 'RATE_LIMIT')
        assert not market_cycle(connection, 63, 'AMARILLO', 'VERDE')
        assert not market_cycle(connection, 64, 'VERDE', 'AMARILLO')
        assert not market_cycle(connection, 64, 'VERDE', 'VERDE')
        assert market_cycle(connection, 65, 'VERDE', 'VERDE')
        assert not market_cycle(connection, 66, 'VERDE', 'VERDE')
        red, recovered = outbox(connection)
        assert red[1] == 'OPERATIONAL_HEALTH_RED'
        assert recovered[1] == 'OPERATIONAL_HEALTH_RECOVERY'
        assert red[0].rsplit(':', 1)[1] == recovered[0].rsplit(':', 1)[1]
        assert 'DATOS PPI ESTABLES EN LA MUESTRA' in recovered[2]
        assert '1/1 instrumentos' in recovered[2]
        assert market_cycle(connection, 67, 'ROJO', 'VERDE')
        assert outbox(connection)[-1][0] != red[0]


def test_marketdata_migrates_open_legacy_alert_and_keeps_evidence_across_restart(tmp_path):
    path = tmp_path / 'paper.db'
    connection = db(path)
    with connection:
        key = 'operational-health:PPI_PRODUCTION_MARKETDATA:BAD:old'
        assert a.enqueue(connection, key, 'OPERATIONAL_HEALTH_RED', 'legacy', AT.isoformat())
        assert not market_cycle(connection, 1, 'ROJO', 'ROJO')
        assert not market_cycle(connection, 2, 'VERDE', 'ROJO')
    connection.close()
    with sqlite3.connect(path) as recovered:
        assert market_cycle(recovered, 3, 'VERDE', 'VERDE')
        assert [item[0] for item in outbox(recovered)] == [key, key.replace(':BAD:', ':GOOD:')]


def test_marketdata_failed_queue_write_leaves_incident_retriable(monkeypatch):
    def fail(*_args, **_kwargs):
        raise sqlite3.OperationalError('fixture queue failure')

    with db() as connection:
        with monkeypatch.context() as patch:
            patch.setattr(a, 'enqueue', fail)
            with pytest.raises(sqlite3.OperationalError, match='fixture queue failure'):
                market_cycle(connection, 0, 'ROJO')
        assert connection.execute('SELECT * FROM paper_marketdata_alert').fetchone() is None
        assert market_cycle(connection, 1, 'ROJO', 'ROJO')
        assert len(outbox(connection)) == 1
