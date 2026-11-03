# POROTA RC4 — PLAN DIARIO CANÓNICO DE READINESS Y AMPLIACIÓN DEL UNIVERSO

**Propósito:** este archivo debe ser leído por todo chat nuevo de POROTA después de RC4. No reemplaza el checkpoint de runtime; define el plan diario que no debe perderse.

## Objetivo operativo

Mantener POROTA en `PRODUCTION_PAPER` / `SIMULATED` mientras se amplía progresivamente la capacidad de decisión sin inferir contratos ni otorgar autoridad antes de tiempo.

## Horizontes orientativos, no promesas

- ACCIONES / CEDEAR PAPER: operativos; mejorar evidencia, replay y observabilidad.
- Scalping ACCIONES/CEDEAR PAPER: objetivo técnico de 1–3 ruedas posteriores a RC4 si freshness, book, exits y costos PAPER son GREEN.
- FCI local / Licitaciones: objetivo aproximado 3–7 días si Contract Evidence completa los campos execution-critical.
- Bonos / Letras / ON: objetivo aproximado 1–2 semanas; prioridad a unidad de precio, nominal, lámina, cantidad mínima/múltiplo/step, settlement y costos.
- Opciones / Futuros: objetivo aproximado 2–4 semanas; requieren contrato completo y simulador/executor específico, nunca sizing spot.
- Universo amplio PAPER decision-ready: horizonte orientativo 3–5 semanas, condicionado a evidencia real acumulada.

Estos tiempos deben recalcularse diariamente. Una familia no se habilita porque haya pasado el tiempo.

## Preguntas obligatorias de cada rueda

1. ¿Runtime exacto, imagen y modo son los esperados?
2. ¿`real_orders_sent=0`?
3. ¿Observer/dashboard saludables y sin restart inesperado?
4. ¿PPI auth/market data frescos?
5. ¿DB quick_check/WAL/storage sanos?
6. ¿Qué operaciones se abrieron/cerraron y por qué?
7. ¿P&L por moneda, nunca mezclado?
8. ¿Costos/slippage PAPER y MFE/MAE disponibles?
9. ¿Dynamic Concurrent Risk consumido y disponible?
10. ¿Qué policies Shadow habrían bloqueado?
11. ¿Sector/correlación tienen evidencia suficiente o siguen UNAVAILABLE?
12. ¿Scalping tuvo candidatos, fills, rechazos, stale o falta de contratos?
13. ¿History v2/legacy avanzó y con qué cobertura por familia?
14. ¿Contract Evidence recolectó algo nuevo, cambió o entró en conflicto?
15. ¿Scheduler muestra última/próxima corrida y fuente de evidencia?
16. ¿Salud amarilla está explicada, no sólo coloreada?
17. ¿Introspección es fresca o fue superseded por runtime vivo?
18. ¿Backups cubren cada storage crítico?
19. ¿Settlement pendiente está conciliado o sigue PENDING_CONFIRMATION?
20. ¿Qué requisito concreto falta para la siguiente familia READY_PAPER_CANDIDATE?

## Readiness por familia

Una familia sólo puede avanzar si tiene, como mínimo:

1. identidad normalizada;
2. contrato completo y fresco;
3. costos aplicables;
4. settlement;
5. sizing/unidades/mínimo/múltiplo/step;
6. simulador de ejecución específico;
7. salida/cierre específico;
8. tests;
9. ausencia de conflicto entre fuentes;
10. `real_orders_sent=0` preservado.

Scraping/History por sí solos nunca habilitan READY.

## Shadow → Binding

Cada control muestra por separado:

- modo actual;
- inicio real de la ventana;
- ruedas válidas;
- muestra;
- would_block;
- evidencia/freshness;
- hitos;
- lecciones;
- cuánto falta;
- decisión permitida.

El dashboard sólo puede declarar `ELIGIBLE_FOR_BINDING_DECISION`. La activación es manual, versionada, testeada y auditada.

La ventana no empieza por una fecha de calendario histórica: empieza en la primera rueda completa con instrumentación válida y persistencia contrafáctica funcional.

## Replay diario/semanal

No cambiar directamente por intuición:

- target/stop;
- ATR period;
- max hold;
- slippage;
- emergency cap override;
- señal tick/candle;
- thresholds de expectancy/regime/sector.

Comparar alternativas sobre los mismos datos; insuficiencia de muestra = `INSUFFICIENT_DATA`.

## Evidencia PAPER vs REAL

PAPER puede validar fórmulas, tarifario, lógica, estabilidad, contrafácticos y fills simulados. No puede afirmar costo realmente cobrado ni ejecución real del broker. Esas métricas deben permanecer `NOT_OBSERVABLE_IN_PAPER` hasta evidencia real externa.

## Regla diaria de prioridad

1. seguridad/runtime;
2. operaciones y riesgo;
3. Contract Evidence;
4. History/replay;
5. familias más cercanas a READY;
6. Shadow validation;
7. observabilidad/UX;
8. deuda no crítica.

## Qué nunca hacer por “apurar” el universo

- inferir sector, lote, step, multiplier o cutoff;
- auto-promover READY desde scraping;
- convertir 2FA/session failure en dato válido;
- reutilizar spot sizing para opciones/futuros;
- mezclar monedas;
- usar A3 como autoridad live;
- cambiar estrategia sin replay;
- pasar a PRODUCTION_REAL por backtest solamente.

## Cierre diario

Cada EOD debe producir un checkpoint que actualice:

- progreso de estos horizontes;
- familia más cercana a READY;
- requisitos que se cerraron;
- requisitos todavía bloqueantes;
- resultado de replay;
- estado Shadow→Binding;
- health/scheduler/backups/storage;
- lecciones aprendidas;
- prioridad concreta para la próxima rueda.
