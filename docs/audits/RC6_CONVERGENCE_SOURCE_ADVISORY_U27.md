# U27 — comparación complementaria sin autoridad de promoción

Estado: COMMITTEADO localmente cuando se integra este archivo. PAPER/SHADOW;
sin despliegue, tráfico proveedor ni órdenes reales.

## RCA y contrato reemplazado

`consolidate()` y `reconcile()` publicaban `shadow_promotion=True` ante evidencia
IOL/BYMA consistente aunque `selection_eligible=False` y `entry_authority=False`.
El nombre podía interpretarse como elegibilidad de promoción que el productor
no había verificado. La búsqueda de consumidores ejecutables encontró sólo los
dos productores y sus asserts; no existía un consumidor runtime de ese flag.
UX confirmó que su proyección tampoco lo consume.

Ambos productores emiten ahora `advisory_comparison_ready`. Es evidencia de
comparación, con `decision_effect=OBSERVE_ONLY`; no habilita promoción,
selección, entrada, rutas live ni dinero real. Se elimina el nombre anterior,
sin alias ambiguo. El schema consolidado pasa a
`rc6-consolidated-source-evidence-v2`; reconciliación pasa a `schema_version=3`.
Los estados comparativos históricos `READY_SHADOW*` y las métricas agregadas de
comparación mantienen su significado de evidencia advisory; no se convierten
en autoridad. Los artefactos de auditoría históricos no se reescriben.

`selection_eligible` sigue separado: exige origen PPI, identidad exacta y
evidencia válida/fresca según cada productor. `entry_authority`,
`live_decision_authority` y `real_money_authorized` permanecen `False` aun cuando
esa selección es elegible. IOL/BYMA-only conserva valores/procedencia útiles
para comparación y nunca gana autoridad PPI.

La identidad completa es `(ticker, family, market, currency, settlement)`.
Una diferencia en cualquiera de sus cinco componentes impide prestar campos
del complemento a la identidad primaria. No se inventa moneda ni se sobrescribe
identidad PPI.

## Evidencia permanente

| Cláusula | Test node / escenario independiente |
|---|---|
| IOL/BYMA-only advisory sin autoridad | `tests/test_rc6_source_consolidation.py::test_complement_only_ready_means_advisory_and_never_authority` y `tests/test_rc6_ppi_iol_reconciliation_rc6.py::test_complement_only_ready_is_advisory_and_never_primary_eligibility` |
| Rechazo exacto por cada dimensión de scope | `tests/test_rc6_source_consolidation.py::test_advisory_complement_never_borrows_any_part_of_primary_scope` y `tests/test_rc6_ppi_iol_reconciliation_rc6.py::test_advisory_complement_requires_same_five_part_primary_identity` |
| Conflicto no parece READY | `tests/test_rc6_source_consolidation.py::test_advisory_conflicts_require_review_and_cannot_look_ready` |
| Selección PPI separada de entrada/live | `tests/test_rc6_ppi_iol_reconciliation_rc6.py::test_advisory_ready_keeps_separate_primary_selection_and_zero_entry_authority` |

Son **6 funciones nuevas de escenario**, no 16 escenarios por sumar sus
parametrizaciones. La regresión de scope prueba las cinco dimensiones dentro
de cada familia de escenario.

Validación offline: **84 tests pasaron**, 0 fallos/errores/skips, 1.589 s.
Suites: `test_rc6_source_consolidation.py`,
`test_rc6_ppi_iol_reconciliation_rc6.py`, `test_rc6_final_family_source_policy.py`,
`test_rc6_byma_morning_pipeline.py`, `test_rc6_cauciones_shadow_evidence.py`.
Receipt local: `/tmp/rc6-u27-evidence.xml`, SHA-256
`eb6bfff3f0756bb8d59fe4406467d15550d506e8ad4c32624c467b5f84048b59`.

Esta validación acredita semántica y guardas de código. Disponibilidad actual
PPI/IOL/BYMA, deployment y comportamiento productivo son NO_VERIFICADO por esta
prueba offline.

La integración final vuelve a ejecutar esas cinco suites junto al presupuesto
y callers nativos: **334/334 GREEN**, cero fallos/errores/skips. El
[registro exacto](convergence/RC6_BUDGET_F01_CONVERGENCE.json) vincula las seis
funciones U27 a sus nodos ejecutados, sin convertir sus parametrizaciones en
escenarios nuevos. [JUnit versionado](convergence/evidence/rc6-budget-source-final.xml),
SHA-256 `1d373c796d933109ba3c67014230bcda0ae91b8f6eade7bd31f1b2b45f28b9a7`.
La severidad heredada de #469 es **P3**, sin bypass de entrada observado.
