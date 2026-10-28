# POROTA TRADING RC6 — CHECKPOINT CP5 GDELT + UX SOURCE WIRING

Fecha: 2026-09-11
Branch: `fix/rc6-w10-sector-map-binding-20260910`
Runtime desplegado y NO mutado: `e47eeffcb94a9468ac7e7f610beee0869353a77e`
Modo: `PRODUCTION_PAPER`; órdenes reales observadas: `0`.
Generic news feed: `OFF` intencionalmente.

## UX global de tablas

Se corrigió en fuente `eq_dashboard_table_layout_rc6.py` la causa concreta de las cabeceras que desaparecían:
- el fallback `data-porota-force-compact=1` ocultaba `tr.porota-table-header` con clipping/posición absoluta;
- ahora las tablas anchas preservan semántica real de tabla;
- overflow horizontal en el contenedor;
- `<thead>` siempre visible;
- `th` sticky;
- no se convierten filas en cards sin contexto;
- `data-wrap=true` queda disponible para columnas textuales largas.

Test agregado: `tests/test_rc6_table_headers_visible_sticky.py`.

## CP5 — GDELT / Event Risk

Se agregó `rc6_risk_gdelt_dashboard.py` y se instala desde el bootstrap de presentación RC6 ya cargado después de Wave8.

Características:
- sólo llama `rc6_gdelt_event_risk_job.latest_status()`;
- NO llama `run_once()` ni `collect_shadow()` desde HTTP;
- NO dispara network/scraping desde el dashboard;
- lee el store local dedicado `event_risk/gdelt_shadow_rc6.db`;
- muestra estado del último run, tiempos, tipos exitosos/solicitados, fetched/stored, total persistido y latest event;
- autoridad fija visible `SHADOW_ONLY`;
- texto explícito `Feed general de noticias: OFF intencional`;
- READ_ERROR/NOT_RUN quedan visibles, nunca reinterpretados como GREEN.

Test agregado: `tests/test_rc6_risk_gdelt_dashboard.py`, incluyendo guard de que renderizar el panel no ejecuta el collector.

## Estado de activación

Estos cambios son SOURCE-ONLY. No se desplegaron al runtime porque GitHub Actions sigue fallando antes del primer step. No se hará deploy ciego.

## GitHub Actions

Runner probe mínimo ya probado en ubuntu-22.04 y ubuntu-24.04: ambos `failure` con `steps=null`. Se reintentará después de este checkpoint. Hasta que un runner ejecute steps reales:
- source preparado: sí;
- tests escritos: sí;
- tests ejecutados: no;
- deploy: no.

`UX_TABLE_HEADERS_SOURCE=READY_FOR_TEST`
`CP5_GDELT_DASHBOARD_SOURCE=READY_FOR_TEST`
`GENERIC_NEWS_FEED=INTENTIONALLY_OFF`
`GDELT_AUTHORITY=SHADOW_ONLY`
`RUNTIME_MUTATED=NO`
`NEXT=RETRY_RUNNER -> IF_GREEN: FOCUSED_CP2_CP5_TESTS || CP3_HOST_PROOF || CP4_A3_PROOF`
