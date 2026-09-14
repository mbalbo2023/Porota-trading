# Checkpoint — prueba contractual Web Cauciones en apertura

Estado: PROGRAMADA.

- Ejecución prevista: 14 Sep 2026, 10:30 ART, mediante GitHub Actions.
- Alcance: disponibilidad de condiciones contractuales/operativas de Cauciones PPI Web; sólo lectura.
- Coordinación: comparte el lock del supervisor contractual RC6; no puede solaparse con escritor residual, recuperación contractual ni la prueba de AL30.
- Guardas: no POST a PPI, no importación DB, no reinicios, no órdenes reales.
- Evidencia: autenticación, resultado de captura, presencia explícita de cauciones y conjuntos de campos observados; Telegram y log de Actions.
- Criterio: sin evidencia estructurada, Cauciones continúa HOLD/PARTIAL. No se infiere tasa, plazo, mínimo, garantía ni liquidación.
- Pendiente para parte 09:00: revisar resultado tras 10:30 ART y decidir si corresponde una captura focalizada adicional.

## Estado actualizado

`PAUSADA_POR_SEGURIDAD`.

La programación fue retirada antes de la rueda: el workflow invocaba el harness global, no una captura focalizada; no validaba frescura de la captura, no comprobaba en forma fail-closed los invariantes de seguridad y el resumen Telegram podía incluir log crudo. Requisitos antes de reprogramar: lock PPI global único, selector de familia/instrumento realmente soportado por el collector, run-id/timestamp fresco, asserts de seguridad, campos obligatorios y resumen redactado.
