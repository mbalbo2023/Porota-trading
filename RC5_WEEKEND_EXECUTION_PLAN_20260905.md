# POROTA RC5 — Weekend Execution Plan

Fecha: 2026-09-05
Base exacta: `release/v17.0.0-rc5` / `852b812d610860b57b579212978443f7450e8855`
Rama de trabajo: `hotfix/rc5-weekend-readiness-20260905`

## Regla de seguridad

Esta rama no autoriza real-money. `PRODUCTION_PAPER`, `SIMULATED` y `real_orders_sent=0` siguen siendo invariantes. No se despliega ningún cambio sin CI, preflight, rollback y postflight.

## Corrección de A3 (#28)

A3 no está "apagado entero". El estado correcto es:

- A3 CEM público: habilitado como reference-data WIP/read-only; products/symbols fueron validados.
- A3/Primary autenticado: cliente read-only existe, pero no se promueve al hot path hasta completar readiness/sidecar.
- A3 live: OFF como fuente primaria; PPI conserva autoridad live.
- A3 order routing: OFF absoluto.
- A3 history: NO debe permanecer OFF por política. Debe pasar a background history en cuanto exista alineación inequívoca de identidad PPI↔A3 y una muestra histórica con contenido válido.

El motivo del OFF histórico temporal es de **identidad/evidencia**, no de estrategia: una corrida con overlap cero no permite atribuir correctamente una serie A3 a un instrumento PPI. Encenderla a la fuerza produciría falsa cobertura o mezclas de símbolos.

### Gate A3_HISTORY_BACKGROUND_ON

1. CEM `symbols/products` legibles.
2. PPI derivative catalog no vacío.
3. overlap exacto o crosswalk explícito/versionado; nunca automap por heurística.
4. `closing-prices` o `getTrades` con contenido válido para instrumentos representativos.
5. escritura a History Store v2 con provenance `A3_*` y clave completa.
6. ninguna participación síncrona en decisiones live.
7. order routing ausente/false.

Si 1–7 quedan GREEN durante el fin de semana, A3 History se enciende como **background ingestion**. No hace falta esperar a real-money ni a promoción live.

## Paralelos activos

- staged auditor fixes: `RC5_WEEKEND_AUDIT_FIXES.patch`
- P0-4 PAPER state: `scripts/porota_rc5_paper_state_inventory.py`
- P0-5 Sunday Readiness: `scripts/porota_rc5_sunday_readiness.sh`
- A3 identity gate: `scripts/porota_a3_catalog_alignment_rc5.py`
- safety tests: `tests/test_rc5_weekend_readiness_tools.py`

## Próximos bloques

1. materializar fixes de auditoría en source y ejecutar focal/full CI;
2. resolver RC5-01/09 paginación/doble render;
3. rotular MFE/MAE como `NO_MEDIDO` hasta wiring real;
4. reparar reflow global tablet/Voice Access;
5. centralizar Telegram weekend severity;
6. ejecutar P0-4/P0-5 en candidato desplegable;
7. validar A3 alignment y, si GREEN, habilitar history background;
8. completar rollback externo por digest antes del deploy.
