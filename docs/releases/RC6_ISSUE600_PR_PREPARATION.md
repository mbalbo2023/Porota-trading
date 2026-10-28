# PR preparado — RC6 historia local, SQL/SHADOW y verdad del dashboard

Base productiva revalidada fa751eb1f985b55f6aeb6729704452150c93c5e4.
Head work/rc6-20261016-postclose-consolidated; abrir DRAFT postcierre.
La sonda38959937247/artifact12072197786 acredita CLOSED/observer MARKET_CLOSED,
PAPER0 y0contratos abiertos. Inflight evaluación/fill permanece NO_VERIFICADO.
Predeploy es aislado; ninguna promoción/Deploy hasta nueva orden explícita.

## Cambio

SQL SHADOW: empaquetar lecturas grandes y transformar fuera del snapshot sin
aumentar plazo2s. Publicar/leer CURRENT con root, manifest/digests y revocación
atómica. Conservar embudo bounded por moneda/canal. Scanner: BUSY/LOCKED de
telemetría opcional se difiere explícitamente sin terminar el proceso; otros
errores propagan y no cambian broker/heartbeat.

Historia local EOD sampled/PARTIAL independiente de bootstrap externo, cold
separado de HOT2s, revalidación PAPER/CLOSED, reserve y commit idempotente.
Repair fullkey7, freshness de últimas sesiones; no inferir cobertura/OHLCV.

Dashboard: semáforos por fuente/dato/calidad/efecto, cadencia real scalping y
SHADOW; restores bounded de3contrafactuales/anual y aliases; cohorte exacta
de quotes/última sesión histórica; manifest105rutas/103paths/52canónicas.

## Validación y límites

223PASS final scoped EOD/dashboard/route/night guards;33PASS scanner lock real,
heartbeat, TP y regresión causal EOD/SQL.43contratos monitor en PR601 separado,
CI GREEN. Compile/secret/dependency verificados; Predeploy exacto pendiente.
Los full locales RED y4SKIProot se conservan; no modificar guard0.15s de
capacidad ni omitir pruebas. Runner nativo debe acreditar0fallos/0skips/0xfail.

SQL crítico permanece abierto hasta validar avance real de2generaciones y
ausencia de nuevas interrupciones tras Deploy V2 autorizado. Visual tablet,
cadencia cron auténtica y aceptación de EOD local no se acreditan por CI.
No hubo runtime mutation, merge, one-shot externo ni acceso a PPI Watch.
PAPER/SHADOW ONLY; real_orders_sent=0; FIX-FORWARD ONLY, sin rollback.
Matriz RCA/FIX/GUARD/TEST: RC6_ISSUE600_20261016.md y JSON adyacente.
Refs #600/#599; control-plane main aislado en PR601DRAFT.
