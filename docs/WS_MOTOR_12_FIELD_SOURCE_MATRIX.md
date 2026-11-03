# WS-MOTOR-12 — matriz campo/fuente/necesidad

La matriz canónica machine-readable es
`docs/evidence/ws_motor_12_before_after.json` (`field_matrix`, 216 filas).
Conserva identidad completa, campo, valor/unidad, fuente, timestamp proveedor,
timestamp de captura, base de frescura, derivación, consumidor PAPER, perfil,
estado y blocker por instrumento.

## Resultado reproducible

| Métrica | Before | After |
|---|---:|---:|
| Catálogo testigo | 20 | 20 |
| Identidades PPI exactas | 10 | 11 |
| Candidates evaluados | 10 | 11 |
| Instrumentos READY integrados | 2 | 3 |
| Filas requeridas evidenciadas | 0 | 69 |
| Dinámicas observadas sin timestamp proveedor | — | 4 |
| Filas unresolved | 216 | 143 |
| Ambigüedad | 0 | 0 |

Las cuatro dinámicas sin timestamp proveedor son tasas de caución heredadas.
Tienen valor informativo, pero `status_after=DYNAMIC_WITHOUT_PROVIDER_TIMESTAMP`
y no suman a las 69 filas evidenciadas.

## Procedencia de las 69 filas válidas

| Fuente | Filas |
|---|---:|
| PPI structured/catalog | 31 |
| IOL MCP/API estructurado | 19 |
| PPI DOM autenticado heredado | 13 |
| PPI documentación oficial | 1 |
| A3 documentación oficial | 2 |
| Regla oficial derivada | 3 |

PPI conserva siempre la identidad primaria. IOL, A3/Clearing y reglas
derivadas completan campos sin redefinir `family+ticker+market+currency+settlement`.

## Resultado OPEN por instrumento

| Familia | Instrumento | Resultado | Ejes residuales |
|---|---|---|---|
| BONOS | GD30 | READY_PAPER_SPOT | eventos futuros condicionales |
| OPCIONES | GFGC6000OC | READY_PAPER_OPTION_LONG | ejercicio condicional |
| FCI | ADCAP.AP.A | READY_PAPER_FCI_SUBSCRIPTION | NAV sólo al evento |
| BONOS/LETRAS/ON | restantes 6 testigos | PAUSED | mínimos/step/base o identidad exacta individual |
| OPCIONES | 3 series restantes | PAUSED | contrato individual incompleto/serie ajustada |
| CAUCIONES | 4 plazos/monedas | PAUSED | contrato y dinámica; todos los ejes se reportan juntos |
| FUTUROS | 3 contratos | PAUSED | contrato individual y margen vigente con timestamp proveedor |
| FCI | IOLCAMA / ADCUSAD | PAUSED | identidad PPI exacta y términos individuales |

No se extrapoló ningún campo entre instrumentos. Los PAUSED son individuales,
no una deshabilitación de la familia ni una afirmación de inexistencia.

## Requirements corregidos

- FCI OPEN ya no duplica `subscription_status`: el catálogo primario PPI
  `AVAILABLE` es la autoridad de admisión; NAV/fecha se exigen al evento.
- Futuros usan el único `margin_requirement` publicado por Clearing. PAPER
  mantiene el 100 % de ese margen como reserva y piso conservador; no inventa
  un segundo maintenance margin de broker.
- Adcap usa identidad PPI `ADCAP.AP.A`, mínimo ARS 1.000 y step ARS 1.
- `capture_timestamp` nunca rejuvenece una dinámica.

## Reproducción

```bash
python3 scripts/ws_motor_11_replay.py --output docs/evidence/ws_motor_12_before_after.json
python3 -m pytest -q tests/test_ws_motor_11_replay.py tests/test_ws_motor_11_broker_parity_evidence.py
```

