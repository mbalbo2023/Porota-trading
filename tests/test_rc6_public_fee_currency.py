"""PPI public spot tariff is a dated PAPER assumption, never an account quote."""
import json
import sqlite3
from decimal import Decimal

import pytest

import au_fee_schedule as fees
import d_economics as economics
from be_paper_engine import PaperBroker, PaperStore
from rc6_performance.costs import ledger_leg_cost, paper_cost_contract
from rc6_ppi_contract_normalizer import separate_unbound_fee_estimate

AFTER = "2026-10-26T19:12:01Z"
BEFORE = "2026-10-26T19:11:59Z"


def test_public_ppi_on_and_currency_specific_letras_in_paper_ledger(tmp_path):
    # Public internet schedule: ON .60% without +IVA, Letras ARS .20%,
    # Letras USD 1.00%. Market rights remain an explicit separate assumption.
    assert fees.arancel_de("ON").comision_iva is False
    assert fees.arancel_de("ON").comision == .006
    assert fees.arancel_de("LETRAS", currency="ARS").comision == .002
    assert fees.arancel_de("LETRAS", currency="USD").comision == .01
    assert fees.arancel_de("LETRAS", currency="USD_MEP").comision == .01
    assert fees.costo_por_tramo("OBLIGACIONES") == .0061
    ars = paper_cost_contract("LETRAS", "ARS", AFTER)
    usd = paper_cost_contract("LETRAS", "USD", AFTER)
    assert ars.binding_error(AFTER) == ""
    assert usd.binding_error(AFTER) == "COST_COMPONENT_UNKNOWN:rights"
    assert ars.commission == Decimal("0.002")
    assert usd.commission == Decimal("0.01")
    assert usd.rights is None
    assert ars.diagnostic()["policy_sha256"] != usd.diagnostic()["policy_sha256"]
    assert ars.account_terms == usd.account_terms == "NO_VERIFICADO"
    broker = PaperBroker(PaperStore(str(tmp_path / "paper.db")))
    assert broker._cost(100, 100, "LETRAS", currency="USD") == ledger_leg_cost(
        100, 100, "LETRAS", currency="USD")
    assert broker._cost(100, 100, "LETRAS", currency="USD") > broker._cost(
        100, 100, "LETRAS", currency="ARS")


def test_missing_or_unknown_currency_cannot_bind_letras(tmp_path):
    broker = PaperBroker(PaperStore(str(tmp_path / "paper.db")))
    for call in (
        lambda: fees.arancel_de("LETRAS"),
        lambda: fees.costo_redondo("LETRAS"),
        lambda: economics.calculate_trade_costs_fraction(asset_class="LETRAS"),
        lambda: broker._cost(100, 1, "LETRAS"),
    ):
        with pytest.raises(ValueError, match="FEE_CURRENCY_REQUIRED:LETRAS"):
            call()
    with pytest.raises(ValueError, match="Moneda/plaza no reconocida"):
        paper_cost_contract("LETRAS", "UNKNOWN", AFTER)
    with pytest.raises(ValueError, match="FEE_BREAKDOWN_ARS_ONLY"):
        fees.desglose("LETRAS", 100, currency="USD")


@pytest.mark.parametrize("family,currency", [("LETRAS", "ARS"),
                                              ("LETRAS", "USD"),
                                              ("OBLIGACIONES", "ARS")])
def test_revised_public_policy_cannot_backdate_decisions(family, currency):
    contract = paper_cost_contract(family, currency, BEFORE)
    assert contract.binding_error(BEFORE) == "COST_POLICY_NOT_KNOWN_OR_EFFECTIVE"
    assert contract.known_at == "2026-10-26T19:12:00Z"


def test_currency_specific_override_never_changes_ars(monkeypatch):
    monkeypatch.setenv("FEE_LETRAS_USD_COMISION", "0.012")
    assert fees.arancel_de("LETRAS", currency="USD").comision == .012
    assert fees.arancel_de("LETRAS", currency="ARS").comision == .002


def test_table_names_both_letras_currencies_without_inventing_account_terms():
    rows = [r for r in fees.tabla_para_documentacion() if r["clase"] == "LETRAS"]
    assert {(r["moneda"], r["comision_pct"]) for r in rows} == {
        ("ARS", .2), ("USD", 1.0)}


def test_authenticated_get_estimate_is_not_fanned_out_to_cash_identities(tmp_path, monkeypatch):
    from scripts import porota_contract_evidence_session_runner_rc4 as runner

    row = {"ticker": "LTEST", "instrument_id": 123,
           "currency": {"description": "Pesos", "symbol": "$"},
           "commission_rate_estimated": .002,
           "commission_vat_rate_estimated": 0,
           "market_fee_rate": .00001}
    contract, estimate = separate_unbound_fee_estimate(row)
    assert "commission_rate_estimated" not in contract
    assert estimate["commission_rate_estimated"] == .002

    db = tmp_path / "contracts.db"
    with sqlite3.connect(db) as connection:
        connection.execute("""CREATE TABLE candidate_identity_v2(
          ticker TEXT, instrument_type TEXT, market TEXT, currency TEXT,
          settlement TEXT, status TEXT)""")
        connection.executemany("INSERT INTO candidate_identity_v2 VALUES(?,?,?,?,?,?)", [
            ("LTEST", "LETRAS", "BYMA", currency, "A-24HS", "AVAILABLE")
            for currency in ("ARS", "USD")])
    capture = tmp_path / "capture.json"
    capture.write_text(json.dumps({"jobs": ["CONTRACT_EVIDENCE_DYNAMIC"],
        "auth_status": "AUTHENTICATED_TRUSTED_DEVICE",
        "endpoints": {"https://trading.portfoliopersonal.com/InstrumentosOperables": {
            "kind": "InstrumentosOperables", "observed_job": "CONTRACT_EVIDENCE_DYNAMIC",
            "rows": [row]}}}), encoding="utf-8")
    importer = runner.IMPORTER.replace(
        'DB="/app/data/paper_v17/observer_v17.db"', f"DB={str(db)!r}")
    monkeypatch.setattr("sys.argv", ["-", str(capture)])
    exec(compile(importer, "embedded_contract_importer", "exec"), {"__name__": "__main__"})
    with sqlite3.connect(db) as connection:
        rows = connection.execute("""SELECT currency,evidence_json
          FROM contract_evidence_v2_snapshots WHERE ticker='LTEST'""").fetchall()
    assert {currency for currency, _ in rows} == {"ARS", "USD"}
    assert all("commission_rate_estimated" not in body and
               "market_fee_rate" not in body for _, body in rows)


def test_legacy_daily_report_does_not_reconcile_unlinked_account_charges(monkeypatch):
    import h_daily_report as report
    import d_economics

    class NoAccountReads:
        def get_tax_report(self, *args, **kwargs):
            raise AssertionError("NO_EXACT_OPERATION_TO_CHARGE_JOIN")

    monkeypatch.setattr(d_economics, "save_fee_stress_factor",
                        lambda *a, **k: pytest.fail("UNLINKED_FEE_STRESS_WRITE"))
    closed = [{"ticker": "LTEST", "entry_price": 100., "quantity": 3}]
    monkeypatch.setenv("ENVIRONMENT", "PRODUCTION_PAPER")
    monkeypatch.setenv("ORDER_EXECUTION_MODE", "SIMULATED")
    paper = report._reconcile_real_costs_today(NoAccountReads(), closed)
    assert paper["state"] == "NOT_OBSERVABLE_IN_PAPER"
    assert paper["estimated_cost_ars"] is None
    monkeypatch.setenv("ENVIRONMENT", "PRODUCTION_REAL")
    monkeypatch.setenv("ORDER_EXECUTION_MODE", "REAL")
    blocked = report._reconcile_real_costs_today(NoAccountReads(), closed)
    assert blocked["state"] == "NO_GO_UNLINKED_FAMILY_CURRENCY_AND_BROKER_CHARGES"
    assert blocked["real_cost_ars"] is blocked["estimated_cost_ars"] is blocked["diff_ars"] is None
