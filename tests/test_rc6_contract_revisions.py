"""Economic owner-decision adversaries; fixtures never qualify native gates."""
import copy
from datetime import datetime, timedelta, timezone

import pytest

from scripts import rc6_contract_revisions as revisions
from scripts.rc6_actions_custody import digest

NOW = datetime(2026, 10, 10, 18, 0, tzinfo=timezone.utc)
BINDING = {"revision": "PRIVATE_PID_BOUNDARY_V2", "scope": "capacity-probe",
    "source_sha": "a" * 40, "source_tree": "b" * 40, "contract_sha256": "c" * 64}
URL = "https://github.com/mbalbo2023/Porota-trading/issues/471#issuecomment-15"


class DecisionFixture:
    def __init__(self, monkeypatch, **changes):
        fields = {"CONTRACT_ACCEPTANCE_SCHEMA": revisions.SCHEMA,
            "RC6_CONTRACT_REVISION_AUTHORIZATION": "APPROVED",
            "CONTRACT_DECISION_ORIGIN": "EXPLICIT_OWNER_DECISION",
            "CONTRACT_REVISION": BINDING["revision"], "CONTRACT_SCOPE": BINDING["scope"],
            "CONTRACT_DOCUMENT_SHA256": BINDING["contract_sha256"],
            "CONTRACT_GATES_ALREADY_GREEN": "false", "NATIVE_EQUIVALENCE_FROM_APPROVAL_CLAIMED": "false",
            "FINAL_CANDIDATE_ELIGIBLE": "false", "DEPLOY_AUTHORIZATION": "NOT_GRANTED",
            "SOURCE_LEASE_EXPIRES_UTC": (NOW + timedelta(minutes=10)).isoformat()}
        fields.update(changes)
        body = "\n".join(key + "=" + value for key, value in fields.items()) + "\n"
        self.twin = {"id": 16, "body": body, "created_at": NOW.isoformat(), "updated_at": NOW.isoformat()}
        self.decision = {**self.twin, "id": 15, "body": body + "OWNER_RECEIPT_473=16\n"
            + "OWNER_RECEIPT_473_SHA256=" + digest(body.encode()) + "\n"}
        self.calls = []

        def observed_owner(github, url, **kwargs):
            self.calls.append((url, kwargs))
            # This fixture represents the independent existing ownership
            # verifier. Native observations are deliberately absent.
            return digest(self.decision["body"].encode())

        monkeypatch.setattr(revisions, "verify_owner", observed_owner)

    def request(self, path):
        return copy.deepcopy({"/issues/comments/15": self.decision,
                              "/issues/comments/16": self.twin}[path])

    def issue(self):
        return revisions.authenticate_revision(self, URL, owner_session="UNIT_DECISION_ONLY",
                                               now=NOW, **BINDING)


def test_explicit_owner_decision_is_immutable_scoped_and_never_a_native_pass(monkeypatch):
    fixture = DecisionFixture(monkeypatch)
    context = fixture.issue()
    assert revisions.validate_revision_approval(context, now=NOW, **BINDING) is context
    assert fixture.calls == [(URL, {"sha": BINDING["source_sha"], "tree": BINDING["source_tree"],
        "owner": "UNIT_DECISION_ONLY", "now": NOW, "plan_sha256": BINDING["contract_sha256"],
        "authorization_field": "RC6_CONTRACT_REVISION_AUTHORIZATION"})]
    assert not hasattr(context, "native_equivalence_proved")
    assert not hasattr(context, "G0_green")
    with pytest.raises(AttributeError, match="IMMUTABLE"):
        context.scope = "capacity-calibration"


@pytest.mark.parametrize("changes", [
    {"RC6_CONTRACT_REVISION_AUTHORIZATION": "NOT_GRANTED"},
    {"CONTRACT_DECISION_ORIGIN": "AUTOMATIC_CI"},
    {"CONTRACT_GATES_ALREADY_GREEN": "true"},
    {"NATIVE_EQUIVALENCE_FROM_APPROVAL_CLAIMED": "true"},
    {"CONTRACT_SCOPE": "capacity-calibration"},
    {"CONTRACT_DOCUMENT_SHA256": "d" * 64},
    {"DEPLOY_AUTHORIZATION": "APPROVED"},
    {"FINAL_CANDIDATE_ELIGIBLE": "true"},
])
def test_automatic_rebound_or_green_decisions_cannot_issue_acceptance(monkeypatch, changes):
    with pytest.raises(ValueError, match="EXPLICIT_PAIRED"):
        DecisionFixture(monkeypatch, **changes).issue()


@pytest.mark.parametrize("field,value", [
    ("source_sha", "d" * 40), ("source_tree", "e" * 40),
    ("scope", "capacity-calibration"), ("contract_sha256", "f" * 64),
])
def test_live_acceptance_cannot_be_borrowed_by_another_source_or_scope(monkeypatch, field, value):
    context = DecisionFixture(monkeypatch).issue()
    with pytest.raises(ValueError, match="BINDING_REBOUND"):
        revisions.validate_revision_approval(context, now=NOW, **{**BINDING, field: value})


@pytest.mark.parametrize("value", [None, True, {}, BINDING])
def test_json_and_flags_are_never_owner_authority(value):
    with pytest.raises(ValueError, match="AUTHENTICATED_OWNER_ACCEPTANCE"):
        revisions.validate_revision_approval(value, now=NOW, **BINDING)


def test_direct_constructor_and_forged_object_never_issue_authority():
    with pytest.raises(ValueError, match="FACTORY"):
        revisions.AuthenticatedRevision()
    forged = object.__new__(revisions.AuthenticatedRevision)
    with pytest.raises(ValueError, match="AUTHENTICATED_OWNER_ACCEPTANCE"):
        revisions.validate_revision_approval(forged, now=NOW, **BINDING)


def test_expired_approval_cannot_start_later_stage(monkeypatch):
    context = DecisionFixture(monkeypatch).issue()
    with pytest.raises(ValueError, match="EXPIRED"):
        revisions.validate_revision_approval(context, now=NOW + timedelta(minutes=11), **BINDING)


def test_duplicate_field_and_changed_paired_decision_fail_closed(monkeypatch):
    fixture = DecisionFixture(monkeypatch)
    fixture.decision["body"] += "CONTRACT_SCOPE=capacity-probe\n"
    with pytest.raises(ValueError, match="DUPLICATE"):
        fixture.issue()
    fixture = DecisionFixture(monkeypatch)
    fixture.twin["updated_at"] = (NOW + timedelta(seconds=1)).isoformat()
    with pytest.raises(ValueError, match="PAIR_CHANGED"):
        fixture.issue()


def test_owner_custody_failure_precedes_contract_payload(monkeypatch):
    fixture = DecisionFixture(monkeypatch)
    def reject(*args, **kwargs):
        raise ValueError("CUSTODY_SUPERSEDED_OWNER")
    monkeypatch.setattr(revisions, "verify_owner", reject)
    with pytest.raises(ValueError, match="SUPERSEDED_OWNER"):
        fixture.issue()
