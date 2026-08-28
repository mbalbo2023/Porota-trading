"""Replay offline de órdenes IOC sobre libros fechados, no backtester de señales.

Una corrida usa una serie, contrato, moneda y versión de estrategia. No llama
al broker, no usa el entorno/tarifario de hoy ni coloca cauciones. Las familias
con garantías, ejercicio o rescate requieren otros ejecutores y se rechazan.
Los fills son hipótesis reproducibles sobre profundidad observada, no negocios
reales. El caller debe aportar sesiones, liquidaciones y aranceles contrastados.
"""
from dataclasses import asdict, dataclass
from datetime import timedelta
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP

from bl_candle_engine import fingerprint, stamp
from br_backtest_gate import marked_drawdown
from bs_instrument_contracts import SPOT_FAMILIES, aware_datetime, decimal_value

ZERO = Decimal('0')
CENT = Decimal('0.01')


def money(value):
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def text_required(value):
    if not isinstance(value, str) or not value.strip() or value.upper() == 'UNKNOWN':
        raise ValueError('Falta identificación/fuente explícita')
    return value


@dataclass(frozen=True)
class FeeTerms:
    """Importes en la moneda del contrato; fracción all-in, NO porcentaje.

    Mínimo y fijo se cobran por fill en este modelo IOC. Deben incluir todos
    los impuestos/derechos aplicables. No se presume que sean tarifas PPI.
    """
    series_id: str
    source: str
    known_at: str
    valid_from: str
    valid_until: str
    rate: Decimal
    minimum: Decimal
    fixed: Decimal

    def __post_init__(self):
        text_required(self.series_id); text_required(self.source)
        known, start, end = map(aware_datetime, (self.known_at, self.valid_from, self.valid_until))
        if not start < end:
            raise ValueError('Vigencia de costos inválida')
        for field in ('rate', 'minimum', 'fixed'):
            object.__setattr__(self, field, decimal_value(getattr(self, field), field, nonnegative=True))
        if self.rate >= 1:
            raise ValueError('Arancel debe ser una fracción menor a uno')

    def fee(self, notional):
        return money(max(self.minimum, notional * self.rate) + self.fixed)

    def available(self, decision, executed):
        return (aware_datetime(self.known_at) <= aware_datetime(decision)
                and aware_datetime(self.valid_from) <= aware_datetime(executed)
                < aware_datetime(self.valid_until))


@dataclass(frozen=True)
class ExecutionAssumptions:
    participation: Decimal
    slippage_bps: Decimal
    price_tick: Decimal
    latency_seconds: float
    maximum_book_age_seconds: float
    source: str

    def __post_init__(self):
        text_required(self.source)
        for field in ('participation', 'slippage_bps', 'price_tick',
                      'latency_seconds', 'maximum_book_age_seconds'):
            value = decimal_value(getattr(self, field), field, nonnegative=True)
            object.__setattr__(self, field, value)
        if not 0 < self.participation <= 1 or self.slippage_bps >= 10000 or self.price_tick <= 0:
            raise ValueError('Supuestos de ejecución fuera de rango')
        if self.maximum_book_age_seconds <= 0:
            raise ValueError('Falta límite de antigüedad del libro')

    def price(self, book, side):
        raw = book.ask if side == 'BUY' else book.bid
        return self.price_at(raw, side)

    def price_at(self, raw, side):
        if side not in {'BUY', 'SELL'}:
            raise ValueError('Sentido de ejecución inválido')
        raw = decimal_value(raw, 'precio', positive=True)
        adjusted = raw * (1 + self.slippage_bps / 10000 * (1 if side == 'BUY' else -1))
        rounding = ROUND_CEILING if side == 'BUY' else ROUND_FLOOR
        return (adjusted / self.price_tick).to_integral_value(rounding=rounding) * self.price_tick


@dataclass(frozen=True)
class BookEvent:
    event_id: str
    series_id: str
    source: str
    book_at: str
    received_at: str
    bid: Decimal
    ask: Decimal
    bid_size: Decimal
    ask_size: Decimal
    # Fechas y segmento normalizados por el adaptador histórico, no inferidos.
    settlement_at: str
    settlement_source: str
    session_open: bool
    session_source: str

    def __post_init__(self):
        for field in ('event_id', 'series_id', 'source', 'settlement_source', 'session_source'):
            text_required(getattr(self, field))
        event, received, due = map(aware_datetime, (self.book_at, self.received_at, self.settlement_at))
        if event > received or due < received or not isinstance(self.session_open, bool):
            raise ValueError('Libro, recepción, sesión o liquidación inválidos')
        for field in ('bid', 'ask', 'bid_size', 'ask_size'):
            object.__setattr__(self, field, decimal_value(getattr(self, field), field,
                               positive=field in {'bid', 'ask'}, nonnegative=True))
        if self.bid > self.ask:
            raise ValueError('Libro cruzado')


@dataclass(frozen=True)
class ReplayOrder:
    order_id: str
    series_id: str
    strategy_version: str
    submitted_at: str
    expires_at: str
    side: str
    quantity: Decimal
    evidence_ids: tuple[int, ...]
    price_limit: Decimal | None = None
    entry_order_id: str | None = None

    def __post_init__(self):
        for field in ('order_id', 'series_id', 'strategy_version'):
            text_required(getattr(self, field))
        if self.side not in {'BUY', 'SELL'}:
            raise ValueError('Sólo compra/venta IOC de tenencia larga financiada con caja')
        if aware_datetime(self.expires_at) <= aware_datetime(self.submitted_at):
            raise ValueError('Orden sin vigencia')
        object.__setattr__(self, 'quantity', decimal_value(self.quantity, 'cantidad', positive=True))
        if self.price_limit is not None:
            object.__setattr__(self, 'price_limit', decimal_value(self.price_limit, 'límite de precio', positive=True))
        if self.entry_order_id is not None:
            text_required(self.entry_order_id)
            if self.side != 'SELL':
                raise ValueError('Sólo una salida puede referenciar el plan de entrada')
        if (not isinstance(self.evidence_ids, tuple) or not self.evidence_ids
                or any(type(i) is not int or i <= 0 for i in self.evidence_ids)
                or len(set(self.evidence_ids)) != len(self.evidence_ids)):
            raise ValueError('Faltan versiones de datos que respaldan la decisión')


def serializable(value):
    if isinstance(value, Decimal):
        return format(value.normalize(), 'f')
    if isinstance(value, dict):
        return {k: serializable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [serializable(v) for v in value]
    return value


class ExecutionReplay:
    def __init__(self, archive, series, contract, fees, assumptions):
        text_required(series.source); text_required(contract.metadata_source)
        identity = (series.symbol, series.asset_class, series.market, series.currency, series.settlement)
        if identity != contract.key or series.cash_multiplier != format(contract.cash_multiplier.normalize(), 'f'):
            raise ValueError('Serie/contrato no coinciden, incluida unidad monetaria')
        if contract.family not in SPOT_FAMILIES:
            raise ValueError('Familia requiere ejecutor especializado; no usar compraventa de contado')
        if (series.adjustment != 'RAW' or series.price_kind != 'PROVIDER_OHLC'
                or series.volume_kind == 'UNKNOWN' or series.adjustment_basis == 'UNKNOWN'):
            raise ValueError('Replay requiere precios nominales y unidades explícitas')
        if fees.series_id != series.key:
            raise ValueError('Costos de otra serie/moneda/plazo')
        self.archive, self.series, self.contract = archive, series, contract
        self.fees, self.assumptions = fees, assumptions

    def run(self, orders, books, *, start, end, initial_cash, strategy=None):
        begin, finish = map(aware_datetime, (start, end))
        if begin >= finish:
            raise ValueError('Intervalo inválido')
        capital = decimal_value(initial_cash, 'capital', positive=True)
        if capital != money(capital):
            raise ValueError('Capital debe expresarse en centavos')
        orders = sorted(orders, key=lambda o: (stamp(o.submitted_at), o.order_id))
        if strategy is not None and orders:
            raise ValueError('No mezclar órdenes preparadas con generación de estrategia')
        books = sorted(books, key=lambda b: (stamp(b.received_at), b.event_id))
        if len({o.order_id for o in orders}) != len(orders) or len({o.strategy_version for o in orders}) > 1:
            raise ValueError('Órdenes duplicadas o versiones de estrategia mezcladas')
        evidence = {}
        registered = {}
        def validate_order(order):
            if order.order_id in registered:
                raise ValueError('Identificador de orden duplicado')
            if registered and order.strategy_version != next(iter(registered.values())).strategy_version:
                raise ValueError('Versión de estrategia cambió durante la corrida')
            if order.series_id != self.series.key or not begin <= aware_datetime(order.submitted_at) < finish:
                raise ValueError('Orden fuera de serie o período')
            self.contract.quantity(order.quantity)
            if order.entry_order_id is not None:
                entry = registered.get(order.entry_order_id)
                if (entry is None or entry.side != 'BUY' or entry.evidence_ids != order.evidence_ids
                        or aware_datetime(entry.submitted_at) >= aware_datetime(order.submitted_at)):
                    raise ValueError('Salida sin evidencia del plan de entrada registrado')
                # Una revisión posterior no anula la evidencia del plan que ya
                # abrió riesgo. No exigir una nueva señal BUY para poder salir.
                evidence[order.order_id] = evidence[entry.order_id]
            else:
                current = {b['version_id']: b for b in self.archive.read(self.series, as_of=order.submitted_at)}
                if any(i not in current for i in order.evidence_ids):
                    raise ValueError('Evidencia futura, revisada, sintética, inválida o de otra serie')
                evidence[order.order_id] = [current[i] for i in order.evidence_ids]
            registered[order.order_id] = order
        for order in orders:
            validate_order(order)
        # Un evento repetido no repone liquidez. Una corrección contradictoria
        # del mismo instante requiere resolver la fuente, no elegir el mejor fill.
        seen_ids, seen_books, unique = {}, {}, []
        for book in books:
            if book.series_id != self.series.key or not begin <= aware_datetime(book.received_at) <= finish:
                raise ValueError('Libro fuera de serie o período')
            payload = serializable(asdict(book))
            digest = fingerprint(payload)
            if book.event_id in seen_ids:
                if seen_ids[book.event_id] != digest:
                    raise ValueError('Mismo ID con contenido distinto')
                continue
            seen_ids[book.event_id] = digest
            key = (book.source, stamp(book.book_at))
            content = {k: v for k, v in payload.items() if k not in {'event_id', 'received_at'}}
            content['book_at'] = stamp(book.book_at)
            content['settlement_at'] = stamp(book.settlement_at)
            digest = fingerprint(content)
            if key in seen_books:
                if seen_books[key] != digest:
                    raise ValueError('Libro contradictorio en el mismo instante')
                continue
            seen_books[key] = digest
            unique.append(book)
        if len({b.source for b in unique}) > 1:
            raise ValueError('No combinar fuentes de libro como liquidez independiente')
        if len({stamp(b.received_at) for b in unique}) != len(unique):
            raise ValueError('Recepción simultánea ambigua: falta secuencia de los libros')

        cash, held, basis, realized = capital, ZERO, ZERO, ZERO
        receivables, fills, curve = [], [], []
        states = {o.order_id: {'order_id': o.order_id, 'status': 'PENDING', 'filled': ZERO} for o in orders}
        consumed = set()
        last_book = None

        def settle(at):
            nonlocal cash, receivables
            cash += sum((r['amount'] for r in receivables if aware_datetime(r['available_at']) <= at), ZERO)
            receivables = [r for r in receivables if aware_datetime(r['available_at']) > at]

        def mark(at, book):
            pending = sum((r['amount'] for r in receivables), ZERO)
            quality, value = 'CURRENT', ZERO
            if held:
                if book is None or (at - aware_datetime(book.book_at)).total_seconds() > float(self.assumptions.maximum_book_age_seconds):
                    quality, value = 'STALE_MARKS', None
                elif not self.fees.available(at, at):
                    quality, value = 'UNKNOWN_EXIT_COST', None
                else:
                    price = self.assumptions.price(book, 'SELL')
                    notional = money(self.contract.notional(price, held))
                    value = notional - self.fees.fee(notional)
            point = {'measured_at': stamp(at), 'currency': self.series.currency, 'quality': quality,
                     'external_flow': ZERO, 'cash': cash, 'pending_proceeds': pending,
                     'quantity': held, 'equity': cash + pending + value if value is not None else None}
            if curve and curve[-1]['measured_at'] == point['measured_at']:
                curve[-1] = point
            else:
                curve.append(point)

        mark(begin, None)
        for book in unique:
            at = aware_datetime(book.received_at)
            settle(at)
            stale = (at - aware_datetime(book.book_at)).total_seconds() > float(self.assumptions.maximum_book_age_seconds)
            older = last_book is not None and aware_datetime(book.book_at) < aware_datetime(last_book.book_at)
            capacities = {side: (size * self.assumptions.participation / self.contract.quantity_step).to_integral_value(rounding=ROUND_FLOOR) * self.contract.quantity_step
                          for side, size in (('BUY', book.ask_size), ('SELL', book.bid_size))}
            for order in orders:
                if order.order_id in consumed or aware_datetime(order.submitted_at) >= at:
                    continue
                state = states[order.order_id]
                if at >= aware_datetime(order.expires_at):
                    state['status'] = 'EXPIRED'; consumed.add(order.order_id); continue
                eligible = aware_datetime(order.submitted_at) + timedelta(seconds=float(self.assumptions.latency_seconds))
                # El timestamp del libro también debe ser posterior a la orden;
                # recibir tarde un libro anterior no crea una ejecución nueva.
                if at < eligible or aware_datetime(book.book_at) <= aware_datetime(order.submitted_at) or aware_datetime(book.book_at) < eligible:
                    continue
                if stale or older or not book.session_open:
                    continue
                consumed.add(order.order_id)
                if not self.fees.available(order.submitted_at, at):
                    state['status'] = 'REJECTED_COST_TERMS'; continue
                price = self.assumptions.price(book, order.side)
                if price <= 0:
                    state['status'] = 'REJECTED_PRICE'; continue
                if order.price_limit is not None and ((order.side == 'BUY' and price > order.price_limit)
                        or (order.side == 'SELL' and price < order.price_limit)):
                    state['status'] = 'UNFILLED_PRICE_LIMIT'; continue
                quantity = min(order.quantity, capacities[order.side])
                if order.side == 'SELL':
                    quantity = min(quantity, held)
                else:
                    # Cota binaria por lotes: incluye costo mínimo/fijo y redondeo.
                    low, high = 0, int(quantity / self.contract.quantity_step)
                    while low < high:
                        middle = (low + high + 1) // 2
                        n = money(self.contract.notional(price, self.contract.quantity_step * middle))
                        if n + self.fees.fee(n) <= cash:
                            low = middle
                        else:
                            high = middle - 1
                    quantity = self.contract.quantity_step * low
                if quantity <= 0:
                    state['status'] = 'UNFILLED_CASH_HOLDINGS_OR_DEPTH'; continue
                notional = money(self.contract.notional(price, quantity))
                fee = self.fees.fee(notional)
                if notional <= 0:
                    state['status'] = 'REJECTED_ZERO_NOTIONAL'; continue
                if order.side == 'SELL' and fee > notional:
                    state['status'] = 'REJECTED_NEGATIVE_PROCEEDS'; continue
                pnl = ZERO
                if order.side == 'BUY':
                    cash -= notional + fee
                    held += quantity
                    basis += notional + fee
                else:
                    allocated = basis if quantity == held else basis * quantity / held
                    pnl = notional - fee - allocated
                    realized += pnl
                    held -= quantity; basis -= allocated
                    receivables.append({'available_at': stamp(book.settlement_at), 'amount': notional - fee})
                    settle(at)
                capacities[order.side] -= quantity
                state.update(status='FILLED' if quantity == order.quantity else 'PARTIAL_CANCELLED', filled=quantity)
                fills.append({'order_id': order.order_id, 'book_id': book.event_id, 'side': order.side,
                              'executed_at': stamp(at), 'book_at': stamp(book.book_at),
                              'price': price, 'quantity': quantity, 'notional': notional, 'fee': fee,
                              'realized_pnl': pnl, 'settlement_at': stamp(book.settlement_at)})
            # Nunca rejuvenecer una marca con un mensaje anterior recibido tarde.
            if last_book is None or aware_datetime(book.book_at) > aware_datetime(last_book.book_at):
                last_book = book
            mark(at, last_book)
            if strategy is not None and at < finish:
                # Copias: el callback no puede alterar libro, estados o ledger.
                # Sólo ve el prefijo ya ocurrido; nunca recibe libros futuros.
                from copy import deepcopy
                view = deepcopy({'at': stamp(at), 'book': book, 'book_usable': not stale and not older and book.session_open,
                    'cash': cash, 'held': held, 'basis': basis, 'equity': curve[-1]['equity'],
                    'orders': list(states.values()), 'fills': fills})
                generated = strategy.on_event(view)
                if generated is not None:
                    if not isinstance(generated, ReplayOrder) or stamp(generated.submitted_at) != stamp(at):
                        raise ValueError('La estrategia sólo puede decidir en el instante actual')
                    validate_order(generated)
                    orders.append(generated)
                    states[generated.order_id] = {'order_id': generated.order_id, 'status': 'PENDING', 'filled': ZERO}
        settle(finish)
        mark(finish, last_book)
        for order in orders:
            if states[order.order_id]['status'] == 'PENDING':
                states[order.order_id]['status'] = 'EXPIRED' if aware_datetime(order.expires_at) <= finish else 'PENDING_AT_END'
        curve_ok = all(p['quality'] == 'CURRENT' for p in curve)
        manifest = serializable({'series': asdict(self.series), 'contract': asdict(self.contract),
            'fees': asdict(self.fees), 'assumptions': asdict(self.assumptions),
            'orders': [asdict(o) for o in orders], 'books': [asdict(b) for b in unique],
            'evidence': evidence, 'start': stamp(begin), 'end': stamp(finish), 'capital': capital})
        return serializable({'mode': 'OFFLINE_EXECUTION_REPLAY', 'promotion_allowed': False,
            'run_id': fingerprint(manifest), 'manifest': manifest,
            'orders': list(states.values()), 'fills': fills, 'curve': curve,
            'cash': cash, 'pending_proceeds': sum((r['amount'] for r in receivables), ZERO),
            'open_quantity': held, 'open_cost_basis': basis, 'realized_pnl': realized,
            'observed_drawdown_pct': marked_drawdown(curve, currency=self.series.currency) if curve_ok else None,
            'limitations': ['No genera ni valida señales/IA/holdout',
                'IOC, mejor punta, posición larga pagada íntegramente con caja; no clearing real',
                'Marca a bid modelado menos costo, sin garantizar liquidación total ni drawdown entre muestras',
                'Faltan contratos especializados, eventos corporativos y benchmark histórico de caución']})


def main(argv=None):
    """Entrada local JSON; la SQLite se abre en modo read-only, sin migraciones."""
    import argparse
    from contextlib import contextmanager
    import json
    from pathlib import Path
    import sqlite3

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True)
    parser.add_argument('--input', required=True, help='Manifiesto JSON con datos y supuestos explícitos')
    args = parser.parse_args(argv)

    class ReadOnlyStore:
        @contextmanager
        def connect(self):
            connection = sqlite3.connect(Path(args.database).resolve().as_uri() + '?mode=ro', uri=True)
            connection.row_factory = sqlite3.Row
            try:
                yield connection
            finally:
                connection.close()

    from bl_candle_engine import CandleArchive, Series
    from bs_instrument_contracts import InstrumentContract
    try:
        if not Path(args.database).is_file():
            raise FileNotFoundError('Falta la base histórica explícita')
        data = json.loads(Path(args.input).read_text())
        engine = ExecutionReplay(CandleArchive(ReadOnlyStore()), Series(**data['series']),
            InstrumentContract(**data['contract']), FeeTerms(**data['fees']),
            ExecutionAssumptions(**data['assumptions']))
        books = [BookEvent(**b) for b in data['books']]
        if 'signal_config' in data:
            from bo_signal_core import SignalConfig, SessionGrid
            from by_strategy_backtest import run_strategy
            if data.get('orders') and data.get('mode') != 'CANDIDATE_STRATEGY_BACKTEST':
                raise ValueError('No mezclar órdenes preparadas con señales')
            grid_data = data['session_grid']
            result = run_strategy(engine, books, SignalConfig(**data['signal_config']),
                SessionGrid(**(grid_data | {'starts': tuple(grid_data['starts'])})),
                start=data['start'], end=data['end'], initial_cash=data['capital'])
            if 'orders' in data and data['orders'] != result['manifest']['orders']:
                raise ValueError('La corrida no reproduce las órdenes del manifiesto')
        else:
            orders = [ReplayOrder(**(o | {'evidence_ids': tuple(o['evidence_ids'])})) for o in data['orders']]
            result = engine.run(orders, books, start=data['start'], end=data['end'], initial_cash=data['capital'])
    except (ValueError, TypeError, KeyError, OSError, sqlite3.Error, ArithmeticError) as exc:
        print(json.dumps({'status': 'INVALID_REPLAY_INPUT', 'promotion_allowed': False,
                          'error_type': type(exc).__name__}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == '__main__':
    # Usar las mismas clases canónicas que importa el driver de estrategia;
    # __main__.ReplayOrder y bx_execution_replay.ReplayOrder no son el mismo tipo.
    from bx_execution_replay import main as entrypoint
    raise SystemExit(entrypoint())
