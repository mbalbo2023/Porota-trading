# POROTA TRADING — CHECKPOINT RC6 — IOL HISTORY SHADOW

Fecha: 2026-09-07
Estado: P1 IN PROGRESS / SHADOW ONLY
Rama: `feature/rc6-iol-history-shadow-20260907`

## Objetivo

Incorporar IOL como fuente histórica secundaria/read-only para backfill, contraste y diagnóstico sin convertirla todavía en writer canónico automático.

## Reglas

- `READ_ONLY=TRUE`
- `EXECUTION_ALLOWED=FALSE`
- `CANONICAL_WRITE=FALSE` inicialmente
- no validación/colocación/cancelación de órdenes;
- identidad financiera completa obligatoria;
- T1 de IOL no se equipara silenciosamente a A-24HS;
- no asumir RAW/ADJUSTED si la fuente no lo declara;
- no degradar FULL_OHLCV existente con datos incompletos;
- toda observación debe conservar provenance y timestamp.

## Evidencia ya obtenida

La consulta read-only de IOL devuelve OHLCV diario para instrumentos BYMA, incluyendo acciones/bonos/CEDEAR, y metadata de asset con tipo, moneda, lote y especies relacionadas. Intraday history no se considera probado como fuente general.

## Entregables de esta rama

1. contrato del adaptador `IOL_HISTORY_READONLY`;
2. normalizador a evidencia versionada;
3. shadow comparison PPI vs IOL por identidad/fecha;
4. métricas de cobertura y discrepancia;
5. pruebas de settlement/price_basis;
6. prohibición explícita de canonical write hasta revisión humana.

## Casos obligatorios

- IOL completa un gap PPI sin borrar evidencia previa;
- discrepancia PPI/IOL queda auditada y no se promedia;
- CLOSE_ONLY nunca desplaza FULL_OHLCV;
- RAW y ADJUSTED no colisionan;
- settlement ambiguo => fail-closed;
- identidad ambigua => fail-closed;
- payload repetido no infla versiones;
- cero impacto en observer/hot path;
- `real_orders_sent=0` invariante.
