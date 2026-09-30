# CHECKPOINT — WS-OPS PREOPEN CLOSURE 2026-09-30

Actualizado: 2026-09-30

## Ownership

- WORKSTREAM_ID: `WS-OPS-PREOPEN-CLOSURE-20260930`
- mode: `DEPLOY_OWNER`
- branch: `ops/ws-ops-preopen-closure-20260930`
- SHA base productiva: `fc27321c2f460639471f298a88fa717ebad37780`
- scope: auditoría runtime previa a la rueda 2026-10-01, caución PAPER, dashboard Riesgo, históricos de instrumentos promovidos y limpieza segura/medida de disco.
- product branch: `deploy/rc6-pr69-isolated-20260915` — **NO escribir directo**.
- PAPER/SHADOW ONLY.
- `real_orders_sent=0` obligatorio.
- PPI Watch: **NO TOCAR**.
- FIX-FORWARD ONLY / NO rollback.
- plano de control: GitHub + GitHub Actions exclusivamente.

## Punto de entrada verificado

- Último Deploy V2 observado: run `36785291301`.
- Candidate/product SHA del run: `fc27321c2f460639471f298a88fa717ebad37780`.
- Predeploy asociado indicado por commit: `36784880243` GREEN.
- Deploy V2 `36785291301`: **FAILURE**.
- La promoción llegó a runtime y las 16 superficies auditadas respondieron HTTP 200.
- Readiness observado durante el run: `6.955`.
- Seguridad observada durante el run: `PRODUCTION_PAPER / real_orders_sent=0`.
- Falla bloqueante: `DASHBOARD_SURFACE_BUDGET_VIOLATION` por un único `data-porota-record='1'` residual en `/vivo`, `/universo-operativo` y `/instrumentos`.
- `VALIDADO_RUNTIME`: **BLOQUEADO** hasta identificar ese residual y cerrar la auditoría.

## Hallazgos de código previos al probe

### Históricos
- `rc6_postclose_history_job.py` retorna `BLOCKED_BY_POLICY / RC6_HISTORY_QUARANTINE` antes de ingerir.
- `cp_history_ingest_policy_hf6.py` limita `OPERATIONAL_HISTORY_FAMILIES` a ACCIONES/CEDEARS.
- `historical_candle_shadow_rc6.py` declara OUT_OF_SCOPE para familias distintas de ACCIONES/CEDEARS.
- Por lo tanto la cobertura histórica de los nuevos instrumentos permanece **NO_VERIFICADA** y existe una brecha conceptual que debe medirse contra runtime.

### Caución
- Existe `di_caucion_cash_sweep_runtime_hf6.py` con `CASH_SWEEP_ORDER_ROUTING_ALLOWED=False`.
- El run de deploy observó 10 identidades CAUCIONES en discovery PPI con estado `PPI_DISCOVERY_CONFIRMED_SPECIALIZED_EXECUTOR_PENDING`.
- La causa runtime de que caución no funcione sigue **NO_VERIFICADA** hasta inspeccionar worker, evidencia, catálogo y ledger.

### Disco
- El deploy falló antes del cleanup post-auditoría y del cleanup final.
- El run comenzó con ~4.31 GB libres.
- Pueden haber quedado temporales `/tmp/porota-deploy-v2-*` y cache Docker reclamable.
- Sólo se permite cleanup medido de temporales Deploy V2 y cache/dangling no referenciado; nunca volúmenes, SQLite, datos persistentes, logs/evidencia requerida, imágenes de contenedores activos ni PPI Watch.

## Siguiente checkpoint

Un único workflow de GitHub Actions ejecutará en paralelo:
1. runtime/dashboard residual + Riesgo;
2. caución PAPER;
3. históricos/cobertura/timers;
4. auditoría preopen para 2026-10-01;

y, después de los probes, cleanup seguro de disco bajo el mutex `rc6-unified-paper-deploy`.

Cada frente debe publicar un artifact Markdown.
