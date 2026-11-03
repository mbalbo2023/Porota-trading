# WS-MOTOR-12 — ERROR → RCA → FIX → GUARD → TEST → EVIDENCIA

## Dinámica sin timestamp proveedor

- ERROR: cuatro tasas de caución contaban como evidencia fresca.
- RCA: el merge usaba `observed_at` (captura) como reloj dinámico.
- FIX: provenance conserva `provider_timestamp`/`freshness_basis`; TTL usa sólo
  `PROVIDER_TIMESTAMP`.
- GUARD: valor dinámico sin timestamp proveedor entra en `missing_dynamic`.
- TEST: regresiones de broker parity y replay.
- EVIDENCIA: 73/222 WS11 pasa a 69/216 WS12; las cuatro filas quedan
  `DYNAMIC_WITHOUT_PROVIDER_TIMESTAMP`.

## Ejes simultáneos

- ERROR: `missing_contract` ocultaba `missing_dynamic`.
- RCA: retorno temprano del evaluador.
- FIX: se calculan contrato, dinámica, stale y eventos antes de clasificar.
- GUARD: `blocker_axes` agrega DATA y DYNAMIC independientemente.
- TEST: futuro vacío reporta ambos ejes.
- EVIDENCIA: DLR y las cuatro cauciones exhiben ambos blockers en el replay.

## Adcap Ahorro Pesos

- ERROR: `1.000` se interpretó como ARS 1 y `ADRDOLA` como clase PPI.
- RCA: semántica numérica en-US y mezcla de ticker IOL con identidad PPI.
- FIX: `ADCAP.AP.A`, ARS 1.000 mínimo, ARS 1 step; parser es-AR explícito.
- GUARD: test de `1.000` y `1.000,50`; FCI rechaza montos bajo ARS 1.000.
- TEST: locale, replay, bridge y lifecycle conectado.
- EVIDENCIA: `READY_PAPER_FCI_SUBSCRIPTION` para el testigo exacto.

## FCI y futuros

- ERROR: lifecycle durable existía aislado y dos requirements no publicados
  bloqueaban OPEN.
- RCA: faltaba unión al catálogo/PaperBroker; se duplicaba estado PPI y se
  pedían dos márgenes aunque Clearing publica uno.
- FIX: ejecutor PAPER conectado, contrato FCI dedicado, futures bridge y margen
  único con piso PAPER conservador.
- GUARD: tipos contractuales, transiciones idempotentes, ninguna ruta broker.
- TEST: suscripción, variación/margen, bridge/capability y suite completa.
- EVIDENCIA: FCI promovido; futuros quedan habilitados por familia pero cada
  contrato sin margen fresco/contrato exacto continúa PAUSED.

