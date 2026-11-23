# Handoff de Porota Trading RC6

Corte del **23/11/2026 a las 18:54 ART**. El Deploy V2 #202 quedó `VALIDADO_RUNTIME` en el Droplet. La prioridad del agente entrante es observar la venta PAPER de BONOS/ON en la próxima rueda hábil y registrar por instrumento dónde llega el recorrido. Reconciliar este corte con los últimos [recibos en #1093](https://github.com/mbalbo2023/Porota-trading/issues/1093) y [ownership en #980](https://github.com/mbalbo2023/Porota-trading/issues/980).

## Release e instalación

| Elemento | Valor comprobado |
| --- | --- |
| Rama de producto | `deploy/rc6-pr69-isolated-20260915`. `main` contiene la documentación y el plano de control. |
| HEAD integrado | [PR #1255](https://github.com/mbalbo2023/Porota-trading/pull/1255) · `28f8337e9026fcfea6dace798d5c9e7267f85d8a` |
| Fuente probada y árbol | `969980989a414791e78c7c36f98b59c4e1be161d` · `ae8d1932a78f378634b1fa325f18bc71654aee32` |
| Predeploy V2 | [45270594762 / artefacto 14866480985](https://github.com/mbalbo2023/Porota-trading/actions/runs/45270594762/artifacts/14866480985). 9.128 pruebas aprobadas, cero skips y prueba funcional HTTP PAPER en la misma imagen. SHA-256 del ZIP `e99e58cb91c21f38c212292d6cca4d1d8c587ace1c7afeed4396dcf118196aaf`. |
| Imagen desplegada | `sha256:8fa64b29c93d7e2cf39db8e3a505da4bbaf633494f8e73c038ce73df3f15df12`. Una promoción del artefacto probado. |
| Recibo físico | [45273533386 / artefacto 14866098686](https://github.com/mbalbo2023/Porota-trading/actions/runs/45273533386/artifacts/14866098686). `VALIDADO_RUNTIME`, `PRODUCTION_PAPER / SIMULATION`, observer en ejecución, dashboard saludable, guard `BLOCKED`, `real_orders_sent=0` en la base activa, `source_alert=NONE`. Limpieza selectiva completa. SHA-256 del ZIP `cf47cc645abaf3da79118346200befd1e4467e987310d99480c4ecbc894870c5`. |
| Activación BONOS/ON | `ACTIVE_PAPER`, seis variantes. El postflight verificó la política, su anclaje al release y la firma de la clave del lector EXIT. Hash de política `4094834c5a5eea75c553d0540906fcef2840b8b4cefb05dea80c19a3804805f3`. |
| Ownership del ciclo | [DEPLOY_OWNER liberado](https://github.com/mbalbo2023/Porota-trading/issues/980#issuecomment-7115123790) · [WRITE_OWNER liberado](https://github.com/mbalbo2023/Porota-trading/issues/980#issuecomment-7115124378). |

## Venta PAPER de BONOS y ON

| Familia BYMA | Pesos | USD MEP | USD cable |
| --- | --- | --- | --- |
| BONOS | GD30 | GD30D | GD30C |
| ON | YMCJO | YMCJD | YMCJC |

La política `ops/policy/rc6-fi-sale-paper-model.json` conserva las identidades PPI y el modelo histórico normalizado por variante. Para el simulador se utiliza `A-24HS`, base 100 VN, mínimo y paso de 1 VN. La auditoría registró el plazo como `REQUEST_CANDIDATE`. El plazo consultado y las unidades del modelo tienen esa procedencia. El lector del libro sigue usando el gateway PPI y su presupuesto. [Catálogo físico](https://github.com/mbalbo2023/Porota-trading/actions/runs/45253122314/artifacts/14857301994) · [evidencia archivada](https://github.com/mbalbo2023/Porota-trading/blob/28f8337e9026fcfea6dace798d5c9e7267f85d8a/docs/evidence/rc6-fi-paper-sale-sources.json)

El supervisor congela la decisión y conserva su identificador cuando llega otro libro. El lector EXIT emite el ACK firmado después del COMMIT y selecciona el primer libro nativo elegible posterior en la misma generación PPI. El supervisor verifica la firma, ambos relojes y la clave exacta. El ledger aplica los VN, la profundidad disponible, costos, moneda y liquidación modelada. El firmante genera la clave privada en su propio proceso. Su rotación revoca la clave anterior para nuevas ventas y conserva las evidencias ya comprometidas. [Cambio y pruebas](https://github.com/mbalbo2023/Porota-trading/pull/1255)

El replay con puntas históricas y relojes y firmas de prueba obtuvo 61 VN en GD30, 19 VN en GD30C, 0 VN en GD30D por profundidad insuficiente y 100 VN en cada variante YPF. La activación del circuito quedó verificada en el Droplet con mercado cerrado. El primer asiento PAPER respaldado por datos de rueda sigue `NO_VERIFICADO`. [Reporte del replay](https://github.com/mbalbo2023/Porota-trading/issues/1093#issuecomment-7112589354) · [recibo de activación](https://github.com/mbalbo2023/Porota-trading/issues/1093#issuecomment-7115118453)

## Próximas comprobaciones

1. **Confirmar rueda y preopen.** La próxima rueda prevista en este corte era el martes 24/11. Confirmar el calendario, el release activo y el recibo de preopen de esa misma fecha. Correlacionar cada timer con su `Unit=` efectiva, hora ART, disparo, salida y motivo. Los [32 timers](TIMERS.md) se midieron el 22/11. [Calendario oficial BYMA](https://www.byma.com.ar/mercado/calendario-bursatil) · [fix de preopen y Scheduler #1253](https://github.com/mbalbo2023/Porota-trading/pull/1253)
2. **Auditar la primera salida con datos de rueda.** Arrancar en `READ_ONLY` por GitHub Actions. Si hay una posición PAPER elegible con decisión de venta, seguir en la base activa `paper_fi_sale_witness_handoff`, `paper_causal_decision_freezes`, `paper_causal_decision_receipts` y `paper_fi_native_sales`. Correlacionar clave, commit/ACK, primer libro posterior, generación, cantidad y asiento. Revisar el guard `BLOCKED` y el contador físico de órdenes reales. Publicar evidencia sanitizada en #1093. Si falta posición, disparo, libro o evidencia, registrar la ausencia concreta y el motivo observado. Usar el flujo normal PAPER para esa comprobación.
3. **Revisar las fuentes complementarias en su próxima ventana.** IOL REST es la fuente secundaria SHADOW efectiva desde #201. El cliente del release leyó GD30 y ambos timers estaban activos. Verificar el próximo recibo de ejecución, catálogo FCI, frescura de libros/TNA y base de precios por variante con la evidencia disponible. Cuatro screeners FCI muestreados devolvieron HTTP 500. El caso del canje MCP quedó en soporte. [IOL REST #201 y límites](https://github.com/mbalbo2023/Porota-trading/issues/1093#issuecomment-7108327492) · [RCA](RCA_Y_LECCIONES.md)
4. **Mantener los históricos en su alcance propio.** [#1203](https://github.com/mbalbo2023/Porota-trading/pull/1203) quedó integrado en GitHub. La ejecución del corte y su completitud todavía requieren evidencia de fuente, modalidad, moneda, base de precio y custodia. Para el 07/12, BYMA distingue negociación de liquidación. Reconciliar el comunicado y el calendario vigente antes de cambiar la ingesta. [Handoff histórico](../RC6_20261119_GATEWAY_RELEASE_BLOCKERS_HANDOFF.md) · [backlog del 07/12](https://github.com/mbalbo2023/Porota-trading/issues/1093#issuecomment-7114454816)

El responsable de la primera comprobación es el agente entrante que retome este alcance. La evidencia de rueda debe registrar la fecha y la ventana efectivamente observadas. Una corrección requiere nuevo `WRITE_OWNER` por rutas. Una intervención en el runtime requiere `DEPLOY_OWNER` exclusivo.

## PR abiertos en el corte

| PR | Disposición |
| --- | --- |
| [#1216 ETF](https://github.com/mbalbo2023/Porota-trading/pull/1216) | DRAFT, `SUPERSEDIDO_CON_REFERENCIA`. Sus dos blobs coinciden con el producto #202. Conservar el PR como evidencia hasta que su owner reconcilie el cierre. |
| [#1166 Performance](https://github.com/mbalbo2023/Porota-trading/pull/1166) | DRAFT, `RETENER_CON_CAUSA`. Frente independiente excluido de este release. Mantener la revisión con su owner y evidencia propia. |

## Instrucción para abrir el próximo chat

```text
Continuá Porota RC6 desde este handoff. Reconciliá HEAD y el recibo del Deploy V2 #202 con #1093 y ownership #980.
Prioridad en READ_ONLY por GitHub Actions. Verificá el preopen de la próxima rueda y seguí el primer cierre PAPER BONOS/ON desde la decisión y ACK firmado hasta el primer libro PPI posterior y su asiento. Registrá motivo y evidencia por instrumento.
Reutilizá la evidencia. PAPER/SHADOW y real_orders_sent=0. PPI Watch fuera de cambios. Fix-forward. Sólo preparar otro candidato y Deploy V2 si aparece un defecto reproducido que lo requiere.
```
