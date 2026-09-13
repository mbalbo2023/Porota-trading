# POROTA TRADING — CAUCIONES INTRADAY OPPORTUNITY SPEC

**Fecha:** 2026-09-13  
**Estado:** diseño vinculante para implementación PAPER; no habilita trading por sí solo  
**Modo:** `PRODUCTION_PAPER`  
**Órdenes reales:** 0

## Objetivo

Permitir que Porota detecte durante la rueda una caución colocadora excepcionalmente atractiva sin esperar obligatoriamente al cash-sweep cercano al cierre, manteniendo caja suficiente para obligaciones y sin convertir una tasa alta aislada en una operación automática.

## Regla central

Una TNA alta **no es suficiente**. La oportunidad sólo existe si atraviesa, en orden, todos estos portones:

1. identidad/contrato de caución validado;
2. `CAUCION_FRESH_DATA_AGENT_GREEN=true` para el universo requerido;
3. lado colocador, tasa ejecutable y profundidad demostrados semánticamente;
4. quote y book dentro del TTL;
5. costos totales exactos para el capital exacto;
6. calendario y cutoff de cauciones abiertos y versionados;
7. vencimiento compatible con la próxima necesidad de liquidez;
8. `ObligationSnapshot` completo y reconciliado;
9. riesgo diario y límites de caja disponibles;
10. señal de oportunidad contra una referencia histórica/reciente explícita y auditable;
11. allocator PAPER vuelve a validar caja, profundidad, costos, calendario, riesgo e idempotencia bajo transacción.

Cualquier ausencia/conflicto produce `HOLD`.

## Cómo se define “muy buena tasa”

No se fija un porcentaje mágico en código. La definición debe ser una **policy versionada** con dos condiciones simultáneas:

- piso absoluto de rendimiento neto anualizado simple después de costos;
- ventaja mínima en puntos básicos frente a una referencia reciente del mismo ticker/plazo/moneda.

La referencia debe indicar fuente, timestamp y cantidad de muestras. No puede construirse silenciosamente con book crudo ambiguo. Hasta contar con una policy calibrada y aprobada, el detector permanece en `HOLD/POLICY_NOT_CONFIGURED`.

El rendimiento anualizado es sólo una métrica comparable: `net_profit / principal * day_count_basis / interest_days`. No presupone reinversión ni representa TIR compuesta.

## Cadencia objetivo

### Data collection

El observer general hoy ejecuta `current + book` con intervalo nominal 60 s, pero CAUCIONES participa por rotación. El collector intraday nominal es 180 s y también rota. Eso no garantiza los TTL de las 10 cauciones de forma simultánea.

Antes de READY debe existir un ciclo especializado que garantice las 10 identidades requeridas dentro del TTL de 5 minutos. Diseño objetivo: evaluación disparada por cada snapshot caución nuevo y, como watchdog, al menos una evaluación agregada por minuto. La cadencia efectiva se medirá por timestamps reales, no por configuración.

### Opportunity evaluation

Cada vez que el aggregate gate vuelve a GREEN con evidencia nueva:

- recalcular métricas de las 10 identidades;
- comparar únicamente ofertas de la moneda/caja correspondiente;
- descartar plazos que excedan el liquidity deadline;
- calcular rendimiento neto para el capital exacto presupuestado;
- comparar contra referencia same-ticker/same-currency;
- persistir un resultado por ticker (`HOLD` / `OPPORTUNITY_CANDIDATE`) y motivo;
- si existe oportunidad, enviar sólo candidatos verificados al bridge PAPER;
- el allocator decide `HOLD` o `PLACED_SIMULATED` con caja/riesgo/depth definitivos.

## Evitar falsos positivos

- No usar `current.volume` como capital ejecutable.
- No inferir que `bids` u `offers` es la punta colocadora.
- No usar una última tasa operada como precio ejecutable.
- No aceptar un único spike sin referencia suficiente.
- No revivir un book viejo cuando desaparece la punta nueva.
- No reutilizar profundidad ya consumida por otra colocación PAPER.
- No mezclar ARS y USD_MEP.
- No tratar históricos completos como evidencia live.
- No operar si el heartbeat o el calendario/cutoff están stale.

## Intradía versus cash-sweep EOD

Son dos decisiones distintas sobre el mismo ejecutor PAPER:

**Intraday opportunity:** sólo actúa cuando la tasa neta supera explícitamente la policy de oportunidad y la referencia. Está orientado a capturar una anomalía favorable antes del cierre.

**EOD cash-sweep:** no necesita una anomalía estadística. Cerca del cierre evalúa si queda caja libre, obligaciones cubiertas, costos exactos y una caución positiva que venza antes del liquidity deadline. Su objetivo es evitar caja ociosa, no perseguir máximos de tasa.

Ambos deben compartir contrato, freshness, obligación, calendario, profundidad, costos, ledger e idempotencia. Ninguno puede enviar órdenes reales.

## Selección entre plazos

Un plazo mayor no gana por TNA bruta. Se comparan:

- beneficio neto exacto;
- rendimiento neto anualizado simple;
- vencimiento y liquidity deadline;
- profundidad colocadora disponible;
- capital exacto al que corresponde el presupuesto de costos;
- caja/reserva y obligaciones futuras.

El detector identifica oportunidades. La colocación final sigue siendo responsabilidad del allocator PAPER transaccional.

## Política y calibración

Los parámetros de oportunidad deben obtenerse del replay PAPER / histórico de tasas validado y luego congelarse en una versión de policy. No se autoajustan intradía y no se promueven por una muestra corta.

Parámetros mínimos:
- `minimum_net_annual_rate_fraction`;
- `minimum_advantage_bps`;
- `minimum_reference_samples`;
- `reference_max_age_seconds`;
- `maximum_quote_age_seconds` (no puede relajar 300 s y el ejecutor puede exigir menos);
- fuente/version de la policy.

## Estado actual de implementación

- Adapter canónico: GREEN.
- Fresh-data aggregate gate: GREEN en tests determinísticos.
- ObligationSnapshot PAPER: GREEN en tests.
- Bridge -> existing cash sweep -> allocator -> `PLACED_SIMULATED`: GREEN en E2E.
- Detector intradía especializado: en implementación.
- Worker dedicado que garantice freshness de las 10 identidades: pendiente.
- Semántica live lado/depth/costos: pendiente de rueda activa.
- Horario/cutoff PPI específico: pendiente de evidencia.
- Dashboard especializado: pendiente de consumir la misma verdad del gate.
