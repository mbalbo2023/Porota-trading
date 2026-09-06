#!/usr/bin/env python3
from pathlib import Path

PATH = Path("POROTA_TRADING_CHECKPOINT_CANONICO_RC6_2026-09-05.md")
MARKER = "## 16. P0-DASHBOARD — CONSISTENCIA TOTAL DE DATOS Y SEMÁNTICA"
SECTION = r'''

## 16. P0-DASHBOARD — CONSISTENCIA TOTAL DE DATOS Y SEMÁNTICA

Prioridad cero agregada por el operador el 2026-09-05. Aplica a **todo** el dashboard, no sólo a Motor de trading.

### Hallazgos de campo

1. En `/motor-trading` se mostraba `Economía matemática BINDING` de forma que podía leerse como si BINDING fuera el modo global. Eso es semánticamente incorrecto: `BINDING` es la política del portón económico PAPER; el modo global continúa `PRODUCTION_PAPER` y la ejecución `SIMULATED`.
2. En mercado cerrado el panel mostraba `Reloj independiente: RUNNING`. El runtime puede mantener vivo el watchdog para conservar heartbeat/fail-closed, mientras el lector ya informa `WAITING_MARKET`; presentar RUNNING como estado operativo de mercado es engañoso. La UI debe distinguir liveness interna de actividad de mercado.
3. El mismo principio debe aplicarse a scanner, workers y cualquier componente market-sensitive: proceso vivo != mercado abierto != capacidad de ejecutar.
4. Las tablas deformadas observadas en Samsung/Voice Access siguen P0: cero scroll horizontal de página, representación legible y controles textuales.

### Criterio de verdad único en toda página

Toda vista canónica debe mostrar desde `observer_state`:
- modo;
- ejecución;
- process state;
- session state;
- PPI auth;
- posiciones abiertas;
- `real_orders_sent`;
- timestamp/heartbeat de la evidencia.

La UI nunca debe inferir “operando” sólo porque un proceso está `RUNNING`.

### Semántica de mercado cerrado

- worker sano + mercado abierto => mostrar su estado real (`RUNNING`, `READY`, etc.);
- worker sano + mercado cerrado + 0 posiciones => `EN_ESPERA_MERCADO_CERRADO`;
- worker sano + mercado cerrado + posiciones abiertas => `MONITOREO_PASIVO`; no ejecución;
- `ERROR/FAILED/DEGRADED/STALE/UNKNOWN` nunca se ocultan ni se degradan cosméticamente por estar fuera de rueda.

### Implementación RC6 iniciada

- `ep_dashboard_truth_layer_rc6.py`: capa read-only de verdad runtime; separa modo/sesión/policy/liveness, corrige semántica de supervisor/scanner y expone `/api/dashboard/truth` autenticado.
- `tests/test_dashboard_truth_layer_rc6.py`: closed-market semantics, error preservation y BINDING como policy, no modo.
- `scripts/rc6_dashboard_http_consistency_check.py`: auditor HTTP read-only de todas las rutas canónicas; verifica navegación, banner de verdad, wording cerrado, ausencia de etiquetas legacy engañosas y `real_orders_sent=0`.
- wiring RC6 por workflow dedicado, sin modificar RC5 desplegada.

### Gate antes de deploy RC6

- cada ruta principal HTTP 200;
- exactamente una navegación canónica;
- exactamente un banner de verdad runtime;
- navegación completa y sin destinos rotos;
- no `Economía matemática BINDING` como rótulo de modo;
- cerrado: supervisor/scanner no presentados como actividad de mercado;
- toda discrepancia de mode/session/orders => RED;
- `real_orders_sent=0`;
- field test Samsung/Voice Access;
- tablas P0 corregidas y verificadas en dispositivo.

Esta prioridad se integra a P0-5 Sunday Readiness y al tablero `/validacion`.
'''


def main() -> int:
    text = PATH.read_text(encoding="utf-8")
    if MARKER in text:
        print("RC6_CHECKPOINT_P0_DASHBOARD=ALREADY_PRESENT")
        return 0
    PATH.write_text(text.rstrip() + SECTION + "\n", encoding="utf-8")
    print("RC6_CHECKPOINT_P0_DASHBOARD=APPENDED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
