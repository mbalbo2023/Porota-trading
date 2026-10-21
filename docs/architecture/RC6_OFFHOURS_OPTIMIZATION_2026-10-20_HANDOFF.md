# HANDOFF EJECUTABLE — WS-RC6-OFFHOURS-OPTIMIZATION

**Fecha de diseño:** 20/10/2026 ART. **Autoridad:** #679, arquitectura ADR, inventario y delivery plan adyacentes. **Estado de este handoff:** EN_GITHUB / DISEÑO PROPUESTO; **NO es código implementado, NO es Deploy**.

## 1. Fuente de trabajo para el siguiente agente

Repositorio https://github.com/mbalbo2023/Porota-trading

- Issue maestro de optimización: https://github.com/mbalbo2023/Porota-trading/issues/679
- Relación con incidente mayor SHADOW: https://github.com/mbalbo2023/Porota-trading/issues/624
- Ownership y coordinación: https://github.com/mbalbo2023/Porota-trading/issues/471 y https://github.com/mbalbo2023/Porota-trading/issues/473
- Productiva protegida: deploy/rc6-pr69-isolated-20260915
- HEAD de referencia 20/10: bc661130968f2b85b8b2146f76923ddd26eb7837; tree 1d7e9fad7e1af792f15a39a6cc68dcd2ecc8f33b. **Revalidar contra HEAD al ejecutar.**
- Predeploy último validado al diseñar: run 39516481351, SHA f525ed707d540ad3b4b75eecf95c503bfd4b230c.
- Deploy V2 último validado: run 39519161504.
- Auditoría independiente último runtime: run 39521189732 (00:25:14 UTC del 21/10): 3 contenedores running, 0 OOM/restarts, 20 timers activos, CLOSED, real_orders_sent=0, exits 2 OPEN con EXIT_PENDING_MARKET_CLOSED/EOD_PAPER, una caución abierta, 4,942,311,424 bytes libres, 1,017,540,608 MemAvailable. Evidencia posterior puede variar; no operar por supuesto.
- Prueba host stress 405min: fue explícitamente DEFERRED, no asumida realizada.

## 2. Lectura secuencial obligatoria

1. ADR: RC6_OFFHOURS_OPTIMIZATION_2026-10-20_ADR.md.
2. Inventario exacto: RC6_OFFHOURS_OPTIMIZATION_2026-10-20_INVENTORY.md.
3. Paquetes y prueba: RC6_OFFHOURS_OPTIMIZATION_2026-10-20_DELIVERY.md.
4. Código productivo indicado y toda política/guard de repo.
5. Comentarios #679 y #624, y HEAD/ownership reales. No se fía del chat ni de snapshots antiguos.

## 3. Misión para implementador

**Modo de arranque: READ_ONLY.** Crear un checkpoint breve en #679 con head/tree actuales, estado #471/#473, objetivo de la primera sonda, whitelist de paths, fecha de rueda y seguridad PAPER. Verificar que cualquier otro workstream concurrente no tenga el mismo WRITE scope. No tocar host en OPEN salvo lecturas pasivas de presupuesto conocido.

Entregar una implementación consolidada de la ADR con mínimo cambio:
- Seguridad y salida CLOCK siempre funcionando; no depende de SHADOW.
- Shadow CLOSED_IDLE sin full compute repetido, sin fake source freshness, cold replay intacto, PREOPEN rewarm completo y OPEN <=120s.
- Candle materializer sin transacciones vacías 2s CLOSED, sin pérdida de 1m/5m ni falso FULL_OHLCV.
- Históricos bootstrap/gap/corrección; eliminar consultas duplicadas PPI/A3 justificadamente; local EOD retoma después de cierre sin quitar guard de seguridad.
- Timers adaptados a calendarios y carga; auditores deduplicados sólo tras preservar semántica; backups sin catchup OPEN; alertas MAX_HOLD contextuales sin ocultar riesgo.
- Observabilidad nativa por proceso/servicio, I/O, WAL, shadow files y quotas recursivas, retention por reachability con FAILED preservados.

**Nunca implementar a partir de un número o diagnóstico inventado.** Primero baseline real y explicación de retorno por tarea.

## 4. Controles excluyentes

- PAPER/SHADOW ONLY, real_orders_sent entero 0, real routes NOT_CALLED.
- PPI Watch NO TOCAR; no acceder/modificar credenciales, unit o ruta. Fuente primaria PPI identidad; complementos IOL/BYMA/A3 no sobrescriben.
- No nuevas órdenes reales, no "paper fill" sin BOOK quote fresco, no cerrar dos posiciones a la fuerza, no maturar cauciones con fecha falsa.
- FIX-FORWARD únicamente, NO rollback.
- WRITE_OWNER exclusivo por scope documentado y branch aislada; no escribir directamente productiva. DEPLOY_OWNER exclusivo global antes de tocar Droplet. Mutex rc6-unified-paper-deploy con cancel-in-progress=false.
- GitHub Actions normal; SSH manual sólo break-glass; no Codespaces operativo normal. No borrar ramas viejas, cabezas PR abiertas, evidencia única, backups ni archivos FAILED.
- No nuevo agente/daemon/timer o capa compleja por defecto. Evitar reemplazar competencia de SQLite por competencia de otro proceso.

## 5. Secuencia de ejecución autónoma, con parada segura

- FASE R0: lectura/revalidación GitHub+runtime read-only, auditoría del 14–20/10 ART, límites de métricas históricas, current head, ownership, calendario y recursos.
- FASE R1: RCA y contratos; test FAIL-before reproducible para cada hallazgo; plan de paths y PR(s) aislados.
- FASE R2: desarrollo de frentes independientes con scopes pareados; integrador único resuelve shared files; CI y A/B offhost, SAT+negative tests sin omisiones no gobernadas.
- FASE R3: congelar único candidato SHA/tree, Predeploy V2 real build once, artifact immutable digest, full suite y native.
- FASE R4: DEPLOY_OWNER exclusivo y Deploy V2 canónico en ventana segura, preflight estrictamente bloqueante de PAPER, live resources, observers/EOD/caución, disk/RAM, no PPI Watch; sin reconstruir Docker en host.
- FASE R5: validación runtime directa e independiente: image digest/restarts, 2 native generations + strict10, todos servicios/timers, WAL/DB/seguridad, min resources, Telegram y SHADOW idle-then-OPEN, datos/candles actualizados.
- FASE R6: observar rueda OPEN real de fecha posterior, demostrar no degradación y resolución natural PAPER de posiciones EOD con BOOK válido. Ejecutar estrés integral de 405 minutos dentro del Droplet sólo si autorizado en ventana segura y admisión aprobada; si no, **NO_VERIFICADO/NO_GO de cierre integral**, nunca marcar GREEN.
- FASE R7: actualizar archivos canónicos y registro de errores/lecciones con evidencia, liberar ownership #471/#473, informar dictamen verificable. Ante NO_GO preservar artefactos, PR, logs/manifest y evidencia; no lanzar bucle de nuevos reintentos ni rollback.

## 6. Cómo reportar avances de forma verificable

Formato final obligatorio: Estado PROPUESTO/DESARROLLADO/EN_GITHUB/ARTEFACTO_VALIDADO/DESPLEGADO/VALIDADO_RUNTIME/NO_VERIFICADO/BLOQUEADO, source SHA/tree, branch/PR, Predeploy run/job, tests/casos y cero FAIL/SKIP, artifact SHA256 OCI y manifest, Deploy run y runtime image ID, SQLite/WAL/CPU/IO tabla de comparación por fase, EOD+CAUCION, 20 timers/units, real_orders=0, PPI Watch untouched, errores RCA/fix guard, cleanup, host stress 405 con status real, owners liberados.

## 7. Mensaje breve para activar al implementador

Actuá como arquitecto e integrador único de POROTA RC6. Empezá READ_ONLY. Leé íntegros los cuatro documentos RC6_OFFHOURS_OPTIMIZATION_2026-10-20_* en docs/architecture/ y el issue #679, vinculados a #624. Revalidá HEAD real, Actions, Droplet y ownership #471/#473, y adquirí WRITE_OWNER exclusivo por paths antes de programar. Implementá toda la arquitectura de optimización de workers, SHADOW fuera de rueda, velas, históricos, SQLite/WAL, timers y recursos con RCA+FIX+GUARD+regresiones, sin pérdida funcional. Consolida un solo candidato en branch+PR, certificá Predeploy V2 y promovelo por Deploy V2 únicamente con DEPLOY_OWNER legítimo y todos los gates. Validá runtime PAPER/0, dos generaciones SHADOW+strict10 y primera rueda real; ejecutá la prueba host 405min sólo con admisión/ventana autorizada. NO órdenes reales, NO PPI Watch, NO rollback, NO atajos. Si un bloqueo impide GREEN, documentá NO_GO exacto y conservá evidencias. No declares implementado o validado lo que no probaste.
