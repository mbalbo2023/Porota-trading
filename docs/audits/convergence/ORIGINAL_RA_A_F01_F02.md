# RA-A independiente — Issue #469 — F-01/F-02 de #466

Fecha de corte: 2026-10-04/05 UTC  
Objeto inmutable: `c27dfd963c4fe83465c0f2105347e974fbbe6356`  
Tree: `bf3cf193434641aa89e4c746b26c77aec5d1d2b2`  
Base productiva de la orden: `da697c6e6c2274579f9e4a112fabc4327475dd35`  
Modo: `READ_ONLY`; pruebas sólo en copias/SQLite efímeros; PAPER/SHADOW; PPI real no contactado; `real_orders_sent=0` en el harness.

## 1. Dictamen ejecutivo

**`GO_TO_FIX_AND_REAUDIT` / NO-GO para preparación de deploy de este candidato.** F-01 y F-02 no sobreviven el reataque. No encontré P0 en este frente, pero sí **cuatro P1 bloqueantes**:

| ID | Severidad | Resultado |
| --- | --- | --- |
| RA-A-F01-01 | P1 | Un scanner `OPENED_CRITICAL` puede ser dueño del single-flight de la misma identidad y hacer fallar al follower `EXIT_CRITICAL` a los 50 ms; repetible aun con reserva EXIT intacta. |
| RA-A-F01-02 | P1 | El sidecar escribe telemetría antes de podar; al llenar su cuota, niega también EXIT y ya no puede ejecutar la poda que lo recuperaría. Reproducido con el mínimo válido y con el default. |
| RA-A-F01-03 | P1 | Una política válida/activa puede tener capacidad EXIT menor que demanda; sólo lo informa. El orden estable del caller hace que las mismas primeras posiciones monopolicen todos los slots entre ventanas. Safety PASS, liveness FAIL. |
| RA-A-F02-01 | P1 | Rollback del reloj dentro de una lectura, después de `begin_probe` y antes de `outcome`, retrofecha el epoch causal; el worker llegó a `BUY_CANDIDATE` y un fill PAPER con un solo punto genuinamente posterior al comienzo real del reprobe. |

La suite focal nativa terminó **223 passed, 0 failed/error/skipped**, pero no cubre estos contraejemplos. JUnit: `native_focal.xml`, SHA-256 `9bded4473d4f199689a91193aa62ecbee6cb5bb70fc4d1a80fb4ffd0764b6d59`.

## 2. Identidad, autoridad y método

- `git rev-parse HEAD^{commit} HEAD^{tree}` en el checkout auditado devolvió exactamente el commit/tree fijados. `git status --short` quedó vacío antes y después de la inspección.
- `origin/deploy/rc6-pr69-isolated-20260915` resolvió localmente a la base `da697c6…`; el diff base→candidato comprende 140 archivos, `63260 insertions, 472 deletions`.
- Issue #469 se leyó íntegro desde el HTML público de GitHub. `articleBody`: 19.088 caracteres, SHA-256 `0f9c776c353440dbcb934585bf80ca866bdac85e2cd231e7d5fe4721567340a3`.
- Se leyeron las autoridades relevantes #458, #460, #462, #464 y #465; `AGENTS.md`; `ops/policy/porota-policy.yaml`; `docs/audits/ISSUE465_F01.md`; `docs/audits/ISSUE465_F02.md`; código, tests y callers F-01/F-02. No se heredó ninguna conclusión.
- Hashes clave: `AGENTS.md ec4ead8c…`; policy `dd88c6f0…`; F01 doc `adf4f187…`; F02 doc `284f3574…`; `rc6_ppi_global_budget.py a8e99292…`; `cf_intraday_scalping.py 54c8733d…`.
- Fixture histórico #463: commit `caf9bc94b4a1f436ad01a84a9e9e9e7a4a9e9423`, tree `f1712a72cb49435c4403145bb38d98c4006f8f07`. Se mutó únicamente una copia temporal para incorporar el harness preservado.
- Evidencia propia: `audit_evidence/f01_f02/ra_a_f01_f02_probes.py`, SHA-256 `dd3c1efeb06133cea6aad69b70de6120597bf063d67fe1cbecc1d2b6500f4bf5`. El script afirma el SHA antes de ejecutar y usa `TemporaryDirectory`.

Leyenda de evidencia usada más abajo:

- `PROBE`: escenario adversarial propio, ejecutado offline.
- `NATIVE`: test del candidato ejecutado por este auditor; GREEN no equivale a prueba independiente.
- `CODE`: inspección/reconstrucción matemática del caller real.
- `OLD`: reproducción en #463/fixture preservado.
- `NO_VERIFICADO`: requiere PPI/host/runtime externo y no se infiere.

## 3. Reproducción del bug viejo y fix nominal

### F-01

En #463, `book cap=5`, global 15, reserva EXIT 5: cinco `OPENED_CRITICAL` fueron admitidos/iniciados/terminados y los cinco `EXIT_CRITICAL` posteriores quedaron sin capacidad. Contadores: `requested=6, allowed=5, used=5, dropped=1` al primer EXIT; proveedor real 0; órdenes reales 0. **OLD BUG REPRODUCIDO**.

En #466, el caso original directo sí cambia: los cinco OPENED devolvieron `PPI_HIGH_PRIORITY_RESERVE_BACKPRESSURE`; los cinco EXIT fueron admitidos. Contadores exactos:

```text
book requested=10 allowed=5 used=5 dropped=5
window admitted=5 used=5 dropped=5 borrowed_in=0 borrowed_out=0
reserved_total=5 reserved_remaining=0 exit_demand=5 open_positions_count=5
```

Esto prueba el fix estrecho de reserva, no la liveness extremo a extremo.

### F-02

En #463, mismo worker, negative D y válido D+1: llamadas sólo en ciclo `[0]`; no hubo read D+1 ni fills. **OLD BUG REPRODUCIDO**.

En #466, el escenario nominal llamó en ciclos `[0,2]`, conservó un solo reader, reprobó D+1 y quedó en `INTRADAY_CAPABILITY_WARMUP_PENDING`, 0 fills. El fix nominal funciona, pero el epoch de recuperación acepta un reloj que retroceda durante la propia lectura.

## 4. Matemática independiente de F-01

Para cada autoridad viva `a`, sean:

- `R_a = {receipts | receipt.at > now - window_a}`;
- `S_a,e` = receipts consumidos por endpoint `e`;
- `U_a,p,e` = receipts propios de prioridad `p`, endpoint `e`;
- `L_a,e` = límite del endpoint y `G_a` = límite global;
- `Q_a,p,e` = reserva asignada por `_allocate_reserves`, primero por prioridad y dentro de ella en orden `book,current,intraday`.

El remanente `M_a,p,e` se recalcula contra límites residuales, no contra contadores redondeados. Para una solicitud `(e,p)`, `H_a,e,p` es la suma de remanentes de prioridades superiores. La admisión se niega si:

```text
|R_a| >= G_a  OR  S_a,e >= L_a,e                           => EXHAUSTED
S_a,e + H_a,e,p >= L_a,e  OR  |R_a| + sum(H_a,*,p) >= G_a => RESERVE_BACKPRESSURE
```

El préstamo va sólo hacia arriba: primero common; luego reservas de prioridades inferiores, incluso de otro endpoint si la escasez es sólo global. Un lower no toma la reserva EXIT. `requested` incrementa por acquire; `allowed` al crear lease; `used` en `start`; `dropped` en denegación. El intento que choca con `SQLITE_FULL` revierte entero y queda fuera de contadores.

`budget_policy` calcula demanda de book como:

```text
ceil(open_positions × window_seconds / critical_book_seconds)
```

Con 10 abiertas, ventana 30 s y cadence 5 s, demanda declarada = 60. Si `book cap=5`, sólo reserva 5 y expone 55 como no reservadas, pero no bloquea la activación.

## 5. Hallazgos

### RA-A-F01-01 — P1 — inversión de prioridad en single-flight

**Reproducción.** Política con `book cap=6`, reserva EXIT=5 y una unidad common. Un thread scanner entró primero con identidad completa `("GGAL","ACCIONES","BYMA","ARS","A-24HS")`; su fetch duró 120 ms. Un EXIT de idéntica identidad llegó mientras el scanner era owner. Se repitió tres veces, avanzando 6 s entre ciclos para vencer TTL 5 s:

| Ciclo | EXIT observado | Espera | Llamadas provider simuladas |
| --- | --- | ---: | --- |
| 0 | `PPI_BOOK_SINGLE_FLIGHT_BACKPRESSURE` | 0,052753 s | sólo scanner |
| 1 | `PPI_BOOK_SINGLE_FLIGHT_BACKPRESSURE` | 0,053054 s | sólo scanner |
| 2 | `PPI_BOOK_SINGLE_FLIGHT_BACKPRESSURE` | 0,057061 s | sólo scanner |

`coalesced=0`; la reserva EXIT nunca fue consumida. El problema se trasladó desde el bucket de capacidad al ownership off-wire.

**Path.** `ProductionMarketReader.book`, `bd_ppi_readonly_guard.py:390-400`, envía ambos rangos al mismo `coalesced_book` por la identidad completa. `GlobalPPIBudget.coalesced_book`, `rc6_ppi_global_budget.py:614-675`, asigna dueño por llegada y fija `deadline = monotonic()+0.05`, sin comparar prioridades. El provider del owner sí pasa después por `acquire/start/wire_scope/finish` en `bd_ppi_readonly_guard.py:267-321`; el probe atacó directamente el coalescer y la inspección cerró el wiring. `collect_exit_books`, `bv_paper_runtime.py:172-226`, registra el error y continúa; `retry_read` no reintenta `BudgetBackpressure` (`bd_ppi_readonly_guard.py:148-158`).

**Por qué GREEN no lo vio.** `test_shared_book_single_flight_has_no_duplicate_fetch_across_real_processes`, líneas 1578-1602, libera al owner tras 15 ms, dentro del límite fijo de 50 ms. No prueba owner inferior + follower EXIT con latencia >50 ms ni repetición alineada a TTL/cadence.

**Impacto.** Una lectura menor puede negar el único book de salida de esa vuelta aunque haya reserva. La ocurrencia real depende de que login/guard/provider/body excedan 50 ms: latencia PPI/SDK en OPEN es **NO_VERIFICADO**. No se afirma frecuencia productiva. Bajo factual OFF, `RuntimePPIBudget.coalesced_book` deriva a `fetch()` (`rc6_ppi_global_budget.py:920-926`); el defecto corresponde al camino dinámico aprobado que debe quedar listo sin nuevo código.

**RCA/FIX/GUARD/TEST recomendado, no implementado.** Ownership consciente de rango: EXIT debe adjuntarse al resultado hasta un límite coherente con SLA o promover/tomar ownership sin duplicar wire. Guardar rango del owner, garantizar que lower no haga expirar EXIT y no resolverlo sólo ampliando 50 ms. Test real caller con 120 ms, misma identidad, orden lower→EXIT, tres TTL, y assert de book EXIT o degradación que suspenda lower.

### RA-A-F01-02 — P1 — cuota llena impide la poda y bloquea EXIT indefinidamente

**RCA.** `_window_total`, `rc6_ppi_global_budget.py:257-270`, hace `_put(window:<second>)` antes del `DELETE` de buckets >3600 s. `_limit_pages`, líneas 220-225, limita páginas a `maximum_bytes // (2*page_size)`. Cuando el INSERT/REPLACE necesita la siguiente página, la transacción falla antes de alcanzar el DELETE; cada reapertura vuelve a intentar el write primero.

**Probe mínimo válido (`maximum_bytes=65536`).** Una denegación lower/s, política vigente cuatro horas:

```text
13 denegaciones completadas; falla el intento 14 (offset segundo 13)
BudgetBackpressure:PPI_BUDGET_STATE_UNAVAILABLE
cause=OperationalError('database or disk is full')
DB=28672 bytes; page_size=4096; page_count=7; freelist=0
window_rows=13; request_rows=0; journal_mode=delete
```

Tras avanzar 4000 s, un EXIT siguió fallando por el mismo `SQLITE_FULL`, no por expiry. En copia aislada, borrar primero los 13 buckets antiguos permitió inmediatamente el EXIT.

**Probe default (`maximum_bytes=8388608`).** Seis scopes lower distintos/s, todos legítimos para la API y todos denegados por reservas completas:

```text
17.865 denegaciones completadas
falla en offset 2.977 s = 49m37s (cuarta operación de ese segundo)
DB=4.194.304 bytes = 1024 páginas × 4096; freelist=0
window_rows=2.978; request_rows=0; journal_mode=delete
EXIT a +4000 s => PPI_BUDGET_STATE_UNAVAILABLE / SQLITE_FULL
DELETE previo de 2.978 buckets en copia => EXIT allowed
```

Los scopes fueron OPENED/book; SCALPING_HOT, WARM y DISCOVERY/intraday; STRATEGY_HOT/current; WARM/book. El archivo se detiene en 4 MiB porque `max_page_count` usa la mitad de los 8 MiB configurados para reservar margen auxiliar. No hubo WAL; el modo observado fue DELETE.

**Límite de inferencia.** Seis denegaciones/s por 49m37s es estrés sintético dentro de los límites, no una tasa productiva observada. El runtime real combina loops/cadencias y puede producir menos; su frecuencia es **NO_VERIFICADO**. El mínimo 64 KiB sí es aceptado por `validate_policy` (`rc6_ppi_global_budget.py:73-100`) y falla determinísticamente con 1/s en 14 intentos.

**Por qué GREEN no lo vio.** El test de cardinalidad usa default pero sólo 100 identidades y exige `<1 MiB`; no cruza `max_page_count`. Los tests BUSY/locks validan fail-closed, no liveness después de cuota+aging. Ninguno llena, avanza >3600 s y exige autorrecuperación EXIT.

**Impacto.** Fail-closed evita wire incierto, pero ciega toda lectura crítica y requiere intervención externa; envejecimiento lógico no recupera el sidecar. Es liveness critical.

**RCA/FIX/GUARD/TEST recomendado.** Poda/compactación lógica antes de cualquier crecimiento; reservar páginas para housekeeping y estado crítico; agregar/ring bounded que no cree 3601 JSON crecientes; validar mínimo contra worst case. Tests al límite mínimo y default, más `+3600/+4000`, con EXIT autorrecuperado sin operación manual.

### RA-A-F01-03 — P1 — configuración EXIT imposible y starvation estable

**Reproducción.** Política válida con 10 identidades, cap book/global=5 y reserva EXIT=5. En tres ventanas separadas 31 s, el caller conceptual recorrió siempre P00…P09. Resultado idéntico:

```text
allowed = P00..P04
denied  = P05..P09
reserved_total=5; exit_unreserved_demand.book=5
```

El caller real obtiene posiciones con `ORDER BY opened_at,paper_id` (`be_paper_engine.py:536-554`) y `collect_exit_books` recorre en ese orden (`bv_paper_runtime.py:178-225`); no hay round-robin/fair cursor por identidad. En la política generada real, 10 abiertas/30 s/5 s declaran demanda 60 y el gap sería 55.

**Interpretación precisa.** La jerarquía de reservas preserva **safety**: lower no roba las cinco unidades. Falla **liveness**: cinco (o más cadencias) no son servibles, la aprobación no se rechaza y `exit_unreserved_demand` sólo observa. `test_tighter_global_cap_exposes_unserviceable_demand_without_lower_borrow` incluso codifica 3/5 como resultado aceptado; no valida fairness ni bloqueo de activación.

**Impacto.** Posiciones tardías pueden quedar perpetuamente sin book de salida bajo una configuración que el módulo acepta. La configuración/cantidad real del host es **NO_VERIFICADO**, pero #469 exige explicitar y bloquear incompatibilidad antes de activación.

**RCA/FIX/GUARD/TEST recomendado.** Rechazar APPROVED_DYNAMIC si la capacidad garantizable no cubre demanda EXIT declarada, o demostrar un scheduler fair con deadline/cadence por identidad y alarma hard. Test con 10 abiertas/cap5 durante múltiples ventanas debe terminar en `ACTIVATION_BLOCKED`, o mostrar servicio bounded de las diez; una métrica no es guard.

### RA-A-F02-01 — P1 — rollback intra-read retrofecha el epoch de recuperación

**Secuencia exacta, worker productivo largo + SQLite real + normalizador/evaluador/PaperBroker reales; sólo provider y clocks sustituidos:**

1. `2026-10-05 10:46:00 ART` (`13:46Z`): PPI devuelve `Instrument not found`; negative con due `14:01Z`.
2. `11:01:00 ART` (`14:01Z`): `decision` habilita TTL reprobe; `begin_probe` y `_invalidate_intraday_capability` persisten `REPROBE_PENDING` con inicio real `14:01Z`.
3. Dentro de la llamada `reader.intraday`, antes de `received/outcome`, el reloj controlado retrocede a `10:47:40 ART` (`13:47:40Z`). El payload tiene fuente máxima `10:46 ART` (`13:46Z`): fuente ≤ received, edad 100 s, misma sesión ART; no se viola el future-source guard.
4. `received`, `outcome` y `persist_payload` aceptan `13:47:40Z` y publican `warmup_after=recovered_at=13:47:40Z`, **13m20s antes del begin real**.
5. `11:01:01 ART`: primera lectura normal; 13 puntos quedan posteriores al epoch retrofechado; estado HOLD/warmup.
6. `11:03:01 ART`: ya hay 15 puntos posteriores al epoch registrado, pero sólo **1 punto** estrictamente posterior al comienzo real `11:01`. El contrato pasa a `CONFIRMED_INTERVAL_VOLUME`; acciones `[HOLD, BUY_CANDIDATE]`; PaperBroker registra 1 fill simulado; `real_orders_sent=0`.

**Path.** `run_worker`, `cf_intraday_scalping.py:1043-1057`, usa `at` para decision/begin; líneas 1065-1085 obtienen después otro `clock_fn()` para received/outcome sin imponer `received >= probe_started_at`. `IntradayCapabilityCache.outcome`, líneas 252-275, sobrescribe epochs con cualquier `at`. `persist_payload`, líneas 579-690, filtra/contabiliza contra el epoch ya retrofechado y exige 15 puntos allí.

**No sobrerreclamo.** Es un rollback condicionado durante la lectura, no una recuperación ordinaria. No hubo timestamp de fuente posterior a received, salto de sesión ni payload stale. Las lecturas posteriores fueron frescas. La falla es de lineage causal entre comienzo durable y resultado.

**Por qué GREEN no lo vio.** `test_clock_rollback_and_rapid_config_changes_never_bypass_spacing`, líneas 373-381, mueve el reloj entre ciclos antes de `decision`; nunca después de `begin_probe` y antes de `outcome`. Los tests de muerte prueban pending/restart, no rollback intra-read.

**Impacto.** El mínimo de warmup puede satisfacerse con historia anterior al reprobe real y conceder autoridad PAPER prematura. No se llamó ninguna ruta real.

**RCA/FIX/GUARD/TEST recomendado.** Persistir `probe_started_at` inmutable; exigir `received/completed >= started` y no menor al último clock durable. Ante rollback, conservar pending/cooldown cerrado sin publicar recovery epoch. Test del worker real que retroceda desde el callback del reader y exija 0 candidate/fill hasta 15 fuentes estrictamente posteriores al start real.

## 6. Variantes obligatorias §7 — F-01

Cada fila contiene EXPECTED / OBSERVED / PATH-TEST-GAP / VERDICT. `NATIVE GREEN` significa que este auditor lo ejecutó, no que confíe en él como prueba suficiente.

| # | Variante | Evidencia | EXPECTED | OBSERVED / PATH / TEST / GAP | VERDICT |
| ---: | --- | --- | --- | --- | --- |
| 1 | EXIT primero | NATIVE+CODE | 5 EXIT | `reverse_order...[True]` GREEN; admisión por envelopes | PASS estrecho |
| 2 | EXIT último | PROBE+NATIVE | lower bloqueado, 5 EXIT | propios 0/5 lower y 5/5 EXIT; test `[False]` GREEN | PASS estrecho |
| 3 | intercalados | NATIVE+CODE | floor constante | tests de burst/authority GREEN; sin probe de todas las permutaciones | PARTIAL |
| 4 | scanner+scalping+exit simultáneos | NATIVE | EXIT=5, lower=0 | `three_real_processes...` GREEN | PASS nativo; no host |
| 5 | current stale+book fresh | NATIVE | exit usa book | `five_real_positions_stale_scanner...` GREEN | PASS nativo |
| 6 | 5 abiertas | PROBE+NATIVE | 5 exits | contadores exactos arriba | PASS |
| 7 | 10 abiertas/cap5 | PROBE+CODE | bloquear incompatibilidad o fairness | mismas primeras 5 en 3 ventanas | **FAIL P1** |
| 8 | endpoint cap<demand | PROBE+CODE | no claim falso; liveness explícita | métrica gap, activación no bloqueada | **FAIL P1** |
| 9 | global cap<demand | NATIVE+CODE | idem | `tighter_global...`: 3/5 y gap2 aceptado | **FAIL liveness** |
| 10 | 2 procesos | NATIVE | atomicidad | `two_processes_cannot_claim...` GREEN | PASS nativo |
| 11 | 3+ procesos | NATIVE | floor EXIT | multiprocess scanner/scalping/exit GREEN | PASS nativo |
| 12 | dos policies vivas | NATIVE+CODE | unión conservadora | `other_process_policy...`, same-window tests GREEN | PASS nativo |
| 13 | replacement shorter window | NATIVE | no olvidar floor/debt | parametrizado 15/60 y debt tests GREEN | PASS nativo |
| 14 | replacement larger cap | NATIVE | no gastar promesa previa | endpoint/global cases GREEN | PASS nativo |
| 15 | expiry mid-window | NATIVE | no emitir; retener deuda/promesa | expired authority/start expiry GREEN | PASS nativo |
| 16 | clock rollback | NATIVE | deny off-wire | budget rollback tests GREEN | PASS nativo |
| 17 | retry lower | NATIVE | retry no evade floor | `actual_http_scanner_retry...` GREEN | PASS nativo |
| 18 | retry EXIT | CODE+NATIVE | bounded y no duplicado | retry sólo transient; BudgetBackpressure no retry; sin slow-flight EXIT | **GAP / ligado P1-01** |
| 19 | 429 antes de EXIT | NATIVE | global breaker | 429 scanner/restart GREEN | PASS nativo |
| 20 | 429 durante EXIT | NATIVE | semántica durable | 429 EXIT case GREEN | PASS nativo |
| 21 | 5xx threshold | NATIVE | endpoint+global breaker | repeated 408/5xx GREEN | PASS nativo |
| 22 | SQLite BUSY pre-wire | NATIVE | bounded, 0 wire | `db_locked...`, constructor/flapping GREEN | PASS nativo |
| 23 | SQLite BUSY post-lease | NATIVE | deuda no desaparece/no resend | finish/scope/sdk lock matrix GREEN | PASS nativo |
| 24 | abandoned lease | NATIVE | bloqueo hasta expiry | lease restart/abandoned GREEN | PASS nativo |
| 25 | kill acquire→start | NATIVE | fail closed | process mutex/crash lease GREEN; no custom nuevo | PASS nativo |
| 26 | kill start→finish | NATIVE | debt/serial persisten | process/streaming/body tests GREEN | PASS nativo |
| 27 | misma identidad scanner+exit | PROBE+CODE | coalesce sin degradar EXIT | lower owner >50ms niega EXIT 3/3 | **FAIL P1** |
| 28 | identidades completas distintas mismo ticker | NATIVE | no colapsar | full-identity cache test GREEN | PASS nativo |
| 29 | coalesced book | PROBE+NATIVE | 1 wire y EXIT vive | 15ms GREEN; 120ms EXIT backpressure | **FAIL P1** |
| 30 | stale cache book | NATIVE | no reutilizar | stale/future/malformed/circuit tests GREEN | PASS nativo |
| 31 | partial reserve+burst | NATIVE+CODE | préstamo sólo ascendente | exact boundary/borrow telemetry GREEN | PASS nativo |
| 32 | DISCOVERY burst | NATIVE | no roba EXIT | every-lower test GREEN | PASS nativo |
| 33 | WARM/HOT burst | NATIVE | jerarquía | WARM/SCALPING/STRATEGY tests GREEN | PASS nativo |
| 34 | current pressure/book intact | NATIVE+CODE | book floor intact/global coherente | reverse burst + real positions GREEN | PASS nativo |
| 35 | accounting global | PROBE+NATIVE | requested/allowed/used/dropped exactos | 10/5/5/5 propio; scope/borrow tests GREEN | PASS para caso; cuota falla antes de registrar intento |
| X1 | cuota mínima + aging | PROBE | poda permite EXIT | full en intento14; +4000 sigue full | **FAIL P1** |
| X2 | cuota default + aging | PROBE | bounded y autorrecuperable | 49m37s sintéticos; +4000 falla; prune-first recupera | **FAIL P1** |

## 7. Variantes obligatorias §8 — F-02

| # | Ataque | Evidencia | EXPECTED | OBSERVED / PATH / TEST / GAP | VERDICT |
| ---: | --- | --- | --- | --- | --- |
| 1 | negative D / válido D+1, mismo worker | OLD+PROBE+NATIVE | reprobe READ_ONLY | #463 no llamó D+1; #466 `[0,2]`, warmup, 0 fill | PASS nominal |
| 2 | válido tras TTL misma sesión | NATIVE | un reprobe | TTL boundary GREEN | PASS nativo |
| 3 | múltiples negatives | NATIVE | no hammer | many cycles/failed reprobe GREEN | PASS nativo |
| 4 | exponential cooldown cap | NATIVE | 900/1800/3600 cap | test exacto GREEN | PASS nativo |
| 5 | config fingerprint change | NATIVE | reprobe, spacing | semantic config test GREEN | PASS nativo |
| 6 | catalog clock-only change | NATIVE+CODE | no hammer | catalog refresh/identity tests GREEN | PASS nativo |
| 7 | cambio real de contrato | NATIVE | reprobe | catalog rebind GREEN | PASS nativo |
| 8 | request/ticker ambiguo | NATIVE+CODE | scope exacto/fail closed | sibling settlement/rebind/toxic lineage GREEN | PASS nativo |
| 9 | cache eviction | NATIVE | durable denial sobrevive | memory/rotating universe GREEN | PASS nativo |
| 10 | restart | NATIVE | cooldown/warmup durable | restart test GREEN | PASS nativo |
| 11 | JSON corrupto | NATIVE | fail closed/bounded | truncated/missing/wrong/oversized GREEN | PASS nativo |
| 12 | timestamps futuros | NATIVE | reject | direct persistence + future_due/toxic clocks GREEN | PASS nativo |
| 13 | rollover UTC/ART | NATIVE+CODE | sesión ART | next-session tests GREEN | PASS nativo |
| 14 | 429 con body engañoso | NATIVE | global, no negative | native SDK matrix GREEN | PASS nativo |
| 15 | 404 vs string exacta | NATIVE+CODE | sólo exact `Instrument not found` | classifier + native matrix GREEN | PASS nativo |
| 16 | malformed tras reprobe | NATIVE | no recovery/hammer | empty/stale/malformed parametrizado GREEN | PASS nativo |
| 17 | recovery + old candidate | NATIVE | no autoridad vieja | old history/candidate timestamp GREEN | PASS nativo |
| 18 | recovery + stale book | CODE+NATIVE | broker gate conserva freshness | candidate timestamp y book cache tests separados; sin probe combinado propio | PARTIAL |
| 19 | repeated timestamp | NATIVE+CODE | no contar como nueva evidencia | overlap/revision/warmup tests GREEN; sin rollback intra-read | PASS nominal/PARTIAL |
| 20 | warmup mínimo | PROBE+NATIVE | 15 puntos posteriores al inicio causal real | normal GREEN; rollback dio 15 vs sólo 1 real y fill | **FAIL P1** |

## 8. Otros controles relevantes que sí quedaron verdes

La corrida focal también ejerció: 429/401/403 y session-invalid; 408/5xx; BUSY y locks antes/después de wire; journal/WAL rechazo; hard/symlinks y cuota superficial; leases abandonados; expiración entre acquire/start; mutex multiproceso; exact full identity; stale/future/crossed/NaN books; cache TTL/restart/fingerprint; corrupt capability lineage; LRU bound; process death después de durable probe start. Es evidencia útil, pero no compensa los cuatro contraejemplos.

## 9. Comandos y recibos

```bash
# Identidad/read-only
git status --short
git rev-parse HEAD^{commit} HEAD^{tree}
git rev-parse da697c6e6c2274579f9e4a112fabc4327475dd35^{commit}

# Suite focal, sin cache en checkout compartido
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
PYTHONPATH=/workspace/scratch/059486315731/verifydeps:/workspace/scratch/1db6b9897804/porota-fix/.venv/lib/python3.12/site-packages \
python3 -m pytest -q -p no:cacheprovider \
  tests/test_issue465_budget_adversarial.py \
  tests/test_rc6_ppi_global_budget.py \
  tests/test_issue465_capability_cache.py \
  --basetemp=/workspace/scratch/10843fa5dbba/audit_evidence/f01_f02/pytest_tmp \
  --junitxml=/workspace/scratch/10843fa5dbba/audit_evidence/f01_f02/native_focal.xml
# 223 passed; 22.339 s

# Contraejemplos propios
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH=/workspace/scratch/059486315731/verifydeps:/workspace/scratch/1db6b9897804/porota-fix/.venv/lib/python3.12/site-packages \
python3 /workspace/scratch/10843fa5dbba/audit_evidence/f01_f02/ra_a_f01_f02_probes.py
# exit 0; ~35 s; JSON reproducible impreso a stdout
```

El script de probes no guarda DBs: todos se destruyen con el `TemporaryDirectory`. El JUnit y este informe son los únicos artefactos persistentes de este frente además del script. El checkout `porota-audit` no fue modificado.

## 10. Límites y estado de verificación

| Claim | Estado |
| --- | --- |
| Código/tree exacto del objeto | VERIFIED localmente |
| 223 tests focales | VERIFIED ejecutados, pero NATIVE |
| Cuatro contraejemplos | VERIFIED offline en SHA exacto |
| `real_orders_sent=0` en harness F02 | VERIFIED |
| Rutas reales contactadas | `NOT_CALLED` por esta auditoría |
| Latencia SDK/PPI OPEN >50 ms | **NO_VERIFICADO**; finding single-flight es condicional a esa latencia |
| Frecuencia productiva de seis denegaciones/s | **NO_VERIFICADO**; sólo stress sintético |
| Política/cantidad de abiertas en host | **NO_VERIFICADO** |
| Estado efectivo del candidato en host | **NO_VERIFICADO**; no hubo SSH/runtime mutation |
| PPI Watch | no observado ni tocado |

## 11. Bloqueos exactos antes de nueva reauditoría

1. Corregir o desactivar single-flight dinámico hasta garantizar prioridad/liveness EXIT bajo latencia >50 ms.
2. Hacer que el sidecar pueda podar/recuperarse incluso estando al límite, con test mínimo/default y horizonte >3600 s.
3. Bloquear activación cuando `exit_reserved < exit_demand`, o probar fairness/deadline por identidad.
4. Anclar F-02 a `probe_started_at` durable y rechazar rollback entre begin y completion.
5. Repetir probes propios y suite, además de runtime PAPER controlado; no basta con renombrar/agregar tests al mismo supuesto.

Ninguna conclusión de este informe autoriza fix, merge, deploy ni activación de capacidad.
