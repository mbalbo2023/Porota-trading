# RC6 — integración sucesora del 9 de octubre de 2026

Una integración de código desde `ecad18b6010e3b7e874a00edc72644b3dcc78790`;
ningún merge de heads antiguos. #475/#476 están reconciliados por ancestry;
#477 por contenido; #479/#480 aportan RCA y contratos, no un candidato GREEN.
Productiva verificada: `da697c6e6c2274579f9e4a112fabc4327475dd35`, tree
`96a112a55ed779df3f7056b1e30d81aac8d0b791`. Los resultados del candidato se
vinculan a su **HEAD/tree reales** en el único PR y los recibos nativos, sin
atribuir resultados de ecad, af08 o del desarrollo mutable a ese candidato.

`FIXED_WITH_GUARD` describe código y regresión económica permanente. **No**
certifica el workload material ni habilita un sucesor G0–G8. `BLOQUEADO_EXTERNAL`
identifica la autorización/perfil externo que falta; `NO_VERIFICADO` conserva
las obligaciones técnicas todavía sin evidencia completa. El registro histórico
[RC6_ERROR_REGISTRY.md](../RC6_ERROR_REGISTRY.md) conserva todos los RED originales.

| ERROR | RCA | FIX | GUARD | TEST/CI y evidencia | Estado y residual |
|---|---|---|---|---|---|
| Ownership; ERR-035 | Antecesor core `RELEASED=false`; expiración no prueba liberación. GitHub no enumera sesiones de Codex. | Sucesión administrativa explícita con confirmación directa del propietario y snapshots frescos de Issues/Actions; rama aislada. | `rc6_material_pr_admission.administrative_anchors`, ambos recibos originales inmutables, último writer y lease verificables; owner ajeno vencido bloquea. | `test_rc6_candidate_authorization`; transferencias #471/6086143803 y #473/6086144145. | `FIXED_WITH_GUARD` para sucesión y admisión; DEPLOY_OWNER no adquirido. |
| V2 capacity; ERR-015/021/027/033 | Un SHA con forma válida, un índice o un pico histórico parcial no prueban los bytes ni el grafo del candidato. | Autenticar commit/tree/blob Git, contenedor/CRC/todos los miembros originales, SourceDelta e inventarios; recomputar costos verificables. Reusar el inventario fresco de admisión. | `rc6_capacity_comparison`: costos canónicos completos obligatorios, reserva separada, sin crédito de cleanup; nodos dinámicos sin modelo probado bloquean. Scope diagnóstico nunca admite RC6. | `test_rc6_capacity_comparison`, negativos de provenance, alteración, grafo incompleto y diagnóstico en admisión. | `FIXED_WITH_GUARD` para verificación auténtica; grafo físico dinámico canónico `NO_VERIFICADO`. No se sustituye la excepción por PASS. |
| G0 arquitectura B; ERR-015/031/032/034/036 | El bootstrap retiene seed, dos fullSource y dos Product157. Backing26GiB, quota20GiB y dos reservas4GiB son cargas de certificación, no un mínimo demostrado del producto. La garantía nominal14GB no es un techo físico; metadata no prueba enforcement ni custodia ROOT. | Observación física por ejecución y propuesta de quota nativa directa conservando20GiB, inodos, ambas reservas y probe independiente; medir fuera de la vista proyectada por quota. | Holds `PRIVILEGED_SIGNAL_CUSTODY` intactos y native manager byte exacto; metadata, cuota directa o capacidad nominal nunca conceden launch. Reserva física y escritores externos exigen pruebas propias. | `test_rc6_capacity_comparison`, `test_rc6_certification_contract`; observador read-only y aritmética autenticada sólo en su ámbito. Probe histórico37845108215 `NOT_STARTED`, no cuota PASS. | `BLOQUEADO` por admisión técnica, no por falta de compra. Enforcement/reserva/custodia nativos `NO_VERIFICADO`; USD84 y ampliación rechazados. Véanse [RUNNER_DECISION.md](RUNNER_DECISION.md) y [ZERO_COST_CONTRACT.md](ZERO_COST_CONTRACT.md). |
| G5 wire; ERR-019/029/037 | Componentes por página sin cota, packs compartidos y churn de diccionario/cohortes impiden extrapolar siete cortes. | Factoring PAGE determinístico: header + <=16 grupos de índices + <=16 grupos de payload; tamaño de grupo potencia de2 >=64. BIN conserva gzip original. Reusar CAS/recipes/decoder existente. | Propuesta opcional sólo si **pack nuevo completo + recipe comprimida**, redondeados al bloque físico, y bytes lógicos no exceden baseline. Corrupción es fatal; overflow opcional conserva baseline. | `test_rc6_archive_grouped_wire`, compatibilidad de CAS, restore byte exacto/CRC/SHA256, límites, tamper y bases transitivas. | `FIXED_WITH_GUARD` para factoring acotado y selector; ahorro global original `NO_VERIFICADO`. El selector no certifica directorios/temporales. |
| G5 reutilización y lectura CAS | Dividir un primer objeto único aumenta índices y nunca gana el selector. Referencias intercaladas reabrían packs completos; errores podían dejar proof cache y catálogos deduplicados omitían filas físicas. El ahorro sintético69,91% no demuestra Horizon original. | Recipes V4 con slices y hashes de padres enteros; lectores públicos V3/V4, image Smoke independiente, restore, recuperación y GC con catálogos mixtos. Escrituras productivas conservan V3; V4 sólo en comparación privada. | Hash del pack completo y slice, dispatch tipado, bases transitivas, presupuesto previo al restore y rechazo de corrupción. Hold de escritura V4 no se levanta por un flag o un PASS unitario. | `test_rc6_archive_slice_reuse`, `test_rc6_cas_original_comparison`: cinco miembros byte exactos, misma sesión/anchors/fallbacks;152 regresiones económicas duales por scope. | `FIXED_WITH_GUARD` en lectores y comparación; habilitar escritura V4 requiere revisión contractual lector>=4, lectores del artifact G7 y consumo global original demostrado. No es G5 GREEN. |
| Compatibilidad productiva acotada | Un runner de4CPU sin límites no demuestra el Droplet1CPU/1GiB/25GB; RLIMIT_FSIZE no limita escritura agregada de descendientes. El stub de filtro anterior rechazaba siempre. | Filtro libseccomp real en supervisor/worker, instalación TSYNC, counter kernel/BPF, herencia fork/exec y23 probes de escape. El perfil instala el filtro después de exec; mantiene los límites1CPU/1GiB/swap0/pids64 y mount256MiB/65536inodos. | DUMPABLE/NNP/caps/UID observados, no inventados; plan de denegación original intacto. Sin G0, Product157, envelope admitido y Source readonly no hay nueve consumidores. Tmpfs forma parte del GiB de RAM; no certifica OS/PPI ni disco durable del Droplet. | `test_rc6_native_namespace_filter`, `test_rc6_product_resource_compatibility`:154 PASS económicos por Python en el scope. Probe NONROOT real sólo certifica filtro/herencia, no perfil completo. | `FIXED_WITH_GUARD` para filtro ejecutable; launcher acotado, nueve cargas PAPER y margen total del Droplet `NO_VERIFICADO`. Sin ROOT/mounts/Docker locales ni infraestructura persistente. |
| G5 global; ERR-019/029/037 |1202 cortes originales; final retiene1201 cortes operacionales. GC elegible no equivale a bytes recuperados; fallback/base/pins/temporales siguen consumiendo. | Modelo condicional de **todos** los prefijos con packs enteros, metadata, profundidad32, pins/bases transitivas,13 estados y6 obligaciones de costo adicionales. | `archive_physical_model`: rechaza siete cortes, ausencia/UNKNOWN, DAG inválido y costos omitidos; créditoGC=0, certificado/cota global auténtica siempre `null`. Hold `HORIZON_MODEL_CLOSURE` intacto. | `test_rc6_archive_physical_model`; evidencia af08/run37633310327: RED1049/1202; forecast500195328B no certifica. | `NO_VERIFICADO`: faltan envelopes auténticos1202 y costo físico simultáneo completo; G5 bloqueado. No se demuestra imposibilidad global con muestras ni se altera512MiB/9h+1h/universo. |
| BIG/captura/WAL; ERR-017/022/028 | Capturas, hashes y copias equivalentes repetidos; writers abiertos pueden checkpointar después del sello. | Reconciliar fixes existentes de una captura autenticada por tick, lectores internos compartidos y cierre explícito de conexiones/writers. Reusar hashes del post-FIN para import closure. | Source concurrente alterado continúa RED; presupuestos y fingerprint productivos originales. | Regresiones focal/Source/lifecycle y fixes existentes en ecad; BIG material del nuevo SHA no ejecutado. | Código reconciliado; calificación material `NO_VERIFICADO`:12000/60000,5 salidas PAPER,<=75s/90s,RSS<2GiB,RAW<=128MiB sin cambios. |
| READONLY_SOURCE10_CHANGED; ERR-022/023 | Diagnóstico tardío/INTERNALERROR ocultaba path/field; Source se reabría o sellaba mientras quedaban consumidores. | Diagnóstico preciso y veto sticky; failure como TestReport real; índices antes/después y validación se preservan antes del rechazo lógico. | Captura sólo tras FIN original; toda diferencia Source10 real rechaza; atime Source11 se registra sin restaurar timestamps. | `test_rc6_material_focal_evidence`, `test_rc6_readonly_complete_archive` controles Source reales. | `FIXED_WITH_GUARD` para reporte/veto/custodia; ausencia de mutaciones en fullGov del candidato `NO_VERIFICADO`. |
| collection/execution/JUnit; ERR-016/026 | Contadores/nombres de archivo y subreportes no equivalen a identidades reales ejecutadas. | Observer nativo conserva collection, started/finished y reports; comparación semántica exacta con JUnit original. | Cobertura exacta y summaries obligatorios en producer, carrier y gates; no editar XML/counters. | `test_rc6_material_focal_evidence`, `test_rc6_governed_evidence_validation`; JUnit económico original preservado. | `FIXED_WITH_GUARD`; fullGov original y157 dependencias `NO_VERIFICADO`. |
| Lifecycle/100000 entradas; ERR-018/023/024/025 | Repetir postfinalizer con cached_result=None perdía el witness; retirar antes de último consumidor/FIN o sin RAW destruye custodia. | Preservar primer witness opaco; declarar5 controles reales del factory original; copiar/hash-verificar antes de retiro de fixture propia. Integrar summaries estrictos en fullGov/carrier. | Error de contexto/declaración, RAW no capturado, consumidor vivo o FIN desconocido bloquean retiro y sucesores. Native manager original conserva ECHILD. | `test_rc6_pytest_fixture_lifecycle`, doble `FixtureDef.finish` real en pytest9.1.1, `test_rc6_governed_evidence_validation`. | `FIXED_WITH_GUARD` para lifecycle; censo material124358→<=100000 del nuevo candidato `NO_VERIFICADO`. |
| Ambiente/retirement NONROOT; ERR-038 | Consolidado local8c98fe8a heredó umask0077: fixtures de modos originales rechazaron160 casos; limpieza encontró directorio propio sin u+w y falló EACCES después de retiros parciales. ROOT previo había ocultado el permiso faltante. | Guard de entrada0022 leído del kernel antes de tooling; preparar sólo directorios propios autenticados para retiro por FD después de FIN+capture+inventory, con transición de modo registrada y nueva validación global antes de primer unlink. | Mask incorrecta rechaza antes de productores; ausencia de FIN/capture, identidad/mount/owner ajenos o carrera bloquean chmod/unlink. Fallo de cleanup preserva JUnit/FIN/RAW, reportaRED, bloquea siguiente epoch y no acredita espacio. | `test_rc6_actions_custody` kernel real mask y error de retiro con child/FIN/capture genuinos; regresiones NONROOT de lifecycle. RAW original8c98fe8a conservado fuera deSource, sin reclasificar; namespace anterior retenido. | `FIXED_WITH_GUARD` para entrada y retiro propio; no es prueba de capacidad/materialG6. |
| RAW perdido en RED; ERR-016/026/033 | Validación lógica se adelantaba al sellado; cleanup podía destruir stdout/stderr/JSON/JUnit originales. | Seal post-FIN antes de validar Source/coverage/import/network; conservar copias selladas ante RED, FIP derivado afuera del productor. | Un FIN inventado/desconocido jamás autoriza lectura, upload o cleanup. Rescue sólo para grupos previamente sellados con ECHILD genuino. | `test_rc6_focal_raw_custody`, `test_rc6_governed_evidence_validation`: negativos Source/network/observer preservan RAW y statusRED. | `FIXED_WITH_GUARD` para orden de custodia; resultados materiales pendientes. |
| CI/Predeploy; ERR-020/030 | Acoplamiento fijo alPR476; heavy simultáneo/automático y reconstrucción de evidencia enG7. | Candidato dinámico autorizado por PR/branch/SHA/tree/base/owner; admission antes de tooling; G7 consume G6 auténtico, build once conservado. Separar14 wheels económicos manuales de157/productores materiales. | DAG obligatorioG0→G1→G2→G3→G4→G5→G6→G7→G8; missing/RED/stale bloquea. Sucesión/custodia/desarrollo no conceden heavy/deploy. | `test_rc6_candidate_authorization`, contratos Predeploy/artifact/gate order, `test_rc6_actions_custody` CLI real antes de tooling. | `FIXED_WITH_GUARD` para scope/pipeline; continuidad native de lease en gates largos y un Predeploy íntegro `NO_VERIFICADO`. |
| Packaging/RAW | COPY/context incluía RAW histórico; una exclusión amplia eliminaría6 helpers runtime o fullSource. | Excluir sólo30 paths RAW después de verificar custodia original en Git ecad, ancestry, blobSHA1/SHA256 y bytes; fullSource permanece completo. | Helper requerido/import dinámico o relativo hacia RAW excluido invalida import closure; falta de bytes recuperables bloquea packaging. | `test_rc6_raw_packaging_custody`, artifact/import/security contracts;30 archivos/6541688B custodiados en original. | `FIXED_WITH_GUARD` para selección/custodia; imagen real build once/manifest/security/G7 `NO_VERIFICADO`. |
| Vencimiento Actions | Primer vencimiento2026-10-14T19:37:34Z. Un índice de2034 miembros no permite recuperar los16 ZIP originales. | Control manual exacto del candidato copia ZIPs originales a assets de draft release, verifica contenedores/CRC/SHA de todos los miembros y descarga assets+recibo para comprobar recuperación. | No overwrite/delete, no release publicada, ambos leases/planhash autentican cada escritura; copias parciales no reciben PASS. | `test_rc6_actions_custody`; run37983279857,16 ZIP/2034 miembros/18816252B, draft408273199, recibo durable asset626018831/SHA2564252f9182ad86ca9a7e95ef9a9b70cdb7cf5d094b47a9b90474503022eb5195c. | Custodia operativa `VERIFICADA` por el controlador deb85bc6: ZIPs y recibo descargados/readback SHA256/CRC. No es certificación material del nuevo SHA. |
| Transporte REST/custodia; ERR-039 | GITHUB_TOKEN Contents:write recibió403 creando draft release en el target histórico. Tras preparar destino por el propietario, el endpoint ZIP Actions rechazó415 un Accept octet-stream incorrecto. | Destino draft408273199 preparado por API, sin publicación; negociación REST por endpoint separada de decodificación binaria: JSON para Actions ZIP/upload, octet-stream sólo para GET de release asset. | Regresión de headers exactos en los tres endpoints; errores conservan reciboRED sin claim de custodia. Los checks económicos requieren autoridad independiente y tienen su propio job manual. | `test_rc6_actions_custody`; runs37980865842/37981578210 RED preservados. Custodia completa verificable en37983279857. | `FIXED_WITH_GUARD` para transporte/RED; no se atribuye custodia al resultado económico ni se reclasifican los RED previos. |
| Diagnóstico nativo; ERR-040 | El resultado/JUnit/FIN/RAW sellado sólo produjo exit1 en el log; la descarga local retornó403. | Resumen bounded con hash del resultado original y readout autenticado del ZIP/JUnit original, sin reejecutar productores. | SHA/GitHub origen/CRC/capture hashes obligatorios; readout no reconstruye FIN ni concede calificación. | `test_rc6_development_readout`; run37986415546 lee el original11642516481/SHA25618fcb0070c5e6267db08f5d7cd4d2158b0fe7b39e0f04fefd1a594b12f15a6da. | `FIXED_WITH_GUARD`; causa original recuperada y trazada a ERR-041. |
| Censo kernel no vacío; ERR-041 | Hosted Linux expone bytes no vacíos en task/children; isdecimal sólo existe en str. El entorno local carece de ese proc member, y nunca ejecutó el camino. | Parser ASCII positivo para bytes/IDs antes de int/read; preservar censo completo y bloqueos ante ausencia/carrera. Job manual económico separado de custodia ya recuperada, con Contents:read. | Malformed/Unicode/zero/negative/unknown/race rechazan; no fabricar censo vacío ni FIN ni cambiar native manager. |16 nuevos controles en `test_rc6_scoped_infrastructure_lease`, más regresión original real zombie/tracker en `test_rc6_pytest_fixture_lifecycle`; JUnit dual exacto del nuevo SHA exigido. | `FIXED_WITH_GUARD` para parser; prueba nativa del candidato se registra en el PR, sin atribuirle PASS de otro SHA. |
| Host/release | Medición previa bloqueóCPU/RAM. API read-only fresca confirma1vCPU/1GiBRAM/25GiB, no recursos disponibles del proceso/cgroups. | Revalidar hardware actual sin tocar runtime; exigir medición read-only futura contra el candidato real antes de promoción. | CI no demuestra compatibilidad Droplet. Sin G0–G8, artifact exacto, capacidad, ownership de deploy y autorización efectiva no hay promoción. | DigitalOcean594077619 read-only2026-10-09T18:37Z; anterior37656549281 pertenece aaf08. | `NO_VERIFICADO` para capacidad del nuevo candidato; release `BLOQUEADO_EXTERNAL`. PPI Watch intacto. |

El control37983279857 verificó custodia completa de los16 ZIP/2034 miembros y
recibo durable SHA2564252f9182ad86ca9a7e95ef9a9b70cdb7cf5d094b47a9b90474503022eb5195c
en asset626018831 de draft release408273199. Sus checks económicos quedaron
RED, sin atribuirles los PASS locales de otro SHA. ERR-040 corrige la falta de
resumen verificable en el log y permite leer el artefacto original11642516481,
SHA25618fcb0070c5e6267db08f5d7cd4d2158b0fe7b39e0f04fefd1a594b12f15a6da,
con control nativo manual de sólo lectura y sin reejecutar productores.
El readout37986415546 recuperó la causa concreta: AttributeError de isdecimal
sobre bytes en el censo kernel no vacío (ERR-041). Los835 casos/1failure por
Python del RED anterior permanecen originales; no se reclasifican como PASS.

## Resultado operativo y gates

G0 `BLOQUEADO`; G1 completo `NO_VERIFICADO`; G2/G3 focales, G4 BIG,
G5 Horizon, G6 full governed, G7 Predeploy/build once y G8 artifact/host/release
**no autorizados ni ejecutados** como calificación del nuevo candidato.
Regresiones económicas positivas y custodia de RAW son controles separados.
No existe artifact RC6 certificado, merge, deploy ni validación runtime nueva.

El plan económico enumera los módulos/controles baratos explícitamente y no
reemplaza discovery automático ni el fullGov original. Usa CPython3.11.16/
3.12.14, pytest9.1.1 y14 bloques con hashes del lock canónico; fuente inalterada,
JUnit/FIN/RAW originales, identidad dual exacta y cleanup de namespaces propias.
Las incompatibilidades ambientales se registran como RED y se corrigen por causa,
sin reclasificar ejecuciones previas ni repetir un mismo material fallido.

PAPER/SHADOW ONLY; PRODUCTION_PAPER / SIMULATION; real_orders_sent=0;
rutas reales BLOCKED/NOT_CALLED; PPI Watch NO TOCAR; FIX-FORWARD ONLY.

## Continuación de código desde PR #482

Base exacta `2bf97dde54b4f960e105d2397d9b80012ba97aab`, tree
`49808f6a229f0f53bb1c765a9d6be1e22ac0095f`; se preservan #481 y #482.
Un único sucesor aislado en `fix/rc6-native-certification-20261010`.
Transferencia auténtica #471/6092206348 y #473/6092202857 después de las
liberaciones explícitas #471/6091939624 y #473/6091938137. El nuevo owner no
suplanta la sesión histórica. Los leases se renuevan en ambos threads antes
de vencer; una expiración ajena sigue bloqueando y no implica RELEASED.

| Bloqueo auditado | Cambio ejecutable y prueba | Residual antes de material |
|---|---|---|
| CI económica exacta #482 | Run[38013494334](https://github.com/mbalbo2023/Porota-trading/actions/runs/38013494334), attempt1: admisiónRED `EXACT_ADMINISTRATIVE_SUCCESSOR_SESSION_REQUIRED`; tests/tooling/fixtures no iniciados. ZIP original12246B,7 miembros conCRC, SHA256`aaebda5bfbbeb0b5180f5b619a21fddc7e8bd2e84406d06c890ca77c5717a23e`. Sucesión nueva admite sólo una liberación auténtica y autoridad dual exacta. | CI positiva del nuevo SHA/tree; no retry idéntico del RED. |
| Native prerequisites inseguros o ficticios | Job económico manual separado; artefacto Actions positivo del mismo SHA/tree, ambas épocas/JUnit originales, inventario Git completo, FIN y cleanup autenticados. Revalida Issues, ref/tree y run/job vivo antes y después de ROOT. Output fresh fuera deSource; filtro irreversible sólo en child NONROOT. | PASS pequeño no concede G0, cuota, perfil productivo ni backing. |
| Custodia ROOT desde primera instrucción | Adaptador efímero sobre PID1/systemd existente: unidad transitoria, cgroup propio, RuntimeMax/TERM/KILL; manager original SHA256`55325b3108e175a42b87ebe544fd307fa45ffd29b6f7ab471443803ad9ba53b8` intacto. Broker en PID privado; sólo FD opaco del directorio propio, sin exponer host/proc/root/cwd/fd ajenos. Identidad pública PID/start/boot/UID/GID/caps/cgroup/NSpid y birth/pidfd; inode de namespace leído sólo por self o ROOT hijo privado. Transporte NONROOT/DUMPABLE0/FIN/reap real y184 regresiones baratas PASS por Python. | Las cuatro situaciones ROOT/NONROOT y guardian timeout requieren Actions autenticado; metadata JSON no sustituye el witness privado. Sin provisionar ni servicio persistente. |
| Contrato ROOT original de G0 | Calibración recibe witness nativo privado y conserva los límites históricos. Hold `CALIBRATION_PRIVATE_PID_ORIGINAL_PROC_ROOT_CONTRACT_REVIEW_REQUIRED` antes de asignar backing: PID privado oculta el issuer y el original exigeEACCES en `/proc/HOSTPID/root`. | Revisar explícitamente la sustitución contractual: ausencia del PID no es EACCES. Debe demostrarse la nueva frontera guardian+FD, escapes denegados y FIN; sin esa revisión no hay cuota ni equivalenteGREEN. |
| Horizon auténtico y consumo global | RAW original autenticado: ZIP5894344B/884 miembros/SHA256`5738a959b1483e46125b2a44a3b536067d6f5bf87b01cde68888c218023f0bb2`,1049 cortes,153 faltantes y0 juegos completos de cinco payloads. Hook del productor entrega los mismos bytes originales a catálogos V3 y V4; conserva1202 ciclos,512MiB, profundidad32,9h+1h, sesiones/anchors/fallbacks y mide recovery/GC/temporales. | Ahorro global y cota permanecen `null`. Admitir físicamente los dos catálogos adicionales y cerrar `HORIZON_MODEL_CLOSURE` antes de namespaces/fixtures; el RAW no permite reconstruir datos ausentes. |

La revalidación READ_ONLY auténtica de Issues cerró un defecto adicional antes
de CI: GitHub incluye `performed_via_github_app.client_id` en collection y lo omite
en GET individual. Comparar el objeto REST completo rechazaba un mismo recibo.
Se conserva comparación estricta de ID/body/timestamps/URLs/autorID+login/appID+slug;
224 regresiones de ownership por Python rechazan alteraciones de esos campos.
No se convierte metadata decorativa en autoridad. El plan completo pasó1333 casos
por Python antes de estos dos fixes; sus checkpoints se conservan y la CI del
SHA final debe ejecutar el plan completo actualizado, sin tomar prestado ese PASS.

Camino finito y secuencial: (1) congelar un único SHA/tree y ejecutar66 selecciones
económicas con los14 pins originales en3.11.16/3.12.14; (2) sólo tras su artefacto
positivo admitir el job pequeño de filtro/custodia y conservar todos sus RAW;
(3) resolver la revisión exacta de `/proc` y probar cuota nativa/reservas/preflight
fresco en Actions estándar, sin asignar backing ante un prerrequisitoRED;
(4) cerrar el grafo físico completo de todos los writers y ejecutarG0–G6 en orden,
incluida la comparación Horizon original y el perfil PAPER acotado;
(5) sólo entoncesG7 build once yG8 artifact exacto/margen read-only del host.
Un RED tiene causa y fix nuevo antes del siguiente SHA; no se repite la misma
ejecución ni se transforma un control económico en gate material. USD0 recurrente
adicional, sin ampliar el Droplet, contratar runners, merge o deploy.

### Evidencia auténtica del candidato PR483 y fix de diagnóstico

El Source `174d7b5d7057e20e5364f9b612b380126bf2cc8d`, tree
`8de2f40cd8942f5236a093c434f47e82bcd3c3f8`, pasó CI económica38018234467
en Actions estándar ubuntu-24.04:1386 PASS por Python,0 failures/errors/skips,
identidades iguales, Source literal completo, FIN/capture/cleanup propios.
Artefacto11657136542:922398B,16 miembros CRC/SHA verificados,
SHA256`bce2b30ffc1ff933f027c022e42a8f3695b8d0af541ad6010e3f3fe9e3abcf02`.
Los managers originales midieron88.117/94.530s y295706624/297369600B de
peak RSS por época; esto no es suma global de RAM ni prueba del Droplet.
La observación fresca tuvo91697651712B libres en ext4/4096 y18423012 inodos
libres. La cuota nativa continúa no probada:CONFIG_QFMT_V2=m, quota_v2 no vivo,
sin módulos coincidentes observados; capacidad nominal14GB no es techo físico.

El control nativo38018906264 autenticó esa CI antes de actuar. Su filtro real
pasó instalación, TSYNC, herencia y26 denegaciones por supervisor/worker:
PASS_NATIVE_FILTER_ONLY,57 reglas, filtro0→1 y1→2, FIN/captura/cleanup propio.
Artefacto11657187465:23333B,9 miembros CRC/SHA verificados,
SHA256`cc37f5ea18436ce5c8f5bcfd50693e0161b8ab3208aebd6227de0d8a904fa46d`.
La custodia ROOT quedó RED enROOT_CUSTODY_ACTUAL_CONTROLLER_PIDFD_REQUIRED;
faltó birth/pidfd y no se ejecutaron sus cuatro casos, cuota/backing/material.
El original queda conservado sin reclasificación ni repetición del mismo SHA.

| Bloqueo | Resultado demostrado | Fix y guard pendiente |
|---|---|---|
| Sucesión/CI económica exacta174d7 | Cerrado para ese Source con recibos auténticos, JUnit/FIN/Source completos y Actions estándar. | Un Source sucesor requiere otra CI exacta; no hereda ese PASS. |
| Filtro supervisor/worker | Cerrado sólo como prerrequisito NONROOT nativo en Actions. | No equivale a perfil PAPER completo ni seguridad ROOT/cuota. |
| Pérdida de diagnóstico al faltar ROOT birth | Confirmada por el ZIP original: no conservó stderr del arranque. | CustodyFailure preserva error primario y FIN cliente original; sólo lee stderr tras unidad nonce exacta inactiva y cgroup2 vacío/ausente. FIN exit4 sólo sirve al diagnóstico RED de not-found estricto. UNKNOWN retiene namespace; no cleanup/crédito/ROOT FIN. Próxima ejecución sobre un SHA corregido debe entregar causa original. |
| G0/cuota/reservas;1202 ciclos;perfil productivo | Permanecen bloqueados; no hay dato global ni porcentaje extrapolado. | Mantener revisión `/proc`, grafo físico, Horizon original y Product157 antes de gates materiales; G7/G8 no preparados. |

El fix mantiene manager55325b3108e175a42b87ebe544fd307fa45ffd29b6f7ab471443803ad9ba53b8,
capacidades, DUMPABLE, límites y todos los holds. No se elimina privacidad para
obtener un PASS. El próximo Source se valida económicamente antes del siguiente
control nativo. Coste recurrente adicional USD0; real_orders_sent=0.
