"""The existing production login exposes only bounded, sanitized wire metadata."""

from json import JSONDecodeError
from types import SimpleNamespace

import pytest
import requests

from bd_ppi_readonly_guard import ProductionAuthResponseError, ProductionMarketReader


LOGIN = "https://clientapi.portfoliopersonal.com/api/1.0/account/loginapi"
SECRET = "AUTH_SECRET_DO_NOT_REPORT_0123456789"


def _reader(monkeypatch, *, status=200, media="text/html", body=None, fail=None,
            fail_get=False,
            direct_error=False):
    calls, audits = [], []
    body = b"<html>" + SECRET.encode() + b"</html>" if body is None else body

    def fake_send(adapter, request, **kwargs):
        calls.append((request.method, request.url))
        if fail is not None:
            raise fail
        if fail_get and request.method == "GET":
            raise requests.ConnectionError("later GET unavailable")
        response = requests.Response()
        response.status_code = status
        response.headers["Content-Type"] = media
        response.headers["Set-Cookie"] = SECRET
        response._content = body
        response.request = request
        return response

    # Patch before installing the allowlisted guard, so its original_send is
    # a strictly local fake. Any extra provider request is counted and fails.
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", fake_send)

    class FakePPI:
        def __init__(self, *, sandbox):
            assert sandbox is False
            self.account = SimpleNamespace(login_api=self.login_api)

        def login_api(self, key, secret):
            assert key == "KEY" and secret == SECRET
            if direct_error:
                if direct_error == "after_get":
                    requests.Session().get(
                        "https://clientapi.portfoliopersonal.com/api/1.0/configuration/markets"
                    )
                elif direct_error == "after_post_get":
                    requests.Session().post(LOGIN, json={"key": key, "secret": secret})
                    requests.Session().get(
                        "https://clientapi.portfoliopersonal.com/api/1.0/configuration/markets"
                    ).json()
                elif direct_error == "after_post_failed_get":
                    requests.Session().post(LOGIN, json={"key": key, "secret": secret})
                    try:
                        requests.Session().get(
                            "https://clientapi.portfoliopersonal.com/api/1.0/configuration/markets"
                        )
                    except requests.ConnectionError:
                        pass
                raise JSONDecodeError("Expecting value", "", 0)
            return requests.Session().post(LOGIN, json={"key": key, "secret": secret}).json()

    monkeypatch.setattr("ppi_client.ppi.PPI", FakePPI)
    reader = ProductionMarketReader("KEY", SECRET, audit=lambda *row: audits.append(row),
                                    budget=object(), consumer="SCANNER")
    return reader, calls, audits, len(body)


@pytest.mark.parametrize("status,media,expected_type", [
    (200, "text/html; token=" + SECRET, "TEXT_HTML"),
    (403, "application/json; bearer=" + SECRET, "APPLICATION_JSON"),
    (502, "application/vnd." + SECRET, "OTHER"),
    (200, "", "ABSENT"),
])
def test_single_production_login_reports_only_enumerated_metadata(
        monkeypatch, status, media, expected_type):
    reader, calls, audits, size = _reader(monkeypatch, status=status, media=media)
    try:
        with pytest.raises(ProductionAuthResponseError) as failure:
            reader.login_once()
        result = str(failure.value)
        assert result == ("PPI_PRODUCTION_AUTH_NON_JSON_RESPONSE: "
                          f"HTTP={status}; Content-Type={expected_type}; Content-Length={size}")
        assert SECRET not in result
        assert "Set-Cookie" not in result and "<html>" not in result
        assert failure.value.__context__ is None
        assert calls == [("POST", LOGIN)]
        assert reader.metrics["login_calls"] == 1
        assert audits == [("POST", "/api/1.0/account/loginapi", "ALLOWED")]
        assert reader._ProductionMarketReader__guard._scope.login_http_metadata is None
        assert reader.metrics["authenticated"] is False
    finally:
        reader.close()


def test_successful_json_login_preserves_one_call_and_clears_metadata(monkeypatch):
    reader, calls, _, _ = _reader(monkeypatch, media="application/json",
                                   body=b'{"authenticated":true}')
    try:
        reader.login_once()
        assert calls == [("POST", LOGIN)]
        assert reader.metrics["login_calls"] == 1
        assert reader.metrics["authenticated"] is True
        assert reader._ProductionMarketReader__guard._scope.login_http_metadata is None
        reader.login_once()
        assert len(calls) == 1
    finally:
        reader.close()


def test_transport_failure_keeps_original_error_and_does_not_retry(monkeypatch):
    original = requests.ConnectionError("transport unavailable")
    reader, calls, _, _ = _reader(monkeypatch, fail=original)
    try:
        with pytest.raises(requests.ConnectionError) as failure:
            reader.login_once()
        assert failure.value is original
        assert calls == [("POST", LOGIN)]
        assert reader._ProductionMarketReader__guard._scope.login_http_metadata is None
    finally:
        reader.close()


def test_json_error_without_wire_response_is_not_misattributed(monkeypatch):
    reader, calls, _, _ = _reader(monkeypatch, direct_error=True)
    try:
        with pytest.raises(JSONDecodeError):
            reader.login_once()
        assert calls == []
        assert reader._ProductionMarketReader__guard._scope.login_http_metadata is None
    finally:
        reader.close()


def test_unrelated_get_metadata_cannot_be_attributed_to_login(monkeypatch):
    reader, calls, audits, _ = _reader(monkeypatch, direct_error="after_get")
    try:
        with pytest.raises(JSONDecodeError):
            reader.login_once()
        assert calls == [("GET", "https://clientapi.portfoliopersonal.com/api/1.0/configuration/markets")]
        assert reader.metrics["login_calls"] == 0
        assert audits == [("GET", "/api/1.0/configuration/markets", "ALLOWED")]
        assert reader._ProductionMarketReader__guard._scope.login_http_metadata is None
    finally:
        reader.close()


def test_later_get_json_error_does_not_inherit_login_http_status(monkeypatch):
    reader, calls, audits, _ = _reader(monkeypatch, direct_error="after_post_get")
    try:
        with pytest.raises(JSONDecodeError):
            reader.login_once()
        assert calls == [
            ("POST", LOGIN),
            ("GET", "https://clientapi.portfoliopersonal.com/api/1.0/configuration/markets"),
        ]
        assert reader.metrics["login_calls"] == 1
        assert [row[2] for row in audits] == ["ALLOWED", "ALLOWED"]
        assert reader._ProductionMarketReader__guard._scope.login_http_metadata is None
    finally:
        reader.close()


def test_failed_later_send_also_clears_login_metadata(monkeypatch):
    reader, calls, _, _ = _reader(monkeypatch, direct_error="after_post_failed_get",
                                  fail_get=True)
    try:
        with pytest.raises(JSONDecodeError):
            reader.login_once()
        assert [method for method, _ in calls] == ["POST", "GET"]
        assert reader.metrics["login_calls"] == 1
        assert reader._ProductionMarketReader__guard._scope.login_http_metadata is None
    finally:
        reader.close()
