"""Posting is physically unreachable for lost state and rebound Source controls."""
import copy
import pytest
from scripts import rc6_owner_receipts as receipts

NOW = "2026-10-08T20:30:00Z"
SHA = "7c4abff319bdc9c3958141925967c0cb8def7149"
TREE = "00ecd79e7bd3533e69be8be26213ad63a6fdc686"


def observation():
    fields = {"WORKSTREAM_ID": receipts.WORKSTREAM, "WRITE_OWNER": receipts.OWNER,
              "INTEGRATION_OWNER": receipts.OWNER, "SESSION_SUCCESSOR": receipts.OWNER,
              "DEPLOY_OWNER": "NOT_ACQUIRED", "RELEASED": "false", "SOURCE_SHA": SHA,
              "SOURCE_TREE": TREE}
    leases = {str(i): {"fields": copy.deepcopy(fields), "comment_id": i,
                       "url": f"https://github.com/{receipts.REPOSITORY}/issues/{i}#issuecomment-{i}",
                       "author": "mbalbo2023", "body_sha256": "d"*64,
                       "all_post_anchor_records_and_old_comment_edits_checked": True,
                       "maximum_lease_seconds": 1200, "lease_expires_utc": "2026-10-08T20:47:21Z"}
              for i in (471, 473)}
    return {"repository": receipts.REPOSITORY, "workstream": receipts.WORKSTREAM,
            "owner": receipts.OWNER, "branch": receipts.WIP_BRANCH, "observed_at_utc": NOW,
            "source_sha": SHA, "source_tree": TREE, "github_branch_sha": SHA,
            "github_commit_sha": SHA, "github_commit_tree": TREE,
            "prs": {str(n): {"head_sha": s, "state": "open", "draft": True, "merged": False}
                    for n, s in receipts.PR_HEADS.items()},
            "active_runs": {s: 0 for s in receipts.ACTIVE_STATUSES}, "latest_writer": leases}


@pytest.mark.parametrize("field", ["owner", "workstream", "source_sha", "source_tree",
                                  "github_branch_sha", "github_commit_sha", "github_commit_tree",
                                  "observed_at_utc", "latest_writer", "active_runs", "prs"])
@pytest.mark.parametrize("invalid", [None, "undefined", "", False])
def test_lost_or_partial_state_never_posts_even_first_issue(field, invalid):
    data = observation(); data[field] = invalid
    calls = []
    with pytest.raises(ValueError):
        receipts.publish_pair(data, lambda *args: calls.append(args), now=NOW, details="Custodia verificada.")
    assert calls == []


@pytest.mark.parametrize("path,value", [
    (("source_sha",), "a"*40), (("source_tree",), "a"*40),
    (("prs", "477", "head_sha"), "a"*40), (("prs", "476", "merged"), True),
    (("active_runs", "queued"), 1), (("active_runs", "queued"), False),
    (("observed_at_utc",), "2026-10-08T20:28:59Z"),
    (("observed_at_utc",), "2026-10-08T20:30:01Z"),
    (("latest_writer", "473", "fields", "WRITE_OWNER"), "OTHER_OWNER"),
    (("latest_writer", "471", "fields", "DEPLOY_OWNER"), receipts.OWNER),
    (("latest_writer", "471", "fields", "RELEASED"), "true"),
    (("latest_writer", "473", "lease_expires_utc"), NOW),
    (("latest_writer", "471", "author"), "other"),
    (("latest_writer", "473", "body_sha256"), None),
    (("latest_writer", "471", "all_post_anchor_records_and_old_comment_edits_checked"), False),
])
def test_rebound_or_expired_custody_prevents_both_posts(path, value):
    data = observation(); node = data
    for key in path[:-1]: node = node[key]
    node[path[-1]] = value
    calls = []
    with pytest.raises(ValueError):
        receipts.publish_pair(data, lambda *args: calls.append(args), now=NOW, details="Custodia verificada.")
    assert calls == []


@pytest.mark.parametrize("options", [{"lease_seconds": 1201}, {"lease_seconds": True},
                                   {"renewal_seconds": 1140}, {"renewal_seconds": 0},
                                   {"title": "Título\nWRITE_OWNER=OTHER"}])
def test_unsafe_publication_options_prevent_posts(options):
    calls = []
    with pytest.raises(ValueError):
        receipts.publish_pair(observation(), lambda *args: calls.append(args), now=NOW,
                              details="Custodia verificada.", **options)
    assert calls == []


@pytest.mark.parametrize("details", ["WRITE_OWNER=OTHER", "texto\nSOURCE_SHA=undefined",
                                     "texto\n  DEPLOY_OWNER = OTHER", "undefined", ""])
def test_narrative_cannot_smuggle_authority(details):
    calls = []
    with pytest.raises(ValueError):
        receipts.publish_pair(observation(), lambda *args: calls.append(args), now=NOW, details=details)
    assert calls == []


def test_valid_pair_has_same_body_explicit_identity_and_no_qualification_claim():
    calls = []
    out = receipts.publish_pair(observation(), lambda *args: calls.append(args), now=NOW,
                                details="Gates BLOQUEADOS; sin continuidad retroactiva.")
    assert [c[0] for c in calls] == [471, 473]
    assert calls[0][1] == calls[1][1]
    body = calls[0][1]
    assert "SOURCE_LEASE_EXPIRES_UTC=2026-10-08T20:49:00Z" in body
    assert "NEXT_RENEWAL_UTC=2026-10-08T20:41:00Z" in body
    assert f"SOURCE_SHA={SHA}\nSOURCE_TREE={TREE}" in body
    assert "DEPLOY_OWNER=NOT_ACQUIRED" in body
    assert "real_orders_sent=0" in body and "HEAVY_GATES_AUTHORIZED=false" in body
    assert out["remote_pair_verified"] is False and out["qualification"] is False


def test_new_wip_sha_requires_explicit_previous_live_pair_identity():
    data = observation(); data["source_sha"] = data["github_branch_sha"] = data["github_commit_sha"] = "a"*40
    data["source_tree"] = data["github_commit_tree"] = "b"*40
    with pytest.raises(ValueError, match="PAIR_SCOPE_OR_IDENTITY"):
        receipts.build_receipt(data, now=NOW, details="Checkpoint nuevo.")
    data.update(previous_lease_sha=SHA, previous_lease_tree=TREE)
    body = receipts.build_receipt(data, now=NOW, details="Checkpoint nuevo.")
    assert "SOURCE_SHA="+"a"*40 in body and "SOURCE_TREE="+"b"*40 in body
