"""Corte diario PAPER por moneda, persistente y sin acceso a red.

Base de medianoche reconstruida del ledger, nunca la riqueza al reiniciar.
Sin marca de cierre anterior conciliada para un carry spot: bloquea el día.
La caución se devenga por contrato; su principal no es ganancia ni caja libre.
"""
from datetime import datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from bs_instrument_contracts import aware_datetime, cash_currency, decimal_value
from bt_caucion_paper import money

TZ = ZoneInfo('America/Argentina/Buenos_Aires')
ZERO = Decimal('0')


def loss_limit_crossed(daily_pnl, realized_today, budget):
    """Mismo umbral inclusivo para PAPER y replay; 1% se convierte antes."""
    budget = decimal_value(budget, 'presupuesto diario', positive=True)
    realized_today = decimal_value(realized_today, 'realizado hoy')
    daily_pnl = None if daily_pnl is None else decimal_value(daily_pnl, 'PnL diario')
    return realized_today <= -budget or (daily_pnl is not None and daily_pnl <= -budget)


def init_schema(store):
    with store.connect() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS paper_risk_capital(
          currency TEXT PRIMARY KEY, initial_capital TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS paper_daily_risk(
          day TEXT NOT NULL, currency TEXT NOT NULL, baseline_equity TEXT,
          limit_pct TEXT NOT NULL, loss_budget TEXT, last_equity TEXT,
          daily_pnl TEXT, state TEXT NOT NULL, latched_at TEXT,
          evaluated_at TEXT NOT NULL, detail TEXT NOT NULL,
          PRIMARY KEY(day,currency));
        """)


def caucion_pnl(p, at, *, exclusive=False):
    opened = aware_datetime(p['opened_at'])
    if opened > at or (exclusive and opened == at):
        return ZERO
    fees = decimal_value(p['total_fees'], 'costos', nonnegative=True)
    if p['settled_at'] and aware_datetime(p['settled_at']) <= at:
        return decimal_value(p['gross_interest'], 'interés', nonnegative=True) - fees
    days = max(0,min(p['interest_days'], (at.astimezone(TZ).date()-opened.astimezone(TZ).date()).days))
    return money(decimal_value(p['principal'],'capital',positive=True) *
                 decimal_value(p['annual_rate_fraction'],'tasa',nonnegative=True) *
                 days / p['day_count_basis']) - fees


class DailyRisk:
    def __init__(self, broker, limit_pct, *, soft_limit_pct=None):
        self.broker, self.store = broker, broker.store
        self.limit_pct = decimal_value(limit_pct,'pérdida diaria %',positive=True)
        self.soft_limit_explicit = soft_limit_pct is not None
        self.soft_limit_pct = (self.limit_pct if soft_limit_pct is None else
                               decimal_value(soft_limit_pct,'freno diario blando %',positive=True))
        if self.limit_pct > 100:
            raise ValueError('Pérdida diaria expresada en porcentaje: 1 significa 1%')
        if self.soft_limit_pct > self.limit_pct:
            raise ValueError('El freno blando no puede superar al límite duro')

    def soft_stop_crossed(self, row):
        """Frena aperturas sin crear intenciones de salida ni tocar el latch."""
        baseline = row.get('baseline_equity')
        daily = row.get('daily_pnl')
        if baseline is None or daily is None:
            return False
        budget = decimal_value(baseline,'base diaria',positive=True)*self.soft_limit_pct/100
        return decimal_value(daily,'PnL diario') <= -budget

    def evaluate(self, at, *, connection=None, quotes=None):
        at = aware_datetime(at)
        if connection is None:
            with self.store.connect() as c:
                c.execute('BEGIN IMMEDIATE')
                return self.evaluate(at,connection=c,quotes=quotes)
        c = connection
        day = at.astimezone(TZ).date().isoformat()
        start = datetime.combine(at.astimezone(TZ).date(),time.min,TZ)
        results = {}
        for currency, capital in sorted(self.broker.initial_balances.items()):
            previous = c.execute('SELECT * FROM paper_daily_risk WHERE day=? AND currency=?',
                                 (day,currency)).fetchone()
            latest_row = c.execute("""SELECT evaluated_at FROM paper_daily_risk
                WHERE currency=? ORDER BY julianday(evaluated_at) DESC LIMIT 1""",
                (currency,)).fetchone()
            latest_at = latest_row['evaluated_at'] if latest_row else None
            input_at = at.isoformat()
            rollback = bool(latest_at and c.execute(
                'SELECT julianday(?) > julianday(?)',(latest_at,input_at)).fetchone()[0])
            if rollback:
                results[currency] = {
                    'state':'CLOCK_ROLLBACK',
                    'latched_at':previous['latched_at'] if previous else None,
                    'input_at':input_at,
                    'latest_evaluated_at':latest_at,
                }
                continue
            c.execute('INSERT OR IGNORE INTO paper_risk_capital VALUES(?,?)',(currency,str(capital)))
            saved_capital = Decimal(c.execute('SELECT initial_capital FROM paper_risk_capital WHERE currency=?',
                                             (currency,)).fetchone()[0])
            baseline, equity, daily, budget = None, None, None, None
            state, detail = 'READY', 'Base del ledger; valuación neta de costos y deslizamiento de salida'
            latched = previous['latched_at'] if previous else None
            try:
                rows = [dict(r) for r in c.execute('SELECT * FROM paper_positions WHERE currency=?',(currency,))]
                for p in rows:
                    if any(p[k] and aware_datetime(p[k])>at for k in ('opened_at','closed_at')):
                        raise ValueError('Ledger con fechas futuras')
                if c.execute("""SELECT 1 FROM paper_spot_sales s JOIN paper_fills f ON f.id=s.fill_id
                    JOIN paper_positions p ON p.paper_id=s.paper_id WHERE p.currency=?
                    AND julianday(f.filled_at)>julianday(?) LIMIT 1""",
                    (currency,at.isoformat())).fetchone():
                    raise ValueError('Ledger parcial con fechas futuras')
                opened_rows,closed_rows = self.broker._positions_at(at,c)
                rows = [p for p in opened_rows+closed_rows if p['currency']==currency]
                cauciones = self.broker.cauciones.positions(currency=currency,connection=c)
                before = realized = today_realized = unrealized = ZERO
                carry, stale = False, False
                for p in rows:
                    opened = aware_datetime(p['opened_at'])
                    closed = aware_datetime(p['closed_at']) if p['closed_at'] else None
                    if opened > at or (closed and (closed > at or closed < opened)):
                        raise ValueError('Ledger con fechas futuras o invertidas')
                    if p['status'] not in {'OPEN','CLOSED'} or (p['status']=='CLOSED') != bool(closed):
                        raise ValueError('Estado y fecha del ledger incompatibles')
                    if opened < start and (not closed or closed >= start):
                        carry = True
                    if closed:
                        net = decimal_value(p['net_pnl'],'PnL cerrado')
                        realized += net
                        if closed < start:
                            before += net
                        else:
                            today_realized += net
                        continue
                    q = (quotes or {}).get(p['symbol']) or self.store.latest_quote(p)
                    valid = False
                    if q:
                        try:
                            valid = (q.monetary_identity()==(currency,p['market']) and
                                (q.symbol,q.asset_class,q.settlement)==(p['symbol'],p['asset_class'],p['settlement']) and
                                not q.time_error(at,max_age_seconds=self.broker.quote_max_age_seconds) and
                                decimal_value(q.bid,'bid',positive=True) <= decimal_value(q.ask,'ask',positive=True))
                        except (ValueError,TypeError,ArithmeticError):
                            valid = False
                    if not valid:
                        stale = True
                        continue
                    factor = self.broker._position_multiplier(p)
                    qty = decimal_value(p['quantity'],'cantidad',positive=True)
                    exit_price = (q.bid*(1-self.broker.slippage)).quantize(Decimal('0.0001'))
                    unrealized += ((exit_price-Decimal(p['entry_price']))*qty*factor -
                        Decimal(p['entry_cost']) - self.broker._cost(exit_price*factor,qty,p['asset_class']))
                candidate = capital + before + sum((caucion_pnl(p,start,exclusive=True) for p in cauciones),ZERO)
                baseline = ((Decimal(previous['baseline_equity']) if previous['baseline_equity'] is not None else None)
                            if previous else candidate if not carry else None)
                if baseline is not None:
                    budget = baseline*self.limit_pct/100
                if not stale:
                    equity = capital + realized + unrealized + sum((caucion_pnl(p,at) for p in cauciones),ZERO)
                    daily = equity-baseline if baseline is not None else None
                if capital != saved_capital or (previous and Decimal(previous['limit_pct']) != self.limit_pct):
                    state, detail = 'CONFIG_CHANGED', 'Capital cambiado o límite cambiado durante el día; requiere conciliación'
                elif baseline is None:
                    state, detail = 'BASELINE_UNAVAILABLE', 'Carry spot sin marca conciliada del día anterior; no se inventa base'
                elif baseline <= 0 or capital <= 0:
                    state, detail = 'NO_CAPITAL', 'Sin base positiva en esta moneda'
                else:
                    if not latched and loss_limit_crossed(daily, today_realized, budget):
                        latched = at.isoformat()
                        c.execute('INSERT INTO paper_events VALUES(NULL,?,?,?,?,?)',
                            (latched,'PRODUCTION_PAPER','PAPER_DAILY_LOSS',None,
                             f'{day} {currency}: corte diario. PnL {daily}; realizado {today_realized}; límite {budget}'))
                        c.execute('''INSERT INTO paper_exit_intents
                          SELECT paper_id,'EXIT_DUE','DAILY_LOSS_PAPER',?,
                            'Corte diario; espera libro ejecutable',?,0
                          FROM paper_positions WHERE status='OPEN' AND currency=?
                          ON CONFLICT(paper_id) DO UPDATE SET state='EXIT_DUE',
                            cause=COALESCE(paper_exit_intents.cause,excluded.cause),
                            due_at=COALESCE(paper_exit_intents.due_at,excluded.due_at),
                            supervised_at=excluded.supervised_at''', (latched,latched,currency))
                    if stale:
                        state, detail = 'STALE_MARKS', 'Faltan libros vigentes; PnL diario actual desconocido'
            except (ValueError,TypeError,ArithmeticError) as exc:
                state, detail = 'INVALID_LEDGER', type(exc).__name__
            if latched:
                state, detail = 'LATCHED', 'Corte persistente hasta otra fecha local; las salidas siguen activas'
            if previous and previous['loss_budget'] is not None:
                budget = Decimal(previous['loss_budget'])
            c.execute('''INSERT INTO paper_daily_risk VALUES(?,?,?,?,?,?,?,?,?,?,?)
              ON CONFLICT(day,currency) DO UPDATE SET last_equity=excluded.last_equity,
                daily_pnl=excluded.daily_pnl,state=excluded.state,latched_at=excluded.latched_at,
                evaluated_at=excluded.evaluated_at,detail=excluded.detail''',
                (day,currency,str(baseline) if baseline is not None else None,
                 str(self.limit_pct),str(budget) if budget is not None else None,
                 str(equity) if equity is not None else None,str(daily) if daily is not None else None,
                 state,latched,at.isoformat(),detail))
            results[currency] = dict(c.execute('SELECT * FROM paper_daily_risk WHERE day=? AND currency=?',
                                              (day,currency)).fetchone())
        return results

    def admission_error(self, currency, at, *, connection=None, quotes=None):
        row = self.evaluate(at,connection=connection,quotes=quotes)[cash_currency(currency)]
        if row['state'] != 'READY':
            code = 'DAILY_RISK_' + row['state']
            if row['state'] == 'CLOCK_ROLLBACK':
                return (f"{code} input_at={row.get('input_at')} "
                        f"latest_evaluated_at={row.get('latest_evaluated_at')}")
            return code
        return 'DAILY_RISK_SOFT_STOP' if self.soft_stop_crossed(row) else ''

    def projected_admission_error(self, currency, at, committed_cost, *, connection=None):
        """Costo de caución comprometido hoy, incluso si se cobra al vencer.

        Un rechazo hipotético no activa latch ni registra un gasto. La
        evaluación del estado existente sí conserva un corte ya ocurrido.
        """
        cost = decimal_value(committed_cost, 'costo comprometido', nonnegative=True)
        row = self.evaluate(at,connection=connection)[cash_currency(currency)]
        if row['state'] != 'READY':
            return 'DAILY_RISK_' + row['state']
        projected = decimal_value(row['daily_pnl'], 'PnL diario') - cost
        soft_budget = (decimal_value(row['baseline_equity'],'base diaria',positive=True)
                       * self.soft_limit_pct / 100)
        if projected > -soft_budget:
            return ''
        return ('DAILY_RISK_PROJECTED_SOFT_STOP' if self.soft_limit_explicit
                else 'DAILY_RISK_PROJECTED_LOSS')
