# POROTA HF6 v2 — Integración A3/Primary read-only

Documento WIP. No habilita órdenes ni modifica el runtime HF6 activo.

## Objetivo

Agregar A3 Mercados / Primary como fuente oficial para futuros y opciones, separando cuatro capacidades:

1. metadata contractual;
2. históricos;
3. market data live;
4. riesgo/post-trade.

Ninguna de estas capacidades, por sí sola, habilita un instrumento para operar en PAPER.

## Invariantes

- `A3_ORDER_ROUTING_ALLOWED=false`.
- No se implementan métodos de alta, reemplazo ni cancelación de órdenes en este patch.
- A3 no reemplaza PPI como broker/read path vigente de Porota para las familias ya operativas.
- El servicio A3 sólo publica datos sanitizados y versionados hacia Porota.
- Token, usuario, contraseña y cuenta nunca se guardan en GitHub, Contract Evidence, History Store, logs o artefactos compartibles.
- Ausencia de credenciales, API, market data, contrato o risk => `HOLD`, nunca valores inferidos.

## Por qué debe ser un servicio separado

El entorno Python principal de Porota contiene `ppi-client==1.2.4`, que requiere `websocket-client==1.0.0` por `signalrcorePPI`.

`pyRofex==0.5.0` requiere `websocket-client>=1.6.4`.

Por lo tanto no se debe forzar la resolución de dependencias ni alterar el entorno PPI. A3/Primary se ejecutará en un sidecar/servicio independiente, con su propio entorno y lifecycle.

## Credenciales y acceso

A3 documenta autenticación por token contra Primary/reMarkets. Las credenciales se almacenarán únicamente en el host, por ejemplo:

`/home/porotaadmin/.porota-secrets/a3_primary.env`

Permisos requeridos: `0600`, propietario `porotaadmin`.

Variables lógicas, sin valores en GitHub:

- `A3_PRIMARY_USER`
- `A3_PRIMARY_PASSWORD`
- `A3_PRIMARY_ACCOUNT` cuando una capacidad lo requiera
- `A3_PRIMARY_ENVIRONMENT=REMARKETS|PRODUCTION`
- `A3_ORDER_ROUTING_ALLOWED=false`

El token de sesión es efímero y no debe persistirse en DB ni logs.

## Etapas de readiness

### A3_ACCESS_READY

- credenciales presentes localmente;
- autenticación responde correctamente;
- token recibido pero no impreso ni persistido;
- health read-only verde.

### A3_CONTRACT_INGEST_READY

- segmentos disponibles;
- catálogo de instrumentos disponible;
- detalles contractuales disponibles;
- `contractMultiplier`, `minPriceIncrement`, `minTradeVol`, `maxTradeVol`, `tickSize`, `roundLot`, vencimiento y otros campos oficiales se normalizan con provenance;
- Contract Evidence v2 registra snapshot/hash/source.

### A3_HISTORY_INGEST_READY

- endpoint histórico de trades responde para instrumentos representativos;
- trades raw se validan;
- barras derivadas se escriben en History Store v2 con identidad completa;
- fuente A3 queda versionada y no sobreescribe silenciosamente evidencia de mayor autoridad.

### A3_LIVE_MD_READY

- WebSocket recibe eventos reales de market data durante la sesión correspondiente;
- freshness, reconnect/backoff y heartbeat funcionan;
- no se utiliza polling REST continuo como sustituto de WebSocket;
- ninguna función de order routing queda cargada.

### A3_RISK_DATA_READY

- Risk/Post Trade accesible cuando el permiso contratado lo permite;
- márgenes, garantías/collateral y restricciones se obtienen desde fuente oficial;
- no se prorratea un margen agregado ni se inventa garantía por contrato.

### FUTURES_READY_PAPER_CANDIDATE

Sólo puede evaluarse cuando las capacidades requeridas por la familia estén verdes y además existan:

- contrato completo;
- histórico suficiente;
- market data live fresco;
- sizing específico de futuros;
- costos;
- horario de la serie/segmento;
- reglas de settlement/ajuste;
- margin/risk verificado cuando corresponda;
- simulador y salida específicos;
- tests agrupados verdes.

Aun así el resultado es `READY_PAPER_CANDIDATE`; la promoción a `READY_PAPER` requiere revisión y aprobación del deploy.

## Flujo de datos

### Contratos

`A3 Primary instruments/details -> sanitización -> Contract Evidence v2`

Frecuencia: baseline post-cierre, refresh diario, evento inmediato ante serie nueva/cambio de hash.

### Históricos

`A3 Primary getTrades / fuente histórica oficial -> raw validado -> barras -> History Store v2`

Frecuencia: batch post-cierre. Las familias HOLD también pueden acumular historia.

### Live

`A3 Primary WebSocket -> normalizador live -> freshness/cache read-only -> motor PAPER`

Sólo durante la sesión oficial de cada segmento. No existe una hora global de apertura.

### Risk/Post Trade

`A3 Risk/Post Trade -> snapshot dinámico -> Contract/Execution Evidence -> readiness`

TTL corto y fail-closed.

## Almacenamiento y aislamiento

Se prefiere un único escritor por DB.

El sidecar A3 guardará su spool/evidencia sanitizada bajo un namespace dedicado, por ejemplo `data/a3/`, y Porota importará snapshots verificados hacia Contract Evidence / History Store v2. No compartir credenciales ni token a través de la DB.

El raw se somete a la política `STORAGE_LIFECYCLE_HF6_V2.md`: retención caliente limitada, compresión posterior y deduplicación por hash cuando sea seguro.

## Horarios

No hardcodear `MARKET_OPEN_HOUR` global.

La referencia vigente de A3 distingue segmentos. Por ejemplo, la tabla pública de A3 muestra Dólar/Yuan con negociación 10:00–15:00 y RFX20/acciones/BTC/Oro/WTI/Títulos/CER/CAUC con negociación 10:30–17:00. Los horarios deben versionarse por segmento/producto y fecha efectiva.

## Qué falta externamente

Para confirmar la ingesta real hace falta contar con un usuario Primary/reMarkets habilitado para las APIs necesarias. Si no existe, debe solicitarse a A3 por su canal oficial. No se deben pegar credenciales en ChatGPT ni en GitHub.

## Validación antes de deploy

Una única batería agrupada deberá probar:

- dependencia A3 aislada de PPI;
- auth sin filtrar secretos;
- segmentos;
- catálogo y detalle contractual;
- histórico representative;
- WebSocket live en horario de mercado;
- Risk/Post Trade si existe permiso;
- History Store v2;
- Contract Evidence v2;
- fail-closed;
- observer HF6 sin restart accidental;
- `real_orders_sent=0`.

No se despliega el sidecar ni se habilitan futuros/opciones sin GO explícito del propietario.
