# POROTA TRADING — CHECKPOINT P1 EVENT RISK + PREOPEN/POSTCLOSE + BACKTEST — 2026-09-06

## 1. Decisión

Se incorpora como **P1 estratégica** una nueva línea de trabajo para POROTA TRADING RC6:

1. nuevo menú independiente de dashboard: **EVENTOS / RIESGO GLOBAL**;
2. ingesta read-only de fuentes cualitativas / event-driven;
3. primera fase estrictamente **SHADOW / observacional**;
4. etiquetado de eventos y exposición por instrumento;
5. backtesting del comportamiento del instrumento después de eventos;
6. tratamiento explícito de **preopen / apertura / cierre / postclose** como regímenes diferentes;
7. posible evaluación futura de un `EVENT_RISK_GATE`, pero **sin autoridad BINDING en RC6**.

Esta línea complementa el motor cuantitativo. No lo reemplaza.

No se habilita real-money. No se modifica el observer live por este checkpoint.

---

## 2. Problema que se quiere resolver

El motor matemático puede medir precio, spread, momentum, volatilidad, expectancy, riesgo, liquidez e históricos, pero puede reaccionar tarde frente a shocks exógenos como:

- guerras y escaladas;
- treguas / ruptura de treguas;
- sanciones;
- bloqueos o restricciones marítimas;
- ataques a infraestructura energética;
- decisiones OPEC/OPEC+;
- decisiones de bancos centrales;
- intervenciones cambiarias;
- defaults / reestructuraciones;
- corporate actions;
- filings regulatorios inesperados;
- desastres naturales;
- cyber incidents;
- cierres o restricciones de mercado;
- eventos políticos / regulatorios de alta materialidad.

Caso real de referencia al 2026-09-06: escalada EE.UU.-Irán / Strait of Hormuz, caída del tránsito de commodities, ataques a tankers y petróleo al alza; simultáneamente OPEC+ mantiene la política de producción para octubre. Este tipo de combinación puede alterar la relación histórica normal entre señales cuantitativas y retorno futuro.

---

## 3. Nuevo menú de dashboard

Nombre propuesto:

**Eventos / Riesgo Global**

Debe ser un menú separado de `/vivo`, `/validacion`, históricos y learning.

### Contenido mínimo

- `GLOBAL_EVENT_RISK`: LOW / MODERATE / HIGH / EXTREME;
- timestamp de última actualización;
- fuente / tier de confianza;
- eventos activos;
- estado: RUMOR / MULTI_SOURCE / OFFICIAL_CONFIRMED;
- región: ASIA / EUROPE / US / LATAM / MIDDLE_EAST / GLOBAL;
- categoría;
- resumen factual corto;
- entidades / países / commodities afectados;
- exposición inferida;
- instrumentos POROTA potencialmente relacionados;
- dirección **no operativa**: POSITIVE_BIAS / NEGATIVE_BIAS / MIXED / UNKNOWN;
- nivel de confianza;
- freshness;
- enlaces/provenance;
- estado SHADOW;
- retorno observado posterior al evento cuando exista;
- resultados históricos para eventos similares, cuando el laboratorio los produzca.

No mostrar recomendaciones BUY/SELL en esta fase.

---

## 4. Fuentes investigadas — primera selección

### 4.1 GDELT — P1 sensor global

**Rol:** detección global de acontecimientos, narrativa y aceleración de cobertura.

Hallazgos:

- proyecto abierto/gratuito;
- cobertura mundial y multilingüe;
- GDELT 2.0 incorpora 65 idiomas traducidos en vivo;
- actualización aproximada cada 15 minutos;
- DOC/Context/GEO APIs;
- Context API permite buscar coincidencias a nivel de oración, reduciendo falsos positivos de artículos donde términos aparecen desconectados;
- adecuado para guerra, treguas, sanciones, protestas, energía, Asia, geopolítica y eventos globales.

**Uso POROTA inicial:** read-only, canario / detector, nunca fuente única de confirmación BINDING.

### 4.2 Marketaux — P1/P2 enriquecimiento financiero

Hallazgos del plan gratuito al 2026-09-06:

- USD 0;
- 100 requests/día;
- 3 artículos por request;
- acceso instantáneo a noticias;
- 5.000+ fuentes;
- 80+ mercados;
- 30+ idiomas;
- metadata completa;
- universo amplio de entidades financieras.

**Rol:** complementar GDELT con entity tagging / financial-news context.

### 4.3 Fuentes oficiales — P1 de confirmación

Fuentes oficiales de primera mano deben tener mayor confianza que agregadores:

- Federal Reserve RSS: monetary policy, press releases, speeches/testimony;
- Bank of Japan RSS + release schedule;
- ECB RSS;
- OPEC/OPEC+ comunicados oficiales;
- SEC EDGAR `data.sec.gov` para submissions/XBRL sin API key para las APIs públicas de datos;
- OFAC Sanctions List Service / XML / CSV / cambios;
- EIA Open Data para energía/petróleo/gas; API gratuita con registro/key y bulk sin key;
- FRED para macro, con API key;
- futuras fuentes oficiales argentinas: BCRA, CNV, BYMA, A3 y emisoras cuando haya endpoints/feeds adecuados.

### 4.4 Política de confianza

Tier sugerido:

- `TIER_A_OFFICIAL`: regulador, exchange, banco central, OPEC/EIA/SEC/OFAC, emisor oficial;
- `TIER_B_MULTI_SOURCE_CONFIRMED`: >=2 fuentes independientes de alta reputación y consistentes;
- `TIER_C_SINGLE_SOURCE`: una fuente periodística/agregador;
- `TIER_D_RUMOR_UNVERIFIED`: rumor/social/no confirmado.

Durante SHADOW ningún tier genera por sí solo órdenes.

---

## 5. Event Risk Engine — arquitectura objetivo

Pipeline:

`SOURCE INGEST`
`→ NORMALIZATION`
`→ DEDUPLICATION`
`→ EVENT CLASSIFICATION`
`→ SOURCE CONFIDENCE`
`→ ENTITY / EXPOSURE GRAPH`
`→ FRESHNESS / NOVELTY`
`→ EVENT RISK SCORE`
`→ INSTRUMENT EXPOSURE`
`→ SHADOW LABEL`
`→ BACKTEST / FORWARD OBSERVATION`

### Clases iniciales

- WAR_ESCALATION
- CEASEFIRE
- CEASEFIRE_BREAKDOWN
- SANCTIONS
- OIL_SUPPLY_SHOCK
- SHIPPING_DISRUPTION
- ENERGY_INFRA_ATTACK
- CENTRAL_BANK
- FX_INTERVENTION
- REGULATORY
- CORPORATE_FILING
- CORPORATE_ACTION
- DEFAULT_RESTRUCTURING
- NATURAL_DISASTER
- CYBER_INCIDENT
- MARKET_HALT
- POLITICAL_SHOCK

### Score conceptual

`EVENT_RISK = severity × confidence × freshness × exposure × novelty`

No fijar coeficientes arbitrarios antes de la campaña histórica/SHADOW.

---

## 6. Exposure Graph — evitar "noticia = ticker"

El evento no se debe mapear directamente a BUY/SELL.

Ejemplo:

`STRAIT_OF_HORMUZ_DISRUPTION`
→ crude_supply_risk
→ Brent/WTI pressure
→ shipping costs
→ inflation pressure
→ rates sensitivity
→ energy equities
→ airlines / transport
→ currencies / sovereign risk

Cada instrumento puede tener signo y magnitud diferentes.

Ejemplo hipotético:

- YPFD: energía + Argentina + regulación local + FX;
- CEDEAR de aerolínea: costo combustible + USD + mercado US;
- banco argentino: relación indirecta vía macro/riesgo;
- bono: inflación/tasas/riesgo país;
- CEDEAR tecnológico: sensibilidad a tasas/risk-off más que petróleo directo.

El mapping debe tener provenance y versionado.

---

## 7. Regímenes de mercado argentino — no tratarlos como minutos normales

### 7.1 Estado horario actual investigado

BYMA mantiene página oficial de horarios y publicó el Comunicado 19016 el 2026-09-01 como referencia vigente de horarios.

Fuentes accesibles actuales de mercado/brokers corroboran:

- rueda argentina regular principal: aproximadamente 10:30–17:00 AR para acciones/CEDEAR y numerosos instrumentos;
- Plazo Normal y Contado Inmediato operan 10:30–17:00 en IOL;
- existen segmentos especiales por subasta / negociación concentrada con horarios propios;
- en el esquema BYMA vigente desde 2025, el segmento estándar utiliza subasta de apertura alrededor de 10:25–10:30;
- para instrumentos/modos con cierre por subasta existe una ventana corta de cierre alrededor de 16:57–17:00;
- existe negociación a precio de cierre cuando es aplicable, típicamente posterior a la publicación del precio de cierre;
- instrumentos de negociación por subasta de baja liquidez mantienen ventanas distintas (apertura, intermedia, cierre);
- A3 posee fases explícitas Pre / Negociación / Post según producto; por ejemplo varios productos OTROS: Pre 10:00–10:30, Negociación 10:30–17:00, Post 17:00–17:15.

**Regla POROTA:** no hardcodear universalmente una única fase para todas las familias. Cada identidad financiera debe resolver su `market_session_profile` desde fuente oficial/versionada.

### 7.2 PREOPEN / OVERNIGHT

El preopen POROTA debe distinguir:

1. `OVERNIGHT_WORLD_SCAN`;
2. `LOCAL_PREOPEN_CONTROL`;
3. `OPENING_AUCTION_WINDOW` cuando corresponda.

Objetivo: llegar a 10:30 con contexto, no descubrir el shock después del primer fill PAPER.

Features propuestas:

- noticias/eventos desde cierre anterior;
- Asia overnight;
- Europa matinal;
- Fed/BOJ/ECB/OPEC/SEC/OFAC/EIA;
- estado mercado US y subyacentes CEDEAR;
- futuros/commodities de referencia donde haya fuente válida;
- gap indicativo vs cierre previo;
- quote freshness;
- auction/book imbalance **sólo si la fuente realmente lo provee**;
- bid/ask/spread/depth de preopen **sólo si observables**;
- corporate actions;
- calendarios de mercado local/subyacente;
- evento global activo;
- `OVERNIGHT_EVENT_RISK`.

Si una feature no existe, estado `UNKNOWN/NOT_AVAILABLE`; nunca inventarla.

### 7.3 OPENING REGIME

La apertura debe ser tratada como régimen propio por alta sensibilidad a:

- overnight gap;
- acumulación de órdenes;
- price discovery;
- spreads iniciales;
- profundidad irregular;
- noticias recientes;
- desalineación CEDEAR/subyacente/FX;
- posible reversión del gap o continuación.

Propuestas SHADOW a evaluar:

- `OPENING_VOLATILITY_GUARD`;
- `OPENING_GAP_RISK`;
- `OPENING_LIQUIDITY_RISK`;
- `EVENT_PLUS_GAP_INTERACTION`;
- espera dinámica X minutos sólo si la evidencia histórica demuestra que mejora expectancy neta.

No fijar X=5/10/15 arbitrariamente sin backtest.

### 7.4 CLOSING REGIME

El cierre también es un régimen diferente.

Según instrumento puede existir cierre por VWAP, subasta o precio de cierre, y no debe confundirse `last trade` con `official close`.

Features objetivo:

- último precio regular;
- official close;
- close method;
- VWAP de la ventana aplicable cuando corresponda;
- diferencia last-vs-close;
- spread/depth antes del cierre;
- imbalance cuando observable;
- volumen de cierre / participación;
- aceleración de volatilidad final;
- evento ocurrido en últimos 30/15/5 min;
- relación CEDEAR/subyacente/FX;
- posición abierta y riesgo overnight.

### 7.5 POSTCLOSE

El postclose no debe iniciar nuevas decisiones intradiarias como si la rueda siguiera abierta.

Debe servir para:

- reconciliación;
- capturar official close;
- validar PnL/marks;
- completar historical ingestion;
- capturar noticias posteriores al cierre;
- etiquetar eventos overnight;
- generar learning samples;
- preparar próximo preopen;
- generar brief de riesgo global;
- producir labels de backtest.

Cuando un mercado extranjero relevante continúe abierto por timezone/DST, debe modelarse como `UNDERLYING_OPEN_LOCAL_CLOSED`, no como una única sesión global.

---

## 8. Cómo puede POROTA "interferir positivamente"

La interpretación autorizada es **mejorar la toma de decisiones y control de riesgo**, nunca manipular mercado/precio.

Mecanismos candidatos, primero SHADOW:

1. elevar `risk_state` ante eventos de alta severidad;
2. reducir confianza de señales cuando el régimen histórico deja de ser comparable;
3. colocar `HOLD_NEW_ENTRY_SHADOW` para instrumentos altamente expuestos;
4. evitar perseguir gaps extremos hasta verificar liquidez/price discovery;
5. elevar requisito de evidencia para abrir cerca de apertura/cierre;
6. forzar `UNKNOWN` cuando falta quote/book/fuente confiable;
7. advertir riesgo overnight antes del cierre;
8. cerrar el día con mapa de eventos que pueden explicar PnL anormal;
9. preparar el siguiente preopen con eventos posteriores al cierre;
10. eventualmente, sólo con evidencia suficiente, proponer un gate BINDING versionado y aprobado humanamente.

---

## 9. Backtesting Event-Driven — P1

Esta línea queda incorporada formalmente al checkpoint de testing/backtesting.

### 9.1 Data model de eventos

Cada evento histórico debe guardar:

- `event_id`;
- `event_type`;
- `first_seen_at`;
- `published_at`;
- `confirmed_at`;
- `source`;
- `source_tier`;
- `source_hash`/URL/provenance;
- región;
- entidades;
- commodities/factores;
- severity;
- confidence;
- novelty;
- dedup group;
- affected exposures;
- mapping version;
- retractions/corrections;
- `available_to_engine_at`.

**Regla anti-look-ahead:** en backtest sólo puede utilizarse lo que era observable al `available_to_engine_at` original. Una confirmación posterior no puede retrotraerse artificialmente.

### 9.2 Horizons de medición

Cuando exista granularidad suficiente:

- +5 min;
- +15 min;
- +30 min;
- +60 min;
- +120 min;
- EOD;
- next open;
- next close;
- 2D/5D cuando sea pertinente.

Con OHLCV diario solamente, no afirmar efectos intradiarios; usar next-open/close/return compatibles con la granularidad.

### 9.3 Métricas

Por evento/instrumento/exposición:

- abnormal return vs baseline;
- raw return;
- MFE;
- MAE;
- spread/slippage cuando exista microestructura;
- gap de apertura;
- first 5/15/30m range;
- reversal vs continuation;
- volumen relativo;
- volatility expansion;
- drawdown posterior;
- false positive;
- false negative;
- blocked winner / avoided loser en SHADOW;
- persistencia por régimen;
- resultado por familia;
- resultado por trading_day_ar;
- resultado por source tier;
- resultado por severity/confidence;
- evento aislado vs evento combinado.

### 9.4 Estudios específicos

- WAR_ESCALATION → energía / CEDEAR / riesgo global;
- CEASEFIRE → reversión/continuación por exposición;
- OIL_SUPPLY_SHOCK → YPFD / transporte / inflación-sensitive;
- sanctions → empresas/países/commodities relacionados;
- central bank surprise → bancos, bonos, FX-sensitive, CEDEAR;
- Asia shock overnight → gap de apertura BYMA;
- breaking event cerca de 16:30–17:00 → riesgo overnight;
- event release durante preopen → apertura / first 30m;
- official confirmation vs rumor → diferencia de comportamiento.

---

## 10. Backtesting de PREOPEN / OPEN / CLOSE / POSTCLOSE

### PREOPEN STUDY

Medir:

- `overnight_return_reference`;
- news/event count y acceleration;
- `OVERNIGHT_EVENT_RISK`;
- gap apertura vs cierre previo;
- relación gap ↔ retorno 5/15/30/60m;
- gap continuation vs mean reversion;
- opening spread/depth si disponible;
- impacto de día con US cerrado/local abierto;
- impacto de Asia/Europa shock.

### OPENING STUDY

Preguntas:

- ¿entrar inmediatamente empeora fills?
- ¿qué ventanas de espera mejoran expectancy neta?
- ¿depende de símbolo/familia/regime?
- ¿un gap con noticia confirmada se comporta distinto a un gap sin noticia?
- ¿la apertura de CEDEAR cambia cuando el subyacente US todavía no abrió?

No imponer una espera fija hasta responder estadísticamente.

### CLOSING STUDY

Preguntas:

- ¿últimos 30/10/3 minutos presentan edge o riesgo adicional según close method?
- ¿qué diferencia hay entre last trade y official close?
- ¿qué posiciones sufren mayor gap overnight?
- ¿un evento fresco cerca del cierre justifica elevar riesgo?
- ¿la relación con el subyacente/FX queda desalineada al cierre?

### POSTCLOSE STUDY

Medir:

- eventos entre cierre AR y siguiente preopen;
- retorno de subyacentes/commodities extranjeros mientras BYMA está cerrada;
- next-day gap;
- relación evento nocturno → gap → comportamiento first 30/60m;
- capacidad del overnight brief para identificar días anómalos.

---

## 11. Overnight Global Risk Brief

Propuesta de nuevo artefacto automático read-only antes del preopen:

### ASIA
- Japón / BOJ;
- China/Hong Kong;
- Corea;
- eventos geopolíticos;
- commodities / shipping relevantes.

### EUROPA
- ECB;
- energía;
- riesgo geopolítico;
- principales cambios regulatorios.

### US
- Fed;
- SEC filings;
- sanciones/OFAC;
- underlying de CEDEAR;
- macro relevante.

### ENERGY
- OPEC/OPEC+;
- EIA;
- petróleo / gas;
- Hormuz / Red Sea / refinerías / shipping.

### OUTPUT

- `GLOBAL_EVENT_RISK`;
- top events;
- source tier;
- affected exposures;
- POROTA identities impacted;
- `SHADOW_WARNING`;
- sin BUY/SELL.

Horario exacto a decidir con scheduler después de probar tiempos de captura y preopen; no hardcodear todavía.

---

## 12. Seguridad / gobernanza

Fase 1:

`READ_ONLY = TRUE`
`SHADOW = TRUE`
`CAN_BLOCK_PAPER = FALSE`
`CAN_SEND_ORDER = FALSE`
`AUTO_PROMOTION = FALSE`

No modificar intraday AI policy: `IA_INTRADIARIA=OFF` continúa.

Clasificación/event scoring inicial debe poder ejecutarse determinísticamente en Python.

La IA, si se usa posteriormente, será para análisis/revisión offline o extracción controlada, no como autoridad intradiaria automática sin una nueva decisión versionada.

---

## 13. Criterio de promoción futura

Un futuro `EVENT_RISK_GATE` sólo podría pasar de SHADOW a BINDING si existe evidencia suficiente de:

- precision/recall adecuados;
- estabilidad temporal;
- estabilidad por familias;
- avoided losses;
- blocked winners cuantificados;
- net expectancy after costs;
- impacto en drawdown;
- falsos positivos aceptables;
- robustez out-of-sample;
- replay sin look-ahead;
- PAPER forward;
- provenance completa;
- revisión humana;
- rollback.

Nunca por un caso anecdótico aislado.

---

## 14. Fuentes web investigadas

- GDELT data / GKG 2.0: https://gdeltproject.org/data.html
- GDELT Context 2.0 API: https://blog.gdeltproject.org/announcing-the-gdelt-context-2-0-api/
- Marketaux pricing: https://www.marketaux.com/pricing
- Federal Reserve RSS: https://www.federalreserve.gov/feeds/feeds.htm
- Bank of Japan news/RSS: https://www.boj.or.jp/en/whatsnew/
- Bank of Japan release schedule: https://www.boj.or.jp/en/about/calendar/index.htm
- ECB RSS: https://www.ecb.europa.eu/home/html/rss.en.html
- SEC EDGAR public data APIs: https://www.sec.gov/search-filings/edgar-application-programming-interfaces
- OFAC Sanctions List Service: https://ofac.treasury.gov/sanctions-list-service
- EIA Open Data: https://www.eia.gov/opendata/
- FRED API: https://fred.stlouisfed.org/docs/api/fred/
- OPEC official press releases: https://www.opec.org/
- BYMA horarios: https://www.byma.com.ar/mercado/horarios
- BYMA comunicado vigente de horarios al 2026-09-01: Comunicado 19016
- IOL guía de horarios e instrumentos: sitio IOL / Auto Gestión
- A3 Datos de Mercado / horarios: https://a3mercados.com.ar/info-de-mercado/datos-de-mercado

---

## 15. Prioridad / semáforo

`EVENT_RISK_MENU = P1_OPEN`
`EVENT_RISK_ENGINE = P1_RESEARCH_DESIGN`
`EVENT_RISK_MODE = SHADOW_ONLY`
`EVENT_BACKTEST = P1_OPEN`
`PREOPEN_REGIME_BACKTEST = P1_OPEN`
`CLOSING_REGIME_BACKTEST = P1_OPEN`
`POSTCLOSE_OVERNIGHT_BACKTEST = P1_OPEN`
`EVENT_RISK_BINDING = NOT_AUTHORIZED`
`REAL_MONEY = NO_GO`

---

## 16. Impacto runtime

Este checkpoint es documental/de diseño/backtesting.

- observer live: sin cambios;
- dashboard live: sin cambios;
- estrategia live: sin cambios;
- thresholds: sin cambios;
- gates: sin promoción;
- orders: ninguna;
- PPI/IOL credentials: sin cambios;
- `real_orders_sent=0` permanece invariante.

Observer frozen de referencia RC6: `db26c76723bb988c956589c572b87cbcb4191731`.
