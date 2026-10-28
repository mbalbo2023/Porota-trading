import pytest

from c_ppi_client import ResilientPPIClient
from ppi_client.ppi import PPI


def _wrapper():
    wrapper = ResilientPPIClient.__new__(ResilientPPIClient)
    wrapper.is_sandbox = True
    return wrapper


def test_adaptador_usa_headers_y_host_configurados(monkeypatch):
    monkeypatch.setenv("PPI_CLIENT_ID", "cliente-prueba")
    monkeypatch.setenv("PPI_CLIENT_KEY", "clave-prueba")
    monkeypatch.setenv(
        "PPI_SANDBOX_BASE_URL",
        "https://clientapisandbox.portfoliopersonal.com/api/",
    )
    candidate = PPI(sandbox=True)

    _wrapper()._configure_sandbox_sdk(candidate)

    api_client = candidate._PPI__apiClient
    rest_client = api_client.get_rest_client()
    assert api_client._PPIClient__authorized_client == "cliente-prueba"
    assert api_client._PPIClient__client_key == "clave-prueba"
    assert rest_client.authorized_client == "cliente-prueba"
    assert rest_client.client_key == "clave-prueba"
    assert rest_client._RestClient__API_BASE_URL == (
        "https://clientapisandbox.portfoliopersonal.com/api/"
    )


def test_adaptador_falla_cerrado_si_faltan_headers(monkeypatch):
    monkeypatch.delenv("PPI_CLIENT_ID", raising=False)
    monkeypatch.delenv("PPI_CLIENT_KEY", raising=False)

    with pytest.raises(RuntimeError, match="PPI_CLIENT_ID y PPI_CLIENT_KEY"):
        _wrapper()._configure_sandbox_sdk(PPI(sandbox=True))


def test_adaptador_no_modifica_produccion(monkeypatch):
    wrapper = _wrapper()
    wrapper.is_sandbox = False
    candidate = PPI(sandbox=False)
    original = candidate._PPI__apiClient._PPIClient__client_key

    wrapper._configure_sandbox_sdk(candidate)

    assert candidate._PPI__apiClient._PPIClient__client_key == original


def test_adaptador_default_usa_host_con_tls_valido(monkeypatch):
    monkeypatch.setenv("PPI_CLIENT_ID", "cliente-prueba")
    monkeypatch.setenv("PPI_CLIENT_KEY", "clave-prueba")
    monkeypatch.delenv("PPI_SANDBOX_BASE_URL", raising=False)
    candidate = PPI(sandbox=True)

    _wrapper()._configure_sandbox_sdk(candidate)

    rest_client = candidate._PPI__apiClient.get_rest_client()
    assert rest_client._RestClient__API_BASE_URL == (
        "https://clientapisandbox.portfoliopersonal.com/api/"
    )


def test_adaptador_captura_metadatos_no_sensibles_sin_red(monkeypatch):
    monkeypatch.setenv("PPI_CLIENT_ID", "cliente-prueba")
    monkeypatch.setenv("PPI_CLIENT_KEY", "clave-prueba")
    wrapper = _wrapper()
    candidate = PPI(sandbox=True)

    wrapper._configure_sandbox_sdk(candidate)
    rest_client = candidate._PPI__apiClient.get_rest_client()
    session = rest_client.get_session()

    response = type(
        "Response",
        (),
        {
            "status_code": 403,
            "headers": {"Content-Type": "text/html"},
            "content": b"<html>denegado</html>",
        },
    )()
    for hook in session.hooks["response"]:
        hook(response)

    assert wrapper._auth_http_diagnostic == (
        "HTTP 403; Content-Type=text/html; Content-Length=21"
    )
