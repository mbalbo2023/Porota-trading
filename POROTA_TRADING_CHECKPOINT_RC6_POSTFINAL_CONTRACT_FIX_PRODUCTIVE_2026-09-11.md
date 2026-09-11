# POROTA TRADING RC6 — POSTFINAL CONTRACT EVIDENCE FIX PRODUCTIVE

Fecha: 2026-09-11
Estado: GREEN / PRODUCTIVO

## Invariantes preservados
- Runtime Git SHA en host: `f8adec8a02b2f9f0ef2dffbee75458c958bf711e` (sin checkout ni rollback).
- Health: `17.0.0-rc6` / `status=ok`.
- Modo: `PRODUCTION_PAPER`.
- Sesión al validar: `MARKET_CLOSED`.
- `real_orders_sent=0`.
- Rutas reales de órdenes: NO llamadas.
- `pragma quick_check=ok`.

## RCA corregido
El wrapper DOM anterior ejecutaba el navegador pesado en cada wake del timer aunque el colector base devolviera `GREEN_NOT_DUE`. Además, el colector esperaba sólo 1.5 s y buscaba tablas HTML literales, lo que producía pases intermitentes con 0 tablas materializables y exit 6 del importer.

## Forward-fix instalado
Branch de control: `fix/rc6-postfinal-contract-dom-stability-20260911`.

Cambios:
1. Gate de cadencia por familia usando la frescura persistida de `PPI_AUTHENTICATED_WEB`:
   - CAUCIONES: 300 s.
   - LICITACIONES: 300 s.
   - FUTUROS: 900 s.
   - BONOS: 900 s.
   - OPCIONES: 900 s.
   Si ninguna ruta está vencida, el navegador no inicia y se emite `STATUS=GREEN_DOM_NOT_DUE`.
2. Espera de render acotada y progresiva; un único retry acotado por ruta.
3. Soporte explícito de HTML tables y ARIA `grid/table/treegrid`, únicamente si existen headers explícitos; no se infieren semánticas ni orden de columnas.
4. Importer acepta schema V1/V2 y un pase autenticado sin tabla se clasifica `YELLOW_NO_TABLE_THIS_PASS` en lugar de crash.
5. El wrapper sólo tolera ese amarillo si ya existe evidencia PPI web fresca <= 1800 s; si queda stale, falla cerrado.
6. Se mantienen `SAFE_METHODS={GET,HEAD,OPTIONS}` y bloqueo de mutaciones.

## Deploy y prueba productiva
Workflow run: `34656912956`
Job: `103451212247`
Conclusión: `success`.

Secuencia productiva validada:
- Esperó a que finalizara el Contract run que ya estaba activo, evitando colisión.
- Instaló la extensión aislada bajo `/usr/local/lib/porota-contract-evidence-rc6` y `/usr/local/sbin/porota-contract-evidence-rc6-with-dom.sh`.
- Wiring efectivo de systemd confirmado hacia el wrapper corregido.
- Timer `porota-contract-evidence-rc6.timer`: `active` y `enabled`.
- Se ejecutó una corrida coordinada post-fix.

Resultado de la corrida post-fix:
- Colector base: `STATUS=GREEN_NOT_DUE`, `AUTH_BROWSER_STARTED=NO`, `PPI_CALLS=0`, `REAL_ORDERS_SENT=0`.
- Gate DOM detectó únicamente `/Cotizaciones/Cauciones` como vencida; no lanzó las cinco rutas indiscriminadamente.
- Auth DOM: `AUTHENTICATED_TRUSTED_DEVICE`.
- `blocked_nonread=0`.
- `materializable_tables=1`.
- Importer: `state=GREEN`, `records=1`, familia `CAUCIONES`.
- Snapshot nuevo: `snapshot_id=1501`, estado `CHANGED_REVIEW_REQUIRED`.
- Persistencia Contract web: `web_rows=120`.
- `web_max_observed=2026-09-11T23:13:37.044787+00:00`.
- Familias web persistidas: ACCIONES, ACCIONES_USA, BONOS, CAUCIONES, CEDEARS, ETF, FCI, FCI_EXTERIOR, FUTUROS, INDICES, LETRAS, LICITACIONES, MONEDAS, ON, OPCIONES, TASAS.
- Conteos finales: `contract_evidence_v2_snapshots=972`, `contract_evidence_v2_changes=1460`.
- Servicio: `result=success`, `ExecMainStatus=0`.
- Prueba final: `CONTRACT_POSTFINAL_SAFETY=GREEN`, `FORWARD_FIX=GREEN`, `RUNTIME_GIT_SHA_UNCHANGED=YES`, `ROLLBACK=NOT_USED`, `REAL_ORDER_ROUTES=NOT_CALLED`.

## Veredicto
El fix está PRODUCTIVO en el host y validado con una corrida real read-only. Se corrigió el problema de cadencia que disparaba Chrome en exceso y se reforzó la materialización frente a render dinámico sin relajar el fail-closed ni la seguridad de trading. El runtime funcional RC6 permanece congelado en `f8adec8...`; el cambio es un overlay operativo postfinal aislado.

No reabrir CP1–CP14 por este cambio. Cualquier observación futura de Contract debe tratarse como wave operacional post-RC6 y revalidar únicamente este subsistema.