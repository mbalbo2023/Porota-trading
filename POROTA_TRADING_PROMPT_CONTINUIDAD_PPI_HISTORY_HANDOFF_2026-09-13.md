# POROTA TRADING — PROMPT DE CONTINUIDAD PPI HISTORY / PPI WEB RESIDUAL — 2026-09-13

## Instrucción al nuevo chat

Estás continuando exactamente el trabajo de POROTA TRADING del chat anterior. No reconstruyas decisiones desde cero, no repitas pruebas ya cerradas y no cambies arquitectura sin evidencia. Lee primero este archivo completo y luego `POROTA_TRADING_CHECKPOINT_CANONICO_PPI_HISTORY_HANDOFF_2026-09-13.md` en la rama `ops/rc6-ppi-web-residual-ready-20260913`.

También son referencias obligatorias:

- `POROTA_TRADING_CHECKPOINT_PPI_WEB_RESIDUAL_HANDOFF_2026-09-13.md`
- `POROTA_TRADING_CHECKPOINT_DATA_LIFECYCLE_INGESTION_PENDING_2026-09-13.md`
- `POROTA_TRADING_CHECKPOINT_CONTRACT_COVERAGE_PENDING_2026-09-13.md`
- `POROTA_TRADING_CHECKPOINT_PPI_HISTORY_SEMANTICS_2026-09-12.md`

El checkpoint canónico nuevo manda sobre estados temporales anteriores.

---

## Contexto esencial

Proyecto: bot conservador para mercado bursátil argentino, broker PPI, RC6, Python 3.11, Docker/Compose, SQLite, dashboard, Telegram. IA intradiaria OFF; motor de decisión Python. Modo requerido: `PRODUCTION_PAPER`; órdenes reales siempre 0 durante este trabajo.

Objetivo de esta línea de trabajo: completar histórico previous-365d del universo canónico PPI usando todas las familias posibles y con precedencia PPI API -> PPI Web residual -> IOL residual final, sin datos sintéticos y sin dos writers históricos simultáneos.

Universo canónico exacto: 1960 identidades.

La fase PPI API ya cerró: 1960/1960 tareas, 0 activas, servicio API histórico inactivo. Residual API congelado: 679. De ese residual, 642 son runnable PPI Web y 37 FCI están deferred con política explícita, no DONE_EMPTY.

Run PPI Web actual: `PPI-WEB-RESIDUAL-20260913-001`.
Servicio: `porota-ppi-web-residual-rc6.service`.
Root runtime: `/opt/porota-ingest/ppi-web-residual`.
Estado durable: `state.sqlite3` + `status.json` + journal systemd.

El scraper PPI Web YA FUE INICIADO y después de un hotfix mostró progreso real. No vuelvas a arrancar otro writer.

Último snapshot validado del chat anterior:

`RUNNING | TOTAL=642 | TERMINAL=5 | DONE_PARTIAL=5 | PENDING=634 | RUNNING=3 | PROGRESS=0.78% | CURRENT=AL35C/BONOS/BYMA/A-24HS | SAFETY=PRODUCTION_PAPER|0`

Este snapshot es sólo punto de partida; puede haber cambiado.

---

## PRIMERA ACCIÓN OBLIGATORIA DEL NUEVO CHAT

Antes de tocar nada, obtener estado runtime actual en modo read-only mediante GitHub Actions/SSH ya probado. Debes verificar y reportar:

`RUN_STATUS | TOTAL | TERMINAL | PENDING | RUNNING | DONE_VALID | DONE_PARTIAL | DONE_EMPTY | ERROR | PROGRESS_PCT | CURRENT | HEARTBEAT | FCI_DEFERRED | SERVICE | NRESTARTS | SAFETY`

Fuentes de verdad:

1. `systemctl` del servicio PPI Web.
2. `/opt/porota-ingest/ppi-web-residual/status.json`.
3. `/opt/porota-ingest/ppi-web-residual/state.sqlite3` en read-only.
4. `journalctl -u porota-ppi-web-residual-rc6.service`.
5. servicio PPI API histórico debe seguir inactive.
6. safety debe seguir `PRODUCTION_PAPER|0` y 0 órdenes reales.

No uses sólo el resultado de un workflow como evidencia funcional.

---

## Qué ya se corrigió y NO hay que redescubrir

1. Auth trusted PPI Web y SSO intermedio ya funcionan.
2. Chrome/browser lock ya fue resuelto/serializado.
3. Endpoint histórico interno PPI Web ya fue descubierto y probado.
4. Manifest residual ya está congelado con hashes y no debe regenerarse durante el run.
5. Error de lectura de manifest por permisos ya fue corregido usando sudo para runtime protected files.
6. El falso “service active pero sin progreso” se diagnosticó: collector devolvía UNKNOWN porque el root `batches/` no era atravesable por `porotaadmin`.
7. Hotfix del root de batches ya se ejecutó y validó con progreso real.

Commit hotfix: `d1dd7d0c0c1cc4971639e515c16fce7b81f0c5c0`.
Run hotfix: `34743873661`.
Marcadores finales: `BATCHDIR_HOTFIX=GREEN`, `SCRAPER_REAL_PROGRESS=YES`.

---

## Reglas operativas obligatorias

- Nunca iniciar dos historical writers.
- No relanzar full PPI API; esa fase está cerrada.
- No regenerar manifest residual durante el run.
- No borrar `state.sqlite3` ni limpiar batches a ciegas.
- No resetear estados sin diagnóstico de causa.
- Mantener los 18 productores/timers pausados hasta cierre integral.
- FCI deferred no equivale a resuelto.
- Histórico no equivale a READY_PAPER.
- PPI API válido tiene precedencia sobre PPI Web; PPI Web sobre IOL fallback.
- No interpolación, repair ni síntesis de OHLC.
- Settlement 48HS debe tratarse como legacy cuando corresponda; operación actual normal BYMA es 24HS/T+1 y CI/T+0. Usar `PlazosOperables` real.
- No fuzzy matching silencioso de instrumentos. Alias conocidos deben ser explícitos, p.ej. `MRCTO -> MRCAC`.

---

## Seguridad absoluta

PPI Web sólo read-only. Permitido navegar, descubrir, consultar cotizaciones, históricos y metadata. Prohibido cursar operaciones, modificar órdenes, operar cauciones/FCI/opciones/futuros/licitaciones, modificar cuenta/seguridad/2FA o exponer material sensible de autenticación.

Safety gate deseado antes y después de acciones relevantes:

`ok|PRODUCTION_PAPER|0`

o en gates con API task counts:

`ok|PRODUCTION_PAPER|0|0|1960`

Si safety cambia, detener y diagnosticar; no continuar.

---

## Plan de acción completo desde aquí

### Fase A — terminar PPI Web residual actual

1. Leer status actual.
2. Si RUNNING y heartbeat avanza, NO tocar el servicio; sólo monitorear.
3. Si hay ERROR, agrupar por causa y atacar causas, no síntomas.
4. Si el servicio reinicia, verificar recuperación de huérfanas y que no haya doble writer.
5. Continuar hasta 642 tareas terminales.
6. Conservar counts y details por estado/familia.

### Fase B — reconciliación post-Web

1. Verificar PPI API válido protegido por precedencia.
2. Validar provenance `PPI_WEB_HISTORY`.
3. Confirmar previous365d exacto.
4. Confirmar duplicados cero o explicar excepciones.
5. Confirmar cero síntesis/reparación.
6. Generar manifest/residual post-Web inmutable.

### Fase C — IOL último fallback

1. Usar IOL sólo sobre el residual post-Web.
2. No sobreescribir PPI API/PPI Web válido de mayor autoridad.
3. Registrar source/provenance y resultado por identidad.
4. Recalcular residual final.

### Fase D — FCI / FCI Exterior

Resolver las 37 deferred: discovery estable, IDs/claseFCIId, semántica, histórico/frecuencia, NAV/cuota/rendimiento/cotización, reglas y mínimos read-only. No marcar DONE_EMPTY por discovery incompleto.

### Fase E — semántica histórica y settlement

1. Hacer overlap PPI Web vs PPI API sobre identidad realmente operable 24HS/CI.
2. Comparar O/H/L/C/V en fechas comunes con tolerancia definida.
3. Confirmar semántica de close/`ultOperado`.
4. Formalizar tratamiento legacy 48HS vs actual 24HS sin duplicar series artificialmente.

### Fase F — contratos / READY_PAPER

Por cada familia cerrar cinco dimensiones: DISCOVERY, HISTORICO, CONTRACT_METADATA, OPERABILITY_RULES, READY_PAPER. Atención especial a cauciones, opciones, futuros, FCI, ON, letras, bonos, CEDEARs, acciones, ETFs, licitaciones, índices y cualquier familia PPI detectada.

### Fase G — lifecycle continuo de datos

1. Estrategia delta con watermark por fuente/instrumento.
2. Idempotencia y dedupe por identidad/fecha/source/hash.
3. Gap/stale detection y refresh selectivo.
4. Rolling 365d con decisión archive/prune.
5. Preservar provenance, hashes, attempts y evidencia de auditoría.
6. Monitoreo de disco y excepciones de lookback si algún modelo necesita más de 365d.

### Fase H — revisar productores/timers y fuentes

Los 18 timers están pausados. Auditar uno por uno propósito, fuente, frecuencia, universo, settlement, dedupe, stale/error, semántica, solapamiento, costo, calendario, delta/365d, restart/resume y observabilidad. No reactivar por mero systemd green.

Construir matriz de utilidad de PPI API, PPI Web, IOL, BYMA/A3, Data912, Yahoo y otras; conservar sólo lo que aporte autoridad, cobertura, resiliencia o validación útil.

### Fase I — calendarios de mercado

Cerrar pendiente de doble calendario Argentina/EE.UU. para CEDEARs, acciones USA, ETF y otros instrumentos dependientes del mercado externo, evitando decisiones cuando corresponda un feriado externo.

### Fase J — cierre integral

Antes de reactivar productores exigir: universo reconciliado, histórico consolidado, duplicados/gaps/stale auditados, safety PAPER|0, real orders 0, writers no superpuestos, FCI resuelto o política explícita aprobada, contratos/operabilidad cerrados, source utility review cerrado y lifecycle/delta listo. Reactivar timers uno por uno con evidencia funcional.

---

## Preferencias de trabajo del usuario

- Informar con semáforo y cifras concretas, no “está en curso” sin detalle.
- Workflow green no equivale a funcional green: siempre exigir evidencia runtime.
- No inventar componentes nuevos si existe stack probado; reutilizar primero.
- Priorizar GitHub Actions para diagnósticos/deploy/validaciones. Terminal manual sólo si es indispensable.
- Si alguna vez hace falta terminal, entregar script `.sh` descargable y compacto, pensado para control por voz/Termius; no exigir escritura manual extensa.
- No ZIP salvo pedido expreso.
- No tocar trabajo paralelo de otros chats si no es necesario.

---

## Criterio de continuidad transparente

El nuevo chat debe comportarse como continuación directa, no como auditor externo sin contexto. Debe conservar decisiones ya tomadas, señalar cualquier dato nuevo que contradiga un checkpoint y actualizar checkpoint/prompt si cambia un estado importante.

Primer mensaje recomendado del nuevo chat, después de leer archivos y consultar runtime:

“Contexto recuperado. Estado runtime actual: [semáforo + conteos + heartbeat + current + safety]. Continúo desde PPI Web residual sin iniciar un segundo writer.”
