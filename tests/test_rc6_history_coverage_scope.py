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
