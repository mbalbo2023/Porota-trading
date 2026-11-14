## Preflight de concurrencia (requerido)

- [ ] Leí `/AGENTS.md` de `main` y del SHA productivo RC6 verificado, junto con su política vigente.
- [ ] Leí el checkpoint canónico vigente y confirmé allí el identificador exacto del baseline RC6.
- [ ] Confirmé que esta rama no pertenece a otro chat ni hay cambios concurrentes en las mismas rutas.
- [ ] Dejé identificados el responsable/chat, el alcance, la rama y el SHA base.

**Responsable/chat y workstream:**  
**Rama de trabajo:**  
**Base y SHA:**  
**Checkpoint canónico:**  
**Rutas modificadas y motivo:**  

## Ownership de este PR (completar; el guard de main verifica el recibo)

Workstream ID: <completar>
Owner declaration URL: <completar>
Work branch: <completar>
Base SHA: <completar>
Scope paths: <completar>

El recibo vigente debe estar en #980, aprobado por `mbalbo2023`, con campos
literales `Workstream ID`, `Ownership status: ACTIVE`, `Work branch`, `Base SHA`,
`Scope paths` y `WRITE_OWNER`. Revisar comentarios posteriores: liberación,
supersesión o nueva decisión estructurada del Workstream ID exacto requieren
reconciliar el recibo antes de continuar; notas de avance no revocan ownership.
El check es un snapshot con fecha UTC: comentarios posteriores no lo reejecutan.
- [ ] Revalidé ownership y scopes solapados inmediatamente antes del merge; un PASS anterior no prueba lease vigente.

## Checklist histórico previo a código y tests (revisión, no prueba automática)

- [ ] Leí #1093 (sucesor de #732 saturado), sus correcciones y cualquier sucesor verificado.
- [ ] Busqué implementaciones equivalentes en código, commits, PRs abiertos/cerrados, issues, tests y Actions antes de escribir código o tests.
- [ ] Reconcilié HEAD real, ownership vigente #980 y scopes únicos; no repetí trabajo ya verificado.
- [ ] Cada test nuevo tiene aplicabilidad actual y una regresión concreta; documenté lo no aplicable.

**Búsquedas exactas, enlaces y resultados (implementado/parcial/faltante/supersedido/no aplicable):**

**Checkpoint vigente: URL/comentario y fecha UTC; correcciones posteriores revisadas:**

**Evidencia separada: código SHA/PR; Actions run; artifact/manifest/digest; runtime receipt o NO_VERIFICADO por capa:**

**Solapamientos descartados y motivo verificable de alcance único:**

El guard técnico no acredita memoria perfecta, búsqueda exhaustiva, ausencia total
de duplicados ni runtime. Distinguir política publicada, template, CI probado,
branch protection, deploy y validación runtime. Cambios del propio guard sólo
entran en vigor al usarse desde una base confiable posterior a su integración.
El workflow del PR sigue siendo modificable; protección del propio workflow y
enforcement automático al momento de merge permanecen NO_VERIFICADO.

El enlace debe apuntar al comentario de ownership en un issue de este repositorio,
publicado por el owner. La rama y el SHA base deben coincidir con el PR actual.
No usar un comentario histórico como prueba de ownership vigente: reconciliar
owners y rutas al momento del merge.

## Continuidad entre chats (requerido)

**Checkpoint ID y ruta leídos completos:**  
**Rama/ref y SHA exactos verificados:**  
**Commits/runs posteriores revisados:**  
**Delta actualizado en este PR (ruta) o motivo verificable si no cambió el estado:**  
**Commit SHA del checkpoint:**  

## Campos literales del control de continuidad (completar con evidencia real)

Checkpoint ID: <completar>
Continuity checkpoint path: <completar>
Checkpoint commit SHA: <completar>
RC6 baseline SHA: <completar>
Safety invariants: <completar>

El control exige `PRODUCTION_PAPER`, `REAL_ORDERS_SENT=0`,
`CASH_SWEEP_ORDER_ROUTING_ALLOWED=False` y `fail-closed` en la línea de
invariantes. Esos términos se completan sólo tras verificar el alcance;
un PASS de metadata no certifica runtime ni autoriza Deploy.

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
