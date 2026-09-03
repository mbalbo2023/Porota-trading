# POROTA TRADING — RC4 Observability / UX Acceptance

Estado: WIP, NO DESPLEGABLE.
Fecha de consolidación: 2026-09-03.
Baseline operativo a preservar: 17.0.0-rc3-hf6-v2-candidate1, PRODUCTION_PAPER, SIMULATED, real_orders=0.

## 1. /vivo — prioridad operacional

La vista `/vivo` debe responder primero qué está ocurriendo con las operaciones PAPER. El orden canónico es:

1. Operaciones abiertas actuales.
2. Operaciones cerradas recientes.
3. Decisiones / rechazos / gates.
4. Resumen Scalping claramente separado.
5. Motores/workers/health técnico al final.

El reporte de embudo no pertenece a la vista principal `/vivo` y debe retirarse de allí.

### 1.1 Operaciones abiertas
Cada operación debe mostrar en la fila/summary, sin abrir el detalle:
- símbolo/familia/moneda/settlement;
- estado;
- cantidad;
- precio de entrada;
- mark/precio actual;
- P&L actual neto PAPER en su propia moneda;
- semáforo: verde positivo, rojo negativo, neutro cero; texto accesible además del color;
- timestamp exacto del último mark;
- freshness/edad del mark. Un mark stale no puede presentarse como P&L actual sin advertencia.

Cada operación debe recuperar el `<details>`/flecha de drill-down con:
- apertura/fills, cantidad y costos;
- stop/target;
- slippage;
- señal/score;
- gates técnico, económico, patrimonial, liquidez y riesgo;
- concurrent risk antes/candidato/locked cuando exista;
- razón completa de aceptación;
- supervisor / exit intent / motivo de salida cuando corresponda.

### 1.2 Operaciones cerradas y aprendizaje
Al cerrar una operación, el drill-down debe preservar el criterio de entrada original y agregar una sección separada `Lección aprendida` basada en evidencia post-trade. Debe incluir cuando existan: resultado neto, costos, MFE, MAE, duración, motivo de cierre, stop/target/time exit, régimen, spread/slippage y diferencias entre expectativa y ejecución. La lección no cambia parámetros automáticamente.

### 1.3 Rechazos
Los rechazos deben ser visibles y explicables: símbolo, hora, decisión, razón, gate que bloqueó y evidencia disponible. No mezclar HOLD contractual con rechazo económico/técnico.

## 2. Scalping

Scalping vuelve a ser destino top-level `/scalping`; no debe quedar escondido como link inferior o redirección exclusiva a `/trading/estrategias`.

- `/scalping`: detalle completo de scanner, contrato intradiario, candidatos y fills PAPER.
- `/vivo`: resumen de actividad Scalping separado de operaciones estándar.
- `/trading/estrategias`: puede conservar una vista agregada/compatibilidad, pero no sustituye el acceso directo.
- Las posiciones Scalping siguen diferenciadas del libro normal y nunca implican órdenes reales.

## 3. Introspección

El dashboard no puede presentar un snapshot viejo como estado actual.

Debe mostrar:
- `snapshot_generated_at`;
- edad del snapshot;
- `FRESH`, `STALE` o `SUPERSEDED_BY_LIVE_STATE`;
- heartbeat/process/session live del observer al lado del snapshot;
- advertencia explícita si el snapshot y el estado vivo difieren.

Caso observado que RC4 debe impedir: snapshot de 00:15 mostrando `WAITING_MARKET/MARKET_CLOSED` durante rueda cuando `observer_state` vivo está `RUNNING/MARKET_OPEN`.

El control funcional integral debe convertirse en job read-only recurrente, sanitizado, persistido y visible en dashboard. Nunca debe modificar runtime, DB financiera ni enviar órdenes. La frecuencia se definirá de modo que aporte diagnóstico sin competir con el hot path; objetivo inicial: cada 15 minutos durante rueda y frecuencia extendida fuera de mercado, sujeto a benchmark de costo.

## 4. Salud / SRE

Un amarillo `RETRASADO` debe explicar por qué está amarillo. Debe mostrar:
- componente;
- estado semántico;
- última comprobación;
- último éxito;
- edad;
- TTL esperado;
- diferencia contra TTL;
- fuente de evidencia;
- razón legible.

Clasificaciones mínimas:
- `FRESH_OK`;
- `STALE_DEGRADATION`;
- `WAITING_FIRST_SAMPLE`;
- `CONDITIONAL_NOT_DUE`;
- `DISABLED_BY_POLICY` / `NO_APLICA`;
- `PARTIAL_EVIDENCE`;
- `ERROR`.

No convertir automáticamente todo amarillo en error ni usar una única regla de staleness para fuentes con cadencias distintas.

## 5. Scheduler

Ningún job catalogado puede aparecer simplemente gris sin explicación.

Para cada job mostrar:
- job key y descripción;
- fuente autoritativa de evidencia (`operational_jobs`, `source_sync`, `api_health`, systemd snapshot u otra explícita);
- condición de ejecución;
- frecuencia efectiva, no sólo frecuencia declarada;
- última ejecución;
- último éxito;
- duración si existe;
- último resultado;
- próxima ejecución/eligibilidad;
- motivo de `NO_APLICA`, `HOLD`, `SIN_EVIDENCIA`, `NUNCA_EJECUTADO`, `RETRASADO` o error.

La reconciliación debe cubrir al menos:
- SRE/backup/financial/news/reports desde `operational_jobs`;
- PPI history/catalog desde `source_sync` cuando ésa sea la evidencia real;
- readiness/coverage/sampling/rotation desde `api_health` cuando corresponda;
- timers host desde snapshot systemd sanitizado.

Un job de 5 minutos sin last/next/result es un fallo de observabilidad, no un estado aceptable.

## 6. Contract Evidence / scraping PPI

Sistema debe incluir una sección propia `Scraping / Contract Evidence` con historial auditable de ejecuciones.

Por corrida mostrar:
- run id;
- inicio/fin/duración;
- trigger/cadencia;
- fuente/método: PPI API, JSON/XHR, browser autenticado, hash estático;
- familias/endpoints consultados;
- instrumentos/evidencias vistos;
- nuevos/cambiados/sin cambios;
- verificados/bloqueados por Porota/bloqueados por proveedor;
- stale/conflictos;
- autenticación/session state de forma sanitizada (nunca cookies/tokens/OTP);
- resultado final y próxima corrida.

La información obtenida por scraping debe materializarse de forma versionada en Contract Evidence y reconciliarse por familia/instrumento/mercado. Descubrir un dato nunca habilita READY_PAPER por sí solo.

Cadencias objetivo ya acordadas:
- XHR/datos dinámicos: 15 min;
- cauciones/open auctions: 5 min;
- opciones/futuros: 15 min;
- contratos estáticos: hash diario;
- browser full: semanal y fuera de mercado.

## 7. Logs

Sistema → Logs debe mostrar y permitir descargar, con la misma autenticación del dashboard:
- Bot / aplicación (`trading_bot.log` o snapshot sanitizado equivalente);
- Observer container/runtime;
- Dashboard runtime;
- scraping/Contract Evidence;
- otros logs explícitamente exportados.

El dashboard nunca recibe `/var/run/docker.sock`. La exportación se realiza host-side, sanitizada, con tamaño acotado y rutas allowlisted. Debe mostrarse mtime/tamaño/freshness de cada fuente.

El Bot log no puede estar ausente de la UI si existe; si no existe, debe mostrar `SIN_FUENTE` y explicar por qué.

## 8. Backups

SRE → Backups debe ser una matriz de cobertura de persistencia, no sólo el histórico de `observer_v17.db`.

Inventario mínimo:
- `observer_v17.db`;
- `market_history.db` / History Store v2 cuando exista;
- `sre_vector_db` como almacén no-SQLite;
- backup general del host;
- cualquier nueva base/almacén persistente RC4.

Por almacén mostrar:
- existe/no existe;
- tamaño;
- mecanismo de backup;
- última copia;
- próxima copia/TTL;
- SHA/hash cuando corresponda;
- restore/integrity test;
- retención;
- cobertura del último backup general;
- estado `PROTECTED`, `STALE_BACKUP`, `NO_BACKUP`, `NOT_CREATED`, `ERROR`.

Situación heredada detectada: `DAILY_BACKUP` respalda sólo `store.path` (observer) y registra `observer_YYYY-MM-DD.db.gz`. El script `v17_host_general_backup.py` puede respaldar todas las `.db` bajo `data/` mediante SQLite online backup y copiar `sre_vector_db`, pero su ejecución periódica no puede asumirse si no existe evidencia/scheduler visible. RC4 debe cerrar esta brecha.

## 9. Invariantes permanentes

- PRODUCTION_PAPER / SIMULATED.
- real_orders=0.
- PPI Orders bloqueado.
- dashboard sin Docker socket.
- read-only para diagnóstico/scraping salvo persistencia explícita de evidencia no financiera.
- no mezclar P&L ARS y USD.
- no inferir READY_PAPER, sectores, correlación o contrato ausente.
- candidate1 no se modifica durante la rueda; RC4 se prepara en rama aislada.
