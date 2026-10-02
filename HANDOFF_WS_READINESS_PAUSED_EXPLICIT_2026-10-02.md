# HANDOFF — WS READINESS PAUSED_EXPLICIT — 2026-10-02

## Estado de salida

**AUDITORÍA COMPLETADA — VALIDADO_RUNTIME — READ_ONLY**

No hubo deploy ni cambios de runtime.

### Workstream
- ID: `WS-READINESS-PAUSED-EXPLICIT-AUDIT-20261002`
- branch: `audit/rc6-paused-explicit-20261002`
- base productiva: `136453dc5b6d4146057142bcdd76b2f6b8d474e0`
- productiva: `deploy/rc6-pr69-isolated-20260915`
- modo: READ_ONLY
- PPI Watch: NO TOCADO
- DB writes: 0
- promotions: 0
- real routes: 0

## Leer primero

1. `AUDITORIA_PAUSED_EXPLICIT_RC6_2026-10-02.md`
2. `PAUSED_EXPLICIT_MATRIX_RC6_2026-10-02.json`
3. `CHECKPOINT_WS_READINESS_PAUSED_EXPLICIT_AUDIT_2026-10-02.md`

## Resumen mínimo que el siguiente chat debe conservar

`candidate_identity_v2` runtime:
- total 13.991
- AVAILABLE 8.079
- PAUSED_EXPLICIT **4.066**
- OBSERVED_SHADOW 1.838
- STALE 8

Los 4.066 PAUSED_EXPLICIT:
- Opciones 2.376
- Obligaciones 1.401
- ON 91
- Futuros 198
- todos `can_simulate=0`

Cruce exacto catálogo por:
`ticker+instrument_type+market+currency+settlement`
- matched: **4.066/4.066**
- missing full-key: 0

Capability:
- MISSING_CURRENCY_OR_MARKET: 1.186
- CONTRACT_EVIDENCE_REVIEW_REQUIRED: 1.114
- NEEDS_NOMINAL_UNITS: 811
- NEEDS_OPTION_CONTRACT: 757
- NEEDS_FUTURES_MARGIN_AND_CONTRACT: 198

Catalog status:
- AVAILABLE: 1.578
- OBSERVED_SHADOW: 2.243
- STALE: 245

## Hallazgos prioritarios

### A. 1.112 Opciones — contrato presente pero change review bloqueante

- 1.114 review-required total.
- 1.112 son Opciones y tienen `financial_contract_v17`.
- 5.865 `CHANGED_REVIEW_REQUIRED` correlacionados.
- source_class: 100% `IOL_STRUCTURED_API`.
- 3–7 cambios por identidad.
- mediana del último cambio: ~0,28 h.
- el cambio de hash es real como evento; su causa por campo es **NO_VERIFICADO**.

**Siguiente acción recomendada:** READ_ONLY diff previous/current `evidence_json` por campo antes de cualquier aceptación/promoción.

### B. 2 OBLIGACIONES review-required sin change-row correlacionado

**NO_VERIFICADO.**

No asumir que comparten la misma causa que las Opciones. Investigar por full key y provenance.

### C. 1.186 MISSING_CURRENCY_OR_MARKET

El nombre es genérico, pero runtime prueba:
- currency UNKNOWN: 1.186
- market missing: 0
- settlement missing: 0

243 tienen alguna clave `currency` no vacía en metadata complementaria, pero canonical currency sigue UNKNOWN.

**No mapear automáticamente.** Auditar normalización/autoridad.

### D. 811 NEEDS_NOMINAL_UNITS

- 720 OBLIGACIONES
- 91 ON
- 25 contienen `units_per_lot` IOL explícito.

Esos 25 son el primer subconjunto para verificar bridge dimensional. No promover el resto por inferencia.

### E. 757 NEEDS_OPTION_CONTRACT

No basta `units_per_lot`.
Los gaps estructurados incluyen:
- cash_multiplier
- expires_at
- minimum_quantity
- option_right
- quantity_step
- strike
- underlying

Mantener fail-closed hasta contrato completo.

### F. 198 Futuros

- 146 AVAILABLE
- 52 STALE
- 134 de los 146 frescos faltan multiplier/expiry/underlying
- 12 faltan además minimum_quantity/quantity_step/paper_margin_policy

### G. Stale/ambiguity

- stale identity >14 días: 245
- retry identity ambiguous: 182

No elegir identidad arbitrariamente.

## Semántica de código confirmada

`bu_instrument_catalog.sync_candidate_universe()`:
- calcula `identity_reasons()`;
- cualquier reason en OPCIONES/FUTUROS/ON/OBLIGACIONES se proyecta a `PAUSED_EXPLICIT`;
- por eso el estado es un agregador de motivos, no una sola causa.

`PAPER_PPI_IDENTITY_LKG_SECONDS = 14 * 86400`.

`rc6_contract_bridge.hard_blocked_bridge()`:
- los `MISSING:*` por sí solos no son hard block de review;
- `CHANGE_REVIEW_REQUIRED`, conflictos, invalid provenance, etc. sí lo son.

`cp_contract_evidence_v2_hf6.evidence_hash()`:
- SHA256 de `canonical_json(evidence)`;
- `observed_at` se persiste aparte.
- NO está probado todavía qué campo cambia en IOL.

## Evidencia reproducible

### Full matrix
- run `37027360445` SUCCESS
- artifact `11235234620`
- digest `sha256:c378c521a7d7b14f758c7a411d478f6efbc2d065943a36966c389bf92065d79c`

### Deep metadata
- run `37027851630` SUCCESS
- artifact `11236375466`
- digest `sha256:78cb58d9154dc8a53ed4d4e4f5a53dfe1a9ea17136993eaadad656d9ba0e3934`

### Change review
- run `37028217039` SUCCESS
- artifact `11236685991`
- digest `sha256:aeebafe587df6dd5ae43a28e86ced30610648bdf536d95d5ab675423be132003`

Artifacts retención 30 días. Si expiraron, volver a ejecutar los workflows de esta branch; son READ_ONLY.

## Próximo workstream recomendado

**WS-PAUSED-01 — IOL option contract hash RCA — READ_ONLY**

Scope:
- sólo 1.112 Opciones `CONTRACT_EVIDENCE_REVIEW_REQUIRED`;
- comparar previous/current evidence por `change_id`;
- agrupar campo cambiado;
- demostrar si el cambio es contractual o ruido/normalización;
- revisar si review debería ser “ack de cambio” idempotente;
- no promover;
- no mutar DB;
- no tocar PPI Watch.

Criterio de cierre:
- tabla de campos que cambian + frecuencia;
- RCA;
- propuesta FIX/GUARD/TEST;
- lista determinista de identidades que podrían revalidarse después.

Luego continuar con:
- currency normalization;
- nominal-unit bridge;
- option contract completion;
- futures completion;
- stale/ambiguity refresh.

## Ownership

Este workstream queda **sin ownership bloqueante**. Es READ_ONLY y puede ser retomado por cualquier chat.

No mergear esta branch a productiva por el solo hecho de contener auditorías/workflows. No hacer deploy desde este handoff.
