# A200 — integración A199 de seis frentes, sin promoción

**Contrato de control:** handoff [A199](https://github.com/mbalbo2023/Porota-trading/issues/732#issuecomment-6529455070), antecedente A198, issue #732, PR [#772](https://github.com/mbalbo2023/Porota-trading/pull/772), issues #485, #624 y #478. Base exacta previa de #772 `af9998cc21eb21bfc24c7b5859b7cc617d7513f9`; base de producto `541882bc0ceb0f3405ab71238a1b66a33ae10316`. Las seis asignaciones exclusivas constan en el [acta de ownership A200](https://github.com/mbalbo2023/Porota-trading/issues/732#issuecomment-6529832783). El integrador es el único owner del candidato.

Esta acta describe **código candidato**, no demuestra estado de host. El PR se mantiene **Draft**; ninguna de estas modificaciones activa Deploy V2, hace merge a la rama productiva, cambia el Droplet, rota credenciales o envía órdenes. Se preservan `PRODUCTION_PAPER` / `SIMULATION`, SHADOW y `real_orders_sent=0` como requisitos invariantes, no como una medición de runtime posterior al despliegue. PPI Watch está excluido del scope.

## Alcance y causa-raíz por frente

| Scope / owner | RCA confirmado y defensa nueva | Prueba de código / límite de autoridad |
|---|---|---|
| FI `/fi` | El ledger A198 ya reducía cupones y amortización, pero su caja y nominal residual no estaban conectados al portfolio PAPER. `rc6_fixed_income_portfolio_bridge.py` valida lote y recibos, segregación por moneda/PIT y costo amortizado; `rc6_fixed_income_risk_projection.py` proyecta pérdida conservadora, resultado y bloqueos de ejecutor. El motor compartido carga migración FI al iniciar, acredita una sola vez los pagos, conserva el BUY íntegro, valúa el residual, reduce sizing/riesgo concurrente y prohíbe SELL genérica sobre lotes con ledger económico. `bw_daily_risk.py` incorpora el resultado FI en PnL diario sin inventar FX ni cobro. | Regresiones de identidad, clocks, reaplicación, efectivo, amortización total/parcial, fila corrupta y sidecar huérfano. **No** hay emisor, OOS, salida nativa ni autorización económica real. Las pruebas son sintéticas. |
| CAUCIONES `/cauciones` | El cash-sweep restaba otra vez reservas ya descontadas por los eventos contables FCI/FUTUROS en `PaperBroker._cash`. `di_caucion_cash_sweep_runtime_hf6.py` reconcilia eventos y metadatos, preserva reservas legacy no reconciliadas y bloquea incoherencias/duplicados. | Pruebas adversariales sobre saldos positivos, cero y negativos. No se modifica PPI BID, feed ni tarifa real; revisiones exactas de cinco cambios de autoridad permanecen sujetas a evidencia. |
| SCALPING y spot `/scalping` | Los 857 rechazos de revisión histórica no contaban con pares forenses verificables. `cf_intraday_scalping.py` conserva hasta 64 muestras hash/día y cuatro por identidad, sin filtrar payloads ni relajar HOLD; A198 ya había corregido comparación Decimal de `100` frente a `100.0`. | Las pruebas de regresión preservan rechazo. Los 1272 HOLD por contrato de volumen no se desbloquean sin semántica PPI verificable; 452 respuestas vacías tampoco constituyen señal. OPCIONES long no se mezcla en Scalping. |
| FCI, ETF y exterior `/fondos` | Faltaba aislar de manera verificable identidad exacta de clase, NAV, clocks, cutoffs, tarifas y contradicciones de fuentes. `rc6_fund_source_quarantine.py` añade la revisión fail-closed sin aceptar declaraciones de autoridad autoproclamadas. | No produce compras: `RUNTIME_ENTRY_AUTHORITY=False`. Siguen sin productor autenticado/scheduler de recibos FCI, condiciones ETF por plaza y ciclo exterior/FX para ACCIONES_USA y FCI_EXTERIOR. |
| FUTUROS e ÍNDICES `/futuros` | Contar identidades descubiertas como contratos negociables era una confusión de readiness. `rc6_future_index_readonly_audit.py` inspecciona identidad PPI, fechas, duplicados, vencimiento y bloqueos con digest sin poder hacer órdenes. | Se reutiliza, no se duplica, el motor DLR PAPER existente de margen/variación/cierre. Los índices siguen siendo contexto; los márgenes simulados no acreditan riesgo/costos de la cuenta. |
| Infraestructura, fuentes y sesiones `/plataforma` | Un subproceso de fuentes podía devolver exit 0 sin publicar un output nuevo, y el dispatcher avanzaba TTL sobre archivos viejos. `scripts/rc6_source_cycle_dispatch.py` exige recibo de publicación atómico. La circular BYMA COM19024 hace efectivo el horario segmentado el **02-Nov-2026**, mientras el timer de fuentes terminaba 16:59: se agrega una ventana tardía **sólo de observación IOL** entre 17–18, sujeta a contrato PPI exacto BYMA/A-24HS/PPI_FIELD para ACCIONES/CEDEARS/OPCIONES, control de feriados y revisión hasta 31-Dec. OAuth en esa ventana es read-only/no-refresh, con terminación 401/403. | No se extiende el horario general de órdenes, PPI Watch, cauciones, contado inmediato, bonos, futuros, publicaciones públicas ni scheduler PAPER. Si catálogo no demuestra contrato exacto, resultado NOT_DUE. Timer nuevo sólo está en candidato, **no instalado** en host. |

## Causa → guard → prueba: controles transversales

1. **FI principal duplicado:** original spot BUY sigue comprometido; ingreso FI se reconoce sólo por evento conciliado y moneda de pago. La posición vencida **no** produce `SELL_SIMULATED`; cierre nativo sigue explícitamente bloqueado. Pruebas de idempotencia, saldo, equity, remanente y sin órdenes reales sobre fixtures.
2. **FI riesgo no integrado:** el nominal residual alimenta exposición, valoración y reserva conservadora (pérdida total residual si no hay salida certificada); PnL diario incorpora realizado del ledger y coste del remanente sin FX implícito. La falta de marca o cruce de moneda conserva cierre de admisión; no se acredita liquidez arbitraria.
3. **Cauciones doble reserva:** se reconcilian receipts reales del ledger PAPER antes de restar; eventos insuficientes se quedan en la ruta histórica conservadora. Test negativo/positivo y secuencias temporales.
4. **Salida de fuente obsoleta:** exit code 0 no equivale a publicación; firma/hash y archivo atómico nuevos son condición para subir TTL. La fuente opcional degradada produce AMBER, no veto global del despliegue.
5. **Reloj BYMA segmentado:** nunca usar una regla de cierre de mercado universal cuando la circular distingue plazo/activo. El corte ampliado es observación de cotizaciones IOL secundarias exactas, sujeto a fecha/calendario; cualquier otro mercado falla cerrado.
6. **Identidades y datos de fuentes:** PPI tiene primacía para identidad/local book; IOL complementa sin sobrescribir ni autenticar reglamentos, márgenes, comisiones o flujos del emisor. Estados de catálogo y snapshots no equivalen a READY ni a fills.

## Matriz de familia y motor resultante

| Familia | Motor PAPER / situación A200 | Pendiente para declarar `VALIDADO_RUNTIME` |
|---|---|---|
| ACCIONES | Scanner spot + supervisor; Scalping separable; fuente PPI primaria | Marca, libro, riesgo y ciclo de salida frescos y evidencia de resultado real del candidato desplegado |
| CEDEARS | Spot + supervisor; filtro exacto IOL tardío de referencia | Igual que acciones, sesión/subyacente correspondiente |
| OPCIONES | Contrato/estrategia long + ledger/supervisor; observación tardía BYMA si T+1 exacto | Fills e integración con prima/derechos reales del contrato, nunca short por inferencia |
| BONOS | FI producer/ledger económico + puente de caja, residual, valoración y riesgo PAPER | Documentos del emisor/OOS reales, recibos, liquidación y ejecutor nativo de venta/redención |
| LETRAS | FI descuento y nominal; ruta FI puente fail-closed | Precio, vencimiento, settlement, redención/cierre nativo y costos efectivos |
| OBLIGACIONES | FI cupón/amortización y nominal residual; bridge fail-closed | Flujos/entitlement y condiciones ON reales, salida nativa |
| CAUCIONES | Libro PPI BID → sweep PAPER → ledger vencimiento; guarda nueva de caja | Observación actual PPI, autoridad de tarifas/cambios exactos y alocación real en PAPER sin estrés |
| FCI | Estrategia/allocator/reconciler existentes y nueva cuarentena de fuente | Productor auténtico por clase, NAV/cutoff/tarifa/recibo y wiring de scheduler |
| FUTUROS | Especializado DLR PAPER; auditor contractual de todos los futuros | Contratos, margenes reales, series vencimiento y clearing para familias adicionales |
| INDICES | Contexto de señales, auditor de identidad/readiness | Índice no negociable: requiere contrato derivado/ETF específico |
| ETFS | Spot bajo gates genéricos; datos de plaza bloqueados | Identidad de plaza, mínimos, tarifas, liquidación y custodia |
| ACCIONES_USA | Discovery PPI sin ejecutor exterior acreditado | Cuenta, plaza, FX, sesión, comisión, settlement y ruta segura |
| FCI_EXTERIOR | Discovery sin productor ejecutable | Reglamento de clase exterior, NAV, FX, cutoff, rescate/caja |
| SCALPING | Intraday PAPER, HOLD causal y forense acotado | Contrato PPI de volumen, pares reales de revisiones y datos no vacíos |

## Verificaciones y promoción

- A198 previo: [Action 40996460648](https://github.com/mbalbo2023/Porota-trading/actions/runs/40996460648) **SUCCESS, 5843/5843** sobre `af9998cc`; no es evidencia de este A200.
- A200 pruebas locales: cada frente informó pruebas focalizadas; el integrador ejecutó regresiones cruzadas, sintéticas y control de diff. Las pruebas locales no sustituyen la suite CI gobernada sobre SHA final.
- La suite completa local requiere dependencias de `requirements.txt` (la primera ejecución offline sin ellas presenta errores de colección por `fastapi` y `python-dotenv`); ejecutar CI remoto con dependencias bloqueadas y conteo completo sobre el SHA exacto, registrar run/artefacto/hash y diagnóstico de cualquier fallo antes de solicitar GO.
- Mantener PR #772 **Draft**; no hacer merge a producto, Deploy V2, transición de ownership global de despliegue ni limpieza Docker. Predeploy V2 sólo corresponde a promoción posterior y aprobación humana explícita para el único deploy consolidado.
- La integridad física de retención SQLite de 365 días (#478) exige grafo de pins, evidencia runtime y ventana autorizada; **no** se hizo purga/backup/cambio de DB.
- `#624`: autorizaciones anteriores por SHA no cubren este nuevo commit; NO se reutilizan para omitir el GO nuevo solicitado.

## Pendientes y semáforo para la decisión

**Verde de código** sólo si CI gobernada A200 completa con 0 fallos/errores/skips y SHA exacto publicado; el output de Actions es prueba de pipeline, no de economía real. **Ámbar**: Scalping volumen, recibos de FI/FCI, cinco transiciones cauciones, tarifas reales, contratos futures fuera DLR, ETF/exterior, y falta de muestra runtime sobre este commit. **Rojo para promoción:** no hay GO humano ni prueba de runtime del candidato; no autorizar operaciones reales ni activar Deploy V2 sin el ciclo acordado. Estado de trabajo pendiente no puede convertirse en READY por ausencia de evidencias.

## Fuente externa oficial

- [BYMA COM19024 — horarios 2026](https://cdn.prod.website-files.com/6697a441a50c6b926e1972e0/6ab2dda27ea9e2c80e659a15_BYMA-COM19024-Horarios_de_Negociacion_Liquidacion_Recepcion-v2.pdf), PDF SHA256 `243aa692439f225d02ed1aac9648a6b224df8cab5a1bc18f3f05f20bcfe4ab98`: 02-Nov-2026, renta variable contado 24h/opciones 18:00, contado inmediato/bonos/cauciones 17:00; segmentos de futuros separados. Los límites de calendario/hora requieren revalidación antes de cualquier vigencia posterior a 31-Dec-2026.
