# RC6 #679 — auditoría de backups y dashboard, 21/10/2026

Estado: **DESARROLLADO local / NO_VERIFICADO_RUNTIME para correcciones**. Producto en droplet: `bc661130968f2b85b8b2146f76923ddd26eb7837`. Candidato previo PR #681: `e9e9ab0f6e76bff6cbe90e95986b8f7b2cce718b`; ningún cambio de esta adenda ha sido desplegado al momento de este corte. PAPER `PRODUCTION_PAPER/SIMULATED`, `real_orders_sent=0`; PPI Watch no accedido. Owner diagnóstico #471/6318403802 + #473/6318406633; integración #471/6318417105 + #473/6318419517 y ampliaciones apareadas. DEPLOY_OWNER no adquirido.

## Evidencia leída

- [Censo 52 rutas y drilldown exacto](https://github.com/mbalbo2023/Porota-trading/actions/runs/39618050473): 52/52 HTTP 200 en 3,95–4,5 s, sin HTML ni token en logs. La primera ficha/contrato/mercado/evidencia con identidad exacta dio HTTP 200. Contadores NO_VERIFICADO/STALE son **ocurrencias de texto HTML**, incluyen campos repetidos y no equivalen a defectos distintos.
- [Manifiesto host exacto, 31 archivos](https://github.com/mbalbo2023/Porota-trading/actions/runs/39616125947): 4.519.770.023 bytes lógicos en raíces backup; libre 4.413.583.360 bytes. Host en SHA productivo, PAPER/0, tres contenedores activos en imagen esperada, PPI Watch NOT_ACCESSED. No hubo borrado.
- [Hashes comprimidos históricos](https://github.com/mbalbo2023/Porota-trading/actions/runs/39617084531): cuatro archivos de 91.768.197 bytes, cuatro SHA-256 distintos. Tamaño igual **no prueba duplicación byte a byte**; igualdad del contenido SQLite descomprimido aún NO_VERIFICADA.
- [Esquema histórico del host](https://github.com/mbalbo2023/Porota-trading/actions/runs/39617681098): `history_canonical_v2` existe, max_rowid 569482, pero carece de `currency`, `price_basis`, `adjustment_basis`, `version_known_at`; `history_local_daily_v1` existe con 431 max_rowid. No atribuir estos máximos a filas actuales o cobertura global.

## RCA por sistema

1. SHADOW comprometido sí estaba coherente y fresco en muestra: as_of 14:34:21 UTC, edad 61,2 s, presupuesto 120 s, dos motores. La fila **Selector dinámico SHADOW** usa 30 s fijo y marcó STALE: contradicción de presentación. Fix local: emplear el presupuesto de generación y `shadow_fresh`; regresión 35 s fresca / 114 s vencida.
2. La cobertura de observaciones se calcula como booleano en `read_runtime` y `run_shadow`, pero `durable_report_view` la omitía. En Sistema/Evidencia aparecía NO_VERIFICADO aun con corte coherente. Fix local: persistir sólo el veredicto acotado booleano, conservando desconocido si no hay prueba. **No cambiar** tamaño ni autoridad de la muestra.
3. Capacidad OPEN `NO_VERIFICADO` con `capacity_policy.status=OFF_BASELINE`: no existe certificación `OPEN_EVIDENCE_VERIFIED`; la pantalla bloquea `safe_capacity` correctamente. No se activa dinámica por presión visual. `preopen_quality.status=NO_VERIFICADO` es pendiente separado.
4. Workers: supervisor 2,8 s FRESH; lector 3,3 s FRESH; scalping 161 s FRESH con presupuesto 300 s; caución 29 s FRESH; runtime 26,9 s FRESH; riesgo ARS `BASELINE_UNAVAILABLE` y otras monedas READY. Este recorte no certifica fills ni todas las fuentes.
5. Sistema/Scheduler publica 32 filas `recorded_at`, `active_state`, `enabled_state`, `next_elapse`, `last_trigger`; el adapter esperaba `captured_at`, `state`, `next_run`, `last_run`. Resultado: 272 ocurrencias NO_VERIFICADO en pantalla pese al snapshot AVAILABLE. Fix local: mapear campos y conservar `last_success` desconocido si no hay timestamp de éxito.
6. Análisis anual: `/analitica/anual` HTTP 200 pero **0 enlaces**. `p.history()` recae a `production_history` (total 1151), 0/10 filas con identidad completa y 0/10 con ambas bases. La tabla canónica histórica no satisface esquema exacto (ver evidencia arriba). Fix de UX: mostrar `ANNUAL_NO_SELECTABLE_CANONICAL_SERIES` con origen y motivo. La migración de identidad/bases/version clocks requiere linaje verificable; NO_GO para un informe anual numérico inventado.
7. Instrumentos: buscador lista 10 enlaces de ficha; las subpantallas sin `identity` piden selección exacta por diseño. Con una identidad exacta, ficha, contrato, mercado y evidencia dan HTTP 200; Mercado mostró STALE en reloj de cotización. No convertir recepción reciente en reloj del proveedor.
8. Sistema/Backups buscaba `backup_status.json`, actualmente `EVIDENCE_NOT_PUBLISHED`; no demostraba generación ni ausencia. Fix local: mostrar política de suspensión `SUSPENDED_BY_OPERATOR` y aclarar que el borrado requiere recibo de host.

## Manifiesto de backups

| Raíz | Archivos | Bytes lógicos | Contenido |
|---|---:|---:|---|
| `/opt/porota-backups` | 3 | 1.353.282.662 | tar.gz general 16/17/19 oct |
| `data/paper_v17/artifacts/observer_v17.db/backups` | 11 | 2.985.151.483 | cuatro `history.db.gz`, cuatro `observer.db.gz`, `.restore_test_7.db` + WAL/SHM |
| `data/backups` | 17 | 181.335.878 | archive wave4 181.288.011 B + dos auxiliares, catorce archivos de preopen-retire |
| **Total** | **31** | **4.519.770.023** | El espacio liberable físico difiere por bloques y archivos abiertos |

El `.restore_test_7.db` mide 1.798.787.072 B, mtime 20/10. Es un temporal de restauración abandonado **probable**; no se elimina hasta comprobar que ningún FD lo use. Los cuatro archivos históricos comprimidos tienen mismo tamaño pero SHA distintos; su contenido lógico duplicado sigue NO_VERIFICADO. `data/deploy/rc6-failed-cold-*` (≈748,8 MB) es evidencia de fallos, no backup ordinario: preservar. Bases vivas, SHADOW, reportes, SQLite WAL operativos, imágenes activas y PPI Watch fuera de la eliminación.

La cifra aproximada de **17 GB** provenía de un gate hipotético de creación del backup general, no de 17 GB ya consumidos por backups. En el mismo filesystem el cálculo es `2 × source_upper_bound + 2 GiB de reserva + 64 MiB de overhead`: staging más archivo final y reserva. El estimador anterior recorría `data` e incluía el subárbol `backups` y WAL/SHM que el archivador real excluye; también volvía a contar copias anteriores dentro de la fuente. El fix alinea la medición con el archivador. Todavía no hay un nuevo `source_upper_bound` del host para afirmar un requerimiento exacto actualizado. Los 4.519.770.023 B son existencias lógicas en tres raíces, no esa ecuación ni espacio físico garantizado recuperable.

**Crecimiento y duplicación:** tres archivos generales fechados 16/17/19 de octubre, cuatro históricos y cuatro PAPER entre otros artefactos demuestran acumulación de derivados y un temporal de restauración probable. No hay una serie temporal de `df` o tamaño lógico homogénea suficiente para ajustar una tasa, mucho menos probar una ley exponencial. La repetición de archivos por período es real; identidad byte a byte de los cuatro históricos es falsa según hashes; duplicación lógica de filas sigue NO_VERIFICADO. Las bases vivas y sus WAL tienen función operativa y se preservan. El nuevo control impide a los productores crear respaldos, y housekeeping preserva los existentes hasta el borrado exacto con recibo.

## Productores y solución en candidato

- Timer `porota-host-general-backup-rc6.timer`: pasar a `QUARANTINED_TIMER` masked/inactive, entrypoint con guard de política. Preopen y deploy V3 dejan de exigir/reactivar el timer.
- `bi_operational_services.create_backup/create_history_backup/service_tick`: política compartida fail-closed; `force=True` tampoco crea archivos.
- `az_maintenance_scheduler` legacy: retirar `maintenance_daily_backup` y bloquear invocación directa del job; `y_infra_monitor` respeta la misma política.
- Política: `backups.suspended=true`, retención 0, sin pins/protección de rutas backup; housekeeping de Deploy V2 aún usa `--preserve-backups` hasta la eliminación exacta, para no borrar sin recibo.
- Script `scripts/rc6_679_backup_suspension.py`: inventario allowlist con límite 64/10 GiB, plan por path/device/inode/tamaño/mtime, rechazo de enlaces/FD abiertos/deriva, APPLY sólo tras candidato `VALIDATED_RUNTIME`, imagen exacta en tres contenedores, PAPER/0, timer masked y guard activo en observer/host. El Deploy V2 canónico lo invoca después de restaurar y verificar timers, genera recibo persistente y falla si faltan pruebas. Requiere DEPLOY_OWNER global y promoción autorizada. **No ejecutado todavía**.

## Censo pantalla por pantalla

| Pantalla | HTTP | Ocurrencias NO_VERIFICADO | Ocurrencias STALE | Atribución / límite |
|---|---:|---:|---:|---|
| `/` | 200 | 14 | 0 | Datos o campos sin evidencia; revisar detalle factual |
| `/en-vivo` | 200 | 21 | 3 | Datos o campos sin evidencia; revisar detalle factual |
| `/en-vivo/oportunidades` | 200 | 3 | 0 | Datos o campos sin evidencia; revisar detalle factual |
| `/en-vivo/decisiones` | 200 | 282 | 30 | Datos o campos sin evidencia; revisar detalle factual |
| `/en-vivo/posiciones` | 200 | 2 | 0 | Datos o campos sin evidencia; revisar detalle factual |
| `/en-vivo/cierres` | 200 | 14 | 4 | Datos o campos sin evidencia; revisar detalle factual |
| `/en-vivo/capacidad` | 200 | 62 | 0 | OFF_BASELINE; capacidad OPEN no certificada |
| `/en-vivo/workers` | 200 | 106 | 4 | Datos o campos sin evidencia; revisar detalle factual |
| `/en-vivo/fuentes` | 200 | 181 | 26 | Datos o campos sin evidencia; revisar detalle factual |
| `/trading` | 200 | 213 | 0 | Datos o campos sin evidencia; revisar detalle factual |
| `/trading/equity-spot` | 200 | 248 | 17 | Datos o campos sin evidencia; revisar detalle factual |
| `/trading/scalping` | 200 | 198 | 17 | Datos o campos sin evidencia; revisar detalle factual |
| `/trading/renta-fija` | 200 | 232 | 0 | Datos o campos sin evidencia; revisar detalle factual |
| `/trading/derivados` | 200 | 236 | 7 | Datos o campos sin evidencia; revisar detalle factual |
| `/trading/cauciones` | 200 | 49 | 0 | Datos o campos sin evidencia; revisar detalle factual |
| `/trading/tesoreria` | 200 | 162 | 0 | Datos o campos sin evidencia; revisar detalle factual |
| `/universo` | 200 | 115 | 0 | Datos o campos sin evidencia; revisar detalle factual |
| `/universo/discovery` | 200 | 5 | 0 | Datos o campos sin evidencia; revisar detalle factual |
| `/universo/tradeability` | 200 | 5 | 0 | Datos o campos sin evidencia; revisar detalle factual |
| `/universo/coverage` | 200 | 62 | 0 | Datos o campos sin evidencia; revisar detalle factual |
| `/universo/excluidos` | 200 | 5 | 0 | Datos o campos sin evidencia; revisar detalle factual |
| `/universo/familias` | 200 | 242 | 0 | Datos o campos sin evidencia; revisar detalle factual |
| `/instrumentos` | 200 | 82 | 0 | Datos o campos sin evidencia; revisar detalle factual |
| `/instrumentos/ficha` | 200 | 82 | 0 | Sin identity muestra selector; con identity HTTP 200 |
| `/instrumentos/contrato` | 200 | 62 | 0 | Requiere identity exacta |
| `/instrumentos/mercado` | 200 | 6 | 0 | Requiere identity; cotización exacta STALE |
| `/instrumentos/evidencia` | 200 | 62 | 0 | Requiere identity exacta |
| `/riesgo` | 200 | 42 | 5 | Datos o campos sin evidencia; revisar detalle factual |
| `/riesgo/exposicion` | 200 | 3 | 0 | Datos o campos sin evidencia; revisar detalle factual |
| `/riesgo/limites` | 200 | 59 | 0 | Datos o campos sin evidencia; revisar detalle factual |
| `/riesgo/posiciones` | 200 | 3 | 0 | Datos o campos sin evidencia; revisar detalle factual |
| `/riesgo/liquidez` | 200 | 3 | 0 | Datos o campos sin evidencia; revisar detalle factual |
| `/riesgo/pnl` | 200 | 77 | 0 | Datos o campos sin evidencia; revisar detalle factual |
| `/riesgo/event-risk` | 200 | 6 | 0 | Datos o campos sin evidencia; revisar detalle factual |
| `/analitica` | 200 | 78 | 0 | Datos o campos sin evidencia; revisar detalle factual |
| `/analitica/senales` | 200 | 5 | 0 | Datos o campos sin evidencia; revisar detalle factual |
| `/analitica/salidas` | 200 | 102 | 20 | Datos o campos sin evidencia; revisar detalle factual |
| `/analitica/historico` | 200 | 162 | 20 | Datos o campos sin evidencia; revisar detalle factual |
| `/analitica/anual` | 200 | 142 | 0 | Selector sin enlaces: historia exacta no disponible |
| `/analitica/experimentos` | 200 | 5 | 0 | Datos o campos sin evidencia; revisar detalle factual |
| `/analitica/contrafactuales` | 200 | 10 | 0 | Datos o campos sin evidencia; revisar detalle factual |
| `/analitica/cierre-diario` | 200 | 30 | 0 | Datos o campos sin evidencia; revisar detalle factual |
| `/analitica/reportes` | 200 | 132 | 0 | Datos o campos sin evidencia; revisar detalle factual |
| `/sistema` | 200 | 107 | 4 | Datos o campos sin evidencia; revisar detalle factual |
| `/sistema/salud` | 200 | 106 | 4 | Datos o campos sin evidencia; revisar detalle factual |
| `/sistema/workers` | 200 | 146 | 4 | SHADOW 30 s vs presupuesto 120 s |
| `/sistema/integraciones` | 200 | 157 | 4 | Datos o campos sin evidencia; revisar detalle factual |
| `/sistema/scheduler` | 200 | 272 | 0 | Snapshot 32 timers; adapter pierde nombres/reloj |
| `/sistema/evidencia` | 200 | 6 | 0 | Corte coherente; cobertura omitida en reporte durable |
| `/sistema/backups` | 200 | 4 | 0 | backup_status.json no publicado |
| `/sistema/logs` | 200 | 102 | 0 | Datos o campos sin evidencia; revisar detalle factual |
| `/sistema/configuracion` | 200 | 87 | 0 | Datos o campos sin evidencia; revisar detalle factual |

## Gates pendientes (NO_GO hasta evidencia)

1. Integrar una sola SHA/tree de PR #681, Predeploy V2 exacto sin FAIL/ERROR/SKIP y artefacto inmutable.
2. Verificar HEAD/ownership #471/#473, adquirir DEPLOY_OWNER exclusivo, someter plan de los 31 archivos a revalidación de inodo y FD. Deshabilitar todos los productores antes de aplicar unlink. Ninguna base viva ni evidencia fallida entra al plan.
3. Admitir Deploy V2 con fórmula real de espacio/imagen, sin rebajar reserva para forzar GREEN. Comprobar policy masked, PAPER/0, PPI Watch intocado, 52 vistas, SHADOW liveness/cobertura y siguiente rueda real. Si la capacidad no alcanza después del borrado, documentar NO_GO sin promoción ni rollback.

## Revalidación intradiaria 21/10 y ruta de promoción

- 15:02:31 UTC, run [39622070687](https://github.com/mbalbo2023/Porota-trading/actions/runs/39622070687): artefactos PAPER 3.749.426.571 B/206 y SHADOW dinámico 272.280.410 B/29; un paquete de auditoría cold de 223.325.880 B estaba activo. A 15:06:13 UTC, run [39622600759](https://github.com/mbalbo2023/Porota-trading/actions/runs/39622600759), cola COMPLETE, SHADOW dinámico 42.590.403 B/26 y libre +221.945.856 B. Es una oscilación transitoria compatible con drenaje de paquete, no una prueba de crecimiento exponencial ni de backup redundante.
- 15:10:58 UTC, run [39623246924](https://github.com/mbalbo2023/Porota-trading/actions/runs/39623246924): observer `PRODUCTION_PAPER`, órdenes reales 0, `RUNNING/MARKET_OPEN`, spots OPEN 0, cauciones OPEN 1, futuros ACTIVE 0. No aplicar el contrato de carryover de 20/10 de dos spots y una caución a esta rueda.
- RCA Deploy V2: la ventana estaba fijada al 20/10 y `drain/install` exigían `--paused-postclose` con libro distinto al observado. El candidato cambia a la ventana del 21/10 de 17:00 a 22:59 ART y ruta CLOSED estándar: el admisor exige sesión cerrada, heartbeat fresco y libro PAPER 0/0/0 antes del stop, revalida tras el stop y antes de instalar; tras el arranque exige `WAITING_MARKET`, sesión cerrada, heartbeat fresco y libro flat. No hay carryover, ventas ni cancelación administrativa. Si la caución sigue OPEN al cierre, este camino es NO_GO y preserva el runtime.
- La ruta nueva necesita una **nueva** Predeploy V2 para su SHA/tree exactos, evidencia de libro flat posterior a 17 ART, gate de espacio exacto y decisiones exactas del owner en #624. El éxito parcial de una ejecución previa no certifica este cambio. Ninguna eliminación ni deshabilitación de backups se acredita hasta recibo de host del Deploy V2.
