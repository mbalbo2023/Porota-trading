# POROTA TRADING RC6 — CHECKPOINT CANÓNICO DE CONTINUIDAD

Actualizado: 2026-09-10 18:33 ART (America/Argentina/Buenos_Aires)

## 1. Recuperación inmediata

Repositorio: `mbalbo2023/Porota-trading`

Rama activa de trabajo: `fix/rc6-w10-sector-map-binding-20260910`

HEAD funcional previo a este checkpoint: `ab9c31c6ab36d8011f2c44285006fd00130a6776`.

Commits recientes relevantes:

- `d88104a25865eb358627c72530857c6159f66926` — `ci(rc6): make W12 permission probe stdin-safe`.
- `33066d128d4d25ba8583c89a169c89825e323fe6` — corrección mínima del test legacy RC4-HF2 para identidad RC6.
- `b373e1ae9c9cf43f8af6b40ff81f797579f671fc` — amplía RCA read-only para exponer el boundary del productor W12.
- `ab9c31c6ab36d8011f2c44285006fd00130a6776` — expone además wiring `DOCKER_BIN`/`CONTAINER` para diseñar el fix de origen sin adivinar.

Regla de continuidad: antes de repetir RCA o pruebas, leer este archivo y continuar únicamente desde los pendientes abiertos. Cada avance relevante debe actualizar este mismo checkpoint.

## 2. Invariantes de seguridad obligatorios

- Runtime: `PRODUCTION_PAPER`.
- Ejecución real: bloqueada.
- `real_orders_sent=0` según los controles W12.
- No habilitar rutas de órdenes reales.
- Browser/scraping/Contract Evidence: GET-only/read-only.
- No mutar fondos, órdenes, cuenta, seguridad ni 2FA.
- El paso final a producción real exige evidencia limpia y aprobación explícita del usuario.

## 3. Cerrado — NO REPETIR

- W10 sector-map binding: GREEN/cerrado.
- RCA trusted browser/auth/device: cerrado.
- RCA EOD ya realizado: no repetir aquí.
- W12 probe viejo que terminaba prematuramente por `find | sort | head -1` bajo pipefail: superado.
- Falso verde del probe W12 causado por `docker exec -i` consumiendo stdin del here-doc SSH: diagnosticado y corregido en `d88104a...`.
- Settlement RCA original: la contradicción entre test legacy y opt-in explícito de `PAPER_T1_FULL_DATE_RELEASE` ya está corregida en el test; no cambiar el default financiero fail-closed.

## 4. W12 — causa raíz exacta confirmada

Estado: **ROJO funcional / RCA CERRADO / FIX DE PRODUCTOR EN PREPARACIÓN**.

Workflow concluyente inicial: `RC6 W12 Capture Permission RCA 2026-09-10`.
Run: `34525822567`.
Job: `103034338686`.

Evidencia inicial:

- capture host: `/opt/porota-trading/data/contract_evidence/rc6_trusted/contract_20260910T201457Z.json`;
- archivo `0600 root:root`;
- importer/observer `uid=1000(botuser) gid=1000(botuser)`;
- `CAPTURE_READABLE_BY_IMPORTER_USER=NO`;
- Python read-only: `PermissionError: [Errno 13] Permission denied`;
- DB accesible, `MODE=PRODUCTION_PAPER`, `REAL_ORDERS_SENT=0`;
- `MUTATIONS=NONE`, `IMPORTER_EXECUTED=NO`, `ORDER_ROUTES=NOT_CALLED`.

Conclusión: W12 no falla por DB ni por lógica de importación; falla en la frontera de permisos productor -> importer.

### 4.1 Productor exacto identificado

Runtime systemd instalado en host:

- unidad `porota-contract-evidence-rc6.service`;
- `WorkingDirectory=/opt/porota-trading`;
- `ExecStart=/usr/local/sbin/porota-contract-evidence-rc6-runtime.sh`;
- no hay `User=` ni `Group=` declarados en la unidad, por lo que el servicio usa el usuario por defecto de systemd (root);
- script host: `/usr/local/sbin/porota-contract-evidence-rc6-runtime.sh`, modo `0755 root:root`, SHA256 observado `bea432952157efa240958e89f1254091d89bfbfde95014c39fedd01c72e599ee`.

RCA read-only run `34532164378`, job `103055240194`, encontró la línea causal exacta del productor:

```bash
install -o root -g root -m 0600 "$STAGE_CAPTURE" "$CAPTURE"
```

El mismo script hace además:

```bash
mkdir -p "$OUTDIR"
chmod 0750 "$OUTDIR"
```

sin alinear explícitamente el grupo del directorio con el importer. Por ello el fix final debe cubrir **directorio y archivo**, no solamente el archivo.

El importer se ejecuta luego en `porota_production_observer` y el runtime identificado es `botuser`, uid/gid 1000. El script ya dispone de `DOCKER_BIN` y `CONTAINER` para el import; el RCA adicional `ab9c31c...` está verificando en qué punto se definen para reutilizarlos de forma segura, sin hardcodear si no es necesario.

Última captura observada por el RCA a las 21:26 UTC:

- `contract_20260910T212326Z.json`;
- schema `POROTA_RC6_PPI_TRUSTED_CONTRACT_V1`;
- `AUTHENTICATED_TRUSTED_DEVICE`;
- `real_orders_sent=0`;
- 6 routes, 1 job, 14 non-read routes bloqueadas.

Esto demuestra que el collector sigue generando evidencia útil y que el blocker restante es el handoff de permisos/importación.

### 4.2 Fix W12 obligatorio

Aplicar el fix **en el origen** del productor, siguiendo el patrón seguro ya usado en RC4:

- resolver `runtime_gid` del observer/importer;
- directorio `root:<runtime_gid>` con `0750`;
- capture `root:<runtime_gid>` con `0640`;
- nunca `0644`, `0777` ni chmod/chown masivo de `/data`;
- no usar un post-hoc chmod como solución productiva definitiva.

Después del fix:

1. exactamente una captura fresca GET-only/read-only;
2. importer rc=0;
3. capture parseable/no vacío;
4. Contract Evidence v2 run count debe superar baseline 105;
5. ningún run RUNNING atascado;
6. DB `quick_check=ok`;
7. `PRODUCTION_PAPER`, capability real bloqueada, `real_orders_sent=0`.

Sólo entonces W12 pasa a GREEN.

## 5. Settlement RC6 — fallo original resuelto

Estado: **VERDE para el fallo original**.

El test que antes fallaba:

`tests/test_rc4_hf1_last_mile.py::test_t1_without_cutoff_waits_until_full_expected_date_elapsed`

ya figura `PASSED` en la suite real posterior al fix. La suite avanzó hasta aproximadamente **1535 tests pasados** antes de encontrar un fallo legacy diferente.

Se mantiene la política correcta:

- `PAPER_T1_FULL_DATE_RELEASE=false` por defecto;
- opt-in explícito para la liberación conservadora T+1 en PAPER;
- no se cambió la lógica financiera ni se habilitó ninguna capacidad de orden real.

No repetir el RCA de settlement ni cambiar el default a `true`.

## 6. Nuevo fallo legacy de compatibilidad RC6

Estado: **PARCHEADO / PRUEBA FOCAL PENDIENTE**.

Nuevo fallo después de ~1535 tests:

`tests/test_rc4_hf2_consolidation.py::test_hf2_version_and_orders_remain_blocked`

La prueba heredada esperaba literalmente:

- `VERSION == "17.0.0-rc4-hf2"`;
- `IMAGE == "porota-trading-bot:17.0.0-rc4-hf2"`.

El runtime canónico ya es RC6. El test fue corregido en commit `33066d128d4d25ba8583c89a169c89825e323fe6` para esperar:

- `17.0.0-rc6`;
- `porota-trading-bot:17.0.0-rc6`;
- manteniendo `REAL_ORDER_CAPABILITY == "BLOCKED"`.

No se modificó `_version.py` ni lógica productiva.

Pendiente: evidencia de targeted test / siguiente fallo real. Los workflows generales disparados automáticamente por push pueden continuar, pero no se debe relanzar manualmente la suite completa por cada cambio.

## 7. Divide y vencerás — carriles activos

Carril A — **W12 prioridad máxima**: cerrar wiring de `DOCKER_BIN/CONTAINER` -> parche mínimo de productor -> una captura fresca -> importer -> persistencia CE -> GREEN.

Carril B — **settlement**: fallo original GREEN; conservar política fail-closed y no repetir RCA.

Carril C — **compatibilidad suite**: test RC4-HF2 ya parcheado; validar de manera focalizada y sólo corregir el siguiente blocker si aparece.

Carril D — **build/deploy readiness en paralelo**: preparar validaciones de candidate sin activar producción real. Suite completa única cuando W12 y el test focal estén GREEN.

## 8. Pendientes posteriores preservados

- historical/background ingestion: freshness, gaps y cobertura;
- Contract Evidence scheduler/backoff/idempotencia;
- matriz de familias/API/contratos;
- doble calendario Argentina/EE.UU. para CEDEAR y subyacentes;
- UX/accesibilidad tablet;
- introspección/early-warning horario;
- follow-ups de lógica de salidas/EOD de la auditoría.

## 9. Regla para un nuevo chat

Si la conversación se corta, leer COMPLETO este archivo en la rama indicada, verificar HEAD actual y los workflows posteriores a este checkpoint. No reconstruir pruebas cerradas ni asumir que un workflow fue exitoso sin leer su evidencia/log final. Continuar desde los carriles A/C/D.
