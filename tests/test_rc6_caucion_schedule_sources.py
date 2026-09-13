from datetime import date

import pytest

from rc6_caucion_schedule_gate import CaucionScheduleError, evaluate_schedule
from rc6_caucion_schedule_sources import (
    BYMA_SOURCE,
    PPI_SOURCE,
    build_all_schedule_evidence,
)


def test_next_session_has_independent_ars_and_usd_evidence():
    values=build_all_schedule_evidence(date(2026,9,14))
    assert set(values)=={"ARS","USD_MEP"}
    for currency,evidence in values.items():
        assert evidence.currency==currency
        assert evidence.byma_source==BYMA_SOURCE
        assert evidence.broker_source==PPI_SOURCE
        result=evaluate_schedule(evidence,now="2026-09-14T10:31:00-03:00")
        assert result["state"]=="OPEN"
        assert result["calendar_state"]=="OPEN"
        assert result["cutoff_state"]=="OPEN"


def test_sunday_is_rejected_fail_closed():
    with pytest.raises(CaucionScheduleError,match="BYMA_CALENDAR_CLOSED_OR_UNAVAILABLE"):
        build_all_schedule_evidence(date(2026,9,13))
