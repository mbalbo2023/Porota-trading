# Porota Trading RC6 — instrucciones para agentes

> **RCA físico vigente, 21-Nov 09:10 ART / 12:10 UTC.** El [probe #44902182900](https://github.com/mbalbo2023/Porota-trading/actions/runs/44902182900) confirmó `CatalogIdentityError / PPI_IDENTITY_TICKER_INVALID` al construir el Gateway de #1213, previo al login. Hay 9.977 llaves candidatas, cero contratos PPI_FIELD atestados y el socket no tiene dueño disponible. [Fix #1224](https://github.com/mbalbo2023/Porota-trading/pull/1224) reutiliza la validación del wire, conserva filas y colisiones, y está sujeto a [Predeploy exacto #44902379305](https://github.com/mbalbo2023/Porota-trading/actions/runs/44902379305). Para declarar recuperación, leer el nuevo runtime, owner por SO_PEERCRED, roles, attach del scanner y PAPER/0. La admisión por instrumento requiere su propia evidencia en sesión válida.
>
> **A729 recuperó la lectura RO de HISTORY.** [Localizador #44902424557](https://github.com/mbalbo2023/Porota-trading/actions/runs/44902424557) observa las tres bases y WAL consistente del Observer. [Archivo #44902534588](https://github.com/mbalbo2023/Porota-trading/actions/runs/44902534588) confirma que el archivo legacy está vacío en HISTORY y OBSERVER. EXTERNAL_EXACT sigue como candidato. #1203 ya proveyó sus guardas al producto. La [API histórica de PPI](https://itatppi.github.io/ppi-official-api-docs/api/documentacionRest/#historical-market-data) documenta las velas sin acreditar moneda ni ajustes por fila. La importación exige procedencia auténtica, calendario de concertación del rango y respaldo restaurado del destino. [Checkpoint #1203](https://github.com/mbalbo2023/Porota-trading/pull/1203#issuecomment-7062397696).


> **Corte vigente 21-Nov-2026.** Leer el [checkpoint posterior a #1213](docs/RC6_20261119_GATEWAY_RELEASE_BLOCKERS_HANDOFF.md) y reconciliar con [#1093](https://github.com/mbalbo2023/Porota-trading/issues/1093) y [#980](https://github.com/mbalbo2023/Porota-trading/issues/980). A678 quedó resuelto en #1213. [Promote V2 #191](https://github.com/mbalbo2023/Porota-trading/actions/runs/44865544338) dejó la imagen PAPER/SHADOW `VALIDADO_RUNTIME` para producto `9e28610be4c2451cd89f0af60ec65ba19b73289b`, con PAPER/0 y tres rutas REAL bloqueadas. El ownership de ese release está liberado.
>
> **P0 en curso.** [Recibo del 21-Nov 08:40 ART / 11:40 UTC](https://github.com/mbalbo2023/Porota-trading/actions/runs/44900242340) identifica el producto instalado y registra `ppi_auth=ERROR`, `BOOT_PPI_GATEWAY_IDENTITY`, `real_orders_sent=0` y `READY=0/17351`. Distinguir attach AF_UNIX, precondición del catálogo, registro del propietario y respuesta HTTP de PPI antes de corregir. Verificar recuperación del dueño y del scanner con sus relojes en el runtime exacto. La fuente fresca y la admisión requieren evidencia por instrumento de una sesión válida.
>
> **Históricos.** #1203 ya aportó sus dos archivos a #1213; conservar su PR como evidencia. A729 tiene ownership separado y aún no acredita fuente para importar, contrato monetario/RAW ni restauración. Diagnosticar SQLite/WAL y custodia por Actions antes del único reparador acotado con respaldo e idempotencia. #1216 incorporado en el release, #1166 Performance OFF, PPI Watch fuera del trabajo de recuperación. GitHub Actions es la vía al Droplet.

> **Checkpoint previo a la promoción, 20-Nov-2026 22:25 ART / 21-Nov 01:25 UTC.** [Handoff A678](docs/RC6_20261119_GATEWAY_RELEASE_BLOCKERS_HANDOFF.md), [#1093](https://github.com/mbalbo2023/Porota-trading/issues/1093) y [#980](https://github.com/mbalbo2023/Porota-trading/issues/980). Los SHAs y estados siguientes corresponden a ese corte.
>
> **Último dato comprobado al corte:** PR #1213 abierto, fuente 20c91228293c3dba84438fd0886c08d7108a11b9, producto 63ba3de63ce74398709013982032415496d07f98, main 4c9684354e3a29b9fd6dfbb291b901e879bbbfd7. Predeploy [#44849786845](https://github.com/mbalbo2023/Porota-trading/actions/runs/44849786845) COMPLETED/FAILED, artifact 0: AssertionError test A678 AF_UNIX primera RPC pull en línea 813, OOM delta 0. Código: dispatch y _role_permitted exigen supervisor/registro/peer real, el test usa consumidor "watch" no registrado; **hipótesis de fixture obsoleto, no RCA física confirmada**. Revisar el valor devuelto en recibo privado + tests ACL existentes y todos los callers antes de corregir en MISMO #1213 bajo owner de Gateway; no bajar seguridad ni omitir prueba. Fuente de detalle/evidencia en handoff y [run](https://github.com/mbalbo2023/Porota-trading/actions/runs/44849786845).
>
> **Secuencia sin atajos:** reconciliar SHA/owner → RCA causal A678 → fix+guard en mismo candidato → Predeploy full 16 shards/JUnit 0 FAIL/ERROR/SKIP/XFAIL → artifact/manifest/digest verificados → normal merge protegido → único Promote V2 build-once por Actions con DEPLOY_OWNER exclusivo/mutex → postflight real de imagen, roles, health, DB, PAPER real_orders_sent=0, rutas REAL bloqueadas y PPI Watch intacto. No declarar VALIDADO_RUNTIME por CI o HTTP aislado. El mercado cerrado no veta Deploy técnico; READY lo decide algoritmo instalado por instrumento/fuente genuina, no GitHub. Históricos #1203 + A729 siguen separados HOLD sin importación, ETF #1216 ya integrado (no duplicar), Performance #1166 OUT/OFF.
>
> **Disciplina del agente:** verificar código anterior en HEAD/branches/PR/tests/workflows, incluyendo interfaces y TODOS los callsites, antes de escribir o añadir otra arquitectura. No inventar código, quota, dato, guard, error o DONE. Estados permitidos: PROPUESTO, DESARROLLADO, COMMITTEADO, EN_GITHUB, ARTEFACTO_VALIDADO, DESPLEGADO, VALIDADO_RUNTIME, NO_VERIFICADO y BLOQUEADO; prueba exacta por nivel. ERROR → RCA → FIX → GUARD → TEST/CI/PREFLIGHT → EVIDENCIA. Conector GitHub para código/Actions; si no expone workflow_dispatch, registrar limitación y usar GitHub UI en Work/navegador interno **sólo cuando esa capacidad esté realmente disponible**. Prohibido fingir dispatch/SSH/Droplet manual. No notificar si no cambió fase, blocker o certificación.


> **Corte posterior 20-Nov: READY sólo en runtime / SQL reparación controlada.** El algoritmo productivo instalado en el Droplet es quien debe aceptar/rechazar cada instrumento con evidencia real (identidad, sesión, fuente, cotización o NAV/TNA según familia, contrato, costos, riesgo, salida). **No** esperar que GitHub CI certifique frescura o READY de las diez familias: CI valida código, tests, artefacto/manifest; Deploy V2 certifica instalación segura; sin datos reales el instrumento permanece HOLD y se verifica en rueda válida. Detalle y camino crítico en [handoff actualizado](docs/RC6_20261119_GATEWAY_RELEASE_BLOCKERS_HANDOFF.md).
>
> **No duplicar sesión única PPI:** el candidato único [#1213](https://github.com/mbalbo2023/Porota-trading/pull/1213) ya implementa `PPISharedSessionOwner.open_once`, `ppi_gateway_lifetime_lease`, AF_UNIX con SO_PEERCRED, y un solo lector para scanner/EXIT/scalping. Estado **EN_GITHUB**, no probado en Droplet; comprobar en postflight el singleton local y documentar separadamente el aislamiento de clientes legacy. B03 EXIT en vuelo y B04 testigo A670 son evidencias técnicas pendientes por alcance, no motivo para reescribir el Gateway. El Predeploy [#44783447120](https://github.com/mbalbo2023/Porota-trading/actions/runs/44783447120) **FAILED** (`CI_RUNNER_NO_COMPLETION_RECEIPT` tras ~2.500s; fixture funcional PAPER PASS); artefacto 0, Deploy nuevo NO EJECUTADO. El owner CI ya activo debe medir fase/shard/exit y corregir sobre #1213 sin modificar guardas ni lanzar un SHA idéntico a ciegas.
>
> **Históricos SQL #1203** mantienen WRITE_OWNER separado y DRAFT/HOLD: el programa `rc6_history_cutoff_repair_once.py` y A729 ya existen. Plan por fases: custodiar HISTORY/OBSERVER/EXTERNAL_EXACT y WAL; blindar rowid/table_xinfo, rango y tamaño SQL, hashes/compression, calendario histórico 2025 y contrato por familia; TDD+DRY_RUN; sólo después one-shot acotado e idempotente con ownership/backup/PAPER0, sin segundo login. Cutoff `PARTIAL` no se convierte en COMPLETE por rellenar cifras; los faltantes de datos históricos no son automáticamente veto al Deploy técnico. #1166 Performance fuera y OFF; #1216 ETF ya incorporado a #1213 (no re-mergear).
>
> **Orden de los gates:** CI exacto GREEN + manifest/digest → owner global libre + Preflight técnico real → Deploy V2 único build-once → postflight imagen/roles/DB/PAPER0/real routes/Watch → QA de instrumentos en runtime. No pedir que el futuro proceso ya exista antes de instalarlo. **PPI Watch NO TOCAR; órdenes reales bloqueadas; FIX-FORWARD ONLY.** Una duda se etiqueta NO_VERIFICADO con dueño y prueba pendiente.


> **Corte vigente 20-Nov-2026, Gateway/familias:** leer primero [handoff actualizado](docs/RC6_20261119_GATEWAY_RELEASE_BLOCKERS_HANDOFF.md), últimos comentarios [#1093](https://github.com/mbalbo2023/Porota-trading/issues/1093) y ownership [#980](https://github.com/mbalbo2023/Porota-trading/issues/980). Este corte desplaza las afirmaciones históricas inferiores donde discrepen; sólo GitHub/Actions/runtime actualizados acreditan estados posteriores.
>
> Producto `63ba3de6` sigue siendo último código productivo observado; **candidato único [#1213](https://github.com/mbalbo2023/Porota-trading/pull/1213) HEAD `36740582`**, Predeploy [#44783447120](https://github.com/mbalbo2023/Porota-trading/actions/runs/44783447120) **EN_EJECUCION al corte**, build completo, suite en curso, sin artifact/digest/runtime certificado. No inferir que el run siga activo ni que terminó GREEN: releer el job.
>
> **Disposición de PRs:** #1216 ETF DRAFT ya incorporado byte-exacto a #1213, **SUPERSEDIDO_CON_REFERENCIA**, NO re-mergear; #1203 históricos DRAFT/HOLD con guardas de código, fuente/custodia/contrato físico pendiente, **COMPLETAR aparte**; #1166 Performance DRAFT **FUERA de release, mantener OFF**. No convertir DRAFT en READY para fabricar un Predeploy o ejecutar otra imagen.
>
> **P0 actuales:** B02 dueño global PPI/legacy no atestado; B03 EXIT ante GET en vuelo sin plazo físico garantizado; B04 A670 aún exige testigo independiente; B05 all-GET y rotación observe-only diez familias en GitHub, runtime sin verificar. Fuente IOL complementaria no valida Book/PIT PPI ni READY; FCI NAV/cutoff, cauciones TNA, futuros A3, opciones y VN de renta fija tienen contratos separados. PPI `SearchInstrument` documenta cuatro campos; `REQUEST_CANDIDATE` no es `PPI_FIELD`. PAPER/SHADOW, órdenes reales bloqueadas, **PPI Watch NO TOCAR** según orden actual, incluso si una nota del 18-Nov inferior dice lo contrario. Sólo Deploy V2 único con paquete exacto y postflight directo; mercado cerrado no es veto técnico per se, pero falta de datos deja HOLD por instrumento.
>
> **Regla de confianza:** alta sólo con fuente+SHA+test+recibo del nivel afirmado; ante duda `NO_VERIFICADO`, con dueño, evidencia faltante y siguiente acción. Documentar cambios en el mismo handoff/#1093, no duplicar código/PR ni repetir runs sin nueva RCA.


Este es el único punto de entrada de trabajo. El procedimiento está en
[docs/RUNBOOK.md](docs/RUNBOOK.md); los documentos retirados se consultan desde
[el índice histórico](docs/history/README.md). La documentación describe reglas;
el código, Actions y los recibos prueban lo que realmente existe o se ejecutó.

**Handoff más reciente:** [RC6 post-P0, 19-Nov-2026](docs/RC6_HANDOFF_2026-11-19.md).
Al continuar, leerlo junto al último comentario de [#1093](https://github.com/mbalbo2023/Porota-trading/issues/1093)
y ownership [#980](https://github.com/mbalbo2023/Porota-trading/issues/980).
El deploy P0 ya fue validado; sus ocho hijos no acreditan por sí solos
Current/Book frescos, histórico completo ni todos los timers. A385 halló un
export STALE y A729 un WAL del observador no reconciliado. No repetir el
deploy P0 ni habilitar writers históricos antes de la custodia y tests allí.

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
