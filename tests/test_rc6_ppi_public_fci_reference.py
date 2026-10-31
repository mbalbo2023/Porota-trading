"""Offline, no-login evidence for PPI's *public* FCI discoverability."""
from datetime import datetime, timezone
from decimal import Decimal
import pytest

from rc6_cafci_fci_reference import CafciClassReference
from rc6_ppi_public_fci_reference import (
    PAPER_ONLY, REAL_ROUTES_USED, RUNTIME_ENTRY_AUTHORITY, SOURCE_AUTHENTICATED,
    SOURCE_URL, inspect_public_fci_listing,
)


STAMP = "2026-10-31T15:40:00Z"
ROWS = [
    {"name": "Adcap Acciones Clase A", "category": "Renta Variable",
     "detail_url": "https://trading.portfoliopersonal.com/Cotizaciones/FCI/293"},
    {"name": "Adcap Acciones Clase B", "category": "Renta Variable",
     "detail_url": "https://trading.portfoliopersonal.com/Cotizaciones/FCI/294"},
]


def test_discovery_keeps_public_metadata_only_and_never_authorizes_entry():
    receipt = inspect_public_fci_listing(ROWS, received_at=STAMP).public_receipt()
    assert receipt["status"] == "REFERENCE_ONLY"
    assert receipt["source_url"] == SOURCE_URL
    assert receipt["received_at"] == "2026-10-31T15:40:00+00:00"
    assert receipt["provider_time"] is None
    assert receipt["rows"][0] == {"name": "Adcap Acciones Clase A",
                                  "category": "Renta Variable", "ppi_detail_id": "293"}
    assert receipt["exact_cafci_class_binding"] is False
    assert receipt["runtime_entry_authority"] is RUNTIME_ENTRY_AUTHORITY is False
    assert receipt["source_authenticated"] is SOURCE_AUTHENTICATED is False
    assert receipt["paper_only"] is PAPER_ONLY is True
    assert receipt["real_routes_used"] == REAL_ROUTES_USED == ()
    assert not any(field in str(receipt) for field in (
        "nav_per_unit", "account_tariff", "cutoff_hhmm", "settlement_receipt", "api_key"))


@pytest.mark.parametrize("bad_url", [
    "https://trading.portfoliopersonal.com/Cotizaciones/FCIs/293",
    "https://trading.portfoliopersonal.com/Cotizaciones/FCI/293?token=secret",
    "https://trading.portfoliopersonal.com/Operar/293",
    "https://evil.example/Cotizaciones/FCI/293",
    "http://trading.portfoliopersonal.com/Cotizaciones/FCI/293",
    "https://trading.portfoliopersonal.com@evil.example/Cotizaciones/FCI/293",
    "https://trading.portfoliopersonal.com/Cotizaciones/FCI/293#order",
])
def test_rejects_unauthenticated_or_order_or_mutating_links(bad_url):
    with pytest.raises(ValueError, match="PPI_PUBLIC_FCI_DETAIL_ROUTE_UNVERIFIED"):
        inspect_public_fci_listing([{**ROWS[0], "detail_url": bad_url}], received_at=STAMP)


@pytest.mark.parametrize("change", [
    {"name": "Adcap Acciones Clase A", "category": "Renta Variable", "detail_url": "https://trading.portfoliopersonal.com/Cotizaciones/FCI/293", "ticker": "CAFCI:308"},
    {"name": "Adcap Acciones Clase A", "category": "Renta Variable", "detail_url": "https://trading.portfoliopersonal.com/Cotizaciones/FCI/293", "fee": "0"},
])
def test_unknown_quote_class_binding_or_fee_fields_fail_closed(change):
    with pytest.raises(ValueError, match="PPI_PUBLIC_FCI_LISTING_SHAPE_INVALID"):
        inspect_public_fci_listing([change], received_at=STAMP)


@pytest.mark.parametrize("invalid", [
    [ROWS[0], ROWS[0]],
    [ROWS[0], {**ROWS[1], "detail_url": ROWS[0]["detail_url"]}],
    [ROWS[0], {**ROWS[1], "name": ROWS[0]["name"].upper()}],
])
def test_duplicate_display_names_or_ppi_detail_ids_block_identity(invalid):
    with pytest.raises(ValueError, match="PPI_PUBLIC_FCI_DUPLICATE_DISPLAY_IDENTITY"):
        inspect_public_fci_listing(invalid, received_at=STAMP)


@pytest.mark.parametrize("time", ["2026-10-31", "n/a", "2026-10-31T15:40:00"])
def test_capture_clock_must_be_aware(time):
    with pytest.raises(ValueError, match="PPI_PUBLIC_FCI_CAPTURE_TIME_INVALID"):
        inspect_public_fci_listing(ROWS, received_at=time)


def test_expected_public_route_is_exact_and_no_completeness_claim():
    with pytest.raises(ValueError, match="PPI_PUBLIC_FCI_SOURCE_URL_UNVERIFIED"):
        inspect_public_fci_listing(ROWS, received_at=STAMP,
                                   source_url="https://trading.portfoliopersonal.com/Cotizaciones/FCIs")
    assert inspect_public_fci_listing(ROWS[:1], received_at=STAMP).status == "REFERENCE_ONLY"


def test_identical_display_name_and_numeric_id_never_bind_ppi_to_cafci():
    # A237: even a matching name and a coincidentally identical number belong
    # to two unrelated ID namespaces. No fuzzy/name join or runtime authority.
    cafci = CafciClassReference(
        class_name="Adcap Acciones Clase A", cafci_class_id=293,
        cnv_fund_id=123, management_company="Gestora Sintetica S.A.",
        currency_raw="ARS", nav_effective_date=None,
        nav_per_thousand=None, nav_per_unit=None,
        subscription_fee_pct=Decimal("0"), redemption_fee_pct=Decimal("0"),
        settlement_days_reported=1, minimum_subscription_reported=None,
        quality_holds=("NAV_EMPTY_CELL",),
        known_at=datetime(2026, 10, 31, 15, 40, tzinfo=timezone.utc),
        source_sha256="a" * 64,
    )
    public = inspect_public_fci_listing(ROWS[:1], received_at=STAMP)
    assert public.rows[0].name == cafci.class_name
    assert public.rows[0].ppi_detail_id == str(cafci.cafci_class_id)
    assert public.exact_cafci_class_binding is False
    assert public.runtime_entry_authority is False
    assert cafci.runtime_entry_authority is False
    assert not hasattr(public.rows[0], "cafci_class_id")
    assert not hasattr(cafci, "ppi_detail_id")
    with pytest.raises(ValueError, match="PPI_PUBLIC_FCI_LISTING_SHAPE_INVALID"):
        inspect_public_fci_listing(
            [{**ROWS[0], "cafci_class_id": cafci.cafci_class_id}],
            received_at=STAMP,
        )
