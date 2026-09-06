# POROTA TRADING — CHECKPOINT CANÓNICO RC6 POST-HOTFIX

**Corte:** 2026-09-06 19:30 AR  
**Timezone operacional:** America/Argentina/Buenos_Aires  
**Release:** `17.0.0-rc6`  
**Branch live congelada:** `hotfix/rc6-cedear-us-labor-day-20260906`  
**SHA live exacto desplegado:** `db26c76723bb988c956589c572b87cbcb4191731`  
**Modo:** `PRODUCTION_PAPER`  
**Ejecución:** `SIMULATED`  
**Real money:** `BLOCKED`  
**Checkpoint/docs branch:** `checkpoint/rc6-20260906-posthotfix`

> Este documento continúa y reemplaza para continuidad operativa al checkpoint RC6 postdeploy original del 2026-09-06. La branch live se mantiene congelada en el SHA desplegado `db26c...`; la documentación/checkpoint vive en una branch separada para no mover la identidad live.

## 1. Estado live después del hotfix

El blocker de calendario USA para CEDEARs quedó corregido y desplegado.

Regla validada para 2026-09-07:
- todos los CEDEARs con subyacente USA: nueva apertura `HOLD — UNDERLYING_MARKET_CLOSED: US_LABOR_DAY`;
- no limitar la regla a AAPL/AAPLD/AAPLC;
- acciones argentinas como GGAL no quedan bloqueadas por Labor Day USA si el mercado local opera;
- cotizaciones CEDEAR continúan observándose/registrándose;
- la sesión no debe tratarse como cohorte CEDEAR normal para aprendizaje.

La corrección pasó:
- compilación;
- tests específicos de feriado USA;
- suite activa RC6 offline;
- invariantes de seguridad;
- build/publicación inmutable;
- deploy transaccional;
- postdeploy DB/health/orders.

Runtime probado postdeploy:
- observer running, restart=0, readonly=true;
- dashboard running, restart=0;
- DB observer quick_check=ok;
- DB history quick_check=ok;
- PPI auth OK/read-only;
- `real_orders_sent=0`;
- real-order capability bloqueada.

### Política canónica de recuperación de aplicación

**NO se mantiene rollback de aplicación en el disco del Droplet.**

- la identidad recuperable es el commit/SHA exacto en GitHub;
- GitHub Actions es el mecanismo preferido de redeploy/recovery;
- se puede reutilizar una imagen/artefacto inmutable de registry si existe y está verificado;
- si no existe, se reconstruye desde el commit exacto mediante el pipeline gobernado;
- imágenes Docker antiguas no quedan protegidas por el solo hecho de ser “rollback”; si no están referenciadas por runtime activo ni constituyen evidencia única indispensable, son candidatas a limpieza.

Esta regla prevalece sobre cualquier texto anterior que indicara conservar una imagen local pre-hotfix o de rollback.

## 2. Auditoría de ingesta/históricos/aprendizaje — resultado

### PPI live / snapshots — GREEN
- datos reales read-only;
- snapshots y símbolos existentes;
- último dato de mercado consistente con último día hábil;
- hot path independiente de A3/Contract Evidence.

### Candles — GREEN
- worker activo;
- cursor de snapshots avanzado;
- candle integrity timer activo;
- no fabricar candles en domingo/feriado sin ticks.

### Store histórico canónico — GREEN de integridad
- DB sana;
- versionado/provenance presentes;
- fuentes PPI Production History + Data912 separadas;
- Data912 actúa como fallback/legacy, no como fuente dinámica primaria.

### PPI historical — P1 PARCIAL
Funciona y avanza, pero no está completo.

Pendientes:
- continuar backfill bounded;
- investigar concentración de `HIGH_NONPOSITIVE`;
- investigar `OHLC_INCONSISTENT`;
- clasificar errores JSON/transitorios por instrumento;
- preservar validadores; no aceptar datos malos sólo para aumentar cobertura;
- demostrar progreso por snapshots antes/después.

### A3 Historical — P1 REAL
La conexión y el servicio funcionan, pero identity alignment no.

Primera ejecución controlada `WEEKEND_DEEP`:
- selected=40;
- failures de transporte=0;
- `ALIGNMENT_UNVERIFIED`=40;
- filas A3 incorporadas=0.

Causa observada: divergencia de identidad, por ejemplo PPI `DLR/SEP26` vs A3 `DLR092026`.

Pendiente:
- mapping canónico producto+vencimiento+familia/mercado;
- provenance del símbolo original A3;
- fail-closed para ambigüedad;
- tests;
- repetir ingesta controlada después de corregir.

No bloquea PAPER del lunes porque A3 es background/read-only y no veta el hot path PPI.

### Contract Evidence / scraping — scheduler GREEN, primer DUE pendiente
- RC6 native overlay/timer presente;
- fuera de ventana devuelve `GREEN_NOT_DUE`;
- no browser/auth artificial fuera de política;
- cero órdenes;
- primera ejecución RC6 realmente DUE debe verificarse mañana cuando corresponda.

### Aprendizaje SHADOW — GREEN de arquitectura
- IA intradía OFF;
- collector read-only;
- no autorización real-money;
- no autopromoción;
- gates económicos/expectancy/régimen/concentración siguen SHADOW/observación;
- learning samples y contrafactual deben seguir acumulándose durante la campaña PAPER.

## 3. Pendientes obligatorios para mañana

### Preopen 10:15–10:30
- verificar SHA live exacto `db26c...`;
- imagen/containers/restarts/readonly;
- health;
- DB quick_check;
- disk;
- timers;
- PPI auth;
- `real_orders_sent=0`;
- calendar local/USA;
- política CEDEAR;
- verificar que fines de semana/feriados locales no permitan operar;
- confirmar que background ingestion sí pueda seguir fuera de rueda;
- verificar dashboard snapshot/logs inconsistentes;
- field test esencial Samsung/Voice Access.

### Durante rueda 10:30–17:00
- PAPER only;
- no cambios de código/config;
- observar quotes/freshness;
- decisiones/gates SHADOW;
- fills PAPER/positions/PnL/marks;
- CEDEAR USA bloqueado sólo para nuevas aperturas;
- candles/history/backfill sin bloquear hot path;
- Contract Evidence primera ejecución DUE si corresponde;
- introspección/early-warning;
- evidencia horaria a `/validacion`.

### Cierre
- reconciliar PAPER;
- confirmar `real_orders_sent=0`;
- comprobar avance postclose de históricos;
- candle integrity;
- errores/retries PPI historical;
- aprendizaje SHADOW;
- actualizar `/validacion`.

## 4. Pendientes visuales/funcionales aportados 2026-09-06

Quedan incorporados al backlog y deben auditarse/implementarse sin mezclar con el hot path durante rueda:

- generar archivo claro de trabajos mañana/semana: realizado como `POROTA_TRADING_PENDIENTES_GO_LIVE_Y_SEMANA_2026-09-06.md`;
- definir prueba continua de que históricos/scraping/resto de instrumentos realmente avanzan;
- limpieza de disco basada en inventario/dbstat, nunca a ciegas;
- panel ejecutivo: no mostrar operaciones de sábados/domingos/feriados; máximo cinco operaciones;
- auditar misma regla en motor de trading, manteniendo ingesta background;
- Caja y patrimonio por moneda → tabla;
- En vivo: retirar workers/contador técnico;
- Histórico trading → tabla;
- retirar de vista operativa histórica `universo por moneda y familia` si no suma;
- retirar duplicación de `caja y patrimonio por moneda` en histórico si no suma;
- Universo operativo/matriz por familia → tabla;
- Scalping/contrato intradiario → tabla;
- `/validacion`: definir criterios/evidencias concretas para completar M0–M11; M0 debe capitalizar evidencia ya existente donde corresponda;
- Instrumentos y contratos → tabla;
- Histórico: eliminar `base objetiva` de vista de operador;
- Histórico: retirar `cobertura` de la vista operativa, sin borrar la métrica técnica necesaria para auditoría;
- Aprendizaje/cobertura empírica → tabla;
- Sistema/introspección drill-down: el menú izquierdo no debe desaparecer;
- Sistema/snapshot `no disponible`: investigar/corregir o explicar fault real;
- Salud APIs → tabla;
- Jobs internos → tabla;
- Scraping → tabla;
- Backups → tabla;
- Configuración: actualizar variables visibles a la realidad RC6 y deprecar obsoletas;
- Logs observer/bot en 0 bytes: investigar ruta/lectura/UX vs ausencia real;
- recuperar en Reportes BCRA/INDEC/otros indicadores;
- recuperar comparación mensual performance del bot vs inflación, con fuente/fecha/metodología.

## 5. `/validacion` — criterio para completar hitos

- M0 Infraestructura/capacidad: usar deploy transaccional, recuperabilidad por SHA exacto desde GitHub/GitHub Actions, disk gate, containers, timers, backups de datos cuando correspondan y health; cerrar criterio por criterio con evidencia.
- M1 Safety: real-order block, readonly, DB integrity, calendar fail-closed, recovery runbook desde GitHub, safety gates.
- M2 Fuentes/contratos: PPI read-only + Contract Evidence DUE real + provenance/contratos por familia.
- M3 Mercado/históricos: progreso sostenido PPI/Data912/A3, freshness/gaps/coverage suficiente; scheduler instalado no equivale a completo.
- M4–M8: campaña PAPER forward, realismo, estabilidad, estadística, A11Y y jornadas sostenidas.
- M9: auditoría/consenso.
- M10: governance candidate real-money.
- M11: real-money sigue BLOCKED.

## 6. Priming seguro de datos antes de mañana

Sí hay tareas útiles que pueden forzarse fuera de rueda, pero sólo background/read-only y con evidencia antes/después:

1. PPI historical bounded backfill — lote acotado con retry/backoff.
2. Data912 reconciliation/versionado — sin mezclar fuentes.
3. History integrity/freshness snapshot antes/después — quick_check, filas, identidades, fuentes, timestamps, gaps, rechazos.
4. Candle integrity/reconciliation de material ya almacenado — nunca fabricar candles.
5. Retry acotado de instrumentos con errores transitorios PPI — sin relajar validadores.

No forzar esta noche:
- browser Contract Evidence si está `NOT_DUE`;
- nuevas corridas A3 mientras identity alignment siga sin corregir;
- cambios de estrategia/gates/parámetros;
- limpieza agresiva de disco.

Criterio de éxito del priming:
- filas válidas/coverage mejoran o errores se clasifican/reducen;
- checkpoints avanzan;
- provenance intacto;
- DB quick_check=ok;
- runtime sigue sano;
- `real_orders_sent=0`.

## 7. Storage

Antes de borrar:
- dbstat/atribución por tablas;
- inventario Docker;
- inventario untracked/backups;
- verificar recuperabilidad del SHA exacto desde GitHub/GitHub Actions;
- **no reservar imagen Docker local de rollback**;
- definir retention;
- borrar sólo elementos clasificados y autorizados.

Imágenes anteriores no referenciadas —incluida cualquier imagen pre-hotfix— son candidatas si no constituyen evidencia única indispensable.

Prohibido como limpieza genérica:
- `docker system prune -a`;
- `git clean`;
- `VACUUM` ciego.

## 8. Backlog semanal consolidado

### P1
- A3 identity alignment.
- PPI historical coverage/calidad/full-universe.
- Contract Evidence primera ejecución DUE.
- Forward Lab v2.
- MFE/MAE executable + provenance.
- campaña SHADOW sostenida.
- A11Y Samsung/Voice Access.
- calendar local/USA fail-closed completo por familia.
- kill-switch fail-closed antes de reutilizar.
- snapshot/logs inconsistentes dashboard.

### P2
- retention v2 después de dbstat.
- lifecycle untracked/backups/imágenes Docker con recuperación de aplicación basada en GitHub, no en rollback local.
- sector map/correlation/family normalization.
- close-only salvage effectiveness.
- candle integrity longitudinal.
- Telegram noise/suppression con CRITICAL no suprimible.
- conversiones UX a tablas solicitadas.
- reportes BCRA/INDEC/performance vs inflación.
- configuración dashboard alineada a RC6.

## 9. Veredicto

- P0 conocidos abiertos para PAPER al corte: 0.
- hotfix Labor Day CEDEAR: CORREGIDO/DEPLOYED.
- PPI historical: funcional pero parcial — P1.
- A3: conexión/job funciona, alignment pendiente — P1.
- Contract Evidence: primera ejecución DUE pendiente — P1/no blocker hot path.
- Go Live mañana: **GO condicionado a preopen GREEN**.
- Real-money: **NO-GO / BLOCKED**.

## 10. Regla de continuidad

En cualquier chat futuro:
1. leer este checkpoint;
2. leer `POROTA_TRADING_PENDIENTES_GO_LIVE_Y_SEMANA_2026-09-06.md`;
3. verificar GitHub y runtime live antes de asumir estado actual;
4. exigir SHA runtime `db26c...` mientras no exista nuevo deploy explícito;
5. no mover la branch live por documentación;
6. registrar cada hallazgo en `/validacion` y siguiente checkpoint;
7. no transformar pendientes P1/P2 en blockers sin evidencia nueva;
8. no declarar GREEN una fuente sólo porque el timer existe;
9. no conservar rollback de aplicación en disco: recovery desde GitHub/GitHub Actions por SHA exacto.
