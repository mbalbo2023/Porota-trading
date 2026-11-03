"""Last-mile, SQLite-locked safety barrier for RC6 PAPER entry writers.

An operator preopen receipt authorizes no real order and is not a substitute
for checking the effective PAPER ledger.  New simulated positions must agree
with the ledger's mode, real-order counter and dataset provenance in the same
transaction that records the financial event.  Offline test stores are not
promoted into the operational dataset merely because they have PAPER tables.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path


def runtime_admission_context(store, *, connection=None):
    """Return ``(is_runtime, error)``; uncertainty fails closed.

    The canonical workspace path enforces the one-link SQLite identity rule.
    Comparing inodes in addition to resolved paths prevents hard-link aliases
    from masquerading as independent fixture databases.  Callers must pass
    their BEGIN IMMEDIATE connection for the final entry admission.
    """
    try:
        from cg_paper_workspace import checked_path, database_path, identity_from_connection

        configured = database_path()
        # Check the *requested* path as well as the configured path: an
        # independent offline fixture is fine, but a symlink/hardlink alias
        # can never silently opt out of the effective runtime admission gate.
        requested = checked_path(Path(store.path))
        is_runtime = (requested == configured or
                      (requested.exists() and configured.exists() and
                       os.path.samefile(requested, configured)))
        if not is_runtime or connection is None:
            return is_runtime, ""
        if connection.in_transaction is not True:
            return True, "PAPER_RUNTIME_WRITE_LOCK_REQUIRED"
        # The decision and the eventual INSERT share one SQLite write lock.
        # Do not trust an old read-only state receipt from another connection.
        rows = connection.execute(
            "SELECT mode,real_orders_sent FROM observer_state WHERE id=1"
        ).fetchall()
        if (len(rows) != 1 or rows[0][0] != "PRODUCTION_PAPER" or
                type(rows[0][1]) is not int or rows[0][1] != 0):
            return True, "PAPER_RUNTIME_STATE_NOT_SAFE"
        try:
            identity_from_connection(connection)
        except ValueError:
            return True, "PAPER_RUNTIME_DATASET_UNTRUSTED"
        return True, ""
    except (OSError, ValueError, TypeError, AttributeError, KeyError,
            sqlite3.Error):
        return True, "PAPER_RUNTIME_IDENTITY_UNAVAILABLE"
