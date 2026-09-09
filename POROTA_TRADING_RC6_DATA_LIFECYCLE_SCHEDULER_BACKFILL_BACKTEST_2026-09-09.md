# POROTA TRADING RC6 — Data Lifecycle, Scheduler, Backfill y Backtesting

Fecha: 2026-09-09
Estado: diseño vinculante para cierre RC6; implementación/live wiring debe probarse antes de READY_FOR_DEPLOY final.

## 1. Invariantes

- Runtime: `PRODUCTION_PAPER`.
- `real_orders_sent=0` absoluto.
- Ninguna fuente histórica, scraper, backfill o backtest puede enviar órdenes.
- PPI browser: read-only, fail-closed, sólo rutas permitidas; nunca `/Operar`.
- IOL en RC6: sólo lectura. No ejecución.
- Ningún resultado de backtest, Forward Lab o aprendizaje puede autopromover parámetros al runtime.
- Concentración sectorial permanece BINDING/fail-closed.

## 2. Arquitectura de almacenamiento

No mezclar scraping/históricos masivos con la DB transaccional del observer.

### A. Runtime DB — HOT

`observer_v17.db` conserva exclusivamente estado operativo PAPER: posiciones, fills, ledgers, riesgo, heartbeat, candidate_universe y estado de operación. No recibe payloads raw ni el histórico masivo.

### B. Contract Evidence — HOT/WARM

Base/almacén normalizado para hechos contractuales por identidad financiera y fuente: símbolo/ID, familia, mercado, moneda, settlement, nominales, ratio, lote, mínimo, step/tick, vencimiento, strike, subyacente, multiplier, margin, cutoff, rescate y demás campos aplicables.

Cada hecho debe conservar provenance, `observed_at`, hash, estado de completitud y conflicto. No fuzzy; no inferencia de campos ausentes.

### C. History Store v2 — HOT/WARM

Histórico canónico versionado. Identidad mínima de barras ordinarias: `symbol + instrument_type + market + settlement + date`; las familias especializadas deben agregar su dimensión contractual efectiva (por ejemplo expiry/contract para futuros/opciones) antes de ser promovidas a canónico.

- `history_versions_v2`: append-only para trazabilidad.
- `history_canonical_v2`: una observación elegida por identidad/fecha según autoridad de fuente.
- No sobrescribir silenciosamente correcciones.
- No synthetic gap filling.
- No convertir histórico en precio de ejecución.

Autoridad vigente en RC6: PPI > BYMA/A3 > IOL > fuentes auxiliares. IOL complementa y reconcilia; no desplaza una observación válida de mayor autoridad.

### D. Raw Evidence — COLD/TTL

HTML, XHR/JSON sanitizado y payloads raw se guardan sólo para trazabilidad/diagnóstico. No cookies, tokens, localStorage ni perfiles Chrome.

Retención base: 30 días para diagnóstico rápido; compresión posterior; hasta 180 días cuando sustenta cambio contractual/conflicto/incidente/auditoría. Payload idéntico puede deduplicarse después de probar que sobreviven normalización + hash.

### E. Backfill Progress / Attempt Ledger

Persistir por `source + family + market + symbol + settlement/contract + requested_window` el cursor, intento, estado OK/EMPTY/FAILED, primer/último dato, error sanitizado y próxima reanudación. El backfill debe ser resumible e idempotente.

### F. Backtest / Forward Lab Results — WARM

Resultados, manifests, dataset identity, strategy version, folds, parámetros congelados, métricas y limitaciones en store separado/append-only. Nunca escribe configuración activa automáticamente.

## 3. IOL: función permanente, no descarga única

IOL tiene dos funciones:

1. **Bootstrap/backfill inicial**: hasta 365 días cuando la familia/instrumento lo permita.
2. **Incremental diario post-cierre**: traer sólo la ventana nueva/correcciones recientes, reconciliar contra PPI/BYMA y reparar gaps comprobados.

No se vuelve a descargar un año todos los días. El almacén local se construye progresivamente; IOL sigue ejecutándose como fuente secundaria read-only y evidencia independiente.

Para cada observación IOL se debe preservar `fechaHora`, `plazo/settlement`, mercado y dimensiones contractuales aplicables. Nunca colapsar múltiples observaciones del mismo día sin probar que representan la misma identidad.

## 4. Política por familia

- ACCIONES / CEDEARS / ETF tradable: OHLCV por settlement, ajustes/provenance explícitos.
- BONOS / LETRAS / ON: OHLCV + settlement; nominal/VN proviene de Contract Evidence, no se infiere de la barra.
- OPCIONES: serie exacta + expiry + strike + side + underlying + multiplier/lote; sólo histórico dentro de la vida real del contrato.
- FUTUROS: contrato exacto + expiry + multiplier/tick/margin; no mezclar vencimientos.
- CAUCIONES: snapshots por moneda/plazo con TNA, volumen y vencimiento; no tratarlas como OHLCV común.
- FCI / FCI Exterior: NAV/cuotaparte, moneda, clase y fechas; no inventar OHLCV.
- LICITACIONES / CANJES: historia event-driven de términos, fechas, resultados y adjudicación; no OHLCV.
- ÍNDICES / MONEDAS / TASAS: series de señal/referencia salvo que exista vehículo operable probado.
- ACCIONES USA / instrumentos externos: identidad, mercado, settlement y restricciones antes de promover histórico.

## 5. Scheduler permanente

### Intradía

Scraping/Contract Evidence según cadencia de familia, siempre read-only. No usar IOL histórico de 365 días durante rueda.

### Post-cierre BYMA

Mantener el gate actual de `porota-history-postclose-rc6.timer`/job y evolucionarlo a una orquestación idempotente:

1. validar calendario/sesión y `PRODUCTION_PAPER`/0 órdenes;
2. cerrar ingest PPI del día;
3. ejecutar IOL incremental read-only;
4. normalizar por familia/settlement/contrato;
5. append a versions y resolver canonical por precedencia;
6. detectar gaps, divergencias y staleness;
7. reconciliar Contract Evidence/History Store;
8. recalcular coverage/readiness/can_simulate;
9. producir EOD/introspection;
10. alimentar métricas de aprendizaje post-jornada, sin IA intradía ni auto-promoción.

El timer actual despierta periódicamente y el job decide si corresponde ejecutar; mantener este patrón para recuperación automática ante reinicios.

### Fin de semana / fuera de mercado

- gap repair/backfill resumible;
- deep historical coverage;
- browser full/contract hash según política;
- backtests y Forward Lab pesados;
- auditoría de integridad/coverage;
- housekeeping no destructivo e inventario de disco.

### Backup

El backup general fuera de mercado debe incluir DBs canónicas de runtime, Contract Evidence, History Store, manifests/resultados necesarios y metadatos de progreso; excluir perfiles Chrome, cookies/tokens y raw reconstruible voluminoso. Debe existir restore proof. No backup inmediato pre-deploy.

## 6. Backfill RC6

### Fase 1 — bootstrap

- 365 días máximos para series donde aplique.
- Resumible por símbolo/familia.
- Rate limiting/backoff.
- Exact identity solamente.
- EMPTY no equivale a error ni a instrumento inexistente.
- Derivados limitados a la vida del contrato.
- Persistencia primero en store shadow/provenance; promoción a History Store v2 sólo después de validar dimensiones y calidad.

### Fase 2 — quality gate

Por instrumento/familia: chronological order, duplicados, gaps, OHLC consistency, settlement, contract identity, freshness, adjusted/unadjusted, source/provenance y cobertura.

### Fase 3 — gap repair

Sólo huecos comprobados. Reconsultar fuente primaria/secundaria según precedencia; nunca rellenar sintéticamente.

### Fase 4 — incremental

Tras bootstrap, normalmente solicitar sólo nueva ventana + pequeño solapamiento de corrección. Idempotencia/hash evita duplicación.

## 7. Backtesting y Forward Lab

No esperar a que todas las familias estén completas: cada familia entra cuando pasa sus propios gates Contract + History + Wiring.

Pipeline:

`Contract READY -> Historical coverage/quality READY -> frozen dataset manifest -> backtest -> walk-forward/embargo -> leave-one-symbol-out cuando aplique -> Forward Lab -> revisión -> PAPER`

Reglas:

- parámetros congelados antes del período de prueba;
- sin look-ahead;
- no shuffle temporal aleatorio;
- costos, settlement y ejecución modelados por familia;
- benchmark pertinente (incluida caución/carry cuando corresponda);
- separar train/test y embargo;
- guardar manifest SHA/dataset identity/strategy version;
- `promotion_allowed=false` hasta aprobación explícita;
- ninguna calibración modifica el motor intradía automáticamente.

El backtest existente de estrategia es una evaluación offline limitada y no sustituye Forward Lab; `fh_forward_lab_v2_rc6.py` aporta folds walk-forward, embargo, leave-one-symbol-out y validación temporal.

## 8. Gate de readiness de datos

Un instrumento no puede pasar a `can_simulate=1` sólo por tener precio/histórico. Debe satisfacer en conjunto:

- identidad determinística;
- Contract Evidence suficiente para su familia;
- History Store suficiente/calidad requerida por estrategia;
- freshness;
- costes/settlement/riesgo aplicables;
- wiring probado end-to-end;
- ausencia de conflicto crítico de fuentes.

Si PPI/IOL divergen materialmente: `SOURCE_DISCREPANCY` y fail-closed para el campo afectado hasta reconciliación.

## 9. Capacidad / mantenimiento

Mantener política de disco existente:

- <65% usado: VERDE;
- 65–75%: AMARILLO;
- 75–85%: limpieza/compresión planificada;
- >=85%: ROJO para ingestas pesadas.

No `docker system prune -a` a ciegas. SQLite activa no se comprime como archivo. `quick_check` es gate; WAL/checkpoint/vacuum sólo en ventana segura.

Métricas diarias: DB/WAL bytes, rows por store/familia/source, raw bytes/count, logs, Docker/cache, filesystem libre, crecimiento 24h/7d y días estimados a umbrales.

## 10. Pendientes vinculantes antes de cierre RC6

1. W12 exact identity/deep Contract Evidence completo por familias relevantes.
2. Normalizador IOL preservando `plazo/settlement` y dimensión contractual.
3. Ejecutar backfill 365d shadow resumible y quality gate.
4. Adaptadores especializados para cauciones/FCI/licitaciones-canjes/derivados.
5. Wiring Contract + History -> readiness/can_simulate -> motor/dashboard.
6. Scheduler post-close live con IOL incremental + reconciliación + gap repair.
7. Scheduler fin de semana para backfill/backtests/Forward Lab.
8. Backup coverage + restore proof de nuevos stores.
9. Dashboard/introspection con source, freshness, coverage, gaps, discrepancies y last successful ingest.
10. Cross-wave integration tests preservando sector BINDING y economía BINDING.
11. Live render/routes/data/freshness/safety después de deploy serial.
12. Canonizar checkpoint final con SHAs/runs/estado real.

Hasta probar estos puntos, RC6 puede tener componentes READY_FOR_DEPLOY pero no debe declararse GO-LIVE final.