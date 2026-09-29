"""Conversión numérica explícita para entradas y salidas humanas es-AR."""
from decimal import Decimal, InvalidOperation


def parse_es_ar_number(value):
    """Interpreta punto como miles y coma como decimal; nunca como en-US."""
    text = str(value or "").strip().replace("\u00a0", "").replace(" ", "")
    if not text:
        raise ValueError("NUMERO_ES_AR_VACIO")
    normalized = text.replace(".", "").replace(",", ".")
    try:
        number = Decimal(normalized)
    except InvalidOperation as exc:
        raise ValueError("NUMERO_ES_AR_INVALIDO") from exc
    if not number.is_finite():
        raise ValueError("NUMERO_ES_AR_INVALIDO")
    return number


def format_es_ar_number(value, decimals=2):
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("NUMERO_INVALIDO") from exc
    if not number.is_finite():
        raise ValueError("NUMERO_INVALIDO")
    rendered = f"{number:,.{int(decimals)}f}"
    return rendered.translate(str.maketrans({",": ".", ".": ","}))
