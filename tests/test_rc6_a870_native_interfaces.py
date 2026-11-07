"""Required native FI modules must exist; existence never grants authority."""
import importlib.util

import pytest


@pytest.mark.parametrize("name", [
    "rc6_fixed_income_native_exit_contract",
    "rc6_fixed_income_native_exit",
    "rc6_fixed_income_paper_market_sale",
    "rc6_fi_sell_persisted_evidence",
])
def test_native_fi_contract_is_available_for_independent_validation(name):
    assert importlib.util.find_spec(name) is not None, f"Missing FI contract: {name}"


def test_caller_hashes_cannot_open_sale_writer_or_connect_database():
    from rc6_fixed_income_paper_market_sale import apply_paper_market_sale

    class UnavailableAuthorityStore:
        def connect(self):
            raise AssertionError("An unauthorized sale must not open SQLite")

    with pytest.raises(ValueError, match="INDEPENDENT_AUTHORITY_UNBOUND"):
        apply_paper_market_sale(UnavailableAuthorityStore(), object(),
            paper_id="PAPER-AL30", request_id="self-authored-hashes", nominal="100",
            quote=object(), book_bytes=b"SYNTHETIC_NOT_AUTHENTICATED",
            approved_book_sha256="a" * 64, fee_bytes=b"caller fee",
            approved_fee_sha256="b" * 64, applied_at="2026-11-06T15:00:00Z")
