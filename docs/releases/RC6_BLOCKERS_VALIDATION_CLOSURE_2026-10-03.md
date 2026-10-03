# RC6 — cierre de validación del release #449

Fecha: 2026-10-03. Última observación directa del runtime: 2026-10-03T15:16:36.701864+00:00 (12:16:36 ART).
Workstream: WS-RC6-PAPER-BLOCKERS-INTEGRATION-20261003.
Tracker de continuidad y ownership: #446. La liberación final se registra allí después de publicar este cierre.

## Estado y alcance

Release #449: **VALIDADO_RUNTIME** para la identidad exacta indicada abajo. No significa rentabilidad validada, todas las identidades READY ni ejecución comprobada de Scalping/cauciones en rueda. Es el cierre técnico del mismo release, no otro despliegue.

Esta revisión recuperó los jobs ya terminados, leyó los logs directos, descargó y verificó el ZIP de evidencia, y cerró metadatos/documentación. No repitió Actions ni mutaciones del host. Los datos detallados y logs permanecen en artifacts; este archivo contiene referencias y métricas agregadas, no DB, secretos, cuentas ni operaciones.

## Identidad verificable

- Repositorio: mbalbo2023/Porota-trading.
- Productiva: deploy/rc6-pr69-isolated-20260915.
- Product SHA: da697c6e6c2274579f9e4a112fabc4327475dd35.
- Candidato: 9ca8cb13bfc6664597e313e5d1874710e3263ec4.
- Tree común: 96a112a55ed779df3f7056b1e30d81aac8d0b791.
- Imagen: sha256:ec7fe885b47975ef9e66428460ce73deb331948c140961bf6a03e0708b5c9718.
- Inputs absorbidos: #447@251fac4cf73981a4fd3feb123c97a37bf10ad02d y #448@3e7819963f65de443421152f0f5614ef1356fb41.
- GitHub confirmó HEAD productivo y merge de dos padres. El segundo padre es el candidato exacto.

## Cadena de evidencia

| Etapa | Identificador | Resultado observado |
|---|---|---|
| Predeploy V2 integrado | 37125173991 | SUCCESS |
| Artifact congelado de producto | 11275685221 | Mismo artifact usado en la promoción y continuación |
| Digest del artifact de producto | sha256:405cf74f6a21f06d32fa66993b21536786d851b6a49c83e8fc965be35115f9cc | Fijado en CURRENT_STATE_V2 final |
| Deploy V2 original | 37125515526 | FAILED después de promoción; no se oculta ni se declara SUCCESS |
| Continuación de validación | 37131712187 | prepare y finalize SUCCESS |
| Source de continuación | d4df82804c48b13e5dd46a03f951a99d496385e5 | Rama ops/rc6-deploy-validation-recovery-20261003 |
| Job final directo | 111228035345 | SUCCESS; contiene preflight, auditorías, cleanup y lectura final |
| Artifact de evidencia runtime | 11277627152 | ZIP descargado y hash recalculado |
| SHA256 del ZIP runtime | a4bf46ea04860448e349483d8fe96f870efacb10cf376725dd42e63af032c99d | Coincide con Actions |

El ZIP contiene preflight-runtime.json, final-runtime.json, resume-runtime.log y resume-config.json. Se comprobaron programáticamente la identidad del producto/candidato/artifact/run, VALIDATED_RUNTIME, igualdad de imagen en los tres contenedores, ausencia de OOM/restarts, suma de READY por familia, PPI Watch invariante y marcas de las verificaciones demoradas. No se reconstruyó el artifact para hacer esta comprobación.

## Runtime observado

- CURRENT_STATE_V2.validation_status: VALIDATED_RUNTIME.
- Modo: PRODUCTION_PAPER; real_orders_sent=0; real_order_routes=NOT_CALLED.
- Dashboard y critical_approval: running / healthy. Observer: running; no healthcheck Docker declarado, supervisores con heartbeat comprobado en las auditorías.
- Los tres contenedores usan la imagen exacta indicada. OOM=false y restart_count=0 en lectura final.
- 423 archivos instalados comprobados; hash de baseline igual antes/después de la continuación.
- 16 de 16 endpoints auditados respondieron HTTP 200. Matriz de rutas coincide con el artifact. Esto no equivale a una auditoría visual exhaustiva nueva de todo el dashboard.
- Auditoría de continuación inmediata, tres ciclos de estabilidad y cinco muestras de soak completadas. No se observaron firmas críticas en el intervalo de logs revisado.
- PPI Watch intacto; listas de unidades invariantes.
- Preopen T_MINUS_45 y T_MINUS_10: NOT_DUE / BYMA_NON_OPERATIONAL_DAY; calendario no operativo. Se aceptó el resultado técnico, con readiness_verified=false y execution_authorized=false. No se autorizó operar el sábado.
- Scalping: WAITING_MARKET. Cash-sweep: WAITING_CALENDAR. Exit supervisor: RUNNING. Rutas de órdenes vacías en los tres.

## Readiness canónica por identidad

Fuente: candidate_identity_v2 (status AVAILABLE + can_simulate=1), concordante con el dashboard en las muestras de estabilidad.

| Familia | READY observados |
|---|---:|
| ACCIONES | 162 |
| CEDEARS | 1.237 |
| BONOS | 2.342 |
| LETRAS | 37 |
| OBLIGACIONES | 3.560 |
| FCI | 1.060 |
| OPCIONES | 1.307 |
| CAUCIONES | 5 |
| FUTUROS | 0 |
| INDICES | 0 |
| ON legacy | 0 |
| TOTAL | 9.710 |

Frente al snapshot anterior de 8.082, la diferencia observada es +1.628. No es una atribución causal aislada del código: cambió también el universo observado (14.006 en el censo anterior; 14.294 en la auditoría actual). No sustituye un replay de cohortes congeladas. READY no significa que se haya abierto una operación; cotización, book, frescura, sesión y riesgo siguen siendo gates independientes. Las identidades no READY no se fuerzan.

## Cleanup verificado

La continuación alineó critical_approval con la imagen promovida y sólo después eliminó la antigua imagen sin referencias. Observer/dashboard no fueron recreados por este resume. Aplicó la política existente de temporales/cachés/journal/backups, preservando los backups retenidos y datos activos.

La cola canónica reportó SPACE_RECOVERED=3141332992 bytes (incluye eliminación de imagen y housekeeping). Housekeeping por sí solo reportó 954540032 bytes. La lectura independiente final observó 8577789952 bytes libres, aproximadamente 7,99 GiB. No se suman ambas cifras de recuperación porque se solapan.

## ERROR → RCA → FIX → GUARD → TEST → EVIDENCIA

**Error principal:** el wrapper de Deploy V2 buscaba literalmente status GREEN, aunque rc6_preopen.py había terminado rc=0 con NOT_DUE el sábado. El fallo ocurrió después de promoción, no antes.

**Fix aplicado al cierre:** continuación parametrizable y protegida por el mutex rc6-unified-paper-deploy / cancel-in-progress=false. Verifica fallo original, SHA/tree/artifact/digest/ownership y runtime antes de ejecutar sólo la cola pendiente; no repite docker load, promoción ni pasada contractual.

**Defensa permanente:** PR #450, head 0311dcc10139777f8be988f4fe8c3efabc86d1a9, Predeploy V2 37131980746 SUCCESS. El parser estricto exige esquema, calendario, timestamp, retorno y flags correctos; NOT_DUE no se convierte en GREEN ni concede autorización. Conserva diagnósticos y comprueba el wiring del wrapper.

**Estado de la defensa:** EN_GITHUB, PR abierto; todavía NO integrada en la rama productiva. El incidente operativo está recuperado, pero su prevención permanente queda pendiente del siguiente candidato coherente. No marcar ese pendiente como desplegado.

**Errores auxiliares preservados:** staging del workflow rechazado con 403 desde GITHUB_TOKEN; se separó la preparación probada de su publicación mediante conector autorizado. La continuación también incorporó validación exacta de los mounts congelados en su preflight. Estos cambios son de tooling aislado, no del motor.

## Cierre de inputs y pendientes

#447 y #448 se cerraron como absorbidos por #449, sin merge individual y sin borrar ramas/commits/evidencia. #450 permanece abierto como guard para el próximo batch. #446 permanece abierto para no ocultar residuales funcionales; su release técnico está completado.

Pendientes explícitos:
1. Reconciliar e integrar #450 en el próximo candidato único, sin redeploy sólo para maquillar el run original fallido.
2. Revalidar residuales por identidad sobre un nuevo censo acotado; futuros continúan en cero READY. No extrapolar del censo anterior un conteo actual de causas.
3. Comprobar Scalping y caución PAPER con sesión válida y evidencia fresca; NO_VERIFICADO todavía. No simular fills para completar una validación.
4. Conservar la auditoría de performance de las últimas 20 ruedas como frente separado: este release no corrige ni demuestra una mejora de stops/EOD/P&L.
5. La clasificación de units legacy y las protecciones administrativas no se revalidaron aquí; siguen NO_VERIFICADO. No borrar fallos ni reactivar GDELT/BCRA.
6. Mantener los estados IOL acotados a su source-path. El timestamp mostrado por el snapshot no se presenta como una nueva lectura live del sábado.

Para cualquier nueva escritura o despliegue: consultar #446 y HEAD real nuevamente, adquirir el ownership correspondiente, conservar PAPER/SHADOW ONLY, real_orders_sent=0, FIX-FORWARD ONLY y PPI Watch UNTOUCHED.
