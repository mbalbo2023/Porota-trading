"""
m_introspection_engine.py — Motor de Introspección SRE, RAG y
Auto-Sanación (NUEVO EN v12.0, Instrucción 5).

Basado en la Propuesta de Arquitectura (Introspección_y_mejoras_de_ia_
propuesta.pdf) que compartiste: intercepta excepciones no manejadas del
proceso principal, arma un volcado de contexto (traceback + variables
locales relevantes), lo enriquece con búsqueda semántica sobre el propio
código fuente (ChromaDB) y le pide un diagnóstico estructurado al modelo
de IA. Si la gravedad es CRÍTICA, corta operaciones con
p_risk_guardian.trigger_halt() — ver la corrección de nombres documentada
ahí y en BITACORA_v12_entradas.md (entrada 10): NO se invoca
r_clear_kill_switch.py, que hace lo opuesto.

LÍMITE HONESTO: la indexación de ChromaDB (ingest_codebase) es best-effort
y se degrada con gracia si el paquete `chromadb` no está instalado o si el
directorio sre_vector_db no existe todavía — un motor de introspección que
él mismo tira una excepción no controlada al arrancar sería contradictorio
con su propio propósito.
"""

import glob
import json
import logging
import os
import bb_runtime_status
bb_runtime_status.configure_process_timezone()
import sys
import traceback
from datetime import datetime
from typing import Optional

logger = logging.getLogger("introspection_engine")

PROPOSALS_DIR = os.getenv("PROPOSALS_DIR", "data/proposals")
SRE_VECTOR_DB_PATH = os.getenv("SRE_VECTOR_DB_PATH", "./sre_vector_db")
SRE_MODEL_TEMPERATURE = float(os.getenv("SRE_MODEL_TEMPERATURE", "0.1"))
CODEBASE_GLOB = os.getenv("SRE_CODEBASE_GLOB", "*.py")

_chroma_collection = None  # se inicializa de forma perezosa, ver _get_collection()


def _get_collection():
    """Devuelve la colección de ChromaDB, creándola/indexando el código
    fuente la primera vez que se necesita. Devuelve None (sin excepción)
    si chromadb no está disponible — el motor sigue funcionando sin RAG,
    solo con el traceback crudo, en vez de romperse."""
    global _chroma_collection
    if _chroma_collection is not None:
        return _chroma_collection
    try:
        import chromadb
    except ImportError:
        logger.warning(
            "chromadb no está instalado — el motor de introspección funciona sin "
            "búsqueda semántica de contexto (solo traceback crudo). "
            "pip install chromadb para habilitar el RAG completo."
        )
        return None
    try:
        client = chromadb.PersistentClient(path=SRE_VECTOR_DB_PATH)
        collection = client.get_or_create_collection(name="system_context")
        _ingest_codebase_if_empty(collection)
        _chroma_collection = collection
        return collection
    except Exception as e:
        logger.error("No se pudo inicializar ChromaDB (%s) — se sigue sin RAG.", e)
        return None


def _ingest_codebase_if_empty(collection):
    """Indexa cada archivo .py del proyecto como un documento (chunk por
    archivo completo — para un proyecto de ~20 módulos de este tamaño no
    hace falta un chunking más fino). Solo indexa si la colección está
    vacía, para no re-vectorizar en cada arranque."""
    try:
        if collection.count() > 0:
            return
    except Exception:
        pass
    files = sorted(glob.glob(CODEBASE_GLOB))
    if not files:
        return
    docs, ids, metadatas = [], [], []
    for path in files:
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            logger.warning("No se pudo leer %s para indexar: %s", path, e)
            continue
        docs.append(content[:8000])  # límite razonable por documento
        ids.append(path)
        metadatas.append({"path": path})
    if docs:
        collection.add(documents=docs, ids=ids, metadatas=metadatas)
        logger.info("Motor SRE: indexados %s archivos en ChromaDB (%s).", len(docs), SRE_VECTOR_DB_PATH)


def _semantic_context(tb_str: str, n_results: int = 3) -> list:
    collection = _get_collection()
    if collection is None:
        return []
    try:
        results = collection.query(query_texts=[tb_str], n_results=n_results)
        return results.get("documents", [[]])[0]
    except Exception as e:
        logger.error("Falla en búsqueda semántica del motor SRE: %s", e)
        return []


def _dump_locals(tb) -> dict:
    """Volcado de contexto (variables locales del frame más profundo donde
    ocurrió la excepción). Se filtra a valores serializables y de largo
    acotado — un objeto con __repr__ pesado (ej. una respuesta HTTP
    completa) no debe inflar el reporte ni, peor, filtrar un token si el
    valor no pasó por obfuscate_secret()."""
    frame_locals = {}
    try:
        from c_ppi_client import obfuscate_secret, sanitize_env_dump
    except Exception:
        def obfuscate_secret(x):
            return x

        def sanitize_env_dump(d):
            return d
    current = tb
    while current.tb_next is not None:
        current = current.tb_next
    for key, val in current.tb_frame.f_locals.items():
        try:
            repr_val = obfuscate_secret(repr(val))
            frame_locals[key] = repr_val[:300]
        except Exception:
            frame_locals[key] = "<no representable>"
    # ENDURECIDO EN v15.0 — hallazgo ALTO de la auditoría de v14 ("el volcado
    # de memoria expone claves en texto plano"), aceptado con una precisión:
    # el volcado ya pasaba por obfuscate_secret(), pero esa función solo
    # reconocía JWTs (ver el bug corregido en c_ppi_client.py), así que una
    # variable local llamada api_secret con un valor que no tuviera forma de
    # JWT salía entera. Ahora se aplica una segunda pasada POR NOMBRE de
    # variable: si la variable se llama api_key, secret, token o password, se
    # enmascara sin siquiera mirar su contenido. Defensa en profundidad: la
    # capa por forma puede fallar ante un formato nuevo; la capa por nombre no.
    return sanitize_env_dump(frame_locals)


def _invoke_sre_model(prompt: str) -> dict:
    """Envía el prompt al modelo de IA (mismo cliente Gemini que ya usa el
    resto del bot — no se crea una segunda configuración de API key
    aparte) con temperatura baja para determinismo técnico (Instrucción 5,
    punto de "Sandboxing de la IA Supervisora"). Fuerza salida JSON con el
    esquema pedido. Si algo falla (API caída, JSON inválido), devuelve un
    diagnóstico degradado de gravedad DESCONOCIDA en vez de propagar la
    excepción — el propio motor de manejo de errores no puede ser un
    punto de falla que tire otra excepción no controlada."""
    try:
        from google import genai
        from google.genai import types
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY no configurada")
        client = genai.Client(api_key=api_key)
        model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
        # CORRECCIÓN DE AUTOAUDITORÍA v12.0: el resto del proyecto
        # (f_gemini_decision_engine.py) pasa la config vía
        # types.GenerateContentConfig(...), no un dict plano — se alinea
        # acá al mismo patrón ya probado, en vez de asumir que el SDK
        # acepta ambas formas indistintamente.
        response = client.models.generate_content(
            model=model, contents=prompt,
            config=types.GenerateContentConfig(
                temperature=SRE_MODEL_TEMPERATURE, response_mime_type="application/json",
            ),
        )
        cleaned = response.text.strip().replace("```json", "").replace("```", "")
        return json.loads(cleaned)
    except Exception as e:
        logger.error("El propio motor SRE falló al invocar al modelo de diagnóstico: %s", e)
        return {
            "gravedad": "DESCONOCIDA",
            "causa_raiz": "No se pudo obtener diagnóstico de IA (ver logs del propio motor SRE).",
            "accion_inmediata": "NINGUNA",
            "propuesta_codigo": "",
        }


def _write_proposal(diagnosis: dict, tb_str: str) -> str:
    os.makedirs(PROPOSALS_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    path = os.path.join(PROPOSALS_DIR, f"FIX_{ts}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# Propuesta de corrección — {ts}\n\n")
        f.write(f"**Gravedad:** {diagnosis.get('gravedad')}\n\n")
        f.write(f"**Causa raíz:** {diagnosis.get('causa_raiz')}\n\n")
        f.write(f"**Acción inmediata tomada:** {diagnosis.get('accion_inmediata')}\n\n")
        f.write(f"**Propuesta de código:**\n\n{diagnosis.get('propuesta_codigo')}\n\n")
        f.write("## Traceback completo\n\n```\n" + tb_str + "\n```\n")
    logger.info("Propuesta de corrección guardada en %s", path)
    return path


def sre_crash_handler(exc_type, exc_value, exc_traceback, notifier=None):
    """Reemplazo de sys.excepthook (Instrucción 5). Se registra desde
    j_main.py con: sys.excepthook = lambda *a: sre_crash_handler(*a, notifier=notifier)
    """
    tb_str = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
    logger.critical("Motor SRE: excepción no manejada capturada:\n%s", tb_str)

    context_docs = _semantic_context(tb_str)
    locals_dump = _dump_locals(exc_traceback)

    prompt = f"""Eres el SRE (Senior Site Reliability Engineer & Python Debugger) de un bot
de trading algorítmico sobre la API de PPI (mercado argentino). Se produjo
un crash. Analizá el traceback y el contexto de código relacionado, y
devolvé EXCLUSIVAMENTE un JSON con este esquema exacto, sin texto
adicional ni markdown:
{{"gravedad": "CRÍTICA|ALTA|MEDIA|BAJA", "causa_raiz": "...",
"accion_inmediata": "TRIGGER_KILL_SWITCH|NINGUNA", "propuesta_codigo": "..."}}

TRACEBACK:
{tb_str}

VARIABLES LOCALES DEL FRAME DONDE OCURRIÓ (ya ofuscadas si eran secretos):
{json.dumps(locals_dump, ensure_ascii=False, indent=2)}

CONTEXTO DE CÓDIGO RELACIONADO (búsqueda semántica sobre el repositorio):
{json.dumps(context_docs, ensure_ascii=False)[:4000]}
"""
    diagnosis = _invoke_sre_model(prompt)
    proposal_path = _write_proposal(diagnosis, tb_str)

    if diagnosis.get("gravedad") == "CRÍTICA" or diagnosis.get("accion_inmediata") == "TRIGGER_KILL_SWITCH":
        try:
            import p_risk_guardian as risk_guardian
            risk_guardian.trigger_halt(
                f"Auto-Sanación SRE (gravedad CRÍTICA): {diagnosis.get('causa_raiz')}"
            )
            logger.critical("Kill switch activado automáticamente por el motor SRE. "
                             "Bot en MAINTENANCE_LOCKED hasta revisión manual "
                             "(r_clear_kill_switch.py, con confirmación humana).")
        except Exception as e:
            logger.error("El motor SRE no pudo activar el kill switch: %s", e)

        if notifier is not None:
            try:
                notifier.notify_error(
                    f"🔴 CRASH CRÍTICO — diagnóstico SRE: {diagnosis.get('causa_raiz')}. "
                    f"Kill switch activado. Propuesta guardada en {proposal_path}."
                )
            except Exception:
                pass


def install(notifier=None):
    """Registra el crash handler como sys.excepthook. Llamar una sola vez
    al arrancar j_main.py, después de configurar logging."""
    def _hook(exc_type, exc_value, exc_traceback):
        sre_crash_handler(exc_type, exc_value, exc_traceback, notifier=notifier)

    sys.excepthook = _hook
    logger.info("Motor de introspección SRE instalado (sys.excepthook).")
