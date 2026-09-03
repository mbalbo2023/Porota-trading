# RC4 — AUDIT DECISION TRACKER (WIP)

Estado: **WIP / NO DESPLEGABLE**. Este documento no es la respuesta final a la auditoría. La respuesta exhaustiva final se cerrará antes del deploy de RC4, con evidencia de código, tests, replay y aceptación/rechazo justificado de cada hallazgo.

## Reglas

1. No copiar pseudocódigo de la auditoría como si fuera API real.
2. Verificar firmas, tablas, campos y llamadores contra el candidate1 exacto.
3. Separar hallazgo correcto de remediación propuesta: aceptar el primero no obliga a aceptar la segunda.
4. No cambiar stop/target/slippage/hold/umbrales de estrategia sin replay o justificación reproducible.
5. SHADOW evalúa y persiste siempre; BINDING sólo agrega autoridad de veto.
6. PRODUCTION_PAPER no genera comisiones reales de PPI. Nunca rotular costo modelado como `reported_broker_cost`.
7. No inferir sector por ticker. Toda clasificación requiere fuente/provenance/fecha efectiva.
8. El release RC4 deberá ser reproducible desde un commit Git, sin patchers de build que reescriban fuente.
9. Todo módulo nuevo debe tener llamador explícito, test y clasificación: runtime, scheduler, CLI, test-only o deprecado.
10. El predeploy incluirá auditoría de módulos huérfanos y cadena de llamadas.

## Correcciones a las aclaraciones recibidas

### C1 — wiring de expectancy/breadth/sector
La aclaración que propone `ci_operational_context.empirical_expectancy(currency)`, `breadth_observation()` y `sector_observation()` sin argumentos sigue siendo incorrecta para candidate1.

- `empirical_expectancy(positions, minimum_sample=...)` vive en `ch_empirical_learning.py`.
- `breadth_observation(rows, minimum_symbols=...)` vive en `ci_operational_context.py` y requiere filas de mercado.
- `sector_observation(positions)` vive en `ci_operational_context.py` y requiere posiciones enriquecidas con sector.

Candidate1 ya construye esos insumos en `ops_introspection_hf4.py`. RC4 debe extraer esa construcción a un proveedor común y reutilizable; no inventar métodos del store.

### C2 — sector mapping
No se acepta el ejemplo manual provisto como verdad. Contiene al menos una clasificación incorrecta (`PAMP -> PRECIOUS_METALS`) y carece de provenance. RC4 admitirá solamente sector/industria con fuente explícita y versionada. Si no existe fuente suficiente, `UNMAPPED` y política no elegible para BINDING.

### C3 — breakeven
`stop/(stop+target)` es el breakeven sólo sin fricción. Para 2%/5% da 28,57%, no 41,2%. Con costos/spread/slippage el criterio correcto es:

`p_be = net_loss / (net_gain + net_loss)`

Los umbrales Sharpe `>0.1` y max drawdown `<15%` no se aceptan como gates hasta justificar muestra, horizonte y objetivo de riesgo.

### C4 — costos en PRODUCTION_PAPER
Correcto: no existe costo real cobrado por PPI porque no hay fill real. En PAPER se puede verificar:

- implementación matemática del tarifario;
- consistencia contra tarifario oficial versionado/scraping contractual;
- contrafáctico del portón;
- fidelidad del modelo de ejecución PAPER.

No se puede afirmar que la bonificación fue efectivamente cobrada. Esa evidencia queda como requisito separado para eventual `REAL_ELIGIBLE`, alimentable por resumen de cuenta/fills externos autorizados en el futuro.

## Confirmaciones finales del auditor — incorporadas 2026-09-03

Estas respuestas ya no son bloqueantes y se aceptan con su etiqueta original:

### A — `PAPER_EMERGENCY_MAX_OPEN_POSITIONS=12`
**Clasificación del auditor: RECOMENDACIÓN.**

- `50` se considera demasiado permisivo como anti-runaway.
- `12` NO queda adoptado como valor canónico.
- RC4 debe derivar el emergency cap cuantitativamente a partir de patrimonio/capital de riesgo, riesgo máximo concurrente, riesgo mínimo plausible por posición, exposición máxima y límites operativos.
- Hasta esa derivación, cualquier número alternativo es hipótesis/configuración técnica, no política financiera demostrada.

### B — `PAPER_MAX_HOLD_MINUTES=120`
**Clasificación del auditor: HIPÓTESIS PARA REPLAY.**

- El hallazgo de que `360` puede quedar dominado por EOD se mantiene.
- `120` NO se adopta a ciegas.
- RC4 debe replayar duraciones alternativas sobre los mismos trades y medir P&L neto, MFE, MAE, causas de salida y deterioro por tiempo antes de fijar el valor.

### C — slippage `8/10/15 bp`
**Clasificación del auditor: HIPÓTESIS CONSERVADORA.**

- No existe respaldo empírico específico BYMA/PPI para esos números.
- RC4 medirá la distribución del slippage PAPER observado/modelado contra el libro disponible: p10/p50/p90, por familia, liquidez, régimen y tipo de salida cuando sea posible.
- Importante: ese valor NO se rotulará como "slippage real de mercado" porque los fills PAPER no son ejecuciones reales. Es una proxy empírica PAPER útil para calibración.
- Cualquier valor nuevo del modelo se decidirá después del replay.

### D — ATR período 20
**Clasificación del auditor: CONVENCIÓN TÉCNICA.**

- `20` puede ser default experimental, nunca ley.
- ATR deberá ser parametrizable.
- Replay mínimo previsto: 10/15/20/25/30 sobre exactamente los mismos datos.
- La selección debe documentar su efecto sobre payoff neto, drawdown, estabilidad y sensibilidad; no optimizar sólo win-rate.

### E — sector
**Clasificación del auditor: REQUIERE EVIDENCIA EXTERNA.**

- No se encontró sector verificable en candidate1 ni en las fuentes PPI/BYMA revisadas por el auditor.
- `UNMAPPED` permanece como default.
- Si se introduce `POROTA_SECTOR_MAP_V1`, debe ser un dataset explícito, versionado y auditable con al menos: `ticker`, `sector`, `source`, `author`, `effective_at/date`, `reviewed`, además de family/market cuando sean necesarios para evitar ambigüedad.
- No se permitirá inferencia automática por ticker/nombre.

## Decisión preliminar por hallazgo de auditoría

| Hallazgo | Decisión RC4 | Tratamiento |
|---|---|---|
| P0-1 políticas declarativas sin autoridad | **ACEPTAR hallazgo / ACEPTAR PARCIAL remediación** | Implementar evaluación siempre + autoridad separada; corregir wiring real; sector sólo con mapping fiable. Tests conductuales. |
| P0-2 economía/barreras | **ACEPTAR como problema a demostrar / NO aceptar calibración propuesta sin replay** | Economic gate pasa a campaña SHADOW; counterfactual FIXED vs alternativas sobre los mismos datos; payoff neto y probabilidad histórica. No tocar stop/target por intuición. |
| P0-3 History Store v2 huérfano | **ACEPTAR** | Conectar por scheduler versionado. No ejecutar simultáneamente por timer + observer salvo locking/idempotencia demostrados; preferir un ejecutor canónico y un watchdog de freshness. |
| P1-1 Históricos 0/823 | **ACEPTAR** | `available=False` cuando el schema v2 no existe; fallback legacy real. Tests de ambos estados. |
| P1-2 Scheduler SIN_REGISTRO | **ACEPTAR Y AMPLIAR** | Reconciliar `operational_jobs`, `source_sync`, `api_health` y snapshot systemd; mostrar fuente, last/next/duration/result/reason. |
| P1-2b NEWS cadence | **ACEPTAR** | Mostrar cadencia efectiva; OFF != retrasado. |
| P1-3 release no reproducible | **ACEPTAR** | Materializar patchers en Git una sola vez; build desde commit; excluir `.bak/.pre-*`; manifest y source SHA reproducibles. |
| P1-4 `PAPER_MAX_OPEN_POSITIONS` fantasma | **ACEPTAR** | Retirarlo de acceptance como autoridad. Mantener sólo compatibilidad de API si hace falta. Emergency cap separado y documentado; valor final se deriva cuantitativamente. |
| P1-5 `PAPER_MAX_HOLD_MINUTES=360` muerto | **ACEPTAR hallazgo / PARCIAL remediación** | No fijar 120 a ciegas. Replay contrafáctico de múltiples duraciones y elegir antes de iniciar ventana; luego congelar valor durante validación. |
| P1-6 bonificación PPI no reconciliada | **ACEPTAR riesgo / REFORMULAR para PAPER** | No fingir costos reportados. En PAPER: tarifario oficial versionado + modelo. Reconciliación real queda feature-gated para evidencia externa/futura. |
| P2-1 settlement `time.max` | **ACEPTAR** | Mantener fail-closed hasta cutoff oficial; mostrar caja inmovilizada y provenance del supuesto. |
| P2-2 concentración sectorial | **ACEPTAR** | Construir mapping autoritativo/versionado si hay fuente; SHADOW mientras coverage insuficiente. No inferir por ticker. |
| P2-3 señal momentum sobre ticks | **ACEPTAR** | Crear señal/candle counterfactual y replay; no reemplazar motor activo antes de evidencia. History/Candle quality es prerequisito. |
| P2-4 artefacto `:memory:.ses` | **ACEPTAR** | Ignorar en release y encontrar productor; test de hygiene del paquete. |
| P2-5 colisiones de prefijos | **ACEPTAR como deuda** | No renombrar RC4 masivamente. Generar `MODULOS.md`/inventario automático con capa, rol, callers y estado. |
| P2-6 slippage 2 pb optimista | **ACEPTAR sospecha / NO aceptar 8/15 pb sin medición** | Reporte de fidelidad PAPER y counterfactual sobre mismos books/fills; calibrar luego. |

## Nuevos requisitos RC4 derivados de la conversación

- `/vivo`: operaciones primero; P&L neto, mark timestamp/freshness, drill-down; cerradas con lección aprendida; después decisiones/rechazos; scalping; motores al final; eliminar embudo.
- Scalping vuelve a top-level y también tiene resumen separado en `/vivo`.
- Introspección: no presentar snapshot stale como estado actual; comparar `generated_at` con heartbeat live.
- Health/SRE: amarillo debe explicar causa y antigüedad.
- Control funcional automático periódico, sanitizado y visible en dashboard.
- Scheduler: ningún job gris sin motivo/last/next/evidence source.
- Sistema → Scraping: runs, endpoints/rutas, familias, cambios, evidencias, conflictos, stale, próxima ejecución, sin secretos.
- Sistema → Logs: Bot/Observer principal descargable, además de dashboard/snapshots.
- Backups: matriz de cobertura de todos los storages, incluyendo History Store.
- Contract Evidence: materializar scraping autenticado de PPI de forma schedulerizada, versionada y fail-closed.
- Validación: menú top-level Shadow→Binding con hitos, objetivos, tiempo cumplido/faltante, evidencia, counterfactual y lecciones.

## Condición de cierre de este tracker

Antes del deploy RC4 se generará `RESPUESTA_AUDITORIA_RC4_FINAL.md`, con cada hallazgo clasificado como `ACEPTADO`, `ACEPTADO_PARCIALMENTE`, `RECHAZADO` o `DEFERIDO_CON_BLOQUEO`, citando commit, archivo/línea, tests, evidencia y justificación financiera/operativa.
