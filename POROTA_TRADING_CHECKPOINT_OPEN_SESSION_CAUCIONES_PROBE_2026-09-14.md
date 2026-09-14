# Checkpoint — prueba contractual Web Cauciones en apertura

Estado: PROGRAMADA.

- Ejecución prevista: 14 Sep 2026, 10:30 ART, mediante GitHub Actions.
- Alcance: disponibilidad de condiciones contractuales/operativas de Cauciones PPI Web; sólo lectura.
- Coordinación: comparte el lock del supervisor contractual RC6; no puede solaparse con escritor residual, recuperación contractual ni la prueba de AL30.
- Guardas: no POST a PPI, no importación DB, no reinicios, no órdenes reales.
- Evidencia: autenticación, resultado de captura, presencia explícita de cauciones y conjuntos de campos observados; Telegram y log de Actions.
- Criterio: sin evidencia estructurada, Cauciones continúa HOLD/PARTIAL. No se infiere tasa, plazo, mínimo, garantía ni liquidación.
- Pendiente para parte 09:00: revisar resultado tras 10:30 ART y decidir si corresponde una captura focalizada adicional.
