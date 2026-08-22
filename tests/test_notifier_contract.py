"""Contrato de retorno del canal Telegram usado por el verificador de APIs."""

import sys
import json
from pathlib import Path


RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

import b_notifiers


class _Respuesta:
    def __init__(self, status_code=200, text='{"ok": true}'):
        self.status_code = status_code
        self.text = text

    def json(self):
        return json.loads(self.text)


def _notificador():
    n = b_notifiers.MultiChannelNotifier()
    n.telegram_token = "token_de_prueba"
    n.telegram_chat_id = "12345"
    return n


def test_send_telegram_devuelve_true_en_http_200(monkeypatch):
    monkeypatch.setattr(b_notifiers.requests, "post", lambda *a, **k: _Respuesta())
    assert _notificador().send_telegram("prueba") is True


def test_confirmacion_generica_devuelve_true_en_http_200(monkeypatch):
    monkeypatch.setattr(b_notifiers.requests, "post", lambda *a, **k: _Respuesta())
    assert _notificador().send_telegram_generic_confirmation(
        "prueba", "VERIF_OK", "VERIF_CANCEL") is True


def test_confirmacion_generica_devuelve_false_en_error_http(monkeypatch):
    monkeypatch.setattr(
        b_notifiers.requests,
        "post",
        lambda *a, **k: _Respuesta(status_code=401, text='{"ok": false}'),
    )
    assert _notificador().send_telegram_generic_confirmation(
        "prueba", "VERIF_OK", "VERIF_CANCEL") is False


def test_http_200_con_ok_false_no_es_un_exito(monkeypatch):
    monkeypatch.setattr(
        b_notifiers.requests,
        "post",
        lambda *a, **k: _Respuesta(status_code=200, text='{"ok": false}'),
    )
    assert _notificador().send_telegram_generic_confirmation(
        "prueba", "VERIF_OK", "VERIF_CANCEL") is False
