"""
w_agent_graph.py — Arquitectura Multi-Agente con LangGraph (NUEVO EN v12.0,
Instrucción 4).

DECISIÓN DE DISEÑO (documentada acá y en BITACORA_v12_entradas.md, por ser
la más importante de esta entrega): este módulo NO reimplementa la lógica
de decisión, riesgo o validación de IA desde cero. La envuelve.

j_main.py (v11) ya tiene, en los hechos, un flujo Trader -> Guardian: primero
technical_engine + f_gemini_decision_engine.evaluate_macro_and_geopolitics()
proponen una señal, después p_risk_guardian.check_and_halt_if_needed() y
t_model_guardian.check_model_status() la validan antes de que
l_order_confirmation.py la ejecute. Esa lógica viene de 11 versiones de
auditorías reales (ver BITACORA_v11.0.pdf) con bugs concretos ya corregidos
(orden de series invertido, quantity_type por tipo de instrumento, etc.).
Reescribirla de cero adentro de los nodos de LangGraph solo para "usar el
framework pedido" arriesgaría reintroducir esos mismos bugs sin necesidad.

Por eso cada nodo de este grafo es un ADAPTADOR fino sobre las funciones que
ya existen y ya están probadas. LangGraph aporta acá la orquestación
explícita (grafo de estados, trazabilidad de qué nodo aprobó/rechazó qué) y
el punto de enganche para el Agente SRE (Fase C) — no reemplaza el trabajo
cuantitativo/de riesgo ya hecho.

ESTADO: este módulo queda listo y probado de forma aislada (ver
test_agent_graph_smoke() al final), pero TODAVÍA NO está conectado al loop
principal de j_main.py — cambiar la orquestación en vivo del bot es una
decisión de mayor riesgo que prefiero confirmar con el usuario antes de
aplicarla (ver BITACORA_v12_entradas.md, entrada 9), en vez de reemplazar
silenciosamente un loop que ya está operando.
"""

import logging
import os
from typing import TypedDict, Optional, Any

from langgraph.graph import StateGraph, END

logger = logging.getLogger("agent_graph")


class AgentState(TypedDict, total=False):
    ticker: str
    instrument_type: str
    settlement: str
    market_data: dict
    ccl_rate: Optional[float]
    news_result: dict
    macro_result: dict            # salida de GeminiDecisionEngine (Agente Trader/Alpha)
    technical_result: dict        # salida de e_technical_engine
    proposed_trade: Optional[dict]
    risk_approval: bool
    risk_reason: str
    model_guardian_ok: bool
    sre_notes: list
    final_decision: str           # "EXECUTE" | "REJECT" | "HALT"


# ---------------------------------------------------------------------- #
# Nodo 1 — Agente Trader (Alpha). Instrucción 4.1.
# ---------------------------------------------------------------------- #
def build_trader_node(gemini_engine, technical_engine_module):
    """Factory: recibe las instancias/módulos YA construidos por j_main.py
    (mismo patrón de inyección de dependencias que el resto del proyecto,
    ver ResilientPPIClient(notifier) en c_ppi_client.py) en vez de crear
    sus propias instancias acá adentro — así el grafo usa exactamente la
    misma sesión de Gemini y el mismo motor técnico que el resto del bot,
    sin duplicar configuración."""

    def trader_node(state: AgentState) -> dict:
        technical_result = state.get("technical_result") or {}
        # NUEVO EN v12.0 (modo shadow, pendiente #1 resuelto): si el caller
        # ya calculó el macro_result (ej. j_main.py corriendo el grafo en
        # paralelo a su propia lógica secuencial, solo para comparar
        # veredictos antes de confiar en el grafo para ejecutar de
        # verdad), se reutiliza en vez de llamar a Gemini una segunda vez
        # por el mismo ticker — evita duplicar costo/latencia de LLM solo
        # por estar en modo observación.
        macro_result = state.get("macro_result")
        if macro_result is None:
            macro_result = gemini_engine.evaluate_macro_and_geopolitics(
                ticker=state["ticker"],
                news_result=state.get("news_result", {}),
                ccl_rate=state.get("ccl_rate") or 0.0,
            )
        proposed = None
        if technical_result.get("signal") and not macro_result.get("veto_risk"):
            proposed = {
                "ticker": state["ticker"],
                "instrument_type": state.get("instrument_type", "CEDEARS"),
                "technical_score": technical_result.get("score"),
                "macro_score": macro_result.get("macro_score"),
            }
        return {"macro_result": macro_result, "proposed_trade": proposed}

    return trader_node


# ---------------------------------------------------------------------- #
# Nodo 2 — Agente de Riesgo (Guardian). Instrucción 4.2. Temperatura 0:
# no hay LLM discrecional acá adentro a propósito — son los mismos checks
# determinísticos de p_risk_guardian.py / t_model_guardian.py (VaR,
# exposición máxima, halt persistido) que ya existían, sin agregar
# aleatoriedad de un modelo a la decisión de riesgo.
# ---------------------------------------------------------------------- #
def build_risk_guardian_node(ppi_client, notifier, risk_guardian_module, model_guardian_module):
    def risk_guardian_node(state: AgentState) -> dict:
        if not state.get("proposed_trade"):
            return {"risk_approval": False, "risk_reason": "Trader no propuso operación."}

        if risk_guardian_module.is_halted():
            return {"risk_approval": False, "risk_reason": f"Kill switch activo: {risk_guardian_module.halt_reason()}"}

        halted_now = risk_guardian_module.check_and_halt_if_needed(ppi_client, notifier)
        if halted_now:
            return {"risk_approval": False, "risk_reason": "Kill switch se activó durante esta evaluación."}

        exposure = risk_guardian_module.get_exposure_by_ticker(state["ticker"])
        model_ok = model_guardian_module.check_model_status(notifier)

        approved = model_ok and exposure < 100.0  # el % exacto ya lo valida k_position_manager aguas abajo
        reason = "Aprobado por Guardian." if approved else "Rechazado: modelo IA degradado o exposición fuera de rango."
        return {"risk_approval": approved, "risk_reason": reason, "model_guardian_ok": model_ok}

    return risk_guardian_node


# ---------------------------------------------------------------------- #
# Nodo 3 — Agente SRE (Introspección). Instrucción 4.3. En esta entrega
# (Fase B) es un stub de solo-observación: registra el resultado del grafo.
# La captura de excepciones/RAG con ChromaDB (m_introspection_engine.py)
# es la Fase C, todavía no incluida en este paquete.
# ---------------------------------------------------------------------- #
def sre_node(state: AgentState) -> dict:
    notes = state.get("sre_notes", [])
    notes.append(f"{state['ticker']}: risk_approval={state.get('risk_approval')} "
                 f"reason={state.get('risk_reason')}")
    return {"sre_notes": notes}


def check_approval(state: AgentState) -> str:
    return "approved" if state.get("risk_approval") else "rejected"


def build_trading_graph(ppi_client, notifier, gemini_engine, technical_engine_module,
                         risk_guardian_module, model_guardian_module):
    """Compila el grafo Trader -> Guardian -> (SRE + execute_trade | END).
    'execute_trade' es un nodo terminal simbólico acá: NO llama a
    l_order_confirmation.py directamente (esa conexión queda para cuando
    se decida reemplazar el loop de j_main.py — ver docstring del módulo).
    Por ahora solo marca final_decision, para que el caller decida qué
    hacer con el resultado."""
    workflow = StateGraph(AgentState)
    workflow.add_node("trader", build_trader_node(gemini_engine, technical_engine_module))
    workflow.add_node("risk_guardian", build_risk_guardian_node(
        ppi_client, notifier, risk_guardian_module, model_guardian_module))
    workflow.add_node("sre", sre_node)

    workflow.set_entry_point("trader")
    workflow.add_edge("trader", "risk_guardian")
    workflow.add_conditional_edges(
        "risk_guardian", check_approval,
        {"approved": "sre", "rejected": "sre"},
    )
    workflow.add_edge("sre", END)

    return workflow.compile()


def run_trading_graph(app, initial_state: AgentState) -> AgentState:
    result = app.invoke(initial_state)
    result["final_decision"] = "EXECUTE" if result.get("risk_approval") else "REJECT"
    logger.info("Grafo evaluado para %s -> %s (%s)", initial_state.get("ticker"),
                result["final_decision"], result.get("risk_reason"))
    return result


# ---------------------------------------------------------------------- #
# NUEVO EN v12.0 — Modo SHADOW (decisión de experto, pendiente #1
# resuelto). Se cachea UN grafo compilado por proceso (compilar el
# StateGraph tiene costo no trivial; no tiene sentido recompilarlo por
# cada ticker evaluado en cada vuelta del loop).
# ---------------------------------------------------------------------- #
_shadow_graph_app = None

# NUEVO EN v13.0 — Oportunidad de Auditoria_version_12.pdf, sección 4.3
# ("Habilitación del nodo de Gating Real"). LANGGRAPH_MODE sigue en
# "shadow" por default: la evidencia acumulada en Sandbox hasta ahora
# (ver dashboard, sección de monitoreo SRE/AIOps del v13) todavía no es
# suficiente para confiarle la ejecución real al grafo — ver Documento
# Maestro v13, sección de roadmap. La función queda lista y probada para
# el día que, con criterio y evidencia, se decida el cutover.
LANGGRAPH_MODE = os.getenv("LANGGRAPH_MODE", "shadow").strip().lower()


def real_gating_evaluate(ppi_client, notifier, gemini_engine, risk_guardian_module, model_guardian_module,
                          ticker: str, instrument_type: str, technical_score: float,
                          macro_result: dict, ccl_rate: float) -> AgentState:
    """
    NUEVO EN v13.0 — versión de shadow_evaluate() que SÍ puede bloquear la
    ejecución real, para cuando LANGGRAPH_MODE=gating (no es el default).
    j_main.py solo debe llamar a esta función si LANGGRAPH_MODE == "gating"
    Y después de que la lógica secuencial real ya aprobó la oportunidad —
    el grafo acá actúa como una validación ADICIONAL (puede rechazar lo
    que la lógica real aprobó, nunca aprobar algo que la lógica real
    rechazó), nunca como el único filtro.

    Si el grafo mismo falla por cualquier motivo, se aprueba por defecto
    (fail-open hacia la lógica ya validada) en vez de bloquear una
    operación real por un error de una pieza todavía en evaluación — el
    mismo criterio de prudencia que shadow_evaluate() ya aplicaba.
    """
    global _shadow_graph_app
    try:
        if _shadow_graph_app is None:
            _shadow_graph_app = build_trading_graph(
                ppi_client, notifier, gemini_engine, None, risk_guardian_module, model_guardian_module,
            )
        state: AgentState = {
            "ticker": ticker, "instrument_type": instrument_type,
            "technical_result": {"signal": True, "score": technical_score},
            "macro_result": macro_result,
            "ccl_rate": ccl_rate,
        }
        result = run_trading_graph(_shadow_graph_app, state)
        logger.info("GATING REAL: veredicto del grafo para %s = %s", ticker, result.get("final_decision"))
        return result
    except Exception as e:
        logger.error("Gating real del grafo falló para %s — se aprueba por defecto (fail-open): %s", ticker, e)
        return {"final_decision": "EXECUTE", "risk_reason": f"fail-open tras error: {e}"}


def shadow_evaluate(ppi_client, notifier, gemini_engine, risk_guardian_module, model_guardian_module,
                     ticker: str, instrument_type: str, technical_score: float,
                     macro_result: dict, ccl_rate: float) -> AgentState:
    """Corre el grafo Trader->Guardian->SRE en modo SOLO OBSERVACIÓN, sin
    tocar la ejecución real. Pensado para que j_main.py lo llame junto a
    su propia lógica secuencial (que sigue siendo la que manda), reutiliza
    el macro_result ya calculado (no llama a Gemini de nuevo) y devuelve
    el veredicto del grafo para loguear si coincide o no con la decisión
    real — así se acumula evidencia de que el grafo replica el
    comportamiento correcto ANTES de considerar conectarlo para ejecutar
    de verdad (ver Documento_Maestro_v12.pdf, sección 6).

    Nunca debe lanzar una excepción hacia el caller: un fallo en el modo
    shadow (observación) no puede degradar el loop de trading real."""
    global _shadow_graph_app
    try:
        if _shadow_graph_app is None:
            _shadow_graph_app = build_trading_graph(
                ppi_client, notifier, gemini_engine, None, risk_guardian_module, model_guardian_module,
            )
        state: AgentState = {
            "ticker": ticker, "instrument_type": instrument_type,
            "technical_result": {"signal": True, "score": technical_score},
            "macro_result": macro_result,
            "ccl_rate": ccl_rate,
        }
        return run_trading_graph(_shadow_graph_app, state)
    except Exception as e:
        logger.error("Modo shadow del grafo falló para %s (no afecta la operación real): %s", ticker, e)
        return {"final_decision": "SHADOW_ERROR", "risk_reason": str(e)}


# ---------------------------------------------------------------------- #
# Smoke test aislado — corre con mocks, sin tocar PPI/Gemini reales.
# Sirve para validar que el grafo compila y fluye antes de conectarlo.
# ---------------------------------------------------------------------- #
def _smoke_test():
    class _FakeGemini:
        def evaluate_macro_and_geopolitics(self, ticker, news_result, ccl_rate):
            return {"macro_score": 0.8, "veto_risk": False}

    class _FakeRiskGuardian:
        @staticmethod
        def is_halted():
            return False

        @staticmethod
        def halt_reason():
            return ""

        @staticmethod
        def check_and_halt_if_needed(ppi_client, notifier):
            return False

        @staticmethod
        def get_exposure_by_ticker(ticker):
            return 10.0

    class _FakeModelGuardian:
        @staticmethod
        def check_model_status(notifier):
            return True

    app = build_trading_graph(
        ppi_client=None, notifier=None, gemini_engine=_FakeGemini(),
        technical_engine_module=None, risk_guardian_module=_FakeRiskGuardian(),
        model_guardian_module=_FakeModelGuardian(),
    )
    state: AgentState = {
        "ticker": "SPY", "instrument_type": "CEDEARS",
        "technical_result": {"signal": True, "score": 0.9},
        "news_result": {"headlines": [], "news_unavailable": False},
        "ccl_rate": 1250.0,
    }
    result = run_trading_graph(app, state)
    assert result["final_decision"] == "EXECUTE", result
    print("w_agent_graph smoke test OK ->", result["final_decision"])


if __name__ == "__main__":
    _smoke_test()
