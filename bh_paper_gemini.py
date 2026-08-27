"""Porton Gemini aislado para la simulacion productiva.

No importa clientes PPI, cuentas ni modulos de ejecucion. Recibe solamente
variables ya observadas y devuelve un veto/aprobacion. Ante cualquier falla
queda cerrado: una senal deterministica no abre ni siquiera una posicion
simulada sin el veredicto de IA requerido por este modo.
"""

from __future__ import annotations

import json
import os


PING = ('Devolve exclusivamente JSON valido: '
        '{"decision":"HOLD","score":0.0,"veto":true,"reason":"healthcheck","risks":[]}')

# Modelos de texto estables vigentes, en orden de preferencia. La lista de la
# cuenta manda: estos nombres son solamente el respaldo si el inventario no
# pudiera consultarse. Nunca se vuelve silenciosamente a un modelo retirado.
CURRENT_TEXT_MODELS = (
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
)
RETIRED_MODELS = {"gemini-2.5-flash-lite"}
NON_TEXT_MARKERS = (
    "embedding", "imagen", "veo", "tts", "live", "robotics", "computer-use",
    "image", "audio", "translate", "aqa", "learnlm",
)


def _clean_model_name(value):
    name = str(value or "").strip()
    return name[7:] if name.startswith("models/") else name


def _supports_generate_content(model):
    actions = getattr(model, "supported_actions", None) or []
    return any("generatecontent" in str(action).replace("_", "").lower()
               for action in actions)


def rank_models(configured="", chain=(), discovered=None):
    """Ordena sólo modelos de texto utilizables por la clave actual.

    ``discovered`` es ``None`` cuando listar modelos falló y una lista cuando
    Google respondió. Una lista vacía es una respuesta válida y no habilita a
    inventar disponibilidad.
    """
    requested = [_clean_model_name(configured),
                 *(_clean_model_name(value) for value in chain)]
    requested = [name for name in requested if name and name not in RETIRED_MODELS]
    if discovered is None:
        pool = [*CURRENT_TEXT_MODELS, *requested]
    else:
        available = {_clean_model_name(value) for value in discovered}
        available.discard("")
        pool = [name for name in requested if name in available]
        pool += [name for name in CURRENT_TEXT_MODELS if name in available]
        pool += sorted(
            name for name in available
            if name.startswith("gemini-")
            and not any(marker in name.lower() for marker in NON_TEXT_MARKERS)
        )
    result = []
    for name in pool:
        if name not in result and name not in RETIRED_MODELS:
            result.append(name)
    return result


class GeminiPaperGate:
    def __init__(self):
        from google import genai
        api_key = os.getenv("GEMINI_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY no configurada en el observador aislado.")
        configured = os.getenv("GEMINI_MODEL", "").strip()
        chain = [item.strip() for item in os.getenv("GEMINI_MODEL_CHAIN", "").split(",")
                 if item.strip()]
        self.client = genai.Client(api_key=api_key)
        discovered = self._discover_models()
        self.models = rank_models(configured, chain, discovered)
        if not self.models:
            raise RuntimeError(
                "La clave no publica ningun modelo de texto compatible con generateContent."
            )
        self.model = self.models[0]

    def _discover_models(self):
        """Inventario barato de la cuenta; no genera contenido ni toca PPI."""
        try:
            result = []
            for model in self.client.models.list():
                name = _clean_model_name(getattr(model, "name", ""))
                if (name.startswith("gemini-") and _supports_generate_content(model)
                        and not any(marker in name.lower() for marker in NON_TEXT_MARKERS)):
                    result.append(name)
            return result
        except Exception:
            # Un proxy o una version vieja del SDK puede impedir listar. En
            # ese caso _call prueba la cadena estable y conserva fail-closed.
            return None

    @staticmethod
    def _parse(response, model):
        text = (getattr(response, "text", "") or "").strip()
        text = text.replace("```json", "").replace("```", "").strip()
        data = json.loads(text)
        decision = str(data.get("decision", "VETO")).upper()
        veto = data.get("veto", True)
        if not isinstance(veto, bool):
            raise ValueError("veto Gemini no es booleano")
        if decision not in {"APPROVE", "VETO", "HOLD"}:
            raise ValueError("decision Gemini fuera del contrato")
        score = max(0.0, min(1.0, float(data.get("score", 0))))
        reason = str(data.get("reason") or "Sin fundamento")[:800]
        risks = data.get("risks") if isinstance(data.get("risks"), list) else []
        return {"decision": decision, "score": score, "veto": veto,
                "reason": reason, "risks": [str(v)[:160] for v in risks[:8]],
                "model": model, "raw": data}

    def _call(self, prompt):
        from google.genai import types
        errors = []
        for model in self.models:
            try:
                response = self.client.models.generate_content(
                    model=model, contents=prompt,
                    config=types.GenerateContentConfig(response_mime_type="application/json"),
                )
                result = self._parse(response, model)
                self.model = model
                return result
            except Exception as exc:
                errors.append(f"{model}: {type(exc).__name__}: {str(exc)[:160]}")
        raise RuntimeError("; ".join(errors)[:700])

    def healthcheck(self):
        result = self._call(PING)
        return {"ok": True, "model": result["model"], "detail": "Contrato JSON correcto."}

    def evaluate(self, quote, deterministic_score, features, context=None):
        payload = {
            "mode": "PRODUCTION_PAPER",
            "real_orders_allowed": False,
            "symbol": quote.symbol,
            "asset_class": quote.asset_class,
            "last": str(quote.last), "bid": str(quote.bid), "ask": str(quote.ask),
            "bid_size": str(quote.bid_size), "ask_size": str(quote.ask_size),
            "deterministic_score": str(deterministic_score),
            "technical_features": features,
            "context": context or {},
        }
        prompt = (
            "Sos el porton de riesgo de una simulacion educativa del mercado argentino. "
            "No ejecutes herramientas ni ordenes. Evalua exclusivamente los datos provistos. "
            "Responde JSON con decision APPROVE, VETO o HOLD; score entre 0 y 1; veto booleano; "
            "reason breve; risks lista. APPROVE solo si la evidencia es suficiente y no hay "
            "riesgo evidente. Ante duda usa VETO o HOLD. DATOS:\n" +
            json.dumps(payload, ensure_ascii=False, default=str)
        )
        return self._call(prompt)
