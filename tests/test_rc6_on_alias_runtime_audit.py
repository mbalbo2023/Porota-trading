"""A188: ON remains a PPI alias, never an extra tradable family.

All data is synthetic and held in an in-memory SQLite database.  This test
never opens a broker route or contacts the Droplet or an external provider.
"""
import ast
import sqlite3
from pathlib import Path


# The host auditor's top-level imports deliberately require the complete
# dashboard runtime; verify its actual pure helper without importing FastAPI,
# a broker, or the remote control plane in this minimal offline test runner.
_SOURCE = (Path(__file__).resolve().parents[1] /
           "scripts/rc6_zero_known_error_runtime_audit.py")
_tree = ast.parse(_SOURCE.read_text(encoding="utf-8"), filename=str(_SOURCE))
_helper = next(node for node in _tree.body
               if isinstance(node, ast.FunctionDef)
               and node.name == "_on_alias_readiness_projection")
_scope = {}
exec(compile(ast.Module(body=[_helper], type_ignores=[]), str(_SOURCE), "exec"), _scope)
_on_alias_readiness_projection = _scope["_on_alias_readiness_projection"]


def _audit(*identities):
    with sqlite3.connect(":memory:") as db:
        db.row_factory = sqlite3.Row
        db.execute("""CREATE TABLE candidate_identity_v2 (
            ticker TEXT, instrument_type TEXT, market TEXT, currency TEXT,
            settlement TEXT, can_simulate INTEGER, status TEXT,
            PRIMARY KEY(ticker,instrument_type,market,currency,settlement)
        )""")
        db.executemany("INSERT INTO candidate_identity_v2 VALUES(?,?,?,?,?,?,?)",
                       identities)
        rows = [dict(row) for row in db.execute("""
            SELECT upper(instrument_type) family,
                   SUM(CASE WHEN can_simulate=1 AND upper(status)='AVAILABLE'
                            THEN 1 ELSE 0 END) ready,
                   COUNT(*) total
            FROM candidate_identity_v2 GROUP BY upper(instrument_type)
        """)]
        original = [dict(row) for row in rows]
        canonical, guard = _on_alias_readiness_projection(db, rows)
        assert rows == original, "Raw audit receipt must remain unmodified"
        return canonical, guard


def _row(ticker, family, ready=0, currency="ARS", settlement="A-24HS"):
    return (ticker, family, "BYMA", currency, settlement,
            ready, "AVAILABLE" if ready else "PAUSED_EXPLICIT")


def test_legacy_on_paused_does_not_hide_canonical_obligaciones_ready():
    canonical, guard = _audit(
        _row("LEGACY", "ON"), _row("YMCJO", "OBLIGACIONES", 1),
        _row("ANOTHER", "OBLIGACIONES"),
    )
    assert canonical == {"OBLIGACIONES": 1}
    assert guard["raw_on_rows"] == 1 and guard["raw_on_ready"] == 0
    assert guard["raw_obligaciones_rows"] == 2
    assert guard["canonical_distinct_rows"] == 3
    assert guard["state"] == "NO_COLLISION_DETECTED"
    assert guard["report_only"] is True
    assert guard["admission_or_database_modified"] is False


def test_same_exact_identity_in_on_and_obligaciones_never_double_counts_ready():
    canonical, guard = _audit(
        _row("YMCJO", "ON", 1), _row("YMCJO", "OBLIGACIONES", 1),
    )
    assert guard["raw_on_ready"] == 1
    assert guard["raw_obligaciones_ready"] == 1
    assert guard["colliding_identity_keys"] == 1
    assert guard["colliding_ready_rows_held"] == 2
    assert guard["redundant_alias_rows"] == 1
    assert guard["canonical_distinct_rows"] == 1
    assert guard["state"] == "ALIAS_COLLISION_HELD"
    assert canonical["OBLIGACIONES"] == 0


def test_paused_legacy_collision_does_not_certify_canonical_ready():
    canonical, guard = _audit(
        _row("YMCJO", "ON"), _row("YMCJO", "OBLIGACIONES", 1),
        _row("CLEAN", "OBLIGACIONES", 1),
    )
    assert guard["colliding_identity_keys"] == 1
    assert guard["colliding_ready_rows_held"] == 1
    assert canonical["OBLIGACIONES"] == 1


def test_market_currency_and_settlement_are_distinct_monetary_identities():
    canonical, guard = _audit(
        _row("YMCJO", "ON", 1, currency="ARS"),
        _row("YMCJO", "OBLIGACIONES", 1, currency="USD"),
        _row("YMCJO", "OBLIGACIONES", 1, settlement="INMEDIATA"),
    )
    assert guard["colliding_identity_keys"] == 0
    assert canonical["OBLIGACIONES"] == 3
    assert guard["canonical_distinct_rows"] == 3


def test_no_on_alias_does_not_change_unrelated_family_totals():
    canonical, guard = _audit(
        _row("GGAL", "ACCIONES", 1),
        _row("AL30", "BONOS", 1),
    )
    assert canonical == {"ACCIONES": 1, "BONOS": 1}
    assert guard["raw_on_rows"] == guard["raw_obligaciones_rows"] == 0
    assert guard["canonical_ready_fail_closed"] == 0
