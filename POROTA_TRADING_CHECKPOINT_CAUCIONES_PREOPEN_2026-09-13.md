# POROTA TRADING — CHECKPOINT CAUCIONES PREOPEN 2026-09-13

**Continuación de:** `POROTA_TRADING_CHECKPOINT_CAUCIONES_READY_CONTINUACION_2026-09-13.md`  
**Modo obligatorio:** `PRODUCTION_PAPER`  
**Capacidad de orden real:** BLOQUEADA  
**Objetivo:** dejar todo lo determinístico cerrado antes de la rueda del 2026-09-14; mañana sólo debe quedar evidencia dinámica de mercado abierto.

## EVIDENCIA PREOPEN YA CERRADA

- Workflow read-only `34780828035`: SUCCESS contra el servidor desplegado, sin restart/deploy.
- `porota_production_observer`: running, restart=0, rootfs read-only.
- Runtime: `PRODUCTION_PAPER`, PPI auth OK, DB quick_check OK, `real_orders_sent=0`.
- Procesos vivos: `bv_paper_runtime.py`, `bf_production_paper_observer.py`, `--intraday-scalping-worker`, candle/notification/exit-reader.
- Catálogo CAUCIONES: 10/10 (`PESOS1/2/7/30/120`, `DOLAR1/2/7/30/120`), BYMA/INMEDIATA, ARS o USD_MEP.
- `ppi_intraday_points`: cobertura histórica 10/10; el worker intraday tiene heartbeat y `real_orders_sent=0`.
- Raw metadata PPI read-only audit `34780956815`: SUCCESS. Confirma ticker, descripción, moneda, `type=CAUCIONES`, `market=BYMA`, `nominalInPrice=1`; no aporta por sí solo fees/maturity ejecutables.
- `caucion_readiness_state` sigue `NOT_DEPLOYED` en el runtime vivo; esto es un blocker conocido, no una promoción implícita.

## HORARIO / CALENDARIO

Fuentes congeladas en `rc6_caucion_schedule_sources.py`:
- BYMA Comunicado 19016, 01/09/2026.
- PPI Support `Horarios de Mercado`, actualizado 28/07/2025: Contado Inmediato, incluyendo Cauciones, lunes a viernes 10:30–17:00.

Se corrigió un gap de seguridad: el controller ya no permite usar una única evidencia ARS para todo el universo ARS+USD_MEP. Exige evidencia independiente por moneda y falla cerrado si falta cualquiera.

Workflow `34781137219`: SUCCESS tras la corrección multi-moneda.
Workflow `34781212505`: SUCCESS con fuentes versionadas y prueba explícita de la rueda 2026-09-14 para ARS + USD_MEP.

## BLOCKERS DETERMINÍSTICOS QUE DEBEN CERRARSE ANTES DE MAÑANA

1. **Freshness dedicado 10/10:** el observer general rota un universo amplio (`PAPER_ACTIVE_SYMBOL_LIMIT=20`) y el intraday rota lotes (`PAPER_INTRADAY_SCAN_SECONDS=180`, batch 24). Aunque ambos ya recogen CAUCIONES, no garantizan que las 10 queden simultáneamente dentro del TTL canónico de 5 minutos. Crear/integrar un refresher CAUCIONES read-only que priorice exactamente las 10 y publique heartbeat.
2. **Canonicalización dinámica:** transformar `current/book` frescos en snapshots canónicos sólo con semántica demostrada: lado colocador, TNA, profundidad ejecutable, plazo/vencimiento, mínimo/step, base 365 y costos. Cualquier campo sin evidencia = HOLD.
3. **Costo all-in:** PPI publica comisión colocadora Internet (ARS 2% + IVA anual; USD hasta 1% + IVA anual). Los derechos BYMA y bonificaciones vigentes deben quedar versionados/calcúlables para no inventar `quoted_total_fees`.
4. **Runtime especializado:** conectar `rc6_caucion_cycle_controller` + `rc6_caucion_readiness_state` al supervisor PAPER. Debe persistir una sola verdad `CAUCION_FRESH_DATA_AGENT_GREEN` y conservar real-order capability = 0.
5. **Dashboard/READY binding:** CAUCIONES no puede heredar `READY_PAPER` del contador spot. Debe consumir `rc6_caucion_readiness_state.effective_state()` y mostrar HOLD ante stale/missing.
6. **Oportunidad -> PAPER placement:** el detector intradía actual clasifica/rankea una oportunidad pero `promotion_allowed=False`. Para quedar realmente operativo en simulación debe conectarse, detrás de todos los gates, al allocator PAPER existente con obligaciones/reserva, profundidad, idempotencia y costo exacto. No agregar rutas reales.
7. **Deploy PAPER off-hours + postflight:** sólo después de CI verde; verificar proceso, tabla specialized readiness, dashboard, DB quick_check, PPI auth y órdenes reales 0. No activar READY artificialmente fuera de rueda.

## LO ÚNICO QUE DEBE QUEDAR PARA LA RUEDA DEL 2026-09-14

- Confirmar que las 10 identidades reciben `current/book` y referencias intraday frescas dentro de TTL con mercado OPEN.
- Confirmar semántica de puntas/depth contra datos reales activos.
- Observar transición real `HOLD -> CAUCION_FRESH_DATA_AGENT_GREEN -> READY_PAPER` sólo si todos los gates son válidos.
- Probar una oportunidad PAPER válida o demostrar correctamente `NO_VALIDATED_INTRADAY_OPPORTUNITY`; no forzar una operación para obtener un verde.
- Mantener `PRODUCTION_PAPER` y `real_orders_sent=0` durante toda la prueba.

## SEMÁFORO PREOPEN

- 🟢 Seguridad PAPER / cero órdenes reales.
- 🟢 Runtime base y PPI auth.
- 🟢 Catálogo CAUCIONES 10/10.
- 🟢 Intraday histórico 10/10.
- 🟢 Horario BYMA/PPI 10:30–17:00 versionado.
- 🟢 Schedule gate separado ARS / USD_MEP y CI verde.
- 🟡 Freshness dedicado 10/10: pendiente de integración.
- 🟡 Costos all-in versionados: pendiente final.
- 🟡 Specialized readiness -> runtime/dashboard: pendiente de deploy/integración.
- 🟡 Detector intradía -> allocator PAPER: pendiente de wiring.
- 🔴 Evidencia de rueda real 2026-09-14: necesariamente pendiente hasta apertura.

**Regla:** no declarar `READY_PAPER` operativo mientras cualquiera de los amarillos anteriores siga abierto, aunque el mercado abra y existan cotizaciones.
