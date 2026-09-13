# POROTA TRADING — CHECKPOINT COBERTURA CONTRACTUAL PENDIENTE — 2026-09-13

## Estado

Pendiente obligatorio antes de considerar una familia financiera completamente `READY_PAPER`.

El trabajo de descubrimiento e histórico PPI Web no reemplaza la validación contractual y operativa por familia.

## Criterio de cierre por familia

Cada familia debe quedar evaluada explícitamente en cinco dimensiones:

- `DISCOVERY`
- `HISTORICO`
- `CONTRACT_METADATA`
- `OPERABILITY_RULES`
- `READY_PAPER`

No marcar `READY_PAPER` mientras `CONTRACT_METADATA` u `OPERABILITY_RULES` estén incompletos.

## Pendiente específico: Cauciones

Validar y documentar, como mínimo:

- enumeración de plazos/especies de caución en PPI;
- tasa, moneda, mercado y vencimiento;
- monto mínimo, lote/step y demás restricciones cuantitativas;
- estabilidad o construcción del identificador por plazo/fecha;
- endpoint/ruta/página PPI Web de donde proviene la información;
- disponibilidad y semántica de histórico, si existe;
- elegibilidad y reglas de operabilidad;
- metadata contractual/económica necesaria para evaluación del motor;
- separación entre cotización, contrato y reglas operativas;
- validación read-only y sin órdenes reales.

## Otras familias a revisar bajo el mismo criterio

- Bonos
- Letras
- Obligaciones Negociables
- Opciones
- Futuros
- FCI
- FCI Exterior
- CEDEARs
- Acciones
- Acciones USA
- ETFs
- Licitaciones
- Índices
- Monedas / Tasas
- cualquier otra familia PPI incorporada al universo

## Regla de seguridad

Toda investigación contractual PPI Web debe ser read-only. Prohibido comprar/vender, cursar órdenes, cauciones, suscripciones/rescates FCI, licitaciones, opciones/futuros, o modificar cuenta/seguridad/2FA. No registrar credenciales, OTP, cookies ni tokens.

## Relación con la ingesta histórica

La ingesta histórica residual puede continuar por su carril una vez que cumpla sus propios gates de seguridad y calidad, pero la habilitación funcional de una familia para `READY_PAPER` queda bloqueada hasta cerrar este checkpoint contractual.

## Estado actual

`CONTRACT_COVERAGE_CHECKPOINT=PENDING`
