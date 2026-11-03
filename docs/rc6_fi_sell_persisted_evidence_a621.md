# A621 — BONOS/ON PAPER SELL: puente persistido de evidencia, no fill

**Estado:** DESARROLLADO; requiere CI/PR e integración serial al candidato #778. Ningún cambio habilita venta simulada ni real. `PAPER/SHADOW ONLY`, `real_orders_sent=0`, PPI Watch intocable. Sin Predeploy/Deploy.

## ERROR → RCA

1. `bm_exit_supervisor.PositionExitSupervisor.supervise()` declaraba la salida genérica `EXIT_DUE` de BONOS/ON, llamaba `_close`, y terminaba en `EXIT_PENDING_EXECUTION`. Sin embargo `be_paper_engine.PaperBroker._close()` deniega BONOS/LETRAS/ON antes de `SELL_SIMULATED`. El estado era equívoco: no era un problema transitorio de fill, sino falta de una ruta FI validada.
2. `paper_exit_intents` conserva causa/estado/hora suministrada por el supervisor, **no** recibo firmado de decisión conocida tras COMMIT. `decision_evidence_snapshots.captured_at=q.observed_at` no demuestra hora de disponibilidad. La decisión FI de salida tampoco equivale a una decisión previa de BUY en `paper_decisions`.
3. `market_snapshots.source='PRODUCTION_PAPER'` identifica **modo**. No prueba proveedor PPI. Su `observed_at` tampoco puede reemplazar `native_book_at`.
4. El diario privado PPI del scanner preserva Book nativo, cinco claves, best level, relojes y hash-chain. Sin embargo `quantity_unit` y `price_unit` quedan expresamente `NO_VERIFICADO`: los campos REST `quantity/price` no prueban VN ni precio por 100. Hash-chain local y etiquetas del caller **no son firmas del proveedor**.
5. `contract_evidence_v2_current` almacena identidad/snapshot por cinco claves; `READY_PAPER_CANDIDATE` no es tarifa account ni autorización de SELL. IOL es complemento y no sustituto de PPI. Las cantidades y montos ARS/USD se mantienen separados.
6. El módulo de venta FI simulada `rc6_fixed_income_paper_market_sale.py` puede contabilizar una venta con evidencia autorizada, pero recibe pines de Book/tarifa desde su caller y calcula settlement **modelado**. Una coincidencia de hashes autoaportados no autentica su economía causal.

## FIX → GUARD

Nuevo `rc6_fi_sell_persisted_evidence.py` realiza **lecturas** del ledger real, sin DDL, ninguna llamada PPI/IOL y ninguna ruta de orden:

- `observer_state` debe ser PRODUCTION_PAPER/0 órdenes reales.
- Posición FI OPEN y BUY PAPER exactamente uno, misma cantidad, sin SELL legacy huérfano; previo SELL nativo requiere conciliación independiente.
- Intención persistida con causa `EXIT_DUE`, 5-key exacta y reloj consistente.
- Último snapshot de misma 5-key con book/observed coherentes; etiqueta de modo no se promueve a fuente.
- `contract_evidence_v2_current` y snapshot anexado deben ser PPI, hashes consistentes, observación no futura, sin `CHANGED_REVIEW_REQUIRED`; ON se mapea explícitamente `OBLIGACIONES→ON` sólo para esquema contractual.
- Reader privado `verified_native_book_journal` realiza verificación canónica de integridad/permisos; Book1 debe enlazarse al mismo snapshot por clock + bid/ask/cantidad exactos; Book2 debe tener native clock estrictamente posterior a la hora **provisional** de intención + latencia, con bid y profundidad positivos. Este par es **sólo diagnóstico**.
- Settlement fecha PAPER modelada puede exponerse como hipótesis; no como acreditación broker.
- Cuatro guardas de autoridad no pueden obtenerse de las tablas actuales: **decisión post-COMMIT con recibo verificable independiente**, **unidad VN/precio por 100 atada a evidencia económica PPI**, **tarifa ALyC vigente para esa identidad** y **semántica de currency/settlement**. El resultado `execution_authorized=false` permanece invariable.

`bm_exit_supervisor.py` conecta el guard después de persistir la intención de BONOS/ON. Si falta evidencia retorna `EXIT_PENDING_FI_NATIVE_EVIDENCE` con códigos concretos, sin llamar `close_fn`; un error de auditoría es `WATCH_FI_NATIVE_EVIDENCE_BLOCKED`, también sin cierre. LETRAS maturity y acciones/CEDEARs no cambian.

### A621.1 — RCA performance del journal y guarda N×I/O

`PaperBroker.on_quote()` llama `_maybe_close()` por cada quote y este construye un **supervisor nuevo**; además `PositionExitSupervisor.tick()` recorre posiciones. Un cache atado a la instancia del supervisor no evita N relecturas/parses de un journal privado de hasta **8 MiB** por ciclo. La versión inicial de este PR releía todo el fichero por posición; corregido antes de merge.

El bridge ahora usa una única entrada LRU (`maxsize=1`) de **proyecciones compactas de best bid/ask** (no conserva 64 niveles ni el documento completo), lock single-flight y fingerprint físico `(dev,inode,size,mtime_ns,ctime_ns,mode,nlink,uid,gid)` obtenido con `lstat` sin seguir symlinks. Valida canon/permisos en primera lectura y contrasta metadata al finalizar parse y en cada lookup. Si se reemplaza, cambia tamaño, tiempo o permiso, **revalida desde cero**; un path ausente/inseguro no entra al cache. Un fallo conserva HOLD. La caché no aporta firma ni frescura: cada auditoría compara `Book2.native_book_at` con el propio cutoff y exige edad nativa máxima de 300 s (la ya definida por el scanner PPI). No se cachea ningún veredicto ni `execution_authorized`.

Pruebas adicionales: dos posiciones del mismo archivo causan **una** validación canónica, reemplazo de bytes causa otra, índice retiene sólo primer nivel aunque la fuente tenga 64, Book2 viejo queda `FI_EXIT_BOOK2_NATIVE_STALE_AT_AUDIT`, y una posición OPEN con `SELL_SIMULATED` previo incluso con recibo nativo del mismo conteo permanece en HOLD hasta conciliación ledger independiente. **32/32 tests locales**; CI remoto de A621.1 por confirmar.

## Solución completa pendiente, con productor independiente

1. **A619/PIT — recibo causal EXIT propio.** En el instante de `EXIT_DUE`, congelar payload exacto `{paper_id,side=SELL,cause,5-key,Book1.event_sha256,decision_at,release_id,hash}` durante una única transacción; luego de COMMIT un observador de otro dominio reabre DB y afirma disponibilidad real mediante ACK durable y firma Ed25519 verificada con trust anchor fuera del caller. `paper_exit_intents.supervised_at` y los hashes autosuministrados no satisfacen este requisito. El productor de decisiones BUY por sí solo no sirve como decisión SELL.
2. **Book2 después del ACK.** Verificador futuro requiere `Book2.native_book_at > max(decision_at, independently_verified_exit_known_at) + latency`, `Book2.known_at` disponible al tiempo de simulación, misma 5-key, sesión, fuente y profundidad compatible. La firma/recibo debe estar anclada a release e identidad; repetir request_id no puede recotizar ni dobles-cerrar.
3. **VN y precio.** En una ventana/identidad PIT coherente, utilizar sólo recibos PPI económicos autorizados, por ejemplo `Configuration/QuantityTypes` y `MarketData/Bonds/Estimate` (documentación REST oficial: https://itatppi.github.io/ppi-official-api-docs/api/documentacionRest/), preservando `quantityTitles`, `amountToReceive`, `issueCurrency` y un precio provisto. Es una **propuesta de adapter**, no una consulta hecha ni evidencia real recibida. No inferir de `Book.quantity` que es VN ni de `Book.price` que es per100. Debe respetarse presupuesto/EXIT antes de nuevos GET.
4. **Tarifa/FX/liquidación.** Enlazar recibo account/tarifa vigente y fecha efectiva, mercado, settlement y moneda a la misma transacción. Separar trading currency, issue/payment currency y FX explícito; tasa declarada que contradiga montos bloquea. La liquidación modelada puede servir para escenario PAPER, pero no como Account/Movements real ni liberar caja fuera de política.
5. **Activación gobernada futura.** Sólo cuando estos recibos estén almacenados y verificados end-to-end se prepara adaptador que pase Book/Fee autenticados a `apply_paper_market_sale` bajo la transacción idempotente ya existente. Revisión independiente de `paper_fi_native_sales`, `paper_fills`, `paper_fi_economic_events`, profundidad y caja. Nunca se usará la salida genérica de spot. La admisión FI de compra sigue con `FI_NATIVE_MARKET_EXIT_NOT_ADMITTED` mientras no se pueda salir.

## Evidencia/CI

Tests nuevos: joins de SQLite verdaderos con eventos PPI **sintéticos controlados** (no se presentan como PPI live), 5-key, Book2 en frontera exacta, Book1 ajeno, journaling HOLD, contract hash/JSON duplicado/review, BUY VN mismatch, SELL previo huérfano, PAPER/0, falla de auditoría, repetición idempotente y acción no-FI intacta; interfaz CLI abre copia SQLite `mode=ro`, `query_only=ON`, rechaza symlink y sobrepasar 100 posiciones, produce JSON sin GET ni write. **A621 inicial** [Actions 41718868804](https://github.com/mbalbo2023/Porota-trading/actions/runs/41718868804) 28/28 PASS/0 skip, artefacto SKIPPED; **A621.1** 32/32 locales, CI remoto pendiente de nueva revisión SHA.

**Fuentes/runtime:** No se consultó el droplet ni se descargó su DB/diario. El JSON publicado por Actions sin `db_opened=true` (A385) no demuestra ausencia ni presencia efectiva de un Book PPI. Script `scripts/rc6_fi_sell_ro_snapshot_audit.py` queda listo para ejecutarse sobre una copia consistente, bajo Action READ_ONLY y sin interferir en runtime cuando el dueño de ese plano lo autorice. `VALIDADO_RUNTIME=NO_VERIFICADO`, `SELL=BLOCKED`, `real_orders_sent=0` sólo como invariante de código/pruebas, no como contador host medido.
