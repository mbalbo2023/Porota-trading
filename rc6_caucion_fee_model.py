"""Versioned PUBLIC PAPER sensitivity for ARS colocadora caucion costs.

Not a PPI account quote, effective account tariff, actual tax bill or right to
trade. Never use this model on a real broker route. Original as-booked rows
remain immutable; this model supports *prospective* PAPER and a separately
attested, known-at historical PAPER-only cost restatement.

Official sources (read 2026-10-28 UTC):
  PPI: https://www.portfoliopersonal.com/Contenido/comisiones?v=072026
    ARS colocadora annual agent commission 2% plus VAT and market rights.
  BYMA: https://www.byma.com.ar/mercado/regulacion/derechos-membresias
    August 2026 PDF SHA256:
    f63f26bcecdcbd51263d798e75e0dda7c413afef6f9c2c3c37d487b7132aac87
    p1: 0.0450% caucion market right; p2: 0.0450% guarantee administration;
    p3 ref06: proportional to term up to 90 days, on greater value.
Both rights are included in this deliberately conservative scenario; their
actual pass-through to a particular PPI account remains NO_VERIFICADO.
"""

from decimal import Decimal, ROUND_HALF_UP

MODEL = "RC6_CAUCION_PAPER_PUBLIC_CONSERVATIVE_V2_2026_08_14"
SOURCE_SHA256 = "f63f26bcecdcbd51263d798e75e0dda7c413afef6f9c2c3c37d487b7132aac87"
ACCOUNT_FEES_VERIFIED = False
CENT = Decimal("0.01")
PPI_ANNUAL_COMMISSION = Decimal("0.0200")
BYMA_MARKET_RIGHT = Decimal("0.00045")
BYMA_GUARANTEE_ADMINISTRATION = Decimal("0.00045")
VAT = Decimal("0.21")


def _money(value):
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _number(value, *, positive=False):
    n = Decimal(str(value))
    if not n.is_finite() or n < 0 or (positive and n <= 0):
        raise ValueError("CAUCION_PUBLIC_FEE_INVALID_AMOUNT")
    return n


def old_public_modeled_fee(principal, days):
    """Forensic reconstruction ONLY, not an authorized current fee model."""
    amount = _number(principal, positive=True)
    if type(days) is not int or not 1 <= days <= 90:
        raise ValueError("CAUCION_PUBLIC_FEE_TERM_UNSUPPORTED")
    return _money(amount * (PPI_ANNUAL_COMMISSION + BYMA_MARKET_RIGHT)
                  * (1 + VAT) * days / 365)


def conservative_public_modeled_fee(principal, gross_interest, days):
    """PAPER hypothetical total; never certify broker costs or waive admission."""
    amount = _number(principal, positive=True)
    interest = _number(gross_interest)
    if type(days) is not int or not 1 <= days <= 90:
        raise ValueError("CAUCION_PUBLIC_FEE_TERM_UNSUPPORTED")
    future_value = amount + interest
    greater_value = max(amount, future_value)
    commission = amount * PPI_ANNUAL_COMMISSION * (1 + VAT) * days / 365
    rights = (greater_value * (BYMA_MARKET_RIGHT + BYMA_GUARANTEE_ADMINISTRATION)
              * (1 + VAT) * days / 90)
    return _money(commission + rights)


def restated_public_modeled_fee(principal, gross_interest, saved_total_fees, days):
    """Never lower original booked costs; may reveal a negative PAPER net."""
    saved = _number(saved_total_fees)
    return max(saved, conservative_public_modeled_fee(principal, gross_interest, days))
