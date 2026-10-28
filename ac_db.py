"""
ac_db.py — Capa única de conexión a SQLite (NUEVO EN v15.0)

POR QUÉ EXISTE ESTE ARCHIVO
===========================
La auditoría de la v14 marcó como MEDIA el riesgo de "database is locked"
cuando los hilos de evaluación de riesgo y de inserción de operaciones
chocan sobre el mismo archivo SQLite, y propuso activar WAL y un timeout.

El diagnóstico es correcto, pero conviene precisar dos cosas antes de
implementar, porque la solución propuesta (una función get_db_connection()
que hace PRAGMA journal_mode=WAL en cada conexión) resuelve la mitad del
problema:

  1) WAL YA ESTABA ACTIVADO en v14.0 — pero una sola vez, en
     j_main.init_db(). Eso alcanza, porque journal_mode=WAL es una
     propiedad PERSISTENTE del archivo de base de datos (queda grabada en
     su header), no de la conexión: una vez puesto, todas las conexiones
     futuras lo heredan. Repetirlo en cada conexión no aporta nada más que
     un PRAGMA extra por conexión.

  2) LO QUE SÍ FALTABA, y es el verdadero origen del bug, es el TIMEOUT.
     De las ~45 llamadas a sqlite3.connect() repartidas por el proyecto,
     la enorme mayoría usaba la firma corta sqlite3.connect(DB_PATH), que
     aplica el timeout por defecto de Python: 5 segundos. Con WAL, un
     lector nunca bloquea a un escritor, pero DOS escritores concurrentes
     (el loop principal grabando una señal y el hilo de confirmaciones
     grabando un fill, por ejemplo) sí se serializan; si el que llegó
     segundo espera más que su timeout, tira OperationalError. Con el
     agravante de que en v14.0 ese error, en algunos caminos, se comía la
     grabación de una operación REAL ya ejecutada en PPI.

Entonces la corrección de v15.0 es: un único punto de entrada para abrir
la base, con timeout amplio, WAL asegurado (idempotente y barato),
synchronous=NORMAL (seguro con WAL, y mucho más rápido que FULL para el
volumen de escrituras del bot) y foreign_keys activado.

CÓMO SE USA
===========
    import ac_db

    with ac_db.connect() as conn:          # context manager: commit + close
        conn.execute("INSERT INTO ...", (...))

    conn = ac_db.connect_raw()             # si necesitás manejar el cierre
    ...
    conn.close()

connect() devuelve un context manager que hace commit si no hubo excepción
y rollback si la hubo, y SIEMPRE cierra. Ese patrón elimina, de paso, un
segundo problema real que había en v14.0: varios caminos de error salían
de una función con `return` antes del conn.close(), dejando la conexión
abierta hasta que el recolector de basura la juntara — que es exactamente
la forma de mantener un lock vivo más tiempo del necesario.

NOTA SOBRE RETRIES
==================
Además del timeout, execute_with_retry() reintenta ante un "database is
locked" con backoff corto. Es una red de seguridad de último recurso para
escrituras críticas (órdenes, posiciones, kill switch): si la base está
trabada más allá del timeout, algo más grave está pasando y conviene que
quede en el log, pero no a costa de perder el registro de una orden real.
"""

import os
import time
import random
import sqlite3
import threading
import logging
from contextlib import contextmanager

from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("db")

DB_PATH = os.getenv("DB_PATH", "data/trading_system.db")  # mismo default que el resto del proyecto

# 20 segundos: cuatro veces el default de Python. Una escritura del bot
# tarda milisegundos; si hay que esperar 20 segundos por un lock, el
# problema no es la contención normal sino un proceso trabado, y eso
# queremos verlo como error, no enmascararlo con un timeout infinito.
DB_TIMEOUT_SECONDS = float(os.getenv("DB_TIMEOUT_SECONDS", "20"))

_pragmas_applied = False


def _apply_pragmas(conn: sqlite3.Connection) -> None:
    """WAL es persistente en el archivo, así que solo hace falta una vez por
    proceso; los demás PRAGMA son por conexión y sí se aplican siempre."""
    global _pragmas_applied
    if not _pragmas_applied:
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            _pragmas_applied = True
        except sqlite3.Error as e:
            # No es fatal: si la base está en un filesystem que no soporta
            # WAL (algunos montajes de red), SQLite la deja en delete mode
            # y el bot sigue funcionando, más lento y con más contención.
            logger.warning("No se pudo activar WAL sobre %s: %s", DB_PATH, e)
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    # Si otra conexión tiene el lock, esperar en vez de fallar al instante.
    conn.execute(f"PRAGMA busy_timeout={int(DB_TIMEOUT_SECONDS * 1000)};")


def connect_raw(path: str = None) -> sqlite3.Connection:
    """Conexión cruda, con los PRAGMA ya aplicados. El que la pide se hace
    cargo de cerrarla. Preferí connect() salvo que necesites controlar el
    ciclo de vida a mano (por ejemplo, para mantenerla abierta en un hilo)."""
    target = path or DB_PATH
    directory = os.path.dirname(target)
    if directory:
        os.makedirs(directory, exist_ok=True)
    conn = sqlite3.connect(target, timeout=DB_TIMEOUT_SECONDS)
    _apply_pragmas(conn)
    return conn


_hilo_local = threading.local()


def connect_threadlocal(path: str = None) -> sqlite3.Connection:
    """Conexión reutilizable, propia de cada hilo.

    NOTA SOBRE LA AUDITORÍA QUE PIDIÓ ESTO: el hallazgo describía que
    conexiones creadas en el hilo principal se compartían con sub-hilos,
    provocando el error "SQLite objects created in a thread can only be used
    in that same thread". Verificado contra el código, eso NO pasaba: cada
    llamada a connect_raw() abre su propia conexión, así que nunca se compartió
    un handle. El diagnóstico era incorrecto.

    Aun así la mejora de fondo es real y se implementa: abrir y cerrar una
    conexión en cada consulta tiene un costo que se nota en los caminos
    calientes, como el bucle de evaluación que consulta posiciones por cada
    instrumento. Esta versión mantiene una conexión viva POR HILO —que es lo
    que SQLite permite— y la reutiliza. El aislamiento por hilo hace, además,
    que el error descrito por la auditoría sea imposible por construcción,
    aunque alguien en el futuro guarde la conexión en una variable global.
    """
    if getattr(_hilo_local, "conn", None) is None:
        _hilo_local.conn = connect_raw(path)
        logger.debug("Conexión SQLite creada para el hilo %s", threading.current_thread().name)
    return _hilo_local.conn


def close_threadlocal() -> None:
    """Cierra la conexión del hilo actual. Se llama al terminar un hilo de
    trabajo para no dejar descriptores abiertos."""
    conn = getattr(_hilo_local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
        _hilo_local.conn = None


@contextmanager
def connect(path: str = None):
    """Context manager: commit al salir bien, rollback si hubo excepción,
    close siempre."""
    conn = connect_raw(path)
    try:
        yield conn
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            conn.close()
        except Exception:
            pass


def execute_with_retry(sql: str, params: tuple = (), attempts: int = 4,
                       path: str = None):
    """Para escrituras que NO se pueden perder (órdenes, posiciones, halts).
    Reintenta solo ante bloqueo de base; cualquier otro error se propaga
    tal cual, porque un error de SQL no mejora reintentándolo."""
    ultimo_error = None
    for intento in range(attempts):
        try:
            with connect(path) as conn:
                cur = conn.execute(sql, params)
                return cur.lastrowid
        except sqlite3.OperationalError as e:
            if "locked" not in str(e).lower() and "busy" not in str(e).lower():
                raise
            ultimo_error = e
            # Jitter: si dos hilos chocan, no conviene que reintenten al
            # mismo tiempo exacto y vuelvan a chocar.
            espera = (0.4 * (2 ** intento)) + random.uniform(0, 0.3)
            logger.warning("Base bloqueada (intento %s/%s). Reintento en %.1fs.",
                           intento + 1, attempts, espera)
            time.sleep(espera)
    logger.error("No se pudo escribir en la base tras %s intentos: %s", attempts, ultimo_error)
    raise ultimo_error


def healthcheck() -> dict:
    """Lo consume y_infra_monitor.py y la sección de infraestructura del
    panel: sirve para ver si WAL quedó activo de verdad en el servidor y
    cuánto pesa el archivo -wal (si crece sin parar, hay una transacción
    que quedó abierta)."""
    info = {"ok": False, "journal_mode": None, "db_size_mb": None, "wal_size_mb": None}
    try:
        with connect() as conn:
            info["journal_mode"] = conn.execute("PRAGMA journal_mode;").fetchone()[0]
            conn.execute("SELECT 1").fetchone()
        if os.path.exists(DB_PATH):
            info["db_size_mb"] = round(os.path.getsize(DB_PATH) / (1024 * 1024), 2)
        wal = DB_PATH + "-wal"
        if os.path.exists(wal):
            info["wal_size_mb"] = round(os.path.getsize(wal) / (1024 * 1024), 2)
        info["ok"] = True
    except Exception as e:
        info["error"] = str(e)
    return info
