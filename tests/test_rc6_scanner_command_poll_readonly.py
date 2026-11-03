"""A145: idle scanner command polls never acquire the SQLite writer lease."""

from concurrent.futures import ThreadPoolExecutor

from be_paper_engine import PaperStore
import bf_production_paper_observer as observer


def test_empty_command_queue_does_not_begin_immediate(tmp_path, monkeypatch):
    store = PaperStore(str(tmp_path / "paper.db"))
    observer._support_schema(store)
    statements = []
    original_connect = store.connect

    def observed_connect():
        connection = original_connect()
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(store, "connect", observed_connect)
    assert observer._claim_command(store) is None
    assert not any("BEGIN IMMEDIATE" in stmt.upper() for stmt in statements)
    assert any("SELECT 1 FROM OBSERVER_COMMANDS" in stmt.upper() for stmt in statements)


def test_command_claim_is_exclusive_under_concurrent_pollers(tmp_path):
    store = PaperStore(str(tmp_path / "paper.db"))
    observer._support_schema(store)
    with store.connect() as c:
        c.execute(
            "INSERT INTO observer_commands(created_at,command,status) VALUES(?,?,?)",
            (observer.now_iso(), "GEMINI_PREFLIGHT", "QUEUED"),
        )
        command_id = c.execute("SELECT last_insert_rowid()").fetchone()[0]

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(observer._claim_command, (store, store)))

    assert sum(claim is not None for claim in claims) == 1
    assert next(claim for claim in claims if claim is not None) == (
        command_id, "GEMINI_PREFLIGHT"
    )
    with store.connect() as c:
        assert c.execute(
            "SELECT status FROM observer_commands WHERE id=?", (command_id,)
        ).fetchone()[0] == "RUNNING"
