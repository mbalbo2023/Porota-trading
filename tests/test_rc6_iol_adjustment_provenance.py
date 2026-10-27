"""The IOL request variant is evidence, not a certified legacy price basis."""

import pytest

import ak_iol_client as iol
import al_historical_ingest as history


class _EnabledClient:
    enabled = True

    def get_serie_historica(self, *_args, **_kwargs):
        raise AssertionError("IOL network read preceded the provenance guard")


def test_iol_legacy_backfill_rejects_before_network_or_sqlite(monkeypatch, tmp_path):
    database = tmp_path / "market_history.db"
    monkeypatch.setattr(history, "HIST_DB_PATH", str(database))
    monkeypatch.setattr(history, "init_db", lambda: pytest.fail("SQLite initialized"))

    result = history.backfill_desde_iol(_EnabledClient(), ["GGAL"],
                                         asset_class="ACCIONES", dias=365)

    assert result == {"ok": False, "motivo": "IOL_HISTORICAL_PROVENANCE_UNVERIFIED",
                      "asset_class": "ACCIONES", "velas": 0}
    assert not database.exists()


@pytest.mark.parametrize("adjusted", [False, True])
def test_direct_iol_legacy_write_rejected_regardless_of_requested_variant(
        monkeypatch, tmp_path, adjusted):
    database = tmp_path / "market_history.db"
    monkeypatch.setattr(history, "HIST_DB_PATH", str(database))
    row = history.HistoricalRow(("2026-10-23", 5940, 6280, 5845, 6235, 2527187),
                                currency="ARS", volume_kind="NOMINAL")

    with pytest.raises(ValueError, match="IOL_HISTORICAL_PROVENANCE_UNVERIFIED"):
        history.guardar_velas("GGAL", "ACCIONES", [row], "IOL", adjusted)
    assert not database.exists()


@pytest.mark.parametrize("adjusted,suffix", [(False, "/false"), (True, "/true")])
def test_iol_client_keeps_literal_provider_fields_and_request_variant(adjusted, suffix):
    row = {"fecha": "2026-10-23T16:00:00", "ultimoPrecio": 6235,
           "precioAjuste": 0, "moneda": "ARS", "volumen": 2527187}
    paths = []
    client = object.__new__(iol.IOLClient)
    client._get = lambda path, _label: (paths.append(path), {"precios": [row]})[1]

    assert client.get_serie_historica("GGAL", mercado="bcba", dias=7,
                                      ajustada=adjusted) == [row]
    assert paths[0].endswith(suffix)
    assert row["precioAjuste"] == 0
