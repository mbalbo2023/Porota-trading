# POROTA TRADING — HF6-v2 Family Readiness

## Regla general

Ninguna familia se habilita por inferencia. `READY_PAPER_CANDIDATE` tampoco equivale a `READY_PAPER`: el gate final exige contrato, costos, sizing, simulador especializado, freshness y pruebas de integración.

PPI es autoritativo para el camino live cuando tiene todos los campos requeridos y frescos. A3/CEM/Data912 son background y una discrepancia con ellos NO pone en HOLD una operación PPI válida.

## READY_PAPER

### ACCIONES

Estado: READY_PAPER para el flujo spot PAPER ya existente, sujeto a los gates de riesgo/caja/liquidez/costos/sesión. HF6-v2 cambia el admission gate de cantidad fija a riesgo concurrente dinámico.

### CEDEARS

Estado: READY_PAPER para el flujo spot PAPER ya existente, sujeto a los mismos gates y a identidad/conversión contractual conocida.

## HOLD / integración pendiente

### BONOS

Estado: HOLD.

Falta cerrar de forma inequívoca cantidad mínima/múltiplo/step de orden y semántica exacta de unidad de cotización/VN para cada identidad operable. `DatosTecnicos` y web autenticada enriquecen mucho el contrato, pero no habilitan sizing por inferencia.

### LETRAS

Estado: HOLD.

Requiere contrato completo de precio/VN, mínimo/step, vencimiento, costos, sesión y simulador especializado.

### ON / OBLIGACIONES NEGOCIABLES

Estado: HOLD.

Requiere contrato completo, pago/cupón/amortización, precio/VN, mínimo/step, costos y simulador específico.

### CAUCIONES

Estado: HOLD para automatización.

El cash sweep está implementado como planner/orquestador PAPER fail-closed, pero sólo puede promover una candidata si se verifican oferta, TNA, principal disponible, mínimo, step, base de días, fee quote exacto, momento de cobro, vencimiento, cutoff oficial, obligaciones de caja y deadline de liquidez. No usar un porcentaje fijo de reserva.

### OPCIONES

Estado: HOLD.

A3/CEM aporta catálogo/reference/histórico; PPI sigue siendo fuente live. Falta cerrar lot size/multiplicador/tick, costos, ejercicio, settlement, simulador y sizing especializado antes de READY_PAPER.

### FUTUROS

Estado: HOLD.

A3/CEM aporta catálogo/reference/histórico y contratos de derivados. Falta completar/validar multiplicador, tick value, margen, settlement/adjustment, costos, session y simulador especializado. A3 no es gate live síncrono.

### FCI_LOCAL

Estado: HOLD / potencial condicional.

Requiere NAV vigente/publicado, mínimo/step, cutoff, plazo de rescate, costos y flujo específico de suscripción/rescate.

### LICITACIONES

Estado: HOLD / potencial condicional.

Requiere ventana, estado, mínimo/step, reglas competitivas/no competitivas, prorrateo, settlement, costos y simulación del evento.

### ETF

Estado: HOLD/PARTIAL.

Requiere contrato completo y política de mercado/exchange antes de habilitación.

### FCI_EXTERIOR

Estado: HOLD.

Requiere contrato, jurisdicción/restricciones, NAV/cutoff/plazo/costos y simulador específico.

### ACCIONES_USA / ACCIONES_EXTERIOR

Estado: HOLD.

Requiere exchange, sesión, fractional policy, settlement, costos y ejecutor/simulador especializado.

### CANJES

Estado: HOLD.

Familia/evento detectado, pero requiere elegibilidad, ratio, ventana, mínimo/step, settlement, costos y simulador del canje.

## OBSERVATION_ONLY / no ejecutables como familia de trading

Índices, monedas y tasas pueden alimentar información/contexto cuando la fuente sea válida, pero no se promueven como posiciones spot por aparecer en catálogo.

## Históricos

La ingesta de históricos es independiente de READY_PAPER. Una familia HOLD puede acumular datos si la identidad financiera es suficiente. History Store v2 usa identidad completa `symbol + instrument_type + market + settlement + date`, conserva versiones append-only y aplica precedencia por fuente.

PPI es historia primaria donde exista; Data912 puede reconciliar ACCIONES/CEDEARS/BONOS en batch; CEM puede aportar cierres/reference de derivados en background. Ninguna de estas fuentes históricas decide una entrada live.
