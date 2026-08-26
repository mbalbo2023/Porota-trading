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


class GeminiPaperGate:
    def __init__(self):
        from google import genai
        api_key = os.getenv("GEMINI_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY no configurada en el observador aislado.")
        configured = os.getenv("GEMINI_MODEL", "").strip()
        chain = [item.strip() for item in os.getenv("GEMINI_MODEL_CHAIN", "").split(",")
                 if item.strip()]
        self.models = []
        for model in [configured, *chain, "gemini-2.5-flash-lite"]:
            if model and model not in self.models:
                self.models.append(model)
        self.client = genai.Client(api_key=api_key)
        self.model = self.models[0]

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
