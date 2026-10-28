# WS-MOTOR-11 — matriz campo/fuente/necesidad

Estado de corte: 2026-09-28/29. Alcance: PAPER/SHADOW. La matriz canónica y
machine-readable es `docs/evidence/ws_motor_11_before_after.json` bajo
`field_matrix` (222 filas). Cada fila conserva familia, especie, mercado,
moneda, settlement, identidad completa, campo, valor/unidad, fuente, endpoint,
timestamps, freshness basis, derivación, consumidor, impacto, perfil,
necesidad, estado before/after, evidencia y test.

## Qué representa

- 20 testigos obligatorios o equivalentes concretos.
- 222 combinaciones instrumento/campo exigidas por la autoridad única
  `cq_family_contract_rules_hf6`.
- 73 filas con evidencia integrada y 149 todavía `UNRESOLVED`.
- 0 valores extrapolados entre instrumentos.
- `provider_timestamp=null` no se reemplaza por `capture_timestamp`: la captura
  sólo respalda términos estáticos.
- Una fila complementaria de IOL queda informativa hasta que se vincula a una
  identidad PPI exacta.

## Testigos y resultado por instrumento

| Familia | Testigo | Identidad PPI | Resultado OPEN | Ejes adicionales | Hecho demostrado |
|---|---|---:|---|---|---|
| BONOS | GD30 | sí | READY_PAPER_SPOT | EVENT_CONDITIONAL | Evidence queda candidato y el gate integrado contrato/catálogo lo promueve; PPI aporta cantidad/step/base 100 y la regla 1/100 da el multiplicador |
| BONOS | AL30 | sí | BLOCKED_DATA | — | Identidad presente; los términos de GD30 no se copian |
| LETRAS | D30N6 | no recapturada | BLOCKED_DATA | — | IOL aporta base 100 y vencimiento 2026-11-30, pero no crea identidad PPI |
| ON | YMCJO | sí | BLOCKED_DATA | — | IOL aporta base 100 y vencimiento 2033-09-30 sobre identidad PPI |
| ON | YMCIO | no recapturada | BLOCKED_DATA | — | ON adicional, sin extrapolación desde YMCJO |
| LETRAS | S31O6 | no recapturada | BLOCKED_DATA | — | LETRA adicional visible y fail-closed |
| OPCIONES | GFGC6000OC | sí | READY_PAPER_OPTION_LONG | ejercicio queda para EVENT | Evidence queda candidato y el gate integrado lo promueve; chain IOL completa subyacente/right/strike/expiry y PPI aporta multiplicador/cantidad |
| OPCIONES | GFGC7000OC | sí | BLOCKED_DATA | EVENT_CONDITIONAL fuera de OPEN | Chain IOL; no hereda lote/step de GFGC6000OC |
| OPCIONES | AAPC1000O | no recapturada | BLOCKED_DATA | — | testigo CEDEAR; identidad exacta pendiente |
| OPCIONES | GFGC50000A | no recapturada | BLOCKED_DATA | — | serie ajustada aparente, bloqueada explícitamente |
| CAUCIONES | ARS 1 día colocadora | sí | BLOCKED_DATA | dinámica sin timestamp proveedor | MCP aporta plazo, mínimo, TNA y fecha; no aporta profundidad ejecutable |
| CAUCIONES | ARS 2 días colocadora | sí | BLOCKED_DATA | dinámica inválida | TNA 0 no se interpreta como oferta operable |
| CAUCIONES | ARS 3 días colocadora | sí | BLOCKED_DATA | dinámica incompleta | MCP aporta plazo/TNA, no principal disponible/step |
| CAUCIONES | USD 1 día colocadora | sí | BLOCKED_DATA | dinámica incompleta | MCP aporta mínimo USD 100 y TNA 0,2; falta profundidad/step |
| FCI | ADRDOLA / Adcap Ahorro Pesos | no ligada | BLOCKED_DATA | BLOCKED_EXECUTOR | UI PPI aporta términos de clase; ticker IOL no prueba clase PPI idéntica |
| FCI | IOLCAMA | no ligada | BLOCKED_DATA | BLOCKED_EXECUTOR | inventario MCP, no identidad PPI ni NAV/fecha |
| FCI | ADCUSAD | no ligada | BLOCKED_DATA | BLOCKED_EXECUTOR | FCI USD del inventario; misma restricción |
| FUTUROS | DLR/DIC26 | sí | BLOCKED_DATA | BLOCKED_EXECUTOR | PPI liga especie; A3 aporta multiplicador/tick; falta margen dinámico y contrato completo |
| FUTUROS | DLR/ENE27 | no recapturada | BLOCKED_DATA | BLOCKED_EXECUTOR | no hereda DLR/DIC26 |
| FUTUROS | RFX20/OCT26 | no recapturada | BLOCKED_DATA | BLOCKED_EXECUTOR | otro contrato A3; no hereda regla DLR |

## Conteo de procedencia en las 222 filas

| Procedencia | Filas evidenciadas |
|---|---:|
| PPI structured/catalog | 30 |
| IOL MCP/API estructurado | 24 |
| PPI DOM autenticado heredado de WS10 | 14 |
| A3 documentación oficial | 2 |
| Regla oficial derivada | 3 |
| Sin evidencia suficiente por instrumento | 149 |

Estos conteos no son una declaración de cobertura del universo comercial. Son
el numerador y denominador exactos del replay sanitizado de 20 testigos. El
detalle completo, incluidos campos `UNRESOLVED`, está en el JSON canónico.

## Estado del resto de las familias

| Familia | Estado resultante | Motivo exacto |
|---|---|---|
| Acciones / CEDEAR / ETF BYMA | `READY_PAPER_SPOT` cuando existe identidad PPI exacta y quote vigente | El contrato unitario se deriva de la convención del mercado; no se exige ISIN, ratio descriptivo, tick ni fee duplicado |
| Bonos / Letras / ON | GD30 `READY_PAPER_SPOT`; restantes testigos `BLOCKED_DATA` | Sólo faltan mínimo, step o base/multiplicador de esa identidad concreta; maturity/cupón/amortización no bloquean OPEN |
| Opciones | GFGC6000OC `READY_PAPER_OPTION_LONG`; restantes testigos `BLOCKED_DATA` | Para OPEN long sólo bloquean subyacente/right/strike/expiry, multiplier, mínimo y step; ejercicio es EVENT |
| Cauciones | testigos `BLOCKED_DATA` o `BLOCKED_DYNAMIC_DATA` | El executor ya existe; el residuo indispensable es una oferta vigente con tasa, profundidad/principal, step y timestamp proveedor, no metadata descriptiva |
| FCI | `BLOCKED_DATA` + `BLOCKED_EXECUTOR` por instrumento | Para OPEN se redujo a clase/moneda/mínimo/step/estado de suscripción; NAV/cutoff/rescate son EVENT. Falta binding de clase y lifecycle PAPER integrado |
| Futuros | `BLOCKED_DATA` + `BLOCKED_EXECUTOR` por contrato | Se redujo a underlying/expiry/multiplier/mínimo/step y margen vigente; settlement method es EVENT. Falta completar cada contrato y conectar lifecycle PAPER |

`READY_PAPER_CANDIDATE` es deliberadamente un estado interno de Evidence: nunca
se publica como readiness final. El resultado visible es el capability devuelto
por el gate integrado (`READY_PAPER_SPOT` o `READY_PAPER_OPTION_LONG`) o el
blocker concreto.

## Perfiles y falsa exigencia removida

- `OPEN` y `CLOSE` no requieren términos que sólo se consumen al ocurrir un
  evento futuro.
- `EVENT` y `FULL` continúan fail-closed para cupón/amortización/moneda de pago
  en renta fija y ejercicio en opciones.
- Balance, buying power, colateral real y permisos reales de cuenta no son
  requisitos PAPER.
- Contratos estáticos no vencen por un TTL global de quote. TNA, sesión, NAV,
  margen y operabilidad sí conservan TTL por campo.
- `fee_schedule`, `trading_session`, `price_tick` e `isin` no bloquean OPEN:
  costos, sesión y libro ya tienen autoridades propias en el motor; ISIN es
  enriquecimiento y no participa de la clave PPI completa.

## Reproducción

```bash
python3 scripts/ws_motor_11_replay.py --output docs/evidence/ws_motor_11_before_after.json
python3 -m pytest -q tests/test_ws_motor_11_replay.py
```

El comando crea un snapshot controlado en un directorio temporal, trabaja sobre
una copia y verifica que el SHA-256 del original no cambie.
