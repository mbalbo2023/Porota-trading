"""Ingest IOL daily bars under their declared, non-canonical scope."""
from datetime import datetime, timezone
from hashlib import sha256
import json

from cu_history_store_v2_hf6 import (
    ART, SOURCE_REPORTED, Candle, append_many, instant, source_reported_adjustment, validate_daily_values,
)
from rc6_multisource_discovery import canonical_family


SOURCE = "IOL_REST"


def ingest_daily_reference(client, store, *, symbol, family, market, start, end, now):
    """Accept only an isolated reference store, preserving the canonical archive."""
    if getattr(store, "source_reported_only", False) is not True:
        raise ValueError("IOL_HISTORY_REFERENCE_STORE_REQUIRED")
    family = canonical_family(family)
    if family not in {"ACCIONES", "CEDEARS", "ETFS", "BONOS", "LETRAS", "OBLIGACIONES", "OPCIONES"}:
        raise ValueError("IOL_HISTORY_FAMILY_UNVERIFIED")
    capture = client.read_daily_bars(symbol, market, start, end)
    received = instant(now())
    if end > received.astimezone(ART).date():
        raise ValueError("IOL_HISTORY_WINDOW_IN_FUTURE")
    digest = sha256(json.dumps(capture, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    records, dates = [], set()
    for row in capture["bars"]:
        if not isinstance(row, dict) or type(row.get("timestamp")) not in (int, float):
            raise ValueError("IOL_HISTORY_ROW_UNVERIFIED")
        provider_at = datetime.fromtimestamp(row["timestamp"], tz=timezone.utc)
        if instant(row["datetime"]) != provider_at or provider_at > received:
            raise ValueError("IOL_HISTORY_CLOCK_MISMATCH")
        day = provider_at.astimezone(ART).date()
        if not start <= day <= end or day in dates:
            raise ValueError("IOL_HISTORY_DAY_UNVERIFIED")
        dates.add(day)
        values = validate_daily_values(date=day.isoformat(), open=row["open"], high=row["high"],
                                       low=row["low"], close=row["close"], volume=row["volume"],
                                       known_at=received, market=market, family=family, settlement=None)
        records.append(Candle(symbol=symbol, instrument_type=family, market=market,
                              currency="UNKNOWN", settlement="UNKNOWN", date=day.isoformat(),
                              source=SOURCE, price_basis=SOURCE_REPORTED,
                              adjustment_basis=source_reported_adjustment(SOURCE),
                              observed_at=received, provider_at=provider_at, **values,
                              metadata={"provider_response_sha256": digest, "readiness_implication": "NONE"}))
    return append_many(store, records)
