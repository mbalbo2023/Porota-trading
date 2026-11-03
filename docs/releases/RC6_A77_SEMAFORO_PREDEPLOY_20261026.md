# RC6 A77 — semáforo previo a Predeploy

Corte base: 2026-10-26 00:06 UTC / 2026-10-25 21:06 ART, domingo `MARKET_CLOSED`.
Revalidación A84: 2026-10-26 03:05 UTC / 26/10 00:05 ART antes de la rueda; los recuentos
de host siguen siendo el corte CLOSED citado y no describen una rueda nueva.
Producto: `deploy/rc6-pr69-isolated-20260915` en `b183a9e8b9b9028d647390c006a8512cb0dc9f52`.
Candidato de historia: [PR #743](https://github.com/mbalbo2023/Porota-trading/pull/743), borrador sobre `69edeba2ae74ab71b08ac82e3c9b05a266bd08cc`, sin merge ni deploy.
Candidato de familias: [PR #744](https://github.com/mbalbo2023/Porota-trading/pull/744),
borrador `a473d5f62d2d7cee8c9f0b4da276058fd5c637bc`, tampoco instalado.
El operador autorizó posteriormente trabajo y promoción autónomos, siempre
con gates exactos. El [run 40247235056](https://github.com/mbalbo2023/Porota-trading/actions/runs/40247235056)
se canceló antes de construir imagen o artefacto; no es certificado.

## Regla de lectura

- `VERDE`: evidencia de ese contrato y ese corte. No equivale a GO integral.
- `AMARILLO`: opera un subconjunto o hay degradación medida sin autorización para ampliar.
- `ROJO`: bloqueo concreto reproducido; el componente no cumple el criterio de salida.
- `NO_VERIFICADO`: hace falta una rueda OPEN auténtica, ensayo separado o evidencia que aún no existe.
- El semáforo integral permanece **NO_GO** mientras una puerta obligatoria esté ROJA o NO_VERIFICADA. No se inventan señales/fills ni se relajan seguridad y contratos.

## Puertas antes de pedir autorización de Predeploy

| Componente | Estado | Evidencia actual | Criterio para pasar |
|---|---|---|---|
| Identidad de release y ownership | AMARILLO | Producto `b183a9e8`; scope A77 exclusivo declarado en #28/#679; PR #743 borrador. No hay artefacto del candidato. | Releer HEAD, PRs/owners/mutex; congelar SHA/tree y resolver cualquier colisión antes de Predeploy. |
| Seguridad PAPER | VERDE en CLOSED | [Sonda A77](https://github.com/mbalbo2023/Porota-trading/actions/runs/40247416855): `PRODUCTION_PAPER`, `MARKET_CLOSED`, enteros 0 en observer y scalping; rutas reales no invocadas. | Repetir justo antes y después de cualquier promoción; PPI Watch sin acceso ni cambio. Nunca órdenes reales. |
| Preapertura | NO_VERIFICADO lunes | Timer 09:45/10:20 habilitado/activo, observer running y 0 reinicios; timer 10:15 adicional no encontrado. Recibo GO del lunes aún no puede existir. | T−45 y T−10 genuinos con checks GREEN; recibo `GO_PAPER_SCALPING` sólo si ocho contratos exactos, release y estado PAPER son válidos. Un NO_GO bloquea nuevas entradas simuladas. |
| Scalping worker | AMARILLO | [Sonda A77](https://github.com/mbalbo2023/Porota-trading/actions/runs/40247416855): `ACTIVE_PAPER`, pulso 0,3 s, `WAITING_MARKET`, fallas 0, foco exacto 8/8, 1.445 identidades spot elegibles, último candidato viernes 23/10. Cadencia 180 s y lote real 24; la rotación total con relectura pareada puede exceder una rueda, aunque el foco tiene prioridad. | En OPEN: `RUNNING`, pulso fresco, PPI intraday autenticado, puntos de sesión confirmados, presupuesto/latencia, foco y cursor con progreso, HOLD legítimo o candidatos reales, cero órdenes. Medir capacidad antes de aumentar 24→40. |
| SHADOW e integridad de generación | VERDE sólo en CLOSED; OPEN NO_VERIFICADO | [Corte A77](https://github.com/mbalbo2023/Porota-trading/actions/runs/40247582848): manifiesto íntegro, secuencia `1792972309123448`, `SHADOW_OBSERVING` CLOSED, `idle` con `source_freshness_renewed=false`, 0 errores de fuente y 0 errores PPI del corte. Su `catalog_ready_count=9983` corresponde al catálogo READY_PAPER; el ledger PAPER exacto da 9.923 simulables en [otra autoridad](https://github.com/mbalbo2023/Porota-trading/actions/runs/40247690381). | Dos o más generaciones OPEN nuevas, distintas, coherentes, frescas (<120 s), SQL/transform <=2 s, publicación completa, sin revocación, OOM ni reinicio. Comparar mismo universo y tiempo al conciliar números. |
| Fuentes PPI/BYMA/IOL | AMARILLO | PPI auth `OK`, BYMA calendario/open data/web VERDE. Catálogo PPI AMARILLO; historia PPI AMARILLO con último intento 25/09. Cache IOL y referencia del viernes, no frescura de lunes. | Confirmar cada fuente y fallback con `source_at/received_at/known_at`, moneda, libro y tramo frescos en OPEN; indisponibilidad no se convierte en cero ni GO. |
| Ingesta e intraday | AMARILLO | Contratos intraday: 108 `CONFIRMED_INTERVAL_VOLUME`, 1423 `PENDING_LIVE_CONFIRMATION`, 503 unidad no verificada, 338 vacíos, 43 revisión rechazada, 16 instrumento no encontrado, 1 reprobe fallido; máximo `last_source_at=23/10 19:59Z`, propio de cierre de viernes. | Sesión lunes: revisar progreso por identidad, payload PPI, reloj, continuidad/revisión, unidad de volumen y frescura; rechazar datos contradictorios sin borrar evidencia. |
| Histórico SQLite V2 | ROJO | [Inventario A77](https://github.com/mbalbo2023/Porota-trading/actions/runs/40247416855): DB 821.477.376 B, 596.196 versiones y 439.833 canónicas legadas; **0** versiones con moneda explícita en metadata. Hay 9.352.527.872 B libres, pero copia privada normal tiene cap 512 MiB. Catálogo actual: 10.445 claves de moneda única y 170 ambiguas; no prueba mapeo de cada fila histórica. | Migración en copia consistente con mapeo exacto revisado, cuarentena de ambigüedades, PIT/revisiones, hashes/quick_check, sin sobrescribir fuente ni reingesta anual. Validar consumo sobre V2 y costo/recursos. |
| Recuperación PPI | ROJO | Corte 21/09 `PARTIAL`, 899/1094 completos y 195 fallidos; job actual en cuarentena. [PR #743](https://github.com/mbalbo2023/Porota-trading/pull/743) prepara guardas y lotes incrementales de hasta 30 días sobre series con baseline; aún no instalado. | Conciliar 195 claves sin etiquetar completo por fiat, migrar V2, demostrar incremento con candados compartidos y cero descarga anual. Sólo entonces revisar habilitación de un pase a 18:45 ART. |
| A3 histórico | ROJO | Tres timers masked/inactive. Catálogo CEM alinea 65 futuros locales pausados; ninguna identidad A3 está simulable. Las 1.329 opciones PAPER BYMA no son el mismo contrato A3. [Muestra](https://github.com/mbalbo2023/Porota-trading/actions/runs/40246606278): 3/5 velas válidas, sin escritura. | V2 migrado; cohorte A3 con símbolo/contrato/moneda/plazo y semántica de precio validados. Histórico de futuros pausados puede ser investigación sin promocionarlos a PAPER. Probar un lote y checkpoint real antes de activar daily/reconcile/weekend. |
| Retención SQLite 365 días | ROJO | No hay política implementada y ensayada con pins/excepciones; tamaño del archivo no mide por sí solo desperdicio. | Inventario por tabla/índice/refs, copia con conservación de evidencias y PIT, ahorro físico medido, integridad e idempotencia antes de purga. |
| Timers/workers y solapamiento | NO_VERIFICADO OPEN | Cuatro timers históricos masked/inactive; preopen único 09:45/10:20; worker scalping espera mercado. Inventario A73: 16 timers RC6 activos tras Deploy, sujeto a revalidación. | Rueda real: mostrar `LastTrigger/NextTrigger`, duración/CPU y salida de cada job, locks, bloqueos SQLite y progreso de nueve workers. Demostrar exclusión A3/PPI/corte y que fuera de rueda sólo corren tareas con finalidad. |
| Contratos, selección, riesgo y fills por familia | ROJO para cobertura integral | [A78](https://github.com/mbalbo2023/Porota-trading/actions/runs/40249154637): 122 BUY de ACCIONES y 13 de CEDEARS acumulados, diez enlaces exactos recientes (9/1); seis cauciones PAPER con ciclo propio; cero BUY de las otras ocho familias. #744 A83 une flujos IOL AL30/YMCJO sólo SHADOW, sin tesis validada. | Matriz por clave de cinco campos y por familia: contrato, fuente/volumen, señal genuina o HOLD, riesgo/cash, gate global, fill simulado/ledger/salida. Sin orden real. |
| OOS, costos y P&L | NO_VERIFICADO | Sin OOS sellado PIT, walk-forward y costes/slippage reales certificados para las diez familias. | Datasets versionados, holdout, costes, drawdown y rechazo de leakage; no extrapolar rentabilidad. |
| UI 52 vistas y voz/tablet | NO_VERIFICADO | HTTP previo y HTML no equivalen a QA autenticada, modal, filas, scroll y voz en imagen instalada. | Recorrido visual/de voz en entorno real con evidencia saneada y cobertura 52/52. |
| Alertas externas | NO_VERIFICADO | ACK Telegram externo de la rueda no medido en este corte. | Emisión, entrega/ACK, duplicados y límites de escalamiento medidos sin mensajes de prueba intrusivos. |
| Recursos host | AMARILLO | Volumen histórico con 9,35 GB libres; observer sin reinicios. RAM libre real, esperas, IO/locks y contención durante OPEN no medidos en este corte. | Piso de disco 2 GiB y reserva RAM 128 MiB, límites 512 MiB/0,75 CPU, cero OOM y latencia sostenida bajo rueda real, sin estrés E09/E10. |
| Política PPI Watch | NO_VERIFICADO en nueva sonda | Las sondas A77 no lo accedieron ni lo modificaron; recibo de Deploy A73 decía `UNTOUCHED`. | Conciliar unidades/estado antes y después de cambios autorizados, sin tocar su código, datos ni timers. |

## Diez familias: evidencia por identidad, no promesa de operatividad global

Fuente: [conteo de ledger y catálogo A77](https://github.com/mbalbo2023/Porota-trading/actions/runs/40247582848) y [causas de pausa](https://github.com/mbalbo2023/Porota-trading/actions/runs/40247690381). Las cantidades son identidades del corte, no órdenes, señales ni rellenos. `ON` es alias de obligaciones y se presenta en la misma fila.

| Familia | Simulables exactas en ledger | Bloqueo o límite conocido | Estado antes de OPEN |
|---|---:|---|---|
| ACCIONES | 167 | Otras 10 observadas y 25 stale; fill por instrumento pendiente. | AMARILLO |
| CEDEARS | 1278 | 19 disponibles sin readiness, 765 observados y 403 stale; subyacente/horario por identidad. | AMARILLO |
| ETFS | 0 | Sin catálogo en el corte SHADOW. | ROJO |
| BONOS | 2429 | 13 disponibles sin readiness, 447 observados y 144 stale; liquidación/riesgo/fill no acreditados. | AMARILLO |
| LETRAS | 34 | 4 disponibles sin readiness, 9 observadas y 9 stale. | AMARILLO |
| OBLIGACIONES / ON | 3624 | 1401 contrato no resuelto, 26 OBLIGACIONES y 91 ON ambiguas; 91 ON no simulables. | ROJO parcial |
| OPCIONES | 1329 | 1719 contratos no resueltos; A3 CEM no prueba equivalencia BYMA. | ROJO parcial |
| FUTUROS | 0 | 207 contrato/margen no resueltos y `PAUSED_EXPLICIT`; 65 alineaciones CEM son historia de investigación. | ROJO |
| CAUCIONES | 5 | Otras 5 disponibles sin readiness; adjudicación real PAPER no observada. | AMARILLO |
| FCI | 1057 | 3 disponibles sin readiness, 21 observados y 1 stale; estrategia/fill no acreditados. | AMARILLO |

SHADOW añade INDICES (1 stale), fuera de estas diez familias operativas; no inventar una undécima capacidad. `entry_authority=false` en las once filas SHADOW es el contrato de observación y no un indicador de órdenes PAPER. El ledger PAPER y el gate de preapertura son autoridades distintas.

## Orden de cierre

1. Conciliar #743 y #744 Draft, producto, ownership y todos los rojos en un candidato único. La autorización del operador ya existe; la cancelación previa sigue siendo recibo rojo/no certificado, nunca reutilizable. Predeploy sólo al cumplir sus gates y tras verificar scalping/preopen sin extrapolar del domingo.
2. Corregir por scopes separados los rojos verificables: migración exacta V2, 195 casos PPI, A3 con cohorte válida, retención y contratos pausados. No modificar PPI Watch ni habilitar los cuatro timers por estado de systemd solamente.
3. En la rueda auténtica del lunes, leer T−45/T−10/OPEN y postcierre sin estrés E09/E10: ver scalping, SHADOW, ingesta/contratos, workers, fuentes, locks y trazas por familia. Un día sin señal válida queda `NO_VERIFICADO` para fills, no FAIL inventado ni GO.
4. Reconciliar este semáforo con #732, #485 y los issues dueños. Congelar SHA/tree, ejecutar Predeploy V2 exacto y verificar el artefacto si el candidato y la rueda satisfacen las puertas. Deploy V2 exige además los recibos literales posteriores de #624, merge de dos padres, árbol idéntico, ownership global y prueba runtime. Un permiso general anterior no falsifica el recibo exacto exigido por código.

## Autoauditoría A84 de capacidad histórica y eficacia de timers

El veto de 512 MiB retirado en #743 era una condición por tamaño anterior al
validador real. El presupuesto general de scratch sigue en 512 MiB; no se lo
aumenta globalmente porque `HistoricalStore.prepare_history_schema` pasaría a
copiar los 821 MB de historia al inicializar el writer en vez de leer su esquema
mediante transacción RO acotada. En el corte A84 `migrate_copy` conservaba por
defecto 512 MiB y cargaba todas las 596.196 versiones en memoria antes del
loop, con 60 s de presupuesto. Hacer ese método sobre el Droplet
sin aislar una copia, mapear moneda por cada clave y medir pico físico/tiempo
sería una migración incompleta o una interferencia con la rueda. Los datos del
corte A74: 67.445 versiones históricas previas a 365d, 439.833 referencias
canónicas y 235.884 cuarentenas con posible solape; ningún DELETE admisible.

El journal del viernes registró 373 ejecuciones IOL (p95 72,662 s), siete
publisher (máximo 80,255 s) y 14 intervalos IOL↔publisher concurrentes. No
equivale a dos instancias simultáneas de la misma unidad, ni prueba que la
contención SQLite sea cero. Los cuatro timers históricos suspendidos no pueden
solaparse mientras sigan masked; la eficiencia real de IOL, publisher, SHADOW,
preopen, scalping y locks exige correlación en una rueda OPEN. No se agrega
otro timer ni se habilitan A3/PPI sólo para obtener un estado verde.

## A85 — migrador de copia explícita, todavía sin datos de host migrados

El candidato #743 ahora separa el **default de 512 MiB** de los lectores
normales del techo máximo de 2 GiB disponible únicamente para una migración
de copia con `scratch_root` privado, en disco, presupuesto físico explícito y
reserva de 2 GiB. Si el entorno runtime fija su scratch a 512 MiB, una
invocación mayor en esa raíz falla cerrada: hace falta un proceso aislado con
custodia propia. El preflight `HistoricalStore` continúa usando el default
512 MiB; por encima de éste lee sólo esquema con transacción RO acotada.

El migrador dejó de cargar todas las versiones en Python y de reinicializar
SQLite por cada fila. Ordena por instante UTC real, recorre un cursor, valida
cada fila y usa savepoint para que una fila fallida no deje inserts parciales.
La fuente y las tablas legadas se preservan; destino nuevo con `quick_check`,
FK y mapping/cuarentena por fila. El plazo por defecto sigue 60 s; una copia
aislada puede solicitar hasta 3600 s. Pruebas locales de source inmóvil,
revisiones con offsets, rollback después de inserción, scratch privado y
preflight >512 MiB pasan. Una muestra sintética de 3000 filas tardó 7,802 s;
**no extrapolar linealmente** esa cifra a 596.196 filas ni declararla medida
de host. No se creó un mapping histórico de moneda confiable para todas las
claves; no se ejecutó migración del archivo real, ni promoción, ni purga 365d,
ni timers. El resultado sigue **EN_GITHUB DRAFT / runtime NO_VERIFICADO**.
