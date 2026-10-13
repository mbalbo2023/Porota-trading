# Revisión independiente de los ocho controles core originales

Este complemento reproduce entradas originales de #468/#469 contra **c27dfd963c4fe83465c0f2105347e974fbbe6356**, árbol **bf3cf193434641aa89e4c746b26c77aec5d1d2b2**, mediante un único `git archive` íntegro. Los 1109 archivos quedaron byte a byte iguales antes y después; los 69 imports del repositorio pertenecen al mismo archive, sin imports ajenos ni intentos de red. Todos los SQLite son efímeros y los fills son PAPER sintéticos. No se cambia código productivo ni los conteos originales 55/80/90/6.

El receipt es `original_core_c27dfd9_replay_receipt.json` (SHA-256 `617090f4a4ac927fd8171eabab26253d20a61b61b94aa3a93a8324a6ef265313`). El driver `tests/probes/rc6_original_core_archive_probe.py` tiene SHA-256 `ec4cc015a2142423305968179993ce1629a14396c9b064eac9e2484fc3c9cf1d`. No representa el artefacto canónico CI, un despliegue ni autenticación externa.

## Procedencia y adaptación permitida

Los tres drivers originales se preservan completos y sin cambios en `tests/probes/fixtures/original_core_audits/`. El wrapper selecciona por AST las definiciones originales de #468 y la función de rollback #469; no ejecuta sus antiguos bloques de enrutamiento a una ruta local absoluta. Cada fragmento original está ligado por SHA en el receipt. El driver económico U06 se ejecuta completo y sin cambios. El Harness auxiliar U03 también se importa del archive c27 íntegro, sin overlays. La reconstrucción U08 declara su alcance distinto y usa `_open`, `_open_future`, `PositionExitSupervisor.tick()` y `PaperBroker.supervise_futures()` antiguos, con controles positivos de salida de identidad exacta.

Un primer ensayo local diagnosticó el alias `__mp_main__` del propio runner como import ajeno. La comprobación se estrechó a permitir únicamente el mismo path/bytes del entry point; el receipt adjunto corresponde al nuevo ensayo válido. No se atribuye aquel rechazo del harness a un error del producto.

## Resultado por requisito

| ID | Clase de evidencia | Resultado y límite |
|---|---|---|
| AUD-468-01 | CONTROL_PRESERVED | Fresh synthetic book with last intraday source 20 minutes old -> HOLD STALE_INTRADAY_SOURCE, preserving the prior #466 guard. No new fix or new PRODUCT_RED claimed; original old-production failure remains documentary evidence from #468. |
| AUD-468-02 | OLD_NATIVE_RED | 15 distinct events spanning 28 seconds and 42 minutes both become BUY_CANDIDATE; the regular 14-minute span is the positive control. Original fixture explicitly initializes its native contract table to confirmed synthetic interval volume. It is a real evaluate_candidate path, not full worker/provider confirmation or factual market cadence. |
| AUD-468-03 | OLD_NATIVE_RED_SYNTHETIC_VOLUME_INFERENCE | Native persist_payload confirms synthetic cumulative-with-reset [10,20,30,40,50,5,6] as interval after stable overlap; monotone interval [10..16] remains PENDING. The unsupported inference is replayed; synthetic ground truth does not authenticate PPI field units. Current UNKNOWN/fail-closed in the absence of a dated unit contract is expected behavior, not a software defect. |
| AUD-468-04 | BASELINE_LIMITATION_EXTERNAL_AUTHORITY | Original #468 baseline scheduling bounds and unverified dynamic capacity are retained as documentary evidence. No new runtime RED or actual approved capacity claimed. Current factual/hot/discovery guards have independent owners and their final execution remains pending. |
| AUD-468-05 | NATIVE_EVENT_COUNT_EQUIVALENCE_AND_CONTRACT_DEFINITION_GAP | Original native main returns BUY, 20 samples and score 0.693023029813731921231795080 for both 90 and 1 minute spans. entry_signal_inputs has an exact vector and signal_window_minutes=90 but no explicit event-count/actual-span contract. Equal event-count outputs are not inherently a mathematical bug. Closure requires declaring the hypothesis and factual span, not forcing every strategy to wait 90 minutes or claiming empirical edge. |
| U03 | OLD_NATIVE_RED | Actual worker probe start 14:01 UTC is retrodated to completion/recovery 13:47:40 UTC. Fifteen points follow the wrong epoch but only one follows the actual start; BUY_CANDIDATE and one real synthetic PAPER fill occur. Original rollback function is unmodified. The tests.test_issue465_capability_cache Harness and every production import come from the same c27 archive; only its documented read-only provider boundary is substituted. |
| U06 | OLD_NATIVE_RED | Unchanged original driver: canonical net R/R 0.6703480482692131723470556809 <1.20 and passed=False; promote_paper_candidate opens 100 units and persists passed=True. Native 30-minute max-hold closes with net -97.0900 ARS. Only original broker-constructor boundary returns a real BINDING PaperBroker; no financial/math/ledger implementation is mocked. PAPER modeling does not prove current account tariffs or economic edge. |
| U08 | OLD_NATIVE_RED_RECONSTRUCTED_CALLER_WITH_POSITIVE_CONTROLS | Old native _open creates spot stop98.019600; exact bid90 is masked by newer USD/NASDAQ bid200 and supervisor.tick() returns WATCH_IDENTITY_MISMATCH/OPEN. Native future _open_future creates stop1490; exact bid1400 is masked and supervise_futures leaves ACTIVE. Explicit same-identity native controls close both. Original handoff has JSON receipt, not a saved executable driver for this case. This is labeled a reconstruction using only the old native APIs; ambiguous catalog collector, TP/EOD, tombstone and restart subcases are not replayed here. |

Los nombres de nodos nativos y paths de fixes están en `consolidated_finance_evidence.json::independent_original_core_review.requirements`. Este replay no ejecuta los guards actuales ni los declara verdes sobre el SHA final: esa validación queda pendiente del freeze del integrador. Los once subcasos de driver no son once escenarios económicos independientes ni nuevos requisitos originales.

## Reproducción

Crear el archive completo de c27 y un índice JSON con `source_sha`, `source_tree`, `archive_sha256`, `extracted_root` y `source_file_hashes` de todos los archivos extraídos. Ejecutar desde el entorno Python 3.11 cerrado:

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 /workspace/venv_rc6/bin/python tests/probes/rc6_original_core_archive_probe.py --source-index /tmp/rc6_finance_core_original_c27dfd9.index.json --output /tmp/core-replay.json
```

El archive ejecutado tiene SHA-256 `11681d9682ff41ff595e10550d0e3fb4d861161e2fda9237930e6fd32dcc61d9`; la ruta temporal sólo es materialización del código fijado. Python 3.11.16 / SQLite 3.53.1. Las fechas/timestamps de inputs originales se preservan. La fecha real del receipt no se utiliza como dato de mercado.

## Autocorrección de alias financiero

Se corrige únicamente el alias derivado de U09: `ISSUE469:RA-C-03` (tick DLR). `RA-C-02` del informe original #469 corresponde a U07 (reserva EXIT futuros), no a U09. Los IDs U09/U07, sus observaciones, receipts, clauses originales y guards se preservan. El registro original #469 no se modifica.

El maestro real de veinte sesiones permanece **EXTERNAL_NO_VERIFICADO**; la cohorte de auditoría #468/#469 y las fixtures sintéticas permanecen separadas. Semántica/unit del volumen PPI, comisiones comerciales actuales, clearing, margen, IVA/FX/calendario, disponibilidad de series y clocks del proveedor siguen sujetos a sus gates externos. **EDGE_NO_DEMOSTRADO**.
