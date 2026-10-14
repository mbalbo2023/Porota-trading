"""Adapter HF6-v2 para mantener el histórico fuera de observer_v17.db."""
from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import stat

import al_historical_ingest as legacy_history


class _OwnedConnection(sqlite3.Connection):
    def __exit__(self, *exception):
        try:
            return super().__exit__(*exception)
        finally:
            self.close()


class HistoricalStore:
    """SQLite store dedicado a series de mercado, con WAL y timeout."""

    def __init__(self, path: str | None = None):
        self.path = path or os.getenv("HIST_DB_PATH", legacy_history.HIST_DB_PATH)
        self._initialized_identity = None
        self._prepared_identity = None

    def _path_identity(self):
        path = Path(self.path).absolute()
        if path.resolve() != path or any(parent.is_symlink() for parent in path.parents):
            raise ValueError('HISTORY_STORE_ALIAS_FORBIDDEN')
        try:
            info = path.lstat()
        except FileNotFoundError:
            return path, None
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('HISTORY_STORE_ALIAS_FORBIDDEN')
        for suffix in ('-wal','-shm','-journal'):
            try: member=Path(str(path)+suffix).lstat()
            except FileNotFoundError: continue
            if not stat.S_ISREG(member.st_mode) or member.st_nlink!=1:
                raise ValueError('HISTORY_STORE_ALIAS_FORBIDDEN')
        return path, (info.st_dev, info.st_ino)

    def prepare_history_schema(self, validator):
        """Capture/inspect once per initializer, before source SQLite or WAL.

        Native updates do not invalidate this dataset-inode initialization
        boundary. The schema is still checked on every init transaction; a
        replaced file or a fresh Store must pass the private-copy preflight.
        """
        path, identity = self._path_identity()
        if identity is not None and identity != self._initialized_identity:
            from rc6_audit_evidence.sqlite_snapshot import readonly_copy
            with readonly_copy(path, validate=False) as source_copy:
                validator(source_copy)
            if self._path_identity()[1] != identity:
                raise ValueError('HISTORY_INITIALIZATION_SOURCE_CHANGED')
        self._prepared_identity = identity

    def finish_history_schema(self):
        """Negotiate WAL only after native schema validation and commit."""
        _, identity = self._path_identity()
        if identity is None:
            raise ValueError('HISTORY_INITIALIZATION_SOURCE_CHANGED')
        if identity == self._initialized_identity:
            return
        with self.connect() as connection:
            if connection.execute('PRAGMA journal_mode=WAL').fetchone()[0].lower() != 'wal':
                raise ValueError('HISTORY_RUNTIME_WAL_REQUIRED')
        self._initialized_identity = self._path_identity()[1]
        self._prepared_identity = self._initialized_identity

    def connect(self) -> sqlite3.Connection:
        _, identity = self._path_identity()
        if self._prepared_identity is not None and identity != self._prepared_identity:
            raise ValueError('HISTORY_INITIALIZATION_SOURCE_CHANGED')
        folder = os.path.dirname(self.path) or "."
        os.makedirs(folder, exist_ok=True)
        c = sqlite3.connect(self.path, timeout=30, factory=_OwnedConnection)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA synchronous=NORMAL")
        c.execute("PRAGMA busy_timeout=30000")
        return c


def default_history_store() -> HistoricalStore:
    return HistoricalStore()
