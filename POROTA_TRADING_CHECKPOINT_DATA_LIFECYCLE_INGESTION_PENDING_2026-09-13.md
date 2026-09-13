# POROTA TRADING — CHECKPOINT PENDIENTES DATA LIFECYCLE + INGESTA — 2026-09-13

## Estado

Pendientes obligatorios posteriores al cierre de la ingesta histórica inicial y antes de reactivar de manera permanente todos los motores de ingesta.

Estos puntos NO bloquean por sí solos el scraping residual actual si sus gates de seguridad/calidad están verdes, pero sí deben cerrarse antes de considerar estable el ciclo operativo continuo de datos.

## 1. Estrategia de DELTAS / actualización incremental

Diseñar y validar cómo se incorporará nueva información sin repetir un full scrape diario.

Pendiente:

- definir por fuente e instrumento el watermark/último dato confirmado;
- obtener sólo el delta faltante desde PPI API, PPI Web, IOL u otra fuente externa aprobada;
- evitar duplicados mediante identidad financiera completa + fecha + fuente/hash;
- mantener proceso idempotente y reanudable;
- registrar provenance/origen de cada observación;
- detectar gaps y stale data antes de decidir qué instrumentos requieren actualización;
- priorizar actualización según uso real del motor, actividad del instrumento y antigüedad del último dato;
- permitir refresh selectivo por instrumento/familia sin relanzar scraping completo;
- documentar fallback entre fuentes y cuándo una fuente secundaria puede completar pero no sobreescribir evidencia de mayor autoridad.

Estado: `DELTA_INGESTION_STRATEGY=PENDING`

## 2. FCI / FCI Exterior

Los FCI continúan como pendiente explícito y NO deben clasificarse como `DONE_EMPTY` por falta de discovery actual.

Pendiente:

- resolver discovery estable de instrumento/fondo;
- identificar IDs/claseFCIId y semántica real por PPI Web;
- determinar si existe histórico utilizable, su frecuencia y significado económico;
- separar NAV/cuota parte, rendimiento, cotización y metadata contractual;
- revisar FCI Exterior por separado;
- documentar reglas de operabilidad, suscripción/rescate y mínimos únicamente en modo read-only;
- integrar FCI al esquema de deltas una vez resuelto el discovery.

Estado: `FCI_DISCOVERY_AND_HISTORY=PENDING`

## 3. Ventana móvil de 365 días y lifecycle de datos

Definir política para que el dataset operativo mantenga aproximadamente los últimos 365 días sin crecer indefinidamente.

Pendiente:

- distinguir entre histórico canónico operativo y evidencia/raw/auditoría;
- decidir qué datos mayores a 365 días se eliminan del store operativo y cuáles se archivan;
- no destruir provenance, hashes, attempts ni evidencia necesaria para auditoría/reproducibilidad;
- implementar pruning/archivado transaccional y reanudable;
- ejecutar pruning sólo después de validar que el delta nuevo quedó persistido;
- definir periodicidad del mantenimiento;
- validar que el pruning no rompa indicadores que necesiten lookback adicional;
- definir excepción por instrumento si algún modelo requiere más de 365 días;
- medir crecimiento de SQLite/disco y establecer umbrales/alertas;
- garantizar que una reingesta no reintroduzca datos expirados fuera de política.

Estado: `ROLLING_365D_DATA_LIFECYCLE=PENDING`

## 4. Revisión integral de motores de ingesta actualmente suspendidos

Antes de reactivar los productores/timers pausados, revisar su lógica a la luz de los últimos descubrimientos sobre PPI API/PPI Web, settlements, contratos y calidad histórica.

Pendiente obligatorio para CADA motor/timer:

- propósito exacto y consumidores;
- fuente utilizada y endpoint/ruta;
- frecuencia actual y necesidad real de esa frecuencia;
- universo/familias cubiertas;
- identidad financiera y settlement manejado;
- lógica de deduplicación/idempotencia;
- tratamiento de stale/carry-forward/empty/error;
- calidad y semántica de los datos producidos;
- solapamiento con otros motores;
- costo operativo, latencia y confiabilidad;
- valor real para decisiones del motor de trading;
- comportamiento ante mercado cerrado/feriados/calendarios;
- compatibilidad con política delta y rolling 365d;
- verificación de que no escriba datos sintéticos o ambiguos como evidencia canónica;
- validación de restart/resume, observabilidad y alertas.

Ningún timer debe reactivarse sólo porque `systemd` indique `active/waiting` o porque un job termine success: debe demostrar producción de evidencia útil y correcta.

Estado: `SUSPENDED_INGESTION_ENGINES_REVIEW=PENDING`

## 5. Matriz de utilidad real de fuentes

Reevaluar todas las fuentes hoy usadas o previstas y decidir si realmente aportan valor.

Para cada fuente (PPI API, PPI Web, IOL, BYMA/A3, Data912, Yahoo u otras) documentar:

- familias/campos para los que es autoridad o complemento;
- cobertura efectiva;
- exactitud/consistencia;
- frecuencia y latencia;
- históricos disponibles;
- metadata contractual disponible;
- estabilidad técnica;
- restricciones de uso;
- duplicación respecto de otra fuente;
- precedencia por tipo de dato;
- cuándo debe descartarse por no aportar valor o por introducir ambigüedad.

Objetivo: minimizar fuentes redundantes y conservar únicamente las que mejoren cobertura, calidad, resiliencia o validación cruzada.

Estado: `SOURCE_UTILITY_REVIEW=PENDING`

## 6. Relación con contratos y READY_PAPER

Este checkpoint complementa `POROTA_TRADING_CHECKPOINT_CONTRACT_COVERAGE_PENDING_2026-09-13.md`.

La existencia de histórico no implica `READY_PAPER`. Cada familia debe cerrar discovery, histórico, metadata contractual y reglas de operabilidad antes de habilitación funcional.

## 7. Regla para reactivar productores

Los 18 productores/timers actualmente pausados deben permanecer suspendidos hasta:

1. cierre de histórico PPI API + PPI Web + fallback final;
2. validación final de integridad/duplicados/seguridad;
3. revisión individual de lógica/fuente/utilidad;
4. compatibilidad con estrategia delta y lifecycle 365d;
5. evidencia funcional útil por timer;
6. reactivación controlada uno por uno.

## Estado consolidado

`DATA_LIFECYCLE_AND_INGESTION_CHECKPOINT=PENDING`
