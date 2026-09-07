# IOL INVERTIRONLINE — RELEVAMIENTO API / MCP PARA POROTA TRADING
**Fecha:** 2026-09-06  
**Estado:** RELEVAMIENTO TÉCNICO COMPLETADO — SIN OPERACIONES  
**Alcance:** autenticación, tokens, conexión, capacidades read-only, riesgos y propuesta de integración para POROTA TRADING.

> **Seguridad:** este documento NO contiene usuario, contraseña, access token, refresh token, cookies, OTP/2FA ni ningún otro secreto. No se ejecutó ninguna compra, venta, caución, FCI, cancelación, stop loss/take profit ni aceptación de DDJJ.

---

## 1. Resumen ejecutivo

InvertirOnline (IOL) ofrece actualmente dos mecanismos técnicamente distintos:

1. **API REST clásica** en `https://api.invertironline.com`
   - Autenticación por `POST /token`.
   - Primer login mediante `username`, `password`, `grant_type=password`.
   - Devuelve un `access_token` (Bearer) y un `refresh_token`.
   - El Bearer tiene vigencia oficial de **15 minutos**.
   - La renovación se hace contra el mismo endpoint `/token` usando `grant_type=refresh_token`.
   - Los recursos autenticados se consumen con `Authorization: Bearer <access_token>`.
   - La API de producción impacta el entorno REAL si se invocan endpoints operativos.
   - IOL dispone además de un **Sandbox API** separado y sin relación con la cuenta productiva.

2. **MCP oficial de IOL** en `https://mcp.invertironline.com`
   - Transporte: **Streamable HTTP**.
   - Autenticación: **OAuth 2.0 con consentimiento del usuario**.
   - Admite **modo solo lectura**.
   - Los permisos pueden revocarse desde IOL.
   - El principio declarado es `default deny`: las capacidades operativas requieren scopes explícitos.
   - La conexión con ChatGPT quedó validada en esta investigación.

### Conclusión clave sobre “el token del menú API”

No se observó un modelo de **API key estática** que deba copiarse desde el menú de IOL. La documentación oficial de REST describe **tokens dinámicos** obtenidos por login (`access_token` + `refresh_token`). El menú `Mi Cuenta > Personalización > APIs` se utiliza para habilitar/aceptar las condiciones del servicio, no como fuente de una clave fija.

Para POROTA TRADING conviene tratar REST y MCP como integraciones separadas:

- **MCP IOL read-only:** excelente para investigación manual, diagnóstico, análisis y validación cruzada desde ChatGPT.
- **REST IOL:** apropiada para un proceso autónomo en el Droplet, siempre que se implemente un cliente estrictamente read-only y un guard de red que bloquee toda ruta operativa excepto `POST /token`.

---

## 2. Activación de la API REST

La documentación oficial de IOL establece:

1. Tener una Cuenta de Inversión abierta.
2. Ingresar al sitio de IOL.
3. Enviar un mensaje a IOL solicitando la activación del producto/API.
4. Esperar confirmación.
5. Ingresar a:
   `Mi Cuenta > Personalización > APIs`
6. Leer y aceptar los Términos y Condiciones.
7. Utilizar exclusivamente HTTPS.

**Importante:** IOL indica expresamente que las acciones de la API de producción impactan el entorno REAL.

---

## 3. REST — autenticación inicial

### Endpoint

```text
POST https://api.invertironline.com/token
Content-Type: application/x-www-form-urlencoded
```

### Body inicial

```text
username=<USUARIO_IOL>
password=<CONTRASEÑA_IOL>
grant_type=password
```

### Ejemplo seguro en Python

```python
import requests

TOKEN_URL = "https://api.invertironline.com/token"

response = requests.post(
    TOKEN_URL,
    data={
        "username": IOL_USERNAME,
        "password": IOL_PASSWORD,
        "grant_type": "password",
    },
    timeout=15,
)
response.raise_for_status()
tokens = response.json()

access_token = tokens["access_token"]
refresh_token = tokens["refresh_token"]
```

**No loguear nunca** `IOL_USERNAME`, `IOL_PASSWORD`, `access_token` ni `refresh_token`.

---

## 4. REST — respuesta de token

La referencia OpenAPI pública/mirror de la API muestra una respuesta con campos equivalentes a:

```json
{
  "access_token": "...",
  "token_type": "bearer",
  "expires_in": 0,
  "refresh_token": "...",
  ".issued": "...",
  ".expires": "...",
  ".refreshexpires": "..."
}
```

La documentación oficial confirma que el Bearer es válido durante **15 minutos**.

---

## 5. REST — uso del Bearer

Cada request protegido debe llevar:

```http
Authorization: Bearer <ACCESS_TOKEN>
```

Ejemplo conceptual:

```python
headers = {"Authorization": f"Bearer {access_token}"}
r = requests.get(
    "https://api.invertironline.com/<recurso-read-only>",
    headers=headers,
    timeout=15,
)
r.raise_for_status()
```

---

## 6. REST — renovación con refresh token

### Endpoint

```text
POST https://api.invertironline.com/token
Content-Type: application/x-www-form-urlencoded
```

### Body

```text
refresh_token=<REFRESH_TOKEN>
grant_type=refresh_token
```

### Ejemplo seguro

```python
response = requests.post(
    "https://api.invertironline.com/token",
    data={
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    },
    timeout=15,
)
response.raise_for_status()
new_tokens = response.json()

access_token = new_tokens["access_token"]
refresh_token = new_tokens["refresh_token"]
```

El cliente debe reemplazar el refresh token anterior por el nuevo cuando IOL entregue un par renovado.

---

## 7. Sandbox API

IOL declara un entorno Sandbox separado:

- seguro y aislado del mercado real;
- permite operaciones simuladas de compra/venta;
- permite cancelaciones simuladas;
- permite asignar fondos ficticios;
- la cuenta Sandbox **no guarda relación** con el usuario ni con los servicios productivos de IOL.

La página pública consultada confirma su existencia pero no expone en texto indexado una URL de Sandbox que podamos declarar como verificada. **No debe inventarse.** Antes de integrar el Sandbox en POROTA hay que obtener la URL/credenciales directamente del onboarding o documentación específica entregada por IOL.

---

## 8. REST — familias de endpoints observadas

La documentación pública/mirror disponible muestra estas familias:

### Cuenta

```text
GET    /api/portafolio
GET    /api/estadocuenta
GET    /api/operaciones
GET    /api/operaciones/{numero}
DELETE /api/operaciones/{numero}
```

Existen variantes/versiones públicas de la API en las que las rutas aparecen con prefijo `/api/v2/...`. POROTA debe implementar configuración por versión y no hardcodear rutas hasta completar un probe contra el producto API habilitado en la cuenta.

### Operación — BLOQUEAR EN POROTA

```text
POST /api/operar/Comprar
POST /api/operar/Vender
```

Cualquier equivalente `/api/v2/operar/...` también debe permanecer bloqueado.

### Instrumentos / títulos

```text
GET /api/{mercado}/Titulos/{simbolo}
GET /api/{mercado}/Titulos/{simbolo}/Opciones
GET /api/{pais}/Titulos/Cotizacion/Instrumentos
GET /api/{pais}/Titulos/Cotizacion/Paneles/{instrumento}
GET /api/Cotizaciones/{Instrumento}/{Panel}/{Pais}
GET /api/{Mercado}/Titulos/{Simbolo}/Cotizacion
GET /api/{mercado}/Titulos/{simbolo}/Cotizacion/seriehistorica/{fechaDesde}/{fechaHasta}/{ajustada}
```

Mercados mostrados en la referencia pública:

```text
BCBA
NYSE
NASDAQ
AMEX
BCS
ROFX
```

Países:

```text
argentina
estados_Unidos
```

Plazos observados en cotización:

```text
t0
t1
t2
t3
```

Instrumentos/paneles observados:

```text
acciones
bonos
opciones
monedas
cauciones
CHPD
futuros
ADRs
obligacionesNegociables
letras
```

La API de panel devuelve, según el instrumento, datos tales como símbolo, descripción, puntas de compra/venta, cantidades, último precio, variación, apertura, máximo, mínimo, cierre, volumen, cantidad de operaciones, fecha, mercado y moneda. Para opciones aparecen también tipo, strike y vencimiento.

La cotización individual incluye además campos como monto operado, volumen nominal, precio promedio, intereses abiertos, plazo, lámina mínima y lote.

La serie histórica permite solicitar `ajustada` o `sinAjustar`.

---

## 9. REST — costo / cantidad de API Calls

La página vigente de tarifas de IOL indica que el servicio API es optativo y requiere habilitación previa.

IOL informa que el uso está **bonificado hasta 25.000 API Calls mensuales**. Superado ese volumen, publica un cargo de **AR$ 500 + IVA**, acompañado por bonificaciones del mismo monto a cuenta de comisiones por transacciones durante los siguientes 30 días.

Para POROTA esto hace necesario:

- cachear catálogos e información estática;
- usar paneles/batches cuando sea posible en lugar de una request por ticker;
- controlar llamadas por endpoint y por fuente;
- no hacer polling agresivo;
- registrar métricas de `api_calls_total`;
- activar budget/alerta antes de consumir el cupo bonificado.

---

## 10. MCP oficial de IOL

### Endpoint

```text
https://mcp.invertironline.com
```

### Características

```text
Transporte: Streamable HTTP
Autenticación: OAuth 2.0
Consentimiento: usuario IOL
Revocación: desde configuración de IOL
Modo: puede configurarse solo lectura
Alcance: cuenta IOL autenticada
```

IOL recomienda 2FA.

Con modo solo lectura, el conector debe exponer únicamente consultas y dejar inhabilitadas las herramientas operativas/aceptación de DDJJ.

---

## 11. MCP — catálogo publicado vs catálogo expuesto hoy

La página pública de documentación enumera **27 herramientas**:
- 15 solo lectura;
- 4 validación/pre-trade;
- 8 operativas.

Sin embargo, la conexión real disponible durante este relevamiento expuso **30 herramientas**. Esto evidencia que el catálogo operativo está evolucionando más rápido que la documentación pública.

### Capacidades read-only observadas en la conexión real

```text
get_portfolio
get_balance
get_order_status
get_asset_quote
get_asset_info
get_price_history
get_intraday_prices
get_fixed_income_analytics
get_options_chain
get_stop_loss_and_take_profit
get_caucion_rates
get_caucion_rate
get_caucion_guarantee_assets
get_fci_funds
get_activities
get_next_corporate_events
get_ddjj
```

Además existe una herramienta especial de disclosure asociada exclusivamente a un caso particular de GGAL; no debe considerarse un endpoint financiero general.

### Validación / pre-trade — NO EJECUTAN pero POROTA debe evitarlas inicialmente

```text
validate_order
validate_caucion
validate_fci_subscription
validate_fci_redemption
```

### Operativas — BLOQUEADAS PARA POROTA

```text
place_order
accept_ddjj
cancel_order
create_stop_loss_or_take_profit
delete_stop_loss_or_take_profit
place_caucion
subscribe_fci
redeem_fci
```

---

## 12. Pruebas read-only realizadas

### 12.1 Datos de referencia — GGAL

La conexión respondió correctamente para `GGAL` en `BCBA`:
- tipo: ACCIONES;
- moneda: ARS;
- lote: 1;
- símbolo ARS relacionado: GGAL;
- símbolo dólar relacionado: GGALD.

### 12.2 Cotización — GGAL

Se obtuvo correctamente último precio, apertura, cierre anterior, máximo, mínimo, variación, volumen/monto, timestamp y puntas bid/ask cuando existen.

Como el relevamiento se realizó **domingo 6 de septiembre de 2026**, la última cotización devuelta correspondió al viernes 4 de septiembre y la caja estaba vacía. Esto confirma que POROTA debe tratar explícitamente `market_closed` y `stale quote`, no confundir ausencia de puntas con falla de API.

### 12.3 Histórico OHLCV — GGAL

Se recuperaron barras diarias con:

```text
date
timestamp
open
high
low
close
volume
```

La consulta de prueba entre 2026-08-20 y 2026-09-04 devolvió correctamente una barra por sesión hábil.

### 12.4 Intraday — hallazgo

La herramienta `get_intraday_prices` está expuesta por el MCP, pero las pruebas sobre 2026-09-03 y 2026-09-04 devolvieron:

```text
intraday_unavailable
```

**CHECKPOINT IOL-INTRADAY-01:** no usar IOL MCP intraday como fuente productiva de velas intradiarias hasta validarlo dentro de mercado y confirmar ventana de retención/completitud.

### 12.5 Renta fija — GD30

La conexión entregó analítica avanzada:
- dirty price;
- clean price;
- interés corrido;
- technical value;
- paridad;
- current yield;
- TIR;
- TEM;
- tasa nominal anual;
- Macaulay duration;
- modified duration;
- cashflow completo;
- saldos residuales;
- fechas de emisión, settlement, cupones y vencimiento.

**Impacto:** IOL puede ser una fuente muy valiosa para validar el motor de renta fija de POROTA, especialmente TIR/duration/cashflows.

### 12.6 Cauciones

La conexión devolvió los próximos plazos hábiles y mínimo operativo.

Para ARS se observó mínimo publicado por la herramienta de:

```text
ARS 100.000
```

Fuera de rueda, las tasas llegaron como `null`.

**Impacto:** no interpretar `null` fuera de mercado como tasa cero.

### 12.7 Opciones — GGAL

La cadena devolvió calls/puts, strike, vencimiento, símbolos y campos para bid/ask, volatilidad implícita, precio teórico, delta, gamma, theta, vega, rho, volumen y variación cuando están disponibles.

Fuera de mercado varios valores/griegas llegaron `null`.

**Impacto:** POROTA debe validar `spot_price`, freshness y horario antes de usar Greeks.

### 12.8 Fondos Comunes de Inversión

Se obtuvo un catálogo de FCI de IOL con:
- descripción;
- tipo;
- símbolo/asset;
- mercado;
- moneda;
- indicador `operable`.

Aparecen fondos en ARS y USD y categorías de plazo fijo, renta fija, renta variable y renta mixta.

---

## 13. Qué NO se consultó durante el relevamiento

Para mantener el alcance mínimo necesario, no se extrajeron ni persistieron:

```text
saldo de cuenta
portfolio personal
historial personal de movimientos
órdenes del usuario
stop loss/take profit personales
DDJJ personales
credenciales
tokens
cookies
OTP/2FA
```

Tampoco se invocó ninguna función de validación pre-trade ni ninguna función operativa.

---

## 14. Arquitectura recomendada para POROTA TRADING

### 14.1 Rol de IOL

Inicialmente IOL debe entrar en POROTA como:

```text
SECONDARY_MARKET_DATA_SOURCE
CROSS_VALIDATION_SOURCE
FIXED_INCOME_ANALYTICS_SOURCE
OPTIONS_ANALYTICS_SOURCE
CAUCION_REFERENCE_SOURCE
CORPORATE_EVENTS_SOURCE
FCI_CATALOG_SOURCE
```

No como broker ejecutor.

### 14.2 Política de órdenes

```text
IOL_ORDER_EXECUTION_MODE=BLOCKED
IOL_REAL_ORDERS_ALLOWED=false
IOL_DDJJ_ACCEPTANCE_ALLOWED=false
IOL_ACCOUNT_MUTATIONS_ALLOWED=false
```

Esta política debe ser **binding**, no meramente informativa.

### 14.3 Guardia HTTP recomendada

Permitir:

```text
GET  recursos read-only
POST /token
```

Bloquear por defecto:

```text
POST cualquier otro endpoint
PUT
PATCH
DELETE
```

Incluso si una nueva versión de la API agrega una ruta operativa que POROTA todavía no conoce, quedará bloqueada por `default deny`.

### 14.4 Secretos

Variables sugeridas:

```text
IOL_API_ENABLED=false
IOL_API_BASE_URL=https://api.invertironline.com
IOL_API_USERNAME=<secret>
IOL_API_PASSWORD=<secret>
IOL_API_READ_ONLY=true
IOL_ORDER_EXECUTION_MODE=BLOCKED
IOL_HTTP_DEFAULT_DENY=true
```

No persistir access token, refresh token o password en Git, logs, dashboard, Telegram ni SQLite en texto plano.

### 14.5 Token manager

Componente sugerido:

```text
IOLTokenManager
```

Responsabilidades:

- login inicial;
- almacenar expiración;
- refresh preventivo;
- una única renovación concurrente;
- retry limitado con backoff;
- no reintentar indefinidamente 401/403;
- invalidar tokens en caso de revocación;
- métricas de login/refresh/error;
- redacción automática de secretos en logs.

### 14.6 Cliente read-only

Componente sugerido:

```text
IOLReadOnlyClient
```

Métodos iniciales:

```text
health()
asset_info()
quote()
historical()
fixed_income_analytics()
options_chain()
caucion_rates()
fci_catalog()
corporate_events()
```

Cualquier método de portfolio/balance debe quedar en un módulo separado y apagado por default para mantener separación entre `market data` y `account data`.

### 14.7 Staleness

Cada respuesta debe normalizar:

```text
source=IOL
source_timestamp
received_at
market_session
is_stale
staleness_seconds
quality_status
```

Reglas:

- mercado cerrado != API caída;
- puntas vacías fuera de mercado != error;
- tasa caución `null` fuera de mercado != 0%;
- Greeks `null` fuera de mercado != 0;
- `intraday_unavailable` != serie vacía válida.

### 14.8 Priorización de fuentes con PPI

No reemplazar PPI de golpe.

```text
PPI primary
   ↓
IOL secondary/cross-check
   ↓
normalizer
   ↓
data quality / discrepancy engine
   ↓
POROTA decision engine
```

Cuando PPI e IOL discrepen:

1. comparar timestamps;
2. comparar plazo de liquidación;
3. comparar mercado/moneda;
4. considerar feriados y sesión;
5. detectar stale;
6. no promediar ciegamente;
7. si no se resuelve, marcar `SOURCE_DISCREPANCY` y evitar decisión dependiente del dato.

---

## 15. Plan de incorporación seguro a RC6

### Fase A — wiring sin secretos productivos

- crear modelos/interfaces;
- agregar env vars;
- agregar HTTP guard;
- tests unitarios;
- ningún request externo.

### Fase B — Sandbox IOL

- obtener URL/credenciales oficiales de Sandbox;
- validar token/refresh;
- validar read-only y errores;
- confirmar que no existe posibilidad de tocar cuenta real.

### Fase C — producción read-only controlada

- habilitar únicamente token + GET;
- usar una cuenta habilitada para API;
- validar catálogo y 3–5 instrumentos canarios;
- registrar calls;
- confirmar `real_orders_sent=0`.

### Fase D — reconciliación PPI/IOL

- cotizaciones;
- históricos diarios;
- renta fija;
- cauciones;
- opciones;
- eventos corporativos;
- FCI.

### Fase E — scheduler

Evitar polling indiscriminado. Separar datos estáticos, catálogo, históricos, quotes, cauciones, opciones y eventos. El scheduler debe respetar market calendars y el presupuesto mensual de API calls.

---

## 16. Checkpoints del relevamiento

```text
IOL-API-01      PASS  REST base URL confirmada
IOL-AUTH-01     PASS  POST /token confirmado
IOL-AUTH-02     PASS  Bearer 15 min confirmado
IOL-AUTH-03     PASS  refresh_token confirmado
IOL-MCP-01      PASS  OAuth 2.0 confirmado
IOL-MCP-02      PASS  conexión ChatGPT/IOL operativa
IOL-MCP-03      PASS  modo read-only soportado por IOL
IOL-MD-01       PASS  quote read-only validado
IOL-HIST-01     PASS  OHLCV diario validado
IOL-FI-01       PASS  analítica GD30 validada
IOL-CAUCION-01  PASS  estructura/plazos/mínimo validados
IOL-OPT-01      PASS  option chain validada
IOL-FCI-01      PASS  catálogo FCI validado
IOL-INTRADAY-01 WARN  intraday_unavailable en prueba fuera de mercado
IOL-ORDERS-01   PASS  no se invocó ninguna función operativa
IOL-SECRETS-01  PASS  ningún secreto guardado en este documento
```

---

## 17. Veredicto

**IOL es técnicamente apto para incorporarse a POROTA TRADING como fuente secundaria read-only y de validación.**

La incorporación tiene valor especialmente alto en renta fija, cadena de opciones, cauciones, OHLCV, catálogo FCI, eventos corporativos y validación cruzada de cotizaciones.

No recomiendo habilitar ejecución de órdenes IOL en RC6. Primero debe existir evidencia de guard HTTP binding, token manager seguro, control de staleness, presupuesto de API calls, reconciliación PPI/IOL, zero-real-orders test y rollback.

Para runtime autónomo del Droplet, la vía más directa es la **API REST con cliente read-only y default-deny**. El **MCP OAuth de IOL** debe conservarse como canal seguro de análisis/diagnóstico humano y, eventualmente, evaluarse como backend programático separado si se define un flujo OAuth apropiado para servicio.

---

## 18. Fuentes consultadas

### Oficiales IOL

- API REST / documentación:  
  `https://api.invertironline.com/`

- Autenticación REST:  
  `https://api.invertironline.com/Help/Autenticacion`

- MCP IOL — documentación técnica:  
  `https://mcp.invertironline.com/documentacion-tecnica`

- MCP IOL — página de producto:  
  `https://www.invertironline.com/mcp`

- Trading Tools / activación API:  
  `https://iol.invertironline.com/Research/TradingTools`

- Tarifas / API calls:  
  `https://iol.invertironline.com/servicios/tarifas`

### Referencia OpenAPI pública no oficial usada únicamente para mapear schemas/rutas

- `https://iol.apidocs.ar/`

**Criterio de confianza:** para autenticación, seguridad, activación, MCP y tarifas prevalecen siempre las páginas oficiales de IOL. Las rutas obtenidas de referencias no oficiales deben verificarse mediante el playground/API habilitada antes de incorporarlas a producción.

---

## 19. Próximo paso técnico propuesto

Crear en el repositorio RC6:

```text
iol_token_manager.py
iol_readonly_client.py
iol_normalizer.py
iol_source_health.py
iol_ppi_reconciler.py
tests/test_iol_http_guard.py
tests/test_iol_token_manager.py
tests/test_iol_readonly_client.py
```

Y configurar un guard estricto:

```text
ALLOW:
  POST https://api.invertironline.com/token
  GET  https://api.invertironline.com/**

DENY:
  POST /**
  PUT  /**
  PATCH /**
  DELETE /**
```

La regla específica `POST /token` debe evaluarse antes del deny general.

---

**FIN DEL RELEVAMIENTO**
