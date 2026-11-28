from datetime import date

import cx_a3_primary_readonly_hf6 as a3


class FakeResponse:
    def __init__(self, status_code=200, headers=None, payload=None):
        self.status_code=status_code
        self.headers=headers or {}
        self._payload=payload or {"status":"OK"}

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self):
        self.calls=[]

    def post(self, url, headers=None, timeout=None):
        self.calls.append(("POST",url,headers or {}))
        return FakeResponse(headers={"X-Auth-Token":"SECRET_TOKEN_TEST_ONLY"})

    def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append(("GET",url,params or {}))
        if url.endswith("/rest/segment/all"):
            return FakeResponse(payload={"status":"OK","segments":[]})
        if url.endswith("/rest/instruments/all"):
            return FakeResponse(payload={"status":"OK","instruments":[]})
        if url.endswith("/rest/instruments/details"):
            return FakeResponse(payload={"status":"OK","instruments":[]})
        if url.endswith("/rest/instruments/detail"):
            return FakeResponse(payload={"status":"OK","instrument":{"contractMultiplier":1000}})
        if url.endswith("/rest/data/getTrades"):
            return FakeResponse(payload={"status":"OK","trades":[]})
        raise AssertionError(url)


def config():
    return a3.A3Config(
        username="user",
        password="password",
        account="account",
        environment="REMARKETS",
        base_url="https://api.remarkets.primary.com.ar",
    )


def test_readonly_invariant_and_no_order_methods():
    assert a3.A3_ORDER_ROUTING_ALLOWED is False
    a3.assert_read_only_contract()
    names=set(dir(a3.A3PrimaryReadOnlyClient))
    assert "send_order" not in names
    assert "replace_order" not in names
    assert "cancel_order" not in names


def test_readiness_authenticates_then_only_reads():
    session=FakeSession()
    client=a3.A3PrimaryReadOnlyClient(config(),session=session)
    r=client.readiness()
    assert r["authenticated"] is True
    assert r["segments_readable"] is True
    assert r["instruments_readable"] is True
    assert r["order_routing_allowed"] is False
    assert [x[0] for x in session.calls]==["POST","GET","GET"]
    assert session.calls[0][1].endswith("/auth/getToken")
    assert all("/order/" not in x[1] for x in session.calls)


def test_contract_and_history_paths_are_get_only():
    session=FakeSession()
    client=a3.A3PrimaryReadOnlyClient(config(),session=session)
    client.get_instrument_details("DLR/DIC26")
    client.get_trade_history("DLR/DIC26",date(2026,9,2))
    methods=[x[0] for x in session.calls]
    assert methods[0]=="POST"
    assert methods[1:]==["GET","GET"]
    assert all("/order/" not in x[1] for x in session.calls)


def test_invalid_base_url_fails_closed():
    bad=a3.A3Config(username="u",password="p",base_url="http://example.invalid")
    try:
        a3.A3PrimaryReadOnlyClient(bad,session=FakeSession())
    except a3.A3ReadOnlyError as exc:
        assert "HTTPS" in str(exc)
    else:
        raise AssertionError("non-HTTPS base URL should fail")
