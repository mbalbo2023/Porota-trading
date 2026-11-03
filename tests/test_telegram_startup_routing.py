"""Contratos de cableado del portón de arranque con el lector único Telegram."""

import importlib
import sys
from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

import ao_startup_gate as startup_gate
import l_order_confirmation as confirmation


class _NotifierTexto:
    def __init__(self, texto, sender_id="123"):
        self.texto = texto
        self.sender_id = sender_id
        self.mensajes = []

    def get_telegram_button_taps(self, offset=0):
        return ([{"text": self.texto, "sender_id": self.sender_id}], offset + 1)

    def send_telegram(self, mensaje):
        self.mensajes.append(mensaje)
        return True


class _NotifierBoton(_NotifierTexto):
    def get_telegram_button_taps(self, offset=0):
        return ([{
            "callback_id": "cb-1",
            "data": self.texto,
            "sender_id": self.sender_id,
        }], offset + 1)

    def answer_telegram_callback(self, callback_id, text=""):
        return None


def _preparar(tmp_path, monkeypatch):
    monkeypatch.setenv("STARTUP_STATE_PATH", str(tmp_path / "startup.json"))
    monkeypatch.setenv("TESTING_LOG_PATH", str(tmp_path / "trace.jsonl"))
    importlib.reload(startup_gate)
    codigo = startup_gate.solicitar_autorizacion(notifier=None)
    if hasattr(confirmation.process_button_taps, "_offset"):
        delattr(confirmation.process_button_taps, "_offset")
    return codigo


def test_mensaje_simular_llega_al_porton(tmp_path, monkeypatch):
    codigo = _preparar(tmp_path, monkeypatch)
    notifier = _NotifierTexto(f"SIMULAR {codigo}")

    confirmation.process_button_taps(object(), notifier, None)

    assert startup_gate.esta_en_simulacion() is True
    assert any("SIMULACION" in mensaje for mensaje in notifier.mensajes)


def test_boton_simulacion_llega_al_porton(tmp_path, monkeypatch):
    codigo = _preparar(tmp_path, monkeypatch)
    notifier = _NotifierBoton(f"arranque:sim:{codigo}")

    confirmation.process_button_taps(object(), notifier, None)

    assert startup_gate.esta_en_simulacion() is True
    assert any("SIMULACION" in mensaje for mensaje in notifier.mensajes)
