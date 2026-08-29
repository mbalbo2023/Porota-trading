"""Continuidad PAPER v17 separada del libro anterior; nunca importa históricos."""
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import uuid

DB_ENV = 'PAPER_V17_DB_PATH'
RELATIVE_DB = Path('paper_v17/observer_v17.db')
CONTAINER_DB = '/app/data/paper_v17/observer_v17.db'
NAMESPACE = 'POROTA_PAPER_V17_FRESH'
IMAGE = 'porota-trading-bot:17.0.0-rc2'
CAPITAL_DEFAULTS = {
    'ARS': ('PAPER_INITIAL_CAPITAL_ARS', '1000000'),
    'USD': ('PAPER_INITIAL_CAPITAL_USD', '0'),
    'USD_MEP': ('PAPER_INITIAL_CAPITAL_USD_MEP', '0'),
    'USD_CCL': ('PAPER_INITIAL_CAPITAL_USD_CCL', '0'),
}


def checked_path(value):
    """Rechaza rutas/alias del libro conocido antes de abrirlo con SQLite."""
    raw = Path(value).absolute()
    path = raw.resolve()
    if (raw.name == 'observer_production.db' or path.name == 'observer_production.db'
            or raw.parent.name == 'observer' or path.parent.name == 'observer'):
        raise ValueError('PAPER_V17_LEGACY_PATH_FORBIDDEN')
    # Un alias no es una base independiente: podría escribir el mismo inodo.
    if raw.is_symlink() or (path.exists() and path.stat().st_nlink != 1):
        raise ValueError('PAPER_V17_DATABASE_ALIAS_FORBIDDEN')
    return path


def database_path(environ=None):
    env = os.environ if environ is None else environ
    # PAPER_DB_PATH pertenece a la instalación anterior: nunca es fallback.
    value = env.get(DB_ENV, '').strip()
    return checked_path(value or Path(env.get('DATA_DIR', 'data')) / RELATIVE_DB)


def artifact_root(path=None):
    target = database_path() if path is None else checked_path(path)
    # Dos datasets en el mismo directorio no comparten nombres de reportes/backups.
    return target.parent / 'artifacts' / target.name


def capital_profile(environ=None):
    env = os.environ if environ is None else environ
    values = {}
    for currency, (key, default) in CAPITAL_DEFAULTS.items():
        try:
            value = Decimal(env.get(key, default))
        except (InvalidOperation, TypeError, ValueError):
            raise ValueError('PAPER_V17_INVALID_INITIAL_CAPITAL') from None
        if not value.is_finite() or value < 0:
            raise ValueError('PAPER_V17_INVALID_INITIAL_CAPITAL')
        # normalize() usa la precisión Decimal activa y puede redondear.
        # Canonicalizar sin perder dígitos: cambios pequeños también bloquean.
        if value:
            sign, digits, exponent = value.as_tuple()
            digits = list(digits)
            while digits[-1] == 0:
                digits.pop()
                exponent += 1
            values[currency] = str(Decimal((sign, tuple(digits), exponent)))
        else:
            values[currency] = '0'
    return json.dumps(values, sort_keys=True, separators=(',', ':'))


def mark_new_database(connection):
    """Sólo para el archivo recién creado por PaperStore, nunca un existente."""
    connection.execute('''CREATE TABLE paper_workspace(
        id INTEGER PRIMARY KEY CHECK(id=1), namespace TEXT NOT NULL,
        dataset_id TEXT NOT NULL, created_at TEXT NOT NULL,
        origin TEXT NOT NULL, imported_legacy INTEGER NOT NULL,
        initial_capital_json TEXT)''')
    connection.execute('INSERT INTO paper_workspace VALUES(1,?,?,?,?,0,NULL)',
        (NAMESPACE, str(uuid.uuid4()), datetime.now(timezone.utc).isoformat(), 'FRESH_EMPTY'))


def identity_from_connection(connection):
    try:
        rows = connection.execute('SELECT * FROM paper_workspace').fetchall()
        names = [r[1] for r in connection.execute('PRAGMA table_info(paper_workspace)')]
        row = dict(zip(names, rows[0])) if len(rows) == 1 else {}
        if (row.get('id') != 1 or row.get('namespace') != NAMESPACE
                or row.get('origin') != 'FRESH_EMPTY' or row.get('imported_legacy') != 0
                or str(uuid.UUID(row['dataset_id'])) != row['dataset_id']
                or datetime.fromisoformat(row['created_at']).tzinfo is None):
            raise ValueError('PAPER_V17_UNVERIFIED_DATABASE')
        return row
    except (sqlite3.Error, KeyError, TypeError, ValueError, IndexError):
        raise ValueError('PAPER_V17_UNVERIFIED_DATABASE') from None


def read_identity(path):
    path = checked_path(path)
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=5)) as c:
        c.execute('PRAGMA query_only=ON')
        return identity_from_connection(c)


def runtime_store(path=None, *, environ=None):
    """Arranque coordinado de la base nueva; sólo reinicia su propio dataset.

    Una base previa sin identidad/capital de este runtime se rechaza, no se
    migra, vacía, sobreescribe ni recupera automáticamente. El lock sólo
    coordina inicializadores v17; no bloquea modificaciones manuales externas.
    """
    from be_paper_engine import PaperStore
    target = database_path(environ) if path is None else checked_path(path)
    profile = capital_profile(environ)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(str(target) + '.initialization.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        checked_path(target)
        if target.exists():
            if read_identity(target).get('initial_capital_json') != profile:
                raise ValueError('PAPER_V17_INITIAL_CAPITAL_OR_ORIGIN_MISMATCH')
            return PaperStore(str(target))
        store = PaperStore(str(target))
        with store.connect() as c:
            identity_from_connection(c)
            c.execute('UPDATE paper_workspace SET initial_capital_json=? WHERE id=1', (profile,))
        return store
