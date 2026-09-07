# POROTA TRADING — CHECKPOINT DASHBOARD CLASSIC TABLES RC6 POSTDEPLOY — 2026-09-07

## Estado ejecutivo

**P1 DASHBOARD CLASSIC TABLES: DEPLOYED_GREEN / FIELD_ACCEPTANCE_PENDING**

Corrección exclusivamente de presentación del dashboard RC6 desplegada transaccionalmente antes del preopen del 07-Sep-2026.

No se modificaron observer, estrategia, gates, órdenes, históricos, DB writers, calendarios, política CEDEAR US Labor Day, PPI guard ni capacidad de ejecución real.

## Identidad desplegada

- Branch: `ux/rc6-dashboard-classic-tables-20260907`
- Dashboard SHA desplegado: `87abde19f38ce7b70f553373acd1088db2a4293c`
- Imagen: `porota-trading-dashboard:17.0.0-rc6-classic-tables`
- Política de tablas: `CLASSIC_ROWS_COLUMNS`

## Observer congelado

Postflight confirmó:

- Observer SHA: `db26c76723bb988c956589c572b87cbcb4191731`
- Branch: `hotfix/rc6-cedear-us-labor-day-20260906`
- Imagen: `porota-trading-bot:17.0.0-rc6`
- Observer restarted: `NO`
- Strategy changed: `NO`
- Runtime: `ok|ok|PRODUCTION_PAPER|0`
- Real order capability: `BLOCKED`
- `real_orders_sent=0`

## GitHub Actions

### Intento 1

Run: `34121259063`

- Validación previa: GREEN.
- Build candidato: GREEN.
- Tests de presentación: GREEN.
- Contrato `CLASSIC_ROWS_COLUMNS`: GREEN.
- Preflight Droplet: GREEN.
- Runtime: `ok|ok|PRODUCTION_PAPER|0`.
- Baseline HTTP matrix: GREEN.
- Build remoto del candidato: GREEN.
- Introspection age: aproximadamente `397 s`.
- Fallo: una aserción del direct proof exigía el texto literal `Snapshot de introspección vigente` incluso cuando la introspección aún estaba dentro del umbral legacy de frescura y el renderer no necesitaba insertar ese rótulo.

El fallo ocurrió **antes de `ACTIVATED=1`**.

Consecuencia:

- el dashboard live no fue reemplazado;
- no hubo observer restart;
- no hubo strategy change;
- no fue necesario ejecutar fallback;
- el defecto estaba en la prueba de deployment, no en el parche gráfico.

### Corrección del direct proof

Commit:

`87abde19f38ce7b70f553373acd1088db2a4293c`

Regla corregida:

- siempre exigir que no aparezca `Snapshot de introspección no vigente` cuando la evidencia está dentro de la guardia de 75 minutos;
- exigir el texto explícito `Snapshot de introspección vigente` sólo cuando la edad supera el umbral legacy de 600 segundos y la capa RC6 debe corregir el warning legacy.

No se modificó ninguna línea de la lógica visual del parche classic tables en esta corrección.

### Intento final

Run: `34121741142`

Resultado: **SUCCESS**.

Validación:

- Build RC6 classic dashboard candidate: GREEN.
- RC6 dashboard presentation tests: GREEN.
- Classic rows and columns contract: GREEN.
- Job validate: SUCCESS.

Deployment:

- strict SSH: GREEN.
- preflight dashboard actual exacto: GREEN.
- preflight observer exacto: GREEN.
- SQLite quick_check observer/history: `ok|ok`.
- `PRODUCTION_PAPER` confirmado.
- `real_orders_sent=0` confirmado.
- baseline HTTP matrix: GREEN.
- build remoto: GREEN.
- direct proof: GREEN.
- activation: GREEN.
- post HTTP matrix: GREEN.
- post runtime check: GREEN.
- deploy job: SUCCESS.

## Evidencia de observabilidad durante postflight

### Introspection

- Age durante direct proof: aproximadamente `741 s`.
- `INTROSPECTION_RENDER_PROOF=GREEN`.
- La capa RC6 manejó correctamente la diferencia entre el umbral legacy y la guardia horaria de 75 minutos.

### Telegram

Estado durante direct proof:

- `VERDE`.
- Notification worker: `RUNNING`.
- Heartbeat: aproximadamente `0 s`.
- Outbox `SENT=83`.
- Outbox `FAILED=0`.

### SRE

Estado persistido durante direct proof:

- `AMARILLO`.
- `quick_check=ok`.
- disco libre: `33.2%`.
- medición SRE: aproximadamente `39363.9 ms`.

Clasificación:

**AMARILLO no bloqueante causado por latencia de la medición SRE; no por corrupción DB ni falta de disco.**

Este punto continúa como RCA/P1 de observabilidad y no invalida por sí solo PAPER cuando integridad y capacidad están sanas.

## Matriz HTTP postdeploy

Todas las rutas verificadas respondieron correctamente después de activar el nuevo dashboard:

- `/`
- `/vivo`
- `/validacion`
- `/historicos`
- `/reportes`
- `/config`
- `/sistema?section=introspeccion`
- `/salud`
- `/universo-operativo`
- `/scalping`
- `/instrumentos`
- `/aprendizaje`
- `/dashboard/logs`

Resultado:

`POST_HTTP_MATRIX=GREEN`

En `/vivo` y `/validacion` se verificó además:

- presencia de la capa `porota-rc6-table-a11y-classic`;
- presencia de `porotaInitClassicTables`;
- ausencia de `data-porota-compact`.

## Contrato UX desplegado

Regla dura:

**Una tabla permanece una tabla en todos los viewports.**

No existe conversión automática a cards/stacked records por:

- cantidad de columnas;
- ancho disponible;
- ancho por columna;
- overflow.

Tablas anchas:

- conservan `<table>/<thead>/<tbody>/<tr>/<th>/<td>`;
- usan scroll horizontal local dentro de su propio contenedor;
- no provocan intencionalmente scroll horizontal global de la página;
- mantienen encabezados reales y `scope="col"`;
- conservan paginación progresiva `Mostrar más` / `Mostrar menos`;
- reducen densidad visual en tablet/móvil sin transformar registros en cards.

## Rollback

Después de postflight GREEN:

`OLD_DASH_TAG_REMOVED=YES`

La imagen anterior `porota-trading-dashboard:17.0.0-rc6-go-live-final` dejó de conservarse como mecanismo permanente de rollback.

Rollback canónico de aplicación:

- exact GitHub SHA;
- GitHub Actions / artefacto verificable;
- no dependencia intencional de una imagen Docker app vieja local.

SHA anterior conocido para reconstrucción si fuera necesario:

`da2c87936d90cda17512de3bc529d13f4693c1c1`

## Aceptación Samsung / Voice Access

Pendiente field test real P1:

1. confirmar tablas de filas/columnas en Samsung Android;
2. confirmar encabezados legibles;
3. confirmar que Voice Access puede identificar/navegar controles;
4. confirmar que tablas anchas desplazan sólo su contenedor;
5. confirmar ausencia de scroll horizontal global de página;
6. confirmar `Mostrar más` / `Mostrar menos`;
7. confirmar ausencia visual de cards/stacked records;
8. revisar especialmente `/vivo`, `/validacion`, `/universo-operativo`, `/salud` e `/instrumentos`.

Esta aceptación de dispositivo continúa clasificada P1 UX/accesibilidad y no es P0 por sí sola, salvo pérdida real de observabilidad crítica.

## Semáforo final

- Código classic tables: 🟢 GREEN
- CI/tests: 🟢 GREEN
- Deployment: 🟢 GREEN
- Postflight HTTP: 🟢 GREEN
- Classic rows/columns runtime contract: 🟢 GREEN
- Observer identity/integrity: 🟢 GREEN
- `real_orders_sent=0`: 🟢 GREEN
- Real order capability: ⚪ BLOCKED
- Telegram runtime evidence: 🟢 GREEN
- SRE integrity/disk: 🟢 GREEN
- SRE measurement latency: 🟡 AMARILLO no bloqueante / RCA pendiente
- Samsung/Voice Access field acceptance: 🟡 PENDING P1
- Production real money: ⚪ NO-GO / BLOCKED

## Veredicto de este checkpoint

**DASHBOARD CLASSIC TABLES = DEPLOYED_GREEN.**

El parche gráfico quedó desplegado de manera aislada y transaccional, con observer y estrategia sin cambios y órdenes reales bloqueadas. El siguiente paso específico de esta corrección es la aceptación visual/funcional en Samsung Android; en paralelo, la prioridad P0 vuelve al preopen operativo, calendarios y política CEDEAR del 07-Sep-2026.
