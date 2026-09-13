import importlib.util
import sys
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "ops" / "ppi_web_readonly_transport_rc6.py"
spec = importlib.util.spec_from_file_location("ppi_web_readonly_transport_rc6", MODULE_PATH)
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


def test_allows_only_known_ppi_hosts():
    mod.validate_url("https://www.portfoliopersonal.com/foo")
    mod.validate_url("https://trading.portfoliopersonal.com/bar")
    with pytest.raises(ValueError, match="host not allowed"):
        mod.validate_url("https://example.com/foo")


def test_rejects_non_https_and_embedded_credentials():
    with pytest.raises(ValueError, match="HTTPS"):
        mod.validate_url("http://www.portfoliopersonal.com/foo")
    with pytest.raises(ValueError, match="credentials in URL"):
        mod.validate_url("https://user:pass@www.portfoliopersonal.com/foo")


def test_authorization_header_is_forbidden_but_ephemeral_cookie_is_allowed():
    with pytest.raises(ValueError, match="forbidden header"):
        mod.sanitize_headers({"Authorization": "Bearer secret"})
    headers = mod.sanitize_headers({"Cookie": "session=secret"})
    assert headers["Cookie"] == "session=secret"


def test_secret_headers_are_redacted_for_logs():
    out = mod.redacted_headers_for_log({"Cookie": "secret", "X-CSRF-Token": "abc", "Accept": "x"})
    assert out["Cookie"] == "<REDACTED>"
    assert out["X-CSRF-Token"] == "<REDACTED>"
    assert out["Accept"] == "x"


def test_source_uses_get_only_and_has_no_mutating_verbs():
    source = MODULE_PATH.read_text(encoding="utf-8").lower()
    assert 'method="get"' in source
    forbidden = ['method="post"', 'method="put"', 'method="delete"', 'method="patch"', "requests.post(", "requests.put(", "requests.delete("]
    assert not [token for token in forbidden if token in source]
