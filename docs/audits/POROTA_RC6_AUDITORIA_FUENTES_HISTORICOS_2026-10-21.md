# POROTA RC6 — Auditoría forense READ_ONLY de precedencia de fuentes e históricos

**Fecha de referencia:** 21 de octubre de 2026 (America/Argentina/Buenos_Aires).  
**Repositorio:** `mbalbo2023/Porota-trading`.  
**SHA productivo revalidado:** [`328a6526a3eaf3851ef93778c1d0345593afc3ee`](https://github.com/mbalbo2023/Porota-trading/commit/328a6526a3eaf3851ef93778c1d0345593afc3ee).  
**Modo:** `READ_ONLY`, exclusivamente inspección de archivos GitHub. No se ejecutaron workflows, tests, SQL, comandos remotos ni modificaciones en GitHub/Droplet/PPI Watch.  
**Estados:** `VALIDADO` = comprobado en código al SHA; `NO_VERIFICADO` = falta DB/log/runtime; `BLOQUEADO` = necesita autorización/ventana segura para evidencia operativa.

## 1. Dictamen ejecutivo

**VALIDADO: hay una contradicción de precedencia en el código.** El contrato para datos de mercado de RC6 establece **PPI → IOL → BYMA**, pero `cu_history_store_v2_hf6.py`, que decide la fila **canónica histórica de SQLite**, otorga prioridad **PPI → BYMA/A3 → IOL**. El reconciliador histórico offline copia ese orden incorrecto, mientras el planificador de backfill mantiene el orden pactado.

**VALIDADO: hay además una contradicción de criterio de completitud.** El reconciliador offline elige primero la calidad de la vela (OHLCV completo > OHLC completo > sólo cierre), pero el almacén real elige primero el ranking de proveedor **sin valorar la completitud**. Por tanto, bajo identidades y fechas coincidentes, una fila BYMA/A3 con sólo cierre puede desplazar a una fila IOL con OHLC completo en el almacén; el test del reconciliador valida el resultado opuesto.

**VALIDADO: las métricas de dashboard pueden declarar OHLC completo sin comprobarlo.** `dd_history_metrics_hf6.py` etiqueta todas las filas del almacén v2 como `full_ohlc_canonical_rows`, pese a que la tabla admite `open/high/low = NULL`. Las métricas también omiten `currency` en identidades que describen como exactas. Esto no significa que el almacén SQLite confunda monedas —el almacén v2 sí las separa—, sino que la **capa de reporte** podría agregarlas incorrectamente.

**NO_VERIFICADO: impacto ya ocurrido sobre la base real, dashboards observados, señales o backtests.** Para confirmarlo hacen falta snapshots/logs/SQLite y trazabilidad de consumidores. Que exista una rama de escritura A3 no prueba que su job esté ejecutándose en el Droplet actual, ni que haya coexistido con IOL en una misma serie.

## 2. Matriz comparada del código actual

| Componente | Orden demostrado | Calidad / identidad | Efecto | Dictamen |
|---|---|---|---|---|
| [`rc6_shadow_runtime/source_authority.py`, L26–41 y L69–162](https://github.com/mbalbo2023/Porota-trading/blob/328a6526a3eaf3851ef93778c1d0345593afc3ee/rc6_shadow_runtime/source_authority.py#L26-L41) | PPI (0), IOL (1), BYMA (2) | Rechaza evidencias vencidas, sin reloj nativo, y marca discrepancias | Campos comparados PAPER/SHADOW; `entry_authority=False` | **Correcto** |
| [`rc6_source_consolidation.py`, L89–101 y L220–249](https://github.com/mbalbo2023/Porota-trading/blob/328a6526a3eaf3851ef93778c1d0345593afc3ee/rc6_source_consolidation.py#L89-L101) | PPI → IOL → BYMA | Identidad monetaria exacta de cinco campos; distingue campo ilustrativo vs validado | Consolida/compara; no autoriza dinero real | **Correcto** |
| [`rc6_ppi_iol_reconciliation_rc6.py`](https://github.com/mbalbo2023/Porota-trading/blob/328a6526a3eaf3851ef93778c1d0345593afc3ee/rc6_ppi_iol_reconciliation_rc6.py) | PPI primario; IOL complemento; BYMA respaldo | Con discrepancias, bloqueo de selección; requiere identidad PPI | Readiness PAPER/SHADOW | **Sin inversión vista** |
| [`fj_history_backfill_planner_rc6.py`, L13 y L44–73](https://github.com/mbalbo2023/Porota-trading/blob/328a6526a3eaf3851ef93778c1d0345593afc3ee/fj_history_backfill_planner_rc6.py#L13-L73) | PPI → IOL → A3/BYMA → DATA912 | Planifica faltantes; `canonical_write=DENY` | Offline / shadow; no escritor canónico | **Orden correcto, no conectado como writer** |
| [`cu_history_store_v2_hf6.py`, L23–28, L276–337 y L394–418](https://github.com/mbalbo2023/Porota-trading/blob/328a6526a3eaf3851ef93778c1d0345593afc3ee/cu_history_store_v2_hf6.py#L23-L28) | PPI (10) → BYMA/A3 (20) → IOL (30) → DATA912 (50) → Yahoo (90) | Separación exacta de moneda/base, pero elección por ranking antes que calidad | **Escritura de `history_canonical_v2` y replay `read_as_of`** | **ERROR CONFIRMADO DE CONTRATO** |
| [`fb_history_reconciler_rc6.py`, L24–43 y L155–180](https://github.com/mbalbo2023/Porota-trading/blob/328a6526a3eaf3851ef93778c1d0345593afc3ee/fb_history_reconciler_rc6.py#L24-L43) | PPI (10) → BYMA/A3 (20) → IOL (30) | Primero calidad, luego ranking; identidad omite moneda y ajuste | Sólo offline, no escribe DB ni es importado por observer vivo | **Orden incorrecto + divergencia con store** |
| [`dd_history_metrics_hf6.py`, L120–155, L181–249](https://github.com/mbalbo2023/Porota-trading/blob/328a6526a3eaf3851ef93778c1d0345593afc3ee/dd_history_metrics_hf6.py#L120-L155) | No arbitra proveedores | `full_ohlc_canonical_rows = COUNT(*)`; claves agregadas sin moneda | Métricas del dashboard | **ERROR CONFIRMADO EN MÉTRICA** |

### Importante: dos políticas distintas no son automáticamente un bug

El arbitraje **de ejecución en vivo** [`cy_market_source_arbitration_hf6.py`](https://github.com/mbalbo2023/Porota-trading/blob/328a6526a3eaf3851ef93778c1d0345593afc3ee/cy_market_source_arbitration_hf6.py) puede exigir PPI fresco y completo, o mantener `HOLD`, sin admitir sustitución IOL/BYMA para abrir PAPER. Esto es un **control deliberado y conservador**. La corrección histórica no debe convertir una fuente complementaria en autorización de trading ni tocar PPI Watch.

## 3. Mecánica exacta del error

1. `SOURCE_RANK` coloca `BYMA_EOD`, `BYMA` y `A3_CEM_CLOSING` en **20**, pero `IOL` en **30** ([store L23–25](https://github.com/mbalbo2023/Porota-trading/blob/328a6526a3eaf3851ef93778c1d0345593afc3ee/cu_history_store_v2_hf6.py#L23-L25)). El número más bajo tiene prioridad.
2. Al llegar una vela con la **misma** identidad completa, día, `price_basis` y `adjustment_basis`, `_prefer()` antepone ese número a la disponibilidad y a la calidad ([L276–283](https://github.com/mbalbo2023/Porota-trading/blob/328a6526a3eaf3851ef93778c1d0345593afc3ee/cu_history_store_v2_hf6.py#L276-L283)). La ruta `append_many → _append_connection → history_canonical_v2` usa esa decisión ([L289–337](https://github.com/mbalbo2023/Porota-trading/blob/328a6526a3eaf3851ef93778c1d0345593afc3ee/cu_history_store_v2_hf6.py#L289-L337)).
3. Se registra la calidad `FULL_OHLC` o `CLOSE_ONLY` en `metadata_json`, pero **no participa en `_prefer()`** ([L298–300](https://github.com/mbalbo2023/Porota-trading/blob/328a6526a3eaf3851ef93778c1d0345593afc3ee/cu_history_store_v2_hf6.py#L298-L300)). No se verifica `volume` para dar la etiqueta `FULL_OHLC`.
4. `read_as_of()` reconstruye revisiones conocidas a fecha de corte, pero vuelve a resolver el proveedor **por ranking**, sin filtrar calidad ni consultar discrepancias registradas ([L394–418](https://github.com/mbalbo2023/Porota-trading/blob/328a6526a3eaf3851ef93778c1d0345593afc3ee/cu_history_store_v2_hf6.py#L394-L418)).
5. El reconciliador offline hace otra cosa: compara primero `QUALITY_ORDER` y luego `SOURCE_RANK`. El test `test_full_iol_beats_a3_close_only_even_though_a3_has_better_rank` espera IOL ([`tests/test_history_reconciler_rc6.py`, L18–23](https://github.com/mbalbo2023/Porota-trading/blob/328a6526a3eaf3851ef93778c1d0345593afc3ee/tests/test_history_reconciler_rc6.py#L18-L23)). **Ese test no evalúa el writer real**. Los tests de convergencia del store cubren identidad, PIT, reintentos y atomicidad, pero no se localizó un test que compare IOL y BYMA para el mismo día bajo ambas políticas ([suite](https://github.com/mbalbo2023/Porota-trading/blob/328a6526a3eaf3851ef93778c1d0345593afc3ee/tests/test_rc6_history_convergence.py)).

## 4. Ejemplos deterministas *hipotéticos*, NO operaciones detectadas

Misma clave **GGAL / ACCIONES / BYMA / ARS / CI / 2026-10-01 / RAW / RAW_NO_ADJUSTMENT**, con validación de sesión, procedencia y reloj aprobada:

| Evidencia candidata | Datos | Rank histórico actual | Qué selecciona el store v2 | Qué selecciona el reconciliador offline |
|---|---|---:|---|---|
| IOL | `O=100, H=110, L=95, C=105, V=1000` | 30 | Pierde | **Gana** (calidad OHLCV) |
| BYMA/A3 | `O/H/L=NULL, C=106, V=NULL` | 20 | **Gana** | Pierde (sólo cierre) |

Incluso cuando **ambas** ofrecen OHLC completo, en ausencia de PPI el store y el reconciliador pueden elegir BYMA antes de IOL, incumpliendo la prioridad acordada. Si PPI ofrece sólo cierre y IOL OHLC, debe definirse por **consumidor**: PPI conserva identidad primaria; una estrategia que necesita rango intradiario no puede transformar su cierre en OHLC ni aceptar esa barra, pero puede ser admisible usar IOL como respaldo histórico de calidad comprobada. Un consumidor de retornos sólo de cierre puede seguir prefiriendo PPI, registrando origen y discrepancia.

**Condición indispensable:** las fuentes deben referirse a exactamente la misma identidad monetaria, término, fecha y base de ajuste. No se afirmó que tal colisión ya haya ocurrido en producción.

## 5. Otras inconsistencias derivadas y severidad

| Riesgo | Dónde / prueba | Consecuencia potencial | Estado |
|---|---|---|---|
| **P0 conceptual — orden de autoridad invertido** | `cu_history_store_v2_hf6.SOURCE_RANK`, `fb_history_reconciler_rc6.SOURCE_RANK` | Gana BYMA/A3 sobre IOL para igual serie, contradice contrato | **VALIDADO código; runtime NO_VERIFICADO** |
| **P0 conceptual — pérdida de calidad OHLC** | `_prefer` ignora `open/high/low`, `read_as_of` repite criterio | Señales de rango/ATR/volumen quedan sin soporte, días históricos inutilizables para backtesting OHLC | **VALIDADO condicional** |
| **P1 — dos motores dan ganadores distintos** | `choose_winner` quality-first frente a store rank-first | Test offline GREEN y DB selecciona otra fuente | **VALIDADO código** |
| **P1 — cobertura FULL inflada** | `dd_history_metrics_hf6.v2_store_metrics`: `full_ohlc_canonical_rows = COUNT(*)` | Dashboard puede presentar cierres parciales como OHLC completo | **VALIDADO código; cantidad real NO_VERIFICADA** |
| **P1 — métricas de identidad incompleta** | `target_universe`, `v2_store_metrics`, `target_store_coverage_metrics` usan cuatro claves, omiten `currency`; el store usa cinco | Cohortes ARS/USD agregadas o ambiguas en métricas, aunque estén separadas en DB | **VALIDADO código; incidencia NO_VERIFICADA** |
| **P1 — discrepancias registradas, no bloqueantes para selección** | `_append_connection` anota `SOURCE_PRICE_DISCREPANCY` en `history_source_conflicts_v2`, pero `_prefer` elige y `read_as_of` no las consulta | Una diferencia de precios puede entrar en una serie investigativa sin revisión consumidora | **VALIDADO código** |
| **P1 — backfill incompleto por falso «día cubierto»** | `fj_history_backfill_planner_rc6.plan()` considera sólo `canonical_dates`; `coverage()` de v2 cuenta días sin calidad | Un cierre parcial puede hacer que no se solicite un OHLC completo; backfill y store no convergen | **VALIDADO condicional** |
| **P1 — cambio de política no reproyecta SQLite automáticamente** | `history_canonical_v2.source_rank` materializado; `_prefer` compara contra valor persistido | Tras corregir números sólo en Python, punteros históricos y `read_as_of` podrían divergir hasta reconvergencia gobernada | **VALIDADO como riesgo de migración** |
| **P1 — política offline pierde dimensión monetaria** | `fb_history_reconciler_rc6.HistoryIdentity` y `fj_history_backfill_planner_rc6.Identity` carecen de `currency` / `adjustment_basis` | Si se conectaran a datos multi-moneda/bases, no podrían demostrar identidad exacta | **VALIDADO estructural, uso productivo NO_VERIFICADO** |
| **P2 — etiquetas de calidad no uniformes** | Reconciliador distingue `FULL_OHLCV`/`FULL_OHLC`; v2 etiqueta todo OHLC como `FULL_OHLC` sin diferenciar volumen; métricas declaran FULL todo | Reportes y pruebas no reflejan las mismas unidades o grados de evidencia | **VALIDADO código** |

### Qué está protegido y **no** debe declararse roto

- El **store v2** realmente separa `symbol`, `instrument_type`, `market`, **`currency`**, `settlement`, día, `price_basis` y `adjustment_basis`; tiene validaciones de OHLC parcial, monto y fechas. **No hay prueba de mezcla monetaria en la tabla canónica.**
- La ingestión A3 [`ew_a3_history_rc6.py`, L145–219](https://github.com/mbalbo2023/Porota-trading/blob/328a6526a3eaf3851ef93778c1d0345593afc3ee/ew_a3_history_rc6.py#L145-L219) **sí llama** a `append_many` del store v2, pero sólo permite escribir con sesión cerrada, `real_orders_sent=0`, identidad verificada y sin capacidad de órdenes. Puede obtener OHLC completo o sólo cierre según payload; no se debe asumir que siempre sea CLOSE_ONLY. Su actividad real en el Droplet está **NO_VERIFICADA**.
- La descarga post-cierre PPI [`rc6_postclose_history_job.py`, L33–51](https://github.com/mbalbo2023/Porota-trading/blob/328a6526a3eaf3851ef93778c1d0345593afc3ee/rc6_postclose_history_job.py#L33-L51) retorna explícitamente `BLOCKED_BY_POLICY / RC6_HISTORY_QUARANTINE`. Que exista un scheduler no significa que esa ruta escriba hoy.
- `DATA912` tiene un sink v2 específico [`cw_data912_history_v2_sink.py`](https://github.com/mbalbo2023/Porota-trading/blob/328a6526a3eaf3851ef93778c1d0345593afc3ee/cw_data912_history_v2_sink.py), pero es un fallback histórico de menor rango, desactivable y sin autoridad de ejecución, no una sustitución implícita del orden primario.
- El arbitraje PAPER/SHADOW de campos está separado de los históricos y prioriza IOL sobre BYMA correctamente; **no se verificó un envío de orden real ni habilitación de trading por este defecto histórico**.

## 6. Consecuencias posibles en backtesting

1. **Rechazos por dataset no íntegro:** una serie que el dashboard marca cubierta puede carecer de OHLC/volumen suficiente. El gate de integridad [`br_backtest_gate.py`, L28–69](https://github.com/mbalbo2023/Porota-trading/blob/328a6526a3eaf3851ef93778c1d0345593afc3ee/br_backtest_gate.py#L28-L69) exige calidad no sintética, grilla verificable y unidad de volumen, pero trabaja con otro archivo de velas; no se demostró que todos los backtests consuman `read_as_of`.
2. **Resultando backtests distintos por ruta:** un backtest que usa `read_as_of` puede elegir BYMA, mientras uno que usa `choose_winner` sobre los mismos candidatos puede elegir IOL, incluso con un mismo SHA. Esto es un riesgo matemático, no una corrida real observada.
3. **Indicadores irrepresentables o sesgados:** ATR, patrones de candela, high-low, volatilidad intradía y algunos stop/take-profit no se pueden evaluar honestamente con cierre solo. Prohibido inventar O/H/L o forward-fill para completar.
4. **Reproducibilidad y PIT:** los cambios de rank/reproyección deben preservar `version_known_at`, la disponibilidad `as_of`, la identidad, el ajuste y los payloads inmutables. No permitir que un histórico recopilado hoy aparezca disponible en decisiones de ayer.
5. **Conflictos sin clasificación por consumidor:** diferencias reales entre proveedores (sesión, ajuste, término, unidad, divisa o precio) deben quedar como `REVIEW_REQUIRED`, con tolerancias documentadas, sin promedio automático.

## 7. Plan propuesto FIX-FORWARD — **NO EJECUTADO**

**P0: definición de contrato y semántica**

1. Publicar una sola matriz versionada de autoridad histórica **PPI(1) → IOL(2) → BYMA/A3(3)**, con DATA912 fallback explícito y Yahoo sólo investigación si corresponde. Separarla del requisito PPI estricto del arbitraje live.
2. Evaluar por consumidor: identidad exacta y reloj PIT → validaciones → calidad **mínima requerida** (`CLOSE_ONLY`, `FULL_OHLC`, `FULL_OHLCV`) → prioridad de proveedor entre candidatos elegibles → discrepancias / cuarentena / selección registrada. No imponer calidad-first ciego que sobreescriba identidad PPI.
3. Unificar `cu_history_store_v2_hf6`, `fb_history_reconciler_rc6`, `fj_history_backfill_planner_rc6` en una definición de `policy_version`, `reason` y clave completa, con origen visible y no inferido.

**P1: pruebas de regresión bloqueantes**

4. Test de integración de **writer real + reconciliador + `read_as_of` + métricas** con mismo fixture: PPI/IOL/BYMA; IOL FULL vs BYMA CLOSE; ambos FULL; PPI CLOSE vs IOL FULL y dos consumidores; igualdad de fechas/relojes; revisiones correcciones; conflicto; ARS vs USD; RAW vs ADJUSTED; idempotencia.
5. Tests que obliguen `full_ohlc_canonical_rows = COUNT(*) WHERE open/high/low realmente completos`, separar `close_only_rows`, `full_ohlcv_rows` y `unknown_volume_kind`; calidad por familia/símbolo/moneda/base/sesión.
6. Gate de GitHub Actions que falle por **dos tablas de prioridad incompatibles** o por **diferentes ganadores** ante casos golden, y reporte JUnit/JSON de causas. El test aislado del reconciliador nunca debe sustituir el end-to-end del store.

**P1: diagnóstico y reconvergencia gobernada**

7. **Después del deploy del otro agente y sin interferir en rueda**, ejecutar desde GitHub Actions un inventario SQL **read-only y acotado** sobre una copia/snapshot consistente: fuente por familia/identidad/día, `NULL` O/H/L/V, revisiones por día y fuente, `history_source_conflicts_v2`, diferencias `history_canonical_v2` vs selección PIT, consumidores reales de histórico, estado/última corrida de los timers A3/Data912/PPI; publicar métricas, digest y recuentos.
8. Sin escritura sobre runtime mientras exista DEPLOY_OWNER. Si se confirma impacto, diseñar corrección **FIX-FORWARD** en candidato aislado: preservar `history_versions_v2` inmutables; reproyectar tabla canónica **sobre copia verificada**, comparar hash, PIT, cobertura, recursos y diffs; preflight bloqueante; sólo con ownership correspondiente evaluar promoción a producción. Cambiar `SOURCE_RANK` en Python **no** arregla por sí solo filas previamente materializadas.
9. Agregar alertas continuas cuando: `selected_source_rank != expected_policy`, `full_ohlc_reported != full_ohlc_verified`, una identidad monetaria se agrupe sin divisa, haya discrepancias sin triage, o un backfill considere completo un día no elegible para su consumidor.

## 8. Evidencia que falta para convertir riesgo en incidente productivo

**NO_VERIFICADO / pendiente del propietario del deploy:** snapshot readonly de `history_canonical_v2` y `history_versions_v2`; recuento de cruces IOL-BYMA/A3 por identidad exacta; historial de resultados `append_many` y `canonical_updates`; distribución real de `history_quality`; logs de timers/runners A3/Data912/PPI; rutas exactas de consumidores de `read_as_of` y dashboards/backtest; SHA/digest efectivo del contenedor en Droplet. No ejecutar sondeos pesados ni abrir escrituras durante el deploy en curso.

## 9. Cierre

**Resultado:** `VALIDADO` defecto de precedencia histórico, divergencia store/reconciliador y defecto de rotulado/agrupamiento en métricas. `NO_VERIFICADO` daño operativo material o ejecución real afectada. **No hubo cambios en GitHub, workflows, Droplet, DB ni PPI Watch.** Priorizar la corrección **después** de finalizar/coordinar el deploy vigente, sin rollback ni alteración del modo PAPER/SHADOW.
