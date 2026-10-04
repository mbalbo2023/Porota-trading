# Issue #465 — F-03: generación lógica SHADOW durable

WORKSTREAM_ID: WS-FIX-AUDIT-08-C-ATOMIC-EVIDENCE-20261004.
Branch aislada: `fix/issue465-atomic-evidence-20261004`.
Base de desarrollo: `caf9bc94b4a1f436ad01a84a9e9e9e7a4a9e9423`, candidato #463 rechazado para promoción.
Base productiva de la misión: `da697c6e6c2274579f9e4a112fabc4327475dd35`.
Ownership: [declaración exclusiva del Issue #465](https://github.com/mbalbo2023/Porota-trading/issues/465#issuecomment-5981112240).
Este frente entrega un head para una sola integración posterior. No publica PR propio ni ejecuta Predeploy, merge, deploy o mutación productiva.

## ERROR → RCA → FIX → GUARD → TEST

El test `test_checkpoint_compression_disk_failure_cannot_publish_new_report_with_old_checkpoint` reprodujo el defecto sobre el worker real de #463. Después de un ciclo correcto a `2026-10-05T13:20:00+00:00`, se inyectó ENOSPC al comprimir el checkpoint del ciclo siguiente. El report ya publicado tenía `13:20:30`; checkpoint y status seguían en `13:20:00`. Los tres JSON podían conservar hashes individuales válidos. El RED original se conserva sin cambiar su assertion causal.

Evidencia RED: `/tmp/issue465-f03-red.xml`, 1 test / 1 FAIL, SHA256 `30023a7a0c44d2a02fdfd421ef383a9cac8cfc6e02bbba6b14b26e56728121d7`. El paquete consolidado debe preservar este JUnit junto al GREEN final. El resultado del frente no sustituye la suite completa ni el Predeploy V2 exacto del candidato integrado.

La RCA es la publicación sucesiva de tres archivos individualmente atómicos. No existía una autoridad que confirmara el conjunto. La recuperación limpia no ejercitaba los estados intermedios de escritura, fsync o muerte del proceso.

El worker publica ahora una generación completa mediante `EvidenceFiles.commit_generation`. `CURRENT.json` es su única autoridad de publicación. Los nombres lógicos `latest.json.gz`, `checkpoint.json.gz` y `status.json` pasan por el lector del conjunto y sus escrituras independientes se rechazan. El consumidor real `RuntimeCapacityController.shadow_report` exige la generación completa aunque sólo utilice el report.

## Protocolo y lectura

1. Se asigna un UUID4 independiente del reloj y una secuencia relativa al CURRENT anterior. Una colisión falla cerrada; nunca reemplaza una generación existente. El worker sigue rechazando un checkpoint del futuro después de un retroceso del reloj.
2. Report/checkpoint/status incluyen generation_id, source_watermark y configuration_fingerprint. Report y checkpoint enlazan los hashes de sus contenidos; status enlaza los digests finales de ambos. El manifest enlaza los SHA256 de cada miembro codificado y el digest de cada payload.
3. Todos los miembros se codifican antes de publicar. El guard de retención, bajo el mismo writer flock, reserva el pico recursivo de archivos/bytes, el directorio y un margen acotado para telemetría. La alerta se incorpora al mismo corte antes de recalcular los hashes.
4. Se escriben los cuatro miembros a `.generation-<UUID>.tmp`, con fsync de cada archivo y del directorio. Se renombra a `gen-<UUID>` y se sincroniza el directorio raíz.
5. El pointer temporal `.CURRENT.<UUID>.tmp` se escribe y sincroniza. Su rename a CURRENT y el fsync de raíz son el commit final. Una excepción posterior al rename puede dejar visible el corte nuevo completo; no se afirma que el fallo haya vuelto al corte viejo.
6. El lector abre directorios mediante descriptors y O_NOFOLLOW, toma un shared flock no bloqueante y verifica CURRENT, manifest, todos los miembros, generation_id, watermark, fingerprint, hashes cruzados, as_of y referencias de status. Se rechazan aliases, gzip truncado, expansión superior al límite, nombres ajenos y JSON con claves duplicadas.

Un CURRENT válido anterior se interpreta exclusivamente como ese corte anterior: conserva su as_of y sus tres miembros. Nunca toma miembros más recientes de otro directorio. Los callers conservan el guard de freshness del evento/as_of; mtime no determina autoridad. Un CURRENT inexistente o inválido falla cerrado.

Los lectores no crean archivos y no esperan a una serialización pesada. Un writer activo produce backpressure inmediato al consumidor; no puede mantener el supervisor factual esperando por el lock SHADOW. El child SHADOW y el supervisor de salidas siguen siendo procesos separados del runtime canónico.

## Health, F-04 y F-05

Un fallo del loop produce `failure.json` separado, con el generation_id y secuencia que observó, un reason code acotado y el estado FAIL_CLOSED. No reemplaza el status committed. Un lector factual falla cerrado mientras el fallo enlaza la generación actual; un commit posterior vuelve a habilitar la lectura. El writer puede leer el último corte válido para recuperarse mediante fix-forward. Si tampoco puede persistir el diagnóstico, conserva el corte anterior y registra explícitamente la indisponibilidad de status en el log.

Los reason codes de cuota incluyen `FUNNEL_CHECKPOINT_CAPACITY_EXCEEDED` y `SHADOW_RADAR_CAPACITY_REACHED`. ENOSPC, permisos y errores de IO tienen códigos seguros; mensajes arbitrarios de excepción o proveedor no se persisten. Los diccionarios de counters/last_trade del radar también tienen guards al insertar, incluso si sus points antiguos expiraron.

El manifest conserva `source_audit_digest` y `source_reports_digest`; el link source_audit debe corresponder exactamente a los reportes reales. Los inputs nativos recibidos antes del watermark inicial siguen disponibles para auditoría sin reintegrarlos al planner. El caso sin freeze preopen también produce la auditoría de las fuentes recibidas. Se reutilizan los helpers del frente D, sin un segundo modelo de fuentes ni reemplazar reloj nativo por captura.

La policy y el runbook de retención pertenecen al frente D. El writer pasa su descriptor verificado a `EvidenceRetention.prepare`. CURRENT y su generación anterior quedan pinned; los freezes preopen conservan su provenance durable. Sólo se limpian temporales propios de nombres estructuralmente conocidos. Una generación final, incluso huérfana antes del pointer, no se borra sin ACK de archivo verificado y sin resolver pins.

El soft threshold produce RETENTION_PRESSURE en report/status, conserva el estado observacional en `observation_status` y permite persistir evidencia completa. El selector de capacidad aprobado cae al fallback conservador ante esa presión. El hard threshold o falta de espacio impide publicar el corte nuevo, preserva el anterior y degrada exclusivamente SHADOW. No se elevan los límites por conveniencia: payload 64 MiB, evidencia 128 MiB y 512 entradas recursivas por defecto. El margen de staging se cuenta antes de escribir.

## Mapa programable de fault injection

Todos los nodos siguientes pertenecen a `tests/test_issue465_generations.py`.

| Requisito #465 F-03 | Regression / ataque | Comportamiento exigido |
| --- | --- | --- |
| Después de report | `test_every_commit_failure_boundary_exposes_only_one_valid_cut[after_report]` | Cut anterior íntegro |
| Después de checkpoint | Mismo nodo `[after_checkpoint]` | Cut anterior íntegro |
| Después de status | Mismo nodo `[after_status]` | Cut anterior íntegro |
| Antes de fsync | Mismo nodo `[before_fsync]` | Cut anterior íntegro |
| Después de fsync | Mismo nodo `[after_fsync]` | Cut anterior íntegro |
| Antes del pointer | Mismo nodo `[before_commit_pointer]` | Cut anterior íntegro; final huérfano no publicado |
| Después del pointer | Mismo nodo `[after_commit_pointer]` | Cut nuevo íntegro, sin mezclar |
| Process kill | `test_real_sigkill_at_every_boundary_never_exposes_staging_as_current`, parametrizado en las siete fronteras | SIGKILL real, lock liberado, reinicio forward seguro |
| Reinicio del caller real | `test_canonical_worker_restart_after_kill_reuses_only_committed_checkpoint` | Worker real reutiliza sólo checkpoint committed |
| Disk full | `test_enospc_from_real_fsync_does_not_change_current` y RED causal original | ENOSPC en fsync/codificación; pointer anterior |
| Permission denied | `test_actual_read_only_directory_permission_failure_preserves_current` | EACCES real en directorio privado de test |
| Truncated gzip | `test_truncated_gzip_rejected_even_with_individual_wire_hash_rebound` | Rechazo aunque adversario recalcula hash individual |
| Corrupt hash | `test_corrupt_file_and_manifest_hash_each_fail_closed` | Hash de member o manifest falla cerrado |
| Stale CURRENT | `test_stale_current_returns_complete_prior_cut_without_borrowing_newer_members` | Se devuelve exclusivamente el corte previo con su as_of |
| CURRENT inexistente | `test_current_pointing_to_missing_generation_fails_closed` | Falla cerrada explícita |
| Rollback de reloj | `test_clock_rollback_never_reuses_generation_id_or_future_worker_checkpoint` | UUID distinto y rechazo de checkpoint futuro |
| Generación mezclada | `test_individually_valid_checkpoint_cannot_be_mixed_into_generation` | Rechazo de id/as_of/config/watermark adulterados |
| Status ligado | `test_status_cannot_link_a_report_outside_the_committed_cut` | Referencia fuera del cut denegada |
| fsync/rename order | `test_durability_order_precedes_current_and_root_fsync_failure_keeps_whole_new_cut` | Orden real observado; fallo tras rename sigue completo |
| Consumer real | `test_all_three_roles_are_verified_by_the_actual_capacity_consumer` | Report válido no oculta checkpoint corrupto |
| Source audit ligado | `test_source_audit_digest_is_bound_to_actual_source_reports_before_commit` | Link inválido impide commit |
| Primer watermark / falta preopen | `test_received_native_sources_are_audited_even_before_initial_watermark_or_without_preopen` | Native receipts auditados, sin duplicar ingestión |
| Aliases / traversal | `test_hostile_aliases_fail_closed_without_modifying_external_inputs`, `test_manifest_path_traversal_cannot_select_any_external_input` | Inputs externos intactos |
| Gzip bomb / UUID collision | `test_compressed_payload_expansion_is_bounded_even_when_hashes_match`, `test_uuid_collision_cannot_overwrite_existing_generation` | Bounds y evidencia anterior intactos |
| Soft/hard cuotas | `test_canonical_worker_soft_pressure_is_committed_and_hard_pressure_never_rewrites_cut` | Warn committed / hard sin nuevo cut |
| 513 files / >128 MiB | `test_513_files_and_over_128mib_preserve_committed_evidence` | Sin borrado no autorizado |
| Health seguro | `test_canonical_loop_records_explicit_backpressure_and_does_not_overwrite_status`, `test_failure_diagnostics_never_persist_exception_message_secrets` | Loop continúa y reason exacto seguro |
| Cache bounded | `test_radar_counter_memory_is_bounded_even_when_old_points_have_expired` | No acumulación sin límite |

## Validación del frente y límites de claims

Suite focal final: 48 nuevas regresiones más 146 regresiones existentes de worker, configuración dinámica, políticas de fuente, lab de salidas, señales de entrada y funnel. JUnit `/tmp/issue465-f03-final-focal.xml`, 194/194 GREEN, SHA256 `16c2d945a87c039a2c53e15a4b77a18e339a6778ed4e3d7beb0cb6b87c954ca6`; failures/errors/skipped/xfail = 0. El artifact final de la integración debe resolver head/tree y conservar esta evidencia junto al RED.

Los fronts C y D se probaron conjuntamente usando solamente blobs frozen del frente D `74d843a896adc684a6146a1b03a1eccbba47fa94`; este commit no incluye sus archivos. Es imprescindible integrar su head final, que debe preservar esos blobs, antes de ejecutar la suite consolidada.

PAPER/SHADOW ONLY; `real_orders_sent=0`; `real_routes=NOT_CALLED`; no provider calls, BYMA API, source DB writes, PPI Watch, runtime productivo, merge o deploy. DEPLOY_OWNER NOT_ACQUIRED. Stress representativo y las cinco salidas factuales concurrentes pertenecen al harness del owner final. La evidencia local no certifica host productivo, mercado OPEN, continuidad física tras una pérdida de energía ni edge financiero; los fsync y pruebas de SIGKILL verifican la implementación y el reinicio offline. Un fallo de persistencia jamás se convierte en un fill o dato de mercado inventado.
