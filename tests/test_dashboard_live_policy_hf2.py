from datetime import datetime
from zoneinfo import ZoneInfo

import eb_dashboard_live_policy_hf2 as live

TZ=ZoneInfo("America/Argentina/Buenos_Aires")
NOW=datetime(2026,9,5,12,0,tzinfo=TZ)


def test_decisions_only_current_argentina_day():
    rows=[
        {"id":1,"decided_at":"2026-09-05T14:00:00+00:00"},
        {"id":2,"decided_at":"2026-09-04T14:00:00+00:00"},
        {"id":3,"decided_at":"invalid"},
    ]
    assert [r["id"] for r in live.decisions_for_live(rows,now=NOW)]==[1]


def test_closed_positions_only_current_argentina_day():
    rows=[
        {"id":1,"closed_at":"2026-09-05T02:30:00-03:00"},
        {"id":2,"closed_at":"2026-09-04T23:59:59-03:00"},
    ]
    assert [r["id"] for r in live.closed_for_live(rows,now=NOW)]==[1]


def test_tablet_page_defaults_to_twenty_and_caps_at_fifty():
    rows=list(range(200))
    assert len(live.page_for_tablet(rows).items)==20
    assert len(live.page_for_tablet(rows,limit=999).items)==50


def test_interactive_refresh_is_manual_by_default():
    policy=live.refresh_policy(requested_seconds=0,interactive_details=True)
    assert policy["automatic"] is False
    assert policy["seconds"]==0


def test_if_auto_refresh_is_explicit_interactive_page_uses_minimum_sixty_seconds():
    policy=live.refresh_policy(requested_seconds=30,interactive_details=True)
    assert policy["automatic"] is True
    assert policy["seconds"]==60
    assert policy["preserve_focus_required"] is True
    assert policy["preserve_pagination_required"] is True
