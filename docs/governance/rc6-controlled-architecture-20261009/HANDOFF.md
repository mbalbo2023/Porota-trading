# POROTA RC6: handoff de recuperación arquitectónica controlada

Resultado finito: **BLOQUEADO, sin candidato elegible ni calificación G0–G8**.
Se publica una entrega documental sucesora de #479; no se agrega otra branch
de deploy, no se mergean #475/#476/#477 y no se modifica su código. La evidencia
de publicación HEAD/tree/PR y la liberación auténtica del scope propio se ligan
en #471/#473 después de releer GitHub; no se inventa un SHA autorreferencial aquí.

## Base, autoridad y ownership

| Referencia GitHub revalidada | SHA | Tree |
|---|---|---|
|BASE documental #479|215169ecd0f71d3b293304c7976be2593efdc043|9b2ea2b7cfed724fe828eb4ec2f129b17f27d89f|
|WIP examinado|ecad18b6010e3b7e874a00edc72644b3dcc78790|7054a76efc0a22e56605af435f0b4502474783ad|
|#476 correctivo|dfc240a478cf08e0c6a9b0ba1b760307b09a9565|192ea26348c8fe10db55857cdd6029edd8344708|
|#477 preventivo|756d37b93aa26bb6395dac481bf3c2dda9d034d7|bfb13175cfc7a1b8325c0e1c08b44b51a03452d1|
|#475 original|3091e93c05cf89f9a0c16ca912d0af98f036ff17|00efec0f39f70f5a4ba1ba9c215647b92e05eeb7|
|Productiva histórica/HEAD actual observado|da697c6e6c2274579f9e4a112fabc4327475dd35|96a112a55ed779df3f7056b1e30d81aac8d0b791|

Los PRs mandatorios continúan OPEN/DRAFT/UNMERGED. #471 y#473 fueron recuperados
íntegros paginados al comienzo (306/251 comentarios), y releídos para eventos
posteriores de ownership. El RCA/ERROR_MATRIX/QUALIFICATION_PLAN y ambos inventarios
principales de #479 se reutilizan; esto no es otra auditoría general.

La última pareja core6069926329/6069926700 pertenece a
`CODEX_RC6_ARCHITECTURAL_RCA_20261008_1205UTC`, `RELEASED=false`.
El lease vencido **no** lo libera. La pareja6070721931/6070722133 sólo libera
el scope documental de #479. Sin transferencia core legítima, Source/runtime/
archive/harness/fixtures/CI siguen READ_ONLY. El bloqueo fue documentado primero
en6080607491/6080607839, antes de adquirir otro scope.

Ownership propio: `CODEX_RC6_CONTROLLED_ARCHITECTURE_20261009`, adquirido en
6080627178/6080627453, renovado en6080890174/6080890490; scope exclusivo
`docs/governance/rc6-controlled-architecture-20261009/`.
Branch única `review/rc6-controlled-architecture-20261009`, BASE=#479 actual;
PR draft contra `review/rc6-systemic-rca-ecad18b6-20261008`.
INTEGRATION_OWNER/DEPLOY_OWNER=NOT_ACQUIRED. Cierre libera sólo este scope.

## Decisiones y validación efectiva

| Entrega | Resultado real | Pendiente |
|---|---|---|
|[G0_ARCHITECTURE.md](G0_ARCHITECTURE.md)|B nativa seleccionada;30GiB mínimos antes de controles frente al piso14GiB; comparación A/B/C y aceptación conjunta|Envelope compound, backend preparado y custodia ROOT desde primer actor/FIN; disponibilidad canónica sin costo nuevo|
|[G5_MODEL.md](G5_MODEL.md)|1202 clocks/phases, ambos bordes, retención sin crédito operativo GC, ecuación global, descarte del forecast como certificación y propuesta única acotada|U físico global del WIP no demostrado; original af08 ya RED; factoring no implementado|
|[ERROR_MATRIX.md](ERROR_MATRIX.md)|Matriz causal única con componente/repro/fix/riesgo/guard/test/aceptación/estado, creada antes del analizador|Corefixes nuevos prohibidos por ownership; antecedentes WIP no calificados|
|[EVIDENCE_CUSTODY.md](EVIDENCE_CUSTODY.md)|3127 rutas/2683 blobs SHA256;36 paths elegibles; seis helpers protegidos; política prospectiva sin pérdida/borrado|Guard CI y packaging propuestos; imagen no inspeccionada; custodia remota durable antes de expiry|
|[reproduce.py](reproduce.py), [analysis.json](analysis.json)|Un run local stdlib/Python3.12.14 exitoso, aritmética+Gitblobs+matchers; sin carga financiera ni ROOT|No G1Actions/dualPython, no FIN nativo, no cuotas ni restauración1202|

Las correcciones demostrables ya desarrolladas en ecad se conservan: captura única
SQLite/WAL por tick, Source prestado autenticado, cierre explícito/quiesce antes
de seal, índice bajo lock antes de READY con child readonly, serialización/reuse
privado acotado y lifecycle de fixtures. No se reescriben ni se otorga PASS global.
Source10 y el mismatch focal siguen sin causa exacta: falta RAW discriminante.
La custodia de RED después de FIN no debe depender del PASS semántico.

Provenance de los inputs/recibos y refs frescos: [source-provenance.json](source-provenance.json).
Validación local acotada y sus límites: [cheap-validation.json](cheap-validation.json).
SHA256/tamaño de todos los archivos de esta entrega: [MANIFEST.json](MANIFEST.json),
que excluye sólo su propio contenido para evitar un hash autorreferencial.
El SHA/tree exacto publicado y la identidad del manifest se atan en el recibo
auténtico final de GitHub; el manifest por sí solo no afirma publicación.

BIG conserva12000 instrumentos,60000 observaciones, cinco salidas PAPER,
qualification<=75s y límite externo90s, RSS<2GiB y evidencia<=128MiB.91.053s
históricos con ciclo incompleto no se “corrigen” cambiando presupuesto.
Publication45.090 incluye prepare34.918 y encode6.819: no sumar esas subfases dos
veces.124358 entradas retenidas se corrigen mediante último consumidor/FIN/RAW,
no ampliando100000 ni omitiendo casos. Ninguna de esas cargas se lanzó aquí.

## Integración única futura: preparación finita

Sólo después de transferencia/release auténtico core, un INTEGRATION_OWNER legítimo
reconcilia #475/#476/#477/ecad por contenido, conserva fixes existentes y aplica
el conjunto compatible mínimo en **una** branch sucesora de integración.
No merge ciego de PRs antiguos, no candidatos paralelos, no deploy en esta misión.

Antes de congelar F/tree(F): cerrar las cuatro condiciones B de G0; entregar U
de G5 con sus cinco inputs auténticos o descartar la propuesta; demostrar cierre
de fixtures/retainedinventory<=100000; diagnóstico discriminante/RED preservado;
packaging selectivo con helpers; custodia y términos G7 Dockerdaemon/storage.
Si G0/G5 no cierran, **STOP**, sin más parches experimentales ni carga completa.
Una cota superior>cap rechaza esa certificación; no inventa imposibilidad del WIP.
Tras fix+guard+barato por causa, congelar un F; cualquier cambio reidentifica F,
sin borrar la evidencia RED previa. Este SHA documental no es F.

## Secuencia posterior verificable, sin autorización automática

Se conserva el contrato de [QUALIFICATION_PLAN de #479](../rc6-systemic-review-20261008/QUALIFICATION_PLAN.md).
Cada gate consume recibos auténticos de **F/tree(F)** y revalida owner/livecapacity
<=60s/FIN antes de su propio productor. UNKNOWN/RED/stale detiene sucesores.
Esta misión no habilita heavy ni privilegiados, aun con documentación publicada.

| Gate futuro | Aceptación de F / prerrequisito |
|---|---|
|G0|Envelope/fullGit/fullSource/157closure/19objetos, límite físico agregado y custodia nativa B, ownership vigente y reservas4GiB/10% inodes; cuatro condiciones previas cerradas|
|G1.311→G1.312|Corpus original barato dualLinux, collection=execution=JUnit y cero skips/xfails; G0F auténtico|
|G2→G3|Focals originales311/312; Source y semántica exactas, RAW inclusoRED tras FIN; G1dualF y luego G2F|
|G4|BIG12000/60000,5PAPER, cycle committed<=75s/outer90s, RSS/evidence/entries originales; G0–G3F|
|G5|1202 originales completos,512MiB global físico/lógico, restauración byte exacta/CRC/hash, profundidad32 y retención9h+1h; G4F y U previo probado|
|G6.311→G6.312|Full governed original sin omisiones, identidades exactas y retained<=100000 intrafase con lifecycle positivo; G5F|
|G7|Predeploy/build once del único F,closure/supplychain/provenance y custodia real Dockerdaemon/container/storage; G6dualF y mandato futuro|
|G8|Inspección del mismo artifact/image/tar inmutable, hashes/config/context/Source y PAPER; no rebuild ni inferencia de contenido de imagen|

No rerun idéntico esperando otro resultado. Un fallo exige causa discriminada,
fix mínimo, guard, barato y evidencia antes de otro candidato. No se usan tests
locales ni checkpoint-attestation para declarar GREEN de G0–G8.

## Estados inequívocos al cierre

**EN_GITHUB**: docs/modelo/matriz/política/índices/analizador de este scope cuando
el PR y sus blobs sean relectos; HEAD/tree exactos quedan en el recibo final.
**VALIDADO**: sólo aritmética1202, consistencia1049 histórica, ledger3127/2683,
matchers36/helperclosure estático, ejemplos prospectivos del guard y publicación
GitHub. **BLOQUEADO**: ownership core, habilitación G0 B, cota G5 WIP, calificación
G1–G8, custodia externa durable pendiente, packaging/guard CI propuestos y G7.
ARTEFACTO_VALIDADO=false; DESPLEGADO=false; VALIDADO_RUNTIME=false.

PAPER/SHADOW ONLY; PRODUCTION_PAPER/SIMULATION; real_orders_sent=0; rutas reales
BLOCKED/NOT_CALLED; PPI Watch UNTOUCHED; FIX-FORWARD ONLY. Sin rollback, merge,
deploy, SSH/manual, force-push, escritura productiva, eliminación ni nuevos costos.
