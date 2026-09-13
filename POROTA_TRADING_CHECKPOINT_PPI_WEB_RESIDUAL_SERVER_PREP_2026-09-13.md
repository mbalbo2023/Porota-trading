# POROTA TRADING — PPI Web residual server preparation checkpoint

Fecha: 2026-09-13
Rama: `ops/rc6-ppi-web-residual-scraper-20260912`
Estado: PREPARACION SOLAMENTE. NO activar scraping masivo hasta cierre formal de PPI API.

## Decisión operativa
La corrida larga PPI Web se ejecutará directamente en el Droplet bajo systemd. GitHub Actions queda únicamente como plano de despliegue, validación offline y probes read-only acotados. El chat no será fuente de verdad del progreso.

## Componentes existentes que SE REUTILIZAN
- `rc6_ppi_web_history_shadow.py` de `feature/rc6-ppi-web-history-shadow-20260907`: motor Playwright histórico autenticado, trusted-device, GET/HEAD/OPTIONS, canonical_write=DENY, db_write=NO.
- `rc6_trusted_browser_contract_collector.py` y runtime RC6 ya desplegado: extracción contractual read-only, perfil Chrome confiable y reauth aislada.
- perfil: `/home/porotaadmin/porota-browser-lab/chrome-profile`.
- venv: `/opt/porota-contract-evidence-venv`.
- usuario browser sin privilegios: `porotaadmin`.

No se crea un segundo motor de scraping. El código nuevo se limita a orquestación, estado durable, selección residual y reconciliación.

## Gate de transición API -> Web
Debe cumplirse TODO antes de iniciar Web:
1. `PENDING + RETRYABLE + RUNNING = 0` en `ppi_history_ingest_tasks` para `PPI-HIST-20260912-001`.
2. universo = 1960 identidades sin colisiones.
3. SQLite quick_check=ok.
4. duplicados canónicos FULL_OHLC=0.
5. seguridad=`PRODUCTION_PAPER|0`.
6. writer PPI API detenido legítimamente/terminado; nunca dos writers históricos simultáneos.
7. manifiesto residual generado y SHA256 congelado.
8. 18 producer timers siguen pausados.

## Residual exacto
`ops/ppi_history_residual_manifest_rc6.py` clasifica:
- `NO_PROVIDER_ROWS`
- `PROVIDER_INVALID`
- `PARTIAL_VALID`
- `HARD_PROVIDER_ERROR`

El generador falla cerrado si la API aún tiene tareas activas. La salida será JSONL inmutable, con hash SHA256 y conteos por familia/clase.

## Extensión mínima al scraper histórico existente
No cambiar motor/browser/safety. Sólo agregar entrada explícita de targets residuales (`--targets-jsonl`) y modo canary (`--limit`/selección por familia), reutilizando el mismo loop Playwright y el mismo guard de red. La salida de observación sigue separada del History Store.

## Arquitectura durable del servidor
Servicio planificado: `porota-ppi-web-residual-rc6.service`.
- `Type=simple`
- propietario de ejecución: servidor, no GitHub Actions
- lock exclusivo `/run/lock/porota-ppi-web-residual-rc6.lock`
- `Restart=on-failure`
- backoff controlado
- estado durable bajo `/opt/porota-ingest/ppi-web-residual/`
- SQLite de control por identidad + `status.json` atómico
- journal systemd
- heartbeat
- timeout por identidad
- resume idempotente

Estados mínimos: `PENDING`, `RUNNING`, `DONE_VALID`, `DONE_PARTIAL`, `DONE_EMPTY`, `ERROR`.
Un RUNNING huérfano se reencola al recuperar el lock después de reinicio.

## Canary obligatorio
Antes de la corrida residual completa se seleccionará un pequeño conjunto real del manifiesto, representativo de las familias presentes (prioridad: CEDEAR/acción si residual, renta fija/ON, opción, futuro y cualquier familia especial residual). El canary debe probar:
- sesión autenticada trusted-device;
- navegación real;
- payload histórico detectado;
- normalización compatible con contrato FULL_OHLC;
- cero requests mutantes posteriores a autenticación;
- cero órdenes reales;
- persistencia de progreso y resume.

No se promueve automáticamente nada a canónico durante el canary.

## Reconciliación
Orden de autoridad: PPI API > PPI Web > IOL.
PPI Web sólo puede completar faltantes o evidencia residual. No pisa una fila FULL_OHLC válida proveniente de PPI API. No repara/sintetiza OHLC. Toda fila conserva source, timestamp de captura y evidencia de origen.

## Post-Web
Al terminar Web se genera un segundo residual. Sólo éste queda habilitado como input eventual de IOL. IOL no se ejecuta en paralelo ni antes del cierre Web.

## Cierre final
Antes de restaurar timers / READY_PAPER:
- quick_check=ok
- duplicados=0
- cobertura y frescura por familia
- contratos obligatorios por familia
- residual final cuantificado
- source provenance verificable
- seguridad PRODUCTION_PAPER|0
- checkpoint final versionado

## Continuidad independiente del chat
Fuente de verdad: SQLite de estado + systemd journal + `status.json` + checkpoint versionado. Un chat nuevo debe poder recuperar la corrida usando sólo run_id/checkpoint/status, sin reconstruir conversaciones previas.

## Estado de preparación
- Gate API->Web: VERIFICADO / esperando PENDING=0.
- Manifest residual: IMPLEMENTADO y fail-closed.
- Motor histórico existente: IDENTIFICADO y tests de safety existentes verificados.
- Motor contractual existente: IDENTIFICADO y runtime trusted-device verificado por código.
- systemd durable: DISEÑO CERRADO, unit aún no activada.
- Persistencia/resume: por implementar/validar en capa de orquestación, no en motor scraper.
- Canary: criterio cerrado; targets concretos dependen del residual final.
- Reconciliación: política cerrada; integración final pendiente.
- IOL: bloqueado hasta residual post-Web.
- 18 timers: mantener pausados.
