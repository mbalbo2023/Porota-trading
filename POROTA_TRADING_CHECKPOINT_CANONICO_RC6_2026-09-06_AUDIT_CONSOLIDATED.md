# POROTA TRADING — CHECKPOINT CANÓNICO RC6 — AUDITORÍAS CONSOLIDADAS PREOPEN

Fecha: 2026-09-06

Este checkpoint incorpora y prevalece, para la clasificación de auditorías y decisión del Go Live PAPER del 2026-09-07, sobre `POROTA_TRADING_CHECKPOINT_CANONICO_RC6_2026-09-06_CORRECTED_STATE.md`. Conserva las correcciones de Contract Evidence/A3 y agrega los hallazgos verificados de las auditorías ChatGPT, Claude y Google.

## 1. Identidad live

- Branch live: `release-candidate/v17.0.0-rc6-deploy3-20260906`
- SHA live auditado: `5bdad270c2a23bb2456a320d41e18940ce70ec6f`
- Imagen: `porota-trading-bot:17.0.0-rc6`
- Modo: `PRODUCTION_PAPER`
- Ejecución: `SIMULATED`
- Real orders: bloqueadas por diseño
- Última evidencia conocida: `real_orders_sent=0`

Este checkpoint es documental; no modifica el runtime live.

## 2. Correcciones canónicas que siguen vigentes

### Contract Evidence RC6

NO es backlog de implementación. El workflow `34055591313` desplegó wiring nativo RC6 como overlay de host, comprobó `GREEN_NOT_DUE` el domingo, `AUTH_BROWSER_STARTED=NO`, `real_orders_sent=0` y dejó `porota-contract-evidence-rc6.timer` enabled/active.

Estado: `CONTRACT_EVIDENCE_RC6_NATIVE=GREEN_ACTIVE_NOT_DUE_SUNDAY`.

Pendiente real: primer run DUE + canonicalización del overlay dentro del release/recovery.

### A3 Historical

NO es backlog de implementación. RC6 ya contiene engine/job, `BOOTSTRAP`, `DAILY_INCREMENTAL`, `RECONCILE`, `WEEKEND_DEEP`, checkpoints, provenance y estados de ingestión. Timers RC6 instalados/activos. Pendiente sólo cobertura/evidencia acumulada.

### Otros ya implementados

- SHADOW counterfactual learning.
- History freshness/depth.
- Close-only separado de FULL_OHLC.
- Telegram P0/CRITICAL unsuppressible.
- A11Y software/table responsive.
- Forward Lab expectancy base.
- MFE/MAE executable calculation base.

## 3. Resultado de las tres auditorías — blocker matrix

### P0-MONDAY / BLOQUEANTE REAL — RC6-CP-003

**Problema:** el 2026-09-07 `US_UNDERLYING_HOLIDAY_BLOCKS` cubre sólo `AAPL`, `AAPLD`, `AAPLC`. El observer, sin embargo, rota el universo `AVAILABLE` fuera del foco. Por ello un CEDEAR no-Apple puede llegar a la evaluación PAPER sin `opening_block_reason` por cierre del mercado subyacente USA.

**Impacto:** no puede enviar dinero real, pero puede abrir una posición simulada incompatible con la política de Labor Day y contaminar la campaña PAPER.

**Gate:** antes de declarar GO para nuevas aperturas PAPER debe quedar corregido, testeado y desplegado.

**Hotfix mínimo conservador para 2026-09-07:** mientras no exista mapping autoritativo por mercado subyacente, toda nueva apertura CEDEAR sin mercado subyacente verificablemente abierto debe quedar HOLD durante el cierre total USA, manteniendo observación/persistencia de cotizaciones. Las acciones locales no deben quedar bloqueadas por esta regla.

Issue: `#36`.

### P1 / NO bloqueante específico del lunes — RC6-CP-004

**Problema real:** `bf_production_paper_observer._business_day()` falla abierto a `weekday()<5` si el calendario BYMA lanza excepción; el módulo central de sesiones falla cerrado.

**Por qué no es P0 del 7/9:** BYMA tiene jornada operativa ese lunes y el preopen RC6 consulta directamente `byma.es_dia_habil_operativo(today)`; si la consulta falla, no existe evidencia válida de preopen GREEN. El fallback peligroso afecta especialmente futuros feriados/días no operativos.

**Plan:** eliminar fallback weekday, centralizar calendario fail-closed y agregar test de excepción.

Issue: `#37`.

### P1 / NO bloqueante PAPER live — H1 Claude kill-switch

**Problema real:** `ag_kill_switch_supervisor.py` puede interpretar error SQLite como cero recuperaciones o ausencia de halt, saltando límite diario/cooldown.

**Clasificación:** P1 de seguridad futura. No integra el grafo del runtime split RC6 `PRODUCTION_PAPER` verificado (`bv_paper_runtime.py` + observer/exit/notifications/candles/scalping). Corregir antes de reutilizar ese supervisor y obligatoriamente antes de M10/real-money.

### P1/P2 — `except Exception: pass`

No tratar los 77 casos como un único defecto:
- P1: los que alimentan decisiones/gates/estado de seguridad -> fail-closed + telemetry.
- P2: Telegram/notificaciones/best-effort -> logging/métrica, sin tumbar el runtime.

### P2 / no bloqueantes

- `OPERATIVE_RELEASE_AUDIT_MANIFEST.txt` desactualizado -> regenerar por pipeline.
- archivos RC4 físicos en repo -> lifecycle controlado; no hay timers RC4 activos; NO limpiar a ciegas.
- `promotion_is_authorized()` sin caller productivo -> requirement futuro al construir promoción SHADOW→BINDING.
- Retention/dbstat/storage governance.
- mapa sectorial/correlaciones.
- History/A3 coverage y fuentes `PROBE_REQUIRED`.

## 4. Falsos positivos / hallazgos superados de las auditorías

### Contract Evidence `BLOCKED_PENDING_RC6_NATIVE_WIRING`

SUPERADO. Las auditorías basadas en el ZIP/live SHA no incorporaron el overlay posterior del workflow `34055591313`. No reabrir como backlog de construcción.

### A3 Historical como implementación pendiente

SUPERADO. Sólo coverage/evidence permanece.

### Archivos RC4 como incidente live

NO REPRODUCIDO. Los archivos legacy no equivalen a unidades activas. Mantener trazabilidad hasta lifecycle explícito; no ejecutar `git clean`, `git rm rc4_*` masivo, `docker system prune` ni otra limpieza ciega antes del Go Live.

## 5. Backlog consolidado posterior a auditorías

### Obligatorio antes del Go Live PAPER

1. RC6-CP-003: corregir política Labor Day/subyacente para CEDEARs fuera del foco Apple.
2. Tests: Apple + múltiples CEDEAR no-Apple + acción local; confirmar HOLD sólo de nuevas aperturas, market-data sigue activo.
3. Candidate/build/tests/invariantes GREEN.
4. Deploy transaccional vía GitHub Actions/SSH estricto; rollback preparado.
5. Preopen 10:15–10:30 AR completamente GREEN.

### P1 semana

1. RC6-CP-004 calendario fail-closed.
2. H1 kill-switch DB fail-closed.
3. Revisar silent exceptions que afectan decisiones/gates.
4. Forward Lab v2: HAC/panel apropiado, block/stationary bootstrap por `trading_day_ar`, leave-day/symbol-out, cohorts/subperiods, multiple testing/DSR.
5. MFE/MAE persistence/provenance/`excursion_state`.
6. Contract Evidence: primer DUE + canonicalizar overlay en recovery/release.
7. Primera observación real de schedulers en rueda.
8. SHADOW campaign sostenida.
9. Samsung/Voice Access field acceptance continua.

### P2 / evidencia evolutiva

- Retention v2 después de dbstat/attribution.
- A3/history coverage.
- source probes de familias no probadas.
- sector map/correlations.
- close-only effectiveness.
- candle integrity longitudinal.
- stale release audit manifest.
- lifecycle de untracked/backups/legacy sin limpieza ciega.

## 6. Decisión operativa del lunes

### Si CP-003 sigue abierto

`NO-GO` para nuevas aperturas PAPER del sistema completo. Puede continuar observación/read-only y acumulación de market-data, pero no debe declararse Go Live de nuevas aperturas.

### Si CP-003 queda corregido + tests/deploy GREEN + preopen GREEN

`GO CONDICIONADO` para `PRODUCTION_PAPER` / `SIMULATED`.

Durante 10:30–17:00 AR: no cambiar código/config. Ante incidente: rollback; no roll-forward improvisado.

### Dinero real

`NO-GO` sin cambios. M10/M11 y autorización explícita siguen siendo obligatorios.

## 7. Fuentes externas verificadas para la política del 7/9

- BYMA calendario 2026 incluye 7 de septiembre como Labor Day USA y no lo lista como feriado argentino sin negociación.
- NYSE calendario 2026 declara Monday September 7 como Labor Day y mercado cerrado.

## 8. Informe asociado

Detalle de la contrarréplica: `AUDITORIA_CONSOLIDADA_RC6_2026-09-06_PREOPEN.md`.

---

Regla persistente: el usuario es la última instancia de ejecución manual. Cambios de runtime deben preferir GitHub Actions -> SSH estricto -> validación -> activación transaccional -> postflight -> rollback. Este checkpoint no autoriza por sí mismo ningún cambio de runtime.