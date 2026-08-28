"""Ventas parciales PAPER: lotes económicos sin alterar la entrada original.

Una posición sigue siendo una muestra de estrategia. Los fills realizan PnL
y producen créditos por separado; sólo el último cierre etiqueta la muestra.
Lecturas históricas reconstruyen cantidades/costos al instante solicitado.
"""
from decimal import Decimal, ROUND_HALF_UP
import json

from bs_instrument_contracts import aware_datetime, decimal_value, cash_currency
from cf_sale_settlement import validated_sale_settlement

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
        f.side,f.paper_id AS fill_paper_id,f.source AS fill_source FROM paper_spot_sales s
        LEFT JOIN paper_fills f ON f.id=s.fill_id WHERE s.paper_id=?
        ORDER BY julianday(f.filled_at),s.fill_id''',(paper_id,))]


def entry_terms(p):
    """Unidades/costos históricos explícitos; no consulta un tarifario actual."""
    if cash_currency(p['currency']) != p['currency']:
        raise ValueError('Moneda de posición no canónica; no reinterpretar el ledger')
    qty = decimal_value(p['quantity'],'cantidad original',positive=True)
    fee = decimal_value(p['entry_cost'],'costo original',nonnegative=True)
    price = decimal_value(p['entry_price'],'precio original',positive=True)
    features = json.loads(p['features_json'] or '{}')
    if not isinstance(features,dict):
        raise ValueError('Contrato de posición debe ser un objeto')
    factor = decimal_value(features.get('contract_cash_multiplier','1'),'factor',positive=True)
    return qty, fee, price, factor


def allocated_entry_cost(original_qty, original_cost, sold_qty, allocated_cost, sell_qty):
    """Regla PAPER única para escritura y lectura de costos de entrada.

    Prorratea el costo histórico por cantidad original, redondea en centavos
    HALF_UP y asigna el residuo exacto al último fill. No consulta ni recalcula
    comisiones. El tope conserva costos subcentavo de entradas importadas.
    """
    original_qty = decimal_value(original_qty, 'cantidad original', positive=True)
    original_cost = decimal_value(original_cost, 'costo original', nonnegative=True)
    sold_qty = decimal_value(sold_qty, 'cantidad ya vendida', nonnegative=True)
    allocated_cost = decimal_value(allocated_cost, 'costo ya asignado', nonnegative=True)
    sell_qty = decimal_value(sell_qty, 'cantidad a vender', positive=True)
    if sold_qty >= original_qty or sell_qty > original_qty - sold_qty or allocated_cost > original_cost:
        raise ValueError('Reparto parcial excede la cantidad o costo original')
    remaining_cost = original_cost - allocated_cost
    if sell_qty == original_qty - sold_qty:
        return remaining_cost
    portion = (original_cost * sell_qty / original_qty).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
    return min(remaining_cost, portion)


def single_close_proceeds(p, fills):
    """Concordancia del cierre completo con un único SELL_SIMULATED.

    Valida contra la entrada almacenada; no certifica origen de esa entrada,
    arancel histórico, ejecución PPI ni modificaciones coordinadas del ledger.
    """
    qty, entry_fee, entry_price, factor = entry_terms(p)
    if p['status']!='CLOSED' or not p['closed_at'] or len(fills)!=1:
        raise ValueError('Cierre simple sin un único fill de venta')
    end, start = aware_datetime(p['closed_at']), aware_datetime(p['opened_at'])
    fill = fills[0]
    price = decimal_value(p['exit_price'],'precio de cierre',positive=True)
    fee = decimal_value(p['exit_cost'],'costo de cierre',nonnegative=True)
    if (end < start or fill['paper_id']!=p['paper_id'] or fill['source']!=p['source']
            or fill['side']!='SELL_SIMULATED' or aware_datetime(fill['filled_at'])!=end
            or decimal_value(fill['quantity'],'cantidad del fill',positive=True)!=qty
            or decimal_value(fill['price'],'precio del fill',positive=True)!=price
            or decimal_value(fill['costs'],'costo del fill',nonnegative=True)!=fee):
        raise ValueError('Cierre simple incompatible con su fill')
    gross = (price-entry_price)*qty*factor
    if (decimal_value(p['gross_pnl'],'PnL bruto')!=gross
            or decimal_value(p['net_pnl'],'PnL neto')!=gross-entry_fee-fee):
        raise ValueError('Importes del cierre simple no concilian')
    return price*qty*factor-fee


def partition(c, p, at=None):
    """Retorna remanente y realizaciones; no cambia filas ni etiqueta parciales."""
    p = dict(p)
    start = aware_datetime(p['opened_at'])
    end = aware_datetime(p['closed_at']) if p['closed_at'] else None
    if (p['status'] not in {'OPEN','CLOSED'} or (p['status']=='CLOSED') != bool(end)
            or (end and end < start)):
        raise ValueError('Cronología de posición inválida')
    at = aware_datetime(at) if at is not None else None
    original_qty, original_cost, entry_price, factor = entry_terms(p)
    rows = sales(c,p['paper_id'])
    fills = c.execute("SELECT * FROM paper_fills WHERE paper_id=? AND side='SELL_SIMULATED'",(p['paper_id'],)).fetchall()
    if not rows:
        if (p['status']=='OPEN' and fills) or len(fills)>1:
            raise ValueError('Ventas sin asignación de cantidad/costo')
        if end:
            single_close_proceeds(p,fills)
        if at is not None and start > at:
            return None, []
        if end and (at is None or end <= at):
            return None, [p]
        p.update(status='OPEN',closed_at=None,exit_price=None,exit_cost=None,
                 gross_pnl=None,net_pnl=None,close_reason=None)
        return p, []
    if {r['fill_id'] for r in rows} != {r['id'] for r in fills}:
        raise ValueError('Ventas sin asignación de cantidad/costo')
    sold = costs = seen_qty = seen_cost = ZERO
    realized = []
    for r in rows:
        if (r['side'] != 'SELL_SIMULATED' or r['fill_paper_id'] != p['paper_id']
                or r['fill_source'] != p['source']):
            raise ValueError('Venta parcial sin fill compatible')
        when = aware_datetime(r['filled_at'])
        qty = decimal_value(r['quantity'],'cantidad vendida',positive=True)
        cost = decimal_value(r['entry_cost'],'costo asignado',nonnegative=True)
        if cost != allocated_entry_cost(original_qty, original_cost, sold, costs, qty):
            raise ValueError('Costo de entrada parcial no concilia con su cantidad histórica')
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
    if end:
        # Misma operación Decimal que el ejecutor: último fill + anteriores.
        # No inferir un precio desde el PnL, que incluye costos y factor nominal.
        average = (Decimal(rows[-1]['price']) * Decimal(rows[-1]['quantity']) + sum(
            (Decimal(r['price']) * Decimal(r['quantity']) for r in rows[:-1]), ZERO)) / original_qty
        if decimal_value(p['exit_price'], 'precio agregado de cierre', positive=True) != average:
            raise ValueError('Precio agregado de cierre no concilia con sus fills')
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
            available = validated_sale_settlement(row['settlement'],sale['filled_at'],
                                                 sale['available_at'],sale['basis'])
            if value > 0 and (available is None or available > at):
                amount += value
    return amount
