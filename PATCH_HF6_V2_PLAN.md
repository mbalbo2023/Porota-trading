# POROTA HF6 — PATCH CONTRACT EVIDENCE V2 / INGESTA / UX / STORAGE

> Documento WIP. No constituye autorización de deploy.

## Invariantes

- Runtime objetivo: `PRODUCTION_PAPER` / `SIMULATED`.
- PPI órdenes reales: bloqueadas.
- `real_orders_sent=0` es invariante.
- IA intradía: OFF; el motor durante rueda es Python determinístico.
- Los archivos históricos de IA pueden permanecer por trazabilidad, pero la IA intradiaria queda formalmente deprecada y no participa de la decisión.
- El tag `v17.0.0-rc3-hf6` es inmutable.
- Ninguna familia pasa a READY_PAPER por inferencia.
- Antes de deploy se presenta el alcance exacto y se requiere autorización explícita del usuario.

## Autoridad de fuentes durante la decisión

- PPI es el broker y la fuente live primaria de Porota.
- Si PPI tiene el snapshot live completo, fresco y válido requerido por una decisión, Porota decide con PPI y no consulta A3/CEM/Data912 en el hot path.
- Una divergencia background A3/PPI no puede dejar una operación PPI en HOLD ni bloquearla.
- A3/Primary/CEM se usan para contratos, históricos, reference data y auditoría asíncrona. Una eventual promoción de A3 como fuente live para una familia completa requerirá evaluación separada y autorización explícita.
- Data912 es exclusivamente histórico/contextual batch; `DATA912_EXECUTION_ALLOWED=false`.
- Nunca se mezclan bid/ask/last de dos proveedores para fabricar un único snapshot ejecutable.

## Alcance autorizado para preparación

1. Contract Evidence v2, versionado, con TTL, hash y estados VERIFIED/MISSING/STALE/CHANGED_REVIEW_REQUIRED/CONFLICT/POROTA_INTEGRATION_REQUIRED/PPI_SUPPORT_REQUIRED/READY_PAPER_CANDIDATE/READY_PAPER/FAIL_CLOSED.
2. Ingesta structured-first desde endpoints PPI oficiales descubiertos; Chrome/Playwright sólo como auditoría/enriquecimiento, nunca en el hot path.
3. Reparación de históricos hoy estancados en 65/243. Ya se probó que los 243 fueron intentados: 65 VALID_PAYLOAD, 169 PARTIAL y 9 EMPTY_OR_INVALID. Los 169 parciales contienen 31.654 velas PPI válidas recuperables; una fila defectuosa no invalidará el resto del payload.
4. Desacoplar `INGESTA_HISTORICA` de `READY_PAPER`: una familia HOLD puede acumular históricos sin quedar habilitada para operar.
5. History Store v2 con identidad `symbol + instrument_type + market + settlement + date`, versiones append-only y precedencia de fuente; no usar la clave heredada `symbol+date` para nuevas familias.
6. Data912 Historical Auto-Reconciliation post-cierre para ACCIONES/CEDEARS/BONOS, dirigido por el universo de Porota y sólo cuando falta contexto. Nunca sustituye PPI live ni pisa una vela canónica de fuente superior.
7. A3/CEM público GET-only para históricos/reference de derivados; reMarkets autenticado queda pendiente mientras el entorno/credencial no autentique. No bloquear el patch por ese acceso.
8. Sesiones de mercado por familia/mercado, sin `MARKET_OPEN_HOUR` global.
9. P1-6..P1-9 de la auditoría: adaptadores para Opciones, Futuros, Cauciones, FCI local, Letras, ON, ETF exterior y demás familias encontradas; HOLD hasta contrato e integración completos.
10. UX/UI: reorganizar Trading por familias/estrategias; eliminar duplicaciones; exponer readiness contractual; Scalping deja de ser excepción arquitectónica.
11. Sistema/Logs: últimas 50 líneas/eventos, origen, timestamp y descarga. Exportador host sanitizado; el dashboard no recibe Docker socket ni privilegios de systemctl.
12. Sistema/Scheduler: jobs internos y timers host, explicación, estado, última ejecución, último éxito/resultado, próxima ejecución y condición cuando corresponda.
13. Panel/Resultado diario: últimas jornadas con ganancia/pérdida, PnL y retorno por moneda, cantidad de fills/cierres, instrumentos/familias operados y resumen de acciones. Nunca sumar ARS y USD para fabricar un resultado único.
14. Reportes y listados: cards responsivas, resúmenes y paginación; evitar tablas anchas y render masivo de cientos de filas. No exigir desplazamiento horizontal en la experiencia principal de tablet/móvil.
15. Preopen HF2 obsoleto: retirar como gate operativo de forma reversible y reemplazar por readiness/sesiones/versionado sin falsos NO_GO.
16. Settlement: no alterar una contabilidad que ya concilia; mejorar UX con `available_at`, fuente y separación de lo que liquida hoy/próximo día hábil. No liberar caja antes de evidencia oficial.
17. Storage lifecycle: inventario → clasificación → validación → limpieza → medición. Nunca `docker system prune -a` ciego.
18. Modelo matemático y aprendizaje: incorporar evidencia de cierre, MFE/MAE, duración, costos, spread, profundidad, causa de salida, expectativa y comparación replay antes de promover cambios de estrategia.

## Riesgo concurrente dinámico

El límite financiero normal deja de ser un número pequeño fijo de posiciones abiertas.

- El runtime HF6 actual tiene `PAPER_MAX_OPEN_POSITIONS=5`; ese valor se depreca como gate financiero normal.
- El presupuesto normal de riesgo concurrente se deriva del `PAPER_DAILY_SOFT_STOP_PCT` y de la base patrimonial diaria conciliada por moneda.
- Se suma el downside conservador hasta stop de todas las posiciones abiertas, incluyendo fricción de salida modelada.
- La pérdida diaria ya consumida reduce la capacidad restante.
- Las ganancias diarias no amplían la capacidad de riesgo.
- La candidata entra sólo si su riesgo adicional cabe en la capacidad restante y además supera todos los portones de caja, exposición, ticker/sector, liquidez, contrato, costos, horario y riesgo diario.
- Se conserva un límite técnico de emergencia alto únicamente como protección runaway/bug; no debe ser el motivo normal de rechazo.
- El dashboard mostrará posiciones abiertas, riesgo concurrente consumido, capacidad restante y soft stop en vez de presentar principalmente `N/5`.

## Cash sweep de cauciones al cierre

Se prepara un barrido de caja PAPER para evitar efectivo ocioso al final de la rueda.

- Sólo usa caja liquidada de la misma moneda.
- Resta compromisos y una reserva requerida calculada antes de dimensionar.
- El scheduler se deriva de un cutoff oficial/verificado de cauciones; no se hardcodea un horario recordado.
- El candidato exige TNA, mínimo, step, profundidad, costos, day-count, vencimiento, cutoff y metadata contractual verificadas.
- La liquidez debe regresar antes del deadline requerido para la siguiente sesión; si esto no puede probarse, no cauciona.
- El retorno debe ser neto positivo luego de costos.
- El planner sólo produce `PAPER_CANDIDATE`; la colocación continúa detrás de los gates PAPER normales.
- El primer deploy queda FAIL_CLOSED hasta completar Contract Evidence de cauciones.
- El resumen diario mostrará capital caucionado, neto esperado y vencimiento cuando exista.

## Cadencias objetivo

- Market data live: ciclo PPI existente.
- Catálogo/operabilidad PPI structured: 15 min en rueda.
- Cauciones/licitaciones activas: 5 min.
- Contrato estático: preapertura + postcierre + hash diario; browser full sólo cuando corresponda.
- Series futuros/opciones: 15 min durante sus respectivas sesiones.
- Históricos: cola de baja prioridad + reconciliación pesada post-cierre. Data912 no se consulta por cada decisión.
- Data912: reconciliación histórica batch post-cierre, alrededor de la publicación diaria; retries dirigidos para faltantes.
- CEM A3: históricos/reference en batch; nunca gate live.
- Playwright completo PPI: semanal o ante cambio/conflicto estructural; nunca en el hot path.

## Política histórica

El estado 65/243 no se considera suficiente ni representa el universo final multi-familia.

- 243 = 55 ACCIONES + 188 CEDEARS de la cola heredada.
- 65 VALID_PAYLOAD.
- 169 PARTIAL, con ratio válido mediano 0,853659 y mediana de 203 velas válidas.
- 157 de los 169 parciales ya tienen al menos 90 velas válidas.
- 103 tienen al menos 180 velas válidas.
- 9 EMPTY_OR_INVALID requieren retry/fallback dirigido.
- El umbral fijo de 250 velas no se usa como readiness de un año calendario porque muchos payloads anuales completos contienen alrededor de 244–246 ruedas.

La nueva política clasifica contexto histórico por cobertura y calidad: mínimo 30, preferido 90, fuerte 180, además de rango temporal/densidad. No se rellenan huecos sintéticamente. Las filas inválidas permanecen rechazadas y auditadas.

El ledger de intentos nuevo es append-only. La tabla heredada `production_history_attempts`, cuya PK reemplaza el intento anterior, se mantiene sólo por compatibilidad durante la transición.

## Storage

- `observer_v17.db` mantiene estado operativo/ledger/readiness; no se usa para inflar históricos multi-familia.
- History Store v2 vive en la base histórica correspondiente.
- SQLite mantiene datos calientes e índices.
- Raw reciente permanece accesible; raw antiguo repetitivo se comprime/deduplica con hash fuera del hot path.
- Contract Evidence normalizado/versionado se conserva; raw browser tiene retención limitada salvo evidencia única/conflicto/auditoría.
- Logs rotan y comprimen; snapshots de UI tienen límite de líneas y se reemplazan.
- Imágenes Docker: conservar activa + rollback validado; retirar versiones viejas sólo después de deploy verde.
- Build cache: podar sólo cache no referenciado luego de deploy verde.
- Artefactos temporales/deploy: TTL y limpieza post-validación.
- Medir GB antes/después y registrar recuperado.
- Si el disco entra en umbral alto se frenan ingestas pesadas antes de comprometer el runtime operativo.

## Deploy

No se cerrará ni desplegará el patch sin presentación previa de:

- alcance exacto;
- archivos que se modifican/agregan;
- cambios de configuración y timers;
- tests agrupados y resultado;
- familias READY/HOLD y razón;
- imagen candidata y hash;
- migraciones/dual-read/dual-write;
- plan de rollback;
- impacto de disco antes/después;
- limpieza propuesta post-VERDE.

Después de esa presentación se requiere autorización explícita del usuario antes del deploy.
