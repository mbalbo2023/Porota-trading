"""PPI public spot tariff is a dated PAPER assumption, never an account quote."""
import json
import sqlite3
from datetime import datetime, timedelta, timezone
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
    assert all(r["cost_model_scope"] == "PAPER_PUBLIC_TARIFF_SENSITIVITY"
               and r["account_terms"] == "NO_VERIFICADO"
               and r["market_rights"] == "PAPER_ASSUMPTION_NOT_RECONCILED"
               for r in rows)


def test_public_paper_fee_sources_never_claim_reconciled_broker_terms():
    from bt_caucion_paper import CaucionOffer
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    assert "PAPER" in fees.CAUCION_PAPER_FEE_AUTHORITY
    assert "BYMA_RIGHTS_UNVERIFIED" in fees.CAUCION_PAPER_FEE_AUTHORITY
    assert "BYMA_RIGHTS_VERIFIED" not in fees.CAUCION_PAPER_FEE_AUTHORITY
    breakdown = fees.desglose("ACCIONES", 10000, currency="ARS")
    assert breakdown["cost_model_scope"] == "PAPER_PUBLIC_TARIFF_SENSITIVITY"
    assert breakdown["account_terms"] == "NO_VERIFICADO"
    assert breakdown["market_rights"] == "PAPER_ASSUMPTION_NOT_RECONCILED"
    for family in ("ACCIONES", "CEDEARS", "BONOS", "LETRAS", "OBLIGACIONES", "OPCIONES"):
        source = paper_cost_contract(family, "ARS", AFTER).diagnostic()["source"]
        assert "PAPER:" in source and "BYMA_RIGHTS_UNVERIFIED" in source
        assert "ACCOUNT_UNVERIFIED" in source

    at = datetime(2026, 10, 27, 15, tzinfo=ZoneInfo("America/Argentina/Buenos_Aires"))
    values = dict(instrument_id="PESOS1", currency="ARS", annual_rate_fraction=Decimal("0.30"),
                  start_date=at.date().isoformat(), maturity_at=(at+timedelta(days=1)).isoformat(),
                  quoted_at=at.isoformat(), available_principal=Decimal("100000"),
                  minimum_principal=Decimal("1000"), principal_step=Decimal("1"),
                  day_count_basis=365, fee_payment="MATURITY",
                  metadata_source="CONTRACT_EVIDENCE_V2:TEST",
                  paper_fill_policy="CONSERVATIVE_NOTIONAL_CAP")
    offer = CaucionOffer(**values, fee_authority=fees.CAUCION_PAPER_FEE_AUTHORITY)
    assert offer.fee_authority == fees.CAUCION_PAPER_FEE_AUTHORITY
    with pytest.raises(ValueError, match="Autoridad de costos PAPER incompatible"):
        CaucionOffer(**values, fee_authority=(
            "PPI_TARIFF_EFFECTIVE_2026-07-01+BYMA_RIGHTS_VERIFIED_2026-08-21"))


def test_generic_etf_fee_binding_requires_venue_and_minimum():
    contract = paper_cost_contract("ETFS", "ARS", "2026-10-27T00:13:28Z")
    assert contract.binding_error("2026-10-27T00:13:28Z") == "COST_COMPONENT_UNKNOWN:rights"
    assert contract.minimum_commission is None
    assert contract.rights_vat is None
    assert "ETF_VENUE_AND_MINIMUM_UNVERIFIED" in contract.diagnostic()["source"]
    with pytest.raises(ValueError, match="COST_COMPONENT_UNKNOWN:rights"):
        contract.leg_cost(Decimal("1000"), decision_at="2026-10-27T00:13:28Z")


def test_authenticated_get_estimate_is_not_fanned_out_to_cash_identities(
        tmp_path, monkeypatch, capsys):
    import rc4_contract_import_job as importer
    from scripts import porota_contract_evidence_session_runner_rc4 as runner

    row = {"ticker": "LTEST", "instrument_id": 123,
           "currency": {"description": "Pesos", "symbol": "$"},
           "commission_rate_estimated": .002,
           "commission_vat_rate_estimated": 0,
           "market_fee_rate": .00001}
    contract, estimate = separate_unbound_fee_estimate(row)
    assert "commission_rate_estimated" not in contract
    assert estimate["commission_rate_estimated"] == .002

    capture = tmp_path / "capture.json"
    capture.write_text(json.dumps({"schema": "POROTA_RC4_PPI_TRUSTED_CONTRACT_1",
        "generated_at": (datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat(),
        "routes": [], "blocked_nonread": [], "continue_clicked": False,
        "amount_filled": False, "price_filled": False, "real_orders_sent": 0,
        "jobs": ["CONTRACT_EVIDENCE_DYNAMIC"],
        "auth_status": "AUTHENTICATED_TRUSTED_DEVICE",
        "endpoints": {"https://trading.portfoliopersonal.com/InstrumentosOperables": {
            "kind": "InstrumentosOperables", "status": 200, "observed_job": "CONTRACT_EVIDENCE_DYNAMIC",
            "rows": [row]}}}), encoding="utf-8")

    # A615: wrapper delegates to canonical importer, never an embedded copy.
    assert "from rc4_contract_import_job import DB, Store, import_capture" in runner.IMPORTER

    def import_fixture(name, primary_rows):
        db = tmp_path / f"{name}.db"
        with sqlite3.connect(db) as connection:
            # Legacy candidate table cannot authenticate PPI identity alone.
            connection.execute("""CREATE TABLE candidate_identity_v2(
              ticker TEXT, instrument_type TEXT, market TEXT, currency TEXT,
              settlement TEXT, status TEXT)""")
            connection.executemany("INSERT INTO candidate_identity_v2 VALUES(?,?,?,?,?,?)", [
                ("LTEST", "LETRAS", "BYMA", currency, "A-24HS", "AVAILABLE")
                for currency in ("ARS", "USD")])
            if primary_rows is not None:
                connection.execute("""CREATE TABLE financial_instrument_catalog(
                  ticker TEXT, instrument_type TEXT, market TEXT, currency TEXT,
                  settlement TEXT, settlement_source TEXT, status TEXT, metadata_json TEXT, last_seen_at TEXT)""")
                connection.executemany(
                    "INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?,?,?,?,?)",
                    [("LTEST", "LETRAS", "BYMA", currency, "A-24HS", source,
                      "AVAILABLE", json.dumps({"_discovery_source": "PPI_PRIMARY",
                       "ticker": "LTEST", "type": "LETRAS", "market": "BYMA",
                       "currency": currency, "settlement": "A-24HS"}),
                      (datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat()) for currency, source in primary_rows])

        # This injection MUST precede exec: never use the default live DB path.
        monkeypatch.setattr(importer, "DB", str(db))
        monkeypatch.setattr("sys.argv", ["-", str(capture)])
        exit_code = 0
        try:
            exec(compile(runner.IMPORTER, "canonical_contract_importer", "exec"),
                 {"__name__": "__main__"})
        except SystemExit as exc:
            exit_code = exc.code
        result = json.loads(capsys.readouterr().out)
        assert exit_code == (0 if result["state"] == "VERDE" and result["records"] > 0 else 1)
        with sqlite3.connect(db) as connection:
            rows = connection.execute("""SELECT currency,evidence_json
              FROM contract_evidence_v2_snapshots WHERE ticker='LTEST'""").fetchall()
            runs = connection.execute("""SELECT state,records FROM contract_evidence_v2_runs
              WHERE job_key='CONTRACT_EVIDENCE_DYNAMIC'""").fetchall()
        assert len(runs) == 1
        assert runs[0][1] == result["records"]
        assert runs[0][0] == result["state"]
        assert result["real_orders_sent"] == 0
        return result, rows

    # 1. Old catalog has two cash variants but no verified PPI source: NO_GO.
    absent, absent_rows = import_fixture("legacy_only", None)
    assert absent_rows == [] and absent["records"] == 0
    assert absent["conflicts"] == 1 and absent["state"] != "VERDE"

    # 2. Even two genuine PPI-primary five-keys remain ambiguous to ticker XHR.
    ambiguous, ambiguous_rows = import_fixture(
        "two_ppi_primary", [("ARS", "PPI_FIELD"),
                            ("USD", "REQUEST_CANDIDATE")])
    assert ambiguous_rows == [] and ambiguous["records"] == 0
    assert ambiguous["conflicts"] == 1 and ambiguous["state"] != "VERDE"

    # 3. With one PPI ARS key + complementary IOL USD, capture exactly ARS.
    unique, unique_rows = import_fixture(
        "one_ppi_plus_iol", [("ARS", "PPI_FIELD"),
                             ("USD", "IOL_COMPLEMENTARY")])
    assert unique["records"] == 1 and unique["conflicts"] == 0
    assert len(unique_rows) == 1 and unique_rows[0][0] == "ARS"
    evidence = json.loads(unique_rows[0][1])
    assert evidence["readiness_guard"] == "NO_AUTO_ACTIVATION_MISSING_FIELDS_REMAIN"
    assert "commission_rate_estimated" not in evidence
    assert "commission_vat_rate_estimated" not in evidence
    assert "market_fee_rate" not in evidence


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
