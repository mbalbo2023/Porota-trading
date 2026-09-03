# POROTA TRADING — RC4 WIP — HALLAZGOS VERIFICADOS 2026-09-03

**Rama:** `release/v17.0.0-rc4-wip`  
**Estado:** NO DESPLEGABLE / preparación en paralelo.  
**Runtime observado:** `17.0.0-rc3-hf6-v2-candidate1`, `PRODUCTION_PAPER`, `SIMULATED`, `real_orders_sent=0`.

## 1. Legacy max-open-positions

Microdiagnóstico read-only del 03/09/2026:

- `LEGACY_MAX_POSITION_BLOCKS_SINCE_CANDIDATE_START=0`
- verdict: `NO_EVIDENCE_OF_RUNTIME_USE_AFTER_DEPLOY`
- desde el arranque de candidate1 sólo se observaron 5 evaluaciones de apertura, todas `Todos los portones aprobaron; compra simulada registrada`.

Conclusión RC4: el viejo límite máximo aparece en historia/código legado, pero no hay evidencia de que esté actuando como gate normal de candidate1. Mantener tests de regresión que impidan reintroducirlo.

## 2. Dynamic Concurrent Risk

Evidencia activa en imagen:

- `be_paper_engine.py` importa y usa `de_concurrent_risk_capacity_hf6` y `dh_paper_dynamic_risk_gate_hf6`.
- existe `PAPER_EMERGENCY_MAX_OPEN_POSITIONS` como cap técnico anti-runaway.
- la política normal calcula riesgo concurrente desde soft-stop diario, pérdidas realizadas consumidas, riesgo a stop de posiciones abiertas y riesgo del candidato.
- ganancias realizadas no amplían el presupuesto.

Libro abierto observado:

### ARS
- nominal abierto: `201082.7085`
- riesgo modelado a stop + entry costs: `5603.174170`
- pérdidas realizadas consumidas hoy: `0`
- `paper_daily_risk`: baseline `980190.1551`, daily PnL `-5038.2150`, estado `READY`.

### USD_MEP
- nominal abierto: `34.3068`
- riesgo modelado a stop + entry costs: `0.956136`
- pérdidas realizadas consumidas hoy: `0`
- `paper_daily_risk`: baseline `999.1196`, daily PnL `-0.5536`, estado `READY`.

Conclusión RC4: preservar el motor dinámico y agregar observabilidad explícita de budget total, consumed realized loss, open stop risk, candidate risk y remaining capacity por moneda.

## 3. Concentración sectorial

Para las 5 posiciones abiertas actuales (`AAPLD`, `BBAR`, `SUPV`, `GGAL`, `YPFD`) el catálogo no contiene mapping sectorial explícito utilizable:

- `EXPLICIT_SECTOR=MAP_UNAVAILABLE` para todas.
- `SECTOR_BINDING_READINESS=NOT_SAFE_TO_BIND_FULL_BOOK_WITHOUT_EXPLICIT_MAPPING`.

Conclusión RC4:

1. no inferir sectores por ticker/nombre;
2. incorporar mapping sectorial explícito, versionado y con fuente;
3. recién entonces activar concentración vinculante;
4. dashboard debe distinguir `MAP_UNAVAILABLE` de concentración baja.

## 4. Correlación

El microdiagnóstico sólo encontró como candidato de serie `market_snapshots(symbol, observed_at, last)`, pero no existe suficiente historia solapada para calcular correlación auditable entre las posiciones abiertas:

- `CORRELATION_RESULT=NO_TABLE_WITH_ENOUGH_OVERLAPPING_PRICE_HISTORY`.

Conclusión RC4:

- la correlación no puede transformarse hoy en gate vinculante;
- debe alimentarse desde History Store v2 / series históricas canónicas con identidad completa, retornos comparables y ventana definida;
- ante insuficiencia de muestra: `CORRELATION_UNAVAILABLE`, nunca asumir correlación cero.

## 5. `/vivo` y drill-down — regresión UX

Requisito RC4 confirmado:

- recuperar flecha/expansor por operación;
- P&L por operación visible en fila principal;
- positivo verde, negativo rojo y texto accesible además del color;
- timestamp exacto del último mark;
- freshness explícita; mark stale => AMARILLO/STALE y no presentar P&L como actual;
- drill-down: entrada, apertura, cantidad, moneda, settlement, mark, timestamp, P&L bruto/neto, costos, slippage, stop, target, señal, secuencia de gates, riesgo concurrente y exit intent.

El código candidate1 contiene capacidades parciales en `bg_paper_dashboard.py` (`paper_page()` ya usa `<details class='paper-trade'>`; `live_page()` ya calcula PnL y libro), pero la ruta/vista actual no entrega toda la experiencia requerida de forma consistente. RC4 debe unificarlo sin duplicar dos conceptos distintos de `/vivo` y `/en-vivo`.

## 6. Acceptance específico para estos cambios

Antes de considerar cerrado este bloque RC4:

- test: 6 posiciones posibles si el riesgo concurrente lo permite, sin bloqueo por `PAPER_MAX_OPEN_POSITIONS` normal;
- test: emergency cap sigue fail-closed;
- test: pérdidas realizadas reducen capacidad y ganancias no la expanden;
- test: sector mapping ausente no produce falso `LOW_CONCENTRATION`;
- test: correlación sin muestra produce `UNAVAILABLE`, no cero;
- test: `/vivo` muestra P&L, color+texto, timestamp del mark y stale state;
- test: drill-down accesible en tablet y conserva detalle técnico;
- `PRODUCTION_PAPER`, `SIMULATED`, `real_orders_sent=0` invariantes.

No se modifica el runtime durante rueda.
