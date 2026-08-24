"""
v_config_metadata.py — Descripciones de cada variable del .env (v10.4)

Un solo lugar con el nombre, la explicación, el ejemplo y si es un dato
sensible (para no mostrarlo completo) de cada variable — lo usa
o_dashboard.py para armar el editor visual (sección "Configuración" del
panel web). No es un archivo que el bot necesite para operar — si no
existe, el bot arranca igual; solo el editor visual del dashboard deja
de tener las descripciones.
"""

# (variable, sección, descripción corta, ejemplo, es_sensible)
CONFIG_METADATA = [
    # --- Credenciales ---
    ("PPI_API_KEY", "Credenciales", "Usuario técnico para operar tu cuenta de PPI.", "a1B2c3D4...", True),
    ("PPI_API_SECRET", "Credenciales", "Contraseña técnica de PPI.", "x9Y8z7W6...", True),
    ("PPI_ACCOUNT_NUMBER", "Credenciales", "Tu número de cuenta en PPI.", "12345678", False),
    ("ENVIRONMENT", "Credenciales", "SANDBOX (práctica) o PRODUCTION (plata real).", "SANDBOX", False),
    ("GEMINI_API_KEY", "Credenciales", "Llave de Google AI Studio para el motor de IA.", "AIzaSyD...", True),
    ("GEMINI_MODEL", "Credenciales", "Modelo de Gemini a usar.", "gemini-2.5-flash-lite", False),
    ("TELEGRAM_BOT_TOKEN", "Credenciales", "Token del bot de Telegram (BotFather).", "123456789:AAE...", True),
    ("TELEGRAM_CHAT_ID", "Credenciales", "A qué chat de Telegram mandar los avisos.", "987654321", False),
    ("ANTHROPIC_API_KEY", "Credenciales", "Opcional — respaldo si Gemini falla (motor Claude). Vacío = desactivado.", "sk-ant-...", True),
    ("ANTHROPIC_FALLBACK_MODEL", "Credenciales", "Modelo de Claude para el respaldo.", "claude-sonnet-5", False),
    # --- Horarios ---
    ("MARKET_OPEN_HOUR", "Horarios", "Hora de apertura de rueda (0-23, hora Argentina).", "11", False),
    ("MARKET_CLOSE_HOUR", "Horarios", "Hora de cierre de rueda.", "17", False),
    ("SERVER_TIMEZONE", "Horarios", "Zona horaria del servidor.", "America/Argentina/Buenos_Aires", False),
    # --- Riesgo ---
    ("RISK_PCT_PER_TRADE", "Riesgo", "% de tu cuenta que se arriesga por operación.", "1.0", False),
    ("STOP_LOSS_ATR_MULT", "Riesgo", "Distancia del stop-loss, en unidades de volatilidad (ATR).", "1.0", False),
    ("TAKE_PROFIT_ATR_MULT", "Riesgo", "Distancia del take-profit, en unidades de ATR.", "2.0", False),
    ("MAX_OPEN_POSITIONS", "Riesgo", "Operaciones simultáneas permitidas.", "3", False),
    ("MAX_PCT_OF_BOOK_DEPTH", "Riesgo", "% máximo de la profundidad del libro por orden.", "10.0", False),
    ("TARGET_HOLD_DAYS", "Riesgo", "Días esperados de tenencia, para prorratear el hurdle.", "5", False),
    # --- Kill switch ---
    ("MAX_DAILY_LOSS_PCT", "Kill switch", "% de pérdida diaria que corta las alertas nuevas.", "1.0", False),
    ("MAX_DRAWDOWN_PCT", "Kill switch", "% de caída desde el máximo que corta las alertas.", "5.0", False),
    ("MAX_CONSECUTIVE_STOP_LOSSES", "Kill switch", "Pérdidas seguidas que activan el corte.", "3", False),
    ("CCL_INTRADAY_CIRCUIT_BREAKER_PCT", "Kill switch", "% que el CCL puede moverse en el día antes de cortar.", "3.0", False),
    ("MAX_EXPOSURE_PER_TICKER_PCT", "Kill switch", "% máximo del capital total en un mismo ticker.", "40.0", False),
    # --- Costos y hurdle ---
    ("PPI_COMMISSION_PCT", "Costos y hurdle", "Comisión de PPI por tramo.", "0.006", False),
    ("IVA_PCT", "Costos y hurdle", "IVA sobre la comisión.", "0.21", False),
    ("BYMA_FEE_PCT", "Costos y hurdle", "Derecho de mercado de BYMA.", "0.0008", False),
    ("DEFAULT_RISK_PREMIUM_PCT", "Costos y hurdle", "Extra de exigencia sobre el piso de inflación/devaluación.", "1.5", False),
    ("MACRO_CONFIG_PATH", "Costos y hurdle", "Archivo con el dato de inflación mensual.", "a_macro_config.json", False),
    ("MACRO_CONFIG_MAX_AGE_DAYS", "Costos y hurdle", "Días antes de tratar el dato de inflación como vencido.", "45", False),
    ("MACRO_CONFIG_STALE_FALLBACK_PCT", "Costos y hurdle", "Piso conservador si el dato está vencido.", "8.0", False),
    ("MONTHLY_FIXED_COSTS_ARS", "Costos y hurdle", "Costo fijo mensual del servidor (informativo).", "0", False),
    # --- Ejecución ---
    ("ORDER_EXECUTION_MODE", "Ejecución", "'auto' (sin botón, ejecuta directo — default en SANDBOX) o 'confirm' (botón en Telegram — default en PRODUCTION). Vacío = automático según ENVIRONMENT.", "auto", False),
    ("PRODUCTION_AUTO_EXECUTE_CONFIRMED", "Ejecución", "Interruptor extra si PRODUCTION + ORDER_EXECUTION_MODE=auto a la vez.", "false", False),
    ("ORDER_CONFIRMATION_TIMEOUT_MINUTES", "Ejecución", "Minutos para confirmar antes de que la propuesta venza (modo confirm).", "10", False),
    ("PRICE_REVALIDATION_TOLERANCE_PCT", "Ejecución", "% máximo que el precio puede moverse antes de ejecutar/confirmar.", "1.0", False),
    ("POLL_CONFIRMATIONS_INTERVAL_SECONDS", "Ejecución", "Cada cuántos segundos se revisan los botones de Telegram (modo confirm).", "4", False),
    # --- Scalping (nuevo en v10.5) ---
    ("SCALPING_MODE", "Scalping", "Solicita scalping; queda bloqueado hasta disponer de intradía contractual de PPI.", "false", False),
    ("SCALPING_TARGET_HOLD_MINUTES", "Scalping", "Minutos de mantenimiento esperado de una posición en modo scalping.", "30", False),
    ("SCALPING_SCAN_INTERVAL_SECONDS", "Scalping", "Segundos de pausa entre instrumentos en modo scalping.", "10", False),
    ("WIN_RATE_LOOKBACK_DAYS", "Scalping", "Días hacia atrás para el win rate mostrado en cada ejecución automática.", "30", False),
    # --- Universo e instrumentos ---
    ("AUTO_DISCOVER_INSTRUMENTS", "Universo de instrumentos", "El bot descubre instrumentos operables solo (true) o usa solo la watchlist (false).", "true", False),
    ("AUTO_DISCOVER_ALL_TYPES", "Universo de instrumentos", "También descubre opciones/futuros/cauciones/FCI, solo para mostrar (no operar).", "true", False),
    ("MAX_DISCOVERED_PER_TYPE", "Universo de instrumentos", "Tope de instrumentos descubiertos por tipo.", "40", False),
    ("MIN_LIQUIDITY_ARS", "Universo de instrumentos", "Volumen promedio mínimo (pesos) para analizar un instrumento.", "5000000", False),
    ("INSTRUMENT_WATCHLIST_PATH", "Universo de instrumentos", "Archivo con la watchlist de respaldo.", "n_instrument_watchlist.json", False),
    ("TECHNICAL_DATA_SOURCE_CEDEARS", "Universo de instrumentos", "Fuente operativa del análisis técnico de CEDEARs.", "ppi", False),
    ("YFINANCE_SHADOW_ONLY", "Universo de instrumentos", "Mantiene Yahoo aislado para investigación; nunca interviene en operaciones.", "true", False),
    ("PPI_AUTH_RETRY_COOLDOWN_SECONDS", "Robustez API PPI", "Espera mínima tras un login fallido; compartida por todos los hilos.", "3600", False),
    ("PPI_RATE_LIMIT_COOLDOWN_SECONDS", "Robustez API PPI", "Espera tras una respuesta 429/cuota excedida.", "3600", False),
    ("AIOPS_ALERT_COOLDOWN_SECONDS", "AIOps (detección de anomalías)", "Tiempo mínimo entre alertas AIOps equivalentes.", "1800", False),
    ("AIOPS_CONFIRM_ANOMALY_SAMPLES", "AIOps (detección de anomalías)", "Muestras anómalas consecutivas para confirmar una alerta.", "3", False),
    # --- Aprendizaje ---
    ("MIN_SAMPLE_SIZE_AUTOTUNE", "Aprendizaje", "Operaciones cerradas mínimas para permitir el ajuste mensual.", "30", False),
    ("SIGNALS_RETENTION_DAYS", "Aprendizaje", "Días que se conservan las filas de la tabla signals antes de podarse (mensual, junto al auto-tuning). No afecta closed_trades/learning_diagnostics.", "365", False),
    ("NEWS_ARCHIVE_RETENTION_DAYS", "Aprendizaje", "Días que se conservan los titulares de noticias en bruto (daily_headlines, global_news_247) antes de podarse.", "90", False),
    ("MAX_PARAM_CHANGE_PCT", "Aprendizaje", "Cuánto puede cambiar un umbral en un ajuste mensual.", "10", False),
    ("DEFAULT_MIN_SCORE_TECH", "Aprendizaje", "Umbral técnico de arranque.", "0.70", False),
    ("DEFAULT_MIN_SCORE_MACRO", "Aprendizaje", "Umbral macro de arranque.", "0.70", False),
    # --- Backtest ---
    ("BACKTEST_INITIAL_CAPITAL_ARS", "Backtest", "Capital ficticio de arranque en cada simulación.", "1000000", False),
    ("BACKTEST_MAX_HOLD_DAYS", "Backtest", "Días máximos antes de cerrar una operación simulada a la fuerza.", "10", False),
    # --- Dashboard y técnico ---
    ("DASHBOARD_PORT", "Dashboard y técnico", "Puerto del panel web.", "8000", False),
    ("DASHBOARD_ACCESS_TOKEN", "Dashboard y técnico", "Contraseña simple para entrar al dashboard (preferí mandarla como header Authorization: Bearer, no en la URL).", "x7K9mQ2wPz", True),
    ("DASHBOARD_HOST", "Dashboard y técnico", "127.0.0.1 (default, solo accesible desde el servidor) o 0.0.0.0 (accesible desde internet).", "127.0.0.1", False),
    ("CSRF_TOKEN_MAX_AGE_SECONDS", "Dashboard y técnico", "Vigencia del token anti-CSRF del editor de configuración.", "900", False),
    ("DB_PATH", "Dashboard y técnico", "Dónde vive el archivo de la base de datos (dentro de ./data para que sobreviva a un reinicio del contenedor Docker — CORREGIDO EN v13.0).", "data/trading_system.db", False),
    ("NEWS_MAX_AGE_SECONDS", "Dashboard y técnico", "Antigüedad máxima de una noticia (segundos).", "21600", False),
    ("PPI_MAX_REQUESTS_PER_SECOND", "Dashboard y técnico", "Freno de velocidad hacia la API de PPI.", "2", False),
    # --- NUEVO EN v13.0 ---
    ("PPI_CLIENT_ID", "Credenciales", "Opcional — fallback de headers para Sandbox. Vacío = comportamiento normal (recomendado salvo que soporte de PPI confirme que hace falta).", "API_CLI_REST", True),
    ("PPI_CLIENT_KEY", "Credenciales", "Opcional — ver PPI_CLIENT_ID. Vacío = comportamiento normal.", "ppApiCliSB", True),
    ("LOG_DIR", "Dashboard y técnico", "Carpeta donde se guarda el log descargable desde el dashboard.", "data/logs", False),
    ("PROPOSALS_DIR", "Dashboard y técnico", "Carpeta donde el motor SRE guarda sus diagnósticos de crashes.", "data/proposals", False),
    ("BACKUP_DIR", "Infraestructura", "Carpeta de backups diarios automáticos de la base.", "data/backups", False),
    ("BACKUP_RETENTION_DAYS", "Infraestructura", "Días que se conservan los backups antes de borrarse.", "30", False),
    ("INFRA_DISK_WARN_PCT", "Infraestructura", "% de uso de disco a partir del cual el panel de Infraestructura marca alerta roja.", "85", False),
    ("BACKUP_MAX_AGE_HOURS_WARN", "Infraestructura", "Horas sin backup antes de marcarlo atrasado en el dashboard.", "26", False),
    ("LANGGRAPH_MODE", "Multi-agente (LangGraph)", "'shadow' (observación, default) o 'gating' (puede rechazar operaciones reales — no activar sin revisar la evidencia acumulada).", "shadow", False),
    ("SCALPING_COST_VALIDATOR_ENABLED", "Scalping", "Frena nuevas entradas de scalping si el margen neto real reciente viene negativo.", "true", False),
    ("MIN_SCALPING_NET_MARGIN_PCT", "Scalping", "Margen neto mínimo (%) exigido en las últimas operaciones de scalping.", "0.5", False),
    ("PARTIAL_FILL_POLL_SECONDS", "Ejecución", "Segundos que se reintenta una orden PartiallyFilled antes de aceptar el remanente.", "20", False),
    ("PARTIAL_FILL_POLL_INTERVAL_SECONDS", "Ejecución", "Frecuencia de reconsulta mientras una orden está PartiallyFilled.", "2", False),
    ("FEE_STRESS_STATE_PATH", "Costos y hurdle", "Dónde se persiste el factor de ajuste dinámico de comisiones.", "data/fee_stress_state.json", False),
    # --- Encontradas al auditar variable por variable (existían en .env
    # desde v12.0 pero nunca se habían agregado acá — quedaban fuera del
    # editor visual del dashboard sin que nada lo avisara) ---
    ("CIRCUIT_FAILURE_THRESHOLD", "Robustez API PPI", "Fallas de servidor/red consecutivas antes de abrir el Circuit Breaker.", "5", False),
    ("CIRCUIT_OPEN_COOLDOWN_SECONDS", "Robustez API PPI", "Segundos que el Circuit Breaker corta llamadas antes de probar de nuevo.", "60", False),
    ("CAUCIONES_AUTO_PLACEMENT", "Robustez API PPI", "Colocación automática de liquidez ociosa en cauciones. Mantener en false hasta confirmar plazo/ticker contra Sandbox real.", "false", False),
    ("DASHBOARD_DOMAIN", "Dashboard y técnico", "Dominio propio para HTTPS automático con Caddy (vacío = sin HTTPS).", "", False),
    ("ENV_FILE_PATH", "Dashboard y técnico", "Ruta del .env que lee/escribe el editor visual del dashboard.", ".env", False),
    # --- NUEVO EN v14.0 ---
    # Tiempo real (streams oficiales de PPI vía SignalR — ver x_ppi_websocket.py)
    ("PPI_STREAM_ENABLED", "Tiempo real (streams PPI)", "Activa los streams oficiales de PPI para precios en vivo. Si falla, el bot cae solo a polling HTTP.", "true", False),
    ("PPI_STREAM_ACCOUNT_ENABLED", "Tiempo real (streams PPI)", "Activa el stream de cuenta: avisos push de cambio de estado de las órdenes (incluye cantidad ejecutada real).", "true", False),
    ("PPI_STREAM_MAX_TICK_AGE_SECONDS", "Tiempo real (streams PPI)", "Antigüedad máxima de un precio en vivo antes de descartarlo y salir a pedirlo por HTTP.", "5", False),
    ("PPI_STREAM_STALE_ALERT_SECONDS", "Tiempo real (streams PPI)", "Segundos sin recibir ningún tick antes de que llegue un aviso por Telegram.", "120", False),
    ("PPI_STREAM_MAX_SUBSCRIPTIONS", "Tiempo real (streams PPI)", "Tope de instrumentos suscritos al stream a la vez.", "60", False),
    # Cierre de fin de día (riesgo overnight)
    ("EOD_CLOSE_ENABLED", "Cierre de fin de día", "Cierra a mercado las posiciones intradía antes de la campana, para no quedar expuesto de un día para otro.", "true", False),
    ("EOD_CLOSE_MINUTES_BEFORE", "Cierre de fin de día", "Cuántos minutos antes del cierre de rueda se ejecuta ese cierre forzado.", "5", False),
    ("EOD_CLOSE_ALL_POSITIONS", "Cierre de fin de día", "true = cierra TODAS las posiciones, no solo las intradía. Dejar en false salvo que no quieras mantener nada de un día para otro.", "false", False),
    # Seguridad del cambio de configuración
    ("ENV_CHANGE_REQUEST_WINDOW_MINUTES", "Seguridad y reinicio", "Minutos durante los que queda disponible el botón de reinicio después de guardar un cambio real.", "15", False),
    ("ENV_RESTART_CONFIRM_TIMEOUT_MINUTES", "Seguridad y reinicio", "Minutos para confirmar el reinicio por Telegram antes de que la solicitud venza.", "10", False),
    ("RESTART_CHECK_INTERVAL_SECONDS", "Seguridad y reinicio", "Cada cuánto revisa el bot si hay un reinicio confirmado esperando que el sistema quede libre.", "30", False),
    ("ACTIVITY_LOCK_MAX_AGE_SECONDS", "Seguridad y reinicio", "Antigüedad tras la cual un aviso de 'operación en curso' se considera basura de un proceso caído y deja de bloquear el reinicio.", "300", False),
    # Vigilancia automática de logs
    ("LOG_WATCH_ENABLED", "Vigilancia de logs", "Manda a Telegram, solo, lo que aparezca en los logs y amerite tu atención.", "true", False),
    ("LOG_WATCH_INTERVAL_MINUTES", "Vigilancia de logs", "Cada cuántos minutos se revisan las líneas nuevas del log.", "30", False),
    ("LOG_WATCH_REPEAT_SUPPRESS_MINUTES", "Vigilancia de logs", "Tiempo mínimo antes de volver a avisar por el MISMO tipo de error (anti-spam).", "60", False),
    # Análisis técnico
    ("TECHNICAL_MAX_WORKERS", "Universo de instrumentos", "Descargas de velas en paralelo (solo contra yfinance, nunca contra PPI).", "6", False),
    ("TECHNICAL_CACHE_SECONDS", "Universo de instrumentos", "Segundos que se reutiliza una descarga de velas dentro de la misma vuelta de escaneo.", "45", False),
    # --- Completadas en v14.0 tras auditar os.getenv() contra este archivo ---
    # (existían en el código desde v12/v13 pero nunca habían llegado al editor
    # visual del dashboard, así que no había forma de tocarlas desde el panel)
    ("AIOPS_ENABLED", "AIOps (detección de anomalías)", "Activa el vigilante que detecta comportamiento anómalo del propio bot (latencia, memoria, slippage).", "true", False),
    ("AIOPS_POLL_INTERVAL_SECONDS", "AIOps (detección de anomalías)", "Cada cuántos segundos toma una muestra de métricas.", "60", False),
    ("AIOPS_CONTAMINATION", "AIOps (detección de anomalías)", "Proporción esperada de muestras anómalas (parámetro del Isolation Forest). Más alto = más sensible.", "0.05", False),
    ("AIOPS_MIN_TRAINING_SAMPLES", "AIOps (detección de anomalías)", "Muestras mínimas antes de empezar a detectar de verdad.", "50", False),
    ("AIOPS_RETRAIN_EVERY_N_SAMPLES", "AIOps (detección de anomalías)", "Cada cuántas muestras se reentrena el modelo con el historial reciente.", "200", False),
    ("AIOPS_NORMAL_LATENCY_MS", "AIOps (detección de anomalías)", "Latencia considerada normal hacia PPI, para el arranque en frío del modelo.", "400", False),
    ("AIOPS_NORMAL_RAM_MB", "AIOps (detección de anomalías)", "Memoria considerada normal del proceso, para el arranque en frío.", "300", False),
    ("AIOPS_NORMAL_SLIPPAGE_PCT", "AIOps (detección de anomalías)", "Slippage considerado normal, para el arranque en frío.", "0.1", False),
    ("BACKTEST_ASSUMED_SPREAD_PCT", "Backtest", "Spread supuesto al simular, para no sobreestimar el resultado histórico.", "0.2", False),
    ("SRE_VECTOR_DB_PATH", "Dashboard y técnico", "Carpeta del índice vectorial (ChromaDB) del motor de introspección.", "./sre_vector_db", False),
    ("SRE_CODEBASE_GLOB", "Dashboard y técnico", "Qué archivos indexa el motor SRE para diagnosticar crashes.", "*.py", False),
    ("SRE_MODEL_TEMPERATURE", "Dashboard y técnico", "Temperatura del modelo al diagnosticar un crash. Baja = más literal.", "0.2", False),
    ("J_MAIN_PID_FILE", "Dashboard y técnico", "Dónde deja el entrypoint el PID del proceso del bot.", "data/j_main.pid", False),
    ("LOG_WATCH_STATE_PATH", "Vigilancia de logs", "Dónde se guarda hasta qué punto del log ya se revisó (para no releer lo mismo).", "data/log_watch_state.json", False),
    ("LOG_WATCH_MAX_SIGNATURES_PER_MESSAGE", "Vigilancia de logs", "Cuántos tipos distintos de error como máximo entran en un mismo aviso de Telegram.", "6", False),
    ("PPI_STREAM_WATCHDOG_INTERVAL_SECONDS", "Tiempo real (streams PPI)", "Cada cuánto se controla que el stream siga recibiendo datos.", "60", False),

    # --- v16.0 · Arranque controlado ---
    ("STARTUP_REQUIRE_AUTH", "Arranque", "Si el bot pide autorización por Telegram antes de operar. En true no arranca solo.", "true", False),
    ("STARTUP_TIMEOUT_MINUTES", "Arranque", "Minutos que espera la autorización antes de quedar detenido sin operar.", "60", False),
    ("STARTUP_STATE_PATH", "Arranque", "Dónde se guarda el estado del arranque (lo comparten el bot y el panel).", "./data/startup_state.json", False),
    ("TESTING_LOG_PATH", "Arranque", "Traza paso a paso que muestra la solapa de Testing.", "./data/testing_trace.jsonl", False),
    # --- v16.0 · Derivados ---
    ("TRADE_DERIVATIVES", "Derivados", "Si se operan opciones y futuros. En false se descubren y se muestran, pero no se opera.", "true", False),
    ("DERIV_MIN_DAYS_TO_EXPIRY", "Derivados", "Días mínimos hasta el vencimiento para entrar. Menos que esto, el decaimiento se come la ganancia.", "15", False),
    ("DERIV_FORCE_CLOSE_DAYS", "Derivados", "Días antes del vencimiento en que se cierra sí o sí, gane o pierda.", "3", False),
    ("DERIV_MAX_MONEYNESS_PCT", "Derivados", "Distancia máxima del strike al precio del subyacente. Evita comprar opciones de lotería.", "15.0", False),
    ("DERIV_MAX_PREMIUM_PCT", "Derivados", "Prima máxima como % del subyacente. Una prima alta indica volatilidad implícita inflada.", "12.0", False),
    ("DERIV_MAX_SPREAD_PCT", "Derivados", "Spread máximo tolerado en el book del derivado.", "6.0", False),
    ("DERIV_MAX_PORTFOLIO_PCT", "Derivados", "Tope del capital total en derivados, sumando todo lo abierto.", "20.0", False),
    ("DERIV_DEFAULT_OPTION_LOT", "Derivados", "Lote a usar solo si la API no informa el real del contrato.", "100", False),
    # --- v16.0 · Portón operativo ---
    ("NEWS_BLACKOUT_MAX_MINUTES", "Portón operativo", "Minutos que se tolera operar sin noticias antes de dejar de abrir.", "90", False),
    ("MACRO_CACHE_MAX_AGE_HOURS", "Portón operativo", "Antigüedad máxima de las series macro para que sirvan como contexto alternativo.", "36", False),
    ("DEGRADED_SIZE_FACTOR", "Portón operativo", "Cuánto se achica la posición cuando el contexto está degradado. 0.5 = la mitad.", "0.5", False),
    ("DEGRADED_HURDLE_MULT", "Portón operativo", "Cuánto se sube el piso de rentabilidad con contexto degradado.", "1.5", False),
    ("NO_OPEN_MINUTES_BEFORE_CLOSE", "Portón operativo", "Minutos antes del cierre en que se deja de abrir posiciones.", "20", False),
    ("CLOCK_DRIFT_MAX_SECONDS", "Portón operativo", "Desfasaje de reloj tolerado antes de frenar todo.", "120", False),
    # --- v16.0 · Datos históricos e IOL ---
    ("IOL_ENABLED", "Datos históricos", "Activa el conector de Invertir Online. En false no se hace ninguna llamada.", "false", False),
    ("IOL_USERNAME", "Datos históricos", "Usuario de IOL.", "tu_usuario", True),
    ("IOL_PASSWORD", "Datos históricos", "Contraseña de IOL.", "tu_clave", True),
    ("IOL_TIMEOUT_SECONDS", "Datos históricos", "Tiempo máximo de espera por llamada a IOL.", "12", False),
    ("IOL_RATE_LIMIT_SLEEP", "Datos históricos", "Pausa entre llamadas a IOL. Protege la cuota mensual.", "0.25", False),
    ("HIST_DB_PATH", "Datos históricos", "Archivo del histórico de precios.", "./data/market_history.db", False),
    ("HIST_DEFAULT_DAYS", "Datos históricos", "Días de historia que se bajan por instrumento.", "365", False),
    ("HIST_MIN_DAYS_USABLE", "Datos históricos", "Mínimo de velas para que las métricas históricas tengan sentido.", "90", False),
    ("HIST_BATCH_SLEEP", "Datos históricos", "Pausa entre instrumentos durante la descarga masiva.", "0.25", False),
    ("DOBLE_CHEQUEO_UMBRAL_PCT", "Datos históricos", "Diferencia con Yahoo a partir de la cual el precio de PPI se considera sospechoso.", "5.0", False),
    ("MACRO_PUBLICATION_LAG_DAYS", "Datos históricos", "Desfasaje de publicación del IPC. Evita que el backtest use datos del futuro.", "20", False),
    # --- v16.0 · Costos ---
    ("BYMA_FEE_TAXED", "Costos", "Si los derechos de mercado tributan IVA. En true suman 0,0968% por tramo en vez de 0,08%.", "true", False),
    # --- v16.0 · Salud y SRE ---
    ("HEALTH_CACHE_SECONDS", "Salud y SRE", "Cuánto se cachea el resultado de las sondas de salud.", "60", False),
    ("HEALTH_SLOW_MS", "Salud y SRE", "Latencia a partir de la cual una API se marca en amarillo.", "2500", False),
    ("SRE_DEPLOY_WINDOW_START_HOUR", "Salud y SRE", "Hora desde la que se pueden aplicar mejoras del SRE.", "20", False),
    ("SRE_DEPLOY_WINDOW_END_HOUR", "Salud y SRE", "Hora hasta la que se pueden aplicar mejoras.", "22", False),
    ("SRE_CONFIRM_TTL_SECONDS", "Salud y SRE", "Vigencia del código de confirmación de Telegram.", "900", False),
    ("SRE_AUTOTEST_TIMEOUT", "Salud y SRE", "Tiempo máximo de cada paso del autotest antes de darlo por fallido.", "300", False),
    ("SRE_BACKUP_DIR", "Salud y SRE", "Dónde se guardan los respaldos previos a cada cambio.", "./data/sre_backups", False),
    ("PROPOSALS_DIR", "Salud y SRE", "Dónde viven las propuestas de mejora del motor SRE.", "./data/proposals", False),

    # --- v16.1 · Comandos de Telegram ---
    ("TELEGRAM_COMMAND_POLL_SECONDS", "Comandos Telegram", "Cada cuántos segundos se consultan comandos nuevos.", "5", False),
    ("TELEGRAM_CONFIRM_TTL_SECONDS", "Comandos Telegram", "Vigencia del código de confirmación de una parada de emergencia.", "300", False),
    # --- v16.1 · Griegas ---
    ("GREEKS_RISK_FREE_DEFAULT", "Griegas", "Tasa libre de riesgo de respaldo si no hay caución disponible. Se usa para despejar la volatilidad implícita.", "0.29", False),
    # --- v16.1 · IA ---
    ("GEMINI_TIMEOUT_SECONDS", "IA", "Segundos máximos que se espera una decisión de la IA antes de pasar al respaldo.", "15", False),
    ("MAX_TICKER_EXPOSURE_PCT", "IA", "Exposición máxima por papel que el guardián posinferencia hace respetar.", "40", False),
]
