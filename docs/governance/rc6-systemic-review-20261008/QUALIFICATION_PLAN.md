# Plan finito de certificación RC6 para un único SHA reconciliado

Estado: **BLOQUEADO / NO EJECUTAR MATERIAL**. Este plan no concede autorización
para calibración privilegiada, BIG, Horizon, fullGov, Predeploy, merge o deploy.
Revisión documental sobre ecad; no candidato final elegible. El SHA futuro F se
fija una vez terminado el conjunto mínimo de correcciones y su revisión.

## Preparación con salida finita

| Paquete causal | Resultado previo obligatorio | Decisión si no se demuestra |
|---|---|---|
|Ownership/integración|Release/transfer auténtico del scope core anterior y ACK de conflictos; un INTEGRATION_OWNER reconciliará #475/#476/#477/WIP sin merge ni forcepush. Estado ops histórico se identifica como tal.|No escribir core. La rama de informe preserva evidencia sin competir con F.|
|G0 arquitectura|Modelo compoundbootstrap ambosPython/157distribuciones/19objetos/fullGit/fullSource y cada temporal/control/inode, metric/origin auténticos; filesystem/device/mount/allocunit comparables;4GiB libres adicionales y10%inodes.|Si cualquier componente es desconocido, detener. No calibrar para averiguar el prerequisito faltante de su propio calibrador.|
|G0 boundary|Hard cap agregado,signal/TERM2/FIN5/ECHILD, UID/capabilitydrop, escape/ABI negatives, owncleanup y disponibilidad nativa garantizados antes de backend.|Elegir una única alternativa de boundary. Si requiere otro runner/contrato, resolución explícita previa; no polls/RLIMIT como equivalencia ficticia ni nuevos wrappers.|
|G5 modelo|Upper bound A(k)<=536870912B para todo1202yencoderbranch,6000cohortes,20:00/20:00:30,CAS/recipes/temp/allocunit/anchors/base/pins/wholepacks. Byteexactrestore/CRC/hash/depth32 y no prematureGC.|Hold permanece. Si el bound conservador excede cuota, corregir reutilización/lifecycle existente y probar barato; ningún fullHorizon experimental.|
|Producto mínimo|Captura única/close/queryindex/publicationreuse ya desarrollados: byte/digest invariants y headroombudget antesBIG. Diagnóstico Source10 member/stat y focalRAW inclusoRED afterFIN.|Sin datos discriminantes no nuevo rerun. No alterar enginefinanciero ni budget.|
|Fixture/harness|Nativepositive lastconsumer/tracker/census; roots propios preservados o retirados tras FIN+capture. Bound<=100000sin perder negativos. Hook no destruye evidencia/collection por error de presentación.|No G6. No globalprune, entradas omitidas, límite ampliado ni infraestructura desconocida exenta.|
|Bundle/custodia|Prospective roots/evidence rules coherentes con Source manifest, replay graph y destino hash-verificado. Roles/copiasSourcelegítimas preservadas; controlesmetadata y RAWexpirable diferenciados.|No borrar RAWhistórico. No veto por líneas/files; riesgo concreto y dependencia no cerrada quedan explícitos.|
|G7 termination|Scope de Dockerbuilder/daemon/container y tooling se acredita hasta FIN real, storagepeak/image/tmp incluidos.|No asumir dockerCLIexit equivale FINdaemon. G7 sigue bloqueado.|

Después se agrupan sólo fixes causales mínimos en una branch sucesora única de
integración y se congela **F/tree(F)**. Revalidar contra GitHub antes de publicar o
consumir evidencia. Las mediciones históricas sirven para RCA/comparación; no son
gates de F. Un cambio de SHA invalida gates candidatos, nunca borra el RED anterior.
No se congela cada parche como candidato para repetir toda la suite.

## Orden de un intento, nunca cadena automática

| Gate | Prueba/evidencia necesaria de F | Prerequisito y criterio objetivo |
|---|---|---|
|G0|Static/admission/fullGit/fullSource/capacity/ownership reales en ubuntu-24.04, exactcode+policy+inputhashes, cierre de boundary si corresponde.|Todos los paquetes de preparación cerrados; actualfilesystem sufficient; no Source/runtime financiero modificado por admission. Artifact authentic, leasecurrent, unknown=RED.|
|G1.311 yG1.312|Corpus arquitectónico barato completo dualLinux, collection/execution/JUnitheaders/identities exactos; sin subTest fakecounts, skip/xfail ni heavy.|G0F.GREEN; closures originales conservadas. Local1413.12 de esta revisión no sustituye esos gates.|
|G2|Focal311 original con sourcecustody/metadata/semanticresults y RAW/JUnit preservados inclusoREDtrasFIN.|G0+G1dualF.GREEN, capacidad live del productor, TERM2/FIN5viables. Missing/RED/stale detiene aquí.|
|G3|Focal312 original mismas identidades y invariantes.|G2F.GREEN y prerequisitos deG2revalidados para su propio productor.|
|G4|BIG12000/60000,5salidasPAPERfactuales,cyclecommitted,Sourceunchanged,<=75s,RSS<2GiB,evidence<=128MiB,entries<=100000,ownFIN/cleanup.|G0–G3F.GREEN, modelo de trabajo+headroom demostrado antes de arrancar.90souterno relajado;75–90diagnóstico no qualification.|
|G5|1202cortes originales,9h+1h,1200/6000Horizonoriginal,bound512MiB; allmemberrestorebytes/CRC/manifest,depth32,browser/retentionoriginales.|G4F.GREEN y modelo global antesmaterial. Lease histórico/continuo autenticado. Factoryrestart no crashproof. Ningún corte reducido ni wallclocksleep inventado.|
|G6.311 yG6.312|Fullgoverned repo-root original, módulos/parametrizaciones heavy preservados; colección=ejecución=JUnit; retainedinventory completo<=100000.|G5F.GREEN; capacidad/lifecycle/infraestructura positivos propios de cada epoch.311antes312; no end-of-suite deletion como sustituto de lifecycle.|
|G7|Predeploy exactF sólotrasG6dual.GREEN; buildonce,closure157+hashes/ABI/backends,supplychain/secret/source/contextchecks, daemoncustody/storage genuine.|Authorcurrentlive y todosgatesFauthentic. No heavy alpush. ConsumoG6authentic no repetición innecesaria. ArtifactSHA/imageconfig/provenance byteexact verificables.|
|G8|Inspección/calificación del único artifactinmutable deG7,F/tree,config/image/tar/manifestsha; verificaciones de las mismasbytes y PAPERsafety.|Sin rebuild ni SSH/deploy en esta misión. Deploy/runtime requieren mandato/ownership separado; G8 no los autoriza.|

Cada heavy futuro necesita autorización vigente de esa etapa; el éxito de una
prueba no autoriza automáticamente la siguiente. Esta misión no concede ninguna.
Antes de CADA productor: livecapacity<=60s, cpu/mem/load/fs, dueño+scope+nonce,
evidencia de prerequisitos exactSHA y prueba de terminación viable.

## Stop rules que impiden otro ciclo improductivo

1. Abrir un registro causal único por fallo, conservar RAW/errno/member/campo y
   primera métrica violada. No sustituirlo por un nombre de guard derivado.
2. Antes de nuevo run documentar defecto reproducible, causa discriminada,
   diffmínimo, guard permanente, barato y criterio de cierre. Si falta un dato,
   hacer inspección acotada; no material para descubrir arquitectura.
3. Elegir un arreglo arquitectónico entre alternativas y verificarlo. No iterar
   codecs/strides/privilegedwrappers para probar suerte. Preservar los fallos
   originales y poner límite de salida a la investigación.
4. Falta capacidad/kernel/FIN/boundglobal/ownership: BLOQUEADO con alternativas
   concretas; no timeoutmayor, quotamayor, escenario omitido o runner más rápido
   para cambiar un RED en GREEN.
5. Si falla un gate de F: detener sucesores, resolver causa y agrupar fixes en
   nuevo F sólodespués barato/revisión. No rerun idéntico ni SHA cambiado sin
   reidentificar evidencia. Historial permanece intacto.
6. Mantener separados STATES: desarrollado/committeado/enGitHub; barato-local;
   Actions-gate; artifactqualified; deployed; runtimevalidated. Un estado no
   otorga los otros.

PAPER/SHADOW, PRODUCTION_PAPER/SIMULATION,real_orders_sent=0,rutas reales bloqueadas,
PPI Watch intacto, fix-forward. Branches/evidencia preservadas. Sin merge/deploy.
