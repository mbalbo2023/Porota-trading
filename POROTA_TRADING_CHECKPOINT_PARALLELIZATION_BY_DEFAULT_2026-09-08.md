# POROTA TRADING — CHECKPOINT CANÓNICO: PARALLELIZATION BY DEFAULT

Fecha: 2026-09-08/09

## Regla operativa permanente

Toda tarea de POROTA TRADING que pueda avanzar en paralelo sin afectar el camino crítico, la seguridad, la trazabilidad ni el diagnóstico debe paralelizarse automáticamente, sin pedir autorización adicional a Martín y sin esperar recordatorios.

Esto incluye, entre otros: auditorías read-only, análisis de código, wiring, materialización en ramas aisladas, compilación, tests, CI, checkpoints, preparación de candidatos y deploy-readiness, documentación, reconciliaciones read-only y validaciones independientes.

## Únicas razones válidas para serializar

Se serializa únicamente cuando dos trabajos compiten por el mismo runtime/DB/estado mutable, cuando una activación depende estrictamente de otra, cuando mezclar cambios comprometería el diagnóstico, o cuando existe riesgo de mutación/destrucción de evidencia.

## Política de olas

Las olas independientes deben prepararse en paralelo hasta estado READY_FOR_DEPLOY. Los deploys runtime se ejecutan de forma controlada/serializada cuando corresponda, conservando PRODUCTION_PAPER, real_orders_sent=0, sin rutas de órdenes reales y sin rollback automático.

## Responsabilidad del asistente

No esperar a que Martín recuerde o vuelva a autorizar esta política. Detectar automáticamente frentes paralelizables, lanzarlos y mantenerlos progresando. Informar bloqueos reales y estados de cada ola; no convertir trabajo paralelizable en espera artificial.

## Contexto actual

Wave1A, Wave1B, Wave2, Wave3 y Wave4 ya tienen despliegues GREEN previos. Wave5, Wave6, Wave7 y Wave8 tienen CI GREEN y auditorías de runtime-readiness GREEN; deben llevarse en paralelo hasta READY_FOR_DEPLOY sobre la baseline realmente desplegada, evitando perder cambios acumulados de Waves anteriores.

El frente de almacenamiento B4 byte-exact sigue separado/read-only y no debe bloquear la preparación paralela de Waves 5–8.
