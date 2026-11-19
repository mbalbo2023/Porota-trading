# Porota Trading RC6 — instrucciones para agentes

Este es el único punto de entrada de trabajo. El procedimiento está en
[docs/RUNBOOK.md](docs/RUNBOOK.md); los documentos retirados se consultan desde
[el índice histórico](docs/history/README.md). La documentación describe reglas;
el código, Actions y los recibos prueban lo que realmente existe o se ejecutó.

## Alcance y autonomía

- Ejecutar la tarea autorizada hasta su resultado verificable. La instrucción
  actual del usuario prevalece sobre planes y procedimientos anteriores.
- No pedir de nuevo autorización dentro de ese alcance, ni imponer micropasos,
  rondas de personajes, unanimidad ficticia o porcentajes de una misión antigua.
- **Antes de escribir código**, inventariar en HEAD, ramas, PRs abiertos/cerrados,
  commits, issues, workflows, tests y parches locales las soluciones anteriores del
  mismo defecto. Registrar SHA/PR y si están completas, parciales o descartadas
  con motivo. **Recuperar y completar la implementación parcial** antes de crear
  otra o un PR duplicado. Reusar pruebas válidas; consultar historial relevante,
  sin reabrir trabajo cerrado sin causa.
- Una limitación real de acceso, una escritura solapada o una ampliación del
  alcance se informa con su causa y el siguiente paso concreto. No inventar
  permisos, controles satisfechos ni restricciones comerciales.
- Paralelizar tareas independientes y revisar cambios según su riesgo. La
  revisión real no se sustituye por un checklist o un número de agentes.

## Seguridad permanente

- PAPER/SHADOW ONLY; modo esperado `PRODUCTION_PAPER / SIMULATION`.
- Órdenes reales bloqueadas. `real_orders_sent=0` es un requisito; sólo declararlo
  medido con evidencia del runtime correspondiente.
- No modificar procesos, cuentas, contenedores ni datos ajenos al ownership
  comprobado, salvo autorización concreta del usuario. **«PPI Watch intocable»
  no es una regla del usuario** (corrección expresa del 18-Nov-2026): ese nombre
  no justifica veto general, cuota supuesta ni prohibición de revisar o corregir
  su integración. Verificar identidad, tráfico y ownership antes de mutaciones.
- FIX-FORWARD ONLY: no rollback de runtime.
- GitHub y GitHub Actions son el control normal. SSH/manual es break-glass;
  Codespaces no es el entorno operativo. Evitar tareas manuales rutinarias para
  el usuario, que opera por voz; cualquier entrega copiable debe ser breve.
- No publicar secretos, datos de cuentas, bases productivas ni logs privados.

## Concurrencia e integración

- `READ_ONLY` no bloquea otros trabajos.
- `WRITE_OWNER` declara responsable, rama aislada, SHA base y rutas/componentes.
  Revisar solapamientos vigentes antes de escribir o integrar; no recuperar
  leases históricos como si siguieran activos.
- `DEPLOY_OWNER` es exclusivo global para cambios de runtime. Mientras esté
  activo, otro trabajo no despliega, reinicia, modifica systemd/DB ni limpia Docker.
- No escribir desarrollo directamente en `main` ni en la rama productiva.
  Usar branches y PRs, reconciliados contra HEAD real.
- Integrar en el candidato vigente; conservar historia y heads con evidencia
  única. No abrir PRs de almacenamiento ni cerrar, fusionar o borrar por edad.
- **Ningún PR huérfano:** asignar owner, scope, base/head, pruebas, siguiente acción
  y disposición explícita (`INTEGRAR`, `COMPLETAR`, `RETENER_CON_CAUSA`,
  `SUPERSEDIDO_CON_REFERENCIA` o `CERRAR_CON_EVIDENCIA`). Revisar todos los PR
  abiertos en cada handoff y reconciliar con #980. Cerrar sólo tras verificar que
  no queda código/evidencia única sin recuperar; jamás borrar su head a ciegas.
- Mantener `group: rc6-unified-paper-deploy` y `cancel-in-progress: false`.
  Los frentes convergen en un candidato y una promoción final coherente.

## Pruebas y despliegue

- Elegir pruebas por comportamiento cambiado, consumidores y riesgo. Agregar una
  regresión cuando detecte un defecto real. Ampliar la suite sólo por una
  dependencia concreta o un contrato vigente; explicar la selección.
- Reusar evidencia aplicable. Un cambio sólo documental no invalida pruebas de
  código inalterado; no construir ni desplegar documentación.
- Predeploy y Promote son los dos workflows de release. El bootstrap rootless
  es una preparación separada; nunca instalar permisos ni migrar datos como
  efecto implícito de una promoción.
- Construir una sola imagen del SHA final. Probar código y comportamiento
  funcional en esa misma imagen, identificarla con manifest/digest y promoverla
  sin reconstruir en el Droplet.
- El preflight comprueba precondiciones que pueden existir antes de instalar.
  La validación posterior comprueba el resultado real. No exigir que el futuro
  contenedor ya esté corriendo como precondición de su creación.
- Antes de modificar runtime: verificar artefacto, destino rootless, aislamiento,
  capacidad, datos necesarios y seguridad PAPER. Después: identidad de procesos,
  salud, rutas reales bloqueadas y evidencia de cero órdenes reales.
- Una caída de proveedor o mercado cerrado no veta por sí sola el deploy
  técnico, incluso fuera de rueda. **Siguen siendo bloqueantes** la identidad
  artefacto/digest↔contenedor↔controlador, procesos/hijos y health, acceso al host,
  integridad de datos y seguridad PAPER. No degradar esos gates para obtener verde.
  Sin mercado, diferir sólo el QA de frescura Current/Book a la **próxima rueda
  válida**, con owner y prueba agendada; cada instrumento sigue fail-closed.
- Limpiar sólo recursos de Porota identificados y no referenciados, con medida
  antes/después. No hacer prune global, borrar DB/volúmenes o tocar imágenes
  referenciadas. Registrar lo retenido y su motivo sin inventar una nueva barrera.

## Datos y resultados

- PPI es autoridad primaria de identidad; IOL/BYMA/A3/ROFEX complementan con
  evidencia. No sobrescribir identidad PPI ni escoger identidades ambiguas.
- Readiness es por instrumento y fail-closed. `ALL_CONTRACT_FAMILIES` no significa
  todos READY; CI verde, más operaciones o un replay rentable no prueban edge.
- Cambios de estrategia conservan baseline, causalidad temporal y costos;
  evaluar en SHADOW antes de cambiar decisiones. Preservar linaje y datos.

## Evidencia y cierre

Usar `PROPUESTO`, `DESARROLLADO`, `COMMITTEADO`, `EN_GITHUB`,
`ARTEFACTO_VALIDADO`, `DESPLEGADO`, `VALIDADO_RUNTIME`, `NO_VERIFICADO`, `BLOQUEADO`.
GitHub/SHA prueba código; Actions prueba pipeline; manifest/digest prueba paquete;
runtime prueba instalación; DB/logs/APIs prueban comportamiento. El chat no es
fuente de verdad operacional. Workflow SUCCESS no equivale a runtime correcto.

Cerrar incidentes significativos con `ERROR → RCA → FIX → GUARD → TEST → EVIDENCIA`;
dejar defensa permanente cuando sea técnicamente posible y explicar cualquier
límite. Registrar sólo el delta útil: cambio, SHA/PR, pruebas y recibos.
`NO_VERIFICADO` **no se cierra ni se deja indefinido**: adjuntar owner, causa,
prueba pendiente, mecanismo para ejecutarla y plazo o próxima rueda válida;
`BLOQUEADO` exige causa y corrección. Nunca cambiar el estado a validado sin
medición. Liberar ownership al terminar.

## Incidente P0 PPI REST — corte 18-Nov-2026

El [checkpoint #1093/6992170026](https://github.com/mbalbo2023/Porota-trading/issues/1093#issuecomment-6992170026)
registra #1192 MERGED y Predeploy V2 validado, pero Deploy V2 #44376376095
FAILED en POSTFLIGHT (`RUNTIME_UNCLASSIFIED_DEGRADED`) y Current/Book frescos
NO_VERIFICADOS. El parche de `scripts/porota_rootless_host.py` se preservó como
diff local **no integrado**. Confirmar `observer.detail` físico antes de aceptar
la hipótesis de ciclo 0/0; AMBER del contenedor no da READY a instrumentos.
La API PPI existente usa REST; no imponer el supuesto límite de 2 GET/h.
La inspección READ_ONLY #44408163645 halló `CURRENT_IDENTITY_UNVERIFIED` y
contenedores sin atribución al release; Market Watch #44405692829 halló
`broker_requests=0`, heartbeat antiguo y `0/17283` READY. El parche previo
`4ef52f97`, recuperado localmente con 51 tests unitarios PASS, **no está en
GitHub ni desplegado**; no resuelve por sí solo hijos/controlador. Separar RCA
físico del fallo, único fix-forward y deploy seguro de QA Current/Book en próxima
rueda. Consultar #980 para ownership actual y #1093 para la evidencia; este
corte no prueba estado presente del runtime.

## Actualización verificada del P0 — 18-Nov-2026, posterior al corte

El corte anterior se conserva como RCA histórico, **no como estado vigente**.
El [PR de producto #1196](https://github.com/mbalbo2023/Porota-trading/pull/1196)
quedó MERGED, Predeploy V2 [#44440710070](https://github.com/mbalbo2023/Porota-trading/actions/runs/44440710070)
probó el artefacto y el único Promote V2 [#44442769638](https://github.com/mbalbo2023/Porota-trading/actions/runs/44442769638)
produjo el recibo físico #14492700787: `state=VALIDADO_RUNTIME`, candidato
`b3cbe57a5b4b82016a65a0e253b251abbb84a04c`, tree
`7370fea6c9c6daa802fcdd8ce6fc20c5cf8e31b0`, imagen
`sha256:62c649ac94c586f488c516f53a030b75529edd7529425254ff33b2a70bc4008d`,
controlador `bv_paper_runtime.py` READY con ocho hijos declarados, observador
running, `PRODUCTION_PAPER/SIMULATION`, rutas reales BLOCKED y
`real_orders_sent=0`. El PR de control [#1197](https://github.com/mbalbo2023/Porota-trading/pull/1197)
se integró a `main` (merge `6ce8db104de923c6915650c146f9460df5dee1b4`)
para encolar diagnóstico en **la misma sesión PPI autenticada**; el modo que
podía abrir un segundo login quedó deshabilitado. Ambos leases liberados en #980.

El recibo del deploy **no acredita** Current/Book frescos, READY por familia ni
completitud histórica. `PPI` es una API HTTP/REST existente, no un cambio
injustificado de proveedor o protocolo. PPI identifica; IOL, BYMA, A3 y ROFEX
aportan sólo campos complementarios probados, sin falsificar identidad.
Históricos requieren RCA propio: `rc6_source_cycle_dispatch.py::_paper_zero`
mantiene HOLD de identidad SQLite/WAL y el scanner exige el cutoff
`2026-09-21` COMPLETE, no acreditado. No habilitar writers ni inventar datos
para eliminar ese HOLD. Registrar owner, causa, defensa y prueba física próxima
en [#1093](https://github.com/mbalbo2023/Porota-trading/issues/1093).
