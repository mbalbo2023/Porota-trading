"""Reloj de salidas PAPER sin red ni IA; intención persistente separada del fill."""
from dataclasses import dataclass
from decimal import Decimal

from bs_instrument_contracts import aware_datetime, decimal_value


def init_schema(store):
    with store.connect() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS paper_exit_intents(
          paper_id TEXT PRIMARY KEY REFERENCES paper_positions(paper_id),
          state TEXT NOT NULL, cause TEXT, due_at TEXT, blocked_reason TEXT NOT NULL,
          supervised_at TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS paper_supervisor_state(
          id INTEGER PRIMARY KEY CHECK(id=1), heartbeat_at TEXT NOT NULL,
          state TEXT NOT NULL, detail TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS paper_exit_reader_state(
          id INTEGER PRIMARY KEY CHECK(id=1), heartbeat_at TEXT NOT NULL,
          state TEXT NOT NULL, detail TEXT NOT NULL);
        """)


def admission_error(store, at, max_silence_seconds=20):
    with store.connect() as c:
        health = c.execute("SELECT * FROM paper_supervisor_state WHERE id=1").fetchone()
        if not health or health["state"] != "RUNNING":
            return "EXIT_SUPERVISOR_UNAVAILABLE"
        try:
            age = (aware_datetime(at) - aware_datetime(health["heartbeat_at"])).total_seconds()
            if not 0 <= age <= max_silence_seconds:
                return "EXIT_SUPERVISOR_STALE"
        except (ValueError, TypeError):
            return "EXIT_SUPERVISOR_INVALID_CLOCK"
        reader = c.execute("SELECT * FROM paper_exit_reader_state WHERE id=1").fetchone()
        if not reader or reader["state"] != "READY":
            return "EXIT_READER_UNAVAILABLE"
        try:
            age = (aware_datetime(at) - aware_datetime(reader["heartbeat_at"])).total_seconds()
            if not 0 <= age <= max_silence_seconds:
                return "EXIT_READER_STALE"
        except (ValueError, TypeError):
            return "EXIT_READER_INVALID_CLOCK"
        if c.execute("""SELECT 1 FROM paper_positions p LEFT JOIN paper_exit_intents x USING(paper_id)
          WHERE p.status='OPEN' AND (x.state IS NULL OR x.state<>'OPEN') LIMIT 1""").fetchone():
            return "OPEN_POSITION_NEEDS_SUPERVISION_OR_EXIT"
    return ""


@dataclass(frozen=True)
class Verdict:
    paper_id: str
    state: str
    cause: str | None
    reason: str


class PositionExitSupervisor:
    def __init__(self, broker, *, clock_fn, session_policy=None, max_hold_minutes=180,
                 close_fn=None):
        self.broker, self.store = broker, broker.store
        self.clock_fn, self.session_policy = clock_fn, session_policy
        if max_hold_minutes <= 0:
            raise ValueError("Permanencia máxima debe ser positiva")
        self.max_hold_minutes = max_hold_minutes
        self.close_fn = close_fn or broker._close

    def heartbeat(self, at, state="RUNNING", detail=""):
        with self.store.connect() as c:
            c.execute("INSERT OR REPLACE INTO paper_supervisor_state VALUES(1,?,?,?)", (at,state,detail))

    def _persist(self, p, verdict, at, attempted=False):
        with self.store.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            actual = c.execute("SELECT status FROM paper_positions WHERE paper_id=?", (p["paper_id"],)).fetchone()
            if not actual or actual[0] != "OPEN":
                return Verdict(p["paper_id"], "CLOSED", verdict.cause, "Fill ya registrado")
            previous = c.execute("SELECT * FROM paper_exit_intents WHERE paper_id=?", (p["paper_id"],)).fetchone()
            due = previous["due_at"] if previous else None
            cause = (previous["cause"] if previous else None) or verdict.cause
            c.execute("""INSERT INTO paper_exit_intents VALUES(?,?,?,?,?,?,?)
              ON CONFLICT(paper_id) DO UPDATE SET state=excluded.state,cause=excluded.cause,
              due_at=excluded.due_at,blocked_reason=excluded.blocked_reason,
              supervised_at=excluded.supervised_at,attempts=excluded.attempts""",
              (p["paper_id"],verdict.state,cause,due or (at if cause else None),verdict.reason,at,
               (previous["attempts"] if previous else 0) + int(attempted)))
            if not previous or (previous["state"],previous["cause"]) != (verdict.state,cause):
                c.execute("INSERT INTO paper_events VALUES(NULL,?,?,?,?,?)",
                          (at,"PRODUCTION_PAPER","PAPER_EXIT_STATE",p["paper_id"],
                           f"{verdict.state}; {cause or 'NONE'}; {verdict.reason}"))
        return verdict

    def supervise(self, p, q, at):
        with self.store.connect() as c:
            previous = c.execute("SELECT * FROM paper_exit_intents WHERE paper_id=?", (p["paper_id"],)).fetchone()
        cause = previous["cause"] if previous else None
        def verdict(state, reason):
            return self._persist(p, Verdict(p["paper_id"],state,cause,reason), at)
        try:
            age = (aware_datetime(at) - aware_datetime(p["opened_at"])).total_seconds() / 60
            if age < 0:
                return verdict("WATCH_INVALID_CLOCK", "Apertura posterior al reloj")
            if not cause and self.session_policy and self.session_policy.exit_due(p, at):
                cause = "EOD_PAPER"
            if not cause and age >= self.max_hold_minutes:
                cause = "MAX_HOLD_PAPER"
        except (ValueError, TypeError):
            return verdict("WATCH_INVALID_CLOCK", "Fecha de apertura inválida")
        prefix = "EXIT_PENDING_" if cause else "WATCH_"
        if self.session_policy:
            error = self.session_policy.execution_error(p, at)
            if error:
                return verdict(prefix + "MARKET_CLOSED", error)
        if q is None:
            return verdict(prefix + "NO_QUOTE", "Sin libro del instrumento")
        try:
            identity = q.monetary_identity()
        except (ValueError, TypeError):
            return verdict(prefix + "IDENTITY_MISMATCH", "Moneda o mercado sin confirmar")
        try:
            if ((q.symbol,q.asset_class,q.settlement,*identity) !=
                    tuple(p[k] for k in ("symbol","asset_class","settlement","currency","market"))):
                return verdict(prefix + "IDENTITY_MISMATCH", "Libro incompatible con el fill original")
            error = q.time_error(at, max_age_seconds=self.broker.quote_max_age_seconds)
            if error:
                return verdict(prefix + "STALE_QUOTE", error)
            if aware_datetime(q.book_at) < aware_datetime(p["opened_at"]):
                return verdict(prefix + "STALE_QUOTE", "Libro anterior a la apertura")
            bid = decimal_value(q.bid, "bid", positive=True)
            ask = decimal_value(q.ask, "ask", positive=True)
            size = decimal_value(q.bid_size, "bid_size", nonnegative=True)
            if ask < bid:
                return verdict(prefix + "INVALID_BOOK", "Libro cruzado")
        except (ValueError, TypeError):
            return verdict(prefix + "NO_LIQUIDITY", "Puntas inválidas o sin precio")
        # El stop puede dispararse con precio y cantidad cero: decide salir y
        # conserva la intención; la ejecución todavía exige profundidad.
        if not cause and bid <= Decimal(p["stop_price"]):
            cause = "STOP_PAPER"
        elif not cause and bid >= Decimal(p["target_price"]):
            cause = "TAKE_PROFIT_PAPER"
        if not cause:
            return verdict("OPEN", "Dentro de parámetros")
        if size * self.broker.participation <= 0:
            return verdict("EXIT_PENDING_NO_LIQUIDITY", "Sin profundidad de salida")
        verdict("EXIT_DUE", "Salida decidida; pendiente de fill")
        reason = "El ejecutor no confirmó un cierre"
        try:
            self.close_fn(p, q, cause, as_of=at)
            # La devolución del callback no sustituye la evidencia del ledger.
            with self.store.connect() as c:
                actual = c.execute("SELECT status FROM paper_positions WHERE paper_id=?", (p["paper_id"],)).fetchone()
            if actual and actual[0] == "CLOSED":
                return Verdict(p["paper_id"],"CLOSED",cause,"Venta simulada registrada")
            with self.store.connect() as c:
                from cd_spot_ledger import partition
                row = c.execute('SELECT * FROM paper_positions WHERE paper_id=?',(p['paper_id'],)).fetchone()
                remaining = partition(c,row)[0] if row else None
            if remaining and Decimal(remaining['quantity']) < Decimal(p['quantity']):
                return Verdict(p['paper_id'],'EXIT_PARTIAL',cause,
                               'Fill parcial registrado; remanente '+remaining['quantity'])
        except Exception as exc:
            reason = "Error del ejecutor: " + type(exc).__name__
        return self._persist(p, Verdict(p["paper_id"],"EXIT_PENDING_EXECUTION",cause,reason),at,attempted=True)

    def tick(self, quotes=None):
        at = aware_datetime(self.clock_fn()).isoformat()
        settlement_error = ''
        try:
            self.broker.settle_cauciones(at)
        except ValueError:
            # Caución no conciliada: no acreditar ni admitir entradas, pero
            # conservar la supervisión/venta de tenencias spot válidas.
            settlement_error = 'CAUCION_SETTLEMENT_BLOCKED; requiere conciliación. '
        if self.broker.daily_risk:
            self.broker.daily_risk.evaluate(at)
        verdicts = []
        for p in self.store.open_positions():
            key = tuple(p[k] for k in ("symbol","asset_class","settlement","currency","market"))
            try:
                q = quotes.get(key) if quotes is not None else self.store.latest_quote(p)
                verdicts.append(self.supervise(p,q,at))
            except Exception as exc:
                verdicts.append(self._persist(p,Verdict(p["paper_id"],"WATCH_ERROR",None,
                                                        type(exc).__name__),at))
        self.heartbeat(at,state='DEGRADED' if settlement_error else 'RUNNING',
                       detail=settlement_error+f"{len(verdicts)} posiciones supervisadas; sin red ni IA")
        return verdicts
