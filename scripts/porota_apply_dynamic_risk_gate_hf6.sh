#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="${1:-/opt/porota-trading}"
MODE="${2:---check}"
TARGET="$ROOT/be_paper_engine.py"

if [[ "$MODE" != "--check" && "$MODE" != "--apply" ]]; then
  echo "USAGE: $0 ROOT [--check|--apply]" >&2
  exit 2
fi
[[ -f "$TARGET" ]] || { echo "TARGET_MISSING=$TARGET"; exit 3; }

python3 - "$TARGET" "$MODE" <<'PY'
from pathlib import Path
import sys

path=Path(sys.argv[1]); mode=sys.argv[2]
text=path.read_text(encoding='utf-8')
original=text

old1='''        if self.store.open_position(q.symbol):\n            return False, "Ya existe una posicion abierta del simbolo", None\n        if len(self.store.open_positions()) >= self.max_positions:\n            self.store.event("REJECTED_PAPER", f"{q.symbol}: maximo de posiciones paper")\n            return False, "Límite máximo de posiciones paper alcanzado", None\n        entry = (q.ask * (1 + self.slippage)).quantize(Decimal("0.0001"))\n'''
new1='''        if self.store.open_position(q.symbol):\n            return False, "Ya existe una posicion abierta del simbolo", None\n        from de_concurrent_risk_capacity_hf6 import emergency_position_guard\n        from dh_paper_dynamic_risk_gate_hf6 import (\n            ConcurrentRiskGateError, candidate_stop_risk, portfolio_capacity, snapshot_dict)\n        emergency_cap = int(os.getenv("PAPER_EMERGENCY_MAX_OPEN_POSITIONS", "50"))\n        opened_now = self.store.open_positions()\n        if emergency_position_guard(len(opened_now), emergency_cap=emergency_cap):\n            self.store.event("REJECTED_PAPER", f"{q.symbol}: cap técnico anti-runaway")\n            return False, "EMERGENCY_POSITION_CAP", None\n        entry = (q.ask * (1 + self.slippage)).quantize(Decimal("0.0001"))\n'''

old2='''        # Incluye ambos tramos y deslizamiento de salida, sin afirmar que un\n        # stop garantice este precio ante gaps o falta de liquidez.\n        by_risk = self._quantity_in_budget(\n            (entry - modeled_stop_fill) * factor, capital * self.risk_pct,\n            lambda qty: self._cost(entry * factor, qty, q.asset_class) +\n                        self._cost(modeled_stop_fill * factor, qty, q.asset_class))\n'''
new2='''        # Incluye ambos tramos y deslizamiento de salida, sin afirmar que un\n        # stop garantice este precio ante gaps o falta de liquidez. La cantidad\n        # se acota por el menor entre el riesgo por trade y la capacidad\n        # concurrente que queda hasta el soft stop diario.\n        try:\n            concurrent_before = portfolio_capacity(\n                self, currency, at, quotes={q.symbol:q})\n        except ConcurrentRiskGateError as exc:\n            return False, str(exc), None\n        per_trade_risk_budget = capital * self.risk_pct\n        concurrent_risk_budget = min(\n            per_trade_risk_budget, concurrent_before.remaining_before_candidate)\n        if concurrent_risk_budget <= 0:\n            self.store.event("REJECTED_PAPER", f"{q.symbol}: riesgo concurrente agotado")\n            return False, "CONCURRENT_RISK_BUDGET_EXHAUSTED", None\n        by_risk = self._quantity_in_budget(\n            (entry - modeled_stop_fill) * factor, concurrent_risk_budget,\n            lambda qty: self._cost(entry * factor, qty, q.asset_class) +\n                        self._cost(modeled_stop_fill * factor, qty, q.asset_class))\n'''

old3='''            "initial_capital": str(self.initial_balances[currency]),\n            "risk_budget": str(capital * self.risk_pct),\n            "max_position_pct": str(self.max_position_pct),\n'''
new3='''            "initial_capital": str(self.initial_balances[currency]),\n            "risk_budget": str(concurrent_risk_budget),\n            "per_trade_risk_budget": str(per_trade_risk_budget),\n            "concurrent_risk_before": snapshot_dict(concurrent_before),\n            "emergency_position_cap": emergency_cap,\n            "max_position_pct": str(self.max_position_pct),\n'''

old4='''        if currency == "ARS":\n            features.update(initial_capital_ars=str(capital), risk_budget_ars=str(capital * self.risk_pct))\n        if qty < 1:\n'''
new4='''        if currency == "ARS":\n            features.update(initial_capital_ars=str(capital), risk_budget_ars=str(concurrent_risk_budget))\n        if qty < 1:\n'''

old5='''            return False, (f"Portón patrimonial/liquidez: {binding} dejó cantidad "\n                           f"ejecutable en {limits[binding]}"), None\n        paper_id = "PAPER-" + uuid.uuid4().hex\n        cost = self._cost(entry * factor, qty, q.asset_class)\n'''
new5='''            return False, (f"Portón patrimonial/liquidez: {binding} dejó cantidad "\n                           f"ejecutable en {limits[binding]}"), None\n        candidate_risk_value = candidate_stop_risk(\n            self, entry_price=entry, stop_price=stop, quantity=qty,\n            cash_multiplier=factor, asset_class=q.asset_class)\n        try:\n            concurrent_preview = portfolio_capacity(\n                self, currency, at, candidate_risk=candidate_risk_value,\n                quotes={q.symbol:q})\n        except ConcurrentRiskGateError as exc:\n            return False, str(exc), None\n        if not concurrent_preview.admitted:\n            self.store.event("REJECTED_PAPER", f"{q.symbol}: {concurrent_preview.reason}")\n            return False, concurrent_preview.reason, None\n        features.update(\n            candidate_stop_risk=str(candidate_risk_value),\n            concurrent_risk_preview=snapshot_dict(concurrent_preview))\n        paper_id = "PAPER-" + uuid.uuid4().hex\n        cost = self._cost(entry * factor, qty, q.asset_class)\n'''

old6='''            opened = self.store.open_positions()\n            exposure_now = sum((D(p["entry_price"]) * D(p["quantity"]) * self._position_multiplier(p)\n                                for p in opened if p["currency"] == currency), ZERO)\n            if (len(opened) >= self.max_positions or any(p["symbol"] == q.symbol for p in opened)\n                    or entry * qty * factor + cost > self._cash(as_of=at, currency=currency, connection=c, for_execution=True)\n                    or exposure_now + entry * qty * factor > capital * self.max_total_exposure_pct):\n                return False, "Caja, posiciones o exposición cambiaron antes de registrar la compra", None\n'''
new6='''            opened = spot_ledger.positions_at(c, at)[0]\n            if emergency_position_guard(len(opened), emergency_cap=emergency_cap):\n                return False, "EMERGENCY_POSITION_CAP", None\n            try:\n                concurrent_locked = portfolio_capacity(\n                    self, currency, at, candidate_risk=candidate_risk_value,\n                    connection=c, quotes={q.symbol:q})\n            except ConcurrentRiskGateError as exc:\n                return False, str(exc), None\n            if not concurrent_locked.admitted:\n                return False, concurrent_locked.reason, None\n            features["concurrent_risk_locked"] = snapshot_dict(concurrent_locked)\n            exposure_now = sum((D(p["entry_price"]) * D(p["quantity"]) * self._position_multiplier(p)\n                                for p in opened if p["currency"] == currency), ZERO)\n            if (any(p["symbol"] == q.symbol for p in opened)\n                    or entry * qty * factor + cost > self._cash(as_of=at, currency=currency, connection=c, for_execution=True)\n                    or exposure_now + entry * qty * factor > capital * self.max_total_exposure_pct):\n                return False, "Caja, identidad o exposición cambiaron antes de registrar la compra", None\n'''

pairs=[(old1,new1,'fixed-position-precheck'),(old2,new2,'risk-budget-sizing'),
       (old3,new3,'risk-features'),(old4,new4,'ars-risk-feature'),
       (old5,new5,'candidate-risk-preview'),(old6,new6,'locked-revalidation')]
for old,new,label in pairs:
    count=text.count(old)
    if count != 1:
        raise SystemExit(f'ANCHOR_{label}=EXPECTED_1_GOT_{count}')
    text=text.replace(old,new,1)

# The legacy max_positions attribute may remain for backward constructor/API
# compatibility, but it must no longer be referenced by _open admission.
segment=text[text.index('    def _open('):text.index('    def _risk_capital(')]
if 'self.max_positions' in segment:
    raise SystemExit('LEGACY_MAX_POSITIONS_STILL_GATES_OPEN')
for required in ('CONCURRENT_RISK_BUDGET_EXHAUSTED','PAPER_EMERGENCY_MAX_OPEN_POSITIONS',
                 'concurrent_risk_locked','spot_ledger.positions_at(c, at)[0]'):
    if required not in segment:
        raise SystemExit('DYNAMIC_RISK_WIRING_MISSING_'+required)

print('DYNAMIC_RISK_PATCH=READY')
print('LEGACY_MAX_POSITIONS_NORMAL_GATE=REMOVED')
print('EMERGENCY_CAP_ONLY=YES')
print('LOCKED_REVALIDATION=YES')
if mode=='--apply':
    path.write_text(text,encoding='utf-8')
    print('FILE_MUTATION=YES')
else:
    print('FILE_MUTATION=NO')
PY
