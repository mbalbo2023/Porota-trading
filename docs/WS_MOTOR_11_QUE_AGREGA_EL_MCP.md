# WS-MOTOR-11 — QUÉ AGREGA EL MCP

## Conclusión

El MCP de IOL funciona y estaba subutilizado como fuente complementaria. Aporta
inventario, analytics, simulación, cadenas y tasas estructuradas que pueden
recorrer `MCP -> captura -> cache -> Evidence v2 -> contrato -> readiness`.
No aporta autoridad sobre la identidad PPI, no expone todos los términos de
orden y no autoriza por sí solo READY.

La contradicción aparente se resuelve así: IOL puede operar porque su motor
interno combina su maestro de instrumentos, reglas de orden, backoffice y
liquidación; el MCP publica sólo una proyección read-only de ese sistema. Lo que
el MCP omite puede existir en el validador/backend del broker, en la bolsa,
clearing o gestora. POROTA necesita evidencia machine-readable propia de esos
términos antes de simular dinero, no sólo la afirmación comercial del broker.

## Inventario y ejecución

| Capacidad | Clase | Uso en WS09/10/11 | Aporte | Límite relevante |
|---|---|---|---|---|
| `get_asset_info` | safe read-only | WS09 | identidad descriptiva, mercado, moneda, plazo, unidades por lote | units-per-lot no prueba mínimo/step de orden |
| `get_asset_quote` | safe read-only | WS09 | bid/ask/last y bases de precio disponibles | capture time no es provider time |
| `get_fixed_income_analytics` | safe read-only | WS09 | vencimiento, dirty/technical value e inputs | no aporta todos los eventos ni términos de orden |
| `simulate_fixed_income_by_amount` | read-only simulator | inventariado | relación monto/nominales | no se usó para enviar/validar una orden |
| `simulate_fixed_income_by_nominals` | read-only simulator | WS09 | relación nominal/base/cash | D30N6/YMCJO devolvieron `not_found` en simulador; no se inventó step |
| `get_options_chain` | safe read-only | WS09 + revalidación única WS11 | underlying, put/call, strike, expiración, 116 series GGAL observadas | no lote contractual, step ni ejercicio completo por sí solo |
| `get_caucion_rates` | safe read-only | WS09 + revalidación única WS11 | moneda, lado consultado, plazo, TNA, mínimo y due date cuando aparecen | no depth/principal ejecutable ni timestamp proveedor suficiente |
| `get_caucion_rate` | safe read-only | inventariado | consulta puntual | no fue necesario repetirla en WS11 |
| `get_fci_funds` | safe read-only | WS09 + revalidación única WS11 | 22 fondos: asset, descripción, tipo, mercado, moneda, operable | inventario no prueba clase PPI, NAV/fecha, cutoff ni lifecycle |
| `get_next_corporate_events` | safe read-only | inventariado | eventos próximos | no sustituye contrato/eventos completos por especie |
| históricos/intraday | safe read-only | ya integrados fuera de este WS | mercado e historia | no prueban contrato operativo |
| `validate_order` | pre-trade | schema inventariado, NO invocado | revela qué valida el backend | puede depender de cuenta real; prohibido llamarlo aquí |
| `validate_caucion` | pre-trade | schema inventariado, NO invocado | valida caución | misma prohibición |
| `validate_fci_subscription` / `validate_fci_redemption` | pre-trade | schema inventariado, NO invocado | parámetros del lifecycle | no se aceptó DDJJ ni se tocó cuenta |
| mutaciones (`place_*`, `cancel_*`, `subscribe_*`, `redeem_*`) | real-money | PROHIBIDAS / NO invocadas | ninguno | fuera de scope |

## Forma del dato y consumidores

- `rc6_iol_family_reference.py` captura de forma acotada información fija,
  simulación por un nominal, quote, chain, fondos y cauciones.
- `rc6_broker_parity_evidence.py` exige identidad PPI completa antes de aceptar
  una fila IOL y la persiste en Evidence v2 con `IOL_STRUCTURED_API`.
- `rc6_contract_bridge.py` expone el contrato sólo para la misma clave
  `family+ticker+market+currency+settlement`.
- `cq_family_contract_rules_hf6.py` consume esa evidencia en el gate canónico.
- Un timestamp de captura sólo acredita estáticos; sin timestamp proveedor se
  descartan/invalidan las dinámicas.

## Numerador/denominador reproducible

Sobre los 20 testigos y 222 filas requeridas del replay:

| Medición | Numerador / denominador | Lectura |
|---|---:|---|
| IOL MCP estructurado, aun antes de binding | 24 / 222 | 10,8 % de filas requeridas tienen un valor MCP observado |
| IOL MCP usable tras identidad PPI exacta | 20 / 222 | 9,0 % entra al gate; 4 filas quedan informativas sin binding |
| PPI structured + IOL structured usables | 50 / 222 | 22,5 % sin DOM/scraping |
| Toda evidencia integrada (incluye DOM/oficial/derivada) | 73 / 222 | 32,9 % de la matriz testigo |

Desglose IOL por familia: BONOS 1/20, ON 1/20, LETRAS 1/14,
OPCIONES 7/44, CAUCIONES 11/64, FCI 3/27 y FUTUROS 0/33. El
denominador es deliberadamente por instrumento, no por una plantilla de familia;
evita declarar cobertura global por extrapolación.

## Puede reemplazar scraping

Sí, para vencimiento/base cuando el payload estructurado es dimensionalmente
coherente, chain de opciones, inventario FCI y términos publicados de caución.
No reemplaza DOM/XHR PPI cuando el campo indispensable es específico del ticket
PPI (mínimo/step/lote/base) y tampoco reemplaza fuente oficial para multiplier,
tick, margen, calendario o términos de gestora.
