# POROTA RC6 — Ejecución unificada del 2 de octubre de 2026

## Estado de este checkpoint

**EN_GITHUB. Deploy V2 EN CURSO. Nueva validación runtime: PENDIENTE.**

Este documento debe leerse junto con los runs y el estado real del servidor. No confundir un merge ni un workflow iniciado con un despliegue validado.

WORKSTREAM_ID: WS-INTEG-RC6-20261002.
Tracker de ownership y evidencia: issue #441.
WRITE_OWNER: integración/auditoría de esta sesión.
DEPLOY_OWNER: ACQUIRED; pendiente liberación tras cierre documentado.

## Identidad

- Repo: mbalbo2023/Porota-trading.
- Productiva: deploy/rc6-pr69-isolated-20260915.
- Base anterior: 136453dc5b6d4146057142bcdd76b2f6b8d474e0.
- PR final: #445, MERGED mediante merge normal de dos padres.
- Candidato: b001ef1caf2f414a5022d2781016fa34f1611260.
- Tree candidato y merge: f3440892a5b3c14b5013f696710016209dc5a689.
- Product SHA tras merge: e9cf3fdbd9e6a71a0aa72365ff3b2727378db10f.
- Rama de integración: integrate/rc6-unified-20261002.
- Rama de herramientas/evidencia, sin promoción de sus workflows auxiliares: audit/rc6-unified-review-20261002.

## Pipeline y artefacto

- Builder de integración: 37076335843, SUCCESS; 215 tests focales sin fallos, errores ni skips.
- Refinamiento final de formato: 37076835694, SUCCESS; 18 tests sin fallos, errores ni skips.
- Predeploy V2 exacto: 37077028639, SUCCESS, sobre b001ef1.
- Artifact: 11256399584.
- Nombre: porota-predeploy-v2-b001ef1caf2f414a5022d2781016fa34f1611260.
- Digest ZIP confirmado por API y descarga del conector: sha256:0abd35ce25816692dc26f6c4678d9ea846c8b6c747103d46cc896632ea5f9142.
- Deploy V2 único: 37078173314, job 111072601172; en curso al guardar este checkpoint.
- Resolución exacta, descarga/verificación, espacio previo y transferencia: SUCCESS comprobados.
- Promoción remota, validación, soak, limpieza y cierre: no declarados finalizados en este checkpoint.

El esquema de congelado se deriva de la fuente exacta: porota-frozen-candidate.json, schema_version=1, validado por tests/ci_frozen_candidate_contract.py. No usar nombres/campos de otros esquemas ni IDs de artefactos copiados de resúmenes no revalidados.

## Alcance integrado

Entradas exactas y exclusiones en docs/releases/RC6_UNIFIED_INPUTS_2026-10-02.json del candidato:

| PR | SHA fuente | Alcance |
|---|---|---|
| #435 | 2c55af7ab4407b9fae218a96f06300a7e0670c71 | Imagen de critical_approval y cleanup seguro de candidatos sin referencias |
| #436 | fd84b4dd736e9f4ebd011495ab16df282ac51d37 | Retiro activo de GDELT |
| #437 | 5ce5927525866ad001348d48a2eb19850557a3c7 | Capacidad de confirmación intradiaria de Scalping |
| #438 | 217dbadfe418277f10ee0304c2a9ae0e02d723b9 | Distinguir cambios contractuales materiales de ruido de procedencia/timestamp |
| #439 | fe34c2e87d7bb53503cca173209e814b93b45e6e | Explicaciones veraces del dashboard |
| #440 | 2c2969e8accf7009ee338ae109258b80d042cb97 | Recuperación acotada de lecturas SQLite del supervisor de salidas |
| #442 | e67612fbba198d6c51ac588b38a56bbb57b73314 | Retiro activo de BCRA, preservando fuentes no retiradas |
| #443 | 67a7e06561337b2aaeff76dcf3ede15af013f764 | Históricos, tablet y superficies consistentes |
| #444 | 9dde0dfa224ffa9301458b61a5fc659524d831ea | Book fresco PPI de caución y conexión con cash-sweep PAPER |

Los PR históricos #426–#431 ya estaban absorbidos por #432/#433. No se reaplicaron ni se fusionaron ciegamente. No se eliminaron ramas ni heads de PR.

## Errores cruzados corregidos con defensas

1. GDELT conservaba tarjetas/imports activos tras retirar el collector. Se retiraron esas superficies y llamadas; quedan marcas estáticas de retiro y evidencia histórica. Regresión transversal del motor/dashboard.
2. El lector nuevo de caución confiaba en un catálogo READY que podía atrasarse respecto de un cambio contractual material. Se restableció el recheck sobre evidencia append-only usando la clasificación material de #438, más procedencia PPI exacta. Regresiones de carrera, cambio material, ruido y fuente desconocida.
3. El filtro de tarifas del cash-sweep rechazaba la nueva policy LIVE_PPI_BID_PARTICIPATION_CAP. Se admitió explícitamente junto con la policy anterior, conservando autoridad PAPER ARS, costos, participación, caja y frescura. Prueba sintética book → oferta → sweep → ledger PAPER real local y recuperación durable/idempotente.
4. La normalización de MAE podía convertir el prefijo cero de un valor no cero; la explicación STOP se duplicaba. Límites numéricos y traducción idempotente con regresiones, incluyendo puntuación final.

Se reconciliaron los solapamientos de rc6_contract_bridge.py y dos archivos de tests, conservando las defensas de todos los workstreams. No se incluyeron workflows one-off de los PR fuente en el candidato.

## Auditoría baseline anterior al deploy

Run 37076792108, SUCCESS; artifact 11256992640. Esta sección NO describe el candidato nuevo.

- PRODUCTION_PAPER; real_orders_sent=0; PPI auth OK; MARKET_CLOSED / WAITING_MARKET.
- Dashboard HTTP 200; dashboard/observer/critical_approval sobre imagen anterior 529ad27c55c30c7254a3fd42b4db67363cb8316528e6b886a04a55ed31839599.
- 8.077 candidatos READY y 4.066 PAUSED_EXPLICIT; había otros estados bloqueados distintos.
- Disco libre: 7.697.268.736 bytes.
- Observer tenía RestartCount=1 previo a esta revisión; causa NO_VERIFICADA. No atribuirlo al nuevo candidato.
- GDELT timer activo y servicio fallido: pendiente retiro del nuevo deploy.
- PPI Watch: ninguna unidad observada, sin cambios.
- Dos units legacy fallidas preservadas: porota-contract-evidence-weekend-backfill-rc6.service y porota-scheduler-export-hf6.service. El scheduler de reemplazo rc6 fue observado sano. No resetear fallos para fabricar salud.

## Errores del auditor y aprendizaje

- El primer baseline 37076570414 consultó family como columna de candidate_identity_v2. La columna canónica es instrument_type; family es alias de presentación. Se corrigió sin mutar DB y se agregó PRAGMA table_info + guard de esquema. Reejecución 37076792108 SUCCESS.
- La primera revisión auxiliar postdeploy esperaba nombres/campos de otro esquema de congelado. Se contrastó con el workflow exacto y se reemplazó por el validador canónico y fixtures negativos, verificando explícitamente schema_version=1. Primer run auxiliar 37078313521 queda superado, no constituye validación runtime.
- Fix del auditor postdeploy: commit 398b10eedbd7569fe4477782b2ee63e2153ce1f4, sólo rama audit. No cambió el candidato ni disparó otro deploy.
- El contenedor local de análisis dejó de ejecutar; se continuó con GitHub Actions. La inspección offline adicional que no se pudo ejecutar no se declaró realizada.

## Validación posterior prevista y límites

El auditor independiente espera acotadamente el SUCCESS del único Deploy V2. Luego valida el paquete con el contrato canónico y lee dos snapshots separados por 120 segundos, sin mutaciones:

- SHA candidato/productivo, frozen/manifest instalados idénticos, etiqueta de commit e imagen de los tres contenedores.
- Diferencia explícita entre ID de build y normalización del motor Docker, respaldada por TAR y hashes, nunca por simple inferencia.
- Hashes de doce módulos en dashboard y observer contra checkout exacto.
- Estado de procesos, OOM, restarts, heartbeat, modo y órdenes reales cero.
- Readiness por instrumento/familia desde candidate_identity_v2; catálogo desde financial_instrument_catalog.
- GDELT retirado, PPI Watch invariante, HTTP 200 y espacio.

Caución con book fresco y Scalping intradiario requieren una ventana de mercado válida. Las pruebas sintéticas no demuestran una operación en rueda. No se fabrican fills ni se alteran horarios, contratos, costos, profundidad o controles de riesgo para producir actividad.

## Seguridad

PAPER/SHADOW ONLY. PRODUCTION_PAPER / SIMULATION. real_orders_sent=0. Rutas reales bloqueadas. PPI Watch UNTOUCHED. FIX-FORWARD ONLY. Build once, sin reconstrucción en Droplet. Mutex rc6-unified-paper-deploy, cancel-in-progress=false.
