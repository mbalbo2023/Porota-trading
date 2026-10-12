# Orden única para Codex — POROTA TRADING RC6

## 0. Misión y resultado exigido

Leé íntegramente esta orden y el paquete adjunto. Ejecutá de punta a punta la remediación y convergencia de POROTA TRADING RC6: conservar el desarrollo de los días **3 y 4 de octubre de 2026, hora de Buenos Aires**, corregir los hallazgos aplicables de #469 y #468, integrar la nueva UX de #470 y producir **un único candidato final**, con regresión adversarial y Predeploy V2 exacto, preparado para **un único despliegue posterior**. No devuelvas planes parciales ni des el trabajo por cerrado por tener CI GREEN.

Repositorio: `mbalbo2023/Porota-trading`. Punto de coordinación: Issue #471, actualizado por los cierres posteriores documentados aquí. El paquete es la orden de desarrollo; #468 y #469 conservan su naturaleza de auditorías READ_ONLY. Esta entrega del auditor no ejecuta sus recomendaciones ni publica cambios en GitHub.

**Dictamen #469: `GO_TO_FIX_AND_REAUDIT`.** El candidato #466 todavía tiene defectos programables; es un input del sucesor, no un release aprobado. El cierre de esta auditoría cubre sus 20 entregables, 29 findings consolidados y 80 escenarios distintos. El informe histórico aporta 21 findings, que se reconcilian individualmente: no sumar 29+21 como si fueran 50 defectos independientes. El trabajo de UX #470 está entregado y su WRITE_OWNER fue liberado; falta verificar sus interfaces en el candidato combinado.

El trabajo termina en `READY_FOR_USER_DEPLOY_AUTHORIZATION` únicamente cuando el candidato y su artefacto final satisfagan los gates de esta orden. **No merge ni deploy automáticos.** Prepará toda la evidencia y la orden de ejecución de Deploy V2 para que la autorización final del usuario recaiga sobre un SHA, un artefacto y un cambio concretos. Esta secuencia respeta #471, #467 y la petición actual de enviar una única instrucción de desarrollo.

Invariantes: PAPER/SHADOW ONLY; `PRODUCTION_PAPER / SIMULATION`; `real_orders_sent=0`; rutas reales `NOT_CALLED`; sin BYMA API; PPI Watch fuera de alcance; sin acceso/modificación de sus host, DB, ramas, timers o ingestiones. FIX-FORWARD ONLY. Nada de rollback a una imagen o snapshot de código anterior. Durante desarrollo: nada de mutaciones de runtime, DB productiva, systemd o Docker operativo. Pruebas sólo aisladas, sintéticas o sobre copias sanitizadas. Dashboard read-only, sin requests a proveedores al renderizar. Ningún cambio concede por sí mismo autoridad de entrada o capacidad APPROVED_DYNAMIC.

## 1. Entrada congelada y verdad temporal

| Aporte | Identidad exacta verificada | Disposición |
| --- | --- | --- |
| Línea productiva GitHub | `deploy/rc6-pr69-isolated-20260915` @ `da697c6e6c2274579f9e4a112fabc4327475dd35`; tree `96a112a55ed779df3f7056b1e30d81aac8d0b791` | Baseline a preservar; merge #449. No equivale a una nueva observación del host. |
| Motor/remediación #466 | `integration/ws-fix-audit-08-reaudit-20261004` @ `c27dfd963c4fe83465c0f2105347e974fbbe6356`; tree `bf3cf193434641aa89e4c746b26c77aec5d1d2b2` | 140 paths, 25 commits; OPEN/DRAFT/NOT_MERGED; base del trabajo fix-forward, sometido a #469. |
| UX #470 / workstream #467 | `work/ws-dash-trader-terminal-08-20261004` @ `4a5fc6384260b31f9a07e791093add6ee83102a1`; tree `b60929752d6a769e5876493486064e7c3c13a258` | 30 paths, 3 commits; OPEN/DRAFT/NOT_MERGED; integrar y preservar 8 destinos, 49 subvistas y 22 aliases. |
| Bootstrap Codex #451 | head `c4e939eea7aab4d662a268c98e0fb8282520b92d`; merge a `main` `183a0be3f6c443d8fab48b1c0f52dcc80b1b88d1`; `AGENTS.md` blob `2742ebf354762877fa9f685c20e704950decc16b` | Mejora documental independiente. Reconciliar su intención con AGENTS/policy productivos; no mergear `main` entero ni reemplazar las reglas productivas a ciegas. |
| Auditoría #468 recibida | `AUDITORIA_ISSUE_468_SALIDA_COMPLETA.zip`, 459158 bytes, SHA256 `aa78ad688c11c6e54f02ae9139554ab274e5210d704abd208ffcff37c5b6c7ce` | 16 miembros, CRC correcto; baseline da697, comparaciones puntuales c27. Conservar original íntegro. |
| Auditoría #469 | `ISSUE469_REAUDITORIA_INDEPENDIENTE_466_C27DFD9.md` y evidencia asociada | Exclusiva de c27/tree bf3c. No trasladar automáticamente su alcance a #470 o al sucesor. |

Revalidá estos heads, trees, PRs, owners, base productiva y cualquier nuevo workstream antes de escribir. Si aparece un head posterior, preservá el fijado como evidencia y revisá su delta; no lo descartes ni lo adoptes sólo por ser más reciente. La ventana “ayer y hoy” corresponde a la petición del 4/10 ART, aunque la ejecución continúe después de medianoche UTC. No se redefine como una ventana móvil que deje afuera el trabajo del 3/10.

El checkpoint #471 decía inicialmente que #469 estaba pendiente y que #467 seguía trabajando. Eso fue cierto en su corte. Para esta convergencia: #469 ya tiene dictamen desfavorable y #467 liberó WRITE_OWNER en su comentario de cierre `5985609600`, posterior a la revalidación `5985590489` de #471. No esperar indefinidamente un owner ya liberado; tampoco asumir que no pudo aparecer otro después.

“Productiva intacta” sólo significa que esta auditoría no la modificó y que su ref GitHub observado continuaba en da697. La evidencia histórica de host llega al **3/10 15:16Z**, y el censo al **3/10 15:39Z**. No se inspeccionó el host actual ni se certifican sus métricas presentes.

## 2. Lectura obligatoria y prioridad de evidencia

Leé: esta orden; `integration_history.md`, `integration_ux470.md`, `integration_provenance.md`, `integration_sre_scope.md` e `integration_quant.md`; informe completo #469 con seis anexos; los 16 entregables del ZIP #468 y sus drivers/resultados; #471 y comentarios; #467, PR #470 y su arquitectura de 1105 líneas; #466, #465 y requisitos #458/#460/#462; AGENTS y políticas en la línea productiva actual. Los snapshots del paquete permiten recuperar el corte; GitHub revalidado determina cambios posteriores.

La jerarquía separa código SHA/tree, ejecución Actions, bytes de artefacto, runtime y comportamiento DB/log/API. Los CSV de cobertura de #468 contienen valores NO_VERIFICADO deliberadamente. No convertir cantidad de filas, tests, attestations, un heartbeat, un literal de seguridad o un manifest autorreferenciado en prueba de operación, capacidad o rentabilidad.

Las instrucciones de proyecto adjuntas con corte septiembre son contexto histórico: no deben restaurar SHAs, límites o estado superados por autorización/evidencia posterior. Preservá sus invariantes vigentes. No cumplas un guard antiguo mintiendo sobre la identidad operacional actual.

## 3. Genealogía: qué conservar y qué no volver a aplicar

| PR fuente | HEAD verificado | Papel en la convergencia |
| --- | --- | --- |
| #449 | `9ca8cb13bfc6664597e313e5d1874710e3263ec4` | Blocker authority y contratos exactos de opciones ya incorporados en producto mediante da697. Preservar también el trabajo anterior #445 y dependencias. |
| #450 | `0311dcc10139777f8be988f4fe8c3efabc86d1a9` | Preopen no operacional honesto; reconciliado en #457 y posteriormente #463/#466. |
| #453 | `2b36429013ec5e3accf4176cf3beaabfd5e33232` | FUTUROS DLR corregido; es el head válido de procedencia. No recuperar `d738db79a452c699b50aed00ac539e57e1cc3b04`. |
| #454 | `ee1b22996907f282f77d3c31956104bd52cba858` | Evidencia/lab económico y SHADOW; evolucionado en #456. |
| #455 | `84528216cd85060075b63be25e71876e1b742b0c` | Control plane/legacy units; incorporado por contenido. No ejecutar retiros por esta lectura. |
| #456 | `b8c95c10459cb8ede2925da491730f5988b29561` | Linaje spot nativo, basket factible, costos compartidos; preservar. |
| #457 | `77c4e13768b252a8e7a614a0653b0b0dabb1ed02` | Preopen, cleanup seguro y frescura intraday; preservar. |
| #459 | `c48960e105640c385d94ac03461b0c936ebf95a7` | Universo dinámico causal y capacidad factual; preservar sin autoautorizarlo. |
| #461 | `fa3a8d64f74fa45181808bd46d8a75131d5450cf` | Consolidación y wiring SHADOW; input histórico de #463. |
| #463 | `caf9bc94b4a1f436ad01a84a9e9e9e7a4a9e9423` | Reconciliación #461 + #453 corregido + requisitos O–V; ancestro de #466, SUPERSEDED. |
| #466 | `c27dfd963c4fe83465c0f2105347e974fbbe6356` | 24 commits posteriores a #463, remediaciones de #465; conservar y reparar sus residuales. |
| #470 | `4a5fc6384260b31f9a07e791093add6ee83102a1` | Nuevo aporte UX no contenido en #466. |
| #451 | `c4e939eea7aab4d662a268c98e0fb8282520b92d` | Documentación bootstrap no absorbida por #466/#470. |

#463 tiene un único parent da697. Los heads #450/#453/#454/#455/#456/#457/#459/#461 **no son ancestros Git directos** de #466: su reconciliación fue por contenido. #466 sí desciende de #463. Por eso `merge-base` solo no demuestra que cada requisito fuente sobrevivió.

No mergear ni cherry-pickear otra vez esos PRs completos encima del sucesor. No usar “está OPEN” como indicador de trabajo pendiente. Verificar changed paths, blobs, evolución y comportamiento. Una fila `MODIFIED_PRESENT` demuestra presencia de un archivo evolucionado, no preservación semántica por sí sola. Los guards de cada requisito y la matriz final deben demostrarla.

Los 140 paths de #466 y 30 de #470 son conjuntos disjuntos en el corte observado. Conservar los **170 deltas esperados**, más la intención documental de #451, y todo archivo nuevo necesario para las correcciones. Cero colisiones textuales no garantiza compatibilidad entre el lector UX y el motor.

#418/#419/#422/#423/#426/#427/#428/#430/#431 u otros PRs viejos: clasificar `ABSORBED`, `SUPERSEDED`, `UNIQUE_DELTA_TO_RECONCILE`, `REVIEW_EVIDENCE_ONLY` o `NO_VERIFICADO`; sin merges amplios, cierres, borrados o force-push por antigüedad. #422/#423 sólo aportan unique deltas compatibles probados respecto de #470; el panel privado #107 y los doce tests de #344 conservan su alcance. No recrear una UI antigua para solucionar un problema del backend.

## 4. Forma de trabajo y barrera contra código viejo

Adquirí un único WRITE_OWNER de integración en una rama nueva, aislada. No escribas las ramas fuente. Creá el sucesor tomando #466 congelado como entrada del motor y reconciliándolo con la productiva vigente; incorporá el delta exacto completo de #470. No arranques la implementación desde la imagen UX sobre da697 como si contuviera el motor. Si da697 dejó de ser productiva, reconciliá los commits nuevos explícitamente antes de fijar la base final. Conservá historia y commits de procedencia; no restauraciones masivas de archivos desde snapshots previos.

Se observó un riesgo concreto: el remote-tracking local de la rama UX aún apuntaba a `88edd36aaab653c249644162ffdab1457c946de4`, sólo arquitectura, mientras GitHub y el objeto final eran `4a5fc6384260b31f9a07e791093add6ee83102a1`. Recuperá y verificá SHA/tree antes de integrar; el nombre de branch local no garantiza frescura. El delta UX completo es **da697→4a5fc**, incluida la arquitectura de 88edd y la implementación posterior, no sólo uno de sus commits.

Paralelizá las lecturas y verificaciones independientes. Para desarrollo, un owner por path y ramas/ámbitos separados, con un integrador. El budget, CF, broker, lifecycle, History Store, persistencia, dashboard y workflows tienen interfaces compartidas: no permitir writers simultáneos sobre la misma ruta. Reconciliar los contratos primero y los cambios después.

Generá `FINAL_INPUT_PROVENANCE.json` y una matriz legible. Por cada path final: blob de producto, blob de #466, blob de #470 si aplica, otros source blobs, blob final, workstream, requirement/finding, motivo de evolución, guard/test y estado. Por cada requisito no localizado, registrar bloqueo. Debe poder contestarse por qué el archivo final conserva o reemplaza cada aporte.

Gates: ningún delta esperado ausente; ninguna regresión a un blob anterior sin justificación funcional y regresión explícita; ningún fix anterior borrado para hacer pasar otro; ningún archivo de procedencia desconocida; ningún generated manifest que silencie paths discrepantes. Un número de commit más nuevo o la igualdad de nombres de archivos no satisface este gate. Preservá el inventario del paquete y actualizalo con el diff real final.

## 5. Registro obligatorio #469: corrección y prueba de cada finding

Los detalles reproducibles, paths, severidad y contraejemplos completos están en el informe #469. Estos 29 IDs no se pueden perder al repartir el trabajo. Unificá fixes que compartan causa, pero conservá la trazabilidad individual.

| ID | Trabajo exigido | Prueba de cierre mínima |
| --- | --- | --- |
| U01 P1 | Servicio EXIT bounded pese a un owner single-flight inferior lento; conservar coalescing y cuota real. | OPENED lento, EXIT concurrente más allá de 50 ms, tres ventanas y deadline por posición; sin doble llamada ni preemption ficticia de HTTP ya enviado. |
| U02 P1 | Mantenimiento de budget posible incluso con sidecar lleno; cardinalidad, poda y cuotas físicas coherentes. | Reproducir 6 scopes/s, SQL full, envejecimiento +4000s y restart; demostrar recuperación de EXIT sin borrar deuda de llamadas. Usa DELETE journal: no “arreglar WAL” inexistente. |
| U03 P1 | Recovery intraday ligado al inicio durable y clocks coherentes; rollback durante fetch no retrofecha warmup. | Caso start14:01 → completion13:47:40, 15 puntos falsamente posteriores; cero fill antes de warmup real, incluidos restart y futuro. |
| U04 P1 | Propagar/validar las seis variables y paths de dynamic capacity desde launcher canónico. | OFF/SHADOW/APPROVED config-only desde `porota_mode_manager`, sin credenciales en logs ni activación automática. |
| U05 P1 | Rechazar una envolvente de capacidad incapaz de cubrir demanda EXIT; fairness y deadline por identidad. | 30 exits/cap15 o10, 10 posiciones/cap5 durante varias ventanas, cuotas globales y endpoint; no aumentar límites sin evidencia. |
| U06 P1 | Una única admisión económica BINDING en CF y broker; jamás falsificar `passed=true`. | Net R/R0,670348<1,20 produce no entrada y conserva razón; caller completo, costos de ambos lados y fill/intent lineage. |
| U07 P1 | Reservar EXIT para FUTUROS ACTIVE y estados canónicos de cada familia. | Mix spot/futuros, 5+5, discovery exhaustivo y cuota insuficiente; ninguna posición omitida por `status='OPEN'`. |
| U08 P1 | Identidad exacta de cinco componentes en quote/catalog/exit end-to-end. | Libro exacto cruza STOP mientras una cotización más nueva de otra moneda/mercado existe; STOP/TP/EOD/restart tanto spot como futuro. Tombstones no reviven LKG. |
| U09 P1 | Tick contractual DLR y redondeo adverso por lado del fill ejecutable, antes de su cash/P&L. | Quotes secuenciales de entrada/salida:1579,3158/1579,6840 frente a tick0,5; no corregir sólo display. Marks y settlement oficial se rigen por su tipo/fuente/precisión contractual, sin redondeo adverso automático. |
| U10 P1 | Cutoff exacto en futuro cash/risk; eventos posteriores no liberan margen antes. | t±1/100/499/500 microsegundos y offsets normalizados, variation/close/restart; no pérdida de precisión con julianday. |
| U11 P1 | Selección del artifact ligada a run/head/workflow/path/event/attempt aprobados. | Run exitoso de otro head, workflow homónimo, artifact de nombre igual, reemplazo externo y expiración rechazados antes de promoción. |
| U12 P1 | Igualdad efectiva expected image ID == loaded image ID antes de tag/promote. | Tar hash válido con loaded ID divergente falla cerrado antes de mutar tag; distinguir normalización legítima de config digest. |
| U13 P2 | Gate de salud canónico contempla child SHADOW y última generación válida/fresca. | Spawn failure, crash loop, CURRENT ausente/stale; scanner heartbeat sano no basta. Preopen legítimo no debe fingir READY. |
| U14 P2 | Retención sirve una rueda completa y recuperación con cadencia real sin autoagotarse. | Caller full tick: repetir frontera soft 40,5 m/hard 50,5 m y una rueda+recovery; sin borrar evidencia no archivada. |
| U15 P2 | ACK ledger con compactación/checkpoint seguro y cuota sostenible. | Archiver ideal, >506 ACKs y miles de ciclos; reinicio y prueba de continuidad del anclaje. |
| U16 P2 | Sanitizar errores de fuentes antes de cache/report/archive. | Marcadores de tokens, headers/URLs/PII sintéticos; taxonomía allowlisted, no mero truncado. |
| U17 P2 condicional | Definir trust boundary y comprobar safety semántico entre report/status. | Rehash integral con orders7/status0, writer/reader y promoción; autenticidad requiere anclaje independiente, no más hashes en la misma raíz. |
| U18 P2 condicional | ACK unido a objeto durable realmente verificado e identidad de archivador. | URI inexistente, digest inventado/replay y archivo inaccesible no habilitan borrar; documentar límite de confianza. |
| U19 P2 | Rotación reanudable e idempotente ante borrado parcial. | EIO en cada unlink/rename/fsync, kill/restart y reintento; ninguna evidencia íntegra no archivada perdida. |
| U20 P2 | High-water/lineage durable frente a rollback de CURRENT. | Volver a pointer válido viejo y commit: no sequence duplicada/fork; recuperación explícita y freshness independiente. |
| U21 P2 | Audit de fuentes semánticamente ligado a reports del mismo corte. | Digest correcto pero audit vacío/ghost/count/status/asof incompatibles debe fallar; recomputación canónica. |
| U22 P2 | `portfolio_capacity(at)` reconstruye estado de futuros al corte. | Un close futuro no cambia riesgo1.500.100 del pasado a0; invariancia ante agregar eventos posteriores. |
| U23 P2 | Idempotencia de FCI valida fingerprint completo de intención. | Mismo event_id con monto1000→2000, moneda/time/detail cambiados y carrera: conflicto explícito; retry idéntico sí. No afirmar wiring FCI no demostrado. |
| U24 P2 | Export histórico realmente no escribe en filesystem fuente. | Main+WAL sin SHM en fuente RO; snapshot coherente en copia, no generar SHM fuente ni ignorar WAL pendiente usando immutable de forma incorrecta. |
| U25 P2 | Validar permisos completos según policy y rechazar writable inesperado. | Mutación0644→0666 con hash externo recalculado; revisar source/bundle/image. Los modos del artifact auditado estaban correctos. |
| U26 P3 | Report duplicado no infla cardinalidad sin semántica declarada. | Exact duplicate y duplicado conflictivo; conteo/digest/identidad coherentes. |
| U27 P3 | `shadow_promotion` no expresa autoridad incompatible con PPI primary. | BYMA-only/IOL-only; flags selection/entry/live/promotion consistentes y consumidores inventariados. |
| U28 P3 | Unificar documentación de disco con autorización dinámica+2 GiB. | YAML/JSON/runbook/gates concuerdan; no reinstalar arbitrariamente6 GiB ni etiquetar como incumplimiento el piso autorizado. |
| U29 P2 residual | Endurecer reproducibilidad de supply chain y explicitar límites. | Actions por commit y dependencias con hashes/wheelhouse soportado; drift negativo. No declarar build hermético sólo por pin de versión. |

U01–U12 bloquean la preparación de deploy. Los P2 materiales de liveness, evidencia y promotion también deben cerrarse. U17/U18 son condicionados al trust model: no inventar intrusión ni exigir resistencia imposible a un atacante omnipotente. Los P3 y U29 no se elevan a P1 por narrativa; implementá su resolución razonable o presentá aceptación residual concreta y justificada para la decisión final, sin autoaceptar riesgos materiales.

## 6. Registro #468 reconciliado: una sola solución por causa

La auditoría histórica inspeccionó da697 y algunas rutas de c27. Su evidencia de producto no desaparece, pero no se traslada sin comparación al sucesor. El cuadro fija la disposición de desarrollo; `integration_history.md` contiene la revisión completa y sus límites.

| ID | Disposición respecto del candidato | Trabajo / guard de cierre |
| --- | --- | --- |
| AUD-468-01 P1 | Fix de frescura presente en #466; stale-tail20m queda HOLD en fixture. No duplicar ese fix. | Conservar y reejecutar guard; completar U03 rollback/recovery y disponibilidad/received clocks. Cerrar sólo el caso demostrado, no todo CF. |
| AUD-468-02 P1 | Persiste: 15 puntos/28s o42m pasan como ventana de15min. | Contrato explícito de tiempo, cadencia, cierre y continuidad. Microbursts, timestamps repetidos por minuto, gaps, irregularidad y sesión; no arreglar subiendo sólo `min_samples`. |
| AUD-468-03 P1 | Persiste: descenso de volumen no identifica intervalo. | Tipo/unidad/semántica por proveedor/familia y procedencia. Acumulativo con reset no se confirma; intervalar monótono no se rechaza por heurística. Sin contrato demostrable, fail-closed en usos que dependan de él. |
| AUD-468-04 P1 | Limitación baseline válida; #466 ofrece alternativa SHADOW/dynamic todavía no aprobada. | Resolver junto U01/U02/U04/U05/U07: catálogo completo, discovery, HOT/WARM acotado y capacidad factual. No volver al lote viejo ni subir batch para simular cobertura. |
| AUD-468-05 P1 | Persiste en motor principal:20 eventos/1min equivalen a20/90min. | Definir si señal es de tiempo de eventos o calendario; si promete horizonte temporal exigir span/cadencia. Reproducir1/15/30/90m, gaps y repeated market-time. No imponer90m a toda estrategia sin justificar su hipótesis. |
| AUD-468-06 P2 | Persiste: adjusted secundario gana a PPI raw. | Separar price basis/adjustment/factor/acción y autoridad por uso; jamás cambiar base con sólo booleano `adjusted`. Conflicto visible. |
| AUD-468-07 P2 | Persiste: History v2 omite currency. | Full-key+serie/base explícitas, migración verificada sobre copia y cuarentena de identidad ambigua. No inventar moneda ni agregar PK a ciegas. |
| AUD-468-08 P2 | Persiste: identical reingest renueva canonical observed_at. | Separar event_at, version_known_at y last_checked_at; precio viejo no se convierte en nueva observación. |
| AUD-468-09 P2 | Persiste: offsets ordenados lexicalmente. | Instantes UTC normalizados con precisión definida, tie-break determinista y rechazo de timestamps inválidos/futuros. |
| AUD-468-10 P2 | Persiste: sink directo permite fecha futura/infinito. | Validación universal de fecha/sesión/finitud/OHLC/volumen antes de escribir; no depender sólo del adaptador PPI. |
| AUD-468-11 P2 | Persiste; caller SHADOW activo en BE, no dead code. | Versión conocida/downloaded <= decision_at; descarga posterior no entra a decisión pasada. Hardenizar lector o reemplazarlo con call graph/test que pruebe supersession. |
| AUD-468-12 P2 | Persiste: revisiones cuentan como barras; revocación rescata versión vieja. | Lector canónico de última versión as-of por serie/bar; filtrar calidad después, no antes. Una barra15revisiones cuenta1. |
| AUD-468-13 P2 | Ruta IOL legacy; uso en runtime RC6 no demostrado. | Probar caller/consumidor/unidad. Si activa, money/qty/nominal tipados; si muerta, deprecar con guard y evidencia, sin borrar datos por inferencia. |
| AUD-468-14 P2 | Error matemático reproducido; impacto binding no probado. | Bounds/modelo según estilo contractual, tasa/dividendos/tiempo. Put europeo válido6,6716 no se rechaza por intrinsic20. No tratar opción americana como europea ni crear autoridad de entrada. |
| AUD-468-15 P2 | Semántica anual heredada; reconciliar con #470. | BONOS/LETRAS/ON: precio sin flujos claramente rotulado; total return/TIR/accrued sólo con cashflows/base válidos. Probar todas las rutas/aliases todavía accesibles. |
| AUD-468-16 P2 | Conceptos distintos siguen necesitando contrato, aunque nueva UX mejora presentación. | Mostrar fuente/cut/identity de history, candles, snapshot, decision y broker por concepto; freshness del panel no implica input usado por el motor. |
| AUD-468-17 P2 | Persiste: dedup no atómico. | Restricción/idempotencia DB con clave semántica y transacción; dos writers idénticos no duplican versiones. |
| AUD-468-18 P2 | Persistencia parcial de lote sin semántica de intento global. | Elegir atomicidad acotada o commits por fila con attempt ledger exacto/reanudable. No llamar bug a toda partialidad; el contrato y conteos deben reflejarla. |
| AUD-468-19 P2 | Store intraday mutable no permite replay previo; snapshot podría sustituirlo, no está probado completo. | Probar vector PIT completo por cada decisión binding+hash/clocks y replay sin tabla mutable; si falta, revisiones append-only. No duplicar stores sin demostrar necesidad. |
| AUD-468-20 P2 | Persiste: Data912 normaliza sin validar de manera uniforme. | Endurecer adaptador y sink común; NaN/inf/futuro/high<low/OHLC parcial, razones precisas, retry429/timeout sin falsa completitud. |
| AUD-468-21 P2 | Gap de cobertura/observabilidad; no es por sí mismo un bug READY. | Métrica full-key/currency/session por consumidor y corte; no trasladar5739/7728 del1/10 al presente ni exigir history global a estrategias que no lo consumen. |

Cada ID debe cerrar como `FIXED_WITH_GUARD`, `SUPERSEDED_BY_PROVEN_REPLACEMENT`, `NOT_APPLICABLE_WITH_PROVEN_DEAD_CALLER`, `BLOQUEADO_EXTERNAL` o `NO_VERIFICADO`, con evidencia exacta y explicación. No vale PASS genérico. Mantener visibles los externos pendientes; no esconder un defecto programable como falta de datos.

## 7. Decisiones de diseño que deben resolverse juntas

### 7.1 Tiempo, scalping, volumen, presupuesto y admisión

No sustituir la mejora dinámica por la rotación productiva vieja. La arquitectura deseada conserva catálogo completo y separa discovery barato de seguimiento acotado HOT/WARM. El tamaño activo se deriva de demanda crítica, endpoints, latencia, revisit, memoria y cuotas medidas; no de un número fijo optimista. Primero servicio de salidas por identidad y estados de todas las familias; después abiertas y nueva actividad. Liveness y fairness deben demostrarse bajo la misma policy y lease que el caller real.

`READY != ELIGIBLE != TRADEABLE != DISCOVERY != WARM != HOT != SIGNAL != ECONOMICS != RISK != PAPER`. HOT nunca equivale a BUY. El rango observado retrospectivo no es expected net edge ni una probabilidad. La UX no concede autoridad a los componentes del funnel.

Especificá el contrato temporal de cada señal antes de cambiar fórmulas. Si son muestras de eventos, denominarlas así y validar la hipótesis; si son barras1m/ventanas15m/90m, construir/consumir barras con grilla, cierre, duración y known_at comprobados. No promover Current muestreado a tape completo. No forward-fill que invente negocios/volumen. Recovery debe exigir datos posteriores reales y no sobrevivir a rollback mediante timestamps retroactivos.

El contrato de `volume` no se puede deducir de monotonía. Mantener quantity, nominal y turnover monetario separados; intervalar/acumulativo, reset y sesiones explícitos. Si no hay contrato PPI demostrable, conservar el caso como externo y dejar cerrada la autoridad dependiente; completar todo lo demás. No relajar el filtro para aumentar operaciones ni introducir un sustituto económico sin validación.

### 7.2 History Store v2 como un único diseño

Resolver AUD-468-06/07/08/09/10/17/18/20 en el mismo contrato: identidad completa, source/basis/adjustment provenance, UTC, clocks separados, validación universal, dedup atómico, intent/attempt ledger y migración compatible. Reutilizar la infraestructura canónica: no crear una tercera “verdad” histórica paralela ni hacer de todo history un gate global READY.

Preparar migración versionada/dry-run sobre copia: inventario y fingerprint inicial, mapeos inequívocos, ambiguos en cuarentena, counts y hashes antes/después, índice/unicidad, versión schema/reader y reanudación tras crash. No etiquetar valores viejos con currency o factor inferidos. No ejecutar la migración productiva en la fase de desarrollo. Mantener compatibilidad de lectura planificada para el único release; si requiere migración operativa, incluirla explícitamente en la futura orden de deploy y en su presupuesto de disco.

### 7.3 PIT, evidencia y archivos durables

`historical_candle_shadow_rc6.collect()` tiene caller activo en #466: no borrarlo como obsoleto por existir el worker nuevo. Corregir sus readers o sustituirlo de forma demostrable. Una última revisión CONFLICT revoca la válida anterior para ese corte; no filtrar primero COMPLETE para resucitarla.

Mantener sólo una autoridad de replay: snapshots completos e inmutables de decisiones o revisiones con known_at. Probar que cada decisión binding queda reconstruible tras correcciones y restarts. No basta que exista una tabla llamada evidence. La cadena comprometida debe unir generation_id, sequence, watermark, config, source reports/audit y safety. El dashboard y consumers deben usar el mismo cut validado.

Retención no puede detenerse a los50,5 min ni mover el problema al ACK ledger. Cuotas separadas y acotadas, archiver con receipt verificable, compactación segura, pins/current, tombstones y recuperación deben sostener una rueda más recovery. No exigir servicio externo ficticio: si el archivo durable falta, declararlo y probar un modo de funcionamiento seguro con horizonte contratado. Hash interno no autentica writer; explicitar custodia/anclaje externo y no imprimir secretos.

### 7.4 Finanzas y lifecycle por familia

Cerrar U06–U10/U22/U23 preservando lifecycle FUTUROS corregido de #453. Tick, multiplicador, cantidad, margen, variation margin, realised/unrealised, release y costos son conceptos distintos; redondeo sólo visual no corrige caja. Las comparaciones al cutoff deben ser exactas y estables ante eventos futuros. Event_id repetido con intención distinta es conflicto, no idempotencia exitosa.

Greeks/IV y cashflows deben usar términos contractuales de la familia. No proyectar equity sobre futuros/bonos/cauciones/FCI ni sumar ARS y USD. Corregir un cálculo orientativo no lo convierte en input binding. PPI sigue como autoridad primaria; complementarios no sobrescriben identidad ni contrato por ranking sin base comparable.

No mezclar la muestra histórica de77 cierres/19 fechas y 80 snapshots con el agregado declarado68 posiciones/151 fills/20 ruedas de #469: son cohortes y evidencias diferentes. Ninguna constituye por sí sola un master completo/OOS. Preservar negativas de rentabilidad sin tuning retrospectivo de Stop/TP/EOD/MaxHold; evaluar baseline, no-trade y alternativas con costos, clocks y holdout preregistrados cuando exista dataset apto.

### 7.5 Contratos financieros de aceptación

Las precisiones siguientes resuelven la revisión quant del borrador conservada en `integration_quant.md`:

- Construir una matriz `campo × fuente × effective_at × known_at × consumer`. PPI conserva autoridad primaria de identidad/readiness y datos del broker; términos oficiales de un contrato, settlement designado, tarifas/statement de cuenta, cashflows, corporate actions, NAV y FX exigen su autoridad por campo. Esto no habilita una fuente complementaria a sobrescribir identidad PPI ni una nueva API.
- Costos versionados: comisión, derechos, clearing, IVA, mínimos/escalones, rebate, moneda, FX y regla de rounding separados. Tarifas reales desconocidas siguen NO_VERIFICADO. Una policy PAPER explícita puede simular supuestos etiquetados; no presentarlos como condiciones comerciales comprobadas. Un dato obligatorio ausente del contrato económico elegido cierra su gate BINDING.
- FUTUROS: mantener el alcance demostrado **DLR estándar LONG PAPER, series verificadas**. No anunciar soporte genérico de todos los futuros ni shorts. Diferenciar reserva conservadora PAPER de margen inicial/mantenimiento del broker y de compra del nocional. Variation cash, collateral, equity y cash disponible se concilian sin doble conteo; settlement reinicia la base de variación y close realiza sólo lo no realizado. Expiry/calendario/último día/settlement/límites se resuelven por serie y fecha.
- Tipar `price_kind`: quote/trade/fill ejecutable respetan grilla correspondiente; settlement oficial preserva su precisión y regla publicada; mark sigue la regla de su fuente. Tick de precio, quantum del ledger y rounding de fees/FX son distintos. Usar Decimal y redondear cada componente una vez en su frontera; nunca redondear el P&L final al tick de cotización.
- Certificar 20 ruedas exige sesiones exactas/timezone, regla de inclusión/cutoff, posiciones abiertas sobrevivientes, cantidades parciales, cash inicial/final, reservas/collateral, variation y equity por moneda. `gross-costs=net` de una cola no reconcilia la cuenta. No-trade nominal0 tampoco sustituye cash/caución neta, costo de oportunidad e inflación cuando el benchmark económico los requiera; sin datos no se calculan ni se inventan.
- Semántica por familia: CEDEAR con ratio, underlying, FX y corporate actions efectivos; renta fija con clean/dirty, accrued, cupón, amortización y day-count; opciones con estilo, dividendos, curva, multiplier y settlement; caución con convención de días, hábiles y tarifa; FCI con cutoff y NAV forward realmente conocido. El componente sin evidencia queda NO_VERIFICADO/NO_MEDIDO, sin derivar autoridad binding de un cálculo equity genérico.
- STOP/TP son triggers, no garantía de fill o pérdida máxima. Conservar depth, parcialidad, gaps, stale/no-bid, prorrateo de costos/cash y razones de salida no ejecutable. Probar que la UI distingue intención, trigger y fill.

## 8. UX #470: preservar el resultado y conectar la verdad

Conservar diseño y accesibilidad aprobados: **Inicio, En Vivo, Trading, Universo, Instrumentos, Riesgo, Analítica, Sistema**; 49 subvistas y 22 aliases legacy; terminal oscuro, sidebar, strip operativo, subnav, tablas de 10 filas server-side, detalle progresivo, foco visible, sin scroll horizontal de página, responsive1440/1280/1024/800/600/360 y Voice Access. Refresh mantiene texto pendiente, foco, filtros, URL, scroll y detalles, y pausa durante interacción. Mantener cero llamadas de proveedor al render.

Preservar los 18 módulos `rc6_trader_dashboard/`, enganche mínimo de `o_dashboard.py`, inventario/truth matrix, arquitectura, acceptance y regresiones. Corregir defectos reales sin volver a ejecutar primero renderers legacy ni reemplazar todo el dashboard por un snapshot anterior.

El adapter #470 consume únicamente `rc6_shadow_runtime.persistence.read_committed_generation`. En su branch aislada la ausencia del contrato se presentaba correctamente como `NO_VERIFICADO_AWAITING_RECONCILIATION`. En el candidato combinado esa ausencia ya no es una excusa: integrar con la implementación final de persistencia, probar schema/sequence/watermark/config/digest y rechazar contradicciones. Nunca inferir coherencia por mtime ni mezclar latest/checkpoint/status de generaciones distintas.

Gates UI/backend: estado exacto `candidate_identity_v2`, cinco componentes de identidad, source/clocks visibles, Decimal por moneda; métricas derivadas con provenance fresco; score no “probabilidad”; MFE/MAE con trayectoria; NAV/Greeks/OI/CCL/cashflows/costos/capacidad sin evidencia siguen NO_VERIFICADO/NO_MEDIDO. Probar consistencia del funnel dentro de un único cohort/currency/channel/cut y los estados de bloqueo reales después de remediación.

Reconciliar AUD-468-15/16 tanto en rutas canónicas como aliases accesibles. Si un renderer anual legacy sigue activo, corregirlo o retirarlo con sustitución y guard; cambiar sólo una tarjeta nueva no cierra el hallazgo. Verificar contrato API, serialización, pagination SQL, bounds, XSS/SQL injection, datos faltantes, DB busy/WAL y ausencia de side effects. Read-only de SQLite puede crear SHM: probar la inmutabilidad exigida sobre copia/RO según U24, sin recomendar `immutable=1` para ignorar un WAL vivo.

### Incompatibilidades concretas halladas en la convergencia

Estos IDs son nuevos hallazgos de **integración #466×#470**, no alteran el objeto ni el registro U01–U29 de #469. Tienen que resolverse antes de considerar completo el candidato. El informe `integration_ux470.md` conserva paths, método, evidencia y límites de las pruebas.

| ID | Incompatibilidad de los heads fijados | Corrección y prueba exigidas |
| --- | --- | --- |
| UX470-I01 | `Projection.shadow` usa por defecto `db + '.shadow'`; #466 escribe en `artifact_root(db)/dynamic-shadow`. El env del dashboard no aporta el override. | Un único resolver/config de raíz y contrato canónico; ejecutar writer real en copia, lectura committed y render sin override manual oculto. Ausente/corrupto debe seguir fail-closed, no buscar arbitrariamente por mtime. |
| UX470-I02 | UX espera `OFF/SHADOW`, `OPEN_EVIDENCE_VERIFIED`, `baseline`, `endpoint_budgets`; motor emite `OFF_BASELINE/SHADOW_BASELINE`, `SHADOW_RECOMMENDATION/NO_VERIFICADO`, `baseline_limits`, `slots_by_endpoint`. | Schema versionado compartido o adapter explícito de versión; mostrar capacidad factual y limitaciones correctas en OFF/SHADOW/APPROVED. No traducir recomendación SHADOW a capacidad OPEN comprobada. |
| UX470-I03 | Labs buscan `rows/families/evaluations/registry`; motor entrega `experiments/cohorts/registries` y `entries/entry_hour_cohorts`. Counts espera `funnel.counts` en raíz; motor publica `by_currency_channel[].stages`. | Labs, cards y funnel deben consumir el mismo cut y cohort/currency/channel con cardinalidades coherentes. Dataset no vacío no puede aparentar vacío por campo desconocido; desconocido produce error contractual. No sumar monedas/canales para rellenar un total. |
| UX470-I04 | Posiciones/count/riesgo UX consulta `paper_positions OPEN`; FUTUROS del motor reside en `paper_future_positions ACTIVE` más marks/lifecycle. | Proyección por familia con identidad/cut exactos, futuros ACTIVE/closed, reserva PAPER, variation, exposure y cash separados; posición FUT de fixture debe verse y conciliar con el risk snapshot corregido. |
| UX470-I05 | CI y truth matrix aislados usan un contrato sintético y no acreditan integración. El launcher monta el hook desde stage, mientras el paquete UX depende de la imagen. | Guard productor→consumidor y truth matrix final; hook, paquete, mounts e imagen deben corresponder al mismo SHA/tree con import closure ejecutada. No mezclar hook nuevo con paquete interno viejo, ni desplegar artifacts parciales. |

Los fixtures de UX deben generarse desde los productores reales del sucesor o un contrato validado byte a byte contra ellos. No reparar los tests reemplazando el writer por otro JSON inventado que coincida con el lector. Las discrepancias merecen errores de contrato explícitos, no un `get(..., [])` silencioso. Mantener la UI aprobada y corregir la interfaz.

Reejecutar los tests y navegador sobre la combinación final, además de los nuevos casos de `integration_ux470.md`. Los 171 tests focales y 294 checks + 22 legacy + 9 interacción declarados por #470 son evidencia de su rama, no PASS automático del sucesor. No reclamar auditoría independiente completa de UX por esta reconciliación acotada.

## 9. SRE, disco y promoción: alcance correcto

El número histórico8.577.789.952bytes libres corresponde al snapshot del3/10, no al host actual ni al footprint del candidato combinado. La divergencia de documentos6 GiB frente a fórmula dinámica+reserva2 GiB es U28P3: existe autorización explícita del1/10. No reinstalar un piso6 GiB sin motivo ni usar2 GiB como autorización para ignorar el tamaño de transferencia/load/migración.

Recalcular con el **artefacto final** el espacio simultáneo por filesystem: ZIP/extracción en el runner; tar transferido, layers/load, coexistencia de imagen vigente y nueva, logs/evidencia y eventual migración en el host correspondiente. El workflow actual extrae el ZIP en el runner y transfiere sus archivos: no sumar otra copia ficticia del ZIP en el host. La fórmula canónica es tar comprimido + tamaño Docker exacto de imagen + dos copias del bundle + reserva residual de 2 GiB; comprobar por separado inodos y cualquier costo adicional real de la migración. Usar guards medidos antes/después de transfer/load/promote/cleanup. El cálculo de #466 es 3.710.319.164 bytes antes de transferir, una referencia histórica que no se reutiliza para el sucesor. Cleanup limitado y medido con pins/evidencia preservados; nada de prune global ni retiro de unidades ajenas. PPI Watch no se inspecciona.

El artifact #466 fue verificado independientemente por bytes: ZIP `6514bfdef175d31044a0f204c799ebc2029722cce77f44724909a7069a2ed552`, bundle e imagen,1044archivos `/app`, sin discrepancias de contenido/modo en el objeto real; static import closure464/464. Eso no ejecutó smoke dentro de la imagen ni demuestra salud del host. U11/U12/U25 atacan guards frente a otras entradas; no prueban que ese ZIP estuviese adulterado.

**Denylist para promoción final:** artifact466 `11315198085`/run37234866451; artifact470 `11317509383`/run37243206476; artifact463 `11293625514`/run37175265248 y cualquier artifact construido antes de cerrar la convergencia. Se conservan para evidencia. El final necesita nuevo SHA/tree y nuevo artifact; no combinar archivos/imágenes de esos ZIPs ni retaggear una parcial como final.

Mantener control plane GitHub/Actions, mutex `rc6-unified-paper-deploy`, `cancel-in-progress: false`, build once, sin rebuild en Droplet. Verificar origen del run y bytes exactos, permisos, runtime closure e image ID antes de promoción. Conservar cleanup en paths de fallo sin borrar evidencia única. Un preopen no operacional válido puede cerrar un gate preopen; no certifica mercado OPEN ni READY end-to-end.

## 10. Regresión adversarial del candidato único

Generar `REMEDIATION_REGISTER` con ERROR → RCA → FIX → GUARD → TEST → EVIDENCIA por ID y SHA. Un fix necesita un caso RED previo reproducible, control positivo, variante adversarial y prueba desde el caller canónico. No borrar asserts, excluir tests, cambiar esperados para legitimar el bug ni generar fixtures verdes sin el camino real.

1. Reejecutar los 80 escenarios de #469 y sus 45 temas previos, F01–F05 completos —incluidas 35 variantes F01 y 20 F02— y los 21 findings de #468 con sus casos. El harness histórico retuvo 96 casos producto y 90 candidato: incorporar al sucesor los seis omitidos (`H_LEGACY_ASSET_CLASS_COLLISION`, `M_FIXED_INCOME_ANNUAL_LABEL`, `M_MAIN_SAMPLE_CADENCE`, `S_CRASH_AFTER_VERSION_INSERT`, `S_HEARTBEAT_NO_PROGRESS`, `U_IOL_ARCHIVED_LIQUIDITY`) o justificar alcance mediante evidencia, jamás convertir ausencia en PASS. No contar parametrizaciones como nuevos escenarios ni sumar suites superpuestas como tests únicos.
2. Probar cruces: tiempo×volumen×economics×budget; FUT ACTIVE×moneda×STOP×PIT; history revisions×source basis×dashboard; persistence recovery×archive quota×UX; artifact selection×hash×mode×imageID.
3. Multiproceso, same-lease contention, deadline/cancel, timeout, retry429, DB busy/full, kill/restart, clock rollback/future, archive EIO/ENOSPC, duplicate/conflicting IDs, generaciones corruptas y schema compatible. Proveedores sustituidos por fixtures offline, órdenes reales imposibles.
4. Stress representativo10× universo / 5× datos con el caller completo, capacidad y retención realistas, memoria/CPU/IO medidos y exits concurrentes. Una prueba que falla antes de fsync no prueba slow-fsync. Una prueba sintética del runner no acredita p99/SLO del host.
5. Full governed suite desde repository-root sin nuevas exclusiones; counts discovered/executed/fail/error/skip/xfail y razones legítimas. Compile, secret scan, dependency drift, policy, import closure, byte provenance y safety negatives.
6. Browser offline de todas las rutas/resoluciones/aliases, interacción durante fetch pendiente y accesibilidad; DB fixtures con clocks/identidad/currency y generation contract reales del sucesor, no mocks incompatibles.

Toda evidencia debe llevar sourceSHA/tree/config/schema/seed/clock/env/versions/cutoff y comando reproducible. Conservar RED y GREEN. Registrar claramente si la prueba es ejecución independiente, test nativo, inspección o evidencia externa; no usar la atestación del autor como conclusión del auditor.

## 11. Predeploy exacto y reauditoría final

Sólo al cerrar el código y la integración, fijar SHA/tree final, PR DRAFT único y manifest completo. Ejecutar Predeploy V2 para **ese SHA**, construir una vez y no reconstruir en producción. Si una corrección cambia el SHA, el artefacto anterior queda invalidado para el nuevo candidato; repetir el ciclo para el nuevo SHA, no maquillar provenance para conservarlo.

Validar: Git→source manifest→bundle→image tar/config/layers→rootfs; conteo/bytes/modos y exclusiones explícitas; readers/entrypoints/import closure dentro de imagen efímera offline sin credenciales/mounts operativos; SBOM/deps según policy; exact run/workflow/head/attempt; digest del ZIP y artifact; ImageID. Nueva UX y nuevas correcciones deben estar presentes en el **mismo** artefacto.

Reauditoría adversarial del sucesor contra todos los registros y tests cruzados. Dictamen separado de calidad de software, seguridad PAPER, integridad de datos, capacidad/operación y edge económico. No declarar GO_TO_DEPLOY_PREPARATION con P1/P2 materiales programables pendientes. External bounds con feature dependiente cerrada y sin claims pueden permanecer explícitos; no impiden completar los fixes ni justifican un GO operacional no demostrado.

Entrega final de desarrollo, completa y verificable:

- PR/branch/SHA/tree final, base vigente y ownership liberado.
- `INPUT_MANIFEST`, `FINAL_INPUT_PROVENANCE.json`, diff/requirements preservation y supersession de cada fuente.
- Registro de29U +21AUD, nuevos findings UX/integración y aceptación residual explícita si corresponde; ninguna fila perdida.
- RED/GREEN, pruebas adversariales/callers/cross-layer/full-suite/browser/stress con recibos y límites.
- RunID/attempt, artifactID/name/size/digest, manifests, ImageID y verificación ejecutada en imagen.
- Plan de esquema/migración/retención/config sólo si procede, dry-run y costo operativo calculado.
- Matriz implementado / validado CI / artifact validado / NO_DESPLEGADO / runtime NO_VERIFICADO / capacidad OPEN NO_VERIFICADO / edge NO_DEMOSTRADO.
- Una única orden concreta de Deploy V2 con inputs exactos, guards, postchecks, cleanup y contingencia fix-forward; nada de operaciones manuales para el usuario.

Estado: `READY_FOR_USER_DEPLOY_AUTHORIZATION` sólo cuando se satisfaga todo lo aplicable. Si queda un bloqueo externo real, entregar `BLOCKED_FOR_DEPLOY` con componente dependiente cerrado, evidencia de todo lo desarrollado y la condición exacta para levantarlo. No abandonar tareas independientes por ese bloqueo ni inventar aprobación.

## 12. Único deploy posterior, con aprobación sobre el resultado concreto

Esta sección prepara la ejecución; no la autoriza ahora. Tras la aprobación explícita del usuario del candidato/artefacto finales: revalidar product HEAD y owners, adquirir DEPLOY_OWNER global, ejecutar un Deploy V2 canónico con los inputs aprobados y el mismo artefacto validado. Sin promoción separada de motor, UX, history o fixes; sin rebuild en Droplet, force-push, merges de PRs históricos o cambio de identidad silencioso.

Postchecks exactos: container imageID y sourceSHA/tree/artifact, módulos nuevos y rutas UX presentes, config/env canónicos, modo PAPER, rutas reales NOT_CALLED, órdenes reales0, health de children/generaciones, schemas y readers, budget/EXIT, clocks y current committed cut, dashboard sin side effects, disco medido y cleanup seguro. Contrastar runtime real con el manifest; no basta workflow SUCCESS. PPI Watch permanece fuera de alcance.

Si falla, preservar evidencia, clasificar el fallo y reparar hacia adelante por nuevo candidato/artefacto: no restaurar código viejo ni ocultar un parcial como validación. Liberar ownership con handoff y estado exacto. No declarar VALIDADO_RUNTIME si sólo hay preopen o metadatos de imagen. Las mediciones de mercado OPEN, contratos/costos de cuenta,20 ruedas completas y OOS siguen siendo evidencia separada; ningún deploy las fabrica.

## 13. Qué no debe confundirse ni repetirse

- El diagnóstico de históricos sobre producto no implica que todo siga roto en #466: AUD-468-01 tiene fix probado en su caso. Tampoco implica que todo esté corregido:02/03/05 y residuales de store/SHADOW siguen relevantes.
- El texto histórico que dice que `promote_paper_candidate` reutiliza gates no cierra U06: la auditoría #469 mostró bypass económico por `_open`. Conservar el contraejemplo de mayor precisión.
- #466 y #470 no constituyen juntos una imagen final existente. Hay que integrar fuentes, corregir y construir el sucesor.
- No elevar el límite autorizado de disco por una discrepancia documental. Sí calcular necesidades del nuevo release y hacer cumplir preflight.
- No volver a ingestar veinte ruedas, reabrir PPI Watch, repetir builds antiguos ni borrar datasets/branches para obtener una apariencia GREEN.
- No aplicar modelos genéricos de equity a todas familias, usar source ranking sin base comparable, ni etiquetar rango/score como rentabilidad/probabilidad.
- No modificar la auditoría #469 para hacerla parecer favorable al sucesor. Un nuevo SHA recibe su propio dictamen y manifest.

Instrucción de arranque para Codex: verificá el paquete, leé esta orden completa, revalidá los heads y ownership, y ejecutá el sucesor fix-forward con un integrador único. Conservá cada requisito de los días 3–4 de octubre y la UX470. Cerrá toda la programación y validación posible antes de presentar el candidato concreto para el único deploy posterior.
