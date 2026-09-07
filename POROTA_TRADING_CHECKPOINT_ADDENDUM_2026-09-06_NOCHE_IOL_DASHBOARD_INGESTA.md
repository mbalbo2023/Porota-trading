# POROTA TRADING — ADDENDUM CHECKPOINT NOCHE 2026-09-06

**Continuidad:** leer después de `POROTA_TRADING_CHECKPOINT_CANONICO_RC6_2026-09-06_POSTHOTFIX.md`.

## 1. Identidad live que NO cambia por este addendum

- Branch live: `hotfix/rc6-cedear-us-labor-day-20260906`
- SHA observer/runtime: `db26c76723bb988c956589c572b87cbcb4191731`
- Release: `17.0.0-rc6`
- Modo: `PRODUCTION_PAPER`
- Ejecución: `SIMULATED`
- Real money: `BLOCKED`
- La documentación vive en branch separada; no mover la branch live por documentación.

## 2. PPI historical — hallazgo de calidad

La ingesta PPI sí corre. El problema observado ya no es ausencia de scheduler sino efectividad/calidad del material recibido y de la priorización del universo.

Evidencia de auditoría read-only de la noche:
- store `history_canonical_v2`: ~48.323 filas FULL_OHLC;
- fuente PPI Production History: ~47.440 filas;
- fuente Data912: ~883 filas;
- evidencia close-only preservada: ~7.821 filas;
- rechazos acumulados observados: ~210.345;
- causas dominantes observadas: `OHLC_INCONSISTENT`, `HIGH_NONPOSITIVE`, `OPEN_NONPOSITIVE`;
- el History Store v2 conserva las filas válidas y rechaza filas defectuosas individualmente; no se deben relajar validadores para inflar coverage;
- el indicador legacy de cobertura completa por identidad (`production_history`) subestima el material útil almacenado porque una serie parcial no reemplaza la última serie declarada completa, aun cuando History Store v2 sí rescata barras válidas;
- se observó que la selección de lotes puede incluir identidades ya completas junto con identidades todavía necesitadas, por lo que existe margen para mejorar priorización sin aumentar llamadas al broker.

### Pendiente P1

Diseñar/testear una priorización de `_historical_targets()` que favorezca, en este orden aproximado y sin fuzzy matching:
1. `NO_ATTEMPT` / sin evidencia canónica;
2. `ERROR` transitorio;
3. `EMPTY_OR_INVALID`;
4. `PARTIAL` con menor cantidad de filas válidas;
5. refresco de identidades ya completas sólo cuando corresponda por freshness/TTL.

No desplegar un cambio de scheduler histórico en el hot path durante la rueda del lunes. Primero tests y prueba read-only/offline.

## 3. Priming nocturno

Workflow: `RC6 night data priming 2026-09-06`, run `34068494819`.

Estado del workflow: `SUCCESS`.

Objetivo: PPI historical bounded + Data912 reconciliation + candle integrity + pre/postflight, manteniendo observer exacto y `real_orders_sent=0`.

Pendiente de continuidad: conservar los resultados before/after de esta corrida como evidencia M3 y no volver a forzar PPI inmediatamente; respetar TTL/backoff.

## 4. Dashboard para el Go Live

Branch UX separada: `ux/rc6-dashboard-go-live-20260906`.

Cambios candidatos preparados:
- panel ejecutivo con últimas 5 operaciones PAPER de días operativos;
- no presentar sábado/domingo/feriado como trading normal;
- retirar bloque técnico `Motores / workers` de En Vivo;
- Histórico más operativo y tabular;
- BCRA/INDEC/macro y performance vs inflación dentro de Reportes;
- Configuración alineada a parámetros efectivos RC6;
- corrección de selección de snapshots de introspección RC6 (el dashboard anterior buscaba sólo patrón HF y podía ignorar snapshots RC6 actuales);
- tablas compactas/responsive/Voice Access para las vistas solicitadas;
- logs presentados con semántica de evidencia y no convertir `0 bytes` automáticamente en GREEN.

### Resultado de validación v2

Run `34067923761`:
- build: GREEN;
- compile: GREEN;
- targeted dashboard regression tests: GREEN;
- deploy: NO ACTIVADO; falló durante preview antes de tocar producción.

Diagnóstico posterior run `34068457128`:
- el candidato puede renderizar directamente `home_page` con datos reales;
- varias rutas HTTP de la preview fallaron porque la preview montó `/app/data` read-only mientras componentes legacy de `o_dashboard/ac_db` intentan pragmas SQLite (`WAL` / `synchronous`) sobre `trading_system.db`;
- producción quedó sin cambios: observer y dashboard continuaron en la imagen RC6 live, restart=0;
- por tanto el fallo es del diseño de la preview no-equivalente al runtime, no evidencia de corrupción del runtime productivo.

### Regla para continuar el dashboard

No activar el candidato hasta tener una prueba product-equivalent segura. Preferencia:
- validar directamente todos los renderers modificados contra data real en modo read-only;
- comprobar las rutas HTTP no modificadas contra el dashboard productivo actual;
- activar únicamente el container dashboard;
- postflight inmediato de todas las rutas críticas;
- si el candidato HTTP falla tras activación, volver a levantar el dashboard con la imagen RC6 base sin reiniciar observer.

El observer/estrategia/gates no se modifican por esta tarea.

## 5. InvertirOnline (IOL) como nueva fuente histórica — NUEVO PENDIENTE

Se incorpora formalmente la investigación de IOL como fuente adicional/independiente para históricos y Contract Evidence.

### Evidencia pública/official encontrada 2026-09-06

IOL publica una API oficial que declara:
- datos de mercado y cotizaciones en tiempo real;
- series de cotizaciones históricas;
- históricas ajustadas;
- familias del mercado argentino que incluyen Acciones, Bonos, Opciones, Cauciones, Futuros, Monedas y Cheques de Pago Diferido;
- JSON sobre HTTPS;
- autenticación con bearer token + refresh token;
- bearer de vida corta (~15 minutos);
- requiere cuenta IOL, habilitación del servicio API y aceptación de términos;
- el entorno productivo de la API también contiene métodos operativos, por lo cual cualquier integración POROTA debe ser estrictamente GET/read-only con allowlist y bloqueo local de rutas de órdenes.

Endpoint de serie histórica documentado públicamente en implementaciones que reflejan la API v2:
`GET /api/v2/{mercado}/Titulos/{simbolo}/Cotizacion/seriehistorica/{fechaDesde}/{fechaHasta}/{ajustada}`

IOL además publica actualmente un MCP oficial con opción explícita de permiso `solo lectura`; dentro de las herramientas read-only declara `get_price_history`, `get_asset_info`, `get_options_chain`, `get_caucion_rates`, `get_fixed_income_analytics`, FCI y otras consultas. Esto puede ser útil como vía de investigación/contract evidence, pero no debe asumirse automáticamente como mecanismo de ingesta runtime sin una integración gobernada.

Tarifa pública observada: servicio API bonificado hasta 25.000 API calls por mes; luego existe cargo según tarifario vigente. Por ello cualquier uso debe tener cache, batch lógico, TTL y presupuesto de llamadas.

### Hipótesis de valor para POROTA

IOL es un candidato fuerte como **segunda fuente histórica independiente** porque puede permitir:
- contraste PPI vs IOL por símbolo/fecha;
- rescate de huecos donde PPI devuelva payload parcial o filas inválidas;
- comparar series `ajustada` vs sin ajustar, especialmente CEDEARs/acciones;
- validar si anomalías `HIGH_NONPOSITIVE` / `OHLC_INCONSISTENT` son específicas de PPI o del instrumento/mercado;
- ampliar Contract Evidence de familias, símbolos, mercados y metadatos.

### Política propuesta para IOL

Antes de integrarlo al History Store:
1. confirmar acceso API en una cuenta IOL y aceptar términos del servicio;
2. construir `IOL_READONLY_GUARD` con allowlist exclusiva de GET de market data/history; ningún POST/DELETE operativo accesible;
3. cero reutilización de credenciales fuera de secrets; no guardar tokens en Git;
4. hacer proof con un conjunto pequeño de instrumentos que hoy presentan problemas en PPI y controles sanos;
5. comparar campos, timezone, ajuste, market/symbol grammar, volumen y OHLC;
6. mapear identidad IOL → identidad canónica POROTA sólo con reglas determinísticas verificadas;
7. guardar provenance `IOL_HISTORY` separado; no sobrescribir PPI silenciosamente;
8. definir precedencia/reconciliación en History Store v2;
9. budget de llamadas mensual + cache por período;
10. no usar IOL para live/sizing/órdenes mientras este pendiente se evalúa; objetivo inicial: históricos + evidencia.

### Scraping IOL

No usar scraping como fuente primaria si la API oficial cubre el dato. IOL tiene páginas web públicas de datos históricos que pueden servir para spot-check/Contract Evidence, pero el scraping es más frágil y debe considerarse sólo complementario.

Uso aceptable a investigar:
- verificar visualmente columnas/campos/ajustes publicados;
- Contract Evidence de familias o metadatos no expuestos claramente por API;
- detectar divergencias API vs web.

No convertir el scraping en sustituto automático de la API sin revisar términos, estabilidad, rate limits y autorización. La automatización robótica puede estar restringida en determinados términos/servicios del sitio; por eso API/MCP read-only tienen prioridad.

### Prioridad

- IOL historical API proof: `P1` para mejorar M3/history quality, pero **no blocker del PAPER del lunes**.
- IOL scraping/Contract Evidence: `P1/P2` según huecos que queden después del proof de API.
- Real money permanece `BLOCKED`.

## 6. Continuidad inmediata

1. finalizar y registrar métricas del priming nocturno;
2. corregir la estrategia de validación/deploy dashboard sin tocar observer;
3. mantener PPI backoff;
4. mañana preopen sigue exigiendo gate completo 10:15–10:30;
5. luego del Go Live PAPER, ejecutar proof read-only de IOL en branch/runner separado, nunca mezclado con el hot path sin evidencia previa.
