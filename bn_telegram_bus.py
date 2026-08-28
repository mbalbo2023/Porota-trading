"""Outbox PAPER transaccional. No envía órdenes ni bloquea el reloj financiero.

El aviso se confirma junto al evento en SQLite. Entrega al menos una vez:
un corte después del ACK remoto y antes del COMMIT puede repetir un mensaje,
siempre con el mismo ID. Nunca se repite una operación para reenviar un aviso.
"""
import json
import os
import uuid
from datetime import timedelta
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from bs_instrument_contracts import aware_datetime


def init_schema(store):
    with store.connect() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS paper_notification_outbox(
          id INTEGER PRIMARY KEY AUTOINCREMENT, event_key TEXT NOT NULL UNIQUE,
          kind TEXT NOT NULL, body TEXT NOT NULL, priority INTEGER NOT NULL,
          created_at TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'PENDING',
          attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at TEXT NOT NULL,
          lease_token TEXT, lease_until TEXT, sent_at TEXT, message_id TEXT,
          last_error TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS paper_notification_worker(
          id INTEGER PRIMARY KEY CHECK(id=1), heartbeat_at TEXT NOT NULL,
          state TEXT NOT NULL, cooldown_until TEXT, detail TEXT NOT NULL);
        CREATE TRIGGER IF NOT EXISTS paper_events_notify_v17 AFTER INSERT ON paper_events
        WHEN NEW.event_type IN ('PAPER_FILLED_BUY','PAPER_FILLED_SELL',
          'PAPER_CAUCION_PLACED','PAPER_CAUCION_MATURED','PAPER_DAILY_LOSS')
          OR (NEW.event_type='PAPER_EXIT_STATE' AND NEW.detail LIKE 'EXIT_PENDING_%')
        BEGIN
          INSERT OR IGNORE INTO paper_notification_outbox
            (event_key,kind,body,priority,created_at,next_attempt_at)
          VALUES(CASE WHEN NEW.event_type='PAPER_EXIT_STATE' THEN 'paper-exit:' || NEW.paper_id
                      ELSE 'paper-event:' || NEW.id END,
            NEW.event_type,'POROTA · SIMULACIÓN · SIN ÓRDENES REALES' || char(10) ||
              NEW.event_type || char(10) || NEW.detail || char(10) ||
              COALESCE(NEW.paper_id,'') || ' ' || COALESCE(
                (SELECT currency FROM paper_positions WHERE paper_id=NEW.paper_id),
                (SELECT currency FROM paper_cauciones WHERE paper_id=NEW.paper_id),'') ||
              char(10) || NEW.event_at,
            CASE WHEN NEW.event_type='PAPER_DAILY_LOSS' THEN 0
                 WHEN NEW.event_type='PAPER_EXIT_STATE' THEN 10 ELSE 20 END,
            NEW.event_at,NEW.event_at);
        END;
        """)


def enqueue(c, key, kind, body, at, priority=30):
    """El llamador aporta SU transacción; no hay commit ni captura de errores."""
    if not key or not body or len(body.encode('utf-16-le')) // 2 > 3500:
        raise ValueError("Aviso vacío o demasiado largo")
    return c.execute("""INSERT OR IGNORE INTO paper_notification_outbox
      (event_key,kind,body,priority,created_at,next_attempt_at) VALUES(?,?,?,?,?,?)""",
      (key,kind,body,priority,at,at)).rowcount == 1


class DeliveryError(Exception):
    def __init__(self, code, *, retry_after=0, permanent=False):
        super().__init__(str(code))
        self.code, self.retry_after, self.permanent = str(code), retry_after, permanent


class TelegramTransport:
    def __init__(self, token, chat, opener=urlopen):
        self.token, self.chat, self.opener = token, chat, opener

    def __call__(self, body):
        data = json.dumps({"chat_id": self.chat, "text": body}).encode()
        request = Request(f"https://api.telegram.org/bot{self.token}/sendMessage",
                          data=data, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with self.opener(request, timeout=15) as response:
                result = json.loads(response.read())
        except HTTPError as exc:
            try:
                result = json.loads(exc.read())
            except (ValueError, OSError):
                result = {}
            result.update(ok=False, error_code=exc.code)
        if result.get("ok") is not True:
            code = result.get("error_code", "INVALID_RESPONSE")
            retry = result.get("parameters", {}).get("retry_after", 0)
            retry = retry if isinstance(retry, int) and retry >= 0 else 0
            raise DeliveryError(code, retry_after=retry,
                                permanent=code in {400,401,403,404})
        message_id = result.get("result", {}).get("message_id")
        if not isinstance(message_id, int) or isinstance(message_id, bool) or message_id <= 0:
            raise DeliveryError("MISSING_MESSAGE_ID")
        return str(message_id)


class OutboxWorker:
    def __init__(self, store, *, clock_fn, send=None, max_attempts=8, lease_seconds=120):
        self.store, self.clock_fn, self.send = store, clock_fn, send
        if max_attempts < 1 or lease_seconds < 60:
            raise ValueError("Política de entrega inválida")
        self.max_attempts, self.lease_seconds = max_attempts, lease_seconds

    def status(self, state, detail=""):
        at = aware_datetime(self.clock_fn()).isoformat()
        with self.store.connect() as c:
            c.execute("""INSERT INTO paper_notification_worker VALUES(1,?,?,NULL,?)
              ON CONFLICT(id) DO UPDATE SET heartbeat_at=excluded.heartbeat_at,
                state=excluded.state,detail=excluded.detail""", (at,state,detail))

    def claim(self):
        at = aware_datetime(self.clock_fn())
        with self.store.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            worker = c.execute("SELECT * FROM paper_notification_worker WHERE id=1").fetchone()
            if worker and worker['cooldown_until'] and aware_datetime(worker['cooldown_until']) > at:
                return None
            # Un solo envío en vuelo para esta cola/bot, incluso con dos workers.
            if c.execute("""SELECT 1 FROM paper_notification_outbox WHERE state='SENDING'
              AND julianday(lease_until)>julianday(?) LIMIT 1""", (at.isoformat(),)).fetchone():
                return None
            c.execute("""UPDATE paper_notification_outbox SET state='PENDING',lease_token=NULL,
              lease_until=NULL,last_error='DELIVERY_UNKNOWN_AFTER_RESTART'
              WHERE state='SENDING' AND julianday(lease_until)<=julianday(?)""", (at.isoformat(),))
            c.execute("""UPDATE paper_notification_outbox SET state='DEAD'
              WHERE state='PENDING' AND attempts>=?""", (self.max_attempts,))
            row = c.execute("""SELECT * FROM paper_notification_outbox
              WHERE state='PENDING' AND julianday(next_attempt_at)<=julianday(?)
              ORDER BY priority,id LIMIT 1""", (at.isoformat(),)).fetchone()
            if not row:
                return None
            token = uuid.uuid4().hex
            c.execute("""UPDATE paper_notification_outbox SET state='SENDING',attempts=attempts+1,
              lease_token=?,lease_until=? WHERE id=?""",
              (token,(at+timedelta(seconds=self.lease_seconds)).isoformat(),row['id']))
            return dict(c.execute("SELECT * FROM paper_notification_outbox WHERE id=?",(row['id'],)).fetchone())

    def tick(self):
        if self.send is None:
            self.status("NOT_CONFIGURED", "Avisos conservados; falta canal configurado")
            return False
        self.status("RUNNING")
        row = self.claim()
        if not row:
            return False
        message_id, error = None, None
        try:
            message_id = self.send(row['body'] + "\nID: " + row['event_key'])
            if not message_id:
                raise DeliveryError("MISSING_MESSAGE_ID")
        except DeliveryError as exc:
            error = exc
        except Exception as exc:
            # No registrar URLs con token ni dar por entregado un timeout.
            error = DeliveryError(type(exc).__name__)
        at = aware_datetime(self.clock_fn())
        delay = max(1, error.retry_after if error else 0,
                    min(3600, 5 * 2 ** min(row['attempts'] - 1, 10)) if error else 1)
        state = 'SENT' if error is None else (
            'DEAD' if error.permanent or row['attempts'] >= self.max_attempts else 'PENDING')
        with self.store.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            updated = c.execute("""UPDATE paper_notification_outbox SET state=?,next_attempt_at=?,
              sent_at=?,message_id=?,last_error=?,lease_token=NULL,lease_until=NULL
              WHERE id=? AND state='SENDING' AND lease_token=?""",
              (state,(at+timedelta(seconds=delay)).isoformat(),at.isoformat() if not error else None,
               message_id,error.code if error else '',row['id'],row['lease_token']))
            if updated.rowcount:
                cooldown = at + timedelta(seconds=delay if error and error.code=='429' else 1)
                c.execute("""UPDATE paper_notification_worker SET cooldown_until=?,heartbeat_at=?,
                  state=?,detail=? WHERE id=1""", (cooldown.isoformat(),at.isoformat(),
                  'DELIVERY_ERROR' if error else 'RUNNING',error.code if error else 'ACK confirmado'))
                if row['kind']=='CLOSING_SUMMARY':
                    c.execute('''UPDATE operational_jobs SET state=?,last_run_at=?,
                      last_success_at=COALESCE(?,last_success_at),detail=? WHERE job_key=?''',
                      ('VERDE' if not error else 'ROJO',at.isoformat(),at.isoformat() if not error else None,
                       'ACK de Telegram confirmado' if not error else state+': '+error.code,row['event_key']))
        return bool(updated.rowcount)


def run_worker(store, stop, *, clock_fn):
    # Proceso distinto del lector PPI: no comparte su interceptor HTTP.
    token, chat = os.getenv('TELEGRAM_BOT_TOKEN',''), os.getenv('TELEGRAM_CHAT_ID','')
    sender = TelegramTransport(token,chat) if token and chat else None
    worker = OutboxWorker(store,clock_fn=clock_fn,send=sender)
    try:
        while not stop.is_set():
            try:
                worker.tick()
            except Exception as exc:
                worker.status('ERROR',type(exc).__name__)
            stop.wait(1)
    finally:
        worker.status('STOPPED')
