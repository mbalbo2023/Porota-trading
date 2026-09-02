"""Adapter HF6-v2 para mantener el histórico fuera de observer_v17.db."""
from __future__ import annotations

import os
import sqlite3

import al_historical_ingest as legacy_history


class HistoricalStore:
    """SQLite store dedicado a series de mercado, con WAL y timeout."""

    def __init__(self, path: str | None = None):
        self.path = path or os.getenv("HIST_DB_PATH", legacy_history.HIST_DB_PATH)

    def connect(self) -> sqlite3.Connection:
        folder = os.path.dirname(self.path) or "."
        os.makedirs(folder, exist_ok=True)
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.execute("PRAGMA busy_timeout=30000")
        return c


def default_history_store() -> HistoricalStore:
    return HistoricalStore()
