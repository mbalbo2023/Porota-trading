# Porota Trading RC6 — instrucciones para agentes

## Continuidad operativa · Deploy V2 #202

[Handoff del 23/11/2026](docs/auditoria-rc6/HANDOFF.md) para retomar RC6. Reconciliar el HEAD de producto y el último recibo físico en [#1093](https://github.com/mbalbo2023/Porota-trading/issues/1093) y consultar el ownership en [#980](https://github.com/mbalbo2023/Porota-trading/issues/980) antes de escribir. El [Deploy V2 #202](https://github.com/mbalbo2023/Porota-trading/actions/runs/45273533386/artifacts/14866098686) acredita seis variantes de BONOS/ON en `ACTIVE_PAPER` y cero órdenes reales. El handoff indica la comprobación pendiente en rueda, el [registro RCA](docs/auditoria-rc6/RCA_Y_LECCIONES.md) y la fecha de cada [insumo de auditoría](docs/auditoria-rc6/README.md). `main` es la rama de control documental.

El procedimiento está en [docs/RUNBOOK.md](docs/RUNBOOK.md). El
[índice histórico](docs/history/README.md) reúne los documentos retirados.

- Distinguir `PPI_FIELD`, `REQUEST_CANDIDATE`, respuesta nativa vinculada a una consulta y readiness de la clave completa. Ante un literal explícito preservar el valor observado y normalizar su significado. La ausencia de eco de plazo tiene el estado `ABSENT` en una respuesta nativa válida.
- Registrar cantidad instalada, activa, deshabilitada y enmascarada de timers por separado. Para el estado de un job usar su unidad `Unit=`, evidencia de ejecución y horario ART/UTC.
- Las simulaciones se ejecutan con proveedor y almacén aislados; el testigo sintético acredita la lógica temporal, los cálculos y las transacciones de prueba. La habilitación PAPER nativa se audita con sus fuentes, autoridad independiente, riesgo y release específico.
- Para la salida PAPER, verificar por separado la política y el firmante activos, el libro nativo estrictamente posterior al ACK y el asiento contable. Si una venta pendiente lleva el ACK de una clave revocada, conservar ese ACK y rearmar con otro libro.

## Antecedentes del 19 al 21 de noviembre

> **Fix-forward histórico #1203, 21-Nov-2026.** El [head `8b73250a`](https://github.com/mbalbo2023/Porota-trading/commit/8b73250af5d121a53e3ba3306f5c0873bbdf3e48) incluye el 21/11/2025 como rueda histórica T+1 para ACCIONES/CEDEARs y admite fragmentos de archivo dentro del corte aun si terminan antes del 21/09/2026. Pasaron 121 pruebas focalizadas. Quedan tres modalidades limitadas pendientes, el inventario por clave y rueda sobre las copias restauradas y el lote acreditado por el proveedor. #1203 conserva DRAFT y la importación HOLD. [Pruebas, piloto y alcance](docs/RC6_20261119_GATEWAY_RELEASE_BLOCKERS_HANDOFF.md#fix-forward-de-1203--21-nov-2026).

> **Antecedente de #1234 e histórico, 21-Nov-2026 16:09:52 ART.** [Promote V2 #193](https://github.com/mbalbo2023/Porota-trading/actions/runs/44938720827) instaló y validó el código `788b00ed9379c7517717a832fff3c5ecc2a87bee` en PAPER/SHADOW, producto `e0cf4944f7f0b780ce8712b1679fa113527a1aca`, con la imagen verificada, DB en PAPER/0 y guard REAL bloqueado. El scanner desacopla la rueda actual del cutoff y registra `HISTORY_PROVENANCE_UNVERIFIED` si falta el contrato PPI acreditado. IOL registra negociación en las cuatro fechas auditadas. [RCA, procedencia, restauraciones y verificación](docs/RC6_20261119_GATEWAY_RELEASE_BLOCKERS_HANDOFF.md#ejecución-de-1234-y-evidencia-de-histórico--21-nov-2026). #1203 sigue en HOLD para importar.

> **Antecedente de cierres diarios y reparación histórica, 21-Nov-2026.** [#1234](https://github.com/mbalbo2023/Porota-trading/pull/1234) estaba DRAFT en ese corte. Separa la rueda vigente del cutoff anual. Reutiliza el Gateway, candado y recibo del scanner. Sólo puede certificar velas con procedencia admitida. [A729 #44933422745](https://github.com/mbalbo2023/Porota-trading/actions/runs/44933422745/artifacts/14718885522) verificó 55/55 cuerpos de la muestra. En 0/55 todos los registros del cuerpo declararon moneda y en 0/55 todos declararon base RAW sin ajustes. [#1203](https://github.com/mbalbo2023/Porota-trading/pull/1203) incluye una regresión para que un cierre posterior no vuelva invisible el archivo del corte. Importación HOLD. Consultar [RCA y comprobaciones](docs/RC6_20261119_GATEWAY_RELEASE_BLOCKERS_HANDOFF.md#cierres-nuevos-y-huecos-del-corte--21-nov-2026) antes de tocar writers o disparar un nuevo login.

> **Corte previo de #1224, 21-Nov-2026 09:36:35 ART.** [#1224](https://github.com/mbalbo2023/Porota-trading/pull/1224) corrige `PPI_IDENTITY_TICKER_INVALID` antes del login. [CI exacto](https://github.com/mbalbo2023/Porota-trading/actions/runs/44903322289) con 8.903/8.903 y [único Promote #192](https://github.com/mbalbo2023/Porota-trading/actions/runs/44904243279) `VALIDADO_RUNTIME`. Producto `6209ddc80447490a3eea5fc49e9366f6549d6e14`, árbol `54618bc017cfea0167c3e94e7a460631ad4543b5`. La [lectura posterior](https://github.com/mbalbo2023/Porota-trading/actions/runs/44904731140) confirmó `ppi_auth=OK` desde 09:33:34 ART, un intento de login del dueño local y tres roles registrados, comprobados por AF_UNIX/SO_PEERCRED. La misma DB se mantuvo en PAPER/0 con guard REAL bloqueado. Quedan 0/17.351 READY fuera de rueda y 0 plazos atestados por PPI. La admisión requiere identidad completa y evidencia genuina por instrumento y sesión.
>
> **Históricos #1203/A729, 21-Nov-2026.** El [ensayo de integridad A729](https://github.com/mbalbo2023/Porota-trading/actions/runs/44921898863/artifacts/14714405961) cotejó 55/55 candidatos EXTERNAL_EXACT en una muestra de 64 filas. [HISTORY](https://github.com/mbalbo2023/Porota-trading/actions/runs/44917242971/artifacts/14712312713) y [OBSERVER](https://github.com/mbalbo2023/Porota-trading/actions/runs/44918906718/artifacts/14712807581) pasaron respaldo online y restauración privada con digest idéntico por base, `quick_check=ok` y PAPER/0. El [cambio de #1203](https://github.com/mbalbo2023/Porota-trading/pull/1203#issuecomment-7065351639) conecta las 245 fechas de negociación BYMA auditadas para ACCIONES/CEDEARs. Mantener DRAFT/HOLD de importación hasta acreditar la procedencia económica en PPI y la modalidad exacta de las ruedas limitadas. Conservá los owners separados. [Handoff](docs/RC6_20261119_GATEWAY_RELEASE_BLOCKERS_HANDOFF.md#evidencia-histórica-y-custodia--21-nov-2026).


> **Corte anterior de #1213, 21-Nov-2026.** Leer el [handoff canónico](docs/RC6_20261119_GATEWAY_RELEASE_BLOCKERS_HANDOFF.md) y reconciliar con [#1093](https://github.com/mbalbo2023/Porota-trading/issues/1093) y [#980](https://github.com/mbalbo2023/Porota-trading/issues/980). A678 quedó resuelto en #1213. [Promote V2 #191](https://github.com/mbalbo2023/Porota-trading/actions/runs/44865544338) certificó esa imagen PAPER/SHADOW con PAPER/0 y guard REAL bloqueado.
>
> **Antecedente del P0.** [Recibo de #1213 del 21-Nov 08:40 ART](https://github.com/mbalbo2023/Porota-trading/actions/runs/44900242340) registra `ppi_auth=ERROR`, `BOOT_PPI_GATEWAY_IDENTITY`, PAPER/0 y 0/17.351 READY. El constructor fallaba antes de abrir el socket y del login. La recuperación posterior de #1224 está acreditada arriba.
>
> **Custodia histórica.** #1203 ya aportó sus dos archivos a #1213. Conservá el PR como evidencia y gestioná cualquier ensayo en el Droplet por GitHub Actions. Usá la custodia SQLite existente en ambos destinos, con una prueba de restauración por base, y verificá su vigencia para un futuro one-shot idempotente. #1216 incorporado en el release, #1166 Performance OFF y PPI Watch protegido.

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


> **Corte histórico del 20-Nov-2026, Gateway/familias.** Ver [handoff de ese corte](docs/RC6_20261119_GATEWAY_RELEASE_BLOCKERS_HANDOFF.md), [#1093](https://github.com/mbalbo2023/Porota-trading/issues/1093) y [#980](https://github.com/mbalbo2023/Porota-trading/issues/980).
>
> Producto `63ba3de6` sigue siendo último código productivo observado; **candidato único [#1213](https://github.com/mbalbo2023/Porota-trading/pull/1213) HEAD `36740582`**, Predeploy [#44783447120](https://github.com/mbalbo2023/Porota-trading/actions/runs/44783447120) **EN_EJECUCION al corte**, build completo, suite en curso, sin artifact/digest/runtime certificado. No inferir que el run siga activo ni que terminó GREEN: releer el job.
>
> **Disposición de PRs:** #1216 ETF DRAFT ya incorporado byte-exacto a #1213, **SUPERSEDIDO_CON_REFERENCIA**, NO re-mergear; #1203 históricos DRAFT/HOLD con guardas de código, fuente/custodia/contrato físico pendiente, **COMPLETAR aparte**; #1166 Performance DRAFT **FUERA de release, mantener OFF**. No convertir DRAFT en READY para fabricar un Predeploy o ejecutar otra imagen.
>
> **P0 actuales:** B02 dueño global PPI/legacy no atestado; B03 EXIT ante GET en vuelo sin plazo físico garantizado; B04 A670 aún exige testigo independiente; B05 all-GET y rotación observe-only diez familias en GitHub, runtime sin verificar. Fuente IOL complementaria no valida Book/PIT PPI ni READY; FCI NAV/cutoff, cauciones TNA, futuros A3, opciones y VN de renta fija tienen contratos separados. PPI `SearchInstrument` documenta cuatro campos; `REQUEST_CANDIDATE` no es `PPI_FIELD`. PAPER/SHADOW, órdenes reales bloqueadas, **PPI Watch NO TOCAR** según orden actual, incluso si una nota del 18-Nov inferior dice lo contrario. Sólo Deploy V2 único con paquete exacto y postflight directo; mercado cerrado no es veto técnico per se, pero falta de datos deja HOLD por instrumento.
>
> **Regla de confianza:** alta sólo con fuente+SHA+test+recibo del nivel afirmado; ante duda `NO_VERIFICADO`, con dueño, evidencia faltante y siguiente acción. Documentar cambios en el mismo handoff/#1093, no duplicar código/PR ni repetir runs sin nueva RCA.


El [handoff post-P0 del 19-Nov-2026](docs/RC6_HANDOFF_2026-11-19.md) conserva
la evidencia de A385 sobre el export STALE y de A729 sobre la custodia SQLite.
La admisión de históricos requiere su propia evidencia de contrato y custodia.

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
- PPI Watch queda fuera del alcance de cambios salvo autorización explícita
  y concreta del usuario. Verificar identidad, tráfico y ownership al inspeccionar
  su integración. Documentar cuotas sólo con evidencia. Aplicar el mismo cuidado
  a procesos, cuentas, contenedores y datos ajenos al scope.
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
