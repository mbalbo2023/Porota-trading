"""
m_instrument_universe.py — Universo de instrumentos, descubrimiento
automático y filtro de liquidez (v10.3)

CAMBIO CENTRAL DE ESTA REVISIÓN: antes, el bot solo miraba los
instrumentos que vos habías cargado a mano en n_instrument_watchlist.json
— una lista fija que había que editar vos mismo para agregar o sacar
algo. Ahora, si AUTO_DISCOVER_INSTRUMENTS está en "true" (default), el
bot le PREGUNTA a PPI qué instrumentos existen de cada tipo, en vez de
depender de que vos hayas anticipado todos los tickers posibles. Vos ya
no decidís "qué mirar" — el bot mira todo lo que PPI le devuelve para
las categorías configuradas, y el filtro de liquidez (más abajo) se
encarga de descartar lo que no conviene analizar.

QUÉ CLASES DE ACTIVO ENTRAN AL UNIVERSO, Y QUIÉN LO DECIDE: ninguna clase
queda excluida por su nombre. Antes había una lista escrita a mano que
dejaba afuera OPCIONES, FUTUROS, CAUCIONES y FCI de una vez y para
siempre. Ahora el descubrimiento trae TODO lo que PPI informe, y cada
instrumento pasa por una PRUEBA DE CAPACIDAD (ai_derivatives_engine.py)
que responde una pregunta concreta sobre ese instrumento puntual: ¿se
puede calcular cuánto se pierde como máximo, y por lo tanto cuántas
unidades comprar para arriesgar el % de la cuenta configurado? Los que
pasan, operan. Los que no, quedan registrados con el DATO EXACTO que
falta —el vencimiento, la garantía, el multiplicador del contrato— en vez
de con una prohibición de categoría. Esa diferencia importa en la
práctica: un dato que falta se puede conseguir, y el día que aparezca el
instrumento entra solo, sin tocar código.

LÍMITE HONESTO que sigue vigente: no hay forma de confirmar, sin una
cuenta activa contra Sandbox, exactamente qué devuelve
search_instrument() con una búsqueda amplia. Por eso el descubrimiento
automático es la fuente PRIMARIA pero nunca la única: si por cualquier
motivo no devuelve nada (la cuenta no tiene el permiso, el mercado
especificado no aplica, lo que sea), el sistema cae solo a la watchlist
curada de n_instrument_watchlist.json — nunca se queda sin nada para
analizar.
"""

import json
import logging
import os
from dataclasses import dataclass
from typing import List

logger = logging.getLogger("instrument_universe")

WATCHLIST_PATH = os.getenv("INSTRUMENT_WATCHLIST_PATH", "n_instrument_watchlist.json")
MIN_LIQUIDITY_ARS = float(os.getenv("MIN_LIQUIDITY_ARS", "5000000"))  # $5M ARS de volumen mínimo del día

AUTO_DISCOVER_INSTRUMENTS = os.getenv("AUTO_DISCOVER_INSTRUMENTS", "true").lower() == "true"
MAX_DISCOVERED_PER_TYPE = int(os.getenv("MAX_DISCOVERED_PER_TYPE", "40"))
AUTO_DISCOVER_ALL_TYPES = os.getenv("AUTO_DISCOVER_ALL_TYPES", "true").lower() == "true"
HISTORICAL_UNIVERSE_FALLBACK = os.getenv(
    "HISTORICAL_UNIVERSE_FALLBACK", "true").lower() == "true"

# Las 4 clases de activo que el motor de riesgo actual sabe OPERAR (calcular
# stop-loss, tamaño de posición, etc.) de forma segura.
DISCOVERABLE_TYPES = {
    "CEDEARS": {"instrument_type": "CEDEARS", "settlement": "A-24HS"},
    "ACCIONES": {"instrument_type": "ACCIONES", "settlement": "A-24HS"},
    "BONOS": {"instrument_type": "BONOS", "settlement": "A-24HS"},
    "ETFS": {"instrument_type": "ETF", "settlement": "A-24HS"},
}

# El descubrimiento ya no separa "clases que se operan" de "clases que solo
# se miran". Todas las clases que PPI informe se descubren por igual; lo que
# decide si un instrumento concreto puede operarse es la prueba de capacidad
# de más abajo, no su pertenencia a este diccionario.
DERIVATIVE_TYPES = {
    "OPCIONES": {"instrument_type": "OPCIONES", "settlement": "A-24HS"},
    "FUTUROS": {"instrument_type": "FUTUROS", "settlement": "A-24HS"},
}

OTHER_TYPES = {
    "CAUCIONES": {"instrument_type": "CAUCIONES", "settlement": "INMEDIATA"},
    "FCI": {"instrument_type": "FCI", "settlement": "INMEDIATA"},
}

ALL_TYPES = {**DISCOVERABLE_TYPES, **DERIVATIVE_TYPES, **OTHER_TYPES}

# Interruptor para el caso en que quieras volver al comportamiento anterior
# sin editar código: en "false", los derivados se descubren y se muestran
# pero no se operan. Queda en "true" por default, que es el comportamiento
# pedido para esta versión.
TRADE_DERIVATIVES = os.getenv("TRADE_DERIVATIVES", "true").lower() == "true"


@dataclass
class Instrument:
    ticker: str
    instrument_type: str
    settlement: str
    asset_class: str  # "CEDEARS" | "ACCIONES" | "BONOS" | "ETFS"


def discover_instruments_dynamically(ppi_client) -> List[Instrument]:
    """
    Le pregunta a PPI qué instrumentos hay de cada tipo, en vez de
    depender de una lista fija. Es la fuente PRIMARIA del universo si
    AUTO_DISCOVER_INSTRUMENTS=true (default).
    """
    discovered = []
    for asset_class, cfg in ALL_TYPES.items():
        try:
            results = ppi_client.search_instruments(cfg["instrument_type"])
        except Exception as e:
            # AMPLIADO v10.5 — auditoría 3, hallazgo MEDIO "Truncamiento en
            # la Búsqueda de Instrumentos por Tipo": se distingue en el log
            # una excepción real (problema de red/API) de una respuesta
            # vacía sin error (sección con 0 resultados, o filtro de
            # mercado que no aplica) — antes ambos casos se veían igual en
            # los logs, dificultando saber si conviene reintentar o si
            # simplemente no hay nada de ese tipo hoy.
            logger.warning("Descubrimiento automático falló para %s (excepción de red/API): %s", asset_class, e)
            results = None

        if results is None:
            continue
        if not results:
            logger.info("Descubrimiento automático — %s: la API respondió sin error pero sin instrumentos "
                        "(0 resultados, no una falla de red).", asset_class)
            continue

        for r in results:
            ticker = r.get("ticker") or r.get("symbol")
            if not ticker:
                continue
            discovered.append(Instrument(
                ticker=ticker,
                instrument_type=cfg["instrument_type"],
                settlement=cfg["settlement"],
                asset_class=asset_class,
            ))
        logger.info("Descubrimiento automático — %s: %d instrumentos encontrados.",
                    asset_class, len(results))
    return discovered


def load_watchlist() -> List[Instrument]:
    """La lista curada a mano — sigue existiendo como respaldo, y como
    forma de agregar algo puntual que el descubrimiento automático no
    haya encontrado (por ejemplo, si querés asegurarte de que un ticker
    específico SIEMPRE se evalúe)."""
    if not os.path.exists(WATCHLIST_PATH):
        logger.warning("No se encontró %s — se sigue solo con el descubrimiento automático (si está activo).",
                        WATCHLIST_PATH)
        return []
    with open(WATCHLIST_PATH, encoding="utf-8") as f:
        data = json.load(f)
    instruments = []
    for asset_class, block in data.items():
        if asset_class.startswith("_"):
            continue
        for ticker in block.get("tickers", []):
            instruments.append(Instrument(
                ticker=ticker,
                instrument_type=block["instrument_type"],
                settlement=block["settlement"],
                asset_class=asset_class,
            ))
    return instruments


def detect_account_permissions(ppi_client) -> dict:
    """Search visibility + local adapter truth; never call order routes.

    RC6 forbids Budget/Confirm/Cancel as permission probes. Search visibility
    proves discoverability only. ``order_param_adapter`` is pure local code and
    is used only to distinguish local adapter support from broker permission.
    Broker execution permission remains NOT_PROVEN.
    """
    import ac_db
    from datetime import date
    from c_ppi_client import order_param_adapter

    permisos = {}
    for asset_class, cfg in ALL_TYPES.items():
        estado = {
            "puede_operar": False,
            "motivo": "",
            "instrumentos_visibles": 0,
            "estado_verificacion": "EXECUTION_NOT_PROVEN_NO_ORDER_ROUTE_PROBE",
        }
        kind = str(cfg.get("instrument_type") or "").upper().strip()
        try:
            order_param_adapter(kind)
            local_adapter = True
        except (NotImplementedError, TypeError, ValueError):
            local_adapter = False
            estado["estado_verificacion"] = "LOCAL_ADAPTER_UNAVAILABLE"

        try:
            resultados = ppi_client.search_instruments(kind) or []
        except Exception as exc:
            estado["motivo"] = f"La búsqueda read-only de instrumentos falló: {type(exc).__name__}."
            if local_adapter:
                estado["estado_verificacion"] = "SEARCH_FAILED_EXECUTION_NOT_PROVEN"
            else:
                estado["motivo"] += " Sin adaptador específico de orden; ni se verificó permiso PPI."
            permisos[asset_class] = estado
            continue

        estado["instrumentos_visibles"] = len(resultados)
        if not local_adapter:
            estado["motivo"] = (
                "Sin adaptador específico de orden; no se consultó presupuesto ni se verificó permiso PPI. "
                "La búsqueda read-only sólo demuestra visibilidad y no constituye una denegación del broker."
            )
        elif resultados:
            estado["motivo"] = (
                "Instrumentos visibles y adaptador local genérico disponible; permiso de ejecución "
                "del broker NOT_PROVEN. No se consultó Budget/Confirm/Cancel."
            )
        else:
            estado["motivo"] = (
                "Adaptador local genérico disponible, pero la búsqueda read-only no devolvió instrumentos; "
                "permiso de ejecución del broker NOT_PROVEN. No se consultó Budget/Confirm/Cancel."
            )
        permisos[asset_class] = estado
        logger.info("Capacidad de cuenta — %s: %s (%s)", asset_class,
                    estado["estado_verificacion"], estado["motivo"])

    try:
        conn = ac_db.connect_raw()
        c = conn.cursor()
        c.execute("""CREATE TABLE IF NOT EXISTS account_permissions (
            date TEXT PRIMARY KEY, data_json TEXT)""")
        c.execute("INSERT OR REPLACE INTO account_permissions (date, data_json) VALUES (?, ?)",
                  (date.today().isoformat(), json.dumps(permisos, ensure_ascii=False)))
        conn.commit(); conn.close()
    except Exception as exc:
        logger.error("No se pudieron guardar los permisos de cuenta: %s", exc)
    return permisos


def _clase_del_subyacente(inst, ppi_client) -> str:
    """Devuelve ACCIONES o CEDEARS según el subyacente de la opción.

    Se resuelve consultando al bróker, no por una lista fija: el conjunto de
    CEDEARs con opciones listadas cambia, y una lista hardcodeada se
    desactualiza en silencio hacia el lado peligroso (tratar un CEDEAR como
    acción usa lote 100 en vez de 10).
    """
    import re as _re
    raiz = _re.match(r"([A-Z]{2,5})", (inst.ticker or "").upper())
    if not raiz:
        return ""
    simbolo = raiz.group(1)
    for clase in ("CEDEARS", "ACCIONES"):
        try:
            datos = ppi_client.get_market_data(simbolo, clase, "A-24HS")
            if datos and datos.get("price"):
                return clase
        except Exception:
            continue
    return ""


def assess_derivative_eligibility(inst, ppi_client, permisos: dict) -> dict:
    """
    Corre la prueba de capacidad sobre un derivado concreto y devuelve el
    veredicto con su motivo. Esta función es la que reemplaza a la lista fija:
    ninguna decisión se toma por el nombre de la categoría.
    """
    import ai_derivatives_engine as deriv

    if not TRADE_DERIVATIVES:
        return {"operable": False, "codigo": "DERIVADOS_DESACTIVADOS",
                "motivo": "TRADE_DERIVATIVES está en false en la configuración."}

    permiso = permisos.get(inst.asset_class, {})
    if not permiso.get("puede_operar", False):
        return {"operable": False, "codigo": "SIN_PERMISO_DE_CUENTA",
                "motivo": permiso.get("motivo", "La cuenta no tiene habilitado este tipo de instrumento.")}

    if inst.asset_class == "OPCIONES":
        # CORREGIDO EN v16.2 — el único call-site real invocaba
        # parse_option_ticker(ticker) sin el tipo de subyacente, así que
        # lote_por_subyacente("") devolvía 100 también para CEDEARs. El lote
        # correcto estaba resuelto en el motor y no llegaba al camino que se
        # ejecuta: opciones sobre acciones son de 100 nominales, las de
        # CEDEARs de 10. Aplicar 100 a un CEDEAR multiplica por diez la
        # posición y la pérdida máxima real frente a la calculada — el
        # sistema creía arriesgar el 1% y arriesgaba el 10%.
        spec = deriv.parse_option_ticker(
            inst.ticker, tipo_subyacente=_clase_del_subyacente(inst, ppi_client))
        if not spec.underlying:
            return {"operable": False, "codigo": spec.blocking_code, "motivo": spec.blocking_reason}

        mkt = ppi_client.get_market_data(inst.ticker, inst.instrument_type, inst.settlement)
        prima = (mkt or {}).get("price", 0) or 0
        subyacente = ppi_client.get_market_data(spec.underlying, "ACCIONES", "A-24HS") or {}
        precio_subyacente = subyacente.get("price")

        book = ppi_client.get_book(inst.ticker, inst.instrument_type, inst.settlement)
        spread_pct = 0.0
        if book and book.get("bids") and book.get("offers"):
            bid = book["bids"][0]["price"]
            if bid > 0:
                spread_pct = (book["offers"][0]["price"] - bid) / bid * 100

        # El dato de la API tiene prioridad absoluta sobre lo deducido del
        # texto del ticker. Deducir del nombre es el último recurso.
        payload = (mkt or {})
        if payload.get("strike"):
            try:
                spec.strike = float(payload["strike"])
                spec.strike_source = "api"
            except (TypeError, ValueError):
                pass
        if payload.get("lotSize"):
            try:
                spec.lot_size = int(payload["lotSize"])
                spec.lot_size_source = "api"
            except (TypeError, ValueError):
                pass

        spec = deriv.assess_option(spec, prima, precio_subyacente, spread_pct, side="LONG")
        return {"operable": spec.capable, "codigo": spec.blocking_code,
                "motivo": spec.blocking_reason or "Pérdida máxima acotada por la prima: dimensionable.",
                "spec": spec}

    if inst.asset_class == "FUTUROS":
        payload = dict(getattr(inst, "api_payload", None) or {})

        # NUEVO EN v16.2 — acá se destraba el pendiente de futuros. El bróker
        # de ejecución no informa multiplicador de contrato ni garantía
        # inicial, y sin esos dos números el dimensionamiento no tiene
        # solución. Se los pide al mercado que sí los publica: la API Primary
        # de Matba Rofex para el multiplicador, el reporte de cuenta para la
        # garantía. Si el conector está apagado o no responde, el instrumento
        # queda no operable con el motivo exacto — nunca con un valor de
        # tabla, porque un multiplicador supuesto es una suposición sobre el
        # contrato específico que se está por operar.
        try:
            import av_rofex_client as rofex
            contrato = rofex.datos_de_contrato(inst.ticker)
            if contrato.multiplicador:
                payload.setdefault("contractMultiplier", contrato.multiplicador)
            if contrato.garantia_inicial:
                payload.setdefault("initialMargin", contrato.garantia_inicial)
            if contrato.vencimiento:
                payload.setdefault("expirationDate", contrato.vencimiento)
            if not contrato.operable and contrato.motivo:
                logger.info("Futuro %s no operable todavía: %s", inst.ticker, contrato.motivo)
        except Exception as e:
            logger.warning("No se pudieron traer los datos de contrato de %s: %s",
                           inst.ticker, e)

        spec = deriv.describe_future(inst.ticker, payload)
        return {"operable": spec.capable, "codigo": spec.blocking_code,
                "motivo": spec.blocking_reason or "Multiplicador y garantía informados: dimensionable.",
                "spec": spec}

    return {"operable": True, "codigo": "", "motivo": "Instrumento de contado: modelo de riesgo estándar."}


def build_universe(ppi_client) -> List[Instrument]:
    """
    Junta las dos fuentes (descubrimiento automático + watchlist curada),
    sin duplicar tickers. Esta es la función que llama j_main.py — ya no
    llama a load_watchlist() sola, para que el cambio de esta revisión
    (descubrimiento automático) quede activo por default sin que tengas
    que cambiar nada más.
    """
    seen = set()
    universe = []

    if AUTO_DISCOVER_INSTRUMENTS:
        for inst in discover_instruments_dynamically(ppi_client):
            key = (inst.ticker, inst.instrument_type)
            if key not in seen:
                seen.add(key)
                universe.append(inst)

    for inst in load_watchlist():
        key = (inst.ticker, inst.instrument_type)
        if key not in seen:
            seen.add(key)
            universe.append(inst)

    if HISTORICAL_UNIVERSE_FALLBACK:
        try:
            import ba_data912_history as data912
            archived = data912.archived_instruments()
            for row in archived:
                asset_class = row["asset_class"]
                config = DISCOVERABLE_TYPES.get(asset_class)
                if not config:
                    continue
                inst = Instrument(
                    ticker=row["symbol"],
                    instrument_type=config["instrument_type"],
                    settlement=config["settlement"],
                    asset_class=asset_class,
                )
                key = (inst.ticker, inst.instrument_type)
                if key not in seen:
                    seen.add(key)
                    universe.append(inst)
            logger.info("Archivo histórico aportó %d instrumentos candidatos.", len(archived))
        except Exception as exc:
            logger.warning("No se pudo sumar el universo histórico local: %s", exc)

    if not universe:
        logger.error("El universo de instrumentos quedó vacío — ni el descubrimiento automático "
                      "ni la watchlist devolvieron nada. Revisar conexión con PPI.")
    return universe


def filter_by_liquidity(instruments: List[Instrument], ppi_client, days_back: int = 5) -> List[Instrument]:
    """
    Ordena por liquidez y aplica el límite DESPUÉS del filtro. PPI es la
    fuente histórica primaria. Una prueba inicial evita repetir cientos de
    autenticaciones si el histórico PPI está caído; en ese caso se usa el
    archivo local Data912 y se conserva PPI para precio, book y ejecución.
    """
    import ba_data912_history as data912

    by_class = {}
    pinned = {(item.ticker, item.instrument_type) for item in load_watchlist()}
    ppi_history_enabled = False
    probe_cache = {}
    probe = next((item for item in instruments if item.ticker == "GGAL"),
                 instruments[0] if instruments else None)
    if probe is not None:
        try:
            probe_history = ppi_client.get_historical_series(
                probe.ticker, probe.instrument_type, probe.settlement, days_back)
            if probe_history:
                ppi_history_enabled = True
                probe_cache[(probe.ticker, probe.instrument_type)] = probe_history
                logger.info("Histórico PPI disponible; se usa como fuente primaria.")
            else:
                logger.warning("Histórico PPI no disponible; se usa Data912 como respaldo.")
        except Exception as exc:
            logger.warning("Prueba de histórico PPI falló; se usa Data912: %s", exc)

    for inst in instruments:
        traded_ars = None
        if ppi_history_enabled:
            try:
                history = probe_cache.get((inst.ticker, inst.instrument_type))
                if history is None:
                    history = ppi_client.get_historical_series(
                        inst.ticker, inst.instrument_type, inst.settlement, days_back)
                if history:
                    volumes = [row.get("volume", 0) or 0 for row in history]
                    prices = [row.get("price", 0) or 0 for row in history]
                    avg_volume = sum(volumes) / len(volumes) if volumes else 0
                    avg_price = sum(prices) / len(prices) if prices else 0
                    traded_ars = (avg_price * avg_volume
                                  if inst.asset_class != "BONOS" else avg_volume)
            except Exception as exc:
                logger.info("PPI no entregó histórico de %s: %s", inst.ticker, exc)

        if traded_ars is None:
            traded_ars = data912.archived_liquidity(
                inst.ticker, inst.asset_class, days_back)
        if traded_ars is None:
            try:
                if data912.ensure_symbol(inst.ticker, inst.asset_class):
                    traded_ars = data912.archived_liquidity(
                        inst.ticker, inst.asset_class, days_back)
            except Exception as exc:
                logger.info("Data912 no completó %s: %s", inst.ticker, exc)

        if traded_ars is None:
            logger.warning("%s queda fuera de la preselección: sin histórico PPI ni Data912.",
                           inst.ticker)
            continue

        if traded_ars >= MIN_LIQUIDITY_ARS:
            by_class.setdefault(inst.asset_class, []).append((traded_ars, inst))
        else:
            logger.info("Descartado por liquidez: %s (%.0f ARS promedio/día, mínimo %.0f)",
                        inst.ticker, traded_ars, MIN_LIQUIDITY_ARS)

    liquid = []
    for asset_class, candidates in by_class.items():
        candidates.sort(
            key=lambda item: (
                (item[1].ticker, item[1].instrument_type) in pinned,
                item[0],
            ),
            reverse=True,
        )
        selected = (candidates if MAX_DISCOVERED_PER_TYPE <= 0
                    else candidates[:MAX_DISCOVERED_PER_TYPE])
        liquid.extend(inst for _score, inst in selected)
        logger.info("Universo %s: %d líquidos, %d seleccionados después de ordenar.",
                    asset_class, len(candidates), len(selected))
    return liquid
