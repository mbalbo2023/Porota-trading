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

## Canary obligatorio — SELECTOR IMPLEMENTADO Y VALIDADO
`ops/ppi_web_canary_selector_rc6.py` NO fija símbolos por anticipado. Consume exclusivamente el manifiesto residual FINAL y elige una muestra determinística.

Reglas:
- misma entrada + mismas opciones => mismo canary;
- por defecto máximo 1 identidad por familia y hasta 8 identidades totales;
- familias representativas primero y cualquier familia extra residual se agrega en orden estable;
- prioridad de casos dentro de cada familia: `PROVIDER_INVALID`, `PARTIAL_VALID`, `NO_PROVIDER_ROWS`, `HARD_PROVIDER_ERROR`;
- nunca selecciona identidades fuera del residual;
- manifiesto vacío, identidad incompleta, clase inválida o duplicado => fail-closed.

El canary real se materializa recién después de `PENDING=0`, cuando exista el residual definitivo. No se promueve automáticamente nada a canónico durante el canary.

## Reconciliación — IMPLEMENTADA Y VALIDADA
`ops/ppi_web_history_reconcile_rc6.py` recibe capturas sanitizadas por identidad y reutiliza sin modificar `cp_history_ingest_policy_hf6.validate_provider_history()`.

Reglas verificadas:
- no interpolación, forward-fill, reparación ni OHLC sintético;
- aliases Web se mapean explícitamente al esquema `date/openingPrice/max/min/price/volume`;
- toda fila pasa el mismo contrato FULL_OHLC utilizado por PPI API;
- fuente persistida: `PPI_WEB_HISTORY`;
- `ready_paper_implication=NONE`;
- `execution_price_implication=NONE`.

Precedencia en `cu_history_store_v2_hf6.py`:
- PPI API / `PPI_PRODUCTION_HISTORY` = rank 10
- `PPI_WEB_HISTORY` = rank 15
- IOL = rank 30

Por lo tanto: PPI API > PPI Web > IOL. Una vela PPI Web no puede reemplazar una canónica PPI API válida; sí puede completar un hueco o reemplazar un fallback IOL de menor autoridad.

## Post-Web hacia IOL — IMPLEMENTADO Y VALIDADO
`ops/ppi_postweb_residual_manifest_rc6.py` consume resultados terminales de reconciliación Web.

- `DONE_VALID` queda resuelto para la ventana solicitada y NO pasa a IOL.
- `DONE_PARTIAL` -> `PPI_WEB_PARTIAL_VALID`.
- `DONE_EMPTY` sin filas -> `PPI_WEB_NO_ROWS`.
- `DONE_EMPTY` con filas rechazadas -> `PPI_WEB_PROVIDER_INVALID`.
- `ERROR` -> `PPI_WEB_ERROR`.
- cualquier estado desconocido/no terminal falla cerrado.

Sólo ese segundo residual queda habilitado como input eventual de IOL. IOL no se ejecuta en paralelo ni antes del cierre Web.

## Contrato de estatus operativo desde chat — IMPLEMENTADO Y VALIDADO
`ops/ppi_progress_status_rc6.py` es read-only y unifica el estatus de las etapas PPI API y PPI Web.

Salida mínima obligatoria para cualquier pedido de "estatus":
- etapa/run_id/status/semáforo;
- porcentaje terminal;
- DONE/ERROR/PENDING;
- familia o identidad actual;
- batch cuando aplica;
- heartbeat y antigüedad;
- filas canónicas;
- disco libre y safety cuando aplica;
- throughput real de la última hora;
- ETA estimada y nivel de confianza.

Regla ETA:
- se calcula sólo con `finished_at` reales de la última hora;
- `HIGH` con >=30 muestras, `MEDIUM` con >=10, `LOW` con 1-9;
- sin muestras recientes => `ETA unavailable`; nunca inventar una fecha/hora;
- al finalizar => ETA=0.

Para la etapa Web, `ResidualState` mantiene SQLite + `status.json` atómico, de modo que el progreso sobrevive a SSH, Actions y chats. Para la ingesta API actualmente activa no se modifica el writer; el estatus se consulta read-only desde las tablas runtime/tasks existentes.

## Validaciones CI
Puntos 8 y 9:
- workflow `.github/workflows/rc6-ppi-web-reconcile-postweb-validate-20260913.yml`
- run `34730231639`
- SUCCESS.

Selector canary + estatus/ETA:
- workflow `.github/workflows/rc6-ppi-canary-status-validate-20260913.yml`
- run `34730420448`
- SUCCESS.
- verifica determinismo, máximo por familia, prioridad residual, ETA fail-closed, conteo terminal y safety estática.

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
- Manifest residual API: IMPLEMENTADO y fail-closed.
- Motor histórico existente: IDENTIFICADO y tests de safety existentes verificados.
- Motor contractual existente: IDENTIFICADO y runtime trusted-device verificado por código.
- systemd durable: PREPARADO, unit NO activada.
- Persistencia/resume: IMPLEMENTADA Y VALIDADA.
- Canary selector: IMPLEMENTADO Y VALIDADO; targets concretos dependen del residual final.
- Reconciliación (punto 8): IMPLEMENTADA Y VALIDADA.
- Residual post-Web -> IOL (punto 9): IMPLEMENTADO Y VALIDADO.
- Estatus/throughput/ETA desde chat: IMPLEMENTADO Y VALIDADO.
- IOL ejecución: BLOQUEADA hasta cierre real de PPI Web.
- 18 timers: mantener pausados.
