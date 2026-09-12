# POROTA TRADING RC6 — CHECKPOINT HOUSEKEEPING + F01 — 2026-09-12

## Runtime canónico vigente

- Versión: `17.0.0-rc6`.
- Host SHA final certificado: `c8773c8346a880ac5ecfe99ece5555c5bb771524`.
- Modo: `PRODUCTION_PAPER`.
- Ejecución: `SIMULATED`.
- `REAL_ORDER_CAPABILITY=BLOCKED`.
- `REAL_ORDERS_SENT=0`.
- Política de cambios: NO ROLLBACK; forward-fix solamente.

## EOD / Overnight UI — DESPLEGADO GREEN

- Run: `34671405159`.
- SHA desplegado inicialmente: `d719deeb379de285b07fa61a40f5d48d19d5bffd`.
- Validación offline: GREEN.
- Activación/postflight: GREEN.
- `TRADING_ESTRATEGIAS_EOD_UI=GREEN`.
- EOD / Overnight queda dentro de `Trading → Estrategias`.
- Scalping no vuelve a aparecer dentro de Estrategias y mantiene su menú propio.
- `EOD_POLICY_ACTIVE=CURRENT_EOD`.
- `EOD_AUTO_PROMOTION=false`.
- `REAL_ORDERS_SENT=0` y rutas reales no llamadas.
- El posterior despliegue F01 preservó esta UI y política (`EOD_UI_PRESERVED=GREEN`).

## F01 — DESPLEGADO GREEN

Antecedente original:

- Rama original: `deploy/rc6-f01-paper-20260912`.
- Candidato original: `55826f9b0a0ee7998f5498cc44d887f76ffc730f`.
- La lógica/CI original era GREEN, pero la activación fue bloqueada por un guard que exigía `git status --porcelain` completamente vacío mientras el host tenía 58 archivos no trackeados.
- No fue un fallo funcional de F01.

Rebase y despliegue final:

- Base productiva exacta usada: `d719deeb379de285b07fa61a40f5d48d19d5bffd`.
- Rama: `deploy/rc6-f01-paper-after-eod-20260912`.
- Commit funcional rebased: `a42fdd4fbce05d9390e55cc8e73358e39c242961`.
- Commit final autorizado/desplegado: `c8773c8346a880ac5ecfe99ece5555c5bb771524`.
- Run final: `34673102199`.
- Validación exacta: SUCCESS.
- Build candidate: SUCCESS.
- Tests F01 + backtest + regresión EOD: SUCCESS.
- Activación/postflight PAPER: SUCCESS.
- `F01_SOURCE_IDENTITY=GREEN`.
- `F01_RUNTIME_MODEL=GREEN`.
- `EOD_UI_PRESERVED=GREEN`.
- `POST_SAFETY=ok|PRODUCTION_PAPER|0`.
- `HEALTH_OK=YES`.
- `REAL_ORDERS_SENT=0 REAL_ORDER_ROUTES=NOT_CALLED`.
- `RC6_F01_DEPLOY_POSTFLIGHT=GREEN`.

## Housekeeping — PENDIENTE

Estado validado durante ambos despliegues:

- `TRACKED_DIRTY_COUNT=0`: no hay código versionado modificado fuera de Git.
- `UNTRACKED_COUNT=58`: existen 58 artefactos no trackeados, principalmente scripts/resultados históricos de diagnósticos y despliegues (`v17_*.py`, `*_result.json`, `.pid`, `.sh`, probes).
- Los 58 artefactos fueron preservados exactamente antes/después de EOD UI y F01.
- No hubo colisiones entre esos artefactos y los paths de los targets desplegados.

Pendientes obligatorios:

1. Inventariar y clasificar los 58 artefactos en evidencia útil / activo / obsoleto.
2. Preservar la evidencia útil fuera del checkout operativo antes de eliminar nada.
3. Retirar sólo residuos confirmados como obsoletos; prohibido `git clean` indiscriminado.
4. Evitar que diagnósticos/deploys escriban nuevos `.json`, `.pid`, logs o scripts temporales en la raíz del repo.
5. Endurecer `.gitignore` sólo para artefactos inequívocos, sin patrones amplios que oculten código legítimo.
6. Auditar y reducir ramas GitHub cerradas/integradas preservando referencias canónicas, release, checkpoints y ramas activas.
7. Establecer lifecycle de ramas: creación, convergencia, certificación y retiro.
8. Converger a un workflow estable de deploy por SHA/dispatch.
9. Mantener deploy observable por etapas: preflight, build, activation, health y postflight.
10. Mantener siempre guardas `PRODUCTION_PAPER`, `SIMULATED`, `REAL_ORDER_CAPABILITY=BLOCKED`, `real_orders_sent=0`.

## Estado final de este checkpoint

- EOD / Overnight UI: `GREEN / DESPLEGADO`.
- F01: `GREEN / DESPLEGADO`.
- Runtime canónico: `c8773c8346a880ac5ecfe99ece5555c5bb771524`.
- Housekeeping: `PENDIENTE`.
- Limpieza destructiva: `NO AUTORIZADA` sin clasificación previa.
- Los 58 untracked permanecen preservados para auditoría posterior.
