# POROTA TRADING RC6 — CHECKPOINT WEEKEND INGESTION KICKSTART

**Fecha:** 2026-09-11 ART  
**Objetivo:** adelantar de forma controlada y read-only la ingesta histórica/API/scraping antes del fin de semana, sin duplicar trabajos ya en curso ni alterar los schedulers productivos.

## Identidad y safety invariants

- Runtime desplegado y congelado: `f8adec8a02b2f9f0ef2dffbee75458c958bf711e`.
- Release: `17.0.0-rc6`.
- Modo: `PRODUCTION_PAPER`.
- Ejecución: `SIMULATED`.
- `REAL_ORDER_CAPABILITY=BLOCKED`.
- `real_orders_sent=0` antes y después del lote.
- Rutas de órdenes reales: **NO llamadas**.
- CP1–CP14 permanecen `GREEN/FROZEN`; este trabajo no los reabre.
- No hubo deploy del runtime, rollback ni cambio de SHA.

## Control plane utilizado

Workflow temporal y coordinado:
`.github/workflows/rc6-weekend-ingestion-kickstart-20260911.yml`

Commits de control:
- `85422fa07277ff789a23bfba65b9e8253369d5e2` — kickstart inicial.
- `a9b48d9f7e9acc097854449522060f677d49de97` — resume forward-fix después de resultado seguro/no-data de Contract Evidence.

Runs:
- Inicial: `34654068429`, job `103442522113`.
- Reanudado/final: `34654515035`, job `103443872581` — **SUCCESS**.

El workflow usa lock exclusivo `/tmp/porota-weekend-ingestion-rc6.lock`, comprueba SHA/safety/DB, detecta servicios ya activos y espera o evita re-ejecuciones antes de arrancar una etapa. Los timers originales no fueron deshabilitados, desplazados ni reprogramados.

## Baseline antes del kickstart

- `history_canonical_v2`: **49,254** filas.
- `history_versions_v2`: **50,002** filas.
- Símbolos canónicos: **270**.
- Familias canónicas: **4**.
- Rango canónico: **2025-09-04 → 2026-09-11**.
- `contract_evidence_v2_runs`: 28.
- `production_history_attempts`: 30.
- Estados A3: 12.
- SQLite quick-check: OK.

## Etapas ejecutadas

### 1. Contract Evidence / scraping

Se ejecutó una pasada RC6 segura:
- autenticación: `AUTHENTICATED_TRUSTED_DEVICE`;
- rutas observadas: 5;
- `blocked_nonread=0`;
- DOM capturado;
- `real_orders_sent=0`;
- tablas DOM: 0;
- candidatos autoritativos: 0;
- tablas/filas materializables: 0;
- pico aproximado del browser: 610 MB RSS.

El servicio devolvió código 6 por ausencia de datos materializables, no por safety ni por autorización. Se decidió **no repetir inmediatamente el navegador** para evitar carga duplicada. El timer productivo queda intacto para futuras ventanas.

Estado: `SAFE_YELLOW / NO_MATERIALIZABLE_DATA_THIS_PASS`.

### 2. PPI post-close history

El servicio fue invocado con control de colisión. El job aplicó su semántica de recent-attempt/idempotencia y no duplicó una ingesta reciente elegible. No aumentó el ledger en ese no-op. Esto es comportamiento esperado de protección contra repetición.

Estado: `GREEN / INTENTIONAL_RECENT_SUPPRESSION`.

### 3. A3 Daily Incremental

Antes de arrancarlo manualmente se verificó el último trigger y `Result`. Ya existía ejecución reciente satisfactoria; por lo tanto el lote **omitió correctamente el duplicado manual** y conservó el timer de 18:30 ART.

Estado: `GREEN / RECENT_SUCCESS_NOT_DUPLICATED`.

### 4. A3 Reconcile

Ejecutado exitosamente. Fue la etapa que produjo crecimiento material:
- canonical: **+33** filas;
- versions: **+33** filas;
- símbolos: **+1**.

Estado: `GREEN`.

### 5. A3 Weekend Deep

Ejecutado exitosamente de manera anticipada. No añadió filas adicionales en esta pasada inmediata, porque el reconcile previo ya había llevado al máximo actual la ventana/fuente alcanzable. El timer automático Sat/Sun 03:00 ART continúa activo y podrá capturar nueva disponibilidad durante el fin de semana.

Estado: `GREEN / NO_ADDITIONAL_ROWS_THIS_PASS`.

### 6. Candle Integrity

Ejecutado satisfactoriamente. Sin efectos inseguros ni cambios de trading.

Estado: `GREEN`.

## Resultado neto del lote

- `history_canonical_v2`: **49,287** filas (**+33**).
- `history_versions_v2`: **50,035** filas (**+33**).
- Símbolos canónicos: **271** (**+1**).
- Familias canónicas: **4** (sin expansión en este lote).
- Rango canónico: **2025-09-04 → 2026-09-11**.
- Runtime: SHA `f8adec8...` sin cambios.
- `real_orders_sent=0`.
- Rutas reales: no llamadas.

## Timers preservados

Quedaron enabled/active y sin reprogramar:
- `porota-contract-evidence-rc6.timer`;
- `porota-history-postclose-rc6.timer`;
- `porota-candle-integrity-rc6.timer`;
- `porota-a3-history-daily-rc6.timer`;
- `porota-a3-history-reconcile-rc6.timer`;
- `porota-a3-history-weekend-rc6.timer`.

Horarios relevantes preservados:
- A3 daily incremental: Mon–Fri 18:30 ART.
- A3 reconcile: Mon–Fri 23:30 ART.
- A3 weekend deep: Sat/Sun 03:00 ART.
- Post-close eligibility poll: cada 15 min, con gate interno.
- Candle integrity: cada 10 min.
- Contract Evidence: timer/cadencias RC6 preservadas.

## Gap que queda abierto

El volumen histórico mejoró, pero **271 símbolos / 4 familias** todavía no representa el objetivo de máxima cobertura del universo PPI. Contract Evidence tampoco materializó tablas en la pasada after-hours.

Próximo paso vinculante de esta wave:
1. auditoría live read-only de cobertura por familia/símbolo/fuente/estado;
2. identificar qué familias ya están disponibles en los catálogos y qué fuente histórica puede alimentarlas;
3. localizar únicamente ejecutores/backfills RC6 existentes y seguros;
4. ejecutar sólo los que sean read-only, idempotentes y no colisionen con timers productivos;
5. preservar `PRODUCTION_PAPER`, `real_orders_sent=0` y SHA runtime congelado.

No declarar `coverage COMPLETE` hasta disponer de evidencia por familia e instrumento.
