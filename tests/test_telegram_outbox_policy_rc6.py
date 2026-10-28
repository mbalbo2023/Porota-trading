from datetime import datetime
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

import bn_telegram_bus as bus

TZ=ZoneInfo('America/Argentina/Buenos_Aires')
SAT=datetime(2026,9,5,12,0,tzinfo=TZ)


class Store:
    def __init__(self,path): self.path=str(path)
    def connect(self):
        c=sqlite3.connect(self.path)
        c.row_factory=sqlite3.Row
        return c


def make_store(tmp_path):
    s=Store(tmp_path/'outbox.db')
    with s.connect() as c:
        c.executescript('''
        CREATE TABLE paper_notification_outbox(
          id INTEGER PRIMARY KEY AUTOINCREMENT,event_key TEXT UNIQUE,kind TEXT,body TEXT,
          priority INTEGER,created_at TEXT,state TEXT DEFAULT 'PENDING',attempts INTEGER DEFAULT 0,
          next_attempt_at TEXT,lease_token TEXT,lease_until TEXT,sent_at TEXT,message_id TEXT,last_error TEXT DEFAULT '');
        CREATE TABLE paper_notification_worker(
          id INTEGER PRIMARY KEY CHECK(id=1),heartbeat_at TEXT,state TEXT,cooldown_until TEXT,detail TEXT);
        ''')
    return s


def insert(s,key,kind,priority):
    with s.connect() as c:
        c.execute('INSERT INTO paper_notification_outbox(event_key,kind,body,priority,created_at,next_attempt_at) VALUES(?,?,?,?,?,?)',
                  (key,kind,'body',priority,SAT.isoformat(),SAT.isoformat()))


def row(s,key):
    with s.connect() as c:
        return dict(c.execute('SELECT * FROM paper_notification_outbox WHERE event_key=?',(key,)).fetchone())


def test_weekend_routine_becomes_terminal_suppressed_without_send(tmp_path,monkeypatch):
    s=make_store(tmp_path);insert(s,'routine','CLOSING_SUMMARY',30)
    calls=[]
    w=bus.OutboxWorker(s,clock_fn=lambda:SAT,send=lambda body:calls.append(body) or '1')
    assert w.tick() is True
    assert calls==[]
    r=row(s,'routine')
    assert r['state']=='SUPPRESSED_WEEKEND'
    assert r['last_error']=='NON_BUSINESS_DAY_ROUTINE'
    # A second tick cannot replay it.
    assert w.tick() is False
    assert calls==[]


def test_weekend_priority_zero_is_delivered(tmp_path):
    s=make_store(tmp_path);insert(s,'critical','PAPER_DAILY_LOSS',0)
    calls=[]
    w=bus.OutboxWorker(s,clock_fn=lambda:SAT,send=lambda body:calls.append(body) or '42')
    assert w.tick() is True
    assert len(calls)==1
    r=row(s,'critical')
    assert r['state']=='SENT'
    assert r['message_id']=='42'
