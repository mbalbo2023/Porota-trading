# POROTA TRADING — CHECKPOINT INGESTA TOTAL — 2026-09-12

## Estado del checkpoint

Checkpoint operativo vivo. Objetivo: completar y clasificar toda la ingesta necesaria del universo argentino expuesto por PPI, agotando primero la API productiva de PPI y recién después usar PPI Web autenticada/scraping para los residuales. IOL queda exclusivamente como tercera fuente para huecos que sigan sin resolución después de PPI API + PPI Web.

### Actualización operativa 2026-09-12 13:12 UTC

- El primer intento de `API_CLOSEOUT` (run GitHub Actions `34695307006`) no llegó a mutar el shadow: la sesión SSH se cortó con `Broken pipe` durante la fase de discovery, antes de la transacción de refresh/clasificación. No se interpretó como fallo de PPI ni se avanzó a Web.
- Se aplicó forward fix en el workflow: SSH keepalive (`ServerAliveInterval=20`, `ServerAliveCountMax=15`) y discovery acotada a la lógica productiva ya probada para familias que complementan el catálogo normalizado, preservando fail-safe el universo conocido para no achicarlo por respuestas parciales del buscador.
- Nuevo `API_CLOSEOUT` activo: run `34695609656`, commit de workflow `0d27b1dbaab3fa3a0be5fa8d8af2f3db711f81b3`.
- El closeout reintenta toda identidad local sin ledger o con `ERROR` hasta tres veces, ingiere cualquier identidad nueva, clasifica persistentes, exige `API_UNCLASSIFIED=0` y recién entonces genera `ppi_web_residual_manifest_rc6`.
- PPI Web queda bloqueada hasta que `API_CLOSEOUT` cierre en verde. Ya existe el workflow preparado para iniciar captura read-only dirigida por las familias efectivamente presentes en el manifiesto residual; antes de usarlo se debe verificar el RCA del servicio weekend que estaba en `ExecMainStatus=4`.
- Seguridad verificada antes del closeout: `PRODUCTION_PAPER|0`; ninguna ruta de órdenes forma parte de estos workflows.

## Invariantes de seguridad

- Runtime: `PRODUCTION_PAPER`.
- Órdenes reales: `real_orders_sent=0`.
- Ninguna tarea de ingesta puede llamar rutas de órdenes.
- La ingesta histórica/de referencia es DATA y no debe depender de `can_simulate` ni otorgar `READY_PAPER` por sí sola.
- Universo informacional y universo operacional/PAPER permanecen separados.
- PPI Web se ejecuta en modo read-only; POST/PUT/PATCH/DELETE y rutas de trading/órdenes quedan prohibidas.
- Los colectores de navegador comparten `/run/lock/porota-ppi-web-browser.lock` y no pueden correr concurrentemente sobre el mismo perfil autenticado.

## Precedencia de fuentes — BINDING

1. PPI API productiva.
2. PPI Web autenticada / scraping read-only.
3. IOL únicamente para residuales no resueltos por PPI.

Nunca reemplazar silenciosamente evidencia PPI con IOL. Toda fila o evidencia debe conservar procedencia.

## Alcance

Solo instrumentos del mercado argentino expuestos por PPI. Mercados/tipos extranjeros excluidos del alcance actual: `ACCIONES-USA`, `FCI-EXTERIOR`, `NYSE`, `NASDAQ`.

## Universo API PPI de referencia

Shadow verificado: 1.960 identidades locales.

| Familia | Identidades |
|---|---:|
| ACCIONES | 55 |
| BONOS | 42 |
| CAUCIONES | 10 |
| CEDEARS | 191 |
| FCI | 1.040 |
| FUTUROS | 52 |
| LEBACS | 1 |
| LETRAS | 23 |
| LICITACIONES | 18 |
| ON | 91 |
| OPCIONES | 437 |
| **TOTAL** | **1.960** |

Fuentes del shadow: 901 identidades desde catálogo normalizado y 1.059 desde búsqueda productiva PPI. El shadow fue observado originalmente el 2026-09-12T04:02:07Z y se está refrescando/reconciliando en `API_CLOSEOUT` antes de declarar cierre definitivo de API.

## Histórico API — estado canónico antes del closeout final

`production_history_attempts` contiene 1.951 identidades. Estados observados:

- ACCIONES: 55 utilizables (25 PARTIAL + 30 VALID_PAYLOAD).
- BONOS: 42 utilizables (41 PARTIAL + 1 VALID_PAYLOAD).
- CAUCIONES: 10 utilizables (9 PARTIAL + 1 VALID_PAYLOAD).
- CEDEARS: 183 utilizables; 8 EMPTY_OR_INVALID.
- FCI: 1.007 utilizables (4 PARTIAL + 1.003 VALID_PAYLOAD); 24 EMPTY_OR_INVALID; 9 identidades todavía ausentes del ledger productivo por errores del pase nocturno y deben reintentarse/clasificarse.
- FUTUROS: 36 utilizables; 7 EMPTY_OR_INVALID; 9 ERROR.
- LEBACS: 1 ERROR/taxonomía-contract gap (`CEDI`).
- LETRAS: 16 utilizables; 4 EMPTY_OR_INVALID; 3 ERROR.
- LICITACIONES: 18 EMPTY_OR_INVALID; el endpoint histórico API respondió sin filas en las pruebas previas.
- ON: 33 utilizables; 58 EMPTY_OR_INVALID.
- OPCIONES: 180 utilizables; 257 EMPTY_OR_INVALID.

El tracker nocturno separado mostró 1.200 identidades al último probe: FCI 1.035 recorridos, con 998 VALID_PAYLOAD + 4 PARTIAL + 24 EMPTY_OR_INVALID + 9 ERROR; además reintentos parciales sobre residuales de otras familias.

## Resultado destacado de FCI

La pasada masiva recorrió los 1.035 FCI que faltaban respecto de la semilla inicial. Se obtuvieron 234.503 filas históricas válidas en ese pase. La cobertura utilizable canónica actual de FCI es 1.007/1.040; los 33 restantes se dividen en 24 vacíos y 9 errores a reintentar/clasificar antes de pasar esos casos a Web.

## Host y concurrencia

Host observado en el probe de 2026-09-12T12:59:33Z: `a47f3339ec6dfe9d5afde444b1aaddabceb0e94d` (detached HEAD). Este SHA está 13 commits por delante del SHA usado al instalar inicialmente los schedulers de ingesta y contiene cambios de dashboard/scalping/swing; no debe revertirse ni reemplazarse por un SHA viejo.

La rama de trabajo de ingesta continúa separada del SHA desplegado del host. Toda mutación server-side nueva debe gatear contra el SHA host actual y volver a verificar que no haya otro deploy activo/queued.

## Scheduler / Web

- API histórico nocturno: instalado y habilitado; último servicio completó con éxito.
- Scraper normal de contratos: lunes a viernes dentro de la rueda y protegido además por calendario BYMA.
- Excepción de fin de semana 12–13 Sep 2026: instalada con rutas locales solamente.
- En el probe 12:59Z, `porota-contract-evidence-weekend-backfill-rc6.service` figura `failed`, `ExecMainStatus=4`. Debe hacerse RCA antes de confiar en el backfill Web; no se considera cobertura Web completada.
- Evidencia Web actual previa al nuevo backfill: 520 filas current, incluyendo familias locales y algunas filas históricas extranjeras que deben filtrarse, no borrarse a ciegas.

## Criterio para declarar API COMPLETA

No alcanza con tener filas. La fase API se cierra solamente cuando:

1. se ejecuta refresh/reconciliación productiva del universo local contra las 1.960 identidades de referencia sin achicar fail-open el universo por respuestas parciales;
2. toda identidad local queda reconciliada contra el ledger histórico;
3. toda identidad queda clasificada como `VALID_PAYLOAD`, `PARTIAL`, `EMPTY_OR_INVALID` o error persistente/taxonomía documentado después de reintentos acotados;
4. cualquier identidad nueva descubierta recibe intento histórico;
5. los errores transitorios son reintentados antes de derivar a Web;
6. se conserva evidencia/provenance y `PRODUCTION_PAPER|0` antes/después;
7. `API_UNCLASSIFIED=0` es condición obligatoria de salida.

`EMPTY_OR_INVALID` confirmado no significa fallo de la ingesta: significa que PPI API fue agotada para esa identidad y el caso pasa al manifiesto residual Web.

## Residuales candidatos a PPI Web — NO cerrar hasta reconciliación

Candidatos conocidos antes del refresh final: CEDEARS 8; FCI 24 + errores no recuperados; FUTUROS 7 vacíos + 9 errores; LEBACS/CEDI 1; LETRAS 4 vacíos + 3 errores; LICITACIONES 18; ON 58; OPCIONES 257. El manifiesto definitivo debe salir del ledger final de `API_CLOSEOUT`, no de estos números preliminares.

PPI Web debe usarse primero para reconciliar identidad/contrato y luego para probes históricos residuales donde exista una ruta read-only comprobada. ETF Web permanece discovery-only hasta poder reconciliar una identidad local API/mercado válida. Ninguna evidencia Web auto-habilita operatoria PAPER.

## Deuda técnica permanente

`bf_production_paper_observer.py::_historical_targets(store)` todavía acopla targets históricos a `can_simulate`/estado operacional. Debe corregirse en código con tests para separar universo DATA del universo PAPER. Los jobs actuales de backfill son una vía desacoplada, no reemplazan esa corrección permanente.

## Próximo hito

`API_CLOSEOUT`: run `34695609656` en ejecución. Salida requerida: `API_UNCLASSIFIED=0`, manifiesto Web generado y seguridad `PRODUCTION_PAPER|0`.

Al completar `API_CLOSEOUT`, abrir inmediatamente `WEB_RESIDUAL_INGEST`: RCA del servicio weekend fallido, manifiesto residual exacto, reconciliación API↔Web y scraping read-only dirigido. Solo después de agotar PPI Web se habilita análisis de IOL para los huecos restantes.
