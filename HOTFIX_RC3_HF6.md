# POROTA TRADING 17.0.0-RC3-HF6 — CANDIDATO

Estado: **preparado para validación transaccional; no desplegado**.

Fecha de decisión y corte documental: 1 de septiembre de 2026.

## Procedencia obligatoria

- Runtime de partida: `17.0.0-rc3-hf5`, ya desplegado y validado.
- HEAD base documentado: `60cfb13e03f3bfe6dbfcbe3158dca9273279fb7e`.
- Árbol de partida: export efectivo HF5, no un checkout reconstruido desde GitHub.
- Payload HF5 de referencia: SHA-256
  `8ee0b216fec8f00cdd4eaa0afe6f09770c8611b55fd2a63fa07fa0d60a52a39d`.
- HF6 se entrega como delta con hash propio. No hace force-push, reset ni
  retroceso del árbol efectivo.

## Invariantes cerrados

- Sólo `PRODUCTION_PAPER / SIMULATED`.
- PPI Producción: market data read-only.
- `PPI_ORDERS=BLOCKED`; `real_orders_sent=0`.
- Motor Python; IA intradiaria OFF; noticias nuevas OFF.
- Economía matemática existente: `BINDING`.
- Riesgo general: 0,2 %; máximo general: 5; hold general: 360 minutos.
- Scalping exclusivamente PAPER.
- No se borra, repara ni reescribe DB, ledger, posiciones, históricos,
  velas, aprendizaje, secretos ni persistencia para mejorar resultados.

## Decisiones HF6 autorizadas

- Freno blando diario: 1,5 %, suspende aperturas sin liquidar.
- Corte duro diario: 2,5 %, conserva latch y salidas PAPER.
- Expectativa empírica con menos de 30 cierres: registrar y seguir operando.
- Régimen bajista o extremo: alertar sin bloquear.
- Concentración sectorial: observar sin límite vinculante.
- Retención de snapshots horarios locales: 90 días, sólo después de una
  consolidación diaria válida y una publicación GitHub confirmada.
- Scheduler de introspección y sincronización GitHub autorizado, con rama
  dedicada, avance lineal, allowlist y ejecución sin privilegios.

Estas políticas no convierten la economía a SHADOW: economía, patrimonio,
liquidez, riesgo y contratos continúan siendo portones vinculantes. Expectativa
y sector son `OBSERVATION_ONLY`; régimen es `ALERT_ONLY`.

## Cambios incluidos

### Riesgo y matemática

- Se separa el freno de nuevas aperturas del corte duro persistente.
- Se corrige el fallback de hold a 360 minutos en todos los caminos activos.
- Se calcula expectativa empírica neta con `Decimal`, por moneda y sobre hasta
  las últimas 100 operaciones de cada moneda. Nunca autoajusta parámetros.
- El dashboard deja de sumar resultados de monedas distintas como ARS.

### Operatorias y monedas

- El foco deja de ser una constante inmodificable y pasa a configuración
  versionada estricta.
- Se conservan las ocho identidades HF5 y se agregan `AAPLD` y `AAPLC` para
  observar/evaluar MEP y CCL. Catálogo, identidad monetaria, libro, costos,
  liquidez y todos los portones siguen siendo obligatorios.
- Todas las familias declaradas se muestran con cobertura y capacidad; ninguna
  se marca excluida por defecto. No se simula un contrato que PPI no haya
  permitido reconstruir de forma completa.

### Cauciones

- Se conserva el asignador PAPER existente y su contabilidad contractual.
- La introspección separa términos faltantes de oferta vigente y términos
  faltantes de política diaria.
- Una caución simulada anteriormente no se presenta como oferta reutilizable.
- No se inventan tasa, vencimiento, cupo, paso, sesión, principal ni comisión.

### Introspección, GitHub y dashboard

- Introspección horaria a los 15 minutos y entrada manual compatible
  `/usr/local/sbin/porota-introspeccion-hf5-manual.sh`.
- Corrección temporal SQLite mediante `julianday()`.
- Scalping muestra evaluados, rechazados, aprobados y fills sin duplicar compras.
- Se agregan embudo por moneda, PPI agrupado, workers, coherencia, disco,
  históricos, régimen, sectores, cauciones y capacidad por familia.
- El dashboard ya no reemplaza por cero el contador de órdenes reales leído de
  SQLite; una violación se muestra en rojo.
- Menú superior reducido y sección `Sistema`: Introspección, Salud y SRE,
  Configuración, Telegram y Logs.
- Refresco parcial accesible, preservando scroll y detalles abiertos.
- `latest.json` y un consolidado diario sanitizado se publican en la rama
  `runtime-observability`; GitHub nunca controla el runtime.
- El estado del publicador queda visible y un fallo se registra localmente.

### Ventas pendientes y disco

- “Ventas pendientes de liquidación” se diferencia de posiciones u órdenes
  abiertas y se separa PnL diario del acumulado.
- Inventario de disco clasifica antes de eliminar y no ejecuta prune ciego.
- La retención de 90 días elimina únicamente JSON/Markdown horarios ya
  consolidados; registra plan y resultado con SHA-256 y bytes.
- Imágenes de runtime, rollback, DB, WAL, ledger, históricos, velas,
  aprendizaje, informes, `.env` y secretos quedan protegidos.

## Despliegue y rollback

El instalador conserva HF5 vivo durante staging, build y suite completa. El
staging excluye datos y secretos; el payload se valida por SHA-256 y por hashes
de base/destino. La imagen HF6 compila y ejecuta `pytest` sin red. Sólo después
inicia el corte corto.

La aceptación exige: health y versión HF6, modo seguro, imágenes exactas,
SQLite `ok`, mismo inodo de DB, filas persistentes no decrecientes, coherencia
del ledger, latidos nuevos del observador y de todos los workers, parámetros
cerrados y cero órdenes reales. La fuente se persiste recién después. Si falla
antes de persistir, vuelve a la imagen HF5.

No hay backup automático, prune destructivo ni mutación Git del árbol fuente.

## Validación pendiente en el Droplet

- Suite completa dentro de la imagen, con `--network none`.
- Preflight vivo HF5, espacio/inodos y cero posiciones abiertas al corte.
- Confirmación read-only de disponibilidad real de ventas A-24HS en PPI.
- Primera observación de `AAPLD/AAPLC` y motivo exacto si no llegan a evaluar.
- Primera publicación GitHub y visibilidad del timer/estado.
- Inventario real de disco antes de decidir IDs concretos para retirar.

## Pendientes conservados

- Errores PPI recientes.
- Históricos 65/243 y 26 fallidos; próxima ingesta vencida/mal representada.
- Rotación completa aritméticamente no factible con la cadencia actual.
- Operaciones USD todavía sin evidencia; HF6 amplía foco y diagnóstico, no
  garantiza un fill.
- Contratos completos para bonos, letras, ON, opciones, futuros y cauciones.
- Configuración parcialmente duplicada.
- Consolidación/retención de informes fuera de snapshots horarios.
- Reconciliación Git del árbol efectivo mediante auditoría, rama y PR.
- Promoción Sandbox bloqueada.

ATR/primer pasaje histórico no se copió literalmente de la auditoría: el método
propuesto refería una fuente OHLC que el runtime no posee validada. Queda como
candidato offline hasta demostrar barras, procedencia y ausencia de look-ahead;
no se degrada el portón BINDING existente para aparentar una corrección.
