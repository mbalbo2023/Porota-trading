# Issue #469 — Reauditoría independiente RA-A F-03/F-04/F-05

**Objeto exclusivo:** candidato #466, commit
`c27dfd963c4fe83465c0f2105347e974fbbe6356`, tree
`bf3cf193434641aa89e4c746b26c77aec5d1d2b2`; base productiva declarada
`da697c6e6c2274579f9e4a112fabc4327475dd35`.

**Fecha de corte:** 2026-10-04 UTC. **Modo:** READ_ONLY, PAPER/SHADOW.
`real_orders_sent=0`, rutas reales `NOT_CALLED`, PPI Watch `UNTOUCHED`.

## Dictamen del frente

**GO_TO_FIX_AND_REAUDIT.** No demostré P0/P1 ni una orden real, mutación de la
base productiva o regresión de las rutas PAPER factuales. Sí reproduje nueve gaps
nuevos, ocho de ellos P2 materiales para evidencia/operación institucional:

1. sin archivador productivo, el caller real entra en soft pressure a **+40,5
   minutos** y el intento de +50,5 minutos deja de publicar evidencia;
2. aun con rotación ideal, el ledger de ACK que nunca se compacta vuelve a
   agotar la misma cuota en menos de una rueda;
3. texto arbitrario de errores IOL llega sin allowlist al reporte committed;
4. una reescritura adversarial completa, con todos los hashes recalculados,
   pasa el lector e incluso admite campos de seguridad contradictorios;
5. un ACK local autoafirmado, sin prueba autenticada del archivo externo, es
   autoridad suficiente para borrar una generación;
6. EIO después del primer `unlink` de una rotación deja una generación parcial
   que un reinicio ya no puede continuar rotando;
7. rollback de `CURRENT` seguido de un commit crea un fork con secuencia
   duplicada;
8. la persistencia acepta un `source_audit` semánticamente falso si conserva el
   digest de la lista; además acepta duplicados exactos de reportes.

Los fixes originales sí mejoran sustancialmente el baseline: los 115 tests
focales pasan, las siete fronteras inyectadas de commit publican sólo un corte
viejo o nuevo completo, SIGKILL/ENOSPC/EACCES/truncación/aliases/traversal/
colisión fallan cerrado en los tests ejecutados, los locks niegan segundo
writer/reader concurrente, y PPI/IOL/BYMA conservan clocks, unidades, estados y
carácter `OBSERVE_ONLY` en los casos focales. Eso no neutraliza los P2 anteriores.

## Autoridad, alcance y método

Leí íntegramente la orden #469 y sus autoridades #464/#465, además de:

- `docs/audits/ISSUE465_F03.md`;
- `docs/audits/ISSUE465_F04_F05.md`;
- `docs/audits/ISSUE465_REAUDIT.md` y el reporte original;
- `docs/runbooks/ISSUE465_RETENTION.md`;
- `rc6_shadow_runtime/{persistence,retention,worker}.py`;
- `rc6_dynamic_universe/{sources,live,promotion}.py`;
- `iol_shadow_collector_rc6.py`;
- callers y los tres módulos de tests focales completos.

Ejecuté el código desde una copia creada con `git archive` del objeto exacto,
sin `.git`, y las reproducciones históricas desde blobs Git exactos. Todas las
mutaciones ocurrieron en `TemporaryDirectory`; el checkout compartido terminó
limpio y con el mismo SHA/tree. No usé red, proveedor, broker, SSH, runtime ni DB
productivos. La única SQLite escrita fue un `PaperStore` sintético efímero para
ejecutar el caller real.

Evidencia reproducible:

- `audit_evidence/f03_f05/execution_log.md` — comandos y salidas;
- `audit_evidence/f03_f05/independent_probe.py` y su JSON;
- `audit_evidence/f03_f05/full_tick_retention_probe.py` y su JSON;
- `audit_evidence/f03_f05/source_secret_full_tick_probe.py` y su JSON.

La suite focal recolectó **48 + 18 + 49 = 115** tests y terminó 115/115 GREEN.
El replay histórico #463 terminó con los dos RED esperados: un reporte recibido
con cero snapshots de auditoría y una falla de cuota sin métricas. El probe
histórico F-03 volvió a producir `latest=t`, `checkpoint/status=t-30s` tras
ENOSPC. Estos RED históricos validan las fallas anteriores; no se usaron como
prueba de que el candidato actual falle.

## Registro de findings

| ID | Sev. | Dominio | Estado | Resultado verificable |
|---|---:|---|---|---|
| RA-A-01 | P2 material | F-05 liveness | FAIL | caller real: soft +40,5m; hard +50,5m sin archiver |
| RA-A-02 | P2 material | F-05 ledger | FAIL | ACK 505 admite 512; ACK 506 proyecta 513 y deniega |
| RA-A-03 | P2 material | F-04 confidencialidad | FAIL | marcador sintético de error IOL persiste committed |
| RA-A-04 | P2 material | F-03 integridad | FAIL | rehash integral acepta report `orders=7` vs status `0` |
| RA-A-05 | P2 material condicionado | F-05 trust/archivo | FAIL | ACK autoafirmado borra evidencia sin abrir/verificar archive |
| RA-A-06 | P2 material | F-05 crash/restart | FAIL | EIO parcial deja rotación no reanudable |
| RA-A-07 | P2 material | F-03 anti-rollback | FAIL | rollback aceptado; commit siguiente duplica `sequence=2` |
| RA-A-08 | P2 material | F-04 binding | FAIL | reporte no vacío + audit vacío/misleading commits y relee |
| RA-A-09 | P3 | F-04 duplicación | FAIL | dos reportes idénticos son aceptados y contados como dos |

### RA-A-01 — P2 material: el caller real se autoagota en 50,5 minutos

**Reproducción.** `full_tick_retention_probe.py` ejecuta
`ShadowRuntime.tick` —no sólo `commit_generation`— cada 30 segundos sobre un
`PaperStore` sintético. Resultado exacto: generación 82 en soft pressure a
+40,5m; 101 generaciones committed; el intento siguiente, a +50,5m, lanza
`RetentionPressure(RETENTION_HARD_FILES_CAPACITY_REACHED)` con 508 entradas y
514 proyectadas. Había sólo 2.449.088 bytes lógicos: el límite vinculante fue el
número de entradas, no 128 MiB.

**Cadencia y aritmética.** `worker.py:110-114` fija `tick_seconds=30` y
`worker.py:361-391` hace `stop.wait(30)`. Cada generación deja cinco entradas
permanentes: directorio, report, checkpoint, status y manifest.
`persistence.py:335-341` reserva seis para el pico del próximo commit: staging
dir + cuatro miembros + temporal de `CURRENT`. El caller completo agrega
`writer.lock`, `CURRENT.json` y el freeze preopen.

**Archivador real.** `git grep` del SHA, excluyendo docs/tests, sólo encuentra
`archive-ack-*`/schema/pins dentro del lector/validador de `retention.py`; no hay
writer ni caller productivo de archivo/ACK. El runbook, líneas 28–34, confirma
que 512 entradas pueden alcanzarse dentro de una rueda y que la misión no
habilita un destino productivo.

**Impacto.** La telemetría no cubre una rueda. Además, por inspección de código,
`promotion.py:244-261` rechaza un reporte cuyo retention esté en soft pressure;
por tanto un consumidor de capacidad dinámica ya cae a baseline desde +40,5m.
El probe no activó capacidad productiva y no demuestra una orden o pérdida; sí
demuestra que el componente que debía producir evidencia continua queda no
operable en menos de una hora.

**RCA.** Cadencia 30s × cinco entradas por corte, cuota fija 512 y política
fail-closed fueron diseñadas sin un lifecycle productivo que satisfaga el
horizonte operativo.

**FIX/GUARD/TEST recomendado, no implementado.** Definir primero el horizonte
mínimo institucional (rueda + restart/RTO + margen), agregar un archiver durable
autorizado o cambiar la granularidad/almacenamiento, y usar una métrica de
`minutes_to_hard` además del porcentaje. El test obligatorio debe ejecutar el
caller real a 30s durante al menos siete horas/una rueda, reiniciar procesos y
probar que ni publicación ni consumidor entran en pressure sin una recuperación
demostrada.

### RA-A-02 — P2 material: el ACK ledger también tiene vida finita

**Reproducción.** Tras la rotación, el diseño conserva un archivo
`archive-ack-<id>.json` por generación (`ISSUE465_RETENTION.md:101-106`). En un
fixture que representa exactamente ese estado post-rotación, 505 ACK +
`writer.lock` + reserva 6 da 512 y admite; 506 da 513 y deniega; 507 da 514 y
deniega. No existe caller de compactación/rotación del ledger en el candidato.

**Impacto.** Un archivador perfecto sólo cambia el horizonte: no elimina el
autoagotamiento. En el fixture deliberadamente favorable, 506 ACK a 30s dan un
límite superior de **253 minutos (4h13m)** antes de negar el próximo commit; un
sistema real conserva además CURRENT, freeze y generaciones pinned, por lo que
será antes. No afirmo un tiempo productivo observado: es un límite derivado y
reproducido offline.

**RCA.** El ACK es audit ledger durable pero comparte la cuota de alta cadencia
y no tiene checkpoint/compaction con anclaje externo.

**FIX/GUARD/TEST.** Diseñar ledger append-only autenticado fuera de esta cuota o
checkpoint compacto/WORM con prueba de inclusión; nunca borrar recibos sin otro
anclaje durable verificable. Testear miles de ciclos archive→ACK→rotate a 30s,
restart, replay e integridad del historial.

### RA-A-03 — P2 material: sink de texto arbitrario en evidencia F-04

**Reproducción real de caller.** `source_secret_full_tick_probe.py` colocó sólo
un marcador sintético `token=SYNTHETIC_CREDENTIAL_MARKER_469` en `reason` y
`errors` de un cache IOL efímero. `ShadowRuntime.tick` produjo
`PARTIAL_SOURCE_ERRORS`; el marcador quedó en `native_reason`, en el error
estructurado y en el report committed. Cero llamadas de red/proveedor/DB
productiva y cero órdenes.

**Paths/symbols.** `iol_shadow_collector_rc6.py:294-308` construye
`reason=f"{type(exc).__name__}:{str(exc)[:160]}"`.
`sources.py:_source_errors`, líneas 201–217, llama “labels only” a un split que
conserva hasta el segundo fragmento textual; `source_observations`, líneas
309–316, copia `row.reason` completo a `native_reason`.

**Impacto y límite.** Está probado que texto arbitrario llega a evidencia
privada y queda elegible para archivo; no está probado que el marcador sea una
credencial real ni que el archivo sea público. El riesgo es confidencialidad y
retención ampliada si una excepción real contiene token, URL firmada, cuenta o
fragmento de body.

**RCA.** Truncar longitud no sanitiza; no hay taxonomy/allowlist en el límite de
ingesta F-04.

**FIX/GUARD/TEST.** Persistir sólo reason codes cerrados y clases allowlisted;
descartar/redactar texto libre antes del cache y volver a sanitizar al construir
source evidence. Agregar fixtures con bearer, query token, account y response
body y un secret scan sobre los tres miembros de la generación descomprimidos.

### RA-A-04 — P2 material: hashes no autenticados y safety semántico no ligado

**Reproducción.** El probe reescribió report/checkpoint/status, recalculó
payload digests, cross hashes, hashes del manifest y digest/hash de CURRENT. El
lector aceptó la generación como válida con
`report.real_orders_sent=7`, `status.real_orders_sent=0` y `report.number=999`.

**Path/symbol.** `persistence.py:189-246` valida exhaustivamente consistencia de
SHA/digests, pero todos son SHA-256 no autenticados y viven bajo la misma raíz.
Las comprobaciones lógicas de líneas 234–246 no exigen igualdad de campos safety
entre roles.

**Impacto y threat model.** Esto no refuta la atomicidad frente a crash ni la
detección de corrupción accidental, que pasan. Requiere un actor comprometido
con escritura sobre toda la raíz; bajo ese modelo la evidencia no es
tamper-evident para un auditor externo. El consumidor de promoción comprueba el
campo del report y fallaría cerrado ante `7`, por lo que no demostré orden real.
Sí se puede falsificar evidencia de auditoría y presentar roles contradictorios.

**RCA.** Hashes autocontenidos prueban consistencia, no autenticidad; falta una
invariante semántica cross-role de campos safety.

**FIX/GUARD/TEST.** Anclar cada manifest en un journal/WORM o firma/MAC cuya clave
y high-water estén fuera de la raíz; exigir igualdad exacta de
`real_orders_sent`, rutas, mode/as_of y otros safety fields en los tres roles.
Test negativo de rehash integral, no sólo de un miembro con cross-links viejos.

### RA-A-05 — P2 material condicionado: ACK autoafirmado autoriza borrado

**Reproducción.** Se creó una generación vieja completa, una CURRENT, y un ACK
con manifest hash correcto pero `archive_sha256="a"*64` y URI sintética cuyo
archivo no existe. Bajo presión, `prepare` borró la generación, reportó una
rotación y dejó el ACK. Hubo cero open/hash/consulta al archive externo.

**Path/symbol.** `retention.py:_acknowledged`, líneas 187–219, verifica la
generación local y sólo forma/no-cero del archive hash, URI no vacía y booleanos
`archive_verified/durable`; no hay firma, identidad de writer ni recibo externo.
El runbook delega esa veracidad a “un proceso autorizado”.

**Impacto y límite.** El caller canónico no escribe ACK y hoy no dispara este
flujo naturalmente. El riesgo aparece cuando se integre el archiver exigido por
RA-A-01, o ante un writer local/archiver defectuoso: una atestación falsa puede
liberar evidencia sin copia durable. Un atacante con write directo también
podría borrar archivos; el punto específico es que la API de control convierte
una afirmación no autenticada en autoridad legítima de borrado.

**RCA.** El trust model está documentado pero no materializado criptográfica ni
operacionalmente en la interfaz ACK.

**FIX/GUARD/TEST.** ACK firmado por identidad allowlisted y ligado al digest del
objeto en un store durable/WORM; verificación independiente del receipt antes de
rotar; URI sin credenciales y esquema allowlisted. Negativos: hash inventado,
URI inexistente, writer no autorizado, replay y archive desaparecido.

### RA-A-06 — P2 material: rotación no es restart-idempotent tras unlink parcial

**Reproducción.** Se inyectó `EIO` en el segundo `os.unlink` dentro de una
generación ACKed. Primer resultado:
`RETENTION_ARCHIVE_ROTATION_FAILED`; quedaron `manifest.json`,
`report.json.gz`, `status.json`. En un nuevo objeto `EvidenceRetention`, el ACK
ya no puede verificar el miembro faltante; el retry devuelve
`RETENTION_HARD_FILES_CAPACITY_REACHED` y no recupera.

**Path/symbol.** `retention.py:_remove_flat_directory`, líneas 168–185, borra
miembros uno por uno dentro del namespace final. `_acknowledged`, 187–219,
requiere luego los tres miembros completos. `_prepare`, 328–347, sólo reintenta
generaciones todavía verificables.

**Impacto.** Un EIO/crash en la ventana convierte evidencia ya ACKed en un
directorio parcial que no es ni readable ni rotatable automáticamente, y puede
dejar SHADOW permanentemente en hard pressure. No ejecuté SIGKILL literal justo
dentro de la rotación; el EIO después del primer unlink reproduce el estado
persistente relevante. Los SIGKILL del commit sí se ejecutaron en la suite.

**RCA.** Borrado destructivo multi-step en el namespace final, sin tombstone de
deletion ni recovery state durable.

**FIX/GUARD/TEST.** Tras validar ACK, renombrar atómicamente a un namespace de
deletion propio, fsync de root, y limpiar allí de modo reanudable; nunca volver a
interpretar un tombstone como generación. Fault injection/SIGKILL después de
cada rename/unlink/fsync, restart y convergencia idempotente.

### RA-A-07 — P2 material: rollback completo produce fork de secuencia

**Reproducción.** Tras commits sequence 1 y 2, se restauró el `CURRENT` válido de
sequence 1. El reader devolvió el corte 1 completo. El commit siguiente derivó
`pointer.sequence + 1` y creó otra sequence 2 con distinto generation ID. Ambas
sequence 2 permanecieron en disco.

**Path/symbol.** `persistence.py:180-252` sólo sigue CURRENT;
`commit_generation`, líneas 292–333, toma sequence/previous exclusivamente del
pointer seleccionado y no valida un high-water ni la cadena existente.

**Impacto y límite.** Aceptar un corte previo completo es una decisión explícita
y no es un mixed-generation/atomicity bug. El defecto es anti-rollback/lineage:
un restore de snapshot, error operativo o writer malicioso crea una bifurcación
con secuencia duplicada. `promotion.selection` limita reportes a `hot_seconds`
(120s en el perfil focal), por lo que un rollback viejo falla freshness; uno
reciente puede ser consumido. No demostré activación productiva.

**RCA.** CURRENT es autoridad única mutable y no existe high-water autenticado
fuera de esa autoridad.

**FIX/GUARD/TEST.** Journal monotónico autenticado o chequeo bounded de cadena y
rechazo de sequence/parent ya bifurcado; procedimiento explícito de disaster
recovery. Test: rollback dentro y fuera de freshness, restart y commit, y rechazo
de secuencia duplicada.

### RA-A-08 — P2 material: el binding F-04 valida digest, no significado

**Reproducciones.** Con un source report PPI válido se entregó
`source_audit={status:NO_SOURCE_REPORTS, source_report_count:0, snapshots:{},
source_reports_digest:digest([report])}`. Tanto commit como read aceptaron la
generación. Con lista vacía también se aceptó un audit que afirmaba un snapshot
`ghost` saludable.

**Path/symbol.** `persistence.py:305-307` y 241–246 condicionan la comprobación
al digest de la lista y, para vacío, no la hacen. No verifican schema, as_of,
count, status, pointers ni digest individual de snapshots. `sources.py:audit_sources`
sí construye esos campos correctamente, pero la frontera durable no lo exige.

**Impacto y límite.** El caller canónico actual llama `audit_sources` y produjo
evidencia coherente en los tests/probes. Esto es un gap de guard ante caller
defectuoso, corrupción rehasheada o integración futura; permite reaparecer la
contradicción conceptual F-04 dentro de una generación “válida”.

**RCA.** Se ligó la lista por digest, no el contrato canónico completo.

**FIX/GUARD/TEST.** Recomputar `audit_sources(reports=..., as_of=...)` en el
writer/reader o validar toda su forma y enlaces tanto para cero como para N.
Negativos con count/status/snapshot/pointer/digest/as_of falsos.

### RA-A-09 — P3: duplicados exactos inflan el modelo de source evidence

`audit_sources(reports=[r, deepcopy(r)])` acepta `source_report_count=2`.
`sources.py:359-404` itera por índice, valida counts/cutoff, pero no unicidad ni
multiplicidad declarada. El caller normal agrupa fuentes y no reproduje un
duplicado natural. Recomendación: rechazar report digests repetidos o modelar
multiplicidad con una razón y clave `(source,path,cutoff)`; testear duplicate
rows/reports y contadores no inflados.

## Matriz adversarial ejecutada

Leyenda: **PROBE** = contraejemplo independiente; **NATIVE** = test del candidato
que ejecuté, no tomado por fe; **CODE** = inspección estática; **NO EJEC.** = no
se hizo esa variante exacta. `PASS` significa que el control observado satisface
el caso; no es una aprobación global.

| Escenario | Tipo | EXPECTED | OBSERVED | PATH / TEST | GAP | VERDICT |
|---|---|---|---|---|---|---|
| F-03 baseline ENOSPC entre archivos #463 | PROBE | reproducir mixed cut viejo | latest t; checkpoint/status t−30s | `historical_f03_mixed_snapshot` | histórico, no actual | RED histórico |
| 7 fronteras report→checkpoint→status→fsync→CURRENT | PROBE | sólo old/new completo | 7/7 coherentes; post-pointer=new | `atomic_faults_and_locking` | exception, no power-cut físico | PASS |
| SIGKILL real en fronteras de commit | NATIVE | staging nunca CURRENT | test pasa en todos los points | `test_real_sigkill_at_every_boundary...` | host/fs real no probado | PASS |
| restart worker tras kill | NATIVE | reusar sólo checkpoint committed | test pasa | `test_canonical_worker_restart_after_kill...` | fixture local | PASS |
| ENOSPC de `fsync` | NATIVE | CURRENT anterior intacto | pasa | `test_enospc_from_real_fsync...` | FS real no agotado | PASS |
| EACCES/directorio read-only | NATIVE | preservar CURRENT | pasa | `test_actual_read_only_directory...` | depende permisos Unix | PASS |
| gzip truncado/zip expansion | NATIVE | reject bounded | ambos pasan | tests `truncated_gzip`, `compressed_payload` | — | PASS |
| member symlink/hardlink/FIFO | NATIVE | reject antes de usar | pasa | `test_hostile_aliases...` | races hostiles no exhaustivas | PASS |
| manifest traversal | NATIVE | nunca leer path externo | pasa | `test_manifest_path_traversal...` | — | PASS |
| UUID collision/clock rollback | NATIVE | no overwrite | pasa | `test_uuid_collision...`; clock test | rollback de CURRENT distinto | PASS parcial |
| dos writers + reader durante writer | PROBE | deny nonblocking | ambos `DENIED_NONBLOCKING` | `atomic_faults_and_locking` | reemplazo hostil de lock no probado | PASS |
| rehash integral malicioso | PROBE | evidencia adulterada no válida | aceptada; report orders 7/status 0 | `fully_rehashed_forgery` | sin auth anchor | **FAIL P2** |
| rollback CURRENT + nuevo commit | PROBE | lineage monotónico/no fork | sequence 2 duplicada, IDs distintos | `current_rollback_fork` | older cut completo por diseño | **FAIL P2** |
| PPI único y counts | PROBE/NATIVE | seen1/useful1 | exacto | `source_audit_probes`; F04 tests | — | PASS |
| PPI vs IOL contradictorios | NATIVE | clocks/units/conflict, no authority | pasa | `test_ppi_iol_disagreement...` | sin provider live | PASS |
| BYMA scraper | NATIVE | `OBSERVE_ONLY`, link real | pasa | `test_byma_scraper...` | sin rueda live | PASS |
| provider clock missing/stale/future | PROBE/NATIVE | rechazo explícito | razones correctas | `source_audit_probes`; parametrizado | — | PASS |
| IOL parcial | PROBE/NATIVE | partial, availability unknown | `PARTIAL_SOURCE_ERRORS`, None | test/probe | texto libre separado | PASS parcial |
| texto arbitrario en error IOL | PROBE full tick | sanitizado/allowlist | persiste committed | `source_secret_full_tick_probe.py` | no se usó secreto real | **FAIL P2** |
| report counts/cutoff/row shape falsos | NATIVE | reject | reject | `test_incoherent_source_reports...` | no cubre audit semantics | PASS |
| report no vacío + audit vacío/misleading | PROBE | reject | commit/read aceptan | `source_audit_probes` | digest de lista sí coincide | **FAIL P2** |
| reports vacíos + audit ghost | PROBE | canonical NO_SOURCE_REPORTS | audit ghost aceptado | `source_audit_probes` | guard condicional | **FAIL P2** |
| report duplicado | PROBE | reject/dedupe explícito | count=2 | `source_audit_probes` | caller natural no demostrado | **FAIL P3** |
| bounds 64 reports/100k rows | NATIVE | fail sin truncar | pasa | `test_source_audit_capacity...` | no benchmark extremo | PASS |
| cuota files 512→513 | NATIVE/PROBE | 512 admite; 513 niega | exacto | policy test; ACK probe | lifecycle insuficiente | PASS local / FAIL sistema |
| bytes lógicos >128MiB/sparse | NATIVE | deny, no evasión sparse | pasa | `test_logical_bytes_above...` | — | PASS |
| no ACK bajo presión | NATIVE | nunca borrar | preserva y degrada | `test_no_ack_never_rotates...` | causa hard <1h | PASS safety / FAIL liveness |
| ACK válido + pin/current | NATIVE | rota sólo unpinned | pasa | rotation/pin/current tests | auth externa ausente | PASS local |
| ACK autoafirmado/URI inexistente | PROBE | no borrar sin receipt real | borra gen; 0 archive calls | `self_asserted_archive_ack` | trust delegado | **FAIL P2 cond.** |
| temporales propios/unknown | NATIVE | limpia allowlist; conserva unknown | pasa | temp cleanup tests | — | PASS |
| low disk/ENOSPC/EACCES/EIO | NATIVE | motivo explícito, preserve | pasa | no-space/permission parametrizados | hardware no probado | PASS |
| concurrent retention durante writer | NATIVE | `RETENTION_WRITER_BUSY` | pasa | active-writer staging test | multiprocess stress acotado | PASS |
| EIO tras primer unlink de rotación | PROBE | retry idempotente | partial gen; retry hard | `retention_crash_during_rotation` | SIGKILL exacto NO EJEC. | **FAIL P2** |
| caller real sin archivo | PROBE full tick | horizonte ≥ rueda | soft 40,5m; hard 50,5m | `full_tick_retention_probe.py` | fixture, no runtime prod | **FAIL P2** |
| ACK ledger post-rotación | PROBE | no agotar cuota activa | ACK506 proyecta513 | `retained_ack_ledger_capacity` | fixture favorable | **FAIL P2** |

## Persistencia, SQLite, concurrencia y recovery

- **SQLite:** `worker._metadata` abre con URI `mode=ro`, activa
  `PRAGMA query_only=ON` y usa timeout/progress handler. En el caller probe sólo
  se escribió la DB sintética al prepararla; no se observó write desde el
  worker. No hubo prueba contra DB productiva ni filesystem remoto.
- **Filesystem/fsync:** el orden member fsync → staging dir fsync → rename
  generation → root fsync → pointer temp fsync → replace CURRENT → root fsync
  pasó los tests/fault points. El finding de rollback no afirma mixed cut.
- **Locks/multiprocess:** writer exclusivo y reader shared son nonblocking; los
  escenarios ejecutados niegan el segundo writer y la lectura durante writer.
  No agoté ataques con reemplazo hostil del inode de `writer.lock`.
- **Restart:** commit/restart conserva el último checkpoint committed. Recovery
  de rotación parcial no converge (RA-A-06). Rollback de CURRENT bifurca lineage
  (RA-A-07).
- **Idempotencia:** una rotación completa con ACK es idempotente en los tests;
  el ledger queda. Una rotación interrumpida durante deletion no lo es.

## Interfaces que debe reconciliar un candidato integrado futuro

Sin tocar los desarrollos UX/históricos paralelos, quedan contratos que deben
cerrarse antes de un único candidato/deploy futuro:

1. `ShadowRuntime.tick` ↔ retention: horizonte mínimo, cadencia y política de
   consumo durante soft pressure.
2. Archiver/control plane ↔ `archive-ack-*`: identidad autenticada, receipt
   durable verificable, recovery y destino privado.
3. ACK ledger ↔ cuota: anclaje/compaction sin perder auditabilidad.
4. Retention ↔ crash recovery: tombstone durable y cleanup reanudable.
5. Persistence reader/writer ↔ audit consumer: high-water anti-rollback,
   autenticidad y safety fields cross-role.
6. IOL collector/cache ↔ `source_observations`: taxonomy cerrada de errores y
   redacción antes de evidencia/archivo.
7. `audit_sources` ↔ `commit_generation`: contrato semántico canónico para cero
   y N reportes, incluidos counts/pointers/status/as_of/duplicates.

## Cierre de seguridad

No se realizó fix, commit, branch, PR, merge, deploy, SSH, mutación de runtime,
DB/PPI Watch productivos ni consulta a proveedor. Los probes terminaron con
`real_orders_sent=0` y rutas reales `NOT_CALLED`. Los findings afectan
disponibilidad, integridad/auditabilidad y potencial confidencialidad de SHADOW;
no son evidencia de una orden o pérdida real. Un candidato corregido requiere
nueva auditoría del SHA/tree y repetición de estas reproducciones, no sólo de la
suite GREEN existente.
