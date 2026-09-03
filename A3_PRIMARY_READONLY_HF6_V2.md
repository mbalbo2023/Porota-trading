# POROTA HF6 v2 — Integración A3/Primary read-only

Documento WIP. No habilita órdenes ni modifica el runtime HF6 activo.

## Objetivo

Agregar A3 Mercados / Primary reMarkets como fuente oficial complementaria para derivados, separando cuatro capacidades:

1. metadata contractual;
2. históricos;
3. market data live;
4. riesgo/post-trade.

Ninguna de estas capacidades, por sí sola, habilita un instrumento para operar en PAPER.

## Invariantes

- `A3_ORDER_ROUTING_ALLOWED=false`.
- No se implementan métodos de alta, reemplazo ni cancelación de órdenes en este patch.
- PPI sigue siendo el broker/read path de referencia de Porota para las familias ya operativas.
- A3 no se utiliza como fallback campo-por-campo dentro de una decisión.
- Una decisión no puede mezclar bid de PPI con ask de A3, ni precio de una fuente con profundidad de otra.
- Token, usuario, contraseña y cuenta nunca se guardan en GitHub, Contract Evidence, History Store, logs ni artefactos compartibles.
- Ausencia de API, market data, contrato, identidad inequívoca o risk requerido => `HOLD`, nunca valores inferidos.

## Por qué debe ser un servicio separado

El entorno Python principal de Porota contiene `ppi-client==1.2.4`, que requiere `websocket-client==1.0.0` por `signalrcorePPI`.

`pyRofex==0.5.0` requiere `websocket-client>=1.6.4`.

Por lo tanto no se debe forzar la resolución de dependencias ni alterar el entorno PPI. A3/Primary se ejecutará en un sidecar/servicio independiente, con su propio entorno y lifecycle.

## Credenciales y cifrado

Las credenciales reMarkets existen fuera del repositorio. Sus valores no se incorporan a `.env`, GitHub, scripts descargables ni documentación.

El mecanismo aprobado es `systemd-creds`:

- credencial cifrada en reposo mediante AES-256-GCM;
- cifrado ligado al host mediante `--with-key=host` (y TPM2 además si el host lo soporta y se decide usarlo);
- archivo cifrado bajo `/etc/credstore.encrypted/`;
- plaintext disponible sólo transitoriamente durante provisioning/ejecución;
- token `X-Auth-Token` sólo en memoria y nunca persistido;
- scripts de provisioning no contienen credenciales en claro.

Para evitar reescribir secretos por accesibilidad, el provisioning usa una clave pública RSA temporal generada en el Droplet. El paquete posterior contiene únicamente ciphertext destinado a ese host. Después del provisioning exitoso, la clave RSA temporal se elimina.

Variables lógicas internas, sin valores en GitHub:

- `A3_PRIMARY_USER`
- `A3_PRIMARY_PASSWORD`
- `A3_PRIMARY_ACCOUNT`
- `A3_PRIMARY_ENVIRONMENT=REMARKETS`
- `A3_PRIMARY_BASE_URL=https://api.remarkets.primary.com.ar`
- `A3_ORDER_ROUTING_ALLOWED=false`

## Política de fuentes: PPI vs A3

### PPI

PPI conserva autoridad para:

- broker y operabilidad disponible para la cuenta;
- saldos/accounting PPI;
- costos/tarifario PPI;
- settlement/reglas específicas del broker;
- market data live de las familias que hoy ya consumen PPI como fuente primaria.

### A3/Primary

A3 tiene autoridad para:

- identidad y metadata contractual del derivado de mercado A3;
- `contractMultiplier`, `minPriceIncrement`, `minTradeVol`, `maxTradeVol`, `tickSize`, `roundLot`, vencimiento y demás campos oficiales disponibles;
- históricos/trades de A3;
- calendario/segmento y especificaciones del mercado;
- market data A3 para validación y, sólo después de homologación interna, eventual fuente live primaria de una familia completa.

### Regla de no mezcla

Cada snapshot de decisión lleva un `source_bundle` coherente. No existe fallback por campo.

Si PPI live está stale o ausente y A3 todavía no fue promovido formalmente a fuente live primaria de esa familia, la decisión queda `HOLD_DATA_SOURCE`; no se completa el hueco silenciosamente con A3.

A3 puede validar PPI en paralelo. Una divergencia superior a tolerancia temporal/precio configurada produce `SOURCE_CONFLICT` y bloquea la decisión hasta reconciliar.

La promoción futura de A3 live para Futuros/Opciones será explícita, versionada, probada por familia y requerirá GO de deploy.

## Etapas de readiness

### A3_ACCESS_READY

- credencial cifrada presente;
- autenticación responde correctamente;
- token recibido pero no impreso ni persistido;
- health read-only verde.

### A3_CONTRACT_INGEST_READY

- segmentos disponibles;
- catálogo de instrumentos disponible;
- detalles contractuales disponibles;
- campos contractuales se normalizan con provenance;
- Contract Evidence v2 registra snapshot/hash/source.

### A3_HISTORY_INGEST_READY

- `rest/data/getTrades` responde para instrumentos representativos;
- trades raw se validan;
- barras derivadas se escriben en History Store v2 con identidad completa;
- fuente A3 queda versionada.

### A3_LIVE_MD_READY

- REST puede validar snapshots;
- WebSocket recibe eventos reales durante la sesión correspondiente;
- freshness, reconnect/backoff y heartbeat funcionan;
- no se utiliza polling REST continuo para realtime;
- ninguna función de order routing queda cargada.

### A3_RISK_DATA_READY

- Risk/Post Trade accesible si reMarkets/permiso correspondiente lo expone;
- márgenes/garantías se obtienen desde fuente oficial verificable;
- un margen agregado no se transforma en garantía unitaria por inferencia.

### FUTURES_READY_PAPER_CANDIDATE

Sólo puede evaluarse cuando existan contrato completo, histórico suficiente, market data fresco, sizing específico, costos, horarios, settlement/ajuste, risk requerido, simulador de entrada/salida y tests agrupados verdes.

El resultado previo al GO sigue siendo `READY_PAPER_CANDIDATE`, nunca auto-promoción.

## Frecuencias

### Contratos

`A3 Primary instruments/details -> sanitización -> Contract Evidence v2`

- baseline post-cierre;
- refresh diario;
- recaptura inmediata ante serie nueva/cambio de hash.

### Históricos

`A3 Primary getTrades -> raw validado -> barras -> History Store v2`

- batch post-cierre;
- familias HOLD también acumulan historia;
- respetar buena práctica oficial: no consultar `getTrades` más frecuentemente que lo necesario; la documentación indica actualización cada 30 segundos, pero Porota lo usa en batch, no polling.

### Live

- REST sólo para smoke/snapshot/cierre;
- WebSocket para realtime una vez validado;
- sólo durante la sesión oficial del segmento.

## Almacenamiento

El sidecar A3 usa namespace dedicado `data/a3/`. Raw reciente tiene retención limitada y compresión según `STORAGE_LIFECYCLE_HF6_V2.md`; históricos canónicos y Contract Evidence permanecen versionados. Credenciales y tokens nunca entran a `data/a3/`.

## Validación antes de deploy

Una única batería agrupada probará autenticación, segmentos, catálogo, detalle contractual, snapshot Market Data REST, históricos representativos, presencia/ausencia de Risk/Post Trade, Contract Evidence v2, History Store v2, fail-closed, ausencia de métodos de orden y `real_orders_sent=0`.

La prueba WebSocket live se completa durante horario de mercado. No se despliega el sidecar ni se habilitan futuros/opciones sin GO explícito del propietario.
