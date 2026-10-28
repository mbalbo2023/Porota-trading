import sqlite3

import bf_production_paper_observer as observer


def test_history_coverage_numerator_uses_same_scope_as_denominator():
    connection = sqlite3.connect(":memory:")
    connection.execute(
        """CREATE TABLE production_history(
               symbol TEXT, instrument_type TEXT, settlement TEXT, row_count INTEGER
           )"""
    )
    connection.executemany(
        "INSERT INTO production_history VALUES(?,?,?,?)",
        [
            ("AL30", "BONOS", "A-24HS", 10),
            ("GD30", "BONOS", "A-24HS", 10),
            ("LEGACY", "ACCIONES", "INMEDIATA", 10),
        ],
    )
    targets = [
        ("AL30", "BONOS", "A-24HS"),
        ("GD30", "BONOS", "A-24HS"),
    ]
    covered = observer._history_covered_target_count(connection, targets)
    assert covered == 2
    assert covered <= len(targets)


def test_history_coverage_ignores_unrelated_legacy_rows():
    connection = sqlite3.connect(":memory:")
    connection.execute(
        """CREATE TABLE production_history(
               symbol TEXT, instrument_type TEXT, settlement TEXT, row_count INTEGER
           )"""
    )
    connection.executemany(
        "INSERT INTO production_history VALUES(?,?,?,?)",
        [
            ("OLD1", "ACCIONES", "INMEDIATA", 10),
            ("OLD2", "CEDEARS", "A-24HS", 10),
        ],
    )
    targets = [("AL30", "BONOS", "A-24HS")]
    assert observer._history_covered_target_count(connection, targets) == 0


def test_ppi_history_request_rejects_other_known_monetary_or_market_identity():
    connection = sqlite3.connect(":memory:")
    connection.execute("""CREATE TABLE financial_instrument_catalog(
        ticker TEXT, instrument_type TEXT, market TEXT, currency TEXT,
        settlement TEXT, status TEXT,
        PRIMARY KEY(ticker,instrument_type,market,currency,settlement))""")
    connection.executemany("INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?,?)", [
        ("AL30", "BONOS", "BYMA", "ARS", "A-24HS", "AVAILABLE"),
        ("AL30", "BONOS", "BYMA", "USD", "A-24HS", "AVAILABLE"),
        ("AL30", "BONOS", "OTRO", "ARS", "INMEDIATA", "AVAILABLE"),
    ])
    identity=("AL30", "BONOS", "BYMA", "ARS", "A-24HS")
    assert observer._ppi_history_request_is_ambiguous(connection,identity)
    connection.execute("UPDATE financial_instrument_catalog SET status='STALE' WHERE currency='USD'")
    assert observer._ppi_history_request_is_ambiguous(connection,identity)
    connection.execute("DELETE FROM financial_instrument_catalog WHERE currency='USD'")
    assert not observer._ppi_history_request_is_ambiguous(connection,identity)
    connection.execute("INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?,?)",
                       ("AL30", "BONOS", "OTRO", "ARS", "A-24HS", "AVAILABLE"))
    assert observer._ppi_history_request_is_ambiguous(connection,identity)
