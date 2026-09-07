# POROTA TRADING — SEMÁFORO CANÓNICO DE PENDIENTES RC6

**Fecha:** 2026-09-06  
**Release:** `17.0.0-rc6`  
**Observer live congelado:** `db26c76723bb988c956589c572b87cbcb4191731`  
**Modo:** `PRODUCTION_PAPER / SIMULATED`  
**Dinero real:** `BLOCKED`  

> Este documento es exclusivamente RC6. No deben incorporarse referencias operativas a releases anteriores. Ante discrepancias de prioridad con documentación anterior, prevalecen este semáforo y los addenda RC6 más recientes.

## Leyenda
- 🟢 GREEN: cerrado/implementado/probado con evidencia.
- 🟡 YELLOW: parcial, pendiente de evidencia de campo o evolución P1/P2.
- 🔴 RED: blocker safety/operativo real; NO-GO o incidente si falla.
- ⚪ GRAY: futuro, no blocker PAPER inmediato o explícitamente bloqueado/no autorizado.

## 🔴 RED — blockers reales
Al cierre nocturno: **P0 conocidos abiertos para PAPER = 0**. El preopen del 2026-09-07 debe volver a verificar los hard gates.

Pasa inmediatamente a RED si falla cualquiera de estos puntos:
1. SHA/release live no autorizado.
2. Observer caído/restart loop/readonly perdido.
3. Health crítico no OK.
4. DB observer/history `quick_check` no OK.
5. Disco bajo gate operativo.
6. PPI read-only/auth/hot-path inválido.
7. `REAL_ORDER_CAPABILITY != BLOCKED`.
8. `real_orders_sent != 0`.
9. Posibilidad real de órdenes monetarias.
10. Calendario BYMA fail-open/incorrecto.
11. Calendario USA/política del subyacente incorrecta.
12. CEDEAR USA capaz de abrir nueva posición PAPER durante `US_LABOR_DAY`.
13. Acción/instrumento puramente argentino bloqueado sólo por feriado USA cuando BYMA opera.
14. Staleness/corrupción de datos crítica para decisiones PAPER.
15. Timer/scheduler crítico funcionalmente roto cuando sea requisito de readiness/safety.

## 🟡 YELLOW — pendientes abiertos
1. Dashboard tablas clásicas reales, filas/columnas, dashboard-only.
2. PPI Historical: coverage, priorización, backfill bounded, rechazos y quality RCA.
3. IOL Historical: proof ampliado, identidad/settlement, coverage y reconciliación antes de canonical.
4. Data912: `LOW_TRUST_SOURCE / LEGACY_FALLBACK`; objetivo de reemplazo preferente por IOL cuando éste quede validado.
5. A3 Historical: identity alignment determinístico antes de canonical.
6. Contract Evidence: primera ejecución auténticamente DUE end-to-end.
7. SRE: latencia real de medición pendiente de RCA/optimización.
8. MFE/MAE ejecutable + provenance; `NO_MEDIDO` cuando corresponda.
9. Forward Lab v2: HAC/bootstrap/cohorts/subperiods/multiple testing/DSR.
10. Campaña SHADOW/contrafactual; ninguna autopromoción a BINDING.
11. Sector map/correlaciones/family normalization con provenance.
12. Cohorts/version separation.
13. Full-universe historical coverage por identidad financiera completa.
14. Retention v2/housekeeping transaccional, sin VACUUM/borrados ciegos.
15. Samsung/Voice Access field test: P1 accesibilidad/operación, no P0 hard-safety por sí mismo. Sólo escala a blocker si impide observar estados críticos/responder alertas.
16. Historical multi-source reconciliation hardening: calidad/completitud y adjusted/raw deben resolverse antes de habilitar A3/IOL canonical multi-source.

## 🟢 GREEN — no reabrir sin evidencia nueva
1. Observer RC6 safety/integrity al último control.
2. `PRODUCTION_PAPER / SIMULATED`.
3. Real-order capability `BLOCKED`.
4. `real_orders_sent=0`.
5. PPI live read-only.
6. Hotfix CEDEAR / US Labor Day desplegado/probado.
7. DB observer/history `quick_check=ok` al último control.
8. History Store v2 con versionado/provenance e identidad completa.
9. Candles bajo su contrato sampled/integrity.
10. Dashboard funcional separado del observer.
11. Introspección postdeploy automática GREEN/fresh al último control.
12. Telegram runtime/dashboard GREEN al último control.
13. Contract Evidence scheduler/overlay instalado; `GREEN_NOT_DUE` válido fuera de ventana.
14. IA intradía OFF; motor de decisión Python.
15. Learning SHADOW aislado de hard safety.

## ⚪ GRAY — futuro/bloqueado/no autorizado
1. Real money: NO-GO/BLOCKED.
2. Thresholds arbitrarios sin evidencia.
3. Cambios masivos stop/target/hold sin evidencia.
4. Autopromoción SHADOW→BINDING.
5. Network order tests reales.
6. Ejecución de derivados sin contrato/readiness probado.
7. A3 como autoridad hot-path.
8. IOL como broker ejecutor.
9. Runtimes legacy como solución a RC6.

## Orden operativo del lunes 2026-09-07
1. Antes del preopen: tablas dashboard-only si existe margen seguro; si no, postergar sin tocar hot path.
2. 10:15–10:30: hard gates RED/GREEN de runtime, DB, disco, PPI, orders, calendarios, CEDEAR y freshness.
3. 10:30–17:00: PAPER only, change freeze, evidencia horaria, CEDEAR USA observables pero sin nuevas aperturas.
4. 17:00+: reconciliar sesión, positions/marks/PnL/decisions/fills/candles/history/learning y confirmar `real_orders_sent=0`.
5. Luego: PPI Historical → IOL → A3 → Contract Evidence DUE → SRE → MFE/MAE/Forward Lab/SHADOW → retention/P2.

**Principio:** backlog no equivale a blockers. Sólo un hard gate fallido debe pintarse RED.