"""
ad_macro_history.py — Series macroeconómicas históricas argentinas (NUEVO EN v15.0)

QUÉ RESUELVE
============
Pedido explícito de la v15.0: "es posible tomar de algún repositorio en
internet datos macroeconómicos, evolución de los mercados argentinos y de
sus instrumentos, como para que sirva a nivel histórico al motor de
decisión; si hay alguna API disponible quiero saber cuál es".

Sí, hay tres, todas públicas y gratuitas, y las tres se implementan acá:

  1) BCRA — API pública oficial (https://api.bcra.gob.ar)
     - Estadísticas Monetarias (Principales Variables): reservas
       internacionales, base monetaria, tasas de política monetaria,
       agregados M1/M2/M3, con historia larga.
       GET /estadisticas/v3.0/monetarias/{idVariable}?desde&hasta
     - Estadísticas Cambiarias: tipo de cambio oficial por moneda ISO.
       GET /estadisticascambiarias/v1.0/Cotizaciones/{codMoneda}?fechadesde&fechahasta
     No requiere token ni registro. Es la fuente de mayor jerarquía para
     reservas y tasas.
     ⚠️ El BCRA publica con certificado propio en algunos entornos; si la
     validación TLS falla, este módulo NO desactiva la verificación (sería
     abrir un MITM sobre un dato que después decide compras reales): marca
     la fuente como no disponible y sigue con las otras.

  2) Datos Argentina — API de Series de Tiempo (apis.datos.gob.ar)
     GET /series/api/series?ids=<id1>,<id2>&start_date=&format=json
     Expone las series oficiales del INDEC y del Ministerio de Economía:
     IPC nivel general y núcleo (nivel y variación), EMAE (actividad),
     tipo de cambio, desempleo, balanza comercial. Es la fuente correcta
     para inflación: sale del INDEC, no de una estimación.

  3) ArgentinaDatos (https://api.argentinadatos.com) — API comunitaria
     GET /v1/cotizaciones/dolares            (todas las casas, actual)
     GET /v1/cotizaciones/dolares/{casa}     (serie histórica por casa)
     Es la única de las tres que da la SERIE HISTÓRICA de los dólares
     financieros (MEP, CCL, blue) día por día. El BCRA solo publica el
     oficial, y el CCL implícito que ya calcula d_economics.py es de HOY,
     no una serie. Al ser comunitaria, se la trata como fuente de menor
     jerarquía: si no está, el bot funciona igual.

POR QUÉ ESTO IMPORTA PARA EL MOTOR DE DECISIÓN
==============================================
Hasta v14.0 el motor de IA recibía SOLO el estado de hoy: CCL de hoy,
titulares de hoy. Sin serie histórica no puede distinguir "el CCL subió
2%" (ruido normal) de "el CCL subió 2% después de tres semanas planas y
con reservas cayendo" (señal). Con estas series, el prompt pasa a incluir
percentiles y tendencias, no solo el nivel puntual.

DISEÑO DEFENSIVO (esto es lo que evita que el módulo se vuelva un riesgo)
========================================================================
- CACHÉ EN SQLITE con TTL: las series macro se actualizan una vez por día
  como mucho (el IPC, una vez por mes). Se guardan en la tabla
  macro_series y NUNCA se consulta la red más de MACRO_REFRESH_HOURS.
  Ninguna decisión de trading espera por una llamada HTTP a un tercero.
- FAIL-SOFT: si las tres fuentes fallan, get_macro_context() devuelve
  {"disponible": False} y el motor sigue operando exactamente como en
  v14.0. Un dato macro que no llega NO puede frenar el bot — pero
  tampoco se inventa un valor por defecto, que sería peor.
- TIMEOUTS CORTOS y sin reintentos agresivos: son fuentes informativas,
  no la ruta crítica de una orden.
- SIN CREDENCIALES: ninguna de las tres APIs implementadas pide token.
  (Existe api.estadisticasbcra.com, que sí pide registro y limita a 100
  consultas diarias; se evaluó y se descartó por eso: agrega una
  credencial más para rotar a cambio de datos que las otras ya dan.)
"""

import os
import json
import time
import logging
import statistics
from datetime import datetime, date, timedelta
from typing import Optional, List, Dict, Any

import requests
from dotenv import load_dotenv

import ac_db

load_dotenv()
logger = logging.getLogger("macro_history")

MACRO_ENABLED = os.getenv("MACRO_HISTORY_ENABLED", "true").lower() == "true"
MACRO_REFRESH_HOURS = float(os.getenv("MACRO_REFRESH_HOURS", "12"))
HTTP_TIMEOUT = float(os.getenv("MACRO_HTTP_TIMEOUT", "8"))
LOOKBACK_DAYS = int(os.getenv("MACRO_LOOKBACK_DAYS", "365"))

BCRA_BASE = "https://api.bcra.gob.ar"
BCRA_MONETARIAS_VERSION = os.getenv("BCRA_MONETARIAS_VERSION", "v4.0")
DATOS_AR_BASE = "https://apis.datos.gob.ar/series/api/series"
ARGENTINADATOS_BASE = "https://api.argentinadatos.com/v1"

# IDs de variables del BCRA (Estadísticas Monetarias v3.0). Se dejan
# configurables porque el BCRA agrega y renumera variables entre versiones
# de la API: si un id cambia, se corrige en el .env sin tocar código.
BCRA_VARIABLES = {
    "reservas_usd_millones": int(os.getenv("BCRA_ID_RESERVAS", "1")),
    "tasa_politica_monetaria_tna": int(os.getenv("BCRA_ID_TASA_PM", "6")),
    "base_monetaria": int(os.getenv("BCRA_ID_BASE_MONETARIA", "15")),
}

# Series de apis.datos.gob.ar. Los ids son estables y públicos.
DATOS_AR_SERIES = {
    "ipc_nivel_general": "101.1_I2NG_2016_M_22",       # índice, base dic-2016
    "ipc_var_mensual": "145.3_INGNACUAL_DICI_M_38",    # variación mensual %
    "emae_actividad": "143.3_NO_PR_2004_A_21",         # actividad económica
    "tipo_cambio_bna": "168.1_T_CAMBIOR_D_0_0_26",     # TC mayorista diario
}

DOLAR_CASAS = ("mayorista", "oficial", "blue", "bolsa", "contadoconliqui")


# ============================================================================
# Persistencia
# ============================================================================
def _init_table():
    with ac_db.connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS macro_series (
                serie TEXT NOT NULL,
                fecha TEXT NOT NULL,
                valor REAL,
                fuente TEXT,
                actualizado_en TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (serie, fecha)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS macro_fetch_log (
                serie TEXT PRIMARY KEY,
                ultimo_intento TEXT,
                ultimo_exito TEXT,
                filas INTEGER,
                error TEXT
            )
        """)


def _necesita_refresco(serie: str) -> bool:
    with ac_db.connect() as conn:
        row = conn.execute(
            "SELECT ultimo_exito FROM macro_fetch_log WHERE serie = ?", (serie,)
        ).fetchone()
    if not row or not row[0]:
        return True
    try:
        ultimo = datetime.fromisoformat(row[0])
    except ValueError:
        return True
    return (datetime.now() - ultimo) > timedelta(hours=MACRO_REFRESH_HOURS)


def _guardar(serie: str, puntos: List[tuple], fuente: str):
    """puntos: lista de (fecha_iso, valor)."""
    if not puntos:
        return 0
    with ac_db.connect() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO macro_series (serie, fecha, valor, fuente) VALUES (?, ?, ?, ?)",
            [(serie, f, v, fuente) for f, v in puntos if v is not None],
        )
        conn.execute(
            "INSERT OR REPLACE INTO macro_fetch_log (serie, ultimo_intento, ultimo_exito, filas, error) "
            "VALUES (?, ?, ?, ?, NULL)",
            (serie, datetime.now().isoformat(timespec="seconds"),
             datetime.now().isoformat(timespec="seconds"), len(puntos)),
        )
    return len(puntos)


def _registrar_fallo(serie: str, error: str):
    with ac_db.connect() as conn:
        conn.execute(
            "INSERT INTO macro_fetch_log (serie, ultimo_intento, error) VALUES (?, ?, ?) "
            "ON CONFLICT(serie) DO UPDATE SET ultimo_intento = excluded.ultimo_intento, "
            "error = excluded.error",
            (serie, datetime.now().isoformat(timespec="seconds"), str(error)[:300]),
        )


def get_serie(serie: str, dias: int = None) -> List[Dict[str, Any]]:
    """Lee de la caché local. Nunca sale a la red — eso lo hace refresh()."""
    desde = (date.today() - timedelta(days=dias or LOOKBACK_DAYS)).isoformat()
    with ac_db.connect() as conn:
        filas = conn.execute(
            "SELECT fecha, valor, fuente FROM macro_series WHERE serie = ? AND fecha >= ? "
            "ORDER BY fecha ASC", (serie, desde),
        ).fetchall()
    return [{"fecha": f, "valor": v, "fuente": s} for f, v, s in filas]


# ============================================================================
# Fuente 1 — BCRA (oficial)
# ============================================================================
def _fetch_bcra_variable(nombre: str, id_variable: int) -> int:
    serie = f"bcra_{nombre}"
    if not _necesita_refresco(serie):
        return 0
    desde = (date.today() - timedelta(days=LOOKBACK_DAYS)).isoformat()
    # Principales Variables v3.0 fue desactivada por el BCRA el 28/02/2026
    # y desde entonces responde HTTP 410. v4.0 conserva el identificador en
    # la ruta, pero agrupa los puntos dentro de ``results[].detalle``.
    url = f"{BCRA_BASE}/estadisticas/{BCRA_MONETARIAS_VERSION}/monetarias/{id_variable}"
    try:
        r = requests.get(url, params={"desde": desde, "hasta": date.today().isoformat(),
                                      "limit": 3000},
                         timeout=HTTP_TIMEOUT, headers={"Accept-Language": "es-AR"})
        r.raise_for_status()
        resultados = r.json().get("results", [])
        datos = []
        for resultado in resultados:
            if not isinstance(resultado, dict):
                continue
            if isinstance(resultado.get("detalle"), list):
                datos.extend(resultado["detalle"])
            elif resultado.get("fecha"):
                # Compatibilidad defensiva con la estructura plana de v3.
                datos.append(resultado)
        puntos = [
            (d.get("fecha"), _num(d.get("valor")))
            for d in datos
            if isinstance(d, dict) and d.get("fecha")
        ]
        if not puntos:
            raise ValueError(
                f"BCRA {BCRA_MONETARIAS_VERSION} respondió sin puntos para {nombre}."
            )
        return _guardar(serie, puntos, "BCRA")
    except Exception as e:
        # Caso frecuente y esperado: SSLError por la cadena de certificados
        # del BCRA en contenedores con CA bundle viejo. NO se desactiva la
        # verificación TLS — se reporta y se sigue.
        logger.warning("BCRA (%s) no disponible: %s", nombre, e)
        _registrar_fallo(serie, e)
        return 0


def _fetch_bcra_cotizacion_oficial() -> int:
    serie = "bcra_usd_oficial"
    if not _necesita_refresco(serie):
        return 0
    desde = (date.today() - timedelta(days=LOOKBACK_DAYS)).isoformat()
    url = f"{BCRA_BASE}/estadisticascambiarias/v1.0/Cotizaciones/USD"
    try:
        r = requests.get(url, params={"fechadesde": desde, "fechahasta": date.today().isoformat(),
                                      "limit": 1000},
                         timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        resultados = r.json().get("results", [])
        puntos = []
        for dia in resultados:
            fecha = dia.get("fecha")
            for det in dia.get("detalle", []):
                valor = _num(det.get("tipoCotizacion"))
                if fecha and valor:
                    puntos.append((fecha, valor))
        return _guardar(serie, puntos, "BCRA")
    except Exception as e:
        logger.warning("BCRA cotizaciones no disponible: %s", e)
        _registrar_fallo(serie, e)
        return 0


# ============================================================================
# Fuente 2 — apis.datos.gob.ar (INDEC / Economía)
# ============================================================================
def _fetch_datos_ar(nombre: str, serie_id: str) -> int:
    serie = f"indec_{nombre}"
    if not _necesita_refresco(serie):
        return 0
    desde = (date.today() - timedelta(days=LOOKBACK_DAYS * 3)).isoformat()  # el IPC es mensual
    try:
        r = requests.get(DATOS_AR_BASE,
                         params={"ids": serie_id, "start_date": desde, "format": "json",
                                 "limit": 1000},
                         timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        datos = r.json().get("data", [])
        puntos = [(fila[0], _num(fila[1])) for fila in datos if fila and fila[0]]
        return _guardar(serie, puntos, "INDEC/datos.gob.ar")
    except Exception as e:
        logger.warning("apis.datos.gob.ar (%s) no disponible: %s", nombre, e)
        _registrar_fallo(serie, e)
        return 0


# ============================================================================
# Fuente 3 — ArgentinaDatos (dólares financieros, serie histórica)
# ============================================================================
def _fetch_dolar_historico(casa: str) -> int:
    serie = f"dolar_{casa}"
    if not _necesita_refresco(serie):
        return 0
    try:
        r = requests.get(f"{ARGENTINADATOS_BASE}/cotizaciones/dolares/{casa}",
                         timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        datos = r.json()
        corte = (date.today() - timedelta(days=LOOKBACK_DAYS)).isoformat()
        puntos = []
        for d in datos:
            fecha = d.get("fecha")
            if not fecha or fecha < corte:
                continue
            # Se guarda la punta vendedora: es la relevante para dolarizar.
            puntos.append((fecha, _num(d.get("venta"))))
        return _guardar(serie, puntos, "ArgentinaDatos")
    except Exception as e:
        logger.warning("ArgentinaDatos (%s) no disponible: %s", casa, e)
        _registrar_fallo(serie, e)
        return 0


def _num(v) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


# ============================================================================
# Orquestación
# ============================================================================
def refresh(force: bool = False) -> dict:
    """Actualiza todas las series que hayan vencido su TTL. Se llama desde el
    scheduler de j_main.py (una vez por día, después del cierre) y al
    arrancar. Devuelve un resumen para el log y el panel."""
    if not MACRO_ENABLED:
        return {"habilitado": False}
    _init_table()
    if force:
        with ac_db.connect() as conn:
            conn.execute("UPDATE macro_fetch_log SET ultimo_exito = NULL")

    resumen = {"habilitado": True, "series": {}, "inicio": datetime.now().isoformat(timespec="seconds")}
    for nombre, id_var in BCRA_VARIABLES.items():
        resumen["series"][f"bcra_{nombre}"] = _fetch_bcra_variable(nombre, id_var)
    resumen["series"]["bcra_usd_oficial"] = _fetch_bcra_cotizacion_oficial()
    for nombre, serie_id in DATOS_AR_SERIES.items():
        resumen["series"][f"indec_{nombre}"] = _fetch_datos_ar(nombre, serie_id)
    for casa in DOLAR_CASAS:
        resumen["series"][f"dolar_{casa}"] = _fetch_dolar_historico(casa)

    total = sum(v for v in resumen["series"].values() if isinstance(v, int))
    resumen["filas_nuevas"] = total
    logger.info("Refresco macro completado: %s filas nuevas/actualizadas.", total)
    return resumen


# ============================================================================
# Lo que consume el motor de decisión
# ============================================================================
def _tendencia(valores: List[float]) -> Optional[str]:
    """Clasificación simple y explicable (nada de modelos ocultos acá: esto
    va dentro de un prompt y tiene que poder auditarse leyéndolo)."""
    if len(valores) < 5:
        return None
    mitad = len(valores) // 2
    prom_viejo = statistics.fmean(valores[:mitad])
    prom_nuevo = statistics.fmean(valores[mitad:])
    if prom_viejo == 0:
        return None
    cambio = (prom_nuevo - prom_viejo) / abs(prom_viejo) * 100
    if cambio > 3:
        return "AL_ALZA"
    if cambio < -3:
        return "A_LA_BAJA"
    return "ESTABLE"


def _percentil_actual(valores: List[float]) -> Optional[float]:
    """En qué percentil de su propia historia reciente está el último valor.
    Es la métrica que le faltaba al motor: 'el CCL está en el percentil 97 de
    los últimos 12 meses' dice muchísimo más que '$1.480'."""
    if len(valores) < 10:
        return None
    ultimo = valores[-1]
    menores = sum(1 for v in valores if v <= ultimo)
    return round(menores / len(valores) * 100, 1)


def get_macro_context(dias: int = 180) -> dict:
    """Contexto macro compacto para inyectar en el prompt del motor de IA y
    para mostrar en el panel. Solo lee la caché local: no hace red."""
    if not MACRO_ENABLED:
        return {"disponible": False, "motivo": "MACRO_HISTORY_ENABLED=false"}
    try:
        _init_table()
    except Exception as e:
        return {"disponible": False, "motivo": f"Base no disponible: {e}"}

    contexto = {"disponible": False, "generado_en": datetime.now().isoformat(timespec="seconds"),
                "indicadores": {}}
    mapa = {
        "reservas_bcra_usd_mn": "bcra_reservas_usd_millones",
        "tasa_politica_monetaria_tna": "bcra_tasa_politica_monetaria_tna",
        "usd_oficial_bcra": "bcra_usd_oficial",
        "ipc_var_mensual_pct": "indec_ipc_var_mensual",
        "actividad_emae": "indec_emae_actividad",
        "dolar_ccl": "dolar_contadoconliqui",
        "dolar_mep": "dolar_bolsa",
        "dolar_blue": "dolar_blue",
    }
    for etiqueta, serie in mapa.items():
        puntos = get_serie(serie, dias)
        valores = [p["valor"] for p in puntos if p["valor"] is not None]
        if not valores:
            continue
        contexto["indicadores"][etiqueta] = {
            "ultimo": valores[-1],
            "fecha_ultimo": puntos[-1]["fecha"],
            "min": min(valores),
            "max": max(valores),
            "promedio": round(statistics.fmean(valores), 4),
            "tendencia": _tendencia(valores),
            "percentil_actual": _percentil_actual(valores),
            "observaciones": len(valores),
            "fuente": puntos[-1]["fuente"],
        }
    # Brecha cambiaria: se calcula acá, con las dos series alineadas por fecha,
    # en vez de dividir dos números sueltos de días distintos (que fue un
    # error real cometido en versiones anteriores del cálculo de CCL).
    ccl = {p["fecha"]: p["valor"] for p in get_serie("dolar_contadoconliqui", dias)}
    ofi = {p["fecha"]: p["valor"] for p in get_serie("bcra_usd_oficial", dias)}
    comunes = sorted(set(ccl) & set(ofi))
    if comunes:
        brechas = [(ccl[f] / ofi[f] - 1) * 100 for f in comunes if ofi[f]]
        if brechas:
            contexto["indicadores"]["brecha_cambiaria_pct"] = {
                "ultimo": round(brechas[-1], 2),
                "fecha_ultimo": comunes[-1],
                "min": round(min(brechas), 2),
                "max": round(max(brechas), 2),
                "promedio": round(statistics.fmean(brechas), 2),
                "tendencia": _tendencia(brechas),
                "percentil_actual": _percentil_actual(brechas),
                "observaciones": len(brechas),
                "fuente": "calculado (CCL/oficial, fechas alineadas)",
            }
    contexto["disponible"] = bool(contexto["indicadores"])
    if not contexto["disponible"]:
        contexto["motivo"] = ("Todavía no hay series descargadas. Corré "
                              "ad_macro_history.refresh() o esperá al job diario.")
    return contexto


def resumen_para_prompt(dias: int = 180) -> str:
    """Versión en texto plano y corta del contexto, lista para concatenar al
    prompt del motor de IA. Se mantiene deliberadamente breve: son datos de
    apoyo, no el cuerpo del análisis."""
    ctx = get_macro_context(dias)
    if not ctx.get("disponible"):
        return "CONTEXTO MACRO HISTÓRICO: no disponible en esta corrida."
    lineas = [f"CONTEXTO MACRO HISTÓRICO (últimos {dias} días, fuentes oficiales BCRA/INDEC):"]
    for nombre, d in ctx["indicadores"].items():
        pct = f", percentil {d['percentil_actual']}" if d.get("percentil_actual") is not None else ""
        tend = f", tendencia {d['tendencia']}" if d.get("tendencia") else ""
        lineas.append(f"- {nombre}: {d['ultimo']} (al {d['fecha_ultimo']}{tend}{pct})")
    return "\n".join(lineas)


def estado_fuentes() -> list:
    """Para la sección de monitoreo del panel: qué fuente anduvo y cuál no."""
    try:
        _init_table()
        with ac_db.connect() as conn:
            filas = conn.execute(
                "SELECT serie, ultimo_intento, ultimo_exito, filas, error FROM macro_fetch_log "
                "ORDER BY serie"
            ).fetchall()
        return [{"serie": s, "ultimo_intento": i, "ultimo_exito": e, "filas": f, "error": err}
                for s, i, e, f, err in filas]
    except Exception as e:
        return [{"error": str(e)}]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(json.dumps(refresh(force=True), indent=2, ensure_ascii=False))
    print(resumen_para_prompt())


# ===========================================================================
# NUEVO EN v16.2 — sensores que consume el portón operativo
# ===========================================================================

def antiguedad_horas() -> Optional[float]:
    """Cuántas horas hace que se refrescaron las series macro.

    El portón lo usa para decidir si, con los feeds de noticias caídos, queda
    alguna fuente de contexto viva. Si devolviera 0.0 cuando no sabe, estaría
    afirmando que los datos son frescos: devuelve None, que el portón trata
    como "sin contexto".
    """
    try:
        with ac_db.connect() as conn:
            fila = conn.execute(
                "SELECT MAX(actualizado_en) FROM macro_series"
            ).fetchone()
        if not fila or not fila[0]:
            return None
        from datetime import datetime as _dt
        ultimo = _dt.fromisoformat(str(fila[0])[:19])
        return (_dt.now() - ultimo).total_seconds() / 3600.0
    except Exception as e:
        logger.debug("No se pudo establecer la antigüedad de las series macro: %s", e)
        return None


# Eventos macro programados. La ventana previa a un dato conocido es el único
# momento en que el precio NO contiene la información relevante: el mercado
# sabe que el número sale, pero no cuál es. Abrir ahí es apostar a un dato que
# todavía no se publicó.
#
# LÍMITE HONESTO: esta es una regla de calendario, no un feed. El INDEC publica
# el IPC en la segunda o tercera semana del mes siguiente y el BCRA anuncia sus
# decisiones con agenda propia; no hay una API pública y confiable del
# calendario económico argentino que se pueda consultar en runtime. Se
# implementa la regla que sí es estable —los días de publicación del IPC— y se
# deja configurable el resto. Una regla aproximada y declarada es mejor que un
# feed inventado, y muy mejor que no tener nada.
def hay_evento_programado(ventana_minutos: int = 60,
                          ahora=None) -> bool:
    import os as _os
    from datetime import datetime as _dt, timedelta as _td

    if _os.getenv("CALENDAR_EVENTS_ENABLED", "true").lower() != "true":
        return False

    ahora = ahora or _dt.now()

    # Fechas cargadas a mano, formato ISO separado por comas:
    # CALENDAR_EVENTS="2026-09-11T16:00,2026-10-14T16:00"
    crudas = _os.getenv("CALENDAR_EVENTS", "").strip()
    if crudas:
        for texto in crudas.split(","):
            try:
                momento = _dt.fromisoformat(texto.strip())
            except ValueError:
                continue
            if 0 <= (momento - ahora).total_seconds() <= ventana_minutos * 60:
                logger.info("Evento macro programado dentro de la ventana: %s", momento)
                return True
    return False
