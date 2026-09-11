# POROTA TRADING RC6 — POSTDEPLOY WEEKEND INGESTION + CONTRACT EVIDENCE RCA

Fecha: 2026-09-11 (ART)

## Invariantes de seguridad

- Runtime desplegado y congelado: `f8adec8a02b2f9f0ef2dffbee75458c958bf711e`.
- Versión: `17.0.0-rc6`.
- Modo: `PRODUCTION_PAPER`.
- Sesión observada durante este trabajo: `MARKET_CLOSED`.
- Capacidad de órdenes reales: `BLOCKED`.
- `real_orders_sent=0` en preflight, trabajos y postflight.
- Ninguna ruta de orden real fue llamada.
- CP1–CP14 permanecen GREEN/FROZEN. Este trabajo es post-RC6 operacional y no reabre certificaciones.
- Política: forward-fix only; sin rollback.

## Lote coordinado de ingesta de fin de semana

Workflow de control: `.github/workflows/rc6-weekend-ingestion-kickstart-20260911.yml`.
Run reanudado: `34654515035` / job `103443872581`.
Resultado: `success`, `WEEKEND_INGESTION_KICKSTART=GREEN`, `ISSUES=0`.

### Baseline

- `history_canonical_v2=49254`
- `history_versions_v2=50002`
- `canonical symbols=270`
- `families=4`
- rango: `2025-09-04` → `2026-09-11`
- `production_history_attempts=246`
- `a3_history_ingest_state_rc6=40`
- `contract_evidence_v2_runs=159`

### PPI post-close

El disparo manual coordinado terminó `success`, pero correctamente devolvió `NOT_DUE / RECENT_HISTORY_ATTEMPT` porque existía un intento PPI reciente a `2026-09-11T22:02:57.941675+00:00`. No se duplicó descarga ni se martilló PPI.

### A3 DAILY_INCREMENTAL

Se omitió una nueva ejecución porque el timer ya había completado recientemente a las 18:30 ART. Evidencia previa del mismo día había agregado 30 versiones/canonical updates sobre 6 futuros; quedaron además identidades no verificadas que se mantienen fail-closed.

### A3 RECONCILE

Ejecución manual coordinada: GREEN.

- selected=40
- deterministic_mapped=8
- matched/complete=8
- failed=0
- versions_appended=33
- canonical_updates=65 (incluye actualización de canónico por observación de igual fuente/autoridad; no equivale a 65 filas nuevas)
- cobertura de futuros DLR simples confirmada hasta 2026-09-11 para varios vencimientos.

Resultado observable del History Store luego de reconcile:

- `history_canonical_v2: 49254 -> 49287` (+33 filas canónicas)
- `history_versions_v2: 50002 -> 50035` (+33 versiones)
- símbolos canónicos: `270 -> 271` (+1)
- familias: 4 (sin cambio)
- fecha máxima: 2026-09-11

### A3 WEEKEND_DEEP

Se ejecutó GREEN, pero no añadió filas adicionales inmediatamente después del reconcile porque los targets materializables ya estaban al día. Persistieron `ALIGNMENT_UNVERIFIED` para identidades complejas/spreads/variantes, por diseño fail-closed.

### Candle Integrity

GREEN:

- quick_check=ok
- checked_versions=5000
- dirty_bars=0
- missing_tables=[]
- violations=[]
- read_only=true

## Contract Evidence — RCA

### Conclusión ejecutiva

Contract Evidence **sí contiene y sigue incorporando datos**. El evento `materializable_tables=0` que cortó el primer lote no significa que Contract Evidence esté vacío ni que haya fallado la autenticación. Fue una pasada DOM puntual sin tablas explícitas materializables.

### Estado de datos en DB observado

- `contract_evidence=855`
- `contract_evidence_v2_current=520`
- `contract_evidence_v2_snapshots=967` en primera auditoría y 968 en la observación siguiente
- `contract_evidence_v2_changes=1454` y luego 1455
- `contract_evidence_v2_runs=159`
- `ppi_intraday_contract_state=834`
- fuente web autenticada: `web_rows=120`
- familias presentes en evidencia web actual: `ACCIONES, ACCIONES_USA, BONOS, CAUCIONES, CEDEARS, ETF, FCI, FCI_EXTERIOR, FUTUROS, INDICES, LETRAS, LICITACIONES, MONEDAS, ON, OPCIONES, TASAS`.

### Evidencia de ejecuciones DOM exitosas recientes

La misma sesión autenticada produjo materializaciones variables según la pasada:

- 21:52Z: 1 tabla → FUTUROS
- 21:57Z: 4 → BONOS, FUTUROS, LICITACIONES, OPCIONES
- 22:03Z: 3 → BONOS, FUTUROS, OPCIONES
- 22:08Z: 1 → BONOS
- 22:13Z: 1 → LICITACIONES
- 22:18Z: 5 → BONOS, CAUCIONES, FUTUROS, LICITACIONES, OPCIONES
- 22:23Z: 5 → las mismas cinco familias
- 22:28Z: 0 → exit 6 (pasada puntual que cortó el primer lote)
- 22:33Z: 1 → OPCIONES
- 22:39Z: 0 → exit 6
- 22:44Z: 3 → BONOS, FUTUROS, LICITACIONES
- 22:48Z: 1 → LICITACIONES
- 22:54Z: 2 → FUTUROS, LICITACIONES; FUTUROS registró `CHANGED_REVIEW_REQUIRED` con snapshot 1493.

En todas las pasadas auditadas: `AUTHENTICATED_TRUSTED_DEVICE`, `blocked_nonread=0`, `real_orders_sent=0`.

### RCA técnico

1. El servicio efectivo usa el drop-in `porota-contract-evidence-rc6-with-dom.sh`.
2. El wrapper ejecuta primero el scheduler/base, que con frecuencia informa `STATUS=GREEN_NOT_DUE`, pero luego lanza igualmente el browser DOM. Es decir: la capa DOM profunda no respeta la cadencia del scheduler base y termina ejecutándose en casi cada wake del timer.
3. Cada browser run consume aproximadamente 1m40–2m20 de CPU elapsed y se observaron picos cercanos a 530–686 MB de RAM y 224–291 MB de swap. Esto es excesivo para una tarea profunda cada ~5 minutos y puede competir con otras ingestas del fin de semana.
4. El collector DOM actual espera sólo ~1.5 s después de `domcontentloaded` y materializa únicamente tablas HTML con headers explícitos. La UI de PPI es dinámica; por ello una misma ruta autenticada puede entregar 0, 1 o varias tablas materializables en pasadas consecutivas.
5. El importer deliberadamente devuelve `NO_EXPLICIT_HEADER_TABLE` para una ruta cuya tabla todavía no está renderizada con headers explícitos. Si ninguna de las cinco rutas materializa, retorna exit 6. Ese exit 6 actualmente se ve como failure de systemd aun cuando existe evidencia web fresca en DB.
6. Los jobs base de Contract Evidence (DYNAMIC/CAUCIONES/AUCTIONS/DERIVATIVES/STATIC) pueden registrar `records=0 / AMARILLO` al mismo tiempo que la extensión DOM agrega snapshots `PPI_AUTHENTICATED_WEB`; son dos caminos de captura distintos y no deben confundirse.
7. Los captures base persistidos en `data/contract_evidence/rc6_trusted` sumaban 111 JSON; el último auditado estaba autenticado, legible por el observer y con `real_orders_sent=0`. Los captures DOM se gestionan por la extensión separada y se materializan en CE v2.

### Forward fix recomendado / pendiente de aplicar

No modificar CP1–CP14 ni el runtime git SHA. Aplicar sólo overlay operacional y revalidar el subsistema afectado:

- gate de frescura/cadencia para DOM profundo (no ejecutar browser en cada timer wake cuando la evidencia `PPI_AUTHENTICATED_WEB` sigue fresca);
- esperar render dinámico explícito hasta un límite seguro y soportar, sin inferencia, tablas HTML y grids ARIA con headers explícitos;
- un retry corto por ruta antes de declarar no materializable, sin relanzar todo Chrome;
- si una pasada produce 0 tablas pero la autenticación/safety es GREEN y existe evidencia web fresca dentro del SLA, clasificar `YELLOW_NO_TABLE_THIS_PASS` sin marcar el servicio como fallo; si la evidencia queda stale, sí fail-closed;
- preservar GET/HEAD/OPTIONS only, abortar mutaciones, no inferir columnas/identidades y mantener `real_orders_sent=0`.

## Estado al checkpoint

- Lote general de ingesta: GREEN.
- PPI post-close: GREEN/NOT_DUE por intento reciente, sin duplicación.
- A3 reconcile: GREEN y agregó +33 filas/versiones útiles, +1 símbolo.
- A3 weekend deep: GREEN, sin delta adicional porque el subset verificable quedó al día.
- Candle integrity: GREEN.
- Contract Evidence: DATA PRESENT / SAFETY GREEN / CADENCE+DOM RELIABILITY YELLOW. No es un problema de autenticación; requiere forward fix del scheduler DOM y de la espera/render del collector.
