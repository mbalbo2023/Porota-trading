# POROTA TRADING RC6 — CAUCIONES / ADDENDUM SOPORTE PPI

Fecha: 2026-09-08
Fuente: tercera respuesta escrita de soporte PPI aportada por el usuario.

## Confirmaciones nuevas

### Comisiones PPI
- Caución Bursátil Colocadora en Pesos: **2% + IVA anual**.
- Caución Bursátil Colocadora en Dólares: **hasta 1% + IVA anual**.
- Derechos y demás cargos dependen del mercado, no de PPI.
- No existe un campo dedicado en `Order/Budget` donde se vea por separado ese detalle contractual según soporte.
- Aun así, PPI confirma que `Order/Budget` es el mecanismo correcto para obtener un presupuesto previo con comisión, derechos, impuestos y rendimiento neto.

Implicación de modelado:
- ARS: la comisión anual PPI de 2% puede tratarse como contractual exacta.
- USD: `hasta 1%` NO debe hardcodearse como 1% exacto; debe resolverse con Budget/evidencia de cuenta o configuración contractual explícita.
- El IVA no debe asumir una alícuota numérica desde este mail; el mail sólo confirma `+ IVA`.
- Derechos de mercado y otros cargos deben permanecer externos/configurables o tomarse del Budget; no inventar importes.

### Horario / timezone
- La fecha operativa debe interpretarse en **horario de Argentina**.
- Mercado indicado por soporte: **10:30 a 17:00**.
- Si la orden se carga dentro de ese horario, se concierta ese mismo día.

Implicación:
- POROTA puede usar su timezone canónico existente `America/Argentina/Buenos_Aires` para esta regla.
- La ventana 10:30–17:00 puede modelarse como contrato de concertación del mismo día.
- Esta regla NO constituye prueba de aceptación ni ejecución de una orden.

### Dólar
- Para cauciones `DOLARn`, el dólar relevante es **MEP**.
- PPI indica que no existe campo o endpoint recomendado específico para validar ese saldo antes de presupuestar.

Implicación:
- `CAUCION_DOLAR_BALANCE_KIND=USD_MEP` queda GREEN contractual.
- La disponibilidad real de saldo debe seguir validándose por evidencia de cuenta/runtime existente y no por un endpoint de cauciones inventado.

### Preguntas 13–16 finalmente mapeadas
13. Presupuesto previo con comisión, derechos, impuestos y rendimiento neto:
- usar `Order/Budget`.

14. `Order/Budget` para presupuestar COLOCAR-CAUCIÓN sin confirmar posteriormente:
- **Sí, es correcto**.

15. Endpoint específico de cauciones fuera de la documentación pública de MarketData:
- **No existe**.

16. Habilitación adicional de cuenta necesaria para consultar esos datos:
- **No existe**.

## Semáforo actualizado

GREEN contractual:
- discovery / ticker / plazo;
- días corridos;
- Actual/365;
- `price` = TNA;
- Bids para colocadora;
- quantity book = monto tomador;
- mínimos ARS 100000 / USD 100;
- step 1 e integer-only;
- vencimiento derivado;
- timezone Argentina;
- ventana 10:30–17:00 para concertación del día;
- ARS comisión PPI 2% + IVA anual;
- USD comisión PPI hasta 1% + IVA anual;
- DOLAR = USD MEP;
- `Order/Budget` es el mecanismo correcto de presupuesto sin confirmar;
- no hay endpoint cauciones oculto/específico adicional;
- no hay habilitación adicional para consultar esos datos.

YELLOW:
- profundidad/paginación del Book: soporte dijo que lo validará y responderá;
- alícuota numérica de IVA no especificada en este mail;
- derechos de mercado / otros cargos exactos;
- tasa exacta de comisión USD dentro del máximo de 1%;
- payload completo y response exacto de Budget;
- semántica Confirm/Cancel y resultado sandbox;
- reglas generales de disponibilidad si la fecha teórica cae fuera de proceso de liquidación.

RED / NOT_PROVEN:
- ejecución API empírica de cauciones;
- READY_PAPER;
- AUTO_PLACEMENT.

## Seguridad
- `CAUCIONES_AUTO_PLACEMENT=false` permanece.
- No realizar Budget/Confirm/Cancel en producción como prueba.
- Budget de cauciones se estudiará en sandbox, siguiendo recomendación expresa de PPI.
- Mantener `real_orders_sent=0`.
