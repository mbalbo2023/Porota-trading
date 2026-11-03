"""A248: permanent operator intent never replaces release/instrument safety.

These assertions make the operator-approved separation explicit in the
canonical policy.  Runtime implementation is exercised in the admission,
preopen, observer, and operator-intent suites separately.
"""

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_operator_intent_has_no_automatic_lease_or_release_revocation():
    policy = yaml.safe_load(
        (ROOT / "ops/policy/porota-policy.yaml").read_text(encoding="utf-8")
    )
    intent = policy["paper_admission_governance"]
    assert intent["operator_paper_intent_persists_until_explicit_pause"] is True
    assert intent["operator_paper_intent_arbitrary_ttl_allowed"] is False
    assert intent["operator_paper_intent_invalidated_by_new_code_sha"] is False
    assert intent["exact_release_safety_reverification_required"] is True
    assert intent["exact_market_and_instrument_admission_required"] is True
    assert intent["live_paper_entry_requires_genuine_session_and_fresh_data"] is True
    assert intent["real_order_routes_must_remain_blocked"] is True


def test_paper_families_do_not_share_arbitrary_watchlist_veto():
    policy = yaml.safe_load(
        (ROOT / "ops/policy/porota-policy.yaml").read_text(encoding="utf-8")
    )
    admission = policy["paper_admission_governance"]
    assert admission["hardcoded_ticker_watchlist_as_global_admission_veto_allowed"] is False
    assert admission["unrelated_family_source_failure_as_global_admission_veto_allowed"] is False
    assert admission["offline_historical_and_synthetic_tests_24x7_allowed"] is True
    assert policy["operating_mode"]["required_mode"] == "PRODUCTION_PAPER"
    assert policy["operating_mode"]["real_trading_allowed"] is False
    assert policy["operating_mode"]["real_orders_sent_required"] == 0


def test_autonomous_development_does_not_bypass_exact_release_controls():
    policy = yaml.safe_load(
        (ROOT / "ops/policy/porota-policy.yaml").read_text(encoding="utf-8")
    )
    collaboration = policy["operator_collaboration"]
    assert collaboration["arbitrary_new_business_controls_allowed"] is False
    assert collaboration["decisions_require_reason_and_evidence"] is True
    assert collaboration["consultations_must_offer_options_and_recommendation"] is True
    assert collaboration["do_not_repeat_generic_approval_when_autonomous_scope_is_granted"] is True
    assert collaboration["exact_release_deploy_authorization_controls_still_apply"] is True
    assert policy["deployment"]["single_consolidated_deploy_required"] is True
    assert policy["deployment"]["exact_sha_required"] is True
    assert policy["deployment"]["rollback_allowed"] is False
