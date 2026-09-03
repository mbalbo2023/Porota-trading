from cz_a3_cem_public_history_hf6 import (
    A3CEMPublicReadOnlyClient,
    CEMReadOnlyError,
    CEM_LIVE_TRADING_GATE_ALLOWED,
    CEM_ORDER_ROUTING_ALLOWED,
    assert_cem_invariants,
)


class _Response:
    status_code=200
    def __init__(self,payload): self.payload=payload
    def json(self): return self.payload


class _Session:
    def __init__(self): self.calls=[]
    def get(self,url,**kwargs):
        self.calls.append((url,kwargs))
        return _Response({"data":[]})


def test_cem_is_not_order_source_or_live_gate():
    assert CEM_ORDER_ROUTING_ALLOWED is False
    assert CEM_LIVE_TRADING_GATE_ALLOWED is False
    assert_cem_invariants()


def test_cem_client_exposes_no_order_methods():
    names=set(dir(A3CEMPublicReadOnlyClient))
    for name in ("send_order","new_order","replace_order","cancel_order"):
        assert name not in names


def test_cem_expected_read_methods_exist():
    names=set(dir(A3CEMPublicReadOnlyClient))
    for name in ("products","symbols","closing_prices","tick_prices","totals","option_underlying_prices"):
        assert name in names


def test_cem_historical_queries_fail_closed_without_scope_and_dates():
    client=A3CEMPublicReadOnlyClient(session=_Session())
    for method in (client.closing_prices,client.tick_prices):
        try:
            method()
            assert False,"unbounded CEM query unexpectedly allowed"
        except CEMReadOnlyError as exc:
            assert str(exc)=="CEM_HISTORY_SCOPE_REQUIRED"


def test_cem_closing_query_is_scoped_and_bounded():
    session=_Session(); client=A3CEMPublicReadOnlyClient(session=session)
    client.closing_prices(
        symbol="DLR/SEP26",date_from="2026-09-01T00:00:00-03:00",
        date_to="2026-09-02T23:59:59-03:00",page=1,page_size=25)
    url,kwargs=session.calls[-1]
    assert url.endswith("/api/v1/closing-prices")
    assert kwargs["params"]["symbol"]=="DLR/SEP26"
    assert kwargs["params"]["pageSize"]==25
    assert kwargs["params"]["from"].startswith("2026-09-01")


def test_cem_tick_query_rejects_ranges_over_one_day():
    client=A3CEMPublicReadOnlyClient(session=_Session())
    try:
        client.tick_prices(
            symbol="DLR/SEP26",date_from="2026-09-01T00:00:00-03:00",
            date_to="2026-09-03T00:00:00-03:00")
        assert False,"wide tick query unexpectedly allowed"
    except CEMReadOnlyError as exc:
        assert str(exc)=="CEM_HISTORY_DATE_RANGE_TOO_WIDE"
