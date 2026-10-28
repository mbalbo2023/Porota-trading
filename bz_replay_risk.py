"""Riesgo diario offline: una moneda, capital inicial, sin flujos externos.

Comparte el corte inclusivo con PAPER. Sin marca conciliada de medianoche,
un carry bloquea entradas todo ese día; nunca inventa un cierre anterior.
"""
from dataclasses import dataclass
from decimal import Decimal

from bl_candle_engine import stamp
from bs_instrument_contracts import aware_datetime, cash_currency, decimal_value
from bw_daily_risk import TZ, loss_limit_crossed

ZERO = Decimal('0')


@dataclass(frozen=True)
class ReplayRiskConfig:
    frozen_at: str
    daily_loss_pct: Decimal

    def __post_init__(self):
        object.__setattr__(self, 'frozen_at', stamp(self.frozen_at))
        limit = decimal_value(self.daily_loss_pct, 'pérdida diaria %', positive=True)
        if limit > 100:
            raise ValueError('Porcentaje diario: 1 significa 1%, máximo 100')
        object.__setattr__(self, 'daily_loss_pct', limit)


class ReplayDailyRisk:
    def __init__(self, config, currency, capital, start):
        if not isinstance(config, ReplayRiskConfig):
            raise ValueError('Falta configuración explícita de riesgo diario')
        self.start = aware_datetime(start)
        if aware_datetime(config.frozen_at) > self.start:
            raise ValueError('Congelar riesgo antes de comenzar el período')
        self.config, self.currency = config, cash_currency(currency)
        self.capital = decimal_value(capital, 'capital', positive=True)
        self.days, self.observations = {}, []
        self.last_at = self.start

    @staticmethod
    def day(at):
        return aware_datetime(at).astimezone(TZ).date().isoformat()

    def evaluate(self, at, *, held, equity, quality, realized_total, realized_today, phase):
        at = aware_datetime(at)
        if at < self.last_at:
            raise ValueError('Reloj de riesgo retrocedió')
        held = decimal_value(held, 'tenencia', nonnegative=True)
        total = decimal_value(realized_total, 'realizado acumulado')
        today = decimal_value(realized_today, 'realizado hoy')
        equity = decimal_value(equity, 'patrimonio') if equity is not None else None
        if quality != 'CURRENT':
            equity = None
        day = self.day(at)
        if day not in self.days:
            # Evaluar siempre antes de los fills del día. Cash + créditos de
            # ventas anteriores pertenece al patrimonio aunque aún no liquide.
            baseline = self.capital + total - today if not held else None
            self.days[day] = {'day': day, 'currency': self.currency,
                'baseline_equity': baseline, 'limit_pct': self.config.daily_loss_pct,
                'loss_budget': baseline * self.config.daily_loss_pct / 100 if baseline is not None else None,
                'latched_at': None}
        record = self.days[day]
        baseline, budget = record['baseline_equity'], record['loss_budget']
        daily = equity - baseline if equity is not None and baseline is not None else None
        if baseline is None:
            state = 'BASELINE_UNAVAILABLE'
        elif baseline <= 0:
            state = 'NO_CAPITAL'
        else:
            if not record['latched_at'] and loss_limit_crossed(daily, today, budget):
                record['latched_at'] = stamp(at)
            state = 'LATCHED' if record['latched_at'] else 'READY' if equity is not None else 'STALE_MARKS'
        # Beneficios no amplían el presupuesto de nuevas entradas. Tampoco
        # compensar pérdidas realizadas con ganancias abiertas para dimensionar.
        remaining = max(ZERO, min(budget, budget + daily, budget + today)) if state == 'READY' else ZERO
        record.update(state=state, last_equity=equity, daily_pnl=daily,
            realized_today=today, remaining_budget=remaining, evaluated_at=stamp(at))
        self.last_at = at
        snapshot = dict(record, phase=phase)
        self.observations.append(snapshot)
        return dict(snapshot)

    def permits_projected_equity(self, at, equity):
        """Rechazar una compra hipotética no registra pérdidas ni activa latch."""
        record = self.days[self.day(at)]
        if record['state'] != 'READY' or equity is None:
            return False
        daily = decimal_value(equity, 'patrimonio proyectado') - record['baseline_equity']
        return not loss_limit_crossed(daily, record['realized_today'], record['loss_budget'])

    def report(self):
        return {'configured': True, 'days': list(self.days.values()), 'observations': self.observations}
