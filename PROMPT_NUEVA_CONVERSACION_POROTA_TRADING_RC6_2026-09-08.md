Quiero continuar POROTA TRADING RC6 exactamente desde el cierre de la conversación anterior, sin reconstruir contexto y sin repetir pruebas ya realizadas.

REPOSITORIO:
mbalbo2023/Porota-trading

RAMA DE CHECKPOINT ACTUAL:
checkpoint/rc6-ppi-dom-api-operability-alerting-20260908

ARCHIVO CANÓNICO QUE DEBES LEER COMPLETO ANTES DE HACER ABSOLUTAMENTE NADA:
POROTA_TRADING_CHECKPOINT_CANONICO_RC6_2026-09-08_CONTINUIDAD.md

ARCHIVOS COMPLEMENTARIOS OBLIGATORIOS:
- POROTA_TRADING_CHECKPOINT_PPI_DOM_API_OPERABILITY_ALERTING_2026-09-08.md
- POROTA_TRADING_CHECKPOINT_PPI_DOM_FULL_SWEEP_2026-09-08.md

INSTRUCCIÓN ABSOLUTA:
Antes de proponer, ejecutar, modificar, corregir, desplegar o diagnosticar nada, conectate a GitHub, leé COMPLETOS esos tres archivos y entendé todo el contexto.

No quiero reconstruir contexto.
No quiero volver a explicar decisiones.
No quiero repetir pruebas ya superadas.
No quiero que inventes estados actuales a partir de información vieja.
No quiero que confundas una decisión planificada con algo ya implementado.
No quiero que pierdas ninguno de los pendientes acumulados.

REGLAS DE SEGURIDAD:
- real_orders_sent=0 es absoluto.
- Producción real sigue NO-GO.
- No hacer pruebas de órdenes reales ni de red de rutas de orden.
- Browser PPI siempre read-only.
- Nunca guardar ni imprimir credenciales, contraseña, OTP, cookies, tokens ni secretos.
- No entrar en rutas /Operar para scraping.
- No hacer click en Operar.
- Cualquier mutación first-party PPI no conocida debe seguir abort + record + fail-closed.
- No inferir que una familia visible en PPI Web es operable por API.
- No inferir tick/step a partir de decimales.
- No borrar históricos ni historical_raw_archive.
- No usar docker system prune.
- No hacer cambios destructivos de DB.
- No asumir cuál es el SHA live sin preflight.
- No asumir que el parche browser V3 host-local ya está versionado en GitHub.

ESTADO EXACTO AL CIERRE:
- observer último postflight: ok|PRODUCTION_PAPER|0
- DB quick_check: ok
- real_orders_sent=0
- PPI Cuenta auth: GREEN
- PPI SSO Trading: GREEN
- trusted device: GREEN
- no 2FA en la validación final
- política browser read-only V3: GREEN en host
- Contract Evidence runtime: GREEN_NOT_DUE
- CE runs: 84; no aumentó porque no estaba DUE
- últimos imports CE conocidos: 2026-09-04, estado AMARILLO
- mecanismo GET/XHR STATIC antiguo: obsoleto, endpoints contractuales antiguos ya no aparecen
- WebSocket realtime-hw.portfoliopersonal.com/socket.io existe, pero no explica el contenido estático completo
- DOM autenticado: fuente primaria viable para Contract Evidence STATIC
- Full DOM Sweep: 16/16 familias autenticadas
- first-party unknown mutations: 0
- third-party mutations abortadas: 224
- no order routes visited
- DB writes: NO
- Contract Evidence import: NO
- observer quedó intacto

FULL DOM SWEEP:
- FCI: 50, POSSIBLY_TRUNCATED_50
- FCI Exterior: 50, POSSIBLY_TRUNCATED_50
- Acciones: 50, POSSIBLY_TRUNCATED_50
- Acciones USA: 50, POSSIBLY_TRUNCATED_50
- Bonos: 50, POSSIBLY_TRUNCATED_50
- Cauciones: 50, POSSIBLY_TRUNCATED_50
- CEDEARs: 50, POSSIBLY_TRUNCATED_50
- ETF: 50, POSSIBLY_TRUNCATED_50
- Futuros: 50, POSSIBLY_TRUNCATED_50
- Letras: 30, LIKELY_COMPLETE pero no demostrado contra universo
- Licitaciones: 3, LIKELY_COMPLETE pero no demostrado contra universo
- ON: 50, POSSIBLY_TRUNCATED_50
- Opciones: 50 renderizadas / 43 únicas, identidad y duplicación pendiente
- Índices: 17, LIKELY_COMPLETE pero no demostrado contra universo
- Monedas: 30, LIKELY_COMPLETE pero no demostrado contra universo
- Tasas: 50, POSSIBLY_TRUNCATED_50

PRÓXIMA TAREA P0 Y PUNTO EXACTO DE CONTINUACIÓN:
Construir la MATRIZ DE OPERABILIDAD REAL POR API para las 16 familias.

Para cada familia debes auditar, SIN ENVIAR NINGUNA ORDEN:
- WEB_VISIBLE
- DOM_EXTRACTABLE
- API_SEARCHABLE
- API_MARKETDATA
- API_HISTORY
- API_ORDER_SUPPORTED
- API_CANCEL_SUPPORTED
- API_MODIFY_SUPPORTED si aplica
- SETTLEMENT_SUPPORTED
- CURRENCY_MARKET_SUPPORTED
- CONTRACT_FIELDS_SUFFICIENT
- evidencia empírica/documental
- estado final: EXECUTABLE / PARTIAL / NON_EXECUTABLE / NOT_PROVEN

Las 16 familias:
Acciones, CEDEARs, Bonos, Letras, ON, Cauciones, Opciones, Futuros, ETF, FCI, FCI Exterior, Acciones USA/Exterior, Licitaciones, Índices, Monedas y Tasas.

La auditoría debe empezar por:
1. código/SDK PPI instalado y wrappers de POROTA;
2. enums/types/markets soportados;
3. métodos de búsqueda;
4. market data read-only;
5. history read-only;
6. interfaces order/cancel solo por inspección de código/docs, NO ejecución;
7. tests existentes;
8. evidencia de soporte real por familia.

Después de la matriz API, pendientes P0 en orden:
1. resolver cobertura DOM >50 y universo esperado;
2. auditar Opciones 50/43;
3. versionar en GitHub la política browser V3 que hoy está probada host-localmente;
4. integrar DOM Contract Evidence V1 en SHADOW / NO_AUTO_ACTIVATION;
5. reconciliar DOM vs API vs History Store;
6. definir SLA/TTL/max_staleness por dato/familia;
7. implementar Dashboard > Scraping / Contract Evidence con semáforo;
8. implementar alerting/introspección/Telegram por staleness, schema drift, coverage gap y API unsupported;
9. fail-closed selectivo por familia/instrumento cuando el dato no sea seguro;
10. probar el hard NO-TRADE date gate si todavía no está demostrado implementado.

PENDIENTES P1:
- History Store completeness/price_basis branch fix/rc6-history-store-completeness-price-basis-20260907
- PPI Web History SHADOW
- Cauciones contrato completo
- Opciones identity/dedup
- doble calendario CEDEAR/mercados extranjeros
- SRE/introspection latency RCA
- Evidence Architecture Phase A
- storage/disk sin pérdida de evidencia

PENDIENTES P2/P3:
- MFE/MAE
- Forward Lab v2
- walk-forward
- Event Risk
- correlations/sector
- cohorts/version separation
- PAPER/SHADOW campaign
- dashboard /vivo final
- drill-down operaciones
- P&L semáforo y timestamp
- responsive/tablet
- documentación operativa
- cleanup físico final solo con equivalencia demostrada

NO-TRADE 2026-09-08:
La decisión es NO TRADE con ingesta ON.
No asumas que el hard gate date-scoped ya está implementado: verificalo.
Debe mantenerse:
- market data ON
- historical ingestion ON
- backfill ON
- Contract Evidence ON read-only
- PPI Web History SHADOW cuando corresponda
- observer/dashboard/introspection ON
- órdenes y paper fills bloqueados
- real_orders_sent=0

FORMA DE TRABAJO:
Cuando necesites que yo ejecute algo en Termius, generame preferentemente un archivo .sh descargable, no un heredoc largo para pegar.
Debe producir una salida final compacta y copiarla al portapapeles vía OSC52 cuando sea posible.
Usar /home/porotaadmin/ como directorio práctico.
No ZIP salvo que yo lo pida.

PRIMERA RESPUESTA QUE QUIERO DE VOS EN LA NUEVA CONVERSACIÓN:
1. Confirmame que leíste los tres checkpoints completos.
2. Dame un semáforo resumido de estado actual.
3. Decime cuál es exactamente el P0 inmediato.
4. Empezá la auditoría de operabilidad API de las 16 familias desde GitHub/código, sin preguntarme cosas ya contenidas en los checkpoints.
5. No ejecutes ni propongas ninguna orden.
