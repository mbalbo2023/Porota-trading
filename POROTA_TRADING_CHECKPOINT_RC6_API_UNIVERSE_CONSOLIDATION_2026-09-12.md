# POROTA TRADING RC6 — API UNIVERSE CONSOLIDATION

Fecha: 2026-09-12
Wave: post-RC6 operacional, forward-only

## Invariantes
- VERSION `17.0.0-rc6`
- MODE `PRODUCTION_PAPER`
- EXECUTION `SIMULATED`
- REAL_ORDER_CAPABILITY `BLOCKED`
- real_orders_sent `0`
- REAL_ORDER_ROUTES `NOT_CALLED`
- CP1–CP14 permanecen GREEN/FROZEN

## Runtime observado durante esta wave
La base RC6 certificada fue `f8adec8a02b2f9f0ef2dffbee75458c958bf711e`.
Durante la auditoría read-only se observó que el host ya había avanzado a `eddcc29bc52eaf0b3d50f87dc851a48accb0fc8a`, hijo directo de `f8adec8`, commit `fix(rc6): correct PAPER scalping economics and prove learning`.

No se ejecutó rollback ni modificación de runtime desde esta wave. Recheck read-only workflow `34660920043`, job `103463059740`: SUCCESS.
- `/health`: `status=ok`, `version=17.0.0-rc6`
- observer: running, restart=0, image `porota-trading-bot:17.0.0-rc6`
- dashboard: healthy
- DB host presente y activa
- `docker exec` smoke: OK, Python 3.11.16

Los fallos iniciales de dos auditorías se explican por controles del propio diagnóstico, no por una caída productiva:
- `34660447904`: esperaba SHA exacto `f8adec8` cuando el host ya estaba en su descendiente `eddcc29`.
- `34660877133`: primera consulta usó `evidence_json` como si estuviera en `contract_evidence_v2_current`; el payload está en `contract_evidence_v2_snapshots` y debe unirse por `snapshot_id`.
Ambos RCA fueron corregidos sin tocar runtime.

## Autoridad de universo: PPI API productiva
Auditoría live autenticada productiva `34659647038` / `103459289644`: SUCCESS.

InstrumentTypes declarados por PPI producción (15):
`ACCIONES`, `ACCIONES-USA`, `BONOS`, `CAUCIONES`, `CEDEARS`, `ETF`, `FCI`, `FCI-EXTERIOR`, `FUTUROS`, `LEBAC`, `LETRAS`, `LICITACIONES`, `NOBAC`, `ON`, `OPCIONES`.

Markets: `BYMA`, `NASDAQ`, `NYSE`, `OTC`, `ROFEX`.
Settlements: `INMEDIATA`, `A-24HS`, `A-48HS`, `A-72HS`.

Regla canónica de esta wave:
`API_DECLARED -> API_DISCOVERED -> CONTRACT_ENRICHED -> HISTORY_READY -> SIMULATOR/SIZING/EXIT_READY -> PAPER_CANDIDATE`.

Web/XHR no crea universo operativo por sí solo. `INDICES`, `MONEDAS`, `TASAS` quedan `CONTEXT_ONLY` mientras no sean InstrumentTypes de la API productiva.

## Catálogo API ya observado
Workflow `34659762851` / `103459630927`: SUCCESS.
- ACCIONES: 55
- BONOS: 42
- CAUCIONES: 10
- CEDEARS: 191
- FUTUROS: 52
- LETRAS: 23
- ON: 91
- OPCIONES: 382
Total identidades materializadas en esas familias: 846.

Pendientes declarados por API sin filas actuales de `instrument_catalog`: `ACCIONES-USA`, `ETF`, `FCI`, `FCI-EXTERIOR`, `LEBAC`, `LICITACIONES`, `NOBAC`.

## Evidencia existente para familias pendientes
Auditoría corregida `34661020272` / `103463358860`: SUCCESS, read-only, safety `ok|PRODUCTION_PAPER|MARKET_CLOSED|0`.

- ACCIONES-USA: sólo wildcard web/XHR; sin identidad concreta.
- ETF: sólo wildcard web/XHR; sin identidad concreta en Contract v2.
- FCI: wildcard web; sin identidad concreta.
- FCI-EXTERIOR: wildcard web/XHR; sin identidad concreta.
- LICITACIONES: identidad explícita `TECPE10`, evidencia XHR con `instrument_id=928820` y moneda USD/CCL; además wildcard web/XHR.
- LEBAC: sin evidencia current.
- NOBAC: sin evidencia current.

Esto confirma que Contract Evidence no debe ser usado como sustituto de discovery API: salvo `TECPE10`, las familias pendientes todavía necesitan identidad API concreta.

## Auditoría en curso
Workflow `34661074786` / job `103463521230`: directed discovery read-only contra PPI producción.
Objetivo: probar candidatos de `ACCIONES-USA`, `ETF`, `LICITACIONES`, `LEBAC`, `NOBAC`, `FCI`, `FCI-EXTERIOR` con `SearchInstrument`, una sola autenticación, únicamente GETs de mercado/configuración y cero rutas de órdenes.

## Hallazgo arquitectónico del runtime
`bf_production_paper_observer.py::_download_catalog()` sólo consulta lo producido por `_candidate_universe()`.
El runtime actual tiene seeds para `ETF` y `FCI`, pero no para `ACCIONES-USA`, `FCI-EXTERIOR`, `LICITACIONES`, `LEBAC`, `NOBAC`. Además los seeds normales se emiten con market `BYMA`, lo cual no alcanza para familias como `ACCIONES-USA` que requieren discovery por `NASDAQ/NYSE/OTC` según corresponda.

Próximo forward-fix: introducir discovery dirigido por familia+mercado validado por Configuration PPI, sin habilitar PAPER automáticamente, y persistir sólo respuestas reales de `SearchInstrument`.
