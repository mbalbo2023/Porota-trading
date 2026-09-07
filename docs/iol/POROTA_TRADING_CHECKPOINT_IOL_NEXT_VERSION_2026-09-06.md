# POROTA TRADING — CHECKPOINT IOL / PENDIENTE PRÓXIMA VERSIÓN

**Fecha:** 2026-09-06  
**Base documental:** `checkpoint/rc6-20260906-posthotfix`  
**Rama de documentación:** `docs/iol-next-version-20260906`  
**Runtime base identificado:** `17.0.0-rc6` / `PRODUCTION_PAPER`  
**Estado:** DOCUMENTADO — NO IMPLEMENTADO — NO DESPLEGADO  
**Prioridad propuesta:** P1 próxima versión posterior a RC6  

---

## 1. Objetivo

Dejar formalmente registrado para la próxima versión de POROTA TRADING el relevamiento de integración con **IOL InvertirOnline**, incluyendo API REST, MCP OAuth 2.0, autenticación/tokens, capacidades read-only, riesgos, controles obligatorios y estrategia de despliegue.

Este checkpoint **no autoriza** integrar IOL en RC6, no modifica el runtime actual y no habilita ejecución real.

---

## 2. Evidencia incorporada

Se incorporan como documentos canónicos de este pendiente:

1. `docs/iol/IOL_API_MCP_FULL_RESEARCH_POROTA_TRADING_2026-09-06.md`
   - relevamiento técnico completo;
   - autenticación y renovación de tokens;
   - REST vs MCP;
   - familias de datos disponibles;
   - pruebas read-only realizadas;
   - staleness y calidad;
   - política de secretos;
   - propuesta de arquitectura PPI + IOL.

2. `docs/iol/IOL_CAPABILITY_MAP_POROTA_TRADING_2026-09-06.json`
   - mapa machine-readable de capacidades verificadas;
   - políticas de seguridad propuestas;
   - estado PASS/WARN de las pruebas read-only.

No se almacenan usuario, contraseña, access token, refresh token, cookies, OTP/2FA ni datos privados de la cuenta.

---

## 3. Hallazgos que pasan a backlog

### IOL-NEXT-01 — Integración read-only

Incorporar IOL inicialmente sólo como:

- `SECONDARY_MARKET_DATA_SOURCE`;
- `CROSS_VALIDATION_SOURCE`;
- `FIXED_INCOME_ANALYTICS_SOURCE`;
- `OPTIONS_ANALYTICS_SOURCE`;
- `CAUCION_REFERENCE_SOURCE`;
- `CORPORATE_EVENTS_SOURCE`;
- `FCI_CATALOG_SOURCE`.

**No convertir IOL en broker ejecutor en la primera versión de integración.**

### IOL-NEXT-02 — Guard HTTP binding

Política obligatoria `default deny`:

```text
ALLOW:
  POST https://api.invertironline.com/token
  GET  https://api.invertironline.com/**

DENY:
  POST /**   (excepto /token)
  PUT  /**
  PATCH /**
  DELETE /**
```

El control debe ser binding y probado; no alcanza una bandera informativa.

### IOL-NEXT-03 — Token manager

Implementar `IOLTokenManager` con:

- login inicial;
- expiración explícita;
- refresh preventivo;
- rotación del refresh token cuando corresponda;
- single-flight lock para evitar múltiples refresh concurrentes;
- backoff limitado;
- tratamiento fail-closed de 401/403;
- redacción de secretos en logs;
- métricas de login/refresh/error;
- cero tokens en dashboard/Telegram/Git/SQLite plano.

### IOL-NEXT-04 — Cliente read-only

Implementar `IOLReadOnlyClient` con una superficie inicial acotada:

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

Portfolio, balance, actividades y cualquier dato de cuenta deben quedar en módulo separado y deshabilitado por defecto.

### IOL-NEXT-05 — Normalización y reconciliación PPI/IOL

No reemplazar PPI de forma abrupta.

Flujo objetivo:

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

Ante discrepancias:

1. comparar timestamps;
2. verificar settlement term;
3. verificar mercado y moneda;
4. verificar calendario/sesión;
5. evaluar staleness;
6. no promediar fuentes ciegamente;
7. marcar `SOURCE_DISCREPANCY` y abstenerse si el dato es material para la decisión.

### IOL-NEXT-06 — Control de API calls

La integración deberá:

- medir `api_calls_total`;
- separar consumo por endpoint/familia;
- cachear catálogos y estáticos;
- priorizar batch/panel cuando sea posible;
- evitar polling agresivo;
- generar warning antes de alcanzar el cupo mensual vigente publicado por IOL;
- no hardcodear tarifas/cupos sin una fuente/versionado vigente.

### IOL-NEXT-07 — Intraday pendiente

La capacidad MCP `get_intraday_prices` existe pero durante el relevamiento devolvió `intraday_unavailable` para las sesiones probadas.

**Bloqueo:** no utilizar IOL intraday como feed productivo hasta:

- probar durante mercado abierto;
- medir ventana de retención;
- verificar granularidad;
- comprobar gaps/completitud;
- documentar timestamps y timezone;
- validar comportamiento por feriados y sesiones parciales.

### IOL-NEXT-08 — Renta fija

Alta prioridad por calidad de los datos observados:

- dirty/clean price;
- accrued interest;
- technical value;
- paridad;
- current yield;
- TIR;
- TEM;
- duration Macaulay/modificada;
- cashflows;
- fechas contractuales.

Usar inicialmente como validación cruzada del motor de renta fija de POROTA.

### IOL-NEXT-09 — Opciones

La cadena puede aportar:

- calls/puts;
- strikes;
- expirations;
- bid/ask;
- IV;
- theoretical price;
- delta/gamma/theta/vega/rho.

Los valores `null` fuera de rueda no deben transformarse a cero. Antes de utilizar Greeks se debe validar spot, timestamp y sesión.

### IOL-NEXT-10 — Cauciones

La integración debe distinguir:

- tasa nula fuera de rueda;
- tasa cero real;
- plazo;
- moneda;
- mínimo;
- vencimiento;
- colocadora vs tomadora.

La primera versión de POROTA continúa con política conservadora y no incorpora caución tomadora/endeudamiento por esta integración.

---

## 4. Archivos de código propuestos para próxima versión

```text
iol_token_manager.py
iol_readonly_client.py
iol_normalizer.py
iol_source_health.py
iol_ppi_reconciler.py

tests/test_iol_http_guard.py
tests/test_iol_token_manager.py
tests/test_iol_readonly_client.py
tests/test_iol_normalizer.py
tests/test_iol_ppi_reconciler.py
```

Los nombres son propuesta arquitectónica; deben ajustarse a la convención final del repositorio cuando se implemente.

---

## 5. Variables de configuración propuestas

```text
IOL_API_ENABLED=false
IOL_API_BASE_URL=https://api.invertironline.com
IOL_API_USERNAME=<secret>
IOL_API_PASSWORD=<secret>
IOL_API_READ_ONLY=true
IOL_ORDER_EXECUTION_MODE=BLOCKED
IOL_REAL_ORDERS_ALLOWED=false
IOL_DDJJ_ACCEPTANCE_ALLOWED=false
IOL_ACCOUNT_MUTATIONS_ALLOWED=false
IOL_HTTP_DEFAULT_DENY=true
```

**Nunca** almacenar valores reales en GitHub.

---

## 6. Despliegue sugerido para la próxima versión

### Fase 0 — documentación y diseño

Estado actual: **COMPLETADO**.

- documentación técnica;
- capability map;
- backlog/checkpoint;
- ninguna modificación runtime;
- ninguna credencial publicada.

### Fase 1 — implementación offline

Crear en rama feature de próxima versión.

Condiciones:

- sin credenciales IOL;
- sin red externa durante tests;
- mocks/fixtures únicamente;
- HTTP guard implementado antes que el cliente;
- token manager probado sin secretos reales;
- serializers y redaction tests;
- ningún método de órdenes en el cliente inicial.

**Gate:** CI GREEN + `real_orders_sent=0` + ningún secreto detectado.

### Fase 2 — Sandbox IOL

Sólo después de obtener de IOL la URL y credenciales Sandbox oficiales.

Validar:

- login;
- token TTL;
- refresh;
- 401/403/429/5xx;
- timeout/retry;
- cotizaciones/históricos;
- familias disponibles;
- presupuesto de llamadas;
- guard que impida toda ruta no autorizada.

Aunque Sandbox permita operaciones simuladas, la primera prueba del cliente POROTA debe seguir siendo read-only.

**Gate:** `IOL_SANDBOX_READONLY=GREEN`.

### Fase 3 — producción read-only controlada

No activar desde un push normal.

Usar GitHub Actions con secretos protegidos y SSH estricto, siguiendo el patrón operativo ya utilizado por POROTA:

1. validar referencia exacta de release;
2. validar versión esperada;
3. secret scan;
4. pytest focal + integral;
5. build imagen candidata;
6. preflight Droplet;
7. confirmar modo `PRODUCTION_PAPER`;
8. confirmar órdenes reales bloqueadas;
9. activar secretos IOL sólo en runtime;
10. probar exclusivamente endpoints read-only canarios;
11. health/postflight;
12. verificar `real_orders_sent=0`;
13. rollback automático ante fallo.

Canarios sugeridos:

```text
GGAL   acción
GD30   renta fija
una caución de referencia
una cadena de opciones
un FCI de catálogo
```

**No ejecutar validate/place/cancel/order/DDJJ/FCI/caución operativa.**

### Fase 4 — shadow/cross-validation

IOL todavía no interviene en decisiones.

Registrar:

```text
source=PPI
source_secondary=IOL
ppi_timestamp
iol_timestamp
price_diff_abs
price_diff_bps
settlement_term_match
currency_match
market_match
staleness
resolution
```

Duración sugerida: varias ruedas reales incluyendo al menos un día normal y, si es posible, una sesión con comportamiento distinto/feriado de EE.UU. relevante para CEDEARs.

**Gate:** discrepancias explicadas y sin false freshness.

### Fase 5 — binding para calidad, no para ejecución

Una vez validada la reconciliación, IOL puede participar en los gates de calidad:

- si PPI está stale y IOL fresco: degradar/abstener según familia;
- si IOL está stale y PPI fresco: no bloquear por IOL;
- si ambos divergen materialmente sin explicación: `SOURCE_DISCREPANCY` y abstención;
- no promediar precios sin política explícita.

### Fase 6 — evaluación de uso ampliado

Sólo en versión posterior y con aprobación explícita del usuario evaluar:

- account balance;
- portfolio;
- activities;
- validaciones pre-trade;
- cualquier capacidad operativa.

La existencia técnica de una función operativa **no constituye autorización** para habilitarla.

---

## 7. GitHub Actions sugerido

Nombre tentativo:

```text
.github/workflows/iol-readonly-candidate.yml
```

Debe incluir como mínimo:

```text
workflow_dispatch manual
release ref exacta
confirmación explícita IOL_READONLY_DEPLOY
secret scan
pytest tests/test_iol_*.py
HTTP guard tests
build Docker exacto
preflight runtime
PRODUCTION_PAPER obligatorio
IOL_ORDER_EXECUTION_MODE=BLOCKED obligatorio
IOL_REAL_ORDERS_ALLOWED=false obligatorio
postflight health
real_orders_sent=0 obligatorio
rollback automático
```

No exponer secretos en output ni artifacts.

---

## 8. Observabilidad / dashboard

Agregar a Salud/APIs:

```text
IOL enabled
IOL mode = READ_ONLY
REST auth status
last token refresh status (sin token)
last successful call
last source timestamp
staleness
calls today/month
429 count
401/403 count
5xx count
PPI vs IOL discrepancy state
```

En `/en-vivo`, mostrar IOL sólo cuando intervenga en la calidad de una decisión; no duplicar datos sin contexto.

Telegram debe alertar únicamente eventos accionables, por ejemplo:

- auth IOL persistente fallida;
- rate limit sostenido;
- discrepancia material PPI/IOL;
- fuente stale en horario de mercado;
- intento bloqueado de ruta operativa.

No enviar tokens ni credenciales.

---

## 9. Criterios de aceptación

La integración no puede pasar a GREEN hasta cumplir todos:

```text
[ ] HTTP default-deny probado
[ ] sólo POST /token permitido como POST
[ ] PUT/PATCH/DELETE bloqueados
[ ] ningún método operativo en cliente inicial
[ ] token refresh probado
[ ] secretos redactados
[ ] CI sin red para unit tests
[ ] Sandbox read-only GREEN
[ ] producción canary read-only GREEN
[ ] PPI/IOL timestamps normalizados
[ ] staleness correcto fuera/dentro de mercado
[ ] intraday validado o explícitamente excluido
[ ] API call budget instrumentado
[ ] dashboard health IOL implementado
[ ] real_orders_sent=0 antes/después
[ ] rollback probado
[ ] documentación actualizada
```

---

## 10. Rollback

El rollback de esta integración debe ser simple y reversible:

```text
IOL_API_ENABLED=false
```

Además:

- remover IOL de source selection;
- volver a PPI-only sin migrar/destruir datos existentes;
- conservar evidencia de discrepancias y health;
- no eliminar históricos válidos ya almacenados únicamente por desactivar la fuente;
- revocar la autorización OAuth/credenciales si el incidente es de seguridad.

El runtime debe continuar funcionando en modo PPI-only cuando IOL esté deshabilitado o indisponible.

---

## 11. No-go explícitos

Para la primera versión con IOL:

```text
NO place_order
NO cancel_order
NO accept_ddjj
NO stop-loss/take-profit automático IOL
NO place_caucion
NO subscribe_fci
NO redeem_fci
NO caución tomadora
NO ejecución real
NO fallback que transforme un error IOL en autorización
NO logging de tokens
NO persistencia de passwords/tokens en Git
```

---

## 12. Veredicto de checkpoint

**IOL queda aceptado como pendiente P1 para la próxima versión de POROTA TRADING, exclusivamente como integración read-only secundaria/cross-validation en su primera etapa.**

RC6 no se modifica por este checkpoint.

La implementación futura debe comenzar por guardas de seguridad y observabilidad, seguir con Sandbox/read-only, y recién después pasar a producción PAPER en shadow. Cualquier capacidad operativa queda fuera del alcance hasta una autorización separada y explícita.

---

## 13. Estado GitHub esperado

```text
BRANCH: docs/iol-next-version-20260906
BASE: checkpoint/rc6-20260906-posthotfix
CODE_CHANGED: NO
RUNTIME_CHANGED: NO
DEPLOY_PERFORMED: NO
ORDERS_SENT: 0
SECRETS_COMMITTED: NO
NEXT_VERSION_BACKLOG: YES
```

**FIN DEL CHECKPOINT**
