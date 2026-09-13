# POROTA TRADING — CHECKPOINT OBSERVER / CAPACIDAD — 2026-09-13

**Estado:** ACTIVO / complemento vinculante del checkpoint CAUCIONES READY  
**Rama:** `ops/rc6-ppi-web-residual-ready-20260913`  
**Modo:** `PRODUCTION_PAPER`  
**Órdenes reales:** `0`  

## REGLAS INVARIANTES

- NO rehacer ni recrear scraper/ingesta histórica.
- No reactivar productores pausados en bloque.
- No habilitar rutas de órdenes reales.
- Escalar observación sólo con evidencia de capacidad, cadencia, API PPI y fail-closed.

## AUDITORÍA READ-ONLY DE CAPACIDAD REAL

Workflow: `RC6 observer capacity read-only proof 2026-09-13`  
Run exitoso: `34781768296`  
Job: `103789961392`.

### Contenedor

- `porota_production_observer` sin hard cap Docker explícito de CPU (`NanoCpus=0`, quota=0) ni memoria (`Memory=0`).
- Snapshot de uso: CPU `10.26%`; memoria `241.9 MiB / 961.5 MiB` (`25.16%`); PIDs `13`.
- Conclusión: el límite `20` NO está impuesto por un hard cap del contenedor/servidor.

### Configuración runtime observada

- `PAPER_ACTIVE_SYMBOL_LIMIT=20`
- `PAPER_OBSERVER_INTERVAL_SECONDS=60`
- `PAPER_PPI_CALL_BUDGET_SECONDS=2`
- `PAPER_INTRADAY_SCAN_SECONDS=180`
- `PAPER_INTRADAY_BATCH_LIMIT=24`
- `PAPER_SIGNAL_MIN_SAMPLES=6`
- `PAPER_SIGNAL_WINDOW_MINUTES=90`

### Universo real

`AVAILABLE_TOTAL=889`:
- CEDEARS: 570
- BONOS: 162
- ON: 60
- ACCIONES: 32
- FUTUROS: 25
- LETRAS: 17
- CAUCIONES: 10
- INDICES: 4
- ETF: 4
- FCI: 3
- OPCIONES: 2

El observer reporta aproximadamente `834` identidades elegibles para su rotación actual.

### Rendimiento medido — últimos 300 ciclos

- `selected_count`: 20 constante.
- Duración media del procesamiento del lote: `36.399 s`.
- Duración p95: `50.216 s`.
- Máxima observada: `67.731 s`.
- Éxitos medios por ciclo: `11.557`; p95 `18`; máximo `20`.
- Fallos medios por ciclo: `0.15`; p95 `1`; máximo `4`.
- Recomendación interna calculada por el propio runtime: media `23.023`, p95 `31`, máximo `32`.

### Latencia por símbolo — últimas 5000 muestras

- media: `1779.53 ms`
- p50: `1731.12 ms`
- p95: `2649.11 ms`
- p99: `3314.58 ms`
- máximo: `58532.3 ms`

La limitación dominante no es CPU/RAM local: son las llamadas PPI/red, ejecutadas de forma serial (`current` + `book`) y los outliers de latencia.

## HALLAZGO CRÍTICO — COBERTURA DE OPORTUNIDADES

Con 20 seleccionados por ciclo, el runtime observado dejó aproximadamente:
- `rotation_pool=824`
- `rotation_slots=10`
- `rotation_turns=83`
- `rotation_feasible=false` para reunir el mínimo de muestras de toda la cola dentro de la ventana de señal de 90 minutos.

Por lo tanto, **sí existe riesgo real de perder oportunidades breves en instrumentos que quedan en la cola rotativa**. El límite 20 es un throttle conservador/configurable, no una limitación física demostrada del servidor.

El ciclo real no dura sólo 60 s: procesa el lote y luego duerme `PAPER_OBSERVER_INTERVAL_SECONDS`. Con media observada, un ciclo típico del observer es del orden de `36.4 + 60 ≈ 96 s`; con p95, `50.2 + 60 ≈ 110 s`. La cobertura completa de la cola puede tardar del orden de horas, no minutos.

## INTRADAY 180 SEGUNDOS

`PAPER_INTRADAY_SCAN_SECONDS=180` es configurable y el código permite bajar hasta 60 s. No es un límite de hardware. Además, el worker procesa un lote (`PAPER_INTRADAY_BATCH_LIMIT=24`) y recién después espera el intervalo. Con cientos de identidades, 24 por lote + 180 s de pausa también produce una vuelta completa demasiado lenta para descubrir eventos breves en toda la plaza.

Bajar ciegamente a 60 s y subir todo a 60 instrumentos NO es la solución final: elevaría presión sobre PPI, logins/sesiones, retries, latencia y riesgo de 429/errores transitorios; además seguiría haciendo `current+book` costoso para instrumentos fríos.

## DECISIÓN DE ARQUITECTURA

Implementar scheduler adaptativo por tiers, manteniendo fail-closed:

1. **HOT / CRITICAL:** posiciones abiertas, 10 CAUCIONES, instrumentos con señal reciente y foco líquido. Refresco objetivo 15–60 s según familia/gate.
2. **ACTIVE:** universo líquido/operable con actividad. Objetivo 1–3 min.
3. **BROAD:** cola larga/fría. Screening barato 5–15 min; promoción automática a HOT ante cambios relevantes.
4. Para la cola amplia, preferir señal barata (`current`/intraday cuando corresponda); pedir `book` completo sólo a candidatos HOT/ACTIVE o cuando el contrato especializado lo exija.
5. Mantener presupuestos de llamadas, retries acotados, cooldown de login, métricas de p50/p95/p99 y degradación automática ante presión PPI.
6. Nunca sacrificar seguimiento de posiciones abiertas ni freshness contractual por ampliar discovery.

## AJUSTE INTERINO PROPUESTO

La propia telemetría del runtime recomienda hasta ~31 instrumentos en p95 y 32 máximo. Por eso, antes de cualquier salto agresivo, el siguiente escalón seguro a probar en PAPER es `PAPER_ACTIVE_SYMBOL_LIMIT=30`, con comparación A/B read-only de latencia, errores, duración de ciclo, cobertura y PPI health. No promover automáticamente a 60 sin esa prueba.

Para intraday, evaluar primero `180 -> 120 -> 60` con telemetría de duración/lote y errores PPI. El objetivo final es scheduler adaptativo, no un número fijo global.

## RELACIÓN CON CAUCIONES READY

Las 10 CAUCIONES deben quedar en tier HOT/CRITICAL para que `current/book` y el heartbeat especializado puedan sostener TTL <=300 s. Esto NO reemplaza `CAUCION_FRESH_DATA_AGENT_GREEN`: sólo garantiza que haya oportunidad de producir la evidencia fresca. El gate especializado, semántica, costos, horario/cutoff y dashboard siguen fail-closed.

## SEMÁFORO

- 🟢 Auditoría de capacidad servidor/contenedor: cerrada.
- 🟢 Causa del límite 20: throttle/configuración + arquitectura serial; no hard cap de CPU/RAM.
- 🔴 Cobertura uniforme de ~834 elegibles dentro de ventana de señal: no viable con configuración actual.
- 🟡 Escalón PAPER 20 -> 30: autorizado para prueba controlada, no aún desplegado.
- 🟡 Intraday 180 -> 120/60: técnicamente posible, pendiente prueba de presión/latencia.
- 🟡 Scheduler por tiers HOT/ACTIVE/BROAD: a implementar/probar.
- 🟢 CAUCIONES: deben tener prioridad HOT 10/10; no depender de la cola general.
- 🟢 Seguridad: `PRODUCTION_PAPER`, `real_orders_sent=0`, sin rutas reales.

## SIGUIENTE ACCIÓN

En paralelo con el cierre de CAUCIONES READY: implementar scheduler adaptativo y prueba A/B PAPER; integrar las 10 CAUCIONES como prioridad de freshness; completar costos all-in, specialized readiness -> runtime/dashboard, opportunity -> allocator PAPER y postflight. La rueda activa queda reservada para la evidencia dinámica final, no para descubrir problemas determinísticos evitables.
