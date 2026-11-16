"""Explicit fake-only AUTH seam for offline tests; no production authority.

Tests must replace the HTTP adapter with an in-process fake BEFORE opting in.
No synthetic quota, signature or SQLite ledger is trusted by runtime code.
"""

import requests


_NATIVE_ADAPTER_SEND = requests.adapters.HTTPAdapter.send


def allow_fake_auth_only(monkeypatch):
    """Exercise SDK login/refresh on a fake transport, without provider IO."""
    if requests.adapters.HTTPAdapter.send is _NATIVE_ADAPTER_SEND:
        raise AssertionError("FAKE_HTTP_ADAPTER_REQUIRED_FOR_AUTH_TEST")
    # The runtime now allows only the account login/refresh whitelist. Keep
    # this fixture as a hard guard against accidentally testing with live HTTP.
