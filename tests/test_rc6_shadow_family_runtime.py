"""Family runtime fixtures exercise the bridge that the observer calls."""
import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone

import pytest

from rc6_shadow_runtime.families import family_reports


AT = datetime(2026, 10, 2, 15, 4, tzinfo=timezone.utc)


def instrument(ticker, family, *, settlement="A-24HS", currency="ARS", market="BYMA"):
    return {"ticker": ticker, "instrument_type": family, "market": market,
            "currency": currency, "settlement": settlement, "status": "AVAILABLE",
            "capability": "READY_PAPER_SPOT" if family in {"ACCIONES", "CEDEARS", "ETFS"} else "NEEDS_SPECIALIZED_CONTRACT"}


def clocked(value, *, source_at=AT, received_at=AT, source="FIXTURE_PROVIDER"):
    return {"value": value, "source": source, "source_at": source_at.isoformat(), "received_at": received_at.isoformat()}


def fixture_db(tmp_path, catalog):
    path = tmp_path / "observer.db"
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE observer_state(id INTEGER PRIMARY KEY,mode TEXT,real_orders_sent INTEGER);
        INSERT INTO observer_state VALUES(1,'PRODUCTION_PAPER',0);
        CREATE TABLE financial_instrument_catalog(ticker TEXT,instrument_type TEXT,market TEXT,
            currency TEXT,settlement TEXT,status TEXT,capability TEXT,last_seen_at TEXT,metadata_json TEXT);
        CREATE TABLE market_snapshots(id INTEGER PRIMARY KEY,source TEXT,symbol TEXT,asset_class TEXT,
            market TEXT,currency TEXT,settlement TEXT,last TEXT,bid TEXT,ask TEXT,bid_size TEXT,
            ask_size TEXT,observed_at TEXT,trade_at TEXT,book_at TEXT,contract_json TEXT);
        CREATE TABLE paper_equity_by_currency(id INTEGER PRIMARY KEY,measured_at TEXT,currency TEXT,cash TEXT);
        CREATE TABLE untouched_lifecycle(id INTEGER PRIMARY KEY,status TEXT);
        INSERT INTO untouched_lifecycle VALUES(1,'OPEN');
    """)
    for row in catalog:
        connection.execute("INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?,?,?,?,?)",
            (*[row[key] for key in ("ticker", "instrument_type", "market", "currency", "settlement", "status", "capability")], AT.isoformat(), "{}"))
    connection.execute("INSERT INTO paper_equity_by_currency VALUES(1,?,?,?)", (AT.isoformat(), "ARS", "500000"))
    connection.commit()
    connection.close()
    return path


def snapshot(path, record, *, price=100, bid=99.95, ask=100.05, bid_size=100, ask_size=100,
             source_at=AT, received_at=AT, book_at=None):
    contract = {"family": record["instrument_type"], "cash_multiplier": "1", "metadata_source": "VERIFIED_EQUITY_SPOT_POLICY"}
    if record["instrument_type"] not in {"ACCIONES", "CEDEARS", "ETFS"}:
        contract = {}
    connection = sqlite3.connect(path)
    connection.execute("""INSERT INTO market_snapshots(source,symbol,asset_class,market,currency,settlement,
        last,bid,ask,bid_size,ask_size,observed_at,trade_at,book_at,contract_json)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", ("PPI", record["ticker"], record["instrument_type"],
        record["market"], record["currency"], record["settlement"], price,bid,ask,bid_size,ask_size,
        received_at.isoformat(), source_at.isoformat(), (book_at or source_at).isoformat(), json.dumps(contract)))
    connection.commit()
    connection.close()


def by_ticker(report):
    return {row["identity"][0]: row for row in report["instruments"]}


def source_row(record, **fields):
    return {**record, "source": "FIXTURE_PROVIDER", "source_at": AT.isoformat(),
            "received_at": AT.isoformat(), **fields}


def test_native_family_rows_preserve_all_sqlite_columns_types_and_cutoff(tmp_path):
    from rc6_shadow_runtime import families
    record = instrument("GGAL", "ACCIONES")
    path = fixture_db(tmp_path, [record])
    snapshot(path, record)
    snapshot(path, record, received_at=AT+timedelta(seconds=1))
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute('ALTER TABLE financial_instrument_catalog ADD COLUMN "extra bytes" BLOB')
        connection.execute('ALTER TABLE financial_instrument_catalog ADD COLUMN extra_real REAL')
        connection.execute('ALTER TABLE financial_instrument_catalog ADD COLUMN extra_null TEXT')
        connection.execute('UPDATE financial_instrument_catalog SET "extra bytes"=?, extra_real=?',
                           (b"\x00\xffnative", 1.125))
        connection.row_factory = sqlite3.Row
        raw_metadata = dict(connection.execute('SELECT * FROM financial_instrument_catalog').fetchone())
        raw_quote = dict(connection.execute('SELECT * FROM market_snapshots WHERE id=1').fetchone())
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    actual = families._read(path, AT)
    assert len(actual["metadata"]) == len(actual["quotes"]) == 1
    assert {key: actual["metadata"][0][key] for key in raw_metadata} == raw_metadata
    assert {key: actual["quotes"][0][key] for key in raw_quote} == raw_quote
    assert actual["metadata"][0]["metadata"] == {}
    assert actual["metadata"][0]["observed_at"] == AT.isoformat()
    assert actual["quotes"][0]["received_at"] == AT.isoformat()
    assert actual["quotes"][0]["source_at"] == AT.isoformat()
    assert not actual["errors"] and not actual["truncated"]
    assert families.QUERY_BUDGET_SECONDS == .5
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_native_family_fixture_writer_is_closed_before_actual_read_without_gc(tmp_path, monkeypatch):
    """Hold every producer connection strongly across the real read boundary."""
    from rc6_shadow_runtime import families
    original_connect = sqlite3.connect
    original_capture = families._read
    connections, captured = [], []
    def tracked_connect(*args, **kwargs):
        connection = original_connect(*args, **kwargs)
        connections.append(connection)
        return connection
    def assert_closed():
        assert len(connections) >= 4  # Catalog, two snapshots, fixture DDL/write.
        for connection in connections:
            with pytest.raises(sqlite3.ProgrammingError, match="closed"):
                connection.execute("SELECT 1")
    def capture(*args, **kwargs):
        assert_closed()
        captured.append(True)
        return original_capture(*args, **kwargs)
    monkeypatch.setattr(sqlite3, "connect", tracked_connect)
    monkeypatch.setattr(families, "_read", capture)
    test_native_family_rows_preserve_all_sqlite_columns_types_and_cutoff(tmp_path)
    assert captured == [True]
    assert_closed()


def test_empty_native_metadata_remains_private_to_each_instrument(tmp_path):
    from rc6_shadow_runtime import families
    path = fixture_db(tmp_path, [instrument("GGAL", "ACCIONES"), instrument("BBAR", "ACCIONES")])
    rows = families._read(path, AT)["metadata"]
    assert len(rows) == 2 and rows[0]["metadata"] == rows[1]["metadata"] == {}
    rows[0]["metadata"]["private_change"] = True
    assert rows[1]["metadata"] == {}


@pytest.mark.parametrize("as_of", [AT-timedelta(seconds=15), AT, AT+timedelta(minutes=5)])
def test_all_native_family_outputs_match_eager_reference_without_discarded_empty_quote_reads(tmp_path, monkeypatch, as_of):
    import ast
    import inspect
    from rc6_shadow_runtime import families
    # Reproduce precisely the preceding eager lookup, including all other
    # native handler logic, to compare every clock/field/identity and absence.
    source = ast.parse(inspect.getsource(families.family_reports))
    class EagerLookup(ast.NodeTransformer):
        changed = 0
        def visit_IfExp(self, node):
            self.generic_visit(node)
            if (isinstance(node.body, ast.Subscript) and isinstance(node.body.value, ast.Name)
                    and node.body.value.id == "quotes" and isinstance(node.orelse, ast.Call)
                    and isinstance(node.orelse.func, ast.Name) and node.orelse.func.id == "_quote"):
                self.changed += 1
                return ast.copy_location(ast.Call(func=ast.Attribute(value=ast.Name(id="quotes", ctx=ast.Load()),
                    attr="get", ctx=ast.Load()), args=[node.body.slice, node.orelse], keywords=[]), node)
            return node
    transform = EagerLookup(); transform.visit(source)
    assert transform.changed == 3
    namespace = dict(families.__dict__)
    exec(compile(ast.fix_missing_locations(source), "<prior-eager-family-lookup>", "exec"), namespace)
    catalog = [instrument("S"+str(index), family, market="A3" if family == "FUTUROS" else "BYMA")
               for index, family in enumerate(("ACCIONES", "CEDEARS", "ETFS", "BONOS", "LETRAS",
                    "OBLIGACIONES", "OPCIONES", "FUTUROS", "CAUCIONES", "FCI"))]
    path = fixture_db(tmp_path, catalog)
    for record in catalog:
        snapshot(path, record)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    calls = []
    original = families._quote
    def checked(rows, at):
        calls.append(len(rows))
        return original(rows, at)
    namespace["_quote"] = checked
    expected = namespace["family_reports"](path, as_of=as_of, catalog=catalog)
    assert calls.count(0) == 7
    calls.clear()
    monkeypatch.setattr(families, "_quote", checked)
    actual = families.family_reports(path, as_of=as_of, catalog=catalog)
    assert actual == expected
    assert calls.count(0) == 0 and len(calls) == 9
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_dispatches_all_ten_families_with_specialized_evidence_and_no_database_mutation(tmp_path):
    catalog = [instrument("GGAL", "ACCIONES"), instrument("AAPL", "CEDEARS"), instrument("SPY", "ETFS"),
               instrument("AL30", "BONOS"), instrument("S31O6", "LETRAS"), instrument("YMCJO", "OBLIGACIONES"),
               instrument("GGALC100", "OPCIONES", settlement="INMEDIATA"), instrument("DLR/OCT26", "FUTUROS", market="A3"),
               instrument("CAU3", "CAUCIONES", settlement="INMEDIATA"), instrument("FUND1", "FCI")]
    path = fixture_db(tmp_path, catalog)
    for record in catalog[:6]:
        snapshot(path, record)
    snapshot(path, catalog[6], price=2, bid=2, ask=2.01, bid_size=5, ask_size=5)
    rows = [source_row(catalog[1], ratio=clocked(10), ccl=clocked(1350), local_divergence=clocked(-.2))]
    for record in catalog[3:6]:
        rows.append(source_row(record, quote_basis_nominal=clocked(100), quantity_step_nominal=clocked(1),
            minimum_nominal=clocked(1), cash_multiplier=clocked(.01),
            yield_to_maturity=clocked(-.01), duration=clocked(2.1), parity=clocked(.9), carry=clocked(.03),
            flows=clocked([{"payment_at": (AT+timedelta(days=30)).isoformat(), "amount": 105, "currency": "ARS"}])))
    rows.append(source_row(catalog[6], underlying=clocked("GGAL"), strike=clocked(100), option_right=clocked("CALL"),
        expires_at=clocked((AT+timedelta(days=10)).isoformat()), strike_unit=clocked("PER_SHARE"),
        iv=clocked(.3), greeks=clocked({"delta": .51, "gamma": .02, "theta": -.03}), open_interest=clocked(40)))
    rows.append(source_row(catalog[8], annual_rate_fraction=clocked(.25), term_days=clocked(3),
        available_principal=clocked(1_000_000), maturity_at=clocked((AT+timedelta(days=3)).isoformat())))
    rows.append(source_row(catalog[9], nav=clocked(10.1), nav_date=clocked(AT.date().isoformat()),
        subscription_status=clocked("OPEN"), redemption_term=clocked("T+1"), cutoff_time=clocked("15:00 ART")))
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    report = family_reports(path, as_of=AT, catalog=catalog, sources={"family_evidence": rows})
    instruments = by_ticker(report)
    assert report["catalog_preserved"] and report["catalog_count"] == 10
    assert report["safety"]["real_orders_sent"] == 0
    assert report["safety"]["lifecycle_mutations"] == 0
    assert report["evidence_read"]["query_only"] is True
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert all(not row["entry_authority"] and row["status"] == "OBSERVE_ONLY" for row in report["instruments"])
    assert instruments["GGAL"]["generic_equity"] and instruments["SPY"]["generic_equity"]
    cedear = instruments["AAPL"]["handler_result"]["cedear_features"]
    assert cedear["underlying_us_session"]["phase"] == "OPEN"
    assert cedear["ratio"]["value"] == 10 and cedear["ccl"]["value"] == 1350
    assert cedear["local_divergence"]["value"] == -.2
    for ticker in ("AL30", "S31O6", "YMCJO"):
        row = instruments[ticker]
        assert row["engine"] == "FIXED_INCOME_ANALYTICS" and not row["generic_equity"]
        assert row["cadence_seconds"] == 300
        assert row["handler_result"]["nominal_contract_status"] == "VERIFIED_NOMINAL_CONTRACT"
        assert row["handler_result"]["cash_per_nominal"] == 1
        assert row["handler_result"]["analytics"]["yield"]["value"] == -.01
        assert len(row["handler_result"]["analytics"]["flows"]["value"]) == 1
    option = instruments["GGALC100"]
    assert option["cadence_seconds"] == 60 and option["handler_result"]["structurally_selected"]
    assert report["option_observation_universe"]["selected"][0]["open_interest"]["value"] == 40
    assert report["option_observation_universe"]["selected"][0]["greeks"]["value"]["theta"] == -.03
    delegated = instruments["DLR/OCT26"]["handler_result"]
    assert delegated["owner_issue"] == 453 and delegated["lifecycle_called"] is False
    caucion = instruments["CAU3"]["handler_result"]
    assert instruments["CAU3"]["cadence_seconds"] is None
    assert caucion["trigger"] == "CASH_RATE_TERM_OR_MATURITY_EVENT" and caucion["cash"]["value"] == 500000
    assert not caucion["intraday_equity_scanner"]
    fci = instruments["FUND1"]["handler_result"]
    assert instruments["FUND1"]["cadence_seconds"] == 86400
    assert fci["terms"]["nav"]["value"] == 10.1 and not fci["intraday_equity_scanner"]
    json.dumps(report, allow_nan=False)


def test_missing_or_stale_specialized_evidence_never_falls_back_to_equity(tmp_path):
    catalog = [instrument("AAPL", "CEDEARS"), instrument("AL30", "BONOS"), instrument("FUND1", "FCI")]
    path = fixture_db(tmp_path, catalog)
    snapshot(path, catalog[0])
    snapshot(path, catalog[1])
    rows = [source_row(catalog[0], ratio=clocked(10), ccl=clocked(1200, source_at=AT-timedelta(hours=1)),
                       local_divergence=clocked(.1, received_at=AT+timedelta(seconds=1))),
            source_row(catalog[1], cash_multiplier=clocked(.01), yield_to_maturity=clocked(.05, source_at=AT-timedelta(minutes=10)))]
    report = family_reports(path, as_of=AT, catalog=catalog, sources={"family_evidence": rows})
    instruments = by_ticker(report)
    cedear = instruments["AAPL"]["handler_result"]["cedear_features"]
    assert cedear["ratio"]["value"] == 10
    assert cedear["ccl"]["status"] == cedear["local_divergence"]["status"] == "NO_VERIFICADO"
    fixed = instruments["AL30"]["handler_result"]
    assert fixed["nominal_contract_status"] == "NO_VERIFICADO"
    assert fixed["cash_per_nominal"] is None and fixed["analytics"]["yield"]["value"] is None
    assert not instruments["AL30"]["generic_equity"] and not instruments["FUND1"]["generic_equity"]


def test_source_capture_is_not_provider_freshness_and_future_revision_is_unavailable(tmp_path):
    catalog = [instrument("AL30", "BONOS"), instrument("GGAL", "ACCIONES")]
    path = fixture_db(tmp_path, catalog)
    connection = sqlite3.connect(path)
    connection.execute("UPDATE financial_instrument_catalog SET last_seen_at=?, metadata_json=? WHERE ticker='AL30'",
        ((AT+timedelta(seconds=1)).isoformat(), json.dumps({"financial_contract_v17": {
            "cash_multiplier": ".01", "fixed_income_evidence": {"quote_basis_nominal": "100"},
            "quantity_step": "1", "minimum_quantity": "1", "metadata_source": "PPI"}})))
    connection.commit()
    connection.close()
    sources = {"BYMA": {"observed_at": AT.isoformat(), "records": [
        {**catalog[1], "last": 100, "price_unit": "PER_SHARE", "bid": 99, "ask": 101,
         "bid_size": 100, "ask_size": 100}]}}
    instruments = by_ticker(family_reports(path, as_of=AT, catalog=catalog, sources=sources))
    assert instruments["AL30"]["handler_result"]["terms"]["quote_basis_nominal"]["value"] is None
    quote = instruments["GGAL"]["handler_result"]["quote"]
    assert quote["status"] == "NO_VERIFICADO" and quote["price"] is None and not quote["tradeable"]


def test_options_are_selected_by_underlying_expiry_moneyness_and_own_ancillary_clocks(tmp_path):
    catalog = [instrument("GGAL", "ACCIONES"), instrument("Z_RELEVANT", "OPCIONES", settlement="INMEDIATA"),
               instrument("A_FAR", "OPCIONES", settlement="INMEDIATA"), instrument("B_EXPIRED", "OPCIONES", settlement="INMEDIATA")]
    path = fixture_db(tmp_path, catalog)
    snapshot(path, catalog[0])
    rows = []
    for record, strike, expiry in ((catalog[1], 100, AT+timedelta(days=10)),
                                  (catalog[2], 200, AT+timedelta(days=10)), (catalog[3], 100, AT)):
        snapshot(path, record, price=2, bid=2, ask=2.01, bid_size=5, ask_size=5)
        rows.append(source_row(record, underlying=clocked("GGAL"), strike=clocked(strike), option_right=clocked("CALL"),
            expires_at=clocked(expiry.isoformat()), strike_unit=clocked("PER_SHARE"),
            iv=clocked(.25, received_at=AT+timedelta(seconds=1)),
            open_interest=clocked(999, source_at=AT-timedelta(minutes=10)),
            greeks=clocked({"delta": .5, "authorization": "excluded"})))
    report = family_reports(path, as_of=AT, catalog=catalog, sources={"family_evidence": rows})
    selected = report["option_observation_universe"]["selected"]
    excluded = {row["identity"][0]: row["reason_codes"] for row in report["option_observation_universe"]["excluded"]}
    assert [row["identity"][0] for row in selected] == ["Z_RELEVANT"]
    assert selected[0]["iv"]["status"] == selected[0]["open_interest"]["status"] == "NO_VERIFICADO"
    assert selected[0]["greeks"]["value"] == {"delta": .5}
    assert excluded["A_FAR"] == ["OPTION_OUTSIDE_ECONOMIC_ZONE"]
    assert excluded["B_EXPIRED"] == ["OPTION_EXPIRED"]
    assert report["option_observation_universe"]["blind_round_robin"] is False


def test_new_invalid_book_invalidates_previous_receipt_and_non_equity_has_no_scanner(tmp_path):
    catalog = [instrument("GGAL", "ACCIONES"), instrument("CAU3", "CAUCIONES"), instrument("FUND1", "FCI")]
    path = fixture_db(tmp_path, catalog)
    snapshot(path, catalog[0], source_at=AT-timedelta(seconds=30), received_at=AT-timedelta(seconds=20))
    snapshot(path, catalog[0], bid=0, ask=0, bid_size=0, ask_size=0)
    instruments = by_ticker(family_reports(path, as_of=AT, catalog=catalog))
    assert instruments["GGAL"]["handler_result"]["quote"]["book_status"] == "NO_VERIFICADO"
    assert not instruments["GGAL"]["handler_result"]["quote"]["tradeable"]
    assert not instruments["CAU3"]["handler_result"]["intraday_equity_scanner"]
    assert not instruments["FUND1"]["handler_result"]["intraday_equity_scanner"]


def test_paper_guard_and_invalid_identity_are_explicit(tmp_path):
    record = instrument("GGAL", "ACCIONES")
    path = fixture_db(tmp_path, [record])
    unknown = {"ticker": "GAP", "instrument_type": "UNKNOWN", "market": "UNKNOWN", "currency": "UNKNOWN", "settlement": "UNKNOWN"}
    report = family_reports(path, as_of=AT, catalog=[record, {"ticker": "BAD"}, unknown])
    assert report["catalog_count"] == 3 and report["catalog_preserved"]
    assert report["instruments"][1]["reason_codes"] == ["EXACT_IDENTITY_REQUIRED"]
    assert report["instruments"][2]["reason_codes"] == ["EXACT_IDENTITY_REQUIRED"]
    assert report["instruments"][2]["observation_status"] == "EXCLUDED_IDENTITY"
    connection = sqlite3.connect(path)
    connection.execute("UPDATE observer_state SET real_orders_sent=1")
    connection.commit()
    connection.close()
    with pytest.raises(ValueError, match="PAPER_SAFETY_REQUIRED"):
        family_reports(path, as_of=AT, catalog=[record])


def test_observation_read_is_bounded_and_reports_partial_coverage(tmp_path):
    record = instrument("GGAL", "ACCIONES")
    path = fixture_db(tmp_path, [record])
    connection = sqlite3.connect(path)
    connection.executemany("""INSERT INTO market_snapshots(source,symbol,asset_class,market,currency,settlement,
        last,bid,ask,bid_size,ask_size,observed_at,trade_at,book_at,contract_json)
        VALUES('PPI','GGAL','ACCIONES','BYMA','ARS','A-24HS',100,99.9,100.1,100,100,?,?,?,'{}')""",
        [(AT.isoformat(), AT.isoformat(), AT.isoformat())]*5001)
    connection.commit()
    connection.close()
    report = family_reports(path, as_of=AT, catalog=[record])
    assert report["catalog_preserved"]
    assert report["evidence_read"]["truncated"] == ["market_snapshots"]


def test_existing_iol_family_reference_is_bound_by_contract_and_capture_cannot_validate_nav_or_rates(tmp_path):
    catalog = [instrument("AL30", "BONOS"), instrument("CAU3", "CAUCIONES"), instrument("FUND1", "FCI")]
    path = fixture_db(tmp_path, catalog)
    snapshot(path, catalog[0])
    reference = {"refreshed_at": AT.isoformat(), "records": [{**catalog[0], "observed_at": AT.isoformat(),
        "financial_contract_v17": {"family": "BONOS", "cash_multiplier": ".01", "quantity_step": "1", "minimum_quantity": "1",
            "metadata_source": "IOL_ASSET_INFO+IOL_QUOTE_PRICE_BASES+IOL_FIXED_INCOME_SIMULATION_1_NOMINAL",
            "fixed_income_evidence": {"quote_basis_nominal": "100"}},
        "fixed_income_analytics": {"dirty_price": 90, "yield": .1}}],
        "fci": [{"asset": "FUND1", "unit_value": 10.1}], "cauciones": {"ARS": [{"term": 3, "rate": 25}]},
        "section_observed_at": {"fci": AT.isoformat(), "caucion:ARS": AT.isoformat()},
        "section_states": {"fci": "LIVE_FRESH", "caucion:ARS": "LIVE_FRESH"}}
    report = family_reports(path, as_of=AT, catalog=catalog, sources={"IOL_FAMILY_REFERENCE": reference})
    instruments = by_ticker(report)
    assert instruments["AL30"]["handler_result"]["nominal_contract_status"] == "VERIFIED_NOMINAL_CONTRACT"
    assert instruments["AL30"]["handler_result"]["analytics"]["yield"]["status"] == "NO_VERIFICADO"
    families = {row["family"]: row for row in report["families"]}
    assert families["FCI"]["reference_evidence"]["inventory_count"] == 1
    assert families["FCI"]["reference_evidence"]["nav_freshness"] == "NO_VERIFICADO"
    caucion_reference = families["CAUCIONES"]["reference_evidence"]["currency_references"][0]
    assert caucion_reference["rate_term_reference_count"] == 1
    assert caucion_reference["rate_freshness"] == "NO_VERIFICADO"
    assert instruments["FUND1"]["handler_result"]["terms"]["nav"]["value"] is None
    reference["section_observed_at"]["fci"] = (AT+timedelta(seconds=1)).isoformat()
    later_capture = family_reports(path, as_of=AT, catalog=catalog, sources={"IOL_FAMILY_REFERENCE": reference})
    assert next(row for row in later_capture["families"] if row["family"] == "FCI")["reference_evidence"]["inventory_count"] is None


def test_real_catalog_standard_option_contract_proves_strike_dimension_without_ticker_parsing(tmp_path):
    catalog = [instrument("GGAL", "ACCIONES"), instrument("NON_PARSEABLE_OPTION", "OPCIONES", settlement="INMEDIATA")]
    path = fixture_db(tmp_path, catalog)
    snapshot(path, catalog[0])
    snapshot(path, catalog[1], price=2, bid=2, ask=2.01, bid_size=5, ask_size=5)
    contract = {"family": "OPCIONES", "currency": "ARS", "market": "BYMA", "settlement": "INMEDIATA",
        "cash_multiplier": "100", "quantity_step": "1", "minimum_quantity": "1", "underlying": "GGAL",
        "strike": "100", "expires_at": (AT+timedelta(days=10)).isoformat(), "option_right": "CALL",
        "metadata_source": "PPI_SEARCH_INSTRUMENT_DESCRIPTION+BYMA_OPTION_CONTRACT_2026+POROTA_PAPER_ONE_CONTRACT_POLICY:v1"}
    connection = sqlite3.connect(path)
    connection.execute("UPDATE financial_instrument_catalog SET metadata_json=? WHERE ticker=?",
        (json.dumps({"financial_contract_v17": contract}), catalog[1]["ticker"]))
    connection.commit()
    connection.close()
    report = family_reports(path, as_of=AT, catalog=catalog)
    option = by_ticker(report)[catalog[1]["ticker"]]["handler_result"]
    assert option["terms"]["strike_unit"]["value"] == "PER_SHARE"
    assert option["structurally_selected"]
    connection = sqlite3.connect(path)
    connection.execute("UPDATE financial_instrument_catalog SET metadata_json=? WHERE ticker=?",
        (json.dumps({"financial_contract_v17": contract, "_contract_conflicts": {"fields": ["strike"]}}), catalog[1]["ticker"]))
    connection.commit()
    connection.close()
    conflicted = by_ticker(family_reports(path, as_of=AT, catalog=catalog))[catalog[1]["ticker"]]["handler_result"]
    assert not conflicted["structurally_selected"]
    assert conflicted["reason_codes"] == ["CONTRACT_CONFLICT_NO_VERIFICADO"]


def test_newer_complement_publication_does_not_reuse_older_ppi_catalog_clock(tmp_path):
    record = instrument("AL30", "BONOS")
    path = fixture_db(tmp_path, [record])
    connection = sqlite3.connect(path)
    connection.execute("UPDATE financial_instrument_catalog SET metadata_json=?", (json.dumps({
        "_effective_observed_at": (AT+timedelta(seconds=1)).isoformat(),
        "financial_contract_v17": {"family": "BONOS", "cash_multiplier": ".01", "quantity_step": "1", "minimum_quantity": "1",
            "metadata_source": "IOL_COMPLEMENTARY", "fixed_income_evidence": {"quote_basis_nominal": "100"}}}),))
    connection.commit()
    connection.close()
    fixed = by_ticker(family_reports(path, as_of=AT, catalog=[record]))["AL30"]["handler_result"]
    assert fixed["nominal_contract_status"] == "NO_VERIFICADO"
    assert fixed["terms"]["quote_basis_nominal"]["value"] is None


def test_independent_feature_clocks_cannot_borrow_fresh_parent_quote_clocks(tmp_path):
    record = instrument("AAPL", "CEDEARS")
    path = fixture_db(tmp_path, [record])
    snapshot(path, record)
    fields = {"cedear_features": {
        "ratio": {"value": 10, "source": "RATIO_REGISTER", "observed_at": AT.isoformat(), "published_at": AT.isoformat()},
        "ccl": {"value": 1300, "source": "FX_PROVIDER", "observed_at": AT.isoformat(),
                "published_at": (AT+timedelta(seconds=1)).isoformat()},
        "local_divergence": {"value": .5, "source": "MODEL", "observed_at": (AT+timedelta(seconds=1)).isoformat(),
                             "published_at": AT.isoformat()}}}
    rows = [source_row(record, **fields)]
    features = by_ticker(family_reports(path, as_of=AT, catalog=[record], sources={"family_evidence": rows}))["AAPL"]["handler_result"]["cedear_features"]
    assert features["ratio"]["value"] == 10
    assert features["ccl"]["status"] == features["local_divergence"]["status"] == "NO_VERIFICADO"
