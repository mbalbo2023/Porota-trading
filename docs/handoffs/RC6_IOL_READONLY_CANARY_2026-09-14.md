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
