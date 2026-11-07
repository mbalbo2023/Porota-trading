import sqlite3,rc6_operational_alerts as a
import pytest
def db():
    c=sqlite3.connect(':memory:'); c.execute("create table paper_notification_outbox(id integer primary key autoincrement,event_key text not null unique,kind text not null,body text not null,priority integer not null,created_at text not null,state text not null default 'PENDING',attempts integer not null default 0,next_attempt_at text not null,lease_token text,lease_until text,sent_at text,message_id text,last_error text not null default '')"); return c
def test_red_once():
    c=db(); assert a.enqueue_transition(c,component='PPI_PRODUCTION_AUTH',previous_state='VERDE',state='ROJO',detail='Credenciales invalidas.',checked_at='t',last_success_at='ok',real_orders_sent=0); assert not a.enqueue_transition(c,component='PPI_PRODUCTION_AUTH',previous_state='ROJO',state='COOLDOWN',detail='x',checked_at='t2',last_success_at='ok'); assert c.execute('select count(*) from paper_notification_outbox').fetchone()[0]==1
def test_recovery_once():
    c=db(); assert a.enqueue_transition(c,component='PPI_PRODUCTION_AUTH',previous_state='ROJO',state='VERDE',detail='ok',checked_at='t'); assert 'no habilita producción real' in c.execute('select body from paper_notification_outbox').fetchone()[0]
def test_noncritical_silent(): assert not a.enqueue_transition(db(),component='PAPER_FOCUS_COVERAGE',previous_state='VERDE',state='ROJO',detail='x',checked_at='t')
def test_sanitize():
    c=db(); a.enqueue_transition(c,component='PPI_PRODUCTION_AUTH',previous_state='VERDE',state='ROJO',detail='token=abcdef012345 secret=xyzxyzxyz',checked_at='t'); body=c.execute('select body from paper_notification_outbox').fetchone()[0]; assert 'abcdef012345' not in body and 'xyzxyzxyz' not in body


@pytest.mark.parametrize("counter", [None, False, True, "0", "oops", 0.0, 1.0, -1, [], {}])
@pytest.mark.parametrize("previous,state", [("VERDE", "ROJO"), ("ROJO", "VERDE")])
def test_invalid_counter_in_critical_alert_or_recovery_is_unknown(counter, previous, state):
    """Counter malformation must not render zero or suppress the notification."""
    c = db()
    assert a.enqueue_transition(
        c, component="PPI_PRODUCTION_MARKETDATA", previous_state=previous,
        state=state, detail="PPI sin lectura", checked_at="2026-11-07T12:00:00Z",
        real_orders_sent=counter,
    )
    rows = c.execute(
        "SELECT body,event_key,kind FROM paper_notification_outbox"
    ).fetchall()
    assert len(rows) == 1
    body, key, kind = rows[0]
    assert "Órdenes reales enviadas: NO_VERIFICADO" in body
    assert "Órdenes reales enviadas: 0" not in body
    assert key.startswith("operational-health:PPI_PRODUCTION_MARKETDATA:")
    assert kind in {"OPERATIONAL_HEALTH_RED", "OPERATIONAL_HEALTH_RECOVERY"}


def test_missing_default_counter_does_not_claim_zero():
    c = db()
    assert a.enqueue_transition(
        c, component="PPI_PRODUCTION_HISTORY", previous_state="VERDE",
        state="ROJO", detail="Sin comprobación", checked_at="t",
    )
    body = c.execute("SELECT body FROM paper_notification_outbox").fetchone()[0]
    assert "Órdenes reales enviadas: NO_VERIFICADO" in body


@pytest.mark.parametrize("counter,expected", [(0, "0"), (2, "2")])
def test_exact_integer_counter_is_reported_without_coercion(counter, expected):
    c = db()
    assert a.enqueue_transition(
        c, component="PPI_PRODUCTION_AUTH", previous_state="VERDE",
        state="ROJO", detail="Error", checked_at="t", real_orders_sent=counter,
    )
    body = c.execute("SELECT body FROM paper_notification_outbox").fetchone()[0]
    assert f"Órdenes reales enviadas: {expected}" in body


def test_observer_missing_state_row_does_not_invent_zero_in_telegram(tmp_path):
    """The actual observer->outbox path handles a missing SQLite state row."""
    from be_paper_engine import PaperStore
    import bf_production_paper_observer as observer

    store = PaperStore(str(tmp_path / "synthetic-observer.db"))
    observer._support_schema(store)
    with store.connect() as connection:
        connection.execute("DELETE FROM observer_state WHERE id=1")

    observer._health(
        store, "PPI_PRODUCTION_MARKETDATA", "ROJO", "Sin datos de fuente", "PPI",
    )
    with store.connect() as connection:
        rows = connection.execute(
            "SELECT body FROM paper_notification_outbox WHERE kind='OPERATIONAL_HEALTH_RED'"
        ).fetchall()
    assert len(rows) == 1
    assert "Órdenes reales enviadas: NO_VERIFICADO" in rows[0][0]
