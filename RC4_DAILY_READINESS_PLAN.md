# POROTA TRADING — RC4 Daily Readiness Plan

Estado: **WIP / NO DESPLEGABLE**.

Este documento debe incorporarse al checkpoint canónico post-deploy RC4 y revisarse en cada chat diario hasta cerrar la expansión operativa.

## Objetivo

Evitar que el proyecto pierda continuidad entre conversaciones y convertir el avance hacia Scalping PAPER, ampliación del universo, Contract Evidence, History Store v2 y políticas SHADOW→BINDING en un proceso diario medible.

## Regla diaria obligatoria

Cada jornada debe comenzar o cerrar con una revisión explícita de:

1. **Runtime RC4 / safety**: versión, imagen/digest, observer/dashboard, DB quick_check, PPI, `PRODUCTION_PAPER`, `SIMULATED`, `real_orders=0`.
2. **Scalping PAPER**: readiness técnico, freshness de book/marks, exits, costos/slippage PAPER observados/modelados, anomalías, replay pendiente y evidencia acumulada.
3. **Universo ampliado**: familias READY/HOLD, Contract Evidence faltante, ejecutor específico requerido, costos, unidades, settlement, mínimos/steps y freshness.
4. **Contract Evidence**: runs por scheduler, evidencia nueva/cambiada/conflictiva/stale, alias normalizados y efecto sobre readiness. Evidencia nueva nunca auto-promueve `READY_PAPER` sin contrato completo.
5. **History Store v2**: schema, población, cobertura v2, fallback legacy, freshness, errores, source arbitration y backup.
6. **Validación SHADOW→BINDING**: campaña, muestra, ruedas válidas, `would_block`, criterios cumplidos, criterios faltantes, cambios de fingerprint, lecciones y elegibilidad. El dashboard nunca cambia autoridad automáticamente.
7. **Replay/calibración**: max-hold, slippage PAPER proxy, FIXED vs ATR y ATR 10/15/20/25/30 sobre los mismos datos; no aplicar parámetros sin evidencia.
8. **Scheduler/Health/SRE**: jobs retrasados o sin evidencia con causa explícita, last/next, duración, fuente de evidencia y acciones.
9. **Backups/Storage**: cobertura por storage, restore test, retención, disk/WAL y lifecycle audit-only cuando corresponda.
10. **Módulos huérfanos**: cantidad `PENDING_WIRING`/`ORPHAN_REVIEW`; cada módulo debe terminar conectado, deferido con motivo o deprecado documentadamente.

## Horizonte operativo de referencia

Estas ventanas son **estimaciones de trabajo/evidencia**, no promesas ni gates automáticos:

| Objetivo | Horizonte de referencia después de RC4 operativo | Condición real de salida |
|---|---:|---|
| Acciones / CEDEAR PAPER | Ya disponibles | Mantener safety, contratos y datos frescos |
| Scalping Acciones/CEDEAR PAPER técnicamente utilizable | 1–3 ruedas | UI/observabilidad, book/marks fresh, exits, costos/slippage PAPER, replay básico |
| Scalping suficientemente observado para calibrar | ~1–2 semanas | Muestra suficiente, anomalías entendidas, replay y estabilidad |
| FCI local / Licitaciones PAPER | ~3–7 días si la evidencia contractual se completa | Contrato, costos, settlement, mínimos/steps, executor y tests |
| Bonos / Letras / ON PAPER | ~1–2 semanas | Unidades nominales, mínimos/múltiplos/steps, costos, settlement y tests |
| Futuros PAPER | ~2–4 semanas | Multiplier, tick/tick value, margen, vencimiento, settlement, costos y executor específico |
| Opciones PAPER | ~2–4 semanas | call/put, strike, expiry, lot/multiplier, tick, exercise, settlement, costos y executor específico |
| Universo significativamente ampliado | Puede comenzar durante primera semana | Cada familia se habilita individualmente al completar readiness; no se espera al resto |
| Mayoría de familias seriamente decision-ready en PAPER | ~2–4 semanas | Contratos + History v2 + executors + replay + observabilidad |
| Determinadas policies candidatas a BINDING | ~3–5 semanas o más según muestra | Criterios propios, evidencia SHADOW y decisión explícita |

## Principios que no pueden perderse

- **INGESTA != HABILITACIÓN**.
- Tener instrumentos o histórico no autoriza operaciones.
- `UNMAPPED` es preferible a inferir sector.
- PAPER no observa costos realmente cobrados por PPI; el slippage PAPER es proxy/modelo, no ejecución real.
- No mezclar P&L ARS y USD.
- No activar Options/Futures con executor de contado.
- No promover una familia a READY por scraping aislado.
- Stale/conflict/auth/2FA insuficiente => HOLD/fail-closed.
- Backtest/replay es necesario pero no suficiente para `PRODUCTION_REAL`.
- Cualquier cambio de parámetros durante una campaña de validación debe quedar versionado y puede invalidar/reiniciar la muestra.

## Entregable canónico post-RC4

Después del deploy y validación de RC4, el checkpoint `.md` diario debe incluir esta sección completa o una versión posterior explícitamente versionada. Además debe incluir:

- fecha/hora del análisis;
- avance contra cada horizonte;
- métricas del día;
- nuevos bloqueos;
- decisiones tomadas;
- desviaciones de la estimación original y su motivo;
- prioridades para la siguiente rueda.
