# Revisión independiente de la derivación del índice SHADOW

Se reprodujo una omisión en el digest lógico de la cuarta pieza del publisher.
El encoding `rc6.shadow-ui-projection-lines.v1` ligaba header, dataset, ordinal
y payload, pero no las columnas SQL usadas para filtrar, ordenar y seleccionar
cohortes. Cambiar sólo `projection_rows.currency` en una copia en memoria podía
seleccionar como USD filas cuyo payload nativo conservaba identidad ARS, sin
alterar ese digest lógico.

La reproducción utiliza el productor PAPER/SHADOW real de
`tests/rc6_dashboard_native_fixture.py`, con 25 instrumentos sintéticos, 50 filas
planner, corte `2026-10-05T16:00:00+00:00` y sus precios/clock originales.
La mutación es exclusivamente
`UPDATE projection_rows SET currency='USD' WHERE dataset='planner' AND ticker='T000'`.
El filtro USD pasa de cero a dos coincidencias; los dos payloads siguen ARS.
No se modifican report, checkpoint, status, manifest, CURRENT ni los bytes del
miembro publicado. Los hashes wire y la custodia verificarían una reescritura
de ese miembro. El hallazgo demuestra una brecha en la derivación lógica
independiente del índice; no demuestra un bypass de esos controles.

## Fuente y reproducción conservada

El [probe conservado](../../../tests/probes/rc6_projection_index_derivation_probe.py)
tiene SHA256 `53758feb042b618ec628203bf8094ed9f866fabbdcf0ec3f16a4cfaee7c5b52c`.
Los dos ensayos usan exactamente sus mismos bytes, inputs y mutación. El probe
lee un índice externo del `git archive`, verifica todos sus archivos antes y
después y registra el origen de todos los módulos importados. Sus archivos de
salida y sus bases sintéticas están fuera del source archive. La copia del
probe dentro del repositorio preserva los bytes del script temporal ejecutado.

| Papel de la evidencia | Source SHA / tree | Resultado y alcance |
| --- | --- | --- |
| [RED reproducible](projection_index_red_e321d8ad_receipt.json) | `e321d8ad3dace55893105bfc13969ae32435abea` / `21d7da4258ca204335757c73b77184c516d3d4d6` | Encoding lines acepta el cambio de índice sin detectar mismatch; el miembro publicado permanece intacto |
| [GREEN integrado intermedio](projection_index_green_f6e46b9d_receipt.json) | `f6e46b9d918a6494afa24053c4cfc7d29b27202b` / `a290427fea4dc8d83f05c50ed1e957f0be2da1b4` | La misma mutación devuelve `SHADOW_PROJECTION_DERIVATION_MISMATCH`; el control previo válido coincide con el digest del manifest |

RED usa archive SHA256
`0dedb17c156b1c52813b513faff1daccb17c8f94676d3d2d732dcec3f0f43ea7`:
1.192 archivos sin cambios, 83 imports del repositorio dentro del archive,
cero imports de checkouts externos y cero intentos de red. Su receipt tiene
SHA256 `f39a1534f02778b4a72aba314a85807e045f5d263d172136f4aebcd40d03db49`.

GREEN usa archive SHA256
`5019cf484fb2a63261edfbc134182f2f81d58dfbeba4e898eca883d8d1e6a02a`:
1.298 archivos sin cambios, 84 imports del repositorio dentro del archive,
cero imports externos y cero intentos de red. Su receipt tiene
SHA256 `c8d7e1ed4286a79086a18354e768cce689f28c47eb221886bb0484837f60146e`.
Estos conteos describen procedencia de código; no cuentan escenarios financieros.

## Corrección y guards complementarios

El owner de persistencia corrigió la derivación en
`99bcd846dedd2d213718254ca04620b223cdf773`, integrado en el GREEN anterior.
El encoding `rc6.shadow-ui-projection-merkle.v1` liga las 17 columnas anteriores
al payload y el payload nativo en cada hoja. Rank se normaliza a REAL y las
banderas a enteros como SQLite. Las hojas incluyen dataset y ordinal, identidad,
strategy/channel/state/cohort/session, event_at, priority/rank/excluded.
La lectura completa compara además con una derivación fresca del reporte
nativo. El reader legacy recomputa `index_record` y compara sus columnas.
La lectura de páginas conserva el alcance declarado
`WIRE_AND_PROJECTION_SEMANTICS`, y la completa declara
`FULL_LOGICAL_SEMANTICS`.

El owner UX agregó en `f33147a072f7fac67994b2bec46babe53a952346` el rechazo
completo de páginas cuyos payloads contradicen los filtros publicados, state
planner o identidad completa. También verifica selected/groups del funnel.
No vuelve a filtrar ni ajusta counts. Es una defensa del consumer que
complementa el binding del índice; no lo reemplaza.

Guards permanentes declarados en los sources de sus respectivos owners:

- `tests/test_rc6_committed_projection.py::test_full_logical_projection_hash_binds_every_query_index_column`
  contiene seis mutaciones SQL declaradas: currency, state, cohort, priority,
  rank y event_at. La inspección de código liga las 17 columnas; no se afirma
  que esas seis variantes ejecuten 17 ataques independientes.
- `tests/test_rc6_dashboard_projection.py::test_projected_payload_that_contradicts_its_filter_is_rejected_without_refiltering`
  rechaza el payload retornado fuera de los filtros sin cambiar el total.
- `tests/test_rc6_dashboard_projection.py::test_projected_funnel_payload_outside_requested_scope_is_rejected`
  conserva el mismo cut y rechaza selected/grupos fuera de currency/channel.

La revisión ejecutó el probe independiente de currency. No usa la presencia
de estos nodos ni los conteos reportados por sus owners como una ejecución
final propia. El SHA final y su run canónico siguen pendientes de freeze.

## Vinculación de requisitos y límites

El hallazgo añade una defensa de derivación a U17 y U21: mantiene la separación
entre safety semántico, vínculo con source y autenticidad externa. Complementa
UX470-I01/I02 al conservar el contrato canónico y su nivel de verificación, y
UX470-I03 al ligar la selección por moneda/cohorte a las filas y sus counts.
No reemplaza ninguno de esos requisitos completos ni altera sus textos
originales. R74 y R78 tratan reconciliación financiera e identidad de moneda de
paquetes históricos con pins: esta prueba no ejercita esos paths y no se les
adjudica cierre por compartir una etiqueta de moneda.

El resultado es `INTERMEDIATE_EXACT_ARCHIVE_DERIVATION_GUARD`, con órdenes
reales cero, rutas reales no llamadas y PPI Watch sin tocar. No verifica el
artefacto promotable, runtime, capacidad factual PPI OPEN, tarifas de cuenta ni
el master externo de veinte sesiones. No demuestra edge económico. Custodia
local y hashes ligados no autentican frente a un actor que controla writer y
todos sus anclajes locales.

## Campos que debe conservar el registro de remediación

Cada ID mantiene original-clause binding, RCA/inputs concretos, SHA/path del
fix y base nodes con sus variantes declaradas. Cada receipt agrega papel de
evidencia, SHA/tree/archive, hash del probe/guard, config/schema/seed/cut,
grafo de imports, hashes de source antes/después, conteos y limitaciones.
Los papeles distinguen `OLD_NATIVE_RED`, `PURE_HELPER_RED`,
`POSITIVE_CONTROL`, `ADVERSARIAL_REJECTION`, `TRUST_LIMIT` y
`INVALIDATED_MIXED_HARNESS`. Un mismo nodo puede contener positivo y ataque;
se declara esa composición sin convertir referencias o parámetros en nuevos
escenarios independientes. Un receipt intermedio no cambia a final por ser
copiado al source bundle.

La corrección del supuesto bootstrap RED sigue etiquetada
`INVALIDATED_MIXED_HARNESS`: había importado BM de otro checkout. El ensayo
íntegro de 19 casos sobre `98153a8f` conserva su alcance intermedio. R78
conserva el pin original rechazado y el rebind consistente como `TRUST_LIMIT`
con fuente/FX/edge externos desconocidos; no se declara arreglada autenticidad
externa mediante un segundo hash local.
