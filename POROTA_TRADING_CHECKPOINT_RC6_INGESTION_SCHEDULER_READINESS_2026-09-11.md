# POROTA TRADING RC6 — CHECKPOINT INGESTION SCHEDULER / FAMILY READINESS

Fecha auditoría: 2026-09-11 20:21–20:22 ART
Branch de control: `fix/rc6-postfinal-contract-dom-stability-20260911`
Runtime host: `f8adec8a02b2f9f0ef2dffbee75458c958bf711e`
Workflow read-only: `34657774541`
Job: `103453781648`
Conclusión: SUCCESS

## Invariantes
- health `17.0.0-rc6` / status ok
- `PRODUCTION_PAPER`
- `MARKET_CLOSED`
- `real_orders_sent=0`
- `pragma quick_check=ok`
- audit sin mutaciones del host
- rutas reales de órdenes no llamadas

## Scheduler
Timers de ingesta/integridad activos y enabled:
- Contract Evidence
- PPI History Postclose
- Candle Integrity
- A3 Daily Incremental
- A3 Reconcile
- A3 Weekend Deep

Controles adicionales:
- Preopen: active/enabled
- Functional Health: active/enabled
- Source Truth: INACTIVE / NOT-FOUND — drift post-RC6 a corregir como wave operacional separada; no es un job de ingesta.

## Últimas ejecuciones
- Contract Evidence: 20:18:05–20:19:47 ART, success/0. El nuevo gate ejecutó sólo LICITACIONES; auth trusted; 1 tabla materializable; snapshot 1502; web_rows=120.
- PPI History Postclose: 20:20:30–20:20:40 ART, success/0; `NOT_DUE` por `RECENT_HISTORY_ATTEMPT`.
- Candle Integrity: 20:21:30–20:21:54 ART, success/0; 5000 versiones revisadas, 0 dirty bars, quick_check ok.
- A3 Daily: 18:30:20–18:30:24 ART, success/0; 8 deterministic_mapped, sin delta nuevo en esa corrida; 32 targets siguen ALIGNMENT_UNVERIFIED.
- A3 Reconcile: corrida coordinada 19:37:47–19:38:05 ART, success/0; 8/8 deterministic complete; +33 versions; +33 filas netas canonical; 32 alignment-unverified.
- A3 Weekend Deep: corrida coordinada 19:39:18–19:39:29 ART, success/0; sin filas adicionales; timer automático aún no disparó por primera vez, próximo sábado 03:00 ART.

## History Store
- canonical rows: 49,287
- version rows: 50,035
- symbols: 271
- families: 4
- rango total: 2025-09-04 .. 2026-09-11

Por familia:
- ACCIONES: 55 símbolos / 12,341 filas / 2025-09-04 .. 2026-09-10
- BONOS: 25 símbolos / 883 filas / 2025-09-05 .. 2026-09-04 (stale; requiere backfill)
- CEDEARS: 183 símbolos / 35,998 filas / 2025-09-04 .. 2026-09-11
- FUTUROS: 8 símbolos / 65 filas / 2026-09-01 .. 2026-09-11

No existe todavía historia canonical v2 para las demás familias.

## Contract Evidence v2
- current: 520
- snapshots: 973
- changes: 1,461
- runs: 159
- legacy contract_evidence: 855
- ppi_intraday_contract_state: 834

Familias con evidencia Web/XHR persistida: ACCIONES, ACCIONES_USA, BONOS, CAUCIONES, CEDEARS, ETF, FCI_LOCAL/FCI, FCI_EXTERIOR, FUTUROS, INDICES, LETRAS, LICITACIONES, MONEDAS, ON, OPCIONES, TASAS.

Cobertura explícita aproximada por identidad Contract v2:
- ACCIONES 54
- BONOS 42
- CEDEARS 191
- FUTUROS 42
- LETRAS 17
- LICITACIONES 1
- ON 84
- ACCIONES_USA 0
- CAUCIONES 0
- ETF 0
- FCI_LOCAL 0
- FCI_EXTERIOR 0
- OPCIONES 0
- CANJES 0

## Readiness contractual estricta
Veredicto importante: `execution_complete_identities=0` y `full_with_enrichment_identities=0` para todas las familias auditadas bajo `cq_contract_readiness_hf6.REQUIREMENTS`.

Esto NO significa ausencia de datos. Hay dos clases de faltantes:
1. Gap de wiring/materialización: ticker/market/settlement e identidad existen como columnas de identidad de Contract v2 en numerosos registros, pero no están materializados dentro de `evidence_json`; el readiness sólo evalúa `evidence`, por lo que los reporta como missing.
2. Gap real de campos económicos/contractuales: mínimos/steps, costos, ratios, ISIN/maturity/coupon/amortization en renta fija, márgenes/tick/multiplier/rules en futuros, strike/expiry/lot/greeks en opciones, NAV/cutoff/redemption en FCI, reglas de licitación, etc.

Familias sin contrato de readiness en `cq_contract_readiness_hf6`: INDICES, MONEDAS y TASAS. Deben permanecer fail-closed para ejecución hasta definir requirements. CANJES sí tiene requirements pero no evidencia actual.

## Prioridades post-auditoría
1. Corregir drift del timer Source Truth (control, no ingesta).
2. Corregir Contract Evidence mapping para incorporar identidad explícita al payload evaluable sin inferir semántica.
3. Completar los campos contractuales verdaderamente faltantes por familia usando fuentes PPI estructuradas/XHR/documentación autenticada y mantener fail-closed.
4. Expandir History Store más allá de 4 familias; prioridad inmediata BONOS por stale hasta 2026-09-04, luego LETRAS/ON y demás familias soportadas.
5. Resolver los 32 A3 futures variants/spreads `ALIGNMENT_UNVERIFIED` sólo con mapping determinístico; no inferir símbolos.

CP1–CP14 siguen FROZEN. Estos son hallazgos operacionales post-RC6 y se resuelven forward-only, revalidando sólo subsistemas afectados.