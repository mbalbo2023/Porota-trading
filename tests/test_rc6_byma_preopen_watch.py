import json
from datetime import date
from pathlib import Path

import rc6_preopen as preopen
import scripts.rc6_byma_morning_watch as watch


TODAY=date(2026,9,25)


def write(tmp_path, *, state="NO_CHANGE", changed=None, observed_at="2026-09-25T11:30:00+00:00", errors=None):
    path=tmp_path/"byma_morning_watch_latest.json"
    path.write_text(json.dumps({
        "state":state,
        "changed_components":changed or [],
        "observed_at":observed_at,
        "errors":errors or [],
    }),encoding="utf-8")
    return path


def test_today_no_change_is_green(tmp_path, monkeypatch):
    monkeypatch.setattr(preopen,"BYMA_MORNING_WATCH",write(tmp_path))
    row=preopen.byma_morning_watch(TODAY)
    assert row["state"]=="GREEN"
    assert row["reason"]=="NO_CHANGE"


def test_hours_or_calendar_change_blocks_preopen_until_review(tmp_path, monkeypatch):
    monkeypatch.setattr(preopen,"BYMA_MORNING_WATCH",
                        write(tmp_path,state="CHANGED_REVIEW_REQUIRED",
                              changed=["HOURS","COMMUNICATIONS"]))
    row=preopen.byma_morning_watch(TODAY)
    assert row["state"]=="RED"
    assert row["critical_components"]==["HOURS"]


def test_exact_official_review_accepts_changed_page_and_pending_repeat(tmp_path, monkeypatch):
    path=write(tmp_path,state="CHANGED_REVIEW_REQUIRED",changed=["HOURS"])
    payload=json.loads(path.read_text())
    payload["pages"]={"HOURS":{"status":"OK","scope":"h1-to-footer-v2",
        "url":"https://www.byma.com.ar/mercado/horarios","sha256":"a"*64}}
    payload["pending_review"]={"HOURS":{"sha256":"a"*64}}
    path.write_text(json.dumps(payload))
    review=tmp_path/"review.json"
    review.write_text(json.dumps({"schema":"porota-byma-authority-review-v1",
        "decision_effect":"OBSERVE_ONLY","real_money_authorized":False,
        "valid_through_ar":"2026-12-31","pages":{"HOURS":{
            "url":"https://www.byma.com.ar/mercado/horarios","sha256":"a"*64}}}))
    monkeypatch.setattr(preopen,"BYMA_MORNING_WATCH",path)
    monkeypatch.setattr(preopen,"BYMA_AUTHORITY_REVIEW",review)
    assert preopen.byma_morning_watch(TODAY)["reason"]=="BYMA_CRITICAL_REVIEWED_EXACT"
    payload["state"]="NO_CHANGE"
    payload["changed_components"]=[]
    path.write_text(json.dumps(payload))
    assert preopen.byma_morning_watch(TODAY)["reviewed_critical_components"]==["HOURS"]
    payload["pages"]["HOURS"]["sha256"]="b"*64
    path.write_text(json.dumps(payload))
    assert preopen.byma_morning_watch(TODAY)["reason"]=="BYMA_AUTHORITY_CHANGE_REVIEW_REQUIRED"


def test_review_expiry_or_missing_scope_fails_closed(tmp_path,monkeypatch):
    path=write(tmp_path,state="CHANGED_REVIEW_REQUIRED",changed=["CALENDAR"])
    payload=json.loads(path.read_text())
    payload["pages"]={"CALENDAR":{"status":"OK","scope":"legacy",
        "url":"https://www.byma.com.ar/mercado/calendario-bursatil","sha256":"c"*64}}
    path.write_text(json.dumps(payload))
    review=tmp_path/"review.json"
    review.write_text(json.dumps({"schema":"porota-byma-authority-review-v1",
        "decision_effect":"OBSERVE_ONLY","real_money_authorized":False,
        "valid_through_ar":"2026-09-24","pages":{"CALENDAR":{
            "url":"https://www.byma.com.ar/mercado/calendario-bursatil","sha256":"c"*64}}}))
    monkeypatch.setattr(preopen,"BYMA_MORNING_WATCH",path)
    monkeypatch.setattr(preopen,"BYMA_AUTHORITY_REVIEW",review)
    assert preopen.byma_morning_watch(TODAY)["state"]=="RED"


def test_observed_20261020_host_transition_requires_exact_review(tmp_path,monkeypatch):
    """Replays the actual failed latest (06:10) into the scoped v2 capture."""
    host={"schema":watch.SCHEMA,"observed_at":"2026-10-20T06:10:44+00:00",
        "state":"CHANGED_REVIEW_REQUIRED",
        "changed_components":["CALENDAR","COMMUNICATIONS","HOURS","OPTIONS","OPEN_DATA"],
        "pages":{
            "HOURS":{"status":"OK","sha256":"5c9bcc216deb0e41b3568715acbbed486ec2d5c2a6facc5b5edca48d8d4f9bf4"},
            "CALENDAR":{"status":"OK","sha256":"3c9e72292230bd608a4ed38ef58b5e3384c42c024413745b8b543a7997a6c054"}},
        "structured_byma":{"status":"SCRAPED_PUBLIC_DATA","identity_count":3236,
                          "identity_sha256":"da091eb72b5d1d83ed0fd738690d685be562cb29312a9de93493b004644b8f91"},
        "signature":"13b7f68835ad538059e0e2bba73cf03c27f401ac1ed111b614118c0632937fad",
        "errors":[]}
    latest=tmp_path/"byma_morning_watch_latest.json"
    latest.write_text(json.dumps(host))
    review=json.loads(Path("ops/policy/rc6-byma-reviewed-authority-v1.json").read_text())
    current=dict(host, observed_at="2026-10-20T06:25:30+00:00",
                 pages={name:{"status":"OK","scope":"h1-to-footer-v2",
                    "url":review["pages"][name]["url"],
                    "sha256":review["pages"][name]["sha256"]}
                    for name in ("HOURS","CALENDAR")})
    result=watch.persist(tmp_path,current)
    assert result["pending_review"].keys()=={"HOURS","CALENDAR"}
    monkeypatch.setattr(preopen,"BYMA_MORNING_WATCH",latest)
    monkeypatch.setattr(preopen,"BYMA_AUTHORITY_REVIEW",
                        Path("ops/policy/rc6-byma-reviewed-authority-v1.json").resolve())
    today=date(2026,10,20)
    assert preopen.byma_morning_watch(today)["reason"]=="BYMA_CRITICAL_REVIEWED_EXACT"
    replay=watch.persist(tmp_path,dict(current,observed_at="2026-10-20T06:30:00+00:00"))
    assert replay["state"]=="NO_CHANGE"
    assert preopen.byma_morning_watch(today)["reviewed_critical_components"]==["CALENDAR","HOURS"]
    current["pages"]["CALENDAR"]["sha256"]="d"*64
    watch.persist(tmp_path,dict(current,observed_at="2026-10-20T06:31:00+00:00"))
    assert preopen.byma_morning_watch(today)["reason"]=="BYMA_AUTHORITY_CHANGE_REVIEW_REQUIRED"


def test_non_clock_official_change_is_visible_without_global_block(tmp_path, monkeypatch):
    monkeypatch.setattr(preopen,"BYMA_MORNING_WATCH",
                        write(tmp_path,state="CHANGED_REVIEW_REQUIRED",
                              changed=["OPTIONS"]))
    row=preopen.byma_morning_watch(TODAY)
    assert row["state"]=="AMBER"
    assert row["reason"]=="BYMA_CHANGE_REVIEW_REQUIRED"


def test_missing_or_stale_watch_is_red(tmp_path, monkeypatch):
    monkeypatch.setattr(preopen,"BYMA_MORNING_WATCH",tmp_path/"missing.json")
    assert preopen.byma_morning_watch(TODAY)["state"]=="RED"
    monkeypatch.setattr(preopen,"BYMA_MORNING_WATCH",
                        write(tmp_path,observed_at="2026-09-24T11:30:00+00:00"))
    assert preopen.byma_morning_watch(TODAY)["reason"]=="BYMA_WATCH_NOT_TODAY"


def test_dry_equivalent_accepts_only_recent_safe_prior_watch(tmp_path, monkeypatch):
    monkeypatch.setattr(preopen, "BYMA_MORNING_WATCH",
                        write(tmp_path, observed_at="2026-09-24T11:30:00+00:00"))
    class FixedDateTime:
        @classmethod
        def now(cls, tz):
            return preopen.datetime.fromisoformat("2026-09-25T00:10:00-03:00")
        fromisoformat = staticmethod(preopen.datetime.fromisoformat)
    monkeypatch.setattr(preopen, "datetime", FixedDateTime)
    row = preopen.byma_morning_watch(TODAY, allow_latest_safe=True)
    assert row["state"] == "AMBER"
    assert row["reason"] == "DRY_VALIDATION_LATEST_SAFE"


def test_dry_equivalent_rejects_old_or_changed_prior_watch(tmp_path, monkeypatch):
    monkeypatch.setattr(preopen, "BYMA_MORNING_WATCH",
                        write(tmp_path, observed_at="2026-09-20T11:30:00+00:00"))
    assert preopen.byma_morning_watch(TODAY, allow_latest_safe=True)["state"] == "RED"


def test_runtime_disk_reserve_is_blocking_below_two_gib_and_amber_below_target():
    assert preopen.BLOCKING_MIN_FREE_BYTES == 2 * 1024**3
    assert preopen.TARGET_FREE_BYTES == 8 * 1024**3


def test_iol_source_unavailable_is_amber_only_with_complete_fail_safe(tmp_path, monkeypatch):
    path = tmp_path / "iol_family_reference_latest.json"
    path.write_text(json.dumps({
        "refreshed_at": "2026-09-25T02:00:00+00:00",
        "cache_state": "SOURCE_UNAVAILABLE",
        "records": [], "fci": [], "cauciones": {"ARS": []},
        "section_states": {"caucion:ARS": "SOURCE_UNAVAILABLE_NO_LKG"},
        "fallback_order": preopen.IOL_FALLBACK_ORDER,
        "continuation_state": "CONTINUE_WITH_PROVENANCE_NEVER_ZERO_FILL",
    }), encoding="utf-8")
    monkeypatch.setattr(preopen, "IOL_FAMILY_REFERENCE", path)
    row = preopen.iol_reference_state(TODAY)
    assert row["state"] == "AMBER"
    assert row["reason"] == "CONTINUE_WITH_PPI_BYMA_ALTERNATIVES"
    assert row["never_zero_fill"] is True


def test_iol_source_unavailable_without_exact_fallback_is_red(tmp_path, monkeypatch):
    path = tmp_path / "iol_family_reference_latest.json"
    path.write_text(json.dumps({
        "refreshed_at": "2026-09-25T02:00:00+00:00",
        "cache_state": "SOURCE_UNAVAILABLE", "section_states": {"global": "SOURCE_UNAVAILABLE"},
        "fallback_order": ["IOL_LIVE_BOUNDED_RETRY"],
        "continuation_state": "CONTINUE_WITH_PROVENANCE_NEVER_ZERO_FILL",
    }), encoding="utf-8")
    monkeypatch.setattr(preopen, "IOL_FAMILY_REFERENCE", path)
    assert preopen.iol_reference_state(TODAY)["state"] == "RED"


def test_degraded_watch_is_red(tmp_path, monkeypatch):
    monkeypatch.setattr(preopen,"BYMA_MORNING_WATCH",
                        write(tmp_path,state="DEGRADED",errors=["CALENDAR:TimeoutError"]))
    row=preopen.byma_morning_watch(TODAY)
    assert row["state"]=="RED"
    assert row["reason"]=="BYMA_WATCH_DEGRADED"


def test_morning_watch_timer_is_required_by_preopen():
    assert "porota-byma-morning-watch-rc6.timer" in preopen.REQUIRED_TIMERS
