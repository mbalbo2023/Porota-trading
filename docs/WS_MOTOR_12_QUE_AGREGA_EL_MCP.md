# WS-MOTOR-12 — qué agrega el MCP de IOL

IOL queda conectado como complemento inmediato de PPI mediante este camino:

`iol_family_reference_latest.json -> Evidence v2 -> Contract Bridge -> catálogo PPI -> readiness`

El observer ingiere el cache estructurado en cada reconciliación. La ingesta
exige una identidad PPI exacta antes de escribir evidencia; una fila IOL nunca
crea ni sobrescribe la identidad primaria.

En el replay controlado aporta 23 valores observados. Diecinueve son evidencia
usable y cuatro tasas de caución quedan informativas porque no traen timestamp
del proveedor. Esa diferencia corrige la métrica WS11: captura reciente no es
sinónimo de dinámica fresca.

| Aporte | Uso | Guard |
|---|---|---|
| renta fija | vencimiento/base y contrato parcial | identidad PPI exacta; sin inferir step |
| opciones | underlying/right/strike/expiry | lote y cantidad siguen específicos de la serie |
| FCI | inventario descriptivo | no sustituye clase PPI ni términos de suscripción |
| cauciones | plazo/tasa/mínimo/fecha cuando aparecen | sin provider timestamp no habilita PAPER |

No se invocan `validate_*`, `place_*`, `subscribe_*`, `redeem_*` ni rutas de
cuenta real. El cache es read-only y su falla no impide que PPI u otra fuente
oficial continúen la reconciliación.

