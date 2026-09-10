# POROTA TRADING RC6 — CHECKPOINT PREDEPLOY + RECONCILIACIÓN AUDITORÍA

Fecha local: 2026-09-09 ~23:00 ART
Repositorio: `mbalbo2023/Porota-trading`
Rama integración: `integration/rc6-waves-9-18-20260909`
HEAD integración al crear este checkpoint: `8a7eb7c82e2b8302edff24642267aaf1e6def8e2`
Runtime: mantener `PRODUCTION_PAPER`; invariante absoluta `real_orders_sent=0`.
Producción real: NO-GO absoluto.

## 1. Reglas operativas vinculantes

- Preparación, diagnóstico y tests pueden ejecutarse en paralelo.
- Integración y deploy son seriales/transaccionales para evitar pisar waves.
- No declarar cambio desplegado por CI/código; exigir evidencia live.
- Sector concentration queda `BINDING`/fail-closed; `SECTOR_UNMAPPED_BINDING` debe bloquear aperturas sin evidencia. Cierres nunca deben quedar bloqueados por concentración.
- IA intradía OFF; motor de decisión Python. Aprendizaje sólo post-jornada.
- PPI browser/scraper: read-only, fail-closed, GET/HEAD/OPTIONS, nunca `/Operar`.
- IOL: read-only solamente; nunca ejecución.
- No auto rollback. No backup inmediato pre-deploy.
- Accesibilidad Termius: intervención manual excepcional; por defecto un único bloque copy/paste, salida compacta copiada automáticamente al portapapeles y terminal abierta. `.sh` sólo si tamaño/riesgo lo justifica. No pedir reingreso de secretos ya almacenados.

## 2. Waves y deploy acumulativo

Integradas/preparadas: W9, W10, W11, W13, W14, W15, W16, W17, W18.
W12 sigue P0 funcional hasta Contract Evidence + readiness/wiring.

Último gate integrado conocido sobre `8a7eb7c...`: run `34425397972`, conclusión FAILURE; live-readonly preflight GREEN. Suite estática: 745 passed / 15 failed. No deploy acumulativo hasta cerrar estos fallos y rerun GREEN.

Bloqueos de integración conocidos:

1. `PAPER_ECONOMIC_GATE_MODE` efectivo queda `SHADOW` por `PAPER_DEFAULTS`/settings congelados, mientras tests/decisión RC6 exigen `BINDING`. Corrección objetivo: default congelado `BINDING`, no des-congelar el control.
2. Fixtures/tests históricos abren instrumentos sin sector; con W10 correctamente BINDING ahora fallan `SECTOR_UNMAPPED_BINDING`. Corrección: aportar sector determinístico a instrumentos conocidos de test; NO permitir UNKNOWN ni relajar BINDING.
3. Test viejo de disk gate conserva expectativa del piso fijo. Política actual es dinámica y debe actualizarse el test, no volver al piso rígido.
4. Aislar cualquier aserción dashboard legacy aún vigente después de los cambios actuales.

## 3. Take-profit / auditoría prediploy

La auditoría externa detectó correctamente que en un baseline anterior `target_price` se calculaba pero no disparaba salida. Este hallazgo era crítico.

Desde commit padre `6aa5971ce968a132344873d94003c81af3ba885d`, la rama integrada contiene cierre explícito por `TAKE_PROFIT_PAPER` en `bm_exit_supervisor.py` y pruebas dedicadas:

- target alcanzado -> intención/cierre PAPER;
- target alcanzado sin profundidad -> `EXIT_PENDING_NO_LIQUIDITY`;
- precedencia EOD;
- precedencia stop.

Estado: F-01 queda CORREGIDO EN CÓDIGO/TESTS, pero NO se considera cerrado operacionalmente hasta deploy + live proof.

La distinción sigue siendo obligatoria: stop/target disparado no equivale a fill garantizado. Deben exponerse `EXIT_DUE`, `EXIT_PARTIAL`, `EXIT_PENDING_NO_LIQUIDITY`, `EXIT_PENDING_EXECUTION` y causa final.

EOD: mantener perfil intraday-flat como default para la próxima campaña PAPER. No habilitar carry mañana sin modelo explícito de overnight risk, calendario, contrato, settlement y costes.

## 4. W12 / Contract Evidence

Bloqueo de auth/scraper antiguo está superado. Evidencia actual:

- trusted landing GREEN en `/Cotizaciones/Acciones`;
- deep scrape GREEN 16/16 rutas;
- 677 filas DOM;
- 200 detalles/fichas;
- 671 XHR first-party;
- 635 solicitudes non-read bloqueadas;
- `NO_INFERENCE=YES`;
- ninguna ruta de órdenes;
- PAPER/0 antes y después.

P0 actual de W12: convertir evidencia profunda en identidad/contrato determinista + wiring, no volver a investigar credenciales salvo evidencia nueva.

Pendientes W12:

1. cuantificar campos contractuales observados por familia;
2. resolver identidad exacta/provable normalization solamente; no fuzzy;
3. persistir Contract Evidence con provenance/hash/observed_at/completeness;
4. distinguir identity link de contract completeness;
5. recalcular coverage/readiness/can_simulate;
6. probar wiring Contract Evidence -> readiness/can_simulate -> motor/dashboard;
7. integrar W12 al acumulativo sólo después del gate propio.

## 5. Auditoría prediploy — reconciliación de hallazgos

- F-01 take-profit: STALE como ausencia de código; corregido en integración, falta deploy/live proof.
- F-02 fresh Contract Evidence: STALE respecto de auth/scrape; scraping profundo ya GREEN, pero contrato/wiring sigue P0.
- F-03 familias visibles != operables: VIGENTE. Mantener familias `NOT_PROVEN/HOLD` hasta contrato/readiness específico.
- F-04 stop no garantiza fill: VIGENTE POR DISEÑO; requiere estados pending/partial y métricas de latencia/liquidez.
- F-05 EOD universal: VIGENTE COMO DECISIÓN DE ESTRATEGIA. Mantener intraday-flat por seguridad; carry es trabajo separado, no cambio de último minuto.
- F-06 W10 BINDING live: VIGENTE hasta postflight/live proof.
- F-07 históricos incompletos: VIGENTE parcialmente; arquitectura de backfill/quality/readiness ya definida, implementación/cobertura live pendiente.
- F-08 provenance multi-source: VIGENTE parcialmente; arquitectura History Store v2 versionada/canónica definida, IOL debe preservar fuente+settlement/contrato.
- F-09 versionado/auditabilidad: VIGENTE P2; exponer runtime SHA/strategy/schema/policy.
- F-10 Voice Access UX acceptance: VIGENTE como aceptación final aunque W17 tenga live proof técnico.

## 6. IOL / histórico

Auth del Droplet: GREEN (`POST /token` 200, access+refresh present, GET GGAL 200), credenciales almacenadas en servidor; no volver a solicitarlas.

Rol permanente de IOL:

- bootstrap/backfill inicial hasta 365d cuando aplique;
- luego incremental post-cierre + pequeño overlap;
- fuente secundaria/cross-check/gap repair, no ejecución;
- precedencia: PPI > BYMA/A3 > IOL > auxiliares;
- discrepancia material -> `SOURCE_DISCREPANCY`, no promedio silencioso.

Antes del backfill masivo, preservar en la identidad histórica `fechaHora`, `plazo/settlement`, source y dimensiones contractuales (expiry/contract donde aplique). No colapsar múltiples observaciones legítimas del mismo día a `(symbol,date)`.

Backfill debe ser shadow/resumible/idempotente; `EMPTY` no equivale a instrumento inexistente. Quality gate antes de promoción canónica.

## 7. Datos / scheduler / backtest

Documento vinculante: `POROTA_TRADING_RC6_DATA_LIFECYCLE_SCHEDULER_BACKFILL_BACKTEST_2026-09-09.md`.

Stores separados:

- runtime DB HOT pequeña;
- Contract Evidence HOT/WARM;
- History Store v2 versioned/canonical;
- raw evidence COLD/TTL;
- progress ledger de backfill;
- resultados backtest/Forward Lab separados/append-only.

Scheduler: systemd en Droplet. Post-cierre: ingest PPI -> IOL incremental -> normalize -> versions/canonical -> gaps/divergence/freshness -> Contract/History reconcile -> readiness/can_simulate -> EOD/introspection -> aprendizaje post-jornada. Fin de semana/off-market: gap repair, backfill pesado, deep contract/history, backtests/Forward Lab, auditoría y housekeeping.

Backtest por familia al pasar Contract + History + Wiring. Pipeline: dataset congelado -> backtest -> walk-forward/embargo -> Forward Lab -> revisión -> PAPER. Nunca auto-promover parámetros.

## 8. Disco / cache / imágenes / capacidad

Limpieza segura ejecutada:

- builder cache ~1.59GB eliminado;
- imagen obsoleta eliminada sólo después de despejar referencias;
- approval gateway legítimo recreado sobre imagen RC6 vigente, healthy y estable;
- no se tocaron DB, History Store, volúmenes ni redes;
- observer/dashboard sin cambio;
- estado PAPER/0 preservado.

Disco aproximado después de limpieza: ~13GB libres / ~47% usado.

Política dinámica reemplaza piso rígido de 8GiB:

- operational reserve = max(4GiB, 20% del filesystem);
- build reserve = max(3GiB, 2x tamaño imagen vigente);
- deploy-pre exige operational + build reserve;
- deploy-post exige operational reserve;
- ingest-start agrega batch margin y preserva margen de próximo deploy.

No `docker system prune -a` ciego. Housekeeping siempre inventory-first/fail-closed.

## 9. Gate para próxima rueda PAPER

Antes de permitir nuevas aperturas:

1. integration gate 100% GREEN;
2. deploy serial exitoso del candidate acumulativo;
3. observer/dashboard running, restarts esperados y health GREEN;
4. DB quick_check=ok;
5. `PRODUCTION_PAPER` + `real_orders_sent=0`;
6. preopen/calendario/timers GREEN;
7. W10 sector `BINDING` live: apertura al límite bloqueada, cierre no bloqueado;
8. economics `BINDING` efectivo;
9. take-profit `TAKE_PROFIT_PAPER` probado en candidate live/smoke sin orden real;
10. sólo familias READY pueden abrir; resto HOLD;
11. W12 Contract Evidence/readiness real, no sólo DOM scrape;
12. freshness/coverage/source visible y no stale crítico;
13. Voice Access/tablet aceptación corta de rutas principales.

Hasta entonces: NO-GO para campaña PAPER irrestricta; producción real NO-GO absoluto.

## 10. Próximas acciones inmediatas

En paralelo:

A. integration: economics BINDING + test fixtures sectoriales + disk gate test + full regression;
B. W12: Contract Evidence exacto + readiness/wiring;
C. IOL: normalizador multi-dimensión + 365d shadow backfill + quality gate;
D. scheduler/backup: wiring incremental + restore proof;
E. UX: timeline de salida y aceptación Voice Access;
F. actualizar este checkpoint con runs/SHAs reales tras cada cierre importante.

Deploy sólo cuando el candidate acumulativo cumpla su gate. No hacer checkouts/deploys independientes de waves que puedan pisarse entre sí.