# Porota Trading RC6 — instrucciones para agentes

## Migración a IOL REST

- IOL REST y su stream de negocios son la fuente operativa de mercado del
  release PAPER. El observer recibe las credenciales de mercado de IOL por un
  archivo privado de solo lectura.
- El comparador #1290 sigue limitado a testing por su marcador. La revisión
  de release usa Predeploy V2 y Promote V2. El replay posdeploy usa el digest
  instalado en un contenedor sin red ni datos de la rueda.
- Acreditar GO, READY, apertura y salida con evidencia independiente por familia.
  Registrar el primer fallo, el RCA y los avances en la
  [matriz de migración](docs/auditoria-rc6/MATRIZ_MIGRACION_RUEDA.md).

Este es el único punto de entrada de trabajo. El procedimiento está en
[docs/RUNBOOK.md](docs/RUNBOOK.md); los documentos retirados se consultan desde
[el índice histórico](docs/history/README.md). La documentación describe reglas;
el código, Actions y los recibos prueban lo que realmente existe o se ejecutó.

## Alcance y autonomía

- Ejecutar la tarea autorizada hasta su resultado verificable. La instrucción
  actual del usuario prevalece sobre planes y procedimientos anteriores.
- No pedir de nuevo autorización dentro de ese alcance, ni imponer micropasos,
  rondas de personajes, unanimidad ficticia o porcentajes de una misión antigua.
- Revisar HEAD, componentes afectados, ownership y evidencia pertinente. Reusar
  trabajo y pruebas válidas. Consultar antecedentes cuando resuelvan una duda
  concreta; no releer todo el historial ni reabrir trabajos cerrados por rutina.
- Una limitación real de acceso, una escritura solapada o una ampliación del
  alcance se informa con su causa y el siguiente paso concreto. No inventar
  permisos, controles satisfechos ni restricciones comerciales.
- Paralelizar tareas independientes y revisar cambios según su riesgo. La
  revisión real no se sustituye por un checklist o un número de agentes.

## Seguridad permanente

- PAPER/SHADOW ONLY; modo esperado `PRODUCTION_PAPER / SIMULATION`.
- Órdenes reales bloqueadas. `real_orders_sent=0` es un requisito; sólo declararlo
  medido con evidencia del runtime correspondiente.
- PPI Watch y servicios ajenos quedan fuera del alcance, salvo autorización
  explícita del usuario para un cambio concreto.
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
  técnico; bloquea la admisión del instrumento afectado. Corrupción de identidad,
  datos, artefacto o seguridad PAPER sí bloquea la operación correspondiente.
- Limpiar sólo recursos de Porota identificados y no referenciados, con medida
  antes/después. No hacer prune global, borrar DB/volúmenes o tocar imágenes
  referenciadas. Registrar lo retenido y su motivo sin inventar una nueva barrera.

## Datos y resultados

- Conservar la identidad y procedencia del catálogo y del histórico. El scanner
  IOL recertifica en rueda ficha, moneda, plazo, negocio y libro por clave
  exacta. Los contratos especializados requieren fuentes y recibos acreditados
  por familia. Una clave ambigua queda en HOLD.
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
límite. Registrar sólo el delta útil: cambio, SHA/PR, pruebas, recibo o
`NO_VERIFICADO`, pendiente y siguiente acción. Liberar ownership al terminar.
