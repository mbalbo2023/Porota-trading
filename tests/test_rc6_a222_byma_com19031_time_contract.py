"""A222 official BYMA COM19031: amended PPT/SENEBI intraday clock.

Official PDF dated 28-Oct-2026, effective 02-Nov-2026, expressly replacing
COM19024; independently downloaded, read and visually checked against COM19024.
https://cdn.prod.website-files.com/6697a441a50c6b926e1972e0/6ae39100dbd9381f5489d706_BYMA-COM19031-Horarios_Negociacion_liquidacion_recepcion_de_informacion.pdf
SHA256: fe31c7baf8c51692e4bbbcc9138455563025964e9ae7178337c3c55990df6af0

PPT/SENEBI intraday 10:30-17:30 is a *separate* eligibility contract; it
must not truncate regular BYMA T+1 equity/options observations at 18:00.
The new check is not wired to a host timer or PAPER trading admission.
"""

from datetime import date, datetime, time
import unittest
from zoneinfo import ZoneInfo

from co_market_sessions_hf6 import byma_source_session_for
from cq_contract_readiness_hf6 import canonical_family
from cy_market_source_arbitration_hf6 import SourceSnapshot, primary_live_source, select_live_snapshot
import iol_shadow_collector_rc6 as iol_windows
from scripts import rc6_source_cycle_dispatch as dispatch


ART = ZoneInfo("America/Argentina/Buenos_Aires")


def local(hour, minute=0, second=0, *, day=2, month=11, year=2026):
    return datetime(year, month, day, hour, minute, second, tzinfo=ART)


class Com19031TimeAuthorityTest(unittest.TestCase):
    def test_superseding_authority_and_exact_intraday_boundary(self):
        self.assertEqual(dispatch.COM19031_EFFECTIVE, date(2026, 11, 2))
        self.assertEqual(dispatch.PPT_SENEBI_INTRADAY_CLOSE, time(17, 30))
        for instant in (local(10, 30), local(17, 29, 59)):
            with self.subTest(instant=instant):
                self.assertEqual(dispatch.phase_status(instant, "intraday_ppt_senebi"),
                                 (True, "COM19031_PPT_SENEBI_INTRADAY"))
        for instant in (local(10, 29), local(17, 30), local(17, 31),
                        local(17, 44), local(17, 45), local(18)):
            with self.subTest(instant=instant):
                self.assertEqual(dispatch.phase_status(instant, "intraday_ppt_senebi"),
                                 (False, "PPT_SENEBI_INTRADAY_NOT_DUE"))

    def test_t1_observation_and_postclose_remain_separate(self):
        # 17:31-17:59 is forbidden for the special PPT/SENEBI intraday
        # operation but still within regular-equity/options T+1 source-only
        # observation. This must remain read-only and cannot grant PAPER entry.
        for instant in (local(17, 30), local(17, 31), local(17, 44), local(17, 59, 59)):
            with self.subTest(instant=instant):
                self.assertTrue(dispatch.phase_status(instant, "late_source")[0])
                self.assertTrue(iol_windows.is_late_observation_window(instant))
                self.assertTrue(iol_windows.is_verified_late_contract_window(
                    instant, family="ACCIONES", market="BYMA", settlement="A-24HS",
                    settlement_source="PPI_FIELD"))
                self.assertFalse(iol_windows.is_verified_late_contract_window(
                    instant, family="BONOS", market="BYMA", settlement="A-24HS",
                    settlement_source="PPI_FIELD"))
                self.assertFalse(dispatch.phase_status(instant, "intraday_ppt_senebi")[0])
        self.assertFalse(dispatch.phase_status(local(18), "late_source")[0])
        self.assertTrue(dispatch.phase_status(local(18, 8), "postclose_snapshot")[0])
        self.assertFalse(dispatch.phase_status(local(18, 8), "intraday_ppt_senebi")[0])

    def test_clock_contract_fails_closed_before_effective_holiday_and_expiry(self):
        for instant in (
            local(17, 29, day=30, month=10),
            local(17, 29, day=9),  # official BYMA holiday in audited calendar
            local(17, 29, day=4, month=1, year=2027),
            datetime(2026, 11, 2, 17, 29),  # missing timezone
        ):
            with self.subTest(instant=instant):
                self.assertFalse(dispatch.phase_status(instant, "intraday_ppt_senebi")[0])

    def test_archive_window_is_scoped_to_audited_regular_t1_equity_sessions(self):
        for day, family, settlement, expected_close in (
            (date(2026, 11, 24), "ACCIONES", "A-24HS", time(18)),
            (date(2026, 11, 24), "CEDEARS", "24HS", time(18)),
            (date(2026, 11, 24), "ACCIONES", "CI", time(17)),
            (date(2026, 11, 24), "ACCIONES", "UNKNOWN", time(17)),
            (date(2026, 11, 24), "BONOS", "A-24HS", time(17)),
            (date(2026, 11, 24), "OPCIONES", "A-24HS", None),
            (date(2026, 10, 30), "ACCIONES", "A-24HS", time(17)),
            (date(2026, 11, 6), "ACCIONES", "A-24HS", time(17)),
            (date(2026, 11, 6), "ACCIONES", "CI", None),
            (date(2026, 11, 9), "ACCIONES", "A-24HS", None),
            (date(2027, 1, 4), "ACCIONES", "A-24HS", None),
        ):
            with self.subTest(day=day, family=family, settlement=settlement):
                session = byma_source_session_for(day, market="BYMA", family=family,
                                                  settlement=settlement)
                self.assertEqual(session.regular_close if session else None, expected_close)

    def test_on_and_fci_aliases_keep_ppi_primary_without_authorizing_trade(self):
        for alias, canonical in (("OBLIGACIONES", "ON"), ("ON", "ON"),
                                 ("FCI", "FCI_LOCAL"), ("FCI_LOCAL", "FCI_LOCAL")):
            with self.subTest(alias=alias):
                self.assertEqual(canonical_family(alias), canonical)
                self.assertEqual(primary_live_source(alias), "PPI")
                self.assertEqual(primary_live_source(canonical), "PPI")
                # An IOL-only snapshot never substitutes for the PPI live
                # source merely because an alias was normalized elsewhere.
                alternative = SourceSnapshot("IOL", canonical, local(16), {"last": 100}, True)
                result = select_live_snapshot(alias, {"IOL": alternative})
                self.assertEqual(result.status, "HOLD_DATA_SOURCE")
                self.assertEqual(result.reason, "PRIMARY_SOURCE_MISSING:PPI")


if __name__ == "__main__":
    unittest.main()
