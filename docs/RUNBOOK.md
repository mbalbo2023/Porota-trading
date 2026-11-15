# Porota RC6 — procedimiento de trabajo y release

Este procedimiento aplica al alcance autorizado por el usuario. Las reglas
estables están en [AGENTS.md](../AGENTS.md) y la
[política estructurada](../ops/policy/porota-policy.yaml). El historial sirve para
investigar un problema concreto; sus checkpoints, aprobaciones y pendientes no
se convierten en puertas automáticas de una tarea nueva.

## 1. Retomar una tarea

1. Identificar objetivo, criterio de éxito, componentes afectados y exclusiones.
2. Consultar HEAD de las ramas relevantes, candidato, runs y ownership vigentes.
   `main` es plano de control; producto es la rama protegida RC6 que indiquen las
   referencias actuales. Ninguna de ellas prueba por sí sola el runtime.
3. Reusar implementación y evidencia aplicables. Buscar antecedentes donde haya
   una duplicación o duda concreta; no reconstruir el proyecto en cada chat.
4. Declarar `READ_ONLY` o registrar `WRITE_OWNER` con rama, SHA base y scope.
   Resolver sólo los solapamientos reales; paralelizar el trabajo independiente.

La orden del usuario autoriza su alcance. Continuar sin permisos repetidos por
micropasos ni rondas ficticias de unanimidad. Una decisión que cambie alcance,
acceso o efecto operativo requiere tratar ese cambio concreto, no reiniciar el
proceso entero.

Si una operación ya está disponible en Actions, ejecutarla por su workflow:
no crear una rama o un PR para disparar una inspección, pausa o despliegue.
Las ramas aíslan cambios de código; los frentes paralelos se integran en un
único candidato y se retiran después de conservar sus commits.

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
| [Predeploy V2](../.github/workflows/porota-predeploy-v2.yml) | Clasificar impacto; construir una vez; probar código y comportamiento funcional en la misma imagen; empaquetar | SHA, imagen/digest, manifest, resultados y artefacto inmutable |
| [Promote V2](../.github/workflows/porota-deploy-v2-promote.yml) | Verificar el paquete admitido; comprobar precondiciones; promover; validar runtime y limpiar lo propio | Run, identidad instalada, salud, PAPER/0 medido, limpieza o retención justificada |

Los frentes de desarrollo se integran en el candidato vigente. Antes de generar
el release, revisar su diff, pruebas y dependencias y fijar el SHA final.
No reconstruir en el Droplet ni cambiar el contenido después de probarlo.
El manifest debe derivarse del checkout y describir el paquete real.

Predeploy corre al actualizar el PR. Tras fusionar el mismo árbol probado,
ejecutar Promote por `workflow_dispatch` desde la rama de producto, indicando
SHA y run de Predeploy. Usar `migrate` una sola vez para la transición inicial
rootless y `deploy` para las siguientes. Fusionar documentación o abrir un chat
no inicia automáticamente un deploy.

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

PPI Watch, otros servicios, volúmenes y datos ajenos permanecen fuera del scope.
La coexistencia debe comprobarse antes de sustituir el runtime de Porota.

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
promoción técnica segura. Cada instrumento conserva su admisión fail-closed;
las pruebas que requieren sesión real quedan `NO_VERIFICADO` hasta observarla.
Corrupción de identidad, artefacto, datos o seguridad sí bloquea la acción afectada.

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
