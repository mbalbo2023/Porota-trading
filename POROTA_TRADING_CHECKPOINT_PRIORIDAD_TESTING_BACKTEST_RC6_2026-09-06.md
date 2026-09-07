# POROTA TRADING — CHECKPOINT PRIORIDAD P1 TESTING / BACKTEST / FORWARD VALIDATION — 2026-09-06

## 1. Decisión

Se eleva formalmente **TESTING / BACKTEST / FORWARD VALIDATION** a **prioridad P1 estratégica** de POROTA TRADING RC6.

Esta prioridad queda vinculada al cierre de históricos multi-source, a History Store v2, al Forward Lab v2 y a la comparación sistemática:

`BACKTEST → EXECUTION REPLAY → PAPER FORWARD → SHADOW CONTRAFACTUAL → DECISIÓN HUMANA`

No se considera una nota secundaria ni un experimento opcional.

## 2. Relación con el lunes PAPER

Esta prioridad **NO reemplaza ni antecede a la seguridad operativa del lunes 2026-09-07**:

- preopen 10:15–10:30 AR continúa siendo obligatorio;
- GO PAPER sólo si el preopen es GREEN;
- durante rueda se mantiene change freeze salvo incidente crítico;
- PAPER es, a su vez, parte de la validación forward real;
- un backtest aislado no es requisito para observar PAPER el lunes;
- sí es requisito antes de justificar tuning/promoción estadística seria y, con mayor razón, antes de cualquier futura discusión de real-money.

**Real-money continúa NO-GO.**

## 3. Pipeline canónico de validación

La estrategia de testing/backtesting queda fijada así:

`HISTÓRICOS CANÓNICOS`
`→ DATA QUALITY GATE`
`→ ESTRATEGIA / CONFIGURACIÓN CONGELADA`
`→ REPLAY CRONOLÓGICO SIN LOOK-AHEAD`
`→ SIMULACIÓN REALISTA DE EJECUCIÓN`
`→ OUT-OF-SAMPLE / WALK-FORWARD TEMPORAL`
`→ ROBUSTEZ ESTADÍSTICA / FORWARD LAB V2`
`→ PAPER FORWARD`
`→ SHADOW CONTRAFACTUAL`
`→ REVISIÓN HUMANA / VERSIONADO`

## 4. Reglas duras

1. **No usar `q_backtest.py` como vía de validación.** Es legado y está explícitamente bloqueado como `LEGACY_BACKTEST_UNVERIFIED`.
2. No usar particiones aleatorias para series temporales.
3. No llamar out-of-sample a una partición hecha después de observar resultados.
4. No usar información futura, cierres posteriores, máximos/mínimos futuros ni revisiones de datos no disponibles al instante de decisión.
5. No relajar validaciones de históricos para aumentar artificialmente coverage.
6. No mezclar símbolo, familia, mercado, moneda, settlement o adjustment.
7. No mezclar distintas fuentes de book como si fueran liquidez independiente.
8. No asumir que una señal equivale a un fill.
9. No asumir que un stop/target se ejecuta exactamente al precio disparador.
10. No usar un único ejecutor para familias con microestructura/contratos diferentes.
11. No afirmar que OHLCV diario valida scalping intradiario.
12. No hacer mass tuning ni promoción automática por resultados de backtest.
13. No convertir un resultado favorable en autorización automática para BINDING.
14. Un backtest aislado **nunca** autoriza real-money.

## 5. Dependencia de históricos multi-source

Antes de una campaña seria de backtest, cada dataset deberá registrar, como mínimo:

- identidad financiera completa;
- símbolo;
- instrument_type / family;
- mercado;
- moneda;
- settlement;
- fecha/hora;
- RAW/ADJUSTED y basis de ajuste;
- fuente;
- provenance;
- versión/hash del dato;
- quality state;
- calendario/sesión aplicable.

La prioridad de testing queda por lo tanto ligada al cierre progresivo de:

- PPI historical ingestion/backfill;
- IOL_HISTORY_READONLY cuando sea validado;
- A3/BYMA con identity mapping determinístico;
- History Store v2 y source arbitration;
- coverage full-universe.

## 6. Componentes que ya existen

### `br_backtest_gate.py`
Controles de integridad de dataset, grilla de sesión, partición temporal purgada/embargo y drawdown sobre curva patrimonial marcada. No aprueba estrategias.

### `by_strategy_backtest.py`
Evaluación offline del candidato de velas con configuración congelada y conectada al ledger de ejecución. `promotion_allowed=False`.

### `bx_execution_replay.py`
Replay offline sobre books fechados, con identidad explícita, costos, bid/ask, profundidad, participación, slippage, tick, latencia, book age, fills, settlement y riesgo.

### `ec_forward_lab_metrics_hf2.py`
Primitivas ya existentes para expectancy, net return, MFE/MAE y métricas relacionadas.

### `q_backtest.py`
LEGADO / NO VALIDADO / RETIRADO como camino de validación.

## 7. P1 — Forward Lab v2 completo

Debe completarse como parte de esta prioridad:

- serie temporal correctamente definida por horizonte;
- HAC/panel correctamente especificado;
- block/stationary bootstrap por `trading_day_ar`;
- leave-one-symbol-out;
- leave-one-day-out;
- subperíodos;
- cohorts versionados;
- accounting de multiple testing;
- Deflated Sharpe Ratio cuando corresponda;
- control de solapamiento temporal;
- purga/embargo;
- modelo congelado antes de OOS;
- manifests reproducibles de cada corrida.

## 8. P1 — MFE / MAE y provenance

Completar medición ejecutable de:

- MFE;
- MAE;
- estado de medición (`excursion_state` o equivalente);
- provenance;
- bid/ask/mark utilizado;
- timestamps;
- calidad del dato;
- vínculo con operación/strategy version.

No inferir MFE/MAE a partir de información que el motor no hubiera tenido disponible.

## 9. Backtest diario vs scalping intradiario

Se mantienen dos tracks distintos:

### Track A — histórico diario / horizonte compatible
Usa históricos OHLCV canónicos para estrategias cuyo horizonte pueda validarse honestamente con ese nivel de granularidad.

### Track B — replay intradiario / scalping
Requiere archivo intradiario suficiente: candles, snapshots/books y timestamps con provenance. Si no existe profundidad histórica suficiente, se declara la limitación; no se inventa spread, secuencia intrabar ni liquidez como si fueran evidencia observada.

PPI/IOL/A3 diarios pueden enriquecer Track A, pero por sí solos no demuestran fidelidad para scalping.

## 10. Simulación de ejecución

Cada corrida relevante debe considerar explícitamente, según familia:

- bid/ask;
- spread;
- profundidad;
- participation cap;
- quantity step / lot;
- price tick;
- slippage;
- latencia;
- antigüedad máxima del book;
- TTL / expiración;
- fills parciales;
- costos y derechos;
- impuestos aplicables;
- settlement;
- receivables/caja;
- salida ejecutable;
- daily risk / latch;
- calendario y sesión.

Las familias especializadas deben tener ejecutores específicos cuando corresponda: contado, bonos, cauciones, opciones, futuros, FCI u otras familias no deben forzarse artificialmente al mismo modelo.

## 11. Métricas mínimas de campaña

Medir como mínimo:

- PnL neto después de costos;
- retorno neto;
- expectancy;
- payoff medio;
- win/loss distribution;
- drawdown máximo sobre equity marcada;
- MFE;
- MAE;
- duración de operaciones;
- turnover;
- costo/PnL;
- fill ratio;
- fills parciales;
- slippage observado/modelado;
- stop / target / timeout / daily-loss exits;
- operaciones rechazadas y razón;
- resultados por familia;
- por símbolo;
- por settlement;
- por `trading_day_ar`;
- por régimen/cohort/subperíodo;
- por strategy version;
- divergencia Backtest ↔ Replay ↔ PAPER.

## 12. Comparación Backtest ↔ Replay ↔ PAPER

La campaña debe responder no sólo si una estrategia gana históricamente, sino si la ventaja sobrevive cuando aumenta el realismo:

1. **Backtest:** señal/estrategia congelada sobre datos canónicos.
2. **Execution Replay:** misma estrategia con ejecución/costos/liquidez modelados de forma reproducible.
3. **PAPER Forward:** misma versión enfrentando jornadas futuras que no pudo conocer.
4. **SHADOW:** comparación contrafactual de gates observacionales sin modificar las decisiones PAPER ejecutadas.

Una divergencia material entre estas capas debe investigarse antes de tocar parámetros.

## 13. Política de tuning / aprendizaje

- Intraday AI continúa OFF.
- Deterministic Python decision engine continúa como motor.
- SHADOW/observation-first continúa vigente para los gates de aprendizaje actuales.
- No hay auto-promotion.
- No hay autotuning automático por resultados recientes.
- No cambiar parámetros después de observar OOS/PAPER y volver a etiquetar ese mismo período como OOS.
- Toda propuesta de cambio requiere evidencia, versión, tests y nueva cohorte de validación.

## 14. Criterio de prioridad

**Estado: P1 OPEN — PRIORIDAD FORMAL.**

No bloquea la observación PAPER del lunes si el preopen es GREEN.
Sí bloquea cualquier afirmación fuerte de robustez estadística, cualquier promoción seria de parámetros/gates basada en performance y cualquier futura justificación de dinero real mientras el paquete de evidencia no esté completo.

## 15. Impacto runtime de este checkpoint

Este checkpoint es exclusivamente documental/de gobernanza.

- no modifica observer;
- no modifica dashboard;
- no modifica estrategia live;
- no modifica thresholds;
- no modifica históricos;
- no modifica timers;
- no envía órdenes;
- no habilita real-money.

Observer frozen de referencia: `db26c76723bb988c956589c572b87cbcb4191731`.

`real_orders_sent=0` continúa siendo invariante absoluto.
