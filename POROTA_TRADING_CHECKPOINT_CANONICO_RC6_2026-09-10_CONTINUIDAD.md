# POROTA TRADING RC6 — CHECKPOINT CANÓNICO DE CONTINUIDAD

Actualizado: 2026-09-10 19:21 ART (America/Argentina/Buenos_Aires)

## 1. Recuperación inmediata

Repositorio: `mbalbo2023/Porota-trading`

Rama activa: `fix/rc6-w10-sector-map-binding-20260910`

HEAD funcional validado en prueba focal: `f8ae3e310b914a2c55e7c430eeea34dcaa4edd8a`.

Commit funcional más reciente:

- `f8ae3e310b914a2c55e7c430eeea34dcaa4edd8a` — `fix(rc6): keep economics shadow and sector concentration binding`.

Otros commits cerrados relevantes:

- `5d1a53fc51ca234224c70322967c1de51d32eb1` — preserva semántica de evidencia histórica parcial.
- `181e078efd3f5431d9b854542bb4dd55f08810a1` — W12 permission boundary + fresh import GREEN.
- `9811765130f1886aa6da20d7b3dadfc0cf02ef14` — alinea settlement legacy con opt-in T+1.
- `33066d128d4d25ba8583c89a169c89825e323fe6` — compatibilidad legacy RC4-HF2 con identidad RC6.
- `75c5bcd43d2eaeb4ef2d93808841748d973228b9` — semántica actual página En Vivo.

Regla de continuidad: leer este archivo completo, verificar HEAD actual y continuar sólo desde pendientes abiertos. No repetir RCA/pruebas cerradas. Actualizar este mismo checkpoint después de cada hito.

## 2. Invariantes de seguridad

- Runtime objetivo: `PRODUCTION_PAPER`.
- `_version.EXECUTION=SIMULATED`.
- `_version.REAL_ORDER_CAPABILITY=BLOCKED`.
- Última evidencia operacional W12: `real_orders_sent=0`.
- No órdenes reales, fondos, cuenta, seguridad ni 2FA.
- Browser/Contract Evidence GET-only/read-only.
- No permisos globales `0644`/`0777` ni cambios recursivos amplios sobre `/data`.
- Cualquier habilitación futura de ejecución real exige evidencia limpia y aprobación explícita del usuario.

## 3. Cerrado — NO REPETIR

- W10 sector concentration binding base: GREEN.
- W12 permission boundary/importer: GREEN.
- Trusted browser/auth/device RCA: cerrado.
- EOD RCA: cerrado para este frente.
- W12 `find|sort|head` bajo pipefail: cerrado.
- W12 falso verde por `docker exec -i` heredando stdin SSH: cerrado.
- Settlement RCA original: cerrado; default T+1 permanece fail-closed.
- Históricos `_history_batch_semantics`: implementado y focal GREEN.
- Tests legacy RC4-HF2 VERSION/IMAGE y En Vivo: cerrados.

## 4. W12 — GREEN CERRADO

Causa raíz: productor `/usr/local/sbin/porota-contract-evidence-rc6-runtime.sh` generaba capture `root:root 0600`; importer/observer corre como `botuser` uid/gid 1000.

Fix: commit `181e078efd3f5431d9b854542bb4dd55f08810a1`.
Workflow: `.github/workflows/rc6-w12-permission-boundary-hotfix-proof-20260910.yml`.
Run `34532755599`, job `103057174938`, `success`.

Estado probado:

- directorio `root:<observer_gid>` `0750`;
- capture `root:<observer_gid>` `0640`;
- `OBSERVER_GID=1000`;
- capture fresco `contract_20260910T213256Z.json`;
- `IMPORTER_USER_READ=YES`;
- schema `POROTA_RC6_PPI_TRUSTED_CONTRACT_V1`;
- auth `AUTHENTICATED_TRUSTED_DEVICE`;
- Contract Evidence v2 runs `105 -> 106`;
- `RUNNING_ROWS=0`;
- DB `quick_check=ok`;
- `MODE=PRODUCTION_PAPER`;
- `real_orders_sent=0`;
- `ORDER_ROUTES=NOT_CALLED`.

No repetir captura de aceptación salvo regresión nueva demostrada.

## 5. Settlement RC6 — GREEN

El fallo original quedó corregido sin modificar la política financiera productiva.

Política preservada:

- `PAPER_T1_FULL_DATE_RELEASE=false` por defecto;
- liberación conservadora T+1 sólo por opt-in PAPER explícito;
- no se habilitó capacidad de orden real.

Pruebas focales settlement/legacy posteriores: GREEN.

## 6. Históricos parciales — GREEN

Commit: `5d1a53fc51ca234224c70322967c1de51d32eb1`.

Semántica canónica implementada en runtime:

- `GREEN`: lote completamente usable;
- `YELLOW`: existe evidencia usable pero parcial/incompleta;
- `RED`: cero evidencia usable/falla dura.

Regresión focal de históricos: 9 tests GREEN.

## 7. Gate completo anterior y hallazgos

Se ejecutó el gate completo sobre `5d1a53fc51ca234224c70322967c1de51d32eb1`.

Pasaron antes de pytest activo:

- linaje/HEAD exacto;
- identidad `_version.VERSION=17.0.0-rc6`;
- `MODE=PRODUCTION_PAPER`;
- `EXECUTION=SIMULATED`;
- `REAL_ORDER_CAPABILITY=BLOCKED`;
- compilación;
- Docker build;
- probes focales offline/read-only.

La suite activa encontró 40 fallos agrupados en sólo dos causas:

1. deriva de autoridad: `PAPER_ECONOMIC_GATE_MODE` había quedado `BINDING` aunque el camino acordado para economics era SHADOW primero;
2. cascada del ledger porque fixtures contables pasaban por el gate sectorial BINDING, más un falso `SECTOR_UNMAPPED` cuando una clasificación revisada existía para el mismo instrumento/mercado/moneda pero otro settlement.

No se interpretaron como 40 defectos independientes.

## 8. Política de autoridad RC6 — DECISIÓN CANÓNICA DEL OPERADOR

Camino general: `SHADOW -> evidencia -> evaluación -> autorización explícita -> BINDING`.

Excepción ya autorizada:

- `SECTOR_CONCENTRATION = BINDING`.
- máximo explícito: `PAPER_MAX_POSITIONS_PER_SECTOR=2`.
- si falta evidencia sectorial real, fail-closed.
- no fuerza ventas existentes; sólo puede vetar nuevas aperturas PAPER.

Resto:

- `PAPER_ECONOMIC_GATE_MODE=SHADOW`.
- `PAPER_EXPECTANCY_POLICY=OBSERVATION_ONLY`.
- `PAPER_MARKET_REGIME_POLICY=ALERT_ONLY`.
- IA intradiaria permanece OFF.

Ninguna de estas políticas habilita órdenes reales.

## 9. Convergencia policy/sector — GREEN FOCAL

Commit funcional: `f8ae3e310b914a2c55e7c430eeea34dcaa4edd8a`.

Workflow materializador/proof:
`.github/workflows/rc6-policy-sector-convergence-20260910.yml`.

Run: `34536775030`.
Job: `103070120748`.
Conclusión: `success`.

Etapas todas GREEN:

1. materialización de política acordada;
2. `compileall`;
3. Docker build focal;
4. regresiones offline;
5. commit sólo después de prueba verde.

Cambios funcionales/resultantes:

- economics vuelve a `SHADOW` en mode manager, runtime, broker y dashboard;
- sector concentration permanece `BINDING`;
- límite sectorial `2` queda congelado explícitamente en defaults y frozen settings;
- sector lookup usa coincidencia exacta primero;
- si sólo cambia settlement, puede reutilizar clasificación únicamente cuando `ticker+family+market+currency` tienen un único sector revisado;
- jamás cruza moneda, familia o mercado;
- si la evidencia es ambigua, queda unmapped y BINDING falla cerrado;
- tests de integridad de ledger aíslan su fixture del gate de selección de entrada; producción NO se debilita;
- `SECTOR_CONCENTRATION` queda documentado como única excepción `BINDING_PAPER` en el contrato SHADOW/BINDING.

## 10. Próximo carril — ÚNICO BLOCKER/GATE

Estado actual: **FOCUSED GREEN / FULL CANDIDATE PENDING**.

Siguiente acción exacta:

1. promover/usar el SHA funcional exacto `f8ae3e310b914a2c55e7c430eeea34dcaa4edd8a` como candidato RC6;
2. ejecutar UNA sola vez el full candidate gate sobre ese SHA;
3. exigir suite activa RC6 GREEN completa;
4. exigir build por SHA/digest e invariantes `PRODUCTION_PAPER/SIMULATED/BLOCKED`;
5. si aparece un nuevo blocker, aislar sólo el primero y corregir focalmente;
6. si queda GREEN, preparar deploy transaccional actual con preflight/postflight/rollback, pero NO dispararlo hasta orden explícita del usuario.

## 11. Pendientes funcionales preservados para post-gate/postflight

No perder del alcance RC6:

- historical/background ingestion: freshness, gaps y cobertura;
- Contract Evidence scheduler/backoff/idempotencia;
- matriz familias/API/contratos;
- doble calendario Argentina/EE.UU., especialmente CEDEAR;
- UX/accesibilidad tablet;
- introspección/early-warning horario;
- follow-ups de lógica de salidas/EOD.

Estos deben verificarse en candidate/postflight según sus gates ya existentes; no reabrir RCA cerrados sin evidencia nueva.

## 12. Regla para nuevo chat

Si el chat se corta: leer COMPLETO este checkpoint, verificar HEAD y workflows posteriores y continuar desde sección 10. No repetir W10, W12, settlement ni históricos ya cerrados. No asumir éxito por nombre de commit: validar logs/evidencia.
