"""RC6: IOL debe permanecer read-only salvo opt-in manual explícito."""

import ak_iol_client as iol


def test_cost_estimate_post_is_blocked_without_explicit_opt_in(monkeypatch):
    monkeypatch.delenv("IOL_COST_ESTIMATE_EXPLICIT_OPT_IN", raising=False)
    client = iol.IOLClient(username="user", password="password")
    client.enabled = True

    def fail_if_called(*args, **kwargs):
        raise AssertionError("no debe existir POST sin opt-in explícito")

    monkeypatch.setattr(iol.requests, "post", fail_if_called)
    assert client.estimar_operacion("GGAL", cantidad=1, precio=1.0) is None


def test_cost_estimate_post_requires_opt_in_but_is_testable_manually(monkeypatch):
    monkeypatch.setenv("IOL_COST_ESTIMATE_EXPLICIT_OPT_IN", "true")
    client = iol.IOLClient(username="user", password="password")
    client.enabled = True
    monkeypatch.setattr(client, "_headers", lambda: {"Authorization": "Bearer test"})
    calls = []

    class Response:
        status_code = 200
        @staticmethod
        def json():
            return {"montoIVA": 3}

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return Response()

    monkeypatch.setattr(iol.requests, "post", fake_post)
    result = client.estimar_operacion("GGAL", cantidad=1, precio=1.0)
    assert result["montoIVA"] == 3
    assert len(calls) == 1
    assert calls[0][0].endswith("/api/v2/operar/estimar")
