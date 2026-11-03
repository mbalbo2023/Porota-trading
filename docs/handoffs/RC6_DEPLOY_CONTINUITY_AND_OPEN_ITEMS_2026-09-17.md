# Porota Trading RC6 — continuidad, deploy y pendientes

**Corte:** 2026-09-17 02:20 UTC  
**Repositorio:** `mbalbo2023/Porota-trading`  
**Rama canónica de deploy:** `deploy/rc6-pr69-isolated-20260915`  
**HEAD verificado:** `e91dc1c011197e95ca70a6a04741b8bc49666790`  
**Modo obligatorio:** `PRODUCTION_PAPER` / `SIMULATED`; `real_orders_sent=0`.

## Instrucción de recuperación

Antes de cambiar o desplegar:

1. leer este checkpoint y `AGENTS.md`;
2. recuperar el estado actual de la rama canónica, PRs y último run canónico;
3. clasificar cada tema como desplegado, implementado sin deploy, verificación programada o pendiente real;
4. no repetir ingestas, deploys ni PRs cerrados sin una regresión comprobable;
5. usar la skill `porota-rc6-deploy-continuity` para procedimientos de RC6.

## Cambios cerrados y desplegados — no relanzar

| Tema | Estado | Evidencia |
|---|---|---|
| Universo operativo e históricos nuevos sólo ACCIONES/CEDEARs | Verde | PR #75, PR #78; deploy #81 verde. |
| Trading/Motor y universo operativo | Verde | PR #75; rutas verificadas por deploy canónico. |
| Históricos/velas, macro y GDELT en SHADOW/OBSERVE_ONLY | Verde | PR #75; sin autoridad sobre decisiones ni órdenes. |
| Contract Evidence sin scraping/reintentos | Verde | PR #75; `SOURCE_UNAVAILABLE_BY_SCOPE` y runner en skip. |
| Validación compacta y Action 4 | Verde | PR #76/#77; bootstraps verdes en runs #80 y #86. |
| Retención de reportes fail-safe | Verde | PR #75; sin borrado sin consolidado verificado. |
| Scheduler histórico interno bloqueado | Verde | PR #79; deploy #86 prueba guard previo a subprocess, ausencia de startup/10:15/19:20. |
| Histórico masivo PPI | Verde/protegido | servicio inactivo/deshabilitado; no repetir. |
| Auditoría de velas | Verde | timer cada 10 minutos; `quick_check=ok`, 0 barras sucias. |
| Job PPI post-cierre | Verde/protegido | timer disabled y condición systemd antes de `ExecStart`. |

## Evidencia canónica de deploy

- Run #80: `35162918128` — éxito: `BOOTSTRAP_VALIDATION_PROJECTION=GREEN`, `BOOTSTRAP_ACTION4_AUDIT=GREEN`, `POST_STATE=ok|PRODUCTION_PAPER|0`, `RC6_UNIFIED_PAPER_DEPLOY=GREEN`.
- Run #81: `35164743485` — éxito: restricción de históricos al universo operativo.
- Run #86: `35168208911` — éxito: `INTERNAL_HISTORY_WRITES=DISABLED`, scheduler guard completo, Action 4/Validación verde, `POST_STATE=ok|PRODUCTION_PAPER|0`, cero rutas/órdenes reales.

Los runs #82–#85 fueron fallos de la verificación nueva del guard, no una regresión del runtime. El run #86 los reemplaza como evidencia final.

## Pendientes reales

| Estado | Pendiente | Dueño/entorno | Criterio de cierre |
|---|---|---|---|
| Amarillo | Probar avance de velas durante rueda | timer `porota-rc6-candle-inmarket-verify` | Cursor > `118740`, worker `RUNNING`, SQLite `quick_check=ok`. Programado 2026-09-17 11:30 ART. |
| Amarillo | Recomputar y publicar matriz actual READY_PAPER por instrumento | operación/datos | No reutilizar la matriz vieja `0/444`; partir de evidencia spot actual y exponer fecha/fuente. |
| Amarillo | Acumular evidencia comparativa SHADOW | rueda PAPER | Comparar baseline/shadow, MFE/MAE, EOD, Max Hold, TP y costos en varias ruedas. No es un deploy. |
| Amarillo | Consolidación semanal/mensual de reportes | reportes | Verificar artefacto consolidado y restauración antes de habilitar compresión. |
| Rojo externo | URL privada del tablero pre-rueda | hosting/TLS | Resolver timeout de certificado TLS. No se arregla con deploy RC6. |

## Restricciones no negociables

- PPI Watch es owner separado: no tocar su rama, secretos, runtime, lock ni sus procesos.
- No iniciar backfill masivo ni refresco diario full-history.
- Un recuperador post-cierre sólo puede existir con lock compartido con PPI Watch, checkpoint persistente, deduplicación canónica, idempotencia demostrada y ventana `last_stored_date - 5 días` a hoy.
- No usar `deploy.yml`, `main`, `testing`, PRs draft/superseded ni ramas de diagnóstico para deploy.
- Todo cambio mantiene PAPER; nunca habilitar rutas ni órdenes reales.
- Los workflows one-shot se usan sólo para una comprobación delimitada y se retiran/restauran tras capturar evidencia.

## Próxima acción exacta

Cuando termine la ventana de rueda, leer el journal de `porota-rc6-candle-inmarket-verify.service`. Si pasa, registrar evidencia y cerrar ese amarillo. Si falla, diagnosticar lectura/cursor sin reactivar históricos ni PPI post-cierre.
