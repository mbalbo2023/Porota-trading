"""Native FI market sale must not bypass BYMA T+1 exact identity gates.

Uses only the existing PAPER settlement model; it never calls providers.
"""

import os
import unittest
from unittest.mock import patch

from cf_sale_settlement import validated_sale_settlement


TRADED = "2026-11-06T12:00:00-03:00"


class NativeFixedIncomeSettlementIdentityHolds(unittest.TestCase):
    def test_missing_market_family_currency_cannot_release_t1(self):
        with patch.dict(os.environ, {"PAPER_T1_FULL_DATE_RELEASE": "true"}):
            self.assertIsNone(validated_sale_settlement(
                "A-24HS", TRADED, None, "PENDING_CONFIRMATION"))

    def test_exact_bonos_ars_clears_after_11nov_full_settlement_day(self):
        with patch.dict(os.environ, {"PAPER_T1_FULL_DATE_RELEASE": "true"}):
            floor = validated_sale_settlement(
                "A-24HS", TRADED, None, "PENDING_CONFIRMATION",
                family="BONOS", market="BYMA", currency="ARS")
        self.assertEqual(floor.isoformat(), "2026-11-12T00:00:00-03:00")

    def test_exact_cedear_ccl_defers_to_12nov_full_settlement_day(self):
        with patch.dict(os.environ, {"PAPER_T1_FULL_DATE_RELEASE": "true"}):
            floor = validated_sale_settlement(
                "A-24HS", TRADED, None, "PENDING_CONFIRMATION",
                family="CEDEARS", market="BYMA", currency="USD_CCL")
        self.assertEqual(floor.isoformat(), "2026-11-13T00:00:00-03:00")


if __name__ == "__main__":
    unittest.main()
