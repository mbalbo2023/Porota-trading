# POROTA TRADING — PENDIENTES GO LIVE PAPER Y SEMANA

**Corte actualizado:** 2026-09-06 22:30 AR  
**Objetivo inmediato:** Go Live `PRODUCTION_PAPER` del lunes 2026-09-07 condicionado a preopen GREEN.  
**Runtime:** `17.0.0-rc6`  
**Observer live:** `db26c76723bb988c956589c572b87cbcb4191731`  
**Dashboard RC6 desplegado:** `da2c87936d90cda17512de3bc529d13f4693c1c1` / `porota-trading-dashboard:17.0.0-rc6-go-live-final`  
**Modo:** `PRODUCTION_PAPER` / `SIMULATED` / real-money `BLOCKED`.

> Este archivo se lee junto con `POROTA_TRADING_CHECKPOINT_CANONICO_RC6_2026-09-06_POSTHOTFIX.md`. El checkpoint canónico actualizado al cierre nocturno prevalece ante cualquier contradicción histórica.

## 1. CERRADO ESTA NOCHE

- Hotfix Labor Day para todos los CEDEARs USA: deployed/validado.
- Auditoría extensa de ingesta/históricos/aprendizaje: realizada.
- RCA de PPI historical: ingesta funcional; problema concentrado en calidad/semántica/rechazos y priorización, no corrupción general del store.
- Auditoría de tarjetas/source-of-truth del dashboard: realizada.
- Dashboard RC6: deploy transaccional GREEN y matriz HTTP completa GREEN.
- Telegram dashboard: usa evidencia runtime vigente del notification worker/outbox/jobs.
- SRE dashboard: muestra AMARILLO real con causa explícita de latencia y no lo confunde con corrupción/disco.
- Introspección dashboard: selecciona snapshots RC6 actuales y usa ventana coherente con productor horario.
- Control postdeploy de introspección automática: GREEN; snapshot 22:15 AR generado después del deploy y mostrado FRESH.
- Navegación `system-nav` preservada en drill-down.
- Limpieza de disco controlada completada sin `docker system prune -a`, `git clean` ni `VACUUM` ciego.
- Política de recovery: GitHub/GitHub Actions por SHA exacto; no rollback local como política de retención.
- Investigación IOL: arquitectura legacy existente identificada y plan GET-only RC6 definido.

## 2. CORRECCIÓN UX OBLIGATORIA PARA MAÑANA — TABLAS

**Estado: P1 INMEDIATO / UX OPERATIVA. No es P0 de safety, pero sí es necesario para que el operador pueda controlar la jornada.**

El requerimiento de tablas fue mal interpretado durante el rediseño. El formato actual que convierte registros en bloques/tarjetas apiladas con pares etiqueta/valor resulta ilegible para la operación real.

### Interpretación correcta y canónica

Cuando se pide “tabla”, se debe volver al formato clásico que POROTA tenía antes del rediseño:
- una tabla matricial real con **filas y columnas**;
- una fila por registro/entidad;
- encabezados de columna visibles y alineados;
- valores de la misma métrica verticalmente alineados;
- NO convertir cada registro en una tarjeta;
- NO repetir el nombre de la columna dentro de cada celda como diseño principal;
- NO usar el modo card/stacked automático en tablet Samsung por ancho de contenido;
- preservar legibilidad, contraste, tamaño táctil y compatibilidad con Voice Access;
- no introducir scroll horizontal global de toda la página; si una tabla excepcionalmente necesita más ancho, resolver columnas/prioridad o limitar el scroll al contenedor de esa tabla, sin transformar el registro en tarjeta.

### Pantallas a corregir con este formato
- Caja y patrimonio por moneda.
- Histórico de trading.
- Universo operativo / matriz por familia.
- Scalping / contrato intradiario.
- Instrumentos y contratos.
- Aprendizaje / cobertura empírica.
- Salud de APIs.
- Jobs internos.
- Scraping.
- Backups.
- Cualquier otra superficie que se haya convertido a pseudo-tabla/card durante el rediseño y que originalmente fuera una grilla de filas/columnas.

### Criterio de aceptación
- Validar visualmente contra el estilo de tabla anterior al rediseño.
- En Samsung/tablet debe verse como una grilla de filas y columnas, no como cards.
- Voice Access debe poder identificar controles y navegación.
- Los datos/source-of-truth auditados esta noche no deben cambiar por la corrección visual.
- Deploy sólo dashboard; observer/estrategia/gates no se tocan.

## 3. MAÑANA — PREOPEN 10:15–10:30 AR

Gate obligatorio:
- observer branch/SHA exactos `hotfix/rc6-cedear-us-labor-day-20260906` / `db26c...`;
- dashboard RC6 running, restart=0;
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
- field test Samsung/Voice Access;
- si es técnicamente seguro antes de la apertura, desplegar la corrección visual de tablas y hacer smoke HTTP/dashboard-only sin tocar observer.

Si cualquiera de los invariantes críticos falla: NO iniciar PAPER hasta resolver mediante procedimiento gobernado. No improvisar cambios durante rueda.

## 4. DURANTE LA RUEDA 10:30–17:00 AR

- PAPER only; ninguna orden real.
- No cambiar código/configuración salvo incidente operativo real.
- Monitorear PPI auth, quotes/freshness, decisiones, gates SHADOW, fills, posiciones, marks y PnL.
- Confirmar que CEDEARs USA se observen pero no abran nuevas posiciones.
- Verificar que acciones locales y demás familias habilitadas sigan política de calendario/contrato.
- Vigilar candles/history/backfill sin bloquear el hot path.
- Observar introspección horaria y early-warning.
- Verificar primera ejecución Contract Evidence realmente DUE si corresponde.
- Registrar evidencia por hora en `/validacion`.
- No hacer cambios estéticos durante la rueda; sólo observar/registrar defects para después del cierre.

## 5. CIERRE 17:00+

- Reconciliar PAPER, posiciones, marks y PnL.
- Confirmar `real_orders_sent=0`.
- Observar postclose historical ingestion y avance real.
- Candle integrity del día.
- Comparar PPI historical antes/después.
- Registrar learning SHADOW/contrafactual.
- Actualizar `/validacion` y checkpoint.

## 6. P1 — HISTÓRICOS / DATOS

### PPI historical
- mejorar priorización de identidades incompletas y reducir ciclos sobre series ya cubiertas;
- retry/backoff acotado;
- clasificar `OHLC_INCONSISTENT`, `HIGH_NONPOSITIVE`, `OPEN_NONPOSITIVE` y errores JSON/transitorios;
- mantener validación fail-closed;
- no aceptar datos inválidos para elevar coverage;
- medir avance con snapshots antes/después.

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

## 7. P1 — OPERACIÓN / ARQUITECTURA

- Restaurar formato de tablas clásico filas/columnas en todas las vistas listadas en §2.
- SRE measurement latency: entender/optimizar la medición que excede el umbral de 250 ms sin confundirla con DB/disk health.
- Forward Lab v2.
- MFE/MAE executable + provenance.
- campaña SHADOW sostenida.
- A11Y Samsung/Voice Access en campo.
- calendar local/USA fail-closed completo por familia.
- kill-switch fail-closed antes de cualquier reutilización/real money.

## 8. P2

- lifecycle de nuevos residuos Docker/untracked cuando aparezcan; ninguna imagen anterior queda protegida como rollback.
- sector map/correlation/family normalization.
- close-only salvage effectiveness.
- candle integrity longitudinal.
- Telegram noise/suppression con CRITICAL no suprimible.
- mejoras estéticas no operativas posteriores; el retorno a tablas clásicas NO es P2, es P1 inmediato.

## 9. `/validacion`

- M0: infraestructura/capacidad por evidencia reproducible.
- M1: safety por real-order block, readonly, DB, calendars y gates.
- M2: fuentes/contratos con PPI read-only + Contract Evidence DUE + provenance.
- M3: progreso medible y sostenido de históricos; timer instalado no equivale a completo.
- M4–M8: campaña PAPER forward, estabilidad, realismo, estadística y A11Y.
- M9: auditoría/consenso.
- M10: governance candidate.
- M11: real-money sigue BLOCKED.

## 10. VEREDICTO DE CIERRE

- **P0 conocidos abiertos para PAPER: 0.**
- Dashboard RC6: GREEN funcionalmente, pero con **P1 UX de tablas pendiente para mañana**.
- Introspección postdeploy automática: GREEN.
- Runtime: GREEN de integridad/safety para cierre nocturno.
- PPI historical: P1 parcial, no blocker hot path.
- IOL: P1 proof read-only pendiente, no blocker.
- A3: P1 alignment pendiente, no blocker.
- Contract Evidence: P1 primera ejecución DUE pendiente, no blocker hot path.
- Go Live PAPER: condicionado a preopen GREEN.
- Real-money: NO-GO / BLOCKED.
