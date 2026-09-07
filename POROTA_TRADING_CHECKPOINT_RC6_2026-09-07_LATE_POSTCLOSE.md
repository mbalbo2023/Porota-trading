# POROTA TRADING RC6 — CHECKPOINT LATE POSTCLOSE — 2026-09-07

## 1. Runtime live confirmado

Observer live:
- branch: `fix/rc6-history-partial-semantics-20260907`
- SHA: `c17b0d777ba49d88c53c5a7ed14218d8eaa94638`
- image: `porota-trading-bot:17.0.0-rc6`
- deploy run: `34169831060`
- deploy markers: `OBSERVER_RUNTIME_SAFETY=GREEN`, `OBSERVER_HISTORY_SEMANTICS=GREEN`, `HISTORY_PARTIAL_POSTCLOSE_DEPLOY=GREEN`, `REAL_ORDERS_SENT=0`, `DASHBOARD_UNCHANGED=YES`.

Dashboard permanece independiente y no fue recreado por este deploy.

## 2. Históricos PPI — semántica PARTIAL

La observabilidad dejó de clasificar como hard failure a un payload `PARTIAL` que contiene evidencia OHLCV válida. La evidencia válida continúa entrando por el mismo salvager/versionado; filas inválidas, CLOSE_ONLY y rechazos siguen preservados y no se promueven como OHLCV completo.

Estados de lote:
- `VALID_PAYLOAD` -> completo;
- `PARTIAL` -> AMARILLO, usable evidence, hard_failure=NO;
- `EMPTY_OR_INVALID` / `ERROR` -> hard failure.

No se relajó validación ni canonicalización.

## 3. Cauciones PPI

Discovery oficial read-only ya está en la línea live:
- ticker `PESOS{dias}` / `DOLAR{dias}`;
- Name=cantidad de días;
- plazos probados inicialmente: 1,2,7,30,120;
- MarketData GET-only;
- no se agregó Budget/order/cancel a `ProductionMarketReader`.

Pendientes contractuales formales: cálculo exacto de vencimiento, quantity step, day-count/redondeo, profundidad/paginación del book, semántica completa de Budget y saldo DOLAR. Estos campos pueden intentarse completar inmediatamente por PPI Web autenticada; cualquier campo no explícitamente observable permanece `UNKNOWN` hasta evidencia formal.

## 4. Contract Evidence PPI Web

Decisión canónica: PPI Web autenticada/XHR pasa a ser fuente formal y sistemática de Contract Evidence para TODAS las familias visibles en PPI, no un relevamiento manual puntual.

Cobertura objetivo: Acciones, CEDEARs, Bonos/Títulos Públicos, Letras, ON, Cauciones, Opciones, Futuros, ETF, FCI, FCI Exterior, Acciones USA, Licitaciones, Índices y cualquier familia adicional descubierta.

Política:
- priorizar XHR/JSON estructurado cuando exista;
- DOM/labels/tooltips como evidencia complementaria;
- versionado/hash/provenance por campo/contrato;
- navegación allow-listed/read-only;
- no BUY/SELL/order/budget/cancel/transfer;
- trust-device prompt `Ahora no` puede automatizarse sólo mediante acción/ruta exacta demostrada como no operativa;
- OTP real permanece fail-closed.

Auto-reauth:
- branch `hotfix/rc6-contract-evidence-auto-reauth-20260907`;
- prior E2E run `34165743278` terminó `BLOCKED_AUTH_USERNAME_REQUIRED` con `CE_RUNS_BEFORE=84`, secreto restringido y órdenes reales 0;
- el usuario actualizó localmente la credencial web después de ese intento;
- rerun por push seguro commit `de0d0b04e66ef747d4069e21c1a4a6c55d25e080`;
- run nuevo `34171303368` estaba IN_PROGRESS al publicar este checkpoint.

## 5. NUEVO P1 — PPI Web History SHADOW

Decisión: probar PPI Web/XHR como fuente histórica secundaria PPI antes de depender obligatoriamente de IOL/A3.

Branch desde el SHA live exacto:
- `feature/rc6-ppi-web-history-shadow-20260907`
- base: `c17b0d777ba49d88c53c5a7ed14218d8eaa94638`
- implementación: `rc6_ppi_web_history_shadow.py`
- workflow: `.github/workflows/rc6-ppi-web-history-shadow-20260907.yml`
- latest branch commit al lanzar workflow: `40eebdd1afdfb71aa8f0a79a5a4069f15b641033`
- run `34171351827`.

Seguridad del probe:
- SHADOW;
- `canonical_write=DENY`;
- `db_write=NO`;
- no broker/order imports;
- GET/HEAD/OPTIONS únicamente;
- rutas de order/confirm/cancel/transfer/suscribir/rescatar bloqueadas;
- no persiste cookies, tokens, Authorization, bodies de request ni query strings;
- evidencia sanitizada: host/path, status, schema, hash, family/symbol context;
- observer/dashboard deben mantener IDs/estado y `real_orders_sent=0`.

Validación offline del run `34171351827`: GREEN. El probe live GET-only estaba ejecutándose al publicar este checkpoint.

Condiciones para que PPI Web pueda reemplazar la dependencia histórica obligatoria de IOL/A3:
1. profundidad temporal suficiente;
2. fecha + OHLCV completos donde corresponda;
3. identidad financiera completa;
4. settlement explícito/reconciliable;
5. semántica RAW/ADJUSTED identificada;
6. revisiones/known_at observables o tratables sin look-ahead;
7. paginación/truncamiento demostrados;
8. cobertura reproducible por familias representativas;
9. ingestión versionada con provenance y validadores canónicos intactos.

Hasta completar esas pruebas, IOL/A3 no se eliminan: quedan como fallback/cross-check opcional.

## 6. History Store — blockers obligatorios antes de cualquier secondary canonical write

- completeness-aware winner: `FULL_OHLCV` no puede ser desplazado por `CLOSE_ONLY` por mera prioridad de fuente;
- `price_basis`: RAW y ADJUSTED deben ser series distintas;
- mismatch de settlement/identidad -> fail-closed;
- source priority nunca puede vencer a identidad/completitud.

El reconciliador offline ya tiene invariantes para estas reglas; falta integración/replay sobre la línea live antes de autorizar PPI Web/IOL/A3 como canonical writer.

## 7. Storage P1 — no iniciar todavía Fase A

`POROTA Empirical Evidence Architecture v1` sigue prioridad 1, pero se mantiene la regla: primero cerrar el checkpoint funcional actual (Contract Evidence + Web History proof + History Store hardening), luego Gate 0 sobre el SHA live final y recién después Fase A.

Objetivo Fase A: RAW Evidence Store content-addressed + manifests + único History Ingestion Coordinator, sin borrar `historical_raw_archive`, sin VACUUM y sin migraciones destructivas.

## 8. Pendientes hasta checkpoint funcional final

P1 bloqueantes:
1. Contract Evidence auto-reauth GREEN con sesión real autenticada.
2. Primer E2E DUE auténtico con incremento de `contract_evidence_v2_runs` y timer GREEN.
3. Full Contract Sweep de todas las familias PPI y matriz campo/valor/fuente/timestamp/hash/confianza/estado.
4. PPI Web History SHADOW: inventario real de endpoints/XHR históricos, cobertura y calidad.
5. Integración History Store de `completeness` + `price_basis` con replay/regresión.
6. Decisión de source policy final: PPI API + PPI Web core; IOL/A3 sólo fallback/cross-check si Web proof alcanza.
7. CI/security regression completa.
8. Deploy funcional final sólo de componentes que lo requieran, transaccional.
9. Postflight final: observer/dashboard health, dos DB integrity checks según política SRE, PPI auth, CE timer, history freshness, `real_orders_sent=0`.
10. Consolidación del checkpoint canónico con SHA/run IDs y UNKNOWNs restantes.

Paralelizables/no necesariamente bloqueantes del checkpoint funcional si permanecen SHADOW/offline:
- MFE/MAE integración;
- Forward Lab v2 y campaña walk-forward;
- Event Risk + vista separada/backtests;
- sector map/correlations/family normalization;
- cohorts/version separation;
- PAPER/SHADOW campaign;
- Samsung/Voice Access field test;
- kill-switch requalification;
- logs/source semantics;
- IOL REST 401 y expansión A3, degradables a fallback si PPI Web History prueba cobertura suficiente.

## 9. Invariantes

- PRODUCTION_PAPER;
- real money NO-GO;
- `real_orders_sent=0`;
- no network order tests;
- no secretos/cookies/tokens en evidencia;
- no secondary canonical write hasta cerrar identidad/completitud/price_basis;
- no storage destructive work antes de Gate 0 final.
