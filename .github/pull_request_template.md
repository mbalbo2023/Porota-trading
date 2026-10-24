## Preflight de concurrencia (requerido)

- [ ] Leí `/AGENTS.md`, versión `2026-09-12/1`, y cualquier política aplicable del subdirectorio.
- [ ] Leí el checkpoint canónico vigente y confirmé allí el identificador exacto del baseline RC6.
- [ ] Confirmé que esta rama no pertenece a otro chat ni hay cambios concurrentes en las mismas rutas.
- [ ] Dejé identificados el responsable/chat, el alcance, la rama y el SHA base.

**Responsable/chat y workstream:**  
**Rama de trabajo:**  
**Base y SHA:**  
**Checkpoint canónico:**  
**Rutas modificadas y motivo:**  

## Continuidad entre chats (requerido)

**Checkpoint ID y ruta leídos completos:**  
**Rama/ref y SHA exactos verificados:**  
**Commits/runs posteriores revisados:**  
**Delta actualizado en este PR (ruta) o motivo verificable si no cambió el estado:**  
**Commit SHA del checkpoint:**  

- [ ] Conservé pendientes, decisiones, invariantes, evidencia única, errores y parciales; marqué lo no verificado.
- [ ] Separé código, CI, runtime, evidencia/importación y READY end-to-end.
- [ ] Volví a leer desde GitHub el checkpoint y verifiqué su commit SHA.

## Cambio y validación

**Resultado esperado:**  

**Validaciones ejecutadas y resultado:**  

**Impacto operativo:**  

- [ ] Este PR no interactúa con el workstream/recursos PPI de ingesta, o incluye la autorización explícita de su responsable.
- [ ] No ejecuté despliegues, órdenes ni workflows manuales de producción.
- [ ] Revisé el diff completo y confirmé que contiene solo el alcance declarado.
- [ ] Actualicé el checkpoint o indiqué el PR/commit donde quedará actualizado.

Si no puedes completar algún punto, explica por qué y deja el PR en borrador.

## Gobierno RC6 de PR y alcance de release

**Issue/workstream dueño y persona responsable de la revisión futura:**  
**Fecha o condición objetiva de siguiente revisión (UTC/ART):**  
**Rol de este PR: integra / reemplaza / conserva evidencia / sigue retenido:**  
**PR relacionados, SHA del sustituto y prueba de equivalencia (si aplica):**  
**Beneficio de negocio y riesgo de no integrar:**  

**Incluido en este PR, por ruta y motivo:**  
**Excluido del próximo candidato y razón:**  
**Regresiones, dependencias, costo y mitigación FIX-FORWARD:**  

- [ ] Comparé este diff contra el HEAD productivo y el inventario vigente de [#485](https://github.com/mbalbo2023/Porota-trading/issues/485); no supuse que un PR cerrado demuestre equivalencia runtime.
- [ ] Contrasté permisos/ownership de las rutas tocadas y registré owner/revisión de todo PR que siga retenido.
- [ ] Distinguí evidencia de código, test/CI, artefacto, identidad desplegada y comportamiento runtime; adjunté SHA, run, digest y estados por fase donde corresponda.
- [ ] Para PAPER: probé caso GO y NO_GO con contratos/fuentes/riesgo por identidad; no elevé catálogo o SHADOW a fill ni permití órdenes reales.
- [ ] Comprobé que el cambio no modifica PPI Watch y que la auditoría no ejecuta estrés del Droplet.
- [ ] Si requiere Predeploy V2 o Deploy V2, presenté antes el inventario exacto de incluidos/excluidos, razones y riesgos; la aprobación expresa de esa fase está enlazada aquí. Si aún no existe, el PR permanece sin promoción.

**Evidencia de aprobación de alcance, si procede:**  
**Estado canónico actual (EN_GITHUB / ARTEFACTO_VALIDADO / DESPLEGADO / VALIDADO_RUNTIME / NO_VERIFICADO):**  

La plantilla solicita evidencia; no implementa por sí misma reglas de protección ni CI. No marcar GREEN sin el recibo de la fase correspondiente.
