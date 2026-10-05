# RC6 — contingencias verificadas y límites reales

Workstream: WS-RC6-NIGHT-CONVERGENCE-20261005.
Investigación solicitada por Martín Balbo; observada el 2026-10-05T02:12:54.262Z.
Complementa las instrucciones fijadas en e51876cdebaa6f198a4912ffb0289479d27253fa. No reduce ni sustituye sus gates, ownership, identidad del candidato, plazo o exclusión. Sólo PAPER/SHADOW, real_orders_sent=0, FIX_FORWARD_ONLY. Toda operación de host exclusivamente mediante Actions canónico.

## 1. Qué acceso se probó realmente

El conector permite leer source exacto, runs, jobs, pasos, logs completos y metadata de artifacts. Se recuperaron los 791319 caracteres del job 111531848234 de Predeploy 37234866451. Sus salidas ejecutadas muestran verificación de checkout, bundle e image tar, imports offline y contrato congelado. Esto es evidencia histórica de la semilla, NO aprobación del candidato de convergencia.

El artifact 11315198085 tiene 407679652 bytes. El conector lo descargó a file_id. La transferencia al executor falló con "file exceeds the executor transfer limit of 32 MiB"; la URL temporal devolvió HTTP403 Forbidden, body "error code: 1010". No se estableció la causa del 403 ni se deduce una incapacidad general de GitHub Actions. No eludir ese control ni reutilizar el artifact prohibido como candidato final.

Prueba positiva realizada ahora: descarga por GitHub connector, transferencia autorizada al executor, lectura real del ZIP, CRC y SHA256 local comparado con digest de metadata GitHub:
- Artifact 11277627152, rc6-validation-resume-runtime-37131712187, 7757 bytes:
  sha256:a4bf46ea04860448e349483d8fe96f870efacb10cf376725dd42e63af032c99d.
- Artifact 11277236383, rc6-validation-resume-plan-37131712187, 48450 bytes:
  sha256:4a5f6d0a2d1824fcdfad2e32d64ca7e2ee7d9c5f7b51b528683302c9fda8428c.
También se contrastaron los cinco hashes internos de resume.sh, resume-config.json, frozen candidate, bundle manifest y permanent-fix.json; config idéntica en ambos ZIP y enlace de SHA/tree/artifact/digest/run entre los JSON. No se ejecutó código de esos artifacts.

Resultado: SMALL_ARTIFACT_BYTE_TRANSFER=VERIFIED. No equivale a FINAL_CANDIDATE_ARTIFACT=VERIFIED, que sigue pendiente.

## 2. Ruta para el artifact final grande

Después de entrega auténtica y liberación del integration owner, auditar el pipeline entregado. Si no contiene evidencia acotada suficiente, corregirlo bajo WRITE_OWNER propio en la rama sucesora autorizada. No escribir ahora los workflows del owner activo, ni crear un workflow paralelo de deploy.

Dentro del Predeploy V2 canónico:
1. Construir una sola vez por SHA; subir el artifact congelado.
2. En un job dependiente del mismo pipeline, resolver artifact ID/digest por API y descargar el ZIP COMPLETO exacto. Verificar repositorio, workflow/path/event, PR, head/tree, run y attempt contra la identidad congelada; rechazar expiración, ambigüedad o cambios.
3. Recalcular SHA256 del ZIP descargado y exigir igualdad con digest GitHub mediante un assert/exit no cero. upload-artifact/download-artifact puede emitir sólo warning ante digest distinto: ese warning NO basta como gate.
4. Auditar código verificador al SHA exacto y ejecutar la inspección completa requerida por RUNBOOK/orden: ZIP/manifest/source/bundle/image/layers/rootfs/modes/ImageID, hashes reales y smoke offline de la imagen congelada. No reconstruir otra imagen ni sustituir bytes por metadata.
5. Exportar artifact de evidencia <=32MiB con identidad completa, digest/longitud del ZIP original, versión/source del verificador, manifests/hashes/modos, resultados ejecutados, sources/bundle verificables acotados y resultado del smoke. Evitar credenciales/secrets.
6. Supervisor: descargar ese evidence artifact, verificar SHA256 local contra API, contenido/enlaces exactos, código y logs reales; diferenciar comandos impresos de resultados ejecutados. Si falta evidencia de un gate, queda pendiente. Un JSON que sólo declara GREEN no certifica nada.

La ruta de transporte pequeño está probada; la implementación integral sobre el futuro candidato aún debe verificarse. No atribuir PASS al candidato por artifacts históricos.

## 3. Antecedente real de recuperación después de deploy parcial

Run original 37125515526 falló el 2026-10-03 después de instalar/verificar runtime, en el wrapper de preopen NOT_DUE/BYMA_NON_OPERATIONAL_DAY.
La continuación 37131712187 concluyó success y produjo final-runtime.json observado 2026-10-03T15:16:36.701864Z:
- candidate 9ca8cb13bfc6664597e313e5d1874710e3263ec4
- product da697c6e6c2274579f9e4a112fabc4327475dd35
- tree 96a112a55ed779df3f7056b1e30d81aac8d0b791
- validation_status VALIDATED_RUNTIME; 423 archivos contrastados; real_orders_sent=0; estabilidad/exit139/cleanup en salida ejecutada.
Fuente:
https://github.com/mbalbo2023/Porota-trading/actions/runs/37131712187
Workflow y scripts fijados:
https://github.com/mbalbo2023/Porota-trading/blob/d4df82804c48b13e5dd46a03f951a99d496385e5/.github/workflows/porota-deploy-v2-validation-resume.yml
scripts/rc6_deploy_validation_resume.py, scripts/rc6_deploy_resume_preparation.py, scripts/rc6_deploy_resume_mounts.py en ese mismo SHA.

Esto es evidencia del 3 de octubre, no del runtime actual ni del candidato nuevo.
No ejecutar ese workflow histórico con defaults: fija SHA/run/artifact/tracker/owner antiguos, acepta sólo una firma de falla y un blob de workflow concreto. Tampoco está presente en la semilla actual como una vía genérica lista.
La continuación evita rebuild/re-promoción de observer/dashboard, pero su tail hace escrituras de estado, housekeeping y puede reconciliar/reiniciar el servicio de aprobación crítica. No describirla como totalmente read-only. Toda reutilización exige auditar el tail exacto y sus efectos, ownership global/mutex y gates actuales; ninguna relajación histórica de ImageID, mounts, calendario u ownership sustituye el contrato actual.
No hay herramienta dispatch expuesta en el conector actual; no prometer que workflow_dispatch puede invocarse aquí. No disparar un push histórico ni un merge vacío para sortearlo.

Si un deploy nuevo instala el artifact correcto y falla sólo en validación:
- Reconciliar el run/attempt, refs, imagen y bytes/config/mounts realmente instalados, sin asumir que failure implica no aplicado.
- Adquirir/revalidar DEPLOY_OWNER y el mutex global; excluir todo deploy/resume queued/in_progress.
- Diagnosticar firma exacta de falla. Adaptar bajo ownership el mecanismo canónico de continuación, con parámetros actuales explícitos, tests adversariales pertinentes y evidencia de todos los gates. No rerun ciego ni copiar los defaults.
- Conservar el artifact aprobado; no reconstruirlo ni re-promoverlo si basta continuar validaciones. Si existe error real de código, FIX_FORWARD con nuevo SHA/artifact/gates.
- El marker histórico RC6_DEPLOY_V2_VALIDATION_RESUME=GREEN no reemplaza el criterio final del runbook actual: todos los gates y CURRENT_STATE VALIDATED_RUNTIME más RC6_DEPLOY_V2=GREEN emitido al final de ejecución real.

## 4. Árbol de contingencias

| Falla | Continuación autorizada | Qué impide certificar |
| --- | --- | --- |
| ZIP grande no transferible localmente | Auditoría integral en Actions + evidencia pequeña comprobada localmente | Verificador/bytes/enlaces faltantes; pipeline final todavía no probado |
| Timeout/API/runner transitorio | Releer estado remoto y resultado antes de retry, guardar run/attempt, máximo 2 intentos sin evidencia nueva | Error ambiguo o cleanup no reconciliado |
| Error programable | Corrección propia tras liberación del owner; nuevo SHA y nuevo artifact verificado | Ownership activo ajeno, auditoría/tests pendientes |
| Deploy parcialmente aplicado | Diagnóstico y continuación canónica condicionada del tail validatorio | Firma desconocida, identidad/runtime distinto o efectos sin auditar |
| Supervisor no despierta o plataforma caída | Estado durable y evidencia permiten retomar al recuperarse la plataforma o transferir explícitamente ownership al integrador existente | No existe un segundo ejecutor independiente armado |
| GitHub Actions/credenciales indisponibles | Mantener estado, registrar bloqueo preciso y seguir sólo reintentos permitidos/plazo | No hay vía autorizada de host fuera de Actions |
| Tiempo insuficiente | No comenzar nueva promoción sin reservar timeout real +15min y los gates previos; observar sólo run ya iniciado | No rebajar gates para llegar al horario |

Un segundo horario en la misma plataforma no prueba independencia. Un watchdog propio de GitHub sería otro trabajo a implementar y probar; un schedule de GitHub sólo en una rama aislada no funciona como respaldo, porque el workflow programado debe estar en default branch. Además puede retrasarse. No modificar main, crear otra automation, ni instalar otro deployer en nombre de esta contingencia. Handoff al integrador sólo con liberación/adquisición explícita del mismo owner global, nunca dos writers.

## Fuentes de comportamiento Actions
https://docs.github.com/en/actions/tutorials/store-and-share-data
https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows

No se ejecutó deploy, no se tocó el desarrollo activo ni producción para estas pruebas. Evidencia y preparación no constituyen garantía de finalización futura.
