# POROTA TRADING RC6 — CHECKPOINT PARALELO 2026-09-11

## 1. OBJETIVO

Conservar un punto de reanudación inequívoco después del avance paralelo sobre tres frentes independientes de RC6:

1. telemetría de revisiones intradiarias para el scalper PAPER;
2. política `SWING_PAPER` de overnight en modo `SHADOW_ONLY`;
3. política de caución/cash-sweep de fin de rueda en modo `SHADOW_ONLY`.

Este checkpoint **NO cambia el runtime** y **NO implica deploy** de ninguna de las tres ramas funcionales. Su finalidad es registrar exactamente qué quedó probado, qué sigue desplegado y qué evidencia falta antes de promover cualquier cambio.

---

## 2. VERDAD DE RUNTIME / BASELINE OPERATIVO

### Runtime desplegado vigente

- Repositorio: `mbalbo2023/Porota-trading`
- Versión lógica: `17.0.0-rc6`
- SHA desplegado/certificado vigente: `eddcc29bc52eaf0b3d50f87dc851a48accb0fc8a`
- Modo certificado: `PRODUCTION_PAPER`
- Ejecución: simulada
- Órdenes reales: bloqueadas por diseño
- Última certificación postdeploy que sustenta este baseline:
  - workflow run: `34660018614`
  - job: `103460392927`
  - resultado: `SUCCESS`
- Evidencia de esa certificación: observer/dashboard activos sin restart, health `ok`, DB quick check `ok`, `REAL_ORDERS_SENT=0`, `PAPER_SCALPING_MODE=ACTIVE_PAPER`, fee model `PPI_INTRADAY_BONUS`, timers productivos 9/9 activos+enabled, sin rutas reales de orden llamadas.

### Regla de interpretación

La evidencia de seguridad anterior corresponde al último postdeploy certificado de `eddcc29...`. Los desarrollos registrados abajo están en ramas aisladas y **no deben describirse como DEPLOYED ni ACTIVE** hasta que exista un deploy y un postdeploy nuevos que lo prueben.

### Estado global

`RUNTIME = GREEN / SIN CAMBIOS`

---

## 3. FRENTE A — TELEMETRÍA DE REVISIONES DEL SCALPER

### Rama y SHA certificado

- Rama: `feature/rc6-scalping-revision-telemetry-20260911`
- HEAD certificado: `3793dbb9cab1eb2b25bcd8e96a4e3a5df336970b`
- Base exacta de trabajo: `eddcc29bc52eaf0b3d50f87dc851a48accb0fc8a`

### Objetivo

Medir durante rueda real la edad de las revisiones que PPI realiza sobre puntos de `MarketData/Intraday`, sin relajar el contrato temporal ni forzar candidatos de scalping. El problema que se busca explicar sigue siendo la alta incidencia histórica de `REJECTED_MUTABLE_CLOSED_POINTS`.

### Cambio implementado

Archivo modificado:

- `cf_intraday_scalping.py`

Archivo de pruebas agregado:

- `tests/test_rc6_scalping_revision_telemetry.py`

Workflow agregado:

- `.github/workflows/rc6-scalping-revision-telemetry-20260911.yml`

La instrumentación registra exclusivamente puntos que realmente cambiaron y conserva:

- identidad del instrumento;
- `event_at`;
- `received_at`;
- `age_seconds`;
- acción de contrato (`REFRESH_MUTABLE` o `REJECT_CLOSED_REVISION`);
- si cambió precio y/o volumen;
- `mutable_seconds` vigente;
- fuente `PPI_MARKETDATA_INTRADAY`.

La telemetría se emite después de la transacción de estado de contrato y es `best-effort`: una falla al registrar la observación no puede alterar el veredicto del contrato ni habilitar una operación.

### Contrato que NO se cambió

- `DEFAULT_MUTABLE_SECONDS = 120`
- no se relajó score;
- no se relajó spread;
- no se cambió el requisito de continuidad/solapamiento;
- una revisión genuina de un punto cerrado continúa en fail-closed;
- el reseteo por sesión sigue vigente;
- no se agregó ninguna ruta de orden.

### Pruebas funcionales nuevas

Se prueba explícitamente:

- revisión mutable a 90 s -> `REFRESH_MUTABLE`, no rechazo;
- revisión cerrada a 660 s -> `REJECT_CLOSED_REVISION` y `REJECTED_MUTABLE_CLOSED_POINTS`;
- falla deliberada del almacenamiento de telemetría -> el contrato conserva el rechazo fail-closed.

### Incidente del primer certifier y RCA

Primer run:

- run `34662371221`
- job `103467342543`
- resultado global: `FAILURE`

RCA:

- delta exacto: PASS;
- compilación: PASS;
- bloque contract policy + telemetría: **12 tests PASS**;
- la falla posterior fue exclusivamente del certifier porque el workflow había instalado `pytest` pero no `requests`, requerido por una regresión histórica importada por `bd_ppi_readonly_guard.py`;
- error: `ModuleNotFoundError: No module named 'requests'`;
- no era un error del producto ni de la telemetría.

Forward fix aplicado:

- se agregó `requests` únicamente a las dependencias del certifier;
- no se cambió código del scalper para resolver ese incidente.

### Certificación final

- run: `34662443014`
- job: `103467550521`
- HEAD: `3793dbb9cab1eb2b25bcd8e96a4e3a5df336970b`
- resultado: `SUCCESS`

Pasos GREEN:

- exact delta from deployed baseline;
- compile touched Python;
- contract policy and telemetry;
- scalping PAPER regression;
- static safety assertions.

### Clasificación

- PRESENT IN SOURCE: YES, en rama funcional
- TESTED: YES
- CERTIFIED CI: YES
- DEPLOYED: NO
- ACTIVE RUNTIME: NO
- NECESITA RUEDA ABIERTA PARA EVIDENCIA REAL PPI: YES

`STATUS = GREEN EN CI / YELLOW OPERATIVO HASTA MEDIR EN RUEDA REAL`

---

## 4. FRENTE B — SWING_PAPER OVERNIGHT SHADOW

### Rama y SHA certificado

- Rama: `feature/rc6-swing-paper-shadow-20260911`
- HEAD certificado: `15fa82bb05ee74f358f3f5b9a6072b23babd9228`
- Base exacta: `eddcc29bc52eaf0b3d50f87dc851a48accb0fc8a`

### Objetivo

Separar conceptualmente la lógica intradía de una estrategia que pueda conservar una posición overnight. La política actual de EOD del runtime **NO fue modificada**. Este frente únicamente calcula qué hubiera decidido una estrategia explícitamente clasificada como `SWING_PAPER`.

### Archivos nuevos

- `fi_swing_paper_shadow_rc6.py`
- `tests/test_rc6_swing_paper_shadow.py`
- `.github/workflows/rc6-swing-paper-shadow-20260911.yml`

### Reglas implementadas en shadow

- `mode = SHADOW_ONLY`;
- solo una posición con `features_json.execution_style = SWING_PAPER` puede evaluarse como swing;
- una posición legacy o sin clasificación explícita nunca se reinterpreta automáticamente como swing;
- `SCALPING_PAPER` e `INTRADAY_PAPER` continúan siendo candidatos a `FORCE_FLAT` al EOD;
- calendario de próxima sesión no verificado -> `FAIL_CLOSED`;
- mark EOD stale -> `FAIL_CLOSED`;
- tesis swing inválida -> `FORCE_FLAT`;
- máximo de sesiones alcanzado -> `FORCE_FLAT`;
- stop ya alcanzado -> `FORCE_FLAT`;
- target ya alcanzado -> `FORCE_FLAT`;
- gap risk superior al límite -> `FORCE_FLAT`;
- CEDEAR requiere además calendario del subyacente verificado; subyacente cerrado en próxima sesión -> no carry automático;
- solo si toda la evidencia explícita pasa -> `CARRY_OVERNIGHT` en shadow.

### Separación de economía

El veredicto shadow etiqueta el modelo como `SWING_NON_INTRADAY`; no presupone la bonificación intradía de costos del scalper.

### Seguridad / aislamiento

El módulo puro no importa broker PAPER, SQLite, PPI, HTTP ni supervisor de salidas. No modifica:

- `bm_exit_supervisor.py`
- `bq_exit_policy.py`
- `be_paper_engine.py`

No dispone de capacidad de ejecución real ni PAPER binding. `eod_exit_binding=False` y `real_execution_allowed=False`.

### Certificación

- run: `34662453597`
- job: `103467581947`
- HEAD: `15fa82bb05ee74f358f3f5b9a6072b23babd9228`
- resultado: `SUCCESS`

Pasos GREEN:

- exact delta from deployed baseline;
- compile pure shadow policy;
- swing shadow tests;
- existing exit supervisor regression;
- prove supervisor untouched and shadow cannot execute.

### Clasificación

- PRESENT IN SOURCE: YES, en rama funcional
- TESTED: YES
- CERTIFIED CI: YES
- DEPLOYED: NO
- ACTIVE: NO
- BINDING: NO
- SHADOW RUNTIME WIRED: NO todavía

`STATUS = GREEN EN CI / YELLOW HASTA REPLAY + WIRING SHADOW`

---

## 5. FRENTE C — CAUCIÓN CASH-SWEEP SHADOW

### Rama y SHA certificado

- Rama: `feature/rc6-caucion-cash-sweep-shadow-20260911`
- HEAD certificado: `eaf10e1214e43cc53274fb2a7ccfaf47be43fcb3`
- Base exacta: `eddcc29bc52eaf0b3d50f87dc851a48accb0fc8a`

### Hallazgo arquitectónico previo

RC6 ya contenía una base conservadora que no convenía duplicar:

- `df_caucion_end_of_day_sweep_hf6.py`: planner puro;
- `di_caucion_cash_sweep_runtime_hf6.py`: runtime PAPER con guardas;
- `CASH_SWEEP_ORDER_ROUTING_ALLOWED=False` en la arquitectura existente;
- reserva/compromisos, schedule verificado, quote fresca, retorno neto y guard de un sweep por día ya estaban contemplados en el diseño existente.

Por lo tanto el nuevo trabajo se limitó a una capa de política de ventana/proveniencia shadow sobre el planner existente.

### Archivos nuevos

- `fj_caucion_cash_sweep_shadow_rc6.py`
- `tests/test_rc6_caucion_cash_sweep_shadow.py`
- `.github/workflows/rc6-caucion-cash-sweep-shadow-20260911.yml`

### Reglas implementadas

- modo `SHADOW_ONLY`;
- no recibe ni liquida posiciones;
- `real_execution_allowed=False`;
- el inicio de la ventana se deriva de `order_cutoff_at - policy_lead_minutes`;
- lead por defecto: 20 minutos;
- rango conservador permitido de policy lead: 10 a 45 minutos;
- el lead es política interna, **no se presenta como horario oficial de BYMA**;
- el cutoff real y su `schedule_source` deben venir de evidencia verificada;
- falta de provenance/schedule -> `FAIL_CLOSED`;
- calendario de liquidación no verificado -> `FAIL_CLOSED`;
- orden temporal inválido entre sweep start/cutoff/liquidity deadline -> `FAIL_CLOSED`;
- si ya hubo sweep del día -> `NO_ACTION`;
- únicamente ofertas con presupuesto de fees exacto pueden pasar al planner automático/shadow;
- fee curve desconocida -> `HOLD / EXACT_FEE_BUDGET_MISSING`;
- un `PAPER_CANDIDATE` del planner se transforma exclusivamente en `SHADOW_CANDIDATE`.

### Seguridad / aislamiento

No modifica:

- `di_caucion_cash_sweep_runtime_hf6.py`
- `ca_caucion_allocator.py`
- `bt_caucion_paper.py`

No importa ni llama `allocate_caucion`, `run_paper_sweep`, `send_order`, `place_order`, ni depende de `paper_positions`.

### Certificación

- run: `34662524864`
- job: `103467789585`
- HEAD: `eaf10e1214e43cc53274fb2a7ccfaf47be43fcb3`
- resultado: `SUCCESS`

Pasos GREEN:

- exact delta from deployed baseline;
- compile shadow layer;
- cash sweep shadow tests;
- existing caucion sweep regressions;
- prove execution runtime remains untouched.

### Clasificación

- PRESENT IN SOURCE: YES, en rama funcional
- TESTED: YES
- CERTIFIED CI: YES
- DEPLOYED: NO
- ACTIVE: NO
- BINDING: NO
- REAL CAUCION ROUTING: NO

`STATUS = GREEN EN CI / YELLOW HASTA WIRING SHADOW + SCHEDULE LIVE CERTIFICADO`

---

## 6. EOD / OVERNIGHT — DECISIÓN QUE SE MANTIENE

No se cambió la lógica productiva de cierre EOD en este avance.

La evidencia histórica ya disponible había mostrado que algunos cierres EOD cristalizan pérdidas antes de que se alcance un stop, pero eso no demuestra que mantener overnight tenga mayor expectativa. Además el stop no puede ejecutarse con mercado cerrado y un gap de apertura puede superar el stop teórico.

Por eso el enfoque correcto sigue siendo:

- intraday/scalping -> flat EOD;
- swing -> solo si fue clasificado explícitamente como swing y pasa una política de overnight;
- no convertir todas las posiciones existentes en swing por omisión;
- no eliminar EOD antes de replay/counterfactual y wiring shadow;
- no usar el `PAPER_MAX_HOLD_MINUTES=360` intradía como sustituto de un horizonte de sesiones/días para swing.

---

## 7. CAUCIÓN — DECISIÓN QUE SE MANTIENE

No se venderá una posición con tesis válida solo para generar caja a caucionar.

La secuencia objetivo sigue siendo:

1. clasificar estrategia y riesgo;
2. permitir siempre salidas que reduzcan riesgo;
3. reconciliar caja, obligaciones y reservas;
4. determinar caja realmente libre y liquidada;
5. validar calendario y schedule de caución;
6. evaluar retorno neto después de costos;
7. en esta etapa, registrar únicamente decisión shadow.

`CAUCIONES_AUTO_PLACEMENT` no debe promoverse a operación autónoma por este checkpoint.

---

## 8. DASHBOARD / OBSERVABILIDAD — SIGUIENTE FRENTE TRANSVERSAL

No se realizó todavía wiring de estos tres nuevos frentes al dashboard del runtime.

Diseño previsto:

### Scalping telemetry

Consumir eventos `SCALPING_INTRADAY_REVISION_TELEMETRY` y mostrar al menos:

- revisiones mutables vs cerradas;
- edad de revisión;
- umbral vigente 120 s;
- p50/p95/max cuando haya muestra suficiente;
- instrumento/familia;
- última evidencia y freshness;
- fuente.

### SWING_PAPER

Antes de integrarlo al supervisor, registrar snapshots shadow/replay con:

- decisión hypothetical carry/flat;
- razón;
- calendario;
- sesiones en posición;
- mark/freshness;
- gap-risk evidence;
- CEDEAR underlying calendar cuando corresponda.

### Caución

Mostrar:

- modo `SHADOW_ONLY`;
- cutoff verificado y fuente;
- policy lead;
- sweep start calculado;
- caja disponible/reserva/budget;
- estado/reason;
- instrumento candidato y retorno neto solo cuando exista evidencia exacta.

El dashboard será consumidor de evidencia, nunca motor de decisión.

---

## 9. SEMÁFORO DEL CHECKPOINT

| Dominio | Estado | Evidencia / razón |
|---|---|---|
| Runtime RC6 desplegado | GREEN | `eddcc29...`, postdeploy run `34660018614` |
| Seguridad órdenes reales | GREEN según último postdeploy | `REAL_ORDERS_SENT=0`, rutas reales no llamadas en cert vigente |
| Scalping ACTIVE_PAPER existente | GREEN runtime / YELLOW fills | activo en baseline; bottleneck temporal aún necesita evidencia de rueda |
| Telemetría revisión scalper | GREEN CI / NO DEPLOY | run `34662443014`, job `103467550521` |
| SWING_PAPER policy | GREEN CI / SHADOW NO WIRED | run `34662453597`, job `103467581947` |
| Caución cash-sweep policy | GREEN CI / SHADOW NO WIRED | run `34662524864`, job `103467789585` |
| EOD productivo | SIN CAMBIO | no se promueve swing sin evidencia adicional |
| Dashboard nuevos frentes | YELLOW | pendiente wiring de observabilidad |
| PPI revision latency real | YELLOW | requiere próxima rueda abierta |
| Caución schedule live/provenance | YELLOW | debe certificarse en contexto operativo antes de binding |

---

## 10. PRÓXIMOS PASOS CANÓNICOS

1. Integrar observabilidad/dashboard en una rama de integración sin capacidad de decisión ni órdenes.
2. Evaluar deploy **solo de la telemetría no-binding** después de preparar certifier/deploy/postdeploy, porque para aprender la latencia real de PPI debe ejecutarse durante rueda abierta.
3. Mantener `SWING_PAPER` sin binding y ejecutar replay/counterfactual sobre cierres EOD históricos antes de tocar `bm_exit_supervisor.py`.
4. Crear wiring shadow de swing que registre snapshots, no ejecuciones.
5. Mantener caución shadow y completar fuente live certificada de schedule/calendario/fee evidence antes de considerar cualquier PAPER binding.
6. No modificar el umbral de 120 s del contrato intradiario por intuición; cambiarlo únicamente con distribución observada de edades de revisión durante rueda real.
7. No desplegar las tres ramas a la vez sin una rama de integración y regresión conjunta; preservar capacidad de atribuir cualquier incidente a un delta mínimo.

---

## 11. QUÉ NO REPETIR / QUÉ NO HACER

- No reabrir el fix de economics del scalper ya certificado en `eddcc29...` salvo evidencia nueva.
- No confundir el primer FAILURE de telemetría con un bug de producto: fue dependencia faltante del certifier y la suite nueva ya había pasado 12/12.
- No relajar score/spread/mutable window para forzar fills.
- No desactivar EOD globalmente.
- No reinterpretar posiciones legacy como swing.
- No vender posiciones válidas para caucionar caja.
- No usar un horario hardcodeado sin provenance como cutoff oficial de caución.
- No activar caución real ni rutas `/Operar`.
- No considerar una rama TESTED como DEPLOYED.
- No hacer rollback del runtime GREEN por cambios que todavía viven fuera del runtime.

---

## 12. REANUDACIÓN EXACTA

Al continuar desde este checkpoint:

1. tomar `eddcc29bc52eaf0b3d50f87dc851a48accb0fc8a` como runtime desplegado hasta que un postdeploy posterior demuestre lo contrario;
2. tomar como ramas funcionales certificadas exactamente:
   - scalping telemetry `3793dbb9cab1eb2b25bcd8e96a4e3a5df336970b`;
   - swing shadow `15fa82bb05ee74f358f3f5b9a6072b23babd9228`;
   - caucion shadow `eaf10e1214e43cc53274fb2a7ccfaf47be43fcb3`;
3. continuar con observabilidad/dashboard y replay/wiring shadow sin tocar ejecución real;
4. si aparece un fallo, aplicar `RCA -> FORWARD FIX -> REVALIDATE -> CONTINUE`;
5. preservar `PRODUCTION_PAPER`, ejecución simulada y superficie real de órdenes bloqueada.

---

**Checkpoint local Argentina:** 2026-09-11. Las ejecuciones de GitHub Actions posteriores a 21:00 ART aparecen fechadas 2026-09-12 en UTC.
