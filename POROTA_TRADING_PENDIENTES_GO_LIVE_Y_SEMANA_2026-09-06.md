# POROTA TRADING — PENDIENTES GO LIVE PAPER Y SEMANA

**Corte actualizado:** 2026-09-06 22:18 AR  
**Objetivo inmediato:** Go Live `PRODUCTION_PAPER` del lunes 2026-09-07 condicionado a preopen GREEN.  
**Runtime:** `17.0.0-rc6`  
**Observer live:** `db26c76723bb988c956589c572b87cbcb4191731`  
**Dashboard RC6 final:** `da2c87936d90cda17512de3bc529d13f4693c1c1` / `porota-trading-dashboard:17.0.0-rc6-go-live-final`  
**Modo:** `PRODUCTION_PAPER` / `SIMULATED` / real-money `BLOCKED`.

> Este archivo se lee junto con `POROTA_TRADING_CHECKPOINT_CANONICO_RC6_2026-09-06_POSTHOTFIX.md`. El checkpoint canónico actualizado al cierre nocturno prevalece ante cualquier contradicción histórica.

## 1. CERRADO ESTA NOCHE

- Hotfix Labor Day para todos los CEDEARs USA: deployed/validado.
- Auditoría extensa de ingesta/históricos/aprendizaje: realizada.
- RCA de PPI historical: ingesta funcional; problema concentrado en calidad/semántica/rechazos y priorización, no corrupción general del store.
- Auditoría de tarjetas/source-of-truth del dashboard: realizada.
- Dashboard RC6 final: deployed GREEN; matriz HTTP completa GREEN.
- Telegram dashboard: usa evidencia runtime vigente del notification worker/outbox/jobs.
- SRE dashboard: muestra AMARILLO real con causa explícita de latencia y no lo confunde con corrupción/disco.
- Introspección dashboard: selecciona snapshots RC6 actuales y usa ventana coherente con productor horario.
- Control postdeploy de introspección automática: GREEN; snapshot 22:15 AR generado después del deploy y mostrado como FRESH.
- Navegación `system-nav` preservada en drill-down.
- Limpieza de disco controlada completada sin prune/git-clean/VACUUM ciegos.
- Política de recovery: GitHub/GitHub Actions por SHA exacto; no rollback local como política de retención.
- Investigación IOL: arquitectura legacy existente identificada y plan GET-only RC6 definido.

## 2. MAÑANA — PREOPEN 10:15–10:30 AR

Gate obligatorio:
- observer branch/SHA exactos `hotfix/rc6-cedear-us-labor-day-20260906` / `db26c...`;
- dashboard RC6 final running, restart=0;
- observer running, restart=0, readonly=true;
- `/health=ok`;
- observer DB/history DB `quick_check=ok`;
- `PRODUCTION_PAPER` / `SIMULATED` / real-order capability BLOCKED;
- `real_orders_sent=0`;
- PPI auth GREEN/read-only;
- disk por encima de gate y sin crecimiento anómalo;
- timers RC6 enabled/active;
- BYMA calendar local GREEN;
- CEDEAR USA: nuevas aperturas HOLD por `US_LABOR_DAY`;
- acciones locales no bloqueadas por feriado USA cuando BYMA está abierto;
- field test Samsung/Voice Access sobre dashboard final.

Si cualquiera de los invariantes críticos falla: NO iniciar PAPER hasta resolver/rollback gobernado. No improvisar cambios durante rueda.

## 3. DURANTE LA RUEDA 10:30–17:00 AR

- PAPER only; ninguna orden real.
- No cambiar código/configuración salvo incidente.
- Monitorear PPI auth, quotes/freshness, decisiones, gates SHADOW, fills, posiciones, marks y PnL.
- Confirmar que CEDEARs USA se observen pero no abran nuevas posiciones.
- Verificar que acciones/locales y demás familias habilitadas sigan política de calendario/contrato.
- Vigilar candles/history/backfill sin bloquear el hot path.
- Observar introspección horaria y early-warning.
- Verificar primera ejecución Contract Evidence realmente DUE si corresponde.
- Registrar evidencia por hora en `/validacion`.

## 4. CIERRE 17:00+

- Reconciliar PAPER, posiciones, marks y PnL.
- Confirmar `real_orders_sent=0`.
- Observar postclose historical ingestion y avance real.
- Candle integrity del día.
- Comparar PPI historical antes/después.
- Registrar learning SHADOW/contrafactual.
- Actualizar `/validacion` y checkpoint.

## 5. P1 — HISTÓRICOS / DATOS

### PPI historical
- mejorar priorización de identidades incompletas y reducir ciclos sobre series ya cubiertas;
- retry/backoff acotado;
- clasificar `OHLC_INCONSISTENT`, `HIGH_NONPOSITIVE`, `OPEN_NONPOSITIVE` y errores JSON/transitorios;
- mantener validación fail-closed;
- no aceptar datos inválidos para elevar coverage.

### IOL_HISTORY_READONLY
- construir cliente aislado GET-only;
- no activar `j_main.py` legacy;
- no exponer POST de `estimar_operacion()`;
- proof pequeño PPI↔IOL sin persistencia canonical inicial;
- comparar OHLCV, gaps, <=0, adjusted/unadjusted, identidad y divergencia;
- si el proof es bueno, integrar como fuente separada al History Store v2 con provenance/versionado;
- scraping IOL sólo para Contract Evidence/spot checks si API insuficiente y términos lo permiten; no scraping masivo histórico.

### A3
- corregir identity alignment determinístico PPI↔A3;
- preservar símbolo A3 original;
- mantener `ALIGNMENT_UNVERIFIED` ante ambigüedad;
- tests antes de nueva ingesta.

### Contract Evidence
- primera ejecución realmente DUE todavía debe observarse;
- browser/auth fuera de DUE no se fuerza;
- read-only/fail-closed.

## 6. P1 — OPERACIÓN / ARQUITECTURA

- SRE measurement latency: entender/optimizar la medición que excede el umbral de 250 ms sin confundirla con DB/disk health.
- Forward Lab v2.
- MFE/MAE executable + provenance.
- campaña SHADOW sostenida.
- A11Y Samsung/Voice Access en campo.
- calendar local/USA fail-closed completo por familia.
- kill-switch fail-closed antes de cualquier reutilización/real money.

## 7. P2

- lifecycle de nuevos residuos Docker/untracked cuando aparezcan; ninguna imagen anterior queda protegida como rollback.
- sector map/correlation/family normalization.
- close-only salvage effectiveness.
- candle integrity longitudinal.
- Telegram noise/suppression con CRITICAL no suprimible.
- refinamientos visuales adicionales únicamente si el field test real detecta problemas.

## 8. `/validacion`

- M0: infraestructura/capacidad por evidencia reproducible.
- M1: safety por real-order block, readonly, DB, calendars y gates.
- M2: fuentes/contratos con PPI read-only + Contract Evidence DUE + provenance.
- M3: progreso medible y sostenido de históricos; timer instalado no equivale a completo.
- M4–M8: campaña PAPER forward, estabilidad, realismo, estadística y A11Y.
- M9: auditoría/consenso.
- M10: governance candidate.
- M11: real-money sigue BLOCKED.

## 9. VEREDICTO DE CIERRE

- **P0 conocidos abiertos para PAPER: 0.**
- Dashboard RC6: GREEN.
- Introspección postdeploy automática: GREEN.
- Runtime: GREEN de integridad/safety para cierre nocturno.
- PPI historical: P1 parcial, no blocker hot path.
- IOL: P1 proof read-only pendiente, no blocker.
- A3: P1 alignment pendiente, no blocker.
- Contract Evidence: P1 primera ejecución DUE pendiente, no blocker hot path.
- Go Live PAPER: condicionado a preopen GREEN.
- Real-money: NO-GO / BLOCKED.
