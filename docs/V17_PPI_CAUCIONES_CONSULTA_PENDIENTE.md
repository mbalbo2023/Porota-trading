# Consulta pendiente a PPI — cauciones colocadoras

**Estado:** PENDIENTE_ENVIO_Y_RESPUESTA_PPI

**Responsable del envío:** Martín Balbo

**Envío:** a cargo del usuario; todavía no confirmado. El asistente no envió el correo.

**Registrado:** 28/08/2026

**Impacto:** bloquea únicamente la habilitación real del adaptador de cauciones.

No bloquea el desarrollo y las pruebas del resto de v17. No repetir el
diagnóstico `v17-public-probe-1`: ya completó autenticación, configuración y
datos públicos. No habilitar cauciones a partir de una familia enumerada o una
respuesta parcial.

Al confirmarse el envío, actualizar el estado a `ESPERANDO_RESPUESTA_PPI` y
registrar su fecha. La recepción del correo no cierra automáticamente el
pendiente: primero debe revisarse la suficiencia de los datos y las pruebas.

## Mensaje para enviar

```text
Asunto: API PPI — consulta técnica de caución colocadora

Usamos ppi-client 1.2.4 en producción. Login, configuración y cotizaciones de ALUA funcionan correctamente.

Configuration devuelve CAUCIONES y COLOCAR-CAUCION. Sin embargo, SearchInstrument con Type=CAUCIONES, Market=BYMA y Ticker/Name=CAUCION, PESOS o DOLAR responde HTTP 200 con lista vacía.

Necesitamos un ejemplo oficial anonimizado que indique:

1. Consulta e identificador correctos para obtener cauciones por moneda y plazo.
2. Cómo interpretar el libro: unidad de tasa, cantidad/capital y lado correspondiente a una colocación.
3. Capital mínimo, incrementos, base de días, liquidación y vencimiento.
4. Ejemplo de presupuesto con comisiones, derechos, impuestos y rendimiento neto.

Sólo queremos cauciones colocadoras financiadas con saldo liquidado disponible, sin endeudamiento.

No solicitamos ejecutar órdenes. Podemos trabajar con ejemplos sin credenciales ni datos de cuenta.
```

## Qué debe conservarse de la respuesta

- endpoint, método y parámetros válidos, sin credenciales ni tokens;
- ticker o identificador, familia, mercado, moneda y plazo;
- significado y unidad de `price`, `quantity`, tasa y lado del libro;
- capital mínimo, paso de cantidad, base anual y regla de vencimiento;
- presupuesto anonimizado con comisiones, derechos, impuestos y neto;
- aclaración de si el ejemplo corresponde a colocadora, Sandbox o producción;
- versión de API/SDK y fecha de vigencia de la información.

Cuando llegue la respuesta, adjuntarla o pegarla en el chat. Antes de modificar
el adaptador se contrastará con la documentación oficial y se agregarán fixtures
y pruebas sin órdenes reales. No incorporar datos personales, número de cuenta,
API key, API secret, tokens ni capturas que los contengan.

## Condición para cerrar este pendiente

La respuesta debe permitir identificar y valorar una caución colocadora sin
inferir tasa, capital, lado, plazo, moneda o costos. Hasta entonces:

- `HOLD_UNVERIFIED_TERMS`;
- sólo colocadoras;
- sólo saldo liquidado, libre de compromisos y de la misma moneda;
- sin tomadoras, endeudamiento ni producido pendiente de liquidación;
- sin presupuesto, confirmación u orden real.
