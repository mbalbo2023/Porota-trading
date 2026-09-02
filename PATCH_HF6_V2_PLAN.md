# POROTA HF6 — PATCH CONTRACT EVIDENCE V2 / INGESTA / UX / STORAGE

> Documento WIP. No constituye autorización de deploy.

## Invariantes

- Runtime objetivo: `PRODUCTION_PAPER` / `SIMULATED`.
- PPI órdenes reales: bloqueadas.
- `real_orders_sent=0` es invariante.
- IA intradía: OFF; el motor durante rueda es Python determinístico.
- El tag `v17.0.0-rc3-hf6` es inmutable.
- Ninguna familia pasa a READY_PAPER por inferencia.

## Alcance autorizado para preparación

1. Contract Evidence v2, versionado, con TTL, hash y estados VERIFIED/MISSING/STALE/CONFLICT/READY_PAPER_CANDIDATE/READY_PAPER/FAIL_CLOSED.
2. Ingesta structured-first desde endpoints PPI oficiales descubiertos; Chrome/Playwright sólo como auditoría/enriquecimiento, nunca en el hot path.
3. Reparación de históricos hoy estancados en 65/243: backlog explícito, retry con backoff, prioridad por instrumentos operables, Data912 sólo como respaldo histórico y jamás precio ejecutable.
4. Sesiones de mercado por familia/mercado, sin `MARKET_OPEN_HOUR` global.
5. P1-6..P1-9 de la auditoría: adaptadores para Opciones, Futuros, Cauciones, FCI local, Letras, ON y ETF exterior; HOLD hasta contrato completo.
6. Fuente oficial A3/Matba-Rofex para referencia/históricos de futuros/opciones cuando corresponda y esté accesible; PPI conserva market data live del broker.
7. UX/UI: reorganizar Trading por familias/estrategias; eliminar duplicaciones; exponer readiness contractual; Scalping deja de ser excepción arquitectónica.
8. Sistema/Logs: últimas 50 líneas/eventos, origen, timestamp y descarga; reutilizar fuentes existentes.
9. Preopen HF2 obsoleto: retirar como gate operativo y reemplazar por validación pre-deploy/versionada sin falsos NO_GO.
10. Settlement: no alterar contabilidad conciliada; mejorar UX con `available_at` y estado.
11. Storage lifecycle: inventario → clasificación → autorización → limpieza → medición. Nunca `docker system prune` ciego.
12. Modelo matemático: replay paralelo actual vs candidato ATR/depth/cost; no promover barreras nuevas por muestra corta.
13. Aprendizaje: observacional, con MFE/MAE, duración, costos, profundidad, spread y causa de cierre; no auto-muta parámetros.

## Cadencias objetivo

- Market data live: ciclo PPI existente.
- Catálogo/operabilidad PPI structured: 15 min en rueda.
- Cauciones/licitaciones activas: 5 min.
- Contrato estático: preapertura + postcierre + hash diario.
- Series futuros/opciones: 15 min durante sus respectivas sesiones.
- Históricos: cola continua de baja prioridad + barrido pesado postcierre/noche/preapertura.
- Playwright completo: semanal o ante cambio/conflicto.

## Política histórica

El estado 65/243 no se considera suficiente. El nuevo worker debe clasificar cada identidad en `COMPLETE`, `SHORT`, `FAILED_RETRYABLE`, `FAILED_CONTRACT`, `PENDING` o `STALE`, registrar `next_retry_at`, `attempts`, `last_error_class` y priorizar:

1. posiciones abiertas;
2. universo READY_PAPER;
3. READY_PAPER_CANDIDATE;
4. resto del catálogo.

No se rellenan huecos sintéticamente. La señal que requiera velas no consume una serie incompleta como si fuera completa.

## Storage

- SQLite mantiene datos calientes e índices.
- Raw repetitivo y snapshots de auditoría envejecidos se comprimen fuera del hot path.
- Contract Evidence normalizado se conserva; raw browser tiene retención limitada y sólo se preserva si aporta evidencia única.
- Logs rotan y comprimen.
- Imágenes Docker: conservar activa + rollback validado; retirar versiones viejas sólo después de deploy verde y autorización.
- Build cache: podar sólo cache no referenciado luego de deploy verde.
- Artefactos temporales/deploy: TTL y limpieza post-validación.
- Medir GB antes/después y registrar recuperado.

## Deploy

No se cerrará ni desplegará el patch sin presentación previa de cambios, tests, familias READY/HOLD, imagen candidata, rollback y limpieza propuesta, seguida de autorización explícita del usuario.
