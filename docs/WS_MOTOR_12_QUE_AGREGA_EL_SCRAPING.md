# WS-MOTOR-12 — qué agrega el scraping

WS12 no ejecutó scraping nuevo ni lo convirtió en camino primario. Reutiliza
13 términos contractuales PPI ya capturados y sanitizados cuando API/MCP u
oficial no publican ese campo indispensable para el instrumento exacto.

El único cambio material derivado de DOM fue corregir la interpretación
argentina de Adcap Ahorro Pesos: `1.000` significa ARS 1.000, no ARS 1. La
identidad se revalidó contra el catálogo PPI como `ADCAP.AP.A`; la documentación
oficial pública de PPI confirma el umbral comercial desde ARS 1.000.

Guardas:

- PPI API/catálogo primero; IOL MCP segundo; oficial/XHR después; DOM último.
- Sólo se habilita fallback para un campo requerido aún no resuelto.
- Cambio de selector o dato ambiguo resulta en evidencia ausente.
- No se guardan cookies, credenciales, OTP ni HTML de cuenta.
- Un timestamp de captura DOM no certifica tasa, profundidad, margen, NAV ni
  sesión dinámica.

Resultado: scraping aporta contrato residual; aporta cero dinámicas frescas y
no se usa para alterar la identidad PPI.

