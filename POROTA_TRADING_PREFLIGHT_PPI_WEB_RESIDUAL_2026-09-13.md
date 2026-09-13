# POROTA TRADING — Pre-flight PPI Web residual

Fecha: 2026-09-13
Rama: `ops/rc6-ppi-web-residual-ready-20260913`

## Veredicto actual
NO-GO para corrida larga todavía. La auditoría estricta detectó huecos reales que deben cerrarse antes de declarar 100% READY.

## Confirmado GREEN
- Ingesta API y scraping Web permanecen desacoplados; no iniciar Web con tareas API activas.
- Residual API exacto y fail-closed: `ops/ppi_history_residual_manifest_rc6.py`.
- Estado durable SQLite/status.json: `ops/ppi_web_residual_state_rc6.py`.
- Selector determinístico de canary: `ops/ppi_web_canary_selector_rc6.py`.
- Reporte de progreso/ETA: `ops/ppi_progress_status_rc6.py`.
- Reconciliación reutiliza `cp_history_ingest_policy_hf6.validate_provider_history()`.
- Precedencia: PPI API > PPI Web > IOL.
- Segundo residual post-Web hacia IOL implementado.
- Reauth histórico probado fue recuperado sin reescribirlo: `rc6_ppi_web_reauth.py`.
- Collector contractual probado y normalizador/importer fueron recuperados de la rama checkpoint histórica.
- Motor histórico existente fue recuperado y extendido en el mismo archivo `rc6_ppi_web_history_shadow.py` para aceptar targets JSONL y emitir filas históricas sanitizadas. No se creó un segundo scraper.

## Blockers encontrados
1. El unit `ops/porota-ppi-web-residual-rc6.service` apunta a `/opt/porota-ingest/ppi_web_residual_server_runner_rc6.py`, pero dicho runner aún no existe. Debe implementarse y probarse antes de desplegar.
2. La última prueba autenticada real (Actions run 34729326888) falló: las rutas privadas redirigieron a `cuenta.portfoliopersonal.com/login`; sesión trusted-device expirada. El guard mantuvo cero requests mutantes y cero órdenes. Antes del canary real se debe ejecutar el helper de reauth existente y comprobar acceso privado.
3. El service corre como root mientras el Chrome profile pertenece a `porotaadmin`; el browser debe ejecutarse como `porotaadmin`. Además `ProtectHome=read-only` debe permitir escritura únicamente al profile si la reauth necesita renovar sesión.
4. La capa de estado durable todavía requiere actualización explícita de heartbeat/status de run y cierre `COMPLETED`; hoy el heartbeat del run no se actualiza con cada task.
5. Falta un runner único que cablee: gate API terminal -> manifiesto/hash -> state -> auth/reauth -> scraper target -> reconciliación -> task terminal -> heartbeat -> segundo residual. Debe aplicar timeout/reintentos y nunca autoarrancar IOL.
6. Falta pre-flight real del servidor para verificar paths, Chrome/venv/profile owner, secreto local por existencia solamente, disk, locks, API writer inactive, timers pausados y safety `PRODUCTION_PAPER|0`.
7. El canary live sólo puede cerrarse cuando exista residual final. Debe probar una muestra real, rows históricas, reconciliación y cero mutaciones antes del GO masivo.

## Pendiente obligatorio posterior a validar ingesta + scraping
- Auditar y revalidar todos los timers/jobs que recolectan información durante la jornada de mercado antes de reactivarlos.
- Confirmar especialmente los jobs de Contract Evidence / scraping periódico: `CONTRACT_EVIDENCE_DYNAMIC`, `CONTRACT_EVIDENCE_CAUCIONES`, `CONTRACT_EVIDENCE_AUCTIONS`, `CONTRACT_EVIDENCE_DERIVATIVES` y cualquier equivalente vigente.
- Verificar para cada timer: calendario/horario de mercado, frecuencia, autenticación PPI Web, lock de browser/profile, ausencia de concurrencia con writers históricos, registros efectivamente recolectados (>0 cuando corresponda), clasificación de estados AMARILLO/VERDE/ROJO, reintentos, idempotencia y `PRODUCTION_PAPER|0`.
- No dar por válido un timer sólo porque esté `active (waiting)` o porque el job termine `success`: debe demostrarse que obtiene evidencia útil y la procesa sin conflictos.
- Mantener los 18 producer timers pausados hasta que la ingesta histórica, el scraping Web residual y la validación final estén cerrados. Recién entonces hacer auditoría timer por timer y restauración controlada.

## Regla de GO
No declarar 100% READY hasta que blockers 1-6 estén resueltos y validados offline/server-preflight. No declarar GO operativo para corrida larga hasta que, además, API haya cerrado (`PENDING+RETRYABLE+RUNNING=0`) y el canary live del blocker 7 sea GREEN.

## Seguridad inmutable
- No dos historical writers simultáneos.
- No órdenes reales; `PRODUCTION_PAPER|0`.
- PPI Web read-only después de autenticación; POST sólo puede existir dentro del helper histórico de login y únicamente al endpoint exacto autorizado.
- No guardar credenciales, OTP, cookies ni tokens.
- No síntesis/reparación/interpolación OHLC.
- IOL sólo recibe el residual post-Web.
- 18 producer timers permanecen pausados hasta cierre final.
