# POROTA TRADING — CHECKPOINT CANÓNICO RC6 POSTDEPLOY

Fecha de corte: 2026-09-06 — America/Argentina/Buenos_Aires
Estado: RC6 DESPLEGADA + Contract Evidence RC6 nativo ACTIVADO

> Este archivo reemplaza al checkpoint del 2026-09-05 como punto de continuidad operativo. El checkpoint anterior permanece como evidencia histórica. Antes de actuar en otro chat, verificar siempre GitHub + runtime live; no inferir estado vivo sólo desde este documento.

---

## 0. Identidad exacta de la release desplegada

Runtime desplegado:
- release: `17.0.0-rc6`;
- branch live: `release-candidate/v17.0.0-rc6-deploy3-20260906`;
- SHA exacto del runtime/image: `5bdad270c2a23bb2456a320d41e18940ce70ec6f`;
- imagen local: `porota-trading-bot:17.0.0-rc6`;
- digest GHCR inmutable: `sha256:ef9f6bdb273e9ea715b22a092ce52fbde9ac6170a5bedbbbaa926b6763cba6a6`;
- workflow final: run `34051968166` — GREEN;
- modo: `PRODUCTION_PAPER`;
- ejecución: `SIMULATED`;
- capacidad de orden real: `BLOCKED`;
- `real_orders_sent=0`;
- network order test: `NOT_PERFORMED`.

Pruebas de deploy concluidas GREEN:
- exact SHA acceptance;
- suite activa RC6 offline;
- invariantes de release;
- publicación GHCR por digest;
- borrado de tag local + re-pull por digest;
- activación RC6;
- rollback real RC6 → RC5 → RC6;
- rollback RC5 usando imagen local y `--pull never`;
- compatibilidad SQLite additive-only;
- migración systemd RC6;
- host probes;
- soak;
- A3 background enable;
- observer/dashboard sin reinicios durante el gate;
- observer readonly;
- DB observer/history `quick_check=ok`;
- disco final superior al mínimo operativo de 8 GiB.

La RC5 exacta `852b812d610860b57b579212978443f7450e8855` queda como baseline histórica y rollback local probado. No borrar su imagen mientras sea la reversión inmediata aprobada.

---

## 1. Invariantes absolutos

1. No usar dinero real en esta campaña.
2. `PRODUCTION_PAPER` + `SIMULATED`.
3. `REAL_ORDER_CAPABILITY=BLOCKED`.
4. `real_orders_sent=0` es gate estructural y empírico.
5. Prohibidos network order tests.
6. PPI producción sólo market/configuration data read-only en esta fase.
7. Derivados: observación/evidencia sí; ejecución real no.
8. A3 live/order routing OFF.
9. A3 background jamás mezcla campos con PPI hot path ni veta una decisión PPI live válida por divergencia asíncrona.
10. Contract Evidence jamás auto-activa una familia/instrumento.
11. No inventar timestamps de liquidación T+1.
12. No llamar COMPLETE a velas `TRADE_SAMPLES`.
13. No inventar volumen/trades/VWAP para sampled candles.
14. No tuning masivo ni threshold 0.83 sin evidencia.
15. No pasar una regla aprendible a BINDING automáticamente.
16. Cualquier paso a dinero real requiere proyecto/gobernanza y autorización humana explícita futura.

---

## 2. Filosofía de aprendizaje fijada — SHADOW primero

Decisión fundamental del operador, reconfirmada en esta conversación:

> En PAPER se prefiere perder dinero ficticio y aprender ahora antes que bloquear demasiado pronto operaciones que contienen evidencia útil.

Por tanto separar dos tipos de controles.

### 2.1 Hard Safety Gates

Son obligatorios desde el primer día y no se “aprenden”:
- bloqueo de órdenes reales;
- ejecución simulada;
- integridad de DB/config;
- guards fail-closed;
- mercado/sesión no apta;
- ausencia de evidencia mínima necesaria para una ejecución simulada interpretable;
- rutas monetarias reales bloqueadas;
- derivados reales bloqueados.

En UI evitar llamarlos simplemente `BINDING`; preferir `SAFETY HARD BLOCK` para no confundirlos con políticas aprendibles.

### 2.2 Learnable Gates

Estado inicial durante campaña PAPER: SHADOW / OBSERVATION_ONLY.

Incluye:
- portón económico;
- expectancy;
- régimen;
- concentración sectorial;
- cualquier filtro futuro destinado a mejorar selección/rentabilidad y no a seguridad estructural.

Regla:
- calcula;
- registra `would_allow` / `would_block`;
- PAPER puede continuar;
- se captura resultado posterior;
- se mide contrafactual;
- no puede vetar PAPER mientras está SHADOW.

### 2.3 Escalera aprobada

`COLLECTING_EVIDENCE`
→ `SHADOW`
→ `SHADOW_VALIDATION`
→ `EVIDENCE_SUFFICIENT`
→ `ELIGIBLE_FOR_BINDING_DECISION`
→ autorización explícita/versionada
→ `BINDING_PAPER`
→ `BINDING_VALIDATION`
→ `BINDING_PAPER_PROVEN`
→ governance real-money
→ canary real-money futuro.

Nunca autopromoción.

### 2.4 Contrafactual obligatorio

Por operación PAPER guardar para cada gate:
- decisión real PAPER;
- decisión hipotética del gate;
- motivo;
- PnL/resultado posterior;
- pérdida que habría evitado;
- ganancia que habría bloqueado;
- false positive;
- false negative;
- cohort/familia/instrumento/régimen;
- versión del gate/commit.

Objetivo: no acumular sólo “317 would_block”, sino saber si esos bloqueos habrían sido útiles.

---

## 3. `/validacion` — Camino a Producción

Prioridad cero permanente. No es un log decorativo: es el tablero de gestión del proyecto.

Objetivo visual:
- dónde estamos;
- a dónde queremos llegar;
- qué esperábamos;
- qué ocurrió;
- qué aprendimos;
- qué bloquea el siguiente hito;
- qué evidencia falta;
- qué fecha/rueda produjo la evidencia;
- qué commit/release estaba activo.

### 3.1 Ledger

Implementación RC6:
- append-only JSONL;
- hash chain SHA-256;
- evidencia no se reescribe;
- lectura del dashboard no modifica trading;
- ningún porcentaje puede habilitar real-money;
- avance descriptivo separado de readiness ponderado;
- readiness ponderado no se habilita hasta aprobar pesos explícitamente.

Campos diarios:
- fecha AR;
- hito;
- objetivo;
- evidencia esperada;
- evidencia observada;
- % descriptivo;
- estado GREEN/YELLOW/RED/GRAY;
- esperado;
- observado;
- desviación;
- causa raíz;
- lección;
- decisión;
- blocker;
- próxima acción;
- owner;
- referencia de evidencia;
- commit/release;
- camino crítico sí/no.

### 3.2 Camino crítico M0–M11

M0 Infraestructura/capacidad.
M1 Safety invariants.
M2 Fuentes/contratos.
M3 Calidad de mercado/históricos.
M4 Estabilidad PAPER.
M5 Realismo de ejecución.
M6 Evidencia estadística.
M7 Operabilidad/accesibilidad.
M8 Campaña PAPER sostenida.
M9 Auditoría independiente/consenso.
M10 Governance candidate real-money.
M11 Real-money — actualmente BLOCKED.

M11 jamás queda GREEN por porcentaje, una rueda buena o un backtest favorable.

### 3.3 Carriles SHADOW dentro de Validación

Mostrar por separado:
- Portón económico;
- Expectancy;
- Régimen;
- Concentración sectorial.

Cada uno debe mostrar:
- fase actual;
- inicio de evidencia;
- ruedas observadas;
- evaluaciones;
- would_allow;
- would_block;
- pérdidas que habría evitado;
- ganancias que habría eliminado;
- false positives/negatives;
- PnL PAPER;
- PnL contrafactual;
- evidencia por instrumento/familia/régimen;
- condición explícita para solicitar promoción.

---

## 4. P0 Dashboard — verdad semántica total

El dashboard completo debe obedecer: **un dato → una fuente de verdad → una interpretación inequívoca**.

Hallazgos que motivaron P0:
- `Economía matemática BINDING` parecía modo global y, además, el portón económico heredado estaba BINDING contra la estrategia SHADOW-first;
- un watchdog `RUNNING` fuera de mercado podía parecer “operando”; proceso vivo != mercado abierto != actividad de trading;
- tablas comprimidas en Samsung podían apilar letras verticalmente y quedar inutilizables por Voice Access.

Reglas RC6:
- modo global siempre separado de modo/política de un gate;
- portón económico actual = SHADOW;
- process/liveness separado de session/market state;
- fuera de mercado sin posiciones: `EN_ESPERA_MERCADO_CERRADO` o equivalente, no actividad de trading;
- error real nunca se oculta;
- supervisor puede estar técnicamente vivo, pero UI explica actividad operativa real;
- todas las páginas deben reconciliar con la misma runtime truth;
- navegación completa y HTTP coherente;
- cero scroll horizontal global;
- tablas densas pasan a representación vertical/card accesible cuando el ancho real no alcanza;
- controles textuales para Voice Access;
- 10–20 filas iniciales + paginación/Mostrar más;
- preservar foco/estado/detalles cuando corresponda.

Vistas sujetas a auditoría de consistencia:
Panel, En vivo, Trading, Universo operativo, Scalping, Validación, Instrumentos/contratos, Históricos, Aprendizaje, Reportes, Sistema y Motor de trading.

---

## 5. Contract Evidence RC6 — ACTIVO nativo

### 5.1 Por qué quedó bloqueado durante deploy3

No se abandonó Contract Evidence.

El timer RC5 todavía terminaba llamando componentes `rc4_*` (`rc4_contract_due_job.py` y session/browser runner RC4). La regla de arquitectura RC6 prohíbe dependencias RC4 en paths activos. Por eso deploy3 actuó fail-closed y dejó explícitamente:

`CONTRACT_EVIDENCE=BLOCKED_PENDING_RC6_NATIVE_WIRING`

Esto fue una protección correcta, no una decisión funcional de apagar Contract Evidence.

### 5.2 Estado posterior

Branch de wiring nativo:
`candidate/v17.0.0-rc6-contract-evidence-native-20260906`

Commit validado/desplegado del paquete host-native:
`4e8b70c49714fa5b3e77c34452a888cb3e2035da`

Workflow:
`RC6 native Contract Evidence deploy`, run `34055331949` — GREEN.

Host:
- `porota-contract-evidence-rc6.timer` ENABLED + ACTIVE;
- rc4/rc5 CE timers no activos;
- runtime nativo en `/usr/local/sbin/porota-contract-evidence-rc6-runtime.sh`;
- librería nativa en `/usr/local/lib/porota-contract-evidence-rc6`;
- active wiring auditado sin referencia `rc4`;
- observer siguió exacto RC6, readonly, restart 0;
- DB quick_check ok;
- `real_orders_sent=0`.

Prueba del domingo:
- `STATUS=GREEN_NOT_DUE`;
- `AUTH_BROWSER_STARTED=NO`;
- `PPI_CALLS=0`;
- `REAL_ORDERS_SENT=0`.

Eso es el comportamiento correcto: timer activo, job atento a cuándo corresponde, pero no inicia browser autenticado en un día no operativo.

### 5.3 Cadencia canónica

- OPERABILITY/dynamic: q15 min dentro de rueda;
- CAUCIONES contract: q5 min dentro de rueda;
- AUCTION status: q5 min dentro de rueda;
- DERIVATIVE series: q15 min dentro de rueda;
- STATIC contract: diario post-close;
- FULL browser audit: semanal, viernes post-close;
- polling del timer host: q5 min monotónico, sin `Persistent`; la lógica due decide si corresponde ejecutar.

### 5.4 Safety browser

Native collector:
- perfil trusted-device existente;
- no recibe username/password/OTP;
- sesión expirada/2FA → bloqueado, no workaround;
- backoff 1 hora ante auth bloqueada para no martillar PPI;
- GET/HEAD/OPTIONS solamente en sesión de evidencia;
- non-read request → abort;
- no llena cantidad/precio;
- no click Continue/Confirm de operación;
- no importa order router;
- captura sanitizada;
- evidencia append/versioned en Contract Evidence v2;
- cambios → review, nunca auto activation.

### 5.5 Gate pendiente mañana

El domingo sólo se puede demostrar `NOT_DUE` correctamente.
La primera ejecución realmente `DUE` en día operativo debe observarse mañana:
- trusted session válida o AMARILLO auth-blocked/backoff;
- GET/XHR read-only;
- import v2;
- quick_check ok;
- `real_orders_sent=0`;
- ningún non-read permitido;
- freshness/cadence correctos.

Esto es una prueba de campaña de mañana, no una razón para dejar el timer apagado hoy.

---

## 6. A3 Historical

Estado postdeploy:
- background RC6 habilitado;
- daily/reconcile/weekend timers activos;
- live/order routing OFF;
- ejecución monetaria falsa/bloqueada;
- fuente de evidencia/history separada del hot path PPI.

Política:
- daily incremental post-close por segmento/familia;
- reconciliación nocturna acotada;
- weekend deep backfill;
- smart retry;
- known-empty backoff;
- checkpoint/resume;
- estados por trading day COMPLETE/PARTIAL/EMPTY_CONFIRMED/FAILED;
- no hammer;
- no mezcla de campos con PPI live;
- no veto sobre decisión live PPI.

Aún requiere continuar validando calidad/coverage/provenance real por endpoint y familia antes de atribuirle readiness de trading.

---

## 7. Scheduler / host truth RC6

La migración systemd RC6 quedó GREEN.

Core RC6:
- functional health;
- postclose history;
- host backup;
- preopen 10:15 AR;
- candle integrity;
- A3 background;
- Contract Evidence RC6 nativo.

Reglas:
- timers monotónicos no usan `Persistent`;
- calendar backup sí puede usar semántica de calendar/persistent cuando corresponde;
- no mantener timers RC4 activos;
- inert legacy unit files sólo son evidencia/rollback y no deben confundirse con wiring activo;
- dashboard scheduler debe reflejar host truth, no una tabla estática.

El viejo preopen 10:40 dejó de ser autoridad. RC6 preopen es 10:15 AR.

---

## 8. Rollback y recovery

Dos niveles:

### inmediato
- release anterior local disponible;
- `--pull never`;
- sin dependencia de GHCR/red;
- rehearsal real RC6→RC5→RC6 realizado GREEN.

### histórico
- GHCR digest inmutable;
- commit/version/manifest;
- re-pull por digest probado.

DB:
- no “probar downgrade” improvisado;
- compatibilidad demostrada o restore de backup canónico asociado;
- deploy3 pasó additive-only compatibility gate.

No borrar RC5 local antes de una decisión futura explícita de lifecycle cleanup post-soak.

---

## 9. Política especial lunes 2026-09-07 — Labor Day USA

Confirmado previamente: BYMA Argentina puede operar y USA está cerrado por Labor Day.

RC6 implementa para foco Apple CEDEAR:
- AAPL / AAPLD / AAPLC: observar/registrar quotes;
- no nuevas aperturas PAPER ese día;
- motivo: `UNDERLYING_MARKET_CLOSED: US_LABOR_DAY`;
- no tratar la rueda como cohorte CEDEAR normal;
- GGAL/acciones argentinas no heredan este bloqueo.

No extrapolar el bloqueo a todos los instrumentos sin evidencia de su subyacente/régimen.

---

## 10. Plan operativo lunes 2026-09-07

### antes de 10:15
- SHA/image live;
- observer/dashboard running + restart count;
- observer readonly;
- DB quickchecks;
- disk;
- timers;
- scheduler truth;
- Contract Evidence timer active;
- A3 background;
- `real_orders_sent=0`.

### 10:15–10:30 preopen
- ejecutar readiness;
- PPI auth read-only;
- catálogo/current quotes;
- verificar política CEDEAR US holiday;
- cualquier P0 RED → NO-GO/rollback, no improvisar roll-forward.

### 10:30–17:00 PAPER
- no cambios de código/config durante rueda;
- observar quotes;
- decisiones;
- posiciones;
- PnL;
- marks;
- exits;
- candles;
- history;
- Contract Evidence due-runs;
- A3 background;
- disk;
- restarts;
- Telegram;
- early-warning/introspection;
- contrafactuales SHADOW.

### 17:00+
- CLOSED/reconciliación;
- orders=0;
- history/CE post-close;
- campaña `/validacion` con esperado vs observado;
- lecciones;
- blockers;
- decisión sobre siguiente rueda.

---

## 11. Telegram

Objetivo:
- fin de semana/feriado: suprimir INFO/rutina/progreso/success/no-trade/history/ingest esperables;
- CRITICAL/P0 jamás silenciado;
- evitar spam;
- distinguir warning informativo de incidente accionable.

Antes de cualquier ampliación central:
- probar explícitamente `priority=0`/CRITICAL unsuppressible;
- no permitir que calendar suppression tape observer down, DB corruption, disk critical, invariant violation, scheduler esencial roto, real_orders anomaly.

---

## 12. MFE/MAE

UI actual debe decir `NO_MEDIDO` donde no existe writer/provenance real.

No convertir 0/NULL histórico en “evidencia”.

Implementación futura completa:
- bid-based executable excursion;
- `excursion_state` o estado equivalente;
- provenance;
- cutover explícito;
- backup + tests antes de ALTER TABLE si se requiere;
- filas previas al writer real = NOT_MEASURED.

---

## 13. Forward Lab v2

No bloquea observar PAPER mañana.
Sí bloquea usar estadísticas débiles como justificación de tuning o real-money.

Pendiente:
- model-fitted HAC/panel apropiado;
- block/stationary bootstrap por `trading_day_ar`;
- leave-symbol-out;
- leave-day-out;
- cohorts;
- subperiodos;
- multiple testing;
- DSR donde corresponda;
- no raw-array HAC;
- no asumir 5 filas = 5 días.

---

## 14. Retention v2 / DB

P0-1 físico resuelto durante fin de semana.
No seguir borrando datos útiles.

Pendiente de ingeniería:
- dbstat/read-only attribution por tabla;
- política por trading_day_ar;
- batches;
- backup first;
- WAL management;
- no VACUUM imprudente;
- no borrar evidencia histórica/validation/Contract Evidence necesaria para aprendizaje.

---

## 15. Accesibilidad / Samsung / Voice Access

Criterio del operador supera el mínimo WCAG genérico.

Obligatorio:
- cero horizontal scroll global;
- no columnas con caracteres apilados;
- cards/vertical layout cuando tabla no cabe;
- labels semánticos;
- controles textuales;
- no icon-only para funciones críticas;
- paginación/Mostrar más;
- foco estable;
- prueba real de campo Samsung/Voice Access.

Field test real sigue siendo evidencia de campaña: tests automáticos no sustituyen el uso real en la tablet.

---

## 16. Qué se terminó durante el fin de semana

- consolidación RC6 desde RC5 exacta;
- restore SHADOW-first;
- corrección DailyRisk persistence/outbox;
- T+1 fail-closed;
- dashboard truth/semantics;
- responsive table strategy;
- `/validacion` Camino a Producción;
- carriles SHADOW/contrafactuales;
- safety/Telegram fixes probados incorporados;
- disk P0-1;
- P0-4 inventory/read-only/dbstat;
- P0-5 readiness tooling;
- host systemd RC6 migration;
- preopen 10:15;
- candle integrity RC6;
- functional health RC6;
- post-close history RC6;
- backup host RC6;
- A3 background RC6;
- GHCR immutable archive;
- local offline rollback rehearsal real;
- schema compatibility gate;
- Labor Day Apple CEDEAR opening policy;
- Contract Evidence RC6 nativo activo con weekend NOT_DUE proof.

No marcar como “terminado” algo sólo porque tiene código: cada punto debe sostener su estado mediante evidencia de runtime/CI/campaña.

---

## 17. Pendientes de la semana — NO PERDER

Estos pendientes continúan aunque el deploy esté GREEN:

1. Primera rueda PAPER RC6 completa y registro M0–M11.
2. Primera ejecución Contract Evidence realmente DUE en día operativo.
3. Introspección/early-warning observada durante rueda.
4. Reconciliar dashboard scheduler con host truth en cada vista.
5. Field test Samsung/Voice Access.
6. Unified fail-closed market clock.
7. Persistencia de fase idempotente.
8. MFE/MAE real bid-based + provenance.
9. Forward Lab v2.
10. Expectancy telemetry SHADOW.
11. Régimen SHADOW.
12. Concentración sectorial SHADOW.
13. Promoción futura SHADOW→BINDING sólo por evidencia + autorización.
14. Telegram central severity/calendar CRITICAL-unsuppressible.
15. Retention v2.
16. A3 Historical coverage/provenance por familia.
17. History Store full-universe/freshness.
18. close-only salvage effectiveness.
19. candle sampled semantics regression.
20. sector map/correlation/family normalization.
21. Contract Evidence browser/XHR coverage por familia y campos faltantes.
22. Auditoría normativa BYMA y trazabilidad de fuentes oficiales.
23. `/validacion` daily campaign + critical path.
24. Lifecycle de imágenes/backups sólo post-soak suficiente.
25. Auditoría independiente final y consenso antes de M10.
26. Diseño separado de governance real-money, sin habilitarlo durante PAPER.

---

## 18. Freeze de rueda

Cuando la rueda del lunes comience:
- no modificar código/config para “arreglar en caliente”;
- observar y registrar;
- si hay incidente P0, usar rollback;
- no roll-forward improvisado;
- preservar evidencia del fallo.

El objetivo de PAPER no es que el tablero se vea todo verde: es producir evidencia fiable para decidir qué merece BINDING y qué necesita seguir aprendiendo.

---

## 19. Regla de continuidad para futuros chats

Al retomar POROTA:
1. recuperar este checkpoint;
2. verificar SHA/image live;
3. verificar GitHub Actions más reciente;
4. verificar `real_orders_sent=0`;
5. verificar DB/timers/Contract Evidence/A3;
6. continuar desde el primer pendiente real, no desde memoria aproximada;
7. no reabrir decisiones SHADOW/safety/rollback sin nueva evidencia.

Este documento es continuidad técnica; el runtime live es siempre la autoridad final sobre estado actual.
