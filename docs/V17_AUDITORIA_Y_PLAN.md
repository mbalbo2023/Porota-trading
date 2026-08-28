# Porota Trading v17 — auditoría y avances de implementación

Actualizado: 28/08/2026. Base entregada: v16.3.5, rama `testing`, commit
`612b0431a33909af3eeaaaa909db648c165a4ac9`.
Trabajo aislado en `feature/v17-convergencia`.

**Estado: desarrollo. No es una versión lista para producción.**
No se cambiaron imágenes, servicios, modo operativo ni permisos del servidor.
No se enviaron órdenes reales. Los nuevos registros son `PRODUCTION_PAPER`.

## Decisiones del operador

- Incluir cauciones y todas las familias del sistema: acciones, CEDEARs,
  ETF, bonos, letras, ON, opciones, futuros y FCI.
- La instrucción posterior de incluir cauciones prevalece sobre la adenda
  que las dejaba como observación. Confirmación del operador del 28/08:
  **sólo cauciones colocadoras, invirtiendo saldo disponible**. Se excluyen
  tomadoras, financiación y uso de fondos comprometidos o aún sin liquidar.
- No incorporar las mejoras de ciberseguridad de las propuestas en esta etapa.
  No quitar las protecciones existentes. Los límites de riesgo, la liquidez,
  los vencimientos y la integridad contable sí forman parte del trabajo.
- Reducir acciones manuales: código a GitHub, archivos complejos por SFTP y
  comandos cortos con salida al portapapeles mediante OSC 52 en Termius.

## Material revisado

Fuente de `Porota-trading-testing.zip`; README, parches y ocho módulos propuestos
en `Porota_Trading_v17_Paquete_Completo.zip`; plan de convergencia y adenda;
PDF de fuentes históricas y PDF de arquitectura de velas/backtesting.
Los cuatro PDF suman 68 páginas. Las referencias al compendio de 127 páginas
no equivalen a haber recibido o revisado ese compendio completo.

Se realizó una revisión inicial dirigida a las rutas que afectan dinero,
ejecución, datos y resultados. No se afirma una auditoría exhaustiva de cada
línea del repositorio ni una validación de rentabilidad de las estrategias.

## Correcciones implementadas

| Hallazgo | Corrección y comprobación |
|---|---|
| La compra podía dejar caja negativa al omitir comisión en sizing | Cantidad máxima calculada con costos redondeados; caso de capital 1000 reproducido y corregido |
| Riesgo del stop omitía comisión de ambas puntas y slippage de salida | Presupuesto de riesgo incluye ambos costos y precio de salida modelado; no promete protección frente a gaps |
| Un cierre podía repetirse con una posición vieja | Actualización condicional y fill en una transacción; reintento sin segunda venta |
| Se podían cerrar posiciones usando otro plazo, clase o símbolo | Identidad y secuencia temporal verificadas; profundidad suficiente para cierre total |
| Se inventaba un cierre total con profundidad insuficiente | Queda pendiente; todavía falta implementar fills parciales |
| NaN e infinitos contaminaban cuentas y señales | Rechazo/normalización de valores no finitos; cotizaciones cruzadas no abren compras |
| Señales mezclaban muestras CI/24 h, clases y monedas | Series filtradas por símbolo, clase, plazo, moneda/plaza y mercado |
| Posiciones abiertas podían quedar fuera del universo rotativo | Todas las abiertas tienen prioridad, incluso fuera del catálogo o sobre el límite de muestreo |
| Una venta T+1 aparecía inmediatamente como caja | Recibos pendientes separados de efectivo; siguen siendo patrimonio |
| Dos operaciones podían gastar la misma caja | Revalidación dentro de transacción de compra; colocaciones serializadas y prueba concurrente |
| Renta fija podía usar precio por 100 VN como precio por unidad | Factor monetario y lote explícitos, conservados con la posición; sin esos datos no se abre una nueva posición de renta fija |
| El panel mezclaba precios de diferentes identidades | Última cotización por símbolo, clase, plazo, moneda/plaza y mercado |
| Una búsqueda de catálogo se confundía con un instrumento real | Historial de consultas separado de los instrumentos efectivamente devueltos; deduplicación por identidad completa |
| Precios MEP/CCL podían gastar pesos o mezclarse con USD genérico | Cuatro cajas separadas; identidad monetaria obligatoria en apertura, cierre, series, valuación e informes |
| Una actualización podía borrar el catálogo antes de terminar la red | Recolección previa y persistencia transaccional; registros no reconfirmados quedan STALE y no abren posiciones |
| Los informes atribuían PnL por fecha de apertura o incorporaban cierres futuros | PnL por fecha de cierre/acreditación y zona horaria; detalle histórico no muestra resultados posteriores al corte |
| Un PnL positivo se presentaba como prueba de superar inflación | Comparación bloqueada hasta tener rendimiento porcentual y benchmark del mismo período |
| CI construía 16.3.5 pero intentaba inspeccionar la imagen 16.2 | Resuelve la imagen exacta desde Compose; test ejecuta el paso con una etiqueta distinta |
| Tests de login compartían estado entre ejecuciones | Aislamiento de base temporal en los tests; ningún cambio al límite operativo de login |

## Cauciones: lo que ya hace el código

`bt_caucion_paper.py`, integrado con `PaperBroker` y la misma caja simulada:

- Exige contrato, moneda, tasa como **fracción anual**, fecha de inicio,
  vencimiento con zona horaria, base anual, costos y profundidad.
- Cuenta días corridos para intereses a partir de las fechas contractuales;
  viernes a lunes no se confunde con un solo día de devengamiento.
- Inmoviliza principal. Respeta reserva de caja y participación en profundidad.
- Distingue costos pagados al inicio de costos descontados al vencimiento.
- Rechaza retorno neto no positivo, cotizaciones futuras/vencidas y costos
  presupuestados para un capital distinto.
- Rechaza usar otra moneda/plaza: ARS, USD, USD_MEP y USD_CCL tienen cajas
  independientes. Los tres saldos USD comienzan en cero salvo configuración
  explícita de su capital paper. No convierte ni consolida monedas.
- Usa el tarifario heredado sólo como modelo ARS; prorratea su comisión anual,
  sin cobrarla como si fuera por operación. En USD exige costos explícitos.
- Persiste colocaciones y claves idempotentes. Vencimiento y evento se registran
  una sola vez, aunque se reinicie el proceso o se repita la llamada.
- El observador procesa vencimientos antes del trabajo de red, incluso con
  mercado cerrado. No aplica stop ni una venta ficticia a la caución.
- El panel muestra capital, moneda, tasa, días, vencimiento, costos y estado.
  Informes PDF/JSON y Telegram separan resultados por moneda/plaza; una caución
  acreditada aporta interés menos costos, nunca la devolución del principal.

**Pendiente para la operación automática:** adaptador de cotizaciones/contratos
PPI con evidencia real de sus campos; política de plazo, capital y reserva;
comparación de alternativas netas; programación de colocaciones y conciliación.
`place_caucion()` es una operación explícita del simulador, no un planificador
de inversión automática ni una orden real.

El método heredado `c_ppi_client.get_caucion_rate()` todavía usa una selección
insegura de primer resultado/proxy; `place_caucion()` de ese cliente devuelve
un presupuesto y no acredita una colocación. **No se conectaron estos métodos
a la nueva contabilidad. Deben reemplazarse antes de habilitar ejecución real.**

## Estado por familia

| Familia | Implementado en este avance | Falta para completar v17 |
|---|---|---|
| Acciones, CEDEARs, ETF | Correcciones de caja, costos, riesgo y cierres en paper | Datos/supervisión independiente y nueva señal validada |
| Bonos, letras, ON | Compras/cierres paper con contrato explícito, factor VN y lote | Cargar factores desde metadatos contrastados; cashflows, amortizaciones, intereses corridos y monedas |
| Cauciones | Ciclo completo de colocadora simulada, capital y vencimiento | Cotización/adaptador PPI y política de asignación; tomadora excluida por instrucción |
| Opciones | Contrato y cálculos de prima/lote/pérdida máxima de opción comprada | Integración del ejecutor, liquidez, ejercicio, vencimiento y supervisor específico |
| Futuros | Separación de nocional, garantía, ajuste diario y déficit de margen | Libro persistente de ajustes, proveedor, conciliación y gestión de márgenes |
| FCI | Familia y unidades reconocidas; no pasa por ejecutor de acciones | Suscripción/rescate, valor de cuotaparte, corte y demora de rescate |

Los cálculos de opciones/futuros **no equivalen a un motor operativo integrado**.
Se bloquean en la ruta genérica de compra de acciones. Las nuevas búsquedas de
catálogo tampoco prueban cobertura total de PPI. No se inventan multiplicadores,
garantías, moneda ni fecha de vencimiento a partir de un ticker.

## Convenciones contables y migración

- Migración aditiva: tablas `paper_sale_receivables` y `paper_cauciones`.
  No se reescriben fills ni etiquetas de aprendizaje existentes.
- CI se modela disponible al ejecutar; T+1 se modela disponible **al final del
  siguiente día auditado**. Es una aproximación deliberadamente conservadora
  del simulador, **no un horario oficial de liquidación de PPI**. No permite
  netear crédito intradiario del broker.
- Plazo o calendario desconocido: el producido queda pendiente de confirmación.
  El calendario actual sólo cubre 2026; deberá actualizarse para operar 2027.
- Posiciones antiguas conservan su factor histórico 1. No se “corrigen” cantidades
  retrospectivamente. Sus muestras se deberán separar de las de v17.
- La migración de moneda conserva la contabilidad heredada en ARS con etiqueta
  `LEGACY_ASSUMED_ARS`; no convierte una compra histórica errónea de ALUAC/AAPLD
  en dólares por cambiar una columna. Un cierre con moneda distinta queda
  pendiente de conciliación. Snapshots antiguos quedan `UNKNOWN`, fuera de
  señales que requieran moneda confirmada. Migración probada dos veces sin
  duplicar fills ni alterar cantidades/precios/costos.
- `financial_instrument_catalog` guarda identidad, procedencia y capacidad;
  `catalog_query_results` guarda búsquedas. Catálogo legado se importa STALE.
  `paper_equity_by_currency` guarda patrimonio por moneda/plaza; `paper_equity`
  conserva sólo ARS por compatibilidad. El capital de cada caja sigue siendo
  configuración del simulador, no saldo obtenido de una cuenta real.
- Patrimonio incluye créditos pendientes y principal/interés devengado de caución;
  caja disponible los excluye hasta que corresponda. El resumen histórico principal
  sigue en ARS; no agrega USD sin una valuación de cambio explícita.
- Falta el libro integral de débitos, créditos, garantías y cashflows corporativos,
  conciliado por moneda y fecha valor. Esta migración no sustituye ese trabajo.

## Evaluación del código sugerido: decisiones pendientes

| Módulo/propuesta | Problema identificado | Decisión |
|---|---|---|
| `bk_free_market_data` | El adaptador data912 espera campos/rutas que no corresponden a todos los activos | No copiar como feed universal; verificar payload, moneda y cobertura por clase |
| `bl_candle_engine` | Lectura mezcla series ajustadas/no ajustadas; upsert no actualiza apertura | Corregir identidad completa y upsert antes de migrar |
| Velas desde snapshots | Midpoint no equivale a último negocio y volumen acumulado no equivale a volumen del intervalo | Separar cotizaciones y operaciones; no fabricar volumen, VWAP o dollar bars |
| Migración histórica | Ruta por defecto difiere de la base actual y cuenta filas ignoradas como migradas | Migración idempotente, conciliación de cantidades y respaldo previo |
| `bm_exit_supervisor` | Da un cierre por hecho aunque el callback devuelva False | Estado persistente dependiente del fill; reloj y proceso independientes del escáner/IA |
| `bq_exit_policy` | Horario 17:00 uniforme; breakeven sin costo completo; bloqueo diario no persistente | Sesión por instrumento, costos de salida y bloqueo diario persistente |
| Liquidación forzada al cierre | No existe fill ejecutable una vez cerrado el mercado o sin profundidad | Anticipar cierre; conservar salida pendiente si no se puede ejecutar |
| `bn_telegram_bus` | Fill y notificación en transacciones distintas; riesgo de perder evento; 429 mal coordinado | Outbox en la misma transacción financiera y cooldown global; documentar entrega al menos una vez |
| `bo_signal_core` | Reward/risk bruto, umbrales heurísticos y controles incompletos de datos | Evaluar neto, calidad y disponibilidad temporal; validar sin anticipación |
| `bp_dashboard_v17` | CSV ordena por `id` inexistente en muestras; consultas silenciosamente vacías | Reutilizar panel existente y corregir consultas; no reemplazo ciego |
| `br_backtest_gate` | Tres meses distintos pueden cubrir sólo ~32 días; drawdown y estrés incompletos | Span real, curva marcada a mercado, costos/slippage y splits temporales efectivos |
| Comparación con caución | Tasa anual fija y período supuesto distorsionan Sharpe/benchmark | Tasa, plazo y período históricos observados, no constantes inventadas |
| Calibración/aprendizaje | Versiones viejas y nuevas comparten muestras; separación temporal insuficiente | Etiquetas/versiones, embargo, holdout y parámetros congelados |
| Producción | Dos motores divergentes, flags y versiones de imagen no alineados | Convergencia por etapas y rollback; no modificar despliegue hasta probar |

Los requisitos propuestos de cantidad de trades, meses, profit factor y drawdown
son criterios de evaluación a discutir, no evidencia de rentabilidad ni una
garantía de resultados. La hipótesis de que una pérdida no se recupera tampoco
se acepta como regla universal sin datos.

## Verificación

Primer checkpoint: 279 tests aprobados. Segundo: 329. Tercero: **354 tests
aprobados** después del diagnóstico real, separación monetaria, catálogo e informes.
Comando usado: `python -m pytest -o addopts='' -q -m 'not red'`.

Entorno local Python 3.12; librerías instaladas para ejecutar la suite. No es
todavía una reproducción completa del contenedor objetivo Python 3.11 ni de
todos los pins de producción. Advertencia observada: deprecación del TestClient
Starlette/httpx del entorno local. En el CI remoto anterior (run 33128104915),
la suite Python 3.11 y el build Docker aprobaron; el job de arranque falló antes
de levantar servicios por buscar la imagen 16.2. Este avance corrige esa causa;
se debe comprobar el nuevo CI completo, no asumir que arrancó por pasar tests.
Los 12 payloads públicos aportados se usan como fixture; aún falta integración
con cotizaciones/contratos especializados y validación en el Droplet.

## Diagnóstico recibido: no repetir el comando

El operador entregó el resultado de `scripts/v17_diagnostico_instrumentos.py`
generado el 28/08/2026 a las 00:17:19 UTC sobre
`/opt/porota-trading/data/observer/observer_production.db`. Catálogo descargado
el 27/08 a las 13:45:03 UTC: **330 instrumentos** (55 acciones, 42 bonos,
188 CEDEARs, 45 futuros). La muestra contiene 12 registros, no el catálogo entero.

| Etiqueta real de PPI | Caja normalizada |
|---|---|
| Pesos | ARS |
| Dolares billete \| MEP | USD_MEP |
| Dolares divisa \| CCL | USD_CCL |

AE38 está cotizado en Pesos aunque su descripción diga USD. ALUAC/AAPLC son CCL;
AE38D/AAPLD, MEP. DLR/AGO26 figura en ROFEX/Pesos, sin multiplicador, margen ni
vencimiento estructurado. No se extrae vencimiento de su nombre/descripción.

Las familias ausentes y los resultados UNAVAILABLE/ERROR describen búsquedas
anteriores; no prueban falta de soporte del broker. Se amplían consultas de
letras, ON, opciones, cauciones y FCI y se conserva la configuración pública
devuelta por el SDK. Sus resultados reales todavía deben comprobarse.
Todos los registros observados pueden entrar al muestreo, pero catálogo no
equivale a ejecutor: futuros/opciones/FCI/cauciones siguen requiriendo su ruta
específica, y renta fija necesita factor nominal/lote contrastados.

## Fuentes externas contrastadas

- [PPI: documentación REST](https://itatppi.github.io/ppi-official-api-docs/api/documentacionRest/):
  instrumentos, cotizaciones, libros y distinción entre fecha de operación y liquidación.
- [BYMA: cauciones](https://www.byma.com.ar/productos/productos-financieros/caucion):
  colocadora/tomadora, monedas y devolución al vencimiento.
- [BYMA: opciones](https://www.byma.com.ar/productos/productos-financieros/opciones):
  prima T+0 y horarios/ejercicio específicos. No corresponde asumir T+1 y cierre
  uniforme de todas las familias.

Los aranceles heredados aún requieren conciliación con el presupuesto aplicable
a la cuenta. No se validaron aquí como tarifas comerciales vigentes del usuario.
