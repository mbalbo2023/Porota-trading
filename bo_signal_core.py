"""Candidato de señal de contado para evaluación OFFLINE, sin promoción.

No copia perfiles arbitrarios por familia ni usa el historial futuro para bajar
umbrales. Conserva la fórmula explícita de momentum paper, sobre cierres de
velas completas; agrega ATR simple, costos, lotes y riesgo modelado. Esto NO es
la misma estrategia que el runtime de muestras/IA y no lo reemplaza todavía.
"""
from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING

from bl_candle_engine import bounds, fingerprint, stamp
from bs_instrument_contracts import SPOT_FAMILIES, aware_datetime, decimal_value

ZERO = Decimal('0')


@dataclass(frozen=True)
class SignalConfig:
    frozen_at: str
    short_window: int
    long_window: int
    atr_period: int
    score_threshold: Decimal
    maximum_spread: Decimal
    atr_stop_multiple: Decimal
    atr_target_multiple: Decimal
    minimum_net_reward_risk: Decimal
    risk_fraction: Decimal
    maximum_position_fraction: Decimal
    stop_gap_fraction: Decimal
    maximum_bar_age_seconds: int
    order_ttl_seconds: int
    maximum_holding_seconds: int

    def __post_init__(self):
        object.__setattr__(self, 'frozen_at', stamp(self.frozen_at))
        for field in ('short_window', 'long_window', 'atr_period', 'maximum_bar_age_seconds',
                      'order_ttl_seconds', 'maximum_holding_seconds'):
            value = getattr(self, field)
            if type(value) is not int or value <= 0:
                raise ValueError('Ventanas y duraciones deben ser enteros positivos')
        if self.short_window >= self.long_window:
            raise ValueError('La ventana corta debe ser menor que la larga')
        for field in ('score_threshold', 'maximum_spread', 'atr_stop_multiple', 'atr_target_multiple',
                      'minimum_net_reward_risk', 'risk_fraction', 'maximum_position_fraction', 'stop_gap_fraction'):
            value = decimal_value(getattr(self, field), field, nonnegative=True)
            object.__setattr__(self, field, value)
        if (not 0 <= self.score_threshold <= 1 or not 0 < self.maximum_spread < 1
                or not 0 < self.risk_fraction <= 1 or not 0 < self.maximum_position_fraction <= 1
                or not 0 <= self.stop_gap_fraction < 1 or self.atr_stop_multiple <= 0
                or self.atr_target_multiple <= 0 or self.minimum_net_reward_risk <= 0):
            raise ValueError('Parámetros fuera de dominio')

    @property
    def warmup(self):
        return max(self.long_window, self.atr_period + 1)

    @property
    def version(self):
        data = {k: format(v.normalize(), 'f') if isinstance(v, Decimal) else v for k, v in asdict(self).items()}
        return 'candidate-candle-momentum-atr-v17:' + fingerprint(data)[:16]


@dataclass(frozen=True)
class SessionGrid:
    starts: tuple[str, ...]
    source: str
    known_at: str
    coverage_start: str
    coverage_end: str

    def validate(self, series, start, end):
        if (not isinstance(self.source, str) or not self.source.strip() or self.source.strip().upper() == 'UNKNOWN'
                or not isinstance(self.starts, tuple) or not self.starts):
            raise ValueError('Falta grilla explícita de sesiones')
        left, right = map(aware_datetime, (self.coverage_start, self.coverage_end))
        if not left <= aware_datetime(start) < aware_datetime(end) <= right:
            raise ValueError('Grilla no cubre el período de evaluación')
        if aware_datetime(self.known_at) > aware_datetime(start):
            raise ValueError('Grilla conocida después del comienzo del test')
        values = [stamp(t) for t in self.starts]
        if values != sorted(set(values)):
            raise ValueError('Grilla desordenada o duplicada')
        for value in values:
            begin, finish = bounds(value, series.resolution)
            if begin != value or not stamp(left) <= begin < finish <= stamp(right):
                raise ValueError('Grilla fuera de cobertura o resolución')


def window_at(archive, series, grid, config, at):
    """No sustituye una barra faltante/inválida por una anterior buena."""
    expected = [(stamp(t), bounds(t, series.resolution)[1]) for t in grid.starts
                if bounds(t, series.resolution)[1] <= stamp(at)]
    expected = expected[-config.warmup:]
    if len(expected) < config.warmup:
        return [], 'INSUFFICIENT_WARMUP'
    if (aware_datetime(at) - aware_datetime(expected[-1][1])).total_seconds() > config.maximum_bar_age_seconds:
        return [], 'STALE_BARS'
    bars = archive.read(series, as_of=at, start=expected[0][0], end=expected[-1][1],
                        qualities=None, include_synthetic=True)
    if [b['start'] for b in bars] != [t[0] for t in expected]:
        return [], 'MISSING_EXPECTED_BARS'
    if any(b['quality'] != 'COMPLETE' or b['synthetic'] or b['volume'] is None for b in bars):
        return [], 'UNVERIFIED_BARS'
    if sum((decimal_value(b['volume'], 'volumen', nonnegative=True) for b in bars), ZERO) <= 0:
        return [], 'NO_OBSERVED_VOLUME'
    return bars, ''


def indicators(bars, book, config):
    closes = [decimal_value(b['close'], 'cierre', positive=True) for b in bars]
    short = sum(closes[-config.short_window:], ZERO) / config.short_window
    long = sum(closes[-config.long_window:], ZERO) / config.long_window
    momentum = short / long - 1
    spread = book.ask / book.bid - 1
    score = max(ZERO, min(Decimal(1), Decimal('.5') + momentum * 40 - spread * 10))
    ranges = []
    for previous, bar in zip(bars, bars[1:]):
        high, low, close = (decimal_value(v, 'OHLC', positive=True)
                            for v in (bar['high'], bar['low'], previous['close']))
        ranges.append(max(high - low, abs(high - close), abs(low - close)))
    atr = sum(ranges[-config.atr_period:], ZERO) / config.atr_period
    return {'sma_short': short, 'sma_long': long, 'momentum': momentum,
            'spread': spread, 'score': score, 'atr_simple': atr}


def trade_economics(contract, fees, assumptions, quantity, entry, stop, target, gap):
    """Pérdida modelada con gap explícito; no es una garantía de pérdida máxima."""
    from bx_execution_replay import money
    entry_notional = money(contract.notional(entry, quantity))
    stop_fill = assumptions.price_at(stop * (1 - gap), 'SELL')
    target_fill = assumptions.price_at(target, 'SELL')
    stop_notional = money(contract.notional(stop_fill, quantity))
    target_notional = money(contract.notional(target_fill, quantity))
    debit = entry_notional + fees.fee(entry_notional)
    loss = debit - stop_notional + fees.fee(stop_notional)
    reward = target_notional - fees.fee(target_notional) - debit
    return {'entry_debit': debit, 'modeled_stop_loss': loss, 'target_net_profit': reward,
            'net_reward_risk': reward / loss if loss > 0 else ZERO}


def candidate(archive, series, contract, fees, assumptions, grid, config, view):
    """Señal pura: cada evaluación usa sólo el estado y datos conocidos a `at`."""
    if contract.family not in SPOT_FAMILIES:
        raise ValueError('Sin perfil implícito de acciones para familias especializadas')
    at, book = view['at'], view['book']
    result = {'action': 'HOLD', 'code': '', 'features': {}, 'evidence_ids': (), 'promotion_allowed': False}
    def reject(code):
        return result | {'code': code}
    if not view['book_usable']:
        return reject('BOOK_NOT_USABLE')
    bars, error = window_at(archive, series, grid, config, at)
    if error:
        return reject(error)
    result['evidence_ids'] = tuple(b['version_id'] for b in bars)
    result['bar_start'] = bars[-1]['start']
    features = indicators(bars, book, config)
    result['features'] = features
    if features['spread'] > config.maximum_spread:
        return reject('SPREAD_TOO_WIDE')
    if features['score'] < config.score_threshold:
        return reject('SCORE_BELOW_THRESHOLD')
    if features['atr_simple'] <= 0:
        return reject('NO_VOLATILITY_ESTIMATE')
    if not fees.available(at, at):
        return reject('COST_TERMS_UNAVAILABLE')
    entry = assumptions.price(book, 'BUY')
    tick = assumptions.price_tick
    stop = ((entry - config.atr_stop_multiple * features['atr_simple']) / tick).to_integral_value(rounding=ROUND_FLOOR) * tick
    target = ((entry + config.atr_target_multiple * features['atr_simple']) / tick).to_integral_value(rounding=ROUND_CEILING) * tick
    if not 0 < stop < entry < target or assumptions.price_at(stop * (1 - config.stop_gap_fraction), 'SELL') <= 0:
        return reject('INVALID_STOP_TARGET')
    cash = decimal_value(view['cash'], 'caja', nonnegative=True)
    equity = decimal_value(view['equity'], 'patrimonio', nonnegative=True) if view['equity'] is not None else ZERO
    if cash <= 0 or equity <= 0:
        return reject('NO_AVAILABLE_CAPITAL')
    risk_budget = equity * config.risk_fraction
    cash_budget = min(cash, equity * config.maximum_position_fraction)
    lot = contract.quantity_step
    # Un único instrumento/posición larga; no asumir margen ni crédito.
    high = int(min(book.ask_size * assumptions.participation / lot,
                   cash_budget / (entry * contract.cash_multiplier * lot)))
    low = 0
    while low < high:
        middle = (low + high + 1) // 2
        economics = trade_economics(contract, fees, assumptions, lot * middle, entry, stop, target, config.stop_gap_fraction)
        if economics['entry_debit'] <= cash_budget and economics['modeled_stop_loss'] <= risk_budget:
            low = middle
        else:
            high = middle - 1
    if low == 0:
        return reject('NO_LOT_WITHIN_CASH_RISK_DEPTH')
    quantity = lot * low
    economics = trade_economics(contract, fees, assumptions, quantity, entry, stop, target, config.stop_gap_fraction)
    features.update(economics, risk_budget=risk_budget, cash_budget=cash_budget)
    if economics['target_net_profit'] <= 0 or economics['net_reward_risk'] < config.minimum_net_reward_risk:
        return reject('NET_REWARD_RISK_TOO_LOW')
    return result | {'action': 'BUY', 'code': 'CANDIDATE_ONLY', 'quantity': quantity,
                     'entry_cap': entry, 'stop': stop, 'target': target}
