# RC6 #717 — cierre de despliegue y censo visual del runtime (22/10 ART; 23/10 UTC)

WORKSTREAM_ID: `WS-RC6-714-PAPER-CAUCION-CARRYOVER-20261022`; continuidad #712–#717. Producto: `deploy/rc6-pr69-isolated-20260915@c0a5ad35a731928c0c214b11a7fac9ac99681155`, árbol `f3c41369c6165f7f964836ad4f06d19c5cdd1777`. Este documento vive en `main`, fuera de la rama que dispara Deploy V2. Corte de evidencia: 23/10/2026 00:14–00:15 UTC, después del cierre de rueda ART. **Deploy VALIDATED_RUNTIME; sistema integral NO_GO.**

## Promoción, seguridad y RCA del fix-forward

| Gate | Recibo | Resultado y discriminante |
|---|---|---|
| #712 dashboard | [PR #712](https://github.com/mbalbo2023/Porota-trading/pull/712), [Predeploy #39848309035](https://github.com/mbalbo2023/Porota-trading/actions/runs/39848309035) | Corrigió HOLD sin gate, conteo de etapas no alcanzadas de cohorte comprometida, grano de familia y vista vacía. Primer Deploy #39851933599 RED **antes de escrituras**: caución PAPER abierta. No se canceló ni se simuló vencimiento anticipado. |
| #715 tarifa caución | [PR #715](https://github.com/mbalbo2023/Porota-trading/pull/715) | Auditor confundía cotización sin principal/comisiones con colocación inválida. Términos elegidos y tarifa PAPER propia sí conservaban igualdad; regla estrechada. El Deploy posterior #39865578392 llegó a instalar, luego COLD rechazó cancelación de archivo previo; quedó en `FIX_FORWARD_VALIDATION_REQUIRED`. |
| #716 COLD | [PR #716](https://github.com/mbalbo2023/Porota-trading/pull/716), [Predeploy #39867883489](https://github.com/mbalbo2023/Porota-trading/actions/runs/39867883489) | Validación de stop archivado con recibo pendiente exacto y admisión COLD2. Deploy #39870401268 RED en guard de disco **antes de transferencia**: 2.985.967.616 B libres, exigidos 4.039.230.766 B. |
| #717 disco | [PR #717](https://github.com/mbalbo2023/Porota-trading/pull/717), [Predeploy V2 #39871191336](https://github.com/mbalbo2023/Porota-trading/actions/runs/39871191336) | Cleanup offline acepta sólo recibo pendiente exacto, retaga imagen estable al digest previamente validado y retira aliases sin referencias; luego, de ser necesario, imágenes antiguas RC6 identificadas sin contenedores. 73 pruebas focalizadas; suite 4.555/4.555, cero fallos/errores/skip/xfail, cinco pruebas nativas `0|false`. Artefacto #12466739817, 297.115.136 B, ZIP SHA256 `b8206719f84b9172c5e44342006691f3def6ce73414d1ab5c0df62155c23e77f`. Candidato `dfb0309042dd4a22a7cda6fb32ca97c67ee25125`, mismo árbol que merge `c0a5ad35a731928c0c214b11a7fac9ac99681155`. |
| Instalación | [Deploy V2 #39873839848](https://github.com/mbalbo2023/Porota-trading/actions/runs/39873839848) SUCCESS | Liberó 1.435.410.432 B de imágenes exactas fuera de uso; admisión 4.421.193.728 B libres frente a 4.039.308.852 B requeridos. Transferencia y tar SHA256 `ab5572e6f65048c0ccfb06739474fb6b00a708acfd734371385d955c5670d107` iguales al congelado; identidad de fuente instalada GREEN. Docker normalizó config ID `sha256:22b5cf...` congelado a `sha256:8382fd...` cargado: diagnóstico observado, no igualdad falsa de image ID. Contenedor ejecuta ID cargado y fuente exacta. COLD/COLD2, reconciliación, servicios y schedulers RC6 GREEN; cleanup de backups `DONE|count=0`; host 4.358.647.808 B libres; PAPER poststart `CLOSED_CAUCION→CLOSED`, caución OPEN intacta hasta vencimiento natural; `REAL_ORDERS_SENT=0`, rutas reales `NOT_CALLED`. PPI Watch no modificado. Se omitió estrés host por orden del operador. `CURRENT_STATE_V2=GREEN|validation_status=VALIDATED_RUNTIME`. |

### Caché y tiempo

El Droplet tenía **0 B** de build cache; la poda genérica anterior (`docker builder prune -af`/`buildx prune -af`) reclamó 0 B y no justificaba vaciar una cache futura. Lo que bloqueaba esta promoción eran imágenes antiguas y una etiqueta fallida sin referencias. #717 dejó el retiro condicionado a IDs/recibos observados y al umbral exacto, sin volúmenes, bases ni PPI Watch. Buildx GHA y paralelismo nativo de #709 se incorporaron mediante #712; el PR viejo #709 se cerró sin merge por supersesión. Su medición separada fue 22m57s frío y 21m11s caliente frente a 26m43s base; no extrapolar ahorro garantizado a cada Deploy. `cadence-fresh→cadence-legacy` sigue causal y `session` corre en paralelo, conservando build once, SHA/manifiesto y artefacto congelado. No se aprobó clasificador diff-aware ni salto selectivo de tests sin matriz de imports y gates negativos.

## Censo visual de la imagen instalada

[Actions read-only #39876835881](https://github.com/mbalbo2023/Porota-trading/actions/runs/39876835881), commit diagnóstico `95a599deb5cd5513081a9a528f5f263215dce816`, artefacto de 78 capturas y JSON sanitizados #12468931996, digest ZIP SHA256 `3ac40d15e9399c7ea66d573ab932e3ed67ee90b796ef59475fd125d23d8174dc`. Navegador Chrome autenticado por túnel local, sólo GET/HEAD, 52 rutas, 49 tablas, 310 diálogos de primera página abiertos/cerrados, menú móvil abierto/cerrado, 78 capturas desktop/tablet/móvil; cero errores JS, desbordes o hallazgos finales. Tres paginadores (`/en-vivo/decisiones`, `/instrumentos`, `/analitica/anual`) pasaron a 10 filas distintas con `offset=10`. El primer censo #39875827569 dio dos falsos hallazgos por observar DOM transitorio; #39876172934 falló por `AttributeError` introducido en la sonda; se corrigió **sólo la sonda**, y #39876835881 comprobó la navegación real sin tocar producto. Los 203 `NO_VERIFICADO` **son apariciones visibles, no 203 incidentes independientes**, y continúan presentes.

| Estado visible | Apariciones | Causa y decisión de autoridad |
|---|---:|---|
| `NO_VERIFICADO` | 203 | Repartidas entre tarjetas, tablas, estado de lectura y explicaciones. Una cuenta textual incluye reiteración de la misma fuente/campo en distintas rutas; los diálogos contienen más repeticiones y no se suman a 203. Se conserva cada ausencia factual hasta tener reloj, identidad, scope, escritor y recibo; no se reemplaza por cero o READY por cosmetología. |
| `OFF_BASELINE` | 52 | Banner global del selector; capacidad OPEN sin línea base certificada. No promover. |
| `STALE` | 83 | 52 cabeceras repiten el último reloj de mercado después de MARKET_CLOSED; el resto son fuentes/relojes scoped. Heartbeat PAPER fresco no convierte quotes/books posteriores al cierre en datos frescos. |
| `SOURCE_UNAVAILABLE` | 1 | Scope de integración sin fuente; otros estados IOL por sección quedan desagregados en truth. |
| `ANNUAL_NO_SELECTABLE_CANONICAL_SERIES` | 1 | Histórico legacy bloqueado: 1.151 filas `production_history` sin recibos originales completos por identidad, base, moneda, reloj/versionado PIT. |

La sonda de productor read-only del mismo run midió SHADOW comprometido y fresco, pero `EQUITY_SPOT`/`SCALPING` tenían `telemetry_count=0`, `capacity_status=NO_VERIFICADO`, `CAPACITY_EVIDENCE_INVALID`. Últimas diez decisiones eran HOLD sin gate BUY: economics/risk `NOT_EVALUATED` es la explicación factual, no un gate inventado. Riesgo diario ARS/USD/USD_CCL/USD_MEP `READY`, valuación `CURRENT`, cero posiciones stale a ese corte. Hay una caución ARS PAPER OPEN con vencimiento original 23/10 17:00 ART, sin otras posiciones abiertas, contador real 0. IOL complementario expuso 4.874 quotes: 1.950 LIVE, 2.493 CACHE_STALE, 431 SOURCE_UNAVAILABLE; secciones con disponibilidad distinta. BYMA_INSTRUMENTS_API seguía GRIS, algunas fuentes PPI/ROFEX antiguas AMARILLO; no se convierten en frescura por un check de login.

Para los ejemplos del operador: `/universo` ya muestra READY 9.884 desde `candidate_identity_v2`, pero elegibilidad/tradeability/HOT necesitan cohorte comprometida, scope y/o capacidad; `/universo/coverage` depende de telemetría OPEN válida; `/instrumentos/mercado` requiere seleccionar la identidad completa desde Buscador y reloj de proveedor, no ticker implícito ni reloj de captura; `/universo/tradeability` está en SHADOW con `entry_authority=false`. Abrir un detalle no crea evidencia. La vista de backups declara suspensión por política; cobertura/protección sin recibo por store siguen desconocidas aunque el recibo de deploy indique cero archivos al corte. El lector de fuente no transforma un check histórico en dato live.

## Pendientes por prioridad y discriminante de salida

| Frente del handoff / misión | Estado tras #717 | Próxima prueba o bloqueo exacto |
|---|---|---|
| P0 histórico anual, #685 F09 | **BLOQUEADO; NO_GO integral** | Ubicar fuente original por cada una de 1.151 filas/versiones con cinco claves de identidad, base de precio/ajuste, unidad, OHLCV, reloj proveedor y `version_known_at`, hash/recibo. Sin ella no hay reproyección PIT idempotente ni serie anual seleccionable; no borrar, imputar por ticker o elevar LKG. |
| P0 OPEN capacidad/preopen/SHADOW, F05/F06/F13 | **NO_VERIFICADO** | Muestras de primera rueda OPEN bajo este SHA: telemetría y denominador por motor, presupuesto de proveedor, cobertura y latencias, snapshots pre/post, decisiones y exits PAPER (EOD/MAX_HOLD/STOP/TAKE_PROFIT). La sesión medida aquí fue MARKET_CLOSED; un benchmark fuera de rueda no certifica OPEN. |
| P0/P1 fuentes, riesgo, dashboard, servicios | **MIXTO** | Dashboard/riesgo/servicios críticos y 52 rutas verificados al corte; frescura por identidad y fuente no global. PPI primaria read-only, IOL complemento parcial, BYMA_INSTRUMENTS_API gris, historia PPI/ROFEX stale. Exigir recibos/event clocks y scope antes de cambiar un badge; PPI Watch permanece separado. |
| P1 DB/WAL/duplicados/retención | **NO_VERIFICADO causal** | Tamaño y disco guardados; falta prueba de duplicado por identidad/evento/versiones ABA, índices y doble escritor en ventanas comparables. Ningún DELETE/VACUUM/REINDEX/copia pesada ni backup activado en host. |
| P1 módulos Python/wiring | **NO_VERIFICADO causal** | Los cinco candidatos de 763 archivos no se probaron muertos: CLI/diagnóstico/emergencia/carga dinámica. Requiere closure AST+entrypoints+timers+workflows+imagen exacta y test negativo antes de retirar. |
| P1 proceso Deploy/backtest | **Deploy #717 resuelto; laboratorio pendiente** | #709 incorporado vía #712 sin fusionar PR viejo. Falta diff classifier cerrado por defecto y A/B de tiempos/errores; backtest PIT/OOS no apto mientras fuente anual no tenga linaje. |
| P1/P2 schedules/alertas | **NO_VERIFICADO E2E** | Schedulers RC6 restaurados tras deploy y snapshots observados; faltan tres eventos genuinos `event=schedule` Market Watch en ventana, ACK y entrega Telegram sólo si hay transición real. No fabricar alerta ni crear programación. |

Cada causa que cambie requiere rama/PR aislado, prueba focalizada, Predeploy V2 con artefacto exacto y Deploy V2, seguido de navegador y fuente/DB/servicio en runtime. No se declara GREEN integral por el éxito del deploy ni por HTTP 200. PPI Watch, PAPER/SHADOW, cero órdenes reales y backups deshabilitados siguen invariantes.

## Censo por ruta

Los conteos siguientes son de texto visible en primera página de la captura sanitizada final. `NV` no implica 203 causas únicas. Los diálogos son los efectivamente abiertos y cerrados; filas posteriores sólo se probaron en los tres paginadores indicados. Capturas/JSON completos en el artefacto vinculado arriba.

| Ruta | NV | STALE | OFF_BASELINE | Diálogos |
|---|---:|---:|---:|---:|
| `/` | 6 | 1 | 1 | 0 |
| `/en-vivo` | 2 | 1 | 1 | 0 |
| `/en-vivo/oportunidades` | 0 | 1 | 1 | 1 |
| `/en-vivo/decisiones` | 1 | 1 | 1 | 10 |
| `/en-vivo/posiciones` | 0 | 1 | 1 | 1 |
| `/en-vivo/cierres` | 1 | 1 | 1 | 1 |
| `/en-vivo/capacidad` | 10 | 1 | 1 | 3 |
| `/en-vivo/workers` | 2 | 1 | 1 | 8 |
| `/en-vivo/fuentes` | 35 | 26 | 1 | 10 |
| `/trading` | 21 | 1 | 1 | 10 |
| `/trading/equity-spot` | 0 | 1 | 1 | 10 |
| `/trading/scalping` | 0 | 1 | 1 | 10 |
| `/trading/renta-fija` | 0 | 1 | 1 | 10 |
| `/trading/derivados` | 0 | 1 | 1 | 10 |
| `/trading/cauciones` | 1 | 1 | 1 | 7 |
| `/trading/tesoreria` | 0 | 1 | 1 | 10 |
| `/universo` | 0 | 1 | 1 | 10 |
| `/universo/discovery` | 2 | 1 | 1 | 1 |
| `/universo/tradeability` | 2 | 1 | 1 | 1 |
| `/universo/coverage` | 10 | 1 | 1 | 3 |
| `/universo/excluidos` | 2 | 1 | 1 | 1 |
| `/universo/familias` | 20 | 1 | 1 | 10 |
| `/instrumentos` | 0 | 1 | 1 | 10 |
| `/instrumentos/ficha` | 0 | 1 | 1 | 10 |
| `/instrumentos/contrato` | 0 | 1 | 1 | 10 |
| `/instrumentos/mercado` | 3 | 1 | 1 | 1 |
| `/instrumentos/evidencia` | 0 | 1 | 1 | 10 |
| `/riesgo` | 0 | 2 | 1 | 5 |
| `/riesgo/exposicion` | 1 | 1 | 1 | 1 |
| `/riesgo/limites` | 0 | 1 | 1 | 5 |
| `/riesgo/posiciones` | 1 | 1 | 1 | 1 |
| `/riesgo/liquidez` | 1 | 1 | 1 | 1 |
| `/riesgo/pnl` | 0 | 1 | 1 | 8 |
| `/riesgo/event-risk` | 3 | 1 | 1 | 1 |
| `/analitica` | 1 | 1 | 1 | 8 |
| `/analitica/senales` | 2 | 1 | 1 | 1 |
| `/analitica/salidas` | 0 | 1 | 1 | 10 |
| `/analitica/historico` | 10 | 1 | 1 | 10 |
| `/analitica/anual` | 20 | 1 | 1 | 10 |
| `/analitica/experimentos` | 2 | 1 | 1 | 1 |
| `/analitica/contrafactuales` | 7 | 1 | 1 | 1 |
| `/analitica/cierre-diario` | 0 | 1 | 1 | 1 |
| `/analitica/reportes` | 0 | 1 | 1 | 10 |
| `/sistema` | 3 | 1 | 1 | 8 |
| `/sistema/salud` | 2 | 1 | 1 | 8 |
| `/sistema/workers` | 5 | 1 | 1 | 9 |
| `/sistema/integraciones` | 5 | 6 | 1 | 10 |
| `/sistema/scheduler` | 9 | 1 | 1 | 10 |
| `/sistema/evidencia` | 1 | 1 | 1 | 1 |
| `/sistema/backups` | 9 | 1 | 1 | 4 |
| `/sistema/logs` | 0 | 1 | 1 | 10 |
| `/sistema/configuracion` | 3 | 1 | 1 | 8 |
| **Total** | **203** | **83** | **52** | **310** |
