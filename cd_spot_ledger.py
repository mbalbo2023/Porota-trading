"""Ventas parciales PAPER: lotes económicos sin alterar la entrada original.

Una posición sigue siendo una muestra de estrategia. Los fills realizan PnL
y producen créditos por separado; sólo el último cierre etiqueta la muestra.
Lecturas históricas reconstruyen cantidades/costos al instante solicitado.
"""
from decimal import Decimal
import json

from bs_instrument_contracts import aware_datetime, decimal_value

ZERO = Decimal(0)


def init_schema(store):
    with store.connect() as c:
        c.executescript('''CREATE TABLE IF NOT EXISTS paper_spot_sales(
          fill_id INTEGER PRIMARY KEY REFERENCES paper_fills(id),
          paper_id TEXT NOT NULL REFERENCES paper_positions(paper_id),
          entry_cost TEXT NOT NULL, gross_pnl TEXT NOT NULL, net_pnl TEXT NOT NULL,
          net_proceeds TEXT NOT NULL, available_at TEXT, basis TEXT NOT NULL,
          reason TEXT NOT NULL);
          CREATE INDEX IF NOT EXISTS idx_spot_sales_position ON paper_spot_sales(paper_id);
          CREATE INDEX IF NOT EXISTS idx_spot_fills_position_side ON paper_fills(paper_id,side);''')


def sales(c, paper_id):
    if not c.execute("SELECT 1 FROM sqlite_master WHERE name='paper_spot_sales'").fetchone():
        return []
    return [dict(r) for r in c.execute('''SELECT s.*,f.quantity,f.price,f.costs,f.filled_at,
        f.side,f.paper_id AS fill_paper_id FROM paper_spot_sales s
        LEFT JOIN paper_fills f ON f.id=s.fill_id WHERE s.paper_id=?
        ORDER BY julianday(f.filled_at),s.fill_id''',(paper_id,))]


def partition(c, p, at=None):
    """Retorna remanente y realizaciones; no cambia filas ni etiqueta parciales."""
    p = dict(p)
    start = aware_datetime(p['opened_at'])
    end = aware_datetime(p['closed_at']) if p['closed_at'] else None
    if (p['status'] not in {'OPEN','CLOSED'} or (p['status']=='CLOSED') != bool(end)
            or (end and end < start)):
        raise ValueError('Cronología de posición inválida')
    at = aware_datetime(at) if at is not None else None
    rows = sales(c,p['paper_id'])
    fills = c.execute("SELECT id FROM paper_fills WHERE paper_id=? AND side='SELL_SIMULATED'",(p['paper_id'],)).fetchall()
    if not rows:
        if (p['status']=='OPEN' and fills) or len(fills)>1:
            raise ValueError('Ventas sin asignación de cantidad/costo')
        if at is not None and start > at:
            return None, []
        if end and (at is None or end <= at):
            return None, [p]
        p.update(status='OPEN',closed_at=None,exit_price=None,exit_cost=None,
                 gross_pnl=None,net_pnl=None,close_reason=None)
        return p, []
    original_qty = decimal_value(p['quantity'],'cantidad original',positive=True)
    original_cost = decimal_value(p['entry_cost'],'costo original',nonnegative=True)
    entry_price = decimal_value(p['entry_price'],'precio original',positive=True)
    factor = decimal_value(json.loads(p['features_json'] or '{}').get('contract_cash_multiplier','1'),'factor',positive=True)
    if {r['fill_id'] for r in rows} != {r['id'] for r in fills}:
        raise ValueError('Ventas sin asignación de cantidad/costo')
    sold = costs = seen_qty = seen_cost = ZERO
    realized = []
    for r in rows:
        if r['side'] != 'SELL_SIMULATED' or r['fill_paper_id'] != p['paper_id']:
            raise ValueError('Venta parcial sin fill compatible')
        when = aware_datetime(r['filled_at'])
        qty = decimal_value(r['quantity'],'cantidad vendida',positive=True)
        cost = decimal_value(r['entry_cost'],'costo asignado',nonnegative=True)
        sold += qty
        costs += cost
        if when < start or (end and when > end) or sold > original_qty or costs > original_cost:
            raise ValueError('Venta parcial incompatible con la entrada')
        for key in ('gross_pnl','net_pnl','net_proceeds'):
            decimal_value(r[key],key)
        price = decimal_value(r['price'],'precio de venta',positive=True)
        fee = decimal_value(r['costs'],'costo de venta',nonnegative=True)
        gross = (price-entry_price)*qty*factor
        if (Decimal(r['gross_pnl'])!=gross or Decimal(r['net_pnl'])!=gross-cost-fee
                or Decimal(r['net_proceeds'])!=price*qty*factor-fee):
            raise ValueError('Importes parciales no concilian con el fill')
        if at is not None and when > at:
            continue
        seen_qty += qty
        seen_cost += cost
        realized.append(dict(p,status='CLOSED',quantity=str(qty),entry_cost=str(cost),
            closed_at=r['filled_at'],exit_price=r['price'],exit_cost=r['costs'],
            gross_pnl=r['gross_pnl'],net_pnl=r['net_pnl'],close_reason=r['reason'],
            sale_fill_id=r['fill_id']))
    if (p['status']=='CLOSED') != (sold==original_qty) or (sold==original_qty and costs!=original_cost):
        raise ValueError('Remanente parcial incompatible con estado/costos')
    if end and (end!=aware_datetime(rows[-1]['filled_at']) or any(
            decimal_value(p[target],target)!=sum((Decimal(r[source]) for r in rows),ZERO)
            for target,source in (('net_pnl','net_pnl'),('gross_pnl','gross_pnl'),('exit_cost','costs')))):
        raise ValueError('Cierre agregado no concilia con sus fills')
    if at is not None and start > at:
        return None, []
    remaining = original_qty-seen_qty
    if not remaining:
        return None, realized
    p.update(status='OPEN',quantity=str(remaining),entry_cost=str(original_cost-seen_cost),
             closed_at=None,exit_price=None,exit_cost=None,gross_pnl=None,net_pnl=None,
             close_reason=None,original_quantity=str(original_qty),sold_quantity=str(seen_qty),
             realized_net_pnl=str(sum((Decimal(r['net_pnl']) for r in realized),ZERO)))
    return p, realized


def positions_at(c, at=None):
    opened, closed = [], []
    for row in c.execute('SELECT * FROM paper_positions ORDER BY opened_at,paper_id'):
        remaining, realized = partition(c,row,at)
        if remaining:
            opened.append(remaining)
        closed.extend(realized)
    return opened, closed


def pending(c, at, currency):
    at = aware_datetime(at)
    amount = ZERO
    for row in c.execute('SELECT * FROM paper_positions WHERE currency=?',(currency,)):
        partition(c,row,at)  # valida también la concordancia del ledger
        for sale in sales(c,row['paper_id']):
            if aware_datetime(sale['filled_at']) > at:
                continue
            value = decimal_value(sale['net_proceeds'],'producido de venta')
            available = aware_datetime(sale['available_at']) if sale['available_at'] else None
            if available is not None and available < aware_datetime(sale['filled_at']):
                raise ValueError('Liquidación anterior a la venta')
            if value > 0 and (available is None or available > at):
                amount += value
    return amount
