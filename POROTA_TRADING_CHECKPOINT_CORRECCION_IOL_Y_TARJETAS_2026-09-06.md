# POROTA TRADING — CORRECCIÓN DE CHECKPOINT: IOL Y TARJETAS — 2026-09-06

Este archivo se lee **después** de `POROTA_TRADING_CHECKPOINT_ADDENDUM_2026-09-06_NOCHE_IOL_DASHBOARD_INGESTA.md` y prevalece sobre cualquier frase incompatible de ese addendum.

## 1. Corrección IOL

La frase previa que decía que no existía integración IOL en el repositorio es incorrecta después de inspeccionar el SHA live exacto `db26c76723bb988c956589c572b87cbcb4191731`.

Estado real:
- existe `ak_iol_client.py`, desactivado por default con `IOL_ENABLED=false` y credenciales por entorno;
- existe `al_historical_ingest.py` con `backfill_desde_iol()` y store legacy `market_historical_ohlcv`;
- existe `j_main.py`, runtime legacy que **NO** se usa en el split `PRODUCTION_PAPER` RC6 y no debe activarse;
- IOL no está cableado al observer RC6, al scheduler histórico RC6 ni al History Store v2;
- el cliente IOL legacy incluye un `POST` de estimación (`estimar_operacion`), por lo que no cumple el objetivo nuevo de cliente history estrictamente GET-only;
- el ETL legacy usa UPSERT por `(symbol,date)` y puede reemplazar source/provenance; no debe ser el store canónico nuevo.

Pendiente P1 correcto: construir `IOL_HISTORY_READONLY` aislado, GET-only, proof pequeño PPI↔IOL sin persistencia canonical, y recién después integrar a History Store v2 con provenance/versionado.

## 2. Telegram dashboard — causa encontrada

El antiguo semáforo Telegram del dashboard se apoyaba en `/app/data/telegram_health.json`, cuyo mtime observado era 2026-08-22; esa evidencia era obsoleta para el preopen.

Sin embargo el runtime actual sí tiene evidencia vigente:
- `paper_notification_worker` RUNNING con heartbeat actual durante la auditoría;
- `real_orders_sent=0`;
- outbox con 83 mensajes `SENT` y sin estado FAILED observado en el corte;
- jobs Telegram persistidos con éxitos posteriores al archivo legacy.

Pendiente inmediato: el dashboard debe usar worker/outbox/jobs como source-of-truth y no el JSON de agosto.

## 3. SRE dashboard — causa del AMARILLO encontrada

El estado AMARILLO es real y no debe repintarse GREEN.

`bi_operational_services.collect_sre()` exige simultáneamente:
- `quick_check=ok`;
- disco libre >=20%;
- `db_query_ms < 250`.

En la auditoría:
- quick_check=ok;
- disco libre ~43,4%;
- `db_query_ms` de la medición SRE estaba en decenas de segundos.

Por eso el AMARILLO es causado por **latencia de la propia medición SRE**, no por corrupción SQLite ni por falta de disco. El detalle persistido anterior sólo mostraba quick_check/disco y ocultaba el tercer criterio.

Pendiente inmediato: dashboard debe mostrar `query_ms` y clasificar ese AMARILLO como warning operacional no bloqueante por sí solo, manteniendo blocking si integridad o disco fallan.

## 4. Dashboard overlay siguiente

Se está preparando una revisión exclusivamente de observabilidad para mañana:
- Telegram desde evidencia runtime vigente;
- SRE con causa explícita;
- ningún cambio del observer;
- ninguna modificación de estrategia/gates;
- real money BLOCKED.

El dashboard actualmente activo antes de esa revisión permanece `porota-trading-dashboard:17.0.0-rc6-go-live-ux2` / SHA `318043805a9736eda77874ba72ee7abc93fcb0f1`, mientras el observer continúa en `db26c...`.
