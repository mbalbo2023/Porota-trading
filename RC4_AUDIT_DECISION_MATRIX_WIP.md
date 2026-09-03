# RC4 — MATRIZ DE DECISIÓN SOBRE AUDITORÍA HF6-v2 (WIP)

Estado: **WIP / NO DESPLEGABLE**. Esta matriz no reemplaza la respuesta final a la auditoría. Antes del deploy de RC4 se emitirá un documento exhaustivo con evidencia de código, tests, replay y criterios de aceptación para cada punto.

## Regla de trabajo

La auditoría y sus artefactos son entradas de ingeniería, no autoridad automática. Cada hallazgo se contrasta contra el paquete operativo exacto de candidate1 y contra evidencia runtime. Se separan: (a) diagnóstico, (b) código sugerido, (c) cambio de política/estrategia. Un diagnóstico correcto no implica que el snippet propuesto sea integrable ni que un parámetro sugerido sea estadísticamente válido.

Invariantes RC4: PRODUCTION_PAPER, SIMULATED, real_orders=0; ningún cambio de runtime durante rueda; PPI live sigue siendo autoridad de market-data; A3 sólo background/history; no se mezclan monedas; no se amplían familias por inferencia; no se alteran stop/target/hold/slippage por intuición sin replay.

## Hallazgos de la auditoría

| ID | Diagnóstico | Código/propuesta del auditor | Decisión RC4 | Implementación / evidencia requerida |
|---|---|---|---|---|
| P0-1 | Tres políticas declarativas no gobiernan admisión | `ck_policy_gate_hf6.py` + inserción en `_open()` | **ACEPTAR diagnóstico / REESCRIBIR integración** | Crear autoridad de admisión real con interfaces existentes. El snippet no se pega: `PaperStore.empirical_samples`, `breadth_snapshot`, `sector_snapshot` y `q.contract.sector` no existen. Agregar adaptadores reales, mapping sectorial explícito/versionado, ledger de motivo estable y tests de comportamiento. Expectancy/regime/sector no pasan a BINDING sólo por existir el módulo. Sector podrá ser vinculante únicamente con mapping suficientemente cubierto y semántica fail-closed definida. |
| P0-2 | El gate económico ratio-only no demuestra esperanza; defaults 3.5%/5% inconsistentes | Probabilidad de barrera con `bl_candle_engine.intraday_sessions()` | **ACEPTAR problema / RECHAZAR snippet / REPLAY OBLIGATORIO** | Unificar defaults con el valor runtime sin cambiar conducta. Construir replay de first-touch/exit efectivo sobre `CandleArchive` real; la API `intraday_sessions()` propuesta no existe. Incorporar probabilidad/expectativa sólo con muestra auditable. No aceptar como hecho que el 5% fue elegido “para pasar el gate” sin evidencia histórica de esa intención. No cambiar stop/target antes del replay. |
| P0-3 | History Store v2 está sin camino de ejecución y sin timers | Timer + `postclose.run(...)` + fallback observer | **ACEPTAR / REDISEÑAR WIRING** | Confirmado: no existen timers history-postclose/A3 y la cadena está inalcanzable. El snippet usa `postclose.run`, inexistente; el módulo real expone `run_if_due`. RC4 conectará PPI history al sink v2 desde el camino de ingesta ya existente y versionará jobs post-close para fallbacks/A3, evitando doble ejecución/race mediante autoridad única o reserva transaccional. |
| P1-1 | `0/823` por `available=True` sin tabla v2 | Patch `available=False` | **ACEPTAR** | Aplicar equivalente nativo + tests. Sin schema v2, la UI debe usar legacy exact-match (67 actuales), no afirmar cero. |
| P1-2 | Scheduler mira sólo `operational_jobs` | Unir `operational_jobs`, `source_sync`, `api_health` | **ACEPTAR Y AMPLIAR** | Unificar además snapshot systemd sanitizado. Mostrar última ejecución, último éxito, próxima, duración, estado, resultado, condición y fuente de evidencia. Evitar duplicados con precedencia explícita. Gris sólo SIN_EVIDENCIA/NO_INSTALADO/NUNCA_EJECUTADO. |
| P1-2b | NEWS declara 45m con política OFF ~12h | Cadencia efectiva | **ACEPTAR** | Mostrar cadencia efectiva y condición; OFF = NO_APLICA/control extendido, no “retrasado”. |
| P1-3 | Release no reproducible por patchers + materialización + `.bak` | Commit materializado y build directo | **ACEPTAR — PRIORIDAD DE RELEASE** | RC4 debe ser commit reproducible byte-a-byte como fuente de build; patchers quedan tooling/dev, fuera del release. Excluir/eliminar `.bak`, `.pre-*`, `:memory:.ses`, runtime artifacts. Manifest debe comprobar allowlist/denylist y SOURCE_SHA real. |
| P1-4 | `PAPER_MAX_OPEN_POSITIONS=5` sigue declarado aunque no gobierna | reemplazar por emergency cap=12 | **ACEPTAR diagnóstico / NO ACEPTAR 12 SIN MODELO** | Retirar el legacy de criterios/UI como autoridad normal y documentarlo como compatibilidad. Mantener sólo guard técnico anti-runaway con valor explícito y tests de no-interferencia. Un cap global 12 podría volverse estrategia accidental al existir presupuestos por moneda; definirlo cuantitativamente antes de fijarlo. |
| P1-5 | `PAPER_MAX_HOLD_MINUTES=360` queda eclipsado por EOD | bajar a 90–120 | **ACEPTAR diagnóstico / REPLAY PARA EL VALOR** | Confirmado: EOD se evalúa antes que MAX_HOLD. No cambiar a 120 por intuición. Incluir hold-time 60/90/120/180/etc. en replay con costos, MFE/MAE y exits reales; luego seleccionar y documentar. |
| P1-6 | Rebate/fee intradía modelado sin reconciliación | comparar PAPER `paper_id` con costo broker reportado | **ACEPTAR necesidad / RECHAZAR interfaz ficticia** | PAPER no llega a PPI, por lo que no existe costo real broker por `paper_id`. RC4 reconciliará modelo contra tarifario oficial/versionado, endpoints/XHR/estimadores PPI y Contract Evidence; alertará drift de fee/rebate/semántica. Para dinero real seguirá siendo bloqueante. |
| P2-1 | `time.max` inmoviliza caja T+1 hasta fin del día | mantener conservador + mostrar costo | **ACEPTAR** | No cambiar cutoff hasta evidencia oficial. Agregar caja inmovilizada por supuesto conservador, monto por moneda, available_at y fuente. |
| P2-2 | Concentración sectorial aún posible | resolver con P0-1 | **ACEPTAR** | Agregar catálogo sectorial explícito, coverage/unmapped, concentración por nominal/riesgo además de conteo y eventual gate binding sólo con mapping confiable. No inferir sector por ticker. |
| P2-3 | Señal operativa usa ticks irregulares; candle engine no gobierna señal | pasar a velas/ATR | **ACEPTAR como deuda / VALIDAR EMPÍRICAMENTE** | Confirmado: `be_paper_engine.signal_prices` usa muestras de trades; `CandleArchive` existe. Crear estrategia candidata candle/ATR/replay detrás de versión/feature flag; no sustituir señal live sin replay out-of-sample. Las afirmaciones “compra techo local” o reversión del panel son hipótesis hasta probarse. |
| P2-4 | `:memory:.ses` viaja en ZIP | ignore y hallar productor | **ACEPTAR** | Artefacto existe y el manifest histórico lo arrastra; no encontramos productor en el source actual. El release RC4 debe eliminarlo y fallar si aparece `*.ses`/runtime data. Continuar arqueología para determinar si el productor ya fue retirado o sólo quedó el artefacto heredado. |
| P2-5 | Colisiones de prefijos reducen navegabilidad | renombrar o documentar | **ACEPTAR DOCUMENTACIÓN / NO RENOMBRAR RC4** | Generar `MODULOS.md`/`MODULE_LIFECYCLE.md` desde AST/docstrings con capa, dependencias, entrypoint y estado. No hacer renombre masivo antes del release. |
| P2-6 | 2bp slippage puede ser optimista | 8–10bp normal, 15bp EOD | **ACEPTAR calibración / NO ACEPTAR números SIN REPLAY** | Separar slippage normal/EOD como parámetros medibles; preservar conducta hasta replay. Calibrar por familia/liquidez/profundidad/exit type con fills PAPER y market snapshots. |

## Revisión de los dos artefactos adjuntos

### `ck_policy_gate_hf6.py`

- Sintaxis/invariantes puras: válidas.
- Concepto de separar observación y autoridad de admisión: aceptado.
- Integración propuesta: no utilizable tal cual por APIs inexistentes en candidate1.
- `sector_block` requiere rediseño: si el candidato no tiene sector, bloquear por la concentración máxima actual puede rechazar incluso una operación diversificante; si se conoce el sector, hoy el contrato no tiene ese campo. RC4 debe resolver mapping fuera del contrato de ejecución o ampliar metadata de forma compatible.
- No se activará BINDING sin datos suficientes sólo porque el módulo exista.

### `porota_hf6_v3_bloque1_observabilidad.sh`

- Los patches conceptuales de históricos/Scheduler/NEWS/higiene son válidos y se incorporarán como cambios nativos de source, no como patcher de release.
- La lógica de anclas es idempotente para sus propios reemplazos.
- Problema detectado durante nuestra verificación: el script usa `pytest -q`; en staging la colección falló por import path, mientras `python3 -m pytest -q` pasó 5/5.
- Problema más serio: ese fallo de pytest no pone `FAILED=1`, por lo que el script puede terminar `BLOQUE1_ESTADO=OK` aun cuando `PYTEST=REVISAR`. No se usará tal cual para certificar RC4.
- La salida OSC52 copia sólo un resumen, no todo el resultado útil requerido por accesibilidad. RC4 conservará la regla de reporte completo/condensado explícito y `COPIADO_AL_PORTAPAPELES=SI` para scripts operativos.

## Auditoría adicional de huérfanos / lifecycle

RC4 realizará grafo de imports + referencias desde scripts/systemd/Docker. Todo módulo deberá quedar clasificado como uno de:

- `ACTIVE_RUNTIME`
- `ACTIVE_SCHEDULED`
- `ACTIVE_TOOLING`
- `TEST_ONLY`
- `DEPRECATED` (con reemplazo/razón)
- `ORPHAN_REVIEW`

Candidatos ya confirmados para revisión de wiring incluyen `cs_postclose_history_scheduler_hf6`, `ct_ppi_history_salvage_hf6`, `cz_a3_cem_public_history_hf6`, `db_a3_cem_normalizer_hf6` y otras piezas HF6 de history/contract/A3. Entry points legítimos invocados desde shell/systemd no serán marcados huérfanos sólo porque no tengan import Python.

No se elimina ningún módulo sólo por falta de inbound import: primero se demuestra si es CLI, systemd, Docker entrypoint, tooling manual, test-only o deprecado.

## Alcance RC4 adicional ya acordado con el operador

Además de los 15 puntos de la auditoría, RC4 incluye:

1. `/vivo` prioriza operaciones abiertas; P&L rojo/verde, mark timestamp/freshness, drill-down, gates/aceptación/rechazo, cierres y lección aprendida; motores/workers abajo; eliminar embudo.
2. Scalping vuelve como destino principal `/scalping` y tiene resumen separado en `/vivo`.
3. Introspección valida freshness y no puede mostrar `WAITING_MARKET` viejo como estado vivo.
4. Automatizar control funcional crítico con evidencia sanitizada al dashboard.
5. Salud/SRE explica todo amarillo/retrasado con age, TTL/cadencia, causa y fuente.
6. Scheduler multi-fuente con estado real de jobs/timers.
7. Scraping PPI/API/XHR/web materializado a Contract Evidence versionado; scheduler y log en Sistema; nunca auto-READY por descubrimiento.
8. Contract Evidence UI: observados / evidencias / verificados / READY / bloqueados; normalización de aliases.
9. Logs: Bot/Observer/Dashboard/scraping visibles y descargables desde snapshots sanitizados.
10. Backups: matriz de cobertura de `observer_v17.db`, `market_history.db`/History Store, `sre_vector_db`, backup general y futuros stores; last/next/SHA/restore/retention.
11. Dynamic Concurrent Risk visible por moneda: budget, realized-loss consumed, open stop risk, remaining.
12. Sector/correlation explícitos; correlación desde histórico suficiente, nunca asumir cero.
13. Settlement cutoff + caja inmovilizada, sin inventar hora oficial.
14. Storage Lifecycle audit-only, crecimiento DB/WAL/disk, dedupe preventivo y no blind prune.
15. Family readiness/Contract Evidence y scraping para cerrar contratos sin inferencias.
16. State-of-the-Art trade-frequency/capacity sólo después de que la estrategia/replay tenga base suficiente.

## Documento final obligatorio antes del deploy

Se generará `RESPUESTA_AUDITORIA_POROTA_RC4_<fecha>.md` con los 15 hallazgos y todos los adicionales. Para cada punto contendrá: veredicto (ACEPTADO / PARCIAL / RECHAZADO), evidencia exacta, código modificado, tests, riesgos, razón financiera/técnica, comportamiento antes/después, parámetros, limitaciones, deuda restante y criterio de deploy. Ningún punto quedará omitido por no ser P0.
