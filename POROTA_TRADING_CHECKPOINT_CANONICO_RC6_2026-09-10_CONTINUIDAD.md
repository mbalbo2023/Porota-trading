# POROTA TRADING RC6 — CHECKPOINT CANÓNICO DE CONTINUIDAD

Actualizado: 2026-09-10 18:02 ART (America/Argentina/Buenos_Aires)

## 1. Recuperación inmediata

Repositorio: `mbalbo2023/Porota-trading`

Rama activa de trabajo: `fix/rc6-w10-sector-map-binding-20260910`

HEAD verificado antes de crear este checkpoint: `d88104a25865eb358627c72530857c6159f66926`

Último cambio funcional previo al checkpoint: `ci(rc6): make W12 permission probe stdin-safe`.

Regla de continuidad: antes de repetir RCA o pruebas, leer este archivo y continuar únicamente desde los pendientes abiertos. Cada avance relevante debe actualizar este mismo checkpoint.

## 2. Invariantes de seguridad obligatorios

- Runtime: `PRODUCTION_PAPER`.
- Ejecución real: bloqueada.
- `real_orders_sent=0` según último control W12.
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

## 4. W12 — estado y causa raíz confirmada

Estado: **ROJO funcional / RCA CERRADO / FIX DE PRODUCTOR PENDIENTE**.

Workflow concluyente: `RC6 W12 Capture Permission RCA 2026-09-10`.

Run: `34525822567`.

Job: `103034338686`.

Resultado esperado: FAILURE/exit 20 porque el workflow ahora falla explícitamente al detectar el bloqueo.

Evidencia concluyente:

- Capture host: `/opt/porota-trading/data/contract_evidence/rc6_trusted/contract_20260910T201457Z.json`.
- Host: `MODE=600`, owner `root:root`, uid/gid `0:0`.
- Runtime importer/observer: `uid=1000(botuser) gid=1000(botuser)`.
- Dentro del contenedor el capture conserva `MODE=600`, owner `root:root`.
- `CAPTURE_READABLE_BY_IMPORTER_USER=NO`.
- Apertura Python read-only: `PermissionError: [Errno 13] Permission denied`.
- `READ_ONLY_OPEN_RC=1`.
- DB accesible; control: `MODE=PRODUCTION_PAPER`, `REAL_ORDERS_SENT=0`.
- `MUTATIONS=NONE`, `IMPORTER_EXECUTED=NO`, `ORDER_ROUTES=NOT_CALLED`.

Conclusión: el blocker W12 no es DB ni lógica del importer. Es la frontera de permisos entre el productor del capture y el usuario runtime `botuser`.

### Próximo paso W12

1. Localizar el productor exacto de `data/contract_evidence/rc6_trusted/contract_*.json`.
2. Corregir permisos **en el origen**, sin chmod global ni world-readable.
3. Patrón seguro preferido: directorio `root:<runtime_gid>` `0750` y archivo `root:<runtime_gid>` `0640`, o equivalente seguro `botuser` `0600`.
4. NO usar `0644`, `0777` ni relajar todo `/data`.
5. Ejecutar una sola captura fresca GET-only/read-only.
6. Ejecutar importer y exigir rc=0.
7. Confirmar nuevo Contract Evidence run (> baseline 105), sin RUNNING atascado, capture útil/no vacío y DB `quick_check=ok`.
8. Reconfirmar `PRODUCTION_PAPER`, órdenes reales bloqueadas y `real_orders_sent=0`.

Nota: `scripts/porota_contract_evidence_trusted_rc4.sh` ya implementa un esquema seguro de runtime gid (`0750` directorio, `0640` archivo). No modificarlo indiscriminadamente; el productor de `rc6_trusted` debe localizarse primero.

## 5. RC6 settlement — único fallo pytest conocido del carril de compilación

Estado: **AMARILLO / RCA AISLADO / FIX MÍNIMO PENDIENTE**.

Workflow: `RC6 Post-W10 Next Failure RCA`.

Run: `34521802994`.

Job: `103020869702`.

La suite alcanzó aproximadamente 1530 pruebas verdes antes del único fallo conocido:

`tests/test_rc4_hf1_last_mile.py::test_t1_without_cutoff_waits_until_full_expected_date_elapsed`

Fallo observado:

- esperado en el test: `2026-09-03 00:00 ART`;
- resultado de `validated_sale_settlement(...)`: `None`.

La prueba actual es semánticamente inconsistente con la política vigente: invoca `validated_sale_settlement(..., full_date_release=False)` y simultáneamente espera que la liberación full-date ocurra a medianoche.

El código actual de `cf_sale_settlement.py` está diseñado fail-closed:

- sin cutoff, full-date release queda apagado por defecto;
- con `full_date_release=False`, devuelve `None`;
- `settlement_guard_state` habilita explícitamente full-date release para `PRODUCTION_PAPER`.

Por tanto, la hipótesis de fix de menor riesgo es corregir el contrato del test (habilitar explícitamente `full_date_release=True` en el caso que prueba la liberación a medianoche) y conservar/agregar una aserción separada de que `False` sigue devolviendo `None`.

**NO cambiar el default de producción de False a True sin nueva evidencia**, porque eso debilitaría una política conservadora/fail-closed.

### Próximo paso settlement

1. Revisar pruebas adyacentes/contrato vigente para confirmar que no haya otra expectativa incompatible.
2. Aplicar fix mínimo al test, no a la política financiera, si la evidencia se mantiene.
3. Ejecutar únicamente el test focalizado/settlement regression.
4. No lanzar otra suite completa hasta que el test focalizado quede GREEN.
5. Después de W12 GREEN + settlement focalizado GREEN, ejecutar una suite completa para descubrir, si existe, el siguiente blocker real.

## 6. Orden de trabajo divide-y-vencerás

Carril A — prioridad máxima: W12 productor -> permiso seguro -> captura fresca -> importer -> persistencia CE -> GREEN.

Carril B — paralelo: settlement legacy test -> fix mínimo -> targeted pytest -> GREEN.

Carril C — una vez A+B verdes: build/suite RC6 completa y empaquetado/deploy candidate.

## 7. Pendientes posteriores ya conocidos

Sin bloquear el RCA actual pero deben conservarse para el cierre total RC6:

- historical/background ingestion: freshness, gaps y cobertura;
- Contract Evidence scheduler/backoff/idempotencia;
- matriz de familias/API/contratos;
- doble calendario Argentina/EE.UU. para CEDEAR y subyacentes;
- UX/accesibilidad tablet;
- introspección/early-warning horario;
- follow-ups de lógica de salidas/EOD de la auditoría.

## 8. Regla para un nuevo chat

Si la conversación se corta, iniciar leyendo COMPLETO este archivo en la rama indicada, verificar HEAD actual y los workflows posteriores a este checkpoint. No reconstruir pruebas cerradas y no asumir que un workflow fue exitoso sin leer su evidencia/log final.
