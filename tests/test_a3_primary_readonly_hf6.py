from cx_a3_primary_readonly_hf6 import (
    A3Config,
    A3PrimaryReadOnlyClient,
    A3ReadOnlyError,
    A3_ORDER_ROUTING_ALLOWED,
    assert_read_only_contract,
)


class FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {"status": "OK"}
        self.headers = headers or {}

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self):
        self.calls = []

    def post(self, url, headers=None, timeout=None):
        self.calls.append(("POST", url, headers, None))
        assert url.endswith("/auth/getToken")
        assert "X-Username" in headers and "X-Password" in headers
        return FakeResponse(headers={"X-Auth-Token": "ephemeral-test-token"})

    def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append(("GET", url, headers, params))
        assert headers == {"X-Auth-Token": "ephemeral-test-token"}
        if url.endswith("/rest/segment/all"):
            return FakeResponse(payload={"status": "OK", "segments": []})
        if url.endswith("/rest/instruments/all"):
            return FakeResponse(payload={"status": "OK", "instruments": []})
        if url.endswith("/rest/instruments/details"):
            return FakeResponse(payload={"status": "OK", "instruments": []})
        if url.endswith("/rest/marketdata/get"):
            return FakeResponse(payload={"status": "OK", "marketData": {}})
        if url.endswith("/rest/data/getTrades"):
            return FakeResponse(payload={"status": "OK", "trades": []})
        return FakeResponse(payload={"status": "OK"})


def config():
    return A3Config(
        username="secret-user",
        password="secret-password",
        account="secret-account",
        environment="REMARKETS",
        base_url="https://api.remarkets.primary.com.ar",
    )


def test_order_routing_is_hard_disabled():
    assert A3_ORDER_ROUTING_ALLOWED is False
    assert_read_only_contract()
    names = {n.lower() for n in dir(A3PrimaryReadOnlyClient)}
    assert not any("cancel_order" in n or "send_order" in n or "new_order" in n for n in names)


def test_secrets_do_not_appear_in_config_repr():
    rendered = repr(config())
    assert "secret-user" not in rendered
    assert "secret-password" not in rendered
    assert "secret-account" not in rendered


def test_readiness_calls_only_auth_plus_get_reads():
    session = FakeSession()
    client = A3PrimaryReadOnlyClient(config(), session=session)
    result = client.readiness()
    assert result["authenticated"] is True
    assert result["segments_readable"] is True
    assert result["instruments_readable"] is True
    assert result["details_readable"] is True
    methods = [c[0] for c in session.calls]
    assert methods == ["POST", "GET", "GET", "GET"]
    assert session.calls[0][1].endswith("/auth/getToken")
    assert all("/order/" not in c[1] for c in session.calls)


def test_market_data_and_history_are_get_only():
    session = FakeSession()
    client = A3PrimaryReadOnlyClient(config(), session=session)
    client.get_market_data("TEST/FUT")
    client.get_trade_history("TEST/FUT", "2026-09-01")
    assert [c[0] for c in session.calls] == ["POST", "GET", "GET"]
    assert session.calls[1][1].endswith("/rest/marketdata/get")
    assert session.calls[2][1].endswith("/rest/data/getTrades")


def test_requires_https():
    bad = A3Config(username="u", password="p", base_url="http://example.invalid")
    try:
        bad.validate()
    except A3ReadOnlyError as exc:
        assert str(exc) == "A3_BASE_URL_MUST_BE_HTTPS"
    else:
        raise AssertionError("Expected HTTPS validation failure")
