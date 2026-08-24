import time


class DummyNotifier:
    def __init__(self):
        self.errors = []

    def notify_error(self, message):
        self.errors.append(message)

    def notify_recovery(self, _service):
        pass


def _fake_ppi(error=None):
    calls = {"login": 0}

    class Account:
        def login_api(self, _key, _secret):
            calls["login"] += 1
            if error:
                raise RuntimeError(error)

    class FakePPI:
        def __init__(self, sandbox=True):
            self.account = Account()

    return FakePPI, calls


def test_ppi_rate_limit_hace_un_solo_login(monkeypatch, tmp_path):
    monkeypatch.setenv("PPI_API_KEY", "key-test")
    monkeypatch.setenv("PPI_API_SECRET", "secret-test")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "db.sqlite"))
    import c_ppi_client as module

    fake, calls = _fake_ppi("429 API calls quota exceeded maximum admitted 10 per 1h")
    monkeypatch.setattr(module, "PPI", fake)
    client = module.ResilientPPIClient(DummyNotifier())

    assert calls["login"] == 1
    assert client.is_authenticated() is False
    assert client.login() is False
    assert client._call_with_retry(lambda: object(), "probe") is None
    assert calls["login"] == 1
    assert client.auth_status()["cooldown_remaining_seconds"] > 3500


def test_ppi_login_exitoso_expone_estado_local(monkeypatch, tmp_path):
    monkeypatch.setenv("PPI_API_KEY", "key-test")
    monkeypatch.setenv("PPI_API_SECRET", "secret-test")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "db.sqlite"))
    import c_ppi_client as module

    fake, calls = _fake_ppi()
    monkeypatch.setattr(module, "PPI", fake)
    client = module.ResilientPPIClient(DummyNotifier())

    assert calls["login"] == 1
    assert client.is_authenticated() is True
    assert client.auth_status()["state"] == "OK"


def test_health_ppi_no_hace_llamadas_de_red(monkeypatch):
    import am_api_health as health

    class Client:
        def auth_status(self):
            return {"authenticated": True, "state": "OK", "last_call_latency_ms": 12}

        def get_available_balance(self):
            raise AssertionError("el dashboard no debe tocar PPI")

    result = health.chequear_ppi(Client())
    assert result.estado == health.VERDE
    assert result.latencia_ms == 12


def test_scalping_no_usa_yahoo_en_modo_operativo(monkeypatch):
    import e_technical_engine as technical

    monkeypatch.setattr(technical, "YFINANCE_SHADOW_ONLY", True)
    monkeypatch.setattr(technical.yf, "Ticker",
                        lambda *_: (_ for _ in ()).throw(AssertionError("Yahoo no debe llamarse")))
    result = technical.evaluate_technical_scalping("AAPL")
    assert result.data_ok is False
    assert "bloqueado" in result.reason.lower()


def test_websocket_no_arranca_sin_autenticacion(monkeypatch):
    import x_ppi_websocket as ws

    class Client:
        client = object()
        account_number = "1"

        def is_authenticated(self):
            return False

    realtime = ws.PPIRealTimeClient(Client())
    realtime.start()
    assert realtime._threads == []
