import sqlite3

import pytest

import fg_critical_approval_gateway_rc6 as mod


class FakeGithub:
    def __init__(self):
        self.issue = {
            "number": 77,
            "title": "[POROTA][RED] PPI quote freshness",
            "body": "evidence\nHOTFIX_AUTHORIZATION=AWAITING",
            "state": "open",
        }
        self.state = "AWAITING"
        self.comments_added = []

    def list_open_critical(self):
        return [self.issue]

    def authorization_state(self, number):
        assert number == 77
        return self.state

    def get_issue(self, number):
        assert number == 77
        return dict(self.issue)

    def add_authorization_comment(self, number, approved, sender_hash):
        assert number == 77
        self.comments_added.append((approved, sender_hash))
        self.state = "APPROVED" if approved else "REJECTED"
        return {"id": 9001}


class FakeTelegram:
    def __init__(self):
        self.chat_id = "12345"
        self.incidents = []
        self.answers = []
        self.messages = []
        self._updates = []

    def send_incident(self, issue):
        self.incidents.append(issue["number"])
        return {"message_id": 1}

    def answer_callback(self, callback_id, text):
        self.answers.append((callback_id, text))

    def send_text(self, text):
        self.messages.append(text)

    def updates(self, offset):
        return list(self._updates)


def gateway(tmp_path):
    gh = FakeGithub()
    tg = FakeTelegram()
    store = mod.ApprovalStore(str(tmp_path / "approval.db"))
    return mod.CriticalApprovalGateway(gh, tg, store), gh, tg, store


def callback(data="CHFOK:77", sender="12345", callback_id="cb1"):
    return {
        "update_id": 8,
        "callback_query": {
            "id": callback_id,
            "data": data,
            "from": {"id": sender},
        },
    }


def test_scan_sends_each_pending_incident_once(tmp_path):
    gw, gh, tg, store = gateway(tmp_path)
    assert gw.scan_once() == 1
    assert tg.incidents == [77]
    assert gw.scan_once() == 0
    assert tg.incidents == [77]
    assert store.was_notified(77)


def test_approve_records_github_marker_but_does_not_execute_anything(tmp_path):
    gw, gh, tg, store = gateway(tmp_path)
    result = gw.handle_callback(callback())
    assert result == "APPROVED"
    assert len(gh.comments_added) == 1
    approved, actor_hash = gh.comments_added[0]
    assert approved is True
    assert len(actor_hash) == 16
    assert "NO se autorizó merge, deploy ni órdenes reales" in tg.messages[-1]
    with store.connect() as c:
        row = c.execute("SELECT decision,github_comment_id FROM critical_approval_state WHERE issue_number=77").fetchone()
    assert tuple(row) == ("APPROVED", 9001)


def test_reject_records_rejection_and_no_hotfix_authorization(tmp_path):
    gw, gh, tg, store = gateway(tmp_path)
    result = gw.handle_callback(callback("CHFNO:77"))
    assert result == "REJECTED"
    assert gh.comments_added[0][0] is False
    assert "NO AUTORIZAR" in tg.messages[-1]


def test_wrong_telegram_sender_is_fail_closed(tmp_path):
    gw, gh, tg, store = gateway(tmp_path)
    assert gw.handle_callback(callback(sender="99999")) == "UNAUTHORIZED"
    assert gh.comments_added == []
    assert tg.messages == []


def test_closed_issue_cannot_be_authorized(tmp_path):
    gw, gh, tg, store = gateway(tmp_path)
    gh.issue["state"] = "closed"
    assert gw.handle_callback(callback()) == "NOT_AWAITING"
    assert gh.comments_added == []


def test_issue_without_marker_cannot_be_authorized(tmp_path):
    gw, gh, tg, store = gateway(tmp_path)
    gh.issue["body"] = "diagnostic only"
    assert gw.handle_callback(callback()) == "NOT_AWAITING"
    assert gh.comments_added == []


def test_already_decided_is_idempotent(tmp_path):
    gw, gh, tg, store = gateway(tmp_path)
    gh.state = "APPROVED"
    assert gw.handle_callback(callback()) == "ALREADY_DECIDED"
    assert gh.comments_added == []


def test_callback_offset_is_persisted(tmp_path):
    gw, gh, tg, store = gateway(tmp_path)
    tg._updates = [callback()]
    assert gw.poll_callbacks_once() == 1
    assert store.offset() == 9


def test_runtime_refuses_ppi_credentials(monkeypatch):
    monkeypatch.setenv("POROTA_CRITICAL_APPROVAL_ENABLED", "true")
    monkeypatch.setenv("POROTA_RUNTIME_MODE", "PRODUCTION_PAPER")
    monkeypatch.setenv("PPI_API_KEY", "must-not-be-here")
    with pytest.raises(mod.GatewayError, match="PPI_CREDENTIAL_PRESENT"):
        mod._validate_runtime()


def test_runtime_requires_production_paper(monkeypatch):
    monkeypatch.setenv("POROTA_CRITICAL_APPROVAL_ENABLED", "true")
    monkeypatch.setenv("POROTA_RUNTIME_MODE", "SANDBOX")
    for name in (
        "PPI_API_KEY", "PPI_API_SECRET", "PPI_API_KEY_PROD", "PPI_API_SECRET_PROD",
        "PPI_ACCOUNT_NUMBER", "PPI_PRODUCTION_SECRET_FILE",
    ):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(mod.GatewayError, match="RUNTIME_MODE_NOT_PRODUCTION_PAPER"):
        mod._validate_runtime()


def test_gateway_schema_contains_no_order_or_trade_tables(tmp_path):
    store = mod.ApprovalStore(str(tmp_path / "approval.db"))
    with sqlite3.connect(store.path) as c:
        names = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert names == {"critical_approval_state", "critical_approval_meta"}
