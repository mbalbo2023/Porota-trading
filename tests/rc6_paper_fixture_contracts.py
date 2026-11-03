"""Minimal empty-paper ledgers for archived host-transition incident fixtures.

The family table DDL mirrors the productive paper lifecycle parent table. A
missing FCI schema must remain a *production* hard RED; these older fixtures
model a flat book with an initialized, empty FCI/FUTUROS parent ledger.
"""


def create_empty_family_parent_ledger(connection):
    connection.execute("""CREATE TABLE paper_family_lifecycle(
          lifecycle_id TEXT PRIMARY KEY,
          family TEXT NOT NULL,
          instrument TEXT NOT NULL,
          currency TEXT NOT NULL,
          state TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          ledger_total TEXT NOT NULL DEFAULT '0',
          metadata_json TEXT NOT NULL DEFAULT '{}'
        )""")
