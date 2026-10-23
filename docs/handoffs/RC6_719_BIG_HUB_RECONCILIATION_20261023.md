# RC6 — conciliación Big Hub y dictamen causal (23/10/2026 UTC)

WORKSTREAM_ID=WS-RC6-719-DASHBOARD-GRAIN-20261022. Corte de esta auditoría: 23/10 ~01:35 UTC. **NO_GO integral.** El estado cambia con la rueda y el host; no extrapolar este corte. La instrucción vigente del operador es PAPER/SHADOW, cero órdenes reales, PPI Watch intocable, backups deshabilitados, FIX-FORWARD; sin rollback, estrés de host, subagentes ni tareas programadas.

## Identidad y recibos

- Producto instalado: `deploy/rc6-pr69-isolated-20260915@c0a5ad35a731928c0c214b11a7fac9ac99681155`, tree `f3c41369c6165f7f964836ad4f06d19c5cdd1777`. [Predeploy #39871191336](https://github.com/mbalbo2023/Porota-trading/actions/runs/39871191336) 4555/4555; [Deploy #39873839848](https://github.com/mbalbo2023/Porota-trading/actions/runs/39873839848) SUCCESS, `VALIDATED_RUNTIME`, `PRODUCTION_PAPER`, `real_orders_sent=0`. El deploy no acredita GREEN integral.
- Corrección candidata [PR #720](https://github.com/mbalbo2023/Porota-trading/pull/720): `2a8b3f7c776355c5758b98a9dc2dd0dfb94fd737`, tree `6aba111889074a5adaec56c7cca14b96de284d1e`. [Predeploy #39882123066](https://github.com/mbalbo2023/Porota-trading/actions/runs/39882123066) SUCCESS: 4562 descubiertos/ejecutados, cero FAIL/ERROR/SKIP/XFAIL, una exclusión gobernada, gate PAPER y contrato de artifact GREEN. Artifact [#12472305928](https://github.com/mbalbo2023/Porota-trading/actions/runs/39882123066/artifacts/12472305928), ZIP digest `sha256:1f237a7c8fc6b0ca08466514ce5d72f24769168dd702f3abf2d44e14820d863f`, expira 30/10 01:28 UTC. **No instalado ni aceptado en Droplet** en este corte.
- [Diagnóstico read-only #39881736672](https://github.com/mbalbo2023/Porota-trading/actions/runs/39881736672) SUCCESS, sin host mutation. Snapshot regular `decision_evidence_latest.json` de 1.999.442 bytes excede lector de 1 MiB; `counterfactual_learning_rc6.json` ausente; por eso los cuatro contadores no son cero verificado. Ledger PAPER CLOSED 132 posiciones reconciliadas por siete grupos con la proyección de performance; ello no prueba edge OOS. El PR #720 añade una publicación compacta hash-verified y lector bounded, y corrige grano/cadencia de integraciones; su resultado runtime queda pendiente de promoción y nueva publicación factual.
- [Censo visual #39876835881](https://github.com/mbalbo2023/Porota-trading/actions/runs/39876835881): 52 rutas, 78 capturas, 49 tablas, 310 diálogos; 203 **apariciones** de NO_VERIFICADO, 83 STALE, 52 OFF_BASELINE, cero JS. No transformar ocurrencias en tickets únicos. Las 3 capturas de usuario se documentaron con huellas y RCA en [handoff #719](RC6_719_GENERAL_AUDIT_NIGHT_HANDOFF_20261022.md).

## Matriz C0–C6

| Frente | Evidencia y dictamen | Siguiente condición verificable |
|---|---|---|
| C0 inventario | 40 issues abiertos (sin PR) al inicio de la conciliación; 16 cerrados en esta fase y 24 continúan abiertos. Además #425/#411/#71 se cerraron antes del corte de 40 tras contrastar código y procedimiento vigente. PRs antiguos abiertos no equivalen a trabajos en curso. | Mantener la matriz inferior por issue y revalidar HEAD/owners/runs. |
| C1 deploy | Producto c0a5ad35 VALIDATED_RUNTIME. #720 sólo Predeploy GREEN. Workflow V2 exige merge de dos padres, árbol idéntico, artifact congelado, autorización exacta #624 y owner global. El legado `deploy.yml` no es la ruta operativa. | Promover únicamente candidato exacto con gates y admisión PAPER, jamás atribuir Predeploy al host. |
| C2 histórico/fuentes | 1.151 filas legacy `production_history` sin linaje original completo (identidad, moneda/bases, unidad, reloj provider, versión PIT); 2.518 claves catálogo ambiguas. No hay recibo autoritativo de materia prima por fila. | Fuente externa original/versionada; de otro modo `ANNUAL_NO_SELECTABLE_CANONICAL_SERIES`. Sin inferir bases por ticker ni reingesta PPI. |
| C3 backtest | Framework y SHADOW existen; datasets aptos PIT y OOS económico reproducible no certificados. | Datos originales/versionados, availability clocks, purga/embargo, costos y comparación neta. |
| C4 RCA | #720: grano de drawer de servicio e intervalos check 300s frente TTL visual 180s; compact snapshot 1.999.442B frente límite 1MiB. Tests focales 13/13 y suite 4562/4562. | Evidencia runtime tras publicación y regresión visual exacta; documentar cualquier falla nueva por SHA/causa. |
| C5 dashboard/operación | 52 rutas estructuralmente navegadas; performance CLOSED conciliado. No se probaron todas las celdas de cada página ni la rueda OPEN. Dos motores `telemetry_count=0`/`CAPACITY_EVIDENCE_INVALID` en MARKET_CLOSED; cron genuino y ACK Telegram no certificados. | Rueda OPEN con cortes causales, denominadores, writer→DB→API→UI, latencia y seguridad PAPER. |
| C6 aceptación | Integridad del Droplet **NO_GO** aun con Deploy anterior SUCCESS. #720 sin merge/deploy. Host stress prohibido por instrucción posterior. | Ningún GREEN integral hasta cerrar P0 y probar fuente anual + capacidad OPEN + postflight; publicar NO_GO exacto si no. |

## Issues abiertos: 24 requerimientos o controles, uno por fila

| Issue | Estado reconciliado | Discriminante actual |
|---|---|---|
| [#679](https://github.com/mbalbo2023/Porota-trading/issues/679) | PENDIENTE P1/P2 | Workers/timers, duplicación, consumo y schedule genuino; auditoría histórica no certifica rueda nueva. |
| [#624](https://github.com/mbalbo2023/Porota-trading/issues/624) | BLOQUEADO P0 | Aceptación SHADOW completa, capacidad OPEN y evidencia externa; stress host del plan antiguo queda excluido por orden posterior. |
| [#511](https://github.com/mbalbo2023/Porota-trading/issues/511) | NO_VERIFICADO | Cron/Telegram genuino y continuidad OPEN; snapshot CLOSED fresco no prueba entrega/ACK real. |
| [#510](https://github.com/mbalbo2023/Porota-trading/issues/510) | HISTÓRICO, RCA sin cierre causal | Incidente 14/10 heartbeat y SQLite; runtime actual saludable no explica por sí solo aquella falla. |
| [#495](https://github.com/mbalbo2023/Porota-trading/issues/495) | NO_VERIFICADO | Trigger Work observado; acción GitHub/Codex end-to-end no probada. No usar como control de release. |
| [#491](https://github.com/mbalbo2023/Porota-trading/issues/491) | PARCIAL | Writer `_reader_status_write` usa `closing(sqlite3.connect)` en producto y critical actual saludable; RCA histórica de locks/restarts, backups/timers y continuidad OPEN no completa. |
| [#489](https://github.com/mbalbo2023/Porota-trading/issues/489) | LEGACY, NO_VERIFICADO | Orden G5P/V3 y gates materiales por SHA original; no equiparar suite productiva actual con certificado de los 1.202 cortes exigidos allí. Revisar contra #624 antes de cierre. |
| [#485](https://github.com/mbalbo2023/Porota-trading/issues/485) | NO_VERIFICADO | AGENTS/README productivos y guard de continuidad en mismo checkout, más tarea de recuperación adversarial; bundle runtime no basta para probarlos. |
| [#478](https://github.com/mbalbo2023/Porota-trading/issues/478) | PENDIENTE | Retención material 365d, DB/WAL, raw y ciclo recurrente con integridad; backups permanecen deshabilitados por instrucción posterior. No hacer DELETE/VACUUM por conjetura. |
| [#473](https://github.com/mbalbo2023/Porota-trading/issues/473) | CONTROL | Ledger pareado de WRITE/DEPLOY owner; no tratarlo como tarea de producto. |
| [#471](https://github.com/mbalbo2023/Porota-trading/issues/471) | CONTROL | Ledger pareado de WRITE/DEPLOY owner; release pareado al cierre de la misión. |
| [#469](https://github.com/mbalbo2023/Porota-trading/issues/469) | NO_VERIFICADO | Orden de reauditoría de #466; sin comentario de aceptación en el issue, comparar con auditorías sucesoras antes de cerrar. |
| [#468](https://github.com/mbalbo2023/Porota-trading/issues/468) | NO_VERIFICADO | Auditoría de históricos/velas/scalping; materia prima anual y PIT siguen bloqueadas pese a auditorías posteriores. |
| [#467](https://github.com/mbalbo2023/Porota-trading/issues/467) | PARCIAL | Dashboard terminal implementado y 52 rutas navegadas; el propio cierre de workstream dejó integración pendiente, y queda RCA por campo/dato en #719/#720. |
| [#101](https://github.com/mbalbo2023/Porota-trading/issues/101) | PARCIAL | IOL complementario 4.874 quotes (1.950 LIVE, 2.493 CACHE_STALE, 431 SOURCE_UNAVAILABLE) en corte anterior; cobertura y validación diaria factual no completas. |
| [#72](https://github.com/mbalbo2023/Porota-trading/issues/72) | BLOQUEADO | Historias/velas SHADOW y forward/backtest, no BINDING sin PIT/OOS, costos, jornada real y fuente apta. |
| [#67](https://github.com/mbalbo2023/Porota-trading/issues/67) | PARCIAL | Código instalado publica tarjeta Action 4 en `/reportes` y GET/POST `/api/reports/action4`; publicación factual y descarga HTTP200 no comprobadas en host. |
| [#61](https://github.com/mbalbo2023/Porota-trading/issues/61) | PENDIENTE P0 | Auditoría exacta: `_focus_coverage()` default 4 y `allow_openings = focus["allow_new_openings"] and sampling_allowed`; no gate persistido `GO_PAPER_SCALPING/NO_GO_PAPER_SCALPING` encontrado en bundle. Ver [recibo #6355249817](https://github.com/mbalbo2023/Porota-trading/issues/61#issuecomment-6355249817). Sigue PAPER/0; falta interlock global para aperturas simuladas. |
| [#45](https://github.com/mbalbo2023/Porota-trading/issues/45) | HISTÓRICO NO_VERIFICADO | Fallo preopen 09/09; servicios actuales sanos no son RCA del evento ni evidencia de día operacional posterior. |
| [#44](https://github.com/mbalbo2023/Porota-trading/issues/44) | NO_VERIFICADO | Exige ACTIVE_LEGACY_RUNTIME=0 y ENABLED_LEGACY_TIMERS=0; falta inventario host exacto de RC4/RC5/HF5. |
| [#38](https://github.com/mbalbo2023/Porota-trading/issues/38) | PARCIAL/NEXT_VERSION | IOL read-only existe como complemento, pero gates originales de cross-validation e intraday no cerrados; ninguna ruta de orden. |
| [#28](https://github.com/mbalbo2023/Porota-trading/issues/28) | NO_VERIFICADO | A3 background sólo con alineación PPI↔A3 y payload factual; no habilitar por edad de issue. |
| [#27](https://github.com/mbalbo2023/Porota-trading/issues/27) | CONTROL ACTIVO | Decisión sin wiring RC4 ejecutable; contradicción vieja sobre rollback cedió a FIX-FORWARD, falta recibo host para cierre del gate legacy. |
| [#6](https://github.com/mbalbo2023/Porota-trading/issues/6) | BLOQUEADO EXTERNO | Implementación HF6 de Porota cerrada; issue OPEN explícitamente por definiciones contractuales oficiales de PPI. No inferir contratos de derivados/especializadas. |

## Limpieza documental y riesgo de reapertura

En esta sesión se cerraron con comentario causal [#425](https://github.com/mbalbo2023/Porota-trading/issues/425) (paginación LIVE instalada), [#411](https://github.com/mbalbo2023/Porota-trading/issues/411) (estados de volumen/confirmación por identidad) y [#71](https://github.com/mbalbo2023/Porota-trading/issues/71) (runbook de deploy anterior); luego #70, #25, #40, #446, #452, #462, #465, #20, #55, #599, #600, #603, #620, #458, #460 y #464. `completed` identifica un alcance histórico efectivamente entregado; `not_planned` identifica instrucciones sustituidas. **Ninguno de estos cierres elimina los discriminantes actuales de la tabla anterior.**

No se lanzó un test sintético Telegram ni una sonda OPEN en MARKET_CLOSED. No se encontró archivo autoritativo para reconstruir bases/moneda/PIT de las 1.151 filas. La auditoría estática del bundle desplegado contiene 529 archivos, 414 módulos Python; no equivale a un análisis exhaustivo probado de todos sus paths, ni se declararon bugs a partir de patrones `except` sin reproducción. El defecto P0 de #61 se registró y no se ocultó bajo el Predeploy exitoso.

## Próximo gate de decisión

1. Completar #61 en candidato aislado con fuente persistida vinculante y tests fail-closed; no declarar resuelto por un estado informativo del dashboard.
2. Promover #720 sólo con autorización exacta de #624, owner global, árbol/artifact y postflight, después de resolver cualquier bloqueo de branch/gate. Luego regenerar evidencia contrafactual real y navegación exacta.
3. Obtener materia prima anual por identidad y corte PIT, y muestras de la primera rueda OPEN para capacidad/freshness; si no existen, conservar `NO_GO` y ownership liberado.
