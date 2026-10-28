# CHECKPOINT 01 — WS-RUNTIME-07 — OCT-01

## Estado y evidencia
- mode: WRITE_OWNER
- branch: work/ws-runtime-07-oct01-truth-health-20260930
- no deploy.
- PAPER/SHADOW ONLY.
- runtime probe: run 36797889809 GREEN.
- focused regression final: run 36798298074 GREEN.
- real_orders_sent=0; rutas reales NOT_CALLED; PPI Watch untouched.
- /trading, /riesgo, /validacion, /analisis, /instrumentos, /universo-operativo y /api/dashboard/truth: HTTP 200 en probe previo.

## RCA DAILY_RISK_CLOCK_ROLLBACK
- Bloqueos observados por pequeñas inversiones temporales entre workers concurrentes.
- Fix: reutilizar snapshot persistido más fresco si el retroceso es same-day y está dentro del presupuesto de freshness.
- Retroceso real/grande permanece fail-closed como CLOCK_ROLLBACK.
- Tests específicos GREEN.

## Riesgo / APIs
- /riesgo no estaba caído; faltaba proyección visible.
- Nueva superficie muestra paper_daily_risk y api_health con componente, fuente, estado, checked_at/last_success y detalle.
- BYMA_STRUCTURED_CAPTURE ROJO tenía RCA de filesystem read-only: observer intentaba /opt/porota-trading dentro del contenedor.
- Fix: captura estructurada del observer usa /app/data/market; el pipeline systemd host conserva /opt explícito.

## IOL / Evidence v2 / Familias
- SOURCE_UNAVAILABLE queda scoped al collector/cache interno y nunca se convierte en “IOL=0/MCP caído”.
- Strategy cards y Análisis consumen truth projection actual.
- Evidence v2 muestra timestamps/provenance; cauciones conservan evidencia antigua y permanecen bloqueadas/stale.
- candidate_identity_v2 conserva AVAILABLE/OBSERVED_SHADOW/STALE/PAUSED_EXPLICIT; no se aplana a un único estado genérico.

## Validación diaria
Run 36797889809:
- DAILY_READ_ONLY generado 2026-09-30 19:58 ART.
- Action 4 generado 2026-09-30 19:58 ART.
- 22 ruedas observadas; última con 686 decisiones, 4 fills y 2 cierres.
- M0–M11 siguen YELLOW/GRAY cuando falta evidencia: no se fuerzan a GREEN.
- Action 4 tenía scope legacy ACCIONES_Y_CEDEARS_PAPER.
Fix:
- scope = ALL_CONTRACT_FAMILIES_PAPER.
- posiciones PAPER de todas las familias persistidas.
- gates/events/learning legacy no atribuibles por familia siguen explícitamente marcados como tales.
- regresión Action 4 incluida y GREEN.

## Pendiente
- validar estos cambios ya integrados contra el artifact exacto y runtime después del único Deploy V2 final.
