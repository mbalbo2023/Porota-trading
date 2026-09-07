# POROTA TRADING — CHECKPOINT IOL/MCP COMO FUENTE HISTÓRICA RC6

**Fecha:** 2026-09-06  
**Release de referencia:** `17.0.0-rc6`  
**Branch documental:** `checkpoint/rc6-20260906-posthotfix`  
**Tipo:** ADDENDUM CANÓNICO / PENDIENTE P1  
**Impacto runtime live:** NINGUNO — observer RC6 permanece congelado  

> Este checkpoint complementa `POROTA_TRADING_CHECKPOINT_CANONICO_RC6_2026-09-06_POSTHOTFIX.md`. No autoriza cambios sobre el observer live ni órdenes reales. Ante textos anteriores sobre IOL/Data912, prevalece esta decisión más reciente dentro de la branch documental.

## 1. Decisión canónica

IOL pasa a ser **candidato P1 prioritario para históricos diarios, backfill y cross-validation** de POROTA TRADING.

El objetivo es que, una vez superadas las pruebas de identidad financiera, settlement, cobertura y reconciliación, **IOL reemplace a Data912 como fallback histórico preferido** en aquellos casos donde PPI no entregue cobertura suficiente o entregue series problemáticas.

Data912 **NO se elimina físicamente ni se borra su provenance**. Queda degradada a:

```text
LEGACY_FALLBACK
LOW_TRUST_SOURCE
DIAGNOSTIC_ONLY_WHEN_HIGHER_CONFIDENCE_SOURCES_EXIST
```

Motivo de la decisión: durante la ingesta/auditoría previa Data912 presentó numerosos errores/rechazos y el usuario declara baja confianza en esa fuente. La prioridad es preservar evidencia histórica existente pero reducir su autoridad en la selección futura de datos canónicos.

## 2. Evidencia IOL ya validada

Documento de investigación base:

`IOL_API_MCP_FULL_RESEARCH_POROTA_TRADING_2026-09-06.md`

El relevamiento confirmó:
- MCP oficial IOL con OAuth 2.0 y modo read-only;
- REST IOL autenticada mediante token dinámico;
- `get_price_history` disponible para históricos diarios OHLCV;
- OHLCV diario previamente validado con GGAL;
- IOL apto como fuente secundaria read-only/cross-validation, no como broker ejecutor en RC6.

Validación adicional read-only realizada el 2026-09-06 mediante la conexión MCP IOL:

### Acción — GGAL / BCBA
- histórico diario OHLCV recuperado correctamente;
- barras recuperadas para sesiones entre 2026-08-20 y 2026-09-04;
- `get_asset_info` identifica `type=ACCIONES`, `currency=ARS`, `term=T1`.

### Bono — GD30 / BCBA
- histórico diario OHLCV recuperado correctamente para el mismo período de prueba.

### CEDEAR — AAPL / BCBA
- histórico diario OHLCV recuperado correctamente;
- `get_asset_info` identifica `type=CEDEARS`, `currency=ARS`, `term=T1`;
- símbolos relacionados observados: `AAPL`, `AAPLD`, `AAPLC`.

### Profundidad histórica
- prueba adicional sobre GGAL en enero de 2020 recuperó barras OHLCV válidas;
- por lo tanto IOL muestra capacidad real de backfill profundo y no solamente de sesiones recientes.

**CHECKPOINT `IOL-HIST-DEEP-01`: PASS preliminar.**  
IOL MCP demostró disponibilidad de histórico diario profundo al menos para la muestra probada.

## 3. Restricción crítica: identidad financiera completa

History Store v2 no debe importar una barra únicamente por ticker.

La identidad canónica sigue siendo:

```text
symbol
+ instrument_type
+ market
+ settlement
+ date
```

Antes de persistir IOL como fuente canonical se debe demostrar de forma determinística el mapping entre el `term` de IOL y el settlement de POROTA, incluyendo como mínimo:

```text
IOL T0/T1/...  <->  settlement canónico POROTA
```

No asumir semántica por similitud de nombres.

Si el mapping es ambiguo o falta algún componente de identidad:

```text
ALIGNMENT_UNVERIFIED
CANONICAL_WRITE=DENY
```

## 4. Política de prioridad de fuentes

Se conserva la jerarquía estructural de History Store v2:

```text
PPI Production / PPI API      rank 10
A3 / BYMA                     rank 20
IOL                           rank 30
Data912                       rank 50
Yahoo                         rank 90
```

Pero se agrega la siguiente política operativa:

1. PPI continúa siendo la fuente primaria actual.
2. IOL debe evaluarse como **fallback preferido y fuente de reconciliación** antes de Data912.
3. Data912 no debe utilizarse para sobreescribir una barra PPI/IOL/A3 válida de identidad equivalente.
4. Data912 no debe convertir un conflicto no resuelto en dato GREEN por simple disponibilidad.
5. Si IOL y PPI discrepan, comparar timestamp, mercado, moneda, settlement, adjusted/unadjusted, feriados, sesión y staleness; no promediar valores.
6. Si la discrepancia no puede resolverse:

```text
SOURCE_DISCREPANCY
CANONICAL_DECISION=FAIL_CLOSED
```

7. Toda barra histórica conserva source/provenance/versionado incluso si deja de ser seleccionada como canonical.

## 5. MCP vs REST — función de cada integración

### MCP IOL
Uso preferente inicial:
- investigación manual;
- canarios read-only;
- auditoría de coverage;
- comparación PPI/IOL;
- diagnóstico de gaps;
- validación de familias e identidad;
- pruebas de profundidad histórica.

El MCP NO se considera todavía el mecanismo autónomo definitivo del Droplet para backfill masivo/scheduler.

### REST IOL
Objetivo para runtime autónomo:

```text
IOL_HISTORY_READONLY
```

Guard obligatorio:

```text
ALLOW:
  POST https://api.invertironline.com/token
  GET  https://api.invertironline.com/**

DENY:
  POST /** excepto /token
  PUT  /**
  PATCH /**
  DELETE /**
```

El cliente legacy `ak_iol_client.py` NO es apto como nuevo cliente canónico porque contiene capacidades POST y no satisface el contrato GET-only/default-deny requerido.

## 6. Plan P1 de auditoría y adopción

### Fase 1 — coverage MCP read-only

Construir una muestra representativa por familia:
- acciones;
- CEDEARs;
- bonos;
- letras;
- obligaciones negociables;
- instrumentos dolarizados C/D cuando existan;
- otros instrumentos para los que `get_price_history` resulte aplicable.

Para cada identidad medir:
- primera fecha disponible;
- última fecha disponible;
- cantidad esperada vs recibida;
- gaps;
- OHLC <= 0;
- `OHLC_INCONSISTENT`;
- volumen nulo/cero;
- duplicados;
- fechas fuera de calendario;
- moneda;
- mercado;
- tipo;
- settlement/term;
- adjusted/unadjusted cuando aplique.

### Fase 2 — matriz comparativa

Generar evidencia:

```text
IDENTIDAD POROTA
-> COVERAGE PPI
-> COVERAGE IOL
-> COVERAGE A3
-> COVERAGE DATA912
-> GAPS POR FUENTE
-> DISCREPANCIAS
-> FUENTE SELECCIONABLE
-> MOTIVO
```

### Fase 3 — implementación aislada

Crear componentes equivalentes a:

```text
iol_token_manager.py
iol_readonly_client.py
iol_normalizer.py
iol_source_health.py
iol_ppi_reconciler.py
```

con tests de default-deny, redacción de secretos, token refresh, staleness, identidad y normalización.

### Fase 4 — shadow ingestion

- leer IOL REST en modo producción read-only;
- primero NO seleccionar IOL como canonical;
- persistir source/version/provenance en canal controlado;
- comparar contra PPI/A3/Data912;
- cuantificar cuánto coverage útil agrega realmente IOL.

### Fase 5 — promoción controlada

Sólo si la evidencia es satisfactoria:
- habilitar `IOL_HISTORY` como fuente normal de History Store v2;
- mantener PPI como primary;
- IOL se convierte en fallback preferido;
- Data912 permanece legacy/low-trust;
- documentar métricas before/after y rollback lógico por source selection.

## 7. Intraday

No extender esta decisión al histórico intradiario.

La investigación previa obtuvo `intraday_unavailable` para pruebas fuera de mercado y la herramienta IOL informa retención limitada de sesiones recientes.

Estado:

```text
IOL_DAILY_HISTORY=CANDIDATE_P1
IOL_INTRADAY_HISTORY=UNVERIFIED
```

No usar IOL como fuente productiva intraday hasta validar durante rueda, ventana de retención, completitud y semántica de volumen/timestamps.

## 8. Safety invariants

Esta incorporación no modifica:

```text
MODE=PRODUCTION_PAPER
EXECUTION=SIMULATED
REAL_MONEY=BLOCKED
real_orders_sent=0
```

IOL no se habilita como broker ejecutor.

No usar:
- `place_order`;
- cancelaciones;
- cauciones operativas;
- FCI subscribe/redeem;
- stop loss/take profit operativos;
- DDJJ;
- endpoints de mutación de cuenta.

No almacenar credenciales, access token, refresh token, cookies u OTP en Git, logs, dashboard, Telegram ni DB de evidencia en texto plano.

## 9. Veredicto de checkpoint

```text
IOL-MCP-HISTORY-CANDIDATE=P1
IOL-DAILY-OHLCV=VALIDATED_ON_SAMPLE
IOL-DEEP-HISTORY=PASS_PRELIMINARY
IOL-INTRADAY=UNVERIFIED
IOL-CANONICAL-WRITE=NOT_YET_AUTHORIZED
DATA912-TRUST=LOW
DATA912-ROLE=LEGACY_FALLBACK
PREFERRED_FALLBACK_TARGET=IOL
OBSERVER_RUNTIME_CHANGED=NO
REAL_ORDERS_SENT=0
```

**Decisión:** priorizar la auditoría e integración read-only de IOL para reducir progresivamente la dependencia de Data912 en históricos, sin borrar provenance ni degradar los controles fail-closed de History Store v2.
