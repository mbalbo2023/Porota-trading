# PLAN — WS OPS / OCT-01 — 2026-09-30

## Gobierno
- WORKSTREAM integrador: WS-OPS-PREOPEN-CLOSURE-20260930
- branch integradora: ops/ws-ops-preopen-closure-20260930
- SHA base de esta tanda: 94b31b3a362f8d0802c1c5f800762c6d024deae1
- modo: DEPLOY_OWNER mientras se integre y despliegue.
- Productiva: deploy/rc6-pr69-isolated-20260915.
- No escribir directo en productiva.
- PAPER/SHADOW ONLY.
- PRODUCTION_PAPER / SIMULATION.
- real_orders_sent=0.
- real routes=NOT_CALLED.
- PPI Watch NO TOCAR.
- FIX-FORWARD ONLY.
- GitHub + GitHub Actions como plano de control.
- Un único candidato reconciliado y un único Deploy V2 final.

## Instrumentación y continuidad
Todo cambio debe:
1. ejecutarse mediante branch aislada + PR;
2. tener Action reproducible;
3. producir artifact/evidencia cuando corresponda;
4. registrar checkpoint frecuente con SHA/runs/hallazgos/pendientes;
5. ser retomable desde otro chat sin depender del contexto conversacional;
6. separar evidencia de código, pipeline, artefacto y runtime;
7. paralelizar análisis/tests/probes siempre que no solapen WRITE_OWNER ni DEPLOY_OWNER.

## Workstreams paralelos
### WS-DASH-06 — Dashboard / UX / verdad visible
Branch: work/ws-dash-06-oct01-ux-truth-20260930
Scope:
- Dashboard En Vivo:
  - investigar tarjeta "Verificaciones pendientes";
  - Resultado de las últimas ruedas: 8 tarjetas, 4+4, excluyendo fines de semana y feriados;
  - operaciones pendientes de liquidación: impedir solapamiento de "PENDING CONFIRMATION" con columna contigua;
- formato global de tablas: resultados positivos en verde + negrita, negativos en rojo + negrita, en todos los menús;
- Universo operativo e Instrumentos: búsqueda/filtro directo por instrumento y filtros por cabecera estilo tabla, sin paginar manualmente hasta encontrar el símbolo;
- Validación: corregir cabeceras superpuestas/filas; tarjetas deben reflejar situación actual;
- Riesgo: corregir formato/legibilidad de tablas;
- Evidence v2: mostrar timestamps correctos y sin fechas ambiguas/desactualizadas;
- Operaciones fuera del catálogo: revisar y corregir semántica/freshness de fechas.

### WS-RUNTIME-07 — Truth / salud / estrategia / APIs
Branch: work/ws-runtime-07-oct01-truth-health-20260930
Scope:
- Trading > Motor: RCA de decisiones bloqueadas por DAILY_RISK_* / clock; no esconder bloqueos legítimos.
- Trading > Estrategia: todas las tarjetas deben derivarse de estado/runtime vigente.
- Trading/Riesgo: verificar APIs/datasets reales; no mostrar vacío si existe evidencia, y marcar NO_VERIFICADO si falta fuente.
- Familias: reconciliar estado real por instrumento; revisar familias completamente pausadas.
- IOL: distinguir MCP/provider de source-path interno; SOURCE_UNAVAILABLE debe ser scoped y no equivaler a IOL=0.
- Evidence v2: verificar contenido y updated_at/as_of.
- Validación M: verificar auditoría diaria, avance y estados STALE/UNKNOWN con causa explícita.
- Análisis: tarjetas/tablas IOL deben representar realidad y provenance.

### WS-HISTORY-08 — Históricos / velas / rotación
Branch: work/ws-history-08-oct01-coverage-rotation-20260930
Scope:
- revalidar reparación histórica fallida/NO_VERIFICADA antes de repetirla;
- históricos: tarjetas/tablas correctas y actualizadas;
- velas: RCA de falta de actualización de varios días y reparación fail-closed;
- revisar rotación actual de 20 instrumentos ante universo READY ampliado; dimensionar por tiempo de ciclo/freshness, no por número arbitrario;
- cubrir nuevas familias PAPER cuando técnicamente aplique;
- no declarar cobertura histórica sólo porque un instrumento es READY.

### WS-INSTRUMENTATION-09 — CI / probes / handoff
Branch: work/ws-instrumentation-09-oct01-ci-handoff-20260930
Scope:
- corregir falso positivo del auditor Dashboard por literales JS;
- tests de regresión;
- Actions paralelas para dashboard, runtime/truth, history/candles y preopen;
- artifacts Markdown/JSON por frente;
- checkpoints automáticos/serializables;
- guard de invariantes PAPER/PPI Watch;
- workflow de repair histórico debe conservar evidencia aun con timeout/RC != 0.

## Criterios obligatorios antes del merge integrador
- Todos los PRs de workstream GREEN.
- Reconciliación contra HEAD real de branch integradora.
- Tests focales + suite pertinente.
- No PENDING genérico donde exista estado exacto.
- No fechas/freshness inventadas.
- No IOL=0 por SOURCE_UNAVAILABLE de un path.
- Dashboard sin HTTP 500, sin DOM masivo y con búsqueda accesible.
- Preopen T-45/T-10 GREEN o BLOQUEADO con evidencia.
- real_orders_sent=0 y real routes NOT_CALLED.
- PPI Watch intacto.

## Criterios del único Deploy V2 final
- Predeploy V2 exacto GREEN.
- Artifact inmutable / build once.
- Mismo artifact testeado y desplegado.
- Runtime: PRODUCTION_PAPER.
- real_orders_sent=0.
- real_order_routes=NOT_CALLED.
- PPI Watch intacto.
- Dashboard/Observer running.
- rutas críticas HTTP 200.
- auditoría Zero-Known-Error inmediata GREEN.
- auditorías de estabilidad GREEN.
- readiness/familias/históricos/velas con evidencia exacta, sin regresión silenciosa.
- cleanup Docker seguro y medido.
- actualizar CURRENT_STATE / ACTIVE_PENDING / ERROR_REGISTRY / LESSONS y liberar ownership.
