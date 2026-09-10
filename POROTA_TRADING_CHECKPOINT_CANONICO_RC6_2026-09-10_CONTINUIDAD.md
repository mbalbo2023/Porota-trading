# POROTA TRADING RC6 — CHECKPOINT CANÓNICO DE CONTINUIDAD

Actualizado: 2026-09-10 18:36 ART (America/Argentina/Buenos_Aires)

## 1. Recuperación inmediata

Repositorio: `mbalbo2023/Porota-trading`

Rama activa: `fix/rc6-w10-sector-map-binding-20260910`

HEAD funcional verificado antes de este checkpoint: `75c5bcd43d2eaeb4ef2d93808841748d973228b9`.

Commits recientes relevantes:

- `d88104a25865eb358627c72530857c6159f66926` — corrige probe W12 stdin-safe.
- `33066d128d4d25ba8583c89a169c89825e323fe6` — alinea test legacy RC4-HF2 con identidad RC6.
- `ab9c31c6ab36d8011f2c44285006fd00130a6776` — cierra wiring del productor W12.
- `181e078efd3f5431d9b854542bb4dd55f08810a1` — `fix(rc6-w12): repair capture boundary and prove fresh import`.
- `75c5bcd43d2eaeb4ef2d93808841748d973228b9` — alinea aserciones legacy de página En Vivo con semántica actual.

Regla de continuidad: leer este archivo completo, verificar HEAD actual y sólo continuar desde pendientes abiertos. No repetir RCA/pruebas cerradas. Actualizar este mismo checkpoint después de cada hito.

## 2. Invariantes de seguridad

- Runtime `PRODUCTION_PAPER`.
- `REAL_ORDER_CAPABILITY=BLOCKED`.
- `real_orders_sent=0`.
- No órdenes reales, fondos, cuenta, seguridad ni 2FA.
- Browser/Contract Evidence GET-only/read-only.
- No permisos globales `0644`/`0777` ni cambios recursivos amplios sobre `/data`.
- Paso final a producción real requiere evidencia limpia y aprobación explícita del usuario.

## 3. Cerrado — NO REPETIR

- W10 sector-map binding: GREEN.
- Trusted browser/auth/device RCA: cerrado.
- EOD RCA: cerrado para este frente.
- W12 `find|sort|head` bajo pipefail: cerrado.
- W12 falso verde por `docker exec -i` consumiendo stdin SSH heredoc: cerrado.
- Settlement RCA original: cerrado; no cambiar default fail-closed.
- Test legacy RC4-HF2 de VERSION/IMAGE: corregido y ya pasó en suite posterior.

## 4. W12 — GREEN CERRADO

### Causa raíz

El productor `/usr/local/sbin/porota-contract-evidence-rc6-runtime.sh` generaba el capture con:

```bash
install -o root -g root -m 0600 "$STAGE_CAPTURE" "$CAPTURE"
```

mientras el importer/observer corre como `botuser`, uid/gid 1000. El directorio `rc6_trusted` también quedaba sin grupo explícitamente alineado.

### Fix de origen aplicado

Commit: `181e078efd3f5431d9b854542bb4dd55f08810a1`.

Workflow durable/reconciliador:
`.github/workflows/rc6-w12-permission-boundary-hotfix-proof-20260910.yml`.

Run: `34532755599`.
Job: `103057174938`.
Conclusión: `success`.

El runtime ahora resuelve dinámicamente el GID del observer/importer y aplica:

- directorio `root:<observer_gid>` `0750`;
- capture `root:<observer_gid>` `0640`;
- sin world-read;
- sin chmod/chown recursivo del árbol de datos.

### Evidencia final W12

- `OBSERVER_GID=1000`.
- baseline `contract_evidence_v2_runs=105`.
- `PATCH_STATE=APPLIED`.
- `RUNTIME_PATCH=GREEN`.
- `OUTDIR_MODE=750`, `OUTDIR_GID=1000`.
- exactamente una ejecución de servicio para la prueba, `SERVICE_START_RC=0`, `Result=success`, `ExecMainStatus=0`.
- capture fresco: `contract_20260910T213256Z.json`.
- capture `0640`, GID `1000`.
- `IMPORTER_USER_READ=YES`.
- capture bytes `3914`.
- schema `POROTA_RC6_PPI_TRUSTED_CONTRACT_V1`.
- auth `AUTHENTICATED_TRUSTED_DEVICE`.
- routes `6`, jobs `1`.
- `real_orders_sent=0`.
- DB `quick_check=ok`.
- post runs `106`, delta `+1` (> baseline 105).
- `RUNNING_ROWS=0`.
- `MODE=PRODUCTION_PAPER`.
- markers finales: `W12_PERMISSION_BOUNDARY=GREEN`, `W12_FRESH_CAPTURE=GREEN`, `W12_IMPORT=GREEN`, `ORDER_ROUTES=NOT_CALLED`.

**Veredicto W12: GREEN / cerrado. No repetir captura de aceptación salvo regresión nueva demostrada.**

## 5. Settlement RC6 — GREEN para fallo original

Test original:
`tests/test_rc4_hf1_last_mile.py::test_t1_without_cutoff_waits_until_full_expected_date_elapsed`.

La suite posterior lo mostró `PASSED`.

Política preservada:

- `PAPER_T1_FULL_DATE_RELEASE=false` por defecto;
- liberación conservadora T+1 sólo por opt-in en PAPER;
- no se modificó lógica financiera para hacer pasar el test;
- ninguna capacidad de orden real fue habilitada.

## 6. Compatibilidad legacy — GREEN para los dos fallos anteriores

La corrida posterior a los fixes mostró `PASSED` para:

- `tests/test_rc4_hf1_last_mile.py::test_t1_without_cutoff_waits_until_full_expected_date_elapsed`;
- `tests/test_rc4_hf2_consolidation.py::test_hf2_version_and_orders_remain_blocked`;
- `tests/test_rc4_hf2_consolidation.py::test_live_page_keeps_current_round_and_history_semantics_clean`.

No tocar `_version.py` ni lógica financiera por estos tests ya cerrados.

## 7. Nuevo primer blocker determinístico de suite

Workflow: `RC6 Post-W10 Next Failure RCA 2026-09-10`.
Run: `34532788953`.
Job: `103057279492`.

La suite llegó a:

- `1564 passed`;
- fallo al `88%`;
- W10 permaneció GREEN antes de la suite.

Primer fallo actual:

`tests/test_rc6_history_partial_semantics.py::test_all_full_is_green`

Error exacto:

```text
AttributeError: module 'bf_production_paper_observer' has no attribute '_history_batch_semantics'
```

Esto pasa a ser el único blocker de suite que se debe investigar ahora. No volver a corregir los fallos anteriores.

## 8. Divide y vencerás — carriles actuales

Carril A — **W12: GREEN/cerrado**. Sólo preservar fix y monitorear que el runtime no sea reinstalado con permisos antiguos.

Carril B — **settlement: GREEN/cerrado para el fallo original**. Mantener fail-closed.

Carril C — **suite/ingesta histórica: ACTIVO**. Investigar `_history_batch_semantics`, determinar si la función debe estar en el observer actual o si el test apunta a una API legacy, aplicar cambio mínimo y prueba focalizada.

Carril D — **build/deploy readiness: ACTIVO EN PARALELO**. Preparar candidate RC6 sin activar órdenes reales. Una suite completa adicional sólo después de cerrar el blocker histórico focalizado.

## 9. Pendientes preservados

- historical/background ingestion: freshness, gaps y cobertura;
- Contract Evidence scheduler/backoff/idempotencia;
- matriz familias/API/contratos;
- doble calendario Argentina/EE.UU., especialmente CEDEAR;
- UX/accesibilidad tablet;
- introspección/early-warning horario;
- follow-ups de lógica de salidas/EOD.

## 10. Regla para nuevo chat

Si el chat se corta: leer COMPLETO este checkpoint, verificar HEAD y workflows posteriores, y continuar desde Carril C/D. No repetir W10, W12 ni settlement ya cerrados. No asumir éxito por nombre de commit: validar logs/evidencia.
