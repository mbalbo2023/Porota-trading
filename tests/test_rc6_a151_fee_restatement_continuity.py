"""A151: a failed Deploy may carry six sealed PAPER fees to a new SHA once."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from scripts.rc6_caucion_fee_restatement_apply import (
    apply_restatement, init_restated_schema, preflight_restatement,
    read_verified_restated_fees,
)
from scripts.rc6_caucion_fee_restatement_continuity import authorize, probe
from test_rc6_a151_fee_consumer_projection import six_positions, CANDIDATE, KNOWN_AT, ORIGIN


NEXT = "b" * 40
REPO = "mbalbo2023/Porota-trading"
WORKFLOW = ".github/workflows/porota-deploy-v2-promote.yml"
OPEN_ORIGIN = dict(ORIGIN, quiescence_mode="OPEN", owner_intraday_comment_id=1003)
HELD_ORIGIN = dict(OPEN_ORIGIN, quiescence_mode="A151_HELD_OPEN")
STOPPED_ORIGIN = dict(OPEN_ORIGIN, quiescence_mode="A151_HELD_STOPPED")


def failed_receipt(tmp_path, *, market_open=False, held_a143=False,
                   stopped_a169=False):
    folder = tmp_path / "deploy"
    folder.mkdir()
    payload = {"candidate_sha": CANDIDATE, "status": "FIX_FORWARD_VALIDATION_REQUIRED",
               "installation_started": True, "real_orders_sent": 0,
               "ppi_watch_accessed": False, "market_open_suspended": market_open,
               "recorded_at": "2026-10-28T13:40:00+00:00"}
    if held_a143:
        lifecycle = {"Status": "running", "StartedAt": "2026-10-28T12:59:00Z",
                     "FinishedAt": "0001-01-01T00:00:00Z", "ExitCode": 0,
                     "OOMKilled": False}
        after = dict(lifecycle, Status="exited", ExitCode=0,
                     FinishedAt="2026-10-28T13:30:00Z")
        from scripts.rc6_caucion_fee_restatement_continuity import (
            A143_SOURCE_SHA, A143_TREE_SHA, A143_IMAGE_ID,
        )
        containers = {name: {"id": str(n) * 64, "image": A143_IMAGE_ID,
                             "lifecycle": lifecycle}
                      for n, name in enumerate(("porota_production_observer",
                                               "porota_production_dashboard"), start=1)}
        payload.update(market_open_suspended=False, a143_pending_adopted=True,
                       open_paper_carryover=False, paused_postclose=False,
                       intraday_caucion=False, pending_caucion=False,
                       spot_carryover=None, caucion_carryover=None,
                       containers={name: {"id": row["id"], "image": row["image"]}
                                   for name, row in containers.items()},
                       stop_attempted=list(containers),
                       stop_evidence={name: {"before": lifecycle, "after": after,
                                             "classification": "CLEAN_EXIT"}
                                      for name in containers},
                       a143_previous_proof={
                           "source_sha": A143_SOURCE_SHA, "candidate_tree_sha": A143_TREE_SHA,
                           "image_id": A143_IMAGE_ID,
                           "marker_sha256": "a" * 64, "frozen_sha256": "b" * 64,
                           "prior_receipt_sha256": "c" * 64,
                           "flat_book": {"process_state": "DEGRADED",
                                         "session_state": "BOOT_CALENDAR",
                                         "source_sha": A143_SOURCE_SHA,
                                         "real_orders_sent": 0},
                           "containers": containers, "critical_admission": {"paper": 0},
                           "close": None,
                           "operator_hold": {
                               "admission": "A151_HELD_OPEN",
                               "business_day_art": "2026-10-28",
                               "observed_at_utc": "2026-10-28T13:10:00+00:00",
                               "operator_receipt_sha256": "d" * 64,
                               "operator_operation_id": "A151-40686116017-1",
                               "paper_execution_authorized": False}})
    if stopped_a169:
        from scripts.rc6_caucion_fee_restatement_continuity import (
            A169_STOPPED_CANDIDATE, A169_STOPPED_RECEIPT_SHA256,
        )
        payload.update(a169_origin_candidate=A169_STOPPED_CANDIDATE,
                       a169_origin_sha256=A169_STOPPED_RECEIPT_SHA256)
    (folder / ("quiescence-" + CANDIDATE + ".json")).write_text(json.dumps(payload))
    return folder


def github_get(*, conclusion="failure", owner="mbalbo2023", tree="c" * 40,
               attempt=1, literal=True, commit_tree="c" * 40,
               branch="deploy/rc6-pr69-isolated-20260915", issue=624,
               origin=ORIGIN, intraday_literal=True, intraday_created_at="2026-10-28T05:00:00Z"):
    body = lambda decision: (f"{decision} candidate={CANDIDATE} tree={tree}"
                             if literal else "DIFFERENT_OWNER_DECISION")
    rows = {
        f"repos/{REPO}/actions/runs/{origin['run_id']}": {
            "id": int(origin["run_id"]), "name": "RC6 Deploy V2 promote frozen candidate",
            "path": WORKFLOW, "event": "push", "head_branch": branch,
            "status": "completed",
            "conclusion": conclusion, "head_sha": origin["deploy_sha"],
            "run_attempt": attempt, "run_started_at": "2026-10-28T13:00:00Z",
            "updated_at": "2026-10-28T14:00:00Z"},
        f"repos/{REPO}/commits/{origin['deploy_sha']}": {
            "sha": origin["deploy_sha"], "parents": [
                {"sha": "e" * 40}, {"sha": CANDIDATE}],
            "commit": {"tree": {"sha": commit_tree}}},
        f"repos/{REPO}/commits/{CANDIDATE}": {
            "sha": CANDIDATE, "commit": {"tree": {"sha": origin["candidate_tree"]}}},
    }
    decisions = [("owner_approval_comment_id", "RC6_624_APPROVE_DEPLOY_AND_COLD2"),
                 ("owner_deferred_comment_id", "RC6_624_APPROVE_DEPLOY_WITH_HOST_STRESS_DEFERRED")]
    if origin["quiescence_mode"] in ("OPEN", "A151_HELD_OPEN", "A151_HELD_STOPPED"):
        decisions.append(("owner_intraday_comment_id", "RC6_624_APPROVE_INTRADAY_SUSPEND_DEPLOY"))
    for field, decision in decisions:
        comment_body = body(decision)
        if field == "owner_intraday_comment_id" and not intraday_literal:
            comment_body = "RC6_624_APPROVE_SOME_OTHER_DECISION"
        rows[f"repos/{REPO}/issues/comments/{origin[field]}"] = {
            "id": origin[field], "user": {"login": owner}, "body": comment_body,
            "issue_url": f"https://api.github.com/repos/{REPO}/issues/{issue}",
            "created_at": (intraday_created_at if field == "owner_intraday_comment_id"
                           else "2026-10-28T05:00:00Z"),
            "updated_at": (intraday_created_at if field == "owner_intraday_comment_id"
                           else "2026-10-28T05:00:00Z")}
    return rows.__getitem__


def _sealed(db):
    with sqlite3.connect(db) as conn:
        return ([tuple(r) for r in conn.execute(
            "SELECT * FROM paper_caucion_fee_restatements ORDER BY paper_id")],
            [tuple(r) for r in conn.execute(
                "SELECT * FROM paper_caucion_fee_restatement_origin")],
            [tuple(r) for r in conn.execute(
                "SELECT paper_id,total_fees FROM paper_cauciones ORDER BY paper_id")])


def test_atomic_origin_exact_failed_deploy_reuses_without_second_money(six_positions, tmp_path):
    assert probe(six_positions, NEXT, tmp_path)["status"] == "FRESH"
    with pytest.raises(ValueError, match="PAPER_RESTATEMENT_DEPLOY_ORIGIN_REQUIRED"):
        apply_restatement(six_positions, CANDIDATE, now=KNOWN_AT)
    with sqlite3.connect(six_positions) as db:
        assert db.execute("SELECT 1 FROM sqlite_master WHERE name='paper_caucion_fee_restatements'").fetchone() is None
        assert db.execute("SELECT 1 FROM sqlite_master WHERE name='paper_caucion_fee_restatement_origin'").fetchone() is None
    applied = apply_restatement(six_positions, CANDIDATE, now=KNOWN_AT, origin=ORIGIN)
    assert applied["inserted"] == 6
    before = _sealed(six_positions)
    folder = failed_receipt(tmp_path)
    p = probe(six_positions, NEXT, folder)
    proof = authorize(p, NEXT, REPO, github_get())
    assert proof["prior_origin_seal_sha256"] == p["origin"]["seal_sha256"]
    assert preflight_restatement(six_positions, NEXT, now=KNOWN_AT,
                                  continuity_proof=proof, receipt_dir=folder)["continuity_adopted"]
    adopted = apply_restatement(six_positions, NEXT, now=KNOWN_AT,
                                continuity_proof=proof, receipt_dir=folder)
    assert adopted["inserted"] == 0 and adopted["continuity_adopted"]
    assert adopted["origin_candidate_sha"] == CANDIDATE
    assert _sealed(six_positions) == before
    with sqlite3.connect(six_positions) as db:
        db.row_factory = sqlite3.Row
        assert len(read_verified_restated_fees(db)) == 6
        with pytest.raises(sqlite3.IntegrityError, match="APPEND_ONLY"):
            db.execute("UPDATE paper_caucion_fee_restatement_origin SET candidate_sha=?", (NEXT,))
        with pytest.raises(sqlite3.IntegrityError, match="APPEND_ONLY"):
            db.execute("DELETE FROM paper_caucion_fee_restatement_origin")


def test_mid_insert_failure_rolls_back_all_six_and_origin(six_positions):
    with sqlite3.connect(six_positions) as db:
        init_restated_schema(db)
        db.execute("""CREATE TRIGGER fail_third_a151 BEFORE INSERT ON paper_caucion_fee_restatements
            WHEN NEW.paper_id='PAPER-CAUCION-3' BEGIN SELECT RAISE(ABORT,'SIMULATED_FAILURE'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="SIMULATED_FAILURE"):
        apply_restatement(six_positions, CANDIDATE, now=KNOWN_AT, origin=ORIGIN)
    with sqlite3.connect(six_positions) as db:
        assert db.execute("SELECT COUNT(*) FROM paper_caucion_fee_restatements").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM paper_caucion_fee_restatement_origin").fetchone()[0] == 0
        db.execute("DROP TRIGGER fail_third_a151")
    assert apply_restatement(six_positions, CANDIDATE, now=KNOWN_AT,
                             origin=ORIGIN)["inserted"] == 6


@pytest.mark.parametrize("change,reason", [
    ({"prior_run_conclusion": "success"}, "PRIOR_FAILED_DEPLOY_PROOF_RED"),
    ({"prior_receipt_sha256": "0" * 64}, "HOST_RECEIPT_CHANGED"),
    ({"new_candidate_sha": "f" * 40}, "PRIOR_FAILED_DEPLOY_PROOF_RED"),
    ({"prior_origin_seal_sha256": "0" * 64}, "PRIOR_FAILED_DEPLOY_PROOF_RED"),
])
def test_false_or_changed_continuity_proof_fails_without_mutation(
        six_positions, tmp_path, change, reason):
    apply_restatement(six_positions, CANDIDATE, now=KNOWN_AT, origin=ORIGIN)
    folder = failed_receipt(tmp_path)
    proof = authorize(probe(six_positions, NEXT, folder), NEXT, REPO, github_get())
    altered = dict(proof, **change)
    before = _sealed(six_positions)
    with pytest.raises(ValueError, match=reason):
        preflight_restatement(six_positions, NEXT, continuity_proof=altered, receipt_dir=folder)
    with pytest.raises(ValueError, match=reason):
        apply_restatement(six_positions, NEXT, continuity_proof=altered, receipt_dir=folder)
    assert _sealed(six_positions) == before


@pytest.mark.parametrize("kwargs,reason", [
    ({"conclusion": "success"}, "PRIOR_GITHUB_DEPLOY_NOT_FAILED"),
    ({"attempt": 2}, "PRIOR_GITHUB_DEPLOY_NOT_FAILED"),
    ({"owner": "stranger"}, "PRIOR_OWNER_AUTHORIZATION_RED"),
    ({"literal": False}, "PRIOR_OWNER_AUTHORIZATION_RED"),
    ({"tree": "e" * 40}, "PRIOR_OWNER_AUTHORIZATION_RED"),
    ({"commit_tree": "e" * 40}, "PRIOR_DEPLOY_TREE_RED"),
    ({"branch": "not-the-production-deploy"}, "PRIOR_GITHUB_DEPLOY_NOT_FAILED"),
    ({"issue": 679}, "PRIOR_OWNER_AUTHORIZATION_RED"),
])
def test_runner_refuses_nonfailed_or_wrong_owner_origin(six_positions, tmp_path, kwargs, reason):
    apply_restatement(six_positions, CANDIDATE, now=KNOWN_AT, origin=ORIGIN)
    folder = failed_receipt(tmp_path)
    p = probe(six_positions, NEXT, folder)
    with pytest.raises(ValueError, match=reason):
        authorize(p, NEXT, REPO, github_get(**kwargs))


def test_partial_tampered_or_changed_host_receipt_does_not_adopt(six_positions, tmp_path):
    apply_restatement(six_positions, CANDIDATE, now=KNOWN_AT, origin=ORIGIN)
    folder = failed_receipt(tmp_path)
    proof = authorize(probe(six_positions, NEXT, folder), NEXT, REPO, github_get())
    prior_path = folder / ("quiescence-" + CANDIDATE + ".json")
    prior_path.write_text(prior_path.read_text() + " ")
    with pytest.raises(ValueError, match="HOST_RECEIPT_CHANGED"):
        preflight_restatement(six_positions, NEXT, continuity_proof=proof, receipt_dir=folder)
    with pytest.raises(ValueError, match="HOST_RECEIPT_CHANGED"):
        apply_restatement(six_positions, NEXT, continuity_proof=proof, receipt_dir=folder)
    with sqlite3.connect(six_positions) as db:
        db.execute("DROP TRIGGER paper_caucion_fee_restatements_no_delete")
        db.execute("DELETE FROM paper_caucion_fee_restatements WHERE paper_id="
                   "(SELECT paper_id FROM paper_caucion_fee_restatements LIMIT 1)")
    with pytest.raises(ValueError, match="PROBE_COUNT_RED"):
        probe(six_positions, NEXT, folder)
    with pytest.raises(ValueError, match="CAUCION_RESTATEMENT_INVALID"):
        preflight_restatement(six_positions, NEXT, continuity_proof=proof, receipt_dir=folder)


def test_changed_financial_snapshot_blocks_continuity_without_rebooking(six_positions, tmp_path):
    apply_restatement(six_positions, CANDIDATE, now=KNOWN_AT, origin=ORIGIN)
    folder = failed_receipt(tmp_path)
    proof = authorize(probe(six_positions, NEXT, folder), NEXT, REPO, github_get())
    with sqlite3.connect(six_positions) as db:
        db.execute("UPDATE paper_cauciones SET total_fees='0.19' WHERE paper_id='PAPER-CAUCION-0'")
    before = _sealed(six_positions)
    with pytest.raises(ValueError, match="HISTORICAL_REPLAY_RED"):
        preflight_restatement(six_positions, NEXT, continuity_proof=proof, receipt_dir=folder)
    with pytest.raises(ValueError, match="HISTORICAL_REPLAY_RED"):
        apply_restatement(six_positions, NEXT, continuity_proof=proof, receipt_dir=folder)
    assert _sealed(six_positions) == before


def test_same_candidate_replay_needs_no_failed_prior_proof(six_positions, tmp_path):
    apply_restatement(six_positions, CANDIDATE, now=KNOWN_AT, origin=ORIGIN)
    assert authorize(probe(six_positions, CANDIDATE, tmp_path), CANDIDATE,
                     REPO, lambda _: (_ for _ in ()).throw(AssertionError("GH not needed"))) is None
    assert apply_restatement(six_positions, CANDIDATE)["inserted"] == 0
    assert preflight_restatement(six_positions, CANDIDATE)["previously_applied"] is True


@pytest.mark.parametrize("origin", [OPEN_ORIGIN, HELD_ORIGIN, STOPPED_ORIGIN])
def test_open_origin_needs_exact_third_literal_before_run_and_keeps_cash_once(
        six_positions, tmp_path, origin):
    assert apply_restatement(six_positions, CANDIDATE, now=KNOWN_AT,
                             origin=origin)["inserted"] == 6
    before = _sealed(six_positions)
    folder = failed_receipt(tmp_path, market_open=origin["quiescence_mode"] == "OPEN",
                            held_a143=origin["quiescence_mode"] in {"A151_HELD_OPEN", "A151_HELD_STOPPED"},
                            stopped_a169=origin["quiescence_mode"] == "A151_HELD_STOPPED")
    p = probe(six_positions, NEXT, folder)
    proof = authorize(p, NEXT, REPO, github_get(origin=origin))
    assert preflight_restatement(six_positions, NEXT, continuity_proof=proof,
                                  receipt_dir=folder)["continuity_adopted"]
    assert apply_restatement(six_positions, NEXT, continuity_proof=proof,
                             receipt_dir=folder)["inserted"] == 0
    assert _sealed(six_positions) == before
    if origin["quiescence_mode"] == "A151_HELD_STOPPED":
        path = folder / ("quiescence-" + CANDIDATE + ".json")
        changed = json.loads(path.read_text())
        changed["a169_origin_sha256"] = "0" * 64
        path.write_text(json.dumps(changed))
        with pytest.raises(ValueError, match="A169_STOPPED_ORIGIN_RED"):
            probe(six_positions, NEXT, folder)
    with pytest.raises(ValueError, match="PRIOR_OWNER_AUTHORIZATION_RED"):
        authorize(p, NEXT, REPO, github_get(origin=origin, intraday_literal=False))
    with pytest.raises(ValueError, match="PRIOR_OWNER_AUTHORIZATION_RED"):
        authorize(p, NEXT, REPO, github_get(origin=origin,
                  intraday_created_at="2026-10-28T13:30:00Z"))


@pytest.mark.parametrize("origin,reason", [
    (dict(OPEN_ORIGIN, owner_intraday_comment_id=None), "INTRADAY_OWNER_REQUIRED"),
    (dict(HELD_ORIGIN, owner_intraday_comment_id=1001), "INTRADAY_OWNER_REQUIRED"),
    (dict(STOPPED_ORIGIN, owner_intraday_comment_id=None), "INTRADAY_OWNER_REQUIRED"),
    (dict(ORIGIN, owner_intraday_comment_id=1003), "UNEXPECTED_INTRADAY_OWNER"),
])
def test_inconsistent_intraday_origin_rolls_back_without_six_receipts(
        six_positions, origin, reason):
    with pytest.raises(ValueError, match=reason):
        apply_restatement(six_positions, CANDIDATE, now=KNOWN_AT, origin=origin)
    with sqlite3.connect(six_positions) as db:
        assert db.execute("SELECT 1 FROM sqlite_master WHERE name='paper_caucion_fee_restatements'").fetchone() is None
        assert db.execute("SELECT 1 FROM sqlite_master WHERE name='paper_caucion_fee_restatement_origin'").fetchone() is None


def test_open_origin_rejects_forged_third_id_or_closed_host_receipt(six_positions, tmp_path):
    apply_restatement(six_positions, CANDIDATE, now=KNOWN_AT, origin=OPEN_ORIGIN)
    folder = failed_receipt(tmp_path, market_open=True)
    p = probe(six_positions, NEXT, folder)
    forged = dict(p, origin=dict(p["origin"], owner_intraday_comment_id=None))
    with pytest.raises(ValueError, match="PRIOR_INTRADAY_OWNER_RED"):
        authorize(forged, NEXT, REPO, github_get(origin=OPEN_ORIGIN))
    path = folder / ("quiescence-" + CANDIDATE + ".json")
    receipt = json.loads(path.read_text())
    receipt["market_open_suspended"] = False
    path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match="HOST_RECEIPT_RED"):
        probe(six_positions, NEXT, folder)


@pytest.mark.parametrize("field,value,reason", [
    (("market_open_suspended",), True, "HOST_RECEIPT_RED"),
    (("a143_pending_adopted",), False, "A151_HELD_PROOF_RED"),
    (("a143_previous_proof", "operator_hold"), None, "A151_HELD_PROOF_RED"),
    (("a143_previous_proof", "operator_hold", "admission"), "NOT_HELD", "A151_HELD_PROOF_RED"),
    (("a143_previous_proof", "operator_hold", "paper_execution_authorized"), True, "A151_HELD_PROOF_RED"),
    (("a143_previous_proof", "source_sha"), "f" * 40, "A151_HELD_PROOF_RED"),
    (("a143_previous_proof", "image_id"), "sha256:" + "f" * 64, "A151_HELD_PROOF_RED"),
    (("a143_previous_proof", "flat_book", "session_state"), "MARKET_OPEN", "A151_HELD_PROOF_RED"),
    (("a143_previous_proof", "close"), {"green": True}, "A151_HELD_PROOF_RED"),
    (("a143_previous_proof", "containers", "porota_production_observer", "id"), "f" * 64, "A151_HELD_IDENTITY_RED"),
    (("stop_evidence", "porota_production_dashboard", "after", "ExitCode"), 1, "A151_HELD_IDENTITY_RED"),
])
def test_held_open_receipt_tampering_fails_probe_and_apply(
        six_positions, tmp_path, field, value, reason):
    apply_restatement(six_positions, CANDIDATE, now=KNOWN_AT, origin=HELD_ORIGIN)
    folder = failed_receipt(tmp_path, held_a143=True)
    good = probe(six_positions, NEXT, folder)
    proof = authorize(good, NEXT, REPO, github_get(origin=HELD_ORIGIN))
    assert preflight_restatement(six_positions, NEXT, continuity_proof=proof,
                                  receipt_dir=folder)["continuity_adopted"]
    before = _sealed(six_positions)
    path = folder / ("quiescence-" + CANDIDATE + ".json")
    tampered = deepcopy(json.loads(path.read_text()))
    target = tampered
    for key in field[:-1]:
        target = target[key]
    target[field[-1]] = value
    path.write_text(json.dumps(tampered))
    with pytest.raises(ValueError, match=reason):
        probe(six_positions, NEXT, folder)
    forged = dict(proof, prior_receipt_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    with pytest.raises(ValueError, match=reason):
        preflight_restatement(six_positions, NEXT, continuity_proof=forged,
                              receipt_dir=folder)
    with pytest.raises(ValueError, match=reason):
        apply_restatement(six_positions, NEXT, continuity_proof=forged,
                          receipt_dir=folder)
    assert _sealed(six_positions) == before


def test_missing_or_tampered_atomic_origin_fails_economic_reader(six_positions, tmp_path):
    apply_restatement(six_positions, CANDIDATE, now=KNOWN_AT, origin=ORIGIN)
    with sqlite3.connect(six_positions) as db:
        db.execute("DROP TRIGGER paper_caucion_fee_restatement_origin_no_update")
        db.execute("UPDATE paper_caucion_fee_restatement_origin SET run_id='999'")
        db.row_factory = sqlite3.Row
        with pytest.raises(ValueError, match="CAUCION_RESTATEMENT_INVALID.*ORIGIN_TAMPERED"):
            read_verified_restated_fees(db)
    with pytest.raises(ValueError, match="PROBE_SEAL_RED"):
        probe(six_positions, NEXT, tmp_path)


def test_workflow_checks_prior_owner_before_suspend_and_rechecks_before_restart():
    source = (Path(__file__).resolve().parents[1] /
              ".github/workflows/porota-deploy-v2-promote.yml").read_text()
    assert source.index("name: Require later exact owner authorization") < source.index(
        "id: restatement_continuity") < source.index(
            "id: suspend") < source.index("RC6_A151_CAUCION_FEE_PREFLIGHT")
    assert source.index("RC6_A151_CAUCION_FEE_PREFLIGHT") < source.index(
        "RC6_A151_CAUCION_FEE_APPLY") < source.index("MODE_OUTPUT=")
    assert source.count('--continuity-proof-b64 "$RC6_A151_CONTINUITY_PROOF_B64"') == 2
    assert "RC6_A151_OWNER_APPROVAL_ID='$RC6_A151_OWNER_APPROVAL_ID'" in source
    assert "RC6_A151_OWNER_DEFERRED_ID='$RC6_A151_OWNER_DEFERRED_ID'" in source
