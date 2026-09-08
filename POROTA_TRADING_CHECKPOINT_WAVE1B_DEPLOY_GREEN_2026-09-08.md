# POROTA TRADING — CHECKPOINT WAVE 1B DEPLOY GREEN — 2026-09-08

## Estado
- Wave 1A: DEPLOYED GREEN.
- Wave 1B: DEPLOYED GREEN.
- Rama deploy Wave 1B: `deploy/rc6-wave1b-sla-scraping-20260908`.
- Commit desplegado: `860b26ca22a73679217e8953207041da2b8afffd`.
- GitHub Actions run: `34282637609`.

## Validación CI
- Exact live-baseline diff gate: GREEN.
- Compile + focused tests: GREEN.
- Safety invariants: GREEN.
- Candidate image build: GREEN.

## Preflight host
- Baseline previo: `b927ddacd062db0a2400c82dc69c9049a9b18d5c`.
- Host tracked diff gate: GREEN.
- Observer running=true, restarts=0.
- Dashboard running=true, restarts=0.
- PRE_DB=`ok|PRODUCTION_PAPER|0`.
- Disk free predeploy: 8,598,278,144 bytes.

## Scope activado
- `rc6_data_sla_policy.py`
- `rc6_dom_coverage_reconciler.py`
- `rc6_scraping_semaphore.py`
- tests/workflows de Wave 1B.

## Postflight
- Observer running=true, restarts=0, image `porota-trading-bot:17.0.0-rc6`.
- Dashboard running=true, restarts=0, image `porota-trading-bot:17.0.0-rc6`.
- POST_DB=`ok|PRODUCTION_PAPER|0`.
- `WAVE1B_MODULE_IMPORTS=GREEN`.
- `REAL_ORDER_TEST=NOT_PERFORMED`.
- `ORDER_ROUTES=NOT_CALLED`.
- `ROLLBACK_AUTOMATICO=NO`.
- `WAVE1B_DEPLOY=GREEN`.

## Política operativa
- `real_orders_sent=0` permanece absoluto.
- Ningún Budget/Confirm/Cancel/orden real fue usado.
- Ante falla futura: RCA -> fix -> redeploy automático cuando sea seguro y respaldado por evidencia.
- Rollback sólo con autorización explícita de Martín.

## Próximas oleadas candidatas
1. Promoción runtime `ppi-client 1.3.0` con credenciales regeneradas ya validadas read-only.
2. Browser V3 read-only safe routes.
3. Contract Evidence/Scraping Dashboard SHADOW.
4. Disk Phase A después de estabilización observada.
5. Event Risk persistence/corroboration, Risk Engine binding PAPER, Learning aggregation y UX tablet.
