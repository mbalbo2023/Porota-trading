# A198 — fuentes, motores y recuperación de estrategias

Alcance: diez familias canónicas, tres categorías adicionales y Scalping. Base
de producto `541882bc0ceb0f3405ab71238a1b66a33ae10316`; candidato consolidado
[PR #772](https://github.com/mbalbo2023/Porota-trading/pull/772). Auditoría y
desarrollo PAPER/SHADOW; ninguna orden real, modificación de PPI Watch o
promoción de runtime forma parte de esta entrega.

## Regla de autoridad

PPI es la primera fuente de identidad, cotización, libro e histórico cuando
aporta datos suficientes, vigentes y compatibles con la identidad completa:
ticker, familia, mercado, moneda y liquidación. Un complemento sólo cubre lo
que falta. Una fuente secundaria rechazada por reloj, disponibilidad o calidad
no veta por sí sola un dato PPI válido; una contradicción admisible sigue
requiriendo revisión. El acuerdo entre fuentes no debe borrar la procedencia
PPI.

Esta prioridad no convierte una cotización en contrato legal: términos y pagos
del emisor, margen/clearing y reglamento/recibos del fondo conservan su autoridad
específica. El tarifario público PAPER no acredita costos efectivos de cuenta.
IOL no es requisito del scanner PPI ni del libro de cauciones colocadoras.

## Matriz de motores

“Implementado”, “probado con entradas controladas” y “observado funcionando en
runtime” son estados diferentes. Una identidad `can_simulate=1` no prueba
señal, riesgo, fill, salida ni rentabilidad.

| Familia | Fuente inicial y motor | Salida/registro | Evidencia y faltante |
|---|---|---|---|
| ACCIONES | Catálogo/current/book PPI; scanner → `PaperBroker` momentum causal | Supervisor de stop/objetivo/EOD; posiciones y fills spot | Circuito probado; 121 posiciones cerradas históricas y una cancelada. Admisión actual exige datos y gates vigentes |
| CEDEARS | PPI local; motor spot con sesión del subyacente | Supervisor/ledger spot | 13 posiciones cerradas históricas; no equivale a acciones USA directas |
| ETFS | PPI ETF local; ruta spot | Supervisor/ledger spot | Sin identidad ETFS separada en el extracto; costos genéricos bloquean componentes desconocidos. No trasladar mínimos/fees extranjeros |
| BONOS | PPI identidad/libro; motor específico FI | Requiere eventos/nominal/caja coherentes | Productor revisado desarrollado; faltan evidencia económica/OOS real y conexión del ledger de eventos al cierre y exposición |
| LETRAS | PPI identidad/libro; FI descuento/vencimiento | Redención nominal especializada | El adaptador previo rechaza vencimiento nativo. No convertir el plazo de una letra en salida equity intradiaria |
| OBLIGACIONES | PPI ON → canon OBLIGACIONES; FI crédito/flujos | Cupones/amortizaciones/salida con nominal residual | Misma integración FI pendiente; analítica IOL no autentica los documentos del emisor |
| OPCIONES | PPI serie/libro/subyacente; estrategia long especializada | Supervisor/ledger spot con contrato y vencimiento | Candidato y entrada PAPER probados; sin posición observada en el extracto. No habilita opciones short |
| FUTUROS | PPI; ruta especializada DLR y margen | `supervise_futures`, ledger propio, variaciones/cierre | 214 identidades pausadas por contrato/margen. No inferir su actividad desde el ledger spot; no extender DLR a todo futuro |
| CAUCIONES | PPI libro BID; collector → cash sweep EOD | Ledger propio y liquidación por vencimiento | Seis vencidas históricas. Cinco ARS bloqueadas por cambio exacto de autoridad tarifaria; reloj de recepción y PESOS3 corregidos en candidato |
| FCI | PPI catálogo; estrategia/allocator/reconciler especializados | Suscripción → NAV → unidades → rescate → caja | Incompatibilidad BYMA/FCI corregida. Falta productor autenticado por clase y scheduler conectado a recibos reales; allocator sintético no admite runtime |
| INDICES | PPI/contexto y observación | Sin contrato negociable directo | Benchmark, no familia de órdenes. Para exposición hace falta un instrumento negociable identificado |
| ACCIONES_USA | Sondas PPI exteriores | Ejecutor exterior no acreditado | Fuera del scheduler canónico; requiere sesión, moneda, costos y contrato propios |
| FCI_EXTERIOR | Sondas PPI exteriores | Clase/NAV/rescate exterior no acreditados | No heredar contrato ni costos FCI ARS |
| SCALPING | PPI Intraday; worker → señal → admission → broker común | Supervisor compartido, límite temporal/EOD y fills spot | ACTIVE_PAPER observado, pero 2582 evaluaciones de la sesión fueron HOLD por datos/contratos de volumen; ver diagnóstico |

## Diagnóstico de producción

Tres Actions acotadas, de sólo lectura, sin consultas a proveedores ni órdenes:

- [40990729916](https://github.com/mbalbo2023/Porota-trading/actions/runs/40990729916): libro/catálogo y seguridad inicial.
- [40992555376](https://github.com/mbalbo2023/Porota-trading/actions/runs/40992555376): todas las familias, alias exactos, workers y claves de cambios contractuales.
- [40993210661](https://github.com/mbalbo2023/Porota-trading/actions/runs/40993210661): transición tarifaria exacta, sesión Scalping y configuración efectiva.

Último corte 2026-10-29 23:42 ART: PRODUCTION_PAPER, cero órdenes reales,
PPI_AUTH OK, mercado cerrado y heartbeats recientes. Scalping y lector de
salidas esperan mercado; supervisor activo. Es liveness, no GO de inversión.
El rearm fue emitido después del cierre; la lectura nocturna no encontró
preopen V2 válido. No se ejecutó el validador de admisión en este diagnóstico.
Los motivos históricos NO_GO no demuestran el estado del siguiente T-10.

Las 5067 identidades ON/OBLIGACIONES deduplicadas contienen 91 colisiones de
alias y 65 discrepancias de readiness. No se sumaron aliases ni se reescribió
la base para fabricar disponibilidad. Futuros, cauciones y fondos utilizan
ledgers especializados: la ausencia de filas spot no demuestra su actividad.

### Cauciones: causa y corrección

El collector comparaba una respuesta con el reloj anterior al request. El
incidente PESOS7 produjo FUTURE_BOOK aunque su timestamp era posterior al
inicio del lote y anterior a la recepción. La corrección toma un reloj por
respuesta/reintento. Conserva rechazo de futuro real, reloj regresivo y TTL.

Los cambios 36957–36961, de 27-Oct, sólo modificaron `fee_authority` en cinco
contratos ARS. El [commit causal](https://github.com/mbalbo2023/Porota-trading/commit/9f5a4de307a5493f1cb2c414cfbc2b1a8468efaa)
corrigió una afirmación indebida de derechos BYMA verificados y explicitó el
modelo público PAPER. El tercer diagnóstico comprobó ambos hashes completos,
los dos tokens exactos y la igualdad del resto económico. No se probó un
conflicto de tasas API/web en estas identidades. Los snapshots y cambios
originales deben conservarse; sólo la revisión de esta transición exacta
puede despejar ese bloqueo. Las futuras modificaciones materiales siguen
requiriendo revisión.

### Scalping: embudo observado

Sesión 29-Oct, muestra completa de 2582 evaluaciones, última a las 17:00 ART:

| Resultado | Cantidad |
|---|---:|
| PPI_VOLUME_CONTRACT_UNVERIFIED | 1272 |
| REJECTED_MUTABLE_CLOSED_POINTS | 857 |
| EMPTY_INTRADAY_PAYLOAD | 452 |
| INTRADAY_CAPABILITY_WARMUP_PENDING | 1 |

Todas fueron HOLD. Configuración observada: ACTIVE_PAPER, lotes de 24 cada
180 segundos, una posición máxima y 30 minutos de permanencia. No se alteraron
umbrales para crear operaciones. La documentación oficial REST publica
`date/price/volume`, pero ese esquema por sí solo no certifica que volume sea
incremental por intervalo, acumulado o revisable. Los estados CONFIRMED de
fechas anteriores no certifican la sesión actual.

Se reprodujo además un defecto de código: comparar cadenas hacía distintos
`100`, `100.0` y `1E+2`. Una prueba integral generaba 19 revisiones cerradas
falsas sólo por cambiar la serialización. Se corrige mediante comparación
Decimal exacta y finita, sin redondear cambios reales ni limpiar contadores.
Esto no demuestra que explique los 857 rechazos runtime: faltan sus pares de
payloads. Sin contrato de volumen sigue correspondiendo HOLD.

### Desarrollo FI entregado

`rc6_fixed_income_runtime.py` liga un manifiesto estático revisado a documentos
del emisor, validación independiente y dataset. Cada decisión usa la cotización
PPI nativa actual; no requiere volver a aprobar el manifiesto por cada tick.
La integración en `PaperStore` lee una sola vez y registra la causa exacta
de bloqueo.

`rc6_fixed_income_event_ledger.py` incorpora persistencia atómica y reintentos
idempotentes sobre el reducer existente: cupones, amortización parcial/final y
fixings. Exige identidad, documento y recibo revisados, exfecha/fecha de registro
y liquidación explícitas. Caja y nominal son proyecciones por moneda y fecha de
conocimiento. Todavía no se conectan al patrimonio, sizing o cierre del motor;
ese límite impide duplicar principal o presentar este módulo como FI operativa.

## Trabajo necesario para declarar operatividad

1. **FI:** integrar nominal residual, caja por moneda, entitlement y eventos
   con exposición, sizing, cierre y aprendizaje sin doble contabilización.
   Acreditar términos y OOS independiente reales. El modelo previo de carry
   exige un flujo dentro de un horizonte intradiario; no es una estrategia
   general de bonos y no cubre por sí mismo la redención de letras.
2. **FCI:** capturar clase, reglamento, cutoff, mínimos, costos y NAV con
   relojes efectivos/de publicación/recepción. Conectar un productor auténtico
   y el scheduler al presupuesto y a recibos de asignación/rescate. Un hash
   o etiqueta de origen no sustituye autenticación del productor.
3. **Scalping:** aportar contrato verificable del volumen o desarrollar una
   estrategia distinta validada que no dependa de esa semántica; investigar
   revisiones económicas reales y respuestas vacías. No borrar el control.
4. **Futuros/ETF/exteriores:** completar contrato y costos específicos donde
   falten; no heredar supuestos de una familia/mercado diferente.
5. **Candidato:** consolidar y verificar cambios, luego un único gate de
   artefacto exacto y promoción gobernada. Las pruebas locales/Actions de
   código no se etiquetan como validación runtime del candidato sin desplegar.

## Referencias

- Código: `bf_production_paper_observer.py`, `bv_paper_runtime.py`,
  `be_paper_engine.py`, `bm_exit_supervisor.py`, `cf_intraday_scalping.py`,
  `rc6_fixed_income_paper_strategy.py`, `rc6_fund_paper_allocator.py`,
  `rc6_paper_family_lifecycle.py`, `di_caucion_cash_sweep_runtime_hf6.py`.
- [API oficial PPI](https://itatppi.github.io/ppi-official-api-docs/api/documentacionRest/).
- [Información oficial FCI de PPI](https://portfoliopersonalsupport.zendesk.com/hc/es-419/articles/21407483801748-Supermercado-de-FCI-y-Fondos-del-Exterior): datos técnicos, factsheet y reglamento; existencia de información no prueba su ingestión en Porota.
- [Cauciones PPI](https://www.portfoliopersonal.com/Cotizaciones/Cauciones): la tabla pública diferida no reemplaza el libro fresco necesario para PAPER.
- Auditoría operativa: [#732](https://github.com/mbalbo2023/Porota-trading/issues/732) y [#485](https://github.com/mbalbo2023/Porota-trading/issues/485).
