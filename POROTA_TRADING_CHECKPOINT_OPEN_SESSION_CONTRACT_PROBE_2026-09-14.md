# Checkpoint — prueba contractual Web en apertura

Estado: PROGRAMADA.

- Muestra elegida: `AL30` (bono líquido, soportado por API; ruta contractual Bonos).
- Ejecución prevista: 14 Sep 2026, 11:00 ART, mediante GitHub Actions.
- Alcance: consulta PPI Web de sólo lectura con lock exclusivo; no relanza históricos ni residual.
- Guardas: aborta si hay writer residual, si el lock está tomado o si falta el harness; no usa POST, no importa DB, no reinicia servicios y no envía órdenes.
- Evidencia a revisar: autenticación, resultado de la captura, presencia explícita de AL30 y sólo nombres de campos encontrados.
- Criterio: datos explícitos y estructurados permiten pasar a la matriz contractual; ausencia o estructura insuficiente mantiene HOLD. Nunca inferir.
- Pendiente para el parte de 09:00: confirmar que el workflow se haya disparado y revisar su log/resultados tras la apertura.


## Contingencia de fuentes oficiales — PENDIENTE

Sólo si las pruebas en rueda confirman que PPI Web/API no expone los campos requeridos:

1. Construir una matriz por familia, instrumento y campo faltante.
2. Investigar y validar la fuente oficial primaria aplicable:
   - PPI API/Web para operabilidad actual, especie habilitada, liquidación, mínimos y steps.
   - Mercado correspondiente (BYMA, A3 o MAE) para reglas/especificaciones de producto.
   - CNV y emisor para términos regulatorios estables de emisiones.
3. Registrar procedencia, fecha de vigencia, campo exacto y validación cruzada.
4. No usar CNV, mercado ni emisor como sustituto automático de configuración operativa PPI.
5. Sin evidencia explícita y consistente, el instrumento sigue HOLD; nunca READY_PAPER por inferencia.
