# Handoff RC6 — IOL API read-only canary

**Fecha:** 2026-09-14 (UTC)  
**Estado:** bloqueado antes de autenticar; ninguna petición llegó a InvertirOnline.

## Alcance y límites vigentes

- Trabajar exclusivamente sobre RC6.
- IOL se usa como fuente de observación/cross-validation de datos de mercado.
- Quedan fuera todas las órdenes, cuentas, carteras, estado de operaciones y estimaciones transaccionales.
- No volcar, copiar ni registrar usuario, contraseña, token, cookies ni respuestas de autenticación.
- No tocar PPI Watch, sus procesos, locks, timers, datos ni ejecuciones.
- No inferir readiness de un HTTP 200 ni de un payload no vacío.
- El canary autorizado está acotado a una autenticación y hasta cinco GET de datos públicos/de mercado; no es ingestión masiva.

## Estado reproducible

- Baseline RC6 reconocido por el observer: imagen `porota-trading-bot:17.0.0-rc6`.
- Rama de trabajo: `feature/rc6-iol-readonly-canary-20260914`.
- Workflow: `.github/workflows/rc6-iol-readonly-canary.yml`.
- Commit inicial del workflow: `52da96e3d3a728e53ca646460d52bb1509fd80cc`.
- Commit con preflight ampliado al `.env` RC6: `d4e0f0be64c7d93178e677fe1d13c7cc936c3c59`.
- Run [34898653992](https://github.com/mbalbo2023/Porota-trading/actions/runs/34898653992): el observer confirmó identidad RC6; no encontró credenciales en el entorno del contenedor y paró antes de la red.
- Run [34898994086](https://github.com/mbalbo2023/Porota-trading/actions/runs/34898994086): confirmó la misma identidad RC6; no encontró ninguno de los nombres permitidos en el entorno del contenedor ni en `/opt/porota-trading/.env`; paró antes de la red.
- En ambos runs: cero llamadas a IOL, cero autenticaciones, cero GET, cero órdenes y ningún valor secreto en logs o artefactos. Los artefactos contienen solo salida saneada del preflight.
- El usuario informa que las credenciales existen en el servidor y que antes se probaron correctamente. Ese dato aún no se pudo reproducir desde estas ubicaciones/nombres de variable.

## Qué hará el canary cuando se resuelva la ubicación de las credenciales

El workflow exige primero la identidad RC6 exacta y solo contempla los nombres `IOL_USERNAME`/`IOL_PASSWORD` o `IOL_API_USERNAME`/`IOL_API_PASSWORD`. Una vez localizados de forma explícita, ejecutará como máximo:

1. POST `/token` (una vez; token solo en memoria).
2. GET del panel de acciones líderes de Argentina.
3. GET de cotización de GGAL y AL30.
4. GET de series históricas ajustadas de 30 días para GGAL y AL30.

Los logs son una lista permitida de códigos HTTP, timestamps, conteos, claves de campos y unas pocas columnas de mercado; no registran cuerpos completos ni credenciales. La ejecución no valida todavía cobertura, frescura/calidad operativa, identidad canónica ni compatibilidad con el motor.

## Siguiente paso bloqueante

Indicar **solo** los nombres exactos de las variables de entorno IOL, o la ruta concreta del archivo de configuración seguro y autorizado que las contiene. No enviar valores de credenciales al chat ni a GitHub. No ampliar la búsqueda de archivos ni probar nombres/rutas por tanteo. Con ese dato se podrá ajustar el preflight para leer presencia únicamente y lanzar una sola ejecución acotada.

Si no hay una ubicación aprobada y reproducible, dejar el canary detenido y continuar en paralelo con la documentación/API ya disponible y los adaptadores offline.

## Documentación relacionada y límites de lo ya probado

- PR #64, adapter IOL read-only offline: https://github.com/mbalbo2023/Porota-trading/pull/64. El run `34888608698` valida compilación y 10 pruebas con doubles; no prueba credenciales ni conectividad real.
- PR #65, combinación offline WA-01 + WA-03: https://github.com/mbalbo2023/Porota-trading/pull/65. Continúa en borrador; no habilita el runtime ni órdenes.
- El workflow de canary está aislado en esta rama y no está desplegado en el servicio productivo.
- Mantener este handoff actualizado con los SHA de commit y los IDs de Actions de cada nueva comprobación. No reemplazar esta evidencia con conclusiones inferidas.

## Seguimiento de continuidad — PR #66

- PR borrador: https://github.com/mbalbo2023/Porota-trading/pull/66.
- Head actual al preparar este seguimiento: `8e5cf25718b79337b7db73c0bc7b253281776876`.
- Ese head añade sincronización de este handoff hacia `/opt/porota-trading/docs/handoffs/`; el paso comprueba primero la imagen RC6 y solo copia este documento.
- No marcar la copia al servidor como confirmada hasta ver `SERVER_HANDOFF_SYNC=OK` en el run correspondiente. La conexión de Actions a RC6 ya se utilizó para los dos preflights descritos arriba, ambos detenidos antes de red IOL.
- Mantener las próximas notas de resultado en este archivo, en GitHub. Al ejecutarse el workflow, vuelve a copiar la versión del handoff del commit al servidor. No guardar secretos ni respuestas completas.

## Resultado confirmado del seguimiento

- Run [34899652579](https://github.com/mbalbo2023/Porota-trading/actions/runs/34899652579), commit `0ef4ac384ff22096987b7be15b1c8a8d698d64f8): `SERVER_HANDOFF_SYNC=OK`; destino confirmó `true|porota-trading-bot:17.0.0-rc6`. El handoff fue copiado a `/opt/porota-trading/docs/handoffs/RC6_IOL_READONLY_CANARY_2026-09-14.md`.
- En el mismo run, el preflight informó `IOL_CREDENTIAL_NAMES_PRESENT=NONE` y `CANARY=STOPPED_BEFORE_NETWORK`. No hubo POST de autenticación ni GET a IOL.
- El run termina en fallo deliberado del canary porque el preflight no encontró las variables; la sincronización documental sí terminó correctamente.

## Hallazgo de integración paralelo (DTO)

El wrapper offline de PR #64 conserva operación, consulta, hora tomada antes de la llamada y estado `NONEMPTY_UNVALIDATED` / `EMPTY_OR_UNAVAILABLE`. No alcanza todavía para persistir evidencia validada: la hora no es recepción, el mercado es el solicitado y no incluye ID del proveedor, venue, moneda, settlement ni resultado de validación campo por campo. No persistir `payload` directamente ni convertir respuesta no vacía en readiness. Antes de conectarlo a Evidence v2 hacen falta identidad ampliada y un contrato de procedencia/validación que evite claves con `UNKNOWN`; no llamar a una lectura “read-only” si ejecuta inicialización/DDL.

## Proyección a evaluación PAPER — revisión paralela

- No se hizo cambio de motor ni del esquema. El mapeo automático de la observación IOL a la evidencia actual sería especulativo: la clave existente no preserva toda la identidad contractual (incluidos venue, moneda y un identificador estable del proveedor), y leer la vista actual también ejecuta inicialización de esquema. No se consultó la base de datos.
- El observer PAPER RC6 actual toma cotización y libro PPI, valida timestamps recientes/no futuros, identidad monetaria y consistencia con contrato, además de sesión y riesgo. IOL debe quedar como contraste hasta tener el mismo instrumento inequívoco, timestamps con semántica conocida y campos comparables.
- Un panel, una cotización o un histórico no aportan por sí solos el último trade, el libro ni la disponibilidad de apertura en PPI. Payload ausente, viejo, incompleto o en conflicto debe quedar como observación no validada y no elevar readiness.
- Pendiente de diseño antes de codificar: envolvente de observación con hora de inicio/recepción, hora efectiva del proveedor si existe, endpoint y parámetros; identidad de proveedor y contrato; validaciones por campo; y comparación de IOL contra PPI en símbolo/mercado/moneda/settlement coincidentes.

## Revisión reiniciada desde la web de IOL — 2026-09-14

La página pública `Herramientas → API` presenta documentación y enlace a la consola de API. La documentación visible indica:

- Requiere cuenta abierta; para habilitar el servicio, la guía indica solicitarlo desde Mensajes y aceptar términos bajo `Mi Cuenta → Personalización → APIs`. El estado de activación de esta cuenta no se verificó en sesión autenticada.
- La consola anuncia API 2.0 y recuerda que las acciones impactan en el entorno REAL. El canary queda limitado a autenticación y GET allowlisted; se excluyen todos los endpoints de cuenta, órdenes, cancelación y cualquier POST distinto de `/token`.
- La ayuda de autenticación se titula API V1 y documenta POST `/token` con `grant_type=password`, bearer token con vencimiento a los 15 minutos y renovación mediante `refresh_token`. No presenta en esa guía otro flujo alternativo de usuario/contraseña.

### Historial de almacenamiento de credenciales (sin valores)

El bootstrap de 2026-09-09 registró el guardado directo de las variables IOL en `/opt/porota-trading/.env`, con reemplazo atómico y permisos restringidos `0600`; no reinició el runtime ni intentó operar. Ese procedimiento no fue un hash. Una contraseña hasheada no permite obtener el token: la API necesita el secreto original o un almacén cifrado/reversible.

La Action del 2026-09-14 volvió a consultar únicamente presencia de los nombres esperados en el entorno del contenedor y `/opt/porota-trading/.env`; ambos dieron `NONE`. Por lo tanto, el dato histórico de guardado no demuestra que sigan presentes hoy. No se imprimió ni se incorporó ningún valor al repo, logs o artefactos.

### Fallback de lectura web

La lectura de `Herramientas → API` completó la documentación pública y no constituye fallo de la API. El scraping autenticado de datos privados queda como fallback solo si el acceso read-only por API realmente falla y la cuenta puede abrirse con el flujo seguro. No usarlo para eludir login/2FA/bloqueos, recorrer páginas de órdenes, ni hacer captura masiva; priorizar consultas puntuales de datos de mercado.


## Continuidad — persistencia local y MCP IOL (2026-09-14)

### Script de credenciales para ejecutar en el droplet

Se generó guardar_credenciales_iol_en_droplet.sh como artefacto entregable, sin valores de credenciales. El usuario lo ejecutará en la terminal del droplet con permisos root. Solicita el usuario y la contraseña interactivamente (la contraseña no se muestra), actualiza únicamente IOL_USERNAME y IOL_PASSWORD en /opt/porota-trading/.env, conserva el resto del archivo y normaliza definiciones duplicadas; escribe atómicamente y fija modo 0600. No imprime secretos, activa IOL_ENABLED, reinicia servicios ni inicia autenticación. No comprobar contenido mediante cat, docker compose config u otra salida que revele valores. Una vez que el usuario termine, el próximo paso es volver a ejecutar el preflight de presencia del canary antes de cualquier red IOL.

### MCP oficial de IOL

La documentación pública en https://mcp.invertironline.com/ describe un servidor MCP con transporte Streamable HTTP y autorización OAuth 2.0 con consentimiento del usuario. IOL lo presenta para conectar asistentes, con acceso de solo lectura por defecto; puede consultar información privada de cuenta, cartera y órdenes pendientes. La misma documentación contempla ampliar funcionalidades al desconectar y volver a autorizar. Por tanto, “solo lectura por defecto” no significa acceso solo a datos públicos ni elimina la exposición de datos personales/financieros.

El MCP es una integración independiente de las credenciales IOL_USERNAME/IOL_PASSWORD usadas por REST /token: el login ocurre en el flujo OAuth oficial y no se deben reutilizar o capturar credenciales en RC6. No conectar ni autorizar MCP automáticamente. Para el trabajo de RC6, priorizar REST oficial con endpoints públicos de mercado allowlisted; estudiar MCP en paralelo como fuente conversacional/consulta de cuenta, solo con autorización OAuth explícita, mínimo alcance y pruebas que no consulten ni almacenen cartera u órdenes. No usar MCP para órdenes, operaciones ni para inferir readiness del motor.

### Estado para retomar

1. El usuario ejecuta el script en el droplet, pudiendo pegar los datos en la entrada oculta; no debe enviarlos al chat.
2. Confirmar solo presencia de nombres/permisos por el preflight ya existente, nunca los valores.
3. Si preflight pasa, correr una sola vez el canary GET de mercado restringido ya definido, manteniendo RC6 exacto, cero órdenes y sin tocar PPI Watch.
4. Evaluar MCP como pista separada; no instalar/conectar ni usar credenciales hasta definir alcance y verificar el OAuth oficial en modo de lectura.


### Corrección de accesibilidad del script — 2026-09-14

El primer bloque pegado en Termius intentó leer el usuario desde stdin mientras stdin seguía siendo consumido por el pegado del heredoc; terminó con EOF antes de modificar el archivo. No hubo escritura confirmada por ese intento. Versión corregida: abrir /dev/tty para prompts interactivos, conservar entrada oculta para la contraseña y enviar al portapapeles solo un resumen saneado mediante OSC 52 (BEL y ST). Nunca incluir secretos en el resultado/clipboard. El script no debe depender de escritura manual ni de seleccionar salida en pantalla.

Regla global persistida también en POROTA_TRADING_RC6_CONTEXT_HANDOFF.md: Tincho tiene cuadriplejia y no puede escribir; cada chat debe asumir voz/pegado, entregar bloques completos y hacer que los scripts preparen la salida para portapapeles antes de empezar instrucciones. Validar PORTAPAPELES_OSC52=EMITIDO; si no se puede emitir, informar el límite sin fingir éxito.


### Addendum v3 de persistencia accesible — 2026-09-14

La versión v3 del script:
- se eleva con sudo de forma automática y no presupone acceso root;
- se entrega con nombre versionado nuevo; el bloque de Termius crea ese nombre fijo con noclobber y lo ejecuta, por lo que los sufijos de descarga no intervienen;
- no reescribe .env: comprueba bajo lock si IOL_USERNAME/IOL_PASSWORD ya están definidos y, si no, anexa ambos al final; si ya existen, se detiene sin modificarlos;
- lee entradas desde /dev/tty para evitar EOF cuando el bloque completo se pega de una sola vez;
- fija permisos 0600 al anexar y no reinicia servicios;
- imprime y emite por OSC 52 (BEL y ST) solo un resumen saneado. PORTAPAPELES_OSC52=EMITIDO confirma la emisión; nunca incluye valores secretos.
Validación local aislada: preservó los bytes existentes, anexó las claves, permisos 0600 y ausencia de secretos en salida/clipboard. No se ejecutó contra el droplet ni contra IOL. Requiere que el usuario pegue los valores en los prompts ocultando la contraseña; ningún valor se incluye en archivo/código/commit.


### Addendum v4: sesión Termius abierta al finalizar — 2026-09-14

La v4 agrega pausa final leyendo desde /dev/tty, para mantener Termius abierto hasta que el operador indique Enter por voz. El bloque de instalación se ejecuta en subshell, con noclobber limitado a ese subshell; así no cambia opciones del shell interactivo ni puede cerrarlo por errexit. Mantiene la elevación automática sudo, nombre fijo/versionado, append-only de .env, salida saneada y emisión OSC 52. Validación aislada: append y modo 0600, sin secretos en salida/clipboard, marcador TERMINAL_MANTENIDA=SI. No se ejecutó contra droplet ni IOL.


### Addendum v5: ruta temporal única para reintento de Termius — 2026-09-14

El intento del bloque v4 encontró el archivo ya existente en /tmp y, correctamente, la protección impidió sobrescribirlo; no pedir al operador borrar ni renombrar archivos. La v5 crea un nombre temporal único con etiqueta v5 en cada pegado, ejecuta la copia recién creada con sudo y mantiene la terminal en pausa final. Repite únicamente la prueba de escritura del .env si las claves aún no existen; no toca servicios y, si detecta claves preexistentes, no modifica el archivo.
