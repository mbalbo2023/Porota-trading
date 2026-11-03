from __future__ import annotations

import al_historical_ingest as history


class _Client:
    enabled = True

    def get_serie_historica(self, *_args, **_kwargs):
        raise AssertionError("No debe consultar una fuente fuera de alcance")


def test_legacy_backfill_rejects_non_operational_family_before_network():
    result = history.backfill_desde_iol(_Client(), ["AL30"], asset_class="BONOS")

    assert result["ok"] is False
    assert result["motivo"] == "FAMILIA_FUERA_DE_ALCANCE_OPERATIVO"
