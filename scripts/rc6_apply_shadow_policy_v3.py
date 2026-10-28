#!/usr/bin/env python3
"""Robust, idempotent RC6 SHADOW-first migration.

This is intentionally tolerant of independent RC6 dashboard/rewire commits while
remaining fail-closed on the invariants that matter.
"""
from pathlib import Path
import re


def read(path): return Path(path).read_text(encoding='utf-8')
def write(path,text): Path(path).write_text(text,encoding='utf-8')


def ensure_replace(path, old, new, *, minimum_old=0):
    text=read(path)
    if new in text and old not in text:
        return False
    n=text.count(old)
    if n < minimum_old:
        raise SystemExit(f'RC6_SHADOW_V3_ANCHOR:{path}:found={n}:minimum={minimum_old}')
    if n:
        write(path,text.replace(old,new))
        return True
    return False


def mode_manager():
    p='porota_mode_manager.py'; text=read(p)
    text=text.replace('"PAPER_ECONOMIC_GATE_MODE": "BINDING"','"PAPER_ECONOMIC_GATE_MODE": "SHADOW"')
    if '"PAPER_ECONOMIC_GATE_MODE": "BINDING"' in text or text.count('"PAPER_ECONOMIC_GATE_MODE": "SHADOW"') < 2:
        raise SystemExit('RC6_SHADOW_MODE_MANAGER_NOT_ENFORCED')
    write(p,text)


def dashboard():
    p='bg_paper_dashboard.py'; text=read(p)
    text=text.replace('PAPER_ECONOMIC_GATE_MODE = os.getenv("PAPER_ECONOMIC_GATE_MODE", "BINDING").upper()',
                      'PAPER_ECONOMIC_GATE_MODE = os.getenv("PAPER_ECONOMIC_GATE_MODE", "SHADOW").upper()')
    if 'os.getenv("PAPER_ECONOMIC_GATE_MODE", "BINDING")' in text:
        raise SystemExit('RC6_SHADOW_DASHBOARD_BINDING_DEFAULT')
    write(p,text)


def observer():
    p='bf_production_paper_observer.py'; text=read(p)
    text=text.replace('os.getenv("PAPER_ECONOMIC_GATE_MODE", "BINDING").upper() == "BINDING"',
                      'os.getenv("PAPER_ECONOMIC_GATE_MODE", "SHADOW").upper() == "BINDING"')
    if 'os.getenv("PAPER_ECONOMIC_GATE_MODE", "BINDING")' in text:
        raise SystemExit('RC6_SHADOW_OBSERVER_BINDING_DEFAULT')
    write(p,text)


def engine():
    p='be_paper_engine.py'; text=read(p)
    marker='            features["economics"] = economics\n'
    mode_line='            features["economics_mode"] = self.economics_mode\n'
    if mode_line not in text:
        if text.count(marker) != 1:
            raise SystemExit('RC6_SHADOW_ENGINE_ECONOMICS_MARKER')
        text=text.replace(marker,marker+mode_line,1)

    old='''        economics = detail.get("economics") if isinstance(detail, dict) else None\n        contradiction = (final == "OPENED_SIMULATED" and\n            (technical != "APPROVE" or patrimonial != "APPROVE" or\n             not isinstance(economics, dict) or economics.get("passed") is not True))'''
    new='''        economics = detail.get("economics") if isinstance(detail, dict) else None\n        economics_mode = str(detail.get("economics_mode") or "BINDING").upper() if isinstance(detail, dict) else "BINDING"\n        economic_violation = (economics_mode == "BINDING" and\n                              (not isinstance(economics, dict) or economics.get("passed") is not True))\n        contradiction = (final == "OPENED_SIMULATED" and\n            (technical != "APPROVE" or patrimonial != "APPROVE" or economic_violation))'''
    if old in text:
        text=text.replace(old,new,1)
    elif 'economic_violation = (economics_mode == "BINDING"' not in text:
        raise SystemExit('RC6_SHADOW_ENGINE_CONTRADICTION_MARKER')
    write(p,text)


def truth_layer():
    p='ep_dashboard_truth_layer_rc6.py'; text=read(p)
    # Preserve structure but make current campaign semantics explicit.
    text=text.replace('policy_ok = policy == "BINDING"','policy_shadow = policy == "SHADOW"')
    text=text.replace('"Aprueban economía", metrics["passed"], "Superan el portón matemático vigente"',
                      '"Would allow", metrics["passed"], "SHADOW habría permitido estas señales; no es autorización real"')
    text=text.replace('"Fallan economía", metrics["failed"], "Con BINDING deben quedar bloqueadas", "red" if policy_ok and metrics["failed"] else "gray"',
                      '"Would block", metrics["failed"], "SHADOW habría bloqueado, pero PAPER sigue para aprender", "yellow" if metrics["failed"] else "gray"')
    text=text.replace('"Abren pese al fallo", metrics["opened_with_failure"], "Invariante: cero cuando la política es BINDING", "red" if metrics["opened_with_failure"] else "green"',
                      '"PAPER pese a would-block", metrics["opened_with_failure"], "Esperado en SHADOW: conserva el contrafactual para aprender", "green" if policy_shadow else "yellow"')
    text=text.replace('<b>No es un “modo BINDING” del sistema.</b>', '<b>Etapa de aprendizaje SHADOW.</b>')
    text=text.replace('El portón sólo decide si una señal simulada puede abrir "\n        "cuando no cubre comisión, derechos, spread, deslizamiento y reward/risk neto.',
                      'En SHADOW el portón calcula costos, spread, slippage y reward/risk, "\n        "pero NO veta PAPER: registra qué habría bloqueado y luego se contrasta contra el resultado realizado.')
    write(p,text)


def policy_module():
    p='ck_policy_gate_hf6.py'; text=read(p)
    text=text.replace('"""RC4 policy gates: evaluate always; only BINDING has blocking authority.',
                      '"""Policy gates: evaluate always; only explicitly promoted BINDING has blocking authority.')
    write(p,text)


def checkpoint():
    p='POROTA_TRADING_CHECKPOINT_CANONICO_RC6_2026-09-05.md'; text=read(p)
    text=text.replace('`BINDING` es la política del portón económico PAPER; el modo global continúa `PRODUCTION_PAPER` y la ejecución `SIMULATED`.',
                      'el portón económico estaba efectivamente BINDING en RC5 y eso contradice el plan de aprendizaje; RC6 lo restaura a SHADOW. El modo global continúa `PRODUCTION_PAPER` y la ejecución `SIMULATED`.')
    marker='## 17. P0-APRENDIZAJE — SHADOW → BINDING → REAL-MONEY GOVERNANCE'
    if marker not in text:
        text=text.rstrip()+'''\n\n## 17. P0-APRENDIZAJE — SHADOW → BINDING → REAL-MONEY GOVERNANCE\n\nDecisión fundamental del operador, 2026-09-05: durante las primeras ruedas PAPER se prefiere perder dinero ficticio y aprender antes que bloquear prematuramente oportunidades. Los controles de selección/rentabilidad calculan y persisten su contrafactual pero no tienen autoridad de veto.\n\n### Learning gates actuales\n- Portón económico: `SHADOW` — NO bloquea PAPER.\n- Expectancy: `SHADOW/OBSERVATION_ONLY` — NO bloquea PAPER.\n- Régimen: `SHADOW/ALERT_ONLY` — NO bloquea PAPER.\n- Concentración sectorial: `SHADOW/OBSERVATION_ONLY` — NO bloquea PAPER. La decisión antigua de hacerlo BINDING desde el inicio queda superada para esta campaña.\n\n### Hard safety separado\nNo son experimentos ni deben llamarse BINDING en la UI: `REAL_ORDER_CAPABILITY=BLOCKED`, ejecución `SIMULATED`, `real_orders_sent=0`, order routing real bloqueado, sesión cerrada sin nueva ejecución, libro stale/missing sin fill inventado, DB/config crítica fail-closed y derivados reales bloqueados. Mostrar como `SAFETY HARD BLOCK — NO APRENDIBLE / NO PROMOCIONABLE`.\n\n### Camino por policy\n`COLLECTING_EVIDENCE → SHADOW → SHADOW_VALIDATION → EVIDENCE_SUFFICIENT → ELIGIBLE_FOR_BINDING_DECISION → autorización explícita + release versionada → BINDING_PAPER → BINDING_VALIDATION → BINDING_PAPER_PROVEN → REAL_MONEY_GOVERNANCE → CANARY_REAL_MONEY`.\n\nNo hay promoción automática por porcentaje, tiempo, número de ruedas, dashboard o backtest. La ventana ~20 ruedas/4 semanas es objetivo de observación, no gatillo.\n\n### Evidencia contrafactual mínima\nPor operación/policy: would_allow/would_block, motivo/inputs/provenance, resultado PAPER, pérdida que habría evitado, ganancia que habría eliminado, false positives/false negatives, PnL real vs contrafactual, precision, cohortes y lecciones. `/validacion` debe mostrar el camino global M0→M11 y un carril independiente SHADOW→BINDING para cada gate. Real-money permanece BLOCKED.\n'''
    write(p,text+'\n')


def verify():
    mode=read('porota_mode_manager.py')
    dash=read('bg_paper_dashboard.py')
    obs=read('bf_production_paper_observer.py')
    eng=read('be_paper_engine.py')
    cp=read('POROTA_TRADING_CHECKPOINT_CANONICO_RC6_2026-09-05.md')
    assert '"PAPER_ECONOMIC_GATE_MODE": "BINDING"' not in mode
    assert mode.count('"PAPER_ECONOMIC_GATE_MODE": "SHADOW"') >= 2
    assert 'os.getenv("PAPER_ECONOMIC_GATE_MODE", "BINDING")' not in dash+obs
    assert 'features["economics_mode"] = self.economics_mode' in eng
    assert 'economic_violation = (economics_mode == "BINDING"' in eng
    assert 'P0-APRENDIZAJE — SHADOW' in cp


def main():
    mode_manager();dashboard();observer();engine();truth_layer();policy_module();checkpoint();verify()
    print('RC6_SHADOW_POLICY_V3=GREEN')

if __name__=='__main__': main()
