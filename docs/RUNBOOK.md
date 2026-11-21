# Porota RC6 — procedimiento de trabajo y release


**Actualización de continuidad 20-Nov-2026 22:25 ART (21-Nov UTC):** el punto de entrada de release es la [ADENDA PRIORITARIA A678 y camino G0–G8 del handoff existente](RC6_20261119_GATEWAY_RELEASE_BLOCKERS_HANDOFF.md), luego últimos comentarios de [#1093](https://github.com/mbalbo2023/Porota-trading/issues/1093) y ownership [#980](https://github.com/mbalbo2023/Porota-trading/issues/980). [Predeploy #44849786845](https://github.com/mbalbo2023/Porota-trading/actions/runs/44849786845) del SHA 20c91228 de #1213 **FAILED** en la primera lectura AF_UNIX A678 (AssertionError, línea813, OOM0), sin artifact. No es una nueva autorización de promover un SHA rojo; usar el MISMO #1213 y owner existente para RCA/fix-forward. No abrir otro runner, Gateway, reparador ni PR producto. Documentos anteriores al corte mantienen evidencia histórica, no estado vivo.



Este procedimiento aplica al alcance autorizado por el usuario. Las reglas
estables están en [AGENTS.md](../AGENTS.md) y la
[política estructurada](../ops/policy/porota-policy.yaml). El historial sirve para
investigar un problema concreto; sus checkpoints, aprobaciones y pendientes no
se convierten en puertas automáticas de una tarea nueva.

**Continuidad actual:** [handoff del 19-Nov-2026](RC6_HANDOFF_2026-11-19.md)
y último comentario de [#1093](https://github.com/mbalbo2023/Porota-trading/issues/1093).
Incluye el censo físico A385 de fuentes STALE y el inventario A729 con WAL
no reconciliado, posteriores al corte anterior. No reejecutar el deploy P0
ya validado: corresponde QA de rueda e históricos con backup/owner independiente.

## Incidente P0 PPI REST — corte 18-Nov-2026

Partir del [checkpoint #1093/6992170026](https://github.com/mbalbo2023/Porota-trading/issues/1093#issuecomment-6992170026)
y ownership actual [#980](https://github.com/mbalbo2023/Porota-trading/issues/980),
no de un resumen de chat. #1192 MERGED y Predeploy #44373257344 SUCCESS;
Deploy V2 #44376376095 FAILED en POSTFLIGHT `RUNTIME_UNCLASSIFIED_DEGRADED`.
La auditoría READ_ONLY [Market Watch #44405692829](https://github.com/mbalbo2023/Porota-trading/actions/runs/44405692829)
midió `broker_requests=0`, heartbeat vencido, `0/17283` READY y fallo de
provenance/identidad. [Rootless inspect #44408163645](https://github.com/mbalbo2023/Porota-trading/actions/runs/44408163645)
mostró daemon activo, `CURRENT_IDENTITY_UNVERIFIED`, contenedores privados sin
atribución al release y journal de Deploy fallido. Parche local `4ef52f97`
recuperado/testeado (51 unit tests PASS), **aún no integrado**; hipótesis de
basket 0/0 sin `observer_state.detail` causal medido. **HOLD sólo mientras
identidad, controlador, guards PAPER/provenance o ownership bloqueen**; mercado
cerrado por sí solo no impide deploy técnico seguro.

Camino crítico: (1) Action READ_ONLY para correlacionar firma POSTFLIGHT con
`observer.detail`; (2) verificar el parche contra HEAD y RED→GREEN sin abrir
otros DEGRADED; (3) candidato único bajo PR y WRITE_OWNER; (4) Predeploy V2 y
artefacto/digest; (5) un Promote V2 PAPER bajo DEPLOY_OWNER; (6) evidencia física
posdeploy de `real_orders_sent=0`, identidad rootless, Current/Book nativos,
frescura y `READY/total` por diez familias en la próxima rueda válida si hoy
está cerrada; (7) QA separado de motores/workers, timers, históricos y DB. AMBER de proceso y login OK no dan readiness; nunca
imponer un límite inventado de 2 GET/h. PPI Watch no tiene veto general por
regla del usuario: cualquier intervención sigue ownership y Actions.

### Actualización física posterior, 18-Nov-2026

El fallo anterior permanece documentado como **RCA histórico**; el único
fix-forward de producto pasó [Predeploy #44440710070](https://github.com/mbalbo2023/Porota-trading/actions/runs/44440710070)
y [Deploy V2 #44442769638](https://github.com/mbalbo2023/Porota-trading/actions/runs/44442769638),
artefacto físico #14492700787 `VALIDADO_RUNTIME`. Fuente
`b3cbe57a5b4b82016a65a0e253b251abbb84a04c`, árbol
`7370fea6c9c6daa802fcdd8ce6fc20c5cf8e31b0`, imagen
`sha256:62c649ac94c586f488c516f53a030b75529edd7529425254ff33b2a70bc4008d`,
controlador `bv_paper_runtime.py` READY, ocho hijos declarados, observador
running, órdenes reales BLOCKED y `real_orders_sent=0`. El PR producto #1196
está MERGED; el PR de control #1197 está MERGED en `main` con CI y ownership
verdes, usando cola del scanner PPI ya logueado en vez de segundo login.
Ambos owners fueron liberados en #980. No volver a desplegar el mismo código
por el mero hecho de cerrar documentación.

**Plan para históricos/complementos:** el RCA #1093/6997492857 identifica dos
guards distintos: `scripts/rc6_source_cycle_dispatch.py::_paper_zero` termina
siempre en `PAPER_DATABASE_IDENTITY_UNVERIFIED` pese a una fila PAPER/0;
`bf_production_paper_observer._history_cutoff_repair_complete` exige una fila
`COMPLETE` al cutoff `2026-09-21`, mientras el último recibo fue `PARTIAL`.
No hacer `ALTER TABLE`, importar RAW, simular un heartbeat ni ejecutar un writer
histórico del host sólo para pasar un semáforo. Recuperar primero scripts,
workflows y PRs de reparación previos. Luego:

1. Action READ_ONLY: identidad del mismo DB físico con FD sin symlinks, inode,
   dispositivo, journal/WAL, schema, `quick_check` y relación con el runtime;
   publicar hash/estado sanitizado, nunca DB ni registros financieros.
2. WRITE_OWNER separado: recuperación idempotente de esquema/archivo por serie
   de cinco claves, locks y lease de writer exclusivos; PPI autenticado del
   scanner y complementos externos sólo si sus campos conservan procedencia.
3. Regresión OFFHOST de doble inicio, crash/WAL, reintento, faltantes y rechazo
   de identidad ambigua; preflight/pipeline de la misma imagen antes de admitir
   un cambio al runtime. Host únicamente mediante GitHub Actions.
4. Recibo físico posterior con conteos de importación, cobertura por familia,
   timestamps y motivos de exclusión. Sólo marcar `COMPLETE`/READY cuando los
   registros correspondientes lo prueben; lo pendiente conserva owner, RCA,
   acción y próxima ventana de rueda.

La frescura del siguiente día **no altera** el recibo Deploy V2 ya acreditado.
No ejecutar un workflow de mercado fuera de su calendario ni fabricar datos
para que un instrumento figure READY. El único PR P1 abierto al corte es #1166
(Performance; DRAFT/RETENER_CON_CAUSA, no merge ni deploy).

## 1. Retomar una tarea

1. Identificar objetivo, criterio de éxito, componentes afectados y exclusiones.
2. Consultar HEAD de las ramas relevantes, candidato, runs y ownership vigentes.
   `main` es plano de control; producto es la rama protegida RC6 que indiquen las
   referencias actuales. Ninguna de ellas prueba por sí sola el runtime.
3. **Antes de escribir una línea nueva de código**, buscar la solución previa en
   `HEAD`, branches, PRs abiertos y cerrados, commits, issues, tests, workflows y
   parches documentados/locales. Registrar referencias y estado de cada candidato;
   recuperar y terminar el parche parcial vigente primero. Si es inservible,
   explicar con evidencia por qué, antes de crear otro PR.
4. Revisar **todos los PR abiertos** y asignar owner, scope, pruebas y destino:
   `INTEGRAR`, `COMPLETAR`, `RETENER_CON_CAUSA`, `SUPERSEDIDO_CON_REFERENCIA`
   o `CERRAR_CON_EVIDENCIA`, con siguiente paso y fecha; sin PR huérfanos.
5. Declarar `READ_ONLY` o registrar `WRITE_OWNER` con rama, SHA base y scope.
   Resolver sólo los solapamientos reales; paralelizar el trabajo independiente.

La orden del usuario autoriza su alcance. Continuar sin permisos repetidos por
micropasos ni rondas ficticias de unanimidad. Una decisión que cambie alcance,
acceso o efecto operativo requiere tratar ese cambio concreto, no reiniciar el
proceso entero.

Si una operación ya está disponible en Actions, ejecutarla por su workflow; no crear una rama o PR para dispararla. Las ramas aíslan cambios de código, que se integran en el candidato vigente.

## 2. Cambiar y comprobar

| Cambio | Comprobación suficiente según impacto |
|---|---|
| Documentación | Enlaces, referencias y coherencia; sin imagen ni deploy |
| Política/CI | Parseo y contratos de los consumidores modificados; caminos de aceptación y rechazo |
| Código funcional | Pruebas del comportamiento y sus consumidores; integración afectada |
| Dependencias/imagen/runtime | Instalación, imports, arranque y funciones afectadas dentro de la imagen final |
| Seguridad/datos/promoción | Regresión del riesgo concreto, aislamiento, identidad y transición afectada |

Registrar qué se ejecutó y por qué. Repetir una suite sólo cuando un cambio
invalide su evidencia o cuando la prueba sea necesaria para el contrato vigente.
Un fallo acotado pide causa, corrección y regresión pertinente; no una sucesión
de builds y suites completas. Distinguir fallo del producto, prueba obsoleta,
entorno y falta de evidencia. No borrar una regresión útil para obtener verde.

## 3. Un candidato, dos workflows de release

| Workflow | Responsabilidad | Evidencia |
|---|---|---|
| [Predeploy V2](https://github.com/mbalbo2023/Porota-trading/blob/integration/rc6-a1115-r87-unified-paper-candidate-20261110/.github/workflows/porota-predeploy-v2.yml) | Clasificar impacto; construir una vez; probar código y comportamiento funcional en la misma imagen; empaquetar | SHA, imagen/digest, manifest, resultados y artefacto inmutable |
| [Promote V2](https://github.com/mbalbo2023/Porota-trading/blob/integration/rc6-a1115-r87-unified-paper-candidate-20261110/.github/workflows/porota-deploy-v2-promote.yml) | Verificar el paquete admitido; comprobar precondiciones; promover; validar runtime y limpiar lo propio | Run, identidad instalada, salud, PAPER/0 medido, limpieza o retención justificada |

Los frentes de desarrollo se integran en el candidato vigente. Antes de generar
el release, revisar su diff, pruebas y dependencias y fijar el SHA final.
No reconstruir en el Droplet ni cambiar el contenido después de probarlo.
El manifest debe derivarse del checkout y describir el paquete real.

Las pruebas funcionales deben ejercer el recorrido afectado con datos
controlados y órdenes reales bloqueadas. Un import o un mock aislado no acredita
ese recorrido. Código y pruebas funcionales identifican la misma imagen;
Promote verifica esa identidad antes de usarla.

Las pruebas históricas de host, COLD, ROOT o cadenas A/B/C/D/E sólo son exigibles
si el contrato actual y el cambio las necesitan. Preservar su evidencia y los
riesgos que detectaban; retirar mecanismos sustituidos mediante revisión y
regresiones equivalentes. La documentación no acredita por sí sola ese retiro.

## 4. Preparación rootless separada

El bootstrap prepara el daemon y las rutas rootless exclusivos de Porota bajo
la cuenta SSH destinada a operarlo, su transporte de Actions y configuración
mínima. No se ejecuta por cada release. Su existencia e identidad se verifican
por recibos; escribir una guía o fusionar código no prueba preparación física.

La promoción usa el entorno ya preparado, con destino de Docker explícito y
recursos identificados como Porota. No recurrir al socket del daemon global ni
instalar permisos elevados para hacer pasar un release. La configuración de
acceso y cualquier migración inicial de datos se realizan en su scope separado
por Actions, con privilegios acotados cuando sean necesarios; las credenciales
nunca van en el repo o en sus recibos públicos.

Los servicios ajenos identificados, sus volúmenes y datos permanecen fuera del
scope. El nombre histórico «PPI Watch» no demuestra por sí solo un consumidor
externo ni una reserva de cuota PPI; exigir evidencia de identidad y tráfico
antes de convertirlo en dependencia. Comprobar la coexistencia real antes de
sustituir el runtime de Porota.

## 5. Promover y verificar

1. Reconciliar el release autorizado y tomar `DEPLOY_OWNER` antes del primer
   cambio host. Mantenerlo hasta terminar la verificación y limpieza.
2. Conservar `rc6-unified-paper-deploy` con `cancel-in-progress: false`.
3. Preflight: verificar SHA/artefacto/digest, destino rootless, propiedad de
   recursos, capacidad disponible, configuración y datos necesarios. Comprobar
   lo que debe existir antes de instalar; no exigir el futuro contenedor activo.
4. Transferir/cargar el paquete admitido, promover una vez y registrar qué se
   modificó. Un fallo debe indicar si dejó el runtime intacto o requiere una
   corrección hacia adelante; no efectuar rollback.
5. Validar después del arranque: imagen y procesos esperados, observer/dashboard,
   salud, DB activa cuando aplique, modo PAPER, rutas reales bloqueadas y
   `real_orders_sent=0` con evidencia actual. Probar también el comportamiento
   afectado que no podía observarse offhost.

Mercado cerrado o proveedor externo degradado no bloquean por sí solos una
promoción técnica 24×7. Cada instrumento conserva su admisión fail-closed.
**Gates técnicos indispensables**: manifest/digest y SHA del mismo artefacto,
`current.json`/receipt que acrediten qué contenedor y qué controlador gobiernan
el runtime, hijos y health reales, PAPER y rutas reales bloqueadas con
`real_orders_sent=0`. La prueba de frescura de Current/Book que requiere datos
de rueda se agenda por instrumento para la **siguiente sesión real**, con owner,
run y plazo; hasta entonces `NO_VERIFICADO` no significa `READY` ni cierre.
Una discrepancia de identidad, artefacto, datos o seguridad bloquea fix-forward.

## 6. Datos y limpieza

No reinterpretar ni borrar datos para superar un gate. Identidades ambiguas,
moneda, procedencia y relojes causales deben conservarse. Una migración requiere
su contrato y comprobación propios; este runbook no habilita purgas, reingestas
masivas, VACUUM, cambios de backups ni activación de timers.

Después de cada intento, retirar sólo staging e imágenes/cache/contenedores
inequívocamente Porota y no referenciados, cuando sea seguro. Verificar propiedad
y referencias antes de borrar; medir espacio antes/después. Prohibidos prune
global, force, volúmenes/DB y cualquier recurso ajeno. Si algo debe conservarse,
registrar identidad y motivo. El espacio libre no demuestra que hubo limpieza.

## 7. Evidencia, incidentes y cierre

Registrar por separado código SHA/PR, run de Actions, manifest/digest, instalación
y comportamiento. `ARTEFACTO_VALIDADO`, `DESPLEGADO` y `VALIDADO_RUNTIME` exigen
su evidencia correspondiente; lo ausente queda `NO_VERIFICADO`. Un SUCCESS de
workflow, una instalación o cero errores de import no sustituyen las otras capas.

Para un incidente significativo: síntoma verificable → RCA → corrección mínima →
defensa permanente → prueba pertinente → enlace de evidencia. Si no puede
automatizarse la defensa, explicar el límite sin inventar otro control.

Cerrar con un delta breve en la fuente de continuidad vigente: resultado,
SHA/PR/run/artefacto/recibo aplicables, pruebas reutilizadas o nuevas, incertidumbre
material y próximo paso. Liberar ownership y preservar trabajo en Git antes de
cambiar de chat. No acumular nuevos runbooks ni duplicar ese estado en archivos.
