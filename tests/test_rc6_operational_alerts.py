import sqlite3,rc6_operational_alerts as a
def db():
    c=sqlite3.connect(':memory:'); c.execute("create table paper_notification_outbox(id integer primary key autoincrement,event_key text not null unique,kind text not null,body text not null,priority integer not null,created_at text not null,state text not null default 'PENDING',attempts integer not null default 0,next_attempt_at text not null,lease_token text,lease_until text,sent_at text,message_id text,last_error text not null default '')"); return c
def test_red_once():
    c=db(); assert a.enqueue_transition(c,component='PPI_PRODUCTION_AUTH',previous_state='VERDE',state='ROJO',detail='Credenciales invalidas.',checked_at='t',last_success_at='ok',real_orders_sent=0); assert not a.enqueue_transition(c,component='PPI_PRODUCTION_AUTH',previous_state='ROJO',state='COOLDOWN',detail='x',checked_at='t2',last_success_at='ok'); assert c.execute('select count(*) from paper_notification_outbox').fetchone()[0]==1
def test_recovery_once():
    c=db(); assert a.enqueue_transition(c,component='PPI_PRODUCTION_AUTH',previous_state='ROJO',state='VERDE',detail='ok',checked_at='t'); assert 'no habilita producción real' in c.execute('select body from paper_notification_outbox').fetchone()[0]
def test_noncritical_silent(): assert not a.enqueue_transition(db(),component='PAPER_FOCUS_COVERAGE',previous_state='VERDE',state='ROJO',detail='x',checked_at='t')
def test_sanitize():
    c=db(); a.enqueue_transition(c,component='PPI_PRODUCTION_AUTH',previous_state='VERDE',state='ROJO',detail='token=abcdef012345 secret=xyzxyzxyz',checked_at='t'); body=c.execute('select body from paper_notification_outbox').fetchone()[0]; assert 'abcdef012345' not in body and 'xyzxyzxyz' not in body
