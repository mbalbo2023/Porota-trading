import sys
from types import SimpleNamespace

import av_rofex_client as rofex


def _report(monkeypatch, account_data):
    fake = SimpleNamespace(get_account_report=lambda account: {"accountData": account_data})
    monkeypatch.setitem(sys.modules, "pyRofex", fake)


def test_margen_agregado_no_se_usa_como_garantia_unitaria(monkeypatch):
    monkeypatch.delenv("FUTURO_GARANTIA_DLR", raising=False)
    _report(monkeypatch, {"initialMargin": 500000})
    valor, motivo = rofex._garantia_de_la_cuenta("DLR/OCT26")
    assert valor is None
    assert "no informa" in motivo


def test_margen_atribuido_al_simbolo_se_acepta(monkeypatch):
    monkeypatch.delenv("FUTURO_GARANTIA_DLR", raising=False)
    _report(monkeypatch, {"initialMargin": {"DLR/OCT26": 50000}})
    valor, fuente = rofex._garantia_de_la_cuenta("DLR/OCT26")
    assert valor == 50000
    assert "DLR/OCT26" in fuente


def test_override_manual_sigue_habilitado(monkeypatch):
    monkeypatch.setenv("FUTURO_GARANTIA_DLR", "45000")
    _report(monkeypatch, {"margin": 999999})
    valor, fuente = rofex._garantia_de_la_cuenta("DLR/OCT26")
    assert valor == 45000
    assert "override" in fuente
