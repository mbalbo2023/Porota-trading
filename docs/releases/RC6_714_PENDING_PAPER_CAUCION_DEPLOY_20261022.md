# RC6 — caución PAPER pendiente y Deploy V2 (22/10/2026)

## RCA y decisión

Deploy V2 #39851933599 se detuvo en la clasificación anterior a cualquier escritura de host: `QUIESCENCE_OPEN_POSITION`. La auditoría READ_ONLY #39852269848 observó una sola caución ARS `OPEN`, iniciada 22/10 16:30 ART y con vencimiento contractual 23/10 17:00 ART, cero posiciones spot/futuros, `PRODUCTION_PAPER` y `real_orders_sent=0`. La protección de libro plano era correcta para una exposición desconocida; carecía de una ruta para preservar una colocación simulada válida hasta su vencimiento. Reetiquetar `MATURED` o inventar un cierre anticipado habría alterado caja, devengo, PnL y el linaje de la asignación. Esta corrección no escribe en la DB financiera.

## Guard de continuidad

La excepción `CLOSED_CAUCION` sólo se admite con los dos contenedores propios en marcha, observador fresco en `WAITING_MARKET/MARKET_CLOSED`, exactamente una caución `PRODUCTION_PAPER` ARS `OPEN` de este incidente, apertura 22/10 16:30 ART, vencimiento original 23/10 17:00 ART aún futuro, ninguna otra exposición y ninguna orden real. Valida contrato/economía mediante `validate_position` y la asignación ligada con `_validated`. Una fotografía de la fila completa y su recibo de asignación, con SHA-256, queda en el recibo duradero de quiescencia. Se repite antes y después de detener, antes de instalar, después de reiniciar y al persistir `verify-carryover`. Cualquier diferencia o vencimiento transcurrido falla cerrado; la fecha nunca se adelanta.

Predeploy V2 ejecuta el replay de transición y las pruebas de la suite. La regresión construye un libro PAPER real con asignación interna válida, conserva `OPEN` y caja inmovilizada, rechaza alteración del principal antes de instalar y del hash de asignación tras reiniciar. Los paths de órdenes reales y PPI Watch no cambian. Backups permanecen suspendidos; no se incorpora estrés host ni rollback.

## Evidencia de promoción pendiente

### FIX-FORWARD #715 tras gate fail-closed

PR #714 se fusionó como `19717edee6452ca8eddb8a46bd8606c6102ba6a2` sobre el árbol exacto `827dd5c0ac105b608ed38b3d954ff1c374c0eb6a`, certificado por Predeploy V2 #39858433352: 4549/4549, cero fallos/errores/skips/xfail; cinco nativos `0|false`; artifact #12461707687 digest `sha256:a5db82cdf162d0a9ae7f25572aaaac4390447c9d2ba678d93731597e911014bd`. Deploy V2 #39861582846 falló de nuevo **antes de escribir** al validar la asignación con `ELIGIBLE_TERMS_MISMATCH`; sólo limpieza local de runner.

Diagnóstico READ_ONLY #39862094973 (22:04 UTC) confirmó en el Droplet: una caución abierta, cero spot/futuros, PAPER/0, contrato validado, arancel cotizado y capital de arancel ausentes, autoridad PAPER de arancel válida, capital y costo seleccionados iguales al asiento. La causa es `cb_caucion_audit._validated`: exigía `principal == fee_quote_principal` aun en la ruta explícitamente autorizada de arancel PAPER, donde esa base es `None` y el selector calcula capital dentro de límites. La corrección #715 admite sólo `quoted_total_fees is None`, `fee_quote_principal is None` y `_paper_tariff_authorized(offer)`, y conserva el resto de la reconstrucción del plan, economía, manifest, contrato, monto, cronología y ledger. Un presupuesto explícito sigue requiriendo capital exacto.

Para admitir la sesión antes de instalar, Deploy V2 ejecuta **en memoria** el auditor del checkout candidato exacto (base64, sin escritura de host); el script de drain transmitido por stdin y el script staged usan los mismos bytes. El SHA-256 del auditor queda en el recibo y debe coincidir tras instalar. La regresión ahora reproduce una asignación con arancel PAPER versionado, verifica `READABLE` y rechaza cambio de capital y hash del recibo. No se canceló ni maduró la caución, no se creó fill ni se modificó caja para franquear el deploy.

Anotar aquí el SHA/tree candidato, PR, run/artifact/digest exactos de Predeploy V2, autorización #624, merge de dos padres, Deploy V2, imagen/runtime, controles y cleanup. Hasta entonces el cambio está EN_GITHUB sólo si se publica el PR; no es `VALIDATED_RUNTIME`. La auditoría integral #713 sigue `NO_GO` por linaje anual y evidencia de capacidad OPEN, aun si esta publicación alcanza `VALIDATED_RUNTIME`.
