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
        "https://clientapi_sandbox.portfoliopersonal.com/api/",
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
        "https://clientapi_sandbox.portfoliopersonal.com/api/"
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
