#!/usr/bin/env python3
"""Apply the RC6 SHADOW-first learning decision deterministically.

Build-time repository patcher only.  It refuses to edit when the expected RC5
anchors are absent/duplicated.  It never touches runtime data.
"""
from pathlib import Path


def replace_exact(path: str, old: str, new: str, expected: int = 1) -> int:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    found = text.count(old)
    if found == 0 and new in text:
        return 0
    if found != expected:
        raise SystemExit(f"RC6_SHADOW_ANCHOR_COUNT:{path}:{found}:expected={expected}")
    p.write_text(text.replace(old, new), encoding="utf-8")
    return found


def patch_mode_manager() -> None:
    replace_exact(
        "porota_mode_manager.py",
        '"PAPER_ECONOMIC_GATE_MODE": "BINDING"',
        '"PAPER_ECONOMIC_GATE_MODE": "SHADOW"',
        expected=2,
    )


def patch_dashboard_default() -> None:
    replace_exact(
        "bg_paper_dashboard.py",
        'PAPER_ECONOMIC_GATE_MODE = os.getenv("PAPER_ECONOMIC_GATE_MODE", "BINDING").upper()',
        'PAPER_ECONOMIC_GATE_MODE = os.getenv("PAPER_ECONOMIC_GATE_MODE", "SHADOW").upper()',
        expected=1,
    )


def patch_truth_layer() -> None:
    path = Path("ep_dashboard_truth_layer_rc6.py")
    text = path.read_text(encoding="utf-8")
    old = '''    policy_ok = policy == "BINDING"\n    cards = "".join((\n        bg._card("Evaluaciones económicas", metrics["evaluated"], "Señales BUY PAPER evaluadas hoy", "gray"),\n        bg._card("Aprueban economía", metrics["passed"], "Superan el portón matemático vigente", "green"),\n        bg._card("Fallan economía", metrics["failed"], "Con BINDING deben quedar bloqueadas", "red" if policy_ok and metrics["failed"] else "gray"),\n        bg._card("Abren pese al fallo", metrics["opened_with_failure"], "Invariante: cero cuando la política es BINDING", "red" if metrics["opened_with_failure"] else "green"),\n    ))'''
    new = '''    policy_shadow = policy == "SHADOW"\n    cards = "".join((\n        bg._card("Evaluaciones económicas", metrics["evaluated"], "Señales BUY PAPER evaluadas hoy", "gray"),\n        bg._card("Would allow", metrics["passed"], "SHADOW habría permitido estas señales; no es autorización real", "green"),\n        bg._card("Would block", metrics["failed"], "SHADOW habría bloqueado, pero PAPER sigue para aprender", "yellow" if metrics["failed"] else "gray"),\n        bg._card("Operaciones PAPER pese a would-block", metrics["opened_with_failure"], "Esperado en SHADOW: conserva el contrafactual para aprender", "green" if policy_shadow else "yellow"),\n    ))'''
    if old in text:
        text = text.replace(old, new, 1)
    elif new not in text:
        raise SystemExit("RC6_SHADOW_TRUTH_CARDS_ANCHOR_MISSING")
    old2 = '''        "<div class='paper-warning'><b>No es un “modo BINDING” del sistema.</b> "\n        f"El modo global observado es <b>{bg._e(truth['mode'])}</b>, ejecución <b>{bg._e(truth['execution'])}</b>, "\n        f"sesión <b>{bg._e(truth['session_state'])}</b>. El portón sólo decide si una señal simulada puede abrir "\n        "cuando no cubre comisión, derechos, spread, deslizamiento y reward/risk neto.</div>"'''
    new2 = '''        "<div class='paper-warning'><b>Etapa de aprendizaje SHADOW.</b> "\n        f"El modo global observado es <b>{bg._e(truth['mode'])}</b>, ejecución <b>{bg._e(truth['execution'])}</b>, "\n        f"sesión <b>{bg._e(truth['session_state'])}</b>. En SHADOW el portón calcula costos, spread, slippage y reward/risk, "\n        "pero NO veta PAPER: registra qué habría bloqueado y luego se contrasta contra el resultado realizado.</div>"'''
    if old2 in text:
        text = text.replace(old2, new2, 1)
    elif new2 not in text:
        raise SystemExit("RC6_SHADOW_TRUTH_NOTICE_ANCHOR_MISSING")
    path.write_text(text, encoding="utf-8")


def patch_checkpoint() -> None:
    path = Path("POROTA_TRADING_CHECKPOINT_CANONICO_RC6_2026-09-05.md")
    text = path.read_text(encoding="utf-8")
    wrong = "`BINDING` es la política del portón económico PAPER; el modo global continúa `PRODUCTION_PAPER` y la ejecución `SIMULATED`."
    corrected = "el rótulo/effective policy `BINDING` del portón económico era una desviación respecto del plan de aprendizaje: en RC6 el portón económico vuelve a `SHADOW`; el modo global continúa `PRODUCTION_PAPER` y la ejecución `SIMULATED`."
    if wrong in text:
        text = text.replace(wrong, corrected, 1)
    elif corrected not in text:
        raise SystemExit("RC6_SHADOW_CHECKPOINT_OLD_SENTENCE_MISSING")

    marker = "## 17. P0-APRENDIZAJE — SHADOW → BINDING → REAL-MONEY GOVERNANCE"
    if marker not in text:
        text = text.rstrip() + "\n\n" + marker + r'''

Decisión fundamental reafirmada por el operador el 2026-09-05 y obligatoria en toda continuidad futura:

**Durante las primeras ruedas PAPER se prefiere perder dinero ficticio y aprender antes que bloquear prematuramente oportunidades.** Los controles destinados a mejorar selección/rentabilidad se evalúan en SHADOW: calculan y registran su veredicto contrafactual, pero no tienen autoridad de veto sobre PAPER.

### Learning gates actuales

- Portón económico: `SHADOW` — NO bloquea PAPER.
- Expectancy: `SHADOW/OBSERVATION_ONLY` — NO bloquea PAPER.
- Régimen: `SHADOW/ALERT_ONLY` — NO bloquea PAPER.
- Concentración sectorial: `SHADOW/OBSERVATION_ONLY` — NO bloquea PAPER. La decisión histórica de hacerlo BINDING desde el inicio queda superada para esta campaña de aprendizaje.

### Hard safety — categoría separada

No son experimentos y no deben etiquetarse BINDING en UI. Mostrar como `SAFETY HARD BLOCK — NO APRENDIBLE / NO PROMOCIONABLE`:

- `REAL_ORDER_CAPABILITY=BLOCKED`;
- ejecución `SIMULATED`;
- `real_orders_sent=0`;
- rutas de órdenes reales bloqueadas;
- mercado/sesión cerrada => no nueva ejecución;
- libro/precio stale o ausente => no inventar fill;
- DB/config crítica inválida => fail-closed;
- derivados reales => bloqueados.

### Camino por política

`COLLECTING_EVIDENCE → SHADOW → SHADOW_VALIDATION → EVIDENCE_SUFFICIENT → ELIGIBLE_FOR_BINDING_DECISION → autorización explícita + release versionada → BINDING_PAPER → BINDING_VALIDATION → BINDING_PAPER_PROVEN → REAL_MONEY_GOVERNANCE → CANARY_REAL_MONEY`.

Ninguna métrica, porcentaje, dashboard, número de ruedas ni backtest promueve automáticamente una policy.

### Evidencia contrafactual mínima por operación PAPER

Para cada learning gate persistir:
- `would_allow / would_block`;
- motivo y valores/inputs observados;
- timestamp/provenance;
- resultado PAPER realizado cuando exista;
- would-block que habría evitado pérdida;
- would-block que habría eliminado ganancia (false positive);
- would-allow ganador/perdedor;
- PnL PAPER real vs PnL contrafactual si el gate hubiese sido BINDING;
- precision/false positives/false negatives;
- cohortes por familia/instrumento/régimen;
- desviaciones y lecciones.

La ventana de aproximadamente 20 ruedas / 4 semanas es un objetivo de observación, **no un gatillo temporal automático**. La calidad, independencia y suficiencia de la evidencia mandan.

### `/validacion`

Debe mostrar simultáneamente:
1. el camino global M0→M11 hacia producción real futura;
2. un carril SHADOW→BINDING independiente para cada learning gate;
3. métricas contrafactuales y lecciones;
4. estado/fecha/objetivo/evidencia/desviación/blocker/próximo paso;
5. real-money siempre `BLOCKED` hasta proyecto futuro explícito.

Esta política es P0 para RC6 y para la campaña del lunes.
'''
    path.write_text(text + "\n", encoding="utf-8")


def main() -> int:
    patch_mode_manager()
    patch_dashboard_default()
    patch_truth_layer()
    patch_checkpoint()
    print("RC6_SHADOW_POLICY_PATCH=GREEN")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
