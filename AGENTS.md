# Porota Trading RC6 — instrucciones para agentes

## Continuidad de la rueda PAPER del 27/11

- Retomar desde el [checkpoint y handoff vigente](docs/auditoria-rc6/HANDOFF_RUEDA_IOL_2026-11-27.md).
  Consultar la [matriz](docs/auditoria-rc6/MATRIZ_MIGRACION_RUEDA.md) y el
  [RCA y lecciones](docs/auditoria-rc6/MIGRACION_IOL_OFFHOST.md).
  Reconciliar HEAD, ownership, SHA de producto y recibos físicos. La revisión
  de release IOL REST está integrada en [#1291](https://github.com/mbalbo2023/Porota-trading/pull/1291).
  El [Promote V2 #214](https://github.com/mbalbo2023/Porota-trading/actions/runs/45921311817/artifacts/15165265389)
  dejó el digest de #1292 VALIDADO_RUNTIME en PAPER/SIMULATION. El checkpoint
  registra la evidencia exacta del replay, el corte SHADOW, el estado de
  rueda y la validación visual de ese release.
- El objetivo del reemplazo es IOL REST primario y cero accesos operativos de
  RC6 a la API de PPI. Retirar PPI también como alternativa. Preservar bases,
  versiones y procedencia históricas, autoridades especializadas acreditadas y
  la operación independiente de PPI Watch.
- Después de instalar el candidato autorizado, repetir en el Droplet el
  backtesting integral con el digest instalado, datos de replay aislados,
  todas las familias PAPER, los workers, timers y servicios efectivos.
  Comprobar infraestructura y dashboard real con Playwright. Cerrar con los
  recibos de runtime y `real_orders_sent=0`.
- La evidencia de rueda exige identidad exacta, reloj IOL del último trade,
  libro vigente y GO efectivo. El scanner IOL mantiene el HOLD explícito de
  entrada hasta contar con un puente al recibo de rueda propio del proveedor.
  Las pruebas sintéticas validan el motor con tres claves por familia y la
  base operativa conserva sus guardas.
- Priorizar GO de IOL desde preopen hasta el broker, calentamiento COLD →
  WARM → HOT con fuente efectiva y corrección de la discrepancia de tres bonos
  en el reporte de candidatos. Exigir paridad por clave entre guard, ledger y
  proyección. Conservar bloqueos por variante hasta acreditar la precondición
  específica. Registrar la vigencia del reporte y la causa en la UI.
- Para el archivado diario, medir espacio libre y reservar 2 GiB, con
  margen por lote. Confirmar en el recibo el cursor frío, el techo caliente,
  el último recibo durable y la procedencia histórica. Evitar límites fijos
  de crecimiento para el archivo acumulativo.
- En el dashboard real, comprobar el clic desde todos los menús, las vistas
  activas, los detalles y M0–M11. Cada estado sin evidencia debe mostrar el
  dato o recibo concreto que falta. El auditor estricto registra el motivo
  visible por campo, la interacción y la identidad de release. El href
  conserva filtros explícitos y los de la vista. Comparar la URL completa de
  mismo origen y comprobar la proyección y el documento recibido por el clic.
  Reusar el [auditor corregido en 42d4843](https://github.com/mbalbo2023/Porota-trading/commit/42d4843aa92951ca21cd19ad9dbbf9c486167b57).

## Aislamiento obligatorio del comparador #1287

- Conservar [#1287](https://github.com/mbalbo2023/Porota-trading/pull/1287) y sus recibos como comparador.
  La rama `fix/rc6-iol-primary-wiring-20261126` preserva el cableado de prueba
  acreditado en el [checkpoint](docs/auditoria-rc6/CHECKPOINT_IOL_PRIMARIO_2026-11-26.md).
  El perfil de pruebas `POROTA_RC6_MARKET_PROVIDER=IOL_REST` se ejerce en Actions
  hospedado, con red deshabilitada, fixtures y SQLite temporal.
- El marcador `ops/policy/rc6-iol-testing-only.json` veta Predeploy V2, el
  paquete de release y Promote V2. La publicación se limita a evidencia de
  pruebas. La autorización del titular cubre la promoción única de la revisión
  de release de este corte. Esa revisión exige el control de promoción vigente,
  los cambios de host y motores que falten, y evidencia de alcance y rueda por
  clave. El candidato de pruebas conserva su marcador y su historial.
- Regresar por Fix-Forward a partir del primer fallo con evidencia. Llevar el
  RCA, las lecciones y la matriz de viabilidad a
  [Migración IOL OFFHOST](docs/auditoria-rc6/MIGRACION_IOL_OFFHOST.md).

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

- Acreditar la autoridad de identidad del perfil activo por clave exacta y
  período de vigencia. En el corte IOL se exige identidad de IOL o de fuente
  especializada acreditada. Las identidades históricas PPI conservan su origen.
  Preservar las versiones de identidad ya publicadas y mantener HOLD en
  variantes ambiguas.
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
