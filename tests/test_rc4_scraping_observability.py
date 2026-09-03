from rc4_scraping_observability import run_view, summary


def test_authenticated_run_never_promotes_ready_paper():
    row=run_view({
        "run_id":"r1","job_key":"CONTRACT_EVIDENCE_XHR_DYNAMIC",
        "source_class":"PPI_AUTHENTICATED_XHR","state":"OK","auth_state":"AUTHENTICATED",
        "started_at":"2026-09-03T15:00:00Z","finished_at":"2026-09-03T15:01:00Z",
        "observed":10,"recorded":10,"changed":2,"conflicts":0,"blocked":0,"errors":0,
    }, now="2026-09-03T15:05:00Z", cadence_seconds=900)
    assert row["visual_state"] == "OK"
    assert row["automatic_ready_paper"] is False


def test_auth_problem_is_hold():
    row=run_view({"state":"OK","auth_state":"2FA_REQUIRED","finished_at":"2026-09-03T15:00:00Z"},
                 now="2026-09-03T15:05:00Z", cadence_seconds=900)
    assert row["visual_state"] == "HOLD"
    assert row["cause"] == "AUTH_OR_2FA_BLOCKED"


def test_stale_run_is_visible_as_stale():
    row=run_view({"state":"OK","auth_state":"AUTHENTICATED","finished_at":"2026-09-03T12:00:00Z"},
                 now="2026-09-03T15:00:01Z", cadence_seconds=900)
    assert row["visual_state"] == "STALE"


def test_summary_never_counts_ready_promotions():
    result=summary([{"recorded":10,"changed":2,"blocked":1,"errors":0,"conflicts":1}],
                   [{"status":"CHANGED_REVIEW_REQUIRED"}])
    assert result["ready_paper_promotions"] == 0
    assert result["review_required"] == 1
