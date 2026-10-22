"""Versioned historical authority; no live/PAPER entry authority."""

POLICY_VERSION = "RC6_HISTORICAL_AUTHORITY_V2_20261022"
SOURCE_RANK = {
    "PPI_PRODUCTION_HISTORY": 10, "PPI_API": 10,
    "IOL": 20,
    "BYMA_EOD": 30, "BYMA": 30, "A3_CEM_CLOSING": 30,
    "DATA912": 50, "DATA912_POROTA_BATCH": 50,
    "YAHOO": 90,
}
QUALITY_ORDER = {"FULL_OHLCV": 0, "FULL_OHLC": 1, "CLOSE_ONLY": 2}


def source_rank(source):
    text=str(source or "").strip().upper()
    for name,rank in SOURCE_RANK.items():
        if text==name or text.startswith(name+"_"):
            return rank
    return 100


def quality(open_, high, low, volume, volume_kind="UNKNOWN"):
    if open_ is None and high is None and low is None:
        return "CLOSE_ONLY"
    if any(x is None for x in (open_,high,low)):
        return "INVALID"
    if volume is not None and volume_kind in {"QUANTITY","NOMINAL","CONTRACTS"}:
        return "FULL_OHLCV"
    return "FULL_OHLC"


def eligible(evidence_quality, minimum_quality):
    if minimum_quality not in QUALITY_ORDER:
        raise ValueError("HISTORY_MINIMUM_QUALITY_INVALID")
    return evidence_quality in QUALITY_ORDER and QUALITY_ORDER[evidence_quality]<=QUALITY_ORDER[minimum_quality]
