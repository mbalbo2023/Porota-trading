# POROTA TRADING — PENDIENTES GO LIVE PAPER Y SEMANA

**Corte:** 2026-09-06 19:30 AR  
**Objetivo inmediato:** Go Live `PRODUCTION_PAPER` del lunes 2026-09-07 condicionado a preopen GREEN.  
**Runtime base:** `17.0.0-rc6`  
**Hotfix live probado:** `db26c76723bb988c956589c572b87cbcb4191731`  
**Modo:** `PRODUCTION_PAPER` / `SIMULATED` / real-money `BLOCKED`.

> Este archivo concentra los trabajos que deben auditarse mañana y durante la semana. No autoriza dinero real ni reemplaza el preopen. Toda evidencia nueva debe registrarse también en `/validacion` y en el checkpoint canónico.

---

## 1. MAÑANA — PREOPEN 10:15–10:30 AR

### 1.1 Gate operativo obligatorio
- Verificar branch/SHA/image exactos y que el runtime siga en el hotfix RC6 validado.
- Observer y dashboard running, restart=0; observer `readonly=true`.
- `/health=ok`.
- observer DB y history DB `quick_check=ok`.
- `PRODUCTION_PAPER`, `SIMULATED`, `REAL_ORDER_CAPABILITY=BLOCKED`.
- `real_orders_sent=0`.
- PPI auth GREEN/read-only.
- Disco por encima del gate mínimo y sin crecimiento anómalo.
- Timers RC6 enabled/active y sin timers RC4 activos.
- Preopen RC6 GREEN antes de permitir PAPER.

### 1.2 Calendarios y días no operables — P0 lógico a verificar
- El motor NO debe abrir/operar en sábados, domingos ni feriados del mercado local.
- En feriados de mercado subyacente extranjero, aplicar la política específica por familia; para 2026-09-07 todos los CEDEARs con subyacente USA deben quedar `HOLD — UNDERLYING_MARKET_CLOSED: US_LABOR_DAY` para nuevas aperturas.
- Las acciones argentinas no deben quedar bloqueadas por Labor Day de USA si BYMA local está abierto.
- La ingesta histórica/background debe poder continuar fuera de rueda conforme a su política, sin confundir `mercado cerrado` con `ingesta deshabilitada`.
- Auditar que el panel ejecutivo no fabrique ni muestre operaciones en fechas no operables. Mostrar como máximo las últimas cinco operaciones reales/PAPER válidas.

### 1.3 Ingesta e históricos — prueba de funcionamiento real
Verificar cada capa con evidencia de avance, no sólo presencia de timers:
- PPI real-time quotes/snapshots: freshness, cantidad de símbolos, último timestamp, errores y staleness.
- Candles intradía: cursor de snapshots, última vela, integridad OHLC, gaps, duplicados y candle-integrity.
- PPI historical: attempts, `VALID/PARTIAL/EMPTY_OR_INVALID/ERROR`, filas incorporadas, cobertura y causas de rechazo.
- Data912: reconciliación, provenance, fallback controlado y ausencia de mezcla con PPI.
- A3: jobs diarios/reconcile/weekend, checkpoints, provenance, cobertura y `ALIGNMENT_UNVERIFIED`.
- Contract Evidence/scraping: scheduler, estado DUE/NOT_DUE, auth/2FA/stale fail-closed, métodos GET/HEAD/OPTIONS solamente, cero acciones de orden.
- Históricos canónicos: `quick_check`, rango temporal, identidades, fuentes y versionado.
- Backfill: comprobar progreso entre dos snapshots horarios para demostrar avance real.

### 1.4 Aprendizaje
- Confirmar que IA intradía permanece OFF.
- Confirmar collector SHADOW `read_only=true`, `real_money_authorized=false`, `automatic_promotion=false`.
- Registrar `would_allow/would_block`, winners/losers, PnL PAPER y contrafactual por gate.
- Confirmar que Expectancy, régimen y concentración siguen SHADOW/observación y no se autopromueven.
- Verificar que los learning samples reciban operaciones cerradas y timestamps/provenance válidos.

### 1.5 Dashboard/operabilidad antes de apertura
- Verificar `snapshot`: actualmente se reportó “no disponible”; encontrar causa y corregir sin ocultar un fault real.
- Verificar logs observer/bot: actualmente se reportan 0 bytes; determinar si es un problema de lectura/ruta/UX o falta real de archivo.
- Field test Samsung/Voice Access de navegación esencial.

---

## 2. DURANTE LA RUEDA 10:30–17:00 AR

- PAPER only; ninguna orden real.
- No cambiar código/configuración durante la rueda salvo incidente que requiera rollback.
- Monitorear PPI auth, quotes, freshness, decisiones, gates SHADOW, fills PAPER, posiciones, marks, PnL y realismo de ejecución.
- Verificar que CEDEARs USA no abran nuevas posiciones el 2026-09-07 pero sí sigan siendo observados/registrados.
- Verificar que acciones locales y otras familias habilitadas funcionen según calendario/contrato.
- Vigilar candles, históricos y backfill sin que los jobs background bloqueen el hot path PPI.
- Verificar introspección/early-warning y distinguir warning informativo de incidente accionable.
- Verificar primera ejecución real DUE de Contract Evidence cuando corresponda; si auth/2FA/stale falla, debe cerrar fail-closed.
- Registrar evidencia por hora en `/validacion`.

---

## 3. CIERRE 17:00+

- Reconciliar operaciones PAPER, posiciones, marks y PnL.
- Confirmar `real_orders_sent=0`.
- Ejecutar/observar postclose historical ingestion y comprobar avance de cobertura.
- Revisar candle integrity del día.
- Comparar PPI historical antes/después y registrar errores/reintentos.
- Registrar aprendizaje SHADOW y contrafactual.
- Actualizar `/validacion`, lecciones, blockers y siguiente acción.

---

## 4. P1 DE DATOS YA IDENTIFICADOS

### 4.1 A3 identity alignment
**Estado:** P1, no blocker del PAPER del lunes.

Evidencia observada:
- servicio/conexión A3 responden;
- primera ejecución controlada `WEEKEND_DEEP` seleccionó 40 y terminó sin fallos de transporte;
- 40/40 quedaron `ALIGNMENT_UNVERIFIED`;
- no se incorporaron filas A3 al histórico canónico;
- ejemplo de identidad: PPI `DLR/SEP26` vs A3 `DLR092026`.

Pendiente:
- construir mapping canónico producto + vencimiento + mercado/familia;
- preservar siempre el símbolo A3 original como provenance;
- no hacer matching ambiguo;
- mantener fail-closed en `ALIGNMENT_UNVERIFIED`;
- agregar tests y luego repetir ingesta controlada.

### 4.2 PPI historical coverage/calidad
**Estado:** P1, no blocker del hot path PAPER.

Última evidencia registrada:
- store canónico sano;
- PPI historical continúa parcial;
- investigar concentración de rechazos `HIGH_NONPOSITIVE` y casos `OHLC_INCONSISTENT`;
- clasificar respuestas JSON defectuosas/transitorias por instrumento;
- continuar backfill bounded con retry/backoff;
- no contaminar el store aceptando datos inválidos sólo para elevar cobertura.

### 4.3 Contract Evidence RC6
- Verificar primera ejecución DUE real.
- Mantener read-only y fail-closed.
- No forzar browser autenticado fuera de política sólo para obtener un GREEN artificial.

---

## 5. LIMPIEZA DE DISCO — HACER CON EVIDENCIA, NO A CIEGAS

Objetivo: eliminar únicamente lo que no sea necesario para funcionamiento, rollback, auditoría o continuidad RC6.

Secuencia obligatoria:
1. `dbstat`/atribución de espacio por DB y tabla.
2. Inventario de imágenes Docker, capas, contenedores detenidos, build cache y volúmenes.
3. Clasificar los artefactos untracked operacionales y backups por fecha/función.
4. Confirmar qué imagen de rollback debe preservarse.
5. Definir retention por `trading_day_ar` y por tipo de evidencia.
6. Borrar sólo elementos explícitamente clasificados como prescindibles.
7. Verificar DB/health/runtime y espacio después.

Prohibido como limpieza genérica:
- `docker system prune`;
- `git clean`;
- `VACUUM` ciego;
- borrar backups/artefactos sin inventario previo.

---

## 6. MEJORAS VISUALES / UX SOLICITADAS

### Panel ejecutivo
- No mostrar operaciones de sábados, domingos o feriados como si fueran actividad válida.
- Mostrar sólo las últimas cinco operaciones.
- `Caja y patrimonio por moneda`: convertir a tabla.

### En vivo
- Quitar el bloque/contador de workers (dato técnico que no aporta al operador).

### Histórico de trading
- Convertir a tabla la presentación principal.
- Retirar de la vista operativa `universo por moneda y familia` si no aporta decisión.
- Retirar de la vista operativa `caja y patrimonio por moneda` si duplica información.
- Eliminar `base objetiva` de la vista histórica por irrelevante para operador.
- Eliminar `cobertura` de esa vista si no aporta al operador; mantener métricas técnicas donde correspondan para auditoría.

### Universo operativo
- `Matriz por familia`: convertir a tabla legible/responsive.

### Scalping
- `Contrato intradiario`: convertir a tabla.

### Instrumentos y contratos
- Convertir a tabla.

### Aprendizaje
- `Cobertura empírica`: convertir a tabla.

### Sistema
- Corregir navegación: al hacer drill-down en introspección no debe desaparecer el menú izquierdo.
- Corregir `snapshot no disponible` o explicar claramente el estado real.
- `Salud de APIs`: convertir a tabla.
- `Jobs internos`: convertir a tabla.
- `Scraping`: convertir a tabla.
- `Backups`: convertir a tabla.
- Actualizar `Configuración` para reflejar variables reales de RC6 y ocultar/deprecar las que ya no aplican.
- Investigar logs observer/bot mostrados como 0 bytes.

### Reportes macro
- Recuperar/ubicar en Reportes la información BCRA, INDEC y otros indicadores macro.
- Recuperar la comparación mensual de performance del bot vs inflación, con fuente, fecha y metodología visibles.

Todas las tablas deben ser responsive, Voice Access friendly, sin scroll horizontal global y con controles textuales claros.

---

## 7. `/validacion` — CÓMO COMPLETAR HITOS

No completar un hito por “sensación”; cerrarlo por evidencia reproducible.

### M0 Infraestructura/capacidad
Puede avanzar materialmente con evidencia ya disponible: deploy transaccional, rollback probado, disk gate, containers, timers, backups y health. Revisar evidencias faltantes y marcar cada criterio individualmente.

### M1 Safety
Cerrar sólo con real-order block, readonly, DB integrity, rollback, calendar fail-closed y safety gates probados.

### M2 Fuentes/contratos
Requiere PPI read-only, Contract Evidence nativo/DUE probado, provenance y contratos por familia.

### M3 Mercado/históricos
Requiere avance sostenido y medible de PPI/Data912/A3, freshness, gaps y coverage suficiente; scheduler instalado no equivale a completo.

### M4–M8
Se completan con la campaña PAPER forward: estabilidad, realismo, estadística, A11Y y jornadas sostenidas.

### M9–M11
M9 auditoría/consenso; M10 governance candidate; M11 real-money continúa bloqueado hasta autorización futura explícita.

---

## 8. PRIMING DE DATOS SEGURO PARA LA NOCHE ANTES DEL GO LIVE

### Sí conviene forzar/ejecutar ahora, siempre read-only/background
1. **PPI historical bounded backfill**: otro lote limitado con retry/backoff, sin tocar el hot path.
2. **Data912 reconciliation**: reconciliar y versionar sin mezclar fuentes.
3. **History integrity/freshness snapshot antes y después**: `quick_check`, filas, identidades, fuentes, latest timestamp, gaps/rechazos.
4. **Candle integrity/reconciliation** sobre lo ya almacenado; no fabricar candles de domingo.
5. **Backfill de instrumentos con errores transitorios** priorizando retries acotados, sin relajar validadores.

### No conviene forzar esta noche
- Browser Contract Evidence autenticado si el scheduler dice `NOT_DUE`.
- Ingesta A3 adicional mientras siga roto el identity alignment; sólo generaría más `ALIGNMENT_UNVERIFIED`.
- Cualquier job que modifique estrategia, gates SHADOW o parámetros de trading.
- Limpieza de disco agresiva antes del inventario.

### Criterio de éxito del priming
El valor no es “job exit 0”. Debemos demostrar:
- más filas válidas o mejor coverage;
- checkpoints avanzados;
- provenance intacto;
- errores clasificados/reducidos;
- DB `quick_check=ok`;
- runtime PAPER sano;
- `real_orders_sent=0`.

---

## 9. BACKLOG SEMANAL CONSOLIDADO

### P1
- A3 identity alignment + repetición controlada.
- PPI historical full-universe/quality y retries.
- Contract Evidence RC6 primera ejecución DUE real.
- Forward Lab v2.
- MFE/MAE executable + provenance.
- Campaña SHADOW sostenida.
- A11Y Samsung/Voice Access continuo.
- Calendar local/USA fail-closed completo y tests por familia.
- Kill-switch fail-closed antes de cualquier reutilización.
- Resolver snapshot/logs inconsistentes del dashboard.

### P2
- Retention v2 después de dbstat.
- Lifecycle de untracked/backups/imágenes Docker con inventario explícito.
- Sector map/correlation/family normalization.
- Close-only salvage effectiveness.
- Candle integrity longitudinal.
- Telegram noise/suppression, CRITICAL no suprimible.
- Todas las conversiones de dashboard a tablas solicitadas.
- Recuperar reportes BCRA/INDEC/performance vs inflación.
- Actualizar menú Configuración a la realidad RC6.

---

## 10. VEREDICTO DE CONTINUIDAD

- P0 conocidos abiertos para PAPER mañana: 0 al corte de este archivo.
- Labor Day CEDEAR: corregido y desplegado en hotfix RC6.
- PPI historical: funcional pero parcial; P1.
- A3: conexión/job funcional, identity alignment pendiente; P1.
- Contract Evidence: esperar primera ejecución DUE; no forzar fuera de política.
- Go Live PAPER: condicionado exclusivamente a preopen GREEN y ausencia de nueva evidencia crítica.
- Real-money: NO-GO / BLOCKED.
