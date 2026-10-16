# PR preparado — RC6 historia local, recuperación SHADOW y verdad del dashboard

Base: deploy/rc6-pr69-isolated-20260915 @fa751eb1f985b55f6aeb6729704452150c93c5e4.
Head: work/rc6-20261016-postclose-consolidated. Abrir DRAFT solo después de cierre
real/freeze; opened/synchronize dispara Predeploy V2. Esta preparación no es PR
abierto ni artefacto validado. No promover hasta nueva orden del operador.

## Cuerpo para revisión

El SQL de SHADOW agotaba el plazo2s al materializar miles de filas Python;
además el dashboard requería CURRENT/lector/root que el worker no publicaba.
Se empaquetan lecturas SQL grandes dentro del mismo límite, se transforman
fuera del snapshot y se publican generaciones coherentes con manifest/digests
y revocación tras fallo. Se preserva baseline PAPER, límites y rutas reales.

La historia local EOD consolida muestras persistidas de forma idempotente en
cold, sin depender de bootstrap externo. Mantiene SAMPLED/PARTIAL, faltantes,
provenance y clocks reales; no fabrica OHLCV completo. Repair distingue moneda,
mercado, liquidación y bases; features históricas exigen sesiones esperadas.

El dashboard separa chequeo/dato/calidad/efecto y cadencia real de workers;
restaura contrafácticos y análisis anual con lectores acotados. Los netos de
modelos censurados no se publican como cierres. Cohorte de quotes por identidad
separa book/trade/receipt; MAX(date) histórico se contrasta con calendario.

Validación local:288PASS focalizados,0 fallos/omisiones;137PASS de dashboard,
SQL replay y regresión causal. Compile/secret/dependency GREEN. Conservar
resultado RED de suite completa y fallo intermitente de admisión de capacidad:
el gate nativo completo sigue pendiente, no se alteró el guard para aprobarlo.

Riesgos y aceptación: unidades EOD aún no instaladas, semántica de visualización
sin QA tablet, cadencia schedule genuina sin certificar. Verificar OCI único,
PAPER0, DB, generaciones reales, SQL sin interrupción, embudo y progreso EOD
tras el único Deploy V2 permitido. No afirmar OPEN acceptance desde CLOSED.

PAPER/SHADOW ONLY; real_orders_sent=0; real_routes=NOT_CALLED;
PPI Watch no accedido; FIX-FORWARD ONLY. Sin merge/runtime mutation durante rueda.
Matriz RCA/FIX/GUARD y evidencia: RC6_ISSUE600_20261016.md y JSON adyacente.
Ref #600 y adendas verificadas #599. Control-plane main aislado en PR601.
