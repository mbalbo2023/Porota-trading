# RC6 — Inventario de fuentes y criterios de evaluación de mercado

**Fecha:** 2026-09-14  
**Propósito:** comparar en paralelo las fuentes documentadas con las necesidades del motor; priorizar APIs documentadas y consultas puntuales.  
**Alcance:** análisis de datos de mercado y validación en modo PAPER. No habilita órdenes ni modifica el runtime.

## Principio de decisión

Separar tres evidencias:

1. **Dato recibido:** la API respondió.
2. **Dato validado:** esquema, unidad, timestamp e identidad tienen significado conocido.
3. **Uso admitido:** el dato sirve para una tarea definida, por ejemplo analítica histórica o contraste de precio.

Un payload no vacío solo acredita el primer punto. La operabilidad de una cuenta, los términos de liquidación y la calidad de un fill simulado requieren evidencia adicional. Si faltan datos críticos, conservarlos como desconocidos y dejar la decisión en HOLD.

## Inventario de fuentes

| Fuente | Aporte documentado | Uso posible en RC6 | Gaps y riesgos |
|---|---|---|---|
| **PPI** | Búsqueda, cotización e históricos; ciertos identificadores y atributos del instrumento; algunas estimaciones de renta fija. La documentación RC6 lo considera la fuente primaria de disponibilidad operativa de la cuenta. | Confirmar condiciones propias de PPI; cotización/libro para la ruta PAPER existente; contraste con otras fuentes para una identidad y hora comparables. | La elegibilidad estricta y cobertura siguen sin estar verificadas de punta a punta. Faltan o requieren contrato explícito unidades, ticks, pasos, mínimos, costos, reglas por familia y términos de cauciones/opciones/futuros. Tener precio no prueba que la especie esté habilitada. |
| **InvertirOnline (IOL)** | Cliente REST existente para panel, cotización e histórico diario; la investigación previa documenta otras consultas read-only, sujetas a verificar cada capacidad. | Descubrir símbolos candidatos; contraste puntual de cotización; análisis histórico si se conserva si la serie está ajustada y sus fechas. | El canary live actual aún no autenticó. Intradiario puede no estar disponible; una respuesta ausente fuera de rueda no es cero. El wrapper offline devuelve NONEMPTY_UNVALIDATED y no está conectado al motor. IOL no confirma operabilidad en PPI. |
| **A3** | Documentación RC6 menciona cierres/ticks mediante CEM y trades mediante Primary. | Enriquecer históricos o microestructura si se demuestra solapamiento y alineamiento de identidad. | No está demostrado el solapamiento ni la semántica de unidades y tiempo para el universo RC6. Antes de consultar en background: mapear PPI↔A3, zona horaria, paginación, duplicados y gaps. |
| **BYMA Market Data / Instruments** | APIs de datos de mercado y características de instrumentos, incluidas referencias de tick según el catálogo. | Fuente oficial complementaria para contrastar precios o enriquecer características de mercado si se confirma acceso y licencia. | No demuestra cobertura completa del universo RC6 ni disponibilidad de cuenta, routing, saldo o términos PPI. Confirmar plan, permisos, latencia, cobertura y uso permitido. |

### Referencias de inventario

- [PPI: issue #6](https://github.com/mbalbo2023/Porota-trading/issues/6) y [checkpoint de paridad/WA-03: PR #62](https://github.com/mbalbo2023/Porota-trading/pull/62).
- [IOL: issue #38](https://github.com/mbalbo2023/Porota-trading/issues/38), [wrapper read-only offline: PR #64](https://github.com/mbalbo2023/Porota-trading/pull/64), [canary y handoff RC6: PR #66](https://github.com/mbalbo2023/Porota-trading/pull/66).
- [A3: issue #28](https://github.com/mbalbo2023/Porota-trading/issues/28).
- [BYMA: catálogo oficial de APIs de Market Data](https://www.byma.com.ar/productos/productos-de-datos/market-data/apis).
- [PPI: documentación oficial de API](https://itatppi.github.io/ppi-official-api-docs/).

## Criterios apoyados en bibliografía y práctica de trading

La bibliografía de microestructura y ejecución sirve para formular controles de ingeniería; no sustituye condiciones del broker ni convierte un precio observado en ejecución probable.

- **Identidad antes de comparar.** Mantener separados familia/subfamilia, identificador del proveedor, ticker, mercado, venue, moneda y settlement. Un ticker igual no prueba que sean el mismo contrato.
- **Tiempo y microestructura.** Guardar hora de inicio y recepción, la hora efectiva reportada por la fuente cuando exista, bid/ask, tamaños y procedencia. Un cierre histórico —en especial una serie ajustada— no equivale a cotización o libro contemporáneo.
- **Precio, tamaño y costos.** No derivar tick de precio, paso de cantidad, unidad nominal, multiplicador ni costo de ejecución a partir de la cantidad de decimales. Para cada producto deben venir de una fuente/contrato con semántica documentada.
- **Límites antes de simular una entrada.** Datos viejos, futuros, sin hora, con identidad incompleta o en conflicto deben provocar HOLD. Una señal calculada puede estudiarse aparte; no debe elevar readiness ni crear fills simulados si faltan controles pretrade.
- **Fuentes con roles explícitos.** Una fuente secundaria puede contrastar precio e histórico; solo la evidencia de la ruta PPI puede confirmar disponibilidad y términos propios de PPI. Resolver discrepancias conservando procedencia, no promediando fuentes incompatibles.

Referencias de lectura:

- Larry Harris, [*Trading and Exchanges*](https://books.google.com/books/about/Trading_and_Exchanges.html?id=Rd9hDRR1Yx4C): estructura del mercado, liquidez y costos.
- Cartea, Jaimungal y Penalva, [*Algorithmic and High-Frequency Trading*](https://sebastian.statistics.utoronto.ca/books/algo-and-hf-trading/): ejecución, costos y selección adversa.
- SEC, [FAQ sobre Rule 15c3-5](https://www.sec.gov/rules-regulations/staff-guidance/trading-markets-frequently-asked-questions/divisionsmarketregfaq-0): ejemplo de controles pretrade en EE.UU.; es referencia de diseño, no conclusión legal para Argentina.

## Cómo avanzar en paralelo

| Frente | Trabajo independiente | Resultado/gate |
|---|---|---|
| **A. Inventario API-first** | Completar por proveedor endpoint, datos, identificadores, timestamps, cobertura, límites, permisos/licencia y evidencia (documentado / probado offline / consultado live / payload validado). | Matriz de cobertura por campo y familias; sin scraping masivo. |
| **B. Canario IOL** | Una autenticación y hasta cinco GET de mercado con allowlist, salida saneada y sin persistir token. | Confirmar únicamente qué responde IOL; cualquier payload comienza no validado. Requiere localizar la configuración autorizada. |
| **C. Comparación de mercado** | Emparejar la misma identidad, settlement y ventana temporal en IOL/PPI (y A3/BYMA si están disponibles). | Diferencias por lado, hora, unidad y cobertura; gaps explícitos, sin readiness inferida. |
| **D. Contrato de observación** | Definir timestamps, identidad de proveedor, endpoint, campos validados y almacenamiento seguro antes de conectar el adaptador. | Esquema revisable; no usar directamente el DTO offline actual para Evidence v2. |
| **E. Criterios de evaluación** | Definir freshness y tolerancias por estrategia/familia a partir de datos verificables y supuestos de costos. | Evaluación diagnóstica; HOLD cuando falte identidad, tiempo o términos críticos. |

## Decisión actual y riesgos

- **Sí tiene sentido** consultar IOL por API como fuente complementaria si el acceso autorizado se localiza y se mantiene el presupuesto acotado.
- **No tiene sentido** modificar el motor para suplir con supuestos los campos que no ofrece ninguna fuente. La alternativa segura es limitar la evaluación al uso que sí permiten los datos y mantener HOLD en la decisión operativa.
- Riesgos principales: comparar instrumentos o liquidaciones distintas, usar series ajustadas como precio ejecutable, tratar cotizaciones desactualizadas como actuales, asumir tick/step/costos, y confundir acceso al endpoint con permiso o disponibilidad en la cuenta.
- Estado PAPER: orden real bloqueada, `real_orders_sent=0`; ningún análisis de esta página cambia esa condición.
