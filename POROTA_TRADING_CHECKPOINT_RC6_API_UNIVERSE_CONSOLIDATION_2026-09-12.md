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

Los fallos iniciales de auditoría se explican por controles del propio diagnóstico, no por una caída productiva:
- `34660447904`: esperaba SHA exacto `f8adec8` cuando el host ya estaba en su descendiente `eddcc29`.
- `34660877133`: mismo cambio de SHA.
- primer intento de pending-family audit: consultó `evidence_json` en `contract_evidence_v2_current`; el payload está en `contract_evidence_v2_snapshots`, join por `snapshot_id`.
Los RCA fueron corregidos sin tocar runtime.

## Autoridad de universo: PPI API productiva
Auditoría live autenticada productiva `34659647038` / `103459289644`: SUCCESS.

InstrumentTypes declarados por PPI producción (15):
`ACCIONES`, `ACCIONES-USA`, `BONOS`, `CAUCIONES`, `CEDEARS`, `ETF`, `FCI`, `FCI-EXTERIOR`, `FUTUROS`, `LEBAC`, `LETRAS`, `LICITACIONES`, `NOBAC`, `ON`, `OPCIONES`.

Markets: `BYMA`, `NASDAQ`, `NYSE`, `OTC`, `ROFEX`.
Settlements: `INMEDIATA`, `A-24HS`, `A-48HS`, `A-72HS`.

Regla canónica:
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
Total materializado en esas familias: 846 identidades.

## Evidencia existente para familias pendientes
Workflow `34661020272` / `103463358860`: SUCCESS, read-only, safety `ok|PRODUCTION_PAPER|MARKET_CLOSED|0`.
- ACCIONES-USA: wildcard web/XHR, sin identidad concreta en Contract v2.
- ETF: wildcard web/XHR.
- FCI: wildcard web.
- FCI-EXTERIOR: wildcard web/XHR.
- LICITACIONES: `TECPE10`, XHR `instrument_id=928820`, moneda USD/CCL, más wildcard web/XHR.
- LEBAC: sin evidencia current.
- NOBAC: sin evidencia current.

## Directed discovery contra API productiva
Workflow `34661074786`, job `103463521230`: SUCCESS.
Safety pre/final `ok|PRODUCTION_PAPER|MARKET_CLOSED|0`; HTTP blocked=0; REAL_ORDER_ROUTES=NOT_CALLED; MUTATIONS=NONE.

Resultados confirmados por `SearchInstrument`:
- `ACCIONES-USA/AAPL/NYSE` -> 1: Apple, USD/CCL.
- `ACCIONES-USA/MSFT/NASDAQ` -> 1: Microsoft, USD/CCL.
- `ACCIONES-USA/NVDA/NASDAQ` -> 1: NVIDIA, USD/CCL.
- `ETF/SPY/NYSE` -> 1: SPDR S&P 500 ETF, USD/CCL.
- `ETF/IWM/NYSE` -> 1: iShares Russell 2000 Index Fund, USD/CCL.

Consultas válidas que devolvieron 0 en el probe actual:
- AAPL/NASDAQ y TSLA/NASDAQ para ACCIONES-USA.
- SPY/NASDAQ, QQQ/NASDAQ, DIA/NYSE para ETF.
- LICITACIONES `TECPE10`/BYMA.
- LEBAC `LEBAC`/BYMA.
- NOBAC `NOBAC`/BYMA.
- FCI `FONDO`/BYMA, `FONDO`/OTC, `FCI`/BYMA.
- FCI-EXTERIOR `FONDO`/OTC, `FONDO`/BYMA, `FCI`/OTC.

Interpretación fail-closed:
- Resultado 1 confirma identidad API concreta.
- Resultado 0 sólo invalida ese filtro exacto; NO demuestra que la familia no tenga instrumentos.
- `TECPE10` existe en XHR de la web pero no fue descubierto por `SearchInstrument` con ese filtro; LICITACIONES puede requerir semántica/ruta distinta de discovery o la evidencia puede corresponder a una ventana de licitación ya no activa.
- FCI/FCI-EXTERIOR necesitan nombres/códigos reales antes de repetir API discovery; no corresponde inventar seeds.

## Hallazgo arquitectónico del runtime
`bf_production_paper_observer.py::_download_catalog()` sólo consulta lo producido por `_candidate_universe()`.
El runtime actual tiene seeds para `ETF` y `FCI`, pero no para `ACCIONES-USA`, `FCI-EXTERIOR`, `LICITACIONES`, `LEBAC`, `NOBAC`. Además los seeds estándar se emiten con market `BYMA`, insuficiente para `ACCIONES-USA` y ETF USA confirmados por NYSE/NASDAQ.

## Siguiente wave
1. Formalizar seeds API por `familia+ticker/name+market`, nunca sólo por familia.
2. Incorporar al universo observado las identidades confirmadas AAPL/MSFT/NVDA y SPY/IWM, sin habilitar PAPER automáticamente.
3. Hacer sweep de mercado para los tickers con filtro inicial 0 antes de descartarlos.
4. Extraer desde evidencia PPI web/XHR nombres/códigos explícitos para FCI/FCI-EXTERIOR y luego validarlos por API.
5. Tratar LICITACIONES como familia API soportada pero con discovery específico de evento/auction; no usar wildcard como identidad.
6. LEBAC/NOBAC permanecen `API_DECLARED_NOT_DISCOVERED` hasta evidencia concreta.
7. Binding/readiness de ejecución sólo sobre `API_DISCOVERED` y familia integrada; `CONTEXT_ONLY` jamás bloquea operación.
