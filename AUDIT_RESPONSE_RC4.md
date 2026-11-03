# POROTA TRADING 17.0.0 RC4 — RESPUESTA EXHAUSTIVA A AUDITORÍA HF6-v2

**Estado del documento:** RC4 TEST-READY — previo a stage/CI en Droplet y previo a deploy.  
**Auditoría base:** `AUDITORIA_POROTA_HF6_V2_20260903.md`.  
**Baseline operativo:** `17.0.0-rc3-hf6-v2-candidate1`.  
**RC4:** `17.0.0-rc4-test-ready1`.  
**Modo permitido:** `PRODUCTION_PAPER` / `SIMULATED`.  
**Órdenes reales:** bloqueadas.  

## Regla de evaluación

Cada hallazgo se clasifica como **ACEPTADO**, **ACEPTADO PARCIALMENTE**, **REFORMULADO** o **NO ACEPTADO**, distinguiendo corrección de código de evidencia financiera pendiente. Ninguna recomendación numérica se convierte en política sólo por aparecer en la auditoría. Los cambios de estrategia/riesgo que requieren evidencia quedan instrumentados para replay/SHADOW y no se auto-promueven.

## Resumen ejecutivo

| Hallazgo | Decisión RC4 | Estado antes de testing host |
|---|---|---|
| P0-1 Policy gates sin autoridad real | ACEPTADO + REFORMULADO | Código evaluate-always integrado; BINDING fail-closed |
| P0-2 Target +5% como número para pasar gate | ACEPTADO PARCIALMENTE | No se cambia target; replay/contrafactual obligatorio |
| P0-3 History Store v2 huérfano | ACEPTADO | Wiring, post-close, ranking y dedupe implementados |
| P1-1 0/823 | ACEPTADO | Causa raíz corregida + fallback legacy por familia |
| P1-2 Scheduler SIN_REGISTRO falso | ACEPTADO | Modelo multi-source + explicación de gris/amarillo |
| P1-2b NEWS_REFRESH cadence falsa | ACEPTADO | Cadencia efectiva OFF/ON diferenciada |
| P1-3 Release no reproducible desde Git | ACEPTADO | Source RC4 empaquetado; cierre final exige commit+digest |
| P1-4 PAPER_MAX_OPEN_POSITIONS=5 engañoso | ACEPTADO + REFORMULADO | Dynamic risk autoridad; emergency cap AUTO derivado |
| P1-5 MAX_HOLD 360 muerto | ACEPTADO PARCIALMENTE | Detectado DOMINATED_BY_EOD; cambio exige replay |
| P1-6 Bonificación PPI no reconciliada | ACEPTADO + REFORMULADO | PAPER = NOT_OBSERVABLE_IN_PAPER; no fake reconciliation |
| P2-1 Settlement time.max | ACEPTADO | T+1 PENDING_CONFIRMATION; CI inmediato |
| P2-2 Concentración sectorial | ACEPTADO PARCIALMENTE | Gate implementado; UNMAPPED hasta fuente revisada |
| P2-3 Signal ticks vs candles | ACEPTADO PARCIALMENTE | Replay preparado; hot path no cambia sin evidencia |
| P2-4 Artefacto runtime en ZIP | ACEPTADO | Eliminado/ignorado + preflight |
| P2-5 Colisión prefijos | ACEPTADO PARCIALMENTE | Inventario completo; renombre diferido por riesgo de churn |
| P2-6 slippage 2 bp optimista | ACEPTADO PARCIALMENTE | Medición PAPER en bps/replay; parámetro no cambia aún |

---

## P0-1 · Los tres portones de riesgo de HF6 son etiquetas de configuración, no código

**Decisión:** ACEPTADO y REFORMULADO.

**Qué comprobamos:** candidate1 exponía policies de expectancy, régimen y sector sin una autoridad de admisión plenamente instrumentada. Además, el pseudocódigo propuesto inicialmente por auditoría usaba métodos inexistentes y en SHADOW devolvía vacío.

**Qué hace RC4:** `ck_policy_gate_hf6.py` evalúa siempre y separa `state`, `would_block`, `authority`, `evidence_block` y `execute_block`. `be_paper_engine.py` recoge contexto mediante `rc4_policy_context.py`, persiste la evaluación en features y sólo ejecuta bloqueo si la policy está explícitamente en `BINDING`. Si BINDING carece de evidencia, falla cerrado. SHADOW/OBSERVATION nunca modifica por sí solo una operación PAPER.

**Justificación:** permite construir el contrafáctico necesario para Shadow→Binding sin otorgar autoridad prematura.

**Pruebas:** `tests/test_rc4_acceptance.py` cubre evaluate-always, BINDING y ausencia de evidencia.

**Riesgo residual:** sector no puede ser BINDING mientras el mapping verificable esté incompleto. Expectancy/régimen necesitan muestra suficiente. El dashboard sólo puede declarar `ELIGIBLE_FOR_BINDING_DECISION`, nunca activar BINDING automáticamente.

---

## P0-2 · El objetivo de +5% no es una tesis de trading: es el número que hace pasar el portón económico

**Decisión:** ACEPTADO PARCIALMENTE.

**Qué aceptamos:** un target no es evidencia de edge y no debe elegirse para “hacer pasar” un reward/risk mínimo.

**Qué no hacemos:** RC4 no reemplaza +5% por otro número arbitrario ni por ATR(20) por convención.

**Qué hace RC4:** agrega replay offline/contrafactual (`rc4_replay.py`, `rc4_replay_analysis.py`) para comparar geometrías, duración, ATR parametrizado y fricción sobre la misma evidencia cuando exista trayectoria suficiente. Si faltan datos, devuelve `INSUFFICIENT_DATA`.

**Criterio matemático:** breakeven se evalúa con payoff neto: `net_loss / (net_gain + net_loss)`, no con un win-rate fijo independiente de costos.

**Riesgo residual:** el target operativo no debe modificarse hasta que replay/walk-forward justifique una alternativa. Este punto no bloquea recopilación PAPER; sí bloquea afirmar estrategia rentable o pasar a dinero real.

---

## P0-3 · El History Store v2 está huérfano — el checkpoint afirma lo contrario

**Decisión:** ACEPTADO.

**Qué hace RC4:** conecta la cadena History Store v2 con `cu_history_store_v2_hf6.py`, adapters/sinks existentes y runner post-close. El runner reutiliza payload histórico ya descargado cuando corresponde, usa lock/idempotencia, identidad `symbol + instrument_type + market + settlement + date`, versiones append-only y canonicalización.

**Autoridad:** PPI conserva ranking superior a A3. A3 CEM es sólo background/history y nunca autoridad live ni fuente de ejecución.

**Deduplicación:** versiones OHLCV idénticas no deben crecer indefinidamente.

**Riesgo residual:** el timer deberá instalarse y probarse en host. Ninguna cobertura histórica auto-habilita una familia contractual.

---

## P1-1 · Causa raíz exacta del `0/823` — corrección de dos líneas

**Decisión:** ACEPTADO, ampliando la corrección.

**Causa:** `market_history.db` existente era interpretado como v2 disponible aunque faltara el schema `history_canonical_v2/history_versions_v2`. La UI descartaba el fallback legacy y mostraba cero.

**RC4:** `dd_history_metrics_hf6.py` diferencia `V2_SCHEMA_NOT_PRESENT` de “v2 disponible” y construye cobertura legacy por familia desde `production_history.instrument_type`, sin inferir familia desde ticker. v2 toma prioridad sólo cuando el schema requerido existe.

**Resultado esperado:** durante migración se ve cobertura legacy real y estado v2 pendiente; nunca `0/x` falso por mera existencia del archivo.

---

## P1-2 · El Scheduler declara SIN_REGISTRO en 7 de 14 jobs que sí corren

**Decisión:** ACEPTADO.

**RC4:** `de_scheduler_catalog_hf6.py` reconcilia `operational_jobs`, `source_sync`, `api_health`, `contract_evidence_runs` y snapshot systemd sanitizado. Cada job expone fuente de evidencia, última corrida, último éxito, cadencia, próxima ejecución, estado y detalle.

**Semántica nueva:** gris significa explícitamente `SIN_EVIDENCIA`, `NO_INSTALADO`, `NUNCA_EJECUTADO` o `NO_APLICA`; amarillo debe explicar stale/causa/age en lugar de ser una etiqueta opaca.

**Incluye:** Contract Evidence, health 5 min, deep audit 60 min, history post-close y A3 CEM history.

---

## P1-2b · NEWS_REFRESH declara una cadencia que no cumple

**Decisión:** ACEPTADO.

**RC4:** la cadencia efectiva depende de `PAPER_NEWS_INGEST_ENABLED`. Con NEWS OFF, la UI no promete 45 minutos de ingestión y muestra el control efectivo aproximado de 12 h como `NO_APLICA`; con ON conserva la cadencia correspondiente.

**Justificación:** una feature deshabilitada por política no debe mostrarse como job retrasado.

---

## P1-3 · La release no es reproducible desde control de versiones

**Decisión:** ACEPTADO. CIERRE PARCIAL PRE-TESTING.

**RC4 realizado:** el source de ingeniería parte del ZIP operativo exacto de candidate1, SHA256 `8e8c7a2596e6575c5e680d6e5c01a79ebb3ac1e89a26f11f7ba5bb4e1cd38499`; RC4 materializa los cambios en archivos finales, no depende de aplicar patchers externos al deploy. `_version.py` separa base auditada histórica, source congelado y SHA del ZIP operativo. `RC4_DEPLOY_CONTRACT.md` documenta el runtime split real y `rc4_release_preflight.py` verifica invariantes.

**Lo que NO se declara cerrado todavía:** antes de deploy el paquete TEST-READY debe subirse a Git, committearse, construirse directamente desde ese commit y registrar image digest/manifest. Hasta entonces el hallazgo queda “aceptado y preparado para cierre”, no cerrado falsamente.

---

## P1-4 · `PAPER_MAX_OPEN_POSITIONS=5` sigue en la configuración pero ya no gobierna nada

**Decisión:** ACEPTADO y REFORMULADO.

**RC4:** Dynamic Concurrent Risk es autoridad normal. `PAPER_MAX_OPEN_POSITIONS` queda sólo como metadata/compatibilidad legacy y no debe presentarse como límite efectivo normal. El anti-runaway pasa a `PAPER_EMERGENCY_MAX_OPEN_POSITIONS=AUTO`.

**Derivación AUTO:** `ceil(soft_stop_fraction / risk_per_trade_fraction)`. Con configuración actual 1.5% / 0.2% = 8. El número es guard técnico, no target de diversificación. Un entero explícito sigue siendo override y debe mostrarse como tal.

**Por qué no 12:** el auditor confirmó que 12 era recomendación prudencial, no fórmula.

---

## P1-5 · `PAPER_MAX_HOLD_MINUTES=360` es configuración muerta

**Decisión:** ACEPTADO PARCIALMENTE.

**RC4:** `rc4_validation.max_hold_readiness()` compara el valor configurado con el máximo efectivo previo a EOD. Con la sesión actual, 360 se marca `DOMINATED_BY_EOD` frente a ~345 min efectivos.

**Qué NO hacemos:** no cambiamos a 120/90/180 sin replay. `rc4_replay_analysis.py` prepara la evaluación por duración/MFE/MAE cuando exista evidencia suficiente.

**Riesgo residual:** sigue siendo un parámetro no vinculante de facto hasta que una duración nueva se justifique con evidencia y se versionen los resultados.

---

## P1-6 · La bonificación intradía de PPI está asumida, nunca reconciliada contra fills

**Decisión:** ACEPTADO y REFORMULADO.

**Corrección conceptual:** PRODUCTION_PAPER no produce arancel realmente cobrado por PPI. No existe un `reported_broker_cost` legítimo que reconciliar.

**RC4:** el modelo PAPER sigue `au_fee_schedule.py` + tarifario oficial/versionado. `h_daily_report.py` y `/validacion` exponen `NOT_OBSERVABLE_IN_PAPER` y no consultan un supuesto tax report real en PAPER/Sandbox. Una futura reconciliación billed-vs-modeled sólo puede activarse con evidencia externa/PRODUCTION_REAL explícita.

**Riesgo residual:** la fidelidad de bonificación frente al cobro real permanece no demostrable en PAPER. No bloquea simulación; bloquea afirmar reconciliación real.

---

## P2-1 · `cf_sale_settlement` usa `time.max` y congela un día entero de caja

**Decisión:** ACEPTADO.

**RC4:** se elimina la falsa precisión de `23:59:59`. CI/T+0 puede acreditarse en el instante modelado. Para T+1 se calcula sólo la fecha hábil esperada; `available_at=None`, `basis=PENDING_CONFIRMATION` hasta conciliación/acreditación defendible.

**Invariante:** un recibo pendiente no libera caja prematuramente, pero tampoco se inventa un cutoff que congele hasta fin de día. Problemas contables no bloquean una salida que reduzca riesgo.

**Pruebas:** batería de settlement actualizada y GREEN.

---

## P2-2 · Concentración sectorial: 4 bancos simultáneos sigue siendo posible

**Decisión:** ACEPTADO PARCIALMENTE.

**RC4:** existe policy sectorial evaluable y fail-closed en BINDING, pero no se inventan sectores. `POROTA_SECTOR_MAP_V1.csv` está vacío salvo encabezados; el único mapping aceptable exige ticker, sector, source, fecha/provenance y `reviewed=true`. Metadata informal o inferencia por ticker no tiene autoridad.

**Estado actual:** `UNMAPPED`; sector no es elegible para BINDING hasta lograr cobertura explícita y revisada.

**Por qué no se “corrige” manualmente:** el auditor confirmó que PPI/BYMA/candidate1 no exponen hoy una fuente sectorial verificable. Un mapping incorrecto sería peor que ausencia de mapping.

---

## P2-3 · La señal es momentum sobre ticks, no sobre velas — y `bl_candle_engine` existe sin usarse

**Decisión:** ACEPTADO PARCIALMENTE.

**RC4:** no reemplaza el signal path intradiario sin evidencia. Se incorpora replay/contrafactual para comparar señal vigente contra variantes candle-based y ATR parametrizado sobre los mismos datos cuando exista trayectoria suficiente.

**ATR:** período configurable; 20 es default de exploración, no ley. Replay previsto 10/15/20/25/30. Si no existe historia intradía suficiente, el resultado es `INSUFFICIENT_DATA`.

**Riesgo residual:** el hot path actual permanece hasta que replay/walk-forward justifique un reemplazo. Esto evita confundir cambio de mercado con mejora de señal.

---

## P2-4 · Artefacto de runtime filtrado en un ZIP que declara no tenerlo

**Decisión:** ACEPTADO.

**RC4:** `:memory:.ses` no aparece en source final. `.gitignore` y `.dockerignore` bloquean `:memory:*`, `*.ses`, `*.bak`, `*.pre-hf6*`; `rc4_release_preflight.py` verifica ausencia de ese artefacto antes del paquete.

**Hallazgo adicional:** no se encontró productor actual dentro de los módulos Python; se trata como artefacto legado arrastrado, no como comportamiento runtime vigente.

---

## P2-5 · Colisión del esquema de prefijos alfabéticos

**Decisión:** ACEPTADO PARCIALMENTE.

**RC4:** `rc4_module_inventory.py` produce un inventario reproducible. Estado previo a testing: 154 módulos clasificados, `REVIEW_REQUIRED=0`, 20 colisiones de prefijo documentadas, un único bridge diferido explícito (`cy_a3_contract_bridge_hf6.py`) por falta de autenticación A3 Primary.

**Qué NO hacemos en RC4:** renombrar masivamente módulos históricos. El beneficio cosmético no justifica el churn de imports/tests en un release que cambia riesgo, evidence y observabilidad. Las colisiones quedan deuda nominal documentada, no huérfanos desconocidos.

**Invariante:** ningún módulo se elimina por ausencia de import directo; CLI, build, scheduler y tests se clasifican explícitamente.

---

## P2-6 · `slippage_bps=2` (0,02%) es optimista para BYMA

**Decisión:** ACEPTADO PARCIALMENTE.

**RC4:** no sustituye 2 por 8/10/15 bp arbitrarios. El auditor confirmó que esos valores eran hipótesis. `rc4_replay_analysis.py` reconstruye el precio de referencia PAPER a partir de fill y slippage registrados y calcula distribución en bps (p10/p50/p90) por lado cuando haya muestra.

**Etiqueta obligatoria:** `PAPER_MODELED/OBSERVED`; nunca “slippage real del broker”. Para ejecución real será necesaria evidencia externa.

**Riesgo residual:** el parámetro operativo sólo debe cambiar tras replay suficiente, idealmente diferenciado por familia/liquidez/régimen.

---

# Hallazgos transversales incorporados a RC4

## `/vivo`

RC4 elimina el embudo de `/vivo`. Orden canónico: operaciones abiertas → operaciones cerradas/lección → decisiones/rechazos → Scalping → motores/workers. Cada operación muestra P&L con texto y semántica visual, mark/timestamp/freshness y drill-down. Una marca stale no se presenta como actual.

## Scalping

Scalping vuelve a menú principal `/scalping` y también tiene resumen separado en `/vivo`; deja de estar escondido como link/subsección del Motor.

## Introspección

Un snapshot viejo no puede reemplazar el estado vivo. Si snapshot y heartbeat/runtime divergen, la UI muestra `SUPERSEDED_BY_LIVE_STATE`.

## Salud/SRE

Amarillo ya no significa sólo “RETRASADO”: se exige causa, antigüedad, cadencia esperada y acción. `NO_APLICA` no cuenta como degradación.

## Logs

`Bot / aplicación` es fuente descargable y sanitizada, además de Observer/Dashboard.

## Scraping

Sistema muestra ledger de ejecuciones contractuales. Browser trusted-device es GET-only; sesión expirada/2FA produce `BLOCKED_AUTH`, sin login automático. Scraping jamás auto-promueve READY.

## Backups

SRE y Sistema comparten una sola matriz de cobertura: Observer DB, History DB, SRE vector y backup general. Una persistencia crítica sin protección no queda invisible.

## Validación Shadow→Binding

Nuevo menú principal `/validacion`: hitos, muestra, tiempo/ruedas, would-block, autoridad, freshness, lecciones y elegibilidad. PAPER y REAL evidence son dimensiones distintas. El dashboard nunca activa BINDING automáticamente.

---

# Testing previo al stage host

- compilación Python del worktree: sin errores;
- matriz diferencial candidate1 vs RC4: cero `RC4_REGRESSION_REVIEW` pendiente después de actualizar la expectativa de settlement;
- dependencias no disponibles en este sandbox: `apscheduler`, `ppi-client`, `hypothesis` y `yfinance`; están declaradas por el proyecto y deben probarse en el entorno host/CI con requirements completos;
- los fallos compartidos con candidate1 permanecen clasificados como deuda baseline y no se maquillan como GREEN.

# Condiciones obligatorias antes de deploy

1. subir el paquete TEST-READY al Droplet sin reemplazar runtime;
2. verificar SHA256;
3. crear staging aislado;
4. instalar/usar requirements completos;
5. ejecutar preflight + compileall + suite RC4 + suite legacy diferencial;
6. ejecutar replay read-only contra copia de DB/evidencia real, no contra DB productiva mutable;
7. revisar output manualmente;
8. subir source final a Git y obtener commit inmutable;
9. build directo desde ese commit;
10. registrar image digest + manifest;
11. completar este MD con evidencia host/CI final;
12. recién entonces decidir GO/NO-GO de deploy.

**TEST-READY NO ES DEPLOY-AUTHORIZED.**
