"""Observador productivo con paper trading completo y cero capacidad operativa."""

from __future__ import annotations

import json
import os
import signal
import sys
import time
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from bd_ppi_readonly_guard import ProductionMarketReader, ReadOnlyPolicyViolation
from be_paper_engine import D, PaperBroker, PaperStore, Quote, now_iso


VERSION = "16.3.4"
TZ = ZoneInfo(os.getenv("SERVER_TIMEZONE", "America/Argentina/Buenos_Aires"))
DB_PATH = os.getenv("PAPER_DB_PATH", "data/observer/observer_production.db")
SECRET_PATH = os.getenv("PPI_PRODUCTION_SECRET_FILE", "/run/secrets/ppi_production.json")
INTERVAL = max(15, int(os.getenv("PAPER_OBSERVER_INTERVAL_SECONDS", "60")))
SYMBOLS = (
    ("GGAL", "ACCIONES", "A-24HS"),
    ("AL30", "BONOS", "A-24HS"),
    ("AAPL", "CEDEARS", "A-24HS"),
)
STOP = False


def _stop(*_):
    global STOP
    STOP = True


def _secret():
    with open(SECRET_PATH, encoding="utf-8") as f:
        value = json.load(f)
    key, secret = value.get("api_key", ""), value.get("api_secret", "")
    if not key or not secret:
        raise RuntimeError("El archivo secreto productivo esta incompleto.")
    return key, secret


def _market_open(now=None):
    now = now or datetime.now(TZ)
    if now.weekday() >= 5:
        return False
    minute = now.hour * 60 + now.minute
    return 10 * 60 + 30 <= minute <= 17 * 60


def _walk_numbers(value, keys):
    """Encuentra el primer numero bajo cualquiera de las claves, sin asumir SDK."""
    if isinstance(value, dict):
        lowered = {str(k).lower(): v for k, v in value.items()}
        for key in keys:
            if key.lower() in lowered and lowered[key.lower()] not in (None, ""):
                candidate = D(lowered[key.lower()], "-1")
                if candidate >= 0:
                    return candidate
        for child in value.values():
            found = _walk_numbers(child, keys)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _walk_numbers(child, keys)
            if found is not None:
                return found
    return None


def _levels(book, side):
    if not isinstance(book, dict):
        return []
    aliases = ("bids", "bid", "buy", "compras") if side == "bid" else (
        "offers", "offer", "asks", "ask", "sell", "ventas")
    for key, value in book.items():
        if str(key).lower() in aliases:
            return value if isinstance(value, list) else [value]
    for value in book.values():
        if isinstance(value, dict):
            found = _levels(value, side)
            if found:
                return found
    return []


def _level(level):
    price = _walk_numbers(level, ("price", "precio"))
    size = _walk_numbers(level, ("quantity", "size", "cantidad", "volume"))
    return price or Decimal(0), size or Decimal(0)


def normalize_quote(symbol, asset_class, settlement, current, book):
    last = _walk_numbers(current, ("price", "last", "lastprice", "ultimo", "close")) or Decimal(0)
    bids, asks = _levels(book, "bid"), _levels(book, "ask")
    bid, bid_size = _level(bids[0]) if bids else (Decimal(0), Decimal(0))
    ask, ask_size = _level(asks[0]) if asks else (Decimal(0), Decimal(0))
    # Algunas respuestas planas traen las puntas sin arreglos.
    bid = bid or _walk_numbers(book, ("bidprice", "bid", "compra")) or Decimal(0)
    ask = ask or _walk_numbers(book, ("offerprice", "askprice", "ask", "venta")) or Decimal(0)
    bid_size = bid_size or _walk_numbers(book, ("bidsize", "bidquantity")) or Decimal(0)
    ask_size = ask_size or _walk_numbers(book, ("offersize", "asksize", "offerquantity")) or Decimal(0)
    if last <= 0 and bid > 0 and ask > 0:
        last = (bid + ask) / 2
    return Quote(symbol, asset_class, settlement, last, bid, ask, bid_size, ask_size, now_iso())


def run():
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    store = PaperStore(DB_PATH)
    broker = PaperBroker(store,
                         initial_cash=os.getenv("PAPER_INITIAL_CAPITAL_ARS", "1000000"),
                         risk_pct=os.getenv("PAPER_RISK_PER_TRADE", "0.005"),
                         max_positions=os.getenv("PAPER_MAX_OPEN_POSITIONS", "3"))
    store.state(process_state="STARTING", session_state="CHECKING",
                ppi_auth="NOT_ATTEMPTED", real_orders_sent=0,
                detail="Simulacion productiva inicializando.")
    if not _market_open():
        store.state(process_state="RUNNING", session_state="MARKET_CLOSED",
                    ppi_auth="NOT_ATTEMPTED", heartbeat_at=now_iso(),
                    detail="Mercado cerrado; no se consume un login productivo.")
    reader = None
    quotes = {}
    try:
        while not STOP:
            if not _market_open():
                store.state(process_state="RUNNING", session_state="MARKET_CLOSED",
                            heartbeat_at=now_iso(), real_orders_sent=0,
                            detail="Mercado cerrado; simulador en espera.")
                time.sleep(min(INTERVAL, 60))
                continue
            if reader is None:
                key, secret = _secret()
                reader = ProductionMarketReader(key, secret, audit=store.audit_http)
                try:
                    reader.login_once()
                    store.state(ppi_auth="OK", session_state="MARKET_OPEN",
                                detail="Login de solo lectura correcto.")
                except Exception as exc:
                    store.state(process_state="DEGRADED", ppi_auth="ERROR",
                                heartbeat_at=now_iso(), detail=f"Login fallo: {type(exc).__name__}")
                    return 2
            cycle_ok = 0
            for symbol, asset_class, settlement in SYMBOLS:
                if STOP:
                    break
                try:
                    current = reader.current(symbol, asset_class, settlement)
                    book = reader.book(symbol, asset_class, settlement)
                    q = normalize_quote(symbol, asset_class, settlement, current, book)
                    if q.last <= 0:
                        store.event("DATA_REJECTED", f"{symbol}: cotizacion sin precio util")
                        continue
                    store.add_quote(q)
                    quotes[symbol] = q
                    broker.on_quote(q)
                    cycle_ok += 1
                    store.state(last_market_data_at=q.observed_at)
                except ReadOnlyPolicyViolation as exc:
                    store.event("HTTP_BLOCKED", str(exc))
                    store.state(process_state="DEGRADED", detail="La barrera bloqueo una ruta no permitida.")
                except Exception as exc:
                    store.event("DATA_ERROR", f"{symbol}: {type(exc).__name__}: {str(exc)[:180]}")
            broker.mark_equity(quotes)
            metrics = reader.metrics
            store.state(process_state="RUNNING" if cycle_ok else "DEGRADED",
                        session_state="MARKET_OPEN", heartbeat_at=now_iso(),
                        http_allowed=metrics["http_allowed"],
                        http_blocked=metrics["http_blocked"], real_orders_sent=0,
                        detail=f"{cycle_ok}/{len(SYMBOLS)} instrumentos actualizados; operaciones solo simuladas.")
            time.sleep(INTERVAL)
    finally:
        if reader:
            reader.close()
        store.state(process_state="STOPPED", heartbeat_at=now_iso(), real_orders_sent=0,
                    detail="Observador detenido ordenadamente.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
