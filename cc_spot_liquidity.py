"""Profundidad consumida por fills PAPER de contado, por fotografía y lado.

No agrega liquidez por reintento o reinicio. Una fotografía posterior es un
supuesto del simulador; no demuestra un fill ni reposición reales en PPI.
"""
from decimal import Decimal
import json

from bl_candle_engine import fingerprint, stamp
from bs_instrument_contracts import decimal_value, family_name


def init_schema(store):
    with store.connect() as c:
        c.execute('''CREATE TABLE IF NOT EXISTS paper_book_consumption(
            fill_id INTEGER PRIMARY KEY REFERENCES paper_fills(id),
            instrument_key TEXT NOT NULL, book_at TEXT NOT NULL,
            side TEXT NOT NULL, quantity TEXT NOT NULL, book_json TEXT NOT NULL)''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_book_consumption ON paper_book_consumption(instrument_key,book_at)')


def book(q):
    currency, market = q.monetary_identity()
    identity = dict(symbol=q.symbol, family=family_name(q.asset_class), settlement=q.settlement,
                    currency=currency, market=market)
    values = {k:format(decimal_value(getattr(q,k),k,nonnegative=True).normalize(),'f')
              for k in ('bid','ask','bid_size','ask_size')}
    if not Decimal(values['bid']) > 0 or Decimal(values['ask']) < Decimal(values['bid']):
        raise ValueError('SPOT_BOOK_INVALID')
    return fingerprint(identity), stamp(q.book_at), values


def available(c, q, side, participation):
    if side not in {'BUY_SIMULATED','SELL_SIMULATED'}:
        raise ValueError('SPOT_BOOK_SIDE_INVALID')
    participation = decimal_value(participation,'participación',positive=True)
    if participation > 1:
        raise ValueError('SPOT_BOOK_PARTICIPATION_INVALID')
    key, at, payload = book(q)
    used = Decimal(0)
    for row in c.execute('SELECT * FROM paper_book_consumption WHERE instrument_key=?',(key,)):
        if row['book_at'] > at:
            raise ValueError('SPOT_BOOK_OLDER_THAN_CONSUMED')
        if row['book_at'] == at:
            if json.loads(row['book_json']) != payload:
                raise ValueError('SPOT_BOOK_CONFLICTING_SNAPSHOT')
            if row['side'] == side:
                used += decimal_value(row['quantity'],'profundidad consumida',positive=True)
    # No inventar el libro de fills históricos sin registro de profundidad.
    # Sólo una fotografía posterior a esos fills permite reiniciar el modelo.
    for row in c.execute('''SELECT f.filled_at FROM paper_fills f JOIN paper_positions p USING(paper_id)
        LEFT JOIN paper_book_consumption b ON b.fill_id=f.id
        WHERE b.fill_id IS NULL AND f.side=? AND p.symbol=? AND p.asset_class=?
          AND p.settlement=? AND p.currency=? AND p.market=?''',
        (side,q.symbol,q.asset_class,q.settlement,*q.monetary_identity())):
        if stamp(row['filled_at']) >= at:
            raise ValueError('SPOT_LEGACY_DEPTH_UNKNOWN')
    size = Decimal(payload['ask_size' if side=='BUY_SIMULATED' else 'bid_size'])
    return max(Decimal(0),size*participation-used)


def record(c, fill_id, q):
    """Sólo tras comprobar available bajo el lock de la transacción del fill."""
    key, at, payload = book(q)
    fill = c.execute('SELECT side,quantity FROM paper_fills WHERE id=?',(fill_id,)).fetchone()
    if fill is None:
        raise ValueError('SPOT_LIQUIDITY_WITHOUT_FILL')
    c.execute('INSERT INTO paper_book_consumption VALUES(?,?,?,?,?,?)',
              (fill_id,key,at,fill['side'],fill['quantity'],json.dumps(payload,sort_keys=True)))
