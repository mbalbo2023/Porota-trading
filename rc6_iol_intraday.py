"""Intervals measured between authenticated IOL trade samples."""
from datetime import timedelta, timezone
import hashlib
import json
import re

from bs_instrument_contracts import aware_datetime, decimal_value
from co_market_sessions_hf6 import byma_paper_spot_open
from rc6_signal_contracts import CONFIRMED_INTERVAL_VOLUME, SCALPING_TEMPORAL_CONTRACT


SOURCE = "IOL_WS_TRADE_TS"
FAMILIES = frozenset({"ACCIONES", "CEDEARS", "ETFS"})
FRESH_SECONDS = 120
_DIGEST = re.compile(r"[0-9a-f]{64}")


def _stamp(value):
    return aware_datetime(value).astimezone(timezone.utc).isoformat(timespec="microseconds")


def read_windows(connection, at):
    """Read a bounded window without creating, resampling or modifying data."""
    now = aware_datetime(at)
    if not byma_paper_spot_open(now) or not connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='historical_raw_archive'"
    ).fetchone():
        return {}
    cutoff = now - timedelta(seconds=SCALPING_TEMPORAL_CONTRACT["lookback_limit_seconds"])
    rows = connection.execute("""SELECT row_key,body_hash,recorded_at,body_json
        FROM historical_raw_archive
        WHERE origin='IOL_REST_TRADE_SAMPLE' AND quality='SAMPLED'
          AND recorded_at BETWEEN ? AND ?
        ORDER BY recorded_at DESC,id DESC LIMIT 1024""", (_stamp(cutoff), _stamp(now))).fetchall()
    windows = {}
    for receipt, body_hash, recorded_at, raw in reversed(rows):
        if not _DIGEST.fullmatch(receipt) or hashlib.sha256(raw.encode()).hexdigest() != body_hash:
            continue
        try:
            body = json.loads(raw)
            key = tuple(body["identity"])
            trade_at, received = aware_datetime(body["trade_at"]), aware_datetime(recorded_at)
            if (len(key) != 5 or key[1] not in FAMILIES or key[2] != "BYMA"
                    or not all(isinstance(part, str) and part for part in key)
                    or body["wire_sha256"] != receipt or not cutoff <= trade_at <= received <= now
                    or (received - trade_at).total_seconds() > FRESH_SECONDS
                    or not byma_paper_spot_open(trade_at)):
                continue
            price = decimal_value(body["price"], "IOL trade price", positive=True)
        except (KeyError, ValueError, TypeError, ArithmeticError):
            continue
        window = windows.setdefault(key, {"points": [], "last": None, "fresh_trade_at": None})
        window["fresh_trade_at"] = trade_at
        counter = body.get("iol_day_counter") or {}
        try:
            generation = counter["gateway_generation"], counter["stream_session"]
            if not all(isinstance(value, str) and _DIGEST.fullmatch(value) for value in generation):
                raise ValueError("IOL_TRADE_SESSION_UNVERIFIED")
            total = decimal_value(counter["nominals"], "IOL TS", nonnegative=True)
        except (KeyError, ValueError, TypeError, ArithmeticError):
            window.update(last=None, points=[])
            continue
        previous = window["last"]
        window["last"] = (trade_at, price, total, generation, receipt)
        if previous is None:
            continue
        prior_at, prior_price, prior_total, prior_generation, prior_receipt = previous
        same_gateway = generation[0] == prior_generation[0]
        if same_gateway and (trade_at, price, total) == previous[:3]:
            window["last"] = (trade_at, price, total, generation, prior_receipt)
            continue
        gap = (trade_at - prior_at).total_seconds()
        # TS is cumulative for the venue day, including across a bearer renewal.
        # Each endpoint is independently matched to Current with its own stream
        # session. Restarting the Gateway or revising TS still clears warmup.
        if (not same_gateway or total < prior_total
                or not 0 < gap <= SCALPING_TEMPORAL_CONTRACT["maximum_gap_seconds"]):
            window["points"] = []
            continue
        window["points"].append({"event_at": _stamp(trade_at), "price": price,
            "volume": total - prior_total, "first_received_at": _stamp(received),
            "last_verified_at": _stamp(received), "source": SOURCE,
            "interval_start": _stamp(prior_at), "start_receipt": prior_receipt, "end_receipt": receipt})
    return windows


def inputs(window):
    """Expose the original nominal unit and refuse a stale or missing interval."""
    points = (window or {}).get("points", [])[-30:]
    if not points:
        return [], {"state": "IOL_INTERVAL_VOLUME_UNVERIFIED"}
    last = points[-1]
    return points, {"state": CONFIRMED_INTERVAL_VOLUME, "last_source_at": last["event_at"],
                    "checked_at": last["last_verified_at"],
                    "volume": {"schema": "rc6.provider-volume-contract.v1",
                        "provider": "IOL", "endpoint": "WebSocket trades TS", "unit": "NOMINAL",
                        "accumulation": "INTERVAL", "reset_rule": "SESSION_ONLY",
                        "verification": "VERIFIED",
                        "evidence_ref": "https://developers.iolinversiones.com/docs/websocket",
                        "known_at": last["last_verified_at"]}}


def quote_matches(quote, window):
    """The persisted Current must still be the endpoint of the verified interval."""
    if quote.metadata_source != "IOL_REST" or not window or not window["points"] or not window["last"]:
        return False
    trade_at, price, total, generation, _ = window["last"]
    evidence = quote.iol_trade_evidence or {}
    try:
        return (aware_datetime(quote.trade_at) == trade_at and quote.last == price
                and tuple(evidence[name] for name in ("gateway_generation", "stream_session")) == generation
                and decimal_value(evidence["day_nominals"], "IOL TS", nonnegative=True) == total)
    except (KeyError, ValueError, TypeError, ArithmeticError):
        return False
