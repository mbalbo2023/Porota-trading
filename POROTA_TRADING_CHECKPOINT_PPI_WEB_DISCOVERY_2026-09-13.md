# POROTA TRADING — CHECKPOINT PPI WEB DISCOVERY — 2026-09-13

## Alcance
Checkpoint de continuidad para la etapa de descubrimiento y validación del histórico residual por PPI Web en RC6. Este documento registra resultados obtenidos hasta el 2026-09-13 y fija el siguiente canary integral antes de habilitar scraping residual masivo.

## Reglas operativas vigentes
- Fuente primaria: PPI API sobre universo canónico de 1960 identidades.
- PPI Web autenticado se usa solamente para residual no resuelto por API.
- IOL queda como última fuente residual.
- Ventana histórica canónica: **PREVIOUS_365D**. Aunque PPI Web entregue más de 365 días, todo dato anterior a la ventana se descarta antes de validar/escribir.
- Nunca sintetizar/interpolar/reparar OHLC inválido.
- Mantener settlement separado; plazo=1 y plazo=2 no se fusionan.
- Diagnóstico/canary: `CANONICAL_WRITE=DENY`, `REAL_ORDERS=0`, `PRODUCTION_PAPER|0` antes/después.
- Sólo navegación/lectura. No órdenes, cauciones, licitaciones, suscripciones/rescates FCI, transferencias, seguridad/2FA.
- No registrar valores de Authorization, clientkey, authorizedclient, cookies, OTP, credenciales o tokens.
- No habilitar POST para diagnóstico. Los POST observados anteriormente fueron telemetría (`/api/logger`, Refiner identify-user), no histórico.
- Browser lock único: `/run/lock/porota-ppi-web-browser.lock`.
- Browser ejecutado como `porotaadmin` con perfil confiable `/home/porotaadmin/porota-browser-lab/chrome-profile`.
- Los 18 producer timers continúan pausados hasta cerrar histórico + residual Web + validación final.
- No interferir con el writer de ingesta PPI API mientras siga activo.

## Estado autenticación PPI Web
Preflight autenticado previamente validado:
- `AUTHENTICATED_TRUSTED_DEVICE`
- página trading autenticada alcanzable
- `CREDENTIALS_EXPOSED=False`
- `REAL_ORDERS_SENT=0`
- `PRODUCTION_PAPER|0`
- reauth/SSO account→trading validado

## Hallazgos históricos ya confirmados
### CRESD / Acciones
Item `794828`.
- plazo=1: HTTP 200, 284 filas, 2025-07-14 → 2026-09-11.
- plazo=2: HTTP 200, 288 filas, 2025-07-14 → 2026-09-11.
- Graficador funcional.
- XHR real observado: `/api/Cotizaciones/Item/794828/Historico/{plazo}`.

### S13N6 / Letras
Item `926121`.
- plazo=1: HTTP 200, 52 filas.
- plazo=2: HTTP 200, 53 filas.
- Graficador funcional.

### Formato de filas histórico Web observado
Campos relevantes observados en `payload`:
- `item`
- `ultOperado`
- `apertura`
- `maxDia`
- `minDia`
- `cierreAnterior`
- `max52`
- `min52`
- `variacion`
- `volumen`
- `fechaCotizacion`

Pendiente antes de escritura canónica: validar formalmente el mapeo OHLC; **no asumir que `ultOperado` es cierre canónico sin validación**.

## Probes de descubrimiento por DOM/listado
Símbolos residuales representativos usados:
- CEDEARS: `AVYC`
- BONOS: `TX28D`
- ON: `MRCTO`
- OPCIONES: `YPFV6100OC`
- FUTUROS: `DLR/AGO27M`
- FCI: `PI.RENT.B`

Resultado: `ITEM_NOT_FOUND` por DOM/listados simples. Esto **no significa instrumento inexistente**; sólo que el método de descubrimiento era incorrecto.

## Probe de transporte de páginas de familia
Las páginas públicas/autenticadas de cotizaciones respondieron correctamente para Cedears, Bonos, ONs, Opciones y Futuros, pero los símbolos objetivo no aparecieron en los GET JSON observados ni en frames WebSocket inspeccionados. Esto descarta que el listado visible simple sea necesariamente la fuente primaria de resolución de IDs.

FCI: las rutas singulares ensayadas eran incorrectas/incompletas.

## Broad static recon — run 34739460123
Workflow: `RC6 PPI Web broad static recon 2026-09-13`.
Resultado: `SUCCESS`.

### Hallazgos fuertes
1. **Buscador global de instrumentos**
   - El frontend usa `quotesApi.search(e)`.
   - La respuesta se transforma con campos `id`, `tipoItem.id`, `ticker`, `descripcion` y, para FCI, `caracteristicas.claseFCIId`.
   - El buscador construye el destino del instrumento a partir del ID/tipo.

2. **Graficador / TradingView dispone de búsqueda propia**
   - Implementa `searchSymbols` → `t.search(e)`.
   - Implementa `getInstrument(i)` para resolver instrumento.
   - Usa `itemId` transversalmente.
   - Eventos realtime observados: `cotizaciones-suscribir`, `cotizaciones-desuscribir`, `cotizacion`, etc.

3. **Ruta correcta FCI**
   - Manifest: `/Cotizaciones/FCIs/[instrumentId]`.
   - Mercado referencia `/Cotizaciones/FCIs` y `/Cotizaciones/FCIsExterior`.
   - Las rutas FCI singulares usadas en probes previos no deben usarse como referencia final.

4. **Otras fuentes de enumeración/resolución disponibles**
   - Alertas: `getInstrumentsForAlerts(tipoItem)`.
   - Mercado, Research y Carteras usan `itemId`, `typeId`, ticker y familias de instrumentos.
   - Research/Arbitrador y Análisis de Bonos exponen estructuras útiles para Cedears/Bonos.

5. **Familias reconocidas explícitamente por frontend**
   - Acciones, Bonos, Cedears, Letras, Obligaciones Negociables, Opciones, Futuros, FCI, FCI Exterior, entre otras.

6. **Detalle de instrumento contempla derivados**
   - `/Cotizaciones/Item/[instrumentId]` contiene lógica explícita para Futuros y Opciones y cambio de instrumento/plazo.

### Hipótesis descartadas
- Que los IDs deban descubrirse exclusivamente recorriendo el DOM de `/Cotizaciones/<familia>`.
- Que `ITEM_NOT_FOUND` implique que el instrumento no existe.
- Que FCI no tenga página de cotización.
- Que WebSocket sea obligatoriamente la única fuente de listado.

## Próximo paso aprobado: canary integral multifamilia
Ejecutar **una sola evaluación amplia** por GitHub Actions, usando la sesión autenticada del droplet y sin escritura canónica, para:
1. Resolver los seis símbolos mediante el buscador global real de PPI.
2. Capturar `ticker → itemId → typeId → descripción → familia` sin registrar secretos.
3. Observar qué endpoint GET alimenta `quotesApi.search`.
4. Usar como fallback el mecanismo de búsqueda/resolución del graficador y otras fuentes read-only si el buscador global no devuelve un símbolo.
5. Abrir la ficha read-only correspondiente a cada instrumento encontrado.
6. Detectar Graficador y observar XHR `/Historico/{plazo}` cuando exista.
7. Para FCI, usar ruta específica `/Cotizaciones/FCIs/[instrumentId]`.
8. Probar plazo=1 y plazo=2 sólo cuando la familia/ruta lo admita.
9. Reportar matriz final por familia con `FOUND`, `itemId`, `typeId`, detalle, graficador, HTTP histórico, filas y rango de fechas.
10. No persistir histórico todavía.
11. Mantener `PRODUCTION_PAPER|0`, `CANONICAL_WRITE=DENY`, cero órdenes y cero exposición de credenciales.

## Criterio de salida del canary
VERDE funcional por familia sólo si:
- símbolo exacto resuelto a `itemId/typeId`,
- ruta de detalle alcanzada sin operación,
- histórico real observado o evidencia concluyente de que la familia usa mecanismo diferente,
- seguridad intacta,
- no hubo escritura canónica.

El resultado del canary definirá el diseño final del scraper residual de 365 días y sus adaptadores por familia.
