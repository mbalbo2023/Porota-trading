"""
economics.py — Fricción, costos reales y hurdle rate dinámico (v2.2)

CORRIGE el hallazgo CRÍTICO "no existe cálculo de fricción/hurdle" de la
auditoría. Antes la ecuación (inflación + prima) estaba solo en la
documentación; acá ya se calcula y se usa para filtrar operaciones.

DISEÑO PEDIDO POR EL USUARIO:
  - SIN TECHO: estas funciones nunca limitan la ganancia máxima. Solo
    imponen un PISO (hurdle) por debajo del cual la operación se rechaza.
    Si una oportunidad rinde 20% neto, se acepta igual que una de 5%
    neto — ninguna función acá recorta ni cappea el retorno esperado.
  - PISO DINÁMICO, no fijo: el hurdle mensual no es "3% + 1,5%" fijo.
    Es max(inflación mensual estimada, devaluación real del CCL en el
    período) + prima de riesgo. Así, si en un mes el dólar se mueve más
    que la inflación (ej. salto devaluatorio), el sistema exige superar
    ESE número, no el de inflación viejo.
  - Ninguna fuente gratuita y confiable ofrece hoy un "API de inflación
    argentina en tiempo real". Por eso la inflación se lee de una config
    editable (macro_config.json) que el usuario actualiza con el dato
    de REM del BCRA (se publica mensualmente: https://www.bcra.gob.ar,
    sección Relevamiento de Expectativas de Mercado) o del INDEC. El
    motor de Gemini (ver gemini_decision_engine.py) también devuelve su
    propia estimación de inflación como segunda opinión, y acá se toma
    el máximo entre ambas fuentes por prudencia (nunca el mínimo: mejor
    exigirle de más al sistema que de menos).
"""

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

logger = logging.getLogger("economics")

MACRO_CONFIG_PATH = os.getenv("MACRO_CONFIG_PATH", "a_macro_config.json")
# NUEVO EN v13.0 — ver dynamic_fee_adjustment() más abajo.
FEE_STRESS_STATE_PATH = os.getenv("FEE_STRESS_STATE_PATH", "data/fee_stress_state.json")

# Costos de PPI/BYMA declarados en el documento original — confirmar
# periódicamente contra la grilla de comisiones vigente de PPI, que puede
# cambiar (https://www.portfoliopersonal.com), y contra el reglamento de
# aranceles de BYMA.
PPI_COMMISSION_PCT = float(os.getenv("PPI_COMMISSION_PCT", "0.006"))      # 0,6% por tramo
IVA_PCT = float(os.getenv("IVA_PCT", "0.21"))                              # IVA sobre la comisión
BYMA_FEE_PCT = float(os.getenv("BYMA_FEE_PCT", "0.0008"))                  # 0,08% derechos de mercado
# Si tu condición frente al IVA hiciera que los derechos de mercado no lo
# tributen, poner en false. El default es true porque es el caso general.
BYMA_FEE_TAXED = os.getenv("BYMA_FEE_TAXED", "true").lower() == "true"
DEFAULT_RISK_PREMIUM_PCT = float(os.getenv("DEFAULT_RISK_PREMIUM_PCT", "1.5"))  # prima mínima por sobre el piso de inflación/FX


@dataclass
class MacroConfig:
    monthly_inflation_pct: float
    updated_at: str
    source: str = "manual (REM/INDEC)"


MACRO_CONFIG_MAX_AGE_DAYS = int(os.getenv("MACRO_CONFIG_MAX_AGE_DAYS", "45"))
MACRO_CONFIG_STALE_FALLBACK_PCT = float(os.getenv("MACRO_CONFIG_STALE_FALLBACK_PCT", "8.0"))


def load_macro_config() -> MacroConfig:
    """
    Lee la última estimación de inflación mensual cargada manualmente.

    CORRECCIÓN DE LA AUDITORÍA 7.1 (acepto el hallazgo): antes, si el
    archivo faltaba, se usaba un 3,0% fijo sin chequear vigencia — un
    valor optimista "porque sí". Ahora:
      - Si el dato tiene más de MACRO_CONFIG_MAX_AGE_DAYS (default 45),
        se lo trata como VENCIDO y se usa un piso conservador más alto
        (MACRO_CONFIG_STALE_FALLBACK_PCT, default 8%) en vez del dato
        viejo — para un sistema que se define a sí mismo como
        conservador, exigirse de más cuando el dato es dudoso es más
        seguro que asumir que todo sigue igual.
      - Si el archivo directamente no existe, mismo criterio: fallback
        conservador alto, no 3% optimista.
    """
    if os.path.exists(MACRO_CONFIG_PATH):
        try:
            with open(MACRO_CONFIG_PATH, "r") as f:
                data = json.load(f)
            updated_at_str = data.get("updated_at", "")
            is_stale = True
            try:
                updated_at = datetime.fromisoformat(updated_at_str)
                is_stale = (datetime.now() - updated_at).days > MACRO_CONFIG_MAX_AGE_DAYS
            except (ValueError, TypeError):
                pass  # sin fecha parseable -> se trata como vencido, por las dudas

            if is_stale:
                logger.warning(
                    "%s tiene más de %s días (o no tiene fecha) — se trata como VENCIDO. "
                    "Usando piso conservador de %.1f%% en vez del dato viejo. Actualizalo con "
                    "write_macro_config().", MACRO_CONFIG_PATH, MACRO_CONFIG_MAX_AGE_DAYS,
                    MACRO_CONFIG_STALE_FALLBACK_PCT,
                )
                return MacroConfig(monthly_inflation_pct=MACRO_CONFIG_STALE_FALLBACK_PCT,
                                    updated_at=updated_at_str or "desconocido", source="vencido")

            return MacroConfig(
                monthly_inflation_pct=float(data["monthly_inflation_pct"]),
                updated_at=updated_at_str,
                source=data.get("source", "manual"),
            )
        except Exception as e:
            logger.error("Error leyendo %s: %s", MACRO_CONFIG_PATH, e)
    logger.warning(
        "No se encontró %s. Usando piso conservador de %.1f%% (no un valor optimista) — "
        "crear este archivo con el último dato de REM/INDEC.",
        MACRO_CONFIG_PATH, MACRO_CONFIG_STALE_FALLBACK_PCT,
    )
    return MacroConfig(monthly_inflation_pct=MACRO_CONFIG_STALE_FALLBACK_PCT,
                        updated_at="default", source="fallback_conservador")


def write_macro_config(monthly_inflation_pct: float, source: str = "REM/BCRA"):
    """Helper para que un cron mensual (o vos a mano) actualice el dato."""
    with open(MACRO_CONFIG_PATH, "w") as f:
        json.dump({
            "monthly_inflation_pct": monthly_inflation_pct,
            "updated_at": datetime.now().isoformat(),
            "source": source,
        }, f, indent=2)


def get_ccl_devaluation_pct(ppi_client, days_back: int = 30) -> Optional[float]:
    """% de devaluación real implícita en el CCL en los últimos N días,
    comparando el CCL de hoy contra el de hace `days_back` días hábiles.
    Devuelve None si no hay suficientes datos (el caller debe manejar
    ese caso con prudencia, nunca asumir 0%)."""
    # CORRECCIÓN v10.5 — Bug #3 de la auditoría 2 (rev. 2), CRÍTICO,
    # confirmado en el código real: se asumía que hist_ars[0]/hist_usd[0]
    # eran la fecha más antigua y [-1] la más reciente, sin verificar el
    # orden real que devuelve la API (si viene descendente, el cálculo
    # queda invertido y puede mostrar devaluación negativa durante una
    # devaluación real) ni que ambas series tengan las mismas fechas
    # disponibles. Ahora se indexa por fecha explícitamente y solo se usan
    # las fechas presentes en AMBAS series, alineadas.
    hist_ars = ppi_client.get_historical_series("AL30", "BONOS", "INMEDIATA", days_back)
    hist_usd = ppi_client.get_historical_series("AL30D", "BONOS", "INMEDIATA", days_back)
    if not hist_ars or not hist_usd:
        return None
    try:
        ars_map = {item["date"]: item["price"] for item in hist_ars if "date" in item and "price" in item}
        usd_map = {item["date"]: item["price"] for item in hist_usd if "date" in item and "price" in item}
        common_dates = sorted(set(ars_map.keys()) & set(usd_map.keys()))
        if len(common_dates) < 2:
            logger.warning("Menos de 2 fechas en común entre AL30 y AL30D — no se puede medir devaluación real.")
            return None
        ccl_start = ars_map[common_dates[0]] / usd_map[common_dates[0]]
        ccl_end = ars_map[common_dates[-1]] / usd_map[common_dates[-1]]
        if ccl_start <= 0:
            return None
        return round((ccl_end / ccl_start - 1) * 100, 2)
    except Exception as e:
        logger.error("Error calculando devaluación CCL: %s", e)
        return None


def get_mep_rate(ppi_client) -> Optional[float]:
    """NUEVO EN v12.0 (Instrucción 3) — Dólar MEP implícito, calculado igual
    que el CCL pero contra el book LOCAL (sin la pata de dólar cable). Se
    agrega como segunda referencia cambiaria: la brecha CCL/MEP es en sí
    misma una señal de fricción regulatoria/cepo que el motor técnico no
    debe ignorar al evaluar rupturas de precio en pesos.

    LÍMITE HONESTO (igual que con get_ccl_rate en c_ppi_client.py): el par
    de tickers/settlement exacto que PPI expone para la pata MEP conviene
    confirmarlo contra Sandbox antes de operar en real; acá se usa AL30/
    AL30D con settlement A-24HS (mercado local) vs. INMEDIATA (CCL) como
    aproximación estándar de mercado."""
    al30_ars_local = ppi_client.get_market_data("AL30", "BONOS", "A-24HS")
    al30_usd_local = ppi_client.get_market_data("AL30D", "BONOS", "A-24HS")
    if not al30_ars_local or not al30_usd_local:
        return None
    try:
        p_ars = al30_ars_local.get("price", 0)
        p_usd = al30_usd_local.get("price", 0)
        if p_ars > 0 and p_usd > 0:
            return round(p_ars / p_usd, 2)
    except Exception as e:
        logger.error("Error calculando MEP: %s", e)
    return None


def deflact_price_series(price_series: list, fx_series: list) -> list:
    """NUEVO EN v12.0 (Instrucción 3) — deflacta una serie de precios en
    pesos por la serie del tipo de cambio implícito (CCL o MEP) del mismo
    período, para pasarle a e_technical_engine.py una serie en dólares
    constantes y evitar falsos breakouts causados solo por devaluación
    (hallazgo MEDIO de la auditoría v11: "Adaptación a la Microestructura
    del Mercado Argentino").

    Ambas listas deben venir alineadas por fecha y del mismo largo (usar
    el mismo criterio de intersección de fechas que get_ccl_devaluation_pct
    antes de llamar a esta función). Devuelve una lista de floats (precio
    en USD constantes); si alguna posición no se puede deflactar (fx <= 0
    o dato faltante), se conserva el precio nominal en pesos en esa
    posición en vez de insertar un None que rompa el motor técnico aguas
    abajo — es preferible una serie con algún punto sin deflactar a una
    serie con huecos."""
    if not price_series or not fx_series or len(price_series) != len(fx_series):
        logger.warning(
            "deflact_price_series: series de distinto largo o vacías (precios=%s, fx=%s) — "
            "no se puede deflactar con seguridad.",
            len(price_series) if price_series else 0, len(fx_series) if fx_series else 0,
        )
        return price_series or []
    deflated = []
    for price, fx in zip(price_series, fx_series):
        try:
            if fx and fx > 0:
                deflated.append(round(price / fx, 6))
            else:
                deflated.append(price)
        except (TypeError, ZeroDivisionError):
            deflated.append(price)
    return deflated


def get_caucion_benchmark_rate(ppi_client) -> Optional[float]:
    """NUEVO EN v12.0 (Instrucción 3 y 7) — tasa de caución bursátil a 1 día
    como benchmark dinámico de costo de oportunidad (tasa libre de riesgo en
    ARS). Antes (v11) el único piso de rentabilidad exigido era el hurdle de
    inflación/CCL (ver get_dynamic_hurdle_rate_monthly); ahora se agrega la
    caución como tercera referencia: si colocar el efectivo en caución rinde
    más que el retorno neto proyectado de una operación, esa operación no
    tiene sentido económico aunque supere el hurdle de inflación.

    Devuelve la tasa mensualizada (TNA/12) para que sea comparable directo
    contra hurdle_monthly_pct. None si no se pudo obtener (el caller debe
    tratarlo igual que la ausencia de dato de CCL: no asumir 0%)."""
    tna_pct = ppi_client.get_caucion_rate(days=1)
    if tna_pct is None:
        return None
    return round(tna_pct / 12, 4)


def get_dynamic_hurdle_rate_monthly(
    ppi_client,
    gemini_inflation_estimate_pct: Optional[float] = None,
    risk_premium_pct: float = DEFAULT_RISK_PREMIUM_PCT,
) -> dict:
    """
    Hurdle mensual = max(inflación estimada, devaluación real del CCL a 30
    días) + prima de riesgo. Combina tres fuentes con criterio conservador
    (siempre se queda con la más exigente, nunca con la más optimista):
      1. macro_config.json (dato manual de REM/INDEC)
      2. estimación de Gemini (segunda opinión, ver gemini_decision_engine)
      3. devaluación real observada del CCL en los últimos 30 días

    Sin techo: esto es un PISO. No limita cuánto puede rendir una operación.
    """
    macro_cfg = load_macro_config()
    inflation_candidates = [macro_cfg.monthly_inflation_pct]
    if gemini_inflation_estimate_pct is not None:
        # Clamp de sanidad: una IA puede alucinar un número absurdo. Se acepta
        # el input pero se lo acota a un rango plausible antes de usarlo.
        inflation_candidates.append(max(0.0, min(gemini_inflation_estimate_pct, 25.0)))
    inflation_pct = max(inflation_candidates)

    devaluacion_pct = get_ccl_devaluation_pct(ppi_client, days_back=30)
    if devaluacion_pct is None:
        logger.warning("No se pudo medir devaluación real del CCL; el hurdle se basa solo en inflación.")
        devaluacion_pct = 0.0

    # NUEVO EN v12.0 (Instrucción 3) — se agrega la tasa de caución
    # mensualizada como tercera candidata al piso. Mismo criterio
    # conservador que las otras dos fuentes: se toma el máximo, nunca el
    # mínimo, porque exigirle de más al sistema es más seguro que de menos.
    caucion_pct = get_caucion_benchmark_rate(ppi_client)
    if caucion_pct is None:
        logger.warning("No se pudo obtener tasa de caución; no se incluye en el piso del hurdle.")
        caucion_pct = 0.0

    piso_pct = max(inflation_pct, devaluacion_pct, caucion_pct)
    hurdle_pct = piso_pct + risk_premium_pct

    if piso_pct == inflation_pct:
        binding_factor = "inflación"
    elif piso_pct == devaluacion_pct:
        binding_factor = "devaluación CCL"
    else:
        binding_factor = "tasa de caución"

    return {
        "hurdle_monthly_pct": round(hurdle_pct, 2),
        "inflation_pct": round(inflation_pct, 2),
        "ccl_devaluation_30d_pct": devaluacion_pct,
        "caucion_benchmark_monthly_pct": caucion_pct,
        "risk_premium_pct": risk_premium_pct,
        "binding_factor": binding_factor,
    }


def load_fee_stress_factor() -> float:
    """NUEVO EN v13.0 — lee el último factor de estrés de comisiones
    persistido por dynamic_fee_adjustment()/h_daily_report.py. Si no existe
    el archivo (bot recién instalado, o nunca hubo reconciliación real
    todavía), devuelve 1.0 — el comportamiento es idéntico al de v12.0."""
    try:
        with open(FEE_STRESS_STATE_PATH, encoding="utf-8") as f:
            return float(json.load(f).get("stress_factor", 1.0))
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        return 1.0


def save_fee_stress_factor(stress_factor: float):
    try:
        os.makedirs(os.path.dirname(FEE_STRESS_STATE_PATH) or ".", exist_ok=True)
        with open(FEE_STRESS_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump({"stress_factor": stress_factor, "updated_at": datetime.now().isoformat()}, f)
    except OSError as e:
        logger.warning("No se pudo persistir el factor de estrés de comisiones: %s", e)


def dynamic_fee_adjustment(base_estimated_fee_pct: float, daily_reconciliation_diff_ars: Optional[float],
                            reference_notional_ars: float) -> float:
    """
    NUEVO EN v13.0 — Oportunidad de Auditoria_version_12.pdf, sección 4.1
    ("Amenaza: cambios de aranceles de BYMA/PPI"). PPI no expone un
    endpoint de "grilla de comisiones vigente" para consultar en vivo (se
    verificó contra la documentación oficial), así que en vez de inventar
    un oráculo que no existe, se reutiliza la reconciliación diaria que
    YA calcula h_daily_report._reconcile_real_costs_today() (v12.0): si el
    costo real que reporta PPI viene sistemáticamente por encima del
    estimado, se sube el % que usa calculate_trade_costs_pct() al día
    siguiente — el hurdle se vuelve más exigente automáticamente cuando la
    fricción real sube, en vez de depender de que alguien note la
    diferencia y actualice el .env a mano.
    Cap de seguridad: nunca ajusta el costo estimado más de 50% por
    encima del valor configurado — un solo día con datos atípicos de
    get_tax_report() no puede disparar el ajuste desproporcionadamente.
    """
    if not reference_notional_ars or reference_notional_ars <= 0 or daily_reconciliation_diff_ars is None:
        return base_estimated_fee_pct
    diff_pct_of_notional = daily_reconciliation_diff_ars / reference_notional_ars
    stress_factor = 1.0 + max(0.0, diff_pct_of_notional)
    stress_factor = min(stress_factor, 1.5)
    return round(base_estimated_fee_pct * stress_factor, 6)


def map_complex_instrument_params(instrument_type: str, base_params: dict) -> dict:
    """
    NUEVO EN v13.0 — Oportunidad de Auditoria_version_12.pdf, sección 4.3:
    mapeo de parámetros de orden para FCI y CAUCIONES, dos de los tipos
    que hoy están en modo "solo observación" (ver m_instrument_universe.py
    y l_order_confirmation.TRADEABLE_INSTRUMENT_TYPES). Estandariza cómo
    se armarían los parámetros de orden el día que se decida operarlos de
    verdad — hoy no se usa en ningún flujo de ejecución real, queda como
    utilidad lista para esa futura decisión (que sigue pendiente, ver
    sección S del Documento Maestro).
    """
    if instrument_type == "FCI":
        # Los FCI se suscriben por monto, no por cantidad nominal de cuotapartes.
        return {
            "monto": base_params.get("capital_asignado"),
            "ticker": base_params.get("ticker"),
            "tipo": "Suscripcion",
        }
    if instrument_type == "CAUCIONES":
        return {
            "plazo_dias": base_params.get("caucion_dias", 1),
            "tasa_minima": base_params.get("tasa_hurdle"),
            "cantidad": base_params.get("capital_asignado"),
        }
    return base_params


def calculate_trade_costs_fraction(spread_pct: float = 0.0,
                                   asset_class: str = "ACCIONES") -> float:
    """Costo total de la operación redonda (compra + venta) como FRACCIÓN del
    monto operado. 0.016456 significa que la fricción se lleva el 1,6456% del
    capital puesto en la operación.

    Esta es la versión que se usa para calcular PLATA: cost_ars = precio ×
    cantidad × esta fracción.

    Componentes, por tramo:
      · comisión del agente 0,6% + IVA 21%  = 0,726%
      · derechos de mercado 0,08% + IVA 21% = 0,0968%
      · total por tramo 0,8228% → operación redonda 1,6456%

    CORREGIDO EN v16.0 — a los derechos de mercado no se les aplicaba el IVA.
    Los derechos de BYMA tributan igual que la comisión, así que el costo real
    por tramo es 0,0968% y no 0,08%. La diferencia es 0,0336% por operación
    redonda: en una tenencia de cinco días es ruido, pero en scalping, donde el
    margen neto entero son dos o tres décimas de punto, un costo subestimado
    de forma SISTEMÁTICA —siempre para el mismo lado, en todas las
    operaciones— es lo que convierte una estrategia levemente positiva en el
    papel en una que sangra despacio en la cuenta.

    El factor de estrés (load_fee_stress_factor(), default 1.0) lo ajusta la
    reconciliación diaria contra los costos reales del back-office.
    """
    stress_factor = load_fee_stress_factor()

    # NUEVO EN v16.2 — el costo depende de la CLASE DE ACTIVO.
    # Hasta esta versión había una sola comisión y un solo derecho de mercado
    # para acciones, CEDEARs, bonos, ETFs, opciones y futuros. En el mercado
    # local los aranceles difieren de forma sustancial, y el error no es
    # simétrico: sobrestimar el costo de un bono descarta operaciones buenas
    # (no se ve nunca), pero subestimar el de una opción aprueba operaciones
    # cuyo neto real es negativo. El tarifario vive en au_fee_schedule.py,
    # con IVA diferenciado por componente y por clase.
    try:
        import au_fee_schedule
        return au_fee_schedule.costo_redondo(asset_class, spread_pct, stress_factor)
    except ImportError:
        # Degradación explícita: si el tarifario no está, se usa la grilla de
        # renta variable, que es la más cara. Errar por caro descarta de más;
        # errar por barato aprueba lo que pierde plata.
        logger.warning("au_fee_schedule no disponible: se usa la grilla de renta variable.")
        comision_por_tramo = (PPI_COMMISSION_PCT * stress_factor) * (1 + IVA_PCT)
        byma_por_tramo = BYMA_FEE_PCT * (1 + IVA_PCT) if BYMA_FEE_TAXED else BYMA_FEE_PCT
        return round((comision_por_tramo + byma_por_tramo) * 2 + (spread_pct / 100.0), 6)


def calculate_trade_costs_pct(spread_pct: float = 0.0,
                              asset_class: str = "ACCIONES") -> float:
    """El mismo costo, expresado en PUNTOS PORCENTUALES: 1.6456 en vez de
    0.016456. Esta es la versión que se compara contra retornos esperados.

    ==========================================================================
    BUG SERIO CORREGIDO EN v16.0 — LA MISMA FUNCIÓN SE USABA CON DOS UNIDADES
    ==========================================================================
    Hasta esta versión existía una sola función, calculate_trade_costs_pct(),
    que devolvía una FRACCIÓN (0,0165) pese al nombre. Y se la consumía de dos
    maneras incompatibles:

      · Para calcular plata —k_position_manager._close_position() y la
        reconciliación de h_daily_report.py— hacían precio × cantidad × costo.
        Ahí la fracción es la unidad correcta, y esas cuentas estaban BIEN.

      · Para el filtro previo a operar, j_main.py hacía:
            net_return = calculate_net_return_pct(expected_gross, spread_pct)
        donde expected_gross viene en puntos porcentuales (atr_pct × múltiplo,
        con atr_pct calculado como (ATR/precio)×100) y spread_pct también
        (calculado como (oferta−demanda)/demanda×100). Ahí la fracción está
        cien veces por debajo de la escala del resto de la fórmula.

    El efecto práctico no era un error visible sino algo peor: el filtro de
    rentabilidad restaba 0,0165 puntos de fricción en vez de 1,6456. O sea que
    el control que existe justamente para descartar operaciones que no le ganan
    a los costos estaba descontando cien veces menos costo del real, y
    aprobaba operaciones cuyo neto verdadero era negativo. Una operación con
    2% bruto esperado se veía como 1,98% neto cuando en realidad era 0,35%.
    Nada fallaba, nada aparecía en el log: simplemente pasaban operaciones que
    no debían pasar.

    La solución no es tocar un número sino separar las dos unidades en dos
    funciones con nombre explícito, para que la próxima vez que alguien las
    use quede claro cuál corresponde. Los tests de tests/test_economics_costs.py
    fijan las dos escalas para que esto no pueda volver a pasar sin que algo
    se ponga en rojo.
    """
    return round(calculate_trade_costs_fraction(spread_pct, asset_class) * 100, 4)


def calculate_net_return_pct(
    expected_gross_return_pct: float,
    spread_pct: float = 0.0,
    asset_class: str = "ACCIONES",
) -> float:
    """Retorno neto esperado = retorno bruto proyectado - fricción total.
    No hay ningún clamp/cap acá: si expected_gross_return_pct es alto, el
    resultado neto también lo es. Solo se descuenta el costo real.

    v16.2 — la fricción que se descuenta es ahora la de la CLASE del
    instrumento que se está evaluando. Antes se descontaba siempre la de
    renta variable, así que el filtro de rentabilidad de un bono y el de una
    opción usaban un número que no era el suyo."""
    costs_pct = calculate_trade_costs_pct(spread_pct, asset_class)
    return round(expected_gross_return_pct - costs_pct, 4)


def passes_hurdle(net_return_pct: float, days_held_estimate: int, hurdle_monthly_pct: float) -> dict:
    """
    Prorratea el hurdle mensual por los días que se espera mantener la
    posición y compara contra el retorno neto proyectado. Es un filtro de
    PISO únicamente: si net_return_pct es mucho mayor al hurdle, pasa
    igual — nunca se rechaza ni se recorta por ser "demasiado" rentable.
    """
    days_held_estimate = max(days_held_estimate, 1)
    hurdle_prorated_pct = hurdle_monthly_pct * (days_held_estimate / 30)
    approved = net_return_pct > hurdle_prorated_pct
    return {
        "approved": approved,
        "net_return_pct": net_return_pct,
        "hurdle_prorated_pct": round(hurdle_prorated_pct, 4),
        "margin_pct": round(net_return_pct - hurdle_prorated_pct, 4),
    }


def calculate_position_size(available_capital_ars: float, entry_price: float,
                             stop_loss_price: float, risk_pct: float = 1.0,
                             spread_pct: float = 0.0,
                             asset_class: str = "ACCIONES",
                             max_affordable_ars: float = 0.0) -> int:
    """
    NUEVO EN v6.0 — antes el bot solo sugería un precio y nunca decía
    cuánto comprar. Tamaño de posición basado en RIESGO, no en "meter todo
    el capital": arriesga como máximo risk_pct% del capital disponible en
    CADA operación (default 1%), calculado contra la distancia real al
    stop-loss — así una operación con stop-loss lejano (activo volátil)
    compra menos cantidad que una con stop-loss cercano, para arriesgar
    siempre el mismo % de la cuenta.

    RECHAZO EXPLÍCITO — auditoría 8.1 (rev. 1) pidió subir
    RISK_PCT_PER_TRADE de 1% a 10%. Evalué el cambio y NO lo apliqué,
    por esto (no es una preferencia, es una incoherencia matemática con
    el resto del sistema):

      - MAX_DRAWDOWN_PCT (kill switch) está en 5% por default. Con
        risk_pct=10%, UNA sola operación que toque el stop-loss ya
        arriesga más del doble de lo que el propio kill switch tolera
        para TODA la cuenta — el sistema se apagaría solo con la
        primera pérdida, si es que no se ejecuta antes.
      - MAX_DAILY_LOSS_PCT está en 1% por default. Una sola operación al
        10% de riesgo dispara el corte diario diez veces más rápido de
        lo que ese límite fue pensado para tolerar.
      - Con MAX_OPEN_POSITIONS=3 y 10% de riesgo cada una, el peor caso
        (las tres tocan stop-loss el mismo día) es una pérdida del 30%
        de la cuenta en un día — completamente incompatible con
        "conservador", que es el objetivo de negocio declarado desde la
        primera versión de este proyecto.
      - La propia auditoría anterior (7.1) había recomendado 0,25%-0,50%
        de riesgo por operación para un bot conservador — 10% contradice
        esa recomendación previa del mismo linaje de auditorías, sin dar
        una razón que explique el giro.

    Se mantiene el 1% (configurable por RISK_PCT_PER_TRADE en el .env,
    igual que antes). Si en algún momento se quiere operar más agresivo,
    lo coherente es subir gradualmente (2-3%) y ajustar en conjunto
    MAX_DRAWDOWN_PCT/MAX_DAILY_LOSS_PCT/MAX_OPEN_POSITIONS para que
    seguén siendo compatibles entre sí — nunca cambiar uno solo de los
    cuatro números de forma aislada.
    """
    if available_capital_ars <= 0 or entry_price <= 0:
        return 0

    # ======================================================================
    # CORREGIDO EN v16.2 — LA FRICCIÓN ENTRA EN LA PÉRDIDA AL STOP
    # ======================================================================
    # Hasta esta versión el tamaño salía de riesgo ÷ (entrada − stop). Esa
    # cuenta ignora que tocar el stop no cuesta solo la distancia de precio:
    # cuesta también la fricción de la operación redonda, que el propio
    # sistema calcula en otra parte y no incorporaba acá.
    #
    # Con un stop a 1 ATR y un ATR típico del 3%, la pérdida real al stop es
    # 3% + 1,65% = 4,65%: un 55% más de lo dimensionado. El "1% por
    # operación", que es la regla de la que se derivan TODAS las demás de
    # este sistema, era en realidad ~1,55%. Con tres posiciones simultáneas
    # el peor día era 4,6% y no 3%, contra un drawdown máximo declarado del
    # 5% — es decir que el peor caso previsto rozaba el corte sin que nadie
    # lo hubiera decidido.
    friccion_por_unidad = entry_price * calculate_trade_costs_fraction(
        spread_pct, asset_class)
    per_share_risk = (entry_price - stop_loss_price) + friccion_por_unidad
    if per_share_risk <= 0:
        return 0
    risk_amount_ars = available_capital_ars * (risk_pct / 100)
    qty = int(risk_amount_ars / per_share_risk)
    # Nunca sugerir comprar más de lo que el capital disponible alcanza.
    # OJO: acá el tope sigue siendo el EFECTIVO, aunque la base del riesgo
    # sea el patrimonio. Son dos cosas distintas y tiene que ser así: se
    # arriesga un porcentaje de lo que la cuenta vale, pero no se puede
    # comprar con plata que está inmovilizada en otra posición.
    max_affordable = int(max_affordable_ars / entry_price) if max_affordable_ars \
        else int(available_capital_ars / entry_price)
    return max(min(qty, max_affordable), 0)


MAX_PCT_OF_BOOK_DEPTH = float(os.getenv("MAX_PCT_OF_BOOK_DEPTH", "10.0"))


def cap_by_book_liquidity(quantity: int, book: dict) -> int:
    """
    NUEVO EN v10.0 — ACEPTADO de la auditoría 9.1 (rev. 1): el tamaño de
    posición se calculaba solo contra el riesgo (distancia al
    stop-loss), sin mirar si el mercado realmente tiene esa cantidad
    disponible para vender en la punta de oferta. Una orden más grande
    que la profundidad real del libro no se llena al precio esperado
    (o no se llena directo) — termina pagando un precio peor del
    analizado. Ahora se limita la cantidad sugerida a un máximo de
    MAX_PCT_OF_BOOK_DEPTH% (default 10%) de lo que hay ofrecido en la
    primera punta del libro.

    Si no hay datos de book (ej. la consulta falló), no se cambia nada —
    es mejor no aplicar el límite que aplicar uno inventado.
    """
    if not book or not book.get("offers"):
        return quantity
    try:
        top_offer_qty = book["offers"][0].get("quantity", 0) or book["offers"][0].get("amount", 0)
    except (IndexError, KeyError, TypeError):
        return quantity
    if not top_offer_qty:
        return quantity
    max_by_depth = int(top_offer_qty * (MAX_PCT_OF_BOOK_DEPTH / 100))
    return max(min(quantity, max_by_depth), 0)


def validate_risk_coherence(risk_pct_per_trade: float, max_open_positions: int,
                             max_drawdown_pct: float, max_daily_loss_pct: float) -> list:
    """
    NUEVO EN v10.0 — la auditoría 9.1 (rev. 2) propuso subir el riesgo por
    operación a 2.5% para cuentas chicas, esta vez ajustando también el
    kill switch a 8% de drawdown en conjunto (a diferencia del pedido
    anterior de subir a 10% sin tocar nada más, que se había rechazado
    por incoherente). La idea de escalar el riesgo para cuentas chicas es
    razonable — con muy poco capital, 1% de riesgo puede no alcanzar para
    comprar ni una unidad de algunos instrumentos. Pero en vez de hacerlo
    de forma automática y silenciosa (que el bot detecte solo el tamaño
    de tu cuenta y cambie su propio comportamiento de riesgo), se prefirió
    esto: SI vos decidís subir el riesgo por operación, el bot valida al
    arrancar que los números sigan siendo matemáticamente coherentes
    entre sí, y te avisa fuerte si no lo son — en vez de operar así no
    más y descubrirlo en la primera pérdida grande.

    Devuelve una lista de strings con las advertencias encontradas (vacía
    si todo es coherente).
    """
    warnings = []
    worst_case_single_trade_pct = risk_pct_per_trade
    worst_case_all_positions_pct = risk_pct_per_trade * max_open_positions

    if worst_case_single_trade_pct > max_daily_loss_pct:
        warnings.append(
            f"RISK_PCT_PER_TRADE ({risk_pct_per_trade}%) es MAYOR que MAX_DAILY_LOSS_PCT "
            f"({max_daily_loss_pct}%) — UNA sola operación que toque el stop-loss ya "
            f"dispara el corte diario. Subí MAX_DAILY_LOSS_PCT o bajá el riesgo por operación."
        )
    if worst_case_all_positions_pct > max_drawdown_pct:
        warnings.append(
            f"RISK_PCT_PER_TRADE x MAX_OPEN_POSITIONS ({worst_case_all_positions_pct}%) "
            f"supera a MAX_DRAWDOWN_PCT ({max_drawdown_pct}%) — si todas las posiciones "
            f"abiertas tocan stop-loss el mismo día, el kill switch no alcanza a frenarlo "
            f"a tiempo. Bajá MAX_OPEN_POSITIONS, bajá el riesgo por operación, o subí "
            f"MAX_DRAWDOWN_PCT para que sea matemáticamente coherente."
        )
    return warnings
