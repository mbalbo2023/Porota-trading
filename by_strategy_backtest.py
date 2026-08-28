"""Evaluación offline del candidato de velas, conectada al ledger de ejecución.

No reproduce Gemini ni el umbral adaptativo del runtime paper de muestras.
No es entrenamiento walk-forward: configuración y grilla congeladas, una serie,
sin promoción automática. Cada escenario vuelve a decidir con sus propios costos.
"""
from dataclasses import asdict
from datetime import timedelta

from bl_candle_engine import fingerprint, stamp
from bo_signal_core import candidate, trade_economics
from bs_instrument_contracts import aware_datetime
from bx_execution_replay import ReplayOrder, serializable


class CandleStrategy:
    def __init__(self, engine, config, grid):
        self.engine, self.config, self.grid = engine, config, grid
        self.plan = None
        self.last_entry_bar = None
        self.decisions = []

    def record(self, view, code, action='HOLD', **detail):
        self.decisions.append(serializable({'at': view['at'], 'book_id': view['book'].event_id,
            'action': action, 'code': code, 'cash': view['cash'], 'held': view['held'],
            'strategy_version': self.config.version, 'promotion_allowed': False, **detail}))

    def on_event(self, view):
        at = aware_datetime(view['at'])
        book = view['book']
        states = {o['order_id']: o for o in view['orders']}
        # No volver a enviar una orden que sigue viva durante latencia o sesión cerrada.
        if any(o['status'] == 'PENDING' for o in states.values()):
            self.record(view, 'ORDER_PENDING')
            return None
        if self.plan is not None:
            entry_fills = [f for f in view['fills'] if f['order_id'] == self.plan['entry_order_id']]
            if view['held'] > 0:
                if not entry_fills:
                    raise ValueError('Tenencia sin fill del plan registrado')
                opened_at = aware_datetime(entry_fills[0]['executed_at'])
                # El timeout puede quedar decidido aunque falte un libro usable.
                if at >= opened_at + timedelta(seconds=self.config.maximum_holding_seconds):
                    self.plan['exit_reason'] = self.plan['exit_reason'] or 'MAX_HOLDING_TIME'
                if view['book_usable']:
                    if book.bid <= self.plan['stop']:
                        self.plan['exit_reason'] = self.plan['exit_reason'] or 'STOP_TRIGGER'
                    elif book.bid >= self.plan['target']:
                        self.plan['exit_reason'] = self.plan['exit_reason'] or 'TARGET_TRIGGER'
                    # Un fill parcial puede volver relevante el mínimo/fijo de costos.
                    # Se revisa el neto realmente abierto; no se llama "aprobado" al
                    # presupuesto original si su economía cambió al ejecutar.
                    if self.engine.fees.available(at, at):
                        qty = view['held']
                        # Para varias salidas parciales, el costo promedio por unidad
                        # ya vive en basis. Usar ese débito directamente en el ratio.
                        economics = trade_economics(self.engine.contract, self.engine.fees,
                            self.engine.assumptions, qty, entry_fills[0]['price'],
                            self.plan['stop'], self.plan['target'], self.config.stop_gap_fraction)
                        correction = view['basis'] - economics['entry_debit']
                        reward = economics['target_net_profit'] - correction
                        risk = economics['modeled_stop_loss'] + correction
                        if reward <= 0 or (risk > 0 and reward / risk < self.config.minimum_net_reward_risk):
                            self.plan['exit_reason'] = self.plan['exit_reason'] or 'FILL_ECONOMICS_CHANGED'
                if self.plan['exit_reason']:
                    if not view['book_usable']:
                        self.record(view, 'EXIT_WAITING_FOR_BOOK', exit_reason=self.plan['exit_reason'])
                        return None
                    order = ReplayOrder(order_id=f"exit:{self.plan['entry_order_id']}:{len(self.decisions)}",
                        series_id=self.engine.series.key, strategy_version=self.config.version,
                        submitted_at=stamp(at), expires_at=stamp(at + timedelta(seconds=self.config.order_ttl_seconds)),
                        side='SELL', quantity=view['held'], evidence_ids=self.plan['evidence_ids'],
                        entry_order_id=self.plan['entry_order_id'])
                    self.record(view, self.plan['exit_reason'], 'SELL', order_id=order.order_id,
                                quantity=view['held'], entry_order_id=self.plan['entry_order_id'])
                    return order
                self.record(view, 'POSITION_OPEN')
                return None
            self.plan = None
            if entry_fills:
                self.record(view, 'FLAT_AFTER_EXIT')
                return None

        signal = candidate(self.engine.archive, self.engine.series, self.engine.contract,
            self.engine.fees, self.engine.assumptions, self.grid, self.config, view)
        if signal['action'] != 'BUY':
            self.record(view, signal['code'], features=signal['features'], evidence_ids=signal['evidence_ids'])
            return None
        if signal['bar_start'] == self.last_entry_bar:
            self.record(view, 'ENTRY_ALREADY_ATTEMPTED_FOR_BAR')
            return None
        self.last_entry_bar = signal['bar_start']
        entry_id = 'entry:' + fingerprint({'version': self.config.version, 'series': self.engine.series.key,
                                           'bar': signal['bar_start'], 'at': stamp(at)})[:24]
        self.plan = {'entry_order_id': entry_id, 'stop': signal['stop'], 'target': signal['target'],
                     'evidence_ids': signal['evidence_ids'], 'exit_reason': ''}
        order = ReplayOrder(entry_id, self.engine.series.key, self.config.version, stamp(at),
            stamp(at + timedelta(seconds=self.config.order_ttl_seconds)), 'BUY', signal['quantity'],
            signal['evidence_ids'], price_limit=signal['entry_cap'])
        self.record(view, 'CANDIDATE_ENTRY', 'BUY', order_id=entry_id, features=signal['features'],
                    evidence_ids=signal['evidence_ids'], quantity=signal['quantity'],
                    stop=signal['stop'], target=signal['target'], price_limit=signal['entry_cap'])
        return order


def run_strategy(engine, books, config, grid, *, start, end, initial_cash):
    if aware_datetime(config.frozen_at) > aware_datetime(start):
        raise ValueError('Los parámetros deben estar congelados antes del período de prueba')
    grid.validate(engine.series, start, end)
    strategy = CandleStrategy(engine, config, grid)
    result = engine.run([], books, start=start, end=end, initial_cash=initial_cash, strategy=strategy)
    result['execution_run_id'] = result['run_id']
    result['mode'] = 'CANDIDATE_STRATEGY_BACKTEST'
    result['strategy_version'] = config.version
    result['decisions'] = strategy.decisions
    result['manifest'].update(serializable({'mode': result['mode'], 'signal_config': asdict(config),
        'session_grid': asdict(grid), 'decision_trace': strategy.decisions}))
    result['run_id'] = fingerprint(result['manifest'])
    result['limitations'] = [
        'Candidato momentum/ATR de contado, no la estrategia completa paper con IA y riesgo diario',
        'Sin calibración walk-forward, holdout independiente, eventos corporativos ni benchmark de caución',
        'Parámetros y grilla aportados, no acreditan por sí solos calidad del feed ni rentabilidad',
        'Stop es un disparador: el fill depende de un libro posterior y puede exceder el riesgo modelado',
        'IOC/primera punta, una serie, posición larga; familia especializada requiere otro ejecutor',
        'Drawdown sólo entre marcas observadas, sin garantizar liquidación íntegra de la posición']
    return result
