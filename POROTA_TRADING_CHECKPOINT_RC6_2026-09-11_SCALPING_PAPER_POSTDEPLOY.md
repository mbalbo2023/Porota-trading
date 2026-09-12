# POROTA TRADING RC6 — CHECKPOINT SCALPING PAPER POSTDEPLOY

**Fecha:** 11 de septiembre de 2026  
**Objeto:** preservar el avance material posterior al checkpoint canónico de auditoría del 11/09/2026: activación/certificación del forward-fix de economía de scalping PAPER, RCA de ausencia de fills, auditoría EOD/overnight y criterio preliminar de tesorería/cauciones.

## 1. Jerarquía de verdad y nuevo baseline runtime

El baseline previo certificado era:

- Commit: `f8adec8a02b2f9f0ef2dffbee75458c958bf711e`
- Versión: `17.0.0-rc6`
- Modo: `PRODUCTION_PAPER`
- Ejecución: `SIMULATED`
- Órdenes reales: `BLOCKED`, `real_orders_sent=0`

A partir de la certificación postdeploy de este checkpoint, el **runtime autoritativo posterior** queda en:

- **Deploy SHA:** `eddcc29bc52eaf0b3d50f87dc851a48accb0fc8a`
- Parent exacto: `f8adec8a02b2f9f0ef2dffbee75458c958bf711e`
- Rama de source del forward-fix: `fix/rc6-scalping-paper-economics-20260911`
- Versión lógica preservada: `17.0.0-rc6`
- Modo: `PRODUCTION_PAPER`
- Ejecución: `SIMULATED`
- Capacidad de órdenes reales: `BLOCKED`
- `real_orders_sent=0`

No hacer rollback ciego a `f8adec8...`: `eddcc29...` lo reemplaza como baseline de runtime porque existe evidencia postdeploy posterior, inequívoca y GREEN.

## 2. Motivo del trabajo

El usuario pidió dos revisiones funcionales:

1. revisar si tiene sentido cerrar operaciones sólo por llegar al final de la rueda y evaluar caucionar caja sobrante antes del cierre;
2. hacer que el scalping PAPER funcione realmente, genere operaciones simuladas, datos y aprendizaje.

## 3. Estado real del scalping antes del forward-fix

La inspección read-only del runtime previo demostró que el scalper **ya estaba configurado en `ACTIVE_PAPER`**, no en `ACTIVE_OBSERVE`.

Parámetros observados/canónicos relevantes:

- `PAPER_SCALPING_MODE=ACTIVE_PAPER`
- scan: 180 segundos
- batch: 24
- riesgo por trade: `0.001` = 0,10%
- máximo de scalps abiertos: 1
- máximo hold scalping: 30 minutos
- margen neto mínimo: 0,5%
- spread máximo: 0,5%
- score mínimo: 0,68

Sin embargo, acumulaba decenas de miles de evaluaciones sin fills scalping ni muestras de aprendizaje etiquetadas porque no se producían candidatos ejecutables.

## 4. RCA de cero BUY_CANDIDATE

Workflow read-only de RCA:

- Run: `34659224740`
- Job: `103458054331`

Para el 11/09/2026:

- evaluaciones READY_PAPER_SPOT: **1419**
- `REJECTED_MUTABLE_CLOSED_POINTS`: **936**
- `PENDING_LIVE_CONFIRMATION`: **397**
- `INSUFFICIENT_INTRADAY_POINTS`: **47**
- `STALE_BOOK`: **33**
- `ECONOMICS_BINDING_RANGE_BELOW_COST`: **6**
- BUY candidates: **0**

Conclusión: el principal cuello de botella estaba antes del score, en el contrato de datos intradiarios PPI/fail-closed. No se relajó ese gate sin evidencia.

### 4.1 Gate temporal PPI

`fg_intraday_contract_policy_rc6.py` mantiene ventana mutable conservadora de 120 segundos. Una revisión posterior de un minuto ya considerado cerrado puede llevar a `REJECTED_MUTABLE_CLOSED_POINTS`; el estado se conserva durante la misma sesión para no usar evidencia temporal inconsistente.

Se intentó medir read-only la edad exacta de las revisiones PPI después del cierre, pero PPI devolvió errores de lectura en esa muestra fuera de rueda. Por lo tanto:

- NO se inventó una edad de revisión;
- NO se cambió arbitrariamente 120s a 300s u otro valor;
- el gate permanece fail-closed hasta obtener evidencia suficiente durante rueda.

## 5. Error independiente encontrado: economía de scalping

El scalper original modelaba el roundtrip como:

`2 * comisión completa + spread + slippage`

Esto sobreestimaba costos para operaciones intradiarias elegibles porque `au_fee_schedule.py` ya contempla la bonificación intradiaria PPI: compra y venta del mismo activo en la misma rueda/mercado/moneda/plazo pueden aplicar bonificación del arancel a la operación de menor valor, preservando los costos no bonificados según el tarifario.

### 5.1 Forward-fix

Se agregó:

- `fh_scalping_economics_rc6.py`

Y se modificó:

- `cf_intraday_scalping.py`

El nuevo modelo persiste provenance económico:

- `fee_model`
- `full_leg_fee_fraction`
- `discounted_leg_fee_fraction`
- `slippage_buffer_fraction`
- `modeled_roundtrip_fraction`
- `required_move_fraction`

Para familias elegibles usa:

`una punta completa + una punta bonificada + spread + buffer de slippage`

Para familias sin bonificación modelada conserva dos puntas completas.

## 6. Pruebas de fill PAPER y aprendizaje

Se agregó:

- `tests/test_rc6_scalping_active_paper_learning.py`
- `tests/test_rc6_scalping_intraday_fee_bonus.py`

La prueba de ciclo completo demuestra:

1. candidato validado;
2. `ACTIVE_PAPER`;
3. apertura exclusivamente `BUY_SIMULATED`;
4. posición marcada `execution_style=SCALPING_PAPER`;
5. creación de `paper_learning_samples` sin label al abrir;
6. cierre exclusivamente `SELL_SIMULATED`;
7. completado de `label_timestamp`, `net_return_pct`, `WIN/LOSS/FLAT` y duración;
8. `real_orders_sent=0` antes y después.

Prueba aislada inicial:

- Run `34659048415`
- Job `103457534885`
- Resultado: GREEN.

## 7. Candidato limpio

Rama limpia:

`fix/rc6-scalping-paper-economics-20260911`

Commit:

`eddcc29bc52eaf0b3d50f87dc851a48accb0fc8a`

Parent exacto:

`f8adec8a02b2f9f0ef2dffbee75458c958bf711e`

Delta exacto de source:

1. `cf_intraday_scalping.py`
2. `fh_scalping_economics_rc6.py`
3. `tests/test_rc6_scalping_active_paper_learning.py`
4. `tests/test_rc6_scalping_intraday_fee_bonus.py`

Validación limpia autoritativa:

- Run: `34659728040`
- Job: `103459526276`
- Resultado: **SUCCESS/GREEN**

Validó:

- identidad exacta candidato/parent;
- delta exacto de cuatro archivos;
- compilación;
- economía intradiaria;
- ciclo fill/aprendizaje;
- regresiones contractuales intradiarias;
- runtime safety;
- invariantes `PRODUCTION_PAPER`, `SIMULATED`, real order capability bloqueada;
- ausencia de `send_order` en el path del scalper.

## 8. Deploy y falso negativo del primer certificador

Deploy run:

- Run: `34659797946`
- Job: `103459733537`

El workflow finalizó `failure`, pero **NO por fallo de activación ni de runtime**.

Antes del punto final del workflow ya había demostrado:

- PRE safety `ok|PRODUCTION_PAPER|0`;
- source/delta correctos;
- build correcto;
- 18 pruebas offline GREEN;
- host checkout a `eddcc29...`;
- health GREEN;
- observer/dashboard running y restart 0;
- `POST_SCALPING_SAFETY=ok|PRODUCTION_PAPER|0|ACTIVE_PAPER|PPI_INTRADAY_BONUS`.

El único fallo fue que el certificador esperaba `SCALPING_WORKER_STATE=RUNNING` después de la rueda. El runtime devolvió correctamente:

`WAITING_MARKET`

porque el mercado ya estaba cerrado.

No se hizo rollback automático ni manual.

## 9. Certificación postdeploy autoritativa

Se ejecutó una certificación posterior read-only consciente del horario de mercado:

- Workflow: `RC6 Scalping PAPER Postdeploy Certify 2026-09-11`
- Run: **`34660018614`**
- Job: **`103460392927`**
- Resultado: **SUCCESS / GREEN**

Evidencia final:

```text
HOST_SHA=eddcc29bc52eaf0b3d50f87dc851a48accb0fc8a
OBSERVER=running|restarts=0|readonly=true
DASHBOARD=running|restarts=0
IMAGE_SHA=eddcc29bc52eaf0b3d50f87dc851a48accb0fc8a
HEALTH=status=ok|version=17.0.0-rc6
DB_QUICK_CHECK=ok
MODE=PRODUCTION_PAPER
REAL_ORDERS_SENT=0
PAPER_SCALPING_MODE=ACTIVE_PAPER
SCALPING_WORKER_STATE=WAITING_MARKET
MARKET_OPEN=FALSE
SCALPING_FEE_MODEL=PPI_INTRADAY_BONUS
TIMERS_ACTIVE=9/9
TIMERS_ENABLED=9/9
READONLY_MUTATION=NONE
REAL_ORDER_ROUTES=NOT_CALLED
RC6_SCALPING_PAPER_POSTDEPLOY=GREEN
```

Esta certificación reemplaza la interpretación superficial del `failure` del deploy workflow.

## 10. EOD / overnight — auditoría y decisión actual

Código actual:

- política PAPER regular BYMA: sesión aproximada 10:30–17:00;
- no nuevas entradas dentro de los últimos 30 minutos;
- cierre EOD del motor legacy cerca de cierre;
- supervisor adicionalmente usa stop, take-profit y `PAPER_MAX_HOLD_MINUTES`;
- generic max hold vigente: 360 minutos;
- el motor económico legacy fue diseñado asumiendo operatoria intradiaria/bonificación intradiaria.

Auditoría read-only de exits:

- Run `34659265646`
- Job `103458168398`

Histórico observado:

- `EOD_PAPER`: 24 cierres, P&L neto agregado `-4626.78`
- `STOP_PAPER`: 9, P&L `-10906.04`
- `DAILY_LOSS_PAPER`: 8, P&L `-11370.63`
- `MAX_HOLD_PAPER`: 6, P&L `-2662.93`

11/09/2026:

- 6 cierres totales;
- 3 STOP;
- 2 MAX_HOLD;
- 1 EOD.

El EOD del día fue PAMP:

- hold ~353 min;
- entry 5591.118;
- exit 5508.898;
- stop 5479.29564;
- target 5870.6739;
- P&L neto -1165.75.

El cierre EOD materializó una pérdida antes de llegar al stop. Esto demuestra que EOD puede cerrar una operación cuya barrera de stop aún no fue alcanzada; **NO demuestra que mantener overnight hubiera producido mejor resultado**.

### 10.1 Decisión actual

NO se cambió EOD/overnight en este deploy.

No se debe simplemente quitar EOD del motor legacy porque:

- su economía está modelada como intradiaria;
- generic max hold es intradiario;
- un stop no puede ejecutarse con mercado cerrado;
- una apertura del día siguiente puede gapear más allá del stop teórico;
- mantener overnight debe ser una decisión explícita de estrategia, no un efecto secundario de desactivar EOD.

Siguiente diseño recomendado: separar estrategias:

- `SCALPING_PAPER`: siempre intradía, flat al cierre;
- `INTRADAY_PAPER`: intradía, cierre por política EOD;
- `SWING_PAPER`: puede quedar overnight sólo si la tesis sigue válida y supera gates específicos de riesgo overnight/gap, calendario, liquidez y horizonte.

Antes de activar `SWING_PAPER`, generar primero evidencia shadow/counterfactual de qué habría ocurrido con los EOD históricos si se hubieran mantenido.

## 11. Cauciones / cash sweep — estado y criterio

`CAUCIONES_AUTO_PLACEMENT` permanece desactivado.

El módulo de tesorería PAPER existente ya es conservador y contempla:

- calendario/sesión certificada;
- próximo settlement operativo;
- freshness de oferta;
- una colocación por día;
- reserva de caja de 50%;
- participación limitada;
- fail-closed.

Pero no está habilitado como auto placement real/PAPER de fin de jornada sin completar wiring y prueba de horario/ofertas.

### 11.1 Criterio recomendado

No vender una posición válida sólo para caucionar.

El cash sweep debe usar únicamente **caja realmente libre**, una vez descontados:

- compromisos de liquidación;
- posiciones/órdenes pendientes;
- garantías/márgenes;
- reserva operativa;
- necesidades previstas del siguiente día.

Con el cuadro de horarios vigente de septiembre de 2026 y evidencia pública de cauciones operando hacia las 17:00, se recomienda diseñar inicialmente una ventana dinámica aproximadamente T−20/T−15 respecto del cierre aplicable, no un hardcode universal de T−10.

El horario exacto debe derivarse del calendario/sesión vigente y, antes de habilitar auto placement, comprobarse contra la operatoria PPI correspondiente. Si no hay certeza, dejar caja y fallar cerrado.

## 12. Semáforo actual

| Dominio | Estado | Nota |
|---|---|---|
| Runtime RC6 | GREEN | Host + imágenes `eddcc29...`, health OK, restarts 0 |
| Safety real orders | GREEN | PRODUCTION_PAPER, SIMULATED, real_orders_sent=0 |
| Scalping mode | GREEN | ACTIVE_PAPER desplegado |
| Scalping fee model | GREEN | PPI_INTRADAY_BONUS desplegado/certificado |
| Fill + learning lifecycle | GREEN en test | BUY_SIMULATED -> SELL_SIMULATED -> learning label probado |
| Fills live del scalper | PENDIENTE DE PRÓXIMA RUEDA | Mercado cerrado al cert; no fabricar fills |
| Intraday PPI mutable/revision gate | YELLOW | Principal cuello de botella observado; permanece fail-closed |
| EOD legacy intraday | YELLOW / EN REVISIÓN | Puede materializar pérdidas antes del stop; no se cambió aún |
| SWING/overnight | NO IMPLEMENTADO | Requiere estrategia explícita y prueba shadow |
| Caución automática | OFF | Diseño cash-sweep pendiente de wiring/horario PPI certificado |
| Timers relevantes | GREEN | 9/9 active, 9/9 enabled |

## 13. Próximas acciones

1. En la próxima rueda, observar `ACTIVE_PAPER` con el fee model corregido y medir BUY candidates/fills/learning samples reales simulados.
2. Instrumentar mejor provenance del gate `REJECTED_MUTABLE_CLOSED_POINTS` para medir durante rueda la edad y naturaleza real de revisiones PPI antes de cambiar 120s.
3. Construir `SWING_PAPER_SHADOW`/counterfactual con EOD históricos antes de permitir overnight real dentro del PAPER runtime.
4. Diseñar cash-sweep/cauciones con ventana dinámica de sesión, caja libre y reserva conservadora; auto placement sigue OFF hasta prueba completa.
5. Mantener `real_orders_sent=0` y no ampliar superficie de órdenes reales.

## 14. Qué NO volver a hacer

- NO rollback a `f8adec8...` por el falso negativo `RUNNING` vs `WAITING_MARKET`.
- NO interpretar `WAITING_MARKET` fuera de rueda como falla del scalper.
- NO relajar score/spread/contract-data gates sólo para forzar operaciones.
- NO cambiar la ventana PPI de 120s sin evidencia de revisiones durante rueda.
- NO desactivar EOD del motor legacy sin separar primero una estrategia SWING con costos y riesgo overnight correctos.
- NO vender posiciones válidas sólo para generar caja caucionable.
- NO activar cauciones automáticas hasta certificar horario, instrumento/plazo, costos y wiring PAPER.
- NO habilitar órdenes reales.

**Baseline runtime para la próxima continuidad:**

`POROTA TRADING RC6 — 17.0.0-rc6 — deploy eddcc29bc52eaf0b3d50f87dc851a48accb0fc8a — PRODUCTION_PAPER — SCALPING ACTIVE_PAPER — PPI_INTRADAY_BONUS — POSTDEPLOY GREEN — real_orders_sent=0`
