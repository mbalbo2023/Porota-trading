# POROTA TRADING — RC4 WIP BASELINE

Preparación RC4 iniciada el 2026-09-03.

**NO DESPLEGABLE.** El runtime actual permanece intacto.

## Baseline canónico a preservar

- runtime `17.0.0-rc3-hf6-v2-candidate1`
- image `sha256:ed5c110bdcf16ef3fc184b7cb5ec6148e288bd91b9d925377bd29aa5e04ccd27`
- source congelado `a03cf47d5db01d8990a99f3f7836879c0606771b`
- audit ZIP `Porota-Trading-17.0.0-rc3-hf6-v2-candidate1-OPERATIVE-AUDIT.zip`
- audit ZIP SHA256 `8e8c7a2596e6575c5e680d6e5c01a79ebb3ac1e89a26f11f7ba5bb4e1cd38499`
- `PRODUCTION_PAPER`
- `SIMULATED`
- `real_orders=0`
- PPI autoridad live
- A3 CEM background/history only
- IA intradiaria OFF
- Dynamic concurrent risk ACTIVE

## Regla de baseline

La rama GitHub `release/v17.0.0-rc3-hf6-v2-candidate` no debe asumirse byte-a-byte equivalente al runtime candidate1. El release builder materializó patches sobre el source congelado. Esta rama RC4 parte del source congelado y deberá reconciliarse contra el ZIP operativo exacto antes de cualquier build/smoke/deploy.

Hasta que exista manifest de paridad, esta rama es exclusivamente WIP de código, tests, documentación y reconciliación.

## Backlog RC4 consolidado

### Operación / estrategia / UX
1. Recuperar drill-down de operaciones mediante flecha/expansor.
2. `/vivo`: P&L actual por operación, positivo verde y negativo rojo, con texto accesible además del color.
3. `/vivo`: timestamp real del último mark y freshness; si stale, AMARILLO/STALE y no fingir actualidad.
4. Drill-down: entrada, apertura, cantidad, moneda, settlement, mark, timestamp, P&L bruto/neto, costos, slippage, stop, target, señal, gates, riesgo y exit intent.
5. Análisis State-of-the-Art de frecuencia/capacidad por familia.
6. Replay ATR/stops/economic/depth con fills PAPER, MFE/MAE, costos, spreads, slippage, duración y rechazos.
7. Mapping sectorial explícito antes de concentración vinculante.

### Contract Evidence / scraping PPI
8. Contract Evidence v2 versionado.
9. Materializar scraping/XHR PPI autenticado; structured-first.
10. Normalizar aliases de familias.
11. Dashboard: Observados / Evidencias / Verificados / READY PAPER / Bloqueados.
12. Scheduler contractual: XHR/operabilidad 15m; cauciones/licitaciones activas 5m; opciones/futuros 15m; estáticos preopen/postclose + hash diario; browser completo semanal/off-market.
13. Ante stale/conflicto/auth/2FA/evidencia incompleta: FAIL_CLOSED/HOLD.
14. Nunca promover READY_PAPER sólo porque scraping encontró un dato.
15. Tarifario versionado con fuente/fecha/hash.

### Históricos / Scheduler / Settlement
16. Fix Históricos `0/823` sin alterar la ingesta.
17. History Store v2 reversible, identidad completa y precedencia PPI > auxiliares.
18. Scheduler: reconciliar `operational_jobs`, `source_sync`, `api_health` y systemd snapshots.
19. NEWS OFF: representar cadencia efectiva.
20. Settlement: PENDING_EXPECTED / AVAILABLE_NOW / OVERDUE_OR_STALE / PENDING_CONFIRMATION; cutoff sólo con evidencia oficial.
21. PPI errors: endpoint, causa, recuperación y stale impact.

### Infraestructura / observabilidad
22. Storage Lifecycle Worker v2 — primera ejecución AUDIT-ONLY.
23. Versionar post-close history timer y A3 CEM history timer.
24. Dynamic risk dashboard: consumo/capacidad/pérdida realizada/riesgo a stop.
25. Freshness Logs/Scheduler/introspección.
26. Nuevos contract/readiness/gates visibles en `/vivo` e introspección.

### Familias
27. Acciones/CEDEAR: conservar READY PAPER y regresión.
28. Bonos: prioridad contractual alta.
29. FCI local/Licitaciones: candidatos cercanos, no READY hasta completar reglas.
30. Letras/ON: adaptador de renta fija con validación contractual.
31. Cauciones: HOLD hasta principal min/step, day-count, cutoff, costo, profundidad y vencimiento.
32. Opciones/Futuros: simuladores especializados; nunca sizing spot.
33. Exterior/Canjes: HOLD hasta integración específica.

## Durante rueda

- no deploy;
- no restart/rebuild del runtime;
- no cambio intradía de riesgo/estrategia;
- no activación de familias HOLD;
- no migración History Store v2 en runtime;
- no Docker prune;
- no retiro de rollback antes de EOD GREEN.

## Acceptance futura

Paridad del baseline materializado, tests, migration rehearsal, secret scan, static checks, smoke aislado, DB quick_check, `PRODUCTION_PAPER`, `SIMULATED`, `real_orders_sent=0`, digest exacto y rollback documentado antes de cualquier deploy.
