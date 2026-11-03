import pytest

import fn_critical_github_proxy_rc6 as mod


class FakeApi:
    def __init__(self):
        self.calls = []
        self.issue = {
            "number": 77,
            "title": "[POROTA][RED] quote freshness",
            "body": "evidence\nHOTFIX_AUTHORIZATION=AWAITING",
            "state": "open",
        }
        self.comments = []

    def __call__(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        if path.endswith("/issues?state=open&per_page=50&sort=created&direction=desc"):
            return [dict(self.issue)]
        if path.endswith("/issues/77"):
            return dict(self.issue)
        if path.endswith("/issues/77/comments?per_page=100"):
            return list(self.comments)
        if method == "POST" and path.endswith("/issues/77/comments"):
            self.comments.append({"body": payload["body"]})
            return {"id": 9001}
        raise AssertionError((method, path, payload))


def broker():
    api = FakeApi()
    return mod.GithubIssueBroker(api), api


def test_capability_key_is_required_and_removed_before_dispatch():
    expected = "a" * 64
    request = {"op": "health", "capability_token": expected}
    mod._require_capability(request, expected)
    assert "capability_token" not in request
    with pytest.raises(mod.BrokerError, match="CAPABILITY_DENIED"):
        mod._require_capability({"op": "health", "capability_token": "b" * 64}, expected)
    with pytest.raises(mod.BrokerError, match="CAPABILITY_DENIED"):
        mod._require_capability({"op": "health"}, expected)


def test_list_is_filtered_to_critical_awaiting():
    b, api = broker()
    rows = b.dispatch({"op": "list_open_critical"})
    assert [r["number"] for r in rows] == [77]
    api.issue["title"] = "ordinary issue"
    assert b.dispatch({"op": "list_open_critical"}) == []


def test_approval_comment_is_constructed_by_broker_not_caller():
    b, api = broker()
    result = b.dispatch({
        "op": "add_authorization_comment",
        "number": 77,
        "approved": True,
        "sender_hash": "0123456789abcdef",
        "body": "ATTACKER CONTROLLED BODY",
    })
    assert result == {"id": 9001}
    method, path, payload = api.calls[-1]
    assert method == "POST"
    assert path.endswith("/issues/77/comments")
    assert "HOTFIX_AUTHORIZATION=AUTHORIZED_TELEGRAM" in payload["body"]
    assert "ATTACKER CONTROLLED BODY" not in payload["body"]
    assert "NO habilita órdenes reales, merge ni deploy" in payload["body"]


def test_reject_comment_uses_rejected_marker():
    b, api = broker()
    b.dispatch({
        "op": "add_authorization_comment",
        "number": 77,
        "approved": False,
        "sender_hash": "0123456789abcdef",
    })
    assert "HOTFIX_AUTHORIZATION=REJECTED_TELEGRAM" in api.comments[-1]["body"]


def test_noncritical_issue_cannot_receive_authorization():
    b, api = broker()
    api.issue["title"] = "ordinary"
    with pytest.raises(mod.BrokerError, match="ISSUE_NOT_CRITICAL"):
        b.dispatch({
            "op": "add_authorization_comment",
            "number": 77,
            "approved": True,
            "sender_hash": "0123456789abcdef",
        })


def test_closed_issue_cannot_receive_authorization():
    b, api = broker()
    api.issue["state"] = "closed"
    with pytest.raises(mod.BrokerError, match="ISSUE_NOT_AWAITING"):
        b.dispatch({
            "op": "add_authorization_comment",
            "number": 77,
            "approved": True,
            "sender_hash": "0123456789abcdef",
        })


def test_existing_terminal_decision_is_idempotent():
    b, api = broker()
    api.comments = [{"body": "HOTFIX_AUTHORIZATION=AUTHORIZED_TELEGRAM"}]
    with pytest.raises(mod.BrokerError, match="ISSUE_ALREADY_DECIDED"):
        b.dispatch({
            "op": "add_authorization_comment",
            "number": 77,
            "approved": True,
            "sender_hash": "0123456789abcdef",
        })


def test_arbitrary_operation_is_rejected():
    b, api = broker()
    with pytest.raises(mod.BrokerError, match="OPERATION_NOT_ALLOWED"):
        b.dispatch({"op": "delete_repo"})
    assert api.calls == []


def test_invalid_sender_hash_is_rejected_before_write():
    b, api = broker()
    with pytest.raises(mod.BrokerError, match="INVALID_SENDER_HASH"):
        b.dispatch({
            "op": "add_authorization_comment",
            "number": 77,
            "approved": True,
            "sender_hash": "not-valid",
        })
    assert not any(c[0] == "POST" for c in api.calls)
