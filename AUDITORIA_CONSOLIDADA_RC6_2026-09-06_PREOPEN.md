# POROTA TRADING RC6 — AUDITORÍA CONSOLIDADA PREOPEN

Fecha: 2026-09-06
Alcance: Go Live `PRODUCTION_PAPER` del lunes 2026-09-07. Dinero real continúa fuera de alcance y bloqueado.

## 1. Veredicto ejecutivo

Después de contrastar las tres auditorías contra el SHA live `5bdad270c2a23bb2456a320d41e18940ce70ec6f`, los workflows posteriores al deploy y el runtime split RC6, se confirma **UN bloqueante real para nuevas aperturas PAPER del lunes**:

### P0-MONDAY — RC6-CP-003 — cierre del mercado subyacente USA limitado sólo a Apple

El live code contiene para 2026-09-07 una lista `AAPL/AAPLD/AAPLC` y sólo asigna `UNDERLYING_MARKET_CLOSED: US_LABOR_DAY` si el CEDEAR pertenece a esa lista. Sin embargo, el observer no escanea únicamente el foco: `_eligible_symbols()` incorpora el universo `AVAILABLE` y `_cycle_symbols()` rota instrumentos fuera del foco. Por lo tanto un CEDEAR no-Apple puede ser evaluado y, si los demás gates lo permiten, abrir PAPER sin el bloqueo del subyacente cerrado.

Esto no crea riesgo de dinero real, pero sí invalida la política aprobada para la rueda del 7/9 y puede contaminar la evidencia PAPER. Antes del Go Live se debe corregir y probar el gate.

**Criterio mínimo de aceptación:**
- el 2026-09-07 toda nueva apertura CEDEAR que no tenga un mercado subyacente abierto verificable debe quedar `HOLD`;
- continuar observando y persistiendo cotizaciones;
- acciones locales como GGAL no deben recibir ese bloqueo;
- tests deben incluir AAPL y varios CEDEAR no-Apple;
- preopen debe probar cobertura de familia/política, no sólo Apple;
- deploy transaccional y preopen GREEN antes de habilitar nuevas aperturas PAPER.

Para el hotfix del lunes, mientras no exista mapeo autoritativo por mercado subyacente para cada CEDEAR, la alternativa conservadora es `HOLD` de nuevas aperturas de la familia CEDEAR durante el cierre total USA, manteniendo market-data. La evolución posterior debe usar calendario por mercado subyacente y no excepciones por ticker.

## 2. Hallazgos reales pero NO bloqueantes del lunes

### P1 — RC6-CP-004 — fallback de calendario BYMA fail-open en observer

El defecto existe: si `ak_byma_calendar` lanza excepción, el observer cae a `weekday() < 5`, mientras el módulo central de sesiones falla cerrado.

No se clasifica como P0 específico del lunes porque:
- BYMA sí tiene jornada operativa el 7/9; el fallback de lunes coincide con el resultado esperado para esa fecha;
- el preopen RC6 llama directamente a `byma.es_dia_habil_operativo(today)`; si esa verificación falla, el preopen no puede considerarse GREEN;
- el defecto sí es peligroso para futuros feriados BYMA y debe corregirse fail-closed.

Plan: P1 de seguridad; centralizar el gate de sesión y agregar test de excepción antes de futuras ruedas no ordinarias y antes de BINDING/real-money.

### P1 — H1 Claude — kill-switch auto-recovery falla abierto ante error SQLite

El defecto en `ag_kill_switch_supervisor.py` es real: errores al leer cooldown o recuperaciones diarias pueden retornar `None/0` y permitir recuperación automática.

No bloquea el PAPER del lunes porque el runtime split RC6 activo arranca `bv_paper_runtime.py`, cuyos procesos son observer/scanner, exit reader, notifications, candles y scalping; `ag_kill_switch_supervisor.py` no forma parte del grafo de ejecución PAPER activo verificado. Debe corregirse antes de reutilizar ese supervisor y obligatoriamente antes de cualquier etapa real-money/M10.

### P1/P2 — excepciones silenciosas

El relevamiento de `except Exception: pass` es deuda real de observabilidad, pero no debe tratarse en bloque. Clasificación:
- P1: excepciones en lecturas que alimentan gates/decisiones de seguridad; deben fallar cerradas.
- P2: notificaciones/best-effort; agregar logging/métrica sin hacer caer el runtime.

## 3. Hallazgos no bloqueantes / documentación / governance

- `OPERATIVE_RELEASE_AUDIT_MANIFEST.txt` desactualizado: P2 documental; regenerar desde release builder.
- Archivos/timers RC4 presentes en el árbol: NO es incidente runtime. No hay unidades RC4 activas. Preservar hasta lifecycle explícito; **no ejecutar limpieza ciega**.
- `promotion_is_authorized()` sin caller productivo: no es defecto hoy porque no existe ruta de promoción. Registrar como requisito obligatorio para el futuro mecanismo SHADOW→BINDING.
- Retention/dbstat/storage: P2.
- Forward Lab v2: P1 antes de decisiones BINDING/real-money, no para observación PAPER.
- MFE/MAE persistence/provenance/`excursion_state`: P1, no hot-path blocker.
- History/A3 full-universe coverage: evidencia/cobertura continua, no implementación faltante.
- A11Y Samsung/Voice Access: aceptación operativa continua.

## 4. Hallazgos de auditoría SUPERADOS por evidencia posterior

### Contract Evidence RC6 nativo

Las tres auditorías basadas sólo en el ZIP/SHA live pueden afirmar que Contract Evidence sigue `BLOCKED_PENDING_RC6_NATIVE_WIRING`. Esa afirmación quedó desactualizada por el workflow posterior `34055591313` sobre la rama `candidate/v17.0.0-rc6-contract-evidence-native-20260906`, que instaló el wiring nativo RC6 como overlay en el host, probó `GREEN_NOT_DUE` el domingo, mantuvo `real_orders_sent=0` y dejó el timer RC6 enabled/active.

Estado canónico: `CONTRACT_EVIDENCE_RC6_NATIVE=GREEN_ACTIVE_NOT_DUE_SUNDAY`. Lo pendiente es el primer run realmente DUE y canonicalizar el overlay para recovery/reproducibilidad.

### A3 Historical

A3 Historical ya está implementado en RC6 con modos daily/reconcile/weekend/bootstrap, checkpoint, provenance, estados y aislamiento del hot path. Sólo queda crecimiento/medición de cobertura.

## 5. Plan de trabajo priorizado

### Antes del preopen del lunes — obligatorio

1. Resolver RC6-CP-003 con cambio mínimo y tests de propiedad/casos CEDEAR no-Apple.
2. Construir/validar candidate exacta; compile/tests GREEN; invariantes de `PRODUCTION_PAPER`, `SIMULATED`, `REAL_ORDER_CAPABILITY=BLOCKED` y `real_orders_sent=0`.
3. Activación transaccional vía GitHub Actions/SSH estricto, con rollback preparado. No editar host a mano.
4. Ejecutar preopen 10:15–10:30 AR. Sin GREEN completo no habilitar nuevas aperturas PAPER.
5. Durante 10:30–17:00 no hacer cambios de código/config. Ante incidente: rollback, no roll-forward improvisado.

### P1 semana

1. RC6-CP-004: eliminar fallback `weekday()` y centralizar calendario fail-closed.
2. H1 kill-switch: consultas de cooldown/recovery fail-closed + tests de DB exception.
3. Auditar únicamente excepciones silenciosas que alimentan decisiones/gates.
4. Forward Lab v2 robusto: HAC/panel, block/stationary bootstrap por `trading_day_ar`, leave-day/symbol-out, cohorts/subperiods, multiple testing/DSR.
5. MFE/MAE: persistencia, provenance y `excursion_state`.
6. Canonicalizar Contract Evidence overlay dentro de release/recovery sin alterar innecesariamente el hot path.
7. Primera evidencia Contract Evidence DUE + observación real de schedulers en rueda.
8. Campaña SHADOW y A11Y física continua.

### P2 / evolución

- Retention v2 después de `dbstat` y attribution de crecimiento.
- History/A3 coverage y fuentes `PROBE_REQUIRED`.
- mapa sectorial/correlaciones.
- efectividad close-only salvage y candle integrity longitudinal.
- release audit manifest regenerado automáticamente.
- lifecycle explícito de artefactos legacy/untracked/backups; nunca `git clean`, `docker system prune` ni `VACUUM` ciego.

## 6. Regla de decisión para mañana

- **Si RC6-CP-003 sigue abierto:** NO-GO para nuevas aperturas PAPER del sistema completo. Se puede observar mercado/read-only, pero no declarar Go Live PAPER de nuevas aperturas.
- **Si RC6-CP-003 queda corregido, testeado y desplegado, y el preopen es GREEN:** GO condicionado para `PRODUCTION_PAPER`.
- **Real-money:** NO-GO sin cambios.

Este informe prevalece sobre severidades asignadas por las auditorías cuando éstas contradicen el código exacto, el grafo runtime efectivo o evidencia postdeploy posterior.