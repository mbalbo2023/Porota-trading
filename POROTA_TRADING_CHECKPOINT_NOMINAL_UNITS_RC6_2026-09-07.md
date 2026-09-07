# POROTA TRADING — CHECKPOINT RC6 — NOMINAL UNITS

Fecha: 2026-09-07
Estado: P1 OPEN / FAIL-CLOSED CORRECTO
Rama: `feature/rc6-fixed-income-nominal-units-20260907`

## Hallazgo

Durante la rueda PAPER se observaron decisiones `HOLD` con razón `NEEDS_NOMINAL_UNITS`.
La auditoría read-only de runtime de 2026-09-07 17:25Z confirmó al menos:

- `MRCXC` — familia ON, BYMA, A-24HS, USD_CCL; `contract_json=null`; quote no disponible en la muestra auditada; `opening_block_reason=NEEDS_NOMINAL_UNITS`.
- `TX28D` — familia BONOS, BYMA, A-24HS, USD_MEP; quote y book disponibles; `contract_json=null`; `opening_block_reason=NEEDS_NOMINAL_UNITS`.

No es un error de ejecución: es un bloqueo preventivo correcto. POROTA no debe dimensionar bonos/ON como si fueran acciones.

## Lógica de negocio

Para renta fija la cantidad puede representar valor nominal (VN) y el precio puede estar expresado por una base contractual distinta de una acción unitaria. Por ello el motor necesita, como mínimo, una convención verificada que permita construir:

- `cash_multiplier`;
- `quantity_step` / lote mínimo;
- moneda/plaza;
- mercado;
- settlement;
- fuente de metadata y evidencia;
- cualquier mínimo o step contractual adicional aplicable.

El contrato financiero RC6 ya modela explícitamente `cash_multiplier` y `quantity_step`; no se autoriza inferirlos desde el ticker.

## Evidencia externa de contraste — IOL read-only

Para `TX28D`, IOL devolvió:

- descripción: Bonos Del Tesoro Boncer 2.25% $ 2028;
- tipo: TIT. PUBLICOS;
- mercado: BCBA;
- currency: USD;
- `units_per_lot=100`;
- `term=T1`;
- especie ARS relacionada: `TX28`;
- especie dólar: `TX28D`.

Esto es evidencia útil para el mapeo, pero **no autoriza por sí sola escritura canónica ni apertura PAPER**. Debe reconciliarse con PPI/Contract Evidence y con la semántica A-24HS/T1 de POROTA.

Para `MRCXC`, la consulta IOL de asset info no resolvió el símbolo. Por tanto no se debe completar metadata por inferencia. Además, en la muestra auditada PPI no entregaba quote ejecutable.

## Trabajo paralelo autorizado en esta rama

1. localizar el adaptador que decide `NEEDS_NOMINAL_UNITS`;
2. definir normalizador de unidades nominales/lote por familia BONOS/LETRAS/ON;
3. separar estrictamente metadata observada de reglas de convención;
4. incorporar evidencia PPI/Contract Evidence y contraste IOL donde exista;
5. mantener `UNKNOWN`/HOLD ante conflicto o ausencia;
6. no tocar observer live durante la rueda;
7. no habilitar futuros/opciones como efecto colateral.

## Tests obligatorios antes de cualquier despliegue

- bono con VN verificado calcula notional con multiplicador correcto;
- ON con metadata ausente permanece `NEEDS_NOMINAL_UNITS`;
- ticker no puede determinar nominal units por heurística;
- T1/IOL no se convierte a A-24HS sin contrato explícito probado;
- moneda ARS/USD_MEP/USD_CCL no se mezcla;
- cantidad respeta `quantity_step`;
- no se aceptan cantidad fraccionaria si el contrato no la permite;
- quote 0/unavailable nunca pasa por disponer de nominal units;
- falta de contract evidence => HOLD;
- `real_orders_sent=0` y capacidad de orden real siguen bloqueados.

## Veredicto

`NEEDS_NOMINAL_UNITS` es hoy un comportamiento de seguridad correcto y, simultáneamente, un gap funcional P1 a cerrar para que BONOS/ON elegibles puedan entrar al ciclo PAPER con dimensionamiento financiero correcto.
