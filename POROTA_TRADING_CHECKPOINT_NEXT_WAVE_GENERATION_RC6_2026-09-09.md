# POROTA TRADING — NEXT WAVE GENERATION RC6
## 2026-09-09

## Regla canónica
Todo frente independiente se ejecuta/prepara/valida en paralelo sin requerir autorización adicional. Solo se serializan activaciones runtime cuando comparten estado mutable, DB, contenedores, scheduler u ownership operativo y mezclar despliegues impediría diagnóstico seguro.

Invariantes absolutos: `PRODUCTION_PAPER`, `real_orders_sent=0`, producción real NO-GO, no pruebas de rutas de órdenes reales, browser read-only, no rollback automático.

## Ola 9 — P0 — API OPERABILITY / FAMILY READINESS / MARKET SESSION SAFETY
Objetivo: cerrar el P0 histórico del checkpoint: matriz de operabilidad real PPI familia por familia y gates fail-closed antes de READY_PAPER.
Incluye: ACCIONES, CEDEARS, BONOS, LETRAS, ON, OPCIONES, FUTUROS, ETF, ACCIONES-USA, CAUCIONES, FCI, FCI-EXTERIOR, LICITACIONES, INDICES y familias adicionales; campos contractuales; settlement; unidades; mínimos/steps; price basis; API capability; calendario BYMA fail-closed; calendario de mercado subyacente USA para CEDEARs; reconciliación Issues #36/#37; cauciones contract gaps.
Gate: ninguna familia promovida por DOM solamente.

## Ola 10 — P0 — RISK ENGINE BINDING
Objetivo: cerrar controles de riesgo que condicionan cualquier campaña PAPER seria.
Incluye: Daily Risk ledger, equity por moneda, pérdidas realizadas, soft/hard stop diario, overnight/carry, exposición por trade, stop/target, RR neto de costos, fees, liquidez, slippage, concentración/correlación, concentración sectorial BINDING, Patrimonial Gate, settlement, family/data risk, kill switch persistido fail-closed y alertas.

## Ola 11 — P0/P1 — EVENT RISK + GDELT
Objetivo: convertir el feed GDELT ya validado en SHADOW en un subsistema de riesgo verificable y no espasmódico.
Incluye: deduplicación, freshness, novelty, source health, corroboración, exposición real del instrumento, preopen/postclose, persistencia, replay/backtest, degradación fail-safe si GDELT está stale/down, integración con dashboard y Risk Engine. Una noticia aislada nunca habilita trading.

## Ola 12 — P1 — DOM / EXPECTED UNIVERSE / CONTRACT EVIDENCE COMPLETENESS
Objetivo: cerrar truncamiento de 50 filas, identidades duplicadas y reconciliación DOM↔API↔universo esperado.
Incluye Opciones 50/43, coverage por familia, Contract Evidence dashboard/wiring, specialized family evidence y dependencias externas PPI Issue #6.

## Ola 13 — P1 — HISTORY / A3 / BACKFILL / SOURCE RECONCILIATION
Objetivo: históricos profundos y confiables sin hammering.
Incluye alignment PPI↔A3, payload validation, timestamps/unidades/timezone, gaps/duplicates, provenance History Store v2, planner, smart retry/backoff, bootstrap, daily incremental, weekend deep reconciliation, source health y A3 live/order routing OFF.

## Ola 14 — P1 — SRE / INTROSPECTION / EARLY WARNING
Objetivo: introspección operativa completa durante jornada.
Incluye observer/dashboard, DB/heartbeat, mode/session, scheduler truth, PPI auth/errors/rate-limit, historical/backfill/source sync, staleness/coverage/gaps, API health, warning vs actionable incident, Telegram anti-spam, latency RCA y panel `/En vivo`/SRE.

## Ola 15 — P1 — FORWARD LAB / LEARNING / MFE-MAE
Objetivo: evidencia empírica robusta, sin hindsight ni promotion automática por métricas débiles.
Incluye MFE/MAE normalizado neto de costos, expectancy, HAC/block bootstrap por trading_day_ar, leave-one-symbol-out, leave-one-day-out, subperiodos/cohorts, multiple-testing/DSR cuando corresponda, top1/top2/leave-top-out, sector/correlation coverage y provenance del mapa sectorial.

## Ola 16 — P1 — BACKUP / RETENTION / DISK GOVERNANCE
Objetivo: materializar política de dos generaciones (`daily-current` + `daily-previous`), disk thresholds, quiescence/checkpoint, retention segura y cierre de Wave4/B5.
No DELETE/VACUUM/PRUNE hasta `EVIDENCE_LOST=0` y migración transaccional verificada.

## Ola 17 — P1 — TABLET / VOICE ACCESS / UX FINAL
Objetivo: validar experiencia física final Samsung/Voice Access.
Incluye cero scroll horizontal, tablas/card layout, controles textuales, máximo inicial de filas, drill-down, P&L semáforo, timestamp último mark, expansión/scroll/foco preservados y refresh no disruptivo.

## Ola 18 — P2 — CONTROL PLANE / RECOVERY HOUSEKEEPING
Objetivo: reconciliar y cerrar artefactos de control plane, pruebas E2E y capacidad de recovery manual.
Incluye Issue #40 E2E ficticio, housekeeping Telegram crítico, external rollback/recovery Issue #25 bajo política actual: jamás rollback automático; recuperación solo último recurso y con autorización explícita.

## Ola 19 — NEXT VERSION / P2 — IOL READ-ONLY CROSS-VALIDATION
Objetivo: mantener separado de RC6 el Issue #38 de IOL.
Primera fase solo read-only: secondary market data, PPI cross-validation, fixed income/options/cauciones/corporate events/FCI. No ejecución, no DDJJ, no caución real, no órdenes.

## Orden de activación runtime
Preparación/CI de Waves 9–19: paralela por defecto. Activaciones runtime: serializadas según dependencia y riesgo. Prioridad runtime: P0 Wave9 → Wave10 → Wave11; luego P1 por readiness e impacto. Wave19 no entra en RC6 runtime.
