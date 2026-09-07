# POROTA TRADING — IOL HISTORICAL / CONTRACT EVIDENCE RESEARCH — 2026-09-06

## Objetivo

Evaluar InvertirOnline (IOL) como fuente independiente para mejorar la calidad/cobertura histórica de POROTA y como fuente complementaria de Contract Evidence. No se incorpora a trading live ni a ejecución en esta etapa.

## Corrección importante después de auditar el SHA live exacto

El repositorio **sí contiene código IOL previo**. No debe describirse como una integración inexistente.

En el SHA live `db26c76723bb988c956589c572b87cbcb4191731` existen:

- `ak_iol_client.py`: cliente IOL desactivado por default mediante `IOL_ENABLED=false`; usa credenciales desde entorno, maneja token/refresh, throttling y consultas API;
- `al_historical_ingest.py`: ETL histórico legacy con `backfill_desde_iol()`, normalización OHLCV y persistencia en `market_historical_ohlcv`;
- `j_main.py`: runtime legacy antiguo; **no forma parte del split `PRODUCTION_PAPER` RC6 y no debe activarse para habilitar IOL**.

Por lo tanto el estado canónico es:

**IOL está implementado parcialmente en código legacy, pero está dormido/no cableado al observer RC6, al scheduler histórico RC6 ni al History Store v2 actual.**

Además, `ak_iol_client.py` no es estrictamente GET-only: aunque no expone envío/cancelación de órdenes, contiene `estimar_operacion()`, que realiza un `POST` al endpoint de estimación. Esa llamada se describe como simulación de costos y no como ejecución, pero impide considerar al módulo completo un guard histórico GET-only. Para un proof RC6 debemos aislar un cliente `IOL_HISTORY_READONLY` con allowlist positiva de GET y sin métodos POST alcanzables.

El ETL legacy `al_historical_ingest.py` tampoco debe convertirse directamente en canonical porque su tabla `market_historical_ohlcv` tiene PK `(symbol,date)` y hace UPSERT reemplazando la fuente anterior. La arquitectura actual History Store v2 conserva versiones/provenance y es el destino correcto para una futura integración gobernada.

## Hechos confirmados en fuentes oficiales IOL

La página oficial de API de IOL declara que el servicio permite obtener:
- datos de mercado;
- cotizaciones actuales;
- series de cotizaciones históricas;
- cotizaciones históricas ajustadas;
- información de Acciones, Bonos, Opciones, Cauciones, Futuros, Monedas y Cheques de Pago Diferido del mercado argentino;
- datos en JSON por HTTPS.

IOL indica que la API es un servicio para clientes con habilitación previa. El tarifario vigente observado informa bonificación de hasta `25.000 API Calls` mensuales y cargo posterior según tarifario.

La documentación oficial del MCP IOL confirma un modo de acceso `solo lectura`, default-deny para capacidades operativas, y herramientas GET de consulta. Entre ellas figura `get_price_history`, que devuelve barras históricas OHLCV de un activo; también existen `get_asset_info`, `get_options_chain`, `get_caucion_rates`, `get_fixed_income_analytics`, `get_fci_funds`, entre otras.

La API general de IOL también posee capacidades operativas. Por lo tanto, cualquier integración POROTA debe implementar aislamiento read-only propio aunque las credenciales estén habilitadas para más funciones.

## Encaje propuesto en POROTA

Primera fase: `IOL_HISTORY_READONLY`, nunca ejecución.

Casos de uso:
1. comparar una misma identidad/día PPI vs IOL;
2. determinar si `OHLC_INCONSISTENT`, `HIGH_NONPOSITIVE` y `OPEN_NONPOSITIVE` son anomalías propias del payload PPI o del activo/mercado;
3. rellenar huecos históricos únicamente bajo una política de precedencia explícita y con provenance separado;
4. contrastar series ajustadas/no ajustadas en acciones y CEDEARs;
5. obtener metadata de instrumento como evidencia adicional de identidad/contrato;
6. enriquecer Contract Evidence en familias donde IOL exponga información verificable que PPI no entregue con claridad.

## Política de seguridad requerida

Antes de una primera llamada autenticada desde POROTA:
- credenciales/tokens exclusivamente en secrets; nunca Git;
- `IOL_HISTORY_READONLY` con allowlist positiva de host y rutas GET autorizadas;
- no importar/exponer `estimar_operacion()` ni ningún POST desde el proceso de history proof;
- rutas de orden/operación no deben ser alcanzables desde el cliente histórico;
- no reutilizar el cliente histórico para trading;
- no arrancar `j_main.py`;
- timeout, retry limitado, backoff, rate budget y auditoría de cada request;
- ningún dato IOL puede habilitar órdenes reales;
- `real_orders_sent=0` sigue siendo invariante;
- si el contrato de respuesta no coincide, fail-closed y archivar evidencia raw versionada.

## Proof mínimo propuesto

No hacer una integración masiva. Probar primero una cohorte pequeña:
- controles sanos locales: GGAL, AL30;
- CEDEAR base con histórico razonable;
- variantes que hoy concentran rechazos PPI (`C`/`D`) según el RCA;
- PAMP / YPFD y otros símbolos que tuvieron errores transitorios PPI;
- una familia con semántica distinta si IOL la soporta claramente (por ejemplo caución/opción) sólo para entender contrato, no para mezclarla con OHLC de spot.

Para cada identidad comparar:
- símbolo y mercado;
- familia/tipo;
- moneda y settlement si están disponibles;
- timezone/fecha de rueda;
- open/high/low/close/volume;
- ajustada vs no ajustada;
- cantidad de barras;
- huecos;
- duplicados;
- precios <=0;
- invariantes OHLC;
- freshness;
- divergencia relativa PPI vs IOL.

## Modelo de persistencia propuesto

Primera prueba: no escribir canonical.

Si el proof es satisfactorio, conectar IOL al History Store v2 como fuente separada, por ejemplo `IOL_HISTORY`, conservando:
- source;
- observed_at;
- raw fingerprint/hash;
- identidad IOL original;
- identidad canónica POROTA sólo después de mapping determinístico;
- adjusted flag;
- validación/razón de rechazo;
- precedencia aplicada.

No usar `market_historical_ohlcv` legacy como store canónico de la nueva integración porque su UPSERT puede reemplazar silenciosamente fuente/provenance por `(symbol,date)`.

La precedencia definitiva PPI/IOL/Data912 debe decidirse con evidencia, no por orden de implementación. El History Store v2 ya tiene conocimiento de fuentes/ranking histórico; verificar el nombre/rank exacto de IOL antes de conectar el sink y revisar la política con datos comparativos reales.

## API vs scraping

Prioridad: API oficial o MCP oficial read-only.

Scraping web IOL se considera sólo complementario para Contract Evidence/spot checks si una pieza contractual no está disponible de manera suficiente por API. Motivos:
- API es estructurada y estable comparativamente;
- evita depender de HTML/JS cambiante;
- permite rate budgeting explícito;
- IOL publica restricciones frente a técnicas automáticas/robóticas en sus términos en determinados contextos y puede bloquear conductas que considere fraudulentas;
- también existe prohibición de retransmisión/publicación de determinada información BYMA distribuida por IOL.

Por eso no se debe convertir scraping IOL en una nueva ingesta masiva sin revisión específica de términos y alcance. Si se usa navegador/web:
- sólo lectura;
- sin operaciones;
- sin modificar cuenta/seguridad;
- sin persistir cookies/tokens;
- frecuencia baja;
- sólo evidencia necesaria;
- respetar derechos/restricciones de redistribución.

## Estado / prioridad

- código IOL legacy: PRESENTE pero DORMIDO/no cableado a RC6;
- IOL historical proof RC6: `P1`, útil para M3 y RCA de calidad histórica;
- migración del ETL IOL a History Store v2: P1 después del proof;
- IOL Contract Evidence: `P1/P2` según huecos reales;
- IOL scraping masivo: `NO APROBADO`; sólo investigar necesidad y términos;
- no es blocker del Go Live PAPER del lunes;
- no cambia PPI como fuente live actual;
- real money permanece `BLOCKED`.

## Fuentes públicas consultadas

- IOL API: `https://www.invertironline.com/api`
- Tarifas IOL: `https://www.invertironline.com/tarifas`
- MCP IOL documentación técnica: `https://mcp.invertironline.com/documentacion-tecnica`
- Términos y condiciones IOL: `https://www.invertironline.com/terminos-y-condiciones-iol`
- Términos de uso históricos del sitio: `https://iol.invertironline.com/home/terminos_y_condiciones`

## Próxima acción

Construir un proof RC6 read-only aislado, inicialmente sin persistencia canónica, que consulte un conjunto pequeño de símbolos, guarde evidencia de auditoría y entregue una matriz PPI vs IOL. Sólo después decidir si se conecta a History Store v2.

No activar credenciales ni realizar llamadas autenticadas hasta que exista el guard GET-only y la cuenta/servicio IOL estén explícitamente habilitados para esa prueba.
