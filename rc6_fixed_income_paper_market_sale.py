"""Fail-closed boundary for native FI market sales during the A870 port.

Historical #778 accounting is preserved in GitHub, not admitted here: its
caller-authored book hashes do not establish independently trusted Book2,
post-COMMIT decision, tariff or settlement provenance. No sale writer or
legacy native-sale cash is enabled by this module. Maturity with no sale
receipts can still reconcile its independently reviewed issuer cash.
"""
from __future__ import annotations

PAPER_ONLY = True
REAL_ROUTES_USED = ()
ENTRY_AUTHORITY = False
REAL_SALE_AUTHORITY = False
REASON = "FI_PAPER_MARKET_SALE_MODELED_SETTLEMENT"
_AUTHORITY_ERROR = "FI_NATIVE_MARKET_EXIT_NOT_ADMITTED: INDEPENDENT_AUTHORITY_UNBOUND"


def sale_rows_checked(connection, paper_id):
    """Absence is not a sale; persisted native sales remain unreconciled/HOLD."""
    present = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='paper_fi_native_sales'"
    ).fetchone()
    if present and connection.execute(
            "SELECT 1 FROM paper_fi_native_sales WHERE paper_id=? LIMIT 1",
            (paper_id,)).fetchone():
        raise ValueError(_AUTHORITY_ERROR)
    return []


def apply_paper_market_sale(store, broker, *, paper_id, request_id, nominal,
                            quote, book_bytes, approved_book_sha256,
                            fee_bytes, approved_fee_sha256, applied_at):
    """Reject before opening SQLite; a caller pin is not independent authority."""
    raise ValueError(_AUTHORITY_ERROR)
