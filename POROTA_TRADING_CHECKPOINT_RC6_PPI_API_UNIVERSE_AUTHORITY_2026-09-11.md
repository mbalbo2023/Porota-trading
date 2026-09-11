# POROTA TRADING RC6 — PPI API UNIVERSE AUTHORITY

Fecha: 2026-09-11
Wave: post-RC6 operacional / auditoría read-only
Runtime host preservado: `f8adec8a02b2f9f0ef2dffbee75458c958bf711e`

## Invariantes preservados
- VERSION `17.0.0-rc6`
- MODE `PRODUCTION_PAPER`
- EXECUTION `SIMULATED`
- REAL_ORDER_CAPABILITY `BLOCKED`
- real_orders_sent `0`
- REAL_ORDER_ROUTES `NOT_CALLED`
- CP1–CP14 permanecen GREEN/FROZEN

## Principio de autoridad
Para POROTA, el universo operativo debe estar definido por la API productiva de PPI, no por el conjunto de pantallas/familias visibles en la web.

Se separan tres capas:
1. `API_DECLARED`: familias/mercados/plazos declarados por la configuración viva de PPI.
2. `API_DISCOVERED`: identidades concretas devueltas por SearchInstrument y observadas en el catálogo PPI de POROTA.
3. `WEB_CONTEXT_ONLY`: información web que puede servir como contexto o enriquecimiento pero no debe crear por sí sola un candidato operativo ni un requisito Binding.

El scraping web se usa para enriquecer contratos de identidades soportadas/descubiertas por API, no para definir un universo independiente.

## Auditoría live PPI Configuration
Workflow: `34659647038`
Job: `103459289644`
Resultado: SUCCESS

Safety inicial/final: `ok|PRODUCTION_PAPER|MARKET_CLOSED|0`.
HTTP: 1 LoginApi + GETs de Configuration; blocked=0; sin órdenes, sin mutaciones.

### InstrumentTypes declarados por la API productiva
- ACCIONES
- ACCIONES-USA
- BONOS
- CAUCIONES
- CEDEARS
- ETF
- FCI
- FCI-EXTERIOR
- FUTUROS
- LEBAC
- LETRAS
- LICITACIONES
- NOBAC
- ON
- OPCIONES

Total: 15 tipos.

### Markets declarados
- BYMA
- NASDAQ
- NYSE
- OTC
- ROFEX

### Settlements declarados
- INMEDIATA
- A-24HS
- A-48HS
- A-72HS

### Operations declaradas por configuración PPI
- COLOCAR-CAUCION
- COMPRA
- EJERCER-CALL
- EJERCER-PUT
- LANZAMIENTO
- LICITAR
- RESCATE-FCI
- STOP-ORDER
- SUSCRIPCION-FCI
- VENTA

Que una operación exista en la configuración de PPI NO implica autorización de POROTA para ejecutarla. POROTA continúa sin rutas reales de órdenes.

## Diferencia API vs POROTA/web
Tipos API todavía no presentes en `_candidate_universe()` del runtime: `ACCIONES-USA`, `FCI-EXTERIOR`, `LEBAC`, `LICITACIONES`, `NOBAC`.

Tipo presente en runtime pero no declarado como InstrumentType por API: `INDICES`.

Familias Contract Evidence presentes pero no declaradas como InstrumentType por API: `INDICES`, `MONEDAS`, `TASAS`.

Decisión recomendada:
- `INDICES`, `MONEDAS`, `TASAS` => `CONTEXT_ONLY`; nunca deben producir candidatos de ejecución ni faltantes Binding contractuales para una operación.
- `LICITACIONES` => API_SUPPORTED; la configuración viva la declara y además existe operación `LICITAR`.
- `LEBAC`/`NOBAC` => API_DECLARED, pero no asumir especies activas hasta obtener identidades concretas mediante SearchInstrument.
- `ACCIONES-USA`/`FCI-EXTERIOR` => API_DECLARED; falta ampliar discovery/catalog antes de evaluar cobertura real.

## Catálogo concreto ya observado desde PPI API
Workflow: `34659762851`
Job: `103459630927`
Resultado: SUCCESS

Safety: `ok|PRODUCTION_PAPER|MARKET_CLOSED|0`; `REAL_ORDER_ROUTES=NOT_CALLED`; `MUTATIONS=NONE`.

`instrument_catalog` actual:
- ACCIONES: 55 tickers
- BONOS: 42
- CAUCIONES: 10
- CEDEARS: 191
- FUTUROS: 52
- LETRAS: 23
- ON: 91
- OPCIONES: 382

No había filas de `instrument_catalog` en ese snapshot para ETF, FCI, ACCIONES-USA, FCI-EXTERIOR, LICITACIONES, LEBAC o NOBAC. Esto NO prueba que la API no las maneje: `InstrumentTypes` live sí las declara. Sólo indica que el discovery actual de POROTA todavía no materializó identidades concretas de esas familias o que no había especies encontradas con las semillas actuales.

### Ejemplos confirmados por catálogo API
- CAUCIONES: `PESOS1`, `PESOS2`, `PESOS7`, `PESOS30`, `PESOS120`, `DOLAR1`, `DOLAR2`, `DOLAR7`, `DOLAR30`, `DOLAR120`.
- FUTUROS: múltiples contratos DLR, incluyendo `DLR/ABR27`, `DLR/AGO26`, variantes y spreads.
- OPCIONES: 382 identidades observadas, p.ej. `GFGC10300D`, `GFGC10300O`, `GFGC10300S`.

## Implicación para scraping y Binding
La web puede mostrar más categorías o información contextual que la API. Esa información no debe contaminar el universo operativo.

Regla recomendada para RC6/post-RC6:
`API_DECLARED` -> `API_DISCOVERED` -> contrato enriquecido/reconciliado -> simulador/sizing/exit listo -> candidato PAPER.

El scraping debe enfocarse prioritariamente en identidades `API_DISCOVERED` o, para familias `API_DECLARED` aún sin catálogo, debe ejecutarse después de un discovery API dirigido. Una familia web que no exista en InstrumentTypes live queda como contexto, salvo futura evidencia API explícita.

## Pendiente arquitectónico recomendado
Introducir una autoridad explícita `api_universe`/equivalente con flags:
- `api_declared`
- `api_discovered`
- `simulator_ready`
- `contract_ready`
- `context_only`

Binding/readiness de ejecución debe evaluar sólo identidades `api_discovered=1` y relevantes para una familia integrada; jamás todos los registros scrapeados de la web.
