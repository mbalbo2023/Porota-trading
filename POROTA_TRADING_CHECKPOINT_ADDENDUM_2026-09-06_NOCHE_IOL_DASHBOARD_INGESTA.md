# POROTA TRADING — ADDENDUM CHECKPOINT NOCHE 2026-09-06

**Continuidad:** leer después de `POROTA_TRADING_CHECKPOINT_CANONICO_RC6_2026-09-06_POSTHOTFIX.md`.

## 1. Identidad vigente

### Observer / trading runtime — CONGELADO
- branch live: `hotfix/rc6-cedear-us-labor-day-20260906`
- SHA: `db26c76723bb988c956589c572b87cbcb4191731`
- image: `porota-trading-bot:17.0.0-rc6`
- release: `17.0.0-rc6`
- modo: `PRODUCTION_PAPER`
- ejecución: `SIMULATED`
- real money: `BLOCKED`
- observer restart=0, readonly rootfs=true.

### Dashboard — overlay operativo separado
- branch: `ux/rc6-dashboard-go-live-20260906`
- SHA dashboard: `318043805a9736eda77874ba72ee7abc93fcb0f1`
- image activa: `porota-trading-dashboard:17.0.0-rc6-go-live-ux2`
- workflow de deploy: `34069093749`
- resultado: `SUCCESS`
- observer no fue reiniciado;
- estrategia/gates no cambiaron;
- DB observer/history quick_check=ok;
- `real_orders_sent=0`.

La documentación continúa en branch `checkpoint/rc6-20260906-posthotfix`; no mover la branch live por documentación.

## 2. Priming nocturno — COMPLETADO

Workflow `RC6 night data priming 2026-09-06`, run `34068494819`: `SUCCESS`.

PPI historical bounded:
- estados antes: `EMPTY_OR_INVALID=10`, `ERROR=4`, `PARTIAL=169`, `VALID_PAYLOAD=63`;
- lote previo: 6/40 completos, 6.896 filas válidas vistas;
- lote forzado: 11/40 completos, 8.108 filas válidas vistas;
- estados después: `EMPTY_OR_INVALID=10`, `ERROR=2`, `PARTIAL=169`, `VALID_PAYLOAD=65`;
- la cobertura legacy completa permaneció 67/246;
- `real_orders_sent=0`.

Data912 batch 40:
- selected=40;
- successful=2;
- without_history=20;
- failed=18;
- canonical_updates potenciales=243;
- protected_rows=243;
- versions_appended=0;
- la precedencia existente protegió las velas PPI; no hubo overwrite silencioso.

Candle integrity:
- GREEN;
- 5.000 versiones chequeadas;
- dirty_bars=0;
- quick_check=ok;
- read_only=true;
- worker RUNNING, cursor 49.764.

Conclusión: priming operacionalmente GREEN. No seguir forzando PPI inmediatamente; respetar TTL/backoff.

Documento de evidencia: `RC6_NIGHT_DATA_PRIMING_EVIDENCE_2026-09-06.md`.

## 3. PPI historical — RCA profundo de calidad

Workflow read-only `RC6 history quality deep audit 2026-09-06`, run `34069228668`: `SUCCESS`, `NETWORK_CALLS=NO`, `RUNTIME_CHANGED=NO`.

### Rechazos acumulados al último corte: 218.878

Por causa:
- `OHLC_INCONSISTENT`: 145.292 filas / 179 identidades;
- `HIGH_NONPOSITIVE`: 61.188 / 87;
- `OPEN_NONPOSITIVE`: 12.398 / 2.

Por familia:
- CEDEARS `OHLC_INCONSISTENT`: 123.485;
- CEDEARS `HIGH_NONPOSITIVE`: 51.752;
- CEDEARS `OPEN_NONPOSITIVE`: 12.398;
- ACCIONES `OHLC_INCONSISTENT`: 21.810;
- ACCIONES `HIGH_NONPOSITIVE`: 9.436.

Existe fuerte concentración diagnóstica en variantes cuyo símbolo termina en C/D, pero **no se debe inferir equivalencia financiera ni copiar la serie base**. Ejemplos de alto rechazo: AVYC, IRSAC, VODC, AMATC, NVSC, GGALC, AAPLC, YPFDC.

History Store v2 sigue comportándose correctamente:
- conserva cada barra FULL_OHLC válida;
- rechaza cada fila defectuosa con causa explícita;
- puede preservar close-only por separado;
- ninguna fila histórica concede READY_PAPER ni precio de ejecución.

### Hallazgo de eficiencia de `_historical_targets()`

Primer lote de 40 según el orden actual después del priming:
- 22 identidades ya `VALID_PAYLOAD` / legacy complete;
- 18 `PARTIAL`.

Una prioridad diagnóstica, sin ejecutar ni persistir cambio, produjo:
- 0 completas;
- 10 `EMPTY_OR_INVALID`;
- 30 `PARTIAL`.

Conclusión: hoy casi la mitad de un batch puede gastarse refrescando identidades ya completas. Es posible mejorar mucho la cobertura sin incrementar el número de llamadas.

### Hallazgo arquitectónico adicional

Los intentos PPI históricos actuales observados corresponden sólo a:
- ACCIONES: 55 identidades;
- CEDEARS: 191 identidades.

No aparecen BONOS PPI en `production_history_attempts`, aunque History Store tiene 25 BONOS / 883 filas desde Data912.

La causa visible es que `_historical_targets()` usa el `candidate_universe` filtrado por `can_simulate=1 OR INDICES`. Esto mezcla dos conceptos que deben separarse:

- `PAPER_TRADING_UNIVERSE`: qué puede operar/simular el motor;
- `HISTORY_INGEST_UNIVERSE`: qué familias/instrumentos tienen un contrato histórico verificable y conviene almacenar aunque no sean operables PAPER todavía.

### Pendiente P1 de históricos

Diseñar y probar fuera del hot path:
1. prioridad por necesidad/freshness, evitando refrescos innecesarios de completos;
2. separación trading-universe vs history-universe;
3. contrato histórico por familia, sin forzar OHLC donde no corresponda;
4. retry/backoff por error y no por simple posición en una rotación;
5. proof con fuente independiente para series problemáticas;
6. métricas nuevas de `useful canonical coverage`, distintas de la vieja métrica `VALID_PAYLOAD completo`.

Documento: `RC6_HISTORY_QUALITY_DEEP_AUDIT_2026-09-06.md`.

## 4. Dashboard Go Live — DESPLEGADO Y VALIDADO

### Cambios activos

- panel ejecutivo agrega últimas 5 operaciones PAPER válidas y excluye fechas BYMA no operativas;
- En Vivo ya no muestra el bloque técnico `5. Motores / workers`;
- Histórico prioriza store canónico/fuentes/estado de ingesta/últimos intentos y retira `Base objetiva` y `coverage` de la vista del operador;
- Reportes vuelve a integrar BCRA/INDEC/macro y performance vs inflación;
- Configuración muestra parámetros efectivos RC6 y `IA intradía=OFF`;
- Sistema/Introspección prefiere snapshots RC6 actuales en vez de ignorarlos por el patrón legacy HF;
- el menú lateral de Sistema permanece en drill-down;
- tablas compactas/responsive/Voice Access se mantienen;
- logs distinguen ausencia real de evidencia de un estado saludable;
- un AMARILLO no bloqueante conserva color/estado AMARILLO y se clasifica por separado con `paper_blocking=false`; no se repinta artificialmente a GRIS.

### Deploy v3

Run `34069093749`: `SUCCESS`.

Validaciones:
- build GREEN;
- compile GREEN;
- suite dashboard GREEN;
- todas las rutas críticas del dashboard anterior respondían 200 antes de activar;
- render directo del candidato contra datos live pasó en modo read-only;
- se activó sólo `porota_production_dashboard`;
- post-activation HTTP matrix GREEN para `/`, `/vivo`, `/historicos`, `/reportes`, `/config`, `/sistema?section=introspeccion`, `/salud`, `/universo-operativo`, `/scalping`, `/instrumentos`, `/aprendizaje`, `/dashboard/logs`;
- DB postflight `ok|ok|PRODUCTION_PAPER|0`;
- observer SHA quedó `db26c...`, restart=0;
- strategy changed=NO;
- real-order capability=BLOCKED.

### Auditoría de verdad de tarjetas

Workflow `RC6 dashboard card truth audit 2026-09-06`, run `34069373182`: `SUCCESS`.

La auditoría cruzó los valores del dashboard con las tablas SQLite directas y verificó los renderers activos.

Valores observados al corte:
- observer: `WAITING_MARKET / MARKET_CLOSED / PPI_AUTH=OK / real_orders_sent=0`;
- DBs observer/history: quick_check=ok;
- spot readiness=READY;
- caucion readiness=READY;
- posiciones cerradas: 26 = 1 WIN / 25 LOSS;
- learning samples: 26, labeled=26;
- históricos: 48.323 filas / 261 identidades;
- PPI fuente: 47.440 / 236;
- Data912: 883 / 25;
- scalping worker: WAITING_MARKET;
- intraday points: 130.862;
- contratos de volumen confirmados: 198;
- scalping real_orders_sent=0;
- universo del último ciclo: selected=20, eligible_total=823, successful=14, recommended_limit=29.

Caja/patrimonio que el dashboard lee de su source-of-truth:
- ARS: cash `-375964.0007`, pending proceeds `1353617.5893`, equity `977653.5886`, realized cumulative `-22346.4114`;
- USD: cash/equity `1000`;
- USD_CCL: cash/equity `1000`;
- USD_MEP: cash `913.9830`, pending proceeds `84.5530`, equity `998.5360`, realized cumulative `-1.4640`.

Estos valores son PAPER/simulados y la auditoría confirma consistencia UI↔DB; no deben reinterpretarse como saldo PPI real.

Últimas cinco posiciones operativas seleccionadas por la nueva regla al corte:
`YPFD`, `SUPV`, `BBAR`, `AAPLD`, `GGAL`, todas del 2026-09-03; no aparecen operaciones de fin de semana.

### Estados de salud relevantes al corte

- PPI auth: VERDE;
- PPI catálogo: VERDE, 824 identidades únicas / 0 búsquedas fallidas en el último refresh observado;
- PPI historical: AMARILLO, no bloqueante por sí solo;
- PPI background history: AMARILLO, no bloqueante por sí solo;
- errores PPI última hora: VERDE / ninguno clasificado;
- PPI market data: NO_APLICA mientras rueda cerrada;
- foco PAPER: VERDE 10/10;
- sampling foco: VERDE;
- rotación universo: AMARILLO y no bloqueante por sí sola;
- Economic Gate SHADOW: AMARILLO porque no hay BUY evaluadas en domingo; sigue siendo evidencia SHADOW, no rentabilidad demostrada;
- Telegram: VERDE según último informe persistido, pero timestamp antiguo (22-Ago) debe refrescarse/confirmarse en preopen;
- BCRA/INDEC: VERDE;
- BYMA Open Data: VERDE;
- SRE snapshot: AMARILLO aunque `quick_check=ok` y había ~43,4% libre; revisar semántica/freshness del SRE card en preopen para que el color corresponda a la causa real.

## 5. InvertirOnline (IOL) — fuente histórica independiente / Contract Evidence

Se incorpora como P1 formal.

### Hechos confirmados en documentación oficial IOL al 2026-09-06

La API oficial declara:
- cotizaciones actuales e históricas;
- históricas ajustadas;
- Acciones, Bonos, Opciones, Cauciones, Futuros, Monedas y Cheques de Pago Diferido del mercado argentino;
- JSON sobre HTTPS;
- cuenta IOL + activación previa del servicio;
- bearer token válido ~15 minutos + refresh token;
- la API productiva también permite operar, y la propia documentación advierte que las acciones allí impactan en entorno real.

El MCP oficial IOL ofrece un permiso explícito `solo lectura`, default-deny para capacidades operativas y, entre sus herramientas GET, `get_price_history` (OHLCV), `get_asset_info`, `get_options_chain`, `get_caucion_rates`, `get_fixed_income_analytics`, `get_fci_funds`, etc.

Tarifa oficial observada: API bonificada hasta 25.000 API calls por mes; luego aplica el costo publicado por IOL.

**No queda registrado como canónico ningún endpoint raw de serie histórica cuya forma exacta no haya sido verificada en la documentación oficial autenticada/Explore.** El proof debe tomar el endpoint/contrato directamente de la documentación oficial habilitada para la cuenta.

### Valor esperado para POROTA

IOL es candidato fuerte a segunda fuente histórica porque permitiría:
- PPI vs IOL por identidad/fecha;
- distinguir defectos de PPI de semántica real del instrumento;
- estudiar variantes C/D con otra fuente sin hacer equivalencias heurísticas;
- comparar ajustada vs no ajustada;
- contrastar OHLCV/gaps/freshness;
- cubrir familias que hoy no entran en el universo PPI historical del observer;
- enriquecer Contract Evidence con metadata de instrumento, opciones, cauciones, renta fija y FCI.

### Política IOL obligatoria

Antes de cualquier integración:
1. acceso API habilitado/aceptación de términos;
2. credenciales/tokens sólo en secrets;
3. `IOL_READONLY_GUARD` con allowlist positiva de host, método y rutas GET permitidas;
4. cliente histórico separado de cualquier cliente operativo;
5. ningún POST/DELETE/place/cancel accesible desde el proceso history;
6. auditoría de request/response sin secretos;
7. cache/TTL/backoff + budget de calls;
8. mapping IOL→Porota determinístico, fail-closed;
9. provenance `IOL_HISTORY` separado;
10. primero proof sin modificar canonical; sólo después decidir precedencia.

El código actual de History Store ya conoce un source rank `IOL=30`, pero **no existe todavía integración IOL en el repositorio**. PPI hoy tiene rank 10 y Data912 rank 50. La precedencia final debe ser revisada con evidencia antes de escribir IOL al store.

## 6. IOL scraping — criterio

Prioridad: **API oficial / MCP read-only antes que scraping**.

El scraping sólo se considera complementario para Contract Evidence/spot-check cuando el dato necesario no pueda obtenerse adecuadamente por API. La web puede servir para:
- verificar columnas/metadatos visibles;
- confirmar contratos/ajustes;
- contrastar API vs UI;
- documentar familias/campos que no estén claros en el endpoint estructurado.

No se aprueba scraping masivo IOL como ingesta primaria. Razones:
- HTML/JS es más frágil que API;
- menor claridad de rate limits/contrato;
- términos IOL contemplan medidas ante técnicas automáticas/robóticas cuando sean consideradas fraudulentas;
- existen restricciones de redistribución/publicación para determinados datos de mercado.

Si se investiga web IOL: read-only, frecuencia baja, sin operar, sin modificar cuenta/2FA, sin guardar cookies/tokens, y sólo evidencia necesaria.

Documento dedicado: `IOL_HISTORY_AND_CONTRACT_EVIDENCE_RESEARCH_2026-09-06.md`.

## 7. Continuidad inmediata para mañana

1. No forzar más PPI esta noche; respetar TTL/backoff.
2. No forzar A3 hasta corregir mapping de identidad.
3. No forzar browser Contract Evidence fuera de DUE.
4. Dashboard UX `ux2` queda activo; observer permanece congelado en `db26c...`.
5. Preopen 10:15–10:30 debe volver a validar todas las fuentes/tarjetas con timestamps actuales, especialmente Telegram/SRE/PPI/DB/calendar/real_orders.
6. El Economic Gate SHADOW y warnings no bloqueantes deben distinguirse de blockers operativos reales.
7. Después del Go Live PAPER, ejecutar proof IOL read-only sobre una cohorte pequeña y comparar PPI vs IOL antes de integrar IOL a History Store.
8. Diseñar P1 de históricos: priorización + history universe separado del trading universe.
9. Real money permanece `NO-GO / BLOCKED`.
