# Checkpoint — prueba contractual Web en apertura

Estado: PROGRAMADA.

- Muestra elegida: `AL30` (bono líquido, soportado por API; ruta contractual Bonos).
- Ejecución prevista: 14 Sep 2026, 11:00 ART, mediante GitHub Actions.
- Alcance: consulta PPI Web de sólo lectura con lock exclusivo; no relanza históricos ni residual.
- Guardas: aborta si hay writer residual, si el lock está tomado o si falta el harness; no usa POST, no importa DB, no reinicia servicios y no envía órdenes.
- Evidencia a revisar: autenticación, resultado de la captura, presencia explícita de AL30 y sólo nombres de campos encontrados.
- Criterio: datos explícitos y estructurados permiten pasar a la matriz contractual; ausencia o estructura insuficiente mantiene HOLD. Nunca inferir.
- Pendiente para el parte de 09:00: confirmar que el workflow se haya disparado y revisar su log/resultados tras la apertura.
