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

## Segundo Source autenticado y fix de CAS/ACK y journal

`98e9f70899d1d6bc1f3f84adbe4b5b5689fbdf8e`, tree
`25f411385dbe7a488f673a0cfe9f85b8a17251fd`, pasó CI38020059029:
1399 PASS por Python3.11/3.12, cero failures/errors/skips, mismas identidades,
Source literal completo y FIN/capturas/cleanup propios. Original11657861635:
920980B/16CRC, SHA256
`6195c1fdd2aa7ec4020fb3ac98566f42fdd7cab56991aa3bb91710f6858989fe`.
Managers87.309/94.223s, RSS máximo295997440/296501248B; ext4 libre91697659904B,
18423027 inodos. Son observaciones de esa ejecución, no garantías ni prueba
del Droplet ni enforcement de cuota. El siguiente Source requiere su propia CI.

Control38020561080 conservó RED con FIN cliente226/EXIT_NAMESPACE antes de
birth ROOT. Original11657921965:36492B/18CRC, SHA256
`59f34068f4ce99cdbd3b40a324797d75527047523d50387344d45c8df807f807`.
Unidad nonce exacta inactiva/not-found, PID0/cgroup ausente; guardian log vacío.
**Errno/ruta causal UNKNOWN**. No se atribuye a permisos concretos ni a falta
de infraestructura. FIN ROOT UNKNOWN, namespace retenido y crédito de reserva0.
El fix captura journal NONROOT del boot y unidad propios con máximo30 entradas,
RLIMIT_FSIZE duro2MiB, timeout5s, FIN y RAW originales; una consulta vacía/RED
continúa UNKNOWN. Conserva mensajes de arranque del guardián sin cambiar
privacy, capabilities, DUMPABLE ni manager. No reintenta Source98.

| Bloqueo nuevo observado | Fix ejecutable y regresión | Estado y obligación pendiente |
|---|---|---|
| Observe CAS llegaba después del fallo/ACK V3: perdía cortes y el live128MiB/512files no podía rotar. | Mismos cinco bytes de la única fixture pasan al candidato privado antes del ACK. Candidato usa el archivo canónico privado; baseline V3 independiente puede quedar sticky RED sin abortar candidato. ACK vuelve a verificar CURRENT/manifest/bytes/receta existente; corrupción o ausencia detiene rotación, sin fallback.163 PASS económicos por Python. | Código corregido; **revisión contractual explícita del ACK privado** y casos ejecutados autenticados G1.311/312 requeridos. COMPARE_COMPLETE mantiene G5=false; writer productivoV3. |
| Manifiesto privado no llegaba del recibo al productor. | Template dual hash-bound exacto; ruta derivada sólo dentro del namespace propio, fichero original exclusivo/fsync/hash, revalidación antes del wrapper/productor.25 PASS por Python; peak normal Horizon rechaza comparación. | Código corregido; grafo agregado propio obligatorio. Dos catálogos totales, una sola fixture/Source; sin equivalencia ni pico físico declarado. |
| Causa de EXIT_NAMESPACE no emitida por modo quiet. | Journal del boot/unidad propios y arranque no quiet sólo para guard-hang; RAW original bounded post-FIN y prueba nativa de ausencia de escritores. | Diagnóstico implementado; próximo Source exige CI económica exacta positiva antes de una única comprobación nativa. No autoriza backing, cuota, pruebas financieras ni ROOT local. |

Todos los1202 ciclos,512MiB, profundidad32, anchors/fallbacks, cambios reales
de sesión, retención9h+1h y live128MiB/512entries se conservan. El ZIP histórico
tiene1049 cortes y ningún conjunto completo de los cinco payloads; no permite
reconstruir los153 faltantes ni medir retrospectivamente V4. Consumo global con
recuperación/GC/temporales y ahorro original siguen NO_VERIFICADO. El69,91%
sintético no se extrapola. G0–G6 materiales, nueve cargas PAPER acotadas y
G7/G8 permanecen bloqueados por sus prerrequisitos originales.

## Source714: prueba positiva económica y filtro, diagnóstico ROOT incompleto

`714a27cf222d1cca8e9fcf75ba26cb5c80214147`, tree
`99afc9f818a666ac34e5f97d3550bbd68ff9dcdd`, pasó38022392677/job114125990140:
1434 PASS por Python3.11/3.12 sin failures/errors/skips, Source completo
literal idéntico, FIN/capturas/cleanup propios/foreign0. Original11658643847:
926016B/16CRC/SHA256
`61de89722c168bcd1fe8686c64ebaa5807ae199a562e2e405f15b2e6bc29f03f`.
Wall88.409/95.328s; RSS máximo295677952/298582016B, no suma; inventario
propio **post-FIN antes de cleanup**, no pico global139370496/139358208B.
Ext4/4096 libre91697479680B,18423010 inodos; no cuota/reserva demostrada.

Native38022857618/job114127387746 conserva RED226 antes de birthROOT.
Original11658139752:42914B/22CRC/SHA256
`00e4ecfdb73b874ae310539a6179ac993b8bef99c7ba5d1d76443252eb363af3`.
Filtro PASS real57 reglas/26 denegaciones por actor, supervisor0→1 y worker
1→2 con herencia fork/exec;69,632B propios post-FIN/cleanup. Journal NONROOT
3454B/SHA256`e5efd653354fc50ee903c3f0384179c7fb8b8a7ca2c5eff823cec062afba9e40`,
FIN0/ECHILD original:cuatro registros PID1 del boot/unidad propios. Mensajes
de arranque guard preservados; errno/ruta causal siguen UNKNOWN. ROOT FIN
UNKNOWN, namespace retenido, sin cleanup ni crédito de reserva. No reintento714.

La revisión upstream systemd v255 identifica otra omisión del diagnóstico:
exec-invoke.c configura mount namespace y emite error antes de instalar el
filtro de address families; el logger de executor usa UNIT=own sin ser PID1
y puede preceder atribución de _SYSTEMD_UNIT. El cambio mínimo consulta
boot+UID0 confiable+UNIT nonce exacta y conserva el segundo filtro de unidad
propia. Un registro pre-cgroup es **diagnóstico**, nunca identidad ROOT ni FIN
ni crédito de reserva. Mantiene30 entradas/2MiB/5s/NONROOT/originales/hash,
properties/caps/DUMPABLE/manager. El próximo Source exige su propia CI antes
de un único control nativo acotado que recupere la causa. No se retira
InaccessiblePaths ni se habilita AF_UNIX por sospecha, ni se modifica G0.


## Continuación desde PR483: causa y frontera de custodia

Se conserva el Source original `5daf01728865c3f65c3f31a5abb843ee8ce874d3` y
la misma rama/PR483; #481/#482 permanecen en su ascendencia. La reacquisición
es de la propia sesión después de releases auténticos, por la nueva orden del
propietario. Un release histórico anterior a una reacquisición no reemplaza la
cobertura de lease del intervalo nuevo: el guard tiene251 regresiones por Python
y rechaza autor/scope ajenos, gaps y revocación dentro del intervalo.

El original38024250723/artifact11659366663 conserva22 miembros CRC/SHA,42853B,
SHA256 `61a00c5c3e2e63c414d00381365e811d35c7964302c7ce41eed7e15a8b528bee`.
Su FIN cliente226 es anterior a birth/pidfd ROOT. Los cuatro mensajes journal
del boot/unidad propios provienen de PID1 y no contienen operación/errno causal.
No se atribuye ENOENT, EACCES ni otro errno a esa ejecución. FIN ROOT permanece
UNKNOWN, namespace retenido, sin cleanup ni crédito físico.

| Propiedad / error | Corrección concreta | Evidencia y límite |
|---|---|---|
| Diagnóstico226 antes de exec | Sólo el guard propio añade `LogLevelMax=debug` con readback; cuatro consultas NONROOT de configuración viva, versión, package y digests instalados, cada una con FIN original/5s/64KiB. |282 PASS económicos por Python; broker/quota sin cambios. Journal30/2MiB intacto. Próxima prueba nativa requiere CI positiva del mismo Source. Binario instalado no acredita FD vivo PID1. |
| Magic links de `InaccessiblePaths` | Auditoría de executor/namespace/chase v255.4: systemd sigue links y puede reordenar mounts. | Hipótesis de máscara del root de servicio y fallo posterior; sólo RAW causal del runner puede convertirla en RCA. No se retira ninguna restricción por sospecha. |
| ROOT puede deshacer mounts protegidos | Auditoría confirma que el actor conservaba CAP_SYS_ADMIN durante probes. Se separa setup confiable de un sello ROOT posterior; originalmanager conserva subreaper/fork/exec/setsid/wait4/killpg. | Sello en desarrollo separado, sin aplicar el filtro NONROOT antes del drop. Frontera única: PID1/cgroup/plazo y supervisor original para drenaje; actores privados sellados. Necesita prueba nativa, no equivalencia por metadata. |
| FullGit shallow impide smoke real | Fetch canónico recuperó ancestry ecad→HEAD; no eliminación manual de shallow. CI debe preparar ese historial después de admisión y antes de tooling/pin, con límites y FIN propios. | Los RED previos se conservan. No se rebaja el guard RAW/provenance ni se obtiene el historial durante las pruebas. |
| Lectores mixtos y recovery | CLI V3→V4→V3, corrupción/falta de parent pack, ocho controles GC reales con siete puntos SIGKILL, inventario de todo Source ejecutable y callers transitivos; reviewv2 exige ejecuciones duales y FIN. | Fixtures económicas; writer productivoV3 y G7 exactimage siguen obligatorios. Ningún porcentaje sintético se extrapola a Horizon. |
| Modelo globalG5 | Guards de fases PREOPEN/OPEN/CLOSED y transición770/771/772; cotas incluyen graph, pins, GC/recovery/temporales. | RAW tiene1049/1202 cortes y0 conjuntos completos de payloads. Cota física/ahorro global siguen null; `HORIZON_MODEL_CLOSURE` intacto, sin ejecutar Horizon antes de admisión. |

Source1 sólo habilita, tras su CI económica auténtica, un diagnóstico nativo
pequeño con las restricciones originales. La corrección de la causa confirmada
exigirá un nuevo Source, regresión positiva/negativa, CI exacta y witness nativo
antes de declarar custodia, cuota o reservas. Cambiar el outcome contractual
EACCES por PID ausente requiere revisión explícita; no se aprueba aquí.
G0–G6, perfil completo1CPU/1GiB y margen total del Droplet permanecen bloqueados;
G7/G8 esperan esos gates. USD0 recurrente adicional, PAPER/SHADOW, órdenes0,
PPI intacto, FIX-FORWARD; sin compra, ampliación, merge ni deploy.

Source `18ba722163f6539e68c7a2c1c27b53fb65e9adae` conserva RED económico
38046686266/artifact11667771533:966426B/22CRC/SHA256
`ab45bd1da324c193cfae156022a70dcc5887cd03b0ab1d3ae86506fa8ba4b868`.
Ambos Python:1577 PASS y7 setupErrors del mismo fixtureGit, sin failure/skip;
Source literal idéntico y FIN/ECHILD/foreignremoved0. El fixture intentó
copiar439734272B dentro de256MiB: se corrige la duplicación con clausura
Git verificada de Source/RAW/legacy/ascendencia, conservando el límite.
No se ejecutó ROOT ni se repite Source18ba. El sucesor exige su propia CI
positiva antes del diagnóstico nativo; el RED no certifica lectores completos.

El sucesor `13fa4520c25b1f6b461b1cfd6507ef418f210f54`, tree
`ed6494e78121d7ae8e3d20a2aa1bcb9d41ef659f`, pasó Actions38048514189,
job114202783308: **1587 PASS por Python**, cero fallos/errores/skips,
identidades iguales, Source literal íntegro, FIN/ECHILD y cleanup0foreign.
ZIP original11668752067:957224B/22CRC/SHA256
`c769abd464bd4112e84ed06f2584857aa5c96ea36f68324cb8778853e9c49161`.
Wall113.636/126.569s; RSS máximo295981056/299286528B, no suma global.
Inventario propio post-FIN809287680/809193472B; **no pico físico**.
Ext4/4096 libre91482906624B/18422998inodos, capacidad observada de esa
ejecución, sin garantía universal ni cuota. El bootstrap histórico fue positivo
5.310s/FIN/ECHILD, Git225435648→439803904B observado; cada fixture conserva
su límite256MiB con clausura selectiva. No se extrapola al Droplet.

La nativa38049039051/job114204280653 autenticó ese ZIP y conservó el RED226
anterior a birth/pidfd. ZIP11668354337:57298B/33CRC/SHA256
`368457c3eb7afa21804cc9bdee5785cab9124f3aa6873a6a861a8782f566d599`.
Las cuatro consultas systemd NONROOT tienen FIN originales y hashes completos:
package255.4-1ubuntu8.17, LogTargetjournal-or-kmsg/LogLevelinfo. Sólo cuatro
mensajes PID1 del boot/unidad propios; operación/errno siguen **UNKNOWN**.
Namespace retenido, ROOT_FIN UNKNOWN, ningún cleanup ni crédito de reserva.
Filtro real57reglas/26denegaciones por actor, herencia y FIN confirmados;
supervisor dumpable0 y worker post-exec/post-install dumpable1 reales.
Esto prueba instalación/herencia; no DUMP0 de todo backend, seguridad ROOT,
perfil1CPU/1GiB ni cuota. El plan histórico permanece byte exacto.

| Causa demostrada | Fix mínimo y guard | Límite de la evidencia |
|---|---|---|
| El contexto genérico del logger255.4 reserva2iovec por campo KEY_VALUE, pero cada uno usa3. INVOCATION_ID se prepende; UNIT puede quedar omitido. Los84patches oficiales Ubuntu8.17 no alteran esa cadena. | Capturar INVOCATION_ID/MESSAGE_ID/EXIT_CODE/EXIT_STATUS del mensaje de salida PID1 con boot/UID0/UNIT nonce exactos; segunda consulta sólo del mismo boot+UID0+ID auténtico. Rechazar anchor ausente/ambiguo/ajeno y conservar ambos FIN/RAW/hashes. | Corrige el canal diagnóstico, no demuestra aún el errno de namespace. Los129 números de secuencia intermedios no permiten conocer sus mensajes. |
| El query y output-fields originales no recuperaban ese contexto. | Máximo2consultas por fallo, cada una30entradas/2MiB/5s; no enumeración de unidades ni perfiles ajenos. Un dato INVOCATION_ID nunca concede custodia ROOT/FIN/cleanup/reservas. | Próximo Source exige CI exacta positiva; no repetir13fa. Properties/caps/bridge/manager y holds intactos. |
| El hold preventivo Horizon introducido enecad no consume un certificado y fija cuatro condiciones NO_VERIFICADO; capacity_comparison también rechaza costos dinámicos sin lector de cota. | Cambio mínimo revisable: demostrar una cota completa de seguridad para obtener evidencia, conservando el hold de calificaciónG5; después deG0→G4 y reviews, una comparación privada original1202, siempreG5false. | Requiere revisión explícita de la regla de admisión, cota auténtica de todos los solapamientos/reservas y Source correctivo. No aprobado ni ejecutado; G5 original512MiB/1202/profundidad32/retención permanece obligatorio. |


Source `2afd79adc7b61815d8c66a07f391c71a12e84dbf`, tree
`f4165b011be21f225423d8e093a3eaeb9ad69191`, pasó Actions38050786152
job114209316381: **1642 PASS por Python**, cero errores/fallos/skips,
identidades iguales, Source literal, FIN/ECHILD, cleanup0foreign. ZIP original
11669367878:960417B/22CRC/SHA256
`8edc277972a004f4754b083eeb9ebe72c269da5cb30af38d5372d925101e5edb`.
Wall120.066/130.488s; RSS máximo298848256/299134976B; inventario
post-FIN810147840/810201088B (no pico). FS observado ext4/4096, libre
91478253568B/18422979inodos; no prueba de cuota ni del Droplet.

Native38051376116/job114211025375 conservó RED226 antes de birth/pidfd.
ZIP11669513385:68188B/37CRC/SHA256
`6b66c01a1b20e0bf2a0b200e0d6bc16772f09ed5fbde099eda73147c18d54f94`.
Anchor PID1 propio enlaza boot4220d65f8c7345d38289a76ec860c966,
Invocation61660a1e6c7340149342237bfae18203 y executorPID2505. El segundo
RAW23086B/SHA256
`2a4d94269ef68527b6e421eaac738710389deb0fe60c3a8769ac8195071b7809`
y FIN original
`35f11a9f99d72595da47d53812d77b1eb4ad544702a170623e18b3d642f00628`
se verificaron contra captura cerrada/Source/CRC/ECHILD. La metadata journal
init.scope previa no se convierte en cgroup live; el parser estricto Source2afd
la declaró UNKNOWN, conservando los originales.

**Operación peligrosa demostrada:** `apply_one_mount`, namespace.c:1704,
registró `Successfully mounted /run/systemd/inaccessible/dir to
/run/systemd/mount-rootfs`. La máscara magiclink `/proc/1/root` alcanzó la
raíz entera. Los25 EBUSY16 de desmontajes dicen `ignoring`: no son evidencia
del errno final226. El error posterior sigue no observado y no se inventa.
Readout completo autenticadoSHA256
`da8be2bfa42e0ebf79ca55734d05f85038559ba355325813da1472ab750370cd`.

Fix causal implementado: guard que rechaza destinos magiclink/raíz antes
de ROOT; máscara del directorio real `/proc/1`; parentPID1 leído y cotejado
por observador externo live más pidfd/ACK antes de actores. Sólo mode=probe
puede usar el sello posterior al proc único/RO, dispositivos privados/RO y
cierreFD3. `PrivateDevices` en systemd255 hace bind del devpts host
(namespace.c1018–1020); el bootstrap privado debe reemplazarlo por una
instancia `newinstance`, RO/nosuid/noexec, y comprobar un device distinto
antes del sello. La opción de systemd sola no prueba aislamiento de PTYs.
Las71 regresiones locales del sello pasan en cadaPython, sin fallos/skips,
como NONROOT: no certifican ROOT ni G0. No puede heredarlo quota/pip:
bloquearía sockets y FSGETXATTR.
Original manager, drop NONROOT, cuatro casos y deadlines se preservan.
Custodia329, guards59 y calibración130 PASS en cadaPython local, todos
NONROOT y sin errores/fallos/skips; no reemplazan CI ni la prueba ROOT.
Este Source necesita CI exacta nueva y evidencia nativa; no hereda el PASS
de2afd. No aprueba la revisión del witness ROOT, EACCES histórico vs
ausencia del PID privado, cuota agregada ni regla temporal de admisiónG5.
No Horizon/cuota/reservas/G0–G8/merge/deploy durante los bloqueos.


## Source9c743: arranque ROOT auténtico y guard de unidad tipada

Source `9c74348a0e5b8e3f3bc4761837d579a6ba54f4a5`, tree
`0da16e98d7f60e7c78eccbbec0c92978728b1123`, pasó Actions38054448912,
job114219930405: **1764 PASS por Python3.11/3.12**, cero fallos/errores/skips,
identidades iguales, Source literal completo, FIN/ECHILD y cleanup0foreign.
Original11669964319:964805B/22CRC/SHA256
`c9cb666e3d55cfc301a5c2584e3cdfc2fee325b9ac4070fcbfbdc4a9b620332b`.
Wall116.937/113.040s y RSS máximo300036096/301252608B por época.
Inventario post-FIN811159552/811134976B: **no pico físico global**.
Ext4/4096 observado con91482693632B libres y18422996 inodos; capacidad
física de esa ejecución, sin garantía universal, enforcement de cuota ni prueba
del Droplet. Bootstrap histórico8.108s/RSS229363712B/FIN/ECHILD;
Git225468416→439844864B observado, conservando el límite histórico512MiB.

La prueba nativa38055205582/job114222135982 conserva un RED nuevo.
Original11671540153:58499B/33CRC/SHA256
`36ebcad1460eb27e8dec67dd000f242e14cb91a5790dbdd763a755e5346a69cc`.
PID1 registró Starting/Started y salida**1**, con boot propio
`a7390f4c7cf94563a3f246590332f9c8`, unidad nonce
`rc6-native-d5d6b6a21245408daef00faf4c3c80cc-guard.service` e Invocation
`acd438f993054198890686213cdf6041`. Python ROOT se ejecutó y alcanzó
`controller_snapshot:330`. RAW1441B/SHA256
`db3e9eb12b32acaa25264395ea7f5565e79e585564bcb193bc2b64c7efa8c85c`
demuestra `ValueError: ROOT_CUSTODY_ACTUAL_SAFE_PID1_MASK_REQUIRED`.
**El errno no aplica a ese ValueError**. Esta ejecución no falló con226;
el errno final del fallo226 histórico continúa UNKNOWN. Birth/pidfd, sello
ROOT y los cuatro casos todavía no están certificados; ROOT_FIN UNKNOWN,
namespace retenido, cleanupfalse y crédito físico0. No se repite este SHA.

| Bloqueo | Corrección mínima y evidencia | Obligación restante |
|---|---|---|
| Parser rechaza máscara real `/proc/1` entre comillas | systemd255.4 `dbus-execute.c:3128–3178` usa `unit_concat_strv`; `unit.c:4575–4604` rodea cada ruta con comillas. Los84 parches Ubuntu8.17 no modifican esos serializadores. `.split()` conserva las comillas. Parser tipado de listas con escapes, append/reset vacío, secciones y conflicto singular fail-closed; conserva RAW y hash. | La unidad RAW no fue capturada en9c743: no se afirma repetición real. Snapshot ROOT-owned antes del guard y captura sólo tras FIN propio y unidad/cgroup inactivos; diagnóstico nunca concede custodia. Nueva CI exacta y después una prueba nativa. |
| Readback namespaces canónico | systemd255.4 `dbus-execute.c:1472,1861` serializa el bitmap permitido mediante `namespace_flags_to_string`. Entrada original `~cgroup net user ipc uts` corresponde exactamente a salida `mnt pid`, bitmap0x20020000. | Guard compara semántica exacta y rechaza cualquier namespace adicional; propiedades del servicio intactas. Source9c743 no alcanzó este guard posterior: hallazgo de código, no un segundo error nativo atribuido. |
| Máscara magiclink del root del executor | Source9c743 alcanzó PythonROOT tras sustituir el magiclink por el directorio real y mantener protección. | La operación peligrosa histórica está probada; su errno final226 sigue UNKNOWN. Guard permanente rechaza raíz/magiclinks antes de ROOT. No se quitan restricciones para obtener PASS. |
| Reporting de mounts | Bootstrap privado realiza mounts confiables. El sello prueba denegaciones con syscalls de mount de operandos inválidos: son intentos denegados, no cero intentos. | Reportar alcance exacto, ausencia de mounts exitosos de actores y backing/cuota no iniciados; ningún flag sustituye evidencia nativa. |
| Filtro NONROOT supervisor/worker | Native9c743 confirma57reglas y26denegaciones, herencia0→1→2, dumpable real0/1, FIN y cleanup0foreign. Wall0.201s/RSS máximo103731200B, inventario post-FIN69632B. | Es prerrequisito de filtro; no prueba custodia ROOT, perfil PAPER1CPU/1GiB ni presupuesto total del Droplet. |
| G0, lectores admitidos y G5 original | Los límites20GiB/26GiB/reservas,1202/512MiB/profundidad32/retención y writer productivoV3 permanecen. | Revisión explícita witness ROOT y EACCES vs PID ausente; cuota/reservas físicas; G1 focal dual con reviews auténticos; cota completa y revisión temporal de admisión antes de Horizon original. G7/G8 esperanG0–G6. |

Cierre local del fix tipado: **428 PASS por Python**, cero fallos/errores/skips,
5.409/5.508s como NONROOT. Helper SHA256
`92cbf003bafad50998b21867f10ba9ff61b9f1f807fbb23d928cd6ae20295bd3`;
tests `9d4c6c265091d09cf005cf207273de7fdfd7046acc985c92ae40dfde500842b4`.
Incluye regresiones de sección[Service], dirección y conjunto completo del
filtro original, identidad dev/inode/mode/UID/GID/nlink/size/mtime/ctime estable
sin confundir atime, y tiempos canónicos290s/10790s sin cambiar deadlines.
El manager, planes, sello, propiedades systemd y drop original permanecen.
Estos PASS locales no certifican ROOT: el Source congelado necesita CI exacta
positiva antes de una nueva prueba nativa con RAW de unidad previo a birth.

Se conserva un candidato en PR483 y ancestry de481/482, sin una rama o PR
documental adicional. USD0 de infraestructura recurrente adicional,
PAPER/SHADOW, real_orders_sent=0 y PPI Watch intacto. Sin contratación,
ampliación, infraestructura persistente, merge ni deploy.
