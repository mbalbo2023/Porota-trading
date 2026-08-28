import json
import os
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from dataclasses import replace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bd_ppi_readonly_guard import (ProductionMarketReader, ReadOnlyPolicyViolation,
                                   ReadOnlyTransportGuard)
from be_paper_engine import D, PaperBroker, PaperStore, Quote
import bf_production_paper_observer as observer
from bh_paper_gemini import CURRENT_TEXT_MODELS, rank_models
from bt_caucion_paper import CaucionOffer, modeled_sale_settlement, pending_proceeds
from bs_instrument_contracts import InstrumentContract
from bs_instrument_contracts import cash_currency
import bu_instrument_catalog as catalog


def quote(symbol="GGAL", price="100", minute=0, bid_size="1000", ask_size="1000", at=None):
    at = at or (datetime(2026, 8, 25, 14, 0, tzinfo=timezone.utc) +
          timedelta(minutes=minute)).isoformat()
    price = D(price)
    return Quote(symbol, "ACCIONES", "A-24HS", price, price-D("0.10"),
                 price+D("0.10"), D(bid_size), D(ask_size), at,
                 currency="ARS", market="BYMA", metadata_source="TEST_FIXTURE",
                 book_at=at, trade_at=at, last_kind="TRADE")


@pytest.fixture
def real_catalog():
    return json.loads((ROOT / "tests/fixtures/ppi_catalog_20260827.json").read_text())["records"]


@pytest.mark.parametrize("label,expected", [("Pesos", "ARS"), ("Dolares billete | MEP", "USD_MEP"),
                                          ("Dolares divisa | CCL", "USD_CCL"), ("USD", "USD")])
def test_moneda_ppi_conserva_plaza(label, expected):
    assert cash_currency(label) == expected


@pytest.mark.parametrize("label", [None, "", "Dolares", "EUR", "INVENTADA"])
def test_moneda_ambigua_no_se_supone_ars(label):
    with pytest.raises(ValueError):
        cash_currency(label)


def test_diagnostico_real_no_convierte_bono_denominado_usd_en_caja_usd(real_catalog):
    raw = next(r for r in real_catalog if r["ticker"] == "AE38")
    record = catalog.normalize_record(raw, "A-24HS", "2026-08-27T13:45:03Z", "test")
    assert record["currency"] == "ARS"
    assert record["capability"] == "NEEDS_NOMINAL_UNITS"
    future = catalog.normalize_record(real_catalog[-1], "A-24HS", "2026-08-27T13:45:03Z", "test")
    assert future["capability"] == "NEEDS_FUTURES_MARGIN_AND_CONTRACT"
    assert catalog.quote_terms(future)["contract"] is None  # No parsea vencimiento desde descripción.


def test_catalogo_real_preserva_clase_moneda_y_no_duplica_resultados(tmp_path, monkeypatch, real_catalog):
    store = PaperStore(str(tmp_path / "paper.db"))
    observer._support_schema(store)
    monkeypatch.setattr(observer, "CATALOG_QUERY_SLEEP_SECONDS", 0)
    monkeypatch.setattr(observer, "_candidate_universe", lambda: [
        ("FILTRO-A", "ACCIONES", "A-24HS", "BYMA", True),
        ("FILTRO-B", "ACCIONES", "A-24HS", "BYMA", True)])
    class Reader:
        def search_instruments(self, *_args, **_kwargs):
            return real_catalog
    assert observer._download_catalog(Reader(), store) == 12
    with store.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM candidate_universe").fetchone()[0] == 12
        assert c.execute("SELECT COUNT(*) FROM instrument_catalog WHERE instrument_type='FUTUROS'").fetchone()[0] == 3
        assert c.execute("SELECT COUNT(*) FROM catalog_query_results").fetchone()[0] == 2
        assert not c.execute("SELECT 1 FROM candidate_universe WHERE ticker LIKE 'FILTRO-%'").fetchone()
    assert catalog.lookup(store, "ALUAC", "ACCIONES", "A-24HS")["currency"] == "USD_CCL"
    assert catalog.lookup(store, "AAPLD", "CEDEARS", "A-24HS")["currency"] == "USD_MEP"
    assert ("DLR/AGO26", "FUTUROS", "A-24HS") in observer._eligible_symbols(store)


def test_actualizacion_fallida_no_borra_catalogo_ni_habilita_registros_viejos(tmp_path, monkeypatch, real_catalog):
    store = PaperStore(str(tmp_path / "paper.db"))
    observer._support_schema(store)
    monkeypatch.setattr(observer, "CATALOG_QUERY_SLEEP_SECONDS", 0)
    monkeypatch.setattr(observer, "_candidate_universe", lambda: [("A", "ACCIONES", "A-24HS", "BYMA", True)])
    class Reader:
        def search_instruments(self, *_args, **_kwargs):
            return real_catalog
    observer._download_catalog(Reader(), store)
    class Broken:
        def search_instruments(self, *_args, **_kwargs):
            raise TimeoutError("fixture")
    assert observer._download_catalog(Broken(), store) == 0
    with store.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM instrument_catalog").fetchone()[0] == 12
        assert c.execute("SELECT COUNT(*) FROM financial_instrument_catalog WHERE status='STALE'").fetchone()[0] == 12
    metadata = catalog.lookup(store, "AAPL", "CEDEARS", "A-24HS")
    assert catalog.quote_terms(metadata)["opening_block_reason"]


def test_cotizacion_mep_del_catalogo_no_gasta_ars_ni_usd_generico(tmp_path, real_catalog):
    store = PaperStore(str(tmp_path / "paper.db"))
    raw = next(r for r in real_catalog if r["ticker"] == "AAPLD")
    metadata = catalog.normalize_record(raw, "INMEDIATA", "2026-08-27T13:45:03Z", "test")
    source_at = observer.now_iso()
    q = observer.normalize_quote("AAPLD", "CEDEARS", "INMEDIATA", {"price": 100, "date": source_at},
                                 {"bid": 99, "ask": 100, "bidsize": 10000, "asksize": 10000, "date": source_at}, metadata=metadata)
    broker = PaperBroker(store, initial_cash="1000000", initial_cash_usd="10000")
    assert q.currency == "USD_MEP" and q.contract.currency == "USD_MEP"
    assert broker._open(q, D("0.8"), {})[0] is False
    assert broker._cash(currency="USD") == 10000
    funded = PaperBroker(store, initial_cash_by_currency={"USD_MEP": "10000"})
    assert funded._open(q, D("0.8"), {})[0]
    p = store.open_positions()[0]
    assert p["currency"] == "USD_MEP"
    assert funded._cash() == 1000000
    assert funded._cash(currency="USD_MEP") < 10000
    ccl = replace(q, currency="USD_CCL", contract=replace(q.contract, currency="USD_CCL"))
    assert not funded._close(p, ccl, "TEST")
    closing_at = (datetime.fromisoformat(q.observed_at)+timedelta(minutes=1)).isoformat()
    closing = replace(q, bid=D("110"), ask=D("111"), observed_at=closing_at, book_at=closing_at)
    assert funded._close(p, closing, "TEST")
    closed = store.recent_closed()[0]
    assert funded._cash(currency="USD_MEP", as_of=closing.observed_at) == 10000 + D(closed["net_pnl"])
    assert funded._cash() == 1000000
    assert funded._cash(currency="USD_CCL") == 0
    values = funded.mark_equity({}, as_of=closing.observed_at)
    assert values["ARS"]["realized_pnl"] == 0
    assert values["USD_MEP"]["realized_pnl"] == D(closed["net_pnl"])


def test_senal_no_mezcla_moneda_ni_mercado(tmp_path):
    store = PaperStore(str(tmp_path / "paper.db"))
    broker = PaperBroker(store)
    for i in range(8):
        store.add_quote(replace(quote(minute=i), currency="USD_CCL"))
        store.add_quote(replace(quote(minute=i), market="OTRO"))
    q = quote(minute=9)
    store.add_quote(q)
    assert broker.decide(q)[3]["samples"] == 1


def test_migracion_moneda_preserva_fills_anteriores_sin_reinterpretar_dolares(tmp_path):
    db = str(tmp_path / "legacy.db")
    store = PaperStore(db)
    q = quote(symbol="ALUAC")
    store.add_quote(q)
    assert PaperBroker(store)._open(q, D("0.8"), {})[0]
    original = store.open_positions()[0]
    # Reproduce el esquema anterior, que no identificaba moneda ni plaza.
    with store.connect() as c:
        c.execute('DROP TRIGGER paper_events_notify_v17')  # Tampoco existía en 16.3.5.
        c.execute("DROP INDEX idx_snapshot_identity")
        for table, columns in {"paper_positions": ("currency", "market", "currency_source"),
                               "market_snapshots": ("currency", "market")}.items():
            for column in columns:
                c.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
    migrated = PaperStore(db)
    PaperStore(db)  # Reintento de migración sin efectos adicionales.
    p = migrated.open_positions()[0]
    assert p["currency"] == "ARS" and p["currency_source"] == "LEGACY_ASSUMED_ARS"
    assert (p["quantity"], p["entry_cost"], p["entry_price"]) == (original["quantity"], original["entry_cost"], original["entry_price"])
    assert not PaperBroker(migrated)._close(p, replace(q, currency="USD_CCL"), "TEST")
    with migrated.connect() as c:
        assert c.execute("SELECT currency FROM market_snapshots").fetchone()[0] == "UNKNOWN"
        assert c.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 1


@pytest.mark.parametrize("changes", [{"currency": None}, {"market": None}, {"opening_block_reason": "STALE"}])
def test_cotizacion_sin_identidad_confirmada_no_abre(tmp_path, changes):
    broker = PaperBroker(PaperStore(str(tmp_path / "paper.db")))
    q = replace(quote(), **changes)
    assert broker._open(q, D("0.8"), {})[0] is False
    assert broker.decide(q)[0] == "HOLD"


def test_metadatos_viejos_se_importan_sin_aprobar_operaciones(tmp_path, real_catalog):
    store = PaperStore(str(tmp_path / "paper.db"))
    observer._support_schema(store)
    raw = next(r for r in real_catalog if r["ticker"] == "ALUAC")
    with store.connect() as c:
        c.execute("INSERT INTO instrument_catalog VALUES(?,?,?,?,?,?,?)",
                  ("ACCIONES", "ALUAC", raw["description"], "BYMA", "A-24HS", "2026-08-27T13:45:03Z", json.dumps(raw)))
    catalog.init_schema(store)
    catalog.init_schema(store)
    record = catalog.lookup(store, "ALUAC", "ACCIONES", "A-24HS")
    assert record["currency"] == "USD_CCL"
    assert record["status"] == "STALE"
    assert catalog.quote_terms(record)["opening_block_reason"]


def test_caucion_mep_no_usa_ccl_ni_usd_sin_plaza(tmp_path):
    broker = PaperBroker(PaperStore(str(tmp_path / "paper.db")), initial_cash_usd="10000",
                         initial_cash_by_currency={"USD_CCL": "10000"})
    offer = caucion_offer(currency="USD_MEP")
    with pytest.raises(ValueError, match="Caja liquidada"):
        broker.place_caucion(offer, "1000", "sin-mep", offer.quoted_at)
    funded = PaperBroker(broker.store, initial_cash_by_currency={"USD_MEP": "2000"})
    funded.place_caucion(offer, "1000", "mep", offer.quoted_at)
    assert funded._cash(currency="USD_MEP") == 1000
    assert funded._cash(currency="USD_CCL") == 0


def caucion_offer(**changes):
    terms = dict(instrument_id="CAUCION-FIXTURE-ARS-20260831", currency="ARS",
                 annual_rate_fraction=D("0.365"), start_date="2026-08-28",
                 maturity_at="2026-08-31T15:00:00-03:00",
                 quoted_at="2026-08-28T11:00:00-03:00",
                 available_principal=D("100000"), minimum_principal=D("100"),
                 principal_step=D("1"), day_count_basis=365, fee_payment="MATURITY",
                 metadata_source="TEST_FIXTURE_NOT_BROKER", quoted_total_fees=D("1"),
                 fee_quote_principal=D("1000"))
    return CaucionOffer(**(terms | changes))


@pytest.mark.parametrize("fee_payment", ["MATURITY", "UPFRONT"])
def test_v17_caucion_inmoviliza_capital_y_acredita_una_vez_al_vencer(tmp_path, fee_payment):
    store = PaperStore(str(tmp_path / "paper.db"))
    broker = PaperBroker(store, initial_cash="10000")
    offer = caucion_offer(fee_payment=fee_payment)
    p = broker.place_caucion(offer, "1000", "pedido-1", offer.quoted_at)
    assert p["interest_days"] == 3  # Viernes a lunes; no usar 1 día para el interés.
    assert D(p["gross_interest"]) == 3
    assert broker._cash() == D("8999" if fee_payment == "UPFRONT" else "9000")
    broker.mark_equity({}, as_of=offer.quoted_at)
    with store.connect() as c:
        assert D(c.execute("SELECT equity FROM paper_equity ORDER BY id DESC LIMIT 1").fetchone()[0]) == 9999
    assert broker.settle_cauciones("2026-08-31T14:59:59-03:00") == []
    assert broker.settle_cauciones(offer.maturity_at) == [p["paper_id"]]
    assert broker._cash() == 10002
    assert broker.settle_cauciones(offer.maturity_at) == []
    broker.mark_equity({}, as_of=offer.maturity_at)
    with store.connect() as c:
        assert D(c.execute("SELECT equity FROM paper_equity ORDER BY id DESC LIMIT 1").fetchone()[0]) == 10002
        assert c.execute("SELECT COUNT(*) FROM paper_events WHERE event_type='PAPER_CAUCION_MATURED'").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 0  # No inventa compra/venta.


def test_v17_caucion_reintento_persistente_no_duplica_ni_reutiliza_terminos(tmp_path):
    path = str(tmp_path / "paper.db")
    broker = PaperBroker(PaperStore(path), initial_cash="10000")
    offer = caucion_offer()
    original = broker.place_caucion(offer, "1000", "pedido", offer.quoted_at)
    restarted = PaperBroker(PaperStore(path), initial_cash="10000")
    assert restarted.place_caucion(offer, "1000", "pedido", offer.maturity_at)["paper_id"] == original["paper_id"]
    with pytest.raises(ValueError, match="términos diferentes"):
        restarted.place_caucion(replace(offer, annual_rate_fraction=D("0.40")), "1000", "pedido", offer.quoted_at)
    assert restarted._cash() == 9000
    assert len(restarted.cauciones.positions()) == 1


def test_v17_caucion_no_convierte_pesos_en_dolares_para_financiarse(tmp_path):
    broker = PaperBroker(PaperStore(str(tmp_path / "paper.db")), initial_cash="1000000")
    offer = caucion_offer(currency="USD")
    with pytest.raises(ValueError, match="Caja liquidada insuficiente"):
        broker.place_caucion(offer, "1000", "usd-sin-caja", offer.quoted_at)
    assert broker._cash() == 1000000
    funded = PaperBroker(broker.store, initial_cash="1000000", initial_cash_usd="2000")
    funded.place_caucion(offer, "1000", "usd", offer.quoted_at)
    assert funded._cash(currency="USD") == 1000
    funded.settle_cauciones(offer.maturity_at)
    assert funded._cash(currency="USD") == 2002
    assert funded._cash() == 1000000


@pytest.mark.parametrize("changes, now, reserve, error", [
    ({}, "2026-08-28T11:01:01-03:00", "0", "vencida o futura"),
    ({}, "2026-08-28T10:59:59-03:00", "0", "vencida o futura"),
    ({"available_principal": D("2000")}, "2026-08-28T11:00:00-03:00", "0", "participación"),
    ({"annual_rate_fraction": D("0.001")}, "2026-08-28T11:00:00-03:00", "0", "neto positivo"),
    ({}, "2026-08-28T11:00:00-03:00", "9500", "Caja liquidada"),
    ({"fee_quote_principal": D("2000")}, "2026-08-28T11:00:00-03:00", "0", "otro capital"),
])
def test_v17_caucion_rechaza_datos_y_fondos_incompatibles(tmp_path, changes, now, reserve, error):
    broker = PaperBroker(PaperStore(str(tmp_path / "paper.db")), initial_cash="10000")
    with pytest.raises(ValueError, match=error):
        broker.place_caucion(caucion_offer(**changes), "1000", "pedido", now, reserve=reserve)
    assert broker.cauciones.positions() == []
    assert broker._cash() == 10000


@pytest.mark.parametrize("changes", [
    {"annual_rate_fraction": "NaN"}, {"day_count_basis": 0},
    {"maturity_at": "2026-08-31T15:00:00"}, {"maturity_at": "2026-08-28T15:00:00-03:00"},
    {"currency": "EUR"}, {"fee_payment": "UNKNOWN"},
    {"currency": "USD", "quoted_total_fees": None}, {"quoted_total_fees": "Infinity"},
])
def test_v17_caucion_exige_terminos_completos(changes):
    with pytest.raises(ValueError):
        caucion_offer(**changes)


def test_v17_caucion_prorratea_arancel_anual_sin_cobrarlo_por_operacion():
    offer = caucion_offer(quoted_total_fees=None)
    interest, fees, net = offer.economics("1000")
    assert interest == 3
    assert fees == D("0.20")  # 1000 * (0.02 + 0.00045) * 1.21 * 3 / 365.
    assert net == D("2.80")


def test_v17_dos_colocaciones_concurrentes_no_gastan_la_misma_caja(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    broker = PaperBroker(PaperStore(str(tmp_path / "paper.db")), initial_cash="1500")
    offer = caucion_offer()
    def place(request):
        try:
            return broker.place_caucion(offer, "1000", request, offer.quoted_at)["paper_id"]
        except ValueError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(place, ["A", "B"]))
    assert sum(result is not None for result in results) == 1
    assert broker._cash() == 500


def test_v17_venta_t1_no_es_caja_hasta_liquidacion_modelada(tmp_path):
    broker = PaperBroker(PaperStore(str(tmp_path / "paper.db")), initial_cash="10000")
    q = quote(at="2026-08-28T11:00:00-03:00")
    assert broker._open(q, D("0.8"), {})[0]
    position = broker.store.open_positions()[0]
    before = broker._cash(as_of=q.observed_at)
    sell = replace(q, bid=D("110"), ask=D("111"), observed_at="2026-08-28T11:01:00-03:00")
    assert broker._close(position, sell, "TEST")
    assert broker._cash(as_of=sell.observed_at) == before
    assert broker._cash(as_of="2026-08-31T12:00:00-03:00") == before
    assert broker._cash(as_of="2026-09-01T00:00:00-03:00") == 10000 + D(broker.store.recent_closed()[0]["net_pnl"])
    assert pending_proceeds(broker.store, sell.observed_at) > 0
    broker.mark_equity({}, as_of=sell.observed_at)
    with broker.store.connect() as c:
        equity = D(c.execute("SELECT equity FROM paper_equity ORDER BY id DESC LIMIT 1").fetchone()[0])
    assert equity == 10000 + D(broker.store.recent_closed()[0]["net_pnl"])


def test_v17_calendario_caja_respeta_dia_sin_liquidacion_y_ano_desconocido():
    # Viernes 6/11 no liquida: jueves T+1 pasa al lunes 9.
    assert modeled_sale_settlement("A-24HS", "2026-11-05T11:00:00-03:00").startswith("2026-11-09")
    assert modeled_sale_settlement("A-24HS", "2026-12-30T11:00:00-03:00") is None
    assert modeled_sale_settlement("PLAZO-DESCONOCIDO", "2026-08-28T11:00:00-03:00") is None


@pytest.mark.parametrize("family", ["CAUCIONES", "OPCIONES", "FUTUROS", "FCI", "DESCONOCIDO"])
def test_v17_no_ejecuta_familias_especiales_como_acciones(tmp_path, family):
    broker = PaperBroker(PaperStore(str(tmp_path / "paper.db")))
    assert broker._open(replace(quote(), asset_class=family), D("0.8"), {})[0] is False
    assert broker.store.open_positions() == []


@pytest.mark.parametrize("family", ["BONOS", "LETRAS", "ON"])
def test_v17_renta_fija_dimensiona_por_nominal_y_persiste_factor(tmp_path, family):
    path = str(tmp_path / "paper.db")
    broker = PaperBroker(PaperStore(path), initial_cash="10000", risk_pct="1",
                         max_position_pct="1", max_total_exposure_pct="1", slippage_bps="0")
    q = replace(quote(ask_size="1000000", bid_size="1000000"), asset_class=family,
                ask=D("100"), settlement="INMEDIATA")
    assert broker._open(q, D("0.8"), {})[0] is False  # No inventa el factor VN.
    spec = InstrumentContract(q.symbol, family, "ARS", "BYMA", "INMEDIATA",
                              D("0.01"), D("100"), "TEST_NOT_BROKER")
    q = replace(q, contract=spec)
    assert broker._open(q, D("0.8"), {})[0]
    position = broker.store.open_positions()[0]
    assert D(position["quantity"]) == 9900
    assert broker._cash() == 10000 - 9900 - D(position["entry_cost"])
    broker = PaperBroker(PaperStore(path), initial_cash="10000", risk_pct="1", slippage_bps="0")
    broker.mark_equity({q.symbol: q}, as_of=q.observed_at)
    with broker.store.connect() as c:
        assert D(c.execute("SELECT exposure FROM paper_equity ORDER BY id DESC LIMIT 1").fetchone()[0]) == q.bid * 99
    closing = replace(q, bid=D("110"), ask=D("111"), observed_at="2026-08-25T14:01:00+00:00")
    assert broker._close(position, replace(closing, contract=replace(spec, cash_multiplier=D("1"))), "TEST") is False
    assert broker._close(position, closing, "TEST")
    closed = broker.store.recent_closed()[0]
    assert D(closed["gross_pnl"]) == 990
    assert D(closed["entry_cost"]) == broker._cost(D("1"), D("9900"), family)
    assert D(closed["exit_cost"]) == broker._cost(D("1.1"), D("9900"), family)
    assert broker._cash(as_of=closing.observed_at) == 10000 + D(closed["net_pnl"])


def test_v17_no_cauciona_el_producido_de_una_venta_t1(tmp_path):
    broker = PaperBroker(PaperStore(str(tmp_path / "paper.db")), initial_cash="1100",
                         risk_pct="1", max_position_pct="1", max_total_exposure_pct="1")
    q = quote(at="2026-08-28T10:59:00-03:00")
    assert broker._open(q, D("0.8"), {})[0]
    assert broker._close(broker.store.open_positions()[0], replace(q, bid=D("110"), ask=D("111"), observed_at="2026-08-28T11:00:00-03:00"), "TEST")
    offer = caucion_offer()
    with pytest.raises(ValueError, match="Caja liquidada insuficiente"):
        broker.place_caucion(offer, "1000", "reusar-venta", offer.quoted_at)


def test_v17_compra_no_reutiliza_capital_colocado_en_caucion(tmp_path):
    broker = PaperBroker(PaperStore(str(tmp_path / "paper.db")), initial_cash="1050",
                         risk_pct="1", max_position_pct="1", max_total_exposure_pct="1")
    offer = caucion_offer()
    broker.place_caucion(offer, "1000", "inmovilizar", offer.quoted_at)
    q = quote(at=offer.quoted_at)
    assert broker._open(q, D("0.8"), {})[0] is False
    assert broker._cash() == 50


def test_v17_diagnostico_exporta_catalogo_y_copia_json_sin_cuentas(tmp_path):
    import base64
    import subprocess
    store = PaperStore(str(tmp_path / "paper.db"))
    observer._support_schema(store)
    with store.connect() as c:
        c.execute("INSERT INTO instrument_catalog VALUES(?,?,?,?,?,?,?)",
                  ("FUTUROS", "DLR-FIXTURE", "Contrato de prueba", "A3", "INMEDIATA", "2026-08-28",
                   json.dumps({"ticker": "DLR-FIXTURE", "contractMultiplier": 1000,
                               "initialMargin": 100000, "accountNumber": "NO-EXPORTAR", "api_key": "NO-EXPORTAR"})))
    result = subprocess.run([sys.executable, str(ROOT / "scripts/v17_diagnostico_instrumentos.py"),
                             "--db", store.path, "--clipboard"], check=True, capture_output=True, text=True)
    plain, rest = result.stdout.split("\033]52;c;", 1)
    report = json.loads(plain)
    copied = json.loads(base64.b64decode(rest.split("\a", 1)[0]))
    assert copied == report
    assert "NO-EXPORTAR" not in result.stdout
    assert report["samples"][0]["public_metadata"]["contractMultiplier"] == 1000
    with store.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM instrument_catalog").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM paper_positions").fetchone()[0] == 0


def test_v17_compra_reserva_comision_dentro_de_la_caja(tmp_path):
    store = PaperStore(str(tmp_path / "paper.db"))
    broker = PaperBroker(store, initial_cash="1000", risk_pct="1",
                         max_position_pct="1", max_total_exposure_pct="1",
                         slippage_bps="0")
    q = replace(quote(ask_size="100000"), ask=D("100"))
    assert broker._open(q, D("0.8"), {})[0]
    assert broker._cash() >= 0
    assert D(store.open_positions()[0]["quantity"]) == 9


def test_v17_riesgo_incluye_ambas_comisiones_y_slippage_de_salida(tmp_path):
    store = PaperStore(str(tmp_path / "paper.db"))
    broker = PaperBroker(store, initial_cash="1000", risk_pct="0.01",
                         max_position_pct="1", max_total_exposure_pct="1")
    assert broker._open(quote(ask_size="100000"), D("0.8"), {})[0]
    p = store.open_positions()[0]
    qty = D(p["quantity"])
    expected_exit = (D(p["stop_price"]) * (1-broker.slippage)).quantize(D("0.0001"))
    modeled_loss = ((D(p["entry_price"])-expected_exit)*qty +
                    D(p["entry_cost"]) + broker._cost(expected_exit, qty, "ACCIONES"))
    assert modeled_loss <= D("10")


@pytest.mark.parametrize("change", [
    {"bid_size": D("1")}, {"settlement": "INMEDIATA"},
    {"asset_class": "BONOS"}, {"symbol": "OTRO"},
    {"observed_at": "2026-08-25T13:59:59+00:00"},
    {"observed_at": "fecha-invalida"},
])
def test_v17_cierre_no_inventa_liquidez_ni_mezcla_instrumentos(tmp_path, change):
    store = PaperStore(str(tmp_path / "paper.db"))
    broker = PaperBroker(store)
    assert broker._open(quote(), D("0.8"), {})[0]
    p = store.open_positions()[0]
    assert broker._close(p, replace(quote(price="110", minute=1), **change), "TEST") is False
    assert store.open_positions()
    with store.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM paper_fills WHERE side='SELL_SIMULATED'").fetchone()[0] == 0


def test_v17_cierre_idempotente_ante_reintento_con_posicion_vieja(tmp_path):
    store = PaperStore(str(tmp_path / "paper.db"))
    broker = PaperBroker(store)
    assert broker._open(quote(), D("0.8"), {})[0]
    p = store.open_positions()[0]
    closing = quote(price="110", minute=1)
    assert broker._close(p, closing, "TEST") is True
    before = broker._cash()
    assert broker._close(p, closing, "TEST") is False
    assert broker._cash() == before
    with store.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM paper_fills WHERE side='SELL_SIMULATED'").fetchone()[0] == 1


def test_v17_no_entrena_senal_con_otro_plazo_o_clase(tmp_path):
    store = PaperStore(str(tmp_path / "paper.db"))
    for i in range(8):
        store.add_quote(replace(quote(price=str(100+i), minute=i), settlement="INMEDIATA"))
        store.add_quote(replace(quote(price=str(100+i), minute=i), asset_class="BONOS"))
    q = quote(price="107", minute=8)
    store.add_quote(q)
    action, _, _, features = PaperBroker(store).decide(q)
    assert action == "HOLD"
    assert features["samples"] == 1


@pytest.mark.parametrize("value", ["NaN", "sNaN", "Infinity", "-Infinity"])
def test_v17_decimal_no_finito_se_rechaza_como_dato_invalido(value):
    assert D(value, "-1") == D("-1")


def test_v17_sin_tarifario_no_se_inventa_comision(tmp_path, monkeypatch):
    import au_fee_schedule
    def unavailable(_kind):
        raise ValueError("tarifario no disponible")
    monkeypatch.setattr(au_fee_schedule, "costo_por_tramo", unavailable)
    broker = PaperBroker(PaperStore(str(tmp_path / "paper.db")))
    with pytest.raises(ValueError, match="tarifario"):
        broker._cost(D("100"), D("1"), "ACCIONES")


def test_v17_prioridad_de_abiertas_incluso_fuera_del_universo(tmp_path, monkeypatch):
    store = PaperStore(str(tmp_path / "paper.db"))
    observer._support_schema(store)
    broker = PaperBroker(store, max_positions=5)
    for i in range(4):
        assert broker._open(quote(symbol=f"ABIERTA{i}"), D("0.8"), {})[0]
    monkeypatch.setattr(observer, "ACTIVE_SYMBOL_LIMIT", 2)
    selected, _, _, _ = observer._cycle_symbols(store)
    assert {v[0] for v in selected} == {f"ABIERTA{i}" for i in range(4)}


def test_v17_compra_rechaza_puntas_cruzadas(tmp_path):
    store = PaperStore(str(tmp_path / "paper.db"))
    broker = PaperBroker(store)
    assert broker._open(replace(quote(), bid=D("110"), ask=D("100")), D("0.8"), {})[0] is False
    assert store.open_positions() == []


@pytest.mark.parametrize("change", [{"bid": Decimal("NaN")},
                                   {"ask": Decimal("Infinity")},
                                   {"ask_size": Decimal("sNaN")}])
def test_v17_senal_no_calcula_con_puntas_no_finitas(tmp_path, change):
    store = PaperStore(str(tmp_path / "paper.db"))
    for i in range(8):
        store.add_quote(quote(price=str(100+i), minute=i))
    assert PaperBroker(store).decide(replace(quote(), **change))[0] == "HOLD"


def test_guard_permite_solo_host_https_y_rutas_lectura():
    g = ReadOnlyTransportGuard()
    assert g.check("GET", "https://clientapi.portfoliopersonal.com/api/1.0/MarketData/Current")
    assert g.check("POST", "https://clientapi.portfoliopersonal.com/api/1.0/Account/LoginApi",
                   count_login=True)
    with pytest.raises(ReadOnlyPolicyViolation):
        g.check("POST", "https://clientapi.portfoliopersonal.com/api/1.0/Order/Confirm")
    with pytest.raises(ReadOnlyPolicyViolation):
        g.check("DELETE", "https://clientapi.portfoliopersonal.com/api/1.0/MarketData/Current")
    with pytest.raises(ReadOnlyPolicyViolation):
        g.check("GET", "http://clientapi.portfoliopersonal.com/api/1.0/MarketData/Current")
    with pytest.raises(ReadOnlyPolicyViolation):
        g.check("GET", "https://evil.example/api/1.0/MarketData/Current")
    with pytest.raises(ReadOnlyPolicyViolation):
        g.check("POST", "https://clientapi.portfoliopersonal.com/api/1.0/Account/LoginApi",
                count_login=True)


def test_guard_permite_catalogo_e_historicos_pero_no_cuenta_ni_ordenes():
    g = ReadOnlyTransportGuard()
    assert g.check("GET", "https://clientapi.portfoliopersonal.com/api/1.0/MarketData/SearchInstrument")
    assert g.check("GET", "https://clientapi.portfoliopersonal.com/api/1.0/MarketData/Search")
    with pytest.raises(ReadOnlyPolicyViolation):
        g.check("GET", "https://clientapi.portfoliopersonal.com/api/1.0/Account/Accounts")
    with pytest.raises(ReadOnlyPolicyViolation):
        g.check("POST", "https://clientapi.portfoliopersonal.com/api/1.0/Order/New")


@pytest.mark.parametrize("word", ["Order/", "Budget", "Confirm", "Cancel", "Transfer", "MassCancel"])
def test_observador_no_contiene_capacidad_operativa(word):
    source = (ROOT / "bf_production_paper_observer.py").read_text(encoding="utf-8")
    assert word not in source


def test_ciclo_compra_y_venta_es_solo_paper(tmp_path):
    store = PaperStore(str(tmp_path / "observer.db"))
    broker = PaperBroker(store, initial_cash="1000000", fee_rate="0.001")
    for i, price in enumerate(("100", "100.2", "100.4", "100.6", "100.8", "101", "101.4", "102")):
        q = quote(price=price, minute=i)
        store.add_quote(q)
        broker.on_quote(q)
    positions = store.open_positions()
    assert len(positions) == 1
    p = positions[0]
    assert p["paper_id"].startswith("PAPER-")
    assert p["source"] == "PRODUCTION_PAPER"
    assert D(p["entry_price"]) > quote(price="102", minute=7).ask
    closing = quote(price=str(D(p["target_price"]) + 1), minute=9)
    store.add_quote(closing)
    broker.on_quote(closing)
    assert not store.open_positions()
    closed = store.recent_closed(1)[0]
    assert closed["status"] == "CLOSED"
    assert closed["close_reason"] == "TAKE_PROFIT_PAPER"
    with store.connect() as c:
        fills = [dict(r) for r in c.execute("SELECT * FROM paper_fills ORDER BY id")]
        sample = dict(c.execute("SELECT * FROM paper_learning_samples").fetchone())
    assert [f["side"] for f in fills] == ["BUY_SIMULATED", "SELL_SIMULATED"]
    assert sample["label_timestamp"] is not None
    assert sample["outcome"] in {"WIN", "LOSS", "FLAT"}


def test_no_duplica_decision_ni_posicion_en_mismo_ciclo(tmp_path):
    store = PaperStore(str(tmp_path / "observer.db"))
    broker = PaperBroker(store)
    for i in range(8):
        q = quote(price=str(100+i), minute=i)
        store.add_quote(q)
        broker.on_quote(q)
    before = len(store.open_positions())
    q = quote(price="107", minute=7)
    broker.on_quote(q)
    assert len(store.open_positions()) == before == 1


def test_sin_ask_size_no_hay_fill(tmp_path):
    store = PaperStore(str(tmp_path / "observer.db"))
    broker = PaperBroker(store)
    for i in range(8):
        q = quote(price=str(100+i), minute=i, ask_size="0")
        store.add_quote(q)
        broker.on_quote(q)
    assert store.open_positions() == []


def test_patrimonio_paper_limita_posicion_y_exposicion(tmp_path):
    store = PaperStore(str(tmp_path / "observer.db"))
    broker = PaperBroker(store, initial_cash="1000000", risk_pct="0.50",
                         max_position_pct="0.25", max_total_exposure_pct="0.60")
    for i in range(8):
        q = quote(price=str(100+i), minute=i, ask_size="100000")
        store.add_quote(q)
        broker.on_quote(q)
    position = store.open_positions()[0]
    notional = D(position["entry_price"]) * D(position["quantity"])
    assert notional <= D("250000")
    features = json.loads(position["features_json"])
    assert features["initial_capital_ars"] == "1000000"
    assert features["max_position_pct"] == "0.25"


def test_base_operativa_no_se_abre(tmp_path):
    operational = tmp_path / "trading_system.db"
    operational.write_bytes(b"NO TOCAR")
    PaperStore(str(tmp_path / "observer" / "observer_production.db"))
    assert operational.read_bytes() == b"NO TOCAR"


def test_estado_declara_cero_ordenes_reales(tmp_path):
    store = PaperStore(str(tmp_path / "observer.db"))
    with store.connect() as c:
        row = dict(c.execute("SELECT * FROM observer_state").fetchone())
    assert row["mode"] == "PRODUCTION_PAPER"
    assert row["real_orders_sent"] == 0


def test_sync_diario_no_se_duplica(tmp_path):
    store = PaperStore(str(tmp_path / "observer.db"))
    observer._support_schema(store)
    assert observer._daily_sync_needed(store)
    today = datetime.now(observer.TZ).date().isoformat()
    with store.connect() as connection:
        for source in ("PPI_PRODUCTION_CATALOG", "PPI_PRODUCTION_HISTORY"):
            connection.execute("INSERT INTO source_sync VALUES(?,?,?,?,?,?)",
                               (source, "VERDE", today, today, 1, "ok"))
    assert not observer._daily_sync_needed(store)


def test_busqueda_ppi_envia_ticker_y_name_no_vacios():
    calls = []
    class Market:
        def search_instrument(self, *args):
            calls.append(args)
            return []
    class Client:
        marketdata = Market()
    reader = object.__new__(ProductionMarketReader)
    reader._ProductionMarketReader__authenticated = True
    reader._ProductionMarketReader__client = Client()
    assert reader.search_instruments("GGAL", "ACCIONES", market="BYMA") == []
    assert calls == [("GGAL", "GGAL", "BYMA", "ACCIONES")]
    with pytest.raises(ValueError):
        reader.search_instruments("", "ACCIONES")


def test_fases_de_mercado_impiden_operar_fuera_de_rueda(monkeypatch):
    monkeypatch.setattr(observer, "_business_day", lambda _day: True)
    closed = datetime(2026, 8, 26, 9, 0, tzinfo=observer.TZ)
    preopen = datetime(2026, 8, 26, 10, 50, tzinfo=observer.TZ)
    opened = datetime(2026, 8, 26, 11, 5, tzinfo=observer.TZ)
    after = datetime(2026, 8, 26, 17, 1, tzinfo=observer.TZ)
    assert observer._market_phase(closed) == "CLOSED"
    assert observer._market_phase(preopen) == "PREOPEN"
    assert observer._market_phase(opened) == "OPEN"
    assert observer._market_phase(after) == "CLOSED"


def test_universo_ampliado_mantiene_derivados_solo_contexto(monkeypatch, tmp_path):
    watchlist = tmp_path / "watchlist.json"
    watchlist.write_text(json.dumps({
        "ACCIONES": {"instrument_type": "ACCIONES", "settlement": "A-24HS",
                      "tickers": ["GGAL", "YPFD"]},
        "FUTUROS": {"instrument_type": "FUTUROS", "settlement": "A-24HS",
                     "tickers": ["DLR"]},
    }), encoding="utf-8")
    monkeypatch.setattr(observer, "WATCHLIST_PATH", watchlist)
    candidates = observer._candidate_universe()
    assert any(row[0] == "YPFD" and row[4] for row in candidates)
    assert any(row[1] == "FUTUROS" and not row[4] for row in candidates)


def test_gemini_es_porton_critico_y_persiste_veredicto(tmp_path):
    class Gate:
        def __init__(self, approve):
            self.approve = approve
        def evaluate(self, *_args):
            return {"decision": "APPROVE" if self.approve else "VETO",
                    "score": 0.91, "veto": not self.approve,
                    "reason": "contrato de prueba", "model": "gemini-test", "raw": {}}

    for approve in (False, True):
        store = PaperStore(str(tmp_path / f"observer-{approve}.db"))
        broker = PaperBroker(store, ai_gate=Gate(approve), require_ai=True)
        for i in range(8):
            q = quote(price=str(100+i), minute=i)
            store.add_quote(q)
            broker.on_quote(q)
        assert bool(store.open_positions()) is approve
        with store.connect() as connection:
            ai = dict(connection.execute(
                "SELECT * FROM ai_shadow_evaluations ORDER BY id DESC LIMIT 1").fetchone())
        assert ai["decision"] == ("APPROVE" if approve else "VETO")
        with store.connect() as connection:
            gate = dict(connection.execute(
                "SELECT * FROM trade_gate_evaluations ORDER BY id DESC LIMIT 1").fetchone())
        assert gate["ai_gate"] == ("APPROVE" if approve else "VETO")
        assert gate["final_result"] == ("OPENED_SIMULATED" if approve else "BLOCKED")


def test_gemini_ausente_cierra_el_porton_paper(tmp_path):
    store = PaperStore(str(tmp_path / "observer.db"))
    broker = PaperBroker(store, require_ai=True)
    for i in range(8):
        q = quote(price=str(100+i), minute=i)
        store.add_quote(q)
        broker.on_quote(q)
    assert store.open_positions() == []


def test_gemini_descarta_modelo_retirado_y_prioriza_inventario_real():
    models = rank_models(
        "gemini-2.5-flash-lite", (),
        ["gemini-3.6-flash", "gemini-3.1-flash-lite"],
    )
    assert models == ["gemini-3.6-flash", "gemini-3.1-flash-lite"]
    assert "gemini-2.5-flash-lite" not in models


def test_gemini_sin_inventario_usa_cadena_estable_actual():
    models = rank_models("gemini-2.5-flash-lite", (), None)
    assert tuple(models[:len(CURRENT_TEXT_MODELS)]) == CURRENT_TEXT_MODELS
    assert models[0] == "gemini-3.7-flash"


def test_comando_gemini_no_abre_ni_requiere_ppi(tmp_path):
    store = PaperStore(str(tmp_path / "observer.db"))
    observer._support_schema(store)
    class Gate:
        def healthcheck(self):
            return {"ok": True, "model": "gemini-3.7-flash"}
    with store.connect() as connection:
        cursor = connection.execute(
            "INSERT INTO observer_commands(created_at,command,status) VALUES(?,?,?)",
            (datetime.now(timezone.utc).isoformat(), "GEMINI_PREFLIGHT", "RUNNING"),
        )
        command_id = cursor.lastrowid
    assert observer._run_command(store, None, (command_id, "GEMINI_PREFLIGHT"), Gate()) is None
    with store.connect() as connection:
        row = connection.execute(
            "SELECT status,result FROM observer_commands WHERE id=?", (command_id,)
        ).fetchone()
    assert row[0] == "OK"
    assert "gemini-3.7-flash" in row[1]


def test_catalogo_incorpora_cada_instrumento_devuelto(monkeypatch, tmp_path):
    store = PaperStore(str(tmp_path / "observer.db"))
    observer._support_schema(store)
    monkeypatch.setattr(observer, "_candidate_universe",
                        lambda: [("A", "ACCIONES", "A-24HS", "BYMA", True)])
    class Reader:
        def search_instruments(self, ticker, kind, name=None, market="BYMA"):
            assert ticker and name
            return [{"ticker": "GGAL", "instrumentType": "ACCIONES", "market": "BYMA"},
                    {"ticker": "YPFD", "instrumentType": "ACCIONES", "market": "BYMA"}]
    assert observer._download_catalog(Reader(), store) == 2
    with store.connect() as connection:
        values = {row[0] for row in connection.execute(
            "SELECT ticker FROM candidate_universe WHERE status='AVAILABLE'")}
    assert {"GGAL", "YPFD"}.issubset(values)


def test_lote_por_ciclo_rota_sobre_todo_el_universo(tmp_path, monkeypatch):
    store = PaperStore(str(tmp_path / "observer.db"))
    observer._support_schema(store)
    monkeypatch.setattr(observer, "ACTIVE_SYMBOL_LIMIT", 3)
    with store.connect() as connection:
        for ticker in ("GGAL", "AL30", "AAPL", "YPFD", "PAMP", "BMA"):
            kind = "CEDEARS" if ticker == "AAPL" else "BONOS" if ticker == "AL30" else "ACCIONES"
            connection.execute("INSERT OR REPLACE INTO candidate_universe VALUES(?,?,?,?,?,?,?,?)",
                               (ticker, kind, "A-24HS", "BYMA", 1, "AVAILABLE", "ok", "2026-08-26"))
    first, total, before, after = observer._cycle_symbols(store)
    with store.connect() as connection:
        connection.execute("""INSERT INTO universe_cycle_metrics
          (started_at,finished_at,eligible_total,selected_count,successful_count,failed_count,
           duration_seconds,cursor_before,cursor_after,recommended_limit,detail)
          VALUES('a','b',?,?,?,?,?,?,?,?,?)""",
          (total, len(first), len(first), 0, 1.0, before, after, 3, "test"))
    second, total2, _, _ = observer._cycle_symbols(store)
    assert total2 == total >= 6
    assert {x[0] for x in first} != {x[0] for x in second}


def test_historicos_usan_universo_completo_no_lote_activo(tmp_path, monkeypatch):
    store = PaperStore(str(tmp_path / "observer.db"))
    observer._support_schema(store)
    with store.connect() as connection:
        for ticker in ("GGAL", "YPFD", "PAMP", "BMA"):
            connection.execute("INSERT OR REPLACE INTO candidate_universe VALUES(?,?,?,?,?,?,?,?)",
                               (ticker, "ACCIONES", "A-24HS", "BYMA", 1, "AVAILABLE", "ok", "2026-08-26"))
    monkeypatch.setattr(observer, "ACTIVE_SYMBOL_LIMIT", 2)
    monkeypatch.setattr(observer, "HISTORY_BATCH_LIMIT", 100)
    class Reader:
        def history(self, symbol, *_args):
            return [{"date":"2026-08-25T17:00:00-03:00","price":1,
                     "openingPrice":1,"max":1,"min":1,"volume":100}]
    observer._download_histories(Reader(), store)
    with store.connect() as connection:
        covered = connection.execute("SELECT COUNT(*) FROM production_history").fetchone()[0]
    assert covered >= 4
